"""Tests for the read-only dependency change classification."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.dependency_pr_report import changed_paths, classify, render


class ImpactReportTests(unittest.TestCase):
    def test_runtime_and_vue(self) -> None:
        selected = classify({"requirements.txt", "frontend-app/package-lock.json",
                             "frontend/vue/prks-vue.js"})
        self.assertEqual([x[0] for x in selected], ["Python runtime", "Vue app"])
        self.assertTrue(selected[1][3])

    def test_missing_generated_assets_are_advisory_only(self) -> None:
        output = render({"tools/research-graph/package-lock.json"})
        self.assertIn("refresh_dependency_artifacts.sh research-graph", output)
        self.assertIn("No derived assets in diff", output)

    def test_workflow_changes_select_lint(self) -> None:
        self.assertIn("actionlint", render({".github/workflows/codeql.yml"}))

    def test_non_dependency_change_does_not_claim_approval(self) -> None:
        output = render({"backend/work_identity.py"})
        self.assertIn("No tracked dependency scope", output)
        self.assertIn("not proof", output)

    def test_base_sha_is_validated(self) -> None:
        with self.assertRaises(ValueError):
            changed_paths("not-a-sha")

    @patch("scripts.dependency_pr_report.subprocess.run")
    def test_git_diff_is_argv_not_shell(self, run) -> None:
        run.return_value.stdout = b"requirements.txt\\0frontend-app/package.json\\0"
        result = changed_paths("a" * 40)
        self.assertEqual(result, {"requirements.txt", "frontend-app/package.json"})
        self.assertEqual(run.call_args.args[0][:4], ["git", "diff", "--name-only", "-z"])
        self.assertTrue(run.call_args.kwargs["check"])


if __name__ == "__main__":
    unittest.main()
