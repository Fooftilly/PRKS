"""Pure safeguards for the opt-in Firefox/WebKit smoke pilot (#651 C2)."""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import Mock, patch

from scripts.ci.cross_browser_smoke import (
    CHECKS, ENGINES, SmokeFailure, main, smoke, verify_isolation, verify_report,
)


def report(engines=("firefox", "webkit")):
    return {
        "format": "prks-cross-browser-smoke-v1",
        "fixture": "disposable_appserver_synthetic_sqlite",
        "browsers": [
            {"engine": engine, "version": "1.2.3", "passed": True, "checks": list(CHECKS)}
            for engine in engines
        ],
    }


class CrossBrowserSmokeContracts(unittest.TestCase):
    def test_disallows_inherited_live_auth_and_database_connections(self):
        verify_isolation({})
        verify_isolation({"PRKS_STORAGE": "/nonexistent-user-storage"})
        for env in (
            "PRKS_AUTH", "PRKS_DATABASE_URL", "PRKS_DATABASE_URL_FILE",
            "PRKS_TEST_DATABASE_URL",
        ):
            with self.subTest(env=env), self.assertRaises(RuntimeError):
                verify_isolation({env: "sensitive-secret"})

    def test_complete_two_engine_report_is_accepted(self):
        self.assertEqual(ENGINES, ("firefox", "webkit"))
        verify_report(report(), ENGINES)
        verify_report(report(("webkit",)), ("webkit",))

    def test_missing_or_unproven_checks_fail_closed(self):
        for mutation in ("missing_browser", "missing_check", "wrong_order",
                         "not_passed", "missing_version", "wrong_fixture"):
            data = report()
            if mutation == "missing_browser":
                data["browsers"].pop()
            elif mutation == "missing_check":
                data["browsers"][0]["checks"].pop()
            elif mutation == "wrong_order":
                data["browsers"].reverse()
            elif mutation == "not_passed":
                data["browsers"][0]["passed"] = False
            elif mutation == "missing_version":
                data["browsers"][0]["version"] = ""
            else:
                data["fixture"] = "arbitrary_production_data"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                verify_report(data, ENGINES)

    def test_no_arbitrary_browser_engine_or_unexpected_results(self):
        data = report(("firefox",))
        data["browsers"][0]["engine"] = "chromium"
        with self.assertRaises(ValueError):
            verify_report(data, ("firefox",))

    def test_each_browser_interaction_reports_only_engine_and_stage(self):
        # No real browser is required to verify that a failed check is
        # attributed precisely, even when Playwright embeds sensitive text.
        failures = (
            ("[data-prks-folder-library-view]", "folders"),
            (".work-detail", "work_detail"),
            (".CodeMirror", "notes_editor"),
            ("button.settings-btn", "settings_focus"),
        )
        for failed_selector, expected_stage in failures:
            with self.subTest(stage=expected_stage):
                page = Mock()
                context = Mock()
                collector = Mock()

                def locator(selector):
                    if selector == failed_selector:
                        raise TimeoutError("secret-url-private-document-title")
                    return Mock()

                page.locator.side_effect = locator
                with patch(
                    "scripts.ci.cross_browser_smoke.open_app_page",
                    return_value=(page, context, collector),
                ):
                    with self.assertRaises(SmokeFailure) as caught:
                        smoke(object(), "http://127.0.0.1:1", "synthetic", engine="webkit")
                error = caught.exception
                self.assertEqual(
                    (error.engine, error.stage, error.cause_type),
                    ("webkit", expected_stage, "TimeoutError"),
                )
                self.assertNotIn("secret-url-private-document-title", str(error))
                context.close.assert_called_once()

    def test_entrypoint_prints_only_safe_engine_and_check(self):
        output = io.StringIO()
        with patch(
            "scripts.ci.cross_browser_smoke.run",
            side_effect=SmokeFailure("webkit", "notes_editor", "TimeoutError"),
        ), redirect_stderr(output):
            self.assertEqual(main(["--engines", "webkit"]), 1)
        self.assertEqual(
            output.getvalue(),
            "Cross-browser smoke failed at webkit/notes_editor (TimeoutError)\n",
        )

    def test_unexpected_preflight_failure_does_not_leak_details(self):
        output = io.StringIO()
        with patch(
            "scripts.ci.cross_browser_smoke.run",
            side_effect=RuntimeError("sensitive-server-connection-string"),
        ), redirect_stderr(output):
            self.assertEqual(main(["--engines", "firefox"]), 1)
        self.assertEqual(
            output.getvalue(),
            "Cross-browser smoke failed at setup/preflight (RuntimeError)\n",
        )


if __name__ == "__main__":
    unittest.main()
