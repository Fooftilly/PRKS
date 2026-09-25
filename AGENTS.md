# PRKS

PRKS is a local research library. Python 3.12 stdlib HTTP, SQLite, vanilla JS. Exact startup, configuration, and safety contracts live in README.md.

Documentation map: `docs/wiki/` is the reviewed source for the GitHub Wiki and for detailed current feature/user behavior (workspace, research network, Saved Views, command palette, and similar product surfaces). Keep exact run/config/safety contracts (host/port, Docker publish, env vars, schema version, auth warning) in `README.md`, implementation rules in this file, UI/interaction authority in `DESIGN.md`, and fast-moving local-first status in `docs/local-first-rollout-status.md`. Do not update the rendered GitHub Wiki as the only source; repository Markdown is canonical and is published by `.github/workflows/publish-wiki.yml`.

## Scoped agent instructions

PRKS keeps detailed rules close to the code they govern so agents do not need unrelated context for every task.

- `backend/AGENTS.md`: backend architecture, privacy/logging, performance, persistence, backup/restore, indexing, schema, and mutation rules. Also routes backend sync/offline, research, and Saved Views work to the shared cross-domain contracts (see its "Cross-domain contracts" section).
- `frontend/AGENTS.md`: UI/runtime behavior, workspace navigation, settings, Saved Views, research surfaces, offline/PWA, and interaction feedback.
- `tests/AGENTS.md`: test-only routing to the production-domain instructions the test exercises.
- `tests/e2e/AGENTS.md`: browser E2E workflow, isolation, assertions/waits, and debugging policy.
- `tests/ux_tour/AGENTS.md`: UX Interaction Tour safety, interaction, artifact, and isolation policy.
- `DESIGN.md`: canonical UI/interaction design authority. For UI work, read the relevant sections for the affected component rather than loading the whole document by default.
- `docs/agent-context/sync-map.md`: routing map for the large local-first/offline specifications.
- `docs/agent-workflows/cursor-projects.md`: recommended Cursor Projects delegation workflow; reference it for project/coordinator operation rather than treating it as an always-on engineering rule.

Nested `AGENTS.md` files refine these root rules for their directory scope.
When both apply, Cursor's more-specific nested instructions take precedence.
Do not encode conflicting overrides of load-bearing root invariants (storage
safety, privacy/logging, issue taxonomy, etc.); keep nested files additive
refinements and routers instead of relying on root precedence to win a conflict.

For test-only changes, follow `tests/AGENTS.md` and load the scoped production-domain instructions for the behavior under test. Backend persistence/migration tests use `backend/AGENTS.md`; frontend/offline/sync tests use `frontend/AGENTS.md`; mixed-domain tests use both.

## Commands

- Tests: `python run_tests.py` (unit). Browser E2E: `python run_tests.py --e2e` (installs Chromium into `.playwright-browsers/` if missing). Both: `python run_tests.py --all`. UX Interaction Tour (separate, opt-in, artifact-producing): `python run_tests.py --ux-tour`.
- Full E2E gate: `python tests/e2e/run.py --jobs 4` (or `python run_tests.py --e2e` / `scripts/e2e full`). Runner enforces a 1200s hard limit (`PRKS_E2E_FULL_TIMEOUT`; expected ~7-10 min; over ~15 min is a hang to investigate). Debugging one failure: `python tests/e2e/run.py --jobs 1 <test id>`. See `tests/e2e/AGENTS.md` sections "E2E test workflow" / "E2E TESTING POLICY".
- Agent/dev E2E loop (preferred): `python tests/e2e/run.py --smoke`, `--feature <group>`, `--affected`, `--last-failed`, or `--dev --feature <group>` — never iterate on the full suite. Convenience: `scripts/e2e smoke|feature|affected|last-failed|dev|full`.
- **Never run browser E2E tests while iterating** — not the full suite, not a whole module — unless the behavior can only be verified in a browser. Use unit tests, Node selftests, static contracts and API tests instead. Run the relevant E2E feature/module once a vertical slice or the milestone implementation is finished (`python tests/e2e/run.py --jobs 4 --no-pointer-capture --feature <group>` or a module path; never raw `python -m unittest`, which is serial), and the full parallel suite once after that. Debug any failure with `--jobs 1 <test id>` or `--last-failed`, never by rerunning the suite. See `tests/e2e/AGENTS.md` section "E2E TESTING POLICY".
- App, default for agents: `python prks_app.py --testing`
- Real app or Compose: only with run-real authorization from the user
- Git hooks: after clone, `./scripts/setup-git-hooks.sh` (sets local `core.hooksPath` to `.githooks`). Successful commits overwrite gitignored `prks-latest.zip` at the repo root with `git archive` of the new `HEAD`. Verify with `git config --get core.hooksPath` (expected: `.githooks`).

