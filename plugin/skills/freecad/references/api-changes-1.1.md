# API notes for FreeCAD 1.0/1.1

Verified against FreeCAD 1.1.4 while building this plugin. When unsure, call `api_help` instead of guessing.

- **Sketch attachment:** `sk.AttachmentSupport = [(plane, "")]` plus `sk.MapMode = "FlatFace"`. The 0.21 name `Support` is gone.
- **Origin features:** find planes and axes by role, e.g. `next(f for f in body.Origin.OriginFeatures if f.Role == "XY_Plane")`; the object names (`XY_Plane`, `XY_Plane001`, …) differ per body.
- **Sketch analysis:** `sk.DoF`, `sk.FullyConstrained`, `sk.ConflictingConstraints`, `sk.RedundantConstraints`, `sk.PartiallyRedundantConstraints`, `sk.MalformedConstraints` exist; `sk.solve()` returns 0 on success. Constraint indices in these lists are 1-based.
- **PartDesign patterns:** `body.newObject("PartDesign::PolarPattern"|"LinearPattern"|"Mirrored", …)` doesn't move the Body's `Tip` to the pattern; set `body.Tip = pattern` before adding the next feature, or that feature builds on the unpatterned shape.
- **Spreadsheet aliases** can't be unit symbols (`L`, `m`, `h`, …): `setAlias("B1", "L")` raises "Invalid alias".
- **VarSet:** `App::VarSet` is the recommended home for parameters (`addProperty(type, name, group, doc)`), next to Spreadsheet.
- **Assembly:** joints are `App::FeaturePython` objects set up with `JointObject.Joint(obj, type_index)` and `obj.Proxy.setJointConnectors(obj, refs)`; references point at parts, not the assembly. Set `assembly.Type = "Assembly"`.
- **Toponaming:** 1.0 added topological naming mitigation, but face/edge numbers can still shift when upstream features change. Pick elements by geometry (center, normal, radius) at the moment you use them, and attach sketches to origin or datum planes.
- **Quantities:** properties like `Length` are `Quantity` objects; compare `.Value` (mm) or use `App.Units.Quantity("30 mm")`.
- **Documents:** `App.newDocument(name, hidden=True, temp=True)` creates an invisible scratch document.
- **Undo:** FreeCAD keeps about 20 undo steps by default (preference `MaxUndoSize`). Each bridge call is one step; bundle related changes.
- **Report view:** messages printed during a call appear there only after the call has finished; read them with `get_console` on the next call.

These notes are plain text; they're checked by review, not by `tests/gui/test_references.py`.
