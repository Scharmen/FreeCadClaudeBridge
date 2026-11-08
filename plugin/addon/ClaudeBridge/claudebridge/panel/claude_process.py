"""One chat turn: Claude Code as an asynchronous QProcess.

The GUI thread is never blocked: QProcess delivers output through signals,
so the bridge can execute the tool calls Claude sends back meanwhile.
"""

from __future__ import annotations

import json
import os
import signal
import time
from typing import Any

import FreeCAD as App
from PySide import QtCore

from ..bridge import paths
from ..bridge.childenv import clean_child_env
from . import secrets
from .prompts import FULL_CHAT_PROMPT, SYSTEM_PROMPT
from .stream import StreamParser, TurnTracker, build_args


def child_env() -> dict[str, str]:
    """Environment for Claude Code started from FreeCAD (no bundle Python, no stray API key)."""
    return clean_child_env(dict(os.environ), App.getHomePath(), os.path.join(App.getUserAppDataDir(), "Mod"))


def open_in_desktop(session_id: str, cwd: str) -> str | None:
    """Open a Claude Code session in the Claude Desktop app. Returns an error message or None."""
    claude = find_claude()
    if not claude:
        return "Claude Code not found. Run /freecad:setup in Claude Code."
    proc = QtCore.QProcess()
    proc.setProgram(claude)
    proc.setArguments(["--desktop", "--resume", session_id])
    proc.setWorkingDirectory(cwd if os.path.isdir(cwd) else os.path.expanduser("~"))
    qenv = QtCore.QProcessEnvironment()
    for k, v in child_env().items():
        qenv.insert(k, v)
    proc.setProcessEnvironment(qenv)
    proc.setStandardInputFile(QtCore.QProcess.nullDevice())
    ok = proc.startDetached()
    if isinstance(ok, tuple):
        ok = ok[0]
    return None if ok else "Could not start Claude Code to open the Desktop app."


def claude_candidates() -> list[str]:
    home = os.path.expanduser("~")
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming"))
        return [os.path.join(home, ".local", "bin", "claude.exe"), os.path.join(appdata, "npm", "claude.cmd")]
    return [os.path.join(home, ".local", "bin", "claude"), "/opt/homebrew/bin/claude", "/usr/local/bin/claude",
            "/usr/bin/claude", os.path.join(home, ".npm-global", "bin", "claude")]


def find_claude() -> str | None:
    """GUI apps don't inherit the shell's PATH: use the path recorded by /freecad:setup, then known spots."""
    cfg = paths.load_config()
    for candidate in (cfg.get("claude_path"), *claude_candidates()):
        if candidate and os.path.isfile(candidate) and (os.name == "nt" or os.access(candidate, os.X_OK)):
            return candidate
    return None


