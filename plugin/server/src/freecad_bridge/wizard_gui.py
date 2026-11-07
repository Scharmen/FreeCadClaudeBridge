"""The setup wizard in a window: a checklist of the steps, download buttons, questions as dialogs.

It runs the same `wizard.run_wizard` as the terminal version, in a worker thread, through a
`GuiSystem` that turns its output into events for the window. Tkinter is imported only for the
window itself, so everything else is unit-tested without a display.
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import __version__, wizard

STEPS = {
    "freecad": "FreeCAD 1.0 or newer",
    "desktop": "Claude Desktop",
    "claude_code": "Claude Code (the `claude` command)",
    "login": "Signed in to Claude",
    "plugin": "FreeCAD plugin in Claude",
    "addon": "Claude panel in FreeCAD",
    "start": "Start FreeCAD",
}
ICONS = {"waiting": ("○", "#888888"), "running": ("◐", "#c77700"), "ok": ("✓", "#1e9e4a"),
         "missing": ("✗", "#cc2b2b"), "info": ("i", "#3b6fd6")}
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
URL = re.compile(r"https?://[^\s'\"<>]+")


@dataclass
class Row:
    key: str
    text: str
    state: str = "waiting"
    link: str | None = None


class Checklist:
    """The steps and their current state, as the window shows them."""

    def __init__(self) -> None:
        self.rows = [Row(k, t) for k, t in STEPS.items()]

    def get(self, key: str) -> Row:
        return next(r for r in self.rows if r.key == key)

    def apply(self, key: str, state: str, text: str, link: str | None = None) -> Row | None:
        if key not in STEPS:
            return None
        row = self.get(key)
        row.state, row.text, row.link = state, text or STEPS[key], link
        return row

    def missing(self) -> list[Row]:
        return [r for r in self.rows if r.state == "missing"]

    def reset(self) -> None:
        for r in self.rows:
            r.state, r.text, r.link = "waiting", STEPS[r.key], None


class GuiSystem(wizard.System):
    """The wizard's machine, reporting to the window through a queue of (kind, payload) events."""

    def __init__(self, events: queue.Queue[tuple[str, Any]], asker: Callable[[str, bool], bool]) -> None:
        super().__init__()
        self.color = False
        self.events = events
        self.asker = asker
        self._input: queue.Queue[str] = queue.Queue()

    def say(self, text: str = "") -> None:
        for line in text.splitlines() or [""]:
            self.events.put(("log", line))

    def step(self, key: str, state: str, text: str, link: str | None = None) -> None:
        self.events.put(("step", (key, state, text, link)))

    def ask(self, question: str, default: bool = True) -> bool:
        return self.asker(question, default)

    def send_input(self, text: str) -> None:
        """A line the user typed into the window for the running program (e.g. a sign-in code)."""
        self._input.put(text)

    def run(self, args: list[str], interactive: bool = False, timeout: int = 600) -> tuple[int, str]:
        if not interactive:
            return super().run(args, interactive, timeout)
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE_") and k != "CLAUDECODE"}
        self.events.put(("log", "$ " + " ".join(args)))
        try:
            proc = subprocess.Popen(args, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)
        except OSError as e:
            self.events.put(("log", str(e)))
            return 1, str(e)
        assert proc.stdin is not None and proc.stdout is not None
        chunks: queue.Queue[bytes] = queue.Queue()
        stdout, stdin = proc.stdout, proc.stdin

        def read() -> None:
            while True:
                data = os.read(stdout.fileno(), 4096)
                chunks.put(data)
                if not data:
                    return

        def write() -> None:
            while proc.poll() is None:
                try:
                    line = self._input.get(timeout=0.2)
                except queue.Empty:
                    continue
                try:
                    stdin.write((line + "\n").encode())
                    stdin.flush()
                except OSError:
                    return

        threading.Thread(target=read, daemon=True).start()
        threading.Thread(target=write, daemon=True).start()
        self.events.put(("input", True))
        out, buf, deadline = [], "", time.monotonic() + timeout
        try:
            while True:
                try:
                    data = chunks.get(timeout=0.3)
                except queue.Empty:
                    data = None
                    if buf:                       # a prompt without a newline: show it anyway
                        self._line(buf, out)
                        buf = ""
                    if time.monotonic() > deadline:
                        proc.kill()
                        self._line("(stopped: took too long)", out)
                        break
                    continue
                if not data:
                    break
                buf += ANSI.sub("", data.decode("utf-8", "replace")).replace("\r\n", "\n").replace("\r", "\n")
                *lines, buf = buf.split("\n")
                for line in lines:
                    self._line(line, out)
            if buf:
                self._line(buf, out)
            code = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = 1
        finally:
            self.events.put(("input", False))
        return code, "\n".join(out)

    def _line(self, line: str, out: list[str]) -> None:
        out.append(line)
        self.events.put(("log", line))


