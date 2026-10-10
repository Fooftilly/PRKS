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

    def test_runs_are_serialized_and_never_cancelled(self) -> None:
        self.assertIn("group: issue-dependency-writer\n  cancel-in-progress: false", self.text)

    def test_both_jobs_require_the_default_branch(self) -> None:
        guard = "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"
        self.assertEqual(self.text.count(guard), 2)

    def test_inputs_reach_the_script_only_through_env(self) -> None:
        for run_block in re.findall(r"run: >-\n((?:          .*\n)+)", self.text):
            self.assertNotIn("${{", run_block)
        self.assertEqual(self.text.count("--apply"), 1)

    def test_checkout_does_not_persist_credentials(self) -> None:
        self.assertEqual(self.text.count("persist-credentials: false"), self.text.count("actions/checkout@"))


if __name__ == "__main__":
    unittest.main()
