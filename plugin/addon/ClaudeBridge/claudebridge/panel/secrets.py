"""API key storage in the system keychain, never in FreeCAD's parameters.

- macOS: Keychain via `security -i` (the key goes over stdin, not the process list)
- Windows: Credential Manager via the Win32 Cred API (ctypes)
- Linux: Secret Service via `secret-tool` (key on stdin) when available
- otherwise: a file with permissions 0600 in the bridge directory
"""

from __future__ import annotations

import getpass
import os
import shutil
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
    for key in ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "DISPLAY", "WAYLAND_DISPLAY"):
        if key in os.environ:  # secret-tool needs the session bus
            env[key] = os.environ[key]
    return subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=10, env=env)


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def backend() -> str:
    if sys.platform == "darwin":
        return "keychain"
    if sys.platform == "win32":
        return "wincred"
    if shutil.which("secret-tool") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return "secret-service"
    return "file"


# ---------- Windows Credential Manager ----------

def _wincred():  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    class CREDENTIAL(ctypes.Structure):  # noqa: N801 (Win32 name)
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                    ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                    ("UserName", wintypes.LPWSTR)]

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    advapi.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
    advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi.CredFree.argtypes = [ctypes.c_void_p]
    return ctypes, CREDENTIAL, advapi


CRED_TYPE_GENERIC, CRED_PERSIST_LOCAL_MACHINE = 1, 2


def _win_get() -> str | None:  # pragma: no cover - Windows only
    ctypes, CREDENTIAL, advapi = _wincred()  # noqa: N806
    ptr = ctypes.POINTER(CREDENTIAL)()
    if not advapi.CredReadW(SERVICE, CRED_TYPE_GENERIC, 0, ctypes.byref(ptr)):
        return None
    try:
        cred = ptr.contents
        return ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize).decode("utf-16-le") or None
    finally:
        advapi.CredFree(ptr)


def _win_set(key: str) -> None:  # pragma: no cover - Windows only
    ctypes, CREDENTIAL, advapi = _wincred()  # noqa: N806
    blob = key.encode("utf-16-le")
    cred = CREDENTIAL()
    cred.Type, cred.Persist = CRED_TYPE_GENERIC, CRED_PERSIST_LOCAL_MACHINE
    cred.TargetName, cred.UserName = SERVICE, _account()
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(ctypes.create_string_buffer(blob, len(blob)), ctypes.POINTER(ctypes.c_char))
    if not advapi.CredWriteW(ctypes.byref(cred), 0):
        raise RuntimeError(f"CredWrite failed (error {ctypes.get_last_error()})")


def _win_clear() -> None:  # pragma: no cover - Windows only
    _, _, advapi = _wincred()
    advapi.CredDeleteW(SERVICE, CRED_TYPE_GENERIC, 0)


# ---------- public API ----------

def get_api_key() -> str | None:
    b = backend()
    if b == "keychain":
        r = _run(["/usr/bin/security", "find-generic-password", "-a", _account(), "-s", SERVICE, "-w"])
        return r.stdout.strip() or None if r.returncode == 0 else None
    if b == "wincred":
        return _win_get()
    if b == "secret-service":
        r = _run([shutil.which("secret-tool") or "secret-tool", "lookup", "service", SERVICE, "account", _account()])
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    try:
        with open(_file()) as f:
            return f.read().strip() or None
    except OSError:
        return None


def set_api_key(key: str) -> None:
    key = key.strip()
    b = backend()
    if b == "keychain":
        cmd = f"add-generic-password -U -a {_quote(_account())} -s {SERVICE} -w {_quote(key)}\n"
        r = _run(["/usr/bin/security", "-i"], stdin=cmd)
        if r.returncode != 0 or get_api_key() != key:
            raise RuntimeError(f"could not store the key in the Keychain: {r.stderr.strip()}")
        return
    if b == "wincred":
        _win_set(key)
        return
    if b == "secret-service":
        r = _run([shutil.which("secret-tool") or "secret-tool", "store", "--label=FreeCAD Claude bridge API key",
                  "service", SERVICE, "account", _account()], stdin=key)
        if r.returncode == 0:
            return
    from ..bridge import paths

    paths.ensure_bridge_dir()
    fd = os.open(_file(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key)


def clear_api_key() -> None:
    b = backend()
    if b == "keychain":
        _run(["/usr/bin/security", "delete-generic-password", "-a", _account(), "-s", SERVICE])
    elif b == "wincred":
        _win_clear()
    elif b == "secret-service":
        _run([shutil.which("secret-tool") or "secret-tool", "clear", "service", SERVICE, "account", _account()])
    try:
        os.unlink(_file())
    except OSError:
        pass
