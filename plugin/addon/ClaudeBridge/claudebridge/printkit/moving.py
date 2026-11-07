# ruff: noqa: E501
"""Parts that turn: axle holes in the wheels, bores or sockets in the host, and the axles to print."""

from __future__ import annotations

from typing import Any

import Part

from .common import Item, Piece, V, _bbox_near, _say


def _axis_vector(axis: str) -> Any:
    return {"x": V(1, 0, 0), "y": V(0, 1, 0), "z": V(0, 0, 1)}[axis]

def _spans(shape: Any, start: Any, end: Any) -> float:
    """How long the line runs inside the shape."""
    try:
        return float(shape.common(Part.makeLine(start, end)).Length)
    except Part.OCCError:
        return 0.0

def _plan_moving(pieces: list[Piece], items: list[Item], moving: list[dict[str, Any]], lim: dict[str, float],
                 model_center: Any, warn: list[str], lang: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Holes for everything that turns, and the axles to print."""
    out: list[dict[str, Any]] = []
    axles: list[dict[str, Any]] = []
    by_name = {it.name: it for it in items}
    done_axles: dict[str, dict[str, Any]] = {}
    for rotor in moving:
        parts = [p for p in pieces if p.rotor is rotor]
        if not parts:
            continue
        direction = _axis_vector(rotor["axis"])
        outer = max(parts, key=lambda p: max(p.box.XLength, p.box.YLength, p.box.ZLength))
        center = outer.box.Center
        i = "xyz".index(rotor["axis"])
        lo = min(p.box.getPoint(0)[i] for p in parts)
        hi = max(p.box.getPoint(6)[i] for p in parts)
        size = max(getattr(outer.box, a + "Length") for a in "XYZ" if a.lower() != rotor["axis"])
        inward = direction if model_center[i] > center[i] else direction * -1
        start = V(center)
        start[i] = lo if inward[i] > 0 else hi      # the face looking into the model
        far = V(center)
        far[i] = hi if inward[i] > 0 else lo
        d = max(2.0, min(lim["axle_d"], 0.35 * size))
        free_d, press_d = d + lim["free_gap"], d + lim["press_gap"]
        entry: dict[str, Any] = {"objects": sorted({n for p in parts for n in p.labels}), "pieces": [p.id for p in parts], "hub": [],
                                 "axis": rotor["axis"], "axle_d": round(d, 2), "free_d": round(free_d, 2),
                                 "press_d": round(press_d, 2), "center": [center.x, center.y, center.z]}
        width = hi - lo
        through = far - inward * (width + 2)
        axle_name = rotor.get("axle")
        if axle_name and axle_name in by_name:
            axle = by_name[axle_name]
            entry["type"] = "through"
            entry["axle"] = axle.label
            box = axle.box
            length = max(box.XLength, box.YLength, box.ZLength)
            if axle_name not in done_axles:
                a_lo = box.getPoint(0)[i]
                begin = V(center)
                begin[i] = a_lo - 1
                bore = Part.makeCylinder(free_d / 2, length + 2, begin, direction)
                for p in pieces:
                    if p.rotor is None and p.shape is not None and _bbox_near(p.box, bore.BoundBox, 0):
                        if p.shape.common(bore).Volume > 1e-3:
                            p.cuts.append(bore)
                done_axles[axle_name] = {"length": length, "d": max(d, min(box.XLength, box.YLength, box.ZLength))}
                axles.append({"kind": "plain", **done_axles[axle_name], "label": axle.label})
            hub = Part.makeCylinder(press_d / 2, width + 4, through, inward)
            for p in parts:
                if p.shape is not None and p.shape.common(hub).Volume > 1e-3:
                    p.cuts.append(hub)
                    entry["hub"].append(p.id)
        else:
            reach = start + inward * max(model_center.distanceToPoint(start) * 2, 1.0)
            best = None
            for p in pieces:
                if p.rotor is not None or p.shape is None:
                    continue
                if not _bbox_near(p.box, Part.makeLine(start, reach).BoundBox, 0):
                    continue
                inside = _spans(p.shape, start, reach)
                if inside >= 2.0:
                    seg = p.shape.common(Part.makeLine(start, reach))
                    gap = min((q.Point - start).Length for q in seg.Vertexes) if seg.Vertexes else 0.0
                    if best is None or gap < best[0]:
                        best = (gap, p, inside)
            if best is None:
                entry["type"] = "none"
                warn.append(_say(lang, f"{entry['objects'][0]}: kein Teil zum Einstecken der Achse gefunden; bitte selbst befestigen.",
                                 f"{entry['objects'][0]}: found no part to take the axle; please mount it yourself."))
                out.append(entry)
                continue
            gap, host, inside = best
            depth = min(inside - 0.6, 3 * d)
            entry.update(type="stub", host=host.labels[0], host_id=host.id, depth=round(depth, 2))
            socket_start = start + inward * gap
            host.cuts.append(Part.makeCylinder(press_d / 2, depth, socket_start, inward))
            hub = Part.makeCylinder(free_d / 2, width + 4, through, inward)
            for p in parts:
                if p.shape is not None and p.shape.common(hub).Volume > 1e-3:
                    p.cuts.append(hub)
                    entry["hub"].append(p.id)
            axles.append({"kind": "headed", "length": width + gap + depth, "d": d, "label": entry["objects"][0]})
        out.append(entry)
    return out, axles
