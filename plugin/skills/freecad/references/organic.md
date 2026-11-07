# Organic shapes that look real (FreeCAD 1.1)

Tested by `tests/gui/test_references.py`. FreeCAD is a CAD program, not a sculpting tool: there is no clay brush. What works:

| Subject | Technique |
| --- | --- |
| Figures, faces, animals, hair, beards, cloth, plants, rocks, terrain | `sculpt` tool: smoothly blended primitives → watertight mesh (below) |
| Smooth products: bottles, handles, grips, shells, vases | Loft through closed B-spline sections; tapered sweep |
| Freeform panels, reliefs, terrain patches | B-spline surface from a point grid, thickened |
| Coins, plaques, lithophanes from a picture | `image_to_3d` mode `relief` |
| Mechanical parts | PartDesign (see `partdesign.md`) |

## Contents
- People and animals: start from a template
- The `sculpt` tool: rules and checks
- Smooth CAD forms: lofts, sweeps, B-spline surfaces
- Meshes built safely

## People and animals: start from a template

Never build a person or an animal from scratch or from Part primitives. The `sculpt` tool has tested templates with correct proportions, faces and fur; scale one to the height you need and change it:

| Template | What it is | Parts |
| --- | --- | --- |
| `person_standing` | adult, 7.5 heads tall, arms relaxed, T-shirt, jeans, sneakers, short hair, round base | Skin, Shirt, Jeans, Shoes_R, Shoes_L, Hair, Base |
| `dog_standing` | Labrador-type dog: deep chest, tucked belly, stop, hanging ears, otter tail, short fur, oval base | Dog, Base |
| `cat_sitting` | sitting cat, paws together, tail around the side, fur flowing down and back | Cat |

```json
{"template": "person_standing", "height": 80,
 "parts": [
  {"name": "Shirt", "color": [0.75, 0.1, 0.12]},
  {"name": "Hair", "color": [0.85, 0.7, 0.35], "fur": {"depth": 0.15, "length": 4, "width": 0.5, "from": [0, 0.5, 81]}},
  {"name": "Cap", "color": [0.1, 0.1, 0.1], "primitives": [{"type": "ellipsoid", "center": [0, 0.5, 78], "radii": [4.3, 4.9, 2.6]}]}]}
```

