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
import re
import time
from typing import Any

import FreeCAD as App
import Mesh
import MeshPart
import Part

from . import plan, threemf
from .surface import Surface

V = App.Vector
TOUCH = 0.05
MAX_FACES = 40
THIN_SHARE = 0.6  # mean thickness (2V/A) reads low on ribbed parts, so a part is too thin only well below the limit
DUST = 0.05       # mm³
MIN_BASE = 10.0   # mm² on the bed, or the part comes loose
CUT_KEEPS = 0.5    # a cut may not take more than this share of a part, or something went wrong


def _step(text: str) -> None:
    """What the job is doing, for the dialog and for finding out what takes long."""
    print(f"FCB_STEP {time.strftime('%H:%M:%S')} {text}", flush=True)


def _bbox_near(a: Any, b: Any, gap: float) -> bool:
    return (a.XMin - gap <= b.XMax and b.XMin - gap <= a.XMax and a.YMin - gap <= b.YMax and b.YMin - gap <= a.YMax
            and a.ZMin - gap <= b.ZMax and b.ZMin - gap <= a.ZMax)


def _safe_name(text: str) -> str:
    return re.sub(r"[^\w-]+", "_", text).strip("_") or "Teil"


class Geo:
    """Something with a shape or a mesh; what is slow to ask for is kept until the shape changes."""

    shape: Any = None
    mesh: Any = None

    def _cached(self, key: str, make: Any) -> Any:
        known = getattr(self, "_cache", {})
        if known.get("shape") is not self.shape:
            known = {"shape": self.shape}
            self._cache = known
        if key not in known:
            known[key] = make()
        return known[key]

    @property
    def volume(self) -> float:
        if self.shape is None:
            return float(abs(self.mesh.Volume))
        return self._cached("volume", lambda: float(self.shape.Volume))

    @property
    def box(self) -> Any:
        if self.shape is None:
            return self.mesh.BoundBox
        return self._cached("box", lambda: self.shape.BoundBox)

    @property
    def surface(self) -> Surface:
        def make() -> Surface:
            size = self.box.DiagonalLength
            pts, tris = self.shape.tessellate(max(0.02, size / 4000))
            return Surface([(p.x, p.y, p.z) for p in pts], tris)
        return self._cached("surface", make)


class Item(Geo):
    """One object of the model in its assembled position."""

    def __init__(self, name: str, label: str, color: str, shape: Any = None, mesh: Any = None) -> None:
        self.name, self.label, self.color, self.shape, self.mesh = name, label, color, shape, mesh


class Piece(Geo):
    """What gets printed as one part."""

    def __init__(self, items: list[Item]) -> None:
        self.items = sorted(items, key=lambda it: -it.volume)
        self.color = self.items[0].color
        self.shape = self.items[0].shape
        self.mesh = self.items[0].mesh
        self.id = ""
        self.rotor: dict[str, Any] | None = None
        self.cuts: list[Any] = []
        self.closed = False
        self.turn = (0.0, 0.0, 0.0)
        self.support = False

    @property
    def labels(self) -> list[str]:
        return [it.label for it in self.items]


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


def _say(lang: str, de: str, en: str) -> str:
    return de if lang == "de" else en


def _touching(a: Geo, b: Geo) -> bool:
    if not _bbox_near(a.box, b.box, TOUCH):
        return False
    if a.shape is None or b.shape is None:
        return True
    small, big = (a, b) if a.box.DiagonalLength <= b.box.DiagonalLength else (b, a)
    reach = TOUCH + big.box.DiagonalLength / 4000
    box = big.box
    if any(big.surface.near(p, reach) for p in small.surface.samples(400)
           if box.XMin - reach <= p[0] <= box.XMax + reach and box.YMin - reach <= p[1] <= box.YMax + reach
           and box.ZMin - reach <= p[2] <= box.ZMax + reach):
        return True
    box = small.box
    return any(small.surface.near(p, reach) for p in big.surface.samples(4000)
               if box.XMin <= p[0] <= box.XMax and box.YMin <= p[1] <= box.YMax and box.ZMin <= p[2] <= box.ZMax)


def _fuse(a: Any, b: Any) -> Any | None:
    """A fuse, or None when the kernel lost a part of it or the two did not really join into one body."""
    try:
        out = a.fuse(b)
    except Part.OCCError:
        return None
    if not out.isValid() or not out.Solids or len(out.Solids) >= len(a.Solids) + len(b.Solids):
        return None
    big, small = max(a.Volume, b.Volume), min(a.Volume, b.Volume)
    return None if out.Volume < 0.98 * big + 0.2 * small else out


