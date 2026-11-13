"""The line tracer around Claude's run_python code: enforces the time limit and keeps FreeCAD drawing.

run_python runs in FreeCAD's GUI thread (FreeCAD's API is not thread-safe), so a long script used to freeze
the whole window. Between lines the tracer now lets Qt process pending events now and then, without user
input (a click could change the document in the middle of Claude's transaction; it waits until the call
is done), and spending at most a tenth of the time on it. Windows repaint, the chat panel's status keeps
moving and macOS does not mark FreeCAD as hanging. A single long C++ operation (a big boolean, a
recompute) still blocks until it returns: Python is not running then. Standard library only, no FreeCAD:
unit-tested.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


class ScriptTimeout(Exception):
    """Raised by the trace hook inside user code when the deadline passes."""


def make_tracer(deadline: float, filename: str, pump: Callable[[], None] | None, interval: float,
                clock: Callable[[], float] = time.monotonic, own: str | None = None) -> Any:
    """A sys.settrace hook. Lines of Claude's own code (`filename`) check the deadline; every Python frame,
    also code that Claude's script loads with exec, may pump the GUI at most every `interval` seconds.

    Never from inside the addon's own code (files under `own`): Qt calls it as callbacks, a row painter or a
    slot, while a document loads inside the script, and events pumped from in there start a second repaint
    under the first: FreeCAD dies in Qt."""
    due = [clock() + interval]
    calls = [0]
    inside = [0]                                         # how deep we are in the addon's own callbacks

    def guard(frame: Any, event: str, arg: Any) -> Any:
        if event == "return":                            # also when an exception leaves the frame
            inside[0] -= 1
        return guard

    def pump_at(now: float) -> None:
        pump()                                           # type: ignore[misc]  (only called when set)
        done = clock()
        # redrawing a heavy model takes a while: wait 9 times as long, so it costs at most a tenth
        due[0] = done + max(interval, 9 * (done - now))

    def local(frame: Any, event: str, arg: Any) -> Any:
        if event == "line":
            now = clock()                                # one clock read per line: the tracer is hot
            if now > deadline:
                raise ScriptTimeout("time limit reached; use run_headless for long computations")
            if pump is not None and now >= due[0] and not inside[0]:
                pump_at(now)
        return local

    def global_(frame: Any, event: str, arg: Any) -> Any:
        if frame.f_code.co_filename == filename:
            return local                                 # its lines pump; a generator resumes here per item
        if own and frame.f_code.co_filename.startswith(own):
            inside[0] += 1
            return guard
        if pump is not None and not inside[0]:           # code the script loaded (exec): pump between calls
            calls[0] += 1
            if calls[0] & 255 == 0:                      # every call is too often to read the clock
                now = clock()
                if now >= due[0]:
                    pump_at(now)
        return None

    return global_


def qt_pump(max_ms: int = 20) -> Callable[[], None]:
    """Process pending Qt events except user input, for at most `max_ms`."""
    from PySide import QtCore

    flags = QtCore.QEventLoop.ExcludeUserInputEvents

    def pump() -> None:
        QtCore.QCoreApplication.processEvents(flags, max_ms)

    return pump
