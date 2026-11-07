"""make_part's catalog: defaults, and wrong requests refused with a reason before any geometry."""

import pytest

from freecad_bridge.parts import catalog


def LINE(y):  # noqa: N802
    return {"points": [[0, y, 1], [3, y, 2], [6, y, 2], [9, y, 1]]}


def test_defaults_are_filled_in_and_values_converted():
    p = catalog.resolve("gear", {"teeth": "24", "module": 2})
    assert p["teeth"] == 24 and isinstance(p["teeth"], int) and p["module"] == 2.0 and p["pressure_deg"] == 20.0


@pytest.mark.parametrize(("family", "params", "says"), [
    ("unicorn", {}, "available"),
    ("gear", {"tooth": 20}, "tooth"),
    ("gear", {"teeth": 4}, "8 teeth"),
    ("gear", {"teeth": 10, "bore": 9}, "root circle"),
    ("gear", {"module": -1}, "negative"),
    ("gear", {"teeth": "many"}, "int"),
    ("bolt", {"size": "M7"}, "M8"),
    ("spring", {"wire": 5, "free_length": 20}, "solid"),
    ("spring", {"wire": 12, "free_length": 200}, "too thick"),
    ("hinge", {"knuckles": 4}, "odd"),
    ("enclosure", {"lid": 30}, "lower"),
    ("snap_box", {"material": "gold"}, "PETG"),
    ("knob", {"diameter": 8}, "wall"),
    ("faired_body", {"lines": []}, "at least 3 lines"),
    ("faired_body", {"lines": [LINE(0), LINE(1)]}, "at least 3 lines"),
    ("faired_body", {"lines": [LINE(1), LINE(2), LINE(0)]}, "first line"),
    ("faired_body", {"lines": [LINE(0), LINE(2), LINE(1)]}, "last line"),
    ("faired_body", {"lines": [LINE(0), {"points": [[0, 1, 1], [1, 1, 1]]}, LINE(0)]}, "4 points"),
    ("faired_body", {"lines": [LINE(0), {"points": [[0, 1, 1], [2, 1, 1], [1, 1, 1], [9, 1, 1]]}, LINE(0)]}, "rear"),
    ("faired_body", {"lines": [LINE(0), {"points": [[3, 1, 1], [4, 1, 1], [5, 1, 1], [9, 1, 1]]}, LINE(0)]}, "start"),
    ("faired_body", {"lines": [LINE(0), {"points": [[0, 1, 1], [1, 1], [5, 1, 1], [9, 1, 1]]}, LINE(0)]}, "x, y, z"),
    ("faired_body", {"lines": [LINE(0), LINE(-1), LINE(0)]}, "negative y"),
    ("faired_body", {"lines": [LINE(0), LINE(1), LINE(0)], "poles": 3}, "poles"),
])
def test_impossible_requests_say_why(family, params, says):
    with pytest.raises(ValueError, match=says):
        catalog.resolve(family, params)


def test_every_family_has_a_description_and_valid_defaults():
    for name, info in catalog.describe().items():
        assert info["what"] and info["params"]
        catalog.resolve(name)


def test_faired_body_keeps_its_lines():
    lines = [LINE(0), {**LINE(5), "crease": True}, LINE(0)]
    p = catalog.resolve("faired_body", {"lines": lines, "name": "Lower"})
    assert p["lines"] == lines and p["poles"] == 13 and p["name"] == "Lower"


def test_a_thread_family_covers_bottle_necks_and_caps():
    p = catalog.resolve("thread", {"major": 32, "pitch": 3, "length": 4.5})
    assert p["kind"] == "external" and p["clearance"] == 0.3 and p["major"] == 32.0
    assert "thread" in catalog.describe() and "neck" in catalog.describe()["thread"]["what"]
    with pytest.raises(ValueError, match="kind"):
        catalog.resolve("thread", {"kind": "sideways"})
    with pytest.raises(ValueError, match="pitch"):
        catalog.resolve("thread", {"major": 6, "pitch": 3})


def test_car_detail_families_have_sensible_defaults_and_refuse_nonsense():
    wheel = catalog.resolve("wheel", {"diameter": 60, "spokes": 5})
    assert wheel["width"] < wheel["diameter"] and wheel["rim"] < wheel["diameter"] and wheel["bore"] > 0
    lamp = catalog.resolve("lamp", {"width": 30})
    assert lamp["shape"] == "rect" and lamp["depth"] > 0
    pattern = catalog.resolve("pattern", {"cells": "honeycomb", "cell": 4})
    assert pattern["bar"] < pattern["cell"] and pattern["kind"] == "panel"
    for family, params, says in (("wheel", {"rim": 70, "diameter": 60}, "rim"),
                                 ("wheel", {"spokes": 1}, "spokes"),
                                 ("lamp", {"shape": "star"}, "shape"),
                                 ("pattern", {"cells": "paisley"}, "cells"),
                                 ("pattern", {"bar": 5, "cell": 4}, "bar")):
        with pytest.raises(ValueError, match=says):
            catalog.resolve(family, params)
