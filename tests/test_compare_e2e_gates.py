"""Offline contract tests for same-SHA source vs packaged E2E cost accounting."""
from __future__ import annotations

import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, call, patch

from scripts.ci.compare_e2e_gates import (
    InvalidBenchmark, compare, summarize, verify_source_full_plan,
)

START = datetime(2026, 10, 24, 20, 59, tzinfo=timezone.utc)
SHA = "a" * 40
STEP_NAMES = {
    "source": "Run E2E selection shard",
    "packaged": "Run complete E2E shard on the packaged frontend",
}
GUARDS = {
    "source": ("E2E plan", "E2E pointer capture", "Full E2E gate"),
    "packaged": (
        "Docker classic bundle and rollback build smoke",
        "Classic bundle SW upgrade and rollback (real Chromium)",
    ),
}


def run(mode: str, run_id: int = 11) -> dict:
    return {
        "id": run_id,
        "head_sha": SHA,
        "event": "pull_request",
        "status": "completed",
        "conclusion": "success",
        "run_attempt": 1,
        "path": (
            ".github/workflows/e2e-gate.yml" if mode == "source"
            else ".github/workflows/e2e-classic-distribution-gate.yml"
        ),
    }


def job(name: str, start_offset: int = 0, duration: int = 600, step: str = "") -> dict:
    started = START + timedelta(seconds=start_offset)
    return {
        "name": name,
        "conclusion": "success",
        "started_at": started.isoformat(),
        "completed_at": (started + timedelta(seconds=duration)).isoformat(),
        "steps": ([{
            "name": step,
            "conclusion": "success",
            "started_at": (started + timedelta(seconds=20)).isoformat(),
            "completed_at": (started + timedelta(seconds=duration - 10)).isoformat(),
        }] if step else []),
    }


def jobs(mode: str) -> list[dict]:
    template = "E2E ({}/6)" if mode == "source" else "Packaged classic bundles ({}/6)"
    return [
        *(job(guard) for guard in GUARDS[mode]),
        *(job(template.format(i), i * 10, 600, STEP_NAMES[mode])
          for i in range(1, 7)),
    ]


