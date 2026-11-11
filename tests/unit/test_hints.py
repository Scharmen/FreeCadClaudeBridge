"""Hints appended to errors of Claude's own code."""

from claudebridge.bridge import hints

MISSING = AttributeError("'NoneType' object has no attribute 'Placement'")


def test_a_missing_object_gets_the_names_that_exist():
    hint = hints.for_error(MISSING, ["Tile_A_Farm", "Slide_print"])
    assert "getObject" in hint and "Tile_A_Farm, Slide_print" in hint


def test_long_documents_are_cut_and_other_errors_get_no_hint():
    hint = hints.for_error(MISSING, [f"Part{i}" for i in range(100)])
    assert "Part39" in hint and "Part40" not in hint and "100 in all" in hint
    assert hints.for_error(ValueError("x"), ["A"]) == ""
    assert hints.for_error(AttributeError("'Box' object has no attribute 'Foo'"), ["A"]) == ""
