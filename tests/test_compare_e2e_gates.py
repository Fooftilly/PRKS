"""Offline contract tests for same-SHA source vs packaged E2E cost accounting."""
from __future__ import annotations

import runpy
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
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
        "status": "completed",
        "run_attempt": 1,
        "conclusion": "success",
        "started_at": started.isoformat(),
        "completed_at": (started + timedelta(seconds=duration)).isoformat(),
        "steps": ([{
            "name": step,
            "status": "completed",
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
        packaged = summarize(p, jobs("packaged"), "packaged")
        packaged["event"] = "workflow_dispatch"
        with self.assertRaisesRegex(InvalidBenchmark, "share SHA"):
            compare(source, packaged)

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

    def test_six_shard_created_to_start_lag_and_accounting_are_disjoint(self):
        for mode in ("source", "packaged"):
            observed = jobs(mode)
            for entry in observed:
                if "steps" in entry and entry["steps"]:
                    entry["created_at"] = (
                        datetime.fromisoformat(entry["started_at"]) - timedelta(seconds=12)
                    ).isoformat()
            summary = summarize(
                run(mode), observed, mode,
                source_plan=("full", "true", "6", "true") if mode == "source" else None,
            )
            self.assertEqual(summary["created_to_start_count"], 6)
            self.assertAlmostEqual(summary["created_to_start_minutes"], 1.2)
            self.assertAlmostEqual(summary["test_minutes"], 57)
            self.assertAlmostEqual(summary["non_test_minutes"], 3)
            self.assertAlmostEqual(
                sum(row["test_seconds"] + row["non_test_seconds"]
                    for row in summary["per_shard"]) / 60,
                summary["runner_minutes"],
            )
            self.assertIsNone(summary["workflow_wall_minutes"])
            self.assertAlmostEqual(summary["shard_envelope_minutes"], 650 / 60)
            for row in summary["per_shard"]:
                self.assertEqual(row["created_to_start_seconds"], 12)

    def test_missing_created_to_start_sample_is_unavailable_not_zero(self):
        observed = jobs("source")
        for entry in observed[3:]:
            entry["created_at"] = (
                datetime.fromisoformat(entry["started_at"]) - timedelta(seconds=5)
            ).isoformat()
        summary = summarize(
            run("source"), observed, "source",
            source_plan=("full", "true", "6", "true"),
        )
        self.assertEqual(summary["created_to_start_count"], 6)
        self.assertAlmostEqual(summary["created_to_start_minutes"], 0.5)
        observed[-1].pop("created_at")
        summary = summarize(
            run("source"), observed, "source",
            source_plan=("full", "true", "6", "true"),
        )
        self.assertEqual(summary["created_to_start_count"], 5)
        self.assertIsNone(summary["created_to_start_minutes"])
        self.assertIsNone(summary["per_shard"][-1]["created_to_start_seconds"])
        report = compare(summary, summarize(run("packaged", 12), jobs("packaged"), "packaged"))
        self.assertIn("5/6", report)
        self.assertIn("unavailable", report)

    def test_created_after_start_and_malformed_creation_fail_closed(self):
        for value in (
            (START + timedelta(hours=1)).isoformat(),
            "not-a-date",
            START.replace(tzinfo=None).isoformat(),
        ):
            with self.subTest(value=value):
                observed = jobs("source")
                observed[3]["created_at"] = value
                with self.assertRaises(InvalidBenchmark):
                    summarize(
                        run("source"), observed, "source",
                        source_plan=("full", "true", "6", "true"),
                    )

    def test_overlapping_steps_and_unfinished_steps_fail_closed(self):
        for mode in ("source", "packaged"):
            observed = jobs(mode)
            selected = observed[-1]["steps"][0]
            # A successful setup step must not overlap the browser step.
            overlap = dict(
                name="Install dependencies", conclusion="success", status="completed",
                started_at=(datetime.fromisoformat(selected["started_at"])
                            - timedelta(seconds=5)).isoformat(),
                completed_at=(datetime.fromisoformat(selected["started_at"])
                              + timedelta(seconds=2)).isoformat(),
            )
            observed[-1]["steps"].append(overlap)
            with self.assertRaisesRegex(InvalidBenchmark, "Overlapping"):
                summarize(
                    run(mode), observed, mode,
                    source_plan=("full", "true", "6", "true") if mode == "source" else None,
                )
            observed[-1]["steps"].pop()
            selected["status"] = "in_progress"
            with self.assertRaisesRegex(InvalidBenchmark, "unfinished"):
                summarize(
                    run(mode), observed, mode,
                    source_plan=("full", "true", "6", "true") if mode == "source" else None,
                )

    def test_job_attempt_status_and_run_identity_are_verified(self):
        for key, value in (("run_attempt", 2), ("status", "in_progress"), ("run_id", 999)):
            with self.subTest(key=key):
                observed = jobs("source")
                observed[-1][key] = value
                with self.assertRaises(InvalidBenchmark):
                    summarize(
                        run("source"), observed, "source",
                        source_plan=("full", "true", "6", "true"),
                    )

    def test_reject_missing_active_job_verification_fields(self):
        for mode in ("source", "packaged"):
            for index in (0, -1):  # required guard and browser shard
                for field in ("status", "run_attempt"):
                    with self.subTest(mode=mode, job=index, field=field):
                        sample = jobs(mode)
                        sample[index].pop(field)
                        with self.assertRaisesRegex(InvalidBenchmark, "unverified"):
                            summarize(
                                run(mode), sample, mode,
                                source_plan=("full", "true", "6", "true")
                                if mode == "source" else None,
                            )

    def test_out_of_order_nonoverlapping_steps_are_rejected(self):
        for mode in ("source", "packaged"):
            sample = jobs(mode)
            target = sample[-1]
            job_start = datetime.fromisoformat(target["started_at"])
            # Listed after the test, but timestamps are strictly before it.
            target["steps"].append({
                "name": "Late-listed setup",
                "status": "completed",
                "conclusion": "success",
                "started_at": (job_start + timedelta(seconds=1)).isoformat(),
                "completed_at": (job_start + timedelta(seconds=4)).isoformat(),
            })
            with self.assertRaisesRegex(InvalidBenchmark, "out-of-order"):
                summarize(
                    run(mode), sample, mode,
                    source_plan=("full", "true", "6", "true")
                    if mode == "source" else None,
                )

    def test_nonblocking_mergify_failure_is_counted_in_residual(self):
        sample = jobs("source")
        target = sample[-1]
        job_start = datetime.fromisoformat(target["started_at"])
        optional = {
            "name": "Report E2E test results to Mergify",
            "status": "completed",
            "conclusion": "failure",
            "started_at": (job_start + timedelta(seconds=590)).isoformat(),
            "completed_at": (job_start + timedelta(seconds=595)).isoformat(),
        }
        target["steps"].append(optional)
        result = summarize(
            run("source"), sample, "source",
            source_plan=("full", "true", "6", "true"),
        )
        self.assertEqual(result["shards"], 6)
        self.assertAlmostEqual(result["runner_minutes"], 60)
        self.assertAlmostEqual(result["non_test_minutes"], 3)
        # The exception is only for that source-gate reporting step.
        optional["name"] = "Install runtime and E2E dependencies"
        with self.assertRaisesRegex(InvalidBenchmark, "Failed or incomplete"):
            summarize(
                run("source"), sample, "source",
                source_plan=("full", "true", "6", "true"),
            )
        optional["name"] = "Report E2E test results to Mergify"
        target["steps"][0]["conclusion"] = "failure"
        with self.assertRaisesRegex(InvalidBenchmark, "Missing or unsuccessful test"):
            summarize(
                run("source"), sample, "source",
                source_plan=("full", "true", "6", "true"),
            )
        package = jobs("packaged")
        package[-1]["steps"].append(dict(optional))
        with self.assertRaisesRegex(InvalidBenchmark, "Failed or incomplete"):
            summarize(run("packaged"), package, "packaged")

    def test_workflow_wall_requires_explicit_consistent_completion(self):
        payload = run("source")
        payload["run_started_at"] = START.isoformat()
        payload["completed_at"] = (START + timedelta(seconds=700)).isoformat()
        kwargs = {"source_plan": ("full", "true", "6", "true")}
        summary = summarize(payload, jobs("source"), "source", **kwargs)
        self.assertAlmostEqual(summary["workflow_wall_minutes"], 700 / 60)
        payload["completed_at"] = (START + timedelta(seconds=200)).isoformat()
        with self.assertRaisesRegex(InvalidBenchmark, "Contradictory"):
            summarize(payload, jobs("source"), "source", **kwargs)
        payload["completed_at"] = (START + timedelta(seconds=700)).isoformat()
        payload.pop("run_started_at")
        self.assertIsNone(
            summarize(payload, jobs("source"), "source", **kwargs)["workflow_wall_minutes"]
        )
        payload["completed_at"] = (START + timedelta(seconds=150)).isoformat()
        with self.assertRaisesRegex(InvalidBenchmark, "Contradictory"):
            summarize(payload, jobs("source"), "source", **kwargs)
        payload["completed_at"] = (START + timedelta(seconds=700)).isoformat()
        payload["run_started_at"] = START.isoformat()
        payload.pop("completed_at")
        self.assertIsNone(
            summarize(payload, jobs("source"), "source", **kwargs)["workflow_wall_minutes"]
        )
        payload["run_started_at"] = (START + timedelta(seconds=100)).isoformat()
        with self.assertRaisesRegex(InvalidBenchmark, "Contradictory"):
            summarize(payload, jobs("source"), "source", **kwargs)

    def test_plan_log_proves_full_not_affected_selection(self):
        plan_jobs = jobs("source")
        plan_jobs[0]["id"] = 122
        signed = "https://productionresultssa.blob.core.windows.net/actions-results/log.txt"
        api = Mock(status_code=302, headers={"Location": signed})
        log_content = ("2026-10-24T20:58:29.7819858Z "
                       "mode=full run=true features=[] test_count=847 shards=6 pointer=true\n")
        download = Mock(status_code=200)
        download.iter_content.return_value = [log_content.encode("utf-8")]
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
                    timeout=30, verify=True, allow_redirects=False, stream=True,
                ),
            ])
            download.close.assert_called_once()

            for bad in (
                "mode=affected run=true features=[\"tiling\"] test_count=201 shards=6 pointer=true",
                "mode=full run=true features=[] test_count=847 shards=8 pointer=true",
                "mode=full run=true features=[] test_count=847 shards=6 pointer=false",
                "unknown planner output",
            ):
                with self.subTest(bad=bad):
                    download.iter_content.return_value = [
                        ("2026-10-24T20:58:29.7819858Z " + bad + "\n").encode("utf-8")
                    ]
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

    def test_streaming_log_refuses_oversize_invalid_utf8_and_http_failure(self):
        plan_jobs = jobs("source")
        plan_jobs[0]["id"] = 122
        api = Mock(
            status_code=302,
            headers={"Location": "https://example.org/signed-github-log"},
        )
        download = Mock(status_code=200)
        with patch("scripts.ci.compare_e2e_gates.requests.get") as request:
            request.side_effect = [api, download]
            download.iter_content.return_value = [b"x" * 200_000, b"y" * 56_001]
            with self.assertRaisesRegex(InvalidBenchmark, "Unexpectedly large"):
                verify_source_full_plan("Fooftilly/PRKS", plan_jobs, "test-token")
            download.close.assert_called_once()

            request.side_effect = [api, download]
            download.iter_content.return_value = [b"\xff"]
            with self.assertRaisesRegex(InvalidBenchmark, "Invalid UTF-8"):
                verify_source_full_plan("Fooftilly/PRKS", plan_jobs, "test-token")
            self.assertEqual(download.close.call_count, 2)

            request.side_effect = [api, download]
            download.status_code = 503
            with self.assertRaisesRegex(InvalidBenchmark, "HTTP 503"):
                verify_source_full_plan("Fooftilly/PRKS", plan_jobs, "test-token")
            self.assertEqual(download.close.call_count, 3)

    def test_direct_script_help_resolves_package_imports(self):
        root = Path(__file__).resolve().parents[1]
        script = root / "scripts" / "ci" / "compare_e2e_gates.py"
        # Emulate a fresh direct-script interpreter without invoking subprocess:
        # neither cwd nor a previously imported scripts package can mask a
        # missing repository-root bootstrap in the executable.
        isolated_path = [p for p in sys.path if Path(p or ".").resolve() != root]
        output = StringIO()
        with patch.dict(sys.modules), patch.object(sys, "path", isolated_path):
            for name in (
                "scripts", "scripts.ci", "scripts.ci.compare_packaged_e2e",
                "scripts.ci.compare_e2e_gates",
            ):
                sys.modules.pop(name, None)
            with patch.object(sys, "argv", [str(script), "--help"]), redirect_stdout(output):
                with self.assertRaises(SystemExit) as status:
                    runpy.run_path(str(script), run_name="__main__")
        self.assertEqual(status.exception.code, 0)
        self.assertIn("--source-run", output.getvalue())

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
