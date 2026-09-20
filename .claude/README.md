# Claude Code / Sonar agent config

Shared, portable project wiring for optional SonarQube CLI integration.
Personal overrides stay out of git (see root `.gitignore`).

## Prerequisites (optional)

These files do **nothing required** for building, testing, or running PRKS.
They only matter if you use [Claude Code](https://code.claude.com/) with the
[SonarQube CLI](https://docs.sonarsource.com/sonarqube-cli) (`sonar` on `PATH`).

1. **Python 3.12+** with `python3` on `PATH` (already required by PRKS). The
   shared hooks invoke `python3` in Claude Code exec form — not Node, not a
   Unix-only shell script.
2. Install the SonarQube CLI for your OS (Linux, macOS ARM64, or Windows).
3. Authenticate locally (`sonar auth login`). Tokens stay on the machine; they
   are never committed (`.mcp.json` is token-free).
4. Restart the terminal so `PATH` picks up `sonar`.

Without `sonar` on `PATH`:

- **Secrets hooks** (`.claude/settings.json`) no-op via
  `.claude/hooks/sonar-secrets/run_hook.py`.
- **SonarQube MCP** (root `.mcp.json`) will fail to start. That is expected:
  uninstall/disable the server in your MCP client, or install the CLI.

## Shared hooks

`.claude/settings.json` registers PreToolUse (Read) and UserPromptSubmit
secrets scans. Commands use Claude Code **exec form** (`python3` + `args`) so
the same config works on Windows PowerShell, macOS, and Linux without Git Bash
and without treating Node as a dependency.

## Local-only files

| Path | Tracked? |
| --- | --- |
| `.claude/settings.json` | Yes (shared hooks) |
| `.claude/settings.local.json` | No (personal MCP opt-in / experiments) |
| `.codex/` | No (Codex integrate may write absolute machine paths) |
| Root `.mcp.json` | Yes (token-free Sonar MCP for project `Fooftilly_PRKS`) |
