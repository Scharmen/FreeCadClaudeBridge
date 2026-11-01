# Part workbench (FreeCAD 1.1)

Every `python` block below is run against FreeCAD 1.1 by `tests/gui/test_references.py` in a fresh document (`doc`).

Primitives, placement and booleans. Booleans keep their inputs; hide them for a clean view.

```python
box = doc.addObject("Part::Box", "Block")
box.Length, box.Width, box.Height = 40, 30, 10
hole = doc.addObject("Part::Cylinder", "Hole")
hole.Radius, hole.Height = 5, 10
hole.Placement.Base = Vector(20, 15, 0)
cut = doc.addObject("Part::Cut", "BlockWithHole")
cut.Base, cut.Tool = box, hole
doc.recompute()
box.Visibility = hole.Visibility = False
assert abs(cut.Shape.Volume - (40 * 30 * 10 - 3.14159265 * 25 * 10)) < 0.01
```

Fuse several shapes, then fillet selected edges of the result. Find edges by geometry, not by number: numbers change when the model changes.

```python
a = doc.addObject("Part::Box", "A"); a.Length = a.Width = a.Height = 20
b = doc.addObject("Part::Box", "B"); b.Length = b.Width = b.Height = 20
b.Placement.Base = Vector(10, 10, 10)
fuse = doc.addObject("Part::MultiFuse", "Fusion"); fuse.Shapes = [a, b]
doc.recompute()
vertical = [i + 1 for i, e in enumerate(fuse.Shape.Edges)
            if abs(e.Vertexes[0].Point.z - e.Vertexes[-1].Point.z) > 1e-6
            and abs(e.Vertexes[0].Point.x - e.Vertexes[-1].Point.x) < 1e-6
            and abs(e.Vertexes[0].Point.y - e.Vertexes[-1].Point.y) < 1e-6]
fil = doc.addObject("Part::Fillet", "Rounded")
fil.Base = fuse
fil.Edges = [(i, 2.0, 2.0) for i in vertical]
doc.recompute()
assert fil.Shape.isValid() and "Invalid" not in fil.State
```

Shapes from code (no document object until you add one): `Part.makeBox`, `makeCylinder`, `makeSphere`, `makeCone`, `makeTorus`; booleans with `a.cut(b)`, `a.fuse(b)`, `a.common(b)`.

```python
s = Part.makeBox(10, 10, 10).cut(Part.makeCylinder(3, 10, Vector(5, 5, 0)))
feat = doc.addObject("Part::Feature", "Drilled")
feat.Shape = s
doc.recompute()
assert len(feat.Shape.Faces) == 7
```

Extrude a closed wire and revolve a profile.

```python
w = Part.makePolygon([Vector(0, 0, 0), Vector(20, 0, 0), Vector(20, 5, 0), Vector(5, 5, 0), Vector(5, 20, 0),
                      Vector(0, 20, 0), Vector(0, 0, 0)])
angle = doc.addObject("Part::Feature", "Angle")
angle.Shape = Part.Face(w).extrude(Vector(0, 0, 30))
prof = Part.Face(Part.makePolygon([Vector(10, 0, 0), Vector(15, 0, 0), Vector(15, 0, 8), Vector(10, 0, 8),
                                   Vector(10, 0, 0)]))
ring = doc.addObject("Part::Feature", "Ring")
ring.Shape = prof.revolve(Vector(0, 0, 0), Vector(0, 0, 1), 360)
doc.recompute()
assert angle.Shape.isValid() and abs(ring.Shape.Volume - 3.14159265 * (15**2 - 10**2) * 8) < 0.1
```
