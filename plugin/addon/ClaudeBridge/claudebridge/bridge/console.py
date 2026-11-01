"""Capture Report view messages into a ring buffer.

FreeCAD 1.1 has no Python API to observe App.Console, so we watch the Report
view widget. Messages arrive there after the next event loop pass, not within
the same call. The level is taken from the text color FreeCAD uses for it.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any

MAX_LINES = 2000
LEVELS = ("log", "message", "warning", "error")


class ConsoleBuffer:
    """Pure buffer logic (unit-testable)."""

    def __init__(self, maxlen: int = MAX_LINES) -> None:
        self.lines: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self.seq = 0

    def add(self, text: str, level: str) -> None:
        text = text.rstrip()
        if not text:
            return
        self.seq += 1
        self.lines.append({"seq": self.seq, "time": time.strftime("%H:%M:%S"), "level": level, "text": text[:2000]})

    def since(self, seq: int = 0, level: str = "message", limit: int = 200) -> dict[str, Any]:
        min_rank = LEVELS.index(level) if level in LEVELS else 0
        items = [ln for ln in self.lines if ln["seq"] > seq and LEVELS.index(ln["level"]) >= min_rank]
        truncated = len(items) > limit
        return {"latest_seq": self.seq, "lines": items[-limit:], "truncated": truncated}


def _rgb(unsigned: int) -> tuple[int, int, int]:
    return (unsigned >> 24) & 0xFF, (unsigned >> 16) & 0xFF, (unsigned >> 8) & 0xFF


class ReportViewWatcher:
    """Hooks the Report view's QTextEdit; classifies new blocks by the color FreeCAD gives them."""

    def __init__(self, buffer: ConsoleBuffer) -> None:
        import FreeCAD as App
        import FreeCADGui as Gui
        from PySide import QtWidgets

        self.buffer = buffer
        p = App.ParamGet("User parameter:BaseApp/Preferences/OutputWindow")
        defaults = {"colorText": 0x000000FF, "colorLogging": 0x0000FFFF,
                    "colorWarning": 0xFFAA00FF, "colorError": 0xFF0000FF}
        self.colors = {
            _rgb(p.GetUnsigned("colorText", defaults["colorText"])): "message",
            _rgb(p.GetUnsigned("colorLogging", defaults["colorLogging"])): "log",
            _rgb(p.GetUnsigned("colorWarning", defaults["colorWarning"])): "warning",
            _rgb(p.GetUnsigned("colorError", defaults["colorError"])): "error",
        }
        widgets = [w for w in Gui.getMainWindow().findChildren(QtWidgets.QTextEdit) if w.objectName() == "Report view"]
        self.view = widgets[0] if widgets else None
        self.processed = self.view.document().blockCount() - 1 if self.view else 0
        if self.view:
            self.view.textChanged.connect(self._on_change)

    def _level(self, block: Any) -> str:
        # The Report view colors lines with a syntax highlighter: the color lives in the
        # block layout's additional formats, not in the character formats.
        formats = block.layout().formats()
        if not formats:
            return "message"
        c = formats[-1].format.foreground().color()
        rgb = (c.red(), c.green(), c.blue())
        if rgb in self.colors:
            return self.colors[rgb]
        # closest known color
        return min(self.colors.items(), key=lambda kv: sum((a - b) ** 2 for a, b in zip(kv[0], rgb, strict=True)))[1]

    def _on_change(self) -> None:
        doc = self.view.document()
        count = doc.blockCount()
        if count - 1 < self.processed:  # Report view was cleared
            self.processed = 0
        # the last block is the one being written; process complete ones only
        for i in range(self.processed, count - 1):
            block = doc.findBlockByNumber(i)
            text = block.text()
            if len(text) > 10 and text[2] == ":" and text[5] == ":":
                text = text[8:].lstrip()  # strip the "HH:MM:SS" timestamp FreeCAD adds
            self.buffer.add(text, self._level(block))
        self.processed = max(self.processed, count - 1)
