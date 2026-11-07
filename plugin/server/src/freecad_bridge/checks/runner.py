"""Run a check on a saved copy of the user's document, in a separate freecadcmd.

The user's document stays as it is, no undo step is added, a crash in a heavy boolean cannot take
FreeCAD with it, and the GUI stays usable meanwhile.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import numpy as np

from .. import headless, platforms

MEASURE = Path(__file__).with_name("measure.py")
TIMEOUT_S = 300.0
SAVE_CODE = "_d = doc\n_d.saveCopy({path!r})\n_result = _d.Name\n"
SCRIPT = """\
import importlib.util as _u, sys as _s
_spec = _u.spec_from_file_location("fcb_measure", {measure!r})
measure = _u.module_from_spec(_spec)
_s.modules["fcb_measure"] = measure
_spec.loader.exec_module(measure)
{call}
"""


class CheckError(Exception):
    pass


Raw = Callable[..., Awaitable[Any]]


def script(call: str) -> str:
    return SCRIPT.format(measure=str(MEASURE), call=call)


def clean_output(text: str, limit: int = 1500) -> str:
    """FreeCAD's mesh and document progress lines say nothing."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and "%)" not in ln]
    return "\n".join(lines)[-limit:]


class CheckCrash(CheckError):
    """freecadcmd itself died on the copy, usually inside the geometry kernel."""


CRASH_TEXT = ("FreeCAD's geometry kernel crashed while working on a copy (exit code {code}); your document is safe. "
              "The same operation in the open document would close FreeCAD and lose unsaved work, so do not try "
              "it there. Usual cause: a fillet or chamfer that does not fit. Make it smaller or leave it out.")


