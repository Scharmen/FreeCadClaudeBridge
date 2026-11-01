"""GUI integration: runs against a live FreeCAD with the ClaudeBridge addon.

Start FreeCAD first, then: `make itest` (or uv run --project plugin/server pytest tests/gui).
Skipped automatically when no FreeCAD instance is running.

Covers the Phase 1 gate items that don't need Claude: handshake, one undo
step per call, rollback on error, changes/errors, read tools, export, busy handling.
"""

import os
import tempfile
import threading
import time

import pytest

from freecad_bridge import __version__, client, protocol

pytestmark = pytest.mark.skipif(not client.list_instances(), reason="no running FreeCAD with ClaudeBridge")

DOC = "BridgeITest"


def call(op, **args):
    return client.call(op, args, timeout_s=args.pop("timeout_s", 30)).result


def py(code, **kw):
    return call("run_python", code=code, doc=kw.pop("doc", DOC), **kw)


@pytest.fixture(autouse=True)
def fresh_doc():
    names = [d["name"] for d in call("status").get("documents", [])]
    if DOC in names:
        call("document", action="close", name=DOC, force=True)
    call("document", action="new", name=DOC)
    yield
    call("document", action="close", name=DOC, force=True)


def test_status_and_versions():
    s = call("status")
    assert s["connected"] and s["addon_version"] == __version__
    assert s["freecad_version"].startswith("1.")
    assert s["active_document"] == DOC
    assert s["busy"] is False
    assert isinstance(s["undo_limit"], int)


def test_run_python_is_one_undo_step():
    r = py("a = doc.addObject('Part::Box', 'A'); b = doc.addObject('Part::Box', 'B'); _result = len(doc.Objects)")
    assert r["result"] == 2
    assert sorted(r["changes"]["created"]) == ["A", "B"]
    assert r["errors"] == []
    u = call("document", action="undo", name=DOC)
    assert u["did"] == "Claude"
    assert py("_result = len(doc.Objects)")["result"] == 0


def test_named_transaction():
    py("doc.addObject('Part::Box', 'T')", transaction="Make T")
    assert call("document", action="undo", name=DOC)["did"] == "Make T"


def test_faulty_code_leaves_nothing():
    with pytest.raises(client.BridgeError) as e:
        py("doc.addObject('Part::Box', 'Ghost')\nraise ValueError('boom')")
    assert e.value.code == protocol.PY_EXCEPTION
    assert "ValueError: boom" in e.value.message
    assert '<claude>", line 2' in e.value.traceback
    assert py("_result = [o.Name for o in doc.Objects]")["result"] == []


def test_stdout_and_persistent_namespace():
    r = py("print('hello'); keep = 41")
    assert r["stdout"].strip() == "hello"
    assert py("_result = keep + 1")["result"] == 42


def test_errors_reported_for_invalid_object():
    r = py("b = doc.addObject('Part::Box', 'Flat'); b.Height = 0")
    assert [e["name"] for e in r["errors"]] == ["Flat"]


def test_changed_objects_reported():
    py("doc.addObject('Part::Box', 'C')")
    r = py("doc.C.Length = 25")
    assert r["changes"]["changed"] == ["C"] and r["changes"]["created"] == []


def test_get_tree_and_get_object():
    py("body = doc.addObject('PartDesign::Body', 'Body'); box = doc.addObject('Part::Box', 'Box'); box.Length = 12")
    tree = call("get_tree", doc=DOC)
    names = {o["name"]: o for o in tree["objects"]}
    assert "Box" in names and "Body" in names
    assert names["Box"]["type"] == "Part::Box"
    obj = call("get_object", name="Box", doc=DOC)
    assert obj["properties"]["Length"] == "12 mm"
    assert obj["shape"]["volume"] == "1200 mm³"
    assert obj["shape"]["faces"] == 6
    assert call("get_object", name="Box", doc=DOC, props=["Height"])["properties"] == {"Height": "10 mm"}


def test_get_object_unknown():
    with pytest.raises(client.BridgeError) as e:
        call("get_object", name="Nope", doc=DOC)
    assert e.value.code == protocol.NOT_FOUND


def test_export_step_and_stl():
    py("doc.addObject('Part::Box', 'Box')")
    with tempfile.TemporaryDirectory() as d:
        for ext in ("step", "stl"):
            r = call("export", path=os.path.join(d, f"box.{ext}"), doc=DOC)
            assert r["objects"] == ["Box"] and r["size_bytes"] > 100 and os.path.exists(r["path"])


def test_save_and_close_guard():
    py("doc.addObject('Part::Box', 'Box')")
    with tempfile.TemporaryDirectory() as d:
        r = call("document", action="save_as", name=DOC, path=os.path.join(d, "t"))
        assert r["file"].endswith("t.FCStd") and os.path.exists(r["file"])
        py("doc.Box.Length = 3")
        with pytest.raises(client.BridgeError) as e:
            call("document", action="close", name=DOC)
        assert "unsaved" in e.value.message


def test_ping_and_status_while_gui_busy():
    """The listener answers while the GUI thread is blocked; a parallel call waits instead of failing."""
    t = threading.Thread(target=lambda: py("import time; time.sleep(3)"))
    t.start()
    time.sleep(0.5)
    t0 = time.perf_counter()
    assert client.call("ping", timeout_s=5).result["busy"] is True
    st = call("status")
    assert st["busy"] is True
    assert time.perf_counter() - t0 < 1.0
    r = py("_result = 'after'")  # queued behind the sleep, not GUI_BUSY
    assert r["result"] == "after"
    t.join()


def test_timeout():
    with pytest.raises(client.BridgeError) as e:
        client.call("run_python", {"code": "import time; time.sleep(4)", "doc": DOC}, timeout_s=1)
    assert e.value.code == protocol.TIMEOUT
    time.sleep(3.5)  # let the GUI thread finish before the next test


def test_unknown_op():
    with pytest.raises(client.BridgeError) as e:
        call("format_disk")
    assert e.value.code == protocol.NOT_FOUND


def test_code_closing_its_own_document():
    other = "BridgeITestClose"
    call("document", action="new", name=other)
    r = call("run_python", code=f"App.closeDocument('{other}')", doc=other)
    assert other not in [d["name"] for d in call("status")["documents"]]
    assert r["changes"]["deleted"] == [] and r["errors"] == []
    call("document", action="activate", name=DOC)
