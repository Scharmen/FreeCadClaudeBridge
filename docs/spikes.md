# Phase 0: Spikes

As of: 2026-10-31 · Mac (Apple Silicon, macOS 27.2), FreeCAD 1.1.4 (`/Applications/FreeCAD.app`, bundle `macOS-arm64-py311`), Claude Code 2.1.310, uv 0.13.6

A third-party addon `freecad-mcp` is already in the user `Mod` folder. It ran alongside the tests without interfering.

## Status of the open points

| # | Item | Status | Result |
| --- | --- | --- | --- |
| 1 | Python in the bundle | macOS ✅, Windows ⏳ Phase 3 | macOS arm64: **Python 3.11.14** (`Contents/Resources/bin/python`) |
| 2 | `freecadcmd` | macOS ✅, Windows ⏳ Phase 3 | Present: `FreeCAD.app/Contents/Resources/bin/freecadcmd`, alongside `freecad`, `python`, `pip`. Signed (Team ID 289DJRF23X) |
| 3 | User directory on Windows | ⏳ Phase 3 | needs a Windows machine |
| 4 | Headless one-liner | macOS ✅, AppImage ⏳ Phase 3 | `freecadcmd <script.py>` runs a file. After the script output, a license banner follows on stdout, so the installer parses marker lines. `getUserAppDataDir()` = `~/Library/Application Support/FreeCAD/v1-1/`, `getHomePath()` = `…/FreeCAD.app/Contents/Resources/` |
| 5 | Addon Manager with `<subdirectory>` | ✅ code read, hands-on test in Phase 1 | FreeCAD's init loader (embedded `FreeCADInit.py` in `libFreeCADApp`) uses the `Subdirectory` field of a `<workbench>` as the path for `Init.py`/`InitGui.py` and `sys.path`. The Addon Manager clones the whole repo to `Mod/FreeCadClaudeBridge/` |
| 6 | Reading the console | ✅ | `App.Console` has **no** Python API for observers. The Report view is reachable: `QTextEdit` with `objectName() == "Report view"` (class `Gui::DockWnd::ReportOutput`), lines with timestamps. **Messages arrive delayed**, only after the next event loop pass, not within the same call. `get_console` therefore captures via `textChanged` into its own ring buffer; a call's own output still comes from stdout/stderr redirection |
| 7 | Task dialogs | ✅ | `Gui.Control.activeTaskDialog()` returns, for C++ dialogs too, an object with **`accept()`**, **`reject()`**, `getStandardButtons()`, `getDialogContent()`. Tested with `Part_Primitives`: `reject()` closes, `accept()` triggers the action, `closeDialog()` closes without asking. `activeDialog()` returns `True`/`False` |
| 8 | `uvx` startup time | ✅ | Throwaway package with `mcp` 2.3.0 and Python 3.12: first run 1.3 s (2.5 s including the Python 3.12 download), then **0.56–0.60 s**. New path (as after a plugin update): 1.2 s, then 0.53 s. **Caution:** `uvx` doesn't pick up source changes under the same path at the same version (cache). For development use `uvx --refresh` or `uv run --project` |
| 9 | Permission rule | ✅ | All tools of the plugin server: **`mcp__plugin_freecad_freecad__*`** (per the docs also `mcp__plugin_freecad_freecad`). Allow globs only work after a fixed `mcp__<server>__` prefix |
| 10 | Claude Code in the background | ✅ | See below |
| 11 | Finding `claude` from FreeCAD | ✅ (Dock launch confirmed in Phase 1, see `docs/phase1.md`) | Started from FreeCAD via `QProcess` with an absolute path: subscription login found (`apiKeySource: none`), tool call goes through. With a minimal environment (`env -i`, as when launched from Dock/Finder), `claude` is not on PATH but runs with the absolute path. **Still open:** actually launch FreeCAD from the Dock. For the test it was started with `open -a` from the terminal and inherited its environment |

## Spike results

**Threading (`spikes/ThreadSpike`, `spikes/thread_client.py`)**, gate met:

