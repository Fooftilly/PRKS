"""Pure safeguards for the optional PostgreSQL query profiler (#858)."""
from __future__ import annotations

import argparse
import json
import os
import unittest
from unittest.mock import patch

from scripts.pg_stat_profile import percentile, project_statement, run, sizes_arg, snapshot


class ProfileSafetyTests(unittest.TestCase):
    def test_never_runs_outside_disposable_wrapper(self):
        with patch.dict(os.environ, {"PRKS_PG_PROFILE_NONCE": ""}):
            with self.assertRaisesRegex(RuntimeError, "scripts/test-postgres --profile"):
                run((12,), 2)

    def test_profile_refuses_invalid_handoff_before_db_import(self):
        for bad in ("", "123", "f" * 31, "0" * 33):
            with self.subTest(nonce=bad), patch.dict(os.environ, {"PRKS_PG_PROFILE_NONCE": bad}):
                with self.assertRaisesRegex(RuntimeError, "scripts/test-postgres --profile"):
                    run((12,), 2)

    def test_snapshot_never_fetches_sql_text(self):
        class FakeStats:
            def execute(self, statement):
                self.statement = statement
                return self

            def fetchall(self):
                return []

        stats = FakeStats()
        self.assertEqual(snapshot(stats), [])
        self.assertIn("pg_stat_statements(false)", stats.statement)
        self.assertNotRegex(stats.statement.split("FROM", 1)[0], r"\bquery\b")

    def test_bounded_ascending_fixture_tiers(self):
        self.assertEqual(sizes_arg("12,48,128"), (12, 48, 128))
        for text in ("", "1,1", "12,2", "501", "0", "1,2,3,4,5", "1,xx", "-1,5"):
            with self.subTest(text=text), self.assertRaises(argparse.ArgumentTypeError):
                sizes_arg(text)

    def test_wall_percentiles_are_nearest_rank_not_guessed(self):
        self.assertEqual(percentile([9, 1, 5, 3], 0.5), 3)
        self.assertEqual(percentile([9, 1, 5, 3], 0.95), 9)
        with self.assertRaises(ValueError):
            percentile([], 0.5)

    def test_report_is_numeric_only_even_if_sql_in_row(self):
        probe_literal = "example select payload MUST NOT appear in profile output"
        row = {
            "queryid": -1,
            "query": "SELECT '" + probe_literal + "'",
            "calls": 5,
            "total_exec_time": 14.33333,
            "mean_exec_time": 2.866666,
            "rows": 11,
            "shared_blks_hit": 8,
            "shared_blks_read": 2,
            "temp_blks_read": 0,
            "temp_blks_written": 1,
            "dbid": 987,
            "userid": 654,
        }
        public = project_statement(row)
        serialized = json.dumps(public)
        self.assertRegex(public["query_fingerprint"], r"^[0-9a-f]{16}$")
        # Deterministic for the same query and key, distinct for different SQL
        # fingerprints, and unlinkable between profiler runs with different keys.
        self.assertEqual(public["query_fingerprint"], project_statement(row)["query_fingerprint"])
        self.assertNotEqual(
            public["query_fingerprint"],
            project_statement({**row, "queryid": 0})["query_fingerprint"],
        )
        with patch("scripts.pg_stat_profile._RUN_FINGERPRINT_KEY", b"second-run-key"):
            self.assertNotEqual(
                public["query_fingerprint"],
                project_statement(row)["query_fingerprint"],
            )
        self.assertEqual(public["shared_cache_hit_percent"], 80.0)
        self.assertNotIn(probe_literal, serialized)
        self.assertNotIn("query", public)
        self.assertNotIn("dbid", serialized)
        self.assertNotIn("userid", serialized)
        self.assertEqual(public["total_exec_ms"], 14.333)
        self.assertEqual(public["calls"], 5)

    def test_no_cache_access_is_not_misreported_as_zero_hit_rate(self):
        row = {
            "queryid": 3, "calls": 1, "total_exec_time": 0,
            "mean_exec_time": 0, "rows": 0, "shared_blks_hit": 0,
            "shared_blks_read": 0, "temp_blks_read": 0, "temp_blks_written": 0,
        }
        self.assertIsNone(project_statement(row)["shared_cache_hit_percent"])


if __name__ == "__main__":
    unittest.main()
