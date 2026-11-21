"""Scheduled test results become issues (scripts/ci/scheduled_report.py)."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts.ci import scheduled_report as report
from scripts.ci.scheduled_report import ApiError, MutationResult

ROOT = Path(__file__).resolve().parents[1]
BOT = {"login": "github-actions[bot]", "type": "Bot"}
# Scheduled workflows that are not test runs. CodeQL and zizmor findings go to
# code scanning alerts; the Project #3 jobs sync the board.
NOT_TEST_RUNS = (
    "codeql.yml", "zizmor.yml",
    "project3-agent-queue.yml", "project3-readiness.yml", "project3-view-sync.yml",
)


class FakeGitHub:
    """Just enough of the issues and jobs API, in memory."""

    def __init__(self, jobs: list[dict[str, Any]] | None = None) -> None:
        self.issues: dict[int, dict[str, Any]] = {}
        self.comments: dict[int, dict[str, Any]] = {}
        self.labels: set[str] = set()
        self.jobs = jobs or []
        self.calls: list[tuple[str, str]] = []
        self._next = 1

    def _id(self) -> int:
        self._next += 1
        return self._next

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data: dict[str, Any] = body or {}
        self.calls.append((method, path.split("?", 1)[0]))
        route = path.split("?", 1)[0]
        if method == "GET" and route == "issues":
            label = re.search(r"labels=([^&]+)", path).group(1)  # type: ignore[union-attr]
            return [i for i in self.issues.values() if i["state"] == "open" and label in i["labels"]]
        if method == "POST" and route == "labels":
            if data["name"] in self.labels:
                raise ApiError(422, method, path)
            self.labels.add(data["name"])
            return {}
        if method == "POST" and route == "issues":
            number = self._id()
            self.issues[number] = {"number": number, "state": "open", "comments": [], **data}
            return self.issues[number]
        if match := re.fullmatch(r"issues/(\d+)", route):
            self.issues[int(match.group(1))].update(data)
            return self.issues[int(match.group(1))]
        if match := re.fullmatch(r"issues/(\d+)/comments", route):
            issue = self.issues[int(match.group(1))]
            if method == "GET":
                page = int(re.search(r"[?&]page=(\d+)", path).group(1))  # type: ignore[union-attr]
                return [self.comments[c] for c in issue["comments"]][(page - 1) * 100:page * 100]
            cid = self._id()
            self.comments[cid] = {"id": cid, "body": data["body"], "user": BOT}
            issue["comments"].append(cid)
            return self.comments[cid]
        if match := re.fullmatch(r"issues/comments/(\d+)", route):
            self.comments[int(match.group(1))]["body"] = data["body"]
            return self.comments[int(match.group(1))]
        if re.fullmatch(r"actions/runs/\d+/jobs", route):
            return {"jobs": self.jobs}
        raise AssertionError(f"unexpected {method} {path}")

    def bodies(self, number: int) -> list[str]:
        return [self.comments[c]["body"] for c in self.issues[number]["comments"]]


URL = "https://github.com/o/r/actions/runs/7"


class FailureIssueTests(unittest.TestCase):
    def test_a_failure_opens_one_issue_then_comments_and_a_pass_closes_it(self):
        gh = FakeGitHub(jobs=[
            {"name": "Unit (b)", "conclusion": "failure"},
            {"name": "Unit (a)", "conclusion": "timed_out"},
            {"name": "Lint", "conclusion": "success"},
        ])
        self.assertEqual(report.report_failure(gh, "Nightly", 7, URL, "success"), "nothing to do")
        self.assertEqual(gh.issues, {})

        self.assertEqual(report.report_failure(gh, "Nightly", 7, URL, "failure"), "opened #2")
        issue = gh.issues[2]
        self.assertEqual(issue["title"], "Scheduled check failing: Nightly")
        self.assertEqual(issue["labels"], ["scheduled-check"])
        self.assertIn(URL, issue["body"])
        self.assertIn("- Unit (a)\n- Unit (b)\n", issue["body"])
        self.assertNotIn("Lint", issue["body"])

        self.assertEqual(report.report_failure(gh, "Nightly", 7, URL, "failure"), "commented on #2")
        self.assertEqual(len(gh.issues), 1)
        self.assertTrue(gh.bodies(2)[0].startswith("Failed again."))

        self.assertEqual(report.report_failure(gh, "Nightly", 7, URL, "success"), "closed #2")
        self.assertEqual(issue["state"], "closed")
        self.assertIn("passes again", gh.bodies(2)[-1])

    def test_each_workflow_has_its_own_issue_and_pull_requests_are_ignored(self):
        gh = FakeGitHub()
        gh.issues[50] = {"number": 50, "state": "open", "labels": ["scheduled-check"], "comments": [],
                         "title": "Scheduled check failing: A", "pull_request": {}}
        report.report_failure(gh, "A", 7, URL, "failure")
        report.report_failure(gh, "B", 7, URL, "failure")
        titles = sorted(i["title"] for i in gh.issues.values() if "pull_request" not in i)
        self.assertEqual(titles, ["Scheduled check failing: A", "Scheduled check failing: B"])
        self.assertIn("No job reported a failure", gh.issues[2]["body"])

    def test_an_existing_label_is_reused(self):
        gh = FakeGitHub()
        gh.labels.add("scheduled-check")
        report.report_failure(gh, "A", 7, URL, "failure")
        self.assertEqual(len(gh.issues), 1)

    def test_other_label_errors_propagate(self):
        class Refusing(FakeGitHub):
            def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
                if path == "labels":
                    raise ApiError(403, method, path)
                return super().request(method, path, body)

        with self.assertRaises(ApiError):
            report.report_failure(Refusing(), "A", 7, URL, "failure")


def _result(survived: int, target: str = "sync-envelope") -> MutationResult:
    stats = {"total": 10, "killed": 10 - survived, "survived": survived, "no_tests": 0, "timeout": 0}
    return MutationResult(target, stats, (("backend.sync_protocol.x_f", survived),) if survived else ())


class MutationReportTests(unittest.TestCase):
    def test_the_issue_is_created_once(self):
        gh = FakeGitHub()
        number = report.ensure_mutation_issue(gh)
        self.assertEqual(report.ensure_mutation_issue(gh), number)
        self.assertEqual(gh.issues[number]["labels"], ["mutation-report"])
        self.assertEqual(gh.issues[number]["title"], "Mutation testing report")

    def test_a_target_edits_its_comment_and_notifies_only_when_survivors_rise(self):
        gh = FakeGitHub()
        number = report.ensure_mutation_issue(gh)
        self.assertEqual(report.report_mutation(gh, number, _result(2), URL), "sync-envelope: 2 survivors")
        self.assertEqual(report.report_mutation(gh, number, _result(1, "text-identity"), URL),
                         "text-identity: 1 survivors")
        self.assertEqual(len(gh.bodies(number)), 2)

        # Same or fewer survivors: edited in place, no new comment.
        report.report_mutation(gh, number, _result(2), URL)
        report.report_mutation(gh, number, _result(1), URL)
        self.assertEqual(len(gh.bodies(number)), 2)
        self.assertIn("**1 survived** (last report: 2)", gh.bodies(number)[0])

        self.assertEqual(report.report_mutation(gh, number, _result(3), URL),
                         "sync-envelope: survivors up 1 -> 3")
        bodies = gh.bodies(number)
        self.assertEqual(len(bodies), 3)
        self.assertIn("survivors went from 1 to 3", bodies[2])
        self.assertIn("| `backend.sync_protocol.x_f` | 3 |", bodies[0])
        self.assertTrue(bodies[1].startswith("<!-- mutation-report:text-identity survived=1 -->"))

    def test_a_marker_in_someone_elses_comment_is_not_state(self):
        gh = FakeGitHub()
        number = report.ensure_mutation_issue(gh)
        gh.request("POST", f"issues/{number}/comments", {"body": "x"})
        forged = gh.issues[number]["comments"][0]
        gh.comments[forged] = {"id": forged, "body": "<!-- mutation-report:sync-envelope survived=0 -->",
                               "user": {"login": "someone", "type": "User"}}
        report.report_mutation(gh, number, _result(4), URL)
        self.assertEqual(gh.comments[forged]["body"], "<!-- mutation-report:sync-envelope survived=0 -->")
        self.assertEqual(len(gh.bodies(number)), 2)  # a new sticky, and no "went up" notice

    def test_comments_are_read_past_the_first_page(self):
        gh = FakeGitHub()
        number = report.ensure_mutation_issue(gh)
        report.report_mutation(gh, number, _result(1), URL)
        for _ in range(150):
            gh.request("POST", f"issues/{number}/comments", {"body": "noise"})
        report.report_mutation(gh, number, _result(1), URL)
        self.assertEqual(sum(b.startswith("<!-- mutation-report:") for b in gh.bodies(number)), 1)

    def test_result_files_are_validated(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "r.json"
            path.write_text(json.dumps({
                "target": "docs-only-ci", "stats": {"total": 3, "survived": 1},
                "survivors_by_function": [["scripts.ci.docs_only.x_f", 1]],
            }), encoding="utf-8")
            self.assertEqual(report.load_result(path), MutationResult(
                "docs-only-ci", {"total": 3, "survived": 1}, (("scripts.ci.docs_only.x_f", 1),),
            ))
            for bad in ("Docs Only", "x" * 41, "a -->"):
                path.write_text(json.dumps({"target": bad, "stats": {}, "survivors_by_function": []}),
                                encoding="utf-8")
                with self.subTest(target=bad), self.assertRaises(ValueError):
                    report.load_result(path)

    def test_previous_survivors_reads_only_that_target(self):
        body = "<!-- mutation-report:a survived=3 -->\n<!-- mutation-report:ab survived=9 -->"
        self.assertEqual(report.previous_survivors(body, "a"), 3)
        self.assertEqual(report.previous_survivors(body, "ab"), 9)
        self.assertIsNone(report.previous_survivors(body, "b"))


class WorkflowWiringTests(unittest.TestCase):
    def _text(self, name: str) -> str:
        return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")

    def test_every_scheduled_test_workflow_is_watched(self):
        text = self._text("scheduled-report.yml")
        watched = re.search(r"workflows:\n((?:      - .+\n)+)", text)
        assert watched is not None
        names = [line.strip()[2:] for line in watched.group(1).splitlines()]
        # A new scheduled workflow must be watched here or named as not a test run.
        scheduled = []
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            body = path.read_text(encoding="utf-8")
            if "\n  schedule:" in body and path.name not in NOT_TEST_RUNS:
                scheduled.append(re.search(r"^name: (.+)$", body, re.MULTILINE).group(1))  # type: ignore[union-attr]
        self.assertEqual(sorted(names), sorted(scheduled))
        for name in NOT_TEST_RUNS:
            self.assertTrue((ROOT / ".github" / "workflows" / name).is_file(), name)
        self.assertIn("python3 scripts/ci/scheduled_report.py failure", text)
        # Only scheduled runs: a pull request run never reaches the issue token.
        self.assertIn("github.event.workflow_run.event == 'schedule'", text)
        self.assertNotIn("pull_request", text.split("jobs:", 1)[0].split("on:", 1)[1].split("permissions:", 1)[0])

    def test_mutation_targets_report_to_the_standing_issue(self):
        text = self._text("mutation-testing.yml")
        self.assertIn("scripts/ci/scheduled_report.py ensure-mutation-issue", text)
        self.assertIn('mutation_testing.py run "$TARGET" --json', text)
        self.assertNotIn("--result", text)
        self.assertIn("needs.report-issue.outputs.issue != ''", text)

    def test_both_scripts_agree_on_the_result_file(self):
        from scripts.ci import mutation_testing

        self.assertEqual(report.RESULT_FILE, mutation_testing.RESULT_FILE)
        self.assertEqual(report.RESULT_FILE.relative_to(ROOT).as_posix(), ".tests/mutation-result.json")

    def test_the_workflow_token_is_the_only_trusted_commenter(self):
        self.assertEqual(report.REPORTER_LOGIN, "github-actions[bot]")


if __name__ == "__main__":
    unittest.main()
