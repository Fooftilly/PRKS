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
    "tests.e2e.test_app.PersonProfileDraftOwnershipTests.test_draft",
    "tests.e2e.test_app.WorkDetailsPolishTests.test_notes_polish",
    "tests.e2e.test_research_graph_offline.ResearchGraphOfflineTests.test_core_cache_and_local_interactions",
    "tests.e2e.test_concepts_durable.DurableConceptTests.test_create",
    "tests.e2e.test_offline.OfflineConceptTests.test_index",
    "tests.e2e.test_offline.OfflineFoundationTests.test_cached_work_renders_offline_after_reload",
    "tests.e2e.test_folders_offline.FoldersOfflineTests.test_default_offline_launch_lands_on_the_cached_hierarchy",
    "tests.e2e.test_work_tags_offline.OfflineWorkTagTests.test_add",
    "tests.e2e.test_work_notes_offline.WorkNotesOfflineTests.test_research",
    "tests.e2e.test_local_store_durability.LocalStoreDurabilityTests.test_a_pending_operation_survives_a_page_reload",
    "tests.e2e.test_app.WorkCreateWorkflowTests.test_unified_create_control_and_menu",
    "tests.e2e.test_app.SettingsCategoryWorkflowTests.test_categories_present_general_default_and_calm",
]


