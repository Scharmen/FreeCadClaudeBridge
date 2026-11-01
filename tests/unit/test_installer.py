import json
import os
import stat
import sys
from pathlib import Path

import pytest

from freecad_bridge import __version__, installer, platforms

ROOT = Path(__file__).resolve().parents[2]
ADDON_SRC = ROOT / "plugin/addon/ClaudeBridge"

BANNER_OUTPUT = """\
FCB_VERSION=1.1.4
FCB_USERAPPDATA=/Users/k/Library/Application Support/FreeCAD/v1-1/
FCB_HOME=/Applications/FreeCAD.app/Contents/Resources/
FreeCAD 1.1.4, Libs: 1.1.4R20260928 (Git shallow)
(C) 2001-2026 FreeCAD contributors
FreeCAD is free and open-source software licensed under the terms of LGPL2+ license.
"""


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("FREECAD_BRIDGE_DIR", str(tmp_path / "bridge"))
    return tmp_path


def test_parse_probe_ignores_banner():
    data = installer.parse_probe(BANNER_OUTPUT)
    assert data == {"version": "1.1.4", "userappdata": "/Users/k/Library/Application Support/FreeCAD/v1-1/",
                    "home": "/Applications/FreeCAD.app/Contents/Resources/"}


posix_only = pytest.mark.skipif(sys.platform == "win32", reason="fake freecadcmd is a shell script")


def fake_app(tmp_path: Path, name: str, version: str) -> Path:
    """A FreeCAD.app whose freecadcmd is a shell script printing probe markers."""
    app = tmp_path / "Applications" / name
    bin_dir = app / "Contents/Resources/bin"
    bin_dir.mkdir(parents=True)
    user = tmp_path / f"user-{version}"
    cmd = bin_dir / "freecadcmd"
    cmd.write_text(f"#!/bin/sh\necho FCB_VERSION={version}\necho FCB_USERAPPDATA={user}/\necho banner\n")
    cmd.chmod(cmd.stat().st_mode | stat.S_IEXEC)
    return app


@posix_only
def test_freecadcmd_for_app_bundle(tmp_path):
    app = fake_app(tmp_path, "FreeCAD.app", "1.1.4")
    assert installer.freecadcmd_for(app) == app / "Contents/Resources/bin/freecadcmd"
    assert installer.freecadcmd_for(tmp_path / "nothing") is None


@posix_only
def test_discover_prefers_newest(tmp_path, monkeypatch):
    old = fake_app(tmp_path, "FreeCAD 1.0.app", "1.0.2")
    new = fake_app(tmp_path, "FreeCAD.app", "1.1.4")
    monkeypatch.setattr(installer, "candidate_apps", lambda: [old, new])
    found = installer.discover()
    assert [i.version for i in found] == ["1.1.4", "1.0.2"]
    assert found[0].app == new


@posix_only
def test_install_copies_addon_and_writes_state(tmp_path, bridge, monkeypatch):
    app = fake_app(tmp_path, "FreeCAD.app", "1.1.4")
    monkeypatch.setattr(installer, "candidate_apps", lambda: [app])
    monkeypatch.setattr(installer, "find_claude", lambda: "/Users/k/.local/bin/claude")
    lines = []
    assert installer.install(str(ADDON_SRC), out=lines.append) == 0
    dest = tmp_path / "user-1.1.4/Mod/ClaudeBridge"
    assert (dest / "InitGui.py").is_file()
    assert (dest / "claudebridge/bridge/protocol.py").is_file()
    assert not list(dest.rglob("__pycache__"))
    assert installer.installed_version(dest.parent) == __version__
    token = platforms.token_path()
    assert len(token.read_bytes()) == 32
    assert oct(os.stat(token).st_mode & 0o777) == "0o600"
    cfg = platforms.load_config()
    assert cfg["claude_path"] == "/Users/k/.local/bin/claude"
    assert cfg["plugin_root"] == str(ROOT / "plugin")
    assert cfg["freecad_version"] == "1.1.4"


@posix_only
def test_reinstall_backs_up_outside_mod(tmp_path, bridge, monkeypatch):
    app = fake_app(tmp_path, "FreeCAD.app", "1.1.4")
    monkeypatch.setattr(installer, "candidate_apps", lambda: [app])
    monkeypatch.setattr(installer, "find_claude", lambda: None)
    installer.install(str(ADDON_SRC), out=lambda _: None)
    installer.install(str(ADDON_SRC), out=lambda _: None)
    mod = tmp_path / "user-1.1.4/Mod"
    assert [p.name for p in mod.iterdir()] == ["ClaudeBridge"], "backup must not land in Mod (FreeCAD would load it)"
    assert len(list((platforms.bridge_dir() / "backups").iterdir())) == 1


def test_install_without_freecad_fails(bridge, monkeypatch):
    monkeypatch.setattr(installer, "candidate_apps", lambda: [])
    lines = []
    assert installer.install(str(ADDON_SRC), out=lines.append) == 1
    assert "no FreeCAD installation" in lines[0]


def test_install_bad_addon_src(bridge):
    lines = []
    assert installer.install("/nonexistent", out=lines.append) == 2


@posix_only
def test_uninstall_removes_everything(tmp_path, bridge, monkeypatch):
    app = fake_app(tmp_path, "FreeCAD.app", "1.1.4")
    monkeypatch.setattr(installer, "candidate_apps", lambda: [app])
    monkeypatch.setattr(installer, "find_claude", lambda: None)
    installer.install(str(ADDON_SRC), out=lambda _: None)
    (platforms.instances_dir() / "123.json").write_text(json.dumps({"pid": 123}))
    installer.uninstall(out=lambda _: None)
    assert not (tmp_path / "user-1.1.4/Mod/ClaudeBridge").exists()
    assert not platforms.token_path().exists()
    assert not platforms.config_path().exists()
    assert not platforms.instances_dir().exists()
