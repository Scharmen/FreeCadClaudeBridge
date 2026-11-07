# ruff: noqa: E501
"""What every part of the print export shares: limits, small helpers and the Item and Piece types."""

from __future__ import annotations

import re
import time
from typing import Any

import FreeCAD as App

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

def _say(lang: str, de: str, en: str) -> str:
    return de if lang == "de" else en

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
