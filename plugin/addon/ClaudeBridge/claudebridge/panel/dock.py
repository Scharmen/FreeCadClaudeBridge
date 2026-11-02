"""The "Claude" chat panel: a dock widget on the right of FreeCAD's main window."""

from __future__ import annotations

import hashlib
import html
import os
import time
from typing import Any

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from ..bridge import paths
from . import conversations, secrets
from .claude_process import ClaudeRun
from .stream import check_init, short_tool_name, turn_summary

PARAMS = "User parameter:BaseApp/Preferences/Mod/ClaudeBridge"
OBJECT_NAME = "ClaudeChatPanel"
NO_DOC = "<no document>"
_panel: dict[str, Any] = {}


def _params() -> Any:
    return App.ParamGet(PARAMS)


def _when(stamp: float) -> str:
    """Short time like the Desktop app's list: 14:05 today, Mon, or 03.11."""
    if not stamp:
        return ""
    now, t = time.localtime(), time.localtime(stamp)
    if t[:3] == now[:3]:
        return time.strftime("%H:%M", t)
    if time.time() - stamp < 6 * 86400:
        return time.strftime("%a %H:%M", t)
    return time.strftime("%d.%m.%Y", t)


def _history_path(key: str) -> str:
    return os.path.join(paths.bridge_dir(), "history", hashlib.sha1(key.encode()).hexdigest()[:16] + ".html")


