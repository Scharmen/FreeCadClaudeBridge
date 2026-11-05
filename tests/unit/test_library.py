"""Good designs are kept locally and found again for similar requests."""

import json
import os
import stat

import anyio

from freecad_bridge import library, mcp_server


def test_a_good_design_is_stored_and_found_again(tmp_path):
    i = library.record(tmp_path, "Make a flanged bearing housing for a 608", "housing 50 x 50", "perfect", ["box = 1"])
    library.record(tmp_path, "A sitting cat figurine", "cat 60 mm", "", ["cat = 1"])
    found = library.search(tmp_path, "housing for a 6001 bearing")
    assert [e["id"] for e in found] == [i]
    assert library.show(tmp_path, i)["code"] == ["box = 1"]
    assert library.search(tmp_path, "spaceship") == []
    assert library.brief(found[0])["comment"] == "perfect" and "code" not in library.brief(found[0])


def test_nothing_is_stored_without_code_and_big_code_keeps_the_end(tmp_path):
    assert library.record(tmp_path, "x", "y", "", []) is None
    big = "a" * 30_000
    i = library.record(tmp_path, "gear", "gear", "", ["first" * 8000, big, "last"])
    assert library.show(tmp_path, i)["code"] == [big, "last"]


def test_entries_are_private_deletable_and_ids_are_safe(tmp_path):
    i = library.record(tmp_path, "gear", "gear", "", ["g"])
    path = tmp_path / library.FOLDER / f"{i}.json"
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert library.show(tmp_path, "../../secret") is None
    assert not library.delete(tmp_path, "../x")
    assert library.delete(tmp_path, i) and not path.exists()


def test_feedback_good_stores_the_code_of_this_session(tmp_path, monkeypatch):
    monkeypatch.setenv("FREECAD_BRIDGE_DIR", str(tmp_path))
    mcp_server._recent_code.clear()
    mcp_server.remember_code("box = Part.makeBox(1, 1, 1)")
    out = json.loads(anyio.run(lambda: mcp_server.record_feedback("bad", comment="no", request="a box", result="box")))
    assert "library_entry" not in out, "only what the user liked"
    def good():
        return mcp_server.record_feedback("good", comment="yes", request="a box", result="box")

    out = json.loads(anyio.run(good))
    assert out["library_entry"]
    found = json.loads(anyio.run(lambda: mcp_server.design_library(action="search", query="box")))
    assert found["designs"][0]["id"] == out["library_entry"]
    full = json.loads(anyio.run(lambda: mcp_server.design_library(action="show", id=out["library_entry"])))
    assert "makeBox" in full["code"][0]
    assert not mcp_server._recent_code, "the next design starts empty"
