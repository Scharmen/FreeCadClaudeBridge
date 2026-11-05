"""Run the design benchmark: every task through Claude Code against a separate FreeCAD, then score it.

    cd tests && uv run --project ../plugin/server python -m design.run --models opus,sonnet,haiku [--tasks a,b]
    cd tests && uv run --project ../plugin/server python -m design.run --rescore design/runs/<stamp>

The benchmark FreeCAD is a second instance with its own bridge directory (token, learned preferences,
settings) and its own copy of the user's FreeCAD settings, so the user's open documents, preferences
and feedback are never touched. Claude Code gets the same command line and system prompt as the chat
panel's one-shot mode, without the user's learned preferences. Results go to tests/design/runs/.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from freecad_bridge import platforms
from freecad_bridge.client import BridgeError, call, list_instances

from . import score, tasks

HERE = Path(__file__).resolve().parent
ADDON = HERE.parents[1] / "plugin" / "addon" / "ClaudeBridge"
if str(ADDON) not in sys.path:
    sys.path.insert(0, str(ADDON))

from claudebridge.panel.prompts import SYSTEM_PROMPT  # noqa: E402
from claudebridge.panel.stream import FREECAD_TOOL_PREFIX, build_args  # noqa: E402

RUNS = HERE / "runs"
BASELINE = HERE / "baseline.json"
USER_BRIDGE = platforms.bridge_dir()
BENCH_HOME = USER_BRIDGE / "benchmark"
BENCH_BRIDGE = BENCH_HOME / "bridge"
TIMEOUT_S = 25 * 60
VIEWS = ("Isometric", "Front", "Right", "Top", "Bottom", "Rear")
CHECK_TOOLS = ("check_design", "check_print", "check_fit", "flex_test", "inspect_views")


# ---------- statistics from Claude Code's stream-json

def stream_stats(lines: Iterable[str]) -> dict[str, Any]:
    tools: list[str] = []
    names: dict[str, str] = {}
    errors: list[str] = []
    shots = 0
    max_chars = 0
    out: dict[str, Any] = {"model": None, "answer": "", "ok": False, "seconds": None, "turns": None, "api_error": None,
                           "cost_usd": None, "output_tokens": None, "input_tokens": None}
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        kind = ev.get("type")
        if kind == "system" and ev.get("subtype") == "init":
            out["model"] = ev.get("model")
        elif kind == "assistant":
            for block in ev.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    name = block["name"].removeprefix(FREECAD_TOOL_PREFIX)
                    names[block["id"]] = name
                    tools.append(name)
                    inp = block.get("input") or {}
                    if name == "view" and inp.get("screenshot") or name == "compare_to_picture":
                        shots += 1
        elif kind == "user":
            content = ev.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if block.get("type") != "tool_result":
                    continue
                if block.get("is_error"):
                    errors.append(names.get(block.get("tool_use_id"), "?"))
                body = block.get("content")
                text = body if isinstance(body, str) else "".join(
                    b.get("text", "") for b in body or [] if isinstance(b, dict))
                max_chars = max(max_chars, len(text))
        elif kind == "result":
            usage = ev.get("usage") or {}
            out.update(ok=ev.get("subtype") == "success" and not ev.get("is_error"),
                       seconds=round((ev.get("duration_ms") or 0) / 1000), turns=ev.get("num_turns"),
                       cost_usd=ev.get("total_cost_usd"), output_tokens=usage.get("output_tokens"),
                       input_tokens=sum(usage.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens",
                                                                      "cache_creation_input_tokens")),
                       answer=(ev.get("result") or "")[:4000], api_error=ev.get("api_error"))
    out.update(tools=tools, tool_errors=errors, screenshots=shots, max_result_chars=max_chars)
    return out


# ---------- the benchmark FreeCAD

def use_bench_bridge() -> None:
    os.environ["FREECAD_BRIDGE_DIR"] = str(BENCH_BRIDGE)


def prepare_bridge() -> None:
    BENCH_BRIDGE.mkdir(parents=True, exist_ok=True)
    cfg = USER_BRIDGE / "config.json"
    if cfg.exists():
        shutil.copy2(cfg, BENCH_BRIDGE / "config.json")   # FreeCAD and Claude Code paths, nothing personal
    (BENCH_BRIDGE / "settings.json").write_text(json.dumps({"learning.ask_for_feedback": 0}), encoding="utf-8")


def freecad_gui(cmd: list[str]) -> str:
    exe = Path(cmd[0])
    for name in ("freecad", "FreeCAD", "FreeCAD.exe", "freecad.exe"):
        if (exe.parent / name).exists():
            return str(exe.parent / name)
    raise RuntimeError(f"no FreeCAD GUI next to {exe}")


def start_freecad(fc_cmd: list[str], own: str | None = None) -> int:
    """The benchmark FreeCAD: the running one, or with `own` a new one just for this worker."""
    use_bench_bridge()
    running = list_instances()
    if running and not own:
        return running[0].pid
    user_cfg = BENCH_HOME / "user.cfg"
    if not user_cfg.exists():
        cfg_dir = score.run_freecad("import FreeCAD as App\n_result = App.getUserConfigDir()", fc_cmd)
        shutil.copy2(Path(cfg_dir) / "user.cfg", user_cfg)
    if own:      # FreeCAD writes its settings on exit: every instance gets its own file
        shutil.copy2(user_cfg, BENCH_HOME / f"user-{own}.cfg")
        user_cfg = BENCH_HOME / f"user-{own}.cfg"
    env = platforms.clean_env()
    env["FREECAD_BRIDGE_DIR"] = str(BENCH_BRIDGE)
    proc = subprocess.Popen([freecad_gui(fc_cmd), "-u", str(user_cfg)], env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(120):
        time.sleep(1)
        if any(i.pid == proc.pid for i in list_instances()):
            time.sleep(2)   # let the GUI settle
            return proc.pid
    raise RuntimeError("the benchmark FreeCAD did not start")


def py(code: str, pid: int, timeout: float = 120) -> Any:
    return call("run_python", {"code": code}, timeout_s=timeout, instance=pid).result["result"]


def close_all(pid: int) -> None:
    for d in call("status", instance=pid).result.get("documents", []):
        call("document", {"action": "close", "name": d["name"], "force": True}, instance=pid)


PICK_DOC = """
best = None
for d in App.listDocuments().values():
    n = sum(1 for o in d.Objects if getattr(o, 'Visibility', False) and (
        (hasattr(o, 'Shape') and o.Shape.Solids) or o.TypeId.startswith('Mesh::')))
    if best is None or n > best[1]:
        best = (d.Name, n)
