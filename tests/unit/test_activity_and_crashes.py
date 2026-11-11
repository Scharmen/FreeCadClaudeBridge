"""Live activity, stall detection, crash markers and debug reports."""

import json
import os

from claudebridge.bridge import crashguard, diagnostics
from claudebridge.panel import activity, stream


def feed(*events):
    p = stream.StreamParser()
    return p.feed(("\n".join(json.dumps(e) for e in events) + "\n").encode())


# ---------- what Claude is doing right now

def test_thinking_progress_and_status_events():
    evs = feed({"type": "stream_event", "event": {"type": "content_block_start",
                                                  "content_block": {"type": "thinking", "thinking": ""}}},
               {"type": "stream_event", "event": {"type": "content_block_delta",
                                                  "delta": {"type": "thinking_delta", "thinking": "",
                                                            "estimated_tokens": 150}}},
               {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 1200,
                "estimated_tokens_delta": 150},
               {"type": "system", "subtype": "status", "status": "requesting"})
    kinds = [e["kind"] for e in evs]
    assert "thinking_start" in kinds and {"kind": "status", "status": "requesting"} in evs
    delta = next(e for e in evs if e["kind"] == "thinking_delta")
    assert delta["step"] == 150 and delta["text"] == "", "Claude Code sends the size of each step, not a total"
    assert "system" not in kinds, "the running total arrives every second and is not worth a log line"


def test_api_retries_and_unknown_system_events_reach_the_panel():
    evs = feed({"type": "system", "subtype": "api_retry", "attempt": 2, "max_retries": 10, "retry_delay_ms": 18000,
                "error_status": 529, "error": "overloaded"},
               {"type": "system", "subtype": "something_new", "x": 1})
    assert evs[0] == {"kind": "api_retry", "attempt": 2, "max_retries": 10, "delay_s": 18.0, "status": 529,
                      "error": "overloaded"}
    assert evs[1] == {"kind": "system", "subtype": "something_new"}


def test_activity_describes_the_current_step():
    a = activity.Activity()
    a.start(now=100)
    assert a.describe(now=101).startswith("Starting")
    a.on_event({"kind": "thinking_start"}, now=102)
    a.on_event({"kind": "thinking_delta", "text": "", "step": 1000}, now=108)
    a.on_event({"kind": "thinking_delta", "text": "", "step": 500}, now=110)
    assert a.describe(now=112) == "Thinking · 0:10 · ~1.5k tokens · +250/s"   # the steps add up
    a.on_event({"kind": "tool_use", "id": "t1", "name": "mcp__plugin_freecad_freecad__run_python",
                "input": {}}, now=120)
    assert a.describe(now=135) == "Running run_python · 0:15 · total 0:35"
    a.on_event({"kind": "tool_result", "id": "t1"}, now=140)
    a.on_event({"kind": "text_delta", "text": "Done"}, now=141)
    assert a.describe(now=142).startswith("Writing the answer")
    a.on_event({"kind": "permission_request"}, now=143)
    assert a.describe(now=150).startswith("Waiting for your approval")


def test_thinking_duration_line():
    a = activity.Activity()
    a.start(now=0)
    a.on_event({"kind": "thinking_start"}, now=1)
    a.on_event({"kind": "text_delta", "text": "x"}, now=13)
    assert a.last_thought_seconds == 12


def test_a_running_tool_is_never_called_stuck():
    a = activity.Activity()
    a.start(now=0)
    a.on_event({"kind": "tool_use", "id": "t", "name": "mcp__plugin_freecad_freecad__check_all", "input": {}},
               now=5)
    assert a.describe(now=130) == "Running check_all · 2:05 · total 2:10"
    for late in (200, 3600):
        assert a.stall(now=late, limit=90) is None, "the status line shows it is running"
    a.on_event({"kind": "tool_result", "id": "t", "content": "ok"}, now=3600)
    assert a.stall(now=3650, limit=90) is None
    msg = a.stall(now=3700, limit=90)
    assert msg and "no sign of life" in msg.lower(), "silence from Claude itself is still reported"
    a.on_event({"kind": "permission_request"}, now=3710)
    assert a.stall(now=9000, limit=90) is None, "waiting for the user is not a stall"


