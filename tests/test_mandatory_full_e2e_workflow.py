"""Regress CI E2E coverage for frontend-impacting PRs without overtesting docs."""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests.e2e import policy


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/e2e-gate.yml"


class FrontendCISelectionTests(unittest.TestCase):
    def test_full_for_browser_source_and_frontend_build_dependencies(self):
        for changed in (
            "frontend/js/components/folders.js",
            "frontend/js/app.js",
            "frontend/sw.js",
            "frontend/vue/BUILD-MANIFEST.json",
            "frontend-app/src/features/works/WorkDetails.vue",
            "frontend-app/package-lock.json",
            "frontend-app/vite.config.ts",
            "tools/frontend-vendor/package-lock.json",
            "tools/research-graph/package-lock.json",
        ):
            with self.subTest(changed=changed):
                self.assertTrue(policy.frontend_browser_change_requires_full_ci([changed]))

    def test_unrelated_and_maintainer_only_paths_do_not_force_full(self):
        for changed in (
            "README.md",
            "docs/wiki/testing.md",
            ".github/workflows/codeql.yml",
            ".github/dependabot.yml",
            "backend/db/schema/snapshot.json",
            "requirements.txt",
            "tests/test_e2e_policy.py",
            "frontend/AGENTS.md",
            "frontend/vue/THIRD_PARTY_LICENSES.md",
            "frontend-app/.storybook/main.ts",
            "frontend-app/src/features/works/WorkDetails.stories.ts",
            "frontend-app/src/features/works/WorkDetails.test.ts",
            "tools/pdf-viewer/package-lock.json",
        ):
            with self.subTest(changed=changed):
                self.assertFalse(policy.frontend_browser_change_requires_full_ci([changed]))

    def test_workflow_keeps_diff_selection_but_requests_full_for_frontend(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('PRKS_E2E_CI_FRONTEND_FULL: "1"', source)
        self.assertIn('plan="$(python tests/e2e/run.py --ci-plan --base "$base")"', source)
        self.assertIn('if [[ "${{ steps.base.outputs.force }}" == "1" ]]; then', source)
        self.assertIn("needs: [e2e-plan, e2e, e2e-pointer]", source)

    @staticmethod
    def _plan(paths: list[str]) -> dict:
        # The production workflow provides this flag only to its plan job.
        from tests.e2e import run as runner

        output = io.StringIO()
        with mock.patch.dict(
            os.environ,
            {
                "PRKS_E2E_CI_FRONTEND_FULL": "1",
                "PRKS_E2E_CI_FORCE_FULL": "",
                "PRKS_E2E": "1",
            },
        ):
            with mock.patch.object(runner, "list_changed_paths", return_value=paths):
                with redirect_stdout(output):
                    result = runner.main(["--ci-plan", "--base", "origin/master"])
        if result != 0:
            raise AssertionError(f"CI planning failed: {result}")
        return json.loads(output.getvalue().strip())

    def test_frontend_feature_change_uses_all_six_shards_and_pointer(self):
        # Previously this mapped feature was affected-only; CI now forces full.
        plan = self._plan(["frontend/js/components/folders.js"])
        self.assertEqual(plan["mode"], "full")
        self.assertTrue(plan["run"])
        self.assertTrue(plan["pointer_capture"])
        self.assertEqual(
            plan["matrix"]["include"],
            policy.ci_matrix_include(policy.FULL_GATE_EXTERNAL_SHARDS, 1),
        )
        self.assertIn("frontend-affecting", plan["reason"])

    def test_docs_and_unrelated_workflows_can_still_skip(self):
        for changed in ("docs/wiki/testing.md", ".github/workflows/codeql.yml"):
            with self.subTest(changed=changed):
                plan = self._plan([changed])
                self.assertEqual(plan["mode"], "skip")
                self.assertFalse(plan["run"])

    def test_same_change_without_ci_flag_still_uses_affected_mode(self):
        self.assertEqual(
            policy.plan_ci_e2e(["frontend/js/components/folders.js"])["mode"],
            "affected",
        )


if __name__ == "__main__":
    unittest.main()
