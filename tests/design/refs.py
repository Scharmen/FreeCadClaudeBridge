"""Reference and known-good models for the benchmark, built with FreeCAD (runs in freecadcmd or the GUI).

`reference(name)` returns a Part shape to compare against (IoU); `solution(name, doc)` builds a
complete model in a document the way a good answer would, so the spec can be validated.
"""

from __future__ import annotations

import math
from typing import Any

import FreeCAD as App
import Part

V = App.Vector


def _cyl(r: float, h: float, x: float = 0, y: float = 0, z: float = 0) -> Any:
    return Part.makeCylinder(r, h, V(x, y, z))


def bearing_housing() -> Any:
    flange = Part.makeBox(50, 50, 6, V(-25, -25, 0))
    boss = _cyl(16, 12)
    body = flange.fuse(boss).removeSplitter()
    top = [e for e in body.Edges if abs(e.BoundBox.ZMin - 12) < 1e-6 and e.BoundBox.XLength > 30]
    corners = [e for e in body.Edges if e.BoundBox.XLength < 1e-6 and e.BoundBox.YLength < 1e-6
               and e.BoundBox.ZLength > 1]
    edges = top + corners
    body = body.makeFillet(1.0, edges)
    cut = _cyl(11, 7.01, z=5).fuse(_cyl(6, 13, z=-0.5))
    for sx in (-19, 19):
        for sy in (-19, 19):
            cut = cut.fuse(_cyl(2.25, 7, sx, sy, -0.5))
    return body.cut(cut).removeSplitter()


def organizer() -> Any:
    block = Part.makeBox(100, 40, 30)
    top = [e for e in block.Edges if abs(e.BoundBox.ZMin - 30) < 1e-6]
    block = block.makeChamfer(2.0, top)
    cut = None
    for i in range(3):
        c = _cyl(7, 26, 14 + i * 18, 20, 5)
        cut = c if cut is None else cut.fuse(c)
    for i in range(4):
        cut = cut.fuse(Part.makeBox(3, 32, 21, V(66 + i * 8, 4, 10)))
    return block.cut(cut).removeSplitter()


def gear_profile(module: float, teeth: int, pressure_deg: float = 20.0) -> Any:
    """A closed involute gear outline in the XY plane (no undercut handling; fine for z >= 17)."""
    m, z, a = module, teeth, math.radians(pressure_deg)
    rp, rb = m * z / 2, m * z / 2 * math.cos(a)
    ra, rf = rp + m, rp - 1.25 * m
    inv = math.tan(a) - a
    half = math.pi / (2 * z) + inv  # half tooth angle at the base circle

    def inv_pt(r: float, sign: float, base: float) -> V:
        t = math.sqrt(max(0.0, (r / rb) ** 2 - 1))
        ang = t - math.atan(t)
        th = base + sign * (half - ang)
        return V(r * math.cos(th), r * math.sin(th), 0)

    pts: list[V] = []
    for k in range(z):
        base = 2 * math.pi * k / z
        r0 = max(rb, rf)
        rs = [r0 + (ra - r0) * i / 8 for i in range(9)]
        left = [inv_pt(r, -1, base) for r in rs]
        right = [inv_pt(r, 1, base) for r in reversed(rs)]
        if rf < rb:
            th = base - half
            pts.append(V(rf * math.cos(th), rf * math.sin(th), 0))
        pts += left + right
        if rf < rb:
            th = base + half
            pts.append(V(rf * math.cos(th), rf * math.sin(th), 0))
    pts.append(pts[0])
    return Part.Face(Part.makePolygon(pts))


def gear(module: float, teeth: int, width: float, bore: float) -> Any:
    g = gear_profile(module, teeth).extrude(V(0, 0, width))
    return g.cut(_cyl(bore / 2, width + 2, z=-1))


def spring() -> Any:
    d, od, length, coils = 1.5, 20.0, 40.0, 10
    rm = (od - d) / 2
    pitch = (length + d) / coils
    helix = Part.makeHelix(pitch, pitch * coils, rm)
    circle = Part.Wire(Part.makeCircle(d / 2, V(rm, 0, 0), V(0, 1, 0)))
    coil = Part.Wire(helix).makePipeShell([circle], True, True)
    cut = Part.makeBox(40, 40, 10, V(-20, -20, -10 + d / 2)).fuse(Part.makeBox(40, 40, 10, V(-20, -20, length + d / 2)))
    s = coil.cut(cut)
    s.translate(V(0, 0, -d / 2))
    return s


