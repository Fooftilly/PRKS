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


def _step_items(job: str) -> list[tuple[str | None, str]]:
    """Split at every top-level YAML list item, including unnamed steps.

    Embedded shell comments and strings must not count as YAML metadata.
    """
    starts = list(re.finditer(r"(?m)^      - ", job))
    items: list[tuple[str | None, str]] = []
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(job)
        block = job[match.end():end]
        first = block.split("\n", 1)[0]
        name = re.fullmatch(r"name: ([^\n]+)", first)
        items.append((name.group(1) if name else None, block))
    return items


def _named_step(job: str, name: str) -> str:
    matches = [body for actual, body in _step_items(job) if actual == name]
    if len(matches) != 1:
        raise AssertionError(f"Expected one Vue step named {name!r}")
    return matches[0]


def _step_guards(step: str) -> list[str]:
    """Read only real step-level if fields, never comments or run text."""
    return re.findall(r"(?m)^        if: ([^\n]+)$", step)


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

        self.assertEqual(_step_guards(_named_step(job, "Checkout repository")), [])
        self.assertEqual(
            _step_guards(_named_step(job, "Classify documentation-only PR (fail closed)")),
            [],
        )
        for name in VUE_HEAVY_STEPS:
            with self.subTest(name=name):
                self.assertEqual(
                    _step_guards(_named_step(job, name)),
                    ["steps.docs-scope.outputs.docs_only != 'true'"],
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
        expected = [
            "Checkout repository",
            "Classify documentation-only PR (fail closed)",
            *VUE_HEAVY_STEPS,
        ]
        # Count EVERY YAML step, including unnamed runs/uses.
        self.assertEqual([name for name, _body in _step_items(job)], expected)

    def test_guard_must_be_yaml_field_not_comment_or_shell_string(self):
        job = STATIC.read_text(encoding="utf-8").split("\n  vue:\n", 1)[1]
        guard = "        if: steps.docs-scope.outputs.docs_only != 'true'"
        sample = _named_step(job, "Vitest")
        self.assertEqual(
            _step_guards(sample),
            ["steps.docs-scope.outputs.docs_only != 'true'"],
        )
        self.assertEqual(
            _step_guards(sample.replace(guard, "        # " + guard.strip())), []
        )
        self.assertEqual(
            _step_guards(sample.replace(guard, "          " + guard.strip())), []
        )

    def test_unnamed_run_and_uses_steps_are_not_hidden(self):
        job = STATIC.read_text(encoding="utf-8").split("\n  vue:\n", 1)[1]
        for extra in (
            "      - run: npm run typecheck --prefix frontend-app\n",
            "      - uses: actions/setup-node@untrusted\n",
        ):
            with self.subTest(extra=extra.strip()):
                items = _step_items(job + extra)
                self.assertIsNone(items[-1][0])
                self.assertEqual(len(items), len(VUE_HEAVY_STEPS) + 3)


if __name__ == "__main__":
    unittest.main()
