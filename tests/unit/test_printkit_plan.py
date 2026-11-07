"""The print export's decisions, without FreeCAD: printers, colours, moving parts, layout, assembly order."""

import pytest

from claudebridge.printkit import plan


def obj(name, lo, hi, color="#ff0000"):
    return {"name": name, "label": name, "lo": list(lo), "hi": list(hi), "color": color}


def wheel(name, x, y0, y1, r=10, z=10, **kw):
    return obj(name, (x - r, y0, z - r), (x + r, y1, z + r), **kw)


def test_printers_have_what_the_export_needs():
    assert plan.PRINTERS, "at least one preset"
    for p in plan.PRINTERS.values():
        assert len(p["bed"]) == 3 and p["nozzle"] > 0 and p["slots"] >= 1 and p["process"] in ("fdm", "resin")


def test_limits_follow_the_process():
    fdm = plan.limits(plan.PRINTERS["Anycubic Kobra S1"])
    resin = plan.limits({"process": "resin", "nozzle": 0.05, "bed": [100, 100, 100], "slots": 1})
    assert fdm["dowel_d"] >= 2 and fdm["hole_gap"] > resin["hole_gap"]
    assert fdm["free_gap"] > fdm["press_gap"], "an axle turns in its bearing and sits tight in the other part"
    assert fdm["min_thick"] >= 2 * 0.4


def test_colour_names_and_groups():
    assert plan.color_name("#0A5A2B", "de") == "Gruen"
    assert plan.color_name("#141416", "en") == "Black"
    groups = plan.group_colors({"a": "#0A5A2B", "b": "#0B5C2C", "c": "#C8141A"})
    assert groups["a"] == groups["b"] != groups["c"], "nearly the same colour is one filament"


def test_more_colours_than_slots_merge_the_closest():
    colors = {"a": "#ff0000", "b": "#ee1111", "c": "#000000", "d": "#ffffff", "e": "#00ff00"}
    slots = plan.assign_slots(colors, 3)
    assert set(slots.values()) == {1, 2, 3} and slots["a"] == slots["b"]


def test_wheels_are_found_with_their_axle():
    objs = [obj("Body", (0, -20, 5), (100, 20, 40), "#00ff00"),
            wheel("Tyre_FL", 20, -22, -12), wheel("Rim_FL", 20, -22, -12, r=7),
            wheel("Tyre_FR", 20, 12, 22), wheel("Rim_FR", 20, 12, 22, r=7),
            wheel("Axle_F", 20, -22, 22, r=1)]
    found = plan.detect_moving(objs)
    assert len(found) == 2
    left = next(m for m in found if "Tyre_FL" in m["objects"])
    assert set(left["objects"]) == {"Tyre_FL", "Rim_FL"} and left["axis"] == "y" and left["axle"] == "Axle_F"
    assert left["checked"] is True


def test_round_things_without_a_hint_or_a_partner_are_not_moving():
    objs = [obj("Body", (0, 0, 0), (100, 100, 40)), wheel("Boss", 50, 0, 3, r=4)]
    assert plan.detect_moving(objs) == []


def test_a_named_knob_is_offered():
    objs = [obj("Box", (0, 0, 0), (100, 100, 40)), obj("Knob", (40, 40, 40), (60, 60, 50))]
    found = plan.detect_moving(objs)
    assert [m["objects"] for m in found] == [["Knob"]] and found[0]["axis"] == "z" and found[0]["axle"] is None


def test_layout_fills_plates_and_opens_a_new_one():
    bed = [100, 100, 100]
    placed, too_big = plan.layout([("a", 60, 60, 5), ("b", 60, 60, 5), ("c", 20, 20, 5), ("d", 300, 10, 5)], bed)
    assert too_big == ["d"]
    assert max(p["plate"] for p in placed.values()) == 2
    for p in placed.values():
        assert p["x"] >= 0 and p["y"] >= 0
    assert placed["a"]["plate"] != placed["b"]["plate"]


def test_assembly_starts_at_the_biggest_and_follows_contacts():
    pieces = [{"id": "body", "volume": 100}, {"id": "glass", "volume": 5}, {"id": "lamp", "volume": 2},
              {"id": "mirror", "volume": 1}]
    joints = [{"a": "body", "b": "glass"}, {"a": "glass", "b": "lamp"}, {"a": "body", "b": "mirror"}]
    order = plan.assembly_order(pieces, joints)
    assert order[0] == "body" and order.index("glass") < order.index("lamp") and sorted(order) == sorted(
        p["id"] for p in pieces)


def test_loose_pieces_still_get_a_place_in_the_order():
    order = plan.assembly_order([{"id": "a", "volume": 9}, {"id": "b", "volume": 1}], [])
    assert order == ["a", "b"]


@pytest.mark.parametrize("lang", ["de", "en"])
def test_every_text_exists_in_both_languages(lang):
    assert set(plan.TEXT[lang]) == set(plan.TEXT["de"]) == set(plan.TEXT["en"])


def test_only_the_hub_goes_on_the_axle_and_the_tyre_is_glued_to_it():
    t = plan.TEXT["en"]
    name = {"rim": "Rim", "tyre": "Tyre", "body": "Body"}
    spin = {"type": "through", "axle": "Axle", "pieces": ["rim", "tyre"], "hub": ["rim"]}
    moving = {"rim": spin, "tyre": spin}
    joints = [{"a": "tyre", "b": "rim", "type": "glue", "count": 0}]
    done: set[str] = set()
    rim = plan.step_lines(t, "rim", joints, {"body"}, name, moving, done)
    tyre = plan.step_lines(t, "tyre", joints, {"body", "rim"}, name, moving, done)
    assert any("through the bores" in x for x in rim) and any("onto the end of Axle" in x for x in rim)
    assert tyre == ["Glue Tyre to Rim."]
    other = plan.step_lines(t, "rim", [], {"body"}, name, moving, done)
    assert not any("through the bores" in x for x in other), "the axle goes in once"


@pytest.mark.parametrize(("colors", "slots", "fits", "wanted"), [
    (4, 4, True, False), (5, 4, True, True), (1, 1, True, False), (3, 1, True, True), (2, 4, False, True),
])
def test_splitting_is_only_offered_by_default_when_one_print_cannot_do_it(colors, slots, fits, wanted):
    assert plan.split_needed(colors, slots, fits) is wanted


def test_the_count_of_prints_is_what_the_user_has_to_print():
    assert plan.print_count({"pieces": [{}, {}, {}], "together": {"moving": [{}, {}]}}) == 3
    assert plan.print_count({"pieces": [], "together": {"moving": [{}, {}]}}) == 3
    assert plan.print_count({"pieces": [], "together": None}) == 1


def test_the_steps_for_one_print_are_only_the_moving_parts():
    t = plan.TEXT["en"]
    lines = plan.together_lines(t, [
        {"type": "stub", "objects": ["Tyre", "Rim"], "host": "Body"},
        {"type": "through", "objects": ["Wheel_L"], "axle": "Axle"},
        {"type": "through", "objects": ["Wheel_R"], "axle": "Axle"},
        {"type": "none", "objects": ["Lost"]},
    ])
    assert len(lines) == 4
    assert "Tyre / Rim" in lines[0] and "Body" in lines[0]
    assert sum("Slide Axle" in ln for ln in lines) == 1, "the axle goes in once"
    assert not any("Lost" in ln for ln in lines), "a part with no axle is a warning, not a step"
