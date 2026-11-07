"""Measure a saved model, for the check tools and the benchmark. Runs inside freecadcmd.

Loaded by its file path, not as a package: FreeCAD's Python must not see the server's site-packages.

The result is plain JSON for `spec.evaluate`: the visible solids with size, holes, round faces and
surface types, plus tree errors, parameters, expressions, joints, interference, a flex test and the
IoU against a reference shape. Everything is measured from the saved file, so the same file always
gives the same numbers.
"""

from __future__ import annotations

import math
import os
import sys
from array import array
from collections import Counter
from typing import Any

import FreeCAD as App
import Part

V = App.Vector
CONTAINERS = {"App::Part", "Assembly::AssemblyObject", "App::DocumentObjectGroup", "App::Origin",
              "Assembly::JointGroup", "App::TextDocument", "App::VarSet", "Spreadsheet::Sheet"}
HOLE_SPAN = math.radians(250)   # a cylinder around at least this much of the circle is a hole, less is a fillet
OPEN_SPAN = math.radians(179)
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
        if not concave:
            radii.append(r4(surf.Radius))
            continue
        for g in groups:
            if abs(g["r"] - surf.Radius) < 1e-4 and axis.cross(g["axis"]).Length < 1e-3:
                d = (V(surf.Center) - g["point"])
                if (d - g["axis"] * d.dot(g["axis"])).Length < 1e-3:
                    g["span"] += span
                    g["widest"] = max(g["widest"], span)
                    break
        else:
            groups.append({"r": surf.Radius, "axis": axis, "point": V(surf.Center), "span": span, "widest": span})
    holes = []
    for g in groups:
        hole = {"d": r4(2 * g["r"]), "axis": [r4(x) for x in g["axis"]], "point": [r4(x) for x in g["point"]]}
        if g["span"] >= HOLE_SPAN:           # quarters of a bore count together
            holes.append(hole)
            continue
        if g["widest"] >= OPEN_SPAN:          # a bore open on one side (a clip ring), or a large concave fillet
            holes.append({**hole, "open": True})
        radii.append(r4(g["r"]))
    return holes, sorted(radii)


def volume_of(s: Any) -> float:
    """Volume of a shape. On long B-spline faces the kernel's number can be far off, so check it on triangles."""
    kernel = float(s.Volume)
    if not any(type(f.Surface).__name__ == "BSplineSurface" for f in s.Faces):
        return kernel
    pts, tris = s.tessellate(max(0.02, s.BoundBox.DiagonalLength / 1000))
    meshed = abs(sum(pts[a].dot(pts[b].cross(pts[c])) for a, b, c in tris)) / 6
    return meshed if abs(meshed - kernel) > 0.2 * max(meshed, 1e-9) else kernel


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


