"""Phase 2 gate: a 10-minute run_headless job while the GUI must stay responsive.

Usage: uv run --project plugin/server python tests/scenarios/headless_gate.py [seconds]
Prints the headless result and GUI ping latencies measured every 2 s during the job.
"""

import statistics
import sys
import threading
import time

import anyio

from freecad_bridge import client, headless

SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 600
SCRIPT = f"""
import time, Part
t0, n = time.time(), 0
while time.time() - t0 < {SECONDS}:
    s = Part.makeBox(10, 10, 10).fuse(Part.makeSphere(6))  # real OCCT work, not just sleeping
    n += 1
_result = {{"iterations": n, "seconds": round(time.time() - t0)}}
"""

result = {}
t = threading.Thread(target=lambda: result.update(anyio.run(headless.run_headless, SCRIPT, SECONDS + 120)))
t.start()
latencies, failures = [], 0
while t.is_alive():
    t0 = time.perf_counter()
    try:
        client.call("run_python", {"code": "_result = len(App.listDocuments())"}, timeout_s=5)
        latencies.append((time.perf_counter() - t0) * 1000)
    except client.BridgeError:
        failures += 1
    time.sleep(2)
t.join()
print({k: result.get(k) for k in ("ok", "result", "duration_s", "timed_out", "exit_code")})
print(f"GUI pings: {len(latencies)} ok, {failures} failed, median {statistics.median(latencies):.1f} ms, "
      f"max {max(latencies):.1f} ms")
