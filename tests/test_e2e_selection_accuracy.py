"""E2E selection accuracy report: join shadow selections with the gate's failed tests."""

from __future__ import annotations

import datetime as dt
import io
import json
import re
import unittest
import zipfile
from pathlib import Path
from typing import Any

from scripts.ci import e2e_selection_accuracy as acc

ROOT = Path(__file__).resolve().parents[1]
A = "tests.e2e.test_a.A.test_one"
B = "tests.e2e.test_b.B.test_two"


def _zip(name: str, payload: Any) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, json.dumps(payload))
    return buffer.getvalue()


def _shadow(mode: str, selected=(), plan_mode="full", tests=None) -> dict[str, Any]:
    selection = {"mode": mode, "tests": len(selected) if tests is None else tests, "total": 861,
                 "files": {}}
    if mode == "subset":
        selection["selected"] = list(selected)
    return {"plan": {"mode": plan_mode, "test_count": 40}, "shadow": selection}


def _run(conclusion="failure", **extra) -> dict[str, Any]:
    return {"id": 7, "html_url": "https://run/7", "head_sha": "a" * 40, "conclusion": conclusion,
            "pull_requests": [{"number": 12}], **extra}


class CompareTests(unittest.TestCase):
    def test_a_failure_outside_the_subset_is_a_miss(self):
        row = acc.compare(_run(), _shadow("subset", [A]), {A, B}, True)
        self.assertEqual(row["missed"], [B])
        self.assertEqual(row["status"], "miss")
        self.assertEqual(row["title"], "#12")

    def test_a_failure_inside_the_subset_is_caught(self):
        row = acc.compare(_run(), _shadow("subset", [A, B]), {A}, True)
        self.assertEqual((row["missed"], row["status"]), ([], "ok"))

    def test_a_full_map_selection_misses_nothing(self):
        row = acc.compare(_run(), _shadow("full", tests=861), {A}, True)
        self.assertEqual(row["missed"], [])

    def test_a_skip_misses_every_failure(self):
        row = acc.compare(_run(), _shadow("skip"), {A}, True)
        self.assertEqual(row["missed"], [A])

    def test_gate_tests_follow_the_plan_mode(self):
        self.assertEqual(acc.compare(_run("success"), _shadow("skip", plan_mode="affected"), set(), True)
                         ["gate_tests"], 40)
        self.assertEqual(acc.compare(_run("success"), _shadow("skip", plan_mode="skip"), set(), True)
                         ["gate_tests"], 0)
        self.assertEqual(acc.compare(_run("success"), _shadow("skip", plan_mode="full"), set(), True)
                         ["gate_tests"], 861)

    def test_a_failure_without_every_shards_ids_is_not_called_ok(self):
        row = acc.compare(_run(), _shadow("subset", [A]), {A}, False)
        self.assertEqual(row["status"], "failed, test IDs incomplete")

    def test_no_report_or_no_map_is_not_compared(self):
        self.assertEqual(acc.compare(_run(), None, set(), True)["status"], "no shadow report")
        self.assertEqual(acc.compare(_run(), {"plan": {}, "shadow": None}, set(), True)["status"], "no map")

    def test_a_merged_pull_request_is_named_by_a_safe_title(self):
        row = acc.compare(_run(pull_requests=[], display_title="Fix | the `table` <b>" + "x" * 60),
                          None, set(), True)
        self.assertNotRegex(row["title"], r"[|`<>]")
        self.assertLessEqual(len(row["title"]), 50)


class _Api:
    def __init__(self, routes: dict[str, Any]):
        self.routes = routes
        self.calls: list[tuple[str, str, Any]] = []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        for pattern, value in self.routes.items():
            if re.fullmatch(pattern, f"{method} {path}"):
                return value(body) if callable(value) else value
        raise AssertionError(f"unexpected {method} {path}")


class GateFailureTests(unittest.TestCase):
    def test_reads_every_failing_shards_ids_and_ignores_the_rest(self):
        api = _Api({
            r"GET actions/runs/7/artifacts.*": {"artifacts": [
                {"id": 1, "name": "e2e-shard-2-of-8-log"},
                {"id": 2, "name": "e2e-shard-5-of-8-log"},
                {"id": 3, "name": "e2e-timings-1-of-8"},
                {"id": 4, "name": "e2e-shard-6-of-8-log", "expired": True},
            ]},
            r"GET actions/runs/7/jobs.*": {"jobs": [
                {"name": "E2E (2/8)", "conclusion": "failure"},
                {"name": "E2E (5/8)", "conclusion": "timed_out"},
                {"name": "E2E (1/8)", "conclusion": "success"},
            ]},
        })
        blobs = {1: _zip("e2e-last-failed.json", {"test_ids": [A, "unittest.loader._FailedTest.x"]}),
                 2: _zip("e2e-last-failed.json", {"test_ids": [B]})}
        ids, complete = acc.gate_failures(api, blobs.__getitem__, 7)
        self.assertEqual(ids, {A, B})
        self.assertTrue(complete)

    def test_a_failing_shard_without_ids_leaves_the_result_incomplete(self):
        api = _Api({
            r"GET actions/runs/7/artifacts.*": {"artifacts": [{"id": 1, "name": "e2e-shard-2-of-8-log"}]},
            r"GET actions/runs/7/jobs.*": {"jobs": [{"name": "E2E (2/8)", "conclusion": "failure"},
                                                     {"name": "E2E (3/8)", "conclusion": "failure"}]},
        })
        ids, complete = acc.gate_failures(api, {1: _zip("e2e-last-failed.json", {"test_ids": [A]})}.__getitem__, 7)
        self.assertEqual(ids, {A})
        self.assertFalse(complete)

    def test_a_non_shard_failure_is_incomplete(self):
        api = _Api({r"GET actions/runs/7/artifacts.*": {"artifacts": []},
                    r"GET actions/runs/7/jobs.*": {"jobs": [{"name": "E2E plan", "conclusion": "failure"}]}})
        self.assertEqual(acc.gate_failures(api, {}.__getitem__, 7), (set(), False))


