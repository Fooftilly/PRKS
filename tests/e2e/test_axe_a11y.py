"""Opt-in advisory axe-core on real, synthetic PRKS surfaces (never production)."""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from tests.e2e.axe_advisory import scan, short_summary, write_report
from tests.e2e.fixtures import seed_library
from tests.e2e.harness import AppServer, open_app_page, require_chromium
from tests.e2e.test_app import _wait_pdf_viewer


def load_tests(loader, standard_tests, pattern):
    if os.environ.get("PRKS_E2E") != "1" or os.environ.get("PRKS_E2E_AXE") != "1":
        return unittest.TestSuite()
    return standard_tests


_PW = None
_BROWSER = None


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    global _PW, _BROWSER
    try:
        if _BROWSER is not None:
            _BROWSER.close()
    finally:
        if _PW is not None:
            _PW.stop()
        _PW = None
        _BROWSER = None


class AxeAccessibilityPilotTests(unittest.TestCase):
    def test_advisory_scans_five_real_fixture_surfaces_and_keyboard_focus(self):
        server = AppServer(seed_fn=seed_library)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin)
        self.addCleanup(context.close)
        self.addCleanup(collector.assert_clean)

        surfaces = []
        page.locator("#sidebar").wait_for(state="visible")
        surfaces.append(scan(page, "#sidebar", "shell"))

        page.evaluate("() => window.prksNavigate('#/folders')")
        page.locator("[data-prks-folder-library-view]").wait_for(state="visible")
        surfaces.append(scan(page, "[data-prks-folder-library-view]", "vue-folders"))

        page.evaluate(
            "id => window.prksNavigate('#/works/' + id)", server.ids["work_a"]
        )
        page.locator(".work-detail").first.wait_for(state="visible", timeout=20000)
        # Work mount is asynchronous; a visible wrapper is not yet the editor.
        page.locator(".CodeMirror").first.wait_for(state="visible", timeout=30000)
        _wait_pdf_viewer(page)
        surfaces.append(scan(page, ".work-detail", "work-detail"))

        surfaces.append(scan(page, '[data-prks-role="pdf-viewer"]', "pdf-viewer"))

        # Independent keyboard assertion: axe cannot validate focus restoration.
        settings = page.locator("button.settings-btn")
        settings.focus()
        page.keyboard.press("Enter")
        page.locator("#settings-modal:not(.hidden)").wait_for(state="visible")
        surfaces.append(scan(page, "#settings-modal:not(.hidden)", "settings-dialog"))
        report_path = os.environ.get("PRKS_AXE_REPORT_PATH")
        if report_path:
            write_report(surfaces, Path(report_path))
        else:
            # Validation still verifies that all five required scans completed.
            from tempfile import TemporaryDirectory
            with TemporaryDirectory() as folder:
                write_report(surfaces, Path(folder) / "axe-smoke.json")
        page.keyboard.press("Escape")
        page.locator("#settings-modal").wait_for(state="hidden")
        self.assertTrue(
            page.evaluate(
                "() => !!(document.activeElement && "
                "document.activeElement.classList.contains('settings-btn'))"
            ),
            "Escape from settings must restore focus to its launching button",
        )

        print("\n[axe-advisory] " + short_summary(surfaces), flush=True)


if __name__ == "__main__":
    unittest.main()
