"""Sculpt organic shapes the way clay or SDF sculpting tools do, outside FreeCAD.

A part is a list of primitives (sphere, ellipsoid, tapered capsule, strand, rounded box, torus,
cylinder) that are added, subtracted or intersected with a smooth blend radius, so limbs grow
out of a body like clay instead of meeting at hard seams. Optional fractal surface detail adds
skin or stone texture; `fur` adds grooves that run along a flow direction. Templates (a sitting cat,
a standing dog, a standing person) are tested starting points that are scaled to the wanted height
and extended with the caller's own parts and primitives. The signed distance field is sampled on
a grid, turned into a closed triangle mesh (surface nets), Taubin-smoothed, and written as binary
STL. FreeCAD only imports the finished file: no geometry kernel call that could crash the user's FreeCAD.

All sizes in mm. NumPy only.
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any

import numpy as np

DEFAULT_CELLS = 300        # grid cells along the scene's longest side: a 6 cm figure gets 0.2 mm voxels
MAX_CELLS = 400            # the finest grid a caller can ask for (memory: ~0.3 GB for a cube-shaped part)
TEMPLATES = Path(__file__).with_name("templates")
FUR_MAX_DEPTH = 0.3        # × groove width
MAX_PRIMITIVES = 3000
CHUNK = 1 << 20             # points evaluated at once (keeps the temporaries of one primitive near 100 MB)
PINCH_NUDGE = 0.002        # mm a split vertex moves into its own sheet
DEFAULT_BLEND = 1.0


# ---------- primitives (p: (N, 3) points in mm) ----------

def _rotation(deg: Any) -> np.ndarray | None:
    if not deg:
        return None
    rx, ry, rz = (math.radians(float(a)) for a in deg)
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    rot_x = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    rot_y = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rot_z = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return rot_z @ rot_y @ rot_x


def _local(p: np.ndarray, center: Any, rotate: Any) -> np.ndarray:
    q = p - np.asarray(center, dtype=float)
    r = _rotation(rotate)
    return q @ r if r is not None else q        # row vectors: q @ R == R^T q


def _vec(prim: dict[str, Any], key: str, n: int = 3) -> np.ndarray:
    if key not in prim:
        raise ValueError(f"{prim.get('type')}: '{key}' is missing")
    v = np.asarray(prim[key], dtype=float).reshape(-1)
    if v.size != n:
        raise ValueError(f"{prim.get('type')}: '{key}' needs {n} numbers")
    return v


def _num(prim: dict[str, Any], key: str, default: float | None = None) -> float:
    if key not in prim:
        if default is None:
            raise ValueError(f"{prim.get('type')}: '{key}' is missing")
        return default
    return float(prim[key])


def _round_cone(p: np.ndarray, a: np.ndarray, b: np.ndarray, r1: float, r2: float) -> np.ndarray:
    """Distance to a capsule whose radius goes from r1 at a to r2 at b (Inigo Quilez' sdRoundCone)."""
    ba = b - a
    l2 = float(ba @ ba)
    if l2 < 1e-12:
        return np.linalg.norm(p - a, axis=1) - max(r1, r2)
    rr = r1 - r2
    a2 = l2 - rr * rr
    il2 = 1.0 / l2
    pa = p - a
    y = pa @ ba
    z = y - l2
    x2 = np.einsum("ij,ij->i", pa * l2 - np.outer(y, ba), pa * l2 - np.outer(y, ba))
    y2 = y * y * l2
    z2 = z * z * l2
    k = math.copysign(1.0, rr) * rr * rr * x2
    d_end = np.sqrt(x2 + z2) * il2 - r2
    d_start = np.sqrt(x2 + y2) * il2 - r1
    d_side = (np.sqrt(np.maximum(x2 * a2 * il2, 0)) + y * rr) * il2 - r1
    return np.where(np.sign(z) * a2 * z2 > k, d_end, np.where(np.sign(y) * a2 * y2 < k, d_start, d_side))


def _sdf(prim: dict[str, Any], p: np.ndarray) -> np.ndarray:
    kind = prim.get("type")
    if kind == "sphere":
        return np.linalg.norm(p - _vec(prim, "center"), axis=1) - _num(prim, "radius")
    if kind == "ellipsoid":
        r = _vec(prim, "radii")
        q = _local(p, _vec(prim, "center"), prim.get("rotate"))
        k0 = np.linalg.norm(q / r, axis=1)
        k1 = np.linalg.norm(q / (r * r), axis=1)
        return np.where(k1 > 1e-9, k0 * (k0 - 1.0) / np.maximum(k1, 1e-9), -float(r.min()))
    if kind == "capsule":
        r1 = _num(prim, "radius")
        return _round_cone(p, _vec(prim, "a"), _vec(prim, "b"), r1, _num(prim, "radius_b", r1))
    if kind == "strand":
        pts = np.asarray(prim.get("points") or [], dtype=float)
        if pts.ndim != 2 or len(pts) < 2:
            raise ValueError("strand: 'points' needs at least two [x, y, z] points")
        r0 = _num(prim, "radius")
        r1 = _num(prim, "radius_end", r0)
        lengths = np.r_[0, np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))]
        radii = r0 + (r1 - r0) * lengths / max(lengths[-1], 1e-9)
        d = np.full(len(p), np.inf)
        for i in range(len(pts) - 1):
            d = np.minimum(d, _round_cone(p, pts[i], pts[i + 1], radii[i], radii[i + 1]))
        return d
    if kind == "box":
        half = _vec(prim, "size") / 2.0
        rnd = min(_num(prim, "round", 0.0), float(half.min()))
        q = np.abs(_local(p, _vec(prim, "center"), prim.get("rotate"))) - (half - rnd)
        return np.linalg.norm(np.maximum(q, 0), axis=1) + np.minimum(q.max(axis=1), 0) - rnd
    if kind == "torus":
        q = _local(p, _vec(prim, "center"), prim.get("rotate"))
        ring = np.hypot(q[:, 0], q[:, 1]) - _num(prim, "radius")
        return np.hypot(ring, q[:, 2]) - _num(prim, "thickness")
    if kind == "cylinder":
        a, b, r = _vec(prim, "a"), _vec(prim, "b"), _num(prim, "radius")
        ba = b - a
        baba = float(ba @ ba)
        pa = p - a
        paba = pa @ ba
        x = np.linalg.norm(pa * baba - np.outer(paba, ba), axis=1) - r * baba
        y = np.abs(paba - baba * 0.5) - baba * 0.5
        x2, y2 = x * x, y * y * baba
        d = np.where(np.maximum(x, y) < 0, -np.minimum(x2, y2),
                     np.where(x > 0, x2, 0) + np.where(y > 0, y2, 0))
        return np.sign(d) * np.sqrt(np.abs(d)) / baba
    raise ValueError(f"unknown primitive type '{kind}' (use {', '.join(KINDS)})")


