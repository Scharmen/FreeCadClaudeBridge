"""Find the newest safety copy that still holds an object: when a part went missing from the model (unsaved
work lost when FreeCAD closed), the bridge's copies usually still have it. Reads only Document.xml inside
the FCStd archive, so it is quick and does not need FreeCAD. Standard library only: unit-tested.
"""

from __future__ import annotations

import glob
import os
import re
import zipfile


def _names(path: str) -> set[str]:
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("Document.xml").decode("utf-8", "replace")
    except (OSError, KeyError, zipfile.BadZipFile):
        return set()
    return set(re.findall(r'<Object\b[^>]*\bname="([^"]+)"', xml))


def newest_copy_with(folder: str, name: str) -> str | None:
    """The most recent .FCStd in `folder` that contains an object called `name`, or None."""
    copies = sorted(glob.glob(os.path.join(folder, "*.FCStd")), key=os.path.getmtime, reverse=True)
    return next((p for p in copies if name in _names(p)), None)


def copy_time(path: str) -> str:
    """'9 Nov 14:43' from a copy named 20261109-144342.FCStd; the file name otherwise."""
    m = re.match(r"(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})", os.path.basename(path))
    if not m:
        return os.path.splitext(os.path.basename(path))[0]
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{int(m[3])} {months[int(m[2]) - 1]} {m[4]}:{m[5]}"
