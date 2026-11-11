# Changelog

## 1.22.0 – long chats that stay cheap, no lost work (unreleased)

- The chat panel saves the models Claude changed after each answer (setting `chat.autosave`, on by default). Work that lived only in the open FreeCAD was lost when FreeCAD closed, as happened to a whole day on the play board's connector test; a model without a file gets a note to save it. The bridge's safety copies stay as well.
- A sub chat whose part is missing from the model says why that usually happens and offers the newest safety copy that still has the part (found by reading the copies' `Document.xml`), opened as a separate, unsaved document. It used to say only "no longer in the model".
- New Claude models appear in the model list without a conversation. The list came only from a session's start, so a new model (Fable 5.2) showed up after the first message. The panel now asks Claude Code for its list when FreeCAD starts and every 6 hours (the same `initialize` request, no message, no tokens), keeps your choice and names new models in the chat.
- Long chats cost about half: no chat was ever compacted, so real sessions grew to 370k–608k tokens of context that every request re-read (220M input tokens over the five largest chats). Past `chat.compact_at_k` (200k tokens by default, 0 = never) the panel now asks Claude Code to compact the chat after an answer: wishes, part names, dimensions, fits, decisions and open problems stay; code, pictures and reasoning go. Replayed on the real context histories this saves 51 % of the input tokens at 250k and 68 % at 150k.
- A new sub chat is compacted before its first message instead of inheriting the whole main chat (the connector sub chat started at 400k tokens; 33M of its 43M input tokens were inherited history).
- New tool `print_export`: the print kit behind the 3D print button, for Claude. Coloured 3MF and STL of the whole model, moving parts apart, plates by colour (`mode="split"`), PDF guide, printer presets (Anycubic Kobra S1 and others), into the model's `print/` folder; FreeCAD stays responsive while it runs. Claude used to build 3MF files by hand: 15–26 shell calls and 8–23 `run_python` calls per printing project.
- New tool `slice_check`: a slicer of the bridge's own, with no slicer program on the machine. The model is cut into layers (a grid of half a nozzle), and the print is simulated from the bed up: what does each layer rest on? It names parts that start in mid air, arms that hang free (with length, height and place), bridges and whether they are short enough, slopes steeper than 45°, walls thinner than the nozzle, a model too large for the bed and a small footprint. Supports are grown down from what hangs and priced (`with_supports`: extra grams and minutes, and whether they stand on the model). It writes G-code (walls, solid and sparse infill, supports; generic Marlin/Klipper style with `;TYPE:` comments) into `print/gcode/` for a G-code viewer, with an estimate of time and filament for the printer preset, nozzle, layer height and material (PLA, PETG, ABS, ASA, TPU). numpy only; a 150 mm model with 465 layers takes about 4 s. The G-code has not been printed on a real machine, and the time is a cautious estimate at moderate speeds.
- No "no sign of life" warning while a tool runs. A check or a long script takes minutes, and after two minutes the chat called it possibly stuck ("check_all has given no sign of life for 2:00"). A running tool now only shows its clock in the status line ("Running check_all · 2:05 · total 2:10"); the warning stays for silence from Claude itself (`chat.stall_seconds`).
- `run_python`: when code fails on an object that does not exist (`'NoneType' object has no attribute …` after `doc.getObject(name)`), the error now says so and lists the document's object names, so Claude corrects the name in one step instead of searching.
- FreeCAD no longer freezes on mesh repair. A chat ran `fixDegenerations` on a fine mesh in the open FreeCAD: a C++ loop that Python cannot interrupt, so the time limit did nothing and FreeCAD hung for more than 25 minutes until it was force-quit. `run_python` now refuses `fixDegenerations`, `fixSelfIntersections`, `fixDeformations` and `fixCaps` and sends that work to `run_headless`, which is stopped at its time limit. And for every other call that hangs: while `run_python` runs, the GUI thread's stack is written to `stuck-<pid>.txt` every 30 s, so a timed-out or busy call now names the line FreeCAD hangs in and tells Claude to inform the user instead of working on.
- `make_part` families for toys: `stud_plate` (DUPLO-compatible grid: pitch 16 mm, hollow studs 9.35 × 4.4 mm) and `tile_connector` (a bow-tie key dropped into pockets from above, printable flat without supports; `coupon=true` gives two test plates and three keys with different clearances). The default clearance 0.2 mm has not been print-tested.
- `design_notes(add)` answers with a confirmation instead of the notes Claude just wrote (2.2k characters per call that stayed in the context).
- Benchmark: three edit tasks (`edit_bearing_seat`, `edit_flange_size`, `edit_add_holes`, starting from a finished model) and `--effort`, to measure follow-up edits. Not part of the default run.
- Skill: tools are called by their full name. Claude called `get_tree` or `design_notes` by the short name the skill uses, got "No such tool available" and retried, in nearly every session; the skill now says so up front (edit tasks: 1 tool error instead of 4, 16 % fewer input tokens).

## 1.21.1 – a smoother chat panel (2026-11-08)

The `waving_person` and `toy_car` benchmark tasks ran for the first time (Sonnet and Haiku). Both models reached for the new families, the pose and `one_piece`; what they got wrong was ours:

- The person template's `arm_r` joint was the figure's left arm (it faces -Y, so its right is at -X); every "waving with the right arm" waved with the left. The joints and `Shoes_R`/`Shoes_L` are mirrored to match; the template says which side is which.
- Joints were not scaled with the template, so a pose at any height but the template's own turned about a pivot in the wrong place (slightly, at 80 mm; badly, at 40).
- `height` is now the finished figure's height, pose included: a raised hand must not grow an 80 mm figure to 88. Both models spent calls scaling the figure back.
- Chat panel: long thinking shows whether it is moving. Claude Code sends no thinking text, and each thinking update carries only the size of its step (50–150 tokens), not a total; the panel showed the largest step as if it were the count, so a 15-minute plan read "~150 tokens" and looked stuck. The steps are now added up, and the status line shows how fast the count grows ("+40/s") or how long it has not grown ("no new tokens for 1:20"). After five minutes of thinking the chat says once that Claude is still planning. The stall warning fires only when the count stops growing. When Claude Code retries a failed API request (overloaded, rate limit, no answer), the panel says so with the error and a countdown instead of showing silent thinking; other unknown system events go to the panel log.
- Chat panel: the ＋ button (and `/new`, Delete, switching conversations) works again after messages sent while Claude was working. Claude Code folds such messages into the running turn and answers them with one result, but the panel counted one result per message, so it stayed "busy" for good and silently ignored ＋. The panel now follows the turns Claude Code reports (start and result) instead of counting, and ＋ during a running turn says to press Stop first.
- Chat panel: "Effort: auto" says what it means and which level it runs at ("Effort: auto → medium"): with no level chosen Claude Code uses its default for the model (medium for Opus, Sonnet and Haiku, high for Fable); after each answer the panel reads the level actually used from the session file. The addon version shows in the panel's top row and the ⚙ menu. Model and effort share the first control row, permission mode and Auto the second, so no list is cut to a few letters. Every effort level and permission mode explains itself on hover, and so do ⚙, Stop, Send, the approval buttons, the panel's float and close buttons, and the buttons inside the chat (answer options show Claude's description of each option).
- Chat panel: checks once a day for a newer ClaudeBridge and updates from FreeCAD. The check goes through the plugin marketplace as the plan requires: Claude Code refreshes the `freecad-claude` marketplace (a folder marketplace that is a git checkout gets `git fetch`), and the addon itself makes no network requests. A newer version turns the version label orange and puts an Update now button in the chat; ⚙ → Check for updates checks at once. Updating refreshes the Claude Code plugin (`claude plugin update`, or `git pull --ff-only` for a folder checkout) and installs the addon from it, so plugin, server and addon keep one version; FreeCAD loads it after a restart. The daily check can be turned off in the preferences.
- Chat panel: a conversation opens its model. Clicking a conversation whose model is closed now opens the file (FreeCAD 1.1's `Gui.open` silently did nothing when called from code, so neither the model nor the conversation switched) and brings its 3D view to the front; the conversation resumes its own Claude Code session with its history. A conversation from "In this folder" (terminal, Desktop app, older panels) is linked to the model it works on, read from its session (the context line, else the last saved .FCStd; the bridge's safety copies never count), instead of to whatever document was active. The `[FreeCAD]` context line now names the model's file, so Claude knows which file the chat belongs to.
- Chat panel: sub chats for one part of a model. Right-click a chat → New sub chat for part, or type `/sub Roof` (no name: the selected part); nothing opens one on its own. A sub chat starts as a fork of its main chat (`--fork-session`), so it knows everything the main chat knew, and every message tells Claude to change only that part and keep its position and fit. The parts stay in the main model's document, so the main chat always holds the full model. Sub chats sit indented under their main chat; opening one selects and zooms to its part, going back to the main chat shows the whole model again.
- Chat panel: the plan shows right after an upgrade ("Claude Max 5x"). Claude Code's `subscriptionType` comes from the login token and still said "pro" after the upgrade; the panel now reads the account profile Claude Code keeps in `~/.claude.json` (organization type and rate-limit tier), every minute and after each answer.
- Sub chats are easy to see and to ask for: main chats show "🧩 N sub chats", sub chats sit on a tree line under them, and the new `sub_chat` tool lets Claude offer them when you ask in the chat ("🧩 Roof" buttons; one click opens it, nothing opens by itself).
- One folder per model instead of files loose in the home folder: `<projects folder>/<Name>/<Name>.FCStd` with `exports/`, `print/` and `references/`. The projects folder is a preference (default `~/Documents/FreeCAD Projects`); `status` and the `[FreeCAD]` line name it, a bare file name in `export` or `save_as` lands there, new chats start there and the 3D print button writes to `print/`. A model saved loose stays where it is.
- A richer chat: your messages as tinted bubbles, Claude's answers under an accent header, one symbol per tool with its ✓ and time on the same line, changes as a small table (➕ ✏️ 🗑), a ⏱ footer per answer, notes with ℹ️ and errors on a red band; Markdown tables get lines, a header row and zebra rows, and Claude is asked for tables and status symbols. Colours follow FreeCAD's light or dark theme.
- Chat panel: tables whose cells hold line breaks render completely. Claude writes `<br>` inside table cells, and Qt's Markdown reader then dropped the rest of the table and all text after it; such breaks become " · " in a cell and a Markdown line break elsewhere (also in the design notes view).
- Chat panel: ↗ Open in Claude Desktop works. `claude --desktop` refuses to run without a terminal, and the panel started it detached without one, so nothing happened while the chat said it had opened. It now runs under `script` (a pseudo-terminal) on macOS and Linux and in a console window on Windows, waits until the panel's own Claude Code process has ended (Claude Code refuses a session that still runs), and reports what Claude Code answered. A chat that is already open in Claude Desktop is brought up there through its `claude://` link. A panel chat appears in Claude Desktop's list once it has been opened there.
- Skill: meshes, holes and repair. Claude walked mesh facets with `NeighbourIndex`, which does not exist (`AttributeError`); the facet attribute is `NeighbourIndices` (`-1` = open edge). `printing.md` now has a tested section on finding holes with the mesh's own checks (`isSolid`, `hasNonManifolds`, `hasSelfIntersections`, `countComponents`) and repairing a copy (`harmonizeNormals`, duplicates, `fillupHoles`, non-manifolds, self-intersections); the API notes name the right attribute.
- FreeCAD no longer freezes while Claude's script runs. `run_python` still runs in the GUI thread (FreeCAD's API is not thread-safe), but between lines it now lets FreeCAD redraw about every 100 ms, without user input (clicks wait until the call is done, so nothing changes the document mid-transaction) and spending at most a tenth of the time on it. A 6 s script froze the window for 6 s; now the longest pause is about 0.2 s, at 3–4 % more run time. A single long C++ operation (one big boolean, a recompute) still blocks until it returns. Setting `chat.redraw_ms` (Preferences → Claude Bridge; 0 = old behaviour).
- Chat panel: sub chats stay in the list before their first message. A sub chat starts (forks its main chat) with its first message; until then it was shown only while open and vanished when you went back to the main chat, and sub chats Claude offered appeared only as buttons. Offered and opened sub chats are now listed under their main chat as "not started yet" (several per model), open with a click, count in the main chat's 🧩 badge, and can be removed with a right-click.
- Chat panel: links to the files Claude made. After each answer, "📂 New files" lists every file written in that turn (exports, saves, script output; found in the tool calls and their results, kept only if it exists and is new): the name opens it with its app, "📁 Show in Finder" (Explorer on Windows) shows it selected in its folder. Temporary files and the bridge's own copies are left out; nothing that would run when opened is linked.
- `export` takes the format in any case (`STL` as well as `stl`); Claude used to lose a turn on the upper-case spelling.
- A relative path in `export` or `save_as` (`exports/x.stl`) now counts from the model's project folder; it used to land wherever FreeCAD's working directory happened to be.
- README: about a quarter of its former length, with 3D-printable models named as the project's main focus, a gallery of recent models (dollhouse, Tonie storage house, play board, Labrador) next to the RS5, and a new panel screenshot; details moved into a collapsed section and the CHANGELOG.
- A raised arm keeps its sleeve on the shoulder: a capsule or strand that crosses a joint leaves its points on the body side of the pivot where they are. Before, the sleeve started above the shoulder pivot, so at `arm_r: 150` that end swung down and hung beside the torso as a lobe while the arm went up bare (found in the re-run after these fixes).
- The person template's hair is locks: 64 short strands on the scalp on top of the shell, so the head no longer reads as a cap.
- `make_part` `wheel`: on toy wheels (rim 12–15 mm, bore 3 mm) the hub formula left no room for spokes and the rim came out as a solid disc; the hub and the rim's wall now scale with the rim, and a wheel with no room for spokes is refused with the sizes that would work.
- Benchmark: the judge also gets a left view, so both ends of a car are seen head-on whichever way it points (the toy cars lost their grille and tail lamps to a view that was never taken).

## 1.21.0 – detail that reads (2026-11-07)

- `make_part` families for vehicles: `wheel` (tyre with rounded shoulders and tread grooves, spoked rim with hub and axle bore, brake disc and caliper), `lamp` (housing with an open front, reflector bowl, lens, LED strip; rect or round) and `pattern` (honeycomb, slats, mesh or round holes as a panel to fuse or as cutters to subtract, for grilles, vents and diffusers).
- `compare_to_picture(crop=[x0, y0, x1, y1], look_at=[...], zoom)`: a part of the picture enlarged next to a close-up of the matching objects, so a lamp or a grille can be checked at the size it is seen.
- `sculpt`: the `hair` preset places 20–80 tapered strands on a scalp (short locks along the head, long, bob, beard, fur) with one key; templates take a `pose` (person: head, arm_r, arm_l; dog: head, tail) that turns everything in the joint's region, sleeve and fur included.
- Benchmark tasks `waving_person` and `toy_car` for figures with a pose and vehicles with wheels, lamps and a grille.
- Code: the chat panel (`panel/dock.py`, 1 976 lines) is split into `common` (settings, paths, HTML helpers), `widgets` (input box, chat view, conversation rows), `sidebar`, `approvals` and `attachments` mixins and a 1 000-line `dock`; the print engine (`printkit/engine.py`, 865 lines) into `common`, `pieces`, `moving`, `joints`, `writers` and a 200-line `engine`. No behaviour change; the print tests and a panel built in a running FreeCAD cover it.

## 1.20.0 – models that pass their own checks (unreleased)

- `make_part` family `thread`: an external thread on any diameter (bottle neck, lid, plug) as a solid to fuse onto the neck, with a lead-in chamfer and an optional thicker core, or an internal thread as a cutter for a cap, clearance wider for printing. Every model in the benchmark had cut the bottle's thread by hand and got the pitch wrong (9.6 to 130 mm instead of 3).
- `sculpt(one_piece=true)`: every part united into one closed mesh for printing, each keeping its own blend, detail and fur; parts that only touch (an eye in its socket) still join. A figure is one print, as the user asked.
- Sculpted meshes: small holes are closed before the import, each part says whether it is closed, and one that stays open says so in `part_warnings` instead of arriving as an unprintable mesh.

## 1.19.0 – leaner sessions (unreleased)

Measured on the benchmark logs: the same model, built by Opus in 96 tool calls for $2.87, by Sonnet in 12 for $0.25, with the same score. Most of the difference was check loops, pictures that stay in the context, and errors that cost a turn each. This release cuts all three.

- `check_all(spec, fits, section)`: design, print, fits and the four-view sheet on one saved copy in one `freecadcmd`, problems first with the check that found them. The skill says: once after building, once after the fixes.
- `sculpt` and `make_part` answer with a small front and side preview (two 400 × 300 views), so no `view` call follows them. `view` takes a view name as action, `look_at=[objects]` and `zoom` for close-ups, and its default picture is 640 × 480; `inspect_views` and `compare_to_picture` are smaller too.
- Errors that used to cost a turn: a check that failed with any exception came back as a bare "Error executing tool" (98 times over the benchmark runs); it now says the type and the message. `design_notes` accepts plain sentences ("… because …") and `text=`; `check_fit` accepts `parts=[a, b]`; `api_help` on a tool name answers from the tool.
- 35 tools become 28: `instances` and `select_instance` are `status(select=...)`, which also carries the learned preferences (`get_preferences` is gone); `inspect_shape` and `inspect_sketch` are `get_object(level=...)`; `snapshot` is `document(action="snapshot"|"snapshots"|"restore")`; `list_commands` and `task_dialog` are `run_command(filter=...)` and `run_command(dialog=...)`. Tool descriptions shrink from 17 000 to 11 000 characters; the sculpt and spec grammars live in `references/organic.md` and the new `references/checks.md`.
- One source of rules: the panel prompts shrink to what the panel needs and point to the skill instead of repeating it. The skill asks for two checks, not ten, and one look per change.
- Shorter answers: results are cut at 16 000 characters (was 38 000), `changes.changed` at 50 names plus a count, the preferences at 30 lessons.
- Benchmark: records requests, final context size, pictures and input tokens per task and their means per model; `Read` and `Skill` are allowed, as in the panel (215 denied reads over the earlier runs were wasted turns).
- CI runs pyright (basic) on the server; the errors it found are fixed (a wizard that could pass `None` on, a memory probe without a match, a seat gap of `None`).

## 1.18.0 (unreleased)

- **3D print export button** (File menu and toolbar in every workbench, and in the chat panel). It asks for the printer and the assembly method only when used, then writes on a saved copy: the whole model as one coloured 3MF and STL; one plate set per colour with each part laid down for printing; holes and printed connector pins, or gluing; axles for parts that turn, such as wheels, so that they need no glue; and a PDF assembly guide with a picture for each step. Parts too thin to print alone go into a neighbour and are named for painting.
- Print export keeps assembly to a minimum. With moving parts it also writes everything fixed as one coloured print (with the axle sockets) plus the moving parts laid flat and their axles. Parts of one colour that touch always become one piece unless together they miss the bed or the kernel cannot really join them. Splitting by colour is ticked by default only when the model has more colours than the printer or does not fit its bed.
- `make_part` family `faired_body`: a smooth closed body from a few feature lines, fitted instead of interpolated, with sharp edges only at creases. It reports how far the surface stays from the given points.
- `check_surface`: glossy paint and reflection stripes from two sides, plus a count of ripples along lengthwise cuts, to find bumps the normal view hides.
- Skill: `references/surfaces.md`, how to build car and boat bodies.
- `make_part` springs: end coils ground over about 3/4 of a turn, pitch opens gradually over one turn. Gears: filleted tooth roots, no waist at 18 to 30 teeth.
- Measuring: a bore split into quarter faces counts as a hole; the volume is checked on triangles where the kernel is far off on long B-spline faces (a spring read 62 instead of 930 mm³).
- Skill: leave only what was asked for visible; a holder gets one seat for each item.
- `view`: no camera animation during the whole call, so a fit followed by a screenshot is not cropped. The benchmark's pictures had the same flaw.
- Person template: separate fingers, raised laces, thicker soles, deeper shirt folds, hair texture. Dog template: deeper fur.
- The server's instructions name `make_part` and the `sculpt` templates, so a model finds them before building by hand.
- Benchmark: the dog spec allows its sculpted parts (figure, eyes, nose, base).

## 1.17.0 – standard parts and your own library (unreleased)

- `make_part(family, params, at, replace)`: tested builders for gear, gear pair, spring, bolt, nut, bolt and nut, hinge, enclosure, snap box, bracket and knob. `family="list"` shows them with their parameters. Sizes that cannot work are refused with the reason. Each object stores how it was made; call again with `replace` to change sizes (no parametric expressions in the shapes).
- `design_library(action, query, id)`: search, show and delete what worked before. A `good` rating in `record_feedback` stores the code of that model with its words.
- The skill and the panel start from what exists: library, then `make_part`, then a recipe.
- New `person_standing` template: real 7.5-head proportions (head was a third too big, neck too thin), shoulders and sleeves that fit, shirt folds, jeans with waistband, belt loops, fly, pockets and back pockets, sneakers with laces and a separate sole, eyes with pupils, fingers. `dog_standing`: broader, shorter muzzle with a clear stop and brows, eyes and nose as their own coloured parts, deeper fur, toes. Both mesh on a finer grid (`cells` in a template).
- `flex_test` survives a crash in the geometry kernel and names the parameter values that cause it; the checks say a crash is a crash and warn against trying the same in the open document.
- Check tools: `check_design` takes a single number for `height` and `solids`, and explains a spec it cannot read.
- Fix: the thread check walked one line through the whole part and counted a bottle's base and shoulder as crests, so short threads failed although they were right. It now follows the helix around the axis, tells rings from a thread and takes a second instead of minutes.
- Fix: `check_design`, `check_print` and `check_fit` (and `run_headless`) could fail on documents with meshes although the script had worked: FreeCAD's progress text landed on the same line as the result. It is now removed from the output as well.
- Benchmark: the judge also sees bottom and rear views and three cross-sections, so it can tell a closed box with nothing inside from a good one; `--rejudge` applies this to saved runs.

## 1.16.0 – recipes that run (unreleased)

- New tested references: `shapes.md` (threads, internal threads, bottle necks, springs, twists, draft, patterns, the largest fillet that works), `mechanical.md` (ISO tables, Hole features, bearings and seats, fits, ribs), `printing.md` (limits per process, test coupon, snap box, hinges, teardrop holes), `multipart.md` (assemblies, clearances, order of work). All their code runs against FreeCAD in the tests.
- The skill says what to read before building and to write a design spec first.

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