def _fits_together(a: Any, b: Any, bed: list[float]) -> bool:
    """Whether two boxes taken as one fit the printer in some axis order."""
    box = App.BoundBox(a)
    box.add(b)
    return all(s <= e for s, e in zip(sorted((box.XLength, box.YLength, box.ZLength)), sorted(bed), strict=True))


def _make_pieces(items: list[Item], bed: list[float]) -> list[Piece]:
    """One print beats many: parts of one colour that touch become one piece, unless the bed or the kernel says no."""
    pieces: list[Piece] = []
    left = list(items)
    while left:
        group = [left.pop(0)]
        for it in group:
            for other in list(left):
                if other.color == it.color and _touching(it, other):
                    left.remove(other)
                    group.append(other)
        group.sort(key=lambda it: -it.volume)
        while group:
            current = Piece([group.pop(0)])
            grown = current.shape is not None
            while grown:
                grown = False
                for it in list(group):
                    if it.shape is None or not _fits_together(current.box, it.box, bed):
                        continue
                    if not any(_touching(member, it) for member in current.items):
                        continue
                    joined = _fuse(current.shape, it.shape)
                    if joined is not None:
                        current.shape = joined
                        current.items.append(it)
                        group.remove(it)
                        grown = True
            pieces.append(current)
    return pieces


def _thickness(p: Piece) -> float:
    if p.shape is None:
        return math.inf
    area = p._cached("area", lambda: float(p.shape.Area))
    return 2 * p.volume / area if area > 0 else math.inf


def _merge_thin(pieces: list[Piece], min_thick: float) -> tuple[list[Piece], list[str]]:
    """A piece too thin for a print of its own goes into its biggest neighbour; the guide says to paint it."""
    painted: list[str] = []
    for thin in sorted(pieces, key=_thickness):
        if thin.rotor or _thickness(thin) >= min_thick or thin not in pieces:
            continue
        near = [p for p in pieces if p is not thin and not p.rotor and p.shape is not None and _touching(thin, p)]
        for host in sorted(near, key=lambda p: -p.volume):
            joined = _fuse(host.shape, thin.shape)
            if joined is not None:
                host.shape = joined
                host.items += thin.items
                painted += thin.labels
                pieces.remove(thin)
                break
    return pieces, painted


def _centre(pieces: list[Piece]) -> Any:
    centre = V(0, 0, 0)
    total = 0.0
    for p in pieces:
        centre += p.box.Center * p.volume
        total += p.volume
    return centre * (1 / total)


def _join_again(pieces: list[Piece], bed: list[float]) -> list[Piece]:
    """A merged thin part can bridge two pieces of one colour that did not touch before."""
    grown = True
    while grown:
        grown = False
        for a in pieces:
            for b in pieces:
                if (a is b or a.color != b.color or a.rotor is not b.rotor or a.shape is None or b.shape is None
                        or a.volume < b.volume or not _fits_together(a.box, b.box, bed) or not _touching(a, b)):
                    continue
                joined = _fuse(a.shape, b.shape)
                if joined is not None:
                    a.shape = joined
                    a.items += b.items
                    pieces.remove(b)
                    grown = True
                    break
            if grown:
                break
    return pieces


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


def _face_points(face: Any) -> list[tuple[Any, Any]]:
    """A grid of points on a face, each with its outward normal."""
    u0, u1, v0, v1 = face.ParameterRange
    out = []
    for a in (0.15, 0.32, 0.5, 0.68, 0.85):
        for b in (0.15, 0.32, 0.5, 0.68, 0.85):
            u, w = u0 + (u1 - u0) * a, v0 + (v1 - v0) * b
            if face.isPartOfDomain(u, w):
                out.append((face.valueAt(u, w), face.normalAt(u, w)))
    return out


def _has_room(piece: Piece, point: Any, normal: Any, r: float, depth: float) -> bool:
    """Is there material along `normal`, deep enough and wide enough around the hole?"""
    side = normal.cross(V(1, 0, 0))
    if side.Length < 0.1:
        side = normal.cross(V(0, 1, 0))
    side.normalize()
    other = normal.cross(side)
    tests = [point + normal * (depth + 0.3)]
    for at in (0.3, depth):
        for s, t in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            tests.append(point + normal * at + side * (r * s) + other * (r * t))
    surface = piece.surface
    return all(surface.inside((q.x, q.y, q.z)) for q in tests)


