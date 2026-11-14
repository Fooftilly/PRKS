"""``/api/diagnostics/performance`` on the PostgreSQL Library (PG-C2 #641).

Each ``Database.command`` or ``Database.query`` transaction counts as one
``db`` call of the request it runs in, recording only its duration and
whether it could write: no SQL or parameter reaches the snapshot, and a
failure to record never fails the transaction. Over HTTP the snapshot keeps
route templates and counts only, never a query string, a PDF name or a
body; the diagnostics routes are left out of the aggregates; reset accepts
only an empty object; the JSON, gzip and thumbnail spans are counted; and
nothing is written under the storage root. These replace the SQLite
``tests/test_performance.py`` cases.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
from typing import Any, Optional
from unittest.mock import patch

from psycopg_pool import PoolTimeout

from backend import performance
from tests.library_adapter import TestLibrary
from tests.pg.http_fixtures import HttpAuthTestCase, Response
from tests.pg.test_search_text import pdf_bytes

SECRET_QUERY = "PRIVATE_RESEARCH_X9Q7"
SECRET_PDF = "PRIVATE_BOOK_X9Q7.pdf"
SECRET_NOTE = "PRIVATE_NOTE_X9Q7"
SECRET_SQL = "PRIVATE_SQL_PARAM_X9Q7"
SNAPSHOT = "/api/diagnostics/performance"
RESET = "/api/diagnostics/performance/reset"


def _dump(value: Any) -> str:
    return json.dumps(value, default=str)


class TransactionTimingTests(HttpAuthTestCase):
    """The ``db`` calls a request's transactions record, without a server."""

    def setUp(self) -> None:
        super().setUp()
        previous = performance.reset_registry_for_tests(performance.PerformanceRegistry())
        self.addCleanup(performance.clear_request)
        self.addCleanup(performance.reset_registry_for_tests, previous)

    def route(self, name: str) -> dict[str, Any]:
        return next(row for row in performance.snapshot()["routes"] if row["route"] == name)

    def test_each_transaction_is_one_call_and_no_sql_parameter_is_kept(self) -> None:
        performance.begin_request("GET", "/api/search")
        row = self.db.query(lambda tx: tx.execute("SELECT %s::text AS v", (SECRET_SQL,)).fetchone())
        self.db.command(lambda tx: tx.execute("SELECT 1"))
        performance.finish_request()
        performance.clear_request()
        self.assertEqual(row, {"v": SECRET_SQL})
        snap = performance.snapshot()
        self.assertNotIn(SECRET_SQL, _dump(snap))
        self.assertEqual(self.route("/api/search")["db_calls"], 2)
        self.assertEqual(snap["counters"].get("db_read"), 1)
        self.assertEqual(snap["counters"].get("db_write"), 1)

    def test_a_failure_to_record_does_not_fail_the_transaction(self) -> None:
        with patch("backend.db.database.record_db_call", side_effect=RuntimeError("boom")):
            row = self.db.query(lambda tx: tx.execute("SELECT 1 AS v").fetchone())
        self.assertEqual(row, {"v": 1})

    def test_a_failed_transaction_is_still_counted(self) -> None:
        performance.begin_request("GET", "/api/works")
        with self.assertRaises(Exception):
            self.db.query(lambda tx: tx.execute("SELECT 1 / 0"))
        performance.finish_request()
        performance.clear_request()
        self.assertEqual(self.route("/api/works")["db_calls"], 1)

    def test_a_borrow_that_times_out_records_no_call(self) -> None:
        performance.begin_request("GET", "/api/tags")
        with patch.object(self.db._pool, "connection", side_effect=PoolTimeout("busy")):
            with self.assertRaises(Exception):
                self.db.query(lambda tx: tx.execute("SELECT 1"))
        performance.finish_request()
        performance.clear_request()
        snap = performance.snapshot()
        self.assertEqual(self.route("/api/tags")["db_calls"], 0)
        self.assertFalse(snap["counters"].get("db_read"))


