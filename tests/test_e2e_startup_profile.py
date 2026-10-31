"""Unit contracts for E2E startup/readiness profiling; no Chromium required."""
from __future__ import annotations

import ast
import os
import re
import unittest
from pathlib import Path
from unittest import mock

from tests.e2e import harness


class GraphNavigationPhaseProfileTests(unittest.TestCase):
    def tearDown(self):
        harness.finish_test_profile("graph-phase")
        harness.finish_test_profile("unprofiled")

    def test_phase_is_opt_in_and_records_success_and_failure(self):
        with mock.patch.dict(os.environ, {"PRKS_E2E_PROFILE": "0"}):
            harness.start_test_profile("unprofiled")
            with mock.patch.object(harness.time, "perf_counter") as clock:
                with harness.profile_e2e_phase("graph_nav_roundtrip_concept"):
                    pass
                clock.assert_not_called()
            self.assertEqual(harness.finish_test_profile("unprofiled"), {})

        with mock.patch.dict(os.environ, {"PRKS_E2E_PROFILE": "1"}):
            harness.start_test_profile("graph-phase")
            try:
                with mock.patch.object(harness.time, "perf_counter",
                                       side_effect=[10, 14, 20, 23, 30, 32]):
                    with harness.profile_e2e_phase("graph_nav_roundtrip_concept"):
                        pass
                    with harness.profile_e2e_phase("graph_nav_roundtrip_concept"):
                        pass
                    with self.assertRaisesRegex(ValueError, "expected failure"):
                        with harness.profile_e2e_phase("graph_nav_uncached_graph"):
                            raise ValueError("expected failure")
                phases = harness.finish_test_profile("graph-phase")
            finally:
                harness.finish_test_profile("graph-phase")
        self.assertEqual(phases["graph_nav_roundtrip_concept"], 7.0)
        self.assertEqual(phases["graph_nav_uncached_graph_failed"], 2.0)

    def test_navigation_phase_is_diagnostic_not_additive_infrastructure(self):
        from tests.e2e import run as runner

        self.assertEqual(
            runner._profile_infrastructure_total({
                "server_start": 2.0,
                "async_wait": 3.0,
                "graph_nav_roundtrip_concept": 4.0,
                "graph_nav_roundtrip_concept_failed": 1.0,
            }),
            5.0,
        )


class ArgumentOverlayPhaseProfileTests(unittest.TestCase):
    def test_browser_phase_samples_are_opt_in_and_reject_invalid_values(self):
        with mock.patch.dict(os.environ, {"PRKS_E2E_PROFILE": "0"}):
            harness.start_test_profile("browser-disabled")
            harness.record_e2e_phase_seconds("arg_overlay_target_write_base", 0.25)
            self.assertEqual(harness.finish_test_profile("browser-disabled"), {})

        with mock.patch.dict(os.environ, {"PRKS_E2E_PROFILE": "1"}):
            harness.start_test_profile("browser-timed")
            try:
                harness.record_e2e_phase_seconds("arg_overlay_target_write_base", 0.25)
                harness.record_e2e_phase_seconds("arg_overlay_target_write_base", 0.375)
                for bad in (-1.0, float("nan"), float("inf")):
                    with self.assertRaises(ValueError):
                        harness.record_e2e_phase_seconds("arg_overlay_target_write_base", bad)
                phases = harness.finish_test_profile("browser-timed")
            finally:
                harness.finish_test_profile("browser-timed")
        self.assertEqual(phases, {"arg_overlay_target_write_base": 0.625})

    def test_argument_interactions_do_not_inflate_infrastructure_ranking(self):
        from tests.e2e import run as runner

        self.assertEqual(
            runner._profile_infrastructure_total({
                "server_start": 2.0,
                "async_wait": 3.0,
                "arg_overlay_setup": 4.0,
                "arg_overlay_verify_graph": 1.5,
                "arg_overlay_verify_graph_failed": 0.5,
            }),
            5.0,
        )



