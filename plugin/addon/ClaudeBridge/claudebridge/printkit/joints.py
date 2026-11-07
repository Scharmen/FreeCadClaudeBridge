# ruff: noqa: E501
"""Joints between pieces: dowel holes where both sides have room, the cuts applied, dust removed."""

from __future__ import annotations

from typing import Any

import Part

from .common import CUT_KEEPS, DUST, MAX_FACES, TOUCH, Piece, V, _bbox_near, _say
from .pieces import _touching


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
