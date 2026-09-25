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

If `PRKS_STORAGE` is **unset**, non-testing runs use the project’s **`data/`** directory: `data/prks_data.db`, `data/pdfs/`, `data/thumbs/`, and person portrait cache `data/people/` (lossy WebP, max 512px edge, keyed by person id + `image_url` hash).

Person profile images (`GET /api/persons/{id}/profile-image`) are optional. `image_url` must be a direct public HTTP/HTTPS URL (HTTPS preferred) that itself returns HTTP 200. PRKS does not follow redirects, and private/local/link-local targets are refused. Only static JPEG/PNG/WebP/GIF rasters are accepted. The download is size- and time-bounded; the image is decoded and transcoded (max 512px edge, usually WebP) before anything is cached. Original remote bytes are not kept. Local portrait upload is not part of this feature. Updating a valid `image_url` clears that person’s cached portraits.

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
