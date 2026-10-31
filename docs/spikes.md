# Phase 0: Spikes

Stand: 31.10.2026 · Mac (Apple Silicon, macOS 27.2), FreeCAD 1.1.4 (`/Applications/FreeCAD.app`, Bundle `macOS-arm64-py311`), Claude Code 2.1.310, uv 0.13.6

Im Benutzer-`Mod`-Ordner liegt schon ein fremdes Addon `freecad-mcp`. Es lief während der Tests mit, ohne zu stören.

## Status der offenen Punkte

| # | Punkt | Status | Ergebnis |
| --- | --- | --- | --- |
| 1 | Python im Bundle | macOS ✅, Windows ⏳ Phase 3 | macOS arm64: **Python 3.11.14** (`Contents/Resources/bin/python`) |
| 2 | `freecadcmd` | macOS ✅, Windows ⏳ Phase 3 | Vorhanden: `FreeCAD.app/Contents/Resources/bin/freecadcmd`, daneben `freecad`, `python`, `pip`. Signiert (Team-ID 289DJRF23X) |
| 3 | Benutzerverzeichnis Windows | ⏳ Phase 3 | braucht einen Windows-Rechner |
| 4 | Headless-Einzeiler | macOS ✅, AppImage ⏳ Phase 3 | `freecadcmd <skript.py>` führt eine Datei aus. Nach der Skriptausgabe folgt ein Lizenz-Banner auf stdout, der Installer parst daher Marker-Zeilen. `getUserAppDataDir()` = `~/Library/Application Support/FreeCAD/v1-1/`, `getHomePath()` = `…/FreeCAD.app/Contents/Resources/` |
| 5 | Addon Manager mit `<subdirectory>` | ✅ Code gelesen, Praxistest in Phase 1 | FreeCADs Init-Loader (eingebettetes `FreeCADInit.py` in `libFreeCADApp`) nimmt bei `<workbench>` das Feld `Subdirectory` als Pfad für `Init.py`/`InitGui.py` und `sys.path`. Der Addon Manager klont das ganze Repo nach `Mod/FreeCadClaudeBridge/` |
| 6 | Konsole mitlesen | ✅ | `App.Console` hat **keine** Python-API für Observer. Die Report-View ist erreichbar: `QTextEdit` mit `objectName() == "Report view"` (Klasse `Gui::DockWnd::ReportOutput`), Zeilen mit Zeitstempel. **Meldungen kommen verzögert** an, erst nach dem nächsten Durchlauf der Ereignisschleife, nicht schon innerhalb desselben Aufrufs. `get_console` schneidet deshalb per `textChanged` in einen eigenen Ringpuffer mit; die Ausgabe eines Aufrufs liefert weiterhin die stdout/stderr-Umleitung |
| 7 | Task-Dialoge | ✅ | `Gui.Control.activeTaskDialog()` liefert auch für C++-Dialoge ein Objekt mit **`accept()`**, **`reject()`**, `getStandardButtons()`, `getDialogContent()`. Getestet mit `Part_Primitives`: `reject()` schließt, `accept()` löst die Aktion aus, `closeDialog()` schließt ohne Rückfrage. `activeDialog()` gibt `True`/`False` |
| 8 | `uvx`-Startzeit | ✅ | Wegwerf-Paket mit `mcp` 2.3.0 und Python 3.12: erster Lauf 1,3 s (mit Download von Python 3.12 2,5 s), danach **0,56–0,60 s**. Neuer Pfad (wie nach Plugin-Update): 1,2 s, danach 0,53 s. **Achtung:** Änderungen am Quellcode unter demselben Pfad bei gleicher Version übernimmt `uvx` nicht (Cache). Für die Entwicklung `uvx --refresh` oder `uv run --project` |
| 9 | Berechtigungsregel | ✅ | Alle Tools des Plugin-Servers: **`mcp__plugin_freecad_freecad__*`** (laut Doku auch `mcp__plugin_freecad_freecad`). Allow-Globs funktionieren nur hinter einem festen `mcp__<server>__`-Präfix |
| 10 | Claude Code im Hintergrund | ✅ | Siehe unten |
| 11 | `claude` aus FreeCAD finden | ⚠️ fast | Aus FreeCAD per `QProcess` mit absolutem Pfad gestartet: Abo-Anmeldung gefunden (`apiKeySource: none`), Tool-Aufruf läuft durch. Mit minimaler Umgebung (`env -i`, wie bei Start aus Dock/Finder) ist `claude` nicht im PATH, läuft mit absolutem Pfad aber. **Noch offen:** FreeCAD wirklich aus dem Dock starten. Für den Test wurde es mit `open -a` aus dem Terminal gestartet und hat dessen Umgebung geerbt |

## Spike-Ergebnisse

**Threading (`spikes/ThreadSpike`, `spikes/thread_client.py`)**, Gate erfüllt:

