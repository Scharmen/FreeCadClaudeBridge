"""Run requests in FreeCAD's GUI thread.

The listener thread calls `submit()`, which emits a Qt signal (QueuedConnection)
to this QObject living in the GUI thread and waits on a Future. Calls are
serialized: a second call waits for the first; GUI_BUSY only if it waits too long.
"""

from __future__ import annotations

import threading
import traceback
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from PySide import QtCore

from . import protocol

BUSY_WAIT_S = 30.0


class OpError(Exception):
    def __init__(self, code: str, message: str, traceback: str | None = None,
                 extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.traceback = traceback
        self.extra = extra or {}


class Executor(QtCore.QObject):
    request = QtCore.Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._slot = threading.Semaphore(1)
        self._busy = threading.Event()
        self.namespace: dict[str, Any] = {}
        self.request.connect(self._run, QtCore.Qt.QueuedConnection)
        from . import ops

        self.ops = ops
        self.recorder = ops.ChangeRecorder()

    @property
    def busy(self) -> bool:
        return self._busy.is_set()

    def submit(self, op: str, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        """Called from the listener thread. Returns a response dict without `id`."""
        if op not in self.ops.OPS:
            return {"ok": False, "error": protocol.error(protocol.NOT_FOUND, f"unknown operation '{op}'")}
        if not self._slot.acquire(timeout=min(BUSY_WAIT_S, timeout)):
            return {"ok": False, "error": protocol.error(
                protocol.GUI_BUSY, "FreeCAD's GUI thread is still working on an earlier call")}
        fut: Future = Future()
        self.request.emit((op, args, fut))
        try:
            return fut.result(timeout=timeout)
        except FutureTimeout:
            fut.cancel()  # skip it if it hasn't started yet
            return {"ok": False, "error": protocol.error(
                protocol.TIMEOUT, f"no result after {timeout:.0f} s; a running C++ operation may still finish")}

    @QtCore.Slot(object)
    def _run(self, item: tuple) -> None:
        op, args, fut = item
        self._busy.set()
        try:
            if not fut.set_running_or_notify_cancel():
                return
            try:
                result = self.ops.OPS[op](args, self)
                fut.set_result({"ok": True, "result": result})
            except OpError as e:
                err = protocol.error(e.code, e.message, e.traceback)
                err.update(e.extra)
                fut.set_result({"ok": False, "error": err})
            except Exception as e:  # bug in the bridge itself
                fut.set_result({"ok": False, "error": protocol.error(
                    protocol.INTERNAL, f"{type(e).__name__}: {e}", traceback.format_exc())})
        finally:
            self._busy.clear()
            self._slot.release()
