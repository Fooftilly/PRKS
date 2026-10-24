"""Tests for the read-only dependency change classification."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.dependency_pr_report import RULES, changed_paths, classify, render


class ImpactReportTests(unittest.TestCase):
    def test_runtime_and_vue(self) -> None:
        selected = classify({"requirements.txt", "frontend-app/package-lock.json",
                             "frontend/vue/prks-vue.js"})
        self.assertEqual([x[0] for x in selected], ["Python runtime", "Vue app"])
        self.assertTrue(selected[1][3])

    def test_missing_generated_assets_are_advisory_only(self) -> None:
        output = render({"tools/research-graph/package-lock.json"})
        self.assertIn("npm run build --prefix tools/research-graph", output)
        self.assertIn("No derived assets in diff", output)

    def test_workflow_changes_select_lint(self) -> None:
        self.assertIn("actionlint", render({".github/workflows/codeql.yml"}))

    def test_non_dependency_change_does_not_claim_approval(self) -> None:
        output = render({"backend/work_identity.py"})
        self.assertIn("No tracked dependency scope", output)
        self.assertIn("not proof", output)

    @patch("scripts.dependency_pr_report.subprocess.run")
    def test_refuses_non_merge_checkout(self, run) -> None:
        run.return_value.stdout = "a" * 40 + " " + "b" * 40 + "\\n"
        with self.assertRaisesRegex(ValueError, "two-parent PR merge"):
            changed_paths()
        self.assertEqual(run.call_count, 1)

    @patch("scripts.dependency_pr_report.subprocess.run")
    def test_diff_tracks_first_parent_of_merge_not_stale_event_base(self, run) -> None:
        sha = "a" * 40
        actual_base = "b" * 40
        head = "c" * 40
        from unittest.mock import Mock
        run.side_effect = [
            Mock(stdout=f"{sha} {actual_base} {head}\\n"),
            Mock(stdout=b"requirements.txt\\0frontend-app/package.json\\0"),
        ]
        result = changed_paths()
        self.assertEqual(result, {"requirements.txt", "frontend-app/package.json"})
        self.assertEqual(run.call_args.args[0], [
            "git", "diff", "--name-only", "-z", actual_base, "HEAD", "--"
        ])
        self.assertTrue(run.call_args.kwargs["check"])

    def test_advised_npm_commands_refer_to_tracked_build_scripts(self) -> None:
        for _name, _patterns, _checks, command in (
            item for item in RULES
            if item[0] in {"Vue app", "Frontend vendor", "PDF viewer", "Research graph"}
        ):
            self.assertIn("npm ci ", command)
            self.assertIn("npm run build --prefix ", command)
            self.assertNotIn("refresh_dependency_artifacts.sh", command)


if __name__ == "__main__":
    unittest.main()
