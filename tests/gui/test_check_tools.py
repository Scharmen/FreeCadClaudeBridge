"""GUI integration: the check tools work on a saved copy and leave the document alone."""

import json

import anyio
import pytest

from freecad_bridge import client, mcp_server

pytestmark = pytest.mark.skipif(not client.list_instances(), reason="no running FreeCAD with ClaudeBridge")

DOC = "BridgeITestChecks"
BUILD = """
import Part
d = App.getDocument("BridgeITestChecks")
vs = d.addObject("App::VarSet", "Params")
vs.addProperty("App::PropertyLength", "Length", "Plate")
vs.Length = 40
plate = d.addObject("Part::Box", "Plate")
plate.Width, plate.Height = 30, 10
plate.setExpression("Length", "Params.Length")
pin = d.addObject("Part::Cylinder", "Pin")
pin.Radius, pin.Height = 3, 10
pin.Placement.Base = Vector(100, 0, 0)
sleeve = d.addObject("Part::Feature", "Sleeve")
sleeve.Shape = Part.makeCylinder(8, 10, Vector(100, 0, 0)).cut(Part.makeCylinder(3.25, 10, Vector(100, 0, 0)))
thin = d.addObject("Part::Feature", "Thin")
thin.Shape = Part.makeBox(30, 30, 20, Vector(200, 0, 0)).cut(Part.makeBox(29, 29, 19.5, Vector(200.5, 0.5, 0.25)))
fixed = d.addObject("Part::Box", "Fixed")
fixed.Length, fixed.Width, fixed.Height = 5, 5, 5
fixed.Placement.Base = Vector(300, 0, 0)
d.recompute()
_result = True
"""


def call(op, timeout_s=60, **args):
    return client.call(op, args, timeout_s=timeout_s).result


def state():
    return call("run_python", code=f'd = App.getDocument("{DOC}")\n_result = [d.UndoCount, len(d.Objects), d.FileName]',
                recompute=False)["result"]


@pytest.fixture(autouse=True)
def fresh_doc():
    for d in call("status").get("documents", []):
        if d["name"].startswith(DOC):
            call("document", action="close", name=d["name"], force=True)
    call("document", action="new", name=DOC)
    call("run_python", code=BUILD, doc=DOC)
    yield
    call("document", action="close", name=DOC, force=True)


def tool(fn, **kw):
    return json.loads(anyio.run(lambda: fn(doc=DOC, **kw)))


def test_check_design_reports_what_is_wrong_and_changes_nothing():
    before = state()
    good = tool(mcp_server.check_design, spec={"parts": [{"name": "plate", "bbox": [40, 30, 10], "tol": 0.2}],
                                               "params": ["Length"], "expressions_min": 1})
    assert good["ok"] and not good["problems"] and any(p["label"] == "Plate" for p in good["parts"])
    bad = tool(mcp_server.check_design, spec={"parts": [{"name": "plate", "bbox": [50, 30, 10], "tol": 0.2}]})
    assert not bad["ok"] and "40" in bad["problems"][0]["found"]
    assert state() == before, "no undo step, no new object, no file name"


def test_a_wrong_spec_key_is_explained():
    with pytest.raises(Exception, match="unknown spec keys"):
        anyio.run(lambda: mcp_server.check_design(doc=DOC, spec={"hight": [1, 2]}))


def test_check_print_finds_a_thin_wall_where_it_is():
    res = tool(mcp_server.check_print, objects=["Thin"])
    assert not res["ok"]
    wall = next(p for p in res["prints"][0]["problems"] if "wall" in p["cause"])
    assert wall["where"][0] > 199 and wall["fix"]
    assert tool(mcp_server.check_print, objects=["Plate"])["ok"]


def test_check_print_handles_a_mesh():
    call("run_python", doc=DOC, code="import Mesh\nm = Mesh.createBox(20, 20, 20)\n"
         f'd = App.getDocument("{DOC}")\no = d.addObject("Mesh::Feature", "M")\no.Mesh = m')
    res = tool(mcp_server.check_print, objects=["M"])
    assert res["ok"] and res["prints"][0]["values"]["closed"]


def test_check_fit():
    res = tool(mcp_server.check_fit, a="Pin", b="Sleeve", kind="sliding")
    assert res["ok"] and res["gap_mm"] == pytest.approx(0.25, abs=0.01)
    tight = tool(mcp_server.check_fit, a="Pin", b="Sleeve", kind="loose")
    assert not tight["ok"] and "0.25" in tight["problem"]["cause"]


def test_flex_test_finds_what_follows_and_what_does_not():
    before = state()
    good = tool(mcp_server.flex_test, params=["Length"])
    assert good["ok"] and good["runs"] == 2
    assert state() == before
    gone = tool(mcp_server.flex_test, params=["Nope"])
    assert not gone["ok"] and "Nope" in gone["problems"][0]["cause"]


def test_inspect_views_gives_a_sheet_and_puts_the_camera_back():
    call("view", action="fit", view="rear")               # jumps: an animated turn would still be moving
    cam = call("run_python", code=f'_result = Gui.getDocument("{DOC}").ActiveView.getCamera()', doc=DOC,
               recompute=False)["result"]
    out = anyio.run(lambda: mcp_server.inspect_views(doc=DOC, section={"axis": "x", "offset": 100}))
    info = json.loads(out[0])
    assert info["size_mm"][0] > 300 and len(info["views"]) == 4 and len(out) == 3
    now = call("run_python", code=f'_result = Gui.getDocument("{DOC}").ActiveView.getCamera()', doc=DOC,
               recompute=False)["result"]
    pose = [[float(x) for x in ln.split()[1:]] for text in (cam, now) for ln in text.splitlines()
            if "orientation" in ln]
    assert pose[0] == pytest.approx(pose[1], abs=1e-4)


def test_check_fit_knows_a_seated_lid():
    call("run_python", doc=DOC, code=f"""
import Part
d = App.getDocument("{DOC}")
box = d.addObject("Part::Feature", "Box")
box.Shape = Part.makeBox(40, 30, 20, Vector(0, 200, 0)).cut(Part.makeBox(36, 26, 20, Vector(2, 202, 2)))
good = d.addObject("Part::Feature", "GoodLid")     # rests on the rim, its plug has 0.2 mm all round
good.Shape = Part.makeBox(40, 30, 3, Vector(0, 200, 20)).fuse(Part.makeBox(35.6, 25.6, 4, Vector(2.2, 202.2, 16)))
tight = d.addObject("Part::Feature", "TightLid")   # the plug touches the walls
tight.Shape = Part.makeBox(40, 30, 3, Vector(0, 200, 20)).fuse(Part.makeBox(36, 26, 4, Vector(2, 202, 16)))
""")
    ok = tool(mcp_server.check_fit, a="Box", b="GoodLid", kind="seated")
    assert ok["ok"] and ok["gap_mm"] == 0 and ok["side_gap_mm"] == pytest.approx(0.2, abs=0.01)
    snug = tool(mcp_server.check_fit, a="Box", b="TightLid", kind="snap")
    assert not snug["ok"] and snug["side_gap_mm"] == 0
