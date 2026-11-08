"""Real token usage, plan limits and model names, from Claude Code's stream-json events.

Pure Python, no Qt, so it is unit-tested. Claude Code reports per API call the context it sent
(message_start) and the tokens it wrote including thinking (message_delta), the context window
and cost with the turn's result, and the subscription's usage windows in rate_limit_event.
"""

from __future__ import annotations

import time
from typing import Any

WINDOW_LABELS = {"five_hour": ("5 h", "5-hour limit"), "seven_day": ("week", "Weekly limit"),
                 "seven_day_opus": ("Opus week", "Weekly Opus limit"),
                 "seven_day_sonnet": ("Sonnet week", "Weekly Sonnet limit")}
WARN, CRITICAL = 0.8, 0.95


def short(n: float) -> str:
    """950, 1k, 12.3k, 1M, 1.25M."""
    n = float(n)
    if n < 1000:
        return f"{n:.0f}"
    if n < 1_000_000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".") + "k"
    return f"{n / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M"


def _when(ts: float, now: float) -> str:
    t, today = time.localtime(ts), time.localtime(now)
    return time.strftime("%H:%M" if t.tm_yday == today.tm_yday and t.tm_year == today.tm_year else "%a %H:%M", t)


class Usage:
    def __init__(self, saved: dict[str, Any] | None = None) -> None:
        saved = saved or {}
        self.windows: dict[str, dict[str, Any]] = dict(saved.get("windows") or {})
        self.seen_at = float(saved.get("seen_at") or 0)
        self.windows_by_model: dict[str, int] = dict(saved.get("context_windows") or {})
        self.model = ""
        self.context = 0
        self.cost: float | None = None
        self._out: dict[str, int] = {}
        self._thinking: dict[str, int] = {}

    def save(self) -> dict[str, Any]:
        return {"windows": self.windows, "seen_at": self.seen_at, "context_windows": self.windows_by_model}

    def start_turn(self) -> None:
        self._out, self._thinking, self.cost = {}, {}, None

    @property
    def turn_output(self) -> int:
        return sum(self._out.values())

    @property
    def turn_thinking(self) -> int:
        return sum(self._thinking.values())

    def on_event(self, ev: dict[str, Any], now: float | None = None) -> None:
        kind = ev.get("kind")
        if kind == "usage":
            msg = ev.get("message") or "?"
            self._out[msg] = max(self._out.get(msg, 0), int(ev.get("output") or 0))
            self._thinking[msg] = max(self._thinking.get(msg, 0), int(ev.get("thinking") or 0))
            if ev.get("context"):
                self.context = int(ev["context"])
            if ev.get("model"):
                self.model = ev["model"]
        elif kind == "rate_limit" and ev.get("windows"):
            self.windows = dict(ev["windows"])
            self.seen_at = time.time() if now is None else now
        elif kind == "result":
            for model, mu in (ev.get("model_usage") or {}).items():
                if isinstance(mu, dict) and mu.get("contextWindow"):
                    self.windows_by_model[model] = int(mu["contextWindow"])
            if ev.get("cost_usd") is not None:
                self.cost = float(ev["cost_usd"])

    def context_window(self) -> int | None:
        return self.windows_by_model.get(self.model)

    def turn_text(self) -> str:
        """Tokens Claude wrote this turn (thinking included) and how full the context is."""
        parts = []
        if self.turn_output:
            text = f"{short(self.turn_output)} tokens"
            if self.turn_thinking:
                text += f" ({short(self.turn_thinking)} thinking)"
            parts.append(text)
        if self.context:
            window = self.context_window()
            parts.append(f"context {short(self.context)} of {short(window)} ({100 * self.context / window:.0f} %)"
                         if window else f"context {short(self.context)}")
        return " · ".join(parts)

    def _live(self, now: float) -> list[tuple[str, dict[str, Any]]]:
        return [(name, w) for name, w in self.windows.items()
                if isinstance(w, dict) and w.get("utilization") is not None and float(w.get("resets_at") or 0) > now]

    def limits_text(self, now: float | None = None) -> str:
        """Plan usage, e.g. '5 h 85 % · week 44 %'; windows that have reset since are left out."""
        now = time.time() if now is None else now
        return " · ".join(f"{WINDOW_LABELS.get(n, (n, n))[0]} {100 * float(w['utilization']):.0f} %"
                          for n, w in self._live(now))

    def limits_tooltip(self, now: float | None = None) -> str:
        now = time.time() if now is None else now
        lines = [f"{WINDOW_LABELS.get(n, (n, n))[1]}: {100 * float(w['utilization']):.0f} % used, resets "
                 f"{_when(float(w['resets_at']), now)}" for n, w in self._live(now)]
        if lines and self.seen_at:
            lines.append(f"As reported by Claude with the last answer ({_when(self.seen_at, now)}).")
        return "\n".join(lines)

    def level(self, now: float | None = None) -> str:
        now = time.time() if now is None else now
        top = max((float(w["utilization"]) for _, w in self._live(now)), default=0.0)
        return "critical" if top >= CRITICAL else "warn" if top >= WARN else "ok"


