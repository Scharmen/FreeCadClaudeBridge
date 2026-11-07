"""The small Qt pieces of the chat panel: document watcher, conversation rows, input box and chat view."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from PySide import QtCore, QtGui, QtWidgets

from .common import (
    doc_key,
)

if TYPE_CHECKING:
    from .dock import ChatPanel


class _ActiveDocWatcher:
    """Switch the panel's conversation when the user activates another document."""

    def __init__(self, panel: ChatPanel) -> None:
        self.panel = panel

    def slotActivateDocument(self, doc: Any) -> None:  # noqa: N802 (FreeCAD API)
        QtCore.QTimer.singleShot(0, self.panel.switch_document)

    def slotDeletedDocument(self, doc: Any) -> None:  # noqa: N802
        QtCore.QTimer.singleShot(0, self.panel.switch_document)

    def slotCreatedDocument(self, doc: Any) -> None:  # noqa: N802
        if self.panel._working():             # Claude made it: the conversation follows into it
            self.panel.created_in_turn.add(doc.Name)

    def slotFinishSaveDocument(self, doc: Any, filename: str) -> None:  # noqa: N802
        # saveCopy (the bridge's safety copies, snapshots) reports a save too, but the document keeps its file
        if os.path.normcase(os.path.abspath(doc.FileName or "")) == os.path.normcase(os.path.abspath(filename)):
            self.panel.on_document_saved(doc, filename)

    def slotRelabelDocument(self, doc: Any) -> None:  # noqa: N802
        self.panel.store.set_label(doc_key(doc), doc.Label)


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