class CompareFullE2EGatesTests(unittest.TestCase):
    def test_successful_six_shards_measure_test_and_non_test_spans(self):
        source = summarize(run("source"), jobs("source"), "source", source_plan=("full", "true", "6", "true"))
        packaged = summarize(run("packaged", 12), jobs("packaged"), "packaged")
        self.assertEqual(source["shards"], 6)
        self.assertAlmostEqual(source["runner_minutes"], 60)
        self.assertAlmostEqual(source["test_minutes"], 57)
        self.assertAlmostEqual(source["non_test_minutes"], 3)
        self.assertAlmostEqual(source["test_share_percent"], 95)
        self.assertEqual(source["start_skew_seconds"], 50)
        self.assertEqual(packaged["shards"], 6)
        self.assertAlmostEqual(packaged["runner_minutes"], 60)
        self.assertAlmostEqual(packaged["test_minutes"], 57)
        self.assertAlmostEqual(packaged["non_test_minutes"], 3)
        self.assertAlmostEqual(packaged["test_share_percent"], 95)
        self.assertEqual(packaged["start_skew_seconds"], 50)
        report = compare(source, packaged)
        self.assertIn("95.0%", report)
        self.assertIn("source #11; packaged #12", report)
        self.assertIn("not establish equivalence", report)

    def test_reject_mismatched_sha_or_event(self):
        source = summarize(run("source"), jobs("source"), "source", source_plan=("full", "true", "6", "true"))
        p = run("packaged", 12)
        p["head_sha"] = "b" * 40
        with self.assertRaisesRegex(InvalidBenchmark, "share SHA"):
            compare(source, summarize(p, jobs("packaged"), "packaged"))
        p["head_sha"] = SHA
        p["event"] = "workflow_dispatch"
        with self.assertRaisesRegex(InvalidBenchmark, "share SHA"):
            compare(source, summarize(p, jobs("packaged"), "packaged"))

    def test_reject_retry_incomplete_or_wrong_workflow(self):
        for key, value in (
            ("run_attempt", 2), ("status", "in_progress"),
            ("conclusion", "failure"), ("path", ".github/workflows/other.yml"),
            ("event", "workflow_dispatch"),
        ):
            with self.subTest(key=key):
                payload = run("source")
                payload[key] = value
                with self.assertRaises(InvalidBenchmark):
                    summarize(payload, jobs("source"), "source", source_plan=("full", "true", "6", "true"))

    def test_six_shards_distinct_required_and_guarded(self):
        for mode in ("source", "packaged"):
            baseline = jobs(mode)
            for modified in (
                baseline[:-1], baseline + [baseline[-1]],
                [j for j in baseline if j["name"] != GUARDS[mode][0]],
                baseline + [job("E2E (7/8)" if mode == "source"
                                else "Packaged classic bundles (7/8)")],
            ):
                with self.subTest(mode=mode, names=[j["name"] for j in modified]):
                    with self.assertRaises(InvalidBenchmark):
                        summarize(run(mode), modified, mode, source_plan=("full", "true", "6", "true") if mode == "source" else None)

    def test_reject_failed_guard_or_malformed_jobs(self):
        for mode in ("source", "packaged"):
            broken = jobs(mode)
            broken[0]["conclusion"] = "failure"
            with self.assertRaises(InvalidBenchmark):
                summarize(run(mode), broken, mode, source_plan=("full", "true", "6", "true") if mode == "source" else None)
            with self.assertRaisesRegex(InvalidBenchmark, "malformed"):
                summarize(run(mode), jobs(mode) + [None], mode, source_plan=("full", "true", "6", "true") if mode == "source" else None)

    def test_test_step_is_mandatory_and_within_own_job(self):
        for mode in ("source", "packaged"):
            normal = jobs(mode)
            no_step = [dict(j) for j in normal]
            no_step[-1] = dict(no_step[-1], steps=[])
            with self.assertRaisesRegex(InvalidBenchmark, "Missing or unsuccessful"):
                summarize(run(mode), no_step, mode, source_plan=("full", "true", "6", "true") if mode == "source" else None)
            invalid = [dict(j) for j in normal]
            last = dict(invalid[-1])
            step = dict(last["steps"][0])
            step["completed_at"] = (START + timedelta(hours=10)).isoformat()
            last["steps"] = [step]
            invalid[-1] = last
            with self.assertRaisesRegex(InvalidBenchmark, "outside its job"):
                summarize(run(mode), invalid, mode, source_plan=("full", "true", "6", "true") if mode == "source" else None)

    def test_source_full_mode_requires_verified_planner(self):
        with self.assertRaisesRegex(InvalidBenchmark, "was not verified"):
            summarize(run("source"), jobs("source"), "source")

    def test_plan_log_proves_full_not_affected_selection(self):
        plan_jobs = jobs("source")
        plan_jobs[0]["id"] = 122
        signed = "https://productionresultssa.blob.core.windows.net/actions-results/log.txt"
        api = Mock(status_code=302, headers={"Location": signed})
        download = Mock(
            status_code=200,
            text=("2026-10-24T20:58:29.7819858Z "
                  "mode=full run=true features=[] test_count=847 shards=6 pointer=true\n"),
        )
        with patch("scripts.ci.compare_e2e_gates.requests.get") as request:
            request.side_effect = [api, download]
            self.assertEqual(
                verify_source_full_plan("Fooftilly/PRKS", plan_jobs, "test-token"),
                ("full", "true", "6", "true"),
            )
            self.assertEqual(request.call_args_list, [
                call(
                    "https://api.github.com/repos/Fooftilly/PRKS/actions/jobs/122/logs",
                    headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "PRKS-E2E-benchmark",
                        "Authorization": "Bearer test-token",
                    },
                    timeout=30, verify=True, allow_redirects=False,
                ),
                call(
                    signed, headers={"User-Agent": "PRKS-E2E-benchmark"},
                    timeout=30, verify=True, allow_redirects=False,
                ),
            ])

            for bad in (
                "mode=affected run=true features=[\"tiling\"] test_count=201 shards=6 pointer=true",
                "mode=full run=true features=[] test_count=847 shards=8 pointer=true",
                "mode=full run=true features=[] test_count=847 shards=6 pointer=false",
                "unknown planner output",
            ):
                with self.subTest(bad=bad):
                    download.text = "2026-10-24T20:58:29.7819858Z " + bad + "\n"
                    request.side_effect = [api, download]
                    with self.assertRaisesRegex(InvalidBenchmark, "did not prove"):
                        verify_source_full_plan("Fooftilly/PRKS", plan_jobs, "test-token")

    def test_plan_log_requires_token_and_https_signed_redirect(self):
        plan_jobs = jobs("source")
        plan_jobs[0]["id"] = 122
        with patch("scripts.ci.compare_e2e_gates.requests.get") as request:
            with self.assertRaisesRegex(InvalidBenchmark, "TOKEN"):
                verify_source_full_plan("Fooftilly/PRKS", plan_jobs, None)
            request.assert_not_called()
            request.return_value.status_code = 302
            request.return_value.headers = {"Location": "http://example.org/log"}
            with self.assertRaisesRegex(InvalidBenchmark, "Invalid signed"):
                verify_source_full_plan("Fooftilly/PRKS", plan_jobs, "test-token")
            request.assert_called_once()

    def test_direct_script_help_resolves_package_imports(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "ci" / "compare_e2e_gates.py"
        process = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("--source-run", process.stdout)

    def test_fetch_api_pair_is_not_needed_for_reporting(self):
        """No further remote requests occur during local reporting."""
        with patch("scripts.ci.compare_e2e_gates.fetch_run") as fetch, \
             patch("scripts.ci.compare_e2e_gates.verify_source_full_plan") as verify:
            verify.return_value = ("full", "true", "6", "true")
            fetch.side_effect = [
                (run("source"), jobs("source")),
                (run("packaged", 12), jobs("packaged")),
            ]
            from scripts.ci.compare_e2e_gates import main
            with patch("sys.argv", ["compare_e2e_gates.py", "--source-run", "11",
                                    "--packaged-run", "12"]), \
                 patch("builtins.print") as output:
                self.assertEqual(main(), 0)
            self.assertEqual(fetch.call_count, 2)
            verify.assert_called_once()
            self.assertIn("95.0%", output.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
