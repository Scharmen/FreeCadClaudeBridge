"""One chat turn: Claude Code as an asynchronous QProcess.

The GUI thread is never blocked: QProcess delivers output through signals,
so the bridge can execute the tool calls Claude sends back meanwhile.
"""

from __future__ import annotations

import os
import signal
import time
from typing import Any

import FreeCAD as App
from PySide import QtCore

from ..bridge import paths
from ..bridge.childenv import clean_child_env
from . import secrets
from .stream import StreamParser, build_args

SYSTEM_PROMPT = (
    "You are running inside the 'Claude' chat panel of the user's FreeCAD. Use the freecad MCP tools "
    "(status, get_tree, get_object, run_python, document, export) to inspect and change the open model. "
    "Bundle related changes into one run_python call: each call is one undo step. Keep replies short; "
    "the panel is narrow. Each message starts with a [FreeCAD] line giving the active document and selection."
)


def find_claude() -> str | None:
    cfg = paths.load_config()
    for candidate in (cfg.get("claude_path"), os.path.expanduser("~/.local/bin/claude"),
                      "/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


class ClaudeRun(QtCore.QObject):
    event = QtCore.Signal(object)   # parsed stream events (dicts)
    finished = QtCore.Signal(int)   # exit code
    failed = QtCore.Signal(str)     # could not start

    def __init__(self, prompt: str, session_id: str | None, mode: str, cwd: str,
                 load_plugin_dir: bool = False, parent: Any = None) -> None:
        super().__init__(parent)
        self.prompt, self.session_id, self.mode, self.cwd = prompt, session_id, mode, cwd
        self.load_plugin_dir = load_plugin_dir
        self.parser = StreamParser()
        self.proc: QtCore.QProcess | None = None
        self.started_at = 0.0
        self.stopping = False

    def start(self) -> None:
        claude = find_claude()
        if not claude:
            self.failed.emit("Claude Code not found. Run /freecad:setup in Claude Code, which records its path.")
            return
        env = clean_child_env(dict(os.environ), App.getHomePath(),
                              os.path.join(App.getUserAppDataDir(), "Mod"))
        env.pop("ANTHROPIC_API_KEY", None)
        if self.mode == "api_key":
            key = secrets.get_api_key()
            if not key:
                self.failed.emit("API key mode is selected but no key is stored. Use ⚙ → Set API key.")
                return
            env["ANTHROPIC_API_KEY"] = key
        plugin_dir = paths.load_config().get("plugin_root") if self.load_plugin_dir else None
        args = build_args(self.prompt, self.session_id, plugin_dir) + ["--append-system-prompt", SYSTEM_PROMPT]
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
        pid = self.proc.processId()
        try:
            os.kill(pid, signal.SIGINT)
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
