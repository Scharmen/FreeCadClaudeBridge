"""Learning from feedback: feedback log, generalized lessons, context for new sessions."""

import json
import os
import sys

from freecad_bridge import cli, learning, settings


def test_record_feedback_and_lesson(tmp_path):
    learning.record_feedback(tmp_path, "bad", comment="too small", request="statue 5 cm",
                             result="40 mm statue", lesson="Sizes like '5 cm high' mean the overall height.")
    recs = [json.loads(line) for line in (tmp_path / learning.FEEDBACK).read_text().splitlines()]
    assert recs[0]["rating"] == "bad" and recs[0]["comment"] == "too small"
    assert learning.lessons(tmp_path) == ["Sizes like '5 cm high' mean the overall height."]
    if sys.platform != "win32":
        assert oct(os.stat(tmp_path / learning.FEEDBACK).st_mode & 0o777) == "0o600"


def test_lessons_are_deduplicated_and_capped(tmp_path):
    learning.add_lesson(tmp_path, "Prefer   lots of detail.")
    learning.add_lesson(tmp_path, "prefer lots of detail.")
    assert learning.lessons(tmp_path) == ["Prefer lots of detail."]
    for i in range(learning.MAX_LESSONS + 5):
        learning.add_lesson(tmp_path, f"lesson {i}")
    got = learning.lessons(tmp_path)
    assert len(got) == learning.MAX_LESSONS and got[-1] == f"lesson {learning.MAX_LESSONS + 4}"


def test_user_edits_to_the_file_are_respected(tmp_path):
    learning.add_lesson(tmp_path, "keep me")
    learning.add_lesson(tmp_path, "delete me")
    p = tmp_path / learning.PREFERENCES
    p.write_text(p.read_text().replace("- delete me\n", ""))
    assert learning.lessons(tmp_path) == ["keep me"]


def test_invalid_rating_rejected(tmp_path):
    try:
        learning.record_feedback(tmp_path, "meh")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_context_text_and_forget(tmp_path):
    assert learning.context_text(tmp_path) == ""
    learning.add_lesson(tmp_path, "Model organic shapes with many details.")
    text = learning.context_text(tmp_path)
    assert "Model organic shapes with many details." in text and "preferences" in text.lower()
    learning.forget(tmp_path)
    assert learning.lessons(tmp_path) == [] and not (tmp_path / learning.FEEDBACK).exists()


def test_ask_setting_exists_and_defaults_on(tmp_path):
    assert settings.load(tmp_path)["learning.ask_for_feedback"] == 1


def test_cli_preferences_and_hook_context(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FREECAD_BRIDGE_DIR", str(tmp_path))
    learning.add_lesson(tmp_path, "Use mm and keep parts printable.")
    assert cli.main(["preferences"]) == 0
    assert "Use mm and keep parts printable." in capsys.readouterr().out
    assert cli.main(["preferences", "context"]) == 0
    assert "Use mm and keep parts printable." in capsys.readouterr().out
    settings.save(tmp_path, {"learning.ask_for_feedback": 0})
    assert cli.main(["preferences", "context"]) == 0
    out = capsys.readouterr().out
    assert "do not ask" in out.lower()
    assert cli.main(["preferences", "forget"]) == 0
    assert learning.lessons(tmp_path) == []
