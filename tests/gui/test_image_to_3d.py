"""GUI integration: image_to_3d against a running FreeCAD, plus the settings window."""

import json

import anyio
import pytest
from PIL import Image, ImageDraw

from freecad_bridge import client, mcp_server

pytestmark = pytest.mark.skipif(not client.list_instances(), reason="no running FreeCAD with ClaudeBridge")

DOC = "BridgeITestImg"


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


def tool(**kw):
    out = anyio.run(lambda: mcp_server.image_to_3d(doc=DOC, **kw))
    return json.loads(out[0]), out[1:]


def ring(tmp_path):
    img = Image.new("L", (200, 200), 255)
    d = ImageDraw.Draw(img)
    d.ellipse((20, 20, 180, 180), fill=0)
    d.ellipse((70, 70, 130, 130), fill=255)
    p = tmp_path / "ring.png"
    img.save(p)
    return str(p)


def vase(tmp_path):
    img = Image.new("L", (200, 300), 255)
    d = ImageDraw.Draw(img)
    d.polygon([(60, 290), (140, 290), (170, 180), (120, 60), (130, 10), (70, 10), (80, 60), (30, 180)], fill=0)
    p = tmp_path / "vase.png"
    img.save(p)
    return str(p)


def test_outline_extrude_with_hole(tmp_path):
    res, _ = tool(path=ring(tmp_path), mode="outline", size_mm=40, depth_mm=4)
    assert res["result"]["valid"] and res["holes"] == 1
    x, y, z = res["result"]["size_mm"]
    assert x == pytest.approx(32, rel=0.05) and z == pytest.approx(4, abs=0.01)
    vol = call("get_object", name=res["result"]["object"], doc=DOC)["shape"]["volume"]
    assert float(vol.split()[0]) < 3.1416 * 16 ** 2 * 4, "the hole is cut out"


def test_outline_revolve_stands_upright(tmp_path):
    res, _ = tool(path=vase(tmp_path), mode="outline", size_mm=60, fit="height", revolve=True)
    assert res["result"]["valid"] and res["result"]["solids"] == 1
    x, y, z = res["result"]["size_mm"]
    assert z == pytest.approx(60 * 280 / 300, rel=0.05) and x == pytest.approx(y, rel=0.02)


def test_relief_mesh_is_closed(tmp_path):
    img = Image.new("L", (120, 80), 255)
    ImageDraw.Draw(img).ellipse((20, 10, 100, 70), fill=0)
    p = tmp_path / "relief.png"
    img.save(p)
    res, _ = tool(path=str(p), mode="relief", size_mm=60, max_height_mm=2, base_mm=1, resolution=60)
    assert res["result"]["solid"], "closed mesh, printable"
    assert res["result"]["size_mm"][2] == pytest.approx(3, abs=0.05)


def test_reference_plane_and_show(tmp_path):
    res, images = tool(path=ring(tmp_path), mode="reference", size_mm=50, plane="XZ")
    assert res["result"]["size_mm"] == [50, 50] and images
    meta, images = tool(path=ring(tmp_path), mode="show")
    assert images and meta["path"].endswith("ring.png")


def test_settings_window_is_not_modal():
    code = ("import claudebridge.panel.prefs as P\nP.show()\n"
            "_result = [P._window.isVisible(), P._window.isModal()]\nP._window.reject()")
    assert call("run_python", code=code)["result"] == [True, False]


def pose(camera):
    """Where the camera is and where it looks (the clipping distances are recomputed on every redraw)."""
    return [ln.strip() for ln in camera.splitlines() if ln.strip().startswith(("position", "orientation"))]


def test_compare_to_picture_shows_picture_and_model_and_keeps_the_camera(tmp_path):
    import io

    call("run_python", doc=DOC, code="doc.addObject('Part::Cylinder', 'Vase'); doc.recompute()")
    call("document", action="activate", name=DOC)
    call("view", action="set_view", view="top")
    camera = call("view", action="set_view", return_camera=True)["camera"]
    out = anyio.run(lambda: mcp_server.compare_to_picture(vase(tmp_path), ["current", "front"]))
    meta = json.loads(out[0])
    assert meta["views"] == ["current", "front"] and "missing" in meta["check"]
    assert len(out) == 3
    for img in out[1:]:
        pic = Image.open(io.BytesIO(img.data))
        assert pic.height == 508 and pic.width > 500              # 480 px high plus the label bar
    assert pose(call("view", action="set_view", return_camera=True)["camera"]) == pose(camera)


def test_compare_crop_shows_a_detail_enlarged_next_to_a_close_up(tmp_path):
    import io

    call("run_python", doc=DOC, code="doc.addObject('Part::Cylinder', 'Vase')\n"
         "b = doc.addObject('Part::Box', 'Lip'); b.Length = b.Width = 2; b.Height = 1; doc.recompute()")
    call("document", action="activate", name=DOC)
    call("view", action="fit", view="front")
    camera = call("view", action="set_view", return_camera=True)["camera"]
    out = anyio.run(lambda: mcp_server.compare_to_picture(vase(tmp_path), ["front"], crop=[0.3, 0.0, 0.7, 0.25],
                                                          look_at=["Lip"], zoom=1.5))
    pic = Image.open(io.BytesIO(out[1].data))
    assert pic.height == 508
    left_width = pic.width - 12 - round(480 * 200 / 300)   # the model side is the picture's aspect, 480 high
    assert left_width > 400, "the crop of the top of the vase is shown enlarged"
    assert pose(call("view", action="set_view", return_camera=True)["camera"]) == pose(camera)
    with pytest.raises(mcp_server.ToolError):
        anyio.run(lambda: mcp_server.compare_to_picture(vase(tmp_path), ["front"], crop=[0.9, 0, 0.1, 1]))
