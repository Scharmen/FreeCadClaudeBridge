---
description: Check the FreeCAD bridge: addon version, token, running FreeCAD, handshake
allowed-tools: Bash(uvx --from:*)
---

Run this command and summarize the result for the user in a few lines; for every FAIL or WARN line, say what to do:

```bash
uvx --from "${CLAUDE_PLUGIN_ROOT}/server" freecad-bridge doctor
```

If FreeCAD is running, also call the `status` tool of the freecad MCP server and report FreeCAD version, open documents and the undo limit.
