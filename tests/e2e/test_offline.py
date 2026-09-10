"""Real Chromium + real PRKS server offline/PWA scenarios (Phase 1, read-only).

Collected only when PRKS_E2E=1 (see tests/e2e/run.py). These scenarios need a
real Service Worker and real IndexedDB, so -- unlike tests.e2e.test_app, which
deliberately blocks service workers for determinism -- every context here is
opened with service_workers="allow".
"""
from __future__ import annotations

import json
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
    POSITION_A_DESCRIPTION,
    POSITION_A_NAME,
    POSITION_ARGUMENT_NAME,
    POSITION_ARGUMENT_VERDICT_LABEL,
    POSITION_B_NAME,
    WORK_A_TITLE,
    WORK_B_TITLE,
    ARGUMENT_A_NAME,
    ARGUMENT_A_TEXT,
    ARGUMENT_B_NAME,
    ARGUMENT_C_NAME,
    ARGUMENT_SOURCE_PAGES,
    ARGUMENT_UNVISITED_NAME,
    PERSON_DISPLAY,
    PERSON_LAST,
    STANCE_NAME,
    STANCE_TEXT,
    PERSON_A_ABOUT,
    PERSON_A_ALIASES,
    PERSON_B_DISPLAY,
    PERSON_GROUP_NAME,
    PERSON_UNVISITED_DISPLAY,
    seed_arguments_library,
    seed_concepts_library,
    seed_library,
    seed_people_library,
    seed_positions_library,
)
from tests.e2e.harness import AppServer, PageCollector, open_app_page, require_chromium
from tests.e2e.test_app import (
    MINIMAL_PDF,
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


def _open_position_index(page):
    page.evaluate("() => { void window.prksNavigate('#/positions'); }")
    page.wait_for_function("() => location.hash === '#/positions'")


def _open_position(page, position_id):
    page.evaluate("id => { void window.prksNavigate('#/positions/' + id); }", position_id)
    page.wait_for_function("id => decodeURIComponent(location.hash).indexOf(id) !== -1", arg=position_id)


def _open_argument_index(page, kind=""):
    target = "#/arguments" + ("?kind=" + kind if kind else "")
    page.evaluate("h => { void window.prksNavigate(h); }", target)
    page.wait_for_function("h => location.hash === h", arg=target)


def _open_argument(page, argument_id):
    page.evaluate("id => { void window.prksNavigate('#/arguments/' + id); }", argument_id)
    page.wait_for_function("id => decodeURIComponent(location.hash).indexOf(id) !== -1", arg=argument_id)


def _row_titles(page):
    return page.evaluate(
        "() => Array.from(document.querySelectorAll('.prks-research-row__title')).map(e => e.textContent.trim())"
    )


def _open_people_index(page, role=""):
    target = "#/people" + ("/role/" + role if role else "")
    page.evaluate("h => { void window.prksNavigate(h); }", target)
    page.wait_for_function("h => location.hash === h", arg=target)


def _open_person(page, person_id):
    page.evaluate("id => { void window.prksNavigate('#/people/' + id); }", person_id)
    page.wait_for_function("id => decodeURIComponent(location.hash).indexOf(id) !== -1", arg=person_id)


def _person_row_names(page):
    return page.evaluate(
        "() => Array.from(document.querySelectorAll('.prks-people-list__title'))"
        ".map(e => e.textContent.trim())"
    )


def _domain_generation(page, domain):
    return page.evaluate(
        "d => (typeof prksOfflineDomainGeneration === 'function' ? prksOfflineDomainGeneration(d) : null)",
        domain,
    )


def _domain_blocked(page, domain):
    return page.evaluate(
        "d => (typeof prksOfflineIsDomainBlocked === 'function' ? prksOfflineIsDomainBlocked(d) : null)",
        domain,
    )


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
        # The indicator is visible for BOTH 'reconnecting' and 'offline', and the
        # startup probe is still in flight right after a reload, so settle on the
        # terminal state before reading its label rather than sampling mid-probe.
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
            timeout=20000,
        )
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
        # The startup probe after a reload can still be in flight ('reconnecting')
        # when the cached page has already rendered, so settle before asserting.
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
            timeout=20000,
        )
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

    def test_playlist_inline_work_rename_invalidates_concept_domain(self):
        """A Work title can also be changed from a Playlist. Cached Concept
        details carry Work mention titles, so that surface owes the Concepts
        domain the same invalidation as the metadata editor."""
        server, page, context, _collector = self._start()
        child = server.ids["concept_child"]
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_concept(page, child)
        _wait_content_contains(page, WORK_A_TITLE)
        _wait_entity_cached(page, "concept", child)
        generation_before = _concept_domain_generation(page)

        playlist_id = page.evaluate(
            """async (workId) => {
                const id = await createPlaylist('E2E Offline Rename Playlist', '');
                await addWorkToPlaylist(id, workId);
                return id;
            }""",
            arg=work_a,
        )
        page.evaluate("id => window.prksNavigate('#/playlists/' + encodeURIComponent(id))", arg=playlist_id)
        page.wait_for_selector(".prks-playlist-detail")
        # The inline per-video rename controls only exist in the Playlist's edit mode.
        page.locator("#prks-playlist-edit-btn").click()
        page.wait_for_selector('[data-pl-rename="%s"]' % work_a)
        page.locator('[data-pl-rename="%s"]' % work_a).click()
        renamed = "Renamed From The Playlist"
        page.locator("#prks-pl-rename-input-" + work_a).fill(renamed)
        page.locator('[data-pl-rename-save="%s"]' % work_a).click()
        page.wait_for_function("t => document.body.innerText.indexOf(t) !== -1", arg=renamed, timeout=15000)

        self.assertGreater(_concept_domain_generation(page), generation_before)
        _wait_entity_uncached(page, "concept", child)

        # And the stale mention title can no longer be served offline.
        context.set_offline(True)
        _open_concept(page, child)
        _wait_offline_unavailable(page)
        self.assertNotIn(WORK_A_TITLE, _content_text(page))

    def test_malformed_concept_index_response_never_replaces_a_good_cache(self):
        """A reachable server answering HTTP 200 with the wrong shape is a route
        error, and must not destroy the previously cached index."""
        server, page, context, _collector = self._start()

        _wait_sw_active(page)
        _open_concept_index(page)
        _wait_content_contains(page, CONCEPT_PARENT_NAME)
        _wait_list_cached(page, "concepts:index")
        good = _cached_list(page, "concepts:index")
        self.assertIsInstance(good["value"], list)

        def wrong_shape(route):
            if route.request.method == "GET" and urlparse(route.request.url).path == "/api/concepts":
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"error":"unexpected shape"}',
                )
                return
            route.fallback()

        page.route("**/api/concepts", wrong_shape)
        try:
            page.evaluate("() => { void window.prksNavigate('#/folders'); }")
            page.wait_for_function("() => location.hash === '#/folders'")
            _open_concept_index(page)
            page.wait_for_function(
                "() => document.querySelector('#prks-route-retry') !== null",
                timeout=15000,
            )
            body = _content_text(page)
            self.assertNotIn("No Concepts yet.", body)
            self.assertEqual(_connectivity_state(page), "online")
            page.wait_for_timeout(500)
            after = _cached_list(page, "concepts:index")
            self.assertEqual(after["value"], good["value"])
        finally:
            _safe_unroute(page, "**/api/concepts", wrong_shape)

        # The untouched snapshot is still what serves offline.
        page.evaluate("() => { void window.prksNavigate('#/folders'); }")
        page.wait_for_function("() => location.hash === '#/folders'")
        context.set_offline(True)
        _open_concept_index(page)
        _wait_offline_banner(page)
        _wait_content_contains(page, CONCEPT_PARENT_NAME)

    def test_malformed_concept_detail_response_never_replaces_a_good_cache(self):
        """Same rule for one Concept: a wrong-shaped 200 is a route error and
        leaves the cached Concept exactly as it was."""
        server, page, context, _collector = self._start()
        child = server.ids["concept_child"]

        _wait_sw_active(page)
        _open_concept(page, child)
        _wait_content_contains(page, CONCEPT_CHILD_NAME)
        _wait_entity_cached(page, "concept", child)
        good = _cached_entity(page, "concept", child)
        self.assertEqual(good["value"]["description"], CONCEPT_CHILD_DEFINITION)

        def wrong_shape(route):
            if route.request.method == "GET" and urlparse(route.request.url).path == "/api/concepts/" + child:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"error":"unexpected shape"}',
                )
                return
            route.fallback()

        page.route("**/api/concepts/**", wrong_shape)
        try:
            _open_concept_index(page)
            _wait_content_contains(page, CONCEPT_CHILD_NAME)
            _open_concept(page, child)
            page.wait_for_function(
                "() => document.querySelector('#prks-route-retry') !== null",
                timeout=15000,
            )
            body = _content_text(page)
            self.assertNotIn("Concept not found", body)
            self.assertNotIn("not available offline", body)
            self.assertEqual(_connectivity_state(page), "online")
            page.wait_for_timeout(500)
            self.assertEqual(_cached_entity(page, "concept", child)["value"], good["value"])
        finally:
            _safe_unroute(page, "**/api/concepts/**", wrong_shape)

        _open_concept_index(page)
        _wait_content_contains(page, CONCEPT_CHILD_NAME)
        context.set_offline(True)
        _open_concept(page, child)
        _wait_offline_banner(page)
        _wait_content_contains(page, CONCEPT_CHILD_DEFINITION)

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


