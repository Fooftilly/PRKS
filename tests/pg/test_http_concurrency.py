"""Concurrent requests against the PostgreSQL Library (PG-C2 #641, #1331).

Every request runs on its own thread and borrows its own pooled connection.
No process-local gate orders them: reads overlap, unrelated mutations overlap
and each commits in its own transaction, and a backup or a remote image fetch
holds nothing that an edit waits on. A restore is fenced in PostgreSQL
(``docs/postgresql-architecture.md`` §7.4), not here. A stage upload that fails unexpectedly answers
``500`` with its request id and leaves no partial file.
"""

from __future__ import annotations

import http.client
import json
import os
import threading
from io import BytesIO
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


class ConcurrentHttpTests(ProcessingHttpTestCase):
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

    def test_unrelated_mutations_run_at_once(self) -> None:
        """Two edits are inside their commands together (#1331): nothing in
        this process orders them, and both commit."""
        both = threading.Barrier(2, timeout=10)
        original = work_commands.create

        def together(*args: Any, **kwargs: Any) -> Any:
            both.wait()  # breaks, failing both creates, unless the two overlap
            return original(*args, **kwargs)

        with mock.patch.object(work_commands, "create", together):
            writers = [_Worker(lambda title=title: self.create(title)) for title in ("A", "B")]
            for writer in writers:
                writer.join()
        self.assertEqual(self.titles(), ["A", "B"])

    def test_a_mutation_runs_while_a_read_is_inside_its_command(self) -> None:
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = work_commands.catalog

        def held(*args: Any) -> list[dict[str, Any]]:
            entered.set()
            self.assertTrue(release.wait(10))
            return original(*args)

        with mock.patch.object(work_commands, "catalog", held):
            reader = _Worker(lambda: self.get("/api/works"))
            self.assertTrue(entered.wait(10))
            self.create("Written during a read")
            release.set()
            self.assertEqual(reader.join().status, 200)
        self.assertEqual(self.titles(), ["Written during a read"])

    def test_a_stalled_remote_image_fetch_does_not_block_a_mutation(self) -> None:
        """A saved remote image's first view waits on another site (#1351): it
        holds only the read scope, so an edit runs meanwhile, and the copy is
        still saved once the fetch returns."""
        from backend.media.person_image import PortraitImage, decode_and_transcode
        from PIL import Image
        from urllib.parse import quote

        buf = BytesIO()
        Image.new("RGB", (8, 8), (1, 2, 3)).save(buf, format="PNG")
        encoded = decode_and_transcode(buf.getvalue())
        assert encoded is not None
        figure = "https://figures.example/stalled.png"
        work = self.create("Has a figure")
        self.run_sql("INSERT INTO prks.work_notes (library_id, work_id, kind, body) VALUES (%s, %s, 'research', %s)",
                     (self.lib, work["id"], f"![x]({figure})"))
        fetching, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def stalled(url: str, max_edge: int) -> Any:
            fetching.set()
            self.assertTrue(release.wait(10))
            return PortraitImage(*encoded)

        with mock.patch("backend.api.remote_images.fetch_and_prepare", stalled):
            image = _Worker(lambda: self.get("/api/remote-images?url=" + quote(figure, safe="")))
            self.assertTrue(fetching.wait(10))
            self.create("Written while the image is fetched")
            release.set()
            self.assertEqual(image.join().status, 200)
        self.assertIn("Written while the image is fetched", self.titles())
        self.assertEqual(len(self.run_sql("SELECT 1 FROM prks.remote_images WHERE source_url = %s", (figure,))), 1)

    def test_a_mutation_runs_during_a_backup(self) -> None:
        inside, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def held(_library: Any, _ctx: Any, write_line: Callable[[dict[str, Any]], None]) -> None:
            inside.set()
            self.assertTrue(release.wait(10))
            write_line({"phase": "ready", "percent": 100})

        with mock.patch.object(maintenance_api, "stream_backup", held):
            backup = _Worker(lambda: self.send("POST", "/api/backups/progress", {}))
            self.assertTrue(inside.wait(10))
            self.create("Written during the backup")
            self.assertEqual(self.titles(), ["Written during the backup"])
            release.set()
            res = backup.join()
        self.assertEqual(res.status, 200, res.raw)
        lines = [json.loads(line) for line in res.raw.decode("utf-8").splitlines() if line.strip()]
        self.assertEqual(lines[-1]["phase"], "ready")

    def test_an_unexpected_stage_failure_is_an_internal_error(self) -> None:
        seen: dict[str, Any] = {}

        def fail(_reader: Any, dest: str, **_kwargs: Any) -> int:
            seen["path"] = dest
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as handle:
                handle.write(b"partial")
            raise OSError("forced")

        with mock.patch("backend.api.maintenance.stream_upload_to_file", fail):
            res = self._stage(b"not-a-real-backup")
        self.assertEqual(res.status, 500, res.raw)
        self.assertEqual(res.body.get("error"), "internal_error")
        request_id = res.header("X-Request-ID")
        self.assertTrue(request_id and request_id[0])
        self.assertEqual(res.body.get("request_id"), request_id[0])
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
