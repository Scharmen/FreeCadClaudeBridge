# ruff: noqa: E501
"""Slicing the model with an installed slicer and reading what the printer would do."""

import json
import os
import shutil

import pytest

from freecad_bridge import slicing

LOG = """
[2026-11-10 22:46:11.72] [0x1] [warning] cli mode, Current AnycubicSlicerNext Version 2.0.0.3
[2026-11-10 22:46:11.72] [0x1] [warning] no filament colors found in projects
[2026-11-10 22:46:11.72] [0x1] [error]   calc_exclude_triangles:Unable to create exclude triangles
[2026-11-10 22:46:11.86] [0x1] [warning] plate 1: found NON_CRITICAL slicing warnings: The frame tee.stl seems to have floating cantilever. Please readjust the object orientation or enable support generation.
[2026-11-10 22:46:12.00] [0x1] [warning] plate 1: found slicing warnings: Object can't be printed for empty layer between 5 and 12.2.
"""
GCODE = """; HEADER_BLOCK_START
; total layer number: 3
; max_z_height: 0.60
M83
;TYPE:Outer wall
G1 X0 Y0 F3000
G1 X10 Y0 E0.5
G1 X10 Y10 E0.5
;TYPE:Overhang wall
G1 X0 Y10 E0.5
;TYPE:Bridge
G1 X0 Y0 E0.5
G1 X30 Y40 F6000
;TYPE:Support
G1 X33 Y44 E0.2
; filament used [g] = 2.28
; estimated printing time (normal mode) = 1h 13m 5s
"""


def test_slicer_messages_are_sorted_into_problems_and_warnings_without_noise():
    found = slicing.read_log(LOG)
    assert found["problems"] == ["Object can't be printed for empty layer between 5 and 12.2."]
    assert len(found["warnings"]) == 1 and "floating cantilever" in found["warnings"][0]
    assert "exclude triangles" not in json.dumps(found), "the slicer's own start-up noise is left out"


def test_gcode_says_time_filament_layers_and_how_much_hangs_in_the_air():
    g = slicing.read_gcode(GCODE)
    assert g["layers"] == 3 and g["height_mm"] == 0.6 and g["filament_g"] == 2.28 and g["minutes"] == 73.1
    assert g["mm"] == {"Outer wall": 20.0, "Overhang wall": 10.0, "Bridge": 10.0, "Support": 5.0}
    assert g["overhang_share"] == pytest.approx(10 / 30, abs=0.01), "overhang walls among all outer walls"


def test_profiles_are_found_by_printer_and_their_inheritance_is_resolved(tmp_path):
    root = tmp_path / "profiles" / "Acme"
    for kind in ("machine", "process", "filament"):
        (root / kind).mkdir(parents=True)

    def put(kind, name, **data):
        (root / kind / f"{name}.json").write_text(json.dumps({"name": name, "type": kind, **data}))

    put("machine", "fdm_machine_common", printable_height="200", nozzle_diameter=["0.4"])
    put("machine", "Acme Cube 0.4 nozzle", inherits="fdm_machine_common", printable_height="250")
    put("machine", "Acme Cube 0.6 nozzle", inherits="fdm_machine_common")
    put("process", "0.20mm Standard @Acme Cube 0.4 nozzle", layer_height="0.2")
    put("process", "0.08mm Fine @Acme Cube 0.4 nozzle", layer_height="0.08")
    put("filament", "Generic PETG @Acme Cube 0.4 nozzle")
    put("filament", "Acme PLA @Acme Cube 0.4 nozzle")
    found = slicing.find_profiles(str(tmp_path / "profiles"), "acme cube", nozzle=0.4)
    assert found["machine"]["name"] == "Acme Cube 0.4 nozzle" and found["machine"]["printable_height"] == "250"
    assert found["machine"]["nozzle_diameter"] == ["0.4"] and "inherits" not in found["machine"], "parent filled in"
    assert found["process"]["name"] == "0.20mm Standard @Acme Cube 0.4 nozzle", "0.2 mm standard is the default"
    assert found["filament"]["name"] == "Acme PLA @Acme Cube 0.4 nozzle", "PLA before other materials"
    assert slicing.find_profiles(str(tmp_path / "profiles"), "Unknown 9000", nozzle=0.4) is None