_result = best
"""

SAVE_DOC = """
d = App.getDocument({doc!r})
d.saveAs({path!r})
"""

OPEN_DOC = """
d = App.openDocument({path!r})
"""

SHOOT = """
import os, time
App.setActiveDocument(d.Name)
gd = Gui.getDocument(d.Name)
v = gd.ActiveView
shots = []
if v.isAnimationEnabled():   # a camera still gliding to the new view ends up off centre, the picture cropped
    v.setAnimationEnabled(False)
def settle():   # a new view draws only after a few event loop rounds; otherwise the image is blank
    for _ in range(10):
        Gui.updateGui()
        time.sleep(0.05)
settle()
for name in {views!r}:
    getattr(v, 'view' + name)()
    v.fitAll()
    settle()
    p = os.path.join({out!r}, name.lower() + '.png')
    v.saveImage(p, 900, 700, 'White')
    shots.append(p)
_result = shots
"""


# ---------- one task with one model

def claude_path() -> str:
    cfg = platforms.load_config()
    path = cfg.get("claude_path") or shutil.which("claude")
    if not path:
        raise RuntimeError("Claude Code not found")
    return path


def run_claude(prompt: str, model: str, pid: int, work: Path, log: Path, no_checks: bool = False) -> dict[str, Any]:
    args = [claude_path(), *build_args(prompt), "--model", model, "--append-system-prompt", SYSTEM_PROMPT]
    if no_checks:     # a "before" measurement: as if the check tools did not exist
        args += ["--disallowedTools", ",".join(FREECAD_TOOL_PREFIX + t for t in CHECK_TOOLS)]
    env = platforms.clean_env()
    env.pop("ANTHROPIC_API_KEY", None)   # the subscription login, as in the panel's default mode
    env["FREECAD_BRIDGE_DIR"] = str(BENCH_BRIDGE)
    env["FREECAD_BRIDGE_INSTANCE"] = str(pid)
    work.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    proc = subprocess.Popen(args, cwd=work, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", start_new_session=True)

    def read() -> None:
        with log.open("w", encoding="utf-8") as f:
            for line in proc.stdout:  # type: ignore[union-attr]
                lines.append(line)
                f.write(line)
                f.flush()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    timed_out = False
    try:
        proc.wait(timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(proc.pid, signal.SIGINT)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
    reader.join(timeout=10)
    stats = stream_stats(lines)
    stats["timed_out"] = timed_out
    return stats


def judge(task: dict[str, Any], shots: list[str], work: Path, model: str = "opus") -> dict[str, Any]:
    """A second Claude looks at the screenshots and ticks off the features a reviewer expects."""
    feats = "\n".join(f"- {f}" for f in task["look"])
    prompt = (f"You review a CAD model made for this request:\n\n{task['prompt']}\n\nRead these pictures of "
              f"the finished model: {', '.join(shots)}\n\nThe file names say what each shows: outside views from "
              "six sides, and section_x, _y and _z, which cut through the middle with the material filled, so "
              "you can see inside closed parts: cavities, walls, internal threads, clips.\n\nFor each feature "
              "below, decide whether it is clearly present and looks right. Be strict: a feature that is missing, "
              "wrong "
              f"or only hinted at is false.\n{feats}\n\nAnswer with JSON only: "
              '{"features": [{"feature": "...", "present": true, "note": "..."}], "overall": "one sentence"}')
    env = platforms.clean_env()
    env.pop("ANTHROPIC_API_KEY", None)
    env["FREECAD_BRIDGE_DIR"] = str(BENCH_BRIDGE)
    proc = subprocess.run([claude_path(), "-p", prompt, "--model", model, "--output-format", "json",
                           "--allowedTools", "Read"], cwd=work, env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", timeout=600)
    try:
        text = json.loads(proc.stdout).get("result", "")
        data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))  # type: ignore[union-attr]
        items = data["features"]
    except (ValueError, AttributeError, KeyError, TypeError):
        return {"score": None, "error": (proc.stdout + proc.stderr)[-500:]}
    present = sum(1 for i in items if i.get("present") is True)
    return {"score": round(100 * present / max(1, len(task["look"])), 1), "features": items,
            "overall": data.get("overall")}


def run_task(task: dict[str, Any], model: str, pid: int, out: Path, fc_cmd: list[str],
             with_judge: bool, no_checks: bool = False) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    name = "Bench_" + task["id"]
    py(f"App.newDocument({name!r})", pid)
    prompt = f"[FreeCAD] Active document: {name} ({name}). Selection: nothing.\n{task['prompt']}"
    result: dict[str, Any] = {"task": task["id"], "model": model, "prompt": task["prompt"]}
    result["run"] = run_claude(prompt, model, pid, out / "work", out / "stream.jsonl", no_checks)
    try:
        doc, count = py(PICK_DOC, pid)
        path = out / "model.FCStd"
        result["document"] = doc
        result["shots"] = py(SAVE_DOC.format(doc=doc, path=str(path)) + SHOOT.format(views=list(VIEWS), out=str(out)),
                             pid, timeout=300)
        result["shots"] += score.section_shots(path, out, fc_cmd)
        scored = score.score_file(path, task, fc_cmd)
        result["score"], result["failed"], result["checks"] = scored["score"], scored["failed"], scored["checks"]
        (out / "measured.json").write_text(json.dumps(scored["measured"], indent=1), encoding="utf-8")
    except (BridgeError, RuntimeError, TypeError, subprocess.TimeoutExpired) as e:
        result["score"], result["error"] = 0.0, str(e)[-1500:]
    if with_judge and result.get("shots"):
        result["visual"] = judge(task, result["shots"], out / "work")
    try:
        close_all(pid)
    except BridgeError as e:   # it crashed or hangs; the caller starts a new one
        result["freecad_lost"] = str(e)[:300]
    (out / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return result


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_model: dict[str, dict[str, Any]] = {}
    for r in results:
        m = by_model.setdefault(r["model"], {"tasks": {}})
        run = r.get("run", {})
        m["tasks"][r["task"]] = {"score": r.get("score"), "visual": (r.get("visual") or {}).get("score"),
                                 "seconds": run.get("seconds"), "tools": len(run.get("tools", [])),
                                 "tool_errors": len(run.get("tool_errors", [])), "cost_usd": run.get("cost_usd"),
                                 "output_tokens": run.get("output_tokens"), "failed": r.get("failed", [])[:12]}
    for m in by_model.values():
        scores = [t["score"] or 0 for t in m["tasks"].values()]
        visual = [t["visual"] for t in m["tasks"].values() if t["visual"] is not None]
        m["mean_score"] = round(sum(scores) / len(scores), 1) if scores else None
        m["mean_visual"] = round(sum(visual) / len(visual), 1) if visual else None
        m["cost_usd"] = round(sum(t["cost_usd"] or 0 for t in m["tasks"].values()), 2)
    return by_model


def collect(run_dir: Path) -> list[dict[str, Any]]:
    """The results of a run as saved, without the runs that a plan limit stopped."""
    out = []
    for f in sorted(run_dir.glob("*/*/result.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        if r.get("run", {}).get("api_error") != "usage_limit_reached":
            out.append(r)
    return out


def worker_args(model: str, args: argparse.Namespace, run_dir: Path) -> list[str]:
    """The command line of one model's worker in a parallel run: its own FreeCAD, a shared run folder."""
    cmd = [sys.executable, "-m", "design.run", "--models", model, "--tasks", args.tasks, "--run-dir", str(run_dir),
           "--own-freecad"]
    if args.no_judge:
        cmd.append("--no-judge")
    if args.no_checks:
        cmd.append("--no-checks")
    return cmd


