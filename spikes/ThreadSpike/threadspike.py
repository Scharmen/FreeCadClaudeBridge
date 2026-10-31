"""Phase 0 spike: a listener thread accepts, execution happens in the GUI thread.

Standard library plus FreeCAD's PySide only. No authentication
(spike only); the socket has permissions 0600.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import threading
import time
import traceback
from concurrent.futures import Future

import FreeCAD as App
from PySide import QtCore

BRIDGE_DIR = os.path.expanduser("~/Library/Application Support/freecad-bridge")
SOCK_PATH = os.path.join(BRIDGE_DIR, "spike.sock")
MAX_FRAME = 64 * 1024 * 1024

_state: dict = {}


def _recv_exact(conn: socket.socket, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def recv_msg(conn: socket.socket) -> dict | None:
    head = _recv_exact(conn, 4)
    if head is None:
        return None
    (size,) = struct.unpack(">I", head)
    if size > MAX_FRAME:
        raise ValueError("frame too large")
    body = _recv_exact(conn, size)
    return None if body is None else json.loads(body.decode("utf-8"))


def send_msg(conn: socket.socket, msg: dict) -> None:
    body = json.dumps(msg).encode("utf-8")
    conn.sendall(struct.pack(">I", len(body)) + body)


class _Dispatcher(QtCore.QObject):
    """Lives in the GUI thread; the signal is delivered via QueuedConnection."""

    request = QtCore.Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.request.connect(self._run, QtCore.Qt.QueuedConnection)
        self.ns: dict = {}

    @QtCore.Slot(object)
    def _run(self, item: tuple) -> None:
        code, fut = item
        if fut.done():  # caller already gave up
            return
        import FreeCADGui as Gui

        self.ns.update(App=App, Gui=Gui, doc=App.ActiveDocument, _result=None)
        doc = App.ActiveDocument
        try:
            if doc:
                doc.openTransaction("Claude spike")
            exec(code, self.ns)
            if doc:
                doc.commitTransaction()
            fut.set_result(
                {
                    "ok": True,
                    "result": repr(self.ns.get("_result")),
                    "gui_thread": threading.current_thread() is threading.main_thread(),
                }
            )
        except Exception:
            if doc:
                doc.abortTransaction()
            fut.set_result({"ok": False, "error": traceback.format_exc()})


def _handle(conn: socket.socket) -> None:
    with conn:
        while True:
            try:
                msg = recv_msg(conn)
            except (OSError, ValueError):
                return
            if msg is None:
                return
            op = msg.get("op")
            if op == "ping":
                # answered without the GUI thread, even while it is blocked
                send_msg(conn, {"id": msg.get("id"), "ok": True, "result": "pong",
                                "thread": threading.current_thread().name})
                continue
            if op == "exec":
                fut: Future = Future()
                _state["dispatcher"].request.emit((msg["code"], fut))
                try:
                    res = fut.result(timeout=msg.get("timeout_s", 60))
                except Exception:
                    fut.cancel()
                    res = {"ok": False, "error": "TIMEOUT"}
                res["id"] = msg.get("id")
                send_msg(conn, res)
                continue
            send_msg(conn, {"id": msg.get("id"), "ok": False, "error": f"unknown op {op}"})


def _listen(srv: socket.socket) -> None:
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        threading.Thread(target=_handle, args=(conn,), name="spike-conn", daemon=True).start()


def start() -> None:
    if "server" in _state:
        return
    os.makedirs(BRIDGE_DIR, mode=0o700, exist_ok=True)
    if os.path.exists(SOCK_PATH):
        os.unlink(SOCK_PATH)
    _state["dispatcher"] = _Dispatcher()  # created in the GUI thread
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)
    try:
        srv.bind(SOCK_PATH)
    finally:
        os.umask(old)
    srv.listen(8)
    _state["server"] = srv
    threading.Thread(target=_listen, args=(srv,), name="spike-listener", daemon=True).start()
    App.Console.PrintMessage(f"ThreadSpike listening on {SOCK_PATH} ({time.strftime('%X')})\n")
