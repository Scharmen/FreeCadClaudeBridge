"""Keep GitHub's traffic numbers of this repository beyond the 14 days GitHub stores.

The daily workflow `.github/workflows/traffic.yml` fetches the numbers with `gh api` and calls this
script, which merges them into `traffic.json` and writes a readable `README.md` next to it (branch `stats`).
Nothing comes from a user's machine: every install and update of the plugin clones the repository, and
GitHub counts that. This script reads and writes files only.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
from typing import Any

PAIRS = (("clones", "clones", "cloners"), ("views", "views", "visitors"))


def merge(history: dict[str, Any], clones: dict[str, Any], views: dict[str, Any], stars: int,
          today: str) -> dict[str, Any]:
    """History plus one answer of the traffic API. A day keeps its highest numbers."""
    days: dict[str, dict[str, int]] = {k: dict(v) for k, v in (history.get("days") or {}).items()}
    for data, (_, total, unique) in zip((clones, views), PAIRS, strict=True):
        for entry in data.get(total) or []:
            row = days.setdefault(str(entry["timestamp"])[:10], {})
            row[total] = max(row.get(total, 0), int(entry["count"]))
            row[unique] = max(row.get(unique, 0), int(entry["uniques"]))
    for row in days.values():
        for _, total, unique in PAIRS:
            row.setdefault(total, 0)
            row.setdefault(unique, 0)
    windows = dict(history.get("windows") or {})
    windows[today] = {"cloners_14d": int(clones.get("uniques") or 0), "visitors_14d": int(views.get("uniques") or 0)}
    return {"days": dict(sorted(days.items())), "windows": dict(sorted(windows.items())), "stars": int(stars),
            "updated": today}


def report(history: dict[str, Any]) -> str:
    days: dict[str, dict[str, int]] = history.get("days") or {}
    windows: dict[str, dict[str, int]] = history.get("windows") or {}
    last = windows[max(windows)] if windows else {"cloners_14d": 0, "visitors_14d": 0}
    months: dict[str, dict[str, int]] = {}
    for date, row in days.items():
        m = months.setdefault(date[:7], {"clones": 0, "cloners": 0, "views": 0, "visitors": 0, "peak": 0})
        for key in ("clones", "cloners", "views", "visitors"):
            m[key] += row[key]
    for date, w in windows.items():
        if date[:7] in months:
            months[date[:7]]["peak"] = max(months[date[:7]]["peak"], w["cloners_14d"])
    lines = [
        "# How many people use FreeCadClaudeBridge",
        "",
        f"Updated {history.get('updated', '?')} by the `Traffic` workflow from GitHub's own numbers.",
        "",
        f"- **Different cloners in the last 14 days: {last['cloners_14d']}** (the closest number to active users: "
        "every install and every update clones the repository)",
        f"- Different visitors of the repository page in the last 14 days: {last['visitors_14d']}",
        f"- Stars: {history.get('stars', 0)}",
        "",
        "| Month | Clones | Cloners, days added up | Most cloners in 14 days | Page views | Visitors, days added up |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for month, m in sorted(months.items(), reverse=True):
        lines.append(f"| {month} | {m['clones']} | {m['cloners']} | {m['peak']} | {m['views']} | {m['visitors']} |")
    lines += [
        "",
        "What the numbers are not: GitHub counts a cloner per day and per 14 days, so someone who updates on "
        "two days is in two days; the added-up columns are an upper bound. Automated clones are in the number "
        "too: each CI run of this repository and this workflow itself clone it. Installs through "
        "`install.sh` fetch the repository the same way and are counted; nobody is identified.",
        "",
    ]
    return "\n".join(lines)


def _load(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("clones")
    p.add_argument("views")
    p.add_argument("repo")
    p.add_argument("out", help="folder holding traffic.json and README.md")
    p.add_argument("--today", default=datetime.datetime.now(datetime.UTC).date().isoformat())
    a = p.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    store = os.path.join(a.out, "traffic.json")
    history = _load(store) if os.path.exists(store) else {}
    history = merge(history, _load(a.clones), _load(a.views), int(_load(a.repo).get("stargazers_count") or 0), a.today)
    with open(store, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=1)
        f.write("\n")
    with open(os.path.join(a.out, "README.md"), "w", encoding="utf-8") as f:
        f.write(report(history))


if __name__ == "__main__":
    main()
