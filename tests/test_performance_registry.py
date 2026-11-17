"""The performance registry and its request context (no database, no server).

These are the driver-free parts of the diagnostics behind
``/api/diagnostics/performance``: nearest-rank percentiles, bounded samples
and routes, unknown spans, the injected clock, per-request DB timing that
never bleeds between contexts, and the read/write SQL classification. The
route itself is ``tests/pg/test_http_performance.py``.
"""

import contextvars
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.performance import (
    MAX_ROUTES,
    SAMPLE_LIMIT,
    PerformanceRegistry,
    begin_request,
    classify_sql_write,
    clear_request,
    finish_request,
    percentile_nearest_rank,
    record_db_call,
    reset_registry_for_tests,
    snapshot,
)


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


if __name__ == "__main__":
    unittest.main()
