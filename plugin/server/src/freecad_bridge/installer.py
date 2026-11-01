"""Find FreeCAD, install/uninstall the ClaudeBridge addon, write config and token.

Detection: macOS (FreeCAD.app), Windows (registry, Program Files, portable via
--freecad), Linux (PATH, AppImages in the usual folders). `--freecad` accepts a
FreeCAD.app, an install folder, a freecadcmd binary or an AppImage on any platform.
FreeCAD itself reports its user directory, so versioned folders (v1-1) just work.
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
    command: list[str]  # how to start freecadcmd, e.g. [".../freecadcmd"] or ["FreeCAD.AppImage", "freecadcmd"]
    version: str
    user_app_data: Path
    app: Path | None = None

    @property
    def freecadcmd(self) -> str:
        return " ".join(self.command)

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


CMD_NAMES = ("freecadcmd", "FreeCADCmd", "freecadcmd.exe", "FreeCADCmd.exe")


def command_for(path: Path) -> list[str] | None:
    """Resolve a FreeCAD.app, install folder, bin folder, freecadcmd binary or AppImage to a command."""
    path = path.expanduser()
    if path.is_file() and (path.suffix.lower() == ".appimage" or path.name == "AppRun"):
        return [str(path), "freecadcmd"]
    if path.is_dir() and (path / "AppRun").is_file():  # extracted AppImage (squashfs-root)
        return [str(path / "AppRun"), "freecadcmd"]
    if path.is_file():
        return [str(path)] if os.access(path, os.X_OK) or path.suffix.lower() == ".exe" else None
    for sub in ("Contents/Resources/bin", "bin", ""):
        for name in CMD_NAMES:
            c = path / sub / name if sub else path / name
            if c.is_file() and (os.access(c, os.X_OK) or c.suffix.lower() == ".exe"):
                return [str(c)]
    return None


def freecadcmd_for(path: Path) -> Path | None:
    """Single-binary form of command_for (None for AppImages)."""
    cmd = command_for(path)
    return Path(cmd[0]) if cmd and len(cmd) == 1 else None


def _mac_candidates() -> list[Path]:
    found: list[Path] = []
    for base in (Path("/Applications"), Path.home() / "Applications"):
        if base.is_dir():
            found += sorted(base.glob("FreeCAD*.app"))
    if shutil.which("mdfind"):
        try:
            out = subprocess.run(["mdfind", "kMDItemFSName == 'FreeCAD*.app'"], capture_output=True,
                                 text=True, timeout=5).stdout
            found += [Path(p) for p in out.splitlines() if p.endswith(".app")]
        except (OSError, subprocess.TimeoutExpired):
            pass
    return found


def _windows_registry_locations() -> list[Path]:
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return []
    out: list[Path] = []
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                root = winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | view)
            except OSError:
                continue
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    sub = winreg.OpenKey(root, winreg.EnumKey(root, i))
                    name = str(winreg.QueryValueEx(sub, "DisplayName")[0])
                    if name.lower().startswith("freecad"):
                        loc = winreg.QueryValueEx(sub, "InstallLocation")[0]
                        if loc:
                            out.append(Path(loc))
                except OSError:
                    continue
    return out


def _windows_candidates() -> list[Path]:
    found = _windows_registry_locations()
    for env in ("ProgramFiles", "ProgramW6432", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if not base:
            continue
        for root in (Path(base), Path(base) / "Programs"):
            if root.is_dir():
                found += sorted(root.glob("FreeCAD*"))
    return found


def _linux_candidates() -> list[Path]:
    found: list[Path] = []
    for name in ("freecadcmd", "FreeCADCmd", "freecad-cmd"):
        hit = shutil.which(name)
        if hit:
            found.append(Path(hit))
    home = Path.home()
    for folder in (home / "Applications", home / "Downloads", home / "Desktop", home / ".local/bin", home / "bin",
                   Path("/opt")):
        if folder.is_dir():
            found += sorted(folder.glob("FreeCAD*.AppImage")) + sorted(folder.glob("freecad*.AppImage"))
            found += sorted(p for p in folder.glob("FreeCAD*") if p.is_dir())
    return found


def candidate_apps() -> list[Path]:
    """Places FreeCAD may be installed on this platform (unverified)."""
    if sys.platform == "darwin":
        found = _mac_candidates()
    elif sys.platform == "win32":
        found = _windows_candidates()
    else:
        found = _linux_candidates()
    unique: list[Path] = []
    for p in found:
        try:
            key = p.resolve()
        except OSError:
            key = p
        if key not in [u.resolve() for u in unique]:
            unique.append(p)
    return unique


def probe(command: list[str] | Path, timeout: float = 120) -> FreeCADInstall:
    cmd = [str(command)] if isinstance(command, Path) else list(command)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(PROBE_SCRIPT)
        script = f.name
    try:
        proc = subprocess.run([*cmd, script], capture_output=True, text=True, timeout=timeout,
                              env=platforms.clean_env(), stdin=subprocess.DEVNULL)
    finally:
        os.unlink(script)
    data = parse_probe(proc.stdout)
    if "version" not in data or "userappdata" not in data:
        raise RuntimeError(f"could not query {' '.join(cmd)}: {proc.stdout[-400:]}{proc.stderr[-400:]}")
    app = next((p for p in Path(cmd[0]).parents if p.suffix == ".app"), None)
    return FreeCADInstall(cmd, data["version"], Path(data["userappdata"]), app)


def discover(explicit: str | None = None) -> list[FreeCADInstall]:
    paths = [Path(explicit)] if explicit else candidate_apps()
    installs: list[FreeCADInstall] = []
    for p in paths:
        cmd = command_for(p)
        if cmd is None:
            continue
        try:
            inst = probe(cmd)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            print(f"warning: skipping {p}: {e}", file=sys.stderr)
            continue
        if all(i.user_app_data != inst.user_app_data or i.version != inst.version for i in installs):
            installs.append(inst)
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
        out(f"note: also found FreeCAD {other.version} ({other.freecadcmd}); use --freecad to pick it.")
    backup = copy_addon(src, target.mod_dir)
    platforms.ensure_token()
    platforms.instances_dir().mkdir(parents=True, exist_ok=True)
    claude = find_claude()
    platforms.update_config(claude_path=claude, plugin_root=str(src.parent.parent), addon_src=str(src),
                            freecad_version=target.version, freecadcmd=target.freecadcmd,
                            freecadcmd_cmd=target.command,
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
