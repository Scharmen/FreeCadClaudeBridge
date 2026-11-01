"""Memory budget for headless FreeCAD jobs, derived from the machine at run time.

A boolean on near-tangent freeform surfaces can make OpenCascade allocate without
bound (seen: 140 GB). Instead of a fixed number, a job is stopped when

- the system's available memory drops below a reserve (15 % of RAM, at least 2 GB)
  while the job itself is a real contributor (over 512 MB), or
- the job alone exceeds a hard cap (75 % of RAM, or FREECAD_BRIDGE_HEADLESS_MAX_MB).

Standard library only: macOS (sysctl, vm_stat), Linux (/proc), Windows (Win32 API).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass

ENV_MAX_MB = "FREECAD_BRIDGE_HEADLESS_MAX_MB"
MB = 1024 ** 2
GB = 1024 ** 3
RESERVE_FRACTION, RESERVE_MIN = 0.15, 2 * GB
HARD_CAP_FRACTION = 0.75
CONTRIBUTOR_MIN = 512 * MB
POLL_S = 0.05
GROUP_EVERY = 10  # also count other processes in the job's group every 10th poll (AppImages fork)


@dataclass(frozen=True)
class Budget:
    total: int
    reserve: int
    hard_cap: int

    @classmethod
    def for_total(cls, total: int, hard_cap: int | None = None) -> Budget:
        return cls(total, max(RESERVE_MIN, int(total * RESERVE_FRACTION)),
                   hard_cap if hard_cap is not None else int(total * HARD_CAP_FRACTION))

    @classmethod
    def current(cls) -> Budget:
        override = os.environ.get(ENV_MAX_MB)
        cap = int(override) * MB if override and override.isdigit() else None
        return cls.for_total(total_memory(), cap)

    def check(self, rss: int, available: int | None) -> str | None:
        """Why the job must stop now, or None."""
        if rss > self.hard_cap:
            return f"over the memory limit: job uses {rss // MB} MB, limit {self.hard_cap // MB} MB"
        if available is not None and available < self.reserve and rss > CONTRIBUTOR_MIN:
            return (f"system memory nearly exhausted: {available // MB} MB left (reserve {self.reserve // MB} MB), "
                    f"job uses {rss // MB} MB")
        return None

    def describe(self) -> dict[str, int]:
        return {"total_mb": self.total // MB, "reserve_mb": self.reserve // MB, "hard_cap_mb": self.hard_cap // MB}


# ---------- system figures

def total_memory() -> int:
    if sys.platform == "darwin":
        return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout)
    if sys.platform == "win32":
        return _win_memory_status()[0]
    return _meminfo()["MemTotal"]


def available_memory() -> int | None:
    """Memory the system can hand out without swapping; None if unknown."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=2).stdout
            page = int(re.search(r"page size of (\d+)", out)[1])
            pages = {m[1]: int(m[2]) for m in re.finditer(r"^Pages (\w+)[^:]*:\s+(\d+)", out, re.M)}
            return page * sum(pages.get(k, 0) for k in ("free", "inactive", "speculative", "purgeable"))
        if sys.platform == "win32":
            return _win_memory_status()[1]
        return _meminfo()["MemAvailable"]
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
        return None


def _meminfo() -> dict[str, int]:
    with open("/proc/meminfo") as f:
        return {k: int(v.split()[0]) * 1024 for k, v in (line.split(":", 1) for line in f)}


def _win_memory_status() -> tuple[int, int]:  # pragma: no cover - Windows only
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
    return st.ullTotalPhys, st.ullAvailPhys


def process_rss(pid: int) -> int:
    """Resident memory of one process, cheap enough to poll every 50 ms."""
    if sys.platform == "darwin":
        return _mac_rss(pid)
    if sys.platform == "win32":
        return _win_rss(pid)
    try:
        with open(f"/proc/{pid}/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return 0


_libproc = None


def _mac_rss(pid: int) -> int:
    global _libproc
    import ctypes

    if _libproc is None:
        _libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
    buf = ctypes.create_string_buffer(96)                  # struct proc_taskinfo
    PROC_PIDTASKINFO = 4  # noqa: N806
    if _libproc.proc_pidinfo(pid, PROC_PIDTASKINFO, ctypes.c_uint64(0), buf, 96) <= 0:
        return 0
    return int.from_bytes(buf.raw[8:16], "little")         # pti_resident_size


def process_tree_rss(pid: int) -> int:
    """Resident memory of a process and everything in its process group (AppImages fork)."""
    if sys.platform == "win32":
        return _win_rss(pid)
    try:
        pgid = os.getpgid(pid)
        out = subprocess.run(["ps", "-A", "-o", "pgid=,rss="], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.TimeoutExpired):
        return process_rss(pid)
    total = 0
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == str(pgid) and parts[1].isdigit():
            total += int(parts[1]) * 1024
    return max(total, process_rss(pid))


def _win_rss(pid: int) -> int:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = k32.OpenProcess(0x1000 | 0x0010, False, pid)  # QUERY_LIMITED_INFORMATION | VM_READ
    if not handle:
        return 0
    try:
        pmc = PMC()
        pmc.cb = ctypes.sizeof(pmc)
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb)  # type: ignore[attr-defined]
        return int(pmc.WorkingSetSize) if ok else 0
    finally:
        k32.CloseHandle(handle)


def kill_tree(pid: int) -> None:
    """Hard-stop the job and its process group."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=10)
        else:
            import signal

            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (OSError, subprocess.TimeoutExpired):
        pass
