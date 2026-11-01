"""Find FreeCAD, install/uninstall the ClaudeBridge addon, write config and token.

Phase 1: macOS detection only (Windows/Linux come in Phase 3); `--freecad`
accepts an explicit FreeCAD.app or freecadcmd path on any platform.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import __version__, platforms
from .client import list_instances

ADDON_NAME = "ClaudeBridge"
INSTALLED_MARKER = ".installed.json"

PROBE_SCRIPT = """\
import FreeCAD as App
print("FCB_VERSION=" + ".".join(App.Version()[:3]))
print("FCB_USERAPPDATA=" + App.getUserAppDataDir())
print("FCB_HOME=" + App.getHomePath())
"""


@dataclass
class FreeCADInstall:
    freecadcmd: Path
    version: str
    user_app_data: Path
    app: Path | None = None

    @property
    def version_tuple(self) -> tuple[int, ...]:
        return tuple(int(x) for x in re.findall(r"\d+", self.version)[:3])

    @property
    def mod_dir(self) -> Path:
        return self.user_app_data / "Mod"


def parse_probe(stdout: str) -> dict[str, str]:
    """Marker lines from the probe script; freecadcmd prints a license banner around them."""
    out = {}
    for line in stdout.splitlines():
        m = re.match(r"^FCB_([A-Z]+)=(.*)$", line.strip())
        if m:
            out[m.group(1).lower()] = m.group(2)
    return out


def freecadcmd_for(path: Path) -> Path | None:
    """Resolve a FreeCAD.app, a bin directory or a freecadcmd binary to freecadcmd."""
    path = path.expanduser()
    candidates = [path] if path.is_file() else [
        path / "Contents/Resources/bin/freecadcmd",
        path / "bin/freecadcmd", path / "bin/FreeCADCmd", path / "bin/freecadcmd.exe",
        path / "freecadcmd", path / "FreeCADCmd",
    ]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c
    return None


def candidate_apps() -> list[Path]:
    """macOS: FreeCAD*.app in /Applications, ~/Applications, plus Spotlight."""
    found: list[Path] = []
    for base in (Path("/Applications"), Path.home() / "Applications"):
        if base.is_dir():
            found += sorted(base.glob("FreeCAD*.app"))
    if sys.platform == "darwin" and shutil.which("mdfind"):
        try:
            out = subprocess.run(["mdfind", "kMDItemFSName == 'FreeCAD*.app'"], capture_output=True,
                                 text=True, timeout=5).stdout
            found += [Path(p) for p in out.splitlines() if p.endswith(".app")]
        except (OSError, subprocess.TimeoutExpired):
            pass
    unique: list[Path] = []
    for p in found:
        if p.resolve() not in [u.resolve() for u in unique]:
            unique.append(p)
    return unique


def probe(freecadcmd: Path, timeout: float = 60) -> FreeCADInstall:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(PROBE_SCRIPT)
        script = f.name
    try:
        proc = subprocess.run([str(freecadcmd), script], capture_output=True, text=True,
                              timeout=timeout, env=platforms.clean_env(), stdin=subprocess.DEVNULL)
    finally:
        os.unlink(script)
    data = parse_probe(proc.stdout)
    if "version" not in data or "userappdata" not in data:
        raise RuntimeError(f"could not query {freecadcmd}: {proc.stdout[-400:]}{proc.stderr[-400:]}")
    app = next((p for p in freecadcmd.parents if p.suffix == ".app"), None)
    return FreeCADInstall(freecadcmd, data["version"], Path(data["userappdata"]), app)


def discover(explicit: str | None = None) -> list[FreeCADInstall]:
    paths = [Path(explicit)] if explicit else candidate_apps()
    installs = []
    for p in paths:
        cmd = freecadcmd_for(p)
        if cmd is None:
            continue
        try:
            installs.append(probe(cmd))
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            print(f"warning: skipping {p}: {e}", file=sys.stderr)
    return sorted(installs, key=lambda i: i.version_tuple, reverse=True)


def installed_version(mod_dir: Path) -> str | None:
    try:
        return json.loads((mod_dir / ADDON_NAME / INSTALLED_MARKER).read_text()).get("version")
    except (OSError, ValueError):
        return None


def copy_addon(addon_src: Path, mod_dir: Path) -> Path | None:
    """Copy the addon into mod_dir; back up an existing copy outside Mod. Returns the backup path."""
    dest = mod_dir / ADDON_NAME
    backup = None
    if dest.exists():
        old = installed_version(mod_dir) or "unknown"
        backup = platforms.bridge_dir() / "backups" / f"{ADDON_NAME}-{old}-{time.strftime('%Y%m%d-%H%M%S')}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dest), str(backup))
    mod_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(addon_src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (dest / INSTALLED_MARKER).write_text(json.dumps(
        {"version": __version__, "source": str(addon_src), "installed_at": time.time()}, indent=2))
    return backup


def find_claude() -> str | None:
    path = shutil.which("claude")
    if path:
        return path
    for p in (Path.home() / ".local/bin/claude", Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")):
        if p.is_file():
            return str(p)
    return None


def install(addon_src: str | None, freecad: str | None = None, out=print) -> int:
    src = Path(addon_src or os.environ.get("FREECAD_BRIDGE_ADDON_SRC", "")).expanduser()
    if not (src / "InitGui.py").is_file():
        out(f"error: addon source not found at '{src}'. Pass --addon-src <plugin>/addon/ClaudeBridge.")
        return 2
    installs = discover(freecad)
    if not installs:
        out("error: no FreeCAD installation found. Install FreeCAD 1.1 or pass --freecad <path to FreeCAD.app>.")
        return 1
    target = installs[0]
    for other in installs[1:]:
        out(f"note: also found FreeCAD {other.version} at {other.freecadcmd}; use --freecad to pick it.")
    backup = copy_addon(src, target.mod_dir)
    platforms.ensure_token()
    platforms.instances_dir().mkdir(parents=True, exist_ok=True)
    claude = find_claude()
    platforms.update_config(claude_path=claude, plugin_root=str(src.parent.parent), addon_src=str(src),
                            freecad_version=target.version, freecadcmd=str(target.freecadcmd),
                            user_app_data=str(target.user_app_data), server_version=__version__)
    out(f"Installed ClaudeBridge {__version__} for FreeCAD {target.version}:")
    out(f"  addon:  {target.mod_dir / ADDON_NAME}")
    if backup:
        out(f"  backup of the previous addon: {backup}")
    out(f"  bridge: {platforms.bridge_dir()}")
    out(f"  claude: {claude or 'NOT FOUND - the chat panel in FreeCAD needs Claude Code'}")
    if list_instances():
        out("FreeCAD is running: restart it to load the new addon.")
    else:
        out("Start FreeCAD; the 'Claude' chat panel appears docked on the right.")
    return 0


def uninstall(freecad: str | None = None, out=print) -> int:
    cfg = platforms.load_config()
    mod_dirs = {Path(cfg["user_app_data"]) / "Mod"} if cfg.get("user_app_data") else set()
    if freecad or not mod_dirs:
        mod_dirs |= {i.mod_dir for i in discover(freecad)}
    for mod in mod_dirs:
        dest = mod / ADDON_NAME
        if dest.exists():
            shutil.rmtree(dest)
            out(f"removed {dest}")
    bd = platforms.bridge_dir()
    for p in [platforms.token_path(), platforms.config_path(), *bd.glob("bridge-*.sock")]:
        if p.exists():
            p.unlink()
            out(f"removed {p}")
    if platforms.instances_dir().exists():
        shutil.rmtree(platforms.instances_dir())
        out(f"removed {platforms.instances_dir()}")
    out("Now run: /plugin uninstall freecad@freecad-claude")
    return 0
