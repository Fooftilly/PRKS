# Production HTTP server (#1329)

Status: **accepted** (#1330, merged 2026-11-19); Phase B (the migration) is
done and Phase C (hardening) is in progress, see "Migration status". This settles the pre-1.0 question in
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

The threads share the web database pool, `PRKS_DB_POOL_MAX` (default 10). More
threads than connections is deliberate: many requests (the shell, scripts,
styles and other static files) never touch the database, and a request that
does waits briefly for a free connection. It fails only after
`pool_timeout_seconds` (10 s), when long requests hold every connection.
`/readyz` waits one second instead and answers 503 when no connection frees
up. If you raise `PRKS_HTTP_THREADS` a lot, raise `PRKS_DB_POOL_MAX` with it.

### Request layer (#1424)

Requests are answered by a small typed layer and a route table, still on
Uvicorn and with no framework. The `http.server`-shaped handler, its fake
`rfile`/`wfile` objects, the `do_*` methods and `handler_base.py` are gone.

- `backend/web/http.py`: `Request` (method, raw path, query, `Headers`,
  route `params`, the admitted `context`, the parsed JSON body) and
  `Response` (status, headers, a bytes body or a `stream(write)` callback,
  and a `cleanup` that runs once the answer is sent or abandoned), plus
  `json_response` (gzip, ETag and 304), `error_response` and friends.
- `backend/web/routing.py`: `Route(method, pattern, handler, body)` and the
  `RouteTable`. A pattern's `{name}` takes one path segment and
  `{name:path}` the rest of the path; literal segments win over
  parameters. A path no route takes is 404; a path whose routes take other
  methods is 405 with `Allow`. A route's body is JSON (the default for
  POST, PUT and PATCH), none, or a stream the route reads itself (backup
  staging, Processing uploads).
- `backend/web/app.py`: `App`, everything one server serves (storage root,
  Library, route table, bind and trusted hosts, identity, jobs, readiness,
  setup phase). It replaces the module globals of `server.py` and the
  `prks_*` attributes on the server object.
- `backend/web/dispatch.py`: the cross-cutting rules, once: method and NUL
  checks, the Host check, the probes and static files, admission
  (`backend/api/session.py`, `backend/api/setup.py`), the JSON body, the
  answer to an exception a route raised (refusals, `NotPortedError` as 501,
  otherwise a 500 logged by type and route only), and the headers every
  answer carries (security headers and CSP, HSTS over TLS, `X-Request-ID`,
  `Server-Timing`, the Library epoch).
- `backend/web/asgi.py`: the Uvicorn adapter (bounded thread pool and wait
  list, head limits and deadline, disconnects, TLS) builds a `Request` and
  sends the `Response`.
- `backend/web/hosts.py` (Host and Origin parsing) and `backend/web/static.py`
  (the frontend files) hold what `server.py` held for them.
- `backend/api/<family>.py`: each family's `ROUTES`; `backend/api/__init__.py`
  collects them. Handlers are `(Request) -> Response` and import their domain
  commands inside the handler. Thumbnails, PDF bytes, backups, sync posting
  and the YouTube oEmbed lookup moved here from `server.py`.
- `backend/server.py` only binds the storage root and Library into an `App`
  and starts or stops the server (`run_server`, `start_setup_server`).

**JSON bodies.** The dispatcher reads a JSON route's body once, before the
route runs. A body that is not a JSON object answers
`400 {"error": "JSON object body required", "code": "invalid_request"}`;
text that is not JSON (including `NaN`, `Infinity`, numbers past the float
range and lone surrogates) answers `400 {"error": "invalid_json", "code":
"invalid_request"}`; a missing or other media type is 415 and a body past the
limit 413, as before. No body is `{}`. `tests/pg/test_http_route_table.py`
walks the route table and sends each JSON route these bodies, so a new route
is covered without being listed.

