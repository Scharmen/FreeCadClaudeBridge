"""Platform paths. Mirrors the bridge directory logic of the addon's `paths.py`."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from . import protocol


def bridge_dir() -> Path:
    override = os.environ.get("FREECAD_BRIDGE_DIR")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/freecad-bridge"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "freecad-bridge"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "freecad-bridge"


def token_path() -> Path:
    return bridge_dir() / "token"


def config_path() -> Path:
    return bridge_dir() / "config.json"


def instances_dir() -> Path:
    return bridge_dir() / "instances"


def read_token() -> bytes | None:
    try:
        data = token_path().read_bytes()
    except FileNotFoundError:
        return None
    return data if len(data) == protocol.TOKEN_BYTES else None


def ensure_token() -> bytes:
    token = read_token()
    if token:
        return token
    return write_token(protocol.new_token())


def write_token(token: bytes) -> bytes:
    """Atomic, so a handshake running in FreeCAD never reads half a token."""
    bridge_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = token_path().with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(token)
    os.replace(tmp, token_path())
    return token


AUDIT_MAX_BYTES = 10 * 1024 * 1024


def audit_path() -> Path:
    return bridge_dir() / "bridge-log.jsonl"


def audit_record(event: str, **fields: object) -> None:
    """Append to the audit log the addon writes (mirrors its bridge/audit.py); never raises."""
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, "pid": os.getpid(), **fields}
    try:
        line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
        path = audit_path()
        if path.exists() and path.stat().st_size + len(line) > AUDIT_MAX_BYTES:
            os.replace(path, path.with_name("bridge-log.1.jsonl"))
        bridge_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def load_config() -> dict:
    try:
        return json.loads(config_path().read_text())
    except (OSError, ValueError):
        return {}


def update_config(**values: object) -> dict:
    cfg = load_config()
    cfg.update({k: v for k, v in values.items() if v is not None})
    bridge_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = config_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(config_path())
    return cfg


def clean_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for freecadcmd: no Python or Claude Code variables leaking in."""
    env = dict(os.environ if environ is None else environ)
    for key in list(env):
        if key.startswith(("PYTHON", "CLAUDE_CODE_", "VIRTUAL_ENV", "UV_")) or key == "CLAUDECODE":
            env.pop(key)
    return env
