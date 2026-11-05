# Changelog

## 1.15.0 – check tools: numbers, not guesses (unreleased)

- `check_design(spec)`: the model against a numeric spec (sizes in any orientation, volume, holes, holes lined up between parts, gaps, interference, thread pitch, teeth, fillets, parameters, expressions, joints); every failed check says what it found.
- `check_print`: closed, wall thickness, overhangs in all six orientations with the best one to print in, bridges measured between the walls that hold them, bed contact and tipping, for FDM, resin or CNC. Works on solids and sculpted meshes; findings say where and how to fix them. Overlapping parts count as one print.
- `check_fit(a, b, kind)`: smallest gap and shared volume of two solids against press, sliding and loose fits per process, or your own range. `seated` and `snap`: a lid on its rim or a bearing on its shoulder may touch on flat faces of one direction; the room around it counts. A press fit accepts at most 0.2 mm of overlap depth.
- `flex_test`: changes each parameter (±20 % by default), rebuilds, and reports what breaks or does not follow.
- `inspect_views`: isometric, front, right and top in one image with axes, scale bars and the overall size; `section` adds a filled cross-section. The camera is put back.
- All of them work on a saved copy (the section too) in a separate `freecadcmd`: the document stays as it is, no undo step, and a crash cannot reach FreeCAD. The skill and the panel ask Claude to write a spec first and to clear every problem before finishing.
- Benchmark: three open briefs without dimensions (`battery_box`, `cable_clip`, `knob`) that also have to pass the print check; `--no-checks` blocks the tools for a before measurement. Spec and measuring moved to `freecad_bridge/checks`.

## 1.14.0 – people and animals that look real (unreleased)

- `sculpt` templates: `person_standing` (7.5 heads tall, T-shirt, jeans, sneakers, hair, round base), `dog_standing` (Labrador-type, short fur, oval base) and `cat_sitting` (fur flowing down and back). `{"template": ..., "height": mm, "parts": [...]}` scales one to the height; parts with a template part's name change it and add primitives, others are added.
- Fur and hair: `"fur": {"depth", "length", "width", "direction" | "from"}` carves grooves that run along a flow; the depth is capped at 0.3 × the groove width so the mesh stays clean.
- Finer and more even meshes: one grid for the whole scene at 1/300 of its size (was up to 1/200 per part), finest 1/400; cutting shapes no longer enlarge the grid; specks smaller than three voxels are dropped; a piece held by another part (an arm under a sleeve) is no longer reported as floating.
- The skill and the panel send every person and animal to a `sculpt` template instead of stacked primitives.
- Benchmark: `dog` and `person` tasks.
- Fix: `list_commands` could crash FreeCAD. It asked every command whether it is active, and FreeCAD's `Std_Expressions` (and other commands whose menu action is not built yet) dereferences a null action there; commands without an action now report `active: null`.
## 1.13.0 – design benchmark (unreleased)

- New design benchmark in `tests/design/` (`make bench`): 12 tasks (bearing housing, tube bracket, enclosure, snap box, gear pair, bolt and nut, spring, bottle, hinge assembly, parametric shelf bracket, a model from a picture, a cat figure) run through Claude Code like the chat panel does, in a separate FreeCAD with its own settings and bridge directory, so the user's documents, preferences and feedback are untouched.
- Each task has a numeric spec, scored headless from the saved file: sizes (orientation-free), holes and diameters, holes lined up between parts, gaps between parts, interference, tooth count, thread pitch, round and sloped faces, parameters and expressions, a flex test that changes parameters and checks the model still rebuilds, joints, and overlap (IoU) with a reference model. Every check says what it found. Known-good models score 100; the same file always scores the same.
- A second Claude checks the screenshots against a list of features per task (visual score, kept separate from the numeric score). `tests/design/baseline.json` holds the scores for Opus, Sonnet and Haiku.
- The chat panel's system prompts moved to `claudebridge/panel/prompts.py` (no FreeCAD import), so the benchmark uses exactly the panel's instructions.
## 1.12.0 – setup in a window (unreleased)

