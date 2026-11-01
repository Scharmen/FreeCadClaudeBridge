# Changelog

## 1.3.0 – pictures to 3D, learning from feedback (unreleased)

- New tool `image_to_3d` (local, no upload): `show` lets Claude look at a picture; `outline` turns the picture's shapes (with holes) into a profile that is extruded or revolved (side silhouettes of vases, figures); `relief` makes a closed, printable height mesh from brightness; `reference` places the picture as a scaled image plane to model against. Dark-on-light pictures and transparent backgrounds work; `invert`/`threshold` correct the rest. New server dependencies: Pillow, NumPy.
- Chat panel: 🖼 attaches a picture to the message.
- Learning: after a turn that changed the model the panel offers 👍 / 〜 / 👎; for "partly" and "no" you say what should change, Claude fixes it and stores one general lesson. In Claude Code, Claude asks after larger models and stores the answer with the new `record_feedback` tool; `get_preferences` and the SessionStart hook give the lessons to every new session, and the panel adds them to its system prompt.
- Lessons live in `preferences.md` in the bridge folder (one per line, edit freely), ratings in `feedback.jsonl`; both stay on this computer, survive uninstall unless `--purge`. Settings page: switch for asking, open the lessons, forget everything. CLI: `freecad-bridge preferences [show|forget]`.
- Fixed: "Settings…" opened FreeCAD's modal Preferences dialog from Python, which holds the GIL and froze the bridge until it closed; it now opens a non-modal window (Edit → Preferences still has the page).
- 24 tools in total; `learning.py` is a third module shared identically by addon and server.

## 1.2.0 – settings (unreleased)

- Settings you can change: in FreeCAD under *Preferences → Claude Bridge* (also *Claude → Settings…* and ⚙ → Settings… in the chat panel), in a terminal with `freecad-bridge settings [set <key> <value> | reset [<key>]]`, or with `/freecad:settings` in Claude Code.
- Headless job limits: share of RAM kept free (15 %), minimum kept free (2 GB), the most one job may use (75 % of RAM), the size from which a job counts as the cause of low memory (512 MB), default time limit (600 s). Every value has an allowed range and is clamped; the page shows what the values mean on this computer.
- The addon switches (autostart, chat panel, connection type) are on the same page.
- Stored in `settings.json` in the bridge folder (only changed values; kept on uninstall unless `--purge`). `FREECAD_BRIDGE_HEADLESS_MAX_MB` still overrides the job limit. The settings module is an identical copy in addon and server, checked by CI.

## 1.1.1 – memory guard for run_headless (unreleased)

- `run_headless` stops a job before it exhausts memory. The budget comes from the machine it runs on, at run time: the job is killed when the system's available memory drops below a reserve (15 % of RAM, at least 2 GB) while the job holds more than 512 MB, or when the job alone passes 75 % of RAM (`FREECAD_BRIDGE_HEADLESS_MAX_MB` overrides that cap). Polled every 50 ms through `libproc` (macOS), `/proc` (Linux) or the Win32 API; the whole process group is killed.
- Results report `killed` and `memory` (total, reserve, cap, peak); the audit log records both.
- Found while modelling: a boolean union of near-tangent freeform surfaces made OpenCascade allocate about 140 GB.

## 1.1.0 – Phase 5: several FreeCAD instances (unreleased)

- New tools `instances` (every running FreeCAD with pid, versions, start time and open documents) and `select_instance` (by pid or by document name/label; without arguments back to automatic). 21 tools in total.
- The selection lasts for the Claude Code session; a selected FreeCAD that has quit is an error, never a silent switch to another one.
- Without a selection, calls still go to the newest FreeCAD, and the result warns when others are running.
- Fixed: the chat panel of an older FreeCAD window controlled the newest one. It now pins its own FreeCAD (`FREECAD_BRIDGE_INSTANCE`).
- Phase 5 options Flatpak/Snap, WSL and network access with TLS are not built.

## 1.0.0 – Phase 4: hardening (unreleased, tagged after the manual acceptance on Windows 11 and Ubuntu)

