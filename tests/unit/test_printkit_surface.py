"""Fast questions about a triangle mesh: is a point on it, is a point inside. Used instead of the kernel's slow ones."""

import pytest

from claudebridge.printkit.surface import Surface

VERTS = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0), (0, 0, 10), (10, 0, 10), (10, 10, 10), (0, 10, 10)]
TRIS = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6),
        (3, 0, 4), (3, 4, 7)]


@pytest.fixture
def box():
    return Surface(VERTS, TRIS)


@pytest.mark.parametrize(("point", "near"), [((5, 5, 10), True), ((5, 5, 10.04), True), ((5, 5, 10.2), False),
                                             ((5, 5, 5), False), ((0, 5, 5), True), ((10, 10, 10), True),
                                             ((30, 5, 5), False)])
def test_near_the_surface(box, point, near):
    assert box.near(point, 0.05) is near


@pytest.mark.parametrize(("point", "inside"), [((5, 5, 5), True), ((0.5, 0.5, 0.5), True), ((9.9, 5, 9.9), True),
                                               ((5, 5, 11), False), ((5, 5, -1), False), ((11, 5, 5), False),
                                               ((5, 5, 5.0), True), ((2, 2, 5), True)])
def test_inside(box, point, inside):
    assert box.inside(point) is inside


def test_a_hollow_part_is_not_inside_its_cavity():
    inner = [(2 + x * 0.6, 2 + y * 0.6, 2 + z * 0.6) for x, y, z in VERTS]
    tris = TRIS + [(c + 8, b + 8, a + 8) for a, b, c in TRIS]
    hollow = Surface(VERTS + inner, tris)
    assert hollow.inside((1, 1, 1)) is True and hollow.inside((5, 5, 5)) is False


def test_points_for_sampling_are_limited(box):
    assert 0 < len(box.samples(4)) <= 4
