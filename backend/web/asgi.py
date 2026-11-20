"""Serve PRKS under Uvicorn: ``PRKSHandler`` dispatch over ASGI (#1329).

Uvicorn owns the
sockets, HTTP parsing, keep-alive and slow clients on its event loop. Each
request runs on a reused daemon thread, bounded by ``PRKS_HTTP_THREADS``, where
an :class:`_AsgiHandler` (a ``PRKSHandler`` built from the ASGI scope) runs
the ``do_*`` dispatch, admission, controllers and static files:

- ``rfile`` pulls the ASGI request body chunk by chunk. A read waits at most
  the connection's timeout (``settimeout``, which ``TimedBody`` sets for
  uploads) or :data:`DEFAULT_BODY_IDLE_SECONDS`, then raises ``TimeoutError``.
- ``wfile`` takes the handler's raw HTTP/1 response bytes, turns the status
  line and headers into ``http.response.start`` and streams the rest as body
  chunks. ``Server`` is dropped (#1306); Uvicorn adds ``Date``.
- A client that is gone surfaces as ``BrokenPipeError``, as it did on a
  socket. Uvicorn's ``send`` returns silently after a disconnect, so a
  :class:`_Receiver` (the only caller of ``receive``) watches for
  ``http.disconnect`` and the writer refuses to send once it has come. A
  response-only stream (backup progress) therefore ends on its next write.
- Uvicorn's TRACE logging (connection lines and the ASGI message logger,
  which prints paths and query strings) is kept off whatever
  ``PRKS_LOG_LEVEL`` is, because lowering the level must never unlock raw
  data.

Signals stay with PRKS (``backend.jobs.embedded.SignalStop``): Uvicorn's own
handlers are not installed. ``proxy_headers`` is off, because PRKS applies its
own ``PRKS_TRUSTED_PROXIES`` rules to the peer address.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import logging
import queue
import socket
import threading
import uuid
from types import TracebackType
from typing import Any, Awaitable, Callable, Iterator, Optional

import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol

from backend import server as server_module
from backend.log_safety import safe_error_type, safe_log_id

LOGGER = logging.getLogger(__name__)

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

#: How long one body read waits for the client when the route set no timeout.
DEFAULT_BODY_IDLE_SECONDS = 60.0
#: How long a stop waits for requests in flight before cancelling them.
GRACEFUL_SHUTDOWN_SECONDS = 30
#: Seconds an idle keep-alive connection stays open.
KEEP_ALIVE_SECONDS = 5
#: Seconds a client has to send a whole request head: from the connection's
#: start (after the TLS handshake) and from the end of each response. Uvicorn
#: has no such limit, so a client could hold a connection forever with a
#: half-sent head, or with no bytes at all (see :class:`_HeadTimeoutH11`).
REQUEST_HEAD_SECONDS = 15
#: Request limits: header count and line length.
MAX_HEADERS = 100
MAX_LINE_BYTES = 65536
#: The largest request head (request line, headers and their line breaks,
#: measured as ``name: value``) PRKS accepts; a larger one is answered 431.
#: The adapter enforces it on the parsed head, so the answer does not depend on
#: how the bytes arrived.
MAX_REQUEST_HEAD_BYTES = 65536
#: h11's buffer for a head still arriving. It is twice the limit above: h11
#: checks only incomplete data, so a head just over the limit must still parse
#: (whether sent at once or in pieces) for the adapter to answer 431. Past this,
#: h11 answers 400 itself, again for either sending pattern, since Uvicorn
#: reads at most 64 KiB at a time. h11's default (16 KiB) refused fragmented
#: heads that passed when sent at once.
H11_HEAD_BUFFER_BYTES = 2 * MAX_REQUEST_HEAD_BYTES
#: Requests that may wait for a PRKS thread, per thread. Past that the
#: server answers 503 at once, so a flood cannot grow the wait list without
#: bound (``limit_concurrency`` stays unset: it also counts idle keep-alive
#: connections, ``docs/http-server.md``).
QUEUE_PER_THREAD = 8
#: Request body a request waiting for a thread may have buffered while the
#: receiver keeps watching for a disconnect (see :class:`_Receiver`).
QUEUED_BODY_BYTES = 65536
_BUSY_BODY = b'{"error":"server_busy"}'

_DROPPED_RESPONSE_HEADERS = frozenset({b"server", b"date"})
#: Uvicorn loggers that log request data at TRACE (below DEBUG).
_UVICORN_TRACE_LOGGERS = ("uvicorn.error", "uvicorn.asgi")


def _keep_uvicorn_trace_off() -> None:
    """Hold Uvicorn's loggers at DEBUG or above, so TRACE never logs."""
    for name in _UVICORN_TRACE_LOGGERS:
        logger = logging.getLogger(name)
        logger.setLevel(max(logger.getEffectiveLevel(), logging.DEBUG))


