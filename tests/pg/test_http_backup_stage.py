"""``POST /api/backups/stage`` and ``/restore`` on the PostgreSQL Library (PG-C1 #640, PG-C2 #641).

The staged upload holds the Library's read gate, which a restore waits out, so
its body is read with the Processing upload's idle timeout and deadline: a
stalled client is answered ``408`` and frees the gate. A body refused before
or after it is read stages nothing, and a restore needs the confirmation and
a token this user staged. The whole backup, stage and restore round trip is
``tests/pg/test_postgres_library_start.py``.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import threading
import time
from unittest import mock

from backend.domain.maintenance import jobs as backup_jobs
from backend.domain.maintenance import staged
from backend.api import processing as processing_api
from tests.pg.http_fixtures import Response
from tests.pg.test_http_processing import ProcessingHttpTestCase


class BackupStageHttpTests(ProcessingHttpTestCase):
    def _stalled_stage(self) -> tuple[bytes, float]:
        head = (
            f"POST /api/backups/stage HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
            f"Origin: {self.origin}\r\nCookie: prks_session={self.cookies['prks_session']}\r\n"
            f"PRKS-Library-Epoch: {self.library_epoch()}\r\n"
            "Content-Type: application/octet-stream\r\nContent-Length: 1000000\r\n\r\n"
        ).encode()
        started = time.monotonic()
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            sock.sendall(head + b"PK\x03\x04" + b"0" * 36)
            raw = sock.recv(65536)
            while chunk := sock.recv(65536):
                raw += chunk
        return raw, time.monotonic() - started

    def test_a_stalled_stage_upload_times_out_and_frees_the_gate_for_a_restore(self) -> None:
        gate = self.httpd.prks_access_gate
        restored = threading.Event()

        def restore() -> None:
            while not gate.snapshot()["active_reads"]:
                time.sleep(0.01)
            with gate.restore():
                restored.set()

        waiter = threading.Thread(target=restore, daemon=True)
        waiter.start()
        with mock.patch.object(processing_api, "UPLOAD_IDLE_SECONDS", 0.5):
            raw, elapsed = self._stalled_stage()
        waiter.join(10)

        status_line, _, rest = raw.partition(b"\r\n")
        self.assertIn(b" 408 ", status_line)
        self.assertEqual(json.loads(rest.partition(b"\r\n\r\n")[2])["reason"], "upload_timeout")
        self.assertIn(b"Connection: close", rest)
        self.assertLess(elapsed, 5)
        self.assertTrue(restored.is_set())
        directory = staged.staged_dir(self.server_module._bound_storage.root)
        self.assertEqual(os.listdir(directory) if os.path.isdir(directory) else [], [])

    def stage(self, data: bytes, content_type: str = "application/octet-stream") -> Response:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.putrequest("POST", "/api/backups/stage", skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", f"127.0.0.1:{self.port}")
            for name, value in self._request_headers("POST", self.cookies, [], self.origin, self.library_epoch()):
                conn.putheader(name, value)
            conn.putheader("Content-Type", content_type)
            conn.putheader("Content-Length", str(len(data)))
            conn.endheaders(data)
            res = conn.getresponse()
            return Response(res.status, list(res.getheaders()), res.read())
        finally:
            conn.close()

    def staged_files(self) -> list[str]:
        directory = staged.staged_dir(self.server_module._bound_storage.root)
        return os.listdir(directory) if os.path.isdir(directory) else []

    def test_refusals_before_the_body_stage_nothing(self) -> None:
        self.assertEqual(self.stage(b"PK\x03\x04", content_type="text/plain").status, 415)
        with mock.patch.object(self.server_module, "backup_max_upload_bytes", return_value=3):
            res = self.stage(b"PK\x03\x04")
        self.assertEqual((res.status, res.body), (413, {"error": "request_too_large"}))
        self.assertEqual(self.staged_files(), [])

    def test_a_body_that_is_not_a_backup_is_refused_and_staged_nothing(self) -> None:
        res = self.stage(b"this is not a zip archive")
        self.assertEqual(res.status, 400, res.raw)
        self.assertTrue(res.body["reason"], res.raw)
        self.assertEqual(self.staged_files(), [])

    def test_a_restore_needs_the_confirmation_and_a_staged_token(self) -> None:
        token = "0" * 32
        res = self.send("POST", "/api/backups/restore", {"token": token})
        self.assertEqual((res.status, res.body["reason"]), (400, "confirmation_required"), res.raw)
        res = self.send("POST", "/api/backups/restore", {"token": token, "confirm": "RESTORE"})
        self.assertEqual((res.status, res.body["reason"]), (400, "unknown_token"), res.raw)
        self.assertEqual(self.staged_files(), [])

    def backup_jobs(self) -> int:
        return int(self.run_sql("SELECT count(*) AS n FROM prks.jobs WHERE kind = %s",
                                (backup_jobs.BACKUP_KIND,))[0]["n"])

    def test_reads_and_malformed_requests_never_start_a_backup(self) -> None:
        self.assertNotEqual(self.get("/api/backups/download").status, 200)
        self.assertNotEqual(self.get("/api/backups/download?token=" + "0" * 32).status, 200)
        self.assertNotEqual(self.get("/api/backups/progress").status, 200)
        res = self.call("POST", "/api/backups/progress", cookies=self.cookies, epoch=self.library_epoch(),
                        headers=[("Content-Type", "text/plain")])
        self.assertEqual(res.status, 415, res.raw)
        self.assertEqual(self.backup_jobs(), 0)
