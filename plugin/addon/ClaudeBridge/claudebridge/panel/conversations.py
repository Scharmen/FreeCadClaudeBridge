"""Conversations of the chat panel: several per document, backed by Claude Code sessions.

Every conversation is a normal Claude Code session (~/.claude/projects/<folder>/<id>.jsonl),
so it can be continued in a terminal (`claude --resume <id>`) or in the Claude Desktop app
(`claude --desktop --resume <id>`), and sessions started there show up in the panel.

The panel's own bookkeeping (which conversations belong to which document, titles, the
working folder each session lives in) is conversations.json in the bridge directory.
No FreeCAD imports: unit-tested without FreeCAD.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

FILE = "conversations.json"
OLD_FILE = "sessions.json"  # one session per document, before 1.4
SOURCES = {"sdk-cli": "FreeCAD panel", "cli": "terminal", "claude-desktop": "Claude Desktop",
           "claude-vscode": "VS Code", "sdk-py": "SDK", "sdk-ts": "SDK"}
CONTEXT_LINE = re.compile(r"^\[FreeCAD\][^\n]*\n*")
SESSION_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


# ---------- Claude Code session files

def projects_dir() -> str:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "projects")


def encode_project(cwd: str) -> str:
    """Claude Code's folder name for a working directory: every non-alphanumeric character becomes '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def session_path(cwd: str, sid: str) -> str | None:
    if not SESSION_ID.match(sid or ""):
        return None
    return os.path.join(projects_dir(), encode_project(cwd), f"{sid}.jsonl")


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


def _prompt(content: Any) -> str:
    """The user's words without the panel's [FreeCAD] context line."""
    return CONTEXT_LINE.sub("", _text(content)).strip()


def _records(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def _is_prompt(rec: dict[str, Any]) -> bool:
    """A real user message (not a tool result or a sidechain)."""
    if rec.get("type") != "user" or rec.get("isSidechain"):
        return False
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, list) and any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
        return False
    return bool(_prompt(content))


def session_info(path: str) -> dict[str, Any]:
    info: dict[str, Any] = {"id": os.path.basename(path)[: -len(".jsonl")], "title": "", "turns": 0, "source": "",
                            "cwd": "", "updated": 0.0}
    try:
        info["updated"] = os.path.getmtime(path)
    except OSError:
        pass
    for rec in _records(path):
        if not _is_prompt(rec):
            continue
        info["turns"] += 1
        if not info["title"]:
            info["title"] = _prompt(rec["message"]["content"]).splitlines()[0][:60]
            info["source"] = SOURCES.get(rec.get("entrypoint", ""), rec.get("entrypoint", "") or "")
            info["cwd"] = rec.get("cwd", "")
    return info


def transcript(path: str, max_chars: int = 4000) -> list[tuple[str, str]]:
    """(role, text) pairs for redrawing a conversation: user, assistant text, tool calls."""
    out: list[tuple[str, str]] = []
    for rec in _records(path):
        if rec.get("isSidechain"):
            continue
        msg = rec.get("message") or {}
        if _is_prompt(rec):
            out.append(("user", _prompt(msg.get("content"))[:max_chars]))
        elif rec.get("type") == "assistant":
            content = msg.get("content")
            text = _text(content).strip()
            if text:
                out.append(("assistant", text[:max_chars]))
            for c in content if isinstance(content, list) else []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    out.append(("tool", str(c.get("name", "")).split("__")[-1]))
    return out


def folder_sessions(cwd: str, exclude: set[str] | None = None, limit: int = 8) -> list[dict[str, Any]]:
    """Claude Code sessions in a working folder (terminal, Desktop app, panel), newest first."""
    folder = os.path.join(projects_dir(), encode_project(cwd))
    try:
        names = [n for n in os.listdir(folder) if n.endswith(".jsonl")]
    except OSError:
        return []
    paths = sorted((os.path.join(folder, n) for n in names), key=os.path.getmtime, reverse=True)
    out = []
    for p in paths:
        sid = os.path.basename(p)[: -len(".jsonl")]
        if exclude and sid in exclude:
            continue
        info = session_info(p)
        if info["turns"]:
            out.append(info)
        if len(out) >= limit:
            break
    return out


