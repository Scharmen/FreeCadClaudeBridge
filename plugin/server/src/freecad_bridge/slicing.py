"""The bridge's own slicer: what an FDM printer would do with the model, layer by layer.

No slicer program is needed. The mesh is cut into layers, each layer becomes a grid of cells (half a nozzle
wide), and the print is simulated from the bed up: what does each layer rest on? Parts that start in mid
air, arms that hang free, bridges, slopes steeper than the printer manages and walls thinner than the nozzle
are found that way. Supports are grown down from what hangs, and G-code is written (walls, solid and sparse
infill, supports) with an estimate of time and filament. numpy only.

The G-code is generic (Marlin/Klipper style, relative extrusion). It is meant for a look in a G-code viewer
and for the numbers; it has not been printed on a real machine.
"""

from __future__ import annotations

import math
import re
import struct
from collections import OrderedDict
from typing import Any, TextIO

import numpy as np

PRINTERS: dict[str, tuple[list[int], str]] = {       # bed x, y, z in mm; the same presets as the print dialog
    "Anycubic Kobra S1": ([250, 250, 250], "fdm"),
    "Bambu Lab A1 mini": ([180, 180, 180], "fdm"),
    "Bambu Lab P1S / X1C": ([256, 256, 256], "fdm"),
    "Prusa MK4": ([250, 210, 220], "fdm"),
    "Creality Ender 3": ([220, 220, 250], "fdm"),
    "Elegoo Mars (resin)": ([143, 89, 175], "resin"),
}
MATERIALS = {       # nozzle °C, bed °C, density g/cm³
    "PLA": (210, 60, 1.24), "PETG": (240, 75, 1.27), "ABS": (250, 100, 1.04), "ASA": (255, 100, 1.07),
    "TPU": (225, 45, 1.21),
}
SPEEDS = {"first": 30.0, "Outer wall": 60.0, "Inner wall": 100.0, "Solid infill": 120.0, "Sparse infill": 150.0,
          "Support": 100.0, "travel": 250.0}       # mm/s, moderate on purpose
ACCEL = 4000.0             # mm/s², for the time estimate
FILAMENT_MM = 1.75
INFILL = 0.15              # share of sparse infill
SHELL_MM = 0.8             # solid top and bottom
SUPPORT_PITCH_MM = 2.5
RETRACT_MM, RETRACT_OVER_MM = 0.8, 2.0
CANTILEVER_MM = 2.0        # from this depth an overhang is named on its own
CANTILEVER_FAIL_MM = 5.0   # from this depth it does not print without supports
MAX_CELLS = 600_000        # grid cells per layer
MAX_BITS = 1_200_000_000   # cells of all layers together (kept packed, 8 per byte)
MAX_FINDINGS = 12


def r2(x: float) -> float:
    return round(float(x), 2)


def printer(name: str) -> tuple[str, list[int]]:
    """The preset whose name contains `name` (letters and digits only), or a ValueError naming them all."""
    want = "".join(ch for ch in name.lower() if ch.isalnum())
    for key, (bed, process) in PRINTERS.items():
        if want and want in "".join(ch for ch in key.lower() if ch.isalnum()):
            if process != "fdm":
                raise ValueError(f"{key} is a resin printer; slice_check simulates filament printers. "
                                 "check_print(process='resin') covers resin.")
            return key, list(bed)
    raise ValueError(f"unknown printer {name!r}; known: " + ", ".join(k for k, v in PRINTERS.items() if v[1] == "fdm"))


