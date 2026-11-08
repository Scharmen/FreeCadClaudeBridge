"""Check for a newer ClaudeBridge and update the plugin and addon from the chat panel.

The addon makes no network requests itself: Claude Code refreshes the marketplace (or git fetches the
checkout a folder marketplace reads from). The commands run in a worker thread; results reach the GUI
thread through a Qt signal. What to run is decided in updates.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from typing import Any

from PySide import QtCore

from . import debuglog, updates
from .common import _buttons, _params


class _Relay(QtCore.QObject):
    message = QtCore.Signal(object)     # (kind, payload) from the worker thread


def _run(cmd: list[str], env: dict[str, str], timeout: float = 600) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _json_list(text: str) -> list[dict[str, Any]]:
    """The JSON array in a command's output (Claude Code may print notices before it)."""
    start = text.find("[")
    try:
        data = json.loads(text[start:]) if start >= 0 else []
    except ValueError:
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def _tools() -> tuple[str | None, dict[str, str], str | None, str | None]:
    """claude, a clean environment, git and uvx (call in the GUI thread: it asks FreeCAD for paths)."""
    from .claude_process import child_env, find_claude

    env = child_env()
    search = env.get("PATH", "")
    return find_claude(), env, updates.find_tool("git", search), updates.find_tool("uvx", search)


