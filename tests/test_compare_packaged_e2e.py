"""Regression tests for conservative packaged E2E cost comparisons."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts.ci.compare_packaged_e2e import InvalidBenchmark, compare, summarize

SHA = "9" * 40
START = datetime(2026, 10, 17, 21, 0, tzinfo=timezone.utc)


def job(name: str, seconds: int, conclusion: str = "success") -> dict:
    return {
        "name": name,
        "conclusion": conclusion,
        "started_at": START.isoformat(),
        "completed_at": (START + timedelta(seconds=seconds)).isoformat(),
    }


def run(run_id: int, sha: str = SHA) -> dict:
    return {
        "id": run_id,
        "head_sha": sha,
        "event": "workflow_dispatch",
        "conclusion": "success",
        "path": ".github/workflows/e2e-classic-distribution-gate.yml",
    }


def full_jobs() -> list[dict]:
    return [
        job("Docker classic bundle and rollback build smoke", 23),
        job("Classic bundle SW upgrade and rollback (real Chromium)", 20),
        *(job(f"Packaged classic bundles ({n}/6)", seconds)
          for n, seconds in enumerate((471, 494, 522, 477, 538, 471), start=1)),
        job("Packaged classic smoke (manual pilot)", 0, "skipped"),
    ]


def smoke_jobs() -> list[dict]:
    return [
        job("Docker classic bundle and rollback build smoke", 18),
        job("Classic bundle SW upgrade and rollback (real Chromium)", 21),
        job("Packaged classic smoke (manual pilot)", 53),
        job("Packaged classic bundles (skipped matrix)", 0, "skipped"),
    ]


class PackagedCostComparisonTests(unittest.TestCase):
    def test_real_same_sha_sample_uses_all_successful_job_spans(self):
        full = summarize(run(39069624598), full_jobs(), "full")
        smoke = summarize(run(39069996637), smoke_jobs(), "smoke")
        self.assertEqual(full["browser_count"], 6)
        self.assertEqual(smoke["browser_count"], 1)
        self.assertAlmostEqual(full["browser_minutes"], 49.55)
        self.assertAlmostEqual(smoke["browser_minutes"], 53 / 60)
        self.assertAlmostEqual(full["all_job_minutes"], (2973 + 43) / 60)
        self.assertAlmostEqual(smoke["all_job_minutes"], 92 / 60)
        report = compare(full, smoke)
        self.assertIn("98.2%", report)
        self.assertIn("does not establish equivalent regression coverage", report)

    def test_missing_or_duplicate_shard_fails_closed(self):
        for altered in (full_jobs()[:-2] + [full_jobs()[-1]],
                        full_jobs() + [full_jobs()[2]]):
            with self.subTest(job_count=len(altered)):
                with self.assertRaises(InvalidBenchmark):
                    summarize(run(1), altered, "full")

    def test_shared_coverage_cannot_disappear(self):
        for mode, jobs in (("full", full_jobs()), ("smoke", smoke_jobs())):
            for name in (
                "Docker classic bundle and rollback build smoke",
                "Classic bundle SW upgrade and rollback (real Chromium)",
            ):
                with self.subTest(mode=mode, guard=name):
                    with self.assertRaises(InvalidBenchmark):
                        summarize(run(1), [j for j in jobs if j["name"] != name], mode)

    def test_skipped_guards_and_failed_browser_job_rejected(self):
        for mode, jobs in (("full", full_jobs()), ("smoke", smoke_jobs())):
            with self.subTest(mode=mode):
                changed = [dict(j) for j in jobs]
                changed[0]["conclusion"] = "skipped"
                with self.assertRaises(InvalidBenchmark):
                    summarize(run(1), changed, mode)
                changed = [dict(j) for j in jobs]
                selected = "Packaged classic smoke (manual pilot)" if mode == "smoke" else "Packaged classic bundles (1/6)"
                for value in changed:
                    if value["name"] == selected:
                        value["conclusion"] = "failure"
                with self.assertRaises(InvalidBenchmark):
                    summarize(run(1), changed, mode)

    def test_wrong_mode_or_event_rejected(self):
        invalid = run(1)
        invalid["event"] = "pull_request"
        with self.assertRaises(InvalidBenchmark):
            summarize(invalid, full_jobs(), "full")
        invalid = run(1)
        invalid["path"] = ".github/workflows/e2e-gate.yml"
        with self.assertRaises(InvalidBenchmark):
            summarize(invalid, full_jobs(), "full")
        with self.assertRaises(InvalidBenchmark):
            summarize(run(1), full_jobs(), "other")

    def test_partial_jobs_and_invalid_durations_rejected(self):
        with self.assertRaises(InvalidBenchmark):
            summarize(run(1), [], "full")
        selected = full_jobs()
        selected[2]["completed_at"] = selected[2]["started_at"]
        with self.assertRaises(InvalidBenchmark):
            summarize(run(1), selected, "full")
        selected = smoke_jobs()
        selected[-1]["conclusion"] = "success"
        with self.assertRaises(InvalidBenchmark):
            summarize(run(2), selected, "smoke")

    def test_distinct_same_commit_is_required(self):
        full = summarize(run(1), full_jobs(), "full")
        smoke = summarize(run(2, "8" * 40), smoke_jobs(), "smoke")
        with self.assertRaisesRegex(InvalidBenchmark, "same exact SHA"):
            compare(full, smoke)
        smoke["sha"] = SHA
        smoke["run_id"] = 1
        with self.assertRaises(InvalidBenchmark):
            compare(full, smoke)


if __name__ == "__main__":
    unittest.main()
