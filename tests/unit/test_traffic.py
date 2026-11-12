"""How many people use the plugin: GitHub's traffic numbers, kept beyond GitHub's 14 days.
Nothing is sent from a user's machine; the numbers come from the repository itself."""

import importlib.util
import inspect
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("traffic", ROOT / "tools" / "traffic.py")
assert spec and spec.loader
traffic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(traffic)


def day(d, count, uniques):
    return {"timestamp": f"2026-11-{d:02d}T00:00:00Z", "count": count, "uniques": uniques}


def api(days):
    return {"count": sum(x["count"] for x in days), "uniques": max((x["uniques"] for x in days), default=0),
            "clones": days, "views": days}


def test_the_script_itself_talks_to_nobody():
    source = inspect.getsource(traffic)
    assert "urllib" not in source and "http.client" not in source and "requests" not in source


def test_days_are_kept_after_github_forgets_them():
    old = traffic.merge({}, api([day(1, 4, 2), day(2, 6, 3)]), api([day(1, 10, 5)]), stars=7, today="2026-11-02")
    new = traffic.merge(old, api([day(2, 9, 4), day(3, 2, 1)]), api([day(3, 8, 6)]), stars=9, today="2026-11-03")
    assert list(new["days"]) == ["2026-11-01", "2026-11-02", "2026-11-03"]
    assert new["days"]["2026-11-01"] == {"clones": 4, "cloners": 2, "views": 10, "visitors": 5}
    assert new["days"]["2026-11-02"]["clones"] == 9, "a day still open yesterday is replaced by its final number"
    assert new["days"]["2026-11-03"] == {"clones": 2, "cloners": 1, "views": 8, "visitors": 6}
    assert new["stars"] == 9


def test_a_day_never_shrinks_when_github_returns_less():
    old = traffic.merge({}, api([day(1, 4, 2)]), api([]), stars=0, today="2026-11-01")
    new = traffic.merge(old, api([day(1, 1, 1)]), api([]), stars=0, today="2026-11-02")
    assert new["days"]["2026-11-01"]["clones"] == 4 and new["days"]["2026-11-01"]["cloners"] == 2


def test_the_fourteen_day_number_is_stored_per_run_because_days_cannot_be_added_up():
    h = traffic.merge({}, {"count": 30, "uniques": 11, "clones": [day(1, 30, 9)]}, api([]), stars=0, today="2026-11-01")
    assert h["windows"]["2026-11-01"] == {"cloners_14d": 11, "visitors_14d": 0}


def test_the_report_names_what_the_numbers_mean():
    h = traffic.merge({}, {"count": 12, "uniques": 5, "clones": [day(1, 4, 2), day(2, 8, 4)]},
                      {"count": 20, "uniques": 9, "views": [day(1, 20, 9)]}, stars=3, today="2026-11-02")
    text = traffic.report(h)
    assert "5" in text and "last 14 days" in text and "Stars: 3" in text
    assert "| 2026-11 | 12 |" in text, "one row per month"
    assert "CI" in text, "the report says that automated clones are in the number"


def test_the_command_updates_the_files(tmp_path):
    (tmp_path / "clones.json").write_text(json.dumps(api([day(1, 4, 2)])))
    (tmp_path / "views.json").write_text(json.dumps(api([day(1, 10, 5)])))
    (tmp_path / "repo.json").write_text(json.dumps({"stargazers_count": 2}))
    out = tmp_path / "stats"
    for _ in range(2):
        traffic.main([str(tmp_path / "clones.json"), str(tmp_path / "views.json"), str(tmp_path / "repo.json"),
                      str(out), "--today", "2026-11-01"])
    assert json.loads((out / "traffic.json").read_text())["days"]["2026-11-01"]["cloners"] == 2
    assert "2026-11" in (out / "README.md").read_text()
