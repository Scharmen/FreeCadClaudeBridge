# Phase 1: MVP on macOS – gate status

As of: 2026-10-31 · version 0.1.0 · Mac (Apple Silicon), FreeCAD 1.1.4, Claude Code 2.1.310

## Gate checklist

| Gate item | Status | Evidence |
| --- | --- | --- |
| Three install steps under 5 minutes on a **fresh** Mac user account | ⏳ manual | On the dev account: `/freecad:setup` equivalent (`freecad-bridge install`) took 0.9 s, FreeCAD start to bridge ready 2–3 s. A fresh account still has to be tried by hand |
| Chat panel: cube, sketch, pad, STEP export on request | ✅ | Panel driven end-to-end: document `GatePanel`, cube, Body, fully constrained sketch (0 DOF), 10 mm pad (6000 mm³), `panel_pad.step` (6.8 KB) in 22 s |
| Same from the terminal | ✅ | `claude -p` with the plugin: same build and export in one run ($0.19) |
| Works with subscription | ✅ | Both runs used the Claude Code login (`apiKeySource: none`) |
| Works with an API key | ⏳ manual | Needs a key. Verified only the error path: API key mode without a stored key shows "no key is stored" |
| GUI stays usable during a response | ✅ | Slowest bridge round trip while Claude worked: 4 ms |
| Stop button aborts cleanly | ✅ | SIGINT mid-stream: process gone after 1.1 s, partial text kept, Send re-enabled |
| Cmd/Ctrl+Z undoes exactly the last Claude step | ✅ (via API) | `test_run_python_is_one_undo_step`: one call with two objects is undone by one undo; Phase 0 showed the same with `Std_Undo`. Pressing Cmd+Z by hand is still worth a try |
| Faulty code leaves no object | ✅ | `test_faulty_code_leaves_nothing` |

## Remaining Phase 0 items

| Item | Status |
| --- | --- |
| Dock launch (item 11) | ✅ FreeCAD started from the Dock (parent `launchd`, PATH `/usr/bin:/bin:/usr/sbin:/sbin`, no terminal variables): the panel found Claude Code via `config.json`, used the subscription login and built `DockTest` with a 27000 mm³ cube in 8 s |
| Addon Manager with `<subdirectory>` (item 5) | ⏳ manual: `package.xml` is in place; add the repo as a custom repository in the Addon Manager (branch with this code) and check that the workbench "Claude Bridge" appears. Don't keep both the Addon Manager copy and the `/freecad:setup` copy installed at once |
| `ANTHROPIC_API_KEY` precedence over the subscription (item 10) | ⏳ manual, together with the API key gate item |

## Tests

- `make test`: 45 unit tests (protocol and handshake, repo consistency, stream parser with a recorded-format fixture, child environment, installer with fake FreeCAD bundles, client against a fake addon, serialization).
- `make itest`: 15 GUI integration tests against the running FreeCAD (status, one undo step, named transaction, rollback, stdout and persistent namespace, error objects, changed objects, tree and properties, unknown object, STEP/STL export, save and close guard, ping/status while busy, timeout, unknown op, code that closes its own document).

## Found and fixed during Phase 1

- Quantities used FreeCAD's `UserString`, which follows the user's decimals setting ("12.00 mm"). Now converted via `getUserPreferred()` to six significant digits.
- Code that closed its own document crashed the transaction handling (`ReferenceError`). Now guarded; regression test added.
- The panel remembered "hidden" when FreeCAD quit (closing hides docks). Visibility is now saved only when the user toggles the panel.
- With no document open, the panel resumed an old unrelated conversation. Without a document it now always starts fresh.

## Deviations from the plan (small)

- Claude path and plugin root live in the bridge directory's `config.json` instead of FreeCAD parameters (FreeCAD rewrites `user.cfg` on exit).
- The addon's `bridge/` and `panel/` live inside one package `claudebridge/` to avoid name clashes on FreeCAD's `sys.path`.
- The mutual HMAC handshake (planned for Phase 4) is already in place; it was small.
- Local development install: the marketplace `freecad-claude` currently points at the local checkout (`claude plugin marketplace add ~/StudioProjects/FreeCadClaudeBridge`). Once `main` has this code, switch to `Scharmen/FreeCadClaudeBridge`.