If the user says only "run the app", your first application execution path is `python prks_app.py --testing`. Do not run unflagged `python prks_app.py`. Do not run Docker Compose. Do not target `./data`.

## Storage

Default. Do not write `data/` or a live `PRKS_STORAGE` tree. `python run_tests.py` assigns `PRKS_TESTING=1` and `PRKS_STORAGE` to `data_testing/`, and clears `PRKS_FOR_PROCESSING_DIR` and `PRKS_LOG_FILE`. Tests never target repo `data/` or `/data`.

Run-real. An instruction to run the real app or Compose authorizes normal application writes only. Creating or updating records the way the app does.

Destructive. Deleting PDFs, deleting, resetting, or replacing the production DB, or clearing production storage needs a separate explicit confirmation that names that action. Run-real is not that confirmation.

## Issue taxonomy

Public GitHub issues must use the checked-in issue templates; unrestricted blank issues are disabled. Choose the template by the purpose of the issue, not merely by which label seems closest:

- `.github/ISSUE_TEMPLATE/bug-report.md` — a concrete malfunction, regression, or incorrect behavior that needs investigation/fixing.
- `.github/ISSUE_TEMPLATE/engineering-task.md` — concrete, bounded implementation work whose desired outcome is already decided.
- `.github/ISSUE_TEMPLATE/research-evaluation.md` — a bounded investigation/PoC where the primary deliverable is evidence and a decision; rejection or keeping the current approach is a valid result.
- `.github/ISSUE_TEMPLATE/audit-finding.md` — an engineering observation from audit/review that is not automatically approved implementation work.
- `.github/ISSUE_TEMPLATE/ux-ui-finding.md` — an observed usability, accessibility, discoverability, consistency, feedback, interaction, or visual problem.
- `.github/ISSUE_TEMPLATE/roadmap.md` — a broader product/engineering direction that should be decomposed into focused work.
- Suspected vulnerabilities, security-boundary bypasses, exploit details, secrets, private data, or sensitive reproductions are never public issue types; follow `SECURITY.md`.

Before creating any issue, search both open and closed issues for overlapping work. Keep the original tracking issue when implementation follows from a roadmap, audit finding, UX finding, bug, or research result; link the focused implementation work instead of rewriting history or creating a duplicate.

### Bug reports

Use the Bug report template when PRKS behaves incorrectly. Record reproducible steps (or say when reproduction is intermittent/not yet reliable), current vs expected behavior, frequency/regression status, tested commit/environment, privacy-safe evidence, likely affected area, acceptance criteria, validation, and related work. Bug fixes require regression coverage when reasonably automatable. Do not expose real library content in logs, screenshots, paths, names, notes, search terms, or URLs.

### Research / evaluation

Use Research / evaluation when the question is genuinely unsettled and the useful output is evidence plus a decision. State the decision to make, current state, viable options including keeping the current approach, load-bearing constraints/invariants, evaluation criteria, deliverables, validation evidence, non-goals, and related work. A completed research issue may conclude adopt, reject, defer, or keep the current approach. A proof of concept does not authorize a broad production migration unless the maintainer explicitly approved that implementation scope.

### Implementation issues

Concrete, bounded implementation work uses the Engineering / implementation task template. This includes approved feature slices, refactors, tooling/CI work, test infrastructure, maintenance, and other work where the desired outcome is already actionable. Keep the task focused enough for one PR or a small coherent PR sequence. Record the goal, context, bounded scope, observable acceptance criteria, validation plan, non-goals/boundaries, related work, and load-bearing implementation notes.

Apply the relevant `priority:*` and `area:*` labels to bugs and implementation/research issues when supported. Use `bug`, `enhancement`, or other classification labels only when appropriate. Do not apply the maintainer-controlled `candidate`/`accepted` lifecycle labels to ordinary bug, research, or implementation issues; those labels belong to roadmap, audit-finding, and UX-finding tracking flows.

## Engineering audit findings

