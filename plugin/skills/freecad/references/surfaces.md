# Body surfaces: cars, boats, fairings

A body is judged by its reflections. Hand-typed tables of sections, each interpolated exactly, copy every small error into the surface: ripples no added detail can hide. Build the big shapes from a few fair lines, check them, and only then add detail.

## Order of work

1. **Read the feature lines from the pictures:** centre line, hood edge, shoulder, waist, sill. Six to ten lines are enough. Do not start with detail.
2. **Build the body with `make_part`, family `faired_body`,** one side only (y >= 0), the lines ordered around the half section from the top centre to the bottom centre:

```json
{"family": "faired_body", "params": {"name": "Lower", "poles": 13, "lines": [
  {"points": [[0, 0, 620], [400, 0, 880], [1400, 0, 1010], [3400, 0, 1020], [4720, 0, 900]]},
  {"points": [[0, 360, 610], [400, 520, 866], [1400, 670, 992], [3400, 650, 1008], [4720, 380, 900]], "crease": true},
  {"points": [[0, 590, 450], [400, 905, 520], [1400, 908, 545], [3400, 925, 565], [4720, 640, 680]]},
  {"points": [[0, 500, 330], [400, 850, 180], [1400, 880, 172], [3400, 890, 215], [4720, 580, 410]], "crease": true},
  {"points": [[0, 0, 300], [400, 0, 140], [1400, 0, 130], [3400, 0, 160], [4720, 0, 370]]}]}}
```

3. **Run `check_surface`.** Long, calm stripes are good. Zigzag, islands and sudden bends are bumps. Change the lines and call `make_part` again with `replace`; do not patch the surface.
4. **Only now cut** windows, wheel arches and panel seams, and add lamps, grille and trim.

## What decides the result

- **Separate bodies for shapes that meet in a hard line.** Hood and cabin are two bodies, fused: the cowl stays a crisp edge. One skin over hood, screen and roof melts into a soft blob.
- **`crease` only where the real object has an edge** (shoulder line, hood edge, sill). Everything else stays smooth between lines.
- **`poles`: 9 to 14 for a whole car.** More poles follow the errors in your points again. `max_distance_to_points_mm` in the answer says how far the surface stayed from them. If a real feature is missed, add a point there rather than poles everywhere.
- **Points get denser where the shape turns fast,** at nose and tail. The knots follow them.
- **The ends are flat caps.** Let the lines close in towards them (the section gets small), and let a grille or bumper cover the cap.
- **Every line needs the same start and end x,** and x must keep rising. Different lines may have different points in between.

## Pitfalls

- `Shape.BoundBox` of a B-spline body can show its control points and read much too big (2128 mm for a 1895 mm wide car). Use `shape.optimalBoundingBox(True, False)` or `check_design`.
- Booleans between surfaces that nearly touch can fail with an empty error. Keep them clearly apart, or pass a tolerance: `a.cut(b, 0.5)`.
- A wall from one line table moved inwards is not an exact offset. Measure wall thickness with `check_print` before printing.
- `removeSplitter()` can break a body with B-spline faces while it looks fine. Work on a copy and keep the result only if `isValid()` still holds.
- Moving lines inwards for a wall: drop the stations the wall passes at nose and tail, or x stops rising and the build fails with "U Poles and degree mismatch".
- After the last cut, mesh the body and check `Mesh.isSolid()` before you call it printable. A valid solid can still give a mesh with open spots.

## Detail on a car body

The body is one `faired_body`; the detail comes from parts that carry their own geometry: `make_part` families `wheel` (tyre with tread, spoked rim, brake disc and caliper; the print export mounts them on axles), `lamp` (housing, reflector bowl, lens, LED strip, looking towards -Y: rotate its Placement for the rear), `pattern` (honeycomb or slats for the grille, vents and the diffuser: `kind="holes"` cuts them into a surface, `kind="panel"` fills an opening). Check each against the photo with `compare_to_picture(crop=..., look_at=[...])`.
