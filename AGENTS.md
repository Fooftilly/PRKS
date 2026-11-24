# PRKS

PRKS is a research-library application. The runtime is Python 3.14 served by Uvicorn, PostgreSQL 18 as the only Library store, managed local files, and a Vue 3 + TypeScript frontend that is replacing the legacy JavaScript UI.

Where things are documented:

- `README.md`: exact run, configuration and safety contracts (host/port, Docker, env vars, auth).
- `docs/wiki/`: current feature and user behavior; it is the source of the GitHub Wiki (published by `.github/workflows/publish-wiki.yml`; never edit the rendered wiki alone).
- `DESIGN.md`: UI and interaction authority. Read only the sections for the component you change.
- `docs/local-first-rollout-status.md`: current offline/sync status.
- `docs/postgresql-architecture.md`, `docs/storage-architecture.md`: design authority for persistence and storage.
- Roadmap issues #179, #310 and #311: accepted long-term direction.
- This file: repository-wide rules.

## Scoped agent instructions

Detailed rules live next to the code they govern. Load only the scopes your change touches.

- `backend/AGENTS.md`: backend architecture, privacy/logging, performance, persistence, backup/restore, schema and mutation rules; routes backend work to the shared sync, research and Saved Views contracts.
- `frontend/AGENTS.md`: UI/runtime behavior, workspace navigation, settings, Saved Views, research surfaces, offline/PWA and interaction feedback.
- `frontend-app/AGENTS.md`: Vue 3 + TypeScript source, component/service/state ownership and migration boundaries.
- `tests/AGENTS.md`: routing for test-only changes. `tests/e2e/AGENTS.md`: browser E2E workflow and policy. `tests/ux_tour/AGENTS.md`: UX Interaction Tour.
- `docs/agent-context/sync-map.md`: routing map for the local-first/offline specifications.
- `docs/agent-workflows/`: GitHub process docs, loaded only for that task: `agent-queue.md` (choosing work), `github-discussions.md`, `issue-dependencies.md`, `project-3.md` (Project #3 configuration), `project3-assignment-dispatch.md`, `project3-assignment-request.md`, `project3-triage.md`.

Nested `AGENTS.md` files refine these root rules for their directory.
When both apply, the more-specific nested instructions take precedence.
Keep nested files additive: do not override load-bearing root invariants (storage safety, privacy/logging, issue taxonomy).

For test-only changes, follow `tests/AGENTS.md` and also load the production scope of the behavior under test.

## Choosing work (Project #3)

Before choosing new work, check the Project #3 custom **Agent** field (not issue Assignees): `bash scripts/agent-queue.sh cursor|claude|chatgpt` (see `docs/agent-workflows/agent-queue.md`, including the hosted-agent fallback). An assigned issue is not automatically actionable: verify Status/Execution, acceptance labels, open blockers, other owners, roadmap supersession and overlapping PRs first. Never change Project fields or labels to make a task look ready. If Project data cannot be read, treat readiness as unknown.

## Pull requests

- Every merge into `master` needs the maintainer's explicit approval. Agents prepare, review and fix PRs, but do not merge them, enable auto-merge, or use Mergify queueing (`@mergifyio queue`, Merge Queue, auto-merge rules).
- A real PR-to-PR merge prerequisite goes on its own `Depends-On: #<PR>` line in the dependent PR's description, then check that `Mergify Merge Protections` reports it. Do not use `Depends-On:` for issue dependencies or merely related work, and never edit it away just to get a green status.

## Current implementation vs target architecture

Distinguish **implemented constraints** from **accepted target architecture**.

- The current implementation (PostgreSQL, local files, the Uvicorn HTTP server, durable browser sync, the Vue/legacy bridge) is authoritative when changing the code that implements it.
- #310 targets a modular monolith: explicit User/Library/LibraryMembership boundaries, authentication separate from authorization, PostgreSQL as canonical store, stateless-capable API processes, durable jobs.
- #311 requires a storage-backend abstraction with a user-configurable data root; a physical file location is never domain identity.
- The offline/local-first machinery is a correctness contract until deliberately migrated, not a design every future client must copy.
- No incidental framework, auth, storage-backend or sync rewrites inside unrelated work. Do not design new schema around SQLite, absolute paths, single-owner assumptions or process-local state; SQLite Library code is gone and must not return.

## Commands

- Unit tests: `python run_tests.py`. UX Interaction Tour (opt-in only): `python run_tests.py --ux-tour`.
- Browser E2E needs a disposable PostgreSQL 18 server in `PRKS_TEST_DATABASE_URL` (see `tests/e2e/AGENTS.md`).
- Agent E2E loop: `scripts/e2e agent` (affected tests, fail-fast, conservative workers). Others: `scripts/e2e smoke|feature|affected|last-failed|dev|full|inventory`. Debug one failure with `python tests/e2e/run.py --jobs 1 <test id>`.
- Full E2E gate: `python tests/e2e/run.py --jobs 4` (1200 s hard limit; 7-10 min is normal).
- **Never run browser E2E tests while iterating.** Use unit, Node, static and API tests; run the relevant E2E feature once the slice is done. Cadence and debugging policy: `tests/e2e/AGENTS.md`.
- App: `python prks_app.py --testing`. If asked only to "run the app", use exactly that: never unflagged `prks_app.py`, Docker Compose or `./data` without run-real authorization.
- Git hooks: `./scripts/setup-git-hooks.sh` (sets `core.hooksPath` to `.githooks`; commits refresh the gitignored `prks-latest.zip`).

## Storage

- Default: never write `data/` or a live `PRKS_STORAGE` tree. `python run_tests.py` sets `PRKS_TESTING=1`, points `PRKS_STORAGE` at `data_testing/` and clears `PRKS_LOG_FILE`.
- Run-real: an instruction to run the real app or Compose authorizes normal application writes only.
- Destructive: deleting PDFs, resetting or replacing the production database, or clearing production storage needs a separate explicit confirmation naming that action. Run-real is not that confirmation.

## Issues

Public issues use the checked-in templates in `.github/ISSUE_TEMPLATE/`; pick by purpose:

- `bug-report.md`: incorrect behavior. Give reproduction (or say it is intermittent), current vs expected, tested commit, privacy-safe evidence. Fixes need regression coverage when reasonably automatable.
- `engineering-task.md`: bounded work whose outcome is decided (features, refactors, CI, tests). Sized for one PR or a short sequence.
- `research-evaluation.md`: an unsettled question whose output is evidence and a decision (`research` label). Keeping the current approach is a valid outcome; a PoC does not authorize a production migration.
- `audit-finding.md`: an engineering observation (`audit-finding` label, `[Audit Finding]` title, next unused `EF-xxx` ID in the body; re-check the ID just before submitting).
- `ux-ui-finding.md`: usability, accessibility, feedback or visual problems (`ux-finding`, `candidate`, `area:ux`; legacy titles use `UX:`, `UI:`, `A11y:`). Record tested commit, steps, observed vs expected, impact, evidence; separate observation from guessed causes. Screenshots use synthetic data only.
- `roadmap.md`: a broad direction to split into focused work (`roadmap` label).
- Vulnerabilities, exploit details, secrets or private data never go in public issues: follow `SECURITY.md`.

Rules for all issues:

- Search open and closed issues (by label first, title prefix as fallback) before creating one; update an overlapping issue instead of duplicating it. Keep the original tracking issue and link focused follow-ups.
- Apply `priority:*` and `area:*` labels when supported. `candidate`/`accepted` are maintainer-only lifecycle labels for roadmap, audit and UX findings. `accepted` on a finding means it is valid, not that implementation is authorized.
- Findings are not implementation instructions: implement one only when the maintainer assigns it or the current task names it, and re-verify older findings against `master` first. This file wins over a finding until the maintainer changes the policy.
- Close fixed, obsolete, rejected or duplicate items with the matching GitHub close reason (`completed`, `not planned`, `duplicate`) and a short disposition.
- Never put real library content (titles, notes, names, file names, paths, search terms, URLs) in issues, logs or screenshots.

## Layout

- `prks_app.py`: the only process entry; roles `serve` (default), `migrate`, `worker`, `admin` (`backend/process_roles.py`).
- `backend/server.py`: HTTP adapter lifecycle (host/origin checks, size limits, admission, dispatch, JSON/ETags, static files).
- `backend/api/<family>.py`: one HTTP controller per route family. Controllers call the family's commands (`backend/domain/<family>/commands.py` or a family-specific `*_commands.py`) with `db.database` and the admitted `handler._prks_request_context`, and import those commands inside the handler (the served app loads no PostgreSQL module at import time; `tests/test_e2e_policy.py`).
- `backend/api_contract/`: typed request/response DTOs and OpenAPI fragments.
- `backend/library.py`: `PostgresLibrary`, the handle the adapter holds as `db` (`database` and `storage` only), and `NotPortedError` (answered 501, never a fallback).
- `backend/domain/`: the PostgreSQL domain packages (`works`, `people`, `organization`, `research`, `annotations`, `objects`, `search`, `maintenance`, `identity`, `processing`, `saved_views`, `settings`). Package docstrings are short overviews, not the full rules: for invariants and lock order read `backend/AGENTS.md`, the cited design sections and the implementation module docstrings before changing a package. Shared rules (`*/rules.py`, `managed_pdf_names.py`, `etags.py`, `pdf_text.py`, `derived_caches.py`) are imported, never re-implemented.
- `backend/domain/identity/`: sign-in, sessions, `RequestContext`, `authorize()` and `library_command`/`library_query`. These enforce actor, capability and epoch, not row scope: callers own their `library_id` predicates and add a cross-Library negative test per command or query. Operator notes: `docs/admin-bootstrap.md`, `docs/login-security.md`.
- `backend/db/`: pool, `command()`/`query()`, SQLSTATE mapping, and the schema runner over `backend/db/schema/NNN_*.sql` (see `backend/AGENTS.md` "Database schema changes"). Tests in `tests/pg/` run through `scripts/test-postgres`.
- `backend/sync/`: the durable-operation ledger (`protocol.py`) and per-family handlers; the shared envelope and operation registry are `envelope.py`; each family's payload rules live with its domain in `backend/domain/<area>/*_rules.py` (`backend/domain/annotations/rules.py` for PDF annotations).
- `backend/jobs/`: the PostgreSQL job queue, runner, worker and embedded worker (`docs/job-worker.md`).
- `backend/storage/`: root selection (`resolver.py` only), root marker and leases (`root_binding.py`, `root_marker.py`), and `objects.py` (`StorageBackend`). Design: `docs/storage-architecture.md`.
- `backend/readiness.py`: `GET /readyz`. `docker-compose.yml`: the only Compose file (`db`, `migrate`, `web`, `worker`); README "Docker" is the operator doc.
- `backend/domain/text_identity.py`: the name identity key, twin of SQL `prks.identity_key` (shared vectors in `tests/fixtures/text_identity_vectors.json`).
- Processing is a managed upload queue (`backend/domain/processing/`); do not bring back a scanned inbox directory.
- `frontend/`: legacy UI shell and runtime (`js/tab-context.js`, `js/workspace-tabs.js`, `js/offline-store.js`, `js/offline-runtime.js`, `sw.js`); see `frontend/AGENTS.md` "Offline / PWA".
- `frontend-app/`: Vue 3 + TypeScript source and Vite build (`src/app-shell/` holds the New File split button); `frontend/vue/` is the committed bundle.
- `tests/`: unittest. Tests build their Library with `tests/library_adapter.py` `TestLibrary`; parity tests replay recorded SQLite answers (`tests/parity_reference/`), so a changed call needs a PostgreSQL-only assertion.

New File UI uses the canonical Work creation path (`#work-modal` / `POST /api/works`), never a second creation API.

New substantial behavior, in order of preference:

1. Extend the focused module that already owns it.
2. Otherwise add a focused domain module.
3. Use `backend/services/` only to coordinate several concerns (database, files, PDF, indexing).
4. Put a cohesive HTTP route family in `backend/api/<family>.py`. No `routes/` package, extra layers or frameworks ahead of real need.

### HTTP adapter decomposition

PRKS serves through Uvicorn with its own plain ASGI adapter (`backend/web/asgi.py`, `docs/http-server.md`); controllers and domain code stay synchronous on a bounded thread pool, and no web framework is added. `server.py` keeps host/origin validation, body size limits, admission, response encoding/ETags/cache headers, static files and dispatch. Controllers parse request shape, call domain code and map domain errors to status and JSON bodies; SQL, file mutation, indexing and sync semantics stay out of controllers. When moving routes, preserve paths, status codes, error bodies, ETags, cache headers, security checks and size limits, and do not move unrelated helpers just to shrink `server.py`.
