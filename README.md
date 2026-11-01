# FreeCadClaudeBridge

Control FreeCAD by chat with Claude, either in a **"Claude" chat panel inside FreeCAD** or from **Claude Code**. Claude works on your open document through FreeCAD's Python API: it creates and changes geometry, reads the model tree and properties, and exports files. Every Claude step is exactly one undo step, and failed code leaves nothing behind.

> Status: 0.1.0 (Phase 1 MVP, macOS). Windows and Linux follow in Phase 3. See [docs/PLAN.md](docs/PLAN.md).

## Install

Requirements: FreeCAD 1.1, [Claude Code](https://claude.com/claude-code) (logged in), and uv.

1. **Install uv** (once): `curl -LsSf https://astral.sh/uv/install.sh | sh` (macOS/Linux) or `brew install uv`.
2. **Install the plugin in Claude Code:**
   ```
   /plugin install freecad --marketplace Scharmen/FreeCadClaudeBridge
   ```
   On Claude Code older than 2.1.275: `/plugin marketplace add Scharmen/FreeCadClaudeBridge`, then `/plugin install freecad@freecad-claude`.
3. **Set up:** run `/freecad:setup` in Claude Code. It finds FreeCAD, copies the ClaudeBridge addon into FreeCAD's `Mod` folder and records the path to Claude Code. Then (re)start FreeCAD; the "Claude" panel appears docked on the right.

Check everything with `/freecad:status`. Remove it with `/freecad:setup --uninstall`, then `/plugin uninstall freecad@freecad-claude`.

## Use

- **In FreeCAD:** type into the Claude panel, e.g. "Make a 40×30×10 mm block with a 10 mm hole in the middle and export it as STEP to ~/Desktop/block.step". The active document and your current selection are sent along, so "make this face 2 mm thicker" works. Tool calls show up as rows; **Stop** ends the turn. Toggle the panel with Ctrl+Alt+K or the *Claude* menu of the "Claude Bridge" workbench.
- **In Claude Code:** just ask; the `freecad` tools talk to the running FreeCAD.
- **Login:** the panel uses your Claude subscription (Claude Code's login) by default. Switch to pay-as-you-go with **⚙ → Use API key**; the key is stored in the macOS Keychain, never in FreeCAD's settings.

## Tools (Phase 1)

`status`, `get_tree`, `get_object`, `run_python`, `document`, `export`. In Claude Code they are named `mcp__plugin_freecad_freecad__<tool>`; allow all of them with `mcp__plugin_freecad_freecad__*` in `permissions.allow` (note that `run_python` runs arbitrary code in FreeCAD).

## Security

The addon listens only locally (Unix socket with permissions 0600) and both sides authenticate with a 32-byte token (mutual HMAC-SHA256). `run_python` is full code execution with your user rights by design; Claude Code's permission prompts are the second barrier. Treat FCStd files from unknown sources like code.

## Development

```bash
make test     # unit tests, no FreeCAD needed
make itest    # GUI integration tests against a running FreeCAD with the addon
make lint
make validate # claude plugin validate .
```

Layout: `plugin/` is the Claude Code plugin (`server/` = Python package `freecad-bridge`, `addon/ClaudeBridge/` = FreeCAD addon, `skills/`, `commands/`, `hooks/`). `uvx --from <path>` caches by version; use `uvx --refresh` after code changes. Details in [CLAUDE.md](CLAUDE.md) and [docs/PLAN.md](docs/PLAN.md).

## License

MIT
