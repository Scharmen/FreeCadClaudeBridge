"""Dynamic memory budget for run_headless: derived from the machine, never a fixed number."""

import os
import sys

import anyio
import pytest

from freecad_bridge import headless, memguard

GB = 1024 ** 3
MB = 1024 ** 2


def test_budget_scales_with_the_machine():
    small, big = memguard.Budget.for_total(8 * GB), memguard.Budget.for_total(64 * GB)
    assert small.reserve == 2 * GB, "at least 2 GB stay free"
    assert big.reserve == pytest.approx(0.15 * 64 * GB)
    assert small.hard_cap == pytest.approx(0.75 * 8 * GB) and big.hard_cap == pytest.approx(0.75 * 64 * GB)


def test_check_kills_only_a_real_contributor_under_pressure():
    b = memguard.Budget.for_total(24 * GB)
    assert b.check(rss=6 * GB, available=8 * GB) is None
    assert "memory" in b.check(rss=6 * GB, available=1 * GB)
    assert b.check(rss=100 * MB, available=1 * GB) is None, "other apps filled memory, not this job"
    assert "limit" in b.check(rss=19 * GB, available=5 * GB)


def test_env_override_sets_the_hard_cap(monkeypatch):
    monkeypatch.setenv(memguard.ENV_MAX_MB, "1500")
    b = memguard.Budget.current()
    assert b.hard_cap == 1500 * MB
    assert "limit" in b.check(rss=1600 * MB, available=10 * GB)


def test_system_figures_are_plausible():
    total, avail = memguard.total_memory(), memguard.available_memory()
    assert total > 512 * MB
    assert avail is not None and 0 < avail <= total
    assert memguard.process_tree_rss(os.getpid()) > 1 * MB
    assert memguard.process_rss(os.getpid()) > 1 * MB


def _fake_freecadcmd(monkeypatch, tmp_path):
    """The wrapper is plain Python, so the current interpreter can stand in for freecadcmd."""
    monkeypatch.setenv("FREECAD_BRIDGE_DIR", str(tmp_path / "bridge"))
    monkeypatch.setattr(headless, "find_freecadcmd", lambda: [sys.executable])


def test_run_headless_normal_job_reports_memory(monkeypatch, tmp_path):
    _fake_freecadcmd(monkeypatch, tmp_path)
    res = anyio.run(headless.run_headless, "import time\ntime.sleep(0.5)\n_result = 2 + 3", 60)
    assert res["ok"] and res["result"] == 5 and res["killed"] is None
    assert res["memory"]["peak_mb"] > 0 and res["memory"]["reserve_mb"] >= 2048


def test_run_headless_kills_a_runaway_job(monkeypatch, tmp_path):
    _fake_freecadcmd(monkeypatch, tmp_path)
    monkeypatch.setenv(memguard.ENV_MAX_MB, "400")
    script = ("import time\nblocks = []\nwhile True:\n"
              "    blocks.append(b'x' * (20 * 1024 * 1024))  # touched pages: 2 GB/s, so RSS grows\n"
              "    time.sleep(0.01)")
    res = anyio.run(headless.run_headless, script, 60)
    assert not res["ok"] and "limit" in res["killed"]
    assert res["memory"]["peak_mb"] < 900, "killed promptly, long before the machine runs out"
