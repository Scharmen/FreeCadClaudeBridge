"""Read-only inspection ops: topology, sketches, API introspection, commands, journal, console."""

from __future__ import annotations

import inspect
import math
from typing import Any

import FreeCAD as App
import FreeCADGui as Gui

from . import protocol
from .executor import OpError
from .ops import _doc, _obj
from .serialize import num

MAX_DOC = 1500


def _vec(v: Any) -> list[float]:
    return [float(f"{v.x:.6g}") + 0.0, float(f"{v.y:.6g}") + 0.0, float(f"{v.z:.6g}") + 0.0]  # no -0.0


def _face_info(i: int, f: Any) -> dict[str, Any]:
    surf = f.Surface
    info: dict[str, Any] = {"ref": f"Face{i}", "type": type(surf).__name__, "area": num(f.Area, "mm²"),
                            "center": _vec(f.CenterOfMass)}
    try:
        u0, u1, v0, v1 = f.ParameterRange
        info["normal"] = _vec(f.normalAt((u0 + u1) / 2, (v0 + v1) / 2))
    except Exception:
        pass
    for attr, key in (("Radius", "radius"), ("Axis", "axis"), ("Center", "axis_point")):
        if hasattr(surf, attr) and info["type"] != "Plane":
            val = getattr(surf, attr)
            info[key] = num(val, "mm") if isinstance(val, float) else _vec(val)
    return info


def _edge_info(i: int, e: Any) -> dict[str, Any]:
    curve = e.Curve
    info: dict[str, Any] = {"ref": f"Edge{i}", "type": type(curve).__name__, "length": num(e.Length, "mm"),
                            "center": _vec(e.CenterOfMass)}
    if info["type"] in ("Line", "LineSegment"):
        d = e.Vertexes[-1].Point - e.Vertexes[0].Point
        if d.Length > 0:
            info["direction"] = _vec(d.normalize())
    if hasattr(curve, "Radius"):
        info["radius"] = num(curve.Radius, "mm")
    if hasattr(curve, "Axis") and info["type"] not in ("Line", "LineSegment"):
        info["axis"] = _vec(curve.Axis)
    info["vertices"] = [_vec(v.Point) for v in e.Vertexes]
    return info


def inspect_shape(args: dict[str, Any], ex: Any) -> dict[str, Any]:
    doc = _doc(args.get("doc"))
    obj = _obj(doc, str(args.get("name", "")))
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        raise OpError(protocol.NOT_FOUND, f"'{obj.Name}' has no shape")
    level = args.get("level", "faces")
    limit, offset = int(args.get("limit", 100)), int(args.get("offset", 0))
    if level == "faces":
        items = [_face_info(i, f) for i, f in enumerate(shape.Faces, 1)][offset:offset + limit]
        total = len(shape.Faces)
    elif level == "edges":
        items = [_edge_info(i, e) for i, e in enumerate(shape.Edges, 1)][offset:offset + limit]
        total = len(shape.Edges)
    elif level == "vertices":
        items = [{"ref": f"Vertex{i}", "point": _vec(v.Point)} for i, v in enumerate(shape.Vertexes, 1)]
        total = len(items)
        items = items[offset:offset + limit]
    else:
        raise OpError(protocol.INVALID_ARGS, "level must be faces, edges or vertices")
    return {"object": obj.Name, "level": level, "total": total, "offset": offset, "items": items,
            "more": offset + limit < total}


GEO_POINTS = {1: "start", 2: "end", 3: "center"}


def _geo_info(i: int, g: Any, construction: bool) -> dict[str, Any]:
    t = type(g).__name__
    info: dict[str, Any] = {"index": i, "type": t}
    if construction:
        info["construction"] = True
    if hasattr(g, "StartPoint") and hasattr(g, "EndPoint"):
        info["start"], info["end"] = _vec(g.StartPoint), _vec(g.EndPoint)
    if hasattr(g, "Center"):
        info["center"] = _vec(g.Center)
    if hasattr(g, "Radius"):
        info["radius"] = num(g.Radius, "mm")
    if t == "Point":
        info["point"] = [float(f"{g.X:.6g}"), float(f"{g.Y:.6g}"), float(f"{g.Z:.6g}")]
    return info


def inspect_sketch(args: dict[str, Any], ex: Any) -> dict[str, Any]:
    doc = _doc(args.get("doc"))
    sk = _obj(doc, str(args.get("name", "")))
    if not sk.isDerivedFrom("Sketcher::SketchObject"):
        raise OpError(protocol.INVALID_ARGS, f"'{sk.Name}' is a {sk.TypeId}, not a sketch")
    status = sk.solve()
    geos = []
    for i, g in enumerate(sk.Geometry):
        try:
            constr = sk.getConstruction(i)
        except Exception:
            constr = False
        geos.append(_geo_info(i, g, constr))
    cons = []
    for i, c in enumerate(sk.Constraints):
        item: dict[str, Any] = {"index": i, "type": c.Type, "first": c.First}
        if c.FirstPos:
            item["first_pos"] = GEO_POINTS.get(c.FirstPos, c.FirstPos)
        if c.Second not in (-2000, None) and c.Type not in ("Horizontal", "Vertical", "Block"):
            item["second"] = c.Second
            if c.SecondPos:
                item["second_pos"] = GEO_POINTS.get(c.SecondPos, c.SecondPos)
        if c.Type in ("Distance", "DistanceX", "DistanceY", "Radius", "Diameter", "Angle"):
            item["value"] = num(math.degrees(c.Value), "°") if c.Type == "Angle" else num(c.Value, "mm")
        if c.Name:
            item["name"] = c.Name
        if not c.Driving:
            item["reference"] = True
        cons.append(item)
    return {
        "name": sk.Name, "label": sk.Label, "solver_status": status, "dof": sk.DoF,
        "fully_constrained": bool(sk.FullyConstrained),
        "conflicting": list(sk.ConflictingConstraints), "redundant": list(sk.RedundantConstraints),
        "partially_redundant": list(sk.PartiallyRedundantConstraints),
        "malformed": list(sk.MalformedConstraints),
        "geometry": geos[:200], "constraints": cons[:300],
        "note": "geometry -1 = X axis, -2 = Y axis, <= -3 = external geometry",
    }