def _dowels(small: Piece, big: Piece, lim: dict[str, float]) -> list[tuple[Any, Any, float]]:
    """Up to two places for dowels between two touching pieces: (point, axis into `big`, depth on each side)."""
    d = lim["dowel_d"]
    r = d / 2 + lim["wall"]
    faces = sorted((f for f in small.shape.Faces if f.Area >= (2 * r) ** 2), key=lambda f: -f.Area)[:MAX_FACES]
    found: list[tuple[Any, Any, float]] = []
    tested = 0
    for face in faces:
        if not _bbox_near(face.BoundBox, big.box, TOUCH):
            continue
        for point, normal in _face_points(face):
            if tested > 120 or len(found) >= 8:
                break
            if not big.box.isInside(point):
                continue
            tested += 1
            if not big.surface.near((point.x, point.y, point.z), TOUCH + big.box.DiagonalLength / 4000):
                continue
            for depth in (lim["dowel_depth"], 2.0, 1.5):
                if _has_room(small, point, normal * -1, r, depth) and _has_room(big, point, normal, r, depth):
                    found.append((point, normal, depth))
                    break
    if not found:
        return []
    first = max(found, key=lambda f: f[2])
    rest = [f for f in found if (f[0] - first[0]).Length >= 2.5 * d + 2 * lim["wall"] and f[2] >= 1.5]
    return [first, max(rest, key=lambda f: (f[0] - first[0]).Length)] if rest else [first]


def _make_joints(pieces: list[Piece], join: str, lim: dict[str, float]) -> tuple[list[dict[str, Any]], list[float]]:
    joints: list[dict[str, Any]] = []
    lengths: list[float] = []
    for i, a in enumerate(pieces):
        for b in pieces[i + 1:]:
            if a.rotor is not None or b.rotor is not None:
                if a.rotor is not b.rotor:
                    continue                                  # what turns is not fixed to its host
            if not _touching(a, b):
                continue
            small, big = (a, b) if a.volume <= b.volume else (b, a)
            joint = {"a": big.id, "b": small.id, "a_objects": big.labels, "b_objects": small.labels, "type": "glue",
                     "count": 0}
            if join == "pins" and a.rotor is None and small.shape is not None and big.shape is not None:
                spots = _dowels(small, big, lim)
                for point, normal, depth in spots:
                    hole = Part.makeCylinder((lim["dowel_d"] + lim["hole_gap"]) / 2, 2 * depth + 0.6,
                                             point - normal * (depth + 0.3), normal)
                    small.cuts.append(hole)
                    big.cuts.append(hole)
                    lengths.append(2 * depth)
                if spots:
                    joint.update(type="dowel", count=len(spots), depth=spots[0][2])
            joints.append(joint)
    return joints, lengths


def _drop_dust(pieces: list[Piece]) -> None:
    """Booleans can leave solids of no size; a slicer sees them as floating parts."""
    for p in pieces:
        if p.shape is None or len(p.shape.Solids) < 2:
            continue
        keep = [q for q in p.shape.Solids if q.Volume >= DUST]
        if keep and len(keep) < len(p.shape.Solids):
            p.shape = keep[0] if len(keep) == 1 else Part.makeCompound(keep)


def _apply_cuts(pieces: list[Piece], warn: list[str], lang: str) -> None:
    for p in pieces:
        if not p.cuts or p.shape is None:
            continue
        before = p.shape.Volume
        out = p.shape
        for tool in p.cuts:
            try:
                n = out.cut(tool)
            except Part.OCCError:
                n = None
            if n is None or not n.Solids or not n.isValid() or n.Volume < CUT_KEEPS * before:
                warn.append(_say(lang, f"{p.labels[0]}: ein Loch ließ sich nicht schneiden, bitte nachbohren.",
                                 f"{p.labels[0]}: one hole could not be cut, please drill it."))
                continue
            out = n
        p.shape = out


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


def _best_pose(mesh: Any) -> tuple[tuple[float, float, float], bool]:
    """Which face down needs least support, and whether it still needs support."""
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
        score = (base < MIN_BASE, over / total + 0.3 * (high - low) / mesh.BoundBox.DiagonalLength - 0.5 * base / total)
        if best is None or score < best[0]:
            best = (score, turn, base < MIN_BASE or over > 0.02 * total)
    return best[1], best[2]


def _pose_mesh(piece: Piece, fine: float = 0.03) -> Any:
    mesh = _mesh(piece.shape, fine) if piece.shape is not None else piece.mesh.copy()
    piece.closed = _closed(mesh)
    piece.turn, piece.support = _best_pose(mesh)
    mesh.transform(_matrix(piece.turn))
    box = mesh.BoundBox
    mesh.translate(-box.XMin, -box.YMin, -box.ZMin)
    return mesh


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
