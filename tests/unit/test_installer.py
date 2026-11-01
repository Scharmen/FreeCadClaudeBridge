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


@pytest.fixture(autouse=True)
def no_keychain(monkeypatch):
    """Never touch the real keychain from tests."""
    monkeypatch.setattr(installer, "clear_api_key", lambda: [])


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


def _installed(tmp_path, monkeypatch, old_version="0.0.1"):
    app = fake_app(tmp_path, "FreeCAD.app", "1.1.4")
    monkeypatch.setattr(installer, "candidate_apps", lambda: [app])
    monkeypatch.setattr(installer, "find_claude", lambda: None)
    installer.install(str(ADDON_SRC), out=lambda _: None)
    mod = tmp_path / "user-1.1.4/Mod"
    marker = mod / "ClaudeBridge" / installer.INSTALLED_MARKER
    marker.write_text(json.dumps({"version": old_version}))
    return mod


@posix_only
def test_auto_update_replaces_older_addon(tmp_path, bridge, monkeypatch):
    mod = _installed(tmp_path, monkeypatch)
    platforms.update_config(plugin_root="/old/plugin/0.0.1", server_version="0.0.1")
    lines = []
    assert installer.auto_update(str(ADDON_SRC), out=lines.append)
    assert installer.installed_version(mod) == __version__
    assert "0.0.1 -> " + __version__ in lines[0]
    cfg = platforms.load_config()
    assert cfg["plugin_root"] == str(ROOT / "plugin") and cfg["server_version"] == __version__
    assert [p.name for p in mod.iterdir()] == ["ClaudeBridge"]
    assert not installer.auto_update(str(ADDON_SRC), out=lines.append), "second run is a no-op"


@posix_only
def test_auto_update_never_downgrades(tmp_path, bridge, monkeypatch):
    mod = _installed(tmp_path, monkeypatch, old_version="99.0.0")
    assert not installer.auto_update(str(ADDON_SRC), out=lambda _: None)
    assert installer.installed_version(mod) == "99.0.0"


def test_auto_update_needs_setup(bridge):
    assert not installer.auto_update(str(ADDON_SRC), out=lambda _: None)
    assert not platforms.config_path().exists()


@posix_only
def test_backups_are_pruned(tmp_path, bridge, monkeypatch):
    app = fake_app(tmp_path, "FreeCAD.app", "1.1.4")
    monkeypatch.setattr(installer, "candidate_apps", lambda: [app])
    monkeypatch.setattr(installer, "find_claude", lambda: None)
    for i in range(installer.MAX_BACKUPS + 3):
        installer.install(str(ADDON_SRC), out=lambda _: None)
        marker = tmp_path / "user-1.1.4/Mod/ClaudeBridge" / installer.INSTALLED_MARKER
        marker.write_text(json.dumps({"version": f"0.{i}.0"}))  # distinct backup names
    assert len(list((platforms.bridge_dir() / "backups").iterdir())) == installer.MAX_BACKUPS


@posix_only
def test_uninstall_keeps_user_data_unless_purged(tmp_path, bridge, monkeypatch):
    _installed(tmp_path, monkeypatch)
    bd = platforms.bridge_dir()
    (bd / "snapshots/Doc").mkdir(parents=True)
    platforms.audit_record("call", op="run_python")
    sock = tmp_path / "stale.sock"
    sock.write_text("")
    (platforms.instances_dir() / "999999.json").write_text(json.dumps(
        {"pid": 999999, "transport": "unix", "address": str(sock)}))
    lines = []
    installer.uninstall(out=lines.append)
    assert not sock.exists()
    assert (bd / "snapshots").exists() and platforms.audit_path().exists()
    assert any("--purge" in line for line in lines)
    installer.uninstall(out=lines.append, purge=True)
    assert not bd.exists()


def test_audit_record_is_private_and_rotates(bridge, monkeypatch):
    monkeypatch.setattr(platforms, "AUDIT_MAX_BYTES", 300)
    for i in range(5):
        platforms.audit_record("run_headless", script="x" * 100, n=i)
    log = platforms.audit_path()
    assert log.with_name("bridge-log.1.jsonl").exists()
    assert all(json.loads(line)["event"] == "run_headless" for line in log.read_text().splitlines())
    if sys.platform != "win32":
        assert oct(os.stat(log).st_mode & 0o777) == "0o600"


def test_addon_audit_clips_and_drops_internal_args(bridge):
    from claudebridge.bridge import audit

    args = audit.call_args({"code": "y" * (audit.MAX_FIELD + 10), "_timeout": 5, "doc": "D"})
    assert "_timeout" not in args and args["doc"] == "D" and len(args["code"]) < audit.MAX_FIELD + 50
    audit.record("call", op="run_python", args=args, ok=True)
    entry = json.loads(Path(audit.log_path()).read_text())
    assert entry["op"] == "run_python" and entry["ok"] is True