KINDS = ("sphere", "ellipsoid", "capsule", "strand", "box", "torus", "cylinder")
HAIR_STYLES = ("short", "long", "bob", "beard", "fur")


def _hair_strands(prim: dict[str, Any]) -> list[dict[str, Any]]:
    """Many tapered strands rooted on a scalp (sphere or ellipsoid), lying along it or hanging down."""
    style = str(prim.get("style", "short"))
    if style not in HAIR_STYLES:
        raise ValueError(f"unknown hair style '{style}' (use {', '.join(HAIR_STYLES)})")
    center = _vec(prim, "center")
    radii = _vec(prim, "radii") if "radii" in prim else np.full(3, _num(prim, "radius"))
    count = int(prim.get("count", 40))
    length = _num(prim, "length", float(radii.mean()))
    radius = _num(prim, "thickness", max(0.12, 0.05 * length + 0.1))
    rng = np.random.default_rng(int(prim.get("seed", 1)))
    out = []
    tries = 0
    while len(out) < count and tries < 50 * count:
        tries += 1
        d = rng.normal(size=3)
        d /= np.linalg.norm(d)
        if style in ("short", "long", "bob") and d[2] < -0.3:
            continue                                  # hair grows on the upper head
        if style == "beard" and not (d[2] < -0.15 and d[1] < -0.3):
            continue                                  # a beard on the lower front
        root = center + radii * d * 0.96
        n_pts = 4
        pts = [root]
        if style in ("short", "fur"):
            # along the scalp, towards the back and down, as a lock lying on the head
            flow = np.array([0.0, 0.35, -1.0]) if style == "short" else np.array([0.0, 0.6, -0.8])
            step_angle = length / (n_pts - 1) / max(float(radii.mean()), 1e-6)
            cur = d.copy()
            for _ in range(n_pts - 1):
                tangent = flow - cur * np.dot(flow, cur)
                if np.linalg.norm(tangent) < 1e-6:
                    tangent = np.array([1.0, 0, 0])
                tangent /= np.linalg.norm(tangent)
                cur = cur * math.cos(step_angle) + tangent * math.sin(step_angle)
                cur /= np.linalg.norm(cur)
                pts.append(center + radii * cur * (0.96 + 0.06 * len(pts) / n_pts))
        else:
            out_dir = d * radii / max(float(radii.max()), 1e-6)
            gravity = np.array([0.0, 0.3 if style == "beard" else 0.0, -1.0])
            wave = rng.uniform(-0.15, 0.15, size=3)
            for k in range(1, n_pts):
                f = k / (n_pts - 1)
                drift = out_dir * 0.35 * (1 - f) + gravity * f + wave * math.sin(math.pi * f)
                drift /= np.linalg.norm(drift)
                pts.append(pts[-1] + drift * length / (n_pts - 1))
        stretch = rng.uniform(0.85, 1.05)
        pts = [pts[0] + (q - pts[0]) * stretch for q in pts]
        out.append({"type": "strand", "points": [[float(x) for x in q] for q in pts],
                    "radius": float(radius * rng.uniform(0.85, 1.1)), "radius_end": float(radius * 0.3),
                    **({"blend": prim["blend"]} if "blend" in prim else {})})
    return out


