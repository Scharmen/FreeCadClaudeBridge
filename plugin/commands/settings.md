---
description: Show or change the FreeCAD bridge settings (memory limits and time limit of headless jobs)
argument-hint: "[set <key> <value> | reset [<key>]]"
allowed-tools: Bash(uvx --from:*)
---

Run this command and show the user the result:

```bash
uvx --from "${CLAUDE_PLUGIN_ROOT}/server" freecad-bridge settings $ARGUMENTS
```

Explain briefly what the values mean for this computer (the last line shows the memory that always stays free and what one headless job may use). If the user wants to change a value but gave no exact key, pick the matching key from the list and run `freecad-bridge settings set <key> <value>`. Changes apply to the next `run_headless` call; no restart needed. The same settings are on the "Claude Bridge" page of FreeCAD's preferences.
