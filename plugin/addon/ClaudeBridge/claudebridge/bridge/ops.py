"""Operations executed in the GUI thread (Phase 1 set).

Each op takes (args, executor) and returns JSON-safe data or raises OpError.
"""

from __future__ import annotations

import io
import linecache
import os
import traceback
from contextlib import redirect_stderr, redirect_stdout
from typing import Any

import FreeCAD as App
import FreeCADGui as Gui

from . import protocol
from .executor import OpError
from .serialize import result_value, shape_summary, to_jsonable

MAX_OUTPUT = 20000
SKIP_PROPS = {"Shape", "Proxy", "ExpressionEngine", "_Body", "Label2"}


class ChangeRecorder:
    """Collects changed objects while a bridge call runs (journal comes in Phase 2)."""

    def __init__(self) -> None:
        self.active = False
        self.changed: set[tuple[str, str]] = set()
        App.addDocumentObserver(self)

    def start(self) -> None:
        self.changed = set()
        self.active = True

    def stop(self) -> set[tuple[str, str]]:
        self.active = False
        return self.changed

    def slotChangedObject(self, obj: Any, prop: str) -> None:  # noqa: N802 (FreeCAD API)
        if self.active:
            try:
                self.changed.add((obj.Document.Name, obj.Name))
            except Exception:
                pass


# ---------- helpers ----------

def _doc(name: str | None, required: bool = True) -> Any:
    if name:
        doc = App.getDocument(name) if name in App.listDocuments() else None
        if doc is None:
            matches = [d for d in App.listDocuments().values() if d.Label == name]
            doc = matches[0] if matches else None
        if doc is None:
            raise OpError(protocol.NO_DOCUMENT, f"no open document named '{name}'")
        return doc
    doc = App.ActiveDocument
    if doc is None and required:
        raise OpError(protocol.NO_DOCUMENT, "no active document; create one with document(action='new')")
    return doc


def _obj(doc: Any, name: str) -> Any:
    obj = doc.getObject(name)
    if obj is None:
        by_label = doc.getObjectsByLabel(name)
        obj = by_label[0] if by_label else None
    if obj is None:
        raise OpError(protocol.NOT_FOUND, f"no object '{name}' in document '{doc.Name}'")
    return obj


def _state(obj: Any) -> list[str]:
    try:
        return [s for s in obj.State if s in ("Invalid", "Error", "Touched", "Restore")]
    except Exception:
        return []


def _errors(docs: list[Any]) -> list[dict[str, Any]]:
    out = []
    for doc in docs:
        for o in doc.Objects:
            st = list(getattr(o, "State", []))
            if "Invalid" in st or "Error" in st:
                out.append({"doc": doc.Name, "name": o.Name, "label": o.Label, "type": o.TypeId, "state": st})
    return out


def _alive(doc: Any) -> bool:
    """False for None and for documents closed in the meantime (their proxies raise ReferenceError)."""
    if doc is None:
        return False
    try:
        return doc.Name in App.listDocuments()
    except ReferenceError:
        return False


def _snapshot() -> dict[str, set[str]]:
    return {name: {o.Name for o in d.Objects} for name, d in App.listDocuments().items()}


def _trunc(text: str) -> str:
    return text if len(text) <= MAX_OUTPUT else text[:MAX_OUTPUT] + f"\n… truncated ({len(text)} chars)"


def _visible(obj: Any) -> bool:
    try:
        return bool(obj.Visibility)
    except Exception:
        vo = getattr(obj, "ViewObject", None)
        return bool(vo and vo.Visibility)


def _parent(obj: Any) -> Any:
    for getter in ("getParentGeoFeatureGroup", "getParentGroup"):
        try:
            p = getattr(obj, getter)()
        except Exception:
            p = None
        if p is not None:
            return p
    return None


def _undo_info(doc: Any) -> dict[str, Any]:
    return {"undo": list(doc.UndoNames)[:5], "redo": list(doc.RedoNames)[:5]}


# ---------- ops ----------

def status_gui(args: dict[str, Any], ex: Any) -> dict[str, Any]:
    docs = []
    for name, d in App.listDocuments().items():
        gd = Gui.getDocument(name)
        docs.append({"name": name, "label": d.Label, "file": d.FileName or None, "objects": len(d.Objects),
                     "modified": bool(getattr(gd, "Modified", False))})
    sel = []
    for s in Gui.Selection.getSelectionEx("", 0):
        sel.append({"doc": s.DocumentName, "object": s.ObjectName, "label": s.Object.Label,
                    "sub": list(s.SubElementNames)})
    try:
        wb = Gui.activeWorkbench().name()
    except Exception:
        wb = None
    return {
        "workbench": wb,
        "documents": docs,
        "active_document": App.ActiveDocument.Name if App.ActiveDocument else None,
        "selection": sel[:50],
        "task_dialog_open": bool(Gui.Control.activeDialog()),
        "undo_limit": App.ParamGet("User parameter:BaseApp/Preferences/Document").GetInt("MaxUndoSize", 20),
    }


