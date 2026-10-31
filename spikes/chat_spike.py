"""Phase-0-Spike: Claude Code als QProcess aus FreeCAD starten.

In der FreeCAD-Python-Konsole (ThreadSpike muss laufen):
    exec(open(os.path.expanduser(
        "~/StudioProjects/FreeCadClaudeBridge/spikes/chat_spike.py")).read())

Prüft: stream-json zeilenweise lesen, Tool-Aufruf läuft zurück in FreeCAD,
GUI bleibt bedienbar (Herzschlag in der Konsole läuft weiter), Fund von
`claude` ohne Shell-PATH.
"""

import json
import os
import time

import FreeCAD as App
from PySide import QtCore

SPIKES = os.path.expanduser("~/StudioProjects/FreeCadClaudeBridge/spikes")
CLAUDE = os.path.expanduser("~/.local/bin/claude")  # später aus /freecad:setup
PROMPT = ("Lege im aktiven FreeCAD-Dokument mit dem Tool run_python einen Würfel "
          "20x20x20 mm namens SpikeCube an und nenne danach sein Volumen.")

mcp_config = json.dumps({"mcpServers": {"spike": {
    "command": "/usr/bin/python3", "args": [os.path.join(SPIKES, "mini_mcp.py")]}}})

_spike = {"t0": time.time(), "beats": 0}
LOG = os.path.expanduser("~/Library/Application Support/freecad-bridge/chat_spike.log")
open(LOG, "w").close()


def _log(line):
    App.Console.PrintMessage(line + "\n")
    with open(LOG, "a") as f:
        f.write(line + "\n")


proc = QtCore.QProcess()
proc.setProgram(CLAUDE)
proc.setArguments([
    "-p", PROMPT,
    "--output-format", "stream-json", "--verbose",
    "--mcp-config", mcp_config, "--strict-mcp-config",
    "--allowedTools", "mcp__spike__run_python",
])
proc.setWorkingDirectory(os.path.expanduser("~"))
proc.setStandardInputFile(QtCore.QProcess.nullDevice())  # sonst wartet claude 3 s auf stdin

# FreeCAD vererbt PYTHONHOME, LD_LIBRARY_PATH und eigene PATH-Einträge; die
# brechen jedes fremde Python im Kindprozess (MCP-Server, uvx).
_env = QtCore.QProcessEnvironment.systemEnvironment()
_bundle = App.getHomePath().rstrip("/")
for _k in ("PYTHONHOME", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "LD_LIBRARY_PATH",
           "SSL_CERT_FILE", "GIT_SSL_CAINFO", "FONTCONFIG_PATH", "PYSIDE6_OPTION_PYTHON_ENUM"):
    _env.remove(_k)
for _k in list(_env.keys()):
    if _k.startswith("CLAUDE_CODE_") or _k == "CLAUDECODE":
        _env.remove(_k)
_env.insert("PATH", os.pathsep.join(
    p for p in _env.value("PATH").split(os.pathsep)
    if p and not p.startswith(_bundle) and "/FreeCAD/" not in p))
proc.setProcessEnvironment(_env)
_buf = {"out": b""}


def _on_stdout():
    _buf["out"] += bytes(proc.readAllStandardOutput())
    *lines, _buf["out"] = _buf["out"].split(b"\n")
    for raw in lines:
        if not raw.strip():
            continue
        try:
            ev = json.loads(raw)
        except ValueError:
            App.Console.PrintWarning(f"[chat] non-JSON: {raw[:120]!r}\n")
            continue
        t = ev.get("type")
        dt = time.time() - _spike["t0"]
        if t == "system" and ev.get("subtype") == "init":
            _log(f"[chat {dt:5.1f}s] init session={ev.get('session_id')} "
                 f"apiKeySource={ev.get('apiKeySource')} mcp={ev.get('mcp_servers')}")
        elif t == "assistant":
            for block in ev.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    _log(f"[chat {dt:5.1f}s] {block['text']}")
                elif block.get("type") == "tool_use":
                    _log(f"[chat {dt:5.1f}s] tool_use {block['name']}")
        elif t == "result":
            _log(f"[chat {dt:5.1f}s] result is_error={ev.get('is_error')} "
                 f"cost={ev.get('total_cost_usd')} beats={_spike['beats']}")


def _beat():
    _spike["beats"] += 1


_hb = QtCore.QTimer()
_hb.timeout.connect(_beat)
_hb.start(100)  # zählt nur, wenn der GUI-Thread frei ist
proc.readyReadStandardOutput.connect(_on_stdout)
proc.readyReadStandardError.connect(
    lambda: _log("[chat stderr] " + bytes(proc.readAllStandardError()).decode(errors="replace")))
proc.finished.connect(lambda code, _status: (_hb.stop(), _log(f"[chat] exit {code}")))
proc.start()