- The installer opens a setup window (Tk, from uv's managed Python): a checklist of FreeCAD, Claude Desktop, Claude Code, sign-in, plugin, panel and start, each with its state; missing items get a Download/Open button; questions come as dialogs; *Show details* streams what runs, with clickable links and an input field when a program asks for something (e.g. the sign-in). Check again / Run again re-run it.
- Double-click launchers: `installer/Install FreeCAD Claude.command` (macOS/Linux) and `installer/Install FreeCAD Claude.cmd` (Windows).
- `freecad-bridge wizard --gui` / `--no-gui`; the one-line installers use the window when started by a person, the terminal in CI, with `--yes` or `--no-gui`, and when no window can be opened. The wizard reports every step as an event, so the terminal and the window share one tested flow.
## 1.11.0 – Claude explains its design (unreleased)

- Claude explains why a model looks the way it does: before building, a short plan with the proportions, facts and assumptions it rests on (e.g. for a Labrador: body length about equal to shoulder height, hanging ears, otter tail, minimum leg thickness for printing); afterwards the main decisions, each with its reason and source (your request, your picture, a real-world reference, printability, or an assumption you can correct). Skill and panel instructions.
- New `design_notes` tool: the decisions are saved in the document as a "Design notes" text object (Markdown, saved with the file, one undo step per entry) and read back before the model is changed later. The panel shows "📝 Why it looks like this" after a turn that recorded notes.
## 1.10.0 – details first, picture check, real usage (unreleased)

- New `compare_to_picture` tool: the user's picture next to a screenshot of the model from the matching view (the current camera or front/left/top…), with a checklist to go through part by part; the camera is put back afterwards. Pictures attached in the panel come with their file path and the request to check the finished model against them and fix every difference.
- Details first: the skill, the panel's instructions and a learned preference ask Claude to list every feature before building, model small details at the right scale, look at the model from several sides and add what is missing; "no screenshots" is gone from the skill.
- Real usage in the panel instead of a thinking estimate: tokens per turn from every API call (thinking included), context fill against the model's context window, and the plan's 5-hour and weekly usage with reset times, as Claude Code reports them (`rate_limit_event`). Kept across restarts; `/usage` shows it in the chat.
- The model menu says which model "Default" is (e.g. "Default (recommended) · Opus 5.5"); tooltips show the model ID.
- Named views jump instead of animating, so screenshots right after a view change show the new view; `view` can return and restore the camera.
## 1.9.0 – better models from the start, organic shapes (unreleased)

- New `sculpt` tool for organic forms (figures, faces, hair, beards, animals, plants, rocks): parts described as primitives (sphere, ellipsoid, tapered capsule, strand, rounded box, torus, cylinder) that melt into each other with a blend radius, subtracted or intersected, with optional surface detail noise. It is computed outside FreeCAD (signed distance field, surface nets, Taubin smoothing) and imported as one closed mesh per part in a group, in one undo step, so it cannot crash FreeCAD. Pinched edges are split and the grid refined until every part is watertight; parts that fall apart into separate pieces are reported. Parts are matte unless they set `shininess`; peak memory stays around 0.6 GB on a 5 cm bust at 0.22 mm.
- Skill: `quality.md` (brief, reference, block-out, refine and check workflow; robust parametric sketches; finer tessellation, materials and colors for a realistic look; printability) and `organic.md` (technique per subject, sculpt rules for proportions, hair, faces and cloth, a checklist of what went wrong in first attempts, tested lofts through B-spline sections, tapered sweeps, B-spline surfaces and safe mesh building).
## 1.8.1 – fixes from the live test (unreleased)

- Panel events were dropped: the debug log call clashed with the events' own `kind` field, so everything except streamed text failed (stale status line, conversations not stored, no switch to a new document). Fixed, and event handling now has a safety net: an error is logged with its traceback and shown once with a debug report button instead of silently stopping the panel.
- Unsaved documents are keyed by their Uid instead of their name: nearly every new document is called "Unnamed", so conversations of unrelated projects mixed. The Uid survives FreeCAD's document recovery.
- Safety copies (`saveCopy`) no longer count as saving the document; they had moved the conversation to the copy's path.
- No false "no sign of life" after FreeCAD was busy in its GUI thread (the window could not update meanwhile); the status line shows the running tool before FreeCAD starts computing; run_headless, export and import may run long without a stall warning.
- "No document" is not marked as closed in the sidebar.

## 1.8.0 – live activity, crash safety, debug reports, projects (unreleased)

- Live status while Claude works, updated twice a second: thinking (with time and token estimate), running a tool (which one, for how long), writing the answer, waiting for your approval. A finished thought shows as "💭 Thought for N s" (Claude Code sends no thinking text, so it is no longer an empty link).
- Stall watchdog: after `chat.stall_seconds` (default 120 s) without any sign of life the chat says so, distinguishing "FreeCAD is still computing run_python" from silence, with Stop and Create debug report buttons.
- Crash safety: before a call that changes a document the addon notes what is running; if FreeCAD dies during it, the next start reports which call in which document crashed it. Modified documents are copied before Claude changes them (`safety.snapshot_minutes`, default every 5 min). The MCP server answers a call during which FreeCAD died with `FREECAD_CRASHED` and tells Claude not to retry it but to use run_headless; the skill now sends raw mesh building and other crash-prone work to run_headless. Found because building a mesh from raw facets crashed FreeCAD (SIGSEGV in MeshKernel::AddFacets).
- Debug reports (⚙ → Create debug report…, and buttons on errors, stalls and crashes): versions, settings, panel state, FreeCAD state, the conversation's end, the Report view, the panel log (every event, kept 7 days in `logs/`), the audit log, crash markers and the newest macOS crash reports, with keys, the token and e-mail addresses redacted.
- Conversations grouped by project (FreeCAD document) across all documents, with the project's name stored; the current project first, others marked "(closed)" when their document is not open. Clicking one in another project brings that document up (opening its file if needed). Order follows the last activity: opening a conversation no longer moves it, a new message does.
- Conversations stay with their document when it is saved for the first time or under another name, and follow into a document Claude creates during a turn, instead of the chat going empty.

## 1.7.0 – one-step installer (unreleased)

- `install.sh` (macOS/Linux, `curl … | sh`) and `install.ps1` (Windows, `irm … | iex`) start a setup wizard (`freecad-bridge wizard`), fetched straight from GitHub as a zip, so git is not needed.
- The wizard checks FreeCAD 1.0+, Claude Desktop (not required on Linux), Claude Code and the Claude sign-in (`claude auth status`). Anything missing is listed with its download page (freecad.org, claude.ai/download, claude.com/claude-code), which it offers to open, and nothing is installed until all is there. A missing Claude Code is installed with the official installer and the sign-in started with `claude auth login`, both after asking; uv is installed too if needed.
- Then it adds the marketplace, installs or updates the `freecad` plugin, installs the panel into FreeCAD from the installed plugin (a plugin read from a local folder uses that folder) and offers to start FreeCAD. Re-running updates or repairs; `--yes` takes the defaults without questions.
- CI runs both one-liners on fresh Linux and Windows runners without uv or FreeCAD: uv gets installed, the wizard stops with the right download links and installs nothing.

## 1.6.0 – approval buttons and Auto (unreleased)

- Approvals are buttons: green Allow once, blue Always, red Deny inside the chat card, plus a bar with real buttons above the input for the oldest open request, so it never scrolls out of sight.
- Questions Claude asks (AskUserQuestion) show each option as a button, in the chat and in the bar; plans from plan mode show with Accept plan.
- ⚡ Auto next to the permission mode: allows every action Claude asks for, answers its questions with the first (recommended) option and accepts its plans, without asking. Each automatic answer is logged in the chat; the switch is remembered and highlighted. Deny rules in the Claude Code settings still apply.

## 1.5.0 – the chat panel as a full Claude Code chat, drag and drop (unreleased)

- One persistent Claude Code session per conversation (`--input-format stream-json`), instead of one process per message. Messages typed while Claude works are queued; Stop interrupts only the current step.
- All Claude Code tools (files, shell, web, every MCP server), not only the FreeCAD tools. Whatever your Claude Code settings don't allow asks in the chat: an approval card shows the command, the edit or the file, with Allow once / Always (Claude Code's suggested rules) / Deny. The FreeCAD tools stay pre-approved.
- Model, effort and permission mode (ask, accept edits, plan, auto, bypass) as in Claude Code; the lists come from Claude Code itself (your plan's models with their effort levels). Slash commands with completion: Claude Code's own commands, skills and plugin commands, plus /clear, /model, /effort, /mode, /desktop, /help handled by the panel.
- Answers render as Markdown; thinking shows as a collapsed line to open; TodoWrite task lists show as a checklist; the status line shows time, steps and, with an API key, cost. The header shows your plan.
- Drag and drop: pictures (dropped or pasted) are sent as real images with thumbnails above the input; other files and folders become references Claude can read; CAD files (STEP, IGES, STL, OBJ, 3MF, DXF, SVG, FCStd …) get an "Import into FreeCAD" link (one undo step); objects dragged from the model tree become `[Object: Label (Name), type, size]` references. Pictures in the chat can be dragged out; selected text can be dragged into other apps or the Python console.
- Sidebar: drag to reorder a document's conversations, pin to the top, move a conversation to another open document.
- Denials and feedback are answered in the input line instead of modal dialogs (a dialog opened from a Python slot holds the GIL and stalls the bridge).

## 1.4.0 – several conversations, shared with Claude Code and the Desktop app (unreleased)

- The chat panel keeps several conversations per document in a sidebar on the left, like the Claude Desktop app: two-line rows (title, source and time), ＋ for a new one, right-click to rename, delete or open in the Desktop app, ☰ to hide the list. Width and visibility are remembered; the panel is widened once to make room.
- Every conversation is a normal Claude Code session. The list also offers the other sessions in the document's folder (started in a terminal or the Claude Desktop app) to continue in the panel; ↗ (or ⚙ → Open in Claude Desktop) opens the current conversation in the Desktop app with `claude --desktop --resume <id>`.
- When a conversation went on outside the panel, switching back redraws it from the session file, so nothing is missing.
- A conversation is resumed in the folder its session lives in, so saving an unsaved document no longer loses it. The old one-session-per-document map is migrated.

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
