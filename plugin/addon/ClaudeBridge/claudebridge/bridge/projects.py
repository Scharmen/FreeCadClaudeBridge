"""One folder per model: the model file, its exports, its 3D-print kit and its reference pictures.

    <root>/<Name>/<Name>.FCStd
                  exports/      STL, 3MF, STEP, … from export
                  print/        the kit from the 3D print button
                  references/   pictures the model is built from

The root is a preference (default ~/Documents/FreeCAD Projects). A model that already lives in a folder of
its own keeps it; one saved loose in the home, Documents, Desktop or Downloads folder gets a project folder
under the root for its new files. Standard library only, no FreeCAD: unit-tested.
"""

from __future__ import annotations

import os
import re

PARAM = "ProjectsFolder"
SUBFOLDERS = {"export": "exports", "print": "print", "reference": "references"}


def default_root(home: str = "") -> str:
    return os.path.join(home or os.path.expanduser("~"), "Documents", "FreeCAD Projects")


def folder_name(label: str) -> str:
    """A folder and file name that works on every system."""
    name = re.sub(r'[\\/:*?"<>|]+', "_", (label or "").strip()).strip(" .")
    return name[:80] or "Model"


def _loose(folder: str, root: str, home: str) -> bool:
    norm = os.path.normcase(os.path.normpath(folder))
    shared = [home] + [os.path.join(home, d) for d in ("Documents", "Desktop", "Downloads")] + [root]
    return norm in {os.path.normcase(os.path.normpath(d)) for d in shared}


def project_folder(file: str, label: str, root: str, home: str = "") -> str:
    """The folder a model's files belong in."""
    home = home or os.path.expanduser("~")
    if file:
        folder = os.path.dirname(file)
        if not _loose(folder, root, home):
            return folder
        label = label or os.path.splitext(os.path.basename(file))[0]
    return os.path.join(root, folder_name(label))


def model_path(folder: str, label: str) -> str:
    return os.path.join(folder, folder_name(label) + ".FCStd")


def place(path: str, kind: str, folder: str) -> str:
    """Where a file goes: a bare name (no folder) lands in the project folder, exports in exports/; a path
    with a folder is the user's choice and stays."""
    path = os.path.expanduser(str(path))
    if os.path.dirname(path):
        return os.path.abspath(path)
    if kind == "save":
        return os.path.join(folder, path if path.lower().endswith(".fcstd") else path + ".FCStd")
    return os.path.join(folder, SUBFOLDERS.get(kind, ""), path)


def context(file: str, label: str, root: str, home: str = "") -> str:
    """For Claude's context line: where this model's files go."""
    folder = project_folder(file, label, root, home)
    rules = "exports/ for STL, 3MF, STEP; references/ for pictures"
    if not file:
        return f"Not saved yet: save it as {model_path(folder, label)}; {rules}"
    if os.path.dirname(file) != folder:
        return (f"Saved loose; its project folder is {folder} (new files there: {rules}; ask before moving the "
                "model)")
    return f"Project folder {folder}: {rules}"
