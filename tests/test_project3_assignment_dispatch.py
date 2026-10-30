"""Offline contracts for comment-free Project V2 assignment dispatch."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import project3_assignment_dispatch as dispatch
from test_project3_triage import Fixture, intent


def command(*items, operation="apply"):
    return json.dumps({"version": 1, "operation": operation, "items": items})


def row(number=815, *, before=None, **overrides):
    data = intent(number)
    data["expected_before"] = (
        {"Agent": None, "Status": "Inbox", "Execution": None}
        if before is None else before)
    data.update(overrides)
    return data


def event_for(raw):
    return {"repository": {"full_name": dispatch.REPOSITORY},
            "sender": {"id": dispatch.OWNER_ID, "login": "Fooftilly"},
            "ref": "refs/heads/master",
            "inputs": {"command": raw}}


def env():
    return {"GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REPOSITORY": dispatch.REPOSITORY,
            "GITHUB_REF": "refs/heads/master",
            "GITHUB_RUN_ATTEMPT": "1"}


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def go(self, *rows, operation="apply", mode="apply"):
        return dispatch.execute(
            command(*(rows or (row(),)), operation=operation),
            project=self.fx.project(apply=mode == "apply" and operation == "apply"),
            read=self.fx.read, mode=mode)

    def test_valid_owner_and_master_dispatch(self):
        raw = command(row())
        self.assertEqual(dispatch.authenticate(event_for(raw), env()), raw)

    def test_wrong_actor_id_login_repo_ref_and_event_ref_refused(self):
        raw = command(row())
        for key, value in (("sender", {"id": 22, "login": "Fooftilly"}),
                           ("sender", {"id": dispatch.OWNER_ID, "login": "other"}),
                           ("repository", {"full_name": "other/repo"}),
                           ("ref", "refs/heads/feature")):
            event = event_for(raw)
            event[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(dispatch.DispatchError):
                dispatch.authenticate(event, env())

    def test_wrong_run_context_or_replayed_attempt_refused(self):
        raw = command(row())
        for key, value in (("GITHUB_EVENT_NAME", "push"),
                           ("GITHUB_REPOSITORY", "other/repo"),
                           ("GITHUB_REF", "refs/heads/feature"),
                           ("GITHUB_RUN_ATTEMPT", "2"),
                           ("GITHUB_RUN_ATTEMPT", "")):
            current = env()
            current[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(dispatch.DispatchError):
                dispatch.authenticate(event_for(raw), current)

    def test_missing_oversized_or_nonstring_input_refused(self):
        event = event_for("x" * (dispatch.MAX_BYTES + 1))
        with self.assertRaisesRegex(dispatch.DispatchError, "oversized"):
            dispatch.authenticate(event, env())
        event["inputs"] = {}
        with self.assertRaisesRegex(dispatch.DispatchError, "missing"):
            dispatch.authenticate(event, env())
        event["inputs"] = {"command": {"value": "wrong"}}
        with self.assertRaises(dispatch.DispatchError):
            dispatch.authenticate(event, env())

    def test_parser_rejects_unexpected_or_missing_fields(self):
        for raw in ("not-json", "[]", json.dumps({"version": True, "operation": "apply", "items": [row()]}),
                    json.dumps({"version": 1, "operation": "admin", "items": [row()]}),
                    command({**row(), "command": "os.system('echo hi')"}),
                    command({k: v for k, v in row().items() if k != "expected_before"}),
                    command({**row(), "expected_before": {"Status": "Inbox"}}),
                    command({**row(), "expected_before": {"Agent": False, "Status": "Inbox", "Execution": None}}),
                    command({**row(), "allow_reassignment": "true"}),
                    command(row(), row())):
            with self.subTest(raw=raw[:65]), self.assertRaises(dispatch.DispatchError):
                dispatch.parse(raw)

    def test_command_cannot_target_legacy_inbox(self):
        with self.assertRaisesRegex(dispatch.DispatchError, "inbox"):
            dispatch.parse(command(row(982)))

    def test_check_does_not_write_even_when_dispatch_is_armed(self):
        outcome, changed, issues = self.go(operation="check")
        self.assertIn("CHECK ONLY", outcome)
        self.assertEqual((changed, issues), (0, 1))
        self.assertEqual(self.fx.api.mutations, [])

    def test_default_mode_does_not_write_even_for_apply(self):
        outcome, changed, issues = self.go(mode="")
        self.assertIn("DRY RUN", outcome)
        self.assertEqual((changed, issues), (0, 1))
        self.assertEqual(self.fx.api.mutations, [])

    def test_approved_apply_changes_only_selected_fields(self):
        outcome, changed, issues = self.go()
        self.assertIn("APPLIED", outcome)
        self.assertEqual((changed, issues), (3, 1))
        self.assertEqual(self.fx.api.items["I815"]["values"],
                         {"Agent": "Cursor Cloud", "Status": "Ready", "Execution": "Agent"})

    def test_expected_before_prevents_replay_and_stale_updates(self):
        self.go()
        count = len(self.fx.api.mutations)
        with self.assertRaisesRegex(dispatch.DispatchError, "expected_before"):
            self.go()
        self.assertEqual(len(self.fx.api.mutations), count)
        self.fx.api.items["I815"]["values"]["Status"] = "In Progress"
        with self.assertRaisesRegex(dispatch.DispatchError, "expected_before"):
            self.go()

    def test_open_blocker_and_candidate_refused(self):
        self.fx.dependencies[815] = [{"number": 4, "state": "open"}]
        with self.assertRaisesRegex(Exception, "open native blockers"):
            self.go()
        self.assertFalse(self.fx.api.mutations)
        with self.assertRaisesRegex(Exception, "candidate is not accepted"):
            self.go(row(509))

    def test_reassignment_requires_explicit_authorization(self):
        self.fx.api.items["I815"]["values"]["Agent"] = "Claude Code"
        snapshot = {"Agent": "Claude Code", "Status": "Inbox", "Execution": None}
        with self.assertRaisesRegex(dispatch.DispatchError, "allow_reassignment"):
            self.go(row(before=snapshot))
        result = self.go(row(before=snapshot, allow_reassignment=True))
        self.assertEqual(result[1], 3)

    def test_plan_drift_stops_partial_batch_and_replay(self):
        def disturb(name, variables):
            if not self.fx.api.mutations:
                self.fx.dependencies[815] = [{"number": 4, "state": "open"}]
        self.fx.api.before_mutation = disturb
        with self.assertRaisesRegex(Exception, "issue changed during write"):
            self.go()
        self.assertEqual(len(self.fx.api.mutations), 1)
        with self.assertRaisesRegex(dispatch.DispatchError, "expected_before"):
            self.go()

    def test_partial_failure_report_does_not_claim_changes_or_dump_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary, output = Path(tmp) / "summary", Path(tmp) / "output"
            with mock.patch.dict(os.environ,
                                 {"GITHUB_STEP_SUMMARY": str(summary),
                                  "GITHUB_OUTPUT": str(output)}):
                dispatch.report("FAILED / POSSIBLY PARTIAL", 0, 0, failure=True)
            self.assertIn("FAILED / POSSIBLY PARTIAL", summary.read_text())
            self.assertFalse(output.exists())

    def test_success_audit_is_in_run_files_not_issue_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary, output = Path(tmp) / "summary", Path(tmp) / "output"
            with mock.patch.dict(os.environ,
                                 {"GITHUB_STEP_SUMMARY": str(summary),
                                  "GITHUB_OUTPUT": str(output)}):
                dispatch.report("APPLIED — Project fields verified", 2, 1)
            self.assertIn("field_updates=2", summary.read_text())
            self.assertEqual(output.read_text(), "changed=true\n")

    def test_main_validates_before_creating_graphql_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "event.json"
            source.write_text(json.dumps(event_for(command(row()))))
            bad_env = {"GITHUB_EVENT_PATH": str(source),
                       "GITHUB_EVENT_NAME": "push",
                       "GITHUB_REPOSITORY": dispatch.REPOSITORY,
                       "GITHUB_REF": "refs/heads/master",
                       "GITHUB_RUN_ATTEMPT": "1",
                       "PROJECT_SYNC_TOKEN": "secret-value",
                       "GITHUB_TOKEN": "another-secret"}
            with (mock.patch.dict(os.environ, bad_env, clear=True),
                  mock.patch.object(dispatch.pm, "http_transport") as transport):
                self.assertEqual(dispatch.main(), 1)
                transport.assert_not_called()


class WorkflowPolicyTests(unittest.TestCase):
    def test_dispatch_is_manual_master_only_and_holds_only_protected_secret(self):
        workflow = (ROOT / ".github/workflows/project3-assignment-dispatch.yml").read_text()
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertNotIn("issue_comment:", workflow)
        self.assertNotIn("pull_request_target:", workflow)
        self.assertNotIn("workflow_run:", workflow)
        self.assertIn("github.ref == 'refs/heads/master'", workflow)
        self.assertIn("github.actor_id == '46610174'", workflow)
        self.assertIn("environment: project-automation", workflow)
        self.assertIn("      issues: read", workflow)
        self.assertIn("      contents: read", workflow)
        self.assertIn("      actions: write", workflow)
        self.assertNotIn("issues: write", workflow)
        self.assertIn("PROJECT3_ASSIGN_DISPATCH_MODE", workflow)
        self.assertIn("GITHUB_EVENT_PATH", workflow)
        self.assertNotIn("inputs.command }}", workflow)
        self.assertIn("group: project3-assignment-mutations", workflow)
        legacy = (ROOT / ".github/workflows/project3-assignment-bridge.yml").read_text()
        self.assertIn("group: project3-assignment-mutations", legacy)


if __name__ == "__main__":
    unittest.main()
