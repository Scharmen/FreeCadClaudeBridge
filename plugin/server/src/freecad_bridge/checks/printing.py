"""Can it be printed? Analysis of a closed triangle mesh, for CAD solids and sculpted meshes alike.

Pure NumPy: the mesh comes from a tessellation made in freecadcmd. Every finding says what is wrong
(`cause`), where (`where`, a point in mm) and how to fix it (`fix`). `problems` must be fixed;
`warnings` are worth knowing (supports, bridges) and depend on how the part is printed.
"""

from __future__ import annotations

from typing import Any

import numpy as np

# Rules of thumb, sources in docs/designer-plan.md; printers differ, so they are limits to check against.
PROCESSES = {
    "fdm": {"min_wall": 1.2, "overhang_deg": 45.0, "bridge_mm": 10.0},
    "resin": {"min_wall": 0.8, "overhang_deg": 30.0, "bridge_mm": 5.0},
    "cnc": {"min_wall": 0.8, "overhang_deg": 90.0, "bridge_mm": 1e9},
}
FITS = {   # gap between the parts in mm: press fits may touch, the others need room
    "fdm": {"press": (0.0, 0.1), "sliding": (0.15, 0.35), "loose": (0.3, 0.7)},
    "resin": {"press": (0.0, 0.05), "sliding": (0.05, 0.2), "loose": (0.2, 0.4)},
    "cnc": {"press": (0.0, 0.01), "sliding": (0.01, 0.06), "loose": (0.06, 0.3)},
}
BED_MM = 0.2            # faces this close to the lowest point lie on the bed
THIN_SHARE = 0.01       # thin area below this share of the surface is a tip or a crest, not a wall
MIN_BASE_MM2 = 10.0
UPS = {"+Z": (0, 0, 1), "-Z": (0, 0, -1), "+X": (1, 0, 0), "-X": (-1, 0, 0), "+Y": (0, 1, 0), "-Y": (0, -1, 0)}


def r2(x: float) -> float:
    return round(float(x), 2)


def _point(p: Any) -> list[float]:
    return [r2(c) for c in p]


def merge(verts: np.ndarray, tris: np.ndarray, digits: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """Identical points become one vertex (a tessellation repeats them along face borders)."""
    uniq, inverse = np.unique(np.round(verts, digits), axis=0, return_inverse=True)
    return uniq, inverse.reshape(-1)[tris]


def _normals_areas(v: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    length = np.linalg.norm(n, axis=1)
    return n / np.maximum(length, 1e-12)[:, None], length / 2


def is_closed(t: np.ndarray) -> bool:
    edges = np.sort(np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]]), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return bool((counts == 2).all())


def wall_thickness(v: np.ndarray, t: np.ndarray, samples: int = 1500, limit: float | None = None,
                   seed: int = 1) -> dict[str, Any]:
    """Shoot rays inward from points on the surface; the first exit is the local wall thickness."""
    normals, areas = _normals_areas(v, t)
    good = np.nonzero(areas > 1e-9)[0]
    if not len(good):
        return {"min_mm": None, "thin_at": None, "thin_share": 0.0, "samples": 0}
    rng = np.random.default_rng(seed)
    picks = rng.choice(good, size=samples, p=areas[good] / areas[good].sum())
    r1, r2_ = rng.random(samples), rng.random(samples)
    flip = r1 + r2_ > 1
    r1[flip], r2_[flip] = 1 - r1[flip], 1 - r2_[flip]
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    origins = a[picks] + r1[:, None] * (b - a)[picks] + r2_[:, None] * (c - a)[picks]
    dirs = -normals[picks]
    centre = (a + b + c) / 3
    radius = np.sqrt(np.maximum(((a - centre) ** 2).sum(1), ((b - centre) ** 2).sum(1)))
    radius = np.maximum(radius, np.sqrt(((c - centre) ** 2).sum(1))) + 1e-6
    e1, e2 = b - a, c - a
    depth = np.full(samples, np.inf)
    for i in range(samples):
        o, d = origins[i], dirs[i]
        w = centre - o
        along = w @ d
        cand = np.nonzero((along > -radius) & ((w * w).sum(1) - along * along < radius * radius))[0]
        if not len(cand):
            continue
        h = np.cross(d, e2[cand])                # Möller-Trumbore on the triangles near the ray
        det = (e1[cand] * h).sum(1)
        ok = np.abs(det) > 1e-12
        cand, h, det = cand[ok], h[ok], det[ok]
        s = o - a[cand]
        u = (s * h).sum(1) / det
        q = np.cross(s, e1[cand])
        w2 = (q @ d) / det
        dist = (e2[cand] * q).sum(1) / det
        hit = (u >= 0) & (w2 >= 0) & (u + w2 <= 1) & (dist > 1e-3)
        if hit.any():
            depth[i] = dist[hit].min()
    found = np.isfinite(depth)
    if not found.any():
        return {"min_mm": None, "thin_at": None, "thin_share": 0.0, "samples": samples}
    i = int(np.argmin(np.where(found, depth, np.inf)))
    out = {"min_mm": r2(depth[i]), "median_mm": r2(np.median(depth[found])), "thin_at": _point(origins[i]),
           "thin_share": 0.0, "samples": samples}
    if limit is not None:
        out["thin_share"] = round(float((depth[found] < limit).mean()), 4)
        # report the thinnest wall that is more than a tip: the percentile above the allowed thin share
        wall = float(np.quantile(depth[found], THIN_SHARE))
        out["wall_mm"] = r2(wall)
    return out