def _load_history(key: str) -> str:
    try:
        with open(_history_path(key), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _save_history(key: str, content: str) -> None:
    try:
        os.makedirs(os.path.dirname(_history_path(key)), exist_ok=True)
        with open(_history_path(key), "w", encoding="utf-8") as f:
            f.write(content)
    except OSError:
        pass


class _ActiveDocWatcher:
    """Switch the panel's conversation when the user activates another document."""

    def __init__(self, panel: ChatPanel) -> None:
        self.panel = panel

    def slotActivateDocument(self, doc: Any) -> None:  # noqa: N802 (FreeCAD API)
        QtCore.QTimer.singleShot(0, self.panel.switch_document)

    def slotDeletedDocument(self, doc: Any) -> None:  # noqa: N802
        QtCore.QTimer.singleShot(0, self.panel.switch_document)


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


SUBTITLE_ROLE = QtCore.Qt.UserRole + 1


class _ConversationDelegate(QtWidgets.QStyledItemDelegate):
    """Two-line rows like the Claude Desktop sidebar: title (elided) over a small grey subtitle."""

    def paint(self, painter: QtGui.QPainter, option: Any, index: Any) -> None:  # noqa: N802 (Qt API)
        if index.data(QtCore.Qt.UserRole) is None:             # section header
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        r = option.rect.adjusted(2, 1, -4, -1)
        pal = option.palette
        fg = pal.text().color()
        if option.state & QtWidgets.QStyle.State_Selected:
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(pal.highlight())
            painter.drawRoundedRect(r, 6, 6)
            fg = pal.highlightedText().color()
        elif option.state & QtWidgets.QStyle.State_MouseOver:
            hover = QtGui.QColor(fg)
            hover.setAlphaF(0.08)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(hover)
            painter.drawRoundedRect(r, 6, 6)
        text = r.adjusted(8, 5, -8, -4)
        font = QtGui.QFont(option.font)
        fm = QtGui.QFontMetrics(font)
        painter.setFont(font)
        painter.setPen(fg)
        title = str(index.data(QtCore.Qt.DisplayRole) or "")
        painter.drawText(text.x(), text.y() + fm.ascent(), fm.elidedText(title, QtCore.Qt.ElideRight, text.width()))
        small = QtGui.QFont(font)
        small.setPointSizeF(max(7.0, font.pointSizeF() * 0.85))
        sfm = QtGui.QFontMetrics(small)
        grey = QtGui.QColor(fg)
        grey.setAlphaF(0.6)
        painter.setFont(small)
        painter.setPen(grey)
        sub = str(index.data(SUBTITLE_ROLE) or "")
        painter.drawText(text.x(), text.y() + fm.height() + 2 + sfm.ascent(),
                         sfm.elidedText(sub, QtCore.Qt.ElideRight, text.width()))
        painter.restore()

    def sizeHint(self, option: Any, index: Any) -> QtCore.QSize:  # noqa: N802 (Qt API)
        if index.data(QtCore.Qt.UserRole) is None:
            hint = super().sizeHint(option, index)
            return QtCore.QSize(hint.width(), hint.height() + 8)
        fm = QtGui.QFontMetrics(option.font)
        return QtCore.QSize(option.rect.width(), fm.height() * 2 + 12)


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
        self.store = conversations.Store(paths.bridge_dir())
        self.pending: dict[str, str] = {}  # title and folder of a conversation until Claude names its session
        self.tool_rows: dict[str, tuple[str, float]] = {}
        self.text_open = False
        self.turn_started = 0.0
        self.current_key = _doc_key()
        self.allowed_extra: dict[str, list[str]] = {}  # per conversation, approved via the panel's links
        self.last_text = ""
        self.turn_results: list[str] = []
        self.turn_denied: list[str] = []
        self.max_result_chars = 0

        body = QtWidgets.QWidget()
        outer = QtWidgets.QHBoxLayout(body)
        outer.setContentsMargins(0, 0, 0, 0)
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        outer.addWidget(self.splitter)
        self.sidebar = self._build_sidebar()
        self.splitter.addWidget(self.sidebar)
        chat = QtWidgets.QWidget()
        self.splitter.addWidget(chat)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.splitterMoved.connect(self._remember_sidebar_width)
        lay = QtWidgets.QVBoxLayout(chat)
        lay.setContentsMargins(6, 6, 6, 6)

        head = QtWidgets.QHBoxLayout()
        self.side_btn = QtWidgets.QToolButton()
        self.side_btn.setText("☰")
        self.side_btn.setCheckable(True)
        self.side_btn.setToolTip("Show or hide the conversation list")
        self.side_btn.toggled.connect(self._toggle_sidebar)
        head.addWidget(self.side_btn)
        self.mode_label = QtWidgets.QLabel()
        head.addWidget(self.mode_label, 1)
        self.menu_btn = QtWidgets.QToolButton()
        self.menu_btn.setText("⚙")
        self.menu_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.menu_btn.setMenu(self._build_menu())
        self.camera_btn = QtWidgets.QToolButton()
        self.camera_btn.setText("📷")
        self.camera_btn.setToolTip("Insert a picture of the 3D view into the conversation")
        self.camera_btn.clicked.connect(self.insert_view_image)
        head.addWidget(self.camera_btn)
        self.image_btn = QtWidgets.QToolButton()
        self.image_btn.setText("🖼")
        self.image_btn.setToolTip("Attach a picture: Claude can look at it, trace it, or turn it into an outline "
                                  "or relief")
        self.image_btn.clicked.connect(self.attach_image)
        head.addWidget(self.image_btn)
        head.addWidget(self.menu_btn)
        lay.addLayout(head)

        self.view = QtWidgets.QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._on_link)
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
        visible = _params().GetBool("SidebarVisible", True)
        self.side_btn.setChecked(visible)
        self.sidebar.setVisible(visible)
        width = _params().GetInt("SidebarWidth", 180)
        self.splitter.setSizes([width, max(260, self.width() - width)])
        self._render_active()
        self._doc_watcher = _ActiveDocWatcher(self)
        App.addDocumentObserver(self._doc_watcher)

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
        menu.addAction("Rename conversation…").triggered.connect(self.rename_conversation)
        menu.addAction("Delete conversation…").triggered.connect(self.delete_conversation)
        menu.addAction("Open in Claude Desktop").triggered.connect(self.open_in_desktop)
        menu.addSeparator()
        menu.addAction("Settings…").triggered.connect(self._open_settings)
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

    # ----- conversations -----

    def _build_sidebar(self) -> QtWidgets.QWidget:
        side = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(side)
        v.setContentsMargins(6, 6, 0, 6)
        top = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("<b>Conversations</b>")
        top.addWidget(title, 1)
        new_btn = QtWidgets.QToolButton()
        new_btn.setText("＋")
        new_btn.setToolTip("New conversation")
        new_btn.clicked.connect(self.new_conversation)
        top.addWidget(new_btn)
        desk_btn = QtWidgets.QToolButton()
        desk_btn.setText("↗")
        desk_btn.setToolTip("Open the current conversation in the Claude Desktop app")
        desk_btn.clicked.connect(self.open_in_desktop)
        top.addWidget(desk_btn)
        v.addLayout(top)
        self.conv_list = QtWidgets.QListWidget()
        self.conv_list.setItemDelegate(_ConversationDelegate(self.conv_list))
        self.conv_list.setMouseTracking(True)
        self.conv_list.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.conv_list.setStyleSheet("QListWidget { background: transparent; }")
        self.conv_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.conv_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.conv_list.customContextMenuRequested.connect(self._conversation_menu)
        self.conv_list.itemClicked.connect(self._on_conversation_item)
        v.addWidget(self.conv_list, 1)
        side.setMinimumWidth(130)
        return side

    def _toggle_sidebar(self, visible: bool) -> None:
        self.sidebar.setVisible(visible)
        _params().SetBool("SidebarVisible", visible)

    def _remember_sidebar_width(self, *_: Any) -> None:
        sizes = self.splitter.sizes()
        if sizes and sizes[0] > 0:
            _params().SetInt("SidebarWidth", sizes[0])

    def _hist_key(self) -> str:
        item = self.store.active(self.current_key)
        return f"session:{item['id']}" if item else f"pending:{self.current_key}"

    def _doc_folder(self) -> str:
        doc = App.ActiveDocument
        return os.path.dirname(doc.FileName) if doc is not None and doc.FileName else os.path.expanduser("~")

    def refresh_conversations(self) -> None:
        lst = self.conv_list
        lst.clear()
        active = self.store.active(self.current_key)
        role = QtCore.Qt.UserRole

        def header(text: str) -> None:
            it = QtWidgets.QListWidgetItem(text.upper())
            it.setFlags(QtCore.Qt.NoItemFlags)
            font = it.font()
            font.setPointSizeF(max(7.0, font.pointSizeF() * 0.8))
            font.setBold(True)
            it.setFont(font)
            it.setForeground(QtGui.QColor("#888"))
            lst.addItem(it)

        def entry(text: str, sub: str, data: tuple, tip: str, current: bool) -> None:
            it = QtWidgets.QListWidgetItem(text)
            it.setData(role, data)
            it.setData(SUBTITLE_ROLE, sub)
            it.setToolTip(tip)
            lst.addItem(it)
            if current:
                lst.setCurrentItem(it)

        doc = App.ActiveDocument
        header(doc.Label if doc is not None else "No document")
        if active is None:
            entry("New conversation", "not started yet", ("pending", None), "Type a message to start it", True)
        for c in sorted(self.store.items(self.current_key), key=lambda c: c.get("created", 0), reverse=True):
            path = conversations.session_path(c["cwd"], c["id"])
            stamp = os.path.getmtime(path) if path and os.path.exists(path) else c.get("created", 0)
            entry(c["title"], _when(stamp), ("doc", c["id"]), f"{c['title']}\nSession {c['id']}",
                  bool(active and c["id"] == active["id"]))
        others = conversations.folder_sessions(self._doc_folder(), exclude=self.store.linked_ids())
        if others:
            header("In this folder")
            for info in others:
                entry(info["title"], f"{info['source'] or 'session'} · {_when(info['updated'])}", ("ext", info),
                      f"{info['title']}\nStarted in: {info['source'] or '?'}\nClick to continue it here", False)

    def _render_active(self) -> None:
        """Show the active conversation: the panel's own record, or the session file if it is newer
        (the conversation went on in a terminal or the Desktop app)."""
        item = self.store.active(self.current_key)
        saved = _load_history(self._hist_key())
        path = conversations.session_path(item["cwd"], item["id"]) if item else None
        newer = path and os.path.exists(path) and (not saved or os.path.getmtime(path) >
                                                   os.path.getmtime(_history_path(self._hist_key())) + 1)
        if item and newer:
            parts = []
            for role, text in conversations.transcript(path):
                body = html.escape(text).replace("\n", "<br>")
                if role == "user":
                    parts.append(f"<p style='margin-top:10px'><b>You:</b> {body}</p>")
                elif role == "assistant":
                    parts.append(f"<p>{body}</p>")
                else:
                    parts.append(f"<div style='color:#888'>⚙ {body}</div>")
            self.view.setHtml("".join(parts))
            self._note("Redrawn from the session file (it continued outside this panel).")
        else:
            self.view.setHtml(saved)
        self.refresh_conversations()
        self._scroll()

    def _on_conversation_item(self, item: QtWidgets.QListWidgetItem) -> None:
        kind, value = item.data(QtCore.Qt.UserRole) or (None, None)
        self._choose(kind, value)

    def _conversation_menu(self, pos: QtCore.QPoint) -> None:
        item = self.conv_list.itemAt(pos)
        data = item.data(QtCore.Qt.UserRole) if item else None
        if not data or data[0] == "pending":
            return
        self._choose(*data)
        menu = QtWidgets.QMenu(self)
        menu.addAction("Rename…").triggered.connect(self.rename_conversation)
        menu.addAction("Open in Claude Desktop").triggered.connect(self.open_in_desktop)
        menu.addSeparator()
        menu.addAction("Delete…").triggered.connect(self.delete_conversation)
        menu.exec(self.conv_list.viewport().mapToGlobal(pos))

    def _choose(self, kind: str | None, value: Any) -> None:
        if kind is None:
            return
        if self.run and self.run.running():
            self.refresh_conversations()
            self._note("Wait until Claude is done, or press Stop, before switching conversations.")
            return
        _save_history(self._hist_key(), self.view.toHtml())
        if kind == "doc":
            self.store.set_active(self.current_key, value)
        elif kind == "ext":
            self.store.add(self.current_key, value["id"], value["title"], value["cwd"] or self._doc_folder())
        elif kind == "pending":
            return
        self._render_active()

    def new_conversation(self) -> None:
        if self.run and self.run.running():
            return
        _save_history(self._hist_key(), self.view.toHtml())
        self.store.start_new(self.current_key)
        self.allowed_extra.pop(self.current_key, None)
        self.view.clear()
        self._note("New conversation.")
        self.refresh_conversations()

    def rename_conversation(self) -> None:
        item = self.store.active(self.current_key)
        if not item:
            self._note("Start the conversation first.")
            return
        title, ok = QtWidgets.QInputDialog.getText(self, "Claude", "Name of this conversation:", text=item["title"])
        if ok and title.strip():
            self.store.rename(self.current_key, item["id"], title)
            self.refresh_conversations()

    def delete_conversation(self) -> None:
        item = self.store.active(self.current_key)
        if not item or (self.run and self.run.running()):
            return
        sure = QtWidgets.QMessageBox.question(
            self, "Claude", f"Delete the conversation \"{item['title']}\"? It also disappears from Claude Code "
                            "(terminal and Desktop app).")
        if sure != QtWidgets.QMessageBox.Yes:
            return
        conversations.delete_session_file(item["cwd"], item["id"])
        try:
            os.unlink(_history_path(f"session:{item['id']}"))
        except OSError:
            pass
        self.store.remove(self.current_key, item["id"])
        self._render_active()

    def open_in_desktop(self) -> None:
        from .claude_process import open_in_desktop

        item = self.store.active(self.current_key)
        if not item:
            self._note("Start the conversation first; then it can be opened in the Claude Desktop app.")
            return
        _save_history(self._hist_key(), self.view.toHtml())
        error = open_in_desktop(item["id"], item["cwd"])
        self._note(error or "Opened in the Claude Desktop app. Changes made there show up here when you "
                            "come back to this conversation.", error=bool(error))

    def switch_document(self) -> None:
        """Show the conversation of the now active document (deferred while Claude is working)."""
        key = _doc_key()
        if key == self.current_key or (self.run and self.run.running()):
            return
        _save_history(self._hist_key(), self.view.toHtml())
        self.current_key = key
        self._render_active()

    def insert_view_image(self) -> None:
        gdoc = Gui.ActiveDocument
        if gdoc is None:
            self._note("No document open.", error=True)
            return
        folder = os.path.join(paths.bridge_dir(), "history", "img")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, time.strftime("%Y%m%d-%H%M%S") + ".png")
        width = max(200, min(600, self.view.viewport().width() - 30))
        gdoc.ActiveView.saveImage(path, width, int(width * 0.75), "Current")
        self._append(f"<p><img src='{QtCore.QUrl.fromLocalFile(path).toString()}' width='{width}'></p>")
        _save_history(self._hist_key(), self.view.toHtml())

    def attach_image(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Attach a picture", os.path.expanduser("~"),
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff)")
        if not path:
            return
        self.input.insertPlainText(("\n" if self.input.toPlainText().strip() else "") + f"[Reference image: {path}]\n")
        self._append(f"<p><img src='{QtCore.QUrl.fromLocalFile(path).toString()}' width='160'></p>")
        self.input.setFocus()

    def _ask_feedback(self) -> None:
        """After a turn that changed the model: offer a rating (learning.ask_for_feedback)."""
        from ..bridge import settings

        if not settings.load(paths.bridge_dir())["learning.ask_for_feedback"]:
            return
        self.feedback_turn = getattr(self, "feedback_turn", 0) + 1
        n = self.feedback_turn
        self.feedback_ctx = {"turn": n, "request": self.last_text, "result": self.last_result,
                             "doc": self.current_key}
        self._append(f"<div style='color:#666'>Did this match what you wanted? "
                     f"<a href='feedback:good:{n}'>👍 Yes</a> · <a href='feedback:partly:{n}'>〜 Partly</a> · "
                     f"<a href='feedback:bad:{n}'>👎 No</a></div>")

    def _on_feedback(self, rating: str, turn: int) -> None:
        from ..bridge import learning

        ctx = getattr(self, "feedback_ctx", None)
        if not ctx or ctx["turn"] != turn or ctx.get("done"):
            self._note("This rating is no longer current.")
            return
        ctx["done"] = True
        if rating == "good":
            learning.record_feedback(paths.bridge_dir(), "good", request=ctx["request"] or "",
                                     result=ctx["result"] or "", doc=ctx["doc"] or "", source="panel")
            self._note("Thanks, noted.")
            return
        comment, ok = QtWidgets.QInputDialog.getMultiLineText(
            self, "Claude", "What should be different? (Claude fixes it and remembers the lesson)")
        comment = comment.strip() if ok else ""
        if not comment:
            learning.record_feedback(paths.bridge_dir(), rating, request=ctx["request"] or "",
                                     result=ctx["result"] or "", doc=ctx["doc"] or "", source="panel")
            self._note("Noted.")
            return
        self.send(f"Feedback from the panel on your last result: {rating}. The user says: \"{comment}\". "
                  f"First call record_feedback with rating \"{rating}\", the user's words, the request and one "
                  f"general lesson worth remembering for future models; then fix the model accordingly.")

    def _on_link(self, url: QtCore.QUrl) -> None:
        if url.scheme() == "feedback":
            rating, _, turn = (url.path() or url.toString()[len("feedback:"):]).partition(":")
            if rating in ("good", "partly", "bad") and turn.isdigit():
                self._on_feedback(rating, int(turn))
            return
        if url.scheme() == "allow":
            tool = url.path() or url.toString()[len("allow:"):]
            allowed = self.allowed_extra.setdefault(self.current_key, [])
            if tool not in allowed:
                allowed.append(tool)
            self._note(f"Allowed {tool} for this conversation. Sending your last message again…")
            if self.last_text:
                self.send(self.last_text, echo=False)
        else:
            QtGui.QDesktopServices.openUrl(url)

    # ----- chat -----

    def send(self, text: str | None = None, echo: bool = True) -> None:
        text = (text if isinstance(text, str) else self.input.toPlainText()).strip()
        if not text or (self.run and self.run.running()):
            return
        if echo:
            self.input.clear()
            self._append(f"<p style='margin-top:10px'><b>You:</b> {html.escape(text)}</p>")
        self.last_text = text
        self._note_pending_update()
        self.turn_results, self.turn_denied, self.max_result_chars = [], [], 0
        prompt = context_line() + "\n\n" + text
        self.current_key = _doc_key()
        item = self.store.active(self.current_key)
        if item and os.path.isdir(item["cwd"]):
            session, cwd = item["id"], item["cwd"]            # resume where the session file lives
        else:
            session, cwd = None, self._doc_folder()
            self.pending = {"title": text.splitlines()[0][:60], "cwd": cwd}
        self.run = ClaudeRun(prompt, session, self.mode(), cwd,
                             load_plugin_dir=_params().GetBool("LoadPluginDir", True),
                             extra_allowed=self.allowed_extra.get(self.current_key), parent=self)
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
            sid = ev.get("session_id")
            if sid:
                item = self.store.active(self.current_key)
                if item is None:
                    self.store.add(self.current_key, sid, self.pending.get("title", ""),
                                   self.pending.get("cwd", self._doc_folder()))
                    _save_history(f"session:{sid}", self.view.toHtml())
                elif item["id"] != sid:
                    self.store.replace(self.current_key, item["id"], sid)
                self.refresh_conversations()
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
            self.max_result_chars = max(self.max_result_chars, int(ev.get("size", 0)))
            if not ev.get("is_error") and ev.get("content"):
                self.turn_results.append(ev["content"])
            if ev.get("is_error"):
                self._append(f"<div style='color:#b00'>✗ <code>{html.escape(name)}</code> {dt:.1f} s: "
                             f"{html.escape(ev.get('summary', ''))}</div>")
            else:
                self._append(f"<div style='color:#2a7'>✓ <code>{html.escape(name)}</code> {dt:.1f} s</div>")
        elif kind == "permission_denied":
            if ev.get("tool") and ev["tool"] not in self.turn_denied:
                self.turn_denied.append(ev["tool"])
        elif kind == "result":
            if ev.get("is_error") and ev.get("text"):
                self._note(ev["text"], error=True)
            for tool in ev.get("denials") or []:
                if tool not in self.turn_denied:
                    self.turn_denied.append(tool)
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
        changed = self._render_turn_summary()
        if changed and not stopped and code == 0:
            self._ask_feedback()
        for tool in self.turn_denied:
            self._append(f"<div style='color:#b00'>⚠ Blocked in the chat panel: <code>{html.escape(tool)}</code> "
                         f"<a href='allow:{html.escape(tool)}'>Allow for this conversation</a></div>")
        self.status.setText(f"Done in {dt:.0f} s" if not stopped else "Stopped")
        _save_history(self._hist_key(), self.view.toHtml())
        QtCore.QTimer.singleShot(0, self.switch_document)  # catch up on a document switch during the turn

    def _render_turn_summary(self) -> bool:
        s = turn_summary(self.turn_results)
        parts = []
        for key, label in (("created", "created"), ("changed", "changed"), ("deleted", "deleted")):
            if s[key]:
                names = ", ".join(s[key][:4]) + (" …" if len(s[key]) > 4 else "")
                parts.append(f"{len(s[key])} {label} ({html.escape(names)})")
        self.last_result = "; ".join(parts)
        if parts:
            self._append(f"<div style='color:#666'>Changes: {'; '.join(parts)}</div>")
        for e in s["errors"][:5]:
            self._append(f"<div style='color:#b00'>⚠ {html.escape(e.get('label') or e.get('name', '?'))} is in an "
                         f"error state ({html.escape(', '.join(e.get('state', [])))})</div>")
        return bool(s["created"] or s["changed"] or s["deleted"])

    def _on_failed(self, message: str) -> None:
        self._busy(False)
        self._note(message, error=True)

    def _open_settings(self) -> None:
        from .prefs import show

        show()

    def _note_pending_update(self) -> None:
        from ..addon import pending_update

        newer = pending_update()
        if newer and not getattr(self, "_update_noted", False):
            self._update_noted = True
            self._note(f"ClaudeBridge {newer} is installed: restart FreeCAD to load it.")

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
        if not _params().GetBool("SidebarIntroduced", False):    # once: room for the conversation list
            _params().SetBool("SidebarIntroduced", True)
            QtCore.QTimer.singleShot(0, lambda: mw.resizeDocks([panel], [580], QtCore.Qt.Horizontal))
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
