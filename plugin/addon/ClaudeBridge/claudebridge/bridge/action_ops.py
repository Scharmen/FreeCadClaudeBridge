"""Action ops: GUI commands, task dialogs, selection, import, view, snapshots."""

from __future__ import annotations

import base64
import glob
import os
import re
import shutil
import tempfile
import time
from typing import Any

import FreeCAD as App
import FreeCADGui as Gui

from . import paths, protocol
from .executor import OpError
from .ops import _doc, _errors, _obj, _snapshot, _visible

MAX_SNAPSHOTS = 20
VIEWS = {"iso": "viewIsometric", "isometric": "viewIsometric", "front": "viewFront", "rear": "viewRear",
         "back": "viewRear", "top": "viewTop", "bottom": "viewBottom", "left": "viewLeft", "right": "viewRight",
         "dimetric": "viewDimetric", "trimetric": "viewTrimetric"}


def _diff(before: dict[str, set[str]]) -> dict[str, list[str]]:
    after = _snapshot()
    created = [f"{d}#{o}" for d, objs in after.items() for o in sorted(objs - before.get(d, set()))]
    deleted = [f"{d}#{o}" for d, objs in before.items() for o in sorted(objs - after.get(d, set()))]
    return {"created": created, "deleted": deleted}


def _dialog_info() -> dict[str, Any]:
    if not Gui.Control.activeDialog():
        return {"open": False}
    info: dict[str, Any] = {"open": True}
    td = Gui.Control.activeTaskDialog()
    if td is not None:
        try:
            info["widgets"] = [w.windowTitle() for w in td.getDialogContent() if w.windowTitle()][:10]
        except Exception:
            pass
        try:
            info["buttons"] = int(td.getStandardButtons())
        except Exception:
            pass
    return info


def _set_selection(items: list[Any], clear: bool = True) -> None:
    if clear:
        Gui.Selection.clearSelection()
    for item in items:
        if isinstance(item, str):  # "Object" or "Object.Face6"
            obj_name, _, sub = item.partition(".")
            item = {"object": obj_name, "sub": [sub] if sub else []}
        doc = _doc(item.get("doc"))
        obj = _obj(doc, item["object"])
        subs = item.get("sub") or [""]
        if isinstance(subs, str):
            subs = [subs]
        for sub in subs:
            Gui.Selection.addSelection(doc.Name, obj.Name, sub)


def _selection() -> list[dict[str, Any]]:
    return [{"doc": s.DocumentName, "object": s.ObjectName, "label": s.Object.Label, "sub": list(s.SubElementNames)}
            for s in Gui.Selection.getSelectionEx("", 0)][:100]


def run_command(args: dict[str, Any], ex: Any) -> dict[str, Any]:
    name = str(args.get("name", ""))
    if name not in Gui.Command.listAll():
        raise OpError(protocol.NOT_FOUND, f"unknown command '{name}'; use list_commands to find one")
    if Gui.Control.activeDialog() and not args.get("allow_with_dialog"):
        raise OpError(protocol.TASK_DIALOG_OPEN, "a task dialog is open; finish it with task_dialog first",
                      extra={"dialog": _dialog_info()})
    if args.get("workbench"):
        Gui.activateWorkbench(args["workbench"])
    if args.get("selection") is not None:
        _set_selection(args["selection"])
    before = _snapshot()
    ex.recorder.start()
    try:
        Gui.runCommand(name)
        Gui.updateGui()
        doc = App.ActiveDocument
        if doc is not None and doc.mustExecute():
            doc.recompute()
    finally:
        changed = ex.recorder.stop()
    out = {"command": name, "changes": {**_diff(before), "changed": sorted(f"{d}#{o}" for d, o in changed)[:200]},
           "errors": _errors(list(App.listDocuments().values())), "task_dialog": _dialog_info()}
    if out["task_dialog"]["open"]:
        out["hint"] = "The command opened a task dialog; use task_dialog(action='accept'|'reject') to finish it."
    return out


def task_dialog(args: dict[str, Any], ex: Any) -> dict[str, Any]:
    action = args.get("action", "status")
    info = _dialog_info()
    if action == "status":
        return info
    if not info["open"]:
        raise OpError(protocol.NOT_FOUND, "no task dialog is open")
    before = _snapshot()
    if action in ("accept", "reject"):
        td = Gui.Control.activeTaskDialog()
        if td is None:
            raise OpError(protocol.INTERNAL, "dialog open but not accessible; try action='close'")
        getattr(td, action)()
    elif action == "close":
        Gui.Control.closeDialog()
    else:
        raise OpError(protocol.INVALID_ARGS, "action must be status, accept, reject or close")
    Gui.updateGui()
    return {"action": action, "dialog_after": _dialog_info(), "changes": _diff(before),
            "errors": _errors(list(App.listDocuments().values()))}


