# Production HTTP server (#1329)

Status: **accepted** (#1330, merged 2026-11-19); Phase B (the migration) is in
progress, see "Migration status". This settles the pre-1.0 question in
`docs/postgresql-architecture.md` §4.5 (PG20, decision 9, risk R9). The
maintainer decided on 2026-11-19 to replace the stdlib server rather than
harden it behind a reverse proxy. This document picks the stack and the
process model.

## Migration status

Phase B1 (#1335) added the Uvicorn transport (`backend/web/`), and Phase B2
(#1338) made it the default. Phase B3 removes the standard-library server:
`PRKSThreadingTCPServer` and the `stdlib` choice are gone, `make_server`
always binds Uvicorn, and the `PRKS_HTTP_SERVER` setting is deleted.
`PRKS_HTTP_THREADS` (1 to 256, default 16) bounds the threads that run PRKS
request work; up to 8 requests per thread wait for one,
and further requests get an immediate 503 with `Retry-After`. Compose passes
`PRKS_HTTP_THREADS` to `web`.

`PRKSHandler` no longer subclasses `http.server.SimpleHTTPRequestHandler`:
its base is `backend.web.handler_base.HttpHandlerBase` (response head,
plain-text `send_error`, static frontend files with `Last-Modified` and
`If-Modified-Since`), and nothing imports `http.server`. A frontend directory
without `index.html` answers 404; directory listings are not served. That
completes step 8 below.

## Decision

| Question | Answer |
| --- | --- |
| Interface | **ASGI** at the edge only. |
| Server | **Uvicorn**, pinned like every other runtime dependency. |
| Framework | **Starlette** as the thin request/response layer. PRKS keeps its own dispatch, admission, controllers and response rules; Starlette's routing, middleware stack and request parsing are used only where they replace code PRKS would otherwise write. |
| Domain code | Stays **synchronous** (psycopg 3 sync API, PG3). Each request's PRKS work runs in a bounded worker thread pool. Nothing in `backend/domain/` becomes `async`. |
| Process model | **One web process**, as today. Multiple web processes wait for #1331, which moves the last process-local state into PostgreSQL. |

## Long-term support

The first draft of this record proposed Cheroot. On review the maintainer
asked whether it is a safe long-term choice. Measured on 2026-11-19 from PyPI
release history and pypistats.org downloads:

| Server | Downloads, last month | Releases since 2025-01-01 | Last release |
| --- | --- | --- | --- |
| Uvicorn | 678 M | 30 | 2026-09-25 |
| Gunicorn | 127 M | 15 | 2026-11-12 |
| Waitress | 7.8 M | 0 | 2024-11-16 |
| Cheroot | 2.8 M | 5 | 2025-11-07 |

Starlette had 35 releases in the same period (last 2026-10-24) and underpins
FastAPI. Uvicorn and Starlette are the most used and most actively released
Python web stack. Their maintainers and the FastAPI ecosystem make
abandonment the least likely risk of any option. Cheroot and Waitress are
maintained by small teams with slow release cadence, so a security fix could
take a long time to land. Popularity is not the only criterion, but for a
component that faces the network for years after 1.0, it is weighty.

## What the server has to carry

Everything below was read from master 02845f1.

- **Dispatch.** `backend/server.py` holds `PRKSHandler(http.server.SimpleHTTPRequestHandler)` and `PRKSThreadingTCPServer(ThreadingMixIn, TCPServer)`. The `do_*` methods run host validation, `/healthz` and `/readyz`, admission (`backend/api/session.py`), the Library access gate and then the route families in `backend/api/*.py`.
- **Controllers use a narrow handler surface.** Across `backend/api/*.py`, the controllers call:
  - `handler.send_json` (194 times) and `send_error` (28);
  - `_prks_if_none_match` and `_send_json_not_modified` (11 each);
  - `_prks_request_context` (7);
  - a handful of others: `headers`, `rfile`, `wfile`, `connection`, `send_response`/`send_header`/`end_headers`, `_send_pdf_stream`, `_send_person_profile_image_bytes` and `_send_not_ported`.

  A responder object with that surface lets every controller move unchanged first.
- **Cross-cutting concerns in the handler:**
  - host and origin checks (`PRKS_TRUSTED_HOSTS`, `PRKS_PUBLIC_ORIGIN`, `PRKS_TRUSTED_PROXIES`);
  - URL NUL refusal;
  - the JSON body limit (50 MiB) and media types;
  - request IDs and safe logging;
  - performance spans and `Server-Timing`;
  - the Library epoch header;
  - `X-Content-Type-Options`;
  - static cache headers;
  - closing the connection after an unread body.
- **Streaming responses:**
  - NDJSON backup progress (`POST /api/backups/progress`, a write and flush per line);
  - PDF bytes with RFC 9110 Range (`parse_byte_range`, 206 and 416);
  - backup download.
- **Streaming request bodies:**
  - Processing upload (`POST /api/processing-items`, up to 2 GiB);
  - backup stage (`POST /api/backups/stage`, up to 64 GiB by default).

  Both read the socket directly through `processing_api.TimedBody`, with a 30 s idle timeout and a deadline, because the upload holds the Library read gate that a restore waits for.
- **Lifecycle.** `run_server` binds the server. The embedded job worker (`backend/jobs/embedded.py`) serves the server and stops it gracefully on SIGTERM or SIGINT. The web process holds the data root's lease shared and bumps the start generation once per process start (§7.5, §11.1).
- **Process-local state** that correctness still depends on: `LibraryAccessGate` and the save-confirm tokens. #1331 retires them, as §7.5 requires.
- **What the stdlib server lacks:**
  - socket timeouts (`StreamRequestHandler.timeout` is `None`, so an idle or half-sent request holds its thread forever);
  - a bound on thread count;
  - HTTP/1.1 keep-alive (`protocol_version` is the default HTTP/1.0);
  - a graceful drain of in-flight requests;
  - a way to drop `Server: SimpleHTTP/x Python/x.y` (#1306).

## Options compared

| Option | Fit for PRKS | Verdict |
| --- | --- | --- |
| **Uvicorn + Starlette** (ASGI) | Most used and most active (see above). The event loop owns the sockets, so slow clients hold no worker thread. Bodies stream to the app as they arrive. Graceful shutdown drains in-flight requests. Runs on Linux, macOS and Windows. The cost is an async edge: sync PRKS code runs in a thread pool, and the two streamed-upload routes pull chunks through a small `anyio.from_thread` bridge (spiked below). Starlette has no global body limit, so PRKS keeps enforcing its own per-route limits, as it does today. | **Chosen** |
| **Gunicorn** (`gthread`, one worker) | Popular, mature and active, and it streams bodies. It is Unix only. The maintainer accepted a second, Windows-only server if that were the only obstacle, with Linux as the main supported platform. But two servers means two sets of body, timeout and shutdown behavior to test, and the realistic Windows partner (Waitress) buffers whole bodies. Gunicorn's main strength, many worker processes, is unusable until #1331, and its threaded workers tie a thread to each slow client. Uvicorn gives one server on every platform. If several processes are wanted after #1331, Gunicorn can still manage Uvicorn workers on Linux without changing the app. | Rejected for now |
| **Cheroot** (WSGI, threaded) | Streams bodies, drains on `stop()`, runs on Windows. But it is little used (2.8 M downloads a month) with a slow release cadence. Half-sent requests hold pool threads until the socket timeout. | Rejected: long-term support |
| **Waitress** (WSGI, threaded) | Handles slow clients well, but has had no release since 2024-11. It buffers *every* request body before calling the app, so a 64 GiB restore archive would be spooled to the temp directory and copied again. `close()` does not drain. | Rejected |
| **Flask or Django** | A full framework would replace routing, validation and error handling that PRKS already owns and tests. AGENTS.md: no framework ahead of real need. | Rejected |

## Spike evidence

Run on Python 3.13. Each server ran a 30-line app on loopback; these were
throwaway scripts, not PRKS code. Versions: uvicorn 0.54.0 with
starlette 1.10.0, cheroot 11.1.2, waitress 3.0.2.

| Check | Uvicorn + Starlette | Cheroot | Waitress |
| --- | --- | --- | --- |
| NDJSON lines from a **sync** generator, 1 s apart | Delivered 1 s apart | Delivered 1 s apart | Delivered 1 s apart |
| 3 MB body sent in three parts 1 s apart, read by **sync** code chunk by chunk | Streamed: read took 2.00 s (through the `from_thread` bridge) | Streamed: 2.00 s | Buffered before the app: 0.002 s |
| Normal request while 50 (8 for Cheroot) clients hold half-sent headers | Served in 1 ms | Waited 10 s for the socket timeout | Served in 7 ms |
| `Server` header | None (`server_header=False`) | Overridable by the app | None (`ident=''`) |
| Stop with a 2 s sync request in flight | Request finished; stop took 1.5 s | Request finished; stop took 1.5 s | Request finished, but the task thread raised `OSError` |
| Header over 16 KiB | Accepted when sent at once: the limit has to come from the adapter or the proxy | 413 | not checked |

## Design for Phase B

1. **Adapter.** Add an ASGI application (proposed `backend/web/`) on Starlette. One endpoint per HTTP method hands the request to a worker thread. There, a responder with today's handler surface runs today's dispatch, so every controller moves unchanged first. Admission, host and origin checks, request IDs, performance spans and the response headers become middleware or adapter code, written once. Security headers (#1305 CSP and anti-framing, and #1306 `Server`) are added there. A header-size cap is added in the adapter.
2. **Thread pool.** Run PRKS work on a dedicated `anyio` capacity limiter, not the shared default of 40. It is sized from a new setting (proposed `PRKS_HTTP_THREADS`) and validated against `PRKS_DB_POOL_MAX` (default 10), so threads do not queue on the database pool. Three limits are specified separately, because they behave differently:
   - the PRKS thread limiter, which queues work waiting for a thread;
   - Uvicorn's `limit_concurrency`, which **immediately** answers 503 once open connections plus in-flight tasks reach the threshold, and counts idle keep-alive connections too, so it can refuse requests while PRKS threads and database slots are free;
   - the TCP `backlog`, which is only the kernel's accept queue.

   `limit_concurrency` stays unset, or far above the thread count, unless a test shows a reason to set it. Values are chosen only after real-socket tests cover saturated PRKS threads and many idle keep-alive connections.
3. **Parity.** Run the same HTTP test suites against both transports until the stdlib classes are deleted. That means `tests/pg/http_fixtures.py` and the 12 test modules that reference `PRKSHandler`, `PRKSThreadingTCPServer` or `run_server` today. Starlette's `TestClient` is not a substitute: tests go over real sockets.
4. **Uploads.** The Processing upload and backup stage read the body through the bridge, chunk by chunk, keeping their per-route limits. The 30 s idle timeout and the deadline become timeouts around each chunk wait, replacing `TimedBody`'s socket `settimeout`. Every other route reads its (bounded) JSON body before it enters the thread.
5. **Streaming responses.** NDJSON progress and PDF Range answers are sync generators wrapped in `StreamingResponse`. A client disconnect does **not** interrupt a blocking `next()` already running. Starlette advances a sync iterator with `anyio.to_thread.run_sync`, whose thread is not cancelled, so cleanup waits until that step returns. Backup progress can hold work while its generator runs, so each step must block for a bounded time only: progress waits poll with a short timeout and check a cancellation flag that the adapter sets on disconnect and on shutdown. The generator's `finally` releases what it holds. A real-socket test drops the client mid-stream and proves the generator's cleanup ran, its thread returned to the limiter, and graceful shutdown finished within the stop grace. Range parsing stays PRKS's `parse_byte_range`.
6. **Static files.** Served by the adapter with today's cache rules and SPA fallback, not by Starlette's `StaticFiles`, whose cache and fallback rules differ.
7. **Lifecycle.** `prks_app.py serve` runs `uvicorn.Server` in the same process, with `server_header=False`, a keep-alive timeout and `timeout_graceful_shutdown`. On stop it closes the listener, drains in-flight requests up to the existing stop grace, stops the embedded worker (now driven from the ASGI lifespan) and closes the pool. The root lease and start-generation bump are unchanged, because the process model is unchanged. Uvicorn's own signal handling is replaced by PRKS's `SignalStop` so that there is one stop path.
8. **Delete.** Remove `PRKSHandler`, `PRKSThreadingTCPServer` and the `http.server` import. Keep no fallback.

## Platforms

Linux (directly or in Docker Compose) is the main supported server platform; CI and the release build run there. Uvicorn and Starlette are pure Python with no platform-specific code path PRKS uses, so running from source on macOS or Windows needs no second server.

## Not decided here

- **Multiple web processes**: after #1331, either Uvicorn's own `--workers` or Gunicorn managing Uvicorn workers on Linux.
- **HTTP/2 and TLS termination**: these stay with the reverse proxy.
- **Exact pins.** Phase B adds `uvicorn`, `starlette` and their dependencies (`h11`, `click`, `anyio`, `idna`) to `requirements.txt`, `dependency-inventory.json`, `THIRD_PARTY_NOTICES.md` and the Pyright CI install step. It installs plain `uvicorn`, not `uvicorn[standard]`: uvloop and httptools are optional speedups PRKS does not need.
