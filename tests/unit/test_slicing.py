"""The bridge's own slicer: layers, what each layer rests on, supports, G-code, time and filament.
No slicer program is involved."""

import inspect
import struct

import numpy as np
import pytest

from freecad_bridge import slicing

QUADS = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]   # outward
BED = [250.0, 250.0, 250.0]


def hexa(corners, inward=False):
    c = np.array(corners, float)
    tris = []
    for q in QUADS:
        tris += [[c[q[0]], c[q[1]], c[q[2]]], [c[q[0]], c[q[2]], c[q[3]]]]
    out = np.array(tris)
    return out[:, ::-1] if inward else out


def box(x, y, z, sx, sy, sz, inward=False):
    unit = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
    return hexa([(x + a * sx, y + b * sy, z + c * sz) for a, b, c in unit], inward)


def frustum(bottom, top, height):
    """A square block whose half width goes from `bottom` to `top` over `height`."""
    lo = [(-bottom, -bottom, 0), (bottom, -bottom, 0), (bottom, bottom, 0), (-bottom, bottom, 0)]
    hi = [(-top, -top, height), (top, -top, height), (top, top, height), (-top, top, height)]
    return hexa(lo + hi)


def run(tris, tmp_path=None, **kw):
    path = str(tmp_path / "out.gcode") if tmp_path else None
    return slicing.slice_model(tris, BED, gcode_path=path, **kw)


def kinds(result):
    return [f["kind"] for f in result["findings"]]


def test_no_slicer_program_is_started_or_looked_for():
    source = inspect.getsource(slicing)
    assert "subprocess" not in source and "OrcaSlicer" not in source and "Applications" not in source


def test_stl_files_are_read_in_both_forms(tmp_path):
    tris = box(0, 0, 0, 2, 3, 4)
    text = "solid t\n" + "".join(
        "facet normal 0 0 0\nouter loop\n" + "".join(f"vertex {p[0]} {p[1]} {p[2]}\n" for p in t)
        + "endloop\nendfacet\n" for t in tris) + "endsolid t\n"
    (tmp_path / "a.stl").write_text(text)
    raw = b"\0" * 80 + struct.pack("<I", len(tris))
    for t in tris:
        raw += struct.pack("<12fH", 0, 0, 0, *t.reshape(-1), 0)
    (tmp_path / "b.stl").write_bytes(raw)
    for name in ("a.stl", "b.stl"):
        got = slicing.read_stl(str(tmp_path / name))
        assert got.shape == (12, 3, 3) and np.allclose(got, tris)


def test_a_plain_block_prints_and_its_numbers_are_plausible(tmp_path):
    r = run(box(5, 5, 3, 20, 20, 10), tmp_path)
    assert r["printable_without_supports"] and not r["problems"] and not r["findings"], r
    assert r["layers"] == 50 and r["height_mm"] == 10.0
    assert 380 <= r["bed_contact_mm2"] <= 420
    solid_g = 20 * 20 * 10 * 1.24 / 1000
    assert 0.25 * solid_g < r["filament_g"] < solid_g, "walls, top, bottom and sparse infill, not a full block"
    assert 3 < r["minutes"] < 60
    assert "with_supports" not in r


def test_the_gcode_is_a_real_print_file_centred_on_the_bed(tmp_path):
    r = run(box(5, 5, 3, 20, 20, 10), tmp_path)
    text = open(r["gcode"], encoding="utf-8").read()
    assert text.count(";LAYER_CHANGE") == 50
    for must in ("M104 S210", "M140 S60", "G28", "M83", ";TYPE:Outer wall", ";TYPE:Inner wall",
                 ";TYPE:Solid infill", ";TYPE:Sparse infill", "M84"):
        assert must in text, must
    g = slicing.read_gcode(text)
    assert 114.5 <= g["x"][0] and g["x"][1] <= 135.5 and 114.5 <= g["y"][0] and g["y"][1] <= 135.5, g
    assert g["z"][0] == pytest.approx(0.2) and g["z"][1] == pytest.approx(10.0)
    assert g["mm"]["Outer wall"] == pytest.approx(50 * 4 * 19.6, rel=0.05), "one loop per layer, half a line inside"
    assert g["filament_g"] == pytest.approx(r["filament_g"], rel=0.02), "the report and the file agree"
    assert "Support" not in g["mm"]


def test_a_part_that_starts_in_mid_air_is_a_problem():
    r = run(np.concatenate([box(0, 0, 0, 10, 10, 5), box(30, 0, 10, 10, 10, 5)]))
    assert not r["printable_without_supports"]
    floating = [f for f in r["findings"] if f["kind"] == "floating"]
    assert len(floating) == 1 and floating[0]["z"] == pytest.approx(10.0) and 90 <= floating[0]["area_mm2"] <= 110
    assert floating[0]["at"] == [pytest.approx(35, abs=0.5), pytest.approx(5, abs=0.5)]
    assert "mid air" in r["problems"][0]
    assert r["with_supports"]["extra_g"] > 0 and not r["with_supports"]["on_model"]


