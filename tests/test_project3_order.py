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


class FakeClaims:
    def __init__(self):
        self.log: list[tuple[str, str]] = []

    def states(self, contexts):
        found: dict[str, set[str]] = {}
        for context, state in self.log:
            if context in contexts:
                found.setdefault(context, set()).add(state)
        return found

    def mark(self, context, state):
        self.log.append((context, state))

    def recent(self, context):
        return any(c == context for c, _ in self.log[-100:])


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

    def test_removed_and_re_added_item_is_refused(self):
        plan = self.approved(rank())
        self.assertEqual(plan["items"][0]["expected_item"], "I815")
        self.fx.api.items["J815"] = self.fx.api.items.pop("I815")
        with self.assertRaisesRegex(t.TriageError, "replaced since review"):
            t.apply_order(plan, self.fx.project(apply=True), self.fx.read)
        self.assertEqual(self.fx.api.mutations, [])
        broken = copy.deepcopy(plan)
        broken["items"][0]["expected_item"] = ""
        with self.assertRaisesRegex(t.TriageError, "expected_item"):
            t.load_plan(json.dumps(broken).encode())

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
        self.claims = FakeClaims()

    def run_command(self, raw, env):
        allow = dispatch.mutations_allowed(raw, env)
        return dispatch.run(raw, project=self.fx.project(apply=allow), read=self.fx.read, env=env,
                            claims=self.claims)

    def digest(self, raw):
        check = json.loads(raw)
        check["operation"] = "check"
        outcome = dispatch.run(json.dumps(check), project=self.fx.project(), read=self.fx.read, env={})[0]
        return outcome.rsplit(" ", 1)[1]

    def armed(self, raw):
        return {"PROJECT3_ORDER_WRITE_MODE": "apply",
                "PROJECT3_ORDER_APPROVED_SHA256": self.digest(raw)}

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
        self.assertIn(f"field {t.order_field(self.fx.project())['id']}; ", outcome)
        self.assertRegex(outcome, r"order digest [0-9a-f]{64}$")
        self.assertEqual((changed, issues, self.fx.api.mutations), (0, 1, []))

    def test_check_and_apply_share_one_digest(self):
        check = self.digest(order_command(request_row()))
        self.assertEqual(check, self.digest(order_command(request_row(), operation="apply")))
        self.assertNotEqual(check, self.digest(order_command(request_row(order=4))))

    def test_issue_change_after_check_invalidates_approval(self):
        raw = order_command(request_row(), operation="apply")
        env = self.armed(raw)
        self.fx.issues[815]["labels"].append({"name": "accepted"})
        with self.assertRaisesRegex(dispatch.DispatchError, "maintainer-approved"):
            self.run_command(raw, env)
        self.fx.issues[815]["labels"].pop()
        self.fx.dependencies[815] = [{"number": 4, "state": "open"}]
        with self.assertRaisesRegex(dispatch.DispatchError, "maintainer-approved"):
            self.run_command(raw, env)
        self.assertEqual(self.fx.api.mutations, [])

    def test_apply_needs_mode_and_exact_approved_digest(self):
        raw = order_command(request_row(), operation="apply")
        self.assertIn("DRY RUN", self.run_command(raw, {})[0])
        for approved in ("", "0" * 64, self.digest(order_command(request_row(order=4)))):
            with self.subTest(approved=approved), \
                    self.assertRaisesRegex(dispatch.DispatchError, "maintainer-approved"):
                self.run_command(raw, {"PROJECT3_ORDER_WRITE_MODE": "apply",
                                       "PROJECT3_ORDER_APPROVED_SHA256": approved})
        self.assertEqual(self.fx.api.mutations, [])
        outcome, changed, _ = self.run_command(raw, self.armed(raw))
        self.assertIn("APPLIED", outcome)
        self.assertEqual(changed, 1)
        self.assertEqual(self.fx.api.items["I815"]["values"][ORDER], 3)

    def test_approval_is_consumed_even_after_rank_is_restored(self):
        raw = order_command(request_row(), operation="apply")
        env = self.armed(raw)
        self.run_command(raw, env)
        digest = env["PROJECT3_ORDER_APPROVED_SHA256"]
        claim = dispatch.batch_claim(digest)
        self.assertEqual(self.claims.log, [(claim, "pending"), (claim, "success")])
        del self.fx.api.items["I815"]["values"][ORDER]
        count = len(self.fx.api.mutations)
        with self.assertRaisesRegex(dispatch.DispatchError, "already used"):
            self.run_command(raw, env)
        self.assertEqual(len(self.fx.api.mutations), count)

    def test_failed_apply_keeps_claim_and_records_failure(self):
        raw = order_command(request_row(), operation="apply")
        env = self.armed(raw)

        def fail(name, variables):
            raise t.pm.MigrationError("GraphQL error: simulated outage")

        self.fx.api.before_mutation = fail
        with self.assertRaisesRegex(t.pm.MigrationError, "simulated outage"):
            self.run_command(raw, env)
        self.assertEqual([state for _, state in self.claims.log], ["pending", "failure"])

    def test_apply_without_claim_store_is_refused(self):
        raw = order_command(request_row(), operation="apply")
        env = self.armed(raw)
        with self.assertRaisesRegex(dispatch.DispatchError, "claim store"):
            dispatch.run(raw, project=self.fx.project(apply=True), read=self.fx.read, env=env)
        self.assertEqual(self.fx.api.mutations, [])

    def test_replaced_field_after_check_invalidates_approval_before_claim(self):
        raw = order_command(request_row(), operation="apply")
        env = self.armed(raw)
        self.fx.api._fid = lambda name: "F_new_" + name.replace(" ", "_")
        with self.assertRaisesRegex(dispatch.DispatchError, "maintainer-approved"):
            self.run_command(raw, env)
        self.assertEqual((self.claims.log, self.fx.api.mutations), ([], []))

    def test_replaced_item_after_check_invalidates_approval(self):
        raw = order_command(request_row(), operation="apply")
        env = self.armed(raw)
        self.fx.api.items["J815"] = self.fx.api.items.pop("I815")
        with self.assertRaisesRegex(dispatch.DispatchError, "maintainer-approved"):
            self.run_command(raw, env)
        self.assertEqual(self.fx.api.mutations, [])

    def test_replay_after_apply_and_stale_before_refused(self):
        raw = order_command(request_row(), operation="apply")
        self.run_command(raw, self.armed(raw))
        count = len(self.fx.api.mutations)
        with self.assertRaisesRegex(dispatch.DispatchError, "expected_before"):
            self.run_command(raw, self.armed(raw))
        self.assertEqual(len(self.fx.api.mutations), count)

    def test_github_claims_use_fixed_master_commit_and_digest_context(self):
        claims = dispatch.GitHubOrderClaims("t", "77")
        digest = "a" * 64
        sent = []

        def fake_send(url, payload=None):
            sent.append((url, payload))
            if payload is None:
                return [{"context": "other"}]
            body = json.loads(payload)
            return {"context": body["context"], "state": body["state"]}

        with mock.patch.object(claims, "_send", side_effect=fake_send):
            self.assertEqual(claims.states([dispatch.batch_claim(digest)]), {})
            claims.mark(dispatch.batch_claim(digest), "pending")
        self.assertIn(f"/commits/{dispatch.ORDER_CLAIM_SHA}/statuses?per_page=100&page=1", sent[0][0])
        self.assertTrue(sent[1][0].endswith(f"/statuses/{dispatch.ORDER_CLAIM_SHA}"))
        self.assertEqual(json.loads(sent[1][1])["context"], dispatch.ORDER_CLAIM_PREFIX + digest)
        with mock.patch.object(claims, "_send", return_value=[
                {"context": dispatch.ORDER_CLAIM_PREFIX + digest, "state": "success"}]):
            self.assertEqual(claims.states([dispatch.batch_claim(digest)]),
                             {dispatch.batch_claim(digest): {"success"}})
        with mock.patch.object(claims, "_send", return_value={"context": "x", "state": "pending"}), \
                self.assertRaisesRegex(dispatch.DispatchError, "not verified"):
            claims.mark(dispatch.batch_claim(digest), "pending")
        for context in (digest, dispatch.ORDER_CLAIM_PREFIX + "A" * 64,
                        dispatch.plan_claim(digest, 0), dispatch.plan_claim(digest, dispatch.MAX_BATCHES + 1),
                        dispatch.PLAN_CLAIM_PREFIX + digest):
            with self.subTest(context=context), self.assertRaisesRegex(dispatch.DispatchError, "invalid"):
                claims.mark(context, "pending")
        with self.assertRaises(dispatch.DispatchError):
            dispatch.GitHubOrderClaims("t", "")

    def test_claim_lookup_reads_past_one_thousand_statuses(self):
        claims = dispatch.GitHubOrderClaims("t", "77")
        old, fresh = "b" * 64, "c" * 64
        pages = {page: [{"context": f"{dispatch.ORDER_CLAIM_PREFIX}{page:064x}"}] * 100 for page in range(1, 13)}
        pages[13] = [{"context": dispatch.ORDER_CLAIM_PREFIX + old, "state": "pending"}]

        def fake_send(url, payload=None):
            return pages.get(int(url.rsplit("=", 1)[1]), [])

        with mock.patch.object(claims, "_send", side_effect=fake_send):
            self.assertEqual(claims.states([dispatch.batch_claim(old), dispatch.batch_claim(fresh)]),
                             {dispatch.batch_claim(old): {"pending"}})

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


