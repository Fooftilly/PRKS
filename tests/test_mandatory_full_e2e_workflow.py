"""Guard the mandatory full E2E workflow without changing local affected selection."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests.e2e import policy


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github/workflows/e2e-gate.yml"


class MandatoryFullE2EWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_every_master_pr_triggers_without_path_filters(self):
        self.assertRegex(
            self.source, r"(?m)^  pull_request:\n    branches:\n      - master\s*$"
        )
        event = self.source.split("  pull_request:\n", 1)[1].split(
            "\n  workflow_dispatch:", 1
        )[0]
        self.assertNotIn("paths:", event)
        self.assertNotIn("paths-ignore:", event)

    def test_required_ci_forces_full_regardless_of_diff(self):
        plan_step = self.source.split("      - name: Decide E2E gate mode\n", 1)[1].split(
            "\n  e2e:", 1
        )[0]
        self.assertIn('PRKS_E2E_CI_FORCE_FULL: "1"', plan_step)
        self.assertIn('plan="$(python tests/e2e/run.py --ci-plan)"', plan_step)
        self.assertNotIn("--ci-plan --base", plan_step)
        self.assertNotIn("steps.base.outputs", plan_step)
        self.assertNotRegex(plan_step, r"(?m)^\s+if: ")
        self.assertIn('if mode != "full" or plan["run"] is not True:', plan_step)
        self.assertIn('if plan["pointer_capture"] is not True:', plan_step)
        self.assertIn('if include != ci_matrix_include(FULL_GATE_EXTERNAL_SHARDS, 1):', plan_step)

    def test_full_plan_has_all_six_shards_and_pointer_for_all_pr_types(self):
        # The existing local affected/skip planner remains useful. Required
        # CI must force a full plan even for those normally skipped paths.
        for paths in (
            ["README.md"],
            [".github/dependabot.yml"],
            [".github/workflows/codeql.yml"],
            ["frontend-app/package-lock.json"],
            ["requirements-dev.txt"],
            ["tests/test_e2e_policy.py"],
            [],
        ):
            with self.subTest(paths=paths):
                plan = policy.plan_ci_e2e(paths, force_full=True)
                self.assertTrue(plan["run"])
                self.assertEqual(plan["mode"], "full")
                self.assertEqual(plan["features"], [])
                self.assertTrue(plan["pointer_capture"])
                self.assertEqual(
                    policy.ci_matrix_include(plan["external_shards"], plan["local_jobs"]),
                    policy.ci_matrix_include(policy.FULL_GATE_EXTERNAL_SHARDS, 1),
                )

    def test_local_affected_selection_remains_available(self):
        self.assertEqual(policy.plan_ci_e2e(["README.md"])["mode"], "skip")
        self.assertEqual(
            policy.plan_ci_e2e(["frontend/js/components/folders.js"])["mode"],
            "affected",
        )

    def test_aggregate_still_requires_every_shard_and_pointer(self):
        aggregator = self.source.split("\n  e2e-result:", 1)[1]
        self.assertIn("needs: [e2e-plan, e2e, e2e-pointer]", aggregator)
        self.assertIn("python tests/e2e/run.py --ci-aggregate", aggregator)


if __name__ == "__main__":
    unittest.main()