Engineering/audit findings are tracked canonically as GitHub Issues with the `audit-finding` label, titles starting with `[Audit Finding]`, and a stable `EF-xxx` ID in the issue body. The label is the primary search key; the title prefix remains a human-readable taxonomy and fallback. Audit findings are separate from roadmap issues. Roadmap issues use the `roadmap` label as their primary search key, with the `[Roadmap]` title prefix as a human-readable fallback. The taxonomy label does not imply maintainer approval.

Before creating a new audit finding, search both open and closed issues with the `audit-finding` label, inspect existing `EF-xxx` IDs, and assign the next unused ID. Use the `[Audit Finding]` title prefix as a fallback search for legacy or misclassified issues. Re-check immediately before submitting so concurrent agents do not knowingly reuse an ID. Apply the `audit-finding` and `candidate` labels, plus the appropriate `priority:P1`/`priority:P2`/`priority:P3` and relevant `area:*` labels when the classification is known.

Lifecycle: both audit findings and roadmap proposals use `candidate`/`accepted` as maintainer-controlled state labels in addition to their taxonomy label. New items start as `candidate`; only the maintainer may replace `candidate` with `accepted`. For an audit finding, `accepted` means the finding itself is acknowledged as valid/tracked, **not** that implementation is authorized; implementation still requires explicit maintainer assignment/approval or a current task that names the finding. For a roadmap issue, `accepted` means the roadmap/planning direction is maintainer-approved, but implementation should still be split into focused work as appropriate. When an item is fixed, obsolete, rejected, superseded, or duplicated, close the issue and record the disposition; use GitHub's close reason where it fits (`completed` for resolved work, `not planned` for rejected/obsolete/superseded items, `duplicate` for duplicates) rather than inventing additional lifecycle labels.

When GitHub access is available, before proposing broad architecture, maintainability, tooling, or refactoring work, search both open and closed issues with the `audit-finding` label (and the `[Audit Finding]` title prefix as a fallback) to avoid duplicates and to understand previously identified concerns, including findings that were resolved, rejected, or superseded. Audit findings are **not implementation instructions**: do not implement one unless the maintainer explicitly assigns/approves it or the current task names it. Re-verify older findings against current `master` before acting. Suspected vulnerabilities, security-boundary bypasses, exploit details, or sensitive reproductions must not be put in public `[Audit Finding]` issues; follow the private reporting process in `SECURITY.md` instead. If a finding conflicts with this file, this file remains authoritative until the maintainer explicitly approves a policy change. Agents without GitHub access should continue using this repository's checked-in engineering rules; they are not required to have an offline copy of audit findings.

## UX / UI findings

UX/UI findings are tracked as GitHub Issues using `.github/ISSUE_TEMPLATE/ux-ui-finding.md`. This applies to automated UX audits (including Grok Bot), manual usability reviews, accessibility observations, discoverability problems, interaction inconsistencies, misleading feedback, and visual/interface defects. UX/UI findings use the `ux-finding` label as their primary search key, with the `UX:` title prefix as a human-readable fallback, and start with the `ux-finding`, `candidate`, and `area:ux` labels. `area:ux` classifies the domain and is also carried by roadmap issues, audit findings, and bugs that touch the interface, so it is not on its own a UX-finding search key. Unlike audit findings, UX/UI findings do not carry a stable `EF-xxx`-style ID; the issue number is the canonical handle. Only the maintainer may replace `candidate` with `accepted`. `accepted` means the finding is acknowledged as valid/tracked; it does **not** authorize implementation.

Before creating a UX/UI finding, search both open and closed issues for the same workflow, symptom, UI text, and likely root cause. Search the `ux-finding` label first, then `area:ux` issues plus relevant title/body terms, including the `UX:` title prefix and the legacy `UI:` and `A11y:` prefixes used by issues that predate this template. Prefer updating or commenting on an existing issue when it substantially overlaps. Do not create a second issue merely because a later audit observed the same problem differently. Group multiple symptoms only when they are tightly coupled to the same interaction lifecycle/root cause; otherwise keep findings focused.

Automated UX/UI findings must follow the checked-in template rather than inventing a new structure. Record the tested commit, affected surface, classification, confidence, reproduction steps, current behavior, expected/improved behavior, user impact, evidence, acceptance criteria, and overlap check. Distinguish directly observed behavior from implementation hypotheses. If a behavior is intermittent, say so and include reproduction frequency when available. Do not present an inferred implementation cause as confirmed without code evidence.

