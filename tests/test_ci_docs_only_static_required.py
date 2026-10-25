"""Keep Pyright/ESLint required contexts while skipping only audited wiki PRs.

This is a structural regression guard, not a substitute for running real CI:
it rejects removed required jobs, extra (including unnamed) work, missing real
step-level guards, or same-tree divergence from the required unit classifier.
The runtime classifier itself is read from the immutable PR base by all jobs.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / ".github/workflows/static-analysis.yml"
UNIT = ROOT / ".github/workflows/test-gate.yml"
CONDITION = "steps.docs-scope.outputs.docs_only != 'true'"

HEAVY = {
    "pyright": (
        "Set up Node.js",
        "Set up Python for PostgreSQL and identity type imports",
        "Install pinned PostgreSQL and identity type dependencies",
        "Run Pyright data-flow checks",
        "Run Pyright typed slice (backend/storage + backend utility modules; basic)",
    ),
    "eslint": (
        "Set up Node.js",
        "Run high-signal JavaScript checks",
    ),
}
REQUIRED = {
    "pyright": "Pyright data-flow + typed storage slice",
    "eslint": "ESLint bug checks",
}


def _job(source: str, name: str) -> str:
    match = re.search(
        r"(?ms)^  " + re.escape(name) + r":\n"
        r"(.*?)(?=^  [a-z][a-z-]*:\n|\Z)",
        source,
    )
    if match is None:
        raise AssertionError(f"Missing required job {name}")
    return match.group(1)


def _steps(job: str) -> list[tuple[str | None, str]]:
    starts = list(re.finditer(r"(?m)^      -(?:[ \t]+|(?=\r?$))", job))
    result: list[tuple[str | None, str]] = []
    for index, match in enumerate(starts):
        stop = starts[index + 1].start() if index + 1 < len(starts) else len(job)
        body = job[match.end():stop]
        lines = body.splitlines()
        first = lines[0].strip() if lines else ""
        if first:
            named = re.fullmatch(r"name: ([^\r\n]+)", first)
        else:
            named = re.fullmatch(
                r"        name: ([^\r\n]+)", lines[1] if len(lines) > 1 else ""
            )
        result.append((named.group(1) if named else None, body))
    return result


def _guards(body: str) -> list[str]:
    return re.findall(r"(?m)^        if: ([^\r\n]+)$", body)


def _classifier(job: str) -> str:
    start = "      - name: Classify documentation-only PR (fail closed)\n"
    if job.count(start) != 1:
        raise AssertionError("Expected one classifier")
    match = re.search(
        r"(?ms)^" + re.escape(start)
        + r"(.*?)(?=^      - (?:name:|run:|uses:)|\Z)",
        job,
    )
    if match is None:
        raise AssertionError("Classifier is not an actual YAML step")
    return match.group(1)


class StaticDocsOnlyTests(unittest.TestCase):
    def test_required_jobs_and_all_heavy_steps_stay_present(self):
        workflow = STATIC.read_text(encoding="utf-8")
        self.assertIn("on:\n  push:\n", workflow)
        self.assertIn("  pull_request:\n", workflow)
        self.assertIn("  workflow_dispatch:\n", workflow)
        for job_id, heavy_names in HEAVY.items():
            with self.subTest(job=job_id):
                job = _job(workflow, job_id)
                self.assertRegex(
                    job,
                    r"(?m)^    name: " + re.escape(REQUIRED[job_id]) + r"$",
                )
                self.assertRegex(job, r"(?m)^    runs-on: ubuntu-latest$")
                self.assertNotRegex(job, r"(?m)^    if:")
                steps = _steps(job)
                self.assertEqual(
                    [name for name, _body in steps],
                    [
                        "Checkout repository",
                        "Classify documentation-only PR (fail closed)",
                        *heavy_names,
                    ],
                )
                self.assertEqual(_guards(steps[0][1]), [])
                self.assertEqual(_guards(steps[1][1]), [])
                for name, body in steps[2:]:
                    with self.subTest(job=job_id, name=name):
                        self.assertEqual(_guards(body), [CONDITION])

    def test_both_classifiers_match_unit_step_in_this_checkout(self):
        static = STATIC.read_text(encoding="utf-8")
        unit = UNIT.read_text(encoding="utf-8")
        expected = _classifier(_job(unit, "unit-api-contract"))
        self.assertIn('git show "$PRKS_PR_BASE_SHA:scripts/ci/docs_only.py"', expected)
        self.assertIn('if base_classifier="$(mktemp)"; then', expected)
        self.assertIn('echo "docs_only=$docs_only" >> "$GITHUB_OUTPUT"', expected)
        for job_id in HEAVY:
            self.assertEqual(_classifier(_job(static, job_id)), expected)

    def test_required_context_names_are_exact_not_substrings(self):
        workflow = STATIC.read_text(encoding="utf-8")
        for job_id in HEAVY:
            with self.subTest(job=job_id):
                actual = _job(workflow, job_id)
                pinned = r"(?m)^    name: " + re.escape(REQUIRED[job_id]) + r"$"
                self.assertRegex(actual, pinned)
                # A renamed required status check must fail the exact match.
                mutated = actual.replace(
                    "    name: " + REQUIRED[job_id] + "\n",
                    "    name: " + REQUIRED[job_id] + " (v2)\n",
                )
                self.assertNotRegex(mutated, pinned)

    def test_both_workflow_steps_must_run_trusted_base_classifier(self):
        static = STATIC.read_text(encoding="utf-8")
        unit = UNIT.read_text(encoding="utf-8")
        for job in (
            _job(static, "pyright"),
            _job(static, "eslint"),
            _job(unit, "unit-api-contract"),
        ):
            with self.subTest(job=job[:35]):
                script = _classifier(job)
                self.assertIn('PRKS_PR_BASE_SHA: ${{ github.event.pull_request.base.sha }}', script)
                self.assertIn('git fetch --no-tags --depth=1 origin "$PRKS_PR_BASE_SHA"', script)
                self.assertIn('git show "$PRKS_PR_BASE_SHA:scripts/ci/docs_only.py"', script)
                self.assertNotIn("python -m scripts.ci.docs_only --base", script)
                self.assertIn('docs_only=false', script)
                self.assertIn('decision=false', script)
                self.assertIn('if [[ "$decision" == "true" ]]; then', script)

    def test_unnamed_steps_and_fake_guards_cannot_escape(self):
        job = _job(STATIC.read_text(encoding="utf-8"), "eslint")
        expected_names = [
            "Checkout repository",
            "Classify documentation-only PR (fail closed)",
            *HEAVY["eslint"],
        ]
        for added in (
            "      - run: npm run lint\n",
            "      - uses: actions/setup-node@untrusted\n",
            "      -\n        run: npm run lint\n",
            "      -\n        name: Unreviewed job\n        run: echo unsafe\n",
        ):
            with self.subTest(added=added):
                self.assertNotEqual(
                    [name for name, _body in _steps(job + added)],
                    expected_names,
                )
        actual = dict(_steps(job))["Run high-signal JavaScript checks"]
        condition = "        if: " + CONDITION
        self.assertEqual(_guards(actual), [CONDITION])
        self.assertEqual(
            _guards(actual.replace(condition, "        # " + condition.strip())),
            [],
        )
        self.assertEqual(
            _guards(actual.replace(condition, "          " + condition.strip())),
            [],
        )


if __name__ == "__main__":
    unittest.main()
