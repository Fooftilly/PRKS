"""The Uvicorn transport (#1329 Phase B): settings, and PRKSHandler served over ASGI.

These run without PostgreSQL. ``tests/pg/http_fixtures.py`` runs the whole
HTTP suite over this transport when ``PRKS_TEST_HTTP_TRANSPORT=uvicorn``.
"""
from __future__ import annotations

import asyncio
import http.client
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

_PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT)
from run_tests import apply_isolated_test_env  # noqa: E402

apply_isolated_test_env(_PROJECT)

from backend import server as server_module  # noqa: E402
from backend.web import asgi  # noqa: E402
from backend.web.settings import (  # noqa: E402
    DEFAULT_THREADS,
    ENV_SERVER,
    ENV_THREADS,
    HttpServerSettings,
    HttpSettingsError,
)


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.NOTSET)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class HttpSettingsTests(unittest.TestCase):
    def test_defaults_select_the_uvicorn_transport(self):
        self.assertEqual(HttpServerSettings.from_env({}), HttpServerSettings("uvicorn", DEFAULT_THREADS))
        self.assertEqual(HttpServerSettings(), HttpServerSettings("uvicorn", DEFAULT_THREADS))

    def test_stdlib_stays_selectable_until_it_is_deleted(self):
        self.assertEqual(HttpServerSettings.from_env({ENV_SERVER: "stdlib"}).transport, "stdlib")

    def test_uvicorn_and_threads(self):
        settings = HttpServerSettings.from_env({ENV_SERVER: " Uvicorn ", ENV_THREADS: "8"})
        self.assertEqual(settings, HttpServerSettings("uvicorn", 8))

    def test_invalid_values_refuse(self):
        for env in (
            {ENV_SERVER: "gunicorn"},
            {ENV_THREADS: "0"},
            {ENV_THREADS: "257"},
            {ENV_THREADS: "-1"},
            {ENV_THREADS: "4.5"},
            {ENV_THREADS: "99999"},
            {ENV_THREADS: "\u00b2"},  # str.isdigit() is True; int() refuses it
        ):
            with self.subTest(env=env), self.assertRaises(HttpSettingsError) as caught:
                HttpServerSettings.from_env(env)
            self.assertIn("PRKS_HTTP_", caught.exception.message)

    def test_stdlib_transport_does_not_import_uvicorn(self):
        httpd = server_module.make_server(("127.0.0.1", 0), HttpServerSettings(transport="stdlib"))
        httpd.server_close()
        self.assertIsInstance(httpd, server_module.PRKSThreadingTCPServer)
        # This module imports Uvicorn, so check the import in a fresh process.
        probe = (
            "import sys\n"
            "from run_tests import apply_isolated_test_env\n"
            f"apply_isolated_test_env({_PROJECT!r})\n"
            "from backend import server\n"
            "from backend.web.settings import HttpServerSettings\n"
            "server.make_server(('127.0.0.1', 0), HttpServerSettings(transport='stdlib')).server_close()\n"
            "sys.exit(1 if 'uvicorn' in sys.modules or 'backend.web.asgi' in sys.modules else 0)\n"
        )
        result = subprocess.run([sys.executable, "-c", probe], cwd=_PROJECT, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, "the stdlib transport imported Uvicorn")


