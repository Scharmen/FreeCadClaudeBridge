"""What the chat panel's modules share: settings keys, paths, history files, HTML helpers."""

from __future__ import annotations

import hashlib
import html
import json
import os
import time
from typing import Any

import FreeCAD as App
import FreeCADGui as Gui

from ..bridge import paths
from . import usage
from .tips import button_tip

PARAMS = "User parameter:BaseApp/Preferences/Mod/ClaudeBridge"


OBJECT_NAME = "ClaudeChatPanel"


NO_DOC = "<no document>"


MODE_LABELS = {"default": "Ask before acting", "acceptEdits": "Accept edits", "plan": "Plan mode",
               "auto": "Auto", "bypassPermissions": "Bypass permissions"}


FALLBACK_MODELS = [{"value": "default", "displayName": "Default"}, {"value": "opus", "displayName": "Opus"},
                   {"value": "sonnet", "displayName": "Sonnet"}, {"value": "haiku", "displayName": "Haiku"}]


LOCAL_COMMANDS = [("clear", "Start a new conversation"), ("new", "Start a new conversation"),
                  ("model", "Switch the model, e.g. /model opus"), ("effort", "Set the effort, e.g. /effort high"),
                  ("mode", "Permission mode: default, acceptEdits, plan, auto"),
                  ("desktop", "Open this conversation in the Claude Desktop app"),
                  ("sub", "Open a sub chat for one part, e.g. /sub Roof (no name: the selected part)"),
                  ("usage", "Tokens of the last answer, context and plan limits"), ("help", "List the commands")]


def _params() -> Any:
    return App.ParamGet(PARAMS)


def _when(stamp: float) -> str:
    """Short time like the Desktop app's list: 14:05 today, Mon, or 03.11."""
    if not stamp:
        return ""
    now, t = time.localtime(), time.localtime(stamp)
    if t[:3] == now[:3]:
        return time.strftime("%H:%M", t)
    if time.time() - stamp < 6 * 86400:
        return time.strftime("%a %H:%M", t)
    return time.strftime("%d.%m.%Y", t)


def _info_path() -> str:
    """Claude Code's last initialize answer (commands, models, account), so the panel knows them early."""
    return os.path.join(paths.bridge_dir(), "claude_info.json")


def _load_info() -> dict[str, Any]:
    try:
        with open(_info_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _usage_path() -> str:
    return os.path.join(paths.bridge_dir(), "usage.json")


def _load_usage() -> usage.Usage:
    try:
        with open(_usage_path(), encoding="utf-8") as f:
            return usage.Usage(json.load(f))
    except (OSError, ValueError, TypeError):
        return usage.Usage()


def _history_path(key: str) -> str:
    return os.path.join(paths.bridge_dir(), "history", hashlib.sha1(key.encode()).hexdigest()[:16] + ".html")


def _load_history(key: str) -> str:
    try:
        with open(_history_path(key), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _save_history(key: str, content: str) -> None:
    try:
        os.makedirs(os.path.dirname(_history_path(key)), exist_ok=True)
        with open(_history_path(key), "w", encoding="utf-8") as f:
            f.write(content)
    except OSError:
        pass


def doc_key(doc: Any) -> str:
    """Which project a document is: its file, or for an unsaved one its Uid. The internal name is no
    good there: nearly every new document is called "Unnamed", so unrelated projects would mix."""
    if doc is None:
        return NO_DOC
    return doc.FileName or f"unsaved:{getattr(doc, 'Uid', '') or doc.Name}"


def _doc_key() -> str:
    return doc_key(App.ActiveDocument)


def context_line() -> str:
    doc = App.ActiveDocument
    if doc is None:
        return "[FreeCAD] No document is open."
    parts = []
    for s in Gui.Selection.getSelectionEx("", 0)[:10]:
        item = f"{s.Object.Label} ({s.ObjectName})"
        if s.SubElementNames:
            item += ": " + ", ".join(list(s.SubElementNames)[:10])
        parts.append(item)
    sel = "; ".join(parts) if parts else "nothing"
    from ..bridge import ops, projects

    where = f", file {doc.FileName}" if doc.FileName else ""
    files = projects.context(doc.FileName or "", doc.Label, ops.projects_root())
    return f"[FreeCAD] Active document: {doc.Label} ({doc.Name}){where}. Selection: {sel}. {files}."


BUTTON_COLORS = {"allow": "#2e7d32", "always": "#1565c0", "deny": "#a12a2a", "option": "#455a64",
                 "chosen": "#2e7d32"}


def _buttons(items: list[tuple[str, str, str]], tip: dict[str, str] | None = None) -> str:
    """Small clickable buttons inside the chat: (label, href, color key)."""
    cells = []
    for label, href, kind in items:
        text = (tip or {}).get(href) or button_tip(label, href)
        title = f" title='{html.escape(text, quote=True)}'" if text else ""
        cells.append(f"<td bgcolor='{BUTTON_COLORS.get(kind, kind)}'><a href='{href}'{title} "
                     f"style='color:#ffffff; text-decoration:none'><b>&nbsp;{html.escape(label)}&nbsp;</b></a></td>")
    return "<table cellspacing='4' cellpadding='3'><tr>" + "".join(cells) + "</tr></table>"


def _always_label(suggestions: list[dict[str, Any]], tool: str) -> str:
    """What "always" would allow, in words, from Claude Code's permission suggestions."""
    for s in suggestions:
        if s.get("type") == "addRules":
            rules = [f"{str(r.get('toolName')).strip(chr(39))}({str(r.get('ruleContent')).strip(chr(39))})"
                     if r.get("ruleContent") else str(r.get("toolName")).strip("'") for r in s.get("rules") or []]
            return "Always allow " + ", ".join(rules)
        if s.get("type") == "setMode":
            return {"acceptEdits": "Accept all edits this session", "bypassPermissions": "Allow everything"}.get(
                s.get("mode"), f"Switch to {s.get('mode')}")
        if s.get("type") == "addDirectories":
            return "Allow access to " + ", ".join(s.get("directories") or [])
    return f"Allow {tool} for this session"


def _tool_summary(inp: dict[str, Any]) -> str:
    if "code" in inp:
        first = next((ln for ln in str(inp["code"]).splitlines() if ln.strip()), "")
        return (first[:60] + "…") if len(first) > 60 else first
    parts = [f"{k}={v}" for k, v in inp.items() if v not in (None, "", [], {})]
    text = ", ".join(parts)
    return (text[:70] + "…") if len(text) > 70 else text
