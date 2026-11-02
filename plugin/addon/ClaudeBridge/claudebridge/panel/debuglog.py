"""The chat panel's debug log: what was sent, every event from Claude Code (streamed text only counted),
control actions, stderr and crashes, one JSON object per line.

logs/panel-YYYYMMDD.jsonl in the bridge directory (0600), kept for 7 days, long fields clipped.
Used by debug reports; never sent anywhere.
"""

from __future__ import annotations

import glob
import json
import os
import time
from typing import Any

KEEP_DAYS = 7
MAX_FIELD = 4000
MAX_FILE = 20 * 1024 * 1024


def folder() -> str:
    from ..bridge import paths

    return os.path.join(paths.bridge_dir(), "logs")


def path_today() -> str:
    return os.path.join(folder(), time.strftime("panel-%Y%m%d.jsonl"))


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_FIELD:
        return value[:MAX_FIELD] + f"… [{len(value)} chars]"
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value[:50]]
    return value


def log(entry: str, /, **fields: Any) -> None:
    """One line: `entry` says what happened; fields may use any name (events carry their own "kind")."""
    try:
        os.makedirs(folder(), mode=0o700, exist_ok=True)
        path = path_today()
        if os.path.exists(path) and os.path.getsize(path) > MAX_FILE:
            return
        line = json.dumps({"ts": time.strftime("%H:%M:%S"), "t": round(time.time(), 2), "log": entry,
                           **_clip(fields)}, ensure_ascii=False, default=str)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def prune() -> None:
    cutoff = time.time() - KEEP_DAYS * 86400
    for p in glob.glob(os.path.join(folder(), "panel-*.jsonl")):
        try:
            if os.path.getmtime(p) < cutoff:
                os.unlink(p)
        except OSError:
            pass
