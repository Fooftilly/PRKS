# Developer Reference

This page collects detailed contributor commands that are useful to humans working on PRKS but are too deep for the project README.

The authoritative contributor/agent rules remain in [AGENTS.md](https://github.com/Fooftilly/PRKS/blob/master/AGENTS.md). UI rules remain in [DESIGN.md](https://github.com/Fooftilly/PRKS/blob/master/DESIGN.md).

## Typed API boundary (#180 / #45)

Positions is the first HTTP family with Pydantic request/response models at the
adapter boundary only. See [docs/api-contract-boundary.md](../api-contract-boundary.md)
for the migration pattern, error envelope, and openapi-core / Schemathesis notes.
Live fragment: `GET /api/openapi.json`. Checked-in artifact:
`docs/api/openapi-positions.json`.

## UI design

`DESIGN.md` is authoritative for PRKS visual and interaction work. New UI primitives must be specified there and shown in `tests/browser/design_system.html` before they are used in production. Do not treat a generic design skill as a license to replace Inter, round the chrome, or add decorative surfaces.

Open the gallery (both themes) from the fixture server:

```bash
python tests/browser/serve.py
```

Then open the printed origin’s `/tests/browser/design_system.html?theme=light` and `?theme=dark`.

## Development and tests

Install both requirement files first. The unflagged unit suite always preflights
`openapi-core` from `requirements-dev.txt` (Playwright is only needed for `--e2e`):

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
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
# One test
python tests/e2e/run.py --jobs 1 tests.e2e.test_app.AppShellAndNavigationTests.test_app_loads_and_real_navigation

# One feature/domain group
python tests/e2e/run.py --list-features
python tests/e2e/run.py --feature graph --jobs 2 --no-pointer-capture
scripts/e2e feature sync --jobs 2

# Git-diff → likely feature groups (working tree vs HEAD; override with --base)
python tests/e2e/run.py --affected
python tests/e2e/run.py --affected --base origin/master

# Curated smoke (~9 essential tests)
python tests/e2e/run.py --smoke --jobs 2
scripts/e2e smoke --jobs 2

# Rerun only previous failures / fail-fast dev loop
python tests/e2e/run.py --last-failed
python tests/e2e/run.py --dev --feature tabs

# Full regression gate (final validation only; runner hard-limits at 1200s)
python run_tests.py --e2e
scripts/e2e full
python tests/e2e/run.py --jobs 4
# optional: timeout 1200 python tests/e2e/run.py --jobs 4
```

A smoke/feature/affected PASS is **not** a full-gate PASS. Reports print the tier.

`-e2e`, `-all`, and `-ux-tour` are the same flags. Unflagged `python run_tests.py` discovers tests under `tests/` and does not launch Chromium. It always forces `PRKS_TESTING=1` and `PRKS_STORAGE` to the repo’s `data_testing/` directory and clears `PRKS_FOR_PROCESSING_DIR` and `PRKS_LOG_FILE`. That is stricter than `python prks_app.py --testing`, which may honor an explicit safe `PRKS_STORAGE`. Neither path uses `./data` or container `/data`. `--ux-tour` is a separate, explicitly opt-in suite: it never runs as part of the default, `--e2e`, or `--all` modes.

## Project layout

| Path | Role |
| ---- | ---- |
| `DESIGN.md` | Authoritative UI visual/interaction contract. |
| `prks_app.py` | Only process entry: parses `--testing`, `--port`, `--host`, starts the server. |
| `backend/server.py` | Threaded stdlib HTTP server and handler: static frontend, method dispatch, shared transport (host/origin, body limits, JSON/ETags). |
| `backend/api/` | Optional domain HTTP controllers extracted from `server.py` (parse/validate, invoke domain, map errors). First family: Saved Views. |
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
| `--unit-contract` | Runtime + pinned `openapi-core` only (unit/API contract discovery; no Playwright) |
| `--test` | Runtime + `requirements-dev.txt` (Playwright and openapi-core) |
| `--repo` | Inventory, npm package.json↔lockfile, vendor VERSION/SHA-256, PDF BUILD-MANIFEST hashes, `DEPENDENCY-MANIFEST.json`, SW cache revision, no CDN loaders |

`python run_tests.py` preflights `--repo` + `--runtime` + `--unit-contract` before unit tests; `--e2e` preflights `--test`.

Authoritative pins live in `requirements*.txt`, `tools/*/package.json`, the `Dockerfile` (`FROM python:X.Y` and direct `apt-get install` packages), and (for Inter) `frontend/vendor/inter/VERSION`. `dependency-inventory.json` references those sources — it does not duplicate version literals when an authoritative file already exists.

Rebuild vendored assets after changing a pin:

```bash
# Ordinary UI vendor (DOMPurify, EasyMDE, CodeMirror, Lucide)
(cd tools/frontend-vendor && npm ci && npm run build)

# Research Graph Cytoscape
(cd tools/research-graph && npm ci && npm run build)

# PDF viewer (EmbedPDF + React)
(cd tools/pdf-viewer && npm ci && npm run build)
```

Each build refreshes `frontend/vendor/DEPENDENCY-MANIFEST.json` and `frontend/sw.js`'s `DEPENDENCY_REVISION` so service-worker static/shell caches retire when vendor bytes change. Inter is intentionally raw-managed (npm would alter the CSS/woff2 contract); update its `VERSION` + assets, then `python scripts/dependency_gate.py --write-manifest`.

## See also

- [Testing](Testing.md)
- [Dependencies and Vendoring](Dependencies-and-Vendoring.md)
- [Development Workflow](Development-Workflow.md)
