import contextvars
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_tests import apply_isolated_test_env

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
apply_isolated_test_env(_PROJECT_DIR)

from backend.db_manager import PRKSDatabase
from backend.performance import (
    MAX_ROUTES,
    SAMPLE_LIMIT,
    PerformanceRegistry,
    begin_request,
    classify_sql_write,
    clear_request,
    finish_request,
    percentile_nearest_rank,
    record_counter,
    record_db_call,
    record_span,
    reset,
    reset_registry_for_tests,
    snapshot,
)
from backend.storage.config import StorageConfig

import backend.server as server_module

SECRET_QUERY = "PRIVATE_RESEARCH_X9Q7"
SECRET_PDF = "PRIVATE_BOOK_X9Q7.pdf"
SECRET_NOTE = "PRIVATE_NOTE_X9Q7"
SECRET_SQL = "PRIVATE_SQL_PARAM_X9Q7"


class SequenceClock:
    def __init__(self, values):
        self._values = list(values)
        self._i = 0

    def __call__(self):
        if self._i >= len(self._values):
            return self._values[-1] if self._values else 0
        value = self._values[self._i]
        self._i += 1
        return value


def _json_dump(obj) -> str:
    return json.dumps(obj, default=str)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


def _pdf_with_text_bytes(text: str) -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text or "")
    out = doc.tobytes()
    doc.close()
    return out


class TestPercentileNearestRank(unittest.TestCase):
    def test_known_sample(self):
        samples = [10.0, 20.0, 30.0, 40.0, 50.0]
        self.assertEqual(percentile_nearest_rank(samples, 50.0), 30.0)
        self.assertEqual(percentile_nearest_rank(samples, 95.0), 50.0)
        self.assertIsNone(percentile_nearest_rank([], 50.0))
        self.assertEqual(percentile_nearest_rank([7.0], 95.0), 7.0)


class TestPerformanceRegistry(unittest.TestCase):
    def test_percentiles_from_controlled_durations(self):
        reg = PerformanceRegistry()
        for dur_ns in (10_000_000, 20_000_000, 30_000_000, 40_000_000, 50_000_000):
            reg.finish(
                method="GET",
                route="/api/works",
                duration_ns=dur_ns,
                status=200,
                response_bytes=100,
                db_calls=2,
                db_ns=dur_ns // 2,
                slow_ms=250.0,
            )
        snap = reg.snapshot()
        route = snap["routes"][0]
        self.assertEqual(route["count"], 5)
        self.assertEqual(route["avg_ms"], 30.0)
        self.assertEqual(route["p50_ms"], 30.0)
        self.assertEqual(route["p95_ms"], 50.0)
        self.assertEqual(route["max_ms"], 50.0)
        self.assertEqual(route["db_calls"], 10)
        self.assertEqual(route["db_calls_avg"], 2.0)
        self.assertEqual(route["measured_db_share_percent"], 50.0)

    def test_recent_sample_bounded(self):
        reg = PerformanceRegistry()
        for i in range(300):
            reg.finish(
                method="GET",
                route="/api/works",
                duration_ns=1_000_000 + i,
                status=200,
                response_bytes=1,
                db_calls=0,
                db_ns=0,
                slow_ms=250.0,
            )
        snap = reg.snapshot()
        self.assertEqual(snap["requests"]["total"], 300)
        self.assertEqual(snap["routes"][0]["count"], 300)
        self.assertLessEqual(len(reg._routes[("GET", "/api/works")].samples), SAMPLE_LIMIT)

    def test_route_cardinality_bounded(self):
        reg = PerformanceRegistry()
        for i in range(200):
            reg.finish(
                method="GET",
                route=f"/api/works/{i}",
                duration_ns=1_000_000,
                status=200,
                response_bytes=1,
                db_calls=0,
                db_ns=0,
                slow_ms=250.0,
            )
        snap = reg.snapshot()
        self.assertLessEqual(len(snap["routes"]), MAX_ROUTES + 1)
        self.assertTrue(
            any(row["route"] == "OTHER" for row in snap["routes"]),
            snap["routes"],
        )

    def test_unknown_span_ignored(self):
        reg = PerformanceRegistry()
        reg.add_span("work_secret_title", 5_000_000)
        snap = reg.snapshot()
        self.assertNotIn("work_secret_title", snap["spans"])

    def test_clock_injection_for_request_duration(self):
        clock = SequenceClock([0, 12_500_000])
        prev = reset_registry_for_tests(PerformanceRegistry(clock=clock))
        try:
            begin_request("GET", "/api/folders")
            finish_request()
            clear_request()
            snap = snapshot()
            route = next(r for r in snap["routes"] if r["route"] == "/api/folders")
            self.assertEqual(route["avg_ms"], 12.5)
            self.assertEqual(route["max_ms"], 12.5)
        finally:
            reset_registry_for_tests(prev)
            clear_request()


