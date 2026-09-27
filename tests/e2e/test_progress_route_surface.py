"""Main Progress stays mounted while a Secondary TabContext navigates.

Uses the real workspace/TabContext path. Owner isolation that can be proven
with mocked owners lives in the route-surface Vitest file.
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


class ProgressRouteSurfaceTests(unittest.TestCase):
    def start(self):
        server = AppServer(seed_fn=seed_library)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(
            _BROWSER, server.origin, service_workers="allow"
        )
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        return server, page

    def progress_snapshot(self, page):
        return page.evaluate(
            """() => {
                const tile = document.querySelector('.prks-tile--main');
                const view = tile && tile.querySelector('[data-prks-progress-view]');
                return {
                    mounted: !!view,
                    marker: view ? view.getAttribute('data-prks-surface-marker') : '',
                    title: tile && tile.querySelector('.prks-page-title')
                        ? tile.querySelector('.prks-page-title').textContent
                        : '',
                    ids: Array.from(tile ? tile.querySelectorAll('[data-work-id]') : []).map(
                        (node) => node.getAttribute('data-work-id')
                    ),
                };
            }"""
        )

    def test_main_progress_stays_mounted_when_secondary_navigates(self):
        server, page = self.start()
        work_b = server.ids["work_b"]
        work_a = server.ids["work_a"]
        person = server.ids["person"]

        page.evaluate("() => prksNavigate('#/progress?status=Not%20Started')")
        page.wait_for_selector(
            f".prks-tile--main [data-work-id='{work_b}']",
            timeout=15000,
        )
        page.evaluate(
            """() => {
                const view = document.querySelector('.prks-tile--main [data-prks-progress-view]');
                if (view) view.setAttribute('data-prks-surface-marker', 'main-progress');
            }"""
        )
        before = self.progress_snapshot(page)
        self.assertTrue(before["mounted"])
        self.assertEqual(before["marker"], "main-progress")
        self.assertEqual(before["title"], "Files · Not Started")
        self.assertEqual(before["ids"], [work_b])
        self.assertEqual(
            page.locator('.nav-link.active[data-status="Not Started"]').count(),
            1,
        )

        page.evaluate(
            "(id) => prksNavigate('#/people/' + id, { target: 'tile' })",
            person,
        )
        page.wait_for_selector(".prks-tile--secondary .person-profile", timeout=15000)
        page.wait_for_function(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                return !!(snap && snap.mode === 'tiled' && snap.secondaryTree && snap.secondaryTree.tabId);
            }"""
        )
        self.assertEqual(self.progress_snapshot(page), before)

        secondary_id = page.evaluate(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                return snap.secondaryTree.tabId;
            }"""
        )
        page.evaluate(
            """({ tabId, workId }) => prksWorkspaceNavigate('#/works/' + workId, { tabId })""",
            {"tabId": secondary_id, "workId": work_a},
        )
        page.wait_for_selector(".prks-tile--secondary .work-detail", timeout=15000)
        page.wait_for_selector(
            f".prks-tile--secondary [data-work-id='{work_a}'], .prks-tile--secondary .work-detail",
            timeout=15000,
        )

        after = self.progress_snapshot(page)
        self.assertEqual(after, before)
        self.assertEqual(page.locator(".prks-tile--main [data-prks-progress-view]").count(), 1)
        self.assertEqual(page.locator(".prks-tile--secondary .work-detail").count(), 1)
        self.assertEqual(page.locator(".prks-tile--secondary [data-prks-progress-view]").count(), 0)
        self.assertEqual(
            page.locator('.nav-link.active[data-status="Not Started"]').count(),
            1,
        )
        self.assertEqual(page.evaluate("() => location.hash"), "#/progress?status=Not%20Started")
