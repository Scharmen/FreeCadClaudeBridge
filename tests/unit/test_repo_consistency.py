"""CI checks from the implementation plan: identical protocol copies, matching versions and names."""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "plugin/addon/ClaudeBridge"
SERVER = ROOT / "plugin/server"


def test_shared_copies_identical():
    for name in ("protocol.py", "settings.py"):
        a = (ADDON / "claudebridge/bridge" / name).read_bytes()
        b = (SERVER / "src/freecad_bridge" / name).read_bytes()
        assert a == b, f"{name} differs between addon and server"


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


def test_no_network_access_and_loopback_only():
    """Security checklist: no telemetry or update checks, TCP only on 127.0.0.1."""
    code = [*ADDON.rglob("*.py"), *(SERVER / "src").rglob("*.py")]
    for py in code:
        text = py.read_text()
        assert not re.search(r"^\s*(?:import|from)\s+(?:urllib|http|requests|httpx|aiohttp)\b", text, re.M), py
        for m in re.finditer(r"\.bind\((.+?)\)\n", text):
            assert "127.0.0.1" in m[1] or "path" in m[1], f"{py}: bind({m[1]})"
