# Parameters: VarSet and Spreadsheet (FreeCAD 1.1)

Tested by `tests/gui/test_references.py`. Put key dimensions in one place and drive features with expressions, so the user can change one value and everything follows.

VarSet (new in 1.0): named, typed properties grouped in one object. Expressions reference them as `<VarSetName>.<Property>`.

```python
vs = doc.addObject("App::VarSet", "Params")
vs.addProperty("App::PropertyLength", "Diameter", "Flange", "Outer diameter")
vs.addProperty("App::PropertyLength", "Thickness", "Flange", "Plate thickness")
vs.addProperty("App::PropertyInteger", "Holes", "Flange", "Number of bolt holes")
vs.Diameter, vs.Thickness, vs.Holes = 80, 8, 6
cyl = doc.addObject("Part::Cylinder", "Plate")
cyl.setExpression("Radius", "Params.Diameter / 2")
cyl.setExpression("Height", "Params.Thickness")
doc.recompute()
assert abs(cyl.Radius.Value - 40) < 1e-9
vs.Diameter = 100
doc.recompute()
assert abs(cyl.Radius.Value - 50) < 1e-9
assert ("Radius", "Params.Diameter / 2") in cyl.ExpressionEngine
```

Spreadsheet: cells with aliases. Values with units are written as strings starting with `=`. Aliases must not clash with unit symbols (`L`, `m`, `h`, `s`, … are rejected).

```python
sh = doc.addObject("Spreadsheet::Sheet", "Sheet")
sh.set("A1", "Length"); sh.set("B1", "=40 mm"); sh.setAlias("B1", "Len")
sh.set("A2", "Width"); sh.set("B2", "=Len * 0.75"); sh.setAlias("B2", "Wid")
doc.recompute()
box = doc.addObject("Part::Box", "Box")
box.setExpression("Length", "Sheet.Len")
box.setExpression("Width", "Sheet.Wid")
doc.recompute()
assert abs(box.Width.Value - 30) < 1e-9
```

Expressions inside a sketch: name a constraint, then drive it (`.Constraints.<name>`).

```python
vs = doc.addObject("App::VarSet", "P")
vs.addProperty("App::PropertyLength", "R", "Base")
vs.R = 12
sk = doc.addObject("Sketcher::SketchObject", "S")
c = sk.addGeometry(Part.Circle(Vector(0, 0, 0), Vector(0, 0, 1), 5))
sk.addConstraint(Sketcher.Constraint("Coincident", c, 3, -1, 1))
r = sk.addConstraint(Sketcher.Constraint("Radius", c, 5))
sk.renameConstraint(r, "r")
sk.setExpression("Constraints.r", "P.R")
doc.recompute()
assert abs(sk.Geometry[0].Radius - 12) < 1e-9 and sk.DoF == 0
```
