"""Tests for scripts/project_sync.py and .github/workflows/project-sync.yml (#441).

``FakeBoard`` stands in for Project #3 and simulates the native workflows that
race with project-sync: auto-add, "Item added to project" (whose When
selector decides whether it touches PRs) and "Pull request merged / Item
closed -> Done". Native writes are queued and only land when a test flushes
them, so each test can replay a specific ordering. No live API is called.
"""
from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Callable, Optional

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "project_sync.py"
_WORKFLOW = _ROOT / ".github" / "workflows" / "project-sync.yml"
_CONFIG = _ROOT / ".github" / "project-sync.json"

_spec = importlib.util.spec_from_file_location("project_sync", _SCRIPT)
assert _spec and _spec.loader
ps = importlib.util.module_from_spec(_spec)
sys.modules["project_sync"] = ps
_spec.loader.exec_module(ps)

CONFIG = ps.Config.load(_CONFIG)
PROJECT_ID = "PVT_project3"
FIELD_ID = "PVTSSF_status"
OPTIONS = {name: f"opt_{key}" for key, name in CONFIG.statuses.items()}
PR_NUMBER = 42
PR_ID = "PR_node42"
LINKED_ISSUE_ID = "I_node7"


class FakeBoard:
    """Project #3 plus the native workflows, with native writes deferred."""

    def __init__(self, *, draft: bool = True, item_added_when: frozenset[str] = frozenset({"issue"})) -> None:
        self.pr = {"id": PR_ID, "state": "OPEN", "draft": draft}
        self.items: dict[str, dict] = {}
        self.item_added_when = item_added_when
        self.pending_native: list[tuple[str, str]] = []
        self.mutations: list[tuple[str, tuple]] = []
        self.before_set: Optional[Callable[[], None]] = None
        self.after_set: Optional[Callable[[], None]] = None
        self.before_add: Optional[Callable[[], None]] = None
        self.options = dict(OPTIONS)
        self._next = 0
        # A linked issue is on the board too; project-sync must never touch it.
        self._create(LINKED_ISSUE_ID, "issue")

    # native behavior ---------------------------------------------------
    def _create(self, content_id: str, kind: str) -> dict:
        self._next += 1
        item = {"id": f"PVTI_{self._next}", "status": None, "archived": False, "kind": kind}
        self.items[content_id] = item
        if kind in self.item_added_when:
            self.pending_native.append((content_id, CONFIG.statuses["inbox"]))
        return item

    def native_auto_add(self) -> None:
        if PR_ID not in self.items:
            self._create(PR_ID, "pr")

    def merge(self, *, merged: bool = True) -> None:
        self.pr["state"] = "MERGED" if merged else "CLOSED"
        if PR_ID in self.items:
            self.pending_native.append((PR_ID, CONFIG.statuses["done"]))

    def flush_native(self) -> None:
        while self.pending_native:
            content_id, status = self.pending_native.pop(0)
            self.items[content_id]["status"] = status

    def person_sets(self, status: str) -> None:
        self.items[PR_ID]["status"] = status

    @property
    def pr_status(self) -> Optional[str]:
        item = self.items.get(PR_ID)
        return item["status"] if item else None

    # ProjectApi ----------------------------------------------------------
    def load_project(self, config):
        return ps.Project(id=PROJECT_ID, field_id=FIELD_ID, options=self.options)

    def load_pr(self, config, number, project_id):
        assert number == PR_NUMBER and project_id == PROJECT_ID
        raw = self.items.get(PR_ID)
        item = ps.Item(id=raw["id"], status=raw["status"], archived=raw["archived"]) if raw else None
        return ps.PullRequest(id=PR_ID, state=self.pr["state"], is_draft=self.pr["draft"], item=item)

    def add_item(self, project_id, content_id):
        self.mutations.append(("add", (project_id, content_id)))
        if self.before_add:
            hook, self.before_add = self.before_add, None
            hook()
        if content_id not in self.items:  # addProjectV2ItemById returns the existing item
            self._create(content_id, "pr")

    def set_status(self, project_id, item_id, field_id, option_id):
        self.mutations.append(("set", (project_id, item_id, field_id, option_id)))
        if self.before_set:
            hook, self.before_set = self.before_set, None
            hook()
        name = {v: k for k, v in self.options.items()}[option_id]
        for item in self.items.values():
            if item["id"] == item_id:
                item["status"] = name
        if self.after_set:
            hook, self.after_set = self.after_set, None
            hook()


