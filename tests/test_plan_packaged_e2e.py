"""Regression contracts for the staged (inactive) packaged E2E policy."""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.ci.plan_packaged_e2e import (
    EXTRA_PACKAGED_SMOKE_IDS,
    MAX_SMOKE_AFFECTED_TESTS,
    SAFE_VUE_VIEWS,
    plan_packaged_e2e,
    _untracked_frontend_app_paths,
    main,
)
from tests.e2e.policy import SMOKE_TEST_IDS

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/e2e-classic-distribution-gate.yml"

PROGRESS = "frontend-app/src/features/progress/ProgressView.vue"
PROCESSING = "frontend-app/src/features/processing/ProcessingFilesRoute.vue"
BUNDLE = "frontend/vue/assets/app-generated.js"
SW = "frontend/sw.js"
STAMP_DIFF = (
    "diff --git a/frontend/sw.js b/frontend/sw.js\n"
    "--- a/frontend/sw.js\n"
    "+++ b/frontend/sw.js\n"
    "@@ -1 +1 @@\n"
    "-const DEPENDENCY_REVISION = '0123456789ab';\n"
    "+const DEPENDENCY_REVISION = 'abcdef123456';\n"
)


def inventory(*affected):
    return list(dict.fromkeys((*SMOKE_TEST_IDS, *EXTRA_PACKAGED_SMOKE_IDS, *affected)))


BROWSE_CASE = "tests.e2e.test_browse_offline.BrowseOfflineTests.test_example"
PROCESSING_CASE = "tests.e2e.test_processing_route_surface.ProcessingRouteTests.test_example"


