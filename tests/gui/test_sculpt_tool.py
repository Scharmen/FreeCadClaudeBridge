"""GUI integration: the sculpt tool imports closed, matte meshes into a running FreeCAD in one undo step."""

import json

import anyio
import pytest

from freecad_bridge import client, mcp_server

pytestmark = pytest.mark.skipif(not client.list_instances(), reason="no running FreeCAD with ClaudeBridge")

DOC = "BridgeITestSculpt"


def call(op, timeout_s=30, **args):
    return client.call(op, args, timeout_s=timeout_s).result


@pytest.fixture(autouse=True)
def fresh_doc():
    for d in call("status").get("documents", []):
        if d["name"].startswith(DOC):
            call("document", action="close", name=d["name"], force=True)
    call("document", action="new", name=DOC)
    yield
    for d in call("status").get("documents", []):
        if d["name"].startswith(DOC):
            call("document", action="close", name=d["name"], force=True)


def test_sculpt_imports_watertight_matte_parts_as_one_undo_step():
    scene = {"blend": 1.0, "parts": [
        {"name": "Body", "color": [0.85, 0.65, 0.52], "detail": {"amplitude": 0.05, "scale": 1},
         "primitives": [{"type": "ellipsoid", "center": [0, 0, 8], "radii": [5, 3.5, 8]},
                        {"type": "sphere", "center": [0, 0, 19], "radius": 3.5}]},
        {"name": "Hat", "color": [0.6, 0.6, 0.62], "shininess": 0.4,
         "primitives": [{"type": "torus", "center": [0, 0, 21], "radius": 3.6, "thickness": 0.8}]},
        {"name": "Loose", "primitives": [{"type": "sphere", "center": [0, 0, 0], "radius": 2},
                                         {"type": "sphere", "center": [12, 0, 0], "radius": 2}]}]}
    full = anyio.run(lambda: mcp_server.sculpt(scene, name="Figure", voxel_mm=0.3, doc=DOC))
    res = json.loads(full[0])
    assert not res.get("errors"), res
    assert len(full) == 2 and full[1].data[:4] == b"\x89PNG", "a front and side preview comes with the answer"
    assert len(full[1].data) < 120_000, "a small picture: two views of 400 x 300"
    parts = {p["object"]: p for p in res["result"]["parts"]}
    assert set(parts) == {"Body", "Hat", "Loose"}
    assert all(p["watertight"] for p in parts.values())
    assert parts["Body"]["size_mm"][2] == pytest.approx(22.5, abs=0.8)
    assert "Loose" in res["part_warnings"] and "Body" not in res["part_warnings"]
    looks = call("run_python", doc=DOC, code=(
        "_result = {o.Name: [o.ViewObject.ShapeAppearance[0].Shininess, o.ViewObject.CreaseAngle,"
        " [round(c, 2) for c in o.ViewObject.ShapeAppearance[0].DiffuseColor[:3]]]"
        " for o in doc.Objects if o.TypeId == 'Mesh::Feature'}"))["result"]
    assert looks["Body"][0] == pytest.approx(0) and looks["Hat"][0] == pytest.approx(0.4, abs=0.01)
    assert looks["Body"][1] == pytest.approx(70) and looks["Body"][2] == [0.85, 0.65, 0.52]
    call("run_python", doc=DOC, code="doc.undo()")
    gone = call("run_python", doc=DOC, code="_result = len(doc.Objects)")["result"]
    assert gone == 0, "the whole sculpt is one undo step"


def test_one_piece_imports_a_single_closed_mesh_for_printing():
    scene = {"template": "cat_sitting", "height": 40,
             "parts": [{"name": "Collar", "color": [0.9, 0.1, 0.1],
                        "primitives": [{"type": "torus", "center": [0, -4, 26], "radius": 5.5, "thickness": 0.8,
                                        "rotate": [80, 0, 0]}]}]}
    full = anyio.run(lambda: mcp_server.sculpt(scene, name="Cat", one_piece=True, voxel_mm=0.25, doc=DOC))
    res = json.loads(full[0])
    assert not res.get("part_warnings"), res
    parts = res["result"]["parts"]
    assert len(parts) == 1 and parts[0]["watertight"] is True
    assert parts[0]["size_mm"][2] == pytest.approx(40, abs=1.0)
    count = call("run_python", doc=DOC, code="_result = sum(1 for o in doc.Objects if o.TypeId == 'Mesh::Feature')",
                 recompute=False)["result"]
    assert count == 1
