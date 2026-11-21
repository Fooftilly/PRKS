"""Master CI after merges: start each full workflow only when master's head needs it."""

from __future__ import annotations

import re
import unittest
from unittest import mock
from urllib.parse import unquote
from pathlib import Path
from typing import Any

from scripts.ci import master_ci

ROOT = Path(__file__).resolve().parents[1]
HEAD = "a" * 40
OLD = "b" * 40
BOT = {"login": "github-actions[bot]"}
PERSON = {"login": "someone"}


def _run(run_id: int, sha: str, status: str = "completed", conclusion: str | None = "success",
         actor: dict[str, str] = BOT) -> dict[str, Any]:
    return {"id": run_id, "head_sha": sha, "status": status, "conclusion": conclusion,
            "actor": actor, "triggering_actor": actor, "html_url": f"https://example.test/runs/{run_id}"}


class FakeActions:
    def __init__(self, runs: list[dict[str, Any]], new_conclusion="success", head=HEAD, ran_on=HEAD):
        self.runs = runs
        self.new_conclusion = new_conclusion
        self.head = head
        self.ran_on = ran_on
        self.dispatched: list[tuple[str, Any]] = []
        self.polls = 0
        self.pages_read = 0
        self.actor_filtered = False

    def request(self, method, path, body=None):
        if path == "branches/master":
            return {"commit": {"sha": self.head}}
        if method == "POST":
            self.dispatched.append((path, body))
            self.runs.insert(0, _run(900, self.ran_on, status="queued", conclusion=None))
            return None
        if path.startswith("actions/runs/"):
            self.polls += 1
            run = next(r for r in self.runs if r["id"] == int(path.rsplit("/", 1)[1]))
            if self.polls >= 2:
                run.update(status="completed", conclusion=run["conclusion"] or self.new_conclusion)
            return dict(run)
        assert "event=workflow_dispatch" in path and "branch=master" in path, path
        query = dict(part.split("=", 1) for part in path.split("?", 1)[1].split("&"))
        per_page, page = int(query["per_page"]), int(query.get("page", "1"))
        runs = self.runs
        if "actor" in query:
            self.actor_filtered = True
            runs = [r for r in runs if r["actor"]["login"] == unquote(query["actor"])]
        if query.get("status") == "success":
            runs = [r for r in runs if r["conclusion"] == "success"]
        else:
            self.pages_read = max(self.pages_read, page)
        return {"workflow_runs": [dict(r) for r in runs[(page - 1) * per_page:page * per_page]]}


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
        passed, message = master_ci.run_workflow(gh, "e2e-gate.yml", sleep=_no_sleep)
        self.assertTrue(passed)
        self.assertIn("skipped", message)
        self.assertEqual(gh.dispatched, [])

    def test_new_merges_start_a_run_and_its_failure_fails_the_job(self):
        gh = FakeActions([_run(1, OLD)], new_conclusion="failure")
        passed, message = master_ci.run_workflow(gh, "e2e-gate.yml", sleep=_no_sleep)
        self.assertEqual(gh.dispatched, [("actions/workflows/e2e-gate.yml/dispatches", {"ref": "master"})])
        self.assertFalse(passed)
        self.assertIn("failure", message)
        self.assertIn("runs/900", message)

    def test_a_persons_manual_run_is_not_ours(self):
        gh = FakeActions([_run(2, HEAD, actor=PERSON), _run(1, OLD)])
        passed, _ = master_ci.run_workflow(gh, "test-gate.yml", sleep=_no_sleep)
        self.assertEqual(len(gh.dispatched), 1)
        self.assertTrue(passed)

    def test_a_run_of_this_commit_in_progress_is_awaited_not_restarted(self):
        gh = FakeActions([_run(5, HEAD, status="in_progress", conclusion=None)])
        passed, _ = master_ci.run_workflow(gh, "zizmor.yml", sleep=_no_sleep)
        self.assertEqual(gh.dispatched, [])
        self.assertTrue(passed)

    def test_dry_run_starts_nothing(self):
        gh = FakeActions([_run(1, OLD)])
        passed, message = master_ci.run_workflow(gh, "codeql.yml", dry_run=True, sleep=_no_sleep)
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
            master_ci.run_workflow(Silent([_run(1, OLD)]), "e2e-gate.yml", sleep=_no_sleep)

    def test_a_person_rerunning_our_run_keeps_it_ours(self):
        rerun = _run(1, HEAD)
        rerun["triggering_actor"] = PERSON
        gh = FakeActions([rerun])
        passed, message = master_ci.run_workflow(gh, "e2e-gate.yml", sleep=_no_sleep)
        self.assertEqual(gh.dispatched, [])
        self.assertIn("skipped", message)

    def test_static_analysis_diffs_against_the_last_checked_commit(self):
        gh = FakeActions([_run(1, OLD)])
        master_ci.run_workflow(gh, "static-analysis.yml", sleep=_no_sleep)
        self.assertEqual(gh.dispatched[0][1], {"ref": "master", "inputs": {"diff_base": OLD}})
        first = FakeActions([])
        master_ci.run_workflow(first, "static-analysis.yml", sleep=_no_sleep)
        self.assertEqual(first.dispatched[0][1], {"ref": "master"})

    def test_retrying_a_failed_static_check_keeps_the_last_passing_base(self):
        # A passed, B failed on a new violation; B unchanged: the retry still diffs against A.
        gh = FakeActions([_run(2, HEAD, conclusion="failure"), _run(1, OLD)])
        master_ci.run_workflow(gh, "static-analysis.yml", sleep=_no_sleep)
        self.assertEqual(gh.dispatched[0][1], {"ref": "master", "inputs": {"diff_base": OLD}})

    def test_without_a_passing_static_check_the_first_checked_commit_stays_the_base(self):
        first = "d" * 40
        gh = FakeActions([_run(3, HEAD, conclusion="failure"), _run(2, OLD, conclusion="failure"),
                          _run(1, first, conclusion="timed_out")])
        master_ci.run_workflow(gh, "static-analysis.yml", sleep=_no_sleep)
        self.assertEqual(gh.dispatched[0][1]["inputs"], {"diff_base": first})

    def test_a_last_pass_behind_many_failures_is_still_the_base(self):
        # 1500 failed nights at HEAD, past GitHub's 1000-result cap, bury the pass at OLD.
        failures = [_run(5000 - i, HEAD, conclusion="failure") for i in range(1500)]
        gh = FakeActions(failures + [_run(1, OLD)])
        master_ci.run_workflow(gh, "static-analysis.yml", sleep=_no_sleep)
        self.assertEqual(gh.dispatched[0][1]["inputs"], {"diff_base": OLD})
        self.assertEqual(gh.pages_read, 1)  # the decision's page; the last pass was asked for directly

    def test_runs_are_filtered_by_actor_on_githubs_side(self):
        # A person's runs must not use up the 1000-result cap.
        people = [_run(900 - i, OLD, actor=PERSON) for i in range(20)]
        first = "d" * 40
        gh = FakeActions(people + [_run(2, HEAD, conclusion="failure"), _run(1, first, conclusion="failure")])
        self.assertEqual(master_ci.diff_base(gh, "static-analysis.yml"), first)
        self.assertTrue(gh.actor_filtered)

    def test_a_history_without_end_is_refused(self):
        gh = FakeActions([_run(1, HEAD, conclusion="failure")])
        with mock.patch.object(master_ci, "HISTORY_PER_PAGE", 1), mock.patch.object(master_ci, "HISTORY_PAGES", 1):
            with self.assertRaisesRegex(RuntimeError, "none passed"):
                master_ci.diff_base(gh, "static-analysis.yml")

    def test_only_static_analysis_gets_a_diff_base(self):
        gh = FakeActions([_run(1, OLD)])
        master_ci.run_workflow(gh, "e2e-gate.yml", sleep=_no_sleep)
        self.assertEqual(gh.dispatched[0][1], {"ref": "master"})

    def test_master_moving_during_the_dispatch_is_named(self):
        newer = "c" * 40
        gh = FakeActions([_run(1, OLD)], ran_on=newer)
        passed, message = master_ci.run_workflow(gh, "e2e-gate.yml", sleep=_no_sleep)
        self.assertTrue(passed)
        self.assertIn("master moved from aaaaaaaa", message)

    def test_a_malformed_head_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "unexpected master head"):
            master_ci.run_workflow(FakeActions([], head="--x"), "e2e-gate.yml", sleep=_no_sleep)


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
