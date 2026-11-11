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
    "sub:": "Open a sub chat for this part",
    "open:": "Open this file with its app",
    "restorecopy:": "Open this safety copy as a separate, unsaved document",
    "reveal:": "Show this file in its folder",
    "update:": "Update the Claude Code plugin and the FreeCAD addon to the newest version",
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


AUTO_EFFORT_DEFAULTS = {"fable": "high", "opus": "medium", "sonnet": "medium", "haiku": "medium"}   # Claude Code 2.1


def auto_effort_default(model: str) -> str | None:
    """Claude Code's default effort for a model (id or alias), when no level is chosen."""
    return next((lv for name, lv in AUTO_EFFORT_DEFAULTS.items() if name in (model or "").lower()), None)


def effort_auto_item(last: str | None, model: str = "") -> tuple[str, str]:
    """The "auto" entry of the effort list: no level chosen, so Claude Code uses its default for the model.
    Name that level: the one it used in the last answer, else its known default."""
    level = last or auto_effort_default(model)
    meaning = ("Auto: you leave the effort to Claude Code, which uses its default for the chosen model. "
               "Pick a level below to set it yourself.")
    if not level:
        return "Effort: auto (Claude Code default)", meaning + " The level shows here after the first answer."
    source = "it used in the last answer" if last else "Claude Code's default for this model"
    return f"Effort: auto → {level}", f"{meaning}\nNow: {level} ({source}). {EFFORT_TIPS.get(level, '')}"


def part_focus(part: dict[str, str] | None) -> str:
    """What a sub chat tells Claude with every message: the one part it works on."""
    if not part:
        return ""
    return (f"Sub chat for the part {part.get('label')} ({part.get('name')}): change only this part and what it "
            "contains; the rest of the model belongs to the main chat. Keep the part's position and its fit "
            "with the neighbouring parts.")


def compact_command(part: dict[str, str] | None) -> str:
    """The /compact request the panel sends for a long chat. The model itself lives in the FreeCAD document
    and its design notes, so the summary keeps what was decided and drops how it was computed."""
    keep = ("Keep: the user's wishes and preferences, every part and object name with its dimensions, positions, "
            "fits and clearances, colours, decisions with their reasons, file paths, what was checked and with what "
            "result, and open problems. Drop: code, tool output, pictures and reasoning steps.")
    if part:
        keep += (f" This is a sub chat for the part {part.get('label')} ({part.get('name')}): keep what concerns "
                 "this part and how it fits its neighbours; the rest of the model in a few lines.")
    return "/compact " + keep