def think(a, step, now):
    return a.on_event({"kind": "thinking_delta", "text": "", "step": step}, now=now)


def test_long_thinking_shows_that_the_token_count_still_grows():
    a = activity.Activity()
    a.start(now=0)
    a.on_event({"kind": "thinking_start"}, now=1)
    for t in range(2, 722, 2):                       # 12 minutes, 40 tokens a second, in steps of 80
        assert think(a, 80, now=t), "a step that adds tokens is progress"
    line = a.describe(now=721)
    assert line.startswith("Thinking · 12:00 · ~28.8k tokens") and "+40/s" in line, line
    assert a.stall(now=721, limit=120) is None


def test_thinking_whose_count_stops_growing_is_a_stall_even_with_events():
    a = activity.Activity()
    a.start(now=0)
    a.on_event({"kind": "thinking_start"}, now=1)
    think(a, 5000, now=10)
    for t in range(12, 300, 2):                      # updates keep coming, the count does not move
        assert not think(a, 0, now=t), "an empty step is no progress"
    line = a.describe(now=300)
    assert "no new tokens for 4:50" in line and "/s" not in line, line
    msg = a.stall(now=300, limit=120)
    assert msg and "not grown for 4:50" in msg and "~5k tokens" in msg, msg


def test_thinking_without_token_estimates_still_counts_as_alive():
    a = activity.Activity()
    a.start(now=0)
    a.on_event({"kind": "thinking_start"}, now=1)
    for t in range(2, 400, 5):
        assert a.on_event({"kind": "thinking_delta", "text": "hm"}, now=t)
    assert a.stall(now=400, limit=120) is None


def test_long_thinking_gets_one_note_that_it_is_still_planning():
    a = activity.Activity()
    a.start(now=0)
    a.on_event({"kind": "thinking_start"}, now=0)
    for t in range(1, 302):
        think(a, 30, now=t)
    assert a.long_thinking(now=200, after=300) is None
    note = a.long_thinking(now=301, after=300)
    assert note and "5:01" in note and "still growing" in note and "Stop" in note, note
    assert a.long_thinking(now=400, after=300) is None, "said once per thinking block"


def test_an_api_retry_is_shown_with_its_countdown_and_is_no_stall():
    a = activity.Activity()
    a.start(now=0)
    a.on_event({"kind": "thinking_start"}, now=1)
    think(a, 150, now=2)
    a.on_event({"kind": "api_retry", "attempt": 2, "max_retries": 10, "delay_s": 300.0, "status": 529,
                "error": "overloaded"}, now=10)
    assert a.describe(now=20) == "API error 529 (overloaded) · retry 2 of 10 in 4:50 · 0:20"
    assert a.stall(now=200, limit=120) is None, "waiting for a retry Claude Code announced is no stall"
    a.on_event({"kind": "thinking_start"}, now=311)
    think(a, 50, now=312)
    assert a.describe(now=313).startswith("Thinking · 0:02 · ~50 tokens"), "the retry starts a fresh count"


# ---------- crash markers

def test_crash_marker_found_for_a_dead_process(tmp_path):
    crashguard.mark_start(tmp_path, pid=111, op="run_python", doc="Wikinger",
                          detail="m = Mesh.Mesh(facets)\nm.write()")
    crashguard.mark_start(tmp_path, pid=222, op="get_tree", doc="X", detail="")
    crashguard.mark_done(tmp_path, pid=222)
    crashes = crashguard.collect_crashes(tmp_path, alive=lambda pid: False)
    assert [c["op"] for c in crashes] == ["run_python"] and crashes[0]["doc"] == "Wikinger"
    assert "Mesh.Mesh" in crashes[0]["detail"]
    assert crashguard.collect_crashes(tmp_path, alive=lambda pid: False) == [], "reported once"
    log = (tmp_path / crashguard.CRASH_LOG).read_text(encoding="utf-8")
    assert "run_python" in log


def test_live_process_marker_is_not_a_crash(tmp_path):
    crashguard.mark_start(tmp_path, pid=333, op="run_python", doc="A", detail="")
    assert crashguard.collect_crashes(tmp_path, alive=lambda pid: True) == []