def write_summary(results: list[dict[str, Any]], run_dir: Path, baseline: bool) -> None:
    summary = summarize(results)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    if baseline:
        old = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
        old.update({m: {**v, "run": run_dir.name} for m, v in summary.items()})
        BASELINE.write_text(json.dumps(old, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for model, m in summary.items():
        print(f"{model}: mean score {m['mean_score']}, visual {m['mean_visual']}, cost ${m['cost_usd']}")


def rescore(run_dir: Path, fc_cmd: list[str]) -> list[dict[str, Any]]:
    out = []
    for f in sorted(run_dir.glob("*/*/result.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        model = f.parent / "model.FCStd"
        if model.exists():
            scored = score.score_file(model, tasks.get(r["task"]), fc_cmd)
            r["score"], r["failed"], r["checks"] = scored["score"], scored["failed"], scored["checks"]
            f.write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
        out.append(r)
    return out


def rejudge(run_dir: Path, pid: int, fc_cmd: list[str], only: set[str] | None = None) -> list[dict[str, Any]]:
    """Take the pictures of saved models again and judge them anew, so old runs and new compare fairly."""
    jobs = []
    for f in sorted(run_dir.rglob("result.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        model = f.parent / "model.FCStd"
        if not model.exists() or (only and r["task"] not in only):
            continue
        close_all(pid)
        code = OPEN_DOC.format(path=str(model.resolve())) + SHOOT.format(views=list(VIEWS), out=str(f.parent))
        r["shots"] = py(code, pid, timeout=300)
        r["shots"] += score.section_shots(model, f.parent, fc_cmd)
        jobs.append((f, r))
    close_all(pid)

    def one(job: tuple[Path, dict[str, Any]]) -> dict[str, Any]:
        f, r = job
        (f.parent / "work").mkdir(exist_ok=True)
        r.setdefault("visual_four_views", r.get("visual"))
        r["visual"] = judge(tasks.get(r["task"]), r["shots"], f.parent / "work")
        f.write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"{r['model']:7} {r['task']:16} visual {(r['visual_four_views'] or {}).get('score')} -> "
              f"{r['visual'].get('score')}", flush=True)
        return r

    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(one, jobs))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", default="opus")
    ap.add_argument("--tasks", default=",".join(t["id"] for t in tasks.TASKS))
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--no-checks", action="store_true", help="block the check tools (a before measurement)")
    ap.add_argument("--rescore", type=Path)
    ap.add_argument("--rejudge", type=Path, help="new pictures and a new visual judgement of a saved run")
    ap.add_argument("--parallel", action="store_true", help="each model at the same time, in its own FreeCAD")
    ap.add_argument("--run-dir", type=Path, help="write into this folder")
    ap.add_argument("--own-freecad", action="store_true", help="start a FreeCAD just for this run, close it afterwards")
    ap.add_argument("--baseline", action="store_true", help="write the summary to tests/design/baseline.json")
    args = ap.parse_args(argv)
    if args.parallel:     # each model in its own process and FreeCAD: the slowest sets the time
        run_dir = args.run_dir or RUNS / time.strftime("%Y%m%d-%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)
        prepare_bridge()
        workers = [subprocess.Popen(worker_args(m, args, run_dir), cwd=HERE.parent) for m in args.models.split(",")]
        code = max(w.wait() for w in workers)
        write_summary(collect(run_dir), run_dir, args.baseline)
        return code
    fc_cmd = score.freecadcmd()
    if args.rejudge:
        prepare_bridge()
        only = set(args.tasks.split(",")) if args.tasks != ",".join(t["id"] for t in tasks.TASKS) else None
        pid = start_freecad(fc_cmd, "rejudge")
        try:
            rejudge(args.rejudge, pid, fc_cmd, only)
        finally:
            os.kill(pid, signal.SIGTERM)
        return 0
    if args.rescore:
        results = rescore(args.rescore, fc_cmd)
        run_dir = args.rescore
    else:
        prepare_bridge()
        pid = start_freecad(fc_cmd, args.models.replace(",", "-") if args.own_freecad else None)
        run_dir = args.run_dir or RUNS / time.strftime("%Y%m%d-%H%M%S")
        results = []
        try:
            for model in args.models.split(","):
                for tid in args.tasks.split(","):
                    task = tasks.get(tid)
                    t0 = time.time()
                    close_all(pid)
                    r = run_task(task, model, pid, run_dir / model / tid, fc_cmd, not args.no_judge, args.no_checks)
                    if r["run"].get("api_error") == "usage_limit_reached":
                        print(f"{model}: plan usage limit reached at {tid}; stopping. Run the rest later with "
                              f"--tasks.", flush=True)
                        break
                    results.append(r)
                    if r.get("freecad_lost") and args.own_freecad:
                        print(f"{model} {tid}: FreeCAD lost ({r['freecad_lost'][:80]}); starting a new one", flush=True)
                        with contextlib.suppress(OSError):
                            os.kill(pid, signal.SIGKILL)
                        pid = start_freecad(fc_cmd, args.models.replace(",", "-"))
                    vis = (r.get("visual") or {}).get("score")
                    print(f"{model:7} {tid:16} score {r.get('score'):5}  visual {vis}  {time.time() - t0:5.0f} s  "
                          f"tools {len(r['run'].get('tools', []))}  " + "; ".join(r.get("failed", [])[:3]), flush=True)
        finally:
            if args.own_freecad:
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGTERM)
    if not args.own_freecad:      # a worker's parent writes the summary of the whole run
        write_summary(results, run_dir, args.baseline)
    return 0


if __name__ == "__main__":
    sys.exit(main())

