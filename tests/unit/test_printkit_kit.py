# ruff: noqa: E501
"""The kit's decisions without FreeCAD: which bodies print together, what a colour costs, how prints join."""

from claudebridge.printkit import plan

BED = [250.0, 250.0, 250.0]


def body(pid, color, volume, lo=(0, 0, 0), hi=(10, 10, 10)):
    return {"id": pid, "color": color, "volume": volume, "lo": list(lo), "hi": list(hi)}


def ids(prints):
    return [sorted(p) for p in prints]


def test_colours_that_fit_the_slots_and_the_bed_are_one_print():
    bodies = [body("body", "green", 100), body("roof", "black", 30), body("lamp", "silver", 2), body("stripe", "red", 1)]
    touching = [("body", "roof"), ("body", "lamp"), ("roof", "stripe")]
    prints = plan.group_prints(bodies, touching, 4, BED)
    assert ids(prints) == [["body", "lamp", "roof", "stripe"]] and prints[0][0] == "body", "the biggest body leads"


def test_with_one_slot_every_body_is_its_own_print():
    bodies = [body("body", "green", 100), body("roof", "black", 30)]
    assert ids(plan.group_prints(bodies, [("body", "roof")], 1, BED)) == [["body"], ["roof"]]


def test_the_colours_that_take_most_details_stay_in_the_main_print():
    bodies = [body("body", "white", 100)] + [body(f"trim{i}", "black", 1) for i in range(5)] + [
        body("lamp", "red", 3), body("badge", "silver", 2)]
    touching = [("body", b["id"]) for b in bodies[1:]]
    prints = plan.group_prints(bodies, touching, 2, BED)
    assert sorted(prints[0]) == ["body", *[f"trim{i}" for i in range(5)]], "white and black: six bodies, one print"
    assert sorted(ids(prints)[1:]) == [["badge"], ["lamp"]]


def test_details_of_a_left_out_colour_that_touch_each_other_print_together():
    bodies = [body("body", "white", 100), body("lamp", "red", 3), body("lens", "orange", 1)]
    prints = plan.group_prints(bodies, [("body", "lamp"), ("lamp", "lens")], 1, BED)
    assert ids(prints) == [["body"], ["lamp"], ["lens"]]
    prints = plan.group_prints([body("body", "white", 100), body("a", "white", 5), body("lamp", "red", 3),
                                body("lens", "orange", 1), body("x", "blue", 2), body("y", "blue", 2)],
                               [("body", "a"), ("body", "x"), ("body", "y"), ("body", "lamp"), ("lamp", "lens")], 2, BED)
    assert ["lamp", "lens"] in ids(prints), "two left-over colours that touch share a print"


def test_bodies_that_together_miss_the_bed_stay_apart():
    bodies = [body("a", "red", 100, (0, 0, 0), (100, 40, 25)), body("b", "red", 90, (100, 0, 0), (200, 40, 25)),
              body("c", "blue", 5, (40, 0, 25), (60, 40, 30))]
    prints = plan.group_prints(bodies, [("a", "b"), ("a", "c")], 4, [110, 110, 110])
    assert ids(prints) == [["a", "c"], ["b"]]


def test_bodies_that_do_not_touch_are_separate_prints():
    assert len(plan.group_prints([body("a", "red", 5), body("b", "red", 4)], [], 4, BED)) == 2


def test_plates_never_need_more_filaments_than_the_printer_has():
    colours = {"main": {"white", "black", "red", "silver"}, "lamp": {"orange"}, "trim": {"black", "red"},
               "badge": {"orange", "blue"}}
    groups = plan.plate_groups(colours, 4)
    assert groups == [["main", "trim"], ["lamp", "badge"]]
    for g in groups:
        assert len(set().union(*(colours[i] for i in g))) <= 4


