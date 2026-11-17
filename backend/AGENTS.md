# PRKS backend agent instructions

These rules apply to backend work in addition to the repository-root `AGENTS.md`. The text below is scoped from the former root policy so backend workers receive the invariants they need without unrelated frontend/E2E context.

## Current persistence vs target architecture

These rules describe the backend **implemented today**. PostgreSQL is the only Library store (PG-C2 #641: the SQLite runtime, its schema and migrations are deleted; records of the retired SQLite tests are in `tests/sqlite_retirement/`). The current storage tree and sync protocol remain correctness constraints, but they are not permanent architecture mandates. Roadmap #310 accepts PostgreSQL as the expected long-term canonical relational store and a server architecture capable of authenticated multi-user/multi-library operation; #311 accepts storage-backend abstraction with a first-class configurable local data root. Preserve current behavior unless the task is an approved migration slice, while avoiding new domain boundaries that unnecessarily encode single-owner, absolute-path, or process-local assumptions. Do not reintroduce a SQLite Library path.

## Cross-domain contracts

Nested `AGENTS.md` applies only within this directory tree. Several backend modules still share load-bearing invariants whose full text currently lives under `frontend/AGENTS.md` (and the docs it routes to). **Do not omit those contracts on a backend-only change** — Cursor will not inject `frontend/AGENTS.md` automatically. Load the smallest applicable route below instead of copying the large sections here.

### Offline / sync / local-first

Before changing `*_sync.py`, `sync_protocol.py`, durable-operation handlers, conflict/revision semantics, acknowledgement/ledger behavior, or other offline/local-first backend surfaces:

1. read `docs/agent-rules/offline-pwa.md` as the rule router;
2. use `docs/agent-context/sync-map.md` to load `offline-foundations.md` plus only the relevant domain leaf files and semantic sections;
3. also follow the Offline / PWA section in `frontend/AGENTS.md` for the shared durable-intent / disposable-cache / online↔offline convergence invariants that apply across HTTP and sync.

Only shared protocol/coordinator changes that genuinely span many durable families should load many leaves; a single-family backend change should not pay the context cost of unrelated families.

### Research network / Research Graph

Before changing `domain/research/`, `api/concepts.py`, `api/positions.py`, `api/arguments.py`, `api/research_notes.py`, `research_graph.py`, `research_markup.py`, or related research API/controllers, read the **Research network** and **Research Graph** sections in `frontend/AGENTS.md` (canonical DB/index/deletion/privacy invariants). Do not treat the derived research index or graph projection as authority for destructive mutation.

### Saved Views

Before changing Saved Views HTTP/API or search-definition persistence (`api/saved_views.py`, `api/search.py` and related DB/search paths), read the **Saved Views** section in `frontend/AGENTS.md` (definitions vs membership, reuse of normal search, privacy of names/definitions, no executable arbitrary rules).

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

Do not add schema indexes, caching, threading or database tuning solely because an
endpoint appears theoretically expensive; use measured diagnostics first.

Performance instrumentation must never be required for application correctness.

Catalogue/list SQL must not compute per-row counts with a correlated scalar
subquery (`(SELECT COUNT(*) FROM child c WHERE c.parent_id = p.id)`); aggregate
once with `GROUP BY` in a CTE or joined derived table. `scripts/check_correlated_catalog_counts.py`
(#192, Fast Static Analysis) fails new instances; existing debt is listed with
its owning issue in `scripts/correlated_catalog_counts_allowlist.json` and may
only shrink. See the script docstring for codes and the reviewed escape hatch.

## Storage root (storage-architecture Phase A)

`docs/storage-architecture.md` is the design authority. Root selection lives only
in `storage/resolver.py`; never read `PRKS_STORAGE` or the bootstrap file
elsewhere, and never let testing mode consult the bootstrap file or a platform
default. Every bootstrap-config write goes through `BootstrapConfigStore`
(config lock, re-read, expected-state check, atomic replace). Only the holder of
a root's `root.lock` lease writes its `prks-root.json`; lease diagnostics in the
marker are never lock authority. The one exception is the PostgreSQL database
pairing (`instance_id`, `start_generation`, `start_token`;
`docs/postgresql-architecture.md` §11.1): only `root_marker.claim_root` (under
the exclusive `root.claim` lock) and `root_marker.bump_start_generation` change
those fields, under the **shared** `root.lock`, and `write_marker` refuses to
change them. The marker, the lease file, `root.claim` and the bootstrap
config are operational and never backup payload. The PostgreSQL Library
stores managed PDFs and portraits as immutable objects through
`storage/objects.py` (`StorageBackend`, Phase B); new file-backed state goes
through it too, never through raw paths under the root.

## Backup and restore

Backup/restore code must never operate on production storage during tests.

Library backup and restore are format 2 on PostgreSQL (`backend/domain/maintenance/`,
`docs/postgresql-architecture.md` §11.3, §11.4; see the root `AGENTS.md`
L-B-1 entry). Do not add a library-scoped table without classifying it in
`domain/maintenance/tables.py` (`tests/pg/test_backup_tables.py` fails an
unclassified one), and do not add a persistent component under the root
without classifying it in the storage inventory (`docs/storage-architecture.md`).

`thumbs/` and the `derived` schema are derived and are not backup state. A
committed restore enqueues `derived.rebuild` for the derived rows (search,
extracted text, research index); thumbnails are cache files regenerated from
the PDFs.

Restore validation and staging (R0, R1) complete before anything another
session can see changes. The replace is one SERIALIZABLE transaction (R4):
a run that stops before it leaves the previous Library; only R4 commits the
restored one. Objects the old catalogue named and the restored one does not
get a `hold` claim and are never deleted automatically.

Backup archives and uploaded restore archives written under the root follow
the `backend/fs_durability.py` convention (`domain/maintenance/backup.py`,
`upload.py`): the file is flushed before it is renamed or used, and the
directory is synced after a rename.

Do not use `ZipFile.extractall()` on unvalidated backup input.

Do not log archive paths, PDF names, manifest contents, or raw restore errors.

## PDF text index

Extracted PDF text is derived, never canonical. It goes only into the
`derived` schema (`derived.asset_text`, keyed by Asset and `content_sha256`),
never into a `prks` table; a command that gives a managed-file Asset new bytes
calls `backend.domain.search.text.content_changed` in its own transaction, and
the `text.extract` job writes the row (`docs/postgresql-architecture.md` §8.3,
§16 decision 5).

Do not add the `derived` schema to backups.

Do not treat an extraction exception as a successful empty PDF.

Do not preserve stale searchable text after the canonical PDF changes.

Derived rows may be rebuilt from canonical state (`derived.rebuild`);
`prks` tables never receive that treatment.

## Post-delete cleanup recovery

Deleting a Work, replacing its PDF, or anything else that stops naming an
object commits the catalogue change first and removes bytes afterwards.
`backend/domain/objects/lifecycle.py` owns the lifecycle
(`docs/postgresql-architecture.md` §7.4, §11.2, §11.3); read its module
docstring before changing it. The rules that must not be weakened:

- **A claim is recorded in the transaction that stops naming a key**
  (`release`, in the caller's Library command), and by the orphan sweep for an
  object nothing ever adopted. Recording never deletes. Claims store keys,
  never absolute paths.
- **Only `settle` deletes**, after re-reading every reference to the key
  (from any Library: one data root serves them all) under the key's
  `pg_advisory_xact_lock`, and it deletes the claim in that same transaction
  once nothing is owed. An adopter takes the same lock, so either order is
  safe.
- **The reference check is three-valued.** `None` ("not known") deletes
  nothing and settles nothing.
- **A `hold` claim** (restore's replaced objects) is never settled
  automatically, and a Library's claims are settled only while its objects
  lock is free (a running backup holds it).
- Retries and the orphan sweep are bounded, lease-fenced job passes
  (`objects.cleanup`, `objects.sweep` in `backend/domain/objects/jobs.py`),
  never an unbounded scan at startup.

Derived state (search rows, research index rows, thumbnails) is disposable:
rebuild it from canonical state instead of keeping retry records for it.

## Database schema changes

Never ALTER/CREATE/DROP schema objects from feature/request code, and never
swallow schema-runner failures. The SQLite schema, its migrations and the
schema-change gate were deleted in PG-C2 (#641).

PostgreSQL schema (#624, `docs/postgresql-architecture.md` §4.4):
`backend/db/schema/NNN_name.sql` are pre-1.0 baseline
files applied in order by `backend/db/migrate.py`, which records each file's
SHA-256 and refuses a database whose applied file has since changed. Each lane
edits only the files it owns (§15.2), schema-qualifies every name (the runner
applies files with an empty `search_path`), and regenerates
`backend/db/schema/snapshot.json` with `scripts/check_pg_schema.py --write` in
the same PR. Never index, default or generate a column from
`prks.search_norm` (§8.3; the snapshot check fails it), and give any SQL function
that an index, default or generated column calls a `BEGIN ATOMIC` body: the
check refuses stored values computed through string-bodied functions, whose
calls the catalog does not record. The runner never reads
or writes the data root; the root claim and start generation belong to L-ID-1.

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