def event(action: str, number: int = PR_NUMBER, repository: str = "Fooftilly/PRKS"):
    return ps.Event(action=action, number=number, repository=repository)


def sync(board: FakeBoard, action: str, apply: bool = True):
    return ps.run(event(action), CONFIG, board, apply)


class LifecycleTests(unittest.TestCase):
    def test_dry_run_reports_without_writing(self):
        board = FakeBoard()
        audit = sync(board, "opened", apply=False)
        self.assertEqual(audit.outcome, "dry-run")
        self.assertEqual(audit.status_after, "In Progress")
        self.assertIn("would add", audit.reason)
        self.assertEqual(board.mutations, [])
        self.assertIsNone(board.pr_status)

    def test_draft_opened_moves_to_in_progress(self):
        board = FakeBoard(draft=True)
        board.native_auto_add()
        audit = sync(board, "opened")
        self.assertEqual((audit.outcome, board.pr_status), ("updated", "In Progress"))

    def test_ready_pr_opened_moves_to_review(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        self.assertEqual(sync(board, "opened").outcome, "updated")
        self.assertEqual(board.pr_status, "Review")

    def test_ready_for_review_moves_to_review(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("In Progress")
        self.assertEqual(sync(board, "ready_for_review").outcome, "updated")
        self.assertEqual(board.pr_status, "Review")

    def test_converted_to_draft_moves_back_to_in_progress(self):
        for start in ("Review", "Changes requested"):
            board = FakeBoard(draft=True)
            board.native_auto_add()
            board.person_sets(start)
            self.assertEqual(sync(board, "converted_to_draft").outcome, "updated", start)
            self.assertEqual(board.pr_status, "In Progress")

    def test_re_review_requested_moves_changes_requested_to_review(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("Changes requested")
        self.assertEqual(sync(board, "review_requested").outcome, "updated")
        self.assertEqual(board.pr_status, "Review")

    def test_review_request_outside_changes_requested_changes_nothing(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("In Progress")
        audit = sync(board, "review_requested")
        self.assertEqual(audit.outcome, "skipped")
        self.assertEqual(board.pr_status, "In Progress")

    def test_blocked_and_done_are_never_left(self):
        for action, draft in (("opened", True), ("ready_for_review", False), ("converted_to_draft", True), ("review_requested", False)):
            for status in ("Blocked", "Done"):
                board = FakeBoard(draft=draft)
                board.native_auto_add()
                board.person_sets(status)
                audit = sync(board, action)
                self.assertEqual(audit.outcome, "skipped", (action, status))
                self.assertEqual(board.pr_status, status)
                self.assertFalse([m for m in board.mutations if m[0] == "set"])

    def test_unknown_status_option_is_left_alone(self):
        board = FakeBoard()
        board.options["Someday"] = "opt_someday"
        board.native_auto_add()
        board.person_sets("Someday")
        self.assertEqual(sync(board, "opened").outcome, "skipped")
        self.assertEqual(board.pr_status, "Someday")

    def test_stale_event_is_skipped(self):
        board = FakeBoard(draft=True)  # ready_for_review arrived, but the PR is a draft again
        board.native_auto_add()
        audit = sync(board, "ready_for_review")
        self.assertEqual(audit.outcome, "skipped")
        self.assertIn("stale event", audit.reason)
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("Review")
        self.assertEqual(sync(board, "converted_to_draft").outcome, "skipped")

    def test_repeated_events_converge_without_writing_again(self):
        board = FakeBoard()
        sync(board, "opened")
        writes = len(board.mutations)
        again = sync(board, "opened")
        self.assertEqual(again.outcome, "unchanged")
        self.assertEqual(len(board.mutations), writes)
        self.assertEqual(board.pr_status, "In Progress")

    def test_merged_or_closed_pr_is_skipped(self):
        for merged in (True, False):
            board = FakeBoard()
            board.native_auto_add()
            board.merge(merged=merged)
            board.flush_native()
            audit = sync(board, "opened")
            self.assertEqual(audit.outcome, "skipped")
            self.assertEqual(board.mutations, [])

    def test_archived_item_is_skipped(self):
        board = FakeBoard()
        board.native_auto_add()
        board.items[PR_ID]["archived"] = True
        self.assertEqual(sync(board, "opened").outcome, "skipped")
        self.assertEqual(board.mutations, [])


class RaceTests(unittest.TestCase):
    """Orderings from docs/agent-workflows/project-3.md section 10.1."""

    def test_project_sync_adds_missing_item_and_delayed_native_run_cannot_reset_it(self):
        board = FakeBoard(item_added_when=frozenset({"issue"}))
        audit = sync(board, "opened")
        self.assertTrue(audit.item_added)
        board.native_auto_add()  # arrives late: the item already exists
        board.flush_native()  # delayed "Item added" run
        self.assertEqual(board.pr_status, "In Progress")
        self.assertEqual(sum(1 for item in board.items.values() if item["kind"] == "pr"), 1)

    def test_native_add_first_with_delayed_item_added_run(self):
        board = FakeBoard(item_added_when=frozenset({"issue"}))
        board.native_auto_add()
        sync(board, "opened")
        board.flush_native()
        self.assertEqual(board.pr_status, "In Progress")

    def test_including_prs_in_item_added_would_reset_a_classified_pr(self):
        # The hazard Option A removes: with "When" covering PRs, a delayed
        # native run overwrites the Status project-sync already set.
        board = FakeBoard(item_added_when=frozenset({"issue", "pr"}))
        sync(board, "opened")
        board.flush_native()
        self.assertEqual(board.pr_status, "Inbox")

    def test_native_auto_add_between_read_and_add_converges_to_one_item(self):
        board = FakeBoard()
        board.before_add = board.native_auto_add
        audit = sync(board, "opened")
        self.assertEqual(audit.outcome, "updated")
        self.assertEqual(sum(1 for item in board.items.values() if item["kind"] == "pr"), 1)
        self.assertEqual(board.pr_status, "In Progress")

    def test_concurrent_runs_converge_to_one_item_and_one_status(self):
        board = FakeBoard()
        second: list = []
        board.before_add = lambda: second.append(sync(board, "opened"))
        first = sync(board, "opened")
        self.assertEqual(second[0].outcome, "updated")
        self.assertIn(first.outcome, {"updated", "unchanged"})
        self.assertEqual(sum(1 for item in board.items.values() if item["kind"] == "pr"), 1)
        self.assertEqual(board.pr_status, "In Progress")

    def test_merge_landing_before_the_write_is_repaired_to_done(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("In Progress")

        def merge_now():
            board.merge()
            board.flush_native()  # native "merged -> Done" lands first

        board.before_set = merge_now
        audit = sync(board, "ready_for_review")
        self.assertEqual(audit.outcome, "corrected")
        self.assertEqual(board.pr_status, "Done")

    def test_merge_landing_after_the_write_ends_in_done(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("In Progress")
        board.after_set = board.merge  # native Done still pending
        audit = sync(board, "ready_for_review")
        self.assertEqual(audit.outcome, "corrected")
        board.flush_native()
        self.assertEqual(board.pr_status, "Done")

    def test_close_during_the_write_ends_in_done(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.after_set = lambda: board.merge(merged=False)
        self.assertEqual(sync(board, "opened").outcome, "corrected")
        self.assertEqual(board.pr_status, "Done")

    def test_a_person_writing_after_the_run_wins(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.after_set = lambda: board.person_sets("Blocked")
        audit = sync(board, "opened")
        self.assertEqual(audit.outcome, "superseded")
        self.assertEqual(board.pr_status, "Blocked")

    def test_only_the_pr_is_ever_added_or_written(self):
        board = FakeBoard()
        sync(board, "opened")
        board.person_sets("Review")
        sync(board, "converted_to_draft")
        issue_item = board.items[LINKED_ISSUE_ID]["id"]
        for kind, args in board.mutations:
            self.assertNotIn(LINKED_ISSUE_ID, args)
            self.assertNotIn(issue_item, args)
        board.flush_native()
        self.assertEqual(board.items[LINKED_ISSUE_ID]["status"], "Inbox")  # only the native issue rule set it


class GuardTests(unittest.TestCase):
    def test_other_repositories_are_refused(self):
        with self.assertRaises(ps.Refused):
            ps.run(event("opened", repository="someone/fork"), CONFIG, FakeBoard(), True)

    def test_unhandled_actions_are_refused(self):
        for action in ("closed", "synchronize", "labeled", "edited"):
            with self.assertRaises(ps.Refused):
                ps.run(event(action), CONFIG, FakeBoard(), True)

    def test_missing_status_option_fails_clearly(self):
        board = FakeBoard()
        del board.options["Changes requested"]
        with self.assertRaisesRegex(ps.ConfigError, "Changes requested"):
            sync(board, "opened")

    def test_config_file_names_every_status(self):
        self.assertEqual(set(CONFIG.statuses), set(ps.STATUS_KEYS))
        self.assertEqual((CONFIG.owner, CONFIG.project_number, CONFIG.repository), ("Fooftilly", 3, "Fooftilly/PRKS"))

    def test_config_missing_a_status_is_rejected(self):
        raw = json.loads(_CONFIG.read_text())
        del raw["statuses"]["blocked"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project-sync.json"
            path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ps.ConfigError, "blocked"):
                ps.Config.load(path)


class MainTests(unittest.TestCase):
    def _main(self, env, factory=None):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.md"
            full = {"GITHUB_STEP_SUMMARY": str(summary), **env}
            with redirect_stdout(out):
                code = ps.main([], full, factory, _CONFIG)
            return code, json.loads(out.getvalue()), summary.read_text()

    def _env(self, **extra):
        return {"EVENT_ACTION": "opened", "PR_NUMBER": str(PR_NUMBER), "EVENT_REPOSITORY": "Fooftilly/PRKS", **extra}

    def test_without_a_token_nothing_is_called_and_the_run_succeeds(self):
        def factory(token):
            raise AssertionError("must not build a client")

        code, record, summary = self._main(self._env(PROJECT_SYNC_MODE="apply"), factory)
        self.assertEqual((code, record["outcome"]), (0, "not-configured"))
        self.assertIn("not-configured", summary)

    def test_dry_run_unless_mode_is_exactly_apply(self):
        for mode in ("", "dry-run", "Apply", "apply "):
            board = FakeBoard()
            code, record, _ = self._main(self._env(PROJECT_SYNC_TOKEN="t", PROJECT_SYNC_MODE=mode), lambda _, b=board: b)
            self.assertEqual((code, record["mode"], record["outcome"]), (0, "dry-run", "dry-run"), mode)
            self.assertEqual(board.mutations, [])

    def test_apply_mode_writes_and_records_an_audit(self):
        board = FakeBoard()
        code, record, summary = self._main(self._env(PROJECT_SYNC_TOKEN="t", PROJECT_SYNC_MODE="apply"), lambda _: board)
        self.assertEqual(code, 0)
        self.assertEqual(record["outcome"], "updated")
        self.assertEqual(record["status_after"], "In Progress")
        self.assertTrue(record["item_added"])
        self.assertIn("PR #42", summary)

    def test_token_never_appears_in_output(self):
        board = FakeBoard()
        _, record, summary = self._main(self._env(PROJECT_SYNC_TOKEN="ghp_secretvalue"), lambda _: board)
        self.assertNotIn("ghp_secretvalue", json.dumps(record) + summary)

    def test_bad_inputs_are_refused(self):
        for env in (self._env(PR_NUMBER="42; rm -rf"), self._env(EVENT_REPOSITORY="evil/repo"), self._env(EVENT_ACTION="closed")):
            code, record, _ = self._main({**env, "PROJECT_SYNC_TOKEN": "t"}, lambda _: FakeBoard())
            self.assertEqual((code, record["outcome"]), (1, "refused"))

    def test_api_failure_exits_nonzero(self):
        class Broken(FakeBoard):
            def load_project(self, config):
                raise ps.ApiError("GraphQL HTTP 502")

        code, record, _ = self._main(self._env(PROJECT_SYNC_TOKEN="t"), lambda _: Broken())
        self.assertEqual((code, record["outcome"]), (2, "failed"))


class GraphQLApiTests(unittest.TestCase):
    def _pr_payload(self, nodes):
        return {"repository": {"pullRequest": {"id": PR_ID, "state": "OPEN", "isDraft": True, "projectItems": {"nodes": nodes}}}}

    def test_items_of_other_projects_are_ignored(self):
        nodes = [
            {"id": "other", "isArchived": False, "project": {"id": "PVT_other"}, "fieldValueByName": None},
            {"id": "mine", "isArchived": False, "project": {"id": PROJECT_ID},
             "fieldValueByName": {"__typename": "ProjectV2ItemFieldSingleSelectValue", "name": "Review"}},
        ]
        api = ps.GraphQLApi(lambda q, v: self._pr_payload(nodes))
        pr = api.load_pr(CONFIG, PR_NUMBER, PROJECT_ID)
        self.assertEqual((pr.item.id, pr.item.status), ("mine", "Review"))

    def test_duplicate_items_fail_closed(self):
        node = {"id": "a", "isArchived": False, "project": {"id": PROJECT_ID}, "fieldValueByName": None}
        api = ps.GraphQLApi(lambda q, v: self._pr_payload([node, dict(node, id="b")]))
        with self.assertRaises(ps.ApiError):
            api.load_pr(CONFIG, PR_NUMBER, PROJECT_ID)

    def test_missing_status_field_fails_clearly(self):
        api = ps.GraphQLApi(lambda q, v: {"user": {"projectV2": {"id": PROJECT_ID, "field": None}}})
        with self.assertRaisesRegex(ps.ConfigError, "Status"):
            api.load_project(CONFIG)

    def test_mutations_carry_only_the_pr_content_id(self):
        calls = []
        api = ps.GraphQLApi(lambda q, v: calls.append((q, v)) or {})
        api.add_item(PROJECT_ID, PR_ID)
        api.set_status(PROJECT_ID, "PVTI_1", FIELD_ID, "opt_review")
        self.assertIn("addProjectV2ItemById", calls[0][0])
        self.assertEqual(calls[0][1], {"project": PROJECT_ID, "content": PR_ID})
        self.assertIn("updateProjectV2ItemFieldValue", calls[1][0])

    def test_non_https_url_is_refused(self):
        with self.assertRaises(ps.ApiError):
            ps.http_transport("t", "http://api.github.com/graphql")

    def test_script_has_no_merge_or_approval_capability(self):
        text = _SCRIPT.read_text().lower()
        for word in ("mergepullrequest", "enablepullrequestautomerge", "addpullrequestreview", "/merge", "check_run", "checksuite"):
            self.assertNotIn(word, text)


class WorkflowShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _WORKFLOW.read_text()

    def test_triggers_only_on_the_four_pr_actions(self):
        self.assertRegex(self.text, r"(?m)^  pull_request_target:\n    types: \[opened, ready_for_review, converted_to_draft, review_requested\]$")
        for trigger in ("pull_request:", "workflow_run:", "push:", "pull_request_review:", "schedule:", "workflow_dispatch:"):
            self.assertNotIn(f"\n  {trigger}", self.text)

    def test_least_privilege(self):
        self.assertIn("\npermissions: {}\n", self.text)
        grants = re.findall(r"(?m)^ +([a-z-]+): (read|write)$", self.text)
        self.assertEqual(grants, [("contents", "read")])

    def test_never_checks_out_pull_request_code(self):
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertNotIn("github.event.pull_request.head", self.text)

    def test_runs_are_serialized_per_pr_and_queued(self):
        self.assertIn(
            "concurrency:\n  group: project-sync-pr-${{ github.event.pull_request.number }}\n"
            "  cancel-in-progress: false\n  queue: max\n",
            self.text,
        )

    def test_only_writes_when_the_mode_variable_says_apply(self):
        self.assertIn("PROJECT_SYNC_MODE: ${{ vars.PROJECT_SYNC_MODE }}", self.text)
        self.assertEqual(re.findall(r"secrets\.([A-Z_]+)", self.text), ["PROJECT_SYNC_TOKEN"])

    def test_no_expressions_in_run_blocks(self):
        for block in re.findall(r"(?ms)^ +run: (.*?)(?=^ +- |\Z)", self.text):
            self.assertNotIn("${{", block)

    def test_repository_guard(self):
        self.assertIn("if: github.repository == 'Fooftilly/PRKS'", self.text)


if __name__ == "__main__":
    unittest.main()
