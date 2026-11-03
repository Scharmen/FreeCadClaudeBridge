"""GUI integration: design notes are stored in the document as a text object, one undo step per call."""

import json

import anyio
import pytest

from freecad_bridge import client, mcp_server

pytestmark = pytest.mark.skipif(not client.list_instances(), reason="no running FreeCAD with ClaudeBridge")

DOC = "BridgeITestNotes"


def call(op, timeout_s=30, **args):
    return client.call(op, args, timeout_s=timeout_s).result


@pytest.fixture(autouse=True)
def fresh_doc():
    for d in call("status").get("documents", []):
        if d["name"].startswith(DOC):
            call("document", action="close", name=d["name"], force=True)
    call("document", action="new", name=DOC)
    yield
    call("document", action="close", name=DOC, force=True)


def tool(**kw):
    return json.loads(anyio.run(lambda: mcp_server.design_notes(doc=DOC, **kw)))


def test_notes_accumulate_in_a_text_object_and_can_be_listed():
    first = tool(action="add", topic="Labrador body", notes=[
        {"part": "Body", "decision": "barrel chest, 1.1 x as long as tall", "reason": "breed proportions",
         "source": "reference"}])
    assert first["result"]["object"] == "DesignNotes" and first["result"]["entries"] == 1
    tool(action="add", topic="Head", notes=[{"part": "Ears", "decision": "hanging, to the jaw line",
                                            "reason": "seen in your picture", "source": "picture"}])
    listed = tool(action="list")["result"]
    assert "Labrador body" in listed["text"] and "Ears" in listed["text"] and listed["entries"] == 2
    label = call("run_python", doc=DOC, code="_result = doc.getObject('DesignNotes').Label")["result"]
    assert label == "Design notes"
    call("run_python", doc=DOC, code="doc.undo()")
    assert "Ears" not in tool(action="list")["result"]["text"], "each call is one undo step"


def test_listing_without_notes_says_so():
    assert tool(action="list")["result"] == {"text": "", "entries": 0}
