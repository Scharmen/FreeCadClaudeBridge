"""Standard parts as solids. Runs inside freecadcmd, loaded by its file path (see checks/runner.py).

Each builder returns {part name: shape}, in place and in position, so that fits can be checked as built.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

import FreeCAD as App
import numpy as np
import Part

sys.path.insert(0, str(Path(__file__).parent))     # this file is loaded by its path, away from its package
import surface_math  # noqa: E402

V = App.Vector
THREAD_DEPTH = 0.6134


def _inv(a: float) -> float:
    return math.tan(a) - a


def _root_fillet(rf: float, rb: float, half_gap: float, module: float) -> float:
    """Radius of the arc from the radial foot of a flank into the root circle; 0 when there is no room for one."""
    if rb - rf < 0.05 * module:
        return 0.0
    largest = (rb ** 2 - rf ** 2) / (2 * rf)                  # the arc would start right at the base circle
    slope = math.tan(0.9 * half_gap)                          # leave some root circle between two arcs
    radius = largest
    for _ in range(6):
        radius = min(largest, slope * math.sqrt(rf ** 2 + 2 * rf * radius))
    return radius


def gear_outline(module: float, teeth: int, pressure_deg: float = 20.0, backlash: float = 0.1) -> Any:
    """Closed wire of an involute gear: spline flanks, arcs on the tip and root circles, filleted radial feet."""
    a = math.radians(pressure_deg)
    rp, rb = module * teeth / 2, module * teeth / 2 * math.cos(a)
    ra, rf = rp + module, rp - 1.25 * module
    half = math.pi / (2 * teeth) + _inv(a) - backlash / (2 * rp)   # half a tooth at the base circle
    start = max(rb, rf)

    def flank_angle(r: float) -> float:
        return half - _inv(math.acos(min(1.0, rb / r)))

    def pt(r: float, th: float) -> Any:
        return V(r * math.cos(th), r * math.sin(th), 0)

    radii = [start + (ra - start) * i / 6 for i in range(7)]
    pitch = 2 * math.pi / teeth
    fillet = _root_fillet(rf, rb, pitch / 2 - half, module)
    edges = []

    def foot(theta: float, side: int) -> tuple[Any, Any]:
        """The way from the root circle to the flank at `theta`: its point on the root circle, and its edges."""
        base = pt(rb, theta)
        if not fillet:
            return pt(rf, theta), [Part.makeLine(pt(rf, theta), base)] if rf < rb else []
        u, n = V(math.cos(theta), math.sin(theta), 0), V(-math.sin(theta), math.cos(theta), 0) * side
        along = math.sqrt(rf ** 2 + 2 * rf * fillet)
        centre = u * along + n * fillet
        tangent, root = u * along, centre * (rf / (rf + fillet))
        middle = (tangent - centre) + (root - centre)
        middle = centre + middle * (fillet / middle.Length)
        out = [Part.Arc(root, middle, tangent).toShape()]
        if rb - along > 1e-6:
            out.append(Part.makeLine(tangent, base))
        return root, out

    for k in range(teeth):
        c = k * pitch
        left = [pt(r, c - flank_angle(r)) for r in radii]
        right = [pt(r, c + flank_angle(r)) for r in reversed(radii)]
        if rf < rb:
            edges += foot(c - flank_angle(rb), -1)[1]
        spline = Part.BSplineCurve()
        spline.interpolate(left)
        edges.append(spline.toShape())
        edges.append(Part.Arc(left[-1], pt(ra, c), right[0]).toShape())
        spline = Part.BSplineCurve()
        spline.interpolate(right)
        edges.append(spline.toShape())
        end = right[-1]
        if rf < rb:
            end, rising = foot(c + flank_angle(rb), 1)
            for e in reversed(rising):               # the same edges, walked down to the root
                e.reverse()
                edges.append(e)
        start_next = foot(c + pitch - flank_angle(rb), -1)[0] if rf < rb else pt(rf, c + pitch - flank_angle(rf))
        edges.append(Part.Arc(end, pt(rf, c + pitch / 2), start_next).toShape())
    return Part.Wire(edges)


def gear(module: float, teeth: int, width: float, bore: float, pressure_deg: float = 20.0, helix_deg: float = 0.0,
         backlash: float = 0.1) -> Any:
    outline = gear_outline(module, teeth, pressure_deg, backlash)
    if helix_deg:
        rp = module * teeth / 2
        twist = width * math.tan(math.radians(helix_deg)) / rp          # radians over the width
        lead = width * 2 * math.pi / twist
        spine = Part.Wire(Part.makeLine(V(0, 0, 0), V(0, 0, width)))
        aux = Part.Wire(Part.makeHelix(lead, width, rp * 2))
        pipe = Part.BRepOffsetAPI.MakePipeShell(spine)
        pipe.setAuxiliarySpine(aux, True, 0)
        pipe.add(outline, False, False)
        pipe.setTransitionMode(1)
        pipe.build()
        last = Part.Wire(pipe.lastShape().Edges)
        body = Part.Solid(Part.Shell(pipe.shape().Faces + [Part.Face(outline), Part.Face(last)]))
    else:
        body = Part.Face(outline).extrude(V(0, 0, width))
    if bore:
        body = body.cut(Part.makeCylinder(bore / 2, width + 2, V(0, 0, -1)))
    return body


def gear_pair(p: dict[str, Any]) -> dict[str, Any]:
    a = gear(p["module"], p["teeth_a"], p["width"], p["bore_a"], p["pressure_deg"], 0.0, p["backlash"])
    b = gear(p["module"], p["teeth_b"], p["width"], p["bore_b"], p["pressure_deg"], 0.0, p["backlash"])
    distance = p["module"] * (p["teeth_a"] + p["teeth_b"]) / 2
    # a's tooth points along +X towards b; b turns half a tooth so that a gap faces it
    b.rotate(V(0, 0, 0), V(0, 0, 1), 180 + 180 / p["teeth_b"])
    b.translate(V(distance, 0, 0))
    return {"GearA": a, "GearB": b}


def spring(p: dict[str, Any]) -> dict[str, Any]:
    d, r = p["wire"], (p["outer"] - p["wire"]) / 2
    active, length = p["active_coils"], p["free_length"]
    # Ground like a real spring: the flat runs around 3/4 of the end coil, from a tip left at about a fifth of the wire
    # to nothing. The plane lies GRIND above the height of the wire's centre at the tip.
    grind = 0.29 * d
    closed = d * 1.05                                         # the end coil's rise: its tip touches the next coil
    rise = length + 2 * grind - 2 * closed                    # centre height gained over the active coils
    even = (rise - closed) / (active - 1) if active > 1 else rise
    pts = []
    per_turn = 24
    total = active + 2

    def lift(t: float) -> float:
        """Centre height over the first turns: closed, then the pitch opens over one turn, then even."""
        if t <= 1 or active <= 1:
            return closed * min(t, 1) + (rise * (t - 1) / active if t > 1 else 0.0)
        if t <= 2:
            return closed + closed * (t - 1) + (even - closed) * (t - 1) ** 2 / 2
        return closed + (closed + even) / 2 + even * (t - 2)

    height = lift(total / 2) * 2
    for i in range(total * per_turn + 1):
        turn = i / per_turn
        z = lift(turn) if turn <= total / 2 else height - lift(total - turn)
        th = 2 * math.pi * turn
        pts.append(V(r * math.cos(th), r * math.sin(th), z))
    path = Part.BSplineCurve()
    path.interpolate(pts)
    wire = Part.Wire(path.toShape())
    start_dir = path.tangent(path.FirstParameter)[0]
    circle = Part.Wire(Part.makeCircle(d / 2, pts[0], start_dir))
    coil = wire.makePipeShell([circle], True, True)
    side = 4 * r + 8 * d
    cut = Part.makeBox(side, side, 4 * d, V(-side / 2, -side / 2, grind - 4 * d))
    cut = cut.fuse(Part.makeBox(side, side, 4 * d, V(-side / 2, -side / 2, height - grind)))
    done = coil.cut(cut)
    done.translate(V(0, 0, -grind))
    return {"Spring": done}


def _thread(major: float, pitch: float, length: float, extra: float = 0.0) -> Any:
    """Core and helical ridge on z = 0 .. length, longer than asked by a pitch at both ends."""
    major = major + extra
    minor = major / 2 - THREAD_DEPTH * pitch
    h = length + 4 * pitch
    half = pitch * 0.44
    tri = Part.makePolygon([V(minor - 0.1, 0, -half), V(major / 2, 0, -pitch / 8), V(major / 2, 0, pitch / 8),
                            V(minor - 0.1, 0, half), V(minor - 0.1, 0, -half)])
    ridge = Part.Wire(Part.makeHelix(pitch, h, minor)).makePipeShell([tri], True, True)
    core = Part.makeCylinder(minor, h + 2 * pitch, V(0, 0, -pitch))
    solid = core.fuse(ridge).removeSplitter()
    solid.translate(V(0, 0, -2 * pitch))
    return solid


def _hex_prism(af: float, height: float, z: float = 0.0) -> Any:
    r = af / math.sqrt(3)
    pts = [V(r * math.cos(i * math.pi / 3), r * math.sin(i * math.pi / 3), z) for i in range(7)]
    prism = Part.Face(Part.makePolygon(pts)).extrude(V(0, 0, height))
    # the usual 30° chamfer on the corners
    corner = r + 1
    profile = Part.makePolygon([V(0, 0, z), V(af / 2 + 0.02, 0, z), V(corner, 0, z + (corner - af / 2) * 0.58),
                                V(corner, 0, z + height - (corner - af / 2) * 0.58), V(af / 2 + 0.02, 0, z + height),
                                V(0, 0, z + height), V(0, 0, z)])
    return prism.common(Part.Face(profile).revolve(V(0, 0, 0), V(0, 0, 1), 360))


def bolt(p: dict[str, Any], iso: dict[str, Any]) -> dict[str, Any]:
    pitch, af, head, _ = iso[p["size"]]
    major = float(p["size"][1:])
    shank = _thread(major, pitch, p["length"]).common(Part.makeCylinder(major, p["length"]))
    # a lead-in chamfer at the free end, the shank hanging from z = 0 down, the head above
    shank.translate(V(0, 0, -p["length"]))
    tip = Part.makeCone(major / 2 - THREAD_DEPTH * pitch, major / 2 + 0.2, THREAD_DEPTH * pitch + 0.2,
                        V(0, 0, -p["length"]))
    rest = Part.makeCylinder(major, p["length"], V(0, 0, -p["length"] + THREAD_DEPTH * pitch + 0.2))
    shank = shank.common(tip.fuse(rest))
    return {"Bolt": shank.fuse(_hex_prism(af, head)).removeSplitter()}


def nut(p: dict[str, Any], iso: dict[str, Any]) -> dict[str, Any]:
    pitch, af, _, height = iso[p["size"]]
    major = float(p["size"][1:])
    tool = _thread(major, pitch, height, p["clearance"])
    return {"Nut": _hex_prism(af, height).cut(tool)}


def bolt_nut(p: dict[str, Any], iso: dict[str, Any]) -> dict[str, Any]:
    pitch = iso[p["size"]][0]
    the_bolt = bolt(p, iso)["Bolt"]
    the_nut = nut(p, iso)["Nut"]
    height = iso[p["size"]][3]
    # the nut's thread is cut from the same helix, so it has to sit a whole number of pitches along the bolt
    drop = round((p["nut_at"] + height) / pitch) * pitch
    the_nut.translate(V(0, 0, -drop))
    return {"Bolt": the_bolt, "Nut": the_nut}


def thread(p: dict[str, Any]) -> dict[str, Any]:
    """An external thread as a solid from z = 0 to length (chamfered lead-in at the top, optionally on a
    thicker core), or an internal thread as a cutter that reaches a pitch beyond both ends."""
    major, pitch, length = p["major"], p["pitch"], p["length"]
    if p["kind"] == "internal":
        return {"ThreadCutter": _thread(major, pitch, length, p["clearance"])}
    solid = _thread(major, pitch, length).common(Part.makeCylinder(major, length))
    lead = THREAD_DEPTH * pitch + 0.2
    tip = Part.makeCone(major / 2 + 0.2, major / 2 - THREAD_DEPTH * pitch, lead, V(0, 0, length - lead))
    rest = Part.makeCylinder(major, length - lead)
    solid = solid.common(tip.fuse(rest))
    core = float(p.get("core") or 0.0)
    if core > major - 2 * THREAD_DEPTH * pitch:
        solid = solid.fuse(Part.makeCylinder(core / 2, length))
    return {"Thread": solid.removeSplitter()}


def _try_fillet(shape: Any, radius: float, edges: list[Any]) -> Any:
    try:
        out = shape.makeFillet(radius, edges)
        return out if out.isValid() and out.Solids else shape
    except Part.OCCError:
        return shape


def wheel(p: dict[str, Any]) -> dict[str, Any]:
    """Tyre, spoked rim and bore on the y axis, the wheel centred at the origin."""
    d, w, rim = p["diameter"], p["width"], p["rim"]
    rw = p["rim_width"] or 0.9 * w
    wall = min(3.0, 0.1 * rim)                    # the rim's outer ring; 3 mm on a 60 mm wheel, 1.5 on a toy's
    hub = p["hub"] or max(0.3 * rim, p["bore"] + 2 * wall)
    sw = p["spoke_width"] or max(0.8, 0.09 * rim)
    if rim / 2 - wall < hub / 2 + 1.0:
        raise ValueError(f"no room for spokes between the hub ({hub:g} mm) and the rim ({rim:g} mm): a bore of "
                         f"{p['bore']:g} needs a rim of at least {2 * (p['bore'] / 2 + 2 * wall + 1 + wall):g} mm")
    y_axis = V(0, 1, 0)
    tyre = Part.makeCylinder(d / 2, w, V(0, -w / 2, 0), y_axis).cut(Part.makeCylinder(rim / 2 - 0.5, w + 2,
                                                                                     V(0, -w / 2 - 1, 0), y_axis))
    shoulder = [e for e in tyre.Edges if type(e.Curve).__name__ == "Circle" and abs(e.Curve.Radius - d / 2) < 1e-6]
    tyre = _try_fillet(tyre, min(0.18 * w, 0.12 * d), shoulder)
    n_groove, deep = int(p["grooves"]), p["groove_depth"]
    if n_groove and deep > 0:
        tread = w - 2 * max(0.18 * w, 1.0)
        for i in range(n_groove):
            y = -tread / 2 + tread * (i + 0.5) / n_groove
            ring = Part.makeCylinder(d / 2 + 1, 1.0, V(0, y - 0.5, 0), y_axis).cut(
                Part.makeCylinder(d / 2 - deep, 1.0, V(0, y - 0.5, 0), y_axis))
            tyre = tyre.cut(ring)
    disc_rim = Part.makeCylinder(rim / 2, rw, V(0, -rw / 2, 0), y_axis)
    inner = Part.makeCylinder(rim / 2 - wall, rw + 2, V(0, -rw / 2 - 1, 0), y_axis).cut(
        Part.makeCylinder(hub / 2, rw + 2, V(0, -rw / 2 - 1, 0), y_axis))
    spokes = None
    for i in range(int(p["spokes"])):
        bar = Part.makeBox(rim, rw + 2, sw, V(0, -rw / 2 - 1, -sw / 2))
        bar.rotate(V(0, 0, 0), y_axis, 360.0 * i / int(p["spokes"]))
        spokes = bar if spokes is None else spokes.fuse(bar)
    windows = inner.cut(spokes) if spokes is not None else inner
    the_rim = disc_rim.cut(windows).cut(Part.makeCylinder(p["bore"] / 2, rw + 2, V(0, -rw / 2 - 1, 0), y_axis))
    out = {"Tyre": tyre.removeSplitter(), "Rim": the_rim.removeSplitter()}
    if p["disc"] > 0:
        y0 = -rw / 2 - 2.5
        brake = Part.makeCylinder(p["disc"] / 2, 4.0, V(0, y0, 0), y_axis).cut(
            Part.makeCylinder(p["bore"] / 2 + 2, 6, V(0, y0 - 1, 0), y_axis))
        cal_w, cal_len, cal_h = 0.3 * p["disc"], min(8.0, 0.5 * p["disc"]), min(10.0, 0.6 * p["disc"])
        y1 = y0 - (cal_len - 4) / 2
        caliper = Part.makeBox(cal_w, cal_len, cal_h, V(p["disc"] / 2 - cal_w + 0.12 * p["disc"], y1, -cal_h / 2))
        slot = Part.makeCylinder(p["disc"] / 2 + 0.3, cal_len + 2, V(0, y1 - 1, 0), y_axis).cut(
            Part.makeCylinder(p["disc"] / 2 - 0.4 * cal_w, cal_len + 2, V(0, y1 - 1, 0), y_axis))
        caliper = caliper.cut(slot)
        if p.get("brake_side", "-y") == "+y":    # the brake sits on the car's side of the wheel
            for shape in (brake, caliper):
                shape.rotate(V(0, 0, 0), V(0, 0, 1), 180)
        out["Disc"] = brake
        out["Caliper"] = caliper
    return out


def lamp(p: dict[str, Any]) -> dict[str, Any]:
    """Housing open towards -Y with a reflector bowl, a lens in the opening and a strip under the top edge."""
    w, h, d, wall, lens = p["width"], p["height"], p["depth"], p["wall"], p["lens"]
    gap = 0.15
    if p["shape"] == "round":
        h = w
        y_axis = V(0, 1, 0)
        housing = Part.makeCylinder(w / 2, d, V(0, 0, 0), y_axis)
        cavity = Part.makeCylinder(w / 2 - wall, d - wall, V(0, -1, 0), y_axis)
        cavity = Part.makeCylinder(w / 2 - wall, d - wall + 1, V(0, -1, 0), y_axis)
        lens_solid = Part.makeCylinder(w / 2 - wall - gap, lens, V(0, 0, 0), y_axis)
        block = Part.makeCylinder(w / 2 - wall - gap, d - wall - lens - 0.8, V(0, lens + 0.4, 0), y_axis)
    else:
        housing = Part.makeBox(w, d, h, V(-w / 2, 0, -h / 2))
        cavity = Part.makeBox(w - 2 * wall, d - wall + 1, h - 2 * wall, V(-w / 2 + wall, -1, -h / 2 + wall))
        lens_solid = Part.makeBox(w - 2 * wall - 2 * gap, lens, h - 2 * wall - 2 * gap,
                                  V(-w / 2 + wall + gap, 0, -h / 2 + wall + gap))
        block = Part.makeBox(w - 2 * wall - 2 * gap, d - wall - lens - 0.8, h - 2 * wall - 2 * gap,
                             V(-w / 2 + wall + gap, lens + 0.4, -h / 2 + wall + gap))
    housing = housing.cut(cavity)
    bowl_r = max(w, h)
    bowl = Part.makeSphere(bowl_r)
    m = App.Matrix()
    m.scale((w / 2 - wall - gap - 0.2) / bowl_r, (d - wall - lens - 1.0) / bowl_r, (h / 2 - wall - gap - 0.2) / bowl_r)
    bowl = bowl.transformGeometry(m)
    bowl.translate(V(0, lens + 0.4, 0))
    reflector = block.cut(bowl)
    strip = Part.makeBox(w - 2 * wall - 3.0, 2.0, 1.2, V(-w / 2 + wall + 1.5, lens + 0.5, h / 2 - wall - gap - 1.4))
    reflector = reflector.cut(Part.makeBox(w - 2 * wall - 2.0, 3.0, 2.0, V(-w / 2 + wall + 1.0, lens + 0.2,
                                                                          h / 2 - wall - gap - 1.8)))
    return {"Housing": housing.removeSplitter(), "Reflector": reflector, "Lens": lens_solid, "Strip": strip}


def pattern(p: dict[str, Any]) -> dict[str, Any]:
    """Bars of a repeated pattern (kind panel) or its openings as cutters (kind holes)."""
    w, h, t, cell, bar, frame = p["width"], p["height"], p["thickness"], p["cell"], p["bar"], p["frame"]
    panel = Part.makeBox(w, t, h, V(-w / 2, 0, 0))
    x0, x1, z0, z1 = -w / 2 + frame, w / 2 - frame, frame, h - frame
    opening = cell - bar
    tools = []
    y_dir = V(0, 1, 0)
    if p["cells"] == "slats":
        z = z0 + bar / 2
        while z + opening <= z1 + 1e-6:
            tools.append(Part.makeBox(x1 - x0, t + 2, opening, V(x0, -1, z)))
            z += cell
    else:
        rows = int((z1 - z0) / (cell * (0.866 if p["cells"] == "honeycomb" else 1.0))) + 2
        cols = int((x1 - x0) / cell) + 2
        for j in range(rows):
            for i in range(cols):
                if p["cells"] == "honeycomb":
                    cx = x0 + (i + 0.5 * (j % 2)) * cell
                    cz = z0 + j * cell * 0.866
                    rc = opening / math.sqrt(3)
                    pts = [V(cx + rc * math.cos(math.radians(60 * k + 30)), -1,
                             cz + rc * math.sin(math.radians(60 * k + 30))) for k in range(7)]
                    tool = Part.Face(Part.makePolygon(pts)).extrude(V(0, t + 2, 0))
                elif p["cells"] == "holes":
                    cx, cz = x0 + i * cell, z0 + j * cell
                    tool = Part.makeCylinder(opening / 2, t + 2, V(cx, -1, cz), y_dir)
                else:
                    cx, cz = x0 + i * cell, z0 + j * cell
                    tool = Part.makeBox(opening, t + 2, opening, V(cx - opening / 2, -1, cz - opening / 2))
                bb = tool.BoundBox
                if bb.XMin < x0 - 1e-6 or bb.XMax > x1 + 1e-6 or bb.ZMin < z0 - 1e-6 or bb.ZMax > z1 + 1e-6:
                    continue
                tools.append(tool)
    if not tools:
        raise ValueError("no opening fits: make the panel bigger or the cell smaller")
    if p["kind"] == "holes":
        return {"Pattern": Part.makeCompound(tools)}
    holes = tools[0].fuse(tools[1:]) if len(tools) > 1 else tools[0]
    return {"Pattern": panel.cut(holes).removeSplitter()}


def _countersunk(screw: float, depth: float, x: float, y: float, top: float, up: bool = True) -> Any:
    """Through hole with a 90° countersink opening at z = top."""
    head = screw * 2
    sink = (head - screw) / 2
    hole = Part.makeCylinder(screw / 2 + 0.25, depth + 2, V(x, y, top - depth - 1))
    cone = Part.makeCone(screw / 2 + 0.25, head / 2 + 0.25, sink, V(x, y, top - sink))
    return hole.fuse(cone)


def hinge(p: dict[str, Any]) -> dict[str, Any]:
    length, width, t, pin_d, gap, n = (p[k] for k in ("length", "leaf_width", "thickness", "pin", "gap", "knuckles"))
    outer_r = max(pin_d / 2 + gap + 1.6, t)
    each = (length - (n - 1) * gap) / n
    leaf_a = Part.makeBox(length, width, t, V(0, outer_r + gap, 0))      # clear of the other leaf's knuckles
    leaf_b = Part.makeBox(length, width, t, V(0, -outer_r - gap - width, 0))
    axis_z = outer_r
    for i in range(n):
        x = i * (each + gap)
        k = Part.makeCylinder(outer_r, each, V(x, 0, axis_z), V(1, 0, 0))
        reach = outer_r + gap + 0.5
        arm = Part.makeBox(each, reach, t, V(x, 0 if i % 2 == 0 else -reach, 0))
        if i % 2 == 0:
            leaf_a = leaf_a.fuse(k).fuse(arm)
        else:
            leaf_b = leaf_b.fuse(k).fuse(arm)
    bore = Part.makeCylinder(pin_d / 2 + gap, length + 2, V(-1, 0, axis_z), V(1, 0, 0))
    leaf_a, leaf_b = leaf_a.cut(bore), leaf_b.cut(bore)
    for leaf_name, y in (("a", outer_r + gap + width * 0.6), ("b", -outer_r - gap - width * 0.6)):
        for x in (length * 0.25, length * 0.75):
            tool = _countersunk(p["screw"], t, x, y, t)
            if leaf_name == "a":
                leaf_a = leaf_a.cut(tool)
            else:
                leaf_b = leaf_b.cut(tool)
    pin_shape = Part.makeCylinder(pin_d / 2, length, V(0, 0, axis_z), V(1, 0, 0))
    return {"LeafA": leaf_a.removeSplitter(), "LeafB": leaf_b.removeSplitter(), "Pin": pin_shape}


def enclosure(p: dict[str, Any]) -> dict[str, Any]:
    L, W, H, w, lid_h, corner = (p[k] for k in ("length", "width", "height", "wall", "lid", "corner"))
    body_h = H - lid_h
    outer = Part.makeBox(L, W, body_h)
    if corner:
        outer = outer.makeFillet(corner, [e for e in outer.Edges if e.BoundBox.ZLength > 1])
    box = outer.cut(Part.makeBox(L - 2 * w, W - 2 * w, body_h, V(w, w, w)))
    boss_r = p["insert"] / 2 + 2.0
    inset = w + boss_r
    spots = [(inset, inset), (L - inset, inset), (inset, W - inset), (L - inset, W - inset)]
    for x, y in spots:
        box = box.fuse(Part.makeCylinder(boss_r, body_h - w, V(x, y, w)))
        # ties the boss to the corner, so it does not stand alone
        box = box.fuse(Part.makeBox((x if x < L / 2 else L - x) - w + 0.01, 1.6, body_h - w,
                                    V(w - 0.01 if x < L / 2 else x, y - 0.8, w)))
    for x, y in spots:
        box = box.cut(Part.makeCylinder(p["insert"] / 2, body_h - w + 1, V(x, y, w)))
    box = box.removeSplitter()
    z = body_h + p["lid_lift"]
    lid = Part.makeBox(L, W, lid_h, V(0, 0, z))
    if corner:
        lid = lid.makeFillet(corner, [e for e in lid.Edges if e.BoundBox.ZLength > 0.5])
    for x, y in spots:
        lid = lid.cut(_countersunk(p["screw"] - 0.5, lid_h, x, y, z + lid_h))
    return {"Body": box, "Lid": lid.removeSplitter()}


def snap_box(p: dict[str, Any], strain: dict[str, float]) -> dict[str, Any]:
    L, W, H, w, lid_h, gap, y0 = (p[k] for k in ("length", "width", "height", "wall", "lid", "gap", "undercut"))
    body_h = H - lid_h
    t_arm = 1.6
    arm = max(8.0, math.ceil(math.sqrt(1.5 * t_arm * y0 / strain[p["material"].upper()])))
    box = Part.makeBox(L, W, body_h).cut(Part.makeBox(L - 2 * w, W - 2 * w, body_h, V(w, w, w)))
    ledge_top, ledge_h = body_h - 3, 1.5
    for y in (w - 0.01, W - w - y0):
        box = box.fuse(Part.makeBox(12, y0 + 0.01, ledge_h, V(L / 2 - 6, y, ledge_top - ledge_h)))
    lid = Part.makeBox(L, W, lid_h, V(0, 0, body_h))
    plug_clear = y0 + gap + t_arm + gap          # the plug stops short of the arms: they need room to bend
    lid = lid.fuse(Part.makeBox(L - 2 * w - 2 * gap, W - 2 * w - 2 * gap - 2 * plug_clear, 2,
                                V(w + gap, w + gap + plug_clear, body_h - 2)))
    hook_top = ledge_top - ledge_h - 0.1
    arm_bottom = body_h - arm - 3
    for side in (0, 1):
        y_hook = w + gap if side == 0 else W - w - y0 - gap - t_arm + y0
        y_arm = w + gap + y0 if side == 0 else W - w - gap - y0 - t_arm
        lid = lid.fuse(Part.makeBox(10, t_arm, body_h - arm_bottom + 0.01, V(L / 2 - 5, y_arm, arm_bottom)))
        lid = lid.fuse(Part.makeBox(10, t_arm + y0, hook_top - arm_bottom,
                                    V(L / 2 - 5, min(y_arm, y_hook), arm_bottom)))
    return {"Box": box.removeSplitter(), "Lid": lid.removeSplitter()}


def bracket(p: dict[str, Any]) -> dict[str, Any]:
    a, b, w, t, d = (p[k] for k in ("leg_a", "leg_b", "width", "thickness", "hole"))
    shape = Part.makeBox(t, w, a).fuse(Part.makeBox(b, w, t))
    g = p["gusset"]
    if g:
        tri = Part.Face(Part.makePolygon([V(t, w / 2 - t / 2, t), V(t + (b - t) * g, w / 2 - t / 2, t),
                                          V(t, w / 2 - t / 2, t + (a - t) * g), V(t, w / 2 - t / 2, t)]))
        shape = shape.fuse(tri.extrude(V(0, t, 0)))
    for frac, off in ((0.55, 0), (0.85, 0)):
        x = t + (b - t) * frac
        z = t + (a - t) * frac
        shape = shape.cut(Part.makeCylinder(d / 2, t + 2, V(x, w * 0.25 + off, -1)))
        shape = shape.cut(Part.makeCylinder(d / 2, t + 2, V(-1, w * 0.25 + off, z), V(1, 0, 0)))
    return {"Bracket": shape.removeSplitter()}


def knob(p: dict[str, Any]) -> dict[str, Any]:
    r, h, d, flat, depth, n, gap = (p[k] for k in ("diameter", "height", "shaft", "flat", "depth", "ridges", "gap"))
    r = r / 2
    body = Part.makeCylinder(r, h)
    top = [e for e in body.Edges if abs(e.BoundBox.ZMin - h) < 1e-6]
    body = body.makeFillet(min(1.5, h / 6), top)
    groove_r = r * math.pi / n * 0.45
    for i in range(n):
        th = 2 * math.pi * i / n
        body = body.cut(Part.makeCylinder(groove_r, h - 2.5, V(r * math.cos(th), r * math.sin(th), -0.5)))
    bore = Part.makeCylinder(d / 2 + gap / 2, depth + 1, V(0, 0, -1))
    # the D: material stays where the shaft has its flat
    bore = bore.cut(Part.makeBox(d + 2, d + 2, depth + 3, V(-d / 2 - 1, d / 2 - flat + gap / 2, -2)))
    body = body.cut(bore)
    mark = Part.makeBox(1.2, r * 0.7, 0.8, V(-0.6, r * 0.2, h - 0.6))
    return {"Knob": body.cut(mark).removeSplitter()}


def _patch_face(patch: dict[str, Any]) -> Any:
    surface = Part.BSplineSurface()
    uk, um = surface_math.knots_and_mults(patch["u_knots"])
    vk, vm = surface_math.knots_and_mults(patch["v_knots"])
    grid = [[V(*(float(c) for c in pole)) for pole in row] for row in patch["poles"]]
    surface.buildFromPolesMultsKnots(poles=grid, umults=um, vmults=vm, uknots=uk, vknots=vk, uperiodic=False,
                                     vperiodic=False, udegree=patch["u_degree"], vdegree=patch["v_degree"])
    return surface.toShape()


def _close(faces: list[Any], tolerance: float) -> Any:
    """Sew the faces into a shell, put flat caps on the open ends, return the solid."""
    sewn = Part.makeCompound(faces)
    sewn.sewShape(tolerance)
    if len(sewn.Shells) != 1:
        raise ValueError(f"the surfaces do not join into one skin ({len(sewn.Shells)} pieces): "
                         "neighbouring lines must start and end at the same x")
    shell = sewn.Shells[0]
    free = [e for e in shell.Edges if len(shell.ancestorsOfType(e, Part.Face)) == 1]
    caps = [Part.Face(Part.Wire(w)) for w in Part.sortEdges(free)]
    closed = Part.makeCompound([*shell.Faces, *caps])
    closed.sewShape(tolerance)
    solid = Part.Solid(closed.Shells[0])
    if solid.Volume < 0:
        solid.reverse()
    return solid


def faired_body(p: dict[str, Any]) -> dict[str, Any]:
    """A smooth closed body from feature lines running front to rear, with sharp edges only at creases."""
    lines = [{**line, "points": np.asarray(line["points"], float)} for line in p["lines"]]
    faces = [_patch_face(patch) for patch in surface_math.patches(lines, p["poles"])]
    faces += [f.mirror(V(0, 0, 0), V(0, 1, 0)) for f in faces]
    size = float(np.ptp(np.concatenate([line["points"] for line in lines]), axis=0).max())
    return {p["name"]: _close(faces, size * 2e-5)}


def fit_error(shape: Any, lines: list[dict[str, Any]]) -> float:
    """How far the given points lie from the finished surface, at most."""
    return max(shape.distToShape(Part.Vertex(V(*pt)))[0] for line in lines for pt in line["points"])


def build(family: str, p: dict[str, Any], iso: dict[str, Any], strain: dict[str, float]) -> dict[str, Any]:
    if family == "gear":
        return {"Gear": gear(p["module"], p["teeth"], p["width"], p["bore"], p["pressure_deg"], p["helix_deg"],
                             p["backlash"])}
    if family in ("bolt", "nut", "bolt_nut"):
        return {"bolt": bolt, "nut": nut, "bolt_nut": bolt_nut}[family](p, iso)
    if family == "snap_box":
        return snap_box(p, strain)
    if family == "faired_body":
        return faired_body(p)
    if family == "thread":
        return thread(p)
    if family in ("wheel", "lamp", "pattern"):
        return {"wheel": wheel, "lamp": lamp, "pattern": pattern}[family](p)
    return {"gear_pair": gear_pair, "spring": spring, "hinge": hinge, "enclosure": enclosure, "bracket": bracket,
            "knob": knob}[family](p)


def export(family: str, p: dict[str, Any], iso: dict[str, Any], strain: dict[str, float], out_dir: str) -> list[dict]:
    """Build the part and write each solid as BREP; what it says about each goes back as plain data."""
    made = []
    for name, shape in build(family, p, iso, strain).items():
        path = f"{out_dir}/{name}.brep"
        shape.exportBrep(path)
        bb = shape.optimalBoundingBox(True, False) if shape.Solids and family != "pattern" else shape.BoundBox
        made.append({"name": name, "path": path, "valid": bool(shape.isValid()), "solids": len(shape.Solids),
                     "size_mm": [round(bb.XLength, 2), round(bb.YLength, 2), round(bb.ZLength, 2)],
                     "volume_mm3": round(shape.Volume, 1),
                     **({"max_distance_to_points_mm": round(fit_error(shape, p["lines"]), 2)}
                        if family == "faired_body" else {})})
    return made
