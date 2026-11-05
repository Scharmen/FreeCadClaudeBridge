# Mechanical design: sizes that fit (FreeCAD 1.1)

Tested by `tests/gui/test_references.py`. Standard values to design against. They are from the ISO standards named; for a real product check a supplier's table. Printed parts need the extra clearance in `printing.md`.

## Contents
- Screws: clearance holes, heads, nuts, tapping
- `PartDesign::Hole`
- Bearings and shafts
- Fits (ISO 286)
- Walls, ribs, bosses, fillets, draft
- Worked example: bearing seat

## Screws (metric coarse)

| Size | Pitch | Clearance hole (ISO 273 medium) | Tap drill | Cap screw head Ø × h (ISO 4762) | Hex nut AF × h (ISO 4032) | Countersunk head Ø (ISO 10642) |
| --- | --- | --- | --- | --- | --- | --- |
| M2 | 0.4 | 2.4 | 1.6 | 3.8 × 2 | 4 × 1.6 | 4.4 |
| M2.5 | 0.45 | 2.9 | 2.05 | 4.5 × 2.5 | 5 × 2 | 5.5 |
| M3 | 0.5 | 3.4 | 2.5 | 5.5 × 3 | 5.5 × 2.4 | 6.7 |
| M4 | 0.7 | 4.5 | 3.3 | 7 × 4 | 7 × 3.2 | 9 |
| M5 | 0.8 | 5.5 | 4.2 | 8.5 × 5 | 8 × 4.7 | 11.2 |
| M6 | 1 | 6.6 | 5 | 10 × 6 | 10 × 5.2 | 13.4 |
| M8 | 1.25 | 9 | 6.8 | 13 × 8 | 13 × 6.8 | 17.9 |
| M10 | 1.5 | 11 | 8.5 | 16 × 10 | 16 × 8.4 | 22.4 |

- Counterbore: head Ø + 0.5–1 mm, depth head height + 0.5 mm.
- Countersink: 90°, top Ø = head Ø + 0.4 mm. Wood screws: head about twice the shank, hole shank + 0.5 mm.
- Heat-set inserts: follow the maker's hole size; M3 usually 3.8–4.2 mm, M4 about 5.6 mm, boss wall at least 2 mm.
- Self-tapping into a print: hole about 85–90 % of the screw diameter, boss wall at least twice the screw's diameter around it.

## `PartDesign::Hole`

Use it for drilled, tapped, counterbored and countersunk holes instead of hand-made cylinders; it knows the ISO sizes. It reads the circle centres from a sketch. A cosmetic thread (`ModelThread = False`) is fast and right for parts that will be tapped; model the thread only for printing or display.

```python
body = doc.addObject("PartDesign::Body", "Body")
blk = body.newObject("PartDesign::AdditiveBox", "Block")
blk.Length, blk.Width, blk.Height = 40, 30, 12
xy = next(f for f in body.Origin.OriginFeatures if f.Role == "XY_Plane")
sk = body.newObject("Sketcher::SketchObject", "HoleCentres")
sk.AttachmentSupport = [(xy, "")]
sk.MapMode = "FlatFace"
c = sk.addGeometry(Part.Circle(Vector(20, 15, 0), Vector(0, 0, 1), 3))
sk.addConstraint(Sketcher.Constraint("Diameter", c, 6))
sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, c, 3, 20))
sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, c, 3, 15))
doc.recompute()      # the Hole needs a valid base shape before its properties are set
hole = body.newObject("PartDesign::Hole", "M6Tapped")
hole.Profile = sk
hole.ThreadType = "ISOMetricProfile"
hole.Threaded = True
hole.ThreadSize = next(n for n in hole.getEnumerationsOfProperty("ThreadSize") if n.startswith("M6x1"))
hole.ModelThread = False
hole.DepthType = "ThroughAll"
hole.Reversed = True      # the sketch is on the bottom face: drill upwards into the block
doc.recompute()
assert "Invalid" not in hole.State and hole.Shape.isValid()
assert abs(hole.Diameter.Value - 5.0) < 0.3      # tap drill size of M6
assert hole.Shape.Volume < blk.Shape.Volume - 150
```

## Bearings

| Bearing | Bore | Outside | Width |
| --- | --- | --- | --- |
| 623 | 3 | 10 | 4 |
| 625 | 5 | 16 | 5 |
| 608 | 8 | 22 | 7 |
| 6000 | 10 | 26 | 8 |
| 6001 | 12 | 28 | 8 |
| 6200 | 10 | 30 | 9 |
| 6201 | 12 | 32 | 10 |
| 6202 | 15 | 35 | 11 |

A seat supports only the outer ring: the step under it leaves the inner ring and seal free, so the opening is at least the bore + 2 × (a third of the ring's height). Printed seats: outer Ø − 0 to + 0.1 mm for a press fit (printed holes come out small), and print one test first.

## Fits (ISO 286, in µm)

Hole basis: hole H7 with a shaft from the table. For diameters over … up to:

| Ø mm | H7 hole | g6 sliding | h6 locating | k6 light press | p6 press |
| --- | --- | --- | --- | --- | --- |
| 6–10 | 0 / +15 | −5 / −14 | 0 / −9 | +1 / +10 | +15 / +24 |
| 10–18 | 0 / +18 | −6 / −17 | 0 / −11 | +1 / +12 | +18 / +29 |
| 18–30 | 0 / +21 | −7 / −20 | 0 / −13 | +2 / +15 | +22 / +35 |
| 30–50 | 0 / +25 | −9 / −25 | 0 / −16 | +2 / +18 | +26 / +42 |

These are for machined parts; a printer cannot hold them. Model the nominal size for machining and put the fit on the drawing; for printing use the gaps in `printing.md`.

## Walls, ribs, bosses, fillets

- **Even walls.** Thick spots sink and warp, in moulding and in print.
- **Ribs instead of thick walls:** 50–70 % of the wall thick, up to 3 × wall high, with a fillet at the foot (25–60 % of the wall). Printed: at least two nozzle widths.
- **Bosses:** tie them to a wall or a rib; outside Ø about 2 × the screw; gussets at the foot; boss wall under 60 % of the wall when moulded.
- **Corners:** inside radius about half the wall, outside radius = inside radius + wall, so the wall stays even.
- **Draft for moulding:** 0.5–1.5° per side.
- **Gussets** go where the load turns a corner: a bracket without one breaks at the bend.

## Worked example: a bearing seat

```python
outer, width, bore = 22.0, 7.0, 8.0
ring_step = (outer - bore) / 2 / 3                  # support only the outer ring
opening = outer - 2 * ring_step
body = Part.makeCylinder(16, width + 4)
seat = Part.makeCylinder(outer / 2, width + 1, Vector(0, 0, 4))
clear = Part.makeCylinder(opening / 2, 6, Vector(0, 0, -1))
part = body.cut(seat).cut(clear)
assert part.isValid() and len(part.Solids) == 1
holes = sorted({round(2 * f.Surface.Radius, 2) for f in part.Faces if type(f.Surface).__name__ == "Cylinder"})
assert holes[0] == round(opening, 2) and outer in holes
assert opening > bore + 4                            # the inner ring stays free
doc.addObject("Part::Feature", "BearingSeat").Shape = part
```
