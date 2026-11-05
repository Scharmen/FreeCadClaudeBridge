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
    if family == "knob" and p["shaft"] + 4 >= p["diameter"]:
        raise ValueError("the knob needs at least 2 mm of wall around the shaft")


def describe() -> dict[str, Any]:
    return {name: {"what": f["doc"], "params": f["params"]} for name, f in sorted(FAMILIES.items())}
