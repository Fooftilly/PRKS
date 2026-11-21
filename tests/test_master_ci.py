"""Master CI after merges: start each full workflow only when master's head needs it."""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

from scripts.ci import master_ci

ROOT = Path(__file__).resolve().parents[1]
HEAD = "a" * 40
OLD = "b" * 40
BOT = {"login": "github-actions[bot]"}
PERSON = {"login": "someone"}


def _run(run_id, sha, status="completed", conclusion="success", actor=BOT):
    return {"id": run_id, "head_sha": sha, "status": status, "conclusion": conclusion,
            "triggering_actor": actor, "html_url": f"https://example.test/runs/{run_id}"}


class FakeActions:
    def __init__(self, runs: list[dict[str, Any]], new_conclusion="success"):
        self.runs = runs
        self.new_conclusion = new_conclusion
        self.dispatched: list[tuple[str, dict]] = []
        self.polls = 0

    def request(self, method, path, body=None):
        if method == "POST":
            self.dispatched.append((path, body))
            self.runs.insert(0, _run(900, HEAD, status="queued", conclusion=None))
            return None
        if path.startswith("actions/runs/"):
            self.polls += 1
            run = next(r for r in self.runs if r["id"] == int(path.rsplit("/", 1)[1]))
            if self.polls >= 2:
                run.update(status="completed", conclusion=run["conclusion"] or self.new_conclusion)
            return dict(run)
        assert "event=workflow_dispatch" in path and "branch=master" in path, path
        return {"workflow_runs": [dict(r) for r in self.runs]}


def _no_sleep(_seconds):
    pass


class DecideTests(unittest.TestCase):
    def test_decisions(self):
        self.assertEqual(master_ci.decide(None, HEAD)[0], "start")
        self.assertEqual(master_ci.decide(_run(1, OLD), HEAD)[0], "start")
        self.assertEqual(master_ci.decide(_run(1, HEAD), HEAD)[0], "done")
        self.assertEqual(master_ci.decide(_run(1, HEAD, status="in_progress", conclusion=None), HEAD)[0], "wait")
        for conclusion in ("failure", "timed_out", "cancelled"):
            with self.subTest(conclusion=conclusion):
                action, reason = master_ci.decide(_run(1, HEAD, conclusion=conclusion), HEAD)
                self.assertEqual(action, "start")
                self.assertIn(conclusion, reason)


class RunWorkflowTests(unittest.TestCase):
    def test_nothing_new_since_a_passing_run_starts_nothing(self):
        gh = FakeActions([_run(1, HEAD)])
        passed, message = master_ci.run_workflow(gh, "e2e-gate.yml", HEAD, sleep=_no_sleep)
        self.assertTrue(passed)
        self.assertIn("skipped", message)
        self.assertEqual(gh.dispatched, [])

    def test_new_merges_start_a_run_and_its_failure_fails_the_job(self):
        gh = FakeActions([_run(1, OLD)], new_conclusion="failure")
        passed, message = master_ci.run_workflow(gh, "e2e-gate.yml", HEAD, sleep=_no_sleep)
        self.assertEqual(gh.dispatched, [("actions/workflows/e2e-gate.yml/dispatches", {"ref": "master"})])
        self.assertFalse(passed)
        self.assertIn("failure", message)
        self.assertIn("runs/900", message)

    def test_a_persons_manual_run_is_not_ours(self):
        gh = FakeActions([_run(2, HEAD, actor=PERSON), _run(1, OLD)])
        passed, _ = master_ci.run_workflow(gh, "test-gate.yml", HEAD, sleep=_no_sleep)
        self.assertEqual(len(gh.dispatched), 1)
        self.assertTrue(passed)

    def test_a_run_of_this_commit_in_progress_is_awaited_not_restarted(self):
        gh = FakeActions([_run(5, HEAD, status="in_progress", conclusion=None)])
        passed, _ = master_ci.run_workflow(gh, "zizmor.yml", HEAD, sleep=_no_sleep)
        self.assertEqual(gh.dispatched, [])
        self.assertTrue(passed)

    def test_dry_run_starts_nothing(self):
        gh = FakeActions([_run(1, OLD)])
        passed, message = master_ci.run_workflow(gh, "codeql.yml", HEAD, dry_run=True, sleep=_no_sleep)
        self.assertTrue(passed)
        self.assertIn("would start", message)
        self.assertEqual(gh.dispatched, [])

    def test_a_run_that_never_appears_fails(self):
        class Silent(FakeActions):
            def request(self, method, path, body=None):
                if method == "POST":
                    return None
                return super().request(method, path, body)

        with self.assertRaisesRegex(RuntimeError, "did not appear"):
            master_ci.run_workflow(Silent([_run(1, OLD)]), "e2e-gate.yml", HEAD, sleep=_no_sleep)

    def test_the_head_must_be_a_full_sha(self):
        with self.assertRaises(SystemExit):
            master_ci.main(["--workflow", "e2e-gate.yml", "--head", "deadbeef"])


class WiringTests(unittest.TestCase):
    def _text(self, name):
        return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")

    def test_the_matrix_lists_every_workflow_and_each_is_dispatchable_and_not_run_on_push(self):
        text = self._text("master-ci.yml")
        matrix = re.search(r"        workflow:\n((?:          - .+\n)+)", text)
        assert matrix is not None
        self.assertEqual([line.strip()[2:] for line in matrix.group(1).splitlines()], list(master_ci.WORKFLOWS))
        self.assertIn("actions: write", text)
        for name in master_ci.WORKFLOWS:
            with self.subTest(workflow=name):
                triggers = re.search(r"\non:\n(.*?)\n(?=[a-z])", self._text(name), re.DOTALL).group(1)  # type: ignore[union-attr]
                self.assertIn("workflow_dispatch:", triggers)
                self.assertNotIn("push:", triggers)

    def test_a_failed_night_is_reported(self):
        self.assertIn("      - Master CI after merges\n", self._text("scheduled-report.yml"))


if __name__ == "__main__":
    unittest.main()
