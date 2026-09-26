"""Unit coverage for tests.e2e.policy — no Chromium."""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
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
    "tests.e2e.test_app.SettingsCategoryWorkflowTests.test_keyboard_category_navigation",
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
        self.assertEqual(rule, "pdf-annotations")
        self.assertEqual(feats, ("pdf-annotations",))
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
            ("backend/pdf_annotations.py", "pdf-annotations", ("pdf-annotations",)),
            ("backend/pdf_annotation_sync.py", "pdf-annotations", ("pdf-annotations",)),
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
            ("frontend/js/pdf-annotation-state.js", "pdf-annotations"),
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

    def test_full_e2e_ci_skips_docs_only(self):
        needed, reason = policy.full_e2e_ci_needed(
            ["docs/wiki/Testing.md", "AGENTS.md", "README.md"]
        )
        self.assertFalse(needed)
        self.assertIn("skipping", reason)

    def test_full_e2e_ci_skips_unit_only(self):
        needed, reason = policy.full_e2e_ci_needed(["tests/test_e2e_sharding.py"])
        self.assertFalse(needed)
        self.assertIn("skipping", reason)

    def test_full_e2e_ci_runs_for_production_and_gate_workflow(self):
        needed, reason = policy.full_e2e_ci_needed(["frontend/js/app.js"])
        self.assertTrue(needed)
        self.assertIn("running", reason)
        needed_wf, _reason = policy.full_e2e_ci_needed(
            [".github/workflows/e2e-gate.yml"]
        )
        self.assertTrue(needed_wf)

    def test_full_e2e_ci_empty_paths_fail_closed_to_run(self):
        needed, reason = policy.full_e2e_ci_needed([])
        self.assertTrue(needed)
        self.assertIn("fail closed", reason)

    def test_test_gate_workflow_is_e2e_framework_not_ignored(self):
        for path in (
            ".github/workflows/test-gate.yml",
            ".github/workflows/e2e-gate.yml",
        ):
            with self.subTest(path=path):
                rule, feats, skip, _note = policy.match_affected_path(path)
                self.assertEqual(rule, "e2e-framework")
                self.assertFalse(skip)
                # wait-async rides with the shared e2e-framework rule (#222).
                self.assertEqual(feats, ("smoke", "wait-async"))

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

    def test_save_commits_atomically_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "nested" / "e2e-last-failed.json"
            self.assertTrue(policy.save_last_failed(path, ["a.b.C.test_x"]))
            self.assertEqual(policy.load_last_failed(path)["test_ids"], ["a.b.C.test_x"])
            self.assertEqual(
                sorted(q.name for q in path.parent.iterdir()),
                ["e2e-last-failed.json"],
            )

    def test_failed_commit_preserves_previous_valid_state(self):
        """An interrupted write must never destroy usable last-failed state."""
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "e2e-last-failed.json"
            policy.save_last_failed(path, ["a.b.C.test_keep"], meta={"tier": "full"})
            before = path.read_bytes()

            with mock.patch("os.replace", side_effect=OSError("boom")):
                self.assertFalse(policy.save_last_failed(path, ["a.b.C.test_new"]))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(
                policy.load_last_failed(path)["test_ids"], ["a.b.C.test_keep"]
            )
            # The uncommitted temp file must not linger next to the real state.
            self.assertEqual(
                sorted(q.name for q in path.parent.iterdir()),
                ["e2e-last-failed.json"],
            )

    def test_failed_temp_write_preserves_previous_valid_state(self):
        def _fail_after_opening(fd, *args, **kwargs):
            os.close(fd)
            raise OSError("no space left on device")

        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "e2e-last-failed.json"
            policy.save_last_failed(path, ["a.b.C.test_keep"])
            before = path.read_bytes()

            with mock.patch("os.fdopen", side_effect=_fail_after_opening):
                self.assertFalse(policy.save_last_failed(path, ["a.b.C.test_new"]))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(
                policy.load_last_failed(path)["test_ids"], ["a.b.C.test_keep"]
            )
            self.assertEqual(
                sorted(q.name for q in path.parent.iterdir()),
                ["e2e-last-failed.json"],
            )

    def test_unavailable_temp_file_preserves_previous_valid_state(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "e2e-last-failed.json"
            policy.save_last_failed(path, ["a.b.C.test_keep"])
            before = path.read_bytes()

            with mock.patch("tempfile.mkstemp", side_effect=OSError("read-only")):
                self.assertFalse(policy.save_last_failed(path, ["a.b.C.test_new"]))
            self.assertEqual(path.read_bytes(), before)

    def test_concurrent_writers_do_not_share_a_temp_file(self):
        """Two runners in one checkout must never write the same uncommitted file."""
        seen = []
        real_mkstemp = tempfile.mkstemp

        def _record(*args, **kwargs):
            fd, name = real_mkstemp(*args, **kwargs)
            seen.append(name)
            return fd, name

        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "e2e-last-failed.json"
            with mock.patch("tempfile.mkstemp", side_effect=_record):
                self.assertTrue(policy.save_last_failed(path, ["a.b.C.test_one"]))
                self.assertTrue(policy.save_last_failed(path, ["a.b.C.test_two"]))
            self.assertEqual(len(seen), 2)
            self.assertNotEqual(seen[0], seen[1])
            for name in seen:
                self.assertEqual(Path(name).parent, path.parent)
            self.assertEqual(
                policy.load_last_failed(path)["test_ids"], ["a.b.C.test_two"]
            )

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

    def test_per_test_watchdog_helper(self):
        self.assertEqual(
            policy.per_test_watchdog_s({}), policy.TEST_WATCHDOG_TIMEOUT_S
        )
        self.assertEqual(policy.per_test_watchdog_s({"PRKS_E2E_TEST_WATCHDOG": "90"}), 90)
        self.assertEqual(policy.per_test_watchdog_s({"PRKS_E2E_TEST_WATCHDOG": "0"}), 0)
        self.assertEqual(
            policy.per_test_watchdog_s({"PRKS_E2E_TEST_WATCHDOG": "nope"}),
            policy.TEST_WATCHDOG_TIMEOUT_S,
        )
        # Distinct from the full-suite deadline.
        self.assertNotEqual(
            policy.TEST_WATCHDOG_TIMEOUT_S, policy.FULL_GATE_TIMEOUT_S
        )
        self.assertLess(policy.TEST_WATCHDOG_TIMEOUT_S, policy.FULL_GATE_TIMEOUT_S)


class PathMatchTests(unittest.TestCase):
    def test_glob_double_star(self):
        self.assertTrue(policy._path_matches("frontend/css/style.css", "frontend/css/**"))
        self.assertTrue(policy._path_matches("backend/work_tag_sync.py", "backend/work_tag_sync.py"))
        self.assertFalse(policy._path_matches("backend/server.py", "backend/work_tag_sync.py"))


def _git_ok(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["git"], returncode=0, stdout=stdout, stderr="")


def _git_fail(stderr: str, code: int = 128) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["git"], returncode=code, stdout="", stderr=stderr)


