# CLAUDE.md

## Goal and architecture

Control FreeCAD by chat, in the "Claude" chat panel inside FreeCAD or in Claude Code. The full plan is in `docs/PLAN.md` (kept locally, not in Git); it is binding.

- **Plugin `freecad`** (Claude Code, marketplace `freecad-claude` = this repo `Scharmen/FreeCadClaudeBridge`) bundles MCP server, skill, `/freecad:setup`, `/freecad:status` and a SessionStart hook.
- **MCP server `freecad-bridge-mcp`** (stdio, Python 3.12, official MCP SDK, started via `uvx`) offers 19 generic tools, first of all `run_python`.
- **Transport:** Unix socket (macOS/Linux) or TCP on 127.0.0.1 only (Windows, fallback); 4-byte length prefix plus JSON, mutual HMAC-SHA256 handshake with a 32-byte token.
- **Addon `ClaudeBridge`** in the FreeCAD GUI process: a listener thread accepts, execution happens in the GUI thread via Qt signal; each call is exactly one undo transaction. The journal distinguishes `user`/`claude`.
- **Chat panel** in the addon starts `claude -p --output-format stream-json` as an asynchronous `QProcess`; login via subscription or API key (`ANTHROPIC_API_KEY`, key in the system keychain).
- **Headless worker:** `run_headless` starts a separate `freecadcmd` process for heavy computations.
- State (token, instance files, snapshots, log) lives in the bridge directory, never under `${CLAUDE_PLUGIN_ROOT}`.

## Conventions

- Repository content (code, comments, docs, commits, PRs) is in English.
- Server: Python 3.12 (`requires-python = ">=3.12,<3.13"`), official MCP SDK 2.x (`from mcp.server.mcpserver import MCPServer`; examples using `FastMCP` are 1.x), package under `plugin/server/`.
- Development: `uvx --from <path>` caches by version, so code changes only take effect with `uvx --refresh` or `uv run --project plugin/server`. uv lives in `~/.local/bin`.
- Every child process started from FreeCAD (Claude Code, `uvx`, `freecadcmd`) gets a cleaned environment; otherwise FreeCAD passes on `PYTHONHOME` and bundle paths.
- Addon: standard library only plus FreeCAD's bundled Python (macOS bundle 1.1.x: 3.11) and PySide; no third-party packages.
- `protocol.py` exists as an identical copy in addon and server; CI checks this.
- Plugin, addon and package versions are identical.
- ruff, pytest, type annotations everywhere.

## Working rules

- One phase per session. The next phase starts only after its gate is met and approved.
- Acceptance criteria as tests first, then the code.
- No deviation from the key decisions in `docs/PLAN.md` without asking.
- After each phase: CHANGELOG entry, version bump, tag.
- Results of spikes and open points go into `docs/spikes.md`; phase gate reports into `docs/phaseN.md`. These planning docs are git-ignored.
