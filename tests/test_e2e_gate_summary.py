"""Regression tests for the stable-name E2E required check's coverage summary."""
from __future__ import annotations

import unittest
from pathlib import Path

from tests.e2e.ci_gate_summary import format_e2e_gate_summary


ROOT = Path(__file__).resolve().parents[1]


class E2EGateSummaryTests(unittest.TestCase):
    def test_affected_gate_success_does_not_claim_full_coverage(self):
        report = format_e2e_gate_summary({
            "PLAN_RUN": "true", "PLAN_MODE": "affected", "PLAN_RESULT": "success",
            "PLAN_COUNT": "626", "PLAN_FEATURES": '["pdf","smoke"]',
            "PLAN_POINTER": "true", "E2E_RESULT": "success",
            "POINTER_RESULT": "success",
        }, passed=True)
        self.assertIn("Affected subset only — NOT the full E2E suite", report)
        self.assertIn("| Planned test IDs | 626 |", report)
        self.assertIn("`pdf`, `smoke`", report)
        self.assertIn("**Aggregate result:** PASS", report)
        self.assertIn("**not** independently observed", report)

    def test_full_selection_is_explicit_and_is_not_a_pass_count(self):
        report = format_e2e_gate_summary({
            "PLAN_RUN": "true", "PLAN_MODE": "full", "PLAN_RESULT": "success",
            "PLAN_COUNT": "847", "PLAN_POINTER": "true",
            "E2E_RESULT": "failure", "POINTER_RESULT": "success",
        }, passed=False)
        self.assertIn("Full discovered E2E suite selected", report)
        self.assertIn("| Browser shard matrix | failure |", report)
        self.assertIn("**Aggregate result:** FAIL", report)
        self.assertIn("confirm actual execution", report)

    def test_skipped_gate_is_not_reported_as_browser_coverage(self):
        report = format_e2e_gate_summary({
            "PLAN_RUN": "false", "PLAN_MODE": "skip", "PLAN_RESULT": "success",
            "PLAN_COUNT": "0", "PLAN_POINTER": "false",
            "E2E_RESULT": "skipped", "POINTER_RESULT": "skipped",
        }, passed=True)
        self.assertIn("Skipped — no browser E2E tests selected", report)
        self.assertIn("| Pointer capture planned | no |", report)
        self.assertIn("**Aggregate result:** PASS", report)

    def test_failed_plan_does_not_exaggerate_full_suite_execution(self):
        report = format_e2e_gate_summary({
            "PLAN_RUN": "true", "PLAN_MODE": "full", "PLAN_RESULT": "failure",
            "PLAN_COUNT": "847",
        }, passed=False)
        self.assertIn("Unverified: planner did not succeed", report)
        self.assertNotIn("Full discovered E2E suite selected", report)

    def test_inconsistent_run_and_mode_is_unverified(self):
        report = format_e2e_gate_summary({
            "PLAN_RUN": "false", "PLAN_MODE": "full", "PLAN_RESULT": "success",
        }, passed=True)
        self.assertIn("inconsistent or unknown selection", report)

    def test_whitespace_is_not_normalized_into_a_valid_plan(self):
        report = format_e2e_gate_summary({
            "PLAN_RUN": "true", "PLAN_MODE": "full\\n",
            "PLAN_RESULT": "success", "PLAN_POINTER": "true\\n",
            "E2E_RESULT": "success",
        }, passed=False)
        self.assertIn("Unverified: inconsistent or unknown selection", report)
        self.assertIn("| Pointer capture planned | unknown |", report)

    def test_untrusted_plan_metadata_cannot_inject_markdown(self):
        report = format_e2e_gate_summary({
            "PLAN_RUN": "true", "PLAN_MODE": "affected", "PLAN_RESULT": "success",
            "PLAN_COUNT": "1\n## forged success",
            "PLAN_FEATURES": '["pdf", "bad` | forged"]',
            "PLAN_REASON": "\n# injected heading\n",
            "POINTER_RESULT": "success\n| fake | passed |",
            "PLAN_POINTER": "true\n| injected flag |",
        }, passed=False)
        self.assertNotIn("forged", report)
        self.assertNotIn("injected", report)
        self.assertIn("| Planned test IDs | unknown |", report)
        self.assertIn("| Selected feature groups | unavailable |", report)
        self.assertIn("| Pointer capture planned | unknown |", report)
        self.assertIn("| Pointer job | unknown |", report)

    def test_aggregate_runner_writes_summary_without_changing_required_check(self):
        runner = (ROOT / "tests/e2e/run.py").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/e2e-gate.yml").read_text(encoding="utf-8")
        self.assertIn("format_e2e_gate_summary(os.environ, passed=ok)", runner)
        self.assertIn('name: Full E2E gate', workflow)
        self.assertIn('run: python tests/e2e/run.py --ci-aggregate', workflow)


if __name__ == "__main__":
    unittest.main()
