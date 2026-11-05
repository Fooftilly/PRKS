"""Contract for the opt-in source and packaged PDF recovery stress lanes (#1007)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github/workflows/e2e-flake-stress.yml"
)
PDF_RECOVERY = (
    "tests.e2e.test_app.PdfAnnotationCommentEditTests."
    "test_a_failed_highlight_is_settled_by_saving_it_again"
)


def _matrix_cases(source: str) -> dict[str, dict[str, str]]:
    """Read complete explicit matrix cases without importing a YAML dependency."""
    matrix = source.split("      matrix:\n        include:\n", 1)[1].split(
        "\n    steps:\n", 1
    )[0]
    rows: dict[str, dict[str, str]] = {}
    pattern = re.compile(r"(?m)^          - case: ([a-z0-9-]+)\n((?:^            [^\n]*\n)+)")
    for match in pattern.finditer(matrix + "\n"):
        name = match.group(1)
        if name in rows:
            raise AssertionError("Duplicate stress case: " + name)
        fields = {}
        for line in match.group(2).splitlines():
            key, value = line.strip().split(": ", 1)
            fields[key] = value
        rows[name] = fields
    return rows


class PdfRecoveryStressWorkflowTests(unittest.TestCase):
    def test_source_and_packaged_pdf_recovery_use_the_same_strict_oracle(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        rows = _matrix_cases(workflow)
        source = rows["pdf-recovery-1007"]
        packaged = rows["packaged-pdf-recovery-1007"]
        self.assertEqual(source["test_id"], PDF_RECOVERY)
        self.assertEqual(packaged["test_id"], PDF_RECOVERY)
        self.assertNotIn("packaged", source)
        self.assertEqual(packaged["packaged"], "true")
        self.assertEqual(
            sum(row.get("test_id") == PDF_RECOVERY for row in rows.values()), 2
        )

    def test_packaged_case_builds_real_distribution_and_runs_unmodified_test(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("if: matrix.packaged == true", workflow)
        for line in (
            "python -m scripts.build_classic_distribution --root . --output .tests/classic-distribution",
            "mv .tests/classic-distribution frontend",
            "node --check frontend/js/prks-classic-head.bundle.js",
            "node --check frontend/js/prks-classic-body.bundle.js",
            "python -m unittest tests.test_frontend_service_worker",
            "PRKS_E2E_DISTRIBUTION: ${{ matrix.packaged == true && 'packaged' || 'source' }}",
            "PRKS_E2E_TEST_ID: ${{ matrix.test_id }}",
            "for ((i=1; i<=PRKS_E2E_STRESS_ITERATIONS; i++)); do",
            'if ! python tests/e2e/run.py --jobs 1 --no-pointer-capture "$PRKS_E2E_TEST_ID"; then',
            "exit 1",
        ):
            with self.subTest(line=line):
                self.assertIn(line, workflow)
        # The deliberate missing-recovery mutation must remain a separate
        # negative control; a green stress test cannot replace its oracle.
        self.assertIn("PRKS_NC_PDF_RECOVERY_OMITTED", workflow)
        self.assertIn("server never stored comment 'recovered highlight'", workflow)

    def test_new_case_does_not_activate_stress_for_unrelated_pull_requests(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertIn("  pull_request:", workflow)
        self.assertIn("      - .github/workflows/e2e-flake-stress.yml", workflow)
        self.assertIn("      - .github/scripts/e2e_flake_negative_controls.py", workflow)
        self.assertIn("    timeout-minutes: 50", workflow)


if __name__ == "__main__":
    unittest.main()
