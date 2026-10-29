"""Offline tests for the reviewed Project #3 batch triage tool (#441)."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
from pathlib import Path
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import project3_triage as t
import project3_migrate as pm
from test_project3_migrate import FakeAPI


def intent(number=815, *, agent="cursor", status="Ready", execution="Agent"):
    return {"number": number, "set": {"Agent": agent, "Status": status, "Execution": execution},
            "reason": "Confirmed against target roadmap and current open pull requests.",
            "roadmap_checked": True, "overlap_checked": True}


class Fixture:
    def __init__(self):
        self.api = FakeAPI(fields={
            "Status": ["Inbox", "Backlog", "Ready", "In Progress", "Blocked", "Done"],
            "Execution": ["Human", "Agent", "Mixed"],
            "Agent": ["Cursor Cloud", "Claude Code", "ChatGPT/Codex", "Manual", "Unassigned"],
        })
        self.api.put("Issue", 815, Status="Inbox")
        self.api.put("Issue", 509, Status="Inbox")
        self.api.put("Issue", 635, Status="In Progress", Agent="Claude Code", Execution="Agent")
        self.issues = {
            n: {"number": n, "state": "open", "updated_at": "2026-10-29T17:00:00Z",
                "labels": [{"name": "bug"}], "title": "Test issue"}
            for n in (815, 509, 635)
        }
        self.issues[509]["labels"] = [{"name": "candidate"}, {"name": "audit-finding"}]
        self.dependencies = {815: [], 509: [], 635: []}

    def read(self, url):
        import re
        match = re.fullmatch(
            r"https://api.github.com/repos/Fooftilly/PRKS/issues/([0-9]+)"
            r"(/dependencies/blocked_by\?per_page=100&page=([0-9]+))?", url)
        if not match:
            raise AssertionError(f"unexpected URL: {url}")
        number = int(match.group(1))
        if number not in self.issues:
            raise t.TriageError("issue inaccessible")
        if match.group(2):
            page = int(match.group(3))
            return copy.deepcopy(self.dependencies[number] if page == 1 else [])
        return copy.deepcopy(self.issues[number])

    def project(self, *, apply=False):
        return pm.Project(pm.GraphQL(self.api, allow_mutations=apply))

    def plan(self, *rows):
        # Match the CLI path: validate/normalize aliases before calling propose.
        rows = [copy.deepcopy(row) for row in rows]
        for row in rows:
            t.validate_row(row, plan=False)
        return t.propose(rows, self.project(), self.read)


class BatchTriageTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def approve(self, plan):
        for row in plan["items"]:
            row["approved"] = True
        return plan

    def test_proposal_generates_unapproved_snapshot_and_aliases(self):
        plan = self.fx.plan(intent())
        self.assertEqual(plan["schema"], t.SCHEMA)
        row = plan["items"][0]
        self.assertFalse(row["approved"])
        self.assertEqual(row["set"]["Agent"], "Cursor Cloud")
        self.assertEqual(row["expected_before"], {"Agent": None, "Status": "Inbox", "Execution": None})
        self.assertEqual(row["expected_issue"]["open_blockers"], [])
        self.assertEqual(self.fx.api.mutations, [])

    def test_approved_batch_applies_only_requested_fields(self):
        plan = self.approve(self.fx.plan(intent()))
        changed = t.apply(plan, self.fx.project(apply=True), self.fx.read)
        self.assertEqual(changed, 3)
        self.assertEqual(self.fx.api.items["I815"]["values"], {
            "Status": "Ready", "Agent": "Cursor Cloud", "Execution": "Agent"})
        self.assertEqual(len(self.fx.api.mutations), 3)

    def test_held_row_is_not_written(self):
        plan = self.fx.plan(intent())
        self.assertEqual(t.apply(plan, self.fx.project(apply=True), self.fx.read), 0)
        self.assertEqual(self.fx.api.mutations, [])

    def test_candidate_requires_acceptance(self):
        with self.assertRaisesRegex(t.TriageError, "candidate is not accepted"):
            self.fx.plan(intent(509))
        self.fx.issues[509]["labels"].append({"name": "accepted"})
        self.assertEqual(len(self.fx.plan(intent(509))["items"]), 1)

    def test_open_native_dependency_blocks_ready(self):
        self.fx.dependencies[815] = [{"number": 123, "state": "open"}]
        with self.assertRaisesRegex(t.TriageError, "open native blockers"):
            self.fx.plan(intent())
        self.fx.dependencies[815] = [{"number": 123, "state": "closed"}]
        self.fx.plan(intent())

    def test_protected_project_state_refused(self):
        self.fx.api.items["I815"]["values"]["Status"] = "Blocked"
        with self.assertRaisesRegex(t.TriageError, "protected status"):
            self.fx.plan(intent())

    def test_drift_on_other_issue_aborts_whole_batch(self):
        plan = self.approve(self.fx.plan(intent(), intent(509, status="Inbox")))
        self.fx.issues[509]["updated_at"] = "2026-10-29T17:01:00Z"
        with self.assertRaisesRegex(t.TriageError, "changed since review"):
            t.apply(plan, self.fx.project(apply=True), self.fx.read)
        self.assertEqual(self.fx.api.mutations, [])

    def test_field_change_since_plan_refused(self):
        plan = self.approve(self.fx.plan(intent()))
        self.fx.api.items["I815"]["values"]["Status"] = "Backlog"
        with self.assertRaisesRegex(t.TriageError, "fields changed"):
            t.apply(plan, self.fx.project(apply=True), self.fx.read)
        self.assertEqual(self.fx.api.mutations, [])

    def test_failure_mid_batch_stops_subsequent_writes(self):
        plan = self.approve(self.fx.plan(intent(), intent(509, status="Inbox")))
        def disturb(name, variables):
            # First write begins, then the second issue changes before its turn.
            if len(self.fx.api.mutations) == 0:
                self.fx.issues[509]["updated_at"] = "2026-10-29T17:01:00Z"
        self.fx.api.before_mutation = disturb
        with self.assertRaisesRegex(t.TriageError, "changed since review"):
            t.apply(plan, self.fx.project(apply=True), self.fx.read)
        self.assertEqual(len(self.fx.api.mutations), 3)
        self.assertEqual(self.fx.api.items["I509"]["values"]["Status"], "Inbox")

    def test_partial_approved_item_respects_other_field_change(self):
        plan = self.approve(self.fx.plan(intent()))
        def disturb(name, variables):
            if len(self.fx.api.mutations) == 0:
                self.fx.api.items["I815"]["values"]["Execution"] = "Human"
        self.fx.api.before_mutation = disturb
        with self.assertRaisesRegex(t.TriageError, "post-write verification failed"):
            t.apply(plan, self.fx.project(apply=True), self.fx.read)
        self.assertEqual(len(self.fx.api.mutations), 1)

    def test_invalid_plan_is_rejected(self):
        plan = self.fx.plan(intent())
        plan["items"][0]["approved"] = "yes"
        with self.assertRaisesRegex(t.TriageError, "boolean"):
            t.load_plan(json.dumps(plan).encode())

    def test_review_attestations_required(self):
        row = intent()
        row["overlap_checked"] = False
        with self.assertRaisesRegex(t.TriageError, "review must be attested"):
            t.load_proposals(self.write_json({"items": [row]}))

    def test_unrecognized_agent_rejected(self):
        row = intent(agent="CursorPro")
        with self.assertRaisesRegex(t.TriageError, "unknown Agent"):
            t.load_proposals(self.write_json({"items": [row]}))

    def test_pr_items_refused(self):
        self.fx.issues[815]["pull_request"] = {"url": "https://github.com/"}
        with self.assertRaisesRegex(t.TriageError, "not an open PRKS issue"):
            self.fx.plan(intent())

    def test_main_sha_guard_prevents_writes(self):
        plan = self.approve(self.fx.plan(intent()))
        p = self.write_json(plan)
        with mock.patch.object(t, "token_from_environment", side_effect=AssertionError("no token needed")):
            result = t.main(["apply", "--plan", str(p), "--plan-sha256", "0"*64],
                            transport=self.fx.api, read=self.fx.read)
        self.assertEqual(result, 1)
        self.assertEqual(self.fx.api.mutations, [])

    def test_main_check_is_read_only_and_approved_apply_works(self):
        plan = self.approve(self.fx.plan(intent()))
        p = self.write_json(plan)
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        self.assertEqual(t.main(["check", "--plan", str(p)], transport=self.fx.api, read=self.fx.read), 0)
        self.assertEqual(self.fx.api.mutations, [])
        self.assertEqual(t.main(["apply", "--plan", str(p), "--plan-sha256", h],
                                transport=self.fx.api, read=self.fx.read), 0)
        self.assertEqual(len(self.fx.api.mutations), 3)

    def test_apply_requires_exact_project_option(self):
        self.fx.api.fields["Agent"] = ["Claude Code", "ChatGPT/Codex"]
        # Propose can still create the plan, but apply fails before writes.
        plan = self.approve(self.fx.plan(intent()))
        with self.assertRaisesRegex(t.TriageError, "missing Project option"):
            t.apply(plan, self.fx.project(apply=True), self.fx.read)
        self.assertEqual(self.fx.api.mutations, [])

    def test_inaccessible_dependency_lookup_fails_closed(self):
        def bad_read(url):
            if "blocked_by" in url:
                raise t.TriageError("dependencies unavailable")
            return self.fx.read(url)
        with self.assertRaisesRegex(t.TriageError, "dependencies unavailable"):
            t.propose([intent()], self.fx.project(), bad_read)

    def test_paths_outside_dedicated_folder_are_rejected(self):
        path = Path(__file__).resolve().parents[1] / "triage-plan.json"
        with self.assertRaisesRegex(t.TriageError, "plan paths must be"):
            t.external_plan_path(path)
        with self.assertRaisesRegex(t.TriageError, "plan paths must be"):
            t.external_plan_path(t.TRIAGE_DIR / "notes.txt")

    def write_json(self, value):
        # Keep fixture plans in a dedicated temporary folder, like a user plan.
        if not hasattr(self, "_temp"):
            self._temp = tempfile.TemporaryDirectory()
            self.addCleanup(self._temp.cleanup)
            self._patch_dir = mock.patch.object(t, "TRIAGE_DIR", Path(self._temp.name))
            self._patch_dir.start()
            self.addCleanup(self._patch_dir.stop)
        path = Path(self._temp.name) / "plan.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path


if __name__ == "__main__":
    unittest.main()