def _installed(claude: str, env: dict[str, str]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    code, out = _run([claude, "plugin", "list", "--json"], env, 120)
    plugin = updates.find_plugin(_json_list(out)) if code == 0 else None
    code, out = _run([claude, "plugin", "marketplace", "list", "--json"], env, 120)
    return plugin, (_json_list(out) if code == 0 else [])


class UpdatesMixin:
    def _init_updates(self) -> None:
        self._update_relay = _Relay()
        self._update_relay.message.connect(self._on_update_message)
        self.update_available: str | None = None
        self.updating = False
        self._update_offered: str | None = None
        self.update_timer = QtCore.QTimer(self)
        self.update_timer.timeout.connect(lambda: self.check_for_updates(manual=False))
        self.update_timer.start(3600 * 1000)            # the check itself runs at most once a day
        QtCore.QTimer.singleShot(8000, lambda: self.check_for_updates(manual=False))
        latest = _params().GetString("UpdateLatest", "")
        if latest:
            self._show_update_state(latest)

    # ----- the check

    def check_for_updates(self, manual: bool = True) -> None:
        p = _params()
        if not manual and (not p.GetBool("CheckUpdates", True)
                           or not updates.check_due(p.GetFloat("UpdateCheckedAt", 0.0) or None, time.time())):
            return
        claude, env, git, _uvx = _tools()
        if not claude:
            if manual:
                self._note("Claude Code not found. Run /freecad:setup in Claude Code.", error=True)
            return
        relay = self._update_relay

        def work() -> None:
            plugin, markets = _installed(claude, env)
            steps, location, problem = updates.check_steps(plugin, markets, claude, git)
            if problem:
                relay.message.emit(("check_failed", (manual, problem)))
                return
            out = ""
            for label, cmd in steps:
                code, out = _run(cmd, env, 180)
                if code != 0:
                    relay.message.emit(("check_failed", (manual, f"{label} failed: {out.strip()[-300:]}")))
                    return
            if location:
                path = updates.market_manifest(location)
                try:
                    with open(path or "", encoding="utf-8") as f:
                        out = f.read()
                except OSError:
                    out = ""
            relay.message.emit(("checked", (manual, updates.manifest_version(out))))

        if manual:
            self._note("Checking for a newer ClaudeBridge…")
        threading.Thread(target=work, name="ClaudeBridge update check", daemon=True).start()

    def _show_update_state(self, latest: str) -> None:
        from .. import __version__

        if updates.is_newer(latest, __version__):
            self.update_available = latest
            self.version_label.setText(f"<a href='update:' style='color:#e08a00'>v{__version__} → {latest}</a>")
            self.version_label.setToolTip(f"ClaudeBridge {latest} is available; you run {__version__}. "
                                          "Click to update the plugin and the addon.")
        else:
            self.update_available = None
            self.version_label.setText(f"v{__version__}")
            self.version_label.setToolTip(f"ClaudeBridge addon version {__version__}. After an update, restart "
                                          "FreeCAD to load the new version.")

    # ----- the update

    def update_addon(self) -> None:
        if self.updating:
            return
        if self._working():
            self._note("Wait until Claude is done, or press Stop, before updating.")
            return
        claude, env, git, uvx = _tools()
        if not claude:
            self._note("Claude Code not found. Run /freecad:setup in Claude Code.", error=True)
            return
        if not uvx:
            self._note("uv is not installed, so the addon cannot be installed from here. Run /freecad:setup "
                       "in Claude Code.", error=True)
            return
        self.updating = True
        self._note("Updating ClaudeBridge…")
        relay = self._update_relay

        def say(text: str) -> None:
            relay.message.emit(("progress", text))

        def work() -> None:
            plugin, markets = _installed(claude, env)
            steps, problem = updates.fetch_steps(plugin, markets, claude, git)
            if problem:
                relay.message.emit(("update_failed", (problem, "")))
                return
            for label, cmd in steps:
                say(label + "…")
                code, out = _run(cmd, env)
                debuglog.log("update_step", cmd=cmd, code=code, output=out[-2000:])
                if code != 0:
                    relay.message.emit(("update_failed", (f"{label} failed (exit code {code}).", out)))
                    return
            code, out = _run([claude, "plugin", "list", "--json"], env, 120)
            plugin = updates.find_plugin(_json_list(out))
            root = updates.plugin_root(plugin) if plugin else ""
            if not root or not os.path.isdir(root):
                relay.message.emit(("update_failed", ("Could not find the updated plugin.", out)))
                return
            label, cmd = updates.install_step(root, uvx)
            say(label + "…")
            code, out = _run(cmd, env)
            debuglog.log("update_step", cmd=cmd, code=code, output=out[-2000:])
            if code != 0:
                relay.message.emit(("update_failed", (f"{label} failed (exit code {code}).", out)))
                return
            try:
                with open(os.path.join(root, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
                    version = updates.manifest_version(f.read())
            except OSError:
                version = None
            relay.message.emit(("updated", version))

        threading.Thread(target=work, name="ClaudeBridge update", daemon=True).start()

    # ----- results, in the GUI thread

    def _on_update_message(self, msg: Any) -> None:
        from .. import __version__

        kind, payload = msg
        if kind == "checked":
            manual, latest = payload
            p = _params()
            p.SetFloat("UpdateCheckedAt", time.time())
            if not latest:
                if manual:
                    self._note("Could not read the newest version. Try again later.", error=True)
                return
            p.SetString("UpdateLatest", latest)
            self._show_update_state(latest)
            if self.update_available and (manual or self._update_offered != latest):
                self._update_offered = latest
                self._note(f"ClaudeBridge {latest} is available; you run {__version__}. Updating refreshes the "
                           "Claude Code plugin and installs the addon; FreeCAD loads it after a restart.")
                self._append(_buttons([("Update now", "update:", "allow")],
                                      tip={"update:": f"Update the plugin and the addon to {latest}"}))
            elif manual:
                self._note(f"ClaudeBridge {__version__} is the latest version.")
        elif kind == "check_failed":
            manual, error = payload
            _params().SetFloat("UpdateCheckedAt", time.time())      # try again tomorrow, not every hour
            debuglog.log("update_check_failed", error=error)
            if manual:
                self._note(f"Could not check for updates: {error}", error=True)
        elif kind == "progress":
            self._note(str(payload))
        elif kind == "update_failed":
            self.updating = False
            problem, output = payload
            tail = "\n".join(output.strip().splitlines()[-6:]) if output else ""
            self._note(problem + (f"\n{tail}" if tail else ""), error=True)
        elif kind == "updated":
            self.updating = False
            version = payload or "?"
            if version != "?" and not updates.is_newer(version, __version__):
                self._note(f"Nothing newer reached this computer: the plugin is at {version}. If it comes from "
                           "a folder, push or pull there first.")
                return
            self._update_noted = True              # the "installed, restart" note below replaces the usual one
            self.version_label.setText(f"<span style='color:#2e7d32'>v{__version__} → {version} installed</span>")
            self.version_label.setToolTip("Restart FreeCAD to load the new version.")
            self._note(f"ClaudeBridge {version} is installed. Save your work and restart FreeCAD to load it.")