class UvicornTransportTests(unittest.TestCase):
    def setUp(self):
        self.httpd = server_module.make_server(
            ("127.0.0.1", 0), HttpServerSettings.from_env({ENV_SERVER: "uvicorn", ENV_THREADS: "4"})
        )
        self.httpd.prks_bind_host = "127.0.0.1"
        self.httpd.prks_trusted_hosts = frozenset()
        self.httpd.prks_access_gate = server_module.LibraryAccessGate()
        self.httpd.prks_auth = None  # every /api/ request answers 503
        self.httpd.prks_jobs = None
        self.httpd.prks_readiness = None
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 10)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.port = self.httpd.server_address[1]
        self._wait_listening()

    def _start_server(self, threads=4):
        """Another Uvicorn transport, configured like setUp's; returns its port."""
        httpd = server_module.make_server(
            ("127.0.0.1", 0), HttpServerSettings.from_env({ENV_SERVER: "uvicorn", ENV_THREADS: str(threads)})
        )
        httpd.prks_bind_host = "127.0.0.1"
        httpd.prks_trusted_hosts = frozenset()
        httpd.prks_access_gate = server_module.LibraryAccessGate()
        httpd.prks_auth = None
        httpd.prks_jobs = None
        httpd.prks_readiness = None
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        port = httpd.server_address[1]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                if self.request("GET", "/healthz", port=port)[0] == 200:
                    return port
            except OSError:
                time.sleep(0.05)
        self.fail("the second Uvicorn transport did not start")

    def _wait_listening(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                status, _, _ = self.request("GET", "/healthz")
                if status == 200:
                    return
            except OSError:
                time.sleep(0.05)
        self.fail("the Uvicorn transport did not start")

    def request(self, method, path, headers=None, body=None, conn=None, port=None):
        own = conn is None
        conn = conn or http.client.HTTPConnection("127.0.0.1", port or self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, response.read(), {k.lower(): v for k, v in response.getheaders()}
        finally:
            if own:
                conn.close()

    def test_probe_answers_like_the_stdlib_transport_without_a_server_header(self):
        for method, expected in (("GET", b"ok"), ("HEAD", b"")):
            with self.subTest(method=method):
                status, body, headers = self.request(method, "/healthz")
                self.assertEqual(status, 200)
                self.assertEqual(body, expected)
                self.assertEqual(headers.get("content-length"), "2")
                self.assertEqual(headers.get("cache-control"), "no-store")
                self.assertEqual(headers.get("x-content-type-options"), "nosniff")
                self.assertRegex(headers.get("x-request-id", ""), r"^[0-9a-f]{12}$")
                self.assertNotIn("server", headers)

    def test_api_without_identity_fails_closed(self):
        status, body, headers = self.request("GET", "/api/folders")
        self.assertEqual(status, 503)
        self.assertIn(b"identity_not_started", body)
        self.assertEqual(headers.get("content-type"), "application/json; charset=utf-8")

    def test_untrusted_host_is_refused(self):
        status, _, _ = self.request("GET", "/healthz", headers={"Host": "evil.example"})
        self.assertEqual(status, 421)

    def test_url_holding_nul_is_refused(self):
        status, body, _ = self.request("GET", "/api/works/%00")
        self.assertEqual(status, 400)
        self.assertIn(b"invalid_request", body)

    def test_static_shell_and_spa_fallback(self):
        status, body, headers = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<html", body.lower())
        self.assertEqual(headers.get("cache-control"), "no-store, max-age=0")
        status, fallback, _ = self.request("GET", "/no/such/route")
        self.assertEqual(status, 200)
        self.assertEqual(fallback, body)

    def test_post_outside_api_is_405_and_unknown_method_501(self):
        self.assertEqual(self.request("POST", "/index.html", body=b"{}")[0], 405)
        self.assertEqual(self.request("BREW", "/")[0], 501)

    def test_keep_alive_reuses_one_connection(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(conn.close)
        for _ in range(3):
            self.assertEqual(self.request("GET", "/healthz", conn=conn)[0], 200)

    def test_stalled_clients_do_not_hold_request_threads(self):
        stalled = []
        for _ in range(12):  # three times the thread limit of 4
            sock = socket.create_connection(("127.0.0.1", self.port))
            sock.sendall(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Partial: ")
            stalled.append(sock)
        self.addCleanup(lambda: [s.close() for s in stalled])
        started = time.monotonic()
        self.assertEqual(self.request("GET", "/healthz")[0], 200)
        self.assertLess(time.monotonic() - started, 2)

    def test_disconnect_ends_a_response_only_stream(self):
        # Uvicorn's send() returns silently once the client is gone; the
        # adapter must still raise, or a backup-progress stream keeps its
        # thread and its Library access scope until the job ends.
        ended = threading.Event()

        def stream(handler):
            handler.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n")
            try:
                for _ in range(400):  # 20 s if nothing stops it
                    handler.wfile.write(b"tick\n")
                    time.sleep(0.05)
            except BrokenPipeError:
                ended.set()
                raise

        with mock.patch.object(asgi._AsgiHandler, "do_GET", stream):
            sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            sock.sendall(b"GET /stream HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            received = b""
            while b"tick" not in received:
                chunk = sock.recv(4096)
                self.assertTrue(chunk, "the stream closed before its first line")
                received += chunk
            sock.close()
            self.assertTrue(ended.wait(3), "the stream kept writing after the client left")

    def test_disconnect_ends_a_stream_whose_request_body_is_unread(self):
        # A GET handler never reads rfile. Body chunks it leaves unread must
        # not stop the adapter from seeing the client leave.
        ended = threading.Event()

        def stream(handler):
            handler.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n")
            try:
                for _ in range(400):
                    handler.wfile.write(b"tick\n")
                    time.sleep(0.05)
            except BrokenPipeError:
                ended.set()
                raise

        with mock.patch.object(asgi._AsgiHandler, "do_GET", stream):
            sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            sock.sendall(
                b"GET /stream HTTP/1.1\r\nHost: 127.0.0.1\r\nTransfer-Encoding: chunked\r\n\r\n"
            )
            for part in (b"hello", b"world", b"again"):
                sock.sendall(b"%x\r\n%s\r\n" % (len(part), part))
                time.sleep(0.05)
            received = b""
            while b"tick" not in received:
                chunk = sock.recv(4096)
                self.assertTrue(chunk, "the stream closed before its first line")
                received += chunk
            sock.close()
            self.assertTrue(ended.wait(3), "the stream kept writing after the client left")

    def test_trace_logging_never_records_request_targets(self):
        root = logging.getLogger()
        handler = _Collect()
        records = handler.records
        old_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.NOTSET)  # PRKS_LOG_LEVEL=NOTSET
        self.addCleanup(root.setLevel, old_level)
        self.addCleanup(root.removeHandler, handler)
        httpd = server_module.make_server(
            ("127.0.0.1", 0), HttpServerSettings.from_env({ENV_SERVER: "uvicorn"})
        )
        for name in ("prks_bind_host", "prks_trusted_hosts", "prks_auth", "prks_jobs", "prks_readiness"):
            setattr(httpd, name, getattr(self.httpd, name))
        httpd.prks_access_gate = server_module.LibraryAccessGate()
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        port = httpd.server_address[1]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/healthz?secret-term=1")
                conn.getresponse().read()
                conn.close()
                break
            except OSError:
                time.sleep(0.05)
        self.assertFalse(
            [r for r in records if "secret-term" in r.getMessage()],
            "a request target reached the logs",
        )

    def test_saturated_threads_queue_requests_instead_of_refusing_them(self):
        # PRKS_HTTP_THREADS bounds PRKS work; limit_concurrency stays unset,
        # so a request over the bound waits for a thread rather than a 503.
        release = threading.Event()
        started = threading.Semaphore(0)
        original = asgi._AsgiHandler.do_GET

        def slow_or_normal(handler):
            if handler.path != "/slow":
                return original(handler)
            started.release()
            release.wait(10)
            handler.send_response(200)
            handler.send_header("Content-Length", "4")
            handler.end_headers()
            handler.wfile.write(b"slow")
            return None

        self.addCleanup(release.set)
        with mock.patch.object(asgi._AsgiHandler, "do_GET", slow_or_normal):
            slow = [threading.Thread(target=self.request, args=("GET", "/slow"), daemon=True) for _ in range(4)]
            for thread in slow:
                thread.start()
            for _ in slow:  # all four PRKS threads are busy
                self.assertTrue(started.acquire(timeout=5))
            result = {}
            waiting = threading.Thread(
                target=lambda: result.update(status=self.request("GET", "/healthz")[0]), daemon=True
            )
            waiting.start()
            waiting.join(0.5)
            self.assertTrue(waiting.is_alive(), "a fifth request ran while every thread was busy")
            release.set()
            waiting.join(5)
            self.assertEqual(result.get("status"), 200)
            for thread in slow:
                thread.join(5)

    def test_requests_past_the_wait_list_bound_get_503_at_once(self):
        # 4 threads x QUEUE_PER_THREAD (patched to 1) = 4 may wait; the next is refused.
        release = threading.Event()
        started = threading.Semaphore(0)
        original = asgi._AsgiHandler.do_GET

        def slow_or_normal(handler):
            if handler.path != "/slow":
                return original(handler)
            started.release()
            release.wait(10)
            handler.send_response(200)
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return None

        self.addCleanup(release.set)
        with mock.patch.object(asgi, "QUEUE_PER_THREAD", 1):
            port = self._start_server()
        with mock.patch.object(asgi._AsgiHandler, "do_GET", slow_or_normal):
            busy = [threading.Thread(target=self.request, args=("GET", "/slow", None, None, None, port), daemon=True)
                    for _ in range(4)]
            for thread in busy:
                thread.start()
            for _ in busy:
                self.assertTrue(started.acquire(timeout=5))
            statuses = []
            waiters = [threading.Thread(target=lambda: statuses.append(self.request("GET", "/healthz", port=port)[0]),
                                        daemon=True) for _ in range(4)]
            for thread in waiters:
                thread.start()
            time.sleep(0.5)  # let all four reach the wait list
            status, body, headers = self.request("GET", "/healthz", port=port)
            self.assertEqual(status, 503)
            self.assertIn(b"server_busy", body)
            self.assertEqual(headers.get("retry-after"), "1")
            release.set()
            for thread in busy + waiters:
                thread.join(5)
            self.assertEqual(statuses, [200, 200, 200, 200])

    def test_a_burst_on_free_threads_cannot_pass_the_wait_list_bound(self):
        # Driven at the ASGI level so the burst is exact: every request enters
        # before any slot acquisition has run. 1 thread + 1 waiter admitted.
        async def burst():
            release = asyncio.Event()
            served = []
            statuses = []

            async def hold(scope, receiver, send):
                served.append(scope["path"])
                await release.wait()

            async def receive():
                await asyncio.Event().wait()  # the client never sends or leaves
                return {"type": "http.disconnect"}

            async def send(message):
                if message["type"] == "http.response.start":
                    statuses.append(message["status"])

            with mock.patch.object(asgi, "QUEUE_PER_THREAD", 1):
                app = asgi.PrksAsgiApp(mock.Mock(), 1)
            app._serve = hold
            scope = {"type": "http", "path": "/burst"}
            calls = [asyncio.ensure_future(app(scope, receive, send)) for _ in range(20)]
            await asyncio.sleep(0.2)
            seen = (list(statuses), list(served))
            release.set()
            await asyncio.wait_for(asyncio.gather(*calls), 5)
            return seen, served, app._admitted

        (refused, running), served, admitted = asyncio.run(burst())
        self.assertEqual(refused, [503] * 18)
        self.assertEqual(running, ["/burst"])
        self.assertEqual(served, ["/burst", "/burst"])
        self.assertEqual(admitted, 0)

    def test_cancelling_a_queued_request_keeps_the_thread_slot(self):
        async def scenario():
            release = asyncio.Event()
            served = []

            async def hold(scope, receiver, send):
                served.append(scope["path"])
                if scope["path"] == "/first":
                    await release.wait()

            async def receive():
                await asyncio.Event().wait()
                return {"type": "http.disconnect"}

            async def send(message):
                pass

            app = asgi.PrksAsgiApp(mock.Mock(), 1)
            app._serve = hold
            first = asyncio.ensure_future(app({"type": "http", "path": "/first"}, receive, send))
            await asyncio.sleep(0.05)
            queued = asyncio.ensure_future(app({"type": "http", "path": "/cancelled"}, receive, send))
            await asyncio.sleep(0.05)
            queued.cancel()
            release.set()
            await first
            await asyncio.gather(queued, return_exceptions=True)
            await asyncio.wait_for(app({"type": "http", "path": "/after"}, receive, send), 2)
            return served, queued.cancelled(), app._admitted

        served, cancelled, admitted = asyncio.run(scenario())
        self.assertTrue(cancelled, "the cancellation was swallowed")
        self.assertEqual(served, ["/first", "/after"])
        self.assertEqual(admitted, 0)

    def test_a_request_whose_client_leaves_while_queued_never_runs(self):
        release = threading.Event()
        started = threading.Semaphore(0)
        ran = []
        original = asgi._AsgiHandler.do_GET

        def routes(handler):
            if handler.path == "/abandoned":
                ran.append(handler.path)
            if handler.path != "/slow":
                return original(handler)
            started.release()
            release.wait(10)
            handler.send_response(200)
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return None

        self.addCleanup(release.set)
        with mock.patch.object(asgi._AsgiHandler, "do_GET", routes):
            busy = [threading.Thread(target=self.request, args=("GET", "/slow"), daemon=True) for _ in range(4)]
            for thread in busy:
                thread.start()
            for _ in busy:  # every thread is held
                self.assertTrue(started.acquire(timeout=5))
            sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            sock.sendall(b"GET /abandoned HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            time.sleep(0.3)  # it is waiting for a thread
            sock.close()
            time.sleep(0.3)  # the disconnect reaches the receiver
            release.set()
            for thread in busy:
                thread.join(5)
            self.assertEqual(self.request("GET", "/healthz")[0], 200)
            time.sleep(0.2)
        self.assertEqual(ran, [], "a request ran after its client had left")

    def _head_of(self, size):
        """A ``GET /healthz`` head of exactly ``size`` bytes, in lines of ~900 bytes."""
        head = b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n"
        index = 0
        while len(head) + 2 < size:
            room = size - len(head) - 2 - len(b"X-H%02d: \r\n" % index)
            head += b"X-H%02d: %s\r\n" % (index, b"v" * max(1, min(900, room)))
            index += 1
        head += b"\r\n"
        self.assertEqual(len(head), size)
        return head

    def _status_of(self, head, piece):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        try:
            try:
                for start in range(0, len(head), piece):
                    sock.sendall(head[start:start + piece])
                    if piece < len(head):
                        time.sleep(0.01)
            except (BrokenPipeError, ConnectionResetError):
                pass  # refused before the head ended; the answer is already sent
            response = b""
            while b"\r\n" not in response:
                try:
                    chunk = sock.recv(4096)
                except ConnectionResetError:
                    break
                if not chunk:
                    break
                response += chunk
            return int(response.split(b" ", 2)[1])
        finally:
            sock.close()

    def test_the_head_limit_does_not_depend_on_how_the_bytes_arrive(self):
        limit = asgi.MAX_REQUEST_HEAD_BYTES
        cases = ((limit - 200, 200), (limit, 200), (limit + 1, 431), (72694, 431),
                 (3 * limit, 400))  # past h11's buffer: h11 refuses it, for either pattern too
        for size, expected in cases:
            head = self._head_of(size)
            for piece in (len(head), 4096, 1000):  # at once, then in fragments
                with self.subTest(size=size, piece=piece):
                    self.assertEqual(self._status_of(head, piece), expected)

    def test_idle_keep_alive_connections_do_not_refuse_requests(self):
        idle = []
        self.addCleanup(lambda: [c.close() for c in idle])
        for _ in range(40):  # ten times the thread limit
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            self.assertEqual(self.request("GET", "/healthz", conn=conn)[0], 200)
            idle.append(conn)  # left open, idle, between requests
        started = time.monotonic()
        self.assertEqual(self.request("GET", "/healthz")[0], 200)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(self.request("GET", "/healthz", conn=idle[0])[0], 200)  # still reusable

    def test_too_many_headers_is_431(self):
        headers = {f"X-H{i}": "v" for i in range(101)}
        self.assertEqual(self.request("GET", "/healthz", headers=headers)[0], 431)


class ShutdownTests(unittest.TestCase):
    def test_shutdown_before_serving_returns(self):
        httpd = server_module.make_server(("127.0.0.1", 0), HttpServerSettings.from_env({ENV_SERVER: "uvicorn"}))
        httpd.shutdown()
        httpd.serve_forever()  # returns at once: a stop was already requested
        httpd.server_close()

    def test_shutdown_waits_for_serving_to_end_and_closes_the_port(self):
        httpd = server_module.make_server(("127.0.0.1", 0), HttpServerSettings.from_env({ENV_SERVER: "uvicorn"}))
        for name, value in (("prks_bind_host", "127.0.0.1"), ("prks_trusted_hosts", frozenset()),
                            ("prks_auth", None), ("prks_jobs", None), ("prks_readiness", None)):
            setattr(httpd, name, value)
        httpd.prks_access_gate = server_module.LibraryAccessGate()
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        port = httpd.server_address[1]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.05)
        httpd.shutdown()
        self.assertFalse(thread.is_alive() and (thread.join(5) or thread.is_alive()))
        httpd.server_close()
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1)


if __name__ == "__main__":
    unittest.main()
