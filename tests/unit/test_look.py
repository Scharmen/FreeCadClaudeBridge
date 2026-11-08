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


def test_line_breaks_inside_table_cells_do_not_break_the_table():
    md = ("| Group | Contents |\n|---|---|\n| 📐 Ground floor | **Kitchen:** fridge<br>**Living room:** sofa<br/>x |\n"
          "| 🧩 Roofs | three |\n\nFirst line<br>second line")
    out = look.markdown_for_qt(md)
    assert "<br" not in out
    assert "| 📐 Ground floor | **Kitchen:** fridge · **Living room:** sofa · x |" in out
    assert "First line  \nsecond line" in out, "outside a table it becomes a Markdown line break"


def test_file_paths_are_found_in_tool_results_also_with_spaces():
    import json

    result = json.dumps({"path": "/Users/k/Documents/FreeCAD Projects/Board/exports/board.3mf", "size_bytes": 9})
    assert look.files_in(result) == ["/Users/k/Documents/FreeCAD Projects/Board/exports/board.3mf"]
    text = "saved /tmp/a.stl and the folder /Users/k/x contains /Users/k/x/b.step"
    found = look.files_in(text)
    assert "/tmp/a.stl" in found and "/Users/k/x/b.step" in found
    win = json.dumps({"file": "C:\\Users\\k\\Board\\Board.FCStd"})
    assert look.files_in(win) == ["C:\\Users\\k\\Board\\Board.FCStd"]


def test_only_files_made_in_this_turn_outside_temp_and_bridge_folders_count(tmp_path):
    import os
    import time

    new, old, tmp, bridge = (tmp_path / "proj" / "new.3mf", tmp_path / "proj" / "old.stl",
                             tmp_path / "tmp" / "shot.png", tmp_path / "bridge" / "snap.FCStd")
    for f in (new, old, tmp, bridge):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    since = time.time() - 5
    os.utime(old, (since - 100, since - 100))
    found = look.fresh_files([str(new), str(old), str(tmp), str(bridge), str(tmp_path / "gone.stl"), str(new)],
                             since, skip=[str(tmp_path / "tmp"), str(tmp_path / "bridge")])
    assert found == [str(new)], "made now, exists, not temporary, listed once"


def test_new_files_show_as_a_table_with_open_and_reveal_links():
    html = look.files_table(["/p/Board/exports/board.3mf", "/p/Board/Board.FCStd"], look.theme(), "Finder")
    assert "href='open:/p/Board/exports/board.3mf'" in html and "board.3mf" in html
    assert "href='reveal:/p/Board/Board.FCStd'" in html and "Show in Finder" in html and "📂" in html
    assert look.files_table([], look.theme(), "Finder") == ""
