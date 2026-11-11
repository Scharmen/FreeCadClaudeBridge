# Designing for 3D printing (FreeCAD 1.1)

Tested by `tests/gui/test_references.py`. Printers differ, so these are ranges to start from; `check_print` and `check_fit` measure the model against them.

## Contents
- Limits per process
- Gaps between parts, test coupon
- Lids and snap fits
- Hinges
- Orientation and strength
- Details that print better
- Slicing: what the printer would really do
- Meshes: find holes and repair them

## Limits

| | FDM (0.4 mm) | Resin | CNC |
| --- | --- | --- | --- |
| Wall, minimum | 1.2 mm | 0.8 mm | 0.8 mm |
| Smallest detail | 0.4 mm | 0.1–0.2 mm | tool radius |
| Overhang without support | 45° | 30° | n/a |
| Bridge | 10 mm | 5 mm | n/a |
| Press gap | 0–0.1 mm | 0–0.05 mm | 0–0.01 mm |
| Sliding gap | 0.15–0.35 mm | 0.05–0.2 mm | 0.01–0.06 mm |
| Loose gap | 0.3–0.7 mm | 0.2–0.4 mm | 0.06–0.3 mm |

Printed holes come out about 0.1–0.2 mm small; printed pins about right. Sources disagree most about press fits, so print a coupon.

## Test coupon

When a fit matters and the printer is unknown, offer a small coupon with the gap in steps; the user prints it once and tells you which step fits.

```python
pin_d, steps = 8.0, [0.0, 0.1, 0.2, 0.3, 0.4]
plate = Part.makeBox(16 * len(steps), 16, 6)
for i, gap in enumerate(steps):
    plate = plate.cut(Part.makeCylinder((pin_d + 2 * gap) / 2, 8, Vector(8 + 16 * i, 8, -1)))
pin = Part.makeCylinder(pin_d / 2, 12)
doc.addObject("Part::Feature", "Coupon").Shape = plate
doc.addObject("Part::Feature", "Pin").Shape = pin
holes = sorted(round(2 * f.Surface.Radius, 1) for f in plate.Faces if type(f.Surface).__name__ == "Cylinder")
assert holes == [8.0, 8.2, 8.4, 8.6, 8.8] and plate.isValid()
```

## Lids and snap fits

A lid has a **seat** (it rests on the rim, touching is fine) and a **plug or skirt** that locates it, with a sliding gap all round. Check it with `check_fit(a, b, kind="seated")`.

**Cantilever clips:** the arm bends by the undercut `y`. For a rectangular arm the strain is `e = 1.5 * t * y / L²`, so choose the length from the material's allowed strain: PLA 1.5–2 %, PETG 3 %, ABS 4 %, nylon 5 %.

