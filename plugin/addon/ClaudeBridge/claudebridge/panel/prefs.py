"""Preferences page "Claude Bridge" (Edit → Preferences): addon switches and headless job limits.

Addon switches live in FreeCAD's parameters; the headless limits go to settings.json in
the bridge directory, because the MCP server (outside FreeCAD) applies them.
"""

from __future__ import annotations

from typing import Any

from PySide import QtWidgets

from ..bridge import paths, settings

GROUP = "Claude Bridge"
TRANSPORTS = ["auto", "unix", "tcp"]


def _params() -> Any:
    from ..addon import params

    return params()


class PreferencesPage:
    """FreeCAD instantiates this class and shows `self.form`; it calls loadSettings/saveSettings."""

    def __init__(self) -> None:
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle(GROUP)
        layout = QtWidgets.QVBoxLayout(self.form)

        addon_box = QtWidgets.QGroupBox("Bridge in FreeCAD")
        af = QtWidgets.QFormLayout(addon_box)
        self.autostart = QtWidgets.QCheckBox("Start the bridge when FreeCAD starts")
        self.show_panel = QtWidgets.QCheckBox("Show the Claude chat panel")
        self.transport = QtWidgets.QComboBox()
        self.transport.addItems(TRANSPORTS)
        self.transport.setToolTip("auto: Unix socket on macOS/Linux, TCP on 127.0.0.1 on Windows")
        af.addRow(self.autostart)
        af.addRow(self.show_panel)
        af.addRow("Connection:", self.transport)
        af.addRow(QtWidgets.QLabel("<small>Changes here apply after restarting FreeCAD.</small>"))
        layout.addWidget(addon_box)

        job_box = QtWidgets.QGroupBox("Headless jobs (run_headless)")
        jf = QtWidgets.QFormLayout(job_box)
        self.spins: dict[str, Any] = {}
        for key, (default, lo, hi, unit, text) in settings.SCHEMA.items():
            if not key.startswith("headless."):
                continue
            if isinstance(default, int):
                spin = QtWidgets.QSpinBox()
            else:
                spin = QtWidgets.QDoubleSpinBox()
                spin.setDecimals(1)
                spin.setSingleStep(0.5 if unit == "GB" else 1.0)
            spin.setRange(lo, hi)
            spin.setSuffix(f" {unit}")
            spin.setToolTip(f"{text}. Default {default:g}, allowed {lo:g}–{hi:g} {unit}.")
            spin.valueChanged.connect(self._update_preview)
            self.spins[key] = spin
            jf.addRow(f"{text}:", spin)
        self.preview = QtWidgets.QLabel()
        self.preview.setWordWrap(True)
        jf.addRow(self.preview)
        reset = QtWidgets.QPushButton("Restore defaults")
        reset.clicked.connect(self._restore_defaults)
        jf.addRow(reset)
        jf.addRow(QtWidgets.QLabel("<small>Applies to the next headless job; no restart needed. "
                                   "Also available as <code>freecad-bridge settings</code>.</small>"))
        layout.addWidget(job_box)

        learn_box = QtWidgets.QGroupBox("Learning from your feedback")
        lf = QtWidgets.QVBoxLayout(learn_box)
        self.ask_feedback = QtWidgets.QCheckBox("Ask after each model whether it matched what I wanted")
        lf.addWidget(self.ask_feedback)
        self.lessons_label = QtWidgets.QLabel()
        self.lessons_label.setWordWrap(True)
        lf.addWidget(self.lessons_label)
        buttons = QtWidgets.QHBoxLayout()
        open_btn = QtWidgets.QPushButton("Open learned preferences")
        open_btn.clicked.connect(self._open_lessons)
        forget_btn = QtWidgets.QPushButton("Forget everything")
        forget_btn.clicked.connect(self._forget)
        buttons.addWidget(open_btn)
        buttons.addWidget(forget_btn)
        lf.addLayout(buttons)
        lf.addWidget(QtWidgets.QLabel("<small>Stored only on this computer (preferences.md, feedback.jsonl in the "
                                      "bridge folder); you can edit the lessons in any text editor.</small>"))
        layout.addWidget(learn_box)
        layout.addStretch(1)
        self._total = settings.total_memory()

    # ----- FreeCAD calls these

    def loadSettings(self) -> None:  # noqa: N802 (FreeCAD API)
        p = _params()
        self.autostart.setChecked(p.GetBool("Autostart", True))
        self.show_panel.setChecked(p.GetBool("ShowPanel", True))
        transport = p.GetString("Transport", "auto")
        self.transport.setCurrentIndex(TRANSPORTS.index(transport) if transport in TRANSPORTS else 0)
        values = settings.load(paths.bridge_dir())
        for key, spin in self.spins.items():
            spin.setValue(values[key])
        self.ask_feedback.setChecked(bool(values["learning.ask_for_feedback"]))
        self._update_lessons()
        self._update_preview()

    def saveSettings(self) -> None:  # noqa: N802 (FreeCAD API)
        p = _params()
        p.SetBool("Autostart", self.autostart.isChecked())
        p.SetBool("ShowPanel", self.show_panel.isChecked())
        p.SetString("Transport", self.transport.currentText())
        settings.save(paths.bridge_dir(), {**self.values(),
                                           "learning.ask_for_feedback": int(self.ask_feedback.isChecked())})

    # ----- helpers

    def values(self) -> dict[str, Any]:
        return {key: spin.value() for key, spin in self.spins.items()}

    def _restore_defaults(self) -> None:
        defaults = settings.defaults()
        for key, spin in self.spins.items():
            spin.setValue(defaults[key])

    def _update_lessons(self) -> None:
        from ..bridge import learning

        items = learning.lessons(paths.bridge_dir())
        self.lessons_label.setText(f"{len(items)} lesson(s) learned so far." if items
                                   else "Nothing learned yet.")

    def _open_lessons(self) -> None:
        from PySide import QtCore, QtGui

        from ..bridge import learning

        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(learning.ensure_file(paths.bridge_dir())))

    def _forget(self) -> None:
        from ..bridge import learning

        sure = QtWidgets.QMessageBox.question(self.form, GROUP, "Delete all feedback and learned preferences?")
        if sure == QtWidgets.QMessageBox.Yes:
            learning.forget(paths.bridge_dir())
            self._update_lessons()

    def _update_preview(self, *_: Any) -> None:
        if not self._total:
            self.preview.setText("RAM of this computer unknown.")
            return
        b = settings.memory_budget(self._total, self.values())
        gb = settings.GB
        self.preview.setText(f"<b>On this computer ({self._total / gb:.1f} GB RAM):</b> {b['reserve'] / gb:.1f} GB "
                             f"always stay free; a headless job may use up to {b['hard_cap'] / gb:.1f} GB.")


def register() -> None:
    import FreeCADGui as Gui

    Gui.addPreferencePage(PreferencesPage, GROUP)


_window: Any = None


def show() -> None:
    """Open the page in its own non-modal window.

    Gui.showPreferences() runs the modal Preferences dialog inside a Python call, which holds
    the GIL until the dialog closes and so freezes the bridge's listener thread. A non-modal
    window returns at once; Edit → Preferences still shows the same page.
    """
    global _window
    import FreeCADGui as Gui

    if _window is not None and _window.isVisible():
        _window.raise_()
        _window.activateWindow()
        return
    page = PreferencesPage()
    page.loadSettings()
    dlg = QtWidgets.QDialog(Gui.getMainWindow())
    dlg.setWindowTitle(GROUP)
    lay = QtWidgets.QVBoxLayout(dlg)
    lay.addWidget(page.form)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    lay.addWidget(buttons)

    def accept() -> None:
        page.saveSettings()
        dlg.accept()

    buttons.accepted.connect(accept)
    buttons.rejected.connect(dlg.reject)
    dlg.page = page  # keep the page alive with the window
    dlg.setModal(False)
    dlg.show()
    _window = dlg
