# PRKS

PRKS is a local research library. Python 3.12 stdlib HTTP, SQLite, vanilla JS. Full run, Docker, and config live in README.md.

## Commands

- Tests: `python run_tests.py`
- App, default for agents: `python prks_app.py --testing`
- Real app or Compose: only with run-real authorization from the user

If the user says only "run the app", your first application execution path is `python prks_app.py --testing`. Do not run unflagged `python prks_app.py`. Do not run Docker Compose. Do not target `./data`.

## Storage

Default. Do not write `data/` or a live `PRKS_STORAGE` tree. `python run_tests.py` already sets `PRKS_TESTING=1` and `PRKS_STORAGE` to `data_testing/`.

Run-real. An instruction to run the real app or Compose authorizes normal application writes only. Creating or updating records the way the app does.

Destructive. Deleting PDFs, deleting, resetting, or replacing the production DB, or clearing production storage needs a separate explicit confirmation that names that action. Run-real is not that confirmation.

## Layout

- `prks_app.py` CLI
- `backend/server.py` HTTP
- `backend/db_manager.py` SQLite
- `frontend/` UI
- `tests/` unittest
