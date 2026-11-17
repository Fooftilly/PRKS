"""The Library access gate in front of the PostgreSQL Library (PG-C2 #641).

Every request runs on its own thread and borrows its own pooled connection,
so reads overlap. Mutations take the gate's mutation scope one at a time. A
backup holds no gate at all, because the worker writes it from one snapshot.
A restore takes the gate exclusively, and a read that arrives meanwhile waits
and then sees the restored Library. A request whose JSON body is still
arriving holds no scope yet. A stage upload that fails unexpectedly answers
``500`` with its request id, leaves no partial file and frees the gate. These replace the SQLite
``tests/test_concurrency.py`` live HTTP cases.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import threading
from typing import Any, Callable
from unittest import mock

from backend.api import maintenance as maintenance_api
from backend.domain.works import work_commands
from tests.pg.http_fixtures import Response
from tests.pg.test_http_processing import ProcessingHttpTestCase


class _Worker:
    """A request run on its own thread; ``join`` re-raises what it raised."""

    def __init__(self, fn: Callable[[], Any]) -> None:
        self.result: Any = None
        self.error: BaseException | None = None

        def run() -> None:
            try:
                self.result = fn()
            except BaseException as exc:  # noqa: BLE001 - re-raised by join
                self.error = exc

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def join(self, timeout: float = 20) -> Any:
        self.thread.join(timeout)
        if self.thread.is_alive():
            raise AssertionError("the request is still running")
        if self.error is not None:
            raise self.error
        return self.result


class LibraryGateHttpTests(ProcessingHttpTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.gate = self.httpd.prks_access_gate

    def titles(self) -> list[str]:
        res = self.get("/api/works")
        self.assertEqual(res.status, 200, res.raw)
        return sorted(row["title"] for row in res.body)

    def create(self, title: str) -> dict[str, Any]:
        res = self.send("POST", "/api/works", {"title": title})
        self.assertEqual(res.status, 200, res.raw)
        return res.body

    def test_reads_overlap_on_separate_threads(self) -> None:
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = work_commands.catalog

        def held(*args: Any) -> list[dict[str, Any]]:
            entered.set()
            self.assertTrue(release.wait(10))
            return original(*args)

        with mock.patch.object(work_commands, "catalog", held):
            first = _Worker(lambda: self.get("/api/works"))
            self.assertTrue(entered.wait(10))
            # A second read runs to the end while the first is still inside the command.
            self.assertEqual(self.get("/api/folders").status, 200)
            release.set()
            self.assertEqual(first.join().status, 200)

    def test_two_reads_run_in_the_facade_at_once(self) -> None:
        both = threading.Barrier(2, timeout=10)
        original = work_commands.catalog

        def together(*args: Any) -> list[dict[str, Any]]:
            both.wait()  # breaks, failing both reads, unless the two overlap
            return original(*args)

        with mock.patch.object(work_commands, "catalog", together):
            readers = [_Worker(lambda: self.get("/api/works")) for _ in range(2)]
            self.assertEqual([reader.join().status for reader in readers], [200, 200])

    def test_mutations_serialize(self) -> None:
        first_inside, release_first, second_inside = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release_first.set)
        original = work_commands.create
        order: list[str] = []

        def held(*args: Any, **kwargs: Any) -> Any:
            if not first_inside.is_set():
                first_inside.set()
                order.append("a")
                self.assertTrue(release_first.wait(10))
            else:
                second_inside.set()
                order.append("b")
            return original(*args, **kwargs)

        with mock.patch.object(work_commands, "create", held):
            first = _Worker(lambda: self.create("A"))
            self.assertTrue(first_inside.wait(10))
            second = _Worker(lambda: self.create("B"))
            self.assertTrue(self.gate.wait_until(lambda s: s["mutation_active"]))
            self.assertFalse(second_inside.wait(0.5), "the second mutation ran beside the first")
            release_first.set()
            first.join()
            second.join()
        self.assertEqual(order, ["a", "b"])
        self.assertEqual(self.titles(), ["A", "B"])

    def test_a_backup_holds_no_gate(self) -> None:
        inside, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def held(_library: Any, _ctx: Any, write_line: Callable[[dict[str, Any]], None]) -> None:
            inside.set()
            self.assertTrue(release.wait(10))
            write_line({"phase": "ready", "percent": 100})

        with mock.patch.object(maintenance_api, "stream_backup", held):
            backup = _Worker(lambda: self.send("POST", "/api/backups/progress", {}))
            self.assertTrue(inside.wait(10))
            snap = self.gate.snapshot()
            self.assertFalse(snap["backup_active"])
            self.assertFalse(snap["mutation_active"])
            self.create("Written during the backup")
            self.assertEqual(self.titles(), ["Written during the backup"])
            release.set()
            res = backup.join()
        self.assertEqual(res.status, 200, res.raw)
        lines = [json.loads(line) for line in res.raw.decode("utf-8").splitlines() if line.strip()]
        self.assertEqual(lines[-1]["phase"], "ready")

    def test_a_read_during_a_restore_waits_and_sees_the_restored_library(self) -> None:
        work = self.create("BEFORE")
        inside, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        read_inside = threading.Event()
        original_read = work_commands.catalog

        def restore(_library: Any, _ctx: Any, _root: str, _data: Any) -> tuple[int, dict[str, Any], None]:
            inside.set()
            self.assertTrue(release.wait(10))
            self.run_sql("UPDATE prks.works SET title = 'RESTORED' WHERE id = %s", (work["id"],))
            return 200, {"status": "restored"}, None

        def read(*args: Any) -> list[dict[str, Any]]:
            read_inside.set()
            return original_read(*args)

        with (mock.patch.object(maintenance_api, "restore_staged", restore),
              mock.patch.object(work_commands, "catalog", read)):
            restoring = _Worker(lambda: self.send("POST", "/api/backups/restore",
                                                  {"token": "0" * 32, "confirm": "RESTORE"}))
            self.assertTrue(inside.wait(10))
            self.assertTrue(self.gate.snapshot()["restore_active"])
            reader = _Worker(lambda: self.get("/api/works"))
            self.assertFalse(read_inside.wait(0.5), "a read ran during the restore")
            release.set()
            self.assertEqual(restoring.join().status, 200)
            res = reader.join()
        self.assertEqual(res.status, 200, res.raw)
        self.assertEqual([row["title"] for row in res.body], ["RESTORED"])

    def test_a_mutation_whose_body_is_still_arriving_holds_no_scope(self) -> None:
        added = threading.Event()
        original = work_commands.create

        def adding(*args: Any, **kwargs: Any) -> Any:
            added.set()
            return original(*args, **kwargs)

        body = json.dumps({"title": "Slow Body Work"}).encode("utf-8")
        head = [f"POST /api/works HTTP/1.1", f"Host: 127.0.0.1:{self.port}", "Content-Type: application/json",
                f"Content-Length: {len(body)}", "Connection: close"]
        head += [f"{name}: {value}" for name, value in
                 self._request_headers("POST", self.cookies, [], self.origin, self.library_epoch())]
        with (mock.patch.object(work_commands, "create", adding),
              socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock):
            sock.sendall(("\r\n".join(head) + "\r\n\r\n").encode("ascii") + body[:8])
            self.assertFalse(self.gate.wait_until(lambda s: s["mutation_active"], timeout=0.5))
            self.assertEqual(self.titles(), [])  # a read is not held up by it
            self.assertFalse(self.gate.snapshot()["mutation_active"])
            self.assertFalse(added.is_set())
            sock.sendall(body[8:])
            raw = b""
            while chunk := sock.recv(65536):
                raw += chunk
        self.assertIn(b" 200 ", raw.partition(b"\r\n")[0])
        self.assertTrue(added.is_set())
        self.assertEqual(self.titles(), ["Slow Body Work"])

    def test_an_unexpected_stage_failure_is_an_internal_error_and_frees_the_gate(self) -> None:
        seen: dict[str, Any] = {}

        def fail(_reader: Any, dest: str, **_kwargs: Any) -> int:
            seen["snap"], seen["path"] = self.gate.snapshot(), dest
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as handle:
                handle.write(b"partial")
            raise OSError("forced")

        with mock.patch.object(self.server_module, "stream_upload_to_file", fail):
            res = self._stage(b"not-a-real-backup")
        self.assertEqual(res.status, 500, res.raw)
        self.assertEqual(res.body.get("error"), "internal_error")
        request_id = res.header("X-Request-ID")
        self.assertTrue(request_id and request_id[0])
        self.assertEqual(res.body.get("request_id"), request_id[0])
        self.assertGreater(seen["snap"]["active_reads"], 0)
        self.assertTrue(self.gate.wait_until(
            lambda s: s["active_reads"] == 0 and not s["mutation_active"] and not s["restore_active"]))
        self.assertFalse(os.path.exists(seen["path"]))
        self.assertEqual(self.titles(), [])

    def _stage(self, data: bytes) -> Response:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.putrequest("POST", "/api/backups/stage", skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", f"127.0.0.1:{self.port}")
            for name, value in self._request_headers("POST", self.cookies, [], self.origin, self.library_epoch()):
                conn.putheader(name, value)
            conn.putheader("Content-Type", "application/octet-stream")
            conn.putheader("Content-Length", str(len(data)))
            conn.endheaders(data)
            res = conn.getresponse()
            return Response(res.status, list(res.getheaders()), res.read())
        finally:
            conn.close()
