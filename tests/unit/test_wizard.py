"""One-step install wizard: prerequisite checks, links when something is missing, install steps."""

import json
from pathlib import Path

from freecad_bridge import wizard
from freecad_bridge.installer import FreeCADInstall

FREECAD = FreeCADInstall(["/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd"], "1.1.4",
                         Path("/u/FreeCAD/v1-1"), Path("/Applications/FreeCAD.app"))


class FakeSystem(wizard.System):
    """Records commands; answers like a machine with the given state."""

    def __init__(self, platform="darwin", desktop=True, cli=True, logged_in=True, answers=None,
                 plugin_installed=False, marketplace=False):
        self.platform = platform
        self.desktop, self.cli, self.logged_in = desktop, cli, logged_in
        self.plugin_installed, self.marketplace = plugin_installed, marketplace
        self.answers = list(answers or [])
        self.commands, self.opened, self.lines = [], [], []

    def say(self, text=""):
        self.lines.append(text)

    def ask(self, question, default=True):
        self.lines.append("? " + question)
        return self.answers.pop(0) if self.answers else default

    def open_url(self, url):
        self.opened.append(url)

    def claude_desktop(self):
        return "/Applications/Claude.app" if self.desktop else None

    def claude_cli(self):
        return "/u/.local/bin/claude" if self.cli else None

    def run(self, args, interactive=False, timeout=600):
        self.commands.append(list(args))
        tail = args[1:]
        if args[0].endswith("sh") or args[0] == "powershell":           # official Claude Code installer
            self.cli = True
            return 0, ""
        if tail[:2] == ["auth", "status"]:
            return 0, json.dumps({"loggedIn": self.logged_in, "authMethod": "claude.ai", "subscriptionType": "pro"})
        if tail[:2] == ["auth", "login"]:
            self.logged_in = True
            return 0, ""
        if tail[:3] == ["plugin", "marketplace", "list"]:
            return 0, json.dumps([{"name": "freecad-claude"}] if self.marketplace else [])
        if tail[:3] == ["plugin", "marketplace", "add"]:
            self.marketplace = True
            return 0, ""
        if tail[:2] == ["plugin", "list"]:
            entry = {"id": wizard.PLUGIN, "version": "1.6.0", "installPath": "/u/.claude/plugins/cache/freecad"}
            return 0, json.dumps([entry] if self.plugin_installed else [])
        if tail[:2] in (["plugin", "install"], ["plugin", "update"]):
            self.plugin_installed = True
            return 0, ""
        return 0, ""

    def text(self):
        return "\n".join(self.lines)


def run(system, installs=(FREECAD,), **kw):
    calls = {}

    def install_addon(src, freecad, out):
        calls["addon"] = (src, freecad)
        out("Installed ClaudeBridge")
        return 0

    rc = wizard.run_wizard(system, discover=lambda explicit=None: list(installs), install_addon=install_addon,
                           start_freecad=lambda inst: calls.setdefault("started", inst.version),
                           freecad_running=lambda: False, **kw)
    return rc, calls


def test_everything_present_installs_plugin_and_addon():
    s = FakeSystem(answers=[True, True])
    rc, calls = run(s)
    assert rc == 0
    cmds = [" ".join(c[1:]) for c in s.commands]
    assert f"plugin marketplace add {wizard.MARKETPLACE}" in cmds
    assert f"plugin install {wizard.PLUGIN}" in cmds
    assert calls["addon"][0] == "/u/.claude/plugins/cache/freecad/addon/ClaudeBridge"
    assert "FreeCAD 1.1.4" in s.text() and "Claude Desktop" in s.text() and "logged in" in s.text().lower()


def test_missing_freecad_points_to_the_download_page():
    s = FakeSystem(answers=[True])
    rc, calls = run(s, installs=())
    assert rc == 1 and "addon" not in calls
    assert wizard.LINKS["freecad"] in s.text() and s.opened == [wizard.LINKS["freecad"]]
    assert not any(c[1:3] == ["plugin", "install"] for c in s.commands), "nothing installed while blocked"


def test_old_freecad_is_rejected():
    old = FreeCADInstall(["/x/freecadcmd"], "0.21.2", Path("/u/v0-21"))
    s = FakeSystem(answers=[False])
    rc, _ = run(s, installs=(old,))
    assert rc == 1 and "0.21.2" in s.text() and wizard.LINKS["freecad"] in s.text()


def test_missing_claude_desktop_points_to_download():
    s = FakeSystem(desktop=False, answers=[False])
    rc, _ = run(s)
    assert rc == 1 and wizard.LINKS["desktop"] in s.text()


def test_linux_needs_no_desktop_app():
    s = FakeSystem(platform="linux", desktop=False, answers=[True, False])
    rc, _ = run(s)
    assert rc == 0 and "not available for Linux" in s.text()


def test_missing_cli_is_installed_with_consent():
    s = FakeSystem(cli=False, answers=[True, True, False])
    rc, _ = run(s)
    assert rc == 0
    assert any("claude.ai/install" in " ".join(c) for c in s.commands)


def test_missing_cli_declined_shows_link():
    s = FakeSystem(cli=False, answers=[False, False])
    rc, _ = run(s)
    assert rc == 1 and wizard.LINKS["claude_code"] in s.text()


def test_login_is_offered_and_rechecked():
    s = FakeSystem(logged_in=False, answers=[True, True, False])
    rc, _ = run(s)
    assert rc == 0
    assert any(c[1:3] == ["auth", "login"] for c in s.commands)


def test_existing_plugin_is_updated_not_reinstalled():
    s = FakeSystem(plugin_installed=True, marketplace=True, answers=[True, False])
    rc, _ = run(s)
    cmds = [c[1:] for c in s.commands]
    assert rc == 0 and ["plugin", "update", wizard.PLUGIN] in cmds
    assert ["plugin", "marketplace", "add", wizard.MARKETPLACE] not in cmds
    assert ["plugin", "install", wizard.PLUGIN] not in cmds


def test_yes_mode_never_asks():
    s = FakeSystem(logged_in=False)
    rc, calls = run(s, yes=True)
    assert rc == 0 and not [ln for ln in s.lines if ln.startswith("? ")]
    assert "started" not in calls, "--yes does not start FreeCAD on its own"


def test_desktop_detection_paths(tmp_path, monkeypatch):
    app = tmp_path / "Applications" / "Claude.app"
    app.mkdir(parents=True)
    assert wizard.find_claude_desktop("darwin", {}, [tmp_path / "Applications"]) == str(app)
    win = tmp_path / "AnthropicClaude"
    win.mkdir()
    (win / "claude.exe").write_text("")
    assert wizard.find_claude_desktop("win32", {"LOCALAPPDATA": str(tmp_path)}, []) == str(win / "claude.exe")
    assert wizard.find_claude_desktop("linux", {}, []) is None


def test_addon_source_prefers_a_local_plugin_folder(tmp_path):
    (tmp_path / "plugin/addon/ClaudeBridge").mkdir(parents=True)
    entry = {"installPath": "/stale/cache/0.1.0", "readFromFolder": str(tmp_path / "plugin")}
    assert wizard.addon_source(entry) == tmp_path / "plugin/addon/ClaudeBridge"
    assert wizard.addon_source({"installPath": "/c/1.6.0"}) == Path("/c/1.6.0/addon/ClaudeBridge")