class PerformanceHttpTests(HttpAuthTestCase):
    def setUp(self) -> None:
        super().setUp()
        module = self.server_module
        previous = module.db
        self.addCleanup(self._restore, previous)
        self.storage = module._bound_storage
        os.makedirs(self.storage.thumbs_dir, exist_ok=True)
        self.facade = TestLibrary(self.db, storage=self.storage)
        module.db = self.facade
        self.serve()
        self.cookies = self.session_cookie()
        performance.reset()

    def _restore(self, previous: Any) -> None:
        self.server_module.db = previous

    def get(self, path: str, headers: Optional[list[tuple[str, str]]] = None) -> Response:
        return self.call("GET", path, cookies=self.cookies, headers=headers)

    def post(self, path: str, body: Any) -> Response:
        return self.call("POST", path, body=body, cookies=self.cookies, epoch=self.library_epoch())

    def post_raw(self, path: str, data: Optional[bytes]) -> Response:
        """POST ``data`` as JSON exactly; ``None`` sends no Content-Length at all."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.putrequest("POST", path, skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", f"127.0.0.1:{self.port}")
            for name, value in self._request_headers("POST", self.cookies, [], self.origin, self.library_epoch()):
                conn.putheader(name, value)
            conn.putheader("Content-Type", "application/json")
            if data is not None:
                conn.putheader("Content-Length", str(len(data)))
            conn.endheaders(data or None)
            res = conn.getresponse()
            return Response(res.status, list(res.getheaders()), res.read())
        finally:
            conn.close()

    def snapshot(self) -> dict[str, Any]:
        res = self.get(SNAPSHOT)
        self.assertEqual(res.status, 200, res.raw)
        return res.body

    def routes(self, snap: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {row["route"]: row for row in snap["routes"]}

    def test_the_snapshot_has_safe_route_fields_and_counts_db_calls(self) -> None:
        self.assertEqual(self.get("/api/works").status, 200)
        self.assertEqual(self.get("/api/folders").status, 200)
        snap = self.snapshot()
        self.assertIn("slow_threshold_ms", snap)
        self.assertIn("uptime_seconds", snap)
        self.assertGreaterEqual(snap["requests"]["total"], 2)
        routes = self.routes(snap)
        self.assertIn("/api/folders", routes)
        works = routes["/api/works"]
        for key in ("count", "avg_ms", "p50_ms", "p95_ms", "max_ms", "avg_db_ms", "db_calls", "db_calls_avg"):
            self.assertIn(key, works)
        self.assertGreaterEqual(works["count"], 1)
        self.assertGreaterEqual(works["db_calls"], 1)

    def test_the_diagnostics_routes_are_left_out_of_the_aggregates(self) -> None:
        self.get("/api/works")
        self.snapshot()
        self.post_raw(RESET, b"[]")  # refused, and still a diagnostics request
        snap = self.snapshot()
        self.assertIn("/api/works", self.routes(snap))
        self.assertNotIn(SNAPSHOT, self.routes(snap))
        self.assertNotIn(RESET, self.routes(snap))

    def test_reset_clears_the_counts_and_the_window(self) -> None:
        self.get("/api/works")
        before = self.snapshot()
        self.assertGreaterEqual(before["requests"]["total"], 1)
        res = self.post(RESET, {})
        self.assertEqual((res.status, res.body.get("status")), (200, "reset"), res.raw)
        after = self.snapshot()
        self.assertEqual(after["requests"]["total"], 0)
        self.assertEqual(after["routes"], [])
        self.assertLessEqual(after["measured_for_seconds"], before["uptime_seconds"])

    def test_reset_refuses_anything_but_an_empty_object(self) -> None:
        self.get("/api/works")
        for raw in (b"[]", b'"no"', b"1", b"null", b'{"unexpected":1}', b"", None):
            with self.subTest(raw=raw):
                res = self.post_raw(RESET, raw)
                self.assertEqual(res.status, 400, res.raw)
                self.assertEqual(res.body.get("code"), "invalid_request", res.raw)
        self.assertIn("/api/works", self.routes(self.snapshot()))

    def test_a_search_query_and_a_pdf_name_never_reach_the_snapshot(self) -> None:
        self.get("/api/search?q=" + SECRET_QUERY)
        self.get("/api/pdfs/" + SECRET_PDF)
        snap = self.snapshot()
        self.assertNotIn(SECRET_QUERY, _dump(snap))
        self.assertNotIn("PRIVATE_BOOK_X9Q7", _dump(snap))
        self.assertIn("/api/search", self.routes(snap))
        self.assertIn("/api/pdfs/:pdf", self.routes(snap))

    def test_a_post_body_is_not_kept(self) -> None:
        res = self.post("/api/works", {"title": SECRET_NOTE, "status": "Planned"})
        self.assertEqual(res.status, 200, res.raw)
        self.assertNotIn(SECRET_NOTE, _dump(self.snapshot()))

    def test_an_instrumentation_failure_does_not_fail_the_request(self) -> None:
        with patch.object(self.server_module, "finish_request", side_effect=RuntimeError("perf boom")):
            self.assertEqual(self.get("/api/works").status, 200)

    def test_json_and_gzip_spans_and_response_bytes(self) -> None:
        self.assertEqual(self.post("/api/works", {"title": "GZIP " + "x" * 2000, "status": "Planned"}).status, 200)
        performance.reset()
        res = self.get("/api/works", headers=[("Accept-Encoding", "gzip")])
        self.assertEqual(res.status, 200)
        self.assertIn("gzip", ",".join(res.header("Content-Encoding")).lower())
        snap = self.snapshot()
        self.assertGreaterEqual(snap["spans"].get("json_encode", {}).get("count", 0), 1)
        self.assertGreaterEqual(snap["spans"].get("gzip", {}).get("count", 0), 1)
        self.assertGreater(self.routes(snap)["/api/works"].get("avg_response_bytes") or 0, 0)
        performance.reset()
        res = self.get("/api/works", headers=[("Accept-Encoding", "identity")])
        self.assertEqual(res.status, 200)
        self.assertNotIn("gzip", ",".join(res.header("Content-Encoding")).lower())
        snap = self.snapshot()
        self.assertEqual(snap["spans"].get("gzip", {}).get("count", 0), 0)
        self.assertGreaterEqual(snap["spans"].get("json_encode", {}).get("count", 0), 1)

    def test_server_timing_names_only_spans(self) -> None:
        res = self.get("/api/search?q=" + SECRET_QUERY)
        self.assertEqual(res.status, 200, res.raw)
        timing = ",".join(res.header("Server-Timing"))
        self.assertTrue(timing, res.raw)
        self.assertNotIn(SECRET_QUERY, timing)
        self.assertNotIn("?", timing)
        for token in filter(None, timing.split(",")):
            name = token.split(";", 1)[0].strip()
            self.assertTrue(name.replace("_", "").isalnum(), timing)
            self.assertIn("dur=", token)

    def test_a_thumbnail_counts_its_cache_miss_hit_and_render(self) -> None:
        payload = {"title": "Thumb Perf Work", "status": "Planned", "file_name": "thumb_perf.pdf",
                   "file_b64": base64.b64encode(pdf_bytes("thumb token")).decode("ascii")}
        res = self.post("/api/works", payload)
        self.assertEqual(res.status, 200, res.raw)
        work_id = res.body["id"]
        performance.reset()
        for _ in range(2):
            res = self.get(f"/api/works/{work_id}/thumbnail?page=1")
            self.assertEqual(res.status, 200, res.raw[:200])
        snap = self.snapshot()
        self.assertGreaterEqual(snap["counters"].get("thumbnail_cache_misses", 0), 1)
        self.assertGreaterEqual(snap["counters"].get("thumbnail_cache_hits", 0), 1)
        self.assertGreaterEqual(snap["spans"].get("thumbnail_render", {}).get("count", 0), 1)

    def test_nothing_is_written_under_the_storage_root(self) -> None:
        self.get("/api/search?q=" + SECRET_QUERY)
        self.post("/api/works", {"title": SECRET_NOTE, "status": "Planned"})
        for _dirpath, _dirnames, filenames in os.walk(self.storage.root):
            for name in filenames:
                for kept in ("performance.db", "metrics.json", "performance.log"):
                    self.assertNotIn(kept, name.lower())
        blob = _dump(self.snapshot())
        for secret in (SECRET_QUERY, SECRET_NOTE, SECRET_SQL):
            self.assertNotIn(secret, blob)
