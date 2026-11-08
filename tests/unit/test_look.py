"""The chat's look: bubbles, tool icons, change tables, notes, in light and dark themes."""

from claudebridge.panel import look


def test_tools_get_their_own_symbols():
    assert look.tool_icon("run_python") == "🐍"
    assert look.tool_icon("check_all") == look.tool_icon("check_print") == "🔍"
    assert look.tool_icon("export") == "📦"
    assert look.tool_icon("something_new") == "🔧"


def test_your_message_is_a_bubble_and_text_is_escaped():
    html = look.user_bubble("<b>bold</b> & more", "", look.theme(dark=False))
    assert "&lt;b&gt;bold&lt;/b&gt; &amp; more" in html and "bgcolor=" in html and "You" in html


def test_changes_show_as_a_table_with_symbols():
    summary = {"created": ["Roof", "Wall", "Door", "Window", "Stairs"], "changed": ["Base"], "deleted": [],
               "errors": []}
    html = look.changes_table(summary, look.theme(dark=True))
    assert "<table" in html and "➕" in html and "✏️" in html and "🗑" not in html
    assert "5" in html and "Stairs" not in html and "…" in html, "long lists are cut"
    assert look.changes_table({"created": [], "changed": [], "deleted": [], "errors": []}, look.theme()) == ""


def test_dark_and_light_themes_differ_and_notes_have_symbols():
    assert look.theme(dark=True)["bubble"] != look.theme(dark=False)["bubble"]
    assert look.note("Saved", "info", look.theme()).count("ℹ️") == 1
    assert "⚠️" in look.note("Broken", "error", look.theme())
