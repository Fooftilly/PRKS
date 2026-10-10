"""Tests for the native issue-dependency writer (#441).

``scripts/issue_dependency_writer.py`` runs against an in-memory fake of the
GitHub issues and issue-dependencies REST API. No live GitHub API is called.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "issue_dependency_writer.py"
_WORKFLOW = _ROOT / ".github" / "workflows" / "issue-dependency-writer.yml"

_spec = importlib.util.spec_from_file_location("issue_dependency_writer", _SCRIPT)
assert _spec and _spec.loader
writer = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = writer  # dataclasses resolve annotations through it
_spec.loader.exec_module(writer)

REPO = "Fooftilly/PRKS"
API = f"https://api.github.com/repos/{REPO}"


class FakeGitHub:
    """Issues keyed by number; ``edges`` holds (blocked, blocking) numbers."""

    def __init__(self) -> None:
        self.issues: dict[int, dict[str, Any]] = {}
        self.edges: set[tuple[int, int]] = set()
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.post_error: Any = None
        self.post_adds_edge = True
        self.before_post: Any = None

    def add(self, number: int, *, state: str = "open", pr: bool = False) -> None:
        issue: dict[str, Any] = {
            "number": number,
            "id": 1000 + number,
            "state": state,
            "repository_url": API,
        }
        if pr:
            issue["pull_request"] = {"url": "x"}
        self.issues[number] = issue

    def _issue(self, number: int) -> dict[str, Any]:
        if number not in self.issues:
            raise writer.ApiError(404, "GET", f"issues/{number}")
        return self.issues[number]

    def get(self, path: str) -> Any:
        match = re.fullmatch(rf"repos/{REPO}/issues/(\d+)(?:/dependencies/(blocked_by|blocking)\?per_page=100&page=(\d+))?", path)
        assert match, path
        number = int(match.group(1))
        if match.group(2) is None:
            return self._issue(number)
        self._issue(number)
        if int(match.group(3)) > 1:
            return []
        if match.group(2) == "blocked_by":
            return [self.issues[b] for (a, b) in sorted(self.edges) if a == number]
        return [self.issues[a] for (a, b) in sorted(self.edges) if b == number]

    def post(self, path: str, body: dict[str, Any]) -> Any:
        if self.before_post:
            self.before_post()
        self.posts.append((path, body))
        match = re.fullmatch(rf"repos/{REPO}/issues/(\d+)/dependencies/blocked_by", path)
        assert match, path
        blocked = int(match.group(1))
        blocking = next(n for n, i in self.issues.items() if i["id"] == body["issue_id"])
        if self.post_adds_edge:
            self.edges.add((blocked, blocking))
        if self.post_error is not None:
            raise self.post_error
        return self.issues[blocked]


def request(blocked: int = 10, blocking: int = 20, *, apply: bool = False,
            actor: str = "Fooftilly", triggering: str = "Fooftilly",
            justification: str = "#10 needs the #20 save contract first.") -> Any:
    return writer.Request(
        repo=REPO,
        blocked=blocked,
        blocking=blocking,
        justification=justification,
        actor=actor,
        triggering_actor=triggering,
        allowed_actors=frozenset({"fooftilly"}),
        apply=apply,
    )


def run(fake: FakeGitHub, req: Any) -> tuple[int, Any]:
    audit = writer.Audit(req)
    return writer.run(fake, req, audit), audit


class IssueDependencyWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gh = FakeGitHub()
        for number in (10, 20, 30, 40):
            self.gh.add(number)

    def test_dry_run_reports_the_edit_and_writes_nothing(self) -> None:
        status, audit = run(self.gh, request())
        self.assertEqual(status, 0)
        self.assertEqual(audit.outcome, "dry-run")
        self.assertEqual(audit.after, ["#20"])
        self.assertEqual(self.gh.posts, [])
        self.assertEqual(self.gh.edges, set())

    def test_apply_writes_the_numeric_issue_id_and_verifies_both_sides(self) -> None:
        status, audit = run(self.gh, request(apply=True))
        self.assertEqual(status, 0)
        self.assertEqual(audit.outcome, "written")
        self.assertEqual(self.gh.posts, [(f"repos/{REPO}/issues/10/dependencies/blocked_by", {"issue_id": 1020})])
        self.assertEqual(self.gh.edges, {(10, 20)})

    def test_repeated_apply_is_a_no_op(self) -> None:
        run(self.gh, request(apply=True))
        status, audit = run(self.gh, request(apply=True))
        self.assertEqual(status, 0)
        self.assertEqual(audit.outcome, "no-op")
        self.assertEqual(len(self.gh.posts), 1)

    def test_existing_manual_edges_are_kept_and_reported(self) -> None:
        self.gh.edges.add((10, 30))
        status, audit = run(self.gh, request(apply=True))
        self.assertEqual(status, 0)
        self.assertEqual(audit.before, ["#30"])
        self.assertEqual(audit.after, ["#20", "#30"])
        self.assertEqual(self.gh.edges, {(10, 20), (10, 30)})

    def test_edge_that_appears_between_check_and_write_is_a_no_op(self) -> None:
        calls = {"n": 0}
        original = self.gh.get

        def get(path: str) -> Any:
            # The second preflight's blocked_by read sees a concurrent write.
            if path.startswith(f"repos/{REPO}/issues/10/dependencies/blocked_by"):
                calls["n"] += 1
                if calls["n"] == 2:
                    self.gh.edges.add((10, 20))
            return original(path)

        self.gh.get = get  # type: ignore[method-assign]
        status, audit = run(self.gh, request(apply=True))
        self.assertEqual(status, 0)
        self.assertEqual(audit.outcome, "no-op")
        self.assertEqual(self.gh.posts, [])

    def test_write_error_with_edge_present_converges(self) -> None:
        self.gh.post_error = writer.ApiError(422, "POST", "x", "Validation failed")
        status, audit = run(self.gh, request(apply=True))
        self.assertEqual(status, 0)
        self.assertEqual(audit.outcome, "converged")

    def test_write_error_without_edge_fails(self) -> None:
        self.gh.post_error = writer.ApiError(403, "POST", "x", "Forbidden")
        self.gh.post_adds_edge = False
        status, audit = run(self.gh, request(apply=True))
        self.assertEqual(status, 2)
        self.assertEqual(audit.outcome, "failed")
        self.assertIn("403", audit.reason)

    def test_accepted_write_that_is_not_visible_fails(self) -> None:
        self.gh.post_adds_edge = False
        status, audit = run(self.gh, request(apply=True))
        self.assertEqual(status, 2)
        self.assertEqual(audit.outcome, "failed")

    def assertRefused(self, req: Any, fragment: str) -> None:
        status, audit = run(self.gh, req)
        self.assertEqual(status, 1, audit.reason)
        self.assertEqual(audit.outcome, "refused")
        self.assertIn(fragment, audit.reason)
        self.assertEqual(self.gh.posts, [])

    def test_self_dependency_is_refused(self) -> None:
        self.assertRefused(request(10, 10, apply=True), "itself")

    def test_direct_cycle_is_refused(self) -> None:
        self.gh.edges.add((20, 10))
        self.assertRefused(request(apply=True), "cycle: #20 blocked by #10")

    def test_refusal_still_records_the_existing_edges(self) -> None:
        self.gh.edges.update({(10, 30), (20, 10)})  # 10 is blocked by 30; 20 is blocked by 10
        status, audit = run(self.gh, request(10, 20, apply=True))
        self.assertEqual((status, audit.outcome), (1, "refused"))
        self.assertEqual(audit.before, ["#30"])
        self.assertEqual(audit.after, ["#30"])
        self.assertIn("#30", audit.summary_markdown())

    def test_transitive_cycle_is_refused_with_its_path(self) -> None:
        self.gh.edges |= {(20, 30), (30, 40), (40, 10)}
        self.assertRefused(request(apply=True), "#20 blocked by #30 blocked by #40 blocked by #10")

    def test_unrelated_edges_are_not_a_cycle(self) -> None:
        self.gh.edges |= {(20, 30), (40, 10)}
        status, audit = run(self.gh, request(apply=True))
        self.assertEqual((status, audit.outcome), (0, "written"))

    def test_graph_too_large_to_check_is_refused(self) -> None:
        for number in range(100, 100 + writer.MAX_GRAPH_NODES + 2):
            self.gh.add(number)
            self.gh.edges.add((number - 1 if number > 100 else 20, number))
        self.assertRefused(request(apply=True), "exceeds")

    def test_missing_issue_is_refused(self) -> None:
        self.assertRefused(request(10, 99, apply=True), "does not exist")

    def test_pull_request_is_refused(self) -> None:
        self.gh.add(50, pr=True)
        self.assertRefused(request(50, 20, apply=True), "pull request")

    def test_closed_issue_is_refused(self) -> None:
        self.gh.add(60, state="closed")
        self.assertRefused(request(10, 60, apply=True), "closed")

    def test_transferred_issue_is_refused_without_writing(self) -> None:
        # GitHub redirects a transferred issue to its new home; the body then
        # names another repository and number.
        for role, req in (("blocked issue", request(10, 20, apply=True)), ("blocking issue", request(30, 10, apply=True))):
            gh = FakeGitHub()
            for number in (10, 20, 30):
                gh.add(number)
            gh.issues[10] = {
                "number": 77,
                "id": 5077,
                "state": "open",
                "repository_url": "https://api.github.com/repos/Fooftilly/elsewhere",
            }
            status, audit = run(gh, req)
            self.assertEqual((status, audit.outcome), (1, "refused"), role)
            self.assertIn(f"{role} #10 was transferred to Fooftilly/elsewhere#77", audit.reason)
            self.assertEqual(gh.posts, [])

    def test_write_targets_the_checked_issue(self) -> None:
        status, _ = run(self.gh, request(apply=True))
        self.assertEqual(status, 0)
        self.assertEqual(self.gh.posts[0][0], f"repos/{REPO}/issues/10/dependencies/blocked_by")

    def test_unlisted_actor_is_refused(self) -> None:
        self.assertRefused(request(actor="someone-else", apply=True), "not allowed")

    def test_rerun_by_unlisted_actor_is_refused(self) -> None:
        self.assertRefused(request(triggering="someone-else", apply=True), "not allowed")

    def test_short_justification_is_refused(self) -> None:
        self.assertRefused(request(justification="related", apply=True), "justification")

    def test_api_failure_while_checking_fails_without_writing(self) -> None:
        def get(path: str) -> Any:
            raise writer.ApiError(500, "GET", path)

        self.gh.get = get  # type: ignore[method-assign]
        status, audit = run(self.gh, request(apply=True))
        self.assertEqual((status, audit.outcome), (2, "failed"))
        self.assertEqual(self.gh.posts, [])


class MainTests(unittest.TestCase):
    def test_main_prints_one_audit_record_and_writes_the_summary(self) -> None:
        gh = FakeGitHub()
        gh.add(10)
        gh.add(20)
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.md"
            env = {
                "GITHUB_TOKEN": "t",
                "GITHUB_REPOSITORY": REPO,
                "GITHUB_ACTOR": "Fooftilly",
                "GITHUB_TRIGGERING_ACTOR": "Fooftilly",
                "GITHUB_STEP_SUMMARY": str(summary),
            }
            argv = ["--blocked", "#10", "--blocking", "20", "--justification",
                    "  #10 cannot start before #20 lands.  ", "--allowed-actors", ""]
            from contextlib import redirect_stdout
            from io import StringIO

            out = StringIO()
            with redirect_stdout(out):
                status = writer.main(argv, env, lambda token, url: gh)
            self.assertEqual(status, 0)
            record = json.loads(out.getvalue().strip().splitlines()[-1])
            self.assertEqual(record["outcome"], "dry-run")
            self.assertEqual(record["mode"], "dry-run")
            self.assertEqual(record["actor"], "Fooftilly")
            self.assertEqual(record["justification"], "#10 cannot start before #20 lands.")
            self.assertIn("#10 blocked by #20", summary.read_text())

    def test_invalid_number_is_refused_before_any_api_call(self) -> None:
        def factory(token: str, url: str) -> Any:
            raise AssertionError("no client may be built")

        from contextlib import redirect_stdout
        from io import StringIO

        with redirect_stdout(StringIO()):
            status = writer.main(
                ["--blocked", "10; rm -rf /", "--blocking", "20", "--justification", "x" * 20],
                {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": REPO, "GITHUB_ACTOR": "Fooftilly"},
                factory,
            )
        self.assertEqual(status, 1)

    def test_parse_refusal_is_written_to_the_job_summary(self) -> None:
        from contextlib import redirect_stdout
        from io import StringIO

        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.md"
            out = StringIO()
            with redirect_stdout(out):
                status = writer.main(
                    ["--blocked", "ten", "--blocking", "20", "--justification", "x" * 20],
                    {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": REPO, "GITHUB_STEP_SUMMARY": str(summary)},
                    lambda token, url: FakeGitHub(),
                )
            self.assertEqual(status, 1)
            self.assertEqual(json.loads(out.getvalue().strip())["outcome"], "refused")
            text = summary.read_text()
            self.assertIn("**refused**", text)
            self.assertIn("blocked must be an issue number", text)


class GitHubClientTests(unittest.TestCase):
    def test_a_non_https_api_url_is_refused_before_any_request(self) -> None:
        from contextlib import redirect_stdout
        from io import StringIO

        out = StringIO()
        with redirect_stdout(out):
            status = writer.main(
                ["--blocked", "10", "--blocking", "20", "--justification", "x" * 20],
                {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": REPO, "GITHUB_ACTOR": "Fooftilly",
                 "GITHUB_API_URL": "http://example.test"},
            )
        self.assertEqual(status, 2)
        self.assertIn("https", json.loads(out.getvalue().strip().splitlines()[-1])["reason"])

    def test_transport_failures_and_bad_bodies_become_api_errors(self) -> None:
        import http.client
        from unittest import mock

        class Body:
            def __init__(self, raw: bytes) -> None:
                self.raw = raw

            def __enter__(self) -> "Body":
                return self

            def __exit__(self, *args: Any) -> None:
                return None

            def read(self) -> bytes:
                return self.raw

        client = writer.GitHubClient("t")
        failures: list[Any] = [
            TimeoutError("timed out"),
            ConnectionResetError("reset"),
            http.client.RemoteDisconnected("closed"),
            http.client.IncompleteRead(b"x"),
        ]
        for failure in failures:
            with mock.patch.object(writer.urllib.request, "urlopen", side_effect=failure):
                with self.assertRaises(writer.ApiError, msg=type(failure).__name__) as caught:
                    client.post("repos/x/y/issues/1/dependencies/blocked_by", {"issue_id": 1})
                self.assertEqual(caught.exception.status, 0)
        with mock.patch.object(writer.urllib.request, "urlopen", return_value=Body(b"<html>")):
            with self.assertRaisesRegex(writer.ApiError, "not valid JSON"):
                client.get("repos/x/y/issues/1")

    def test_an_unreadable_error_body_keeps_the_http_status(self) -> None:
        import http.client
        import io
        from unittest import mock

        class Broken(io.BytesIO):
            def __init__(self, failure: Exception) -> None:
                super().__init__()
                self.failure = failure

            def read(self, *args: Any) -> bytes:
                raise self.failure

        client = writer.GitHubClient("t")
        for failure in (http.client.IncompleteRead(b"x"), TimeoutError("timed out")):
            error = writer.urllib.error.HTTPError("https://api.github.com/x", 502, "Bad Gateway", {}, Broken(failure))  # type: ignore[arg-type]
            with mock.patch.object(writer.urllib.request, "urlopen", side_effect=error):
                with self.assertRaises(writer.ApiError, msg=type(failure).__name__) as caught:
                    client.get("repos/x/y/issues/1")
            self.assertEqual(caught.exception.status, 502)

    def test_a_timed_out_write_is_still_verified(self) -> None:
        # The POST's response is lost after GitHub created the edge: the
        # re-read reports the write instead of crashing.
        gh = FakeGitHub()
        gh.add(10)
        gh.add(20)
        gh.post_error = writer.ApiError(0, "POST", "x", "TimeoutError: timed out")
        status, audit = run(gh, request(apply=True))
        self.assertEqual((status, audit.outcome), (0, "converged"))

    def test_error_message_tolerates_non_json_bodies(self) -> None:
        import io
        import urllib.error

        def http_error(body: bytes) -> urllib.error.HTTPError:
            return urllib.error.HTTPError("https://x", 422, "x", {}, io.BytesIO(body))  # type: ignore[arg-type]

        self.assertEqual(writer._error_message(http_error(b'{"message": "Validation failed"}')), "Validation failed")
        self.assertEqual(writer._error_message(http_error(b"<html>")), "")
        self.assertEqual(writer._error_message(http_error(b"[1]")), "")


class WorkflowShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = _WORKFLOW.read_text(encoding="utf-8")

    def test_only_manual_dispatch_triggers_it(self) -> None:
        on_block = self.text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        triggers = re.findall(r"^  ([a-z_]+):", on_block, re.MULTILINE)
        self.assertEqual(triggers, ["workflow_dispatch"])

    def test_default_permissions_are_empty_and_only_apply_can_write(self) -> None:
        self.assertIn("\npermissions: {}\n", self.text)
        self.assertEqual(len(re.findall(r"^ +issues: write", self.text, re.MULTILINE)), 1)
        apply_job = self.text.split("\n  apply:\n", 1)[1]
        self.assertIn("issues: write", apply_job)
        self.assertIn("inputs.mode == 'apply'", apply_job)
        for forbidden in ("pull-requests: write", "contents: write", "repository-projects", "id-token"):
            self.assertNotIn(forbidden, self.text)

    def test_runs_are_serialized_and_pending_runs_are_queued_not_replaced(self) -> None:
        # The default queue keeps one pending run and a newer dispatch cancels
        # it, which could drop an approved apply request.
        self.assertIn(
            "concurrency:\n  group: issue-dependency-writer\n  cancel-in-progress: false\n  queue: max\n",
            self.text,
        )

    def test_both_jobs_require_the_default_branch(self) -> None:
        plan_job = self.text.split("\n  plan:\n", 1)[1].split("\n  apply:\n", 1)[0]
        apply_job = self.text.split("\n  apply:\n", 1)[1]
        guard = "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"
        self.assertIn(guard, apply_job)
        self.assertIn("needs: plan", apply_job)
        # A job-level condition would skip the plan job, which reports success
        # with no audit record; the plan job refuses in its first step instead.
        self.assertNotIn(guard, plan_job)
        steps = plan_job.split("\n      - name: ")
        self.assertTrue(steps[1].startswith("Require the default branch"))
        self.assertIn('if [ "${RUN_REF}" != "${DEFAULT_REF}" ]', steps[1])
        self.assertIn('"outcome": "refused"', steps[1])
        self.assertNotIn("actions/checkout", steps[1])
        self.assertIn("GITHUB_STEP_SUMMARY", steps[1])
        self.assertIn("exit 1", steps[1])
        self.assertTrue(steps[2].startswith("Check out repository"))

    def test_inputs_reach_the_script_only_through_env(self) -> None:
        for run_block in re.findall(r"run: >-\n((?:          .*\n)+)", self.text):
            self.assertNotIn("${{", run_block)
        self.assertEqual(self.text.count("--apply"), 1)

    def test_checkout_does_not_persist_credentials(self) -> None:
        self.assertEqual(self.text.count("persist-credentials: false"), self.text.count("actions/checkout@"))


if __name__ == "__main__":
    unittest.main()
