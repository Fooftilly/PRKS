"""Fast, browser-free regression tests for PostgreSQL E2E phase attribution."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from tests.e2e import harness, run
from tests.e2e.policy import PROFILE_ENV


class E2EPostgresProfileTests(unittest.TestCase):
    def test_profile_distinguishes_postgres_provision_from_server_start_and_drop(self):
        library = mock.Mock()
        library.server_env.return_value = {}
        test_id = "profile.synthetic_postgres"
        with (
            mock.patch.dict(os.environ, {PROFILE_ENV: "1"}),
            mock.patch.object(harness, "find_free_port", return_value=18123),
            mock.patch.object(harness, "provision_library", return_value=library) as provision,
            mock.patch.object(harness.AppServer, "_spawn"),
            mock.patch.object(harness, "wait_http"),
            mock.patch.object(
                harness, "apply_playwright_browser_env", return_value=Path("/unused")
            ),
        ):
            harness.start_test_profile(test_id)
            server = harness.AppServer()
            try:
                server.start()
                self.assertIs(server.library, library)
            finally:
                server.stop()
                phases = harness.finish_test_profile(test_id)

        provision.assert_called_once_with(server.storage_root)
        library.drop.assert_called_once_with()
        self.assertGreater(phases.get("postgres_provision", 0), 0)
        self.assertGreater(phases.get("postgres_drop", 0), 0)
        self.assertGreaterEqual(phases["server_stop"], phases["postgres_drop"])
        self.assertIn("server_start", phases)
        self.assertNotIn("postgres_provision_failed", phases)

    def test_provision_failure_reports_failed_phase_and_does_not_start_app(self):
        test_id = "profile.synthetic_failed_provision"
        with (
            mock.patch.dict(os.environ, {PROFILE_ENV: "1"}),
            mock.patch.object(harness, "find_free_port", return_value=18123),
            mock.patch.object(
                harness, "provision_library", side_effect=RuntimeError("synthetic failure")
            ),
            mock.patch.object(harness.AppServer, "_spawn") as spawn,
        ):
            harness.start_test_profile(test_id)
            server = harness.AppServer()
            try:
                with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                    server.start()
            finally:
                phases = harness.finish_test_profile(test_id)

        self.assertIsNone(server.library)
        spawn.assert_not_called()
        self.assertGreater(phases.get("postgres_provision_failed", 0), 0)
        self.assertNotIn("postgres_provision", phases)

    def test_profile_ranking_does_not_double_count_postgres_drop(self):
        phases = {
            "postgres_provision": 2.0,
            "server_start": 3.0,
            "server_spawn": 1.0,  # Nested inside server_start.
            "server_http_ready": 2.0,  # Nested inside server_start.
            "server_stop": 7.0,
            "postgres_drop": 5.0,  # Nested inside server_stop.
        }
        self.assertEqual(run._profile_infrastructure_total(phases), 12.0)
        self.assertEqual(
            run._profile_infrastructure_total({"postgres_provision": 2, "postgres_drop": 5}),
            7.0,
        )


if __name__ == "__main__":
    unittest.main()
