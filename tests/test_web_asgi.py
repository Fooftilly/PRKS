"""The Uvicorn transport (#1329 Phase B): settings, and PRKSHandler served over ASGI.

These run without PostgreSQL. ``tests/pg/http_fixtures.py`` runs the whole
HTTP suite over this transport when ``PRKS_TEST_HTTP_TRANSPORT=uvicorn``.
"""
from __future__ import annotations

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
    def test_defaults_keep_the_stdlib_transport(self):
        self.assertEqual(HttpServerSettings.from_env({}), HttpServerSettings("stdlib", DEFAULT_THREADS))

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
        httpd = server_module.make_server(("127.0.0.1", 0), HttpServerSettings())
        httpd.server_close()
        self.assertIsInstance(httpd, server_module.PRKSThreadingTCPServer)
        # This module imports Uvicorn, so check the import in a fresh process.
        probe = (
            "import sys\n"
            "from run_tests import apply_isolated_test_env\n"
            f"apply_isolated_test_env({_PROJECT!r})\n"
            "from backend import server\n"
            "from backend.web.settings import HttpServerSettings\n"
            "server.make_server(('127.0.0.1', 0), HttpServerSettings()).server_close()\n"
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

    def request(self, method, path, headers=None, body=None, conn=None):
        own = conn is None
        conn = conn or http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
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
