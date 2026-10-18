"""Unit contracts for E2E startup/readiness profiling; no Chromium required."""
from __future__ import annotations

import os
import unittest
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


class StartupReadinessProfileTests(unittest.TestCase):
    def test_server_subphases_keep_http_readiness_and_inclusive_total(self):
        with mock.patch.dict(os.environ, {"PRKS_E2E_PROFILE": "1"}):
            with (
                mock.patch.object(harness, "find_free_port", return_value=42002),
                mock.patch.object(harness, "apply_playwright_browser_env"),
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
