"""Processing Files route surface opens #/processing-files in the main pane.

Save, import, and preview lifetime stay in the Processing Vitest file. This
module exists so a Processing-only affected run selects a test that actually
mounts the page.
"""
from __future__ import annotations

import os
import unittest

from tests.e2e.fixtures import seed_library
from tests.e2e.harness import AppServer, open_app_page, require_chromium


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get("PRKS_E2E") == "1" else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close()
    _PW.stop()


class ProcessingRouteSurfaceTests(unittest.TestCase):
    def start(self):
        server = AppServer(seed_fn=seed_library)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(
            _BROWSER, server.origin, service_workers="allow"
        )
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        return page

    def test_processing_files_page_mounts_from_the_route(self):
        page = self.start()
        page.evaluate("() => prksNavigate('#/processing-files')")
        page.wait_for_selector(
            ".prks-tile--main [data-prks-processing-page]",
            timeout=15000,
        )
        title = page.locator(".prks-tile--main .prks-page-title").inner_text()
        self.assertEqual(title, "Files for Processing")
        self.assertEqual(
            page.locator(".prks-tile--main [data-prks-processing-page]").count(),
            1,
        )
