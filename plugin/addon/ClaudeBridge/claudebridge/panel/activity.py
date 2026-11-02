"""What Claude is doing right now, for the panel's live status line and its stall watchdog.

Fed with the StreamParser's events; time is passed in, so it is unit-tested without Qt.
"""

from __future__ import annotations

from typing import Any

from .stream import short_tool_name
from .usage import short

LONG_TOOLS = {"run_headless", "export", "import"}     # may legitimately run for minutes


def _clock(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60}:{s % 60:02d}"


class Activity:
    def __init__(self) -> None:
        self.started = 0.0
        self.last_event = 0.0
        self.phase = "idle"            # starting, thinking, writing, tool, approval, idle
        self.since = 0.0
        self.tool = ""
        self.tool_id: str | None = None
        self.tokens = 0                # estimate for the thinking block in progress
        self.done_tokens: dict[str, int] = {}   # real output tokens per API call of this turn
        self.thinking_since: float | None = None
        self.last_thought_seconds: int | None = None

    def start(self, now: float) -> None:
        self.started = self.last_event = self.since = now
        self.phase, self.tool, self.tool_id, self.tokens = "starting", "", None, 0
        self.done_tokens = {}

    def stop(self) -> None:
        self.phase = "idle"

    def alive(self, now: float) -> None:
        """Count as a sign of life (FreeCAD was busy computing and could not update the window)."""
        self.last_event = max(self.last_event, now)

    def _set(self, phase: str, now: float) -> None:
        if self.phase == "thinking" and phase != "thinking" and self.thinking_since is not None:
            self.last_thought_seconds = int(round(now - self.thinking_since))
            self.thinking_since = None
        if phase != self.phase:
            self.phase, self.since = phase, now

    def on_event(self, ev: dict[str, Any], now: float) -> None:
        kind = ev.get("kind")
        self.last_event = now
        if kind in ("thinking_start", "thinking_delta"):
            if self.phase != "thinking":
                self.thinking_since, self.tokens = now, 0
            self._set("thinking", now)
            self.tokens = max(self.tokens, int(ev.get("tokens") or 0))
        elif kind == "usage":
            msg = ev.get("message") or "?"
            out = int(ev.get("output") or 0)
            if out > self.done_tokens.get(msg, 0):
                self.done_tokens[msg] = out
                self.tokens = 0            # the estimate is now part of the real count
        elif kind in ("text_delta", "text"):
            self._set("writing", now)
        elif kind == "tool_use":
            self._set("tool", now)
            self.since = now
            self.tool, self.tool_id = short_tool_name(ev.get("name", "?")), ev.get("id")
        elif kind == "tool_result":
            self._set("starting", now)          # waiting for Claude's next step
            self.tool_id = None
        elif kind == "permission_request":
            self._set("approval", now)
        elif kind == "result":
            self._set("idle", now)

    def describe(self, now: float) -> str:
        total = _clock(now - self.started)
        if self.phase == "thinking":
            text = f"Thinking · {_clock(now - self.since)}"
            total = sum(self.done_tokens.values()) + self.tokens
            return text + (f" · ~{short(total)} tokens" if total else "")
        if self.phase == "tool":
            return f"Running {self.tool} ({int(now - self.since)} s) · {total}"
        if self.phase == "writing":
            return f"Writing the answer · {total}"
        if self.phase == "approval":
            return f"Waiting for your approval · {total}"
        if self.phase == "starting":
            return f"Starting the next step · {total}" if now - self.started > 2 else f"Starting · {total}"
        return ""

    def stall(self, now: float, limit: float, freecad_busy: bool = False) -> str | None:
        """A warning when nothing happened for `limit` seconds, or None (waiting for the user is fine)."""
        if self.phase in ("idle", "approval") or now - self.last_event < limit:
            return None
        quiet = _clock(now - self.last_event)
        if self.phase == "tool" and self.tool in LONG_TOOLS:
            return f"{self.tool} is still running ({quiet}); long jobs are normal here. Stop ends it."
        if self.phase == "tool" and freecad_busy:
            return (f"FreeCAD is still computing {self.tool} ({quiet} without news). Long geometry can take a "
                    "while; Stop interrupts Claude, the running FreeCAD operation finishes on its own.")
        if self.phase == "tool":
            return (f"{self.tool} has given no sign of life for {quiet}. It may be stuck: Stop, or create a "
                    "debug report.")
        return f"Claude has given no sign of life for {quiet}. It may be stuck: Stop, or create a debug report."
