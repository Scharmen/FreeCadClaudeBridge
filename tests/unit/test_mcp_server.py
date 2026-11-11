"""The MCP server: the tool set, plain JSON text, and the argument forgiveness that saves wasted turns."""

import asyncio

import pytest

from freecad_bridge import mcp_server

EXPECTED = {"status", "get_tree", "get_object", "get_changes", "get_console", "api_help", "run_python",
            "run_command", "selection", "document", "export", "import", "view", "run_headless", "image_to_3d",
            "record_feedback", "sculpt", "compare_to_picture", "design_notes", "check_design", "check_print",
            "check_fit", "flex_test", "inspect_views", "check_surface", "check_all", "make_part", "design_library",
            "sub_chat", "print_export"}
READ_ONLY = {"check_design", "check_print", "check_fit", "flex_test", "inspect_views", "check_surface", "check_all",
             "status", "get_tree", "get_object", "get_changes", "get_console", "api_help", "sub_chat"}
GONE = {"instances", "select_instance", "inspect_shape", "inspect_sketch", "snapshot", "task_dialog", "list_commands",
        "get_preferences"}


def tools():
    return {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}


def run(coro):
    return asyncio.run(coro)


def test_the_tool_set_is_the_merged_one():
    names = set(tools())
    assert names == EXPECTED
    assert not names & GONE


def test_descriptions_stay_short():
    total = sum(len(t.description or "") for t in tools().values())
    assert total <= 11000, f"{total} characters of tool descriptions; move grammar into the skill references"
    assert all(len(t.description or "") <= 1100 for t in tools().values()), \
        sorted((len(t.description or ""), t.name) for t in tools().values())[-3:]


def test_read_only_annotations():
    for name, t in tools().items():
        assert bool(t.annotations and t.annotations.read_only_hint) == (name in READ_ONLY), name


def test_no_structured_output_wrapper():
    for name, t in tools().items():
        assert getattr(t, "output_schema", None) is None, f"{name} would wrap its JSON text"


def test_response_cap_is_about_four_thousand_tokens():
    assert mcp_server.MAX_CHARS == 16000
    text = mcp_server._dump({"x": "a" * 100000})
    assert len(text) < mcp_server.MAX_CHARS + 200 and "truncated" in text


def test_select_instance_by_document(monkeypatch):
    from freecad_bridge import client

    monkeypatch.setattr(client, "_selected", None)
    infos = [{"pid": 1, "documents": [{"name": "Flange", "label": "Flange"}]},
             {"pid": 2, "documents": [{"name": "Unnamed", "label": "Housing"}]}]
    assert mcp_server._match_document(infos, "housing")["pid"] == 2
    assert mcp_server._match_document(infos, "Flange")["pid"] == 1
    with pytest.raises(mcp_server.ToolError) as e:
        mcp_server._match_document(infos, "Nope")
    assert "Flange" in str(e.value) and "Housing" in str(e.value)


# ---------- errors that used to come back empty or as a schema lecture

def test_a_check_failing_with_any_exception_says_what_happened(monkeypatch):
    from freecad_bridge.checks import runner

    async def boom(*_a, **_k):
        raise KeyError("parts")
    monkeypatch.setattr(runner, "on_copy", boom)
    with pytest.raises(mcp_server.ToolError) as e:
        run(mcp_server._on_copy(lambda p, t: "", None, None))
    assert "KeyError" in str(e.value) and "parts" in str(e.value)


def test_notes_given_as_text_become_decision_and_reason():
    out = mcp_server._notes_list(["Legs 4 mm thick because thinner ones break when printed",
                                  {"decision": "Head 1/7 of the height", "reason": "adult proportion"},
                                  "Tail curled. Why: the picture shows it"], text="Base 2 mm high")
    assert [n["decision"] for n in out] == ["Legs 4 mm thick", "Head 1/7 of the height", "Tail curled",
                                            "Base 2 mm high"]
    assert out[0]["reason"] == "thinner ones break when printed"
    assert out[2]["reason"] == "the picture shows it"
    assert out[3]["reason"], "a note without a reason still gets one, instead of an error"


