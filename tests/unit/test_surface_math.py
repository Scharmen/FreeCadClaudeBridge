"""Fair body surfaces: smooth fits through feature lines, creases only where asked, no FreeCAD needed."""

import numpy as np
import pytest

from freecad_bridge.parts import surface_math as sm


def wavy_line(y=500.0, noise=6.0, n=18, seed=1):
    x = np.r_[0, 40, 160, np.linspace(400, 3600, n - 6), 3800, 3950, 4000]
    z = 800 + 180 * np.sin(x / 4000 * np.pi)
    rng = np.random.default_rng(seed)
    return np.c_[x, np.full(len(x), y), z + rng.normal(0, noise, len(x))]


def test_a_fit_follows_the_points_and_drops_their_noise():
    line = wavy_line()
    rows, knots = sm.fit_lines([line], 9)
    dense = np.linspace(0, 1, 400)
    u = (line[:, 0] - line[0, 0]) / (line[-1, 0] - line[0, 0])
    assert np.abs(sm.evaluate(rows[0], knots, u) - line).max() < 25
    curve = sm.evaluate(rows[0], knots, dense)
    assert sm.inflections(curve[:, [0, 2]]) < sm.inflections(line[:, [0, 2]])


def test_the_ends_of_a_line_are_kept_exactly():
    line = wavy_line()
    rows, knots = sm.fit_lines([line], 9)
    ends = sm.evaluate(rows[0], knots, np.array([0.0, 1.0]))
    assert np.allclose(ends[0], line[0]) and np.allclose(ends[1], line[-1])


def test_lines_with_different_points_share_one_knot_vector():
    a, b = wavy_line(100), wavy_line(600, n=12, seed=2)
    rows, knots = sm.fit_lines([a, b], 9)
    assert rows.shape == (2, 9, 3)
    assert len(knots) == 9 + 4 and all(p <= q for p, q in zip(knots, knots[1:], strict=False))


def test_few_points_lower_the_number_of_poles():
    short = wavy_line(n=9)[::2]
    rows, _knots = sm.fit_lines([short], 13)
    assert rows.shape[1] == len(short)


def test_a_skin_passes_through_every_line():
    lines = [{"points": wavy_line(y, 0)} for y in (0, 200, 500, 800)]
    patch = sm.patches(lines, 9)[0]
    surface = sm.evaluate_surface(patch, np.array([0.5]), patch["line_params"])
    rows, knots = sm.fit_lines([ln["points"] for ln in lines], 9)
    assert np.allclose(surface[0], [sm.evaluate(r, knots, np.array([0.5]))[0] for r in rows], atol=1e-6)


def test_a_crease_splits_the_skin_along_that_line():
    lines = [{"points": wavy_line(y, 0), "crease": y == 500} for y in (0, 250, 500, 700, 900)]
    a, b = sm.patches(lines, 9)
    assert np.allclose(a["poles"][:, -1], b["poles"][:, 0])


def test_a_cross_section_with_only_two_lines_is_straight():
    patch = sm.patches([{"points": wavy_line(0, 0)}, {"points": wavy_line(900, 0)}], 9)[0]
    assert patch["v_degree"] == 1


@pytest.mark.parametrize(("points", "count"), [
    ([(0, 0), (1, 0.5), (2, 1.5), (3, 3)], 0),       # bends one way
    ([(0, 0), (1, 1), (2, 0), (3, 1), (4, 0), (5, 1)], 3),
])
def test_inflections_counts_changes_of_bending(points, count):
    assert sm.inflections(np.array(points, float), floor=0.0) == count
