"""Offline security contracts for a ChatGPT-writable Project #3 request branch."""
from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import project3_assignment_request as relay
from test_project3_triage import Fixture, intent

SHA = "0123456789abcdef0123456789abcdef01234567"
RUN_ID = 50042


def valid_command(*, operation="apply"):
    row = intent(815)
    row["expected_before"] = {"Agent": None, "Status": "Inbox", "Execution": None}
    return json.dumps({"version": 1, "operation": operation, "items": [row]})


def run_record():
    return {"id": RUN_ID, "event": "push", "status": "completed",
            "conclusion": "success", "head_branch": relay.BRANCH,
            "head_repository": {"full_name": relay.REPO},
            "actor": {"id": relay.dispatch.OWNER_ID, "login": "Fooftilly"},
            "run_attempt": 1, "path": relay.SIGNAL + "@refs/heads/" + relay.BRANCH,
            "head_sha": SHA}


def event():
    return {"action": "completed", "repository": {"full_name": relay.REPO},
            "workflow_run": run_record()}


def environment():
    return {"GITHUB_EVENT_NAME": "workflow_run",
            "GITHUB_REPOSITORY": relay.REPO,
            "GITHUB_REF": "refs/heads/master", "GITHUB_RUN_ATTEMPT": "1"}


class RelayREST:
    def __init__(self, command=None):
        self.command = command or valid_command()
        self.upstream = run_record()
        self.branch = {"commit": {"sha": SHA}}
        self.commit = {"sha": SHA, "parents": [{"sha": "a" * 40}],
                       "files": [{"filename": relay.FILE, "status": "modified"}]}
        payload = self.command.encode()
        self.source = {"type": "file", "size": len(payload), "encoding": "base64",
                       "content": base64.b64encode(payload).decode()}
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        data = {
            relay.API + "/actions/runs/" + str(RUN_ID): self.upstream,
            relay.API + "/branches/" + relay.BRANCH: self.branch,
            relay.API + "/commits/" + SHA: self.commit,
            relay.API + "/contents/" + relay.FILE + "?ref=" + SHA: self.source,
        }
        if url not in data:
            raise AssertionError("unexpected REST path: " + url)
        return copy.deepcopy(data[url])


