# ruff: noqa: E501
"""Joints between prints, tried in this order: loose dowels between two big parts, the seat a piece's own shape gives
it, a keyed peg that is part of the smaller piece, a pocket the piece sinks into, and glue only when none fits."""

from __future__ import annotations

from typing import Any

import FreeCAD as App
import Part

from .common import CUT_KEEPS, DUST, MAX_FACES, TOUCH, Piece, V, _bbox_near, _say

PEG_SIZES = (1.5, 1.0, 0.8)   # times the dowel diameter, biggest first
BIG_PART = 12.0               # mm: two parts at least this thick take loose dowels and both stay flat on the bed
SHOULDER = 0.4                # mm of flat face around a peg
SEAT_SHARE = 0.25             # of a part's surface in contact, in two directions: its shape holds it
SEAT_DEPTH = 0.6              # mm it has to sit in the host
SEAT_FACES = 60               # a seat gets its gap only on parts simple enough to grow safely


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

def _frame(normal: Any) -> tuple[Any, Any]:
    side = normal.cross(V(1, 0, 0))
    if side.Length < 0.1:
        side = normal.cross(V(0, 1, 0))
    side.normalize()
    return side, normal.cross(side)

def _on_face(face: Any, point: Any, normal: Any, r: float) -> bool:
    """Does a circle of radius r around the point lie on the face?"""
    side, other = _frame(normal)
    for s, t in ((1, 0), (-1, 0), (0, 1), (0, -1), (0.7, 0.7), (-0.7, 0.7), (0.7, -0.7), (-0.7, -0.7)):
        u, w = face.Surface.parameter(point + side * (r * s) + other * (r * t))
        if not face.isPartOfDomain(u, w):
            return False
    return True

def _flat_contacts(small: Piece, big: Piece) -> list[tuple[Any, list[tuple[Any, Any]], list[tuple[Any, Any]]]]:
    """Flat faces of `small` that lie on `big`: (face, its sample points, those of them that touch)."""
    reach = TOUCH + big.box.DiagonalLength / 4000
    faces = sorted((f for f in small.shape.Faces if isinstance(f.Surface, Part.Plane)), key=lambda f: -f.Area)[:MAX_FACES]
    out = []
    for face in faces:
        if not _bbox_near(face.BoundBox, big.box, TOUCH):
            continue
        points = _face_points(face)
        touching = [(q, n) for q, n in points if big.surface.near((q.x, q.y, q.z), reach)]
        if touching:
            out.append((face, points, touching))
    return out

def _d_shape(d: float, length: float, flat_at: float, point: Any, normal: Any) -> Any:
    """A round bar with one flat side, so that it goes in one way only; from `point` along `normal`."""
    bar = Part.makeCylinder(d / 2, length)
    if flat_at < d / 2:
        bar = bar.cut(Part.makeBox(d, 2 * d, length + 2, V(flat_at, -d, -1)))
    bar.Placement = App.Placement(point, App.Rotation(V(0, 0, 1), normal))
    return bar

def _peg(small: Piece, big: Piece, lim: dict[str, float]) -> dict[str, Any] | None:
    """A keyed peg on `small` and its socket in `big`; a second, round one where the face has room for it."""
    contacts = _flat_contacts(small, big)
    gap = lim["hole_gap"]
    for scale in PEG_SIZES:
        d = scale * lim["dowel_d"]
        r = d / 2
        for face, _points, touching in contacts:
            spots = []
            for point, normal in touching:
                if not _on_face(face, point, normal, r + SHOULDER):
                    continue
                for depth in (lim["dowel_depth"], 2.0, 1.5):
                    if _has_room(big, point, normal, r + gap / 2 + lim["wall"], depth):
                        spots.append((point, normal, depth))
                        break
            if not spots:
                continue
            first = max(spots, key=lambda f: f[2])
            far = [f for f in spots if (f[0] - first[0]).Length >= 3 * d]
            chosen = [first, max(far, key=lambda f: (f[0] - first[0]).Length)] if far else [first]
            adds, cuts = [], []
            for n, (point, normal, depth) in enumerate(chosen):
                flat = 0.3 * d if n == 0 else d                       # the first peg is keyed, the second round
                adds.append(_d_shape(d, depth, flat, point - normal * 0.3, normal))             # 0.3 short of the bottom
                cuts.append(_d_shape(d + gap, depth + 0.3, flat + gap / 2, point - normal * 0.3, normal))
            return {"type": "peg", "count": len(chosen), "d": round(d, 2), "depth": chosen[0][2], "adds": adds, "cuts": cuts,
                    "axes": [chosen[0][1]]}
    return None

