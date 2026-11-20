"""The Uvicorn HTTP server (#1329): settings, and PRKSHandler served over ASGI.

These run without PostgreSQL; ``tests/pg/http_fixtures.py`` runs the
PostgreSQL HTTP suites over the same server.
"""
from __future__ import annotations

import asyncio
import contextlib
import http.client
import logging
import os
import socket
import ssl
import sys
import tempfile
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
    ENV_THREADS,
    ENV_TLS_CERT_FILE,
    ENV_TLS_KEY_FILE,
    HttpServerSettings,
    HttpSettingsError,
)
from tests.tls_certificate import client_context, encrypted_key, temporary_pair  # noqa: E402


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.NOTSET)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class HttpSettingsTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(HttpServerSettings.from_env({}), HttpServerSettings(DEFAULT_THREADS))
        self.assertEqual(HttpServerSettings(), HttpServerSettings(DEFAULT_THREADS))

    def test_threads(self):
        self.assertEqual(HttpServerSettings.from_env({ENV_THREADS: " 8 "}), HttpServerSettings(8))

    def test_invalid_threads_refuse(self):
        for env in (
            {ENV_THREADS: "0"},
            {ENV_THREADS: "257"},
            {ENV_THREADS: "-1"},
            {ENV_THREADS: "4.5"},
            {ENV_THREADS: "99999"},
            {ENV_THREADS: "\u00b2"},  # str.isdigit() is True; int() refuses it
        ):
            with self.subTest(env=env), self.assertRaises(HttpSettingsError) as caught:
                HttpServerSettings.from_env(env)
            self.assertIn(ENV_THREADS, caught.exception.message)

    def test_tls_files_are_loaded_together(self):
        cert, key = temporary_pair(self)
        got = HttpServerSettings.from_env({ENV_TLS_CERT_FILE: cert, ENV_TLS_KEY_FILE: f" {key} "})
        self.assertEqual((got.tls, got.tls_cert_file, got.tls_key_file), (True, cert, key))
        self.assertFalse(HttpServerSettings.from_env({}).tls)
        self.assertFalse(HttpServerSettings.from_env({ENV_TLS_CERT_FILE: " ", ENV_TLS_KEY_FILE: ""}).tls)

    def test_unusable_tls_settings_refuse_and_name_the_variable(self):
        cert, key = temporary_pair(self)
        missing = os.path.join(os.path.dirname(cert), "absent-secret-name.pem")
        not_pem = os.path.join(os.path.dirname(cert), "not-pem.txt")
        with open(not_pem, "w", encoding="ascii") as handle:
            handle.write("not a certificate\n")
        other_cert, other_key = temporary_pair(self)
        fifo = os.path.join(os.path.dirname(cert), "key.fifo")
        os.mkfifo(fifo)  # opening it would block until a writer came
        locked = encrypted_key(os.path.dirname(key), key)
        cases = (
            ({ENV_TLS_CERT_FILE: cert, ENV_TLS_KEY_FILE: fifo}, ENV_TLS_KEY_FILE),
            # Refused, never a passphrase prompt on the terminal.
            ({ENV_TLS_CERT_FILE: cert, ENV_TLS_KEY_FILE: locked}, ENV_TLS_KEY_FILE),
            ({ENV_TLS_CERT_FILE: cert}, ENV_TLS_KEY_FILE),
            ({ENV_TLS_KEY_FILE: key}, ENV_TLS_CERT_FILE),
            ({ENV_TLS_CERT_FILE: missing, ENV_TLS_KEY_FILE: key}, ENV_TLS_CERT_FILE),
            ({ENV_TLS_CERT_FILE: cert, ENV_TLS_KEY_FILE: missing}, ENV_TLS_KEY_FILE),
            ({ENV_TLS_CERT_FILE: os.path.dirname(cert), ENV_TLS_KEY_FILE: key}, ENV_TLS_CERT_FILE),
            ({ENV_TLS_CERT_FILE: not_pem, ENV_TLS_KEY_FILE: key}, ENV_TLS_CERT_FILE),
            ({ENV_TLS_CERT_FILE: cert, ENV_TLS_KEY_FILE: other_key}, ENV_TLS_KEY_FILE),
            ({ENV_TLS_CERT_FILE: key, ENV_TLS_KEY_FILE: cert}, ENV_TLS_CERT_FILE),
        )
        for env, variable in cases:
            with self.subTest(env=env), self.assertRaises(HttpSettingsError) as caught:
                HttpServerSettings.from_env(env)
            message = caught.exception.message
            self.assertIn(variable, message)
            # The error names the variable, never the path or the file's text.
            for path in (cert, key, missing, not_pem, other_cert, other_key, locked, fifo):
                self.assertNotIn(path, message)
                self.assertNotIn(os.path.basename(path), message)
            self.assertNotIn("not a certificate", message)
            self.assertIsNone(caught.exception.__cause__)

    def test_make_server_serves_with_uvicorn(self):
        httpd = server_module.make_server(("127.0.0.1", 0))
        httpd.server_close()
        self.assertIsInstance(httpd, asgi.UvicornServing)


