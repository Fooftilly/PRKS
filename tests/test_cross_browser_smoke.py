"""Pure safeguards for the opt-in Firefox/WebKit smoke pilot (#651 C2)."""
from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr
from types import ModuleType
from unittest.mock import MagicMock, Mock, patch

from scripts.ci.cross_browser_smoke import (
    CHECKS, ENGINES, SmokeFailure, close_preserving_failure, collector_failure_categories,
    main, run, smoke,
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

                def locator(selector, **_kwargs):
                    if selector == failed_selector:
                        raise TimeoutError("secret-url-private-document-title")
                    return Mock()

                page.locator.side_effect = locator
                with (
                    patch("scripts.ci.cross_browser_smoke.open_app_page",
                          return_value=(page, context, collector)),
                    patch("scripts.ci.cross_browser_smoke.wait_for_async"),
                ):
                    with self.assertRaises(SmokeFailure) as caught:
                        smoke(object(), "http://127.0.0.1:1", "synthetic", "related", engine="webkit")
                error = caught.exception
                self.assertEqual(
                    (error.engine, error.stage, error.cause_type),
                    ("webkit", expected_stage, "TimeoutError"),
                )
                self.assertNotIn("secret-url-private-document-title", str(error))
                context.close.assert_called_once()

    def test_error_bucket_diagnostics_omit_sensitive_browser_details(self):
        collector = Mock()
        collector.pageerrors = ["private-document-title", "secret-content"]
        collector.pageerror_names = ["TypeError", "ReferenceError"]
        collector.console_errors = ["credential=private"]
        collector.failed_requests = []
        collector.http_5xx = []
        collector.external = []
        self.assertEqual(
            collector_failure_categories(collector), "page-2_console-1_kinds-typeerror_referenceerror"
        )
        self.assertNotIn("private", collector_failure_categories(collector))

    def test_e2e_collector_keeps_pageerror_name_separate_from_private_message(self):
        from tests.e2e.harness import PageCollector

        collector = PageCollector.__new__(PageCollector)
        collector.pageerrors = []
        collector.pageerror_names = []

        class SyntheticError:
            name = "TypeError"

            def __str__(self):
                return "secret-research-note-in-exception"

        collector._on_page_error(SyntheticError())
        self.assertEqual(collector.pageerror_names, ["TypeError"])
        self.assertEqual(collector.pageerrors, ["secret-research-note-in-exception"])
        collector.console_errors = []
        collector.failed_requests = []
        collector.http_5xx = []
        collector.external = []
        self.assertEqual(
            collector_failure_categories(collector), "page-1_kinds-typeerror"
        )
        self.assertNotIn("secret", collector_failure_categories(collector))

    def test_resize_loop_notice_is_bounded_and_webkit_only(self):
        from tests.e2e.harness import PageCollector

        class BrowserError:
            name = ""
            stack = ""

            def __init__(self, message):
                self.message = message

            def __str__(self):
                return self.message

        def collector(allow):
            instance = PageCollector.__new__(PageCollector)
            instance._allow_webkit_resize_notices = allow
            instance._webkit_resize_notices = 0
            instance.pageerrors = []
            instance.pageerror_names = []
            return instance

        notice = "ResizeObserver loop completed with undelivered notifications."
        webkit = collector(True)
        for _ in range(3):
            webkit._on_page_error(BrowserError(notice))
        self.assertEqual(webkit.pageerrors, [])
        self.assertEqual(webkit._webkit_resize_notices, 3)

        # Repeating every paint is a genuine loop: fail, never silently clear.
        webkit._on_page_error(BrowserError(notice))
        self.assertEqual(webkit.pageerrors, [notice])
        self.assertEqual(webkit._webkit_resize_notices, 4)

        # Another browser cannot opt in by accident.
        firefox = collector(False)
        firefox._on_page_error(BrowserError(notice))
        self.assertEqual(firefox.pageerrors, [notice])

        # Only the browser-defined literal is accepted, not related errors.
        variant = collector(True)
        variant._on_page_error(BrowserError(notice + " private"))
        self.assertEqual(variant.pageerrors, [notice + " private"])

        # Real script exceptions (with source or JS exception type) still fail.
        script = BrowserError(notice)
        script.stack = "at handler (http://127.0.0.1:1234/js/app.js:1:1)"
        sourced = collector(True)
        sourced._on_page_error(script)
        self.assertEqual(sourced.pageerrors, [notice])

        typed = BrowserError(notice)
        typed.name = "TypeError"
        typed_collector = collector(True)
        typed_collector._on_page_error(typed)
        self.assertEqual(typed_collector.pageerrors, [notice])

        # WebKit's older exact browser string uses the same bounded contract.
        older = collector(True)
        older._on_page_error(BrowserError("ResizeObserver loop limit exceeded"))
        self.assertEqual(older.pageerrors, [])
        self.assertEqual(older._webkit_resize_notices, 1)

    def test_notes_commit_and_reload_check_is_required(self):
        self.assertIn("notes_save_reload", CHECKS)
        data = report()
        data["browsers"][0]["checks"].remove("notes_save_reload")
        with self.assertRaises(ValueError):
            verify_report(data, ENGINES)

    def test_notes_keyboard_failure_is_safely_attributed(self):
        page, context = Mock(), Mock()
        page.keyboard.insert_text.side_effect = TimeoutError("private-note-body")
        with patch(
            "scripts.ci.cross_browser_smoke.open_app_page",
            return_value=(page, context, Mock()),
        ):
            with self.assertRaises(SmokeFailure) as caught:
                smoke(object(), "http://127.0.0.1:1", "synthetic", "related",
                      engine="webkit")
        self.assertEqual(
            (caught.exception.engine, caught.exception.stage, caught.exception.cause_type),
            ("webkit", "notes_save_reload", "TimeoutError"),
        )
        self.assertNotIn("private-note-body", str(caught.exception))
        context.close.assert_called_once()

    def test_server_ack_never_passes_on_unresolved_or_stale_note(self):
        page, context = Mock(), Mock()
        with (
            patch("scripts.ci.cross_browser_smoke.open_app_page",
                  return_value=(page, context, Mock())),
            patch("scripts.ci.cross_browser_smoke.wait_for_async",
                  side_effect=AssertionError("private-uncommitted-note")) as poll,
        ):
            with self.assertRaises(SmokeFailure) as caught:
                smoke(object(), "http://127.0.0.1:1", "synthetic", "related",
                      engine="firefox")
        self.assertEqual(
            (caught.exception.engine, caught.exception.stage, caught.exception.cause_type),
            ("firefox", "notes_save_reload", "AssertionError"),
        )
        self.assertNotIn("private-uncommitted-note", str(caught.exception))
        self.assertEqual(poll.call_count, 1)
        # Guard against reverting to Playwright wait_for_function on a Promise.
        args, kwargs = poll.call_args
        self.assertIn("async", args[1])
        self.assertIn("text_content === text", args[1])
        self.assertEqual(kwargs["arg"]["id"], "synthetic")
        context.close.assert_called_once()

    def test_pdf_and_secondary_pane_checks_are_required(self):
        # These checks run through the same two-engine smoke and are not
        # implied by seeing the Work wrapper.
        self.assertEqual(CHECKS[-3:-1], ("pdf_render", "split_panes"))
        data = report()
        data["browsers"][0]["checks"].remove("pdf_render")
        with self.assertRaises(ValueError):
            verify_report(data, ENGINES)

    def test_pdf_render_failure_is_attributed_without_leaking_details(self):
        page, context, collector = Mock(), Mock(), Mock()
        page.evaluate.return_value = True
        with (
            patch("scripts.ci.cross_browser_smoke.open_app_page",
                  return_value=(page, context, collector)),
            patch("scripts.ci.cross_browser_smoke._wait_pdf_viewer",
                  side_effect=TimeoutError("private-pdf-content")),
            patch("scripts.ci.cross_browser_smoke.wait_for_async"),
        ):
            with self.assertRaises(SmokeFailure) as caught:
                smoke(object(), "http://127.0.0.1:1", "synthetic", "related", engine="webkit")
        self.assertEqual((caught.exception.stage, caught.exception.cause_type),
                         ("pdf_render", "TimeoutError"))
        self.assertNotIn("private-pdf-content", str(caught.exception))
        context.close.assert_called_once()

    def test_split_failure_is_attributed_without_leaking_details(self):
        page, context, collector = Mock(), Mock(), Mock()
        page.evaluate.return_value = True
        def fail_split_only(js, **_kwargs):
            if "s.mode === 'tiled'" in js:
                raise TimeoutError("secret-tab-title")
            return None
        page.wait_for_function.side_effect = fail_split_only
        with (
            patch("scripts.ci.cross_browser_smoke.open_app_page",
                  return_value=(page, context, collector)),
            patch("scripts.ci.cross_browser_smoke._wait_pdf_viewer"),
            patch("scripts.ci.cross_browser_smoke.wait_for_async"),
        ):
            with self.assertRaises(SmokeFailure) as caught:
                smoke(object(), "http://127.0.0.1:1", "synthetic", "related", engine="firefox")
        self.assertEqual((caught.exception.stage, caught.exception.cause_type),
                         ("split_panes", "TimeoutError"))
        self.assertNotIn("secret-tab-title", str(caught.exception))
        context.close.assert_called_once()

    def test_split_reload_must_be_recorded_in_every_browser(self):
        self.assertEqual(CHECKS[-1], "split_reload")
        missing = report()
        missing["browsers"][1]["checks"].remove("split_reload")
        with self.assertRaises(ValueError):
            verify_report(missing, ENGINES)

    def test_split_reload_failure_reports_safe_stage_not_private_page(self):
        page, context, collector = Mock(), Mock(), Mock(unsafe=True)
        original = {"mainTabId": "main-tab", "secondaryTree": {"tabId": "related-tab"}}
        page.evaluate.side_effect = lambda script, *args, **kwargs: (
            original if script == "() => window.prksWorkspaceSnapshot()" else True
        )
        page.reload.side_effect = [None, TimeoutError("secret-work-title")]
        with (
            patch("scripts.ci.cross_browser_smoke.open_app_page",
                  return_value=(page, context, collector)),
            patch("scripts.ci.cross_browser_smoke.wait_for_async"),
            patch("scripts.ci.cross_browser_smoke._wait_pdf_viewer"),
            patch("scripts.ci.cross_browser_smoke._wait_pdf_tab"),
        ):
            with self.assertRaises(SmokeFailure) as caught:
                smoke(object(), "http://127.0.0.1:1", "synthetic", "related",
                      engine="firefox")
        self.assertEqual(
            (caught.exception.engine, caught.exception.stage, caught.exception.cause_type),
            ("firefox", "split_reload", "TimeoutError"),
        )
        self.assertNotIn("secret-work-title", str(caught.exception))
        self.assertEqual(page.reload.call_count, 2)
        context.close.assert_called_once()

    def test_context_cleanup_failure_has_its_own_safe_stage(self):
        page = Mock()
        page.evaluate.side_effect = lambda js, *args: (
            {"mainTabId": "main", "secondaryTree": {"tabId": "second"}}
            if js == "() => window.prksWorkspaceSnapshot()" else True
        )
        context = Mock()
        context.close.side_effect = RuntimeError("secret-context-path")
        with (
            patch("scripts.ci.cross_browser_smoke.open_app_page",
                  return_value=(page, context, Mock(unsafe=True))),
            patch("scripts.ci.cross_browser_smoke._wait_pdf_viewer"),
            patch("scripts.ci.cross_browser_smoke._wait_pdf_tab"),
            patch("scripts.ci.cross_browser_smoke.wait_for_async"),
        ):
            with self.assertRaises(SmokeFailure) as caught:
                smoke(object(), "http://127.0.0.1:1", "synthetic", "related", engine="webkit")
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
                smoke(object(), "http://127.0.0.1:1", "synthetic", "related", engine="firefox")
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
        server.ids = {"work_a": "synthetic-work", "work_b": "synthetic-related"}
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
