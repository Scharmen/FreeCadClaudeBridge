"""CI checks from docs/PLAN.md: identical protocol copies, matching versions and names."""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "plugin/addon/ClaudeBridge"
SERVER = ROOT / "plugin/server"


def test_protocol_copies_identical():
    a = (ADDON / "claudebridge/bridge/protocol.py").read_bytes()
    b = (SERVER / "src/freecad_bridge/protocol.py").read_bytes()
    assert a == b, "protocol.py differs between addon and server"


def _versions():
    plugin = json.loads((ROOT / "plugin/.claude-plugin/plugin.json").read_text())["version"]
    pyproject = tomllib.loads((SERVER / "pyproject.toml").read_text())["project"]["version"]
    server_init = re.search(r'__version__ = "(.+?)"', (SERVER / "src/freecad_bridge/__init__.py").read_text())[1]
    addon_init = re.search(r'__version__ = "(.+?)"', (ADDON / "claudebridge/__init__.py").read_text())[1]
    init_py = re.search(r'CLAUDE_BRIDGE_VERSION = "(.+?)"', (ADDON / "Init.py").read_text())[1]
    package_xml = re.search(r"<version>(.+?)</version>", (ROOT / "package.xml").read_text())[1]
    return {"plugin.json": plugin, "pyproject": pyproject, "server": server_init,
            "addon": addon_init, "Init.py": init_py, "package.xml": package_xml}


def test_versions_match():
    versions = _versions()
    assert len(set(versions.values())) == 1, versions


def test_plugin_names_match():
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    plugin = json.loads((ROOT / "plugin/.claude-plugin/plugin.json").read_text())
    assert market["name"] == "freecad-claude"
    assert [p["name"] for p in market["plugins"]] == [plugin["name"]] == ["freecad"]
    assert market["plugins"][0]["source"] == "./plugin"


def test_mcp_server_named_freecad():
    cfg = json.loads((ROOT / "plugin/.mcp.json").read_text())
    assert list(cfg["mcpServers"]) == ["freecad"]


def test_addon_has_no_third_party_imports():
    allowed = {"FreeCAD", "FreeCADGui", "PySide", "Part", "Sketcher", "Import", "Mesh", "claudebridge"}
    import sys

    stdlib = set(sys.stdlib_module_names)
    for py in ADDON.rglob("*.py"):
        for m in re.finditer(r"^\s*(?:from|import)\s+([A-Za-z_][\w]*)", py.read_text(), re.M):
            mod = m[1]
            assert mod in stdlib or mod in allowed or mod == "__future__", f"{py}: imports {mod}"
