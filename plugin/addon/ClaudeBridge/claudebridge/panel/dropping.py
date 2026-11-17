"""What the chat panel does with things dropped or pasted into it. No Qt, no FreeCAD: unit-tested.

Every dropped, pasted or chosen file becomes an attachment of the next message (a chip above the input):

- pictures are sent to Claude as real images (up to MAX_IMAGE_BYTES)
- PDFs are sent as documents (up to MAX_PDF_BYTES), UTF-8 text files with their content (up to MAX_TEXT_BYTES)
- CAD files are named by path and offered for import into FreeCAD
- everything else (binary, too big, folders) is named by path for Claude to open itself
- FreeCAD objects dragged from the model tree become an [Object: ...] reference line
"""

from __future__ import annotations

import base64
import os
from typing import Any

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp"}
CAD_EXTS = {".step", ".stp", ".iges", ".igs", ".brep", ".brp", ".stl", ".obj", ".3mf", ".ply", ".off",
            ".fcstd", ".dxf", ".svg", ".dae", ".ifc"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_TEXT_BYTES = 256 * 1024
ICONS = {"image": "🖼", "pdf": "📄", "text": "📄", "cad": "📦", "file": "📎", "folder": "📁"}


def classify(path: str) -> str:
    """image, pdf, text, cad, folder or file."""
    if os.path.isdir(path):
        return "folder"
    ext = os.path.splitext(path)[1].lower()
    size = os.path.getsize(path) if os.path.isfile(path) else None
    if ext in IMAGE_TYPES and size is not None and size <= MAX_IMAGE_BYTES:
        return "image"
    if ext in CAD_EXTS:
        return "cad"
    if size is None or ext in IMAGE_TYPES:
        return "file"
    if ext == ".pdf":
        return "pdf" if size <= MAX_PDF_BYTES else "file"
    return "text" if size <= MAX_TEXT_BYTES and _is_text(path) else "file"


def _is_text(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            data = f.read()
        data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return b"\x00" not in data


def attachment(path: str) -> dict[str, Any]:
    """One file for the next message: path, name, kind and, for what is sent as content, its bytes."""
    kind = classify(path)
    att: dict[str, Any] = {"path": path, "name": os.path.basename(path.rstrip("/\\")) or path, "kind": kind}
    if kind in ("image", "pdf", "text"):
        try:
            with open(path, "rb") as f:
                att["data"] = f.read()
        except OSError:
            att["kind"] = "file"
            return att
        att["media_type"] = {"image": media_type(path), "pdf": "application/pdf", "text": "text/plain"}[kind]
    return att


def content_blocks(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The content of pictures, PDFs and text files as blocks of a user message."""
    blocks: list[dict[str, Any]] = []
    for a in attachments:
        kind = a.get("kind", "image")
        if kind in ("image", "pdf"):
            block: dict[str, Any] = {
                "type": "image" if kind == "image" else "document",
                "source": {"type": "base64", "media_type": a["media_type"],
                           "data": base64.b64encode(a["data"]).decode()}}
            if kind == "pdf":
                block["title"] = a["name"]
            blocks.append(block)
        elif kind == "text":
            body = a["data"].decode("utf-8")
            blocks.append({"type": "text", "text": f"[Attached file {a['name']}, saved at {a['path']}]\n"
                                                   f"<file name=\"{a['name']}\">\n{body}\n</file>"})
    return blocks


def reference_lines(attachments: list[dict[str, Any]]) -> list[str]:
    """Attachments that go to Claude as a path: CAD files, folders, binary and oversized files."""
    return [reference_line(a["kind"], a["path"]) for a in attachments if a.get("kind") in ("cad", "file", "folder")]


def chip_text(att: dict[str, Any]) -> str:
    name = att["name"]
    return f"{ICONS.get(att['kind'], '📎')} {name if len(name) <= 24 else name[:23] + '…'}"


def chip_tip(att: dict[str, Any]) -> str:
    how = {"image": "Sent as a picture", "pdf": "Sent as a document, with its content",
           "text": "Sent with its content", "cad": "Sent as a path; Claude imports it into FreeCAD",
           "folder": "Sent as a path; Claude looks into the folder"}.get(
               att["kind"], "Sent as a path (not text, or too big to send); Claude opens it itself")
    return f"{how} with your next message. Click to remove."


def media_type(path: str) -> str:
    return IMAGE_TYPES.get(os.path.splitext(path)[1].lower(), "image/png")


def reference_line(kind: str, path: str) -> str:
    label = {"folder": "Folder", "cad": "CAD file", "image": "Image"}.get(kind, "File")
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
