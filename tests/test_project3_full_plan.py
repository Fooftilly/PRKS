"""Offline contracts for whole-plan Implementation Order check and apply (#1478)."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import project3_assignment_dispatch as dispatch
import project3_assignment_request as relay
import project3_triage as t
from test_project3_assignment_dispatch import event_for
from test_project3_assignment_request import RelayREST, environment, event, fake_token
from test_project3_order import (ORDER, FakeClaims, OrderFixture, armed_for, order_command,
                                 plan_command, request_row)


class FakeBudget:
    def __init__(self):
        self.required: list[int] = []
        self.waited: list[int] = []
        self.fail_require: str | None = None
        self.fail_wait_after: int | None = None

    def require(self, cost):
        self.required.append(cost)
        if self.fail_require:
            raise dispatch.DispatchError(self.fail_require)

    def wait(self, cost):
        self.waited.append(cost)
        if self.fail_wait_after is not None and len(self.waited) > self.fail_wait_after:
            raise dispatch.DispatchError("Actions-token requests would run out before the job deadline")


def full_command(batches, operation="check", manifest=None):
    payload: dict[str, Any] = {"version": 1, "kind": dispatch.FULL_KIND, "operation": operation,
                               "batches": batches}
    if manifest is not None:
        payload["manifest"] = manifest
    return json.dumps(payload)


class BigFixture(OrderFixture):
    def __init__(self, extra=0):
        super().__init__()
        for n in range(1000, 1000 + extra):
            self.api.put("Issue", n, Status="Backlog")
            self.issues[n] = {"number": n, "state": "open", "updated_at": "2026-11-27T10:00:00Z",
                              "labels": [{"name": "bug"}], "title": "Test issue"}
            self.dependencies[n] = []
        self.reads = 0
        base = self.read

        def counted(url):
            self.reads += 1
            return base(url)

        self.read = counted


class FullPlanTests(unittest.TestCase):
    def setUp(self):
        self.fx = BigFixture()
        self.claims = FakeClaims()
        self.budget = FakeBudget()
        self.batches = [[request_row(815, 3)], [request_row(509, 4)], [request_row(635, 5)]]

    def run_full(self, raw, env):
        allow = dispatch.mutations_allowed(raw, env)
        return dispatch.run(raw, project=self.fx.project(apply=allow), read=self.fx.read, env=env,
                            claims=self.claims, budget=self.budget)

    def check(self, batches=None):
        with mock.patch.object(dispatch, "publish") as published:
            outcome, changed, issues = self.run_full(full_command(batches or self.batches), {})
        lines = [line for call in published.call_args_list for line in call.args[0]]
        manifest = json.loads(next(line for line in lines if line.startswith("ORDER MANIFEST "))
                              .removeprefix("ORDER MANIFEST "))
        return outcome, manifest, lines

    def apply(self, manifest, env=None, batches=None):
        raw = full_command(batches or self.batches, "apply", manifest)
        with mock.patch.object(dispatch, "publish") as published:
            try:
                return self.run_full(raw, env if env is not None else
                                     armed_for(dispatch.manifest_digest(manifest)))
            finally:
                self.published = [line for call in published.call_args_list for line in call.args[0]]

    def order_of(self, number):
        return self.fx.api.items[f"I{number}"]["values"].get(ORDER)

    def test_check_validates_every_batch_in_one_read_only_run(self):
        outcome, manifest, lines = self.check()
        self.assertEqual(outcome, f"CHECK ONLY — no Project fields changed; 3 batches; "
                                  f"manifest digest {dispatch.manifest_digest(manifest)}")
        self.assertEqual((self.fx.api.mutations, self.claims.log), ([], []))
        self.assertIn("| 2 | #509 | None | 4 |", lines)
        self.assertFalse(any("Ranked after" in line for line in lines))
        singles = [dispatch.run(order_command(*rows), project=self.fx.project(), read=self.fx.read,
                                env={})[0].rsplit(" ", 1)[1] for rows in self.batches]
        self.assertEqual([entry["digest"] for entry in manifest["batches"]], singles)
        self.assertEqual(manifest["field"], t.order_field(self.fx.project()))

    def test_one_approval_applies_every_batch_in_one_run(self):
        _, manifest, _ = self.check()
        approval = dispatch.manifest_digest(manifest)
        outcome, changed, issues = self.apply(manifest)
        self.assertEqual((outcome, changed, issues),
                         (f"APPLIED — {ORDER} verified; 3 batches; manifest digest {approval}", 3, 3))
        self.assertEqual([self.order_of(n) for n in (815, 509, 635)], [3, 4, 5])
        self.assertEqual({m[1]["field"] for m in self.fx.api.mutations}, {manifest["field"]["id"]})
        expected = []
        for n, entry in enumerate(manifest["batches"], start=1):
            step, batch = dispatch.plan_claim(approval, n), dispatch.batch_claim(entry["digest"])
            expected += [(step, "pending"), (batch, "pending"), (batch, "success"), (step, "success")]
        self.assertEqual(self.claims.log, expected)
        self.assertIn("batch 2/3 verified: #509=4", self.published)

    def test_apply_needs_mode_exact_approval_and_claim_store(self):
        _, manifest, _ = self.check()
        approval = dispatch.manifest_digest(manifest)
        self.assertIn("DRY RUN", self.apply(manifest, {"PROJECT3_ORDER_APPROVED_SHA256": approval})[0])
        for value in ("", "0" * 64, manifest["batches"][0]["digest"]):
            with self.subTest(value=value), self.assertRaisesRegex(dispatch.DispatchError, "maintainer-approved"):
                self.apply(manifest, armed_for(value))
        with self.assertRaisesRegex(dispatch.DispatchError, "no approval claim store"):
            dispatch.run(full_command(self.batches, "apply", manifest), project=self.fx.project(apply=True),
                         read=self.fx.read, env=armed_for(approval), budget=self.budget)
        with self.assertRaisesRegex(dispatch.DispatchError, "no rate budget"):
            dispatch.run(full_command(self.batches), project=self.fx.project(), read=self.fx.read, env={})
        self.assertEqual((self.fx.api.mutations, self.claims.log), ([], []))

    def test_stale_later_batch_stops_before_the_first_write_and_ends_the_manifest(self):
        _, manifest, _ = self.check()
        approval = dispatch.manifest_digest(manifest)
        self.fx.issues[635]["labels"].append({"name": "accepted"})
        with self.assertRaisesRegex(dispatch.DispatchError, "batch 3: live evidence differs"):
            self.apply(manifest)
        self.assertEqual(self.fx.api.mutations, [])
        self.assertEqual(self.claims.log, [(dispatch.plan_claim(approval, 1), "pending"),
                                           (dispatch.plan_claim(approval, 1), "failure")])
        self.assertIn("batch 1: REFUSED before any write in this batch", self.published[-1])
        self.fx.issues[635]["labels"].pop()
        with self.assertRaisesRegex(dispatch.DispatchError, "batch 1 already started"):
            self.apply(manifest)
        _, fresh, _ = self.check([[request_row(815, 3, reason="Second check after a stale refusal; reviewed again.")],
                                  *self.batches[1:]])
        self.apply(fresh, batches=[[request_row(815, 3, reason="Second check after a stale refusal; reviewed again.")],
                                   *self.batches[1:]])
        self.assertEqual([self.order_of(n) for n in (815, 509, 635)], [3, 4, 5])

    def test_rank_change_after_project_load_stops_before_the_first_write(self):
        _, manifest, _ = self.check()
        approval = dispatch.manifest_digest(manifest)
        project = self.fx.project(apply=True)
        self.fx.api.items["I635"]["values"][ORDER] = 42
        with (mock.patch.object(dispatch, "publish"),
              self.assertRaisesRegex(t.TriageError, "#635: .* changed since review")):
            dispatch.run(full_command(self.batches, "apply", manifest), project=project,
                         read=self.fx.read, env=armed_for(approval), claims=self.claims,
                         budget=self.budget)
        self.assertEqual(self.fx.api.mutations, [])
        self.assertEqual(self.claims.log[-1], (dispatch.plan_claim(approval, 1), "failure"))

    def test_stale_later_batch_during_apply_stops_and_reports_the_remainder(self):
        _, manifest, _ = self.check()
        approval = dispatch.manifest_digest(manifest)

        def drift(name, variables):
            if variables["item"] == "I509":
                self.fx.dependencies[635] = [{"number": 4, "state": "open"}]

        self.fx.api.before_mutation = drift
        with self.assertRaisesRegex(dispatch.DispatchError, "batch 3: live evidence differs"):
            self.apply(manifest)
        self.assertEqual([self.order_of(n) for n in (815, 509, 635)], [3, 4, None])
        self.assertEqual(self.claims.log[-2:], [(dispatch.plan_claim(approval, 3), "pending"),
                                                (dispatch.plan_claim(approval, 3), "failure")])
        self.assertFalse(self.claims.states([dispatch.batch_claim(manifest["batches"][2]["digest"])]))
        self.assertIn("2 of 3 batches verified; batch 3: REFUSED before any write", self.published[-1])

    def test_failed_write_is_possibly_partial_and_blocks_the_rest(self):
        _, manifest, _ = self.check()
        approval = dispatch.manifest_digest(manifest)

        def fail(name, variables):
            if variables["item"] == "I509":
                raise t.pm.MigrationError("GraphQL error: lost")

        self.fx.api.before_mutation = fail
        with self.assertRaisesRegex(t.pm.MigrationError, "lost"):
            self.apply(manifest)
        self.assertEqual(self.published[-1],
                         f"order full plan; manifest digest {approval}: 1 of 3 batches verified; "
                         "batch 2: FAILED / POSSIBLY PARTIAL; batch 3 not applied")
        failed = {c for c, s in self.claims.log if s == "failure"}
        self.assertEqual(failed, {dispatch.plan_claim(approval, 2),
                                  dispatch.batch_claim(manifest["batches"][1]["digest"])})
        self.fx.api.before_mutation = None
        with self.assertRaisesRegex(dispatch.DispatchError, "already"):
            self.apply(manifest)
        with self.assertRaisesRegex(dispatch.DispatchError, "already used|not succeeded"):
            dispatch.run(plan_command(manifest, 3, self.batches[2], "apply"),
                         project=self.fx.project(apply=True), read=self.fx.read,
                         env=armed_for(approval), claims=self.claims)
        self.assertIsNone(self.order_of(635))

    def test_cancellation_marks_the_running_batch_failed(self):
        _, manifest, _ = self.check()
        approval = dispatch.manifest_digest(manifest)

        def cancel(name, variables):
            if variables["item"] == "I509":
                raise KeyboardInterrupt("signal 15")

        self.fx.api.before_mutation = cancel
        with self.assertRaises(KeyboardInterrupt):
            self.apply(manifest)
        self.assertIn((dispatch.plan_claim(approval, 2), "failure"), self.claims.log)
        self.assertIn("batch 2: FAILED / POSSIBLY PARTIAL", self.published[-1])
        self.assertIsNone(self.order_of(635))

    def test_sigterm_handler_stops_the_run(self):
        with dispatch._cancellable():
            handler = dispatch.signal.getsignal(dispatch.signal.SIGTERM)
            assert callable(handler)
            with self.assertRaises(KeyboardInterrupt):
                handler(15, None)
        self.assertIsNot(dispatch.signal.getsignal(dispatch.signal.SIGTERM), handler)

    def test_revocation_status_stops_before_the_next_batch(self):
        _, manifest, _ = self.check()
        approval = dispatch.manifest_digest(manifest)
        real = self.claims.recent

        def recent(context):
            done = any(c == dispatch.plan_claim(approval, 1) and s == "success" for c, s in self.claims.log)
            if done and context == dispatch.revoke_claim(approval):
                self.claims.log.append((context, "failure"))
            return real(context)

        self.claims.recent = recent
        with self.assertRaisesRegex(dispatch.DispatchError, "revoked this manifest during the run"):
            self.apply(manifest)
        self.assertEqual([self.order_of(n) for n in (815, 509, 635)], [3, None, None])
        self.assertIn((dispatch.plan_claim(approval, 2), "failure"), self.claims.log)

    def test_revocation_during_the_last_budget_wait_stops_the_last_batch(self):
        _, manifest, _ = self.check()
        approval = dispatch.manifest_digest(manifest)
        waits: list[int] = []

        def wait(cost):
            waits.append(cost)
            if len(waits) == 6:
                self.claims.log.append((dispatch.revoke_claim(approval), "failure"))

        self.budget.wait = wait
        with self.assertRaisesRegex(dispatch.DispatchError, "revoked this manifest during the run"):
            self.apply(manifest)
        self.assertEqual([self.order_of(n) for n in (815, 509, 635)], [3, 4, None])
        self.assertIn((dispatch.plan_claim(approval, 3), "failure"), self.claims.log)

    def test_writer_steps_exec_python_so_cancellation_signals_reach_it(self):
        for name, script in (("project3-assignment-dispatch.yml", "project3_assignment_dispatch.py"),
                             ("project3-assignment-request-processor.yml", "project3_assignment_request.py")):
            text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            self.assertIn(f"run: exec python3 scripts/{script}\n", text)

    def test_sigterm_through_an_exec_shell_runs_the_cleanup(self):
        code = ("import sys, time; sys.path.insert(0, 'scripts'); import project3_assignment_dispatch as d\n"
                "with d._cancellable():\n"
                "    try:\n"
                "        print('ready', flush=True); time.sleep(30)\n"
                "    except KeyboardInterrupt:\n"
                "        print('cleanup', flush=True)\n")
        shell = subprocess.Popen(["bash", "-c", 'exec "$0" -c "$1"', sys.executable, code], cwd=ROOT,
                                 stdout=subprocess.PIPE, text=True)
        assert shell.stdout is not None
        self.assertEqual(shell.stdout.readline().strip(), "ready")
        shell.send_signal(signal.SIGTERM)
        output, _ = shell.communicate(timeout=20)
        self.assertEqual(output.strip(), "cleanup")

    def test_revocation_before_start_refuses_every_path_without_claims(self):
        _, manifest, _ = self.check()
        approval = dispatch.manifest_digest(manifest)
        self.claims.log.append((dispatch.revoke_claim(approval), "failure"))
        with self.assertRaisesRegex(dispatch.DispatchError, "revoked this manifest"):
            self.apply(manifest)
        with self.assertRaisesRegex(dispatch.DispatchError, "revoked this manifest"):
            dispatch.run(plan_command(manifest, 1, self.batches[0], "apply"),
                         project=self.fx.project(apply=True), read=self.fx.read,
                         env=armed_for(approval), claims=self.claims)
        self.assertEqual((len(self.claims.log), self.fx.api.mutations), (1, []))

    def test_claims_are_shared_with_single_and_step_paths(self):
        _, manifest, _ = self.check()
        approval = dispatch.manifest_digest(manifest)
        first = manifest["batches"][0]["digest"]
        dispatch.run(order_command(*self.batches[0], operation="apply"), project=self.fx.project(apply=True),
                     read=self.fx.read, env=armed_for(first), claims=self.claims)
        del self.fx.api.items["I815"]["values"][ORDER]
        with self.assertRaisesRegex(dispatch.DispatchError, "batch 1: order digest was already used"):
            self.apply(manifest)
        self.claims.log.clear()
        dispatch.run(plan_command(manifest, 1, self.batches[0], "apply"), project=self.fx.project(apply=True),
                     read=self.fx.read, env=armed_for(approval), claims=self.claims)
        with self.assertRaisesRegex(dispatch.DispatchError, "already"):
            self.apply(manifest)
        self.claims.log.clear()
        del self.fx.api.items["I815"]["values"][ORDER]
        self.apply(manifest)
        del self.fx.api.items["I815"]["values"][ORDER]
        for raw, env in ((order_command(*self.batches[0], operation="apply"), armed_for(first)),
                         (plan_command(manifest, 1, self.batches[0], "apply"), armed_for(approval))):
            with self.subTest(raw=raw[:40]), self.assertRaisesRegex(dispatch.DispatchError, "already"):
                dispatch.run(raw, project=self.fx.project(apply=True), read=self.fx.read, env=env,
                             claims=self.claims)

    def test_budget_refusal_is_read_only_and_a_mid_run_stop_ends_the_manifest(self):
        _, manifest, _ = self.check()
        approval = dispatch.manifest_digest(manifest)
        self.budget.fail_require = "plan needs about 120 Actions-token requests; about 50 fit"
        with self.assertRaisesRegex(dispatch.DispatchError, "plan needs about"):
            self.apply(manifest)
        self.assertEqual((self.claims.log, self.fx.api.mutations), ([], []))
        self.budget = FakeBudget()
        self.budget.fail_wait_after = 4
        with self.assertRaisesRegex(dispatch.DispatchError, "run out before the job deadline"):
            self.apply(manifest)
        self.assertEqual([self.order_of(n) for n in (815, 509, 635)], [3, None, None])
        self.assertEqual(self.claims.log[-1], (dispatch.plan_claim(approval, 2), "failure"))

    def test_malformed_full_plans_are_refused(self):
        _, manifest, _ = self.check()
        other = copy.deepcopy(manifest)
        other["batches"][2]["items"] = [[635, 6]]
        bad = {
            "duplicate issue": full_command([[request_row(815, 3)], [request_row(815, 4)]]),
            "duplicate rank": full_command([[request_row(815, 3)], [request_row(509, 3)]]),
            "big batch": full_command([[request_row(2000 + n, 2000 + n) for n in range(11)]]),
            "too many": full_command([[request_row(2000 + n, 2000 + n)] for n in range(31)]),
            "empty batch": full_command([[]]),
            "no batches": full_command([]),
            "check with manifest": full_command(self.batches, "check", manifest),
            "apply without manifest": full_command(self.batches, "apply"),
            "manifest mismatch": full_command(self.batches, "apply", other),
            "manifest order": full_command([self.batches[1], self.batches[0], self.batches[2]], "apply", manifest),
            "bad row": full_command([[request_row(815, 0)]]),
            "legacy inbox": full_command([[request_row(982, 3)]]),
            "unknown key": json.dumps({"version": 1, "kind": dispatch.FULL_KIND, "operation": "check",
                                       "batches": self.batches, "approved": True}),
        }
        for name, raw in bad.items():
            with self.subTest(name), self.assertRaises(dispatch.DispatchError):
                dispatch.command_kind(raw)

    def test_three_hundred_issues_fit_the_limits_and_the_rest_budget(self):
        fx = BigFixture(300)
        self.fx = fx
        batches = [[request_row(1000 + b * 10 + n, 1 + b * 10 + n,
                                reason=f"Ranked as {b * 10 + n + 1} after the roadmap and PR review on 2026-11-27.")
                    for n in range(10)] for b in range(30)]
        check = full_command(batches)
        self.assertGreater(len(check.encode()), dispatch.MAX_BYTES)
        _, manifest, _ = self.check(batches)
        apply = full_command(batches, "apply", manifest)
        self.assertLess(len(apply.encode()), dispatch.FULL_MAX_BYTES)
        reads = fx.reads
        self.apply(manifest, batches=batches)
        apply_reads = fx.reads - reads
        self.assertEqual([self.order_of(1000 + n) for n in (0, 155, 299)], [1, 156, 300])
        claim_calls = len(self.claims.log) + 2 * 30
        self.assertLessEqual(apply_reads + claim_calls,
                             300 * (dispatch.CHECK_COST_PER_ROW + dispatch.APPLY_COST_PER_ROW)
                             + 30 * dispatch.BATCH_OVERHEAD)
        self.assertEqual(self.budget.required[-1],
                         dispatch.ORDER_CLAIM_PAGES + 300 * 10 + 30 * dispatch.BATCH_OVERHEAD)
        self.assertLessEqual(reads, 300 * dispatch.CHECK_COST_PER_ROW)

    def test_size_limits_stay_per_kind(self):
        rows = [request_row(2000 + n, 2000 + n, reason="x" * 1200) for n in range(10)]
        with self.assertRaisesRegex(dispatch.DispatchError, "oversized command"):
            dispatch.command_kind(order_command(*rows))
        self.assertEqual(dispatch.command_kind(full_command([rows])), (dispatch.FULL_KIND, "check"))
        huge = [[request_row(3000 + b * 10 + n, 3000 + b * 10 + n, reason="y" * 600) for n in range(10)]
                for b in range(30)]
        with self.assertRaisesRegex(dispatch.DispatchError, "oversized full-plan"):
            dispatch.command_kind(full_command(huge))
        env = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REPOSITORY": dispatch.REPOSITORY,
               "GITHUB_REF": "refs/heads/master", "GITHUB_RUN_ATTEMPT": "1"}
        self.assertEqual(dispatch.authenticate(event_for(full_command([rows])), env), full_command([rows]))


class RateBudgetTests(unittest.TestCase):
    def budget(self, cores, now=1000.0, seconds=3 * 3600):
        clock = {"now": now}
        sleeps: list[float] = []

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            clock["now"] += seconds

        budget = dispatch.GitHubRateBudget("t", seconds=seconds, clock=lambda: clock["now"], sleep=sleep)
        values = iter(cores)
        budget._core = lambda: next(values)
        return budget, sleeps

    def test_require_counts_the_windows_left_before_the_deadline(self):
        budget, _ = self.budget([(1000, 200, 2000.0)])
        budget.require(3200)
        budget, _ = self.budget([(1000, 200, 2000.0)])
        with self.assertRaisesRegex(dispatch.DispatchError, "needs about 3201 .* about 3200 fit"):
            budget.require(3201)

    def test_wait_sleeps_until_the_reset_and_rechecks(self):
        budget, sleeps = self.budget([(1000, 10, 1600.0), (1000, 1000, 5200.0)])
        budget.wait(90)
        self.assertEqual(sleeps, [605.0])
        budget, _ = self.budget([(1000, 10, 1600.0), (1000, 20, 5200.0)])
        with self.assertRaisesRegex(dispatch.DispatchError, "still exhausted"):
            budget.wait(90)
        budget, sleeps = self.budget([(1000, 10, 20000.0)])
        with self.assertRaisesRegex(dispatch.DispatchError, "before the job deadline"):
            budget.wait(90)
        self.assertEqual(sleeps, [])

    def test_no_batch_starts_after_the_deadline(self):
        budget, _ = self.budget([(1000, 1000, 5000.0)], now=1000.0, seconds=60)
        budget._clock = lambda: 1061.0
        with self.assertRaisesRegex(dispatch.DispatchError, "deadline passed"):
            budget.wait(1)

    def test_recent_reads_only_the_newest_statuses(self):
        claims = dispatch.GitHubOrderClaims("t", "77")
        context = dispatch.revoke_claim("d" * 64)
        sent = []

        def fake_send(url, payload=None):
            sent.append(url)
            return [{"context": context, "state": "failure"}]

        with mock.patch.object(claims, "_send", side_effect=fake_send):
            self.assertTrue(claims.recent(context))
        self.assertEqual(sent, [f"https://api.github.com/repos/{dispatch.REPOSITORY}/commits/"
                                f"{dispatch.ORDER_CLAIM_SHA}/statuses?per_page=100&page=1"])
        with self.assertRaisesRegex(dispatch.DispatchError, "invalid"):
            claims.mark(context, "failure")


class FullPlanRelayTests(unittest.TestCase):
    def test_relay_checks_a_large_full_plan_from_the_request_file(self):
        fx = BigFixture(80)
        batches = [[request_row(1000 + b * 10 + n, 1 + b * 10 + n,
                                reason="Ranked after the roadmap and open pull request review today.")
                    for n in range(10)] for b in range(8)]
        command = full_command(batches)
        self.assertGreater(len(command.encode()), dispatch.MAX_BYTES)
        rest = RelayREST(command)
        budget = FakeBudget()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "event.json"
            path.write_text(json.dumps(event()), encoding="utf-8")
            env = {**environment(), "GITHUB_EVENT_PATH": str(path),
                   "GITHUB_TOKEN": fake_token("actions"), "PROJECT_SYNC_TOKEN": fake_token("project"),
                   "GITHUB_RUN_ID": "9007", "PROJECT3_REQUEST_RULESET_ID": "1234",
                   "PROJECT3_REQUEST_RULESET_UPDATED_AT": "2026-10-30T15:00:00Z"}
            with (mock.patch.dict(os.environ, env, clear=True),
                  mock.patch.object(relay, "provenance_client", return_value=rest),
                  mock.patch.object(relay.triage, "rest_client", return_value=fx.read),
                  mock.patch.object(relay.pm, "http_transport", return_value=fx.api),
                  mock.patch.object(relay, "create_status") as claims,
                  mock.patch.object(relay.dispatch, "GitHubRateBudget", return_value=budget),
                  mock.patch.object(relay.dispatch, "publish"),
                  mock.patch.object(relay.dispatch, "report") as audit):
                code = relay.main()
        self.assertEqual((code, [c.args[2] for c in claims.call_args_list]), (0, ["pending", "success"]))
        self.assertIn("CHECK ONLY", audit.call_args.args[0])
        self.assertEqual((fx.api.mutations, len(budget.waited)), ([], 8))


if __name__ == "__main__":
    unittest.main()