class TestContextIsolation(unittest.TestCase):
    def test_db_timing_does_not_bleed(self):
        prev = reset_registry_for_tests(PerformanceRegistry())
        try:
            ctx_a = contextvars.copy_context()
            ctx_b = contextvars.copy_context()
            ctx_a.run(lambda: begin_request("GET", "/api/a"))
            ctx_b.run(lambda: begin_request("GET", "/api/b"))
            ctx_a.run(lambda: record_db_call(10_000_000))
            ctx_b.run(lambda: record_db_call(99_000_000))
            ctx_a.run(lambda: (finish_request(), clear_request()))
            ctx_b.run(lambda: (finish_request(), clear_request()))
            snap = snapshot()
            by_route = {row["route"]: row for row in snap["routes"]}
            self.assertEqual(by_route["/api/a"]["avg_db_ms"], 10.0)
            self.assertEqual(by_route["/api/b"]["avg_db_ms"], 99.0)
            self.assertEqual(by_route["/api/a"]["db_calls"], 1)
            self.assertEqual(by_route["/api/b"]["db_calls"], 1)
        finally:
            reset_registry_for_tests(prev)
            clear_request()


class TestSqlClassification(unittest.TestCase):
    def test_read_vs_write(self):
        self.assertFalse(classify_sql_write("SELECT 1"))
        self.assertFalse(classify_sql_write("  pragma foreign_keys"))
        self.assertTrue(classify_sql_write("INSERT INTO works"))
        self.assertTrue(classify_sql_write("UPDATE works SET title=?"))


class TestExecuteQueryInstrumentation(unittest.TestCase):
    def setUp(self):
        self._prev = reset_registry_for_tests(PerformanceRegistry())
        self._tmpdir = tempfile.mkdtemp(prefix="prks-perf-db-")
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self._tmpdir))

    def tearDown(self):
        reset_registry_for_tests(self._prev)
        clear_request()
        try:
            import shutil

            shutil.rmtree(self._tmpdir, ignore_errors=True)
        except Exception:
            pass

    def test_sql_params_never_enter_snapshot(self):
        begin_request("GET", "/api/search")
        self.db.execute_query("SELECT ? AS v", (SECRET_SQL,))
        finish_request()
        clear_request()
        blob = _json_dump(snapshot())
        self.assertNotIn(SECRET_SQL, blob)
        route = next(r for r in snapshot()["routes"] if r["route"] == "/api/search")
        self.assertGreaterEqual(route["db_calls"], 1)

    def test_record_db_failure_does_not_break_query(self):
        with patch("backend.db_manager.record_db_call", side_effect=RuntimeError("boom")):
            rows = self.db.execute_query("SELECT 1 AS v")
        self.assertEqual(rows[0]["v"], 1)

    def test_processing_scan_span_excludes_list_hydration(self):
        processing_root = self.db.storage.processing_dir
        os.makedirs(processing_root, exist_ok=True)
        with open(os.path.join(processing_root, "span.pdf"), "wb") as handle:
            handle.write(b"%PDF-1.4\n%%EOF\n")
        original = self.db.execute_query
        calls = {"n": 0}

        def wrapped(*args, **kwargs):
            calls["n"] += 1
            return original(*args, **kwargs)

        with patch.object(self.db, "execute_query", side_effect=wrapped):
            rows = self.db.scan_processing_files()
        self.assertEqual(len(rows), 1)
        self.assertEqual(calls["n"], 3)
        snap = snapshot()
        self.assertEqual(snap["spans"].get("processing_scan", {}).get("count"), 1)


