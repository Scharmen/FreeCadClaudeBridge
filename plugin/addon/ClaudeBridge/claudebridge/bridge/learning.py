"""Learning what the user wants: feedback log and generalized lessons, kept locally.

Identical copy in addon and server (CI checks). In the bridge directory:
- feedback.jsonl: every rating (good / partly / bad) with the user's words, request and result
- preferences.md: one generalized lesson per line ("- ..."); the user may edit or delete lines

The lessons are given to Claude at the start of every session (SessionStart hook, chat
panel system prompt, get_preferences tool). Nothing leaves the computer except as part of
the prompt to Claude. Standard library only.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

FEEDBACK = "feedback.jsonl"
PREFERENCES = "preferences.md"
RATINGS = ("good", "partly", "bad")
MAX_LESSONS = 60
MAX_FIELD = 2000
HEADER = ("# What Claude learned about how you model in FreeCAD\n\n"
          "One lesson per line, starting with \"- \". Edit or delete lines freely; "
          "they are read at the start of every session.\n\n")


def _p(bridge_dir: Any, name: str) -> str:
    return os.path.join(str(bridge_dir), name)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


def lessons(bridge_dir: Any) -> list[str]:
    try:
        with open(_p(bridge_dir, PREFERENCES), encoding="utf-8") as f:
            return [line[2:].strip() for line in f if line.startswith("- ") and line[2:].strip()]
    except OSError:
        return []


def _write_lessons(bridge_dir: Any, items: list[str]) -> None:
    os.makedirs(str(bridge_dir), mode=0o700, exist_ok=True)
    tmp = _p(bridge_dir, PREFERENCES) + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(HEADER + "".join(f"- {item}\n" for item in items))
    os.replace(tmp, _p(bridge_dir, PREFERENCES))


def add_lesson(bridge_dir: Any, lesson: str) -> bool:
    """Append a generalized lesson; duplicates (ignoring case and spacing) are skipped. Oldest go first."""
    text = _norm(lesson)[:300]
    if not text:
        return False
    items = lessons(bridge_dir)
    if text.casefold() in (i.casefold() for i in items):
        return False
    _write_lessons(bridge_dir, (items + [text])[-MAX_LESSONS:])
    return True


def record_feedback(bridge_dir: Any, rating: str, comment: str = "", request: str = "", result: str = "",
                    doc: str = "", source: str = "", lesson: str = "") -> dict[str, Any]:
    if rating not in RATINGS:
        raise ValueError(f"rating must be one of {', '.join(RATINGS)}")
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "rating": rating, "comment": _norm(comment)[:MAX_FIELD],
             "request": _norm(request)[:MAX_FIELD], "result": _norm(result)[:MAX_FIELD], "doc": doc,
             "source": source, "lesson": _norm(lesson)[:300]}
    os.makedirs(str(bridge_dir), mode=0o700, exist_ok=True)
    fd = os.open(_p(bridge_dir, FEEDBACK), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    entry["lesson_added"] = add_lesson(bridge_dir, lesson) if lesson else False
    return entry


def recent_feedback(bridge_dir: Any, n: int = 5) -> list[dict[str, Any]]:
    try:
        with open(_p(bridge_dir, FEEDBACK), encoding="utf-8") as f:
            lines = f.readlines()[-n:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def context_text(bridge_dir: Any, max_lessons: int = 40) -> str:
    """Text for Claude at session start; empty if nothing was learned yet."""
    items = lessons(bridge_dir)[-max_lessons:]
    if not items:
        return ""
    return ("Learned preferences of this FreeCAD user (from their earlier feedback; follow them unless the "
            "request says otherwise):\n" + "\n".join(f"- {i}" for i in items))


def forget(bridge_dir: Any) -> None:
    for name in (FEEDBACK, PREFERENCES):
        try:
            os.unlink(_p(bridge_dir, name))
        except OSError:
            pass


def ensure_file(bridge_dir: Any) -> str:
    """Create preferences.md with its header if missing (for opening it in an editor)."""
    if not os.path.exists(_p(bridge_dir, PREFERENCES)):
        _write_lessons(bridge_dir, [])
    return _p(bridge_dir, PREFERENCES)
