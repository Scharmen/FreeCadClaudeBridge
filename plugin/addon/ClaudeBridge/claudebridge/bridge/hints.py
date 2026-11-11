"""One line of help for errors Claude's code runs into again and again."""

from __future__ import annotations

MAX_NAMES = 40


def for_error(error: BaseException, names: list[str]) -> str:
    """A hint to append to the error message, or "". `names` are the document's object names."""
    text = str(error)
    if isinstance(error, AttributeError) and "'NoneType' object has no attribute" in text:
        shown = ", ".join(names[:MAX_NAMES]) + (f" … ({len(names)} in all)" if len(names) > MAX_NAMES else "")
        return ("Hint: doc.getObject(name) returns None when no object has that internal Name (a Label is not "
                "a Name). Names in this document: " + (shown or "none") + ".")
    return ""


# C++ calls that can run for hours on a real mesh and cannot be interrupted from Python: in the GUI
# process they freeze FreeCAD until it is force-quit (seen: fixDegenerations on a fine mesh, 26 minutes).
UNSTOPPABLE = ("fixDegenerations", "fixSelfIntersections", "fixDeformations", "fixCaps")
MARK = "=====STACK====="


def unstoppable(code: str) -> str:
    """Why this code must not run in the open FreeCAD, or ""."""
    import re

    plain = re.sub(r"#.*", "", code)
    hit = next((name for name in UNSTOPPABLE if re.search(rf"\.\s*{name}\s*\(", plain)), None)
    if hit is None:
        return ""
    return (f"{hit}() can run for hours inside FreeCAD and cannot be interrupted: the open FreeCAD would freeze "
            "until the user force-quits it. Do mesh repair with run_headless instead (it is stopped at its time "
            "limit): save or export the shape there, repair, write the result to a file and import it. Cheap "
            "steps stay fine here: harmonizeNormals, removeDuplicatedPoints, removeDuplicatedFacets, fillupHoles, "
            "removeNonManifolds.")


def stuck_path(folder: str, pid: int) -> str:
    import os

    return os.path.join(folder, f"stuck-{pid}.txt")


def stuck_header(code: str, started: float) -> str:
    """What the watchdog file starts with; Python's faulthandler appends the stacks below it."""
    return f"started {started:.0f}\n{code}\n{MARK}\n"
