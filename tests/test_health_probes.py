"""HTTP deployment probe contract, before PostgreSQL Compose cutover (#630)."""
from __future__ import annotations

import http.client
import os
import sys
import threading
import unittest
from unittest.mock import patch

_PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT)
from run_tests import apply_isolated_test_env  # noqa: E402

apply_isolated_test_env(_PROJECT)

from backend.server import PRKSHandler, PRKSThreadingTCPServer  # noqa: E402


class HealthProbeTests(unittest.TestCase):
    def setUp(self):
        self.httpd = PRKSThreadingTCPServer(("127.0.0.1", 0), PRKSHandler)
        self.httpd.prks_bind_host = "127.0.0.1"
        self.httpd.prks_trusted_hosts = frozenset()
        self.httpd.prks_auth = object()  # no session; probe cannot depend on it
        self.httpd.prks_jobs = None
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 5)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, method, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        try:
            conn.request(method, path, headers=headers or {})
            response = conn.getresponse()
            status = response.status
            body = response.read()
            fields = dict(response.getheaders())
            return status, body, fields
        finally:
            conn.close()

    def test_liveness_is_unauthenticated_constant_time_no_io(self):
        # No database or storage is configured on this temporary HTTP server.
        # If either probe enters the API authentication gate the test fails.
        with patch.object(PRKSHandler, "_prks_admit", side_effect=AssertionError("API auth invoked")):
            for method, expected_body in (("GET", b"ok"), ("HEAD", b"")):
                with self.subTest(method=method):
                    status, body, headers = self.request(method, "/healthz?probe=1")
                    self.assertEqual(status, 200)
                    self.assertEqual(body, expected_body)
                    self.assertEqual(headers.get("Content-Length"), "2")
                    self.assertEqual(headers.get("Cache-Control"), "no-store")
                    self.assertEqual(headers.get("Content-Type"), "text/plain; charset=utf-8")
                    self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")

    def test_readiness_is_explicitly_unavailable_until_pg_cutover(self):
        # A running HTTP handler cannot claim PostgreSQL readiness while the
        # canonical API still serves SQLite and no pairing check is connected.
        for method, expected_body in (("GET", b"not ready"), ("HEAD", b"")):
            with self.subTest(method=method):
                status, body, headers = self.request(method, "/readyz")
                self.assertEqual(status, 503)
                self.assertEqual(body, expected_body)
                self.assertEqual(headers.get("Content-Length"), "9")
                self.assertEqual(headers.get("Cache-Control"), "no-store")

    def test_untrusted_host_is_rejected_before_any_probe(self):
        for path in ("/healthz", "/readyz"):
            with self.subTest(path=path):
                status, body, _headers = self.request(
                    "GET", path, headers={"Host": "untrusted.invalid"}
                )
                self.assertEqual(status, 421)
                self.assertNotIn(b"not ready", body)


if __name__ == "__main__":
    unittest.main()
