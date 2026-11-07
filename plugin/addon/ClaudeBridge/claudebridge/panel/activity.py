"""What Claude is doing right now, for the panel's live status line and its stall watchdog.

Fed with the StreamParser's events; time is passed in, so it is unit-tested without Qt.
"""

from __future__ import annotations

from typing import Any

from .stream import short_tool_name
from .usage import short

LONG_TOOLS = {"run_headless", "export", "import"}     # may legitimately run for minutes
RATE_WINDOW = 10.0       # seconds of token samples behind the "+N/s" in the status line
QUIET_SHOWN = 15.0       # from this many seconds without new tokens the status line says so
LONG_THINKING_SECONDS = 300.0   # after this much thinking the chat says once that Claude is still at it


def _clock(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60}:{s % 60:02d}"


class Activity:
    def __init__(self) -> None:
        self.started = 0.0
        self.last_event = 0.0
        self.phase = "idle"            # starting, thinking, writing, tool, approval, retrying, idle
        self.since = 0.0
        self.tool = ""
        self.tool_id: str | None = None
        self.tokens = 0                # estimate for the thinking block in progress (sum of its steps)
        self.retry: dict[str, Any] = {}
        self.retry_at = 0.0
        self.done_tokens: dict[str, int] = {}   # real output tokens per API call of this turn
        self.thinking_since: float | None = None
        self.last_thought_seconds: int | None = None
        self.progress_at = 0.0         # last real progress: a new token, text, a tool step (not a repeated count)
        self.samples: list[tuple[float, int]] = []   # (time, tokens so far) while thinking, for the rate
        self.long_noted = False

    def start(self, now: float) -> None:
        self.started = self.last_event = self.since = self.progress_at = now
        self.samples, self.long_noted = [], False
        self.phase, self.tool, self.tool_id, self.tokens = "starting", "", None, 0
        self.done_tokens = {}

    def stop(self) -> None:
        self.phase = "idle"

    def alive(self, now: float) -> None:
        """Count as a sign of life (FreeCAD was busy computing and could not update the window)."""
        self.last_event = max(self.last_event, now)
        self.progress_at = max(self.progress_at, now)

    def _set(self, phase: str, now: float) -> None:
        if self.phase == "thinking" and phase != "thinking" and self.thinking_since is not None:
            self.last_thought_seconds = int(round(now - self.thinking_since))
            self.thinking_since = None
        if phase != self.phase:
            self.phase, self.since = phase, now

    def thought_tokens(self) -> int:
        return sum(self.done_tokens.values()) + self.tokens

    def on_event(self, ev: dict[str, Any], now: float) -> bool:
        """Take one event; True when it is real progress (a thinking update that repeats the count is not)."""
        kind = ev.get("kind")
        self.last_event = now
        progress = True
        if kind in ("thinking_start", "thinking_delta"):
            if self.phase != "thinking":
                self.thinking_since, self.tokens = now, 0
                self.samples, self.long_noted = [], False
            self._set("thinking", now)
            if kind == "thinking_delta":
                step = int(ev.get("step") or 0)
                progress = step > 0 or bool(ev.get("text"))
                self.tokens += step
                if "step" in ev:
                    self.samples.append((now, self.thought_tokens()))
                    self.samples = [s for s in self.samples if s[0] >= now - RATE_WINDOW]
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
        elif kind == "api_retry":
            self._set("retrying", now)
            self.retry = ev
            self.retry_at = now + float(ev.get("delay_s") or 0)
        elif kind == "result":
            self._set("idle", now)
        if progress:
            self.progress_at = now
        if kind == "api_retry":           # Claude Code said when it tries again: quiet until then is expected
            self.last_event = self.progress_at = max(now, self.retry_at)
        return progress

    def rate(self) -> float:
        """Thinking tokens per second over the last few seconds of updates (0 when there are too few)."""
        if len(self.samples) < 2 or self.samples[-1][0] - self.samples[0][0] < 2:
            return 0.0
        (t0, n0), (t1, n1) = self.samples[0], self.samples[-1]
        return max(0.0, (n1 - n0) / (t1 - t0))

    def describe(self, now: float) -> str:
        total = _clock(now - self.started)
        if self.phase == "thinking":
            text = f"Thinking · {_clock(now - self.since)}"
            count = self.thought_tokens()
            if count:
                text += f" · ~{short(count)} tokens"
            quiet = now - self.progress_at
            if quiet >= QUIET_SHOWN:
                return text + f" · no new tokens for {_clock(quiet)}"
            rate = self.rate()
            return text + (f" · +{rate:.0f}/s" if rate >= 1 else "")
        if self.phase == "tool":
            return f"Running {self.tool} ({int(now - self.since)} s) · {total}"
        if self.phase == "writing":
            return f"Writing the answer · {total}"
        if self.phase == "approval":
            return f"Waiting for your approval · {total}"
        if self.phase == "retrying":
            r = self.retry
            what = f"API error {r['status']}" if r.get("status") else "No answer from the API"
            what += f" ({r['error']})" if r.get("error") else ""
            wait = f" in {_clock(self.retry_at - now)}" if self.retry_at > now else ""
            return f"{what} · retry {r.get('attempt')} of {r.get('max_retries')}{wait} · {total}"
        if self.phase == "starting":
            return f"Starting the next step · {total}" if now - self.started > 2 else f"Starting · {total}"
        return ""

    def long_thinking(self, now: float, after: float) -> str | None:
        """Once per thinking block that runs past `after` seconds while its count still grows: say it is alive."""
        if (self.phase != "thinking" or self.long_noted or now - self.since < after
                or now - self.progress_at >= QUIET_SHOWN):
            return None
        self.long_noted = True
        rate = self.rate()
        speed = f", +{rate:.0f} a second" if rate >= 1 else ""
        return (f"Claude has been thinking for {_clock(now - self.since)} and is not stuck: its plan is still "
                f"growing (~{short(self.thought_tokens())} tokens{speed}). Big designs can take several minutes. "
                "Stop ends it; a lower Effort answers sooner.")

    def stall(self, now: float, limit: float, freecad_busy: bool = False) -> str | None:
        """A warning when nothing happened for `limit` seconds, or None (waiting for the user is fine).

        While thinking, updates that repeat the same token count do not count: only a growing count does.
        """
        if self.phase in ("idle", "approval"):
            return None
        if self.phase == "thinking":
            if now - self.progress_at < limit:
                return None
            quiet = _clock(now - self.progress_at)
            return (f"Claude has been thinking for {_clock(now - self.since)}, but its token count has not grown "
                    f"for {quiet} (~{short(self.thought_tokens())} tokens). It may be stuck: Stop, or create a "
                    "debug report.")
        if now - self.last_event < limit:
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
