"""Phase-0-Spike: minimaler MCP-Server (stdio) nur mit Standardbibliothek.

Ein Tool `run_python`, weitergereicht an den ThreadSpike-Socket. Ersetzt für
den Chat-Spike das MCP-SDK, damit uv dafür noch nicht nötig ist.
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from thread_client import call, connect  # noqa: E402

TOOL = {
    "name": "run_python",
    "description": "Run Python code in the running FreeCAD GUI (one undo step). "
                   "Set _result to return a value. App and Gui are predefined.",
    "inputSchema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
}


def reply(req_id, result=None, error=None) -> None:
    msg = {"jsonrpc": "2.0", "id": req_id}
    msg.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        req = json.loads(line)
        method, req_id = req.get("method"), req.get("id")
        if req_id is None:  # Notification
            continue
        if method == "initialize":
            reply(req_id, {
                "protocolVersion": req["params"].get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "freecad-spike", "version": "0.0.0"},
            })
        elif method == "tools/list":
            reply(req_id, {"tools": [TOOL]})
        elif method == "tools/call":
            code = req["params"]["arguments"]["code"]
            try:
                r = call(connect(), {"id": req_id, "op": "exec", "code": code, "timeout_s": 60})
            except OSError as e:
                r = {"ok": False, "error": f"FreeCAD not reachable: {e}"}
            reply(req_id, {"content": [{"type": "text", "text": json.dumps(r)}], "isError": not r.get("ok")})
        elif method == "ping":
            reply(req_id, {})
        else:
            reply(req_id, error={"code": -32601, "message": f"unknown method {method}"})


if __name__ == "__main__":
    main()
