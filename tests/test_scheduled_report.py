"""Scheduled test results become issues (scripts/ci/scheduled_report.py)."""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
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
            state = re.search(r"state=(\w+)", path).group(1)  # type: ignore[union-attr]
            return [i for i in self.issues.values() if i["state"] == state and label in i["labels"]]
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

        # A failure that comes back reopens the same issue.
        self.assertEqual(report.report_failure(gh, "Nightly", 7, URL, "failure"), "reopened #2")
        self.assertEqual((issue["state"], len(gh.issues)), ("open", 1))
        self.assertTrue(gh.bodies(2)[-1].startswith("Failing again."))

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

    def test_results_are_read_from_the_merged_job_outputs(self):
        def out(target: str, survived: int = 1) -> str:
            return json.dumps({"target": target, "stats": {"total": 3, "survived": survived},
                               "survivors_by_function": [["m.x_f", survived]]})

        outputs = json.dumps({"result-a": out("a"), "result-b": "", "result-c": out("c", 2)})
        found, missing = report.results_from_outputs(outputs, ["a", "b", "c", "d"])
        self.assertEqual(found, [
            MutationResult("a", {"total": 3, "survived": 1}, (("m.x_f", 1),)),
            MutationResult("c", {"total": 3, "survived": 2}, (("m.x_f", 2),)),
        ])
        self.assertEqual(missing, ["b", "d"])
        self.assertEqual(report.results_from_outputs("", ["a"]), ([], ["a"]))
        with self.assertRaisesRegex(ValueError, "result-a holds the result of c"):
            report.results_from_outputs(json.dumps({"result-a": out("c")}), ["a"])

    def test_result_target_names_are_validated(self):
        for bad in ("Docs Only", "x" * 41, "a -->"):
            with self.subTest(target=bad), self.assertRaises(ValueError):
                report.parse_result(json.dumps({"target": bad, "stats": {}, "survivors_by_function": []}))

    def test_the_runner_writes_what_the_reporter_reads(self):
        from scripts.ci import mutation_testing

        names = ["backend.sync_protocol.x_f__mutmut_1", "backend.sync_protocol.x_f__mutmut_2"]
        stats = {"total": 5, "killed": 3, "survived": 2, "no_tests": 0, "timeout": 0}
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "out"
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
                mutation_testing._write_result("sync-envelope", stats, names)
            key, _, value = output.read_text(encoding="utf-8").rstrip("\n").partition("=")
        self.assertEqual(key, "result-sync-envelope")
        found, missing = report.results_from_outputs(json.dumps({key: value}), ["sync-envelope"])
        self.assertEqual(missing, [])
        self.assertEqual(found[0], MutationResult("sync-envelope", stats, (("backend.sync_protocol.x_f", 2),)))

    def test_the_dry_run_fails_when_a_target_handed_over_nothing(self):
        from scripts.ci.mutation_testing import TARGETS

        def outputs(targets: list[str]) -> str:
            return json.dumps({f"result-{name}": json.dumps({
                "target": name, "stats": {"survived": 0}, "survivors_by_function": []}) for name in targets})

        for targets, code in ((sorted(TARGETS), 0), (sorted(TARGETS)[1:], 1)):
            with self.subTest(code=code), mock.patch.dict(os.environ, {"MUTATION_OUTPUTS": outputs(targets)}), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(report.main(["mutation", "--run-id", "1", "--dry-run"]), code)

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

    def test_only_the_non_pull_request_report_job_holds_an_issue_token(self):
        from scripts.ci.mutation_testing import TARGETS

        text = self._text("mutation-testing.yml")
        jobs = dict(re.findall(r"^  ([a-z-]+):\n(.*?)(?=^  [a-z-]+:\n|\Z)", text.split("\njobs:\n", 1)[1],
                               re.MULTILINE | re.DOTALL))
        self.assertEqual(sorted(jobs), ["mutation", "report", "report-preview"])
        self.assertNotIn("issues:", jobs["mutation"])
        self.assertNotIn("issues:", jobs["report-preview"])
        self.assertIn("issues: write", jobs["report"])
        self.assertIn("github.event_name == 'schedule'", jobs["report"])
        self.assertNotIn("pull_request", jobs["report"].split("runs-on:", 1)[0])
        self.assertIn("--dry-run", jobs["report-preview"])
        self.assertIn("github.event_name == 'pull_request'", jobs["report-preview"])
        # One output per target, each set only by its own matrix leg.
        self.assertEqual(
            re.findall(r"result-([a-z-]+): \$\{\{ steps\.run\.outputs\.result-\1 \}\}", jobs["mutation"]),
            sorted(TARGETS),
        )
        self.assertIn('mutation_testing.py run "$TARGET" --json', jobs["mutation"])

    def test_the_workflow_token_is_the_only_trusted_commenter(self):
        self.assertEqual(report.REPORTER_LOGIN, "github-actions[bot]")


if __name__ == "__main__":
    unittest.main()
