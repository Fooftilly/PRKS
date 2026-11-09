"""``POST /api/backups/stage`` on the PostgreSQL Library (PG-C1 #640).

The staged upload holds the Library's read gate, which a restore waits out, so
its body is read with the Processing upload's idle timeout and deadline: a
stalled client is answered ``408`` and frees the gate.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from unittest import mock

from backend.domain.maintenance import staged
from backend.api import processing as processing_api
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
