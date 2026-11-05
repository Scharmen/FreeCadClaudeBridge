"""A crash in freecadcmd says so, and flex_test finds the values that crash."""

import anyio
import pytest

from freecad_bridge import headless
from freecad_bridge.checks import runner


async def _raw(*_a, **_k):
    return {"result": "Doc"}


def _fake(monkeypatch, crash_on):
    async def run(script, _timeout):
        if crash_on(script):
            return {"ok": False, "exit_code": -11, "output": "noise", "killed": None, "timed_out": False,
                    "result": None}
        if "param_names" in script:
            return {"ok": True, "exit_code": 0, "result": ["A", "B"], "output": ""}
        return {"ok": True, "exit_code": 0, "output": "",
                "result": {"params": [], "missing": [],
                           "runs": [{"param": "A", "factor": 1, "ok": True, "changed": True}]}}
    monkeypatch.setattr(headless, "run_headless", run)


def test_a_crash_is_named_and_warns_about_the_open_document(monkeypatch):
    _fake(monkeypatch, lambda s: True)
    async def go():
        await runner.on_copy(_raw, lambda p, t: "x", None, None)
    with pytest.raises(runner.CheckCrash, match="would close FreeCAD"):
        anyio.run(go)


def test_an_ordinary_failure_is_not_called_a_crash(monkeypatch):
    async def run(_s, _t):
        return {"ok": False, "exit_code": 0, "output": "ValueError: x", "killed": None, "timed_out": False,
                "result": None}
    monkeypatch.setattr(headless, "run_headless", run)
    async def go():
        await runner.on_copy(_raw, lambda p, t: "x", None, None)
    with pytest.raises(runner.CheckError) as e:
        anyio.run(go)
    assert not isinstance(e.value, runner.CheckCrash) and "ValueError" in str(e.value)


def test_flex_finds_the_values_that_crash(monkeypatch):
    _fake(monkeypatch, lambda s: "['B']" in s and "[0.8]" in s)
    res = anyio.run(runner.flex_each, _raw, None, [0.8, 1.2], None, None)
    crashes = [r for r in res["runs"] if r.get("crash")]
    assert [(r["param"], r["factor"]) for r in crashes] == [("B", 0.8)]
    summary = runner.flex_summary(res)
    assert not summary["ok"]
    assert any("crashes" in p["cause"] and "open document" in p["fix"] for p in summary["problems"])
