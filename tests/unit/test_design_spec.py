"""Design benchmark: a numeric spec scored against measurements of the finished model."""

import json

import pytest
from design import tasks

from freecad_bridge.checks import spec


def part(label, dims, holes=(), volume=1000.0, valid=True, center=(0, 0, 0), **extra):
    p = {"label": label, "dims": list(dims), "sorted": sorted(dims), "center": list(center), "volume": volume,
         "valid": valid, "closed": True, "solids": 1,
         "holes": [{"d": d, "axis": list(axis), "point": list(pt)} for d, axis, pt in holes],
         "radii": [], "surfaces": {"Plane": 6}}
    p.update(extra)
    return p


def measured(*parts, **extra):
    m = {"parts": list(parts), "errors": [], "params": {}, "expressions": 0, "sketches": [], "types": {},
         "interference": [], "flex": [], "iou": None}
    m.update(extra)
    return m


Z = (0, 0, 1)
PLATE = {"parts": [{"name": "plate", "bbox": [60, 40, 5], "tol": 0.5,
                    "holes": [{"d": [4.3, 4.8], "count": [2, 2]}]}],
         "solids": [1, 1]}


def by_name(result):
    return {c["name"]: c for c in result["checks"]}


def test_a_model_that_matches_scores_full_marks():
    m = measured(part("Plate", (40, 60, 5), holes=[(4.5, Z, (10, 10, 0)), (4.5, Z, (50, 10, 0))]))
    r = spec.evaluate(PLATE, m)
    assert r["score"] == 100.0
    assert all(c["ok"] for c in r["checks"])


def test_size_is_compared_independent_of_orientation_unless_asked():
    m = measured(part("Plate", (5, 40, 60), holes=[(4.5, Z, (0, 0, 0)), (4.5, Z, (9, 0, 0))]))
    assert by_name(spec.evaluate(PLATE, m))["plate: size"]["ok"]
    upright = {"parts": [{**PLATE["parts"][0], "oriented": True}], "solids": [1, 1]}
    bad = by_name(spec.evaluate(upright, m))["plate: size"]
    assert not bad["ok"] and "60" in bad["detail"]


def test_wrong_holes_and_sizes_lose_points_and_say_why():
    m = measured(part("Plate", (60, 40, 6.2), holes=[(4.0, Z, (10, 10, 0))]))
    r = spec.evaluate(PLATE, m)
    c = by_name(r)
    assert not c["plate: size"]["ok"] and "6.2" in c["plate: size"]["detail"]
    assert not c["plate: holes 4.3–4.8 mm"]["ok"] and "found 0" in c["plate: holes 4.3–4.8 mm"]["detail"]
    assert 0 < r["score"] < 100


def test_a_missing_part_fails_its_checks_and_invalid_shapes_fail():
    two = {"parts": [{"name": "body", "bbox": [80, 50, 22], "tol": 0.5}, {"name": "lid", "bbox": [80, 50, 3]}],
           "solids": [2, 2]}
    r = spec.evaluate(two, measured(part("Body", (80, 50, 22), valid=False)))
    c = by_name(r)
    assert not c["lid: found"]["ok"]
    assert not c["solids 2–2"]["ok"]
    assert not c["valid shapes"]["ok"] and "Body" in c["valid shapes"]["detail"]


def test_parts_are_matched_to_the_closest_solid():
    two = {"parts": [{"name": "small", "bbox": [8, 22, 22]}, {"name": "big", "bbox": [8, 42, 42]}],
           "center_distance": {"a": "small", "b": "big", "value": 30, "tol": 0.1}}
    m = measured(part("G40", (42, 42, 8), center=(30, 0, 4)), part("G20", (22, 22, 8), center=(0, 0, 4)))
    r = spec.evaluate(two, m)
    assert r["matched"] == {"small": "G20", "big": "G40"}
    assert by_name(r)["centre distance small–big"]["ok"]


def test_interference_errors_and_parameters():
    s = {"parts": [{"name": "a", "bbox": [10, 10, 10]}], "no_interference": True,
         "params": ["Length", "HoleDiameter"], "expressions_min": 3}
    m = measured(part("A", (10, 10, 10)), interference=[{"a": "A", "b": "B", "volume": 12.0}],
                 errors=["Pad001"], params={"length": 100, "Width": 30}, expressions=2)
    c = by_name(spec.evaluate(s, m))
    assert not c["no interference"]["ok"] and "12" in c["no interference"]["detail"]
    assert not c["no errors in the tree"]["ok"]
    assert not c["parameters"]["ok"] and "HoleDiameter" in c["parameters"]["detail"]
    assert not c["expressions ≥ 3"]["ok"]


