"""What make_part can build: families, parameters with defaults, ISO sizes. Pure Python.

`resolve` fills in defaults and refuses unknown or impossible values with a message that says why, so
that a wrong request fails before any geometry is computed.
"""

from __future__ import annotations

from typing import Any

ISO_METRIC = {  # size: pitch, head across flats, head height, nut height (ISO 4017, ISO 4032)
    "M3": (0.5, 5.5, 2.0, 2.4), "M4": (0.7, 7.0, 2.8, 3.2), "M5": (0.8, 8.0, 3.5, 4.7), "M6": (1.0, 10.0, 4.0, 5.2),
    "M8": (1.25, 13.0, 5.3, 6.8), "M10": (1.5, 16.0, 6.4, 8.4), "M12": (1.75, 18.0, 7.5, 10.8),
}

POD = (  # the default faired_body and an example of its lines: y and z of each line along x
    [[0, 0, 22], [30, 0, 40], [100, 0, 50], [170, 0, 40], [200, 0, 22]],
    [[0, 12, 20], [30, 26, 34], [100, 34, 42], [170, 26, 34], [200, 12, 20]],
    [[0, 14, 14], [30, 32, 16], [100, 40, 18], [170, 32, 16], [200, 14, 14]],
    [[0, 0, 8], [30, 0, 2], [100, 0, 0], [170, 0, 2], [200, 0, 8]],
)
FAMILIES: dict[str, dict[str, Any]] = {
    "gear": {"doc": "Involute spur gear, or helical with helix_deg.",
             "params": {"module": 1.0, "teeth": 20, "width": 8.0, "bore": 5.0, "pressure_deg": 20.0, "helix_deg": 0.0,
                        "backlash": 0.1}},
    "gear_pair": {"doc": "Two spur gears in mesh at the right centre distance, without touching.",
                  "params": {"module": 1.0, "teeth_a": 20, "teeth_b": 40, "width": 8.0, "bore_a": 5.0, "bore_b": 5.0,
                             "pressure_deg": 20.0, "backlash": 0.1}},
    "spring": {"doc": "Compression spring: active coils, one closed coil at each end, ends ground flat.",
               "params": {"wire": 1.5, "outer": 20.0, "free_length": 40.0, "active_coils": 8}},
    "bolt": {"doc": "Hex bolt with a modelled ISO thread (ISO 4017).",
             "params": {"size": "M8", "length": 30.0}},
    "nut": {"doc": "Hex nut with a modelled inner thread (ISO 4032); clearance widens it for printing.",
            "params": {"size": "M8", "clearance": 0.3}},
    "bolt_nut": {"doc": "A bolt with its nut screwed on, nut_at mm below the head.",
                 "params": {"size": "M8", "length": 30.0, "nut_at": 10.0, "clearance": 0.3}},
    "thread": {"doc": "A thread on any diameter: external (a bottle neck, a lid, a screw-in plug) as a solid to "
                      "fuse onto the neck, from z = 0 up with a lead-in chamfer at the top; internal as a cutter to "
                      "subtract from a cap or a hole, clearance wider for printing. major = outside diameter, core = "
                      "diameter of the neck under an external thread (0: the thread's minor diameter).",
               "params": {"major": 32.0, "pitch": 3.0, "length": 9.0, "kind": "external", "core": 0.0,
                          "clearance": 0.3}},
    "wheel": {"doc": "Car or model wheel on the y axis at the origin: a tyre with rounded shoulders and tread grooves, "
                     "a spoked rim with a hub and an axle bore; disc > 0 adds a brake disc and a caliper on the inner "
                     "side (brake_side -y or +y: the side the car is on). Parts Tyre, Rim (Disc, "
                     "Caliper); printable apart, the print export treats them as moving.",
              "params": {"diameter": 60.0, "width": 24.0, "rim": 40.0, "rim_width": 0.0, "spokes": 5,
                         "spoke_width": 0.0, "hub": 0.0, "bore": 3.0, "grooves": 4, "groove_depth": 1.0,
                         "disc": 0.0, "brake_side": "-y"}},
    "lamp": {"doc": "Head or tail lamp looking towards -Y from y = 0: a housing with an open front, a reflector bowl "
                    "inside, a clear lens closing the front and an LED strip under the top edge. shape rect or round "
                    "(round: width is the diameter). Colour the parts afterwards (lens transparent).",
             "params": {"width": 30.0, "height": 12.0, "depth": 15.0, "shape": "rect", "wall": 1.2, "lens": 1.0}},
    "pattern": {"doc": "A panel of repeated openings for grilles, vents and diffusers, standing in the XZ plane from "
                       "z = 0 up, centred on x, thickness along +y: cells honeycomb, slats, mesh or holes, cell = "
                       "spacing, bar = material between openings, frame = solid border. kind panel gives the bars to "
                       "fuse into an opening, kind holes gives the openings as cutters to subtract from a surface.",
                "params": {"width": 40.0, "height": 20.0, "thickness": 1.2, "cells": "honeycomb", "cell": 4.0,
                           "bar": 0.8, "frame": 1.5, "kind": "panel"}},
    "hinge": {"doc": "Pin hinge opened flat: two leaves, knuckles, a separate pin, countersunk screw holes.",
              "params": {"length": 40.0, "leaf_width": 20.0, "thickness": 3.0, "pin": 3.0, "gap": 0.3, "knuckles": 5,
                         "screw": 4.0}},
    "enclosure": {"doc": "Box with a seated lid, screw bosses for heat-set inserts, countersunk lid holes.",
                  "params": {"length": 80.0, "width": 50.0, "height": 25.0, "wall": 2.0, "lid": 3.0, "corner": 1.0,
                             "insert": 4.0, "screw": 3.4, "lid_lift": 0.0}},
    "snap_box": {"doc": "Box with a snap-on lid: seat on the rim, locating plug, two cantilever clips behind ledges.",
                 "params": {"length": 60.0, "width": 40.0, "height": 30.0, "wall": 1.6, "lid": 6.0, "gap": 0.25,
                            "undercut": 0.8, "material": "PETG"}},
    "bracket": {"doc": "L bracket with a diagonal gusset and screw holes in both legs.",
                "params": {"leg_a": 100.0, "leg_b": 150.0, "width": 30.0, "thickness": 5.0, "hole": 5.0,
                           "gusset": 0.5}},
    "knob": {"doc": "Knob for a D-shaft: grip ridges, indicator mark, rounded top.",
             "params": {"diameter": 25.0, "height": 18.0, "shaft": 6.0, "flat": 1.5, "depth": 12.0, "ridges": 18,
                        "gap": 0.2}},
    "stud_plate": {"doc": "Plate with a DUPLO-compatible stud grid (pitch 16 mm, hollow studs 9.35 x 4.4 mm); "
                          "size = studs x pitch. For play boards, toy bases, roofs that take bricks.",
                   "params": {"studs_x": 4, "studs_y": 4, "thickness": 3.2, "pitch": 16.0, "stud_d": 9.35,
                              "stud_h": 4.4, "hollow": 6.8}},
    "tile_connector": {"doc": "Bow-tie key that joins two tiles lying side by side: dropped in from above, printed "
                              "flat without supports. Returns Key and Pocket (a cutter): put the seam at x = 0 and "
                              "the tiles' top at z = 0, cut Pocket from both tiles. gap is the clearance per side "
                              "(0.2 is a starting value, not print-tested: coupon=true gives two small test plates "
                              "and three keys with gap-0.1, gap, gap+0.1, marked with 1-3 dots).",
                       "params": {"length": 30.0, "width": 18.0, "waist": 9.0, "height": 4.0, "gap": 0.2,
                                  "coupon": False}},
    "faired_body": {"doc": "Smooth closed body (car, boat, fairing) from feature lines running front to rear. Each "
                           "line: {points: [[x, y, z], ...], crease: true for a sharp edge}, x rising, all lines from "
                           "the same start x to the same end x. Give one side (y >= 0), mirrored across y=0: order the "
                           "lines around the half section, from the top centre line to the bottom centre line.",
                    "params": {"lines": [{"points": pts, "crease": i == 2} for i, pts in enumerate(POD)],
                               "poles": 13, "name": "Body"}},
}
STRAIN = {"PLA": 0.02, "PETG": 0.03, "ABS": 0.04, "NYLON": 0.05}    # allowed bending strain of a printed clip


