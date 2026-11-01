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
        layout.addStretch(1)
        self._total = settings.total_memory()

    # ----- FreeCAD calls these

    def loadSettings(self) -> None:  # noqa: N802 (FreeCAD API)
        p = _params()
        self.autostart.setChecked(p.GetBool("Autostart", True))
        self.show_panel.setChecked(p.GetBool("ShowPanel", True))
        transport = p.GetString("Transport", "auto")
        self.transport.setCurrentIndex(TRANSPORTS.index(transport) if transport in TRANSPORTS else 0)
        for key, value in settings.load(paths.bridge_dir()).items():
            self.spins[key].setValue(value)
        self._update_preview()

    def saveSettings(self) -> None:  # noqa: N802 (FreeCAD API)
        p = _params()
        p.SetBool("Autostart", self.autostart.isChecked())
        p.SetBool("ShowPanel", self.show_panel.isChecked())
        p.SetString("Transport", self.transport.currentText())
        settings.save(paths.bridge_dir(), self.values())

    # ----- helpers

    def values(self) -> dict[str, Any]:
        return {key: spin.value() for key, spin in self.spins.items()}

    def _restore_defaults(self) -> None:
        for key, value in settings.defaults().items():
            self.spins[key].setValue(value)

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


def show() -> None:
    import FreeCADGui as Gui

    Gui.showPreferences(GROUP)
