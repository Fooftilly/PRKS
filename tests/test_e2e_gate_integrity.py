"""Fail-closed required E2E check when its planner outputs are inconsistent.

These tests are browser-free: job conclusions are synthetic, while the
decision is the same pure function and CLI path used in GitHub Actions.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.e2e import policy


_FULL = {
    "plan_run": True,
    "plan_mode": "full",
    "plan_pointer": True,
    "plan_result": "success",
    "e2e_result": "success",
    "pointer_result": "success",
}
_SKIP = {
    "plan_run": False,
    "plan_mode": "skip",
    "plan_pointer": False,
    "plan_result": "success",
    "e2e_result": "skipped",
    "pointer_result": "skipped",
}
_AFFECTED = {
    **_FULL,
    "plan_mode": "affected",
    "plan_pointer": False,
    "pointer_result": "skipped",
}


class E2EGateIntegrityTests(unittest.TestCase):
    def check_failed(self, plan, **changes):
        result, reason = policy.aggregate_ci_gate_outcome(**(plan | changes))
        self.assertFalse(result, (changes, reason))
        self.assertTrue(reason)

    def test_valid_full_affected_and_docs_skip_are_preserved(self):
        for plan in (_FULL, _AFFECTED, _SKIP, {
            **_AFFECTED, "plan_pointer": True, "pointer_result": "success"
        }):
            with self.subTest(mode=plan["plan_mode"], pointer=plan["plan_pointer"]):
                result, reason = policy.aggregate_ci_gate_outcome(**plan)
                self.assertTrue(result, reason)

    def test_skip_cannot_hide_unscheduled_full_or_affected_matrix(self):
        for mode in ("full", "affected"):
            with self.subTest(mode=mode):
                self.check_failed(
                    _SKIP, plan_mode=mode, plan_run=False,
                )

    def test_run_true_must_not_approve_a_skip_plan(self):
        self.check_failed(_SKIP, plan_run=True)

    def test_skipped_plan_requires_skipped_shards_and_pointer(self):
        self.check_failed(_SKIP, e2e_result="failure")
        self.check_failed(_SKIP, e2e_result="success")
        self.check_failed(_SKIP, pointer_result="failure")
        self.check_failed(_SKIP, pointer_result="success")
        self.check_failed(_SKIP, plan_pointer=True)

    def test_full_mode_cannot_omit_pointer_capture(self):
        self.check_failed(_FULL, plan_pointer=False, pointer_result="skipped")
        self.check_failed(_FULL, plan_pointer=False)

    def test_unplanned_pointer_success_cannot_be_mistaken_for_a_valid_gate(self):
        self.check_failed(_AFFECTED, pointer_result="success")

    def test_unknown_and_malformed_planner_decisions_fail(self):
        for patch in (
            {"plan_mode": ""},
            {"plan_mode": "garbage"},
            {"plan_run": None},
            {"plan_pointer": None},
            {"plan_run": 0},
            {"plan_pointer": 1},
            {"plan_result": "failure"},
        ):
            with self.subTest(patch=patch):
                self.check_failed(_FULL, **patch)

    def test_cli_rejects_missing_and_noncanonical_boolean_outputs(self):
        from tests.e2e import run as runner

        env = {
            "PLAN_RUN": "true",
            "PLAN_MODE": "full",
            "PLAN_POINTER": "true",
            "PLAN_RESULT": "success",
            "E2E_RESULT": "success",
            "POINTER_RESULT": "success",
        }
        with tempfile.TemporaryDirectory() as folder:
            summary = Path(folder) / "summary.md"
            for name, invalid in (("PLAN_RUN", None), ("PLAN_RUN", "yes"),
                                  ("PLAN_POINTER", None), ("PLAN_POINTER", "1"),
                                  ("PLAN_MODE", None), ("PLAN_MODE", "skip")):
                with self.subTest(name=name, invalid=invalid):
                    corrupted = dict(env, GITHUB_STEP_SUMMARY=str(summary))
                    if invalid is None:
                        corrupted.pop(name)
                    else:
                        corrupted[name] = invalid
                    with mock.patch.dict(os.environ, corrupted, clear=True):
                        self.assertEqual(runner.main(["--ci-aggregate"]), 1)
                    self.assertIn("**Aggregate result:** FAIL", summary.read_text())
                    summary.unlink()

            with mock.patch.dict(os.environ, dict(env, GITHUB_STEP_SUMMARY=str(summary)), clear=True):
                self.assertEqual(runner.main(["--ci-aggregate"]), 0)
            self.assertIn("**Aggregate result:** PASS", summary.read_text())

    def test_workflow_rejects_corrupt_decision_before_scheduling_jobs(self):
        source = (Path(__file__).resolve().parents[1]
                  / ".github/workflows/e2e-gate.yml").read_text(encoding="utf-8")
        self.assertIn('if (mode == "skip") != (not plan["run"]):', source)
        self.assertIn('if mode == "full" and not plan["pointer_capture"]:', source)
        self.assertIn('if mode == "skip" and plan["pointer_capture"]:', source)
        self.assertIn('if mode == "skip" and include:', source)
        self.assertIn('name: Full E2E gate', source)
        self.assertIn('run: python tests/e2e/run.py --ci-aggregate', source)


if __name__ == "__main__":
    unittest.main()