def _clusters(t: np.ndarray, faces: np.ndarray) -> list[np.ndarray]:
    """Groups of the given faces that share an edge."""
    if not len(faces):
        return []
    parent = list(range(len(faces)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    seen: dict[tuple[int, int], int] = {}
    for i, f in enumerate(faces):
        a, b, c = (int(x) for x in t[f])
        for e in ((a, b), (b, c), (c, a)):
            key = (min(e), max(e))
            if key in seen:
                parent[find(i)] = find(seen[key])
            else:
                seen[key] = i
    groups: dict[int, list[int]] = {}
    for i in range(len(faces)):
        groups.setdefault(find(i), []).append(i)
    return [faces[np.array(g)] for g in groups.values()]


def orientation(v: np.ndarray, t: np.ndarray, up: Any, overhang_deg: float) -> dict[str, Any]:
    """What printing with `up` pointing upwards would need."""
    up = np.asarray(up, dtype=float)
    normals, areas = _normals_areas(v, t)
    heights = v @ up
    low = heights.min()
    tri_h = heights[t]
    down = normals @ -up
    on_bed = (tri_h.max(1) <= low + BED_MM) & (down > 0.9)
    limit = np.cos(np.radians(overhang_deg))      # angle between the face's normal and straight down
    over = (down > limit) & ~on_bed & (areas > 0)
    return {"overhang_mm2": r2(areas[over].sum()), "base_mm2": r2(areas[on_bed].sum()),
            "height_mm": r2(heights.max() - low), "_over": np.nonzero(over)[0], "_flat": np.nonzero(
                (down > 0.99) & ~on_bed)[0], "_bed": on_bed}


def centre_of_mass(v: np.ndarray, t: np.ndarray) -> np.ndarray:
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    vol = (a * np.cross(b, c)).sum(1) / 6
    if abs(vol.sum()) < 1e-9:
        return v.mean(0)
    return ((a + b + c) / 4 * vol[:, None]).sum(0) / vol.sum()


def _inside_hull(point: np.ndarray, points: np.ndarray) -> bool:
    """Is a 2D point inside the convex hull of points? (monotone chain)"""
    pts = sorted({(round(float(x), 4), round(float(y), 4)) for x, y in points})
    if len(pts) < 3:
        return False

    def half(seq: Any) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for p in seq:
            while len(out) >= 2 and ((out[-1][0] - out[-2][0]) * (p[1] - out[-2][1])
                                     - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0])) <= 0:
                out.pop()
            out.append(p)
        return out[:-1]

    hull = half(pts) + half(reversed(pts))
    for i, a in enumerate(hull):
        b = hull[(i + 1) % len(hull)]
        if (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0]) < 0:
            return False
    return True


def _bridge_span(v: np.ndarray, t: np.ndarray, faces: np.ndarray) -> float:
    """How far a flat underside must span between the walls that hold it (shorter way, as a slicer would)."""
    pts = v[t[faces]].reshape(-1, 3)
    lo, hi = pts.min(0), pts.max(0)
    inner = set(faces.tolist())
    around: dict[tuple[int, int], list[int]] = {}
    for f in np.nonzero(np.isin(t, np.unique(t[faces])).any(axis=1))[0]:
        a, b, c = (int(x) for x in t[f])
        for e in ((a, b), (b, c), (c, a)):
            around.setdefault((min(e), max(e)), []).append(int(f))
    held = {(axis, side): False for axis in (0, 1) for side in (0, 1)}
    for (a, b), tris in around.items():
        here = [f for f in tris if f in inner]
        if len(here) != 1:
            continue
        for f in tris:
            if f in inner:
                continue
            third = v[[x for x in t[f] if x not in (a, b)][0]]
            if third[2] >= v[a][2] - 1e-6:       # this neighbour rises: a free edge, not a wall below
                continue
            mid = (v[a] + v[b]) / 2
            for axis in (0, 1):
                if abs(v[a][axis] - v[b][axis]) < 1e-6:
                    if abs(mid[axis] - lo[axis]) < 1e-6:
                        held[(axis, 0)] = True
                    if abs(mid[axis] - hi[axis]) < 1e-6:
                        held[(axis, 1)] = True
    spans = [float(hi[a] - lo[a]) for a in (0, 1) if held[(a, 0)] and held[(a, 1)]]
    return min(spans) if spans else float(min(hi[0] - lo[0], hi[1] - lo[1]))


