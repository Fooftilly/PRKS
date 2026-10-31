"""The Processing queue's HTTP routes (L-PR-1 #636; ``docs/postgresql-architecture.md`` §11.5).

``backend/api/processing.py`` served from PostgreSQL the way the cutover will
bind it (:class:`~backend.pg_facade.PostgresLibrary` as ``server.db``): a
signed-in owner uploads raw PDFs with an ``Idempotency-Key``, pages the
queue, edits drafts at a revision, previews the bytes with ``Range``, imports
one item or a batch, and deletes. Answers decided before the body was read
close the connection and never wait for the body. The domain's races and
lifecycle are covered by ``tests/pg/test_processing_queue.py``; these tests
cover the HTTP adaptation.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
import uuid
from typing import Any, Optional
from unittest import mock
from urllib.parse import quote

from backend.api import processing as processing_api
from backend.api_contract import processing_items as contract
from backend.pg_facade import PostgresLibrary
from tests.pg.http_fixtures import HttpAuthTestCase, Response

_MISSING = "PI-" + "0" * 32


def pdf(label: str = "a") -> bytes:
    return b"%PDF-1.4\n% " + label.encode() + b"\n" + b"0" * 64 + b"\n%%EOF\n"


def _conform(body: dict[str, Any]) -> None:
    """Validate a queue answer against its wire model (backend/api_contract/processing_items.py)."""
    if "results" in body:
        contract.ProcessingImportResults.model_validate(body)
    elif "next_cursor" in body:
        contract.ProcessingItemPage.model_validate(body)
    elif "duplicates" in body:
        contract.ProcessingItemUploaded.model_validate(body)
    elif "work_id" in body and "item_id" in body:
        contract.ProcessingItemImported.model_validate(body)
    elif str(body.get("id", "")).startswith("PI-"):
        contract.ProcessingItem.model_validate(body)


class ProcessingHttpTestCase(HttpAuthTestCase):
    def setUp(self) -> None:
        super().setUp()
        module = self.server_module
        previous = module.db
        self.addCleanup(setattr, module, "db", previous)
        module.db = PostgresLibrary(self.db, storage=module._bound_storage)
        self.serve()
        self.cookies = self.session_cookie()

    # -- requests ----------------------------------------------------------------------

    def get(self, path: str, cookies: Optional[dict[str, str]] = None,
            headers: Optional[list[tuple[str, str]]] = None) -> Response:
        return self.call("GET", path, cookies=cookies or self.cookies, headers=headers)

    def send(self, method: str, path: str, body: Any = None,
             cookies: Optional[dict[str, str]] = None) -> Response:
        return self.call(method, path, body=body, cookies=cookies or self.cookies, epoch=self.library_epoch())

    def raw_upload(self, data: bytes, *, key: Optional[str] = "", filename: Optional[str] = "paper.pdf",
                   content_type: Optional[str] = "application/pdf", length: Optional[int] = None,
                   send_body: bool = True, epoch: Any = "", cookies: Optional[dict[str, str]] = None) -> Response:
        """``POST /api/processing-items`` with exactly the given headers; ``send_body=False`` sends none."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.putrequest("POST", processing_api.COLLECTION, skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", f"127.0.0.1:{self.port}")
            headers = self._request_headers("POST", cookies or self.cookies, [], self.origin,
                                            self.library_epoch() if epoch == "" else epoch)
            for name, value in headers:
                conn.putheader(name, value)
            if key is not None:
                conn.putheader("Idempotency-Key", key or uuid.uuid4().hex)
            if filename is not None:
                conn.putheader("PRKS-Filename", quote(filename))
            if content_type is not None:
                conn.putheader("Content-Type", content_type)
            conn.putheader("Content-Length", str(len(data) if length is None else length))
            conn.endheaders(data if send_body else None)
            res = conn.getresponse()
            return Response(res.status, list(res.getheaders()), res.read())
        finally:
            conn.close()

    def upload(self, data: Optional[bytes] = None, **kwargs: Any) -> dict[str, Any]:
        res = self.raw_upload(data or pdf(uuid.uuid4().hex), **kwargs)
        self.assertEqual(res.status, 201, res.raw)
        return res.body["item"]

    def ok(self, res: Response, status: int = 200) -> Any:
        self.assertEqual(res.status, status, (res.raw, self.logs.getvalue()[-3000:]))
        if res.header("Content-Type")[:1] == ["application/json; charset=utf-8"] and isinstance(res.body, dict):
            _conform(res.body)
        return res.body

    def refused(self, res: Response, status: int, code: str) -> dict[str, Any]:
        self.assertEqual(res.status, status, res.raw)
        self.assertEqual(res.body["code"], code, res.raw)
        refusal = contract.ProcessingItemRefusal.model_validate(res.body)
        self.assertIsNotNone(refusal.message, res.raw)
        return res.body

    def assert_closes(self, res: Response) -> None:
        self.assertEqual([value.lower() for value in res.header("Connection")], ["close"])


