"""Listener thread: accepts local connections, authenticates, forwards requests.

`ping` and `status` are answered here without the GUI thread; everything else
goes to the Executor, which runs it in the GUI thread.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from . import paths, protocol


class BridgeServer:
    def __init__(self, executor: Any, addon_version: str, instance_info: dict[str, Any],
                 log: Callable[[str], None], transport: str = "auto") -> None:
        self.executor = executor
        self.addon_version = addon_version
        self.instance_info = instance_info
        self.log = log
        self.transport = transport
        self.pid = os.getpid()
        self.sock: socket.socket | None = None
        self.address = ""
        self._stop = threading.Event()

    def start(self) -> None:
        paths.ensure_bridge_dir()
        paths.load_or_create_token()
        use_unix = self.transport == "unix" or (self.transport == "auto" and hasattr(socket, "AF_UNIX")
                                                 and sys.platform != "win32")
        if use_unix:
            path = paths.socket_path(self.pid)
            if os.path.exists(path):
                os.unlink(path)
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            old = os.umask(0o177)
            try:
                sock.bind(path)
            finally:
                os.umask(old)
            kind, self.address = "unix", path
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            kind, self.address = "tcp", f"127.0.0.1:{sock.getsockname()[1]}"
        sock.listen(16)
        self.sock = sock
        paths.write_instance(self.pid, {
            **self.instance_info, "pid": self.pid, "transport": kind, "address": self.address,
            "proto": protocol.PROTOCOL_VERSION, "addon_version": self.addon_version, "started": time.time(),
        })
        threading.Thread(target=self._accept_loop, name="claudebridge-listener", daemon=True).start()
        self.log(f"ClaudeBridge {self.addon_version} listening ({kind}: {self.address})")

    def stop(self) -> None:
        self._stop.set()
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        paths.remove_instance(self.pid)

    def _accept_loop(self) -> None:
        while not self._stop.is_set() and self.sock:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), name="claudebridge-conn", daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(10)
            try:
                token = paths.load_or_create_token()  # re-read so token rotation needs no restart
                if not protocol.server_handshake(conn, token, self.addon_version, self.pid):
                    self.log("ClaudeBridge: rejected a connection (handshake failed)")
                    return
                conn.settimeout(None)
                while True:
                    msg = protocol.recv_msg(conn)
                    if msg is None:
                        return
                    protocol.send_msg(conn, self._dispatch(msg))
            except (OSError, ValueError, protocol.ProtocolError):
                return

    def _dispatch(self, msg: dict[str, Any]) -> dict[str, Any]:
        req_id, op = msg.get("id"), msg.get("op")
        args = msg.get("args") or {}
        timeout = float(msg.get("timeout_s") or 60)
        if op == "ping":
            return {"id": req_id, "ok": True, "result": {"pong": True, "busy": self.executor.busy}}
        if op == "status":
            return {"id": req_id, "ok": True, "result": self._status()}
        resp = self.executor.submit(op, args, timeout)
        resp["id"] = req_id
        return resp

    def _status(self) -> dict[str, Any]:
        base = {"connected": True, "pid": self.pid, "addon_version": self.addon_version,
                "protocol": protocol.PROTOCOL_VERSION, "freecad_version": self.instance_info.get("freecad_version"),
                "busy": self.executor.busy}
        if self.executor.busy:
            base["note"] = "FreeCAD's GUI thread is busy with an earlier call; details unavailable."
            return base
        resp = self.executor.submit("status_gui", {}, 3)
        if resp.get("ok"):
            base.update(resp["result"])
        else:
            base["busy"] = True
        return base
