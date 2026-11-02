"""The "Claude" chat panel: a dock widget on the right of FreeCAD's main window."""

from __future__ import annotations

import hashlib
import html
import json
import os
import time
from typing import Any

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from ..bridge import paths
from . import conversations, dropping, secrets
from .claude_process import ClaudeSession
from .stream import PERMISSION_MODES, check_init, parse_slash, short_tool_name, turn_summary

PARAMS = "User parameter:BaseApp/Preferences/Mod/ClaudeBridge"
OBJECT_NAME = "ClaudeChatPanel"
NO_DOC = "<no document>"
_panel: dict[str, Any] = {}
MODE_LABELS = {"default": "Ask before acting", "acceptEdits": "Accept edits", "plan": "Plan mode",
               "auto": "Auto", "bypassPermissions": "Bypass permissions"}
FALLBACK_MODELS = [{"value": "default", "displayName": "Default"}, {"value": "opus", "displayName": "Opus"},
                   {"value": "sonnet", "displayName": "Sonnet"}, {"value": "haiku", "displayName": "Haiku"}]
LOCAL_COMMANDS = [("clear", "Start a new conversation"), ("new", "Start a new conversation"),
                  ("model", "Switch the model, e.g. /model opus"), ("effort", "Set the effort, e.g. /effort high"),
                  ("mode", "Permission mode: default, acceptEdits, plan, auto"),
                  ("desktop", "Open this conversation in the Claude Desktop app"), ("help", "List the commands")]


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


def _info_path() -> str:
    """Claude Code's last initialize answer (commands, models, account), so the panel knows them early."""
    return os.path.join(paths.bridge_dir(), "claude_info.json")


def _load_info() -> dict[str, Any]:
    try:
        with open(_info_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


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


def _from_tree(source: Any) -> bool:
    return isinstance(source, QtWidgets.QTreeWidget)


def _accepts(mime: Any, source: Any) -> bool:
    return bool(mime.hasUrls() or mime.hasImage() or _from_tree(source))


class _DropMixin:
    """Files, pictures and FreeCAD objects dropped on a widget go to the panel (as a copy, never a move:
    a move would make FreeCAD's tree remove the dragged rows)."""

    dropped: Any

    def dragEnterEvent(self, e: Any) -> None:  # noqa: N802 (Qt API)
        if _accepts(e.mimeData(), e.source()):
            e.setDropAction(QtCore.Qt.CopyAction)
            e.accept()
        else:
            super().dragEnterEvent(e)  # type: ignore[misc]

    def dragMoveEvent(self, e: Any) -> None:  # noqa: N802
        if _accepts(e.mimeData(), e.source()):
            e.setDropAction(QtCore.Qt.CopyAction)
            e.accept()
        else:
            super().dragMoveEvent(e)  # type: ignore[misc]

    def dropEvent(self, e: Any) -> None:  # noqa: N802
        if _accepts(e.mimeData(), e.source()):
            e.setDropAction(QtCore.Qt.CopyAction)
            e.accept()
            self.dropped.emit(e.mimeData(), e.source())
        else:
            super().dropEvent(e)  # type: ignore[misc]


class _Input(_DropMixin, QtWidgets.QPlainTextEdit):
    submitted = QtCore.Signal()
    escaped = QtCore.Signal()
    dropped = QtCore.Signal(object, object)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.completer: QtWidgets.QCompleter | None = None

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        """Slash commands for completion: (name, description)."""
        model = QtGui.QStandardItemModel(self)
        for name, desc in commands:
            item = QtGui.QStandardItem(f"/{name}" + (f"  — {desc}" if desc else ""))
            item.setToolTip(desc)
            model.appendRow(item)
        c = QtWidgets.QCompleter(model, self)
        c.setWidget(self)
        c.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        c.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
        c.setMaxVisibleItems(12)
        c.activated.connect(self._complete)
        self.completer = c

    def _complete(self, text: str) -> None:
        self.setPlainText(str(text).split("  — ")[0] + " ")
        cur = self.textCursor()
        cur.movePosition(QtGui.QTextCursor.End)
        self.setTextCursor(cur)

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:  # noqa: N802 (Qt API)
        c = self.completer
        if c and c.popup().isVisible() and e.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter, QtCore.Qt.Key_Tab,
                                                        QtCore.Qt.Key_Escape, QtCore.Qt.Key_Backtab):
            e.ignore()
            return
        if e.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and not (e.modifiers() & QtCore.Qt.ShiftModifier):
            self.submitted.emit()
            return
        if e.key() == QtCore.Qt.Key_Escape:
            self.escaped.emit()
        super().keyPressEvent(e)
        text = self.toPlainText()
        if c and text.startswith("/") and not any(ch.isspace() for ch in text):
            c.setCompletionPrefix(text)
            if c.completionCount():
                rect = self.cursorRect()
                rect.setWidth(max(300, c.popup().sizeHintForColumn(0) + 24))
                c.complete(rect)
                return
        if c:
            c.popup().hide()

    def canInsertFromMimeData(self, mime: Any) -> bool:  # noqa: N802 (Qt API)
        return bool(mime.hasUrls() or mime.hasImage() or super().canInsertFromMimeData(mime))

    def insertFromMimeData(self, mime: Any) -> None:  # noqa: N802 (Qt API)
        if mime.hasUrls() or mime.hasImage():           # pasted pictures and files
            self.dropped.emit(mime, None)
            return
        super().insertFromMimeData(mime)