- 1.000 Aufrufe ohne Hänger, alle im GUI-Thread, jeder zehnte legt eine `Part::Box` an. Latenz pro Aufruf p50 0,06 ms, p99 9,9 ms, maximal 78 ms.
- Fehler nach `addObject` → `abortTransaction()` → kein Objekt bleibt übrig.
- `ping` antwortet in 0,7 ms, während der GUI-Thread 3 s blockiert ist.
- Ein Aufruf mit zwei `addObject` lässt sich mit einem einzigen `Std_Undo` komplett zurücknehmen; `UndoNames` zeigt den Transaktionsnamen.
- **Neu:** FreeCAD behält standardmäßig nur **20 Undo-Schritte** (`UndoCount` blieb bei 20). Viele kleine Claude-Aufrufe verdrängen so den Undo-Verlauf des Nutzers.

**Chat (`spikes/chat_spike.py`, `spikes/mini_mcp.py`)**, Gate erfüllt:

- Aus FreeCAD per `QProcess` gestartet: `claude -p … --output-format stream-json --verbose --mcp-config … --allowedTools mcp__spike__run_python`. Claude ruft `run_python` auf, der Würfel entsteht im offenen Dokument als eine Undo-Transaktion. Ergebnis nach 8,7 s.
- GUI bleibt frei: Ein 100-ms-Timer im GUI-Thread lief in 8,7 s 77-mal (ideal 87).
- **Fund 1, Umgebung:** FreeCAD setzt `PYTHONHOME` aufs Bundle, `PYTHONPATH`, `LD_LIBRARY_PATH`, `SSL_CERT_FILE` und stellt rund 35 Bundle-Pfade (darunter ein eigenes `python`) vor den PATH. Kindprozesse erben das; der MCP-Server starb deshalb sofort (`CONNECTION_CLOSED`). Erst mit bereinigter `QProcessEnvironment` lief er. Das betrifft auch `uvx` und `run_headless`.
- **Fund 2, stdin:** Ohne `setStandardInputFile(QProcess.nullDevice())` wartet `claude -p` 3 s auf stdin und warnt.
- **Fund 3, Tool Search:** Claude lädt MCP-Tools erst per `ToolSearch` nach, das kostet eine Runde (etwa 3 s).

## Punkt 10 im Detail: `claude -p`

- NDJSON-Ereignisse: `system/init` (`session_id`, `apiKeySource`, `model`, `permissionMode`, `mcp_servers` mit `status`, `plugins`, `plugin_errors`), `assistant` (Text- und `tool_use`-Blöcke), `rate_limit_event`, `system/post_turn_summary`, `result` (`result`, `is_error`, `total_cost_usd`). `--verbose` ist für stream-json nötig; Token-Streaming mit `--include-partial-messages` (`stream_event`/`text_delta`).
- `--resume <session-id>` setzt fort (getestet).
- Ohne `--bare` lädt `-p` installierte Plugins, Skills, Hooks und CLAUDE.md. Zusätzlich gibt es `--plugin-dir <pfad>`.
- Berechtigungen: Manual-Modus seit 2.1.302; nicht freigegebene Aufrufe werden abgelehnt. Mit `--permission-prompts none` kommen Ablehnungen als `system/permission_denied` und in `result.permission_denials`. Alternative: `--permission-prompt-tool`.
- Abbruch: SIGINT beendet die Runde sauber; SIGTERM (Exit 143) lässt sie ohne Ergebnis.
- Nicht getestet: Vorrang von `ANTHROPIC_API_KEY` vor der Abo-Anmeldung, weil kein Key vorhanden ist. Ob die Abo-Bedingungen diese Nutzung abdecken, muss Kevin selbst prüfen.
- **Risiko:** Laut Doku wird `--bare` künftig Standard für `-p`. Bare nutzt **keine** Abo-Anmeldung und keine installierten Plugins.

## Weitere Funde

- **MCP-SDK 2.x:** Aktuell ist `mcp` 2.3.0. `FastMCP` heißt jetzt `MCPServer` (`from mcp.server.mcpserver import MCPServer`). Code aus neka-nat/blwfish (v1-API) lässt sich nicht direkt übernehmen.
- **Python-Version für uvx:** `requires-python = ">=3.12"` ließ uv Python 3.15 wählen. Für 3.12 braucht es `">=3.12,<3.13"` oder `--python 3.12`.

## Spike-Dateien

- `spikes/ThreadSpike/`: Mini-Addon, Unix-Socket-Listener, Dispatch per Qt-Signal plus `Future`, eine Transaktion pro Aufruf. Ist für die Tests nach `~/Library/Application Support/FreeCAD/v1-1/Mod/ThreadSpike` kopiert.
- `spikes/thread_client.py`: Lasttest.
- `spikes/mini_mcp.py`: MCP-Server nur mit Standardbibliothek.
- `spikes/chat_spike.py`: Claude Code als `QProcess` mit bereinigter Umgebung.

## Gate Phase 0

Erfüllt bis auf zwei Restpunkte, die das Gate nicht blockieren: der echte Dock-Start (Punkt 11) und die Windows/AppImage-Teile von 1 bis 4 (gehören in Phase 3).