def delete_session_file(cwd: str, sid: str) -> bool:
    path = session_path(cwd, sid)
    if not path or not os.path.isfile(path):
        return False
    os.unlink(path)
    return True


# ---------- the panel's bookkeeping

class Store:
    """{doc_key: {"active": id or None, "items": [{"id", "title", "cwd", "created"}]}}, saved on every change."""

    def __init__(self, bridge_dir: Any) -> None:
        self.path = os.path.join(str(bridge_dir), FILE)
        self.data: dict[str, dict[str, Any]] = {}
        try:
            with open(self.path, encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self._migrate(os.path.join(str(bridge_dir), OLD_FILE))

    def _migrate(self, old: str) -> None:
        try:
            with open(old, encoding="utf-8") as f:
                mapping = json.load(f)
        except (OSError, ValueError):
            return
        for key, sid in mapping.items():
            if isinstance(sid, str):
                cwd = os.path.dirname(key) if not key.startswith("unsaved:") else os.path.expanduser("~")
                self.data[key] = {"active": sid, "items": [{"id": sid, "title": "Earlier conversation", "cwd": cwd,
                                                            "created": time.time()}]}
        self._save()

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def _doc(self, key: str) -> dict[str, Any]:
        return self.data.setdefault(key, {"active": None, "items": []})

    def items(self, key: str) -> list[dict[str, Any]]:
        return list(self._doc(key)["items"]) if key in self.data else []

    def find(self, key: str, sid: str) -> dict[str, Any] | None:
        return next((c for c in self.items(key) if c["id"] == sid), None)

    def active(self, key: str) -> dict[str, Any] | None:
        doc = self.data.get(key)
        return self.find(key, doc["active"]) if doc and doc.get("active") else None

    def linked_ids(self) -> set[str]:
        return {c["id"] for d in self.data.values() for c in d.get("items", [])}

    def add(self, key: str, sid: str, title: str, cwd: str) -> None:
        doc = self._doc(key)
        if not self.find(key, sid):
            doc["items"].append({"id": sid, "title": title or "Conversation", "cwd": cwd, "created": time.time()})
        doc["active"] = sid
        self._save()

    def replace(self, key: str, old: str, new: str) -> None:
        item = self.find(key, old)
        if item:
            item["id"] = new
            if self.data[key].get("active") == old:
                self.data[key]["active"] = new
            self._save()

    def set_active(self, key: str, sid: str | None) -> None:
        self._doc(key)["active"] = sid
        self._save()

    def start_new(self, key: str) -> None:
        self.set_active(key, None)

    def rename(self, key: str, sid: str, title: str) -> None:
        item = self.find(key, sid)
        if item and title.strip():
            item["title"] = title.strip()[:80]
            self._save()

    def ordered(self, key: str) -> list[dict[str, Any]]:
        """Pinned first, then the user's drag order, then newest first."""
        order = self._doc(key).get("order", []) if key in self.data else []
        rank = {sid: i for i, sid in enumerate(order)}
        items = self.items(key)
        return sorted(items, key=lambda c: (not c.get("pinned", False), rank.get(c["id"], len(rank)),
                                            -c.get("created", 0)))

    def set_order(self, key: str, ids: list[str]) -> None:
        self._doc(key)["order"] = [i for i in ids if self.find(key, i)]
        self._save()

    def pin(self, key: str, sid: str, pinned: bool) -> None:
        item = self.find(key, sid)
        if item:
            item["pinned"] = bool(pinned)
            self._save()

    def move(self, src: str, dst: str, sid: str) -> None:
        """Hand a conversation over to another document (it becomes that document's active one)."""
        item = self.find(src, sid)
        if not item or src == dst:
            return
        self.remove(src, sid)
        doc = self._doc(dst)
        doc["items"].append(item)
        doc["active"] = sid
        self._save()

    def remove(self, key: str, sid: str) -> None:
        doc = self._doc(key)
        doc["items"] = [c for c in doc["items"] if c["id"] != sid]
        doc["order"] = [i for i in doc.get("order", []) if i != sid]
        if doc.get("active") == sid:
            doc["active"] = doc["items"][-1]["id"] if doc["items"] else None
        self._save()
