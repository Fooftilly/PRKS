"""Unit coverage for tests.e2e.policy — no Chromium."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from tests.e2e import policy


FAKE_IDS = [
    "tests.e2e.test_app.AppShellAndNavigationTests.test_app_loads_and_real_navigation",
    "tests.e2e.test_app.AppShellAndNavigationTests.test_command_palette_opens_seeded_work",
    "tests.e2e.test_app.WorkspaceTabsTests.test_close_selects_right_neighbor_then_home",
    "tests.e2e.test_app.WorkspaceTilingTests.test_split_right",
    "tests.e2e.test_app.ResearchGraphChromeTests.test_escape_closes_filters_and_returns_focus",
    "tests.e2e.test_research_graph_offline.ResearchGraphOfflineTests.test_core_cache_and_local_interactions",
    "tests.e2e.test_concepts_durable.DurableConceptTests.test_create",
    "tests.e2e.test_offline.OfflineConceptTests.test_index",
    "tests.e2e.test_offline.OfflineFoundationTests.test_cached_work_renders_offline_after_reload",
    "tests.e2e.test_folders_offline.FoldersOfflineTests.test_default_offline_launch_lands_on_the_cached_hierarchy",
    "tests.e2e.test_work_tags_offline.OfflineWorkTagTests.test_add",
    "tests.e2e.test_local_store_durability.LocalStoreDurabilityTests.test_a_pending_operation_survives_a_page_reload",
    "tests.e2e.test_app.WorkCreateWorkflowTests.test_unified_create_control_and_menu",
    "tests.e2e.test_app.SettingsCategoryWorkflowTests.test_categories_present_general_default_and_calm",
]


class SmokeSelectionTests(unittest.TestCase):
    def test_smoke_returns_only_known_curated_ids_in_order(self):
        selected = policy.select_smoke(FAKE_IDS)
        self.assertTrue(selected)
        self.assertEqual(selected, [tid for tid in policy.SMOKE_TEST_IDS if tid in FAKE_IDS])
        self.assertIn(
            "tests.e2e.test_app.AppShellAndNavigationTests.test_app_loads_and_real_navigation",
            selected,
        )

    def test_smoke_drops_missing_ids_without_failing(self):
        selected = policy.select_smoke(["tests.e2e.test_app.AppShellAndNavigationTests.test_app_loads_and_real_navigation"])
        self.assertEqual(len(selected), 1)


class FeatureSelectionTests(unittest.TestCase):
    def test_graph_feature_selects_app_and_offline_graph(self):
        selected = policy.select_features(FAKE_IDS, ["graph"])
        self.assertTrue(
            any("ResearchGraphChromeTests" in tid for tid in selected)
        )
        self.assertTrue(
            any("test_research_graph_offline" in tid for tid in selected)
        )
        self.assertFalse(any("WorkspaceTabsTests" in tid for tid in selected))

    def test_unknown_feature_raises(self):
        with self.assertRaises(ValueError) as ctx:
            policy.select_features(FAKE_IDS, ["not-a-real-group"])
        self.assertIn("unknown E2E feature", str(ctx.exception))

    def test_catalog_lists_every_feature(self):
        text = policy.format_feature_catalog()
        for name in policy.FEATURES:
            self.assertIn(name, text)


class AffectedMappingTests(unittest.TestCase):
    def test_graph_production_path_maps_to_graph(self):
        rule, feats, skip, _note = policy.match_affected_path(
            "frontend/js/components/research-graph.js"
        )
        self.assertEqual(rule, "graph")
        self.assertEqual(feats, ("graph",))
        self.assertFalse(skip)

    def test_shared_core_is_broad(self):
        _rule, feats, skip, _note = policy.match_affected_path("frontend/js/app.js")
        self.assertFalse(skip)
        self.assertIn("smoke", feats)
        self.assertIn("shell", feats)
        self.assertIn("offline", feats)

    def test_docs_are_skipped(self):
        _rule, feats, skip, _note = policy.match_affected_path("AGENTS.md")
        self.assertTrue(skip)
        self.assertEqual(feats, ())

    def test_unmapped_frontend_falls_back_to_smoke(self):
        rule, feats, skip, note = policy.match_affected_path(
            "frontend/js/totally-new-widget.js"
        )
        self.assertEqual(rule, "unmapped-production")
        self.assertEqual(feats, ("smoke",))
        self.assertFalse(skip)
        self.assertIn("smoke", note)

    def test_e2e_module_maps_to_its_feature(self):
        _rule, feats, skip, _note = policy.match_affected_path(
            "tests/e2e/test_work_tags_offline.py"
        )
        self.assertFalse(skip)
        self.assertIn("sync", feats)

    def test_test_app_module_maps_to_smoke_only(self):
        _rule, feats, skip, _note = policy.match_affected_path("tests/e2e/test_app.py")
        self.assertFalse(skip)
        self.assertEqual(feats, ("smoke",))

    def test_select_affected_unions_features_and_explains(self):
        plan = policy.select_affected(
            FAKE_IDS,
            [
                "frontend/js/components/research-graph.js",
                "README.md",
                "frontend/js/workspace-tabs.js",
            ],
        )
        self.assertIn("graph", plan["features"])
        self.assertIn("tabs", plan["features"])
        self.assertTrue(plan["test_ids"])
        skips = [d for d in plan["decisions"] if d["path"] == "README.md"]
        self.assertTrue(skips[0]["skip"])

    def test_select_affected_empty_when_only_docs(self):
        plan = policy.select_affected(FAKE_IDS, ["docs/local-first-sync.md"])
        self.assertEqual(plan["features"], [])
        self.assertEqual(plan["test_ids"], [])
        self.assertIsNotNone(plan["empty_reason"])


class LastFailedPersistenceTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "e2e-last-failed.json"
            policy.save_last_failed(path, ["a.b.C.test_x"], meta={"tier": "feature"})
            data = policy.load_last_failed(path)
            self.assertEqual(data["test_ids"], ["a.b.C.test_x"])
            self.assertEqual(data["meta"]["tier"], "feature")

    def test_corrupt_or_missing_returns_none(self):
        self.assertIsNone(policy.load_last_failed(Path("/no/such/file.json")))
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "bad.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertIsNone(policy.load_last_failed(path))


class PathMatchTests(unittest.TestCase):
    def test_glob_double_star(self):
        self.assertTrue(policy._path_matches("frontend/css/style.css", "frontend/css/**"))
        self.assertTrue(policy._path_matches("backend/work_tag_sync.py", "backend/*_sync.py"))
        self.assertFalse(policy._path_matches("backend/server.py", "backend/*_sync.py"))


class ListChangedPathsTests(unittest.TestCase):
    def test_invokes_git_diff_against_base(self):
        with mock.patch("subprocess.check_output") as check:
            check.side_effect = [
                "frontend/js/app.js\n",  # diff vs base
                "frontend/js/app.js\nbackend/x.py\n",  # local vs HEAD when base != HEAD
                "frontend/js/new.js\n",  # untracked
            ]
            paths = policy.list_changed_paths(
                Path("/tmp/repo"), base="origin/master", include_untracked=True
            )
            self.assertIn("frontend/js/app.js", paths)
            self.assertIn("backend/x.py", paths)
            self.assertIn("frontend/js/new.js", paths)


class ReportBannerTests(unittest.TestCase):
    def test_full_gate_labelled(self):
        text = policy.report_banner("full", 700)
        self.assertIn("FULL REGRESSION GATE", text)
        self.assertIn("tier=full", text)

    def test_smoke_not_full_gate(self):
        text = policy.report_banner("smoke", 9)
        self.assertIn("NOT a full E2E gate", text)


class RunnerSelectionIntegrationTests(unittest.TestCase):
    """Smoke-check that run.py wires policy flags without launching Chromium."""

    def test_list_features_exit_zero(self):
        previous = os.environ.get("PRKS_E2E")
        os.environ["PRKS_E2E"] = "1"
        try:
            from tests.e2e import run as runner

            with mock.patch.object(runner, "ensure_chromium_installed"):
                code = runner.main(["--list-features"])
            self.assertEqual(code, 0)
        finally:
            if previous is None:
                os.environ.pop("PRKS_E2E", None)
            else:
                os.environ["PRKS_E2E"] = previous

    def test_list_tests_smoke_prints_curated(self):
        previous = os.environ.get("PRKS_E2E")
        os.environ["PRKS_E2E"] = "1"
        try:
            from tests.e2e import run as runner
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with mock.patch.object(runner, "ensure_chromium_installed"):
                with redirect_stdout(buf):
                    code = runner.main(["--smoke", "--list-tests"])
            self.assertEqual(code, 0)
            out = buf.getvalue()
            self.assertIn(
                "AppShellAndNavigationTests.test_app_loads_and_real_navigation",
                out,
            )
            # Must not dump the whole suite
            self.assertLess(out.count("\n"), 40)
        finally:
            if previous is None:
                os.environ.pop("PRKS_E2E", None)
            else:
                os.environ["PRKS_E2E"] = previous

    def test_dev_refuses_full_suite(self):
        previous = os.environ.get("PRKS_E2E")
        os.environ["PRKS_E2E"] = "1"
        try:
            from tests.e2e import run as runner

            with mock.patch.object(runner, "ensure_chromium_installed"):
                code = runner.main(["--dev"])
            self.assertEqual(code, 2)
        finally:
            if previous is None:
                os.environ.pop("PRKS_E2E", None)
            else:
                os.environ["PRKS_E2E"] = previous


if __name__ == "__main__":
    unittest.main()
