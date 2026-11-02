"""Run requests in FreeCAD's GUI thread.

The listener thread calls `submit()`, which emits a Qt signal (QueuedConnection)
to this QObject living in the GUI thread and waits on a Future. Calls are
serialized: a second call waits for the first; GUI_BUSY only if it waits too long.
"""

from __future__ import annotations

import os
import threading
import time
import traceback
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from PySide import QtCore

from . import audit, crashguard, paths, protocol

BUSY_WAIT_S = 30.0
SAFETY_OPS = {"run_python", "run_command", "import", "task_dialog"}   # copy unsaved work before these
READ_ONLY_OPS = {"status_gui", "get_tree", "get_object", "inspect_shape", "inspect_sketch", "get_changes",
                 "get_console", "list_commands"}


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
        self.current_op = ""
        self._last_snapshot: dict[str, float] = {}
        self.request.connect(self._run, QtCore.Qt.QueuedConnection)
        from . import ops
        from .console import ConsoleBuffer
        from .journal import Journal, install_observers

        self.ops = ops
        self.recorder = ops.ChangeRecorder()
        self.journal = Journal()
        self._observers = install_observers(self.journal)
        self.console = ConsoleBuffer()
        try:
            from .console import ReportViewWatcher

            self._console_watcher = ReportViewWatcher(self.console)
        except Exception:  # Report view not available; get_console stays empty
            self._console_watcher = None

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
        self.request.emit((op, {**args, "_timeout": timeout}, fut))
        try:
            return fut.result(timeout=timeout)
        except FutureTimeout:
            fut.cancel()  # skip it if it hasn't started yet
            return {"ok": False, "error": protocol.error(
                protocol.TIMEOUT, f"no result after {timeout:.0f} s; a running C++ operation may still finish")}

    @staticmethod
    def _target_doc(args: dict[str, Any]) -> Any:
        import FreeCAD as App

        name = args.get("doc")
        if name and name in App.listDocuments():
            return App.getDocument(name)
        return App.ActiveDocument

    def _safety_snapshot(self, doc: Any) -> None:
        """Keep a copy of unsaved work before Claude changes it: a crash in FreeCAD's core (seen with
        raw mesh data) takes the whole program down. At most every safety.snapshot_minutes."""
        try:
            import FreeCADGui as Gui

            from . import settings
            from .action_ops import create_snapshot

            if doc is None or not doc.Objects:
                return
            gdoc = Gui.getDocument(doc.Name)
            if not getattr(gdoc, "Modified", False):
                return
            minutes = settings.load(paths.bridge_dir())["safety.snapshot_minutes"]
            if crashguard.snapshot_due(self._last_snapshot.get(doc.Name), time.time(), minutes):
                create_snapshot(doc)
                self._last_snapshot[doc.Name] = time.time()
        except Exception:
            pass

    @QtCore.Slot(object)
    def _run(self, item: tuple) -> None:
        op, args, fut = item
        self._busy.set()
        self.journal.claude_active = op not in READ_ONLY_OPS
        started = time.monotonic()
        resp: dict[str, Any] = {}
        guarded = op not in READ_ONLY_OPS
        try:
            if not fut.set_running_or_notify_cancel():
                guarded = False
                return
            self.current_op = op
            if guarded:
                doc = self._target_doc(args)
                if op in SAFETY_OPS:
                    self._safety_snapshot(doc)
                detail = args.get("code") or args.get("command") or args.get("path") or ""
                crashguard.mark_start(paths.bridge_dir(), os.getpid(), op, doc.Name if doc else None, str(detail))
            try:
                resp = {"ok": True, "result": self.ops.OPS[op](args, self)}
            except OpError as e:
                err = protocol.error(e.code, e.message, e.traceback)
                err.update(e.extra)
                resp = {"ok": False, "error": err}
            except Exception as e:  # bug in the bridge itself
                resp = {"ok": False, "error": protocol.error(
                    protocol.INTERNAL, f"{type(e).__name__}: {e}", traceback.format_exc())}
            fut.set_result(resp)
        finally:
            if guarded:
                crashguard.mark_done(paths.bridge_dir(), os.getpid())
            self.current_op = ""
            if resp and op != "status_gui":
                err = resp.get("error") or {}
                audit.record("call", op=op, args=audit.call_args(args), ok=resp["ok"],
                             ms=round((time.monotonic() - started) * 1000), error=err.get("code"),
                             message=err.get("message"))
            self.journal.claude_active = False
            self._busy.clear()
            self._slot.release()
