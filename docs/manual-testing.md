# Manual acceptance test (Windows 11 and Ubuntu)

CI already runs FreeCAD 1.1.4 on Windows (portable build, headless) and Linux (AppImage, headless and GUI under xvfb). This checklist covers what CI cannot: a real desktop session, a real Claude login, the Windows installer build, a Linux distro package, and the chat panel with Stop and Ctrl+Z. Run it once per platform; a release is tagged only after both pass.

Note each step as ✅ / ❌. For a ❌ keep the exact message and the files listed under [What to send back](#what-to-send-back).

## 0. Prerequisites

| | Windows 11 | Ubuntu 24.04 |
| --- | --- | --- |
| FreeCAD 1.1 | **Installer** `FreeCAD_1.1.x-Windows-x86_64-installer.exe` from the [releases page](https://github.com/FreeCAD/FreeCAD/releases) (CI covers the portable `.7z`) | **AppImage** from the releases page (`chmod +x`, put it in `~/Applications`; needs `sudo apt install libfuse2t64`) and, as a second run, a **distro package** from the PPA: `sudo add-apt-repository ppa:freecad-maintainers/freecad-stable && sudo apt install freecad`. The plain Ubuntu archive ships an older FreeCAD, so check `freecadcmd --version` shows 1.1 |
| Claude Code | `irm https://claude.ai/install.ps1 \| iex` in PowerShell, then `claude` and log in | `curl -fsSL https://claude.ai/install.sh \| bash`, then `claude` and log in |
| uv | `winget install --id=astral-sh.uv -e` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| API key test | an Anthropic API key (step 6) | same; also `sudo apt install libsecret-tools` so the key goes to the Secret Service |

Open a **new** terminal after installing so `claude` and `uv` are on PATH.

## 1. Install the plugin

In Claude Code (any folder):

```
/plugin install freecad --marketplace Scharmen/FreeCadClaudeBridge
```

Then restart Claude Code. ✅ when `/plugin` lists `freecad` as enabled and the session starts without a hook error.

## 2. Set up the addon

```
/freecad:setup
```

✅ when it prints `Installed ClaudeBridge 0.2.0 for FreeCAD 1.1.x` and the addon path is:

- Windows: `%APPDATA%\FreeCAD\v1-1\Mod\ClaudeBridge`
- Ubuntu: `~/.local/share/FreeCAD/v1-1/Mod/ClaudeBridge`

If FreeCAD isn't found, rerun with `--freecad "<path>"` (the install folder, e.g. `C:\Program Files\FreeCAD 1.1`, or the `.AppImage` file) and note it as ❌ for auto-detection.

## 3. Start FreeCAD and check the bridge

Start FreeCAD normally (Start menu / app launcher / double-click the AppImage).

- ✅ The **Claude** panel is docked on the right. Report view shows `ClaudeBridge 0.2.0 ready`.
- In Claude Code run `/freecad:status`. ✅ when it shows the running instance, a successful handshake and matching versions.
- Menu *Claude Bridge* workbench → *Claude* → *Bridge status*. ✅ shows the address (Windows: `127.0.0.1:<port>`, Ubuntu: a `.sock` path) and `GUI thread: idle`.

## 4. Chat panel with the subscription

Create a new document (Ctrl+N), then type into the panel:

1. `Make a 40×30×10 mm block with a 10 mm hole through the middle.`
   ✅ Tool rows appear while it works, the block with the hole is in the 3D view, and a change summary lists the new objects.
2. Select the top face in the 3D view, then: `Make this face 2 mm higher.`
   ✅ The block is now 12 mm high (Claude knew which face you meant).
3. Press **Ctrl+Z** in FreeCAD once.
   ✅ Exactly the last Claude change is undone (back to 10 mm), not more.
4. `Export it as STEP to my Desktop as block.step.`
   ✅ The file exists and opens in FreeCAD.
5. Send `Make 200 small cubes in a 20×10 grid, one run_python call per cube.` and press **Stop** after a few seconds.
   ✅ The turn ends within ~3 s, the panel is usable again, FreeCAD stays responsive, and the cubes created so far remain (each a separate undo step).
6. Click 📷. ✅ A picture of the 3D view appears in the input.
7. Open a second document and ask something there, then switch back.
   ✅ Each document shows its own conversation.

## 5. Claude Code side

In Claude Code (FreeCAD still open):

1. `List the objects in my FreeCAD document.` ✅ It uses `get_tree` and lists them.
2. `In FreeCAD, run 1/0 with run_python.` ✅ An error comes back and the document is unchanged (nothing to undo).
3. `Export the active document as STL using run_headless.` ✅ The file is written while FreeCAD stays responsive (you can rotate the view during the job).

## 6. API key mode

1. In the panel: **⚙ → Use API key**, paste the key.
2. Check where it went:
   - Windows: `cmdkey /list:freecad-bridge-anthropic-api-key` lists one entry.
   - Ubuntu: `secret-tool lookup service freecad-bridge-anthropic-api-key account $USER` prints the key. (Without `libsecret-tools` the fallback is `~/.local/state/freecad-bridge/api_key` with permissions 600; note which one you got.)
   - ✅ The key does **not** appear in FreeCAD's `user.cfg` (search the file for `sk-ant`).
3. Ask `What is 2+2?` in the panel. ✅ It answers (billed to the API key).
4. **⚙ → Use subscription**, ask again. ✅ It answers; the key entry from step 2 is gone.

## 7. Restart and uninstall

1. Quit and restart FreeCAD. ✅ The panel is back where it was; `/freecad:status` connects again.
2. `/freecad:setup --uninstall`, then `/plugin uninstall freecad@freecad-claude`.
   ✅ The `Mod/ClaudeBridge` folder and the bridge folder's token/config are gone; FreeCAD starts without the panel.

## What to send back

The ✅/❌ list per platform plus, for any ❌:

- the message on screen and the output of `/freecad:status`
- FreeCAD's Report view (right-click → Copy all)
- the bridge folder contents (except `token` and `api_key`):
  - Windows: `%LOCALAPPDATA%\freecad-bridge`
  - Ubuntu: `~/.local/state/freecad-bridge`
- Windows version (`winver`) or `lsb_release -a`, and the FreeCAD build (installer, AppImage or PPA package)
