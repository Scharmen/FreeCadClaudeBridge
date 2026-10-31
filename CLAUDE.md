# CLAUDE.md

## Ziel und Architektur

FreeCAD per Chat steuern, im Chat-Fenster „Claude“ in FreeCAD oder in Claude Code. Der vollständige Plan steht in [docs/PLAN.md](docs/PLAN.md); er ist verbindlich.

- **Plugin `freecad`** (Claude Code, Marketplace `freecad-claude` = dieses Repo `Scharmen/FreeCadClaudeBridge`) bündelt MCP-Server, Skill, `/freecad:setup`, `/freecad:status` und einen SessionStart-Hook.
- **MCP-Server `freecad-bridge-mcp`** (stdio, Python 3.12, offizielles MCP-SDK, gestartet per `uvx`) bietet 19 generische Tools, allen voran `run_python`.
- **Transport:** Unix-Socket (macOS/Linux) bzw. TCP nur auf 127.0.0.1 (Windows, Fallback); 4-Byte-Längenpräfix plus JSON, beidseitiger HMAC-SHA256-Handshake mit 32-Byte-Token.
- **Addon `ClaudeBridge`** im FreeCAD-GUI-Prozess: Listener-Thread nimmt an, ausgeführt wird im GUI-Thread per Qt-Signal; jeder Aufruf ist genau eine Undo-Transaktion. Journal unterscheidet `user`/`claude`.
- **Chat-Fenster** im Addon startet `claude -p --output-format stream-json` als asynchronen `QProcess`; Anmeldung über Abo oder API-Key (`ANTHROPIC_API_KEY`, Key im System-Schlüsselbund).
- **Headless-Worker:** `run_headless` startet einen eigenen `freecadcmd`-Prozess für schwere Rechnungen.
- Zustand (Token, Instanzdateien, Snapshots, Log) liegt im Bridge-Verzeichnis, nie unter `${CLAUDE_PLUGIN_ROOT}`.

## Konventionen

- Server: Python 3.12 (`requires-python = ">=3.12,<3.13"`), offizielles MCP-SDK 2.x (`from mcp.server.mcpserver import MCPServer`; Beispiele mit `FastMCP` sind 1.x), Paket unter `plugin/server/`.
- Entwicklung: `uvx --from <pfad>` cached nach Version, Codeänderungen greifen nur mit `uvx --refresh` oder `uv run --project plugin/server`. uv liegt in `~/.local/bin`.
- Jeder Kindprozess aus FreeCAD (Claude Code, `uvx`, `freecadcmd`) startet mit bereinigter Umgebung: FreeCAD vererbt sonst `PYTHONHOME` und Bundle-Pfade.
- Addon: nur Standardbibliothek plus das in FreeCAD gebündelte Python (macOS-Bundle 1.1.x: 3.11) und PySide; keine Fremdpakete.
- `protocol.py` existiert als identische Kopie in Addon und Server; die CI prüft das.
- Plugin-, Addon- und Paketversion sind identisch.
- ruff, pytest, Typannotationen überall.

## Arbeitsregeln

- Eine Phase pro Sitzung. Die nächste Phase beginnt erst nach erfülltem Gate und Freigabe.
- Zuerst die Abnahmekriterien als Tests, dann der Code.
- Keine Abweichung von den Grundsatzentscheidungen in `docs/PLAN.md` ohne Rückfrage.
- Nach jeder Phase: CHANGELOG-Eintrag, Versionssprung, Tag.
- Ergebnisse von Spikes und offenen Punkten stehen in `docs/spikes.md`.