def pitch_from_crests(lines: list[list[float]], eps: float = 0.02) -> float | None:
    """The pitch of a helix from where lines at equal angles around the axis run through material.

    Going one line further, a helix moves by pitch / number of lines. A ring, a wall or a base does not
    move, so it casts no vote, and a thread of only a turn or two still counts, which repeats along one
    line would not show.
    """
    votes = []
    for here, there in zip(lines, lines[1:] + lines[:1], strict=True):
        for z in here:
            if there:
                dz = min((t - z for t in there), key=abs)
                if abs(dz) > eps:
                    votes.append(dz * len(lines))
    best, most = None, 0
    for v in votes:
        near = [w for w in votes if abs(w - v) <= 0.15 * abs(v) + eps]
        if len(near) > most:
            best, most = sorted(abs(w) for w in near)[len(near) // 2], len(near)
    return best if most >= 4 else None


def _line_runs(pts: Any, tris: Any, u: float, w: float) -> list[float]:
    """Middles of the stretches where a line along the first coordinate runs through the mesh."""
    import numpy as np

    a, b, c = pts[tris[:, 0]], pts[tris[:, 1]], pts[tris[:, 2]]
    ab, ac = b[:, 1:] - a[:, 1:], c[:, 1:] - a[:, 1:]
    ap = np.array([u, w]) - a[:, 1:]
    det = ab[:, 0] * ac[:, 1] - ab[:, 1] * ac[:, 0]
    good = np.abs(det) > 1e-12
    p = (ap[good, 0] * ac[good, 1] - ap[good, 1] * ac[good, 0]) / det[good]
    q = (ab[good, 0] * ap[good, 1] - ab[good, 1] * ap[good, 0]) / det[good]
    hit = (p >= 0) & (q >= 0) & (p + q <= 1)
    z = a[good][hit, 0] + p[hit] * (b[good][hit, 0] - a[good][hit, 0]) + q[hit] * (c[good][hit, 0] - a[good][hit, 0])
    z = np.sort(z)
    z = z[np.concatenate(([True], np.diff(z) > 1e-6))] if len(z) else z   # a line through a shared edge hits twice
    return [float((z[i] + z[i + 1]) / 2) for i in range(0, len(z) - 1, 2)]


def thread_pitch(s: Any, band: list[float], lines: int = 6) -> dict[str, Any] | None:
    """Pitch of a thread: lines parallel to the axis at the middle of the thread's radial band, at equal
    angles around it, and where they run through material (works for internal threads too).

    The axis is the box dimension that differs most from the other two (long for a bolt, short for a nut).
    Lines are cut with the triangles of the shape because sections of swept thread faces often fail.
    """
    import numpy as np

    bb = tight_box(s)
    dims = [bb.XLength, bb.YLength, bb.ZLength]
    k = max(range(3), key=lambda i: abs(dims[i] - (sum(dims) - dims[i]) / 2))
    verts, faces = s.tessellate(0.01)
    order = [k, (k + 1) % 3, (k + 2) % 3]
    pts = np.array([[v.x, v.y, v.z] for v in verts])[:, order]
    tris = np.array(faces)
    c = np.array([bb.Center.x, bb.Center.y, bb.Center.z])[order]
    r = (band[0] + band[1]) / 2
    found = []
    for j in range(lines):
        angle = 2 * math.pi * j / lines + 0.0137       # off the seams of round faces
        found.append(_line_runs(pts, tris, c[1] + r * math.cos(angle), c[2] + r * math.sin(angle)))
    pitch = pitch_from_crests(found)
    return {"crests": len(found[0]), "pitch": None if pitch is None else r4(pitch)}


def _solid_part(o: Any, s: Any, want_teeth: bool) -> dict[str, Any]:
    bb = tight_box(s)
    dims = [r4(bb.XLength), r4(bb.YLength), r4(bb.ZLength)]
    holes, radii = _holes_and_radii(s)
    p = {"label": o.Label, "type": o.TypeId, "dims": dims, "sorted": sorted(dims),
         "center": [r4(bb.Center.x), r4(bb.Center.y), r4(bb.Center.z)], "volume": r4(volume_of(s)),
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
        want_seat = any(c.get("seated") for c in spec.get("clearance", []))
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
                    gap = {"a": oa.Label, "b": ob.Label, "d": r4(sa.distToShape(sb)[0])}
                    if want_seat and gap["d"] < 1e-3:
                        contact = seat(sa, sb)
                        if contact and contact["side_gap"] is not None:
                            gap["side_gap"] = contact["side_gap"]
                    gaps.append(gap)
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


# ---------- for the check tools, on a saved copy of the user's document

def _pick(doc: Any, names: list[str] | None) -> list[Any]:
    if not names:
        return [o for o in doc.Objects if _is_result(o)]
    out = []
    for n in names:
        o = doc.getObject(n) or next((x for x in doc.Objects if x.Label == n), None)
        if o is None:
            known = ", ".join(x.Label for x in doc.Objects if _is_result(x))
            raise ValueError(f"no object '{n}' (visible results: {known})")
        out.append(o)
    return out


def export_meshes(path: str, names: list[str] | None, out_dir: str) -> list[dict[str, Any]]:
    """Each object as triangles in two binary files (little endian float64 points, int32 corners)."""
    doc = App.openDocument(path)
    try:
        out = []
        for i, o in enumerate(_pick(doc, names)):
            if o.TypeId.startswith("Mesh::"):
                pts, tris = o.Mesh.Topology
            else:
                s = _global_shape(o)
                diag = s.BoundBox.DiagonalLength
                pts, tris = s.tessellate(min(0.2, max(0.02, diag / 1000)))
            pv = array("d", [c for p in pts for c in (p.x, p.y, p.z)])
            tv = array("i", [c for t in tris for c in t])
            if sys.byteorder != "little":
                pv.byteswap()
                tv.byteswap()
            base = os.path.join(out_dir, f"mesh{i}")
            with open(base + ".pts", "wb") as f:
                pv.tofile(f)
            with open(base + ".tris", "wb") as f:
                tv.tofile(f)
            out.append({"label": o.Label, "base": base})
        return out
    finally:
        App.closeDocument(doc.Name)


def _surface_mesh(s: Any) -> tuple[list[Any], list[Any], list[Any]]:
    """Triangles with the true normals of the surface: mesh normals would show the triangles, not the shape."""
    points, normals, tris = [], [], []
    deflection = max(0.01, s.BoundBox.DiagonalLength / 4000)
    for face in s.Faces:
        pts, corners = face.tessellate(deflection)
        base, surface, flip = len(points), face.Surface, -1 if face.Orientation == "Reversed" else 1
        for p in pts:
            n = surface.normal(*surface.parameter(p))
            points.append(p)
            normals.append(n * flip)
        tris += [(a + base, b + base, c + base) for a, b, c in corners]
    return points, normals, tris


def _ripples(s: Any, label: str) -> list[dict[str, Any]]:
    """Changes of bending along lengthwise cuts: each one is a hollow or bump a reflection will show."""
    bb = s.BoundBox
    out = []
    along_x = bb.XLength >= bb.YLength
    normal = App.Vector(0, 1, 0) if along_x else App.Vector(1, 0, 0)
    low, length = (bb.YMin, bb.YLength) if along_x else (bb.XMin, bb.XLength)
    for share in (0.5, 0.65, 0.8):
        wires = s.slice(normal, low + length * share)
        if not wires:
            continue
        wire = max(wires, key=lambda w: w.Length)
        pts = [(p.x if along_x else p.y, p.z) for p in wire.discretize(Number=400)]
        turns = [math.atan2(b[1] - a[1], b[0] - a[0]) for a, b in zip(pts, pts[1:], strict=False)
                 if abs(b[0] - a[0]) + abs(b[1] - a[1]) > 1e-9]
        bends, last = 0, 0
        for a, b in zip(turns, turns[1:], strict=False):
            d = (b - a + math.pi) % (2 * math.pi) - math.pi
            if abs(d) > 0.002 and abs(d) < 0.3:    # above that: an edge between faces, not a ripple
                sign = 1 if d > 0 else -1
                bends += sign != last and last != 0
                last = sign
        out.append({"object": label, "cut_at": round(low + length * share, 2), "inflections": int(bends)})
    return out


def export_surfaces(path: str, names: list[str] | None, out_dir: str) -> dict[str, Any]:
    """Each object as triangles with surface normals (.pts, .nrm, .tris), and its ripple count."""
    doc = App.openDocument(path)
    try:
        items, ripples = [], []
        for i, o in enumerate(_pick(doc, names)):
            if o.TypeId.startswith("Mesh::") or not hasattr(o, "Shape"):
                continue
            s = _global_shape(o)
            points, normals, tris = _surface_mesh(s)
            base = os.path.join(out_dir, f"surface{i}")
            for ext, code, values in ((".pts", "d", [c for p in points for c in (p.x, p.y, p.z)]),
                                      (".nrm", "d", [c for n in normals for c in (n.x, n.y, n.z)]),
                                      (".tris", "i", [c for t in tris for c in t])):
                data = array(code, values)
                if sys.byteorder != "little":
                    data.byteswap()
                with open(base + ext, "wb") as f:
                    data.tofile(f)
            items.append({"label": o.Label, "base": base})
            ripples += _ripples(s, o.Label)
        return {"items": items, "ripples": ripples}
    finally:
        App.closeDocument(doc.Name)


def seat(sa: Any, sb: Any, touch: float = 1e-3, reach: float = 2.0, max_pairs: int = 20000) -> dict[str, Any] | None:
    """Two touching solids: do they touch only on flat faces of one direction (a seat), and how much room is
    left on the faces around it? None when they touch in other ways, or are too complex to tell."""
    pairs = []
    for fa in sa.Faces:
        box = fa.BoundBox
        box.enlarge(reach)
        for fb in sb.Faces:
            if box.intersect(fb.BoundBox):
                pairs.append((fa, fb))
                if len(pairs) > max_pairs:
                    return None

    def normal(f: Any) -> Any:
        return V(f.Surface.Axis) if type(f.Surface).__name__ == "Plane" else None

    def along(f: Any, axis: Any) -> bool:
        n = normal(f)
        return n is not None and abs(n.dot(axis)) > 0.999

    near = [(fa, fb, fa.distToShape(fb)[0]) for fa, fb in pairs]
    contacts: list[tuple[Any, float]] = []     # direction and area of each real contact on flat faces
    for fa, fb, d in near:
        na, nb = normal(fa), normal(fb)
        if d < touch and na is not None and nb is not None and abs(na.dot(nb)) > 0.999:
            area = fa.common(fb).Area
            if area > 1e-6:                     # a shared edge is no contact: flush sides meet at one
                contacts.append((na, area))
    if not contacts:
        return None
    totals: list[list[Any]] = []
    for n, area in contacts:
        for t in totals:
            if abs(t[0].dot(n)) > 0.999:
                t[1] += area
                break
        else:
            totals.append([n, area])
    axis = max(totals, key=lambda t: t[1])[0]   # the seat is the largest contact direction

    def side_distance(fa: Any, fb: Any, d: float) -> float | None:
        if along(fa, axis) or along(fb, axis):
            return None
        na, nb = normal(fa), normal(fb)
        if d < touch and na is not None and nb is not None:
            parallel = abs(na.dot(nb)) > 0.999
            return 0.0 if parallel and fa.common(fb).Area > 1e-6 else None   # else they only meet at an edge
        return d

    sides = [x for x in (side_distance(fa, fb, d) for fa, fb, d in near) if x is not None]
    return {"axis": [r4(axis.x), r4(axis.y), r4(axis.z)], "side_gap": r4(min(sides)) if sides else None}


def fit(path: str, a: str, b: str) -> dict[str, Any]:
    """Smallest gap and shared volume of two solids, and where they come closest."""
    doc = App.openDocument(path)
    try:
        oa, ob = _pick(doc, [a, b])
        for o in (oa, ob):
            if o.TypeId.startswith("Mesh::") or not hasattr(o, "Shape"):
                raise ValueError(f"'{o.Label}' is not a solid; check_fit works on solids")
        sa, sb = _global_shape(oa), _global_shape(ob)
        d, pairs, _ = sa.distToShape(sb)
        overlap, depth = 0.0, 0.0
        if sa.BoundBox.intersect(sb.BoundBox):
            shared = sa.common(sb)
            overlap = shared.Volume
            if overlap > 0:
                bb = shared.BoundBox
                depth = min(bb.XLength, bb.YLength, bb.ZLength)
        p = pairs[0][0] if pairs else sa.BoundBox.Center
        contact = seat(sa, sb) if d < 1e-3 and overlap <= 0.5 else None
        return {"a": oa.Label, "b": ob.Label, "gap": float(d), "overlap": float(overlap), "depth": float(depth),
                "seat": contact, "where": [r4(p.x), r4(p.y), r4(p.z)]}
    finally:
        App.closeDocument(doc.Name)


def param_names(path: str) -> list[str]:
    doc = App.openDocument(path)
    try:
        return list(_params(doc))[:20]
    finally:
        App.closeDocument(doc.Name)


def flex(path: str, params: list[str] | None, factors: list[float]) -> dict[str, Any]:
    """Change the parameters of the copy one by one and rebuild it. Nothing reaches the user's document."""
    doc = App.openDocument(path)
    try:
        names = params or list(_params(doc))[:20]
        missing = [n for n in names if not _find_param(doc, n)]
        parts = [o for o in doc.Objects if _is_result(o) and not o.TypeId.startswith("Mesh::")]
        doc.recompute()
        return {"params": names, "missing": missing, "before_errors": _errors(doc),
                "runs": _flex(doc, [n for n in names if n not in missing], factors, parts),
                "objects": [o.Label for o in parts]}
    finally:
        App.closeDocument(doc.Name)


def check_all(path: str, spec: dict[str, Any], fits: list[dict[str, Any]], out_dir: str) -> dict[str, Any]:
    """Everything the check tools measure, from one copy in one process: the spec, the meshes, each pair."""
    out: dict[str, Any] = {"design": measure(path, spec), "meshes": export_meshes(path, None, out_dir), "fits": []}
    for f in fits:
        try:
            out["fits"].append({**f, **fit(path, str(f.get("a", "")), str(f.get("b", "")))})
        except Exception as e:  # noqa: BLE001 - one bad pair must not hide the other results
            out["fits"].append({**f, "error": f"{type(e).__name__}: {e}"})
    return out