class SmokeSelectionTests(unittest.TestCase):
    def test_smoke_returns_curated_ids_when_all_present(self):
        selected = policy.select_smoke(list(policy.SMOKE_TEST_IDS) + ["extra.unrelated"])
        self.assertEqual(selected, list(policy.SMOKE_TEST_IDS))

    def test_smoke_fails_when_curated_id_missing(self):
        with self.assertRaises(ValueError) as ctx:
            policy.select_smoke(
                [
                    "tests.e2e.test_app.AppShellAndNavigationTests.test_app_loads_and_real_navigation",
                ]
            )
        self.assertIn("missing test id", str(ctx.exception))
        self.assertIn("WorkspaceTabsTests", str(ctx.exception))


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

    def test_test_app_module_maps_to_all_owning_features_not_smoke_only(self):
        _rule, feats, skip, _note = policy.match_affected_path("tests/e2e/test_app.py")
        self.assertFalse(skip)
        self.assertIn("graph", feats)
        self.assertIn("tabs", feats)
        self.assertIn("people", feats)
        self.assertIn("work-detail", feats)
        self.assertIn("tiling", feats)
        self.assertNotEqual(feats, ("smoke",))

    def test_works_js_maps_to_work_detail_not_notes_only(self):
        rule, feats, skip, _note = policy.match_affected_path(
            "frontend/js/components/works.js"
        )
        self.assertEqual(rule, "work-detail")
        self.assertFalse(skip)
        self.assertEqual(feats, ("work-detail",))
        selected = policy.select_features(FAKE_IDS, list(feats))
        self.assertTrue(any("WorkDetailsPolishTests" in tid for tid in selected))
        self.assertTrue(any("test_work_notes_offline" in tid for tid in selected))

    def test_works_pdf_production_path_not_stale_alias(self):
        rule, feats, skip, _note = policy.match_affected_path(
            "frontend/js/components/works-pdf.js"
        )
        self.assertEqual(rule, "work-detail")
        self.assertEqual(feats, ("work-detail",))
        self.assertFalse(skip)
        stale_rule, stale_feats, stale_skip, _ = policy.match_affected_path(
            "frontend/js/works-pdf.js"
        )
        # Wrong path must not pretend to be the notes rule.
        self.assertNotEqual(stale_rule, "notes")
        self.assertNotEqual((stale_rule, stale_feats, stale_skip), (rule, feats, skip))

    def test_domain_sync_files_map_to_feature_groups(self):
        cases = (
            ("backend/concept_sync.py", "concepts", ("concepts", "graph", "notes")),
            ("backend/folder_sync.py", "folders", ("folders", "browse")),
            ("backend/person_sync.py", "people", ("people", "person-groups")),
            ("backend/argument_sync.py", "arguments", ("arguments", "graph")),
            ("backend/position_sync.py", "positions", ("positions", "graph")),
            ("backend/playlist_sync.py", "playlists", ("playlists",)),
            ("backend/work_note_sync.py", "notes", ("notes",)),
            ("backend/pdf_annotations.py", "work-detail", ("work-detail",)),
            ("backend/work_lifecycle_sync.py", "work-lifecycle", ("offline", "folders", "sync")),
            ("backend/work_tag_sync.py", "sync-families", ("sync", "offline")),
        )
        for path, rule_name, expected in cases:
            with self.subTest(path=path):
                rule, feats, skip, _note = policy.match_affected_path(path)
                self.assertEqual(rule, rule_name)
                self.assertFalse(skip)
                self.assertEqual(feats, expected)

    def test_domain_state_files_map_to_feature_groups(self):
        cases = (
            ("frontend/js/concept-state.js", "concepts"),
            ("frontend/js/folder-state.js", "folders"),
            ("frontend/js/person-state.js", "people"),
            ("frontend/js/argument-state.js", "arguments"),
            ("frontend/js/work-notes-state.js", "notes"),
            ("frontend/js/work-lifecycle-state.js", "work-lifecycle"),
            ("frontend/js/workspace-overview.js", "workspace-overview"),
            ("frontend/js/overview-primitives.js", "workspace-overview"),
        )
        for path, rule_name in cases:
            with self.subTest(path=path):
                rule, feats, skip, _note = policy.match_affected_path(path)
                self.assertEqual(rule, rule_name)
                self.assertFalse(skip)
                self.assertTrue(feats)

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
        self.assertFalse(plan["noop_ok"])
        skips = [d for d in plan["decisions"] if d["path"] == "README.md"]
        self.assertTrue(skips[0]["skip"])

    def test_select_affected_docs_only_is_successful_noop(self):
        plan = policy.select_affected(FAKE_IDS, ["docs/local-first-sync.md"])
        self.assertEqual(plan["features"], [])
        self.assertEqual(plan["test_ids"], [])
        self.assertTrue(plan["noop_ok"])
        self.assertIsNotNone(plan["empty_reason"])

    def test_select_affected_unit_only_is_successful_noop(self):
        plan = policy.select_affected(FAKE_IDS, ["tests/test_e2e_policy.py"])
        self.assertTrue(plan["noop_ok"])
        self.assertEqual(plan["test_ids"], [])

    def test_select_affected_broken_feature_selection_is_not_noop(self):
        # Features mapped, but none of the known IDs match → fail, not noop.
        plan = policy.select_affected(
            ["tests.e2e.unrelated.SomeTests.test_x"],
            ["frontend/js/components/research-graph.js"],
        )
        self.assertIn("graph", plan["features"])
        self.assertEqual(plan["test_ids"], [])
        self.assertFalse(plan["noop_ok"])
        self.assertIn("zero tests", plan["empty_reason"])

    def test_deleted_path_still_maps(self):
        rule, feats, skip, _note = policy.match_affected_path(
            "frontend/js/components/research-graph.js"
        )
        self.assertEqual(rule, "graph")
        self.assertFalse(skip)
        self.assertEqual(feats, ("graph",))


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

    def test_merge_retains_unexecuted_prior_failures(self):
        previous = ["a.Fail.test_1", "a.Fail.test_2", "a.Fail.test_3"]
        executed = ["a.Fail.test_1", "b.Other.test_ok"]
        current_failed = ["b.Other.test_ok"]
        merged = policy.merge_last_failed(previous, executed, current_failed)
        self.assertEqual(
            merged,
            ["a.Fail.test_2", "a.Fail.test_3", "b.Other.test_ok"],
        )

    def test_merge_removes_rerun_passed_failures(self):
        previous = ["a.Fail.test_1", "a.Fail.test_2"]
        executed = ["a.Fail.test_1", "a.Fail.test_2"]
        current_failed = []
        merged = policy.merge_last_failed(previous, executed, current_failed)
        self.assertEqual(merged, [])

    def test_merge_multi_failure_partial_rerun(self):
        # Three prior failures; only one rerun and it still fails; another new fails.
        previous = ["t.A.test_1", "t.A.test_2", "t.A.test_3"]
        executed = ["t.A.test_1", "t.B.test_new"]
        current_failed = ["t.A.test_1", "t.B.test_new"]
        merged = policy.merge_last_failed(previous, executed, current_failed)
        self.assertEqual(merged, ["t.A.test_2", "t.A.test_3", "t.A.test_1", "t.B.test_new"])

    def test_merge_fail_fast_keeps_unexecuted(self):
        previous = ["t.A.test_1", "t.A.test_2", "t.A.test_3"]
        # Fail-fast ran only the first and it failed again.
        executed = ["t.A.test_1"]
        current_failed = ["t.A.test_1"]
        merged = policy.merge_last_failed(previous, executed, current_failed)
        self.assertEqual(merged, ["t.A.test_2", "t.A.test_3", "t.A.test_1"])

    def test_merge_prunes_renamed_removed_ids(self):
        previous = ["gone.Old.test_x", "t.A.test_1", "t.A.test_2"]
        known = ["t.A.test_1", "t.A.test_2", "t.A.test_3"]
        merged = policy.merge_last_failed(
            previous, executed_ids=["t.A.test_1"], current_failed_ids=[], known_ids=known
        )
        self.assertEqual(merged, ["t.A.test_2"])
        self.assertNotIn("gone.Old.test_x", merged)

    def test_full_gate_timeout_helper(self):
        self.assertEqual(policy.full_gate_timeout_s({}), policy.FULL_GATE_TIMEOUT_S)
        self.assertEqual(policy.full_gate_timeout_s({"PRKS_E2E_FULL_TIMEOUT": "90"}), 90)
        self.assertEqual(policy.full_gate_timeout_s({"PRKS_E2E_FULL_TIMEOUT": "0"}), 0)
        self.assertEqual(
            policy.full_gate_timeout_s({"PRKS_E2E_FULL_TIMEOUT": "nope"}),
            policy.FULL_GATE_TIMEOUT_S,
        )