class _ChatView(_DropMixin, QtWidgets.QTextBrowser):
    """The conversation. Accepts drops like the input; pictures in it can be dragged out."""

    dropped = QtCore.Signal(object, object)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self._press: QtCore.QPoint | None = None

    def _image_at(self, pos: QtCore.QPoint) -> str | None:
        hit = self.cursorForPosition(pos).position()
        for p in (hit, hit + 1):
            cur = QtGui.QTextCursor(self.document())
            cur.setPosition(max(0, min(p, self.document().characterCount() - 1)))
            fmt = cur.charFormat()
            if fmt.isImageFormat():
                path = QtCore.QUrl(fmt.toImageFormat().name()).toLocalFile()
                if path and os.path.isfile(path):
                    return path
        return None

    def mousePressEvent(self, e: Any) -> None:  # noqa: N802 (Qt API)
        self._press = e.position().toPoint() if e.button() == QtCore.Qt.LeftButton else None
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: Any) -> None:  # noqa: N802 (Qt API)
        pos = e.position().toPoint()
        if self._press is not None and (e.buttons() & QtCore.Qt.LeftButton) and \
                (pos - self._press).manhattanLength() > QtWidgets.QApplication.startDragDistance():
            path = self._image_at(self._press)
            if path:
                mime = QtCore.QMimeData()
                mime.setUrls([QtCore.QUrl.fromLocalFile(path)])
                mime.setImageData(QtGui.QImage(path))
                drag = QtGui.QDrag(self)
                drag.setMimeData(mime)
                pix = QtGui.QPixmap(path)
                if not pix.isNull():
                    drag.setPixmap(pix.scaledToWidth(min(160, pix.width())))
                self._press = None
                drag.exec(QtCore.Qt.CopyAction)
                return
        super().mouseMoveEvent(e)