class PdfAnnotationRollbackPhaseProfileTests(unittest.TestCase):
    def test_detailed_phases_do_not_double_count_infrastructure(self):
        from tests.e2e import run as runner

        phases = {
            "server_start": 2.0,
            "async_wait": 3.0,
            "pdf_rollback_setup": 4.0,
            "pdf_rollback_fail_other_save": 2.5,
            "pdf_rollback_verify_late_failed": 1.0,
        }
        # Breakdown remains intact for the manual benchmark/phase report.
        self.assertEqual(phases["pdf_rollback_fail_other_save"], 2.5)
        self.assertEqual(runner._profile_infrastructure_total(phases), 5.0)



class PdfRollbackWorkflowTargetTests(unittest.TestCase):
    def test_manual_profiler_names_an_existing_browser_test(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (
            root / ".github/workflows/e2e-pdf-annotation-rollback-profile.yml"
        ).read_text(encoding="utf-8")
        targets = re.findall(
            r"^\s*TARGET:\s*(tests\.e2e\.[A-Za-z_0-9.]+)\s*$",
            workflow,
            flags=re.MULTILINE,
        )
        self.assertEqual(len(targets), 1, "expected exactly one manual profile target")
        module, class_name, method_name = targets[0].rsplit(".", 2)
        self.assertTrue(method_name.startswith("test_"))
        module_path = root.joinpath(*module.split(".")).with_suffix(".py")
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        classes = [
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        ]
        self.assertEqual(len(classes), 1, "manual profile class must exist")
        methods = [
            node for node in classes[0].body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == method_name
        ]
        self.assertEqual(len(methods), 1, "manual profile method must be in that class")


class OfflinePersonProfileWorkflowTargetTests(unittest.TestCase):
    def test_both_manual_targets_and_stage_names_exist(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (
            root / ".github/workflows/e2e-offline-person-profile.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch: {}", workflow)
        self.assertNotIn("inputs:", workflow)
        self.assertIn("for SCENARIO in create edit; do", workflow)
        targets = re.findall(
            r"^\s*(create|edit)\) TARGET=(tests\.e2e\.[A-Za-z_0-9.]+) ;;$",
            workflow,
            flags=re.MULTILINE,
        )
        self.assertEqual({mode for mode, _ in targets}, {"create", "edit"})
        self.assertEqual(len(targets), 2)
        names = set(re.findall(r'"(person_(?:create|edit)_[a-z_]+)"', workflow))
        helper_tree = ast.parse(
            (root / "tests/e2e/test_app.py").read_text(encoding="utf-8")
        )
        nav_helpers = [
            node for node in helper_tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_open_work_from_home"
        ]
        self.assertEqual(len(nav_helpers), 1)
        # ast.walk visits nested calls breadth-first, not in source order.
        # Sort the stage calls by their locations before asserting the real
        # interaction sequence; the separate check below asserts that the
        # click/card timers and the opt-in trial stay inside inclusive recent.
        helper_stages = [
            call.args[0].value
            for call in sorted(
                (
                    node for node in ast.walk(nav_helpers[0])
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "stage"
                    and len(node.args) == 1
                    and isinstance(node.args[0], ast.Constant)
                ),
                key=lambda node: (node.lineno, node.col_offset),
            )
        ]
        self.assertEqual(
            helper_stages,
            ["folders", "recent", "recent_trial", "recent_click", "recent_card", "card_click", "work_ready"],
        )
        recent_scopes = [
            node for node in ast.walk(nav_helpers[0])
            if isinstance(node, ast.With)
            and any(
                isinstance(item.context_expr, ast.Call)
                and isinstance(item.context_expr.func, ast.Name)
                and item.context_expr.func.id == "stage"
                and len(item.context_expr.args) == 1
                and isinstance(item.context_expr.args[0], ast.Constant)
                and item.context_expr.args[0].value == "recent"
                for item in node.items
            )
        ]
        self.assertEqual(len(recent_scopes), 1)
        self.assertEqual(
            [
                item.context_expr.args[0].value
                for node in recent_scopes[0].body
                if isinstance(node, ast.With)
                for item in node.items
                if isinstance(item.context_expr, ast.Call)
                and isinstance(item.context_expr.func, ast.Name)
                and item.context_expr.func.id == "stage"
            ],
            ["recent_click", "recent_card"],
        )
        # Only the manual probe enters this conditional; ordinary navigation
        # still runs the original click and card assertion unmodified.
        trial_scopes = [
            node for stmt in recent_scopes[0].body
            if isinstance(stmt, ast.If)
            for node in ast.walk(stmt)
            if isinstance(node, ast.With)
            and any(
                isinstance(item.context_expr, ast.Call)
                and isinstance(item.context_expr.func, ast.Name)
                and item.context_expr.func.id == "stage"
                and len(item.context_expr.args) == 1
                and isinstance(item.context_expr.args[0], ast.Constant)
                and item.context_expr.args[0].value == "recent_trial"
                for item in node.items
            )
        ]
        self.assertEqual(len(trial_scopes), 1)
        self.assertLess(
            trial_scopes[0].lineno,
            next(node.lineno for node in recent_scopes[0].body
                 if isinstance(node, ast.With)),
        )
        for mode, target in targets:
            module, class_name, method_name = target.rsplit(".", 2)
            file = root.joinpath(*module.split(".")).with_suffix(".py")
            tree = ast.parse(file.read_text(encoding="utf-8"))
            classes = [
                node for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == class_name
            ]
            self.assertEqual(len(classes), 1, target)
            methods = [
                node for node in classes[0].body
                if isinstance(node, ast.FunctionDef) and node.name == method_name
            ]
            self.assertEqual(len(methods), 1, target)
            # The create scenario invokes a shared UI helper; its nested
            # role-link stages are deliberately outside the selected test body.
            nodes = list(methods)
            if mode == "create":
                # The helper's timers belong to this *inclusive* phase only.
                # Merely finding the helper definition would not ensure the
                # measured test still calls it under person_create_role_link.
                link_scopes = [
                    node for node in ast.walk(methods[0])
                    if isinstance(node, ast.With)
                    and any(
                        isinstance(item.context_expr, ast.Call)
                        and isinstance(item.context_expr.func, ast.Name)
                        and item.context_expr.func.id == "profile_e2e_phase"
                        and len(item.context_expr.args) == 1
                        and isinstance(item.context_expr.args[0], ast.Constant)
                        and item.context_expr.args[0].value == "person_create_role_link"
                        for item in node.items
                    )
                ]
                self.assertEqual(len(link_scopes), 1, target)
                def is_link_call(node):
                    return (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "self"
                        and node.func.attr == "link_as_author"
                    )
                all_calls = [node for node in ast.walk(methods[0])
                             if is_link_call(node)]
                scoped_calls = [
                    node for stmt in link_scopes[0].body
                    for node in ast.walk(stmt) if is_link_call(node)
                ]
                self.assertEqual(len(all_calls), 1, target)
                self.assertEqual(len(scoped_calls), 1, target)
                helpers = [
                    node for node in classes[0].body
                    if isinstance(node, ast.FunctionDef) and node.name == "link_as_author"
                ]
                self.assertEqual(len(helpers), 1, target)
                nodes += helpers
            phases = {
                call.args[0].value
                for node in nodes
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "profile_e2e_phase"
                and call.args
                and isinstance(call.args[0], ast.Constant)
            }
            nav_names = {
                name for name in names
                if name.startswith(f"person_{mode}_link_nav_")
            }
            self.assertEqual(
                nav_names,
                {
                    f"person_{mode}_link_nav_{stage}"
                    for stage in ("folders", "recent", "recent_trial", "recent_click", "recent_card", "card_click", "work_ready")
                },
                target,
            )
            # The helper constructs its stage names from the opt-in prefix, so
            # their literals live in test_app rather than either test method.
            self.assertEqual(
                phases,
                {name for name in names if name.startswith(f"person_{mode}_")} - nav_names,
                target,
            )
            nav_scopes = [
                node for parent in nodes for node in ast.walk(parent)
                if isinstance(node, ast.With)
                and any(
                    isinstance(item.context_expr, ast.Call)
                    and isinstance(item.context_expr.func, ast.Name)
                    and item.context_expr.func.id == "profile_e2e_phase"
                    and len(item.context_expr.args) == 1
                    and isinstance(item.context_expr.args[0], ast.Constant)
                    and item.context_expr.args[0].value == f"person_{mode}_link_nav"
                    for item in node.items
                )
            ]
            self.assertEqual(len(nav_scopes), 1, target)
            nav_calls = [
                call for stmt in nav_scopes[0].body for call in ast.walk(stmt)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "_open_work_from_home"
            ]
            self.assertEqual(len(nav_calls), 1, target)
            self.assertEqual(
                [(kw.arg, kw.value.value) for kw in nav_calls[0].keywords
                 if isinstance(kw.value, ast.Constant)],
                [("profile_prefix", f"person_{mode}")],
                target,
            )

    def test_person_phase_timers_are_not_additive_infrastructure(self):
        from tests.e2e import run as runner

        self.assertEqual(runner._profile_infrastructure_total({
            "server_start": 1.0,
            "async_wait": 2.0,
            "person_create_offline_reload": 8.0,
            "person_edit_reconnect_settle": 9.0,
        }), 3.0)


def _fake_library(_storage_root, **_kwargs):
    """Stands in for the disposable PostgreSQL Library these timing tests never reach."""
    return mock.Mock(server_env=mock.Mock(return_value={}))


class StartupReadinessProfileTests(unittest.TestCase):
    def test_server_subphases_keep_http_readiness_and_inclusive_total(self):
        with mock.patch.dict(os.environ, {"PRKS_E2E_PROFILE": "1"}):
            with (
                mock.patch.object(harness, "find_free_port", return_value=42002),
                mock.patch.object(harness, "apply_playwright_browser_env"),
                mock.patch.object(harness, "provision_library", _fake_library),
                mock.patch.object(harness.AppServer, "_spawn") as spawn,
                mock.patch.object(harness, "wait_http") as wait_http,
            ):
                server = harness.AppServer()
                try:
                    harness.start_test_profile("server-profile")
                    try:
                        with mock.patch.object(
                            harness.time, "perf_counter", side_effect=[100, 102, 107]
                        ):
                            self.assertIs(server.start(), server)
                        phases = harness.finish_test_profile("server-profile")
                    finally:
                        harness.finish_test_profile("server-profile")
                    spawn.assert_called_once()
                    wait_http.assert_called_once_with(
                        "http://127.0.0.1:42002/api/works"
                    )
                    self.assertEqual(phases["server_spawn"], 2.0)
                    self.assertEqual(phases["server_http_ready"], 5.0)
                    self.assertEqual(phases["server_start"], 7.0)
                    self.assertAlmostEqual(
                        phases["server_spawn"] + phases["server_http_ready"],
                        phases["server_start"],
                        delta=0.00001,
                    )
                finally:
                    server.stop()

    def test_failed_retry_is_separate_from_successful_server_breakdown(self):
        with mock.patch.dict(os.environ, {"PRKS_E2E_PROFILE": "1"}):
            with (
                mock.patch.object(
                    harness, "find_free_port", side_effect=[42002, 42004]
                ),
                mock.patch.object(harness, "apply_playwright_browser_env"),
                mock.patch.object(harness, "provision_library", _fake_library),
                mock.patch.object(harness.AppServer, "_spawn") as spawn,
                mock.patch.object(
                    harness, "wait_http", side_effect=[RuntimeError("bind"), None]
                ) as wait_http,
                mock.patch.object(harness, "_address_in_use", return_value=True),
            ):
                server = harness.AppServer()
                try:
                    harness.start_test_profile("server-retry")
                    try:
                        with mock.patch.object(
                            harness.time,
                            "perf_counter",
                            side_effect=[100, 102, 105, 110, 112, 117],
                        ):
                            self.assertIs(server.start(), server)
                        phases = harness.finish_test_profile("server-retry")
                    finally:
                        harness.finish_test_profile("server-retry")
                    self.assertEqual(spawn.call_count, 2)
                    self.assertEqual(wait_http.call_count, 2)
                    self.assertEqual(server.port, 42004)
                    self.assertEqual(phases["server_spawn"], 2.0)
                    self.assertEqual(phases["server_http_ready"], 5.0)
                    self.assertEqual(phases["server_start"], 7.0)
                    self.assertEqual(phases["server_spawn_failed"], 2.0)
                    self.assertEqual(phases["server_http_ready_failed"], 3.0)
                finally:
                    server.stop()

    def test_virtual_clock_opt_in_is_installed_before_app_navigation(self):
        # Mock Playwright so the ordering contract runs without Chromium.
        for enabled in (False, True):
            browser = mock.Mock()
            page = browser.new_context.return_value.new_page.return_value
            calls = []
            page.clock.install.side_effect = lambda log=calls: log.append("install")
            page.goto.side_effect = lambda *args, log=calls, **kwargs: log.append("goto")
            with mock.patch.dict(os.environ, {"PRKS_E2E_PROFILE": "0"}):
                harness.open_app_page(
                    browser, "http://127.0.0.1:42002", install_clock=enabled,
                )
            self.assertEqual(calls, ["install", "goto"] if enabled else ["goto"])
            self.assertEqual(page.clock.install.call_count, int(enabled))
            page.goto.assert_called_once_with(
                "http://127.0.0.1:42002/#/folders", wait_until="domcontentloaded",
            )

    def test_app_subphases_preserve_real_navigation_and_all_three_waits(self):
        browser = mock.Mock()
        context = browser.new_context.return_value
        page = context.new_page.return_value
        page.evaluate.return_value = {
            "fetchStart": 10, "responseStart": 65,
            "responseEnd": 70, "domInteractive": 140,
            "domContentLoadedEventStart": 150,
            "domContentLoadedEventEnd": 160,
        }
        origin = "http://127.0.0.1:42002"
        with mock.patch.dict(os.environ, {"PRKS_E2E_PROFILE": "1"}):
            harness.start_test_profile("app-profile")
            try:
                with mock.patch.object(
                    harness.time,
                    "perf_counter",
                    side_effect=[10, 11, 20, 24, 27, 32, 34],
                ):
                    result = harness.open_app_page(browser, origin)
                phases = harness.finish_test_profile("app-profile")
            finally:
                harness.finish_test_profile("app-profile")

        self.assertIs(result[0], page)
        self.assertIs(result[1], context)
        page.goto.assert_called_once_with(
            origin + "/#/folders", wait_until="domcontentloaded"
        )
        self.assertEqual(
            page.wait_for_selector.call_args_list,
            [mock.call("#sidebar"), mock.call(".prks-folder-library")],
        )
        page.wait_for_function.assert_called_once_with(
            "() => location.hash === '#/folders'"
        )
        self.assertEqual(
            {name: phases[name] for name in (
                "browser_context", "app_navigation", "app_sidebar_wait",
                "app_folder_wait", "app_route_wait", "app_ready",
                "nav_response_wait", "nav_response_body", "nav_dom_content_loaded",
                "nav_until_interactive", "nav_interactive_to_dcl", "nav_dcl_handlers",
            )},
            {
                "browser_context": 1.0,
                "app_navigation": 4.0,
                "app_sidebar_wait": 3.0,
                "app_folder_wait": 5.0,
                "app_route_wait": 2.0,
                "app_ready": 14.0,
                "nav_response_wait": 0.055,
                "nav_response_body": 0.005,
                "nav_dom_content_loaded": 0.09,
                "nav_until_interactive": 0.07,
                "nav_interactive_to_dcl": 0.01,
                "nav_dcl_handlers": 0.01,
            },
        )
        self.assertEqual(phases["nav_timing_attempts"], 1.0)
        self.assertEqual(phases["nav_timing_samples"], 1.0)
        page.evaluate.assert_called_once()
        self.assertIn("getEntriesByType('navigation')", page.evaluate.call_args.args[0])
        self.assertAlmostEqual(
            sum(
                phases[name]
                for name in (
                    "app_navigation",
                    "app_sidebar_wait",
                    "app_folder_wait",
                    "app_route_wait",
                )
            ),
            phases["app_ready"],
            delta=0.00001,
        )

    def test_success_then_failed_page_open_does_not_drop_failed_time(self):
        from tests.e2e import run as runner

        browser = mock.Mock()
        page = browser.new_context.return_value.new_page.return_value
        page.wait_for_selector.side_effect = [
            None, None, RuntimeError("later sidebar never loaded")
        ]
        with mock.patch.dict(os.environ, {"PRKS_E2E_PROFILE": "1"}):
            harness.start_test_profile("mixed-page-opens")
            try:
                with mock.patch.object(
                    harness.time, "perf_counter",
                    side_effect=[
                        10, 11, 20, 24, 27, 32, 34,  # successful page open
                        40, 41, 50, 54, 57,          # failed page open
                    ],
                ):
                    harness.open_app_page(browser, "http://127.0.0.1:42002")
                    with self.assertRaisesRegex(RuntimeError, "later sidebar"):
                        harness.open_app_page(browser, "http://127.0.0.1:42002")
                phases = harness.finish_test_profile("mixed-page-opens")
            finally:
                harness.finish_test_profile("mixed-page-opens")

        self.assertEqual(phases["app_ready"], 14.0)
        self.assertEqual(phases["app_navigation"], 4.0)
        self.assertEqual(phases["app_sidebar_wait"], 3.0)
        self.assertEqual(phases["app_folder_wait"], 5.0)
        self.assertEqual(phases["app_route_wait"], 2.0)
        self.assertEqual(phases["app_navigation_failed"], 4.0)
        self.assertEqual(phases["app_sidebar_wait_failed"], 3.0)
        self.assertEqual(phases["browser_context"], 2.0)
        # An inclusive successful interval must not hide failed-call details.
        self.assertEqual(runner._profile_infrastructure_total(phases), 23.0)

    def test_browser_navigation_timing_missing_or_invalid_is_best_effort(self):
        page = mock.Mock()
        valid = {
            "fetchStart": 10.0,
            "responseStart": 20.0,
            "responseEnd": 22.0,
            "domInteractive": 24.0,
            "domContentLoadedEventStart": 27.0,
            "domContentLoadedEventEnd": 30.0,
        }
        samples = (
            None,
            {"fetchStart": 10},
            {**valid, "responseEnd": float("nan")},
            {**valid, "responseStart": 9},
            {**valid, "domInteractive": 21},
            {**valid, "domContentLoadedEventStart": 31},
            {**valid, "domContentLoadedEventStart": float("nan")},
            {**valid, "fetchStart": True},
        )
        for sample in samples:
            with self.subTest(sample=sample):
                page.evaluate.return_value = sample
                with mock.patch.dict(os.environ, {"PRKS_E2E_PROFILE": "1"}):
                    harness.start_test_profile("bad-browser-timing")
                    try:
                        harness._profile_browser_navigation(page)
                        phases = harness.finish_test_profile("bad-browser-timing")
                    finally:
                        harness.finish_test_profile("bad-browser-timing")
                self.assertEqual(phases, {"nav_timing_attempts": 1.0})
        with mock.patch.dict(os.environ, {"PRKS_E2E_PROFILE": "1"}):
            harness.start_test_profile("navigation-error")
            try:
                page.evaluate.side_effect = RuntimeError("performance entries unavailable")
                harness._profile_browser_navigation(page)
                self.assertEqual(
                    harness.finish_test_profile("navigation-error"),
                    {"nav_timing_attempts": 1.0},
                )
            finally:
                harness.finish_test_profile("navigation-error")

        page.reset_mock()
        with mock.patch.dict(os.environ, {"PRKS_E2E_PROFILE": "0"}):
            harness.start_test_profile("no-browser-timing")
            harness._profile_browser_navigation(page)
        page.evaluate.assert_not_called()

    def test_navigation_counter_name_validation_runs_without_assert(self):
        with mock.patch.dict(os.environ, {"PRKS_E2E_PROFILE": "1"}):
            harness.start_test_profile("bad-nav-counter")
            try:
                with self.assertRaisesRegex(ValueError, "unknown navigation timing counter"):
                    harness._profile_nav_count("not_a_navigation_counter")
                phases = harness.finish_test_profile("bad-nav-counter")
            finally:
                harness.finish_test_profile("bad-nav-counter")
        self.assertEqual(phases, {})

    def test_browser_navigation_zero_duration_is_a_valid_captured_sample(self):
        page = mock.Mock()
        page.evaluate.return_value = {
            "fetchStart": 10, "responseStart": 20,
            "responseEnd": 20, "domInteractive": 20,
            "domContentLoadedEventStart": 20,
            "domContentLoadedEventEnd": 20,
        }
        with mock.patch.dict(os.environ, {"PRKS_E2E_PROFILE": "1"}):
            harness.start_test_profile("zero-nav")
            try:
                harness._profile_browser_navigation(page)
                phases = harness.finish_test_profile("zero-nav")
            finally:
                harness.finish_test_profile("zero-nav")
        self.assertEqual(phases["nav_timing_attempts"], 1.0)
        self.assertEqual(phases["nav_timing_samples"], 1.0)
        self.assertEqual(phases["nav_response_wait"], 0.01)
        # finish_test_profile intentionally omits zero-duration phase sums.
        # Explicit coverage counters retain the distinction from missing data.
        self.assertNotIn("nav_response_body", phases)
        self.assertNotIn("nav_dom_content_loaded", phases)
        for name in ("nav_until_interactive", "nav_interactive_to_dcl", "nav_dcl_handlers"):
            self.assertNotIn(name, phases)

    def test_unprofiled_page_and_server_take_no_extra_clock_samples(self):
        browser = mock.Mock()
        with mock.patch.dict(os.environ, {"PRKS_E2E_PROFILE": "0"}):
            harness.finish_test_profile("none")
            with mock.patch.object(
                harness.time, "perf_counter", side_effect=[10, 11, 20, 34]
            ) as clock:
                harness.open_app_page(browser, "http://127.0.0.1:42002")
            self.assertEqual(clock.call_count, 4)  # existing context + app totals
            with (
                mock.patch.object(harness, "find_free_port", return_value=42004),
                mock.patch.object(harness, "apply_playwright_browser_env"),
                mock.patch.object(harness, "provision_library", _fake_library),
                mock.patch.object(harness.AppServer, "_spawn"),
                mock.patch.object(harness, "wait_http"),
            ):
                server = harness.AppServer()
                try:
                    with mock.patch.object(
                        harness.time, "perf_counter", side_effect=[100, 107]
                    ) as clock:
                        self.assertIs(server.start(), server)
                    self.assertEqual(clock.call_count, 2)  # existing server total
                finally:
                    server.stop()

    def test_slowest_ranking_does_not_double_count_inclusive_phases(self):
        from tests.e2e import run as runner

        phases = {
            "server_start": 5.0,
            "server_spawn": 2.0,
            "server_http_ready": 3.0,
            "app_ready": 8.0,
            "app_navigation": 4.0,
            "app_sidebar_wait": 1.0,
            "app_folder_wait": 2.0,
            "app_route_wait": 1.0,
            "request_routing": 6.0,
            "routing_decision": 0.1,
            "seed_clone": 0.5,
            # Browser-native details are diagnostic overlaps, not new work.
            "nav_response_wait": 0.5,
            "nav_response_body": 0.2,
            "nav_dom_content_loaded": 0.6,
            "nav_until_interactive": 0.4,
            "nav_interactive_to_dcl": 0.1,
            "nav_dcl_handlers": 0.1,
            "nav_timing_attempts": 7.0,
            "nav_timing_samples": 6.0,
        }
        self.assertEqual(runner._profile_infrastructure_total(phases), 19.5)
        # Without inclusive parents the same detail phases still count.
        for parent in ("server_start", "app_ready", "request_routing"):
            phases.pop(parent)
        self.assertAlmostEqual(runner._profile_infrastructure_total(phases), 13.6)

    def test_failed_readiness_never_reports_successful_app_ready(self):
        browser = mock.Mock()
        page = browser.new_context.return_value.new_page.return_value
        page.wait_for_selector.side_effect = RuntimeError("missing real sidebar")
        with mock.patch.dict(os.environ, {"PRKS_E2E_PROFILE": "1"}):
            harness.start_test_profile("failed-readiness")
            try:
                with mock.patch.object(
                    harness.time, "perf_counter", side_effect=[10, 11, 20, 24, 27]
                ):
                    with self.assertRaisesRegex(RuntimeError, "missing real sidebar"):
                        harness.open_app_page(browser, "http://127.0.0.1:42003")
                phases = harness.finish_test_profile("failed-readiness")
            finally:
                harness.finish_test_profile("failed-readiness")
        self.assertNotIn("app_ready", phases)
        self.assertNotIn("app_navigation", phases)
        self.assertNotIn("app_sidebar_wait", phases)
        self.assertEqual(phases["app_navigation_failed"], 4.0)
        self.assertEqual(phases["app_sidebar_wait_failed"], 3.0)


if __name__ == "__main__":
    unittest.main()
