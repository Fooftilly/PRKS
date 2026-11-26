# Claude Code project config

Project skills and optional SonarQube CLI integration.
Personal overrides stay out of git (see root `.gitignore`).

## Prerequisites (optional)

Claude Code configuration is optional. Skills do not require Sonar.
The hooks below require the [SonarQube CLI](https://docs.sonarsource.com/sonarqube-cli) (`sonar` on `PATH`).

1. **Python 3.14+** (already required by PRKS). The shared hooks do **not**
   hard-code a bare `python3` command on Windows. Launchers follow the same
   interpreter preference as PRKS tooling:
   - POSIX / Git Bash (`run_hook.sh`): `python3`, then `python`, then `py -3`
   - Native Windows PowerShell (`run_hook.ps1`, local override only): `python`,
     then `py -3`, then `python3` (see `tools/resolve-python3.mjs` /
     `backend/runtime/dependency_check.py`)
2. Install the SonarQube CLI for your OS (Linux, macOS ARM64, or Windows).
3. Authenticate locally (`sonar auth login`). Tokens stay on the machine; they
   are never committed (`.mcp.json` is token-free).
4. Restart the terminal so `PATH` picks up `sonar`.

Without `sonar` (or a usable Python interpreter) on `PATH`:

- **Secrets hooks** no-op via the launchers → `run_hook.py`.
- **SonarQube MCP** (root `.mcp.json`) will fail to start. That is expected:
  uninstall/disable the server in your MCP client, or install the CLI.

## Shared hooks

`.claude/settings.json` registers **one** handler per event (PreToolUse / Read
and UserPromptSubmit). The shared command is the bash-form launcher
`run_hook.sh`, which Claude runs under Bash on macOS and Linux, and under Git
Bash on Windows when it is installed.

That single registration avoids the dual-handler foot-gun: Claude runs every
matching handler in parallel, and on native Windows **without** Git Bash the
default shell is PowerShell, so a sibling `.sh` command would be parsed by
PowerShell and surface a recurring non-blocking hook error even when a
PowerShell handler still scanned successfully.

`run_hook.py` still reads stdin once, claims an **atomic** request-scoped lock
keyed by a stable digest of (project, hook, payload), and only the winning
process forwards that same payload to `sonar hook` (safe if a local override
adds a second handler).

### Native Windows without Git Bash

Install [Git for Windows](https://git-scm.com/download/win) so Claude Code can
use Git Bash for hooks (the usual Claude-on-Windows setup), **or** add a
PowerShell handler only in gitignored `.claude/settings.local.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Read",
        "hooks": [
          {
            "type": "command",
            "shell": "powershell",
            "command": "& \"$env:CLAUDE_PROJECT_DIR\\.claude\\hooks\\sonar-secrets\\run_hook.ps1\" claude-pre-tool-use",
            "timeout": 60
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "shell": "powershell",
            "command": "& \"$env:CLAUDE_PROJECT_DIR\\.claude\\hooks\\sonar-secrets\\run_hook.ps1\" claude-prompt-submit",
            "timeout": 60
          }
        ]
      }
    ]
  }
}
```

Do **not** also keep the shared bash-form handler active in that local file on
a PowerShell-default machine — register one platform-appropriate handler, not
both. `run_hook.ps1` remains in the repo for this override.

## Local-only files

| Path | Tracked? |
| --- | --- |
| `.claude/settings.json` | Yes (shared hooks) |
| `.claude/settings.local.json` | No (personal MCP opt-in / Windows PowerShell override / experiments) |
| `.codex/` | No (Codex integrate may write absolute machine paths) |
| Root `.mcp.json` | Yes (token-free Sonar MCP for project `Fooftilly_PRKS`) |

## Skills

Start Claude Code from the repository root. See `skills/*/SKILL.md` for opt-in commands. Existing AGENTS.md rules apply.

Smoke-test in a disposable checkout: confirm all nine skills and `prks-change-auditor` are discovered. On a synthetic diff, verify `/no-comments report-only` and `/prks-review` actually delegate to the auditor without editing or posting; fallback is not a delegation pass. Verify `/no-comments` on a synthetic diff that mixes redundant narration with comments it must keep (a sync or lock-order rule, a license header, a lint directive): it removes only the narration. Use `/verify-feature` on an isolated test and confirm the reported result matches the actual run. These are manual checks, not CI evidence.