def available() -> bool:
    """Whether a window can be opened here (Tk present, and a display on Linux)."""
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        import tkinter

        root = tkinter.Tk()
        root.destroy()
        return True
    except Exception:          # no Tk in this Python, or no display
        return False


class App:  # pragma: no cover - the window itself is checked by hand (see docs/manual-testing.md)
    def __init__(self, freecad: str | None = None) -> None:
        import tkinter as tk
        from tkinter import font, messagebox, ttk

        self.tk, self.ttk, self.messagebox = tk, ttk, messagebox
        self.freecad = freecad
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.checklist = Checklist()
        self.system: GuiSystem | None = None
        self.running = False
        self.rc: int | None = None

        root = self.root = tk.Tk()
        root.title("FreeCAD × Claude – Setup")
        root.geometry("660x600")
        root.minsize(520, 460)
        base = font.nametofont("TkDefaultFont")
        size = base.cget("size")
        size = size if size > 0 else 12
        self.f_title = base.copy()
        self.f_title.configure(size=size + 8, weight="bold")
        self.f_row = base.copy()
        self.f_row.configure(size=size + 1)
        self.f_icon = base.copy()
        self.f_icon.configure(size=size + 4, weight="bold")

        outer = ttk.Frame(root, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="FreeCAD × Claude", font=self.f_title).pack(anchor="w")
        ttk.Label(outer, text=f"Sets up the Claude panel in FreeCAD and the FreeCAD tools in Claude. "
                              f"Version {__version__}.", wraplength=600).pack(anchor="w", pady=(2, 14))

        rows = ttk.Frame(outer)
        rows.pack(fill="x")
        rows.columnconfigure(1, weight=1)
        self.widgets: dict[str, tuple[Any, Any, Any]] = {}
        for i, row in enumerate(self.checklist.rows):
            icon = tk.Label(rows, text="○", width=2, font=self.f_icon, fg=ICONS["waiting"][1])
            icon.grid(row=i, column=0, sticky="w", pady=3)
            text = ttk.Label(rows, text=row.text, font=self.f_row, wraplength=440)
            text.grid(row=i, column=1, sticky="w", padx=(6, 6))
            link = ttk.Button(rows, text="Download", command=lambda k=row.key: self._open(k))
            link.grid(row=i, column=2, sticky="e")
            link.grid_remove()
            self.widgets[row.key] = (icon, text, link)

        self.status = ttk.Label(outer, text="", wraplength=600, font=self.f_row)
        self.status.pack(anchor="w", pady=(14, 6))

        self.show_details = tk.BooleanVar(value=False)
        ttk.Checkbutton(outer, text="Show details", variable=self.show_details,
                        command=self._toggle_details).pack(anchor="w")
        self.details = ttk.Frame(outer)
        self.log = tk.Text(self.details, height=10, wrap="word", relief="flat", borderwidth=1)
        scroll = ttk.Scrollbar(self.details, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set, state="disabled")
        self.log.tag_configure("url", foreground="#3b6fd6", underline=True)
        self.log.tag_bind("url", "<Button-1>", self._click_url)
        self.log.tag_bind("url", "<Enter>", lambda _e: self.log.configure(cursor="hand2"))
        self.log.tag_bind("url", "<Leave>", lambda _e: self.log.configure(cursor=""))
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.input_row = ttk.Frame(outer)
        ttk.Label(self.input_row, text="The program asks for input (e.g. a code from the browser):").pack(
            anchor="w")
        entry_row = ttk.Frame(self.input_row)
        entry_row.pack(fill="x", pady=(2, 0))
        self.entry = ttk.Entry(entry_row)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda _e: self._send())
        ttk.Button(entry_row, text="Send", command=self._send).pack(side="left", padx=(6, 0))

        buttons = ttk.Frame(outer)
        buttons.pack(side="bottom", fill="x", pady=(12, 0))
        self.close_btn = ttk.Button(buttons, text="Close", command=root.destroy)
        self.close_btn.pack(side="right")
        self.main_btn = ttk.Button(buttons, text="Check and install", command=self.start)
        self.main_btn.pack(side="right", padx=(0, 8))
        ttk.Button(buttons, text="Help", command=lambda: webbrowser.open(wizard.LINKS["help"])).pack(side="left")

        root.after(50, self._poll)
        root.after(300, self.start)

    # ----- worker

    def start(self) -> None:
        if self.running:
            return
        self.running, self.rc = True, None
        self.checklist.reset()
        for row in self.checklist.rows:
            self._paint(row)
        self.main_btn.configure(state="disabled")
        self.status.configure(text="Checking what is needed …")
        system = GuiSystem(self.events, self._ask_from_worker)
        self.system = system

        def work() -> None:
            try:
                rc = wizard.run_wizard(system, freecad=self.freecad)
            except Exception as e:          # show it instead of a silent, stuck window
                self.events.put(("log", f"Error: {e!r}"))
                rc = 2
            self.events.put(("done", rc))

        threading.Thread(target=work, daemon=True).start()

    def _ask_from_worker(self, question: str, default: bool) -> bool:
        answer: dict[str, bool] = {}
        done = threading.Event()
        self.events.put(("ask", (question, default, answer, done)))
        done.wait()
        return answer.get("yes", default)

    # ----- window side

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def _handle(self, kind: str, payload: Any) -> None:
        if kind == "log":
            self._log(payload)
        elif kind == "step":
            row = self.checklist.apply(*payload)
            if row:
                self._paint(row)
                if row.state == "running":
                    self.status.configure(text=row.text)
        elif kind == "ask":
            question, default, answer, done = payload
            answer["yes"] = self.messagebox.askyesno("FreeCAD × Claude", question, parent=self.root,
                                                     default="yes" if default else "no")
            done.set()
        elif kind == "input":
            if payload:
                if self.show_details.get():
                    self.input_row.pack(fill="x", pady=(6, 0), before=self.details)
                else:
                    self.input_row.pack(fill="x", pady=(6, 0))
                if not self.show_details.get():
                    self.show_details.set(True)
                    self._toggle_details()
                self.entry.focus_set()
            else:
                self.input_row.pack_forget()
        elif kind == "done":
            self._finish(int(payload))

    def _finish(self, rc: int) -> None:
        self.running, self.rc = False, rc
        self.main_btn.configure(state="normal")
        missing = self.checklist.missing()
        if rc == 0:
            self.status.configure(text="Done. In FreeCAD the 'Claude' panel is on the right: type what you want to "
                                       "build. In Claude Desktop (Code) or Claude Code just ask Claude to work in "
                                       "FreeCAD.")
            self.main_btn.configure(text="Run again")
            self.close_btn.configure(text="Finish")
        elif missing:
            self.status.configure(text="Please install what is marked ✗ (Download opens the page), then press "
                                       "Check again.")
            self.main_btn.configure(text="Check again")
        else:
            self.status.configure(text="Setup did not finish; see the details below.")
            self.main_btn.configure(text="Try again")
            if not self.show_details.get():
                self.show_details.set(True)
                self._toggle_details()

    def _paint(self, row: Row) -> None:
        icon, text, link = self.widgets[row.key]
        symbol, color = ICONS.get(row.state, ICONS["waiting"])
        icon.configure(text=symbol, fg=color)
        text.configure(text=row.text)
        if row.link:
            link.configure(text="Download" if row.key in ("freecad", "desktop", "claude_code") else "Open")
            link.grid()
        else:
            link.grid_remove()

    def _open(self, key: str) -> None:
        link = self.checklist.get(key).link
        if link:
            webbrowser.open(link)

    def _log(self, line: str) -> None:
        self.log.configure(state="normal")
        start = self.log.index("end-1c")
        self.log.insert("end", line + "\n")
        for m in URL.finditer(line):
            self.log.tag_add("url", f"{start}+{m.start()}c", f"{start}+{m.end()}c")
        self.log.configure(state="disabled")
        self.log.see("end")

    def _click_url(self, event: Any) -> None:
        index = self.log.index(f"@{event.x},{event.y}")
        for start, end in zip(*[iter(self.log.tag_ranges("url"))] * 2, strict=False):
            if self.log.compare(start, "<=", index) and self.log.compare(index, "<", end):
                webbrowser.open(self.log.get(start, end))
                return

    def _toggle_details(self) -> None:
        if self.show_details.get():
            self.details.pack(fill="both", expand=True, pady=(4, 0))
        else:
            self.details.pack_forget()

    def _send(self) -> None:
        text = self.entry.get()
        if self.system is not None and text:
            self.system.send_input(text)
            self._log("> " + ("*" * len(text) if len(text) > 12 else text))
            self.entry.delete(0, "end")

    def run(self) -> int:
        self.root.mainloop()
        return self.rc if self.rc is not None else 1


def main(freecad: str | None = None) -> int:  # pragma: no cover
    return App(freecad).run()