class _Bridge:
    """Run a coroutine on the server's event loop from a request thread."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def call(self, make: Callable[[], Awaitable[Any]], timeout: Optional[float] = None) -> Any:
        async def run() -> Any:
            return await make()

        try:
            future = asyncio.run_coroutine_threadsafe(run(), self._loop)
        except RuntimeError as exc:  # the loop has closed
            raise BrokenPipeError("server stopped") from exc
        try:
            return future.result(timeout)
        except TimeoutError:
            future.cancel()
            raise


class _Connection:
    """The socket stand-in: only the read timeout the upload routes set."""

    def __init__(self) -> None:
        self._timeout: Optional[float] = None

    def settimeout(self, value: Optional[float]) -> None:
        self._timeout = value

    def gettimeout(self) -> Optional[float]:
        return self._timeout


class _Receiver:
    """The one consumer of ASGI ``receive`` for a request, on the event loop.

    Body messages pass through a one-slot queue, so Uvicorn's flow control
    still pauses a client the handler is not reading. After the body it keeps
    waiting, and ``gone`` is set once the client disconnects (or the request
    task ends, as on a forced shutdown).

    While the request still waits for a thread, body messages that arrive
    behind a full queue are merged into the queued one, up to
    :data:`QUEUED_BODY_BYTES`, so the receiver keeps reading and sees a client
    that leaves (Uvicorn reports that only through a later ``receive()``).
    Past that bound it waits as above; such a request then ends at its first
    body read once it runs.

    Once the response starts, body the handler left unread is dropped rather
    than queued (:meth:`discard_body`). Otherwise a full queue would stop the
    receiver from ever seeing ``http.disconnect``: a GET carrying a body that
    its handler never reads would keep streaming to a client that has gone.
    """

    def __init__(self, receive: Receive) -> None:
        self._receive = receive
        self.messages: asyncio.Queue[Message] = asyncio.Queue(maxsize=1)
        self.gone = threading.Event()
        #: Set on ``http.disconnect`` only (not when the task merely ends), for
        #: code on the loop: a request still waiting for a thread is dropped.
        self.disconnected = asyncio.Event()
        self._discard = asyncio.Event()
        self._serving = False

    def start_serving(self) -> None:
        """The request has a thread: hand body over one message at a time."""
        self._serving = True

    async def run(self) -> None:
        try:
            while True:
                message = await self._receive()
                if message.get("type") != "http.request":
                    self.gone.set()
                    self.disconnected.set()
                    if not self._discard.is_set():
                        await self._put(message)
                    return
                if not self._discard.is_set():
                    await self._put(message)
        finally:
            self.gone.set()

    async def _put(self, message: Message) -> None:
        """Queue ``message``, or drop it if :meth:`discard_body` runs first."""
        if not self._serving and self._merged(message):
            return
        put = asyncio.ensure_future(self.messages.put(message))
        discard = asyncio.ensure_future(self._discard.wait())
        try:
            await asyncio.wait({put, discard}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            put.cancel()
            discard.cancel()

    def _merged(self, message: Message) -> bool:
        """Append ``message``'s body to the queued one, within the bound."""
        if not self.messages.full() or message.get("type") != "http.request":
            return False
        held = self.messages.get_nowait()
        body = held.get("body", b"") + message.get("body", b"")
        if held.get("type") != "http.request" or len(body) > QUEUED_BODY_BYTES:
            self.messages.put_nowait(held)
            return False
        self.messages.put_nowait(
            {"type": "http.request", "body": body, "more_body": message.get("more_body", False)}
        )
        return True

    async def discard_body(self) -> None:
        """The response has started: drop request body the handler did not read."""
        self._discard.set()


