# PRKS

PRKS is a local research library. Python 3.12 stdlib HTTP, SQLite, vanilla JS. Exact startup, configuration, and safety contracts live in README.md.

Documentation map: `docs/wiki/` is the reviewed source for the GitHub Wiki and for detailed current feature/user behavior (workspace, research network, Saved Views, command palette, and similar product surfaces). Keep exact run/config/safety contracts (host/port, Docker publish, env vars, schema version, auth warning) in `README.md`, implementation rules in this file, UI/interaction authority in `DESIGN.md`, and fast-moving local-first status in `docs/local-first-rollout-status.md`. Do not update the rendered GitHub Wiki as the only source; repository Markdown is canonical and is published by `.github/workflows/publish-wiki.yml`.

## Commands

- Tests: `python run_tests.py` (unit). Browser E2E: `python run_tests.py --e2e` (installs Chromium into `.playwright-browsers/` if missing). Both: `python run_tests.py --all`. UX Interaction Tour (separate, opt-in, artifact-producing): `python run_tests.py --ux-tour`.
- Full E2E gate: `python tests/e2e/run.py --jobs 4` (or `python run_tests.py --e2e` / `scripts/e2e full`). Runner enforces a 1200s hard limit (`PRKS_E2E_FULL_TIMEOUT`; expected ~7-10 min; over ~15 min is a hang to investigate). Debugging one failure: `python tests/e2e/run.py --jobs 1 <test id>`. See "E2E test workflow" / "E2E TESTING POLICY".
- Agent/dev E2E loop (preferred): `python tests/e2e/run.py --smoke`, `--feature <group>`, `--affected`, `--last-failed`, or `--dev --feature <group>` — never iterate on the full suite. Convenience: `scripts/e2e smoke|feature|affected|last-failed|dev|full`.
- **Never run browser E2E tests while iterating** — not the full suite, not a whole module — unless the behavior can only be verified in a browser. Use unit tests, Node selftests, static contracts and API tests instead. Run the relevant E2E feature/module once a vertical slice or the milestone implementation is finished (`python tests/e2e/run.py --jobs 4 --no-pointer-capture --feature <group>` or a module path; never raw `python -m unittest`, which is serial), and the full parallel suite once after that. Debug any failure with `--jobs 1 <test id>` or `--last-failed`, never by rerunning the suite. See "E2E TESTING POLICY".
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
- `frontend/js/offline-store.js` disposable IndexedDB client cache (see "Offline / PWA")
- `frontend/js/offline-runtime.js` online/offline state, read-through/mutation-guard policy (see "Offline / PWA")
- `frontend/sw.js` app-shell/static + managed-PDF service worker (see "Offline / PWA")
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

## Logging privacy

Logs are metadata-only. Never log user/library content, request bodies/query strings,
titles, notes, annotations, names, filenames/absolute paths, user URLs, headers,
raw browser messages/stacks, qpdf stderr, or str/repr(exception).

Use `backend/log_safety.py` for route/id/error normalization.
Unexpected traceback logging must go through the privacy-safe formatter.
Lowering `PRKS_LOG_LEVEL` / `PRKS_LOG_FILE_LEVEL` must never unlock raw data.

## Performance

Performance work must measure before optimizing.

Runtime performance metrics are aggregate and privacy-safe; never record request
bodies/query strings, search terms, library metadata, filenames/paths, SQL params,
or dynamic user labels.

Do not add schema indexes, caching, threading or SQLite tuning solely because an
endpoint appears theoretically expensive; use measured diagnostics first.

Performance instrumentation must never be required for application correctness.

## Backup and restore

Backup/restore code must never operate on production storage during tests.

Do not add a persistent storage component without classifying it as canonical,
derived, operational, or conditional in backup inventory.

`thumbs/`, `prks_text_index.db`, and `prks_research_index.db` (including WAL/SHM) are derived. They are not
required backup state and must be rebuilt after restore.

Restore validation must complete before live canonical state is modified.

Crash before committed restores the previous library. Only committed keeps
the restored library.

Restore journal writes, staged payload files and component renames follow the
`backend/fs_durability.py` convention, because recovery has to hold after a
machine crash and not only a process crash. A journal phase counts as persisted
only once its contents are synced, the journal is replaced and the journal
directory is synced; a component rename counts as completed only once every
directory it changed -- both parents when the move crosses directories -- is
synced; an extracted payload file is flushed before anything can rename it onto
a canonical name. Never record a phase, and never clean up rollback material,
whose durability boundary was refused. The commit record is the single
exception: once it has been written, rolling back is the unsafe answer, so
restore keeps the journal and the rollback tree and lets the next start resolve
whichever phase survived.

Do not use `ZipFile.extractall()` on unvalidated backup input.

Do not log archive paths, PDF names, manifest contents, or raw restore errors.

## PDF text index

`prks_text_index.db` is derived, never canonical.

Any code path that changes a work's managed PDF identity must synchronize the
text index or rely on the central reconciliation mechanism.

Do not write extracted PDF text into the canonical main DB solely for search.

Do not add text-index files to backups.

Do not treat an extraction exception as a successful empty PDF.

Do not preserve stale searchable text after the canonical PDF changes.

Derived-index schema corruption may be repaired by recreating the index;
canonical `prks_data.db` must never receive that treatment.

Normal startup must not run a full FTS5 integrity-check on a healthy derived
index. Strong FTS verification belongs to explicit rebuild/repair or when
FTS is already marked suspect.

## Post-delete cleanup recovery

Deleting a Work commits the canonical row first and does filesystem/derived
cleanup afterwards. Do not make that one transaction: SQLite cannot make
external side effects atomic, and holding a write transaction open across
`os.remove()` trades a recoverable leak for a locked database.

The four post-commit categories have **different** recovery models, and that
asymmetry is deliberate -- do not unify them:

| Category | Recovery owner |
| --- | --- |
| text index | `text_index.reconcile_all()` (`removed_orphans`), at startup |
| research index | `PRKSResearchIndex.reconcile_all()`, at startup |
| thumbnails | `prune_orphan_pdf_thumbnails()`, at startup |
| managed PDF | the durable claim below |

The first three are derived and disposable: a stale row is a wrong search hit
or a wasted cache file, and rebuilding from canonical state is both simpler and
safer than per-artifact retry records. Do not give them retry rows.

A managed PDF is different, and it is the only one that needs durable state:
the bytes are private research material, and `works.file_path` -- the one thing
that said which file belonged to that Work -- is destroyed by the very commit
that precedes the cleanup. Nothing could reconstruct it afterwards.

`pending_pdf_cleanup` (schema 16) is that record. Its lifecycle is bounded and
has exactly one shape:

- **Written** by `delete_work_record_on_conn()` **inside the Work-delete
  transaction**, and only when no surviving row references the basename. That
  placement is the point: a crash between the commit and `os.remove()` still
  leaves a retryable claim. Only filesystem *work* stays outside the
  transaction; the identity is recorded inside it.
