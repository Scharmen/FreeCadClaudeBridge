"""Where a frozen FreeCAD hangs.

While run_python runs, the addon lets Python's faulthandler write the GUI thread's stack to
`stuck-<pid>.txt` in the bridge directory every 30 seconds (it works even while a C++ call holds the
interpreter). When a call times out or finds FreeCAD busy, the line it hangs in is read from there.
"""

from __future__ import annotations

import os
import re
import time

MARK = "=====STACK====="


def read(text: str, now: float) -> str:
    """The line the newest stack is in and for how long the call has run, as a sentence, or ""."""
    head, _, stacks = text.partition(f"\n{MARK}\n")
    lines = head.split("\n")
    started = re.match(r"started (\d+)", lines[0]) if lines else None
    last = stacks.rsplit("Thread 0x", 1)[-1] if "Thread 0x" in stacks else stacks   # the main thread comes last
    frame = re.search(r'File "<claude>", line (\d+)', last)
    if not started or not frame:
        return ""
    number = int(frame[1])
    source = lines[number].strip() if number < len(lines) else ""
    seconds = int(max(0.0, now - int(started[1])))
    return (f"FreeCAD's main thread has been inside line {number} of the run_python code"
            + (f" (`{source[:160]}`)" if source else "") + f" for {seconds // 60}:{seconds % 60:02d} min. A C++ call cannot be "
            "interrupted: if it does not return, FreeCAD stays frozen until the user force-quits it, and what "
            "was not saved is lost. Tell the user now which call hangs, do not send more work to FreeCAD, and do "
            "such work with run_headless next time.")


def note(folder: str, pid: int, now: float | None = None) -> str:
    try:
        with open(os.path.join(folder, f"stuck-{pid}.txt"), encoding="utf-8", errors="replace") as f:
            return read(f.read(), time.time() if now is None else now)
    except OSError:
        return ""