- Updates need only a FreeCAD restart: after a plugin update the SessionStart hook and the MCP server point the config at the new plugin and replace an older addon (backups outside `Mod`, last 3 kept, never a downgrade). The chat panel, `/freecad:status` and tool results say when FreeCAD still runs the old addon.
- Audit log `bridge-log.jsonl` (0600, rotates at 10 MB): every bridge call with arguments and code, every `run_headless` script, every rejected handshake. *Claude → Open audit log* in FreeCAD.
- Token rotation from the FreeCAD menu (*Claude → Rotate token*) as well as `freecad-bridge token --rotate`; tokens are written atomically and take effect without a restart.
- Handshake accepts only 4 KB frames before authentication.
- Setup and connection errors come with a fix (`Fix: …`) in tool results and in `doctor`; a blocked loopback port on Windows is named as a possible cause.
- Uninstall also removes sockets and the API key from the system keychain; `--purge` removes snapshots, chat history, headless work folders, backups and the audit log. Stale sockets of crashed FreeCAD processes are cleaned up on start; old headless work folders are pruned (last 20).
- [Security checklist](docs/security.md) and [manual acceptance checklist](docs/manual-testing.md).

## Phase 3: Windows and Linux (in 1.0.0)

- FreeCAD detection on Windows (registry, Program Files, portable) and Linux (PATH, AppImages); AppImages run as `FreeCAD.AppImage freecadcmd`.
- Windows: safe process check (no accidental Ctrl+C), PATH cleanup, TCP transport, chat panel stop; API key in Windows Credential Manager or Linux Secret Service.
- CI against real FreeCAD 1.1.4: unit tests on macOS/Windows/Ubuntu, headless tests with the Linux AppImage and the Windows portable build, all GUI integration tests on Linux under xvfb.

## 0.2.0 – Phase 2: full control (2026-10-31)

- 13 new MCP tools (19 in total): `inspect_shape`, `inspect_sketch`, `get_changes`, `get_console`, `api_help`, `list_commands`, `run_command`, `task_dialog`, `selection`, `import`, `view` (optional screenshot), `run_headless` (separate `freecadcmd` process), `snapshot`.
- Journal of GUI events with source `user`/`claude`, Report view capture, time limit for Python loops (trace hook, rolled back), response size cap, snapshots before closing unsaved documents.
- Skill references for Part, PartDesign, Sketcher, Assembly, TechDraw, VarSet/Spreadsheet and export; every snippet is tested against FreeCAD 1.1.
- Chat panel: conversation per document, link to allow a blocked tool, change/error summary per answer, picture of the 3D view.
- Fixed: tool results were double-encoded by the MCP SDK's structured output; `uvx` did not rebuild after source changes.
- Verified with six reference scenarios end-to-end from the chat panel and a 10-minute headless job with a responsive GUI.

## 0.1.0 – Phase 1: MVP on macOS (2026-10-31)

- Claude Code plugin `freecad` with marketplace `freecad-claude`, `/freecad:setup`, `/freecad:status`, SessionStart check and a `freecad` skill.
- MCP server `freecad-bridge-mcp` (MCP SDK 2.x) with `status`, `get_tree`, `get_object`, `run_python`, `document`, `export`.
- FreeCAD addon ClaudeBridge: autostart, discovery via instance files, Unix socket with mutual HMAC handshake, execution in the GUI thread with one undo transaction per call and rollback on errors, changes/errors in every response.
- "Claude" chat panel in FreeCAD: Claude Code as an asynchronous QProcess with cleaned environment, streamed answers, tool rows, stop button (SIGINT), session resume per document, selection as context, subscription or API key (macOS Keychain).
- Installer (`freecad-bridge install`) for macOS with backup of a previous addon and `--uninstall`; `freecad-bridge doctor`.
- 45 unit tests and 15 GUI integration tests; CI for unit tests, lint and plugin validation.

## Phase 0 – Spikes (2026-10-31)

- Added the implementation plan (kept locally) and `CLAUDE.md`.
- Spikes on macOS with FreeCAD 1.1.4: threading (1,000 calls in the GUI thread, rollback, one undo step per call), chat from FreeCAD via `QProcess` with `claude -p` and an MCP callback, `uvx` startup time, Report view, task dialogs. Results recorded in the local planning docs.
- Plan adjusted: cleaned environment for child processes, MCP SDK 2.x, Python pinned to 3.12, `--plugin-dir` and startup check in the chat panel, `--permission-prompts none`, SIGINT on stop, undo limit, repo name `Scharmen/FreeCadClaudeBridge`.
- Translated all repository content to English.
