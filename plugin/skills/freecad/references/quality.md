# Model quality from the start (FreeCAD 1.1)

Tested by `tests/gui/test_references.py`. Read this before a model the user will judge by its look or will print.

## Workflow that gets it right the first time

1. **Pin down the brief.** Purpose (print, render, assembly), overall size and which dimension it means ("5 cm high" = the whole thing including the base), material and printer if it is printed, level of detail. Ask once if it is unclear; do not guess the size.
2. **Get a reference.** If the user has a picture, look at it (`image_to_3d` mode `show`) and place it as an image plane (mode `reference`) to model against. Otherwise describe the proportions you are going for in one line before building.
3. **Block out first.** Build the big masses with few, simple shapes and check the proportions from the front, the side and the top (`view` with a screenshot) before adding any detail. Wrong proportions cannot be fixed with detail.
4. **Refine, then detail; detail is what makes it real.** List every feature first (see "Details make the model" in the skill) and work down the list. Secondary forms (muscles, folds, chamfers), then tertiary detail (texture, small features). Keep the detail size proportional: on a 5 cm figure 0.2–0.5 mm surface detail reads well; a printed detail below the nozzle width (0.4 mm FDM) disappears.
5. **Check after every step,** and against the user's picture when there is one (`compare_to_picture`, see the skill). `changes` and `errors` in each result; `get_object` for real sizes; one undo step per call, so bundle a stage into one call.
6. **Show, explain and ask.** A screenshot from a good angle, why it looks like this (main decisions with reasons and sources, assumptions marked; also recorded with `design_notes`), then ask whether it matches (the panel asks with 👍/👎).

## Robust parametric models

- **Sketches:** attach them to the Body's origin planes or to datum planes, not to faces of earlier features (face numbers change when the model changes). Fully constrain every sketch (`inspect_sketch` shows the degrees of freedom); an under-constrained sketch may flip when something changes.
- **Independent features:** let each sketch hang on a datum, not on the previous pad, so one can change without breaking the next. Avoid links to external geometry where a datum works.
- **Key dimensions in one place:** a VarSet or Spreadsheet with expressions (see `varset-spreadsheet.md`), so "make it 10 % bigger" is one change.
- **Names:** meaningful `Label`s (Base, Handle, Hole_M6) so the user and later calls find things.
- **Check validity:** `shape.isValid()` and `errors` after booleans and fillets; a fillet that fails usually needs a smaller radius or a different edge order.

## A realistic look in the 3D view

FreeCAD shows curved faces as triangles; the defaults look faceted on organic shapes. Finer tessellation, a material with some gloss and sensible colors make a model read as real. Meshes get smooth shading with `CreaseAngle`.

```python
box = doc.addObject("Part::Box", "Block")
cyl = doc.addObject("Part::Cylinder", "Knob")
cyl.Radius, cyl.Height = 6, 8
cyl.Placement.Base = Vector(5, 5, 10)
doc.recompute()
for o in (box, cyl):
    vo = o.ViewObject
    vo.Deviation = 0.05              # % of the object's size; default 0.5. Smaller = rounder display and STL
    vo.AngularDeflection = 8         # degrees between facets on curves
mat = App.Material()
mat.DiffuseColor = (0.62, 0.45, 0.22)   # bronze
mat.SpecularColor = (0.55, 0.5, 0.4)
mat.Shininess = 0.55
cyl.ViewObject.ShapeAppearance = [mat]
assert abs(cyl.ViewObject.ShapeAppearance[0].Shininess - 0.55) < 1e-3
assert cyl.ViewObject.Deviation < 0.1
```

Use gloss only for metal, glass, glazed or wet surfaces; skin, hair, cloth, wood and stone look real when matte (Shininess 0, SpecularColor black), and glossy they read as plastic. Colors that read as real are rarely pure: skin around (0.85, 0.65, 0.52), wood (0.55, 0.36, 0.2), bronze (0.62, 0.45, 0.22), stone (0.55, 0.53, 0.5), steel (0.7, 0.72, 0.75) with high shininess. Give separate parts separate colors (hair, skin, clothes, base).

## Printable by design

- Wall thickness at least 1.2 mm (FDM) or 0.8 mm (resin); pins and thin limbs at least 2 mm thick at the target scale.
- A flat base or plinth for figures; overhangs beyond 45° need supports, so lean shapes against the body.
- One closed solid or watertight mesh per part; check `isSolid()` on meshes and `isValid()` on shapes before `export`.
