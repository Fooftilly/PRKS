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
                        self.assertIs(server.start(), server)
                        phases = harness.finish_test_profile("server-profile")
                    finally:
                        harness.finish_test_profile("server-profile")
                    spawn.assert_called_once()
                    wait_http.assert_called_once_with(
                        "http://127.0.0.1:42002/api/works"
                    )
                    self.assertIn("server_spawn", phases)
                    self.assertIn("server_http_ready", phases)
                    self.assertIn("server_start", phases)
                    self.assertGreater(phases["server_start"], 0)
                    self.assertAlmostEqual(
                        phases["server_spawn"] + phases["server_http_ready"],
                        phases["server_start"],
                        delta=0.00001,
                    )
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
        for phase in (
            "browser_context",
            "app_navigation",
            "app_sidebar_wait",
            "app_folder_wait",
            "app_route_wait",
            "app_ready",
        ):
            self.assertIn(phase, phases)
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
        self.assertEqual(runner._profile_infrastructure_total(phases), 13.6)

    def test_failed_readiness_never_reports_successful_app_ready(self):
        browser = mock.Mock()
        page = browser.new_context.return_value.new_page.return_value
        page.wait_for_selector.side_effect = RuntimeError("missing real sidebar")
        with mock.patch.dict(os.environ, {"PRKS_E2E_PROFILE": "1"}):
            harness.start_test_profile("failed-readiness")
            try:
                with self.assertRaisesRegex(RuntimeError, "missing real sidebar"):
                    harness.open_app_page(browser, "http://127.0.0.1:42003")
                phases = harness.finish_test_profile("failed-readiness")
            finally:
                harness.finish_test_profile("failed-readiness")
        self.assertNotIn("app_ready", phases)


if __name__ == "__main__":
    unittest.main()