class ListChangedPathsTests(unittest.TestCase):
    def test_invokes_git_diff_against_base_including_deletes(self):
        with mock.patch("subprocess.run") as run:
            run.side_effect = [
                _git_ok("deadbeef\n"),  # rev-parse: base resolves to one commit
                # --name-status -z (incl D): status\0path\0...
                _git_ok("M\0frontend/js/app.js\0D\0frontend/js/gone.js\0"),
                _git_ok("M\0frontend/js/app.js\0M\0backend/x.py\0"),  # local vs HEAD
                _git_ok("scripts/e2e\nfrontend/js/new.js\n"),  # untracked
            ]
            paths = policy.list_changed_paths(
                Path("/tmp/repo"), base="origin/master", include_untracked=True
            )
            self.assertIn("frontend/js/app.js", paths)
            self.assertIn("frontend/js/gone.js", paths)
            self.assertIn("backend/x.py", paths)
            self.assertIn("frontend/js/new.js", paths)
            self.assertIn("scripts/e2e", paths)
            # Diff filter must include Deleted (D); name-status -z keeps both
            # rename images (covered below) and is what CI planning uses.
            diff_cmd = run.call_args_list[1][0][0]
            self.assertIn("--diff-filter=ACMRD", diff_cmd)
            self.assertIn("--name-status", diff_cmd)
            self.assertIn("-z", diff_cmd)
            self.assertIn("rev-parse", run.call_args_list[0][0][0])

    def test_name_status_z_keeps_both_rename_images(self):
        self.assertEqual(
            policy.paths_from_name_status_z(
                "R100\0frontend/js/app.js\0docs/app-notes.md\0"
            ),
            ["frontend/js/app.js", "docs/app-notes.md"],
        )
        self.assertEqual(
            policy.paths_from_name_status_z(
                "C080\0backend/server.py\0backend/server_copy.py\0M\0frontend/js/ui.js\0"
            ),
            ["backend/server.py", "backend/server_copy.py", "frontend/js/ui.js"],
        )
        self.assertEqual(policy.paths_from_name_status_z(""), [])
        self.assertEqual(policy.paths_from_name_status_z("M\0alone.py\0"), ["alone.py"])

    def test_rename_into_docs_still_requires_full_e2e(self):
        """Post-image alone is docs-only; pre-image production path must keep the gate."""
        needed_post_only, _reason = policy.full_e2e_ci_needed(["docs/app-notes.md"])
        self.assertFalse(needed_post_only)
        needed_both, reason = policy.full_e2e_ci_needed(
            ["frontend/js/app.js", "docs/app-notes.md"]
        )
        self.assertTrue(needed_both)
        self.assertIn("running", reason)

    def test_untracked_scripts_and_e2e_policy_are_discoverable(self):
        with mock.patch("subprocess.run") as run:
            run.side_effect = [
                _git_ok(""),  # diff vs HEAD
                _git_ok("scripts/e2e\ntests/e2e/policy.py\ntmp/scratch.txt\n"),
            ]
            paths = policy.list_changed_paths(
                Path("/tmp/repo"), base=None, include_untracked=True
            )
            self.assertIn("scripts/e2e", paths)
            self.assertIn("tests/e2e/policy.py", paths)
            self.assertNotIn("tmp/scratch.txt", paths)

    def test_genuine_empty_diff_is_not_an_error(self):
        """No changes must stay an ordinary empty result — not a discovery failure."""
        with mock.patch("subprocess.run") as run:
            run.side_effect = [_git_ok(""), _git_ok("")]
            self.assertEqual(
                policy.list_changed_paths(Path("/tmp/repo"), include_untracked=True), []
            )

    def test_invalid_base_ref_fails_closed(self):
        with mock.patch("subprocess.run") as run:
            run.side_effect = [_git_fail("fatal: bad revision 'origin/nope'")]
            with self.assertRaises(policy.ChangeDiscoveryError) as ctx:
                policy.list_changed_paths(Path("/tmp/repo"), base="origin/nope")
            message = str(ctx.exception)
            self.assertIn("origin/nope", message)
            self.assertIn("bad revision", message)

    def test_option_like_base_fails_closed_without_running_git(self):
        """A leading '-' would be parsed as a git option and report no changes."""
        with mock.patch("subprocess.run") as run:
            with self.assertRaises(policy.ChangeDiscoveryError) as ctx:
                policy.list_changed_paths(
                    Path("/tmp/repo"), base="--relative=definitely-no-such-prefix"
                )
            self.assertIn("cannot start with", str(ctx.exception))
            run.assert_not_called()

    def test_diff_commands_terminate_revision_parsing(self):
        """`--` stops git reading a base that is also a path as a pathspec."""
        with mock.patch("subprocess.run") as run:
            run.side_effect = [_git_ok("deadbeef\n"), _git_ok(""), _git_ok(""), _git_ok("")]
            policy.list_changed_paths(Path("/tmp/repo"), base="origin/master")
            diff_calls = [
                call[0][0] for call in run.call_args_list if "diff" in call[0][0]
            ]
            self.assertEqual(len(diff_calls), 2)
            for cmd in diff_calls:
                self.assertEqual(cmd[-1], "--")

    def test_missing_git_executable_fails_closed(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError("git")):
            with self.assertRaises(policy.ChangeDiscoveryError) as ctx:
                policy.list_changed_paths(Path("/tmp/repo"))
            self.assertIn("FileNotFoundError", str(ctx.exception))

    def test_local_diff_failure_against_explicit_base_fails_closed(self):
        """The secondary working-tree diff is required too — never silently skipped."""
        with mock.patch("subprocess.run") as run:
            run.side_effect = [
                _git_ok("deadbeef\n"),
                _git_ok("frontend/js/app.js\n"),
                _git_fail("fatal: not a git repository"),
            ]
            with self.assertRaises(policy.ChangeDiscoveryError) as ctx:
                policy.list_changed_paths(Path("/tmp/repo"), base="origin/master")
            self.assertIn("local change discovery", str(ctx.exception))

    def test_untracked_discovery_failure_fails_closed(self):
        with mock.patch("subprocess.run") as run:
            run.side_effect = [_git_ok(""), _git_fail("fatal: unable to read index")]
            with self.assertRaises(policy.ChangeDiscoveryError) as ctx:
                policy.list_changed_paths(Path("/tmp/repo"), include_untracked=True)
            self.assertIn("untracked change discovery", str(ctx.exception))

    def test_revision_range_base_fails_closed(self):
        """A range switches git diff to commit-vs-commit and drops the working tree."""
        with mock.patch("subprocess.run") as run:
            run.side_effect = [_git_fail("fatal: Needed a single revision")]
            with self.assertRaises(policy.ChangeDiscoveryError) as ctx:
                policy.list_changed_paths(Path("/tmp/repo"), base="release..main")
            message = str(ctx.exception)
            self.assertIn("release..main", message)
            self.assertIn("not a single revision", message)
            # Rejected before any diff runs.
            self.assertEqual(run.call_count, 1)
            self.assertIn("rev-parse", run.call_args_list[0][0][0])

    def test_untracked_failure_is_irrelevant_when_discovery_is_disabled(self):
        with mock.patch("subprocess.run") as run:
            run.side_effect = [_git_ok("M\0backend/server.py\0")]
            self.assertEqual(
                policy.list_changed_paths(Path("/tmp/repo"), include_untracked=False),
                ["backend/server.py"],
            )