class UploadTests(ProcessingHttpTestCase):
    def test_an_upload_creates_an_item_and_a_replay_answers_it_without_reading_the_body(self) -> None:
        data = pdf("one")
        key = uuid.uuid4().hex
        res = self.raw_upload(data, key=key, filename="Über paper.pdf")
        body = self.ok(res, 201)
        item = body["item"]
        self.assertTrue(body["created"])
        self.assertEqual(body["duplicates"], {"items": [], "works": [], "more_items": False, "more_works": False})
        self.assertEqual((item["status"], item["original_filename"], item["byte_size"], item["revision"]),
                         ("ready", "Über paper.pdf", len(data), 0))
        self.assertEqual(item["draft"]["title"], "")
        self.assertEqual(res.header("Connection"), [])

        # The body is never sent: the answer must not wait for it.
        replay = self.raw_upload(data, key=key, send_body=False)
        self.assertEqual(self.ok(replay)["item"]["id"], item["id"])
        self.assertFalse(replay.body["created"])
        self.assert_closes(replay)
        self.assertEqual(self.ok(self.get(f"/api/processing-items/{item['id']}"))["id"], item["id"])

    def test_same_bytes_under_another_key_are_a_new_item_with_a_duplicate_warning(self) -> None:
        data = pdf("same")
        first = self.upload(data, filename="paper.pdf")
        res = self.ok(self.raw_upload(data, filename="paper.pdf"), 201)
        self.assertNotEqual(res["item"]["id"], first["id"])
        self.assertEqual(res["duplicates"]["items"], [{"id": first["id"], "original_filename": "paper.pdf"}])

    def test_refusals_before_the_body_close_the_connection(self) -> None:
        data = pdf()
        cases = [
            (self.raw_upload(data, content_type="application/json", send_body=False), 415,
             "unsupported_media_type"),
            (self.raw_upload(data, key=None, send_body=False), 400, "VALIDATION_ERROR"),
            (self.raw_upload(data, key="bad key!", send_body=False), 400, "VALIDATION_ERROR"),
            (self.raw_upload(data, length=3, send_body=False), 415, "PROCESSING_NOT_A_PDF"),
            (self.raw_upload(data, epoch=None, send_body=False), 400, "LIBRARY_EPOCH_REQUIRED"),
        ]
        for res, status, code in cases:
            with self.subTest(code=code):
                self.refused(res, status, code)
                self.assert_closes(res)
        with mock.patch.object(processing_api, "MAX_UPLOAD_BYTES", 10):
            res = self.raw_upload(data, send_body=False)
        self.refused(res, 413, "PROCESSING_UPLOAD_TOO_LARGE")
        self.assert_closes(res)
        self.assertEqual(self.ok(self.get("/api/processing-items"))["items"], [])

    def test_a_filename_that_is_not_utf8_is_refused(self) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.putrequest("POST", processing_api.COLLECTION, skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", f"127.0.0.1:{self.port}")
            for name, value in self._request_headers("POST", self.cookies, [], self.origin, self.library_epoch()):
                conn.putheader(name, value)
            conn.putheader("Idempotency-Key", uuid.uuid4().hex)
            conn.putheader("PRKS-Filename", "%FF.pdf")
            conn.putheader("Content-Type", "application/pdf")
            conn.putheader("Content-Length", "100")
            conn.endheaders()
            reply = conn.getresponse()
            self.assertEqual(reply.status, 400)
            self.assertEqual(json.loads(reply.read())["code"], "VALIDATION_ERROR")
        finally:
            conn.close()

    def test_no_filename_uses_the_fallback(self) -> None:
        item = self.upload(filename=None)
        self.assertEqual(item["original_filename"], "upload.pdf")

    def test_a_body_that_is_not_a_pdf_or_ends_early_is_refused_and_leaves_no_item(self) -> None:
        self.refused(self.raw_upload(b"<html>not a pdf</html>"), 415, "PROCESSING_NOT_A_PDF")
        # Declares more than it sends, then stops writing.
        head = (
            f"POST {processing_api.COLLECTION} HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
            f"Origin: {self.origin}\r\nCookie: prks_session={self.cookies['prks_session']}\r\n"
            f"PRKS-Library-Epoch: {self.library_epoch()}\r\nIdempotency-Key: {uuid.uuid4().hex}\r\n"
            "Content-Type: application/pdf\r\nContent-Length: 500\r\n\r\n"
        ).encode()
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            sock.sendall(head + pdf()[:40])
            sock.shutdown(socket.SHUT_WR)
            raw = b""
            while chunk := sock.recv(65536):
                raw += chunk
        status_line, _, rest = raw.partition(b"\r\n")
        self.assertIn(b" 400 ", status_line)
        self.assertEqual(json.loads(rest.partition(b"\r\n\r\n")[2])["code"], "PROCESSING_UPLOAD_INCOMPLETE")
        self.assertIn(b"Connection: close", rest)
        self.assertEqual(self.ok(self.get("/api/processing-items"))["items"], [])

    def _stalled_upload(self, trickle: bool) -> tuple[bytes, float]:
        """Declare a large body, send its start, then stall (or trickle a byte at a time)."""
        head = (
            f"POST {processing_api.COLLECTION} HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
            f"Origin: {self.origin}\r\nCookie: prks_session={self.cookies['prks_session']}\r\n"
            f"PRKS-Library-Epoch: {self.library_epoch()}\r\nIdempotency-Key: {uuid.uuid4().hex}\r\n"
            "Content-Type: application/pdf\r\nContent-Length: 1000000\r\n\r\n"
        ).encode()
        started = time.monotonic()
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            sock.sendall(head + pdf()[:40])
            if trickle:
                sock.settimeout(0.1)
                raw = b""
                while not raw:
                    try:
                        raw = sock.recv(65536)
                    except TimeoutError:
                        sock.sendall(b"0")
                sock.settimeout(10)
            else:
                raw = sock.recv(65536)
            while chunk := sock.recv(65536):
                raw += chunk
        return raw, time.monotonic() - started

    def assert_timed_out(self, raw: bytes) -> None:
        status_line, _, rest = raw.partition(b"\r\n")
        self.assertIn(b" 408 ", status_line)
        body = json.loads(rest.partition(b"\r\n\r\n")[2])
        contract.ProcessingItemRefusal.model_validate(body)
        self.assertEqual(body["code"], "PROCESSING_UPLOAD_TIMEOUT")
        self.assertIn(b"Connection: close", rest)
        self.assertEqual(self.ok(self.get("/api/processing-items"))["items"], [])

    def test_a_stalled_upload_times_out_and_frees_the_gate_for_a_restore(self) -> None:
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
            raw, elapsed = self._stalled_upload(trickle=False)
        waiter.join(10)
        self.assert_timed_out(raw)
        self.assertLess(elapsed, 5)
        self.assertTrue(restored.is_set())

    def test_a_trickling_upload_misses_its_deadline(self) -> None:
        # Each read gets a byte well within the idle timeout; only the deadline ends it.
        with mock.patch.multiple(processing_api, UPLOAD_IDLE_SECONDS=5.0, UPLOAD_GRACE_SECONDS=0.6,
                                 UPLOAD_MIN_BYTES_PER_SECOND=10**12):
            raw, elapsed = self._stalled_upload(trickle=True)
        self.assert_timed_out(raw)
        self.assertLess(elapsed, 4)

    def test_a_deleted_key_is_refused_on_replay(self) -> None:
        key = uuid.uuid4().hex
        item = self.upload(key=key)
        self.ok(self.send("DELETE", f"/api/processing-items/{item['id']}"))
        res = self.refused(self.raw_upload(pdf(), key=key, send_body=False), 409, "PROCESSING_ITEM_DELETED")
        self.assertEqual(res["item"]["status"], "deleted")


class QueueTests(ProcessingHttpTestCase):
    def test_pages(self) -> None:
        ids = [self.upload()["id"] for _ in range(3)]
        first = self.ok(self.get("/api/processing-items?limit=2"))
        self.assertEqual([one["id"] for one in first["items"]], ids[:2])
        self.assertIsNotNone(first["next_cursor"])
        second = self.ok(self.get(f"/api/processing-items?limit=2&cursor={quote(first['next_cursor'])}"))
        self.assertEqual(([one["id"] for one in second["items"]], second["next_cursor"]), (ids[2:], None))
        for query in ("limit=0", "limit=501", "limit=x", "cursor=nope"):
            with self.subTest(query=query):
                self.refused(self.get(f"/api/processing-items?{query}"), 400, "VALIDATION_ERROR")

    def test_markers_are_not_listed_but_are_readable(self) -> None:
        kept = self.upload()
        gone = self.upload()
        self.ok(self.send("DELETE", f"/api/processing-items/{gone['id']}"))
        self.assertEqual([one["id"] for one in self.ok(self.get("/api/processing-items"))["items"]], [kept["id"]])
        self.assertEqual(self.ok(self.get(f"/api/processing-items/{gone['id']}"))["status"], "deleted")
        self.refused(self.get(f"/api/processing-items/{_MISSING}"), 404, "ENTITY_NOT_FOUND")

    def test_edit_at_a_revision(self) -> None:
        item = self.upload()
        tag = self.ok(self.add_tag(self.cookies))
        path = f"/api/processing-items/{item['id']}"
        self.refused(self.send("PATCH", path, {"title": "No revision"}), 400, "VALIDATION_ERROR")
        edited = self.ok(self.send("PATCH", path, {"revision": 0, "title": " Title ", "tags": [{"id": tag["id"]}]}))
        self.assertEqual((edited["revision"], edited["draft"]["title"]), (1, "Title"))
        self.assertEqual([one["id"] for one in edited["draft"]["tags"]], [tag["id"]])
        stale = self.refused(self.send("PATCH", path, {"revision": 0, "title": "Lost"}), 409, "REVISION_CONFLICT")
        self.assertEqual(stale["item"]["draft"]["title"], "Title")
        self.refused(self.send("PATCH", path, {"revision": 1, "status_draft": "Nope"}), 400, "VALIDATION_ERROR")
        self.refused(self.send("PATCH", f"/api/processing-items/{_MISSING}", {"revision": 0}), 404,
                     "ENTITY_NOT_FOUND")

    def test_preview_with_range_and_head(self) -> None:
        data = pdf("preview")
        item = self.upload(data)
        path = f"/api/processing-items/{item['id']}/pdf"
        whole = self.get(path)
        self.assertEqual((whole.status, whole.raw, whole.header("Content-Type")), (200, data, ["application/pdf"]))
        part = self.get(path, headers=[("Range", "bytes=0-4")])
        self.assertEqual((part.status, part.raw), (206, b"%PDF-"))
        self.assertEqual(part.header("Content-Range"), [f"bytes 0-4/{len(data)}"])
        head = self.call("HEAD", path, cookies=self.cookies)
        self.assertEqual((head.status, head.header("Content-Length"), head.raw), (200, [str(len(data))], b""))
        self.assertEqual(self.get(path, headers=[("Range", f"bytes={len(data)}-")]).status, 416)
        self.ok(self.send("DELETE", f"/api/processing-items/{item['id']}"))
        self.refused(self.get(path), 404, "ENTITY_NOT_FOUND")
        self.assertEqual(self.call("HEAD", path, cookies=self.cookies).status, 404)
        # Read off the socket: a HEAD refusal has headers only.
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            sock.sendall((f"HEAD {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
                          f"Cookie: prks_session={self.cookies['prks_session']}\r\n\r\n").encode())
            raw = b""
            while chunk := sock.recv(65536):
                raw += chunk
        head, _, body = raw.partition(b"\r\n\r\n")
        self.assertIn(b" 404 ", head.split(b"\r\n", 1)[0])
        self.assertNotIn(b"Content-Length: 0\r\n", head + b"\r\n")
        self.assertEqual(body, b"")


class ImportAndDeleteTests(ProcessingHttpTestCase):
    def test_import_once_then_replay(self) -> None:
        item = self.upload(filename="Paper Title.pdf")
        path = f"/api/processing-items/{item['id']}"
        done = self.ok(self.send("POST", f"{path}/import", {}), 201)
        self.assertEqual((done["item_id"], done["created"]), (item["id"], True))
        again = self.ok(self.send("POST", f"{path}/import", {}))
        self.assertEqual((again["work_id"], again["created"]), (done["work_id"], False))
        work = self.ok(self.get(f"/api/works/{done['work_id']}"))
        self.assertEqual(work["title"], "Paper Title")
        marker = self.ok(self.get(path))
        self.assertEqual((marker["status"], marker["imported_work_id"], marker["draft"]),
                         ("imported", done["work_id"], None))
        self.refused(self.send("PATCH", path, {"revision": marker["revision"], "title": "x"}), 409,
                     "PROCESSING_ITEM_IMPORTED")
        self.refused(self.send("DELETE", path), 409, "PROCESSING_ITEM_IMPORTED")
        self.refused(self.get(f"{path}/pdf"), 404, "ENTITY_NOT_FOUND")

    def test_delete_is_idempotent_and_refuses_import(self) -> None:
        item = self.upload()
        path = f"/api/processing-items/{item['id']}"
        marker = self.ok(self.send("DELETE", path))
        self.assertEqual((marker["status"], marker["draft"]), ("deleted", None))
        self.assertEqual(self.ok(self.send("DELETE", path))["status"], "deleted")
        self.refused(self.send("POST", f"{path}/import", {}), 409, "PROCESSING_ITEM_DELETED")
        self.refused(self.send("DELETE", f"/api/processing-items/{_MISSING}"), 404, "ENTITY_NOT_FOUND")

    def test_a_batch_answers_each_item(self) -> None:
        first = self.upload()
        deleted = self.upload()
        second = self.upload()
        self.ok(self.send("DELETE", f"/api/processing-items/{deleted['id']}"))
        ids = [first["id"], deleted["id"], _MISSING, second["id"], first["id"]]
        results = self.ok(self.send("POST", "/api/processing-items/import", {"item_ids": ids}))["results"]
        self.assertEqual([(one["item_id"], one["ok"], one["created"], one["code"]) for one in results], [
            (first["id"], True, True, None),
            (deleted["id"], False, False, "PROCESSING_ITEM_DELETED"),
            (_MISSING, False, False, "ENTITY_NOT_FOUND"),
            (second["id"], True, True, None),
            (first["id"], True, False, None),
        ])
        self.assertEqual(results[1]["item"]["status"], "deleted")
        self.assertEqual(results[0]["work_id"], results[4]["work_id"])
        for body in ({}, {"item_ids": []}, {"item_ids": [1]}, {"item_ids": ["x"] * 501}):
            with self.subTest(body=str(body)[:40]):
                self.refused(self.send("POST", "/api/processing-items/import", body), 400, "VALIDATION_ERROR")


class AccessTests(ProcessingHttpTestCase):
    def test_a_reader_lists_and_previews_and_is_refused_every_write(self) -> None:
        item = self.upload()
        self.user("Reader", library=self.lib, role="reader")
        reader = self.session_cookie("Reader")
        self.assertEqual(len(self.ok(self.get("/api/processing-items", cookies=reader))["items"]), 1)
        self.assertEqual(self.get(f"/api/processing-items/{item['id']}/pdf", cookies=reader).status, 200)
        path = f"/api/processing-items/{item['id']}"
        refused = [
            self.raw_upload(pdf(), cookies=reader, send_body=False),
            self.send("PATCH", path, {"revision": 0, "title": "x"}, cookies=reader),
            self.send("DELETE", path, cookies=reader),
            self.send("POST", f"{path}/import", {}, cookies=reader),
        ]
        for res in refused:
            self.assertEqual(res.status, 403, res.raw)
        self.assert_closes(refused[0])
        self.assertEqual(self.ok(self.get(path))["status"], "ready")

    def test_the_openapi_document_is_served(self) -> None:
        doc = self.ok(self.get("/api/openapi/processing-items.json"))
        self.assertIn("/api/processing-items/{item_id}/import", doc["paths"])


class SqliteRuntimeTests(HttpAuthTestCase):
    def test_the_sqlite_runtime_has_no_queue(self) -> None:
        self.serve(auth=None)
        self.assertEqual(self.call("GET", "/api/processing-items").status, 404)
        res = self.call("POST", "/api/processing-items", headers=[("Content-Type", "application/pdf")])
        self.assertEqual(res.status, 404)
        self.assertEqual(self.call("HEAD", f"/api/processing-items/{_MISSING}/pdf").status, 404)