def test_colour_on_few_layers_is_cheap_and_a_stripe_up_the_height_is_not():
    inlay = plan.color_layers([("white", 0.0, 20.0), ("red", 19.6, 20.0)])
    assert inlay == {"layers": 100, "mixed_layers": 2, "changes": 2}
    stripe = plan.color_layers([("white", 0.0, 20.0), ("red", 0.0, 20.0), ("blue", 10.0, 20.0)])
    assert stripe["mixed_layers"] == 100 and stripe["changes"] == 150
    assert plan.color_layers([("white", 0.0, 5.0), ("white", 2.0, 8.0)])["changes"] == 0, "one colour, no change"


def test_every_print_is_joined_once_and_plugged_joints_win_over_glue():
    volume = {"roof": 100, "base": 90, "slab": 50, "lamp": 2, "alone": 3}
    edges = [("slab", "roof", True), ("slab", "base", False), ("base", "roof", False), ("lamp", "roof", True),
             ("lamp", "slab", False)]
    order, chosen, loose = plan.join_tree(volume, edges)
    assert order[0] == "roof" and set(order) == set(volume) and loose == ["alone"]
    assert sorted(chosen) == [1, 2, 4], "three joints for four prints, none of them glued"
    assert order.index("base") < order.index("slab") < order.index("lamp")


def test_glue_is_taken_only_when_a_print_cannot_be_plugged_anywhere():
    order, chosen, loose = plan.join_tree({"body": 9, "rod": 1}, [("rod", "body", True)])
    assert order == ["body", "rod"] and chosen == [0] and loose == []


def test_a_wheel_is_built_from_its_hub():
    order, _chosen, _loose = plan.join_tree({"tyre": 9, "rim": 4}, [("rim", "tyre", False)], start="rim")
    assert order == ["rim", "tyre"]


def test_the_summary_says_what_the_user_prints_and_what_still_needs_glue():
    report = {
        "prints": [{"id": "Body", "pieces": ["Body", "Trim"], "color_names": ["White", "Black"], "size_mm": [100, 40, 30],
                    "mixed_layers": 4, "changes": 4, "moving": False, "support": True},
                   {"id": "Lamp", "pieces": ["Lamp"], "color_names": ["Red"], "size_mm": [5, 4, 3], "mixed_layers": 0,
                    "changes": 0, "moving": False, "support": False},
                   {"id": "Badge", "pieces": ["Badge"], "color_names": ["Blue"], "size_mm": [12, 4, 3],
                    "mixed_layers": 0, "changes": 0, "moving": False, "support": False}],
        "pieces": [{}, {}, {}, {}],
        "joints": [{"a": "Body", "b": "Lamp", "type": "peg"}, {"a": "Body", "b": "Badge", "type": "glue",
                                                              "why": "no flat face with room"}],
        "painted": ["Stripe"], "moving": [], "warnings": [], "colors": 4,
    }
    s = plan.kit_summary(report)
    assert s["prints"] == 3 and s["bodies"] == 4 and s["colours"] == 4 and s["joints"] == {"peg": 1, "glue": 1}
    assert s["glue"] == [{"part": "Badge", "on": "Body", "why": "no flat face with room"}]
    assert s["small_loose_parts"] == ["Lamp"], "under 8 mm and printed on its own"
    assert s["colour_change_layers"] == 4 and s["painted"] == ["Stripe"] and s["needs_support"] == ["Body"]
    assert s["print_list"][0] == {"print": "Body", "bodies": 2, "colours": ["White", "Black"], "size_mm": [100, 40, 30]}


def test_the_guide_has_a_sentence_for_every_kind_of_joint():
    t = plan.TEXT["en"]
    name = {"body": "Body", "lamp": "Lamp"}
    for kind, word in (("peg", "peg"), ("pocket", "pocket"), ("seat", "seat"), ("dowel", "pin"), ("glue", "Glue")):
        lines = plan.step_lines(t, "lamp", [{"a": "body", "b": "lamp", "type": kind, "count": 1}], {"body"}, name, {}, set())
        assert len(lines) == 1 and word in lines[0] and "Lamp" in lines[0] and "Body" in lines[0], lines