def selection(args: dict[str, Any], ex: Any) -> dict[str, Any]:
    action = args.get("action", "get")
    items = args.get("items") or []
    if action == "clear":
        Gui.Selection.clearSelection()
    elif action == "set":
        _set_selection(items)
    elif action == "add":
        _set_selection(items, clear=False)
    elif action != "get":
        raise OpError(protocol.INVALID_ARGS, "action must be get, set, add or clear")
    return {"selection": _selection()}


IMPORT_KINDS = {".step": "cad", ".stp": "cad", ".iges": "cad", ".igs": "cad", ".brep": "cad", ".brp": "cad",
                ".stl": "mesh", ".obj": "mesh", ".3mf": "mesh", ".ply": "mesh", ".fcstd": "fcstd"}


def import_file(args: dict[str, Any], ex: Any) -> dict[str, Any]:
    path = os.path.abspath(os.path.expanduser(str(args.get("path") or "")))
    if not os.path.isfile(path):
        raise OpError(protocol.NOT_FOUND, f"file not found: {path}")
    kind = IMPORT_KINDS.get(os.path.splitext(path)[1].lower())
    if kind is None:
        raise OpError(protocol.INVALID_ARGS, f"unsupported file type; supported: {', '.join(sorted(IMPORT_KINDS))}")
    doc = _doc(args.get("doc"), required=False)
    if doc is None:
        doc = App.newDocument(re.sub(r"\W", "_", os.path.splitext(os.path.basename(path))[0]) or "Imported")
    App.setActiveDocument(doc.Name)
    before = {o.Name for o in doc.Objects}
    doc.openTransaction(f"Import {os.path.basename(path)}")
    try:
        if kind == "cad":
            import Import

            Import.insert(path, doc.Name)
        elif kind == "mesh":
            import Mesh

            Mesh.insert(path, doc.Name)
        else:
            doc.mergeProject(path)
        doc.recompute()
        doc.commitTransaction()
    except Exception as e:
        doc.abortTransaction()
        raise OpError(protocol.PY_EXCEPTION, f"import failed: {e}") from None
    created = [o for o in doc.Objects if o.Name not in before]
    items = [{"name": o.Name, "label": o.Label, "type": o.TypeId} for o in created]
    return {"document": doc.Name, "created": items[:200], "errors": _errors([doc])}


def view(args: dict[str, Any], ex: Any) -> dict[str, Any]:
    v = Gui.ActiveDocument.ActiveView if Gui.ActiveDocument else None
    animated = v.isAnimationEnabled() if v and hasattr(v, "isAnimationEnabled") else False
    if animated:      # jump: a camera still moving ends up off centre, and the screenshot cropped
        v.setAnimationEnabled(False)
    try:
        return _view(args, ex)
    finally:
        if animated:
            v.setAnimationEnabled(True)


