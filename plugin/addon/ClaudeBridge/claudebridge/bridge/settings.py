"""User settings shared by the addon (preferences page) and the server (run_headless).

Identical copy in addon and server (CI checks). Stored as JSON in the bridge
directory (settings.json, 0600); only values that differ from the default are
kept, and every value is clamped to its allowed range when read or written.
Standard library only.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any

FILE_NAME = "settings.json"
MB = 1024 ** 2
GB = 1024 ** 3

# key: (default, minimum, maximum, unit, description)
SCHEMA: dict[str, tuple[Any, Any, Any, str, str]] = {
    "headless.reserve_percent": (15.0, 5.0, 50.0, "%",
                                 "Share of RAM always kept free for the system and FreeCAD"),
    "headless.reserve_min_gb": (2.0, 0.5, 64.0, "GB", "Keep at least this much RAM free"),
    "headless.hard_cap_percent": (75.0, 10.0, 95.0, "%", "A headless job may use at most this share of RAM"),
    "headless.contributor_min_mb": (512, 64, 16384, "MB",
                                    "Stop a job for low system memory only if it uses more than this"),
    "headless.timeout_s": (600, 10, 86400, "s", "Default time limit of run_headless"),
}


def defaults() -> dict[str, Any]:
    return {k: spec[0] for k, spec in SCHEMA.items()}


def clamp(key: str, value: Any) -> Any:
    default, lo, hi = SCHEMA[key][:3]
    number = float(value)
    if number != number:  # NaN
        raise ValueError(f"{key}: not a number")
    number = min(max(number, lo), hi)
    return int(round(number)) if isinstance(default, int) else round(number, 2)


def path(bridge_dir: Any) -> str:
    return os.path.join(str(bridge_dir), FILE_NAME)


def load(bridge_dir: Any) -> dict[str, Any]:
    """Effective settings: defaults overlaid with valid stored values."""
    values = defaults()
    try:
        with open(path(bridge_dir), encoding="utf-8") as f:
            stored = json.load(f)
    except (OSError, ValueError):
        return values
    if isinstance(stored, dict):
        for key, value in stored.items():
            if key in SCHEMA:
                try:
                    values[key] = clamp(key, value)
                except (TypeError, ValueError):
                    pass
    return values


def save(bridge_dir: Any, changes: dict[str, Any]) -> dict[str, Any]:
    """Apply changes (unknown keys raise KeyError), store only non-defaults, return the effective settings."""
    for key in changes:
        if key not in SCHEMA:
            raise KeyError(f"unknown setting '{key}'; known: {', '.join(SCHEMA)}")
    values = load(bridge_dir)
    values.update({k: clamp(k, v) for k, v in changes.items()})
    stored = {k: v for k, v in values.items() if v != SCHEMA[k][0]}
    os.makedirs(str(bridge_dir), mode=0o700, exist_ok=True)
    tmp = path(bridge_dir) + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(stored, f, indent=2)
    os.replace(tmp, path(bridge_dir))
    return values


def reset(bridge_dir: Any, key: str | None = None) -> dict[str, Any]:
    if key is None:
        try:
            os.unlink(path(bridge_dir))
        except OSError:
            pass
        return defaults()
    return save(bridge_dir, {key: SCHEMA[key][0]})


def memory_budget(total: int, values: dict[str, Any]) -> dict[str, int]:
    """Bytes: what stays free (reserve), what one job may use (hard_cap), and from when a job counts."""
    reserve = max(int(values["headless.reserve_min_gb"] * GB), int(total * values["headless.reserve_percent"] / 100))
    return {"reserve": reserve, "hard_cap": int(total * values["headless.hard_cap_percent"] / 100),
            "contributor_min": int(values["headless.contributor_min_mb"] * MB)}


def total_memory() -> int:
    """Physical RAM of this computer in bytes (0 if unknown)."""
    try:
        if sys.platform == "darwin":
            return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True,
                                      timeout=5).stdout)
        if sys.platform == "win32":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):  # noqa: N801 (Win32 name)
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(st)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))  # type: ignore[attr-defined]
            return int(st.ullTotalPhys)
        with open("/proc/meminfo") as f:
            return int(re.search(r"MemTotal:\s+(\d+)", f.read())[1]) * 1024
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return 0
