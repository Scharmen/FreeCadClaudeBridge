"""Design notes: the decisions behind a model, with their reasons and sources, kept in the document."""

from __future__ import annotations

import time
from typing import Any

HEADER = ("# Design notes\n\nWhy the model looks the way it does: each decision with its reason and where it "
          "came from. Written by Claude; edit freely.\n")
SOURCES = {"request": "your request", "picture": "your picture", "reference": "real-world reference",
           "printability": "printability", "assumption": "my assumption, tell me if it is wrong",
           "feedback": "your feedback"}


def render(topic: str, entries: list[dict[str, Any]], when: str | None = None) -> str:
    """One section of notes in Markdown."""
    if not entries:
        raise ValueError("give at least one note")
    lines = [f"## {when or time.strftime('%Y-%m-%d %H:%M')} · {topic.strip() or 'Model'}", ""]
    for i, e in enumerate(entries, 1):
        decision, reason = str(e.get("decision") or "").strip(), str(e.get("reason") or "").strip()
        if not decision or not reason:
            raise ValueError(f"note {i} needs a decision and a reason")
        part = str(e.get("part") or "").strip()
        source = str(e.get("source") or "").strip()
        text = f"- **{part}:** {decision}" if part else f"- {decision}"
        text = text.rstrip(".") + f". Why: {reason.rstrip('.')}"
        if source:
            text += f" ({SOURCES.get(source.lower(), source)})"
        lines.append(text + ".")
    return "\n".join(lines) + "\n"
