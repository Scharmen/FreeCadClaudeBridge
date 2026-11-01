# Assembly workbench (FreeCAD 1.1)

Tested by `tests/gui/test_references.py`. An assembly is an `Assembly::AssemblyObject` with `Type = "Assembly"` and a `Joints` group. Parts live inside it (directly, or as `App::Link` to parts in other documents). Ground exactly one part, then connect the others with joints and call `solve()`.

Joint references point at the **part** and a list `[element, vertex-or-element]`; never at the assembly (that creates a dependency cycle: "The graph must be a DAG"). The second entry picks the joint's origin: a face's center when it repeats the face, a circle's center for a circular edge.

```python
import JointObject
asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
asm.Type = "Assembly"
joints = asm.newObject("Assembly::JointGroup", "Joints")
base = asm.newObject("Part::Box", "Base"); base.Length, base.Width, base.Height = 60, 40, 10
pin = asm.newObject("Part::Cylinder", "Pin"); pin.Radius, pin.Height = 5, 30
pin.Placement.Base = Vector(100, 50, 20)  # anywhere; the solver moves it
doc.recompute()
ground = joints.newObject("App::FeaturePython", "GroundedBase")
JointObject.GroundedJoint(ground, base)
bottom = next(f"Edge{i + 1}" for i, e in enumerate(pin.Shape.Edges)
              if hasattr(e.Curve, "Radius") and abs(e.Curve.Center.z - pin.Placement.Base.z) < 1e-6)
top_face = next(f"Face{i + 1}" for i, f in enumerate(base.Shape.Faces) if abs(f.CenterOfMass.z - 10) < 1e-6)
rev = joints.newObject("App::FeaturePython", "PinJoint")
JointObject.Joint(rev, JointObject.JointTypes.index("Revolute"))
rev.Proxy.setJointConnectors(rev, [[base, [top_face, top_face]], [pin, [bottom, bottom]]])
assert asm.solve() == 0
assert [round(x, 6) for x in pin.Placement.Base] == [30, 20, 10]
```

Joint types (`JointObject.JointTypes`): Fixed, Revolute, Cylindrical, Slider, Ball, Distance, Parallel, Perpendicular, Angle, RackPinion, Screw, Gears, Belt. Distance-type joints take `joint.Distance` (and `Distance2` for some types).

A third part with a Fixed joint, stacked on the pin's top face:

```python
import JointObject
asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
asm.Type = "Assembly"
joints = asm.newObject("Assembly::JointGroup", "Joints")
base = asm.newObject("Part::Box", "Base")
cap = asm.newObject("Part::Cylinder", "Cap"); cap.Radius, cap.Height = 8, 4
cap.Placement.Base = Vector(50, 50, 50)
doc.recompute()
JointObject.GroundedJoint(joints.newObject("App::FeaturePython", "Grounded"), base)
top = next(f"Face{i + 1}" for i, f in enumerate(base.Shape.Faces) if abs(f.CenterOfMass.z - 10) < 1e-6)
under = next(f"Face{i + 1}" for i, f in enumerate(cap.Shape.Faces)
             if type(f.Surface).__name__ == "Plane" and abs(f.CenterOfMass.z - 50) < 1e-6)
fix = joints.newObject("App::FeaturePython", "CapFixed")
JointObject.Joint(fix, JointObject.JointTypes.index("Fixed"))
fix.Proxy.setJointConnectors(fix, [[base, [top, top]], [cap, [under, under]]])
assert asm.solve() == 0
assert [round(x, 6) for x in cap.Placement.Base] == [5, 5, 10]  # BoundBox of curved shapes is approximate
```

GUI alternative (same result, useful when the Python API is unclear): activate the assembly with `Gui.ActiveDocument.setEdit(asm)`, `selection(action="set", items=[{"object": "Assembly", "sub": ["Base.Face6", "Pin.Edge2"]}])`, `run_command("Assembly_CreateJointRevolute")`, then `task_dialog(action="accept")`. Leave edit mode with `Gui.ActiveDocument.resetEdit()`.
