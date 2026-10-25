"""Ensure the required Vue static context stays present for audited docs-only PRs."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / ".github/workflows/static-analysis.yml"
TEST_GATE = ROOT / ".github/workflows/test-gate.yml"

VUE_HEAVY_STEPS = (
    "Set up Node.js",
    "Install Vue toolchain from the lockfile",
    "OpenAPI TypeScript types match checked-in artifacts",
    "Vue/TypeScript ESLint",
    "Typecheck",
    "Vitest",
    "Production build matches committed assets",
    "Storybook build when frontend tooling changes",
)


def _named_step(job: str, name: str) -> str:
    match = re.search(
        r"(?ms)^      - name: " + re.escape(name) + r"\n"
        r"(.*?)(?=^      - name: |\Z)",
        job,
    )
    if match is None:
        raise AssertionError(f"Missing Vue step: {name}")
    return match.group(1)


class DocsOnlyVueGateTests(unittest.TestCase):
    def test_required_vue_job_remains_reported_and_heavy_steps_are_guarded(self):
        workflow = STATIC.read_text(encoding="utf-8")
        job = workflow.split("\n  vue:\n", 1)[1]
        self.assertIn("name: Vue typecheck, test, and build", job)
        self.assertIn("runs-on: ubuntu-latest", job)
        self.assertNotRegex(job, r"(?m)^    if:")
        self.assertIn("on:\n  push:", workflow)
        self.assertIn("  pull_request:", workflow)
        self.assertIn("  workflow_dispatch:", workflow)

        self.assertNotIn("if:", _named_step(job, "Checkout repository"))
        for name in VUE_HEAVY_STEPS:
            with self.subTest(name=name):
                self.assertIn(
                    "if: steps.docs-scope.outputs.docs_only != 'true'",
                    _named_step(job, name),
                )
        self.assertEqual(
            job.count("if: steps.docs-scope.outputs.docs_only != 'true'"),
            len(VUE_HEAVY_STEPS),
        )

    def test_classifier_uses_exact_same_reviewed_base_sha_selection(self):
        static = STATIC.read_text(encoding="utf-8")
        unit = TEST_GATE.read_text(encoding="utf-8")
        job = static.split("\n  vue:\n", 1)[1]
        start = "      - name: Classify documentation-only PR (fail closed)\n"
        end_unit = "      - name: Set up Python for documentation-only PR\n"
        end_vue = "      - name: Set up Node.js\n"
        base_classifier = unit.split(start, 1)[1].split(end_unit, 1)[0]
        vue_classifier = job.split(start, 1)[1].split(end_vue, 1)[0]
        self.assertEqual(vue_classifier, base_classifier)

        step = _named_step(job, "Classify documentation-only PR (fail closed)")
        self.assertIn("id: docs-scope", step)
        self.assertIn('git show "$PRKS_PR_BASE_SHA:scripts/ci/docs_only.py"', step)
        self.assertIn('if base_classifier="$(mktemp)"; then', step)
        self.assertIn('echo "docs_only=$docs_only" >> "$GITHUB_OUTPUT"', step)
        self.assertIn("PRKS_EVENT_NAME", step)
        self.assertNotIn("python -m scripts.ci.docs_only", step)

    def test_every_vue_step_is_explicitly_expected(self):
        static = STATIC.read_text(encoding="utf-8")
        job = static.split("\n  vue:\n", 1)[1]
        found = re.findall(r"(?m)^      - name: ([^\n]+)$", job)
        self.assertEqual(
            found,
            [
                "Checkout repository",
                "Classify documentation-only PR (fail closed)",
                *VUE_HEAVY_STEPS,
            ],
        )


if __name__ == "__main__":
    unittest.main()
