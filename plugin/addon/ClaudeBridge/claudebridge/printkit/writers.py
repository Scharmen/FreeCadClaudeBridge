# ruff: noqa: E501
"""The files: the whole model and the fixed parts as one print, moving parts, plates and connectors."""

from __future__ import annotations

import math
import os
from typing import Any

import FreeCAD as App
import Mesh
import MeshPart
import Part

from . import plan, threemf
from .common import MIN_BASE, Item, Piece, V, _safe_name, _say
from .joints import _apply_cuts, _drop_dust
from .moving import _plan_moving
from .pieces import _centre


def _mesh(shape: Any, fine: float = 0.03) -> Any:
    size = shape.BoundBox.DiagonalLength
    return MeshPart.meshFromShape(Shape=shape, LinearDeflection=max(fine, size / 8000), AngularDeflection=0.25)

def _closed(mesh: Any) -> bool:
    """Is the mesh watertight? Tries the repairs that do no harm first."""
    if mesh.isSolid() and not mesh.hasNonManifolds():
        return True
    mesh.removeDuplicatedPoints()
    mesh.removeDuplicatedFacets()
    mesh.removeNonManifolds()
    mesh.fillupHoles(50, 1)
    mesh.harmonizeNormals()
    return bool(mesh.isSolid() and not mesh.hasNonManifolds())

def _triangles(mesh: Any) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]]:
    index: dict[tuple[float, float, float], int] = {}
    verts: list[tuple[float, float, float]] = []
    tris: list[tuple[int, int, int]] = []
    for f in mesh.Facets:
        t = []
        for p in f.Points:
            key = (round(p[0], 4), round(p[1], 4), round(p[2], 4))
            if key not in index:
                index[key] = len(verts)
                verts.append(key)
            t.append(index[key])
        if len(set(t)) == 3:
            tris.append((t[0], t[1], t[2]))
    return verts, tris

ORIENTATIONS = [(0, 0, 0), (180, 0, 0), (90, 0, 0), (-90, 0, 0), (0, 90, 0), (0, -90, 0)]

def _matrix(turn: tuple[float, float, float]) -> Any:
    place = App.Placement(V(0, 0, 0), App.Rotation(V(1, 0, 0), turn[0]).multiply(App.Rotation(V(0, 1, 0), turn[1])))
    return place.toMatrix()

def _best_pose(mesh: Any, pegs: tuple[Any, ...] = ()) -> tuple[tuple[float, float, float], bool]:
    """Which face down needs least support, and whether it still needs support. Pegs should point up: lying, their
    underside comes out rough and they fit badly; pointing down, the part would stand on them."""
    best = None
    data = [(f.Normal, f.Area, f.Points) for f in mesh.Facets]
    for turn in ORIENTATIONS:
        rot = App.Rotation(V(1, 0, 0), turn[0]).multiply(App.Rotation(V(0, 1, 0), turn[1]))
        rows = [(rot.multVec(V(*n)), a, min(rot.multVec(V(*q)).z for q in pts), max(rot.multVec(V(*q)).z for q in pts))
                for n, a, pts in data]
        low = min(r[2] for r in rows)
        high = max(r[3] for r in rows)
        base = sum(a for n, a, z0, z1 in rows if n.z < -0.5 and z1 - low < 0.3)
        over = sum(a for n, a, z0, z1 in rows if n.z < -0.707 and z1 - low >= 0.3)
        total = sum(r[1] for r in rows) or 1.0
        # a usable foot first, then little support; height counts, since tall parts on a small foot fail
        peg = sum(0.0 if rot.multVec(a).z > 0.9 else 0.6 if rot.multVec(a).z < -0.5 else 0.25 for a in pegs)
        score = (base < MIN_BASE, over / total + 0.3 * (high - low) / mesh.BoundBox.DiagonalLength - 0.5 * base / total + peg)
        if best is None or score < best[0]:
            best = (score, turn, base < MIN_BASE or over > 0.02 * total)
    return best[1], best[2]

def _write_stl(mesh: Any, path: str) -> None:
    mesh.write(path)

