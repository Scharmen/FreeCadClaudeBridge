"""Phase-0-Spike: 1.000 Aufrufe gegen den ThreadSpike-Listener.

Aufruf (System-Python genügt): python3 spikes/thread_client.py [anzahl]
Prüft: kein Hänger, Ausführung im GUI-Thread, ping antwortet während der
GUI-Thread blockiert, fehlerhafter Code wird zurückgerollt.
"""

from __future__ import annotations

import json
import os
import socket
import statistics
import struct
import sys
import threading
import time

SOCK_PATH = os.path.expanduser("~/Library/Application Support/freecad-bridge/spike.sock")


def connect() -> socket.socket:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCK_PATH)
    return s


def call(s: socket.socket, msg: dict) -> dict:
    body = json.dumps(msg).encode()
    s.sendall(struct.pack(">I", len(body)) + body)
    head = s.recv(4, socket.MSG_WAITALL)
    (n,) = struct.unpack(">I", head)
    return json.loads(s.recv(n, socket.MSG_WAITALL))


def main() -> int:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    s = connect()
    setup = call(s, {"id": 0, "op": "exec", "code": (
        "d = App.newDocument('SpikeDoc'); App.setActiveDocument(d.Name); _result = d.Name")})
    print("setup:", setup)

    lat = []
    not_gui = 0
    for i in range(1, count + 1):
        code = "_result = len(App.ActiveDocument.Objects)"
        if i % 10 == 0:
            code = "App.ActiveDocument.addObject('Part::Box', 'Box'); App.ActiveDocument.recompute(); " + code
        t0 = time.perf_counter()
        r = call(s, {"id": i, "op": "exec", "code": code, "timeout_s": 10})
        lat.append((time.perf_counter() - t0) * 1000)
        if not r.get("ok"):
            print("FAIL", i, r)
            return 1
        not_gui += 0 if r.get("gui_thread") else 1
    lat.sort()
    print(f"{count} calls ok; not in GUI thread: {not_gui}; "
          f"p50={statistics.median(lat):.2f} ms p99={lat[int(len(lat) * 0.99) - 1]:.2f} ms max={lat[-1]:.2f} ms")
    print("objects:", call(s, {"id": -1, "op": "exec", "code": "_result = len(App.ActiveDocument.Objects)"}))

    # Rollback: Fehler nach addObject darf kein Objekt hinterlassen
    before = call(s, {"id": -2, "op": "exec", "code": "_result = len(App.ActiveDocument.Objects)"})["result"]
    bad = call(s, {"id": -3, "op": "exec", "code": "App.ActiveDocument.addObject('Part::Box','Bad'); 1/0"})
    after = call(s, {"id": -4, "op": "exec", "code": "_result = len(App.ActiveDocument.Objects)"})["result"]
    print(f"rollback: ok={bad['ok']} objects before={before} after={after} -> {'PASS' if before == after else 'FAIL'}")

    # ping während der GUI-Thread 3 s blockiert
    blocker = threading.Thread(target=lambda: call(connect(), {
        "id": -5, "op": "exec", "code": "import time; time.sleep(3)", "timeout_s": 10}))
    blocker.start()
    time.sleep(0.5)
    t0 = time.perf_counter()
    pong = call(connect(), {"id": -6, "op": "ping"})
    print(f"ping while GUI busy: {pong['result']} in {(time.perf_counter() - t0) * 1000:.1f} ms")
    blocker.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