class ClaudeRun(QtCore.QObject):
    event = QtCore.Signal(object)   # parsed stream events (dicts)
    finished = QtCore.Signal(int)   # exit code
    failed = QtCore.Signal(str)     # could not start

    def __init__(self, prompt: str, session_id: str | None, mode: str, cwd: str,
                 load_plugin_dir: bool = False, extra_allowed: list[str] | None = None, parent: Any = None) -> None:
        super().__init__(parent)
        self.prompt, self.session_id, self.mode, self.cwd = prompt, session_id, mode, cwd
        self.load_plugin_dir = load_plugin_dir
        self.extra_allowed = extra_allowed or []
        self.parser = StreamParser()
        self.proc: QtCore.QProcess | None = None
        self.started_at = 0.0
        self.stopping = False

    def start(self) -> None:
        claude = find_claude()
        if not claude:
            self.failed.emit("Claude Code not found. Run /freecad:setup in Claude Code, which records its path.")
            return
        env = child_env()
        env.pop("ANTHROPIC_API_KEY", None)
        env["FREECAD_BRIDGE_INSTANCE"] = str(os.getpid())  # the MCP server talks to this FreeCAD, not the newest
        if self.mode == "api_key":
            key = secrets.get_api_key()
            if not key:
                self.failed.emit("API key mode is selected but no key is stored. Use ⚙ → Set API key.")
                return
            env["ANTHROPIC_API_KEY"] = key
        plugin_dir = paths.load_config().get("plugin_root") if self.load_plugin_dir else None
        args = build_args(self.prompt, self.session_id, plugin_dir, extra_allowed=self.extra_allowed)
        from ..bridge import learning

        learned = learning.context_text(paths.bridge_dir())
        args += ["--append-system-prompt", SYSTEM_PROMPT + ("\n\n" + learned if learned else "")]
        qenv = QtCore.QProcessEnvironment()
        for k, v in env.items():
            qenv.insert(k, v)
        proc = QtCore.QProcess(self)
        proc.setProgram(claude)
        proc.setArguments(args)
        proc.setWorkingDirectory(self.cwd if os.path.isdir(self.cwd) else os.path.expanduser("~"))
        proc.setProcessEnvironment(qenv)
        proc.setStandardInputFile(QtCore.QProcess.nullDevice())  # otherwise claude waits 3 s for stdin
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.readyReadStandardError.connect(self._on_stderr)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self.proc = proc
        self.started_at = time.time()
        proc.start()

    def stop(self) -> None:
        """SIGINT first so Claude Code ends the turn cleanly; escalate if it doesn't exit."""
        if not self.proc or self.proc.state() == QtCore.QProcess.NotRunning:
            return
        self.stopping = True
        if os.name == "nt":  # no SIGINT for a console child on Windows; end it directly
            self.proc.kill()
            return
        try:
            os.kill(self.proc.processId(), signal.SIGINT)
        except OSError:
            self.proc.terminate()
        QtCore.QTimer.singleShot(5000, self._escalate)

    def _escalate(self) -> None:
        if self.proc and self.proc.state() != QtCore.QProcess.NotRunning:
            self.proc.terminate()
            QtCore.QTimer.singleShot(3000, lambda: self.proc and self.proc.kill())

    def running(self) -> bool:
        return bool(self.proc and self.proc.state() != QtCore.QProcess.NotRunning)

    def _on_stdout(self) -> None:
        for ev in self.parser.feed(bytes(self.proc.readAllStandardOutput())):
            self.event.emit(ev)

    def _on_stderr(self) -> None:
        text = bytes(self.proc.readAllStandardError()).decode("utf-8", "replace").strip()
        if text:
            App.Console.PrintLog(f"ClaudeBridge chat stderr: {text}\n")

    def _on_finished(self, code: int, _status: Any = None) -> None:
        for ev in self.parser.flush():
            self.event.emit(ev)
        self.finished.emit(int(code))

    def _on_error(self, err: Any) -> None:
        if err == QtCore.QProcess.FailedToStart:
            self.failed.emit(f"Could not start Claude Code: {self.proc.errorString()}")


