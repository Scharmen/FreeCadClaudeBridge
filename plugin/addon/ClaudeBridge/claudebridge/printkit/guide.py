"""The assembly guide as a PDF, drawn with Qt from the export's report. Runs in the FreeCAD GUI process."""

from __future__ import annotations

import json
import math
import os
from typing import Any

from PySide import QtCore, QtGui, QtWidgets

from . import plan

DPI = 150
PAGE_W, PAGE_H = int(8.27 * DPI), int(11.69 * DPI)
MARGIN = 90
LIGHT = (0.35, -0.45, 0.82)


def _project(view: tuple[float, float]) -> tuple[tuple, tuple, tuple]:
    az, el = math.radians(view[0]), math.radians(view[1])
    toward = (math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el))
    right = (-math.sin(az), math.cos(az), 0.0)
    up = (-math.sin(el) * math.cos(az), -math.sin(el) * math.sin(az), math.cos(el))
    return toward, right, up


def _dot(a: tuple, b: tuple) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


class Scene:
    """All pieces as triangles with one camera: a picture shows any subset, the new piece highlighted."""

    def __init__(self, folder: str, pieces: list[dict[str, Any]], view: tuple[float, float] = (215, 24)) -> None:
        self.tris: list[tuple[float, str, list[tuple[float, float]], tuple[int, int, int]]] = []
        toward, right, up = _project(view)
        xs: list[float] = []
        ys: list[float] = []
        for p in pieces:
            with open(os.path.join(folder, p["preview"]), encoding="utf-8") as f:
                data = json.load(f)
            base = QtGui.QColor(data["color"])
            verts = [(_dot(v, right), _dot(v, up), _dot(v, toward), v) for v in data["verts"]]
            xs += [v[0] for v in verts]
            ys += [v[1] for v in verts]
            for a, b, c in data["tris"]:
                pa, pb, pc = verts[a][3], verts[b][3], verts[c][3]
                u = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
                w = (pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2])
                n = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])
                size = math.sqrt(_dot(n, n)) or 1.0
                n = (n[0] / size, n[1] / size, n[2] / size)
                if _dot(n, toward) <= 0:
                    continue
                shade = 0.45 + 0.55 * max(0.0, _dot(n, LIGHT))
                color = (int(base.red() * shade) + 10, int(base.green() * shade) + 10, int(base.blue() * shade) + 10)
                depth = (verts[a][2] + verts[b][2] + verts[c][2]) / 3
                self.tris.append((depth, p["id"], [verts[a][:2], verts[b][:2], verts[c][:2]], color))
        self.tris.sort(key=lambda t: t[0])
        self.lo = (min(xs), min(ys)) if xs else (0.0, 0.0)
        self.hi = (max(xs), max(ys)) if xs else (1.0, 1.0)

    def image(self, size: tuple[int, int], shown: set[str] | None = None, new: str | None = None) -> QtGui.QImage:
        w, h = size
        img = QtGui.QImage(w, h, QtGui.QImage.Format_RGB32)
        img.fill(QtGui.QColor(255, 255, 255))
        scale = min((w - 20) / max(self.hi[0] - self.lo[0], 1e-6), (h - 20) / max(self.hi[1] - self.lo[1], 1e-6))
        cx, cy = (self.lo[0] + self.hi[0]) / 2, (self.lo[1] + self.hi[1]) / 2
        paint = QtGui.QPainter(img)
        paint.setRenderHint(QtGui.QPainter.Antialiasing, False)
        for _depth, pid, pts, color in self.tris:
            if shown is not None and pid not in shown:
                continue
            c = color
            if new is not None and pid != new:             # what is already built steps back
                c = tuple(int(200 + 0.2 * v) for v in color)
            q = QtGui.QColor(*c)
            paint.setPen(q)
            paint.setBrush(q)
            paint.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(w / 2 + (x - cx) * scale, h / 2 - (y - cy) * scale)
                                               for x, y in pts]))
        paint.end()
        return img


class Doc:
    """Pages of text and pictures."""

    def __init__(self, path: str) -> None:
        self.writer = QtGui.QPdfWriter(path)
        self.writer.setResolution(DPI)
        self.writer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.A4))
        self.writer.setPageMargins(QtCore.QMarginsF(0, 0, 0, 0))
        self.paint = QtGui.QPainter(self.writer)
        self.y = MARGIN
        self.page_w = self.paint.viewport().width() or PAGE_W
        self.page_h = self.paint.viewport().height() or PAGE_H

    def new_page(self) -> None:
        self.writer.newPage()
        self.y = MARGIN

    def _room(self, need: int) -> None:
        if self.y + need > self.page_h - MARGIN:
            self.new_page()

    def text(self, text: str, size: int = 22, bold: bool = False, gap: int = 14, color: str = "#222222") -> None:
        font = QtGui.QFont("Helvetica")
        font.setPixelSize(size)
        font.setBold(bold)
        self.paint.setFont(font)
        self.paint.setPen(QtGui.QColor(color))
        width = self.page_w - 2 * MARGIN
        box = self.paint.boundingRect(QtCore.QRect(MARGIN, 0, width, 10000), QtCore.Qt.TextWordWrap, text)
        self._room(box.height() + gap)
        self.paint.drawText(QtCore.QRect(MARGIN, self.y, width, box.height() + 4), QtCore.Qt.TextWordWrap, text)
        self.y += box.height() + gap

    def picture(self, img: QtGui.QImage, height: int) -> None:
        self._room(height + 16)
        width = self.page_w - 2 * MARGIN
        self.paint.drawImage(QtCore.QRect(MARGIN, self.y, width, height), img)
        self.y += height + 16

    def row(self, cells: list[str], widths: list[float], swatch: str | None = None, bold: bool = False) -> None:
        font = QtGui.QFont("Helvetica")
        font.setPixelSize(18)
        font.setBold(bold)
        self.paint.setFont(font)
        total = self.page_w - 2 * MARGIN
        self._room(34)
        x = MARGIN
        self.paint.setPen(QtGui.QColor("#222222"))
        for i, (cell, w) in enumerate(zip(cells, widths, strict=True)):
            shift = 0
            if swatch and i == 1:
                self.paint.fillRect(QtCore.QRect(x, self.y + 5, 20, 20), QtGui.QColor(swatch))
                self.paint.setPen(QtGui.QColor("#222222"))
                shift = 28
            span = int(total * w)
            self.paint.drawText(QtCore.QRect(x + shift, self.y, span - shift - 6, 30), QtCore.Qt.AlignVCenter,
                                self.paint.fontMetrics().elidedText(cell, QtCore.Qt.ElideMiddle, span - shift - 6))
            x += span
        self.y += 32

    def close(self) -> None:
        self.paint.end()


