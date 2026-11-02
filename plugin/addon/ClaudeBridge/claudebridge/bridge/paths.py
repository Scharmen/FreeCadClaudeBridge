"""Bridge directory layout, independent of the FreeCAD version.

The server package has a mirror of the directory logic in
`freecad_bridge/platforms.py`; keep both in sync.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

from . import protocol


def bridge_dir() -> str:
    override = os.environ.get("FREECAD_BRIDGE_DIR")
    if override:
        return override
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/freecad-bridge")
    if sys.platform == "win32":
        return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "freecad-bridge")
    state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(state, "freecad-bridge")


def ensure_bridge_dir() -> str:
    d = bridge_dir()
    os.makedirs(os.path.join(d, "instances"), mode=0o700, exist_ok=True)
    return d


def token_path() -> str:
    return os.path.join(bridge_dir(), "token")


def config_path() -> str:
    """Written by `freecad-bridge install` and the MCP server: claude path, plugin root."""
    return os.path.join(bridge_dir(), "config.json")


def load_or_create_token() -> bytes:
    path = token_path()
    try:
        with open(path, "rb") as f:
            data = f.read()
        if len(data) == protocol.TOKEN_BYTES:
            return data
    except FileNotFoundError:
        pass
    return write_token(protocol.new_token())


def write_token(data: bytes) -> bytes:
    """Write a token atomically, so a concurrent handshake never reads half a token."""
    ensure_bridge_dir()
    tmp = token_path() + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, token_path())
    return data


def socket_path(pid: int) -> str:
    """Unix socket path; falls back to $TMPDIR when the path gets too long (~104 bytes on macOS)."""
    path = os.path.join(bridge_dir(), f"bridge-{pid}.sock")
    if len(path.encode()) > 100:
        path = os.path.join(tempfile.gettempdir(), f"freecad-bridge-{os.getuid()}-{pid}.sock")
    return path


def remove_stale_sockets() -> None:
    """Sockets left behind by FreeCAD processes that ended without cleanup (crash, forced quit)."""
    import glob
    import re

    for path in glob.glob(os.path.join(bridge_dir(), "bridge-*.sock")):
        m = re.search(r"bridge-(\d+)\.sock$", path)
        if not m or int(m.group(1)) == os.getpid():
            continue
        try:
            os.kill(int(m.group(1)), 0)
        except ProcessLookupError:
            for p in (path, instance_path(int(m.group(1)))):
                try:
                    os.unlink(p)
                except OSError:
                    pass
        except OSError:
            pass


def instance_path(pid: int) -> str:
    return os.path.join(bridge_dir(), "instances", f"{pid}.json")


def write_instance(pid: int, info: dict) -> None:
    ensure_bridge_dir()
    tmp = instance_path(pid) + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2)
    os.replace(tmp, instance_path(pid))


def remove_instance(pid: int) -> None:
    for p in (instance_path(pid), socket_path(pid)):
        try:
            os.unlink(p)
        except OSError:
            pass


def load_config() -> dict:
    try:
        with open(config_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}