class PackagedSelectionPolicyTests(unittest.TestCase):
    def test_cli_direct_help_imports_policy_from_repo_root(self):
        script = ROOT / "scripts/ci/plan_packaged_e2e.py"
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=ROOT, check=False, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--base", result.stdout)

    def test_activation_is_impossible_through_this_planner(self):
        proposal = plan_packaged_e2e(
            [PROGRESS], known_test_ids=inventory(BROWSE_CASE)
        )
        self.assertFalse(proposal["active"])
        self.assertEqual(proposal["effective_mode"], "full")
        self.assertEqual(proposal["proposed_mode"], "smoke_affected")
        self.assertEqual(proposal["full_shards_if_executed"], 6)
        self.assertEqual(
            proposal["required_independent_guards"],
            ["docker-build-modes", "worker-upgrade-rollback"],
        )
        self.assertIn("browse", proposal["features"])
        self.assertEqual(len(proposal["test_ids"]), 15)
        self.assertEqual(set(inventory(BROWSE_CASE)), set(proposal["test_ids"]))

    def test_two_safe_views_union_their_affected_tests(self):
        candidate = plan_packaged_e2e(
            [PROGRESS, PROCESSING], known_test_ids=inventory(BROWSE_CASE, PROCESSING_CASE)
        )
        self.assertTrue(candidate["candidate"])
        self.assertEqual(candidate["proposed_mode"], "smoke_affected")
        self.assertIn(BROWSE_CASE, candidate["test_ids"])
        self.assertIn(PROCESSING_CASE, candidate["test_ids"])
        self.assertEqual(candidate["test_count"], 16)

    def test_source_reproducibility_and_revision_stamp_are_obligatory(self):
        safe = plan_packaged_e2e(
            [PROGRESS, BUNDLE, SW],
            known_test_ids=inventory(BROWSE_CASE),
            path_diffs={SW: STAMP_DIFF},
        )
        self.assertEqual(safe["proposed_mode"], "smoke_affected")
        for paths, diffs in (
            ([PROGRESS, BUNDLE, SW], {}),
            ([PROGRESS, BUNDLE, SW], {SW: STAMP_DIFF.replace("DEPENDENCY_REVISION", "CACHE_NAME")}),
            ([BUNDLE], {SW: STAMP_DIFF}),
            ([PROGRESS, SW], {SW: STAMP_DIFF}),
        ):
            with self.subTest(paths=paths, diffs=diffs):
                result = plan_packaged_e2e(paths, known_test_ids=inventory(BROWSE_CASE), path_diffs=diffs)
                self.assertEqual(result["proposed_mode"], "full")

    def test_narrow_allowlist_rejects_shared_and_durable_paths(self):
        risky = (
            "frontend/js/app.js",
            "frontend/js/offline-runtime.js",
            "frontend/sw.js",
            "frontend/index.html",
            "frontend/js/pdf-annotation-state.js",
            "frontend-app/src/main.ts",
            "frontend-app/src/features/progress/route.ts",
            "frontend-app/src/features/processing/session.ts",
            "frontend-app/src/features/research-graph/GraphView.vue",
            "frontend-app/src/components/SharedModal.vue",
            "frontend-app/src/features/progress/NewRoute.vue",
            "backend/server.py",
            "backend/person_sync.py",
            "Dockerfile",
            "scripts/build_classic_distribution.py",
            "tests/e2e/policy.py",
            ".github/workflows/e2e-classic-distribution-gate.yml",
            "requirements-dev.txt",
        )
        for changed in risky:
            with self.subTest(changed=changed):
                result = plan_packaged_e2e([PROGRESS, changed], known_test_ids=inventory(BROWSE_CASE))
                self.assertEqual(result["proposed_mode"], "full")
                self.assertEqual(result["effective_mode"], "full")

    def test_smoke_alone_cannot_masquerade_as_affected_coverage(self):
        only_smoke = inventory()
        result = plan_packaged_e2e([PROGRESS], known_test_ids=only_smoke)
        self.assertEqual(result["proposed_mode"], "full")
        self.assertIn("affected feature", result["reason"])

    def test_local_cli_fails_full_on_new_untracked_vue_file(self):
        missing = b"frontend-app/src/features/progress/NewComponent.vue\0"
        with patch(
            "scripts.ci.plan_packaged_e2e.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout=missing),
        ):
            self.assertEqual(
                _untracked_frontend_app_paths(ROOT),
                ["frontend-app/src/features/progress/NewComponent.vue"],
            )
        with (
            patch(
                "scripts.ci.plan_packaged_e2e.policy.list_changed_paths",
                return_value=[PROGRESS],
            ),
            patch(
                "scripts.ci.plan_packaged_e2e._untracked_frontend_app_paths",
                return_value=["frontend-app/src/features/progress/NewComponent.vue"],
            ),
            patch("sys.argv", ["plan_packaged_e2e", "--base", "origin/master"]),
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(main(), 0)
        self.assertEqual(json.loads(output.getvalue())["proposed_mode"], "full")

    def test_dry_run_requires_complete_discovered_inventory(self):
        dry = plan_packaged_e2e([PROGRESS])
        self.assertTrue(dry["candidate"])
        self.assertEqual(dry["proposed_mode"], "full")
        self.assertEqual(dry["test_ids"], [])
        self.assertIn("discovery", dry["reason"])
        for ids in ([], [BROWSE_CASE], inventory(BROWSE_CASE)[:-1]):
            with self.subTest(ids=ids):
                self.assertEqual(
                    plan_packaged_e2e([PROGRESS], known_test_ids=ids)["proposed_mode"],
                    "full",
                )

    def test_empty_unsafe_docs_only_and_forced_full_fail_closed(self):
        cases = (
            [],
            None,
            [""],
            ["../frontend-app/src/features/progress/ProgressView.vue"],
            ["/frontend-app/src/features/progress/ProgressView.vue"],
            ["frontend-app//src/features/progress/ProgressView.vue"],
            ["frontend-app\\src\\features\\progress\\ProgressView.vue"],
            ["docs/performance.md"],
            ["frontend-app/src/features/progress/ProgressView.vue", "unknown/path"],
        )
        for changed in cases:
            with self.subTest(changed=changed):
                self.assertEqual(
                    plan_packaged_e2e(changed, known_test_ids=inventory(BROWSE_CASE))["proposed_mode"],
                    "full",
                )
        forced = plan_packaged_e2e(
            [PROGRESS], known_test_ids=inventory(BROWSE_CASE), force_full=True
        )
        self.assertEqual(forced["proposed_mode"], "full")
        self.assertIn("release", forced["reason"])

    def test_feature_local_unit_tests_can_accompany_an_eligible_view(self):
        result = plan_packaged_e2e(
            [PROGRESS, "frontend-app/src/features/progress/ProgressView.test.ts"],
            known_test_ids=inventory(BROWSE_CASE),
        )
        self.assertEqual(result["proposed_mode"], "smoke_affected")

    def test_nonproduction_docs_changes_do_not_hide_unsafe_changes(self):
        proposal = plan_packaged_e2e(
            [PROGRESS, "docs/feature-notes.md"],
            known_test_ids=inventory(BROWSE_CASE),
        )
        self.assertEqual(proposal["proposed_mode"], "smoke_affected")
        rejected = plan_packaged_e2e(
            [PROGRESS, "docs/feature-notes.md", "backend/db_manager.py"],
            known_test_ids=inventory(BROWSE_CASE),
        )
        self.assertEqual(rejected["proposed_mode"], "full")

    def test_broad_affected_test_selection_falls_back_to_full(self):
        many = [
            f"tests.e2e.test_browse_offline.BrowseOfflineTests.test_case_{i}"
            for i in range(MAX_SMOKE_AFFECTED_TESTS + 1)
        ]
        proposal = plan_packaged_e2e(
            [PROGRESS], known_test_ids=inventory(*many)
        )
        self.assertEqual(proposal["proposed_mode"], "full")
        self.assertIn("too broad", proposal["reason"])

    def test_real_manual_smoke_five_ids_match_single_source_of_truth(self):
        content = WORKFLOW.read_text(encoding="utf-8")
        match = re.search(
            r"(?ms)^  packaged-smoke:\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)",
            content,
        )
        self.assertIsNotNone(match)
        job = match.group(1)
        lines = job.splitlines()
        starts = [
            i for i, line in enumerate(lines)
            if line.strip().startswith("python tests/e2e/run.py --jobs 1 --no-pointer-capture ")
        ]
        self.assertEqual(len(starts), 1)
        actual = []
        for line in lines[starts[0] + 1 :]:
            path = line.strip()
            if not path.startswith("tests.e2e."):
                break
            actual.append(path.rstrip("\\").strip())
        self.assertEqual(tuple(actual), EXTRA_PACKAGED_SMOKE_IDS)
        self.assertEqual(len(SMOKE_TEST_IDS), 9)
        self.assertEqual(len(EXTRA_PACKAGED_SMOKE_IDS), 5)

    def test_workflow_remains_full_on_every_pull_request(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        full = workflow.split("\n  packaged-frontend:\n", 1)[1].split("\n  packaged-smoke:\n", 1)[0]
        smoke = workflow.split("\n  packaged-smoke:\n", 1)[1]
        self.assertIn(
            "if: github.event_name != 'workflow_dispatch' || inputs.packaged_mode != 'smoke'",
            full,
        )
        self.assertIn("shard: [1, 2, 3, 4, 5, 6]", full)
        self.assertIn("github.event_name == 'workflow_dispatch'", smoke)
        self.assertNotIn("plan_packaged_e2e", workflow)
        self.assertNotIn("smoke_affected", workflow)


if __name__ == "__main__":
    unittest.main()
