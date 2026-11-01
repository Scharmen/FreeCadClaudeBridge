# Export, import and headless work (FreeCAD 1.1)

Tested by `tests/gui/test_references.py`.

Prefer the tools: `export(path, format, objects)` (STEP, IGES, BREP, STL, OBJ, 3MF) and `import(path)` (STEP, IGES, BREP, STL, OBJ, 3MF, PLY, FCStd). PDF/SVG drawings: see `techdraw.md`.

In code, STEP/IGES go through `Import`, meshes through `Mesh`. Control mesh fineness with `MeshPart` for STL.

```python
import os, tempfile, Import, Mesh, MeshPart
box = doc.addObject("Part::Cylinder", "Cyl"); box.Radius, box.Height = 10, 20
doc.recompute()
d = tempfile.mkdtemp()
Import.export([box], os.path.join(d, "cyl.step"))
mesh = MeshPart.meshFromShape(Shape=box.Shape, LinearDeflection=0.05, AngularDeflection=0.3, Relative=False)
mesh.write(os.path.join(d, "cyl_fine.stl"))
coarse = MeshPart.meshFromShape(Shape=box.Shape, LinearDeflection=1.0, AngularDeflection=0.5, Relative=False)
assert mesh.CountFacets > coarse.CountFacets > 0
assert os.path.getsize(os.path.join(d, "cyl.step")) > 1000
```

Read a STEP file into the document and check what came in.

```python
import os, tempfile, Import
src = doc.addObject("Part::Box", "Src"); doc.recompute()
path = os.path.join(tempfile.mkdtemp(), "in.step")
Import.export([src], path)
before = {o.Name for o in doc.Objects}
Import.insert(path, doc.Name)
new = [o for o in doc.Objects if o.Name not in before]
assert new and all(o.Shape.isValid() for o in new if hasattr(o, "Shape"))
```

Mesh to solid (for STL input that needs CAD operations): sew the mesh into a shape, then make a solid.

```python
import Mesh
m = Mesh.createBox(10, 10, 10)
shape = Part.Shape()
shape.makeShapeFromMesh(m.Topology, 0.05)
solid = Part.makeSolid(shape).removeSplitter()
feat = doc.addObject("Part::Feature", "FromMesh"); feat.Shape = solid
doc.recompute()
assert abs(feat.Shape.Volume - 1000) < 1e-6 and len(feat.Shape.Faces) == 6
```

`run_headless(script)`: no GUI, no open documents. Load and save by path, write output into `WORKDIR`, return data via `_result`. Use it for heavy booleans/fillets on big models and batch conversions, so the user's FreeCAD stays responsive:

```text
import FreeCAD as App, Part, Import, os
doc = App.openDocument("/path/to/model.FCStd")
shape = doc.getObject("Body").Shape
Import.export([doc.getObject("Body")], os.path.join(WORKDIR, "model.step"))
_result = {"volume": shape.Volume, "step": os.path.join(WORKDIR, "model.step")}
```
