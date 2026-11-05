"""Designs the user rated good, kept locally as examples for new requests.

One JSON file per design in <bridge dir>/designs: what was asked, the result, the user's words and the code
that built it. Never sent anywhere; `design_library` lists, searches and deletes them.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

FOLDER = "designs"
MAX_CODE_CHARS = 40_000
MAX_ENTRIES = 200
STOP = frozenset("a an and the of for to with in on at it is that this make model design create build me i my mm "
                 "please new one".split())


def _dir(bridge_dir: Any) -> Path:
    return Path(bridge_dir) / FOLDER


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9äöüß]+", text.lower()) if w not in STOP and len(w) > 1}


def record(bridge_dir: Any, request: str, result: str, comment: str, code: list[str]) -> str | None:
    """Store a good design; without any code there is nothing to learn from. Returns its id."""
    code = [c for c in code if c.strip()]
    if not code:
        return None
    folder = _dir(bridge_dir)
    folder.mkdir(parents=True, exist_ok=True)
    kept: list[str] = []
    room = MAX_CODE_CHARS
    for c in reversed(code):              # the last calls are the ones that made the final model
        if len(c) > room:
            break
        kept.insert(0, c)
        room -= len(c)
    stem = "-".join(sorted(words(request))[:4]) or "design"
    entry_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{stem}"[:80]
    path = folder / f"{entry_id}.json"
    path.write_text(json.dumps({"id": entry_id, "request": request, "result": result, "comment": comment,
                                "when": time.strftime("%Y-%m-%d"), "code": kept}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    os.chmod(path, 0o600)
    for old in sorted(folder.glob("*.json"))[:-MAX_ENTRIES]:
        old.unlink(missing_ok=True)
    return entry_id


def _load(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def entries(bridge_dir: Any) -> list[dict[str, Any]]:
    out = [_load(p) for p in sorted(_dir(bridge_dir).glob("*.json"), reverse=True)]
    return [e for e in out if e]


def brief(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: entry.get(k) for k in ("id", "when", "request", "result", "comment")}


def search(bridge_dir: Any, query: str, limit: int = 3) -> list[dict[str, Any]]:
    """The closest designs by shared words; none when nothing is related."""
    wanted = words(query)
    scored = []
    for e in entries(bridge_dir):
        have = words(f"{e.get('request', '')} {e.get('result', '')}")
        shared = len(wanted & have)
        if shared:
            scored.append((shared / (len(wanted | have) or 1), e))
    scored.sort(key=lambda s: s[0], reverse=True)
    return [e for _, e in scored[:limit]]


def show(bridge_dir: Any, entry_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"[\w.-]+", entry_id):
        return None
    return _load(_dir(bridge_dir) / f"{entry_id}.json")


def delete(bridge_dir: Any, entry_id: str) -> bool:
    if not re.fullmatch(r"[\w.-]+", entry_id):
        return False
    path = _dir(bridge_dir) / f"{entry_id}.json"
    if not path.exists():
        return False
    path.unlink()
    return True
