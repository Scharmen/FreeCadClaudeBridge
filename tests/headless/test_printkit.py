# ruff: noqa: E501
"""The print export against a real FreeCAD: a small car with coloured parts, wheels and an axle.

Set FREECAD_TARGET (see test_real_freecad.py).
"""

import json
import os
import zipfile
from pathlib import Path

import pytest
from design import score

from freecad_bridge import installer

TARGET = os.environ.get("FREECAD_TARGET")
pytestmark = pytest.mark.skipif(not TARGET, reason="FREECAD_TARGET not set")
ADDON = Path(__file__).resolve().parents[2] / "plugin/addon/ClaudeBridge"

MODEL = """
import FreeCAD as App, Part, json
V = App.Vector
d = App.newDocument("Car")
def add(name, shape):
    o = d.addObject("Part::Feature", name)
    o.Shape = shape
    return o
add("Body", Part.makeBox(100, 40, 25, V(0, -20, 10)))
add("Roof", Part.makeBox(50, 30, 15, V(25, -15, 35)))
add("Lamp", Part.makeBox(6, 8, 6, V(-6, -14, 22)))
add("Stripe", Part.makeBox(90, 40, 0.3, V(5, -20, 35)))
for name, x, y0 in (("FL", 20, 20.5), ("RL", 80, 20.5), ("FR", 20, -30.5), ("RR", 80, -30.5)):
    y = V(0, 1, 0)
    add("Tyre_" + name, Part.makeCylinder(12, 10, V(x, y0, 12), y).cut(Part.makeCylinder(8, 10, V(x, y0, 12), y)))
    add("Rim_" + name, Part.makeCylinder(8, 10, V(x, y0, 12), y))
d.recompute()
d.saveAs(WORK + "/car.FCStd")
App.closeDocument(d.Name)
COLORS = {"Body": "#0a5a2b", "Roof": "#000000", "Lamp": "#b4b6ba", "Stripe": "#c8141a"}
for n in ("FL", "RL", "FR", "RR"):
    COLORS["Tyre_" + n] = "#141416"
    COLORS["Rim_" + n] = "#b4b6ba"
MOVING = [{"objects": ["Tyre_" + n, "Rim_" + n], "axis": "y", "axle": None} for n in ("FL", "RL", "FR", "RR")]
"""


@pytest.fixture(scope="module")
def cmd():
    found = installer.discover(TARGET)
    assert found, f"no FreeCAD found at {TARGET}"
    return found[0].command


def run(cmd, tmp, join="pins", printer=None, moving=True, extra=""):
    printer = printer or {"name": "Test", "bed": [250, 250, 250], "nozzle": 0.4, "slots": 4, "process": "fdm"}
    code = (f"import sys\nsys.path.insert(0, {str(ADDON)!r})\nWORK = {str(tmp)!r}\n" + MODEL + extra
            + "from claudebridge.printkit import engine\n"
            + f"_result = engine.run(WORK + '/car.FCStd', {{'colors': COLORS, 'printer': {printer!r}, 'join': {join!r}, "
            + f"'whole': True, 'split': True, 'moving': MOVING if {moving} else [], 'lang': 'de', 'name': 'Car'}}, "
            + "WORK + '/kit')\n")
    return score.run_freecad(code, cmd)


