"""The button's dialog: asks for the printer and how to assemble, only when the export is used, then runs it."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from ..bridge import childenv
from . import guide, plan

PARAMS = "User parameter:BaseApp/Preferences/Mod/ClaudeBridge/PrintExport"
SCRIPT = """\
import json, sys
if not getattr(sys, "_fcb_print_export", False):   # freecadcmd can run a script twice
    sys._fcb_print_export = True
    sys.path.insert(0, {addon!r})
    from claudebridge.printkit import engine
    report = engine.run({path!r}, json.load(open({options!r}, encoding="utf-8")), {out!r})
    print("\\nFCB_PRINT_OK", flush=True)
"""
_open: list[Any] = []


def language() -> str:
    return "de" if QtCore.QLocale.system().name().startswith("de") else "en"


def _hex(color: Any) -> str:
    return "#" + "".join(f"{min(255, max(0, round(c * 255))):02x}" for c in color[:3])


def _object_color(o: Any) -> str:
    v = getattr(o, "ViewObject", None)
    if v is None:
        return "#808080"
    appearance = getattr(v, "ShapeAppearance", None)
    if appearance:
        return _hex(appearance[0].DiffuseColor)
    return _hex(getattr(v, "ShapeColor", (0.5, 0.5, 0.5)))


def collect(doc: Any) -> list[dict[str, Any]]:
    """The visible results of the document: name, label, colour and bounding box."""
    out = []
    for o in doc.Objects:
        view = getattr(o, "ViewObject", None)
        if view is None or not view.Visibility or o.TypeId in ("App::Part", "App::DocumentObjectGroup"):
            continue
        if o.TypeId.startswith("Mesh::"):
            box = o.Mesh.BoundBox
        elif hasattr(o, "Shape") and o.Shape.Solids:
            box = o.Shape.BoundBox
        else:
            continue
        out.append({"name": o.Name, "label": o.Label, "color": _object_color(o),
                    "lo": [box.XMin, box.YMin, box.ZMin], "hi": [box.XMax, box.YMax, box.ZMax]})
    return out


def default_folder(doc: Any) -> str:
    from ..bridge import ops

    return os.path.join(ops.project_folder(doc), "print")


class PrintDialog(QtWidgets.QDialog):
    def __init__(self, doc: Any, parent: Any = None) -> None:
        super().__init__(parent)
        self.doc = doc
        self.lang = language()
        self.t = plan.TEXT[self.lang]
        self.items = collect(doc)
        self.params = App.ParamGet(PARAMS)
        self.setWindowTitle(self.t["dialog"])
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.printer = QtWidgets.QComboBox()
        self.printer.addItems([*plan.PRINTERS, self.t["custom"]])
        saved = self.params.GetString("Printer", "")
        if saved and self.printer.findText(saved) >= 0:
            self.printer.setCurrentText(saved)
        self.printer.currentTextChanged.connect(self._printer_changed)
        form.addRow(self.t["printer"], self.printer)
        self.bed = [self._spin(50, 2000, 250) for _ in range(3)]
        bed_row = QtWidgets.QHBoxLayout()
        for s in self.bed:
            bed_row.addWidget(s)
        form.addRow(self.t["bed"], bed_row)
        self.nozzle = QtWidgets.QDoubleSpinBox()
        self.nozzle.setRange(0.02, 1.2)
        self.nozzle.setSingleStep(0.1)
        self.nozzle.setValue(0.4)
        form.addRow(self.t["nozzle"], self.nozzle)
        self.slots = self._spin(1, 16, 1)
        form.addRow(self.t["slots"], self.slots)
        self.process = QtWidgets.QComboBox()
        self.process.addItems(["FDM", "Resin"])
        form.addRow(self.t["process"], self.process)
        self.guide_lang = QtWidgets.QComboBox()
        self.guide_lang.addItems(["Deutsch", "English"])
        chosen = self.params.GetString("Language", self.lang)
        self.guide_lang.setCurrentIndex(0 if chosen == "de" else 1)
        form.addRow(self.t["guide_lang"], self.guide_lang)
        lay.addLayout(form)
        self.whole = QtWidgets.QCheckBox(self.t["whole"])
        self.whole.setChecked(True)
        self.split = QtWidgets.QCheckBox(self.t["split"])
        lay.addWidget(self.whole)
        lay.addWidget(self.split)
        box = QtWidgets.QGroupBox(self.t["join"])
        inner = QtWidgets.QVBoxLayout(box)
        self.pins = QtWidgets.QRadioButton(self.t["pins"])
        self.glue = QtWidgets.QRadioButton(self.t["glue"])
        (self.glue if self.params.GetString("Join", "pins") == "glue" else self.pins).setChecked(True)
        inner.addWidget(self.pins)
        inner.addWidget(self.glue)
        lay.addWidget(box)
        move = QtWidgets.QGroupBox(self.t["moving"])
        move_lay = QtWidgets.QVBoxLayout(move)
        self.moving = plan.detect_moving(self.items)
        label = {o["name"]: o["label"] for o in self.items}
        self.moving_boxes = []
        for m in self.moving:
            check = QtWidgets.QCheckBox(", ".join(label[n] for n in m["objects"]))
            check.setChecked(bool(m["checked"]))
            move_lay.addWidget(check)
            self.moving_boxes.append(check)
        if not self.moving:
            move_lay.addWidget(QtWidgets.QLabel(self.t["none_moving"]))
        lay.addWidget(move)
        row = QtWidgets.QHBoxLayout()
        self.folder = QtWidgets.QLineEdit(default_folder(doc))
        pick = QtWidgets.QPushButton("…")
        pick.clicked.connect(self._pick)
        row.addWidget(QtWidgets.QLabel(self.t["folder"]))
        row.addWidget(self.folder, 1)
        row.addWidget(pick)
        lay.addLayout(row)
        self.state = QtWidgets.QLabel("")
        self.state.setWordWrap(True)
        lay.addWidget(self.state)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.hide()
        lay.addWidget(self.bar)
        buttons = QtWidgets.QHBoxLayout()
        self.open_btn = QtWidgets.QPushButton(self.t["open"])
        self.open_btn.clicked.connect(self._open_folder)
        self.open_btn.hide()
        self.start_btn = QtWidgets.QPushButton(self.t["start"])
        self.start_btn.setDefault(True)
        self.start_btn.clicked.connect(self.start)
        buttons.addStretch(1)
        buttons.addWidget(self.open_btn)
        buttons.addWidget(self.start_btn)
        lay.addLayout(buttons)
        self.proc: Any = None
        self.job: dict[str, str] = {}
        self._printer_changed(self.printer.currentText())

    @staticmethod
    def _spin(low: int, high: int, value: int) -> Any:
        s = QtWidgets.QSpinBox()
        s.setRange(low, high)
        s.setValue(value)
        return s

    def _printer_changed(self, text: str) -> None:
        preset = plan.PRINTERS.get(text)
        for w in (*self.bed, self.nozzle, self.slots, self.process):
            w.setEnabled(preset is None)
        if preset:
            for s, v in zip(self.bed, preset["bed"], strict=True):
                s.setValue(v)
            self.nozzle.setValue(preset["nozzle"])
            self.slots.setValue(preset["slots"])
            self.process.setCurrentIndex(1 if preset["process"] == "resin" else 0)
        self._split_default()

    def _split_default(self) -> None:
        """One print unless it cannot work: too many colours for the printer, or too big for its bed."""
        colors = len(set(plan.group_colors({o["name"]: o["color"] for o in self.items}).values()))
        fits = all(max(o["hi"][i] for o in self.items) - min(o["lo"][i] for o in self.items) <= s.value()
                   for i, s in enumerate(self.bed)) if self.items else True
        self.split.setChecked(plan.split_needed(colors, self.slots.value(), fits))

    def _pick(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(self, self.t["folder"], self.folder.text())
        if path:
            self.folder.setText(path)

    def _open_folder(self) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(self.folder.text()))

    def options(self) -> dict[str, Any]:
        chosen = [{k: m[k] for k in ("objects", "axis", "axle")}
                  for m, c in zip(self.moving, self.moving_boxes, strict=True) if c.isChecked()]
        return {"name": self.doc.Label, "lang": "de" if self.guide_lang.currentIndex() == 0 else "en",
                "whole": self.whole.isChecked(),
                "split": self.split.isChecked(), "join": "pins" if self.pins.isChecked() else "glue",
                "colors": {o["name"]: o["color"] for o in self.items}, "moving": chosen,
                "printer": {"name": self.printer.currentText(), "bed": [s.value() for s in self.bed],
                            "nozzle": self.nozzle.value(), "slots": self.slots.value(),
                            "process": "resin" if self.process.currentIndex() == 1 else "fdm"}}

    def start(self) -> None:
        if not self.items:
            self.state.setText(self.t["no_objects"])
            return
        options = self.options()
        self.params.SetString("Printer", self.printer.currentText())
        self.params.SetString("Join", options["join"])
        self.params.SetString("Language", options["lang"])
        self.start_btn.setEnabled(False)
        self.open_btn.hide()
        self.bar.show()
        self.state.setText(self.t["working"])
        self.job = prepare(self.doc, options, self.folder.text())
        self.proc = QtCore.QProcess(self)
        self.proc.setProcessEnvironment(_environment())
        self.proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.proc.finished.connect(self._finished)
        command = _freecadcmd()
        self.proc.start(command[0], [*command[1:], self.job["script"]])

    def _finished(self, *_args: Any) -> None:
        self.bar.hide()
        self.start_btn.setEnabled(True)
        output = bytes(self.proc.readAll()).decode("utf-8", "replace")
        result = finish(self.job, output)
        if result["ok"]:
            self.state.setText(self.t["done"].format(n=result["pieces"], path=self.folder.text()))
            self.open_btn.show()
        else:
            self.state.setText(self.t["failed"].format(why=result["why"]))


def _freecadcmd() -> list[str]:
    from .. import addon

    command = addon._freecadcmd_info(App.getHomePath()).get("freecadcmd_cmd")
    if not command:
        raise RuntimeError("freecadcmd not found")
    return list(command)


def _clean() -> dict[str, str]:
    return childenv.clean_child_env(dict(os.environ), App.getHomePath())


def _environment() -> Any:
    env = QtCore.QProcessEnvironment()
    for k, v in _clean().items():
        env.insert(k, v)
    return env


def prepare(doc: Any, options: dict[str, Any], folder: str) -> dict[str, str]:
    """Save a copy, write the options and the script; the open document stays as it is."""
    os.makedirs(folder, exist_ok=True)
    work = tempfile.mkdtemp(prefix="print-")
    copy = os.path.join(work, "copy.FCStd")
    doc.saveCopy(copy)
    opts = os.path.join(work, "options.json")
    with open(opts, "w", encoding="utf-8") as f:
        json.dump(options, f)
    addon_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    script = os.path.join(work, "run.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(SCRIPT.format(addon=addon_dir, path=copy, options=opts, out=folder))
    return {"work": work, "script": script, "folder": folder, "lang": options.get("lang", "en")}


def finish(job: dict[str, str], output: str) -> dict[str, Any]:
    """After the job: make the guide, say what happened."""
    if "FCB_PRINT_OK" not in output:
        lines = [ln for ln in output.splitlines() if ln.strip()]
        return {"ok": False, "why": "\n".join(lines[-4:])[:600]}
    with open(os.path.join(job["folder"], "report.json"), encoding="utf-8") as f:
        report = json.load(f)
    name = "Anleitung.pdf" if report.get("lang") == "de" else "Assembly_guide.pdf"
    pdf = guide.build(report, job["folder"], os.path.join(job["folder"], name))
    return {"ok": True, "guide": pdf, "pieces": plan.print_count(report)}


def export_now(doc: Any, options: dict[str, Any], folder: str) -> dict[str, Any]:
    """The whole export, waiting for it: for tests and for callers without a dialog."""
    import subprocess

    job = prepare(doc, options, folder)
    done = subprocess.run([*_freecadcmd(), job["script"]], capture_output=True, text=True, env=_clean(),
                          stdin=subprocess.DEVNULL, cwd=job["work"], timeout=900)
    return finish(job, done.stdout + done.stderr)


def show() -> None:
    doc = App.ActiveDocument
    parent = Gui.getMainWindow()
    if doc is None or not collect(doc):
        QtWidgets.QMessageBox.information(parent, plan.TEXT[language()]["dialog"], plan.TEXT[language()]["no_objects"])
        return
    dialog = PrintDialog(doc, parent)
    _open.append(dialog)
    dialog.finished.connect(lambda *_a, d=dialog: _open.remove(d) if d in _open else None)
    dialog.show()
