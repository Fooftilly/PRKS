"""Pure safeguards for the opt-in Firefox/WebKit smoke pilot (#651 C2)."""
from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr
from types import ModuleType
from unittest.mock import MagicMock, Mock, patch

from scripts.ci.cross_browser_smoke import (
    CHECKS, ENGINES, SmokeFailure, close_preserving_failure, main, run, smoke,
    verify_isolation, verify_report,
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

    def test_context_cleanup_failure_has_its_own_safe_stage(self):
        page = Mock()
        page.evaluate.return_value = True
        context = Mock()
        context.close.side_effect = RuntimeError("secret-context-path")
        with patch(
            "scripts.ci.cross_browser_smoke.open_app_page",
            return_value=(page, context, Mock()),
        ):
            with self.assertRaises(SmokeFailure) as caught:
                smoke(object(), "http://127.0.0.1:1", "synthetic", engine="webkit")
        self.assertEqual(
            (caught.exception.engine, caught.exception.stage, caught.exception.cause_type),
            ("webkit", "context_close", "RuntimeError"),
        )
        self.assertNotIn("secret-context-path", str(caught.exception))

    def test_cleanup_never_replaces_the_original_check_failure(self):
        context = Mock()
        context.close.side_effect = RuntimeError("private-url-in-cleanup")
        failed_page = Mock()
        failed_page.locator.side_effect = TimeoutError("private-work-title")
        with patch(
            "scripts.ci.cross_browser_smoke.open_app_page",
            return_value=(failed_page, context, Mock()),
        ):
            with self.assertRaises(SmokeFailure) as caught:
                smoke(object(), "http://127.0.0.1:1", "synthetic", engine="firefox")
        self.assertEqual(
            (caught.exception.engine, caught.exception.stage, caught.exception.cause_type),
            ("firefox", "folders", "TimeoutError"),
        )
        self.assertNotIn("private-work-title", str(caught.exception))
        self.assertNotIn("private-url-in-cleanup", str(caught.exception))

    def test_browser_cleanup_preserves_original_and_reports_unpaired_failure(self):
        browser = Mock()
        browser.close.side_effect = RuntimeError("sensitive-page-url")
        # A cleanup failure during exception propagation must be suppressed.
        close_preserving_failure(
            browser, engine="firefox", stage="browser_close", failed=True,
        )
        with self.assertRaises(SmokeFailure) as caught:
            close_preserving_failure(
                browser, engine="webkit", stage="browser_close", failed=False,
            )
        self.assertEqual((caught.exception.engine, caught.exception.stage),
                         ("webkit", "browser_close"))
        self.assertNotIn("sensitive-page-url", str(caught.exception))

    def test_browser_version_failure_is_not_mislabeled_as_browser_close(self):
        # Build an in-memory Playwright module so unit tests don't need the
        # optional installed browser/package. Real engines run in advisory CI.
        class Browser:
            @property
            def version(self):
                raise ValueError("sensitive-version-diagnostic")

            def close(self):
                raise RuntimeError("sensitive-close-diagnostic")

        fake_session = MagicMock()
        fake_session.__enter__.return_value.firefox.launch.return_value = Browser()
        fake_playwright = ModuleType("playwright")
        fake_sync_api = ModuleType("playwright.sync_api")
        fake_sync_api.sync_playwright = Mock(return_value=fake_session)
        server = Mock()
        server.ids = {"work_a": "synthetic-work"}
        with (
            patch.dict(sys.modules, {
                "playwright": fake_playwright,
                "playwright.sync_api": fake_sync_api,
            }),
            patch("scripts.ci.cross_browser_smoke.AppServer", return_value=server),
            patch("scripts.ci.cross_browser_smoke.apply_playwright_browser_env"),
            patch("scripts.ci.cross_browser_smoke.verify_isolation"),
            patch("scripts.ci.cross_browser_smoke.smoke"),
        ):
            with self.assertRaises(SmokeFailure) as caught:
                run(("firefox",))
        self.assertEqual(
            (caught.exception.engine, caught.exception.stage, caught.exception.cause_type),
            ("firefox", "browser_version", "ValueError"),
        )
        self.assertNotIn("sensitive-version-diagnostic", str(caught.exception))
        self.assertNotIn("sensitive-close-diagnostic", str(caught.exception))
        server.stop.assert_called_once()

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