class RequestRelayTests(unittest.TestCase):
    def setUp(self):
        self.rest = RelayREST()
        self.event = event()
        self.env = environment()

    def load(self):
        return relay.load_command(self.event, self.env, self.rest)

    def test_owner_pinned_single_file_request_passes(self):
        command, sha = self.load()
        self.assertEqual((command, sha), (self.rest.command, SHA))
        self.assertEqual(len(self.rest.calls), 4)

    def test_untrusted_event_rejected_before_api_access(self):
        cases = [
            ({"GITHUB_EVENT_NAME": "push"}, {}),
            ({"GITHUB_REF": "refs/heads/" + relay.BRANCH}, {}),
            ({"GITHUB_RUN_ATTEMPT": "2"}, {}),
            ({}, {"action": "requested"}),
            ({}, {"repository": {"full_name": "other/repo"}}),
            ({}, {"workflow_run": {**run_record(), "event": "workflow_dispatch"}}),
            ({}, {"workflow_run": {**run_record(), "conclusion": "cancelled"}}),
            ({}, {"workflow_run": {**run_record(), "actor": {"id": 1, "login": "Fooftilly"}}}),
            ({}, {"workflow_run": {**run_record(), "actor": {"id": relay.dispatch.OWNER_ID,
                                                           "login": "other"}}}),
            ({}, {"workflow_run": {**run_record(), "head_branch": "master"}}),
            ({}, {"workflow_run": {**run_record(), "head_repository":
                                    {"full_name": "attacker/PRKS"}}}),
            ({}, {"workflow_run": {**run_record(), "path": ".github/workflows/other.yml"}}),
            ({}, {"workflow_run": {**run_record(), "run_attempt": 2}}),
        ]
        for env_changes, event_changes in cases:
            with self.subTest(env_changes=env_changes, event_changes=event_changes):
                rest = RelayREST()
                with self.assertRaises(relay.RequestError):
                    relay.load_command({**event(), **event_changes},
                                       {**environment(), **env_changes}, rest)
                self.assertEqual(rest.calls, [])

    def test_workflow_rest_record_cannot_differ_from_event(self):
        self.rest.upstream["head_sha"] = "a" * 40
        with self.assertRaisesRegex(relay.RequestError, "differs"):
            self.load()

    def test_old_request_refuses_even_if_its_signal_succeeded(self):
        self.rest.branch["commit"]["sha"] = "f" * 40
        with self.assertRaisesRegex(relay.RequestError, "no longer"):
            self.load()

    def test_only_single_existing_request_file_may_change(self):
        cases = [
            {"sha": SHA, "parents": [{"sha": "a" * 40}, {"sha": "b" * 40}],
             "files": [{"filename": relay.FILE, "status": "modified"}]},
            {"sha": SHA, "parents": [{"sha": "a" * 40}],
             "files": [{"filename": relay.FILE, "status": "added"}]},
            {"sha": SHA, "parents": [{"sha": "a" * 40}],
             "files": [{"filename": ".github/workflows/evil.yml", "status": "modified"}]},
            {"sha": SHA, "parents": [{"sha": "a" * 40}],
             "files": [{"filename": relay.FILE, "status": "modified"},
                       {"filename": "README.md", "status": "modified"}]},
        ]
        for changed in cases:
            with self.subTest(changed=changed):
                self.rest.commit = changed
                with self.assertRaisesRegex(relay.RequestError, "only"):
                    self.load()

    def test_missing_oversized_invalid_or_non_utf8_file_refused(self):
        cases = [
            {"size": relay.MAX_BYTES + 1},
            {"type": "dir"},
            {"encoding": "utf-8"},
            {"content": "!invalidbase64!"},
            {"content": base64.b64encode(b"\xff").decode(), "size": 1},
            {"size": 999},
        ]
        for patch in cases:
            with self.subTest(patch=patch):
                rest = RelayREST()
                rest.source.update(patch)
                with self.assertRaises(relay.RequestError):
                    relay.load_command(self.event, self.env, rest)

    def test_dispatch_parser_is_shared_and_stale_before_values_refused(self):
        fx = Fixture()
        command, _ = self.load()
        outcome, changed, n = relay.dispatch.execute(
            command, project=fx.project(apply=True), read=fx.read, mode="apply")
        self.assertEqual((changed, n), (3, 1))
        self.assertIn("APPLIED", outcome)
        with self.assertRaisesRegex(relay.dispatch.DispatchError, "expected_before"):
            relay.dispatch.execute(
                command, project=fx.project(apply=True), read=fx.read, mode="apply")

    def test_read_only_check_and_disabled_apply_do_not_mutate(self):
        fx = Fixture()
        for operation, mode in (("check", "apply"), ("apply", "")):
            command = valid_command(operation=operation)
            outcome, changed, n = relay.dispatch.execute(
                command, project=fx.project(apply=False), read=fx.read, mode=mode)
            self.assertEqual((changed, n), (0, 1))
        self.assertFalse(fx.api.mutations)

    def test_candidate_and_native_blocker_guards_preserved(self):
        fx = Fixture()
        fx.dependencies[815] = [{"number": 901, "state": "open"}]
        with self.assertRaisesRegex(Exception, "open native blockers"):
            relay.dispatch.execute(valid_command(),
                                   project=fx.project(apply=True), read=fx.read, mode="apply")
        self.assertFalse(fx.api.mutations)


class WorkflowContractTests(unittest.TestCase):
    def test_signal_is_unprivileged_and_does_not_read_json(self):
        text = (ROOT / ".github/workflows/project3-assignment-request-signal.yml").read_text()
        self.assertIn("  push:\n    branches: [project3-assignment-requests]", text)
        self.assertIn("      - project3-assignment-request.json", text)
        self.assertIn("\npermissions: {}\n", text)
        for forbidden in ("actions/checkout", "secrets.", "GITHUB_TOKEN:", "github.token",
                          "upload-artifact", "curl ", "python3 "):
            self.assertNotIn(forbidden, text)

    def test_master_processor_never_checks_out_untrusted_code(self):
        text = (ROOT / ".github/workflows/project3-assignment-request-processor.yml").read_text()
        self.assertIn("    workflows: [Project 3 assignment request signal]", text)
        self.assertIn("    branches: [project3-assignment-requests]", text)
        self.assertIn("github.ref == 'refs/heads/master'", text)
        self.assertIn("github.event.workflow_run.actor.id == 46610174", text)
        self.assertIn("environment: project-automation", text)
        self.assertIn("PROJECT_SYNC_TOKEN", text)
        self.assertIn("PROJECT3_ASSIGN_DISPATCH_MODE", text)
        self.assertIn("persist-credentials: false", text)
        self.assertNotIn("github.event.workflow_run.head_sha }}", text)
        self.assertIn("group: project3-assignment-mutations", text)
        self.assertIn("if: always() && (steps.assignment.outputs.changed == 'true' || steps.assignment.outcome == 'failure')", text)


if __name__ == "__main__":
    unittest.main()
