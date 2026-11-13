# PRKS — Personal Research Knowledge System

PRKS is a self-hosted, local-first research library for organizing PDFs, Markdown research notes, online video references, bibliographic metadata, people, tags, reading progress, PDF annotations, playlists, and structured research relationships.

**License:** [GNU Affero General Public License v3.0 only](LICENSE) (`AGPL-3.0-only`). See [Licensing](#licensing) for third-party notices and source-availability details.

It runs as a Python web application with its Library in PostgreSQL 18 and its PDFs and portraits as files in a data directory. Docker Compose runs both for you.

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
- Run with Docker Compose, or from source against your own PostgreSQL 18 server.

## Requirements

- **PostgreSQL 18** holds the Library. Docker Compose (below) runs it for you; to run PRKS from source, use a PostgreSQL 18 server you control.
- **Python 3.12+** and the exact runtime package pins from `requirements.txt`, for a run from source.

Prefer a project-local virtual environment. On PEP 668 “externally managed” systems, do not use `sudo pip` or `--break-system-packages`.

## Quick start

The quickest way to start is Docker Compose: see [Docker](#docker). It runs the database, the schema step, the web server and the job worker, and needs no Python on the host.

### From source

PostgreSQL is the only Library backend. Create an empty PostgreSQL 18 database for PRKS, with the builtin locale (`CREATE DATABASE prks LOCALE_PROVIDER builtin BUILTIN_LOCALE 'PG_UNICODE_FAST' TEMPLATE template0`), then:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
export PRKS_DATABASE_URL='postgresql://prks:<password>@127.0.0.1:5432/prks'
./.venv/bin/python prks_app.py
```

`prks_app.py` validates the Python environment (minimum version + exact `requirements.txt` pins via `importlib.metadata`) **before** it touches the database, the storage root or the network. A mismatch exits non-zero with platform-aware remediation. It never auto-runs `pip` and never contacts PyPI. It then applies the database schema, binds the data root (`data/` by default) and, on a first run, **waits for setup**. In a second terminal, with the same `PRKS_DATABASE_URL`, create the owner account and the Library ([admin bootstrap](docs/admin-bootstrap.md)):

```bash
./.venv/bin/python prks_app.py admin bootstrap issue-token --root data
./.venv/bin/python prks_app.py admin bootstrap complete --root data --login alice
```

The first command prints a one-time token; the second asks for it and for the owner's password. The waiting server then starts serving. Open [http://127.0.0.1:8080](http://127.0.0.1:8080) and sign in with that login and password.

The process listens on **127.0.0.1:8080** only. Optional port (still loopback):

```bash
python prks_app.py --port 9000
```

To listen on every local interface (LAN or VPN), pass an explicit host:

```bash
python prks_app.py --host 0.0.0.0
```

`--host localhost` also works and binds that name. The default is the literal address `127.0.0.1`, not `localhost`. Every account signs in with its password. Still do not expose PRKS to an untrusted network without an HTTPS reverse proxy: see [Security and Operations](docs/wiki/Security-and-Operations.md) before changing the bind/publish address.

`PRKS_LIBRARY_BACKEND` is no longer read. A process that finds it set, to any value, refuses to start until you remove it. A data root that still holds a pre-0.8 SQLite Library (`prks_data.db` or `prks_data_testing.db`) is refused too: this version does not read it, so start on a new data root.

### Testing mode

```bash
scripts/dev-postgres            # once: a local PostgreSQL 18 for testing, in Docker
unset PRKS_DATABASE_URL         # if you exported your Library's URL above
python prks_app.py --testing
```

Testing mode is a disposable instance for development and agents. It sets `PRKS_TESTING=1`, uses port **8070** by default (unless you pass `--port`) and, with `PRKS_STORAGE` unset, the `data_testing/` root, so the repository's `data/` is untouched. You may set `PRKS_STORAGE` to an explicit safe testing root. Testing mode refuses `/data` and the repository `data/` directory (and descendants), including via symlinks.

Its database is `PRKS_DATABASE_URL`, or by default the one `scripts/dev-postgres` serves (`postgresql://prks:prks@127.0.0.1:55433/prks_testing`; `scripts/dev-postgres stop` stops it, `reset` deletes it, and then delete `data_testing/` too). **Before it writes anything, a testing process proves its database is a testing one**: the database must be marked for testing (`scripts/dev-postgres` and the test harness mark theirs) and hold no non-testing instance, or the process exits with status 2 and leaves it untouched. To use a database you created only for testing, mark it with `COMMENT ON DATABASE <name> IS 'prks-testing'`; never mark one that holds a real Library. On a fresh testing database the first start creates its owner and Library itself and every request acts as that owner (`PRKS_AUTH=loopback-owner`, loopback only); set `PRKS_AUTH=session` to sign in with a password instead.

## Docker

`docker-compose.yml` runs PRKS as the PostgreSQL design lays it out ([`docs/postgresql-architecture.md`](docs/postgresql-architecture.md) §12.3). Four services:

| Service | What it does |
| ------- | ------------ |
| `db` | PostgreSQL 18; named volume `pgdata` at `/var/lib/postgresql` (the PostgreSQL 18 location, never `/var/lib/postgresql/data`); no host port |
| `migrate` | One-shot: applies the schema, then, once setup is complete, runs the start check (below) and exits. `web` and `worker` start only after it succeeded |
| `web` | `prks_app.py serve` with password sign-in (`PRKS_AUTH=session`); healthcheck on `/readyz` |
| `worker` | `prks_app.py worker`: background jobs, beside `web` |

Every service sets `PRKS_MIGRATE_ON_START=0`, so `migrate` is the only schema runner, and reads the database URL from the `prks_database_url` Compose secret (`PRKS_DATABASE_URL_FILE`). Only the services that list a secret can read it. The data directory (`./data` by default, `PRKS_DATA_DIR`) is mounted at `/data`, and the containers run as **`${UID:-1000}:${GID:-1000}`** so files on the bind mount match your user.

Docker images build the classic frontend into two ordered JavaScript bundles (rather than 84 separate requests); the raw Python source checkout remains unbundled. To rebuild the previous frontend without bundles, use `docker compose build --build-arg PRKS_CLASSIC_BUNDLES=0` followed by `docker compose up -d`. This affects packaged static assets only, not library files or the database. Build details, cache versioning and validation: [Classic-script distribution](docs/classic-script-distribution.md).

The `web` container binds **0.0.0.0:8080** so Docker port forwarding can reach it. Compose then publishes that port on the **host loopback** only (`127.0.0.1:8080`). Container `0.0.0.0` is not the same as exposing PRKS on every host interface.

**1. Secrets and data directory.** Create them once, owner-only, outside version control (`secrets/` and `.env` are ignored by Git and by Docker builds):

```bash
install -d -m 700 secrets data
umask 077
# Refuse an existing file: PostgreSQL reads the password only when it creates the database.
( set -C; openssl rand -hex 32 > secrets/db_password ) || exit 1
printf 'postgresql://prks:%s@db:5432/prks\n' "$(cat secrets/db_password)" > secrets/database_url
cp .env.example .env   # then set UID/GID to `id -u`/`id -g`
```

**Do not regenerate `secrets/db_password` on an initialized volume:** changing the file does not change the `prks` role's password. Rotate it with an authenticated `ALTER ROLE` and update both secret files together.

**2. Start and set up.**

```bash
./docker-build.sh
docker compose up -d
```

On a new, empty data directory `web` binds it as a PRKS data root (`prks-root.json`) and then **waits for setup**: it serves nothing, and `/readyz` stays unreachable (the container is unhealthy) until you create the owner and the Library. `worker` waits too. Run setup in the waiting `web` container ([admin bootstrap](docs/admin-bootstrap.md)):

```bash
docker compose exec web python /app/prks_app.py admin bootstrap issue-token --root /data
docker compose exec web python /app/prks_app.py admin bootstrap complete --root /data --login alice
```

`web` then starts serving at [http://127.0.0.1:8080](http://127.0.0.1:8080) and turns healthy. Loopback is the default publish address. Expose PRKS on a network only behind an HTTPS reverse proxy, with `PRKS_PUBLISH_HOST`, `PRKS_PUBLIC_ORIGIN` and `PRKS_TRUSTED_PROXIES` set in `.env`. `PRKS_TRUSTED_PROXIES` must name only the proxy, and the web port must then be reachable only by that proxy (keep it on loopback, or drop the published port and put the proxy on the Compose network). Anyone who reaches the port from a trusted address can set `X-Forwarded-For` and spread their sign-in attempts over invented addresses.

**Health and stopping.** `GET /healthz` answers 200 while the process runs. `GET /readyz` answers 200 only when the database is reachable, its schema is exactly this image's, setup is complete and the data root is still paired with this database; otherwise 503. Neither shows any detail. On `docker compose stop` (SIGTERM) `web` stops accepting connections and exits, and `worker` claims nothing more and finishes its running job or hands it back, within `stop_grace_period` (30 s).

**Start generation.** The database and the data root's `prks-root.json` both record a *start generation*. Every start of `migrate`, `web` or `worker` checks that the two match and moves both forward by one, database first. A database behind its data root, or one that ran elsewhere, is refused. If a start stops between its two writes, `web` stays not ready until the next start of any service finishes it (`docker compose run --rm migrate` does). **The start generation counts starts, not writes**, so it cannot catch every stale copy of the database (below).

**Upgrades.** Build the new image, then `docker compose up -d`: `migrate` runs first, while the previous `web` and `worker` may still hold the data root, and the new `web` and `worker` refuse a schema other than their own. Before upgrading PostgreSQL itself or moving its volume, read the next two paragraphs.

**One database per data root.** At most one database ever runs against a data root. **Two databases running against one data root at the same time can permanently delete files, and PRKS cannot detect it**: each database's cleanup removes files that only the other one still references. A copy of the database (a volume snapshot, `pg_dump`, an upgraded or moved cluster) looks like the original to PRKS:

- A database copy taken while PRKS is serving is never started against this data root. Start it only against its own copy of the data root, taken at the same moment (whole-instance disaster recovery).
- Once a copy (for example an upgraded cluster) has started against the data root, retire the database it came from: move the old cluster's data directory out of the volume, or delete it. It must never start here again.
- **Reverting the image tag is not a rollback.** Prepare a rollback before upgrading. Either keep the old database's data directory together with a copy of the data root, both taken offline at the same moment, and restore them together. Or restore a format-2 Library backup into a fresh instance with its own new data root, never into the old database on this root.

**Database volume capacity.** PRKS cannot see the free space on the PostgreSQL volume. Monitor it, and keep free at least twice the database's size plus `max_wal_size` (1 GB by default): a Library restore stages a copy of the Library's rows before the old ones are vacuumed. If the volume fills up, free space, restart `db` if it stopped, and let PRKS clean up the failed restore on its next start. A recreated (empty) database volume does not take over a used data root: setup refuses it, and its files stay untouched.

**Lifecycle.** `docker compose down` stops every service and keeps the database volume. **`down -v` deletes the database volume.** The CI acceptance run of this file is `scripts/ci/test_postgres_compose.sh`.

## Configuration and data layout

| Variable | Purpose |
| -------- | ------- |
| `PRKS_DATABASE_URL` | The PostgreSQL 18 database of the Library (`PRKS_DATABASE_URL_FILE`: a file holding it). Required by every role, except that `--testing` defaults to the `scripts/dev-postgres` database. |
| `PRKS_AUTH` | `session` (the default): password sign-in. `loopback-owner` (the default of `--testing`): every request acts as the owner; testing mode on a loopback address only. |
| `PRKS_STORAGE` | If set, the data root: the root marker, managed PDFs and portraits, thumbnails and logs. |
| `PRKS_TESTING` | When truthy (`1`, `true`, `yes`), uses testing paths and stricter checks (see [Testing mode](#testing-mode) above). |
| `PRKS_THUMB_LOSSLESS` | When truthy, PDF card thumbnails use lossless WebP/PNG cache encoding (debugging). Default is card-optimized lossy WebP; cache filenames use rev `_v2`. |
| `PRKS_LOG_LEVEL` | Stderr log level. Default `INFO`. Changes volume, not what kinds of data may be logged. |
| `PRKS_LOG_FILE_LEVEL` | Persistent file log level. Default `ERROR`. |
| `PRKS_LOG_RETENTION_DAYS` | Rotated persistent log copies to keep. Default `7`. |
| `PRKS_LOG_FILE` | Override path for the rotating error log. Default `$PRKS_STORAGE/prks-errors.log`. |
| `PRKS_PERF_SLOW_MS` | API request duration in milliseconds counted as slow. Default `250`. Clamped to 10–60000. Does not timeout requests. |
| `PRKS_PERF_LOG_SLOW` | When truthy (`1`, `true`, `yes`, `on`), emit a privacy-safe `slow_request` INFO log for slow API requests. Default off. |
| `PRKS_BACKUP_MAX_UPLOAD_BYTES` | Maximum size of an uploaded `.prks-backup` restore archive. Default 64 GiB. A valid `Content-Length` is required. |
| `PRKS_CONFIG_FILE` | Override path of the bootstrap configuration file (default: `~/.config/prks/config.json` on Linux, `~/Library/Application Support/PRKS/config.json` on macOS, `%LOCALAPPDATA%\PRKS\config.json` on Windows). Ignored in testing mode. |

If `PRKS_STORAGE` is **unset**, non-testing runs use the project’s **`data/`** directory: managed PDFs and portraits, `data/thumbs/`, and person portrait cache `data/people/` (lossy WebP, max 512px edge, keyed by person id + `image_url` hash).

The storage root is chosen by the first of these that is set, and a set but invalid choice is a startup error rather than a fall-through: `python prks_app.py --storage-root PATH` (this run only), `PRKS_STORAGE`, `storage.local_root` in the bootstrap configuration file (nothing in PRKS writes that file yet), then the default above. Testing mode never reads the bootstrap file. A relative or `~` path is made absolute once at startup.

Every storage root carries a small `prks-root.json` marker with a stable `storage_root_id`. PRKS writes it the first time it opens a root: a new empty directory, or an existing library (one that has `prks_data.db` or `pdfs/`). PRKS refuses to open a non-empty directory that does not look like a PRKS library, a root whose marker is unreadable or from a newer PRKS, a root a storage move left retired or unfinished, and a root selected by the bootstrap file that is missing or empty (for example an unmounted disk). It also refuses links inside the root (the root itself may be a link), components on another filesystem, and roots on NFS/SMB and other network filesystems. The marker is not part of backups; a restore keeps the target root's marker.

Exactly one PRKS web process serves a storage root at a time; the `migrate` and `worker` roles may share it beside that web process (they hold `.prks-maintenance/root.lock` shared). A second web process pointed at the same root is refused. One database runs against one data root (see [Docker](#docker), "One database per data root").

Person profile images (`GET /api/persons/{id}/profile-image`) are optional. `image_url` must be a direct public HTTP/HTTPS URL (HTTPS preferred) that itself returns HTTP 200. PRKS does not follow redirects, and private/local/link-local targets are refused. Only static JPEG/PNG/WebP/GIF rasters are accepted. The download is size- and time-bounded; the image is decoded and transcoded (max 512px edge, usually WebP) before anything is cached. Original remote bytes are not kept. Local portrait upload is not part of this feature. Updating a valid `image_url` clears that person’s cached portraits.

## Database migrations

Every start applies the PostgreSQL schema (`backend/db/schema/`) before it serves, unless `PRKS_MIGRATE_ON_START=0` leaves that to `python prks_app.py migrate` (Compose does). Each schema file is applied once, in order, inside a transaction, and its checksum is recorded. A process refuses a database whose applied files differ from its own or that a newer PRKS migrated, rather than downgrading it. Download a verified backup before installing a PRKS revision that announces a schema change.

Before 1.0 there is no migration from a pre-0.8 SQLite Library: start PostgreSQL PRKS on a new data root.

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
| Accessibility goals, tested areas, known barriers and reporting | [ACCESSIBILITY.md](ACCESSIBILITY.md) |

Implementation/design authorities that intentionally remain outside the Wiki:

- [AGENTS.md](AGENTS.md) — contributor/agent architecture and testing rules.
- [DESIGN.md](DESIGN.md) — authoritative visual/interaction contract.
- [SECURITY.md](SECURITY.md) — vulnerability reporting.
- [docs/local-first-sync.md](docs/local-first-sync.md) — detailed sync design.
- [docs/local-first-rollout-status.md](docs/local-first-rollout-status.md) — current local-first coverage.

## Data and backups

Normal runs use `data/` unless `PRKS_STORAGE` selects another storage root. The canonical library is the PostgreSQL database together with the managed files under the data root; several indexes and caches are derived and rebuildable.

Use **Settings → Backup & restore → Download backup** for supported backups. A `.prks-backup` contains private research data and should be protected like the live library.

See [Storage, Backup, and Restore](docs/wiki/Storage-Backup-and-Restore.md) before manual recovery, moving storage, or restoring a library.

## Features (summary)

- **Command palette** — Ctrl/Cmd+K for navigation, search, and common creates. Details: [User Guide](docs/wiki/User-Guide.md#command-palette).
- **Workspace tabs / split view** — in-app tabs, Main + Secondary panes, resize, and local layout memory. Details: [Workspace Tabs and Split View](docs/wiki/Workspace-Tabs-and-Split-View.md).
- **Research Graph** — read-only map of Concepts, Positions, Arguments/Stances, and note references. Details: [Research Network](docs/wiki/Research-Network.md#research-graph).
- **Saved Views** — store search definitions, not result snapshots. Details: [User Guide](docs/wiki/User-Guide.md#saved-views).

## Development

Install runtime pins plus development/test tooling (includes `openapi-core` for the unit-contract preflight that `python run_tests.py` always runs):

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
```

Fast test suite (no Chromium):

```bash
./.venv/bin/python run_tests.py
```

Full browser E2E gate (Playwright; Chromium installs into `.playwright-browsers/` if missing):

Every E2E server runs the PostgreSQL-backed sign-in on its own disposable database, so E2E needs `PRKS_TEST_DATABASE_URL` set to the `postgres` maintenance database of a disposable PostgreSQL 18 server (the same variable `scripts/test-postgres` uses). Without it, each E2E server refuses to start.

```bash
./.venv/bin/python run_tests.py --e2e
```

A runtime-only install (`requirements.txt` alone) is enough to run `prks_app.py`, but not enough for `run_tests.py`: the unit suite preflights `openapi-core` from `requirements-dev.txt`.

Use [Developer Reference](docs/wiki/Developer-Reference.md) for targeted E2E modes, UX tours, browser fixtures, Sonar tooling, project layout, and the local post-commit archive. Contributor/agent rules are authoritative in [AGENTS.md](AGENTS.md).

## Licensing

Copyright © 2026 Nikola Perović and other PRKS contributors. Each contributor retains
copyright in their own contributions. This notice does not claim ownership of
vendored, third-party or user-supplied material.

PRKS's original application code and documentation are made available under the
[GNU Affero General Public License version 3 only](LICENSE)
(`SPDX-License-Identifier: AGPL-3.0-only`), subject to the rights of
individual authors and separately licensed materials. You may use, modify, and
redistribute covered code under that license; **no warranty** is provided.

This license does **not** relicense third-party dependencies, vendored scripts,
fonts, PDF viewer components, or user-supplied research files. The
[third-party notices](THIRD_PARTY_NOTICES.md) list bundled components and their
original license files. Contributors should read [CONTRIBUTING.md](CONTRIBUTING.md).

**Source for network users:** The unmodified upstream source is at
[Fooftilly/PRKS](https://github.com/Fooftilly/PRKS). The application also exposes
a source and license link under **Settings → Diagnostics → About PRKS**.
If you distribute or operate a *modified* PRKS, you are responsible for making
the Corresponding Source for **your actual deployed version** available under
the applicable AGPL terms. An upstream link alone is not sufficient for
your own modifications; update the in-app source link or supply an equally
prominent link to the exact matching source, including build instructions and
required corresponding material. The application's original data (PDFs, notes,
research libraries, and backups) is not licensed by this project.

## Security

Every PRKS account signs in with a password (`PRKS_AUTH=session`); testing mode alone may act as its owner without one, on loopback. Loopback is still the safe default. Network exposure, trusted host behavior, browser-origin protections, Markdown sanitization, privacy-safe logging, and remote portrait constraints are documented in [Security and Operations](docs/wiki/Security-and-Operations.md).

For vulnerability reporting, see [SECURITY.md](SECURITY.md).
