"""make_part's catalog: defaults, and wrong requests refused with a reason before any geometry."""

import pytest

from freecad_bridge.parts import catalog


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
])
def test_impossible_requests_say_why(family, params, says):
    with pytest.raises(ValueError, match=says):
        catalog.resolve(family, params)


def test_every_family_has_a_description_and_valid_defaults():
    for name, info in catalog.describe().items():
        assert info["what"] and info["params"]
        catalog.resolve(name)
