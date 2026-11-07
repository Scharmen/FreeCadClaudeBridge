"""Standard parts against a real FreeCAD: valid solids of the right size whose fits are right.

Set FREECAD_TARGET (see test_real_freecad.py).
"""

import json
import os

import pytest
from design import score

from freecad_bridge import installer
from freecad_bridge.parts import builders_path, catalog

TARGET = os.environ.get("FREECAD_TARGET")
pytestmark = pytest.mark.skipif(not TARGET, reason="FREECAD_TARGET not set")


@pytest.fixture(scope="module")
def cmd():
    found = installer.discover(TARGET)
    assert found, f"no FreeCAD found at {TARGET}"
    return found[0].command


LOAD = f"""
import importlib.util as _u, json, sys
_spec = _u.spec_from_file_location("fcb_builders", {str(builders_path())!r})
b = _u.module_from_spec(_spec)
sys.modules["fcb_builders"] = b
_spec.loader.exec_module(b)
iso = json.loads({json.dumps(catalog.ISO_METRIC)!r})
strain = json.loads({json.dumps(catalog.STRAIN)!r})
def make(family, params):
    return b.build(family, json.loads(params), iso, strain)
def box(s):
    bb = s.optimalBoundingBox(True, False)
    return sorted(round(x, 2) for x in (bb.XLength, bb.YLength, bb.ZLength))
"""


def run(code, cmd, family, **params):
    p = json.dumps(catalog.resolve(family, params))
    return score.run_freecad(LOAD + f"parts = make({family!r}, {p!r})\n" + code, cmd)


@pytest.mark.parametrize("family", sorted(catalog.FAMILIES))
def test_every_family_makes_valid_single_solids_at_its_defaults(family, cmd):
    r = run("_result = {n: [bool(s.isValid()), len(s.Solids), s.Volume] for n, s in parts.items()}", cmd, family)
    for name, (valid, solids, volume) in r.items():
        assert valid and solids == 1 and volume > 0, (family, name, r)


def test_gear_size_and_teeth(cmd):
    r = run("_result = box(parts['Gear'])", cmd, "gear", module=2, teeth=20, width=10, bore=8)
    assert r == pytest.approx([10, 44, 44], abs=0.1)


def test_helical_gear_keeps_its_section(cmd):
    r = run("g = parts['Gear']\n_result = [bool(g.isValid()), g.Volume, box(g)]", cmd, "gear", helix_deg=15.0)
    assert r[0] and r[2] == pytest.approx([8, 22, 22], abs=0.3)
    plain = run("_result = parts['Gear'].Volume", cmd, "gear")
    assert r[1] == pytest.approx(plain, rel=0.03)


def test_a_gear_pair_meshes_without_touching(cmd):
    r = run("a, b2 = parts['GearA'], parts['GearB']\n"
            "_result = [a.common(b2).Volume, a.distToShape(b2)[0], b2.BoundBox.Center.x]", cmd, "gear_pair")
    assert r[0] < 1e-6, "the teeth do not overlap"
    assert r[1] < 0.3, "but they are engaged"
    assert r[2] == pytest.approx(30, abs=0.3)


def test_spring_is_the_right_size_with_ground_ends(cmd):
    r = run("s = parts['Spring']\n_result = [box(s), sum(1 for f in s.Faces if type(f.Surface).__name__ == 'Plane')]",
            cmd, "spring")
    assert r[0] == pytest.approx([20, 20, 40], abs=0.5)
    assert r[1] >= 2


def test_bolt_fits_its_nut(cmd):
    r = run("a, n = parts['Bolt'], parts['Nut']\n_result = [a.common(n).Volume, a.distToShape(n)[0], box(a), box(n)]",
            cmd, "bolt_nut")
    assert r[0] < 0.5, "the threads do not overlap"
    assert r[1] < 0.4, "but they are engaged"
    assert r[2][2] == pytest.approx(35.3, abs=0.1) and r[3][0] == pytest.approx(6.8, abs=0.1)


def test_hinge_has_its_gaps(cmd):
    r = run("a, c, p = parts['LeafA'], parts['LeafB'], parts['Pin']\n"
            "_result = [a.common(c).Volume, a.distToShape(c)[0], p.distToShape(a)[0], p.distToShape(c)[0]]", cmd,
            "hinge")
    assert r[0] == 0 and r[1:] == pytest.approx([0.3, 0.3, 0.3], abs=0.02)


