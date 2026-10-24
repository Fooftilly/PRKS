"""Publishers route surface opens #/publishers in the main pane.

Busy labels, refresh failures, and stale owners stay in the Publishers Vitest
files. This module proves the page mounts from the route and that its typed
client and query cache round-trip real writes through the server.
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

    def test_create_alias_and_delete_round_trip_through_the_server(self):
        page = self.start()
        page.evaluate("() => prksNavigate('#/publishers')")
        main = ".prks-tile--main [data-prks-publishers-page]"
        page.wait_for_selector(f"{main} #publishers-page-cloud", timeout=15000)
        page.fill(f"{main} #publishers-page-new-name", "E2E Round Trip Press")
        page.click(f"{main} #publishers-page-add-btn")
        row = page.locator(f"{main} .publishers-page__list-item", has_text="E2E Round Trip Press")
        row.wait_for(timeout=15000)
        self.assertEqual(page.input_value(f"{main} #publishers-page-new-name"), "")

        row.locator(".publishers-page__alias-btn").click()
        page.fill(f"{main} #publishers-page-alias-input", "E2E RTP")
        page.click(f"{main} #publishers-page-alias-add-btn")
        page.wait_for_selector(f'{main} [data-publisher-alias-remove="E2E RTP"]', timeout=15000)
        self.assertEqual(row.locator(".publishers-page__list-stats").inner_text(), "0 files · 1 alias")

        page.click(f"{main} #publishers-page-delete-btn")
        page.click("#prks-modal-confirm-ok")
        row.wait_for(state="detached", timeout=15000)
        self.assertEqual(page.locator(f"{main} #publishers-page-alias-modal").count(), 0)
        listed = page.evaluate(
            "async () => (await (await fetch('/api/publishers?used=1')).json()).map((p) => p.name)"
        )
        self.assertNotIn("E2E Round Trip Press", listed)
