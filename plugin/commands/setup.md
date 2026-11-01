---
description: Install or update the ClaudeBridge addon in FreeCAD (add --uninstall to remove it)
argument-hint: "[--freecad <path to FreeCAD.app>] [--uninstall]"
allowed-tools: Bash(uvx --from:*)
---

Run this command and show the user its output:

```bash
uvx --from "${CLAUDE_PLUGIN_ROOT}/server" freecad-bridge install --addon-src "${CLAUDE_PLUGIN_ROOT}/addon/ClaudeBridge" $ARGUMENTS
```

Then:

- If it failed because FreeCAD wasn't found, ask the user for the path to FreeCAD.app and rerun with `--freecad <path>`.
- If it succeeded and this was not `--uninstall`, tell the user to (re)start FreeCAD; the "Claude" chat panel appears docked on the right. Then run `/freecad:status` once FreeCAD is up.
- If it succeeded and this was not `--uninstall`, offer to add allow rules to `~/.claude/settings.json` so the read-only tools don't prompt every time: `mcp__plugin_freecad_freecad__status`, `mcp__plugin_freecad_freecad__get_tree`, `mcp__plugin_freecad_freecad__get_object`. Mention that `mcp__plugin_freecad_freecad__*` would allow all tools including `run_python`, which runs arbitrary code. Only edit the settings file if the user agrees.
