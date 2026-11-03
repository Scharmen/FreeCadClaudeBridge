"""Design notes: why the model looks the way it does, kept in the document."""

import pytest

from freecad_bridge import notes


def test_notes_render_decision_reason_and_source():
    text = notes.render("Labrador, 8 cm long", [
        {"part": "Legs", "decision": "leg length 0.55 of the shoulder height",
         "reason": "Labradors are about as long as they are tall, legs a bit over half the height",
         "source": "reference"},
        {"part": "Tail", "decision": "thick otter tail, tapering", "reason": "breed trait", "source": "assumption"},
    ], when="2026-11-02 16:40")
    assert text.startswith("## 2026-11-02 16:40 · Labrador, 8 cm long\n")
    assert "- **Legs:** leg length 0.55 of the shoulder height. Why: Labradors are about" in text
    assert "(real-world reference)" in text and "(my assumption, tell me if it is wrong)" in text


def test_notes_need_a_decision_and_a_reason():
    with pytest.raises(ValueError, match="reason"):
        notes.render("x", [{"part": "Ears", "decision": "floppy"}])
    with pytest.raises(ValueError, match="at least one"):
        notes.render("x", [])


def test_unknown_sources_are_kept_as_written():
    assert "(FCI breed standard)" in notes.render("x", [{"decision": "a", "reason": "b",
                                                          "source": "FCI breed standard"}], when="t")