class OfflinePositionTests(unittest.TestCase):
    """Phase 1 read-only Position routes: #/positions and #/positions/:positionId."""

    def _start(self):
        server = AppServer(seed_fn=seed_positions_library)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin, service_workers="allow")
        self.addCleanup(context.close)
        return server, page, context, collector

    def _record_calls(self, page, pattern):
        """Records every matching request issued from here on."""
        seen = []

        def record(route):
            seen.append((route.request.method, urlparse(route.request.url).path))
            route.fallback()

        page.route(pattern, record)
        self.addCleanup(lambda: _safe_unroute(page, pattern, record))
        return seen

    # ---- cached index -------------------------------------------------------

    def test_cached_position_index_renders_and_searches_offline(self):
        """Cached Position index renders offline with provenance, searches
        locally with zero API traffic, and offers no enabled New Position."""
        server, page, context, _collector = self._start()

        _wait_sw_active(page)
        _open_position_index(page)
        _wait_content_contains(page, POSITION_A_NAME)
        _wait_list_cached(page, "positions:index")

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_content_contains(page, POSITION_A_NAME)
        _wait_offline_banner(page)
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
            timeout=20000,
        )

        seen = self._record_calls(page, "**/api/positions**")
        page.locator("#prks-position-search").fill(POSITION_B_NAME)
        page.wait_for_function(
            "name => { const rows = document.querySelectorAll('.prks-research-row__title');"
            " return rows.length === 1 && rows[0].textContent.indexOf(name) !== -1; }",
            arg=POSITION_B_NAME,
        )
        # Description text is part of the same local filter.
        page.locator("#prks-position-search").fill("offline Position detail assertions")
        page.wait_for_function(
            "name => { const rows = document.querySelectorAll('.prks-research-row__title');"
            " return rows.length === 1 && rows[0].textContent.indexOf(name) !== -1; }",
            arg=POSITION_A_NAME,
        )
        page.locator("#prks-position-search").fill("no such position anywhere")
        page.locator(".prks-research-index__empty", has_text="match").wait_for()
        self.assertEqual(seen, [], "offline Position search must issue zero API requests")

        new_btn = page.locator("#prks-position-new")
        self.assertTrue(new_btn.is_disabled())
        self.assertEqual(new_btn.get_attribute("aria-disabled"), "true")

    def test_uncached_position_index_offline_is_explicitly_unavailable(self):
        """No cached index is "not cached", never "No Positions yet."."""
        server, page, context, _collector = self._start()

        _wait_sw_active(page)
        _open_position_index(page)
        _wait_content_contains(page, POSITION_A_NAME)
        _wait_list_cached(page, "positions:index")
        page.evaluate("() => { void window.prksNavigate('#/folders'); }")
        page.wait_for_function("() => location.hash === '#/folders'")
        _clear_cached_list(page, "positions:index")
        _wait_list_uncached(page, "positions:index")

        context.set_offline(True)
        _open_position_index(page)
        _wait_offline_unavailable(page)
        body = _content_text(page)
        self.assertIn("not available offline", body)
        self.assertNotIn("No Positions yet.", body)
        self.assertEqual(page.locator("#prks-position-new").count(), 0)

    def test_cached_empty_position_index_is_not_the_uncached_state(self):
        """An authoritative [] that really was cached still renders the ordinary
        empty state -- but its New Position escape route is disabled offline.
        That is a different thing from having no cached index at all."""
        server = AppServer(seed_fn=seed_concepts_library)  # no Positions seeded
        self.addCleanup(server.stop)
        server.start()
        page, context, _collector = open_app_page(_BROWSER, server.origin, service_workers="allow")
        self.addCleanup(context.close)

        _wait_sw_active(page)
        _open_position_index(page)
        _wait_content_contains(page, "No Positions yet.")
        _wait_list_cached(page, "positions:index")
        self.assertEqual(_cached_list(page, "positions:index")["value"], [])

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_content_contains(page, "No Positions yet.")
        _wait_offline_banner(page)
        body = _content_text(page)
        self.assertNotIn("not available offline", body)
        page.wait_for_function(
            "() => !!document.querySelector('#prks-position-new-empty[disabled]')", timeout=20000
        )
        self.assertTrue(page.locator("#prks-position-new").is_disabled())

    # ---- cached detail ------------------------------------------------------

    def test_cached_position_detail_renders_offline(self):
        """A Position opened online renders its full cached detail offline,
        including the Arguments & Stances it is targeted by."""
        server, page, context, _collector = self._start()
        position_a = server.ids["position_a"]

        _wait_sw_active(page)
        _open_position(page, position_a)
        _wait_content_contains(page, POSITION_A_NAME)
        _wait_entity_cached(page, "position", position_a)

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_content_contains(page, POSITION_A_NAME)
        _wait_offline_banner(page)
        body = _content_text(page)
        self.assertIn(POSITION_A_DESCRIPTION, body)
        self.assertIn("Arguments & Stances", body)
        self.assertIn(POSITION_ARGUMENT_NAME, body)
        self.assertIn(POSITION_ARGUMENT_VERDICT_LABEL, body)

    def test_cached_index_does_not_prefetch_every_position_detail(self):
        """Opening the index caches the list only; an unopened Position stays
        unavailable offline rather than mirroring the research network."""
        server, page, context, _collector = self._start()
        position_a = server.ids["position_a"]
        position_b = server.ids["position_b"]

        _wait_sw_active(page)
        _open_position_index(page)
        _wait_list_cached(page, "positions:index")
        _open_position(page, position_a)
        _wait_entity_cached(page, "position", position_a)
        _open_position_index(page)
        _wait_content_contains(page, POSITION_B_NAME)
        self.assertIsNone(_cached_entity(page, "position", position_b))

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        _wait_content_contains(page, POSITION_B_NAME)
        page.locator('.prks-research-row[href$="%s"]' % position_b).click()
        _wait_offline_unavailable(page)
        body = _content_text(page)
        self.assertIn("not available offline", body)
        self.assertNotIn("Position not found", body)
        # The Position that WAS opened online still works from cache.
        _wait_entity_cached(page, "position", position_a)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _open_position(page, position_a)
        _wait_content_contains(page, POSITION_A_NAME)

    # ---- Argument/Stance destinations are ordinary routes -------------------

    def test_cached_position_opens_a_cached_argument_offline(self):
        """Arguments became offline-capable after Positions did, so a Position
        no longer decides whether an Argument destination is reachable. The row
        is an ordinary link and the Argument route resolves it from its own
        cache."""
        server, page, context, _collector = self._start()
        position_a = server.ids["position_a"]
        argument_id = server.ids["position_argument"]

        _wait_sw_active(page)
        _open_position(page, position_a)
        _wait_content_contains(page, POSITION_ARGUMENT_NAME)
        _wait_entity_cached(page, "position", position_a)
        _open_argument(page, argument_id)
        _wait_content_contains(page, POSITION_ARGUMENT_NAME)
        _wait_entity_cached(page, "argument", argument_id)

        context.set_offline(True)
        _open_position(page, position_a)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_offline_banner(page)

        arg_link = page.locator('[data-prks-role="position-argument-link"]')
        self.assertEqual(arg_link.count(), 1)
        # No Position-specific offline policy is applied to the row any more.
        self.assertIsNone(arg_link.get_attribute("aria-disabled"))
        self.assertIsNone(arg_link.get_attribute("title"))
        self.assertEqual(arg_link.get_attribute("href"), "#/arguments/" + argument_id)

        arg_link.click()
        page.wait_for_function(
            "id => decodeURIComponent(location.hash).indexOf(id) !== -1", arg=argument_id, timeout=20000
        )
        _wait_offline_banner(page)
        self.assertIn(POSITION_ARGUMENT_NAME, _content_text(page))

    def test_cached_position_reports_an_uncached_argument_as_unavailable(self):
        """The destination that was never opened online is the Argument route's
        own "not available offline", not a Position-side refusal and not a
        misleading "Argument not found"."""
        server, page, context, _collector = self._start()
        position_a = server.ids["position_a"]
        argument_id = server.ids["position_argument"]

        _wait_sw_active(page)
        _open_position(page, position_a)
        _wait_content_contains(page, POSITION_ARGUMENT_NAME)
        _wait_entity_cached(page, "position", position_a)
        # Deliberately never opened online, so its detail is not cached.
        self.assertIsNone(_cached_entity(page, "argument", argument_id))

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_offline_banner(page)

        seen = self._record_calls(page, "**/api/arguments**")
        arg_link = page.locator('[data-prks-role="position-argument-link"]')
        self.assertIsNone(arg_link.get_attribute("aria-disabled"))
        arg_link.click()
        page.wait_for_function(
            "id => decodeURIComponent(location.hash).indexOf(id) !== -1", arg=argument_id, timeout=20000
        )
        _wait_offline_unavailable(page)
        body = _content_text(page)
        self.assertIn("not available offline", body)
        self.assertNotIn("Argument not found", body)
        self.assertNotIn("not available offline yet", body)
        # The Argument route may attempt its own authoritative read; what must
        # never happen is a Position-side alert instead of the route resolving.
        self.assertTrue(page.locator("#prks-modal-confirm.hidden").count() >= 1)
        del seen

    def test_position_code_no_longer_applies_its_own_argument_offline_policy(self):
        """Structural guard for the cleanup: ordinary workspace navigation owns
        every activation gesture, so Position code must not mark Argument rows
        or attach its own activation interception."""
        server, page, context, _collector = self._start()
        position_a = server.ids["position_a"]

        _wait_sw_active(page)
        _open_position(page, position_a)
        _wait_content_contains(page, POSITION_ARGUMENT_NAME)
        _wait_entity_cached(page, "position", position_a)

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_offline_banner(page)
        # The graph button is the one control the Position route still settles.
        page.wait_for_function(
            "() => !!document.querySelector('#prks-position-view-graph[disabled]')", timeout=20000
        )
        state = page.evaluate(
            """() => {
                const a = document.querySelector('[data-prks-role="position-argument-link"]');
                const hosts = [...document.querySelectorAll('*')]
                    .filter(el => el.__prksPositionArgumentGuardBound).length;
                return {
                    ariaDisabled: a && a.getAttribute('aria-disabled'),
                    title: a && a.getAttribute('title'),
                    guardHosts: hosts,
                };
            }"""
        )
        self.assertEqual(state, {"ariaDisabled": None, "title": None, "guardHosts": 0})


    def test_position_shape_validators_reject_unusable_rows(self):
        """The validators gate cache publication, so they are checked directly
        against the shapes a server could actually hand back."""
        server, page, _context, _collector = self._start()
        _wait_sw_active(page)
        _open_position_index(page)
        _wait_content_contains(page, POSITION_A_NAME)

        index_cases = page.evaluate(
            """() => ({
                empty: prksIsPositionIndexShape([]),
                ok: prksIsPositionIndexShape([{ id: 'P-1', name: 'A' }]),
                notArray: prksIsPositionIndexShape({ error: 'boom' }),
                nullValue: prksIsPositionIndexShape(null),
                missingId: prksIsPositionIndexShape([{ name: 'A' }]),
                blankId: prksIsPositionIndexShape([{ id: '   ', name: 'A' }]),
                nestedArrayRow: prksIsPositionIndexShape([['P-1']]),
                oneBadRow: prksIsPositionIndexShape([{ id: 'P-1' }, { id: '' }]),
            })"""
        )
        self.assertEqual(
            index_cases,
            {
                "empty": True,
                "ok": True,
                "notArray": False,
                "nullValue": False,
                "missingId": False,
                "blankId": False,
                "nestedArrayRow": False,
                "oneBadRow": False,
            },
        )

        detail_cases = page.evaluate(
            """() => ({
                ok: prksIsPositionShape({ id: 'P-1', arguments: [] }, 'P-1'),
                okWithArgs: prksIsPositionShape(
                    { id: 'P-1', arguments: [{ id: 'A-1', name: 'x', kind: 'stance' }] }, 'P-1'),
                argsWithoutDisplayFields: prksIsPositionShape(
                    { id: 'P-1', arguments: [{ id: 'A-1' }] }, 'P-1'),
                wrongId: prksIsPositionShape({ id: 'P-2', arguments: [] }, 'P-1'),
                missingArguments: prksIsPositionShape({ id: 'P-1' }, 'P-1'),
                argumentsNotArray: prksIsPositionShape({ id: 'P-1', arguments: {} }, 'P-1'),
                argumentMissingId: prksIsPositionShape(
                    { id: 'P-1', arguments: [{ name: 'no id' }] }, 'P-1'),
                argumentBlankId: prksIsPositionShape(
                    { id: 'P-1', arguments: [{ id: '  ', name: 'blank' }] }, 'P-1'),
                errorBody: prksIsPositionShape({ error: 'boom' }, 'P-1'),
                arrayBody: prksIsPositionShape([], 'P-1'),
            })"""
        )
        self.assertEqual(
            detail_cases,
            {
                "ok": True,
                "okWithArgs": True,
                "argsWithoutDisplayFields": True,
                "wrongId": False,
                "missingArguments": False,
                "argumentsNotArray": False,
                "argumentMissingId": False,
                "argumentBlankId": False,
                "errorBody": False,
                "arrayBody": False,
            },
        )

    def test_position_graph_action_requires_a_connection_offline(self):
        server, page, context, _collector = self._start()
        position_a = server.ids["position_a"]

        _wait_sw_active(page)
        _open_position(page, position_a)
        _wait_content_contains(page, POSITION_A_NAME)
        _wait_entity_cached(page, "position", position_a)
        self.assertFalse(page.locator("#prks-position-view-graph").is_disabled())

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_offline_banner(page)
        page.wait_for_function(
            "() => !!document.querySelector('#prks-position-view-graph[disabled]')", timeout=20000
        )
        page.locator("#prks-position-view-graph").click(force=True)
        page.wait_for_timeout(300)
        self.assertNotIn("/graph", page.evaluate("() => location.hash"))

        context.set_offline(False)
        page.evaluate("""async () => { try { await window.prksRequest('/api/settings'); } catch (_e) {} }""")
        page.wait_for_function(
            "() => !document.querySelector('#prks-position-view-graph[disabled]')", timeout=20000
        )

    # ---- mutation blocking --------------------------------------------------

    def test_offline_position_create_is_blocked(self):
        server, page, context, _collector = self._start()

        _wait_sw_active(page)
        _open_position_index(page)
        _wait_content_contains(page, POSITION_A_NAME)
        _wait_list_cached(page, "positions:index")

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_content_contains(page, POSITION_A_NAME)
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
            timeout=20000,
        )

        mutations = []

        def record_mutation(route):
            if route.request.method in ("POST", "PATCH", "PUT", "DELETE"):
                mutations.append((route.request.method, urlparse(route.request.url).path))
            route.fallback()

        page.route("**/api/positions**", record_mutation)
        try:
            btn = page.locator("#prks-position-new")
            self.assertTrue(btn.is_disabled())
            self.assertEqual(btn.get_attribute("aria-disabled"), "true")
            btn.click(force=True)
            page.wait_for_timeout(300)
            self.assertEqual(page.locator("#prks-modal-confirm .prks-modal-prompt__input").count(), 0)
            self.assertEqual(mutations, [])
        finally:
            _safe_unroute(page, "**/api/positions**", record_mutation)

    def test_disconnect_while_position_prompt_open_blocks_the_create(self):
        """The re-check immediately before createPosition() means clicking
        Create after the connection dropped issues no POST."""
        server, page, context, _collector = self._start()

        _wait_sw_active(page)
        _open_position_index(page)
        _wait_content_contains(page, POSITION_A_NAME)

        page.locator("#prks-position-new").click()
        prompt_input = page.locator("#prks-modal-confirm .prks-modal-prompt__input")
        prompt_input.wait_for()
        prompt_input.fill("Created while disconnected")

        mutations = []

        def block_api(route):
            if route.request.method in ("POST", "PATCH", "PUT", "DELETE"):
                mutations.append((route.request.method, urlparse(route.request.url).path))
            route.abort("connectionrefused")

        page.route("**/api/**", block_api)
        try:
            page.evaluate("""async () => { try { await window.prksRequest('/api/settings'); } catch (_e) {} }""")
            page.wait_for_function(
                "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
                timeout=20000,
            )
            page.locator("#prks-modal-confirm-ok").click()
            page.wait_for_timeout(500)
            self.assertEqual(mutations, [], "no Position create may be attempted after disconnect")
        finally:
            _safe_unroute(page, "**/api/**", block_api)

        page.evaluate("""async () => { try { await window.prksRequest('/api/settings'); } catch (_e) {} }""")
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'online'",
            timeout=20000,
        )
        names = page.evaluate("() => fetchPositions().then(items => items.map(p => p.name))")
        self.assertNotIn("Created while disconnected", names)

    def test_live_position_pages_react_to_connectivity(self):
        """Pages mounted online become read-only in place, without a reload."""
        server, page, _context, _collector = self._start()
        position_a = server.ids["position_a"]

        _wait_sw_active(page)
        _open_position_index(page)
        _wait_content_contains(page, POSITION_A_NAME)
        self.assertFalse(page.locator("#prks-position-new").is_disabled())

        def abort_api(route):
            route.abort("connectionrefused")

        page.route("**/api/**", abort_api)
        try:
            page.evaluate("""async () => { try { await window.prksRequest('/api/settings'); } catch (_e) {} }""")
            page.wait_for_function(
                "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
                timeout=20000,
            )
            self.assertTrue(page.evaluate("() => navigator.onLine"))
            page.wait_for_function(
                "() => !!document.querySelector('#prks-position-new[disabled]')", timeout=20000
            )
        finally:
            _safe_unroute(page, "**/api/**", abort_api)

        page.evaluate("""async () => { await window.prksRequest('/api/settings'); }""")
        page.wait_for_function(
            "() => !document.querySelector('#prks-position-new[disabled]')", timeout=20000
        )

        # Now the detail page: graph + Argument destinations settle, content stays.
        _open_position(page, position_a)
        _wait_content_contains(page, POSITION_A_NAME)
        _wait_entity_cached(page, "position", position_a)
        self.assertFalse(page.locator("#prks-position-view-graph").is_disabled())

        page.route("**/api/**", abort_api)
        try:
            page.evaluate("""async () => { try { await window.prksRequest('/api/settings'); } catch (_e) {} }""")
            page.wait_for_function(
                "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
                timeout=20000,
            )
            page.wait_for_function(
                "() => !!document.querySelector('#prks-position-view-graph[disabled]')", timeout=20000
            )
            # Argument/Stance rows never became disabled, so there is nothing
            # for reconnect to restore -- the Argument route owns availability.
            self.assertIsNone(
                page.locator('[data-prks-role="position-argument-link"]').get_attribute("aria-disabled")
            )
            # Read-only Position content stays readable throughout.
            body = _content_text(page)
            self.assertIn(POSITION_A_DESCRIPTION, body)
            self.assertIn(POSITION_ARGUMENT_NAME, body)
        finally:
            _safe_unroute(page, "**/api/**", abort_api)

        page.evaluate("""async () => { await window.prksRequest('/api/settings'); }""")
        page.wait_for_function(
            "() => !document.querySelector('#prks-position-view-graph[disabled]')", timeout=20000
        )
        self.assertIsNone(
            page.locator('[data-prks-role="position-argument-link"]').get_attribute("aria-disabled")
        )

    # ---- HTTP errors are never disguised as offline -------------------------

    def test_position_detail_http_errors_keep_their_normal_meaning(self):
        server, page, context, _collector = self._start()
        position_a = server.ids["position_a"]

        _wait_sw_active(page)
        _open_position(page, position_a)
        _wait_content_contains(page, POSITION_A_NAME)
        _wait_entity_cached(page, "position", position_a)
        good = _cached_entity(page, "position", position_a)

        _open_position(page, "P-DOES-NOT-EXIST")
        _wait_content_contains(page, "Position not found")
        self.assertEqual(_connectivity_state(page), "online")

        mode = {"status": 500}

        def broken(route):
            req = route.request
            if req.method == "GET" and urlparse(req.url).path == "/api/positions/" + position_a:
                if mode["status"] == 500:
                    route.fulfill(status=500, content_type="application/json", body='{"error":"boom"}')
                    return
                if mode["status"] == 200:
                    route.fulfill(
                        status=200, content_type="application/json", body='{"error":"unexpected shape"}'
                    )
                    return
            route.fallback()

        page.route("**/api/positions/**", broken)
        try:
            _open_position(page, position_a)
            page.wait_for_function(
                "() => document.querySelector('#prks-route-retry') !== null", timeout=15000
            )
            body = _content_text(page)
            self.assertNotIn("Position not found", body)
            self.assertNotIn("not available offline", body)
            self.assertEqual(_connectivity_state(page), "online")

            # HTTP 200 with a wrong-shaped body is also a route error, and must
            # not overwrite the good snapshot already cached on this device.
            mode["status"] = 200
            _open_position_index(page)
            _wait_content_contains(page, POSITION_A_NAME)
            _open_position(page, position_a)
            page.wait_for_function(
                "() => document.querySelector('#prks-route-retry') !== null", timeout=15000
            )
            page.wait_for_timeout(500)
            self.assertEqual(_cached_entity(page, "position", position_a)["value"], good["value"])
            mode["status"] = 0
        finally:
            _safe_unroute(page, "**/api/positions/**", broken)

        # Transport failure + valid cache -> the cached Position.
        _open_position_index(page)
        _wait_content_contains(page, POSITION_A_NAME)
        context.set_offline(True)
        _open_position(page, position_a)
        _wait_offline_banner(page)
        _wait_content_contains(page, POSITION_A_DESCRIPTION)
        # Transport failure + no cache -> offline unavailable.
        _open_position(page, server.ids["position_b"])
        _wait_offline_unavailable(page)

    # ---- domain coherence ---------------------------------------------------

    def test_position_mutation_invalidates_the_whole_position_domain(self):
        server, page, context, _collector = self._start()
        position_a = server.ids["position_a"]
        position_b = server.ids["position_b"]

        _wait_sw_active(page)
        _open_position_index(page)
        _wait_list_cached(page, "positions:index")
        _open_position(page, position_a)
        _wait_entity_cached(page, "position", position_a)
        _open_position(page, position_b)
        _wait_entity_cached(page, "position", position_b)
        generation_before = _domain_generation(page, "positions")

        page.evaluate("id => window.updatePosition(id, { description: 'Domain coherence check.' })", position_a)
        self.assertGreater(_domain_generation(page, "positions"), generation_before)
        # The whole domain goes, including the Position that did not change.
        _wait_entity_uncached(page, "position", position_b)
        _wait_list_uncached(page, "positions:index")

        context.set_offline(True)
        _open_position(page, position_b)
        _wait_offline_unavailable(page)
        _open_position_index(page)
        _wait_offline_unavailable(page)

    def test_failed_position_mutation_retains_the_position_cache(self):
        server, page, context, _collector = self._start()
        position_a = server.ids["position_a"]

        _wait_sw_active(page)
        _open_position(page, position_a)
        _wait_entity_cached(page, "position", position_a)
        generation_before = _domain_generation(page, "positions")

        def reject_patch(route):
            if route.request.method in ("PATCH", "POST", "DELETE"):
                route.fulfill(status=500, content_type="application/json", body='{"error":"nope"}')
                return
            route.fallback()

        page.route("**/api/positions**", reject_patch)
        try:
            page.evaluate(
                """async (id) => {
                    try { await window.updatePosition(id, { description: 'never applied' }); } catch (_e) {}
                    try { await window.createPosition({ name: 'never created' }); } catch (_e) {}
                    try { await window.deletePosition(id); } catch (_e) {}
                }""",
                position_a,
            )
            page.wait_for_timeout(400)
            self.assertEqual(_domain_generation(page, "positions"), generation_before)
            self.assertIsNotNone(_cached_entity(page, "position", position_a))
        finally:
            _safe_unroute(page, "**/api/positions**", reject_patch)

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_content_contains(page, POSITION_A_NAME)
        _wait_offline_banner(page)

    def test_argument_rename_invalidates_the_position_domain(self):
        """A cached Position detail shows the Argument's name and kind."""
        server, page, context, _collector = self._start()
        position_a = server.ids["position_a"]
        argument_id = server.ids["position_argument"]

        _wait_sw_active(page)
        _open_position_index(page)
        _wait_list_cached(page, "positions:index")
        _open_position(page, position_a)
        _wait_entity_cached(page, "position", position_a)
        generation_before = _domain_generation(page, "positions")

        page.evaluate(
            "id => window.updateArgument(id, { name: 'Renamed Targeting Argument', kind: 'stance' })",
            argument_id,
        )
        self.assertGreater(_domain_generation(page, "positions"), generation_before)
        _wait_entity_uncached(page, "position", position_a)
        _wait_list_uncached(page, "positions:index")

        context.set_offline(True)
        _open_position(page, position_a)
        _wait_offline_unavailable(page)
        self.assertNotIn(POSITION_ARGUMENT_NAME, _content_text(page))

    def test_failed_argument_update_retains_the_position_cache(self):
        server, page, _context, _collector = self._start()
        position_a = server.ids["position_a"]
        argument_id = server.ids["position_argument"]

        _wait_sw_active(page)
        _open_position(page, position_a)
        _wait_entity_cached(page, "position", position_a)
        generation_before = _domain_generation(page, "positions")

        def reject(route):
            if route.request.method in ("PATCH", "PUT", "DELETE"):
                route.fulfill(status=500, content_type="application/json", body='{"error":"nope"}')
                return
            route.fallback()

        page.route("**/api/arguments**", reject)
        try:
            page.evaluate(
                """async (id) => {
                    try { await window.updateArgument(id, { name: 'never applied' }); } catch (_e) {}
                }""",
                argument_id,
            )
            page.wait_for_timeout(400)
            self.assertEqual(_domain_generation(page, "positions"), generation_before)
            self.assertIsNotNone(_cached_entity(page, "position", position_a))
        finally:
            _safe_unroute(page, "**/api/arguments**", reject)

    def test_argument_target_change_invalidates_the_position_domain(self):
        """Targets carry Position membership and the per-Position verdict."""
        server, page, context, _collector = self._start()
        position_a = server.ids["position_a"]
        position_b = server.ids["position_b"]
        argument_id = server.ids["position_argument"]

        _wait_sw_active(page)
        _open_position(page, position_a)
        _wait_entity_cached(page, "position", position_a)
        generation_before = _domain_generation(page, "positions")

        # A now opposes P-A and additionally targets P-B.
        page.evaluate(
            """([argId, a, b]) => window.putArgumentTargets(argId, [
                { type: 'position', id: a, verdict_id: 'opposes' },
                { type: 'position', id: b, verdict_id: 'supports' },
            ])""",
            [argument_id, position_a, position_b],
        )
        self.assertGreater(_domain_generation(page, "positions"), generation_before)
        _wait_entity_uncached(page, "position", position_a)

        # A later authoritative read publishes the NEW verdict, never the old one.
        _open_position(page, position_a)
        _wait_content_contains(page, "Opposes")
        _wait_entity_cached(page, "position", position_a)
        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_offline_banner(page)
        self.assertIn("Opposes", _content_text(page))
        self.assertNotIn("Supports", _content_text(page))

    def test_argument_delete_invalidates_the_position_domain(self):
        server, page, context, _collector = self._start()
        position_a = server.ids["position_a"]
        argument_id = server.ids["position_argument"]

        _wait_sw_active(page)
        _open_position(page, position_a)
        _wait_entity_cached(page, "position", position_a)
        generation_before = _domain_generation(page, "positions")

        page.evaluate("id => window.deleteArgument(id)", argument_id)
        self.assertGreater(_domain_generation(page, "positions"), generation_before)
        _wait_entity_uncached(page, "position", position_a)

        _open_position(page, position_a)
        _wait_content_contains(page, "No Arguments or Stances target this Position yet.")

    def test_argument_source_change_does_not_invalidate_positions(self):
        """Source Works are not part of the Position read model."""
        server, page, _context, _collector = self._start()
        position_a = server.ids["position_a"]
        argument_id = server.ids["position_argument"]
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_position(page, position_a)
        _wait_entity_cached(page, "position", position_a)
        generation_before = _domain_generation(page, "positions")

        page.evaluate(
            "([argId, workId]) => window.putArgumentSources(argId, [{ work_id: workId, pages: '1-2' }])",
            [argument_id, work_a],
        )
        page.wait_for_timeout(400)
        self.assertEqual(_domain_generation(page, "positions"), generation_before)
        self.assertIsNotNone(_cached_entity(page, "position", position_a))

    def test_stale_pre_mutation_position_read_cannot_repopulate_the_cache(self):
        server, page, _context, _collector = self._start()
        # A Position nothing has fetched yet, so the read really goes out.
        target = server.ids["position_b"]
        argument_id = server.ids["position_argument"]

        _wait_sw_active(page)
        _open_position_index(page)
        _wait_list_cached(page, "positions:index")
        self.assertIsNone(_cached_entity(page, "position", target))

        held = []

        def hold_target_get(route):
            req = route.request
            if req.method == "GET" and urlparse(req.url).path == "/api/positions/" + target:
                held.append(route)
                return
            route.fallback()

        page.route("**/api/positions/**", hold_target_get)
        try:
            page.evaluate(
                """id => {
                    window.__prksHeldPositionRead = window.prksOfflineReadEntity(
                        'position', id, '/api/positions/' + id, { domain: 'positions' }
                    );
                }""",
                target,
            )
            for _ in range(100):
                if held:
                    break
                page.wait_for_timeout(100)
            self.assertTrue(held, "the Position GET was not intercepted")
            generation_before = _domain_generation(page, "positions")
            # An Argument-side mutation lands while that read is still in flight.
            page.evaluate("id => window.updateArgument(id, { name: 'Stale read check' })", argument_id)
            self.assertGreater(_domain_generation(page, "positions"), generation_before)
            page.wait_for_function(
                "() => (typeof prksOfflineIsDomainBlocked === 'function'"
                " ? prksOfflineIsDomainBlocked('positions') : true) === false",
                timeout=15000,
            )
            held[0].fallback()
            result = page.evaluate("() => window.__prksHeldPositionRead")
            self.assertEqual(result["source"], "server")
            page.wait_for_timeout(500)
            self.assertIsNone(
                _cached_entity(page, "position", target),
                "a pre-mutation read must not repopulate the invalidated domain",
            )
        finally:
            _safe_unroute(page, "**/api/positions/**", hold_target_get)

        _open_position(page, target)
        _wait_entity_cached(page, "position", target)

    # ---- domain independence -----------------------------------------------

    def test_position_and_concept_domains_are_independent(self):
        """The first real demonstration of two simultaneous coherence domains."""
        server, page, context, _collector = self._start()
        position_a = server.ids["position_a"]
        concept_child = server.ids["concept_child"]
        argument_id = server.ids["position_argument"]

        _wait_sw_active(page)
        _open_position(page, position_a)
        _wait_entity_cached(page, "position", position_a)
        _open_concept(page, concept_child)
        _wait_entity_cached(page, "concept", concept_child)
        concepts_before = _domain_generation(page, "concepts")

        # An Argument target change invalidates Positions only.
        page.evaluate(
            """([argId, a]) => window.putArgumentTargets(argId, [
                { type: 'position', id: a, verdict_id: 'qualifies' },
            ])""",
            [argument_id, position_a],
        )
        _wait_entity_uncached(page, "position", position_a)
        self.assertEqual(_domain_generation(page, "concepts"), concepts_before)
        self.assertFalse(_domain_blocked(page, "concepts"))
        self.assertIsNotNone(_cached_entity(page, "concept", concept_child))

        context.set_offline(True)
        _open_position(page, position_a)
        _wait_offline_unavailable(page)
        _open_concept(page, concept_child)
        _wait_offline_banner(page)
        _wait_content_contains(page, CONCEPT_CHILD_NAME)

        # Back online: repopulate the Position, then invalidate Concepts only.
        context.set_offline(False)
        page.evaluate("""async () => { try { await window.prksRequest('/api/settings'); } catch (_e) {} }""")
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'online'",
            timeout=20000,
        )
        _open_position(page, position_a)
        _wait_entity_cached(page, "position", position_a)
        positions_before = _domain_generation(page, "positions")

        page.evaluate(
            "id => window.updateConcept(id, { description: 'Domain independence check.' })", concept_child
        )
        _wait_entity_uncached(page, "concept", concept_child)
        self.assertEqual(_domain_generation(page, "positions"), positions_before)
        self.assertFalse(_domain_blocked(page, "positions"))
        self.assertIsNotNone(_cached_entity(page, "position", position_a))

        context.set_offline(True)
        _open_concept(page, concept_child)
        _wait_offline_unavailable(page)
        _open_position(page, position_a)
        _wait_offline_banner(page)
        _wait_content_contains(page, POSITION_A_NAME)


