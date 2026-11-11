"""Real token usage, plan limits and model names in the chat panel, from Claude Code's stream."""

import json

from claudebridge.panel import activity, stream, usage

MODELS = [
    {"value": "default", "resolvedModel": "claude-opus-5-5", "displayName": "Default (recommended)",
     "description": "Opus 5.5 · Best for everyday, complex tasks"},
    {"value": "opus", "resolvedModel": "claude-opus-5-5", "displayName": "Opus 5.5",
     "description": "For complex work and everyday tasks"},
    {"value": "sonnet", "resolvedModel": "claude-sonnet-5-5", "displayName": "Sonnet 5.5",
     "description": "Most efficient for simpler tasks"},
]


def feed(*events):
    p = stream.StreamParser()
    return p.feed(("\n".join(json.dumps(e) for e in events) + "\n").encode())


def start(msg_id, context, model="claude-opus-5-5"):
    return {"type": "stream_event", "event": {"type": "message_start", "message": {
        "id": msg_id, "model": model, "usage": {"input_tokens": 2, "cache_creation_input_tokens": 1000,
                                                "cache_read_input_tokens": context - 1002, "output_tokens": 4}}}}


def delta(output, thinking=0):
    return {"type": "stream_event", "event": {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
                                              "usage": {"output_tokens": output,
                                                        "output_tokens_details": {"thinking_tokens": thinking}}}}


RATE = {"type": "rate_limit_event", "rate_limit_info": {
    "status": "allowed", "rateLimitType": "five_hour", "unifiedWindows": {
        "five_hour": {"utilization": 0.85, "resetsAt": 2000, "status": "allowed"},
        "seven_day": {"utilization": 0.44, "resetsAt": 9000, "status": "allowed"}}}}
RESULT = {"type": "result", "subtype": "success", "total_cost_usd": 0.12, "usage": {"output_tokens": 45},
          "modelUsage": {"claude-opus-5-5": {"contextWindow": 1000000, "outputTokens": 45, "costUSD": 0.12}}}


def test_the_parser_reports_real_usage_and_plan_limits():
    evs = feed(start("m1", 46000), delta(12248, 5917), RATE, RESULT)
    use = [e for e in evs if e["kind"] == "usage"]
    assert use[0] == {"kind": "usage", "message": "m1", "model": "claude-opus-5-5", "context": 46000, "output": 4,
                      "thinking": 0}
    assert use[1]["output"] == 12248 and use[1]["thinking"] == 5917 and use[1]["message"] == "m1"
    limits = next(e for e in evs if e["kind"] == "rate_limit")
    assert limits["windows"]["five_hour"] == {"utilization": 0.85, "resets_at": 2000, "status": "allowed"}
    result = next(e for e in evs if e["kind"] == "result")
    assert result["model_usage"]["claude-opus-5-5"]["contextWindow"] == 1000000


def test_a_turn_adds_up_every_api_call_not_only_the_last_estimate():
    u = usage.Usage()
    u.start_turn()
    for ev in feed(start("m1", 40000), delta(800, 600), start("m2", 46000), delta(1500, 200), RESULT):
        u.on_event(ev)
    assert u.turn_output == 2300 and u.turn_thinking == 800
    assert u.context == 46000 and u.context_window() == 1000000
    assert u.turn_text() == "2.3k tokens (800 thinking) · context 46k of 1M (5 %)"
    u.start_turn()                                     # the next turn starts at zero, the context stays
    assert u.turn_output == 0 and u.context == 46000


def test_context_without_a_known_window_and_number_format():
    u = usage.Usage()
    for ev in feed(start("m1", 46000, model="claude-new"), delta(150)):
        u.on_event(ev)
    assert u.turn_text() == "150 tokens · context 46k"
    assert [usage.short(n) for n in (950, 1000, 12345, 1_000_000, 1_250_000)] == ["950", "1k", "12.3k", "1M", "1.25M"]


def test_plan_limits_are_shown_until_they_reset_and_survive_a_restart():
    u = usage.Usage()
    for ev in feed(RATE):
        u.on_event(ev, now=1000)
    assert u.limits_text(now=1500) == "5 h 85 % · week 44 %"
    assert "resets" in u.limits_tooltip(now=1500) and "5-hour" in u.limits_tooltip(now=1500)
    assert u.level(now=1500) == "warn"                 # 80 % and more
    assert u.limits_text(now=2500) == "week 44 %"      # the 5-hour window has reset
    again = usage.Usage(u.save())
    assert again.limits_text(now=1500) == "5 h 85 % · week 44 %"