def _write_assembled(parts: list[tuple[str, int, Any]], name: str, palette: list[str], bed: list[float], out_dir: str,
                     base: str) -> dict[str, Any]:
    """Parts as they sit in the model, as one coloured print in the middle of the bed: 3MF and STL."""
    boxes = [m.BoundBox for _, _, m in parts]
    low = [min(getattr(b, a + "Min") for b in boxes) for a in "XYZ"]
    size = [max(getattr(b, a + "Max") for b in boxes) - low[i] for i, a in enumerate("XYZ")]
    shift = ((bed[0] - size[0]) / 2 - low[0], (bed[1] - size[1]) / 2 - low[1], -low[2])
    threemf.write(os.path.join(out_dir, base + ".3mf"), [(name, [(label, slot, *_triangles(m)) for label, slot, m in parts],
                                                          shift)], palette)
    together = Mesh.Mesh()
    for _, _, m in parts:
        together.addMesh(m)
    together.translate(*shift)
    together.write(os.path.join(out_dir, base + ".stl"))
    return {"file": base + ".3mf", "stl": base + ".stl", "size_mm": [round(s, 1) for s in size],
            "fits": all(s <= b for s, b in zip(size, bed, strict=True)), "colors": len(palette), "palette": palette}

def _whole(items: list[Item], printer: dict[str, Any], name: str, out_dir: str, folder: str,
           lang: str = "de") -> dict[str, Any]:
    colors = {it.name: it.color for it in items}
    slots = plan.assign_slots(colors, int(printer["slots"]))
    parts = [(it.label, slots[it.name], _mesh(it.shape) if it.shape is not None else it.mesh.copy()) for it in items]
    word = "komplett" if lang == "de" else "complete"
    return _write_assembled(parts, name, plan.slot_colors(colors, slots), printer["bed"], out_dir,
                            os.path.join(folder, f"{_safe_name(name)}_{word}"))

def _lay_flat(mesh: Any, axis: str) -> None:
    """What turns prints with its axis upright: round on the bed, and no support."""
    if axis == "x":
        mesh.rotate(0, math.pi / 2, 0)
    elif axis == "y":
        mesh.rotate(math.pi / 2, 0, 0)

def _write_moving(pieces: list[Piece], slots: dict[str, int], palette: list[str], printer: dict[str, Any], out_dir: str,
                  folder: str, base: str, warn: list[str], lang: str) -> list[str]:
    """Each moving part as one coloured print, however many objects it has; plates of them."""
    rotors: dict[int, list[Piece]] = {}
    for p in pieces:
        rotors.setdefault(id(p.rotor), []).append(p)
    laid: dict[str, list[tuple[str, int, Any]]] = {}
    for group in rotors.values():
        meshes = [(p.labels[0], slots[p.items[0].name], _mesh(p.shape) if p.shape is not None else p.mesh.copy()) for p in group]
        for _, _, m in meshes:
            _lay_flat(m, group[0].rotor["axis"])
        boxes = [m.BoundBox for _, _, m in meshes]
        low = [min(getattr(b, a + "Min") for b in boxes) for a in "XYZ"]
        for _, _, m in meshes:
            m.translate(-low[0], -low[1], -low[2])
        laid[group[0].labels[0]] = meshes
    def size(meshes: list[tuple[str, int, Any]]) -> tuple[float, float, float]:
        return tuple(max(getattr(m.BoundBox, a + "Max") for _, _, m in meshes) for a in "XYZ")
    placed, too_big = plan.layout([(n, *size(ms)) for n, ms in laid.items()], printer["bed"])
    for n in too_big:
        warn.append(_say(lang, f"{n} ist größer als der Bauraum und muss kleiner skaliert werden.",
                         f"{n} is larger than the build volume: scale it down."))
    plates: dict[int, list[tuple]] = {}
    for n, spot in placed.items():
        meshes = laid[n]
        if spot["turned"]:
            for _, _, m in meshes:
                m.rotate(0, 0, math.pi / 2)
            width = min(m.BoundBox.XMin for _, _, m in meshes)
            depth = min(m.BoundBox.YMin for _, _, m in meshes)
            for _, _, m in meshes:
                m.translate(-width, -depth, 0)
        plates.setdefault(int(spot["plate"]), []).append(
            (n, [(label, slot, *_triangles(m)) for label, slot, m in meshes], (spot["x"], spot["y"], 0.0)))
    files = []
    for number, objects in sorted(plates.items()):
        rel = os.path.join(folder, f"{base}_{number}.3mf")
        threemf.write(os.path.join(out_dir, rel), objects, palette)
        files.append(rel)
    return files

