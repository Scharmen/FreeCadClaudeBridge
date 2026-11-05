"""GUI integration: make_part puts standard parts in the document, in one undo step, ready to check."""

import json

import anyio
import pytest

from freecad_bridge import client, mcp_server

pytestmark = pytest.mark.skipif(not client.list_instances(), reason="no running FreeCAD with ClaudeBridge")

DOC = "BridgeITestParts"


def call(op, timeout_s=60, **args):
    return client.call(op, args, timeout_s=timeout_s).result


@pytest.fixture(autouse=True)
def fresh_doc():
    for d in call("status").get("documents", []):
        if d["name"].startswith(DOC):
            call("document", action="close", name=d["name"], force=True)
    call("document", action="new", name=DOC)
    yield
    call("document", action="close", name=DOC, force=True)


def tool(fn, **kw):
    return json.loads(anyio.run(lambda: fn(doc=DOC, **kw)))


def test_listing_needs_no_document():
    out = json.loads(anyio.run(lambda: mcp_server.make_part(family="list")))
    assert "gear_pair" in out["families"] and out["families"]["bolt"]["params"]["size"] == "M8"


def test_a_gear_pair_arrives_in_one_undo_step_and_passes_its_checks():
    out = tool(mcp_server.make_part, family="gear_pair", params={"teeth_a": 18, "teeth_b": 36}, at=[0, 0, 5])
    assert [p["object"] for p in out["parts"]] == ["GearA", "GearB"] and all(p["valid"] for p in out["parts"])
    z = call("run_python", doc=DOC, recompute=False, code=f'''
d = App.getDocument("{DOC}")
a = d.getObject("GearA")
import json
_result = [d.UndoCount, round(a.Shape.BoundBox.ZMin, 3), a.Family, json.loads(a.Parameters)["teeth_a"]]
''')["result"]
    assert z[0] == 1 and z[1] == 5 and z[2] == "gear_pair" and z[3] == 18
    fit = tool(mcp_server.check_fit, a="GearA", b="GearB", kind="custom", gap_mm=[0.0, 0.3])
    assert fit["ok"]


def test_replace_swaps_the_old_part_for_new_sizes():
    tool(mcp_server.make_part, family="gear", params={"teeth": 20})
    out = tool(mcp_server.make_part, family="gear", params={"teeth": 30}, replace=["Gear"])
    assert out["parts"][0]["object"] == "Gear"
    code = f'd = App.getDocument("{DOC}")\n_result = [len(d.Objects), d.Gear.Shape.BoundBox.XLength]'
    size = call("run_python", doc=DOC, recompute=False, code=code)["result"]
    assert size[0] == 1 and size[1] == pytest.approx(32, abs=1)


def test_a_wrong_request_changes_nothing():
    with pytest.raises(Exception, match="odd"):
        anyio.run(lambda: mcp_server.make_part(family="hinge", params={"knuckles": 4}, doc=DOC))
    count = call("run_python", doc=DOC, recompute=False, code=f'_result = len(App.getDocument("{DOC}").Objects)')
    assert count["result"] == 0
