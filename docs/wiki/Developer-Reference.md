# Developer Reference

This page collects detailed contributor commands that are useful to humans working on PRKS but are too deep for the project README.

The authoritative contributor/agent rules remain in [AGENTS.md](https://github.com/Fooftilly/PRKS/blob/master/AGENTS.md). UI rules remain in [DESIGN.md](https://github.com/Fooftilly/PRKS/blob/master/DESIGN.md).

## UI design

`DESIGN.md` is authoritative for PRKS visual and interaction work. New UI primitives must be specified there and shown in `tests/browser/design_system.html` before they are used in production. Do not treat a generic design skill as a license to replace Inter, round the chrome, or add decorative surfaces.

Open the gallery (both themes) from the fixture server:

```bash
python tests/browser/serve.py
```

Then open the printed origin’s `/tests/browser/design_system.html?theme=light` and `?theme=dark`.

## Development and tests

```bash
python run_tests.py          # unit/API/structural/Node (no Chromium)
python run_tests.py --e2e    # real Chromium + real PRKS server (full gate)
python run_tests.py --all    # unit suite, then E2E
python run_tests.py --ux-tour                    # UX interaction tour (see below)
PRKS_UX_RECORD=1 python run_tests.py --ux-tour   # record every scenario for review
```

### Optional SonarQube CLI (Claude Code)

Shared Claude Code secrets hooks (`.claude/settings.json`) and the project
SonarQube MCP server (`.mcp.json`, project key `Fooftilly_PRKS`) are **optional**.
They are not required to develop, test, or run PRKS.

To use them, install the [SonarQube CLI](https://docs.sonarsource.com/sonarqube-cli)
so `sonar` is on your `PATH`, then authenticate locally (`sonar auth login`).
No tokens are committed; `.mcp.json` only names the project.

- Hooks use a single shared bash-form launcher (`run_hook.sh`) that locates
  Python the same way PRKS does (`python3` on POSIX / Git Bash; then `python` /
  `py -3`), then calls `run_hook.py`. If `sonar` or a usable interpreter is
  missing, the hooks exit 0 and do nothing. Native Windows without Git Bash
  should install Git Bash or add the PowerShell launcher via
  `.claude/settings.local.json` (see `.claude/README.md`) — do not register
  both handlers in shared settings (Claude runs every match in parallel).
- `.mcp.json` always launches `sonar run mcp …`. Without the CLI, that MCP
  server fails to initialize — install Sonar, or disable/remove that MCP entry
  in your client. See `.claude/README.md` for the full agent-config notes.
- Machine-local overrides (`.claude/settings.local.json`, `.codex/`) stay
  gitignored.

### E2E tiers (agent-friendly)

PRKS E2E is `tests/e2e/run.py` (Python unittest + Playwright), not npm Playwright.
See **E2E TESTING POLICY** in `AGENTS.md` for the authoritative agent rules.
Declarative feature/smoke/affected mapping: `tests/e2e/policy.py`.

```bash

## Project layout

| Path | Role |
| ---- | ---- |
| `DESIGN.md` | Authoritative UI visual/interaction contract. |
| `prks_app.py` | Only process entry: parses `--testing`, `--port`, `--host`, starts the server. |
| `backend/server.py` | Threaded stdlib HTTP server and handler: static frontend, REST-style `/api/...` routes. |
| `backend/concurrency.py` | Process-local library access gate (reads, mutations, backup, restore). |
| `backend/storage/config.py` | Frozen storage snapshot and env parser. |
| `backend/storage/paths.py` | Path derivation and testing-mode containment. |
| `backend/performance.py` | In-memory API/DB/span performance diagnostics. |
| `backend/db_manager.py` | SQLite access and business logic. |
| `backend/pdf_annotations.py` | Canonical PDF annotation normalize/validate/reconstruct. |
| `backend/db_migrations.py` | Ordered schema migrations and current-schema validation. |
| `backend/backup_restore.py` | Verified library backup and restore. |
| `backend/research_markup.py` | Authoritative `[[concept:]]` / `[[argument:]]` parser. |
| `backend/research_network.py` | Concept, Position, and Argument/Stance domain. |
| `backend/research_index.py` | Disposable derived note-reference index. |
| `backend/db_schema.sql` | Complete latest schema for fresh databases. |
| `frontend/` | Static SPA (HTML, CSS, JS), PWA assets. |
| `frontend/js/request-coordinator.js` | Client request coordinator for ordinary same-origin `/api` traffic. Memory-only; not offline support. |
| `frontend/js/offline-store.js` | Disposable IndexedDB client cache (entities/lists/metadata). No DOM, no routing, no connectivity policy. |
| `frontend/js/offline-runtime.js` | Online/offline/reconnecting state, read-through cache policy, offline coherence domains, mutation guard. No canonical persistence of its own. |
| `frontend/sw.js` | Service worker: app-shell/static-asset availability plus a focused managed-PDF cache. Never queues API mutations. |
| `data/` | Default production database and files (gitignored as appropriate). |
| `data_testing/` | Test fixtures and isolated DB/PDFs for automated tests. |
| `tests/` | `unittest` modules. |

## Dependency consistency

PRKS separates **freshness discovery** (Dependabot / optional `python scripts/dependency_gate.py --check-latest`) from **consistency enforcement** (`scripts/dependency_gate.py`, fully offline):

| Mode | What it checks |
| ---- | -------------- |
| `--runtime` | Python min version + exact `requirements.txt` pins (used at `prks_app.py` startup) |
| `--test` | Runtime + `requirements-dev.txt` (Playwright) |
| `--repo` | Inventory, npm package.json↔lockfile, vendor VERSION/SHA-256, PDF BUILD-MANIFEST hashes, `DEPENDENCY-MANIFEST.json`, SW cache revision, no CDN loaders |

`python run_tests.py` preflights `--repo` + `--runtime` before unit tests; `--e2e` preflights `--test`.

Authoritative pins live in `requirements*.txt`, `tools/*/package.json`, the `Dockerfile` (`FROM python:X.Y` and direct `apt-get install` packages), and (for Inter) `frontend/vendor/inter/VERSION`. `dependency-inventory.json` references those sources — it does not duplicate version literals when an authoritative file already exists.

Rebuild vendored assets after changing a pin:

```bash

## See also

- [Testing](Testing.md)
- [Dependencies and Vendoring](Dependencies-and-Vendoring.md)
- [Development Workflow](Development-Workflow.md)
