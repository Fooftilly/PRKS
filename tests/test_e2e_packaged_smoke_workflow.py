"""Guard the packaged gate: smoke on routine PRs, every test on packaging PRs.

No Chromium or YAML dependency: this protects the workflow's critical
routing and offline/production-distribution invariants at code review time.
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/e2e-classic-distribution-gate.yml"


def job_source(workflow: str, job: str) -> str:
    match = re.search(
        r"(?ms)^  " + re.escape(job) + r":\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)",
        workflow,
    )
    if match is None:
        raise AssertionError(f"Workflow job missing: {job}")
    return match.group(1)


class PackagedSmokeWorkflowTests(unittest.TestCase):
    def test_pull_requests_run_the_planned_packaged_jobs(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        plan = job_source(workflow, "packaged-plan")
        full = job_source(workflow, "packaged-frontend")
        self.assertIn("  pull_request:\n", workflow)
        self.assertIn(
            "if: github.event_name != 'workflow_dispatch' || inputs.packaged_mode != 'smoke'",
            plan,
        )
        # Manual runs (including the nightly master CI) always run every test.
        self.assertIn(
            "PRKS_E2E_CI_FORCE_FULL: ${{ github.event_name == 'workflow_dispatch' && '1' || '' }}",
            plan,
        )
        self.assertIn('--packaged-plan --base "origin/${BASE_REF}"', plan)
        # A failed base fetch runs every test rather than no plan at all.
        self.assertIn('plan="$(PRKS_E2E_CI_FORCE_FULL=1 python tests/e2e/run.py --packaged-plan)"', plan)
        self.assertIn(
            'if mode not in ("full", "smoke") or (mode == "full") != bool(include):', plan
        )
        self.assertIn("needs: packaged-plan", full)
        # A smoke plan has no matrix: the shard job must be skipped, never expanded.
        self.assertRegex(full, r"(?m)^    if: needs\.packaged-plan\.outputs\.mode == 'full'$")
        self.assertIn("matrix: ${{ fromJSON(needs.packaged-plan.outputs.matrix) }}", full)
        self.assertIn("PRKS_E2E_SHARD_INDEX: ${{ matrix.shard }}", full)
        self.assertIn("PRKS_E2E_SHARD_TOTAL: ${{ matrix.total }}", full)
        self.assertIn(
            'python tests/e2e/run.py --jobs 1 --shard "${PRKS_E2E_SHARD_INDEX}/${PRKS_E2E_SHARD_TOTAL}"',
            full,
        )
        self.assertNotIn("--packaged-routine", workflow)
        smoke = job_source(workflow, "packaged-smoke")
        self.assertIn("needs: packaged-plan", smoke)
        self.assertIn(
            "(github.event_name == 'pull_request' && needs.packaged-plan.outputs.mode == 'smoke')",
            smoke,
        )
        # A manual smoke run skips the plan job; the smoke job must still run.
        self.assertIn("!cancelled() &&", smoke)

        # Both real Docker packaging/rollback and browser SW transitions
        # remain standalone jobs on PRs and both manual modes.
        for job in ("docker-build-modes", "worker-upgrade-rollback"):
            source = job_source(workflow, job)
            self.assertNotIn("if: github.event_name", source)

    def test_manual_modes_have_distinct_concurrency_groups(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "group: e2e-classic-distribution-${{ github.event.pull_request.number || "
            "format('{0}-{1}', github.ref, inputs.packaged_mode) }}",
            workflow,
        )
        self.assertIn("cancel-in-progress: ${{ github.event_name == 'pull_request' }}", workflow)

    def test_smoke_runs_on_smoke_plans_and_manual_modes_and_stages_real_distribution(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("packaged_mode:", workflow)
        smoke = job_source(workflow, "packaged-smoke")
        full = job_source(workflow, "packaged-frontend")
        self.assertIn(
            "      (github.event_name == 'workflow_dispatch' &&\n"
            "      (inputs.packaged_mode == 'smoke' || inputs.packaged_mode == 'compare')))",
            smoke,
        )
        # The one-run compare must execute smoke alongside the full matrix,
        # while manual full (the nightly master CI) runs the shards only.
        # Bind the full/smoke/compare options to the packaged_mode input,
        # rather than matching unrelated YAML content elsewhere.
        self.assertIn(
            "      packaged_mode:\n"
            "        description:",
            workflow,
        )
        self.assertIn(
            "        options:\n"
            "          - full\n"
            "          - smoke\n"
            "          - compare\n"
            "        default: full",
            workflow,
        )
        self.assertIn(
            "if: github.event_name != 'workflow_dispatch' || inputs.packaged_mode != 'smoke'",
            job_source(workflow, "packaged-plan"),
        )
        self.assertIn("needs: packaged-plan", full)
        # Conditions must be attached to their actual job, not merely
        # present somewhere in the workflow. Both independent guard jobs
        # remain unconditional for PRs, full, smoke and paired runs.
        for guard in ("docker-build-modes", "worker-upgrade-rollback"):
            guard_body = job_source(workflow, guard)
            self.assertNotRegex(guard_body, r"(?m)^    if:")
        # This must be the same packaged application, not a faster fake.
        for command in (
            "python -m scripts.build_classic_distribution --root . --output .tests/classic-distribution",
            "mv .tests/classic-distribution frontend",
            "node --check frontend/js/prks-classic-head.bundle.js",
            "node --check frontend/js/prks-classic-body.bundle.js",
            "python -m unittest tests.test_frontend_service_worker",
        ):
            with self.subTest(command=command):
                self.assertIn(command, full)
                self.assertIn(command, smoke)
        self.assertIn("python tests/e2e/run.py --jobs 1 --smoke --no-pointer-capture", smoke)
        expected_cases = {
            "tests/e2e/test_app.py": {
                "MainSecondaryDividerTests": (
                    "test_divider_drag_causes_no_additional_route_or_pdf_requests",
                ),
                "PdfPersistenceTests": (
                    "test_annotation_copy_link_reports_clipboard_success_and_failure",
                ),
            },
            "tests/e2e/test_offline.py": {
                "OfflineFoundationTests": (
                    "test_previously_opened_pdf_reopens_offline",
                    "test_cached_pdf_reopened_offline_keeps_annotation_tools",
                ),
            },
            "tests/e2e/test_research_graph_offline.py": {
                "ResearchGraphOfflineTests": (
                    "test_record_graph_navigation_both_directions",
                ),
            },
        }
        # Extract only the continuation lines of the actual targeted run.py
        # command. Finding IDs in comments or the job summary is insufficient.
        lines = smoke.splitlines()
        targeted = [
            i
            for i, line in enumerate(lines)
            if line.strip().startswith(
                "python tests/e2e/run.py --jobs 1 --no-pointer-capture "
            )
        ]
        self.assertEqual(len(targeted), 1)
        self.assertTrue(lines[targeted[0]].rstrip().endswith(chr(92)))
        executed_ids = []
        for line in lines[targeted[0] + 1 :]:
            entry = line.strip()
            if not entry.startswith("tests.e2e."):
                break
            executed_ids.append(entry.rstrip(chr(92)).strip())
        expected_ids = [
            f"tests.e2e.{Path(path).stem}.{class_name}.{method_name}"
            for path, classes in expected_cases.items()
            for class_name, methods in classes.items()
            for method_name in methods
        ]
        self.assertCountEqual(executed_ids, expected_ids)

        for path, classes in expected_cases.items():
            # Validate real unittest methods, not just matching strings in a
            # comment.
            source = ast.parse((ROOT / path).read_text(encoding="utf-8"))
            actual = {
                node.name: {
                    method.name
                    for method in node.body
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                for node in source.body
                if isinstance(node, ast.ClassDef)
            }
            for class_name, methods in classes.items():
                for method_name in methods:
                    with self.subTest(class_name=class_name, method_name=method_name):
                        self.assertIn(method_name, actual.get(class_name, set()))
        self.assertIn("9 curated + 5 explicit browser tests", smoke)


if __name__ == "__main__":
    unittest.main()
