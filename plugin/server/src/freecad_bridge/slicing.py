"""Slice a model with a slicer installed on this machine and read what the printer would do.

OrcaSlicer and its relatives (AnycubicSlicerNext, Bambu Studio) slice from the command line with their own
printer, process and filament profiles. The slicer's messages say what cannot print (floating regions,
empty layers, cantilevers); the G-code says how long it takes, how much filament it needs and how much of
it is overhang, bridge or support. Everything runs locally. Standard library only.
"""

from __future__ import annotations

import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any

ENV = "FREECAD_BRIDGE_SLICER"
NOISE = ("cli mode", "can not find key", "no filament colors", "exclude triangles", "Initializing StaticPrintConfigs")
SERIOUS = ("can't be printed", "cannot be printed", "empty layer", "floating regions", "outside the print volume",
           "collision", "is too close to", "exceeds the maximum build volume")


def candidates(platform: str = sys.platform) -> list[tuple[str, str]]:
    """(executable, profiles folder) of the slicers this module can drive, most specific first."""
    out: list[tuple[str, str]] = []
    if platform == "darwin":
        for app, exe in (("AnycubicSlicerNext", "AnycubicSlicerNext"), ("OrcaSlicer", "OrcaSlicer"),
                         ("BambuStudio", "BambuStudio")):
            for base in ("/Applications", os.path.expanduser("~/Applications")):
                root = f"{base}/{app}.app/Contents"
                out.append((f"{root}/MacOS/{exe}", f"{root}/Resources/profiles"))
    elif platform == "win32":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        for folder, exe in (("AnycubicSlicerNext", "AnycubicSlicerNext.exe"), ("OrcaSlicer", "orca-slicer.exe"),
                            ("Bambu Studio", "bambu-studio.exe")):
            out.append((os.path.join(pf, folder, exe), os.path.join(pf, folder, "resources", "profiles")))
    else:
        for exe, share in (("orca-slicer", "OrcaSlicer"), ("OrcaSlicer", "OrcaSlicer"),
                           ("bambu-studio", "BambuStudio")):
            path = shutil.which(exe)
            if path:
                real = os.path.dirname(os.path.realpath(path))
                for profiles in (os.path.join(real, "..", "resources", "profiles"), f"/usr/share/{share}/profiles",
                                 os.path.join(real, "resources", "profiles")):
                    out.append((path, os.path.normpath(profiles)))
    return out


def find_slicer() -> dict[str, str] | None:
    """The first installed slicer with profiles (see find_slicers)."""
    found = find_slicers()
    return found[0] if found else None


def pick(printer: str, nozzle: float, layer: float, material: str,
         slicers: list[dict[str, str]] | None = None) -> tuple[Any, Any, str]:
    """(slicer, profiles, "") for the first installed slicer that knows the printer, else (None, None, why)."""
    slicers = find_slicers() if slicers is None else slicers
    if not slicers:
        return None, None, ("no slicer found. Install AnycubicSlicerNext, OrcaSlicer or Bambu Studio, or set "
                            f"{ENV} to the slicer's executable")
    tried = []
    for slicer in slicers:
        profiles = find_profiles(slicer["profiles"], printer, nozzle, layer, material)
        if profiles:
            return slicer, profiles, ""
        packed = bool(glob.glob(os.path.join(slicer["profiles"], "*.opc"))) and \
            not glob.glob(os.path.join(slicer["profiles"], "*", "machine", "*.json"))
        reason = "its profiles are packed and cannot be read" if packed else "no such profile"
        tried.append(f"{slicer['name']} ({reason})")
    return None, None, (f"no profile for '{printer}' with a {nozzle:g} mm nozzle in: " + ", ".join(tried)
                        + ". Name the printer as the slicer does, e.g. 'Anycubic Kobra S1'")


def find_slicers() -> list[dict[str, str]]:
    """Installed slicers with a profiles folder, the one named in FREECAD_BRIDGE_SLICER (its executable) first."""
    forced = os.environ.get(ENV)
    pairs = candidates()
    if forced:
        root = os.path.dirname(os.path.dirname(forced))
        pairs = [(forced, p) for p in (os.path.join(root, "Resources", "profiles"),
                                       os.path.join(os.path.dirname(forced), "resources", "profiles"))] + pairs
    found: list[dict[str, str]] = []
    for exe, profiles in pairs:
        if os.path.isfile(exe) and os.path.isdir(profiles) and exe not in [f["exe"] for f in found]:
            found.append({"exe": exe, "profiles": profiles, "name": os.path.basename(exe).replace(".exe", "")})
    return found