This replaced per-route body handling on purpose (#1424). Routes that took a
non-object body now refuse it: the bare-array form of `PUT
/api/arguments/{id}/sources|targets` and `PUT /api/concepts/{id}/parents|aliases`
(send `{"sources": [...]}` and so on; the app writes these through sync),
the ignored body of `POST /api/processing-items/{id}/import` (send `{}`), and
the Processing PATCH and batch import, which answered `VALIDATION_ERROR`
for a non-object body. A known path asked with a method it does not serve is
405 with `Allow`, where some families answered 404.

**Paths.** The route table sees the path as sent: `;params` are no longer
stripped, so `;x` stays part of the segment. `/api/pdfs/<name>;x` names
another file (404), and a frontend path such as `/index.html;x` falls back to
the app shell. A path segment is percent-decoded by the route that reads it,
once. The most specific pattern that takes a path owns it: a wrong method on
a literal endpoint such as `/api/works/bulk` is 405 with that endpoint's own
`Allow`, never a fall-through to `/api/works/{work_id}`.

Only regular files beneath the frontend directory are served, each resolved
with its symlinks (the shell too): `/` and unknown paths get `index.html`,
any other directory answers 404, and directory listings are not served.

#1339 adds PRKS's own TLS on this server: `PRKS_TLS_CERT_FILE` and
`PRKS_TLS_KEY_FILE` (`backend.web.settings`, loaded once at start) are passed
to Uvicorn, and a request served over TLS gets `Secure` cookies, HSTS and an
`https` expected origin. See [docs/https.md](https.md).

**No Starlette** (maintainer decision, 2026-11-20). B1 to B3 wrote the parts
Starlette was meant to supply directly on plain ASGI (`backend/web/asgi.py`):
streamed bodies, disconnects, the bounded queue, head limits, TLS and static
files. Every cross-cutting check already runs once, in the dispatcher, so
Starlette would now replace little of PRKS's code for a new runtime dependency. The design steps below that name
Starlette are superseded on that point; the behaviour they describe holds.

Phase C so far:

- **Request-head deadline.** Uvicorn has no limit on how long a client takes
  to send a request head: a connection with a half-sent head, or with no bytes
  at all, stayed open for good. A client now has `REQUEST_HEAD_SECONDS` (15 s)
  from the connection's start (over TLS, from the end of the handshake,
  which asyncio bounds at 60 s on its own), and again from the end of each
  response, to send a whole head; trickling bytes does not extend it. Idle keep-alive still
  closes after 5 s.
- **`TCP_NODELAY`.** asyncio sets it only on sockets whose `proto` is
  `IPPROTO_TCP`, and an accepted socket reports 0. Without it a response's
  head and body left as two segments, and on a keep-alive connection, which
  browsers use, every request waited about 40 ms for the client's delayed
  ACK. It is now set on each accepted connection.
- **Reused request threads.** Request work runs on reused daemon threads
  instead of a new thread per request; `PRKS_HTTP_THREADS` still bounds it.
- **Fewer event-loop crossings.** A response's head is sent with its first
  body chunk, and the newest chunk with the end of the body (a stream's
  `flush()` sends at once), so a typical response crosses from its thread to
  the event loop once instead of four times.
- **Graceful stop** is tested over real sockets: a request in flight
  finishes before the stop returns, and a stop never waits past its grace for
  one that outlives it. The embedded worker's stop is tested in
  `tests/pg/test_embedded_worker.py`. The web pool and the data root's shared
  lease end with the process, as before.

### Baseline (2026-11-20)

Measured on one 4-CPU Linux container with Python 3.13. The server ran
`make_server` with 16 threads, and the client ran as a separate process, using
1 or 16 threads for 5 s per row, with no database. `/api/tags` is the 503
admission answer without `PRKS_AUTH`. The old server is the stdlib transport at
9ddeb4c, the last commit that had it. Numbers are requests per second, then
p50 latency in ms. These are informational only, with no budget attached, and
#765 owns real workload benchmarks.

| Route, clients | stdlib, new connection | Uvicorn, new connection | Uvicorn, keep-alive |
| --- | --- | --- | --- |
| `/healthz`, 1 | 1342, 0.73 | 1271, 0.75 | 1511, 0.63 |
| `/healthz`, 16 | 1870, 3.14 | 1123, 13.5 | 1676, 9.06 |
| `/` (static), 1 | 1115, 0.88 | 829, 1.15 | 1128, 0.87 |
| `/` (static), 16 | 959, 7.82 | 869, 18.3 | 961, 16.5 |
| `/api/tags`, 1 | 1231, 0.78 | 966, 1.01 | 1338, 0.72 |
| `/api/tags`, 16 | 1577, 3.73 | 1199, 13.2 | 1507, 10.2 |

Before the changes above, the Uvicorn transport managed about half the
stdlib's rate on new connections (for example 668 requests per second on
`/healthz` with one client), and about 23 requests per second on a keep-alive
connection, because of the delayed ACK. The stdlib server spoke HTTP/1.0
and closed every connection. At 16 clients Uvicorn's latency is higher,
because one event loop parses every request where the stdlib ran a thread per
connection. That is ample for one person's library, and it is the price of
keeping slow clients off the request threads.

## Decision

