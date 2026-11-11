"""Operations executed in the GUI thread (Phase 1 set).

Each op takes (args, executor) and returns JSON-safe data or raises OpError.
"""

from __future__ import annotations

import io
import linecache
import os
import sys
import time
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


from .pacing import ScriptTimeout  # noqa: E402 (re-exported: callers catch it from here)


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
                     "modified": bool(getattr(gd, "Modified", False)), "project_folder": project_folder(d)})
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
        "projects_folder": projects_root(),
        "undo_limit": App.ParamGet("User parameter:BaseApp/Preferences/Document").GetInt("MaxUndoSize", 20),
    }


def _tracer(deadline: float, filename: str) -> Any:
    """Time limit, and FreeCAD keeps drawing while the script runs (setting chat.redraw_ms, 0 = off)."""
    from . import pacing, paths, settings

    try:
        ms = float(settings.load(paths.bridge_dir())["chat.redraw_ms"])
    except Exception:
        ms = 100.0
    return pacing.make_tracer(deadline, filename, pacing.qt_pump() if ms > 0 else None, ms / 1000.0)


STUCK_EVERY_S = 30.0


def _watch_stuck(code: str) -> Any:
    """Let Python write the GUI thread's stack to a file every 30 s while the code runs: a C++ call that
    never returns freezes FreeCAD, and the server then reads from the file which line it hangs in."""
    try:
        import faulthandler

        from . import hints, paths

        f = open(hints.stuck_path(paths.bridge_dir(), os.getpid()), "w", encoding="utf-8")  # noqa: SIM115
        f.write(hints.stuck_header(code, time.time()))
        f.flush()
        faulthandler.dump_traceback_later(STUCK_EVERY_S, repeat=True, file=f)
        return f
    except Exception:  # noqa: BLE001 - a watchdog must never break the call
        return None


def _unwatch_stuck(f: Any) -> None:
    if f is None:
        return
    try:
        import faulthandler

        faulthandler.cancel_dump_traceback_later()
        f.close()
        os.remove(f.name)
    except Exception:  # noqa: BLE001, S110
        pass


