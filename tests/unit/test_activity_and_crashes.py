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
                                                            "estimated_tokens": 1200}}},
               {"type": "system", "subtype": "status", "status": "requesting"})
    kinds = [e["kind"] for e in evs]
    assert "thinking_start" in kinds and {"kind": "status", "status": "requesting"} in evs
    delta = next(e for e in evs if e["kind"] == "thinking_delta")
    assert delta["tokens"] == 1200 and delta["text"] == ""


def test_activity_describes_the_current_step():
    a = activity.Activity()
    a.start(now=100)
    assert a.describe(now=101).startswith("Starting")
    a.on_event({"kind": "thinking_start"}, now=102)
    a.on_event({"kind": "thinking_delta", "text": "", "tokens": 1500}, now=110)
    assert a.describe(now=112) == "Thinking · 0:10 · ~1,500 tokens"   # since thinking began
    a.on_event({"kind": "tool_use", "id": "t1", "name": "mcp__plugin_freecad_freecad__run_python",
                "input": {}}, now=120)
    assert a.describe(now=135) == "Running run_python (15 s) · 0:35"
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


def test_stall_detection_tells_freecad_work_from_silence():
    a = activity.Activity()
    a.start(now=0)
    a.on_event({"kind": "tool_use", "id": "t", "name": "mcp__plugin_freecad_freecad__run_python", "input": {}},
               now=5)
    assert a.stall(now=60, limit=90) is None
    msg = a.stall(now=200, limit=90, freecad_busy=True)
    assert msg and "FreeCAD is still computing" in msg
    msg = a.stall(now=200, limit=90, freecad_busy=False)
    assert msg and "no sign of life" in msg.lower()
    a.on_event({"kind": "permission_request"}, now=210)
    assert a.stall(now=900, limit=90) is None, "waiting for the user is not a stall"


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
    assert "long jobs are normal" in a.stall(now=500, limit=90)
    b = activity.Activity()
    b.start(now=0)
    b.on_event({"kind": "tool_use", "id": "t", "name": "mcp__plugin_freecad_freecad__run_python", "input": {}},
               now=1)
    b.alive(now=200)                       # the window was frozen by FreeCAD work until now
    assert b.stall(now=210, limit=90) is None
