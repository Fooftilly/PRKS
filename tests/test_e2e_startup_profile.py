"""Unit contracts for E2E startup/readiness profiling; no Chromium required."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from tests.e2e import harness


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
            )},
            {
                "browser_context": 1.0,
                "app_navigation": 4.0,
                "app_sidebar_wait": 3.0,
                "app_folder_wait": 5.0,
                "app_route_wait": 2.0,
                "app_ready": 14.0,
            },
        )
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
        self.assertEqual(phases["app_navigation"], 4.0)
        self.assertEqual(phases["app_sidebar_wait"], 3.0)


if __name__ == "__main__":
    unittest.main()