def test_holes_lined_up_between_two_parts():
    s = {"parts": [{"name": "lid", "bbox": [80, 50, 3]}, {"name": "body", "bbox": [80, 50, 22]}],
         "aligned_holes": {"a": "lid", "b": "body", "d_a": [3.2, 3.6], "d_b": [3.8, 4.3], "count": 4, "tol": 0.3}}
    corners = [(6, 6), (74, 6), (6, 44), (74, 44)]
    lid = part("Lid", (80, 50, 3), holes=[(3.4, Z, (x, y, 30)) for x, y in corners])
    body = part("Body", (80, 50, 22), holes=[(4.0, (0, 0, -1), (x, y, 5)) for x, y in corners[:3]]
                + [(4.0, Z, (74, 46, 5))])
    c = by_name(spec.evaluate(s, measured(lid, body)))["holes line up lid–body"]
    assert not c["ok"] and "3 of 4" in c["detail"]


def test_flex_test_and_iou_give_partial_credit():
    s = {"parts": [{"name": "a", "bbox": [10, 10, 10]}], "flex": {"params": ["Length"]}, "iou_min": 0.9}
    m = measured(part("A", (10, 10, 10)), iou=0.72,
                 flex=[{"param": "Length", "factor": 0.8, "ok": True, "changed": True},
                       {"param": "Length", "factor": 1.2, "ok": False, "changed": True, "error": "Pad: invalid"}])
    r = spec.evaluate(s, m)
    c = by_name(r)
    assert c["shape matches the reference (IoU)"]["credit"] == pytest.approx(0.8)
    assert not c["survives parameter changes"]["ok"] and "1.2" in c["survives parameter changes"]["detail"]
    assert 0 < r["score"] < 100


def test_counts_of_teeth_radii_and_surfaces():
    s = {"parts": [{"name": "g", "bbox": [8, 22, 22], "teeth": 20, "radii_min": {"r": [0.8, 1.2], "count": 4},
                    "surfaces_any": ["BSplineSurface", "SurfaceOfExtrusion"], "surfaces_min": {"Plane": 3}}]}
    m = measured(part("G", (22, 22, 8), teeth=19, radii=[1.0, 1.0, 0.5], surfaces={"Plane": 2}))
    c = by_name(spec.evaluate(s, m))
    assert not c["g: 20 teeth"]["ok"] and "19" in c["g: 20 teeth"]["detail"]
    assert not c["g: round faces r 0.8–1.2"]["ok"]
    assert not c["g: curved surfaces"]["ok"]
    assert not c["g: ≥ 3 Plane faces"]["ok"] and "2 Plane" in c["g: ≥ 3 Plane faces"]["detail"]


def test_height_and_total_size():
    s = {"height": [57, 63], "total": {"bbox": [60, 40, 30], "tol": 2}}
    m = measured(part("Cat", (30, 25, 60)), total=[61, 41, 30], total_z=60)
    c = by_name(spec.evaluate(s, m))
    assert c["height 57–63 mm"]["ok"]
    assert c["total size"]["ok"]


def test_scoring_is_deterministic():
    m = measured(part("Plate", (40, 60, 5), holes=[(4.5, Z, (10, 10, 0))]))
    a, b = spec.evaluate(PLATE, m), spec.evaluate(PLATE, json.loads(json.dumps(m)))
    assert a == b


def test_every_task_is_complete_and_its_spec_is_known():
    assert len(tasks.TASKS) == 17
    ids = [t["id"] for t in tasks.TASKS]
    assert len(set(ids)) == len(ids)
    for t in tasks.TASKS:
        assert t["prompt"] and t["look"], t["id"]
        unknown = set(t["spec"]) - spec.KNOWN
        assert not unknown, (t["id"], unknown)
        for p in t["spec"].get("parts", []):
            assert set(p) <= spec.PART_KEYS, (t["id"], set(p) - spec.PART_KEYS)
        if t["spec"].get("iou_min"):
            assert t.get("reference"), t["id"]
        assert spec.evaluate(t["spec"], measured())["score"] < 50, t["id"]  # an empty document fails


