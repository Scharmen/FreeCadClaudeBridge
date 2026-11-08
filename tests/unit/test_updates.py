"""Update check against GitHub and the update steps run from the chat panel."""

import json
import os

from claudebridge.panel import updates


def test_versions_compare_as_numbers():
    assert updates.is_newer("1.22.0", "1.21.1")
    assert updates.is_newer("1.21.10", "1.21.9"), "not as text"
    assert not updates.is_newer("1.21.1", "1.21.1")
    assert not updates.is_newer("1.20.5", "1.21.0")
    assert not updates.is_newer("garbage", "1.21.0")


def test_latest_version_comes_from_the_plugin_manifest():
    assert updates.manifest_version(json.dumps({"name": "freecad", "version": "1.22.0"})) == "1.22.0"
    assert updates.manifest_version("<html>rate limited</html>") is None
    assert updates.manifest_version(json.dumps({"name": "freecad"})) is None


def test_the_check_runs_at_most_once_a_day():
    day = 24 * 3600
    assert updates.check_due(last=None, now=1000)
    assert not updates.check_due(last=1000, now=1000 + day - 1)
    assert updates.check_due(last=1000, now=1000 + day)


PLUGINS = [{"id": "other@x", "installPath": "/o"},
           {"id": "freecad@freecad-claude", "scope": "user", "installPath": "/cache/freecad/1.21.1"}]


def test_a_github_marketplace_is_refreshed_and_the_plugin_updated():
    plugin = updates.find_plugin(PLUGINS)
    markets = [{"name": "freecad-claude", "source": "github", "repo": "Scharmen/FreeCadClaudeBridge"}]
    steps, problem = updates.fetch_steps(plugin, markets, claude="/bin/claude", git="/usr/bin/git")
    assert problem is None
    assert [s[1] for s in steps] == [["/bin/claude", "plugin", "marketplace", "update", "freecad-claude"],
                                     ["/bin/claude", "plugin", "update", "freecad@freecad-claude", "--scope", "user"]]


def test_a_folder_marketplace_that_is_a_git_checkout_is_pulled(tmp_path):
    (tmp_path / ".git").mkdir()
    plugin = {"id": "freecad@freecad-claude", "readFromFolder": str(tmp_path / "plugin")}
    markets = [{"name": "freecad-claude", "source": "directory", "path": str(tmp_path)}]
    steps, problem = updates.fetch_steps(plugin, markets, claude="/bin/claude", git="/usr/bin/git")
    assert problem is None and steps == [("Pulling the latest version into " + str(tmp_path),
                                          ["/usr/bin/git", "-C", str(tmp_path), "pull", "--ff-only"])]


def test_a_plain_folder_cannot_be_updated_from_freecad(tmp_path):
    plugin = {"id": "freecad@freecad-claude", "readFromFolder": str(tmp_path / "plugin")}
    markets = [{"name": "freecad-claude", "source": "directory", "path": str(tmp_path)}]
    steps, problem = updates.fetch_steps(plugin, markets, claude="/bin/claude", git="/usr/bin/git")
    assert steps == [] and problem and str(tmp_path) in problem


def test_no_plugin_means_setup_first():
    steps, problem = updates.fetch_steps(updates.find_plugin([]), [], claude="/bin/claude", git=None)
    assert steps == [] and "/freecad:setup" in problem


def test_the_addon_is_installed_from_the_updated_plugin():
    folder = {"id": "freecad@m", "readFromFolder": "/repo/plugin", "installPath": "/cache/1.7.0"}
    assert updates.plugin_root(folder) == "/repo/plugin", "a folder marketplace is read live"
    assert updates.plugin_root({"id": "freecad@m", "installPath": "/cache/1.22.0"}) == "/cache/1.22.0"
    label, cmd = updates.install_step("/cache/1.22.0", uvx="/u/uvx")
    root = "/cache/1.22.0"
    assert cmd == ["/u/uvx", "--from", os.path.join(root, "server"), "freecad-bridge", "install", "--addon-src",
                   os.path.join(root, "addon", "ClaudeBridge")]


def test_the_check_for_a_github_marketplace_goes_through_claude_code(tmp_path):
    loc = tmp_path / "market"
    plugin = {"id": "freecad@freecad-claude", "installPath": "/cache/1.21.1"}
    markets = [{"name": "freecad-claude", "source": "github", "installLocation": str(loc)}]
    steps, location, problem = updates.check_steps(plugin, markets, claude="/bin/claude", git="/usr/bin/git")
    assert problem is None and location == str(loc)
    assert [s[1] for s in steps] == [["/bin/claude", "plugin", "marketplace", "update", "freecad-claude"]]
    (loc / ".claude-plugin").mkdir(parents=True)
    (loc / ".claude-plugin" / "marketplace.json").write_text(json.dumps(
        {"plugins": [{"name": "other", "source": "./x"}, {"name": "freecad", "source": "./plugin"}]}))
    assert updates.market_manifest(str(loc)) == str(loc / "plugin" / ".claude-plugin" / "plugin.json")


def test_the_check_for_a_folder_checkout_fetches_and_reads_the_upstream_manifest(tmp_path):
    (tmp_path / ".git").mkdir()
    plugin = {"id": "freecad@freecad-claude", "readFromFolder": str(tmp_path / "plugin")}
    markets = [{"name": "freecad-claude", "source": "directory", "path": str(tmp_path)}]
    steps, location, problem = updates.check_steps(plugin, markets, claude="/bin/claude", git="/usr/bin/git")
    assert problem is None and location == "", "the manifest is the last command's output"
    assert [s[1] for s in steps] == [["/usr/bin/git", "-C", str(tmp_path), "fetch", "--quiet"],
                                     ["/usr/bin/git", "-C", str(tmp_path), "show",
                                      "@{upstream}:plugin/.claude-plugin/plugin.json"]]


def test_the_addon_itself_makes_no_network_requests():
    import inspect

    source = inspect.getsource(updates)
    assert "http" not in source.replace("github", ""), "checks go through Claude Code or git, not the addon"