def test_snapshot_due():
    assert crashguard.snapshot_due(last=None, now=1000, minutes=5)
    assert not crashguard.snapshot_due(last=900, now=1000, minutes=5)
    assert crashguard.snapshot_due(last=600, now=1000, minutes=5)
    assert not crashguard.snapshot_due(last=None, now=1000, minutes=0), "0 switches it off"


# ---------- debug report

def test_redaction():
    token = "0123456789abcdef" * 4
    text = f"key sk-ant-api03-ABCdef1234567890xyz token={token} mail kevin@example.com"
    out = diagnostics.redact(text)
    assert "sk-ant-api03" not in out and "kevin@example.com" not in out and "0123456789abcdef0123" not in out
    assert "[redacted]" in out


def test_crash_report_summary(tmp_path):
    ips = tmp_path / "freecad-2026-11-02-085037.ips"
    body = {"exception": {"type": "EXC_BAD_ACCESS", "signal": "SIGSEGV"}, "faultingThread": 0,
            "usedImages": [{"name": "Mesh.so"}, {"name": "libpython3.11.dylib"}],
            "threads": [{"frames": [{"imageIndex": 0, "symbol": "MeshCore::MeshKernel::AddFacets(...)"},
                                    {"imageIndex": 1, "symbol": "PyEval_EvalCode"}]}]}
    ips.write_text('{"app_name":"freecad"}\n' + json.dumps(body), encoding="utf-8")
    s = diagnostics.crash_summary(str(ips))
    assert "SIGSEGV" in s and "Mesh.so" in s and "AddFacets" in s


def test_report_assembly(tmp_path):
    (tmp_path / "bridge-log.jsonl").write_text('{"op": "run_python"}\n', encoding="utf-8")
    path = diagnostics.write_report(tmp_path, {"Versions": "addon 1.8.0", "Secret": "sk-ant-api03-XYZ1234567890abc"},
                                    logs={"Audit log": str(tmp_path / "bridge-log.jsonl")})
    text = open(path, encoding="utf-8").read()
    assert os.path.dirname(path).endswith("reports")
    assert "## Versions" in text and "addon 1.8.0" in text and "run_python" in text
    assert "sk-ant-api03" not in text


def test_debuglog_accepts_events_with_their_own_kind(tmp_path, monkeypatch):
    from claudebridge.panel import debuglog

    monkeypatch.setattr(debuglog, "folder", lambda: str(tmp_path))
    debuglog.log("event", kind="result", session_id="s1", content="x" * 10)
    line = json.loads(open(debuglog.path_today(), encoding="utf-8").read().splitlines()[-1])
    assert line["log"] == "event" and line["kind"] == "result" and line["session_id"] == "s1"


def test_long_jobs_and_frozen_gui_are_not_stalls():
    a = activity.Activity()
    a.start(now=0)
    a.on_event({"kind": "tool_use", "id": "h", "name": "mcp__plugin_freecad_freecad__run_headless", "input": {}},
               now=1)
    assert a.stall(now=500, limit=90) is None
    b = activity.Activity()
    b.start(now=0)
    b.on_event({"kind": "tool_use", "id": "t", "name": "mcp__plugin_freecad_freecad__run_python", "input": {}},
               now=1)
    b.alive(now=200)                       # the window was frozen by FreeCAD work until now
    assert b.stall(now=210, limit=90) is None


# ---------- whether Claude Code is in a turn

def test_messages_merged_into_one_turn_leave_the_panel_free():
    t = stream.TurnTracker()
    assert not t.busy
    t.sent()
    assert t.busy, "sent and not answered yet"
    t.on_event("init")
    for _ in range(3):                       # sent while Claude works: Claude Code folds them into this turn
        t.sent()
    t.on_event("result")                     # one result answers all four
    assert not t.busy, "the turn is over even though four messages got one result"


def test_a_queued_message_that_gets_its_own_turn_keeps_the_panel_busy():
    t = stream.TurnTracker()
    t.sent()
    t.on_event("init")
    t.sent()
    t.on_event("result")
    t.on_event("init")                       # Claude Code starts the queued message as its own turn
    assert t.busy
    t.on_event("result")
    assert not t.busy
    t.sent()
    t.reset()                                # the process ended
    assert not t.busy
