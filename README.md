# FreeCadClaudeBridge

Design **models for 3D printing by chat**: describe what you want to Claude in a chat panel inside FreeCAD (or from Claude Code), and Claude builds it in your open FreeCAD document, checks walls, overhangs and fits, and exports print-ready files. Every Claude step is one undo step.

The main focus of this project is printable models: toys, storage, figures, scale models and spare parts that come out of the printer ready to use.

![FreeCAD with the Claude panel next to an Audi RS5 at 1:24 that Claude built from photos](docs/images/freecad-rs5.png)

*An Audi RS5 at 1:24 (68 parts), built by Claude from photos for printing. Real screenshot, FreeCAD 1.1 on macOS.*

## Made by chat

Each built from a few sentences in the chat panel (renders of the real files):

| | |
|---|---|
| ![A modular Duplo-compatible dollhouse with furniture in every room](docs/images/gallery-dollhouse.png) | ![A storage house for Tonie figures](docs/images/gallery-tonie-house.png) |
| **Dollhouse** for a two-year-old: three modules that click together, furnished rooms | **Tonie storage house:** a slot for every figure, tiled roof |
| ![A 20 × 20 cm Duplo-compatible play board with a house, trees and a river](docs/images/gallery-playboard.png) | ![A sculpted Labrador on a base](docs/images/gallery-labrador.png) |
| **Play board:** 20 × 20 cm Duplo-compatible tiles | **Labrador:** a sculpted figure, one watertight print |

## Install

You need **[FreeCAD](https://www.freecad.org/downloads.php) 1.0+** and **[Claude Desktop](https://claude.ai/download)**, signed in (on Linux, [Claude Code](https://claude.com/claude-code) is enough).

**Double-click:** [Install FreeCAD Claude.command](installer/Install%20FreeCAD%20Claude.command) (macOS; first time: right-click → *Open*) or [Install FreeCAD Claude.cmd](installer/Install%20FreeCAD%20Claude.cmd) (Windows). **Or one line:**

```bash
curl -fsSL https://raw.githubusercontent.com/Scharmen/FreeCadClaudeBridge/main/install.sh | sh
```

```powershell
irm https://raw.githubusercontent.com/Scharmen/FreeCadClaudeBridge/main/install.ps1 | iex
```

The setup window checks what is missing, offers to install it, then adds the `freecad` plugin to Claude and the chat panel to FreeCAD. Run it again to repair.

<details><summary>Manual install, settings, update, remove</summary>

- **Manual:** install [uv](https://docs.astral.sh/uv/), then in Claude Code `/plugin install freecad --marketplace Scharmen/FreeCadClaudeBridge` and `/freecad:setup` (pass `--freecad <path>` if FreeCAD isn't found). `/freecad:status` checks everything.
- **Settings:** *Preferences → Claude Bridge* in FreeCAD (projects folder, startup, update check, memory and time limits of headless jobs), or `/freecad:settings`.
- **Update:** the panel checks once a day and offers **Update now**; or ⚙ → *Check for updates*. Restart FreeCAD to load a new version.
- **Remove:** `/freecad:setup --uninstall` (add `--purge` for history and snapshots), then `/plugin uninstall freecad@freecad-claude`.

</details>

## Use

![The Claude panel on the dollhouse: a main chat with a sub chat, a table answer and the turn footer](docs/images/panel.png)

- **Ask in the panel**, e.g. "a 40 × 30 × 10 mm block with a 10 mm hole, export it as STEP". Your active document and selection go along, so "make this face 2 mm thicker" works. ⚡ **Auto** skips the approvals; **Stop** ends a turn.
- **Conversations belong to models:** clicking one opens its model and continues the same session. For a model in parts, ask for **sub chats** ("open sub chats for the roof and the ground floor", or `/sub Roof`): each starts with the main chat's history and changes only its part.
- **One folder per model:** `~/Documents/FreeCAD Projects/<Name>/` with the model, `exports/`, `print/` and `references/`.
- **From pictures:** drop a photo or sketch into the chat; Claude can trace it, turn it into a relief, or compare the model against it.
- **Model, effort, permissions** above the chat; hover over any control for what it does. The header shows your plan and usage.
- **In Claude Code:** just ask; the `freecad` tools talk to the running FreeCAD.

## Printing

![The 3D print dialog next to pages of the generated assembly guide](docs/images/print-export.png)

- **3D print…** in the panel (or the File menu) asks for your printer and whether to plug or glue, and writes into the model's `print/` folder.
- **One print where possible:** the whole model as one coloured 3MF and STL; moving parts (wheels, knobs) apart with axles; split by colour with connector pins only when needed.
- **A PDF assembly guide** with what to print, the steps and what to paint.
- **Before export**, Claude checks walls, overhangs, fits and stability with `check_all`.

## Tools

29 tools (`mcp__plugin_freecad_freecad__<tool>` in Claude Code):

- **Build:** `make_part` (gears, springs, threads, hinges, boxes, wheels…), `sculpt` (people, animals), `image_to_3d`, `run_python`
- **Check:** `check_all`, `check_print`, `check_fit`, `check_design`, `check_surface`, `flex_test`, `compare_to_picture`
- **Work with FreeCAD:** `status`, `get_tree`, `get_object`, `document`, `view`, `selection`, `export`, `import`, `run_headless` and more

The bundled skill holds tested FreeCAD 1.1 patterns and guides for printable, good-looking models; screws, nuts and fits follow ISO sizes (ISO 4017, 4032, 4762, 10642, 273, 286).

## Troubleshooting

`/freecad:status` checks everything and names the fix.

| Message | Fix |
| --- | --- |
| `NOT_RUNNING` | Start FreeCAD; panel missing → `/freecad:setup`, restart FreeCAD |
| `AUTH_FAILED` | `/freecad:setup`, restart FreeCAD |
| `VERSION_MISMATCH` | Restart FreeCAD; else update and `/freecad:setup` |
| `GUI_BUSY` / `TIMEOUT` | FreeCAD is busy; heavy work goes to `run_headless` |

Stuck or crashed? ⚙ → **Create debug report** writes one file with everything needed (secrets removed).

## Security

Local only (Unix socket or TCP on 127.0.0.1) with a mutual token handshake; every call is in an audit log. `run_python` runs code with your rights by design, so Claude Code's permission prompts stay on. Treat FCStd files from strangers like code. Details: [docs/security.md](docs/security.md).

## Development

`make test` (unit tests), `make itest` (against a running FreeCAD), `make lint`, `make bench` (design benchmark in a separate FreeCAD). Layout and conventions: [CLAUDE.md](CLAUDE.md).

## License

MIT
