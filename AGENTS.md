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

