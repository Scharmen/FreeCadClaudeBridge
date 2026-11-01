from pathlib import Path

from claudebridge.panel.stream import StreamParser, build_args, check_init, short_tool_name

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "stream_tool_call.jsonl"


def parse_all(data: bytes, chunk: int | None = None):
    p = StreamParser()
    events = []
    if chunk:
        for i in range(0, len(data), chunk):
            events += p.feed(data[i:i + chunk])
    else:
        events += p.feed(data)
    return events + p.flush()


def test_parses_recorded_turn():
    events = parse_all(FIXTURE.read_bytes())
    kinds = [e["kind"] for e in events]
    assert kinds == ["init", "text_delta", "text_delta", "tool_use", "tool_result",
                     "permission_denied", "text", "result"]
    init = events[0]
    assert init["session_id"] == "sess-1" and init["plugins"] == ["freecad"]
    assert init["mcp"] == {"plugin:freecad:freecad": "connected"}
    tool = events[3]
    assert short_tool_name(tool["name"]) == "run_python"
    assert events[4]["id"] == "tu1" and not events[4]["is_error"]
    assert "Cube" in events[4]["summary"]
    assert events[-1]["denials"] == ["Bash"]


def test_full_text_not_duplicated_after_deltas():
    texts = [e for e in parse_all(FIXTURE.read_bytes()) if e["kind"] in ("text", "text_delta")]
    joined = "".join(e["text"] for e in texts)
    assert joined == "Creating the cube.Done: 1000 mm³."


def test_split_across_chunks():
    data = FIXTURE.read_bytes()
    assert parse_all(data, chunk=7) == parse_all(data)


def test_tolerates_garbage_and_unknown_events():
    events = parse_all(b'not json\n{"type":"brand_new_event","x":1}\n[1,2]\n')
    assert events == [{"kind": "invalid", "line": "not json"}]


def test_check_init_ok_subscription():
    init = parse_all(FIXTURE.read_bytes())[0]
    assert check_init(init, "subscription") == []


def test_check_init_reports_problems():
    init = {"plugins": [], "mcp": {"plugin:freecad:freecad": "failed"}, "api_key_source": "ANTHROPIC_API_KEY",
            "plugin_errors": [{"plugin": "freecad", "message": "boom"}]}
    problems = check_init(init, "subscription")
    assert len(problems) == 4
    assert any("not loaded" in p for p in problems)
    assert any("failed" in p for p in problems)
    assert any("Subscription mode" in p for p in problems)


def test_check_init_api_key_mode_without_key():
    init = {"plugins": ["freecad"], "mcp": {}, "api_key_source": "none"}
    assert check_init(init, "api_key") == ["API key mode is selected, but Claude Code did not pick up the key."]


def test_build_args():
    args = build_args("hi", session_id="s1", plugin_dir="/p")
    assert args[:2] == ["-p", "hi"]
    assert "--bare" not in args
    for flag in ("--verbose", "--include-partial-messages"):
        assert flag in args
    assert args[args.index("--output-format") + 1] == "stream-json"
    assert args[args.index("--permission-prompts") + 1] == "none"
    assert args[args.index("--resume") + 1] == "s1"
    assert args[args.index("--plugin-dir") + 1] == "/p"
    assert args[args.index("--allowedTools") + 1] == "mcp__plugin_freecad_freecad__*"


def test_build_args_first_message():
    args = build_args("hi")
    assert "--resume" not in args and "--plugin-dir" not in args


def test_tool_result_carries_content_and_size():
    tr = next(e for e in parse_all(FIXTURE.read_bytes()) if e["kind"] == "tool_result")
    assert tr["size"] == len(tr["content"]) and '"created":["Cube"]' in tr["content"]


def test_extra_allowed_tools():
    args = build_args("hi", extra_allowed=["Bash", "mcp__plugin_freecad_freecad__*"])
    assert args[args.index("--allowedTools") + 1] == "mcp__plugin_freecad_freecad__*,Bash"


def test_turn_summary_aggregates_changes_and_latest_errors():
    from claudebridge.panel.stream import turn_summary

    results = [
        '{"changes":{"created":["Body","Pad"],"changed":["Sketch"],"deleted":[]},"errors":[{"name":"Pad","state":["Invalid"]}]}',
        '{"changes":{"created":[],"changed":["Pad","Sketch"],"deleted":[]},"errors":[]}',
        '{"document":"D","created":[{"name":"Imported"}],"errors":[]}',
        "not json",
    ]
    s = turn_summary(results)
    assert s["created"] == ["Body", "Pad", "Imported"]
    assert s["changed"] == ["Sketch"]
    assert s["errors"] == []
    s2 = turn_summary(results[:1])
    assert [e["name"] for e in s2["errors"]] == ["Pad"]


def test_turn_summary_unwraps_structured_output():
    import json

    from claudebridge.panel.stream import turn_summary

    inner = json.dumps({"changes": {"created": ["CylA"], "changed": [], "deleted": []}, "errors": []})
    wrapped = json.dumps({"result": inner})
    assert turn_summary([wrapped])["created"] == ["CylA"]