@unittest.skipUnless(shutil.which("git"), "git executable not available")
class ListChangedPathsRealGitTests(unittest.TestCase):
    """The fail-closed contract against a real git process, not just mocks."""

    def _git(self, repo, *args):
        subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    def _seeded_repo(self, repo: Path) -> Path:
        """A repo with one commit touching backend/server.py."""
        self._git(repo, "init", "--quiet")
        (repo / "backend").mkdir()
        (repo / "backend" / "server.py").write_text("x = 1\n", encoding="utf-8")
        self._git(repo, "add", "backend/server.py")
        self._git(
            repo,
            "-c",
            "user.email=e2e@example.invalid",
            "-c",
            "user.name=E2E",
            "commit",
            "--quiet",
            "--no-gpg-sign",
            "-m",
            "seed",
        )
        return repo

    def test_unusable_head_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            self._git(repo, "init", "--quiet")
            # No commit yet: HEAD cannot be resolved, so discovery must not
            # report "nothing changed".
            with self.assertRaises(policy.ChangeDiscoveryError):
                policy.list_changed_paths(repo)

    def test_clean_checkout_reports_no_changes(self):
        with tempfile.TemporaryDirectory() as raw:
            repo = self._seeded_repo(Path(raw))
            self.assertEqual(policy.list_changed_paths(repo), [])
            (repo / "backend" / "server.py").write_text("x = 2\n", encoding="utf-8")
            self.assertEqual(policy.list_changed_paths(repo), ["backend/server.py"])

    def test_invalid_base_ref_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            self._git(repo, "init", "--quiet")
            with self.assertRaises(policy.ChangeDiscoveryError):
                policy.list_changed_paths(repo, base="origin/definitely-missing")

    def test_revision_range_base_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            repo = self._seeded_repo(Path(raw))
            self._git(repo, "branch", "other", "HEAD")
            # A range resolves for git, but answers a different question: the
            # working tree is left out entirely.
            (repo / "frontend").mkdir()
            (repo / "frontend" / "app.js").write_text("// x\n", encoding="utf-8")
            self._git(repo, "add", "frontend/app.js")
            self.assertIn("frontend/app.js", policy.list_changed_paths(repo, base="other"))
            with self.assertRaises(policy.ChangeDiscoveryError) as ctx:
                policy.list_changed_paths(repo, base="other..HEAD")
            self.assertIn("not a single revision", str(ctx.exception))

    def test_base_that_is_a_path_fails_closed(self):
        """`--base backend` is a pathspec to git, and would diff nothing at all."""
        with tempfile.TemporaryDirectory() as raw:
            repo = self._seeded_repo(Path(raw))
            with self.assertRaises(policy.ChangeDiscoveryError):
                policy.list_changed_paths(repo, base="backend")

    def test_rename_production_to_docs_keeps_both_images(self):
        """A prod→docs rename must still require the full E2E CI gate."""
        with tempfile.TemporaryDirectory() as raw:
            repo = self._seeded_repo(Path(raw))
            seed = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                text=True,
            ).strip()
            (repo / "docs").mkdir()
            self._git(repo, "mv", "backend/server.py", "docs/server-notes.md")
            self._git(
                repo,
                "-c",
                "user.email=e2e@example.invalid",
                "-c",
                "user.name=E2E",
                "commit",
                "--quiet",
                "--no-gpg-sign",
                "-m",
                "rename into docs",
            )
            paths = policy.list_changed_paths(
                repo, base=seed, include_untracked=False
            )
            self.assertIn("backend/server.py", paths)
            self.assertIn("docs/server-notes.md", paths)
            needed, reason = policy.full_e2e_ci_needed(paths)
            self.assertTrue(needed, reason)