class CollectTests(unittest.TestCase):
    def test_joins_each_gate_run_with_the_shadow_run_on_its_head(self):
        api = _Api({
            r"GET actions/workflows/e2e-selection-shadow\.yml/runs\?.*created=%3E%3D2026-11-14.*": {
                "workflow_runs": [{"id": 50, "head_sha": "a" * 40}, {"id": 40, "head_sha": "a" * 40}]},
            r"GET actions/workflows/e2e-gate\.yml/runs\?event=pull_request.*": {"workflow_runs": [
                _run("success"), {**_run("cancelled"), "id": 8}, {**_run("success"), "id": 9, "head_sha": "b" * 40}]},
            r"GET actions/runs/50/artifacts.*": {"artifacts": [{"id": 5, "name": "e2e-selection-shadow"}]},
        })
        rows = acc.collect(api, {5: _zip("e2e-selection-shadow.json", _shadow("subset", [A]))}.__getitem__,
                           7, dt.date(2026, 11, 21))
        self.assertEqual([(r["run_id"], r["status"]) for r in rows], [(7, "ok"), (9, "no shadow report")])
        # The newest shadow run on a head is the one read; the older one is never fetched.
        self.assertNotIn(("GET", "actions/runs/40/artifacts?per_page=100", None), api.calls)


class ReportTests(unittest.TestCase):
    def _api(self, comments):
        posted: list[str] = []
        edited: list[str] = []
        api = _Api({
            r"GET issues\?state=open&labels=e2e-selection-report.*": [
                {"number": 3, "title": acc.TITLE}],
            r"GET issues/3/comments.*": comments,
            r"POST issues/3/comments": lambda body: posted.append(body["body"]),
            r"PATCH issues/comments/91": lambda body: edited.append(body["body"]),
        })
        return api, posted, edited

    def test_each_miss_is_posted_once_and_the_summary_is_edited(self):
        miss = acc.compare(_run(), _shadow("subset", [A]), {A, B}, True)
        old_miss = {**miss, "run_id": 6}
        ours = {"login": "github-actions[bot]"}
        api, posted, edited = self._api([
            {"id": 91, "user": ours, "body": "<!-- e2e-selection-accuracy:summary -->\nold"},
            {"id": 92, "user": ours, "body": "<!-- e2e-selection-miss:6 -->\nalready posted"},
            # Someone else's marker is not state.
            {"id": 93, "user": {"login": "someone"}, "body": "<!-- e2e-selection-miss:7 -->"},
        ])
        self.assertEqual(acc.report(api, [miss, old_miss], 7, "https://run/1"), "#3: 2 gate runs, 1 new misses")
        self.assertEqual(len(posted), 1)
        self.assertIn("<!-- e2e-selection-miss:7 -->", posted[0])
        self.assertIn(f"`{B}`", posted[0])
        self.assertEqual(len(edited), 1)
        self.assertIn("would not have run: **2**", edited[0])

    def test_a_first_run_posts_the_summary(self):
        api, posted, edited = self._api([])
        acc.report(api, [], 7, "https://run/1")
        self.assertEqual(len(posted), 1)
        self.assertTrue(posted[0].startswith("<!-- e2e-selection-accuracy:summary -->"))
        self.assertEqual(edited, [])


class SummaryTests(unittest.TestCase):
    def test_counts_and_lists_only_compared_runs(self):
        rows = [acc.compare(_run("success"), _shadow("subset", [A, B], plan_mode="full"), set(), True),
                acc.compare({**_run("success"), "id": 9}, None, set(), True)]
        text = acc.summary_comment(rows, 7, "https://run/1")
        self.assertIn("Gate runs on pull requests: 2; with a coverage-map selection: 1", text)
        self.assertIn("Tests the gate ran: 861; the map would have run: 2", text)
        self.assertIn("| #12 | [success](https://run/7) | 861 | subset | 2 | ok |", text)
        self.assertNotIn("https://run/9", text)


class WiringTests(unittest.TestCase):
    def test_only_the_non_pull_request_job_holds_an_issue_token(self):
        text = (ROOT / ".github/workflows/e2e-selection-accuracy.yml").read_text(encoding="utf-8")
        jobs = dict(re.findall(r"^  ([a-z-]+):\n(.*?)(?=^  [a-z-]+:\n|\Z)", text.split("\njobs:\n", 1)[1],
                               re.MULTILINE | re.DOTALL))
        self.assertEqual(sorted(jobs), ["preview", "report"])
        self.assertIn("issues: write", jobs["report"])
        self.assertNotIn("pull_request", jobs["report"].split("runs-on:", 1)[0])
        self.assertNotIn("issues:", jobs["preview"])
        self.assertIn("--dry-run", jobs["preview"])
        self.assertIn("E2E selection accuracy (advisory)",
                      (ROOT / ".github/workflows/scheduled-report.yml").read_text(encoding="utf-8"))
        self.assertNotIn("e2e_selection", (ROOT / ".github/workflows/e2e-gate.yml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