def model_name(model_id: str, models: list[dict[str, Any]]) -> str:
    """A model id's display name ('claude-opus-5-5' -> 'Opus 5.5'), or the id."""
    for m in models:
        if m.get("value") != "default" and m.get("resolvedModel") == model_id and m.get("displayName"):
            return str(m["displayName"])
    for m in models:
        desc = str(m.get("description") or "")
        if m.get("resolvedModel") == model_id and "·" in desc:
            return desc.split("·")[0].strip()
    return model_id


def model_items(models: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """(label, value, tooltip) per model; the default says which model it currently is."""
    out = []
    for m in models:
        value = str(m.get("value") or "")
        label = str(m.get("displayName") or value)
        tip = str(m.get("description") or "")
        resolved = m.get("resolvedModel")
        if value == "default" and resolved:
            label += f" · {model_name(resolved, models)}"
        if resolved:
            tip = (tip + "\n" if tip else "") + f"Model ID: {resolved}"
        out.append((label, value, tip))
    return out


def picture_line(paths: list[str]) -> str:
    """Asks Claude to check the finished model against the user's pictures."""
    if not paths:
        return ""
    files = "; ".join(paths)
    return (f"[Pictures attached, saved at: {files}. Model every detail you see in them. When the model is "
            "built, call compare_to_picture with each picture from the matching view, list every difference "
            "(missing parts, proportions, details, colors), fix them and check again before you answer.]")



def read_profile() -> dict[str, Any]:
    """The account profile Claude Code keeps in its global config (it refreshes it itself). Its
    organizationType follows a plan change right away, unlike the login token's subscriptionType."""
    import json
    import os

    base = os.environ.get("CLAUDE_CONFIG_DIR")
    path = os.path.join(base, ".claude.json") if base else os.path.join(os.path.expanduser("~"), ".claude.json")
    try:
        with open(path, encoding="utf-8") as f:
            account = json.load(f).get("oauthAccount")
    except (OSError, ValueError, AttributeError):
        return {}
    return account if isinstance(account, dict) else {}


PLAN_NAMES = {"claude_max": "Claude Max", "claude_pro": "Claude Pro", "claude_team": "Claude Team",
              "claude_enterprise": "Claude Enterprise", "max": "Claude Max", "pro": "Claude Pro",
              "team": "Claude Team", "enterprise": "Claude Enterprise"}


def plan_name(profile: dict[str, Any], token_type: str | None = None) -> str | None:
    """"Claude Max 5x" from the profile; else the plan the login token names; None when neither says."""
    import re

    org = str(profile.get("organizationType") or "").lower()
    name = PLAN_NAMES.get(org)
    if name:
        tier = re.search(r"(\d+x)$", str(profile.get("organizationRateLimitTier") or ""))
        return f"{name} {tier[1]}" if tier and name == "Claude Max" else name
    if token_type:
        return PLAN_NAMES.get(token_type.lower(), token_type)
    return None