Apply `priority:P1`/`priority:P2`/`priority:P3` when the evidence supports triage; otherwise leave priority as needing triage. Add other relevant `area:*` labels (for example correctness, reliability, testing, sync, or tooling) only when supported by the finding. Do not use the generic GitHub `bug` or `enhancement` labels for these findings.

UX audit screenshots and artifacts must use testing/synthetic library data and must not expose real research content, notes, names, filenames, URLs, or other private library data. Suspected security vulnerabilities or sensitive security reproductions do not belong in UX/UI issues; follow `SECURITY.md`.

When a finding is fixed, obsolete, rejected, superseded, or duplicated, close it with the appropriate disposition and GitHub close reason where applicable. A UX/UI finding is a tracking artifact, not an implementation instruction: do not implement it unless the maintainer explicitly assigns/approves it or the current task names it.

## Layout

- `prks_app.py` CLI (the only process entry)
- `backend/server.py` HTTP adapter lifecycle: host/origin checks, request-size limits, library access gate, method dispatch, status/headers, JSON/ETags, static files
- `backend/api/` optional domain HTTP controllers extracted from `server.py` (parse/validate request shape, invoke domain, map errors to status/bodies). First family: `backend/api/saved_views.py`
- `backend/api_contract/` typed HTTP request/response DTOs + OpenAPI fragment (#180); domain still receives plain values
- `backend/storage/config.py` frozen storage snapshot and env parser
- `backend/storage/paths.py` storage-path derivation and testing-mode containment
- `backend/db_manager.py` SQLite
- `backend/pdf_annotations.py` canonical PDF annotation metadata
- `backend/db_migrations.py` ordered schema migrations
- `backend/backup_restore.py` verified backup/restore
- `backend/fs_durability.py` fsync convention for rename-based file replacement
- `backend/research_markup.py` research-note semantic markup parser
- `backend/research_network.py` Concepts, Positions, Arguments/Stances
- `backend/research_index.py` disposable derived note-reference index
- `backend/research_graph.py` read-only Research Graph projection
- `backend/performance.py` in-memory performance diagnostics
- `frontend/` UI (`frontend/js/tab-context.js` per-tab runtime, `frontend/js/workspace-tabs.js` stacked workspace tabs, `frontend/js/workspace-persistence.js` workspace localStorage, `frontend/js/ribbon-create.js` unified New File split button)
- `frontend/js/offline-store.js` disposable IndexedDB client cache (see `frontend/AGENTS.md` section "Offline / PWA")
- `frontend/js/offline-runtime.js` online/offline state, read-through/mutation-guard policy (see `frontend/AGENTS.md` section "Offline / PWA")
- `frontend/sw.js` app-shell/static + managed-PDF service worker (see `frontend/AGENTS.md` section "Offline / PWA")
- `tests/` unittest

New File UI must use the canonical Work creation path (`#work-modal` / `POST /api/works`) rather than duplicate creation APIs.

New substantial behavior, in order:

1. Extend an existing focused module when the behavior belongs there (`text_index` for indexing, `pdf_linearize` for linearization, `storage.paths` only for storage-path resolution).
2. Otherwise create a focused feature or domain module.
3. Use `backend/services/` when an operation coordinates multiple concerns such as DB + filesystem + PDF + indexing.
4. Prefer `backend/api/<domain>.py` when extracting a cohesive HTTP route family from `server.py` (see "HTTP adapter decomposition" below). Do not invent `routes/`, extra service layers, or frameworks ahead of real behavior.

### HTTP adapter decomposition

`server.py` remains the stdlib HTTP adapter entry (no Starlette / framework migration as an incidental follow-on). Incremental decomposition is allowed: move one cohesive `/api/...` family at a time into `backend/api/<domain>.py`.

Controller responsibility: parse path/query/body shape, call existing domain modules (`db_manager`, `research_network`, `*_sync`, `services/`, …), map domain errors to HTTP status and JSON bodies. Domain SQL, filesystem mutation, indexing, and sync semantics stay outside the controller.

`server.py` keeps lifecycle and shared transport: host/origin validation, JSON body size limits, library access gate, response encoding/ETags/cache headers, static files, and method-level dispatch into controllers.

Do not move unrelated helpers solely to shrink `server.py`. Do not introduce enterprise indirection (generic registries, DI containers, parallel route frameworks). Preserve routes, status codes, error bodies, ETags, cache headers, security checks, and request-size limits when extracting.
