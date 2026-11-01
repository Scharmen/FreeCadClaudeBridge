"""Platform detection and secrets without the real platforms (fake trees, monkeypatched OS)."""

import os
import stat
import sys
from pathlib import Path

import pytest

from freecad_bridge import installer


def touch_exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def test_command_for_appimage_and_extracted(tmp_path):
    img = touch_exe(tmp_path / "Käse Müller" / "FreeCAD_1.1.4-Linux-x86_64-py311.AppImage")
    assert installer.command_for(img) == [str(img), "freecadcmd"]
    apprun = touch_exe(tmp_path / "squashfs-root" / "AppRun")
    assert installer.command_for(apprun.parent) == [str(apprun), "freecadcmd"]
    assert installer.freecadcmd_for(img) is None


def test_command_for_windows_layout(tmp_path):
    exe = tmp_path / "Program Files" / "FreeCAD 1.1" / "bin" / "FreeCADCmd.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    cmd = installer.command_for(exe.parent.parent)
    assert cmd and Path(cmd[0]).resolve() == exe.resolve() or str(cmd[0]).lower() == str(exe).lower()


def test_linux_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(installer.shutil, "which", lambda name: None)
    img = touch_exe(tmp_path / "Applications" / "FreeCAD_1.1.4-Linux-x86_64-py311.AppImage")
    dl = touch_exe(tmp_path / "Downloads" / "FreeCAD-1.0.2.AppImage")
    found = installer.candidate_apps()
    assert img in found and dl in found


def test_windows_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(installer, "_windows_registry_locations", lambda: [tmp_path / "Reg" / "FreeCAD"])
    pf = tmp_path / "Program Files"
    (pf / "FreeCAD 1.1" / "bin").mkdir(parents=True)
    monkeypatch.setenv("ProgramFiles", str(pf))
    for var in ("ProgramW6432", "LOCALAPPDATA"):
        monkeypatch.delenv(var, raising=False)
    found = installer.candidate_apps()
    assert tmp_path / "Reg" / "FreeCAD" in found and pf / "FreeCAD 1.1" in found


@pytest.mark.skipif(sys.platform == "win32", reason="uses a shell script as fake freecadcmd")
def test_install_into_user_dir_with_spaces_and_umlauts(tmp_path, monkeypatch):
    monkeypatch.setenv("FREECAD_BRIDGE_DIR", str(tmp_path / "bridge dir"))
    user = tmp_path / "Käse Müller" / "Application Support" / "FreeCAD" / "v1-1"
    cmd = touch_exe(tmp_path / "Free CAD.app" / "Contents/Resources/bin/freecadcmd")
    cmd.write_text(f"#!/bin/sh\necho FCB_VERSION=1.1.4\necho 'FCB_USERAPPDATA={user}/'\n")
    monkeypatch.setattr(installer, "candidate_apps", lambda: [tmp_path / "Free CAD.app"])
    monkeypatch.setattr(installer, "find_claude", lambda: None)
    root = Path(__file__).resolve().parents[2]
    assert installer.install(str(root / "plugin/addon/ClaudeBridge"), out=lambda _: None) == 0
    assert (user / "Mod" / "ClaudeBridge" / "InitGui.py").is_file()


def test_secrets_file_backend(tmp_path, monkeypatch):
    from claudebridge.panel import secrets

    monkeypatch.setenv("FREECAD_BRIDGE_DIR", str(tmp_path))
    monkeypatch.setattr(secrets, "backend", lambda: "file")
    assert secrets.get_api_key() is None
    secrets.set_api_key("  sk-test-123  ")
    assert secrets.get_api_key() == "sk-test-123"
    if os.name != "nt":
        assert oct(os.stat(tmp_path / "api_key").st_mode & 0o777) == "0o600"
    secrets.clear_api_key()
    assert secrets.get_api_key() is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager")
def test_secrets_windows_credential_manager(monkeypatch):
    from claudebridge.panel import secrets

    monkeypatch.setattr(secrets, "SERVICE", "freecad-bridge-ci-test")
    secrets.set_api_key("sk-ci-ÄÖÜ-1")
    try:
        assert secrets.get_api_key() == "sk-ci-ÄÖÜ-1"
    finally:
        secrets.clear_api_key()
    assert secrets.get_api_key() is None


def test_pid_alive_current_and_dead():
    from freecad_bridge.client import pid_alive

    assert pid_alive(os.getpid())
    assert not pid_alive(2**22 + 12345)