def test_the_default_model_says_which_model_it_is():
    items = usage.model_items(MODELS)
    assert items[0][0] == "Default (recommended) · Opus 5.5" and items[0][1] == "default"
    assert "claude-opus-5-5" in items[0][2]
    assert items[2][0] == "Sonnet 5.5"
    odd = usage.model_items([{"value": "default", "resolvedModel": "claude-x-9", "displayName": "Default",
                              "description": "Recommended"}])
    assert odd[0][0] == "Default · claude-x-9"
    assert usage.model_name("claude-opus-5-5", MODELS) == "Opus 5.5"


def test_thinking_shows_the_turns_real_tokens_plus_the_running_estimate():
    a = activity.Activity()
    a.start(now=100)
    for ev in feed(start("m1", 40000), delta(3000, 2000)):
        a.on_event(ev, now=101)
    a.on_event({"kind": "thinking_start"}, now=102)
    a.on_event({"kind": "thinking_delta", "text": "", "step": 1500}, now=110)
    assert a.describe(now=112) == "Thinking · 0:10 · ~4.5k tokens"


def test_a_picture_line_asks_for_a_check_against_the_pictures():
    line = usage.picture_line(["/tmp/a.png", "/tmp/b b.jpg"])
    assert "/tmp/a.png" in line and "/tmp/b b.jpg" in line and "compare_to_picture" in line
    assert usage.picture_line([]) == ""



def test_effort_entries_say_what_they_do_and_auto_names_the_level_it_used():
    from claudebridge.panel import tips

    label, tip = tips.effort_auto_item("high", "claude-opus-5-5")
    assert label == "Effort: auto → high" and "last answer" in tip and "default" in tip, "what it used wins"
    label, tip = tips.effort_auto_item(None, "opus")
    assert label == "Effort: auto → medium" and "Claude Code's default" in tip
    assert tips.effort_auto_item(None, "claude-fable-5-1")[0] == "Effort: auto → high"
    label, tip = tips.effort_auto_item(None, "some-new-model")
    assert label == "Effort: auto (Claude Code default)" and "after the first answer" in tip
    for level in ("low", "medium", "high", "xhigh", "max"):
        assert tips.EFFORT_TIPS[level]


def test_every_chat_button_and_permission_mode_has_a_tooltip():
    from claudebridge.panel import tips

    buttons = [("Allow once", "perm:allow:r1"), ("Deny", "perm:deny:r1"), ("A roof", "perm:ans:r3:0:1"),
               ("Always", "perm:always:r1"), ("Stop", "stop:"), ("Create debug report", "report:"),
               ("Open copies", "snapshots:")]
    assert all(tips.button_tip(label, href) for label, href in buttons)
    assert tips.button_tip("Skip", "perm:deny:r2").startswith("Skip this question"), "Skip is no Deny"
    for mode in stream.PERMISSION_MODES:
        assert tips.MODE_TIPS[mode]


def test_a_sub_chat_tells_claude_which_part_it_owns():
    from claudebridge.panel import tips

    line = tips.part_focus({"name": "Roof001", "label": "Roof"})
    assert "Roof (Roof001)" in line and "only" in line and "main chat" in line
    assert tips.part_focus(None) == ""


def test_a_sub_chat_starts_as_a_fork_of_its_main_chat():
    args = stream.session_args("main-1", fork=True)
    assert args[args.index("--resume") + 1] == "main-1" and "--fork-session" in args
    assert "--fork-session" not in stream.session_args("main-1"), "a normal resume continues the same session"
    assert "--fork-session" not in stream.session_args(None, fork=True), "nothing to fork"


