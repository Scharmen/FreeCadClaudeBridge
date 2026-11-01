"""The "Claude" chat panel: a dock widget on the right of FreeCAD's main window."""

from __future__ import annotations

import html
import json
import os
import time
from typing import Any

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from ..bridge import paths
from . import secrets
from .claude_process import ClaudeRun
from .stream import check_init, short_tool_name

PARAMS = "User parameter:BaseApp/Preferences/Mod/ClaudeBridge"
OBJECT_NAME = "ClaudeChatPanel"
NO_DOC = "<no document>"
_panel: dict[str, Any] = {}


def _params() -> Any:
    return App.ParamGet(PARAMS)


def _sessions_path() -> str:
    return os.path.join(paths.bridge_dir(), "sessions.json")


def _load_sessions() -> dict[str, str]:
    try:
        with open(_sessions_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_sessions(data: dict[str, str]) -> None:
    try:
        paths.ensure_bridge_dir()
        with open(_sessions_path(), "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def _doc_key() -> str:
    doc = App.ActiveDocument
    if doc is None:
        return NO_DOC
    return doc.FileName or f"unsaved:{doc.Name}"


def context_line() -> str:
    doc = App.ActiveDocument
    if doc is None:
        return "[FreeCAD] No document is open."
    parts = []
    for s in Gui.Selection.getSelectionEx("", 0)[:10]:
        item = f"{s.Object.Label} ({s.ObjectName})"
        if s.SubElementNames:
            item += ": " + ", ".join(list(s.SubElementNames)[:10])
        parts.append(item)
    sel = "; ".join(parts) if parts else "nothing"
    return f"[FreeCAD] Active document: {doc.Label} ({doc.Name}). Selection: {sel}."


class _Input(QtWidgets.QPlainTextEdit):
    submitted = QtCore.Signal()

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:  # noqa: N802 (Qt API)
        if e.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and not (e.modifiers() & QtCore.Qt.ShiftModifier):
            self.submitted.emit()
            return
        super().keyPressEvent(e)


class ChatPanel(QtWidgets.QDockWidget):
    def __init__(self, parent: Any = None) -> None:
        super().__init__("Claude", parent)
        self.setObjectName(OBJECT_NAME)
        self.run: ClaudeRun | None = None
        self.sessions = _load_sessions()
        self.tool_rows: dict[str, tuple[str, float]] = {}
        self.text_open = False
        self.turn_started = 0.0

        body = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(body)
        lay.setContentsMargins(6, 6, 6, 6)

        head = QtWidgets.QHBoxLayout()
        self.mode_label = QtWidgets.QLabel()
        head.addWidget(self.mode_label, 1)
        self.menu_btn = QtWidgets.QToolButton()
        self.menu_btn.setText("⚙")
        self.menu_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.menu_btn.setMenu(self._build_menu())
        head.addWidget(self.menu_btn)
        lay.addLayout(head)

        self.view = QtWidgets.QTextBrowser()
        self.view.setOpenExternalLinks(True)
        lay.addWidget(self.view, 1)

        self.input = _Input()
        self.input.setPlaceholderText("Ask Claude to build or change something… (Enter sends, Shift+Enter new line)")
        self.input.setMaximumHeight(90)
        self.input.submitted.connect(self.send)
        lay.addWidget(self.input)

        row = QtWidgets.QHBoxLayout()
        self.status = QtWidgets.QLabel("")
        self.status.setStyleSheet("color: gray")
        row.addWidget(self.status, 1)
        self.stop_btn = QtWidgets.QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop)
        self.stop_btn.setEnabled(False)
        row.addWidget(self.stop_btn)
        self.send_btn = QtWidgets.QPushButton("Send")
        self.send_btn.clicked.connect(self.send)
        row.addWidget(self.send_btn)
        lay.addLayout(row)

        self.setWidget(body)
        self._update_mode_label()

    # ----- settings -----

    def mode(self) -> str:
        return _params().GetString("AuthMode", "subscription")

    def _build_menu(self) -> QtWidgets.QMenu:
        menu = QtWidgets.QMenu(self)
        group = QtGui.QActionGroup(menu) if hasattr(QtGui, "QActionGroup") else QtWidgets.QActionGroup(menu)
        self.act_sub = menu.addAction("Use Claude subscription")
        self.act_key = menu.addAction("Use API key")
        for act, mode in ((self.act_sub, "subscription"), (self.act_key, "api_key")):
            act.setCheckable(True)
            group.addAction(act)
            act.triggered.connect(lambda _=False, m=mode: self.set_mode(m))
        menu.addSeparator()
        menu.addAction("Set API key…").triggered.connect(self.ask_api_key)
        menu.addAction("Remove stored API key").triggered.connect(self.clear_api_key)
        menu.addSeparator()
        menu.addAction("New conversation").triggered.connect(self.new_conversation)
        return menu

    def set_mode(self, mode: str) -> None:
        if mode == "api_key" and not secrets.get_api_key():
            self.ask_api_key()
            if not secrets.get_api_key():
                mode = "subscription"
        _params().SetString("AuthMode", mode)
        self._update_mode_label()

    def _update_mode_label(self) -> None:
        mode = self.mode()
        self.act_sub.setChecked(mode == "subscription")
        self.act_key.setChecked(mode == "api_key")
        text = "Mode: Claude subscription" if mode == "subscription" else "Mode: API key (pay as you go)"
        self.mode_label.setText(f"<b>{text}</b>")

    def ask_api_key(self) -> None:
        key, ok = QtWidgets.QInputDialog.getText(self, "Claude API key", "Anthropic API key (stored in the Keychain):",
                                                 QtWidgets.QLineEdit.Password)
        if ok and key.strip():
            try:
                secrets.set_api_key(key)
                _params().SetString("AuthMode", "api_key")
                self._note("API key stored. Mode: API key.")
            except Exception as e:
                self._note(f"Could not store the key: {e}", error=True)
        self._update_mode_label()

    def clear_api_key(self) -> None:
        secrets.clear_api_key()
        _params().SetString("AuthMode", "subscription")
        self._update_mode_label()
        self._note("API key removed. Mode: subscription.")

    def new_conversation(self) -> None:
        self.sessions.pop(_doc_key(), None)
        _save_sessions(self.sessions)
        self.view.clear()
        self._note("New conversation.")

    # ----- chat -----

    def send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text or (self.run and self.run.running()):
            return
        self.input.clear()
        self._append(f"<p style='margin-top:10px'><b>You:</b> {html.escape(text)}</p>")
        prompt = context_line() + "\n\n" + text
        doc = App.ActiveDocument
        cwd = os.path.dirname(doc.FileName) if doc is not None and doc.FileName else os.path.expanduser("~")
        self.current_key = _doc_key()
        # Without a document there is nothing to tie a conversation to: start fresh each time.
        session = None if self.current_key == NO_DOC else self.sessions.get(self.current_key)
        self.run = ClaudeRun(prompt, session, self.mode(), cwd,
                             load_plugin_dir=_params().GetBool("LoadPluginDir", True), parent=self)
        self.run.event.connect(self._on_event)
        self.run.finished.connect(self._on_finished)
        self.run.failed.connect(self._on_failed)
        self.text_open = False
        self.turn_started = time.time()
        self._busy(True)
        self.run.start()

    def stop(self) -> None:
        if self.run:
            self.status.setText("Stopping…")
            self.run.stop()

    def _busy(self, busy: bool) -> None:
        self.stop_btn.setEnabled(busy)
        self.send_btn.setEnabled(not busy)
        self.status.setText("Claude is working…" if busy else "")

    def _on_event(self, ev: dict[str, Any]) -> None:
        kind = ev.get("kind")
        if kind == "init":
            if ev.get("session_id") and self.current_key != NO_DOC:
                self.sessions[self.current_key] = ev["session_id"]
                _save_sessions(self.sessions)
            for problem in check_init(ev, self.mode()):
                self._note(problem, error=True)
        elif kind in ("text_delta", "text"):
            if not self.text_open:
                self._append("<p><b>Claude:</b> </p>")
                self.text_open = True
            self._insert_text(ev.get("text", ""))
        elif kind == "tool_use":
            self.text_open = False
            name = short_tool_name(ev.get("name", "?"))
            self.tool_rows[ev.get("id") or name] = (name, time.time())
            self._append(f"<div style='color:#666'>▸ <code>{html.escape(name)}</code> "
                         f"{html.escape(_tool_summary(ev.get('input') or {}))}</div>")
        elif kind == "tool_result":
            name, t0 = self.tool_rows.pop(ev.get("id"), ("tool", time.time()))
            dt = time.time() - t0
            if ev.get("is_error"):
                self._append(f"<div style='color:#b00'>✗ <code>{html.escape(name)}</code> {dt:.1f} s: "
                             f"{html.escape(ev.get('summary', ''))}</div>")
            else:
                self._append(f"<div style='color:#2a7'>✓ <code>{html.escape(name)}</code> {dt:.1f} s</div>")
        elif kind == "permission_denied":
            self._note(f"Blocked: {ev.get('tool')} {ev.get('message', '')}", error=True)
        elif kind == "result":
            if ev.get("is_error") and ev.get("text"):
                self._note(ev["text"], error=True)
            for tool in ev.get("denials") or []:
                self._note(f"Not allowed in the chat panel: {tool}", error=True)
        elif kind == "invalid":
            App.Console.PrintLog(f"ClaudeBridge chat: {ev.get('line')}\n")

    def _on_finished(self, code: int) -> None:
        stopped = self.run.stopping if self.run else False
        dt = time.time() - self.turn_started
        self._busy(False)
        if stopped:
            self._note("Stopped.")
        elif code != 0:
            self._note(f"Claude Code exited with code {code}. See Report view for details.", error=True)
        self.status.setText(f"Done in {dt:.0f} s" if not stopped else "Stopped")

    def _on_failed(self, message: str) -> None:
        self._busy(False)
        self._note(message, error=True)

    # ----- rendering -----

    def _append(self, fragment: str) -> None:
        self.view.append(fragment)
        self._scroll()

    def _insert_text(self, text: str) -> None:
        cursor = self.view.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        cursor.insertText(text)
        self._scroll()

    def _note(self, text: str, error: bool = False) -> None:
        color = "#b00" if error else "#666"
        self._append(f"<div style='color:{color}'><i>{html.escape(text)}</i></div>")

    def _scroll(self) -> None:
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def transcript(self) -> str:
        return self.view.toPlainText()


def _tool_summary(inp: dict[str, Any]) -> str:
    if "code" in inp:
        first = next((ln for ln in str(inp["code"]).splitlines() if ln.strip()), "")
        return (first[:60] + "…") if len(first) > 60 else first
    parts = [f"{k}={v}" for k, v in inp.items() if v not in (None, "", [], {})]
    text = ", ".join(parts)
    return (text[:70] + "…") if len(text) > 70 else text


def get_panel() -> ChatPanel | None:
    return _panel.get("widget")


def ensure_panel(show: bool = True) -> ChatPanel:
    panel = get_panel()
    if panel is None:
        mw = Gui.getMainWindow()
        panel = ChatPanel(mw)
        mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, panel)
        _panel["widget"] = panel
    panel.setVisible(show)
    if show:
        panel.raise_()
    return panel


def toggle_panel() -> None:
    """Menu/shortcut toggle; only this persists visibility (closing FreeCAD hides docks too)."""
    panel = get_panel()
    show = panel is None or not panel.isVisible()
    ensure_panel(show=show)
    _params().SetBool("PanelVisible", show)
    if show:
        get_panel().input.setFocus()