class OfflineArgumentTests(unittest.TestCase):
    """Phase 1 read-only Argument/Stance routes: #/arguments and #/arguments/:id."""

    def _start(self):
        server = AppServer(seed_fn=seed_arguments_library)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin, service_workers="allow")
        self.addCleanup(context.close)
        return server, page, context, collector

    def _record_calls(self, page, pattern):
        seen = []

        def record(route):
            seen.append((route.request.method, urlparse(route.request.url).path))
            route.fallback()

        page.route(pattern, record)
        self.addCleanup(lambda: _safe_unroute(page, pattern, record))
        return seen

    # ---- one cached index, local ?kind= filtering ---------------------------

    def test_one_cached_index_serves_every_kind_filter_offline(self):
        """Visiting the Stances tab online must cache the COMPLETE collection, so
        All/Arguments/Stances all work offline from that single key. This is the
        regression test for the single-cache-key design."""
        server, page, context, _collector = self._start()

        _wait_sw_active(page)
        # Deliberately the most filtered route.
        _open_argument_index(page, "stance")
        _wait_content_contains(page, STANCE_NAME)
        _wait_list_cached(page, "arguments:index")

        cached = _cached_list(page, "arguments:index")["value"]
        kinds = sorted({row["kind"] for row in cached})
        self.assertEqual(kinds, ["argument", "stance"], "a filtered route cached a filtered list")
        self.assertIn(ARGUMENT_A_NAME, [row["name"] for row in cached])

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_content_contains(page, STANCE_NAME)
        _wait_offline_banner(page)
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
            timeout=20000,
        )
        # The Stances tab shows only Stances ...
        stance_titles = _row_titles(page)
        self.assertIn(STANCE_NAME, stance_titles)
        self.assertNotIn(ARGUMENT_A_NAME, stance_titles)

        # A route change is still a fresh read-through -- it attempts the server
        # and falls back to cache -- so what matters here is that it never asks
        # for a server-filtered list. That is the single-cache-key invariant.
        requested = []

        def record_urls(route):
            requested.append(route.request.url)
            route.fallback()

        page.route("**/api/arguments**", record_urls)
        self.addCleanup(lambda: _safe_unroute(page, "**/api/arguments**", record_urls))

        _open_argument_index(page, "argument")
        _wait_content_contains(page, ARGUMENT_A_NAME)
        argument_titles = _row_titles(page)
        self.assertIn(ARGUMENT_A_NAME, argument_titles)
        self.assertNotIn(STANCE_NAME, argument_titles)

        _open_argument_index(page)
        _wait_content_contains(page, STANCE_NAME)
        all_titles = _row_titles(page)
        self.assertIn(ARGUMENT_A_NAME, all_titles)
        self.assertIn(STANCE_NAME, all_titles)
        self.assertGreater(len(all_titles), len(argument_titles))
        # Every one of those subsets came from the same cached complete snapshot.
        self.assertTrue(requested, "the route should still attempt its read-through")
        for url in requested:
            self.assertEqual(urlparse(url).path, "/api/arguments")
            self.assertNotIn("kind=", urlparse(url).query, url)

    def test_cached_argument_index_searches_locally(self):
        server, page, context, _collector = self._start()

        _wait_sw_active(page)
        _open_argument_index(page)
        _wait_content_contains(page, ARGUMENT_A_NAME)
        _wait_list_cached(page, "arguments:index")

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_content_contains(page, ARGUMENT_A_NAME)
        _wait_offline_banner(page)

        seen = self._record_calls(page, "**/api/arguments**")
        search = page.locator("#prks-argument-search")
        # By name ...
        search.fill(ARGUMENT_C_NAME)
        page.wait_for_function(
            "n => { const r = document.querySelectorAll('.prks-research-row__title');"
            " return r.length === 1 && r[0].textContent.indexOf(n) !== -1; }",
            arg=ARGUMENT_C_NAME,
        )
        # ... by kind ...
        search.fill("stance")
        page.wait_for_function(
            "n => { const r = document.querySelectorAll('.prks-research-row__title');"
            " return r.length === 1 && r[0].textContent.indexOf(n) !== -1; }",
            arg=STANCE_NAME,
        )
        # ... by target name ...
        search.fill(POSITION_A_NAME)
        page.wait_for_function(
            "() => document.querySelectorAll('.prks-research-row__title').length >= 2"
        )
        # ... and by source Work title.
        search.fill(WORK_A_TITLE)
        page.wait_for_function(
            "n => { const r = document.querySelectorAll('.prks-research-row__title');"
            " return r.length === 1 && r[0].textContent.indexOf(n) !== -1; }",
            arg=ARGUMENT_A_NAME,
        )
        search.fill("no such argument anywhere")
        page.locator(".prks-research-index__empty", has_text="match").wait_for()
        self.assertEqual(seen, [], "offline Argument search must issue zero API requests")

        self.assertTrue(page.locator("#prks-argument-new").is_disabled())
        self.assertTrue(page.locator("#prks-stance-new").is_disabled())

    def test_uncached_argument_index_offline_is_explicitly_unavailable(self):
        server, page, context, _collector = self._start()

        _wait_sw_active(page)
        _open_argument_index(page)
        _wait_content_contains(page, ARGUMENT_A_NAME)
        _wait_list_cached(page, "arguments:index")
        page.evaluate("() => { void window.prksNavigate('#/folders'); }")
        page.wait_for_function("() => location.hash === '#/folders'")
        _clear_cached_list(page, "arguments:index")
        _wait_list_uncached(page, "arguments:index")

        context.set_offline(True)
        _open_argument_index(page)
        _wait_offline_unavailable(page)
        body = _content_text(page)
        self.assertIn("not available offline", body)
        self.assertNotIn("No Arguments or Stances yet.", body)
        self.assertEqual(page.locator("#prks-argument-new").count(), 0)

    def test_legitimately_empty_kind_subset_is_not_the_uncached_state(self):
        """A cached complete list with zero Stances still shows the ordinary
        "No Stances yet." empty state -- with creation disabled offline."""
        server = AppServer(seed_fn=seed_positions_library)  # Arguments, no Stances
        self.addCleanup(server.stop)
        server.start()
        page, context, _collector = open_app_page(_BROWSER, server.origin, service_workers="allow")
        self.addCleanup(context.close)

        _wait_sw_active(page)
        _open_argument_index(page)
        _wait_list_cached(page, "arguments:index")
        cached = _cached_list(page, "arguments:index")["value"]
        self.assertTrue(cached, "fixture should seed at least one Argument")
        self.assertEqual([row for row in cached if row["kind"] == "stance"], [])

        context.set_offline(True)
        _open_argument_index(page, "stance")
        _wait_content_contains(page, "No Stances yet.")
        body = _content_text(page)
        self.assertNotIn("not available offline", body)
        page.wait_for_function(
            "() => !!document.querySelector('#prks-stance-new-empty[disabled]')", timeout=20000
        )

    # ---- cached detail ------------------------------------------------------

    def test_cached_argument_detail_renders_offline(self):
        server, page, context, _collector = self._start()
        argument_a = server.ids["argument_a"]

        _wait_sw_active(page)
        _open_argument(page, argument_a)
        _wait_content_contains(page, ARGUMENT_A_NAME)
        _wait_entity_cached(page, "argument", argument_a)

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_content_contains(page, ARGUMENT_A_NAME)
        _wait_offline_banner(page)
        body = _content_text(page)
        self.assertIn("Argument", body)
        self.assertIn(ARGUMENT_A_TEXT, body)
        self.assertIn(POSITION_A_NAME, body)          # Position target
        self.assertIn(ARGUMENT_B_NAME, body)          # Argument target
        self.assertIn(WORK_A_TITLE, body)             # source Work
        self.assertIn("E2E Author", body)             # source Work author
        self.assertIn(ARGUMENT_SOURCE_PAGES, body)    # source pages
        self.assertIn(ARGUMENT_C_NAME, body)          # incoming response
        self.assertIn(WORK_B_TITLE, body)             # note mention backlink
        self.assertIn("Supports", body)               # verdict labels
        self.assertIn("Opposes", body)

    def test_cached_stance_detail_renders_offline(self):
        """A Stance is an Argument with kind 'stance'; the route and domain must
        not be accidentally Argument-only."""
        server, page, context, _collector = self._start()
        stance = server.ids["stance"]

        _wait_sw_active(page)
        _open_argument(page, stance)
        _wait_content_contains(page, STANCE_NAME)
        _wait_entity_cached(page, "argument", stance)

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_content_contains(page, STANCE_NAME)
        _wait_offline_banner(page)
        body = _content_text(page)
        self.assertIn("Stance", body)
        self.assertIn(STANCE_TEXT, body)
        self.assertIn(POSITION_A_NAME, body)
        self.assertIn("Holds", body)

    def test_cached_index_does_not_prefetch_every_argument_detail(self):
        server, page, context, _collector = self._start()
        argument_a = server.ids["argument_a"]
        unvisited = server.ids["argument_unvisited"]

        _wait_sw_active(page)
        _open_argument_index(page)
        _wait_list_cached(page, "arguments:index")
        _open_argument(page, argument_a)
        _wait_entity_cached(page, "argument", argument_a)
        _open_argument_index(page)
        _wait_content_contains(page, ARGUMENT_UNVISITED_NAME)
        self.assertIsNone(_cached_entity(page, "argument", unvisited))

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        _wait_content_contains(page, ARGUMENT_UNVISITED_NAME)
        page.locator('.prks-research-row[href$="%s"]' % unvisited).click()
        _wait_offline_unavailable(page)
        body = _content_text(page)
        self.assertIn("not available offline", body)
        self.assertNotIn("Argument not found", body)
        _wait_entity_cached(page, "argument", argument_a)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _open_argument(page, argument_a)
        _wait_content_contains(page, ARGUMENT_A_NAME)

    def test_cached_argument_relationships_navigate_offline(self):
        """Relationship links are ordinary PRKS links: each destination decides
        for itself whether it has cached data. No offline-specific router."""
        server, page, context, _collector = self._start()
        argument_a = server.ids["argument_a"]
        target = server.ids["argument_target"]
        position_a = server.ids["position_a"]
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        for arg_id in (argument_a, target):
            _open_argument(page, arg_id)
            _wait_entity_cached(page, "argument", arg_id)
        _open_position(page, position_a)
        _wait_entity_cached(page, "position", position_a)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)

        context.set_offline(True)
        _open_argument(page, argument_a)
        _wait_content_contains(page, ARGUMENT_A_NAME)
        _wait_offline_banner(page)

        # ... to a cached Argument target
        page.locator('.prks-research-row[href$="%s"]' % target).click()
        page.wait_for_function("id => decodeURIComponent(location.hash).indexOf(id) !== -1", arg=target)
        _wait_content_contains(page, ARGUMENT_B_NAME)
        # ... back, then to a cached Position target
        _open_argument(page, argument_a)
        _wait_offline_banner(page)
        page.locator('.prks-research-row[href$="%s"]' % position_a).click()
        page.wait_for_function("id => decodeURIComponent(location.hash).indexOf(id) !== -1", arg=position_a)
        _wait_content_contains(page, POSITION_A_NAME)
        # ... and out to a cached source Work through the existing Work route.
        _open_argument(page, argument_a)
        _wait_offline_banner(page)
        page.locator('.prks-research-row[href$="%s"]' % work_a).click()
        page.wait_for_function("id => decodeURIComponent(location.hash).indexOf(id) !== -1", arg=work_a)
        page.wait_for_function("t => document.body.innerText.indexOf(t) !== -1", arg=WORK_A_TITLE)

        # An UNCACHED destination gives that destination's own offline state.
        _open_argument(page, argument_a)
        _wait_offline_banner(page)
        page.locator('.prks-research-row[href$="%s"]' % server.ids["argument_response"]).click()
        _wait_offline_unavailable(page)
        self.assertIn("not available offline", _content_text(page))

    def test_argument_graph_action_requires_a_connection_offline(self):
        server, page, context, _collector = self._start()
        argument_a = server.ids["argument_a"]

        _wait_sw_active(page)
        _open_argument(page, argument_a)
        _wait_content_contains(page, ARGUMENT_A_NAME)
        _wait_entity_cached(page, "argument", argument_a)
        self.assertFalse(page.locator("#prks-arg-view-graph").is_disabled())

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_offline_banner(page)
        page.wait_for_function(
            "() => !!document.querySelector('#prks-arg-view-graph[disabled]')", timeout=20000
        )
        page.locator("#prks-arg-view-graph").click(force=True)
        page.wait_for_timeout(300)
        self.assertNotIn("/graph", page.evaluate("() => location.hash"))

        context.set_offline(False)
        page.evaluate("""async () => { try { await window.prksRequest('/api/settings'); } catch (_e) {} }""")
        page.wait_for_function(
            "() => !document.querySelector('#prks-arg-view-graph[disabled]')", timeout=20000
        )

    # ---- mutation blocking --------------------------------------------------

    def test_offline_argument_index_and_detail_cannot_mutate(self):
        server, page, context, _collector = self._start()
        argument_a = server.ids["argument_a"]

        _wait_sw_active(page)
        _open_argument_index(page)
        _wait_list_cached(page, "arguments:index")
        _open_argument(page, argument_a)
        _wait_entity_cached(page, "argument", argument_a)

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_content_contains(page, ARGUMENT_A_NAME)
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
            timeout=20000,
        )

        mutations = []

        def record_mutation(route):
            if route.request.method in ("POST", "PATCH", "PUT", "DELETE"):
                mutations.append((route.request.method, urlparse(route.request.url).path))
            route.fallback()

        page.route("**/api/arguments**", record_mutation)
        try:
            for selector in ("#prks-arg-edit", "#prks-arg-response", "#prks-arg-delete"):
                btn = page.locator(selector)
                self.assertTrue(btn.is_disabled(), "%s must be disabled offline" % selector)
                self.assertEqual(btn.get_attribute("aria-disabled"), "true", selector)
                btn.click(force=True)
                page.wait_for_timeout(150)
            # Edit must not have opened a form the user could never submit.
            self.assertEqual(page.locator("#prks-arg-form").count(), 0)

            _open_argument_index(page)
            _wait_content_contains(page, ARGUMENT_A_NAME)
            for selector in ("#prks-argument-new", "#prks-stance-new"):
                btn = page.locator(selector)
                self.assertTrue(btn.is_disabled(), selector)
                btn.click(force=True)
                page.wait_for_timeout(150)
            self.assertEqual(page.locator("#prks-modal-confirm .prks-modal-prompt__input").count(), 0)

            # The Work Research Notes creation path is a second mutation surface.
            page.evaluate(
                """async () => {
                    try { await window.prksCreateArgumentFromWork({ name: 'Offline argument' }); } catch (_e) {}
                }"""
            )
            page.wait_for_timeout(300)
            self.assertEqual(mutations, [])
        finally:
            _safe_unroute(page, "**/api/arguments**", record_mutation)

    def test_disconnect_while_argument_prompt_open_blocks_the_create(self):
        server, page, context, _collector = self._start()

        _wait_sw_active(page)
        _open_argument_index(page)
        _wait_content_contains(page, ARGUMENT_A_NAME)
        page.locator("#prks-argument-new").click()
        prompt_input = page.locator("#prks-modal-confirm .prks-modal-prompt__input")
        prompt_input.wait_for()
        prompt_input.fill("Created while disconnected")

        mutations = []

        def block_api(route):
            if route.request.method in ("POST", "PATCH", "PUT", "DELETE"):
                mutations.append((route.request.method, urlparse(route.request.url).path))
            route.abort("connectionrefused")

        page.route("**/api/**", block_api)
        try:
            page.evaluate("""async () => { try { await window.prksRequest('/api/settings'); } catch (_e) {} }""")
            page.wait_for_function(
                "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
                timeout=20000,
            )
            page.locator("#prks-modal-confirm-ok").click()
            page.wait_for_timeout(500)
            self.assertEqual(mutations, [], "no Argument create may be attempted after disconnect")
        finally:
            _safe_unroute(page, "**/api/**", block_api)

        page.evaluate("""async () => { try { await window.prksRequest('/api/settings'); } catch (_e) {} }""")
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'online'",
            timeout=20000,
        )
        names = page.evaluate("() => fetchArguments().then(items => items.map(a => a.name))")
        self.assertNotIn("Created while disconnected", names)

    def test_open_edit_form_survives_disconnect_without_losing_the_draft(self):
        """An editor mounted online keeps its unsaved values when PRKS stops
        answering; only the controls that could submit them go inert."""
        server, page, _context, _collector = self._start()
        argument_a = server.ids["argument_a"]

        _wait_sw_active(page)
        _open_argument(page, argument_a)
        _wait_content_contains(page, ARGUMENT_A_NAME)
        page.locator("#prks-arg-edit").click()
        page.locator("#prks-arg-form").wait_for()
        draft = "Draft written before the connection dropped"
        page.locator("#prks-arg-name").fill(draft)

        mutations = []

        def block_api(route):
            if route.request.method in ("POST", "PATCH", "PUT", "DELETE"):
                mutations.append((route.request.method, urlparse(route.request.url).path))
            route.abort("connectionrefused")

        page.route("**/api/**", block_api)
        try:
            page.evaluate("""async () => { try { await window.prksRequest('/api/settings'); } catch (_e) {} }""")
            page.wait_for_function(
                "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
                timeout=20000,
            )
            # The draft is still there ...
            page.wait_for_function(
                "() => !!document.querySelector('#prks-arg-name[disabled]')", timeout=20000
            )
            self.assertEqual(page.locator("#prks-arg-name").input_value(), draft)
            self.assertTrue(page.locator("#prks-arg-text").is_disabled())
            self.assertTrue(page.locator("#prks-arg-kind").is_disabled())
            self.assertTrue(page.locator("#prks-arg-add-target").is_disabled())
            # ... Cancel stays usable so the user can leave edit mode ...
            self.assertFalse(page.locator("#prks-arg-cancel").is_disabled())
            # ... and Save cannot reach the network.
            page.locator("#prks-arg-form button[type=submit]").click(force=True)
            page.wait_for_timeout(400)
            self.assertEqual(mutations, [])
        finally:
            _safe_unroute(page, "**/api/**", block_api)

        page.evaluate("""async () => { await window.prksRequest('/api/settings'); }""")
        page.wait_for_function(
            "() => !document.querySelector('#prks-arg-name[disabled]')", timeout=20000
        )
        self.assertEqual(page.locator("#prks-arg-name").input_value(), draft)
        self.assertFalse(page.locator("#prks-arg-form button[type=submit]").is_disabled())

    def test_fresh_offline_route_renders_read_mode(self):
        """A cached detail mounted while already offline starts read-only rather
        than inheriting a stale edit session."""
        server, page, context, _collector = self._start()
        argument_a = server.ids["argument_a"]

        _wait_sw_active(page)
        _open_argument(page, argument_a)
        _wait_entity_cached(page, "argument", argument_a)
        page.locator("#prks-arg-edit").click()
        page.locator("#prks-arg-form").wait_for()

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_content_contains(page, ARGUMENT_A_NAME)
        _wait_offline_banner(page)
        self.assertEqual(page.locator("#prks-arg-form").count(), 0)
        self.assertEqual(page.locator("#prks-arg-edit").count(), 1)

    def test_live_argument_pages_react_to_connectivity(self):
        server, page, _context, _collector = self._start()
        argument_a = server.ids["argument_a"]

        _wait_sw_active(page)
        _open_argument_index(page)
        _wait_content_contains(page, ARGUMENT_A_NAME)
        self.assertFalse(page.locator("#prks-argument-new").is_disabled())

        def abort_api(route):
            route.abort("connectionrefused")

        page.route("**/api/**", abort_api)
        try:
            page.evaluate("""async () => { try { await window.prksRequest('/api/settings'); } catch (_e) {} }""")
            page.wait_for_function(
                "() => !!document.querySelector('#prks-argument-new[disabled]')", timeout=20000
            )
            self.assertTrue(page.evaluate("() => navigator.onLine"))
            self.assertTrue(page.locator("#prks-stance-new").is_disabled())
        finally:
            _safe_unroute(page, "**/api/**", abort_api)

        page.evaluate("""async () => { await window.prksRequest('/api/settings'); }""")
        page.wait_for_function(
            "() => !document.querySelector('#prks-argument-new[disabled]')", timeout=20000
        )

        _open_argument(page, argument_a)
        _wait_content_contains(page, ARGUMENT_A_NAME)
        _wait_entity_cached(page, "argument", argument_a)
        page.route("**/api/**", abort_api)
        try:
            page.evaluate("""async () => { try { await window.prksRequest('/api/settings'); } catch (_e) {} }""")
            page.wait_for_function(
                "() => !!document.querySelector('#prks-arg-edit[disabled]')", timeout=20000
            )
            for selector in ("#prks-arg-response", "#prks-arg-delete", "#prks-arg-view-graph"):
                self.assertTrue(page.locator(selector).is_disabled(), selector)
            # Read-only content and relationship links stay usable.
            body = _content_text(page)
            self.assertIn(ARGUMENT_A_TEXT, body)
            self.assertIn(POSITION_A_NAME, body)
            self.assertGreaterEqual(page.locator(".prks-research-row").count(), 3)
        finally:
            _safe_unroute(page, "**/api/**", abort_api)

        page.evaluate("""async () => { await window.prksRequest('/api/settings'); }""")
        page.wait_for_function(
            "() => !document.querySelector('#prks-arg-edit[disabled]')", timeout=20000
        )

    def test_argument_shape_validators_reject_unusable_rows(self):
        """The validators gate cache publication, so they are checked directly
        against the shapes a server could actually hand back."""
        server, page, _context, _collector = self._start()
        _wait_sw_active(page)
        _open_argument_index(page)
        _wait_content_contains(page, ARGUMENT_A_NAME)

        index_cases = page.evaluate(
            """() => {
                const ok = { id: 'A-1', kind: 'argument', targets: [], sources: [] };
                return {
                    empty: prksIsArgumentIndexShape([]),
                    ok: prksIsArgumentIndexShape([ok]),
                    stanceOk: prksIsArgumentIndexShape([{ id: 'A-2', kind: 'stance', targets: [], sources: [] }]),
                    notArray: prksIsArgumentIndexShape({ error: 'boom' }),
                    nullValue: prksIsArgumentIndexShape(null),
                    missingId: prksIsArgumentIndexShape([{ kind: 'argument', targets: [], sources: [] }]),
                    blankId: prksIsArgumentIndexShape([{ id: '  ', kind: 'argument', targets: [], sources: [] }]),
                    badKind: prksIsArgumentIndexShape([{ id: 'A-1', kind: 'claim', targets: [], sources: [] }]),
                    missingKind: prksIsArgumentIndexShape([{ id: 'A-1', targets: [], sources: [] }]),
                    targetsNotArray: prksIsArgumentIndexShape([{ id: 'A-1', kind: 'argument', targets: {}, sources: [] }]),
                    sourcesNotArray: prksIsArgumentIndexShape([{ id: 'A-1', kind: 'argument', targets: [], sources: {} }]),
                    targetMissingId: prksIsArgumentIndexShape([
                        { id: 'A-1', kind: 'argument', targets: [{ type: 'position' }], sources: [] }]),
                    targetMissingType: prksIsArgumentIndexShape([
                        { id: 'A-1', kind: 'argument', targets: [{ id: 'P-1' }], sources: [] }]),
                    targetBadType: prksIsArgumentIndexShape([
                        { id: 'A-1', kind: 'argument', targets: [{ id: 'P-1', type: 'concept' }], sources: [] }]),
                    sourceMissingWorkId: prksIsArgumentIndexShape([
                        { id: 'A-1', kind: 'argument', targets: [], sources: [{ authors: [] }] }]),
                    sourceAuthorsNotArray: prksIsArgumentIndexShape([
                        { id: 'A-1', kind: 'argument', targets: [], sources: [{ work_id: 'W-1' }] }]),
                    authorNull: prksIsArgumentIndexShape([
                        { id: 'A-1', kind: 'argument', targets: [],
                          sources: [{ work_id: 'W-1', authors: [null] }] }]),
                    authorString: prksIsArgumentIndexShape([
                        { id: 'A-1', kind: 'argument', targets: [],
                          sources: [{ work_id: 'W-1', authors: ['bad'] }] }]),
                    authorMissingId: prksIsArgumentIndexShape([
                        { id: 'A-1', kind: 'argument', targets: [],
                          sources: [{ work_id: 'W-1', authors: [{}] }] }]),
                    authorMinimalOk: prksIsArgumentIndexShape([
                        { id: 'A-1', kind: 'argument', targets: [],
                          sources: [{ work_id: 'W-1', authors: [{ id: 'P-1' }] }] }]),
                    optionalDisplayFieldsAbsent: prksIsArgumentIndexShape([
                        { id: 'A-1', kind: 'argument',
                          targets: [{ id: 'P-1', type: 'position' }],
                          sources: [{ work_id: 'W-1', authors: [] }] }]),
                };
            }"""
        )
        self.assertEqual(
            index_cases,
            {
                "empty": True,
                "ok": True,
                "stanceOk": True,
                "notArray": False,
                "nullValue": False,
                "missingId": False,
                "blankId": False,
                "badKind": False,
                "missingKind": False,
                "targetsNotArray": False,
                "sourcesNotArray": False,
                "targetMissingId": False,
                "targetMissingType": False,
                "targetBadType": False,
                "sourceMissingWorkId": False,
                "sourceAuthorsNotArray": False,
                "authorNull": False,
                "authorString": False,
                "authorMissingId": False,
                "authorMinimalOk": True,
                "optionalDisplayFieldsAbsent": True,
            },
        )

        detail_cases = page.evaluate(
            """() => {
                const base = () => ({ id: 'A-1', kind: 'argument', targets: [], sources: [],
                                      responses: [], mentions: [], verdicts: [] });
                const withField = (k, v) => { const o = base(); o[k] = v; return o; };
                return {
                    ok: prksIsArgumentShape(base(), 'A-1'),
                    stanceOk: prksIsArgumentShape(withField('kind', 'stance'), 'A-1'),
                    wrongId: prksIsArgumentShape(base(), 'A-2'),
                    badKind: prksIsArgumentShape(withField('kind', 'claim'), 'A-1'),
                    targetsNotArray: prksIsArgumentShape(withField('targets', {}), 'A-1'),
                    sourcesNotArray: prksIsArgumentShape(withField('sources', null), 'A-1'),
                    responsesNotArray: prksIsArgumentShape(withField('responses', {}), 'A-1'),
                    mentionsNotArray: prksIsArgumentShape(withField('mentions', 'x'), 'A-1'),
                    verdictsNotArray: prksIsArgumentShape(withField('verdicts', {}), 'A-1'),
                    targetMissingId: prksIsArgumentShape(
                        withField('targets', [{ type: 'argument' }]), 'A-1'),
                    sourceMissingWorkId: prksIsArgumentShape(
                        withField('sources', [{ authors: [] }]), 'A-1'),
                    authorNull: prksIsArgumentShape(
                        withField('sources', [{ work_id: 'W-1', authors: [null] }]), 'A-1'),
                    authorString: prksIsArgumentShape(
                        withField('sources', [{ work_id: 'W-1', authors: ['bad'] }]), 'A-1'),
                    authorMissingId: prksIsArgumentShape(
                        withField('sources', [{ work_id: 'W-1', authors: [{}] }]), 'A-1'),
                    authorMinimalOk: prksIsArgumentShape(
                        withField('sources', [{ work_id: 'W-1', authors: [{ id: 'P-1' }] }]), 'A-1'),
                    blankId: prksIsArgumentShape(
                        { id: '   ', kind: 'argument', targets: [], sources: [],
                          responses: [], mentions: [], verdicts: [] }, '   '),
                    responseMissingId: prksIsArgumentShape(
                        withField('responses', [{ name: 'x' }]), 'A-1'),
                    responseBadKind: prksIsArgumentShape(
                        withField('responses', [{ id: 'A-9', kind: 'claim' }]), 'A-1'),
                    mentionMissingWorkId: prksIsArgumentShape(
                        withField('mentions', [{ title: 'x' }]), 'A-1'),
                    verdictMissingId: prksIsArgumentShape(
                        withField('verdicts', [{ label: 'Supports' }]), 'A-1'),
                    errorBody: prksIsArgumentShape({ error: 'boom' }, 'A-1'),
                    arrayBody: prksIsArgumentShape([], 'A-1'),
                    optionalDisplayFieldsAbsent: prksIsArgumentShape({
                        id: 'A-1', kind: 'argument',
                        targets: [{ id: 'A-9', type: 'argument' }],
                        sources: [{ work_id: 'W-1', authors: [] }],
                        responses: [{ id: 'A-8' }],
                        mentions: [{ work_id: 'W-2' }],
                        verdicts: [{ id: 'supports' }],
                    }, 'A-1'),
                    authorDisplayFieldsAbsent: prksIsArgumentShape(
                        withField('sources', [{ work_id: 'W-1', authors: [
                            { id: 'P-1', first_name: '', last_name: '', credit_name: null },
                        ] }]), 'A-1'),
                };
            }"""
        )
        self.assertEqual(
            detail_cases,
            {
                "ok": True,
                "stanceOk": True,
                "wrongId": False,
                "badKind": False,
                "targetsNotArray": False,
                "sourcesNotArray": False,
                "responsesNotArray": False,
                "mentionsNotArray": False,
                "verdictsNotArray": False,
                "targetMissingId": False,
                "sourceMissingWorkId": False,
                "authorNull": False,
                "authorString": False,
                "authorMissingId": False,
                "authorMinimalOk": True,
                "blankId": False,
                "responseMissingId": False,
                "responseBadKind": False,
                "mentionMissingWorkId": False,
                "verdictMissingId": False,
                "errorBody": False,
                "arrayBody": False,
                "optionalDisplayFieldsAbsent": True,
                "authorDisplayFieldsAbsent": True,
            },
        )

    def test_malformed_author_row_in_a_200_never_replaces_a_good_cache(self):
        """A reachable server answering 200 with an unusable author row is a
        route error. Those rows are walked to build the author label, so letting
        one into the cache would turn a bad response into a later crash."""
        server, page, context, _collector = self._start()
        argument_a = server.ids["argument_a"]
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_argument(page, argument_a)
        _wait_content_contains(page, ARGUMENT_A_NAME)
        _wait_entity_cached(page, "argument", argument_a)
        good = _cached_entity(page, "argument", argument_a)
        self.assertTrue(good["value"]["sources"][0]["authors"], "fixture should seed an author")

        malformed = json.dumps(
            {
                "id": argument_a,
                "name": ARGUMENT_A_NAME,
                "kind": "argument",
                "main_text": "",
                "targets": [],
                "sources": [{"work_id": work_a, "work_title": "T", "pages": "", "authors": [None]}],
                "responses": [],
                "mentions": [],
                "verdicts": [{"id": "supports", "label": "Supports"}],
            }
        )

        def bad_authors(route):
            req = route.request
            if req.method == "GET" and urlparse(req.url).path == "/api/arguments/" + argument_a:
                route.fulfill(status=200, content_type="application/json", body=malformed)
                return
            route.fallback()

        page.route("**/api/arguments/**", bad_authors)
        try:
            _open_argument_index(page)
            _wait_content_contains(page, ARGUMENT_A_NAME)
            _open_argument(page, argument_a)
            page.wait_for_function(
                "() => document.querySelector('#prks-route-retry') !== null", timeout=15000
            )
            body = _content_text(page)
            self.assertNotIn("Argument not found", body)
            self.assertNotIn("not available offline", body)
            self.assertEqual(_connectivity_state(page), "online")
            page.wait_for_timeout(500)
            self.assertEqual(_cached_entity(page, "argument", argument_a)["value"], good["value"])
        finally:
            _safe_unroute(page, "**/api/arguments/**", bad_authors)

        # The untouched snapshot is still what serves offline.
        _open_argument_index(page)
        _wait_content_contains(page, ARGUMENT_A_NAME)
        context.set_offline(True)
        _open_argument(page, argument_a)
        _wait_offline_banner(page)
        _wait_content_contains(page, ARGUMENT_A_TEXT)

    def test_malformed_cached_author_row_is_discarded_not_rendered(self):
        """A cached entity that somehow holds an unusable author row must report
        offline-unavailable and be discarded -- never reach the renderer, which
        walks every author row to build its label."""
        server, page, context, _collector = self._start()
        argument_a = server.ids["argument_a"]

        _wait_sw_active(page)
        _open_argument(page, argument_a)
        _wait_content_contains(page, ARGUMENT_A_NAME)
        _wait_entity_cached(page, "argument", argument_a)

        # Corrupt the cached snapshot in place, the way a schema drift or a
        # partially written row could.
        page.evaluate(
            """async (id) => {
                const store = window.createPrksOfflineStore();
                const row = await store.getEntity('argument', id);
                const value = row.value;
                value.sources = [{ work_id: 'W-1', work_title: 'T', pages: '', authors: [null] }];
                await store.putEntity('argument', id, value, '');
            }""",
            argument_a,
        )
        page.wait_for_function(
            """(id) => window.createPrksOfflineStore().getEntity('argument', id)
                .then(row => !!row && row.value.sources[0].authors[0] === null)""",
            arg=argument_a,
            timeout=15000,
        )

        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_offline_unavailable(page)
        body = _content_text(page)
        self.assertIn("not available offline", body)
        self.assertNotIn("Argument not found", body)
        self.assertEqual(errors, [], "a malformed cached row must not reach the renderer")
        # ... and the unusable snapshot is discarded rather than left to fail again.
        _wait_entity_uncached(page, "argument", argument_a)

    # ---- HTTP errors --------------------------------------------------------

    def test_argument_detail_http_errors_keep_their_normal_meaning(self):
        server, page, context, _collector = self._start()
        argument_a = server.ids["argument_a"]

        _wait_sw_active(page)
        _open_argument(page, argument_a)
        _wait_content_contains(page, ARGUMENT_A_NAME)
        _wait_entity_cached(page, "argument", argument_a)
        good = _cached_entity(page, "argument", argument_a)

        _open_argument(page, "A-DOES-NOT-EXIST")
        _wait_content_contains(page, "Argument not found")
        self.assertEqual(_connectivity_state(page), "online")

        mode = {"status": 500}

        def broken(route):
            req = route.request
            if req.method == "GET" and urlparse(req.url).path == "/api/arguments/" + argument_a:
                if mode["status"] == 500:
                    route.fulfill(status=500, content_type="application/json", body='{"error":"boom"}')
                    return
                if mode["status"] == 200:
                    route.fulfill(
                        status=200, content_type="application/json", body='{"error":"unexpected shape"}'
                    )
                    return
            route.fallback()

        page.route("**/api/arguments/**", broken)
        try:
            _open_argument(page, argument_a)
            page.wait_for_function(
                "() => document.querySelector('#prks-route-retry') !== null", timeout=15000
            )
            body = _content_text(page)
            self.assertNotIn("Argument not found", body)
            self.assertNotIn("not available offline", body)
            self.assertEqual(_connectivity_state(page), "online")

            mode["status"] = 200
            _open_argument_index(page)
            _wait_content_contains(page, ARGUMENT_A_NAME)
            _open_argument(page, argument_a)
            page.wait_for_function(
                "() => document.querySelector('#prks-route-retry') !== null", timeout=15000
            )
            page.wait_for_timeout(500)
            self.assertEqual(_cached_entity(page, "argument", argument_a)["value"], good["value"])
            mode["status"] = 0
            _safe_unroute(page, "**/api/arguments/**", broken)
        except Exception:
            _safe_unroute(page, "**/api/arguments/**", broken)
            raise

        _open_argument_index(page)
        _wait_content_contains(page, ARGUMENT_A_NAME)
        context.set_offline(True)
        _open_argument(page, argument_a)
        _wait_offline_banner(page)
        _wait_content_contains(page, ARGUMENT_A_TEXT)
        _open_argument(page, server.ids["argument_unvisited"])
        _wait_offline_unavailable(page)


