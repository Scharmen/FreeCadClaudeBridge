# ruff: noqa: E501
"""Build the print files. Runs inside freecadcmd on a saved copy, so the open document stays as it is.

The whole model goes out as one coloured print. Split, touching bodies print together as long as they fit the bed
and the printer's colour slots, so only what one print cannot hold becomes a part of its own. Each such print is
joined once, to the biggest print it touches: by a keyed peg, a pocket or its own seat, by loose dowels between two
big parts, and by glue only when none of them fits. Parts that should turn get axles instead.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any

import FreeCAD as App
import Mesh
import Part

from . import plan, threemf
from .common import CUT_KEEPS, THIN_SHARE, Item, Piece, _bbox_near, _safe_name, _say, _step
from .joints import _apply_cuts, _apply_joints, _drop_dust, _join, _loose, _take
from .moving import _plan_moving
from .pieces import _centre, _make_pieces, _merge_thin, _touching
from .writers import _best_pose, _closed, _connector_plate, _matrix, _mesh, _together, _triangles, _whole, _write_stl


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
    folders = {"whole": "Ganz" if lang == "de" else "Whole", "plates": "Platten" if lang == "de" else "Plates",
               "parts": "Teile" if lang == "de" else "Parts", "joints": "Verbinder" if lang == "de" else "Connectors",
               "preview": "vorschau"}
    plan_only = bool(options.get("plan_only"))
    for f in [] if plan_only else folders.values():
        os.makedirs(os.path.join(out_dir, f), exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)
    warn: list[str] = []
    report: dict[str, Any] = {"name": name, "printer": printer, "join": options.get("join", "glue"), "lang": lang,
                              "whole": None, "together": None, "prints": [], "pieces": [], "plates": [], "joints": [], "moving": [], "painted": [],
                              "plan_only": plan_only,
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
        if options.get("whole", True) and not plan_only:
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

def _contacts(pieces: list[Piece]) -> set[frozenset[str]]:
    """Which pieces touch. What turns touches only the other pieces of the same moving part."""
    return {frozenset((a.id, b.id)) for i, a in enumerate(pieces) for b in pieces[i + 1:]
            if a.rotor is b.rotor and _touching(a, b)}

def _group(pieces: list[Piece], touching: set[frozenset[str]], slots: int, bed: list[float]) -> list[list[Piece]]:
    by_id = {p.id: p for p in pieces}
    # by the space a body takes, not its material: a hollow shell leads its print, not the block inside it
    bodies = [{"id": p.id, "color": p.color, "volume": _box_volume([p]), "lo": [p.box.XMin, p.box.YMin, p.box.ZMin],
               "hi": [p.box.XMax, p.box.YMax, p.box.ZMax]} for p in pieces]
    return [[by_id[i] for i in group] for group in plan.group_prints(bodies, [tuple(t) for t in touching], slots, bed)]

def _box_volume(unit: list[Piece]) -> float:
    box = App.BoundBox(unit[0].box)
    for p in unit[1:]:
        box.add(p.box)
    return box.XLength * box.YLength * box.ZLength

def _split(items: list[Item], options: dict[str, Any], report: dict[str, Any], lim: dict[str, float],
           folders: dict[str, str], out_dir: str, warn: list[str], lang: str) -> None:
    printer = options["printer"]
    slots, bed = int(printer["slots"]), printer["bed"]
    plan_only = bool(options.get("plan_only"))
    moving = list(options.get("moving") or [])
    axle_names = {m["axle"] for m in moving if m.get("axle")}
    rotor_of = {n: m for m in moving for n in m["objects"]}
    parts = [it for it in items if it.name not in axle_names]
    fixed = [it for it in parts if it.name not in rotor_of]
    colour_groups = plan.group_colors({it.name: it.color for it in parts})
    if options.get("merge_colors"):                 # the user's choice: shades share a filament until the slots are enough
        number = plan.assign_slots(colour_groups, slots)
        palette = plan.slot_colors(colour_groups, number)
        merged = {c: palette[number[n] - 1] for n, c in colour_groups.items()}
        report["merged_colors"] = [{"from": plan.color_name(a, lang), "to": plan.color_name(b, lang)}
                                   for a, b in merged.items() if a != b]
        colour_groups = {n: merged[c] for n, c in colour_groups.items()}
    for it in parts:
        it.color = colour_groups[it.name]
    report["colors"] = len(set(colour_groups.values()))
    pieces = _make_pieces(fixed, bed)
    for m in moving:
        group = [it for it in parts if it.name in m["objects"]]
        for p in _make_pieces(group, bed):
            p.rotor = m
            pieces.append(p)
    pieces.sort(key=lambda p: -p.volume)
    used: dict[str, int] = {}
    for p in pieces:
        base = _safe_name(p.labels[0])
        used[base] = used.get(base, 0) + 1
        p.id = base if used[base] == 1 else f"{base}_{used[base]}"
    _step(f"{len(pieces)} bodies")
    touching = _contacts(pieces)
    near: dict[str, set[str]] = {p.id: set() for p in pieces}
    for a, b in (tuple(t) for t in touching):
        near[a].add(b)
        near[b].add(a)
    # which bodies print together
    units = _group([p for p in pieces if p.rotor is None], touching, slots, bed)
    for m in moving:
        units += _group([p for p in pieces if p.rotor is m], touching, slots, bed)
    # a thin body that would be a print of its own goes into its neighbour instead
    alone = {u[0].id for u in units if len(u) == 1}
    pieces, painted, into = _merge_thin(pieces, THIN_SHARE * lim["min_thick"], alone, near)
    report["painted"] = painted
    for thin, host in into.items():
        touching = {t for t in touching if thin not in t} | {frozenset((host, o)) for o in near[thin] if o != host}
    units = [u for u in units if u[0].id not in into]
    for u in units:
        for p in u:
            p.unit = u[0].id
    unit = {u[0].id: u for u in units}
    _step(f"{len(units)} prints, thin parts merged: {len(painted)}")
    report["moving"], axles = _plan_moving(pieces, items, moving, lim, _centre(pieces), warn, lang)
    _step("moving parts planned")
    # every print is joined once: of all contacts, the ones that need the least glue
    by_id = {p.id: p for p in pieces}
    beside = {frozenset((by_id[a].unit, by_id[b].unit)) for a, b in (tuple(t) for t in touching)
              if by_id[a].unit != by_id[b].unit}
    volume = {uid: _box_volume(u) for uid, u in unit.items()}
    found = []
    for a, b in (sorted(t, key=lambda i: (volume[i], i)) for t in beside):       # the smaller print gets the peg
        found.append(_join(a, b, unit, touching, options.get("join", "glue"), lim, lang))
    order: list[str] = []
    joints: list[dict[str, Any]] = []
    lengths: list[float] = []
    pending: list[dict[str, Any]] = []
    for rotor in [None, *moving]:
        ids = {uid for uid, u in unit.items() if u[0].rotor is rotor}
        if not ids:
            continue
        hub = None
        if rotor is not None:
            entry = next((m for m in report["moving"] if set(m["pieces"]) & {p.id for i in ids for p in unit[i]}), None)
            hub = next((by_id[i].unit for i in (entry or {}).get("hub", [])), None)
        index = [n for n, f in enumerate(found) if f[0]["a"] in ids and f[0]["b"] in ids]
        part_order, chosen, loose = plan.join_tree(
            {i: volume[i] for i in ids}, [(found[n][0]["a"], found[n][0]["b"], found[n][0]["type"] == "glue") for n in index], hub)
        order += part_order
        joints += [_take(found[index[n]], lengths, pending) for n in chosen]
        joints += [_loose(uid, unit, lang) for uid in loose if rotor is None]
    _step(f"{len(joints)} joints")
    _apply_cuts(pieces, warn, lang)
    _apply_joints(pending, lang)
    _drop_dust(pieces)
    _step("joints cut")
    report["joints"] = joints
    report["order"] = order
    for m in report["moving"]:
        m["pieces"] = list(dict.fromkeys(by_id[i].unit for i in m["pieces"]))
        m["hub"] = list(dict.fromkeys(by_id[i].unit for i in m["hub"]))
        if m.get("host_id") in by_id:
            m["host_id"] = by_id[m["host_id"]].unit
    # each print lies on the bed as one object with its colours
    fine = 0.3 if plan_only else 0.03
    posed: dict[str, dict[str, Any]] = {}
    size: dict[str, tuple[float, float, float]] = {}
    info: dict[str, dict[str, Any]] = {}
    for uid, members in unit.items():
        meshes = {p.id: _mesh(p.shape, fine) if p.shape is not None else p.mesh.copy() for p in members}
        both = Mesh.Mesh()
        for p in members:
            p.closed = _closed(meshes[p.id])
            both.addMesh(meshes[p.id])
        turn, support = _best_pose(both, tuple(a for p in members for a in p.pegs))
        both.transform(_matrix(turn))
        box = both.BoundBox
        for m in meshes.values():
            m.transform(_matrix(turn))
            m.translate(-box.XMin, -box.YMin, -box.ZMin)
        posed[uid] = meshes
        size[uid] = (box.XLength, box.YLength, box.ZLength)
        colours = list(dict.fromkeys(p.color for p in members))
        info[uid] = {"id": uid, "pieces": [p.id for p in members], "objects": [n for p in members for n in p.labels],
                     "colors": colours, "color_names": [plan.color_name(c, lang) for c in colours],
                     "size_mm": [round(v, 1) for v in size[uid]], "volume": round(sum(p.volume for p in members), 1),
                     "moving": members[0].rotor is not None, "support": bool(support), "plate": None, "file": None,
                     **plan.color_layers([(p.color, meshes[p.id].BoundBox.ZMin, meshes[p.id].BoundBox.ZMax) for p in members])}
    _step("prints laid on the bed")
    number = 0
    for group in plan.plate_groups({uid: set(info[uid]["colors"]) for uid in order}, slots):
        palette = list(dict.fromkeys(c for uid in group for c in info[uid]["colors"]))
        placed, too_big = plan.layout([(uid, *size[uid]) for uid in group], bed)
        for uid in too_big:
            warn.append(_say(lang, f"{unit[uid][0].labels[0]} ist größer als der Bauraum und muss geteilt oder kleiner skaliert werden.",
                             f"{unit[uid][0].labels[0]} is larger than the build volume: split it or scale it down."))
        plates: dict[int, list[tuple]] = {}
        for uid in group:
            spot = placed.get(uid)
            if not spot:
                continue
            meshes = posed[uid]
            if spot["turned"]:
                for m in meshes.values():
                    m.rotate(0, 0, math.pi / 2)
                x0 = min(m.BoundBox.XMin for m in meshes.values())
                y0 = min(m.BoundBox.YMin for m in meshes.values())
                for m in meshes.values():
                    m.translate(-x0, -y0, 0)
            info[uid]["plate"] = number + int(spot["plate"])
            if not plan_only:
                plates.setdefault(int(spot["plate"]), []).append(
                    (uid, [(p.labels[0], palette.index(p.color) + 1, *_triangles(meshes[p.id])) for p in unit[uid]],
                     (spot["x"], spot["y"], 0.0)))
        word = "Platte" if lang == "de" else "Plate"
        for local, objects in sorted(plates.items()):
            rel = os.path.join(folders["plates"], f"{word}_{number + local}.3mf")
            threemf.write(os.path.join(out_dir, rel), objects, palette)
            names = [plan.color_name(c, lang) for c in palette]
            report["plates"].append({"file": rel, "number": number + local, "prints": [o[0] for o in objects],
                                     "pieces": [o[0] for o in objects], "colors": palette, "color_names": names,
                                     "color": palette[0], "color_name": ", ".join(names)})
            for o in objects:
                info[o[0]]["file"] = rel
        number += max([int(s["plate"]) for s in placed.values()], default=0)
    report["prints"] = [info[uid] for uid in order]
    for p in pieces:
        m = posed[p.unit][p.id]
        box = m.BoundBox
        entry = {"id": p.id, "print": p.unit, "objects": p.labels, "color": p.color, "color_name": plan.color_name(p.color, lang),
                 "volume": round(p.volume, 1), "size_mm": [round(box.XLength, 1), round(box.YLength, 1), round(box.ZLength, 1)],
                 "closed": p.closed, "stl": None, "brep": None, "preview": None, "plate": info[p.unit]["plate"],
                 "moving": p.rotor is not None, "support": info[p.unit]["support"]}
        if not p.closed:
            warn.append(_say(lang, f"{p.labels[0]}: das Netz ist nicht ganz geschlossen; der Slicer repariert das meist.",
                             f"{p.labels[0]}: the mesh is not fully closed; a slicer usually repairs that."))
        if not plan_only:
            entry["stl"] = os.path.join(folders["parts"], f"{p.id}.stl")
            _write_stl(m, os.path.join(out_dir, entry["stl"]))
            if p.shape is not None:
                entry["brep"] = os.path.join(folders["parts"], f"{p.id}.brep")
                p.shape.exportBrep(os.path.join(out_dir, entry["brep"]))
            verts, tris = _triangles(_mesh(p.shape, 0.2) if p.shape is not None else p.mesh)
            entry["preview"] = os.path.join(folders["preview"], f"{p.id}.json")
            with open(os.path.join(out_dir, entry["preview"]), "w", encoding="utf-8") as f:
                json.dump({"verts": verts, "tris": tris, "color": p.color}, f)
        report["pieces"].append(entry)
    _step("files written")
    if lengths:
        spare = 4
        pins = [(ln, lim["dowel_d"], False) for ln in lengths] + [(max(lengths), lim["dowel_d"], False)] * spare
        report["dowels"] = {"count": len(lengths), "spare": spare, "d": lim["dowel_d"], "lengths": sorted(set(lengths)),
                            "hole_d": lim["dowel_d"] + lim["hole_gap"],
                            "file": None if plan_only else _connector_plate(
                                pins, out_dir, folders["joints"], "Stifte" if lang == "de" else "Pins", printer)}
    if axles:
        pins = [(a["length"], a["d"], a["kind"] == "headed") for a in axles]
        report["axles"] = {"count": len(axles), "items": axles,
                           "file": None if plan_only else _connector_plate(
                               pins, out_dir, folders["joints"], "Achsen" if lang == "de" else "Axles", printer)}
    report["kit"] = plan.kit_summary(report)
