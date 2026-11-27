"""Offline contracts for reviewed Project #3 Implementation Order updates (#1478)."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import project3_assignment_dispatch as dispatch
import project3_assignment_request as relay
import project3_readiness as pr
import project3_triage as t
from test_project3_assignment_request import RelayREST, SHA, environment, event, fake_token
from test_project3_triage import Fixture

ORDER = t.ORDER_FIELD


def rank(number: int = 815, order: Any = 3, **overrides: Any) -> dict[str, Any]:
    row = {"number": number, "order": order,
           "reason": "Ranked after reviewing roadmap order and open pull requests.",
           "roadmap_checked": True, "overlap_checked": True}
    row.update(overrides)
    return row


def order_command(*rows, operation="check"):
    return json.dumps({"version": 1, "kind": dispatch.ORDER_KIND,
                       "operation": operation, "items": list(rows)})


def request_row(number: int = 815, order: Any = 3, before: Any = None, **overrides: Any) -> dict[str, Any]:
    return {**rank(number, order), "expected_before": before, **overrides}


class OrderFixture(Fixture):
    def __init__(self):
        super().__init__()
        fields: dict[str, Any] = self.api.fields
        fields[ORDER] = "NUMBER"
        fields["Roadmap Order"] = "NUMBER"
        self.api.items["I815"]["values"]["Roadmap Order"] = 7.0

    def order_plan(self, *rows):
        rows = [copy.deepcopy(row) for row in rows]
        return t.propose_order(t.load_order_proposals({"operation": "order", "items": rows}),
                               self.project(), self.read)


class OrderValidationTests(unittest.TestCase):
    def test_order_must_be_bounded_integer(self):
        for value in (True, False, 0, -1, t.ORDER_MAX + 1, 1.0, 2.5, float("nan"),
                      float("inf"), "3", None, [3]):
            with self.subTest(value=value), self.assertRaises(t.TriageError):
                t.load_order_proposals({"operation": "order", "items": [rank(order=value)]})
        t.load_order_proposals({"operation": "order", "items": [rank(order=t.ORDER_MIN)]})
        t.load_order_proposals({"operation": "order", "items": [rank(order=t.ORDER_MAX)]})

    def test_unknown_fields_duplicates_and_missing_attestation_refused(self):
        bad = (
            {"operation": "order", "items": [{**rank(), "set": {"Status": "Ready"}}]},
            {"operation": "order", "items": [rank(), rank()]},
            {"operation": "order", "items": [rank(815, 3), rank(509, 3)]},
            {"operation": "order", "items": [rank(overlap_checked=False)]},
            {"operation": "order", "items": [rank(reason="short")]},
            {"operation": "order", "items": []},
            {"operation": "order", "items": [rank(n) for n in range(1, t.MAX_ITEMS + 2)]},
            {"operation": "assign", "items": [rank()]},
        )
        for data in bad:
            with self.subTest(data=str(data)[:70]), self.assertRaises(t.TriageError):
                t.load_order_proposals(data)

    def test_plan_requires_bound_field_identity_and_nullable_before(self):
        fx = OrderFixture()
        plan = fx.order_plan(rank())
        self.assertEqual(plan["field"], {"name": ORDER, "id": "F_Implementation_Order"})
        self.assertIsNone(plan["items"][0]["expected_before"])
        raw = json.dumps(plan).encode()
        self.assertEqual(t.load_plan(raw)["schema"], t.ORDER_SCHEMA)
        for mutate in (lambda p: p["field"].update(name="Roadmap Order"),
                       lambda p: p.pop("field"),
                       lambda p: p["items"][0].update(expected_before=1.5),
                       lambda p: p["items"][0].update(expected_before=True),
                       lambda p: p["items"][0].update(approved="yes"),
                       lambda p: p.update(scope={})):
            broken = copy.deepcopy(plan)
            mutate(broken)
            with self.assertRaises(t.TriageError):
                t.load_plan(json.dumps(broken).encode())


class OrderApplyTests(unittest.TestCase):
    def setUp(self):
        self.fx = OrderFixture()

    def approved(self, *rows):
        plan = self.fx.order_plan(*rows)
        for row in plan["items"]:
            row["approved"] = True
        return plan

    def test_missing_or_wrong_type_field_refused(self):
        self.fx.api.fields[ORDER] = ["1", "2"]
        with self.assertRaisesRegex(t.TriageError, "no NUMBER field"):
            self.fx.order_plan(rank())
        del self.fx.api.fields[ORDER]
        with self.assertRaisesRegex(t.TriageError, "no NUMBER field"):
            self.fx.order_plan(rank())

    def test_apply_changes_only_implementation_order(self):
        before = copy.deepcopy(self.fx.api.items)
        plan = self.approved(rank(815, 2), rank(509, 1))
        self.assertEqual(t.apply_order(plan, self.fx.project(apply=True), self.fx.read), 2)
        for key in ("I815", "I509"):
            values = dict(self.fx.api.items[key]["values"])
            values.pop(ORDER)
            self.assertEqual(values, before[key]["values"])
        self.assertEqual(self.fx.api.items["I815"]["values"][ORDER], 2)
        self.assertEqual({name for name, _ in self.fx.api.mutations}, {"updateProjectV2ItemFieldValue"})
        self.assertTrue(all(set(v) == {"project", "item", "field", "number"}
                            for _, v in self.fx.api.mutations))

    def test_candidate_inbox_and_blocked_may_be_ranked_but_not_promoted(self):
        self.fx.api.items["I635"]["values"]["Status"] = "Blocked"
        self.fx.dependencies[509] = [{"number": 4, "state": "open"}]
        plan = self.approved(rank(509, 1), rank(635, 2), rank(815, 3))
        self.assertEqual(t.apply_order(plan, self.fx.project(apply=True), self.fx.read), 3)
        self.assertEqual(self.fx.api.items["I509"]["values"]["Status"], "Inbox")
        self.assertEqual(self.fx.api.items["I635"]["values"]["Status"], "Blocked")
        self.assertNotIn("Agent", self.fx.api.items["I509"]["values"])
        self.assertNotIn("Execution", self.fx.api.items["I815"]["values"])

    def test_closed_archived_and_non_prks_items_refused(self):
        self.fx.api.items["I815"]["archived"] = True
        with self.assertRaisesRegex(t.TriageError, "archived or closed"):
            self.fx.order_plan(rank())
        self.fx.api.items["I815"]["archived"] = False
        self.fx.api.content[815]["state"] = "CLOSED"
        with self.assertRaisesRegex(t.TriageError, "archived or closed"):
            self.fx.order_plan(rank())
        self.fx.api.content[815]["state"] = "OPEN"
        self.fx.api.content[815]["repo"] = "other/repo"
        with self.assertRaisesRegex(t.TriageError, "not on Project #3"):
            self.fx.order_plan(rank())

    def test_unknown_dependency_state_fails_closed(self):
        self.fx.dependencies[815] = [{"number": 4, "state": "unknown"}]
        with self.assertRaisesRegex(t.TriageError, "unknown dependency state"):
            self.fx.order_plan(rank())

    def test_stale_before_value_refused_without_writes(self):
        plan = self.approved(rank(815, 2), rank(509, 1))
        self.fx.api.items["I509"]["values"][ORDER] = 9
        with self.assertRaisesRegex(t.TriageError, "changed since review"):
            t.apply_order(plan, self.fx.project(apply=True), self.fx.read)
        self.assertEqual(self.fx.api.mutations, [])

    def test_issue_drift_and_field_identity_drift_refused(self):
        plan = self.approved(rank())
        self.fx.issues[815]["updated_at"] = "2026-10-29T18:00:00Z"
        with self.assertRaisesRegex(t.TriageError, "changed since review"):
            t.apply_order(plan, self.fx.project(apply=True), self.fx.read)
        plan = self.approved(rank())
        plan["field"]["id"] = "F_other"
        with self.assertRaisesRegex(t.TriageError, "field identity"):
            t.apply_order(plan, self.fx.project(apply=True), self.fx.read)
        self.assertEqual(self.fx.api.mutations, [])

    def test_fractional_live_value_refused(self):
        self.fx.api.items["I815"]["values"][ORDER] = 2.5
        with self.assertRaisesRegex(t.TriageError, "not an integer"):
            self.fx.order_plan(rank())
        self.fx.api.items["I815"]["values"][ORDER] = 4.0
        self.assertEqual(self.fx.order_plan(rank())["items"][0]["expected_before"], 4)

    def test_held_and_no_op_rows_are_not_written(self):
        self.fx.api.items["I509"]["values"][ORDER] = 1
        plan = self.fx.order_plan(rank(815, 2), rank(509, 1))
        plan["items"][1]["approved"] = True
        self.assertEqual(t.apply_order(plan, self.fx.project(apply=True), self.fx.read), 0)
        self.assertEqual(self.fx.api.mutations, [])

    def test_concurrent_edit_before_second_write_stops_batch(self):
        plan = self.approved(rank(815, 2), rank(509, 1))

        def disturb(name, variables):
            if not self.fx.api.mutations:
                self.fx.api.items["I509"]["values"][ORDER] = 8

        self.fx.api.before_mutation = disturb
        with self.assertRaisesRegex(t.TriageError, "stopped after 1 writes"):
            t.apply_order(plan, self.fx.project(apply=True), self.fx.read)
        self.assertEqual(len(self.fx.api.mutations), 1)
        self.assertEqual(self.fx.api.items["I509"]["values"][ORDER], 8)

    def test_unrelated_field_change_fails_post_write_verification(self):
        plan = self.approved(rank())

        def disturb(name, variables):
            self.fx.api.items["I815"]["values"]["Status"] = "Backlog"

        self.fx.api.before_mutation = disturb
        with self.assertRaisesRegex(t.TriageError, "post-write verification failed"):
            t.apply_order(plan, self.fx.project(apply=True), self.fx.read)

    def test_failed_mutation_reports_possible_partial_and_keeps_earlier_write(self):
        plan = self.approved(rank(815, 2), rank(509, 1))

        def fail_second(name, variables):
            if self.fx.api.mutations:
                raise t.pm.MigrationError("GraphQL error: simulated outage")

        self.fx.api.before_mutation = fail_second
        with self.assertRaisesRegex(t.pm.MigrationError, "simulated outage"):
            t.apply_order(plan, self.fx.project(apply=True), self.fx.read)
        self.assertEqual(self.fx.api.items["I815"]["values"][ORDER], 2)
        self.assertNotIn(ORDER, self.fx.api.items["I509"]["values"])

    def test_read_only_project_cannot_write(self):
        plan = self.approved(rank())
        with self.assertRaisesRegex(t.pm.MigrationError, "dry-run"):
            t.apply_order(plan, self.fx.project(apply=False), self.fx.read)
        self.assertEqual(self.fx.api.mutations, [])

    def test_cli_propose_check_and_sha_bound_apply(self):
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(t, "TRIAGE_DIR", Path(temp)):
            source, out = Path(temp) / "in.json", Path(temp) / "plan.json"
            source.write_text(json.dumps({"operation": "order", "items": [rank()]}))
            run = lambda *argv: t.main(list(argv), transport=self.fx.api, read=self.fx.read)
            self.assertEqual(run("propose", "--input", str(source), "--out", str(out)), 0)
            plan = json.loads(out.read_text())
            self.assertFalse(plan["items"][0]["approved"])
            plan["items"][0]["approved"] = True
            out.write_text(json.dumps(plan))
            digest = hashlib.sha256(out.read_bytes()).hexdigest()
            self.assertEqual(run("check", "--plan", str(out)), 0)
            self.assertEqual(run("apply", "--plan", str(out), "--plan-sha256", "0" * 64), 1)
            self.assertEqual(self.fx.api.mutations, [])
            self.assertEqual(run("apply", "--plan", str(out), "--plan-sha256", digest), 0)
            self.assertEqual(self.fx.api.items["I815"]["values"][ORDER], 3)


class OrderDispatchTests(unittest.TestCase):
    def setUp(self):
        self.fx = OrderFixture()

    def run_command(self, raw, env):
        allow = dispatch.mutations_allowed(raw, env)
        return dispatch.run(raw, project=self.fx.project(apply=allow), read=self.fx.read, env=env)

    def armed(self, raw):
        _, rows = dispatch.parse_order(raw)
        return {"PROJECT3_ORDER_WRITE_MODE": "apply",
                "PROJECT3_ORDER_APPROVED_SHA256": dispatch.order_digest(rows)}

    def test_assignment_commands_keep_v1_behavior(self):
        raw = json.dumps({"version": 1, "operation": "apply", "items": [
            {"number": 815, "set": {"Status": "Inbox"}, "reason": "Assignment remains on its v1 path.",
             "roadmap_checked": True, "overlap_checked": True,
             "expected_before": {"Agent": None, "Status": "Inbox", "Execution": None}}]})
        self.assertEqual(dispatch.command_kind(raw), ("assignment", "apply"))
        self.assertFalse(dispatch.mutations_allowed(raw, {"PROJECT3_ORDER_WRITE_MODE": "apply"}))
        self.assertTrue(dispatch.mutations_allowed(raw, {"PROJECT3_ASSIGN_DISPATCH_MODE": "apply"}))

    def test_assignment_write_mode_does_not_enable_order_writes(self):
        raw = order_command(request_row(), operation="apply")
        self.assertFalse(dispatch.mutations_allowed(raw, {"PROJECT3_ASSIGN_DISPATCH_MODE": "apply"}))
        outcome, changed, _ = self.run_command(raw, {"PROJECT3_ASSIGN_DISPATCH_MODE": "apply"})
        self.assertIn("DRY RUN", outcome)
        self.assertEqual((changed, self.fx.api.mutations), (0, []))

    def test_check_prints_digest_and_never_writes(self):
        raw = order_command(request_row())
        outcome, changed, issues = self.run_command(raw, self.armed(raw))
        self.assertIn("CHECK ONLY", outcome)
        _, rows = dispatch.parse_order(raw)
        self.assertIn(dispatch.order_digest(rows), outcome)
        self.assertEqual((changed, issues, self.fx.api.mutations), (0, 1, []))

    def test_check_and_apply_share_one_digest(self):
        check = dispatch.parse_order(order_command(request_row()))[1]
        apply = dispatch.parse_order(order_command(request_row(), operation="apply"))[1]
        self.assertEqual(dispatch.order_digest(check), dispatch.order_digest(apply))
        other = dispatch.parse_order(order_command(request_row(order=4)))[1]
        self.assertNotEqual(dispatch.order_digest(check), dispatch.order_digest(other))

    def test_apply_needs_mode_and_exact_approved_digest(self):
        raw = order_command(request_row(), operation="apply")
        self.assertIn("DRY RUN", self.run_command(raw, {})[0])
        for approved in ("", "0" * 64, dispatch.order_digest(
                dispatch.parse_order(order_command(request_row(order=4)))[1])):
            with self.subTest(approved=approved), \
                    self.assertRaisesRegex(dispatch.DispatchError, "maintainer-approved"):
                self.run_command(raw, {"PROJECT3_ORDER_WRITE_MODE": "apply",
                                       "PROJECT3_ORDER_APPROVED_SHA256": approved})
        self.assertEqual(self.fx.api.mutations, [])
        outcome, changed, _ = self.run_command(raw, self.armed(raw))
        self.assertIn("APPLIED", outcome)
        self.assertEqual(changed, 1)
        self.assertEqual(self.fx.api.items["I815"]["values"][ORDER], 3)

    def test_replay_after_apply_and_stale_before_refused(self):
        raw = order_command(request_row(), operation="apply")
        self.run_command(raw, self.armed(raw))
        count = len(self.fx.api.mutations)
        with self.assertRaisesRegex(dispatch.DispatchError, "expected_before"):
            self.run_command(raw, self.armed(raw))
        self.assertEqual(len(self.fx.api.mutations), count)

    def test_malformed_order_commands_refused(self):
        bad = [
            json.dumps({"version": 2, "kind": dispatch.ORDER_KIND, "operation": "check",
                        "items": [request_row()]}),
            json.dumps({"version": 1, "kind": "status", "operation": "check", "items": [request_row()]}),
            json.dumps({"version": 1, "kind": dispatch.ORDER_KIND, "operation": "order",
                        "items": [request_row()]}),
            order_command({k: v for k, v in request_row().items() if k != "expected_before"}),
            order_command(request_row(before=1.5)),
            order_command(request_row(before=False)),
            order_command(request_row(order=0)),
            order_command(request_row(order=2.0)),
            order_command(request_row(set={"Status": "Ready"})),
            order_command(request_row(allow_reassignment=True)),
            order_command(request_row(), request_row()),
            order_command(request_row(982)),
            order_command(*(request_row(n, n) for n in range(1, dispatch.MAX_ITEMS + 2))),
        ]
        for raw in bad:
            with self.subTest(raw=raw[:80]), self.assertRaises(dispatch.DispatchError):
                dispatch.command_kind(raw)


class OrderRelayTests(unittest.TestCase):
    def main_with(self, command, extra_env):
        fx = OrderFixture()
        rest = RelayREST(command)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "event.json"
            path.write_text(json.dumps(event()), encoding="utf-8")
            env = {**environment(), "GITHUB_EVENT_PATH": str(path),
                   "GITHUB_TOKEN": fake_token("actions"), "PROJECT_SYNC_TOKEN": fake_token("project"),
                   "GITHUB_RUN_ID": "9007", "PROJECT3_REQUEST_RULESET_ID": "1234",
                   "PROJECT3_REQUEST_RULESET_UPDATED_AT": "2026-10-30T15:00:00Z", **extra_env}
            with (mock.patch.dict(os.environ, env, clear=True),
                  mock.patch.object(relay, "provenance_client", return_value=rest),
                  mock.patch.object(relay.triage, "rest_client", return_value=fx.read),
                  mock.patch.object(relay.pm, "http_transport", return_value=fx.api),
                  mock.patch.object(relay, "create_status") as claims,
                  mock.patch.object(relay.dispatch, "report") as audit):
                code = relay.main()
        return code, fx, [c.args[2] for c in claims.call_args_list], audit

    def test_relay_claims_and_applies_only_approved_order(self):
        raw = order_command(request_row(), operation="apply")
        digest = dispatch.order_digest(dispatch.parse_order(raw)[1])
        code, fx, claims, audit = self.main_with(raw, {
            "PROJECT3_ORDER_WRITE_MODE": "apply", "PROJECT3_ORDER_APPROVED_SHA256": digest,
            "PROJECT3_ASSIGN_DISPATCH_MODE": "apply"})
        self.assertEqual((code, claims), (0, ["pending", "success"]))
        self.assertEqual(fx.api.items["I815"]["values"][ORDER], 3)
        self.assertIn("APPLIED", audit.call_args.args[0])

    def test_relay_unapproved_apply_consumes_claim_without_writes(self):
        raw = order_command(request_row(), operation="apply")
        code, fx, claims, _ = self.main_with(raw, {"PROJECT3_ORDER_WRITE_MODE": "apply"})
        self.assertEqual((code, claims, fx.api.mutations), (1, ["pending", "failure"], []))

    def test_relay_already_claimed_commit_is_not_replayed(self):
        raw = order_command(request_row(), operation="apply")
        rest = RelayREST(raw)
        rest.statuses = [{"context": relay.STATUS_CONTEXT, "state": "success"}]
        with self.assertRaisesRegex(relay.RequestError, "no replay"):
            relay._status_history(rest, SHA)

    def test_relay_validates_order_command_before_claim(self):
        raw = order_command(request_row(order=True), operation="apply")
        code, fx, claims, _ = self.main_with(raw, {})
        self.assertEqual((code, claims, fx.api.mutations), (1, [], []))


def planning_value(kind, value):
    if value is None:
        return None
    if kind == "NUMBER":
        return {"__typename": "ProjectV2ItemFieldNumberValue", "number": value}
    return {"__typename": "ProjectV2ItemFieldSingleSelectValue", "name": value}


class FakePlanningGitHub:
    def __init__(self, nodes, *, fields=None, public=True):
        self.nodes = nodes
        self.public = public
        self.fields = fields if fields is not None else {
            name: kind for name, kind in pr.PLANNING_FIELDS.values()}
        self.queries = []

    def __call__(self, document, variables):
        self.queries.append(document)
        if "fields(first" in document:
            nodes = [{"__typename": "X", "id": "F" + name, "name": name, "dataType": kind}
                     for name, kind in self.fields.items()]
            return {"user": {"projectV2": {
                "id": pr.PROJECT_ID, "number": 3, "public": self.public, "closed": False,
                "owner": {"__typename": "User", "login": pr.OWNER},
                "fields": {"totalCount": len(nodes), "nodes": nodes}}}}
        return {"user": {"projectV2": {"id": pr.PROJECT_ID, "items": {
            "totalCount": len(self.nodes), "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": copy.deepcopy(self.nodes)}}}}


def planning_item(item_id, number, *, order=None, labels=("bug",), parent=None, repo="Fooftilly/PRKS",
                  status="Inbox"):
    values = {"status": status, "implementation_order": order}
    node = {"id": item_id, "isArchived": False}
    for key, (_, kind) in pr.PLANNING_FIELDS.items():
        node[key] = planning_value(kind, values.get(key))
    node["content"] = {
        "__typename": "Issue", "number": number, "state": "OPEN",
        "repository": {"nameWithOwner": repo, "isPrivate": False},
        "labels": {"totalCount": len(labels), "nodes": [{"name": n} for n in labels]},
        "parent": None if parent is None else {"number": parent, "repository": {"nameWithOwner": "Fooftilly/PRKS"}},
    }
    return node


class PlanningSnapshotTests(unittest.TestCase):
    NOW = datetime(2026, 11, 27, 20, 0, tzinfo=timezone.utc)

    def build(self, fake):
        return pr.build_planning(fake, generated_at=self.NOW)

    def test_snapshot_lists_orders_labels_and_parent_and_validates(self):
        fake = FakePlanningGitHub([
            planning_item("I2", 20, order=2.0, labels=("candidate", "audit-finding"), parent=10),
            planning_item("I1", 10),
            {"id": "P1", "isArchived": False, **{k: None for k in pr.PLANNING_FIELDS},
             "content": {"__typename": "PullRequest"}},
        ])
        snap = self.build(fake)
        self.assertEqual([r["number"] for r in snap["issues"]], [10, 20])
        self.assertIsNone(snap["issues"][0]["implementation_order"])
        self.assertEqual(snap["issues"][1]["implementation_order"], 2)
        self.assertEqual(snap["issues"][1]["labels"], ["audit-finding", "candidate"])
        self.assertEqual(snap["issues"][1]["parent"], "Fooftilly/PRKS#10")
        self.assertEqual(snap["counts"]["skipped_items"], {"pull_requests": 1})
        self.assertIn("blocked_by", snap["dependencies"])
        self.assertTrue(all(pr.is_read_only(q) for q in fake.queries))
        pr.check_planning(json.loads(json.dumps(snap)), now=self.NOW)

    def test_missing_or_wrong_type_order_field_fails_closed(self):
        for fields in ({k: v for k, v in FakePlanningGitHub([]).fields.items() if k != ORDER},
                       {**FakePlanningGitHub([]).fields, ORDER: "TEXT"}):
            with self.subTest(fields=sorted(fields)), self.assertRaisesRegex(pr.ReadinessError, ORDER):
                self.build(FakePlanningGitHub([], fields=fields))

    def test_private_project_and_malformed_numbers_refused(self):
        with self.assertRaises(pr.Confidential):
            self.build(FakePlanningGitHub([], public=False))
        for value in (True, "3", float("inf")):
            node = planning_item("I1", 1)
            node["implementation_order"] = {"__typename": "ProjectV2ItemFieldNumberValue", "number": value}
            with self.subTest(value=value), self.assertRaises(pr.Malformed):
                self.build(FakePlanningGitHub([node]))

    def test_truncated_labels_refused(self):
        node = planning_item("I1", 1)
        node["content"]["labels"]["totalCount"] = 60
        with self.assertRaisesRegex(pr.Malformed, "more labels"):
            self.build(FakePlanningGitHub([node]))

    def test_consumer_rejects_stale_incomplete_or_readiness_schema(self):
        snap = self.build(FakePlanningGitHub([planning_item("I1", 1, order=4)]))
        with self.assertRaisesRegex(pr.ReadinessError, "stale"):
            pr.check_planning(snap, now=self.NOW + timedelta(hours=31))
        for mutate in (lambda s: s.update(complete=False),
                       lambda s: s.update(schema=pr.SCHEMA),
                       lambda s: s["issues"][0].update(implementation_order=True),
                       lambda s: s["issues"][0].update(extra=1),
                       lambda s: s["counts"].update(issues=5)):
            broken = copy.deepcopy(snap)
            mutate(broken)
            with self.assertRaises(pr.ReadinessError):
                pr.check_planning(broken, now=self.NOW)
        with self.assertRaises(pr.ReadinessError):
            pr.check_snapshot(snap, now=self.NOW)

    def test_cli_writes_planning_and_check_planning_accepts_it(self):
        fake = FakePlanningGitHub([planning_item("I1", 1, order=4)])
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "project3-planning.json"
            code = pr.main(["planning", "--out", str(out)], env={"PROJECT3_READINESS_TOKEN": "t"},
                           transport_factory=lambda _token: fake, now=lambda: self.NOW)
            self.assertEqual(code, 0)
            self.assertEqual(pr.main(["check-planning", "--snapshot", str(out)], env={},
                                     now=lambda: self.NOW), 0)
            self.assertEqual(pr.main(["check", "--snapshot", str(out)], env={},
                                     now=lambda: self.NOW), 1)


class WorkflowOrderTests(unittest.TestCase):
    def test_writers_pass_order_gates_and_refresh_planning(self):
        for name in ("project3-assignment-dispatch.yml", "project3-assignment-request-processor.yml"):
            text = (ROOT / ".github/workflows" / name).read_text()
            with self.subTest(name=name):
                self.assertIn("PROJECT3_ORDER_WRITE_MODE: ${{ vars.PROJECT3_ORDER_WRITE_MODE }}", text)
                self.assertIn("PROJECT3_ORDER_APPROVED_SHA256: ${{ vars.PROJECT3_ORDER_APPROVED_SHA256 }}", text)
                self.assertIn("gh workflow run project3-readiness.yml --repo Fooftilly/PRKS --ref master", text)
                self.assertIn("group: project3-assignment-mutations", text)
                self.assertEqual(text.count("secrets."), 1)


if __name__ == "__main__":
    unittest.main()
