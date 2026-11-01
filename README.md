# FreeCadClaudeBridge

Control FreeCAD by chat with Claude, either in a **"Claude" chat panel inside FreeCAD** or from **Claude Code**. Claude works on your open document through FreeCAD's Python API: it creates and changes geometry, reads the model tree and properties, and exports files. Every Claude step is exactly one undo step, and failed code leaves nothing behind.

> Status: 1.2.0, not yet tagged. Tested by hand on macOS; Linux (AppImage) and Windows (portable build) are tested in CI against FreeCAD 1.1.4, including the GUI on Linux. The release is tagged after the [manual acceptance](docs/manual-testing.md) on Windows 11 and Ubuntu. See [CHANGELOG.md](CHANGELOG.md).

## Install

Requirements: FreeCAD 1.1, [Claude Code](https://claude.com/claude-code) (logged in), and uv.

1. **Install uv** (once): `curl -LsSf https://astral.sh/uv/install.sh | sh` (macOS/Linux), `brew install uv`, or on Windows `winget install --id=astral-sh.uv -e`.
2. **Install the plugin in Claude Code:**
   ```
   /plugin install freecad --marketplace Scharmen/FreeCadClaudeBridge
   ```
   On Claude Code older than 2.1.275: `/plugin marketplace add Scharmen/FreeCadClaudeBridge`, then `/plugin install freecad@freecad-claude`.
3. **Set up:** run `/freecad:setup` in Claude Code. It finds FreeCAD (macOS app, Windows install or portable folder, Linux package or AppImage; pass `--freecad <path>` if it isn't found), copies the ClaudeBridge addon into FreeCAD's `Mod` folder and records the path to Claude Code. Then (re)start FreeCAD; the "Claude" panel appears docked on the right.

Check everything with `/freecad:status`.

**Settings:** FreeCAD → *Preferences → Claude Bridge* (or ⚙ → Settings… in the chat panel), `/freecad:settings` in Claude Code, or `freecad-bridge settings` in a terminal. There you set how much memory a headless job may use (as a share of this computer's RAM, plus what always stays free), its default time limit, and whether the bridge and chat panel start with FreeCAD.

**Update:** run `claude plugin marketplace update freecad-claude` and `claude plugin update freecad@freecad-claude` in a terminal (or turn on auto-update for the `freecad-claude` marketplace in `/plugin`), then restart Claude Code. The next session replaces the addon on its own; restart FreeCAD to load it.

**Remove:** `/freecad:setup --uninstall` removes the addon, token, config, instance files and the stored API key; add `--purge` to also delete snapshots, chat history and the audit log. Then `/plugin uninstall freecad@freecad-claude`.

## Use

- **In FreeCAD:** type into the Claude panel, e.g. "Make a 40×30×10 mm block with a 10 mm hole in the middle and export it as STEP to ~/Desktop/block.step". The active document and your current selection are sent along, so "make this face 2 mm thicker" works. Tool calls show up as rows, followed by a summary of created/changed objects and any errors; **Stop** ends the turn. Each document keeps its own conversation; 📷 inserts a picture of the 3D view. If Claude needs a tool the panel doesn't allow, a link lets you allow it for that conversation. Toggle the panel with Ctrl+Alt+K or the *Claude* menu of the "Claude Bridge" workbench.
- **In Claude Code:** just ask; the `freecad` tools talk to the running FreeCAD.
- **Several FreeCAD windows:** the chat panel always works on the FreeCAD it belongs to. In Claude Code, calls go to the most recently started FreeCAD and the result says so; ask Claude to switch ("work in the FreeCAD that has Flange open"), which uses `instances` and `select_instance`. The choice lasts for the Claude Code session.
- **Login:** the panel uses your Claude subscription (Claude Code's login) by default. Switch to pay-as-you-go with **⚙ → Use API key**; the key is stored in the macOS Keychain, Windows Credential Manager or Linux Secret Service, never in FreeCAD's settings.

## Tools

21 tools; in Claude Code they are named `mcp__plugin_freecad_freecad__<tool>`.

- **Read:** `status`, `instances`, `get_tree`, `get_object`, `inspect_shape` (faces/edges with geometry), `inspect_sketch` (constraints, degrees of freedom, conflicts), `get_changes` (what you did in the GUI meanwhile), `get_console` (Report view), `api_help` (API of the installed FreeCAD), `list_commands`.
- **Act:** `run_python` (one undo step per call, rolled back on errors), `run_command`, `task_dialog`, `selection`, `document`, `export`, `import`, `view` (optional screenshot), `run_headless` (heavy work in a separate `freecadcmd` process so FreeCAD stays responsive; stopped before it exhausts memory, with a budget derived from the machine's RAM), `snapshot`, `select_instance`.

Allow all of them with `mcp__plugin_freecad_freecad__*` in `permissions.allow` (note that `run_python` runs arbitrary code in FreeCAD). The bundled skill carries tested FreeCAD 1.1 patterns for Part, PartDesign, Sketcher, Assembly, TechDraw, VarSet/Spreadsheet and export.

## Troubleshooting

`/freecad:status` checks everything and prints a fix for each problem. The common ones:

| Message | Fix |
| --- | --- |
| `NOT_RUNNING` | Start FreeCAD. If it runs but the Claude panel is missing, run `/freecad:setup` and restart FreeCAD. On Windows, security software may block 127.0.0.1. |
| `AUTH_FAILED` | Claude Code and FreeCAD use different tokens: run `/freecad:setup`, restart FreeCAD. |
| `VERSION_MISMATCH` or "FreeCAD still runs ClaudeBridge x" | Restart FreeCAD; if that is not enough, `claude plugin update freecad@freecad-claude` and `/freecad:setup`. |
| `GUI_BUSY` / `TIMEOUT` | FreeCAD is busy with a long operation. Wait, and let Claude use `run_headless` for heavy geometry. |
| FreeCAD not found by `/freecad:setup` | `/freecad:setup --freecad <FreeCAD.app, install folder or AppImage>` |

## Security

The addon listens only locally (Unix socket with permissions 0600; TCP on 127.0.0.1 on Windows) and both sides authenticate with a 32-byte token (mutual HMAC-SHA256); rotate it with *Claude → Rotate token* in FreeCAD or `freecad-bridge token --rotate`. Every call, with its code, is recorded in an audit log in the bridge folder (*Claude → Open audit log*). `run_python` is full code execution with your user rights by design; Claude Code's permission prompts are the second barrier. Treat FCStd files from unknown sources like code. Details and known limits: [docs/security.md](docs/security.md).

## Development

```bash
make test     # unit tests, no FreeCAD needed
make itest    # GUI integration tests against a running FreeCAD with the addon (incl. every skill snippet)
make lint
make validate # claude plugin validate .
```

Layout: `plugin/` is the Claude Code plugin (`server/` = Python package `freecad-bridge`, `addon/ClaudeBridge/` = FreeCAD addon, `skills/`, `commands/`, `hooks/`). `uvx --from plugin/server` rebuilds automatically after code changes (sources are uv cache keys). Restart FreeCAD to load a changed addon. Conventions are in [CLAUDE.md](CLAUDE.md).

## License

MIT
