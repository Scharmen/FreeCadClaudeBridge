"""run_headless: a script in its own freecadcmd process, so a crash or a long
computation never touches the GUI. Runs from the MCP server, not the addon."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

import anyio

from . import memguard, platforms
from .client import list_instances

MAX_OUTPUT = 20000
KEEP_WORKDIRS = 20  # older headless work folders are removed
BANNER = re.compile(r"^(FreeCAD \d+\.\d+.*Libs:.*|\(C\) \d{4}-\d{4} FreeCAD contributors|"
                    r"FreeCAD is free and open-source software.*|.*LGPL2\+ license\.)$")

WRAPPER = """\
import json as _json, sys as _sys, traceback as _tb
WORKDIR = {workdir!r}
_result = None
try:
    exec(compile(open({script!r}, encoding="utf-8").read(), "<headless>", "exec"))
except SystemExit:
    pass
except BaseException:
    _tb.print_exc()
    print("FCB_STATUS=error")
else:
    print("FCB_STATUS=ok")
print("FCB_RESULT=" + _json.dumps(_result, default=repr))
"""


def _usable(cmd: Any) -> list[str] | None:
    if isinstance(cmd, str):
        cmd = [cmd]
    if isinstance(cmd, list) and cmd and os.path.exists(cmd[0]):
        return [str(c) for c in cmd]
    return None


def find_freecadcmd() -> list[str] | None:
    """Command for freecadcmd: from a running FreeCAD (same version), else from the setup config."""
    for inst in list_instances():
        cmd = _usable(inst.info.get("freecadcmd_cmd")) or _usable(inst.info.get("freecadcmd"))
        if cmd:
            return cmd
    cfg = platforms.load_config()
    return _usable(cfg.get("freecadcmd_cmd")) or _usable(cfg.get("freecadcmd"))


ANSI = re.compile(r"\x1b\[[0-9;]*m")
OCCT_NOISE = re.compile(r"^(\*{3,}.*|Step File Name.*|.*Statistics on Transfer.*|Transfer Mode.*)$")


def parse_output(stdout: str) -> dict[str, Any]:
    status, result, lines = None, None, []
    for line in ANSI.sub("", stdout).splitlines():
        if OCCT_NOISE.match(line.strip()):
            continue
        if line.startswith("FCB_STATUS="):
            status = line.split("=", 1)[1]
        elif line.startswith("FCB_RESULT="):
            try:
                result = json.loads(line.split("=", 1)[1])
            except ValueError:
                result = line.split("=", 1)[1]
        elif not BANNER.match(line.strip()):
            lines.append(line)
    text = "\n".join(lines).strip()
    if len(text) > MAX_OUTPUT:
        text = text[:MAX_OUTPUT] + f"\n… truncated ({len(text)} chars)"
    return {"status": status, "result": result, "output": text}


async def run_headless(script: str, timeout_s: float = 600) -> dict[str, Any]:
    cmd = find_freecadcmd()
    if not cmd:
        raise RuntimeError("freecadcmd not found; start FreeCAD once with the addon or run /freecad:setup")
    root = Path(platforms.bridge_dir()) / "headless"
    workdir = root / time.strftime("%Y%m%d-%H%M%S")
    workdir.mkdir(parents=True, exist_ok=True)
    for old in sorted(p for p in root.iterdir() if p.is_dir())[:-KEEP_WORKDIRS]:
        shutil.rmtree(old, ignore_errors=True)
    script_path = workdir / "script.py"
    script_path.write_text(script, encoding="utf-8")
    wrapper = workdir / "wrapper.py"
    wrapper.write_text(WRAPPER.format(workdir=str(workdir), script=str(script_path)), encoding="utf-8")
    t0 = time.monotonic()
    timed_out = False
    budget = memguard.Budget.current()
    killed: str | None = None
    peak = 0
    async with await anyio.open_process([*cmd, str(wrapper)], env=platforms.clean_env(), cwd=str(workdir),
                                        stdin=None, start_new_session=True) as proc:
        chunks: list[bytes] = []

        async def read(stream: Any) -> None:
            async for chunk in stream:
                chunks.append(chunk)

        async def watch_memory() -> None:
            nonlocal killed, peak
            others, avail, n = 0, None, 0
            while proc.returncode is None:
                if n % memguard.GROUP_EVERY == 0:          # the slower system-wide figures
                    tree = await anyio.to_thread.run_sync(memguard.process_tree_rss, proc.pid)
                    others = max(0, tree - memguard.process_rss(proc.pid))
                    avail = await anyio.to_thread.run_sync(memguard.available_memory)
                n += 1
                rss = memguard.process_rss(proc.pid) + others
                peak = max(peak, rss)
                reason = budget.check(rss, avail)
                if reason:
                    killed = reason
                    memguard.kill_tree(proc.pid)
                    return
                await anyio.sleep(memguard.POLL_S)

        try:
            with anyio.fail_after(timeout_s):
                async with anyio.create_task_group() as outer:
                    outer.start_soon(watch_memory)
                    async with anyio.create_task_group() as tg:
                        tg.start_soon(read, proc.stdout)
                        tg.start_soon(read, proc.stderr)
                    await proc.wait()
                    outer.cancel_scope.cancel()
        except TimeoutError:
            timed_out = True
            memguard.kill_tree(proc.pid)
            await proc.wait()
    parsed = parse_output(b"".join(chunks).decode("utf-8", "replace"))
    ok = parsed["status"] == "ok" and not timed_out and killed is None
    memory = {**budget.describe(), "peak_mb": peak // memguard.MB}
    platforms.audit_record("run_headless", script=script[:20_000], workdir=str(workdir), exit_code=proc.returncode,
                           timed_out=timed_out, killed=killed, peak_mb=memory["peak_mb"], ok=ok,
                           ms=round((time.monotonic() - t0) * 1000))
    return {"exit_code": proc.returncode, "timed_out": timed_out, "duration_s": round(time.monotonic() - t0, 1),
            "ok": ok, "killed": killed, "memory": memory, "result": parsed["result"],
            "output": parsed["output"], "workdir": str(workdir)}