class ChatPanel(QtWidgets.QDockWidget):
    def __init__(self, parent: Any = None) -> None:
        super().__init__("Claude", parent)
        self.setObjectName(OBJECT_NAME)
        self.session: ClaudeSession | None = None
        self.info: dict[str, Any] = _load_info()
        self.perms: dict[str, dict[str, Any]] = {}       # open permission requests by request id
        self.session_always: set[str] = set()            # tools allowed for the rest of this session
        self.attachments: list[dict[str, Any]] = []      # pictures for the next message
        self.thoughts: list[str] = []
        self.think_open = False
        self.md_start = 0
        self.md_buf = ""
        self.stopping = False
        self.restart_after_turn = False
        self.turn_steps = 0
        self.store = conversations.Store(paths.bridge_dir())
        self.pending: dict[str, str] = {}  # title and folder of a conversation until Claude names its session
        self.tool_rows: dict[str, tuple[str, float]] = {}
        self.text_open = False
        self.turn_started = 0.0
        self.current_key = _doc_key()
        self.last_text = ""
        self.turn_results: list[str] = []
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

        ctl = QtWidgets.QHBoxLayout()
        self.model_box = QtWidgets.QComboBox()
        self.model_box.setToolTip("Model (like /model in Claude Code)")
        self.effort_box = QtWidgets.QComboBox()
        self.effort_box.setToolTip("Effort: how long Claude thinks (like /effort)")
        self.pmode_box = QtWidgets.QComboBox()
        self.pmode_box.setToolTip("Permission mode: when Claude asks before acting (like Shift+Tab in Claude Code)")
        for m in PERMISSION_MODES:
            self.pmode_box.addItem(MODE_LABELS.get(m, m), m)
        for box in (self.model_box, self.effort_box, self.pmode_box):
            box.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
            ctl.addWidget(box, 1)
        lay.addLayout(ctl)

        self.view = _ChatView()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._on_link)
        self.view.dropped.connect(self.handle_drop)
        lay.addWidget(self.view, 1)

        self.attach_row = QtWidgets.QWidget()
        self.attach_lay = QtWidgets.QHBoxLayout(self.attach_row)
        self.attach_lay.setContentsMargins(0, 0, 0, 0)
        self.attach_lay.addStretch(1)
        self.attach_row.setVisible(False)
        lay.addWidget(self.attach_row)

        self.input = _Input()
        self.input.setPlaceholderText("Ask Claude… (Enter sends, Shift+Enter new line, / for commands; drop files, "
                                      "pictures or objects from the model tree here)")
        self.input.setMaximumHeight(90)
        self.input.submitted.connect(self.send)
        self.input.dropped.connect(self.handle_drop)
        self.input.escaped.connect(self.skip_feedback)
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
        self._apply_info(self.info)
        self.model_box.currentIndexChanged.connect(self._on_model_changed)
        self.effort_box.currentIndexChanged.connect(self._on_effort_changed)
        self.pmode_box.currentIndexChanged.connect(self._on_pmode_changed)
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
        account = (self.info.get("account") or {}).get("subscriptionType")
        text = (account or "Claude subscription") if mode == "subscription" else "API key (pay as you go)"
        self.mode_label.setText(f"<b>{html.escape(text)}</b>")

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
        self.conv_list.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.conv_list.setDefaultDropAction(QtCore.Qt.MoveAction)
        self.conv_list.model().rowsMoved.connect(self._on_conversations_reordered)
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
            it.setFlags(QtCore.Qt.ItemIsDropEnabled)
            font = it.font()
            font.setPointSizeF(max(7.0, font.pointSizeF() * 0.8))
            font.setBold(True)
            it.setFont(font)
            it.setForeground(QtGui.QColor("#888"))
            lst.addItem(it)

        def entry(text: str, sub: str, data: tuple, tip: str, current: bool) -> None:
            it = QtWidgets.QListWidgetItem(text)
            flags = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
            if data[0] == "doc":
                flags |= QtCore.Qt.ItemIsDragEnabled             # reorder this document's conversations
            it.setFlags(flags)
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
        for c in self.store.ordered(self.current_key):
            path = conversations.session_path(c["cwd"], c["id"])
            stamp = os.path.getmtime(path) if path and os.path.exists(path) else c.get("created", 0)
            entry(("📌 " if c.get("pinned") else "") + c["title"], _when(stamp), ("doc", c["id"]),
                  f"{c['title']}\nSession {c['id']}\nDrag to reorder; right-click for more",
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
        item_data = self.store.active(self.current_key)
        if not item_data:
            return
        menu = QtWidgets.QMenu(self)
        menu.addAction("Rename…").triggered.connect(self.rename_conversation)
        pinned = bool(item_data.get("pinned"))
        menu.addAction("Unpin" if pinned else "Pin to top").triggered.connect(
            lambda: (self.store.pin(self.current_key, item_data["id"], not pinned), self.refresh_conversations()))
        move = menu.addMenu("Move to document")
        for d in App.listDocuments().values():
            key = d.FileName or f"unsaved:{d.Name}"
            if key != self.current_key:
                move.addAction(d.Label).triggered.connect(
                    lambda _=False, k=key, label=d.Label: self._move_conversation(item_data["id"], k, label))
        move.setEnabled(bool(move.actions()))
        menu.addAction("Open in Claude Desktop").triggered.connect(self.open_in_desktop)
        menu.addSeparator()
        menu.addAction("Delete…").triggered.connect(self.delete_conversation)
        menu.exec(self.conv_list.viewport().mapToGlobal(pos))

    def _move_conversation(self, sid: str, key: str, label: str) -> None:
        if self._working():
            return
        self._close_session()
        _save_history(self._hist_key(), self.view.toHtml())
        self.store.move(self.current_key, key, sid)
        self._render_active()
        self._note(f"Moved to {label}; it continues there when you switch to that document.")

    def _on_conversations_reordered(self, *_: Any) -> None:
        ids = []
        for i in range(self.conv_list.count()):
            data = self.conv_list.item(i).data(QtCore.Qt.UserRole)
            if data and data[0] == "doc":
                ids.append(data[1])
        self.store.set_order(self.current_key, ids)
        QtCore.QTimer.singleShot(0, self.refresh_conversations)   # normalize sections after the drop

    def _choose(self, kind: str | None, value: Any) -> None:
        if kind is None:
            return
        if self._working():
            self.refresh_conversations()
            self._note("Wait until Claude is done, or press Stop, before switching conversations.")
            return
        _save_history(self._hist_key(), self.view.toHtml())
        self._close_session()
        if kind == "doc":
            self.store.set_active(self.current_key, value)
        elif kind == "ext":
            self.store.add(self.current_key, value["id"], value["title"], value["cwd"] or self._doc_folder())
        elif kind == "pending":
            return
        self._render_active()

    def new_conversation(self) -> None:
        if self._working():
            return
        _save_history(self._hist_key(), self.view.toHtml())
        self._close_session()
        self.store.start_new(self.current_key)
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
        if not item or self._working():
            return
        sure = QtWidgets.QMessageBox.question(
            self, "Claude", f"Delete the conversation \"{item['title']}\"? It also disappears from Claude Code "
                            "(terminal and Desktop app).")
        if sure != QtWidgets.QMessageBox.Yes:
            return
        self._close_session()
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
        self._close_session()       # one writer per session file: the Desktop app takes over
        error = open_in_desktop(item["id"], item["cwd"])
        self._note(error or "Opened in the Claude Desktop app. Changes made there show up here when you "
                            "come back to this conversation.", error=bool(error))

    def switch_document(self) -> None:
        """Show the conversation of the now active document (deferred while Claude is working)."""
        key = _doc_key()
        if key == self.current_key or self._working():
            return
        _save_history(self._hist_key(), self.view.toHtml())
        self._close_session()
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
        self._add_file(path)
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
        # inline instead of a dialog (a modal dialog from a Python slot stalls the bridge)
        self.feedback_pending = {"rating": rating, **ctx}
        self.input.setPlaceholderText("What should be different? Enter sends it as feedback (Esc: skip)")
        self.input.setFocus()
        self._note("Tell Claude below what should be different.")

    def _on_link(self, url: QtCore.QUrl) -> None:
        if url.scheme() == "feedback":
            rating, _, turn = (url.path() or url.toString()[len("feedback:"):]).partition(":")
            if rating in ("good", "partly", "bad") and turn.isdigit():
                self._on_feedback(rating, int(turn))
            return
        rest = url.toString()[len(url.scheme()) + 1:]
        if url.scheme() == "perm":
            decision, _, rid = rest.partition(":")
            self._answer_permission(rid, decision)
        elif url.scheme() == "think":
            self._show_thought(int(rest) if rest.isdigit() else -1)
        elif url.scheme() == "import":
            self._import_file(QtCore.QUrl.fromPercentEncoding(rest.encode()))
        elif url.scheme() == "detach":
            idx = int(rest) if rest.isdigit() else -1
            if 0 <= idx < len(self.attachments):
                self.attachments.pop(idx)
                self._render_attachments()
        else:
            QtGui.QDesktopServices.openUrl(url)

    # ----- chat -----

    def _working(self) -> bool:
        return bool(self.session and self.session.busy)

    def _close_session(self) -> None:
        if self.session:
            self.session.blockSignals(True)
            self.session.close()
            self.session = None
        self.perms.clear()
        self.session_always.clear()

    def _ensure_session(self) -> ClaudeSession | None:
        if self.session and self.session.running():
            return self.session
        item = self.store.active(self.current_key)
        if item and os.path.isdir(item["cwd"]):
            sid, cwd = item["id"], item["cwd"]                 # resume where the session file lives
        else:
            sid, cwd = None, self._doc_folder()
        model = self.model_box.currentData() or None
        effort = self.effort_box.currentData() or None
        session = ClaudeSession(sid, self.mode(), cwd, model=None if model == "default" else model, effort=effort,
                                permission_mode=self.pmode_box.currentData() or None,
                                load_plugin_dir=_params().GetBool("LoadPluginDir", True), parent=self)
        session.event.connect(self._on_event)
        session.ready.connect(self._on_ready)
        session.exited.connect(self._on_exited)
        session.failed.connect(self._on_failed)
        self.session = session
        if not session.start():
            self.session = None
            return None
        return session

    def send(self, text: str | None = None, echo: bool = True) -> None:
        text = (text if isinstance(text, str) else self.input.toPlainText()).strip()
        if not text and not self.attachments:
            return
        slash = parse_slash(text)
        if slash and self._local_command(*slash):
            self.input.clear()
            return
        fb = getattr(self, "feedback_pending", None)
        if fb and text and not slash:
            self.feedback_pending = None
            self._reset_placeholder()
            text = (f"Feedback on your last result: {fb['rating']}. I say: \"{text}\". First call record_feedback "
                    f"with rating \"{fb['rating']}\", my words, the request and one general lesson worth remembering "
                    "for future models; then fix the model accordingly.")
        images = [(a["media_type"], a["data"]) for a in self.attachments]
        if echo:
            self.input.clear()
            pics = "".join(f"<img src='{QtCore.QUrl.fromLocalFile(a['path']).toString()}' width='120'> "
                           for a in self.attachments)
            self._append(f"<p style='margin-top:10px'><b>You:</b> {html.escape(text)}</p>" + (f"<p>{pics}</p>"
                                                                                           if pics else ""))
        self.attachments = []
        self._render_attachments()
        self.last_text = text
        self._note_pending_update()
        self.current_key = _doc_key()
        queued = self._working()
        session = self._ensure_session()
        if session is None:
            return
        if self.store.active(self.current_key) is None:
            words = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("[")]
            title = words[0] if words else ("Picture" if images else text.splitlines()[0] if text else "Conversation")
            self.pending = {"title": title.strip()[:60], "cwd": session.cwd}
        if not queued:
            self.turn_results, self.max_result_chars, self.turn_steps = [], 0, 0
            self.turn_started = time.time()
            self.stopping = False
        else:
            self._note("Queued: Claude reads it after the current step.")
        # slash commands go to Claude Code as typed; everything else gets the FreeCAD context line
        prompt = text if slash else context_line() + "\n\n" + (text or "Look at the attached picture.")
        session.send_user(prompt, images)
        self._busy(True)

    def _reset_placeholder(self) -> None:
        self.input.setPlaceholderText("Ask Claude… (Enter sends, Shift+Enter new line, / for commands; drop files, "
                                      "pictures or objects from the model tree here)")

    def skip_feedback(self) -> None:
        fb = getattr(self, "feedback_pending", None)
        if fb:
            from ..bridge import learning

            learning.record_feedback(paths.bridge_dir(), fb["rating"], request=fb.get("request") or "",
                                     result=fb.get("result") or "", doc=fb.get("doc") or "", source="panel")
            self.feedback_pending = None
            self._reset_placeholder()
            self._note("Noted.")

    def _local_command(self, name: str, arg: str) -> bool:
        """Commands the panel handles itself; anything else goes to Claude Code (skills, /compact, …)."""
        if name in ("clear", "new"):
            self.new_conversation()
        elif name == "model":
            self._select(self.model_box, arg) if arg else self.model_box.showPopup()
        elif name == "effort":
            self._select(self.effort_box, arg) if arg else self.effort_box.showPopup()
        elif name in ("mode", "permissions"):
            self._select(self.pmode_box, arg) if arg else self.pmode_box.showPopup()
        elif name == "desktop":
            self.open_in_desktop()
        elif name == "help":
            names = [f"/{n} — {d}" for n, d in LOCAL_COMMANDS]
            names += [f"/{c.get('name')} — {c.get('description', '')}" for c in self.info.get("commands", [])]
            self._append("<div style='color:#666'>" + "<br>".join(html.escape(n) for n in names) + "</div>")
        else:
            return False
        return True

    def _select(self, box: QtWidgets.QComboBox, value: str) -> None:
        for i in range(box.count()):
            if value.lower() in (str(box.itemData(i)).lower(), box.itemText(i).lower()):
                box.setCurrentIndex(i)
                return
        self._note(f"Unknown value '{value}'.", error=True)

    def stop(self) -> None:
        if self._working():
            self.stopping = True
            self.status.setText("Stopping…")
            self.session.interrupt()

    def _busy(self, busy: bool) -> None:
        self.stop_btn.setEnabled(busy)
        self.status.setText("Claude is working…" if busy else "")

    # ----- session info: commands, models, account

    def _on_ready(self, info: dict[str, Any]) -> None:
        self.info = info
        try:
            with open(_info_path(), "w", encoding="utf-8") as f:
                json.dump({k: info.get(k) for k in ("commands", "models", "account", "agents")}, f)
        except OSError:
            pass
        self._apply_info(info)
        self._update_mode_label()

    def _apply_info(self, info: dict[str, Any]) -> None:
        cmds = LOCAL_COMMANDS + [(c.get("name", ""), c.get("description", ""))
                                 for c in info.get("commands", []) if c.get("name")
                                 and not c["name"].startswith("__")]
        self.input.set_commands(cmds)
        for box in (self.model_box, self.effort_box, self.pmode_box):
            box.blockSignals(True)
        self.model_box.clear()
        for m in info.get("models") or FALLBACK_MODELS:
            self.model_box.addItem(m.get("displayName") or m.get("value"), m.get("value"))
            self.model_box.setItemData(self.model_box.count() - 1, m.get("description", ""), QtCore.Qt.ToolTipRole)
        self._select_quiet(self.model_box, _params().GetString("Model", "default"))
        self._fill_effort()
        self._select_quiet(self.pmode_box, _params().GetString("PermissionMode", "default"))
        for box in (self.model_box, self.effort_box, self.pmode_box):
            box.blockSignals(False)

    def _fill_effort(self) -> None:
        current = _params().GetString("Effort", "")
        model = next((m for m in self.info.get("models") or [] if m.get("value") == self.model_box.currentData()), {})
        levels = model.get("supportedEffortLevels") or ["low", "medium", "high", "xhigh", "max"]
        self.effort_box.clear()
        self.effort_box.addItem("Effort: auto", "")
        for lv in levels:
            self.effort_box.addItem(f"Effort: {lv}", lv)
        self._select_quiet(self.effort_box, current)

    @staticmethod
    def _select_quiet(box: QtWidgets.QComboBox, value: str) -> None:
        idx = box.findData(value)
        box.setCurrentIndex(idx if idx >= 0 else 0)

    def _on_model_changed(self, _i: int) -> None:
        value = self.model_box.currentData() or "default"
        _params().SetString("Model", value)
        self.effort_box.blockSignals(True)
        self._fill_effort()
        self.effort_box.blockSignals(False)
        if self.session and self.session.running():
            self.session.control("set_model", model=value)
        self._update_mode_label()

    def _on_effort_changed(self, _i: int) -> None:
        _params().SetString("Effort", self.effort_box.currentData() or "")
        if self.session and self.session.running():     # effort is a start option: resume with it next time
            if self._working():
                self.restart_after_turn = True
            else:
                self._close_session()

    def _on_pmode_changed(self, _i: int) -> None:
        mode = self.pmode_box.currentData() or "default"
        _params().SetString("PermissionMode", mode)
        if self.session and self.session.running():
            self.session.control("set_permission_mode", mode=mode)
        if mode == "bypassPermissions":
            self._note("Bypass permissions: Claude acts without asking, including shell commands and file changes.",
                       error=True)

    # ----- events

    def _on_event(self, ev: dict[str, Any]) -> None:
        kind = ev.get("kind")
        if kind not in ("text_delta", "text", "thinking_delta"):
            self._close_text()
        if kind != "thinking_delta":
            self.think_open = False
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
                cur = self.view.textCursor()
                cur.movePosition(QtGui.QTextCursor.End)
                self.md_start, self.md_buf, self.text_open = cur.position(), "", True
            self.md_buf += ev.get("text", "")
            self._insert_text(ev.get("text", ""))
        elif kind == "thinking_delta":
            if not self.think_open:
                self.thoughts.append("")
                self._append(f"<div style='color:#888'><i>💭 <a href='think:{len(self.thoughts) - 1}'>Thinking…</a>"
                             "</i></div>")
                self.think_open = True
            self.thoughts[-1] += ev.get("text", "")
        elif kind == "tool_use":
            name = short_tool_name(ev.get("name", "?"))
            self.turn_steps += 1
            self.tool_rows[ev.get("id") or name] = (name, time.time())
            if name != "TodoWrite":
                self._append(f"<div style='color:#666'>▸ <code>{html.escape(name)}</code> "
                             f"{html.escape(_tool_summary(ev.get('input') or {}))}</div>")
        elif kind == "todos":
            self._render_todos(ev.get("items") or [])
        elif kind == "tool_result":
            name, t0 = self.tool_rows.pop(ev.get("id"), ("tool", time.time()))
            if name == "TodoWrite":
                return
            dt = time.time() - t0
            self.max_result_chars = max(self.max_result_chars, int(ev.get("size", 0)))
            if not ev.get("is_error") and ev.get("content"):
                self.turn_results.append(ev["content"])
            if ev.get("is_error"):
                self._append(f"<div style='color:#b00'>✗ <code>{html.escape(name)}</code> {dt:.1f} s: "
                             f"{html.escape(ev.get('summary', ''))}</div>")
            else:
                self._append(f"<div style='color:#2a7'>✓ <code>{html.escape(name)}</code> {dt:.1f} s</div>")
        elif kind == "permission_request":
            self._permission_card(ev)
        elif kind == "permission_denied":
            self._note(f"Not allowed by your Claude Code settings: {ev.get('tool')}", error=True)
        elif kind == "control_response" and not ev.get("ok"):
            self._note(f"Claude Code refused: {ev.get('error')}", error=True)
        elif kind == "result":
            self._finish_turn(ev)
        elif kind == "invalid":
            App.Console.PrintLog(f"ClaudeBridge chat: {ev.get('line')}\n")

    def _finish_turn(self, ev: dict[str, Any]) -> None:
        interrupted = self.stopping and ev.get("is_error")
        if ev.get("is_error") and ev.get("text") and not interrupted:
            self._note(str(ev["text"]), error=True)
        if interrupted:
            self._note("Stopped.")
        if self._working():          # queued messages follow
            return
        dt = time.time() - self.turn_started
        self._busy(False)
        changed = self._render_turn_summary()
        if changed and not interrupted:
            self._ask_feedback()
        parts = [f"Done in {dt:.0f} s"]
        if self.turn_steps:
            parts.append(f"{self.turn_steps} step{'s' if self.turn_steps != 1 else ''}")
        if self.mode() == "api_key" and ev.get("cost_usd"):
            parts.append(f"${ev['cost_usd']:.2f}")
        self.status.setText("Stopped" if interrupted else " · ".join(parts))
        self.stopping = False
        _save_history(self._hist_key(), self.view.toHtml())
        if self.restart_after_turn:
            self.restart_after_turn = False
            self._close_session()
        QtCore.QTimer.singleShot(0, self.switch_document)  # catch up on a document switch during the turn

    def _on_exited(self, code: int) -> None:
        self._close_text()
        if self.session is not None and code != 0:
            self._note(f"Claude Code ended unexpectedly (code {code}). See Report view; your next message "
                       "resumes the conversation.", error=True)
        self.session = None
        self.perms.clear()
        self._busy(False)

    # ----- permission prompts

    def _permission_card(self, ev: dict[str, Any]) -> None:
        rid, tool, inp = ev["request_id"], ev["tool"], ev.get("input") or {}
        if tool in self.session_always:
            self.session.answer_permission(rid, "allow", inp)
            self._note(f"Allowed (this session): {ev.get('summary', tool)}")
            return
        self.perms[rid] = ev
        def mono(text: str) -> str:   # wraps inside the narrow panel, unlike <pre>
            return ("<div style='font-family:monospace; white-space:pre-wrap'>"
                    + html.escape(text).replace("\n", "<br>") + "</div>")

        detail = mono(ev.get("summary", "").split(": ", 1)[-1])
        if inp.get("command"):
            detail = mono(str(inp["command"])[:1500])
            if inp.get("description"):
                detail = f"<i>{html.escape(str(inp['description']))}</i>" + detail
        elif "old_string" in inp or "new_string" in inp:
            old = "\n".join("− " + ln for ln in str(inp.get("old_string", "")).splitlines()[:8])
            new = "\n".join("+ " + ln for ln in str(inp.get("new_string", "")).splitlines()[:8])
            detail = f"<i>{html.escape(str(inp.get('file_path', '')))}</i>" + mono(old + "\n" + new)
        elif inp.get("content"):
            detail = (f"<i>{html.escape(str(inp.get('file_path', '')))}</i>"
                      + mono("\n".join(str(inp["content"]).splitlines()[:10])))
        always = _always_label(ev.get("suggestions") or [], short_tool_name(tool))
        short = always if len(always) <= 48 else always[:45].rstrip() + "…"
        self._append(
            "<table border='1' cellspacing='0' cellpadding='6' width='100%'><tr><td>"
            f"<b>🔐 Claude wants to use {html.escape(short_tool_name(tool))}</b><br>{detail}<br>"
            f"<a href='perm:allow:{rid}'>Allow once</a> · "
            f"<a href='perm:always:{rid}' title='{html.escape(always)}'>{html.escape(short)}</a> · "
            f"<a href='perm:deny:{rid}'>Deny</a></td></tr></table>")
        self.status.setText("Waiting for your approval…")
        QtWidgets.QApplication.alert(self)

    def _answer_permission(self, rid: str, decision: str) -> None:
        ev = self.perms.pop(rid, None)
        if not ev or not self.session or decision not in ("allow", "always", "deny"):
            self._note("This request is no longer open.")
            return
        # no modal dialog here: a dialog opened from a Python slot holds the GIL and stalls the bridge
        message = "The user declined this action; wait for their next message." if decision == "deny" else ""
        if decision == "always" and not ev.get("suggestions"):
            self.session_always.add(ev["tool"])
        self.session.answer_permission(rid, decision, ev.get("input") or {}, ev.get("suggestions"),
                                       message or "The user declined this action.")
        verdict = {"allow": "✓ Allowed once", "always": "✓ Allowed", "deny": "✗ Denied"}[decision]
        self._note(f"{verdict}: {ev.get('summary', ev['tool'])}"
                   + (". Type what Claude should do instead." if decision == "deny" else ""))
        if decision == "deny":
            self.input.setFocus()
        self.status.setText("Claude is working…")

    def _show_thought(self, idx: int) -> None:
        if not 0 <= idx < len(self.thoughts):
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Claude's thinking")
        lay = QtWidgets.QVBoxLayout(dlg)
        view = QtWidgets.QTextBrowser()
        view.setPlainText(self.thoughts[idx] or "(no thinking text)")
        lay.addWidget(view)
        dlg.resize(520, 420)
        dlg.show()

    def _render_todos(self, items: list[dict[str, Any]]) -> None:
        icons = {"completed": "☑", "in_progress": "◐", "pending": "☐"}
        rows = []
        for t in items:
            text = html.escape(str(t.get("content") or t.get("activeForm") or ""))
            st = t.get("status", "pending")
            text = f"<s>{text}</s>" if st == "completed" else f"<b>{text}</b>" if st == "in_progress" else text
            rows.append(f"{icons.get(st, '☐')} {text}")
        self._append("<table cellpadding='4'><tr><td><b>Tasks</b><br>" + "<br>".join(rows) + "</td></tr></table>")

    # ----- drag and drop, attachments

    def handle_drop(self, mime: Any, source: Any) -> None:
        if _from_tree(source):
            self._insert_objects()
            return
        if mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    self._add_file(url.toLocalFile())
                else:
                    self.input.insertPlainText(url.toString() + " ")
            return
        if mime.hasImage():
            img = QtGui.QImage(mime.imageData())
            folder = os.path.join(paths.bridge_dir(), "history", "img")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, time.strftime("paste-%Y%m%d-%H%M%S") + ".png")
            if not img.isNull() and img.save(path, "PNG"):
                self._add_file(path)

    def _add_file(self, path: str) -> None:
        kind = dropping.classify(path)
        if kind == "image":
            with open(path, "rb") as f:
                self.attachments.append({"path": path, "media_type": dropping.media_type(path), "data": f.read()})
            self._render_attachments()
            return
        self.input.insertPlainText(dropping.reference_line(kind, path) + " ")
        if kind == "cad":
            link = QtCore.QUrl.toPercentEncoding(path).data().decode()
            self._append(f"<div style='color:#666'>📦 {html.escape(os.path.basename(path))}: "
                         f"<a href='import:{link}'>Import into FreeCAD</a></div>")
        self.input.setFocus()

    def _render_attachments(self) -> None:
        while self.attach_lay.count() > 1:
            w = self.attach_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i, a in enumerate(self.attachments):
            btn = QtWidgets.QToolButton()
            btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            pix = QtGui.QPixmap(a["path"])
            if not pix.isNull():
                btn.setIcon(QtGui.QIcon(pix.scaled(32, 32, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)))
                btn.setIconSize(QtCore.QSize(32, 32))
            btn.setText(os.path.basename(a["path"])[:18] + " ✕")
            btn.setToolTip("Sent as a picture with your next message. Click to remove.")
            btn.clicked.connect(lambda _=False, idx=i: (self.attachments.pop(idx), self._render_attachments()))
            self.attach_lay.insertWidget(i, btn)
        self.attach_row.setVisible(bool(self.attachments))

    def _insert_objects(self) -> None:
        lines = []
        for sel in Gui.Selection.getSelectionEx("", 0)[:20]:
            obj, size = sel.Object, None
            shape = getattr(obj, "Shape", None)
            if shape is not None and not shape.isNull():
                bb = shape.BoundBox
                size = (bb.XLength, bb.YLength, bb.ZLength)
            lines.append(dropping.object_reference(obj.Label, obj.Name, obj.TypeId, size,
                                                   list(sel.SubElementNames)))
        if lines:
            self.input.insertPlainText(("\n" if self.input.toPlainText().strip() else "") + "\n".join(lines) + "\n")
            self.input.setFocus()

    def _import_file(self, path: str) -> None:
        if not os.path.isfile(path):
            self._note(f"File not found: {path}", error=True)
            return
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".fcstd" or App.ActiveDocument is None:
                Gui.open(path)
                self._note(f"Opened {os.path.basename(path)}.")
                return
            doc = App.ActiveDocument
            before = len(doc.Objects)
            doc.openTransaction(f"Import {os.path.basename(path)}")
            if ext in (".step", ".stp", ".iges", ".igs", ".brep", ".brp"):
                import ImportGui

                ImportGui.insert(path, doc.Name)
            elif ext in (".stl", ".obj", ".ply", ".off", ".3mf"):
                import Mesh

                Mesh.insert(path, doc.Name)
            elif ext == ".dxf":
                import importDXF

                importDXF.insert(path, doc.Name)
            elif ext == ".svg":
                import importSVG

                importSVG.insert(path, doc.Name)
            else:
                Gui.insert(path, doc.Name)
            doc.commitTransaction()
            doc.recompute()
            added = len(doc.Objects) - before
            if added > 0:
                self._note(f"Imported {os.path.basename(path)} ({added} object{'s' if added != 1 else ''}; "
                           "one undo step).")
            else:
                self._note(f"Nothing was imported from {os.path.basename(path)}.", error=True)
        except Exception as e:
            if App.ActiveDocument is not None:
                App.ActiveDocument.abortTransaction()
            self._note(f"Import failed: {e}", error=True)

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

    def _close_text(self) -> None:
        """Replace the streamed plain text of the last answer with rendered Markdown."""
        if not self.text_open:
            return
        self.text_open = False
        cur = self.view.textCursor()
        cur.setPosition(self.md_start)
        cur.movePosition(QtGui.QTextCursor.End, QtGui.QTextCursor.KeepAnchor)
        if hasattr(cur, "insertMarkdown") and self.md_buf.strip():
            cur.removeSelectedText()
            cur.insertMarkdown(self.md_buf)
            # leave the list/quote/code block, so what follows starts as a normal paragraph
            cur.movePosition(QtGui.QTextCursor.End)
            cur.insertBlock(QtGui.QTextBlockFormat(), QtGui.QTextCharFormat())
            lst = cur.currentList()
            if lst is not None:
                lst.remove(cur.block())
            fmt = cur.blockFormat()
            fmt.setIndent(0)
            fmt.setLeftMargin(0)
            cur.setBlockFormat(fmt)
        self._scroll()

    def _note(self, text: str, error: bool = False) -> None:
        color = "#b00" if error else "#666"
        self._append(f"<div style='color:{color}'><i>{html.escape(text)}</i></div>")

    def _scroll(self) -> None:
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def transcript(self) -> str:
        return self.view.toPlainText()


def _always_label(suggestions: list[dict[str, Any]], tool: str) -> str:
    """What "always" would allow, in words, from Claude Code's permission suggestions."""
    for s in suggestions:
        if s.get("type") == "addRules":
            rules = [f"{str(r.get('toolName')).strip(chr(39))}({str(r.get('ruleContent')).strip(chr(39))})"
                     if r.get("ruleContent") else str(r.get("toolName")).strip("'") for r in s.get("rules") or []]
            return "Always allow " + ", ".join(rules)
        if s.get("type") == "setMode":
            return {"acceptEdits": "Accept all edits this session", "bypassPermissions": "Allow everything"}.get(
                s.get("mode"), f"Switch to {s.get('mode')}")
        if s.get("type") == "addDirectories":
            return "Allow access to " + ", ".join(s.get("directories") or [])
    return f"Allow {tool} for this session"


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
