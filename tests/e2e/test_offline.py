"""Real Chromium + real PRKS server offline/PWA scenarios (Phase 1, read-only).

Collected only when PRKS_E2E=1 (see tests/e2e/run.py). These scenarios need a
real Service Worker and real IndexedDB, so -- unlike tests.e2e.test_app, which
deliberately blocks service workers for determinism -- every context here is
opened with service_workers="allow".
"""
from __future__ import annotations

import os
import unittest

from tests.e2e.fixtures import WORK_A_TITLE, WORK_B_TITLE, seed_library
from tests.e2e.harness import AppServer, PageCollector, open_app_page, require_chromium
from tests.e2e.test_app import (
    _FOCUSED_WORK_NOTES,
    _open_details_drawer_if_tiled,
    _open_work_from_home,
    _wait_pdf_viewer,
)


def load_tests(loader, standard_tests, pattern):
    if os.environ.get("PRKS_E2E") != "1":
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


def _wait_sw_active(page):
    page.evaluate(
        """() => navigator.serviceWorker && navigator.serviceWorker.ready
            ? navigator.serviceWorker.ready.then(() => true)
            : Promise.resolve(false)"""
    )
    page.wait_for_function(
        "() => !!(navigator.serviceWorker && navigator.serviceWorker.controller)"
    )


def _wait_entity_cached(page, kind, entity_id, timeout=15000):
    page.wait_for_function(
        """([kind, id]) => {
            if (typeof window.createPrksOfflineStore !== 'function') return false;
            const store = window.createPrksOfflineStore();
            return store.getEntity(kind, id).then(v => !!v);
        }""",
        arg=[kind, entity_id],
        timeout=timeout,
    )


def _wait_pdf_whole_file_cached(page, pdf_path, timeout=15000):
    page.wait_for_function(
        """(path) => {
            if (typeof caches === 'undefined') return false;
            return caches.open('prks-pdf-v1').then(c => c.match(path)).then(m => !!m);
        }""",
        arg=pdf_path,
        timeout=timeout,
    )


def _connectivity_state(page):
    return page.evaluate(
        "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null)"
    )


