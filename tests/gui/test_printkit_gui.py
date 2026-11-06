# ruff: noqa: E501
"""The print export inside FreeCAD: the button, what it reads from the document, the PDF guide."""

import json
import re
from pathlib import Path

import pytest

from freecad_bridge import client

pytestmark = pytest.mark.skipif(not client.list_instances(), reason="no running FreeCAD with ClaudeBridge")
DOC = "BridgeITestPrint"
MODEL = """
V = App.Vector
def add(name, shape, color):
    o = doc.addObject("Part::Feature", name)
    o.Shape = shape
    o.ViewObject.ShapeColor = color
    return o
add("Body", Part.makeBox(100, 40, 25, V(0, -20, 10)), (0.04, 0.35, 0.17))
add("Roof", Part.makeBox(50, 30, 15, V(25, -15, 35)), (0, 0, 0))
add("Hidden", Part.makeBox(5, 5, 5), (1, 0, 0)).ViewObject.Visibility = False
for name, x, y0 in (("FL", 20, 20.5), ("RL", 80, 20.5), ("FR", 20, -30.5), ("RR", 80, -30.5)):
    y = V(0, 1, 0)
    add("Tyre_" + name, Part.makeCylinder(12, 10, V(x, y0, 12), y).cut(Part.makeCylinder(8, 10, V(x, y0, 12), y)), (0.08, 0.08, 0.08))
    add("Rim_" + name, Part.makeCylinder(8, 10, V(x, y0, 12), y), (0.7, 0.7, 0.72))
doc.recompute()
"""


def call(op, timeout_s=120, **args):
    return client.call(op, args, timeout_s=timeout_s).result


def py(code, timeout_s=300):
    return call("run_python", code=code, doc=DOC, timeout_s=timeout_s, timeout_s_=None) if False else client.call(
        "run_python", {"code": code, "doc": DOC, "timeout_s": timeout_s}, timeout_s=timeout_s + 10).result["result"]


@pytest.fixture(autouse=True)
def fresh():
    for d in call("status").get("documents", []):
        if d["name"].startswith(DOC):
            call("document", action="close", name=d["name"], force=True)
    call("document", action="new", name=DOC)
    client.call("run_python", {"code": MODEL, "doc": DOC})
    yield
    for d in call("status").get("documents", []):
        if d["name"].startswith(DOC):
            call("document", action="close", name=d["name"], force=True)


def test_the_button_exists_as_a_command():
    assert py("import FreeCADGui as G\n_result = 'ClaudeBridge_PrintExport' in G.listCommands()") is True


def test_only_what_is_visible_is_offered_with_its_colour():
    r = py("from claudebridge.printkit import dialog\nitems = dialog.collect(doc)\n_result = "
           "{o['name']: o['color'] for o in items}")
    assert "Hidden" not in r and "Body" in r and r["Roof"] == "#000000" and re.fullmatch(r"#[0-9a-f]{6}", r["Body"])


def test_the_wheels_are_suggested_as_moving():
    r = py("from claudebridge.printkit import dialog, plan\n_result = plan.detect_moving(dialog.collect(doc))")
    assert len(r) == 4 and all(m["axis"] == "y" and m["checked"] for m in r)


def test_the_dialog_asks_for_printer_and_assembly_and_opens_nothing_by_itself():
    r = py("""
from claudebridge.printkit import dialog
d = dialog.PrintDialog(doc)
opts = d.options()
d.deleteLater()
_result = {"printer": opts["printer"]["name"], "join": opts["join"], "moving": len(opts["moving"]),
           "whole": opts["whole"], "split": opts["split"], "visible": d.isVisible()}
""")
    assert r["printer"] and r["join"] in ("pins", "glue") and r["moving"] == 4 and r["whole"] and r["split"]
    assert r["visible"] is False


def test_export_creates_files_and_a_guide(tmp_path):
    folder = str(tmp_path / "kit")
    r = py(f"""
from claudebridge.printkit import dialog
d = dialog.PrintDialog(doc)
opts = d.options()
d.deleteLater()
_result = dialog.export_now(doc, opts, {folder!r})
""", timeout_s=280)
    assert r["ok"], r
    pdf = Path(r["guide"])
    data = pdf.read_bytes()
    assert data[:4] == b"%PDF" and len(data) > 5000
    assert len(re.findall(rb"/Type\s*/Page[^s]", data)) >= 4, "cover, parts, at least one step and tips"
    report = json.loads((Path(folder) / "report.json").read_text())
    assert report["whole"]["fits"] and report["axles"]["count"] == 4
    assert (Path(folder) / report["whole"]["file"]).is_file()


def test_the_guide_is_written_in_both_languages(tmp_path):
    r = py(f"""
import json
from claudebridge.printkit import dialog, guide
d = dialog.PrintDialog(doc)
opts = d.options()
d.deleteLater()
out = dialog.export_now(doc, opts, {str(tmp_path / 'kit')!r})
report = json.load(open({str(tmp_path / 'kit' / 'report.json')!r}))
report["lang"] = "en"
_result = guide.build(report, {str(tmp_path / 'kit')!r}, {str(tmp_path / 'en.pdf')!r})
""", timeout_s=280)
    assert Path(r).read_bytes()[:4] == b"%PDF"


def test_the_button_is_in_the_menu_and_toolbar_of_a_normal_workbench():
    r = py("""
import FreeCADGui as G
from PySide import QtGui, QtWidgets
Action = getattr(QtGui, "QAction", None) or QtWidgets.QAction
G.activateWorkbench("PartWorkbench")
G.updateGui()
w = G.getMainWindow()
actions = [a for a in w.findChildren(Action) if a.text().startswith("3D print export")]
bars = [b.objectName() for b in w.findChildren(QtWidgets.QToolBar)
        if any(a.text().startswith("3D print export") for a in b.actions())]
_result = {"actions": len(actions), "bars": bars}
""")
    assert r["actions"] >= 1 and r["bars"], r


def test_the_create_button_runs_the_export_in_the_background(tmp_path):
    folder = str(tmp_path / "kit")
    r = py(f"""
from claudebridge.printkit import dialog
d = dialog.PrintDialog(doc)
d.folder.setText({folder!r})
d.start()
started = d.bar.isVisible() or not d.start_btn.isEnabled()
assert d.proc.waitForFinished(240000)
d._finished()
_result = {{"started": started, "state": d.state.text(), "open": not d.open_btn.isHidden()}}
d.deleteLater()
""".replace("{folder!r}", repr(folder)).replace("{{{{", "{{").replace("}}}}", "}}"), timeout_s=280)
    assert r["started"] and r["open"], r
    assert any(Path(folder).glob("*.pdf"))


def test_a_picture_of_the_dialog(tmp_path):
    r = py(f"""
from claudebridge.printkit import dialog
d = dialog.PrintDialog(doc)
d.show()
d.adjustSize()
FreeCADGui_ = __import__("FreeCADGui")
FreeCADGui_.updateGui()
ok = d.grab().save({"'/private/tmp/claude-501/-Users-kevin-StudioProjects-FreeCadClaudeBridge/84ab89a9-5ebb-45d4-ba6a-619392c88397/scratchpad/v2/dialog.png'"})
d.close()
d.deleteLater()
_result = ok
""")
    assert r is True
