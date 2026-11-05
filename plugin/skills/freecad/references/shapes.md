# Shapes beyond extrude (FreeCAD 1.1)

Tested by `tests/gui/test_references.py`. A plain extrusion is the usual way a model comes out wrong: threads become stacked rings, springs become tori, bottles become faceted. Use the real operation.

## Contents
- Threads: external, internal, partial on a neck
- Springs and other helix sweeps
- Twisted extrusion
- Shell, draft
- Patterns and mirror
- Fillets: order and recovery

## Threads

A real thread is one sweep along a helix, fused to the core. Stacked rings or grooves are not a thread: the parts will not screw together. Rules: the profile sits on the helix's start (x = radius, y = 0, around z = 0) and overlaps the core by about 0.1 mm so the fuse gives one solid; start the core below and end it above, then trim both ends flat. Threads take some seconds: run them with `run_headless` if there are many.

```python
def thread(major, pitch, length, depth=None):
    """External ISO-like thread on z = 0..length: a core plus one helical ridge, trimmed flat."""
    depth = depth or 0.6134 * pitch
    minor = major / 2 - depth
    h = length + 2 * pitch
    helix = Part.Wire(Part.makeHelix(pitch, h, minor))
    half = pitch * 0.44
    tri = Part.makePolygon([Vector(minor - 0.1, 0, -half), Vector(major / 2, 0, -pitch / 8), Vector(major / 2, 0, pitch / 8),
                            Vector(minor - 0.1, 0, half), Vector(minor - 0.1, 0, -half)])
    ridge = helix.makePipeShell([tri], True, True)
    core = Part.makeCylinder(minor, h + 2 * pitch, Vector(0, 0, -pitch))
    solid = core.fuse(ridge).removeSplitter()
    solid.translate(Vector(0, 0, -pitch))
    keep = Part.makeCylinder(major, length)   # flat ends at z = 0 and z = length
    return solid.common(keep)

bolt = thread(8, 1.25, 20)
assert bolt.isValid() and len(bolt.Solids) == 1
assert abs(bolt.optimalBoundingBox(True, False).ZLength - 20) < 0.01    # BoundBox of B-splines is too large
assert any(type(f.Surface).__name__ == "BSplineSurface" for f in bolt.Faces)
doc.addObject("Part::Feature", "Thread").Shape = bolt
```

An **internal thread** is the negative of a slightly enlarged external thread, so the parts have clearance (FDM 0.2–0.4 mm on the diameter is the usual start; print a test).

```python
def thread(major, pitch, length):
    depth = 0.6134 * pitch
    minor = major / 2 - depth
    h = length + 2 * pitch
    helix = Part.Wire(Part.makeHelix(pitch, h, minor))
    half = pitch * 0.44
    tri = Part.makePolygon([Vector(minor - 0.1, 0, -half), Vector(major / 2, 0, -pitch / 8), Vector(major / 2, 0, pitch / 8),
                            Vector(minor - 0.1, 0, half), Vector(minor - 0.1, 0, -half)])
    core = Part.makeCylinder(minor, h + 2 * pitch, Vector(0, 0, -pitch))
    solid = core.fuse(helix.makePipeShell([tri], True, True)).removeSplitter()
    solid.translate(Vector(0, 0, -pitch))
    return solid

import math
clearance = 0.3
tool = thread(8 + clearance, 1.25, 8)
tool.translate(Vector(0, 0, -1.25))
hexagon = Part.makePolygon([Vector(7.5 * math.cos(a * math.pi / 3), 7.5 * math.sin(a * math.pi / 3), 0) for a in range(7)])
blank = Part.Face(hexagon).extrude(Vector(0, 0, 6.8))
nut = blank.cut(tool)
assert nut.isValid() and len(nut.Solids) == 1 and nut.Volume < blank.Volume - 200
doc.addObject("Part::Feature", "Nut").Shape = nut
```

A **partial thread on a neck** (bottle, jar): the ridge runs a set number of turns and stands on the neck; its ends should fade or be left blunt, then fuse it. The pitch is the helix's, so 1.5 turns of 3 mm pitch is a helix 4.5 mm high.

