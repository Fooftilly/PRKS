"""Protect manual-only E2E timing artifacts and their privacy boundary."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".github/workflows/e2e-gate.yml"
PACKAGED = ROOT / ".github/workflows/e2e-classic-distribution-gate.yml"


def job_source(workflow: str, job: str) -> str:
    match = re.search(
        r"(?ms)^  " + re.escape(job) + r":\\n(.*?)(?=^  [a-z][a-z-]*:\\n|\\Z)",
        workflow,
    )
    if match is None:
        raise AssertionError("Missing CI job: " + job)
    return match.group(1)


class E2ETimingArtifactWorkflowTests(unittest.TestCase):
    def test_manual_source_full_is_unprofiled_and_successful(self):
        workflow = SOURCE.read_text(encoding="utf-8")
        shard = job_source(workflow, "e2e")
        self.assertIn("github.event_name == 'workflow_dispatch' &&", shard)
        self.assertIn("!inputs.profile && !inputs.route_fastpath &&", shard)
        self.assertIn("needs.e2e-plan.outputs.mode == 'full' &&", shard)
        self.assertIn("steps.tests.outcome == 'success'", shard)
        self.assertIn(
            "name: e2e-timings-${{ matrix.shard }}-of-${{ matrix.total }}",
            shard,
        )
        self.assertIn("path: .tests/e2e-timings.json", shard)
        self.assertIn("if-no-files-found: error", shard)
        self.assertEqual(shard.count("path: .tests/e2e-timings.json"), 1)
        self.assertNotIn("path: .tests/\\n", shard)

    def test_manual_packaged_full_not_smoke_or_pr(self):
        workflow = PACKAGED.read_text(encoding="utf-8")
        shard = job_source(workflow, "packaged-frontend")
        self.assertIn("github.event_name == 'workflow_dispatch' &&", shard)
        self.assertIn("inputs.packaged_mode == 'full' && success()", shard)
        self.assertIn("name: packaged-e2e-timings-${{ matrix.shard }}-of-6", shard)
        self.assertIn("path: .tests/e2e-timings.json", shard)
        self.assertIn("if-no-files-found: error", shard)
        self.assertEqual(shard.count("path: .tests/e2e-timings.json"), 1)
        self.assertNotIn("path: .tests/\\n", shard)
        manual_smoke = job_source(workflow, "packaged-smoke")
        self.assertNotIn("e2e-timings.json", manual_smoke)

    def test_complete_run_must_remain_required_for_committed_baseline(self):
        script = (
            ROOT / "tests/e2e/update_timing_baseline.py"
        ).read_text(encoding="utf-8")
        self.assertIn("assess_measurement_coverage", script)
        self.assertIn("if not args.allow_partial:", script)


if __name__ == "__main__":
    unittest.main()
