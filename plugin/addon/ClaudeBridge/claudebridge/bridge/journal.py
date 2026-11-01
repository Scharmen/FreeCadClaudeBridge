"""Journal: what happens in the GUI, with a sequence number and a source.

Document and selection observers append to a ring buffer. While a bridge call
runs, entries carry source "claude", otherwise "user". Property changes are
aggregated per object (dragging a placement fires hundreds of events), so one
entry lists all properties changed in a burst.

The `Journal` class has no FreeCAD imports so the aggregation logic is
unit-testable; `install_observers()` wires it to FreeCAD.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

MAX_ENTRIES = 2000
MERGE_WINDOW_S = 2.0


class Journal:
    def __init__(self, maxlen: int = MAX_ENTRIES) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._seq = 0
        self._lock = threading.Lock()
        self.claude_active = False

    @property
    def seq(self) -> int:
        return self._seq

    def source(self) -> str:
        return "claude" if self.claude_active else "user"

    def add(self, event: str, **fields: Any) -> None:
        now = time.time()
        src = self.source()
        with self._lock:
            last = self._entries[-1] if self._entries else None
            if (event == "changed" and last and last["event"] == "changed" and last["source"] == src
                    and last.get("doc") == fields.get("doc") and last.get("object") == fields.get("object")
                    and now - last["last_time"] < MERGE_WINDOW_S):
                prop = fields.get("prop")
                if prop and prop not in last["props"]:
                    last["props"].append(prop)
                last["count"] += 1
                last["last_time"] = now
                return
            self._seq += 1
            entry = {"seq": self._seq, "time": now, "last_time": now, "source": src, "event": event, **fields}
            if event == "changed":
                entry["props"] = [fields["prop"]] if fields.get("prop") else []
                entry["count"] = 1
                entry.pop("prop", None)
            self._entries.append(entry)

    def since(self, seq: int = 0, source: str = "all", limit: int = 200) -> dict[str, Any]:
        with self._lock:
            items = [e for e in self._entries if e["seq"] > seq and (source == "all" or e["source"] == source)]
            oldest = self._entries[0]["seq"] if self._entries else self._seq + 1
        truncated = len(items) > limit
        items = items[-limit:] if truncated else items
        out = []
        for e in items:
            e = {k: v for k, v in e.items() if k not in ("last_time",)}
            e["time"] = time.strftime("%H:%M:%S", time.localtime(e["time"]))
            out.append(e)
        return {"latest_seq": self._seq, "since": seq, "events": out,
                "truncated": truncated, "gap": seq + 1 < oldest and seq > 0}


class _DocObserver:
    def __init__(self, journal: Journal) -> None:
        self.j = journal

    @staticmethod
    def _ids(obj: Any) -> dict[str, str]:
        try:
            return {"doc": obj.Document.Name, "object": obj.Name, "label": obj.Label}
        except Exception:
            return {}

    def slotCreatedObject(self, obj: Any) -> None:  # noqa: N802 (FreeCAD API)
        self.j.add("created", type=getattr(obj, "TypeId", "?"), **self._ids(obj))

    def slotDeletedObject(self, obj: Any) -> None:  # noqa: N802
        self.j.add("deleted", **self._ids(obj))

    def slotChangedObject(self, obj: Any, prop: str) -> None:  # noqa: N802
        if prop in ("Shape", "Label2", "ExpressionEngine", "_Body"):
            return  # Shape changes follow every recompute; not informative
        self.j.add("changed", prop=prop, **self._ids(obj))

    def slotRecomputedDocument(self, doc: Any) -> None:  # noqa: N802
        self.j.add("recomputed", doc=doc.Name)

    def slotUndoDocument(self, doc: Any) -> None:  # noqa: N802
        self.j.add("undo", doc=doc.Name)

    def slotRedoDocument(self, doc: Any) -> None:  # noqa: N802
        self.j.add("redo", doc=doc.Name)

    def slotCreatedDocument(self, doc: Any) -> None:  # noqa: N802
        self.j.add("document_opened", doc=doc.Name)

    def slotDeletedDocument(self, doc: Any) -> None:  # noqa: N802
        self.j.add("document_closed", doc=doc.Name)

    def slotActivateDocument(self, doc: Any) -> None:  # noqa: N802
        self.j.add("document_activated", doc=doc.Name)

    def slotFinishSaveDocument(self, doc: Any, path: str) -> None:  # noqa: N802
        self.j.add("saved", doc=doc.Name, path=path)


class _SelObserver:
    def __init__(self, journal: Journal) -> None:
        self.j = journal

    def addSelection(self, doc: str, obj: str, sub: str, pnt: Any = None) -> None:  # noqa: N802
        self.j.add("selected", doc=doc, object=obj, sub=sub or None)

    def removeSelection(self, doc: str, obj: str, sub: str) -> None:  # noqa: N802
        self.j.add("deselected", doc=doc, object=obj, sub=sub or None)

    def clearSelection(self, doc: str) -> None:  # noqa: N802
        self.j.add("selection_cleared", doc=doc or None)


def install_observers(journal: Journal) -> list[Any]:
    """Register with FreeCAD; returns the observer objects (keep references alive)."""
    import FreeCAD as App
    import FreeCADGui as Gui

    doc_obs, sel_obs = _DocObserver(journal), _SelObserver(journal)
    App.addDocumentObserver(doc_obs)
    Gui.Selection.addObserver(sel_obs)
    return [doc_obs, sel_obs]
