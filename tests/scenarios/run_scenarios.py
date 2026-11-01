"""Phase 2 gate: the six reference scenarios, end-to-end through the chat panel in FreeCAD.

Usage (FreeCAD with ClaudeBridge running, plugin installed):
    uv run --project plugin/server python tests/scenarios/run_scenarios.py [1 2 ...]

Each scenario is typed into the panel like a user would, then checked through the
bridge. Reports duration, tool calls, largest tool result (gate: < 10,000 tokens,
estimated as chars / 4) and whether a screenshot was used (gate: none).
Writes tests/scenarios/results.json.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from freecad_bridge.client import BridgeError, call

HERE = Path(__file__).resolve().parent
OUT = Path("/tmp/claude-501/scenarios") if Path("/tmp/claude-501").exists() else Path("/tmp/fcb-scenarios")
OUT.mkdir(parents=True, exist_ok=True)
P = "from claudebridge.panel.dock import get_panel, ensure_panel\np = get_panel()\n"

SCENARIOS = {
    1: ("Flange", "Create a new document named Flange. Build a flange that is fully parametric through a VarSet named "
        "Params: outer diameter 80 mm, thickness 8 mm, center bore 20 mm, 6 bolt holes of 6.6 mm on a 60 mm bolt "
        "circle. Every dimension must be driven by the VarSet. Then set the outer diameter to 100 mm and the bolt "
        "circle to 76 mm and confirm the model updated correctly."),
    2: ("Housing", "Create a new document named Housing. Model a box-shaped electronics housing, outside 80 x 60 x 40 mm, "
        "2 mm walls, open at the top, with four screw bosses (outer diameter 7 mm, hole diameter 2.5 mm, full inner "
        "height) in the inner corners. Add a separate lid 80 x 60 x 3 mm with four 3.2 mm through holes aligned with "
        "the bosses, placed 10 mm above the housing."),
    3: ("Profile", "Create a new document named Profile with one fully constrained sketch (0 degrees of freedom) on the "
        "XY plane: a T-profile with flange width 60 mm, flange thickness 8 mm, total height 40 mm and web thickness "
        "6 mm, symmetric about the Y axis, with the flange on top and the bottom of the web on the X axis."),
    4: ("Hinge", "Create a new document named Hinge with an assembly (Assembly workbench, real joints) of three parts: "
        "a base plate 80 x 40 x 5 mm that is grounded, a pin of 6 mm diameter and 30 mm length standing upright in "
        "the center of the plate's top face with a revolute joint, and an arm 60 x 10 x 5 mm lying on top of the pin, "
        "fixed to the pin's top face. Solve the assembly and confirm where each part ended up."),
    5: ("Drawing", "Create a new document named Drawing: a 60 x 40 x 20 mm block with a 10 mm through hole in the "
        "center of the top face. Make a TechDraw page with an ISO A4 landscape template showing front, top and right "
        f"views, add the overall length, width and height dimensions, and export the page as PDF to {OUT}/drawing.pdf."),
    6: ("Bracket", f"Import {HERE}/fixtures/bracket.step into a new document named Bracket. Fillet all vertical edges "
        "of the bracket with 8 mm. If a fillet fails, find out why and repair it so that the result is a valid solid "
        f"with as many 8 mm fillets as possible, then export the result as STL to {OUT}/bracket.stl."),
}


def py(code: str, timeout: float = 60):
    return call("run_python", {"code": code}, timeout_s=timeout).result["result"]


def check(n: int) -> dict:
    """Verify the outcome through the bridge. Returns {"pass": bool, ...details}."""
    if n == 1:
        r = py("d = App.getDocument('Flange')\nvs = [o for o in d.Objects if o.TypeId == 'App::VarSet']\n"
               "exprs = sum(len(o.ExpressionEngine) for o in d.Objects if hasattr(o, 'ExpressionEngine'))\n"
               "solids = [o for o in d.Objects if hasattr(o, 'Shape') and o.Shape.Solids and o.Visibility]\n"
               "s = max(solids, key=lambda o: o.Shape.Volume).Shape if solids else None\n"
               "cyl = [f for f in s.Faces if type(f.Surface).__name__ == 'Cylinder'] if s else []\n"
               "_result = {'varset': [o.Name for o in vs], 'expressions': exprs, 'valid': bool(s and s.isValid()),"
               "'diameter': round(s.BoundBox.XLength, 3) if s else None, 'holes_small': sum(1 for f in cyl if abs(f.Surface.Radius - 3.3) < 1e-3),"
               "'errors': [o.Name for o in d.Objects if 'Invalid' in o.State]}")
        r["pass"] = bool(r["varset"]) and r["expressions"] >= 4 and r["valid"] and r["diameter"] == 100 \
            and r["holes_small"] >= 6 and not r["errors"]
    elif n == 2:
        r = py("d = App.getDocument('Housing')\nsolids = [o for o in d.Objects if hasattr(o, 'Shape') and o.Shape.Solids "
               "and o.Visibility]\n_result = {'solids': [(o.Label, round(o.Shape.Volume), o.Shape.isValid(), "
               "[round(x, 1) for x in (o.Shape.BoundBox.XLength, o.Shape.BoundBox.YLength, o.Shape.BoundBox.ZLength)]) "
               "for o in solids], 'errors': [o.Name for o in d.Objects if 'Invalid' in o.State]}")
        sizes = [tuple(s[3]) for s in r["solids"] if s[2]]
        r["pass"] = (80.0, 60.0, 40.0) in sizes and (80.0, 60.0, 3.0) in sizes and not r["errors"]
    elif n == 3:
        r = py("d = App.getDocument('Profile')\nsk = [o for o in d.Objects if o.TypeId == 'Sketcher::SketchObject']\n"
               "s = sk[0]\ns.solve()\nw = s.Shape.Wires\nf = Part.Face(w[0]) if w and w[0].isClosed() else None\n"
               "_result = {'sketches': len(sk), 'dof': s.DoF, 'closed': bool(f), 'area': round(f.Area, 3) if f else None,"
               "'bbox': [round(x, 3) for x in (s.Shape.BoundBox.XMin, s.Shape.BoundBox.XMax, s.Shape.BoundBox.YMin, s.Shape.BoundBox.YMax)]}")
        r["pass"] = r["dof"] == 0 and r["closed"] and r["area"] == 672.0 and r["bbox"] == [-30, 30, 0, 40]
    elif n == 4:
        r = py("d = App.getDocument('Hinge')\nasm = [o for o in d.Objects if o.TypeId == 'Assembly::AssemblyObject']\n"
               "a = asm[0] if asm else None\njg = [o for o in d.Objects if o.TypeId == 'Assembly::JointGroup']\n"
               "joints = [(o.Label, getattr(o, 'JointType', 'Grounded')) for o in (jg[0].Group if jg else [])]\n"
               "_result = {'assembly': bool(a), 'joints': joints, 'solve': a.solve() if a else None,"
               "'errors': [o.Name for o in d.Objects if 'Invalid' in o.State]}")
        types = [t for _, t in r["joints"]]
        r["pass"] = r["assembly"] and r["solve"] == 0 and "Revolute" in types and "Fixed" in types \
            and len(types) >= 3 and not r["errors"]
    elif n == 5:
        path = OUT / "drawing.pdf"
        r = py("d = App.getDocument('Drawing')\n_result = {'pages': [o.Name for o in d.Objects if o.TypeId == "
               "'TechDraw::DrawPage'], 'views': len([o for o in d.Objects if o.TypeId.startswith('TechDraw::DrawViewPart') "
               "or o.TypeId == 'TechDraw::DrawProjGroupItem']), 'dims': len([o for o in d.Objects if o.TypeId == "
               "'TechDraw::DrawViewDimension'])}")
        r["pdf_bytes"] = path.stat().st_size if path.exists() else 0
        r["pass"] = r["pdf_bytes"] > 1000 and r["views"] >= 3 and r["dims"] >= 3
    else:
        path = OUT / "bracket.stl"
        r = py("d = App.getDocument('Bracket')\nvis = [o for o in d.Objects if hasattr(o, 'Shape') and o.Shape.Solids "
               "and o.Visibility]\nbest = vis[-1] if vis else None\nfil = [o for o in d.Objects if 'Fillet' in o.TypeId]\n"
               "_result = {'visible_solids': [(o.Name, o.TypeId, o.Shape.isValid()) for o in vis], 'fillets': [(o.Name, "
               "list(o.State)) for o in fil], 'errors': [o.Name for o in vis if 'Invalid' in o.State]}")
        r["stl_bytes"] = path.stat().st_size if path.exists() else 0
        r["pass"] = r["stl_bytes"] > 1000 and any(v[2] for v in r["visible_solids"]) and bool(r["fillets"]) \
            and not r["errors"]
    return r


def run(n: int) -> dict:
    doc, prompt = SCENARIOS[n]
    for name in [d["name"] for d in call("status").result.get("documents", [])]:
        call("document", {"action": "close", "name": name, "force": True})
    py(P + "ensure_panel(True)\np.new_conversation()")
    py(P + f"p.input.setPlainText({prompt!r})\np.send()")
    t0 = time.time()
    while time.time() - t0 < 900:
        time.sleep(3)
        try:
            if not py(P + "_result = bool(p.run and p.run.running())", 10):
                break
        except BridgeError:
            pass  # GUI busy with a tool call; try again
    dt = time.time() - t0
    info = py(P + "_result = {'max_chars': p.max_result_chars, 'transcript': p.transcript()}")
    turn = info["transcript"].split("You: ")[-1]
    tools = re.findall(r"▸ (\S+)", turn)
    failed = re.findall(r"✗ (\S+)", turn)
    result = {"scenario": n, "name": doc, "seconds": round(dt), "tools": tools, "tool_errors": failed,
              "max_result_tokens_est": info["max_chars"] // 4,
              "screenshot_used": "screenshot=True" in turn or "screenshot=true" in turn,
              "answer": turn.split("Claude:")[-1].strip()[:600]}
    try:
        result["check"] = check(n)
    except BridgeError as e:
        result["check"] = {"pass": False, "error": str(e)}
    result["pass"] = bool(result["check"].get("pass")) and result["max_result_tokens_est"] < 10000 \
        and not result["screenshot_used"]
    return result


if __name__ == "__main__":
    which = [int(a) for a in sys.argv[1:]] or list(SCENARIOS)
    results_path = HERE / "results.json"
    results = json.loads(results_path.read_text()) if results_path.exists() else {}
    for n in which:
        r = run(n)
        results[str(n)] = r
        results_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print(json.dumps({k: r[k] for k in ("scenario", "name", "pass", "seconds", "max_result_tokens_est",
                                             "screenshot_used", "tool_errors")}), flush=True)
        print("   tools:", " ".join(r["tools"]), flush=True)
        print("   check:", json.dumps(r["check"])[:400], flush=True)
    os._exit(0)
