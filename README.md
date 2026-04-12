# PRKS — Personal Research Knowledge System

PRKS is a self-hosted web application for organizing research materials: PDFs, Markdown notes, and online video references. It stores everything in a SQLite database and on-disk files on your machine—no separate database server. The UI supports folders, tags, reading progress, people and bibliographic metadata, PDF annotations, playlists for videos, and a knowledge graph of how items relate.

## Requirements

- **Python 3.12+**
- **PyMuPDF** 1.24.10 

The HTTP server and SQLite access use the Python standard library.

## Quick start (local)

From the repository root:

```bash
pip install -r requirements.txt
python prks_app.py
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080) in a browser. The default listen port is **8080** (`backend/server.py`).

Optional port:

```bash
python prks_app.py --port 9000
```

### Testing mode (Creates seperate testing database)

```bash
python prks_app.py --testing
```

This sets `PRKS_TESTING=1`, uses port **8070** by default (unless you pass `--port`), and uses `data_testing/` for the database and PDFs so normal `./data` is untouched. If `PRKS_STORAGE` points at `/data` (or under it), the server refuses to start in testing mode to avoid mixing container storage with tests.

## Docker

Build:

Use `./docker-build.sh`, which builds `prks:latest` and prunes dangling images (from previous builds).

And run with Compose (from the repo root):

```bash
docker compose up -d
```

This maps **8080:8080**, sets `PRKS_STORAGE=/data`, mounts **`./data` on the host to `/data` in the container**, and runs the process as **`${UID:-1000}:${GID:-1000}`** so files on the bind mount match your user. The entrypoint creates `/data/pdfs` if needed and runs `python /app/prks_app.py`.

## Configuration and data layout

| Variable | Purpose |
| -------- | ------- |
| `PRKS_STORAGE` | If set, root directory for persistent data. Database: `$PRKS_STORAGE/prks_data.db`. PDFs: `$PRKS_STORAGE/pdfs/`. Thumbnails: `$PRKS_STORAGE/thumbs/`. |
| `PRKS_TESTING` | When truthy (`1`, `true`, `yes`), uses testing paths and stricter checks (see testing mode above). |

If `PRKS_STORAGE` is **unset**, non-testing runs use the project’s **`data/`** directory: `data/prks_data.db`, `data/pdfs/`, and `data/thumbs/`.

Backup your database by copying `/data` folder.

## Development and tests

```bash
python run_tests.py
```

This discovers tests under `tests/`, sets `PRKS_TESTING=1` and `PRKS_STORAGE` to the repo’s `data_testing/` directory so tests do not use `./data` or container `/data`.

## Project layout

| Path | Role |
| ---- | ---- |
| `prks_app.py` | CLI entry: parses `--testing`, `--port`, starts the server. |
| `backend/server.py` | HTTP handler: static frontend, REST-style `/api/...` routes. |
| `backend/db_manager.py` | SQLite access and business logic. |
| `backend/db_schema.sql` | Schema and FTS triggers. |
| `frontend/` | Static SPA (HTML, CSS, JS), PWA assets. |
| `data/` | Default production database and files (gitignored as appropriate). |
| `data_testing/` | Test fixtures and isolated DB/PDFs for automated tests. |
| `tests/` | `unittest` modules. |

## Security note

PRKS is aimed at **local or trusted network** use. There is **no built-in authentication** in the application. If you expose it beyond localhost, put it behind a reverse proxy or custom VPN and enforce access control yourself.
