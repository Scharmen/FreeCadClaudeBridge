# ruff: noqa: E501
"""Which parts print as one piece: touching parts of one colour, thin parts into their neighbour."""

from __future__ import annotations

import math
from typing import Any

import FreeCAD as App
import Part

from .common import TOUCH, Geo, Item, Piece, V, _bbox_near


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