- 1,000 calls without a hang, all in the GUI thread, every tenth creates a `Part::Box`. Latency per call p50 0.06 ms, p99 9.9 ms, max 78 ms.
- Error after `addObject` → `abortTransaction()` → no object left behind.
- `ping` answers in 0.7 ms while the GUI thread is blocked for 3 s.
- A call with two `addObject` is undone completely by a single `Std_Undo`; `UndoNames` shows the transaction name.
- **New:** by default FreeCAD keeps only **20 undo steps** (`UndoCount` stayed at 20). Many small Claude calls push out the user's undo history.

**Chat (`spikes/chat_spike.py`, `spikes/mini_mcp.py`)**, gate met:

- Started from FreeCAD via `QProcess`: `claude -p … --output-format stream-json --verbose --mcp-config … --allowedTools mcp__spike__run_python`. Claude calls `run_python`, the cube appears in the open document as one undo transaction. Result after 8.7 s.
- GUI stays free: a 100 ms timer in the GUI thread fired 77 times in 8.7 s (ideal 87).
- **Finding 1, environment:** FreeCAD sets `PYTHONHOME` to the bundle, `PYTHONPATH`, `LD_LIBRARY_PATH`, `SSL_CERT_FILE`, and prepends about 35 bundle paths (including its own `python`) to PATH. Child processes inherit this; the MCP server therefore died immediately (`CONNECTION_CLOSED`). Only with a cleaned `QProcessEnvironment` did it run. This also affects `uvx` and `run_headless`.
- **Finding 2, stdin:** Without `setStandardInputFile(QProcess.nullDevice())`, `claude -p` waits 3 s for stdin and prints a warning.
- **Finding 3, tool search:** Claude loads MCP tools on demand via `ToolSearch`, which costs one round trip (about 3 s).

## Item 10 in detail: `claude -p`

- NDJSON events: `system/init` (`session_id`, `apiKeySource`, `model`, `permissionMode`, `mcp_servers` with `status`, `plugins`, `plugin_errors`), `assistant` (text and `tool_use` blocks), `rate_limit_event`, `system/post_turn_summary`, `result` (`result`, `is_error`, `total_cost_usd`). `--verbose` is required for stream-json; token streaming with `--include-partial-messages` (`stream_event`/`text_delta`).
- `--resume <session-id>` continues a session (tested).
- Without `--bare`, `-p` loads installed plugins, skills, hooks and CLAUDE.md. `--plugin-dir <path>` is available in addition.
- Permissions: Manual mode since 2.1.302; calls that aren't allowed are denied. With `--permission-prompts none`, denials arrive as `system/permission_denied` and in `result.permission_denials`. Alternative: `--permission-prompt-tool`.
- Abort: SIGINT ends the turn cleanly; SIGTERM (exit 143) leaves it without a result.
- Not tested: precedence of `ANTHROPIC_API_KEY` over the subscription login, because no key is available. Whether the subscription terms cover this use is for Kevin to check.
- **Risk:** According to the docs, `--bare` will become the default for `-p`. Bare uses **no** subscription login and no installed plugins.

## Other findings

- **MCP SDK 2.x:** The current release is `mcp` 2.3.0. `FastMCP` is now `MCPServer` (`from mcp.server.mcpserver import MCPServer`). Code from neka-nat/blwfish (v1 API) can't be reused as is.
- **Python version for uvx:** `requires-python = ">=3.12"` made uv choose Python 3.15. For 3.12 use `">=3.12,<3.13"` or `--python 3.12`.

## Spike files

- `spikes/ThreadSpike/`: mini addon, Unix socket listener, dispatch via Qt signal plus `Future`, one transaction per call. Copied to `~/Library/Application Support/FreeCAD/v1-1/Mod/ThreadSpike` for the tests.
- `spikes/thread_client.py`: load test.
- `spikes/mini_mcp.py`: MCP server using only the standard library.
- `spikes/chat_spike.py`: Claude Code as `QProcess` with a cleaned environment.

## Phase 0 gate

Met except for two remaining items that don't block the gate: the real Dock launch (item 11) and the Windows/AppImage parts of 1 to 4 (belong to Phase 3).