def _view(args: dict[str, Any], ex: Any) -> dict[str, Any]:
    gdoc = Gui.ActiveDocument
    if gdoc is None:
        raise OpError(protocol.NO_DOCUMENT, "no active document")
    v = gdoc.ActiveView
    camera = v.getCamera()
    if args.get("camera"):                 # put a camera back that an earlier call returned
        v.setCamera(str(args["camera"]))
    action = args.get("action", "fit")
    doc = App.ActiveDocument
    objs = [_obj(doc, n) for n in args.get("objects") or []]
    if action in ("set_view", "fit") and args.get("view"):
        method = VIEWS.get(str(args["view"]).lower())
        if not method:
            raise OpError(protocol.INVALID_ARGS, f"view must be one of {', '.join(sorted(set(VIEWS)))}")
        getattr(v, method)()
    if action == "show":
        for o in objs:
            o.Visibility = True
    elif action == "hide":
        for o in objs:
            o.Visibility = False
    elif action == "isolate":
        keep = {o.Name for o in objs}
        for o in objs:
            p = o
            while p is not None:  # keep parents visible so the object can show
                keep.add(p.Name)
                p = p.getParentGeoFeatureGroup() if hasattr(p, "getParentGeoFeatureGroup") else None
        for o in doc.Objects:
            if hasattr(o, "Visibility") and o.Name not in keep and _visible(o):
                o.Visibility = False
    elif action not in ("fit", "set_view"):
        raise OpError(protocol.INVALID_ARGS, "action must be fit, set_view, show, hide or isolate")
    if action in ("fit", "isolate") or args.get("fit"):
        v.fitAll()
    look = [_obj(doc, n) for n in args.get("look_at") or []]
    if look:                               # frame only these: a close-up without exporting pictures
        kept = [(s.DocumentName, s.ObjectName, list(s.SubElementNames)) for s in Gui.Selection.getSelectionEx("", 0)]
        Gui.Selection.clearSelection()
        for o in look:
            Gui.Selection.addSelection(doc.Name, o.Name)
        Gui.SendMsgToActiveView("ViewSelection")
        Gui.Selection.clearSelection()
        for d, n, subs in kept:
            for sub in subs or [""]:
                Gui.Selection.addSelection(d, n, sub)
    zoom = float(args.get("zoom") or 1.0)
    if zoom > 0 and zoom != 1.0:
        from pivy import coin  # noqa: F401  (loads the Coin wrappers; without it getCameraNode fails in a fresh FreeCAD)

        cam = v.getCameraNode()
        for field in ("height", "heightAngle"):
            if hasattr(cam, field):
                getattr(cam, field).setValue(getattr(cam, field).getValue() / zoom)
                break
    out: dict[str, Any] = {"action": action, "visible": [o.Name for o in doc.Objects if _visible(o)][:200]}
    if args.get("return_camera"):
        out["camera"] = camera             # as it was before this call
    if args.get("screenshot"):
        Gui.updateGui()
        w, h = int(args.get("width", 800)), int(args.get("height", 600))
        fd, png = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            v.saveImage(png, w, h, "Current")
            with open(png, "rb") as f:
                out["image_png_base64"] = base64.b64encode(f.read()).decode()
        finally:
            os.unlink(png)
    return out


def _snap_dir(doc: Any) -> str:
    return os.path.join(paths.bridge_dir(), "snapshots", re.sub(r"[^\w.-]", "_", doc.Name))


def create_snapshot(doc: Any) -> str:
    d = _snap_dir(doc)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, time.strftime("%Y%m%d-%H%M%S") + ".FCStd")
    doc.saveCopy(path)
    snaps = sorted(glob.glob(os.path.join(d, "*.FCStd")))
    for old in snaps[:-MAX_SNAPSHOTS]:
        os.unlink(old)
    return path


def snapshot(args: dict[str, Any], ex: Any) -> dict[str, Any]:
    action = args.get("action", "list")
    doc = _doc(args.get("doc"))
    if action == "create":
        path = create_snapshot(doc)
        return {"document": doc.Name, "snapshot": os.path.basename(path), "path": path}
    snaps = sorted(glob.glob(os.path.join(_snap_dir(doc), "*.FCStd")), reverse=True)
    if action == "list":
        return {"document": doc.Name, "snapshots": [
            {"name": os.path.basename(p), "size_bytes": os.path.getsize(p)} for p in snaps]}
    if action == "restore":
        name = args.get("name") or (os.path.basename(snaps[0]) if snaps else None)
        match = [p for p in snaps if os.path.basename(p) == name]
        if not match:
            raise OpError(protocol.NOT_FOUND, f"no snapshot '{name}' for {doc.Name}")
        tmp = os.path.join(tempfile.mkdtemp(prefix="fcb-restore-"), f"{doc.Name}_snapshot_{name}")
        shutil.copy(match[0], tmp)
        restored = App.openDocument(tmp)
        restored.Label = f"{doc.Label} (snapshot {name.removesuffix('.FCStd')})"
        return {"opened_as": restored.Name, "label": restored.Label,
                "note": "Opened as a separate, unsaved copy; the original is unchanged. Use save_as to keep it."}
    raise OpError(protocol.INVALID_ARGS, "action must be create, list or restore")


def sub_chat(args: dict[str, Any], ex: Any) -> dict[str, Any]:
    """Offer the user sub chats for parts of the active model in the chat panel (buttons; nothing opens
    by itself)."""
    doc = App.ActiveDocument
    if doc is None:
        raise OpError(protocol.NO_DOCUMENT, "no active document")
    found, missing = [], []
    for name in args.get("parts") or []:
        obj = doc.getObject(str(name)) or next(iter(doc.getObjectsByLabel(str(name))), None)
        if obj is None:
            missing.append(name)
        else:
            found.append({"name": obj.Name, "label": obj.Label})
    from ..panel import dock

    panel = dock.get_panel()
    if panel is not None and found:
        panel.offer_sub_chats(found)
    return {"offered": [p["label"] for p in found], "missing": missing, "panel": panel is not None,
            "note": "The user opens each sub chat with a click; it starts from this chat's history."}
