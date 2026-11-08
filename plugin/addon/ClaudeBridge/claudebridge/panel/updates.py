"""Is a newer ClaudeBridge available, and which commands bring it here.

The addon ships inside the Claude Code plugin `freecad`, together with the MCP server and the skill, and
all three must keep one version. So an update refreshes the plugin through Claude Code (or pulls the
checkout a folder marketplace reads from) and then installs the addon from that plugin, exactly like
/freecad:setup. FreeCAD loads the new addon on its next start.

The addon makes no network requests itself (docs/PLAN.md: updates come only through the plugin
marketplace): the check refreshes the marketplace with Claude Code, or fetches the git checkout a folder
marketplace reads from, and reads the version there. No FreeCAD or Qt imports: unit-tested.
"""

from __future__ import annotations

import json
import os
from typing import Any

PLUGIN_NAME = "freecad"
CHECK_EVERY = 24 * 3600.0

Step = tuple[str, list[str]]          # (what the panel says, command)


def _parts(version: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(p) for p in version.strip().split("."))
    except (AttributeError, ValueError):
        return None


def is_newer(latest: str, current: str) -> bool:
    a, b = _parts(latest), _parts(current)
    return bool(a and b and a > b)


def manifest_version(text: str) -> str | None:
    """The version in a plugin manifest, or None for anything else."""
    try:
        version = json.loads(text).get("version")
    except (ValueError, AttributeError):
        return None
    return version if isinstance(version, str) and _parts(version) else None


def check_due(last: float | None, now: float, every: float = CHECK_EVERY) -> bool:
    return last is None or now - last >= every


def find_plugin(plugins: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The `freecad` plugin in `claude plugin list --json`."""
    return next((p for p in plugins if str(p.get("id", "")).split("@")[0] == PLUGIN_NAME), None)


def plugin_root(plugin: dict[str, Any]) -> str:
    """Where the installed plugin lives: a folder marketplace is read live, others from Claude Code's cache."""
    return str(plugin.get("readFromFolder") or plugin.get("installPath") or "")


def _market(plugin: dict[str, Any], markets: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    pid = str(plugin["id"])
    name = pid.split("@", 1)[1] if "@" in pid else ""
    return name, next((m for m in markets if m.get("name") == name), {})


def _checkout(market: dict[str, Any], git: str | None) -> str | None:
    """The git checkout a folder marketplace reads from, or None."""
    folder = str(market.get("path") or market.get("installLocation") or "")
    return folder if git and folder and os.path.isdir(os.path.join(folder, ".git")) else None


def check_steps(plugin: dict[str, Any] | None, markets: list[dict[str, Any]], claude: str,
                git: str | None) -> tuple[list[Step], str, str | None]:
    """Commands that learn the newest version: (steps, marketplace folder to read the manifest from, or ""
    when the last command prints the manifest, problem)."""
    if not plugin:
        return [], "", "The Claude Code plugin is not installed. Run /freecad:setup in Claude Code first."
    name, market = _market(plugin, markets)
    if market.get("source") == "directory":
        folder = _checkout(market, git)
        if not folder or not git:
            return [], "", (f"The plugin is read from the folder {market.get('path') or '?'}, which is no git "
                            "checkout, so there is nothing to compare with.")
        rel = os.path.relpath(plugin_root(plugin), folder).replace(os.sep, "/")
        return [("Fetching", [git, "-C", folder, "fetch", "--quiet"]),
                ("Reading", [git, "-C", folder, "show", f"@{{upstream}}:{rel}/.claude-plugin/plugin.json"])], "", None
    return ([(f"Refreshing the {name} marketplace", [claude, "plugin", "marketplace", "update", name])],
            str(market.get("installLocation") or ""), None)


def market_manifest(location: str) -> str | None:
    """The plugin manifest inside a refreshed marketplace (its marketplace.json says where the plugin is)."""
    try:
        with open(os.path.join(location, ".claude-plugin", "marketplace.json"), encoding="utf-8") as f:
            entries = json.load(f).get("plugins") or []
    except (OSError, ValueError, AttributeError):
        return None
    entry = next((p for p in entries if isinstance(p, dict) and p.get("name") == PLUGIN_NAME), None)
    source = entry.get("source") if entry else None
    if not isinstance(source, str):
        return None
    return os.path.normpath(os.path.join(location, source, ".claude-plugin", "plugin.json"))


def fetch_steps(plugin: dict[str, Any] | None, markets: list[dict[str, Any]], claude: str,
                git: str | None) -> tuple[list[Step], str | None]:
    """Commands that bring the newest plugin here, or a reason why FreeCAD cannot do it."""
    if not plugin:
        return [], "The Claude Code plugin is not installed. Run /freecad:setup in Claude Code first."
    pid = str(plugin["id"])
    market_name, market = _market(plugin, markets)
    if market.get("source") == "directory":
        folder = _checkout(market, git)
        if folder and git:
            return [(f"Pulling the latest version into {folder}", [git, "-C", folder, "pull", "--ff-only"])], None
        return [], (f"The plugin is read from the folder {market.get('path') or '?'}. Update that folder, then "
                    "press Update again to install the addon from it.")
    steps: list[Step] = [(f"Refreshing the {market_name} marketplace",
                          [claude, "plugin", "marketplace", "update", market_name]),
                         ("Updating the Claude Code plugin", [claude, "plugin", "update", pid, "--scope",
                                                              str(plugin.get("scope") or "user")])]
    return steps, None


def install_step(root: str, uvx: str) -> Step:
    return ("Installing the addon into FreeCAD",
            [uvx, "--from", os.path.join(root, "server"), "freecad-bridge", "install", "--addon-src",
             os.path.join(root, "addon", "ClaudeBridge")])


def tool_candidates(name: str) -> list[str]:
    """Where uv and git usually live; FreeCAD started from the Dock does not get the shell's PATH."""
    home = os.path.expanduser("~")
    exe = name + (".exe" if os.name == "nt" else "")
    return [os.path.join(home, ".local", "bin", exe), os.path.join(home, ".cargo", "bin", exe),
            f"/opt/homebrew/bin/{exe}", f"/usr/local/bin/{exe}", f"/usr/bin/{exe}"]


def find_tool(name: str, path: str = "") -> str | None:
    import shutil

    found = shutil.which(name, path=path or None)
    if found:
        return found
    return next((c for c in tool_candidates(name) if os.path.isfile(c) and os.access(c, os.X_OK)), None)
