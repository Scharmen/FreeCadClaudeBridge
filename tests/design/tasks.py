"""The benchmark tasks: what the user asks, the numeric spec, and what a reviewer looks for.

Prompts read like a user's request; the spec checks the result in numbers (see spec.py), and
`look` lists the features the visual judge checks on the screenshots. `reference` names a model
in refs.py for the IoU check; `solution` names a known-good model that must score ~100 (it
validates the spec, see tests/headless/test_design_measure.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SMOOTH = ["BSplineSurface", "SurfaceOfExtrusion", "SurfaceOfRevolution", "OffsetSurface"]
M8 = {"value": 1.25, "tol": 0.1, "band": [3.0, 4.6]}  # thread crests between minor and major radius
LEAF_HOLES = [{"d": [3.9, 4.8], "count": [2, 2]}, {"d": [3.4, 3.9], "count": [1, 9]}]  # screws, knuckle bores

TASKS: list[dict[str, Any]] = [
    {
        "id": "bearing_housing",
        "prompt": "Design a flanged housing for a 608 ball bearing (22 mm outside diameter, 7 mm wide), 3D printed in "
                  "PLA on a 0.4 mm nozzle. Square flange 50 x 50 x 6 mm with four M4 mounting holes on a 38 mm square "
                  "pattern; a round boss of 32 mm diameter on top, total height 12 mm. The bearing sits on the "
                  "vertical centre axis in a seat 7 mm deep from the top and is pressed in; below the seat a through "
                  "hole for an 8 mm shaft with clearance. Round the outer edges with 1 mm.",
        "spec": {
            "parts": [{"name": "housing", "bbox": [50, 50, 12], "tol": 0.5,
                       "holes": [{"d": [21.8, 22.2], "count": [1, 1]}, {"d": [4.3, 4.8], "count": [4, 4]},
                                 {"d": [8.2, 18], "count": [1, 2]}],  # shaft hole, maybe stepped
                       "radii_min": {"r": [0.8, 1.2], "count": 4}}],
            "solids": [1, 1], "iou_min": 0.9,
        },
        "reference": "bearing_housing", "solution": "bearing_housing",
        "look": ["square flange", "four mounting holes in the corners", "round boss on top", "bearing seat",
                 "shaft hole below the seat", "rounded outer edges"],
    },
    {
        "id": "tube_bracket",
        "prompt": "Design a 3D-printable wall bracket that holds a 22 mm copper tube parallel to the wall, with the "
                  "tube axis 40 mm from the wall. Wall plate 60 x 40 x 5 mm with two countersunk holes for 4 mm wood "
                  "screws; an arm with a gusset from the plate to a clamp ring with 3 mm wall around the tube; the "
                  "ring has a gap so the tube clips in. Round all edges with about 1 mm.",
        "spec": {
            "parts": [{"name": "bracket", "holes": [{"d": [22.0, 23.0], "count": [1, 1], "open": True},  # clip ring
                                                    {"d": [4.0, 5.0], "count": [2, 2]}],
                       "radii_min": {"r": [0.7, 1.3], "count": 6}, "surfaces_any": ["Cone"],
                       "inclined_min": 1}],  # the gusset
            "solids": [1, 1], "total": {"bbox": [60, 40, 51], "tol": 4},  # ring front 54 minus the opening
        },
        "look": ["wall plate", "two countersunk screw holes", "arm", "gusset", "clamp ring", "gap in the ring",
                 "rounded edges"],
    },
    {
        "id": "enclosure",
        "prompt": "Design a 3D-printable electronics enclosure: outside 80 x 50 x 25 mm when closed, made of a body "
                  "and a separate 3 mm lid (so the body is 22 mm high), 2 mm walls and floor, 1 mm rounding on the "
                  "outer vertical edges. Four screw bosses in the inner corners take M3 heat-set inserts; the lid has "
                  "four countersunk M3 clearance holes lined up with them. A USB-C cut-out (9 x 3.5 mm, rounded "
                  "ends) in one short wall, centred, 5 mm above the inside of the floor. Place the lid 10 mm above "
                  "the body.",
        "spec": {
            "parts": [{"name": "body", "bbox": [80, 50, 22], "tol": 0.5, "holes": [{"d": [3.8, 4.6], "count": [4, 4]}],
                       "radii_min": [{"r": [0.8, 1.2], "count": 4},
                                     {"r": [1.6, 1.9], "count": 2}]},  # edges; USB-C slot ends
                      {"name": "lid", "bbox": [80, 50, 3], "tol": 0.5, "holes": [{"d": [3.2, 3.7], "count": [4, 4]}],
                       "surfaces_any": ["Cone"]}],
            "aligned_holes": {"a": "lid", "b": "body", "d_a": [3.2, 3.7], "d_b": [3.8, 4.6], "count": 4, "tol": 0.3},
            "solids": [2, 2], "no_interference": True,
        },
        "look": ["body with walls", "separate lid above", "four screw bosses", "countersunk lid holes",
                 "USB-C cut-out with rounded ends", "rounded vertical edges"],
    },
    {
        "id": "snap_box",
        "prompt": "Design a small 3D-printable box with a snap-on lid for PETG: outside 60 x 40 x 30 mm when closed, "
                  "1.6 mm walls, the lid 6 mm of that height. The lid holds with two cantilever snap clips that catch "
                  "on ledges of the box (about 0.8 mm undercut) and has 0.2 mm clearance to the box. Show the lid in "
                  "the closed position.",
        "spec": {
            "parts": [{"name": "box", "bbox": [60, 40, 24], "tol": 3}, {"name": "lid"}],
            "solids": [2, 2], "no_interference": True, "total": {"bbox": [60, 40, 30], "tol": 1.0},
            "clearance": [{"a": "box", "b": "lid", "range": [0.1, 0.5]}],
        },
        "look": ["box", "lid in the closed position", "two snap clips", "ledges the clips catch on",
                 "visible gap between lid and box"],
    },
    {
        "id": "gear_pair",
        "prompt": "Make a pair of spur gears with involute teeth: module 1 mm, 20° pressure angle, 20 teeth and 40 "
                  "teeth, 8 mm face width, each with a 5 mm bore. Place them in mesh side by side at the correct "
                  "centre distance, without the teeth overlapping.",
        "spec": {
            "parts": [{"name": "pinion", "bbox": [22, 22, 8], "tol": 0.4, "teeth": 20,
                       "holes": [{"d": [4.9, 5.3], "count": [1, 1]}]},
                      {"name": "wheel", "bbox": [42, 42, 8], "tol": 0.4, "teeth": 40,
                       "holes": [{"d": [4.9, 5.3], "count": [1, 1]}]}],
            "center_distance": {"a": "pinion", "b": "wheel", "value": 30, "tol": 0.15},
            "clearance": [{"a": "pinion", "b": "wheel", "range": [0.0, 0.3]}],  # teeth engaged
            "solids": [2, 2], "no_interference": True,
        },
        "solution": "gear_pair",
        "look": ["20-tooth gear", "40-tooth gear", "involute tooth shape", "teeth in mesh", "bores"],
    },
    {
        "id": "bolt_nut",
        "prompt": "Model an M8 x 30 hex bolt (ISO 4017: 13 mm across flats, 5.3 mm head height, 30 mm shank, fully "
                  "threaded) and a matching M8 hex nut (ISO 4032: 13 mm across flats, 6.8 mm high), both with real "
                  "modelled ISO metric threads of 1.25 mm pitch, for a 3D print. The nut is screwed onto the bolt "
                  "with its face 10 mm below the head, and the threads must not overlap.",
        "spec": {
            "parts": [{"name": "bolt", "bbox": [13, 15.01, 35.3], "tol": 1.0, "surfaces_any": SMOOTH, "pitch": M8},
                      {"name": "nut", "bbox": [6.8, 13, 15.01], "tol": 1.0, "surfaces_any": SMOOTH, "pitch": M8}],
            "solids": [2, 2], "no_interference": True, "clearance": [{"a": "bolt", "b": "nut", "range": [0.0, 0.4]}],
        },
        "look": ["hex head", "threaded shank", "hex nut", "thread inside the nut", "chamfers on head and nut"],
    },
    {
        "id": "spring",
        "prompt": "Model a compression spring: 1.5 mm wire, 20 mm outside diameter, 40 mm free length, 8 active "
                  "coils plus one closed coil at each end, ends ground flat.",
        "spec": {
            "parts": [{"name": "spring", "bbox": [20, 20, 40], "tol": 1.0, "volume": [800, 1250],
                       "surfaces_any": SMOOTH, "surfaces_min": {"Plane": 2}}],  # both ends ground flat
            "solids": [1, 1],
        },
        "solution": "spring",
        "look": ["helical coils", "closed end coils", "flat ground ends", "even pitch in the middle"],
    },
    {
        "id": "bottle",
        "prompt": "Model a 3D-printable bottle: the body is a loft from a 60 mm circle at the bottom to a 50 x 50 mm "
                  "square with 8 mm corner radius at 110 mm height, then a shoulder up to a 28 mm neck that ends at "
                  "150 mm total height, with an external thread on the neck (about 3 mm pitch, 1.5 turns). The "
                  "bottle is hollow with 1.6 mm walls and open at the neck.",
        "spec": {
            "parts": [{"name": "bottle", "bbox": [60, 60, 150], "tol": 1.5, "volume": [30000, 65000],
                       "holes": [{"d": [20, 26], "count": [1, 3]}], "surfaces_any": ["BSplineSurface"],
                       "pitch": {"value": 3.0, "tol": 0.5, "band": [13.0, 16.5]}}],  # neck thread
            "solids": [1, 1],
        },
        "look": ["round bottom", "rounded square top of the body", "smooth transition between them", "shoulder",
                 "neck", "thread on the neck", "open, hollow neck"],
    },
    {
        "id": "hinge",
        "prompt": "Build a hinge as an assembly (Assembly workbench with real joints): two leaves, each 40 x 20 x 3 mm "
                  "plus knuckles, sharing a 3 mm steel pin 40 mm long (the pin is a third part). Leave 0.3 mm "
                  "clearance around the pin in the knuckles and between neighbouring knuckles, add a revolute joint "
                  "between the leaves on the pin axis, fix the pin to one leaf, and give each leaf two countersunk "
                  "4 mm screw holes. Show it opened flat.",
        "spec": {
            "parts": [{"name": "pin", "bbox": [3, 3, 40], "tol": 0.2},
                      {"name": "leaf_a", "holes": LEAF_HOLES, "surfaces_any": ["Cone"]},
                      {"name": "leaf_b", "holes": LEAF_HOLES, "surfaces_any": ["Cone"]}],
            "solids": [3, 3], "no_interference": True, "joints": ["Revolute"],
            "clearance": [{"a": "leaf_a", "b": "leaf_b", "range": [0.1, 0.5]},
                          {"a": "pin", "b": "leaf_a", "range": [0.1, 0.5]},
                          {"a": "pin", "b": "leaf_b", "range": [0.1, 0.5]}],
            "requires": [{"label": "an assembly", "types": ["Assembly::AssemblyObject"]}],
        },
        "look": ["two leaves", "alternating knuckles", "pin through the knuckles", "countersunk screw holes",
                 "opened flat"],
    },
    {
        "id": "shelf_bracket",
        "prompt": "Design a 3D-printable L-shaped shelf bracket: vertical leg 100 mm, horizontal leg 150 mm, 30 mm "
                  "wide, 5 mm thick, with a diagonal brace between the legs and two 5 mm screw holes in each leg. "
                  "Make it parametric: a VarSet named Params holds VerticalLength, HorizontalLength, Width, "
                  "Thickness and HoleDiameter, and every dimension follows it.",
        "spec": {
            "parts": [{"name": "bracket", "bbox": [30, 100, 150], "tol": 0.5,
                       "holes": [{"d": [4.9, 5.3], "count": [4, 4]}]}],
            "solids": [1, 1], "params": ["VerticalLength", "HorizontalLength", "Width", "Thickness", "HoleDiameter"],
            "expressions_min": 5, "flex": {"params": ["VerticalLength", "HorizontalLength", "HoleDiameter"]},
        },
        "solution": "shelf_bracket",
        "look": ["vertical leg", "horizontal leg", "diagonal brace", "two holes in each leg"],
    },
    {
        "id": "from_picture",
        "prompt": f"Model the object in this picture as a solid for 3D printing: {FIXTURES / 'organizer.png'} . It is "
                  "100 mm long; take every other size from the picture and match every feature.",
        "spec": {
            "parts": [{"name": "organizer", "bbox": [100, 40, 30], "tol": 3,
                       "holes": [{"d": [12, 16], "count": [3, 3]}]}],
            "solids": [1, 1], "iou_min": 0.85,
        },
        "reference": "organizer", "solution": "organizer",
        "look": ["block body", "three round pen holes", "four slots", "chamfered top edges"],
    },
    {
        "id": "cat",
        "prompt": "Model a sitting cat figurine, 60 mm tall, for 3D printing in one piece with a flat base. Make it "
                  "look like a real cat.",
        "spec": {"height": [57, 63], "solids": [1, 3]},
        "look": ["sitting pose", "pointed ears", "eyes", "muzzle", "front legs", "tail around the body",
                 "flat base", "fur or surface detail"],
    },
    {
        "id": "dog",
        "prompt": "Model a standing Labrador retriever figurine, 50 mm tall at the head, for 3D printing in one "
                  "piece on a thin oval base. Make it look like a real dog.",
        "spec": {"height": [47, 53], "solids": [1, 3]},
        "look": ["four legs with paws", "deep chest and tucked belly", "broad head with a clear stop",
                 "hanging ears", "eyes and nose", "thick tapering tail", "short fur texture", "oval base"],
    },
    {
        "id": "person",
        "prompt": "Model a standing person figurine, 80 mm tall, in casual clothes (T-shirt, jeans, sneakers), arms "
                  "relaxed at the sides, on a round base, for 3D printing in one piece. Make it look like a real "
                  "person, with a face and hair.",
        "spec": {"height": [77, 83], "solids": [1, 10]},
        "look": ["natural proportions (about 7.5 heads tall)", "face with eyes, nose and mouth", "hair",
                 "arms with hands", "T-shirt with sleeves and folds", "jeans", "sneakers", "round base"],
    },
]


def get(task_id: str) -> dict[str, Any]:
    return next(t for t in TASKS if t["id"] == task_id)