class TestPerformanceHTTP(unittest.TestCase):
    @classmethod
    def _wait_for_server_ready(cls, timeout_seconds=8.0):
        deadline = time.time() + timeout_seconds
        last_err = None
        while time.time() < deadline:
            try:
                req = urllib.request.Request(f"{cls._base_url}/api/works")
                with urllib.request.urlopen(req, timeout=1.2) as res:
                    if res.status == 200:
                        return
            except Exception as e:
                last_err = e
            time.sleep(0.1)
        raise RuntimeError(f"Server did not become ready in time: {last_err}")

    @classmethod
    def setUpClass(cls):
        cls._test_port = _find_free_port()
        cls._base_url = f"http://127.0.0.1:{cls._test_port}"
        cls._tmpdir = tempfile.mkdtemp(prefix="prks-perf-http-")
        cfg = StorageConfig.for_testing(os.path.join(cls._tmpdir, "storage"))
        server_module.bind_storage(cfg)
        cls.server_thread = threading.Thread(
            target=server_module.run_server,
            args=(cls._test_port,),
            daemon=True,
        )
        cls.server_thread.start()
        cls._wait_for_server_ready()

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "_tmpdir", None):
            try:
                import shutil

                shutil.rmtree(cls._tmpdir, ignore_errors=True)
            except Exception:
                pass

    def setUp(self):
        reset()

    def _get(self, path, headers=None):
        req = urllib.request.Request(self._base_url + path)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, res.read(), res.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def _post(self, path, payload):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self._base_url + path, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def _snapshot(self):
        status, body, _headers = self._get("/api/diagnostics/performance")
        self.assertEqual(status, 200)
        return json.loads(body.decode())

    def test_snapshot_has_safe_route_fields(self):
        self._get("/api/works")
        self._get("/api/folders")
        snap = self._snapshot()
        self.assertIn("slow_threshold_ms", snap)
        self.assertIn("uptime_seconds", snap)
        self.assertGreaterEqual(snap["requests"]["total"], 2)
        routes = {row["route"]: row for row in snap["routes"]}
        self.assertIn("/api/works", routes)
        self.assertIn("/api/folders", routes)
        works = routes["/api/works"]
        for key in ("count", "avg_ms", "p50_ms", "p95_ms", "max_ms", "avg_db_ms", "db_calls", "db_calls_avg"):
            self.assertIn(key, works)
        self.assertGreaterEqual(works["count"], 1)
        self.assertGreaterEqual(works["db_calls"], 1)

    def test_diagnostics_excluded_from_route_aggregates(self):
        self._get("/api/works")
        self._snapshot()
        self._snapshot()
        snap = self._snapshot()
        routes = [row["route"] for row in snap["routes"]]
        self.assertNotIn("/api/diagnostics/performance", routes)
        self.assertNotIn("/api/diagnostics/performance/reset", routes)

    def test_reset_clears_counts_and_window(self):
        self._get("/api/works")
        before = self._snapshot()
        self.assertGreaterEqual(before["requests"]["total"], 1)
        status, payload = self._post("/api/diagnostics/performance/reset", {})
        self.assertEqual(status, 200)
        self.assertEqual(payload.get("status"), "reset")
        after = self._snapshot()
        self.assertEqual(after["requests"]["total"], 0)
        self.assertEqual(after["routes"], [])
        self.assertLessEqual(after["measured_for_seconds"], before["uptime_seconds"])

    def test_privacy_search_query_and_pdf_name(self):
        self._get("/api/search?q=" + SECRET_QUERY)
        self._get("/api/pdfs/" + SECRET_PDF)
        snap = self._snapshot()
        blob = _json_dump(snap)
        self.assertNotIn(SECRET_QUERY, blob)
        self.assertNotIn("PRIVATE_BOOK_X9Q7", blob)
        routes = {row["route"] for row in snap["routes"]}
        self.assertIn("/api/search", routes)
        self.assertIn("/api/pdfs/:pdf", routes)

    def test_privacy_post_body_not_retained(self):
        self._post(
            "/api/works",
            {"title": SECRET_NOTE, "status": "Planned"},
        )
        snap = self._snapshot()
        self.assertNotIn(SECRET_NOTE, _json_dump(snap))

    def test_instrumentation_failure_does_not_break_request(self):
        with patch.object(
            server_module,
            "finish_request",
            side_effect=RuntimeError("perf boom"),
        ):
            status, _body, _headers = self._get("/api/works")
        self.assertEqual(status, 200)

    def test_json_gzip_spans_and_bytes(self):
        long_title = "GZIP " + ("x" * 2000)
        self._post("/api/works", {"title": long_title, "status": "Planned"})
        reset()
        status, body, headers = self._get(
            "/api/works",
            headers={"Accept-Encoding": "gzip"},
        )
        self.assertEqual(status, 200)
        encoding = (headers.get("Content-Encoding") or "").lower()
        self.assertIn("gzip", encoding)
        snap = self._snapshot()
        spans = snap["spans"]
        self.assertGreaterEqual(spans.get("json_encode", {}).get("count", 0), 1)
        self.assertGreaterEqual(spans.get("gzip", {}).get("count", 0), 1)
        works = next(r for r in snap["routes"] if r["route"] == "/api/works")
        self.assertIsNotNone(works.get("avg_response_bytes"))
        self.assertGreater(works["avg_response_bytes"], 0)
        gzip_before = spans["gzip"]["count"]
        reset()
        status, _body, headers = self._get(
            "/api/works",
            headers={"Accept-Encoding": "identity"},
        )
        self.assertEqual(status, 200)
        self.assertNotIn("gzip", (headers.get("Content-Encoding") or "").lower())
        snap2 = self._snapshot()
        self.assertEqual(snap2["spans"].get("gzip", {}).get("count", 0), 0)
        self.assertGreaterEqual(snap2["spans"].get("json_encode", {}).get("count", 0), 1)
        self.assertGreaterEqual(gzip_before, 1)

    def test_server_timing_is_privacy_safe(self):
        _status, _body, headers = self._get("/api/works")
        timing = headers.get("Server-Timing") or headers.get("server-timing") or ""
        if not timing:
            self.skipTest("Server-Timing not present on this response")
        self.assertNotIn(SECRET_QUERY, timing)
        self.assertNotIn("?", timing)
        for token in timing.split(","):
            name = token.split(";", 1)[0].strip()
            self.assertTrue(name.replace("_", "").isalnum(), timing)
            self.assertIn("dur=", token)

    def test_thumbnail_cache_hit_and_miss(self):
        pdf_bytes = _pdf_with_text_bytes("thumb token")
        status, created = self._post(
            "/api/works",
            {
                "title": "Thumb Perf Work",
                "status": "Planned",
                "file_b64": __import__("base64").b64encode(pdf_bytes).decode("utf-8"),
                "file_name": "thumb_perf.pdf",
            },
        )
        self.assertEqual(status, 200)
        w_id = created["id"]
        reset()
        first, _body, _headers = self._get(f"/api/works/{w_id}/thumbnail?page=1")
        if first not in (200, 404):
            self.fail(f"unexpected thumbnail status {first}")
        if first != 200:
            self.skipTest("thumbnail rendering unavailable")
        second, _body, _headers = self._get(f"/api/works/{w_id}/thumbnail?page=1")
        self.assertEqual(second, 200)
        snap = self._snapshot()
        counters = snap["counters"]
        self.assertGreaterEqual(counters.get("thumbnail_cache_misses", 0), 1)
        self.assertGreaterEqual(counters.get("thumbnail_cache_hits", 0), 1)
        self.assertGreaterEqual(
            snap["spans"].get("thumbnail_render", {}).get("count", 0), 1
        )

    def test_no_performance_storage_file(self):
        root = server_module._bound_storage.root
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                lower = name.lower()
                self.assertNotIn("performance.db", lower)
                self.assertNotIn("metrics.json", lower)
                self.assertNotIn("performance.log", lower)
        blob = _json_dump(self._snapshot())
        self.assertNotIn(SECRET_QUERY, blob)
        self.assertNotIn(SECRET_NOTE, blob)
        self.assertNotIn(SECRET_SQL, blob)

    def test_schema_version_unchanged(self):
        from backend.db_manager import PRKS_SCHEMA_VERSION

        self.assertEqual(PRKS_SCHEMA_VERSION, 11)
