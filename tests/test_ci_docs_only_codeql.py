"""CodeQL required contexts must report success on vetted docs-only PRs (#643).

Structural guard: exact YAML step declarations and immutable-base classifier.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODEQL = ROOT / ".github/workflows/codeql.yml"
UNIT = ROOT / ".github/workflows/test-gate.yml"
CLASSIFIER = "Classify documentation-only PR (fail closed)"
HEAVY = ("Initialize CodeQL", "Perform CodeQL Analysis")
CONDITION = "steps.docs-scope.outputs.docs_only != 'true'"


def _steps(text: str) -> list[tuple[str | None, str]]:
    starts = list(re.finditer(r"(?m)^      -(?:[ \t]+|(?=\r?$))", text))
    result = []
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        body = text[match.start():end]
        found = re.search(r"(?m)^        name: ([^\n]+)$|^      - name: ([^\n]+)$", body)
        name = (found.group(1) or found.group(2)) if found else None
        result.append((name, body))
    return result


def _analyze(source: str) -> str:
    match = re.search(r"(?ms)^  analyze:\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)", source)
    if match is None:
        raise AssertionError("Required analyze job missing")
    return match.group(1)


def _step(job: str, name: str) -> str:
    found = [body for actual, body in _steps(job) if actual == name]
    if len(found) != 1:
        raise AssertionError("Expected one step: " + name)
    return found[0]


def _guards(step: str) -> list[str]:
    return re.findall(r"(?m)^        if: ([^\n]+)$", step)


class CodeQLDocsOnlyCIContractTests(unittest.TestCase):
    def test_keep_all_required_context_names_and_event_triggers(self):
        source = CODEQL.read_text(encoding="utf-8")
        for trigger in ("push", "pull_request", "workflow_dispatch", "schedule"):
            self.assertRegex(source, r"(?m)^  " + trigger + r":\s*$")
        # Restrict trigger-filter checks to the `on:` event section:
        # CodeQL's own `init.with.config` intentionally contains a
        # `paths-ignore:` key for vendored source, not event filtering.
        events = source.split("\non:\n", 1)[1].split("\nconcurrency:\n", 1)[0]
        self.assertNotRegex(events, r"(?m)^\s+paths(?:-ignore)?:\s*$")
        self.assertIn("            paths-ignore:\n              - frontend/vendor/**", source)
        # A workflow-level filter inside pull_request must still fail.
        filtered_events = events.replace(
            "  pull_request:\n    branches:\n",
            "  pull_request:\n    paths-ignore:\n      - backend/**\n    branches:\n",
        )
        self.assertRegex(filtered_events, r"(?m)^\s+paths(?:-ignore)?:\s*$")
        job = _analyze(source)
        self.assertIn("name: Analyze ($"+"{{ matrix.language }})", job)
        self.assertIn("runs-on: ubuntu-latest", job)
        self.assertNotRegex(job, r"(?m)^    if:")
        for language in ("python", "javascript-typescript", "actions"):
            self.assertIn("- language: " + language, job)
        self.assertIn("security-events: write", job)
        self.assertIn("fail-fast: false", job)

    def test_only_codeql_actions_are_guarded_and_all_steps_remain(self):
        job = _analyze(CODEQL.read_text(encoding="utf-8"))
        steps = _steps(job)
        self.assertEqual([name for name, _ in steps],
                         ["Checkout repository", CLASSIFIER, *HEAVY])
        self.assertEqual(_guards(_step(job, "Checkout repository")), [])
        self.assertEqual(_guards(_step(job, CLASSIFIER)), [])
        self.assertIn("persist-credentials: false", _step(job, "Checkout repository"))
        for name in HEAVY:
            with self.subTest(name=name):
                self.assertEqual(_guards(_step(job, name)), [CONDITION])
        # A green CodeQL check with upload disabled is not a security scan.
        analyze = _step(job, "Perform CodeQL Analysis")
        self.assertNotRegex(analyze, r"(?m)^\s+upload:\s*never\s*$")
        self.assertIn('category: "/language:', analyze)

    def test_classifier_matches_immutable_base_unit_gate(self):
        codeql = _analyze(CODEQL.read_text(encoding="utf-8"))
        unit = UNIT.read_text(encoding="utf-8")
        unit_job = unit.split("  unit-api-contract:\n", 1)[1].split(
            "  postgres-helpers:\n", 1
        )[0]
        expected = _step(unit_job, CLASSIFIER)
        actual = _step(codeql, CLASSIFIER)
        self.assertEqual(actual, expected)
        self.assertIn('git show "$PRKS_PR_BASE_SHA:scripts/ci/docs_only.py"', actual)
        self.assertIn('if base_classifier="$(mktemp)"; then', actual)
        self.assertIn('echo "docs_only=$docs_only" >> "$GITHUB_OUTPUT"', actual)
        self.assertIn('"$PRKS_EVENT_NAME" == "pull_request"', actual)
        self.assertIn("docs_only=false", actual)
        self.assertNotIn("python -m scripts.ci.docs_only", actual)

    def test_guard_field_not_comment_or_shell_string_and_no_extra_steps(self):
        job = _analyze(CODEQL.read_text(encoding="utf-8"))
        init = _step(job, "Initialize CodeQL")
        guard = "        if: " + CONDITION
        self.assertEqual(_guards(init.replace(guard, "        # " + guard.strip())), [])
        self.assertEqual(_guards(init.replace(guard, "          " + guard.strip())), [])
        for extra in (
            "      - run: python check.py\n",
            "      - uses: github/codeql-action/init@untrusted\n",
            "      -\n        run: python check.py\n",
            "      -\n        name: Unconditional work\n        run: echo unsafely-run\n",
        ):
            with self.subTest(extra=extra):
                self.assertNotEqual(
                    [name for name, _ in _steps(job + extra)],
                    ["Checkout repository", CLASSIFIER, *HEAVY],
                )


if __name__ == "__main__":
    unittest.main()