def analyze(v: np.ndarray, t: np.ndarray, process: str = "fdm", min_wall: float | None = None,
            samples: int | None = None, each_part: bool = True) -> dict[str, Any]:
    """`each_part=False`: several overlapping parts as one print; walls and closure are checked per part."""
    if process not in PROCESSES:
        raise ValueError(f"unknown process '{process}' (use {', '.join(PROCESSES)})")
    rules = PROCESSES[process]
    wall_limit = float(min_wall if min_wall is not None else rules["min_wall"])
    v, t = merge(v, t)
    problems: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    closed = is_closed(t) if each_part else True
    if not closed:
        problems.append({"cause": "the mesh is not closed: a slicer cannot tell inside from outside",
                         "where": _point(v.mean(0)),
                         "fix": "make one valid solid (or use `sculpt` for organic forms) and remove zero-thickness "
                         "faces"})
    n_samples = samples or (1500 if len(t) < 200_000 else 800)
    wall = wall_thickness(v, t, n_samples, wall_limit) if each_part else {}
    if closed and wall.get("wall_mm") is not None and wall["wall_mm"] < wall_limit:
        problems.append({"cause": f"walls about {wall['wall_mm']} mm thick ({wall['thin_share']:.0%} of the surface), "
                         f"below the {wall_limit:g} mm that {process} needs", "where": wall["thin_at"],
                         "fix": f"thicken it to at least {wall_limit:g} mm"})
    all_up = {name: orientation(v, t, up, rules["overhang_deg"]) for name, up in UPS.items()}
    now = all_up["+Z"]
    if now["overhang_mm2"] > 0:
        groups = _clusters(t, now["_over"])
        _, areas = _normals_areas(v, t)
        big = max(groups, key=lambda g: areas[g].sum())
        warnings.append({"cause": f"{now['overhang_mm2']} mm² hang over more than {rules['overhang_deg']:g}° and need "
                         "support", "where": _point(v[t[big]].mean((0, 1))),
                         "fix": "lean the shape to 45° or less, add a chamfer, or print it in another orientation"})
    longest = 0.0
    longest_at = None
    for g in _clusters(t, now["_flat"]):
        span = _bridge_span(v, t, g)
        if span > longest:
            longest, longest_at = span, v[t[g]].mean((0, 1))
    if longest > rules["bridge_mm"]:
        warnings.append({"cause": f"a bridge of {longest:.1f} mm, longer than the {rules['bridge_mm']:g} mm that "
                         f"{process} spans cleanly", "where": _point(longest_at),
                         "fix": "add a support rib, an arch or a chamfer, or split the span"})
    com = centre_of_mass(v, t)
    bed_pts = v[t[now["_bed"]]].reshape(-1, 3)[:, :2]
    stable = bool(len(bed_pts)) and _inside_hull(com[:2], bed_pts)
    if now["base_mm2"] < MIN_BASE_MM2:
        problems.append({"cause": f"only {now['base_mm2']} mm² touch the bed: it will come loose while printing",
                         "where": _point([com[0], com[1], v[:, 2].min()]),
                         "fix": "add a flat base or side of at least 10 mm², or print it in another orientation"})
    elif not stable:
        problems.append({"cause": "it would tip over: the centre of mass is outside the footprint on the bed",
                         "where": _point(com), "fix": "widen the base, or print it lying on a larger side"})
    best = min(all_up, key=lambda k: (all_up[k]["overhang_mm2"], -all_up[k]["base_mm2"]))
    values = {"process": process, "closed": closed, "min_wall_mm": wall.get("min_mm"), "wall_mm": wall.get("wall_mm"),
              "overhang_mm2": now["overhang_mm2"], "base_mm2": now["base_mm2"], "height_mm": now["height_mm"],
              "stable": stable, "centre_of_mass": _point(com), "best_up": best,
              "orientations": {k: {f: u[f] for f in ("overhang_mm2", "base_mm2", "height_mm")}
                               for k, u in all_up.items()}}
    return {"ok": not problems, "problems": problems, "warnings": warnings, "values": values}


