"""Parse `claude -p --output-format stream-json` output into simple events.

Pure Python, no Qt, so it is unit-tested with recorded output. Tolerant of
unknown event types and fields: anything it doesn't recognize is skipped.

Events (dicts with a "kind" key):
  init           session_id, api_key_source, model, plugins, plugin_errors, mcp
  text_delta     text
  text           text (only when no deltas were streamed for that message)
  tool_use       id, name, input
  tool_result    id, is_error, summary
  permission_denied  tool, message
  result         is_error, text, session_id, cost_usd, denials
  invalid        line (non-JSON output)
"""

from __future__ import annotations

import json
from typing import Any

FREECAD_TOOL_PREFIX = "mcp__plugin_freecad_freecad__"


class StreamParser:
    def __init__(self) -> None:
        self._buf = b""
        self._deltas_in_message = False

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        self._buf += data
        *lines, self._buf = self._buf.split(b"\n")
        events: list[dict[str, Any]] = []
        for raw in lines:
            events.extend(self._line(raw))
        return events

    def flush(self) -> list[dict[str, Any]]:
        rest, self._buf = self._buf, b""
        return self._line(rest)

    def _line(self, raw: bytes) -> list[dict[str, Any]]:
        raw = raw.strip()
        if not raw:
            return []
        try:
            ev = json.loads(raw)
        except ValueError:
            return [{"kind": "invalid", "line": raw.decode("utf-8", "replace")[:500]}]
        if not isinstance(ev, dict):
            return []
        t, sub = ev.get("type"), ev.get("subtype")
        if t == "system" and sub == "init":
            return [_init(ev)]
        if t == "system" and sub == "permission_denied":
            return [{"kind": "permission_denied",
                     "tool": ev.get("tool_name") or ev.get("tool") or "?",
                     "message": ev.get("message") or ev.get("reason") or ""}]
        if t == "stream_event":
            return self._stream_event(ev.get("event") or {})
        if t == "assistant":
            return self._assistant(ev)
        if t == "user":
            return _tool_results(ev)
        if t == "result":
            denials = ev.get("permission_denials") or []
            return [{"kind": "result", "is_error": bool(ev.get("is_error")),
                     "text": ev.get("result") or "", "session_id": ev.get("session_id"),
                     "cost_usd": ev.get("total_cost_usd"), "subtype": sub,
                     "denials": [d.get("tool_name", "?") if isinstance(d, dict) else str(d) for d in denials]}]
        return []

    def _stream_event(self, e: dict[str, Any]) -> list[dict[str, Any]]:
        if e.get("type") == "message_start":
            self._deltas_in_message = False
        delta = e.get("delta") or {}
        if e.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
            self._deltas_in_message = True
            return [{"kind": "text_delta", "text": delta.get("text", "")}]
        return []

    def _assistant(self, ev: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for block in (ev.get("message") or {}).get("content") or []:
            bt = block.get("type")
            if bt == "text" and not self._deltas_in_message:
                out.append({"kind": "text", "text": block.get("text", "")})
            elif bt == "tool_use":
                out.append({"kind": "tool_use", "id": block.get("id"), "name": block.get("name", "?"),
                            "input": block.get("input") or {}})
        self._deltas_in_message = False  # the next message streams its own deltas (or none)
        return out


def _init(ev: dict[str, Any]) -> dict[str, Any]:
    plugins = [p.get("name") if isinstance(p, dict) else str(p) for p in ev.get("plugins") or []]
    mcp = {}
    for s in ev.get("mcp_servers") or []:
        if isinstance(s, dict):
            mcp[s.get("name", "?")] = s.get("status", "?")
    return {"kind": "init", "session_id": ev.get("session_id"), "api_key_source": ev.get("apiKeySource"),
            "model": ev.get("model"), "plugins": plugins, "plugin_errors": ev.get("plugin_errors") or [],
            "mcp": mcp}


def _tool_results(ev: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    content = (ev.get("message") or {}).get("content")
    if not isinstance(content, list):
        return out
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            out.append({"kind": "tool_result", "id": block.get("tool_use_id"),
                        "is_error": bool(block.get("is_error")), "summary": _summary(block.get("content"))})
    return out


def _summary(content: Any, limit: int = 300) -> str:
    if isinstance(content, list):
        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    text = str(content or "").replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def check_init(init: dict[str, Any], mode: str) -> list[str]:
    """Problems with a session start, as user-facing messages. Empty list = fine."""
    problems = []
    if "freecad" not in init.get("plugins", []):
        problems.append("The freecad plugin is not loaded in Claude Code. Run /freecad:setup in Claude Code.")
    for err in init.get("plugin_errors", []):
        if isinstance(err, dict) and err.get("plugin", "").startswith("freecad"):
            problems.append(f"Plugin error: {err.get('message', err)}")
    status = next((v for k, v in init.get("mcp", {}).items() if "freecad" in k), None)
    if status not in (None, "connected", "pending"):
        problems.append(f"The FreeCAD MCP server is {status}. Check /freecad:status in Claude Code.")
    source = init.get("api_key_source")
    if mode == "subscription" and source not in (None, "none"):
        problems.append(f"Subscription mode is selected, but Claude Code uses {source}.")
    if mode == "api_key" and source in (None, "none"):
        problems.append("API key mode is selected, but Claude Code did not pick up the key.")
    return problems


def build_args(prompt: str, session_id: str | None = None, plugin_dir: str | None = None,
               allowed_tools: list[str] | None = None) -> list[str]:
    """Arguments for `claude` in the chat panel. Never `--bare`: it drops the subscription login."""
    args = ["-p", prompt, "--output-format", "stream-json", "--verbose",
            "--include-partial-messages", "--permission-prompts", "none"]
    if session_id:
        args += ["--resume", session_id]
    if plugin_dir:
        args += ["--plugin-dir", plugin_dir]
    tools = allowed_tools if allowed_tools is not None else [FREECAD_TOOL_PREFIX + "*"]
    if tools:
        args += ["--allowedTools", ",".join(tools)]
    return args


def short_tool_name(name: str) -> str:
    return name[len(FREECAD_TOOL_PREFIX):] if name.startswith(FREECAD_TOOL_PREFIX) else name
