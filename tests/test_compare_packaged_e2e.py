"""Regression tests for conservative packaged E2E cost comparisons."""

from __future__ import annotations

import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from scripts.ci.compare_packaged_e2e import InvalidBenchmark, _get_json, _github_connection, compare, fetch_run, summarize

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
        job("Packaged classic bundles (" + "$" + "{{ matrix.shard }}/6)", 0, "skipped"),
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
        with patch("scripts.ci.compare_packaged_e2e.HTTPSConnection") as connection:
            for path in ("file:///etc/passwd", "https://example.org", "//example.org"):
                with self.subTest(path=path):
                    with self.assertRaisesRegex(InvalidBenchmark, "Unexpected GitHub API path"):
                        _get_json(path, None)
            connection.assert_not_called()

    def test_fixed_host_connection_rejects_non_object_response(self):
        with patch("scripts.ci.compare_packaged_e2e.getproxies", return_value={}), \
                patch("scripts.ci.compare_packaged_e2e.HTTPSConnection") as connection:
            response = connection.return_value.getresponse.return_value.__enter__.return_value
            response.status = 200
            with patch("scripts.ci.compare_packaged_e2e.json.load", return_value=[]):
                with self.assertRaisesRegex(InvalidBenchmark, "JSON object"):
                    _get_json("/repos/Fooftilly/PRKS/actions/runs/11", None)
            connection.assert_called_once_with("api.github.com", timeout=30)
            connection.return_value.request.assert_called_once_with(
                "GET", "/repos/Fooftilly/PRKS/actions/runs/11",
                headers={"Accept": "application/vnd.github+json",
                         "User-Agent": "PRKS-E2E-benchmark"},
            )
            connection.return_value.close.assert_called_once()

    def test_fixed_host_proxy_tunnel_and_no_proxy_bypass(self):
        with patch("scripts.ci.compare_packaged_e2e.getproxies",
                   return_value={"https": "http://proxy.example:3128"}), \
             patch("scripts.ci.compare_packaged_e2e.proxy_bypass", return_value=False), \
             patch("scripts.ci.compare_packaged_e2e.HTTPSConnection") as connect:
            connection = _github_connection()
            self.assertIs(connection, connect.return_value)
            connect.assert_called_once_with("proxy.example", 3128, timeout=30)
            connection.set_tunnel.assert_called_once_with("api.github.com", 443)

        with patch("scripts.ci.compare_packaged_e2e.getproxies",
                   return_value={"https": "http://proxy.example:3128"}), \
             patch("scripts.ci.compare_packaged_e2e.proxy_bypass", return_value=True), \
             patch("scripts.ci.compare_packaged_e2e.HTTPSConnection") as connect:
            _github_connection()
            connect.assert_called_once_with("api.github.com", timeout=30)
            connect.return_value.set_tunnel.assert_not_called()

    def test_reject_unsafe_proxy_url_and_bad_port(self):
        for proxy in ("https://proxy.example:443", "http://user:pass@proxy.example:3128",
                      "file:///tmp/proxy", "http://proxy.example:bad"):
            with self.subTest(proxy=proxy), \
                 patch("scripts.ci.compare_packaged_e2e.getproxies",
                       return_value={"https": proxy}), \
                 patch("scripts.ci.compare_packaged_e2e.proxy_bypass", return_value=False):
                with self.assertRaises(InvalidBenchmark):
                    _github_connection()

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