def run_python(args: dict[str, Any], ex: Any) -> dict[str, Any]:
    code = args.get("code")
    if not isinstance(code, str) or not code.strip():
        raise OpError(protocol.INVALID_ARGS, "code is empty")
    doc = _doc(args.get("doc"), required=False)
    ns = ex.namespace
    if not ns:
        import Part
        import Sketcher

        ns.update(App=App, FreeCAD=App, Gui=Gui, FreeCADGui=Gui, Part=Part, Sketcher=Sketcher,
                  Vector=App.Vector, Placement=App.Placement, Rotation=App.Rotation)
    ns["doc"] = doc
    ns["_result"] = None
    filename = "<claude>"
    linecache.cache[filename] = (len(code), None, code.splitlines(True), filename)
    out, err = io.StringIO(), io.StringIO()
    before = _snapshot()
    if doc is not None:
        doc.openTransaction(args.get("transaction") or "Claude")
    ex.recorder.start()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            exec(compile(code, filename, "exec"), ns)
            if args.get("recompute", True):
                for d in {id(x): x for x in (doc, App.ActiveDocument) if _alive(x)}.values():
                    d.recompute()
        if _alive(doc):  # the code may have closed its own document
            doc.commitTransaction()
    except BaseException as e:  # noqa: BLE001 - user code may raise anything, even SystemExit
        if _alive(doc):
            doc.abortTransaction()
        ex.recorder.stop()
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        user_tb = tb.split('File "<claude>"', 1)
        tb = ('File "<claude>"' + user_tb[1]) if len(user_tb) > 1 else tb
        raise OpError(protocol.PY_EXCEPTION, f"{type(e).__name__}: {e} (transaction rolled back)", _trunc(tb),
                      {"stdout": _trunc(out.getvalue()), "stderr": _trunc(err.getvalue())}) from None
    changed = ex.recorder.stop()
    after = _snapshot()
    home = doc.Name if _alive(doc) else None

    def label(d: str, o: str) -> str:  # objects of the call's document without prefix
        return o if d == home else f"{d}#{o}"

    created = [label(d, o) for d, objs in after.items() for o in sorted(objs - before.get(d, set()))]
    deleted = [label(d, o) for d, objs in before.items() for o in sorted(objs - after.get(d, set()))]
    changed_names = sorted({label(d, o) for d, o in changed if d in after} - set(created))
    touched = [App.getDocument(n) for n in after if n in App.listDocuments()]
    return {
        "result": result_value(ns.get("_result")),
        "stdout": _trunc(out.getvalue()),
        "stderr": _trunc(err.getvalue()),
        "changes": {"created": created, "changed": changed_names[:200], "deleted": deleted},
        "errors": _errors(touched),
        "document": home or (App.ActiveDocument.Name if App.ActiveDocument else None),
    }


def get_tree(args: dict[str, Any], ex: Any) -> dict[str, Any]:
    doc = _doc(args.get("doc"))
    max_depth = int(args.get("depth", 2))
    flt = (args.get("filter") or "").lower()
    limit, offset = int(args.get("limit", 200)), int(args.get("offset", 0))
    rows = []
    for o in doc.Objects:
        chain, p = [], _parent(o)
        while p is not None and len(chain) < 20:
            chain.append(p)
            p = _parent(p)
        depth = len(chain)
        if depth > max_depth:
            continue
        if flt and flt not in o.Name.lower() and flt not in o.Label.lower() and flt not in o.TypeId.lower():
            continue
        rows.append({"name": o.Name, "label": o.Label, "type": o.TypeId,
                     "parent": chain[0].Name if chain else None, "depth": depth,
                     "visible": _visible(o), "state": _state(o)})
    return {"document": doc.Name, "total": len(rows), "offset": offset,
            "objects": rows[offset:offset + limit],
            "more": offset + limit < len(rows)}


def get_object(args: dict[str, Any], ex: Any) -> dict[str, Any]:
    doc = _doc(args.get("doc"))
    obj = _obj(doc, str(args.get("name", "")))
    wanted = args.get("props")
    props: dict[str, Any] = {}
    for p in obj.PropertiesList:
        if wanted and p not in wanted:
            continue
        if not wanted and p in SKIP_PROPS:
            continue
        try:
            props[p] = to_jsonable(getattr(obj, p))
        except Exception as e:
            props[p] = f"<unreadable: {e}>"
    out: dict[str, Any] = {"name": obj.Name, "label": obj.Label, "type": obj.TypeId, "document": doc.Name,
                           "state": list(getattr(obj, "State", [])), "properties": props}
    try:
        expr = obj.ExpressionEngine
        if expr:
            out["expressions"] = {k: v for k, v in expr}
    except Exception:
        pass
    parent = _parent(obj)
    if parent is not None:
        out["parent"] = parent.Name
    if hasattr(obj, "Shape"):
        try:
            out["shape"] = shape_summary(obj.Shape)
        except Exception as e:
            out["shape"] = {"error": str(e)}
    return out


