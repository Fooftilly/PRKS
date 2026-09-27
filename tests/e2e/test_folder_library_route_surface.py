"""Folder Library Vue surface: retain refresh + #170 preview teardown.

Uses the real workspace/TabContext coordinator path (prksRenderTabRoute).
"""
from __future__ import annotations

import os
import unittest

from tests.e2e.fixtures import seed_graph_context_library
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
        server = AppServer(seed_fn=seed_graph_context_library)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(
            _BROWSER, server.origin, service_workers="allow"
        )
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        return server, page, context

    def test_folder_filter_survives_same_tab_library_refresh(self):
        _server, page, _context = self.start()
        page.evaluate("() => prksNavigate('#/folders')")
        page.wait_for_selector("[data-prks-folder-library-view]", timeout=15000)
        page.wait_for_selector("#prks-folder-library-search", timeout=15000)

        host_before = page.evaluate(
            """() => {
                const tile = document.querySelector('.prks-tile--main');
                const host = tile && tile.querySelector('[data-prks-vue-route-host]');
                if (host) host.setAttribute('data-prks-surface-marker', 'folder-library-host');
                return {
                    marker: host ? host.getAttribute('data-prks-surface-marker') : '',
                    view: !!(tile && tile.querySelector('[data-prks-folder-library-view]')),
                };
            }"""
        )
        self.assertTrue(host_before["view"])
        self.assertEqual(host_before["marker"], "folder-library-host")

        search = page.locator("#prks-folder-library-search")
        search.fill("zzz-nonexistent-folder-query")
        page.wait_for_function(
            """() => {
                const input = document.querySelector('#prks-folder-library-search');
                return !!(input && input.value === 'zzz-nonexistent-folder-query');
            }"""
        )

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
                    host.getAttribute('data-prks-surface-marker') === 'folder-library-host' &&
                    search &&
                    search.value === 'zzz-nonexistent-folder-query' &&
                    tile.querySelector('[data-prks-folder-library-view]')
                );
            }""",
            timeout=15000,
        )
        self.assertEqual(
            page.locator("#prks-folder-library-search").input_value(),
            "zzz-nonexistent-folder-query",
        )

    def test_recently_added_preview_released_when_switching_to_folders_tab(self):
        """#170: Folders ↔ Recently Added must dismiss body-mounted quick preview."""
        _server, page, _context = self.start()
        page.evaluate("() => prksNavigate('#/folders')")
        page.wait_for_selector("[data-prks-folder-library-view]", timeout=15000)

        page.locator('.prks-folder-library__tab-btn[data-tab="recently-added"]').click()
        page.wait_for_selector("#prks-folder-library-recently-added", timeout=15000)

        # Seed a visible body-mounted preview owned by a card inside Recently
        # Added so scoped release (root.contains(source)) models the real path.
        seeded = page.evaluate(
            """() => {
                const pane = document.querySelector('#prks-folder-library-recently-added');
                if (!pane) return { visible: false, owned: false };
                let el = document.getElementById('prks-work-thumb-preview');
                if (!el) {
                    el = document.createElement('div');
                    el.id = 'prks-work-thumb-preview';
                    document.body.appendChild(el);
                }
                el.hidden = false;
                el.style.display = 'block';
                el.textContent = 'stale-preview';
                const source = document.createElement('img');
                source.setAttribute('data-prks-e2e-preview-source', '1');
                pane.appendChild(source);
                window.__prksWorkThumbPreviewSource = source;
                return {
                    visible: !el.hidden && el.style.display !== 'none',
                    owned: !!(pane.contains(source)),
                };
            }"""
        )
        self.assertTrue(seeded["visible"])
        self.assertTrue(seeded["owned"])

        page.locator('.prks-folder-library__tab-btn[data-tab="folders"]').click()
        page.wait_for_function(
            """() => {
                const foldersPane = document.querySelector('[data-pane="folders"]');
                const preview = document.getElementById('prks-work-thumb-preview');
                const foldersVisible = foldersPane && !foldersPane.classList.contains('is-hidden');
                const previewGone = !preview || preview.hidden || preview.style.display === 'none';
                return !!(foldersVisible && previewGone);
            }""",
            timeout=15000,
        )

    def test_leaving_folder_library_dismisses_vue_surface(self):
        _server, page, _context = self.start()
        page.evaluate("() => prksNavigate('#/folders')")
        page.wait_for_selector("[data-prks-folder-library-view]", timeout=15000)

        page.evaluate("() => prksNavigate('#/concepts')")
        page.wait_for_function(
            """() => !document.querySelector('.prks-tile--main [data-prks-folder-library-view]')""",
            timeout=15000,
        )
        self.assertEqual(
            page.locator(".prks-tile--main [data-prks-folder-library-view]").count(),
            0,
        )