class OfflineArgumentCoherenceTests(unittest.TestCase):
    """The Arguments read model depends on five other canonical record families,
    so its coherence hooks get their own suite."""

    def _start(self):
        server = AppServer(seed_fn=seed_arguments_library)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin, service_workers="allow")
        self.addCleanup(context.close)
        return server, page, context, collector

    def _cache_arguments(self, page, server):
        """Caches the complete index plus Argument A's detail."""
        _wait_sw_active(page)
        _open_argument_index(page)
        _wait_list_cached(page, "arguments:index")
        _open_argument(page, server.ids["argument_a"])
        _wait_entity_cached(page, "argument", server.ids["argument_a"])

    def _assert_arguments_invalidated(self, page, server, generation_before):
        # Canonical mutations driven through the UI resolve asynchronously, so
        # wait for the generation to advance rather than sampling it.
        page.wait_for_function(
            "n => (typeof prksOfflineDomainGeneration === 'function'"
            " ? prksOfflineDomainGeneration('arguments') : 0) > n",
            arg=generation_before,
            timeout=20000,
        )
        _wait_entity_uncached(page, "argument", server.ids["argument_a"])
        _wait_list_uncached(page, "arguments:index")

    # ---- direct Argument mutations -----------------------------------------

    def test_argument_create_update_and_delete_invalidate_arguments(self):
        server, page, _context, _collector = self._start()
        argument_a = server.ids["argument_a"]

        self._cache_arguments(page, server)
        before = _domain_generation(page, "arguments")
        page.evaluate("() => window.createArgument({ name: 'Created Argument', kind: 'argument' })")
        self._assert_arguments_invalidated(page, server, before)

        self._cache_arguments(page, server)
        before = _domain_generation(page, "arguments")
        # B may embed A as a target or a response, so the whole domain goes.
        page.evaluate("id => window.updateArgument(id, { name: 'Renamed Argument', kind: 'stance' })", argument_a)
        self._assert_arguments_invalidated(page, server, before)

        self._cache_arguments(page, server)
        before = _domain_generation(page, "arguments")
        page.evaluate("id => window.deleteArgument(id)", server.ids["argument_unvisited"])
        self._assert_arguments_invalidated(page, server, before)

    def test_failed_argument_mutations_retain_the_cache(self):
        server, page, _context, _collector = self._start()
        argument_a = server.ids["argument_a"]

        self._cache_arguments(page, server)
        before = _domain_generation(page, "arguments")

        def reject(route):
            if route.request.method in ("POST", "PATCH", "PUT", "DELETE"):
                route.fulfill(status=500, content_type="application/json", body='{"error":"nope"}')
                return
            route.fallback()

        page.route("**/api/arguments**", reject)
        try:
            page.evaluate(
                """async (id) => {
                    try { await window.updateArgument(id, { name: 'never applied' }); } catch (_e) {}
                    try { await window.createArgument({ name: 'never created', kind: 'argument' }); } catch (_e) {}
                    try { await window.deleteArgument(id); } catch (_e) {}
                    try { await window.putArgumentSources(id, []); } catch (_e) {}
                }""",
                argument_a,
            )
            page.wait_for_timeout(400)
            self.assertEqual(_domain_generation(page, "arguments"), before)
            self.assertIsNotNone(_cached_entity(page, "argument", argument_a))
        finally:
            _safe_unroute(page, "**/api/arguments**", reject)

    def test_partial_multi_request_save_keeps_arguments_invalidated(self):
        """updateArgument succeeded, putArgumentTargets failed: canonical success
        of the first request controls coherence, not the UI workflow's outcome."""
        server, page, _context, _collector = self._start()
        argument_a = server.ids["argument_a"]

        self._cache_arguments(page, server)
        before = _domain_generation(page, "arguments")

        def reject_targets(route):
            if route.request.method == "PUT" and urlparse(route.request.url).path.endswith("/targets"):
                route.fulfill(status=500, content_type="application/json", body='{"error":"nope"}')
                return
            route.fallback()

        page.route("**/api/arguments/**", reject_targets)
        try:
            outcome = page.evaluate(
                """async (id) => {
                    await window.updateArgument(id, { name: 'Partially saved', kind: 'argument' });
                    try {
                        await window.putArgumentTargets(id, []);
                        return 'targets-succeeded';
                    } catch (_e) {
                        return 'targets-failed';
                    }
                }""",
                argument_a,
            )
            self.assertEqual(outcome, "targets-failed")
            self._assert_arguments_invalidated(page, server, before)
        finally:
            _safe_unroute(page, "**/api/arguments/**", reject_targets)

    # ---- domain boundaries --------------------------------------------------

    def test_argument_sources_invalidate_arguments_but_not_positions(self):
        server, page, _context, _collector = self._start()
        argument_a = server.ids["argument_a"]
        position_a = server.ids["position_a"]
        concept_child = server.ids["concept_child"]

        self._cache_arguments(page, server)
        _open_position(page, position_a)
        _wait_entity_cached(page, "position", position_a)
        _open_concept(page, concept_child)
        _wait_entity_cached(page, "concept", concept_child)
        arguments_before = _domain_generation(page, "arguments")
        positions_before = _domain_generation(page, "positions")
        concepts_before = _domain_generation(page, "concepts")

        page.evaluate(
            "([id, workId]) => window.putArgumentSources(id, [{ work_id: workId, pages: '3-4' }])",
            [argument_a, server.ids["work_b"]],
        )
        self._assert_arguments_invalidated(page, server, arguments_before)
        # Source Works are not in the Position read model, and nothing here
        # touches Concepts at all.
        self.assertEqual(_domain_generation(page, "positions"), positions_before)
        self.assertEqual(_domain_generation(page, "concepts"), concepts_before)
        self.assertIsNotNone(_cached_entity(page, "position", position_a))
        self.assertIsNotNone(_cached_entity(page, "concept", concept_child))

    def test_argument_targets_invalidate_arguments_and_positions_only(self):
        server, page, _context, _collector = self._start()
        argument_a = server.ids["argument_a"]
        position_a = server.ids["position_a"]
        concept_child = server.ids["concept_child"]

        self._cache_arguments(page, server)
        _open_position(page, position_a)
        _wait_entity_cached(page, "position", position_a)
        _open_concept(page, concept_child)
        _wait_entity_cached(page, "concept", concept_child)
        arguments_before = _domain_generation(page, "arguments")
        positions_before = _domain_generation(page, "positions")
        concepts_before = _domain_generation(page, "concepts")

        page.evaluate(
            """([id, positionId]) => window.putArgumentTargets(id, [
                { type: 'position', id: positionId, verdict_id: 'qualifies' },
            ])""",
            [argument_a, position_a],
        )
        self._assert_arguments_invalidated(page, server, arguments_before)
        self.assertGreater(_domain_generation(page, "positions"), positions_before)
        _wait_entity_uncached(page, "position", position_a)
        self.assertEqual(_domain_generation(page, "concepts"), concepts_before)
        self.assertIsNotNone(_cached_entity(page, "concept", concept_child))

    def test_concept_mutation_leaves_arguments_and_positions_alone(self):
        server, page, _context, _collector = self._start()
        position_a = server.ids["position_a"]
        concept_child = server.ids["concept_child"]

        self._cache_arguments(page, server)
        _open_position(page, position_a)
        _wait_entity_cached(page, "position", position_a)
        _open_concept(page, concept_child)
        _wait_entity_cached(page, "concept", concept_child)
        arguments_before = _domain_generation(page, "arguments")
        positions_before = _domain_generation(page, "positions")

        page.evaluate("id => window.updateConcept(id, { description: 'Domain isolation check.' })", concept_child)
        _wait_entity_uncached(page, "concept", concept_child)
        self.assertEqual(_domain_generation(page, "arguments"), arguments_before)
        self.assertEqual(_domain_generation(page, "positions"), positions_before)
        self.assertIsNotNone(_cached_entity(page, "argument", server.ids["argument_a"]))
        self.assertIsNotNone(_cached_entity(page, "position", position_a))

    # ---- external dependencies ----------------------------------------------

    def test_position_rename_invalidates_arguments(self):
        server, page, _context, _collector = self._start()
        self._cache_arguments(page, server)
        before = _domain_generation(page, "arguments")
        # A cached Argument's targets embed the Position's name.
        page.evaluate(
            "id => window.updatePosition(id, { name: 'Renamed Target Position' })", server.ids["position_a"]
        )
        self._assert_arguments_invalidated(page, server, before)

        # A failed Position update retains the cache.
        self._cache_arguments(page, server)
        before = _domain_generation(page, "arguments")

        def reject(route):
            if route.request.method == "PATCH":
                route.fulfill(status=500, content_type="application/json", body='{"error":"nope"}')
                return
            route.fallback()

        page.route("**/api/positions/**", reject)
        try:
            page.evaluate(
                """async (id) => {
                    try { await window.updatePosition(id, { name: 'never applied' }); } catch (_e) {}
                }""",
                server.ids["position_a"],
            )
            page.wait_for_timeout(400)
            self.assertEqual(_domain_generation(page, "arguments"), before)
            self.assertIsNotNone(_cached_entity(page, "argument", server.ids["argument_a"]))
        finally:
            _safe_unroute(page, "**/api/positions/**", reject)

    def test_work_title_change_invalidates_arguments_and_concepts_but_not_positions(self):
        """A Work title appears in cached Argument sources/mentions and cached
        Concept mentions -- and in neither Position field."""
        server, page, _context, _collector = self._start()
        concept_child = server.ids["concept_child"]
        position_a = server.ids["position_a"]

        self._cache_arguments(page, server)
        _open_concept(page, concept_child)
        _wait_entity_cached(page, "concept", concept_child)
        _open_position(page, position_a)
        _wait_entity_cached(page, "position", position_a)
        arguments_before = _domain_generation(page, "arguments")
        concepts_before = _domain_generation(page, "concepts")
        positions_before = _domain_generation(page, "positions")

        _open_work_from_home(page, WORK_A_TITLE)
        page.locator("#panel-content button", has_text="Edit metadata").click()
        page.locator("#meta-title").fill("Argument Source Title Changed")
        page.locator("#inline-save-metadata-btn").click()
        page.locator("#panel-content .card-title", has_text="Argument Source Title Changed").wait_for(timeout=15000)

        self._assert_arguments_invalidated(page, server, arguments_before)
        self.assertGreater(_domain_generation(page, "concepts"), concepts_before)
        self.assertEqual(_domain_generation(page, "positions"), positions_before)
        self.assertIsNotNone(_cached_entity(page, "position", position_a))

    def test_playlist_inline_work_rename_invalidates_arguments(self):
        """The shared Work-title helper owns this dependency, so the Playlist
        rename surface gets it without its own hook."""
        server, page, _context, _collector = self._start()
        work_a = server.ids["work_a"]

        self._cache_arguments(page, server)
        before = _domain_generation(page, "arguments")

        playlist_id = page.evaluate(
            """async (workId) => {
                const id = await createPlaylist('E2E Argument Rename Playlist', '');
                await addWorkToPlaylist(id, workId);
                return id;
            }""",
            arg=work_a,
        )
        page.evaluate("id => window.prksNavigate('#/playlists/' + encodeURIComponent(id))", arg=playlist_id)
        page.wait_for_selector(".prks-playlist-detail")
        page.locator("#prks-playlist-edit-btn").click()
        page.wait_for_selector('[data-pl-rename="%s"]' % work_a)
        page.locator('[data-pl-rename="%s"]' % work_a).click()
        page.locator("#prks-pl-rename-input-" + work_a).fill("Renamed From The Playlist")
        page.locator('[data-pl-rename-save="%s"]' % work_a).click()
        page.wait_for_function(
            "t => document.body.innerText.indexOf(t) !== -1", arg="Renamed From The Playlist", timeout=15000
        )
        self._assert_arguments_invalidated(page, server, before)

    def test_research_notes_save_invalidates_arguments_and_concepts(self):
        server, page, _context, _collector = self._start()
        concept_child = server.ids["concept_child"]

        self._cache_arguments(page, server)
        _open_concept(page, concept_child)
        _wait_entity_cached(page, "concept", concept_child)
        arguments_before = _domain_generation(page, "arguments")
        concepts_before = _domain_generation(page, "concepts")

        _open_work_from_home(page, WORK_A_TITLE)
        page.locator(".work-notes-editor-wrap .CodeMirror").first.click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text("[[argument:%s|mention]] and a note line" % server.ids["argument_a"])
        page.locator('[data-prks-role="editor-status"]', has_text="All changes saved").wait_for(timeout=15000)

        self._assert_arguments_invalidated(page, server, arguments_before)
        self.assertGreater(_domain_generation(page, "concepts"), concepts_before)

    def test_superseded_notes_save_still_invalidates_arguments(self):
        server, page, _context, _collector = self._start()

        self._cache_arguments(page, server)
        _open_work_from_home(page, WORK_A_TITLE)
        page.locator(".work-notes-editor-wrap .CodeMirror").first.click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text("first save that really commits")
        page.locator('[data-prks-role="editor-status"]', has_text="All changes saved").wait_for(timeout=15000)
        after_first = _domain_generation(page, "arguments")
        self.assertGreaterEqual(after_first, 1)

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

        # Save #1 changed canonical mention data; #2 failing does not undo that.
        self.assertIsNone(_cached_entity(page, "argument", server.ids["argument_a"]))

    def test_work_deletion_invalidates_arguments(self):
        server, page, _context, _collector = self._start()

        self._cache_arguments(page, server)
        before = _domain_generation(page, "arguments")
        _open_work_from_home(page, WORK_A_TITLE)
        _open_details_drawer_if_tiled(page)
        advanced = page.locator(".work-details-advanced")
        if advanced.get_attribute("open") is None:
            advanced.locator("summary").click()
        page.locator(".delete-work-btn").click()
        page.locator("#prks-modal-confirm:not(.hidden)", has_text="Delete file?").wait_for()
        page.locator("#prks-modal-confirm-ok").click()
        page.wait_for_function("() => location.hash === '#/folders'", timeout=15000)
        self._assert_arguments_invalidated(page, server, before)

    def test_author_role_changes_invalidate_arguments(self):
        """Cached Argument sources carry each source Work's Author rows, so an
        Author link change stales them -- and other role types do not."""
        server, page, _context, _collector = self._start()
        work_a = server.ids["work_a"]
        person = server.ids["person"]

        # A non-Author role is not part of the Argument read model.
        self._cache_arguments(page, server)
        before = _domain_generation(page, "arguments")
        linked = page.evaluate(
            """async ([workId, personId]) => {
                const res = await window.prksRequest('/api/roles', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ person_id: personId, work_id: workId, role_type: 'Editor' }),
                });
                if (res.ok) window.prksMarkWorkAuthorDisplayChanged(workId, 'Editor');
                return res.ok;
            }""",
            [work_a, person],
        )
        self.assertTrue(linked)
        page.wait_for_timeout(300)
        self.assertEqual(
            _domain_generation(page, "arguments"),
            before,
            "a non-Author role is not part of the Argument read model",
        )
        self.assertIsNotNone(_cached_entity(page, "argument", server.ids["argument_a"]))

        # Removing the seeded Author link through the real Work-details UI must.
        before = _domain_generation(page, "arguments")
        _open_work_from_home(page, WORK_A_TITLE)
        _open_details_drawer_if_tiled(page)
        # The per-role unlink control only renders in the editable people mode.
        page.locator("#panel-content button", has_text="Manage relationships").click()
        unlink = page.locator(
            '.work-linked-persons__unlink[data-role-type="Author"][data-person-id="%s"]' % person
        )
        unlink.wait_for(timeout=15000)
        unlink.click()
        page.locator("#prks-modal-confirm:not(.hidden)").wait_for(timeout=15000)
        page.locator("#prks-modal-confirm-ok").click()
        self._assert_arguments_invalidated(page, server, before)

    def _save_person_profile(self, page, person_id, field, value):
        """Drives the real profile editor: open, change one field, Save."""
        page.evaluate("id => { void window.prksNavigate('#/people/' + id); }", person_id)
        page.wait_for_function("id => decodeURIComponent(location.hash).indexOf(id) !== -1", arg=person_id)
        _open_details_drawer_if_tiled(page)
        page.locator("#panel-content button", has_text="Edit profile").click()
        page.locator('.person-panel-edit[data-person-edit-id="%s"]' % person_id).wait_for()
        page.locator(field).fill(value)
        page.locator("#pd-save-btn").click()
        page.wait_for_selector(".person-panel-edit", state="detached", timeout=15000)

    def test_person_rename_invalidates_arguments_but_other_profile_edits_do_not(self):
        """Cached Argument sources show each author by canonical first/last name,
        so a rename stales them -- and nothing else on that form does."""
        server, page, _context, _collector = self._start()
        person = server.ids["person"]

        # A biography-only edit is not part of the Argument read model.
        self._cache_arguments(page, server)
        before = _domain_generation(page, "arguments")
        self._save_person_profile(page, person, "#pd-about", "A revised biography, same name.")
        page.wait_for_timeout(400)
        self.assertEqual(
            _domain_generation(page, "arguments"),
            before,
            "an edit that cannot change the displayed author must not cost the cache",
        )
        self.assertIsNotNone(_cached_entity(page, "argument", server.ids["argument_a"]))

        # A canonical-name change does stale it.
        before = _domain_generation(page, "arguments")
        self._save_person_profile(page, person, "#pd-first-name", "Renamed")
        self._assert_arguments_invalidated(page, server, before)