def document(args: dict[str, Any], ex: Any) -> dict[str, Any]:
    action = args.get("action")
    name, path = args.get("name"), args.get("path")
    if action == "list":
        return status_gui(args, ex)
    if action == "new":
        d = App.newDocument(name or "Unnamed")
        App.setActiveDocument(d.Name)
        return {"document": d.Name, "label": d.Label}
    if action == "open":
        if not path or not os.path.exists(os.path.expanduser(path)):
            raise OpError(protocol.NOT_FOUND, f"file not found: {path}")
        d = App.openDocument(os.path.expanduser(path))
        return {"document": d.Name, "label": d.Label, "objects": len(d.Objects)}
    doc = _doc(name)
    if action == "save":
        if not doc.FileName:
            raise OpError(protocol.INVALID_ARGS, "document was never saved; use save_as with a path")
        doc.save()
        return {"document": doc.Name, "file": doc.FileName}
    if action == "save_as":
        if not path:
            raise OpError(protocol.INVALID_ARGS, "save_as needs a path")
        target = os.path.abspath(os.path.expanduser(path))
        if not target.lower().endswith(".fcstd"):
            target += ".FCStd"
        doc.saveAs(target)
        return {"document": doc.Name, "file": doc.FileName}
    if action == "close":
        gd = Gui.getDocument(doc.Name)
        if getattr(gd, "Modified", False) and not args.get("force"):
            raise OpError(protocol.INVALID_ARGS, f"'{doc.Name}' has unsaved changes; save it or pass force=true")
        closed = doc.Name
        App.closeDocument(closed)
        return {"closed": closed}
    if action == "activate":
        App.setActiveDocument(doc.Name)
        try:
            Gui.setActiveDocument(doc.Name)
        except Exception:
            pass
        return {"active_document": doc.Name}
    if action == "recompute":
        doc.recompute()
        return {"document": doc.Name, "errors": _errors([doc])}
    if action in ("undo", "redo"):
        available = doc.UndoNames if action == "undo" else doc.RedoNames
        if not available:
            raise OpError(protocol.INVALID_ARGS, f"nothing to {action}")
        getattr(doc, action)()
        doc.recompute()
        return {"document": doc.Name, "did": available[0], **_undo_info(doc)}
    raise OpError(protocol.INVALID_ARGS, f"unknown action '{action}'")


EXPORT_FORMATS = {".step": "step", ".stp": "step", ".iges": "iges", ".igs": "iges", ".brep": "brep",
                  ".brp": "brep", ".stl": "stl", ".obj": "obj", ".3mf": "3mf"}


def _default_export_objects(doc: Any) -> list[Any]:
    objs = [o for o in doc.Objects if _visible(o) and hasattr(o, "Shape")
            and not o.Shape.isNull() and o.Shape.Solids]
    names = {o.Name for o in objs}
    return [o for o in objs if _parent(o) is None or _parent(o).Name not in names]


def export(args: dict[str, Any], ex: Any) -> dict[str, Any]:
    doc = _doc(args.get("doc"))
    path = os.path.abspath(os.path.expanduser(str(args.get("path") or "")))
    if not args.get("path"):
        raise OpError(protocol.INVALID_ARGS, "export needs a path")
    fmt = (args.get("format") or EXPORT_FORMATS.get(os.path.splitext(path)[1].lower(), "")).lower()
    if fmt not in set(EXPORT_FORMATS.values()):
        raise OpError(protocol.INVALID_ARGS, f"unsupported format '{fmt or os.path.splitext(path)[1]}'; "
                      "use step, iges, brep, stl, obj or 3mf (DXF/SVG/PDF come later)")
    objs = [_obj(doc, n) for n in args["objects"]] if args.get("objects") else _default_export_objects(doc)
    if not objs:
        raise OpError(protocol.NOT_FOUND, "nothing to export (no visible solids)")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if fmt in ("step", "iges"):
        import Import

        Import.export(objs, path)
    elif fmt == "brep":
        import Part

        Part.makeCompound([o.Shape for o in objs]).exportBrep(path)
    else:
        import Mesh

        Mesh.export(objs, path)
    if not os.path.exists(path):
        raise OpError(protocol.INTERNAL, f"export reported success but {path} does not exist")
    return {"path": path, "format": fmt, "objects": [o.Name for o in objs], "size_bytes": os.path.getsize(path)}


OPS = {
    "status_gui": status_gui,
    "run_python": run_python,
    "get_tree": get_tree,
    "get_object": get_object,
    "document": document,
    "export": export,
}