def fit_verdict(gap: float, overlap: float, kind: str, process: str = "fdm",
                gap_range: list[float] | None = None) -> dict[str, Any]:
    """Does the gap between two parts suit the fit? `overlap` is the shared volume in mm³."""
    if gap_range is not None:
        lo, hi = float(gap_range[0]), float(gap_range[1])
    else:
        table = FITS.get(process)
        if table is None:
            raise ValueError(f"unknown process '{process}' (use {', '.join(FITS)})")
        if kind not in table:
            raise ValueError(f"unknown fit '{kind}' (use {', '.join(table)}, or give gap_range)")
        lo, hi = table[kind]
    wanted = f"{lo:g}–{hi:g} mm"
    out: dict[str, Any] = {"gap_mm": r2(gap), "overlap_mm3": r2(overlap), "wanted_gap_mm": [lo, hi], "ok": True}
    if overlap > 0.5 and kind != "press":
        out["ok"] = False
        out["problem"] = {"cause": f"the parts overlap by {overlap:.1f} mm³",
                          "fix": f"move or shrink them to a gap of {wanted}"}
    elif not lo <= gap <= hi:
        out["ok"] = False
        out["problem"] = {"cause": f"the gap is {gap:.2f} mm, a {kind} fit in {process} needs {wanted}",
                          "fix": f"change the mating sizes so that the gap is {wanted}"}
    return out


def analyze_objects(meshes: list[tuple[str, np.ndarray, np.ndarray]], process: str = "fdm",
                    min_wall: float | None = None) -> dict[str, Any]:
    """All objects, grouped into what prints as one piece. The result of one print per group."""
    prints = []
    for group in print_groups([(m[1], m[2]) for m in meshes]):
        labels = [meshes[i][0] for i in group]
        if len(group) == 1:
            _, v, t = meshes[group[0]]
            rep = analyze(v, t, process, min_wall)
        else:
            vs, ts, off = [], [], 0
            for i in group:
                vs.append(meshes[i][1])
                ts.append(meshes[i][2] + off)
                off += len(meshes[i][1])
            rep = analyze(np.vstack(vs), np.vstack(ts), process, min_wall, each_part=False)
            rep["warnings"] = [{**w, "cause": w["cause"] + " (upper limit: hidden faces of overlapping parts count)"}
                               if "support" in w["cause"] else w for w in rep["warnings"]]
            for i in group:
                single = analyze(meshes[i][1], meshes[i][2], process, min_wall)
                for p in single["problems"]:
                    if "wall" in p["cause"] or "closed" in p["cause"]:
                        rep["problems"].append({**p, "cause": f"{meshes[i][0]}: {p['cause']}"})
            rep["ok"] = not rep["problems"]
        prints.append({"objects": labels, **rep})
    return {"ok": all(p["ok"] for p in prints), "prints": prints}


def print_groups(meshes: list[tuple[np.ndarray, np.ndarray]]) -> list[list[int]]:
    """Objects whose boxes overlap print as one piece (a figure's parts); others separately (a lid beside a box)."""
    boxes = [(m[0].min(0), m[0].max(0)) for m in meshes]
    groups: list[list[int]] = []
    for i, (lo, hi) in enumerate(boxes):
        hits = [g for g in groups if any((lo <= boxes[j][1]).all() and (boxes[j][0] <= hi).all() for j in g)]
        merged = [i]
        for g in hits:
            merged = g + merged
            groups.remove(g)
        groups.append(sorted(merged))
    return sorted(groups)


def section_segments(v: np.ndarray, t: np.ndarray, axis: str, offset: float) -> np.ndarray:
    """Where a plane across `axis` cuts the mesh: segments [[a, b], [c, d]] in the two other coordinates."""
    k = "xyz".index(axis)
    keep = [i for i in range(3) if i != k]
    d = v[:, k] - offset
    d = np.where(np.abs(d) < 1e-9, 1e-9, d)     # a vertex on the plane counts as just above it
    tri_d, tri_v = d[t], v[t]
    mask = (tri_d.min(1) < 0) & (tri_d.max(1) > 0)
    segs = []
    for dist, pts in zip(tri_d[mask], tri_v[mask], strict=True):
        cut = []
        for i in range(3):
            j = (i + 1) % 3
            if (dist[i] < 0) != (dist[j] < 0):
                f = dist[i] / (dist[i] - dist[j])
                cut.append((pts[i] + f * (pts[j] - pts[i]))[keep])
        if len(cut) == 2:
            segs.append(cut)
    return np.array(segs).reshape(-1, 2, 2)
