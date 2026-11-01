"""GUI integration tests for the Phase 2 tools (needs a running FreeCAD with ClaudeBridge)."""

import base64
import json
import os
import tempfile
import time

import anyio
import pytest

from freecad_bridge import client, headless, protocol

pytestmark = pytest.mark.skipif(not client.list_instances(), reason="no running FreeCAD with ClaudeBridge")

DOC = "BridgeITest2"


def call(op, timeout_s=30, **args):
    return client.call(op, args, timeout_s=timeout_s).result


def py(code, **kw):
    return call("run_python", code=code, doc=kw.pop("doc", DOC), **kw)


@pytest.fixture(autouse=True)
def fresh_doc():
    if call("task_dialog")["open"]:
        call("task_dialog", action="close")
    for d in call("status").get("documents", []):
        if d["name"].startswith(DOC):
            call("document", action="close", name=d["name"], force=True)
    call("document", action="new", name=DOC)
    yield
    if call("task_dialog")["open"]:
        call("task_dialog", action="close")
    for d in call("status").get("documents", []):
        if d["name"].startswith(DOC):
            call("document", action="close", name=d["name"], force=True)


RECT_SKETCH = """
sk = doc.addObject("Sketcher::SketchObject", "Sketch")
p = [Vector(0,0,0), Vector(30,0,0), Vector(30,20,0), Vector(0,20,0)]
for i in range(4):
    sk.addGeometry(Part.LineSegment(p[i], p[(i+1)%4]))
for i in range(4):
    sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i+1)%4, 1))
sk.addConstraint(Sketcher.Constraint("Horizontal", 0)); sk.addConstraint(Sketcher.Constraint("Horizontal", 2))
sk.addConstraint(Sketcher.Constraint("Vertical", 1)); sk.addConstraint(Sketcher.Constraint("Vertical", 3))
"""


def test_inspect_shape_faces_edges_vertices():
    py("doc.addObject('Part::Box','Box'); c = doc.addObject('Part::Cylinder','Cyl'); c.Radius = 4")
    faces = call("inspect_shape", name="Box", doc=DOC)
    assert faces["total"] == 6 and faces["items"][0]["ref"] == "Face1"
    assert {f["type"] for f in faces["items"]} == {"Plane"}
    assert faces["items"][0]["area"] == "100 mm²"
    assert len(faces["items"][0]["normal"]) == 3
    edges = call("inspect_shape", name="Box", level="edges", doc=DOC, limit=5, offset=10)
    assert edges["total"] == 12 and [e["ref"] for e in edges["items"]] == ["Edge11", "Edge12"]
    assert edges["items"][0]["length"] == "10 mm" and edges["more"] is False
    cyl = call("inspect_shape", name="Cyl", doc=DOC)
    side = next(f for f in cyl["items"] if f["type"] == "Cylinder")
    assert side["radius"] == "4 mm" and [abs(c) for c in side["axis"]] == [0.0, 0.0, 1.0]
    assert call("inspect_shape", name="Box", level="vertices", doc=DOC)["total"] == 8


def test_inspect_sketch_dof_and_conflicts():
    py(RECT_SKETCH)
    s = call("inspect_sketch", name="Sketch", doc=DOC)
    assert s["dof"] == 4 and not s["fully_constrained"]
    assert len(s["geometry"]) == 4 and s["geometry"][0]["type"] == "LineSegment"
    assert s["constraints"][0]["type"] == "Coincident"
    py("sk = doc.Sketch\n"
       "sk.addConstraint(Sketcher.Constraint('DistanceX', 0, 1, 0, 2, 30))\n"
       "sk.addConstraint(Sketcher.Constraint('DistanceY', 1, 1, 1, 2, 20))\n"
       "sk.addConstraint(Sketcher.Constraint('Coincident', 0, 1, -1, 1))")
    s = call("inspect_sketch", name="Sketch", doc=DOC)
    assert s["dof"] == 0 and s["fully_constrained"] and s["conflicting"] == []
    assert any(c.get("value") == "30 mm" for c in s["constraints"])
    py("doc.Sketch.addConstraint(Sketcher.Constraint('DistanceX', 0, 1, 0, 2, 40))")
    s = call("inspect_sketch", name="Sketch", doc=DOC)
    assert s["conflicting"] or s["redundant"]


def test_get_changes_tracks_user_and_claude():
    start = call("get_changes", since=0, limit=1)["latest_seq"]
    py("doc.addObject('Part::Box','Box')")
    # a change made after the call returns counts as the user's
    py("from PySide import QtCore\nQtCore.QTimer.singleShot(0, lambda: setattr(doc.Box, 'Length', 33))")
    time.sleep(0.3)
    ch = call("get_changes", since=start)
    claude = [e for e in ch["events"] if e["source"] == "claude" and e["event"] == "created"]
    user = [e for e in ch["events"] if e["source"] == "user" and e["event"] == "changed"]
    assert [e["object"] for e in claude] == ["Box"]
    assert user and "Length" in user[0]["props"]
    assert call("get_changes", since=ch["latest_seq"], source="user")["events"] == []


def test_get_console_captures_report_view():
    start = call("get_console", level="log", limit=1)["latest_seq"]
    py("App.Console.PrintWarning('bridge-console-test-warning\\n')\n"
       "App.Console.PrintError('bridge-console-test-error\\n')")
    time.sleep(0.3)
    out = call("get_console", since=start, level="warning")
    texts = {ln["text"]: ln["level"] for ln in out["lines"]}
    assert texts.get("bridge-console-test-warning") == "warning"
    assert texts.get("bridge-console-test-error") == "error"


