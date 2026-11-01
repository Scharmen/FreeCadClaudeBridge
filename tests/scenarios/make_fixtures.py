"""Build tests/scenarios/fixtures/bracket.step for scenario 6 (run_headless, no GUI needed).

An L-bracket with a 4 mm wide slot in its wall: filleting every vertical edge with 8 mm
must fail at the slot, so the scenario tests finding and repairing a broken fillet.
"""

from pathlib import Path

import anyio

from freecad_bridge import headless

OUT = Path(__file__).resolve().parent / "fixtures" / "bracket.step"
SCRIPT = f"""
import Part, FreeCAD as App
base = Part.makeBox(80, 40, 8)
wall = Part.makeBox(8, 40, 50)
slot = Part.makeBox(8, 4, 20, App.Vector(0, 18, 30))
shape = base.fuse(wall).cut(slot).removeSplitter()
assert shape.isValid()
shape.exportStep({str(OUT)!r})
_result = {{"volume": shape.Volume, "faces": len(shape.Faces)}}
"""

if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    print(anyio.run(headless.run_headless, SCRIPT, 120))
