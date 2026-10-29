"""Offline security and behavior tests for issue-comment Project #3 bridge."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import project3_command_bridge as bridge
from test_project3_triage import Fixture, intent


NOW = datetime(2026, 10, 29, 17, 35, tzinfo=timezone.utc)


def payload(*items, operation="apply"):
    return bridge.PREFIX + json.dumps({"version": 1, "operation": operation, "items": items})


class GitHubStub:
    def __init__(self, body: str):
        timestamp = NOW.isoformat().replace("+00:00", "Z")
        self.comment_data = {
            "id": 53, "url": bridge.API + "/issues/comments/53",
            "issue_url": bridge.API + "/issues/982", "body": body,
            "user": {"id": bridge.OWNER_ID, "login": bridge.OWNER_LOGIN},
            "performed_via_github_app": {"id": bridge.CONNECTOR_APP_ID},
            "created_at": timestamp, "updated_at": timestamp,
        }
        self.controller = {"number": bridge.INBOX, "state": "open"}
        self.claimed = False
        self.receipts: list[str] = []

    def comment(self, cid):
        if cid != 53:
            raise AssertionError("unexpected comment ID")
        return copy.deepcopy(self.comment_data)

    def issue(self):
        return copy.deepcopy(self.controller)

    def has_claim(self, cid):
        return self.claimed

    def claim(self, cid, run_url):
        if self.claimed:
            raise AssertionError("duplicate claim")
        self.claimed = True
        return 5001

    def receipt(self, claim_id, comment_id, result):
        self.receipts.append(result)


def event_for(api):
    return {"action": "created", "repository": {"full_name": bridge.REPO},
            "issue": {"number": bridge.INBOX},
            "comment": {"id": 53, "body": api.comment_data["body"]}}


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.api = GitHubStub(payload(intent()))
        self.transport = mock.patch.object(bridge.pm, "http_transport", return_value=self.fx.api)
        self.issue_reader = mock.patch.object(bridge.triage, "rest_client", return_value=self.fx.read)
        self.transport.start()
        self.issue_reader.start()
        self.addCleanup(self.transport.stop)
        self.addCleanup(self.issue_reader.stop)

    def go(self, *, api=None, ev=None, mode="apply", token="test-only-token"):
        api = api or self.api
        return bridge.execute(ev or event_for(api), github=api, project_token=token,
                              mode=mode, now=NOW, run_url="https://github.com/Fooftilly/PRKS/actions/runs/123")

    def test_allowed_user_connector_applies_assignment(self):
        message = self.go()
        self.assertIn("APPLIED", message)
        self.assertEqual(len(self.fx.api.mutations), 3)
        self.assertEqual(self.fx.api.items["I815"]["values"]["Agent"], "Cursor Cloud")
        self.assertIn("APPLIED", self.api.receipts[0])

    def test_default_mode_makes_no_change(self):
        message = self.go(mode="")
        self.assertIn("DRY RUN", message)
        self.assertFalse(self.fx.api.mutations)
        self.assertTrue(self.api.claimed)

    def test_explicit_check_is_read_only(self):
        self.api = GitHubStub(payload(intent(), operation="check"))
        self.assertIn("CHECK ONLY", self.go())
        self.assertFalse(self.fx.api.mutations)

    def test_replay_refused_even_if_original_succeeded(self):
        self.go()
        count = len(self.fx.api.mutations)
        with self.assertRaisesRegex(bridge.BridgeError, "prior claim"):
            self.go()
        self.assertEqual(len(self.fx.api.mutations), count)

    def test_other_app_with_same_user_is_refused(self):
        self.api.comment_data["performed_via_github_app"]["id"] = 12
        with self.assertRaisesRegex(bridge.BridgeError, "not authorized"):
            self.go()
        self.assertFalse(self.api.claimed)

    def test_personal_web_comment_is_not_connector_command(self):
        self.api.comment_data["performed_via_github_app"] = None
        with self.assertRaisesRegex(bridge.BridgeError, "not authorized"):
            self.go()

    def test_other_user_is_refused_even_via_connector(self):
        self.api.comment_data["user"]["id"] = 101
        with self.assertRaisesRegex(bridge.BridgeError, "not authorized"):
            self.go()

    def test_injected_event_body_mismatch_is_refused(self):
        ev = event_for(self.api)
        ev["comment"]["body"] = "PRKS_PROJECT3_ASSIGN_V1\n{}"
        with self.assertRaisesRegex(bridge.BridgeError, "differs"):
            self.go(ev=ev)

    def test_wrong_issue_or_repo_refused(self):
        ev = event_for(self.api)
        ev["issue"]["number"] = 441
        with self.assertRaisesRegex(bridge.BridgeError, "not a new comment"):
            self.go(ev=ev)
        ev = event_for(self.api)
        ev["repository"]["full_name"] = "Other/Repo"
        with self.assertRaisesRegex(bridge.BridgeError, "not a new comment"):
            self.go(ev=ev)

    def test_edited_or_old_comment_refused(self):
        self.api.comment_data["updated_at"] = (NOW + timedelta(seconds=3)).isoformat()
        with self.assertRaisesRegex(bridge.BridgeError, "edited"):
            self.go()
        self.api.comment_data["updated_at"] = self.api.comment_data["created_at"]
        self.api.comment_data["created_at"] = (NOW - timedelta(hours=3)).isoformat()
        self.api.comment_data["updated_at"] = self.api.comment_data["created_at"]
        with self.assertRaisesRegex(bridge.BridgeError, "too old"):
            self.go()

    def test_closed_inbox_refused(self):
        self.api.controller["state"] = "closed"
        with self.assertRaisesRegex(bridge.BridgeError, "not open"):
            self.go()

    def test_bad_command_marker_and_extra_keys_refused(self):
        with self.assertRaisesRegex(bridge.BridgeError, "marker"):
            bridge.parse("do evil")
        with self.assertRaisesRegex(bridge.BridgeError, "version"):
            bridge.parse(bridge.PREFIX + json.dumps({"version": 1, "operation": "apply",
                                                      "items": [intent()], "admin": True}))
        with self.assertRaisesRegex(bridge.BridgeError, "unexpected"):
            bridge.parse(payload({**intent(), "code": "rm -rf /"}))

    def test_duplicate_issue_command_refused(self):
        with self.assertRaisesRegex(bridge.BridgeError, "duplicate issue"):
            bridge.parse(payload(intent(), intent()))

    def test_bulk_unaccepted_candidate_cannot_be_promoted(self):
        self.api = GitHubStub(payload(intent(), intent(509)))
        with self.assertRaisesRegex(Exception, "candidate is not accepted"):
            self.go()
        self.assertFalse(self.api.claimed)
        self.assertFalse(self.fx.api.mutations)

    def test_blocked_by_native_dependency_is_refused(self):
        self.fx.dependencies[815] = [{"number": 900, "state": "open"}]
        with self.assertRaisesRegex(Exception, "open native blockers"):
            self.go()
        self.assertFalse(self.fx.api.mutations)

    def test_reassignment_requires_explicit_flag(self):
        self.fx.api.items["I815"]["values"]["Agent"] = "Claude Code"
        with self.assertRaisesRegex(bridge.BridgeError, "allow_reassignment"):
            self.go()
        self.assertFalse(self.fx.api.mutations)
        row = {**intent(), "allow_reassignment": True}
        self.api = GitHubStub(payload(row))
        self.assertIn("APPLIED", self.go())
        self.assertEqual(self.fx.api.items["I815"]["values"]["Agent"], "Cursor Cloud")

    def test_missing_project_token_never_claims(self):
        with self.assertRaisesRegex(bridge.BridgeError, "not configured"):
            self.go(token="")
        self.assertFalse(self.api.claimed)

    def test_partial_write_failure_keeps_replay_claim(self):
        def new_blocker(name, variables):
            if not self.fx.api.mutations:
                self.fx.dependencies[815] = [{"number": 900, "state": "open"}]
        self.fx.api.before_mutation = new_blocker
        with self.assertRaisesRegex(Exception, "issue changed during write"):
            self.go()
        self.assertTrue(self.api.claimed)
        self.assertEqual(len(self.fx.api.mutations), 1)
        self.assertIn("POSSIBLY PARTIAL", self.api.receipts[-1])
        with self.assertRaisesRegex(bridge.BridgeError, "prior claim"):
            self.go()

    def test_cannot_authorize_without_roadmap_attestation(self):
        row = intent()
        row["roadmap_checked"] = False
        self.api = GitHubStub(payload(row))
        with self.assertRaisesRegex(bridge.BridgeError, "must be attested"):
            self.go()

    def test_claim_failure_prevents_any_mutation(self):
        def error(cid, run_url):
            raise bridge.BridgeError("claim could not be created")
        self.api.claim = error
        with self.assertRaisesRegex(bridge.BridgeError, "claim could not"):
            self.go()
        self.assertFalse(self.fx.api.mutations)


if __name__ == "__main__":
    unittest.main()
