"""The installer's window: step events from the wizard, the checklist, questions and live program output."""

import queue
import sys

from test_wizard import FREECAD, FakeSystem, run

from freecad_bridge import wizard, wizard_gui


class StepSystem(FakeSystem):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.steps = []

    def step(self, key, state, text, link=None):
        self.steps.append((key, state, link))


def test_the_wizard_reports_each_step_with_its_state():
    s = StepSystem()
    rc, _ = run(s)
    assert rc == 0
    final = {}
    for key, state, _ in s.steps:
        final[key] = state
    assert final == {"freecad": "ok", "desktop": "ok", "claude_code": "ok", "login": "ok", "plugin": "ok",
                     "addon": "ok", "start": "ok"}
    assert ("plugin", "running", None) in s.steps                       # shown while it works


def test_missing_parts_come_with_their_download_link():
    s = StepSystem(desktop=False)
    rc, _ = run(s, installs=())
    assert rc == 1
    assert ("freecad", "missing", wizard.LINKS["freecad"]) in s.steps
    assert ("desktop", "missing", wizard.LINKS["desktop"]) in s.steps
    assert not any(k == "plugin" for k, _, _ in s.steps), "nothing is installed while something is missing"


def test_the_checklist_follows_the_events():
    rows = wizard_gui.Checklist()
    assert [r.key for r in rows.rows] == list(wizard_gui.STEPS)
    assert all(r.state == "waiting" for r in rows.rows)
    rows.apply("freecad", "ok", "FreeCAD 1.1.4")
    rows.apply("desktop", "missing", "Claude Desktop is not installed.", "https://claude.ai/download")
    assert rows.get("freecad").state == "ok" and rows.get("freecad").text == "FreeCAD 1.1.4"
    assert rows.get("desktop").link == "https://claude.ai/download"
    assert rows.missing() == [rows.get("desktop")]
    rows.reset()
    assert rows.get("freecad").state == "waiting" and rows.get("freecad").text == wizard_gui.STEPS["freecad"]


def test_gui_system_sends_events_and_asks_through_the_window():
    q = queue.Queue()
    asked = []
    s = wizard_gui.GuiSystem(q, asker=lambda question, default: asked.append(question) or False)
    s.say("hello")
    s.step("freecad", "ok", "FreeCAD 1.1.4")
    assert s.ask("Start FreeCAD now?", True) is False and asked == ["Start FreeCAD now?"]
    events = [q.get_nowait() for _ in range(q.qsize())]
    assert ("log", "hello") in events and ("step", ("freecad", "ok", "FreeCAD 1.1.4", None)) in events


def test_interactive_programs_stream_their_output_and_take_input():
    q = queue.Queue()
    s = wizard_gui.GuiSystem(q, asker=lambda *a: True)
    s.send_input("my-code")                                     # typed into the window before it is read
    code, out = s.run([sys.executable, "-c", "print('Open https://claude.ai/x'); print(input().upper())"],
                      interactive=True, timeout=30)
    assert code == 0 and "MY-CODE" in out
    events = [q.get_nowait() for _ in range(q.qsize())]
    assert ("log", "Open https://claude.ai/x") in events and ("input", True) in events and ("input", False) in events


def test_a_missing_display_falls_back_to_the_text_wizard(monkeypatch):
    monkeypatch.setattr(wizard_gui, "available", lambda: False)
    ran = {}
    monkeypatch.setattr(wizard, "run_wizard", lambda system, **kw: ran.setdefault("text", type(system)) and 0)
    assert wizard.main(gui=True) == 0 and ran["text"] is wizard.System
    assert FREECAD.version == "1.1.4"