class OfflineFoundationTests(unittest.TestCase):
    def _start(self, seed_fn=seed_library):
        server = AppServer(seed_fn=seed_fn)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin, service_workers="allow")
        self.addCleanup(context.close)
        return server, page, context, collector

    def test_cached_work_renders_offline_after_reload(self):
        """Scenario 1: online load, open Work A, wait for cache, go offline, reload --
        shell loads, Work A renders from cache, offline state is visible."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)
        self.assertEqual(_connectivity_state(page), "online")
        self.assertTrue(page.locator("#prks-connectivity-indicator[hidden]").count() >= 1)

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")

        page.wait_for_selector("#sidebar")
        self.assertIn(work_a, page.evaluate("() => location.hash"))
        page.wait_for_function("() => document.body.innerText.indexOf(%r) !== -1" % WORK_A_TITLE)
        page.locator('[data-prks-role="offline-provenance-banner"]', has_text="Offline").wait_for()
        page.wait_for_function("() => !document.getElementById('prks-connectivity-indicator').hidden")
        self.assertIn("Offline", page.locator("#prks-connectivity-indicator").inner_text())
        self.assertEqual(_connectivity_state(page), "offline")

    def test_offline_open_of_uncached_work_shows_unavailable(self):
        """Scenario 2: offline navigation to a Work never opened online -- a clean
        offline-unavailable state, never a false "not found"."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]
        work_b = server.ids["work_b"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("() => document.body.innerText.indexOf(%r) !== -1" % WORK_A_TITLE)

        page.evaluate("id => { void window.prksNavigate('#/works/' + id); }", work_b)
        page.wait_for_selector('[data-prks-role="offline-unavailable"]')
        self.assertIn(
            "This item is not available offline.",
            page.locator('[data-prks-role="offline-unavailable"]').inner_text(),
        )
        self.assertNotIn(WORK_B_TITLE, page.locator("#page-content").inner_text())

    def test_previously_opened_pdf_reopens_offline(self):
        """Scenario 3: a fully opened PDF is cached whole-file; offline, reopening
        the same Work re-renders that same PDF from the service worker's cache."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]
        pdf_path = "/api/pdfs/%s" % server.ids["pdf_name"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        _wait_pdf_whole_file_cached(page, pdf_path)

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        self.assertIn(work_a, page.evaluate("() => location.hash"))
        _wait_pdf_viewer(page)
        self.assertGreaterEqual(
            page.evaluate(
                """() => {
                    const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                    const pdf = ctx && ctx.getResource ? ctx.getResource('pdf') : null;
                    const v = pdf && pdf.viewer;
                    return v && v.getPageCount ? v.getPageCount() : 0;
                }"""
            ),
            1,
        )

    def test_offline_mutation_is_blocked_not_faked(self):
        """Scenario 4: an offline mutation attempt never reaches the network and is
        never silently accepted -- the user sees an explicit requires-connection message."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("() => document.body.innerText.indexOf(%r) !== -1" % WORK_A_TITLE)
        page.wait_for_function("() => !document.getElementById('prks-connectivity-indicator').hidden")

        mutation_requests = []
        page.on(
            "request",
            lambda req: mutation_requests.append(req.method)
            if req.method in ("POST", "PUT", "PATCH", "DELETE") and "/api/works/" in req.url
            else None,
        )

        _open_details_drawer_if_tiled(page)
        # The Delete File button lives inside the "More" advanced disclosure.
        advanced = page.locator(".work-details-advanced")
        if advanced.get_attribute("open") is None:
            advanced.locator("summary").click()
        delete_btn = page.locator(".delete-work-btn")
        delete_btn.wait_for()
        delete_btn.click()

        page.locator("#prks-modal-confirm-title").wait_for()
        self.assertEqual(page.locator("#prks-modal-confirm-title").inner_text(), "Offline")
        self.assertIn(
            "This change requires a connection to PRKS.",
            page.locator("#prks-modal-confirm-desc").inner_text(),
        )
        # Single-button alert: no destructive "Delete file" confirm ever appeared.
        self.assertTrue(page.locator("#prks-modal-confirm-cancel.hidden").count() >= 1)
        page.locator("#prks-modal-confirm-ok").click()
        page.locator("#prks-modal-confirm:not(.hidden)").wait_for(state="detached", timeout=5000)

        self.assertEqual(mutation_requests, [])
        # Work A must still exist server-side -- nothing was silently "succeeded" client-side.
        context.set_offline(False)
        import urllib.request

        with urllib.request.urlopen(server.origin + "/api/works/" + work_a) as res:
            self.assertEqual(res.status, 200)

    def test_reconnect_refreshes_focused_route_with_server_data(self):
        """Scenario 5: restoring connectivity naturally returns the focused, previously
        cache-served route to fresh authoritative server data."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("() => document.body.innerText.indexOf(%r) !== -1" % WORK_A_TITLE)
        page.locator('[data-prks-role="offline-provenance-banner"]').wait_for()
        self.assertEqual(_connectivity_state(page), "offline")

        context.set_offline(False)
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'online'",
            timeout=20000,
        )
        page.locator('[data-prks-role="offline-provenance-banner"]').wait_for(state="detached", timeout=20000)
        page.wait_for_function("() => document.getElementById('prks-connectivity-indicator').hidden")

    def test_online_prks_works_with_indexeddb_disabled(self):
        """Scenario 6: IndexedDB unavailable never blocks ordinary online PRKS use."""
        server = AppServer(seed_fn=seed_library)
        self.addCleanup(server.stop)
        server.start()
        context = _BROWSER.new_context(
            viewport={"width": 1400, "height": 900},
            service_workers="allow",
        )
        self.addCleanup(context.close)
        context.add_init_script(
            "Object.defineProperty(window, 'indexedDB', { value: undefined, configurable: true });"
        )
        page = context.new_page()
        collector = PageCollector(page, server.origin)
        self.addCleanup(collector.assert_clean)
        page.goto(server.origin + "/", wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        page.wait_for_selector("#page-content")
        page.locator('#sidebar a.nav-link[href="#/folders"]').click()
        page.wait_for_function("() => location.hash === '#/folders'")

        self.assertEqual(page.evaluate("() => typeof window.indexedDB"), "undefined")
        available = page.evaluate(
            """() => (typeof prksOfflineDiagnostics === 'function'
                ? prksOfflineDiagnostics().then(d => d.available)
                : null)"""
        )
        self.assertEqual(available, False)

        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        self.assertEqual(_connectivity_state(page), "online")

    def test_research_notes_are_read_only_immediately_when_offline(self):
        """Scenario 7: reopening a cached Work directly offline must never leave
        the Research Notes editor briefly editable while waiting for a later
        connectivity-subscriber callback -- it starts read-only immediately."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)
        page.wait_for_selector(".CodeMirror")
        original_text = page.evaluate("() => %s.value()" % _FOCUSED_WORK_NOTES)

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        self.assertIn(work_a, page.evaluate("() => location.hash"))
        page.wait_for_selector(".CodeMirror")

        self.assertTrue(
            page.evaluate(
                """() => {
                    const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                    const notes = ctx && ctx.getResource ? ctx.getResource('workNotes') : null;
                    const cm = notes && notes.editor && notes.editor.codemirror;
                    return !!(cm && cm.getOption('readOnly'));
                }"""
            )
        )
        page.locator(
            '[data-prks-role="editor-status"]', has_text="Offline — notes are read-only"
        ).wait_for()

        page.locator(".CodeMirror").click()
        page.keyboard.type("SHOULD-NOT-APPEAR")
        page.wait_for_timeout(200)
        self.assertEqual(page.evaluate("() => %s.value()" % _FOCUSED_WORK_NOTES), original_text)

    def test_private_notes_are_read_only_immediately_when_offline(self):
        """Scenario 8: same read-only-on-init guarantee for the Private Notes
        textarea -- offline from the start, never a moment of editability."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]
        selector = "#prks-private-notes-work-" + work_a
        status_selector = "#prks-private-notes-status-work-" + work_a

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)
        page.locator(selector).wait_for()
        original_value = page.locator(selector).input_value()

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        self.assertIn(work_a, page.evaluate("() => location.hash"))
        page.locator(selector).wait_for()

        self.assertTrue(page.evaluate("(sel) => document.querySelector(sel).readOnly", selector))
        page.locator(status_selector, has_text="Offline — notes are read-only").wait_for()

        page.locator(selector).click()
        page.keyboard.type("SHOULD-NOT-APPEAR")
        page.wait_for_timeout(200)
        self.assertEqual(page.locator(selector).input_value(), original_value)

    def test_cached_pdf_reopened_offline_has_no_annotation_tools(self):
        """Scenario 9: a previously-cached PDF reopened offline mounts the vendor
        viewer in its read-only 'preview' mode -- the markup toolbar
        (Highlight/Underline/Undo/Redo) is entirely absent from the DOM and no
        annotation-sync persistence worker is installed, never merely disabled
        client-side tooling that could race a real mutation through."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]
        pdf_path = "/api/pdfs/%s" % server.ids["pdf_name"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        _wait_pdf_whole_file_cached(page, pdf_path)
        # Sanity check: online, the markup toolbar is present.
        page.locator(
            '[data-prks-role="pdf-viewer"] .prks-pdf-toolbar [aria-label="Highlight"]'
        ).wait_for()

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        self.assertIn(work_a, page.evaluate("() => location.hash"))
        _wait_pdf_viewer(page)

        self.assertEqual(
            page.evaluate(
                """() => {
                    const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                    const pdf = ctx && ctx.getResource ? ctx.getResource('pdf') : null;
                    return pdf ? pdf.mode : null;
                }"""
            ),
            "preview",
        )
        self.assertEqual(
            page.locator(
                '[data-prks-role="pdf-viewer"] .prks-pdf-toolbar [aria-label="Highlight"]'
            ).count(),
            0,
        )
        self.assertEqual(
            page.locator(
                '[data-prks-role="pdf-viewer"] .prks-pdf-toolbar [aria-label="Underline"]'
            ).count(),
            0,
        )
        self.assertIsNone(
            page.evaluate(
                """() => {
                    const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                    const pdf = ctx && ctx.getResource ? ctx.getResource('pdf') : null;
                    return pdf ? (pdf.annotationPersistence || null) : null;
                }"""
            )
        )


if __name__ == "__main__":
    unittest.main()
