"""Contracts for the manual-only, privacy-limited E2E shard profile summary."""

from __future__ import annotations

import contextlib
import io
import json
import math
import tempfile
import unittest
from pathlib import Path

from scripts.ci.e2e_profile_summary import main, render_summary


def _report():
    return {
        "schema_version": 2,
        "navigation_samples": {"attempted": 3, "captured": 2},
        "benchmark_modes": ["profile"],
        "workers": 1,
        "tests": 3,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "wall_seconds": 32.0,
        "phase_seconds": {
            "postgres_provision": 9.0,
            "postgres_drop": 6.0,
            "server_start": 5.0,
            "server_stop": 7.0,
            "seed_clone": 2.5,
            "app_ready": 2.0,
        },
    }


class ManualE2EProfileSummaryTests(unittest.TestCase):
    def test_renders_verified_aggregate_only_without_double_counting(self):
        text = render_summary(_report(), shard="2/6")
        self.assertIn("shard 2/6", text)
        self.assertIn("3 passing tests", text)
        self.assertIn("| `postgres_provision` | 9.000 | 3.000 |", text)
        self.assertIn("| `postgres_drop` | 6.000 | 2.000 |", text)
        self.assertIn("2/3", text)
        self.assertIn("Nested phases overlap", text)
        self.assertNotIn("12.0s", text)  # no fabricated sum of the two drop timers

    def test_unobserved_postgres_drop_is_not_zero(self):
        report = _report()
        del report["phase_seconds"]["postgres_drop"]
        text = render_summary(report, shard="1/6")
        self.assertIn("| `postgres_drop` | not observed |", text)
        self.assertNotIn("| `postgres_drop` | 0.000 |", text)

    def test_rejects_incomplete_false_success_and_nonfinite_timings(self):
        for key, bad in (
            ("tests", 0),
            ("tests", True),
            ("workers", 2),
            ("failures", 1),
            ("errors", 1),
            ("skipped", 1),
            ("wall_seconds", math.inf),
            ("wall_seconds", 0),
            ("schema_version", 1),
            ("benchmark_modes", ["no-seed-cache"]),
        ):
            with self.subTest(key=key, bad=bad):
                report = _report()
                report[key] = bad
                with self.assertRaises(ValueError):
                    render_summary(report, shard="1/6")
        for bad in (-1, math.inf, math.nan, True):
            with self.subTest(phase=bad):
                report = _report()
                report["phase_seconds"]["postgres_drop"] = bad
                with self.assertRaises(ValueError):
                    render_summary(report, shard="1/6")

    def test_rejects_path_and_markdown_injection_before_output(self):
        for name in (
            "../../private/log",
            "postgres_drop\\n| secret | 100 |",
            "postgres_drop](/tmp/private)",
            "postgres_drop` | secret |",
            "postgres-drop<script>",
        ):
            with self.subTest(name=name):
                report = _report()
                report["phase_seconds"] = {name: 2.0}
                with self.assertRaises(ValueError):
                    render_summary(report, shard="1/6")

    def test_rejects_bad_navigation_and_shard_labels(self):
        for shard in ("0/6", "7/6", "1/0", "1/13", "1/6\\nsecret", "any"):
            with self.subTest(shard=shard):
                with self.assertRaises(ValueError):
                    render_summary(_report(), shard=shard)
        report = _report()
        report["navigation_samples"] = {"attempted": 1, "captured": 2}
        with self.assertRaises(ValueError):
            render_summary(report, shard="1/6")

    def test_cli_reads_a_private_report_without_echoing_its_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "private-work-title.json"
            report_path.write_text(json.dumps(_report()), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main([str(report_path), "--shard", "1/6"]), 0)
            self.assertNotIn("private-work-title", output.getvalue())
            report_path.write_text("not JSON", encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main([str(report_path), "--shard", "1/6"])
            self.assertEqual(raised.exception.code, 2)

    def test_workflow_limits_summary_to_manual_opt_in(self):
        workflow = (
            Path(__file__).resolve().parents[1] / ".github/workflows/e2e-gate.yml"
        ).read_text(encoding="utf-8")
        section = workflow.split("- name: Run E2E selection shard", 1)[1].split(
            "- name: Report E2E test results", 1
        )[0]
        self.assertIn('if [[ "${PRKS_E2E_PROFILE}" == "1" ]]', section)
        self.assertIn('export PRKS_E2E_BENCHMARK_RESULT="$RUNNER_TEMP/', section)
        self.assertIn("github.event_name == 'workflow_dispatch'", section)
        self.assertIn("inputs.profile &&", section)
        self.assertIn("steps.tests.outcome == 'success'", section)
        self.assertIn('>> "$GITHUB_STEP_SUMMARY"', section)
        self.assertNotIn("upload-artifact", section)


if __name__ == "__main__":
    unittest.main()