class BenchmarkModeTests(unittest.TestCase):
    """Effective benchmark configuration — CLI flags are exported into env first."""

    def test_default_environment_is_representative(self):
        self.assertEqual(policy.benchmark_modes({}), ())
        self.assertEqual(policy.benchmark_modes({"PRKS_E2E_SEED_CACHE": "1"}), ())
        self.assertEqual(policy.benchmark_modes({"PRKS_E2E_PROFILE": "0"}), ())

    def test_profile_and_disabled_seed_cache_are_benchmark_modes(self):
        self.assertEqual(policy.benchmark_modes({"PRKS_E2E_PROFILE": "1"}), ("profile",))
        self.assertEqual(
            policy.benchmark_modes({"PRKS_E2E_SEED_CACHE": "off"}), ("no-seed-cache",)
        )
        self.assertEqual(
            policy.benchmark_modes(
                {"PRKS_E2E_PROFILE": "yes", "PRKS_E2E_SEED_CACHE": "false"}
            ),
            ("profile", "no-seed-cache"),
        )

    def test_env_truthiness_is_shared_with_the_harness(self):
        """One definition decides how the harness reads a switch and how the
        runner judges the same switch."""
        from tests.e2e import harness

        for raw, enabled in (("1", True), ("true", True), ("0", False), ("off", False)):
            self.assertEqual(policy.env_flag_enabled("X", environ={"X": raw}), enabled)
            with mock.patch.dict(os.environ, {"X": raw}, clear=False):
                self.assertEqual(harness._env_enabled("X"), enabled)
        self.assertTrue(policy.env_flag_enabled("X", default=True, environ={}))
        self.assertFalse(policy.env_flag_enabled("X", environ={}))


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

    def test_affected_discovery_failure_fails_closed(self):
        """A failed Git query must never look like a clean "nothing affected" run."""
        previous = os.environ.get("PRKS_E2E")
        os.environ["PRKS_E2E"] = "1"
        try:
            from tests.e2e import run as runner
            import io
            from contextlib import redirect_stderr, redirect_stdout

            out = io.StringIO()
            err = io.StringIO()
            failure = policy.ChangeDiscoveryError(
                "change discovery vs origin/nope failed: "
                "`git diff --name-status -z --diff-filter=ACMRD origin/nope` exited 128 "
                "— fatal: bad revision 'origin/nope'"
            )
            with mock.patch.object(runner, "ensure_chromium_installed") as ensure:
                with mock.patch.object(runner, "run_serial") as serial:
                    with mock.patch.object(
                        runner, "list_changed_paths", side_effect=failure
                    ):
                        with redirect_stdout(out), redirect_stderr(err):
                            code = runner.main(["--affected", "--base", "origin/nope"])
            self.assertEqual(code, 2)
            diagnostic = err.getvalue()
            self.assertIn("origin/nope", diagnostic)
            self.assertIn("bad revision", diagnostic)
            self.assertIn("refusing to report zero affected tests", diagnostic)
            self.assertNotIn("success no-op", out.getvalue())
            ensure.assert_not_called()
            serial.assert_not_called()
        finally:
            if previous is None:
                os.environ.pop("PRKS_E2E", None)
            else:
                os.environ["PRKS_E2E"] = previous

    def test_ci_plan_discovery_failure_fails_closed_to_run(self):
        """Unresolved --base (e.g. force-push before SHA) must still run the gate."""
        previous = os.environ.get("PRKS_E2E")
        os.environ["PRKS_E2E"] = "1"
        try:
            from tests.e2e import run as runner
            import io
            import json
            from contextlib import redirect_stderr, redirect_stdout

            out = io.StringIO()
            err = io.StringIO()
            failure = policy.ChangeDiscoveryError(
                "invalid --base 'deadbeef': not a single revision this repository resolves"
            )
            with mock.patch.object(
                runner, "list_changed_paths", side_effect=failure
            ):
                with redirect_stdout(out), redirect_stderr(err):
                    code = runner.main(["--ci-plan", "--base", "deadbeef"])
            self.assertEqual(code, 0)
            plan = json.loads(out.getvalue().strip())
            self.assertTrue(plan["run"])
            self.assertIn("fail closed", plan["reason"])
            self.assertIn("fail closed", err.getvalue())
        finally:
            if previous is None:
                os.environ.pop("PRKS_E2E", None)
            else:
                os.environ["PRKS_E2E"] = previous

    def test_external_full_shard_reports_targeted_and_preserves_sibling_timings(self):
        """A --shard slice of the full suite must not prune other shards' timings.

        Keep tier==full for the deadline supervisor (child env set here), but
        report/persist as targeted so merge_timings(known_ids=None) retains
        sibling history.
        """
        previous = os.environ.get("PRKS_E2E")
        os.environ["PRKS_E2E"] = "1"
        try:
            from tests.e2e import run as runner
            import io
            from contextlib import redirect_stderr, redirect_stdout

            ids = [
                "tests.e2e.runner_selfcheck_cases.PassingCases.test_first",
                "tests.e2e.runner_selfcheck_cases.PassingCases.test_second",
            ]
            out = io.StringIO()
            err = io.StringIO()
            # Isolate REPO so last-failed / timings writes cannot touch the
            # checkout's .tests/ (passing runs clear or prune last-failed).
            with tempfile.TemporaryDirectory() as raw:
                with mock.patch.object(runner, "REPO", Path(raw)):
                    with mock.patch.object(
                        runner, "discover_test_ids", return_value=ids
                    ):
                        with mock.patch.object(runner, "ensure_chromium_installed"):
                            with mock.patch.object(
                                runner,
                                "run_serial",
                                return_value=(True, {ids[0]: 1.0}, [], {}),
                            ):
                                with mock.patch.object(
                                    runner, "_persist_timings"
                                ) as persist:
                                    with mock.patch.dict(
                                        os.environ, {runner.FULL_GATE_CHILD_ENV: "1"}
                                    ):
                                        with redirect_stdout(out), redirect_stderr(
                                            err
                                        ):
                                            code = runner.main(
                                                [
                                                    "--shard",
                                                    "1/2",
                                                    "--jobs",
                                                    "1",
                                                    "--no-pointer-capture",
                                                ]
                                            )
            self.assertEqual(code, 0)
            text = out.getvalue()
            self.assertIn("NOT a full E2E gate", text)
            self.assertIn("E2E PASS (targeted)", text)
            self.assertNotIn("FULL REGRESSION GATE", text)
            persist.assert_called_once()
            _observed, known_ids = persist.call_args[0]
            self.assertIsNone(known_ids)
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
                                    return_value=(False, observed, failed, {}),
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

    def test_last_failed_stale_only_clears_and_succeeds(self):
        """All persisted IDs renamed/removed → prune file, exit 0 (not error)."""
        previous_env = os.environ.get("PRKS_E2E")
        os.environ["PRKS_E2E"] = "1"
        try:
            from tests.e2e import run as runner
            import io
            from contextlib import redirect_stdout

            known = ["tests.e2e.live.T.test_ok"]
            stale = [
                "tests.e2e.gone.Old.test_a",
                "tests.e2e.gone.Old.test_b",
            ]
            with tempfile.TemporaryDirectory() as raw:
                last_path = Path(raw) / "e2e-last-failed.json"
                policy.save_last_failed(last_path, stale, meta={"tier": "full"})
                buf = io.StringIO()
                with mock.patch.object(runner, "LAST_FAILED_PATH", last_path):
                    with mock.patch.object(runner, "REPO", Path(raw)):
                        with mock.patch.object(
                            runner, "discover_test_ids", return_value=known
                        ):
                            with mock.patch.object(
                                runner, "ensure_chromium_installed"
                            ) as ensure:
                                with redirect_stdout(buf):
                                    code = runner.main(["--last-failed"])
                self.assertEqual(code, 0)
                ensure.assert_not_called()
                self.assertFalse(last_path.is_file())
                self.assertIn("no known unresolved failures remain", buf.getvalue())
        finally:
            if previous_env is None:
                os.environ.pop("PRKS_E2E", None)
            else:
                os.environ["PRKS_E2E"] = previous_env

    def test_full_gate_parent_supervises_child(self):
        """Parent of a timed full gate re-execs under deadline supervision."""
        from tests.e2e import run as runner

        previous = os.environ.get("PRKS_E2E")
        child_key = runner.FULL_GATE_CHILD_ENV
        child_prev = os.environ.get(child_key)
        os.environ["PRKS_E2E"] = "1"
        os.environ.pop(child_key, None)
        try:
            ids = ["tests.e2e.fake.T.test_x"]
            with tempfile.TemporaryDirectory() as raw:
                with mock.patch.object(runner, "REPO", Path(raw)):
                    with mock.patch.object(
                        runner, "discover_test_ids", return_value=ids
                    ):
                        with mock.patch.object(
                            runner, "full_gate_timeout_s", return_value=1200
                        ):
                            with mock.patch.object(
                                runner, "_supervise_full_gate", return_value=0
                            ) as supervise:
                                with mock.patch.object(
                                    runner, "run_serial"
                                ) as serial:
                                    with mock.patch.object(
                                        runner, "ensure_chromium_installed"
                                    ) as ensure:
                                        code = runner.main(
                                            [
                                                "--jobs",
                                                "1",
                                                "--no-pointer-capture",
                                            ]
                                        )
            self.assertEqual(code, 0)
            supervise.assert_called_once()
            self.assertEqual(supervise.call_args[0][1], 1200)
            serial.assert_not_called()
            ensure.assert_not_called()
        finally:
            if previous is None:
                os.environ.pop("PRKS_E2E", None)
            else:
                os.environ["PRKS_E2E"] = previous
            if child_prev is None:
                os.environ.pop(child_key, None)
            else:
                os.environ[child_key] = child_prev

    def test_full_gate_child_runs_shards_and_pointer_under_same_process(self):
        """Supervised child runs E2E + pointer_capture without re-supervising."""
        from tests.e2e import run as runner

        previous = os.environ.get("PRKS_E2E")
        child_key = runner.FULL_GATE_CHILD_ENV
        child_prev = os.environ.get(child_key)
        os.environ["PRKS_E2E"] = "1"
        os.environ[child_key] = "1"
        try:
            ids = ["tests.e2e.fake.T.test_x"]
            with tempfile.TemporaryDirectory() as raw:
                with mock.patch.object(runner, "REPO", Path(raw)):
                    with mock.patch.object(
                        runner, "LAST_FAILED_PATH", Path(raw) / "last.json"
                    ):
                        with mock.patch.object(
                            runner, "ensure_chromium_installed"
                        ):
                            with mock.patch.object(
                                runner, "discover_test_ids", return_value=ids
                            ):
                                with mock.patch.object(
                                    runner,
                                    "run_serial",
                                    return_value=(True, {ids[0]: 0.1}, [], {}),
                                ):
                                    with mock.patch.object(
                                        runner,
                                        "_run_pointer_capture",
                                        return_value=0,
                                    ) as pointer:
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
                                                        "_supervise_full_gate",
                                                    ) as supervise:
                                                        with mock.patch.object(
                                                            runner,
                                                            "full_gate_timeout_s",
                                                            return_value=1200,
                                                        ):
                                                            code = runner.main(
                                                                ["--jobs", "1"]
                                                            )
            self.assertEqual(code, 0)
            supervise.assert_not_called()
            pointer.assert_called_once()
        finally:
            if previous is None:
                os.environ.pop("PRKS_E2E", None)
            else:
                os.environ["PRKS_E2E"] = previous
            if child_prev is None:
                os.environ.pop(child_key, None)
            else:
                os.environ[child_key] = child_prev

    def test_deadline_subprocess_kills_hung_child(self):
        """Real hung subprocess is killed and yields 124 — not a mocked watchdog."""
        import time

        from tests.e2e import run as runner

        py = sys.executable
        started = time.perf_counter()
        code = runner._run_with_deadline(
            [py, "-c", "import time; time.sleep(60)"],
            timeout_s=1,
        )
        elapsed = time.perf_counter() - started
        self.assertEqual(code, 124)
        self.assertLess(elapsed, 20.0)
        # Successful short child still returns its own exit code.
        code_ok = runner._run_with_deadline(
            [py, "-c", "raise SystemExit(42)"],
            timeout_s=10,
        )
        self.assertEqual(code_ok, 42)

    def test_deadline_supervisor_cleans_child_on_abnormal_exit(self):
        """Interrupting/terminating the supervisor must not orphan the child.

        Platform-aware: always covers KeyboardInterrupt cleanup with a real
        supervised subprocess. On POSIX, also SIGTERM the outer supervisor
        process (the `timeout 1200` path) and assert the child is gone.
        """
        import time

        from tests.e2e import run as runner

        def pid_alive(pid: int) -> bool:
            """True if pid is a live (non-zombie) process.

            Generic POSIX check is ``os.kill(pid, 0)``. When ``/proc`` exists
            (Linux), also treat zombies as not alive so a reaped-but-unwaited
            child cannot look like a running orphan.
            """
            if os.name == "nt":
                completed = subprocess.run(
                    ["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                out = completed.stdout or ""
                return str(pid) in out and "No tasks" not in out
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            except PermissionError:
                pass
            status_path = Path("/proc") / str(pid) / "status"
            if not status_path.is_file():
                return True
            try:
                text = status_path.read_text(encoding="utf-8")
            except OSError:
                return True
            for line in text.splitlines():
                if line.startswith("State:"):
                    # Zombies still occupy a pid; they are not running children.
                    return not line.split(":", 1)[1].strip().startswith("Z")
            return True

        def wait_until(predicate, timeout_s=10.0, interval_s=0.05):
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                if predicate():
                    return True
                time.sleep(interval_s)
            return False

        with tempfile.TemporaryDirectory() as raw:
            pid_path = Path(raw) / "supervised.pid"
            child_code = (
                "import os, pathlib, time\n"
                "pathlib.Path(%r).write_text(str(os.getpid()), encoding='utf-8')\n"
                "time.sleep(120)\n"
            ) % str(pid_path)

            # --- KeyboardInterrupt path (works on Windows + POSIX) ---
            original_popen = subprocess.Popen

            class InterruptAfterStart(original_popen):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self._raised_interrupt = False

                def wait(self, timeout=None):
                    # First wait() simulates Ctrl+C; later waits (tree cleanup)
                    # must still reap so we do not leave a zombie looking "alive".
                    if not self._raised_interrupt:
                        ready = wait_until(pid_path.is_file, timeout_s=10.0)
                        if not ready:
                            raise AssertionError("supervised child never wrote pid")
                        self._raised_interrupt = True
                        raise KeyboardInterrupt
                    return original_popen.wait(self, timeout=timeout)

            with mock.patch.object(runner.subprocess, "Popen", InterruptAfterStart):
                with self.assertRaises(KeyboardInterrupt):
                    runner._run_with_deadline(
                        [sys.executable, "-c", child_code],
                        timeout_s=60,
                    )

            child_pid = int(pid_path.read_text(encoding="utf-8").strip())
            self.assertTrue(
                wait_until(lambda: not pid_alive(child_pid), timeout_s=15.0),
                "KeyboardInterrupt left supervised child pid=%s alive" % child_pid,
            )

            # --- SIGTERM to outer supervisor (POSIX / external timeout) ---
            if os.name == "nt" or not hasattr(signal, "SIGTERM"):
                return

            pid_path.unlink(missing_ok=True)
            supervisor_code = (
                "import sys\n"
                "from tests.e2e.run import _run_with_deadline\n"
                "raise SystemExit(_run_with_deadline("
                "[sys.executable, '-c', sys.argv[1]], timeout_s=120))\n"
            )
            env = os.environ.copy()
            # Ensure the repo root is importable for `tests.e2e.run`.
            repo_root = str(Path(__file__).resolve().parents[1])
            prev_pp = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = (
                repo_root if not prev_pp else repo_root + os.pathsep + prev_pp
            )
            super_proc = subprocess.Popen(
                [sys.executable, "-c", supervisor_code, child_code],
                cwd=repo_root,
                env=env,
            )
            try:
                self.assertTrue(
                    wait_until(pid_path.is_file, timeout_s=10.0),
                    "SIGTERM-case supervised child never wrote pid",
                )
                child_pid = int(pid_path.read_text(encoding="utf-8").strip())
                self.assertTrue(pid_alive(child_pid))
                self.assertTrue(pid_alive(super_proc.pid))
                os.kill(super_proc.pid, signal.SIGTERM)
                try:
                    super_proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    super_proc.kill()
                    super_proc.wait(timeout=10)
                    self.fail("supervisor did not exit after SIGTERM")
                self.assertTrue(
                    wait_until(lambda: not pid_alive(child_pid), timeout_s=15.0),
                    "SIGTERM left supervised child pid=%s alive" % child_pid,
                )
            finally:
                if super_proc.poll() is None:
                    super_proc.kill()
                    try:
                        super_proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        pass

    def test_last_failed_stale_unlink_failure_skips_cleared_message(self):
        """Failed stale last-failed unlink must not claim the file was cleared."""
        previous_env = os.environ.get("PRKS_E2E")
        os.environ["PRKS_E2E"] = "1"
        try:
            from tests.e2e import run as runner
            import io
            from contextlib import redirect_stderr, redirect_stdout

            known = ["tests.e2e.live.T.test_ok"]
            stale = ["tests.e2e.gone.Old.test_a"]
            with tempfile.TemporaryDirectory() as raw:
                last_path = Path(raw) / "e2e-last-failed.json"
                policy.save_last_failed(last_path, stale, meta={"tier": "full"})
                buf = io.StringIO()
                err = io.StringIO()
                with mock.patch.object(runner, "LAST_FAILED_PATH", last_path):
                    with mock.patch.object(runner, "REPO", Path(raw)):
                        with mock.patch.object(
                            runner, "discover_test_ids", return_value=known
                        ):
                            with mock.patch.object(
                                runner, "ensure_chromium_installed"
                            ):
                                with mock.patch.object(
                                    Path,
                                    "unlink",
                                    side_effect=OSError("simulated unlink failure"),
                                ):
                                    with redirect_stdout(buf):
                                        with redirect_stderr(err):
                                            code = runner.main(["--last-failed"])
                self.assertEqual(code, 0)
                self.assertNotIn("cleared stale state", buf.getvalue())
                self.assertIn(
                    "could not unlink stale last-failed state", err.getvalue()
                )
                self.assertTrue(last_path.is_file())
        finally:
            if previous_env is None:
                os.environ.pop("PRKS_E2E", None)
            else:
                os.environ["PRKS_E2E"] = previous_env

    def _invoke_runner_history_case(
        self,
        repo,
        last_path,
        test_id,
        known_extra=(),
        observed=None,
        argv_extra=(),
        serial_result=None,
    ):
        """runner.main() over a temp repo with Chromium and the browser run stubbed.

        Returns (exit_code, _print_slowest mock).
        """
        from tests.e2e import run as runner

        if observed is None:
            observed = {test_id: 99.0}
        if serial_result is None:
            serial_result = (True, observed, [], {test_id: {"seed_build": 0.01}})
        with contextlib.ExitStack() as stack:
            enter = stack.enter_context
            enter(mock.patch.object(runner, "REPO", repo))
            enter(mock.patch.object(runner, "LAST_FAILED_PATH", last_path))
            enter(mock.patch.object(runner, "ensure_chromium_installed"))
            enter(
                mock.patch.object(
                    runner, "discover_test_ids", return_value=[test_id, *known_extra]
                )
            )
            enter(mock.patch.object(runner, "run_serial", return_value=serial_result))
            print_slow = enter(mock.patch.object(runner, "_print_slowest"))
            enter(mock.patch.object(runner, "_run_pointer_capture", return_value=0))
            code = runner.main(
                [test_id, "--jobs", "1", "--no-pointer-capture", *argv_extra]
            )
        return code, print_slow

    def test_env_only_benchmark_modes_do_not_persist_history(self):
        """PRKS_E2E_PROFILE / PRKS_E2E_SEED_CACHE=0 are benchmark runs without a flag."""
        from tests.e2e.sharding import load_timings, save_timings

        test_id = "tests.e2e.fake.BenchTests.test_x"
        prior_failed = ["tests.e2e.fake.Other.test_keep"]
        prior_timings = {test_id: 1.25, prior_failed[0]: 2.0}
        observed = {test_id: 99.0}

        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            (repo / ".tests").mkdir()
            timings_path = repo / ".tests" / "e2e-timings.json"
            last_path = repo / ".tests" / "e2e-last-failed.json"
            save_timings(timings_path, prior_timings)
            policy.save_last_failed(last_path, prior_failed, meta={"tier": "full"})
            prior_timings_bytes = timings_path.read_bytes()
            prior_last_bytes = last_path.read_bytes()

            def _run(env):
                timings_path.write_bytes(prior_timings_bytes)
                last_path.write_bytes(prior_last_bytes)
                with mock.patch.dict(os.environ, {"PRKS_E2E": "1"}, clear=False):
                    os.environ.pop("PRKS_E2E_PROFILE", None)
                    os.environ.pop("PRKS_E2E_SEED_CACHE", None)
                    os.environ.update(env)
                    return self._invoke_runner_history_case(
                        repo,
                        last_path,
                        test_id,
                        known_extra=prior_failed,
                        observed=observed,
                    )

            for env in (
                {"PRKS_E2E_PROFILE": "1"},
                {"PRKS_E2E_SEED_CACHE": "0"},
                {"PRKS_E2E_PROFILE": "1", "PRKS_E2E_SEED_CACHE": "0"},
            ):
                code, print_slow = _run(env)
                self.assertEqual(code, 0)
                print_slow.assert_called_once()
                self.assertEqual(
                    timings_path.read_bytes(),
                    prior_timings_bytes,
                    "env=%s must leave timing history untouched" % env,
                )
                self.assertEqual(
                    last_path.read_bytes(),
                    prior_last_bytes,
                    "env=%s must leave last-failed untouched" % env,
                )

            # An explicitly-default seed cache is a representative run: it persists.
            code, _ = _run({"PRKS_E2E_SEED_CACHE": "1"})
            self.assertEqual(code, 0)
            self.assertEqual(load_timings(timings_path)[test_id], 99.0)

    def test_benchmark_flags_do_not_leak_into_a_later_in_process_run(self):
        """CLI mode exports are scoped to one main(); the next run persists again."""
        from tests.e2e.sharding import load_timings, save_timings

        test_id = "tests.e2e.fake.BenchTests.test_x"
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            (repo / ".tests").mkdir()
            timings_path = repo / ".tests" / "e2e-timings.json"
            last_path = repo / ".tests" / "e2e-last-failed.json"
            save_timings(timings_path, {test_id: 1.25})

            with mock.patch.dict(os.environ, {"PRKS_E2E": "1"}, clear=False):
                os.environ.pop("PRKS_E2E_PROFILE", None)
                os.environ.pop("PRKS_E2E_SEED_CACHE", None)

                code, _ = self._invoke_runner_history_case(
                    repo, last_path, test_id, argv_extra=["--profile"]
                )
                self.assertEqual(code, 0)
                self.assertEqual(load_timings(timings_path)[test_id], 1.25)
                # The benchmark run must not leave the process in benchmark mode.
                self.assertEqual(policy.benchmark_modes(os.environ), ())

                code, _ = self._invoke_runner_history_case(repo, last_path, test_id)
                self.assertEqual(code, 0)
                self.assertEqual(load_timings(timings_path)[test_id], 99.0)

    def test_env_only_profiling_still_prints_the_infrastructure_profile(self):
        """PRKS_E2E_PROFILE=1 pays the profiling cost, so it must show the report."""
        import io
        from contextlib import redirect_stdout

        test_id = "tests.e2e.fake.BenchTests.test_x"
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            (repo / ".tests").mkdir()
            last_path = repo / ".tests" / "e2e-last-failed.json"
            out = io.StringIO()
            with mock.patch.dict(os.environ, {"PRKS_E2E": "1"}, clear=False):
                os.environ.pop("PRKS_E2E_SEED_CACHE", None)
                os.environ["PRKS_E2E_PROFILE"] = "1"
                with redirect_stdout(out):
                    code, _ = self._invoke_runner_history_case(repo, last_path, test_id)
            self.assertEqual(code, 0)
            printed = out.getvalue()
            self.assertIn("E2E infrastructure profile", printed)
            self.assertIn("seed_build", printed)
            self.assertIn("benchmark mode (profile)", printed)

    def test_benchmark_mode_does_not_clear_stale_last_failed_state(self):
        """Pruning the stale file is a history mutation; benchmark runs skip it."""
        import io
        from contextlib import redirect_stdout
        from tests.e2e import run as runner

        known = ["tests.e2e.live.T.test_ok"]
        stale = ["tests.e2e.gone.Old.test_a"]
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            last_path = repo / "e2e-last-failed.json"
            policy.save_last_failed(last_path, stale, meta={"tier": "full"})
            before = last_path.read_bytes()

            out = io.StringIO()
            with mock.patch.dict(os.environ, {"PRKS_E2E": "1"}, clear=False):
                os.environ.pop("PRKS_E2E_PROFILE", None)
                os.environ.pop("PRKS_E2E_SEED_CACHE", None)
                with contextlib.ExitStack() as stack:
                    enter = stack.enter_context
                    enter(mock.patch.object(runner, "REPO", repo))
                    enter(mock.patch.object(runner, "LAST_FAILED_PATH", last_path))
                    enter(
                        mock.patch.object(
                            runner, "discover_test_ids", return_value=known
                        )
                    )
                    ensure = enter(
                        mock.patch.object(runner, "ensure_chromium_installed")
                    )
                    with redirect_stdout(out):
                        code = runner.main(["--last-failed", "--profile"])
            self.assertEqual(code, 0)
            ensure.assert_not_called()
            self.assertTrue(last_path.is_file())
            self.assertEqual(last_path.read_bytes(), before)
            self.assertIn("leaves the state untouched", out.getvalue())

    def test_failed_last_failed_write_warns_and_keeps_previous_state(self):
        """A last-failed write that never commits is reported, not silently lost."""
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from tests.e2e import run as runner

        test_id = "tests.e2e.fake.BenchTests.test_x"
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            (repo / ".tests").mkdir()
            last_path = repo / ".tests" / "e2e-last-failed.json"
            policy.save_last_failed(last_path, ["tests.e2e.fake.Other.test_keep"])
            prior_last_bytes = last_path.read_bytes()

            out = io.StringIO()
            err = io.StringIO()
            with mock.patch.dict(os.environ, {"PRKS_E2E": "1"}, clear=False):
                os.environ.pop("PRKS_E2E_PROFILE", None)
                os.environ.pop("PRKS_E2E_SEED_CACHE", None)
                with mock.patch.object(
                    runner, "save_last_failed", return_value=False
                ) as save:
                    with redirect_stdout(out), redirect_stderr(err):
                        code, _ = self._invoke_runner_history_case(
                            repo,
                            last_path,
                            test_id,
                            serial_result=(False, {test_id: 12.0}, [test_id], {}),
                        )
            self.assertNotEqual(code, 0)
            save.assert_called_once()
            self.assertIn("could not write last-failed state", err.getvalue())
            self.assertNotIn("Wrote last-failed", out.getvalue())
            self.assertEqual(last_path.read_bytes(), prior_last_bytes)

    def test_benchmark_modes_do_not_persist_timing_or_last_failed_history(self):
        """--profile / --no-seed-cache must not train LPT timings or last-failed."""
        from tests.e2e import run as runner
        from tests.e2e.sharding import load_timings, save_timings

        previous = os.environ.get("PRKS_E2E")
        profile_prev = os.environ.get("PRKS_E2E_PROFILE")
        seed_prev = os.environ.get("PRKS_E2E_SEED_CACHE")
        os.environ["PRKS_E2E"] = "1"
        os.environ.pop("PRKS_E2E_PROFILE", None)
        os.environ.pop("PRKS_E2E_SEED_CACHE", None)
        try:
            test_id = "tests.e2e.fake.BenchTests.test_x"
            prior_failed = ["tests.e2e.fake.Other.test_keep"]
            prior_timings = {test_id: 1.25, prior_failed[0]: 2.0}
            # Distinct from prior so a mistaken persist is obvious.
            observed = {test_id: 99.0}

            with tempfile.TemporaryDirectory() as raw:
                repo = Path(raw)
                tests_dir = repo / ".tests"
                tests_dir.mkdir()
                timings_path = tests_dir / "e2e-timings.json"
                last_path = tests_dir / "e2e-last-failed.json"
                save_timings(timings_path, prior_timings)
                policy.save_last_failed(
                    last_path, prior_failed, meta={"tier": "full"}
                )
                prior_timings_bytes = timings_path.read_bytes()
                prior_last_bytes = last_path.read_bytes()

                def _invoke(extra_flags):
                    return self._invoke_runner_history_case(
                        repo,
                        last_path,
                        test_id,
                        known_extra=prior_failed,
                        observed=observed,
                        argv_extra=extra_flags,
                    )

                for flags in (
                    ["--profile"],
                    ["--no-seed-cache"],
                    ["--profile", "--no-seed-cache"],
                ):
                    timings_path.write_bytes(prior_timings_bytes)
                    last_path.write_bytes(prior_last_bytes)
                    code, print_slow = _invoke(flags)
                    self.assertEqual(code, 0)
                    print_slow.assert_called_once()
                    self.assertEqual(
                        timings_path.read_bytes(),
                        prior_timings_bytes,
                        "benchmark flags=%s must leave timing history untouched"
                        % flags,
                    )
                    self.assertEqual(
                        last_path.read_bytes(),
                        prior_last_bytes,
                        "benchmark flags=%s must leave last-failed untouched"
                        % flags,
                    )
                    os.environ.pop("PRKS_E2E_PROFILE", None)
                    os.environ.pop("PRKS_E2E_SEED_CACHE", None)

                timings_path.write_bytes(prior_timings_bytes)
                last_path.write_bytes(prior_last_bytes)
                code, print_slow = _invoke([])
                self.assertEqual(code, 0)
                print_slow.assert_called_once()
                loaded = load_timings(timings_path)
                self.assertEqual(loaded[test_id], 99.0)
                self.assertNotEqual(timings_path.read_bytes(), prior_timings_bytes)
                # Ordinary success still merges last-failed (unexecuted priors remain).
                data = policy.load_last_failed(last_path)
                self.assertIsNotNone(data)
                self.assertEqual(data["test_ids"], prior_failed)
        finally:
            if previous is None:
                os.environ.pop("PRKS_E2E", None)
            else:
                os.environ["PRKS_E2E"] = previous
            if profile_prev is None:
                os.environ.pop("PRKS_E2E_PROFILE", None)
            else:
                os.environ["PRKS_E2E_PROFILE"] = profile_prev
            if seed_prev is None:
                os.environ.pop("PRKS_E2E_SEED_CACHE", None)
            else:
                os.environ["PRKS_E2E_SEED_CACHE"] = seed_prev

    def test_no_sigalrm_watchdog_in_runner(self):
        """Full-gate deadline must not depend on POSIX-only alarm APIs."""
        import ast
        from tests.e2e import run as runner

        path = Path(runner.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        self.assertNotIn("SIGALRM", names)
        self.assertNotIn("setitimer", names)
        self.assertNotIn("ITIMER_REAL", names)
        self.assertTrue(hasattr(runner, "_run_with_deadline"))
        self.assertTrue(hasattr(runner, "_supervise_full_gate"))
        self.assertFalse(hasattr(runner, "_arm_full_gate_watchdog"))
        self.assertFalse(hasattr(runner, "FullGateTimeoutError"))


if __name__ == "__main__":
    unittest.main()
