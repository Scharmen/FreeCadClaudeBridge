"""Sculpt organic shapes the way clay or SDF sculpting tools do, outside FreeCAD.

A part is a list of primitives (sphere, ellipsoid, tapered capsule, strand, rounded box, torus,
cylinder) that are added, subtracted or intersected with a smooth blend radius, so limbs grow
out of a body like clay instead of meeting at hard seams. Optional fractal surface detail adds
skin, stone or fur texture. The signed distance field is sampled on a grid, turned into a closed
triangle mesh (surface nets), Taubin-smoothed, and written as binary STL. FreeCAD only imports
the finished file: no geometry kernel call that could crash the user's FreeCAD.

All sizes in mm. NumPy only.
"""

from __future__ import annotations

import math
import os
import time
from typing import Any

import numpy as np

MAX_CELLS = 200            # grid cells along the longest side (about 300 MB peak memory)
MAX_PRIMITIVES = 3000
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


def _bounds(prim: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    kind = prim.get("type")
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
    smallest = longest / MAX_CELLS
    return max(float(requested or 0), smallest, 0.05)


def field(part: dict[str, Any], voxel: float | None = None) -> tuple[np.ndarray, np.ndarray, float]:
    prims = part.get("primitives") or []
    if not prims:
        raise ValueError("a part needs at least one primitive")
    if len(prims) > MAX_PRIMITIVES:
        raise ValueError(f"too many primitives ({len(prims)} > {MAX_PRIMITIVES})")
    blend = float(part.get("blend", DEFAULT_BLEND))
    detail = part.get("detail") or {}
    amp = float(detail.get("amplitude", 0))
    boxes = [_bounds(p) for p in prims]
    for p in prims:                        # validate everything before the heavy work
        _sdf(p, np.zeros((1, 3)))
    lo = np.min([b[0] for b in boxes], axis=0)
    hi = np.max([b[1] for b in boxes], axis=0)
    voxel = choose_voxel([tuple(lo), tuple(hi)], voxel)
    margin = 2 * voxel + max(blend, 0) + abs(amp)
    lo, hi = lo - margin, hi + margin
    n = np.ceil((hi - lo) / voxel).astype(int) + 1
    axes = [lo[i] + np.arange(n[i]) * voxel for i in range(3)]
    far = float(np.linalg.norm(hi - lo))
    grid = np.full(tuple(n), far, dtype=np.float32)
    for prim, (blo, bhi) in zip(prims, boxes, strict=True):
        k = float(prim.get("blend", blend))
        reach = k + voxel * 2
        sl = tuple(slice(max(0, int((blo[i] - reach - lo[i]) // voxel)),
                         min(n[i], int((bhi[i] + reach - lo[i]) // voxel) + 2)) for i in range(3))
        sub_axes = [axes[i][sl[i]] for i in range(3)]
        if min(len(a) for a in sub_axes) == 0:
            continue
        gx, gy, gz = np.meshgrid(*sub_axes, indexing="ij")
        pts = np.stack([gx.ravel(), gy.ravel(), gz.ravel()], axis=1)
        d = _sdf(prim, pts).reshape(gx.shape)
        cur = grid[sl].astype(float)
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
        pts = np.stack([axes[0][idx[0]], axes[1][idx[1]], axes[2][idx[2]]], axis=1)
        grid[idx] -= amp * _value_noise(pts, float(detail.get("scale", 3.0)), int(detail.get("seed", 1)))
    return grid, lo, voxel


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
    cell_id = np.full(active.shape, -1, dtype=np.int64)
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
        new_verts = [verts]
        next_id = len(verts)
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
                centre = verts[tris[group]].reshape(-1, 3).mean(axis=0)
                step = centre - verts[v]
                step *= PINCH_NUDGE / max(float(np.linalg.norm(step)), 1e-12)
                new_verts.append((verts[v] + step)[None, :])
                for t in group:
                    tris[t][tris[t] == v] = next_id
                next_id += 1
        verts = np.concatenate(new_verts)
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


def mesh_part(part: dict[str, Any], voxel: float | None = None, smooth: int = 4) -> tuple[np.ndarray, np.ndarray]:
    grid, origin, voxel = field(part, voxel)
    verts, tris = split_pinches(*surface_nets(grid, origin, voxel))
    return taubin(verts, tris, int(part.get("smooth", smooth))), tris


def pieces(n_verts: int, tris: np.ndarray) -> int:
    """Number of separate pieces (connected components): more than one means something floats."""
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
    used = np.unique(tris)
    return len({find(int(v)) for v in used})


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


def sculpt_scene(scene: dict[str, Any], out_dir: str, voxel: float | None = None, smooth: int = 4) -> dict[str, Any]:
    """Mesh every part of a scene into out_dir/<part>.stl. A scene is {"parts": [...]} or one part."""
    t0 = time.monotonic()
    parts = scene.get("parts") or [{"name": scene.get("name", "Sculpt"), **scene}]
    os.makedirs(out_dir, exist_ok=True)
    out = []
    for i, part in enumerate(parts):
        merged = {"blend": scene.get("blend", DEFAULT_BLEND), **({"detail": scene["detail"]} if "detail" in scene
                                                                else {}), **part}
        verts, tris = mesh_part(merged, part.get("voxel", voxel), smooth)
        name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(part.get("name") or f"Part{i + 1}"))
        path = os.path.join(out_dir, f"{name}.stl")
        write_stl(path, verts, tris)
        size = (verts.max(0) - verts.min(0)) if len(verts) else np.zeros(3)
        n_pieces = pieces(len(verts), tris)
        entry = {"name": name, "path": path, "triangles": int(len(tris)), "color": part.get("color"),
                 "size_mm": [round(float(x), 2) for x in size], "pieces": n_pieces}
        if n_pieces > 1:
            entry["warning"] = (f"{n_pieces} separate pieces: something does not touch the rest (a floating "
                                "strand, a fold sticking out). Move it into the body or remove it.")
        out.append(entry)
    return {"parts": out, "seconds": round(time.monotonic() - t0, 2)}