def test_the_verdict_puts_what_cannot_print_first():
    plain = {"log": {"problems": [], "warnings": ["The frame x seems to have floating cantilever."]},
             "gcode": {"layers": 115, "height_mm": 23.0, "filament_g": 2.28, "minutes": 13.0,
                       "mm": {"Outer wall": 900.0, "Overhang wall": 100.0, "Bridge": 40.0}, "overhang_share": 0.1}}
    supported = {"log": {"problems": [], "warnings": []},
                 "gcode": {"layers": 115, "height_mm": 23.0, "filament_g": 3.0, "minutes": 16.0,
                           "mm": {"Outer wall": 900.0, "Support": 300.0}, "overhang_share": 0.0}}
    v = slicing.verdict(plain, supported)
    assert v["printable_without_supports"] is False and v["with_supports"]["extra_g"] == 0.72
    assert v["with_supports"]["extra_minutes"] == 3.0 and v["minutes"] == 13.0 and v["bridge_mm"] == 40.0
    assert any("cantilever" in w for w in v["warnings"])
    clean = slicing.verdict({"log": {"problems": [], "warnings": []}, "gcode": dict(plain["gcode"], mm={"Outer wall": 5.0},
                                                                                 overhang_share=0.0)}, None)
    assert clean["printable_without_supports"] is True and "with_supports" not in clean


CUBE = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
FACES = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6),
         (3, 0, 4), (3, 4, 7)]


def stl(path, boxes):
    lines = ["solid t"]
    for (x, y, z, sx, sy, sz) in boxes:
        for f in FACES:
            lines.append("facet normal 0 0 0\nouter loop")
            lines += [f"vertex {x + CUBE[i][0] * sx} {y + CUBE[i][1] * sy} {z + CUBE[i][2] * sz}" for i in f]
            lines.append("endloop\nendfacet")
    path.write_text("\n".join(lines) + "\nendsolid t\n")
    return str(path)


SLICER = slicing.find_slicer()


@pytest.mark.skipif(SLICER is None or shutil.which("true") is None, reason="no slicer installed")
def test_a_real_slicer_finds_a_part_that_hangs_in_the_air(tmp_path):
    profiles = slicing.find_profiles(SLICER["profiles"], "Anycubic Kobra S1", nozzle=0.4) \
        or slicing.find_profiles(SLICER["profiles"], "Bambu Lab A1 mini", nozzle=0.4)
    if profiles is None:
        pytest.skip("the installed slicer has no profile for the test printers")
    good = slicing.slice_file(SLICER, profiles, stl(tmp_path / "cube.stl", [(0, 0, 0, 20, 20, 10)]),
                              str(tmp_path / "good"))
    assert good["ok"] and good["gcode"]["layers"] >= 40 and good["gcode"]["minutes"] > 1 and good["log"]["problems"] == []
    assert os.path.isfile(good["file"])
    bad = slicing.slice_file(SLICER, profiles, stl(tmp_path / "t.stl", [(0, 0, 0, 10, 10, 20), (-20, 0, 20, 50, 10, 3)]),
                             str(tmp_path / "bad"))
    assert any("floating" in w or "cantilever" in w for w in bad["log"]["warnings"] + bad["log"]["problems"])
    helped = slicing.slice_file(SLICER, profiles, stl(tmp_path / "t2.stl", [(0, 0, 0, 10, 10, 20), (-20, 0, 20, 50, 10, 3)]),
                                str(tmp_path / "sup"), supports=True)
    assert helped["ok"] and helped["gcode"]["mm"].get("Support", 0) > 0


def test_the_slicer_that_knows_the_printer_is_picked_and_packed_profiles_are_named(tmp_path):
    packed = tmp_path / "Orca" / "profiles"
    packed.mkdir(parents=True)
    (packed / "Acme.opc").write_bytes(b"ZCRO")
    open_ = tmp_path / "Other" / "profiles" / "Acme"
    for kind, name in (("machine", "Acme Cube 0.4 nozzle"), ("process", "0.20mm Standard @Acme Cube 0.4 nozzle"),
                       ("filament", "Acme PLA @Acme Cube 0.4 nozzle")):
        (open_ / kind).mkdir(parents=True)
        (open_ / kind / f"{name}.json").write_text(json.dumps({"name": name}))
    slicers = [{"exe": "/x/orca", "profiles": str(packed), "name": "OrcaSlicer"},
               {"exe": "/x/other", "profiles": str(open_.parent), "name": "OtherSlicer"}]
    slicer, profiles, why = slicing.pick("Acme Cube", 0.4, 0.2, "PLA", slicers)
    assert slicer["name"] == "OtherSlicer" and profiles["machine"]["name"] == "Acme Cube 0.4 nozzle"
    slicer, profiles, why = slicing.pick("Nothing", 0.4, 0.2, "PLA", slicers)
    assert slicer is None and "OrcaSlicer" in why and "packed" in why and "OtherSlicer" in why
    assert slicing.pick("Acme Cube", 0.4, 0.2, "PLA", [])[2].startswith("no slicer found")