def expand_macros(prims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Presets that stand for many primitives ("hair") become those primitives; the rest pass through."""
    out: list[dict[str, Any]] = []
    for p in prims:
        if p.get("type") == "hair":
            out.extend(_hair_strands(p))
        else:
            out.append(p)
    return out


def _bounds(prim: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    kind = prim.get("type")
    if kind == "hair":                      # the preset before expansion: the scalp plus the strands' length
        center = _vec(prim, "center")
        radii = _vec(prim, "radii") if "radii" in prim else np.full(3, _num(prim, "radius"))
        reach = radii * 1.05 + _num(prim, "length", float(radii.mean()))
        return center - reach, center + reach
    if kind not in KINDS:
        raise ValueError(f"unknown primitive type '{kind}' (use {', '.join(KINDS)})")
    if kind in ("capsule", "cylinder"):
        a, b = _vec(prim, "a"), _vec(prim, "b")
        r = max(_num(prim, "radius"), _num(prim, "radius_b", 0.0))
        return np.minimum(a, b) - r, np.maximum(a, b) + r
    if kind == "strand":
        pts = np.asarray(prim.get("points") or [[0, 0, 0]], dtype=float)
        r = max(_num(prim, "radius"), _num(prim, "radius_end", 0.0))
        return pts.min(0) - r, pts.max(0) + r
    c = _vec(prim, "center")
    if kind == "sphere":
        r = _num(prim, "radius")
    elif kind == "ellipsoid":
        r = float(_vec(prim, "radii").max())
    elif kind == "box":
        r = float(np.linalg.norm(_vec(prim, "size")) / 2)
    elif kind == "torus":
        r = _num(prim, "radius") + _num(prim, "thickness")
    else:
        raise ValueError(f"unknown primitive type '{kind}' (use {', '.join(KINDS)})")
    return c - r, c + r


# ---------- smooth booleans ----------

def _smooth_union(a: np.ndarray, b: np.ndarray, k: float) -> np.ndarray:
    if k <= 0:
        return np.minimum(a, b)
    h = np.clip(0.5 + 0.5 * (b - a) / k, 0, 1)
    return b * (1 - h) + a * h - k * h * (1 - h)


def _smooth_subtract(base: np.ndarray, cut: np.ndarray, k: float) -> np.ndarray:
    if k <= 0:
        return np.maximum(base, -cut)
    h = np.clip(0.5 - 0.5 * (base + cut) / k, 0, 1)
    return base * (1 - h) - cut * h + k * h * (1 - h)


def _smooth_intersect(a: np.ndarray, b: np.ndarray, k: float) -> np.ndarray:
    if k <= 0:
        return np.maximum(a, b)
    h = np.clip(0.5 - 0.5 * (b - a) / k, 0, 1)
    return b * (1 - h) + a * h + k * h * (1 - h)


# ---------- surface detail ----------

def _value_noise(points: np.ndarray, scale: float, seed: int, octaves: int = 3) -> np.ndarray:
    """Fractal value noise in [-1, 1] (smooth, repeatable)."""
    rng = np.random.default_rng(seed)
    table = rng.uniform(-1, 1, 4096)
    out = np.zeros(len(points))
    amp, total, freq = 1.0, 0.0, 1.0 / max(scale, 1e-6)
    for octave in range(octaves):
        q = points * freq + octave * 17.3
        i = np.floor(q).astype(np.int64)
        f = q - i
        f = f * f * (3 - 2 * f)
        acc = np.zeros(len(points))
        for dx in (0, 1):
            for dy in (0, 1):
                for dz in (0, 1):
                    h = ((i[:, 0] + dx) * 73856093 ^ (i[:, 1] + dy) * 19349663 ^ (i[:, 2] + dz) * 83492791) & 4095
                    w = ((f[:, 0] if dx else 1 - f[:, 0]) * (f[:, 1] if dy else 1 - f[:, 1])
                         * (f[:, 2] if dz else 1 - f[:, 2]))
                    acc += w * table[h]
        out += amp * acc
        total += amp
        amp *= 0.5
        freq *= 2.0
    return out / total


# ---------- field, mesh ----------

def choose_voxel(bounds: list[tuple[float, float, float]], requested: float | None = None) -> float:
    lo, hi = np.asarray(bounds[0], dtype=float), np.asarray(bounds[1], dtype=float)
    longest = float((hi - lo).max())
    return max(float(requested or longest / DEFAULT_CELLS), longest / MAX_CELLS, 0.05)


def _fur_params(fur: dict[str, Any]) -> tuple[float, float, float]:
    try:
        depth, length, width = (float(fur[k]) for k in ("depth", "length", "width"))
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError("fur needs depth, length and width in mm") from e
    if min(depth, length, width) <= 0:
        raise ValueError("fur depth, length and width must be > 0")
    # deeper than about a third of the groove width, the surface folds over itself (islands, bad edges)
    return min(depth, FUR_MAX_DEPTH * width), length, width


def fur_noise(points: np.ndarray, fur: dict[str, Any]) -> np.ndarray:
    """Ridges in [-1, 1] stretched along the flow: grooves like locks of fur.

    The flow is a fixed `direction`, or away `from` a point (fur that grows out from a crown or the nose).
    Noise sampled in coordinates squeezed along the flow changes slowly along it and fast across it.
    """
    _, length, width = _fur_params(fur)
    if fur.get("from") is not None:
        d = points - np.asarray(fur["from"], dtype=float)
    else:
        d = np.broadcast_to(np.asarray(fur.get("direction", [0, 0, -1]), dtype=float), points.shape).copy()
    d /= np.linalg.norm(d, axis=1, keepdims=True) + 1e-9
    along = np.einsum("ij,ij->i", points, d)[:, None]
    q = points - d * along * (1 - min(1.0, width / length))
    n = _value_noise(q, width, int(fur.get("seed", 3)), octaves=2)
    return np.cos(np.pi * np.clip(n, -1, 1))       # smooth ridges where the noise crosses zero


def _part_margin(part: dict[str, Any]) -> float:
    detail = part.get("detail") or {}
    fur = part.get("fur") or None
    return max(float(part.get("blend", DEFAULT_BLEND)), 0) + abs(float(detail.get("amplitude", 0))) \
        + (_fur_params(fur)[0] if fur else 0.0)


def _frame(parts: list[dict[str, Any]], voxel: float | None) -> tuple[np.ndarray, np.ndarray, list[np.ndarray],
                                                                        float, float]:
    """The grid every part of a union is sampled on: origin, size, axes, voxel and a far value."""
    margin = 0.0
    lo_all: np.ndarray | None = None
    hi_all: np.ndarray | None = None
    for part in parts:
        prims = part.get("primitives") or []
        if not prims:
            raise ValueError("a part needs at least one primitive")
        if len(prims) > MAX_PRIMITIVES:
            raise ValueError(f"too many primitives ({len(prims)} > {MAX_PRIMITIVES})")
        boxes = [_bounds(p) for p in prims]
        for p in prims:                        # validate everything before the heavy work
            _sdf(p, np.zeros((1, 3)))
        adds = [b for p, b in zip(prims, boxes, strict=True) if p.get("op", "add") == "add"] or boxes
        lo = np.min([b[0] for b in adds], axis=0)     # cuts never make a shape larger: a big box that
        hi = np.max([b[1] for b in adds], axis=0)     # flattens the base does not enlarge the grid
        lo_all = lo if lo_all is None else np.minimum(lo_all, lo)
        hi_all = hi if hi_all is None else np.maximum(hi_all, hi)
        margin = max(margin, _part_margin(part))
    assert lo_all is not None and hi_all is not None
    voxel = choose_voxel([tuple(lo_all), tuple(hi_all)], voxel)
    lo, hi = lo_all - (margin + 2 * voxel), hi_all + (margin + 2 * voxel)
    n = np.ceil((hi - lo) / voxel).astype(int) + 1
    axes = [lo[i] + np.arange(n[i]) * voxel for i in range(3)]
    return lo, n, axes, voxel, float(np.linalg.norm(hi - lo))


def field(part: dict[str, Any], voxel: float | None = None) -> tuple[np.ndarray, np.ndarray, float]:
    lo, n, axes, voxel, far = _frame([part], voxel)
    return _fill(part, lo, n, axes, voxel, far), lo, voxel


def field_union(parts: list[dict[str, Any]], voxel: float | None = None) -> tuple[np.ndarray, np.ndarray, float]:
    """Several parts as one body: each keeps its own blend, detail and fur, then the fields are united."""
    lo, n, axes, voxel, far = _frame(parts, voxel)
    grid: np.ndarray | None = None
    for part in parts:
        one = _fill(part, lo, n, axes, voxel, far)
        # a part's own blend joins it to the rest; at least a voxel and a half, so an eye that only touches
        # its socket still becomes one body
        k = max(float(part.get("blend", DEFAULT_BLEND)), 1.5 * voxel)
        grid = one if grid is None else _smooth_union(grid, one, k)
    assert grid is not None
    return grid, lo, voxel


def _fill(part: dict[str, Any], lo: np.ndarray, n: np.ndarray, axes: list[np.ndarray], voxel: float,
          far: float) -> np.ndarray:
    prims = part.get("primitives") or []
    blend = float(part.get("blend", DEFAULT_BLEND))
    detail = part.get("detail") or {}
    amp = float(detail.get("amplitude", 0))
    fur = part.get("fur") or None
    fur_depth = _fur_params(fur)[0] if fur else 0.0
    boxes = [_bounds(p) for p in prims]
    grid = np.full(tuple(n), far, dtype=np.float32)
    for prim, (blo, bhi) in zip(prims, boxes, strict=True):
        k = float(prim.get("blend", blend))
        reach = k + voxel * 2
        sl = tuple(slice(max(0, int((blo[i] - reach - lo[i]) // voxel)),
                         min(n[i], int((bhi[i] + reach - lo[i]) // voxel) + 2)) for i in range(3))
        sub_axes = [axes[i][sl[i]] for i in range(3)]
        if min(len(a) for a in sub_axes) == 0:
            continue
        d = _sample(prim, sub_axes)
        cur = grid[sl]
        op = prim.get("op", "add")
        if op == "add":
            grid[sl] = _smooth_union(cur, d, k)
        elif op == "subtract":
            grid[sl] = _smooth_subtract(cur, d, k)
        elif op == "intersect":
            out = np.full(grid.shape, far, dtype=np.float32)
            out[sl] = _smooth_intersect(cur, d, k)
            grid = np.where(grid < far, out, grid).astype(np.float32)
        else:
            raise ValueError(f"unknown op '{op}' (use add, subtract or intersect)")
    if amp:
        near = np.abs(grid) < (abs(amp) * 3 + voxel * 3)
        idx = np.nonzero(near)
        scale, seed = float(detail.get("scale", 3.0)), int(detail.get("seed", 1))
        for start in range(0, len(idx[0]), CHUNK):
            part_idx = tuple(i[start:start + CHUNK] for i in idx)
            pts = np.stack([axes[0][part_idx[0]], axes[1][part_idx[1]], axes[2][part_idx[2]]], axis=1)
            grid[part_idx] -= amp * _value_noise(pts, scale, seed)
    if fur:
        idx = np.nonzero(np.abs(grid) < fur_depth * 3 + voxel * 3)
        for start in range(0, len(idx[0]), CHUNK):
            part_idx = tuple(i[start:start + CHUNK] for i in idx)
            pts = np.stack([axes[0][part_idx[0]], axes[1][part_idx[1]], axes[2][part_idx[2]]], axis=1)
            # carve only (0..depth): grooves between the locks; pushing out could leave islands in the air
            grid[part_idx] += (fur_depth * 0.5 * (1 - fur_noise(pts, fur))).astype(np.float32)
    return grid


def _sample(prim: dict[str, Any], sub_axes: list[np.ndarray]) -> np.ndarray:
    """The primitive's distance on a grid, evaluated in slabs so a large primitive stays small in memory."""
    out = np.empty(tuple(len(a) for a in sub_axes), dtype=np.float32)
    plane = len(sub_axes[1]) * len(sub_axes[2])
    step = max(1, CHUNK // max(plane, 1))
    gy, gz = np.meshgrid(sub_axes[1], sub_axes[2], indexing="ij")
    for start in range(0, len(sub_axes[0]), step):
        xs = sub_axes[0][start:start + step]
        pts = np.empty((len(xs), plane, 3))
        pts[:, :, 0] = xs[:, None]
        pts[:, :, 1] = gy.ravel()
        pts[:, :, 2] = gz.ravel()
        out[start:start + step] = _sdf(prim, pts.reshape(-1, 3)).reshape(len(xs), *gy.shape)
    return out


_CORNERS = np.array([(i, j, k) for i in (0, 1) for j in (0, 1) for k in (0, 1)])
_EDGES = [(a, b) for a in range(8) for b in range(a + 1, 8) if np.abs(_CORNERS[a] - _CORNERS[b]).sum() == 1]


def surface_nets(grid: np.ndarray, origin: np.ndarray, voxel: float) -> tuple[np.ndarray, np.ndarray]:
    """Closed triangle mesh of the zero level of a sampled distance field (naive surface nets)."""
    inside = grid < 0
    nx, ny, nz = grid.shape
    corner_in = [inside[i:nx - 1 + i, j:ny - 1 + j, k:nz - 1 + k] for i, j, k in _CORNERS]
    any_in = np.logical_or.reduce(corner_in)
    all_in = np.logical_and.reduce(corner_in)
    active = any_in & ~all_in
    ci, cj, ck = np.nonzero(active)
    cell_id = np.full(active.shape, -1, dtype=np.int32)
    cell_id[ci, cj, ck] = np.arange(len(ci))
    pos = np.zeros((len(ci), 3))
    cnt = np.zeros(len(ci))
    for a, b in _EDGES:
        ca, cb = _CORNERS[a], _CORNERS[b]
        fa = grid[ci + ca[0], cj + ca[1], ck + ca[2]].astype(float)
        fb = grid[ci + cb[0], cj + cb[1], ck + cb[2]].astype(float)
        m = (fa < 0) != (fb < 0)
        t = fa[m] / (fa[m] - fb[m])
        pos[m] += ca + t[:, None] * (cb - ca)
        cnt[m] += 1
    verts = origin + (np.stack([ci, cj, ck], axis=1) + pos / np.maximum(cnt, 1)[:, None]) * voxel
    quads, flips = [], []
    for axis in range(3):
        u, v = [a for a in range(3) if a != axis]
        sl_a = [slice(None)] * 3
        sl_b = [slice(None)] * 3
        sl_a[axis] = slice(0, -1)
        sl_b[axis] = slice(1, None)
        change = inside[tuple(sl_a)] != inside[tuple(sl_b)]
        valid = np.zeros_like(change)
        rng = [slice(None)] * 3
        rng[u] = slice(1, change.shape[u] - 0)
        rng[v] = slice(1, change.shape[v] - 0)
        valid[tuple(rng)] = True
        pi = np.nonzero(change & valid)
        p = np.stack(pi, axis=1)
        p = p[(p[:, u] < active.shape[u] + 1) & (p[:, v] < active.shape[v] + 1)]

        def cell(du: int, dv: int, p: np.ndarray = p, u: int = u, v: int = v) -> np.ndarray:
            c = p.copy()
            c[:, u] -= du
            c[:, v] -= dv
            ok = (c >= 0).all(axis=1) & (c[:, 0] < active.shape[0]) & (c[:, 1] < active.shape[1]) \
                & (c[:, 2] < active.shape[2])
            ids = np.full(len(c), -1)
            ids[ok] = cell_id[c[ok, 0], c[ok, 1], c[ok, 2]]
            return ids

        q = np.stack([cell(0, 0), cell(1, 0), cell(1, 1), cell(0, 1)], axis=1)
        keep = (q >= 0).all(axis=1)
        start_inside = inside[p[keep, 0], p[keep, 1], p[keep, 2]]
        quads.append(q[keep])
        flips.append(np.where(start_inside, 1.0, -1.0)[:, None] * np.eye(3)[axis])
    quads_arr = np.concatenate(quads) if quads else np.zeros((0, 4), dtype=np.int64)
    out_dir = np.concatenate(flips) if flips else np.zeros((0, 3))
    tris = np.concatenate([quads_arr[:, [0, 1, 2]], quads_arr[:, [0, 2, 3]]])
    want = np.concatenate([out_dir, out_dir])
    a, b, c = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    normals = np.cross(b - a, c - a)
    wrong = np.einsum("ij,ij->i", normals, want) < 0
    tris[wrong] = tris[wrong][:, [0, 2, 1]]
    used = np.unique(tris)
    remap = np.full(len(verts), -1)
    remap[used] = np.arange(len(used))
    return verts[used], remap[tris]


def split_pinches(verts: np.ndarray, tris: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Make the mesh manifold: where two sheets touch along an edge (shared by four triangles, a surface-nets
    corner case at thin spots or under surface detail), give each sheet its own copy of the vertices."""
    tris = tris.copy()
    for _ in range(4):
        e = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
        key = np.sort(e, axis=1)
        uniq, inv, counts = np.unique(key, axis=0, return_inverse=True, return_counts=True)
        bad = np.nonzero(counts > 2)[0]
        if len(bad) == 0:
            break
        n_t = len(tris)
        normals = np.cross(verts[tris[:, 1]] - verts[tris[:, 0]], verts[tris[:, 2]] - verts[tris[:, 0]])
        normals /= np.maximum(np.linalg.norm(normals, axis=1), 1e-12)[:, None]
        tri_of = np.arange(3 * n_t) % n_t
        # links between triangles: manifold edges connect their two triangles
        links: dict[int, set[int]] = {}
        good = np.nonzero(counts == 2)[0]
        order = np.argsort(inv, kind="stable")
        starts = np.r_[0, np.cumsum(counts)]
        pinned: set[int] = set()
        for k in bad:
            members = order[starts[k]:starts[k + 1]]
            a, b = uniq[k]
            fwd = [m for m in members if e[m, 0] == a]
            bwd = [m for m in members if e[m, 0] != a]
            used: set[int] = set()
            for f in fwd:                      # pair each direction with the most similar opposite triangle
                tf = tri_of[f]
                best = max((g for g in bwd if g not in used), key=lambda g: normals[tf] @ normals[tri_of[g]],
                           default=None)
                if best is None:
                    continue
                used.add(best)
                links.setdefault(int(tf), set()).add(int(tri_of[best]))
                links.setdefault(int(tri_of[best]), set()).add(int(tf))
            pinned.update((int(a), int(b)))
        for k in good:
            m1, m2 = order[starts[k]], order[starts[k] + 1]
            t1, t2 = int(tri_of[m1]), int(tri_of[m2])
            if set(tris[t1]) & pinned:
                links.setdefault(t1, set()).add(t2)
                links.setdefault(t2, set()).add(t1)
        extra: list[np.ndarray] = []
        next_id = len(verts)

        def position(ids: np.ndarray, verts: np.ndarray = verts, extra: list = extra) -> np.ndarray:
            all_v = np.concatenate([verts, np.array(extra).reshape(-1, 3)]) if extra else verts
            return all_v[ids]

        for v in pinned:
            around = [int(t) for t in np.nonzero((tris == v).any(axis=1))[0]]
            seen: set[int] = set()
            groups = []
            for t in around:
                if t in seen:
                    continue
                stack, group = [t], []
                seen.add(t)
                while stack:
                    cur = stack.pop()
                    group.append(cur)
                    for nb in links.get(cur, ()):
                        if nb in around and nb not in seen and v in tris[nb]:
                            seen.add(nb)
                            stack.append(nb)
                groups.append(group)
            for group in groups[1:]:
                # nudge the copy towards its own sheet: STL keeps only coordinates, and readers (FreeCAD
                # included) merge identical points, which would bring the pinch back
                centre = position(tris[group].ravel()).mean(axis=0)
                step = centre - verts[v]
                step *= PINCH_NUDGE / max(float(np.linalg.norm(step)), 1e-12)
                extra.append(verts[v] + step)
                for t in group:
                    tris[t][tris[t] == v] = next_id
                next_id += 1
        if extra:
            verts = np.concatenate([verts, np.array(extra)])
    return verts, tris


def taubin(verts: np.ndarray, tris: np.ndarray, iterations: int, lam: float = 0.5, mu: float = -0.53) -> np.ndarray:
    """Smooth without shrinking: alternate a Laplacian step and a slightly larger inverse step."""
    if iterations <= 0 or len(tris) == 0:
        return verts
    edges = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    edges = np.unique(np.sort(edges, axis=1), axis=0)
    deg = np.bincount(edges.ravel(), minlength=len(verts)).astype(float)
    v = verts.copy()
    for _ in range(iterations):
        for factor in (lam, mu):
            acc = np.zeros_like(v)
            np.add.at(acc, edges[:, 0], v[edges[:, 1]])
            np.add.at(acc, edges[:, 1], v[edges[:, 0]])
            v = v + factor * (acc / np.maximum(deg, 1)[:, None] - v)
    return v


Parts = dict[str, Any] | list[dict[str, Any]]


def _field_any(part: Parts, voxel: float | None) -> tuple[np.ndarray, np.ndarray, float]:
    return field_union(part, voxel) if isinstance(part, list) else field(part, voxel)


def mesh_part(part: Parts, voxel: float | None = None, smooth: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """One part, or a list of parts united into one body (each with its own blend, detail and fur)."""
    group = [{**p, "primitives": expand_macros(p.get("primitives") or [])} for p in
             (part if isinstance(part, list) else [part])]
    part = group if isinstance(part, list) else group[0]
    best = None
    for attempt in range(3):
        # a pinch that survives the split depends on how the grid happens to lie over the shape:
        # a slightly finer grid moves the samples and usually removes it
        wanted = None if voxel is None and attempt == 0 else \
            (voxel or choose_voxel(*_part_bounds(group))) * (1 - 0.07 * attempt)
        grid, origin, used = _field_any(part, wanted)
        verts, tris = split_pinches(*surface_nets(grid, origin, used))
        bad = non_manifold_edges(tris)
        if best is None or bad < best[0]:
            best = (bad, verts, tris, wanted, used)
        if bad == 0:
            break
    assert best is not None
    bad, verts, tris, wanted, used = best
    if bad:
        verts, tris = _carve_bad_edges(part, wanted, verts, tris)
    tris = drop_specks(verts, tris, 3 * used)
    tris, _holes = close_holes(verts, tris)
    kept = np.unique(tris)                       # a dropped speck leaves vertices nobody uses
    remap = np.full(len(verts), -1)
    remap[kept] = np.arange(len(kept))
    verts, tris = verts[kept], remap[tris]
    furry = any(p.get("fur") for p in group)
    given = [p["smooth"] for p in group if "smooth" in p]
    passes = int(given[0] if given else (min(smooth, 2) if furry else smooth))  # more would erase the fur
    return taubin(verts, tris, passes), tris


def boundary_loops(tris: np.ndarray) -> list[list[int]]:
    """The holes of a mesh: closed runs of edges that belong to one triangle only, in that triangle's direction."""
    e = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    key = np.sort(e, axis=1)
    _, first, counts = np.unique(key, axis=0, return_index=True, return_counts=True)
    single = e[first[counts == 1]]
    nxt: dict[int, int] = {int(a): int(b) for a, b in single}
    loops: list[list[int]] = []
    while nxt:
        start, cur = next(iter(nxt)), None
        loop = [start]
        cur = nxt.pop(start)
        while cur != start and cur in nxt:
            loop.append(cur)
            cur = nxt.pop(cur)
        if cur == start and len(loop) >= 3:
            loops.append(loop)
    return loops


def close_holes(verts: np.ndarray, tris: np.ndarray, max_edges: int = 400) -> tuple[np.ndarray, int]:
    """Fill each small hole with a fan, keeping the outward orientation; returns the triangles and the count."""
    loops = [lp for lp in boundary_loops(tris) if len(lp) <= max_edges]
    if not loops:
        return tris, 0
    extra = [[lp[0], lp[i + 1], lp[i]] for lp in loops for i in range(1, len(lp) - 1)]
    return np.concatenate([tris, np.array(extra, dtype=tris.dtype)]), len(loops)


def bad_edge_points(verts: np.ndarray, tris: np.ndarray) -> np.ndarray:
    """Midpoints of the edges shared by more than two triangles."""
    e = np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1)
    uniq, counts = np.unique(e, axis=0, return_counts=True)
    bad = uniq[counts > 2]
    return (verts[bad[:, 0]] + verts[bad[:, 1]]) / 2 if len(bad) else np.zeros((0, 3))


def _carve_bad_edges(part: Parts, voxel: float | None, verts: np.ndarray,
                     tris: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Edges the splitter cannot untangle (two sheets meeting at a fur ridge): carve the field a third of a
    voxel deeper around them, which turns the ambiguous cell into an ordinary one, and mesh again."""
    grid, origin, used = _field_any(part, voxel)
    for _ in range(4):
        spots = bad_edge_points(verts, tris)
        if not len(spots):
            break
        idx = np.round((spots - origin) / used).astype(int)
        for i, j, k in idx:
            sl = tuple(slice(max(0, c - 1), c + 2) for c in (i, j, k))
            grid[sl] += np.float32(used / 3)
        verts, tris = split_pinches(*surface_nets(grid, origin, used))
    return verts, tris


def non_manifold_edges(tris: np.ndarray) -> int:
    e = np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1)
    _, counts = np.unique(e, axis=0, return_counts=True)
    return int((counts != 2).sum())


def _part_bounds(part: Parts) -> tuple[list[tuple[float, float, float]], None]:
    group = part if isinstance(part, list) else [part]
    boxes = [_bounds(p) for one in group for p in one.get("primitives") or []]
    lo = np.min([b[0] for b in boxes], axis=0)
    hi = np.max([b[1] for b in boxes], axis=0)
    return [tuple(lo), tuple(hi)], None


def pieces(n_verts: int, tris: np.ndarray) -> int:
    """Number of separate pieces (connected components): more than one means something floats."""
    return len(set(piece_labels(n_verts, tris)[np.unique(tris)].tolist()))


def piece_labels(n_verts: int, tris: np.ndarray) -> np.ndarray:
    """The connected component (a representative vertex index) of every vertex."""
    parent = np.arange(n_verts)

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, c in tris:
        ra, rb, rc = find(a), find(b), find(c)
        parent[rb] = ra
        parent[rc] = ra
    return np.array([find(int(v)) for v in range(n_verts)])


def drop_specks(verts: np.ndarray, tris: np.ndarray, size: float) -> np.ndarray:
    """Triangles without the pieces smaller than `size` in every direction (noise leaves such specks)."""
    if not len(tris):
        return tris
    labels = piece_labels(len(verts), tris)
    tri_label = labels[tris[:, 0]]
    keep = np.ones(len(tris), dtype=bool)
    for lab in np.unique(tri_label):
        sel = tri_label == lab
        pts = verts[np.unique(tris[sel])]
        if (pts.max(0) - pts.min(0)).max() < size:
            keep[sel] = False
    return tris[keep] if keep.any() else tris


def _added_distance(parts: list[dict[str, Any]], points: np.ndarray) -> np.ndarray:
    """Distance from points to the added primitives of other parts (blends and cuts ignored)."""
    best = np.full(len(points), np.inf)
    for part in parts:
        for p in part.get("primitives") or []:
            if p.get("op", "add") == "add":
                best = np.minimum(best, _sdf(p, points))
    return best


def floating_pieces(verts: np.ndarray, tris: np.ndarray, others: list[dict[str, Any]], reach: float) -> int:
    """Pieces besides the largest that touch neither the rest of their part nor another part.

    Arms under sleeves or two shoes are separate pieces of their part but are held by the shirt or
    the base; an eyeball floating inside its socket is not.
    """
    labels = piece_labels(len(verts), tris)[np.unique(tris)]
    used = np.unique(tris)
    ids, counts = np.unique(labels, return_counts=True)
    if len(ids) < 2:
        return 0
    largest = ids[np.argmax(counts)]
    loose = 0
    for i in ids:
        if i == largest:
            continue
        pts = verts[used[labels == i]]
        if not others or _added_distance(others, pts[:: max(1, len(pts) // 400)]).min() > reach:
            loose += 1
    return loose


def write_stl(path: str, verts: np.ndarray, tris: np.ndarray) -> None:
    a, b, c = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    n = np.cross(b - a, c - a)
    n /= np.maximum(np.linalg.norm(n, axis=1), 1e-12)[:, None]
    rec = np.zeros(len(tris), dtype=[("n", "<f4", 3), ("a", "<f4", 3), ("b", "<f4", 3), ("c", "<f4", 3),
                                     ("attr", "<u2")])
    rec["n"], rec["a"], rec["b"], rec["c"] = n, a, b, c
    with open(path, "wb") as f:
        f.write(b"FreeCAD x Claude sculpt".ljust(80, b" "))
        f.write(np.uint32(len(tris)).tobytes())
        f.write(rec.tobytes())


# ---------- templates ----------

VECTOR_KEYS = ("center", "radii", "a", "b", "size", "from", "pivot")
SCALAR_KEYS = ("radius", "radius_b", "radius_end", "round", "thickness", "blend", "voxel", "amplitude", "scale",
               "depth", "length", "width")


def template_names() -> list[str]:
    return sorted(p.stem for p in TEMPLATES.glob("*.json"))


def load_template(name: str) -> dict[str, Any]:
    path = TEMPLATES / f"{name}.json"
    if not path.is_file():
        raise ValueError(f"unknown template '{name}' (available: {', '.join(template_names())})")
    return json.loads(path.read_text(encoding="utf-8"))


def _scaled(obj: Any, k: float) -> Any:
    """A copy of a template piece with every length multiplied by k (angles, colors, flags unchanged)."""
    if isinstance(obj, list):
        return [_scaled(x, k) for x in obj]
    if not isinstance(obj, dict):
        return obj
    out: dict[str, Any] = {}
    for key, v in obj.items():
        if key in VECTOR_KEYS and isinstance(v, list):
            out[key] = [float(x) * k for x in v]
        elif key == "points":
            out[key] = [[float(x) * k for x in p] for p in v]
        elif key in SCALAR_KEYS and isinstance(v, (int, float)):
            out[key] = float(v) * k
        elif key == "box" and isinstance(v, list):       # a joint's region: two corners
            out[key] = [[float(x) * k for x in corner] for corner in v]
        elif key in ("primitives", "detail", "fur", "joints"):
            out[key] = _scaled(v, k)
        else:
            out[key] = v
    return out


def _joints(tpl: dict[str, Any], k: float) -> dict[str, Any]:
    """The template's joints with pivot and box scaled by k (the axis is a direction)."""
    return {name: _scaled(joint, k) for name, joint in (tpl.get("joints") or {}).items()}

def expand_template(scene: dict[str, Any]) -> dict[str, Any]:
    """{"template": name, "height": mm, "parts": [...]} -> a plain scene.

    The template is scaled to `height` (its base on z = 0, facing -Y). A part of the same name as a
    template part changes it: its keys replace the template's, its primitives are added (in final mm).
    Other parts are added as they are. Scene-level keys (blend, detail, fur) stay.
    """
    if "template" not in scene:
        return scene
    tpl = load_template(str(scene["template"]))
    height = float(scene.get("height") or tpl["height"])
    k = height / float(tpl["height"])
    pose = scene.get("pose") or {}
    joints = _joints(tpl, k)
    for name in pose:
        if name not in joints:
            raise ValueError(f"unknown joint '{name}' for {scene['template']} (joints: {', '.join(joints) or 'none'})")
    if pose:            # `height` is the finished figure's height: a raised arm must not grow past it
        probe = [{**p, "primitives": expand_macros(p.get("primitives") or [])} for p in
                 (_scaled(p, k) for p in tpl["parts"])]            # hair as strands, not its loose bound
        crown = _part_bounds(probe)[0][1][2]
        for name, angle in pose.items():
            _pose_parts(probe, joints[name], float(angle))
        top = _part_bounds(probe)[0][1][2]
        if top > crown + 1e-6:
            k *= crown / top
            joints = _joints(tpl, k)
    parts = [_scaled(p, k) for p in tpl["parts"]]
    by_name = {p["name"]: p for p in parts}
    for extra in scene.get("parts") or []:
        target = by_name.get(extra.get("name"))
        if target is None:
            parts.append(extra)
            continue
        for key, v in extra.items():
            if key == "primitives":
                target["primitives"] = target["primitives"] + list(v)
            else:
                target[key] = v
    for name, angle in pose.items():
        _pose_parts(parts, joints[name], float(angle))
    rest = {key: v for key, v in scene.items() if key not in ("template", "height", "parts", "pose")}
    return {**({"cells": tpl["cells"]} if "cells" in tpl else {}), "template": scene["template"], **rest,
            "parts": parts}


def _axis_angle(axis: Any, deg: float) -> np.ndarray:
    u = np.array([float(x) for x in axis])
    u /= np.linalg.norm(u)
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    x, y, z = u
    return np.array([[c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
                     [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
                     [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)]])


def _euler(rot: np.ndarray) -> list[float]:
    """Angles [rx, ry, rz] in degrees for which _rotation gives `rot` (R = Rz Ry Rx)."""
    ry = math.asin(max(-1.0, min(1.0, -rot[2, 0])))
    if abs(math.cos(ry)) > 1e-6:
        rx, rz = math.atan2(rot[2, 1], rot[2, 2]), math.atan2(rot[1, 0], rot[0, 0])
    else:
        rx, rz = math.atan2(-rot[1, 2], rot[1, 1]), 0.0
    return [round(math.degrees(a), 4) for a in (rx, ry, rz)]


def _reference(prim: dict[str, Any]) -> np.ndarray | None:
    for key in ("center", "a"):
        if key in prim:
            return np.array([float(x) for x in prim[key]])
    if prim.get("points"):
        return np.array([float(x) for x in prim["points"][0]])
    return None


def _pose_parts(parts: list[dict[str, Any]], joint: dict[str, Any], deg: float) -> None:
    """Turn every primitive whose reference point lies in the joint's box about the joint's pivot and axis."""
    rot = _axis_angle(joint["axis"], deg)
    pivot = np.array([float(x) for x in joint["pivot"]])
    lo, hi = (np.array([float(x) for x in b]) for b in joint["box"])

    def turn(point: Any) -> list[float]:
        q = pivot + rot @ (np.array([float(x) for x in point]) - pivot)
        return [float(x) for x in q]

    for part in parts:
        for prim in part.get("primitives") or []:
            ref = _reference(prim)
            if ref is None or not (np.all(ref >= lo) and np.all(ref <= hi)):
                continue
            for key in ("center", "a", "b"):
                if key in prim:
                    prim[key] = turn(prim[key])
            if prim.get("points"):
                prim["points"] = [turn(q) for q in prim["points"]]
            if prim.get("type") in ("ellipsoid", "box", "torus"):
                old = _rotation(prim.get("rotate"))
                prim["rotate"] = _euler(rot @ old if old is not None else rot)


def _scene_bounds(parts: list[dict[str, Any]]) -> list[tuple[float, float, float]]:
    boxes = [_bounds(p) for part in parts for p in part.get("primitives") or [] if p.get("op", "add") == "add"]
    if not boxes:
        raise ValueError("a part needs at least one primitive")
    lo = np.min([b[0] for b in boxes], axis=0)
    hi = np.max([b[1] for b in boxes], axis=0)
    return [tuple(lo), tuple(hi)]


def sculpt_scene(scene: dict[str, Any], out_dir: str, voxel: float | None = None, smooth: int = 4) -> dict[str, Any]:
    """Mesh every part of a scene into out_dir/<part>.stl. A scene is {"parts": [...]}, one part, or a template.

    All parts share one grid size, chosen from the whole scene, so small parts (eyes, shoes) are not
    meshed finer than the body and large ones not coarser.
    """
    t0 = time.monotonic()
    scene = expand_template(scene)
    parts = scene.get("parts") or [{"name": scene.get("name", "Sculpt"), **scene}]
    parts = [{**p, "primitives": expand_macros(p.get("primitives") or [])} for p in parts]
    if "parts" not in scene:
        scene = {k: v for k, v in scene.items() if k not in ("primitives", "one_piece")}
    for part in parts:
        for p in part.get("primitives") or []:
            _sdf(p, np.zeros((1, 3)))      # explain bad input before the heavy work
    bounds = _scene_bounds(parts)
    if voxel is None and scene.get("cells"):       # a template with small features asks for a finer grid
        voxel = float(max(np.subtract(bounds[1], bounds[0]))) / float(scene["cells"])
    shared = choose_voxel(bounds, voxel)
    os.makedirs(out_dir, exist_ok=True)
    out = []
    lo_all, hi_all = None, None
    merged_parts = []
    for part in parts:
        inherited = {key: scene[key] for key in ("detail", "fur") if key in scene and key not in part}
        merged_parts.append({"blend": scene.get("blend", DEFAULT_BLEND), **inherited, **part})
    jobs: list[tuple[dict[str, Any], Parts]] = [(p, m) for p, m in zip(parts, merged_parts, strict=True)]
    if scene.get("one_piece") and parts:        # one print: every part's primitives in one body, fur and all
        first = parts[0]
        whole = {"name": scene.get("name") or scene.get("template") or first.get("name") or "Sculpt",
                 "color": first.get("color"), "shininess": first.get("shininess", 0.0)}
        jobs = [(whole, merged_parts)]
    for i, (part, merged) in enumerate(jobs):
        verts, tris = mesh_part(merged, part.get("voxel", shared), smooth)
        name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(part.get("name") or f"Part{i + 1}"))
        path = os.path.join(out_dir, f"{name}.stl")
        write_stl(path, verts, tris)
        size = (verts.max(0) - verts.min(0)) if len(verts) else np.zeros(3)
        if len(verts):
            lo_all = verts.min(0) if lo_all is None else np.minimum(lo_all, verts.min(0))
            hi_all = verts.max(0) if hi_all is None else np.maximum(hi_all, verts.max(0))
        n_pieces = pieces(len(verts), tris)
        others = [m for j, (_p, m) in enumerate(jobs) if j != i and isinstance(m, dict)]
        loose = floating_pieces(verts, tris, others, 2 * shared) if n_pieces > 1 else 0
        is_closed = not boundary_loops(tris) and non_manifold_edges(tris) == 0
        entry = {"name": name, "path": path, "triangles": int(len(tris)), "color": part.get("color"),
                 "shininess": float(part.get("shininess", 0.0)),
                 "size_mm": [round(float(x), 2) for x in size], "pieces": n_pieces, "closed": is_closed}
        if loose:
            entry["warning"] = (f"{loose} piece(s) float: they touch neither the rest of the part nor another part "
                                "(a strand in the air, an eyeball inside its socket). Move them into the body.")
        elif not is_closed:
            entry["warning"] = ("the mesh is not closed (a hole too large to fill or sheets that meet at an edge): "
                                "thicken the thin spot or raise the blend there, a slicer cannot print it as it is")
        out.append(entry)
    total = (hi_all - lo_all) if lo_all is not None else np.zeros(3)
    return {"parts": out, "voxel_mm": round(shared, 4), "size_mm": [round(float(x), 2) for x in total],
            "seconds": round(time.monotonic() - t0, 2)}
