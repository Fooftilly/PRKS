"""Prove PG facade profile evidence completeness before it becomes an Actions baseline."""
from __future__ import annotations

import copy
import json
import unittest

from scripts.ci.summarize_pg_profile import render, validate

_STAT = {
    "query_fingerprint": "abcdef0123456789",
    "calls": 4,
    "total_exec_ms": 2.34,
    "mean_exec_ms": 0.585,
    "rows": 12,
    "shared_blks_hit": 5,
    "shared_blks_read": 1,
    "shared_cache_hit_percent": 83.33,
    "temp_blks_read": 0,
    "temp_blks_written": 0,
}


def fixture() -> dict:
    rows = []
    for n in (12, 48):
        for name in ("works_list", "works_browse", "work_detail"):
            rows.append({
                "fixture_works": n, "operation": name,
                "repetitions": 4, "completed_samples": 4,
                "correctness_checks": 7, "warmup_calls": 2,
                "first_observed_call_ms": 3.2,
                "first_call_context": "first_call_of_operation_not_cold_start",
                "call_wall_p50_ms": 1.7, "call_wall_p95_ms": 2.2,
                "top_statements": [dict(_STAT)],
            })
    return {
        "tool": "pg_stat_statements",
        "server_version_num": 180006,
        "fixture": "synthetic_works_only",
        "fixture_version": "works-v1",
        "fixture_tiers": [12, 48],
        "backend": "postgresql_facade",
        "measurement": "warm_facade_calls_not_http",
        "readiness": "disposable_pg18_identity_verified",
        "query_fingerprints": "run_local_only; no SQL text is collected",
        "profiles": rows,
    }


class PGProfileSummaryTests(unittest.TestCase):
    def test_valid_fixture_reports_all_six_reads_without_raw_sql(self):
        data = fixture()
        self.assertEqual(len(validate(data)), 6)
        report = render(data)
        self.assertIn("| 48 | works_browse |", report)
        self.assertIn("not authenticated HTTP/API", report)
        self.assertIn("none is a process/DB cold start", report)
        self.assertNotIn("abcdef0123456789", report)
        self.assertNotIn("postgresql://", report)
        self.assertNotIn("SELECT", report)

    def test_missing_duplicate_and_unchecked_samples_fail_closed(self):
        tests = (
            lambda d: d["profiles"].pop(),
            lambda d: d["profiles"].__setitem__(1, d["profiles"][0]),
            lambda d: d["profiles"][0].__setitem__("completed_samples", 3),
            lambda d: d["profiles"][0].__setitem__("correctness_checks", 6),
            lambda d: d["profiles"][0].__setitem__("top_statements", []),
        )
        for modify in tests:
            with self.subTest(modify=str(modify)):
                data = fixture()
                modify(data)
                with self.assertRaises(ValueError):
                    render(data)

    def test_sql_text_disclosure_is_never_published(self):
        data = fixture()
        data["profiles"][0]["top_statements"][0]["query"] = "SELECT * FROM private_user_works"
        with self.assertRaisesRegex(ValueError, "disclosure"):
            render(data)

    def test_unknown_metadata_and_row_fields_cannot_reach_json_logs(self):
        data = fixture()
        data["injected_source"] = "private filename"
        with self.assertRaisesRegex(ValueError, "disclosure"):
            render(data)
        data = fixture()
        data["profiles"][0]["query"] = "SELECT secret"
        with self.assertRaisesRegex(ValueError, "profiler row"):
            render(data)


    def test_unusable_scope_missing_readiness_or_tiers_fail(self):
        cases = (
            ("backend", "sqlite"),
            ("readiness", "not_ready"),
            ("measurement", "http_latency"),
            ("fixture_tiers", [12, 12]),
            ("fixture_version", "unknown"),
        )
        for key, value in cases:
            with self.subTest(key=key):
                data = fixture()
                data[key] = value
                with self.assertRaises(ValueError):
                    validate(data)

    def test_non_finite_or_negative_durations_rejected(self):
        for bad in (float("nan"), float("inf"), -1, "0.7", True):
            with self.subTest(bad=bad):
                data = fixture()
                data["profiles"][0]["call_wall_p95_ms"] = bad
                with self.assertRaises(ValueError):
                    validate(data)


if __name__ == "__main__":
    unittest.main()