def test_the_plan_comes_from_the_profile_claude_code_refreshes():
    max5 = {"organizationType": "claude_max", "organizationRateLimitTier": "default_claude_max_5x"}
    assert usage.plan_name(max5, token_type="pro") == "Claude Max 5x", "the login token lags behind an upgrade"
    assert usage.plan_name({"organizationType": "claude_max",
                            "organizationRateLimitTier": "default_claude_max_20x"}) == "Claude Max 20x"
    assert usage.plan_name({"organizationType": "claude_pro"}) == "Claude Pro"
    assert usage.plan_name({"organizationType": "claude_team"}) == "Claude Team"
    assert usage.plan_name({}, token_type="Claude Pro") == "Claude Pro", "no profile: what Claude Code said"
    assert usage.plan_name({}, token_type="max") == "Claude Max"
    assert usage.plan_name({}) is None


def test_the_profile_is_read_from_claude_codes_config(tmp_path, monkeypatch):
    (tmp_path / ".claude.json").write_text(json.dumps({"oauthAccount": {"organizationType": "claude_max",
                                                                        "organizationRateLimitTier": "x_5x"}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert usage.read_profile()["organizationType"] == "claude_max"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "missing"))
    assert usage.read_profile() == {}


def test_the_desktop_handoff_gets_a_terminal_because_claude_code_refuses_without_one():
    prog, args = stream.desktop_command("/u/claude", "abc-123", "darwin")
    assert prog == "/usr/bin/script" and args == ["-q", "/dev/null", "/u/claude", "--desktop", "--resume", "abc-123"]
    prog, args = stream.desktop_command("/u/claude", "abc-123", "linux")
    assert prog == "script" and args[:2] == ["-q", "-c"] and "--desktop --resume abc-123" in args[2]
    assert stream.desktop_command("C:/c/claude.exe", "abc-123", "win32") == \
        ("C:/c/claude.exe", ["--desktop", "--resume", "abc-123"]), "a new console window is a terminal on Windows"


def test_the_handoff_answer_is_readable():
    raw = "\x04\x1b[0m\x1b[31m--desktop only opens the Claude Desktop app; it can't be combined.\x1b[0m\r\n\x1b[?25h"
    assert stream.plain_terminal_text(raw) == "--desktop only opens the Claude Desktop app; it can't be combined."


def test_a_chat_already_open_in_claude_desktop_is_brought_up_by_its_link():
    assert stream.desktop_link("abc-123") == "claude://claude.ai/epitaxy/local_abc-123"
    assert stream.handoff_busy("Session abc-123 is open in another terminal. Run /desktop there instead.")
    assert not stream.handoff_busy("Claude Code ended with code 1")


def test_the_turn_knows_which_documents_claude_changed():
    import json as _json

    results = [_json.dumps({"changes": {"created": [], "changed": ["Roof"], "deleted": []}, "document": "Board"}),
               _json.dumps({"changes": {"created": [], "changed": [], "deleted": []}, "document": "Other"}),
               _json.dumps({"result": {"valid": True}, "document": "Read"})]
    assert stream.turn_summary(results)["documents"] == ["Board"], "only documents with changes"


def test_new_models_are_named_when_claude_code_lists_them():
    old = [{"value": "opus", "displayName": "Opus 5.5"}, {"value": "fable", "displayName": "Fable 5.1"}]
    new = old + [{"value": "claude-fable-5-2", "displayName": "Fable 5.2"}]
    assert usage.new_models(old, new) == ["Fable 5.2"]
    assert usage.new_models(old, old) == []
    assert usage.new_models([], new) == [], "a first list has nothing new to announce"


def test_a_long_chat_is_compacted_above_the_threshold():
    assert usage.should_compact(context=230_000, threshold_k=200)
    assert not usage.should_compact(context=150_000, threshold_k=200)
    assert not usage.should_compact(context=900_000, threshold_k=0), "0 switches it off"


def test_the_compact_command_says_what_to_keep_and_names_a_sub_chats_part():
    from claudebridge.panel import tips

    cmd = tips.compact_command(None)
    assert cmd.startswith("/compact ") and "dimensions" in cmd and "decisions" in cmd and "\n" not in cmd
    sub = tips.compact_command({"name": "Roof001", "label": "Roof"})
    assert "Roof (Roof001)" in sub and "this part" in sub


def test_compacting_shows_in_the_status_line_and_is_no_stall():
    a = activity.Activity()
    a.start(now=0)
    a.on_event({"kind": "status", "status": "compacting"}, now=1)
    assert a.describe(now=61).startswith("Compacting the conversation · 1:00")
    assert a.stall(now=400, limit=120) is None
