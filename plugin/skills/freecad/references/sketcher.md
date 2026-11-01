# Sketcher (FreeCAD 1.1)

Tested by `tests/gui/test_references.py`. Aim for 0 degrees of freedom; check with `inspect_sketch` or `sk.solve()` and `sk.DoF`.

Point positions in constraints: 1 = start, 2 = end, 3 = center. Geometry -1 is the X axis, -2 the Y axis, -1 with position 1 the origin.

Fully constrained rectangle with its corner on the origin.

```python
sk = doc.addObject("Sketcher::SketchObject", "Rect")
p = [Vector(0, 0, 0), Vector(30, 0, 0), Vector(30, 20, 0), Vector(0, 20, 0)]
for i in range(4):
    sk.addGeometry(Part.LineSegment(p[i], p[(i + 1) % 4]))
for i in range(4):
    sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
sk.addConstraint(Sketcher.Constraint("Horizontal", 0)); sk.addConstraint(Sketcher.Constraint("Horizontal", 2))
sk.addConstraint(Sketcher.Constraint("Vertical", 1)); sk.addConstraint(Sketcher.Constraint("Vertical", 3))
sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 30))
sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, 20))
sk.addConstraint(Sketcher.Constraint("Coincident", 0, 1, -1, 1))
assert sk.solve() == 0 and sk.DoF == 0 and sk.FullyConstrained
```

Centered circle with a named, driving diameter (rename lets expressions refer to `Rect.Constraints.d`).

```python
sk = doc.addObject("Sketcher::SketchObject", "Circ")
c = sk.addGeometry(Part.Circle(Vector(0, 0, 0), Vector(0, 0, 1), 10))
sk.addConstraint(Sketcher.Constraint("Coincident", c, 3, -1, 1))
d = sk.addConstraint(Sketcher.Constraint("Diameter", c, 25))
sk.renameConstraint(d, "d")
sk.setDatum("d", App.Units.Quantity("30 mm"))
assert sk.solve() == 0 and sk.DoF == 0 and abs(sk.Geometry[0].Radius - 15) < 1e-9
```

Slot (two arcs and two tangent lines) with symmetric constraints.

```python
sk = doc.addObject("Sketcher::SketchObject", "Slot")
import math
a1 = sk.addGeometry(Part.ArcOfCircle(Part.Circle(Vector(-10, 0, 0), Vector(0, 0, 1), 4), math.pi / 2, 3 * math.pi / 2))
a2 = sk.addGeometry(Part.ArcOfCircle(Part.Circle(Vector(10, 0, 0), Vector(0, 0, 1), 4), -math.pi / 2, math.pi / 2))
l1 = sk.addGeometry(Part.LineSegment(Vector(-10, 4, 0), Vector(10, 4, 0)))
l2 = sk.addGeometry(Part.LineSegment(Vector(-10, -4, 0), Vector(10, -4, 0)))
sk.addConstraint(Sketcher.Constraint("Tangent", a1, 1, l1, 1))
sk.addConstraint(Sketcher.Constraint("Tangent", a2, 2, l1, 2))
sk.addConstraint(Sketcher.Constraint("Tangent", a1, 2, l2, 1))
sk.addConstraint(Sketcher.Constraint("Tangent", a2, 1, l2, 2))
sk.addConstraint(Sketcher.Constraint("Equal", a1, a2))
sk.addConstraint(Sketcher.Constraint("Horizontal", l1))
sk.addConstraint(Sketcher.Constraint("Radius", a1, 4))
sk.addConstraint(Sketcher.Constraint("Symmetric", a1, 3, a2, 3, -1, 1))
sk.addConstraint(Sketcher.Constraint("DistanceX", a1, 3, a2, 3, 20))
assert sk.solve() == 0 and sk.DoF == 0
```

Diagnose a broken sketch: conflicting and redundant constraints are listed by 1-based constraint index.

```python
sk = doc.addObject("Sketcher::SketchObject", "Bad")
sk.addGeometry(Part.LineSegment(Vector(0, 0, 0), Vector(10, 0, 0)))
sk.addConstraint(Sketcher.Constraint("Horizontal", 0))
sk.addConstraint(Sketcher.Constraint("Distance", 0, 10))
sk.addConstraint(Sketcher.Constraint("Distance", 0, 12))
sk.solve()
assert sk.ConflictingConstraints or sk.RedundantConstraints
sk.delConstraint(2)
assert sk.solve() == 0 and not sk.ConflictingConstraints
```
