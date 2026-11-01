# PartDesign (FreeCAD 1.1)

Tested by `tests/gui/test_references.py`. One Body per part; each feature builds on the previous one (the Body's `Tip`). Attach sketches to the Body's origin planes or to datum planes, not to face numbers.

Origin features: `body.Origin.OriginFeatures` are X/Y/Z axes and XY/XZ/YZ planes; find them by `Role`.

Pad a fully constrained sketch on XY, then pocket a hole through all from a sketch on the top face's plane (offset datum plane, robust against topology changes).

```python
body = doc.addObject("PartDesign::Body", "Body")
xy = next(f for f in body.Origin.OriginFeatures if f.Role == "XY_Plane")
sk = body.newObject("Sketcher::SketchObject", "BaseSketch")
sk.AttachmentSupport = [(xy, "")]
sk.MapMode = "FlatFace"
p = [Vector(0, 0, 0), Vector(40, 0, 0), Vector(40, 30, 0), Vector(0, 30, 0)]
for i in range(4):
    sk.addGeometry(Part.LineSegment(p[i], p[(i + 1) % 4]))
for i in range(4):
    sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
for i in (0, 2):
    sk.addConstraint(Sketcher.Constraint("Horizontal", i))
for i in (1, 3):
    sk.addConstraint(Sketcher.Constraint("Vertical", i))
sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 40))
sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, 30))
sk.addConstraint(Sketcher.Constraint("Coincident", 0, 1, -1, 1))
pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 10
top = body.newObject("PartDesign::Plane", "TopPlane")
top.AttachmentSupport = [(xy, "")]
top.MapMode = "FlatFace"
top.AttachmentOffset = App.Placement(Vector(0, 0, 10), App.Rotation())
hs = body.newObject("Sketcher::SketchObject", "HoleSketch")
hs.AttachmentSupport = [(top, "")]
hs.MapMode = "FlatFace"
c = hs.addGeometry(Part.Circle(Vector(20, 15, 0), Vector(0, 0, 1), 4))
hs.addConstraint(Sketcher.Constraint("Diameter", c, 8))
hs.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, c, 3, 20))
hs.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, c, 3, 15))
pocket = body.newObject("PartDesign::Pocket", "Pocket")
pocket.Profile = hs
pocket.Type = "ThroughAll"
doc.recompute()
assert body.Tip == pocket and hs.DoF == 0
assert abs(body.Shape.Volume - (40 * 30 * 10 - 3.14159265 * 16 * 10)) < 0.01
```

Pattern a feature around an axis (bolt circle) and fillet edges found by geometry.

```python
body = doc.addObject("PartDesign::Body", "Body")
xy = next(f for f in body.Origin.OriginFeatures if f.Role == "XY_Plane")
z_axis = next(f for f in body.Origin.OriginFeatures if f.Role == "Z_Axis")
disk = body.newObject("PartDesign::AdditiveCylinder", "Disk")
disk.Radius, disk.Height = 40, 8
hole = body.newObject("PartDesign::SubtractiveCylinder", "BoltHole")
hole.Radius, hole.Height = 3, 8
hole.Placement.Base = Vector(30, 0, 0)
pattern = body.newObject("PartDesign::PolarPattern", "BoltCircle")
pattern.Originals = [hole]
pattern.Axis = (z_axis, [""])
pattern.Angle = 360
pattern.Occurrences = 6
body.Tip = pattern  # newObject() doesn't advance the Tip for pattern features in 1.1
doc.recompute()
outer = [f"Edge{i + 1}" for i, e in enumerate(pattern.Shape.Edges)
         if hasattr(e.Curve, "Radius") and abs(e.Curve.Radius - 40) < 1e-6]
fil = body.newObject("PartDesign::Fillet", "OuterFillet")
fil.Base = (pattern, outer)
fil.Radius = 1.5
doc.recompute()
assert len(outer) == 2 and fil.Shape.isValid() and "Invalid" not in fil.State
assert sum(1 for f in fil.Shape.Faces if type(f.Surface).__name__ == "Cylinder" and abs(f.Surface.Radius - 3) < 1e-6) == 6
```

Shell (thickness) a box into an open housing: remove the top face, 2 mm walls.

```python
body = doc.addObject("PartDesign::Body", "Housing")
blk = body.newObject("PartDesign::AdditiveBox", "Block")
blk.Length, blk.Width, blk.Height = 60, 40, 25
doc.recompute()
top = next(f"Face{i + 1}" for i, f in enumerate(blk.Shape.Faces) if abs(f.CenterOfMass.z - 25) < 1e-6)
shell = body.newObject("PartDesign::Thickness", "Shell")
shell.Base = (blk, [top])
shell.Value = 2
shell.Reversed = True  # walls grow inward, outer size stays 60 x 40 x 25
doc.recompute()
bb = shell.Shape.BoundBox
assert shell.Shape.isValid() and (round(bb.XLength), round(bb.YLength), round(bb.ZLength)) == (60, 40, 25)
assert shell.Shape.Volume < 60 * 40 * 25 / 2
```

If a feature turns `Invalid`, read `errors` from the tool response or `get_console(level="error")`, then fix the inputs (radius too big, sketch not closed, missing reference) instead of retrying blindly.