class OfflinePeopleTests(unittest.TestCase):
    """Phase 1 read-only People routes: #/people, #/people/role/:role, #/people/:id."""

    def _start(self, seed_fn=seed_people_library):
        server = AppServer(seed_fn=seed_fn)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin, service_workers="allow")
        self.addCleanup(context.close)
        return server, page, context, collector

    def _record_calls(self, page, pattern):
        seen = []

        def record(route):
            seen.append((route.request.method, urlparse(route.request.url).path))
            route.fallback()

        page.route(pattern, record)
        self.addCleanup(lambda: _safe_unroute(page, pattern, record))
        return seen

    def test_people_validator_matches_renderer_scalar_contract(self):
        server, page, _context, _collector = self._start()

        cases = page.evaluate(
            """() => {
                const scalarFields = [
                    'first_name', 'last_name', 'aliases', 'about', 'image_url',
                    'link_wikipedia', 'link_stanford_encyclopedia', 'link_iep',
                    'links_other', 'birth_date', 'death_date',
                ];
                const badValues = [{}, [], 3, true];
                const allowedValues = [undefined, null, '', 'value'];
                const detail = () => ({ id: 'P-1', works: [], groups: [] });
                const index = () => ({ id: 'P-1', assigned_roles: [], groups: [] });
                const withField = (base, field, value) => Object.assign(base, { [field]: value });
                const workWith = (field, value) => ({
                    id: 'P-1', works: [withField({ id: 'W-1' }, field, value)], groups: [],
                });
                const malformedExamples = {
                    first_name: {}, last_name: [], aliases: [], about: {}, image_url: [],
                    link_wikipedia: 7, link_stanford_encyclopedia: false, link_iep: {},
                    links_other: {}, birth_date: [], death_date: true,
                };
                return {
                    optionalStringContract:
                        prksIsOptionalString(undefined) && prksIsOptionalString(null) &&
                        prksIsOptionalString('') && prksIsOptionalString('value') &&
                        !prksIsOptionalString([]) && !prksIsOptionalString({}) &&
                        !prksIsOptionalString(3) && !prksIsOptionalString(true),
                    everyScalarRejectsEveryBadType: scalarFields.every((field) =>
                        badValues.every((value) =>
                            !prksIsPersonShape(withField(detail(), field, value), 'P-1') &&
                            !prksIsPeopleIndexShape([withField(index(), field, value)])
                        )
                    ),
                    everyScalarAcceptsOptionalStrings: scalarFields.every((field) =>
                        allowedValues.every((value) =>
                            prksIsPersonShape(withField(detail(), field, value), 'P-1') &&
                            prksIsPeopleIndexShape([withField(index(), field, value)])
                        )
                    ),
                    everyAuditedMalformedExampleRejected: Object.entries(malformedExamples).every(
                        ([field, value]) =>
                            !prksIsPersonShape(withField(detail(), field, value), 'P-1') &&
                            !prksIsPeopleIndexShape([withField(index(), field, value)])
                    ),
                    sparseDetailAccepted: prksIsPersonShape({
                        id: 'P-1', first_name: null, last_name: '', aliases: '', about: '',
                        image_url: null, link_wikipedia: null,
                        link_stanford_encyclopedia: null, link_iep: null, links_other: '',
                        birth_date: null, death_date: null, works: [], groups: [],
                    }, 'P-1'),
                    sparseIndexAccepted: prksIsPeopleIndexShape([{
                        id: 'P-1', first_name: null, last_name: '', aliases: '', about: '',
                        image_url: null, link_wikipedia: null,
                        link_stanford_encyclopedia: null, link_iep: null, links_other: '',
                        birth_date: null, death_date: null, assigned_roles: [], groups: [],
                    }]),
                    workScalarsRejectBadTypes: ['year', 'published_date'].every((field) =>
                        badValues.every((value) => !prksIsPersonShape(workWith(field, value), 'P-1'))
                    ),
                    workScalarsAcceptOptionalStrings: ['year', 'published_date'].every((field) =>
                        allowedValues.every((value) => prksIsPersonShape(workWith(field, value), 'P-1'))
                    ),
                };
            }"""
        )
        self.assertTrue(all(cases.values()), cases)

    # ---- one cached index, local role filtering -----------------------------

    def test_one_cached_index_serves_every_role_view_offline(self):
        """Visiting a role view online must cache the COMPLETE collection, so
        every other role view and the unfiltered list work offline from that one
        key. This is the regression test for the single-cache-key design."""
        server, page, context, _collector = self._start()

        _wait_sw_active(page)
        # Deliberately start from the most filtered route.
        _open_people_index(page, "Author")
        _wait_content_contains(page, PERSON_DISPLAY)
        _wait_list_cached(page, "people:index")

        cached = _cached_list(page, "people:index")["value"]
        roles = sorted({r for row in cached for r in row["assigned_roles"]})
        self.assertIn("Reviewer", roles, "a role view cached a role-filtered list")
        self.assertIn(PERSON_UNVISITED_DISPLAY.split()[-1], [row["last_name"] for row in cached])

        requested = []

        def record_urls(route):
            requested.append(route.request.url)
            route.fallback()

        page.route("**/api/persons**", record_urls)
        self.addCleanup(lambda: _safe_unroute(page, "**/api/persons**", record_urls))

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_content_contains(page, PERSON_DISPLAY)
        _wait_offline_banner(page)
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
            timeout=20000,
        )
        author_names = _person_row_names(page)
        self.assertIn(PERSON_DISPLAY, author_names)
        self.assertNotIn(PERSON_B_DISPLAY, author_names)

        _open_people_index(page, "Reviewer")
        _wait_content_contains(page, PERSON_B_DISPLAY)
        reviewer_names = _person_row_names(page)
        self.assertIn(PERSON_B_DISPLAY, reviewer_names)
        self.assertNotIn(PERSON_DISPLAY, reviewer_names)

        _open_people_index(page)
        _wait_content_contains(page, PERSON_UNVISITED_DISPLAY)
        all_names = _person_row_names(page)
        self.assertGreater(len(all_names), len(reviewer_names))
        # A route change may still attempt its read-through; what matters is
        # that it never asks the server for a filtered subset.
        self.assertTrue(requested, "the route should still attempt its read-through")
        for url in requested:
            self.assertEqual(urlparse(url).path, "/api/persons")
            self.assertEqual(urlparse(url).query, "", url)

    def test_cached_people_index_searches_locally(self):
        server, page, context, _collector = self._start()

        _wait_sw_active(page)
        _open_people_index(page)
        _wait_content_contains(page, PERSON_DISPLAY)
        _wait_list_cached(page, "people:index")

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_content_contains(page, PERSON_DISPLAY)
        _wait_offline_banner(page)

        seen = self._record_calls(page, "**/api/persons**")
        search = page.locator("#prks-people-library-search")
        for needle, expected in (
            (PERSON_B_DISPLAY, PERSON_B_DISPLAY),   # canonical name
            (PERSON_A_ALIASES, PERSON_DISPLAY),      # aliases
            ("offline Person detail", PERSON_DISPLAY),  # biography
            ("Reviewer", PERSON_B_DISPLAY),          # assigned role name
            (PERSON_GROUP_NAME, PERSON_DISPLAY),     # group name
        ):
            search.fill(needle)
            page.wait_for_function(
                "n => { const r = document.querySelectorAll('.prks-people-list__title');"
                " return r.length === 1 && r[0].textContent.trim() === n; }",
                arg=expected,
                timeout=15000,
            )
        search.fill("no such person anywhere")
        page.wait_for_function(
            "() => document.querySelectorAll('.prks-people-list__title').length === 0"
        )
        self.assertEqual(seen, [], "offline People search must issue zero API requests")

        page.wait_for_function(
            "() => !!document.querySelector('[data-prks-role=\"person-mutation-control\"][disabled]')",
            timeout=20000,
        )

    def test_uncached_people_index_offline_is_explicitly_unavailable(self):
        server, page, context, _collector = self._start()

        _wait_sw_active(page)
        _open_people_index(page)
        _wait_content_contains(page, PERSON_DISPLAY)
        _wait_list_cached(page, "people:index")
        page.evaluate("() => { void window.prksNavigate('#/folders'); }")
        page.wait_for_function("() => location.hash === '#/folders'")
        _clear_cached_list(page, "people:index")
        _wait_list_uncached(page, "people:index")

        context.set_offline(True)
        _open_people_index(page)
        _wait_offline_unavailable(page)
        body = _content_text(page)
        self.assertIn("not available offline", body)
        self.assertNotIn("No people yet.", body)
        # The same applies to a role view.
        _open_people_index(page, "Author")
        _wait_offline_unavailable(page)

    def test_cached_empty_people_index_is_not_the_uncached_state(self):
        """An authoritative [] that really was cached still renders the ordinary
        empty state -- with New Person disabled offline."""
        server, page, context, _collector = self._start(seed_fn=seed_library)
        # seed_library seeds one Person, so remove it to get a real empty list.
        page.evaluate(
            """async (id) => {
                await window.prksRequest('/api/works/' + id, { method: 'DELETE' });
            }""",
            server.ids["work_a"],
        )
        page.evaluate(
            """async (id) => {
                await window.prksRequest('/api/persons/' + id, { method: 'DELETE' });
            }""",
            server.ids["person"],
        )

        _wait_sw_active(page)
        _open_people_index(page)
        _wait_content_contains(page, "No people yet.")
        _wait_list_cached(page, "people:index")
        self.assertEqual(_cached_list(page, "people:index")["value"], [])

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_content_contains(page, "No people yet.")
        _wait_offline_banner(page)
        self.assertNotIn("not available offline", _content_text(page))
        page.wait_for_function(
            "() => !!document.querySelector('[data-prks-role=\"person-mutation-control\"][disabled]')",
            timeout=20000,
        )

    def test_cached_empty_role_subset_is_not_the_uncached_state(self):
        """People exist, but none hold this role: a legitimate empty role view,
        not an offline-unavailable one."""
        server, page, context, _collector = self._start()

        _wait_sw_active(page)
        _open_people_index(page)
        _wait_content_contains(page, PERSON_DISPLAY)
        _wait_list_cached(page, "people:index")

        context.set_offline(True)
        # Nothing in the fixture holds the Translator role.
        _open_people_index(page, "Translator")
        _wait_offline_banner(page)
        body = _content_text(page)
        self.assertNotIn("not available offline", body)
        self.assertEqual(_person_row_names(page), [])

    def test_malformed_people_index_response_never_replaces_a_good_cache(self):
        server, page, context, _collector = self._start()

        _wait_sw_active(page)
        _open_people_index(page)
        _wait_content_contains(page, PERSON_DISPLAY)
        _wait_list_cached(page, "people:index")
        good = _cached_list(page, "people:index")
        malformed = json.loads(json.dumps(good["value"]))
        malformed[0]["first_name"] = {}

        def bad_index(route):
            if route.request.method == "GET" and urlparse(route.request.url).path == "/api/persons":
                route.fulfill(status=200, content_type="application/json", body=json.dumps(malformed))
                return
            route.fallback()

        page.route("**/api/persons", bad_index)
        try:
            page.evaluate("() => { void window.prksNavigate('#/folders'); }")
            page.wait_for_function("() => location.hash === '#/folders'")
            _open_people_index(page)
            page.wait_for_function(
                "() => document.querySelector('#prks-route-retry') !== null", timeout=15000
            )
            body = _content_text(page)
            self.assertNotIn("No people yet.", body)
            self.assertNotIn("not available offline", body)
            self.assertEqual(_connectivity_state(page), "online")
            page.wait_for_timeout(500)
            self.assertEqual(_cached_list(page, "people:index"), good)
        finally:
            _safe_unroute(page, "**/api/persons", bad_index)

        page.evaluate("() => { void window.prksNavigate('#/folders'); }")
        page.wait_for_function("() => location.hash === '#/folders'")
        context.set_offline(True)
        _open_people_index(page)
        _wait_offline_banner(page)
        _wait_content_contains(page, PERSON_DISPLAY)

    # ---- cached detail ------------------------------------------------------

    def test_cached_person_detail_renders_offline(self):
        server, page, context, _collector = self._start()
        person_a = server.ids["person_a"]

        _wait_sw_active(page)
        _open_person(page, person_a)
        _wait_content_contains(page, PERSON_DISPLAY)
        _wait_entity_cached(page, "person", person_a)

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_content_contains(page, PERSON_DISPLAY)
        _wait_offline_banner(page)
        body = _content_text(page)
        self.assertIn(PERSON_A_ABOUT, body)          # biography
        self.assertIn(PERSON_A_ALIASES, body)        # aliases
        self.assertIn("1903", body)                  # lifespan
        self.assertIn(PERSON_GROUP_NAME, body)       # group membership
        self.assertIn(WORK_A_TITLE, body)            # linked Work card
        self.assertIn("Author", body)                # role label

    def test_malformed_person_responses_never_replace_a_good_cache(self):
        server, page, context, _collector = self._start()
        person_a = server.ids["person_a"]

        _wait_sw_active(page)
        _open_person(page, person_a)
        _wait_content_contains(page, PERSON_DISPLAY)
        _wait_entity_cached(page, "person", person_a)
        good = _cached_entity(page, "person", person_a)

        malformed_values = {"value": good["value"]}

        def bad_detail(route):
            if (
                route.request.method == "GET"
                and urlparse(route.request.url).path == "/api/persons/" + person_a
            ):
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(malformed_values["value"]),
                )
                return
            route.fallback()

        scalar_cases = {
            "first_name": {},
            "last_name": [],
            "aliases": [],
            "about": {},
            "image_url": [],
            "link_wikipedia": 7,
            "link_stanford_encyclopedia": False,
            "link_iep": {},
            "links_other": {},
            "birth_date": [],
            "death_date": True,
        }
        malformed_cases = []
        for field, value in scalar_cases.items():
            row = json.loads(json.dumps(good["value"]))
            row[field] = value
            malformed_cases.append((field, row))
        for field, value in (("year", []), ("published_date", {})):
            row = json.loads(json.dumps(good["value"]))
            row["works"][0][field] = value
            malformed_cases.append(("works[]." + field, row))

        page.route("**/api/persons/**", bad_detail)
        try:
            for label, malformed in malformed_cases:
                with self.subTest(field=label):
                    malformed_values["value"] = malformed
                    _open_people_index(page)
                    _wait_content_contains(page, PERSON_DISPLAY)
                    _open_person(page, person_a)
                    page.wait_for_function(
                        "() => document.querySelector('#prks-route-retry') !== null",
                        timeout=15000,
                    )
                    body = _content_text(page)
                    self.assertNotIn("Person not found", body)
                    self.assertNotIn("not available offline", body)
                    self.assertEqual(_connectivity_state(page), "online")
                    page.wait_for_timeout(100)
                    self.assertEqual(_cached_entity(page, "person", person_a), good)
        finally:
            _safe_unroute(page, "**/api/persons/**", bad_detail)

        _open_people_index(page)
        _wait_content_contains(page, PERSON_DISPLAY)
        context.set_offline(True)
        _open_person(page, person_a)
        _wait_offline_banner(page)
        _wait_content_contains(page, PERSON_A_ABOUT)

    def test_malformed_cached_person_scalar_is_discarded_before_render(self):
        server, page, context, _collector = self._start()
        person_a = server.ids["person_a"]

        _wait_sw_active(page)
        _open_person(page, person_a)
        _wait_content_contains(page, PERSON_DISPLAY)
        _wait_entity_cached(page, "person", person_a)
        page.evaluate(
            """async (id) => {
                const store = window.createPrksOfflineStore();
                const row = await store.getEntity('person', id);
                row.value.aliases = [];
                await store.putEntity('person', id, row.value, '');
            }""",
            person_a,
        )
        page.wait_for_function(
            """(id) => window.createPrksOfflineStore().getEntity('person', id)
                .then(row => !!row && Array.isArray(row.value.aliases))""",
            arg=person_a,
        )
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_offline_unavailable(page)
        body = _content_text(page)
        self.assertIn("Person not available offline", body)
        self.assertNotIn("Person not found", body)
        self.assertNotIn(PERSON_A_ABOUT, body)
        self.assertEqual(errors, [])
        _wait_entity_uncached(page, "person", person_a)

    def test_malformed_cached_person_work_scalar_is_discarded_before_render(self):
        server, page, context, _collector = self._start()
        person_a = server.ids["person_a"]

        _wait_sw_active(page)
        _open_person(page, person_a)
        _wait_content_contains(page, WORK_A_TITLE)
        _wait_entity_cached(page, "person", person_a)
        page.evaluate(
            """async (id) => {
                const store = window.createPrksOfflineStore();
                const row = await store.getEntity('person', id);
                row.value.works[0].year = [];
                await store.putEntity('person', id, row.value, '');
            }""",
            person_a,
        )
        page.wait_for_function(
            """(id) => window.createPrksOfflineStore().getEntity('person', id)
                .then(row => !!row && Array.isArray(row.value.works[0].year))""",
            arg=person_a,
        )
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_offline_unavailable(page)
        body = _content_text(page)
        self.assertIn("Person not available offline", body)
        self.assertNotIn("Person not found", body)
        self.assertNotIn(WORK_A_TITLE, body)
        self.assertEqual(errors, [])
        _wait_entity_uncached(page, "person", person_a)

    def test_cached_index_does_not_prefetch_every_person_detail(self):
        server, page, context, _collector = self._start()
        person_a = server.ids["person_a"]
        unvisited = server.ids["person_unvisited"]

        _wait_sw_active(page)
        _open_people_index(page)
        _wait_list_cached(page, "people:index")
        _open_person(page, person_a)
        _wait_entity_cached(page, "person", person_a)
        _open_people_index(page)
        _wait_content_contains(page, PERSON_UNVISITED_DISPLAY)
        self.assertIsNone(_cached_entity(page, "person", unvisited))

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        _wait_content_contains(page, PERSON_UNVISITED_DISPLAY)
        page.locator('.prks-people-list__link[href$="%s"]' % unvisited).click()
        _wait_offline_unavailable(page)
        body = _content_text(page)
        self.assertIn("not available offline", body)
        self.assertNotIn("Person not found", body)
        _wait_entity_cached(page, "person", person_a)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _open_person(page, person_a)
        _wait_content_contains(page, PERSON_DISPLAY)

    def test_cached_person_work_links_use_the_existing_work_route(self):
        server, page, context, _collector = self._start()
        person_a = server.ids["person_a"]
        person_b = server.ids["person_b"]
        work_a = server.ids["work_a"]
        work_b = server.ids["work_b"]

        _wait_sw_active(page)
        _open_person(page, person_a)
        _wait_entity_cached(page, "person", person_a)
        _open_person(page, person_b)
        _wait_entity_cached(page, "person", person_b)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)
        self.assertIsNone(_cached_entity(page, "work", work_b))

        context.set_offline(True)
        _open_person(page, person_a)
        _wait_content_contains(page, WORK_A_TITLE)
        _wait_offline_banner(page)
        page.locator('[data-prks-route="#/works/%s"]' % work_a).first.click()
        page.wait_for_function("id => decodeURIComponent(location.hash).indexOf(id) !== -1", arg=work_a)
        page.wait_for_function("t => document.body.innerText.indexOf(t) !== -1", arg=WORK_A_TITLE)

        # A linked Work whose detail was never cached gets the ordinary Work
        # offline-unavailable state -- no Person-specific Work router.
        _open_person(page, person_b)
        _wait_content_contains(page, WORK_B_TITLE)
        page.locator('[data-prks-route="#/works/%s"]' % work_b).first.click()
        _wait_offline_unavailable(page)

    def test_cached_person_mounts_request_no_prks_media(self):
        """Phase 1 caches structured data only, so a cached mount must not ask
        for portrait or thumbnail bytes it cannot get."""
        server, page, context, _collector = self._start()
        person_a = server.ids["person_a"]

        _wait_sw_active(page)
        # Give the Person an image_url so a portrait would normally be requested.
        page.evaluate(
            """async (id) => {
                await window.prksRequest('/api/persons/' + id, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ image_url: 'https://example.com/portrait.png' }),
                });
            }""",
            person_a,
        )
        _open_person(page, person_a)
        _wait_content_contains(page, PERSON_DISPLAY)
        _wait_entity_cached(page, "person", person_a)

        media = []

        def record_media(route):
            path = urlparse(route.request.url).path
            if path.endswith("/profile-image") or "/thumbnail" in path:
                media.append(path)
            route.fallback()

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_content_contains(page, PERSON_DISPLAY)
        _wait_offline_banner(page)
        page.route("**/api/**", record_media)
        try:
            page.wait_for_timeout(1200)
            self.assertEqual(media, [], "cached Person mount requested PRKS media: %r" % (media,))
            # ... and the profile still renders cleanly, with no broken image.
            self.assertIn(PERSON_A_ABOUT, _content_text(page))
            self.assertEqual(page.locator("img.person-portrait").count(), 0)
            # Work cards keep their placeholder box (no broken image), but carry
            # no thumbnail source to fetch.
            self.assertEqual(page.locator("[data-prks-thumb-src]").count(), 0)
            self.assertGreaterEqual(page.locator(".work-card__thumb--empty").count(), 1)
        finally:
            _safe_unroute(page, "**/api/**", record_media)


