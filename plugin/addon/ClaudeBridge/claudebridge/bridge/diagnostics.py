"""Debug reports: one Markdown file with everything needed to understand a problem.

Secrets (API keys, the bridge token, e-mail addresses) are redacted. Standard library only.
"""

from __future__ import annotations

import glob
import json
import os
import re
import time
from typing import Any

REDACTIONS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),                 # Anthropic API keys
    re.compile(r"\b[0-9a-f]{48,}\b"),                          # hex secrets (bridge token, MACs)
    re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),   # e-mail addresses
]


def redact(text: str) -> str:
    for pattern in REDACTIONS:
        text = pattern.sub("[redacted]", text)
    return text


def tail(path: str, lines: int = 200, max_chars: int = 60000) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            data = f.readlines()[-lines:]
    except OSError:
        return "(not found)"
    return "".join(data)[-max_chars:]


def crash_summary(path: str, frames: int = 12) -> str:
    """Exception and top frames of the crashed thread from a macOS .ips crash report."""
    try:
        with open(path, encoding="utf-8") as f:
            f.readline()
            data = json.loads(f.read())
    except (OSError, ValueError):
        return f"(could not read {path})"
    exc = data.get("exception") or {}
    out = [f"{os.path.basename(path)}: {exc.get('type', '?')} {exc.get('signal', '')} {exc.get('subtype', '')}".strip()]
    threads = data.get("threads") or []
    images = data.get("usedImages") or []
    idx = data.get("faultingThread", 0)
    if idx < len(threads):
        for fr in threads[idx].get("frames", [])[:frames]:
            i = fr.get("imageIndex")
            img = images[i].get("name", "?") if isinstance(i, int) and i < len(images) else "?"
            out.append(f"  {img}  {fr.get('symbol', '')[:120]}")
    return "\n".join(out)


def recent_crash_reports(hours: float = 48) -> list[str]:
    """FreeCAD crash reports of the last hours (macOS keeps them in DiagnosticReports)."""
    folder = os.path.expanduser("~/Library/Logs/DiagnosticReports")
    cutoff = time.time() - hours * 3600
    paths = [p for p in glob.glob(os.path.join(folder, "*reecad*.ips")) if os.path.getmtime(p) >= cutoff]
    return sorted(paths, key=os.path.getmtime, reverse=True)[:3]


def write_report(bridge_dir: Any, sections: dict[str, str], logs: dict[str, str] | None = None) -> str:
    """Write reports/report-<time>.md and return its path."""
    folder = os.path.join(str(bridge_dir), "reports")
    os.makedirs(folder, exist_ok=True)
    parts = [f"# FreeCAD × Claude debug report\n\nCreated {time.strftime('%Y-%m-%d %H:%M:%S %z')}. "
             "Secrets and e-mail addresses are redacted.\n"]
    for title, body in sections.items():
        parts.append(f"## {title}\n\n```\n{body}\n```\n")
    for title, path in (logs or {}).items():
        parts.append(f"## {title} ({os.path.basename(path)}, last lines)\n\n```\n{tail(path)}\n```\n")
    for ips in recent_crash_reports():
        parts.append(f"## Crash report\n\n```\n{crash_summary(ips)}\n```\n")
    path = os.path.join(folder, time.strftime("report-%Y%m%d-%H%M%S.md"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(redact("\n".join(parts)))
    return path
