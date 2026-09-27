"""Folder Library Vue surface: retain + #170 preview/thumb lifecycle.

Uses the real workspace/TabContext coordinator path (prksRenderTabRoute).
"""
from __future__ import annotations

import os
import unittest

from tests.e2e.fixtures import seed_folders_library
from tests.e2e.harness import AppServer, open_app_page, require_chromium


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get("PRKS_E2E") == "1" else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close()
    _PW.stop()


class FolderLibraryRouteSurfaceTests(unittest.TestCase):
    def start(self):
        server = AppServer(seed_fn=seed_folders_library)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(
            _BROWSER, server.origin, service_workers="allow"
        )
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        return server, page, context, server.ids

    def test_filter_survives_same_tab_folders_refresh(self):
        _server, page, _context, _ids = self.start()
        page.evaluate("() => prksNavigate('#/folders')")
        page.wait_for_selector(
            ".prks-tile--main [data-prks-folder-library-view]", timeout=15000
        )
        page.wait_for_selector("#prks-folder-library-search", timeout=15000)

        host_before = page.evaluate(
            """() => {
                const tile = document.querySelector('.prks-tile--main');
                const host = tile && tile.querySelector('[data-prks-vue-route-host]');
                if (host) host.setAttribute('data-prks-surface-marker', 'folders-host');
                return {
                    marker: host ? host.getAttribute('data-prks-surface-marker') : '',
                    view: !!(tile && tile.querySelector('[data-prks-folder-library-view]')),
                };
            }"""
        )
        self.assertTrue(host_before["view"])
        self.assertEqual(host_before["marker"], "folders-host")

        search = page.locator("#prks-folder-library-search")
        search.fill("zzz-nonexistent-folder-query")
        page.wait_for_function(
            """() => {
                const host = document.querySelector(
                    '.prks-tile--main [data-prks-folder-tree-host]'
                );
                return !!(host && host.textContent &&
                    host.textContent.indexOf('No folders match') !== -1);
            }""",
            timeout=10000,
        )
        self.assertEqual(search.input_value(), "zzz-nonexistent-folder-query")

        page.evaluate(
            """() => {
                const ctx = prksGetMainTabContext();
                return prksRenderTabRoute(ctx, '#/folders', {
                    leaveApproved: true,
                    internalRefresh: true,
                });
            }"""
        )
        page.wait_for_function(
            """() => {
                const tile = document.querySelector('.prks-tile--main');
                const host = tile && tile.querySelector('[data-prks-vue-route-host]');
                const search = document.querySelector('#prks-folder-library-search');
                return !!(
                    host &&
                    host.getAttribute('data-prks-surface-marker') === 'folders-host' &&
                    search &&
                    search.value === 'zzz-nonexistent-folder-query'
                );
            }""",
            timeout=15000,
        )
        self.assertEqual(
            page.locator("#prks-folder-library-search").input_value(),
            "zzz-nonexistent-folder-query",
        )

        page.evaluate("() => prksNavigate('#/progress?status=Not%20Started')")
        page.wait_for_function("() => location.hash.indexOf('#/progress') === 0")
        page.wait_for_function(
            """() => !document.querySelector(
                '.prks-tile--main [data-prks-folder-library-view]'
            )""",
            timeout=15000,
        )

    def test_recently_added_preview_released_on_tab_switch(self):
        """#170: P on Recently added → Folders tab clears body-mounted preview."""
        _server, page, _context, ids = self.start()
        page.evaluate("() => prksNavigate('#/folders')")
        page.wait_for_selector(
            ".prks-tile--main [data-prks-folder-library-view]", timeout=15000
        )
        page.locator(
            '.prks-folder-library__tab-btn[data-tab="recently-added"]'
        ).click()
        page.wait_for_selector(
            f"#prks-folder-library-recently-added [data-work-id='{ids['work_a']}']",
            timeout=15000,
        )
        card = page.locator(
            f"#prks-folder-library-recently-added [data-work-id='{ids['work_a']}']"
        ).first
        card.focus()
        page.keyboard.press("p")
        page.wait_for_selector(
            "#prks-work-thumb-preview.work-card-preview--visible", timeout=10000
        )

        page.locator('.prks-folder-library__tab-btn[data-tab="folders"]').click()
        page.wait_for_function(
            """() => {
                const el = document.getElementById('prks-work-thumb-preview');
                const srcGone = !window.__prksWorkThumbPreviewSource;
                return srcGone && (!el || el.hidden ||
                    !el.classList.contains('work-card-preview--visible'));
            }""",
            timeout=10000,
        )
        self.assertTrue(
            page.locator(
                '.prks-folder-library__pane[data-pane="folders"]:not(.is-hidden)'
            ).count()
            >= 1
        )
