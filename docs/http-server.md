# Production HTTP server (#1329 Phase A)

Status: **proposed**, waiting for the maintainer's review. This settles the
pre-1.0 question in `docs/postgresql-architecture.md` §4.5 (PG20, decision 9,
risk R9). The maintainer decided on 2026-11-19 to replace the stdlib server
rather than harden it behind a reverse proxy. This document picks the stack
and the process model. Phase B (the migration) starts only on the
maintainer's go.

## Decision

| Question | Answer |
| --- | --- |
| Interface | **WSGI** (PEP 3333). No ASGI. |
| Server | **Cheroot** (the CherryPy project's WSGI server), pinned like every other runtime dependency. |
| Framework | **None.** A thin PRKS-owned WSGI adapter keeps today's dispatch, admission, controllers and response helpers. No Flask, Starlette or Werkzeug. |
| Process model | **One web process with a bounded thread pool**, as today. Multiple web processes are out of scope until the PG13 residue below is gone. |
| Domain code | Stays synchronous (psycopg 3 sync API, PG3). Nothing is converted to `async`. |

## What the server has to carry

Everything below was read from master 02845f1.

- **Dispatch.** `backend/server.py` holds `PRKSHandler(http.server.SimpleHTTPRequestHandler)` and `PRKSThreadingTCPServer(ThreadingMixIn, TCPServer)`. The `do_*` methods run host validation, `/healthz` and `/readyz`, admission (`backend/api/session.py`), the Library access gate and then the route families in `backend/api/*.py`.
- **Controllers use a narrow handler surface.** Across `backend/api/*.py` the controllers call `handler.send_json` (194 times), `send_error` (28), `_prks_if_none_match`/`_send_json_not_modified` (11 each), `_prks_request_context` (7), and a handful of others: `headers`, `rfile`, `wfile`, `connection`, `send_response`/`send_header`/`end_headers`, `_send_pdf_stream`, `_send_person_profile_image_bytes` and `_send_not_ported`. A responder object with that surface lets every controller move unchanged first.
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
- **Process-local state that correctness still depends on (PG13 residue):**
  - `LibraryAccessGate` (`backend/concurrency.py`) still serializes mutations and makes restore exclusive inside one process;
  - the save-confirm tokens are still in-memory dictionaries (`backend/api/annotations.py`).

  §7.5 says both move to PostgreSQL. Until they do, two web processes would each have their own gate, and a restore in one would not fence writes in the other.
- **What the stdlib server lacks:**
  - socket timeouts (`StreamRequestHandler.timeout` is `None`, so an idle or half-sent request holds its thread forever);
  - a bound on thread count;
  - HTTP/1.1 keep-alive (`protocol_version` is the default HTTP/1.0);
  - a graceful drain of in-flight requests;
  - a way to drop `Server: SimpleHTTP/x Python/x.y` (#1306).

## Options compared

| Option | Fit for PRKS | Verdict |
| --- | --- | --- |
| **Cheroot** (WSGI, threaded, pure Python) | Streams `wsgi.input` to the app as it arrives, so 2 GiB and 64 GiB uploads keep their current semantics. Has a bounded thread pool, a socket timeout, request header and body limits, HTTP/1.1, and `stop()` that drains in-flight requests. Runs on Linux, macOS and Windows. Small dependency set (`jaraco.functools`, `more-itertools`). | **Chosen** |
| **Waitress** (WSGI, threaded, pure Python) | Zero dependencies, and handles slow clients best: its I/O loop buffers whole requests, so a slow client never holds a worker thread. But it buffers *every* request body before calling the app, so a 64 GiB restore archive would be spooled to the temp directory and then copied into the stage (twice the disk, outside the data root). `close()` does not drain in-flight requests. | Rejected: upload buffering |
| **Gunicorn** (`gthread`, one worker) | A mature prefork server, but Unix only. PRKS documents Windows and macOS paths and #46 plans a desktop build. Its main strength, many worker processes, is unusable until the PG13 residue is gone. | Rejected: no Windows |
| **Uvicorn + Starlette** (ASGI) | Good streaming and graceful shutdown. But every handler is synchronous, so each request would hop from the event loop into a thread pool. Reading a streamed upload from sync code needs `anyio.from_thread` bridging, and there is no built-in body size limit. It adds starlette, anyio, uvicorn, h11 and sniffio. Its benefits (WebSockets, many idle connections) are not 1.0 needs. | Rejected for 1.0; revisit for push sync |
| **Flask or Werkzeug** on any server | A framework would replace routing and parsing that PRKS already owns and tests. AGENTS.md: no framework ahead of real need. | Rejected |

## Spike evidence

Run on Python 3.13 with waitress 3.0.2 and cheroot 11.1.2. Each was a 30-line WSGI app on loopback, with 4 threads and a 10 MB body limit. These were throwaway scripts, not PRKS code.

| Check | Waitress | Cheroot |
| --- | --- | --- |
| NDJSON lines yielded 1 s apart | Delivered 1 s apart | Delivered 1 s apart |
| 3 MB body sent in three parts 1 s apart | App's `read()` took 0.002 s: body was buffered before the app ran | App read took 2.00 s: body streamed to the app as it arrived |
| Body over the limit | 413 | 413 before the body is read |
| Header over the limit | not checked | 413 |
| `Server` header | removed with `ident=''` | an app-set `Server` header replaces `Cheroot/x` |
| Normal request while 50 (waitress) or 8 (cheroot) clients hold half-sent headers | Served in 7 ms; idle clients dropped after `channel_timeout` | Waited 10 s: half-sent requests hold pool threads until the 5 s socket timeout |
| `stop()`/`close()` with a 2 s request in flight | Request finished, but the task thread raised `OSError: Bad file descriptor` | Request finished; `stop()` returned in 1.5 s |

**The cost of choosing Cheroot** is the slow-client row. It is bounded rather than open-ended as it is today, and it is acceptable because:
- the default bind is loopback;
- remote exposure already requires an HTTPS reverse proxy (README, `docs/wiki/Security-and-Operations.md`), and nginx and Caddy buffer slow clients themselves;
- the pool size and timeout will be configurable.

## Design for Phase B

1. **Adapter.** Add a WSGI application (proposed `backend/web/`) that builds one request object from `environ` and gives controllers a responder with today's handler surface. Admission, host and origin checks, the gate, request IDs, performance spans and the response headers move into the adapter once, with no per-route copies. Security headers (#1305 CSP and anti-framing, and #1306 `Server`) are added there.
2. **Parity.** Run the same HTTP test suites (`tests/pg/http_fixtures.py` and the 12 test modules that reference `PRKSHandler`, `PRKSThreadingTCPServer` or `run_server` today) against both transports until the stdlib classes are deleted.
3. **Uploads.** Cheroot's socket `timeout` is the idle timeout. The 30 s value, and the deadline checked between reads, replace `TimedBody`'s `settimeout` calls. The app keeps its own per-route limits (JSON 50 MiB, upload 2 GiB, stage 64 GiB). Cheroot's `max_request_body_size` is set to the largest of them as a backstop.
4. **Streaming.** Responses are generators. NDJSON progress yields per line, and PDF Range answers yield file chunks. A client disconnect surfaces as `GeneratorExit` instead of `BrokenPipeError`.
5. **Static files.** Served by the adapter with today's cache rules and SPA fallback. They are not left to Cheroot.
6. **Lifecycle.** `prks_app.py serve` starts Cheroot in the same process. The embedded worker's `serve()` drives `start()` and `stop()`. Shutdown stops accepting, drains in-flight requests up to the existing stop grace, stops the worker and then closes the pool. The root lease and start-generation bump are unchanged, because the process model is unchanged.
7. **Settings.** Add pool size and socket timeout settings (proposed `PRKS_HTTP_THREADS` and `PRKS_HTTP_TIMEOUT`), validated at startup. The thread count must not exceed what `PRKS_DB_POOL_MAX` (default 10) can serve without queueing every request; the default ties the two together.
8. **Delete.** Remove `PRKSHandler`, `PRKSThreadingTCPServer` and the `http.server` import. Keep no fallback.

## Not decided here

- **Multiple web processes.** First move the access gate and save-confirm tokens to PostgreSQL, as §7.5 already requires. That is a separate issue; #1329 does not need it.
- **HTTP/2 and TLS termination.** These stay with the reverse proxy.
- **Exact pins.** Phase B adds `cheroot` and its dependencies to `requirements.txt`, `dependency-inventory.json`, `THIRD_PARTY_NOTICES.md` and the Pyright CI install step.
