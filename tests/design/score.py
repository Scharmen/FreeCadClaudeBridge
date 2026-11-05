"""Score saved models headless: measure in freecadcmd, evaluate against the task's spec.

    uv run --project plugin/server python -m design.score <task_id> <model.FCStd>   (from tests/)
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from freecad_bridge import headless, imaging, platforms
from freecad_bridge.checks import printing, runner
from freecad_bridge.checks import spec as spec_mod

from . import tasks

TESTS = Path(__file__).resolve().parent.parent
SERVER_SRC = TESTS.parent / "plugin" / "server" / "src"


def freecadcmd() -> list[str]:
    cmd = headless.find_freecadcmd()
    if not cmd:
        raise RuntimeError("freecadcmd not found; start FreeCAD with the addon once or run /freecad:setup")
    return cmd


def run_freecad(code: str, cmd: list[str] | None = None, timeout: float = 900) -> Any:
    """Run `code` in freecadcmd; it sets `_result`. Raises with the output when the script fails."""
    with tempfile.TemporaryDirectory(prefix="fcb-design-") as tmp:
        script = Path(tmp) / "script.py"
        head = f"import sys\nsys.path[:0] = [{str(TESTS)!r}, {str(SERVER_SRC)!r}]\n"
        script.write_text(head + code, encoding="utf-8")
        wrapper = Path(tmp) / "wrapper.py"
        wrapper.write_text(headless.WRAPPER.format(workdir=tmp, script=str(script)), encoding="utf-8")
        proc = subprocess.run([*(cmd or freecadcmd()), str(wrapper)], capture_output=True, text=True,
                              timeout=timeout, env=platforms.clean_env(), stdin=subprocess.DEVNULL, cwd=tmp)
    out = headless.parse_output(proc.stdout + proc.stderr)
    if out["status"] != "ok":
        raise RuntimeError(f"FreeCAD script failed:\n{out['output'][-3000:]}")
    return out["result"]


def measure_file(path: Path, task: dict[str, Any], cmd: list[str] | None = None) -> dict[str, Any]:
    path = Path(path).resolve()   # freecadcmd runs in a temporary folder
    ref = task.get("reference")
    code = ("from design import refs\nfrom freecad_bridge.checks import measure\n"
            f"ref = refs.reference({ref!r}) if {ref!r} else None\n"
            f"_result = measure.measure({str(path)!r}, __import__('json').loads({json.dumps(task['spec'])!r}), ref)\n")
    return run_freecad(code, cmd)


def print_problems(path: Path, process: str, cmd: list[str] | None = None) -> list[str]:
    """What check_print would say about the saved model."""
    with tempfile.TemporaryDirectory(prefix="fcb-print-") as tmp:
        items = run_freecad("from freecad_bridge.checks import measure\n"
                            f"_result = measure.export_meshes({str(Path(path).resolve())!r}, None, {tmp!r})\n", cmd)
        rep = printing.analyze_objects(runner.read_meshes(items), process)
    return [p["cause"] for pr in rep["prints"] for p in pr["problems"]]


def section_shots(path: Path, out: Path, cmd: list[str] | None = None) -> list[str]:
    """Cuts through the middle of the model along each axis, with the material filled."""
    with tempfile.TemporaryDirectory(prefix="fcb-sect-") as tmp:
        items = run_freecad("from freecad_bridge.checks import measure\n"
                            f"_result = measure.export_meshes({str(Path(path).resolve())!r}, None, {tmp!r})\n", cmd)
        meshes = runner.read_meshes(items)
    if not meshes:
        return []
    points = np.vstack([v for _, v, _ in meshes])
    middle = (points.min(0) + points.max(0)) / 2
    shots = []
    for k, axis in enumerate("xyz"):
        cuts = [(label, printing.section_segments(v, t, axis, float(middle[k]))) for label, v, t in meshes]
        shot = Path(out) / f"section_{axis}.png"
        shot.write_bytes(imaging.section_image(cuts, axis, float(middle[k])))
        shots.append(str(shot))
    return shots


def score_file(path: Path, task: dict[str, Any], cmd: list[str] | None = None) -> dict[str, Any]:
    measured = measure_file(path, task, cmd)
    result = spec_mod.evaluate(task["spec"], measured)
    if task.get("printable"):
        problems = print_problems(path, task["printable"], cmd)
        result = spec_mod.add_check(result, "printable", not problems, "; ".join(problems)[:300])
    return {"measured": measured, **result}


def build_solution(task_id: str, out: Path, cmd: list[str] | None = None) -> Path:
    """Save the known-good model of a task (refs.solution) to `out`."""
    name = tasks.get(task_id)["solution"]
    run_freecad("import FreeCAD as App\nfrom design import refs\n"
                f"doc = App.newDocument('Solution')\nrefs.solution({name!r}, doc)\n"
                f"doc.saveAs({str(out)!r})\nApp.closeDocument(doc.Name)\n_result = True\n", cmd)
    return out


if __name__ == "__main__":
    task_id, file = sys.argv[1], Path(sys.argv[2]).resolve()
    r = score_file(file, tasks.get(task_id))
    print(json.dumps({"score": r["score"], "failed": r["failed"]}, indent=2, ensure_ascii=False))