def test_enclosure_lid_rests_and_holes_line_up(cmd):
    r = run("a, c = parts['Body'], parts['Lid']\n"
            "def hs(s, r0):\n"
            "    return sorted({(round(f.Surface.Center.x, 2), round(f.Surface.Center.y, 2)) for f in s.Faces\n"
            "                   if type(f.Surface).__name__ == 'Cylinder' and abs(f.Surface.Radius - r0) < 0.01})\n"
            "_result = [a.common(c).Volume, a.distToShape(c)[0], hs(a, 2.0), hs(c, 1.7), box(a), box(c)]", cmd,
            "enclosure")
    assert r[0] < 0.5 and r[1] < 1e-3
    assert r[2] == r[3] and len(r[2]) == 4
    assert r[4] == pytest.approx([22, 50, 80], abs=0.1) and r[5] == pytest.approx([3, 50, 80], abs=0.1)


def test_snap_box_closes_with_room_to_bend(cmd):
    r = run("a, c = parts['Box'], parts['Lid']\n_result = [a.common(c).Volume, a.distToShape(c)[0], box(a), box(c)]",
            cmd, "snap_box")
    assert r[0] < 0.5 and r[1] < 1e-3, "rests on the rim without running into the box"
    assert r[2][2] == pytest.approx(60, abs=0.1)


def test_knob_has_a_d_bore(cmd):
    r = run("k = parts['Knob']\n"
            "_result = [box(k), sum(1 for f in k.Faces if type(f.Surface).__name__ == 'Cylinder' and"
            " abs(f.Surface.Radius - 3.1) < 0.01), k.Volume]", cmd, "knob")
    assert r[0] == pytest.approx([18, 25, 25], abs=0.2) and r[1] == 1


def test_an_external_thread_on_a_bottle_neck_measures_its_pitch(cmd):
    r = run("import Part\nfrom freecad_bridge.checks import measure\nt = parts['Thread']\n"
            "neck = Part.makeCylinder(14, 10)\nbottle = neck.fuse(t)\n"
            "_result = [box(t), measure.thread_pitch(bottle, [13.0, 16.5]), bool(bottle.isValid()),"
            " len(bottle.Solids)]",
            cmd, "thread", major=32.0, pitch=3.0, length=4.5, core=28.0)
    assert r[0][2] == pytest.approx(32, abs=0.2) and r[0][0] == pytest.approx(4.5, abs=0.3)
    assert r[1]["pitch"] == pytest.approx(3.0, abs=0.3), r
    assert r[2] and r[3] == 1, "the thread fuses with the neck into one solid"


def test_an_internal_thread_is_a_cutter_for_a_cap(cmd):
    r = run("import Part\nfrom freecad_bridge.checks import measure\nc = parts['ThreadCutter']\n"
            "cap = Part.makeCylinder(20, 10).cut(c)\n"
            "_result = [bool(cap.isValid()), len(cap.Solids), measure.thread_pitch(cap, [13.0, 16.5]), box(c)]",
            cmd, "thread", kind="internal", major=32.0, pitch=3.0, length=8.0)
    assert r[0] and r[1] == 1
    assert r[2]["pitch"] == pytest.approx(3.0, abs=0.3), r
    assert r[3][2] == pytest.approx(32.3, abs=0.2), "the cutter is the clearance wider than the thread"


def test_a_wheel_has_a_tyre_with_tread_a_spoked_rim_and_an_axle_bore(cmd):
    r = run("Part = b.Part\nt, rim = parts['Tyre'], parts['Rim']\n"
            "solid_rim = Part.makeCylinder(20, 22, b.V(0, -11, 0), b.V(0, 1, 0))\n"
            "probe = Part.makeCylinder(1.4, 40, b.V(0, -20, 0), b.V(0, 1, 0))\n"
            "_result = [box(t), box(rim), rim.Volume / solid_rim.Volume, rim.common(probe).Volume,\n"
            "           len([f for f in t.Faces if type(f.Surface).__name__ == 'Cylinder']), sorted(parts)]",
            cmd, "wheel", diameter=60.0, width=24.0, rim=40.0, spokes=5, bore=3.0, grooves=6)
    assert r[0] == pytest.approx([24, 60, 60], abs=0.3) and r[1][1] == pytest.approx(40, abs=0.3)
    assert 0.15 < r[2] < 0.7, "a spoked rim is much lighter than a disc"
    assert r[3] < 1e-6, "the axle bore goes through"
    assert r[4] >= 6 + 2, "tread grooves around the tyre"
    assert r[5] == ["Rim", "Tyre"]