class _RequestBody:
    """The request body as the file object ``rfile`` the handler reads."""

    def __init__(self, bridge: _Bridge, receiver: _Receiver, connection: _Connection) -> None:
        self._bridge = bridge
        self._receiver = receiver
        self._connection = connection
        self._buffer = bytearray()
        self._eof = False

    def _fill(self) -> bool:
        """Append one ASGI body message; False once the body has ended."""
        if self._eof:
            return False
        timeout = self._connection.gettimeout()
        message = self._bridge.call(
            self._receiver.messages.get, DEFAULT_BODY_IDLE_SECONDS if timeout is None else timeout
        )
        if message.get("type") != "http.request":
            self._eof = True  # http.disconnect: the client is gone
            return False
        self._buffer += message.get("body", b"")
        if not message.get("more_body", False):
            self._eof = True
        return True

    def _take(self, size: int) -> bytes:
        chunk = bytes(self._buffer[:size])
        del self._buffer[:size]
        return chunk

    def read(self, size: Optional[int] = -1) -> bytes:
        if size is None or size < 0:
            while self._fill():
                pass
            return self._take(len(self._buffer))
        while len(self._buffer) < size and self._fill():
            pass
        return self._take(size)

    def read1(self, size: int = -1) -> bytes:
        while not self._buffer and self._fill():
            pass
        return self._take(len(self._buffer) if size is None or size < 0 else size)

    def readline(self, size: int = -1) -> bytes:
        while b"\n" not in self._buffer and self._fill():
            pass
        end = self._buffer.find(b"\n")
        length = len(self._buffer) if end < 0 else end + 1
        if size is not None and size >= 0:
            length = min(length, size)
        return self._take(length)


class _ResponseWriter:
    """``wfile``: the handler's raw HTTP/1 response, sent as ASGI messages."""

    def __init__(self, bridge: _Bridge, send: Send, receiver: _Receiver) -> None:
        self._bridge = bridge
        self._send_message = send
        self._receiver = receiver
        self._gone = receiver.gone
        self._head = bytearray()
        self._pending_start: Optional[Message] = None
        self._held: Optional[bytes] = None
        self.started = False

    def write(self, data: bytes | bytearray | memoryview) -> int:
        chunk = bytes(data)
        if self.started:
            if chunk:
                self._body(chunk)
            return len(chunk)
        self._head += chunk
        end = self._head.find(b"\r\n\r\n")
        if end >= 0:
            head = bytes(self._head[:end])
            rest = bytes(self._head[end + 4:])
            self._head.clear()
            self._start(head)
            if rest:
                self._body(rest)
        return len(chunk)

    def flush(self) -> None:
        """Send what is held now (a stream flushes each line)."""
        if self._pending_start is not None or self._held is not None:
            self._send(self._take_held(more=True))

    def finish(self) -> None:
        """End the body. Without a response, Uvicorn answers 500 itself."""
        if self.started:
            held = self._take_held(more=False)
            self._send(held or {"type": "http.response.body", "body": b"", "more_body": False})

    def _start(self, head: bytes) -> None:
        lines = head.split(b"\r\n")
        status = int(lines[0].split(b" ", 2)[1])
        headers: list[tuple[bytes, bytes]] = []
        for line in lines[1:]:
            name, _, value = line.partition(b":")
            key = name.strip().lower()
            if key and key not in _DROPPED_RESPONSE_HEADERS:
                headers.append((key, value.strip()))
        # Held until the first body chunk, flush() or the end, and sent with
        # it: each crossing to the event loop costs a round trip, and most
        # responses are a head and one body write (docs/http-server.md).
        self._pending_start = {"type": "http.response.start", "status": status, "headers": headers}
        self.started = True

    def _body(self, data: bytes) -> None:
        # The newest chunk is held back until the next write, flush() or the
        # end, so a head and a one-write body leave in one crossing with the
        # end of the body. Earlier chunks go out as each new one arrives.
        previous = self._take_held(more=True)
        self._held = data
        if previous is not None:
            self._send(previous)

    def _take_held(self, *, more: bool) -> Optional[Message]:
        held, self._held = self._held, None
        if held is None:
            return None
        return {"type": "http.response.body", "body": held, "more_body": more}

    def _send(self, message: Optional[Message] = None) -> None:
        if self._gone.is_set():  # Uvicorn would drop the bytes without an error
            raise BrokenPipeError("client disconnected")
        messages: list[Message] = []
        start = self._pending_start
        if start is not None:
            self._pending_start = None
            messages.append(start)
        if message is not None:
            messages.append(message)

        async def deliver() -> None:
            if start is not None:
                await self._receiver.discard_body()
            for each in messages:
                await self._send_message(each)

        try:
            self._bridge.call(deliver)
        except OSError as exc:  # uvicorn's ClientDisconnected is an OSError
            raise BrokenPipeError("client disconnected") from exc


