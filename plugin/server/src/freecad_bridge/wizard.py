"""One-step setup: check what is needed, point to downloads for what is missing, install the rest.

Started by the one-line installers (install.sh / install.ps1) as `freecad-bridge wizard`:

1. FreeCAD 1.0 or newer               missing → freecad.org download page
2. Claude Desktop (macOS, Windows)     missing → claude.ai/download
3. Claude Code (the `claude` command)  missing → offer the official installer, else the page
4. Signed in to Claude                 not yet → offer `claude auth login` (browser)
5. Plugin `freecad` in Claude Code     marketplace add + install (or update)
6. Addon ClaudeBridge in FreeCAD       copied into FreeCAD's Mod folder
7. Start (or restart) FreeCAD

Everything with side effects goes through `System`, so the flow is unit-tested.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import installer
from .client import list_instances

MARKETPLACE = "Scharmen/FreeCadClaudeBridge"
MARKETPLACE_NAME = "freecad-claude"
PLUGIN = "freecad@freecad-claude"
MIN_FREECAD = (1, 0)
LINKS = {
    "freecad": "https://www.freecad.org/downloads.php",
    "desktop": "https://claude.ai/download",
    "claude_code": "https://claude.com/claude-code",
    "help": "https://github.com/Scharmen/FreeCadClaudeBridge#install",
}
CLAUDE_CODE_INSTALL = {
    "win32": ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
              "irm https://claude.ai/install.ps1 | iex"],
    "posix": ["/bin/bash", "-c", "curl -fsSL https://claude.ai/install.sh | bash"],
}


def find_claude_desktop(platform: str, env: dict[str, str], app_dirs: list[Path]) -> str | None:
    """The Claude Desktop app, if installed (there is none for Linux)."""
    if platform == "darwin":
        for d in app_dirs:
            if (Path(d) / "Claude.app").is_dir():
                return str(Path(d) / "Claude.app")
        return None
    if platform == "win32":
        local = Path(env.get("LOCALAPPDATA", ""))
        for c in (local / "AnthropicClaude" / "claude.exe", local / "Programs" / "Claude" / "Claude.exe",
                  Path(env.get("ProgramFiles", r"C:\Program Files")) / "Claude" / "Claude.exe"):
            if c.is_file():
                return str(c)
    return None


class System:
    """The real machine: terminal I/O, programs, browser."""

    def __init__(self) -> None:
        self.platform = sys.platform
        self.color = sys.stdout.isatty() and os.name != "nt"

    def say(self, text: str = "") -> None:
        try:
            print(text, flush=True)
        except UnicodeEncodeError:              # a console without UTF-8 (older Windows terminals)
            print(text.encode("ascii", "replace").decode(), flush=True)

    def ask(self, question: str, default: bool = True) -> bool:
        hint = "[Y/n]" if default else "[y/N]"
        while True:
            try:
                answer = input(f"  {question} {hint} ").strip().lower()
            except EOFError:
                return default
            if not answer:
                return default
            if answer in ("y", "yes", "j", "ja"):
                return True
            if answer in ("n", "no", "nein"):
                return False

    def open_url(self, url: str) -> None:
        webbrowser.open(url)

    def step(self, key: str, state: str, text: str, link: str | None = None) -> None:
        """A step's state for a checklist (running, ok, missing, info); the terminal shows `say` lines."""

    def claude_desktop(self) -> str | None:
        return find_claude_desktop(self.platform, dict(os.environ),
                                   [Path("/Applications"), Path.home() / "Applications"])

    def claude_cli(self) -> str | None:
        found = installer.find_claude()
        if found:
            return found
        exe = Path.home() / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")
        return str(exe) if exe.is_file() else None

    def run(self, args: list[str], interactive: bool = False, timeout: int = 600) -> tuple[int, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE_") and k != "CLAUDECODE"}
        try:
            if interactive:
                return subprocess.run(args, env=env, timeout=timeout).returncode, ""
            proc = subprocess.run(args, env=env, timeout=timeout, capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL)
            return proc.returncode, proc.stdout + proc.stderr
        except (OSError, subprocess.TimeoutExpired) as e:
            return 1, str(e)


def _json(text: str) -> Any:
    """The JSON part of a command's output (Claude Code may print notices before it)."""
    for start in (text.find("["), text.find("{")):
        if start >= 0:
            try:
                return json.loads(text[start:])
            except ValueError:
                continue
    return None


def auth_status(system: System, claude: str) -> dict[str, Any]:
    code, out = system.run([claude, "auth", "status", "--json"], timeout=60)
    data = _json(out)
    return data if isinstance(data, dict) else {}


def plugin_entry(system: System, claude: str) -> dict[str, Any] | None:
    _, out = system.run([claude, "plugin", "list", "--json"], timeout=120)
    data = _json(out)
    return next((p for p in data or [] if isinstance(p, dict) and p.get("id") == PLUGIN), None)


def addon_source(entry: dict[str, Any]) -> Path:
    """Where the installed plugin keeps the FreeCAD addon. A plugin read from a local folder (a folder
    marketplace) lives there; its installPath is only a stale cache then."""
    folder = entry.get("readFromFolder")
    if folder and (Path(folder) / "addon" / "ClaudeBridge").is_dir():
        return Path(folder) / "addon" / "ClaudeBridge"
    return Path(entry.get("installPath", "")) / "addon" / "ClaudeBridge"


def start_freecad_default(inst: installer.FreeCADInstall) -> None:
    """Open FreeCAD's GUI next to the freecadcmd that was found."""
    cmd = Path(inst.command[0])
    if inst.app is not None and sys.platform == "darwin":
        args = ["open", "-a", str(inst.app)]
    elif len(inst.command) > 1:                       # AppImage: run it without the freecadcmd argument
        args = [str(cmd)]
    else:
        gui = next((cmd.parent / n for n in ("freecad", "FreeCAD", "freecad.exe", "FreeCAD.exe")
                    if (cmd.parent / n).is_file()), None)
        if gui is None:
            return
        args = [str(gui)]
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def run_wizard(system: System, discover: Callable[..., list[installer.FreeCADInstall]] = installer.discover,
               install_addon: Callable[..., int] = installer.install,
               start_freecad: Callable[[installer.FreeCADInstall], None] = start_freecad_default,
               freecad_running: Callable[[], bool] = lambda: bool(list_instances()),
               yes: bool = False, freecad: str | None = None) -> int:
    ok, bad, arrow = ("\033[32m✓\033[0m", "\033[31m✗\033[0m", "→") if getattr(system, "color", False) \
        else ("✓", "✗", "→")
    missing: list[tuple[str, str]] = []

    def confirm(question: str, default: bool, when_yes: bool) -> bool:
        """--yes takes `when_yes` without asking (installs yes, opening pages or starting FreeCAD no)."""
        return when_yes if yes else system.ask(question, default)

    def fail(text: str, link: str, key: str = "") -> None:
        system.say(f"{bad} {text}\n    {arrow} {link}")
        missing.append((text, link))
        if key:
            system.step(key, "missing", text, link)

    def good(key: str, text: str) -> None:
        system.say(f"{ok} {text}")
        system.step(key, "ok", text)

    system.say("FreeCAD × Claude – setup\n")
    system.say("Checking what is needed …")

    # 1. FreeCAD
    system.step("freecad", "running", "Looking for FreeCAD …")
    installs = discover(freecad)
    newest = installs[0] if installs else None
    if newest is None:
        fail("FreeCAD is not installed (or not found). Install FreeCAD 1.0 or newer.", LINKS["freecad"], "freecad")
    elif newest.version_tuple[:2] < MIN_FREECAD:
        fail(f"FreeCAD {newest.version} is too old. Install FreeCAD 1.0 or newer.", LINKS["freecad"], "freecad")
        newest = None
    else:
        good("freecad", f"FreeCAD {newest.version}")

    # 2. Claude Desktop
    if system.platform.startswith("linux"):
        good("desktop", "Claude Desktop is not available for Linux; Claude Code alone is fine")
    elif system.claude_desktop():
        good("desktop", "Claude Desktop")
    else:
        fail("Claude Desktop is not installed. Install it and sign in.", LINKS["desktop"], "desktop")

    # 3. Claude Code (the `claude` command the FreeCAD chat panel and the plugin use)
    claude = system.claude_cli()
    if not claude:
        system.say(f"{bad} Claude Code (the `claude` command) is missing")
        system.step("claude_code", "running", "Claude Code (the `claude` command) is missing")
        if confirm("Install Claude Code now with the official installer from claude.ai?", True, True):
            key = "win32" if system.platform == "win32" else "posix"
            system.step("claude_code", "running", "Installing Claude Code …")
            system.run(CLAUDE_CODE_INSTALL[key], interactive=True)
            claude = system.claude_cli()
        if claude:
            good("claude_code", "Claude Code installed")
        else:
            fail("Claude Code is not installed.", LINKS["claude_code"], "claude_code")
    else:
        good("claude_code", "Claude Code")

    # 4. Signed in
    if claude:
        system.step("login", "running", "Checking the Claude sign-in …")
        status = auth_status(system, claude)
        if not status.get("loggedIn"):
            system.say(f"{bad} Not signed in to Claude")
            system.step("login", "running", "Not signed in to Claude")
            if confirm("Sign in now? A browser window opens; use the same account as in Claude Desktop.",
                       True, True):
                system.run([claude, "auth", "login"], interactive=True)
                status = auth_status(system, claude)
        if status.get("loggedIn"):
            plan = status.get("subscriptionType") or status.get("authMethod") or ""
            good("login", "Logged in to Claude" + (f" ({plan})" if plan else ""))
        else:
            fail("Not signed in to Claude. Run `claude auth login` or sign in when Claude asks.",
                 LINKS["claude_code"], "login")
    else:
        system.step("login", "missing", "Needs Claude Code first", LINKS["claude_code"])

    if missing:
        system.say("\nPlease install what is missing, then run the installer again:")
        for text, link in missing:
            system.say(f"  • {text}\n    {link}")
        if confirm(f"Open {missing[0][1]} in your browser now?", True, False):
            system.open_url(missing[0][1])
        return 1

    # 5. + 6. Install
    system.say("")
    if not confirm("Everything is ready. Install the FreeCAD plugin into Claude and the Claude panel into "
                   "FreeCAD?", True, True):
        system.say("Cancelled; nothing was changed.")
        return 1
    system.step("plugin", "running", "Installing the FreeCAD plugin into Claude …")
    _, out = system.run([claude, "plugin", "marketplace", "list", "--json"], timeout=120)
    names = {m.get("name") for m in _json(out) or [] if isinstance(m, dict)}
    if MARKETPLACE_NAME not in names:
        code, out = system.run([claude, "plugin", "marketplace", "add", MARKETPLACE], timeout=300)
        if code != 0:
            fail(f"Could not add the plugin marketplace: {out.strip()[-300:]}", LINKS["help"], "plugin")
            return 1
    entry = plugin_entry(system, claude)
    action = "update" if entry else "install"
    code, out = system.run([claude, "plugin", action, PLUGIN], timeout=600)
    entry = plugin_entry(system, claude)
    if not entry:
        fail(f"Could not {action} the Claude plugin: {out.strip()[-300:]}", LINKS["help"], "plugin")
        return 1
    good("plugin", f"Claude plugin {PLUGIN} {'updated' if action == 'update' else 'installed'}"
         + (f" ({entry.get('version')})" if entry.get("version") else ""))

    src = addon_source(entry)
    def addon_line(line: str) -> None:
        if not line.startswith(("FreeCAD is running", "Start FreeCAD")):   # the wizard says that itself
            system.say(f"    {line}")

    system.step("addon", "running", "Installing the Claude panel into FreeCAD …")
    rc = install_addon(str(src), freecad, addon_line)
    if rc != 0:
        fail("Could not install the Claude panel into FreeCAD.", LINKS["help"], "addon")
        return 1
    good("addon", f"Claude panel installed in FreeCAD {newest.version}")

    # 7. Start
    system.say("")
    if freecad_running():
        system.say(f"{arrow} FreeCAD is running: restart it to load the Claude panel.")
        system.step("start", "ok", "FreeCAD is running: restart it to load the Claude panel")
    elif confirm("Start FreeCAD now?", True, False):
        start_freecad(newest)
        system.step("start", "ok", "FreeCAD started")
    else:
        system.step("start", "ok", "Start FreeCAD whenever you like")
    system.say("\nDone. In FreeCAD the 'Claude' panel is on the right: type what you want to build.\n"
               "In Claude Desktop (Code) or Claude Code you can also just ask Claude to work in FreeCAD.")
    return 0


def main(yes: bool = False, freecad: str | None = None, gui: bool = False) -> int:
    if gui and not yes:
        from . import wizard_gui

        if wizard_gui.available():
            return wizard_gui.main(freecad)
        print("No window can be opened here (no display or no Tk); continuing in the terminal.")
    if not shutil.which("uvx") and not shutil.which("uv"):
        print("note: uv was not found on PATH; the Claude plugin needs it (https://docs.astral.sh/uv/).")
    return run_wizard(System(), yes=yes, freecad=freecad)
