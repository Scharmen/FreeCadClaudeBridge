"""Clean environment for child processes started from FreeCAD.

FreeCAD's bundle sets PYTHONHOME, LD_LIBRARY_PATH etc. and prepends its own
directories (including a `python`) to PATH. Every child process (Claude Code,
uvx, freecadcmd) inherits that and any foreign Python breaks. No FreeCAD
imports here so it stays unit-testable.
"""

from __future__ import annotations

import os

STRIP_VARS = (
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONSTARTUP",
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "DYLD_FALLBACK_LIBRARY_PATH",
    "SSL_CERT_FILE",
    "GIT_SSL_CAINFO",
    "FONTCONFIG_PATH",
    "FONTCONFIG_FILE",
    "QT_PLUGIN_PATH",
    "PYSIDE6_OPTION_PYTHON_ENUM",
    "CLAUDECODE",
)
STRIP_PREFIXES = ("CLAUDE_CODE_",)


def clean_child_env(environ: dict[str, str], bundle_home: str | None = None,
                    user_mod_dir: str | None = None) -> dict[str, str]:
    """Return a copy of `environ` safe for a non-FreeCAD child process.

    `bundle_home` is App.getHomePath(); `user_mod_dir` the user's Mod folder.
    PATH entries below either are dropped.
    """
    env = {k: v for k, v in environ.items()
           if k not in STRIP_VARS and not k.startswith(STRIP_PREFIXES)}
    drop = [p.rstrip("/") for p in (bundle_home, user_mod_dir) if p]
    parts = []
    for p in env.get("PATH", "").split(os.pathsep):
        if not p or any(p == d or p.startswith(d + "/") for d in drop):
            continue
        if p not in parts:
            parts.append(p)
    for extra in ("/usr/local/bin", "/opt/homebrew/bin", os.path.expanduser("~/.local/bin"),
                  "/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        if extra not in parts and os.path.isdir(extra):
            parts.append(extra)
    env["PATH"] = os.pathsep.join(parts)
    return env