- **Stores a managed basename, never a path.** An absolute path would bind the
  claim to one storage root and survive a restore into another. `filename` is
  the primary key, so two Works sharing a PDF, a replay, or repeated retries
  can never accumulate a second permanent row for the same bytes.
- **Removed** only once the cleanup it owns is finished: the bytes are gone
  (`os.remove()` succeeded, or `FileNotFoundError` -- already gone is the
  successful terminal state), or a live Work now references the name so nothing
  is owed. A failed removal, an unreadable catalogue and an uncontainable name
  all keep the claim.
- **Retried** by `retry_pending_pdf_cleanup()`: one bounded pass
  (`PENDING_PDF_CLEANUP_RETRY_LIMIT`) at startup, after each Work deletion so
  recovery does not require a restart, and after a restore rebinds a library
  (which brings back both the claims and the bytes they describe, long after
  startup ran). Never an unbounded startup scan, and never a general job queue.
- **Selection rotates.** `last_attempt_at` is stamped on every claim a pass
  tried and could not settle, and selection takes never-attempted claims first
  and then the least recently attempted. Ordering by `recorded_at` alone let a
  handful of permanently unsettleable claims fill every bounded pass and strand
  every later orphan.

Three safety rules are absolute:

1. **Re-ask the live catalogue immediately before every retry deletion**, never
   a deletion-time `managed_pdf_still_referenced` snapshot. A record only ever
   says a file was orphaned once; the catalogue says whether it still is.
2. **That check is three-valued.** `None` means the catalogue could not be
   read, which is not `False` and not `True`: nothing is deleted and nothing is
   settled, so the claim survives for a readable database later. A boolean that
   failed closed would either strand orphans or discard a claim over a
   transient error.
3. **The check and the retirement are ONE transaction**
   (`settle_claim_if_referenced()`). Retiring a claim by basename alone races
   the deletion of the last referring Work: that deletion writes its claim
   inside its own transaction, so a pass that observed the Work still alive
   could erase the very claim the deletion depends on, and a failed unlink
   would then have no durable record. Serialized, both orders are safe.

Path containment is unchanged: `safe_pdf_path_under_dir()` remains the
filesystem boundary, and a name it refuses is never resolved to a path.

**The reference check and the unlink are not separable.** Both are held under
the basename's shared `managed_pdf_path_lock()` -- the same lock the COW
replace path takes -- because `POST /api/works` may point a new Work at an
EXISTING managed PDF rather than uploading one. Without a shared guard, cleanup
could decide a name is unreferenced and unlink it in the gap before that row
commits ownership, leaving a live Work referencing bytes that are gone. The
create path takes the same lock across its commit when it adopts a name it did
not just mint; an upload needs no guard, because
`store_new_managed_pdf_bytes()` mints a unique name and creates it exclusively.

`pending_pdf_cleanup` lives in `prks_data.db` and is therefore canonical backup
state, like the sync ledger -- it is operational rather than user-visible, but
it must travel with the library it describes, because a restore brings back the
same `pdfs/` tree. It is never a reason to delete bytes on its own: a restored
claim is re-evaluated against the restored catalogue like any other.

The HTTP and durable `DELETE_WORK` paths converge here. Both commit the row,
both record the claim in that same transaction, and both run the same
post-commit cleanup, so a replayed operation cannot create duplicate cleanup
state -- the ledger replay carries no `file_path` at all, and the claim it
would have written already exists.

## Database schema changes

`backend/db_schema.sql` describes the complete latest schema for fresh databases.

Any schema/data change needed by an existing database requires:

1. bump `LATEST_SCHEMA_VERSION`;
2. add exactly one ordered migration;
3. update `db_schema.sql` to the same final state;
4. add fresh-DB and upgraded-DB tests.

Never change schema only in `db_schema.sql`.
Never ALTER/CREATE/DROP schema objects from feature/request code.
Never swallow migration DDL failures.
Never manually bump `schema_version` before migration success.
Migrations may modify SQLite state only, not managed filesystem data.

## Bulk work mutations

Bulk work mutations must be validated before modification and commit atomically.

Do not implement frontend bulk operations as one HTTP mutation per selected work
when a transactional bulk backend operation exists.

Bulk selection is ephemeral route-local UI state; it is not canonical application
data.

Adding new bulk actions requires explicit server-side action validation. Never
allow arbitrary field names or dynamic method dispatch.

Bulk deletion is not part of generic organization semantics and requires a
separate reviewed design.

## Command palette

Command palette commands must use explicit allowlisted actions. Never execute
user query text as JavaScript or dynamic method names.

Navigation commands must use prksNavigate() and existing canonical hash routes.

Do not introduce duplicate CRUD forms solely for command-palette actions; reuse
existing modals and route handlers.

Global command shortcuts must not steal keyboard shortcuts while the user is
typing/editing or while another modal owns focus.

Palette queries are ephemeral UI state and must not be persisted or logged.