```python
neck_r, pitch, turns, ridge = 14.0, 3.0, 1.5, 1.2
neck = Part.makeCylinder(neck_r, 20).cut(Part.makeCylinder(neck_r - 1.6, 20))
helix = Part.Wire(Part.makeHelix(pitch, pitch * turns, neck_r))
prof = Part.makePolygon([Vector(neck_r - 0.3, 0, -0.9), Vector(neck_r + ridge, 0, -0.35), Vector(neck_r + ridge, 0, 0.35),
                         Vector(neck_r - 0.3, 0, 0.9), Vector(neck_r - 0.3, 0, -0.9)])
band = helix.makePipeShell([prof], True, True)
band.translate(Vector(0, 0, 10))
threaded = neck.fuse(band).removeSplitter()
assert threaded.isValid() and len(threaded.Solids) == 1
assert abs(threaded.optimalBoundingBox(True, False).XLength - 2 * (neck_r + ridge)) < 0.2
doc.addObject("Part::Feature", "Neck").Shape = threaded
```

For **tapped holes that are not printed**, use `PartDesign::Hole` (see `mechanical.md`), which knows the ISO sizes.

## Springs and helix sweeps

A compression spring: closed end coils at wire pitch, active coils in between, ends cut flat. Build the path from three helix pieces that join, or cut one uniform helix flat for a simple spring.

```python
wire_d, outer, free, coils = 1.5, 20.0, 40.0, 10
r = (outer - wire_d) / 2
pitch = (free + wire_d) / coils
path = Part.Wire(Part.makeHelix(pitch, pitch * coils, r))
circle = Part.Wire(Part.makeCircle(wire_d / 2, Vector(r, 0, 0), Vector(0, 1, 0)))
coil = path.makePipeShell([circle], True, True)
cut = Part.makeBox(50, 50, 10, Vector(-25, -25, -10 + wire_d / 2)).fuse(Part.makeBox(50, 50, 10, Vector(-25, -25, free + wire_d / 2)))
spring = coil.cut(cut)
spring.translate(Vector(0, 0, -wire_d / 2))
bb = spring.optimalBoundingBox(True, False)     # BoundBox of a B-spline includes control points
assert spring.isValid() and abs(bb.ZLength - free) < 0.05 and abs(bb.XLength - outer) < 0.3
assert sum(1 for f in spring.Faces if type(f.Surface).__name__ == "Plane") >= 2   # ground ends
doc.addObject("Part::Feature", "Spring").Shape = spring
```

## Twisted extrusion

Sweep a section along a straight line with a helix as the auxiliary spine; the section turns with the helix (helical gears, drill flutes, twisted vases).

```python
height, twist = 40.0, 90.0
pitch = height * 360.0 / twist
sq = Part.makePolygon([Vector(10, -10, 0), Vector(10, 10, 0), Vector(-10, 10, 0), Vector(-10, -10, 0), Vector(10, -10, 0)])
spine = Part.Wire(Part.makeLine(Vector(0, 0, 0), Vector(0, 0, height)))
aux = Part.Wire(Part.makeHelix(pitch, height, 15))
pipe = Part.BRepOffsetAPI.MakePipeShell(spine)
pipe.setAuxiliarySpine(aux, True, 0)
pipe.add(sq, False, False)
pipe.setTransitionMode(1)
pipe.build()
tw = Part.Solid(Part.Shell(pipe.shape().Faces + [Part.Face(sq), Part.Face(Part.Wire(pipe.lastShape().Edges))]))
assert tw.isValid() and abs(tw.Volume - 400 * height) < 400 * height * 0.05
doc.addObject("Part::Feature", "Twisted").Shape = tw
```

## Shell and draft

Shell: `PartDesign::Thickness` (see `partdesign.md`) or `shape.makeThickness([faces], -wall, 1e-3)`. Draft tilts walls so a moulded part releases and a printed wall needs no support.

