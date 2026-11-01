"""Addon lifecycle: start the bridge server and chat panel, register menu commands."""

from __future__ import annotations

import os
import sys
from typing import Any

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from . import __version__

PARAMS = "User parameter:BaseApp/Preferences/Mod/ClaudeBridge"
COMMANDS = ["ClaudeBridge_TogglePanel", "ClaudeBridge_Status", "ClaudeBridge_Restart"]
_state: dict[str, Any] = {}


def params() -> Any:
    return App.ParamGet(PARAMS)


def log(msg: str) -> None:
    App.Console.PrintLog(msg + "\n")


def boot() -> None:
    """Called from InitGui.py. Defers the real start until the event loop runs."""
    _register_commands()
    QtCore.QTimer.singleShot(0, start)


def start() -> None:
    if "server" in _state:
        return
    p = params()
    if p.GetBool("Autostart", True):
        try:
            _start_server(p.GetString("Transport", "auto"))
        except Exception as e:
            App.Console.PrintError(f"ClaudeBridge: could not start the bridge: {e}\n")
    if p.GetBool("ShowPanel", True):
        try:
            from .panel.dock import ensure_panel

            ensure_panel(show=p.GetBool("PanelVisible", True))
        except Exception as e:
            App.Console.PrintError(f"ClaudeBridge: could not create the chat panel: {e}\n")
    app = QtWidgets.QApplication.instance()
    if app and not _state.get("quit_hooked"):
        app.aboutToQuit.connect(stop)
        _state["quit_hooked"] = True


def _start_server(transport: str) -> None:
    from .bridge.executor import Executor
    from .bridge.transport import BridgeServer

    home = App.getHomePath()
    info = {
        "freecad_version": ".".join(App.Version()[:3]),
        "user_app_data": App.getUserAppDataDir(),
        "freecad_home": home,
        "python": sys.version.split()[0],
        **_freecadcmd_info(home),
    }
    executor = _state.get("executor") or Executor()
    server = BridgeServer(executor, __version__, info, log, transport)
    server.start()
    _state.update(executor=executor, server=server)
    App.Console.PrintMessage(f"ClaudeBridge {__version__} ready\n")


def _freecadcmd_info(home: str) -> dict[str, Any]:
    """How to start this FreeCAD's freecadcmd. Inside an AppImage the bundle is a temporary mount,
    so the AppImage itself is called with the `freecadcmd` argument."""
    appimage = os.environ.get("APPIMAGE")
    if appimage and os.path.isfile(appimage):
        return {"freecadcmd": appimage, "freecadcmd_cmd": [appimage, "freecadcmd"]}
    for name in ("freecadcmd", "FreeCADCmd", "freecadcmd.exe", "FreeCADCmd.exe"):
        path = os.path.join(home, "bin", name)
        if os.path.isfile(path):
            return {"freecadcmd": path, "freecadcmd_cmd": [path]}
    return {"freecadcmd": None, "freecadcmd_cmd": None}


def stop() -> None:
    server = _state.pop("server", None)
    if server:
        server.stop()


def restart() -> None:
    stop()
    _start_server(params().GetString("Transport", "auto"))


def status_text() -> str:
    server = _state.get("server")
    if not server:
        return "ClaudeBridge is not running."
    busy = _state["executor"].busy
    return (f"ClaudeBridge {__version__}\nListening on: {server.address}\n"
            f"GUI thread: {'busy' if busy else 'idle'}\nPID: {server.pid}")


class _Command:
    def __init__(self, menu: str, tip: str, fn: Any, accel: str = "") -> None:
        self.menu, self.tip, self.fn, self.accel = menu, tip, fn, accel

    def GetResources(self) -> dict[str, str]:  # noqa: N802 (FreeCAD API)
        res = {"MenuText": self.menu, "ToolTip": self.tip}
        if self.accel:
            res["Accel"] = self.accel
        return res

    def Activated(self) -> None:  # noqa: N802
        self.fn()

    def IsActive(self) -> bool:  # noqa: N802
        return True


def _toggle_panel() -> None:
    from .panel.dock import toggle_panel

    toggle_panel()


def _show_status() -> None:
    QtWidgets.QMessageBox.information(Gui.getMainWindow(), "Claude Bridge", status_text())


def _register_commands() -> None:
    if _state.get("commands"):
        return
    Gui.addCommand(COMMANDS[0], _Command("Claude chat panel", "Show or hide the Claude chat panel",
                                         _toggle_panel, "Ctrl+Alt+K"))
    Gui.addCommand(COMMANDS[1], _Command("Bridge status", "Show the Claude bridge status", _show_status))
    Gui.addCommand(COMMANDS[2], _Command("Restart bridge", "Restart the Claude bridge server", restart))
    _state["commands"] = True
