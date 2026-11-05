# Several parts that belong together (FreeCAD 1.1)

Tested by `tests/gui/test_references.py`. Parts that mate must get their shared sizes from one place. If the lid and the box each have their own numbers, one change breaks the fit and nothing says so.

## Contents
- One set of parameters
- Interfaces: derive, don't retype
- Build in the mounted position
- Assembly joints and checks

## One set of parameters

A VarSet holds the sizes and the gaps; every part uses expressions (`varset-spreadsheet.md`). Name what the size means: `BoxInnerLength`, `LidGap`. Derive the rest, so that a change to one value is one change. `flex_test` then shows that the parts follow.

```python
vs = doc.addObject("App::VarSet", "Params")
for name, value in (("Outer", 60.0), ("Wall", 2.0), ("Gap", 0.25), ("Height", 20.0), ("PlugHeight", 3.0)):
    vs.addProperty("App::PropertyLength", name, "Size")
    setattr(vs, name, value)
outer = doc.addObject("Part::Box", "Outer")
inner = doc.addObject("Part::Box", "Inner")
for o in (outer,):
    o.setExpression("Length", "Params.Outer")
    o.setExpression("Width", "Params.Outer")
    o.setExpression("Height", "Params.Height")
inner.setExpression("Length", "Params.Outer - 2 * Params.Wall")
inner.setExpression("Width", "Params.Outer - 2 * Params.Wall")
inner.setExpression("Height", "Params.Height")
inner.setExpression(".Placement.Base.x", "Params.Wall")
inner.setExpression(".Placement.Base.y", "Params.Wall")
inner.setExpression(".Placement.Base.z", "Params.Wall")
box = doc.addObject("Part::Cut", "Box")
box.Base, box.Tool = outer, inner
plug = doc.addObject("Part::Box", "Lid")
plug.setExpression("Length", "Params.Outer - 2 * Params.Wall - 2 * Params.Gap")
plug.setExpression("Width", "Params.Outer - 2 * Params.Wall - 2 * Params.Gap")
plug.setExpression("Height", "Params.PlugHeight")
plug.setExpression(".Placement.Base.x", "Params.Wall + Params.Gap")
plug.setExpression(".Placement.Base.y", "Params.Wall + Params.Gap")
plug.setExpression(".Placement.Base.z", "Params.Height - Params.PlugHeight")
doc.recompute()
assert abs(box.Shape.distToShape(plug.Shape)[0] - 0.25) < 1e-6
vs.Outer, vs.Gap = 80, 0.3
doc.recompute()
assert abs(box.Shape.distToShape(plug.Shape)[0] - 0.3) < 1e-6, "the fit follows the parameters"
```

## Interfaces: derive, don't retype

Mating features come from one source: hole positions in a shared sketch or a table, used by both parts. Then the holes line up however the size changes. `check_design` confirms it with `aligned_holes`.

```python
corners = [(6, 6), (54, 6), (6, 34), (54, 34)]
def screw_plate(height, hole_d, z):
    plate = Part.makeBox(60, 40, height, Vector(0, 0, z))
    for x, y in corners:
        plate = plate.cut(Part.makeCylinder(hole_d / 2, height + 2, Vector(x, y, z - 1)))
    return plate
base = screw_plate(4, 2.5, 0)        # tapped by the screw
lid = screw_plate(3, 3.4, 4.2)       # clearance for M3
doc.addObject("Part::Feature", "Base").Shape = base
doc.addObject("Part::Feature", "Lid").Shape = lid
assert base.isValid() and lid.isValid() and abs(base.distToShape(lid)[0] - 0.2) < 1e-6
```

## Build in the mounted position

Model each part where it sits in the product, so `check_fit` can measure the real gaps. For printing, place copies or export each part alone; never move the mounted original just to print it. To show a product open, use a parameter (`LidLift = 10`) rather than editing placements.

## Assembly joints and checks

Use the Assembly workbench when parts move (`assembly.md`). After every change:

1. `check_fit` for each pair that meets, in the mounted position and, for moving parts, at the end positions too.
2. `check_design` with `no_interference` for the whole product.
3. `inspect_views` with a `section` through the joint to see the gaps.

A pin hinge's gaps are all in one place: gap round the pin, gap between knuckles.

```python
knuckle_r, pin_r, gap, length = 3.0, 1.5, 0.3, 40.0
n, each = 3, (length - 2 * gap) / 3
def knuckle(z, h):
    return Part.makeCylinder(knuckle_r, h, Vector(0, 0, z)).cut(Part.makeCylinder(pin_r + gap, h + 2, Vector(0, 0, z - 1)))
leaf_a = knuckle(0, each).fuse(knuckle(2 * (each + gap), each))
leaf_b = knuckle(each + gap, each)
pin = Part.makeCylinder(pin_r, length)
for name, shape in (("LeafA", leaf_a), ("LeafB", leaf_b), ("Pin", pin)):
    doc.addObject("Part::Feature", name).Shape = shape
assert abs(leaf_a.distToShape(leaf_b)[0] - gap) < 1e-6
assert abs(pin.distToShape(leaf_a)[0] - gap) < 1e-6 and abs(pin.distToShape(leaf_b)[0] - gap) < 1e-6
```
