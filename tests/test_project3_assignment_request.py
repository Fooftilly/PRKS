"""Offline security contracts for a ChatGPT-writable Project #3 request branch."""
from __future__ import annotations

import base64
import copy
import json
import os
import tempfile
from unittest import mock
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
        self.commit = {"sha": SHA, "author": {"id": relay.dispatch.OWNER_ID},
                       "committer": {"id": relay.dispatch.OWNER_ID},
                       "parents": [{"sha": "a" * 40}],
                       "files": [{"filename": relay.FILE, "status": "modified"}]}
        payload = self.command.encode()
        self.source = {"type": "file", "size": len(payload), "encoding": "base64",
                       "content": base64.b64encode(payload).decode()}
        self.rulesets = [{"id": 1234}]
        self.ruleset = {"id": 1234, "target": "branch", "enforcement": "active",
                        "updated_at": "2026-10-30T15:00:00Z",
                        "conditions": {"ref_name": {"include": [relay.REQUEST_REF], "exclude": []}},
                        "bypass_actors": [],
                        "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}]}
        self.statuses = []
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        data = {
            relay.API + "/actions/runs/" + str(RUN_ID): self.upstream,
            relay.API + "/branches/" + relay.BRANCH: self.branch,
            relay.API + "/commits/" + SHA: self.commit,
            relay.API + "/contents/" + relay.FILE + "?ref=" + SHA: self.source,
            relay.API + "/rulesets?per_page=100&page=1": self.rulesets,
            relay.API + "/rulesets/1234": self.ruleset,
            relay.API + "/commits/" + SHA + "/statuses?per_page=100&page=1": self.statuses,
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

    def test_provenance_reader_is_separate_from_issue_rest_reader(self):
        with mock.patch.object(relay.pm, "_https_only_opener") as opener:
            client = relay.provenance_client("test-token")
            with self.assertRaisesRegex(relay.RequestError, "outside fixed"):
                client(relay.API + "/issues/815")
            with self.assertRaisesRegex(relay.RequestError, "outside fixed"):
                client("https://evil.example/anything")
            for url in (
                relay.API + "/actions/runs/1/logs",
                relay.API + "/actions/runs/01",
                relay.API + "/branches/" + relay.BRANCH + "-shadow",
                relay.API + "/commits/" + "a" * 40 + "/status",
                relay.API + "/commits/" + "not-a-sha",
                relay.API + "/contents/" + relay.FILE + "?ref=" + "a" * 40 + "&x=1",
                relay.API + "/contents/" + relay.FILE + "?ref=master",
            ):
                with self.subTest(url=url), self.assertRaisesRegex(
                        relay.RequestError, "outside fixed"):
                    client(url)
            opener.assert_called_once()

    def test_main_uses_provenance_and_issue_clients_with_distinct_scopes(self):
        fx = Fixture()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "event.json"
            path.write_text(json.dumps(self.event), encoding="utf-8")
            env = {**self.env, "GITHUB_EVENT_PATH": str(path),
                   "GITHUB_TOKEN": "actions-token",
                   "PROJECT_SYNC_TOKEN": "project-token",
                   "PROJECT3_ASSIGN_DISPATCH_MODE": "apply",
                   "GITHUB_RUN_ID": "9007",
                   "PROJECT3_REQUEST_RULESET_ID": "1234",
                   "PROJECT3_REQUEST_RULESET_UPDATED_AT": "2026-10-30T15:00:00Z"}
            with (mock.patch.dict(os.environ, env, clear=True),
                  mock.patch.object(relay, "provenance_client",
                                    return_value=self.rest) as provenance,
                  mock.patch.object(relay.triage, "rest_client",
                                    return_value=fx.read) as issue_client,
                  mock.patch.object(relay.pm, "http_transport",
                                    return_value=fx.api) as project_transport,
                  mock.patch.object(relay, "create_status") as claim_writer,
                  mock.patch.object(relay.dispatch, "report") as audit):
                self.assertEqual(relay.main(), 0)
            provenance.assert_called_once_with("actions-token")
            issue_client.assert_called_once_with("actions-token")
            project_transport.assert_called_once_with("project-token")
            self.assertEqual(len(fx.api.mutations), 3)
            self.assertEqual(claim_writer.call_count, 2)
            self.assertEqual(claim_writer.call_args_list[0].args,
                             ("actions-token", SHA, "pending", "9007"))
            self.assertEqual(claim_writer.call_args_list[1].args,
                             ("actions-token", SHA, "success", "9007"))
            self.assertIn("APPLIED", audit.call_args.args[0])

    def test_failed_apply_writes_failure_audit_and_keeps_claim(self):
        fx = Fixture()
        fx.dependencies[815] = [{"number": 999, "state": "open"}]
        with tempfile.TemporaryDirectory() as temp:
            event_file = Path(temp) / "event.json"
            event_file.write_text(json.dumps(self.event), encoding="utf-8")
            env = {**self.env, "GITHUB_EVENT_PATH": str(event_file),
                   "GITHUB_TOKEN": "actions-token", "PROJECT_SYNC_TOKEN": "project-token",
                   "GITHUB_RUN_ID": "9007", "PROJECT3_ASSIGN_DISPATCH_MODE": "apply",
                   "PROJECT3_REQUEST_RULESET_ID": "1234",
                   "PROJECT3_REQUEST_RULESET_UPDATED_AT": "2026-10-30T15:00:00Z"}
            with (mock.patch.dict(os.environ, env, clear=True),
                  mock.patch.object(relay, "provenance_client", return_value=self.rest),
                  mock.patch.object(relay.triage, "rest_client", return_value=fx.read),
                  mock.patch.object(relay.pm, "http_transport", return_value=fx.api),
                  mock.patch.object(relay, "create_status") as audit,
                  mock.patch.object(relay.dispatch, "report")):
                self.assertEqual(relay.main(), 1)
            self.assertEqual([call.args[2] for call in audit.call_args_list],
                             ["pending", "failure"])
            self.assertEqual(fx.api.mutations, [])

    def test_request_tip_change_before_claim_blocks_project_write(self):
        rest = RelayREST()
        original = rest.__call__
        def changing_tip(url):
            result = original(url)
            if url == relay.API + "/branches/" + relay.BRANCH and rest.calls.count(url) == 2:
                return {"commit": {"sha": "f" * 40}}
            return result
        fx = Fixture()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "event.json"
            path.write_text(json.dumps(self.event), encoding="utf-8")
            env = {**self.env, "GITHUB_EVENT_PATH": str(path),
                   "GITHUB_TOKEN": "actions-token",
                   "PROJECT_SYNC_TOKEN": "project-token", "GITHUB_RUN_ID": "9007",
                   "PROJECT3_REQUEST_RULESET_ID": "1234",
                   "PROJECT3_REQUEST_RULESET_UPDATED_AT": "2026-10-30T15:00:00Z"}
            with (mock.patch.dict(os.environ, env, clear=True),
                  mock.patch.object(relay, "provenance_client", return_value=changing_tip),
                  mock.patch.object(relay.triage, "rest_client", return_value=fx.read),
                  mock.patch.object(relay.pm, "http_transport", return_value=fx.api),
                  mock.patch.object(relay, "create_status") as writer,
                  mock.patch.object(relay.dispatch, "report")):
                self.assertEqual(relay.main(), 1)
            writer.assert_not_called()
            self.assertEqual(fx.api.mutations, [])

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

    def test_author_and_committer_must_be_owner(self):
        for key in ("author", "committer"):
            with self.subTest(key=key):
                rest = RelayREST()
                rest.commit[key]["id"] = 777
                with self.assertRaisesRegex(relay.RequestError, "author/committer"):
                    relay.load_command(self.event, self.env, rest)

    def test_protected_branch_rules_are_required_and_revision_pinned(self):
        relay.require_branch_rules(self.rest, "1234", "2026-10-30T15:00:00Z")
        for changes in (
            {"enforcement": "disabled"},
            {"id": 1235},
            {"updated_at": "2026-10-30T15:00:01Z"},
            {"bypass_actors": [{"actor_id": 1, "actor_type": "RepositoryRole"}]},
            {"conditions": {"ref_name": {"include": ["refs/heads/master"], "exclude": []}}},
            {"conditions": {"ref_name": {"include": [relay.REQUEST_REF], "exclude": [relay.REQUEST_REF]}}},
            {"rules": [{"type": "deletion"}]},
            {"rules": [{"type": "non_fast_forward"}]},
        ):
            with self.subTest(changes=changes):
                rest = RelayREST()
                rest.ruleset.update(changes)
                with self.assertRaises(relay.RequestError):
                    relay.require_branch_rules(rest, "1234", "2026-10-30T15:00:00Z")

    def test_missing_ruleset_binding_refuses_before_rest_read(self):
        for name, stamp in (
            ("", "2026-10-30T15:00:00Z"),
            ("1234", ""),
            ("0", "2026-10-30T15:00:00Z"),
            ("1234/admin", "2026-10-30T15:00:00Z"),
        ):
            with self.subTest(id=name, stamp=stamp):
                rest = RelayREST()
                with self.assertRaisesRegex(relay.RequestError, "configuration missing"):
                    relay.require_branch_rules(rest, name, stamp)
                self.assertEqual(rest.calls, [])

    def test_metadata_limited_bypass_visibility_uses_pinned_revision(self):
        # GitHub omits bypass_actors from metadata-only ruleset responses.
        # Owner attests empty bypasses when recording the ruleset revision.
        rest = RelayREST()
        rest.ruleset.pop("bypass_actors")
        relay.require_branch_rules(rest, "1234", "2026-10-30T15:00:00Z")
        rest.ruleset["updated_at"] = "2026-10-30T15:01:00Z"
        with self.assertRaisesRegex(relay.RequestError, "drifted"):
            relay.require_branch_rules(rest, "1234", "2026-10-30T15:00:00Z")

    def test_provenance_url_policy_restricts_rulesets_and_statuses(self):
        with mock.patch.object(relay.pm, "_https_only_opener"):
            client = relay.provenance_client("test-token")
            for url in (
                relay.API + "/rulesets/1234/anything",
                relay.API + "/rulesets?per_page=100&page=0",
                relay.API + "/commits/" + SHA + "/statuses?per_page=100&page=1&extra=1",
                relay.API + "/commits/" + SHA + "/statuses?per_page=101&page=1",
            ):
                with self.subTest(url=url), self.assertRaises(relay.RequestError):
                    client(url)

    def test_status_claim_blocks_reuse_even_after_state_resets(self):
        relay._status_history(self.rest, SHA)
        self.rest.statuses = [
            {"context": relay.STATUS_CONTEXT, "state": "success"},
            {"context": "other-check", "state": "success"},
        ]
        with self.assertRaisesRegex(relay.RequestError, "already claimed"):
            relay._status_history(self.rest, SHA)
        self.rest.statuses = [{"context": relay.STATUS_CONTEXT, "state": "pending"}]
        with self.assertRaisesRegex(relay.RequestError, "already claimed"):
            relay._status_history(self.rest, SHA)

    def test_status_list_must_be_complete_and_well_formed(self):
        self.rest.statuses = [{"context": "anything"}] * 100
        with self.assertRaises(AssertionError):
            relay._status_history(self.rest, SHA)  # attempts page two
        self.rest.statuses = [{"context": "anything"}] * 101
        with self.assertRaisesRegex(relay.RequestError, "malformed commit status"):
            relay._status_history(self.rest, SHA)
        self.rest.statuses = [{"context": "other"}, "unexpected"]
        with self.assertRaisesRegex(relay.RequestError, "malformed commit status"):
            relay._status_history(self.rest, SHA)

    def test_claim_post_must_be_verified_before_mutation(self):
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *unused):
                return False
            def read(self):
                return b'{"context":"wrong","state":"pending"}'
        with mock.patch.object(relay.pm, "_https_only_opener") as opener:
            opener.return_value.open.return_value = Response()
            with self.assertRaisesRegex(relay.RequestError, "not verified"):
                relay.create_status("token", SHA, "pending", "123")
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(request.full_url, relay.API + "/statuses/" + SHA)
        with self.assertRaisesRegex(relay.RequestError, "invalid claim identity"):
            relay.create_status("token", SHA, "pending", "")
        with self.assertRaisesRegex(relay.RequestError, "unsupported claim state"):
            relay.create_status("token", SHA, "invalid", "123")

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
                self.rest.commit = {**changed, "author": {"id": relay.dispatch.OWNER_ID},
                                    "committer": {"id": relay.dispatch.OWNER_ID}}
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
        self.assertIn("statuses: write", text)
        self.assertIn("GITHUB_RUN_ID", text)
        self.assertIn("PROJECT3_REQUEST_RULESET_ID", text)
        self.assertIn("PROJECT3_REQUEST_RULESET_UPDATED_AT", text)
        self.assertIn("PROJECT3_ASSIGN_DISPATCH_MODE", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("ref: master", text)
        self.assertIn("zizmor: ignore[dangerous-triggers]", text)
        # The trigger exception is allowed only with a fixed protected master
        # checkout and no untrusted-ref execution or upstream artifact downloads.
        for forbidden in ("run: ${{", "download-artifact", "github.event.workflow_run.head_sha }}"):
            self.assertNotIn(forbidden, text)
        self.assertNotIn("github.event.workflow_run.head_sha }}", text)
        self.assertIn("group: project3-assignment-mutations", text)
        self.assertIn("if: always() && (steps.assignment.outputs.changed == 'true' || steps.assignment.outcome == 'failure')", text)


if __name__ == "__main__":
    unittest.main()
