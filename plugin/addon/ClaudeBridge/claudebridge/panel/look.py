"""How the chat looks: your messages as bubbles, Claude's answers under an accent header, one symbol per
tool, changes as a small table, notes with symbols. Qt's rich text knows only a subset of HTML (tables,
bgcolor, font colours, no rounded corners), so everything here is built from that. No FreeCAD or Qt
imports: unit-tested.
"""

from __future__ import annotations

import html
from typing import Any

ACCENT = "#d97757"          # Claude's orange

TOOL_ICONS = {
    "run_python": "🐍", "run_headless": "🐍", "run_command": "▶️", "make_part": "⚙️", "sculpt": "🗿",
    "view": "👁", "inspect_views": "👁", "compare_to_picture": "🖼", "image_to_3d": "🖼", "export": "📦",
    "import": "📥", "document": "📄", "selection": "🎯", "get_tree": "🌳", "get_object": "🔎",
    "get_changes": "🔎", "get_console": "🧾", "api_help": "📚", "design_notes": "📝", "design_library": "📚",
    "record_feedback": "💬", "status": "🩺", "sub_chat": "🧩", "flex_test": "🧪",
    "Bash": "💻", "Read": "📖", "Edit": "✏️", "Write": "✏️", "Glob": "🗂", "Grep": "🔎", "WebSearch": "🌐",
    "WebFetch": "🌐", "Task": "🤖", "Agent": "🤖", "Skill": "📚",
}


def tool_icon(name: str) -> str:
    if name.startswith("check_"):
        return "🔍"
    return TOOL_ICONS.get(name, "🔧")


def theme(dark: bool = False) -> dict[str, str]:
    if dark:
        return {"bubble": "#2b3445", "muted": "#9aa0a6", "ok": "#5cc98a", "error": "#ff7b72", "error_bg": "#3a2323",
                "table_head": "#3a3f4b", "table_line": "#555b66", "zebra": "#262a31", "accent": ACCENT}
    return {"bubble": "#eaf1fb", "muted": "#6b6f76", "ok": "#1e8a4c", "error": "#b3261e", "error_bg": "#fdecea",
            "table_head": "#f1ece6", "table_line": "#d6d2cc", "zebra": "#faf8f5", "accent": ACCENT}


def user_bubble(text: str, pictures_html: str, t: dict[str, str]) -> str:
    body = html.escape(text).replace("\n", "<br>")
    return (f"<table width='100%' cellspacing='0' cellpadding='8' style='margin-top:12px'><tr>"
            f"<td bgcolor='{t['bubble']}'><b>🧑 You</b><br>{body}{pictures_html}</td></tr></table>")


def claude_header(t: dict[str, str]) -> str:
    return f"<p style='margin-top:10px'><b><font color='{t['accent']}'>✳ Claude</font></b></p>"


def tool_line(name: str, summary: str, t: dict[str, str]) -> str:
    detail = f" <font color='{t['muted']}'>{html.escape(summary)}</font>" if summary else ""
    return f"<div>{tool_icon(name)} <code>{html.escape(name)}</code>{detail}</div>"


def tool_done(ok: bool, seconds: float, error: str, t: dict[str, str]) -> str:
    """Appended to the tool's own line when its result comes."""
    if ok:
        return f" <font color='{t['ok']}'>✓ {seconds:.1f} s</font>"
    return f" <font color='{t['error']}'>✗ {seconds:.1f} s {html.escape(error)}</font>"


def changes_table(summary: dict[str, Any], t: dict[str, str]) -> str:
    rows = []
    for key, symbol, label in (("created", "➕", "created"), ("changed", "✏️", "changed"), ("deleted", "🗑", "deleted")):
        names = summary.get(key) or []
        if names:
            shown = ", ".join(html.escape(str(n)) for n in names[:4]) + (" …" if len(names) > 4 else "")
            rows.append(f"<tr><td>{symbol} <b>{len(names)}</b> {label}</td>"
                        f"<td><font color='{t['muted']}'>{shown}</font></td></tr>")
    if not rows:
        return ""
    return (f"<table cellspacing='0' cellpadding='4' border='1' style='border-color:{t['table_line']};"
            f"border-style:solid; margin-top:6px'>{''.join(rows)}</table>")


def footer(parts: list[str], t: dict[str, str]) -> str:
    return f"<div style='margin-top:4px'><font color='{t['muted']}'><small>{' · '.join(parts)}</small></font></div>"


def note(text: str, kind: str, t: dict[str, str]) -> str:
    body = html.escape(text).replace("\n", "<br>")
    if kind == "error":
        return (f"<table width='100%' cellspacing='0' cellpadding='6'><tr><td bgcolor='{t['error_bg']}'>"
                f"<font color='{t['error']}'>⚠️ {body}</font></td></tr></table>")
    return f"<div><font color='{t['muted']}'>ℹ️ <i>{body}</i></font></div>"
