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

from freecad_bridge import headless, platforms

from . import spec as spec_mod
from . import tasks

TESTS = Path(__file__).resolve().parent.parent


def freecadcmd() -> list[str]:
    cmd = headless.find_freecadcmd()
    if not cmd:
        raise RuntimeError("freecadcmd not found; start FreeCAD with the addon once or run /freecad:setup")
    return cmd


def run_freecad(code: str, cmd: list[str] | None = None, timeout: float = 900) -> Any:
    """Run `code` in freecadcmd; it sets `_result`. Raises with the output when the script fails."""
    with tempfile.TemporaryDirectory(prefix="fcb-design-") as tmp:
        script = Path(tmp) / "script.py"
        script.write_text(f"import sys\nsys.path.insert(0, {str(TESTS)!r})\n" + code, encoding="utf-8")
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
    code = ("from design import measure, refs\n"
            f"ref = refs.reference({ref!r}) if {ref!r} else None\n"
            f"_result = measure.measure({str(path)!r}, __import__('json').loads({json.dumps(task['spec'])!r}), ref)\n")
    return run_freecad(code, cmd)


def score_file(path: Path, task: dict[str, Any], cmd: list[str] | None = None) -> dict[str, Any]:
    measured = measure_file(path, task, cmd)
    return {"measured": measured, **spec_mod.evaluate(task["spec"], measured)}


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