def test_check_fit_takes_the_pair_as_a_list_too():
    assert mcp_server._pair("Lid", "Box", None) == ("Lid", "Box")
    assert mcp_server._pair(None, None, ["Lid", "Box"]) == ("Lid", "Box")
    with pytest.raises(mcp_server.ToolError):
        mcp_server._pair("Lid", None, None)


def test_a_view_name_as_action_sets_that_view(monkeypatch):
    sent = {}

    async def raw(op, args, **_k):
        sent.update(op=op, **args)
        return {"action": "fit"}
    monkeypatch.setattr(mcp_server, "_raw", raw)
    run(mcp_server.view(action="isometric"))
    assert sent["op"] == "view" and sent["action"] == "fit" and sent["view"] == "iso"
    assert sent["width"] == 640 and sent["height"] == 480, "smaller pictures by default"


def test_api_help_on_a_tool_name_answers_from_the_tool(monkeypatch):
    async def never(*_a, **_k):
        raise AssertionError("FreeCAD must not be asked about an MCP tool")
    monkeypatch.setattr(mcp_server, "_call", never)
    text = run(mcp_server.api_help("sculpt"))
    assert "template" in text and "mcp tool" in text


# ---------- merged tools route to the old operations

def test_get_object_levels_route_to_the_inspect_ops(monkeypatch):
    sent = []

    async def call(op, args, **_k):
        sent.append((op, args))
        return "{}"
    monkeypatch.setattr(mcp_server, "_call", call)
    run(mcp_server.get_object("Box"))
    run(mcp_server.get_object("Box", level="edges", limit=5, offset=10))
    run(mcp_server.get_object("Sketch", level="sketch"))
    assert [s[0] for s in sent] == ["get_object", "inspect_shape", "inspect_sketch"]
    assert sent[1][1]["level"] == "edges" and sent[1][1]["limit"] == 5 and sent[1][1]["offset"] == 10


def test_document_snapshots_route_to_the_snapshot_op(monkeypatch):
    sent = []

    async def call(op, args, **_k):
        sent.append((op, args))
        return "{}"
    monkeypatch.setattr(mcp_server, "_call", call)
    run(mcp_server.document("snapshot", name="Car"))
    run(mcp_server.document("snapshots", name="Car"))
    run(mcp_server.document("restore", name="Car", path="20260101-000000.FCStd"))
    run(mcp_server.document("save", name="Car"))
    assert [s[0] for s in sent] == ["snapshot", "snapshot", "snapshot", "document"]
    assert [s[1]["action"] for s in sent[:3]] == ["create", "list", "restore"]
    assert sent[2][1]["name"] == "20260101-000000.FCStd" and sent[2][1]["doc"] == "Car"


def test_run_command_covers_listing_and_the_task_dialog(monkeypatch):
    sent = []

    async def call(op, args, **_k):
        sent.append((op, args))
        return "{}"
    monkeypatch.setattr(mcp_server, "_call", call)
    run(mcp_server.run_command("Std_ViewFitAll"))
    run(mcp_server.run_command(filter="pad", workbench="PartDesignWorkbench"))
    run(mcp_server.run_command(dialog="accept"))
    assert [s[0] for s in sent] == ["run_command", "list_commands", "task_dialog"]
    assert sent[1][1]["filter"] == "pad" and sent[2][1]["action"] == "accept"


def test_status_carries_the_preferences_and_selects_an_instance(monkeypatch, tmp_path):
    from freecad_bridge import client, learning, platforms

    monkeypatch.setattr(platforms, "bridge_dir", lambda: tmp_path)
    learning.add_lesson(tmp_path, "Sizes mean the whole height including the base")
    monkeypatch.setattr(client, "_selected", None)

    async def call(op, args, **_k):
        return mcp_server._dump({"connected": True, "op": op})
    monkeypatch.setattr(mcp_server, "_call", call)
    infos = [{"pid": 1, "documents": [{"name": "Flange", "label": "Flange"}]},
             {"pid": 2, "documents": [{"name": "Unnamed", "label": "Housing"}]}]

    async def instance_infos():
        return infos
    monkeypatch.setattr(mcp_server, "_instance_infos", instance_infos)
    text = run(mcp_server.status())
    assert "whole height" in text and '"connected":true' in text
    text = run(mcp_server.status(select="housing"))
    assert client._selected == 2 and '"selected"' in text
    run(mcp_server.status(select="auto"))
    assert client._selected is None


def test_a_compare_crop_is_checked_before_any_picture_is_taken():
    assert mcp_server._crop([0.1, 0.2, 0.6, 0.9]) == [0.1, 0.2, 0.6, 0.9]
    assert mcp_server._crop(None) is None
    for bad in ([0.5, 0.5, 0.4, 0.9], [0, 0, 1], [-0.1, 0, 1, 1], ["a", 0, 1, 1]):
        with pytest.raises(mcp_server.ToolError):
            mcp_server._crop(bad)


def test_a_spec_written_loosely_is_folded_into_the_grammar_instead_of_refused():
    spec, ignored = mcp_server._fold_spec({"bbox": [50, 50, 12], "tol": 0.5, "holes": [{"d": [4, 5], "count": 4}],
                                           "name": "housing", "solids": 1, "process": "fdm", "min_wall_mm": 1.2})
    assert spec["parts"] == [{"name": "housing", "bbox": [50, 50, 12], "tol": 0.5,
                              "holes": [{"d": [4, 5], "count": 4}]}]
    assert spec["solids"] == 1 and set(ignored) == {"process", "min_wall_mm"}
    spec, ignored = mcp_server._fold_spec({"parts": ["Box", {"name": "Lid", "bbox": [60, 40, 6]}],
                                           "total": {"bbox": [1, 1, 1]}})
    assert spec["parts"][0] == {"name": "Box"} and spec["parts"][1]["bbox"] == [60, 40, 6]
    spec, _ = mcp_server._fold_spec({"parts": {"Box": {"bbox": [60, 40, 24]}, "Lid": {}}})
    assert [p["name"] for p in spec["parts"]] == ["Box", "Lid"]
    with pytest.raises(mcp_server.ToolError, match="parts"):
        mcp_server._prepare_spec("Housing 50 x 50 x 12 mm with four M4 holes")
    prepared = mcp_server._prepare_spec({"bbox": [50, 50, 12], "weird": 1})
    assert prepared["parts"][0]["bbox"] == [50, 50, 12] and prepared.get("_ignored") == ["weird"]


def test_sculpt_takes_the_scene_keys_at_the_top_level_too(monkeypatch):
    seen = {}

    def fake_scene(scene, out_dir, voxel, smooth):
        seen.update(scene)
        return {"parts": [], "seconds": 0.1, "voxel_mm": 0.2, "size_mm": [1, 1, 1]}
    from freecad_bridge import sculpt as sculpt_mod

    monkeypatch.setattr(sculpt_mod, "sculpt_scene", fake_scene)

    async def raw(op, args, **_k):
        return {"result": {"group": "G", "parts": []}}
    monkeypatch.setattr(mcp_server, "_raw", raw)
    run(mcp_server.sculpt(template="dog_standing", height=50, parts=[{"name": "Collar", "primitives": []}],
                          one_piece=True))
    assert seen["template"] == "dog_standing" and seen["height"] == 50 and seen["one_piece"] is True
    assert seen["parts"][0]["name"] == "Collar"
    with pytest.raises(mcp_server.ToolError, match="scene"):
        run(mcp_server.sculpt())


def test_api_help_takes_query_as_well(monkeypatch):
    async def never(*_a, **_k):
        raise AssertionError("not asked")
    monkeypatch.setattr(mcp_server, "_call", never)
    assert "mcp tool" in run(mcp_server.api_help(query="make_part"))
    with pytest.raises(mcp_server.ToolError, match="target"):
        run(mcp_server.api_help())


def test_export_takes_the_format_in_any_case(monkeypatch):
    seen = {}

    async def fake_call(op, args, timeout_s=60, instance=None):
        seen.update(args)
        return "{}"

    monkeypatch.setattr(mcp_server, "_call", fake_call)
    run(mcp_server.export(path="/p/x.stl", format="STL"))
    assert seen["format"] == "stl", "a wasted turn: Claude wrote STL and had to retry"
    schema = tools()["export"].input_schema["properties"]["format"]
    assert "STL" in str(schema) or "3MF" in str(schema), "the schema accepts the upper-case spelling too"