def test_api_help_variants():
    f = call("api_help", target="Part.makeBox")
    assert "box" in f["doc"].lower()
    t = call("api_help", target="PartDesign::Pad")
    length = next(p for p in t["properties"] if p["name"] == "Length")
    assert length["type"].startswith("App::PropertyLength") and length.get("doc")
    py("doc.addObject('Part::Box','Box')")
    o = call("api_help", target="Box")
    assert o["type"] == "Part::Box" and any(p["name"] == "Height" for p in o["properties"])
    assert "ClaudeApiHelp" not in [d["name"] for d in call("status")["documents"]]
    with pytest.raises(client.BridgeError) as e:
        call("api_help", target="Part.doesNotExist")
    assert e.value.code == protocol.NOT_FOUND


def test_list_commands():
    out = call("list_commands", filter="fit all")
    assert any(c["name"] == "Std_ViewFitAll" and c["menu"] == "Fit All" for c in out["commands"])
    pd = call("list_commands", workbench="PartDesignWorkbench", filter="pad")
    assert any(c["name"] == "PartDesign_Pad" for c in pd["commands"])


def test_run_command_and_task_dialog():
    r = call("run_command", name="Part_Box")
    assert any(c.endswith("#Box") for c in r["changes"]["created"])
    r = call("run_command", name="Part_Primitives")
    assert r["task_dialog"]["open"] and "hint" in r
    with pytest.raises(client.BridgeError) as e:
        call("run_command", name="Std_ViewFitAll")
    assert e.value.code == protocol.TASK_DIALOG_OPEN
    assert call("task_dialog")["open"] is True
    out = call("task_dialog", action="reject")
    assert out["dialog_after"]["open"] is False
    with pytest.raises(client.BridgeError):
        call("run_command", name="No_Such_Command")


def test_selection_roundtrip():
    py("doc.addObject('Part::Box','Box'); doc.addObject('Part::Box','Box001')")
    out = call("selection", action="set", items=["Box.Face1", {"object": "Box001"}])
    sel = {(s["object"], tuple(s["sub"])) for s in out["selection"]}
    assert sel == {("Box", ("Face1",)), ("Box001", ())}
    assert call("status")["selection"][0]["object"] == "Box"
    assert call("selection", action="clear")["selection"] == []


def test_import_step_roundtrip():
    py("b = doc.addObject('Part::Box','Box'); b.Length = 17")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "box.step")
        call("export", path=path, doc=DOC)
        call("document", action="new", name=DOC + "Imp")
        r = call("import", path=path, doc=DOC + "Imp")
        assert r["document"] == DOC + "Imp" and len(r["created"]) >= 1 and r["errors"] == []
        vol = py("_result = sum(o.Shape.Volume for o in doc.Objects if hasattr(o, 'Shape'))", doc=DOC + "Imp")
        assert round(vol["result"]) == 1700


def test_view_and_screenshot():
    py("doc.addObject('Part::Box','A'); doc.addObject('Part::Box','B')")
    call("document", action="activate", name=DOC)
    out = call("view", action="set_view", view="iso")
    assert set(out["visible"]) >= {"A", "B"}
    out = call("view", action="isolate", objects=["A"])
    assert out["visible"] == ["A"]
    shot = call("view", action="fit", screenshot=True, width=320, height=240)
    png = base64.b64decode(shot["image_png_base64"])
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 1000


def test_snapshots_and_close_without_saving():
    py("doc.addObject('Part::Box','Box')")
    c = call("snapshot", action="create", doc=DOC)
    assert c["snapshot"].endswith(".FCStd") and os.path.exists(c["path"])
    assert call("snapshot", action="list", doc=DOC)["snapshots"][0]["name"] == c["snapshot"]
    r = call("snapshot", action="restore", doc=DOC)
    restored = r["opened_as"]
    assert py("_result = [o.Name for o in doc.Objects]", doc=restored)["result"] == ["Box"]
    call("document", action="close", name=restored, force=True)
    time.sleep(1.1)  # distinct timestamp
    closed = call("document", action="close", name=DOC, force=True)
    assert os.path.exists(closed["snapshot"])
    call("document", action="new", name=DOC)


def test_run_python_snapshot_flag():
    py("doc.addObject('Part::Box','Box')")
    r = py("doc.Box.Length = 99", snapshot=True)
    assert os.path.exists(r["snapshot"])


def test_trace_hook_stops_infinite_loop_and_rolls_back():
    t0 = time.monotonic()
    with pytest.raises(client.BridgeError) as e:
        client.call("run_python", {"code": "doc.addObject('Part::Box','Loop')\nwhile True:\n    pass", "doc": DOC},
                    timeout_s=4)
    assert e.value.code == protocol.TIMEOUT
    assert "rolled back" in e.value.message
    assert time.monotonic() - t0 < 6
    assert py("_result = [o.Name for o in doc.Objects]")["result"] == []


def test_responses_stay_small():
    py("for i in range(400):\n    doc.addObject('Part::Box', f'B{i}')", timeout_s=60)
    text = json.dumps(call("get_tree", doc=DOC))
    assert len(text) < 38000
    assert call("get_tree", doc=DOC)["more"] is True


def test_run_headless():
    script = "import Part\nb = Part.makeBox(10, 20, 30)\nprint('computed')\n_result = {'volume': b.Volume}"
    out = anyio.run(headless.run_headless, script, 120)
    assert out["ok"] and out["result"] == {"volume": 6000.0}
    assert out["output"] == "computed"
    bad = anyio.run(headless.run_headless, "raise ValueError('nope')", 120)
    assert not bad["ok"] and "ValueError: nope" in bad["output"]
    slow = anyio.run(headless.run_headless, "import time\ntime.sleep(30)", 3)
    assert slow["timed_out"] and not slow["ok"]