Any command that depends on transient command-palette operation state (e.g.
`state.splitPlacement`, set by a pane menu's explicit Split right/down request)
must snapshot that state before calling `closePalette()`, because closing the
palette clears it. Read the snapshot afterward, never the live state.

## Workspace navigation

Internal PRKS navigation uses prksNavigate.

Do not write window.location.hash directly from feature code.

Do not use window.open for ordinary internal PRKS routes.

Normal navigation targets the originating workspace context (the TabContext that owns the link, or the focused context for palette/global commands). Sidebar chrome navigates Main.

Ctrl/Cmd-click and middle-click target a background PRKS tab.

The shared link layer intercepts anchor clicks in the capture phase, so
`handleNavEvent` must bow out entirely — no `preventDefault`, no
`stopPropagation` — for a destination the owning component has explicitly
marked `aria-disabled="true"`, in every intent (same tab, background tab,
tile). That component then refuses the activation and explains why, from an
ordinary bubble-phase `click`/`auxclick` handler; a middle click only ever
arrives as `auxclick`. Offline pages rely on this to keep a relationship's real
`href` inspectable while saying the destination is not cached; without it the
capture-phase handler would navigate first and swallow the explanation.
`onMiddleMouseDown` is deliberately *not* part of that contract: it only
suppresses the middle-button mousedown default (autoscroll) and never
navigates, so it has nothing to bow out of.

Alt-click and `prksNavigate(..., { target: "tile" })` open a Secondary leaf when the route is tile-capable.

User-facing copy says "Split view", "Split right", "Split down", "Make main", "Hide from split", "Hide split" / "Show split". Internal APIs stay `tile`, `secondaryTree`, split-node IDs, and `target: "tile"` — never user-facing.

Existing parked tabs should be tiled through `prksWorkspaceTileTab(tabId)`, not duplicated through `navigate(... { target: "tile" })`. Split right/down onto a specific focused leaf go through `prksWorkspaceSplitLeaf(targetLeafTabId, axis, options)`, reusing an existing tab (`options.tabId`) or creating one (`options.hash`) — never duplicating.

Main never recursively splits; it is permanently the single root pane. Secondary is `workspace-tree.js`'s recursive `leaf`/`split` tree (`secondaryTree`): `null` (no Secondary), a bare `{ type: "leaf", tabId }` (the common single-Secondary case — do not wrap it in a pointless split node), or a `{ type: "split", id, axis, ratio, first, second }` node whose children are themselves leaves or splits. A tab occurs at most once in `secondaryTree`, and Main's own tab never appears inside it. Split-node IDs are stable per-runtime keys (never array index, DOM position, or a child's tab ID) used for DOM reuse, resize ownership, and targeted mutation; they are in-memory only, never persisted. Route capability for a Secondary leaf covers works, people, concepts, positions, arguments, playlists, and folder detail (`folder-detail`); the Folder library index (`#/folders`) stays main-only. Do not add further route types as an incidental follow-on to recursive splitting.

Tree mutation goes only through `workspace-tree.js`'s pure helpers (`findLeafByTabId`, `replaceLeaf`, `splitLeaf`, `removeLeaf`, `replaceTabId`, `setSplitRatio`, `normalizeTree`, `validateTree`, `collectLeafTabIds`, `containsTab`, …); routes and UI code must never mutate `secondaryTree` structure directly. Removing a leaf always normalizes the tree afterward: a split node left with one child collapses into that child, repeated upward, so the tree never carries a redundant single-child split node; if the last leaf disappears, `secondaryTree` becomes `null` and the view returns to stacked.

At most `PRKS_MAX_VISIBLE_TABS` (4: 1 Main + 3 Secondary) TabContexts are ever mounted at once. Split right/down are disabled with an explanation once the cap is reached; ordinary New Tab is unaffected and still creates a parked tab.

Generic `target:'tile'` navigation and "Open in split view" are additive: they never evict an existing Secondary leaf. There is no user-facing "Replace split pane" operation. Default placement is unambiguous only when there is no Secondary tree (new bare leaf), exactly one Secondary leaf (split it), or a focused Secondary leaf in a recursive tree (split that one); otherwise placement is ambiguous and the operation is fail-closed — no new logical tab, no tree mutation, no mount, no paint, no leave check, just the ambiguity/cap announcement and `false`. The same fail-closed rule applies at the pane cap. New Tab is the only fallback that intentionally creates a parked logical tab regardless of ambiguity or the cap.

Close: parked closes only that tab. A Secondary leaf's close removes and normalizes the tree — it must never remount or otherwise touch any other leaf's TabContext — and prefers focusing the closest surviving sibling in the collapsed subtree, else the nearest remaining leaf in deterministic depth-first tree order, else Main. Main close promotes the first surviving Secondary leaf in that same deterministic order, else the right tab-strip neighbor, then left, then Home. Do not flash Home while a successor exists. Keep leave guards. Batch close (other tabs / tabs to the right) preflights every mounted tab being closed and aborts entirely on reject.

Hide/park is two distinct operations. Global "Hide split" (the shell Split button) parks every currently-visible Secondary leaf at once, atomically (depth-first preflight, stop at first rejection, no partial parking), but preserves the whole `secondaryTree` logically for "Show split" to remount unchanged. Local "Hide from split" (per-leaf, context-menu only) removes just that one leaf from the tree and normalizes it, while keeping its logical tab open and parked — distinct from Close, which destroys the tab. Neither ever duplicates a tab: reopening a parked leaf reuses its existing tab ID.

Make main is an in-place role swap, valid from any Secondary leaf at any tree depth: the promoted leaf becomes `mainTabId`, and the old Main takes over that exact leaf position (`replaceTabId`) — never a tree rebuild, a move to the root, or a sibling reorder. Every leaf's TabContext identity (including the promoted and demoted ones) survives untouched; only DOM placement/role and Main-owned chrome (URL, History, title) change.

Promoting a Secondary leaf to Main (`makeMain`, a Secondary navigating to a non-tile-capable route, or a visible Secondary's popstate promotion) is `Promise<boolean>` and preflights the old Main through the shared `preflightMainPromotion()` helper before any state mutation. If the old Main supports tiling it is only demoted into the promoted leaf's exact old position (the role swap above) and never leaves, so no leave prompt runs. If the old Main does not support tiling, promotion would cold-park/unmount it, so it must pass `awaitLeave(oldMain.id, oldMain.route)` first — the old Main's own current route, not the incoming target's, so autosave/owned-draft guards run without a false route-change read. A rejected preflight is an atomic no-op: no tab/tree/URL/mount mutation, and (for popstate) the URL is restored. `promoteSecondaryToMain()` itself stays the synchronous, unchecked state-mutation primitive; it is only ever called after that preflight succeeds. Pure startup reconciliation has no mounted dirty runtime and may keep using the primitive directly.

Focusing a tile must not promote Main, change the URL, remount, or reset PDF/editor. Clicking a *visible* Secondary leaf's global tab-strip entry focuses it in place; it does not promote it — that is reserved for parked tabs and explicit Make main. After close, hide (global or local), split, Make main, or narrow fallback, restore focus to the resulting focused tile or its workspace tab control.

User-facing menu copy is Split view / Split right / Split down / Make main / Hide from split / Hide split / Show split. Do not expose `tileTab`, `secondaryTree`, split-node IDs, or `mainTabId`.

Secondary tiled headers keep grip, icon, title, a **Pane actions** control, and Close. Infrequent pane actions (Make main, Split right/down, Hide from split, move) must open the existing `prksWorkspaceOpenTabMenu` from `workspace-tab-menu.js` — do not add a second tile-header action list or Split dropdown in `workspace-tiling.js`.

Parked tabs use two lifecycles. Cold-parked tabs perform no API requests and own no live
DOM/resources. Ordinary global-tab
switching may warm-suspend an actual PDF Work (`ctx.getResource('pdf')`) by reparenting its
existing root into `#prks-tab-warm-parking`; warm resume reparents that same root and requests
only a container resize, never a route render, Work/PDF fetch, viewer init, fit, reload, or
layout. A Work with active metadata editing is never warm-parked: after leave approval it
cold-unmounts so normal TabContext teardown discards the draft. Warm parking is a three-context
LRU. Eviction, Close, batch close, application teardown,
Hide split / Hide from split, and narrow fallback cold-unmount and destroy normally. Non-PDF
routes always cold-park. Warm-cache state is runtime-only and never persisted.

Stacked mode mounts one TabContext (Main). Tiled mounts Main plus every visible Secondary leaf in the tree, up to the visible-pane cap. Do not introduce a fifth mounted context.

URL always represents Main, no matter how deep the focused Secondary leaf is nested. Secondary routes never mutate browser History. Make Main uses replaceState.

The right panel always follows the focused TabContext. Feature navigation uses the originating TabContext when it is known; do not use focused context as a substitute for an originating element/context. Because the shared `#panel-content` lives outside every tile's DOM, a click originating inside it resolves its owning tab from `panel.dataset.prksOwnerTabId` (verified against a live TabContext), never from current Main, the focused tab, or `location.hash`. Main remains a fallback only for genuinely shell-global links that have no TabContext or right-panel owner at all.

Do not add route-level global runtime state.

Tab switching must not create contextual Back origins.

Workspace logical state is persistent; workspace runtime state is ephemeral. `frontend/js/workspace-persistence.js` owns all workspace `localStorage` behavior (schema, validation, debounce, restore, corrupt-snapshot cleanup). Do not write workspace storage from `workspace-tabs.js`, `workspace-tree.js`, `workspace-tiling.js`, `workspace-split.js`, `workspace-drag.js`, or `tab-context.js`. Never persist TabContext/runtime objects, editor drafts, effective constrained ratios, `narrowFallback`, or split-node runtime IDs. Restore happens before first normal mount. Parked restored tabs must not fetch. The current startup URL outranks a persisted Main route. Persistence failures must not break app startup.

If a live workspace's serialized snapshot ever fails persistence validation (e.g. a tab parked on an unknown route via the router's "Section In Development" fallback), the writer invalidates/removes the previously-stored snapshot rather than leaving it behind looking authoritative for a workspace that no longer matches it, and resets its own change-tracking so the next persistable snapshot still writes normally. This never touches the running in-memory workspace and never globally disables persistence — it resumes as soon as the user returns to a persistable/known route.

Root Main/Secondary width is workspace-owned: one normalized ratio (`mainSplitRatio`,
default `0.58`) lives in workspace-tabs.js state, alongside `mainTabId` /
`focusedTabId` / `secondaryTree`. Every internal Secondary split node owns its own
local `ratio` (default `0.5`) inside its own tree node — never on either child tab,
never inherited from the root ratio or from a sibling split. Routes must never
store, read, or modify any of these ratios. Canonical preferred ratios persist
through `workspace-persistence.js` only; do not write them from feature code, and
do not persist constrained/effective ratios.

Main/Secondary ratio follows roles, not tab IDs. Make Main, adding/removing
Secondary panes, Hide/Show split, and the narrow responsive fallback must never
invert or reset the root ratio or any nested split's ratio.

The Main/Secondary divider and every nested Secondary split divider
(`workspace-split.js`, class `.prks-splitter`) share the one separator
implementation, keyed by split-node ID for nested separators. Do not implement
independent divider drag, keyboard-resize, or ARIA logic in `works.js`,
`works-pdf.js`, other route components, or a second implementation inside
`workspace-tiling.js` for nested splits. `workspace-tiling.js` only calls into
`workspace-split.js`; it does not own pointer, keyboard, ARIA, or persistence
logic itself.

Divider resizing (root or nested) is layout-only and must not remount
TabContexts, unmount/mount a route, re-render a route, or trigger a leave guard.
It must not run a full workspace paint on every pointer-move; the canonical
ratio updates via `prksWorkspaceSetMainSplitRatio(ratio, { paint: false })` (root)
or `prksWorkspaceSetNestedSplitRatio(splitId, ratio, { paint: false })` (nested),
and the DOM applies the resulting pixel width/height through a CSS custom
property. A nested split's minimum sizes are measured against that split's own
container only, never the window or workspace root; if its container is too
small for both children's minimums, its ratio clamps safely to that split's own
midpoint rather than producing a negative/overflowing pane.

Tile-local components (PDF viewer, EasyMDE, other detail routes) respond to
divider resizing through their own existing container-aware sizing /
ResizeObserver lifecycle. Do not use `window.dispatchEvent(new Event('resize'))`
as a substitute for tile-local container sizing.

TabContext owns route runtime:

- route state → TabContext (`ctx.navigation`, `ctx.lastResolvedRoute`, `ctx.entity`)
- route DOM → `ctx.root`
- page-local lookup → `ctx.query` / `data-prks-role` (`ctx.domId` only for ARIA)
- async lifetime → `ctx.beginRoute()` / `ctx.isCurrent(generation)`
- live resources → `ctx.resources` / `ctx.setTimer`
- shell → main/focused context (`prksGetMainTabContext`, `prksGetFocusedTabContext`)

People-library search runtime belongs to rendered `.prks-people-library` root
(`root.__prksPeopleLibraryState`), never a `window` singleton or tab-ID global map.
Rerender only that root. SessionStorage preserves shared query preference; it is not
workspace persistence or route state.

Group-library runtime likewise belongs to rendered `.prks-group-library` root, never
a route-scoped `window` singleton. `personGroupEditing` and
`personGroupMembersEditing` are mutually exclusive TabContext UI modes: metadata
editing owns the right panel; membership management owns the Members section.

Person profile edit state, including selected Person Groups, belongs to the Person's
TabContext. Never store Person-editor selections or drafts in a window-global
singleton. Right-panel reconstruction must render from the owning TabContext draft.

Person profile and Work metadata drafts are TabContext-owned runtime state. Never
persist them through workspace persistence. Merely focusing another mounted pane is
non-destructive and must not prompt. Any operation that will replace a route, unmount,
park, or destroy a context with a dirty editable draft must preflight through
`prksCanLeaveTabContext`; rejection is an atomic no-op that preserves route, tab order,
tree topology, focus, draft, and editor DOM. Batch operations preflight all affected
mounted contexts before mutating any of them.

Stacked mode: one mounted context. Tiled mode: Main + every visible Secondary leaf (up to the visible-pane cap), each with an independent TabContext. Do not store route-scoped state on `window`. The Research Graph
is `ctx.getResource('researchGraph')`; no module-level singleton fallback.

### Workspace drag and drop

`workspace-drag.js` is an alternate input path for existing canonical workflows, never a
parallel layout model. Drag state (`active`, `source`, `origin`, `pointerId`, `target`) is
transient and lives only in that module's own closure; it is never canonical/persisted and
never written to `localStorage`, `sessionStorage`, or IndexedDB.

`workspace-tree.js` owns recursive tree transformations (including drag-driven pane moves, via
`moveLeafRelativeToTarget`); `workspace-tabs.js` owns global tab ordering (via
`prksWorkspaceReorderTab`). `workspace-drag.js` only computes/previews user intent and invokes
those same canonical APIs on drop — it must never mutate `secondaryTree` or `state.tabs`
directly, and must never mutate either while the pointer is merely moving/hovering (preview
only; the DOM insertion marker/edge overlay are pure visual feedback with no state effect).

A pane move is one atomic tree transaction. Moving a visible pane is spatial repositioning, not
a leave operation — it must not run PDF leave confirmation, must not flush-for-unmount Research
Notes, and must not remount the moved pane or any unrelated pane. Parking a pane (grip → tab
strip) is equivalent to "Hide from split" and does require leave preflight; a rejected leave
must leave the tree, tab order, and focus completely unchanged.

Parked-tab insertion into the Secondary tree always reuses that tab's existing logical tab ID —
never a duplicate tab, never a second mount. Main can never be inserted into `secondaryTree` by
drag; only the existing "Make main" action changes Main ownership. The pane cap
(`PRKS_MAX_VISIBLE_TABS`) blocks new visible leaves being added by drag, not existing panes
being moved.

Drag cancellation (Escape, `pointercancel`, `lostpointercapture`, window blur, responsive
transition, external tile removal) must run through one idempotent cleanup that removes every
transient listener, the preview element, every overlay/marker, the autoscroll animation frame,
source styling, and the body drag class, and must leave canonical workspace state completely
untouched. `prksWorkspaceCancelActiveDrag` exists specifically so `workspace-tiling.js` can
defensively end an active drag before a real narrow/wide transition and before pruning any
stale tile that could contain the live drag source — it is always safe to call when nothing is
active. `workspace-drag.js` must never itself mutate responsive/narrow-fallback state.

## Settings

Settings category navigation is presentation state; it must not create another
settings persistence model. The active category lives only in an in-memory module
variable, never `localStorage`, never `/api/settings`, never a URL hash.

Inactive Settings category panels are hidden and inert, never removed/recreated.
Switching categories must not reset a running Backup/Maintenance operation, a
chosen restore file, or any control's in-progress value.

Diagnostics data loads only on first activation of the Diagnostics category, not
whenever Settings opens. Revisiting Diagnostics reuses the retained snapshot;
only the explicit Refresh action re-fetches.

## Saved Views

Saved Views store search definitions, never cached work membership.

Executing a Saved View must reuse the normal PRKS search implementation; do not
create a parallel search engine for Saved Views.

Saved View names and search definitions are private canonical user data. Never
include them in logs or performance diagnostics.

Any future Saved View definition expansion requires an explicit schema/search
contract rather than arbitrary executable rules.

Do not add per-view polling or background notifications as an incidental Saved
Views feature.

## Research network

Concepts, Positions, and Arguments/Stances are persistent canonical records in
`prks_data.db`. Work↔Concept membership is never stored as Work metadata.

The Work→Concept relation exists only because `works.text_content` contains
explicit `[[concept:Name]]`. `private_notes` must not participate. Ordinary
prose never auto-links. Unknown valid Concept names are created on note save in
the same transaction as the note. Removing every note reference does not delete
the Concept.

Concept aliases/search keys resolve note references. A Concept identity
rename preserves the old name as an alias. Capitalization or spacing-only
display changes update `concepts.name` without a new alias; existing notes
still resolve through the same normalized identity. Multi-parent hierarchy
is allowed; cycles are rejected. Do not add Glossaries/Concept Senses or
Debates/Theories as an incidental follow-on.

Arguments/Stances use stable IDs in notes (`[[argument:A-id|Label]]`) and do not
auto-create from unknown markup. Every target requires a verdict. Incoming
Counter/Response Arguments are reverse queries of target relations, not a
separate stored list.

`prks_research_index.db` is derived, never canonical. Mention offsets may be
stored; surrounding note prose must not be. Unknown/corrupt derived schema may
be deleted and recreated. Never touch `prks_data.db` because the research index
is corrupt. Derived indexing failure must not roll back a valid canonical note
save. Do not add research-index files to backups. Concept and Argument deletion
must inspect canonical `works.text_content` with `parse_research_markup()`;
the derived index is never the sole authority for those destructive checks.

Concept, Position, and Argument names, definitions, aliases, main text, verdict
labels, page ranges, markup, and backlink snippets are private. Never log them.

## Research Graph

The Research Graph is a read-only derived projection. It is never canonical
relationship storage and must never authorize a destructive mutation.

Graph node IDs must be namespaced by entity type; raw PRKS IDs are not globally
unique across record types.

Work→Concept and Work→Argument graph edges represent explicit research-note
semantic references only. Never infer graph relations from plain prose, PDF
text, tags or search similarity.

Argument source edges and note-mention edges have different semantics and must
remain distinguishable.

Do not add canonical graph persistence or a main-DB schema migration merely to
render the Research Graph. Disposable server projection snapshots may be cached
by the offline runtime; graph UI/layout state is never persisted.

The research graph and research-reference index are read-only projections. Their
presence or absence must never authorize deletion or other canonical mutation.

The global right panel on the Research Graph route is selection-aware, not
route-aware: it follows the focused Graph runtime's own `hasSelection()`
(`ctx.getResource('researchGraph')`), never DOM markup and never an unfocused
Graph tile. Graph filter state (which node/relation types are visible) is
runtime-only; do not persist it to `localStorage`, workspace persistence,
`/api/settings`, or the URL. Toggling the graph inspector must never call
`fit()` or rerun the Cytoscape layout — only a container `resize()`.

## Client request coordinator

Ordinary first-party `/api` traffic from `frontend/js` uses `prksRequest()`. Do not
call `fetch()` for those requests. Raw `fetch()` is a reviewed bypass only:
`POST /api/client-errors` keepalive, backup progress/stage/restore, and external
YouTube oEmbed. The coordinator does not assign or replace `window.fetch`.

Reads are bounded (foreground 4, background 1). Mutations are serialized (max 1)
and never automatically retried. Safe GET retry covers network errors and
502/503/504 only, up to the initial attempt plus two retries.

Only complete-value autosaves may set `coalesceKey` (research notes and private
notes). Creates, deletes, relationships, bulk, reorder, PDF, and backup must not.

`window.__prksRouteAbortController` is for route reads. Canonical writes survive
navigation. Route generation (`window.__prksRouteGen` / `prksRouteStale`) still
guards paint after abort.

Coordinator diagnostics are aggregate counters and occupancy only. They must never
contain private URL, query, body, Work ID, search text, or coalesce-key content.

Persistent cache, IndexedDB, outbox, and offline synchronization do not belong in
`frontend/js/request-coordinator.js`. The burst catalog cache is memory-only and
short-lived. It is not offline support. The coordinator may make a best-effort
reachability signal (dynamic lookup of `prksOfflineNoteRequestSuccess` /
`prksOfflineNoteRequestFailure`) at the real `fetch` boundary only: a resolved
`Response` of any HTTP status means PRKS answered; a non-abort transport
rejection after retries are exhausted means it did not. Managed PDF GETs
(`/api/pdfs/...`) are excluded: the service worker may resolve those from Cache
Storage without the PRKS process answering. Memory-cache hits,
deduped completed responses, `AbortError` / route cancellation, `response.clone()`
failure, and JSON/domain errors must not be treated as a connectivity change.
Do not add another probe timer in the coordinator — recovery stays in
`offline-runtime.js`.

## Offline / PWA

Offline/local-first work has a large, load-bearing domain contract that is
intentionally scoped out of this global file. **Before changing offline,
local-first, synchronization, service-worker, conflict/revision, or client-cache
behavior — or tests that encode those contracts — read
`docs/agent-rules/offline-pwa.md` completely.**

Global rules still apply, especially:

- disposable read cache state belongs in `offline-store.js`; durable
  unsynchronized user intent belongs in `local-store.js`;
- never make disposable cache state authoritative for unsynchronized user work;
- online and offline mutation paths must converge on the same domain semantics;
- preserve revision/conflict and acknowledgement contracts rather than adding a
  second ad-hoc sync path;
- update the detailed domain contract and its regression tests when intentionally
  changing an offline/local-first invariant;
- current rollout status remains in `docs/local-first-rollout-status.md`.

The detailed operation families, projection rules, dependency ordering,
conflict semantics, service-worker behavior, test contracts, and historical
load-bearing constraints are maintained in
`docs/agent-rules/offline-pwa.md`.

## Interaction feedback

Do not replace the synchronous pending-annotation-sync navigation guard
(`prksCanLeaveTabContext` and the mirrored check inside `prksRenderTabRoute`,
both in `frontend/js/app.js`) with `prksConfirmDialog`/`prksConfirmDestructive`
without redesigning the navigation contract. It must stay a native
`window.confirm`; this synchronous PDF safety decision must complete before any
async editable-draft confirmation begins. This is the sole native-confirm exception.
Person profile and Work metadata dirty-draft leave guards use the styled async
confirmation and are awaited by workspace `awaitLeave()` before mutation.

Every other confirmation, including both PDF annotation-delete entry points
(the annotation editor's Delete button and the annotation-list row's Delete
button), goes through the shared `prksConfirmDeletePdfAnnotation()` helper in
`frontend/js/ui.js`, which wraps `prksConfirmDestructive`. Keep both entry
points on that one helper rather than duplicating the confirmation copy.

`prksSetButtonBusy(button, busy, { busyLabel })` (`frontend/js/ui.js`) is the
shared busy-button helper for async mutations with meaningful latency. It
snapshots and restores exact button contents (icon markup included), so
callers must restore it from a `finally` rather than only on the success or
failure path.

## E2E TESTING POLICY

PRKS E2E is Python unittest + Playwright Chromium via `tests/e2e/run.py`
(not an npm Playwright project). Tiers:

| Tier | Command | Meaning |
| --- | --- | --- |
| Targeted | `python tests/e2e/run.py --jobs 1 <test id>` | One test/class/module |
| Feature | `python tests/e2e/run.py --feature graph` | One domain group |
| Smoke | `python tests/e2e/run.py --smoke` | Curated essential suite (~9 tests) |
| Affected | `python tests/e2e/run.py --affected` | Git diff → feature groups |
| Last-failed | `python tests/e2e/run.py --last-failed` | unresolved failures from prior runs |
| Dev | `python tests/e2e/run.py --dev --feature tabs` | Fail-fast + no pointer-capture |
| Full | `python tests/e2e/run.py --jobs 4` | Complete regression gate (runner hard-limits at 1200s; `timeout 1200 …` still fine) |

Convenience wrapper: `scripts/e2e smoke|feature|affected|last-failed|dev|full`.
Catalog: `python tests/e2e/run.py --list-features`. Mapping lives in
`tests/e2e/policy.py` (declarative, edit there).

Reports always name the tier. A PASS on targeted/feature/smoke/affected/dev
is **not** equivalent to a full E2E gate. Say which tier ran.

### During implementation

1. Run the relevant unit/self-tests first.
2. Run only E2E tests for the changed feature (`--feature` or a test id).
3. Prefer `--affected` when the working-tree diff is the right scope.
4. Use `--dev` (fail-fast, no pointer-capture) while debugging. Retries are
   already off; do not add retries that hide failures.
5. Stop quickly on failures (`--fail-fast` / `--dev`).
6. If an E2E test fails, reproduce that specific failure before any broader run.
7. After fixing, rerun the failed test (`--last-failed` or the test id).
8. Then rerun the affected feature group.
9. Then run `--smoke` if the change touches shell/navigation/shared paths.
10. Run the complete E2E suite **only** when the implementation is otherwise
    ready for final validation.

Agents must not perform more than **two consecutive full E2E executions**
without narrowing a failure to targeted tests and investigating it.

### When the full E2E suite fails

* Do **not** immediately rerun the entire suite.
* Narrow to the failing test/spec (`--last-failed` or the printed test id).
* Reproduce it individually (`--jobs 1`).
* Diagnose and fix it.
* Rerun the failing test until it passes reliably.
* Run its feature group.
* Run smoke if relevant.
* Only then rerun the full gate **once**.

### `--affected` defaults

Compares the working tree (+ relevant untracked production/E2E paths) to
`HEAD`, or to `--base <ref>` when given. Prints every changed path, the rule
that matched, and the selected features. Unmapped production files fall back
to **smoke** (not the full suite). Shared core (`app.js`, `server.py`,
`db_manager.py`, …) selects smoke + shell/tabs/offline/sync. Docs/unit-only
paths select nothing. Add new production areas by editing `AFFECTED_RULES`
and `FEATURES` in `tests/e2e/policy.py`. Classify a new E2E module by adding
its module/class prefix to the right feature's `selectors`.

`--affected` fails closed: when Git change discovery itself fails (invalid
`--base` ref, unusable checkout, missing/failing `git`, or untracked-file
discovery failure) the runner exits nonzero with a diagnostic instead of
reporting zero affected tests. A genuine empty diff remains a successful no-op.
`--base` must name a single revision: a leading `-` is rejected, the base is
verified to resolve to one commit (a range like `a..b` compares commit to
commit and would drop the working tree), and the diff terminates revision
parsing with `--`. An option-like, range or path-like base therefore fails
closed instead of quietly answering a different question.

Benchmark/profile runs never train history. `--profile` / `--no-seed-cache`
**and** their environment equivalents (`PRKS_E2E_PROFILE`,
`PRKS_E2E_SEED_CACHE=0`) suppress `.tests/e2e-timings.json` and last-failed
persistence; `policy.benchmark_modes()` is the single decision.

Optional stress: `tests/e2e/stress_cache_offline.py` — never part of normal
iteration or the full gate.

## E2E test workflow

`tests/e2e/run.py` is the only entry point for the real-Chromium suite.

```
python tests/e2e/run.py --smoke --jobs 2
python tests/e2e/run.py --feature graph --jobs 2 --no-pointer-capture
python tests/e2e/run.py --affected
python tests/e2e/run.py --affected --base origin/master
python tests/e2e/run.py --last-failed
python tests/e2e/run.py --dev --feature tabs
python tests/e2e/run.py --jobs 4                # full regression gate
python tests/e2e/run.py --jobs 1 tests.e2e.test_playlists_offline.OfflinePlaylistTests.test_x
python tests/e2e/run.py --jobs 4 --no-pointer-capture tests.e2e.test_playlists_offline
scripts/e2e smoke
scripts/e2e feature graph --jobs 2
scripts/e2e full --jobs 4
```

`--jobs` wins over `PRKS_E2E_JOBS`; the default is 1 so debugging is never
accidentally parallel. There is no Playwright retry loop in this runner;
failed tests stay failed.

The E2E performance milestone is closed. Console suppression ignores only
`blob:` resource failures containing `ERR_FILE_NOT_FOUND`; other blob errors
remain failures. Do not broaden that teardown exception.

### When to run what

**During normal implementation iterations, do not run browser E2E tests at all
-- neither the full suite nor an entire module** -- unless the behavior being
debugged can only be verified at browser level. Prefer unit tests, Node
selftests, static contracts, API tests and other fast targeted tests. Almost
every sync, projection, validator and coherence question is answerable that
way, in seconds rather than minutes.

**After a complete user-visible vertical slice, or once the milestone's
implementation is finished, run the relevant targeted E2E feature/module once.**
That is where a browser earns its cost: real navigation, real IndexedDB, real
service worker. Always through the sharding runner, never raw unittest:

```
python tests/e2e/run.py --jobs 4 --no-pointer-capture --feature sync
python tests/e2e/run.py --jobs 4 --no-pointer-capture tests.e2e.test_x
```

`python -m unittest tests.e2e.<module>` runs SERIALLY -- a browser launch and a
PRKS server per test, one at a time -- which turns a two-minute module into
half an hour. It is for isolating one already-identified failure, not for
feature completion. `--no-pointer-capture` skips the once-per-run pointer
checks, which belong to the full gate rather than to a module.

**After all milestone work and targeted verification are complete, run the full
parallel E2E suite once:**

```
python run_tests.py
python run_tests.py --e2e
# equivalent: scripts/e2e full   or   python tests/e2e/run.py --jobs 4
# optional shell wrapper still fine: timeout 1200 python tests/e2e/run.py --jobs 4
```

The full run already contains every module, so do not run a module separately
immediately before it.

**Budget the full run and treat an overrun as a bug, not as patience.**

| | |
| --- | --- |
| Expected `--jobs 4` runtime | ~7-10 min |
| Investigate | > 15 min |
| Hard outer timeout | ~20 min (runner `PRKS_E2E_FULL_TIMEOUT` default 1200; `timeout 1200` optional) |

A generous outer timeout does not make a run succeed; it hides a hang. If the
suite overruns, do NOT wait it out and do NOT restart it: read the log, see
which shards reported and which did not, check for orphaned Chromium or PRKS
processes, and reproduce the suspect test alone. A worker that never reports
while its peers finish in eight minutes is a stuck test or a stuck teardown,
and the full suite is the worst possible instrument for finding it. Raise the
limit only when the suite genuinely grows, and re-measure rather than guess.

**If an E2E test fails, stop running the full suite.** Reproduce that one test:

```
python tests/e2e/run.py --jobs 1 tests.e2e.test_x.Class.test_name
python tests/e2e/run.py --last-failed
```

Fix it, re-run its feature group, and only then spend the full parallel gate once
more. Never rerun the full suite to find out whether a fix worked.

A full run costs several minutes of wall clock and saturates the machine; a
single module still costs a browser launch and a PRKS server per test. The
optimization is faster execution, not less verification: the full suite is
still mandatory before declaring a milestone complete, and a failure it reports
is never dismissed without being reproduced and classified.

Worker count guidance (do not hard-code suite size here — discovery grows):
on a 12-core development machine, `--jobs 4` was the best measured full-gate
setting in an earlier benchmark pass (serial much slower; 2–3 better than
serial; 4 recommended). Re-measure when the suite or machine changes rather
than trusting a fixed case count. Discover current modules/cases via
`tests/e2e/run.py` / unittest discovery with `PRKS_E2E=1`. **4 remains the
recommended default gate** until a fresh benchmark says otherwise. More
workers are not automatically better -- Chromium plus a PRKS server per test
is memory- and CPU-hungry, and per-worker overhead was already substantial at
4 -- so re-benchmark rather than raising it on a different machine. Drop to
`--jobs 3` if a machine shows contention-driven flakiness.

Debugging. `--jobs 1` is the mode for reproducing a flake, reading one clean
traceback, or checking that the suite still passes serially. It does not need to
run after every milestone once the parallel suite is reliable. A test that fails
only under `--jobs > 1` is a real defect — investigate port collisions, shared
filesystem paths, hardcoded ports, singleton temp files, CPU-sensitive timing
assumptions, or unowned browser/server processes. Never paper over it with a
retry: there is deliberately no blanket retry mechanism, because retrying
conceals races.

Sharding. Workers receive individual test IDs, not whole modules, balanced
longest-processing-time-first from `.tests/e2e-timings.json` (gitignored runner
metadata, written after each run). That history is an optimisation hint and
never required state: absent, corrupt, or full of renamed tests, the runner
still works and unknown tests take a default estimate. Each shard keeps one
module's tests contiguous, because these modules launch Chromium in
`setUpModule` and unittest re-runs a module fixture whenever the module changes.
The scheduling and aggregation logic lives in `tests/e2e/sharding.py` as pure
functions covered by `tests/test_e2e_sharding.py` — no Chromium needed.

The parent fails the gate if any worker fails, errors, crashes, or exits without
writing a result document; a vanished worker is never read as a pass. Pointer
capture (`tests/browser/pointer_capture.py`) runs exactly once, in the parent,
after every shard has passed — never once per worker.

## E2E isolation invariant

Parallel execution relies on each E2E test keeping independent PRKS
storage/database and browser-context state: a fresh `TemporaryDirectory`, a
fresh database, a fresh port, and a fresh browser context. Do not introduce
fixed ports, shared writable temp files, shared test databases, a shared browser
context, or cross-worker mutable globals.

Ports come from `find_free_port()`, which binds and closes before the server
subprocess binds for real. Each worker gets a disjoint port window below the
Linux ephemeral range via `PRKS_E2E_PORT_BASE`/`PRKS_E2E_PORT_SPAN`, and
`AppServer.start()` retries a bounded number of times on a genuine bind
conflict only — other startup failures are reported as themselves, never
disguised as port conflicts.

Server reuse across tests (one persistent PRKS process, database rollback
between tests, a shared IndexedDB or service-worker profile) is deliberately
**not** implemented. It trades a large correctness risk for time that
parallelism already recovered.

## Never return a live object from `page.evaluate`

`page.evaluate` serializes whatever its expression evaluates to. Handing back a
live application object -- a cytoscape instance, a store, anything with a deep
internal graph -- makes Playwright walk all of it across the protocol. Measured
on `prksGetResearchGraphDebug().cy`: **7-10 seconds per call**, enough
allocation to crash the renderer (`Target crashed`) under `--jobs 4`, and the
value arrives as `None` regardless.

Two defects had shipped from this, in the same file:

- `page.evaluate('window.__x = prksGetResearchGraphDebug().cy')` is an
  assignment EXPRESSION, so it returns the instance. Use a block body:
  `page.evaluate('() => { window.__x = ...; }')`.
- `assertIsNone(page.evaluate('prksGetResearchGraphDebug().cy'))` passed whether
  or not a graph was mounted, because a live instance serializes to `None` too.
  That assertion could not fail. Ask the page for a **boolean**:
  `page.evaluate('() => !!prksGetResearchGraphDebug().cy')`.

Return scalars, booleans, ids, counts and small plain data. Compare identity
inside the page (`a === b`), never by shipping both objects out.

## Scope a "nothing is showing" assertion to where it must not show

A page-wide `page.locator('.work-detail').count() == 0` is not the claim
"the Folders tab shows no Work detail". A tab whose PDF resource has mounted is
**warm-parked** when you switch away (`prksWarmParkTabContext`): its DOM is
moved into the hidden `#prks-tab-warm-parking` host and deliberately kept alive
so returning to it is instant. The node still matches a page-wide selector.

Whether it survives depends on whether the PDF finished mounting before the
switch, so the page-wide count passed when the test ran alone and failed under
`--jobs 4` -- which looks exactly like a render race and is not one. Measuring
it settled the question: the two views stayed co-mounted for the whole 2.5 s
sample window with no transition, so nothing was waiting to finish.

Scope to the visible surface (`.prks-tile--main .work-detail`), assert the
scope itself is non-empty so it cannot pass vacuously, and where a survivor is
legitimate, pin WHERE it survives:

    self.assertEqual(page.locator(".prks-tile--main").count(), 1)
    self.assertEqual(page.locator(".prks-tile--main .work-detail").count(), 0)
    self.assertEqual(page.locator(".work-detail").count(),
                     page.locator("#prks-tab-warm-parking .work-detail").count())

Cold-parked routes (anything without a PDF resource) are destroyed on switch,
so their page-wide counts are safe -- that asymmetry is why only the Work
detail flaked.

## No arbitrary sleeps in E2E

E2E tests prefer observable application/browser state over fixed delays. Wait on
the request, the API response, the domain generation, the IndexedDB row, the
route, the DOM state, the right-panel owner, or the button state — not
`page.wait_for_timeout(500)` after a mutation.

A fixed delay is appropriate only when elapsed time itself is under test:
debounce, backoff, probe intervals, and the settle windows that prove an action
issues *no* request. Those must stay — absence of behavior cannot be observed
without a window. Do not "optimize" them away, and do not lower Playwright's
default timeouts for speed: a shorter timeout does not make a passing test
faster, only a slow one fail sooner.

One measured exception, which looks like an arbitrary sleep and is not: the
250 ms settle delay in `_wait_entity_cached()` / `_wait_list_cached()`
(`tests/e2e/test_offline.py`). An IndexedDB row that a separate read
transaction can already observe is still lost when the page is torn down (go
offline + reload) immediately afterwards — `getEntity` returns null after the
reload and the route renders "not available offline". `offline-store.js`
resolving `readwrite` from `tx.oncomplete` does **not** make that safe.
Measured on the Concept-detail transition with
`tests/e2e/stress_cache_offline.py`: 20/20 iterations pass with the delay,
6–9/20 fail without it. There is no page-observable "durable across teardown"
signal to wait on instead, so the delay stays. Re-run that script before
believing any claim to the contrary.

## `wait_for_function` cannot express an async condition

`page.wait_for_function` **awaits a returned Promise and then tests the settled
promise for truthiness** — and a promise is always truthy. So this gate

```python
page.wait_for_function("() => prksSync.store.listOperations().then(r => r.length === 0)")
```

passes on its first poll whether the queue is empty or not: it waits one round
trip and reports success. It does not fail loudly; it silently stops being a
gate, which is worse than either passing or failing. Verified directly:
a predicate returning `Promise.resolve(false)` passes in 0.01 s, and one
resolving to `false` after 3 s passes in 3.03 s — the delay comes from awaiting
the promise, not from the answer.

Almost every durable-queue, IndexedDB and offline-store condition in this suite
is asynchronous, so **use `wait_for_async(page, expression, arg=..., timeout=...)`
from `tests/e2e/harness.py` for any predicate whose body contains `.then(`,
`async` or `await`.** It polls with `page.evaluate`, which does return the
resolved value, and raises an `AssertionError` naming the last value it saw.
`wait_for_function` remains correct — and preferred — for a synchronous
predicate (DOM state, a global, `location.hash`).

This was found when a Title save appeared to reach an empty queue instantly
while its operation was in fact still `syncing`. Forty gates across seven
modules were vacuous; converting them exposed two real defects that had been
passing.

## UX Interaction Tour

`tests/ux_tour/` is a separate, deliberately human-oriented, artifact-producing
review suite (video, Playwright trace, checkpoint screenshots, an action log,
and a coverage matrix). It complements the fast unit/browser-selftest/E2E
suites; it is not a replacement, and finding something there is not a reason
to delete or weaken a precise E2E regression test that already covers the
same operation.

UX Tour actions should use real user-visible controls when one exists
(buttons, menus, links, inputs, keyboard shortcuts, drag handles, the command
palette, modals) — not `window.prksNavigate(...)`,
`window.prksWorkspaceSplitLeaf(...)`, `selectGraphNode(...)`, or similar
internal calls. Internal JavaScript calls may inspect state for assertions or
instrumentation (workspace snapshot, TabContext ids, PDF runtime identity,
request counters) but should not substitute for the interaction being tested.
The one narrow exception is Cytoscape graph edges: they are canvas pixels with
no separate DOM control, so selecting one goes through the same
`window.selectGraphEdge(id)` hook the existing E2E suite already uses for that
same reason.

Do not add the UX Tour to normal unit/E2E modes (`python run_tests.py`,
`--e2e`, `--all`). It is explicit opt-in only, via `python run_tests.py
--ux-tour`. `tests/ux_tour/test_tours.py` stays out of ordinary discovery via
the same `load_tests()` gate `tests/e2e/test_app.py` uses for `PRKS_E2E` —
here keyed on `PRKS_UX_TOUR`, set only by `tests/ux_tour/run.py`.

Every scenario opens its own fresh `tests.e2e.harness.AppServer` (fresh
`TemporaryDirectory`, fresh database) and a fresh browser context — never a
long-lived one shared across tours. Recorded artifacts live under
`artifacts/ux-tour/<run-id>/`, gitignored and never committed; a passing
scenario's heavy artifacts (video/trace/screenshots) are deleted after its
manifest entry is recorded unless `PRKS_UX_RECORD=1` was set.
