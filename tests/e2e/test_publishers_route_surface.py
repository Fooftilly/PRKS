"""Publishers route surface opens #/publishers in the main pane.

Dialog resume, busy labels, and refresh failures stay in the Publishers Vitest
file. This module exists so a Publishers-only affected run selects a test that
actually mounts the page.
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


class PublishersRouteSurfaceTests(unittest.TestCase):
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

    def test_publishers_page_mounts_from_the_route(self):
        page = self.start()
        page.evaluate("() => prksNavigate('#/publishers')")
        page.wait_for_selector(
            ".prks-tile--main [data-prks-publishers-page]",
            timeout=15000,
        )
        title = page.locator(".prks-tile--main .prks-page-title").inner_text()
        self.assertEqual(title, "Publishers")
        self.assertEqual(
            page.locator(".prks-tile--main [data-prks-publishers-page]").count(),
            1,
        )