async def on_copy(raw: Raw, make_call: Callable[[str, str], str], doc: str | None, instance: int | None,
                  after: Callable[[Any, str], Any] | None = None) -> Any:
    """Save a copy of the document, run `make_call(path, workdir)` code in freecadcmd, return its `_result`."""
    root = Path(platforms.bridge_dir()) / "checks"
    root.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="check-", dir=root)
    try:
        path = os.path.join(tmp, "copy.FCStd")
        await raw("run_python", {"code": SAVE_CODE.format(path=path), "doc": doc, "recompute": False,
                                 "transaction": "Check"}, timeout_s=120, instance=instance)
        out = await headless.run_headless(script(make_call(path, tmp)), TIMEOUT_S)
        code = out.get("exit_code")
        if not out["ok"] and not out.get("killed") and not out.get("timed_out") and code not in (0, 1, None):
            raise CheckCrash(CRASH_TEXT.format(code=code))
        if not out["ok"]:
            reason = out.get("killed") or ("timed out" if out.get("timed_out") else clean_output(out["output"]))
            raise CheckError(f"the check failed in freecadcmd: {reason}")
        return after(out["result"], tmp) if after else out["result"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def read_meshes(items: list[dict[str, Any]]) -> list[tuple[str, np.ndarray, np.ndarray]]:
    out = []
    for it in items:
        pts = np.fromfile(it["base"] + ".pts", dtype="<f8").reshape(-1, 3)
        tris = np.fromfile(it["base"] + ".tris", dtype="<i4").reshape(-1, 3)
        out.append((it["label"], pts, tris))
    return out


def design_summary(spec_result: dict[str, Any], measured: dict[str, Any]) -> dict[str, Any]:
    parts = [{"label": p["label"], "size_mm": p["dims"], "volume_mm3": round(p["volume"], 1), "valid": p["valid"],
              "holes_mm": sorted(h["d"] for h in p["holes"] if not h.get("open")),
              **({"thread": p["thread"]} if p.get("thread") else {}),
              **({"teeth": p["teeth"]} if "teeth" in p else {})} for p in measured.get("parts", [])]
    failed = [{"check": c["name"], "found": c["detail"]} for c in spec_result["checks"] if not c["ok"]]
    return {"ok": not failed, "score": spec_result["score"], "problems": failed,
            "passed": sum(1 for c in spec_result["checks"] if c["ok"]), "matched": spec_result["matched"],
            "parts": parts[:30], "gaps": measured.get("gaps", [])[:20], "tree_errors": measured.get("errors", [])}


def flex_summary(res: dict[str, Any]) -> dict[str, Any]:
    problems = [{"cause": f"parameter '{n}' not found", "fix": "use the name in the VarSet or the Spreadsheet alias"}
                for n in res["missing"]]
    for r in res["runs"]:
        if r.get("crash"):
            problems.append({"cause": f"{r['param']} × {r['factor']:g} crashes FreeCAD's geometry kernel",
                             "fix": "do not try this value in the open document, it would close FreeCAD; make the "
                             "fillets or chamfers smaller, or leave them out"})
        elif not r["ok"]:
            problems.append({"cause": f"{r['param']} × {r['factor']:g} breaks the model: {r.get('error')}",
                             "fix": "drive the failing feature from the parameter too, or attach its sketch to a "
                             "datum plane instead of a face"})
        elif not r["changed"]:
            problems.append({"cause": f"{r['param']} × {r['factor']:g} changes nothing in the model",
                             "fix": f"bind the dimensions to {r['param']} with expressions"})
    if not res["params"]:
        problems.append({"cause": "the document has no parameters (VarSet or Spreadsheet aliases)",
                         "fix": "put the key dimensions in a VarSet and drive the model with expressions"})
    return {"ok": not problems, "params": res["params"], "problems": problems[:20], "runs": len(res["runs"]),
            "note": "tested on a copy; your document is unchanged"}


async def flex_each(raw: Raw, names: list[str] | None, steps: list[float], doc: str | None,
                    instance: int | None) -> dict[str, Any]:
    """After a crash: every parameter and factor in its own process, to find the values that crash."""
    names = list(names or await on_copy(raw, lambda path, _t: f"_result = measure.param_names({path!r})", doc,
                                        instance))
    merged: dict[str, Any] = {"params": names, "missing": [], "runs": []}
    for name in names:
        for step in steps:
            try:
                res = await on_copy(raw, lambda path, _t, n=name, f=step: f"_result = measure.flex({path!r}, {[n]!r}, "
                                    f"{[f]!r})", doc, instance)
            except CheckCrash:
                merged["runs"].append({"param": name, "factor": step, "ok": False, "changed": False, "crash": True})
            else:
                merged["missing"] += [m for m in res["missing"] if m not in merged["missing"]]
                merged["runs"] += res["runs"]
    return merged


def spec_json(spec: dict[str, Any]) -> str:
    return json.dumps(spec)


def all_summary(design: dict[str, Any] | None, prints: dict[str, Any] | None,
                fits: list[dict[str, Any]]) -> dict[str, Any]:
    """One report for check_all: every problem first, each saying which check found it, then the warnings."""
    problems: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if design:
        problems += [{"from": "design", "cause": f"{p['check']}: {p['found']}"} for p in design["problems"]]
        problems += [{"from": "design", "cause": f"tree error: {e}"} for e in design.get("tree_errors", [])]
    for pr in (prints or {}).get("prints", []):
        problems += [{"from": "print", "objects": pr["objects"], **p} for p in pr["problems"]]
        warnings += [{"from": "print", "objects": pr["objects"], **w} for w in pr["warnings"]]
    for f in fits:
        pair = [f.get("a"), f.get("b")]
        if f.get("error"):
            problems.append({"from": "fit", "pair": pair, "cause": f["error"], "fix": "name two solids"})
        elif f.get("problem"):
            problems.append({"from": "fit", "pair": pair, **f["problem"]})
    out: dict[str, Any] = {"ok": not problems, "problems": problems, "warnings": warnings}
    if design:
        out["design"] = {k: design[k] for k in ("score", "passed", "matched", "parts", "gaps") if k in design}
    if prints:
        out["print"] = [{"objects": p["objects"], "ok": p["ok"], **{k: p["values"][k] for k in
                         ("closed", "wall_mm", "best_up", "base_mm2", "stable") if k in p.get("values", {})}}
                        for p in prints["prints"]]
    if fits:
        out["fits"] = [{k: f[k] for k in ("a", "b", "kind", "ok", "gap_mm", "overlap_mm3", "side_gap_mm",
                                          "wanted_gap_mm") if k in f} for f in fits]
    return out
