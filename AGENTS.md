# PRKS

PRKS is a local research library. Python 3.12 stdlib HTTP, SQLite, vanilla JS. Full run, Docker, and config live in README.md.

## Commands

- Tests: `python run_tests.py`
- App, default for agents: `python prks_app.py --testing`
- Real app or Compose: only with run-real authorization from the user

If the user says only "run the app", your first application execution path is `python prks_app.py --testing`. Do not run unflagged `python prks_app.py`. Do not run Docker Compose. Do not target `./data`.

## Storage

Default. Do not write `data/` or a live `PRKS_STORAGE` tree. `python run_tests.py` assigns `PRKS_TESTING=1` and `PRKS_STORAGE` to `data_testing/`, and clears `PRKS_FOR_PROCESSING_DIR` and `PRKS_LOG_FILE`. Tests never target repo `data/` or `/data`.

Run-real. An instruction to run the real app or Compose authorizes normal application writes only. Creating or updating records the way the app does.

Destructive. Deleting PDFs, deleting, resetting, or replacing the production DB, or clearing production storage needs a separate explicit confirmation that names that action. Run-real is not that confirmation.

## Layout

- `prks_app.py` CLI (the only process entry)
- `backend/server.py` HTTP adapter: parsing, dispatch, status/headers, JSON, ETags, static files
- `backend/storage/config.py` frozen storage snapshot and env parser
- `backend/storage/paths.py` storage-path derivation and testing-mode containment
- `backend/db_manager.py` SQLite
- `backend/db_migrations.py` ordered schema migrations
- `backend/backup_restore.py` verified backup/restore
- `backend/performance.py` in-memory performance diagnostics
- `frontend/` UI
- `tests/` unittest

New substantial behavior, in order:

1. Extend an existing focused module when the behavior belongs there (`text_index` for indexing, `pdf_linearize` for linearization, `storage.paths` only for storage-path resolution).
2. Otherwise create a focused feature or domain module.
3. Use `backend/services/` when an operation coordinates multiple concerns such as DB + filesystem + PDF + indexing.
4. Do not create `routes/`, `services/`, or other layers ahead of real behavior.

`server.py` keeps HTTP concerns. Substantial SQL, filesystem mutation, PDF processing, indexing, imports, and domain workflows live outside the handler. Do not split `server.py` or introduce a framework.

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

`thumbs/` and `prks_text_index.db` (including WAL/SHM) are derived. They are not
required backup state and must be rebuilt after restore.

Restore validation must complete before live canonical state is modified.

Crash before committed restores the previous library. Only committed keeps
the restored library.

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