class OfflinePeopleMutationTests(unittest.TestCase):
    """Every Person mutation surface is online-only, and an open editor keeps
    its draft when connectivity drops."""

    def _start(self):
        server = AppServer(seed_fn=seed_people_library)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin, service_workers="allow")
        self.addCleanup(context.close)
        return server, page, context, collector

    def _go_offline_via_transport(self, page):
        def abort_api(route):
            route.abort("connectionrefused")

        page.route("**/api/**", abort_api)
        page.evaluate("""async () => { try { await window.prksRequest('/api/settings'); } catch (_e) {} }""")
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
            timeout=20000,
        )
        return lambda: _safe_unroute(page, "**/api/**", abort_api)

    def test_new_person_is_blocked_from_every_surface_offline(self):
        """Guarding is centralized in openModal('person-modal'), so the People
        page, the ribbon and the command palette all fail safely at once."""
        server, page, context, _collector = self._start()

        _wait_sw_active(page)
        _open_people_index(page)
        _wait_content_contains(page, PERSON_DISPLAY)
        _wait_list_cached(page, "people:index")

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_content_contains(page, PERSON_DISPLAY)
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
            timeout=20000,
        )

        mutations = []

        def record_mutation(route):
            if route.request.method in ("POST", "PATCH", "PUT", "DELETE"):
                mutations.append((route.request.method, urlparse(route.request.url).path))
            route.fallback()

        page.route("**/api/persons**", record_mutation)
        try:
            btn = page.locator('[data-prks-role="person-mutation-control"]').first
            self.assertTrue(btn.is_disabled())
            btn.click(force=True)
            page.wait_for_timeout(200)
            self.assertEqual(page.locator("#person-modal:not(.hidden)").count(), 0)
            # The central guard covers every other caller of the same modal.
            page.evaluate("() => { try { openModal('person-modal'); } catch (_e) {} }")
            page.wait_for_timeout(200)
            self.assertEqual(page.locator("#person-modal:not(.hidden)").count(), 0)
            self.assertEqual(mutations, [])
        finally:
            _safe_unroute(page, "**/api/persons**", record_mutation)

    def test_disconnect_while_new_person_modal_open_blocks_the_post(self):
        server, page, _context, _collector = self._start()

        _wait_sw_active(page)
        _open_people_index(page)
        _wait_content_contains(page, PERSON_DISPLAY)
        page.evaluate("() => openModal('person-modal')")
        page.locator("#person-modal:not(.hidden)").wait_for()
        page.locator("#person-lname").fill("Disconnected")

        mutations = []

        def block_api(route):
            if route.request.method in ("POST", "PATCH", "PUT", "DELETE"):
                mutations.append((route.request.method, urlparse(route.request.url).path))
            route.abort("connectionrefused")

        page.route("**/api/**", block_api)
        try:
            page.evaluate("""async () => { try { await window.prksRequest('/api/settings'); } catch (_e) {} }""")
            page.wait_for_function(
                "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
                timeout=20000,
            )
            page.locator("#save-person-btn").click()
            page.wait_for_timeout(500)
            self.assertEqual(mutations, [], "no Person create may be attempted after disconnect")
        finally:
            _safe_unroute(page, "**/api/**", block_api)

        page.evaluate("""async () => { try { await window.prksRequest('/api/settings'); } catch (_e) {} }""")
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'online'",
            timeout=20000,
        )
        names = page.evaluate("() => fetchPersons().then(items => items.map(p => p.last_name))")
        self.assertNotIn("Disconnected", names)

    def test_cached_person_detail_is_read_only_offline(self):
        server, page, context, _collector = self._start()
        person_a = server.ids["person_a"]

        _wait_sw_active(page)
        _open_person(page, person_a)
        _wait_content_contains(page, PERSON_DISPLAY)
        _wait_entity_cached(page, "person", person_a)

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_content_contains(page, PERSON_DISPLAY)
        _wait_offline_banner(page)
        _open_details_drawer_if_tiled(page)

        page.wait_for_function(
            "() => { const b = document.querySelector('#panel-content"
            " [data-prks-role=\"person-mutation-control\"]'); return !!b && b.disabled; }",
            timeout=20000,
        )
        self.assertTrue(page.locator("#prks-person-view-graph").is_disabled())
        # Relationship editing cannot be entered ...
        page.evaluate("() => { try { prksTogglePersonWorksEdit(); } catch (_e) {} }")
        page.wait_for_timeout(200)
        self.assertEqual(page.locator(".person-profile__card-unlink").count(), 0)
        # ... nor can profile editing.
        page.evaluate("() => { try { openPersonProfileEdit(); } catch (_e) {} }")
        page.wait_for_timeout(200)
        self.assertEqual(page.locator(".person-panel-edit").count(), 0)
        # Work links stay usable.
        self.assertGreaterEqual(page.locator('[data-prks-route^="#/works/"]').count(), 1)

    def test_person_group_links_use_normal_offline_destination(self):
        server, page, context, _collector = self._start()
        person_a = server.ids["person_a"]
        group_id = server.ids["person_group"]
        _wait_sw_active(page)
        _open_person(page, person_a)
        _wait_entity_cached(page, "person", person_a)
        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        _wait_offline_banner(page)
        link = page.locator('[data-prks-role="person-group-link"]').first
        self.assertIsNone(link.get_attribute("aria-disabled"))
        link.click()
        _wait_offline_unavailable(page)
        self.assertEqual(page.evaluate("location.hash"), "#/people/groups/" + group_id)
        self.assertIn("Group not available offline", _content_text(page))
    def test_person_graph_action_requires_a_connection_offline(self):
        server, page, context, _collector = self._start()
        person_a = server.ids["person_a"]

        _wait_sw_active(page)
        _open_person(page, person_a)
        _wait_content_contains(page, PERSON_DISPLAY)
        _wait_entity_cached(page, "person", person_a)
        _open_details_drawer_if_tiled(page)
        self.assertFalse(page.locator("#prks-person-view-graph").is_disabled())

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _wait_offline_banner(page)
        _open_details_drawer_if_tiled(page)
        page.wait_for_function(
            "() => !!document.querySelector('#prks-person-view-graph[disabled]')", timeout=20000
        )
        page.locator("#prks-person-view-graph").click(force=True)
        page.wait_for_timeout(300)
        self.assertNotIn("/graph", page.evaluate("() => location.hash"))

        context.set_offline(False)
        page.evaluate("""async () => { try { await window.prksRequest('/api/settings'); } catch (_e) {} }""")
        page.wait_for_function(
            "() => !document.querySelector('#prks-person-view-graph[disabled]')", timeout=20000
        )

    def test_open_profile_editor_survives_disconnect_without_losing_the_draft(self):
        server, page, _context, _collector = self._start()
        person_a = server.ids["person_a"]

        _wait_sw_active(page)
        _open_person(page, person_a)
        _wait_content_contains(page, PERSON_DISPLAY)
        _open_details_drawer_if_tiled(page)
        page.locator("#panel-content button", has_text="Edit profile").click()
        page.locator('.person-panel-edit[data-person-edit-id="%s"]' % person_a).wait_for()
        draft = "Draft written before the connection dropped"
        page.locator("#pd-about").fill(draft)

        mutations = []

        def block_api(route):
            if route.request.method in ("POST", "PATCH", "PUT", "DELETE"):
                mutations.append((route.request.method, urlparse(route.request.url).path))
            route.abort("connectionrefused")

        page.route("**/api/**", block_api)
        try:
            page.evaluate("""async () => { try { await window.prksRequest('/api/settings'); } catch (_e) {} }""")
            page.wait_for_function(
                "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
                timeout=20000,
            )
            page.wait_for_function(
                "() => !!document.querySelector('#pd-about[disabled]')", timeout=20000
            )
            # The draft is still there ...
            self.assertEqual(page.locator("#pd-about").input_value(), draft)
            self.assertTrue(page.locator("#pd-first-name").is_disabled())
            self.assertTrue(page.locator("#pd-save-btn").is_disabled())
            self.assertTrue(page.locator("#pd-group-add-btn").is_disabled())
            # ... Cancel stays usable ...
            self.assertFalse(
                page.locator('.person-panel-edit [data-prks-person-cancel]').is_disabled()
            )
            # ... and Save cannot reach the network.
            page.locator("#pd-save-btn").click(force=True)
            page.wait_for_timeout(400)
            self.assertEqual(mutations, [])
        finally:
            _safe_unroute(page, "**/api/**", block_api)

        page.evaluate("""async () => { await window.prksRequest('/api/settings'); }""")
        page.wait_for_function("() => !document.querySelector('#pd-about[disabled]')", timeout=20000)
        self.assertEqual(page.locator("#pd-about").input_value(), draft)
        self.assertFalse(page.locator("#pd-save-btn").is_disabled())

    def test_relationship_editor_becomes_inert_on_disconnect(self):
        server, page, _context, _collector = self._start()
        person_a = server.ids["person_a"]

        _wait_sw_active(page)
        _open_person(page, person_a)
        _wait_content_contains(page, WORK_A_TITLE)
        page.locator("button", has_text="Edit relationships").first.click()
        page.locator(".person-profile__card-unlink").first.wait_for()

        mutations = []

        def block_api(route):
            if route.request.method == "DELETE":
                mutations.append(urlparse(route.request.url).path)
            route.abort("connectionrefused")

        page.route("**/api/**", block_api)
        try:
            page.evaluate("""async () => { try { await window.prksRequest('/api/settings'); } catch (_e) {} }""")
            page.wait_for_function(
                "() => { const b = document.querySelector('.person-profile__card-unlink');"
                " return !!b && b.disabled; }",
                timeout=20000,
            )
            # The relationship list stays visible; only unlink goes inert.
            self.assertIn(WORK_A_TITLE, _content_text(page))
            page.locator(".person-profile__card-unlink").first.click(force=True)
            page.wait_for_timeout(400)
            self.assertEqual(mutations, [])
            # Done still exits relationship-edit mode.
            page.locator("button", has_text="Done").first.click()
            page.wait_for_timeout(300)
            self.assertEqual(page.locator(".person-profile__card-unlink").count(), 0)
        finally:
            _safe_unroute(page, "**/api/**", block_api)

    def test_profile_group_creation_is_blocked_after_disconnect(self):
        server, page, _context, _collector = self._start()
        person_a = server.ids["person_a"]

        _wait_sw_active(page)
        _open_person(page, person_a)
        _wait_content_contains(page, PERSON_DISPLAY)
        _open_details_drawer_if_tiled(page)
        page.locator("#panel-content button", has_text="Edit profile").click()
        page.locator('.person-panel-edit[data-person-edit-id="%s"]' % person_a).wait_for()
        page.wait_for_function(
            "() => typeof document.querySelector('#pd-group-add-btn')?.onclick === 'function'"
        )
        page.locator("#pd-group-search").fill("Brand New Offline Group")

        mutations = []

        def block_api(route):
            if route.request.method in ("POST", "PATCH", "PUT", "DELETE"):
                mutations.append((route.request.method, urlparse(route.request.url).path))
            route.abort("connectionrefused")

        page.route("**/api/**", block_api)
        try:
            page.evaluate("""async () => { try { await window.prksRequest('/api/settings'); } catch (_e) {} }""")
            page.wait_for_function(
                "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
                timeout=20000,
            )
            page.locator("#pd-group-add-btn").click(force=True)
            page.wait_for_timeout(400)
            self.assertEqual(mutations, [], "no Group create may be attempted offline")
            # The draft the user was building is untouched.
            self.assertEqual(page.locator("#pd-group-search").input_value(), "Brand New Offline Group")
        finally:
            _safe_unroute(page, "**/api/**", block_api)


