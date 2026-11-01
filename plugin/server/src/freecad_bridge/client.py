"""Find running FreeCAD instances and talk to the ClaudeBridge addon."""

from __future__ import annotations

import itertools
import json
import os
import socket
import sys
from dataclasses import dataclass, field
from typing import Any

from . import __version__, platforms, protocol

NOT_RUNNING = "NOT_RUNNING"
_ids = itertools.count(1)

# What to do about errors that come from setup or the connection rather than from FreeCAD code.
FIXES = {
    NOT_RUNNING: "Start FreeCAD. If it is running and the Claude panel is missing, run /freecad:setup and restart "
                 "FreeCAD.",
    protocol.AUTH_FAILED: "Claude Code and FreeCAD use different tokens: run /freecad:setup, then restart FreeCAD.",
    protocol.VERSION_MISMATCH: "Plugin and addon are incompatible: run `claude plugin update "
                               "freecad@freecad-claude`, then /freecad:setup, and restart FreeCAD.",
    protocol.GUI_BUSY: "FreeCAD is still working on an earlier call; wait and retry, and use run_headless for heavy "
                       "operations.",
    protocol.TIMEOUT: "The call ran too long. Split the work, raise timeout_s, or move heavy geometry to "
                      "run_headless.",
}


def fix_for(code: str) -> str | None:
    return FIXES.get(code)


class BridgeError(Exception):
    def __init__(self, code: str, message: str, traceback: str | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.traceback = traceback


@dataclass
class Instance:
    pid: int
    transport: str
    address: str
    info: dict[str, Any] = field(default_factory=dict)

    @property
    def started(self) -> float:
        return float(self.info.get("started", 0))


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pid_alive_windows(pid: int) -> bool:
    """os.kill(pid, 0) on Windows sends CTRL_C_EVENT; query the process handle instead."""
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    PROCESS_QUERY_LIMITED_INFORMATION, STILL_ACTIVE = 0x1000, 259  # noqa: N806
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def list_instances(cleanup: bool = True) -> list[Instance]:
    """Live instances, most recently started first. Orphaned files are removed."""
    out = []
    d = platforms.instances_dir()
    if not d.is_dir():
        return out
    for path in d.glob("*.json"):
        try:
            info = json.loads(path.read_text())
            inst = Instance(int(info["pid"]), info["transport"], info["address"], info)
        except (OSError, ValueError, KeyError):
            continue
        if not pid_alive(inst.pid):
            if cleanup:
                path.unlink(missing_ok=True)
            continue
        out.append(inst)
    return sorted(out, key=lambda i: i.started, reverse=True)


def pick_instance(pid: int | None = None) -> Instance:
    instances = list_instances()
    if pid is not None:
        for inst in instances:
            if inst.pid == pid:
                return inst
        raise BridgeError(NOT_RUNNING, f"no FreeCAD instance with pid {pid}")
    if not instances:
        raise BridgeError(NOT_RUNNING, "FreeCAD with the ClaudeBridge addon is not running.")
    return instances[0]


def connect(inst: Instance, timeout: float) -> socket.socket:
    if inst.transport == "unix":
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        target: Any = inst.address
    else:
        host, port = inst.address.rsplit(":", 1)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        target = (host, int(port))
    sock.settimeout(timeout)
    try:
        sock.connect(target)
    except OSError as e:
        sock.close()
        hint = " Security software may be blocking 127.0.0.1." if inst.transport == "tcp" else ""
        raise BridgeError(NOT_RUNNING, f"cannot connect to FreeCAD (pid {inst.pid}): {e}.{hint}") from e
    return sock


@dataclass
class CallResult:
    result: Any
    instance: Instance
    hello: dict[str, Any]

    @property
    def warnings(self) -> list[str]:
        addon = self.hello.get("addon_version")
        if not addon or addon == __version__:
            return []
        from .installer import version_tuple  # installer imports this module

        if version_tuple(addon) < version_tuple(__version__):
            return [f"FreeCAD still runs ClaudeBridge {addon}; restart FreeCAD to load {__version__}."]
        return [f"FreeCAD runs ClaudeBridge {addon}, newer than this plugin ({__version__}): "
                "run `claude plugin update freecad@freecad-claude`."]


def call(op: str, args: dict[str, Any] | None = None, timeout_s: float = 60,
         instance: int | None = None) -> CallResult:
    inst = pick_instance(instance)
    token = platforms.read_token()
    if token is None:
        raise BridgeError(protocol.AUTH_FAILED, "no token file")
    sock = connect(inst, timeout=min(10.0, timeout_s + 5))
    try:
        try:
            hello = protocol.client_handshake(sock, token)
        except protocol.ProtocolError as e:
            raise BridgeError(e.code, e.message) from e
        sock.settimeout(timeout_s + 10)
        req_id = next(_ids)
        protocol.send_msg(sock, {"id": req_id, "op": op, "args": args or {}, "timeout_s": timeout_s})
        resp = protocol.recv_msg(sock)
    except TimeoutError as e:
        raise BridgeError(protocol.TIMEOUT, f"no answer from FreeCAD within {timeout_s + 10:.0f} s") from e
    except protocol.ProtocolError as e:
        raise BridgeError(e.code, e.message) from e
    finally:
        sock.close()
    if resp is None:
        raise BridgeError(protocol.INTERNAL, "FreeCAD closed the connection")
    if not resp.get("ok"):
        err = resp.get("error") or {}
        raise BridgeError(err.get("code", protocol.INTERNAL), err.get("message", "unknown error"),
                          err.get("traceback"))
    return CallResult(resp.get("result"), inst, hello)
