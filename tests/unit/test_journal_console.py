
from claudebridge.bridge.console import ConsoleBuffer
from claudebridge.bridge.journal import Journal
from freecad_bridge.headless import parse_output


def test_changes_aggregate_per_object_and_source():
    j = Journal()
    for _ in range(300):  # dragging a placement
        j.add("changed", doc="D", object="Box", label="Box", prop="Placement")
    j.add("changed", doc="D", object="Box", label="Box", prop="Length")
    j.claude_active = True
    j.add("changed", doc="D", object="Box", label="Box", prop="Height")
    events = j.since()["events"]
    assert len(events) == 2
    assert events[0]["source"] == "user" and events[0]["props"] == ["Placement", "Length"]
    assert events[0]["count"] == 301
    assert events[1]["source"] == "claude" and events[1]["props"] == ["Height"]


def test_other_object_breaks_merge():
    j = Journal()
    j.add("changed", doc="D", object="A", prop="X")
    j.add("changed", doc="D", object="B", prop="X")
    j.add("changed", doc="D", object="A", prop="Y")
    assert [e["object"] for e in j.since()["events"]] == ["A", "B", "A"]


def test_merge_window(monkeypatch):
    j = Journal()
    j.add("changed", doc="D", object="A", prop="X")
    j._entries[-1]["last_time"] -= 10
    j.add("changed", doc="D", object="A", prop="X")
    assert len(j.since()["events"]) == 2


def test_since_and_source_filter():
    j = Journal()
    j.add("created", doc="D", object="A")
    j.claude_active = True
    j.add("created", doc="D", object="B")
    j.claude_active = False
    j.add("selected", doc="D", object="B")
    first = j.since()["latest_seq"]
    assert [e["object"] for e in j.since(source="user")["events"]] == ["A", "B"]
    assert [e["object"] for e in j.since(source="claude")["events"]] == ["B"]
    assert j.since(first)["events"] == []
    j.add("deleted", doc="D", object="A")
    assert [e["event"] for e in j.since(first)["events"]] == ["deleted"]


def test_ring_buffer_reports_gap_and_limit():
    j = Journal(maxlen=5)
    for i in range(10):
        j.add("created", doc="D", object=f"O{i}")
    out = j.since(1)
    assert out["gap"] is True and len(out["events"]) == 5
    assert j.since(0, limit=2)["truncated"] is True


def test_console_levels_and_since():
    c = ConsoleBuffer()
    c.add("loaded module\n", "log")
    c.add("hello", "message")
    c.add("careful", "warning")
    c.add("boom", "error")
    c.add("   ", "message")
    assert [ln["text"] for ln in c.since()["lines"]] == ["hello", "careful", "boom"]
    assert [ln["text"] for ln in c.since(level="warning")["lines"]] == ["careful", "boom"]
    assert len(c.since(level="log")["lines"]) == 4
    assert c.since(3)["lines"][0]["text"] == "boom"
    assert c.since()["latest_seq"] == 4


def test_headless_output_parsing():
    stdout = (
        "computing\nFCB_STATUS=ok\nFCB_RESULT={\"volume\": 1000.0}\n"
        "FreeCAD 1.1.4, Libs: 1.1.4R20260928 (Git shallow)\n(C) 2001-2026 FreeCAD contributors\n"
        "FreeCAD is free and open-source software licensed under the terms of LGPL2+ license.\n"
    )
    out = parse_output(stdout)
    assert out == {"status": "ok", "result": {"volume": 1000.0}, "output": "computing"}


def test_headless_error_status():
    out = parse_output("Traceback (most recent call last):\nValueError: x\nFCB_STATUS=error\nFCB_RESULT=null\n")
    assert out["status"] == "error" and "ValueError" in out["output"] and out["result"] is None




def test_headless_strips_occt_transfer_noise():
    noisy = ("\x1b[32;1m\n*******************************************************************\n"
             "******        Statistics on Transfer (Write)                 ******\x1b[0m\n"
             "keep me\nFCB_STATUS=ok\nFCB_RESULT=1\n")
    assert parse_output(noisy)["output"] == "keep me"