def _norm(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _load(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _flatten(path: str, depth: int = 0) -> dict[str, Any]:
    """A profile with everything it inherits filled in (parents live in the same folder)."""
    data = _load(path)
    parent = data.get("inherits")
    if parent and depth < 12:
        parent_path = os.path.join(os.path.dirname(path), f"{parent}.json")
        if os.path.isfile(parent_path):
            data = {**_flatten(parent_path, depth + 1), **data}
    data.pop("inherits", None)
    data["from"] = "system"
    return data


def find_profiles(profiles_root: str, printer: str, nozzle: float = 0.4,
                  layer: float = 0.2, material: str = "PLA") -> dict[str, dict[str, Any]] | None:
    """Machine, process and filament profile of a printer, searched in every vendor folder; None if unknown."""
    want = _norm(printer.split("/")[0])
    noz = f"{nozzle:g} nozzle"
    machines = [p for p in glob.glob(os.path.join(profiles_root, "*", "machine", "*.json"))
                if want in _norm(os.path.basename(p)) and noz in os.path.basename(p)]
    if not machines:
        return None
    machine_path = min(machines, key=lambda p: len(os.path.basename(p)))
    vendor = os.path.dirname(os.path.dirname(machine_path))
    machine_name = os.path.basename(machine_path)[:-len(".json")]

    def fits(path: str) -> bool:
        name = os.path.basename(path)[:-len(".json")]
        if name.endswith("@" + machine_name):
            return True
        try:
            return machine_name in (_load(path).get("compatible_printers") or [])
        except (OSError, ValueError):
            return False

    processes = [p for p in glob.glob(os.path.join(vendor, "process", "*.json")) if fits(p)]
    filaments = [p for p in glob.glob(os.path.join(vendor, "filament", "*.json")) if fits(p)]
    if not processes or not filaments:
        return None
    layer_tag = f"{layer:.2f}mm"
    process = min(processes, key=lambda p: (layer_tag not in os.path.basename(p), "Standard" not in os.path.basename(p),
                                            len(os.path.basename(p))))
    mat = material.upper()
    filament = min(filaments, key=lambda p: (f" {mat} " not in f" {os.path.basename(p).upper()} ".replace("@", " @"),
                                             "HIGH SPEED" in os.path.basename(p).upper(),
                                             "SILK" in os.path.basename(p).upper(), len(os.path.basename(p))))
    return {"machine": _flatten(machine_path), "process": _flatten(process), "filament": _flatten(filament)}


def read_log(text: str) -> dict[str, list[str]]:
    """The slicer's own messages: what cannot print, and what it warns about."""
    problems: list[str] = []
    warnings: list[str] = []
    for line in text.splitlines():
        m = re.search(r"\[(warning|error)\]\s*(.*)", line)
        if not m or any(n in m[2] for n in NOISE):
            continue
        msg = re.sub(r"^plate \d+: found (NON_CRITICAL )?slicing (warnings|errors): ", "", m[2]).strip()
        if not msg:
            continue
        target = problems if any(s in msg for s in SERIOUS) else warnings
        if msg not in target:
            target.append(msg[:300])
    return {"problems": problems, "warnings": warnings}


def _minutes(text: str) -> float:
    total = 0.0
    for value, unit in re.findall(r"(\d+)\s*([dhms])", text):
        total += int(value) * {"d": 1440.0, "h": 60.0, "m": 1.0, "s": 1 / 60}[unit]
    return round(total, 1)


def read_gcode(text: str) -> dict[str, Any]:
    """Layers, height, time and filament from the G-code's comments, and the extruded path length per kind
    of line (outer wall, overhang wall, bridge, support…)."""
    def head(pattern: str) -> str | None:
        m = re.search(pattern, text, re.M)
        return m[1] if m else None

    lengths: dict[str, float] = {}
    kind = "Other"
    x = y = None
    for line in text.splitlines():
        if line.startswith(";TYPE:") or line.startswith("; FEATURE: "):
            kind = line.split(":", 1)[1].strip()
            continue
        if not line.startswith(("G1 ", "G0 ", "G2 ", "G3 ")):
            continue
        nx = re.search(r"X(-?[\d.]+)", line)
        ny = re.search(r"Y(-?[\d.]+)", line)
        e = re.search(r"E(-?[\d.]+)", line)
        px, py = x, y
        x = float(nx[1]) if nx else x
        y = float(ny[1]) if ny else y
        if e and float(e[1]) > 0 and px is not None and py is not None and x is not None and y is not None:
            lengths[kind] = lengths.get(kind, 0.0) + math.hypot(x - px, y - py)
    lengths = {k: round(v, 1) for k, v in lengths.items() if v >= 0.05 and k != "Custom"}
    walls = lengths.get("Outer wall", 0.0) + lengths.get("Overhang wall", 0.0)
    grams = head(r"^; (?:total )?filament used \[g\] = ([\d.]+)")
    return {"layers": int(head(r"^; total layer(?:s count| number)\s*[:=]\s*(\d+)") or 0),
            "height_mm": float(head(r"^; max_z_height: ([\d.]+)") or 0.0),
            "filament_g": float(grams) if grams else None,
            "minutes": _minutes(head(r"^; estimated printing time \(normal mode\) = (.+)$") or ""),
            "mm": lengths,
            "overhang_share": round(lengths.get("Overhang wall", 0.0) / walls, 3) if walls else 0.0}


def slice_file(slicer: dict[str, str], profiles: dict[str, dict[str, Any]], model: str, out_dir: str,
               supports: bool = False, timeout: float = 900) -> dict[str, Any]:
    """Slice one STL or 3MF file. Returns ok, the G-code file, its reading and the slicer's messages."""
    os.makedirs(out_dir, exist_ok=True)
    work = tempfile.mkdtemp(prefix="fcb-slice-")
    process = dict(profiles["process"])
    process["enable_support"] = "1" if supports else "0"
    if supports:
        process.setdefault("support_type", "tree(auto)")
    paths = {}
    for key, data in (("machine", profiles["machine"]), ("process", process), ("filament", profiles["filament"])):
        paths[key] = os.path.join(work, f"{key}.json")
        with open(paths[key], "w", encoding="utf-8") as f:
            json.dump(data, f)
    cmd = [slicer["exe"], "--load-settings", f"{paths['machine']};{paths['process']}", "--load-filaments",
           paths["filament"], "--arrange", "1", "--orient", "0", "--slice", "0", "--outputdir", out_dir,
           "--debug", "2", model]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        log, code = (done.stdout or "") + (done.stderr or ""), done.returncode
    except subprocess.TimeoutExpired:
        log, code = "[error] the slicer did not finish in time", -1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    files = sorted(glob.glob(os.path.join(out_dir, "*.gcode")))
    messages = read_log(log)
    if code != 0 and not messages["problems"]:
        tail = [ln for ln in log.splitlines() if ln.strip()][-2:]
        messages["problems"].append("the slicer stopped: " + " | ".join(t[-200:] for t in tail))
    gcode = None
    if files:
        with open(files[0], encoding="utf-8", errors="replace") as f:
            gcode = read_gcode(f.read())
    return {"ok": code == 0 and bool(files), "file": files[0] if files else None, "gcode": gcode, "log": messages}


def verdict(plain: dict[str, Any], supported: dict[str, Any] | None) -> dict[str, Any]:
    """What to tell Claude: problems first, then warnings, then the numbers; with supports only if needed."""
    g = plain.get("gcode") or {}
    mm = g.get("mm") or {}
    hanging = [w for w in plain["log"]["warnings"] if "floating" in w or "cantilever" in w or "support" in w.lower()]
    ok = not plain["log"]["problems"] and not hanging
    out: dict[str, Any] = {"printable_without_supports": ok, "problems": plain["log"]["problems"],
                           "warnings": plain["log"]["warnings"], "minutes": g.get("minutes"),
                           "filament_g": g.get("filament_g"), "layers": g.get("layers"),
                           "height_mm": g.get("height_mm"), "overhang_wall_mm": mm.get("Overhang wall", 0.0),
                           "overhang_share": g.get("overhang_share", 0.0), "bridge_mm": mm.get("Bridge", 0.0)}
    if supported and supported.get("gcode"):
        s = supported["gcode"]
        out["with_supports"] = {
            "prints": not supported["log"]["problems"],
            "extra_g": round((s.get("filament_g") or 0) - g["filament_g"], 2) if g.get("filament_g") else None,
            "extra_minutes": round((s.get("minutes") or 0) - g["minutes"], 1) if g.get("minutes") else None,
            "support_mm": (s.get("mm") or {}).get("Support", 0.0) + (s.get("mm") or {}).get("Support interface", 0.0)}
    return out
