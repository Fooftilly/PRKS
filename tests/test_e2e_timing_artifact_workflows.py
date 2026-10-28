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
        r"(?ms)^  " + re.escape(job) + r":\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)",
        workflow,
    )
    if match is None:
        raise AssertionError("Missing CI job: " + job)
    return match.group(1)


def named_upload_step(job: str, name: str) -> str:
    match = re.search(
        r"(?ms)^      - name: " + re.escape(name)
        + r"\n(.*?)(?=^      - name: |\Z)",
        job,
    )
    if match is None:
        raise AssertionError("Missing upload step: " + name)
    return match.group(1)


class E2ETimingArtifactWorkflowTests(unittest.TestCase):
    def test_manual_source_full_is_unprofiled_and_successful(self):
        workflow = SOURCE.read_text(encoding="utf-8")
        shard = job_source(workflow, "e2e")
        step = named_upload_step(
            shard, "Export exact test timings (manual full benchmark only)"
        )
        self.assertIn(
            "        if: >-\n"
            "          github.event_name == 'workflow_dispatch' &&\n"
            "          !inputs.profile && !inputs.route_fastpath &&\n"
            "          needs.e2e-plan.outputs.mode == 'full' &&\n"
            "          steps.tests.outcome == 'success'\n",
            step,
        )
        self.assertIn(
            "name: e2e-timings-${{ matrix.shard }}-of-${{ matrix.total }}",
            step,
        )
        self.assertIn(
            "uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9",
            step,
        )
        self.assertIn("path: .tests/e2e-timings.json", step)
        self.assertIn("if-no-files-found: error", step)
        self.assertEqual(shard.count("path: .tests/e2e-timings.json"), 1)
        self.assertNotIn("path: .tests/\n", step)

    def test_class_locality_is_manual_only_and_never_changes_default_gate(self):
        workflow = SOURCE.read_text(encoding="utf-8")
        self.assertIn("      class_locality:", workflow)
        self.assertIn("        default: false", workflow)
        shard = job_source(workflow, "e2e")
        self.assertIn(
            "github.event_name == 'workflow_dispatch' && inputs.class_locality",
            shard,
        )
        self.assertIn("args+=(--class-locality-pilot)", shard)
        self.assertIn('if [[ "$mode" != "full" ]]; then', shard)

    def test_manual_packaged_full_and_compare_not_smoke_or_pr(self):
        workflow = PACKAGED.read_text(encoding="utf-8")
        shard = job_source(workflow, "packaged-frontend")
        step = named_upload_step(
            shard, "Export packaged test timings (manual full benchmark only)"
        )
        self.assertIn(
            "        if: >-\n"
            "          github.event_name == 'workflow_dispatch' &&\n"
            "          (inputs.packaged_mode == 'full' || inputs.packaged_mode == 'compare') && success()\n",
            step,
        )
        self.assertIn("name: packaged-e2e-timings-${{ matrix.shard }}-of-6", step)
        self.assertIn(
            "uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9",
            step,
        )
        self.assertIn("path: .tests/e2e-timings.json", step)
        self.assertIn("if-no-files-found: error", step)
        self.assertEqual(shard.count("path: .tests/e2e-timings.json"), 1)
        self.assertNotIn("path: .tests/\n", step)
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
