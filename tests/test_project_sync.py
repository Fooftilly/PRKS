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
_SIGNAL = _ROOT / ".github" / "workflows" / "project-sync-review.yml"
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
        self.review_requests: list[tuple[str, str]] = []
        self.converted_to_draft_at: Optional[str] = None
        self.changes_by: dict[str, str] = {}  # reviewer -> latest changes-requested review
        self.withdrawals: list[tuple[str, str]] = []  # dismissed or approved change requests
        self.status_at: Optional[str] = None  # when the PR's Status was last set, if known
        self.after_add: Optional[Callable[[], None]] = None
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

    def request_review(self, at: str, reviewer: str = "alice") -> None:
        self.review_requests.append((reviewer, at))

    def convert_to_draft(self, at: str) -> None:
        self.pr["draft"] = True
        self.converted_to_draft_at = at

    def request_changes(self, at: str, reviewer: str = "alice") -> None:
        """A changes-requested review whose Changes requested write is queued.

        The queued write stands for any other writer of that Status (a
        person, or the review's own project-sync run), so tests can land it
        mid-run.
        """
        self.changes_by[reviewer] = at
        if PR_ID in self.items:
            self.pending_native.append((PR_ID, CONFIG.statuses["changes_requested"]))

    def submit_changes_review(self, at: str, reviewer: str = "alice") -> None:
        """A changes-requested review with no native rule: only the time is recorded."""
        self.changes_by[reviewer] = at

    def dismiss(self, reviewer: str = "alice", at: str = "2026-10-10T20:00:00Z") -> None:
        """A maintainer dismissed the reviewer's change request."""
        if self.changes_by.pop(reviewer, None) is not None:
            self.withdrawals.append((reviewer, at))

    def approve(self, reviewer: str = "alice", at: str = "2026-10-10T20:00:00Z") -> None:
        """The reviewer approves; an earlier change request of theirs is withdrawn."""
        if self.changes_by.pop(reviewer, None) is not None:
            self.withdrawals.append((reviewer, at))

    def flush_native(self) -> None:
        while self.pending_native:
            content_id, status = self.pending_native.pop(0)
            self.items[content_id]["status"] = status

    def person_sets(self, status: str, at: Optional[str] = None) -> None:
        self.items[PR_ID]["status"] = status
        self.status_at = at

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
        item = (
            ps.Item(id=raw["id"], status=raw["status"], archived=raw["archived"], status_updated_at=self.status_at)
            if raw
            else None
        )
        return ps.PullRequest(
            id=PR_ID,
            state=self.pr["state"],
            is_draft=self.pr["draft"],
            item=item,
            last_converted_to_draft_at=self.converted_to_draft_at,
            review_requests=tuple(self.review_requests),
            changes_requested=tuple(self.changes_by.items()),
            withdrawals=tuple(self.withdrawals),
        )

    def add_item(self, project_id, content_id):
        self.mutations.append(("add", (project_id, content_id)))
        if self.before_add:
            hook, self.before_add = self.before_add, None
            hook()
        if content_id not in self.items:  # addProjectV2ItemById returns the existing item
            self._create(content_id, "pr")
        if self.after_add:
            hook, self.after_add = self.after_add, None
            hook()

    def set_status(self, project_id, item_id, field_id, option_id):
        self.mutations.append(("set", (project_id, item_id, field_id, option_id)))
        if self.before_set:
            hook, self.before_set = self.before_set, None
            hook()
        name = {v: k for k, v in self.options.items()}[option_id]
        if item_id == self.items.get(PR_ID, {}).get("id"):
            self.status_at = None  # the fake has no clock for its own writes
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
            board = FakeBoard(draft=False)
            board.native_auto_add()
            if start == "Changes requested":
                board.request_changes("2026-10-10T10:00:00Z")
                board.flush_native()
            else:
                board.person_sets(start)
            board.convert_to_draft("2026-10-10T11:00:00Z")
            self.assertEqual(sync(board, "converted_to_draft").outcome, "updated", start)
            self.assertEqual(board.pr_status, "In Progress")

    def test_re_review_requested_moves_changes_requested_to_review(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.request_changes("2026-10-10T10:00:00Z")
        board.flush_native()
        board.request_review("2026-10-10T11:00:00Z")
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
        for action, draft in (
            ("opened", True),
            ("ready_for_review", False),
            ("converted_to_draft", True),
            ("review_requested", False),
            ("review_changed", False),
        ):
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


class ChangesRequestedTests(unittest.TestCase):
    """The native "Code changes requested" rule is off; project-sync owns it."""

    def test_moves_review_in_progress_or_empty_to_changes_requested(self):
        for start, draft in ((None, False), ("In Progress", True), ("Review", False)):
            board = FakeBoard(draft=draft)
            board.native_auto_add()
            if start:
                board.person_sets(start)
            board.submit_changes_review("2026-10-10T12:00:00Z")
            audit = sync(board, "review_changed")
            self.assertEqual(audit.outcome, "updated", start)
            self.assertEqual(board.pr_status, "Changes requested")

    def test_never_moves_blocked_done_inbox_or_ready(self):
        for start in ("Blocked", "Done", "Inbox", "Ready"):
            board = FakeBoard(draft=False)
            board.native_auto_add()
            board.person_sets(start)
            board.submit_changes_review("2026-10-10T12:00:00Z")
            self.assertEqual(sync(board, "review_changed").outcome, "skipped", start)
            self.assertEqual(board.pr_status, start)
            self.assertFalse([m for m in board.mutations if m[0] == "set"])

    def test_without_a_recorded_review_fails_closed(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("Review")
        audit = sync(board, "review_changed")
        self.assertEqual(audit.outcome, "skipped")
        self.assertIn("no changes-requested review", audit.reason)
        self.assertEqual(board.pr_status, "Review")

    def test_delayed_review_run_cannot_undo_a_newer_review_request(self):
        # T1 changes requested (its run is delayed); T2 re-review requested and
        # its run moves the PR to Review; T3 the T1 run executes.
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("Changes requested")
        board.submit_changes_review("2026-10-10T11:00:00Z")
        board.request_review("2026-10-10T12:00:00Z")
        self.assertEqual(sync(board, "review_requested").outcome, "updated")
        audit = sync(board, "review_changed")
        self.assertEqual(audit.outcome, "skipped")
        self.assertIn("no changes-requested review is unanswered", audit.reason)
        self.assertEqual(board.pr_status, "Review")

    def test_delayed_review_run_cannot_undo_a_newer_draft_conversion(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("Review")
        board.submit_changes_review("2026-10-10T11:00:00Z")
        board.convert_to_draft("2026-10-10T12:00:00Z")
        self.assertEqual(sync(board, "converted_to_draft").outcome, "updated")
        self.assertEqual(sync(board, "review_changed").outcome, "skipped")
        self.assertEqual(board.pr_status, "In Progress")

    def test_a_request_at_the_same_instant_does_not_answer_the_review(self):
        # Ties resolve toward the review: both runs leave Changes requested.
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("Review")
        board.request_review("2026-10-10T12:00:00Z")
        board.submit_changes_review("2026-10-10T12:00:00Z")
        self.assertEqual(sync(board, "review_changed").outcome, "updated")
        self.assertEqual(sync(board, "review_requested").outcome, "skipped")
        self.assertEqual(board.pr_status, "Changes requested")

    def test_requesting_another_reviewer_keeps_changes_requested(self):
        # Alice asked for changes; requesting Bob does not answer her review.
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("Changes requested")
        board.submit_changes_review("2026-10-10T11:00:00Z", reviewer="alice")
        board.request_review("2026-10-10T12:00:00Z", reviewer="bob")
        audit = sync(board, "review_requested")
        self.assertEqual(audit.outcome, "skipped")
        self.assertIn("changes requested by alice", audit.reason)
        self.assertEqual(board.pr_status, "Changes requested")
        # Re-requesting Alice answers it.
        board.request_review("2026-10-10T13:00:00Z", reviewer="alice")
        self.assertEqual(sync(board, "review_requested").outcome, "updated")
        self.assertEqual(board.pr_status, "Review")

    def test_every_reviewer_must_be_re_requested(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("Changes requested")
        board.submit_changes_review("2026-10-10T11:00:00Z", reviewer="alice")
        board.submit_changes_review("2026-10-10T11:30:00Z", reviewer="bob")
        board.request_review("2026-10-10T12:00:00Z", reviewer="alice")
        self.assertEqual(sync(board, "review_requested").outcome, "skipped")
        board.request_review("2026-10-10T12:30:00Z", reviewer="bob")
        self.assertEqual(sync(board, "review_requested").outcome, "updated")
        self.assertEqual(board.pr_status, "Review")

    def test_a_relay_for_a_comment_review_only_confirms_the_review_history(self):
        # A non-changes-requested review's relay run may still arrive. It
        # sets Changes requested only while a review is unanswered.
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("Review")
        board.submit_changes_review("2026-10-10T11:00:00Z")
        board.request_review("2026-10-10T12:00:00Z")
        self.assertEqual(sync(board, "review_changed").outcome, "skipped")
        self.assertEqual(board.pr_status, "Review")

    def test_a_later_approval_by_the_same_reviewer_withdraws_the_request(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("Review")
        board.submit_changes_review("2026-10-10T11:00:00Z")
        board.approve()
        self.assertEqual(sync(board, "review_changed").outcome, "skipped")
        self.assertEqual(board.pr_status, "Review")

    def test_a_dismissed_review_releases_changes_requested(self):
        for draft, expected in ((False, "Review"), (True, "In Progress")):
            board = FakeBoard(draft=draft)
            board.native_auto_add()
            board.submit_changes_review("2026-10-10T11:00:00Z")
            self.assertEqual(sync(board, "review_changed").outcome, "updated")
            self.assertEqual(board.pr_status, "Changes requested")
            board.dismiss()
            audit = sync(board, "review_changed")
            self.assertEqual(audit.outcome, "updated", draft)
            self.assertIn("by alice was dismissed or withdrawn", audit.reason)
            self.assertEqual(board.pr_status, expected)

    def test_a_later_approval_releases_changes_requested(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("Changes requested")
        board.submit_changes_review("2026-10-10T11:00:00Z")
        board.approve()
        self.assertEqual(sync(board, "review_changed").outcome, "updated")
        self.assertEqual(board.pr_status, "Review")

    def test_dismissing_one_of_two_change_requests_keeps_changes_requested(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("Changes requested")
        board.submit_changes_review("2026-10-10T11:00:00Z", reviewer="alice")
        board.submit_changes_review("2026-10-10T11:30:00Z", reviewer="bob")
        board.dismiss(reviewer="alice")
        audit = sync(board, "review_changed")
        self.assertEqual(audit.outcome, "unchanged")
        self.assertEqual(board.pr_status, "Changes requested")

    def test_a_hand_set_changes_requested_survives_comment_and_unrelated_approval_runs(self):
        # No change request stands behind it, so nothing was withdrawn.
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("Changes requested", at="2026-10-10T11:00:00Z")
        audit = sync(board, "review_changed")  # a bot's comment review
        self.assertEqual(audit.outcome, "skipped")
        self.assertIn("no change request was dismissed or withdrawn", audit.reason)
        board.approve(reviewer="bob")  # never requested changes
        self.assertEqual(sync(board, "review_changed").outcome, "skipped")
        self.assertEqual(board.pr_status, "Changes requested")
        self.assertFalse([m for m in board.mutations if m[0] == "set"])

    def test_a_withdrawal_before_the_status_was_set_does_not_release_it(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.submit_changes_review("2026-10-10T10:00:00Z")
        board.dismiss(at="2026-10-10T11:00:00Z")
        board.person_sets("Changes requested", at="2026-10-10T12:00:00Z")
        self.assertEqual(sync(board, "review_changed").outcome, "skipped")
        self.assertEqual(board.pr_status, "Changes requested")
        # A later withdrawal does.
        board.submit_changes_review("2026-10-10T13:00:00Z")
        board.dismiss(at="2026-10-10T14:00:00Z")
        self.assertEqual(sync(board, "review_changed").outcome, "updated")
        self.assertEqual(board.pr_status, "Review")

    def test_a_dismissal_between_the_review_runs_read_and_write_is_undone(self):
        # The review run reads Alice unanswered; the dismissal lands before its
        # write, so the dismissal's run would find it older than the Status.
        for draft, expected in ((False, "Review"), (True, "In Progress")):
            board = FakeBoard(draft=draft)
            board.native_auto_add()
            board.person_sets("Review" if not draft else "In Progress", at="2026-10-10T09:00:00Z")
            board.submit_changes_review("2026-10-10T10:00:00Z")
            board.before_set = lambda board=board: board.dismiss(at="2026-10-10T10:01:00Z")
            audit = sync(board, "review_changed")
            self.assertEqual(audit.outcome, "corrected", draft)
            self.assertIn("withdrawn during the write", audit.reason)
            self.assertEqual(board.pr_status, expected)
            board.status_at = "2026-10-10T10:02:00Z"  # GitHub's time for that write
            self.assertEqual(sync(board, "review_changed").outcome, "skipped")  # the dismissal's own run
            self.assertEqual(board.pr_status, expected)

    def test_an_approval_during_a_late_opened_runs_write_is_undone(self):
        # The late `opened` run retargets to Changes requested; Alice approves
        # before the write lands.
        board = FakeBoard(draft=False)
        board.submit_changes_review("2026-10-10T10:00:00Z")
        board.before_set = lambda: board.approve(at="2026-10-10T10:01:00Z")
        audit = sync(board, "opened")
        self.assertTrue(audit.item_added)
        self.assertEqual(audit.outcome, "corrected")
        self.assertEqual(board.pr_status, "Review")

    def test_release_only_moves_changes_requested(self):
        # With no unanswered review the run leaves every other Status alone.
        for start in (None, "Inbox", "Ready", "In Progress", "Review", "Blocked", "Done"):
            board = FakeBoard(draft=False)
            board.native_auto_add()
            if start:
                board.person_sets(start)
            board.submit_changes_review("2026-10-10T11:00:00Z")
            board.dismiss()
            self.assertEqual(sync(board, "review_changed").outcome, "skipped", start)
            self.assertEqual(board.pr_status, start)
            self.assertFalse([m for m in board.mutations if m[0] == "set"])

    def test_a_delayed_review_run_and_a_dismissal_end_released_in_either_order(self):
        # Both relay runs re-derive the state, so the later run's history wins.
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("Review")
        board.submit_changes_review("2026-10-10T11:00:00Z")
        board.dismiss()
        sync(board, "review_changed")
        sync(board, "review_changed")
        self.assertEqual(board.pr_status, "Review")

    def test_review_run_after_the_re_review_run_in_either_order_ends_right(self):
        # Changes at T1, re-review at T2: whichever run executes first, the
        # PR ends in Review. Changes at T2 after a re-review at T1: it ends
        # in Changes requested.
        for first in ("review_changed", "review_requested"):
            board = FakeBoard(draft=False)
            board.native_auto_add()
            board.person_sets("Review")
            board.submit_changes_review("2026-10-10T11:00:00Z")
            board.request_review("2026-10-10T12:00:00Z")
            second = "review_requested" if first == "review_changed" else "review_changed"
            sync(board, first)
            sync(board, second)
            self.assertEqual(board.pr_status, "Review", first)
        for first in ("review_changed", "review_requested"):
            board = FakeBoard(draft=False)
            board.native_auto_add()
            board.person_sets("Changes requested")
            board.request_review("2026-10-10T11:00:00Z")
            board.submit_changes_review("2026-10-10T12:00:00Z")
            second = "review_requested" if first == "review_changed" else "review_changed"
            sync(board, first)
            sync(board, second)
            self.assertEqual(board.pr_status, "Changes requested", first)

    def test_late_opened_run_keeps_an_outstanding_changes_requested(self):
        # The review lands before the delayed `opened` run adds the item (for
        # example when the review relay is unavailable): the run writes
        # Changes requested, not Review.
        for draft in (False, True):
            board = FakeBoard(draft=draft)
            board.submit_changes_review("2026-10-10T12:00:00Z")
            audit = sync(board, "opened")
            self.assertTrue(audit.item_added)
            self.assertEqual(board.pr_status, "Changes requested", draft)
            self.assertIn("still outstanding", audit.reason)

    def test_ready_for_review_keeps_an_unanswered_changes_requested(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("In Progress")
        board.submit_changes_review("2026-10-10T11:00:00Z")
        self.assertEqual(sync(board, "ready_for_review").outcome, "updated")
        self.assertEqual(board.pr_status, "Changes requested")
        # Answered by a newer review request, the next ready run moves on.
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("In Progress")
        board.submit_changes_review("2026-10-10T11:00:00Z")
        board.request_review("2026-10-10T12:00:00Z")
        sync(board, "ready_for_review")
        self.assertEqual(board.pr_status, "Review")

    def test_adds_a_missing_item_and_sets_changes_requested(self):
        board = FakeBoard(draft=False)
        board.submit_changes_review("2026-10-10T12:00:00Z")
        audit = sync(board, "review_changed")
        self.assertTrue(audit.item_added)
        self.assertEqual(board.pr_status, "Changes requested")

    def test_merge_during_the_write_ends_in_done(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("Review")
        board.submit_changes_review("2026-10-10T12:00:00Z")
        board.after_set = board.merge
        self.assertEqual(sync(board, "review_changed").outcome, "corrected")
        self.assertEqual(board.pr_status, "Done")

    def test_closed_pr_is_skipped(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("Review")
        board.submit_changes_review("2026-10-10T12:00:00Z")
        board.merge(merged=False)
        board.flush_native()
        self.assertEqual(sync(board, "review_changed").outcome, "skipped")
        self.assertEqual(board.pr_status, "Done")


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

    def test_delayed_review_request_cannot_erase_newer_changes_requested(self):
        # T1 re-review requested (its run is delayed); T2 the reviewer asks for
        # changes again and the native rule lands; T3 the T1 run executes.
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.request_changes("2026-10-10T10:00:00Z")
        board.flush_native()
        board.request_review("2026-10-10T11:00:00Z")
        board.request_changes("2026-10-10T12:00:00Z")
        board.flush_native()
        audit = sync(board, "review_requested")
        self.assertEqual(audit.outcome, "skipped")
        self.assertIn("changes requested by alice", audit.reason)
        self.assertEqual(board.pr_status, "Changes requested")
        self.assertFalse([m for m in board.mutations if m[0] == "set"])

    def test_review_request_without_a_recorded_request_fails_closed(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("Changes requested")
        self.assertEqual(sync(board, "review_requested").outcome, "skipped")
        self.assertEqual(board.pr_status, "Changes requested")

    def test_review_request_at_the_same_instant_as_changes_fails_closed(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.request_review("2026-10-10T11:00:00Z")
        board.request_changes("2026-10-10T11:00:00Z")
        board.flush_native()
        self.assertEqual(sync(board, "review_requested").outcome, "skipped")

    def test_changes_requested_during_the_write_is_restored(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.request_changes("2026-10-10T10:00:00Z")
        board.flush_native()
        board.request_review("2026-10-10T11:00:00Z")

        def review_now():
            board.request_changes("2026-10-10T12:00:00Z")
            board.flush_native()  # native rule lands before the write

        board.before_set = review_now
        audit = sync(board, "review_requested")
        self.assertEqual(audit.outcome, "corrected")
        self.assertEqual(board.pr_status, "Changes requested")

    def test_close_during_the_corrective_write_still_ends_in_done(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.request_changes("2026-10-10T10:00:00Z")
        board.flush_native()
        board.request_review("2026-10-10T11:00:00Z")

        def close_now():
            board.merge(merged=False)
            board.flush_native()  # native Done lands before the corrective write

        def review_now():
            board.request_changes("2026-10-10T12:00:00Z")
            board.flush_native()
            board.before_set = close_now

        board.before_set = review_now
        audit = sync(board, "review_requested")
        self.assertEqual(audit.outcome, "corrected")
        self.assertIn("closed during the write", audit.reason)
        self.assertEqual(board.pr_status, "Done")

    def test_delayed_draft_conversion_cannot_erase_newer_changes_requested(self):
        # T1 converted to draft (its run is delayed); T2 a reviewer still asks
        # for changes and the native rule lands; T3 the T1 run executes.
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("Review")
        board.convert_to_draft("2026-10-10T11:00:00Z")
        board.request_changes("2026-10-10T12:00:00Z")
        board.flush_native()
        audit = sync(board, "converted_to_draft")
        self.assertEqual(audit.outcome, "skipped")
        self.assertIn("changes were requested after", audit.reason)
        self.assertEqual(board.pr_status, "Changes requested")

    def test_draft_conversion_without_a_recorded_event_fails_closed(self):
        board = FakeBoard(draft=True)
        board.native_auto_add()
        board.person_sets("Review")
        self.assertEqual(sync(board, "converted_to_draft").outcome, "skipped")
        self.assertEqual(board.pr_status, "Review")

    def test_changes_requested_during_a_draft_conversion_write_is_restored(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.person_sets("Review")
        board.convert_to_draft("2026-10-10T11:00:00Z")

        def review_now():
            board.request_changes("2026-10-10T12:00:00Z")
            board.flush_native()

        board.before_set = review_now
        self.assertEqual(sync(board, "converted_to_draft").outcome, "corrected")
        self.assertEqual(board.pr_status, "Changes requested")

    def test_merge_during_item_creation_ends_in_done(self):
        # The merge lands after the add mutation but before the re-read, with
        # the native Done rule either already delivered or still pending.
        for native_delivered in (True, False):
            board = FakeBoard()

            def merge_now(board=board, delivered=native_delivered):
                board.merge()
                if delivered:
                    board.flush_native()

            board.after_add = merge_now
            audit = sync(board, "opened")
            self.assertEqual(audit.outcome, "skipped" if native_delivered else "corrected")
            board.flush_native()
            self.assertEqual(board.pr_status, "Done", native_delivered)

    def test_merge_between_first_read_and_add_ends_in_done(self):
        # The PR merges while it is not on the board, so the native Done rule
        # has no item to update; the item project-sync then adds gets Done.
        board = FakeBoard()

        def merge_now():
            board.merge()
            board.flush_native()

        board.before_add = merge_now
        audit = sync(board, "opened")
        self.assertTrue(audit.item_added)
        self.assertEqual(audit.outcome, "corrected")
        self.assertEqual(board.pr_status, "Done")

    def test_close_between_first_read_and_add_keeps_a_status_set_meanwhile(self):
        board = FakeBoard()

        def close_and_classify():
            board.merge(merged=False)
            board.native_auto_add()
            board.person_sets("Blocked")

        board.before_add = close_and_classify
        audit = sync(board, "opened")
        self.assertEqual(audit.outcome, "skipped")
        self.assertEqual(board.pr_status, "Blocked")

    def test_a_person_writing_after_the_run_wins(self):
        board = FakeBoard(draft=False)
        board.native_auto_add()
        board.after_set = lambda: board.person_sets("Blocked")
        audit = sync(board, "opened")
        self.assertEqual(audit.outcome, "superseded")
        self.assertEqual(board.pr_status, "Blocked")

    def test_only_the_pr_is_ever_added_or_written(self):
        board = FakeBoard(draft=False)
        sync(board, "opened")
        board.convert_to_draft("2026-10-10T11:00:00Z")
        self.assertEqual(sync(board, "converted_to_draft").outcome, "updated")
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


class ReviewHistoryTests(unittest.TestCase):
    """Withdrawals derived from GitHub's review history, end to end through
    GraphQL parsing and the ``review_changed`` decision."""

    def _decide(self, status, status_at, *, changes=(), approvals=(), latest=(), dismissals=()):
        def reviews(pairs):
            return {"nodes": [{"submittedAt": at, "author": {"login": who}} for who, at in pairs]}

        pr_raw = {
            "id": PR_ID, "state": "OPEN", "isDraft": False,
            "projectItems": {"nodes": [{
                "id": "item", "isArchived": False, "project": {"id": PROJECT_ID},
                "fieldValueByName": {"__typename": "ProjectV2ItemFieldSingleSelectValue",
                                     "name": status, "updatedAt": status_at},
            }]},
            "changeReviews": reviews(changes),
            "approvals": reviews(approvals),
            "latestOpinionatedReviews": {"nodes": [
                {"state": state, "submittedAt": at, "author": {"login": who}} for who, state, at in latest
            ]},
            "dismissals": {"nodes": [
                {"createdAt": at, "previousReviewState": "CHANGES_REQUESTED", "review": {"author": {"login": who}}}
                for who, at in dismissals
            ]},
        }
        payload = {"repository": {"pullRequest": pr_raw}}
        pr = ps.GraphQLApi(lambda q, v: payload).load_pr(CONFIG, PR_NUMBER, PROJECT_ID)
        return ps.decide(CONFIG, "review_changed", pr)

    def test_an_approval_after_a_change_request_releases_it(self):
        target, _ = self._decide(
            "Changes requested", "2026-10-10T10:30:00Z",
            changes=[("alice", "2026-10-10T10:00:00Z")],
            approvals=[("alice", "2026-10-10T11:00:00Z")],
            latest=[("alice", "APPROVED", "2026-10-10T11:00:00Z")],
        )
        self.assertEqual(target, "review")

    def test_a_delayed_run_does_not_replay_an_old_withdrawal(self):
        # Released at T2 by the approval's run; set by hand at T3; the same
        # approval's relay run arrives again late. T2 is older than T3.
        target, reason = self._decide(
            "Changes requested", "2026-10-10T13:00:00Z",
            changes=[("alice", "2026-10-10T11:00:00Z")],
            approvals=[("alice", "2026-10-10T12:00:00Z")],
            latest=[("alice", "APPROVED", "2026-10-10T12:00:00Z")],
        )
        self.assertIsNone(target)
        self.assertIn("no change request was dismissed or withdrawn", reason)

    def test_one_reviewers_approval_does_not_release_anothers_change_request(self):
        target, reason = self._decide(
            "Changes requested", "2026-10-10T10:30:00Z",
            changes=[("alice", "2026-10-10T10:00:00Z"), ("bob", "2026-10-10T10:15:00Z")],
            approvals=[("alice", "2026-10-10T11:00:00Z")],
            latest=[("alice", "APPROVED", "2026-10-10T11:00:00Z"), ("bob", "CHANGES_REQUESTED", "2026-10-10T10:15:00Z")],
        )
        self.assertEqual((target, reason), ("changes_requested", "already set"))

    def test_approvals_after_a_dismissal_withdraw_nothing_more(self):
        # The dismissed review is no longer CHANGES_REQUESTED, so a later
        # approval has no change request to withdraw; the dismissal (T2) is
        # older than the hand-set Status (T3).
        target, _ = self._decide(
            "Changes requested", "2026-10-10T13:00:00Z",
            approvals=[("alice", "2026-10-10T14:00:00Z"), ("alice", "2026-10-10T15:00:00Z")],
            latest=[("alice", "APPROVED", "2026-10-10T15:00:00Z")],
            dismissals=[("alice", "2026-10-10T12:00:00Z")],
        )
        self.assertIsNone(target)

    def test_repeated_dismissal_and_approval_reads_are_idempotent(self):
        kwargs = dict(
            changes=[("alice", "2026-10-10T10:00:00Z")],
            approvals=[("alice", "2026-10-10T11:00:00Z"), ("alice", "2026-10-10T12:00:00Z")],
            latest=[("alice", "APPROVED", "2026-10-10T12:00:00Z")],
        )
        first = self._decide("Changes requested", "2026-10-10T10:30:00Z", **kwargs)
        second = self._decide("Changes requested", "2026-10-10T10:30:00Z", **kwargs)
        self.assertEqual(first, second)
        self.assertEqual(first[0], "review")

    def test_blocked_and_done_survive_a_fresh_withdrawal(self):
        for status in ("Blocked", "Done"):
            target, _ = self._decide(
                status, "2026-10-10T09:00:00Z",
                changes=[("alice", "2026-10-10T10:00:00Z")],
                approvals=[("alice", "2026-10-10T11:00:00Z")],
                latest=[("alice", "APPROVED", "2026-10-10T11:00:00Z")],
                dismissals=[("bob", "2026-10-10T11:30:00Z")],
            )
            self.assertIsNone(target, status)


class GraphQLApiTests(unittest.TestCase):
    def _pr_payload(self, nodes):
        return {"repository": {"pullRequest": {"id": PR_ID, "state": "OPEN", "isDraft": True, "projectItems": {"nodes": nodes}}}}

    def test_review_times_are_read_and_dismissed_reviews_are_excluded(self):
        payload = self._pr_payload([])
        pr_raw = payload["repository"]["pullRequest"]
        pr_raw["reviewRequests"] = {"nodes": [
            {"createdAt": "2026-10-10T11:00:00Z", "requestedReviewer": {"__typename": "User", "login": "Alice"}},
            {"createdAt": "2026-10-10T11:30:00Z", "requestedReviewer": {"__typename": "Team", "slug": "core"}},
            {"createdAt": "2026-10-10T11:40:00Z", "requestedReviewer": None},
        ]}
        pr_raw["latestOpinionatedReviews"] = {"nodes": [
            {"state": "CHANGES_REQUESTED", "submittedAt": "2026-10-10T10:00:00Z", "author": {"login": "Bob"}},
            {"state": "APPROVED", "submittedAt": "2026-10-10T10:30:00Z", "author": {"login": "carol"}},
            {"state": "DISMISSED", "submittedAt": "2026-10-10T10:40:00Z", "author": {"login": "dave"}},
            {"state": "CHANGES_REQUESTED", "submittedAt": "2026-10-10T10:50:00Z", "author": None},
        ]}
        pr_raw["convertedToDraft"] = {"nodes": [{"createdAt": "2026-10-10T09:00:00Z"}]}
        pr_raw["changeReviews"] = {"nodes": [
            {"submittedAt": "2026-10-10T10:00:00Z", "author": {"login": "Bob"}},
            {"submittedAt": "2026-10-10T10:10:00Z", "author": {"login": "Carol"}},
        ]}
        pr_raw["approvals"] = {"nodes": [
            {"submittedAt": "2026-10-10T10:30:00Z", "author": {"login": "carol"}},
        ]}
        pr_raw["dismissals"] = {"nodes": [
            {"createdAt": "2026-10-10T10:45:00Z", "previousReviewState": "CHANGES_REQUESTED", "review": {"author": {"login": "Dave"}}},
            {"createdAt": "2026-10-10T10:46:00Z", "previousReviewState": "APPROVED", "review": {"author": {"login": "erin"}}},
        ]}
        seen = []
        api = ps.GraphQLApi(lambda q, v: seen.append(q) or payload)
        pr = api.load_pr(CONFIG, PR_NUMBER, PROJECT_ID)
        self.assertEqual(pr.review_requests, (("alice", "2026-10-10T11:00:00Z"), ("team:core", "2026-10-10T11:30:00Z")))
        self.assertEqual(pr.changes_requested, (("bob", "2026-10-10T10:00:00Z"), ("ghost", "2026-10-10T10:50:00Z")))
        self.assertEqual(pr.last_review_requested_at, "2026-10-10T11:30:00Z")
        self.assertEqual(pr.last_changes_requested_at, "2026-10-10T10:50:00Z")
        self.assertEqual(pr.last_converted_to_draft_at, "2026-10-10T09:00:00Z")
        self.assertEqual(ps.unanswered_changes(pr), ["bob", "ghost"])
        # Dave's change request was dismissed; Carol approved after hers.
        # Erin's dismissed review was an approval, so it withdraws nothing.
        self.assertEqual(pr.withdrawals, (("dave", "2026-10-10T10:45:00Z"), ("carol", "2026-10-10T10:30:00Z")))
        self.assertIn("reviews(last: 100, states: [CHANGES_REQUESTED])", seen[0])
        self.assertIn("reviews(last: 100, states: [APPROVED])", seen[0])
        self.assertIn("itemTypes: [REVIEW_DISMISSED_EVENT]", seen[0])
        self.assertIn("itemTypes: [CONVERT_TO_DRAFT_EVENT]", seen[0])
        self.assertIn("timelineItems(last: 100, itemTypes: [REVIEW_REQUESTED_EVENT])", seen[0])
        self.assertIn("latestOpinionatedReviews(first: 100)", seen[0])

    def test_a_repeat_approval_withdraws_nothing_new(self):
        # Alice requests changes (T1) and approves (T2); a person sets
        # Changes requested (T3); Alice approves again (T4). Only T2 is a
        # withdrawal, so the hand-set Status stays.
        nodes = [{"id": "mine", "isArchived": False, "project": {"id": PROJECT_ID},
                  "fieldValueByName": {"__typename": "ProjectV2ItemFieldSingleSelectValue",
                                       "name": "Changes requested", "updatedAt": "2026-10-10T13:00:00Z"}}]
        payload = self._pr_payload(nodes)
        pr_raw = payload["repository"]["pullRequest"]
        pr_raw["isDraft"] = False
        pr_raw["changeReviews"] = {"nodes": [{"submittedAt": "2026-10-10T11:00:00Z", "author": {"login": "alice"}}]}
        pr_raw["approvals"] = {"nodes": [
            {"submittedAt": "2026-10-10T12:00:00Z", "author": {"login": "alice"}},
            {"submittedAt": "2026-10-10T14:00:00Z", "author": {"login": "alice"}},
        ]}
        pr_raw["latestOpinionatedReviews"] = {"nodes": [
            {"state": "APPROVED", "submittedAt": "2026-10-10T14:00:00Z", "author": {"login": "alice"}},
        ]}
        pr = ps.GraphQLApi(lambda q, v: payload).load_pr(CONFIG, PR_NUMBER, PROJECT_ID)
        self.assertEqual(pr.withdrawals, (("alice", "2026-10-10T12:00:00Z"),))
        target, reason = ps.decide(CONFIG, "review_changed", pr)
        self.assertIsNone(target)
        self.assertIn("no change request was dismissed or withdrawn", reason)
        # A new change request followed by an approval is a fresh withdrawal.
        pr_raw["changeReviews"]["nodes"].append({"submittedAt": "2026-10-10T15:00:00Z", "author": {"login": "alice"}})
        pr_raw["approvals"]["nodes"].append({"submittedAt": "2026-10-10T16:00:00Z", "author": {"login": "alice"}})
        pr = ps.GraphQLApi(lambda q, v: payload).load_pr(CONFIG, PR_NUMBER, PROJECT_ID)
        self.assertEqual(ps.decide(CONFIG, "review_changed", pr)[0], "review")

    def test_items_of_other_projects_are_ignored(self):
        nodes = [
            {"id": "other", "isArchived": False, "project": {"id": "PVT_other"}, "fieldValueByName": None},
            {"id": "mine", "isArchived": False, "project": {"id": PROJECT_ID},
             "fieldValueByName": {"__typename": "ProjectV2ItemFieldSingleSelectValue", "name": "Review",
                                  "updatedAt": "2026-10-10T08:00:00Z"}},
        ]
        api = ps.GraphQLApi(lambda q, v: self._pr_payload(nodes))
        pr = api.load_pr(CONFIG, PR_NUMBER, PROJECT_ID)
        self.assertEqual((pr.item.id, pr.item.status), ("mine", "Review"))
        self.assertEqual(pr.item.status_updated_at, "2026-10-10T08:00:00Z")

    def test_item_beyond_the_first_page_fails_closed(self):
        payload = self._pr_payload([{"id": "other", "isArchived": False, "project": {"id": "PVT_other"}, "fieldValueByName": None}])
        payload["repository"]["pullRequest"]["projectItems"]["pageInfo"] = {"hasNextPage": True}
        api = ps.GraphQLApi(lambda q, v: payload)
        with self.assertRaisesRegex(ps.ApiError, "more than 50 projects"):
            api.load_pr(CONFIG, PR_NUMBER, PROJECT_ID)
        payload["repository"]["pullRequest"]["projectItems"]["pageInfo"] = {"hasNextPage": False}
        self.assertIsNone(api.load_pr(CONFIG, PR_NUMBER, PROJECT_ID).item)

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

    def test_transport_failures_and_bad_bodies_become_api_errors(self):
        import http.client
        from unittest import mock

        class Body:
            def __init__(self, raw):
                self.raw = raw

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return self.raw

        send = ps.http_transport("t")
        for failure in (TimeoutError("t"), ConnectionResetError("r"), http.client.RemoteDisconnected("c"),
                        http.client.IncompleteRead(b"x")):
            with mock.patch.object(ps.urllib.request, "urlopen", side_effect=failure):
                with self.assertRaises(ps.ApiError, msg=type(failure).__name__):
                    send("query", {})
        for raw in (b"<html>", b"[1]"):
            with mock.patch.object(ps.urllib.request, "urlopen", return_value=Body(raw)):
                with self.assertRaises(ps.ApiError, msg=raw):
                    send("query", {})

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

    def test_triggers_only_on_the_four_pr_actions_and_the_review_signal(self):
        self.assertRegex(self.text, r"(?m)^  pull_request_target:\n    types: \[opened, ready_for_review, converted_to_draft, review_requested\]$")
        self.assertIn(
            "  workflow_run:\n    workflows: [Project sync review signal]\n    types: [completed]\n", self.text
        )
        for trigger in ("pull_request:", "push:", "pull_request_review:", "schedule:", "workflow_dispatch:"):
            self.assertNotIn(f"\n  {trigger}", self.text)

    def test_a_relayed_review_needs_a_successful_signal_for_a_pr_in_this_repository(self):
        job_if = self.text.split("    if: >-\n", 1)[1].split("    runs-on:", 1)[0]
        for condition in (
            "github.repository == 'Fooftilly/PRKS'",
            "github.event.workflow_run.event == 'pull_request_review'",
            "github.event.workflow_run.conclusion == 'success'",
            "github.event.workflow_run.head_repository.full_name == github.repository",
            "github.event.workflow_run.pull_requests[0].number",
        ):
            self.assertIn(condition, job_if)
        self.assertIn(
            "EVENT_ACTION: ${{ github.event_name == 'workflow_run' && 'review_changed' || github.event.action }}",
            self.text,
        )
        self.assertIn(
            "PR_NUMBER: ${{ github.event.pull_request.number || github.event.workflow_run.pull_requests[0].number }}",
            self.text,
        )

    def test_least_privilege(self):
        self.assertIn("\npermissions: {}\n", self.text)
        grants = re.findall(r"(?m)^ +([a-z-]+): (read|write)$", self.text)
        self.assertEqual(grants, [("contents", "read")])

    def test_never_checks_out_pull_request_code(self):
        # Without a ref, pull_request_target checks out the base commit.
        self.assertNotRegex(self.text, r"(?m)^ +ref:")
        self.assertIn("persist-credentials: false", self.text)
        self.assertNotIn("github.event.pull_request.head", self.text)
        self.assertNotIn("workflow_run.head_sha", self.text)
        self.assertNotIn("workflow_run.head_branch", self.text)

    def test_runs_are_serialized_per_pr_and_queued(self):
        self.assertIn(
            "concurrency:\n  group: project-sync-pr-${{ github.event.pull_request.number"
            " || github.event.workflow_run.pull_requests[0].number || github.run_id }}\n"
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
        self.assertIn("      github.repository == 'Fooftilly/PRKS' &&\n", self.text)


class SignalWorkflowShapeTests(unittest.TestCase):
    """The unprivileged relay: it runs the PR's copy of its file, so it must hold nothing."""

    @classmethod
    def setUpClass(cls):
        cls.text = _SIGNAL.read_text()

    def test_name_matches_the_workflow_run_trigger(self):
        self.assertTrue(self.text.startswith("name: Project sync review signal\n"))

    def test_triggers_only_on_submitted_and_dismissed_reviews(self):
        self.assertIn("on:\n  pull_request_review:\n    types: [submitted, dismissed]\n\n", self.text)

    def test_holds_no_permission_secret_or_checkout(self):
        self.assertIn("\npermissions: {}\n", self.text)
        self.assertNotRegex(self.text, r"(?m)^ +[a-z-]+: (read|write)$")
        for forbidden in ("secrets.", "vars.", "actions/checkout", "github.token", "upload-artifact"):
            self.assertNotIn(forbidden, self.text)

    def test_other_review_states_skip_the_relay_job(self):
        # Only an optimization: if a skipped run still counts as success,
        # project-sync re-derives the state from the review history (see
        # test_a_later_approval_by_the_same_reviewer_withdraws_the_request).
        self.assertIn(
            "    if: >-\n      github.event.action == 'dismissed' ||\n"
            "      github.event.review.state == 'changes_requested' ||\n"
            "      github.event.review.state == 'approved'\n",
            self.text,
        )

    def test_no_expressions_in_run_blocks(self):
        for block in re.findall(r"(?ms)^ +run: (.*?)(?=^ +- |\Z)", self.text):
            self.assertNotIn("${{", block)


if __name__ == "__main__":
    unittest.main()