def _doc_text(obj: Any) -> str:
    doc = inspect.getdoc(obj) or ""
    return doc if len(doc) <= MAX_DOC else doc[:MAX_DOC] + "…"


def _props_of(obj: Any) -> list[dict[str, Any]]:
    out = []
    for p in obj.PropertiesList:
        item: dict[str, Any] = {"name": p, "type": obj.getTypeIdOfProperty(p)}
        try:
            d = obj.getDocumentationOfProperty(p)
            if d:
                item["doc"] = d[:200]
        except Exception:
            pass
        try:
            group = obj.getGroupOfProperty(p)
            if group:
                item["group"] = group
        except Exception:
            pass
        out.append(item)
    return out


def api_help(args: dict[str, Any], ex: Any) -> dict[str, Any]:
    target = str(args.get("target", "")).strip()
    if not target:
        raise OpError(protocol.INVALID_ARGS, "target is empty (e.g. 'Part.makeBox', 'PartDesign::Pad', 'Box')")
    if "::" in target:  # a FreeCAD type: instantiate in a throwaway document
        try:
            tmp = App.newDocument("ClaudeApiHelp", hidden=True, temp=True)
        except TypeError:
            tmp = App.newDocument("ClaudeApiHelp")
        try:
            try:
                obj = tmp.addObject(target, "Probe")
            except Exception as e:
                raise OpError(protocol.NOT_FOUND, f"unknown type '{target}': {e}") from None
            methods = sorted(m for m in dir(obj) if not m.startswith("_") and callable(getattr(obj, m, None)))
            return {"target": target, "kind": "type", "properties": _props_of(obj), "methods": methods[:150]}
        finally:
            App.closeDocument(tmp.Name)
    doc = App.ActiveDocument
    if doc is not None and "." not in target:
        obj = doc.getObject(target) or (doc.getObjectsByLabel(target) or [None])[0]
        if obj is not None:
            return {"target": target, "kind": "object", "type": obj.TypeId, "properties": _props_of(obj),
                    "methods": sorted(m for m in dir(obj) if not m.startswith("_"))[:150]}
    parts = target.split(".")
    try:
        value: Any = __import__(parts[0])
    except ImportError:
        ns = ex.namespace
        if parts[0] not in ns:
            raise OpError(protocol.NOT_FOUND, f"cannot resolve '{target}'") from None
        value = ns[parts[0]]
    for p in parts[1:]:
        try:
            value = getattr(value, p)
        except AttributeError:
            raise OpError(protocol.NOT_FOUND, f"'{target}': no attribute '{p}'") from None
    out: dict[str, Any] = {"target": target, "kind": type(value).__name__, "doc": _doc_text(value)}
    try:
        out["signature"] = str(inspect.signature(value))
    except (TypeError, ValueError):
        pass
    if inspect.ismodule(value) or inspect.isclass(value):
        out["members"] = [m for m in dir(value) if not m.startswith("_")][:300]
    return out


def list_commands(args: dict[str, Any], ex: Any) -> dict[str, Any]:
    flt = (args.get("filter") or "").lower()
    names = Gui.Command.listAll()
    wb_name = args.get("workbench")
    if wb_name:
        try:
            wb = Gui.getWorkbench(wb_name)
        except Exception:
            raise OpError(protocol.NOT_FOUND, f"unknown workbench '{wb_name}' "
                          f"(known: {', '.join(sorted(Gui.listWorkbenches()))})") from None
        cmds: set[str] = set()
        for items in wb.getToolbarItems().values():
            cmds |= {c for c in items if c != "Separator"}
        names = [n for n in names if n in cmds]
    out = []
    for n in sorted(names):
        try:
            info = Gui.Command.get(n).getInfo()
        except Exception:
            continue
        text = f"{n} {info.get('menuText', '')} {info.get('toolTip', '')}".lower()
        if flt and flt not in text:
            continue
        try:
            active = bool(Gui.Command.get(n).isActive())
        except Exception:
            active = None
        out.append({"name": n, "menu": (info.get("menuText") or "").replace("&", ""),
                    "tooltip": (info.get("toolTip") or "")[:150], "active": active})
    limit = int(args.get("limit", 100))
    return {"total": len(out), "commands": out[:limit], "more": len(out) > limit}


def get_changes(args: dict[str, Any], ex: Any) -> dict[str, Any]:
    return ex.journal.since(int(args.get("since", 0)), args.get("source", "all"), int(args.get("limit", 200)))


def get_console(args: dict[str, Any], ex: Any) -> dict[str, Any]:
    out = ex.console.since(int(args.get("since", 0)), args.get("level", "message"), int(args.get("limit", 200)))
    if ex._console_watcher is None:
        out["note"] = "Report view not found; console capture unavailable"
    return out