def _headers(scope: Scope) -> http.client.HTTPMessage:
    message = http.client.HTTPMessage()
    for name, value in scope.get("headers", []):
        message[name.decode("latin-1")] = value.decode("latin-1")
    return message


def _target(scope: Scope) -> str:
    raw = scope.get("raw_path") or scope.get("path", "/").encode("utf-8")
    target = raw.decode("latin-1")
    query = scope.get("query_string", b"")
    return f"{target}?{query.decode('latin-1')}" if query else target


class _AsgiHandler(server_module.PRKSHandler):
    """A ``PRKSHandler`` for one ASGI request: same dispatch, no socket."""

    def __init__(
        self,
        scope: Scope,
        server: "UvicornServing",
        rfile: _RequestBody,
        wfile: _ResponseWriter,
        connection: _Connection,
    ) -> None:
        super().__init__()
        self._prks_request_id = uuid.uuid4().hex[:12]
        self.server = server
        client = scope.get("client") or ("", 0)
        self.client_address = (str(client[0]), int(client[1]))
        self.command = str(scope.get("method", "GET")).upper()
        self.path = _target(scope)
        # "https" when Uvicorn served this request over TLS (#1339).
        self._prks_scheme = "https" if scope.get("scheme") == "https" else "http"
        self.request_version = f"HTTP/{scope.get('http_version', '1.1')}"
        self.requestline = f"{self.command} {self.path} {self.request_version}"
        self.raw_requestline = self.requestline.encode("latin-1", "replace") + b"\r\n"
        self.close_connection = True
        self.headers = _headers(scope)
        self.rfile = rfile
        self.wfile = wfile
        self.connection = connection
        self.request = connection
        self._prks_unread_body = False

    def run(self) -> None:
        """Check the request head, then dispatch to ``do_<METHOD>``."""
        try:
            head_bytes = len(self.raw_requestline) + 2 + sum(
                len(name) + len(value) + 4 for name, value in self.headers.items()
            )
            if len(self.headers) > MAX_HEADERS or head_bytes > MAX_REQUEST_HEAD_BYTES or any(
                len(name) + len(value) + 4 > MAX_LINE_BYTES for name, value in self.headers.items()
            ):
                self.send_error(431, "Request header fields too large")
                return
            if len(self.raw_requestline) > MAX_LINE_BYTES:
                self.send_error(414, "Request-URI Too Long")
                return
            if not self._prks_after_parse():
                return
            method = getattr(self, "do_" + self.command, None)
            if method is None:
                self.send_error(501, "Unsupported method")
                return
            method()
        finally:
            self._prks_finish_request()


