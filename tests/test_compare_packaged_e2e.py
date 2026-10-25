"""Regression tests for conservative packaged E2E cost comparisons."""

from __future__ import annotations

import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from scripts.ci.compare_packaged_e2e import (
    SKIPPED_FULL_MATRIX,
    SMOKE_JOB,
    InvalidBenchmark,
    _get_json,
    compare,
    fetch_run,
    summarize,
    summarize_paired,
    main,
)

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
        "run_attempt": 1,
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
        job(SKIPPED_FULL_MATRIX, 0, "skipped"),
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
        self.assertIn("do not establish equivalent regression coverage", report)


    def test_single_dispatch_paired_full_and_smoke_includes_shared_guards_once(self):
        combined = [
            *full_jobs()[:-1],
            job(SMOKE_JOB, 75),
        ]
        full, smoke = summarize_paired(run(99), combined)
        self.assertEqual(full["browser_count"], 6)
        self.assertEqual(smoke["browser_count"], 1)
        self.assertAlmostEqual(full["browser_minutes"], 49.55)
        self.assertAlmostEqual(smoke["browser_minutes"], 75 / 60)
        self.assertEqual(full["run_id"], smoke["run_id"])
        self.assertAlmostEqual(full["shared_minutes"], 43 / 60)
        self.assertAlmostEqual(smoke["shared_minutes"], 43 / 60)
        self.assertAlmostEqual(full["all_job_minutes"], full["browser_minutes"] + 43 / 60)
        self.assertAlmostEqual(smoke["all_job_minutes"], smoke["browser_minutes"] + 43 / 60)
        report = compare(full, smoke, paired=True)
        self.assertIn("BOTH modes on one SHA", report)
        self.assertIn("guards ran once", report)
        self.assertIn("not two sets of actual billed jobs", report)
        with self.assertRaises(InvalidBenchmark):
            compare(full, smoke)

    def test_compare_mode_refuses_partial_paired_runs(self):
        valid = [*full_jobs()[:-1], job(SMOKE_JOB, 75)]
        failures = [
            valid[:-1],
            valid[:-2] + [valid[-1]],
            valid + [valid[-1]],
            valid + [job("Unaccounted job", 30)],
            valid + [job(SKIPPED_FULL_MATRIX, 0, "skipped")],
            valid + [job(SMOKE_JOB, 0, "skipped")],
        ]
        for altered in failures:
            with self.subTest(names=[j["name"] for j in altered]):
                with self.assertRaises(InvalidBenchmark):
                    summarize_paired(run(88), altered)
        bad = list(valid)
        bad[0] = dict(bad[0], conclusion="failure")
        with self.assertRaises(InvalidBenchmark):
            summarize_paired(run(88), bad)
        invalid_run = run(88)
        invalid_run["event"] = "pull_request"
        with self.assertRaises(InvalidBenchmark):
            summarize_paired(invalid_run, valid)

    def test_paired_cli_fetches_one_run_and_renders_comparison(self):
        valid = [*full_jobs()[:-1], job(SMOKE_JOB, 75)]
        with (
            patch("scripts.ci.compare_packaged_e2e.fetch_run",
                  return_value=(run(777), valid)) as fetch,
            patch("scripts.ci.compare_packaged_e2e.os.environ.get",
                  return_value=None),
            patch("scripts.ci.compare_packaged_e2e.sys.argv",
                  ["compare_packaged_e2e.py", "--paired-run", "777"]),
            patch("builtins.print") as output,
        ):
            self.assertEqual(main(), 0)
        fetch.assert_called_once_with("Fooftilly/PRKS", 777, None)
        self.assertIn("GitHub run #777", output.call_args.args[0])

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

    def test_smoke_rejects_renamed_or_partial_skipped_full_matrix(self):
        for fake_name in ("Packaged classic bundles (renamed/6)",
                          "Packaged classic bundles (1/6)"):
            with self.subTest(fake_name=fake_name):
                incomplete = smoke_jobs()
                incomplete[-1]["name"] = fake_name
                with self.assertRaisesRegex(InvalidBenchmark, "skipped full matrix"):
                    summarize(run(1), incomplete, "smoke")
        duplicated = smoke_jobs()
        duplicated.append(dict(duplicated[-1]))
        with self.assertRaisesRegex(InvalidBenchmark, "skipped full matrix"):
            summarize(run(1), duplicated, "smoke")

    def test_malformed_jobs_fail_with_explanation(self):
        for invalid in ([None], ["not a job"], [smoke_jobs()[0], None]):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(InvalidBenchmark, "must be JSON objects"):
                    summarize(run(1), invalid, "smoke")
        with self.assertRaisesRegex(InvalidBenchmark, "Workflow run must be a JSON object"):
            summarize(None, smoke_jobs(), "smoke")

    def test_refuses_rerun_attempts_and_unknown_attempts(self):
        for attempt in (2, None):
            with self.subTest(attempt=attempt):
                payload = run(4)
                if attempt is None:
                    del payload["run_attempt"]
                else:
                    payload["run_attempt"] = attempt
                with self.assertRaisesRegex(InvalidBenchmark, "Re-run attempts"):
                    summarize(payload, full_jobs(), "full")

    def test_fetch_run_jobs_then_summarize_and_compare(self):
        base = "/repos/Fooftilly/PRKS/actions/runs/"
        payloads = {
            base + "11": run(11),
            base + "11/jobs?per_page=100&page=1": {"jobs": full_jobs()},
            base + "12": run(12),
            base + "12/jobs?per_page=100&page=1": {"jobs": smoke_jobs()},
        }
        with patch("scripts.ci.compare_packaged_e2e._get_json",
                   side_effect=lambda url, _token: payloads[url]) as get_json:
            fetched_full, fetched_full_jobs = fetch_run("Fooftilly/PRKS", 11, None)
            fetched_smoke, fetched_smoke_jobs = fetch_run("Fooftilly/PRKS", 12, None)
        self.assertEqual(get_json.call_count, 4)
        report = compare(
            summarize(fetched_full, fetched_full_jobs, "full"),
            summarize(fetched_smoke, fetched_smoke_jobs, "smoke"),
        )
        self.assertIn("98.2%", report)

    def test_refuses_non_api_paths_without_issuing_network_requests(self):
        with patch("scripts.ci.compare_packaged_e2e.requests.get") as request:
            for path in ("file:///etc/passwd", "https://example.org", "//example.org"):
                with self.subTest(path=path):
                    with self.assertRaisesRegex(InvalidBenchmark, "Unexpected GitHub API path"):
                        _get_json(path, None)
            request.assert_not_called()

    def test_proxy_aware_requests_transport_pins_origin_and_tls(self):
        with patch("scripts.ci.compare_packaged_e2e.requests.get") as request:
            request.return_value.status_code = 200
            request.return_value.json.return_value = {"id": 11}
            data = _get_json("/repos/Fooftilly/PRKS/actions/runs/11", None)
            self.assertEqual(data, {"id": 11})
            request.assert_called_once_with(
                "https://api.github.com/repos/Fooftilly/PRKS/actions/runs/11",
                headers={"Accept": "application/vnd.github+json",
                         "User-Agent": "PRKS-E2E-benchmark"},
                timeout=30, verify=True, allow_redirects=False,
            )
            self.assertNotIn("proxies", request.call_args.kwargs)

    def test_auth_token_only_sent_to_fixed_github_origin(self):
        with patch("scripts.ci.compare_packaged_e2e.requests.get") as request:
            request.return_value.status_code = 200
            request.return_value.json.return_value = {"id": 11}
            self.assertEqual(
                _get_json("/repos/Fooftilly/PRKS/actions/runs/11", "test-token"),
                {"id": 11},
            )
            request.assert_called_once_with(
                "https://api.github.com/repos/Fooftilly/PRKS/actions/runs/11",
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "PRKS-E2E-benchmark",
                    "Authorization": "Bearer test-token",
                },
                timeout=30, verify=True, allow_redirects=False,
            )

    def test_refuses_github_http_errors_and_non_object_responses(self):
        with patch("scripts.ci.compare_packaged_e2e.requests.get") as request:
            request.return_value.status_code = 503
            with self.assertRaisesRegex(InvalidBenchmark, "HTTP 503"):
                _get_json("/repos/Fooftilly/PRKS/actions/runs/11", None)
            request.return_value.status_code = 200
            request.return_value.json.return_value = []
            with self.assertRaisesRegex(InvalidBenchmark, "JSON object"):
                _get_json("/repos/Fooftilly/PRKS/actions/runs/11", None)

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
