# ruff: noqa: E501
"""Build the print files. Runs inside freecadcmd on a saved copy, so the open document stays as it is.

The whole model goes out as one coloured print. Split, every colour gets its own plates: touching parts of one
colour become one piece, pieces too thin to print go into a neighbour, touching pieces get dowel holes where both
sides have room (otherwise they are glued), and parts that should turn get axles instead of glue.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any

import FreeCAD as App
import Part

from . import plan, threemf
from .common import CUT_KEEPS, THIN_SHARE, Item, Piece, _bbox_near, _safe_name, _say, _step
from .joints import _apply_cuts, _drop_dust, _make_joints
from .moving import _plan_moving
from .pieces import _centre, _join_again, _make_pieces, _merge_thin
from .writers import _connector_plate, _mesh, _pose_mesh, _together, _triangles, _whole, _write_stl


def _load(doc: Any, colors: dict[str, str]) -> list[Item]:
    items = []
    for name, color in colors.items():
        o = doc.getObject(name)
        if o is None:
            continue
        if o.TypeId.startswith("Mesh::"):
            m = o.Mesh.copy()
            m.Placement = o.getGlobalPlacement()
            items.append(Item(name, o.Label, color, mesh=m))
            continue
        shape = o.Shape.copy()
        shape.Placement = o.getGlobalPlacement()
        if shape.Solids:
            items.append(Item(name, o.Label, color, shape=shape))
    return items

def _clean_overlaps(items: list[Item], warn: list[str], lang: str) -> None:
    """The smaller part keeps the space two parts share, so a slicer has one answer for each point."""
    solids = sorted((it for it in items if it.shape is not None), key=lambda it: it.volume)
    for i, big in enumerate(solids):
        for small in solids[:i]:
            if not _bbox_near(big.box, small.box, 0.0):
                continue
            try:
                shared = big.shape.common(small.shape).Volume
                if shared <= 1e-3:
                    continue
                cut = big.shape.cut(small.shape)
                if cut.isValid() and cut.Solids and cut.Volume >= CUT_KEEPS * big.volume:
                    big.shape = cut
                else:
                    warn.append(_say(lang, f"{big.label} und {small.label} überlappen sich, das ließ sich nicht trennen.",
                                     f"{big.label} and {small.label} overlap and could not be separated."))
            except Part.OCCError:
                continue

def _moving_ids(report: dict[str, Any]) -> set[str]:
    return {i for m in report["moving"] for i in m["pieces"]}

def run(path: str, options: dict[str, Any], out_dir: str) -> dict[str, Any]:
    """Write everything into out_dir and return the report the guide is made from."""
    lang = options.get("lang", "de")
    printer = options["printer"]
    lim = plan.limits(printer)
    name = options.get("name", "Modell")
    folders = {"whole": "Ganz" if lang == "de" else "Whole", "colors": "Farben" if lang == "de" else "Colours",
               "parts": "Teile" if lang == "de" else "Parts", "joints": "Verbinder" if lang == "de" else "Connectors",
               "preview": "vorschau"}
    for f in folders.values():
        os.makedirs(os.path.join(out_dir, f), exist_ok=True)
    warn: list[str] = []
    report: dict[str, Any] = {"name": name, "printer": printer, "join": options.get("join", "glue"), "lang": lang,
                              "whole": None, "together": None, "pieces": [], "plates": [], "joints": [], "moving": [], "painted": [],
                              "order": [], "warnings": warn, "dowels": {"count": 0, "file": None, "d": lim["dowel_d"]},
                              "axles": {"count": 0, "file": None}, "limits": lim}
    doc = App.openDocument(path)
    try:
        items = _load(doc, options["colors"])
        if not items:
            raise ValueError("nothing to print")
        _step(f"{len(items)} objects")
        _clean_overlaps(items, warn, lang)
        _step("overlaps cleaned")
        if options.get("whole", True):
            report["whole"] = _whole(items, printer, name, out_dir, folders["whole"], lang)
            report["together"] = _together(items, options, lim, folders["whole"], folders["joints"], out_dir, warn, lang)
            _step("whole model written")
        if options.get("split", True):
            _split(items, options, report, lim, folders, out_dir, warn, lang)
    finally:
        App.closeDocument(doc.Name)
    report["report"] = "report.json"
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    return report

def _split(items: list[Item], options: dict[str, Any], report: dict[str, Any], lim: dict[str, float],
           folders: dict[str, str], out_dir: str, warn: list[str], lang: str) -> None:
    printer = options["printer"]
    moving = list(options.get("moving") or [])
    axle_names = {m["axle"] for m in moving if m.get("axle")}
    rotor_of = {n: m for m in moving for n in m["objects"]}
    parts = [it for it in items if it.name not in axle_names]
    fixed = [it for it in parts if it.name not in rotor_of]
    colour_groups = plan.group_colors({it.name: it.color for it in parts})
    for it in parts:
        it.color = colour_groups[it.name]
    pieces = _make_pieces(fixed, printer["bed"])
    for m in moving:
        group = [it for it in parts if it.name in m["objects"]]
        for p in _make_pieces(group, printer["bed"]):
            p.rotor = m
            pieces.append(p)
    _step(f"{len(pieces)} pieces")
    pieces, painted = _merge_thin(pieces, THIN_SHARE * lim["min_thick"])
    _step(f"thin parts merged: {len(painted)}")
    report["painted"] = painted
    pieces = _join_again(pieces, printer["bed"])
    pieces.sort(key=lambda p: -p.volume)
    used: dict[str, int] = {}
    for p in pieces:
        base = _safe_name(p.labels[0])
        used[base] = used.get(base, 0) + 1
        p.id = base if used[base] == 1 else f"{base}_{used[base]}"
    report["moving"], axles = _plan_moving(pieces, items, moving, lim, _centre(pieces), warn, lang)
    _step("moving parts planned")
    joints, lengths = _make_joints(pieces, options.get("join", "glue"), lim)
    report["joints"] = joints
    _step(f"{len(joints)} joints")
    _apply_cuts(pieces, warn, lang)
    _drop_dust(pieces)
    _step("holes cut")
    # one plate set for each colour
    by_color: dict[str, list[Piece]] = {}
    for p in pieces:
        by_color.setdefault(p.color, []).append(p)
    for color, group in by_color.items():
        cname = plan.color_name(color, lang)
        _step(f"writing {cname}: {len(group)} pieces")
        meshes = {p.id: _pose_mesh(p) for p in group}
        placed, too_big = plan.layout([(i, m.BoundBox.XLength, m.BoundBox.YLength, m.BoundBox.ZLength)
                                       for i, m in meshes.items()], printer["bed"])
        plates: dict[int, list[tuple]] = {}
        for p in group:
            m = meshes[p.id]
            stl = os.path.join(folders["parts"], f"{p.id}.stl")
            _write_stl(m, os.path.join(out_dir, stl))
            brep = os.path.join(folders["parts"], f"{p.id}.brep")
            if p.shape is not None:
                p.shape.exportBrep(os.path.join(out_dir, brep))
            preview = _mesh(p.shape, 0.2) if p.shape is not None else p.mesh
            verts, tris = _triangles(preview)
            prev = os.path.join(folders["preview"], f"{p.id}.json")
            with open(os.path.join(out_dir, prev), "w", encoding="utf-8") as f:
                json.dump({"verts": verts, "tris": tris, "color": color}, f)
            spot = placed.get(p.id)
            if p.id in too_big:
                warn.append(_say(lang, f"{p.labels[0]} ist größer als der Bauraum und muss geteilt oder kleiner skaliert werden.",
                                 f"{p.labels[0]} is larger than the build volume: split it or scale it down."))
            box = m.BoundBox
            entry = {"id": p.id, "objects": p.labels, "color": color, "color_name": cname, "volume": round(p.volume, 1),
                     "size_mm": [round(box.XLength, 1), round(box.YLength, 1), round(box.ZLength, 1)],
                     "closed": p.closed, "stl": stl, "brep": brep, "preview": prev,
                     "plate": spot["plate"] if spot else None, "moving": p.rotor is not None,
                     "support": bool(p.support)}
            report["pieces"].append(entry)
            if not p.closed:
                warn.append(_say(lang, f"{p.labels[0]}: das Netz ist nicht ganz geschlossen; der Slicer repariert das meist.",
                                 f"{p.labels[0]}: the mesh is not fully closed; a slicer usually repairs that."))
            if spot:
                if spot["turned"]:
                    m.rotate(0, 0, math.pi / 2)
                    b = m.BoundBox
                    m.translate(-b.XMin, -b.YMin, 0)
                plates.setdefault(int(spot["plate"]), []).append(
                    (p.id, [(p.id, 1, *_triangles(m))], (spot["x"], spot["y"], 0.0)))
        for number, objects in sorted(plates.items()):
            rel = os.path.join(folders["colors"], f"{_safe_name(cname)}_{number}.3mf")
            threemf.write(os.path.join(out_dir, rel), objects, [color])
            report["plates"].append({"file": rel, "color": color, "color_name": cname, "number": number,
                                     "pieces": [o[0] for o in objects]})
    if lengths:
        spare = 4
        pins = [(ln, lim["dowel_d"], False) for ln in lengths] + [(max(lengths), lim["dowel_d"], False)] * spare
        report["dowels"] = {"count": len(lengths), "spare": spare, "d": lim["dowel_d"], "lengths": sorted(set(lengths)),
                            "hole_d": lim["dowel_d"] + lim["hole_gap"],
                            "file": _connector_plate(pins, out_dir, folders["joints"],
                                                    "Stifte" if lang == "de" else "Pins", printer)}
    if axles:
        pins = [(a["length"], a["d"], a["kind"] == "headed") for a in axles]
        report["axles"] = {"count": len(axles), "items": axles,
                           "file": _connector_plate(pins, out_dir, folders["joints"],
                                                   "Achsen" if lang == "de" else "Axles", printer)}
    size = {p["id"]: p["size_mm"][0] * p["size_mm"][1] * p["size_mm"][2] for p in report["pieces"]}
    report["order"] = plan.assembly_order(
        [{"id": p["id"], "volume": size[p["id"]]} for p in report["pieces"] if not p["moving"]], [
            j for j in joints if not (j["a"] in _moving_ids(report) or j["b"] in _moving_ids(report))])
    for m in report["moving"]:
        rest = [i for i in m["pieces"] if i not in m["hub"]]
        report["order"] += m["hub"] + rest
