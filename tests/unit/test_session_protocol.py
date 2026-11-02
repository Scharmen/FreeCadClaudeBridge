"""Full chat: stream-json session protocol (permission prompts, control requests, thinking, todos)."""

import base64
import json

from claudebridge.panel import stream


def feed(*events):
    p = stream.StreamParser()
    return p.feed(("\n".join(json.dumps(e) for e in events) + "\n").encode())


def test_permission_request_event():
    ev = feed({"type": "control_request", "request_id": "r1", "request": {
        "subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "ls -la"}, "tool_use_id": "t1",
        "permission_suggestions": [{"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "ls:*"}],
                                    "behavior": "allow", "destination": "session"}],
        "decision_reason": "not in allow list"}})[0]
    assert ev["kind"] == "permission_request" and ev["request_id"] == "r1" and ev["tool"] == "Bash"
    assert ev["input"] == {"command": "ls -la"} and ev["suggestions"][0]["type"] == "addRules"
    assert "ls -la" in ev["summary"]


def test_control_response_and_unknown_control_requests():
    evs = feed({"type": "control_response", "response": {"subtype": "success", "request_id": "m1",
                                                        "response": {"model": "haiku"}}},
               {"type": "control_request", "request_id": "x", "request": {"subtype": "hook_callback"}})
    assert evs[0] == {"kind": "control_response", "request_id": "m1", "ok": True, "response": {"model": "haiku"},
                      "error": None}
    assert evs[1]["kind"] == "control_request" and evs[1]["subtype"] == "hook_callback"


def test_thinking_and_todos():
    evs = feed({"type": "stream_event", "event": {"type": "content_block_delta",
                                                  "delta": {"type": "thinking_delta", "thinking": "Let me see"}}},
               {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t9", "name": "TodoWrite",
                                                              "input": {"todos": [
                                                                  {"content": "Sketch", "status": "completed"},
                                                                  {"content": "Pad", "status": "in_progress"}]}}]}})
    assert evs[0] == {"kind": "thinking_delta", "text": "Let me see"}
    todo = [e for e in evs if e["kind"] == "todos"][0]
    assert [t["status"] for t in todo["items"]] == ["completed", "in_progress"]


def test_result_carries_usage():
    ev = feed({"type": "result", "subtype": "success", "result": "ok", "session_id": "s", "total_cost_usd": 0.12,
               "duration_ms": 3400, "num_turns": 2, "usage": {"input_tokens": 10, "output_tokens": 20}})[0]
    assert ev["kind"] == "result" and ev["duration_ms"] == 3400 and ev["num_turns"] == 2
    assert ev["usage"]["output_tokens"] == 20


def test_user_message_with_images():
    msg = stream.user_message("What is this?", images=[("image/png", b"\x89PNGdata")])
    content = msg["message"]["content"]
    assert msg["type"] == "user" and content[0]["type"] == "image"
    assert base64.b64decode(content[0]["source"]["data"]) == b"\x89PNGdata"
    assert content[-1] == {"type": "text", "text": "What is this?"}
    assert stream.user_message("plain")["message"]["content"] == "plain"


def test_permission_answers():
    allow = stream.permission_response("r1", "allow", {"command": "ls"})
    assert allow["response"]["response"] == {"behavior": "allow", "updatedInput": {"command": "ls"}}
    always = stream.permission_response("r1", "always", {"command": "ls"}, suggestions=[{"type": "addRules"}])
    assert always["response"]["response"]["updatedPermissions"] == [{"type": "addRules"}]
    deny = stream.permission_response("r1", "deny", {}, message="not now")
    assert deny["response"]["response"] == {"behavior": "deny", "message": "not now"}
    assert deny["type"] == "control_response" and deny["response"]["request_id"] == "r1"


def test_control_requests():
    req = stream.control_request("set_model", model="opus")
    assert req["type"] == "control_request" and req["request"] == {"subtype": "set_model", "model": "opus"}
    assert req["request_id"] != stream.control_request("interrupt")["request_id"]


def test_session_args():
    args = stream.session_args(session_id="s1", plugin_dir="/p", model="opus", effort="high",
                               permission_mode="acceptEdits", system_prompt="hi")
    assert args[:2] == ["-p", "--input-format"] and "stream-json" in args
    for flag, value in (("--resume", "s1"), ("--plugin-dir", "/p"), ("--model", "opus"), ("--effort", "high"),
                        ("--permission-mode", "acceptEdits"), ("--permission-prompt-tool", "stdio"),
                        ("--append-system-prompt", "hi")):
        assert args[args.index(flag) + 1] == value
    assert args[args.index("--allowedTools") + 1].startswith(stream.FREECAD_TOOL_PREFIX)
    assert "--bare" not in args and "--permission-prompts" not in args
    bare = stream.session_args()
    assert "--model" not in bare and "--resume" not in bare


def test_permission_summary_for_common_tools():
    assert "rm -rf build" in stream.permission_summary("Bash", {"command": "rm -rf build"})
    assert "/a/b.py" in stream.permission_summary("Edit", {"file_path": "/a/b.py", "old_string": "x",
                                                           "new_string": "y"})
    assert "https://x.org" in stream.permission_summary("WebFetch", {"url": "https://x.org"})


def test_slash_command_parsing():
    assert stream.parse_slash("/model opus") == ("model", "opus")
    assert stream.parse_slash("/clear") == ("clear", "")
    assert stream.parse_slash("make a cube") is None
    assert stream.parse_slash("/ notacommand") is None