def _spin() -> None:
    app = QtWidgets.QApplication.instance()
    if app:
        app.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)


def build(report: dict[str, Any], folder: str, path: str) -> str:
    """Write the guide to `path` and return it."""
    lang = report.get("lang", "de")
    t = plan.TEXT.get(lang, plan.TEXT["en"])
    doc = Doc(path)
    pieces = report.get("pieces", [])
    name = {p["id"]: p["objects"][0] for p in pieces}
    scene = Scene(folder, pieces) if pieces else None
    moving_of = {pid: m for m in report.get("moving", []) for pid in m.get("pieces", [])}
    try:
        doc.text(t["title"].format(name=report["name"]), 42, True, 26)
        doc.text(f"{t['printer']}: {report['printer'].get('name', '')}", 24)
        if scene:
            doc.picture(scene.image((1000, 620)), 620)
        whole = report.get("whole")
        doc.text(t["contents"], 28, True)
        if whole:
            size = whole["size_mm"]
            doc.text(t["whole_tip"].format(file=whole["file"], x=size[0], y=size[1], z=size[2], n=whole["colors"]))
            for number, color in enumerate(whole.get("palette", []), 1):
                doc.text(f"{t['filament']} {number}: {plan.color_name(color, lang)} ({color})", 20, gap=4)
            if not whole["fits"]:
                doc.text(t["too_big_whole"], color="#b02020")
        together = report.get("together")
        if together:
            size = together["size_mm"]
            doc.text(t["whole_fixed"], 20)
            doc.text(t["together_title"], 28, True)
            doc.text(t["together_tip"].format(file=together["file"], x=size[0], y=size[1], z=size[2],
                                              n=together["colors"]))
            for number, color in enumerate(together["palette"], 1):
                doc.text(f"{t['filament']} {number}: {plan.color_name(color, lang)} ({color})", 20, gap=4)
            for f in together["moving_files"]:
                doc.text(f, 20, gap=6)
            if together["axles"]["count"]:
                doc.text(f"{t['axles']}: {together['axles']['count']} × {together['axles']['file']}", 20, gap=6)
            for line in plan.together_lines(t, together["moving"]):
                doc.text(line, 22, gap=8)
            if not together["fits"]:
                doc.text(t["too_big_whole"], color="#b02020")
        if not pieces:
            if not together:
                doc.text(t["no_split"])
        else:
            for plate in report.get("plates", []):
                doc.text(f"{plate['color_name']}, {t['plate']} {plate['number']}: {plate['file']}", 20, gap=6)
            for key, title in (("dowels", t["dowels"]), ("axles", t["axles"])):
                info = report.get(key) or {}
                if info.get("count"):
                    doc.text(f"{title}: {info['count']} × {info['file']}", 20, gap=6)
            doc.new_page()
            doc.text(t["overview"], 28, True)
            widths = [0.36, 0.2, 0.2, 0.08, 0.16]
            doc.row([t["piece"], t["color"], t["file"], t["plate"], t["size"]], widths, bold=True)
            for p in pieces:
                size = "×".join(str(s) for s in p["size_mm"])
                doc.row([p["objects"][0], p["color_name"], os.path.basename(p["stl"]), str(p["plate"] or "-"), size],
                        widths, p["color"])
            doc.new_page()
            built: set[str] = set()
            axles_done: set[str] = set()
            for n, pid in enumerate(report.get("order", [])):
                _spin()
                piece = next(p for p in pieces if p["id"] == pid)
                doc._room(560)
                doc.text(t["step"].format(n=n + 1), 26, True, 8)
                if n == 0:
                    lines = [t["start_with"].format(piece=name[pid], color=piece["color_name"])]
                else:
                    lines = plan.step_lines(t, pid, report.get("joints", []), built, name, moving_of, axles_done)
                built.add(pid)
                doc.picture(scene.image((900, 460), set(built), pid), 400)
                for line in lines:
                    doc.text(line, 22, gap=8)
                doc.y += 18
        doc.new_page()
        doc.text(t["print_tips"], 28, True)
        doc.text(t["tips_resin"] if report["printer"].get("process") == "resin" else t["tips_fdm"])
        doc.text(t["dry_fit"])
        need = [p["objects"][0] for p in pieces if p.get("support")]
        if need:
            doc.text(t["support"].format(list=", ".join(need)))
        if report.get("dowels", {}).get("count"):
            doc.text(t["fit_test"])
        if report.get("painted"):
            doc.text(t["painted"].format(list=", ".join(report["painted"])))
        if report.get("warnings"):
            doc.text(t["warnings"], 24, True)
            for w in report["warnings"]:
                doc.text("• " + w, 20, gap=6)
    finally:
        doc.close()
    return path