- **Coordinates:** base on z = 0, the figure faces -Y, X is its left-right axis, Z up. Primitives you add are in the final size (after scaling), so read `size_mm` and the screenshots first, then place them.
- **Change, don't restart:** recolor parts, add a beard (strands on Skin), a jacket (a new part over Shirt), a different pose (new capsules for the arms; hide the old ones by overriding the part). Another animal: change the closest template (a horse or a fox from the dog: longer legs or a pointed muzzle) rather than a new blob.
- **Fur and hair** give the look: `"fur": {"depth", "length", "width", "direction" | "from"}` carves grooves that run along the flow (down and back on an animal's body, out from the crown on a head). Short coat: depth 0.1–0.15 mm, width 0.6–0.8 mm on a 5 cm figure; long fur: depth 0.3, width 1, length 8–10. Depth is capped at 0.3 × width so the surface stays clean.
- **Check the face up close:** `view` with a screenshot zoomed on the head; eyes, nose and mouth must read at the printed size.

**One piece:** a template's parts touch and print together. To join them, put all their primitives into one part in the `sculpt` scene. Do not fuse the finished meshes: that leaves open, invalid meshes and costs many minutes.

## The `sculpt` tool

Describe the form as primitives that melt into each other with a blend radius, like clay. It runs outside FreeCAD and imports a smooth, watertight mesh per part (one undo step), so it cannot crash FreeCAD.

```json
{"blend": 1.2, "detail": {"amplitude": 0.15, "scale": 1.5},
 "parts": [
  {"name": "Body", "color": [0.85, 0.65, 0.52], "primitives": [
    {"type": "ellipsoid", "center": [0, 0, 30], "radii": [7, 4.5, 9]},
    {"type": "ellipsoid", "center": [0, -0.5, 44], "radii": [3.4, 3.8, 4.4]},
    {"type": "capsule", "a": [0, 0, 38], "b": [0, -0.3, 41], "radius": 1.6},
    {"type": "capsule", "a": [-6, 0, 36], "b": [-9, -2, 27], "radius": 1.9, "radius_b": 1.5},
    {"type": "capsule", "a": [-9, -2, 27], "b": [-8, -5, 20], "radius": 1.5, "radius_b": 1.1},
    {"type": "sphere", "center": [-1.3, -3.6, 44.5], "radius": 0.7, "op": "subtract", "blend": 0.4}]},
  {"name": "Hair", "color": [0.45, 0.3, 0.18], "blend": 0.6, "detail": {"amplitude": 0.08, "scale": 0.6},
   "primitives": [
    {"type": "strand", "points": [[0, 1, 48.5], [0.5, 3.5, 46], [0.8, 4.5, 41], [0.5, 4.8, 36]],
     "radius": 1.2, "radius_end": 0.35}]}]}
```

Grammar (mm; every primitive may set `"op": "add" | "subtract" | "intersect"` and its own `"blend"`, 0 = hard edge):

| Primitive | Keys |
| --- | --- |
| `sphere` | `center`, `radius` |
| `ellipsoid` | `center`, `radii: [rx, ry, rz]`, `rotate: [rx, ry, rz]` degrees |
| `capsule` | `a`, `b`, `radius`, `radius_b` (tapered limb, finger) |
| `strand` | `points: [[x, y, z], ...]`, `radius`, `radius_end` (hair lock, tail, beard, vine) |
| `box` | `center`, `size`, `round`, `rotate` |
| `torus` | `center`, `radius`, `thickness`, `rotate` |
| `cylinder` | `a`, `b`, `radius` |

Scene and part keys: `blend` (mm), `color: [r, g, b]` 0..1, `shininess` 0..1 (default 0, matte; only metal or wet parts get 0.3–0.6), `detail: {"amplitude": mm, "scale": mm, "seed"}` skin or stone texture, `fur: {"depth": mm, "length": mm, "width": mm, "direction": [x, y, z] or "from": [x, y, z], "seed"}` grooves along the flow (depth up to 0.3 × width; `"fur": null` switches a scene-wide fur off for one part). Tool arguments: `voxel_mm` (default 1/300 of the scene, the same for every part), `smooth` Taubin passes (default 4, 2 with fur), `preview` (default true: a front and side picture comes with the answer).

Rules that make it look real instead of plastic:

- **Proportions first.** Adult figure 7.5–8 heads tall (heroic 8–8.5, child 5–6); shoulders about 2 head widths; the eyes sit halfway down the head; the hand reaches mid-thigh; the elbow is at the waist. Animals: work from the skeleton masses (skull, rib cage, pelvis) outwards.
- **Ellipsoids and tapered capsules, not spheres.** Equal spheres in a row read as bubbles. Limbs taper (`radius_b`); muscles are flattened ellipsoids rotated along the limb.
- **Hair, beards, fur: many tapered strands, plus `fur` grooves along them.** 20–80 strands with 3–5 points each, thick at the root and fine at the tip (`radius_end` ≈ 0.3 × `radius`), slightly different lengths and curves, in overlapping layers and following the head's shape; a small blend (0.3–0.8 mm) so they merge into locks; a little detail noise along them.
- **Faces:** subtract the eye sockets and the mouth line with a small blend; add brow ridge, cheekbones, nose and chin as small ellipsoids. Break the symmetry slightly; real faces are not mirror-perfect.
- **Cloth:** folds as long thin capsules added (ridges) and subtracted (valleys) along the direction of pull; hems as a torus or a strand.
- **Surface detail scales with the object:** `detail.amplitude` 0.1–0.3 % of the size for skin, 1–3 % for stone or bark; `scale` is the feature size. Give each material its own part, color and detail.
- **Resolution:** default 1/300 of the whole scene, the same for every part (0.2 mm on a 6 cm figure); set `voxel_mm` to roughly a third of the finest feature. The finest allowed is 1/400 of the scene.
- **Iterate:** block out with a few primitives, look at it from front and side, then add.
- **Matte by default.** Sculpted parts get no gloss; gloss makes skin, hair and cloth look like plastic. Give only metal or wet parts `"shininess"` (0.3–0.6).

Check after every sculpt, before showing it (each of these broke a first attempt):

- **No `part_warnings`.** "separate pieces" means a strand or fold floats in the air: move it into the body.
- **Parts overlap what they sit on.** Clothes reach into the neck and the base, the helmet into the head, hair roots start inside the scalp. A gap of one voxel shows as a crack.
- **Added details stay inside the body's silhouette.** A fold or muscle that sticks out reads as a growth; ridges are thin and lie on the surface.
- **Details follow the surface.** Put strand points on the surface they run along (crests, hems, hair over the skull), not a straight capsule between two far points.
- **Depth matches width.** A torso is about half as deep as it is wide, a head about as deep as high; an egg-shaped chest is the typical mistake.
- **Grooves are at least 2–3 voxels deep and wide,** or the smoothing erases them.
- **Look from the front and from the side** (`view` with a screenshot), not only from the angle you modelled in.

## Smooth CAD forms in FreeCAD

**Loft through closed B-spline sections.** Same point count on every section, start points aligned (all start at angle 0), `ruled=False` for smooth transitions; `maxDegree` 3 keeps it from overshooting.

```python
import math


def section(z, rx, ry, wobble=0.0, phase=0.0, n=24):
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        k = 1 + wobble * math.sin(3 * a + phase)
        pts.append(Vector(rx * k * math.cos(a), ry * k * math.sin(a), z))
    curve = Part.BSplineCurve()
    curve.interpolate(pts, PeriodicFlag=True)
    return Part.Wire(curve.toShape())


profiles = [section(0, 14, 10), section(25, 18, 13, 0.04), section(55, 11, 9, 0.06, 0.5),
            section(80, 16, 12, 0.03), section(95, 6, 6)]
vase = Part.makeLoft(profiles, True, False, False, 3)   # solid, ruled, closed, maxDegree
obj = doc.addObject("Part::Feature", "Vase")
obj.Shape = vase
assert vase.isValid() and len(vase.Solids) == 1 and vase.Volume > 1000
```

**Tapered sweep** for horns, tails, handles and limbs as solids: a circle at each end of a B-spline path.

```python
path = [Vector(0, 0, 0), Vector(10, 0, 20), Vector(30, 0, 35), Vector(55, 0, 38)]
spine = Part.BSplineCurve()
spine.interpolate(path)
wire = Part.Wire(spine.toShape())
start = Part.Wire(Part.Circle(path[0], spine.tangent(spine.FirstParameter)[0], 8).toShape())
tip = Part.Wire(Part.Circle(path[-1], spine.tangent(spine.LastParameter)[0], 1.5).toShape())
horn = wire.makePipeShell([start, tip], True, True)     # solid, Frenet
doc.addObject("Part::Feature", "Horn").Shape = horn
assert horn.isValid() and horn.Volume > 0
```

**B-spline surface from a grid**, thickened into a solid (terrain, a freeform panel, a relief).

```python
import math

grid = [[Vector(x * 10, y * 10, 3 * math.sin(x * 0.8) * math.cos(y * 0.6)) for y in range(7)] for x in range(9)]
surf = Part.BSplineSurface()
surf.interpolate(grid)
panel = surf.toShape().makeOffsetShape(2.0, 0.01, fill=True)
doc.addObject("Part::Feature", "Panel").Shape = panel
assert panel.isValid() and panel.Volume > 0
```

## Meshes, safely

Build a mesh from triangles given as **coordinates** (FreeCAD builds the topology itself) or read a file with `Mesh.Mesh(path)`. Never hand raw point/index lists to the C++ API: one bad index crashed FreeCAD (SIGSEGV in `MeshKernel::AddFacets`). Smooth with `smooth("Taubin", n)` (the docstring's `smooth(iteration)` is outdated), reduce with `decimate()`, and set `CreaseAngle` so it shades round.

```python
import math

import Mesh

src = Mesh.createSphere(10, 40)
points, facets = src.Topology
bumped = [p * (1 + 0.06 * math.sin(p.x * 0.9) * math.cos(p.y * 0.7)) for p in points]
rock = Mesh.Mesh([[bumped[i] for i in f] for f in facets])   # triangles as coordinates
rock.smooth("Taubin", 2)   # Method "Taubin" (keeps the volume) or "Laplace" (shrinks), iterations
rock.harmonizeNormals()
obj = doc.addObject("Mesh::Feature", "Rock")
obj.Mesh = rock
obj.ViewObject.CreaseAngle = 70
assert rock.isSolid() and rock.CountFacets == src.CountFacets
```

## What breaks (learned the hard way)

- **Many curved solids fused together** (dozens of ellipsoids and pipes) make OpenCascade's booleans slow, memory-hungry (tens of GB) or crash on near-tangent surfaces. For organic forms use `sculpt`, or few lofts and sweeps; keep separate pieces as a compound instead of fusing everything.
- **Raw mesh data through `Mesh.Mesh(points, indices)`** can segfault on a bad index: use coordinates or files (above).
- **Heavy or risky geometry** belongs in `run_headless`: if it crashes, the user's FreeCAD and their unsaved work survive.