class _Workers:
    """Reused daemon threads that run PRKS request work.

    Starting a thread per request cost about a third of a millisecond of
    every request (``docs/http-server.md``, baseline). The threads are daemons,
    like the per-request threads were, so a request stuck past the stop grace
    never holds the process open. The ``PRKS_HTTP_THREADS`` slots bound the
    work; a new thread starts only when no thread is idle, which after the
    first requests happens only if a request's task was cancelled while its
    work still runs. :meth:`close` (when serving ends) retires the idle
    threads at once and each busy one when its request returns, so servers
    started and stopped in one process leave no threads behind.
    """

    def __init__(self) -> None:
        self._jobs: "queue.SimpleQueue[Optional[Callable[[], None]]]" = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._idle = 0
        self._closed = False

    def submit(self, job: Callable[[], None]) -> None:
        with self._lock:
            start = self._closed or self._idle == 0
            if not start:
                self._idle -= 1  # that idle thread takes this job
        if start:
            # A job after close() (none is expected) still runs, on a thread
            # that then ends.
            threading.Thread(target=self._loop, args=(job,), name="prks-http", daemon=True).start()
        else:
            self._jobs.put(job)

    def close(self) -> None:
        """Retire every thread: idle ones now, busy ones after their request."""
        with self._lock:
            self._closed = True
            idle, self._idle = self._idle, 0
        for _ in range(idle):
            self._jobs.put(None)

    def _loop(self, job: Optional[Callable[[], None]]) -> None:
        while job is not None:
            try:
                job()
            except Exception as exc:  # noqa: BLE001 - _run reports its own errors; keep the thread
                # Metadata only, like _run: no traceback or message in the log.
                LOGGER.error("http_worker_job_failed error_type=%s", safe_error_type(exc))
            with self._lock:
                if self._closed:
                    return
                self._idle += 1
            job = self._jobs.get()


class PrksAsgiApp:
    """The ASGI application: request work on bounded, reused threads."""

    def __init__(self, server: "UvicornServing", threads: int) -> None:
        self._server = server
        self._threads = threads
        # Requests running or waiting for a thread. Counted synchronously
        # before any await, so a burst cannot slip past the bound while slot
        # acquisitions are still pending.
        self._max_admitted = threads + threads * QUEUE_PER_THREAD
        self._admitted = 0
        self._slots: Optional[asyncio.Semaphore] = None
        self._workers = _Workers()

    def close(self) -> None:
        """Serving has ended: retire the request threads."""
        self._workers.close()

    @property
    def admitted(self) -> int:
        """Requests running or waiting for a thread."""
        return self._admitted

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            return  # lifespan is off; no WebSocket routes
        if self._slots is None:
            self._slots = asyncio.Semaphore(self._threads)
        if self._admitted >= self._max_admitted:
            await _send_busy(send, https=scope.get("scheme") == "https")
            return
        self._admitted += 1
        # The receiver starts before the wait for a thread, so a client that
        # leaves while queued is seen (Uvicorn reports it only via receive())
        # and its request is dropped instead of run for nobody. Body messages
        # wait in the receiver's queue meanwhile; none is lost.
        receiver = _Receiver(receive)
        watch = asyncio.ensure_future(receiver.run())
        try:
            acquired = await self._acquire_unless_disconnected(receiver)
            if not acquired:
                return
            receiver.start_serving()
            try:
                await self._serve(scope, receiver, send)
            finally:
                self._slots.release()
        finally:
            self._admitted -= 1
            watch.cancel()
            receiver.gone.set()

    async def _acquire_unless_disconnected(self, receiver: _Receiver) -> bool:
        """Take a thread slot; False (holding none) if the client leaves first."""
        assert self._slots is not None
        acquire = asyncio.ensure_future(self._slots.acquire())
        left = asyncio.ensure_future(receiver.disconnected.wait())
        acquired = False
        try:
            await asyncio.wait({acquire, left}, return_when=asyncio.FIRST_COMPLETED)
            if not acquire.done():
                acquire.cancel()
            # Settle the acquisition without awaiting its result, so a
            # cancellation of this task itself is never caught here.
            await asyncio.wait({acquire})
            acquired = not acquire.cancelled() and acquire.exception() is None
        finally:
            left.cancel()
            if not acquire.done():
                acquire.cancel()  # Semaphore.acquire hands back a slot it was given
            elif acquired and receiver.disconnected.is_set():
                self._slots.release()  # acquired as the client left: run nothing
                acquired = False
            elif not acquired and not acquire.cancelled() and acquire.exception() is None:
                self._slots.release()  # this task was cancelled after the slot was taken
        return acquired

    async def _serve(self, scope: Scope, receiver: _Receiver, send: Send) -> None:
        loop = asyncio.get_running_loop()
        done: asyncio.Future[None] = loop.create_future()
        self._workers.submit(lambda: self._run(loop, done, scope, receiver, send))
        await done

    def _run(
        self,
        loop: asyncio.AbstractEventLoop,
        done: "asyncio.Future[None]",
        scope: Scope,
        receiver: _Receiver,
        send: Send,
    ) -> None:
        bridge = _Bridge(loop)
        connection = _Connection()
        writer = _ResponseWriter(bridge, send, receiver)
        handler: Optional[_AsgiHandler] = None
        try:
            handler = _AsgiHandler(scope, self._server, _RequestBody(bridge, receiver, connection), writer, connection)
            handler.run()
            writer.finish()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # logged metadata-only; the connection closes
            LOGGER.error(
                "http_request_failed error_type=%s request_id=%s",
                safe_error_type(exc),
                safe_log_id(handler._prks_request_id if handler is not None else ""),
            )
        finally:
            with contextlib.suppress(RuntimeError):  # the loop has closed
                loop.call_soon_threadsafe(_resolve, done)


