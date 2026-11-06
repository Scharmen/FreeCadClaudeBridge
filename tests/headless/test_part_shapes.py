"""Standard parts and the measuring against a real FreeCAD: details a look at the model would catch.

Set FREECAD_TARGET (see test_real_freecad.py).
"""

import json
import os

import pytest
from design import score

from freecad_bridge import installer

TARGET = os.environ.get("FREECAD_TARGET")
pytestmark = pytest.mark.skipif(not TARGET, reason="FREECAD_TARGET not set")

HEAD = "import math, FreeCAD as App, Part\nfrom freecad_bridge.parts import builders as b\nV = App.Vector\n"
SPRING = "s = b.spring({'wire': 1.5, 'outer': 20.0, 'free_length': 40.0, 'active_coils': 8})['Spring']\n"
WIDTH = """
def width(wire, teeth, r):
    pts = [(math.hypot(p.x, p.y), math.atan2(p.y, p.x)) for p in wire.discretize(Distance=0.005)]
    near = [t for q, t in pts if abs(q - r) < 0.01 and abs(t) < math.pi / teeth]
    return (max(near) - min(near)) * r
"""


@pytest.fixture(scope="module")
def cmd():
    found = installer.discover(TARGET)
    assert found, f"no FreeCAD found at {TARGET}"
    return found[0].command


@pytest.mark.parametrize("teeth", [18, 20, 30])
def test_gear_teeth_do_not_narrow_towards_the_root(cmd, teeth):
    r = score.run_freecad(HEAD + WIDTH + f"z = {teeth}\nw = b.gear_outline(1, z)\n"
                          "rb = z / 2 * math.cos(math.radians(20))\n"
                          "rf = z / 2 - 1.25\n"
                          "_result = [width(w, z, rf + (rb - rf) * k / 4) for k in range(1, 5)]\n", cmd)
    assert all(a >= b - 0.04 for a, b in zip(r, r[1:], strict=False)), r


@pytest.mark.parametrize("teeth_a,teeth_b", [(18, 36), (20, 40), (30, 30)])
def test_gears_with_filleted_roots_still_clear_each_other_through_a_turn(cmd, teeth_a, teeth_b):
    r = score.run_freecad(HEAD + f"za, zb = {teeth_a}, {teeth_b}\n"
                          "a = Part.Face(b.gear_outline(1, za)).extrude(V(0, 0, 1))\n"
                          "o = Part.Face(b.gear_outline(1, zb)).extrude(V(0, 0, 1))\n"
                          "c = (za + zb) / 2\nworst = 0\n"
                          "for k in range(8):\n"
                          "    ta = 2 * math.pi / za * k / 8\n"
                          "    ga = a.copy()\n    ga.rotate(V(0, 0, 0), V(0, 0, 1), math.degrees(ta))\n"
                          "    gb = o.copy()\n"
                          "    back = 180 + (0 if zb % 2 else 180 / zb) - math.degrees(ta) * za / zb\n"
                          "    gb.rotate(V(0, 0, 0), V(0, 0, 1), back)\n"
                          "    gb.translate(V(c, 0, 0))\n"
                          "    worst = max(worst, ga.common(gb).Volume)\n"
                          "_result = worst\n", cmd)
    assert r < 0.02, r


def test_spring_ends_are_ground_flat_around_most_of_the_turn_and_closed(cmd):
    r = score.run_freecad(
        HEAD + SPRING +
        "bb = s.optimalBoundingBox(True, False)\n"
        "def seat(z):\n"
        "    ang = []\n"
        "    for f in s.Faces:\n"
        "        flat = type(f.Surface).__name__ == 'Plane'\n"
        "        if flat and abs(f.BoundBox.ZMin - z) < 0.01 and abs(f.BoundBox.ZMax - z) < 0.01:\n"
        "            ang += [math.degrees(math.atan2(p.y, p.x)) % 360 for e in f.Edges for p in e.discretize(100)]\n"
        "    ang = sorted(ang)\n"
        "    gaps = [q - a for a, q in zip(ang, ang[1:])] + [ang[0] + 360 - ang[-1]]\n"
        "    return 360 - max(gaps)\n"
        "_result = {'z': [bb.ZMin, bb.ZMax], 'd': bb.XLength, 'valid': s.isValid(),\n"
        "           'seats': [seat(bb.ZMin), seat(bb.ZMax)]}\n",
        cmd)
    assert r["valid"] and r["z"][0] == pytest.approx(0, abs=0.02) and r["z"][1] == pytest.approx(40, abs=0.02), r
    assert r["d"] == pytest.approx(20, abs=0.3), r
    assert min(r["seats"]) >= 250, r


def test_a_spring_keeps_its_wire_gap_where_the_coils_close_and_opens_evenly_in_the_middle(cmd):
    r = score.run_freecad(
        HEAD + SPRING +
        "wires = s.slice(V(0, 1, 0), 0)\n"
        "zs = sorted(w.BoundBox.ZMax for w in wires if w.BoundBox.Center.x > 0)\n"
        "_result = zs\n", cmd)
    gaps = [b - a for a, b in zip(r, r[1:], strict=False)]    # by the tops: the bottom tip is ground from below
    assert all(g >= 1.5 for g in gaps[:-1]), gaps                                 # the top tip is ground from above
    middle = gaps[3:-3]
    assert max(middle) - min(middle) < 0.02, gaps