class OfflinePeopleCoherenceTests(unittest.TestCase):
    """People is staled by Person, role, Work and Group changes -- and
    deliberately not by Concept, Position, Argument or Research Notes changes."""

    def _start(self):
        server = AppServer(seed_fn=seed_people_library)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin, service_workers="allow")
        self.addCleanup(context.close)
        return server, page, context, collector

    def _cache_people(self, page, server):
        _wait_sw_active(page)
        _open_people_index(page)
        _wait_list_cached(page, "people:index")
        _open_person(page, server.ids["person_a"])
        _wait_entity_cached(page, "person", server.ids["person_a"])

    def _assert_people_invalidated(self, page, server, before):
        page.wait_for_function(
            "n => (typeof prksOfflineDomainGeneration === 'function'"
            " ? prksOfflineDomainGeneration('people') : 0) > n",
            arg=before,
            timeout=20000,
        )
        _wait_entity_uncached(page, "person", server.ids["person_a"])
        _wait_list_uncached(page, "people:index")

    # Person Groups shares every Work-side dependency People has, because a
    # cached Group detail embeds whole People index rows (assigned_roles and
    # all). These helpers keep that half of the matrix assertable next to the
    # workflow that owns it rather than only in the Person Groups suite.
    def _cache_person_groups(self, page, server):
        page.evaluate("() => window.prksNavigate('#/people/groups')")
        _wait_list_cached(page, "person-groups:index")
        page.evaluate(
            "id => window.prksNavigate('#/people/groups/' + encodeURIComponent(id))",
            server.ids["person_group"],
        )
        _wait_entity_cached(page, "person-group", server.ids["person_group"])

    def _assert_person_groups_invalidated(self, page, server, before):
        page.wait_for_function(
            "n => (typeof prksOfflineDomainGeneration === 'function'"
            " ? prksOfflineDomainGeneration('person-groups') : 0) > n",
            arg=before,
            timeout=20000,
        )
        _wait_entity_uncached(page, "person-group", server.ids["person_group"])
        _wait_list_uncached(page, "person-groups:index")

    def _assert_person_groups_intact(self, page, server, before):
        self.assertEqual(_domain_generation(page, "person-groups"), before)
        self.assertIsNotNone(_cached_list(page, "person-groups:index"))
        self.assertIsNotNone(_cached_entity(page, "person-group", server.ids["person_group"]))

    def _create_work_through_the_modal(self, page, title, role=None):
        """Drives the real New File modal, so the create handler's own
        invalidation hooks are what is under test -- not a helper called by the
        test itself."""
        page.locator("#prks-ribbon-new-file").click()
        page.wait_for_selector("#work-modal:not(.hidden)")
        page.fill("#work-title", title)
        page.set_input_files("#work-file", str(MINIMAL_PDF))
        page.locator("#upload-selected-file-name").wait_for(state="visible")
        if role is not None:
            person_id, role_type = role
            page.evaluate(
                """([pid, roleType]) => {
                    document.getElementById('upload-person-id').value = pid;
                    document.getElementById('upload-person-search').value = 'E2E linked person';
                    document.getElementById('upload-role-type').value = roleType;
                }""",
                [person_id, role_type],
            )
            page.evaluate("() => window.addRoleToUploadList()")
            page.locator("#upload-roles-list .author-tag").first.wait_for()
        page.locator("#save-work-btn").click()
        page.wait_for_function("() => location.hash.indexOf('#/works/') === 0", timeout=20000)

    # ---- Person mutations ---------------------------------------------------

    def test_person_create_update_and_delete_invalidate_people(self):
        server, page, _context, _collector = self._start()
        person_a = server.ids["person_a"]

        self._cache_people(page, server)
        before = _domain_generation(page, "people")
        page.evaluate(
            """async () => {
                await window.prksRequest('/api/persons', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ first_name: 'New', last_name: 'Person' }),
                });
                window.prksMarkPeopleDomainChanged();
            }"""
        )
        self._assert_people_invalidated(page, server, before)

        # A biography-only edit stales People but NOT Arguments.
        self._cache_people(page, server)
        _open_argument(page, server.ids["argument_a"])
        _wait_entity_cached(page, "argument", server.ids["argument_a"])
        before = _domain_generation(page, "people")
        arguments_before = _domain_generation(page, "arguments")
        self._save_person_profile(page, person_a, "#pd-about", "A revised biography, same name.")
        self._assert_people_invalidated(page, server, before)
        self.assertEqual(_domain_generation(page, "arguments"), arguments_before)
        self.assertIsNotNone(_cached_entity(page, "argument", server.ids["argument_a"]))

        # A canonical-name change stales both.
        self._cache_people(page, server)
        before = _domain_generation(page, "people")
        arguments_before = _domain_generation(page, "arguments")
        self._save_person_profile(page, person_a, "#pd-first-name", "Renamed")
        self._assert_people_invalidated(page, server, before)
        self.assertGreater(_domain_generation(page, "arguments"), arguments_before)

        # Delete an unlinked Person.
        self._cache_people(page, server)
        before = _domain_generation(page, "people")
        page.evaluate(
            """async (id) => {
                await window.prksRequest('/api/persons/' + id, { method: 'DELETE' });
                window.prksMarkPeopleDomainChanged();
            }""",
            server.ids["person_unvisited"],
        )
        self._assert_people_invalidated(page, server, before)

    def _save_person_profile(self, page, person_id, field, value):
        page.evaluate("id => { void window.prksNavigate('#/people/' + id); }", person_id)
        page.wait_for_function("id => decodeURIComponent(location.hash).indexOf(id) !== -1", arg=person_id)
        _open_details_drawer_if_tiled(page)
        page.locator("#panel-content button", has_text="Edit profile").click()
        page.locator('.person-panel-edit[data-person-edit-id="%s"]' % person_id).wait_for()
        page.locator(field).fill(value)
        page.locator("#pd-save-btn").click()
        page.wait_for_selector(".person-panel-edit", state="detached", timeout=15000)

    def test_failed_person_mutation_retains_the_people_cache(self):
        server, page, _context, _collector = self._start()

        self._cache_people(page, server)
        before = _domain_generation(page, "people")

        def reject(route):
            if route.request.method in ("POST", "PATCH", "DELETE"):
                route.fulfill(status=500, content_type="application/json", body='{"error":"nope"}')
                return
            route.fallback()

        page.route("**/api/persons**", reject)
        try:
            page.evaluate(
                """async (id) => {
                    try {
                        const res = await window.prksRequest('/api/persons/' + id, {
                            method: 'PATCH',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ about: 'never applied' }),
                        });
                        if (res.ok) window.prksMarkPeopleDomainChanged();
                    } catch (_e) {}
                }""",
                server.ids["person_a"],
            )
            page.wait_for_timeout(400)
            self.assertEqual(_domain_generation(page, "people"), before)
            self.assertIsNotNone(_cached_entity(page, "person", server.ids["person_a"]))
        finally:
            _safe_unroute(page, "**/api/persons**", reject)

    # ---- role mutations -----------------------------------------------------

    def test_every_role_type_invalidates_people_but_only_author_invalidates_arguments(self):
        """People carries assigned_roles, the Person's linked Work rows and
        aliases (credit names), so EVERY role type stales it. Arguments only
        lists a source Work's Authors."""
        server, page, _context, _collector = self._start()
        work_b = server.ids["work_b"]
        person_unvisited = server.ids["person_unvisited"]

        # Non-Author role: People only.
        self._cache_people(page, server)
        _open_argument(page, server.ids["argument_a"])
        _wait_entity_cached(page, "argument", server.ids["argument_a"])
        before = _domain_generation(page, "people")
        arguments_before = _domain_generation(page, "arguments")
        page.evaluate(
            """async ([workId, personId]) => {
                const res = await window.prksRequest('/api/roles', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ person_id: personId, work_id: workId, role_type: 'Editor' }),
                });
                if (res.ok) window.prksMarkWorkRoleChanged(workId, 'Editor');
            }""",
            [work_b, person_unvisited],
        )
        self._assert_people_invalidated(page, server, before)
        self.assertEqual(_domain_generation(page, "arguments"), arguments_before)
        self.assertIsNotNone(_cached_entity(page, "argument", server.ids["argument_a"]))

        # Author role: People AND Arguments.
        self._cache_people(page, server)
        before = _domain_generation(page, "people")
        arguments_before = _domain_generation(page, "arguments")
        page.evaluate(
            """async ([workId, personId]) => {
                const res = await window.prksRequest('/api/roles', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ person_id: personId, work_id: workId, role_type: 'Author' }),
                });
                if (res.ok) window.prksMarkWorkRoleChanged(workId, 'Author');
            }""",
            [work_b, person_unvisited],
        )
        self._assert_people_invalidated(page, server, before)
        self.assertGreater(_domain_generation(page, "arguments"), arguments_before)

    def test_role_changes_through_the_real_ui_invalidate_people_and_groups(self):
        """`prksMarkWorkRoleChanged()` owns the People/Person Groups/Arguments
        split, but only a real role surface proves every surface calls it. Both
        halves of the Author boundary are exercised through the same UI: a
        non-Author link stales People and Person Groups and leaves Arguments
        alone; an Author unlink additionally stales Arguments."""
        server, page, _context, _collector = self._start()
        person_a = server.ids["person_a"]
        person_b = server.ids["person_b"]

        # Non-Author link, through the Manage relationships panel.
        self._cache_people(page, server)
        self._cache_person_groups(page, server)
        before = _domain_generation(page, "people")
        before_groups = _domain_generation(page, "person-groups")
        before_arguments = _domain_generation(page, "arguments")
        _open_work_from_home(page, WORK_A_TITLE)
        _open_details_drawer_if_tiled(page)
        page.locator("#panel-content button", has_text="Manage relationships").click()
        page.locator(".work-link-person-btn").click()
        page.wait_for_selector("#role-modal:not(.hidden)")
        # The role segmented control is mounted when the modal opens, so pick
        # the role through it rather than writing its hidden input first.
        page.locator('#role-role-seg-mount .prks-segmented__btn[data-value="Reviewer"]').click()
        page.evaluate(
            """([pid, wid]) => {
                document.getElementById('role-person-id').value = pid;
                document.getElementById('role-person-search').value = 'E2E linked person';
                document.getElementById('role-work-id').value = wid;
                document.getElementById('role-work-search').value = 'E2E linked work';
            }""",
            [person_b, server.ids["work_a"]],
        )
        self.assertEqual(page.locator("#role-type").input_value(), "Reviewer")
        page.locator("#save-role-btn").click()
        page.locator("#role-modal").wait_for(state="hidden", timeout=20000)
        self.assertIn(
            "Reviewer",
            page.evaluate(
                """async ([wid, pid]) => {
                    const w = await fetchWorkDetails(wid);
                    // A work role row is the joined Person row, so its `id`
                    // is the person's id.
                    return (w.roles || [])
                        .filter(r => String(r.id) === String(pid))
                        .map(r => r.role_type);
                }""",
                [server.ids["work_a"], person_b],
            ),
        )
        self._assert_people_invalidated(page, server, before)
        self._assert_person_groups_invalidated(page, server, before_groups)
        self.assertEqual(
            _domain_generation(page, "arguments"),
            before_arguments,
            "a non-Author role cannot change any cached Argument's displayed authors",
        )

        # Author unlink, same panel.
        self._cache_people(page, server)
        self._cache_person_groups(page, server)
        before = _domain_generation(page, "people")
        before_groups = _domain_generation(page, "person-groups")
        before_arguments = _domain_generation(page, "arguments")
        _open_work_from_home(page, WORK_A_TITLE)
        _open_details_drawer_if_tiled(page)
        page.locator("#panel-content button", has_text="Manage relationships").click()
        unlink = page.locator(
            '.work-linked-persons__unlink[data-role-type="Author"][data-person-id="%s"]' % person_a
        )
        unlink.wait_for(timeout=15000)
        unlink.click()
        page.locator("#prks-modal-confirm:not(.hidden)").wait_for(timeout=15000)
        page.locator("#prks-modal-confirm-ok").click()
        self._assert_people_invalidated(page, server, before)
        self._assert_person_groups_invalidated(page, server, before_groups)
        page.wait_for_function(
            "n => prksOfflineDomainGeneration('arguments') > n", arg=before_arguments, timeout=20000
        )

    # ---- Work mutations -----------------------------------------------------

    def test_work_metadata_save_invalidates_people(self):
        server, page, _context, _collector = self._start()

        self._cache_people(page, server)
        before = _domain_generation(page, "people")
        _open_work_from_home(page, WORK_A_TITLE)
        page.locator("#panel-content button", has_text="Edit metadata").click()
        page.locator("#meta-title").fill("Person Work Card Title Changed")
        page.locator("#inline-save-metadata-btn").click()
        page.locator("#panel-content .card-title", has_text="Person Work Card Title Changed").wait_for(
            timeout=15000
        )
        self._assert_people_invalidated(page, server, before)

    def test_playlist_work_rename_invalidates_people(self):
        """The shared Work-title helper owns this dependency, so the Playlist
        rename surface gets it without its own hook."""
        server, page, _context, _collector = self._start()
        work_a = server.ids["work_a"]

        self._cache_people(page, server)
        before = _domain_generation(page, "people")
        playlist_id = page.evaluate(
            """async (workId) => {
                const id = await createPlaylist('E2E People Rename Playlist', '');
                await addWorkToPlaylist(id, workId);
                return id;
            }""",
            arg=work_a,
        )
        page.evaluate("id => window.prksNavigate('#/playlists/' + encodeURIComponent(id))", arg=playlist_id)
        page.wait_for_selector(".prks-playlist-detail")
        page.locator("#prks-playlist-edit-btn").click()
        page.wait_for_selector('[data-pl-rename="%s"]' % work_a)
        page.locator('[data-pl-rename="%s"]' % work_a).click()
        page.locator("#prks-pl-rename-input-" + work_a).fill("Renamed From The Playlist")
        page.locator('[data-pl-rename-save="%s"]' % work_a).click()
        page.wait_for_function(
            "t => document.body.innerText.indexOf(t) !== -1", arg="Renamed From The Playlist", timeout=15000
        )
        self._assert_people_invalidated(page, server, before)

    def test_bulk_status_invalidates_people_but_folder_and_tag_moves_do_not(self):
        server, page, _context, _collector = self._start()
        work_a = server.ids["work_a"]

        self._cache_people(page, server)
        before = _domain_generation(page, "people")
        page.evaluate(
            "id => window.bulkUpdateWorks({ action: 'set_status', work_ids: [id], status: 'Completed' })",
            work_a,
        )
        self._assert_people_invalidated(page, server, before)

        # A folder move and a tag change are not on a Person's Work cards.
        self._cache_people(page, server)
        before = _domain_generation(page, "people")
        page.evaluate(
            """async (id) => {
                const folderId = await createFolder('People-neutral folder', '');
                await window.bulkUpdateWorks({ action: 'move_folder', work_ids: [id], folder_id: folderId });
                const tagRes = await window.prksRequest('/api/tags', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: 'people-neutral-tag', color: '#6d6cf7' }),
                });
                const tag = await tagRes.json();
                await window.bulkUpdateWorks({ action: 'add_tags', work_ids: [id], tag_ids: [tag.id] });
            }""",
            work_a,
        )
        page.wait_for_timeout(500)
        self.assertEqual(_domain_generation(page, "people"), before)
        self.assertIsNotNone(_cached_entity(page, "person", server.ids["person_a"]))

    def test_work_deletion_invalidates_people_and_person_groups(self):
        """Deleting a Work drops its role rows, so every cached Person *and*
        every cached Group member row that carried them is now stale."""
        server, page, _context, _collector = self._start()

        self._cache_people(page, server)
        self._cache_person_groups(page, server)
        before = _domain_generation(page, "people")
        before_groups = _domain_generation(page, "person-groups")
        _open_work_from_home(page, WORK_A_TITLE)
        _open_details_drawer_if_tiled(page)
        advanced = page.locator(".work-details-advanced")
        if advanced.get_attribute("open") is None:
            advanced.locator("summary").click()
        page.locator(".delete-work-btn").click()
        page.locator("#prks-modal-confirm:not(.hidden)", has_text="Delete file?").wait_for()
        page.locator("#prks-modal-confirm-ok").click()
        page.wait_for_function("() => location.hash === '#/folders'", timeout=15000)
        self._assert_people_invalidated(page, server, before)
        self._assert_person_groups_invalidated(page, server, before_groups)

    def test_work_creation_invalidates_people_and_groups_only_with_role_links(self):
        """Driven through the real New File modal, so the create handler's own
        hooks are what is under test. The Work-create endpoint can link roles in
        the same canonical request, bypassing POST /api/roles, so it owes People
        *and* Person Groups their own invalidation -- and owes them nothing at
        all when the payload carries no roles."""
        server, page, _context, _collector = self._start()
        person_a = server.ids["person_a"]
        _wait_sw_active(page)

        # Case A: no role links -- neither read model can have changed.
        self._cache_people(page, server)
        self._cache_person_groups(page, server)
        before_people = _domain_generation(page, "people")
        before_groups = _domain_generation(page, "person-groups")
        self._create_work_through_the_modal(page, "Roleless Modal Work")
        page.wait_for_timeout(500)
        self.assertEqual(
            _domain_generation(page, "people"),
            before_people,
            "a Work created with no role links cannot change the People read model",
        )
        self.assertIsNotNone(_cached_entity(page, "person", person_a))
        self._assert_person_groups_intact(page, server, before_groups)

        # Case B: a non-Author role link stales People and Person Groups, and
        # deliberately not Arguments.
        self._cache_people(page, server)
        self._cache_person_groups(page, server)
        before_people = _domain_generation(page, "people")
        before_groups = _domain_generation(page, "person-groups")
        before_arguments = _domain_generation(page, "arguments")
        self._create_work_through_the_modal(
            page, "Reviewer Modal Work", role=(person_a, "Reviewer")
        )
        self._assert_people_invalidated(page, server, before_people)
        self._assert_person_groups_invalidated(page, server, before_groups)
        self.assertEqual(_domain_generation(page, "arguments"), before_arguments)

        # Case C: an Author role link on a *newly created* Work stales the same
        # two domains and no more.
        self._cache_people(page, server)
        self._cache_person_groups(page, server)
        before_people = _domain_generation(page, "people")
        before_groups = _domain_generation(page, "person-groups")
        before_arguments = _domain_generation(page, "arguments")
        self._create_work_through_the_modal(
            page, "Author Modal Work", role=(person_a, "Author")
        )
        self._assert_people_invalidated(page, server, before_people)
        self._assert_person_groups_invalidated(page, server, before_groups)
        # ...but *not* Arguments, unlike an Author link onto an existing Work.
        # A Work that did not exist a moment ago cannot be in any cached
        # Argument's `sources[]` (those rows only come from putArgumentSources)
        # or `mentions[]` (those come from research notes, empty at create), and
        # no Person's displayed name changed. Copying the Author rule here would
        # shorten the Arguments cache for nothing.
        self.assertEqual(_domain_generation(page, "arguments"), before_arguments)

    def test_managed_pdf_save_invalidates_people_and_person_groups(self):
        """The managed PDF save changes file_size_bytes on every Person Work
        card and can add `Mentioned` roles from annotation markup, both of which
        are embedded in a cached Group's member rows. Driven by a real
        highlight, so the hook lives on the canonical persistence path."""
        server, page, _context, _collector = self._start()
        _wait_sw_active(page)
        self._cache_people(page, server)
        self._cache_person_groups(page, server)

        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        page.wait_for_function(
            "() => { const pdf = %s; return !!(pdf && pdf.annotationPersistence); }" % _FOCUSED_PDF,
            timeout=20000,
        )
        before_people = _domain_generation(page, "people")
        before_groups = _domain_generation(page, "person-groups")
        _commit_pdf_highlight(page)
        page.wait_for_function(_PDF_SYNC_SETTLED_JS, timeout=30000)
        self._assert_people_invalidated(page, server, before_people)
        self._assert_person_groups_invalidated(page, server, before_groups)

    def test_failed_managed_pdf_save_retains_the_person_groups_cache(self):
        """Coherence is published only on acknowledged canonical success."""
        server, page, _context, _collector = self._start()
        work_a = server.ids["work_a"]
        _wait_sw_active(page)
        self._cache_people(page, server)
        self._cache_person_groups(page, server)

        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        page.wait_for_function(
            "() => { const pdf = %s; return !!(pdf && pdf.annotationPersistence); }" % _FOCUSED_PDF,
            timeout=20000,
        )
        before_groups = _domain_generation(page, "person-groups")

        def fail_pdf_save(route):
            if route.request.method == "POST":
                route.fulfill(status=500, content_type="application/json", body="{}")
            else:
                route.fallback()

        pattern = "**/api/works/%s/pdf" % work_a
        page.route(pattern, fail_pdf_save)
        try:
            _commit_pdf_highlight(page)
            page.wait_for_function(
                "() => { const pdf = %s; return !!(pdf && pdf.syncState && pdf.syncState.lastError); }"
                % _FOCUSED_PDF,
                timeout=30000,
            )
            self._assert_person_groups_intact(page, server, before_groups)
        finally:
            _safe_unroute(page, pattern, fail_pdf_save)


    # ---- Group mutations ----------------------------------------------------

    def test_group_membership_update_and_delete_invalidate_people(self):
        server, page, _context, _collector = self._start()
        group_id = server.ids["person_group"]
        person_b = server.ids["person_b"]

        self._cache_people(page, server)
        before = _domain_generation(page, "people")
        page.evaluate(
            "([g, p]) => window.addPersonGroupMember(g, p)", [group_id, person_b]
        )
        self._assert_people_invalidated(page, server, before)

        self._cache_people(page, server)
        before = _domain_generation(page, "people")
        page.evaluate(
            "([g, p]) => window.removePersonGroupMember(g, p)", [group_id, person_b]
        )
        self._assert_people_invalidated(page, server, before)

        self._cache_people(page, server)
        before = _domain_generation(page, "people")
        page.evaluate("g => window.updatePersonGroup(g, { name: 'Renamed Group' })", group_id)
        self._assert_people_invalidated(page, server, before)

        self._cache_people(page, server)
        before = _domain_generation(page, "people")
        page.evaluate("g => window.deletePersonGroup(g)", group_id)
        self._assert_people_invalidated(page, server, before)

    def test_creating_an_unassigned_group_leaves_people_eligible(self):
        """A brand-new Group appears in no existing Person's read model."""
        server, page, _context, _collector = self._start()

        self._cache_people(page, server)
        before = _domain_generation(page, "people")
        page.evaluate(
            """async () => {
                await window.prksRequest('/api/person-groups', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: 'Freshly Created Empty Group', description: '' }),
                });
            }"""
        )
        page.wait_for_timeout(500)
        self.assertEqual(_domain_generation(page, "people"), before)
        self.assertIsNotNone(_cached_entity(page, "person", server.ids["person_a"]))

    # ---- domain isolation ---------------------------------------------------

    def test_unrelated_domains_never_invalidate_people(self):
        server, page, _context, _collector = self._start()
        person_a = server.ids["person_a"]

        self._cache_people(page, server)
        before = _domain_generation(page, "people")
        page.evaluate(
            """async ([conceptId, positionId, argumentId]) => {
                await window.updateConcept(conceptId, { description: 'People isolation check.' });
                await window.updatePosition(positionId, { description: 'People isolation check.' });
                await window.putArgumentSources(argumentId, []);
            }""",
            [server.ids["concept_child"], server.ids["position_a"], server.ids["argument_a"]],
        )
        page.wait_for_timeout(600)
        self.assertEqual(
            _domain_generation(page, "people"),
            before,
            "Concept/Position/Argument data is not in the People read model",
        )
        self.assertIsNotNone(_cached_entity(page, "person", person_a))

        # Research Notes drive Concept and Argument mentions, not People.
        before = _domain_generation(page, "people")
        _open_work_from_home(page, WORK_A_TITLE)
        page.locator(".work-notes-editor-wrap .CodeMirror").first.click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text("a note that touches no Person data")
        page.locator('[data-prks-role="editor-status"]', has_text="All changes saved").wait_for(timeout=15000)
        page.wait_for_timeout(400)
        self.assertEqual(_domain_generation(page, "people"), before)
        self.assertIsNotNone(_cached_entity(page, "person", person_a))

    def test_people_invalidation_leaves_the_other_three_domains_alone(self):
        server, page, _context, _collector = self._start()
        concept_child = server.ids["concept_child"]
        position_a = server.ids["position_a"]
        argument_a = server.ids["argument_a"]

        self._cache_people(page, server)
        _open_concept(page, concept_child)
        _wait_entity_cached(page, "concept", concept_child)
        _open_position(page, position_a)
        _wait_entity_cached(page, "position", position_a)
        _open_argument(page, argument_a)
        _wait_entity_cached(page, "argument", argument_a)
        before = _domain_generation(page, "people")
        others = {
            d: _domain_generation(page, d) for d in ("concepts", "positions", "arguments")
        }

        # A Group membership change is People-only.
        page.evaluate(
            "([g, p]) => window.addPersonGroupMember(g, p)",
            [server.ids["person_group"], server.ids["person_b"]],
        )
        self._assert_people_invalidated(page, server, before)
        for domain, gen in others.items():
            self.assertEqual(_domain_generation(page, domain), gen, domain)
            self.assertFalse(_domain_blocked(page, domain), domain)
        self.assertIsNotNone(_cached_entity(page, "concept", concept_child))
        self.assertIsNotNone(_cached_entity(page, "position", position_a))
        self.assertIsNotNone(_cached_entity(page, "argument", argument_a))

    def test_stale_pre_invalidation_people_reads_cannot_repopulate_the_cache(self):
        server, page, _context, _collector = self._start()
        target = server.ids["person_unvisited"]

        _wait_sw_active(page)
        _open_people_index(page)
        _wait_list_cached(page, "people:index")
        _clear_cached_list(page, "people:index")
        _wait_list_uncached(page, "people:index")
        self.assertIsNone(_cached_entity(page, "person", target))

        held = []

        def hold_gets(route):
            req = route.request
            path = urlparse(req.url).path
            if req.method == "GET" and path in ("/api/persons", "/api/persons/" + target):
                held.append(route)
                return
            route.fallback()

        page.route("**/api/persons**", hold_gets)
        try:
            page.evaluate(
                """id => {
                    window.__heldPerson = window.prksOfflineReadEntity(
                        'person', id, '/api/persons/' + id, { domain: 'people' }
                    );
                    window.__heldList = window.prksOfflineReadList(
                        'people:index', '/api/persons', { domain: 'people' }
                    );
                }""",
                target,
            )
            for _ in range(100):
                if len(held) >= 2:
                    break
                page.wait_for_timeout(100)
            self.assertGreaterEqual(len(held), 2, "the People GETs were not intercepted")
            before = _domain_generation(page, "people")
            # A role mutation lands while both reads are still in flight.
            page.evaluate(
                """async ([workId, personId]) => {
                    const res = await window.prksRequest('/api/roles', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ person_id: personId, work_id: workId, role_type: 'Editor' }),
                    });
                    if (res.ok) window.prksMarkWorkRoleChanged(workId, 'Editor');
                }""",
                [server.ids["work_b"], server.ids["person_b"]],
            )
            self.assertGreater(_domain_generation(page, "people"), before)
            page.wait_for_function(
                "() => (typeof prksOfflineIsDomainBlocked === 'function'"
                " ? prksOfflineIsDomainBlocked('people') : true) === false",
                timeout=15000,
            )
            for route in held:
                route.fallback()
            page.evaluate("() => window.__heldPerson")
            page.evaluate("() => window.__heldList")
            page.wait_for_timeout(600)
            self.assertIsNone(
                _cached_entity(page, "person", target),
                "a pre-invalidation Person read must not repopulate the domain",
            )
            self.assertIsNone(
                _cached_list(page, "people:index"),
                "a pre-invalidation People list read must not repopulate the domain",
            )
        finally:
            _safe_unroute(page, "**/api/persons**", hold_gets)

        # A later authoritative read populates normally.
        _open_person(page, target)
        _wait_entity_cached(page, "person", target)


def _safe_unroute(page, pattern, handler):
    try:
        page.unroute(pattern, handler)
    except Exception:
        pass


if __name__ == "__main__":
    unittest.main()
