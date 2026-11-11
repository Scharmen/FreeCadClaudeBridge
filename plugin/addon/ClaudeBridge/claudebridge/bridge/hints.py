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