def test_a_bore_split_into_quarters_is_still_a_hole(cmd):
    r = score.run_freecad(
        "import FreeCAD as App, Part\nfrom freecad_bridge.checks import measure\nV = App.Vector\n"
        "import BOPTools.SplitAPI as sp\n"
        "cut = Part.makeBox(40, 40, 20, V(-20, -20, 0)).cut(Part.makeCylinder(6, 20))\n"
        "planes = [Part.makePlane(100, 100, V(-50, 0, -40), V(0, 1, 0)),\n"
        "          Part.makePlane(100, 100, V(0, 50, -40), V(1, 0, 0))]\n"
        "parts = sp.slice(cut, planes, 'Standard').Solids\n"
        "comp = Part.Compound([f for p in parts for f in p.Faces if type(f.Surface).__name__ == 'Cylinder'])\n"
        "_result = measure._holes_and_radii(comp)[0]\n", cmd)
    assert [h["d"] for h in r] == [12.0], r
    assert not r[0].get("open"), r


def test_the_volume_of_a_spring_is_right_even_where_the_kernel_gets_it_wrong(cmd):
    r = score.run_freecad(
        HEAD + "from freecad_bridge.checks import measure\n" + SPRING + "_result = measure.volume_of(s)\n", cmd)
    assert 800 < r < 1250, r


BODY = """
from freecad_bridge.parts import builders as bb
xs = [0, 60, 200, 500, 1000, 1500, 2000, 2500, 3000, 3500, 3800, 3940, 4000]
def z(x, top, drop): return top - drop * ((x - 2000) / 2000) ** 2
lines = [{"points": [[x, 0, z(x, 1000, 180)] for x in xs]},
         {"points": [[x, 450, z(x, 960, 170)] for x in xs]},
         {"points": [[x, 800 - 200 * ((x - 2000) / 2000) ** 2, z(x, 800, 150)] for x in xs], "crease": True},
         {"points": [[x, 860 - 200 * ((x - 2000) / 2000) ** 2, z(x, 500, 100)] for x in xs]},
         {"points": [[x, 700 - 200 * ((x - 2000) / 2000) ** 2, 200] for x in xs], "crease": True},
         {"points": [[x, 0, 180] for x in xs]}]
"""


def test_a_faired_body_is_one_valid_solid_with_a_sharp_crease(cmd):
    code = HEAD + BODY + """
s = bb.faired_body({"lines": lines, "poles": 9, "name": "Lower"})["Lower"]
bb_ = s.optimalBoundingBox(True, False)
p = App.Vector(2000, 800, 800)
near = [f for f in s.Faces if f.distToShape(Part.Vertex(p))[0] < 1]
ns = []
for f in near:
    u, v = f.Surface.parameter(f.distToShape(Part.Vertex(p))[1][0][0])
    ns.append(f.normalAt(u, v))
angle = math.degrees(ns[0].getAngle(ns[1]))
_result = {"valid": s.isValid(), "solids": len(s.Solids), "w": bb_.YLength, "l": bb_.XLength,
           "near": len(near), "angle": angle, "vol": s.Volume}
"""
    res = score.run_freecad(code, cmd)
    assert res["valid"] and res["solids"] == 1 and res["vol"] > 0
    assert abs(res["l"] - 4000) < 1 and abs(res["w"] - 2 * 860) < 40
    assert res["near"] == 2 and res["angle"] > 15          # the crease stays a real edge


def test_a_faired_body_ripples_less_than_a_loft_through_the_same_noisy_points(cmd):
    code = HEAD + BODY + """
import random
import surface_math as sm
import numpy as np
random.seed(4)
noisy = [{**ln, "points": [[x, max(0, y + random.uniform(-4, 4)) if y else 0, zz + random.uniform(-4, 4)]
                           for x, y, zz in ln["points"]]} for ln in lines]
new = bb.faired_body({"lines": noisy, "poles": 9, "name": "B"})["B"]
secs = []
for i, x in enumerate(xs):
    pts = [App.Vector(*ln["points"][i]) for ln in noisy]
    pts.append(pts[0])
    secs.append(Part.makePolygon(pts))
old = Part.makeLoft(secs, True, False)
def count(shape):
    out = 0
    for y in (200.0, 450.0):
        w = max(shape.slice(App.Vector(0, 1, 0), y), key=lambda q: q.Length)
        p = np.array([[v.x, v.z] for v in w.discretize(Number=400)])
        out += sm.inflections(p)
    return out
_result = {"new": count(new), "old": count(old)}
"""
    res = score.run_freecad(code, cmd)
    assert res["new"] < res["old"]


def test_the_default_faired_body_builds_and_reports_its_fit(cmd, tmp_path):
    from freecad_bridge.parts import catalog

    params = catalog.resolve("faired_body")
    code = HEAD + "import json\nfrom freecad_bridge.parts import builders as bb\n" + (
        f"_result = bb.export('faired_body', json.loads({json.dumps(json.dumps(params))}), {{}}, {{}}, "
        f"{str(tmp_path)!r})\n")
    made = score.run_freecad(code, cmd)[0]
    assert made["valid"] and made["solids"] == 1 and made["max_distance_to_points_mm"] < 3
    assert abs(made["size_mm"][0] - 200) < 1 and abs(made["size_mm"][1] - 80) < 8