async def _send_busy(send: Send, *, https: bool) -> None:
    """503 for a request over the wait-list bound, without starting a thread.

    Over PRKS's own TLS it carries HSTS too, like every ``PRKSHandler`` answer.
    """
    LOGGER.info("request_rejected reason=server_busy")
    headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(_BUSY_BODY)).encode("ascii")),
        (b"cache-control", b"no-store"),
        (b"retry-after", b"1"),
        (b"x-content-type-options", b"nosniff"),
        (b"connection", b"close"),
    ]
    if https:
        headers.append((b"strict-transport-security", server_module.HSTS_VALUE.encode("ascii")))
    await send({"type": "http.response.start", "status": 503, "headers": headers})
    await send({"type": "http.response.body", "body": _BUSY_BODY, "more_body": False})


def _resolve(done: "asyncio.Future[None]") -> None:
    if not done.done():
        done.set_result(None)


class _HeadTimeoutH11(H11Protocol):
    """Uvicorn's h11 protocol, closing a connection whose request head is late.

    The deadline (:data:`REQUEST_HEAD_SECONDS`) starts when the connection is
    made and again when each response completes, and ends when a whole head
    has arrived. Bytes that arrive in between do not extend it, so trickling a
    head byte by byte does not help. Keep-alive is unchanged: an idle
    connection still closes after :data:`KEEP_ALIVE_SECONDS`.
    """

    _head_deadline: Optional[asyncio.TimerHandle] = None

    def connection_made(self, transport: asyncio.Transport) -> None:  # type: ignore[override]
        super().connection_made(transport)
        # asyncio sets TCP_NODELAY only on sockets whose ``proto`` is
        # IPPROTO_TCP, and an accepted socket reports 0. Without it, the
        # response head and body leave as two segments, and on a keep-alive
        # connection the second waits for the client's delayed ACK: about
        # 40 ms per request (docs/http-server.md, baseline).
        sock = transport.get_extra_info("socket")
        if sock is not None and sock.family in (socket.AF_INET, socket.AF_INET6):
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._arm_head_deadline()

    def connection_lost(self, exc: Optional[Exception]) -> None:
        self._cancel_head_deadline()
        super().connection_lost(exc)

    def handle_events(self) -> None:
        cycle = self.cycle
        super().handle_events()
        if self.cycle is not cycle:  # a whole head arrived: a new request
            self._cancel_head_deadline()

    def on_response_complete(self) -> None:
        # Armed first: a pipelined head already buffered is handled inside
        # super(), and its handle_events cancels the deadline again.
        if not self.transport.is_closing():
            self._arm_head_deadline()
        super().on_response_complete()

    def _arm_head_deadline(self) -> None:
        self._cancel_head_deadline()
        self._head_deadline = self.loop.call_later(REQUEST_HEAD_SECONDS, self._head_late)

    def _cancel_head_deadline(self) -> None:
        if self._head_deadline is not None:
            self._head_deadline.cancel()
            self._head_deadline = None

    def _head_late(self) -> None:
        self._head_deadline = None
        if not self.transport.is_closing():
            self.transport.close()  # connection_lost records it with h11


