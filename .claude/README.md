# Claude Code / Sonar agent config

Shared, portable project wiring for optional SonarQube CLI integration.
Personal overrides stay out of git (see root `.gitignore`).

## Prerequisites (optional)

These files do **nothing required** for building, testing, or running PRKS.
They only matter if you use [Claude Code](https://code.claude.com/) with the
[SonarQube CLI](https://docs.sonarsource.com/sonarqube-cli) (`sonar` on `PATH`).

1. **Python 3.12+** (already required by PRKS). The shared hooks do **not**
   hard-code a bare `python3` command on Windows. Launchers follow the same
   interpreter preference as PRKS tooling:
   - POSIX / Git Bash (`run_hook.sh`): `python3`, then `python`, then `py -3`
   - Native Windows PowerShell (`run_hook.ps1`): `python`, then `py -3`, then
     `python3` (see `tools/resolve-python3.mjs` / `backend/dependency_gate.py`)
2. Install the SonarQube CLI for your OS (Linux, macOS ARM64, or Windows).
3. Authenticate locally (`sonar auth login`). Tokens stay on the machine; they
   are never committed (`.mcp.json` is token-free).
4. Restart the terminal so `PATH` picks up `sonar`.

Without `sonar` (or a usable Python interpreter) on `PATH`:

- **Secrets hooks** no-op via the launchers → `run_hook.py`.
- **SonarQube MCP** (root `.mcp.json`) will fail to start. That is expected:
  uninstall/disable the server in your MCP client, or install the CLI.

## Shared hooks

`.claude/settings.json` registers PreToolUse (Read) and UserPromptSubmit
secrets scans twice per event:

- default shell form → `run_hook.sh` (macOS, Linux, Windows + Git Bash)
- `"shell": "powershell"` → `run_hook.ps1` (native Windows without Git Bash)

`run_hook.py` dedupes a second launcher within a few seconds so Windows does
not double-scan when both hook entries fire.

## Local-only files

| Path | Tracked? |
| --- | --- |
| `.claude/settings.json` | Yes (shared hooks) |
| `.claude/settings.local.json` | No (personal MCP opt-in / experiments) |
| `.codex/` | No (Codex integrate may write absolute machine paths) |
| Root `.mcp.json` | Yes (token-free Sonar MCP for project `Fooftilly_PRKS`) |
