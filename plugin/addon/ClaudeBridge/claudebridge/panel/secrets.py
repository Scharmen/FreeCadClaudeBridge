"""API key storage: macOS Keychain via `security`, otherwise a 0600 file.

The key never goes into FreeCAD's parameters. On macOS it is passed to
`security -i` on stdin, so it doesn't show up in the process list.
"""

from __future__ import annotations

import getpass
import os
import subprocess
import sys

SERVICE = "freecad-bridge-anthropic-api-key"


def _account() -> str:
    return getpass.getuser()


def _file() -> str:
    from ..bridge import paths

    return os.path.join(paths.bridge_dir(), "api_key")


def _run(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin", "HOME": os.path.expanduser("~")}
    return subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=10, env=env)


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def get_api_key() -> str | None:
    if sys.platform == "darwin":
        r = _run(["/usr/bin/security", "find-generic-password", "-a", _account(), "-s", SERVICE, "-w"])
        return r.stdout.strip() or None if r.returncode == 0 else None
    try:
        with open(_file()) as f:
            return f.read().strip() or None
    except OSError:
        return None


def set_api_key(key: str) -> None:
    key = key.strip()
    if sys.platform == "darwin":
        cmd = f"add-generic-password -U -a {_quote(_account())} -s {SERVICE} -w {_quote(key)}\n"
        r = _run(["/usr/bin/security", "-i"], stdin=cmd)
        if r.returncode != 0 or get_api_key() != key:
            raise RuntimeError(f"could not store the key in the Keychain: {r.stderr.strip()}")
        return
    from ..bridge import paths

    paths.ensure_bridge_dir()
    fd = os.open(_file(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key)


def clear_api_key() -> None:
    if sys.platform == "darwin":
        _run(["/usr/bin/security", "delete-generic-password", "-a", _account(), "-s", SERVICE])
        return
    try:
        os.unlink(_file())
    except OSError:
        pass