class PathMatchTests(unittest.TestCase):
    def test_glob_double_star(self):
        self.assertTrue(policy._path_matches("frontend/css/style.css", "frontend/css/**"))
        self.assertTrue(policy._path_matches("backend/work_tag_sync.py", "backend/work_tag_sync.py"))
        self.assertFalse(policy._path_matches("backend/server.py", "backend/work_tag_sync.py"))


class ListChangedPathsTests(unittest.TestCase):
    def test_invokes_git_diff_against_base_including_deletes(self):
        with mock.patch("subprocess.check_output") as check:
            check.side_effect = [
                "frontend/js/app.js\nfrontend/js/gone.js\n",  # diff vs base (incl D)
                "frontend/js/app.js\nbackend/x.py\n",  # local vs HEAD when base != HEAD
                "scripts/e2e\nfrontend/js/new.js\n",  # untracked
            ]
            paths = policy.list_changed_paths(
                Path("/tmp/repo"), base="origin/master", include_untracked=True
            )
            self.assertIn("frontend/js/app.js", paths)
            self.assertIn("frontend/js/gone.js", paths)
            self.assertIn("backend/x.py", paths)
            self.assertIn("frontend/js/new.js", paths)
            self.assertIn("scripts/e2e", paths)
            # Diff filter must include Deleted (D).
            first_cmd = check.call_args_list[0][0][0]
            self.assertIn("--diff-filter=ACMRD", first_cmd)

    def test_untracked_scripts_and_e2e_policy_are_discoverable(self):
        with mock.patch("subprocess.check_output") as check:
            check.side_effect = [
                "",  # diff vs HEAD
                "scripts/e2e\ntests/e2e/policy.py\ntmp/scratch.txt\n",
            ]
            paths = policy.list_changed_paths(
                Path("/tmp/repo"), base=None, include_untracked=True
            )
            self.assertIn("scripts/e2e", paths)
            self.assertIn("tests/e2e/policy.py", paths)
            self.assertNotIn("tmp/scratch.txt", paths)


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

    def test_affected_docs_only_is_success_noop(self):
        previous = os.environ.get("PRKS_E2E")
        os.environ["PRKS_E2E"] = "1"
        try:
            from tests.e2e import run as runner
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with mock.patch.object(runner, "ensure_chromium_installed") as ensure:
                with mock.patch.object(
                    runner, "list_changed_paths", return_value=["README.md", "docs/x.md"]
                ):
                    with redirect_stdout(buf):
                        code = runner.main(["--affected"])
            self.assertEqual(code, 0)
            self.assertIn("success no-op", buf.getvalue())
            ensure.assert_not_called()
        finally:
            if previous is None:
                os.environ.pop("PRKS_E2E", None)
            else:
                os.environ["PRKS_E2E"] = previous

    def test_fail_fast_last_failed_persists_unexecuted_priors(self):
        """Runner must merge on observed completions, not the pre-run selection."""
        previous_env = os.environ.get("PRKS_E2E")
        os.environ["PRKS_E2E"] = "1"
        try:
            from tests.e2e import run as runner

            selected = [
                "tests.e2e.fake.FailTests.test_1",
                "tests.e2e.fake.FailTests.test_2",
                "tests.e2e.fake.FailTests.test_3",
            ]
            with tempfile.TemporaryDirectory() as raw:
                last_path = Path(raw) / "e2e-last-failed.json"
                policy.save_last_failed(last_path, selected, meta={"tier": "full"})
                # Fail-fast: only test_1 completed (and failed); 2/3 never ran.
                observed = {"tests.e2e.fake.FailTests.test_1": 0.4}
                failed = ["tests.e2e.fake.FailTests.test_1"]

                with mock.patch.object(runner, "LAST_FAILED_PATH", last_path):
                    with mock.patch.object(runner, "REPO", Path(raw)):
                        with mock.patch.object(
                            runner, "ensure_chromium_installed"
                        ):
                            with mock.patch.object(
                                runner, "discover_test_ids", return_value=selected
                            ):
                                with mock.patch.object(
                                    runner,
                                    "run_serial",
                                    return_value=(False, observed, failed),
                                ) as serial:
                                    with mock.patch.object(
                                        runner, "load_timings", return_value={}
                                    ):
                                        with mock.patch.object(
                                            runner, "_persist_timings"
                                        ):
                                            with mock.patch.object(
                                                runner, "_print_slowest"
                                            ):
                                                code = runner.main(
                                                    [
                                                        "--last-failed",
                                                        "--fail-fast",
                                                        "--no-pointer-capture",
                                                        "--jobs",
                                                        "1",
                                                    ]
                                                )
                self.assertEqual(code, 1)
                serial.assert_called_once()
                # Selected set was [1,2,3]; if merge used selection, 2 and 3
                # would be dropped as "passed". They must remain unresolved.
                data = policy.load_last_failed(last_path)
                self.assertIsNotNone(data)
                self.assertEqual(
                    data["test_ids"],
                    [
                        "tests.e2e.fake.FailTests.test_2",
                        "tests.e2e.fake.FailTests.test_3",
                        "tests.e2e.fake.FailTests.test_1",
                    ],
                )
                self.assertEqual(data["meta"]["executed"], 1)
                self.assertEqual(data["meta"]["selected"], 3)
        finally:
            if previous_env is None:
                os.environ.pop("PRKS_E2E", None)
            else:
                os.environ["PRKS_E2E"] = previous_env

    def test_full_gate_arms_watchdog(self):
        previous = os.environ.get("PRKS_E2E")
        os.environ["PRKS_E2E"] = "1"
        try:
            from tests.e2e import run as runner

            ids = ["tests.e2e.fake.T.test_x"]
            cancel = mock.Mock()
            with tempfile.TemporaryDirectory() as raw:
                with mock.patch.object(runner, "REPO", Path(raw)):
                    with mock.patch.object(
                        runner, "LAST_FAILED_PATH", Path(raw) / "last.json"
                    ):
                        with mock.patch.object(runner, "ensure_chromium_installed"):
                            with mock.patch.object(
                                runner, "discover_test_ids", return_value=ids
                            ):
                                with mock.patch.object(
                                    runner,
                                    "run_serial",
                                    return_value=(True, {ids[0]: 0.1}, []),
                                ):
                                    with mock.patch.object(
                                        runner, "_run_pointer_capture", return_value=0
                                    ):
                                        with mock.patch.object(
                                            runner, "load_timings", return_value={}
                                        ):
                                            with mock.patch.object(
                                                runner, "_persist_timings"
                                            ):
                                                with mock.patch.object(
                                                    runner, "_print_slowest"
                                                ):
                                                    with mock.patch.object(
                                                        runner,
                                                        "_arm_full_gate_watchdog",
                                                        return_value=cancel,
                                                    ) as arm:
                                                        with mock.patch.object(
                                                            runner,
                                                            "full_gate_timeout_s",
                                                            return_value=1200,
                                                        ):
                                                            code = runner.main(
                                                                [
                                                                    "--jobs",
                                                                    "1",
                                                                    "--no-pointer-capture",
                                                                ]
                                                            )
            self.assertEqual(code, 0)
            arm.assert_called_once_with(1200)
            cancel.assert_called_once()
        finally:
            if previous is None:
                os.environ.pop("PRKS_E2E", None)
            else:
                os.environ["PRKS_E2E"] = previous


if __name__ == "__main__":
    unittest.main()
