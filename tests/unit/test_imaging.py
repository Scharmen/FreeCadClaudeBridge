"""Image to 3D, server side: outline contours with holes, scaling, alpha, relief grids."""

import pytest
from PIL import Image, ImageDraw

from freecad_bridge import imaging


def ring_png(tmp_path, name="ring.png"):
    img = Image.new("L", (200, 200), 255)
    d = ImageDraw.Draw(img)
    d.ellipse((20, 20, 180, 180), fill=0)          # dark disc on white
    d.ellipse((70, 70, 130, 130), fill=255)        # with a hole
    p = tmp_path / name
    img.save(p)
    return p


def test_outline_finds_outer_and_hole(tmp_path):
    prof = imaging.outline_profile(ring_png(tmp_path), size_mm=40)
    assert len(prof["shapes"]) == 1
    shape = prof["shapes"][0]
    assert len(shape["holes"]) == 1
    xs = [p[0] for p in shape["outer"]]
    assert max(xs) - min(xs) == pytest.approx(40 * 160 / 200, rel=0.05)
    assert prof["width_mm"] == pytest.approx(40, rel=0.01)


def test_outline_scales_by_height_and_y_is_up(tmp_path):
    img = Image.new("L", (100, 200), 255)
    ImageDraw.Draw(img).rectangle((10, 0, 89, 99), fill=0)    # dark block in the TOP half
    p = tmp_path / "top.png"
    img.save(p)
    prof = imaging.outline_profile(p, size_mm=50, fit="height")
    ys = [pt[1] for pt in prof["shapes"][0]["outer"]]
    assert prof["height_mm"] == pytest.approx(50, rel=0.01)
    assert min(ys) > 20, "image top must end up at the top (y up)"


def test_two_blobs_and_noise_removed(tmp_path):
    img = Image.new("L", (300, 100), 255)
    d = ImageDraw.Draw(img)
    d.rectangle((10, 10, 120, 90), fill=0)
    d.ellipse((180, 10, 290, 90), fill=0)
    d.point((150, 50), fill=0)                                 # a speck of noise
    p = tmp_path / "two.png"
    img.save(p)
    prof = imaging.outline_profile(p, size_mm=30)
    assert len(prof["shapes"]) == 2


def test_alpha_channel_wins_and_invert(tmp_path):
    img = Image.new("RGBA", (100, 100), (255, 255, 255, 0))
    ImageDraw.Draw(img).rectangle((25, 25, 74, 74), fill=(255, 255, 255, 255))   # white but opaque
    p = tmp_path / "alpha.png"
    img.save(p)
    prof = imaging.outline_profile(p, size_mm=10)
    xs = [pt[0] for pt in prof["shapes"][0]["outer"]]
    assert max(xs) - min(xs) == pytest.approx(5, rel=0.08)
    light = tmp_path / "light.png"
    Image.new("L", (100, 100), 0).save(light)                  # black everywhere
    dark_inv = imaging.outline_profile(ring_png(tmp_path, "r2.png"), size_mm=40, invert=True)
    assert dark_inv["shapes"], "inverted ring: the hole and the frame become shapes"


def test_simplify_keeps_shape_with_few_points(tmp_path):
    prof = imaging.outline_profile(ring_png(tmp_path), size_mm=40, detail=0.3)
    assert 8 < len(prof["shapes"][0]["outer"]) < 200


def test_relief_grid(tmp_path):
    img = Image.new("L", (400, 100))
    img.putdata([int(255 * (x / 399)) for _y in range(100) for x in range(400)])   # dark left, light right
    p = tmp_path / "grad.png"
    img.save(p)
    g = imaging.height_grid(p, width_mm=80, max_height_mm=3, base_mm=1, resolution=100)
    assert g["nx"] <= 100 and g["ny"] >= 2
    row = g["heights"][: g["nx"]]
    assert row[0] == pytest.approx(4, abs=0.05) and row[-1] == pytest.approx(1, abs=0.05), "dark = high"
    assert g["dx"] * (g["nx"] - 1) == pytest.approx(80, rel=0.01)
    inv = imaging.height_grid(p, width_mm=80, max_height_mm=3, base_mm=1, resolution=100, invert=True)
    assert inv["heights"][0] == pytest.approx(1, abs=0.05)


def test_preview_png_is_small(tmp_path):
    data = imaging.preview_png(ring_png(tmp_path), max_side=64)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(imaging.ImageError):
        imaging.outline_profile(tmp_path / "nope.png", size_mm=10)
