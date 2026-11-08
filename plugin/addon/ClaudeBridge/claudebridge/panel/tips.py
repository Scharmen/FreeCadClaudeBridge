"""Hover texts for the chat panel's buttons and lists (no FreeCAD imports, so they are unit-tested)."""

from __future__ import annotations

MODE_TIPS = {
    "default": "Ask before acting: Claude asks before every change to the model or your files.",
    "acceptEdits": "Accept edits: file edits run without asking; other actions still ask.",
    "plan": "Plan mode: Claude only looks and plans; nothing changes until you accept its plan.",
    "auto": "Auto: Claude Code decides which actions are safe to run without asking.",
    "bypassPermissions": "Bypass permissions: every action runs without asking. Use with care.",
}

BUTTON_TIPS = {      # by label first, then by link prefix
    "Skip": "Skip this question; Claude goes on without your answer",
    "perm:allow": "Allow this action once",
    "perm:always": "Allow this and similar actions for the rest of this conversation",
    "perm:deny": "Refuse this action; Claude is told and tries something else",
    "perm:ans": "Answer with this option",
    "stop:": "Stop Claude now",
    "report:": "Write a debug report (secrets removed) you can attach to a bug report",
    "snapshots:": "Open the folder with the copies of unsaved documents the bridge keeps",
}


def button_tip(label: str, href: str) -> str:
    """The hover text of a button inside the chat, by its label first, then by its link."""
    return BUTTON_TIPS.get(label) or next((t for k, t in BUTTON_TIPS.items() if ":" in k and href.startswith(k)), "")


EFFORT_TIPS = {
    "low": "Low: little thinking. Fastest and cheapest; good for small changes and questions.",
    "medium": "Medium: balanced thinking for everyday modelling.",
    "high": "High: thinks longer. Better for complex designs, slower.",
    "xhigh": "Extra high: even longer thinking for hard designs and replicas.",
    "max": "Max: the longest thinking. Slowest and uses the most of your plan or budget.",
}


def effort_auto_item(last: str | None) -> tuple[str, str]:
    """The "auto" entry of the effort list: Claude Code picks the effort for each request; name the one it used."""
    tip = "Auto: Claude Code chooses the effort for each request"
    if last:
        return f"Effort: auto ({last})", f"{tip}. It last used {last} with this model."
    return "Effort: auto", f"{tip}. The level it used shows here after the first answer."
