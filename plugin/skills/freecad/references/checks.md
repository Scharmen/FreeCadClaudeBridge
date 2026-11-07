# Checking with numbers: `check_all` and the spec grammar

Size, position and fit errors do not show in a screenshot, and they are the most common mistakes. All check tools work on a saved copy: the document stays as it is, no undo step, and a kernel crash cannot reach FreeCAD.

## One call, twice

`check_all(spec, fits=[...], process="fdm", section={"axis": "y"})` runs in one go, on one copy, in one `freecadcmd`:

- `check_design` with your spec (below),
- `check_print` of every visible result (`printable=false` skips it),
- `check_fit` for each pair in `fits` = `[{"a": "Lid", "b": "Box", "kind": "snap"}, ...]`,
- the four-view sheet of `inspect_views` (`views=false` skips it) and an optional section.

The answer lists `problems` first, each with the check that found it (`from`: design, print, fit) and how to fix it, then `warnings`, then the numbers. Call it **once after building and once after the fixes**. A third call is only for a third change; what did not change needs no new look.

The single tools stay for a targeted re-check: `check_fit` after moving one part, `check_print` after thickening one wall, `check_surface` for a body judged by its reflections, `flex_test` for a parametric model.

```python
# the spec written before building, used after it
spec = {"parts": [{"name": "housing", "bbox": [50, 50, 12], "tol": 0.5,
                   "holes": [{"d": [21.8, 22.2], "count": [1, 1]}, {"d": [4.3, 4.8], "count": [4, 4]}],
                   "radii_min": {"r": [0.8, 1.2], "count": 4}}],
        "solids": [1, 1]}
# check_all(spec=spec, fits=[{"a": "Bearing", "b": "Housing", "kind": "press"}], section={"axis": "y"})
```

## Spec grammar (`check_design`, `check_all`)

Ranges are `[lo, hi]`; a single number where a range belongs means that number (`"solids": 1`).

| Key | Meaning |
| --- | --- |
| `parts` | list of parts, each matched to the closest measured solid (see below) |
| `solids` | `[lo, hi]` number of visible result solids |
| `no_interference` | `true`: no two parts share volume |
| `clearance` | `[{"a", "b", "range": [lo, hi], "seated": false}]` smallest gap between two parts |
| `aligned_holes` | `{"a", "b", "d_a": [lo, hi], "d_b": [lo, hi], "count": n, "tol": 0.3}` holes of two parts on one axis |
| `center_distance` | `{"a", "b", "value", "tol"}` between two parts' centres |
| `height` | `[lo, hi]` overall height (a single number allows 2 %) |
| `total` | `{"bbox": [a, b, c], "tol"}` overall size |
| `params` | `["Length", ...]` VarSet or Spreadsheet parameters that must exist |
| `expressions_min` | at least this many expressions |
| `flex` | `{"params": [...], "factors": [0.8, 1.2]}` rebuild with changed parameters |
| `sketch_dof_zero` | `true`: every sketch fully constrained |
| `joints` | `["Revolute", ...]` assembly joint types that must exist |
| `requires` | `[{"label", "types": ["Assembly::AssemblyObject"]}]` objects that must exist |
| `iou_min` | overlap with a reference shape (benchmark only) |

Per part (`parts[i]`):

| Key | Meaning |
| --- | --- |
| `name` | label used in the report |
| `bbox`, `tol` | size `[a, b, c]` in any orientation (`"oriented": true`: x, y, z as given) |
| `volume` | `[lo, hi]` mm³ |
| `holes` | `[{"d": [lo, hi], "count": [lo, hi], "open": false}]` cylindrical holes by diameter; `open` for a clip ring |
| `teeth` | gear teeth |
| `pitch` | `{"value", "tol", "band": [r_lo, r_hi]}` thread pitch, measured in the radial band of the flanks |
| `radii_min` | `{"r": [lo, hi], "count": n}` fillets of that radius |
| `inclined_min` | at least n sloped faces (a gusset) |
| `surfaces_any` | `["BSplineSurface", "Cone", ...]` at least one of these surface types |
| `surfaces_min` | `{"Plane": 2}` at least that many faces of a type |

## Fits

`kind`: `press` 0–0.1 mm (FDM), `sliding` 0.15–0.35, `loose` 0.3–0.7; resin and CNC tighter; `custom` with `gap_mm=[lo, hi]`. `seated` and `snap`: a part on a seat (lid on a rim, bearing on a shoulder) may touch on flat faces of one direction; the room around it is measured and must be a sliding gap. A press fit may overlap by its interference, not run into the other part.

## Walls and printing

At least 1.2 mm for FDM and 0.8 mm for resin, also on clips, ribs and thin spots of a curved part. `problems` must be fixed; `warnings` (supports, long bridges) are for you to weigh and mention. The best orientation is in `best_up`.
