# PRKS backend agent instructions

These rules apply to backend work in addition to the repository-root `AGENTS.md`. The text below is scoped from the former root policy so backend workers receive the invariants they need without unrelated frontend/E2E context.

## Cross-domain contracts

Nested `AGENTS.md` applies only within this directory tree. Several backend modules still share load-bearing invariants whose full text currently lives under `frontend/AGENTS.md` (and the docs it routes to). **Do not omit those contracts on a backend-only change** — Cursor will not inject `frontend/AGENTS.md` automatically. Load the smallest applicable route below instead of copying the large sections here.

### Offline / sync / local-first

Before changing `*_sync.py`, `sync_protocol.py`, durable-operation handlers, conflict/revision semantics, acknowledgement/ledger behavior, or other offline/local-first backend surfaces:

1. read `docs/agent-context/sync-map.md` for the smallest relevant map entry;
2. **read `docs/agent-rules/offline-pwa.md` completely**;
3. also follow the Offline / PWA section in `frontend/AGENTS.md` for the shared durable-intent / disposable-cache / online↔offline convergence invariants that apply across HTTP and sync.

### Research network / Research Graph

Before changing `research_network.py`, `research_index.py`, `research_graph.py`, `research_markup.py`, or related research API/controllers, read the **Research network** and **Research Graph** sections in `frontend/AGENTS.md` (canonical DB/index/deletion/privacy invariants). Do not treat the derived research index or graph projection as authority for destructive mutation.

### Saved Views

Before changing Saved Views HTTP/API or search-definition persistence (`api/saved_views.py` and related DB/search paths), read the **Saved Views** section in `frontend/AGENTS.md` (definitions vs membership, reuse of normal search, privacy of names/definitions, no executable arbitrary rules).

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

Work identity (#60, schema 17, `docs/work-identity-model.md`): `works` is
still the only authority for every field. `manifestations`/`assets` are a
trigger-maintained projection of it (`legacy_work_*_mirror` views, one
direction only); never write their mirrored columns, and never add readers of
them outside the slice that moves authority. `validate_current_schema`
compares every Slice A object by definition, so changing one needs a new
migration, not an edit of `_V17_WORK_IDENTITY_SQL`.
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