def read_stl(path: str) -> np.ndarray:
    """The triangles of a binary or text STL file as an array (n, 3, 3)."""
    with open(path, "rb") as f:
        raw = f.read()
    if len(raw) >= 84:
        n = struct.unpack_from("<I", raw, 80)[0]
        if len(raw) == 84 + 50 * n:
            rec = np.frombuffer(raw, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]), offset=84)
            return rec["v"].astype(float)
    nums = re.findall(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", raw)
    return np.array(nums, dtype=float).reshape(-1, 3, 3)


def resolution(size: Any, nozzle: float, layer: float) -> float:
    """Cell size of the grid: half a nozzle, coarser for models too large for that."""
    sx, sy, sz = (float(s) for s in size)
    layers = max(1.0, sz / layer)
    cells = min(MAX_CELLS, MAX_BITS / layers)
    return round(max(nozzle / 2, math.sqrt(max(sx * sy, 1e-9) / cells)), 2)


# ---------- layers

class Layers:
    """The model as one grid of cells per layer; cell (row, col) is centred at x0 + (col + .5) cell."""

    def __init__(self, tris: np.ndarray, layer: float, cell: float) -> None:
        lo, hi = tris.min((0, 1)), tris.max((0, 1))
        self.layer, self.cell = layer, cell
        self.lo, self.hi = lo, hi
        # the grid sits a hair off round numbers, so cell centres do not fall on the model's own corners
        self.x0, self.y0, self.z0 = lo[0] - 2.0137 * cell, lo[1] - 2.0137 * cell, lo[2]
        self.cols = int(math.ceil((hi[0] - lo[0]) / cell)) + 4
        self.rows = int(math.ceil((hi[1] - lo[1]) / cell)) + 4
        self.count = max(1, int(math.ceil((hi[2] - lo[2]) / layer - 1e-6)))
        self.leaks = 0
        self._packed: list[np.ndarray] = []
        self._cache: OrderedDict[int, np.ndarray] = OrderedDict()
        nx = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])[:, 0]
        zmin, zmax = tris[:, :, 2].min(1), tris[:, :, 2].max(1)
        for k in range(self.count):
            z = self.z0 + (k + 0.5) * layer
            sel = (zmin < z) & (zmax > z) & (nx != 0)
            self._packed.append(np.packbits(self._fill(tris[sel], nx[sel], z)))

    def _fill(self, t: np.ndarray, nx: np.ndarray, z: float) -> np.ndarray:
        """Cut the triangles at z and fill between the cuts, row by row (non-zero winding)."""
        acc = np.zeros(self.rows * (self.cols + 1))
        if len(t):
            d = t[:, :, 2] - z
            d[d == 0] = 1e-9
            above = d > 0
            alone = above == (above.sum(1) == 1)[:, None]        # the corner that is alone on its side
            i = np.arange(len(t))
            a = alone.argmax(1)
            b, c = (a + 1) % 3, (a + 2) % 3
            fb = (d[i, a] / (d[i, a] - d[i, b]))[:, None]
            fc = (d[i, a] / (d[i, a] - d[i, c]))[:, None]
            p = t[i, a, :2] + fb * (t[i, b, :2] - t[i, a, :2])
            q = t[i, a, :2] + fc * (t[i, c, :2] - t[i, a, :2])
            p, q = np.round(p, 7), np.round(q, 7)        # two triangles of one face cut at the same point
            gp, gq = (p[:, 1] - self.y0) / self.cell - 0.5, (q[:, 1] - self.y0) / self.cell - 0.5
            j0 = np.ceil(np.minimum(gp, gq)).astype(np.int64)
            n = np.ceil(np.maximum(gp, gq)).astype(np.int64) - j0
            keep = n > 0
            if keep.any():
                j0, n, p, q = j0[keep], n[keep], p[keep], q[keep]
                sign = np.where(nx[keep] < 0, 1.0, -1.0)
                seg = np.repeat(np.arange(len(n)), n)
                j = j0[seg] + np.arange(len(seg)) - np.repeat(np.cumsum(n) - n, n)
                yc = self.y0 + (j + 0.5) * self.cell
                x = p[seg, 0] + (yc - p[seg, 1]) / (q[seg, 1] - p[seg, 1]) * (q[seg, 0] - p[seg, 0])
                col = np.clip(np.ceil((x - self.x0) / self.cell - 0.5), 0, self.cols).astype(np.int64)
                j = np.clip(j, 0, self.rows - 1)
                acc = np.bincount(j * (self.cols + 1) + col, weights=sign[seg], minlength=len(acc))
        wind = np.rint(np.cumsum(acc.reshape(self.rows, self.cols + 1), axis=1))
        self.leaks += int(np.count_nonzero(wind[:, -1]))
        return wind[:, :-1] != 0

    def mask(self, k: int) -> np.ndarray:
        if k < 0 or k >= self.count:
            return np.zeros((self.rows, self.cols), bool)
        hit = self._cache.get(k)
        if hit is None:
            hit = np.unpackbits(self._packed[k], count=self.rows * self.cols).reshape(self.rows, self.cols).astype(bool)
            self._cache[k] = hit
            if len(self._cache) > 24:
                self._cache.popitem(last=False)
        else:
            self._cache.move_to_end(k)
        return hit

    def xy(self, row: float, col: float) -> list[float]:
        return [r2(self.x0 + (col + 0.5) * self.cell), r2(self.y0 + (row + 0.5) * self.cell)]


