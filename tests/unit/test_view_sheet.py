"""The labelled view sheet and cross-section images of inspect_views."""

import io

import numpy as np
import pytest
from PIL import Image

from freecad_bridge import imaging
from freecad_bridge.checks import printing as pr

CAMERA = """#Inventor V2.1 ascii
OrthographicCamera {
  viewportMapping ADJUST_CAMERA
  position 0 -200 0
  orientation 1 0 0  1.5707963
  height 120
}
"""


def png(size=(320, 240), color="white"):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def test_camera_gives_the_scale_and_the_axes_on_screen():
    cam = imaging.parse_camera(CAMERA)
    assert cam["height"] == 120
    assert cam["right"] == pytest.approx([1, 0, 0], abs=1e-6)   # front view: X to the right, Z up
    assert cam["up"] == pytest.approx([0, 0, 1], abs=1e-6)


@pytest.mark.parametrize(("mm", "want"), [(23, 20), (7.5, 5), (1.2, 1), (130, 100), (0.3, 0.2)])
def test_scale_bar_lengths_are_round(mm, want):
    assert imaging.nice_length(mm) == pytest.approx(want)


def test_sheet_is_a_grid_with_marks_on_it():
    shots = [{"label": n, "png": png(), "camera": CAMERA} for n in ("Isometric", "Front", "Right", "Top")]
    im = Image.open(io.BytesIO(imaging.view_sheet(shots, "Size 10 x 20 x 30 mm"))).convert("RGB")
    assert im.size == (640, 480 + 28)
    px = np.asarray(im)
    assert (px < 200).any(), "labels, axes and the scale bar are drawn"


def ring_section():
    """The outline of a 40 x 30 mm box with 2 mm walls, from a real mesh cut."""
    def box(lo, hi, flip=False):
        (x0, y0, z0), (x1, y1, z1) = lo, hi
        v = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                      [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], dtype=float)
        t = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
                      [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
        return v, t[:, ::-1] if flip else t

    (v1, t1), (v2, t2) = box((0, 0, 0), (40, 30, 20)), box((2, 2, 2), (38, 28, 18), True)
    return np.vstack([v1, v2]), np.vstack([t1, t2 + 8])


def test_section_fills_the_walls_and_leaves_the_cavity_empty():
    v, t = ring_section()
    seg = pr.section_segments(v, t, "z", 10.0)
    im = np.asarray(Image.open(io.BytesIO(imaging.section_image([("Box", seg)], "z", 10.0, size=400))).convert("RGB"))
    mid = im[200, 200]
    assert (mid == 255).all(), "the cavity is empty"
    row = im[200]
    assert (row < 240).any(axis=1).sum() > 6, "the walls are filled"


def test_a_section_that_misses_says_so():
    data = imaging.section_image([("Box", np.zeros((0, 2, 2)))], "z", 99.0, size=300)
    assert Image.open(io.BytesIO(data)).size == (300, 300)
