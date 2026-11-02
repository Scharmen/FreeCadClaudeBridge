"""Chat panel conversations: several per document, Claude Code session files, migration."""

import json
import os
import time

from claudebridge.panel import conversations as conv


def session_file(projects, cwd, sid, prompts, entrypoint="sdk-cli", answer="Done."):
    d = projects / conv.encode_project(cwd)
    d.mkdir(parents=True, exist_ok=True)
    lines = []
    for p in prompts:
        lines.append({"type": "user", "sessionId": sid, "cwd": cwd, "entrypoint": entrypoint,
                      "timestamp": "2026-11-01T10:00:00Z", "message": {"role": "user", "content": p}})
        lines.append({"type": "assistant", "sessionId": sid, "cwd": cwd,
                      "message": {"role": "assistant", "content": [{"type": "text", "text": answer},
                                                                    {"type": "tool_use", "name": "mcp__x__run_python",
                                                                     "input": {}}]}})
    path = d / f"{sid}.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return path


def test_project_folder_name_matches_claude_code():
    assert conv.encode_project("/Users/kevin/StudioProjects/FreeCadClaudeBridge") == \
        "-Users-kevin-StudioProjects-FreeCadClaudeBridge"
    assert conv.encode_project("/Users/k/Library/Application Support/x.y") == "-Users-k-Library-Application-Support-x-y"


def test_config_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert conv.projects_dir() == os.path.join(str(tmp_path / "cfg"), "projects")


def test_store_several_conversations_per_document(tmp_path):
    store = conv.Store(tmp_path)
    store.add("doc:/a.FCStd", "s1", "Flange with bolt circle", "/a")
    store.add("doc:/a.FCStd", "s2", "Housing", "/a")
    assert store.active("doc:/a.FCStd")["id"] == "s2"
    store.set_active("doc:/a.FCStd", "s1")
    store.rename("doc:/a.FCStd", "s1", "Flange v2")
    again = conv.Store(tmp_path)                                   # persisted
    assert [c["title"] for c in again.items("doc:/a.FCStd")] == ["Flange v2", "Housing"]
    assert again.active("doc:/a.FCStd")["id"] == "s1"
    again.remove("doc:/a.FCStd", "s1")
    assert again.active("doc:/a.FCStd")["id"] == "s2"
    again.start_new("doc:/a.FCStd")
    assert again.active("doc:/a.FCStd") is None, "a new conversation has no session until the first answer"


def test_replace_keeps_title_when_claude_forks(tmp_path):
    store = conv.Store(tmp_path)
    store.add("d", "s1", "Bracket", "/w")
    store.replace("d", "s1", "s1b")
    assert store.active("d")["id"] == "s1b" and store.active("d")["title"] == "Bracket"


def test_migrates_old_single_session_map(tmp_path):
    (tmp_path / "sessions.json").write_text(json.dumps({"unsaved:PanelA": "abc"}), encoding="utf-8")
    store = conv.Store(tmp_path)
    assert store.active("unsaved:PanelA")["id"] == "abc"


def test_session_info_and_transcript(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    path = session_file(tmp_path / "projects", "/w", "sid1",
                        ["[FreeCAD] Active document: A (A). Selection: nothing.\n\nMake a 40 mm cube", "Now fillet it"])
    info = conv.session_info(str(path))
    assert info["id"] == "sid1" and info["title"] == "Make a 40 mm cube" and info["turns"] == 2
    assert info["source"] == "FreeCAD panel"
    t = conv.transcript(str(path))
    assert t[0] == ("user", "Make a 40 mm cube") and t[1][0] == "assistant" and "Done." in t[1][1]
    assert any("run_python" in text for role, text in t if role == "tool")


def test_folder_sessions_exclude_linked_and_newest_first(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    old = session_file(tmp_path / "projects", "/w", "old", ["first"], entrypoint="cli")
    time.sleep(0.02)
    session_file(tmp_path / "projects", "/w", "new", ["second"], entrypoint="claude-desktop")
    session_file(tmp_path / "projects", "/w", "mine", ["third"])
    os.utime(old, (time.time() - 100, time.time() - 100))
    found = conv.folder_sessions("/w", exclude={"mine"})
    assert [s["id"] for s in found] == ["new", "old"]
    assert found[1]["source"] == "terminal"


def test_delete_session_removes_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    sid = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
    path = session_file(tmp_path / "projects", "/w", sid, ["x"])
    assert conv.delete_session_file("/w", sid)
    assert not path.exists()
    assert not conv.delete_session_file("/w", "../../etc"), "ids are never paths"
