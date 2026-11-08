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
  result         is_error, text, session_id, cost_usd, denials, duration_ms, num_turns, usage, model_usage
  usage          message, model, context (tokens sent), output, thinking (per API call; see usage.py)
  rate_limit     status, type, windows {name: {utilization 0..1, resets_at, status}} (subscription)
  invalid        line (non-JSON output)
Full chat (persistent session, --input-format stream-json):
  permission_request  request_id, tool, input, suggestions, summary, reason, tool_use_id
  control_request     request_id, subtype, request (other requests from Claude Code)
  control_response    request_id, ok, response, error
  thinking_delta      text, step (estimated tokens this step adds; Claude Code sends no thinking text)
  api_retry           attempt, max_retries, delay_s, status, error (Claude Code retries a failed request)
  system              subtype (any other system event, for the debug log)
  todos               items (from TodoWrite)

Builders for the session's stdin: user_message, control_request, permission_response;
session_args for the command line.
"""

from __future__ import annotations

import base64
import itertools
import json
import re
from typing import Any

FREECAD_TOOL_PREFIX = "mcp__plugin_freecad_freecad__"
MAX_CONTENT = 60000


class TurnTracker:
    """Whether Claude Code is in a turn. Messages sent while it works may be folded into the running turn
    and answered with one result, so counting sends against results drifts and leaves the panel busy for
    good. The stream says it instead: init opens a turn, result closes it."""

    def __init__(self) -> None:
        self.waiting = False      # a message was sent and its turn has not started yet
        self.open = False

    @property
    def busy(self) -> bool:
        return self.waiting or self.open

    def sent(self) -> None:
        if not self.open:
            self.waiting = True

    def on_event(self, kind: str) -> None:
        if kind == "init":
            self.open, self.waiting = True, False
        elif kind == "result":
            self.open = self.waiting = False

    def reset(self) -> None:
        self.open = self.waiting = False


class StreamParser:
    def __init__(self) -> None:
        self._buf = b""
        self._deltas_in_message = False
        self._message: str | None = None
        self._model: str | None = None

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
        if t == "system" and sub == "status":
            return [{"kind": "status", "status": ev.get("status")}]
        if t == "system" and sub == "permission_denied":
            return [{"kind": "permission_denied",
                     "tool": ev.get("tool_name") or ev.get("tool") or "?",
                     "message": ev.get("message") or ev.get("reason") or ""}]
        if t == "system" and sub == "api_retry":
            return [{"kind": "api_retry", "attempt": int(ev.get("attempt") or 0),
                     "max_retries": int(ev.get("max_retries") or 0),
                     "delay_s": round(float(ev.get("retry_delay_ms") or 0) / 1000, 1),
                     "status": ev.get("error_status"), "error": str(ev.get("error") or "")}]
        if t == "system" and sub == "thinking_tokens":
            return []          # the running total, every second; the thinking deltas carry the same steps
        if t == "system":
            return [{"kind": "system", "subtype": sub}]
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
                     "duration_ms": ev.get("duration_ms"), "num_turns": ev.get("num_turns"),
                     "usage": ev.get("usage") or {}, "model_usage": ev.get("modelUsage") or {},
                     "denials": [d.get("tool_name", "?") if isinstance(d, dict) else str(d) for d in denials]}]
        if t == "rate_limit_event":
            return [_rate_limit(ev.get("rate_limit_info") or {})]
        if t == "control_request":
            return [_control_request(ev)]
        if t == "control_response":
            r = ev.get("response") or {}
            return [{"kind": "control_response", "request_id": r.get("request_id"), "ok": r.get("subtype") == "success",
                     "response": r.get("response"), "error": r.get("error")}]
        return []

    def _stream_event(self, e: dict[str, Any]) -> list[dict[str, Any]]:
        if e.get("type") == "message_start":
            self._deltas_in_message = False
            msg = e.get("message") or {}
            self._message, self._model = msg.get("id"), msg.get("model")
            return [_usage(msg.get("usage"), self._message, self._model)]
        if e.get("type") == "message_delta" and e.get("usage"):
            return [_usage(e["usage"], self._message, self._model)]
        if e.get("type") == "content_block_start" and (e.get("content_block") or {}).get("type") == "thinking":
            return [{"kind": "thinking_start"}]
        delta = e.get("delta") or {}
        if e.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
            self._deltas_in_message = True
            return [{"kind": "text_delta", "text": delta.get("text", "")}]
        if e.get("type") == "content_block_delta" and delta.get("type") == "thinking_delta":
            out = {"kind": "thinking_delta", "text": delta.get("thinking", "")}
            if delta.get("estimated_tokens") is not None:     # no thinking text, only the size of this step
                out["step"] = delta["estimated_tokens"]
            return [out]
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
                if block.get("name") == "TodoWrite":
                    items = (block.get("input") or {}).get("todos") or []
                    out.append({"kind": "todos", "items": [t for t in items if isinstance(t, dict)]})
        self._deltas_in_message = False  # the next message streams its own deltas (or none)
        return out


def _usage(u: Any, message: str | None, model: str | None) -> dict[str, Any]:
    u = u if isinstance(u, dict) else {}
    context = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                "cache_read_input_tokens"))
    return {"kind": "usage", "message": message, "model": model, "context": context,
            "output": int(u.get("output_tokens") or 0),
            "thinking": int((u.get("output_tokens_details") or {}).get("thinking_tokens") or 0)}


def _rate_limit(info: dict[str, Any]) -> dict[str, Any]:
    windows = {name: {"utilization": w.get("utilization"), "resets_at": w.get("resetsAt"), "status": w.get("status")}
               for name, w in (info.get("unifiedWindows") or {}).items() if isinstance(w, dict)}
    return {"kind": "rate_limit", "status": info.get("status"), "type": info.get("rateLimitType"), "windows": windows}


def _control_request(ev: dict[str, Any]) -> dict[str, Any]:
    req = ev.get("request") or {}
    if req.get("subtype") == "can_use_tool":
        tool, inp = req.get("tool_name", "?"), req.get("input") or {}
        return {"kind": "permission_request", "request_id": ev.get("request_id"), "tool": tool, "input": inp,
                "suggestions": req.get("permission_suggestions") or [], "tool_use_id": req.get("tool_use_id"),
                "reason": req.get("decision_reason") or "", "summary": permission_summary(tool, inp)}
    return {"kind": "control_request", "request_id": ev.get("request_id"), "subtype": req.get("subtype"),
            "request": req}


def permission_summary(tool: str, inp: dict[str, Any]) -> str:
    """One line saying what a tool call would do, for the approval card."""
    if tool == "AskUserQuestion":
        asked = [q.get("question", "") for q in inp.get("questions") or [] if isinstance(q, dict)]
        return "Question: " + " / ".join(asked)[:400]
    if tool == "ExitPlanMode":
        return "Plan: " + str(inp.get("plan", ""))[:200].replace("\n", " ")
    for key in ("command", "file_path", "notebook_path", "url", "query", "pattern", "path", "prompt"):
        if inp.get(key):
            return f"{short_tool_name(tool)}: {str(inp[key])[:400]}"
    return f"{short_tool_name(tool)}: {json.dumps(inp, ensure_ascii=False)[:400]}"


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
            text = _text(block.get("content"))
            out.append({"kind": "tool_result", "id": block.get("tool_use_id"),
                        "is_error": bool(block.get("is_error")), "summary": _summary(text),
                        "size": len(text), "content": text[:MAX_CONTENT]})
    return out


def _text(content: Any) -> str:
    if isinstance(content, list):
        return " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return str(content or "")


def _summary(text: str, limit: int = 300) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def turn_summary(results: list[str]) -> dict[str, Any]:
    """Aggregate `changes` and `errors` from the JSON results of freecad tool calls in one turn."""
    created: list[str] = []
    changed: list[str] = []
    deleted: list[str] = []
    errors: dict[str, dict[str, Any]] = {}
    for text in results:
        try:
            data = json.loads(text)
            if isinstance(data, dict) and set(data) == {"result"} and isinstance(data["result"], str):
                data = json.loads(data["result"])  # structured-output wrapper of older servers
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        ch = data.get("changes") or {}
        for src, dst in ((ch.get("created"), created), (ch.get("changed"), changed), (ch.get("deleted"), deleted)):
            for name in src or []:
                if name not in dst:
                    dst.append(name)
        for name in data.get("created", []) if isinstance(data.get("created"), list) else []:
            n = name.get("name") if isinstance(name, dict) else name
            if n and n not in created:
                created.append(n)
        if "errors" in data:  # the latest state per object wins
            for e in data.get("errors") or []:
                errors[e.get("name", "?")] = e
            for name in list(errors):
                if name in (ch.get("changed") or []) and name not in {e.get("name") for e in data["errors"]}:
                    errors.pop(name)
    changed = [c for c in changed if c not in created]
    return {"created": created, "changed": changed, "deleted": deleted, "errors": list(errors.values())}


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
               allowed_tools: list[str] | None = None, extra_allowed: list[str] | None = None) -> list[str]:
    """Arguments for `claude` in the chat panel. Never `--bare`: it drops the subscription login.

    `extra_allowed` are tools the user approved for this conversation with the panel's button.
    """
    args = ["-p", prompt, "--output-format", "stream-json", "--verbose",
            "--include-partial-messages", "--permission-prompts", "none"]
    if session_id:
        args += ["--resume", session_id]
    if plugin_dir:
        args += ["--plugin-dir", plugin_dir]
    tools = list(allowed_tools if allowed_tools is not None else [FREECAD_TOOL_PREFIX + "*"])
    tools += [t for t in extra_allowed or [] if t not in tools]
    if tools:
        args += ["--allowedTools", ",".join(tools)]
    return args


def short_tool_name(name: str) -> str:
    return name[len(FREECAD_TOOL_PREFIX):] if name.startswith(FREECAD_TOOL_PREFIX) else name


# ---------- full chat: the persistent session's command line and stdin messages

_request_ids = itertools.count(1)
PERMISSION_MODES = ["default", "acceptEdits", "plan", "auto", "bypassPermissions"]


def session_args(session_id: str | None = None, plugin_dir: str | None = None, model: str | None = None,
                 effort: str | None = None, permission_mode: str | None = None, system_prompt: str | None = None,
                 allowed_tools: list[str] | None = None, fork: bool = False) -> list[str]:
    """Arguments for a persistent `claude` session that reads messages and permission answers on stdin.

    All tools are available; anything the user's Claude Code settings don't allow comes back as a
    permission_request (--permission-prompt-tool stdio). The FreeCAD tools are allowed up front.
    Never `--bare`: it drops the subscription login and installed plugins.
    """
    args = ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--include-partial-messages", "--permission-prompt-tool", "stdio"]
    for flag, value in (("--resume", session_id), ("--plugin-dir", plugin_dir), ("--model", model),
                        ("--effort", effort), ("--permission-mode", permission_mode),
                        ("--append-system-prompt", system_prompt)):
        if value:
            args += [flag, value]
    if fork and session_id:              # a sub chat: the main chat's history, under a new session id
        args.append("--fork-session")
    tools = allowed_tools if allowed_tools is not None else [FREECAD_TOOL_PREFIX + "*"]
    if tools:
        args += ["--allowedTools", ",".join(tools)]
    return args


def user_message(text: str, images: list[tuple[str, bytes]] | None = None) -> dict[str, Any]:
    """A user turn; images go as real image content (media type, bytes)."""
    if not images:
        return {"type": "user", "message": {"role": "user", "content": text}}
    content: list[dict[str, Any]] = [
        {"type": "image", "source": {"type": "base64", "media_type": mt, "data": base64.b64encode(data).decode()}}
        for mt, data in images]
    content.append({"type": "text", "text": text})
    return {"type": "user", "message": {"role": "user", "content": content}}


def control_request(subtype: str, **fields: Any) -> dict[str, Any]:
    return {"type": "control_request", "request_id": f"fcb-{next(_request_ids)}",
            "request": {"subtype": subtype, **fields}}


def permission_response(request_id: str, decision: str, tool_input: dict[str, Any],
                        suggestions: list[dict[str, Any]] | None = None, message: str = "") -> dict[str, Any]:
    """decision: allow (once), always (allow and apply Claude Code's suggested rules), deny."""
    if decision == "deny":
        answer: dict[str, Any] = {"behavior": "deny", "message": message or "The user declined this action."}
    else:
        answer = {"behavior": "allow", "updatedInput": tool_input}
        if decision == "always" and suggestions:
            answer["updatedPermissions"] = suggestions
    return {"type": "control_response", "response": {"subtype": "success", "request_id": request_id,
                                                     "response": answer}}


_SLASH = re.compile(r"^/([A-Za-z][\w:.-]*)(?:\s+(.*))?$", re.S)


def parse_slash(text: str) -> tuple[str, str] | None:
    m = _SLASH.match(text.strip())
    return (m.group(1), (m.group(2) or "").strip()) if m else None


# ---------- questions Claude asks (AskUserQuestion) and the panel's auto mode

QUESTION_TOOL = "AskUserQuestion"


def questions(tool: str, tool_input: dict[str, Any]) -> list[dict[str, Any]]:
    """The questions of an AskUserQuestion request as {question, header, multi, options: [labels],
    descriptions: [one per option]}."""
    if tool != QUESTION_TOOL:
        return []
    out = []
    for q in tool_input.get("questions") or []:
        if isinstance(q, dict) and q.get("question"):
            out.append({"question": q["question"], "header": q.get("header", ""), "multi": bool(q.get("multiSelect")),
                        "options": [o.get("label", "") for o in q.get("options") or [] if isinstance(o, dict)],
                        "descriptions": [o.get("description", "") for o in q.get("options") or []
                                         if isinstance(o, dict)]})
    return out


def answered_input(tool_input: dict[str, Any], answers: dict[str, str]) -> dict[str, Any]:
    return {**tool_input, "answers": dict(answers)}


def auto_answers(tool: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Input for auto mode: questions get their first (recommended) option, other tools stay unchanged."""
    qs = questions(tool, tool_input)
    if not qs:
        return tool_input
    return answered_input(tool_input, {q["question"]: q["options"][0] for q in qs if q["options"]})