class ClaudeSession(QtCore.QObject):
    """A persistent Claude Code session for one conversation (the full chat).

    Messages, permission answers and control requests (interrupt, model, permission mode) go
    to stdin as stream-json; events come back through StreamParser. `ready` carries Claude
    Code's initialize answer: slash commands, models with effort levels, account, agents.
    """

    event = QtCore.Signal(object)
    ready = QtCore.Signal(object)
    exited = QtCore.Signal(int)
    failed = QtCore.Signal(str)

    def __init__(self, session_id: str | None, mode: str, cwd: str, model: str | None = None,
                 effort: str | None = None, permission_mode: str | None = None, load_plugin_dir: bool = True,
                 fork: bool = False, parent: Any = None) -> None:
        super().__init__(parent)
        self.session_id, self.mode, self.cwd = session_id, mode, cwd
        self.model, self.effort, self.permission_mode = model, effort, permission_mode
        self.load_plugin_dir, self.fork = load_plugin_dir, fork
        self.parser = StreamParser()
        self.proc: QtCore.QProcess | None = None
        self.info: dict[str, Any] = {}
        self.turn = TurnTracker()   # whether Claude Code is in a turn (or about to start one)
        self._init_id: str | None = None

    def start(self) -> bool:
        claude = find_claude()
        if not claude:
            self.failed.emit("Claude Code not found. Run /freecad:setup in Claude Code, which records its path.")
            return False
        env = child_env()
        env.pop("ANTHROPIC_API_KEY", None)
        env["FREECAD_BRIDGE_INSTANCE"] = str(os.getpid())
        if self.mode == "api_key":
            key = secrets.get_api_key()
            if not key:
                self.failed.emit("API key mode is selected but no key is stored. Use ⚙ → Set API key.")
                return False
            env["ANTHROPIC_API_KEY"] = key
        from ..bridge import learning
        from .stream import session_args

        learned = learning.context_text(paths.bridge_dir())
        plugin_dir = paths.load_config().get("plugin_root") if self.load_plugin_dir else None
        args = session_args(self.session_id, plugin_dir, self.model, self.effort, self.permission_mode,
                            FULL_CHAT_PROMPT + ("\n\n" + learned if learned else ""), fork=self.fork)
        qenv = QtCore.QProcessEnvironment()
        for k, v in env.items():
            qenv.insert(k, v)
        proc = QtCore.QProcess(self)
        proc.setProgram(claude)
        proc.setArguments(args)
        proc.setWorkingDirectory(self.cwd if os.path.isdir(self.cwd) else os.path.expanduser("~"))
        proc.setProcessEnvironment(qenv)
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.readyReadStandardError.connect(self._on_stderr)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self.proc = proc
        proc.start()
        from .stream import control_request

        init = control_request("initialize")
        self._init_id = init["request_id"]
        self._send(init)
        return True

    def running(self) -> bool:
        return bool(self.proc and self.proc.state() != QtCore.QProcess.NotRunning)

    @property
    def busy(self) -> bool:
        return self.turn.busy

    def _send(self, obj: dict[str, Any]) -> None:
        if self.proc is not None:
            self.proc.write((json.dumps(obj) + "\n").encode("utf-8"))

    def send_user(self, text: str, images: list[tuple[str, bytes]] | None = None) -> None:
        from .stream import user_message

        self.turn.sent()
        self._send(user_message(text, images))

    def answer_permission(self, request_id: str, decision: str, tool_input: dict[str, Any],
                          suggestions: list[dict[str, Any]] | None = None, message: str = "") -> None:
        from .stream import permission_response

        self._send(permission_response(request_id, decision, tool_input, suggestions, message))

    def control(self, subtype: str, **fields: Any) -> None:
        from .stream import control_request

        self._send(control_request(subtype, **fields))

    def interrupt(self) -> None:
        self.control("interrupt")

    def close(self) -> None:
        """End the session: closing stdin lets Claude Code finish and exit; force it if it lingers."""
        if not self.running():
            return
        self.proc.closeWriteChannel()
        proc = self.proc
        QtCore.QTimer.singleShot(5000, lambda: proc.state() != QtCore.QProcess.NotRunning and proc.terminate())

    def _on_stdout(self) -> None:
        for ev in self.parser.feed(bytes(self.proc.readAllStandardOutput())):
            if ev["kind"] == "control_response" and ev.get("request_id") == self._init_id:
                self.info = ev.get("response") or {}
                self.ready.emit(self.info)
                continue
            self.turn.on_event(ev["kind"])
            if ev["kind"] == "init" and ev.get("session_id"):
                self.session_id = ev["session_id"]
            self.event.emit(ev)

    def _on_stderr(self) -> None:
        text = bytes(self.proc.readAllStandardError()).decode("utf-8", "replace").strip()
        if text:
            App.Console.PrintLog(f"ClaudeBridge chat stderr: {text}\n")
            from . import debuglog

            debuglog.log("stderr", text=text)

    def _on_finished(self, code: int, _status: Any = None) -> None:
        for ev in self.parser.flush():
            self.event.emit(ev)
        self.turn.reset()
        self.exited.emit(int(code))

    def _on_error(self, err: Any) -> None:
        if err == QtCore.QProcess.FailedToStart:
            self.failed.emit(f"Could not start Claude Code: {self.proc.errorString()}")