| Question | Answer |
| --- | --- |
| Interface | **ASGI** at the edge only. |
| Server | **Uvicorn**, pinned like every other runtime dependency. |
| Framework | **None** (changed 2026-11-20, see "Migration status"). It was **Starlette** as a thin request/response layer; PRKS's own plain ASGI adapter took that role. |
| Domain code | Stays **synchronous** (psycopg 3 sync API, PG3). Each request's PRKS work runs in a bounded worker thread pool. Nothing in `backend/domain/` becomes `async`. |
| Process model | **One web process** by default. Since #1331 nothing a request depends on lives in process memory, and more web processes may serve one Library side by side: each holds the root's `root.lock` shared, and `open.lock` only makes their starts take turns. A brand-new root is bound by the first web process; start more once it is ready. Compose and `serve` still start one. |

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

Everything below was read from master 02845f1, before the request layer
(#1424) replaced the handler; it is kept as the design record.

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
  - the security headers (`X-Content-Type-Options`, `X-Frame-Options`, `Content-Security-Policy`, `Referrer-Policy`; #1305);
  - static cache headers;
  - closing the connection after an unread body.
- **Streaming responses:**
  - NDJSON backup progress (`POST /api/backups/progress`, a write and flush per line);
  - PDF bytes with RFC 9110 Range (`backend.web.byte_range.parse_byte_range`, 206 and 416);
  - backup download.
- **Streaming request bodies:**
  - Processing upload (`POST /api/processing-items`, up to 2 GiB);
  - backup stage (`POST /api/backups/stage`, up to 64 GiB by default).

  Both read the socket directly through `processing_api.TimedBody`, with a 30 s idle timeout and a deadline. A stalled client still holds its request thread until one of them ends the read. On Uvicorn, enough stalled uploads can fill the bounded `PRKS_HTTP_THREADS` pool for that long; the timeouts bound the hold, they do not prevent it.
- **Lifecycle.** `run_server` binds the server. The embedded job worker (`backend/jobs/embedded.py`) serves the server and stops it gracefully on SIGTERM or SIGINT. The web process holds the data root's lease shared and bumps the start generation once per process start (§7.5, §11.1).
- **Process-local state**: none that correctness depends on. #1331 deleted the last of it, the `LibraryAccessGate` and the save-confirm tokens (§7.5).
- **What the stdlib server lacks:**
  - socket timeouts (`StreamRequestHandler.timeout` is `None`, so an idle or half-sent request holds its thread forever);
  - a bound on thread count;
  - HTTP/1.1 keep-alive (`protocol_version` is the default HTTP/1.0);
  - a graceful drain of in-flight requests;
  - a way to drop `Server: SimpleHTTP/x Python/x.y` (#1306).

## Options compared

| Option | Fit for PRKS | Verdict |
| --- | --- | --- |
| **Uvicorn + Starlette** (ASGI) | Most used and most active (see above). The event loop owns the sockets, so slow clients hold no worker thread. Bodies stream to the app as they arrive. Graceful shutdown drains in-flight requests. Runs on Linux, macOS and Windows. The cost is an async edge: sync PRKS code runs in a thread pool, and the two streamed-upload routes pull chunks through a small `anyio.from_thread` bridge (spiked below). Starlette has no global body limit, so PRKS keeps enforcing its own per-route limits, as it does today. | **Chosen** (Uvicorn; Starlette dropped 2026-11-20) |
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
8. **Delete.** Remove `PRKSThreadingTCPServer` and the `http.server` import, and move `PRKSHandler` (which stays the request dispatcher) onto a PRKS-owned base class. Keep no fallback.

## Platforms

Linux (directly or in Docker Compose) is the main supported server platform; CI and the release build run there. Uvicorn and Starlette are pure Python with no platform-specific code path PRKS uses, so running from source on macOS or Windows needs no second server.

## Not decided here

- **How to run multiple web processes**: they are safe side by side since #1331 and its follow-up, but no launcher starts several yet. The options are Uvicorn's own `--workers` or Gunicorn managing Uvicorn workers on Linux; both need each worker to run PRKS's own start (storage open and start check), which is a later #1329 or deployment decision.
- **HTTP/2**: stays with the reverse proxy. TLS termination was decided later (#1339, 2026-11-20): PRKS can serve HTTPS itself (`PRKS_TLS_CERT_FILE`, `PRKS_TLS_KEY_FILE`, [docs/https.md](https.md)), and a reverse proxy such as Caddy stays the recommended path for internet exposure.
- **Exact pins.** Phase B added `uvicorn` and its dependencies (`h11`, `click`); `starlette`, `anyio` and `idna` are not needed after the 2026-11-20 decision. They go to `requirements.txt`, `dependency-inventory.json`, `THIRD_PARTY_NOTICES.md` and the Pyright CI install step. It installs plain `uvicorn`, not `uvicorn[standard]`: uvloop and httptools are optional speedups PRKS does not need.
