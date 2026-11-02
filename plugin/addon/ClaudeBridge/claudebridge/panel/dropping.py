"""What the chat panel does with things dropped or pasted into it. No Qt, no FreeCAD: unit-tested.

- pictures are sent to Claude as real images (up to MAX_IMAGE_BYTES, else as a path)
- CAD files are referenced and offered for import into FreeCAD
- other files and folders are referenced by path for Claude to read
- FreeCAD objects dragged from the model tree become an [Object: ...] reference line
"""

from __future__ import annotations

import os

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp"}
CAD_EXTS = {".step", ".stp", ".iges", ".igs", ".brep", ".brp", ".stl", ".obj", ".3mf", ".ply", ".off",
            ".fcstd", ".dxf", ".svg", ".dae", ".ifc"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def classify(path: str) -> str:
    """image, cad, folder or file."""
    if os.path.isdir(path):
        return "folder"
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_TYPES and os.path.isfile(path) and os.path.getsize(path) <= MAX_IMAGE_BYTES:
        return "image"
    if ext in CAD_EXTS:
        return "cad"
    return "file"


def media_type(path: str) -> str:
    return IMAGE_TYPES.get(os.path.splitext(path)[1].lower(), "image/png")


def reference_line(kind: str, path: str) -> str:
    label = {"folder": "Folder", "cad": "CAD file", "file": "File", "image": "Image"}[kind]
    return f"[{label}: {path}]"


def object_reference(label: str, name: str, type_id: str, size_mm: tuple[float, float, float] | None,
                     sub: list[str] | None = None) -> str:
    """A FreeCAD object dragged into the chat, with enough for Claude to find and judge it."""
    parts = [f"{label} ({name})", type_id]
    if size_mm:
        parts.append("{:g} × {:g} × {:g} mm".format(*(round(v, 2) for v in size_mm)))
    if sub:
        parts.append("elements " + ", ".join(sub[:10]))
    return "[Object: " + ", ".join(parts) + "]"
