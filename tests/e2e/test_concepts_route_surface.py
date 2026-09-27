"""Concepts index keeps local search state across same-tab in-place refresh.

Uses the real workspace/TabContext coordinator path (prksRenderTabRoute), not
only the Vue session bridge.
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


class ConceptsRouteSurfaceTests(unittest.TestCase):
    def start(self):
        server = AppServer(seed_fn=seed_graph_context_library)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(
            _BROWSER, server.origin, service_workers="allow"
        )
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        return server, page

    def test_index_search_survives_same_tab_concepts_refresh(self):
        _server, page = self.start()
        page.evaluate("() => prksNavigate('#/concepts')")
        page.wait_for_selector("#prks-concept-search", timeout=15000)
        page.wait_for_selector("#prks-concept-rows .prks-research-row", timeout=15000)

        host_before = page.evaluate(
            """() => {
                const tile = document.querySelector('.prks-tile--main');
                const host = tile && tile.querySelector('[data-prks-vue-route-host]');
                if (host) host.setAttribute('data-prks-surface-marker', 'concepts-host');
                return {
                    marker: host ? host.getAttribute('data-prks-surface-marker') : '',
                    view: !!(tile && tile.querySelector('[data-prks-concepts-index-view]')),
                };
            }"""
        )
        self.assertTrue(host_before["view"])
        self.assertEqual(host_before["marker"], "concepts-host")

        search = page.locator("#prks-concept-search")
        search.fill("zzz-nonexistent-query")
        page.wait_for_function(
            "() => document.querySelectorAll('#prks-concept-rows .prks-research-row').length === 0"
        )
        self.assertEqual(search.input_value(), "zzz-nonexistent-query")

        page.evaluate(
            """() => {
                const ctx = prksGetMainTabContext();
                return prksRenderTabRoute(ctx, '#/concepts', {
                    leaveApproved: true,
                    internalRefresh: true,
                });
            }"""
        )
        page.wait_for_function(
            """() => {
                const tile = document.querySelector('.prks-tile--main');
                const host = tile && tile.querySelector('[data-prks-vue-route-host]');
                const search = document.querySelector('#prks-concept-search');
                return !!(
                    host &&
                    host.getAttribute('data-prks-surface-marker') === 'concepts-host' &&
                    search &&
                    search.value === 'zzz-nonexistent-query' &&
                    document.querySelectorAll('#prks-concept-rows .prks-research-row').length === 0
                );
            }""",
            timeout=15000,
        )
        self.assertEqual(page.locator("#prks-concept-search").input_value(), "zzz-nonexistent-query")
        self.assertEqual(
            page.locator(".prks-tile--main [data-prks-concepts-index-view]").count(),
            1,
        )

        page.evaluate("() => prksNavigate('#/progress?status=Not%20Started')")
        page.wait_for_function("() => location.hash.indexOf('#/progress') === 0")
        page.wait_for_function(
            """() => !document.querySelector('.prks-tile--main [data-prks-concepts-index-view]')""",
            timeout=15000,
        )
        self.assertEqual(
            page.locator(".prks-tile--main [data-prks-concepts-index-view]").count(),
            0,
        )

    def test_retained_concepts_clear_stale_provenance_banner_on_not_found(self):
        """Retained contentDiv must not keep a prior route's offline provenance banner."""
        _server, page = self.start()
        page.evaluate("() => prksNavigate('#/concepts')")
        page.wait_for_selector("#prks-concept-search", timeout=15000)
        page.wait_for_selector("#prks-concept-rows .prks-research-row", timeout=15000)

        seeded = page.evaluate(
            """() => {
                const tile = document.querySelector('.prks-tile--main');
                const root = tile && tile.querySelector('.prks-tab-root');
                const host = root && root.querySelector(':scope > [data-prks-vue-route-host]');
                if (!root || !host) return { ok: false };
                host.setAttribute('data-prks-surface-marker', 'concepts-host');
                const banner = document.createElement('div');
                banner.setAttribute('data-prks-role', 'offline-provenance-banner');
                banner.className = 'prks-offline-banner';
                banner.textContent = 'Offline · cached index (stale test banner)';
                root.insertBefore(banner, root.firstChild);
                return {
                    ok: true,
                    banners: root.querySelectorAll('[data-prks-role="offline-provenance-banner"]').length,
                };
            }"""
        )
        self.assertTrue(seeded["ok"])
        self.assertEqual(seeded["banners"], 1)

        page.evaluate("() => prksNavigate('#/concepts/no-such-concept-id-xyz')")
        page.wait_for_function(
            """() => {
                const tile = document.querySelector('.prks-tile--main');
                const title = tile && tile.querySelector('.prks-page-title');
                return !!(title && /Concept not found/i.test(title.textContent || ''));
            }""",
            timeout=15000,
        )
        state = page.evaluate(
            """() => {
                const tile = document.querySelector('.prks-tile--main');
                const root = tile && tile.querySelector('.prks-tab-root');
                const host = root && root.querySelector(':scope > [data-prks-vue-route-host]');
                return {
                    marker: host ? host.getAttribute('data-prks-surface-marker') : '',
                    detail: !!(tile && tile.querySelector('[data-prks-concept-detail-view]')),
                    banners: root
                        ? root.querySelectorAll('[data-prks-role="offline-provenance-banner"]').length
                        : -1,
                };
            }"""
        )
        self.assertEqual(state["marker"], "concepts-host")
        self.assertTrue(state["detail"])
        self.assertEqual(state["banners"], 0)
