"""Arguments index keeps local search state across same-tab in-place refresh.

Uses the real workspace/TabContext coordinator path (prksRenderTabRoute), not
only the Vue session bridge.
"""
from __future__ import annotations

import os
import unittest
from urllib.parse import urlparse

from tests.e2e.fixtures import seed_arguments_library
from tests.e2e.harness import AppServer, open_app_page, require_chromium


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get("PRKS_E2E") == "1" else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close()
    _PW.stop()


class ArgumentsRouteSurfaceTests(unittest.TestCase):
    def start(self):
        server = AppServer(seed_fn=seed_arguments_library)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(
            _BROWSER, server.origin, service_workers="allow"
        )
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        return server, page, context

    def test_index_search_survives_same_tab_arguments_refresh(self):
        _server, page, _context = self.start()
        page.evaluate("() => prksNavigate('#/arguments')")
        page.wait_for_selector("#prks-argument-search", timeout=15000)
        page.wait_for_selector("#prks-argument-rows .prks-research-row", timeout=15000)

        host_before = page.evaluate(
            """() => {
                const tile = document.querySelector('.prks-tile--main');
                const host = tile && tile.querySelector('[data-prks-vue-route-host]');
                if (host) host.setAttribute('data-prks-surface-marker', 'arguments-host');
                return {
                    marker: host ? host.getAttribute('data-prks-surface-marker') : '',
                    view: !!(tile && tile.querySelector('[data-prks-arguments-index-view]')),
                };
            }"""
        )
        self.assertTrue(host_before["view"])
        self.assertEqual(host_before["marker"], "arguments-host")

        search = page.locator("#prks-argument-search")
        search.fill("zzz-nonexistent-query")
        page.wait_for_function(
            "() => document.querySelectorAll('#prks-argument-rows .prks-research-row').length === 0"
        )
        self.assertIn("No Arguments or Stances match", page.locator("#prks-argument-rows").inner_text())
        self.assertEqual(search.input_value(), "zzz-nonexistent-query")

        page.evaluate(
            """() => {
                const ctx = prksGetMainTabContext();
                return prksRenderTabRoute(ctx, '#/arguments', {
                    leaveApproved: true,
                    internalRefresh: true,
                });
            }"""
        )
        page.wait_for_function(
            """() => {
                const tile = document.querySelector('.prks-tile--main');
                const host = tile && tile.querySelector('[data-prks-vue-route-host]');
                const search = document.querySelector('#prks-argument-search');
                return !!(
                    host &&
                    host.getAttribute('data-prks-surface-marker') === 'arguments-host' &&
                    search &&
                    search.value === 'zzz-nonexistent-query' &&
                    document.querySelectorAll('#prks-argument-rows .prks-research-row').length === 0
                );
            }""",
            timeout=15000,
        )
        self.assertEqual(page.locator("#prks-argument-search").input_value(), "zzz-nonexistent-query")
        self.assertEqual(
            page.locator(".prks-tile--main [data-prks-arguments-index-view]").count(),
            1,
        )

        page.evaluate("() => prksNavigate('#/progress?status=Not%20Started')")
        page.wait_for_function("() => location.hash.indexOf('#/progress') === 0")
        page.wait_for_function(
            """() => !document.querySelector('.prks-tile--main [data-prks-arguments-index-view]')""",
            timeout=15000,
        )
        self.assertEqual(
            page.locator(".prks-tile--main [data-prks-arguments-index-view]").count(),
            0,
        )

    def _seed_stale_provenance_banner(self, page):
        seeded = page.evaluate(
            """() => {
                const tile = document.querySelector('.prks-tile--main');
                const root = tile && tile.querySelector('.prks-tab-root');
                const host = root && root.querySelector(':scope > [data-prks-vue-route-host]');
                if (!root || !host) return { ok: false };
                host.setAttribute('data-prks-surface-marker', 'arguments-host');
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

    def _retained_banner_state(self, page):
        return page.evaluate(
            """() => {
                const tile = document.querySelector('.prks-tile--main');
                const root = tile && tile.querySelector('.prks-tab-root');
                const host = root && root.querySelector(':scope > [data-prks-vue-route-host]');
                return {
                    marker: host ? host.getAttribute('data-prks-surface-marker') : '',
                    detail: !!(tile && tile.querySelector('[data-prks-argument-detail-view]')),
                    banners: root
                        ? root.querySelectorAll('[data-prks-role="offline-provenance-banner"]').length
                        : -1,
                    unavailable: !!(
                        tile && tile.querySelector('[data-prks-role="offline-unavailable"]')
                    ),
                };
            }"""
        )

    def test_retained_arguments_clear_stale_provenance_banner_on_not_found(self):
        """Retained contentDiv must not keep a prior route's offline provenance banner."""
        _server, page, _context = self.start()
        page.evaluate("() => prksNavigate('#/arguments')")
        page.wait_for_selector("#prks-argument-search", timeout=15000)
        page.wait_for_selector("#prks-argument-rows .prks-research-row", timeout=15000)

        self._seed_stale_provenance_banner(page)

        page.evaluate("() => prksNavigate('#/arguments/no-such-argument-id-xyz')")
        page.wait_for_function(
            """() => {
                const tile = document.querySelector('.prks-tile--main');
                const title = tile && tile.querySelector('.prks-page-title');
                return !!(title && /Argument not found/i.test(title.textContent || ''));
            }""",
            timeout=15000,
        )
        state = self._retained_banner_state(page)
        self.assertEqual(state["marker"], "arguments-host")
        self.assertTrue(state["detail"])
        self.assertEqual(state["banners"], 0)

    def test_retained_arguments_clear_stale_provenance_banner_on_unavailable(self):
        """Same retained-host clear as not-found, for the offline unavailable outcome."""
        _server, page, context = self.start()
        page.evaluate("() => prksNavigate('#/arguments')")
        page.wait_for_selector("#prks-argument-search", timeout=15000)
        page.wait_for_selector("#prks-argument-rows .prks-research-row", timeout=15000)

        self._seed_stale_provenance_banner(page)

        context.set_offline(True)
        page.evaluate("() => prksOfflineNoteRequestFailure()")
        page.wait_for_function(
            "() => typeof prksOfflineRuntimeState === 'function' && prksOfflineRuntimeState() !== 'online'",
            timeout=20000,
        )

        page.evaluate("() => prksNavigate('#/arguments/no-such-argument-id-xyz')")
        page.wait_for_function(
            """() => {
                const tile = document.querySelector('.prks-tile--main');
                const title = tile && tile.querySelector('.prks-page-title');
                return !!(title && /not available offline/i.test(title.textContent || ''));
            }""",
            timeout=20000,
        )
        state = self._retained_banner_state(page)
        self.assertEqual(state["marker"], "arguments-host")
        self.assertTrue(state["detail"])
        self.assertTrue(state["unavailable"])
        self.assertEqual(state["banners"], 0)

    def test_retained_refresh_error_unmounts_arguments_before_retry(self):
        """A same-route Arguments refresh that receives a non-404 response must
        unmount the Vue tree before the retry view replaces the host. Later
        leave/destroy must dismiss that surface rather than keep it."""
        _server, page, _context = self.start()
        page.evaluate("() => prksNavigate('#/arguments')")
        page.wait_for_selector("[data-prks-arguments-index-view]", timeout=15000)
        page.wait_for_selector("#prks-argument-rows .prks-research-row", timeout=15000)

        def non_404(route):
            path = urlparse(route.request.url).path
            if route.request.method == "GET" and path == "/api/arguments":
                route.fulfill(
                    status=500,
                    content_type="application/json",
                    body='{"error":"arguments unavailable"}',
                )
                return
            route.fallback()

        page.route("**/api/arguments", non_404)
        try:
            page.evaluate(
                """() => {
                    const tile = document.querySelector('.prks-tile--main');
                    const host = tile && tile.querySelector('[data-prks-vue-route-host]');
                    window.__prksArgumentsErrorProbe = { host: host, dismisses: 0, steps: [] };
                    const dismiss = window.prksVueDismissArguments;
                    window.prksVueDismissArguments = function (ctx) {
                        const probe = window.__prksArgumentsErrorProbe;
                        const saved = probe.host;
                        probe.dismisses += 1;
                        probe.steps.push({
                            step: 'dismiss',
                            view: !!(saved && saved.querySelector('[data-prks-arguments-index-view]')),
                            retry: !!document.querySelector('#prks-route-retry'),
                        });
                        const result = dismiss(ctx);
                        probe.steps.push({
                            step: 'dismissed',
                            view: !!(saved && saved.querySelector('[data-prks-arguments-index-view]')),
                            retry: !!document.querySelector('#prks-route-retry'),
                        });
                        return result;
                    };
                    const renderError = window.prksRenderRouteError;
                    window.prksRenderRouteError = function () {
                        const probe = window.__prksArgumentsErrorProbe;
                        const saved = probe.host;
                        probe.steps.push({
                            step: 'retry',
                            view: !!(saved && saved.querySelector('[data-prks-arguments-index-view]')),
                            retry: !!document.querySelector('#prks-route-retry'),
                        });
                        return renderError.apply(this, arguments);
                    };
                }"""
            )
            page.evaluate(
                """() => {
                    const ctx = prksGetMainTabContext();
                    return prksRenderTabRoute(ctx, '#/arguments', {
                        leaveApproved: true,
                        internalRefresh: true,
                    });
                }"""
            )
            page.wait_for_selector("#prks-route-retry", timeout=15000)
            report = page.evaluate(
                """() => {
                    const probe = window.__prksArgumentsErrorProbe;
                    const ctx = prksGetMainTabContext();
                    const session = ctx && ctx.__prksRouteSurface;
                    return {
                        steps: probe.steps,
                        dismisses: probe.dismisses,
                        cleanupCount: ctx.debugSnapshot().cleanupCount,
                        cleanupArmed: !!ctx.__prksArgumentsCleanupArmed,
                        mountedHost: !!(session && session.mountedHost),
                        retry: !!document.querySelector('#prks-route-retry'),
                        viewInDocument: !!document.querySelector('[data-prks-arguments-index-view]'),
                        viewOnSavedHost: !!(
                            probe.host && probe.host.querySelector('[data-prks-arguments-index-view]')
                        ),
                    };
                }"""
            )
            self.assertGreaterEqual(report["dismisses"], 1)
            self.assertEqual(report["steps"][0]["step"], "dismiss")
            self.assertTrue(report["steps"][0]["view"])
            self.assertFalse(report["steps"][0]["retry"])
            dismissed = next(step for step in report["steps"] if step["step"] == "dismissed")
            retry = next(step for step in report["steps"] if step["step"] == "retry")
            self.assertLess(
                report["steps"].index(dismissed),
                report["steps"].index(retry),
            )
            self.assertFalse(dismissed["view"])
            self.assertFalse(dismissed["retry"])
            self.assertFalse(retry["view"])
            self.assertFalse(retry["retry"])
            self.assertTrue(report["retry"])
            self.assertFalse(report["viewInDocument"])
            self.assertFalse(report["viewOnSavedHost"])
            self.assertFalse(report["mountedHost"])
            self.assertGreater(report["cleanupCount"], 0)
            self.assertTrue(report["cleanupArmed"])

            left = page.evaluate(
                """() => {
                    const probe = window.__prksArgumentsErrorProbe;
                    const ctx = prksGetMainTabContext();
                    ctx.destroy();
                    const session = ctx.__prksRouteSurface;
                    return {
                        cleanupArmed: !!ctx.__prksArgumentsCleanupArmed,
                        mountedHost: !!(session && session.mountedHost),
                        viewOnSavedHost: !!(
                            probe.host && probe.host.querySelector('[data-prks-arguments-index-view]')
                        ),
                        viewInDocument: !!document.querySelector('[data-prks-arguments-index-view]'),
                    };
                }"""
            )
            self.assertFalse(left["cleanupArmed"])
            self.assertFalse(left["mountedHost"])
            self.assertFalse(left["viewOnSavedHost"])
            self.assertFalse(left["viewInDocument"])
        finally:
            page.unroute("**/api/arguments", non_404)