REFERENCES = {"bearing_housing": bearing_housing, "organizer": organizer}


def reference(name: str) -> Any:
    return REFERENCES[name]()


def _feature(doc: Any, label: str, shape: Any) -> Any:
    o = doc.addObject("Part::Feature", label)
    o.Shape = shape
    return o


def _shelf_bracket(doc: Any) -> None:
    vs = doc.addObject("App::VarSet", "Params")
    for name, value in (("VerticalLength", 100), ("HorizontalLength", 150), ("Width", 30), ("Thickness", 5),
                        ("HoleDiameter", 5)):
        vs.addProperty("App::PropertyLength", name, "Bracket")
        setattr(vs, name, value)
    vert = doc.addObject("Part::Box", "VerticalLeg")
    vert.setExpression("Length", "Params.Thickness")
    vert.setExpression("Width", "Params.Width")
    vert.setExpression("Height", "Params.VerticalLength")
    hor = doc.addObject("Part::Box", "HorizontalLeg")
    hor.setExpression("Length", "Params.HorizontalLength")
    hor.setExpression("Width", "Params.Width")
    hor.setExpression("Height", "Params.Thickness")
    brace = doc.addObject("Part::Wedge", "Brace")   # right triangle in XZ, extruded along Y
    brace.setExpression("Xmax", "Params.HorizontalLength * 0.5")
    brace.setExpression("Ymin", "Params.Width / 2 - Params.Thickness / 2")
    brace.setExpression("Ymax", "Params.Width / 2 + Params.Thickness / 2")
    brace.setExpression("Zmax", "Params.VerticalLength * 0.5")
    brace.X2min = brace.X2max = 0
    brace.setExpression("Z2max", "Params.VerticalLength * 0.5")
    brace.Z2min = 0
    brace.Xmin, brace.Zmin = 0, 0
    fuse = doc.addObject("Part::MultiFuse", "Legs")
    fuse.Shapes = [vert, hor, brace]
    holes = []
    for i, (axis, frac) in enumerate((("x", 0.25), ("x", 0.8), ("z", 0.25), ("z", 0.8))):
        c = doc.addObject("Part::Cylinder", f"Hole{i}")
        c.setExpression("Radius", "Params.HoleDiameter / 2")
        c.Height = 20
        if axis == "x":   # vertical holes through the horizontal leg
            c.Placement.Base = V(0, 0, -5)
            c.setExpression(".Placement.Base.x", f"Params.HorizontalLength * {frac}")
            c.setExpression(".Placement.Base.y", "Params.Width / 2")
        else:             # horizontal holes through the vertical leg
            c.Placement = App.Placement(V(-5, 0, 0), App.Rotation(V(0, 1, 0), 90))
            c.setExpression(".Placement.Base.y", "Params.Width / 2")
            c.setExpression(".Placement.Base.z", f"Params.VerticalLength * {frac}")
        holes.append(c)
    tools = doc.addObject("Part::MultiFuse", "Holes")
    tools.Shapes = holes
    cut = doc.addObject("Part::Cut", "ShelfBracket")
    cut.Base, cut.Tool = fuse, tools
    doc.recompute()
    for o in doc.Objects:
        o.Visibility = o is cut or o.TypeId == "App::VarSet"


def solution(name: str, doc: Any) -> None:
    if name in REFERENCES:
        _feature(doc, name.title().replace("_", ""), reference(name))
    elif name == "gear_pair":
        pinion = gear(1, 20, 8, 5)
        wheel = gear(1, 40, 8, 5)
        wheel.rotate(V(0, 0, 0), V(0, 0, 1), 180 / 40)  # a tooth gap faces the pinion's tooth
        wheel.translate(V(30, 0, 0))
        _feature(doc, "Pinion", pinion)
        _feature(doc, "Wheel", wheel)
    elif name == "spring":
        _feature(doc, "Spring", spring())
    elif name == "shelf_bracket":
        _shelf_bracket(doc)
    else:
        raise KeyError(name)
    doc.recompute()
