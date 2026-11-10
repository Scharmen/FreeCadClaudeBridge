"""The "Claude" chat panel: a dock widget on the right of FreeCAD's main window."""

from __future__ import annotations

import html
import json
import os
import sys
import time
from typing import Any

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from ..bridge import paths
from . import activity, conversations, debuglog, look, secrets, tips, usage
from .approvals import ApprovalsMixin
from .attachments import AttachmentsMixin
from .claude_process import ClaudeSession
from .common import (
    BUTTON_COLORS,
    FALLBACK_MODELS,
    LOCAL_COMMANDS,
    MODE_LABELS,
    OBJECT_NAME,
    _buttons,
    _doc_key,
    _info_path,
    _load_info,
    _load_usage,
    _params,
    _save_history,
    _tool_summary,
    _usage_path,
    context_line,
)
from .sidebar import SidebarMixin
from .stream import (
    PERMISSION_MODES,
    check_init,
    parse_slash,
    short_tool_name,
    turn_summary,
)
from .updater import UpdatesMixin
from .widgets import _ActiveDocWatcher, _ChatView, _Input


class ChatPanel(SidebarMixin, ApprovalsMixin, AttachmentsMixin, UpdatesMixin, QtWidgets.QDockWidget):
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
        self.activity = activity.Activity()
        self.usage = _load_usage()
        self.stall_noted = False
        self.created_in_turn: set[str] = set()     # documents Claude created during the running turn
        self.doc_keys: dict[str, str] = {}         # document name -> its key before the next save
        self.spin = 0
        self.turn_steps = 0
        self.store = conversations.Store(paths.bridge_dir())
        self.pending: dict[str, str] = {}  # title and folder of a conversation until Claude names its session
        self.tool_rows: dict[str, tuple[str, float, Any]] = {}   # name, start, cursor at the end of its line
        self.text_open = False
        self.turn_started = 0.0
        self.current_key = _doc_key()
        self.last_text = ""
        self.turn_results: list[str] = []
        self.turn_inputs: list[str] = []          # tool inputs of this turn: a script may name the file it writes
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
        self.menu_btn.setToolTip("Menu: subscription or API key, conversations, settings, debug report")
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
        from .. import __version__
        self.version_label = QtWidgets.QLabel(f"v{__version__}")
        self.version_label.setStyleSheet("color: gray")
        self.version_label.linkActivated.connect(lambda _url: self.update_addon())
        self.version_label.setToolTip(f"ClaudeBridge addon version {__version__}. After an update, restart "
                                      "FreeCAD to load the new version.")
        head.addWidget(self.version_label)
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
            self.pmode_box.setItemData(self.pmode_box.count() - 1, tips.MODE_TIPS.get(m, ""), QtCore.Qt.ToolTipRole)
        for box in (self.model_box, self.effort_box, self.pmode_box):
            box.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        ctl.addWidget(self.model_box, 1)            # two rows, so "Effort: auto → medium" stays readable
        ctl.addWidget(self.effort_box, 1)
        ctl2 = QtWidgets.QHBoxLayout()
        ctl2.addWidget(self.pmode_box, 1)
        self.effort_box.currentIndexChanged.connect(self._effort_box_tip)
        self.auto_btn = QtWidgets.QToolButton()
        self.auto_btn.setText("⚡ Auto")
        self.auto_btn.setCheckable(True)
        self.auto_btn.setToolTip("Auto: allow every action Claude asks for, answer its questions with the "
                                 "recommended option and accept its plans, without asking you. Deny rules in your "
                                 "Claude Code settings still apply.")
        self.auto_btn.setStyleSheet("QToolButton:checked { background-color: #c77700; color: white; "
                                    "border-radius: 4px; font-weight: bold; }")
        ctl2.addWidget(self.auto_btn)
        lay.addLayout(ctl)
        lay.addLayout(ctl2)

        self.view = _ChatView()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._on_link)
        self.view.dropped.connect(self.handle_drop)
        lay.addWidget(self.view, 1)

        self.approve_bar = QtWidgets.QFrame()
        self.approve_bar.setFrameShape(QtWidgets.QFrame.StyledPanel)
        bar = QtWidgets.QHBoxLayout(self.approve_bar)
        bar.setContentsMargins(6, 4, 6, 4)
        self.approve_label = QtWidgets.QLabel()
        self.approve_label.setMinimumWidth(60)
        bar.addWidget(self.approve_label, 1)
        self.option_lay = QtWidgets.QHBoxLayout()          # answer buttons when Claude asks a question
        bar.addLayout(self.option_lay)
        self.approve_btns: dict[str, QtWidgets.QPushButton] = {}
        for key, label in (("allow", "Allow once"), ("always", "Always"), ("deny", "Deny")):
            btn = QtWidgets.QPushButton(label)
            btn.setStyleSheet(f"QPushButton {{ background-color: {BUTTON_COLORS[key]}; color: white; "
                              "border-radius: 4px; padding: 3px 8px; font-weight: bold; }")
            btn.clicked.connect(lambda _=False, k=key: self._bar_answer(k))
            bar.addWidget(btn)
            self.approve_btns[key] = btn
        self.approve_bar.setVisible(False)
        lay.addWidget(self.approve_bar)

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
        self.print_btn = QtWidgets.QPushButton("3D print…")
        self.print_btn.setToolTip("Export the model for a 3D printer")
        self.print_btn.clicked.connect(self._print_export)
        row.addWidget(self.print_btn)
        self.stop_btn = QtWidgets.QPushButton("Stop")
        self.stop_btn.setToolTip("Stop Claude now; a FreeCAD operation already running finishes on its own")
        self.stop_btn.clicked.connect(self.stop)
        self.stop_btn.setEnabled(False)
        row.addWidget(self.stop_btn)
        self.send_btn = QtWidgets.QPushButton("Send")
        self.send_btn.setToolTip("Send your message (Enter). While Claude works it is queued and read after the "
                                 "current step.")
        self.send_btn.clicked.connect(self.send)
        row.addWidget(self.send_btn)
        lay.addLayout(row)

        self.setWidget(body)
        title_tips = (("qt_dockwidget_floatbutton", "Undock the chat panel into its own window, or dock it back"),
                      ("qt_dockwidget_closebutton", "Close the chat panel (View → Panels → Claude brings it back)"))
        for name, tip in title_tips:
            btn = self.findChild(QtWidgets.QAbstractButton, name)
            if btn is not None:
                btn.setToolTip(tip)
        self._apply_info(self.info)
        self.model_box.currentIndexChanged.connect(self._on_model_changed)
        self.effort_box.currentIndexChanged.connect(self._on_effort_changed)
        self.pmode_box.currentIndexChanged.connect(self._on_pmode_changed)
        self.auto_btn.setChecked(_params().GetBool("AutoApprove", False))
        self.auto_btn.toggled.connect(self._on_auto_toggled)
        self._update_mode_label()
        visible = _params().GetBool("SidebarVisible", True)
        self.side_btn.setChecked(visible)
        self.sidebar.setVisible(visible)
        width = _params().GetInt("SidebarWidth", 180)
        self.splitter.setSizes([width, max(260, self.width() - width)])
        self._render_active()
        self._doc_watcher = _ActiveDocWatcher(self)
        App.addDocumentObserver(self._doc_watcher)
        self.ticker = QtCore.QTimer(self)
        self.ticker.setInterval(500)
        self.ticker.timeout.connect(self._tick)
        self.ticker.start()
        debuglog.prune()
        QtCore.QTimer.singleShot(0, self._report_crashes)
        self._init_updates()
        self.plan_timer = QtCore.QTimer(self)            # a plan change shows within a minute
        self.plan_timer.timeout.connect(self._update_mode_label)
        self.plan_timer.start(60 * 1000)

    # ----- settings -----

    def mode(self) -> str:
        return _params().GetString("AuthMode", "subscription")

    def _build_menu(self) -> QtWidgets.QMenu:
        menu = QtWidgets.QMenu(self)
        from .. import __version__
        menu.addAction(f"ClaudeBridge {__version__}").setEnabled(False)
        menu.addAction("Check for updates…").triggered.connect(lambda: self.check_for_updates(manual=True))
        menu.addSeparator()
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
        menu.addAction("Create debug report…").triggered.connect(self.create_debug_report)
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
        token_type = (self.info.get("account") or {}).get("subscriptionType")
        plan = usage.plan_name(usage.read_profile(), token_type)      # the profile follows an upgrade at once
        text = (plan or "Claude subscription") if mode == "subscription" else "API key (pay as you go)"
        label = f"<b>{html.escape(text)}</b>"
        limits = self.usage.limits_text() if mode == "subscription" else ""
        if limits:
            color = {"critical": "#c00", "warn": "#c77700"}.get(self.usage.level(), "gray")
            label += f" <span style='color:{color}'>· {html.escape(limits)}</span>"
        self.mode_label.setText(label)
        self.mode_label.setToolTip(self.usage.limits_tooltip() if limits else "Plan usage shows here after "
                                   "Claude's first answer (Claude Code reports it with every answer).")

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
            if decision == "ans":
                rid, _, pick = rid.partition(":")
                qi, _, oi = pick.partition(":")
                if qi.isdigit() and oi.isdigit():
                    self._answer_question(rid, int(qi), int(oi))
            else:
                self._answer_permission(rid, decision)
        elif url.scheme() == "report":
            self.create_debug_report()
        elif url.scheme() == "update":
            self.update_addon()
        elif url.scheme() == "sub":
            self.new_sub_chat(rest)
        elif url.scheme() == "restorecopy":
            self._open_safety_copy(QtCore.QUrl.fromPercentEncoding(rest.encode("utf-8")))
        elif url.scheme() in ("open", "reveal"):
            path = QtCore.QUrl.fromPercentEncoding(rest.encode("utf-8"))      # spaces arrive as %20
            _open_path(path, reveal=url.scheme() == "reveal")
        elif url.scheme() == "stop":
            self.stop()
        elif url.scheme() == "snapshots":
            from ..bridge import paths as bpaths

            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(os.path.join(bpaths.bridge_dir(),
                                                                                  "snapshots")))
        elif url.scheme() == "notes":
            self._show_design_notes()
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
        self._update_approval_bar()

    def _ensure_session(self) -> ClaudeSession | None:
        if self.session and self.session.running():
            return self.session
        item = self.store.active(self.current_key)
        pending = self.store.pending_sub(self.current_key) if item is None else None
        main = self.store.find(self.current_key, pending["parent"]) if pending else None
        fork = bool(main and os.path.isdir(main["cwd"]))
        if item and os.path.isdir(item["cwd"]):
            sid, cwd = item["id"], item["cwd"]                 # resume where the session file lives
        elif main and fork:
            sid, cwd = main["id"], main["cwd"]                 # a new sub chat: the main chat's history, forked
        else:
            sid, cwd = None, self._doc_folder()
        model = self.model_box.currentData() or None
        effort = self.effort_box.currentData() or None
        session = ClaudeSession(sid, self.mode(), cwd, model=None if model == "default" else model, effort=effort,
                                permission_mode=self.pmode_box.currentData() or None,
                                load_plugin_dir=_params().GetBool("LoadPluginDir", True), fork=fork, parent=self)
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
        pictures = [a["path"] for a in self.attachments if a.get("path")]
        if echo:
            self.input.clear()
            pics = "".join(f"<img src='{QtCore.QUrl.fromLocalFile(a['path']).toString()}' width='120'> "
                           for a in self.attachments)
            self._append(look.user_bubble(text, f"<br>{pics}" if pics else "", self._theme()))
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
            self._begin_turn()
        else:
            self._note("Queued: Claude reads it after the current step.")
        active = self.store.active(self.current_key)
        if active:
            self.store.touch(self.current_key, active["id"])
        debuglog.log("send", doc=self.current_key, session=session.session_id, text=text, images=len(images),
                     queued=queued)
        # slash commands go to Claude Code as typed; everything else gets the FreeCAD context line
        prompt = text if slash else context_line() + "\n\n" + (text or "Look at the attached picture.")
        focus = tips.part_focus(self._current_part())
        if focus and not slash:
            prompt = focus + "\n" + prompt
        if pictures and not slash:
            prompt += "\n\n" + usage.picture_line(pictures)
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
        elif name == "sub":
            self.new_sub_chat(arg)
        elif name == "model":
            self._select(self.model_box, arg) if arg else self.model_box.showPopup()
        elif name == "effort":
            self._select(self.effort_box, arg) if arg else self.effort_box.showPopup()
        elif name in ("mode", "permissions"):
            self._select(self.pmode_box, arg) if arg else self.pmode_box.showPopup()
        elif name == "desktop":
            self.open_in_desktop()
        elif name == "usage":
            self._show_usage()
        elif name == "help":
            names = [f"/{n} — {d}" for n, d in LOCAL_COMMANDS]
            names += [f"/{c.get('name')} — {c.get('description', '')}" for c in self.info.get("commands", [])]
            self._append("<div style='color:#666'>" + "<br>".join(html.escape(n) for n in names) + "</div>")
        else:
            return False
        return True

    def _save_usage(self) -> None:
        try:
            with open(_usage_path(), "w", encoding="utf-8") as f:
                json.dump(self.usage.save(), f)
        except OSError:
            pass

    def _show_usage(self) -> None:
        lines = []
        if self.usage.model:
            lines.append(f"Model: {usage.model_name(self.usage.model, self.info.get('models') or [])}")
        lines.append(f"Last answer: {self.usage.turn_text() or 'no answer yet'}")
        limits = self.usage.limits_tooltip()
        lines.append(limits if limits else "Plan limits: not reported yet; Claude Code sends them with every answer.")
        self._append("<div style='color:#666'>" + "<br>".join(html.escape(x) for x in lines) + "</div>")

    def _select(self, box: QtWidgets.QComboBox, value: str) -> None:
        for i in range(box.count()):
            if value.lower() in (str(box.itemData(i)).lower(), box.itemText(i).lower()):
                box.setCurrentIndex(i)
                return
        self._note(f"Unknown value '{value}'.", error=True)

    def _print_export(self) -> None:
        from ..printkit import dialog

        dialog.show()

    def stop(self) -> None:
        if self._working():
            debuglog.log("stop", activity=self.activity.phase)
            self.stopping = True
            self.status.setText("Stopping…")
            self.session.interrupt()

    def _begin_turn(self) -> None:
        self.turn_results, self.max_result_chars, self.turn_steps = [], 0, 0
        self.turn_inputs = []
        self.turn_started = time.time()
        self.usage.start_turn()
        self.stopping = False
        self.stall_noted = False
        self.activity.start(time.time())

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
        for label, value, tip in usage.model_items(info.get("models") or FALLBACK_MODELS):
            self.model_box.addItem(label, value)
            self.model_box.setItemData(self.model_box.count() - 1, tip, QtCore.Qt.ToolTipRole)
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
        known = self._auto_effort()
        chosen = self.model_box.currentData() or "default"
        resolved = model.get("resolvedModel") or ""
        last = known.get(resolved) or known.get(chosen)
        auto_label, auto_tip = tips.effort_auto_item(last, resolved or chosen)
        self.effort_box.addItem(auto_label, "")
        self.effort_box.setItemData(0, auto_tip, QtCore.Qt.ToolTipRole)
        for lv in levels:
            self.effort_box.addItem(f"Effort: {lv}", lv)
            self.effort_box.setItemData(self.effort_box.count() - 1, tips.EFFORT_TIPS.get(lv, ""),
                                        QtCore.Qt.ToolTipRole)
        self._select_quiet(self.effort_box, current)
        self._effort_box_tip()

    def _effort_box_tip(self, *_: Any) -> None:
        """The box's hover text says what the shown setting does."""
        tip = self.effort_box.itemData(self.effort_box.currentIndex(), QtCore.Qt.ToolTipRole) or ""
        self.effort_box.setToolTip("Effort: how long Claude thinks (like /effort)" + (f"\n\n{tip}" if tip else ""))

    @staticmethod
    def _auto_effort() -> dict[str, str]:
        """The effort Claude Code last chose on "auto", per model (from the session files)."""
        try:
            data = json.loads(_params().GetString("AutoEffort", "") or "{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _learn_auto_effort(self) -> None:
        """After a turn on "auto": read the effort Claude Code used and show it in the effort list."""
        item = self.store.active(self.current_key)
        path = conversations.session_path(item["cwd"], item["id"]) if item else None
        found = conversations.last_effort(path) if path else None
        if not found or self.effort_box.currentData():
            return
        known = self._auto_effort()
        chosen = self.model_box.currentData() or "default"          # the alias the list shows, e.g. "opus"
        if known.get(found[0]) != found[1] or known.get(chosen) != found[1]:
            known[found[0]] = known[chosen] = found[1]
            _params().SetString("AutoEffort", json.dumps(known))
            self.effort_box.blockSignals(True)
            self._fill_effort()
            self.effort_box.blockSignals(False)

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
        """Every event from Claude Code. A bug in handling one must not silently stop the panel:
        it is logged with its traceback and shown once, with a debug report button."""
        try:
            self._handle_event(ev)
        except Exception as e:
            import traceback

            debuglog.log("panel_error", event=ev.get("kind"), error=repr(e), traceback=traceback.format_exc())
            App.Console.PrintError(f"ClaudeBridge chat: error handling {ev.get('kind')}: {e!r}\n")
            if not getattr(self, "_error_noted", False):
                self._error_noted = True
                self._note(f"The chat panel hit an internal error ({e!r}); it keeps going, but please create a "
                           "debug report.", error=True)
                self._append(_buttons([("Create debug report", "report:", "option")]))

    def _handle_event(self, ev: dict[str, Any]) -> None:
        kind = ev.get("kind")
        if kind not in ("text_delta", "thinking_delta"):
            debuglog.log("event", **{k: v for k, v in ev.items() if k != "content"})
        if kind == "init" and self.activity.phase == "idle":   # a queued message got a turn of its own
            self._begin_turn()
            self._busy(True)
        was = self.activity.phase
        progressed = self.activity.on_event(ev, time.time())
        self.usage.on_event(ev)
        if kind in ("rate_limit", "result"):
            self._save_usage()
            self._update_mode_label()
        if progressed:            # a thinking update that repeats its token count does not end a stall
            self.stall_noted = False
        if was == "thinking" and self.activity.phase != "thinking":
            self._thought_line()
        if kind not in ("text_delta", "text", "thinking_delta", "thinking_start"):
            self._close_text()
        if kind not in ("thinking_delta", "thinking_start"):
            self.think_open = False
        if kind == "init":
            sid = ev.get("session_id")
            if sid:
                item = self.store.active(self.current_key)
                if item is None:
                    self.store.add(self.current_key, sid, self.pending.get("title", ""),
                                   self.pending.get("cwd", self._doc_folder()),
                                   **self.store.take_pending_sub(self.current_key))
                    _save_history(f"session:{sid}", self.view.toHtml())
                elif item["id"] != sid:
                    self.store.replace(self.current_key, item["id"], sid)
                self.refresh_conversations()
            for problem in check_init(ev, self.mode()):
                self._note(problem, error=True)
        elif kind in ("text_delta", "text"):
            if not self.text_open:
                self._append(look.claude_header(self._theme()))
                cur = self.view.textCursor()
                cur.movePosition(QtGui.QTextCursor.End)
                cur.insertBlock(QtGui.QTextBlockFormat(), QtGui.QTextCharFormat())   # the answer below its header
                self.md_start, self.md_buf, self.text_open = cur.position(), "", True
            self.md_buf += ev.get("text", "")
            self._insert_text(ev.get("text", ""))
        elif kind in ("thinking_start", "thinking_delta"):
            if not self.think_open:
                self.thoughts.append("")
                self.think_open = True
            self.thoughts[-1] += ev.get("text", "")
        elif kind == "tool_use":
            name = short_tool_name(ev.get("name", "?"))
            self.turn_steps += 1
            line_end = None
            self.status.setText(f"◐ Running {name}…")
            self.status.repaint()          # visible before FreeCAD's GUI thread may block on the call
            if name not in ("TodoWrite", "ToolSearch"):      # ToolSearch only loads tool definitions
                self._append(look.tool_line(name, _tool_summary(ev.get("input") or {}), self._theme()))
                line_end = self.view.textCursor()
                line_end.movePosition(QtGui.QTextCursor.End)      # the result's ✓ goes on this line
                line_end.setKeepPositionOnInsert(True)            # also when more is appended meanwhile
            self.tool_rows[ev.get("id") or name] = (name, time.time(), line_end)
            self.turn_inputs.append(json.dumps(ev.get("input") or {}))
        elif kind == "todos":
            self._render_todos(ev.get("items") or [])
        elif kind == "tool_result":
            name, t0, line_end = self.tool_rows.pop(ev.get("id"), ("tool", time.time(), None))
            if name in ("TodoWrite", "ToolSearch"):
                return
            dt = time.time() - t0
            self.max_result_chars = max(self.max_result_chars, int(ev.get("size", 0)))
            if not ev.get("is_error") and ev.get("content"):
                self.turn_results.append(ev["content"])
            done = look.tool_done(not ev.get("is_error"), dt, str(ev.get("summary", ""))[:200], self._theme())
            if line_end is not None:
                line_end.clearSelection()        # its anchor moved with what was appended since: no selection
                line_end.insertHtml(done)
            else:
                self._append(f"<div>{look.tool_icon(name)} <code>{html.escape(name)}</code>{done}</div>")
        elif kind == "permission_request":
            self._permission_card(ev)
        elif kind == "api_retry":
            what = f"error {ev['status']}" if ev.get("status") else "no answer"
            what += f" ({ev['error']})" if ev.get("error") else ""
            self._note(f"⚠ The Claude API answered with {what}. Claude Code tries again in "
                       f"{ev.get('delay_s', 0):.0f} s (attempt {ev.get('attempt')} of {ev.get('max_retries')}).",
                       error=True)
        elif kind == "permission_denied":
            self._note(f"Not allowed by your Claude Code settings: {ev.get('tool')}", error=True)
        elif kind == "control_response" and not ev.get("ok"):
            self._note(f"Claude Code refused: {ev.get('error')}", error=True)
        elif kind == "result":
            if self.activity.phase == "thinking":
                self._thought_line()
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
        self.activity.stop()
        self._busy(False)
        active = self.store.active(self.current_key)
        if active:
            self.store.touch(self.current_key, active["id"])
            self.refresh_conversations()
        changed = self._render_turn_summary()
        self._autosave()
        self._render_new_files()
        if changed and not interrupted:
            self._ask_feedback()
        parts = [f"Done in {dt:.0f} s"]
        if self.turn_steps:
            parts.append(f"{self.turn_steps} step{'s' if self.turn_steps != 1 else ''}")
        if self.usage.turn_text():
            parts.append(self.usage.turn_text())
        if self.mode() == "api_key" and ev.get("cost_usd"):
            parts.append(f"${ev['cost_usd']:.2f}")
        if not interrupted:
            icons = ["⏱", "🔧", "🪙", "💵"]
            self._append(look.footer([f"{icons[i]} {p}" for i, p in enumerate(parts)], self._theme()))
        self.status.setText("Stopped" if interrupted else " · ".join(parts))
        self.status.setToolTip("Tokens Claude wrote in this turn (thinking included, as reported by Claude Code) "
                               "and how much of the model's context window the conversation fills.")
        self.stopping = False
        _save_history(self._hist_key(), self.view.toHtml())
        self._learn_auto_effort()
        if self.restart_after_turn:
            self.restart_after_turn = False
            self._close_session()
        QtCore.QTimer.singleShot(0, self.switch_document)  # catch up on a document switch during the turn

    def _on_exited(self, code: int) -> None:
        self._close_text()
        debuglog.log("exited", code=code, activity=self.activity.phase)
        self.activity.stop()
        if self.session is not None and code != 0:
            self._note(f"Claude Code ended unexpectedly (code {code}); your next message resumes the "
                       "conversation.", error=True)
            self._append(_buttons([("Create debug report", "report:", "option")]))
        self.session = None
        self.perms.clear()
        self._update_approval_bar()
        self._busy(False)

    # ----- permission prompts

    def _tick(self) -> None:
        """Every half second while Claude works: what it is doing, and whether it went quiet."""
        now = time.time()
        gap = now - getattr(self, "_last_tick", now)
        self._last_tick = now
        if not self._working():
            return
        if gap > 5:          # the GUI thread was blocked: FreeCAD computed something, that is not a stall
            self.activity.alive(now)
        self.spin = (self.spin + 1) % 4
        text = self.activity.describe(now)
        if text and not self.perms:
            self.status.setText("◐◓◑◒"[self.spin] + " " + text)
        from ..bridge import paths as bpaths
        from ..bridge import settings as bsettings

        limit = bsettings.load(bpaths.bridge_dir())["chat.stall_seconds"]
        ex = _executor()
        note = self.activity.long_thinking(now, activity.LONG_THINKING_SECONDS)
        if note:
            debuglog.log("long_thinking", note=note)
            self._note(f"💭 {note}")
        warning = self.activity.stall(now, limit, freecad_busy=bool(ex and ex.busy))
        if warning and not self.stall_noted:
            self.stall_noted = True
            debuglog.log("stall", warning=warning, activity=self.activity.phase,
                         freecad_op=getattr(ex, "current_op", ""))
            self._note(f"⏳ {warning}", error=True)
            self._append(_buttons([("Stop", "stop:", "deny"), ("Create debug report", "report:", "option")]))

    def _thought_line(self) -> None:
        secs = self.activity.last_thought_seconds
        if secs is None:
            return
        idx = len(self.thoughts) - 1
        text = self.thoughts[idx] if idx >= 0 else ""
        label = f"💭 Thought for {secs} s" if secs >= 1 else "💭 Thought briefly"
        if text.strip():
            label = f"<a href='think:{idx}'>{label}</a>"
        self._append(f"<div style='color:#888'><i>{label}</i></div>")
        self.activity.last_thought_seconds = None

    def _report_crashes(self) -> None:
        from ..addon import _state

        for c in _state.pop("crashes", []):
            first = next((ln for ln in str(c.get("detail", "")).splitlines() if ln.strip()), "")
            debuglog.log("crash_noticed", **c)
            self._note(f"⚠ FreeCAD crashed on {c.get('started')} while Claude ran {c.get('op')} in "
                       f"{c.get('doc')}: {first[:120]}. FreeCAD's document recovery offers the last autosave; "
                       "the bridge also keeps copies of unsaved work.", error=True)
            self._append(_buttons([("Open copies", "snapshots:", "option"),
                                   ("Create debug report", "report:", "option")]))

    def create_debug_report(self) -> None:
        """Everything needed to understand a problem, in one file (secrets redacted)."""
        import platform
        import sys as _sys

        from .. import __version__
        from ..bridge import audit, crashguard, diagnostics
        from ..bridge import paths as bpaths
        from ..bridge import settings as bsettings

        bd = bpaths.bridge_dir()
        ex = _executor()
        s = self.session
        sections = {
            "Versions": "\n".join([f"ClaudeBridge {__version__}", f"FreeCAD {'.'.join(App.Version()[:3])}",
                                   f"Python {_sys.version.split()[0]}", f"{platform.platform()}",
                                   f"Claude Code {self.info.get('claude_code_version', '?')}",
                                   f"Plugin root {bpaths.load_config().get('plugin_root', '?')}"]),
            "Settings": json.dumps(bsettings.load(bd), indent=2),
            "Chat panel": "\n".join([
                f"document key: {self.current_key}",
                f"conversation: {(self.store.active(self.current_key) or {}).get('id')}",
                f"session running: {bool(s and s.running())}, in a turn: {s.turn.open if s else False}, "
                f"waiting for a turn: {s.turn.waiting if s else False}",
                f"activity: {self.activity.phase} — {self.activity.describe(time.time())}",
                f"open approvals: {len(self.perms)}, auto: {self.auto_btn.isChecked()}",
                f"model: {self.model_box.currentData()}, effort: {self.effort_box.currentData()}, "
                f"mode: {self.pmode_box.currentData()}"]),
            "FreeCAD": "\n".join([f"executor busy: {bool(ex and ex.busy)}, current op: {getattr(ex, 'current_op', '')}",
                                  "documents: " + ", ".join(f"{d.Label} ({len(d.Objects)} objects)"
                                                            for d in App.listDocuments().values())]),
            "Conversation (last part)": self.view.toPlainText()[-6000:],
        }
        if ex is not None and getattr(ex, "console", None) is not None:
            try:
                lines = ex.console.since(0, "log", 400)["lines"]
                sections["Report view (last messages)"] = "\n".join(
                    f"{m.get('level', '')}: {m.get('text', '')}" for m in lines[-80:])
            except Exception:
                pass
        logs = {"Panel log": debuglog.path_today(), "Audit log": audit.log_path(),
                "Crashes": os.path.join(bd, crashguard.CRASH_LOG)}
        path = diagnostics.write_report(bd, sections, logs)
        debuglog.log("report", path=path)
        QtWidgets.QApplication.clipboard().setText(path)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(os.path.dirname(path)))
        self._note(f"Debug report written (path copied): {path}. Secrets are redacted; attach it to an issue "
                   "or send it to whoever helps you.")

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

    def _show_design_notes(self) -> None:
        doc = App.ActiveDocument
        notes = doc.getObject("DesignNotes") if doc else None
        if notes is None or not getattr(notes, "Text", ""):
            self._note("This document has no design notes yet.")
            return
        cur = self.view.textCursor()
        cur.movePosition(QtGui.QTextCursor.End)
        cur.insertBlock()
        if hasattr(cur, "insertMarkdown"):
            cur.insertMarkdown(look.markdown_for_qt(notes.Text))
        else:
            cur.insertText(notes.Text)
        cur.movePosition(QtGui.QTextCursor.End)
        cur.insertBlock(QtGui.QTextBlockFormat(), QtGui.QTextCharFormat())   # leave the list
        lst = cur.currentList()
        if lst is not None:
            lst.remove(cur.block())
        self.view.ensureCursorVisible()

    def _render_turn_summary(self) -> bool:
        s = turn_summary(self.turn_results)
        self.last_summary = s
        parts = []
        for key, label in (("created", "created"), ("changed", "changed"), ("deleted", "deleted")):
            if s[key]:
                names = ", ".join(s[key][:4]) + (" …" if len(s[key]) > 4 else "")
                parts.append(f"{len(s[key])} {label} ({html.escape(names)})")
        self.last_result = "; ".join(parts)
        table = look.changes_table(s, self._theme())
        if table:
            self._append(table)
        if "DesignNotes" in s["created"] + s["changed"]:
            self._append("<div>📝 <a href='notes:'>Why it looks like this</a> (design notes, saved with the "
                         "document)</div>")
        for e in s["errors"][:5]:
            self._append(look.note(f"{e.get('label') or e.get('name', '?')} is in an error state "
                                   f"({', '.join(e.get('state', []))})", "error", self._theme()))
        return bool(s["created"] or s["changed"] or s["deleted"])

    def _autosave(self) -> None:
        """Save the models Claude changed in this answer (setting chat.autosave). Work that only lived in the
        open FreeCAD was lost when FreeCAD closed; the bridge's safety copies stay as well."""
        from ..bridge import paths as bpaths
        from ..bridge import settings as bsettings

        try:
            on = bool(bsettings.load(bpaths.bridge_dir())["chat.autosave"])
        except Exception:
            on = True
        saved, unsaved = [], []
        for name in (getattr(self, "last_summary", None) or {}).get("documents") or []:
            if name not in App.listDocuments():
                continue
            doc = App.getDocument(name)
            if not doc.FileName:
                unsaved.append(doc.Label)
            elif on:
                try:
                    doc.save()
                    saved.append(os.path.basename(doc.FileName))
                except Exception as exc:  # noqa: BLE001 (read-only file, full disk)
                    self._note(f"Could not save {doc.Label}: {exc}", error=True)
        if saved:
            self._append(look.footer([f"💾 Saved {', '.join(saved)}"], self._theme()))
        if unsaved:
            self._note(f"{', '.join(unsaved)} has no file yet, so it is lost when FreeCAD closes: ask Claude to "
                       "save it, or use File → Save As.")

    def _render_new_files(self) -> None:
        """Files Claude wrote in this answer (exports, saves, script output), each with a link to open it and
        one to show it in its folder. Temporary files and the bridge's own copies are left out."""
        import tempfile

        from ..bridge import paths as bpaths

        candidates = [p for content in self.turn_results + self.turn_inputs for p in look.files_in(content)]
        skip = [tempfile.gettempdir(), "/tmp", "/private/tmp", "/var/folders", "/private/var/folders",
                bpaths.bridge_dir(), os.path.join(os.path.expanduser("~"), ".claude")]
        fresh = look.fresh_files(candidates, self.turn_started - 1, skip)
        if fresh:
            self._append(look.files_table(fresh, self._theme(), _file_browser()))

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
            cur.insertMarkdown(look.markdown_for_qt(self.md_buf))
            self._style_tables(self.md_start)
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
        self._append(look.note(text, "error" if error else "info", self._theme()))

    def _theme(self) -> dict[str, str]:
        return look.theme(dark=self.view.palette().base().color().lightness() < 128)

    def _style_tables(self, start: int) -> None:
        """Markdown tables from Claude get lines, a tinted header row and zebra rows."""
        t = self._theme()
        doc = self.view.document()
        seen: set[int] = set()
        block = doc.findBlock(start)
        while block.isValid():
            cur = QtGui.QTextCursor(block)
            table = cur.currentTable()
            if table is not None and id(table) not in seen:
                seen.add(id(table))
                fmt = table.format()
                fmt.setBorder(1)
                fmt.setBorderStyle(QtGui.QTextFrameFormat.BorderStyle_Solid)
                fmt.setBorderBrush(QtGui.QBrush(QtGui.QColor(t["table_line"])))
                fmt.setCellSpacing(0)
                fmt.setCellPadding(4)
                if hasattr(fmt, "setBorderCollapse"):
                    fmt.setBorderCollapse(True)
                table.setFormat(fmt)
                for r in range(table.rows()):
                    for c in range(table.columns()):
                        cell = table.cellAt(r, c)
                        cf = cell.format()
                        if r == 0:
                            cf.setBackground(QtGui.QColor(t["table_head"]))
                            cf.setFontWeight(QtGui.QFont.Bold)
                        elif r % 2 == 0:
                            cf.setBackground(QtGui.QColor(t["zebra"]))
                        cell.setFormat(cf)
            block = block.next()

    def _scroll(self) -> None:
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def transcript(self) -> str:
        return self.view.toPlainText()


def _file_browser() -> str:
    return {"darwin": "Finder", "win32": "Explorer"}.get(sys.platform, "folder")


def _open_path(path: str, reveal: bool) -> None:
    """Open a file with its app, or show it selected in its folder (Finder, Explorer, the file manager)."""
    if not os.path.exists(path):
        return
    if reveal and sys.platform == "darwin":
        QtCore.QProcess.startDetached("/usr/bin/open", ["-R", path])
    elif reveal and sys.platform == "win32":
        QtCore.QProcess.startDetached("explorer", ["/select,", os.path.normpath(path)])
    else:
        target = os.path.dirname(path) if reveal else path
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(target))


_panel: dict[str, Any] = {}


def _executor() -> Any:
    from ..addon import _state

    return _state.get("executor")


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
