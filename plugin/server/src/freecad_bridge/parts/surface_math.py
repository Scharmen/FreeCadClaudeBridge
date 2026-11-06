"""Fair body surfaces as numbers: a smooth fit through feature lines, skinned across them. Needs only numpy.

Interpolating every point copies each small error of the points into the surface, which shows as ripples
in reflections. A least-squares fit with few poles cannot follow such errors; all lines share one knot
vector, so that they can be skinned into one surface.
"""

from __future__ import annotations

from typing import Any

import numpy as np

DEGREE = 3


def basis(knots: np.ndarray, degree: int, u: np.ndarray) -> np.ndarray:
    """B-spline basis values: one row per parameter, one column per pole (Cox-de Boor)."""
    knots, u = np.asarray(knots, float), np.asarray(u, float)
    n = len(knots) - degree - 1
    b = np.zeros((len(u), len(knots) - 1))
    for i in range(len(knots) - 1):
        b[:, i] = (u >= knots[i]) & (u < knots[i + 1])
    last = np.where(knots[:-1] < knots[1:])[0][-1]
    b[u >= knots[-1], last] = 1
    for d in range(1, degree + 1):
        c = np.zeros((len(u), len(knots) - 1 - d))
        for i in range(c.shape[1]):
            left, right = knots[i + d] - knots[i], knots[i + d + 1] - knots[i + 1]
            if left > 0:
                c[:, i] += (u - knots[i]) / left * b[:, i]
            if right > 0:
                c[:, i] += (knots[i + d + 1] - u) / right * b[:, i + 1]
        b = c
    return b[:, :n]


def _params(line: np.ndarray, x0: float, x1: float) -> np.ndarray:
    return (line[:, 0] - x0) / (x1 - x0)


def fit_lines(lines: list[Any], poles: int = 13) -> tuple[np.ndarray, np.ndarray]:
    """One cubic per line, all on one knot vector placed where the points are dense (nose and tail).

    The ends stay exactly on the first and last point. Returns (lines x poles x 3 array, knots on 0..1).
    """
    arrays = [np.asarray(line, float) for line in lines]
    poles = max(DEGREE + 1, min(poles, *(len(a) for a in arrays))) if len(arrays) > 1 else \
        max(DEGREE + 1, min(poles, len(arrays[0])))
    x0, x1 = arrays[0][0, 0], arrays[0][-1, 0]
    every = np.sort(np.concatenate([_params(a, x0, x1) for a in arrays]))
    inner = poles - DEGREE - 1
    middle = np.quantile(every, (np.arange(inner) + 1) / (inner + 1)) if inner > 0 else np.array([])
    knots = np.r_[[0.0] * (DEGREE + 1), middle, [1.0] * (DEGREE + 1)]
    rows = np.zeros((len(arrays), poles, 3))
    for k, a in enumerate(arrays):
        b = basis(knots, DEGREE, _params(a, x0, x1))
        rows[k, 0], rows[k, -1] = a[0], a[-1]
        if poles > 2:
            rest = a - b[:, [0]] * a[0] - b[:, [-1]] * a[-1]
            rows[k, 1:-1] = np.linalg.lstsq(b[:, 1:-1], rest, rcond=None)[0]
    return rows, knots


def evaluate(poles: np.ndarray, knots: np.ndarray, u: np.ndarray, degree: int = DEGREE) -> np.ndarray:
    return basis(knots, degree, u) @ poles


def _skin(rows: np.ndarray) -> dict[str, Any]:
    """Interpolate compatible lines across, cubic where there are enough, spaced by their mean distance."""
    gaps = np.linalg.norm(np.diff(rows, axis=0), axis=2).mean(axis=1)
    params = np.r_[0.0, np.cumsum(gaps)] / max(float(gaps.sum()), 1e-12)
    degree = min(DEGREE, len(rows) - 1)
    inner = [float(params[i + 1:i + degree + 1].mean()) for i in range(len(rows) - degree - 1)]
    knots = np.r_[[0.0] * (degree + 1), inner, [1.0] * (degree + 1)]
    across = np.linalg.solve(basis(knots, degree, params), rows.reshape(len(rows), -1)).reshape(rows.shape)
    return {"poles": np.swapaxes(across, 0, 1), "v_knots": knots, "v_degree": degree, "line_params": params}


def patches(lines: list[dict[str, Any]], poles: int = 13) -> list[dict[str, Any]]:
    """Surfaces between creases. A patch is {poles (u x v x 3), u_knots, v_knots, u_degree, v_degree, line_params}."""
    rows, knots = fit_lines([line["points"] for line in lines], poles)
    cuts = [0, *(i for i in range(1, len(lines) - 1) if lines[i].get("crease")), len(lines) - 1]
    return [{**_skin(rows[a:b + 1]), "u_knots": knots, "u_degree": DEGREE}
            for a, b in zip(cuts, cuts[1:], strict=False)]


def evaluate_surface(patch: dict[str, Any], u: np.ndarray, v: np.ndarray) -> np.ndarray:
    bu = basis(patch["u_knots"], patch["u_degree"], u)
    bv = basis(patch["v_knots"], patch["v_degree"], v)
    return np.einsum("iu,jv,uvk->ijk", bu, bv, patch["poles"])


def knots_and_mults(knots: np.ndarray) -> tuple[list[float], list[int]]:
    values: list[float] = []
    mults: list[int] = []
    for k in knots:
        if values and abs(values[-1] - k) < 1e-12:
            mults[-1] += 1
        else:
            values.append(float(k))
            mults.append(1)
    return values, mults


def inflections(points: np.ndarray, floor: float = 0.002) -> int:
    """How often a 2D line changes its bending. Turns below `floor` (radians per step) count as straight."""
    d = np.diff(np.asarray(points, float), axis=0)
    d = d[np.linalg.norm(d, axis=1) > 1e-9]
    turn = np.diff(np.unwrap(np.arctan2(d[:, 1], d[:, 0])))
    turn = turn[np.abs(turn) > floor]
    return int((np.sign(turn[1:]) != np.sign(turn[:-1])).sum())
