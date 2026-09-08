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

from tests.e2e.fixtures import (
    CONCEPT_CHILD_ALIAS,
    CONCEPT_CHILD_DEFINITION,
    CONCEPT_CHILD_NAME,
    CONCEPT_PARENT_NAME,
    CONCEPT_UNVISITED_NAME,
    WORK_A_TITLE,
    WORK_B_TITLE,
    seed_concepts_library,
    seed_library,
)
from tests.e2e.harness import AppServer, PageCollector, open_app_page, require_chromium
from tests.e2e.test_app import (
    _FOCUSED_PDF,
    _FOCUSED_VIEWER,
    _FOCUSED_WORK_NOTES,
    _commit_pdf_highlight,
    _continue_held_routes,
    _open_details_drawer_if_tiled,
    _open_work_from_home,
    _pdf_selection_geometry,
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
    # Reading the row back through a separate connection does not guarantee the
    # writing transaction is finished with the page; tearing the page down (go
    # offline + reload) immediately after can still lose it. Let it settle.
    page.wait_for_timeout(250)


def _cached_entity(page, kind, entity_id):
    return page.evaluate(
        """([kind, id]) => {
            const store = window.createPrksOfflineStore();
            return store.getEntity(kind, id);
        }""",
        [kind, entity_id],
    )


def _wait_list_cached(page, list_key, timeout=15000):
    page.wait_for_function(
        """(key) => {
            if (typeof window.createPrksOfflineStore !== 'function') return false;
            return window.createPrksOfflineStore().getList(key).then(v => !!v);
        }""",
        arg=list_key,
        timeout=timeout,
    )
    page.wait_for_timeout(250)


def _cached_list(page, list_key):
    return page.evaluate(
        "(key) => window.createPrksOfflineStore().getList(key)",
        list_key,
    )


def _clear_cached_list(page, list_key):
    page.evaluate("(key) => window.createPrksOfflineStore().deleteList(key)", list_key)


def _content_text(page):
    """Visible text of the focused route's own container (ctx.root)."""
    return page.evaluate(
        """() => {
            const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
            return ctx && ctx.root ? ctx.root.innerText : document.body.innerText;
        }"""
    )


def _wait_entity_uncached(page, kind, entity_id, timeout=15000):
    page.wait_for_function(
        """([kind, id]) => window.createPrksOfflineStore().getEntity(kind, id).then(row => row === null)""",
        arg=[kind, entity_id],
        timeout=timeout,
    )


def _wait_list_uncached(page, list_key, timeout=15000):
    page.wait_for_function(
        "(key) => window.createPrksOfflineStore().getList(key).then(row => row === null)",
        arg=list_key,
        timeout=timeout,
    )


def _wait_focused_role(page, role, timeout=30000):
    page.wait_for_function(
        """(role) => {
            const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
            const root = ctx && ctx.root;
            return !!root && !!root.querySelector('[data-prks-role="' + role + '"]');
        }""",
        arg=role,
        timeout=timeout,
    )


def _wait_offline_banner(page, timeout=30000):
    _wait_focused_role(page, "offline-provenance-banner", timeout)


def _wait_offline_unavailable(page, timeout=30000):
    _wait_focused_role(page, "offline-unavailable", timeout)


def _wait_content_contains(page, text, timeout=30000):
    """Waits on the focused route's own rendered text.

    Deliberately not a visibility-based locator wait: these offline scenarios
    only care that the focused route rendered the expected content, and polling
    in-page avoids depending on which pane happens to be laid out.
    """
    page.wait_for_function(
        """(needle) => {
            const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
            const root = ctx && ctx.root;
            return !!root && root.innerText.indexOf(needle) !== -1;
        }""",
        arg=text,
        timeout=timeout,
    )


def _open_concept_index(page):
    page.evaluate("() => { void window.prksNavigate('#/concepts'); }")
    page.wait_for_function("() => location.hash === '#/concepts'")


def _open_concept(page, concept_id):
    page.evaluate("id => { void window.prksNavigate('#/concepts/' + id); }", concept_id)
    page.wait_for_function("id => decodeURIComponent(location.hash).indexOf(id) !== -1", arg=concept_id)


def _concept_domain_generation(page):
    return page.evaluate(
        "() => (typeof prksOfflineDomainGeneration === 'function' ? prksOfflineDomainGeneration('concepts') : null)"
    )


def _concept_domain_blocked(page):
    return page.evaluate(
        "() => (typeof prksOfflineIsDomainBlocked === 'function' ? prksOfflineIsDomainBlocked('concepts') : null)"
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

    def test_authoritative_metadata_refresh_replaces_offline_work_cache(self):
        """PATCH plus the existing complete Work GET replaces, never merges,
        the disposable offline snapshot."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]
        new_title = "Offline Coherent Metadata Title"

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)
        page.locator("#panel-content button", has_text="Edit metadata").click()
        page.locator("#meta-title").fill(new_title)
        page.locator("#inline-save-metadata-btn").click()
        page.locator("#panel-content .card-title", has_text=new_title).wait_for(timeout=15000)
        page.wait_for_function(
            """([id, title]) => window.createPrksOfflineStore().getEntity('work', id)
                .then(row => !!row && row.value && row.value.title === title)""",
            arg=[work_a, new_title],
            timeout=15000,
        )

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("title => document.body.innerText.indexOf(title) !== -1", arg=new_title)
        page.locator('[data-prks-role="offline-provenance-banner"]', has_text="Offline").wait_for()

    def test_metadata_success_with_failed_refresh_leaves_work_offline_unavailable(self):
        """PATCH success invalidates before its complete Work GET; a failed GET
        cannot leave the old title eligible for fallback."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)

        def fail_followup_detail(route):
            req = route.request
            if req.method == "GET" and urlparse(req.url).path == "/api/works/" + work_a:
                route.abort("failed")
                return
            route.fallback()

        page.route("**/api/works/*", fail_followup_detail)
        try:
            page.locator("#panel-content button", has_text="Edit metadata").click()
            page.locator("#meta-title").fill("New but no follow-up GET")
            page.locator("#inline-save-metadata-btn").click()
            page.wait_for_function(
                "id => window.createPrksOfflineStore().getEntity('work', id).then(row => row === null)",
                arg=work_a,
                timeout=15000,
            )
        finally:
            page.unroute("**/api/works/*", fail_followup_detail)

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.locator('[data-prks-role="offline-unavailable"]').wait_for(timeout=15000)

    def test_successful_research_notes_save_invalidates_work_cache(self):
        """A partial notes PATCH cannot make an older cached complete Work eligible."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)
        page.locator(".CodeMirror").click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text("offline coherence research note")
        page.locator('[data-prks-role="editor-status"]', has_text="All changes saved").wait_for(timeout=15000)
        page.wait_for_function(
            "id => window.createPrksOfflineStore().getEntity('work', id).then(row => row === null)",
            arg=work_a,
            timeout=15000,
        )

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.locator('[data-prks-role="offline-unavailable"]').wait_for(timeout=15000)

    def test_successful_private_notes_save_invalidates_work_cache(self):
        """Private Work note PATCH is partial too; cache must be absent until a fresh Work read."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]
        selector = "#prks-private-notes-work-" + work_a

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)
        page.locator(selector).fill("offline coherence private note")
        page.locator(selector).blur()
        page.locator("#prks-private-notes-status-work-" + work_a, has_text="Saved").wait_for(timeout=15000)
        page.wait_for_function(
            "id => window.createPrksOfflineStore().getEntity('work', id).then(row => row === null)",
            arg=work_a,
            timeout=15000,
        )

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.locator('[data-prks-role="offline-unavailable"]').wait_for(timeout=15000)

    def test_authoritative_tag_refresh_replaces_offline_work_cache(self):
        """Representative relationship mutation stores only its refreshed full Work."""
        server, page, _context, _collector = self._start()
        work_a = server.ids["work_a"]
        tag_name = "Offline Coherent Tag"

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)
        page.locator("#panel-content button", has_text="Manage tags").click()
        page.locator("#work-tag-search").fill(tag_name)
        page.locator("#work-tag-search-results .result-item--create", has_text=tag_name).click()
        page.locator("#work-tags-list .work-tag-chip", has_text=tag_name).wait_for(timeout=15000)
        page.wait_for_function(
            """([id, name]) => window.createPrksOfflineStore().getEntity('work', id)
                .then(row => !!row && Array.isArray(row.value.tags)
                    && row.value.tags.some(tag => tag && tag.name === name))""",
            arg=[work_a, tag_name],
            timeout=15000,
        )

    def test_successful_work_delete_evicts_offline_cache(self):
        """Acknowledged DELETE removes its Work snapshot before offline navigation can reuse it."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)
        _open_details_drawer_if_tiled(page)
        advanced = page.locator(".work-details-advanced")
        if advanced.get_attribute("open") is None:
            advanced.locator("summary").click()
        page.locator(".delete-work-btn").click()
        page.locator("#prks-modal-confirm:not(.hidden)", has_text="Delete file?").wait_for()
        page.locator("#prks-modal-confirm-ok").click()
        page.wait_for_function("() => location.hash === '#/folders'", timeout=15000)
        self.assertIsNone(_cached_entity(page, "work", work_a))

        context.set_offline(True)
        page.evaluate("id => { void window.prksNavigate('#/works/' + id); }", work_a)
        page.locator('[data-prks-role="offline-unavailable"]').wait_for(timeout=15000)

    def test_successful_folder_add_work_invalidates_offline_work_cache(self):
        """Folder endpoint attachment shares Work coherence helper behavior."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)
        page.evaluate(
            """async (workId) => {
                const folderId = await createFolder('Offline coherence folder add', '');
                return addWorkToFolder(folderId, workId);
            }""",
            arg=work_a,
        )
        page.wait_for_function(
            "id => window.createPrksOfflineStore().getEntity('work', id).then(row => row === null)",
            arg=work_a,
            timeout=15000,
        )

        context.set_offline(True)
        page.evaluate("id => { void window.prksNavigate('#/works/' + id); }", work_a)
        page.locator('[data-prks-role="offline-unavailable"]').wait_for(timeout=15000)

    def test_failed_folder_add_work_retains_offline_work_cache(self):
        """Non-success Folder attachment never advances Work coherence."""
        server, page, _context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)
        folder_id = page.evaluate("() => createFolder('Offline failed folder add', '')")

        def reject_folder_add(route):
            if route.request.method == "POST" and urlparse(route.request.url).path == "/api/folders/" + folder_id + "/works":
                route.fulfill(status=409, content_type="application/json", body='{"error":"test failure"}')
                return
            route.fallback()

        page.route("**/api/folders/*/works", reject_folder_add)
        try:
            failed = page.evaluate(
                """async ([folderId, workId]) => {
                    try { await addWorkToFolder(folderId, workId); return false; }
                    catch (_e) { return true; }
                }""",
                arg=[folder_id, work_a],
            )
            self.assertTrue(failed)
            self.assertIsNotNone(_cached_entity(page, "work", work_a))
        finally:
            page.unroute("**/api/folders/*/works", reject_folder_add)

    def test_failed_notes_save_retains_previous_work_cache(self):
        """A rejected notes PATCH leaves the last known-good snapshot available."""
        server, page, _context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)

        def reject_notes_patch(route):
            if route.request.method == "PATCH" and urlparse(route.request.url).path == "/api/works/" + work_a:
                route.fulfill(status=500, content_type="application/json", body='{"error":"test failure"}')
                return
            route.fallback()

        page.route("**/api/works/*", reject_notes_patch)
        try:
            page.locator(".CodeMirror").click()
            page.keyboard.press("Control+A")
            page.keyboard.insert_text("failed offline coherence note")
            page.locator('[data-prks-role="editor-status"]', has_text="Error saving changes").wait_for(timeout=15000)
            cached = _cached_entity(page, "work", work_a)
            self.assertIsNotNone(cached)
            self.assertEqual(cached["value"]["id"], work_a)
        finally:
            page.unroute("**/api/works/*", reject_notes_patch)

    def test_failed_work_delete_retains_offline_cache(self):
        """Unacknowledged DELETE must not discard a potentially valid snapshot."""
        server, page, _context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)

        def reject_delete(route):
            if route.request.method == "DELETE" and urlparse(route.request.url).path == "/api/works/" + work_a:
                route.fulfill(status=500, content_type="application/json", body='{"error":"test failure"}')
                return
            route.fallback()

        page.route("**/api/works/*", reject_delete)
        try:
            _open_details_drawer_if_tiled(page)
            advanced = page.locator(".work-details-advanced")
            if advanced.get_attribute("open") is None:
                advanced.locator("summary").click()
            page.locator(".delete-work-btn").click()
            page.locator("#prks-modal-confirm:not(.hidden)", has_text="Delete file?").wait_for()
            page.locator("#prks-modal-confirm-ok").click()
            page.locator("#prks-modal-confirm:not(.hidden)", has_text="Error deleting file!").wait_for()
            self.assertIsNotNone(_cached_entity(page, "work", work_a))
        finally:
            page.unroute("**/api/works/*", reject_delete)

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
        page.locator("#panel-content button", has_text="Edit metadata").click()
        page.locator("#meta-title").fill("IndexedDB unavailable still saves")
        page.locator("#inline-save-metadata-btn").click()
        page.locator("#panel-content .card-title", has_text="IndexedDB unavailable still saves").wait_for(timeout=15000)
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
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
            timeout=20000,
        )
        # Startup probe retries (~250ms + 750ms) hold probeInFlight, which
        # makes the browser 'online' handler a no-op. Let them exhaust while
        # the context is still offline. Ordinary Work GETs now report
        # reachability, so holding only /api/settings would let a Work GET
        # sneak the runtime online before the held probe is observed.
        page.wait_for_timeout(1500)

        held_probe = []

        def hold_reachability_gets(route):
            req = route.request
            path = urlparse(req.url).path
            if req.method == "GET" and path.startswith("/api/") and not path.startswith("/api/pdfs/"):
                held_probe.append(route)
                return
            route.fallback()

        page.route("**/api/**", hold_reachability_gets)
        try:
            # Network access is restored, but reachability confirmation
            # (probe and ordinary JSON GETs) is held -- the runtime must stay
            # non-online (and the viewer must stay in 'preview') until a held
            # request is actually resolved.
            context.set_offline(False)
            deadline = time.time() + 12
            while time.time() < deadline and not held_probe:
                page.wait_for_timeout(50)
            self.assertTrue(held_probe, "reachability probe did not start")

            page.wait_for_timeout(200)
            self.assertNotEqual(_connectivity_state(page), "online")
            self.assertEqual(_pdf_mode(page), "preview")

            settings_held = [
                route
                for route in held_probe
                if urlparse(route.request.url).path == "/api/settings"
            ]
            others = [
                route
                for route in held_probe
                if urlparse(route.request.url).path != "/api/settings"
            ]
            for route in settings_held:
                try:
                    route.fulfill(status=200, content_type="application/json", body="{}")
                except Exception:
                    pass
            _continue_held_routes(others)
            held_probe.clear()
            page.unroute("**/api/**", hold_reachability_gets)

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
                page.unroute("**/api/**", hold_reachability_gets)
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

    def test_ctrl_b_shortcut_does_not_alter_notes_while_offline(self):
        """Scenario 14 (AGENTS.md "hard CodeMirror offline mutation barrier"):
        a formatting keyboard shortcut (EasyMDE's default Ctrl/Cmd-B ->
        toggleBold) calls `cm.replaceSelection()` directly -- it never goes
        through the disabled toolbar button at all, so the disabled-button
        belt alone would not stop it. The `beforeChange` barrier must cancel
        it while offline regardless."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)
        page.wait_for_selector(".CodeMirror")
        original_text = page.evaluate("() => %s.value()" % _FOCUSED_WORK_NOTES)

        context.set_offline(True)
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) !== 'online'",
            timeout=20000,
        )
        page.wait_for_function(
            """() => {
                const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                const notes = ctx && ctx.getResource ? ctx.getResource('workNotes') : null;
                const cm = notes && notes.editor && notes.editor.codemirror;
                return !!(cm && cm.getOption('readOnly'));
            }"""
        )

        page.locator(".CodeMirror").click()
        page.evaluate(
            """() => {
                const ctx = window.prksGetFocusedTabContext();
                const cm = ctx.getResource('workNotes').editor.codemirror;
                const last = cm.lastLine();
                cm.setCursor({ line: last, ch: cm.getLine(last).length });
                cm.focus();
            }"""
        )
        page.keyboard.press("Control+b")
        page.wait_for_timeout(200)
        self.assertEqual(page.evaluate("() => %s.value()" % _FOCUSED_WORK_NOTES), original_text)

    def test_stale_autocomplete_picker_does_not_mutate_notes_offline(self):
        """Scenario 15 (AGENTS.md "guard every Research Notes autocomplete
        completion"): opening a wiki/concept/PDF-annotation autocomplete
        dropdown while online, then losing connectivity before picking a
        suggestion, must never let that click mutate the document --
        `prksWorkNotesMutationAllowed()` re-checks connectivity (and editor
        identity) at pick time, not merely at the moment the dropdown opened.
        One parameterized helper drives all three completion kinds."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        _wait_entity_cached(page, "work", work_a)
        page.wait_for_selector(".CodeMirror")

        # A real PDF annotation must exist server-side for the [[pdf: ...
        # completion to have a real candidate; commit one online first and
        # let its persistence settle before touching Research Notes.
        _commit_pdf_highlight(page)
        page.wait_for_function(_PDF_SYNC_SETTLED_JS, timeout=20000)
        # A committed highlight is immediately followed by a programmatic
        # `selectAnnotation()` (selection-menu.tsx's apply()), which can fire
        # its own slightly-delayed annotation event and a second, unrelated
        # flush pass. Let that fully settle before attaching the mutation
        # listener below, so only *new* activity caused by the offline
        # completion-pick attempts is ever counted.
        page.wait_for_timeout(800)
        page.wait_for_function(_PDF_SYNC_SETTLED_JS, timeout=20000)

        annotation_post_count = [0]

        def on_request(req):
            if req.method == "POST" and urlparse(req.url).path == "/api/works/%s/annotations" % work_a:
                annotation_post_count[0] += 1

        page.on("request", on_request)

        def cm_set_cursor_to_end():
            page.evaluate(
                """() => {
                    const ctx = window.prksGetFocusedTabContext();
                    const cm = ctx.getResource('workNotes').editor.codemirror;
                    const last = cm.lastLine();
                    cm.setCursor({ line: last, ch: cm.getLine(last).length });
                    cm.focus();
                }"""
            )

        def notes_text():
            return page.evaluate("() => %s.value()" % _FOCUSED_WORK_NOTES)

        def close_any_open_hints():
            page.evaluate(
                """() => {
                    const ctx = window.prksGetFocusedTabContext();
                    const cm = ctx.getResource('workNotes').editor.codemirror;
                    if (cm.state && cm.state.completionActive) cm.state.completionActive.close();
                }"""
            )

        for label, trigger_text in (
            ("wiki", "[[" + WORK_B_TITLE[:11]),
            ("concept", "[[concept:E2E Fix"),
            ("pdf-annotation", "[[pdf:"),
        ):
            self.assertEqual(_connectivity_state(page), "online", "expected online before %s trigger" % label)
            page.locator(".CodeMirror").click()
            cm_set_cursor_to_end()
            before_trigger = notes_text()
            page.keyboard.press("Enter")
            page.keyboard.type(trigger_text)
            try:
                page.wait_for_selector(".CodeMirror-hints .CodeMirror-hint", timeout=8000)
            except Exception as exc:
                raise AssertionError(
                    "%s autocomplete dropdown never appeared after typing %r" % (label, trigger_text)
                ) from exc
            text_with_dropdown_open = notes_text()

            context.set_offline(True)
            page.wait_for_function(
                "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) !== 'online'",
                timeout=20000,
            )

            hint_item = page.locator(".CodeMirror-hints .CodeMirror-hint").first
            self.assertGreater(
                hint_item.count(),
                0,
                "%s autocomplete dropdown closed before an offline pick could be attempted" % label,
            )
            hint_item.click()
            # The guarded pick calls prksOfflineGuardMutation(), which raises
            # the shared "Offline" confirm alert -- dismiss it before moving on.
            page.locator("#prks-modal-confirm-title", has_text="Offline").wait_for(timeout=5000)
            page.locator("#prks-modal-confirm-ok").click()
            page.locator("#prks-modal-confirm:not(.hidden)").wait_for(state="detached", timeout=5000)

            self.assertEqual(
                notes_text(),
                text_with_dropdown_open,
                "%s completion pick mutated Research Notes text while offline" % label,
            )

            close_any_open_hints()
            context.set_offline(False)
            page.wait_for_function(
                "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'online'",
                timeout=20000,
            )
            # Undo the harmless trigger-text typing itself (never a picked
            # completion) so the next iteration starts from clean note text.
            page.evaluate(
                "(text) => %s.value(text)" % _FOCUSED_WORK_NOTES,
                before_trigger,
            )
            page.wait_for_timeout(50)

        # The one real PDF annotation POST already happened (and settled)
        # before this listener was attached -- none of the offline
        # completion-pick attempts below may cause another.
        self.assertEqual(annotation_post_count[0], 0)

    def test_active_markup_tool_cleared_on_disconnect(self):
        """Scenario 16 (AGENTS.md "clear an already-active PDF markup tool
        when mutations are disabled"): the Highlight *tool* (toolbar
        activation, not a committed annotation) must be cleared the instant
        connectivity drops -- never merely hidden a frame later -- and a
        subsequent drag/select on the PDF must create nothing and issue no
        mutation request."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        annotation_count_before = _viewer_annotation_count(page)

        mutation_requests = []
        page.on(
            "request",
            lambda req: mutation_requests.append(req.method)
            if req.method in ("POST", "PUT", "PATCH", "DELETE") and "/api/works/" in req.url
            else None,
        )

        highlight_btn = page.locator('[data-prks-role="pdf-viewer"] .prks-pdf-toolbar [aria-label="Highlight"]')
        highlight_btn.wait_for()
        highlight_btn.click()
        page.wait_for_function(
            """() => {
                const b = document.querySelector('[data-prks-role="pdf-viewer"] .prks-pdf-toolbar [aria-label="Highlight"]');
                return b && b.getAttribute('aria-pressed') === 'true';
            }"""
        )

        context.set_offline(True)
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) !== 'online'",
            timeout=20000,
        )
        self.assertEqual(_pdf_mode(page), "preview")
        page.wait_for_function(
            """() => !document.querySelector(
                '[data-prks-role="pdf-viewer"] .prks-pdf-toolbar [aria-label="Highlight"]'
            )"""
        )
        # Tool cleared -> Pointer (no active markup tool, no panning) shows pressed.
        page.wait_for_function(
            """() => {
                const b = document.querySelector('[data-prks-role="pdf-viewer"] .prks-pdf-toolbar [aria-label="Pointer"]');
                return b && b.getAttribute('aria-pressed') === 'true';
            }"""
        )

        geo = _pdf_selection_geometry(page)
        page.mouse.move(geo["sx"], geo["sy"])
        page.mouse.down()
        page.mouse.move(geo["ex"], geo["ey"], steps=12)
        page.mouse.up()
        page.wait_for_timeout(400)

        self.assertEqual(page.locator(".prks-pdf-selection-popup").count(), 0)
        self.assertEqual(_viewer_annotation_count(page), annotation_count_before)
        self.assertFalse(_pdf_has_pending_changes(page))
        self.assertEqual(mutation_requests, [])

        context.set_offline(False)
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'online'",
            timeout=20000,
        )
        page.wait_for_function(_PDF_WORK_CAPABLE_ONLINE_JS, timeout=20000)
        page.locator(
            '[data-prks-role="pdf-viewer"] .prks-pdf-toolbar [aria-label="Highlight"]'
        ).wait_for()

    def test_persistence_setup_abandons_on_disconnect_before_worker_install(self):
        """Scenario 17 (AGENTS.md "an async annotation-persistence setup...
        must re-check current viewer identity, runtime.mode, and
        connectivity... after every await boundary"): holding the initial
        `GET /api/works/<id>/annotations` past an offline transition must
        make setup abandon rather than install an active worker, and must
        reset `_persistenceSetupStarted` so a later online reconcile can
        retry exactly once."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]

        held = []

        def hold_annotations_get(route):
            req = route.request
            if req.method == "GET" and urlparse(req.url).path == "/api/works/%s/annotations" % work_a:
                held.append(route)
                return
            route.fallback()

        page.route("**/api/works/**", hold_annotations_get)
        try:
            _wait_sw_active(page)
            _open_work_from_home(page, WORK_A_TITLE)
            _wait_pdf_viewer(page)

            deadline = time.time() + 12
            while time.time() < deadline and not held:
                page.wait_for_timeout(50)
            self.assertTrue(held, "initial GET /annotations did not start")
            self.assertEqual(len(held), 1)
            self.assertEqual(_pdf_mode(page), "work")

            context.set_offline(True)
            page.wait_for_function(
                "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) !== 'online'",
                timeout=20000,
            )
            self.assertEqual(_pdf_mode(page), "preview")

            # Release the held GET only now, after PRKS has already
            # transitioned offline -- setup must abandon, not install.
            _continue_held_routes(held)
            held.clear()
            page.unroute("**/api/works/**", hold_annotations_get)
            page.wait_for_timeout(600)

            self.assertIsNone(
                page.evaluate(
                    "() => { const pdf = %s; return pdf ? pdf.annotationPersistence : null; }" % _FOCUSED_PDF
                )
            )
            self.assertFalse(
                page.evaluate(
                    "() => { const pdf = %s; return !!(pdf && pdf._persistenceSetupStarted); }" % _FOCUSED_PDF
                )
            )
            self.assertEqual(_pdf_mode(page), "preview")

            context.set_offline(False)
            page.wait_for_function(
                "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'online'",
                timeout=20000,
            )
            page.wait_for_function(_PDF_WORK_CAPABLE_ONLINE_JS, timeout=20000)
            page.wait_for_function(
                "() => { const pdf = %s; return !!(pdf && pdf.annotationPersistence); }" % _FOCUSED_PDF,
                timeout=20000,
            )
            self.assertTrue(
                page.evaluate(
                    "() => { const pdf = %s; return !!(pdf && pdf._persistenceSetupStarted); }" % _FOCUSED_PDF
                )
            )
        finally:
            _continue_held_routes(held)
            try:
                page.unroute("**/api/works/**", hold_annotations_get)
            except Exception:
                pass


    def test_transport_failure_while_browser_stays_online_goes_offline_and_recovers(self):
        """Server-unreachable while navigator.onLine remains true: an ordinary
        prksRequest() transport failure must flip the runtime offline (notes
        read-only, PDF preview) without context.set_offline, and a later real
        HTTP response must restore online mutation capability."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        _wait_entity_cached(page, "work", work_a)
        page.wait_for_selector(".CodeMirror")
        private_selector = "#prks-private-notes-work-" + work_a
        page.locator(private_selector).wait_for()

        self.assertTrue(page.evaluate("() => navigator.onLine"))
        self.assertEqual(_connectivity_state(page), "online")
        self.assertEqual(_pdf_mode(page), "work")

        hits = []

        def abort_api(route):
            hits.append(route.request.url)
            route.abort("connectionrefused")

        page.route("**/api/**", abort_api)
        try:
            outcome = page.evaluate(
                """async (id) => {
                    try {
                        const res = await window.prksRequest('/api/works/' + id);
                        return { ok: true, status: res.status };
                    } catch (e) {
                        return {
                            ok: false,
                            name: e && e.name ? String(e.name) : '',
                            message: e && e.message ? String(e.message) : '',
                        };
                    }
                }""",
                work_a,
            )
            self.assertTrue(hits, "Playwright did not intercept the Work request")
            self.assertFalse(outcome.get("ok"), "Work request should fail at transport: %s" % outcome)
            self.assertNotEqual(outcome.get("name"), "AbortError")
            page.wait_for_function(
                "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
                timeout=20000,
            )
            self.assertTrue(page.evaluate("() => navigator.onLine"))
            page.wait_for_function("() => !document.getElementById('prks-connectivity-indicator').hidden")
            self.assertIn("Offline", page.locator("#prks-connectivity-indicator").inner_text())
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
            self.assertTrue(page.evaluate("(sel) => document.querySelector(sel).readOnly", private_selector))
            page.wait_for_function(_PDF_READ_ONLY_JS, timeout=20000)
            self.assertEqual(_pdf_mode(page), "preview")
        finally:
            try:
                page.unroute("**/api/**", abort_api)
            except Exception:
                pass

        self.assertTrue(page.evaluate("() => navigator.onLine"))
        page.evaluate(
            """async (id) => {
                await window.prksRequest('/api/works/' + id);
            }""",
            work_a,
        )
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'online'",
            timeout=20000,
        )
        page.wait_for_function("() => document.getElementById('prks-connectivity-indicator').hidden")
        page.wait_for_function(
            """() => {
                const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                const notes = ctx && ctx.getResource ? ctx.getResource('workNotes') : null;
                const cm = notes && notes.editor && notes.editor.codemirror;
                return !!(cm && !cm.getOption('readOnly'));
            }""",
            timeout=20000,
        )
        self.assertFalse(page.evaluate("(sel) => document.querySelector(sel).readOnly", private_selector))
        page.wait_for_function(_PDF_WORK_CAPABLE_ONLINE_JS, timeout=20000)
        self.assertEqual(_pdf_mode(page), "work")

    def test_http_500_does_not_mark_runtime_offline(self):
        """A reachable PRKS process returning HTTP 500 is application health,
        not transport unreachability — the runtime must stay online."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        self.assertTrue(page.evaluate("() => navigator.onLine"))
        self.assertEqual(_connectivity_state(page), "online")

        def fulfill_500(route):
            req = route.request
            if req.method == "GET" and urlparse(req.url).path == "/api/works/%s" % work_a:
                route.fulfill(status=500, content_type="application/json", body='{"error":"boom"}')
                return
            route.fallback()

        page.route("**/api/works/**", fulfill_500)
        try:
            status = page.evaluate(
                """async (id) => {
                    const res = await window.prksRequest('/api/works/' + id);
                    return res.status;
                }""",
                work_a,
            )
            self.assertEqual(status, 500)
            self.assertTrue(page.evaluate("() => navigator.onLine"))
            self.assertEqual(_connectivity_state(page), "online")
            self.assertTrue(page.locator("#prks-connectivity-indicator[hidden]").count() >= 1)
            self.assertEqual(_pdf_mode(page), "work")
            self.assertFalse(
                page.evaluate(
                    """() => {
                        const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                        const notes = ctx && ctx.getResource ? ctx.getResource('workNotes') : null;
                        const cm = notes && notes.editor && notes.editor.codemirror;
                        return !!(cm && cm.getOption('readOnly'));
                    }"""
                )
            )
        finally:
            try:
                page.unroute("**/api/works/**", fulfill_500)
            except Exception:
                pass



class OfflineConceptTests(unittest.TestCase):
    """Phase 1 read-only Concept routes: #/concepts and #/concepts/:conceptId."""

    def _start(self):
        server = AppServer(seed_fn=seed_concepts_library)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin, service_workers="allow")
        self.addCleanup(context.close)
        return server, page, context, collector

    def _concept_api_calls(self, page):
        """Records every Concept API request issued from here on."""
        seen = []

        def record(route):
            seen.append((route.request.method, urlparse(route.request.url).path))
            route.fallback()

        page.route("**/api/concepts**", record)
        self.addCleanup(lambda: _safe_unroute(page, "**/api/concepts**", record))
        return seen

    # ---- cached index -------------------------------------------------------

    def test_cached_concept_index_renders_and_searches_offline(self):
        """Cached Concept index renders offline with provenance, searches locally
        with zero API traffic, and offers no enabled New Concept escape route."""
        server, page, context, _collector = self._start()

        _wait_sw_active(page)
        _open_concept_index(page)
        page.locator(".prks-research-row__title", has_text=CONCEPT_PARENT_NAME).wait_for()
        _wait_list_cached(page, "concepts:index")

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_content_contains(page, CONCEPT_PARENT_NAME)
        _wait_offline_banner(page)
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
            timeout=20000,
        )

        seen = self._concept_api_calls(page)
        page.locator("#prks-concept-search").fill(CONCEPT_CHILD_NAME)
        page.wait_for_function(
            "name => { const rows = document.querySelectorAll('.prks-research-row__title');"
            " return rows.length === 1 && rows[0].textContent.indexOf(name) !== -1; }",
            arg=CONCEPT_CHILD_NAME,
        )
        # Aliases and parent names are part of the same local filter.
        page.locator("#prks-concept-search").fill(CONCEPT_CHILD_ALIAS)
        page.wait_for_function(
            "name => { const rows = document.querySelectorAll('.prks-research-row__title');"
            " return rows.length === 1 && rows[0].textContent.indexOf(name) !== -1; }",
            arg=CONCEPT_CHILD_NAME,
        )
        page.locator("#prks-concept-search").fill("no such concept anywhere")
        page.locator(".prks-research-index__empty", has_text="match").wait_for()
        self.assertEqual(seen, [], "offline Concept search must issue zero API requests")

        new_btn = page.locator("#prks-concept-new")
        self.assertTrue(new_btn.is_disabled())
        self.assertEqual(new_btn.get_attribute("aria-disabled"), "true")

    def test_uncached_concept_index_offline_is_explicitly_unavailable(self):
        """No cached index is "not cached", never "No Concepts yet."."""
        server, page, context, _collector = self._start()

        _wait_sw_active(page)
        _open_concept_index(page)
        page.locator(".prks-research-row__title", has_text=CONCEPT_PARENT_NAME).wait_for()
        _wait_list_cached(page, "concepts:index")
        # Leave the route first, so no in-flight index render can re-cache the
        # list between the clear and the offline navigation.
        page.evaluate("() => { void window.prksNavigate('#/folders'); }")
        page.wait_for_function("() => location.hash === '#/folders'")
        _clear_cached_list(page, "concepts:index")
        _wait_list_uncached(page, "concepts:index")

        context.set_offline(True)
        _open_concept_index(page)
        _wait_offline_unavailable(page)
        body = _content_text(page)
        self.assertIn("not available offline", body)
        self.assertNotIn("No Concepts yet.", body)
        self.assertEqual(page.locator("#prks-concept-new").count(), 0)

    # ---- cached detail ------------------------------------------------------

    def test_cached_concept_detail_renders_offline(self):
        """A Concept opened online renders its full cached detail offline."""
        server, page, context, _collector = self._start()
        child = server.ids["concept_child"]

        _wait_sw_active(page)
        _open_concept(page, child)
        _wait_content_contains(page, CONCEPT_CHILD_NAME)
        _wait_entity_cached(page, "concept", child)

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_content_contains(page, CONCEPT_CHILD_NAME)
        _wait_offline_banner(page)
        body = _content_text(page)
        self.assertIn(CONCEPT_CHILD_DEFINITION, body)
        self.assertIn(CONCEPT_CHILD_ALIAS, body)
        self.assertIn(CONCEPT_PARENT_NAME, body)
        self.assertIn(WORK_A_TITLE, body)

    def test_cached_index_does_not_prefetch_every_concept_detail(self):
        """Opening the index caches the list only; an unopened Concept stays
        unavailable offline rather than mirroring the whole research network."""
        server, page, context, _collector = self._start()
        child = server.ids["concept_child"]
        unvisited = server.ids["concept_unvisited"]

        _wait_sw_active(page)
        _open_concept_index(page)
        _wait_list_cached(page, "concepts:index")
        _open_concept(page, child)
        _wait_entity_cached(page, "concept", child)
        _open_concept_index(page)
        page.locator(".prks-research-row__title", has_text=CONCEPT_UNVISITED_NAME).wait_for()
        self.assertIsNone(_cached_entity(page, "concept", unvisited))

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        _wait_content_contains(page, CONCEPT_UNVISITED_NAME)
        page.locator('.prks-research-row[href$="%s"]' % unvisited).click()
        _wait_offline_unavailable(page)
        body = _content_text(page)
        self.assertIn("not available offline", body)
        self.assertNotIn("Concept not found", body)
        # The Concept that WAS opened online still works from cache. Reload
        # first so this starts from a clean offline boot rather than inheriting
        # the previous route's in-flight failed request state.
        _wait_entity_cached(page, "concept", child)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _open_concept(page, child)
        _wait_content_contains(page, CONCEPT_CHILD_NAME)

    def test_cached_concept_relationships_navigate_offline(self):
        """Parent/subconcept and Concept -> Work mention links are ordinary PRKS
        navigation; there is no offline-specific router."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]
        parent = server.ids["concept_parent"]
        child = server.ids["concept_child"]

        _wait_sw_active(page)
        _open_concept(page, parent)
        _wait_entity_cached(page, "concept", parent)
        _open_concept(page, child)
        _wait_entity_cached(page, "concept", child)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)

        context.set_offline(True)
        _open_concept(page, child)
        _wait_content_contains(page, CONCEPT_CHILD_NAME)
        _wait_offline_banner(page)
        page.locator('.prks-research-row[href$="%s"]' % parent).click()
        page.wait_for_function("id => decodeURIComponent(location.hash).indexOf(id) !== -1", arg=parent)
        _wait_content_contains(page, CONCEPT_PARENT_NAME)
        _wait_offline_banner(page)
        # ... and back down to the subconcept.
        page.locator('.prks-research-row[href$="%s"]' % child).click()
        page.wait_for_function("id => decodeURIComponent(location.hash).indexOf(id) !== -1", arg=child)
        _wait_content_contains(page, CONCEPT_CHILD_NAME)
        # ... and out to the cached Work through the existing Work offline route.
        page.locator(".research-entity__mention-title", has_text=WORK_A_TITLE).click()
        page.wait_for_function("id => decodeURIComponent(location.hash).indexOf(id) !== -1", arg=work_a)
        page.wait_for_function("title => document.body.innerText.indexOf(title) !== -1", arg=WORK_A_TITLE)

    # ---- mutation blocking --------------------------------------------------

    def test_offline_concept_detail_cannot_mutate(self):
        """Every Concept mutation surface is inert offline and issues no request."""
        server, page, context, _collector = self._start()
        child = server.ids["concept_child"]

        _wait_sw_active(page)
        _open_concept(page, child)
        _wait_content_contains(page, CONCEPT_CHILD_NAME)
        _wait_entity_cached(page, "concept", child)

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        page.wait_for_function("id => decodeURIComponent(location.hash).indexOf(id) !== -1", arg=child)
        _wait_content_contains(page, CONCEPT_CHILD_NAME)
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
            timeout=20000,
        )

        mutations = []

        def record_mutation(route):
            if route.request.method in ("POST", "PATCH", "PUT", "DELETE"):
                mutations.append((route.request.method, urlparse(route.request.url).path))
            route.fallback()

        page.route("**/api/concepts**", record_mutation)
        try:
            for selector in (
                "#prks-concept-rename",
                "#prks-concept-delete",
                "#prks-concept-edit-def",
                "#prks-concept-edit-aliases",
                "#prks-concept-edit-parents",
            ):
                btn = page.locator(selector)
                self.assertTrue(btn.is_disabled(), "%s must be disabled offline" % selector)
                self.assertEqual(btn.get_attribute("aria-disabled"), "true", selector)
                btn.click(force=True)
            # The New Concept flow is also reachable from Work Research Notes, so
            # drive it directly: it must refuse before opening its dialog rather
            # than reaching the network.
            page.evaluate("""async () => {
                    try { await window.prksCreateConceptFlow('Offline concept'); } catch (_e) {}
                }""")
            page.wait_for_timeout(300)
            self.assertEqual(mutations, [])
            # The guard's own requires-a-connection notice is the only dialog: no
            # editor was opened that could never save.
            page.locator("#prks-modal-confirm:not(.hidden)", has_text="requires a connection").wait_for()
            self.assertEqual(page.locator("#prks-modal-confirm .prks-modal-prompt__input").count(), 0)
        finally:
            _safe_unroute(page, "**/api/concepts**", record_mutation)

        self.assertTrue(page.locator("#prks-concept-view-graph").is_disabled())

    def test_disconnect_while_concept_prompt_open_blocks_the_save(self):
        """Connectivity can change while a dialog is open: the re-check before the
        canonical request means clicking Save issues no PATCH."""
        server, page, context, _collector = self._start()
        child = server.ids["concept_child"]

        _wait_sw_active(page)
        _open_concept(page, child)
        _wait_content_contains(page, CONCEPT_CHILD_NAME)
        _wait_entity_cached(page, "concept", child)
        cached_before = _cached_entity(page, "concept", child)
        self.assertIsNotNone(cached_before)

        page.locator("#prks-concept-rename").click()
        prompt_input = page.locator("#prks-modal-confirm .prks-modal-prompt__input")
        prompt_input.wait_for()
        prompt_input.fill("Renamed while disconnected")

        mutations = []

        def block_api(route):
            if route.request.method in ("POST", "PATCH", "PUT", "DELETE"):
                mutations.append((route.request.method, urlparse(route.request.url).path))
            route.abort("connectionrefused")

        page.route("**/api/**", block_api)
        try:
            # Make PRKS unreachable while the dialog is open.
            page.evaluate(
                """async () => {
                    try { await window.prksRequest('/api/settings'); } catch (_e) {}
                }"""
            )
            page.wait_for_function(
                "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
                timeout=20000,
            )
            page.locator("#prks-modal-confirm-ok").click()
            page.wait_for_timeout(500)
            self.assertEqual(mutations, [], "no Concept mutation may be attempted after disconnect")
        finally:
            _safe_unroute(page, "**/api/**", block_api)

        self.assertEqual(_cached_entity(page, "concept", child)["value"]["name"], CONCEPT_CHILD_NAME)

    def test_live_concept_page_becomes_read_only_on_disconnect_and_restores(self):
        """A Concept page mounted online becomes read-only in place when PRKS
        stops answering, without a reload, and restores on reconnect."""
        server, page, _context, _collector = self._start()
        child = server.ids["concept_child"]

        _wait_sw_active(page)
        _open_concept(page, child)
        _wait_content_contains(page, CONCEPT_CHILD_NAME)
        self.assertFalse(page.locator("#prks-concept-rename").is_disabled())
        self.assertFalse(page.locator("#prks-concept-view-graph").is_disabled())

        def abort_api(route):
            route.abort("connectionrefused")

        page.route("**/api/**", abort_api)
        try:
            page.evaluate(
                """async () => {
                    try { await window.prksRequest('/api/settings'); } catch (_e) {}
                }"""
            )
            page.wait_for_function(
                "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
                timeout=20000,
            )
            self.assertTrue(page.evaluate("() => navigator.onLine"))
            page.wait_for_function("() => !!document.querySelector('#prks-concept-rename[disabled]')", timeout=20000)
            for selector in (
                "#prks-concept-delete",
                "#prks-concept-edit-def",
                "#prks-concept-edit-aliases",
                "#prks-concept-edit-parents",
                "#prks-concept-view-graph",
            ):
                self.assertTrue(page.locator(selector).is_disabled(), selector)
            # Read/navigation links stay usable.
            self.assertEqual(page.locator('.prks-research-row[href$="%s"]' % server.ids["concept_parent"]).count(), 1)
        finally:
            _safe_unroute(page, "**/api/**", abort_api)

        page.evaluate("""async () => { await window.prksRequest('/api/settings'); }""")
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'online'",
            timeout=20000,
        )
        page.wait_for_function("() => !document.querySelector('#prks-concept-rename[disabled]')", timeout=20000)
        self.assertFalse(page.locator("#prks-concept-view-graph").is_disabled())

    # ---- HTTP errors are never disguised as offline -------------------------

    def test_concept_detail_http_errors_keep_their_normal_meaning(self):
        """404 is not-found, 500 is a route error, and only a transport failure
        consults the cache."""
        server, page, context, _collector = self._start()
        child = server.ids["concept_child"]

        _wait_sw_active(page)
        _open_concept(page, child)
        _wait_entity_cached(page, "concept", child)

        _open_concept(page, "C-DOES-NOT-EXIST")
        _wait_content_contains(page, "Concept not found")
        self.assertEqual(_connectivity_state(page), "online")

        failing = {"on": True}

        def fulfill_500(route):
            if (
                failing["on"]
                and route.request.method == "GET"
                and urlparse(route.request.url).path.startswith("/api/concepts/")
            ):
                route.fulfill(status=500, content_type="application/json", body='{"error":"boom"}')
                return
            route.fallback()

        page.route("**/api/concepts/**", fulfill_500)
        self.addCleanup(lambda: _safe_unroute(page, "**/api/concepts/**", fulfill_500))
        _open_concept(page, child)
        page.wait_for_function(
            "() => document.querySelector('#prks-route-retry') !== null"
            " || document.body.innerText.indexOf('Could not load') !== -1",
            timeout=15000,
        )
        body = _content_text(page)
        self.assertNotIn("Concept not found", body)
        self.assertNotIn(CONCEPT_CHILD_DEFINITION, body)
        self.assertEqual(_connectivity_state(page), "online")
        self.assertTrue(page.locator("#prks-connectivity-indicator[hidden]").count() >= 1)
        # Stop intercepting entirely while still reachable: leaving a route
        # handler installed once the context is offline makes its pass-through
        # unreliable, and this phase must exercise a real transport failure.
        failing["on"] = False
        _safe_unroute(page, "**/api/concepts/**", fulfill_500)
        # Leave the failed route too, so the navigation below is a real one
        # rather than a same-hash no-op.
        _open_concept_index(page)
        _wait_content_contains(page, CONCEPT_CHILD_NAME)

        context.set_offline(True)
        _open_concept(page, child)
        _wait_offline_banner(page)
        _wait_content_contains(page, CONCEPT_CHILD_DEFINITION)
        _open_concept(page, server.ids["concept_unvisited"])
        _wait_offline_unavailable(page)

    # ---- domain coherence ---------------------------------------------------

    def test_concept_mutation_invalidates_the_whole_concept_domain(self):
        """Renaming one Concept conservatively stales every cached Concept, since
        siblings may display its old name as a parent/subconcept."""
        server, page, context, _collector = self._start()
        parent = server.ids["concept_parent"]
        child = server.ids["concept_child"]

        _wait_sw_active(page)
        _open_concept_index(page)
        _wait_list_cached(page, "concepts:index")
        _open_concept(page, parent)
        _wait_entity_cached(page, "concept", parent)
        _open_concept(page, child)
        _wait_entity_cached(page, "concept", child)
        generation_before = _concept_domain_generation(page)

        page.evaluate(
            "id => window.updateConcept(id, { description: 'Domain coherence rename check.' })",
            parent,
        )
        self.assertGreater(_concept_domain_generation(page), generation_before)
        _wait_entity_uncached(page, "concept", child)
        _wait_list_uncached(page, "concepts:index")

        context.set_offline(True)
        _open_concept(page, child)
        _wait_offline_unavailable(page)
        _open_concept_index(page)
        _wait_offline_unavailable(page)

    def test_failed_concept_mutation_retains_the_concept_cache(self):
        """A rejected Concept PATCH never advances Concept-domain coherence."""
        server, page, context, _collector = self._start()
        child = server.ids["concept_child"]

        _wait_sw_active(page)
        _open_concept(page, child)
        _wait_entity_cached(page, "concept", child)
        generation_before = _concept_domain_generation(page)

        def reject_patch(route):
            if route.request.method == "PATCH":
                route.fulfill(status=500, content_type="application/json", body='{"error":"nope"}')
                return
            route.fallback()

        page.route("**/api/concepts/**", reject_patch)
        try:
            page.evaluate(
                """async (id) => {
                    try { await window.updateConcept(id, { description: 'never applied' }); } catch (_e) {}
                }""",
                child,
            )
            page.wait_for_timeout(300)
            self.assertEqual(_concept_domain_generation(page), generation_before)
            self.assertIsNotNone(_cached_entity(page, "concept", child))
        finally:
            _safe_unroute(page, "**/api/concepts/**", reject_patch)

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        _wait_content_contains(page, CONCEPT_CHILD_NAME)
        _wait_offline_banner(page)

    def test_stale_pre_mutation_concept_read_cannot_repopulate_the_cache(self):
        """A GET begun before a Concept mutation must not become eligible cache
        data when it finally resolves."""
        server, page, _context, _collector = self._start()
        # A Concept nothing has fetched yet, so the read really goes to the
        # network rather than being answered from in-memory request state.
        target = server.ids["concept_unvisited"]
        parent = server.ids["concept_parent"]

        _wait_sw_active(page)
        _open_concept_index(page)
        _wait_list_cached(page, "concepts:index")
        self.assertIsNone(_cached_entity(page, "concept", target))

        held = []

        def hold_target_get(route):
            req = route.request
            if req.method == "GET" and urlparse(req.url).path == "/api/concepts/" + target:
                held.append(route)
                return
            route.fallback()

        page.route("**/api/concepts/**", hold_target_get)
        try:
            page.evaluate(
                """id => {
                    window.__prksHeldConceptRead = window.prksOfflineReadEntity(
                        'concept', id, '/api/concepts/' + id, { domain: 'concepts' }
                    );
                }""",
                target,
            )
            for _ in range(100):
                if held:
                    break
                page.wait_for_timeout(100)
            self.assertTrue(held, "the Concept GET was not intercepted")
            generation_before = _concept_domain_generation(page)
            # A canonical Concept mutation lands while that read is still in flight.
            page.evaluate(
                "id => window.updateConcept(id, { description: 'stale-read coherence check.' })",
                parent,
            )
            self.assertGreater(_concept_domain_generation(page), generation_before)
            page.wait_for_function(
                "() => (typeof prksOfflineIsDomainBlocked === 'function'"
                " ? prksOfflineIsDomainBlocked('concepts') : true) === false",
                timeout=15000,
            )
            held[0].fallback()
            result = page.evaluate("() => window.__prksHeldConceptRead")
            # The pre-mutation response still resolves to its caller ...
            self.assertEqual(result["source"], "server")
            page.wait_for_timeout(500)
            # ... but it is not eligible offline cache data.
            self.assertIsNone(
                _cached_entity(page, "concept", target),
                "a pre-mutation read must not repopulate the invalidated domain",
            )
        finally:
            _safe_unroute(page, "**/api/concepts/**", hold_target_get)

        # A later authoritative read in the current generation populates it again.
        _open_concept(page, target)
        _wait_entity_cached(page, "concept", target)

    def test_successful_research_notes_save_invalidates_concept_domain(self):
        """Research Notes are the canonical Work -> Concept mention source."""
        server, page, context, _collector = self._start()
        child = server.ids["concept_child"]

        _wait_sw_active(page)
        _open_concept_index(page)
        _wait_list_cached(page, "concepts:index")
        _open_concept(page, child)
        _wait_entity_cached(page, "concept", child)
        _open_work_from_home(page, WORK_A_TITLE)
        page.locator(".work-notes-editor-wrap .CodeMirror").first.click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text("[[concept:%s]] plus a new note line" % CONCEPT_PARENT_NAME)
        page.locator('[data-prks-role="editor-status"]', has_text="All changes saved").wait_for(timeout=15000)

        _wait_entity_uncached(page, "concept", child)
        _wait_list_uncached(page, "concepts:index")

    def test_superseded_notes_save_still_invalidates_concept_domain(self):
        """Save #1 succeeded canonically even if a newer save #2 fails: stale for
        the UI is not the same as unsuccessful."""
        server, page, _context, _collector = self._start()
        child = server.ids["concept_child"]

        _wait_sw_active(page)
        _open_concept(page, child)
        _wait_entity_cached(page, "concept", child)
        _open_work_from_home(page, WORK_A_TITLE)
        page.locator(".work-notes-editor-wrap .CodeMirror").first.click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text("first save that really commits")
        page.locator('[data-prks-role="editor-status"]', has_text="All changes saved").wait_for(timeout=15000)
        generation_after_first = _concept_domain_generation(page)

        def reject_notes(route):
            if route.request.method == "PATCH":
                route.fulfill(status=500, content_type="application/json", body='{"error":"nope"}')
                return
            route.fallback()

        page.route("**/api/works/**", reject_notes)
        try:
            page.locator(".work-notes-editor-wrap .CodeMirror").first.click()
            page.keyboard.press("Control+A")
            page.keyboard.insert_text("second save that fails")
            page.locator('[data-prks-role="editor-status"]', has_text="Error saving changes").wait_for(timeout=15000)
        finally:
            _safe_unroute(page, "**/api/works/**", reject_notes)

        # The failed second save adds nothing, but the first one already did.
        self.assertGreaterEqual(generation_after_first, 1)
        self.assertIsNone(_cached_entity(page, "concept", child))

    def test_successful_work_metadata_save_invalidates_concept_domain(self):
        """Cached Concept details carry Work mention titles."""
        server, page, _context, _collector = self._start()
        child = server.ids["concept_child"]

        _wait_sw_active(page)
        _open_concept(page, child)
        _wait_entity_cached(page, "concept", child)
        _open_work_from_home(page, WORK_A_TITLE)
        new_title = "Concept Mention Title Changed"
        page.locator("#panel-content button", has_text="Edit metadata").click()
        page.locator("#meta-title").fill(new_title)
        page.locator("#inline-save-metadata-btn").click()
        page.locator("#panel-content .card-title", has_text=new_title).wait_for(timeout=15000)

        _wait_entity_uncached(page, "concept", child)

    def test_failed_work_metadata_save_retains_concept_cache(self):
        server, page, _context, _collector = self._start()
        child = server.ids["concept_child"]

        _wait_sw_active(page)
        _open_concept(page, child)
        _wait_entity_cached(page, "concept", child)
        _open_work_from_home(page, WORK_A_TITLE)
        generation_before = _concept_domain_generation(page)

        def reject_patch(route):
            if route.request.method == "PATCH":
                route.fulfill(status=500, content_type="application/json", body='{"error":"nope"}')
                return
            route.fallback()

        page.route("**/api/works/**", reject_patch)
        try:
            page.locator("#panel-content button", has_text="Edit metadata").click()
            page.locator("#meta-title").fill("Rejected title")
            page.locator("#inline-save-metadata-btn").click()
            page.wait_for_timeout(700)
            self.assertEqual(_concept_domain_generation(page), generation_before)
            self.assertIsNotNone(_cached_entity(page, "concept", child))
        finally:
            _safe_unroute(page, "**/api/works/**", reject_patch)

    def test_successful_work_delete_invalidates_concept_domain(self):
        """Deleting a Work removes its Concept mentions from canonical data."""
        server, page, _context, _collector = self._start()
        child = server.ids["concept_child"]

        _wait_sw_active(page)
        _open_concept(page, child)
        _wait_entity_cached(page, "concept", child)
        _open_work_from_home(page, WORK_A_TITLE)
        _open_details_drawer_if_tiled(page)
        advanced = page.locator(".work-details-advanced")
        if advanced.get_attribute("open") is None:
            advanced.locator("summary").click()
        page.locator(".delete-work-btn").click()
        page.locator("#prks-modal-confirm:not(.hidden)", has_text="Delete file?").wait_for()
        page.locator("#prks-modal-confirm-ok").click()
        page.wait_for_function("() => location.hash === '#/folders'", timeout=15000)

        _wait_entity_uncached(page, "concept", child)

    def test_unrelated_work_mutation_leaves_concept_cache_alone(self):
        """Tags/folders/playlists do not change the Concept read model."""
        server, page, _context, _collector = self._start()
        child = server.ids["concept_child"]
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_concept(page, child)
        _wait_entity_cached(page, "concept", child)
        generation_before = _concept_domain_generation(page)

        page.evaluate(
            """async (workId) => {
                const folderId = await createFolder('Concept-neutral folder', '');
                return addWorkToFolder(folderId, workId);
            }""",
            arg=work_a,
        )
        page.wait_for_function(
            "id => window.createPrksOfflineStore().getEntity('work', id).then(row => row === null)",
            arg=work_a,
            timeout=15000,
        )
        self.assertEqual(_concept_domain_generation(page), generation_before)
        self.assertIsNotNone(_cached_entity(page, "concept", child))


def _safe_unroute(page, pattern, handler):
    try:
        page.unroute(pattern, handler)
    except Exception:
        pass


if __name__ == "__main__":
    unittest.main()