def test_a_toy_wheel_still_has_windows_between_its_spokes(cmd):
    r = run("Part = b.Part\nrim = parts['Rim']\n"
            "solid_rim = Part.makeCylinder(7, rim.BoundBox.YLength, b.V(0, rim.BoundBox.YMin, 0), b.V(0, 1, 0))\n"
            "_result = rim.Volume / solid_rim.Volume", cmd, "wheel", diameter=20.0, width=8.0, rim=14.0, spokes=5,
            bore=3.2, grooves=4, groove_depth=0.6)
    assert 0.2 < r < 0.75, "a 14 mm rim with a 3.2 mm bore is still spoked, not a disc"
    with pytest.raises(Exception, match="hub|spokes"):
        run("_result = 1", cmd, "wheel", diameter=12.0, width=5.0, rim=8.0, bore=3.2)


def test_a_wheel_with_brake_adds_disc_and_caliper_inside_the_rim(cmd):
    r = run("_result = [sorted(parts), box(parts['Disc']), parts['Caliper'].BoundBox.XMax, parts['Rim'].BoundBox.XMax,"
            " parts['Disc'].BoundBox.YMax]", cmd, "wheel", diameter=60.0, rim=40.0, disc=30.0)
    assert r[0] == ["Caliper", "Disc", "Rim", "Tyre"]
    assert r[1][1] == pytest.approx(30, abs=0.3) and r[2] < r[3], "the caliper stays inside the rim"
    assert r[4] < 0, "the brake is on the -y side by default"
    other = run("_result = parts['Disc'].BoundBox.YMin", cmd, "wheel", diameter=60.0, rim=40.0, disc=30.0,
                brake_side="+y")
    assert other > 0, "brake_side +y puts it on the +y side"


def test_a_lamp_has_housing_reflector_lens_and_strip_that_fit_together(cmd):
    r = run("h, ref, lens, strip = parts['Housing'], parts['Reflector'], parts['Lens'], parts['Strip']\n"
            "_result = [box(h), box(lens), h.common(lens).Volume, h.common(ref).Volume, h.distToShape(lens)[0],\n"
            "           lens.BoundBox.YMin < h.BoundBox.YMin + 0.5, strip.Volume > 0, ref.Volume < h.Volume]",
            cmd, "lamp", width=30.0, height=12.0, depth=15.0)
    assert r[0][2] == pytest.approx(30, abs=0.3)
    assert r[2] < 1e-3 and r[3] < 1e-3, "the lens and the reflector sit in the housing without overlap"
    assert r[4] < 0.3 and r[5] and r[6] and r[7], "the lens closes the front, the strip and reflector are inside"


@pytest.mark.parametrize("cells", ["honeycomb", "slats", "mesh", "holes"])
def test_a_pattern_panel_is_mostly_open_and_the_holes_kind_is_its_negative(cmd, cells):
    r = run("Part = b.Part\np = parts['Pattern']\nfull = Part.makeBox(40, 1.2, 20, b.V(-20, 0, 0))\n"
            "_result = [box(p), p.Volume / full.Volume, bool(p.isValid()), len(p.Solids)]",
            cmd, "pattern", cells=cells, width=40.0, height=20.0, thickness=1.2, cell=4.0, bar=0.8)
    assert r[0] == pytest.approx([1.2, 20, 40], abs=0.3) and r[2] and r[3] == 1
    assert 0.25 < r[1] < 0.8, f"{cells}: a panel of bars is mostly open"
    neg = run("Part = b.Part\np = parts['Pattern']\n_result = [p.Volume, len(p.Solids) >= 2]", cmd, "pattern",
              cells=cells, kind="holes", width=40.0, height=20.0, thickness=1.2, cell=4.0, bar=0.8)
    assert neg[0] > 0 and neg[1], "the holes kind is a set of cutters"