def _pocket(small: Piece, big: Piece, lim: dict[str, float]) -> dict[str, Any] | None:
    """A flat face too small for a peg: the piece gets a foot of its own outline and the host a pocket for it."""
    for face, points, touching in _flat_contacts(small, big):
        if len(touching) < len(points) or face.Area < 3.0:
            continue                                                   # the whole face has to sit on the host
        normal = touching[0][1]
        for depth in (1.0, 0.6):
            deep = [q + normal * (depth + 0.3) for q, _ in points]
            if not all(big.surface.inside((q.x, q.y, q.z)) for q in deep):
                continue
            try:
                foot = face.extrude(normal * depth)
                wide = face.makeOffset2D(lim["hole_gap"] / 2)
                wide.translate(normal * -0.05)
                hole = wide.extrude(normal * (depth + lim["hole_gap"] / 2 + 0.05))
            except (Part.OCCError, RuntimeError, ValueError):
                continue
            if foot.isValid() and hole.isValid() and hole.Volume > foot.Volume:
                return {"type": "pocket", "count": 1, "depth": depth, "adds": [foot], "cuts": [hole], "axes": []}
    return None

def _seat(small: Piece, big: Piece, lim: dict[str, float]) -> dict[str, Any] | None:
    """A piece that sits in the host, held sideways over some depth, is located by its shape; the host gets the gap."""
    reach = TOUCH + big.box.DiagonalLength / 4000
    total = held = 0.0
    touched: list[tuple[Any, Any, float]] = []
    for face in sorted(small.shape.Faces, key=lambda f: -f.Area)[:MAX_FACES]:
        points = _face_points(face)
        if not points:
            continue
        share = face.Area / len(points)
        total += face.Area
        if not _bbox_near(face.BoundBox, big.box, TOUCH):
            continue
        for q, n in points:
            if big.surface.near((q.x, q.y, q.z), reach):
                held += share
                touched.append((q, n, share))
    if total <= 0 or held < SEAT_SHARE * total:
        return None
    some = touched[::max(1, len(touched) // 60)]
    free = [(n, share) for _q, n, share in touched if not any(n.dot(m) < -0.5 for _p, m, _s in some)]
    if free:                                                           # a pocket: the faces nothing presses against are its bottom
        main = V(0, 0, 0)
        for n, share in free:
            main += n * share
        if main.Length < 1e-9:
            return None
        main.normalize()
        along = [q.dot(main) for q, n, _share in touched if abs(n.dot(main)) < 0.5]     # contact on the sides
        if not along or max(along) - min(along) < SEAT_DEPTH:
            return None                                                # it lies on the host, it does not sit in it
    out: dict[str, Any] = {"type": "seat", "count": 1, "adds": [], "cuts": [], "tight": True}
    if len(small.shape.Faces) <= SEAT_FACES:
        box = small.box
        mid = box.Center
        grow = App.Matrix()
        for k, length in enumerate((box.XLength, box.YLength, box.ZLength)):
            f = 1 + lim["hole_gap"] / max(length, 0.1)
            setattr(grow, f"A{k + 1}{k + 1}", f)
            setattr(grow, f"A{k + 1}4", mid[k] * (1 - f))
        try:
            wide = small.shape.transformGeometry(grow)
        except (Part.OCCError, RuntimeError, ValueError):
            wide = None
        if wide is not None and wide.isValid() and wide.Volume > small.volume:
            out.update(cuts=[wide], tight=False)
    return out

def _why(lang: str, key: str) -> str:
    return {"mesh": _say(lang, "Netzteil: lässt sich nicht schneiden", "a mesh part: it cannot be cut"),
            "none": _say(lang, "keine ebene Fläche mit Platz für Zapfen oder Tasche", "no flat face with room for a peg or a pocket"),
            "glue": _say(lang, "Kleben gewählt", "glue was chosen"),
            "failed": _say(lang, "der Zapfen ließ sich nicht schneiden", "the peg could not be cut"),
            "apart": _say(lang, "berührt kein anderes Teil", "touches no other part")}[key]

def _join(uid: str, host: str, units: dict[str, list[Piece]], touching: set[frozenset[str]], join: str,
          lim: dict[str, float], lang: str) -> tuple[dict[str, Any], list[tuple[Piece, Any]], dict[str, Any] | None]:
    """The joint of one print to one host: (joint, dowel holes as (piece, hole), pegs and pockets still to cut)."""
    pairs = [(b, a) for a in units[host] for b in units[uid] if frozenset((a.id, b.id)) in touching]
    pairs.sort(key=lambda pair: -min(pair[0].volume, pair[1].volume))
    joint: dict[str, Any] = {"a": host, "b": uid, "a_objects": units[host][0].labels, "b_objects": units[uid][0].labels,
                             "type": "glue", "count": 0, "why": _why(lang, "glue" if join != "pins" else "none")}
    for small, big in pairs if join == "pins" else []:
        if small.shape is None or big.shape is None:
            joint["why"] = _why(lang, "mesh")
            continue
        box = small.box
        if min(box.XLength, box.YLength, box.ZLength) >= BIG_PART:
            lo, hi = (small, big) if small.volume <= big.volume else (big, small)
            spots = _dowels(lo, hi, lim)
            if spots:
                holes = []
                for point, normal, depth in spots:
                    hole = Part.makeCylinder((lim["dowel_d"] + lim["hole_gap"]) / 2, 2 * depth + 0.6,
                                             point - normal * (depth + 0.3), normal)
                    holes += [(small, hole), (big, hole)]
                joint.update(type="dowel", count=len(spots), depth=spots[0][2], lengths=[2 * s[2] for s in spots])
                joint.pop("why")
                return joint, holes, None
        found = _seat(small, big, lim) or _peg(small, big, lim) or _pocket(small, big, lim)   # what already sits needs no peg
        if found:
            adds, cuts, axes = found.pop("adds"), found.pop("cuts"), found.pop("axes", [])
            joint.update(found)
            joint.pop("why")
            return joint, [], {"joint": joint, "small": small, "big": big, "adds": adds, "cuts": cuts, "axes": axes}
    return joint, [], None

def _loose(uid: str, units: dict[str, list[Piece]], lang: str) -> dict[str, Any]:
    return {"a": uid, "b": uid, "a_objects": [], "b_objects": units[uid][0].labels, "type": "loose", "count": 0,
            "why": _why(lang, "apart")}

def _take(found: tuple[dict[str, Any], list[tuple[Piece, Any]], dict[str, Any] | None], lengths: list[float],
          pending: list[dict[str, Any]]) -> dict[str, Any]:
    """A joint that was chosen: its dowel holes go onto the pieces, its pegs and pockets wait to be cut."""
    joint, holes, job = found
    for piece, hole in holes:
        piece.cuts.append(hole)
    lengths += joint.pop("lengths", [])
    if job and (job["adds"] or job["cuts"]):
        pending.append(job)
    return joint

def _grown(shape: Any, add: Any) -> Any | None:
    try:
        out = shape.fuse(add)
    except Part.OCCError:
        return None
    if not out.isValid() or not out.Solids or len(out.Solids) > len(shape.Solids):
        return None
    if not shape.Volume - 1e-6 <= out.Volume <= shape.Volume + add.Volume + 1e-6:
        return None
    try:
        clean = out.removeSplitter()
        return clean if clean.isValid() and abs(clean.Volume - out.Volume) < 1e-3 else out
    except Part.OCCError:
        return out

def _taken(shape: Any, tool: Any) -> Any | None:
    try:
        out = shape.cut(tool)
    except Part.OCCError:
        return None
    if not out.isValid() or not out.Solids or not CUT_KEEPS * shape.Volume <= out.Volume <= shape.Volume + 1e-6:
        return None
    return out

def _apply_joints(pending: list[dict[str, Any]], lang: str) -> None:
    """Sockets and pockets out of the hosts, then pegs and feet onto the small pieces.

    A host with many sockets is cut once with all of them, which is far quicker on a big part; only when that
    fails are they cut one by one, and a joint whose socket cannot be cut falls back to glue."""
    def fail(job: dict[str, Any]) -> None:
        job["joint"].update(type="glue", count=0, why=_why(lang, "failed"))
        job["joint"].pop("tight", None)
        job["failed"] = True

    by_host: dict[int, list[dict[str, Any]]] = {}
    for job in pending:
        if job["cuts"]:
            by_host.setdefault(id(job["big"]), []).append(job)
    for group in by_host.values():
        big = group[0]["big"]
        tools = [t for job in group for t in job["cuts"]]
        host = _taken(big.shape, Part.makeCompound(tools)) if len(tools) > 1 else None
        if host is not None:
            big.shape = host
            continue
        for job in group:
            host = big.shape
            for tool in job["cuts"]:
                host = _taken(host, tool) if host is not None else None
            if host is None:
                fail(job)
            else:
                big.shape = host
    for job in pending:
        if job.get("failed") or not job["adds"]:
            continue
        piece = job["small"].shape
        for add in job["adds"]:
            piece = _grown(piece, add) if piece is not None else None
        if piece is None:
            fail(job)
        else:
            job["small"].shape = piece
            job["small"].pegs += job.get("axes", [])

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