def grow(m: np.ndarray, n: int = 1, square: bool = True) -> np.ndarray:
    """Widen a mask by n cells (a square per step, or a cross)."""
    for _ in range(n):
        a = m.copy()
        a[1:] |= m[:-1]
        a[:-1] |= m[1:]
        if square:
            b = a.copy()
            b[:, 1:] |= a[:, :-1]
            b[:, :-1] |= a[:, 1:]
        else:
            b = a
            b[:, 1:] |= m[:, :-1]
            b[:, :-1] |= m[:, 1:]
        m = b
    return m


def shrink(m: np.ndarray, n: int = 1) -> np.ndarray:
    """Narrow a mask by n cells, about round (cross and square steps take turns)."""
    out = ~m
    for i in range(n):
        out = grow(out, 1, square=bool(i % 2))
    return ~out


def _runs(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Runs of set cells per row: row, first column, column after the last."""
    edge = np.diff(np.pad(mask, ((0, 0), (1, 1))).astype(np.int8), axis=1)
    rows, start = np.nonzero(edge == 1)
    return rows, start, np.nonzero(edge == -1)[1]


def components(mask: np.ndarray) -> list[tuple[int, int, np.ndarray]]:
    """Connected regions (touching corners count): row and column of the cut-out, and the cut-out."""
    rows, start, end = _runs(mask)
    n = len(rows)
    if not n:
        return []
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first = np.searchsorted(rows, np.arange(mask.shape[0] + 1)).tolist()
    s, e = start.tolist(), end.tolist()
    for r in range(1, mask.shape[0]):
        a, a_end, b, b_end = first[r - 1], first[r], first[r], first[r + 1]
        while a < a_end and b < b_end:
            if s[a] <= e[b] and s[b] <= e[a]:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb
            if e[a] < e[b]:
                a += 1
            else:
                b += 1
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = []
    rl = rows.tolist()
    for members in groups.values():
        r0, r1 = min(rl[i] for i in members), max(rl[i] for i in members) + 1
        c0, c1 = min(s[i] for i in members), max(e[i] for i in members)
        sub = np.zeros((r1 - r0, c1 - c0), bool)
        for i in members:
            sub[rl[i] - r0, s[i] - c0:e[i] - c0] = True
        out.append((r0, c0, sub))
    return out


# ---------- the print, layer by layer

def simulate(layers: Layers, nozzle: float, overhang_deg: float, bridge_mm: float) -> dict[str, Any]:
    """What every layer rests on. Returns the findings and, per layer, the cells that need a support below."""
    cell, h = layers.cell, layers.layer
    reach = max(1, int(round(h * math.tan(math.radians(overhang_deg)) / cell)))
    small = max(4, int(math.ceil(2 * nozzle * nozzle / (cell * cell))))
    findings: list[dict[str, Any]] = []
    seeds: list[np.ndarray | None] = [None] * layers.count
    steep: dict[str, Any] = {"area": 0.0, "z0": None, "z1": None}
    bridges = {"count": 0, "longest_mm": 0.0}
    below = layers.mask(0)
    areas = [float(below.sum()) * cell * cell]
    for k in range(1, layers.count):
        m = layers.mask(k)
        areas.append(float(m.sum()) * cell * cell)
        hang = m & ~grow(below, reach)
        below = m
        if not hang.any():
            continue
        z = r2(k * h)
        seed = np.zeros_like(m)
        for r0, c0, sub in components(hang):
            area = int(sub.sum())
            ra, rb = max(0, r0 - 1), min(m.shape[0], r0 + sub.shape[0] + 1)
            ca, cb = max(0, c0 - 1), min(m.shape[1], c0 + sub.shape[1] + 1)
            part = np.zeros((rb - ra, cb - ca), bool)
            part[r0 - ra:r0 - ra + sub.shape[0], c0 - ca:c0 - ca + sub.shape[1]] = sub
            anchors = grow(part) & m[ra:rb, ca:cb] & ~part
            rr, cc = np.nonzero(part)
            at = layers.xy(ra + rr.mean(), ca + cc.mean())
            if not anchors.any():
                if area >= small:
                    findings.append({"kind": "floating", "z": z, "area_mm2": r2(area * cell * cell), "at": at})
                    seed[ra:rb, ca:cb] |= part
                continue
            front, rest, depth, last = anchors, part.copy(), 0, part
            while rest.any():
                front = grow(front, 1, square=bool(depth % 2))
                hit = rest & front
                if not hit.any():
                    break
                last, depth = hit, depth + 1
                rest &= ~front
            mm = depth * cell
            if mm < nozzle:
                continue
            if mm < CANTILEVER_MM:
                steep["area"] += area * cell * cell
                steep["z0"] = z if steep["z0"] is None else steep["z0"]
                steep["z1"] = z
                seed[ra:rb, ca:cb] |= part
                continue
            fr, fc = (int(v[0]) for v in np.nonzero(last))
            ar, ac = np.nonzero(anchors)
            vec = np.stack([ar - fr, ac - fc], 1).astype(float)
            dist = np.hypot(vec[:, 0], vec[:, 1])
            near = dist <= 1.5 * depth + 2
            pull = np.linalg.norm((vec[near] / np.maximum(dist[near], 1e-9)[:, None]).mean(0))
            if pull < 0.6:                               # held from opposite sides
                span = 2 * mm
                bridges["count"] += 1
                bridges["longest_mm"] = max(bridges["longest_mm"], r2(span))
                if span <= bridge_mm:
                    continue
                findings.append({"kind": "bridge", "z": z, "mm": r2(span), "area_mm2": r2(area * cell * cell),
                                 "at": at})
            else:
                findings.append({"kind": "cantilever", "z": z, "mm": r2(mm), "area_mm2": r2(area * cell * cell),
                                 "at": at})
            seed[ra:rb, ca:cb] |= part
        if seed.any():
            seeds[k] = np.packbits(seed)
    return {"findings": findings, "seeds": seeds, "steep": steep, "bridges": bridges, "areas": areas}


def supports(layers: Layers, seeds: list[np.ndarray | None]) -> tuple[list[np.ndarray | None], bool]:
    """Columns under everything that hangs, down to the bed or the model. Per layer the support cells
    (packed), and whether a column stands on the model."""
    out: list[np.ndarray | None] = [None] * layers.count
    need = np.zeros((layers.rows, layers.cols), bool)
    on_model = False
    size = layers.rows * layers.cols
    for k in range(layers.count - 1, 0, -1):
        packed = seeds[k]
        if packed is not None:
            need = need | np.unpackbits(packed, count=size).reshape(need.shape).astype(bool)
        if not need.any():
            continue
        m = layers.mask(k - 1)
        on_model = on_model or bool((need & m).any())
        need = need & ~m
        if need.any():
            out[k - 1] = np.packbits(need)
    return out, on_model


# ---------- tool paths and G-code

def loops(mask: np.ndarray) -> list[np.ndarray]:
    """The outlines of a mask as closed paths in grid units (x, y), the solid on the left."""
    mask = np.pad(mask, 1)                # a region may reach the rim of the grid
    up = mask[1:] & ~mask[:-1]            # solid above the edge: go +x
    down = mask[:-1] & ~mask[1:]          # solid below: go -x
    left = mask[:, :-1] & ~mask[:, 1:]    # solid left of the edge: go +y
    right = mask[:, 1:] & ~mask[:, :-1]   # solid right: go -y
    w = mask.shape[1] + 1
    starts, ends = [], []
    r, c = np.nonzero(up)
    starts.append((r + 1) * w + c)
    ends.append((r + 1) * w + c + 1)
    r, c = np.nonzero(down)
    starts.append((r + 1) * w + c + 1)
    ends.append((r + 1) * w + c)
    r, c = np.nonzero(left)
    starts.append(r * w + c + 1)
    ends.append((r + 1) * w + c + 1)
    r, c = np.nonzero(right)
    starts.append((r + 1) * w + c + 1)
    ends.append(r * w + c + 1)
    nxt: dict[int, list[int]] = {}
    for a, b in zip(np.concatenate(starts).tolist(), np.concatenate(ends).tolist(), strict=True):
        nxt.setdefault(a, []).append(b)
    out = []
    while nxt:
        first = next(iter(nxt))
        path, at = [first], first
        while True:
            ways = nxt[at]
            to = ways.pop()
            if not ways:
                del nxt[at]
            if to == first:
                break
            path.append(to)
            at = to
        v = np.array(path)
        pts = np.stack([v % w, v // w], 1).astype(float) - 1.0
        mid = (pts + np.roll(pts, -1, 0)) / 2                 # through the edge centres: stairs become slopes
        a, b = mid - np.roll(mid, 1, 0), np.roll(mid, -1, 0) - mid
        mid = mid[np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]) > 1e-9]
        if len(mid) >= 3:
            out.append(mid)
    return out


def _move_seconds(length: np.ndarray, speed: float) -> np.ndarray:
    ramp = speed * speed / ACCEL
    return np.where(length >= ramp, length / speed + speed / ACCEL, 2 * np.sqrt(length / ACCEL))


class Writer:
    """Writes moves and keeps the sums: path length, filament and time per kind of line."""

    def __init__(self, out: TextIO | None, nozzle: float, layer: float) -> None:
        self.out = out
        self.e_per_mm = nozzle * layer / (math.pi * (FILAMENT_MM / 2) ** 2)
        self.mm: dict[str, float] = {}
        self.e: dict[str, float] = {}
        self.seconds: dict[str, float] = {}
        self.pos: np.ndarray | None = None
        self.kind = ""
        self.first = True

    def write(self, text: str) -> None:
        if self.out is not None:
            self.out.write(text)

    def _add(self, kind: str, length: float, seconds: float, travel: float) -> None:
        self.mm[kind] = self.mm.get(kind, 0.0) + length
        self.e[kind] = self.e.get(kind, 0.0) + length * self.e_per_mm
        self.seconds[kind] = self.seconds.get(kind, 0.0) + seconds + travel

    def paths(self, kind: str, pts: list[np.ndarray]) -> None:
        """Print each path (an array of points) after a travel to its start."""
        if not pts:
            return
        speed = SPEEDS["first"] if self.first else SPEEDS[kind]
        if kind != self.kind:
            self.write(f";TYPE:{kind}\n")
            self.kind = kind
        lines: list[str] = []
        for path in pts:
            start = path[0]
            hop = 0.0 if self.pos is None else float(np.hypot(*(start - self.pos)))
            far = hop > RETRACT_OVER_MM
            seg = np.hypot(*(np.diff(path, axis=0)).T)
            travel = float(_move_seconds(np.array([hop]), SPEEDS["travel"])[0]) + (0.06 if far else 0.0)
            self._add(kind, float(seg.sum()), float(_move_seconds(seg, speed).sum()), travel)
            if self.out is not None:
                if far:
                    lines.append(f"G1 E-{RETRACT_MM} F2400")
                lines.append(f"G0 X{start[0]:.3f} Y{start[1]:.3f} F{SPEEDS['travel'] * 60:.0f}")
                if far:
                    lines.append(f"G1 E{RETRACT_MM} F2400")
                lines.append(f"G1 F{speed * 60:.0f}")
                lines += [f"G1 X{x:.3f} Y{y:.3f} E{e:.5f}"
                          for (x, y), e in zip(path[1:].tolist(), (seg * self.e_per_mm).tolist(), strict=True)]
            self.pos = path[-1]
        if lines:
            self.write("\n".join(lines) + "\n")


def _lines(mask: np.ndarray, step: int, along_x: bool, origin: np.ndarray, cell: float) -> list[np.ndarray]:
    """Straight lines through a mask every `step` cells, back and forth."""
    grid = mask if along_x else mask.T
    picked = np.arange(step // 2, grid.shape[0], step)
    rows, start, end = _runs(grid[picked])
    keep = end - start >= 2
    rows, start, end = rows[keep], start[keep], end[keep]
    if not len(rows):
        return []
    a = np.stack([start + 0.5, picked[rows] + 0.5], 1)
    b = np.stack([end - 0.5, picked[rows] + 0.5], 1)
    back = rows % 2 == 1
    a[back], b[back] = b[back].copy(), a[back].copy()
    order = np.lexsort((np.where(back, -start, start), rows))
    a, b = a[order], b[order]
    if not along_x:
        a, b = a[:, ::-1], b[:, ::-1]
    return list(np.stack([a, b], 1) * cell + origin)


def write_gcode(layers: Layers, props: list[np.ndarray | None], bed: list[float], nozzle: float, material: str,
                out: TextIO | None) -> dict[str, Any]:
    """Walls, infill and supports for every layer. Returns the sums, and what was too thin to print."""
    cell, h = layers.cell, layers.layer
    hot, plate, density = MATERIALS[material]
    half = max(1, int(round(nozzle / 2 / cell)))
    line = max(1, int(round(nozzle / cell)))
    shell = max(2, int(round(SHELL_MM / h)))
    sparse_step = max(line + 1, int(round(nozzle / INFILL / cell)))
    support_step = max(line + 1, int(round(SUPPORT_PITCH_MM / cell)))
    centre = (layers.lo[:2] + layers.hi[:2]) / 2
    origin = np.array([layers.x0, layers.y0]) - centre + np.array([bed[0] / 2, bed[1] / 2])
    w = Writer(out, nozzle, h)
    w.write("; FreeCadClaudeBridge slice_check: the bridge's own slicer, for a G-code viewer and the numbers.\n"
            f"; Not tuned for a machine. nozzle {nozzle} mm, layer {h} mm, {material}, grid {cell} mm\n"
            f"M140 S{plate}\nM104 S{hot}\nG28\nM190 S{plate}\nM109 S{hot}\nG90\nM83\nG92 E0\n")
    lost: dict[str, Any] = {"area": 0.0, "layers": 0, "z": None}
    size = layers.rows * layers.cols
    for k in range(layers.count):
        m = layers.mask(k)
        w.first, w.kind = k == 0, ""
        w.write(f";LAYER_CHANGE\n;Z:{(k + 1) * h:.3f}\n;HEIGHT:{h}\nG1 Z{(k + 1) * h:.3f} F600\n")
        outer = shrink(m, half)
        gone = float((m & ~grow(outer, half + 1)).sum()) * cell * cell
        if gone >= 1.0:
            lost["area"] += gone
            lost["layers"] += 1
            lost["z"] = r2(k * h) if lost["z"] is None else lost["z"]
        inner = shrink(outer, line)
        fill = shrink(inner, half)
        for kind, region in (("Outer wall", outer), ("Inner wall", inner)):
            w.paths(kind, [np.concatenate([p, p[:1]]) * cell + origin for p in loops(region)])
        if fill.any():
            if k < shell or k >= layers.count - shell:
                solid = fill
            else:
                covered = m
                for j in range(1, shell + 1):
                    covered = covered & layers.mask(k - j) & layers.mask(k + j)
                solid = fill & ~covered
                solid = grow(solid, line) & fill if solid.any() else solid
            for kind, region, step in (("Solid infill", solid, line), ("Sparse infill", fill & ~solid, sparse_step)):
                if region.any():
                    w.paths(kind, _lines(region, step, k % 2 == 0, origin, cell))
        packed = props[k]
        if packed is not None:
            prop = np.unpackbits(packed, count=size).reshape(m.shape).astype(bool)
            w.paths("Support", _lines(prop & ~grow(m, 1), support_step, True, origin, cell))
    grams = {kind: e * math.pi * (FILAMENT_MM / 2) ** 2 / 1000 * density for kind, e in w.e.items()}
    total_g, total_s = sum(grams.values()), sum(w.seconds.values()) + layers.count * 0.5
    w.write(f"G1 E-{RETRACT_MM} F2400\nG1 Z{min(bed[2], layers.count * h + 10):.1f} F600\nM104 S0\nM140 S0\nM84\n"
            f"; filament used [g] = {total_g:.2f}\n; estimated printing time = {total_s / 60:.1f} min\n")
    return {"mm": {k: round(v, 1) for k, v in w.mm.items()}, "grams": grams, "seconds": w.seconds, "lost": lost}


def read_gcode(text: str) -> dict[str, Any]:
    """Extruded path length per kind of line, the space the print takes and its filament, from G-code."""
    mm: dict[str, float] = {}
    kind = "Other"
    x: float | None = None
    y: float | None = None
    xs: list[float] = []
    ys: list[float] = []
    for line in text.splitlines():
        if line.startswith(";TYPE:"):
            kind = line[6:].strip()
        if not line.startswith(("G0 ", "G1 ")):
            continue
        nx, ny, e = (re.search(rf"{axis}(-?[\d.]+)", line) for axis in "XYE")
        px, py = x, y
        x = float(nx[1]) if nx else x
        y = float(ny[1]) if ny else y
        if e and float(e[1]) > 0 and (nx or ny) and px is not None and py is not None and x is not None \
                and y is not None:
            mm[kind] = mm.get(kind, 0.0) + math.hypot(x - px, y - py)
            xs += [px, x]
            ys += [py, y]
    grams = re.search(r"^; filament used \[g\] = ([\d.]+)", text, re.M)
    layer_z = [float(z) for z in re.findall(r"^;Z:([\d.]+)", text, re.M)]
    return {"mm": {k: round(v, 1) for k, v in mm.items()}, "x": [min(xs), max(xs)] if xs else None,
            "y": [min(ys), max(ys)] if ys else None, "z": [min(layer_z), max(layer_z)] if layer_z else None,
            "filament_g": float(grams[1]) if grams else None}


# ---------- the whole run

def slice_model(tris: np.ndarray, bed: list[int] | list[float], nozzle: float = 0.4, layer: float = 0.2,
                material: str = "PLA", gcode_path: str | None = None, overhang_deg: float = 45.0,
                bridge_mm: float = 10.0) -> dict[str, Any]:
    """Slice, simulate and write G-code. `gcode_path` gets "_with_supports" before its ending when the
    print needs supports. The model is taken as it stands, its lowest point on the bed."""
    material = material.upper()
    if material not in MATERIALS:
        raise ValueError(f"unknown material {material!r}; known: " + ", ".join(MATERIALS))
    if len(tris) == 0:
        raise ValueError("nothing to slice: the mesh is empty")
    if not 0.04 <= layer <= nozzle:
        raise ValueError(f"layer {layer} mm does not suit a {nozzle} mm nozzle (0.04 mm up to the nozzle size)")
    size = tris.max((0, 1)) - tris.min((0, 1))
    plate = [float(b) for b in bed]
    cell = resolution(size, nozzle, layer)
    layers = Layers(tris, layer, cell)
    sim = simulate(layers, nozzle, overhang_deg, bridge_mm)
    problems: list[str] = []
    warnings: list[str] = []
    over = [f"{'xyz'[i]} {r2(size[i])} > {plate[i]:g} mm" for i in range(3) if size[i] > plate[i] + 1e-6]
    fits = not over
    if over:
        problems.append("does not fit the printer (" + ", ".join(over) + "): scale it, turn it or split it")
    found = sorted(sim["findings"], key=lambda f: (["floating", "cantilever", "bridge"].index(f["kind"]),
                                                   -f.get("mm", 0.0), -f["area_mm2"]))
    floating = [f for f in found if f["kind"] == "floating"]
    arms = [f for f in found if f["kind"] == "cantilever"]
    spans = [f for f in found if f["kind"] == "bridge"]
    if floating:
        f = floating[0]
        problems.append(f"{len(floating)} region(s) start in mid air, the largest at z={f['z']} mm near "
                        f"x={f['at'][0]}, y={f['at'][1]} ({f['area_mm2']} mm²): connect it to the model, turn the "
                        "model or split it")
    hard = [f for f in arms if f["mm"] >= CANTILEVER_FAIL_MM]
    if hard:
        f = hard[0]
        problems.append(f"{len(hard)} region(s) hang free with nothing below, the longest {f['mm']} mm at "
                        f"z={f['z']} mm near x={f['at'][0]}, y={f['at'][1]}: add a 45° chamfer or gusset, turn the "
                        "model or split it")
    soft = [f for f in arms if f["mm"] < CANTILEVER_FAIL_MM]
    if soft:
        warnings.append(f"{len(soft)} overhang(s) of {soft[-1]['mm']}–{soft[0]['mm']} mm print into the air and will "
                        f"droop (first at z={min(f['z'] for f in soft)} mm)")
    if spans:
        f = spans[0]
        warnings.append(f"{len(spans)} bridge(s) longer than {bridge_mm:g} mm will sag, the longest {f['mm']} mm at "
                        f"z={f['z']} mm near x={f['at'][0]}, y={f['at'][1]}: shorten the span or arch it")
    steep = sim["steep"]
    if steep["area"] >= 20.0:
        warnings.append(f"slopes steeper than {overhang_deg:.0f}° from the vertical between z={steep['z0']} and "
                        f"z={steep['z1']} mm ({r2(steep['area'])} mm² hang over): the underside comes out rough")
    if layers.leaks:
        warnings.append("the mesh is not closed (holes or flipped faces); the layers may be filled wrongly. "
                        "Repair the mesh first")
    height = layers.count * layer
    if height > 20 and sim["areas"][0] < 0.1 * max(sim["areas"]):
        warnings.append(f"small footprint: only {r2(sim['areas'][0])} mm² touch the bed under a {r2(height)} mm tall "
                        "print; widen the base or print with a brim")
    needs = bool(floating or arms or spans)
    props: list[np.ndarray | None] = [None] * layers.count
    on_model = False
    if needs:
        props, on_model = supports(layers, sim["seeds"])
    target = None
    if gcode_path and fits:
        target = re.sub(r"(\.[^.]*)$", r"_with_supports\1", gcode_path) if needs else gcode_path
        with open(target, "w", encoding="utf-8") as out:
            done = write_gcode(layers, props, plate, nozzle, material, out)
    else:
        done = write_gcode(layers, props, plate, nozzle, material, None)
    lost = done["lost"]
    if lost["layers"] >= 3:
        warnings.append(f"features thinner than the nozzle are not printed: {r2(lost['area'] / lost['layers'])} mm² "
                        f"per layer over {lost['layers']} layers, from z={lost['z']} mm. Make them at least "
                        f"{2 * nozzle} mm thick")
    prop_g = done["grams"].get("Support", 0.0)
    prop_s = done["seconds"].get("Support", 0.0)
    model_g = sum(done["grams"].values()) - prop_g
    model_s = sum(done["seconds"].values()) - prop_s + layers.count * 0.5
    density = MATERIALS[material][2]
    result: dict[str, Any] = {
        "printable_without_supports": fits and not (floating or hard), "problems": problems, "warnings": warnings,
        "findings": found[:MAX_FINDINGS], "layers": layers.count, "height_mm": r2(height),
        "minutes": round(model_s / 60, 1), "filament_g": round(model_g, 2),
        "filament_m": round(model_g / density / (math.pi * (FILAMENT_MM / 2) ** 2), 2),
        "bed_contact_mm2": r2(sim["areas"][0]), "bridges": sim["bridges"],
        "steep_overhang_mm2": r2(steep["area"]), "path_mm": done["mm"], "grid_mm": cell}
    if len(found) > MAX_FINDINGS:
        result["more_findings"] = len(found) - MAX_FINDINGS
    if needs:
        result["with_supports"] = {"prints": fits, "extra_g": round(prop_g, 2), "extra_minutes": round(prop_s / 60, 1),
                                   "on_model": on_model}
    if target:
        result["gcode"] = target
    return result
