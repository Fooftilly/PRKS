"""Playlists index/detail keeps its Vue host across a same-tab refresh.

Uses the real workspace coordinator (prksRenderTabRoute), not only the session
bridge. Also checks the empty-index New playlist control (#84).
"""
from __future__ import annotations

import os
import unittest

from tests.e2e.fixtures import (
    PRKSDatabase,
    SCHEMA,
    StorageConfig,
    seed_playlists_library,
)
from tests.e2e.harness import AppServer, open_app_page, require_chromium


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get("PRKS_E2E") == "1" else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close()
    _PW.stop()


class PlaylistsRouteSurfaceTests(unittest.TestCase):
    def start(self, seed=seed_playlists_library):
        server = AppServer(seed_fn=seed)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(
            _BROWSER, server.origin, service_workers="allow"
        )
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        return server, page

    def test_index_host_survives_same_tab_refresh(self):
        _server, page = self.start()
        page.evaluate("() => prksNavigate('#/playlists')")
        page.wait_for_selector("#prks-playlists-header-new", timeout=15000)
        page.wait_for_selector(".playlists-page__list-item", timeout=15000)
        host_before = page.evaluate(
            """() => {
                const tile = document.querySelector('.prks-tile--main');
                const host = tile && tile.querySelector('[data-prks-vue-route-host]');
                if (host) host.setAttribute('data-prks-surface-marker', 'playlists-host');
                return {
                    marker: host ? host.getAttribute('data-prks-surface-marker') : '',
                    view: !!(tile && tile.querySelector('[data-prks-playlists-index-view]')),
                };
            }"""
        )
        self.assertTrue(host_before["view"])
        self.assertEqual(host_before["marker"], "playlists-host")
        before_generation = page.evaluate("() => prksGetMainTabContext().generation")
        page.evaluate(
            """() => {
                const ctx = prksGetMainTabContext();
                return prksRenderTabRoute(ctx, '#/playlists', {
                    leaveApproved: true,
                    internalRefresh: true,
                });
            }"""
        )
        page.wait_for_function(
            """(before) => {
                const ctx = prksGetMainTabContext();
                const tile = document.querySelector('.prks-tile--main');
                const host = tile && tile.querySelector('[data-prks-vue-route-host]');
                return !!(
                    ctx && ctx.generation > before &&
                    host &&
                    host.getAttribute('data-prks-surface-marker') === 'playlists-host' &&
                    tile.querySelector('[data-prks-playlists-index-view]') &&
                    document.querySelector('#prks-playlists-header-new')
                );
            }""",
            arg=before_generation,
            timeout=15000,
        )
        self.assertEqual(
            page.locator(".prks-tile--main [data-prks-playlists-index-view]").count(),
            1,
        )

    def test_detail_host_survives_same_tab_refresh(self):
        server, page = self.start()
        playlist_id = server.ids["playlist_a"]
        page.evaluate(
            "id => prksNavigate('#/playlists/' + encodeURIComponent(id))",
            playlist_id,
        )
        page.wait_for_selector(".prks-playlist-detail", timeout=15000)
        page.wait_for_selector("#prks-playlist-delete-btn", timeout=15000)
        page.evaluate(
            """() => {
                const tile = document.querySelector('.prks-tile--main');
                const host = tile && tile.querySelector('[data-prks-vue-route-host]');
                if (host) host.setAttribute('data-prks-surface-marker', 'playlist-detail-host');
            }"""
        )
        before_generation = page.evaluate("() => prksGetMainTabContext().generation")
        page.evaluate(
            """id => {
                const ctx = prksGetMainTabContext();
                return prksRenderTabRoute(ctx, '#/playlists/' + encodeURIComponent(id), {
                    leaveApproved: true,
                    internalRefresh: true,
                });
            }""",
            playlist_id,
        )
        page.wait_for_function(
            """(before) => {
                const ctx = prksGetMainTabContext();
                const tile = document.querySelector('.prks-tile--main');
                const host = tile && tile.querySelector('[data-prks-vue-route-host]');
                return !!(
                    ctx && ctx.generation > before &&
                    host &&
                    host.getAttribute('data-prks-surface-marker') === 'playlist-detail-host' &&
                    tile.querySelector('[data-prks-playlist-detail-view]') &&
                    document.querySelector('#prks-playlist-delete-btn')
                );
            }""",
            arg=before_generation,
            timeout=15000,
        )

    def test_empty_index_offers_new_playlist_in_the_page(self):
        def empty(root):
            ids = seed_playlists_library(root)
            db = PRKSDatabase(storage=StorageConfig.for_testing(root), schema_path=str(SCHEMA))
            db.delete_playlist(ids["playlist_a"])
            db.delete_playlist(ids["playlist_b"])
            return ids

        _server, page = self.start(empty)
        page.evaluate("() => prksNavigate('#/playlists')")
        page.wait_for_selector("#prks-playlists-header-new", timeout=15000)
        page.wait_for_selector("#prks-playlists-empty-new", timeout=15000)
        body = page.locator(".prks-tile--main").inner_text()
        self.assertIn("No playlists yet.", body)
        self.assertIn("New playlist", page.locator("#prks-playlists-header-new").inner_text())
        self.assertIn("New playlist", page.locator("#prks-playlists-empty-new").inner_text())
        self.assertEqual(page.locator("#prks-playlists-empty-new").count(), 1)

    def test_secondary_new_playlist_navigates_only_that_owner(self):
        server, page = self.start()
        person = server.ids["person"]
        playlist_id = server.ids["playlist_a"]
        page.evaluate("(id) => prksNavigate('#/people/' + id)", person)
        page.wait_for_selector(".prks-tile--main .person-profile", timeout=15000)
        main_hash = page.evaluate("() => location.hash")
        page.evaluate(
            "(id) => prksNavigate('#/playlists/' + encodeURIComponent(id), { target: 'tile' })",
            playlist_id,
        )
        page.wait_for_selector(".prks-tile--secondary .prks-playlist-detail", timeout=15000)
        page.wait_for_function(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                return !!(
                    snap && snap.mode === 'tiled' &&
                    snap.secondaryTree && snap.secondaryTree.tabId
                );
            }"""
        )
        secondary_id = page.evaluate(
            "() => window.prksWorkspaceSnapshot().secondaryTree.tabId"
        )
        page.evaluate(
            """(tabId) => {
                const ctx = prksGetTabContext(tabId);
                return prksRenderTabRoute(ctx, '#/playlists', { leaveApproved: true });
            }""",
            secondary_id,
        )
        page.wait_for_function(
            """(tabId) => {
                const ctx = prksGetTabContext(tabId);
                const route = ctx && ctx.lastResolvedRoute;
                const tile = document.querySelector('.prks-tile--secondary');
                return !!(
                    route && route.name === 'playlists' &&
                    tile && tile.querySelector('#prks-playlists-header-new')
                );
            }""",
            arg=secondary_id,
            timeout=15000,
        )
        main_name = page.evaluate(
            """() => {
                const main = prksGetMainTabContext();
                const route = main && (main.lastResolvedRoute || main.route);
                return route ? route.name : '';
            }"""
        )
        self.assertEqual(main_name, "person")
        page.locator(".prks-tile--secondary #prks-playlists-header-new").click()
        page.wait_for_selector("#playlist-modal:not(.hidden)", timeout=10000)
        page.fill("#playlist-title", "Secondary created playlist")
        page.click("#save-playlist-btn")
        page.wait_for_function(
            """(tabId) => {
                const ctx = prksGetTabContext(tabId);
                const route = ctx && ctx.lastResolvedRoute;
                return !!(
                    route &&
                    route.name === 'playlist-detail' &&
                    String(route.canonicalHash || '').indexOf('#/playlists/') === 0
                );
            }""",
            arg=secondary_id,
            timeout=15000,
        )
        page.wait_for_selector(".prks-tile--secondary .prks-playlist-detail", timeout=15000)
        self.assertEqual(page.evaluate("() => location.hash"), main_hash)
        main_after = page.evaluate(
            """() => {
                const main = prksGetMainTabContext();
                const route = main && (main.lastResolvedRoute || main.route);
                return route ? route.name : '';
            }"""
        )
        self.assertNotEqual(main_after, "playlist-detail")
        self.assertEqual(page.locator(".prks-tile--main .prks-playlist-detail").count(), 0)
        self.assertEqual(page.locator(".prks-tile--secondary .prks-playlist-detail").count(), 1)
        self.assertGreater(page.locator(".prks-tile--main .person-profile").count(), 0)
