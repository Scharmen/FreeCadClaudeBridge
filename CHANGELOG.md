# Changelog

## Phase 0 – Spikes (2026-10-31)

- Added `docs/PLAN.md` and `CLAUDE.md`.
- Spikes on macOS with FreeCAD 1.1.4: threading (1,000 calls in the GUI thread, rollback, one undo step per call), chat from FreeCAD via `QProcess` with `claude -p` and an MCP callback, `uvx` startup time, Report view, task dialogs. Results in `docs/spikes.md`.
- Plan adjusted: cleaned environment for child processes, MCP SDK 2.x, Python pinned to 3.12, `--plugin-dir` and startup check in the chat panel, `--permission-prompts none`, SIGINT on stop, undo limit, repo name `Scharmen/FreeCadClaudeBridge`.
- Translated all repository content to English.
