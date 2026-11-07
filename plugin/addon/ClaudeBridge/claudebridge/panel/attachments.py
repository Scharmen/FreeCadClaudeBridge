"""Pictures and files for the next message, dropped objects, imports."""

from __future__ import annotations

import html
import os
import time
from typing import Any

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from ..bridge import paths
from . import dropping
from .common import (
    _save_history,
)
from .widgets import _from_tree


class AttachmentsMixin:
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