def resolve(family: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if family not in FAMILIES:
        raise ValueError(f"unknown family '{family}' (available: {', '.join(sorted(FAMILIES))})")
    defaults = FAMILIES[family]["params"]
    given = params or {}
    unknown = set(given) - set(defaults)
    if unknown:
        raise ValueError(f"{family} has no parameter {sorted(unknown)} (it has: {', '.join(defaults)})")
    out: dict[str, Any] = {}
    for key, default in defaults.items():
        value = given.get(key, default)
        try:
            out[key] = type(default)(value)
        except (TypeError, ValueError) as e:
            raise ValueError(f"{family}.{key} must be a {type(default).__name__}, not {value!r}") from e
        if isinstance(out[key], (int, float)) and out[key] < 0:
            raise ValueError(f"{family}.{key} must not be negative")
    _validate(family, out)
    return out


def _validate(family: str, p: dict[str, Any]) -> None:
    if family in ("bolt", "nut", "bolt_nut") and p["size"] not in ISO_METRIC:
        raise ValueError(f"unknown size {p['size']} (available: {', '.join(ISO_METRIC)})")
    if family == "gear" and p["teeth"] < 8:
        raise ValueError("a gear needs at least 8 teeth")
    if family == "gear_pair" and min(p["teeth_a"], p["teeth_b"]) < 8:
        raise ValueError("a gear needs at least 8 teeth")
    if family == "gear" and p["bore"] >= p["module"] * (p["teeth"] - 2.5):
        raise ValueError(f"the bore {p['bore']:g} does not fit inside the teeth's root circle")
    if family == "spring" and p["wire"] * (p["active_coils"] + 2) >= p["free_length"]:
        raise ValueError("the coils do not fit in the free length: the spring would be solid")
    if family == "spring" and p["wire"] * 2 >= p["outer"]:
        raise ValueError("the wire is too thick for this outer diameter")
    if family == "enclosure" and p["lid"] >= p["height"]:
        raise ValueError("the lid must be lower than the whole height")
    if family == "snap_box":
        if p["material"].upper() not in STRAIN:
            raise ValueError(f"unknown material {p['material']} (available: {', '.join(STRAIN)})")
        if p["lid"] >= p["height"] / 2:
            raise ValueError("the lid must be lower than half the height")
    if family == "hinge" and p["knuckles"] % 2 == 0:
        raise ValueError("a hinge needs an odd number of knuckles, so that one leaf holds both ends of the pin")
    if family == "faired_body":
        _validate_lines(p)
    if family == "thread":
        if p["kind"] not in ("external", "internal"):
            raise ValueError("kind must be external or internal")
        if p["pitch"] <= 0 or p["length"] <= 0:
            raise ValueError("pitch and length must be positive")
        if p["major"] < 4 * p["pitch"]:
            raise ValueError(f"a pitch of {p['pitch']:g} is too coarse for {p['major']:g} mm: the thread depth would "
                             "eat the core (major must be at least 4 × pitch)")
        if p["core"] and p["core"] >= p["major"]:
            raise ValueError("core must be smaller than major")
    if family == "wheel":
        if p["brake_side"] not in ("-y", "+y"):
            raise ValueError("brake_side must be -y or +y")
        if p["rim"] >= p["diameter"]:
            raise ValueError("the rim must be smaller than the tyre's diameter")
        if p["spokes"] < 2:
            raise ValueError("a rim needs at least 2 spokes")
        if p["bore"] >= p["rim"] / 3:
            raise ValueError("the bore does not fit the hub")
        if p["disc"] and p["disc"] >= p["rim"] - 4:
            raise ValueError("the disc must fit inside the rim")
    if family == "lamp":
        if p["shape"] not in ("rect", "round"):
            raise ValueError("shape must be rect or round")
        if 2 * p["wall"] + p["lens"] >= min(p["width"], p["height"], p["depth"]):
            raise ValueError("the walls and the lens leave no room inside")
    if family == "pattern":
        if p["cells"] not in ("honeycomb", "slats", "mesh", "holes"):
            raise ValueError("cells must be honeycomb, slats, mesh or holes")
        if p["kind"] not in ("panel", "holes"):
            raise ValueError("kind must be panel or holes")
        if p["bar"] >= p["cell"]:
            raise ValueError("bar must be smaller than cell, or nothing is open")
    if family == "stud_plate":
        if not (1 <= p["studs_x"] <= 32 and 1 <= p["studs_y"] <= 32):
            raise ValueError("a stud_plate has 1 to 32 studs each way; join larger boards from tiles")
        if p["stud_d"] >= p["pitch"] - 2:
            raise ValueError("the studs would touch: stud_d must be at least 2 mm smaller than the pitch")
        if p["hollow"] and p["hollow"] > p["stud_d"] - 1.6:
            raise ValueError("a hollow stud needs a wall of at least 0.8 mm")
    if family == "tile_connector":
        if p["waist"] > p["width"] - 4:
            raise ValueError("the waist must be at least 4 mm narrower than the width, or the key holds nothing")
        if not 0.05 <= p["gap"] <= 0.6:
            raise ValueError("gap must be between 0.05 and 0.6 mm")
        if p["height"] < 2 or p["length"] < 12:
            raise ValueError("the key is too small to print: height from 2 mm, length from 12 mm")
    if family == "knob" and p["shaft"] + 4 >= p["diameter"]:
        raise ValueError("the knob needs at least 2 mm of wall around the shaft")


def _validate_lines(p: dict[str, Any]) -> None:
    lines = p["lines"]
    if len(lines) < 3:
        raise ValueError("a faired_body needs at least 3 lines: top centre, at least one between, bottom centre")
    if p["poles"] < 4:
        raise ValueError("a faired_body needs at least 4 poles along a line")
    start, end = None, None
    span = 1e-6
    for i, line in enumerate(lines):
        pts = line.get("points") if isinstance(line, dict) else None
        if not pts or len(pts) < 4:
            raise ValueError(f"line {i} needs at least 4 points")
        if any(not isinstance(q, (list, tuple)) or len(q) != 3 for q in pts):
            raise ValueError(f"line {i}: every point is x, y, z")
        xs = [float(q[0]) for q in pts]
        if any(b <= a for a, b in zip(xs, xs[1:], strict=False)):
            raise ValueError(f"line {i}: x must rise from front to rear, at every point")
        start, end = xs[0] if start is None else start, xs[-1] if end is None else end
        span = max(span, end - start)
        if abs(xs[0] - start) > 1e-3 * span or abs(xs[-1] - end) > 1e-3 * span:
            raise ValueError(f"line {i} must start and end at the same x as line 0 ({start:g} to {end:g})")
        if any(float(q[1]) < 0 for q in pts):
            raise ValueError(f"line {i}: negative y; give one side only, y >= 0, it gets mirrored")
    for index, what in ((0, "first line"), (-1, "last line")):
        if any(abs(float(q[1])) > 1e-3 * span for q in lines[index]["points"]):
            raise ValueError(f"the {what} must lie on the centre, y = 0")


def describe() -> dict[str, Any]:
    return {name: {"what": f["doc"], "params": f["params"]} for name, f in sorted(FAMILIES.items())}
