"""The design benchmark's measuring against a real FreeCAD: known-good models score full marks.

Set FREECAD_TARGET (see test_real_freecad.py). Each task with a `solution` is built headless,
saved, measured and scored; a good model that loses points means the spec or the measuring is wrong.
"""

import os

import pytest
from design import score, tasks

from freecad_bridge import installer

TARGET = os.environ.get("FREECAD_TARGET")
pytestmark = pytest.mark.skipif(not TARGET, reason="FREECAD_TARGET not set")
SOLVED = [t["id"] for t in tasks.TASKS if t.get("solution")]


@pytest.fixture(scope="module")
def cmd():
    found = installer.discover(TARGET)
    assert found, f"no FreeCAD found at {TARGET}"
    return found[0].command


@pytest.mark.parametrize("task_id", SOLVED)
def test_a_known_good_model_scores_full_marks(task_id, cmd, tmp_path):
    path = score.build_solution(task_id, tmp_path / f"{task_id}.FCStd", cmd)
    r = score.score_file(path, tasks.get(task_id), cmd)
    assert r["score"] == 100.0, r["failed"]


def test_the_same_file_scores_the_same_and_a_wrong_model_scores_lower(cmd, tmp_path):
    task = tasks.get("bearing_housing")
    good = score.build_solution("bearing_housing", tmp_path / "good.FCStd", cmd)
    a, b = score.score_file(good, task, cmd), score.score_file(good, task, cmd)
    assert a["score"] == b["score"] and a["measured"] == b["measured"]
    wrong = tmp_path / "wrong.FCStd"
    score.run_freecad("import FreeCAD as App, Part\ndoc = App.newDocument('W')\n"
                      "o = doc.addObject('Part::Feature', 'Block')\n"
                      "o.Shape = Part.makeBox(50, 50, 12).cut(Part.makeCylinder(11.5, 20, App.Vector(25, 25, -1)))\n"
                      f"doc.recompute()\ndoc.saveAs({str(wrong)!r})\n_result = True\n", cmd)
    w = score.score_file(wrong, task, cmd)
    assert w["score"] < 70, w
    assert any("holes 4.3–4.8" in f for f in w["failed"])


def test_thread_pitch_is_measured_on_a_helical_thread_and_inside_a_nut(cmd):
    r = score.run_freecad(
        "import FreeCAD as App, Part\nfrom freecad_bridge.checks import measure\nV = App.Vector\n"
        "p = 1.25\n"
        "tri = Part.makePolygon([V(3.2, 0, -0.55), V(4.0, 0, -0.05), V(4.0, 0, 0.05), V(3.2, 0, 0.55),\n"
        "                        V(3.2, 0, -0.55)])\n"
        "helix = Part.Wire(Part.makeHelix(p, 20, 3.2))\n"
        "ridge = helix.makePipeShell([tri], True, True)\n"
        "bolt = Part.makeCylinder(3.25, 20.6, V(0, 0, -0.3)).fuse(ridge)\n"
        "nut = Part.makeBox(14, 14, 8, V(-7, -7, 6)).cut(bolt)  # the internal thread is the bolt's negative\n"
        "_result = [measure.thread_pitch(bolt, [3.0, 4.6]), measure.thread_pitch(nut, [3.0, 4.6])]\n", cmd)
    assert r[0]["pitch"] == pytest.approx(1.25, abs=0.05), r
    assert r[1]["pitch"] == pytest.approx(1.25, abs=0.05), r


def test_a_short_thread_on_a_bottle_is_measured_and_rings_are_no_thread(cmd):
    r = score.run_freecad(
        "import FreeCAD as App, Part\nfrom freecad_bridge.checks import measure\nV = App.Vector\n"
        "body = Part.makeCylinder(30, 110).fuse(Part.makeCone(30, 14, 30, V(0, 0, 110)))"
        ".fuse(Part.makeCylinder(14, 10, V(0, 0, 140)))\n"
        "bottle = body.cut(Part.makeCylinder(12.4, 200, V(0, 0, -1)).cut(Part.makeCylinder(13, 2, V(0, 0, -1))))\n"
        "tri = Part.makePolygon([V(13.8, 0, -0.9), V(15.5, 0, -0.3), V(15.5, 0, 0.3), V(13.8, 0, 0.9),\n"
        "                        V(13.8, 0, -0.9)])\n"
        "helix = Part.Wire(Part.makeHelix(3, 4.5, 13.8))\nhelix.translate(V(0, 0, 142))\n"
        "tri.translate(V(0, 0, 142))\n"
        "threaded = bottle.fuse(helix.makePipeShell([tri], True, True))\n"
        "rings = bottle\n"
        "for z in (142, 145, 148):\n"
        "    rings = rings.fuse(Part.makeCylinder(15.5, 1, V(0, 0, z)).cut(Part.makeCylinder(13, 3, V(0, 0, z - 1))))\n"
        "_result = [measure.thread_pitch(threaded, [13.0, 16.5]), measure.thread_pitch(rings, [13.0, 16.5])]\n", cmd)
    assert r[0]["pitch"] == pytest.approx(3.0, abs=0.3), r
    assert r[1]["pitch"] is None, r