| Rule | Value |
| --- | --- |
| Arm thickness `t` | 1–2 mm (at least 3 perimeters) |
| Arm length `L` | at least `sqrt(1.5 * t * y / e)`, usually 5–15 mm |
| Undercut `y` | 0.5–1.5 mm |
| Hook | 45° lead-in, 90° to stay shut, about 60° to open again |
| Foot of the arm | fillet of at least t/2: this is where it breaks |
| Direction | the arm along the layers (printed lying flat or standing with the arm's length in the XY plane where possible) |
| Room | the arm needs a gap of at least the undercut to bend into |

A box with a seated lid and two working clips, every gap measured:

```python
import math
L, W, H, wall = 60.0, 40.0, 24.0, 1.6
gap, undercut, t_arm, strain = 0.25, 0.8, 1.6, 0.03
arm = math.ceil(math.sqrt(1.5 * t_arm * undercut / strain))      # 8 mm for PETG
box = Part.makeBox(L, W, H).cut(Part.makeBox(L - 2 * wall, W - 2 * wall, H, Vector(wall, wall, wall)))
ledge_top, ledge_h = H - 3, 1.5
for y in (wall - 0.01, W - wall - undercut + 0.01):               # ledges on the inside of the long walls
    box = box.fuse(Part.makeBox(10, undercut + 0.01, ledge_h, Vector(L / 2 - 5, y, ledge_top - ledge_h)))
box = box.removeSplitter()
lid = Part.makeBox(L, W, 6, Vector(0, 0, H))                      # rests on the rim
plug_l, plug_w = L - 2 * wall - 2 * gap, W - 2 * wall - 2 * gap
plug = Part.makeBox(plug_l, plug_w, 2, Vector(wall + gap, wall + gap, H - 2))
lid = lid.fuse(plug)
hook_top = ledge_top - ledge_h - 0.1                              # the hook catches just under the ledge
for y_arm, y_hook in ((wall + undercut + gap, wall + gap), (W - wall - undercut - gap - t_arm, W - wall - undercut - gap)):
    lid = lid.fuse(Part.makeBox(8, t_arm, arm + 3, Vector(L / 2 - 4, y_arm, H - arm - 3)))    # the arm bends inward
    lid = lid.fuse(Part.makeBox(8, undercut + t_arm, hook_top - (H - arm - 3), Vector(L / 2 - 4, min(y_arm, y_hook), H - arm - 3)))
lid = lid.removeSplitter()
b, c = doc.addObject("Part::Feature", "Box"), doc.addObject("Part::Feature", "Lid")
b.Shape, c.Shape = box, lid
assert box.isValid() and lid.isValid() and len(lid.Solids) == 1
assert box.common(lid).Volume < 0.5, "a closed lid must not run into the box"
assert box.distToShape(lid)[0] < 1e-3, "it rests on the rim"
```

Then check it: `check_fit(a="Box", b="Lid", kind="snap")` measures the side gap, and `inspect_views(section={"axis": "x", "offset": 30})` shows the hook behind the ledge.

## Hinges

- **Pin hinge:** alternate knuckles with a separate pin; gap 0.3 mm round the pin and between knuckles, at least 2 mm of wall round the pin.
- **Print-in-place:** cones instead of a pin (the 45° cone prints without support), 0.4 mm gap.
- **Living hinge:** only in polypropylene or nylon, 0.4–0.6 mm thick, flexed once right after printing. In PLA it breaks.

## Orientation and strength

Layers are the weak direction: a printed part is about half as strong across its layers. Lay the part so the load runs along the layers: a hook prints lying down, not standing. Ask `check_print` for overhang and base per orientation, then weigh that against the load.

## Details that print better

- **Elephant foot:** a 0.4–0.5 mm chamfer on the bottom edge, instead of a fillet.
- **Bottom edges:** chamfer rather than fillet (a fillet there overhangs); top edges can have either.
- **Horizontal holes** print with a sagging top: a teardrop keeps them round without support.
- **Text:** raised or sunk at least 0.5 mm deep, strokes at least 0.8 mm wide; on the top or a side face.
- **Long flat parts** warp: rounded corners, a brim, ribs rather than mass.

```python
def teardrop(d, length):
    """A horizontal hole along Y that prints without support: a circle with a 45° roof."""
    r = d / 2
    tip = r * 2 ** 0.5
    side = r * 2 ** 0.5 / 2
    arc = Part.Arc(Vector(side, 0, side), Vector(0, 0, -r), Vector(-side, 0, side)).toShape()
    roof = Part.makePolygon([Vector(-side, 0, side), Vector(0, 0, tip), Vector(side, 0, side)])
    face = Part.Face(Part.Wire([arc, *roof.Edges]))
    return face.extrude(Vector(0, length, 0))

tool = teardrop(6, 20)
block = Part.makeBox(20, 10, 20, Vector(-10, 5, -10)).cut(tool)
assert block.isValid() and len(block.Solids) == 1
assert abs(tool.BoundBox.ZMax - 6 / 2 * 2 ** 0.5) < 1e-6
doc.addObject("Part::Feature", "Teardrop").Shape = block
```

## Meshes: find holes and repair them

A slicer needs a closed mesh. `check_print` reports open meshes; to look or repair yourself, use the mesh's own checks instead of walking facets: `isSolid()`, `hasNonManifolds()`, `hasSelfIntersections()`, `countComponents()`. Walking facets: a facet's neighbours are `NeighbourIndices` (plural; there is no `NeighbourIndex`), `-1` marks an open edge. Repair a copy and assign it back, so a failure leaves the original.

```python
import Mesh

# a mesh with a hole: a box without its last two facets
box = Mesh.createBox(20, 20, 10)
part = doc.addObject("Mesh::Feature", "Printable")
part.Mesh = Mesh.Mesh(box.Facets[:-2])

m = part.Mesh
open_edges = sum(1 for f in m.Facets for n in f.NeighbourIndices if n < 0)   # NeighbourIndices: -1 = open edge
report = {"solid": m.isSolid(), "open_edges": open_edges, "non_manifold": m.hasNonManifolds(),
          "self_intersections": m.hasSelfIntersections(), "pieces": m.countComponents()}
assert report["open_edges"] == 4 and not report["solid"], report

# repair a copy, then put it back: one undo step, the original stays if anything fails
fixed = m.copy()
fixed.harmonizeNormals()
fixed.removeDuplicatedPoints()
fixed.removeDuplicatedFacets()
fixed.fillupHoles(1000)                  # longest hole border to close, in edges
fixed.removeNonManifolds()
part.Mesh = fixed
assert part.Mesh.isSolid() and not part.Mesh.hasNonManifolds()
```

Never call `fixDegenerations`, `fixSelfIntersections`, `fixDeformations` or `fixCaps` with `run_python`: on a real mesh they can run for hours in C++, nothing can interrupt them, and the user's FreeCAD freezes until it is force-quit. `run_python` refuses them. A mesh that still intersects itself after the cheap steps is better rebuilt: mesh the shape again with other deflections, or fix the solid (fuse, `removeSplitter`) and mesh that. If repair is the only way, do it with `run_headless`, which is stopped at its time limit: write the mesh to a file there, repair, save the result and `import` it.

## Slicing: what the printer would really do

`check_print` measures the geometry; `slice_check` prints the model in simulation. It is the bridge's own slicer and needs no slicer program: the model is cut into layers, and from the bed up every layer is checked for what it rests on. Call it when the user aims for a print, after `check_all` passes and before `print_export`: `slice_check(printer="Anycubic Kobra S1")`, optionally `objects=[...]` for one part, `layer`, `nozzle`, `material` (PLA, PETG, ABS, ASA, TPU). Read the answer like this:

- `problems` with "start in mid air" (`findings` of kind `floating`): a part begins with nothing below it. Fix the model: connect it, turn it, or split it at that height (`z`, `at` say where).
- "hang free" (kind `cantilever`, `mm` is how far): an arm or roof sticks out over air. Add a 45° chamfer or gusset under it, turn the part so the flat side lies on the bed, or split it. Up to 5 mm it only droops (a warning).
- kind `bridge`: held on both sides. Up to 10 mm is fine and only counted in `bridges`; longer ones sag: shorten the span, arch it or put a rib below.
- "slopes steeper than 45°": the underside comes out rough; ease the slope or turn the part.
- "thinner than the nozzle": that wall or tip is not printed at all. Make it at least two nozzle widths.
- "does not fit the printer", "small footprint": scale, turn or split; widen the base.
- `with_supports` appears when supports are needed: `extra_g` and `extra_minutes` are their price, `on_model: true` means they stand on the model and leave marks. Prefer fixing the shape; keep supports for shapes that have to stay.
- `minutes` and `filament_g` are estimates at moderate speeds; a fast printer is quicker. Tell the user, and thin or hollow a part that takes far longer than they expect.
- The G-code lies in `print/gcode/` (walls, infill, supports) for a look in a G-code viewer. It is generic and not tuned for the machine; for the real print the user slices the 3MF from `print_export`.
- The model is simulated as it stands, its lowest point on the bed: put it the way it should print first. After a fix, run `slice_check` again.

## Exporting for the printer

When the user wants print files, an assembly kit or an assembly guide, call **`print_export`** (`printer="Anycubic Kobra S1"` or another preset, `mode="whole"` for one coloured print or `"split"` for plates by colour, `join="glue"|"pins"`): it writes the coloured 3MF and STL, moving parts apart and the PDF guide into the model's `print/` folder and answers with the files and warnings. Never write 3MF, zip or slicer project files by hand with `run_python` or the shell: that costs dozens of calls and slicers refuse the result. The same export is the **3D print export** button (File menu, or `3D print…` in the chat panel), which asks the user for the printer and the assembly method. Both read each object's colour and visibility, so give every part the colour it should print in and hide helpers. Name wheels, knobs and rollers as such, keep them round about one axis and separate from their host: the button offers them as moving parts, with axles instead of glue. Keep assembly minimal: one print beats many small parts, so model what belongs together as touching parts and split only for a colour, the bed size or something that moves.
