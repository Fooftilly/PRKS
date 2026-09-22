# PRKS — Personal Research Knowledge System

PRKS is a self-hosted, local-first research library for organizing PDFs, Markdown research notes, online video references, bibliographic metadata, people, tags, reading progress, PDF annotations, playlists, and structured research relationships.

It uses Python, SQLite, and on-disk files on your machine—no separate database server is required.

![Folder of public-domain books](docs/screenshots/folders.png)

![Origin of Species open in the PDF reader](docs/screenshots/work.png)

![People in the library](docs/screenshots/people.png)

## Highlights

- Organize research with Works, Folders, Tags, People/Roles, Playlists, progress, and Saved Views.
- Read and annotate managed PDFs inside PRKS.
- Keep Research Notes alongside source material and connect them to Concepts, Positions, Arguments/Stances, and the Research Graph.
- Work across in-app tabs and split panes without opening a collection of browser tabs.
- Use progressively local-first/offline-capable workflows with durable browser-side operations for supported domains.
- Back up and restore the canonical PRKS library through the application.
- Run locally with Python/SQLite or with Docker Compose.

## Requirements

- **Python 3.12+**
- Exact runtime package pins from `requirements.txt`

Prefer a project-local virtual environment. On PEP 668 “externally managed” systems, do not use `sudo pip` or `--break-system-packages`.

## Quick start

