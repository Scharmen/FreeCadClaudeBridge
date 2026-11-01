import sys

import pytest

from claudebridge.bridge.childenv import clean_child_env

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="macOS-style PATH with ':'")

BUNDLE = "/Applications/FreeCAD.app/Contents/Resources"
MOD = "/Users/k/Library/Application Support/FreeCAD/v1-1/Mod"


def freecad_env():
    """What FreeCAD 1.1.4 on macOS passes to child processes (seen in Phase 0)."""
    return {
        "HOME": "/Users/k",
        "PYTHONHOME": BUNDLE,
        "PYTHONPATH": "",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LD_LIBRARY_PATH": BUNDLE + "/lib",
        "SSL_CERT_FILE": BUNDLE + "/ssl/cacert.pem",
        "GIT_SSL_CAINFO": BUNDLE + "/ssl/cacert.pem",
        "FONTCONFIG_PATH": "/etc/fonts",
        "PYSIDE6_OPTION_PYTHON_ENUM": "True",
        "CLAUDE_CODE_EXECPATH": "/x/claude",
        "ANTHROPIC_API_KEY": "keep-decided-by-caller",
        "PATH": ":".join([BUNDLE + "/bin", BUNDLE + "/Mod/Part", MOD + "/ThreadSpike",
                          "/Users/k/.local/bin", "/usr/bin", "/bin", "/usr/bin"]),
    }


@posix_only
def test_strips_python_and_bundle_vars():
    env = clean_child_env(freecad_env(), BUNDLE + "/", MOD)
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "LD_LIBRARY_PATH", "SSL_CERT_FILE",
                "GIT_SSL_CAINFO", "FONTCONFIG_PATH", "PYSIDE6_OPTION_PYTHON_ENUM", "CLAUDE_CODE_EXECPATH"):
        assert key not in env
    assert env["HOME"] == "/Users/k"
    assert env["ANTHROPIC_API_KEY"] == "keep-decided-by-caller"


@posix_only
def test_path_without_bundle_entries_and_deduplicated():
    parts = clean_child_env(freecad_env(), BUNDLE, MOD)["PATH"].split(":")
    assert not any(p.startswith(BUNDLE) or p.startswith(MOD) for p in parts)
    assert parts[:3] == ["/Users/k/.local/bin", "/usr/bin", "/bin"]
    assert len(parts) == len(set(parts))


def test_input_not_modified():
    src = freecad_env()
    clean_child_env(src, BUNDLE, MOD)
    assert src["PYTHONHOME"] == BUNDLE


def test_windows_style_paths(monkeypatch):
    import os

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(os, "pathsep", ";")
    bundle = "C:\\Program Files\\FreeCAD 1.1"
    env = {"Path": ";".join([bundle + "\\bin", "c:\\program files\\freecad 1.1\\lib", "C:\\Windows\\system32",
                            "C:\\Users\\Käse Müller\\.local\\bin", "C:\\Windows\\System32"]),
           "PythonHome": bundle + "\\bin", "CLAUDE_CODE_SSE_PORT": "1"}
    out = clean_child_env(env, bundle, None)
    assert "PythonHome" not in out and "CLAUDE_CODE_SSE_PORT" not in out
    assert out["Path"].split(";")[:2] == ["C:\\Windows\\system32", "C:\\Users\\Käse Müller\\.local\\bin"]
    assert "PATH" not in out
