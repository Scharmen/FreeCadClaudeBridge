"""Wire protocol shared by the FreeCAD addon and the MCP server.

This file exists twice with identical content: in the addon
(`claudebridge/bridge/protocol.py`) and in the server
(`freecad_bridge/protocol.py`). CI checks that both copies match.
Standard library only; must run on Python 3.11 (FreeCAD bundle) and 3.12.

Framing: 4-byte big-endian length, then UTF-8 JSON.

Handshake (both sides prove knowledge of the shared token):
  server -> {"type": "hello", "proto", "nonce", "addon_version", "pid"}
  client -> {"type": "auth", "proto", "nonce", "mac"}   mac = HMAC(token, "client:" + server_nonce)
  server -> {"type": "welcome", "ok": true, "mac"}       mac = HMAC(token, "server:" + client_nonce)
         or {"type": "welcome", "ok": false, "error": {...}}
After that: one request {"id", "op", "args", "timeout_s"} per response
{"id", "ok", "result"} / {"id", "ok": false, "error": {"code", "message", "traceback"?}}.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import socket
import struct
from typing import Any

PROTOCOL_VERSION = "1.0.0"
MAX_FRAME = 64 * 1024 * 1024
NONCE_BYTES = 32
TOKEN_BYTES = 32

AUTH_FAILED = "AUTH_FAILED"
VERSION_MISMATCH = "VERSION_MISMATCH"
GUI_BUSY = "GUI_BUSY"
TIMEOUT = "TIMEOUT"
PY_EXCEPTION = "PY_EXCEPTION"
NO_DOCUMENT = "NO_DOCUMENT"
NOT_FOUND = "NOT_FOUND"
TASK_DIALOG_OPEN = "TASK_DIALOG_OPEN"
INVALID_ARGS = "INVALID_ARGS"
INTERNAL = "INTERNAL"


class ProtocolError(Exception):
    """A protocol-level failure with one of the error codes above."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _recv_exact(sock: socket.socket, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def send_msg(sock: socket.socket, msg: dict[str, Any]) -> None:
    body = json.dumps(msg, separators=(",", ":"), default=str).encode("utf-8")
    if len(body) > MAX_FRAME:
        raise ProtocolError(INTERNAL, f"message too large ({len(body)} bytes)")
    sock.sendall(struct.pack(">I", len(body)) + body)


def recv_msg(sock: socket.socket) -> dict[str, Any] | None:
    """Read one message; None on a clean EOF before the length prefix."""
    head = _recv_exact(sock, 4)
    if head is None:
        return None
    (size,) = struct.unpack(">I", head)
    if size > MAX_FRAME:
        raise ProtocolError(INTERNAL, f"frame too large ({size} bytes)")
    body = _recv_exact(sock, size)
    if body is None:
        raise ProtocolError(INTERNAL, "connection closed mid-frame")
    msg = json.loads(body.decode("utf-8"))
    if not isinstance(msg, dict):
        raise ProtocolError(INTERNAL, "message is not a JSON object")
    return msg


def new_token() -> bytes:
    return secrets.token_bytes(TOKEN_BYTES)


def new_nonce() -> str:
    return secrets.token_hex(NONCE_BYTES)


def mac(token: bytes, role: str, nonce: str) -> str:
    return hmac.new(token, f"{role}:{nonce}".encode(), hashlib.sha256).hexdigest()


def major(version: str) -> int:
    return int(version.split(".", 1)[0])


def compatible(a: str, b: str) -> bool:
    """Same major version means wire-compatible."""
    return major(a) == major(b)


def error(code: str, message: str, traceback: str | None = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if traceback:
        err["traceback"] = traceback
    return err


def server_handshake(sock: socket.socket, token: bytes, addon_version: str, pid: int) -> bool:
    """Run the server (addon) side of the handshake. Returns True if authenticated."""
    nonce = new_nonce()
    send_msg(sock, {"type": "hello", "proto": PROTOCOL_VERSION, "nonce": nonce,
                    "addon_version": addon_version, "pid": pid})
    msg = recv_msg(sock)
    if not msg or msg.get("type") != "auth":
        return False
    if not compatible(str(msg.get("proto", "0")), PROTOCOL_VERSION):
        send_msg(sock, {"type": "welcome", "ok": False, "error": error(
            VERSION_MISMATCH, f"client protocol {msg.get('proto')}, addon protocol {PROTOCOL_VERSION}")})
        return False
    if not hmac.compare_digest(str(msg.get("mac", "")), mac(token, "client", nonce)):
        send_msg(sock, {"type": "welcome", "ok": False, "error": error(AUTH_FAILED, "bad token")})
        return False
    send_msg(sock, {"type": "welcome", "ok": True, "mac": mac(token, "server", str(msg.get("nonce", "")))})
    return True


def client_handshake(sock: socket.socket, token: bytes) -> dict[str, Any]:
    """Run the client (MCP server) side. Returns the addon's hello message.

    Raises ProtocolError if the addon rejects us or fails to prove the token.
    """
    hello = recv_msg(sock)
    if not hello or hello.get("type") != "hello":
        raise ProtocolError(AUTH_FAILED, "no hello from addon")
    if not compatible(str(hello.get("proto", "0")), PROTOCOL_VERSION):
        raise ProtocolError(VERSION_MISMATCH,
                            f"addon protocol {hello.get('proto')}, server protocol {PROTOCOL_VERSION}")
    nonce = new_nonce()
    send_msg(sock, {"type": "auth", "proto": PROTOCOL_VERSION, "nonce": nonce,
                    "mac": mac(token, "client", str(hello["nonce"]))})
    welcome = recv_msg(sock)
    if not welcome or welcome.get("type") != "welcome":
        raise ProtocolError(AUTH_FAILED, "no welcome from addon")
    if not welcome.get("ok"):
        err = welcome.get("error") or {}
        raise ProtocolError(err.get("code", AUTH_FAILED), err.get("message", "rejected"))
    if not hmac.compare_digest(str(welcome.get("mac", "")), mac(token, "server", nonce)):
        raise ProtocolError(AUTH_FAILED, "addon failed to prove the token")
    return hello