def test_run_statistics_come_from_claude_codes_stream():
    from design import run

    lines = [
        {"type": "system", "subtype": "init", "model": "claude-opus-5-5"},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Plan: …"},
            {"type": "tool_use", "id": "t1", "name": "mcp__plugin_freecad_freecad__run_python", "input": {}},
            {"type": "tool_use", "id": "t2", "name": "mcp__plugin_freecad_freecad__view",
             "input": {"screenshot": True}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "Traceback …"},
            {"type": "tool_result", "tool_use_id": "t2", "content": [{"type": "text", "text": "x" * 4000}]}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Done. Why it looks like this: …"}]}},
        {"type": "result", "subtype": "success", "duration_ms": 61000, "num_turns": 3, "total_cost_usd": 0.42,
         "usage": {"input_tokens": 10, "output_tokens": 2500, "cache_read_input_tokens": 90000,
                   "cache_creation_input_tokens": 5000}, "result": "Done. Why it looks like this: …"},
    ]
    s = run.stream_stats(json.dumps(x) for x in lines)
    assert s["tools"] == ["run_python", "view"] and s["tool_errors"] == ["run_python"]
    assert s["screenshots"] == 1 and s["max_result_chars"] == 4000
    assert s["seconds"] == 61 and s["turns"] == 3 and s["cost_usd"] == 0.42 and s["output_tokens"] == 2500
    assert s["model"] == "claude-opus-5-5" and s["answer"].startswith("Done.") and s["ok"]
    limit = run.stream_stats([json.dumps({"type": "result", "subtype": "success", "is_error": True,
                                          "api_error": "usage_limit_reached", "result": "limit"})])
    assert limit["api_error"] == "usage_limit_reached" and not limit["ok"]


def test_gaps_sloped_faces_and_thread_pitch():
    s = {"parts": [{"name": "bolt", "bbox": [13, 15, 35], "pitch": {"value": 1.25, "tol": 0.1, "band": [3, 4.6]}},
                   {"name": "nut", "bbox": [7, 13, 15], "inclined_min": 1}],
         "clearance": [{"a": "bolt", "b": "nut", "range": [0.0, 0.4]}]}
    m = measured(part("Bolt", (13, 15, 35), thread={"crests": 20, "pitch": 1.5}),
                 part("Nut", (7, 13, 15), inclined=0), gaps=[{"a": "Nut", "b": "Bolt", "d": 0.6}])
    c = by_name(spec.evaluate(s, m))
    assert not c["bolt: thread pitch 1.25"]["ok"] and "1.5" in c["bolt: thread pitch 1.25"]["detail"]
    assert not c["nut: sloped faces"]["ok"]
    assert not c["gap bolt–nut 0–0.4 mm"]["ok"] and "0.6" in c["gap bolt–nut 0–0.4 mm"]["detail"]


def test_an_open_bore_counts_only_where_the_spec_allows_it():
    ring = part("Bracket", (60, 40, 50), holes=[(22.3, Z, (0, 0, 0)), (4.5, Z, (9, 0, 0))])
    ring["holes"][0]["open"] = True
    closed = {"parts": [{"name": "b", "holes": [{"d": [22, 23], "count": [1, 1]}]}]}
    clip = {"parts": [{"name": "b", "holes": [{"d": [22, 23], "count": [1, 1], "open": True}]}]}
    assert not by_name(spec.evaluate(closed, measured(ring)))["b: holes 22–23 mm"]["ok"]
    assert by_name(spec.evaluate(clip, measured(ring)))["b: holes 22–23 mm"]["ok"]



def test_a_seated_clearance_counts_the_sides_when_the_parts_touch():
    s = {"parts": [{"name": "box", "bbox": [60, 40, 24]}, {"name": "lid", "bbox": [60, 40, 6]}],
         "clearance": [{"a": "box", "b": "lid", "range": [0.1, 0.5], "seated": True}]}
    parts = part("Box", (60, 40, 24)), part("Lid", (60, 40, 6))
    good = measured(*parts, gaps=[{"a": "Box", "b": "Lid", "d": 0.0, "side_gap": 0.2}])
    assert by_name(spec.evaluate(s, good))["gap box–lid 0.1–0.5 mm"]["ok"]
    bad = measured(*parts, gaps=[{"a": "Box", "b": "Lid", "d": 0.0, "side_gap": 0.0}])
    assert not by_name(spec.evaluate(s, bad))["gap box–lid 0.1–0.5 mm"]["ok"]
    no_seat = measured(*parts, gaps=[{"a": "Box", "b": "Lid", "d": 0.0}])
    assert not by_name(spec.evaluate(s, no_seat))["gap box–lid 0.1–0.5 mm"]["ok"]