def plan_command(manifest, index, rows, operation="check"):
    return json.dumps({"version": 1, "kind": dispatch.PLAN_KIND, "operation": operation,
                       "manifest": manifest, "batch": index, "items": rows})


def armed_for(digest):
    return {"PROJECT3_ORDER_WRITE_MODE": "apply", "PROJECT3_ORDER_APPROVED_SHA256": digest}


class OrderPlanTests(unittest.TestCase):
    def setUp(self):
        self.fx = OrderFixture()
        self.claims = FakeClaims()
        self.batches = [[request_row(815, 3)], [request_row(509, 4)], [request_row(635, 5)]]
        self.manifest = self.build(self.batches)
        self.approval = dispatch.manifest_digest(self.manifest)

    def batch_digest(self, rows):
        outcome = dispatch.run(order_command(*rows), project=self.fx.project(), read=self.fx.read, env={})
        return outcome[0].rsplit(" ", 1)[1]

    def build(self, batches):
        field = t.order_field(self.fx.project())
        return {"schema": dispatch.MANIFEST_SCHEMA, "field": field, "batches": [
            {"digest": self.batch_digest(rows), "items": [[r["number"], r["order"]] for r in rows]}
            for rows in batches]}

    def run_plan(self, index, env, manifest=None, rows=None):
        raw = plan_command(manifest or self.manifest, index,
                           rows if rows is not None else self.batches[index - 1], "apply")
        allow = dispatch.mutations_allowed(raw, env)
        return dispatch.run(raw, project=self.fx.project(apply=allow), read=self.fx.read,
                            env=env, claims=self.claims)

    def order_of(self, number):
        return self.fx.api.items[f"I{number}"]["values"].get(ORDER)

    def test_check_prints_manifest_digest_and_is_not_approval(self):
        raw = plan_command(self.manifest, 2, self.batches[1])
        outcome, changed, _ = dispatch.run(raw, project=self.fx.project(), read=self.fx.read,
                                           env={"PROJECT3_ORDER_WRITE_MODE": "apply"})
        self.assertIn("CHECK ONLY", outcome)
        self.assertIn(f"order digest {self.manifest['batches'][1]['digest']}; batch 2/3", outcome)
        self.assertTrue(outcome.endswith("manifest digest " + self.approval))
        with self.assertRaisesRegex(dispatch.DispatchError, "maintainer-approved"):
            self.run_plan(1, {"PROJECT3_ORDER_WRITE_MODE": "apply"})
        self.assertIn("DRY RUN", self.run_plan(1, {"PROJECT3_ORDER_APPROVED_SHA256": self.approval})[0])
        self.assertEqual((changed, self.fx.api.mutations, self.claims.log), (0, [], []))

    def test_one_approval_applies_every_batch_in_order(self):
        env = armed_for(self.approval)
        statuses = {n: self.fx.api.items[f"I{n}"]["values"].get("Status") for n in (815, 509, 635)}
        for index in (1, 2, 3):
            outcome, changed, _ = self.run_plan(index, env)
            self.assertIn(f"{3 - index} batches pending; batch {index}/3", outcome)
            self.assertEqual(changed, 1)
        self.assertEqual([self.order_of(n) for n in (815, 509, 635)], [3, 4, 5])
        self.assertEqual({n: self.fx.api.items[f"I{n}"]["values"].get("Status") for n in statuses}, statuses)
        field = self.manifest["field"]["id"]
        self.assertEqual({m[1]["field"] for m in self.fx.api.mutations}, {field})
        expected = []
        for index, entry in enumerate(self.manifest["batches"], start=1):
            step, batch = dispatch.plan_claim(self.approval, index), dispatch.batch_claim(entry["digest"])
            expected += [(step, "pending"), (batch, "pending"), (batch, "success"), (step, "success")]
        self.assertEqual(self.claims.log, expected)

    def test_skipped_or_reordered_batch_is_refused_before_claim(self):
        env = armed_for(self.approval)
        with self.assertRaisesRegex(dispatch.DispatchError, "batch 1 has not succeeded"):
            self.run_plan(2, env)
        self.run_plan(1, env)
        with self.assertRaisesRegex(dispatch.DispatchError, "batch 2 has not succeeded"):
            self.run_plan(3, env)
        self.assertEqual((self.order_of(509), self.order_of(635)), (None, None))
        self.assertEqual(len(self.claims.log), 4)

    def test_replay_is_refused_after_values_are_restored(self):
        env = armed_for(self.approval)
        self.run_plan(1, env)
        del self.fx.api.items["I815"]["values"][ORDER]
        count = len(self.fx.api.mutations)
        with self.assertRaisesRegex(dispatch.DispatchError, "already used; no replay"):
            self.run_plan(1, env)
        self.assertEqual(len(self.fx.api.mutations), count)

    def test_used_later_digest_stops_the_plan_before_any_write(self):
        self.claims.mark(dispatch.batch_claim(self.manifest["batches"][2]["digest"]), "success")
        with self.assertRaisesRegex(dispatch.DispatchError, "batch 3: order digest was already used"):
            self.run_plan(1, armed_for(self.approval))
        self.assertEqual((self.fx.api.mutations, len(self.claims.log)), ([], 1))

    def test_batch_claims_are_shared_with_the_single_batch_path(self):
        first, second = (entry["digest"] for entry in self.manifest["batches"][:2])
        single = order_command(*self.batches[0], operation="apply")
        dispatch.run(single, project=self.fx.project(apply=True), read=self.fx.read,
                     env=armed_for(first), claims=self.claims)
        del self.fx.api.items["I815"]["values"][ORDER]
        with self.assertRaisesRegex(dispatch.DispatchError, "already used; no replay"):
            self.run_plan(1, armed_for(self.approval))

        self.claims.log.clear()
        self.run_plan(1, armed_for(self.approval))
        del self.fx.api.items["I815"]["values"][ORDER]
        with self.assertRaisesRegex(dispatch.DispatchError, "already used; no replay"):
            dispatch.run(single, project=self.fx.project(apply=True), read=self.fx.read,
                         env=armed_for(first), claims=self.claims)
        with self.assertRaisesRegex(dispatch.DispatchError, "maintainer-approved"):
            dispatch.run(order_command(*self.batches[1], operation="apply"),
                         project=self.fx.project(apply=True), read=self.fx.read,
                         env=armed_for(self.approval), claims=self.claims)
        with self.assertRaisesRegex(dispatch.DispatchError, "maintainer-approved"):
            self.run_plan(2, armed_for(second))

    def test_failed_batch_blocks_the_rest_and_recovery_needs_a_new_plan(self):
        env = armed_for(self.approval)
        self.run_plan(1, env)

        def fail(name, variables):
            raise t.pm.MigrationError("GraphQL error: simulated outage")

        self.fx.api.before_mutation = fail
        with self.assertRaisesRegex(t.pm.MigrationError, "simulated outage"):
            self.run_plan(2, env)
        self.fx.api.before_mutation = None
        failed = [dispatch.batch_claim(self.manifest["batches"][1]["digest"]),
                  dispatch.plan_claim(self.approval, 2)]
        self.assertEqual(self.claims.log[-2:], [(c, "failure") for c in failed])
        with self.assertRaisesRegex(dispatch.DispatchError, "already used"):
            self.run_plan(2, env)
        with self.assertRaisesRegex(dispatch.DispatchError, "batch 2 has not succeeded"):
            self.run_plan(3, env)
        self.assertEqual((self.order_of(509), self.order_of(635)), (None, None))

        retry = [request_row(509, 4, reason="Retry after the failed batch; roadmap and PRs re-checked.")]
        rest = [retry, self.batches[2]]
        manifest = self.build(rest)
        self.assertEqual(manifest["batches"][1]["digest"], self.manifest["batches"][2]["digest"])
        approval = dispatch.manifest_digest(manifest)
        with self.assertRaisesRegex(dispatch.DispatchError, "maintainer-approved"):
            self.run_plan(1, env, manifest, retry)
        for index, rows in enumerate(rest, start=1):
            self.run_plan(index, armed_for(approval), manifest, rows)
        self.assertEqual((self.order_of(509), self.order_of(635)), (4, 5))

    def test_partial_failure_reports_blocked_batches(self):
        env = armed_for(self.approval)
        self.run_plan(1, env)
        self.fx.api.before_mutation = lambda name, variables: (_ for _ in ()).throw(
            t.pm.MigrationError("GraphQL error: lost"))
        with mock.patch("sys.stderr") as err, self.assertRaises(t.pm.MigrationError):
            self.run_plan(2, env)
        written = "".join(call.args[0] for call in err.write.call_args_list)
        self.assertIn(f"batch 2/3; manifest digest {self.approval}: FAILED / POSSIBLY PARTIAL", written)
        self.assertIn("batch 3 stays blocked under this manifest", written)
        manifest = self.build(self.batches[2:])
        approval = dispatch.manifest_digest(manifest)
        with mock.patch("sys.stderr") as err, self.assertRaises(t.pm.MigrationError):
            self.run_plan(1, armed_for(approval), manifest, self.batches[2])
        written = "".join(call.args[0] for call in err.write.call_args_list)
        self.assertIn("batch 1/1", written)
        self.assertIn("no later batches under this manifest", written)

    def stale(self, change, restore, error="live evidence differs"):
        self.setUp()
        env = armed_for(self.approval)
        self.run_plan(1, env)
        change()
        with self.assertRaisesRegex(dispatch.DispatchError, error):
            self.run_plan(2, env)
        restore()
        self.assertEqual(self.claims.log[-2:], [(dispatch.plan_claim(self.approval, 2), "pending"),
                                                (dispatch.plan_claim(self.approval, 2), "failure")])
        with self.assertRaisesRegex(dispatch.DispatchError, "batch 2 already started"):
            self.run_plan(2, env)
        self.assertEqual(self.order_of(509), None)
        manifest = self.build(self.batches[1:])
        self.assertEqual(manifest["batches"][0]["digest"], self.manifest["batches"][1]["digest"])
        self.run_plan(1, armed_for(dispatch.manifest_digest(manifest)), manifest, self.batches[1])
        self.assertEqual(self.order_of(509), 4)

    def test_stale_refusal_ends_the_manifest_but_keeps_the_batch_digest(self):
        values = lambda: self.fx.api.items["I509"]["values"]
        labels = lambda: self.fx.issues[509]["labels"]
        self.stale(lambda: values().update({ORDER: 9}), lambda: values().pop(ORDER), "expected_before")
        self.stale(lambda: labels().append({"name": "accepted"}), lambda: labels().pop())
        self.stale(lambda: self.fx.dependencies.update({509: [{"number": 4, "state": "open"}]}),
                   lambda: self.fx.dependencies.update({509: []}))
        self.stale(lambda: self.fx.api.items.update(J509=self.fx.api.items.pop("I509")),
                   lambda: self.fx.api.items.update(I509=self.fx.api.items.pop("J509")))

    def test_replaced_field_ends_the_manifest(self):
        env = armed_for(self.approval)
        self.run_plan(1, env)
        self.fx.api._fid = lambda name: "F_new_" + name.replace(" ", "_")
        with self.assertRaisesRegex(dispatch.DispatchError, "field identity differs"):
            self.run_plan(2, env)
        self.assertEqual(self.claims.log[-1], (dispatch.plan_claim(self.approval, 2), "failure"))

    def test_unapproved_or_out_of_order_apply_stays_read_only(self):
        self.fx.issues[509]["labels"].append({"name": "accepted"})
        with self.assertRaisesRegex(dispatch.DispatchError, "batch 1 has not succeeded"):
            self.run_plan(2, armed_for(self.approval))
        with self.assertRaisesRegex(dispatch.DispatchError, "maintainer-approved"):
            self.run_plan(1, armed_for("0" * 64))
        self.assertEqual(self.claims.log, [])

    def test_altered_manifest_and_revoked_approval_are_refused(self):
        env = armed_for(self.approval)
        self.run_plan(1, env)
        altered = copy.deepcopy(self.manifest)
        altered["batches"][2]["items"] = [[635, 6]]
        with self.assertRaisesRegex(dispatch.DispatchError, "maintainer-approved"):
            self.run_plan(2, env, altered)
        self.assertIn("DRY RUN", self.run_plan(2, {"PROJECT3_ORDER_APPROVED_SHA256": self.approval})[0])
        with self.assertRaisesRegex(dispatch.DispatchError, "maintainer-approved"):
            self.run_plan(2, armed_for("0" * 64))
        self.assertEqual(self.order_of(509), None)

    def test_substituted_batch_rows_are_refused(self):
        raw = plan_command(self.manifest, 2, self.batches[2], "apply")
        with self.assertRaisesRegex(dispatch.DispatchError, "rows differ from its manifest entry"):
            dispatch.command_kind(raw)
        forged = copy.deepcopy(self.manifest)
        forged["batches"][0]["items"] = [[635, 5]]
        forged["batches"][2]["items"] = [[815, 3]]
        with self.assertRaisesRegex(dispatch.DispatchError, "live evidence differs"):
            self.run_plan(1, armed_for(dispatch.manifest_digest(forged)), forged, self.batches[2])
        self.assertEqual(self.fx.api.mutations, [])

    def test_malformed_manifests_are_refused(self):
        def variant(change):
            manifest = copy.deepcopy(self.manifest)
            change(manifest)
            return manifest

        first = self.manifest["batches"][0]
        bad = {
            "schema": variant(lambda m: m.update(schema="prks-project3-order-manifest/v2")),
            "field name": variant(lambda m: m["field"].update(name="Roadmap Order")),
            "extra key": variant(lambda m: m.update(approved=True)),
            "no batches": variant(lambda m: m.update(batches=[])),
            "too many": variant(lambda m: m.update(batches=[
                {"digest": f"{n:064x}", "items": [[1000 + n, 1000 + n]]}
                for n in range(dispatch.MAX_BATCHES + 1)])),
            "duplicate issue": variant(lambda m: m["batches"][2].update(items=[[815, 9]])),
            "duplicate rank": variant(lambda m: m["batches"][2].update(items=[[635, 3]])),
            "duplicate digest": variant(lambda m: m["batches"][2].update(digest=first["digest"])),
            "bad digest": variant(lambda m: m["batches"][2].update(digest="A" * 64)),
            "boolean rank": variant(lambda m: m["batches"][2].update(items=[[635, True]])),
            "fraction rank": variant(lambda m: m["batches"][2].update(items=[[635, 5.5]])),
            "big batch": variant(lambda m: m["batches"][2].update(
                items=[[2000 + n, 2000 + n] for n in range(dispatch.MAX_ITEMS + 1)])),
            "object pair": variant(lambda m: m["batches"][2].update(items=[{"number": 635, "order": 5}])),
        }
        for name, manifest in bad.items():
            with self.subTest(name), self.assertRaises(dispatch.DispatchError):
                dispatch.command_kind(plan_command(manifest, 1, self.batches[0]))
        for index in (0, 4, True):
            with self.subTest(index=index), self.assertRaises(dispatch.DispatchError):
                dispatch.command_kind(plan_command(self.manifest, index, self.batches[0]))

    def test_full_manifest_fits_the_command_byte_limit(self):
        manifest = {"schema": dispatch.MANIFEST_SCHEMA, "field": self.manifest["field"], "batches": [
            {"digest": f"{b:064x}", "items": [[1000 + b * 10 + n, 1000 + b * 10 + n] for n in range(10)]}
            for b in range(dispatch.MAX_BATCHES)]}
        rows = [request_row(1000 + n, 1000 + n) for n in range(10)]
        raw = plan_command(manifest, 1, rows)
        self.assertLess(len(raw.encode("utf-8")), dispatch.MAX_BYTES)
        self.assertEqual(dispatch.command_kind(raw), (dispatch.PLAN_KIND, "check"))


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
                  mock.patch.object(relay.dispatch, "GitHubOrderClaims", return_value=self.order_claims),
                  mock.patch.object(relay.dispatch, "report") as audit):
                code = relay.main()
        return code, fx, [c.args[2] for c in claims.call_args_list], audit

    def setUp(self):
        self.order_claims = FakeClaims()

    def test_relay_claims_and_applies_only_approved_order(self):
        raw = order_command(request_row(), operation="apply")
        fx = OrderFixture()
        check = dispatch.run(order_command(request_row()), project=fx.project(), read=fx.read, env={})
        digest = check[0].rsplit(" ", 1)[1]
        code, fx, claims, audit = self.main_with(raw, {
            "PROJECT3_ORDER_WRITE_MODE": "apply", "PROJECT3_ORDER_APPROVED_SHA256": digest,
            "PROJECT3_ASSIGN_DISPATCH_MODE": "apply"})
        self.assertEqual((code, claims), (0, ["pending", "success"]))
        self.assertEqual([state for _, state in self.order_claims.log], ["pending", "success"])
        self.assertEqual(fx.api.items["I815"]["values"][ORDER], 3)
        code, fx, claims, _ = self.main_with(raw, {
            "PROJECT3_ORDER_WRITE_MODE": "apply", "PROJECT3_ORDER_APPROVED_SHA256": digest})
        self.assertEqual((code, claims, fx.api.mutations), (1, ["pending", "failure"], []))
        self.assertIn("APPLIED", audit.call_args.args[0])

    def test_relay_applies_first_plan_batch_under_manifest_approval(self):
        fx = OrderFixture()
        rows = [request_row()]
        check = dispatch.run(order_command(*rows), project=fx.project(), read=fx.read, env={})
        manifest = {"schema": dispatch.MANIFEST_SCHEMA, "field": t.order_field(fx.project()),
                    "batches": [{"digest": check[0].rsplit(" ", 1)[1], "items": [[815, 3]]}]}
        approval = dispatch.manifest_digest(manifest)
        code, fx, claims, _ = self.main_with(plan_command(manifest, 1, rows, "apply"), {
            "PROJECT3_ORDER_WRITE_MODE": "apply", "PROJECT3_ORDER_APPROVED_SHA256": approval})
        self.assertEqual((code, claims), (0, ["pending", "success"]))
        self.assertEqual(fx.api.items["I815"]["values"][ORDER], 3)
        self.assertIn((dispatch.plan_claim(approval, 1), "success"), self.order_claims.log)

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
    def test_writers_pass_order_gates_and_refresh_both_snapshots(self):
        for name in ("project3-assignment-dispatch.yml", "project3-assignment-request-processor.yml"):
            text = (ROOT / ".github/workflows" / name).read_text()
            with self.subTest(name=name):
                self.assertIn("PROJECT3_ORDER_WRITE_MODE: ${{ vars.PROJECT3_ORDER_WRITE_MODE }}", text)
                self.assertIn("PROJECT3_ORDER_APPROVED_SHA256: ${{ vars.PROJECT3_ORDER_APPROVED_SHA256 }}", text)
                self.assertIn("gh workflow run project3-agent-queue.yml --repo Fooftilly/PRKS --ref master || status=1", text)
                self.assertIn("gh workflow run project3-planning.yml --repo Fooftilly/PRKS --ref master || status=1", text)
                self.assertIn('exit "$status"', text)
                self.assertIn("group: project3-assignment-mutations", text)
                self.assertEqual(text.count("secrets."), 1)

    def test_planning_workflow_is_separate_read_only_and_master_only(self):
        text = (ROOT / ".github/workflows/project3-planning.yml").read_text()
        readiness = (ROOT / ".github/workflows/project3-readiness.yml").read_text()
        self.assertNotIn("planning", readiness)
        self.assertIn("\npermissions: {}\n", text)
        self.assertIn("      contents: read", text)
        self.assertNotIn(": write", text)
        self.assertIn("if: github.repository == 'Fooftilly/PRKS' && github.ref == 'refs/heads/master'", text)
        self.assertIn("environment: project-automation", text)
        self.assertEqual(text.count("secrets."), 1)
        self.assertIn("python3 scripts/project3_readiness.py planning", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("archive: false", text)


if __name__ == "__main__":
    unittest.main()