def test_an_arm_that_hangs_free_needs_supports_and_the_file_has_them(tmp_path):
    tee = np.concatenate([box(0, 0, 0, 10, 10, 20), box(-20, 0, 20, 50, 10, 3)])
    r = run(tee, tmp_path)
    arms = [f for f in r["findings"] if f["kind"] == "cantilever"]
    assert len(arms) == 2 and all(f["mm"] == pytest.approx(20, abs=1) and f["z"] == pytest.approx(20) for f in arms), r
    assert not r["printable_without_supports"] and "hang free" in r["problems"][0]
    assert r["with_supports"]["extra_g"] > 0 and r["with_supports"]["extra_minutes"] > 0
    assert r["gcode"].endswith("_with_supports.gcode")
    g = slicing.read_gcode(open(r["gcode"], encoding="utf-8").read())
    assert g["mm"]["Support"] > 100


def test_a_short_bridge_is_fine_and_a_long_one_sags():
    def gate(gap):
        return np.concatenate([box(0, 0, 0, 5, 10, 10), box(5 + gap, 0, 0, 5, 10, 10), box(0, 0, 10, 10 + gap, 10, 2)])

    short = run(gate(8))
    assert short["printable_without_supports"] and not short["warnings"], short
    assert short["bridges"] == {"count": 1, "longest_mm": pytest.approx(8, abs=0.8)}
    long = run(gate(30))
    spans = [f for f in long["findings"] if f["kind"] == "bridge"]
    assert len(spans) == 1 and spans[0]["mm"] == pytest.approx(30, abs=1)
    assert any("bridge" in w and "sag" in w for w in long["warnings"]), long


def test_slopes_up_to_45_degrees_print_and_steeper_ones_are_named():
    gentle = run(frustum(5, 5 + 10 * np.tan(np.radians(40)), 10))
    assert gentle["printable_without_supports"] and not gentle["warnings"] and not gentle["findings"], gentle
    steep = run(frustum(5, 5 + 10 * np.tan(np.radians(75)), 10))
    assert any("steeper" in w for w in steep["warnings"]), steep
    assert steep["steep_overhang_mm2"] > 100


def test_a_closed_hollow_is_left_empty_and_its_roof_is_a_bridge():
    full = run(box(0, 0, 0, 20, 20, 20))
    hollow = run(np.concatenate([box(0, 0, 0, 20, 20, 20), box(2, 2, 2, 16, 16, 16, inward=True)]))
    assert hollow["path_mm"]["Sparse infill"] < 0.5 * full["path_mm"]["Sparse infill"], "no infill in the hollow"
    assert not hollow["problems"] and not any("not closed" in w for w in hollow["warnings"])
    roof = [f for f in hollow["findings"] if f["kind"] == "bridge"]
    assert len(roof) == 1 and roof[0]["mm"] == pytest.approx(16, abs=1.2) and roof[0]["z"] == pytest.approx(18)


def test_a_wall_thinner_than_the_nozzle_is_not_printed_and_that_is_said():
    r = run(np.concatenate([box(0, 0, 0, 20, 20, 2), box(5, 5, 2, 10, 0.2, 10)]))
    assert any("thinner than the nozzle" in w for w in r["warnings"]), r


def test_a_model_larger_than_the_bed_is_a_problem():
    r = slicing.slice_model(box(0, 0, 0, 200, 30, 5), [180.0, 180.0, 180.0])
    assert any("does not fit" in p for p in r["problems"]) and not r["printable_without_supports"]


def test_an_open_mesh_is_reported_instead_of_filled_wrongly():
    r = run(np.delete(box(0, 0, 0, 10, 10, 10), [6, 7], axis=0))      # one side wall is missing
    assert any("not closed" in w for w in r["warnings"]), r


def test_big_models_are_sliced_coarser_instead_of_running_out_of_memory():
    assert slicing.resolution([20, 20, 10], 0.4, 0.2) == 0.2
    coarse = slicing.resolution([240, 240, 240], 0.4, 0.2)
    assert 0.2 < coarse <= 0.5


def test_printers_are_found_by_a_loose_name_and_resin_is_refused():
    assert slicing.printer("kobra s1") == ("Anycubic Kobra S1", [250, 250, 250])
    assert slicing.printer("Prusa MK4")[1] == [250, 210, 220]
    with pytest.raises(ValueError, match="Anycubic Kobra S1"):
        slicing.printer("Nothing 9000")
    with pytest.raises(ValueError, match="resin"):
        slicing.printer("Elegoo Mars")


def test_materials_set_temperatures_and_weight(tmp_path):
    r = run(box(0, 0, 0, 10, 10, 2), tmp_path, material="petg")
    text = open(r["gcode"], encoding="utf-8").read()
    assert "M104 S240" in text and "M140 S75" in text
    with pytest.raises(ValueError, match="PLA"):
        run(box(0, 0, 0, 10, 10, 2), material="wood")