def _together(items: list[Item], options: dict[str, Any], lim: dict[str, float], folder: str, joints_folder: str,
              out_dir: str, warn: list[str], lang: str) -> dict[str, Any] | None:
    """Least assembly with something that moves: one print of all that is fixed, and the moving parts to mount."""
    moving = list(options.get("moving") or [])
    if not moving:
        return None
    printer = options["printer"]
    name = options.get("name", "Modell")
    axle_names = {m["axle"] for m in moving if m.get("axle")}
    rotor_of = {n: m for m in moving for n in m["objects"]}
    parts = [it for it in items if it.name not in axle_names]
    colors = {it.name: it.color for it in parts}
    slots = plan.assign_slots(colors, int(printer["slots"]))
    palette = plan.slot_colors(colors, slots)
    pieces = []
    for it in parts:
        p = Piece([it])
        p.rotor = rotor_of.get(it.name)
        p.id = it.name
        pieces.append(p)
    entries, axles = _plan_moving(pieces, items, moving, lim, _centre(pieces), warn, lang)
    _apply_cuts(pieces, warn, lang)
    _drop_dust(pieces)
    fixed = [(p.labels[0], slots[p.items[0].name], _mesh(p.shape) if p.shape is not None else p.mesh.copy())
             for p in pieces if p.rotor is None]
    if not fixed:
        return None
    word = "bewegliche_Teile" if lang == "de" else "moving_parts"
    body = "Rest_als_ein_Druck" if lang == "de" else "Rest_as_one_print"
    out = _write_assembled(fixed, name, palette, printer["bed"], out_dir, os.path.join(folder, f"{_safe_name(name)}_{body}"))
    out["moving"] = entries
    out["moving_files"] = _write_moving([p for p in pieces if p.rotor is not None], slots, palette, printer, out_dir, folder,
                                        f"{_safe_name(name)}_{word}", warn, lang)
    out["axles"] = {"count": len(axles), "file": None}
    if axles:
        out["axles"]["file"] = _connector_plate([(a["length"], a["d"], a["kind"] == "headed") for a in axles], out_dir,
                                                joints_folder, "Achsen" if lang == "de" else "Axles", printer)
    return out

def _pin_mesh(length: float, d: float, headed: bool) -> Any:
    """A pin lying on a small flat, so that it sticks to the bed and its layers run along it."""
    shaft = Part.makeCylinder(d / 2, length, V(0, 0, d / 2 - 0.2), V(1, 0, 0))
    if headed:
        head_r = d / 2 + 1.2
        head = Part.makeCylinder(head_r, 1.6, V(-1.6, 0, d / 2 - 0.2), V(1, 0, 0))
        shaft = shaft.fuse(head)
    keep = Part.makeBox(length + 8, 4 * d + 8, 4 * d + 8, V(-4, -2 * d - 4, 0))
    out = shaft.common(keep)
    return _mesh(out, 0.02)

def _connector_plate(pins: list[tuple[float, float, bool]], out_dir: str, folder: str, base: str,
                     printer: dict[str, Any]) -> str:
    objects = []
    x, y, row = 5.0, 5.0, 0.0
    bed = printer["bed"]
    for n, (length, d, headed) in enumerate(pins):
        m = _pin_mesh(length, d, headed)
        box = m.BoundBox
        if x + box.XLength > bed[0] - 5:
            x, y, row = 5.0, y + row + 4, 0.0
        objects.append((f"{base}_{n + 1}", [(base, 1, *_triangles(m))], (x - box.XMin, y - box.YMin, 0.0)))
        x += box.XLength + 4
        row = max(row, box.YLength)
    rel = os.path.join(folder, f"{base}.3mf")
    threemf.write(os.path.join(out_dir, rel), objects, ["#808080"])
    stl = Mesh.Mesh()
    for _, parts, shift in objects:
        for _p, _f, verts, tris in parts:
            stl.addFacets([[tuple(a + b for a, b in zip(verts[i], shift, strict=True)) for i in t] for t in tris])
    stl.write(os.path.join(out_dir, folder, f"{base}.stl"))
    return rel
