"""`freecad-bridge` command line: install, doctor, token."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from . import __version__, installer, learning, memguard, platforms, protocol, settings
from .client import BridgeError, call, fix_for, list_instances


def doctor(quiet: bool = False, addon_src: str | None = None) -> int:
    """Check setup and connection. --quiet prints only problems (SessionStart hook).
    With --addon-src, an addon older than the plugin is updated first."""
    lines: list[tuple[str, str]] = []
    if addon_src:
        try:
            installer.auto_update(addon_src, out=lambda text: lines.append(("INFO", text)))
        except OSError as e:
            lines.append(("WARN", f"could not update the addon: {e}; run /freecad:setup"))
    cfg = platforms.load_config()
    if not cfg.get("user_app_data"):
        lines.append(("FAIL", "FreeCAD bridge is not set up yet: run /freecad:setup"))
    else:
        mod = Path(cfg["user_app_data"]) / "Mod"
        have = installer.installed_version(mod)
        if have is None:
            lines.append(("FAIL", f"ClaudeBridge addon missing in {mod}: run /freecad:setup"))
        elif have != __version__:
            lines.append(("WARN", f"addon {have} is outdated (plugin {__version__}): run /freecad:setup, "
                                  "then restart FreeCAD"))
        else:
            lines.append(("OK", f"addon {have} installed for FreeCAD {cfg.get('freecad_version', '?')}"))
        claude = cfg.get("claude_path")
        if not claude or not Path(claude).exists():
            lines.append(("WARN", "claude executable not recorded; the chat panel in FreeCAD won't start. "
                                  "Run /freecad:setup"))
    if platforms.read_token() is None and cfg:
        lines.append(("FAIL", "token file missing: run /freecad:setup"))
    lines.append(("OK", f"uv: {shutil.which('uv') or shutil.which('uvx') or 'via uvx'}"))
    instances = list_instances()
    if not instances:
        lines.append(("INFO", "FreeCAD with ClaudeBridge is not running"))
    for inst in instances:
        try:
            res = call("ping", timeout_s=5, instance=inst.pid)
            for w in res.warnings:
                lines.append(("WARN", w))
            lines.append(("OK", f"FreeCAD {inst.info.get('freecad_version', '?')} (pid {inst.pid}) "
                                f"handshake ok, addon {res.hello.get('addon_version')}"))
        except BridgeError as e:
            fix = f" Fix: {fix_for(e.code)}" if fix_for(e.code) else ""
            lines.append(("FAIL", f"FreeCAD pid {inst.pid}: {e.code}: {e.message}{fix}"))
    for level, text in lines:
        if quiet and level == "OK" or quiet and level == "INFO" and not text.startswith("updated"):
            continue
        print(f"freecad: {text}" if quiet else f"[{level:4}] {text}")
    return 1 if any(level == "FAIL" for level, _ in lines) and not quiet else 0


def settings_cmd(action: str | None, key: str | None, value: str | None) -> int:
    bd = platforms.bridge_dir()
    try:
        if action == "set":
            if key is None or value is None:
                print("usage: freecad-bridge settings set <key> <value>")
                return 2
            settings.save(bd, {key: value})
        elif action == "reset":
            settings.reset(bd, key)
    except KeyError as e:
        print(f"error: {e.args[0]}")
        return 2
    except ValueError as e:
        print(f"error: {e}")
        return 2
    values = settings.load(bd)
    print(f"Settings ({settings.path(bd)}):")
    for k, (default, lo, hi, unit, text) in settings.SCHEMA.items():
        mark = "" if values[k] == default else "  (changed)"
        print(f"  {k} = {values[k]:g} {unit}{mark}\n      {text}; default {default:g}, allowed {lo:g}-{hi:g}")
    b = memguard.Budget.current()
    print(f"On this computer ({b.total / settings.GB:.1f} GB RAM): {b.reserve / settings.GB:.1f} GB always stay free, "
          f"a headless job may use up to {b.hard_cap / settings.GB:.1f} GB.")
    if memguard.ENV_MAX_MB in os.environ:
        print(f"  note: {memguard.ENV_MAX_MB} overrides the job limit")
    return 0


def preferences_context() -> str:
    """Text for the SessionStart hook: learned lessons and whether to ask for feedback."""
    bd = platforms.bridge_dir()
    if not platforms.load_config() and not learning.lessons(bd):
        return ""
    parts = [learning.context_text(bd)]
    if settings.load(bd)["learning.ask_for_feedback"]:
        parts.append("FreeCAD: after you finish creating or changing a model for the user, ask in one short line "
                     "whether it matches what they wanted, then store their answer with the record_feedback tool "
                     "(rating, their words, and one general lesson).")
    else:
        parts.append("FreeCAD: the user asked not to be asked for feedback after models: do not ask.")
    return "\n".join(p for p in parts if p)


def preferences_cmd(action: str) -> int:
    bd = platforms.bridge_dir()
    if action == "forget":
        learning.forget(bd)
        print("forgot all feedback and learned preferences")
        return 0
    if action == "context":
        text = preferences_context()
        if text:
            print(text)
        return 0
    items = learning.lessons(bd)
    print(f"Learned preferences ({learning.ensure_file(bd) if items else bd / learning.PREFERENCES}):")
    print("\n".join(f"  - {i}" for i in items) if items else "  (nothing learned yet)")
    print(f"Feedback entries: {len(learning.recent_feedback(bd, 10_000))}; asking for feedback: "
          f"{'on' if settings.load(bd)['learning.ask_for_feedback'] else 'off'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="freecad-bridge", description=f"FreeCAD × Claude Code bridge {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_inst = sub.add_parser("install", help="install the ClaudeBridge addon into FreeCAD")
    p_inst.add_argument("--addon-src", help="path to plugin/addon/ClaudeBridge")
    p_inst.add_argument("--freecad", help="FreeCAD.app or freecadcmd to use")
    p_inst.add_argument("--uninstall", action="store_true", help="remove addon, token and instance files")
    p_inst.add_argument("--purge", action="store_true", help="with --uninstall: also remove snapshots, backups and "
                        "the audit log")
    p_doc = sub.add_parser("doctor", help="check setup and connection")
    p_doc.add_argument("--quiet", action="store_true", help="print only problems")
    p_doc.add_argument("--addon-src", help="update an older installed addon from this folder first")
    p_doc.add_argument("--with-preferences", action="store_true", help="also print learned preferences (hook)")
    p_tok = sub.add_parser("token", help="manage the shared token")
    p_tok.add_argument("--rotate", action="store_true", help="create a new token")
    sub.add_parser("instances", help="list running FreeCAD instances as JSON")
    p_pref = sub.add_parser("preferences", help="what Claude learned from your feedback (show, context, forget)")
    p_pref.add_argument("action", nargs="?", choices=["show", "context", "forget"], default="show")
    p_set = sub.add_parser("settings", help="show or change user settings (memory limits of headless jobs, ...)")
    p_set.add_argument("action", nargs="?", choices=["show", "set", "reset"], default="show")
    p_set.add_argument("key", nargs="?")
    p_set.add_argument("value", nargs="?")
    args = parser.parse_args(argv)

    if args.cmd == "install":
        if args.uninstall:
            return installer.uninstall(args.freecad, purge=args.purge)
        return installer.install(args.addon_src, args.freecad)
    if args.cmd == "doctor":
        rc = doctor(args.quiet, args.addon_src)
        if args.with_preferences:
            text = preferences_context()
            if text:
                print(text)
        return rc
    if args.cmd == "token":
        if args.rotate:
            platforms.write_token(protocol.new_token())
            print(f"new token written to {platforms.token_path()}; FreeCAD and Claude Code use it from the next "
                  "call, no restart needed")
        else:
            print(platforms.token_path() if platforms.read_token() else "no token")
        return 0
    if args.cmd == "preferences":
        return preferences_cmd(args.action)
    if args.cmd == "settings":
        return settings_cmd(args.action, args.key, args.value)
    if args.cmd == "instances":
        print(json.dumps([i.info for i in list_instances()], indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