Prefer a project-local virtual environment (required on PEP 668 “externally managed” systems — never use `sudo pip` or `--break-system-packages`):

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python prks_app.py
```

`prks_app.py` validates the Python environment (minimum version + exact `requirements.txt` pins via `importlib.metadata`) **before** DB recovery, migrations, storage binding, or server startup. A mismatch exits non-zero with platform-aware remediation. It never auto-runs `pip` and never contacts PyPI.

The process listens on **127.0.0.1:8080** only. Open [http://127.0.0.1:8080](http://127.0.0.1:8080) in a browser. No extra firewall or network setup is required for this case.

Optional port (still loopback):

```bash
python prks_app.py --port 9000
```

To listen on every local interface (LAN or VPN), pass an explicit host:

```bash
python prks_app.py --host 0.0.0.0
```

`--host localhost` also works and binds that name. The default is the literal address `127.0.0.1`, not `localhost`.

PRKS has **no application-level authentication**. Do not expose it to an untrusted network. See [Security and Operations](docs/wiki/Security-and-Operations.md) before changing the bind/publish address.

### Testing mode

```bash
python prks_app.py --testing
```

This sets `PRKS_TESTING=1` and uses port **8070** by default (unless you pass `--port`). With `PRKS_STORAGE` unset it defaults to `data_testing/` so repo `data/` is untouched. You may set `PRKS_STORAGE` to an explicit safe testing root. Testing mode refuses `/data` and the repository `data/` directory (and descendants), including via symlinks. `prks_app.py` is the only process entry.

## Docker

Build with `./docker-build.sh`, which builds `prks:latest` and prunes dangling images from previous builds. Then run Compose from the repo root:

```bash
docker compose up -d
```

The container process binds **0.0.0.0:8080** so Docker port forwarding can reach it. Compose then publishes that port on the **host loopback** only (`127.0.0.1:8080:8080`). Container `0.0.0.0` is not the same as exposing PRKS on every host interface.

Open [http://127.0.0.1:8080](http://127.0.0.1:8080) on the machine that runs Compose. This also sets `PRKS_STORAGE=/data`, mounts **`./data` on the host to `/data` in the container**, and runs the process as **`${UID:-1000}:${GID:-1000}`** so files on the bind mount match your user. The entrypoint creates `/data/pdfs` if needed and runs `python /app/prks_app.py --host 0.0.0.0`.

To publish the host port on every interface (LAN access):

```bash
PRKS_PUBLISH_HOST=0.0.0.0 docker compose up -d
```

Use that override only on a network you already trust, or behind an access layer you control.

## Configuration and data layout

| Variable | Purpose |
| -------- | ------- |
| `PRKS_STORAGE` | If set, root directory for persistent data. Database: `$PRKS_STORAGE/prks_data.db`. PDFs: `$PRKS_STORAGE/pdfs/`. Thumbnails: `$PRKS_STORAGE/thumbs/`. |
| `PRKS_TESTING` | When truthy (`1`, `true`, `yes`), uses testing paths and stricter checks (see [Testing mode](#testing-mode) above). |
| `PRKS_THUMB_LOSSLESS` | When truthy, PDF card thumbnails use lossless WebP/PNG cache encoding (debugging). Default is card-optimized lossy WebP; cache filenames use rev `_v2`. |
| `PRKS_LOG_LEVEL` | Stderr log level. Default `INFO`. Changes volume, not what kinds of data may be logged. |
| `PRKS_LOG_FILE_LEVEL` | Persistent file log level. Default `ERROR`. |
| `PRKS_LOG_RETENTION_DAYS` | Rotated persistent log copies to keep. Default `7`. |
| `PRKS_LOG_FILE` | Override path for the rotating error log. Default `$PRKS_STORAGE/prks-errors.log`. |
| `PRKS_PERF_SLOW_MS` | API request duration in milliseconds counted as slow. Default `250`. Clamped to 10–60000. Does not timeout requests. |
| `PRKS_PERF_LOG_SLOW` | When truthy (`1`, `true`, `yes`, `on`), emit a privacy-safe `slow_request` INFO log for slow API requests. Default off. |
| `PRKS_BACKUP_MAX_UPLOAD_BYTES` | Maximum size of an uploaded `.prks-backup` restore archive. Default 64 GiB. A valid `Content-Length` is required. |

If `PRKS_STORAGE` is **unset**, non-testing runs use the project’s **`data/`** directory: `data/prks_data.db`, `data/pdfs/`, `data/thumbs/`, and person portrait cache `data/people/` (lossy WebP, max 512px edge, keyed by person id + `image_url` hash).

Person profile images (`GET /api/persons/{id}/profile-image`) are optional. `image_url` must be a direct public HTTP/HTTPS URL (HTTPS preferred) that itself returns HTTP 200. PRKS does not follow redirects, and private/local/link-local targets are refused. Only static JPEG/PNG/WebP/GIF rasters are accepted. The download is size- and time-bounded; the image is decoded and transcoded (max 512px edge, usually WebP) before anything is cached. Original remote bytes are not kept. Local portrait upload is not part of this feature. Updating a valid `image_url` clears that person’s cached portraits.

## Database migrations

PRKS automatically upgrades supported older databases at startup.

Current schema version: **16**.

Schema migrations are transactional and version-ordered. A database marked version N has passed every migration through N.

Databases created by a newer PRKS version are refused rather than downgraded. Download a verified backup before installing a PRKS revision that announces a database schema upgrade.

## Documentation

Exact startup, configuration, and safety contracts stay in this README (host/port, Docker publish, env vars, schema version, auth warning). Detailed current feature and user behavior lives in the version-controlled [Wiki source](docs/wiki/Home.md), which is published to the [GitHub Wiki](https://github.com/Fooftilly/PRKS/wiki).

| Topic | Documentation |
| --- | --- |
| Install and run | [Getting Started](docs/wiki/Getting-Started.md) |
| Everyday workflows | [User Guide](docs/wiki/User-Guide.md) |
| Tabs and split view | [Workspace Tabs and Split View](docs/wiki/Workspace-Tabs-and-Split-View.md) |
| PDFs and annotations | [PDFs and Annotations](docs/wiki/PDFs-and-Annotations.md) |
| Research graph/model | [Research Network](docs/wiki/Research-Network.md) |
| Offline/local-first | [Offline and Sync](docs/wiki/Offline-and-Sync.md) |
| Storage and backups | [Storage, Backup, and Restore](docs/wiki/Storage-Backup-and-Restore.md) |
| Configuration/logging/performance | [Configuration and Operations](docs/wiki/Configuration-and-Operations.md) |
| Troubleshooting | [Troubleshooting](docs/wiki/Troubleshooting.md) |
| Architecture | [Architecture](docs/wiki/Architecture.md) |
| Contributor commands | [Developer Reference](docs/wiki/Developer-Reference.md) |
| Dependencies/vendoring | [Dependencies and Vendoring](docs/wiki/Dependencies-and-Vendoring.md) |
| Security | [Security and Operations](docs/wiki/Security-and-Operations.md) |

Implementation/design authorities that intentionally remain outside the Wiki:

- [AGENTS.md](AGENTS.md) — contributor/agent architecture and testing rules.
- [DESIGN.md](DESIGN.md) — authoritative visual/interaction contract.
- [SECURITY.md](SECURITY.md) — vulnerability reporting.
- [docs/local-first-sync.md](docs/local-first-sync.md) — detailed sync design.
- [docs/local-first-rollout-status.md](docs/local-first-rollout-status.md) — current local-first coverage.

## Data and backups

Normal runs use `data/` unless `PRKS_STORAGE` selects another storage root. The canonical library includes the SQLite database and managed research files; several indexes and caches are derived and rebuildable.

Use **Settings → Backup & restore → Download backup** for supported backups. A `.prks-backup` contains private research data and should be protected like the live library.

See [Storage, Backup, and Restore](docs/wiki/Storage-Backup-and-Restore.md) before manual recovery, moving storage, or restoring a library.

## Features (summary)

- **Command palette** — Ctrl/Cmd+K for navigation, search, and common creates. Details: [User Guide](docs/wiki/User-Guide.md#command-palette).
- **Workspace tabs / split view** — in-app tabs, Main + Secondary panes, resize, and local layout memory. Details: [Workspace Tabs and Split View](docs/wiki/Workspace-Tabs-and-Split-View.md).
- **Research Graph** — read-only map of Concepts, Positions, Arguments/Stances, and note references. Details: [Research Network](docs/wiki/Research-Network.md#research-graph).
- **Saved Views** — store search definitions, not result snapshots. Details: [User Guide](docs/wiki/User-Guide.md#saved-views).

## Development

Fast test suite:

```bash
python run_tests.py
```

Full browser E2E gate:

```bash
python run_tests.py --e2e
```

Use [Developer Reference](docs/wiki/Developer-Reference.md) for targeted E2E modes, UX tours, browser fixtures, Sonar tooling, project layout, and the local post-commit archive. Contributor/agent rules are authoritative in [AGENTS.md](AGENTS.md).

## Security

PRKS is a single-user application without built-in authentication. Loopback is the safe default. Network exposure, trusted host behavior, browser-origin protections, Markdown sanitization, privacy-safe logging, and remote portrait constraints are documented in [Security and Operations](docs/wiki/Security-and-Operations.md).

For vulnerability reporting, see [SECURITY.md](SECURITY.md).
