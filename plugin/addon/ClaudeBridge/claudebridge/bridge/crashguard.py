"""Notice when FreeCAD crashed during a Claude call, and keep a copy of unsaved work.

Before the executor runs a call it writes inflight-<pid>.json (operation, document, code); after
the call the file is removed. A file whose process is gone means FreeCAD died during that call:
the next start reports it once (and appends it to crashes.jsonl). Standard library only.
"""

from __future__ import annotations

import glob
import json
import os
import time
from collections.abc import Callable
from typing import Any

CRASH_LOG = "crashes.jsonl"


def _marker(bridge_dir: Any, pid: int) -> str:
    return os.path.join(str(bridge_dir), f"inflight-{pid}.json")


def mark_start(bridge_dir: Any, pid: int, op: str, doc: str | None, detail: str) -> None:
    try:
        os.makedirs(str(bridge_dir), mode=0o700, exist_ok=True)
        with open(_marker(bridge_dir, pid), "w", encoding="utf-8") as f:
            json.dump({"pid": pid, "op": op, "doc": doc, "detail": detail[:4000],
                       "started": time.strftime("%Y-%m-%d %H:%M:%S")}, f)
    except OSError:
        pass


def mark_done(bridge_dir: Any, pid: int) -> None:
    try:
        os.unlink(_marker(bridge_dir, pid))
    except OSError:
        pass


def collect_crashes(bridge_dir: Any, alive: Callable[[int], bool]) -> list[dict[str, Any]]:
    """Calls that were running when their FreeCAD died; each is returned once."""
    found = []
    for path in glob.glob(os.path.join(str(bridge_dir), "inflight-*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            info = None
        if info and alive(int(info.get("pid", 0))):
            continue
        try:
            os.unlink(path)
        except OSError:
            pass
        if not info:
            continue
        found.append(info)
        try:
            with open(os.path.join(str(bridge_dir), CRASH_LOG), "a", encoding="utf-8") as f:
                f.write(json.dumps({**info, "noticed": time.strftime("%Y-%m-%d %H:%M:%S")}) + "\n")
        except OSError:
            pass
    return found


def snapshot_due(last: float | None, now: float, minutes: float) -> bool:
    """Time for another safety copy of a modified document? minutes = 0 switches it off."""
    if minutes <= 0:
        return False
    return last is None or now - last >= minutes * 60
