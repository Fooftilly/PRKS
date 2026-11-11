# Configuration and Operations

This page is the detailed operational reference for PRKS runtime configuration, storage-path selection, schema migration behavior, logging, and performance diagnostics.

For a first run, start with [Getting Started](Getting-Started.md). For backup/recovery semantics, see [Storage, Backup, and Restore](Storage-Backup-and-Restore.md).

## Configuration and data layout

| Variable | Purpose |
| -------- | ------- |
| `PRKS_STORAGE` | If set, root directory for persistent data. Database: `$PRKS_STORAGE/prks_data.db`. PDFs: `$PRKS_STORAGE/pdfs/`. Thumbnails: `$PRKS_STORAGE/thumbs/`. |
| `PRKS_TESTING` | When truthy (`1`, `true`, `yes`), uses testing paths and stricter checks. Port **8070**, `data_testing/` when `PRKS_STORAGE` is unset, and refusal of `/data` and the repository `data/` tree are described in [Testing mode](Getting-Started.md#testing-mode). |
| `PRKS_THUMB_LOSSLESS` | When truthy, PDF card thumbnails use lossless WebP/PNG cache encoding (debugging). Default is card-optimized lossy WebP; cache filenames use rev `_v2`. |
| `PRKS_LOG_LEVEL` | Stderr log level. Default `INFO`. Changes volume, not what kinds of data may be logged. |
| `PRKS_LOG_FILE_LEVEL` | Persistent file log level. Default `ERROR`. |
| `PRKS_LOG_RETENTION_DAYS` | Rotated persistent log copies to keep. Default `7`. |
| `PRKS_LOG_FILE` | Override path for the rotating error log. Default `$PRKS_STORAGE/prks-errors.log`. |
| `PRKS_PERF_SLOW_MS` | API request duration in milliseconds counted as slow. Default `250`. Clamped to 10–60000. Does not timeout requests. |
| `PRKS_PERF_LOG_SLOW` | When truthy (`1`, `true`, `yes`, `on`), emit a privacy-safe `slow_request` INFO log for slow API requests. Default off. |
| `PRKS_BACKUP_MAX_UPLOAD_BYTES` | Maximum size of an uploaded `.prks-backup` restore archive. Default 64 GiB. A valid `Content-Length` is required. |
| `PRKS_CONFIG_FILE` | Override path of the bootstrap configuration file (default: `~/.config/prks/config.json` on Linux, `~/Library/Application Support/PRKS/config.json` on macOS, `%LOCALAPPDATA%\PRKS\config.json` on Windows). Ignored in testing mode. |
| `PRKS_TRUSTED_HOSTS` | Extra `Host` header names the server accepts, comma-separated, beyond `localhost`, IP literals and the bind host. Hostnames only: no port, scheme, path or wildcard. An invalid entry stops startup. |
| `PRKS_PDF_LINEARIZE` | `1`/`true` or `0`/`false` turns PDF linearization on or off. Unset, it runs when `qpdf` is installed. |

If `PRKS_STORAGE` is **unset**, non-testing runs use the project’s **`data/`** directory: `data/prks_data.db`, `data/pdfs/`, `data/thumbs/`, and person portrait cache `data/people/` (lossy WebP, max 512px edge, keyed by person id + `image_url` hash).

Person profile images (`GET /api/persons/{id}/profile-image`) are optional. `image_url` must be a direct public HTTP/HTTPS URL (HTTPS preferred) that itself returns HTTP 200. PRKS does not follow redirects, and private/local/link-local targets are refused. Only static JPEG/PNG/WebP/GIF rasters are accepted. The download is size- and time-bounded; the image is decoded and transcoded (max 512px edge, usually WebP) before anything is cached. Original remote bytes are not kept. Local portrait upload is not part of this feature. Updating a valid `image_url` clears that person’s cached portraits.

## PostgreSQL settings

These settings configure every PRKS process: PostgreSQL is the only Library backend (README, "Docker" and "Quick start"; `docs/postgresql-architecture.md` §4.3, §9.3, §11.1, §12.3). `serve`, `migrate`, `worker` and `admin` all need the database URL (`--testing` defaults it to the `scripts/dev-postgres` server). A setting that is set but unusable stops the process with exit status 2. PRKS never falls back to a default.

### Process roles

`python prks_app.py <role>` picks what a process does. With no role, it runs `serve`.

| Role | What it does |
| ---- | ------------ |
| `serve` | The web server. With `PRKS_AUTH` set, it uses PostgreSQL for accounts, sessions, the root pairing and jobs. |
| `migrate` | One-shot: applies the schema. Once setup is complete, it also runs the start check and moves the start generation forward, then exits. |
| `worker` | Runs background jobs beside `serve`. |
| `admin` | Admin commands such as `admin bootstrap` ([admin bootstrap](../admin-bootstrap.md)), `admin backup --library ID --out DIR` (a verified format-2 backup of one Library) and `admin restore --library ID --archive FILE --owner LOGIN` (restores it in place of the Library's contents; the Library's replaced files are kept, not deleted). Each command takes its own `--root`. |

Every `serve`, `migrate` and `worker` start checks that the database and the data root's `prks-root.json` record the same start generation, then moves both forward by one, database first (§11.1). The start generation counts starts, not writes.

### Process environment

| Variable | Read by | Purpose |
| -------- | ------- | ------- |
| `PRKS_AUTH` | `serve` | Turns on PostgreSQL sign-in. `session` uses passwords and `prks_session` cookies. `loopback-owner` acts as the bootstrap owner on every request: it works only for a testing instance (`--testing`) bound to a loopback address, and it is never a default. Unset (the default), the SQLite application runs. |
| `PRKS_DATABASE_URL` | all roles | PostgreSQL connection string. It is a secret: it never appears in errors or logs. Set this or `PRKS_DATABASE_URL_FILE`, never both. |
| `PRKS_DATABASE_URL_FILE` | all roles | Path of a file holding the connection string (a Compose secret, `/run/secrets/prks_database_url`). Preferred over `PRKS_DATABASE_URL`. |
| `PRKS_DB_POOL_MAX` | `serve`, `migrate` | Largest connection pool of the web process (and of `migrate`, which opens the same pool), at least 1. Default `10`. The worker's pool stays at `4`. |
| `PRKS_MIGRATE_ON_START` | `serve` | `1` (default) applies the schema at start. `0` leaves that to `migrate` and only verifies it: the web process still refuses a schema other than its code's. `worker` never applies the schema and always verifies it. Compose sets `0`. |
| `PRKS_EMBEDDED_WORKER` | `serve` | `1` (default) runs a job worker inside the web process. `0` runs none, for a separate `worker`. Compose sets `0`. |
| `PRKS_LIBRARY_BACKEND` | every role | Retired (PG-C2 #641): PostgreSQL is the only Library backend. A process that finds it set, to any value, refuses to start with exit status 2; remove it. |
| `PRKS_PUBLIC_ORIGIN` | `serve` | The origin browsers use behind an HTTPS reverse proxy, such as `https://prks.example.org`: scheme, host and optional port only. Sign-in checks `Origin` against it, and its host is accepted as a `Host`. |
| `PRKS_TRUSTED_PROXIES` | `serve` | Comma-separated proxy addresses or networks whose `X-Forwarded-For` is believed for sign-in rate limiting. Name only your proxy, and make the web port reachable only by it. Anyone who reaches the port from a trusted address can spread sign-in attempts over invented addresses. |
| `PRKS_SESSION_IDLE_DAYS` | `serve` | Days a session stays valid without use, 1 to 365. Default `30`. |
| `PRKS_LOGIN_WINDOW_SECONDS` | `serve` | Length of the sign-in rate-limit window, 1 second to 1 day. Default `900` (15 minutes). |
| `PRKS_LOGIN_ACCOUNT_LIMIT` | `serve` | Sign-in attempts allowed per account per window from a browser that is not a known sign-in device for it, 1 to 10000. Default `5`. |
| `PRKS_LOGIN_ADDRESS_LIMIT` | `serve` | Sign-in attempts allowed per client address per window, 1 to 10000. Default `20`. It counts the same attempts as the account limit; attempts from a known sign-in device count only against the device limit. |
| `PRKS_LOGIN_DEVICE_LIMIT` | `serve` | Sign-in attempts allowed per known sign-in device per window, 1 to 10000. Default `5`. |

`PRKS_STORAGE` selects the data root, as above. In Compose it is `/data`.

### Compose variables (`.env`)

`docker-compose.postgres.yml` reads these from `.env`. `.env.example` lists them with their defaults. Never put a password or a database URL in `.env`.

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `UID`, `GID` | `1000` | Host user and group the PRKS containers run as, so files in the data directory belong to you. |
| `PRKS_DATA_DIR` | `./data` | Host directory mounted at `/data` as the data root. Create it before the first `up`. |
| `PRKS_DB_PASSWORD_FILE` | `./secrets/db_password` | Owner-only file with the `prks` role's password. Only `db` gets it. PostgreSQL reads it only when it creates the database. |
| `PRKS_DB_URL_FILE` | `./secrets/database_url` | Owner-only file with the full database URL. It becomes the `prks_database_url` secret that `migrate`, `web` and `worker` read through `PRKS_DATABASE_URL_FILE`. |
| `PRKS_PUBLISH_HOST` | `127.0.0.1` | Host address the web port is published on. Leave it on loopback unless an HTTPS reverse proxy you control sits in front. |
| `PRKS_PUBLISH_PORT` | `8080` | Host port the web port is published on. |
| `PRKS_PUBLIC_ORIGIN`, `PRKS_TRUSTED_PROXIES`, `PRKS_TRUSTED_HOSTS` | empty | Passed to `web` (above). |

### Health endpoints

`GET /healthz` answers 200 while the process runs, with no I/O. `GET /readyz` answers 200 only when the database is reachable, its schema is exactly the code's, setup is complete and the data root is still paired with this database. Otherwise it answers 503, with no detail. On a first run, `serve` waits for setup before it opens its port, so until setup is complete `/readyz` cannot be reached at all rather than answering 503. Without `PRKS_AUTH`, `/readyz` always answers 503.

## Database migrations

PRKS automatically upgrades supported older databases at startup.

Current schema version: **17**.

Schema migrations are transactional and version-ordered. A database marked version N has passed every migration through N.

Databases created by a newer PRKS version are refused rather than downgraded. Download a verified backup before installing a PRKS revision that announces a database schema upgrade.

## Logging and privacy

Persistent log: `<storage>/prks-errors.log`. Default persistent threshold is **ERROR**. Rotation is daily at midnight. Retention is **7 days**.

PRKS logs describe operations and failures, not the contents of the research library. Increasing `PRKS_LOG_LEVEL` or `PRKS_LOG_FILE_LEVEL` (including `DEBUG`) changes volume, not privacy policy.

Logs may include:

- event names
- request IDs
- safe endpoint templates (`/api/search`, `/api/pdfs/:pdf`, `/api/works/:id`)
- HTTP status
- internal opaque IDs (`work_id`, `processing_file_id`)
- counts, page numbers, byte ranges, file sizes
- exception class names
- repository-relative traceback locations

PRKS deliberately does not log:

- research titles, notes, abstracts, annotations, or selected PDF text
- person, tag, folder, publisher, Concept, Position, or Argument names
- Concept aliases, definitions, Argument main text, verdict labels, or backlink snippets
- search terms
- PDF filenames or absolute filesystem paths
- source, portrait, or image URLs
- request bodies, query strings, or headers (`Host`, `Origin`, `User-Agent`, …)
- client/LAN IP addresses
- browser messages, stacks, routes, or hash state
- raw exception messages
- qpdf stderr

Docker captures process stderr. Console output follows the same privacy rules as the persistent file.

There is no remote telemetry. `POST /api/client-errors` is same-application metadata for correlating browser failures with server request IDs.

## Performance diagnostics

Use **Settings → Performance diagnostics** to see what is slow in this PRKS process.

Measurements live only in memory. They reset when PRKS restarts or when you click **Reset**. They are not written to disk, not included in backups, and contain aggregate operational metadata only: safe route templates, HTTP method/status, durations, counts, and response sizes. They never include search terms, query strings, request bodies, titles, notes, filenames, paths, SQL, or person names. The same Settings page also shows Client request coordinator counters (in-flight occupancy, retries, dedupe joins, burst-cache hits). Those are memory-only too and never include URLs, query strings, bodies, or entity ids. A copied report is safe to paste into a bug report.

The table lists API routes with:

- **Calls** — how often the route ran in this measurement window (frequency, not just latency)
- **Avg / P50 / P95 / Max** — duration in milliseconds. P50 and P95 are nearest-rank percentiles of the recent bounded sample (last 128 durations for that route), not a permanent historical distribution. Tiny samples are not statistically strong.
- **DB** — measured DB share: instrumented `execute_query()` time divided by request time. This is directional, not profiler-grade SQL accounting. Some direct SQLite work is not included.

Subsystem lines (PDF file stats, JSON encode, gzip, thumbnail render, PDF text search, text-index phases, and similar) use the same timing rules with fixed span names only. Text-index reconciliation reports `text_index_reconcile` plus `text_index_load_state`, `text_index_source_scan`, `text_index_extract`, `text_index_write`, and `text_index_fts_verify` when those phases ran.

Optional:

```bash
PRKS_PERF_SLOW_MS=150
PRKS_PERF_LOG_SLOW=1
```

Slow logs stay privacy-safe (`method`, templated `route`, status, durations, call counts, request id). They do not stop or timeout requests.

Do not add indexes, caching, threading, or SQLite tuning solely because an endpoint looks expensive on paper. Measure first.

## See also

- [Security and Operations](Security-and-Operations.md)
- [Storage, Backup, and Restore](Storage-Backup-and-Restore.md)
- [Troubleshooting](Troubleshooting.md)
