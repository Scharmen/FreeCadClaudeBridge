"""The conversation list beside the chat: documents, their conversations, moving and renaming them."""

from __future__ import annotations

import html
import os
from typing import Any

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from . import conversations, debuglog
from .common import (
    _doc_key,
    _history_path,
    _load_history,
    _params,
    _save_history,
    _when,
    doc_key,
)
from .widgets import SUBTITLE_ROLE, _ConversationDelegate


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
        return os.path.dirname(doc.FileName) if doc is not None and doc.FileName else os.path.expanduser("~")

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

        def entry(text: str, sub: str, data: tuple, tip: str, current: bool) -> None:
            it = QtWidgets.QListWidgetItem(text)
            flags = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
            if data[0] == "conv":
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
            if here and active is None:
                entry("New conversation", "not started yet", ("pending", None), "Type a message to start it", True)
            for c in group["items"]:
                stamp = c.get("updated") or c.get("created", 0)
                entry(("📌 " if c.get("pinned") else "") + c["title"], _when(stamp), ("conv", key, c["id"]),
                      f"{c['title']}\nProject: {group['label']}\nSession {c['id']}\n"
                      "Drag to reorder; right-click for more", bool(here and active and c["id"] == active["id"]))
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
        data = item.data(QtCore.Qt.UserRole)
        if not data:
            return
        if data[0] == "conv":
            self._open_conversation(data[1], data[2])
        elif data[0] == "ext":
            self._open_external(data[1])
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
