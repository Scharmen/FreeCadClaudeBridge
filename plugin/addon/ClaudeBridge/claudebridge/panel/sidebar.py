"""The conversation list beside the chat: documents, their conversations, moving and renaming them."""

from __future__ import annotations

import html
import os
import time
from typing import Any

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from . import conversations, debuglog, look
from .common import (
    _buttons,
    _doc_key,
    _history_path,
    _load_history,
    _params,
    _save_history,
    _when,
    doc_key,
)
from .widgets import DEPTH_ROLE, SUBTITLE_ROLE, _ConversationDelegate


class SidebarMixin:
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
        from ..bridge import ops

        if doc is not None and doc.FileName:
            return os.path.dirname(doc.FileName)
        root = ops.projects_root()                       # new work starts in the projects folder, not in ~
        try:
            os.makedirs(root, exist_ok=True)
            return root
        except OSError:
            return os.path.expanduser("~")

    def refresh_conversations(self) -> None:
        """Conversations grouped by project (FreeCAD document), the current one first. Within a project
        the most recent activity is on top; opening one does not move it."""
        lst = self.conv_list
        lst.blockSignals(True)
        lst.clear()
        active = self.store.active(self.current_key)
        role = QtCore.Qt.UserRole
        doc = App.ActiveDocument
        if doc is not None:
            self.store.set_label(self.current_key, doc.Label)
        for d in App.listDocuments().values():
            self.doc_keys[d.Name] = doc_key(d)

        def header(text: str) -> None:
            it = QtWidgets.QListWidgetItem(text.upper())
            it.setFlags(QtCore.Qt.ItemIsDropEnabled)
            font = it.font()
            font.setPointSizeF(max(7.0, font.pointSizeF() * 0.8))
            font.setBold(True)
            it.setFont(font)
            it.setForeground(QtGui.QColor("#888"))
            lst.addItem(it)

        def entry(text: str, sub: str, data: tuple, tip: str, current: bool, nested: bool = False) -> None:
            it = QtWidgets.QListWidgetItem(("🧩 " if nested else "") + text)
            it.setData(DEPTH_ROLE, 1 if nested else 0)
            flags = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
            if data[0] == "conv" and not nested:
                flags |= QtCore.Qt.ItemIsDragEnabled             # reorder within its project
            it.setFlags(flags)
            it.setData(role, data)
            it.setData(SUBTITLE_ROLE, sub)
            it.setToolTip(tip)
            lst.addItem(it)
            if current:
                lst.setCurrentItem(it)

        for group in self.store.groups(self.current_key):
            key, here = group["key"], group["key"] == self.current_key
            closed = not here and not key.startswith("<") and self._doc_for_key(key) is None
            header(group["label"] + ("  (closed)" if closed else ""))
            pending_sub = self.store.pending_sub(key) if here and active is None else None
            if here and active is None and not pending_sub:
                entry("New conversation", "not started yet", ("pending", None), "Type a message to start it", True)
            for c, depth in self.store.nested(key):
                stamp = c.get("updated") or c.get("created", 0)
                current = bool(here and active and c["id"] == active["id"])
                if depth:
                    part = c.get("part") or {}
                    entry(c["title"], f"part {part.get('name', '?')} · {_when(stamp)}", ("conv", key, c["id"]),
                          f"Sub chat for the part {part.get('label')} ({part.get('name')})\nIt changes only this "
                          "part; the main chat above holds the full model.\nRight-click for more", current, True)
                    continue
                subs = sum(1 for s in group["items"] if s.get("parent") == c["id"]) + \
                    sum(1 for p in self.store.pending_subs(key) if p["parent"] == c["id"])
                when = _when(stamp) + (f" · 🧩 {subs} sub chat{'s' if subs != 1 else ''}" if subs else "")
                entry(("📌 " if c.get("pinned") else "") + c["title"], when, ("conv", key, c["id"]),
                      f"{c['title']}\nProject: {group['label']}\nSession {c['id']}\n"
                      "Drag to reorder; right-click for more (also: a sub chat for one part)", current)
                for p in self.store.pending_subs(key):
                    if p["parent"] != c["id"]:
                        continue
                    shown = bool(pending_sub and pending_sub["parent"] == c["id"]
                                 and pending_sub["part"].get("name") == p["part"].get("name"))
                    entry(f"{p['part'].get('label')}", "not started yet · click to open",
                          ("pendsub", key, p["part"].get("name"), c["id"]),
                          f"Sub chat for the part {p['part'].get('label')}, not started yet: open it and type a "
                          "message; it begins with the main chat's history.\nRight-click to remove it", shown, True)
        others = conversations.folder_sessions(self._doc_folder(), exclude=self.store.linked_ids())
        if others:
            header("In this folder")
            for info in others:
                entry(info["title"], f"{info['source'] or 'session'} · {_when(info['updated'])}", ("ext", info),
                      f"{info['title']}\nStarted in: {info['source'] or '?'}\nClick to continue it here", False)
        lst.blockSignals(False)

    def _doc_for_key(self, key: str) -> Any:
        for d in App.listDocuments().values():
            if doc_key(d) == key:
                return d
        return None

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
            t = self._theme()
            for role, text in conversations.transcript(path):
                body = html.escape(text).replace("\n", "<br>")
                if role == "user":
                    parts.append(look.user_bubble(text, "", t))
                elif role == "assistant":
                    parts.append(look.claude_header(t) + f"<p>{body}</p>")
                else:
                    parts.append(f"<div><font color='{t['muted']}'>⚙ {body}</font></div>")
            self.view.setHtml("".join(parts))
            self._note("Redrawn from the session file (it continued outside this panel).")
        else:
            self.view.setHtml(saved)
        self.refresh_conversations()
        self._scroll()

    def _on_conversation_item(self, item: QtWidgets.QListWidgetItem) -> None:
        data = item.data(QtCore.Qt.UserRole)
        if not data:
            return
        if data[0] == "conv":
            self._open_conversation(data[1], data[2])
        elif data[0] == "ext":
            self._open_external(data[1])
        elif data[0] == "pendsub":
            self._open_pending_sub(data[1], data[2], data[3])
        else:
            self._choose(data[0], data[1])

    def _open_conversation(self, key: str, sid: str) -> None:
        """A conversation from the list: in this project switch to it; in another project bring that
        document up (opening its file if needed); a closed unsaved document's conversation comes here."""
        if key == self.current_key:
            self._choose("doc", sid)
            return
        if self._working():
            self._note("Wait until Claude is done, or press Stop, before switching conversations.")
            self.refresh_conversations()
            return
        self.store.set_active(key, sid)
        doc = self._doc_for_key(key)
        if doc is None and not key.startswith(("unsaved:", "<")) and os.path.isfile(key):
            doc = self._open_model(key)
        if doc is not None:
            if doc_key(doc) != key:                    # the file opened under another spelling of its path
                self.store.rekey(key, doc_key(doc))
            self._bring_up(doc)
            QtCore.QTimer.singleShot(0, self.switch_document)
            QtCore.QTimer.singleShot(0, self._focus_conversation)
        else:
            _save_history(self._hist_key(), self.view.toHtml())
            self._close_session()
            self.store.move(key, self.current_key, sid)
            self._render_active()
            self._note(f"Its document ({self.store.label(key)}) is no longer open; the conversation continues "
                       "here.")

    def _open_external(self, info: dict[str, Any]) -> None:
        """A session not linked to a document yet (terminal, Desktop app, an older panel): open the model it
        works on and link it there, so it continues with its own model and its whole history."""
        if self._working():
            self._note("Wait until Claude is done, or press Stop, before switching conversations.")
            self.refresh_conversations()
            return
        from ..bridge import paths as bpaths

        path = conversations.session_path(info.get("cwd") or self._doc_folder(), info["id"])
        model = conversations.session_model(path, ignore=(bpaths.bridge_dir(),)) if path else None
        doc = self._open_model(model) if model else None
        if doc is None:
            self._choose("ext", info)                  # no model found: it continues with the current document
            return
        _save_history(self._hist_key(), self.view.toHtml())
        self._close_session()
        self.store.add(doc_key(doc), info["id"], info["title"], info.get("cwd") or self._doc_folder())
        self.store.set_label(doc_key(doc), doc.Label)
        debuglog.log("link_session", session=info["id"], model=model)
        self._bring_up(doc)
        QtCore.QTimer.singleShot(0, self.switch_document)

    def _open_model(self, path: str) -> Any:
        """The open document of a FreeCAD file, opening it if needed (None if it cannot be opened).
        Gui.open does nothing when called from code in FreeCAD 1.1, so App.openDocument it is."""
        want = os.path.normcase(os.path.realpath(path))
        for d in App.listDocuments().values():
            if d.FileName and os.path.normcase(os.path.realpath(d.FileName)) == want:
                return d
        try:
            return App.openDocument(path)
        except Exception as exc:  # noqa: BLE001 (a broken or locked file)
            self._note(f"Could not open {path}: {exc}", error=True)
            return None

    def _bring_up(self, doc: Any) -> None:
        """Make a document the active one, with its 3D view in front."""
        App.setActiveDocument(doc.Name)
        try:
            Gui.setActiveDocument(doc.Name)
            view = Gui.getDocument(doc.Name).ActiveView
            area = Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
            for sub in area.subWindowList() if area is not None else []:
                if sub.widget() is not None and sub.widget().windowTitle().startswith(doc.Label) and view:
                    area.setActiveSubWindow(sub)
                    break
        except Exception:  # noqa: BLE001 (no GUI document, e.g. while FreeCAD closes)
            pass

    def _conversation_menu(self, pos: QtCore.QPoint) -> None:
        item = self.conv_list.itemAt(pos)
        data = item.data(QtCore.Qt.UserRole) if item else None
        if not data or data[0] == "pending":
            return
        if data[0] == "pendsub":
            menu = QtWidgets.QMenu(self)
            menu.addAction("Open").triggered.connect(lambda: self._open_pending_sub(data[1], data[2], data[3]))
            menu.addAction("Remove this sub chat").triggered.connect(
                lambda: (self.store.discard_pending_sub(data[1], data[2], data[3]), self._render_active()))
            menu.exec(self.conv_list.viewport().mapToGlobal(pos))
            return
        if data[0] == "conv":
            if data[1] != self.current_key:
                self._open_conversation(data[1], data[2])
                return
            self._choose("doc", data[2])
        else:
            self._choose(*data)
        item_data = self.store.active(self.current_key)
        if not item_data:
            return
        menu = QtWidgets.QMenu(self)
        sub_menu = menu.addMenu("New sub chat for part")
        sub_menu.setToolTipsVisible(True)
        doc = App.ActiveDocument
        selected = self._selected_part()
        if selected:
            sub_menu.addAction(f"Selected: {selected['label']}").triggered.connect(
                lambda _=False, p=selected: self._start_sub(p))
            sub_menu.addSeparator()
        for part in self._part_candidates(doc) if doc is not None else []:
            sub_menu.addAction(part["label"]).triggered.connect(lambda _=False, p=part: self._start_sub(p))
        sub_menu.setEnabled(bool(sub_menu.actions()))
        menu.addSeparator()
        menu.addAction("Rename…").triggered.connect(self.rename_conversation)
        pinned = bool(item_data.get("pinned"))
        menu.addAction("Unpin" if pinned else "Pin to top").triggered.connect(
            lambda: (self.store.pin(self.current_key, item_data["id"], not pinned), self.refresh_conversations()))
        move = menu.addMenu("Move to document")
        for d in App.listDocuments().values():
            key = doc_key(d)
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
        orders: dict[str, list[str]] = {}
        for i in range(self.conv_list.count()):
            data = self.conv_list.item(i).data(QtCore.Qt.UserRole)
            if data and data[0] == "conv":
                orders.setdefault(data[1], []).append(data[2])
        for key, ids in orders.items():
            self.store.set_order(key, ids)
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
        self._focus_conversation()

    def new_conversation(self) -> None:
        if self._working():
            self._note("Claude is still working on this conversation. Press Stop first, then start a new one.")
            return
        _save_history(self._hist_key(), self.view.toHtml())
        self._close_session()
        self.store.start_new(self.current_key)
        self.view.clear()
        self._note("New conversation.")
        self.refresh_conversations()

    # ----- sub chats: one part of the model, opened only when asked for

    def _current_part(self) -> dict[str, str] | None:
        """The part the shown conversation works on (a sub chat, also one not started yet), else None."""
        item = self.store.active(self.current_key)
        if item:
            return item.get("part")
        pending = self.store.pending_sub(self.current_key)
        return pending["part"] if pending else None

    @staticmethod
    def _part_candidates(doc: Any) -> list[dict[str, str]]:
        """The model's visible top-level pieces a sub chat can work on (containers and solids, no helpers)."""
        out = []
        for o in doc.RootObjects:
            if o.TypeId.startswith(("App::Origin", "Sketcher::", "App::Line", "App::Plane", "App::TextDocument",
                                    "Spreadsheet::")):
                continue
            visible = getattr(getattr(o, "ViewObject", None), "Visibility", True)
            container = o.TypeId in ("App::Part", "PartDesign::Body", "App::DocumentObjectGroup", "App::Link")
            shape = getattr(o, "Shape", None)
            solid = shape is not None and not shape.isNull() and bool(shape.Solids)
            if visible and (container or solid):
                out.append({"name": o.Name, "label": o.Label})
        return out

    @staticmethod
    def _selected_part() -> dict[str, str] | None:
        sel = Gui.Selection.getSelection()
        return {"name": sel[0].Name, "label": sel[0].Label} if sel else None

    def new_sub_chat(self, name: str = "") -> None:
        """/sub [part]: a sub chat for one part of the model (the selected one without a name)."""
        doc = App.ActiveDocument
        if doc is None:
            self._note("Open the model first.")
            return
        if name.strip():
            want = name.strip().lower()
            candidates = self._part_candidates(doc)
            part = next((p for p in candidates if want in (p["label"].lower(), p["name"].lower())), None) or \
                next((p for p in candidates if want in p["label"].lower()), None)
            obj = None if part else (doc.getObject(name.strip()) or next(iter(doc.getObjectsByLabel(name.strip())),
                                                                          None))
            part = part or ({"name": obj.Name, "label": obj.Label} if obj else None)
        else:
            part = self._selected_part()
        if not part:
            names = ", ".join(p["label"] for p in self._part_candidates(doc)) or "none found"
            self._note(f"Which part? Select it in the model or name it, e.g. /sub Roof. Parts: {names}.")
            return
        self._start_sub(part)

    def offer_sub_chats(self, parts: list[dict[str, str]]) -> None:
        """Claude offers sub chats (the user asked for them): listed under this chat and as buttons; nothing
        opens until the user clicks one."""
        self._close_text()
        active = self.store.active(self.current_key)
        main = self.store.find(self.current_key, active["parent"]) if active and active.get("parent") else active
        if main is not None:
            for p in parts:
                self.store.add_pending_sub(self.current_key, main["id"], p)
            self.refresh_conversations()
        self._append("<p>🧩 <b>Sub chats</b> — listed under this chat on the left; each starts with this chat's "
                     "history and changes only its part:</p>"
                     + _buttons([(f"🧩 {p['label']}", f"sub:{p['name']}", "option") for p in parts],
                                tip={f"sub:{p['name']}": f"Open a sub chat for {p['label']}" for p in parts}))

    def _open_pending_sub(self, key: str, name: str, parent: str) -> None:
        """A prepared sub chat from the list: show it (opening its model first if needed)."""
        if self._working():
            self._note("Wait until Claude is done, or press Stop, before switching conversations.")
            self.refresh_conversations()
            return
        if key != self.current_key:
            self.store.open_pending_sub(key, name, parent)
            doc = self._doc_for_key(key) or (self._open_model(key) if os.path.isfile(key) else None)
            if doc is not None:
                self._bring_up(doc)
                QtCore.QTimer.singleShot(0, self.switch_document)
                QtCore.QTimer.singleShot(0, self._focus_conversation)
            return
        main = self.store.find(key, parent)
        p = next((x for x in self.store.pending_subs(key) if x["parent"] == parent and x["part"].get("name") == name),
                 None)
        if main is None or p is None:
            self.refresh_conversations()
            return
        self._start_sub(p["part"], main)

    def _start_sub(self, part: dict[str, str], main: dict[str, Any] | None = None) -> None:
        if main is None:
            active = self.store.active(self.current_key)
            pending = self.store.pending_sub(self.current_key) if active is None else None
            parent = (active or {}).get("parent") or (pending or {}).get("parent")
            main = self.store.find(self.current_key, parent) if parent else active
        if main is None:
            self._note("A sub chat continues a main chat: start the main chat for this model first.")
            return
        if self._working():
            self._note("Wait until Claude is done, or press Stop, before opening a sub chat.")
            return
        _save_history(self._hist_key(), self.view.toHtml())
        self._close_session()
        self.store.start_sub(self.current_key, main["id"], part)
        self.view.clear()
        self._note(f"Sub chat for {part['label']}. It starts with everything the main chat \"{main['title']}\" "
                   "knows, and Claude changes only this part. The main chat keeps the full model. Type a message "
                   "to start it.")
        self.refresh_conversations()
        self._focus_conversation()

    def _focus_conversation(self) -> None:
        """A sub chat shows its part (selected, zoomed to); back in a main chat the whole model shows again."""
        doc = App.ActiveDocument
        if doc is None:
            return
        part = self._current_part()
        try:
            Gui.Selection.clearSelection()
            if part:
                obj = doc.getObject(part.get("name", "")) or next(iter(doc.getObjectsByLabel(part.get("label", ""))),
                                                                   None)
                if obj is None:
                    self._note(f"The part {part.get('label')} is no longer in the model.")
                    return
                Gui.Selection.addSelection(obj)
                Gui.SendMsgToActiveView("ViewSelection")
                self._shown_part = True
            elif getattr(self, "_shown_part", False):
                Gui.SendMsgToActiveView("ViewFit")
                self._shown_part = False
        except Exception:  # noqa: BLE001 (no 3D view, e.g. while FreeCAD closes)
            pass

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
        item = self.store.active(self.current_key)
        if not item:
            self._note("Start the conversation first; then it can be opened in the Claude Desktop app.")
            return
        if self._working():
            self._note("Wait until Claude is done, or press Stop, before opening this chat in Claude Desktop.")
            return
        _save_history(self._hist_key(), self.view.toHtml())
        own = self.session.proc if self.session and self.session.running() else None
        self._close_session()       # one writer per session file: the Desktop app takes over
        self._note("Opening the chat in the Claude Desktop app…")
        self._handoff_when_free(item, own, time.time())

    def _handoff_when_free(self, item: dict[str, Any], own: Any, since: float) -> None:
        """Claude Code refuses a session that still runs, and the panel's own process needs a moment to end
        after its input closes: hand over once it has (at most 10 s)."""
        if own is not None and own.state() != QtCore.QProcess.NotRunning and time.time() - since < 10:
            QtCore.QTimer.singleShot(200, lambda: self._handoff_when_free(item, own, since))
            return
        from .claude_process import open_in_desktop
        from .stream import desktop_link, handoff_busy

        def done(error: str | None) -> None:
            if error and handoff_busy(error):
                # already running elsewhere, usually in Claude Desktop itself: bring it up there
                QtGui.QDesktopServices.openUrl(QtCore.QUrl(desktop_link(item["id"])))
                self._note("This chat is already open (in Claude Desktop or a terminal); Claude Desktop shows it "
                           "now. If it runs in a terminal, type /desktop there.")
            elif error:
                self._note(f"Claude Desktop did not open the chat: {error}", error=True)
            else:
                self._note("Opened in the Claude Desktop app: the chat is listed there now as a local Code "
                           "session (rename it there if you like). Changes made there show up here when you "
                           "come back to this conversation.")

        error = open_in_desktop(item["id"], item["cwd"], done, parent=self)
        if error:
            self._note(error, error=True)

    def switch_document(self) -> None:
        """Show the conversation of the now active document (deferred while Claude is working). A document
        Claude created during the turn takes the running conversation along instead of an empty chat."""
        key = _doc_key()
        if key == self.current_key or self._working():
            return
        doc = App.ActiveDocument
        carry = doc is not None and doc.Name in self.created_in_turn
        self.created_in_turn.clear()
        active = self.store.active(self.current_key)
        if carry and active and self.store.active(key) is None:
            debuglog.log("carry", src=self.current_key, dst=key, session=active["id"])
            self.store.move(self.current_key, key, active["id"])
            self.current_key = key
            self.refresh_conversations()
            return
        _save_history(self._hist_key(), self.view.toHtml())
        self._close_session()
        self.current_key = key
        self._render_active()

    def on_document_saved(self, doc: Any, filename: str) -> None:
        """Saving gives a document a new key (its file, also after Save As); its conversations go with it."""
        name, label = doc.Name, doc.Label
        old = self.doc_keys.get(name, f"unsaved:{getattr(doc, 'Uid', '') or name}")
        self.doc_keys[name] = filename
        if old == filename:
            return
        debuglog.log("rekey", old=old, new=filename)
        self.store.rekey(old, filename)
        self.store.set_label(filename, label)
        if self.current_key == old:
            self.current_key = filename
        self.refresh_conversations()
