"""Audit log: every bridge call with time, duration, result and the code it ran.

One JSON object per line in `bridge-log.jsonl` in the bridge directory (0600),
rotated to `bridge-log.1.jsonl` at 10 MB. The MCP server appends `run_headless`
runs to the same file. Logging never breaks a call.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from . import paths

MAX_BYTES = 10 * 1024 * 1024
MAX_FIELD = 20_000
_lock = threading.Lock()


def log_path() -> str:
    return os.path.join(paths.bridge_dir(), "bridge-log.jsonl")


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_FIELD:
        return value[:MAX_FIELD] + f"… [{len(value)} chars]"
    return value


def call_args(args: dict[str, Any]) -> dict[str, Any]:
    """Arguments worth logging: internal keys (leading underscore) dropped, long strings clipped."""
    return {k: _clip(v) for k, v in args.items() if not k.startswith("_")}


def record(event: str, **fields: Any) -> None:
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, "pid": os.getpid(), **fields}
    try:
        line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
        with _lock:
            paths.ensure_bridge_dir()
            path = log_path()
            try:
                if os.path.getsize(path) + len(line) > MAX_BYTES:
                    os.replace(path, path[: -len(".jsonl")] + ".1.jsonl")
            except OSError:
                pass
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass
