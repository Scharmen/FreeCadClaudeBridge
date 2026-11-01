"""The MCP server exposes the 19 planned tools and returns plain JSON text (no structured wrapper)."""

import asyncio

from freecad_bridge import mcp_server

EXPECTED = {"status", "get_tree", "get_object", "inspect_shape", "inspect_sketch", "get_changes", "get_console",
            "api_help", "list_commands", "run_python", "run_command", "task_dialog", "selection", "document",
            "export", "import", "view", "run_headless", "snapshot"}
READ_ONLY = {"status", "get_tree", "get_object", "inspect_shape", "inspect_sketch", "get_changes", "get_console",
             "api_help", "list_commands"}


def tools():
    return {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}


def test_nineteen_tools():
    assert set(tools()) == EXPECTED


def test_read_only_annotations():
    for name, t in tools().items():
        assert bool(t.annotations and t.annotations.read_only_hint) == (name in READ_ONLY), name


def test_no_structured_output_wrapper():
    for name, t in tools().items():
        assert getattr(t, "output_schema", None) is None, f"{name} would wrap its JSON text"


def test_response_cap():
    text = mcp_server._dump({"x": "a" * 100000})
    assert len(text) < mcp_server.MAX_CHARS + 200 and "truncated" in text
