"""Point queries on a triangle mesh in pure Python, for parts with thousands of faces.

The kernel's isInside and distToShape cost seconds per call on such a part, and the export asks thousands of times.
A grid over XY finds the few triangles that matter.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Vec = Sequence[float]
GRID = 64
JITTER = (1.3711e-5, 2.1173e-5)    # keeps a ray from running exactly along an edge


def _closest_on_triangle(p: Vec, a: Vec, b: Vec, c: Vec) -> tuple[float, float, float]:
    ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    ac = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    ap = (p[0] - a[0], p[1] - a[1], p[2] - a[2])
    d1 = ab[0] * ap[0] + ab[1] * ap[1] + ab[2] * ap[2]
    d2 = ac[0] * ap[0] + ac[1] * ap[1] + ac[2] * ap[2]
    if d1 <= 0 and d2 <= 0:
        return (a[0], a[1], a[2])
    bp = (p[0] - b[0], p[1] - b[1], p[2] - b[2])
    d3 = ab[0] * bp[0] + ab[1] * bp[1] + ab[2] * bp[2]
    d4 = ac[0] * bp[0] + ac[1] * bp[1] + ac[2] * bp[2]
    if d3 >= 0 and d4 <= d3:
        return (b[0], b[1], b[2])
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        t = d1 / (d1 - d3)
        return (a[0] + t * ab[0], a[1] + t * ab[1], a[2] + t * ab[2])
    cp = (p[0] - c[0], p[1] - c[1], p[2] - c[2])
    d5 = ab[0] * cp[0] + ab[1] * cp[1] + ab[2] * cp[2]
    d6 = ac[0] * cp[0] + ac[1] * cp[1] + ac[2] * cp[2]
    if d6 >= 0 and d5 <= d6:
        return (c[0], c[1], c[2])
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        t = d2 / (d2 - d6)
        return (a[0] + t * ac[0], a[1] + t * ac[1], a[2] + t * ac[2])
    va = d3 * d6 - d5 * d4
    if va <= 0 and d4 - d3 >= 0 and d5 - d6 >= 0:
        t = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return (b[0] + t * (c[0] - b[0]), b[1] + t * (c[1] - b[1]), b[2] + t * (c[2] - b[2]))
    den = 1.0 / (va + vb + vc)
    v, w = vb * den, vc * den
    return (a[0] + ab[0] * v + ac[0] * w, a[1] + ab[1] * v + ac[1] * w, a[2] + ab[2] * v + ac[2] * w)


class Surface:
    def __init__(self, verts: Sequence[Vec], tris: Sequence[Sequence[int]]) -> None:
        self.verts = [(float(v[0]), float(v[1]), float(v[2])) for v in verts]
        self.tris = [(int(t[0]), int(t[1]), int(t[2])) for t in tris]
        xs, ys = [v[0] for v in self.verts], [v[1] for v in self.verts]
        self.x0, self.y0 = min(xs), min(ys)
        span = max(max(xs) - self.x0, max(ys) - self.y0, 1e-9)
        self.cell = span / GRID
        self.grid: dict[tuple[int, int], list[int]] = {}
        for index, (a, b, c) in enumerate(self.tris):
            va, vb, vc = self.verts[a], self.verts[b], self.verts[c]
            i0, i1 = self._ix(min(va[0], vb[0], vc[0])), self._ix(max(va[0], vb[0], vc[0]))
            j0, j1 = self._iy(min(va[1], vb[1], vc[1])), self._iy(max(va[1], vb[1], vc[1]))
            for i in range(i0, i1 + 1):
                for j in range(j0, j1 + 1):
                    self.grid.setdefault((i, j), []).append(index)

    def _ix(self, x: float) -> int:
        return int(math.floor((x - self.x0) / self.cell))

    def _iy(self, y: float) -> int:
        return int(math.floor((y - self.y0) / self.cell))

    def near(self, p: Vec, tol: float) -> bool:
        """Is the point within `tol` of the surface?"""
        seen: set[int] = set()
        for i in range(self._ix(p[0] - tol), self._ix(p[0] + tol) + 1):
            for j in range(self._iy(p[1] - tol), self._iy(p[1] + tol) + 1):
                for index in self.grid.get((i, j), ()):
                    if index in seen:
                        continue
                    seen.add(index)
                    a, b, c = self.tris[index]
                    q = _closest_on_triangle(p, self.verts[a], self.verts[b], self.verts[c])
                    if (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2 + (q[2] - p[2]) ** 2 <= tol * tol:
                        return True
        return False

    def inside(self, p: Vec) -> bool:
        """Is the point inside the closed mesh? Counts the crossings of a ray going up."""
        x, y = p[0] + JITTER[0], p[1] + JITTER[1]
        crossings = 0
        for index in self.grid.get((self._ix(x), self._iy(y)), ()):
            a, b, c = self.tris[index]
            va, vb, vc = self.verts[a], self.verts[b], self.verts[c]
            den = (vb[1] - vc[1]) * (va[0] - vc[0]) + (vc[0] - vb[0]) * (va[1] - vc[1])
            if abs(den) < 1e-18:
                continue
            u = ((vb[1] - vc[1]) * (x - vc[0]) + (vc[0] - vb[0]) * (y - vc[1])) / den
            v = ((vc[1] - va[1]) * (x - vc[0]) + (va[0] - vc[0]) * (y - vc[1])) / den
            w = 1 - u - v
            if u < 0 or v < 0 or w < 0:
                continue
            if u * va[2] + v * vb[2] + w * vc[2] > p[2]:
                crossings += 1
        return crossings % 2 == 1

    def samples(self, count: int) -> list[tuple[float, float, float]]:
        """Points on the surface, spread over the vertices."""
        step = max(1, len(self.verts) // count)
        return self.verts[::step][:count]