class UvicornTransportTests(unittest.TestCase):
    def setUp(self):
        self.httpd = server_module.make_server(
            ("127.0.0.1", 0), HttpServerSettings(4)
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
            ("127.0.0.1", 0), HttpServerSettings(threads)
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
        self.other_httpd = httpd
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

    def test_static_files_carry_type_length_cache_and_last_modified(self):
        for path, content_type, cache in (
            ("/css/", None, None),  # a directory without index.html: 404, never a listing
            ("/manifest.webmanifest", "application/manifest+json", "public, max-age=3600"),
            ("/favicon.svg", "image/svg+xml", "public, max-age=604800, immutable"),
            ("/sw.js", "text/javascript", "no-cache"),
        ):
            with self.subTest(path=path):
                status, body, headers = self.request("GET", path)
                if content_type is None:
                    self.assertEqual(status, 404)
                    self.assertEqual(headers.get("content-type"), "text/plain; charset=utf-8")
                    self.assertEqual(headers.get("connection"), "close")
                    continue
                self.assertEqual(status, 200)
                self.assertEqual(headers.get("content-type"), content_type)
                self.assertEqual(headers.get("content-length"), str(len(body)))
                self.assertEqual(headers.get("cache-control"), cache)
                self.assertTrue(headers.get("last-modified"))
                with open(os.path.join(server_module.frontend_dir, path.lstrip("/")), "rb") as f:
                    self.assertEqual(body, f.read())

    def test_static_head_has_no_body_and_unchanged_files_answer_304(self):
        status, body, headers = self.request("HEAD", "/favicon.svg")
        self.assertEqual((status, body), (200, b""))
        self.assertGreater(int(headers["content-length"]), 0)
        status, body, _ = self.request("GET", "/favicon.svg", headers={"If-Modified-Since": headers["last-modified"]})
        self.assertEqual((status, body), (304, b""))
        # If-None-Match takes precedence: no 304 from the date alone.
        status, _, _ = self.request(
            "GET", "/favicon.svg", headers={"If-Modified-Since": headers["last-modified"], "If-None-Match": '"x"'}
        )
        self.assertEqual(status, 200)

    def test_symlinks_out_of_the_frontend_are_not_followed(self):
        with tempfile.TemporaryDirectory() as frontend, tempfile.TemporaryDirectory() as outside:
            secret = os.path.join(outside, "secret.html")
            with open(secret, "w", encoding="utf-8") as handle:
                handle.write("<html>outside the frontend")
            os.mkdir(os.path.join(frontend, "app"))
            with open(os.path.join(frontend, "app", "index.html"), "w", encoding="utf-8") as handle:
                handle.write("<html>a subdirectory index")
            os.symlink(secret, os.path.join(frontend, "index.html"))
            os.symlink(secret, os.path.join(frontend, "leak.html"))
            with mock.patch.object(server_module, "frontend_dir", frontend):
                for path in ("/", "/no/such/route", "/index.html", "/leak.html", "/app", "/app/"):
                    with self.subTest(path=path):
                        status, body, _ = self.request("GET", path)
                        self.assertEqual(status, 404)
                        self.assertNotIn(b"outside", body)
                        self.assertNotIn(b"subdirectory", body)
                status, body, _ = self.request("GET", "/app/index.html")
                self.assertEqual((status, body), (200, b"<html>a subdirectory index"))

    def test_a_path_outside_the_frontend_is_404(self):
        status, body, _ = self.request("GET", "/%2e%2e/backend/server.py")
        self.assertEqual(status, 404)
        self.assertNotIn(b"import", body)

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
            ("127.0.0.1", 0), HttpServerSettings()
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
            deadline = time.monotonic() + 5
            while self.other_httpd.app.admitted < 8:  # four running, four waiting
                self.assertLess(time.monotonic(), deadline, "the waiters never reached the wait list")
                time.sleep(0.01)
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

    def test_a_queued_request_whose_client_leaves_mid_body_never_runs(self):
        # Two body messages fill the one-slot queue; the disconnect behind
        # them must still be seen while the request waits for a thread.
        async def scenario():
            release = asyncio.Event()
            served = []

            async def hold(scope, receiver, send):
                served.append(scope["path"])
                if scope["path"] == "/first":
                    await release.wait()

            async def idle():
                await asyncio.Event().wait()
                return {"type": "http.disconnect"}

            messages = [
                {"type": "http.request", "body": b"a" * 1000, "more_body": True},
                {"type": "http.request", "body": b"b" * 1000, "more_body": True},
                {"type": "http.disconnect"},
            ]

            async def leaving():
                if messages:
                    return messages.pop(0)
                return await idle()

            async def send(message):
                pass

            app = asgi.PrksAsgiApp(mock.Mock(), 1)
            app._serve = hold
            first = asyncio.ensure_future(app({"type": "http", "path": "/first"}, idle, send))
            await asyncio.sleep(0.05)
            queued = asyncio.ensure_future(app({"type": "http", "path": "/upload"}, leaving, send))
            await asyncio.sleep(0.1)
            dropped_while_queued = queued.done()
            release.set()
            await first
            await asyncio.wait_for(queued, 2)
            return served, dropped_while_queued, app.admitted

        served, dropped, admitted = asyncio.run(scenario())
        self.assertTrue(dropped, "the request stayed queued after its client left")
        self.assertEqual(served, ["/first"])
        self.assertEqual(admitted, 0)

    def test_a_queued_request_keeps_its_whole_body(self):
        # 1000-byte chunks are merged while queued; 40000-byte ones pass the
        # QUEUED_BODY_BYTES bound, so the receiver waits on the full queue.
        for size in (1000, 40000):
            with self.subTest(chunk=size):
                bodies, expected = asyncio.run(self._queued_upload(size))
                self.assertEqual(bodies, [expected])

    async def _queued_upload(self, size):
        release = asyncio.Event()
        bodies = []

        async def hold(scope, receiver, send):
            if scope["path"] == "/first":
                await release.wait()
                return
            body = b""
            while True:
                message = await receiver.messages.get()
                body += message.get("body", b"")
                if not message.get("more_body", False):
                    break
            bodies.append(body)

        async def idle():
            await asyncio.Event().wait()
            return {"type": "http.disconnect"}

        chunks = [b"a" * size, b"b" * size, b"c" * size]
        messages = [{"type": "http.request", "body": c, "more_body": i < 2} for i, c in enumerate(chunks)]

        async def uploading():
            if messages:
                return messages.pop(0)
            return await idle()

        async def send(message):
            pass

        app = asgi.PrksAsgiApp(mock.Mock(), 1)
        app._serve = hold
        first = asyncio.ensure_future(app({"type": "http", "path": "/first"}, idle, send))
        await asyncio.sleep(0.05)
        queued = asyncio.ensure_future(app({"type": "http", "path": "/upload"}, uploading, send))
        await asyncio.sleep(0.1)
        release.set()
        await first
        await asyncio.wait_for(queued, 2)
        return bodies, b"".join(chunks)

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
        # Past h11's buffer the head is refused either way: h11 answers 400 if
        # a read leaves it incomplete, PRKS 431 if one read held all of it.
        cases = ((limit - 200, {200}), (limit, {200}), (limit + 1, {431}), (72694, {431}),
                 (3 * limit, {400, 431}))
        for size, expected in cases:
            head = self._head_of(size)
            for piece in (len(head), 4096, 1000):  # at once, then in fragments
                with self.subTest(size=size, piece=piece):
                    self.assertIn(self._status_of(head, piece), expected)

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

    def test_plain_http_responses_do_not_send_hsts(self):
        for path in ("/healthz", "/", "/api/tags"):
            with self.subTest(path=path):
                _, _, headers = self.request("GET", path)
                self.assertNotIn("strict-transport-security", headers)

    def test_too_many_headers_is_431(self):
        headers = {f"X-H{i}": "v" for i in range(101)}
        self.assertEqual(self.request("GET", "/healthz", headers=headers)[0], 431)


class TlsTransportTests(unittest.TestCase):
    """PRKS's own TLS over a real socket, with a throwaway self-signed pair (#1339)."""

    def setUp(self):
        self.cert, key = temporary_pair(self)
        self.httpd = server_module.make_server(
            ("127.0.0.1", 0), HttpServerSettings(2, tls_cert_file=self.cert, tls_key_file=key)
        )
        self.httpd.prks_bind_host = "127.0.0.1"
        self.httpd.prks_trusted_hosts = frozenset()
        self.httpd.prks_access_gate = server_module.LibraryAccessGate()
        self.httpd.prks_auth = None
        self.httpd.prks_jobs = None
        self.httpd.prks_readiness = None
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.port = self.httpd.server_address[1]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                if self.request("GET", "/healthz")[0] == 200:
                    return
            except OSError:
                time.sleep(0.05)
        self.fail("the TLS transport did not start")

    def request(self, method, path, headers=None):
        conn = http.client.HTTPSConnection(
            "127.0.0.1", self.port, timeout=10, context=client_context(self.cert)
        )
        try:
            conn.request(method, path, headers=headers or {})
            response = conn.getresponse()
            return response.status, response.read(), {k.lower(): v for k, v in response.getheaders()}
        finally:
            conn.close()

    def test_https_responses_carry_hsts(self):
        for method, path, status in (
            ("GET", "/healthz", 200),
            ("GET", "/", 200),
            ("GET", "/api/tags", 503),
            ("GET", "/no-such/../../file", 404),
        ):
            with self.subTest(path=path):
                got, _, headers = self.request(method, path)
                self.assertEqual(got, status)
                self.assertEqual(headers.get("strict-transport-security"), server_module.HSTS_VALUE)

    def test_the_busy_answer_over_tls_carries_hsts(self):
        for https, expected in ((True, server_module.HSTS_VALUE.encode("ascii")), (False, None)):
            sent = []

            async def send(message, sent=sent):
                sent.append(message)

            asyncio.run(asgi._send_busy(send, https=https))
            headers = dict(sent[0]["headers"])
            with self.subTest(https=https):
                self.assertEqual(sent[0]["status"], 503)
                self.assertEqual(headers.get(b"strict-transport-security"), expected)

    def test_the_client_sees_the_configured_certificate(self):
        conn = http.client.HTTPSConnection(
            "127.0.0.1", self.port, timeout=10, context=client_context(self.cert)
        )
        try:
            conn.request("GET", "/healthz")
            self.assertEqual(conn.getresponse().status, 200)
            assert isinstance(conn.sock, ssl.SSLSocket)
            peer = conn.sock.getpeercert()
            assert peer is not None
            self.assertEqual(peer["subject"], ((("commonName", "127.0.0.1"),),))
        finally:
            conn.close()

    def test_plain_http_to_the_tls_port_is_not_served(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            sock.sendall(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            received = b""
            with contextlib.suppress(OSError):
                while chunk := sock.recv(4096):
                    received += chunk
        # The TLS handshake fails: no HTTP response, only the connection closing.
        self.assertFalse(received.startswith(b"HTTP/"), received[:40])


class ShutdownTests(unittest.TestCase):
    def test_shutdown_before_serving_returns(self):
        httpd = server_module.make_server(("127.0.0.1", 0), HttpServerSettings())
        httpd.shutdown()
        httpd.serve_forever()  # returns at once: a stop was already requested
        httpd.server_close()

    def test_shutdown_waits_for_serving_to_end_and_closes_the_port(self):
        httpd = server_module.make_server(("127.0.0.1", 0), HttpServerSettings())
        for name, value in (("prks_bind_host", "127.0.0.1"), ("prks_trusted_hosts", frozenset()),
                            ("prks_auth", None), ("prks_jobs", None), ("prks_readiness", None)):
            setattr(httpd, name, value)
        httpd.prks_access_gate = server_module.LibraryAccessGate()
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        port = httpd.server_address[1]
        served = False
        deadline = time.monotonic() + 5
        while not served and time.monotonic() < deadline:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            try:
                conn.request("GET", "/healthz")
                served = conn.getresponse().status == 200
            except OSError:
                time.sleep(0.05)
            finally:
                conn.close()
        self.assertTrue(served, "the server never answered before shutdown")
        httpd.shutdown()
        self.assertFalse(thread.is_alive() and (thread.join(5) or thread.is_alive()))
        httpd.server_close()
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1)


if __name__ == "__main__":
    unittest.main()