def run_python(args: dict[str, Any], ex: Any) -> dict[str, Any]:
    code = args.get("code")
    if not isinstance(code, str) or not code.strip():
        raise OpError(protocol.INVALID_ARGS, "code is empty")
    from . import hints

    refused = hints.unstoppable(code)
    if refused:
        raise OpError(protocol.INVALID_ARGS, refused)
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
    snapshot_path = None
    if args.get("snapshot") and doc is not None:
        from .action_ops import create_snapshot

        snapshot_path = create_snapshot(doc)
    before = _snapshot()
    if doc is not None:
        doc.openTransaction(args.get("transaction") or "Claude")
    ex.recorder.start()
    deadline = time.monotonic() + max(1.0, float(args.get("_timeout", 60)) - 1.5)
    previous_trace = sys.gettrace()
    watch = _watch_stuck(code)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            sys.settrace(_tracer(deadline, filename))
            try:
                exec(compile(code, filename, "exec"), ns)
            finally:
                sys.settrace(previous_trace)
            if args.get("recompute", True):
                for d in {id(x): x for x in (doc, App.ActiveDocument) if _alive(x)}.values():
                    d.recompute()
        if _alive(doc):  # the code may have closed its own document
            doc.commitTransaction()
        _unwatch_stuck(watch)
    except BaseException as e:  # noqa: BLE001 - user code may raise anything, even SystemExit
        _unwatch_stuck(watch)
        if _alive(doc):
            doc.abortTransaction()
        ex.recorder.stop()
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        user_tb = tb.split('File "<claude>"', 1)
        tb = ('File "<claude>"' + user_tb[1]) if len(user_tb) > 1 else tb
        if isinstance(e, ScriptTimeout):
            raise OpError(protocol.TIMEOUT, f"{e} (transaction rolled back)", _trunc(tb),
                          {"stdout": _trunc(out.getvalue())}) from None
        hint = hints.for_error(e, [o.Name for o in doc.Objects] if _alive(doc) else [])
        raise OpError(protocol.PY_EXCEPTION, f"{type(e).__name__}: {e} (transaction rolled back)",
                      _trunc(tb) + (f"\n{hint}" if hint else ""),
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
        "changes": {"created": created, "changed": changed_names[:50], "deleted": deleted,
                    **({"changed_count": len(changed_names)} if len(changed_names) > 50 else {})},
        "errors": _errors(touched),
        "document": home or (App.ActiveDocument.Name if App.ActiveDocument else None),
        **({"snapshot": snapshot_path} if snapshot_path else {}),
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
        from . import projects

        target = projects.place(path, "save", project_folder(doc))     # a bare name: its project folder
        if not target.lower().endswith(".fcstd"):
            target += ".FCStd"
        os.makedirs(os.path.dirname(target), exist_ok=True)
        doc.saveAs(target)
        return {"document": doc.Name, "file": doc.FileName}
    if action == "close":
        gd = Gui.getDocument(doc.Name)
        modified = bool(getattr(gd, "Modified", False))
        if modified and not args.get("force"):
            raise OpError(protocol.INVALID_ARGS, f"'{doc.Name}' has unsaved changes; save it or pass force=true")
        snap = None
        if modified and doc.Objects:  # closing without saving: keep a snapshot so nothing is lost
            from .action_ops import create_snapshot

            snap = create_snapshot(doc)
        closed = doc.Name
        App.closeDocument(closed)
        return {"closed": closed, **({"snapshot": snap} if snap else {})}
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


def projects_root() -> str:
    """Where every model gets its own folder (the preference, else ~/Documents/FreeCAD Projects)."""
    from . import projects

    return App.ParamGet("User parameter:BaseApp/Preferences/Mod/ClaudeBridge").GetString(projects.PARAM, "") \
        or projects.default_root()


def project_folder(doc: Any) -> str:
    from . import projects

    return projects.project_folder(doc.FileName or "", doc.Label, projects_root())


def export(args: dict[str, Any], ex: Any) -> dict[str, Any]:
    from . import projects

    doc = _doc(args.get("doc"))
    path = projects.place(str(args.get("path") or ""), "export", project_folder(doc)) if args.get("path") else ""
    if not args.get("path"):
        raise OpError(protocol.INVALID_ARGS, "export needs a path")
    fmt = (args.get("format") or EXPORT_FORMATS.get(os.path.splitext(path)[1].lower(), "")).lower()
    if fmt not in set(EXPORT_FORMATS.values()):
        raise OpError(protocol.INVALID_ARGS, f"unsupported format '{fmt or os.path.splitext(path)[1]}'; "
                      "use step, iges, brep, stl, obj or 3mf (DXF/SVG/PDF come later)")
    objs = [_obj(doc, n) for n in args["objects"]] if args.get("objects") else _default_export_objects(doc)
    if not args.get("objects") and fmt in ("stl", "obj", "3mf"):      # sculpted figures are meshes, not solids
        objs += [o for o in doc.Objects if o.TypeId.startswith("Mesh::") and _visible(o)]
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


from . import action_ops, inspect_ops  # noqa: E402 (they import helpers from this module)

OPS = {
    "status_gui": status_gui,
    "run_python": run_python,
    "get_tree": get_tree,
    "get_object": get_object,
    "document": document,
    "export": export,
    "inspect_shape": inspect_ops.inspect_shape,
    "inspect_sketch": inspect_ops.inspect_sketch,
    "api_help": inspect_ops.api_help,
    "list_commands": inspect_ops.list_commands,
    "get_changes": inspect_ops.get_changes,
    "get_console": inspect_ops.get_console,
    "run_command": action_ops.run_command,
    "task_dialog": action_ops.task_dialog,
    "selection": action_ops.selection,
    "import": action_ops.import_file,
    "view": action_ops.view,
    "snapshot": action_ops.snapshot,
    "sub_chat": action_ops.sub_chat,
    "print_export": action_ops.print_export,
}
