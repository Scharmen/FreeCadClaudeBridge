"""Measure a saved model for the benchmark. Runs inside FreeCAD (freecadcmd), see `score.py`.

The result is plain JSON for `spec.evaluate`: the visible solids with size, holes, round faces and
surface types, plus tree errors, parameters, expressions, joints, interference, a flex test and the
IoU against a reference shape. Everything is measured from the saved file, so the same file always
gives the same numbers.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

import FreeCAD as App
import Part

V = App.Vector
CONTAINERS = {"App::Part", "Assembly::AssemblyObject", "App::DocumentObjectGroup", "App::Origin",
              "Assembly::JointGroup", "App::TextDocument", "App::VarSet", "Spreadsheet::Sheet"}
HOLE_SPAN = math.radians(250)   # a cylinder around at least this much of the circle is a hole, less is a fillet
INTERFERENCE_MM3 = 0.5


def r4(x: float) -> float:
    return round(float(x), 4)


def _is_result(o: Any) -> bool:
    if not getattr(o, "Visibility", False) or o.TypeId in CONTAINERS:
        return False
    if o.TypeId.startswith("PartDesign::") and o.TypeId != "PartDesign::Body":
        return False
    if o.TypeId.startswith("Mesh::"):
        return True
    return hasattr(o, "Shape") and bool(o.Shape.Solids)


def _global_shape(o: Any) -> Any:
    s = Part.getShape(o)
    parent = o.getParentGeoFeatureGroup()
    if parent is not None and parent.TypeId != "PartDesign::Body":
        s = s.copy()
        s.Placement = parent.getGlobalPlacement().multiply(s.Placement)
    return s


def _holes_and_radii(s: Any) -> tuple[list[dict[str, Any]], list[float]]:
    groups: list[dict[str, Any]] = []
    radii: list[float] = []
    for f in s.Faces:
        surf = f.Surface
        name = type(surf).__name__
        if name == "Toroid":
            radii.append(r4(surf.MinorRadius))
            continue
        if name != "Cylinder":
            continue
        u0, u1, v0, v1 = f.ParameterRange
        span = abs(u1 - u0)
        um, vm = (u0 + u1) / 2, (v0 + v1) / 2
        p, nrm = f.valueAt(um, vm), f.normalAt(um, vm)
        axis = V(surf.Axis).normalize()
        rel = p - surf.Center
        radial = rel - axis * rel.dot(axis)
        concave = nrm.dot(radial) < 0
        if not concave or span < math.radians(179):
            radii.append(r4(surf.Radius))
            continue
        for g in groups:
            if abs(g["r"] - surf.Radius) < 1e-4 and axis.cross(g["axis"]).Length < 1e-3:
                d = (V(surf.Center) - g["point"])
                if (d - g["axis"] * d.dot(g["axis"])).Length < 1e-3:
                    g["span"] += span
                    break
        else:
            groups.append({"r": surf.Radius, "axis": axis, "point": V(surf.Center), "span": span})
    holes = []
    for g in groups:
        hole = {"d": r4(2 * g["r"]), "axis": [r4(x) for x in g["axis"]], "point": [r4(x) for x in g["point"]]}
        if g["span"] >= HOLE_SPAN:
            holes.append(hole)
        else:   # a bore open on one side (a clip ring), or a large concave fillet
            holes.append({**hole, "open": True})
            radii.append(r4(g["r"]))
    return holes, sorted(radii)


def count_teeth(s: Any) -> int | None:
    """Teeth of a gear: lobes of the outline of a section through the middle, across the thinnest axis."""
    bb = s.BoundBox
    dims = [bb.XLength, bb.YLength, bb.ZLength]
    k = dims.index(min(dims))
    axis = [V(1, 0, 0), V(0, 1, 0), V(0, 0, 1)][k]
    mid = [bb.Center.x, bb.Center.y, bb.Center.z][k]
    wires = s.slice(axis, mid)
    if not wires:
        return None
    outer = max(wires, key=lambda w: w.BoundBox.DiagonalLength)
    c = outer.BoundBox.Center
    pts = outer.discretize(Distance=max(0.02, outer.Length / 6000))
    polar = []
    for p in pts:
        d = p - c
        d = d - axis * d.dot(axis)
        u, w = [(d.y, d.z), (d.x, d.z), (d.x, d.y)][k]
        polar.append((math.atan2(w, u), d.Length))
    polar.sort()
    rs = [r for _, r in polar]
    lo, hi = min(rs), max(rs)
    if hi - lo < 0.2:
        return 0
    mid_r = (lo + hi) / 2
    above = [r > mid_r for r in rs]
    runs = sum(1 for i in range(len(above)) if above[i] and not above[i - 1])
    return runs if runs or not all(above) else 1


def tight_box(s: Any) -> Any:
    """The real extent: BoundBox of B-spline faces includes their control points."""
    try:
        return s.optimalBoundingBox(True, False)
    except Exception:  # noqa: BLE001
        return s.BoundBox


def inclined_planes(s: Any) -> int:
    """Planar faces that are not parallel to a main plane: gussets, braces, chamfers."""
    count = 0
    for f in s.Faces:
        if type(f.Surface).__name__ != "Plane":
            continue
        nrm = f.Surface.Axis
        if max(abs(nrm.x), abs(nrm.y), abs(nrm.z)) < 0.995 and f.Area > 1.0:
            count += 1
    return count


def thread_pitch(s: Any, band: list[float], step: float = 0.04) -> dict[str, Any] | None:
    """Pitch of a thread: walk a line parallel to the axis at the middle of the thread's radial band and
    note where it runs through material; crests repeat once per pitch (works for internal threads too).

    The axis is the box dimension that differs most from the other two (long for a bolt, short for a nut).
    Point tests are used instead of a section because sections of swept thread faces often fail.
    """
    bb = tight_box(s)
    dims = [bb.XLength, bb.YLength, bb.ZLength]
    k = max(range(3), key=lambda i: abs(dims[i] - (sum(dims) - dims[i]) / 2))
    axes = [V(1, 0, 0), V(0, 1, 0), V(0, 0, 1)]
    axis, across = axes[k], axes[(k + 1) % 3]
    c = bb.Center
    r = (band[0] + band[1]) / 2
    lo = [bb.XMin, bb.YMin, bb.ZMin][k] - c.dot(axis)
    count = int(dims[k] / step)
    inside = [s.isInside(c + across * r + axis * (lo + i * step), 1e-4, True) for i in range(count + 1)]
    centres, start = [], None
    for i, v in enumerate(inside):
        if v and start is None:
            start = i
        elif not v and start is not None:
            centres.append((start + i - 1) / 2 * step)
            start = None
    gaps = sorted(b - a for a, b in zip(centres, centres[1:], strict=False))
    if len(gaps) < 2:
        return {"crests": len(centres), "pitch": None}
    return {"crests": len(centres), "pitch": r4(gaps[len(gaps) // 2])}


def _solid_part(o: Any, s: Any, want_teeth: bool) -> dict[str, Any]:
    bb = tight_box(s)
    dims = [r4(bb.XLength), r4(bb.YLength), r4(bb.ZLength)]
    holes, radii = _holes_and_radii(s)
    p = {"label": o.Label, "type": o.TypeId, "dims": dims, "sorted": sorted(dims),
         "center": [r4(bb.Center.x), r4(bb.Center.y), r4(bb.Center.z)], "volume": r4(s.Volume),
         "area": r4(s.Area), "valid": bool(s.isValid()), "closed": all(sh.isClosed() for sh in s.Shells),
         "solids": len(s.Solids), "holes": holes, "radii": radii,
         "surfaces": dict(Counter(type(f.Surface).__name__ for f in s.Faces)), "inclined": inclined_planes(s)}
    if want_teeth:
        try:
            p["teeth"] = count_teeth(s)
        except Exception:  # noqa: BLE001 - an odd shape just has no tooth count
            p["teeth"] = None
    return p


def _mesh_part(o: Any) -> dict[str, Any]:
    m = o.Mesh
    bb = m.BoundBox
    dims = [r4(bb.XLength), r4(bb.YLength), r4(bb.ZLength)]
    return {"label": o.Label, "type": o.TypeId, "dims": dims, "sorted": sorted(dims),
            "center": [r4(bb.Center.x), r4(bb.Center.y), r4(bb.Center.z)], "volume": r4(abs(m.Volume)),
            "area": r4(m.Area), "valid": bool(m.isSolid() and not m.hasNonManifolds()), "closed": bool(m.isSolid()),
            "solids": 1, "holes": [], "radii": [], "surfaces": {"Mesh": m.CountFacets}, "mesh": True}


_VARSET_BASE: set[str] = set()


def _user_props(o: Any) -> list[str]:
    """Properties a user added to a VarSet (not the ones every VarSet has)."""
    if not _VARSET_BASE:
        probe = App.newDocument("FcbProbe", hidden=True)
        _VARSET_BASE.update(probe.addObject("App::VarSet", "P").PropertiesList)
        App.closeDocument(probe.Name)
    return [p for p in o.PropertiesList if p not in _VARSET_BASE]


def _value(v: Any) -> Any:
    if hasattr(v, "Value"):
        return r4(v.Value)
    return v if isinstance(v, (int, float)) else str(v)


def _params(doc: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for o in doc.Objects:
        if o.TypeId == "App::VarSet":
            for prop in _user_props(o):
                out[prop] = _value(getattr(o, prop))
        elif o.TypeId == "Spreadsheet::Sheet":
            for cell in o.getUsedCells():
                alias = o.getAlias(cell)
                if alias:
                    out[alias] = _value(o.get(cell))
    return out


def _find_param(doc: Any, name: str) -> tuple[Any, str] | None:
    for o in doc.Objects:
        if o.TypeId == "App::VarSet":
            for prop in _user_props(o):
                if prop.lower() == name.lower():
                    return o, prop
        elif o.TypeId == "Spreadsheet::Sheet":
            for cell in o.getUsedCells():
                if (o.getAlias(cell) or "").lower() == name.lower():
                    return o, cell
    return None


def _errors(doc: Any) -> list[str]:
    return [o.Label for o in doc.Objects if "Invalid" in o.State or "Error" in o.State]


def _signature(s: Any) -> list[float]:
    bb = s.BoundBox
    return [r4(bb.XLength), r4(bb.YLength), r4(bb.ZLength), round(s.Volume, 2)]


def _flex(doc: Any, names: list[str], factors: list[float], parts: list[Any]) -> list[dict[str, Any]]:
    runs = []
    for name in names:
        found = _find_param(doc, name)
        if not found:
            continue
        owner, key = found
        sheet = owner.TypeId == "Spreadsheet::Sheet"
        before = owner.get(key) if sheet else getattr(owner, key)
        value = before.Value if hasattr(before, "Value") else float(before)
        base_dims = [_signature(o.Shape) for o in parts]
        for f in factors:
            if sheet:
                owner.set(key, str(value * f))
            else:
                setattr(owner, key, value * f)
            doc.recompute()
            errs = _errors(doc)
            bad = [o.Label for o in parts if not o.Shape.isValid() or not o.Shape.Solids]
            dims = [_signature(o.Shape) for o in parts]
            runs.append({"param": key if not sheet else name, "factor": f, "ok": not errs and not bad,
                         "changed": dims != base_dims,
                         "error": ", ".join(errs + bad)[:200] or None})
        if sheet:
            owner.set(key, str(value))
        else:
            setattr(owner, key, value)
        doc.recompute()
    return runs


def _rotations() -> list[Any]:
    out = []
    for perm in ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)):
        for signs in ((1, 1, 1), (1, 1, -1), (1, -1, 1), (1, -1, -1), (-1, 1, 1), (-1, 1, -1), (-1, -1, 1),
                      (-1, -1, -1)):
            rows = [[0.0] * 3 for _ in range(3)]
            for i in range(3):
                rows[i][perm[i]] = float(signs[i])
            det = (rows[0][0] * (rows[1][1] * rows[2][2] - rows[1][2] * rows[2][1])
                   - rows[0][1] * (rows[1][0] * rows[2][2] - rows[1][2] * rows[2][0])
                   + rows[0][2] * (rows[1][0] * rows[2][1] - rows[1][1] * rows[2][0]))
            if det > 0:
                m = App.Matrix()
                m.A11, m.A12, m.A13 = rows[0]
                m.A21, m.A22, m.A23 = rows[1]
                m.A31, m.A32, m.A33 = rows[2]
                out.append(m)
    return out


def iou(shape: Any, ref: Any) -> float | None:
    """Best overlap of two solids over the 24 axis-aligned orientations, both centred on their boxes."""
    def centred(s: Any) -> Any:
        s = s.copy()
        s.translate(-s.BoundBox.Center)
        return s

    a, b = centred(shape), centred(ref)
    rbb = [b.BoundBox.XLength, b.BoundBox.YLength, b.BoundBox.ZLength]
    best = None
    for m in _rotations():
        t = a.copy()
        t.transformShape(m)
        t.translate(-t.BoundBox.Center)
        tb = [t.BoundBox.XLength, t.BoundBox.YLength, t.BoundBox.ZLength]
        if any(abs(x - y) > 0.15 * max(y, 1.0) for x, y in zip(tb, rbb, strict=True)):
            continue
        common = t.common(b).Volume
        union = t.Volume + b.Volume - common
        value = common / union if union > 0 else 0.0
        best = value if best is None else max(best, value)
    return 0.0 if best is None else r4(best)   # no orientation even has the right size


def _pitch_band(spec: dict[str, Any]) -> list[float] | None:
    bands = [p["pitch"]["band"] for p in spec.get("parts", []) if "pitch" in p]
    return bands[0] if bands else None


def measure(path: str, spec: dict[str, Any], reference: Any = None) -> dict[str, Any]:
    doc = App.openDocument(path)
    try:
        objs = [o for o in doc.Objects if _is_result(o)]
        want_teeth = any("teeth" in p for p in spec.get("parts", []))
        parts, shapes, seen = [], [], set()
        for o in objs:
            if o.TypeId.startswith("Mesh::"):
                parts.append(_mesh_part(o))
                continue
            s = _global_shape(o)
            key = (round(s.Volume, 3), round(s.BoundBox.Center.x, 3), round(s.BoundBox.Center.y, 3),
                   round(s.BoundBox.Center.z, 3))
            if key in seen:   # a link and its source, shown twice
                continue
            seen.add(key)
            part = _solid_part(o, s, want_teeth)
            band = _pitch_band(spec)
            if band:
                try:
                    part["thread"] = thread_pitch(s, band)
                except Exception:  # noqa: BLE001
                    part["thread"] = None
            parts.append(part)
            shapes.append((o, s))
        hits, gaps = [], []
        for i in range(len(shapes)):
            for j in range(i + 1, len(shapes)):
                (oa, sa), (ob, sb) = shapes[i], shapes[j]
                try:
                    gaps.append({"a": oa.Label, "b": ob.Label, "d": r4(sa.distToShape(sb)[0])})
                except Exception:  # noqa: BLE001
                    pass
                if not sa.BoundBox.intersect(sb.BoundBox):
                    continue
                try:
                    vol = sa.common(sb).Volume
                except Exception:  # noqa: BLE001 - a failed boolean is reported as unknown overlap
                    vol = -1.0
                if vol > INTERFERENCE_MM3 or vol < 0:
                    hits.append({"a": oa.Label, "b": ob.Label, "volume": r4(vol)})
        bb = None
        for p in parts:
            c, d = p["center"], p["dims"]
            box = App.BoundBox(c[0] - d[0] / 2, c[1] - d[1] / 2, c[2] - d[2] / 2,
                               c[0] + d[0] / 2, c[1] + d[1] / 2, c[2] + d[2] / 2)
            bb = box if bb is None else (bb.add(box) or bb)
        out: dict[str, Any] = {
            "parts": parts, "interference": hits, "gaps": gaps,
            "total": [r4(bb.XLength), r4(bb.YLength), r4(bb.ZLength)] if bb else None,
            "total_z": r4(bb.ZLength) if bb else None,
            "params": _params(doc),
            "expressions": sum(len(getattr(o, "ExpressionEngine", [])) for o in doc.Objects),
            "types": dict(Counter(o.TypeId for o in doc.Objects)),
            "joints": sorted(str(o.JointType) for o in doc.Objects if hasattr(o, "JointType")),
            "sketches": [],
        }
        for o in doc.Objects:
            if o.TypeId == "Sketcher::SketchObject":
                try:
                    o.solve()
                    out["sketches"].append({"label": o.Label, "dof": int(getattr(o, "DoF", 0) or 0)})
                except Exception:  # noqa: BLE001
                    out["sketches"].append({"label": o.Label, "dof": -1})
        out["iou"] = None
        if reference is not None and shapes:
            solids = [sol for _, s in shapes for sol in s.Solids]
            cand = solids[0] if len(solids) == 1 else solids[0].fuse(solids[1:])
            try:
                out["iou"] = iou(cand, reference)
            except Exception:  # noqa: BLE001
                out["iou"] = None
        doc.recompute()
        out["errors"] = _errors(doc)
        flex = spec.get("flex")
        out["flex"] = _flex(doc, flex["params"], flex.get("factors", [0.8, 1.2]),
                            [o for o, _ in shapes]) if flex else []
        return out
    finally:
        App.closeDocument(doc.Name)
