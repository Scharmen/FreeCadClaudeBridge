# Security checklist (1.0)

`run_python` is full code execution with your user rights by design. What the bridge secures is **who can reach it** and **what is recorded**, not what Claude may do once allowed. Each item below names where it is implemented and how it is checked.

| # | Item | Implementation | Checked by |
| --- | --- | --- | --- |
| 1 | Local only: Unix socket on macOS/Linux, TCP bound to 127.0.0.1 on Windows; no option for network access | `bridge/transport.py` (socket created under umask 0177) | `test_no_network_access_and_loopback_only`, CI on all three systems |
| 2 | Bridge folder 0700; token, config, instance files and audit log 0600 | `bridge/paths.py`, `platforms.py`, `bridge/audit.py` | `test_install_copies_addon_and_writes_state`, `test_audit_record_is_private_and_rotates` |
| 3 | 32-byte random token (`secrets`), mutual HMAC-SHA256 with fresh 32-byte nonces from both sides and role-bound MACs (`client:` / `server:`), constant-time comparison | `protocol.py` (identical in addon and server) | `tests/unit/test_protocol.py`, `test_wrong_token_fails` |
| 4 | Before authentication only frames up to 4 KB are read (64 MB afterwards) | `protocol.MAX_HANDSHAKE_FRAME` | `test_handshake_rejects_large_frames_before_auth` |
| 5 | Token rotation without restart: `freecad-bridge token --rotate` or *Claude → Rotate token*; written atomically, re-read on every connection | `paths.write_token`, `platforms.write_token`, `transport._serve` | verified live against FreeCAD 1.1.4 (rotate, then call) |
| 6 | The token never appears on a command line, in an environment variable or in a log | read from the file on both sides | code review |
| 7 | API key only in the system keychain (macOS Keychain, Windows Credential Manager, Secret Service; 0600 file as last resort), passed to `security`/`secret-tool` on stdin, given to Claude Code only through its environment, never stored in FreeCAD parameters; removed by uninstall | `panel/secrets.py`, `panel/claude_process.py`, `installer.clear_api_key` | manual test step 6 in [manual-testing.md](manual-testing.md) |
| 8 | Audit log of every bridge call (operation, arguments incl. the full code up to 20,000 characters, result, duration), every `run_headless` script and every rejected handshake; rotates at 10 MB | `bridge/audit.py`, `platforms.audit_record` | `test_addon_audit_clips_and_drops_internal_args`, verified live |
| 9 | Every write call is one undo transaction and is rolled back on error; snapshots on request | `bridge/ops.py` | `tests/gui` |
| 10 | Child processes (Claude Code, uvx, freecadcmd) start with a cleaned environment and stdin from the null device | `bridge/childenv.py`, `platforms.clean_env` | `tests/unit/test_childenv.py` |
| 11 | The chat panel allows only the bridge's own tools without asking (`--allowedTools mcp__plugin_freecad_freecad__*`, `--permission-prompts none`); more tools only per conversation through an explicit link | `panel/stream.py` `build_args` | `tests/unit/test_stream.py` |
| 12 | No network traffic from addon or server: no telemetry, no update checks; updates come only through the plugin marketplace | – | `test_no_network_access_and_loopback_only` |
| 13 | Protocol version check in the handshake; plugin/addon version differences are reported with the fix | `protocol.compatible`, `client.CallResult.warnings` | `test_version_warning`, `test_newer_addon_asks_for_plugin_update` |
| 14 | Uninstall removes addon, token, config, instance files, sockets and the API key; snapshots, chat history and the audit log only with `--purge` | `installer.uninstall` | `test_uninstall_removes_everything`, `test_uninstall_keeps_user_data_unless_purged`, verified live |
| 16 | Headless jobs cannot exhaust memory: killed when available memory falls below a reserve (15 % of RAM, min. 2 GB) or the job passes 75 % of RAM; budget computed per host at run time; shares and minimums are user settings within clamped ranges | `memguard.py`, `headless.run_headless` | `tests/unit/test_memguard.py`, verified live with freecadcmd |
| 17 | Learning data stays local: feedback.jsonl and preferences.md in the bridge folder (0600), never uploaded except as prompt text to Claude; user can edit, switch off and forget; kept on uninstall only without `--purge` | `learning.py`, `installer.uninstall` | `tests/unit/test_learning.py` |
| 18 | `image_to_3d` processes pictures locally (Pillow/NumPy); no external service | `imaging.py` | `tests/unit/test_imaging.py`, `test_no_network_access_and_loopback_only` |
| 15 | Third-party content: texts inside models are data, not instructions (skill rule); FCStd files from unknown sources are treated like code (README) | `skills/freecad/SKILL.md`, README | review |

## Known limits

- **Same user = full access.** Any process running as your user can read the token and drive FreeCAD. The bridge protects against other users and against processes that cannot read your files, not against malware in your own account.
- **Windows loopback.** Every local process can open the TCP port, but nothing gets past the handshake without the token in `%LOCALAPPDATA%\freecad-bridge`, which inherits your profile's permissions.
- **The audit log contains your code.** It can hold file paths and model details; it stays in the bridge folder and is never sent anywhere.
