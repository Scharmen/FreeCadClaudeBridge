# Changelog

## Phase 0 – Spikes (31.10.2026)

- `docs/PLAN.md` und `CLAUDE.md` angelegt.
- Spikes auf macOS mit FreeCAD 1.1.4: Threading (1.000 Aufrufe im GUI-Thread, Rollback, ein Undo-Schritt pro Aufruf), Chat aus FreeCAD per `QProcess` mit `claude -p` und MCP-Rückruf, `uvx`-Startzeit, Report-View, Task-Dialoge. Ergebnisse in `docs/spikes.md`.
- Plan angepasst: bereinigte Umgebung für Kindprozesse, MCP-SDK 2.x, Python 3.12 fest, `--plugin-dir` und Startprüfung im Chat-Fenster, `--permission-prompts none`, SIGINT beim Stopp, Undo-Limit, Repo-Name `Scharmen/FreeCadClaudeBridge`.