```python
body = doc.addObject("PartDesign::Body", "Body")
blk = body.newObject("PartDesign::AdditiveBox", "Block")
blk.Length, blk.Width, blk.Height = 40, 30, 20
doc.recompute()
sides = [f"Face{i + 1}" for i, f in enumerate(blk.Shape.Faces) if abs(f.normalAt(0, 0).z) < 1e-6]
xy = next(f for f in body.Origin.OriginFeatures if f.Role == "XY_Plane")
draft = body.newObject("PartDesign::Draft", "Draft")
draft.Base = (blk, sides)
draft.Angle = 3
draft.NeutralPlane = (xy, [""])
draft.Reversed = True
doc.recompute()
assert draft.Shape.isValid() and "Invalid" not in draft.State and abs(draft.Shape.Volume - blk.Shape.Volume) > 1
```

## Patterns and mirror

Model one rib, hole or tooth and repeat it; `pattern.Occurrences` then changes the count in one place.

```python
body = doc.addObject("PartDesign::Body", "Body")
x_axis = next(f for f in body.Origin.OriginFeatures if f.Role == "X_Axis")
yz = next(f for f in body.Origin.OriginFeatures if f.Role == "YZ_Plane")
plate = body.newObject("PartDesign::AdditiveBox", "Plate")
plate.Length, plate.Width, plate.Height = 100, 30, 4
plate.Placement.Base = Vector(-50, -15, 0)
slot = body.newObject("PartDesign::SubtractiveBox", "Slot")
slot.Length, slot.Width, slot.Height = 4, 20, 6
slot.Placement.Base = Vector(-34, -10, -1)
row = body.newObject("PartDesign::LinearPattern", "Slots")
row.Originals = [slot]
row.Direction = (x_axis, [""])
row.Length = 64
row.Occurrences = 5
body.Tip = row
doc.recompute()
hole = body.newObject("PartDesign::SubtractiveCylinder", "Hole")
hole.Radius, hole.Height = 3, 6
hole.Placement.Base = Vector(42, 0, -1)
mirror = body.newObject("PartDesign::Mirrored", "BothEnds")
mirror.Originals = [hole]            # one feature at a time: a pattern of a pattern needs MultiTransform
mirror.MirrorPlane = (yz, [""])
body.Tip = mirror
doc.recompute()
assert mirror.Shape.isValid() and "Invalid" not in mirror.State
assert abs(mirror.Shape.Volume - (100 * 30 * 4 - 5 * 4 * 20 * 4 - 2 * 3.14159265 * 9 * 4)) < 1
```

## Fillets: order and recovery

1. Booleans first, then large fillets, then small ones; a chamfer before a fillet where they meet.
2. Pick edges by geometry (position, length, curve type), not by number: numbers change when the model changes.
3. A fillet that fails: the radius is usually larger than a neighbouring face. Try the edges one by one to find the culprit, then use a smaller radius there or a chamfer.

```python
block = Part.makeBox(40, 30, 10).cut(Part.makeBox(30, 20, 10, Vector(5, 5, 2)))
def outer_top(shape):
    return [e for e in shape.Edges if abs(e.BoundBox.ZMin - 10) < 1e-6 and e.BoundBox.ZLength < 1e-6
            and (min(e.BoundBox.XMin, e.BoundBox.YMin) < 1e-6 or e.BoundBox.XMax > 39.9 or e.BoundBox.YMax > 29.9)]
def largest_fillet(shape, edges, radius):
    """Halve the radius until the fillet works: too large for the neighbouring face is the usual failure."""
    while radius > 0.05:
        try:
            trial = shape.makeFillet(radius, edges)
            if trial.isValid() and trial.Volume < shape.Volume:
                return trial, radius
        except Exception:
            pass
        radius /= 2
    return shape, 0.0
result, used = largest_fillet(block, outer_top(block), 8.0)   # 8 mm does not fit on the 5 mm rim
assert result.isValid() and 0 < used < 8
doc.addObject("Part::Feature", "Filleted").Shape = result
```
