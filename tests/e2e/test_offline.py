"""Real Chromium + real PRKS server offline/PWA scenarios (Phase 1, read-only).

Collected only when PRKS_E2E=1 (see tests/e2e/run.py). These scenarios need a
real Service Worker and real IndexedDB, so -- unlike tests.e2e.test_app, which
deliberately blocks service workers for determinism -- every context here is
opened with service_workers="allow".
"""
from __future__ import annotations

import os
import time
import unittest
from urllib.parse import urlparse

from tests.e2e.fixtures import WORK_A_TITLE, WORK_B_TITLE, seed_library
from tests.e2e.harness import AppServer, PageCollector, open_app_page, require_chromium
from tests.e2e.test_app import (
    _FOCUSED_PDF,
    _FOCUSED_VIEWER,
    _FOCUSED_WORK_NOTES,
    _commit_pdf_highlight,
    _continue_held_routes,
    _open_details_drawer_if_tiled,
    _open_work_from_home,
    _viewer_annotation_count,
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


def _pdf_mode(page):
    return page.evaluate("() => { const pdf = %s; return pdf ? pdf.mode : null; }" % _FOCUSED_PDF)


def _pdf_has_pending_changes(page):
    return bool(
        page.evaluate(
            "() => { const pdf = %s; return !!(pdf && pdf.syncState && pdf.syncState.pendingChanges); }"
            % _FOCUSED_PDF
        )
    )


_PDF_WORK_CAPABLE_ONLINE_JS = (
    "() => { const pdf = %s; return !!(pdf && pdf.mode === 'work' && pdf.viewer); }" % _FOCUSED_PDF
)
_PDF_READ_ONLY_JS = "() => { const pdf = %s; return !!(pdf && pdf.mode !== 'work' && pdf.viewer); }" % _FOCUSED_PDF
_PDF_SYNC_SETTLED_JS = (
    "() => { const pdf = %s; return !!(pdf && pdf.syncState && !pdf.syncState.pendingChanges && !pdf.syncState.inFlight); }"
    % _FOCUSED_PDF
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

    def test_offline_research_notes_toolbar_and_pickers_are_inert(self):
        """Scenario 10: reopening a cached Work directly offline leaves Research
        Notes unmutable through every PRKS-owned edit path, not merely
        CodeMirror's own readOnly flag -- keyboard typing, mutating EasyMDE
        toolbar buttons (native `disabled`, so clicks never dispatch), and
        (as defense-in-depth beyond the disabled toolbar button) the
        Concept/Argument picker's onPick/onCreate guard all leave note text
        byte-for-byte unchanged and never issue a POST /api/arguments."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)
        page.wait_for_selector(".CodeMirror")
        original_text = page.evaluate("() => %s.value()" % _FOCUSED_WORK_NOTES)

        argument_posts = []
        page.on(
            "request",
            lambda req: argument_posts.append(req.method)
            if req.method == "POST" and "/api/arguments" in req.url
            else None,
        )

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        self.assertIn(work_a, page.evaluate("() => location.hash"))
        page.wait_for_selector(".CodeMirror")
        page.wait_for_function(
            """() => {
                const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                const notes = ctx && ctx.getResource ? ctx.getResource('workNotes') : null;
                const cm = notes && notes.editor && notes.editor.codemirror;
                return !!(cm && cm.getOption('readOnly'));
            }"""
        )

        # Keyboard typing still fails (same guarantee as scenario 7, re-verified
        # here as the baseline for the toolbar/picker assertions below).
        page.locator(".CodeMirror").click()
        page.keyboard.type("SHOULD-NOT-APPEAR")
        page.wait_for_timeout(150)
        self.assertEqual(page.evaluate("() => %s.value()" % _FOCUSED_WORK_NOTES), original_text)

        # Mutating toolbar buttons are natively disabled -- clicking a disabled
        # <button> never dispatches a click event at all, so this is a real
        # inertness check, not merely a CSS/visual one.
        for cls in ("bold", "italic", "heading", "quote", "unordered-list", "ordered-list", "link", "image"):
            self.assertTrue(
                page.evaluate(
                    "(c) => { const b = document.querySelector('.editor-toolbar button.' + c); return !!(b && b.disabled); }",
                    cls,
                ),
                "expected .%s toolbar button disabled while offline" % cls,
            )
        # Non-mutating actions remain enabled.
        for cls in ("preview", "side-by-side", "fullscreen"):
            self.assertFalse(
                page.evaluate(
                    "(c) => { const b = document.querySelector('.editor-toolbar button.' + c); return !!(b && b.disabled); }",
                    cls,
                ),
                "expected .%s toolbar button to remain enabled while offline" % cls,
            )

        page.evaluate("() => document.querySelector('.editor-toolbar button.bold').click()")
        page.wait_for_timeout(100)
        self.assertEqual(page.evaluate("() => %s.value()" % _FOCUSED_WORK_NOTES), original_text)

        self.assertTrue(
            page.evaluate(
                """() => { const b = document.querySelector('.editor-toolbar button.prks-insert-concept'); return !!(b && b.disabled); }"""
            )
        )
        page.evaluate("() => document.querySelector('.editor-toolbar button.prks-insert-concept').click()")
        page.wait_for_timeout(100)
        self.assertEqual(page.locator("#prks-research-picker").count(), 0)
        self.assertEqual(page.evaluate("() => %s.value()" % _FOCUSED_WORK_NOTES), original_text)

        self.assertTrue(
            page.evaluate(
                """() => { const b = document.querySelector('.editor-toolbar button.prks-insert-argument'); return !!(b && b.disabled); }"""
            )
        )

        # Defense-in-depth: even if an Argument picker is opened directly
        # (bypassing the disabled toolbar button), picking an existing
        # Argument/Stance is a guarded no-op while offline.
        page.evaluate(
            """(workId) => {
                const ctx = window.prksGetFocusedTabContext();
                ctx.setResource('argumentHintList', [
                    { id: 'e2e-fixture-argument', name: 'Existing Fixture Argument', kind: 'argument' },
                ]);
                const cm = ctx.getResource('workNotes').editor.codemirror;
                window.prksOpenArgumentPicker(cm, { id: workId });
            }""",
            work_a,
        )
        page.wait_for_selector("#prks-research-picker .prks-dialog")
        page.locator(
            "#prks-research-picker .prks-research-picker__item", has_text="Existing Fixture Argument"
        ).click()
        page.wait_for_function("() => !document.getElementById('prks-research-picker')")
        page.locator("#prks-modal-confirm-title", has_text="Offline").wait_for()
        page.locator("#prks-modal-confirm-ok").click()
        page.locator("#prks-modal-confirm:not(.hidden)").wait_for(state="detached", timeout=5000)
        self.assertEqual(page.evaluate("() => %s.value()" % _FOCUSED_WORK_NOTES), original_text)

        # Attempting to create a brand-new Argument likewise never reaches the
        # network and never mutates the note.
        page.evaluate(
            """(workId) => {
                const ctx = window.prksGetFocusedTabContext();
                const cm = ctx.getResource('workNotes').editor.codemirror;
                window.prksOpenArgumentPicker(cm, { id: workId });
            }""",
            work_a,
        )
        page.wait_for_selector("#prks-research-picker .prks-dialog")
        page.locator("#prks-research-picker input.prks-input").fill("Offline Created Argument")
        page.locator("#prks-research-picker [data-create='argument']").click()
        page.wait_for_function("() => !document.getElementById('prks-research-picker')")
        page.locator("#prks-modal-confirm-title", has_text="Offline").wait_for()
        page.locator("#prks-modal-confirm-ok").click()
        page.locator("#prks-modal-confirm:not(.hidden)").wait_for(state="detached", timeout=5000)
        self.assertEqual(page.evaluate("() => %s.value()" % _FOCUSED_WORK_NOTES), original_text)
        self.assertEqual(argument_posts, [])

    def test_pending_annotation_survives_disconnect_and_resumes_on_reconnect(self):
        """Scenario 11: an annotation created while ONLINE must not be lost
        because connectivity vanishes before persistence completes. The live
        Work viewer/document stay mounted, mutation tools become unavailable,
        no retry storm fires while offline, and reconnecting resumes exactly
        one flush that saves the held annotation."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)

        held = []
        ann_post_count = [0]

        def hold_annotations_post(route):
            req = route.request
            if req.method == "POST" and urlparse(req.url).path == "/api/works/%s/annotations" % work_a:
                ann_post_count[0] += 1
                held.append(route)
                return
            route.fallback()

        page.route("**/api/works/**", hold_annotations_post)
        try:
            _commit_pdf_highlight(page)
            deadline = time.time() + 12
            while time.time() < deadline and not held:
                page.wait_for_timeout(50)
            self.assertTrue(held, "annotation persistence POST did not start")
            self.assertEqual(len(held), 1)
            self.assertTrue(_pdf_has_pending_changes(page))
            annotation_count_before = _viewer_annotation_count(page)
            self.assertGreaterEqual(annotation_count_before, 1)

            context.set_offline(True)
            page.wait_for_function(
                "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
                timeout=20000,
            )

            # Same annotation remains visible; viewer/document not destroyed.
            self.assertEqual(_viewer_annotation_count(page), annotation_count_before)
            self.assertTrue(page.evaluate("() => !!%s" % _FOCUSED_VIEWER))

            # Mutation tools become unavailable (preview-equivalent toolbar).
            self.assertEqual(
                page.locator(
                    '[data-prks-role="pdf-viewer"] .prks-pdf-toolbar [aria-label="Highlight"]'
                ).count(),
                0,
            )

            # Pending state remains represented, and no repeated persistence
            # requests fire while offline (no retry storm).
            page.wait_for_timeout(1500)
            self.assertEqual(len(held), 1, "annotation persistence retried a request while offline")
            self.assertTrue(_pdf_has_pending_changes(page))

            # Reconnect: persistence resumes and the held annotation is saved.
            # resume() itself queues another pass through the same drain loop
            # if a mutation was requested while the original save was still
            # in flight, so releasing the request(s) currently in `held` is
            # not necessarily a one-shot affair -- keep draining whatever
            # newly appears in `held` until the sync settles.
            context.set_offline(False)
            page.wait_for_function(
                "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'online'",
                timeout=20000,
            )
            deadline = time.time() + 20
            settled = False
            while time.time() < deadline:
                if held:
                    _continue_held_routes(held)
                    held.clear()
                if page.evaluate(_PDF_SYNC_SETTLED_JS):
                    settled = True
                    break
                page.wait_for_timeout(100)
            self.assertTrue(settled, "annotation persistence never settled after reconnect")
            self.assertGreaterEqual(ann_post_count[0], 1)
            self.assertEqual(
                page.locator(
                    '[data-prks-role="pdf-viewer"] .prks-pdf-toolbar [aria-label="Highlight"]'
                ).count(),
                1,
            )
        finally:
            _continue_held_routes(held)
            try:
                page.unroute("**/api/works/**", hold_annotations_post)
            except Exception:
                pass

    def test_reconnect_probe_race_settles_pdf_to_online_work_capable_state(self):
        """Scenario 12 (browser-level approximation -- the exact millisecond
        async-mount race is covered deterministically at the unit level by
        tests/test_frontend_offline_pdf.py's
        test_mount_reconciles_stale_desired_mode_before_publishing and
        tests/browser/run_pdf_runtime_selftest.js): a PDF mounted offline in
        'preview' mode, with the reachability probe held in flight while
        network access is actually restored, must settle to a single
        Work-capable online viewer once that probe resolves -- never a
        leftover preview viewer, never a duplicate mount, exactly one
        annotation persistence worker."""
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
        self.assertEqual(_pdf_mode(page), "preview")
        self.assertEqual(
            page.locator('[data-prks-role="pdf-viewer"] .prks-pdf-toolbar [aria-label="Highlight"]').count(),
            0,
        )

        held_probe = []

        def hold_settings_get(route):
            req = route.request
            if req.method == "GET" and urlparse(req.url).path == "/api/settings":
                held_probe.append(route)
                return
            route.fallback()

        page.route("**/api/settings", hold_settings_get)
        try:
            # Network access is restored, but the reachability probe itself
            # is held -- the runtime must stay non-online (and the viewer
            # must stay in 'preview') until that probe actually resolves.
            context.set_offline(False)
            deadline = time.time() + 12
            while time.time() < deadline and not held_probe:
                page.wait_for_timeout(50)
            self.assertTrue(held_probe, "reachability probe did not start")

            page.wait_for_timeout(200)
            self.assertNotEqual(_connectivity_state(page), "online")
            self.assertEqual(_pdf_mode(page), "preview")

            for route in list(held_probe):
                try:
                    route.fulfill(status=200, content_type="application/json", body="{}")
                except Exception:
                    pass
            held_probe.clear()

            page.wait_for_function(
                "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'online'",
                timeout=20000,
            )
            page.wait_for_function(_PDF_WORK_CAPABLE_ONLINE_JS, timeout=20000)
            self.assertEqual(page.locator('[data-prks-role="pdf-viewer"]').count(), 1)
            self.assertEqual(
                page.locator(
                    '[data-prks-role="pdf-viewer"] .prks-pdf-toolbar [aria-label="Highlight"]'
                ).count(),
                1,
            )
            # prksEnsureAnnotationPersistence()'s setup is async (fire-and-forget
            # from the reconciler) -- give it a moment past the mode flip to land.
            page.wait_for_function(
                "() => { const pdf = %s; return !!(pdf && pdf.annotationPersistence); }" % _FOCUSED_PDF,
                timeout=20000,
            )
        finally:
            _continue_held_routes(held_probe)
            try:
                page.unroute("**/api/settings", hold_settings_get)
            except Exception:
                pass

    def test_rapid_connectivity_transitions_settle_to_latest_state(self):
        """Scenario 13: online -> offline -> online (rapid) must end in an
        online, mutation-capable viewer; offline -> online -> offline (rapid)
        must end read-only. No stale worker/viewer survives either
        sequence (exactly one PDF viewer container remains mounted)."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)

        # online -> offline -> online, rapid.
        context.set_offline(True)
        page.wait_for_timeout(300)
        context.set_offline(False)
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'online'",
            timeout=20000,
        )
        page.wait_for_function(_PDF_WORK_CAPABLE_ONLINE_JS, timeout=20000)
        self.assertEqual(
            page.locator(
                '[data-prks-role="pdf-viewer"] .prks-pdf-toolbar [aria-label="Highlight"]'
            ).count(),
            1,
        )
        self.assertEqual(page.locator('[data-prks-role="pdf-viewer"]').count(), 1)

        # offline -> online -> offline, rapid. A few hundred ms between each
        # toggle (rather than zero) avoids a pure CDP-level race where a
        # probe request dispatched in one transition races the *next*
        # transition's own network-condition change rather than the app's
        # own reconciliation logic; runProbe()'s in-flight guard plus the
        # 'online'/'offline' event handlers are what is actually under test.
        context.set_offline(True)
        page.wait_for_timeout(300)
        context.set_offline(False)
        page.wait_for_timeout(300)
        context.set_offline(True)
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) !== 'online'",
            timeout=20000,
        )
        page.wait_for_function(_PDF_READ_ONLY_JS, timeout=20000)
        self.assertEqual(
            page.locator(
                '[data-prks-role="pdf-viewer"] .prks-pdf-toolbar [aria-label="Highlight"]'
            ).count(),
            0,
        )
        self.assertEqual(page.locator('[data-prks-role="pdf-viewer"]').count(), 1)
        context.set_offline(False)


if __name__ == "__main__":
    unittest.main()