@pytest.fixture(scope="module")
def pins(cmd, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pins")
    return tmp, run(cmd, tmp)


def test_the_whole_model_is_one_coloured_print(pins):
    tmp, r = pins
    path = tmp / "kit" / r["whole"]["file"]
    assert path.is_file() and r["whole"]["fits"] is True and r["whole"]["colors"] == 4
    assert zipfile.ZipFile(path).testzip() is None
    assert {"#0a5a2b", "#c8141a"} <= set(r["whole"]["palette"])


def test_every_colour_has_its_own_plate_and_closed_parts(pins):
    tmp, r = pins
    colors = {p["color_name"] for p in r["plates"]}
    assert {"Gruen", "Schwarz", "Silber"} <= colors
    for plate in r["plates"]:
        assert (tmp / "kit" / plate["file"]).is_file()
    for piece in r["pieces"]:
        assert piece["closed"] is True, piece
        assert (tmp / "kit" / piece["stl"]).is_file()


def test_a_part_too_thin_to_print_goes_into_its_neighbour_and_is_named_for_painting(pins):
    _tmp, r = pins
    assert "Stripe" in r["painted"]
    assert not any("Stripe" in p["objects"] for p in r["pieces"] if p["color_name"] == "Rot")


def test_touching_parts_get_pins_when_there_is_room(pins):
    _tmp, r = pins
    joints = {frozenset((j["a_objects"][0], j["b_objects"][0])): j for j in r["joints"]}
    roof = joints[frozenset(("Body", "Roof"))]
    assert roof["type"] == "dowel" and roof["count"] >= 2
    assert r["dowels"]["count"] >= 2 and r["dowels"]["file"]


def test_holes_are_really_cut_on_both_sides(cmd, pins):
    tmp, r = pins
    code = (f"import Part, json\nr = json.loads({json.dumps(json.dumps(r))})\nout = {{}}\n"
            f"for p in r['pieces']:\n    s = Part.read({str(tmp / 'kit')!r} + '/' + p['brep'])\n"
            "    out[p['objects'][0]] = (round(s.Volume, 1), s.isValid())\n_result = out\n")
    vols = score.run_freecad(code, cmd)
    assert all(v[1] for v in vols.values())
    assert vols["Roof"][0] < 50 * 30 * 15 - 5, "the holes took material out of the roof"


def test_wheels_turn_on_axle_pins(pins):
    tmp, r = pins
    assert len(r["moving"]) == 4
    wheel = r["moving"][0]
    assert wheel["type"] == "stub" and wheel["host"] == "Body" and wheel["axle_d"] >= 3
    assert wheel["free_d"] - wheel["axle_d"] == pytest.approx(0.4) and wheel["press_d"] - wheel["axle_d"] == pytest.approx(0.1)
    assert (tmp / "kit" / r["axles"]["file"]).is_file() and r["axles"]["count"] == 4


def test_moving_parts_are_never_joined_with_pins_to_their_host(pins):
    _tmp, r = pins
    for j in r["joints"]:
        names = {*j["a_objects"], *j["b_objects"]}
        assert not ({"Body"} & names and any(n.startswith(("Tyre", "Rim")) for n in names)), j


def test_glue_mode_has_no_pins_but_keeps_the_axles(cmd, tmp_path):
    r = run(cmd, tmp_path, join="glue")
    assert r["dowels"]["count"] == 0 and all(j["type"] == "glue" for j in r["joints"])
    assert r["axles"]["count"] == 4


def test_a_one_colour_printer_still_gets_a_plate_per_colour(cmd, tmp_path):
    printer = {"name": "Mono", "bed": [250, 250, 250], "nozzle": 0.4, "slots": 1, "process": "fdm"}
    r = run(cmd, tmp_path, printer=printer)
    assert r["whole"]["colors"] == 1 and len({p["color_name"] for p in r["plates"]}) >= 3


def test_too_small_a_printer_is_named(cmd, tmp_path):
    printer = {"name": "Tiny", "bed": [60, 60, 60], "nozzle": 0.4, "slots": 1, "process": "fdm"}
    r = run(cmd, tmp_path, printer=printer)
    assert r["whole"]["fits"] is False
    assert any("Body" in w for w in r["warnings"])


def test_the_guide_data_lists_every_piece_and_step(pins):
    tmp, r = pins
    ids = {p["id"] for p in r["pieces"]}
    assert set(r["order"]) == ids
    assert (tmp / "kit" / r["report"]).is_file()
    for mesh in r["pieces"]:
        data = json.loads((tmp / "kit" / mesh["preview"]).read_text())
        assert data["verts"] and data["tris"]


def test_an_axle_object_turns_in_its_host_and_sits_tight_in_the_wheels(cmd, tmp_path):
    extra = """
d = App.openDocument(WORK + "/car.FCStd")
ax = d.addObject("Part::Feature", "Axle_F")
ax.Shape = Part.makeCylinder(1, 61, V(20, -30.5, 12), V(0, 1, 0))
d.save()
App.closeDocument(d.Name)
COLORS["Axle_F"] = "#808080"
MOVING = [{"objects": ["Tyre_" + n, "Rim_" + n], "axis": "y", "axle": "Axle_F"} for n in ("FL", "FR")]
"""
    r = run(cmd, tmp_path, extra=extra)
    wheels = r["moving"]
    assert {w["type"] for w in wheels} == {"through"} and wheels[0]["axle_d"] >= 3
    assert r["axles"]["count"] == 1 and not any("Axle_F" in p["objects"] for p in r["pieces"])


def test_two_big_parts_of_one_colour_stay_apart_and_small_trim_joins(cmd, tmp_path):
    extra = """
d = App.openDocument(WORK + "/car.FCStd")
trim = d.addObject("Part::Feature", "Trim")
trim.Shape = Part.makeBox(4, 4, 4, V(40, -2, 50))
d.save()
App.closeDocument(d.Name)
COLORS["Trim"] = COLORS["Roof"]
COLORS["Roof"] = COLORS["Body"]
COLORS["Trim"] = COLORS["Body"]
"""
    r = run(cmd, tmp_path, extra=extra, moving=False)
    ids = {p["objects"][0]: p for p in r["pieces"]}
    assert "Roof" in ids and "Body" in ids, "two big parts"
    assert "Trim" in ids["Roof"]["objects"]


def test_the_order_builds_the_fixed_parts_before_the_wheels(pins):
    _tmp, r = pins
    moving = {p["id"] for p in r["pieces"] if p["moving"]}
    first_moving = min(r["order"].index(i) for i in moving)
    assert all(r["order"].index(p["id"]) < first_moving for p in r["pieces"] if not p["moving"])
    for m in r["moving"]:
        assert m["hub"], m


def test_a_long_part_prints_lying_on_a_real_base_not_standing_on_its_nose(pins):
    _tmp, r = pins
    body = next(p for p in r["pieces"] if p["objects"][0] == "Body")
    assert body["size_mm"][2] < 60, body["size_mm"]


def test_each_part_says_whether_it_needs_support(pins):
    _tmp, r = pins
    assert all(isinstance(p["support"], bool) for p in r["pieces"])
    roof = next(p for p in r["pieces"] if p["objects"][0] == "Roof")
    assert roof["support"] is False, "a block lies flat"