class _Server(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        # PRKS's SignalStop owns SIGTERM/SIGINT and calls shutdown().
        yield


class UvicornServing:
    """``backend.jobs.embedded.Serving`` over Uvicorn, with the ``prks_*`` attributes.

    The listening socket is bound here, so a bind failure surfaces before
    serving, as ``TCPServer`` did, and ``server_address`` is known at once.
    """

    prks_bind_host: str
    prks_trusted_hosts: frozenset[str]
    prks_access_gate: Any
    prks_auth: Any
    prks_jobs: Any
    prks_readiness: Any

    def __init__(
        self,
        address: tuple[str, int],
        *,
        threads: int,
        tls_cert_file: Optional[str] = None,
        tls_key_file: Optional[str] = None,
    ) -> None:
        self._socket = socket.create_server(address, family=socket.AF_INET, backlog=128)
        self.server_address: tuple[str, int] = self._socket.getsockname()[:2]
        self._threads = threads
        # Uvicorn wraps each accepted connection in TLS when these are set.
        self._tls_cert_file = tls_cert_file
        self._tls_key_file = tls_key_file
        self._server: Optional[_Server] = None
        self.app: Optional[PrksAsgiApp] = None
        self._lock = threading.Lock()
        self._stop_requested = False
        self._stopped = threading.Event()
        self._closed = False

    def _config(self) -> uvicorn.Config:
        self.app = PrksAsgiApp(self, self._threads)
        return uvicorn.Config(
            self.app,
            http=_HeadTimeoutH11,
            loop="asyncio",
            ws="none",
            lifespan="off",
            interface="asgi3",
            log_config=None,
            access_log=False,
            proxy_headers=False,
            server_header=False,
            date_header=True,
            timeout_keep_alive=KEEP_ALIVE_SECONDS,
            h11_max_incomplete_event_size=H11_HEAD_BUFFER_BYTES,
            timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
            ssl_certfile=self._tls_cert_file,
            ssl_keyfile=self._tls_key_file,
        )

    def serve_forever(self, poll_interval: float = 0.5) -> None:  # noqa: ARG002 - Serving protocol
        with self._lock:
            if self._stop_requested:
                self._stopped.set()
                return
            _keep_uvicorn_trace_off()
            self._server = _Server(self._config())
        try:
            self._server.run(sockets=[self._socket])
        finally:
            if self.app is not None:
                self.app.close()
            self._stopped.set()

    def shutdown(self) -> None:
        """Stop serving and wait until :meth:`serve_forever` has returned."""
        with self._lock:
            self._stop_requested = True
            server = self._server
        if server is None:
            return
        server.should_exit = True
        self._stopped.wait()

    def server_close(self) -> None:
        if not self._closed:
            self._closed = True
            with contextlib.suppress(OSError):
                self._socket.close()

    def __enter__(self) -> "UvicornServing":
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.server_close()
