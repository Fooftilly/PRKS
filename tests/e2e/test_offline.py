"""Real Chromium + real PRKS server offline/PWA scenarios.

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
from tests.e2e.harness import AppServer, PageCollector, open_app_page, require_chromium, wait_for_async
from tests.e2e.test_app import (
    MINIMAL_PDF,
    _FOCUSED_PDF,
    _FOCUSED_VIEWER,
    _FOCUSED_WORK_NOTES,
    _commit_pdf_highlight,
    _continue_held_routes,
    _sync_success_at,
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


def _rename_work_durably(page, new_title, timeout=15000):
    """Rename the open Work through the durable Identity save.

    A Work Title is local-first: there is no metadata PATCH any more, so a
    rename is an operation that is enqueued, sent and then RECONCILED into
    every cached representation. Waiting for the queue to drain is therefore
    waiting for the acknowledgement, not for a response.
    """
    # The synchronized controls stay disabled until the durable base has been
    # read: saving against a base this session could not establish would
    # overwrite an edit it never saw. Wait for that, exactly as a user would.
    page.wait_for_function(
        "() => { const b = document.getElementById('save-work-identity-btn');"
        "        return !!b && !b.disabled; }",
        timeout=timeout,
    )
    page.locator('[data-prks-work-field="title"]').fill(new_title)
    page.locator("#save-work-identity-btn").click()
    # The operation is enqueued ASYNCHRONOUSLY by the click handler, so waiting
    # for an EMPTY queue can be satisfied by the instant before it is created.
    # Wait for it to exist first, then for it to retire.
    wait_for_async(page,
        "() => prksSync.store.listOperations().then(rows => rows.some("
        "  o => o.operation === 'SET_WORK_METADATA_FIELD'))",
        timeout=timeout,
    )
    wait_for_async(page,
        "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
        timeout=timeout,
    )


def _wait_entity_cached(page, kind, entity_id, timeout=15000):
    wait_for_async(page,
        """([kind, id]) => {
            if (typeof window.createPrksOfflineStore !== 'function') return false;
            const store = window.createPrksOfflineStore();
            return store.getEntity(kind, id).then(v => !!v);
        }""",
        arg=[kind, entity_id],
        timeout=timeout,
    )
    # Do NOT remove this settle delay, and do not shrink it, without new
    # evidence. `offline-store.js` resolving readwrite from `tx.oncomplete` is
    # NOT sufficient: a row this read transaction can observe is still lost if
    # the page is torn down (go offline + reload) immediately afterwards, and
    # the route then renders "not available offline". Measured with
    # tests/e2e/stress_cache_offline.py on the Concept-detail transition:
    # 20/20 iterations pass with this delay, 6-9/20 FAIL without it. There is no
    # page-observable signal for "the write is durable across teardown", so
    # there is no state condition to wait on instead.
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
    wait_for_async(page,
        """(key) => {
            if (typeof window.createPrksOfflineStore !== 'function') return false;
            return window.createPrksOfflineStore().getList(key).then(v => !!v);
        }""",
        arg=list_key,
        timeout=timeout,
    )
    # Same measured teardown-durability reason as _wait_entity_cached(); see
    # the comment there before touching this.
    page.wait_for_timeout(250)


def _drain_durable(page, message='a durable operation never retired'):
    """Wait for every durable operation to be acknowledged and retired.

    Coherence for the local-first families happens on ACKNOWLEDGEMENT, not at
    the call: a durable write changes nothing cached until the server answers,
    so a coherence assertion has to wait for that answer rather than for the
    call to return.
    """
    wait_for_async(
        page,
        "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
        timeout=40000, message=message)


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
    wait_for_async(page,
        """([kind, id]) => window.createPrksOfflineStore().getEntity(kind, id).then(row => row === null)""",
        arg=[kind, entity_id],
        timeout=timeout,
    )


def _wait_list_uncached(page, list_key, timeout=15000):
    wait_for_async(page,
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


def _set_group_membership(page, group_id, person_id, present):
    """Through the durable path, and drained: the canonical change a coherence
    rule follows is the acknowledgement, not the click."""
    page.evaluate(
        """async ([g, p, present]) => {
            const ops = await prksSync.store.listOperations();
            const observed = await prksAcknowledgedPersonGroupMembership(g, p, ops);
            await prksSetPersonGroupMemberDurably(g, p, present, observed);
        }""",
        [group_id, person_id, present],
    )
    _wait_sync_settled(page)


def _rename_group(page, group_id, name):
    page.evaluate(
        """async ([g, name]) => {
            const ops = await prksSync.store.listOperations();
            const base = await prksAcknowledgedPersonGroupBase(g, ops);
            await prksSavePersonGroupFieldsDurably(g, { name }, base);
        }""",
        [group_id, name],
    )
    _wait_sync_settled(page)


def _delete_group(page, group_id):
    page.evaluate("g => prksDeletePersonGroupDurably(g)", group_id)
    _wait_sync_settled(page)


def _wait_sync_settled(page, timeout_ms=30000):
    """Every durable operation sent and retired."""
    page.evaluate("""async ms => {
        const deadline = Date.now() + ms;
        for (;;) {
            const rows = await prksSync.store.listOperations();
            if (!rows.length) return;
            if (Date.now() > deadline) {
                throw new Error('sync did not settle: ' + JSON.stringify(rows));
            }
            await new Promise(resolve => setTimeout(resolve, 50));
        }
    }""", timeout_ms)


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
    wait_for_async(page,
        """(path) => {
            if (typeof caches === 'undefined') return false;
            return caches.open('prks-pdf-v1').then(c => c.match(path)).then(m => !!m);
        }""",
        arg=pdf_path,
        timeout=timeout,
    )


def _pdf_cache_fingerprint(page, pdf_path):
    """Length and FNV-1a of the prks-pdf-v1 whole-file entry, or None."""
    return page.evaluate(
        """async (path) => {
            if (typeof caches === 'undefined' || !path) return null;
            const cache = await caches.open('prks-pdf-v1');
            const match = await cache.match(path);
            if (!match) return null;
            const bytes = new Uint8Array(await match.arrayBuffer());
            let hash = 2166136261;
            for (let i = 0; i < bytes.length; i++) {
                hash ^= bytes[i];
                hash = Math.imul(hash, 16777619);
            }
            return { length: bytes.length, hash: hash >>> 0 };
        }""",
        pdf_path,
    )


def _wait_pdf_cache_replaced(page, pdf_path, previous, timeout=30000):
    """Wait until the whole-file cache entry is no longer `previous`."""
    wait_for_async(
        page,
        """async (arg) => {
            if (typeof caches === 'undefined') return false;
            const cache = await caches.open('prks-pdf-v1');
            const match = await cache.match(arg.path);
            if (!match) return false;
            const bytes = new Uint8Array(await match.arrayBuffer());
            let hash = 2166136261;
            for (let i = 0; i < bytes.length; i++) {
                hash ^= bytes[i];
                hash = Math.imul(hash, 16777619);
            }
            hash = hash >>> 0;
            return bytes.length !== arg.length || hash !== arg.hash;
        }""",
        arg={
            "path": pdf_path,
            "length": previous["length"],
            "hash": previous["hash"],
        },
        timeout=timeout,
        message="in-place PDF materialization did not replace prks-pdf-v1",
    )


def _connectivity_state(page):
    return page.evaluate(
        "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null)"
    )


def _pdf_mode(page):
    return page.evaluate("() => { const pdf = %s; return pdf ? pdf.mode : null; }" % _FOCUSED_PDF)


def _pdf_open_markup_tool(page, label="Highlight"):
    """Activate a markup tool, opening the responsive More menu when needed.

    Below the PDF viewer's 640px container breakpoint, Highlight/Underline live
    under More tools rather than in the primary toolbar strip.
    """
    toolbar = page.locator('[data-prks-role="pdf-viewer"] .prks-pdf-toolbar')
    primary = toolbar.locator(
        '.prks-pdf-toolbar__group > button[aria-label="%s"]' % label
    )
    if primary.count() and primary.first.is_visible():
        primary.first.click()
        return primary.first
    more = toolbar.locator('.prks-pdf-toolbar__more button[aria-label="More tools"]')
    more.wait_for(state="visible", timeout=15000)
    more.click()
    menu_tool = page.locator(
        '[data-prks-role="pdf-viewer"] .prks-pdf-toolbar__menu button[aria-label="%s"]'
        % label
    )
    menu_tool.wait_for(state="visible", timeout=15000)
    menu_tool.click()
    return menu_tool


_PDF_MARKUP_TOOLS_AVAILABLE_JS = """() => {
    const pdf = (window.prksGetFocusedTabContext &&
        window.prksGetFocusedTabContext().getResource('pdf'));
    // Coherent catch-up / materialization holds a gate and flips
    // setMutationEnabled(false) — mode may still be 'work' while tools are
    // hidden. Settlement requires the gate released AND tools reachable.
    if (pdf && pdf._annotationMaterializing) return false;
    const root = document.querySelector('[data-prks-role="pdf-viewer"] .prks-pdf-toolbar');
    if (!root) return false;
    const primary = root.querySelector(
        '.prks-pdf-toolbar__group > button[aria-label="Highlight"]');
    if (primary && primary.offsetParent !== null) return true;
    const more = root.querySelector(
        '.prks-pdf-toolbar__more button[aria-label="More tools"]');
    return !!(more && more.offsetParent !== null);
}"""


def _pdf_markup_tools_available(page):
    """True when markup tools are reachable (primary strip or More) and the
    materialization/catch-up gate is not holding user input locked."""
    return bool(page.evaluate(_PDF_MARKUP_TOOLS_AVAILABLE_JS))


def _wait_pdf_markup_tools_settled(page, timeout=20000):
    """Wait until catch-up/materialization unlocks and markup tools show."""
    page.wait_for_function(_PDF_MARKUP_TOOLS_AVAILABLE_JS, timeout=timeout)


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
        """The acknowledgement reconciles the cached Work to the exact new
        title -- it is not merged into a stale snapshot, and not dropped."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]
        new_title = "Offline Coherent Metadata Title"

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)
        page.locator("#panel-content button", has_text="Edit metadata").click()
        # No view card to wait for: a durable save leaves the editor open
        # rather than closing it and re-rendering, so the acknowledged cache is
        # the thing to observe -- and `_rename_work_durably` already waited for
        # the queue to drain.
        _rename_work_durably(page, new_title)

        wait_for_async(page,
            """([id, title]) => window.createPrksOfflineStore().getEntity('work', id)
                .then(row => !!row && row.value && row.value.title === title)""",
            arg=[work_a, new_title],
            timeout=15000,
        )

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("title => document.body.innerText.indexOf(title) !== -1", arg=new_title)
        page.locator('[data-prks-role="offline-provenance-banner"]', has_text="Offline").wait_for()

    # `test_metadata_success_with_failed_refresh_leaves_work_offline_unavailable`
    # was removed here rather than rewritten. It asserted that a metadata PATCH
    # invalidated the cached Work and that a failed follow-up GET left it
    # unavailable -- and a Work Title is local-first now, so there is no PATCH,
    # no follow-up GET and no invalidation: the acknowledgement reconciles the
    # cached Work with the exact new title.
    #
    # The invariant that replaced it -- an operation whose cache reconciliation
    # FAILS must stay in the queue and replay rather than retire on a guess --
    # is covered where it can actually be driven, in
    # `tests/browser/run_work_metadata_sync_selftest.js`
    # (`titleReferenceReconciliation`, `unreadableSummariesBlockRetirement`),
    # which can make a cache read fail. A browser test cannot, without a
    # product hook that exists only for the test.

    def test_successful_research_notes_save_patches_work_cache(self):
        """ACK patches the Work snapshot in place so the file stays available offline."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)
        page.locator(".CodeMirror").click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text("offline coherence research note")
        page.locator('[data-prks-role="editor-status"]', has_text="All changes saved").wait_for(timeout=15000)
        wait_for_async(page,
            """id => window.createPrksOfflineStore().getEntity('work', id)
                .then(row => !!(row && row.value && row.value.text_content === 'offline coherence research note'))""",
            arg=work_a,
            timeout=15000,
        )

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".CodeMirror")
        self.assertIn(work_a, page.evaluate("() => location.hash"))
        self.assertEqual(
            page.evaluate("() => %s.value()" % _FOCUSED_WORK_NOTES),
            "offline coherence research note",
        )

    def test_successful_private_notes_save_patches_work_cache(self):
        """ACK patches private_notes onto the cached Work; the file stays available offline."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]
        selector = "#prks-private-notes-work-" + work_a

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)
        page.locator(selector).fill("offline coherence private note")
        page.locator(selector).blur()
        wait_for_async(page,
            """() => prksSync.store.listOperations().then(rows =>
                rows.filter(r => r.operation === 'SET_WORK_PRIVATE_NOTE').length === 0)""",
            timeout=15000,
        )
        wait_for_async(page,
            """id => window.createPrksOfflineStore().getEntity('work', id)
                .then(row => !!(row && row.value && row.value.private_notes === 'offline coherence private note'))""",
            arg=work_a,
            timeout=15000,
        )

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.locator(selector).wait_for()
        self.assertEqual(page.locator(selector).input_value(), "offline coherence private note")

    def test_authoritative_tag_refresh_replaces_offline_work_cache(self):
        """Representative relationship mutation stores only its refreshed full Work."""
        server, page, _context, _collector = self._start()
        work_a = server.ids["work_a"]
        tag_name = "Offline Coherent Tag"

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)
        _open_details_drawer_if_tiled(page)
        page.locator("#panel-content button", has_text="Manage tags").click()
        page.wait_for_function(
            "() => { const i = document.getElementById('work-tag-search'); return i && !i.disabled; }",
            timeout=15000,
        )
        page.locator("#work-tag-search").fill(tag_name)
        page.locator("#work-tag-search-results .result-item--create", has_text=tag_name).click()
        page.locator("#work-tags-list .work-tag-chip", has_text=tag_name).wait_for(timeout=15000)
        wait_for_async(page,
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
        # Navigation follows local DELETE_WORK enqueue; eviction is on ACK.
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=60000,
            message="DELETE_WORK must acknowledge before cache eviction",
        )
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
        folder_id = page.evaluate(
            """async (workId) => {
                const folderId = await createFolder('Offline coherence folder add', '');
                await addWorkToFolder(folderId, workId);
                return folderId;
            }""",
            arg=work_a,
        )
        _wait_sync_settled(page)
        # Filing is durable now, and the acknowledgement states the new folder
        # and its title exactly -- so the Work's snapshot is PATCHED rather than
        # dropped. Discarding it would cost the user a page they can no longer
        # re-read once offline, for a change already known in full.
        cached = _cached_entity(page, "work", work_a)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["value"]["folder_id"], folder_id)

        context.set_offline(True)
        page.evaluate("id => { void window.prksNavigate('#/works/' + id); }", work_a)
        _wait_content_contains(page, WORK_A_TITLE)

    def test_a_filing_with_no_readable_base_is_refused_and_keeps_the_cache(self):
        """Filing cannot fail on the wire any more -- it is durable. What can
        still stop it is a base this device cannot read: without the revision
        the move was measured against it would have to guess, and guessing is
        what a base revision exists to prevent."""
        server, page, _context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)
        folder_id = page.evaluate("() => createFolder('Offline failed folder add', '')")
        _wait_sync_settled(page)

        def refuse_base(route):
            if urlparse(route.request.url).path.endswith("/folder-state"):
                route.fulfill(status=500, content_type="application/json",
                              body='{"error":"test failure"}')
                return
            route.fallback()

        page.route("**/api/works/**", refuse_base)
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
            self.assertEqual(
                page.evaluate("() => prksSync.store.listOperations().then(rows => rows.filter("
                              "  o => o.operation === 'SET_WORK_FOLDER').length)"), 0,
                "nothing is queued against a base it could not read")
        finally:
            page.unroute("**/api/works/**", refuse_base)
            _collector.console_errors[:] = [
                e for e in _collector.console_errors
                if "500 (Internal Server Error)" not in e
            ]
            _collector.http_5xx.clear()

    def test_failed_notes_save_retains_previous_work_cache(self):
        """A notes change that never reaches PRKS stays local; the last
        known-good Work snapshot remains available offline."""
        server, page, _context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)
        original = _cached_entity(page, "work", work_a)["value"]["text_content"]

        def reject_sync(route):
            route.fulfill(status=500, content_type="application/json", body='{"error":"test failure"}')

        page.route("**/api/sync/operations", reject_sync)
        try:
            page.locator(".CodeMirror").click()
            page.keyboard.press("Control+A")
            page.keyboard.insert_text("failed offline coherence note")
            page.evaluate("""() => {
                const ctx = window.prksGetFocusedTabContext();
                window.prksFlushPendingWorkResearchNotes(ctx);
            }""")
            wait_for_async(page,
                """() => prksSync.store.listOperations().then(rows =>
                    rows.some(r => r.operation === 'SET_WORK_RESEARCH_NOTE'))""",
                timeout=15000)
            cached = _cached_entity(page, "work", work_a)
            self.assertIsNotNone(cached)
            self.assertEqual(cached["value"]["id"], work_a)
            self.assertEqual(cached["value"]["text_content"], original)
        finally:
            page.unroute("**/api/sync/operations", reject_sync)

    def test_failed_work_delete_retains_offline_cache(self):
        """Unacknowledged DELETE must not discard a potentially valid snapshot."""
        server, page, _context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)

        def reject_sync(route):
            route.fulfill(status=500, content_type="application/json", body='{"error":"test failure"}')

        page.route("**/api/sync/operations", reject_sync)
        try:
            _open_details_drawer_if_tiled(page)
            advanced = page.locator(".work-details-advanced")
            if advanced.get_attribute("open") is None:
                advanced.locator("summary").click()
            page.locator(".delete-work-btn").click()
            page.locator("#prks-modal-confirm:not(.hidden)", has_text="Delete file?").wait_for()
            page.locator("#prks-modal-confirm-ok").click()
            # Durable delete enqueues locally and navigates; ACK never arrives.
            page.wait_for_function("() => location.hash === '#/folders'", timeout=15000)
            wait_for_async(
                page,
                """() => prksSync.store.listOperations().then(rows =>
                    rows.some(r => r.operation === 'DELETE_WORK'))""",
                timeout=15000,
            )
            self.assertIsNotNone(_cached_entity(page, "work", work_a))
        finally:
            page.unroute("**/api/sync/operations", reject_sync)

    def test_pending_delete_work_reopen_never_renders_cached_detail(self):
        """Cached Work + pending DELETE_WORK must stay unavailable on reopen.

        DELETE intentionally retains the disposable cache until ACK. The Work
        route must still classify the tombstone reliably — never paint the
        cached detail while deletion is pending.
        """
        server, page, _context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)

        def reject_sync(route):
            route.fulfill(status=500, content_type="application/json", body='{"error":"test failure"}')

        page.route("**/api/sync/operations", reject_sync)
        try:
            _open_details_drawer_if_tiled(page)
            advanced = page.locator(".work-details-advanced")
            if advanced.get_attribute("open") is None:
                advanced.locator("summary").click()
            page.locator(".delete-work-btn").click()
            page.locator("#prks-modal-confirm:not(.hidden)", has_text="Delete file?").wait_for()
            page.locator("#prks-modal-confirm-ok").click()
            page.wait_for_function("() => location.hash === '#/folders'", timeout=15000)
            wait_for_async(
                page,
                """() => prksSync.store.listOperations().then(rows =>
                    rows.some(r => r.operation === 'DELETE_WORK'))""",
                timeout=15000,
            )
            self.assertIsNotNone(_cached_entity(page, "work", work_a))

            page.evaluate("id => { void window.prksNavigate('#/works/' + id); }", work_a)
            page.wait_for_function(
                "id => location.hash.indexOf('#/works/' + id) === 0",
                arg=work_a,
                timeout=15000,
            )
            page.locator('[data-prks-role="offline-unavailable"]').wait_for(timeout=15000)
            self.assertEqual(page.locator(".work-detail").count(), 0)
            self.assertNotIn(
                WORK_A_TITLE,
                page.locator("#page-content").inner_text(),
            )
            wait_for_async(
                page,
                """() => prksSync.store.listOperations().then(rows =>
                    rows.some(r => r.operation === 'DELETE_WORK'))""",
                timeout=5000,
            )
            self.assertIsNotNone(_cached_entity(page, "work", work_a))
        finally:
            page.unroute("**/api/sync/operations", reject_sync)

    def test_pending_delete_work_survives_reload_without_painting_or_opening(self):
        """DELETE_WORK in IndexedDB must classify after a real reload.

        The live memory set starts empty; the Work route must read the
        persisted per-Work lifecycle marker (not await full listOperations)
        before publishing a retained cache or recording MARK_WORK_OPENED.
        """
        server, page, _context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)

        def reject_sync(route):
            route.fulfill(status=500, content_type="application/json", body='{"error":"test failure"}')

        page.route("**/api/sync/operations", reject_sync)
        try:
            _open_details_drawer_if_tiled(page)
            advanced = page.locator(".work-details-advanced")
            if advanced.get_attribute("open") is None:
                advanced.locator("summary").click()
            page.locator(".delete-work-btn").click()
            page.locator("#prks-modal-confirm:not(.hidden)", has_text="Delete file?").wait_for()
            page.locator("#prks-modal-confirm-ok").click()
            page.wait_for_function("() => location.hash === '#/folders'", timeout=15000)
            wait_for_async(
                page,
                """id => prksSync.store.listOperations().then(rows =>
                    rows.some(r => r.operation === 'DELETE_WORK' && r.entity_id === id))""",
                arg=work_a,
                timeout=15000,
            )
            wait_for_async(
                page,
                """id => prksSync.store.getWorkLifecycle(id).then(k => k === 'delete')""",
                arg=work_a,
                timeout=5000,
            )
            self.assertIsNotNone(_cached_entity(page, "work", work_a))

            # Count may be 0 (initial open already ACKed before sync was
            # blocked). wait_for_async treats 0 as failure, so evaluate.
            open_count_before = page.evaluate(
                """id => prksSync.store.listOperations().then(rows =>
                    rows.filter(r => r.operation === 'MARK_WORK_OPENED'
                        && r.entity_id === id).length)""",
                work_a,
            )

            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#sidebar", timeout=15000)
            _wait_sw_active(page)
            wait_for_async(
                page,
                """id => prksSync.store.getWorkLifecycle(id).then(k => k === 'delete')""",
                arg=work_a,
                timeout=15000,
            )
            wait_for_async(
                page,
                """id => prksSync.store.listOperations().then(rows =>
                    rows.some(r => r.operation === 'DELETE_WORK' && r.entity_id === id))""",
                arg=work_a,
                timeout=15000,
            )
            self.assertIsNotNone(_cached_entity(page, "work", work_a))

            page.evaluate("id => { void window.prksNavigate('#/works/' + id); }", work_a)
            page.wait_for_function(
                "id => location.hash.indexOf('#/works/' + id) === 0",
                arg=work_a,
                timeout=15000,
            )
            page.locator('[data-prks-role="offline-unavailable"]').wait_for(timeout=15000)
            self.assertEqual(page.locator(".work-detail").count(), 0)
            self.assertNotIn(
                WORK_A_TITLE,
                page.locator("#page-content").inner_text(),
            )
            self.assertFalse(
                page.evaluate(
                    """() => {
                        const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                        const work = ctx && ctx.getEntity ? ctx.getEntity('work') : null;
                        return !!(work && work.id);
                    }"""
                ),
                "pending DELETE must never publish the Work as the live entity",
            )
            open_count_after = page.evaluate(
                """id => prksSync.store.listOperations().then(rows =>
                    rows.filter(r => r.operation === 'MARK_WORK_OPENED'
                        && r.entity_id === id).length)""",
                work_a,
            )
            self.assertEqual(
                open_count_after,
                open_count_before,
                "reopening a tombstoned Work must not enqueue MARK_WORK_OPENED",
            )
            wait_for_async(
                page,
                """id => prksSync.store.listOperations().then(rows =>
                    rows.some(r => r.operation === 'DELETE_WORK' && r.entity_id === id))""",
                arg=work_a,
                timeout=5000,
            )
            self.assertIsNotNone(_cached_entity(page, "work", work_a))
            self.assertTrue(
                wait_for_async(
                    page,
                    """id => prksSync.store.getWorkLifecycle(id).then(k => k === 'delete')""",
                    arg=work_a,
                    timeout=5000,
                )
            )
        finally:
            page.unroute("**/api/sync/operations", reject_sync)

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
        never silently accepted -- the user sees an explicit requires-connection message.

        DELETE_WORK is durable; this pins a still connection-required surface:
        bulk organize.
        """
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)

        context.set_offline(True)
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
            timeout=20000,
        )

        mutation_requests = []
        page.on(
            "request",
            lambda req: mutation_requests.append(req.method)
            if req.method in ("POST", "PUT", "PATCH", "DELETE") and "/api/works" in req.url
            else None,
        )

        page.evaluate(
            """async (wid) => {
                try {
                    await bulkUpdateWorks({ action: 'set_status', work_ids: [wid], status: 'Completed' });
                } catch (e) { /* offline guard throws */ }
            }""",
            work_a,
        )

        page.locator("#prks-modal-confirm-title").wait_for()
        self.assertEqual(page.locator("#prks-modal-confirm-title").inner_text(), "Offline")
        self.assertIn(
            "Bulk organize requires a connection to PRKS.",
            page.locator("#prks-modal-confirm-desc").inner_text(),
        )
        self.assertTrue(page.locator("#prks-modal-confirm-cancel.hidden").count() >= 1)
        page.locator("#prks-modal-confirm-ok").click()
        page.locator("#prks-modal-confirm:not(.hidden)").wait_for(state="detached", timeout=5000)

        self.assertEqual(mutation_requests, [])
        context.set_offline(False)
        import urllib.request

        with urllib.request.urlopen(server.origin + "/api/works/" + work_a) as res:
            self.assertEqual(res.status, 200)
            body = json.loads(res.read().decode("utf-8"))
            self.assertEqual(body.get("status"), "In Progress")

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
        # This assertion INVERTED when Work metadata became local-first, and
        # deliberately so. It used to read "IndexedDB unavailable still saves",
        # because the editor PATCHed directly. A synchronized field must never
        # fall back to a PATCH: saving against a base this session could not
        # read would overwrite an edit it never saw. So with durable storage
        # unavailable the editor REFUSES rather than saving blind -- visibly,
        # with the controls disabled and a reason -- and the rest of the app
        # keeps working online exactly as before.
        page.wait_for_function(
            "() => { const b = document.getElementById('save-work-identity-btn');"
            "        return !!b && b.disabled; }",
            timeout=15000,
        )
        self.assertTrue(page.locator('[data-prks-work-field="title"]').is_disabled())
        self.assertIn(
            "Local changes could not be read",
            page.locator('[data-prks-role="work-identity-sync"]').inner_text(),
        )
        # The app itself is unaffected: still online, still rendering.
        self.assertEqual(_connectivity_state(page), "online")
        self.assertEqual(page.locator('[data-prks-role="work-identity-editor"]').count(), 1,
                         "the editor still renders; only saving is refused")

    def test_research_notes_stay_editable_immediately_when_offline(self):
        """Reopening a cached Work offline must leave Research Notes editable
        -- they are durable, not a connectivity-gated PATCH."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)
        page.wait_for_selector(".CodeMirror")

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        self.assertIn(work_a, page.evaluate("() => location.hash"))
        page.wait_for_selector(".CodeMirror")

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
        page.locator(".CodeMirror").click()
        page.keyboard.type(" OFFLINE-EDIT")
        page.wait_for_timeout(200)
        self.assertIn("OFFLINE-EDIT", page.evaluate("() => %s.value()" % _FOCUSED_WORK_NOTES))

    def test_private_notes_stay_editable_immediately_when_offline(self):
        """Same guarantee for Work Reminders -- the textarea starts editable."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]
        selector = "#prks-private-notes-work-" + work_a

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_entity_cached(page, "work", work_a)
        page.locator(selector).wait_for()

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        self.assertIn(work_a, page.evaluate("() => location.hash"))
        page.locator(selector).wait_for()

        self.assertFalse(page.evaluate("(sel) => document.querySelector(sel).readOnly", selector))
        page.locator(selector).click()
        page.keyboard.type("OFFLINE-EDIT")
        page.wait_for_timeout(200)
        self.assertIn("OFFLINE-EDIT", page.locator(selector).input_value())

    def test_cached_pdf_reopened_offline_keeps_annotation_tools(self):
        """Scenario 9 (V2): a previously-cached PDF reopened offline stays in
        'work' mode when PDF bytes, an acknowledged annotation base, and the
        durable store are available — markup tools remain reachable and the
        hydrate/event bridge installs (legacy full-list flush stays paused)."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]
        pdf_path = "/api/pdfs/%s" % server.ids["pdf_name"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        _wait_pdf_whole_file_cached(page, pdf_path)
        # Warm acknowledged annotation base into disposable cache.
        page.wait_for_function(
            """() => {
                const pdf = (window.prksGetFocusedTabContext &&
                    window.prksGetFocusedTabContext().getResource('pdf'));
                return !!(pdf && pdf.annotationBaseReady);
            }""",
            timeout=20000,
        )
        _pdf_open_markup_tool(page, "Highlight")
        page.keyboard.press("Escape")

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        self.assertIn(work_a, page.evaluate("() => location.hash"))
        _wait_pdf_viewer(page)
        page.wait_for_function(
            """() => {
                const pdf = (window.prksGetFocusedTabContext &&
                    window.prksGetFocusedTabContext().getResource('pdf'));
                return !!(pdf && pdf.mode === 'work' && pdf.annotationMutationDurable);
            }""",
            timeout=20000,
        )
        self.assertEqual(_pdf_mode(page), "work")
        self.assertTrue(_pdf_markup_tools_available(page))
        self.assertEqual(
            page.evaluate(
                """() => {
                    const pdf = (window.prksGetFocusedTabContext &&
                        window.prksGetFocusedTabContext().getResource('pdf'));
                    return pdf ? (pdf.annotationMutationReason || '') : '';
                }"""
            ),
            "offline_durable",
        )

    def test_inplace_pdf_materialization_refreshes_whole_file_cache(self):
        """In-place annotation materialization replaces prks-pdf-v1.

        Opening a PDF primes the whole-file cache. A later exclusive POST /pdf
        keeps the same /api/pdfs/ path (no COW). Range loads never rewrite that
        entry, so the cache must take the bytes just accepted by the server.
        Offline reopen then serves that generation, not the pre-edit file.
        """
        server, page, context, collector = self._start()
        work_a = server.ids["work_a"]
        pdf_path = "/api/pdfs/%s" % server.ids["pdf_name"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        _wait_pdf_whole_file_cached(page, pdf_path)
        page.wait_for_function(
            """() => {
                const pdf = (window.prksGetFocusedTabContext &&
                    window.prksGetFocusedTabContext().getResource('pdf'));
                return !!(pdf && pdf.annotationBaseReady && pdf.annotationMutationDurable);
            }""",
            timeout=20000,
        )
        before = _pdf_cache_fingerprint(page, pdf_path)
        self.assertIsNotNone(before)
        self.assertGreater(before["length"], 0)

        collector.reset_handshake()
        since = _sync_success_at(page)
        _commit_pdf_highlight(page)
        collector.wait_pdf_handshake(page, since_ms=since)
        _wait_pdf_cache_replaced(page, pdf_path, before)
        after = _pdf_cache_fingerprint(page, pdf_path)
        self.assertIsNotNone(after)
        self.assertNotEqual(
            (after["length"], after["hash"]),
            (before["length"], before["hash"]),
        )
        # Exclusive file: materialization must not retarget the managed path.
        self.assertEqual(
            page.evaluate(
                "() => { const pdf = %s; return pdf ? String(pdf.filePath || '').split('?')[0] : ''; }"
                % _FOCUSED_PDF
            ),
            pdf_path,
        )
        # The PDF save drops the disposable Work snapshot. Re-read that JSON
        # while still online so an offline reload can mount the page. This
        # must not be a PDF GET: a whole-file network response would rewrite
        # prks-pdf-v1 on its own and hide a missing cache seed.
        warmed = page.evaluate(
            """async (id) => {
                const result = await prksOfflineReadEntity(
                    'work', id, '/api/works/' + encodeURIComponent(id));
                return !!(result && result.value && result.value.id === id);
            }""",
            work_a,
        )
        self.assertTrue(warmed)
        _wait_entity_cached(page, "work", work_a)
        self.assertEqual(_pdf_cache_fingerprint(page, pdf_path), after)

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        self.assertIn(work_a, page.evaluate("() => location.hash"))
        _wait_pdf_viewer(page)
        offline_cache = _pdf_cache_fingerprint(page, pdf_path)
        self.assertEqual(offline_cache, after)
        served = page.evaluate(
            """async (path) => {
                const res = await fetch(path);
                if (!res.ok) return { status: res.status, length: 0, hash: 0 };
                const bytes = new Uint8Array(await res.arrayBuffer());
                let hash = 2166136261;
                for (let i = 0; i < bytes.length; i++) {
                    hash ^= bytes[i];
                    hash = Math.imul(hash, 16777619);
                }
                return { status: res.status, length: bytes.length, hash: hash >>> 0 };
            }""",
            pdf_path,
        )
        self.assertEqual(served["status"], 200)
        self.assertEqual(
            (served["length"], served["hash"]),
            (after["length"], after["hash"]),
        )
        page.wait_for_function(
            """() => {
                const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                const pdf = ctx && ctx.getResource ? ctx.getResource('pdf') : null;
                const v = pdf && pdf.viewer;
                return !!(v && v.getAnnotations && v.getAnnotations().length >= 1);
            }""",
            timeout=20000,
        )

    def test_offline_research_notes_toolbar_and_pickers_stay_live(self):
        """Reopening a cached Work offline leaves Research Notes editable
        through every PRKS-owned edit path: keyboard, mutating EasyMDE
        toolbar buttons, and Concept/Argument pickers. Creating an
        Argument from the picker is durable, so it never POSTs
        /api/arguments while unreachable."""
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

        page.locator(".CodeMirror").click()
        page.keyboard.type(" OFFLINE-LIVE")
        page.wait_for_timeout(150)
        self.assertIn("OFFLINE-LIVE", page.evaluate("() => %s.value()" % _FOCUSED_WORK_NOTES))

        for cls in ("bold", "italic", "heading", "quote", "unordered-list", "ordered-list", "link", "image"):
            self.assertFalse(
                page.evaluate(
                    "(c) => { const b = document.querySelector('.editor-toolbar button.' + c); return !!(b && b.disabled); }",
                    cls,
                ),
                "expected .%s toolbar button enabled while offline" % cls,
            )
        for cls in ("preview", "side-by-side", "fullscreen"):
            self.assertFalse(
                page.evaluate(
                    "(c) => { const b = document.querySelector('.editor-toolbar button.' + c); return !!(b && b.disabled); }",
                    cls,
                ),
                "expected .%s toolbar button to remain enabled while offline" % cls,
            )

        self.assertFalse(
            page.evaluate(
                """() => { const b = document.querySelector('.editor-toolbar button.prks-insert-concept'); return !!(b && b.disabled); }"""
            )
        )
        page.evaluate("() => document.querySelector('.editor-toolbar button.prks-insert-concept').click()")
        page.wait_for_selector("#prks-research-picker .prks-dialog")
        page.evaluate("() => window.prksCloseResearchPicker && window.prksCloseResearchPicker()")
        page.wait_for_function("() => !document.getElementById('prks-research-picker')")

        self.assertFalse(
            page.evaluate(
                """() => { const b = document.querySelector('.editor-toolbar button.prks-insert-argument'); return !!(b && b.disabled); }"""
            )
        )

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
        self.assertIn(
            "[[argument:e2e-fixture-argument|Existing Fixture Argument]]",
            page.evaluate("() => %s.value()" % _FOCUSED_WORK_NOTES),
        )
        self.assertEqual(page.locator("#prks-modal-confirm:not(.hidden)").count(), 0)

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
        page.wait_for_function(
            """() => {
                const ctx = window.prksGetFocusedTabContext();
                const text = ctx.getResource('workNotes').editor.value();
                return text.indexOf('[[argument:') !== -1 && text.indexOf('Offline Created Argument') !== -1;
            }"""
        )
        self.assertEqual(argument_posts, [])
        self.assertNotEqual(page.evaluate("() => %s.value()" % _FOCUSED_WORK_NOTES), original_text)

    def test_pending_annotation_survives_disconnect_and_resumes_on_reconnect(self):
        """Scenario 11 (V2): an annotation created while ONLINE is durable in
        local-store before sync ACK. Holding `/api/sync/operations` must not
        lose the highlight; going offline keeps work-capable tools when PDF
        bytes + base are present; reconnect drains the durable queue."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]
        pdf_path = "/api/pdfs/%s" % server.ids["pdf_name"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        _wait_pdf_whole_file_cached(page, pdf_path)
        page.wait_for_function(
            """() => {
                const pdf = (window.prksGetFocusedTabContext &&
                    window.prksGetFocusedTabContext().getResource('pdf'));
                return !!(pdf && pdf.annotationBaseReady && pdf.annotationMutationDurable);
            }""",
            timeout=20000,
        )

        held = []
        sync_post_count = [0]

        def hold_sync_operations(route):
            req = route.request
            if req.method == "POST" and urlparse(req.url).path == "/api/sync/operations":
                sync_post_count[0] += 1
                held.append(route)
                return
            route.fallback()

        page.route("**/api/sync/operations", hold_sync_operations)
        try:
            _commit_pdf_highlight(page)
            wait_for_async(
                page,
                """() => prksSync.store.listOperations().then(rows => rows.some(
                    r => r && (r.operation === 'CREATE_PDF_ANNOTATION'
                        || r.operation === 'SET_PDF_ANNOTATION')
                    && r.entity_id === %s))"""
                % json.dumps(work_a),
                timeout=20000,
            )
            deadline = time.time() + 12
            while time.time() < deadline and not held:
                page.wait_for_timeout(50)
            self.assertTrue(held, "durable annotation sync POST did not start")
            annotation_count_before = _viewer_annotation_count(page)
            self.assertGreaterEqual(annotation_count_before, 1)

            context.set_offline(True)
            page.wait_for_function(
                "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
                timeout=20000,
            )

            self.assertEqual(_viewer_annotation_count(page), annotation_count_before)
            self.assertTrue(page.evaluate("() => !!%s" % _FOCUSED_VIEWER))
            self.assertEqual(_pdf_mode(page), "work")
            self.assertTrue(_pdf_markup_tools_available(page))

            page.wait_for_timeout(1500)
            self.assertEqual(len(held), 1, "sync retried a request while offline")
            wait_for_async(
                page,
                """() => prksSync.store.listOperations().then(rows => rows.some(
                    r => r && (r.operation === 'CREATE_PDF_ANNOTATION'
                        || r.operation === 'SET_PDF_ANNOTATION')
                    && r.entity_id === %s))"""
                % json.dumps(work_a),
                timeout=5000,
            )

            context.set_offline(False)
            page.wait_for_function(
                "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'online'",
                timeout=20000,
            )
            deadline = time.time() + 20
            while time.time() < deadline and held:
                _continue_held_routes(held)
                held.clear()
                page.wait_for_timeout(100)
                # New sync attempts may appear after resume.
                page.wait_for_timeout(50)
            wait_for_async(
                page,
                "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
                timeout=30000,
            )
            self.assertGreaterEqual(sync_post_count[0], 1)
            # Reconnect can leave mode===work while catch-up still locks input
            # and hides markup tools. Sibling reconnect tests wait for tools;
            # asserting immediately races remount under parallel load (#64).
            page.wait_for_function(_PDF_WORK_CAPABLE_ONLINE_JS, timeout=20000)
            _wait_pdf_markup_tools_settled(page)
            self.assertEqual(_pdf_mode(page), "work")
            self.assertTrue(_pdf_markup_tools_available(page))
        finally:
            _continue_held_routes(held)
            try:
                page.unroute("**/api/sync/operations", hold_sync_operations)
            except Exception:
                pass

    def test_reconnect_probe_race_settles_pdf_to_online_work_capable_state(self):
        """Scenario 12 (V2): a PDF mounted offline in durable 'work' mode, with
        the reachability probe held while network access is restored, must
        settle to a single online Work-capable viewer — never a duplicate
        mount, never a leftover preview when prerequisites still hold."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]
        pdf_path = "/api/pdfs/%s" % server.ids["pdf_name"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        _wait_pdf_whole_file_cached(page, pdf_path)
        page.wait_for_function(
            """() => {
                const pdf = (window.prksGetFocusedTabContext &&
                    window.prksGetFocusedTabContext().getResource('pdf'));
                return !!(pdf && pdf.annotationBaseReady);
            }""",
            timeout=20000,
        )

        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        self.assertIn(work_a, page.evaluate("() => location.hash"))
        _wait_pdf_viewer(page)
        page.wait_for_function(
            """() => {
                const pdf = (window.prksGetFocusedTabContext &&
                    window.prksGetFocusedTabContext().getResource('pdf'));
                return !!(pdf && pdf.mode === 'work' && pdf.annotationMutationDurable);
            }""",
            timeout=20000,
        )
        self.assertEqual(_pdf_mode(page), "work")
        self.assertTrue(_pdf_markup_tools_available(page))
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
            timeout=20000,
        )
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
            context.set_offline(False)
            deadline = time.time() + 12
            while time.time() < deadline and not held_probe:
                page.wait_for_timeout(50)
            self.assertTrue(held_probe, "reachability probe did not start")

            page.wait_for_timeout(200)
            self.assertNotEqual(_connectivity_state(page), "online")
            # Prerequisites still hold → stay work while probe is in flight.
            self.assertEqual(_pdf_mode(page), "work")

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
                page.locator('[data-prks-role="pdf-viewer"] .prks-pdf-toolbar').count(),
                1,
            )
            _wait_pdf_markup_tools_settled(page)
            self.assertTrue(_pdf_markup_tools_available(page))
        finally:
            _continue_held_routes(held_probe)
            try:
                page.unroute("**/api/**", hold_reachability_gets)
            except Exception:
                pass

    def test_rapid_connectivity_transitions_settle_to_latest_state(self):
        """Scenario 13: online -> offline -> online (rapid) must end in an
        online, mutation-capable viewer; offline -> online -> offline (rapid)
        must end work-capable when PDF bytes + annotation base remain, else
        preview. Exactly one PDF viewer container remains mounted."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]
        pdf_path = "/api/pdfs/%s" % server.ids["pdf_name"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        _wait_pdf_whole_file_cached(page, pdf_path)
        page.wait_for_function(
            """() => {
                const pdf = (window.prksGetFocusedTabContext &&
                    window.prksGetFocusedTabContext().getResource('pdf'));
                return !!(pdf && pdf.annotationBaseReady);
            }""",
            timeout=20000,
        )

        # online -> offline -> online, rapid.
        context.set_offline(True)
        page.wait_for_timeout(300)
        context.set_offline(False)
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'online'",
            timeout=20000,
        )
        page.wait_for_function(_PDF_WORK_CAPABLE_ONLINE_JS, timeout=20000)
        # Coherent catch-up holds the materialization gate (and hides markup
        # tools) while projecting /annotations-snapshot into the live viewer.
        # Settlement means the gate is released AND tools are reachable again —
        # mode===work alone is not enough (viewer may still be input-locked).
        _wait_pdf_markup_tools_settled(page)
        self.assertTrue(_pdf_markup_tools_available(page))
        self.assertEqual(page.locator('[data-prks-role="pdf-viewer"]').count(), 1)

        # offline -> online -> offline, rapid. With prerequisites, settle in
        # durable work mode rather than preview.
        context.set_offline(True)
        page.wait_for_timeout(300)
        context.set_offline(False)
        page.wait_for_timeout(300)
        context.set_offline(True)
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
            timeout=20000,
        )
        page.wait_for_function(
            """() => {
                const pdf = (window.prksGetFocusedTabContext &&
                    window.prksGetFocusedTabContext().getResource('pdf'));
                return !!(pdf && pdf.mode === 'work' && pdf.annotationMutationDurable
                    && !pdf._annotationMaterializing);
            }""",
            timeout=20000,
        )
        self.assertEqual(page.locator('[data-prks-role="pdf-viewer"]').count(), 1)
        _wait_pdf_markup_tools_settled(page)
        self.assertTrue(_pdf_markup_tools_available(page))
        context.set_offline(False)

    def test_ctrl_b_shortcut_alters_notes_while_offline(self):
        """EasyMDE's Ctrl/Cmd-B calls `cm.replaceSelection()` directly.
        Research Notes are durable, so the shortcut must still apply while
        unreachable -- there is no beforeChange connectivity barrier."""
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
        self.assertNotEqual(page.evaluate("() => %s.value()" % _FOCUSED_WORK_NOTES), original_text)

    def test_stale_autocomplete_picker_still_mutates_notes_offline(self):
        """Opening a wiki/concept/PDF-annotation autocomplete dropdown while
        online, then losing connectivity before picking, must still apply the
        completion: Research Notes are durable. PDF annotation POSTs stay
        forbidden -- that family is not durable."""
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
            page.wait_for_function("() => !document.querySelector('.CodeMirror-hints')")
            self.assertNotEqual(
                notes_text(),
                text_with_dropdown_open,
                "%s completion pick must still apply while offline" % label,
            )
            self.assertEqual(page.locator("#prks-modal-confirm:not(.hidden)").count(), 0)

            close_any_open_hints()
            context.set_offline(False)
            page.wait_for_function(
                "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'online'",
                timeout=20000,
            )
            # Undo the picked completion so the next iteration starts clean.
            page.evaluate(
                "(text) => %s.value(text)" % _FOCUSED_WORK_NOTES,
                before_trigger,
            )
            page.wait_for_timeout(50)

        # The one real PDF annotation POST already happened (and settled)
        # before this listener was attached -- none of the offline
        # completion-pick attempts below may cause another.
        self.assertEqual(annotation_post_count[0], 0)

    def test_active_markup_tool_stays_on_disconnect_when_durable(self):
        """Scenario 16 (V2): when PDF bytes + annotation base + durable store
        are available, disconnecting must NOT clear an active Highlight tool
        or force preview — capability keeps work mode and a drag still
        enqueues durable intent (no canonical HTTP while offline)."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]
        pdf_path = "/api/pdfs/%s" % server.ids["pdf_name"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        _wait_pdf_whole_file_cached(page, pdf_path)
        page.wait_for_function(
            """() => {
                const pdf = (window.prksGetFocusedTabContext &&
                    window.prksGetFocusedTabContext().getResource('pdf'));
                return !!(pdf && pdf.annotationBaseReady && pdf.annotationMutationDurable);
            }""",
            timeout=20000,
        )
        annotation_count_before = _viewer_annotation_count(page)

        mutation_requests = []
        page.on(
            "request",
            lambda req: mutation_requests.append(req.method)
            if req.method in ("POST", "PUT", "PATCH", "DELETE") and "/api/works/" in req.url
            else None,
        )

        _pdf_open_markup_tool(page, "Highlight")
        page.wait_for_function(
            """() => {
                const pdf = (window.prksGetFocusedTabContext &&
                    window.prksGetFocusedTabContext().getResource('pdf'));
                return !!(pdf && pdf.mode === 'work' && pdf.viewer);
            }"""
        )
        page.wait_for_function(
            """() => {
                const buttons = document.querySelectorAll(
                    '[data-prks-role="pdf-viewer"] .prks-pdf-toolbar [aria-label="Highlight"],'
                    + '[data-prks-role="pdf-viewer"] .prks-pdf-toolbar__menu [aria-label="Highlight"]');
                return [...buttons].some(b => b.getAttribute('aria-pressed') === 'true');
            }"""
        )

        context.set_offline(True)
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) !== 'online'",
            timeout=20000,
        )
        page.wait_for_function(
            """() => {
                const pdf = (window.prksGetFocusedTabContext &&
                    window.prksGetFocusedTabContext().getResource('pdf'));
                return !!(pdf && pdf.mode === 'work' && pdf.annotationMutationDurable);
            }""",
            timeout=20000,
        )
        self.assertEqual(_pdf_mode(page), "work")

        geo = _pdf_selection_geometry(page)
        page.mouse.move(geo["sx"], geo["sy"])
        page.mouse.down()
        page.mouse.move(geo["ex"], geo["ey"], steps=12)
        page.mouse.up()
        page.wait_for_timeout(400)

        self.assertGreaterEqual(_viewer_annotation_count(page), annotation_count_before + 1)
        wait_for_async(
            page,
            """() => prksSync.store.listOperations().then(rows => rows.some(
                r => r && (r.operation === 'CREATE_PDF_ANNOTATION'
                    || r.operation === 'SET_PDF_ANNOTATION')
                && r.entity_id === %s))"""
            % json.dumps(work_a),
            timeout=20000,
        )
        self.assertEqual(mutation_requests, [])

        context.set_offline(False)
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'online'",
            timeout=20000,
        )
        page.wait_for_function(_PDF_WORK_CAPABLE_ONLINE_JS, timeout=20000)
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=30000,
        )

    def test_persistence_setup_abandons_on_disconnect_before_worker_install(self):
        """Scenario 17 (AGENTS.md "an async annotation-persistence setup...
        must re-check current viewer identity, runtime.mode, and
        connectivity... after every await boundary"): holding the initial
        `GET /api/works/<id>/annotations-snapshot` past an offline transition must
        make setup abandon rather than install an active worker, and must
        reset `_persistenceSetupStarted` so a later online reconcile can
        retry exactly once."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]

        held = []

        def hold_annotations_get(route):
            req = route.request
            path = urlparse(req.url).path
            if req.method == "GET" and path == "/api/works/%s/annotations-snapshot" % work_a:
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
            self.assertTrue(held, "initial GET /annotations-snapshot did not start")
            self.assertEqual(len(held), 1)
            # Awaiting coherent base must not leave a mutation-capable viewer.
            self.assertEqual(_pdf_mode(page), "preview")
            self.assertFalse(
                page.evaluate(
                    "() => { const pdf = %s; return !!(pdf && pdf.annotationMutationAllowed); }"
                    % _FOCUSED_PDF
                )
            )

            context.set_offline(True)
            page.wait_for_function(
                "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) !== 'online'",
                timeout=20000,
            )
            # Annotation snapshot still held ⇒ no acknowledged base yet ⇒ preview.
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

    def test_held_annotations_snapshot_blocks_untracked_mutation(self):
        """Held `/annotations-snapshot` must keep mutation disabled so the user
        cannot create an untracked annotation during durable-bridge startup."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]

        held = []

        def hold_snapshot(route):
            req = route.request
            path = urlparse(req.url).path
            if req.method == "GET" and path == "/api/works/%s/annotations-snapshot" % work_a:
                held.append(route)
                return
            route.fallback()

        page.route("**/api/works/**", hold_snapshot)
        try:
            _wait_sw_active(page)
            _open_work_from_home(page, WORK_A_TITLE)
            _wait_pdf_viewer(page)

            deadline = time.time() + 12
            while time.time() < deadline and not held:
                page.wait_for_timeout(50)
            self.assertTrue(held, "initial GET /annotations-snapshot did not start")

            page.wait_for_function(
                """() => {
                    const pdf = (window.prksGetFocusedTabContext &&
                        window.prksGetFocusedTabContext().getResource('pdf'));
                    return !!(pdf && pdf.mode === 'preview');
                }""",
                timeout=10000,
            )
            self.assertEqual(_pdf_mode(page), "preview")
            self.assertFalse(_pdf_markup_tools_available(page))
            self.assertFalse(
                page.evaluate(
                    """() => {
                        const pdf = %s;
                        return !!(pdf && pdf.annotationDurableBridgeReady);
                    }""" % _FOCUSED_PDF
                )
            )
            before_ops = page.evaluate(
                "() => prksSync.store.listOperations().then(r => r.length)"
            )
            # Attempt a highlight while snapshot is held — must not create
            # durable intent or leave an untracked viewer annotation accepted.
            try:
                _commit_pdf_highlight(page)
            except Exception:
                pass
            page.wait_for_timeout(400)
            after_ops = page.evaluate(
                "() => prksSync.store.listOperations().then(r => r.length)"
            )
            self.assertEqual(before_ops, after_ops)
            self.assertEqual(_pdf_mode(page), "preview")

            _continue_held_routes(held)
            held.clear()
            page.unroute("**/api/works/**", hold_snapshot)
            page.wait_for_function(_PDF_WORK_CAPABLE_ONLINE_JS, timeout=20000)
            page.wait_for_function(
                """() => {
                    const pdf = %s;
                    return !!(pdf && pdf.annotationDurableBridgeReady
                        && pdf.annotationMutationDurable && pdf.mode === 'work');
                }""" % _FOCUSED_PDF,
                timeout=20000,
            )
            _wait_pdf_markup_tools_settled(page)
            self.assertTrue(_pdf_markup_tools_available(page))
        finally:
            _continue_held_routes(held)
            try:
                page.unroute("**/api/works/**", hold_snapshot)
            except Exception:
                pass

    def test_failed_then_successful_annotations_snapshot_becomes_editable(self):
        """Transient non-OK `/annotations-snapshot` must retry hydration and
        become work-capable without reopening the Work."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]

        snapshot_hits = {"n": 0}

        def fail_then_ok_snapshot(route):
            req = route.request
            path = urlparse(req.url).path
            if req.method == "GET" and path == "/api/works/%s/annotations-snapshot" % work_a:
                snapshot_hits["n"] += 1
                if snapshot_hits["n"] <= 2:
                    route.fulfill(
                        status=503,
                        content_type="application/json",
                        body='{"error":"temporary"}',
                    )
                    return
            route.fallback()

        page.route("**/api/works/**", fail_then_ok_snapshot)
        try:
            _wait_sw_active(page)
            _open_work_from_home(page, WORK_A_TITLE)
            _wait_pdf_viewer(page)

            page.wait_for_function(
                """() => {
                    const pdf = %s;
                    return !!(pdf && pdf.mode === 'preview'
                        && (pdf.annotationMutationReason === 'online_awaiting_base'
                            || pdf._annotationBaseHydrationNeedsRetry));
                }""" % _FOCUSED_PDF,
                timeout=15000,
            )
            self.assertEqual(_pdf_mode(page), "preview")
            self.assertFalse(_pdf_markup_tools_available(page))

            page.wait_for_function(_PDF_WORK_CAPABLE_ONLINE_JS, timeout=30000)
            page.wait_for_function(
                """() => {
                    const pdf = %s;
                    return !!(pdf && pdf.annotationBaseReady
                        && pdf.annotationDurableBridgeReady
                        && pdf.annotationMutationDurable
                        && pdf.mode === 'work');
                }""" % _FOCUSED_PDF,
                timeout=30000,
            )
            self.assertGreaterEqual(snapshot_hits["n"], 3)
            _wait_pdf_markup_tools_settled(page, timeout=30000)
            self.assertTrue(_pdf_markup_tools_available(page))
        finally:
            try:
                page.unroute("**/api/works/**", fail_then_ok_snapshot)
            except Exception:
                pass

    def test_transport_failure_while_browser_stays_online_goes_offline_and_recovers(self):
        """Server-unreachable while navigator.onLine remains true: an ordinary
        prksRequest() transport failure must flip the runtime offline without
        context.set_offline. Notes stay editable (durable); PDF stays
        work-capable when bytes + annotation base are already local."""
        server, page, context, _collector = self._start()
        work_a = server.ids["work_a"]
        pdf_path = "/api/pdfs/%s" % server.ids["pdf_name"]

        _wait_sw_active(page)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        _wait_pdf_whole_file_cached(page, pdf_path)
        _wait_entity_cached(page, "work", work_a)
        page.wait_for_selector(".CodeMirror")
        page.wait_for_function(
            """() => {
                const pdf = (window.prksGetFocusedTabContext &&
                    window.prksGetFocusedTabContext().getResource('pdf'));
                return !!(pdf && pdf.annotationBaseReady);
            }""",
            timeout=20000,
        )
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
            self.assertFalse(page.evaluate("(sel) => document.querySelector(sel).readOnly", private_selector))
            page.wait_for_function(
                """() => {
                    const pdf = (window.prksGetFocusedTabContext &&
                        window.prksGetFocusedTabContext().getResource('pdf'));
                    return !!(pdf && pdf.mode === 'work' && pdf.annotationMutationDurable);
                }""",
                timeout=20000,
            )
            self.assertEqual(_pdf_mode(page), "work")
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
    """Cached Concept routes: #/concepts and #/concepts/:conceptId (mutations are durable separately)."""

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

        # New Concept stays LIVE: a Concept is created under an id this device
        # mints, so it is real the moment it is written.
        new_btn = page.locator("#prks-concept-new")
        self.assertFalse(new_btn.is_disabled())
        self.assertIsNone(new_btn.get_attribute("aria-disabled"))

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

    def test_offline_concept_detail_edits_durably(self):
        """Every Concept mutation surface stays live offline, and an edit made
        there becomes a durable operation without any canonical request."""
        server, page, context, _collector = self._start()
        child = server.ids["concept_child"]

        _wait_sw_active(page)
        _open_concept(page, child)
        _wait_content_contains(page, CONCEPT_CHILD_NAME)
        _wait_entity_cached(page, "concept", child)
        # Editing offline needs the revisions the edit is measured against.
        # Without them the refusal is "unknown base", which is a different
        # thing from "no connection" and is the correct answer here.
        page.evaluate("id => { void Promise.resolve(prksReadConceptState(id)).catch(() => {}); }",
                      child)
        _wait_entity_cached(page, "concept-state", child)

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
            # Every Concept control stays usable offline: each one is a durable
            # decision with a revision and a defined conflict.
            for selector in (
                "#prks-concept-rename",
                "#prks-concept-delete",
                "#prks-concept-edit-def",
                "#prks-concept-edit-aliases",
                "#prks-concept-edit-parents",
            ):
                btn = page.locator(selector)
                self.assertFalse(btn.is_disabled(), "%s must stay live offline" % selector)
                self.assertIsNone(btn.get_attribute("aria-disabled"), selector)
            # And an actual edit goes through, reaching no canonical request.
            page.evaluate(
                "id => updateConcept(id, { description: 'Written with no server.' })",
                child)
            wait_for_async(
                page,
                "() => prksSync.store.listOperations().then(rows => rows.some("
                "  o => o.operation === 'SET_CONCEPT_FIELD'))",
                timeout=30000, message="the edit never became a durable operation")
            # The New Concept flow is also reachable from Work Research Notes, so
            # drive it directly: it opens its dialog offline like any other.
            # Fire and forget: the flow does not settle until the dialog is
            # answered, and page.evaluate() awaits a returned promise with no
            # timeout, so awaiting it here would deadlock against the Escape
            # below.
            page.evaluate("""() => {
                    void Promise.resolve(window.prksCreateConceptFlow('Offline concept'))
                        .catch(() => {});
                }""")
            page.locator("#prks-modal-confirm .prks-modal-prompt__input").wait_for()
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
            self.assertEqual(mutations, [],
                             "nothing canonical left the browser")
        finally:
            _safe_unroute(page, "**/api/concepts**", record_mutation)

        self.assertFalse(page.locator("#prks-concept-view-graph").is_disabled())

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

    def test_a_live_concept_page_keeps_every_control_on_disconnect(self):
        """A Concept page mounted online stays fully usable when PRKS stops
        answering, without a reload.

        This test used to assert the opposite -- that the page went read-only in
        place. The invariant it protects is unchanged and still worth holding:
        connectivity must reach a MOUNTED page, not only a freshly routed one.
        What changed is the correct answer. Every Concept control is durable, so
        the page settles to "still editable" rather than to "inert", and a save
        made in that state has to actually land.
        """
        server, page, _context, _collector = self._start()
        child = server.ids["concept_child"]

        _wait_sw_active(page)
        _open_concept(page, child)
        _wait_content_contains(page, CONCEPT_CHILD_NAME)
        page.evaluate("id => { void Promise.resolve(prksReadConceptState(id)).catch(() => {}); }",
                      child)
        _wait_entity_cached(page, "concept-state", child)
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
            for selector in (
                "#prks-concept-rename",
                "#prks-concept-delete",
                "#prks-concept-edit-def",
                "#prks-concept-edit-aliases",
                "#prks-concept-edit-parents",
                "#prks-concept-view-graph",
            ):
                self.assertFalse(page.locator(selector).is_disabled(), selector)
            # Not just enabled -- actually able to save, from the page that was
            # mounted before the connection dropped.
            page.evaluate("id => updateConcept(id, { description: 'Saved after the drop.' })",
                          child)
            wait_for_async(
                page,
                "() => prksSync.store.listOperations().then(rows => rows.some("
                "  o => o.operation === 'SET_CONCEPT_FIELD'))",
                timeout=30000,
                message="a page mounted before the drop must still be able to save")
            # Read/navigation links stay usable.
            self.assertEqual(page.locator('.prks-research-row[href$="%s"]' % server.ids["concept_parent"]).count(), 1)
        finally:
            _safe_unroute(page, "**/api/**", abort_api)

        page.evaluate("""async () => { await window.prksRequest('/api/settings'); }""")
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'online'",
            timeout=20000,
        )
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

        # A RENAME is what stales siblings, because a sibling may display the
        # old name as a parent or subconcept. A definition edit does not -- no
        # other Concept renders it -- so this test now uses the mutation whose
        # consequence it is actually describing.
        page.evaluate("id => window.updateConcept(id, { name: 'Domain coherence rename' })",
                      parent)
        _drain_durable(page)
        self.assertGreater(_concept_domain_generation(page), generation_before)
        _wait_entity_uncached(page, "concept", child)
        # The catalogue is deliberately KEPT: the acknowledgement states the
        # renamed row exactly, so it is patched rather than dropped, and an
        # offline device does not lose its Concept list to a rename.
        cached_index = _cached_list(page, "concepts:index")
        self.assertIsNotNone(cached_index)
        self.assertIn("Domain coherence rename",
                      [row["name"] for row in cached_index["value"]])

        context.set_offline(True)
        _open_concept(page, child)
        _wait_offline_unavailable(page)

    def test_a_refused_concept_operation_retains_the_concept_cache(self):
        """A durable operation the server rejects is retried, not lost -- and
        until it is acknowledged, nothing cached is touched.

        This used to reject the PATCH. There is no PATCH any more, so the
        refusal is injected where the decision actually travels: the sync
        endpoint.
        """
        server, page, context, _collector = self._start()
        child = server.ids["concept_child"]

        _wait_sw_active(page)
        _open_concept(page, child)
        _wait_entity_cached(page, "concept", child)
        page.evaluate("id => { void Promise.resolve(prksReadConceptState(id)).catch(() => {}); }",
                      child)
        _wait_entity_cached(page, "concept-state", child)
        generation_before = _concept_domain_generation(page)

        def reject_sync(route):
            if route.request.method == "POST":
                route.fulfill(status=500, content_type="application/json", body='{"error":"nope"}')
                return
            route.fallback()

        page.route("**/api/sync/operations**", reject_sync)
        try:
            page.evaluate("id => updateConcept(id, { description: 'never applied' })", child)
            wait_for_async(
                page,
                "() => prksSync.store.listOperations().then(rows => rows.some("
                "  o => o.operation === 'SET_CONCEPT_FIELD'))",
                timeout=30000, message="the edit was never enqueued")
            page.wait_for_timeout(800)
            queued = page.evaluate(
                "() => prksSync.store.listOperations().then(rows => rows.length)")
            self.assertGreater(queued, 0, "a refused operation is retried, never dropped")
            self.assertEqual(_concept_domain_generation(page), generation_before)
            self.assertIsNotNone(_cached_entity(page, "concept", child))
        finally:
            _safe_unroute(page, "**/api/sync/operations**", reject_sync)

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
            # A Concept mutation is ACKNOWLEDGED while that read is still in
            # flight. A rename, because that is the change whose consequence
            # reaches siblings -- and the fence is what stops this pre-rename
            # body from publishing afterwards.
            page.evaluate("id => window.updateConcept(id, { name: 'Stale read rename' })",
                          parent)
            _drain_durable(page)
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
        """Save #1 ACKed even if a later save cannot leave the device: the first
        canonical write already fenced Concepts."""
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

        def reject_sync(route):
            route.fulfill(status=500, content_type="application/json", body='{"error":"nope"}')

        page.route("**/api/sync/operations", reject_sync)
        try:
            page.locator(".work-notes-editor-wrap .CodeMirror").first.click()
            page.keyboard.press("Control+A")
            page.keyboard.insert_text("second save that stays local")
            page.evaluate("""() => {
                const ctx = window.prksGetFocusedTabContext();
                window.prksFlushPendingWorkResearchNotes(ctx);
            }""")
            wait_for_async(page,
                """() => prksSync.store.listOperations().then(rows =>
                    rows.some(r => r.operation === 'SET_WORK_RESEARCH_NOTE'))""",
                timeout=15000,
            )
        finally:
            _safe_unroute(page, "**/api/sync/operations", reject_sync)

        self.assertGreaterEqual(generation_after_first, 1)
        self.assertIsNone(_cached_entity(page, "concept", child))

    def test_a_rename_reconciles_the_cached_concept_rather_than_dropping_it(self):
        """Cached Concept details carry Work mention titles -- and a Work Title
        is local-first, so the acknowledgement patches the exact new title into
        them. This test used to assert the opposite: that the save INVALIDATED
        the whole Concepts domain. Destroying a usable offline Concept for a
        change whose shape is already known is what the reconciler exists to
        avoid."""
        server, page, _context, _collector = self._start()
        child = server.ids["concept_child"]

        _wait_sw_active(page)
        _open_concept(page, child)
        _wait_entity_cached(page, "concept", child)
        _open_work_from_home(page, WORK_A_TITLE)
        new_title = "Concept Mention Title Changed"
        page.locator("#panel-content button", has_text="Edit metadata").click()
        _rename_work_durably(page, new_title)

        wait_for_async(page,
            """([id, title]) => window.createPrksOfflineStore().getEntity('concept', id)
                .then(row => !!row && (row.value.mentions || []).some(m => m.title === title))""",
            arg=[child, new_title],
            timeout=15000,
        )
        self.assertIsNotNone(_cached_entity(page, "concept", child),
                             "the snapshot was patched, not thrown away")

    def test_a_rename_the_server_rejects_leaves_the_concept_cache_alone(self):
        server, page, _context, _collector = self._start()
        child = server.ids["concept_child"]

        _wait_sw_active(page)
        _open_concept(page, child)
        _wait_entity_cached(page, "concept", child)
        _open_work_from_home(page, WORK_A_TITLE)
        generation_before = _concept_domain_generation(page)

        def reject_sync(route):
            route.fulfill(status=500, content_type="application/json", body='{"error":"nope"}')

        page.route("**/api/sync/operations", reject_sync)
        try:
            page.locator("#panel-content button", has_text="Edit metadata").click()
            page.locator('[data-prks-work-field="title"]').fill("Rejected title")
            page.locator("#save-work-identity-btn").click()
            page.wait_for_timeout(900)
            # Nothing was acknowledged, so nothing was reconciled -- and
            # nothing was invalidated either.
            self.assertEqual(_concept_domain_generation(page), generation_before)
            self.assertIsNotNone(_cached_entity(page, "concept", child))
            # The intent is still saved locally and will retry.
            rows = page.evaluate(
                "() => prksSync.store.listOperations().then(r => r.length)")
            self.assertGreaterEqual(rows, 1)
        finally:
            _safe_unroute(page, "**/api/sync/operations", reject_sync)

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

    def test_playlist_inline_work_rename_uses_the_same_durable_title_path(self):
        """A Work Title can also be changed from a Playlist -- and it is a WORK
        Title, not Playlist state, so it takes the same durable operation the
        metadata editor uses and reaches every cached representation the same
        way. This test used to assert that the Playlist surface owed the
        Concepts domain an INVALIDATION; it now owes it a reconciliation."""
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
        # One durable Title operation, exactly as the metadata editor enqueues.
        wait_for_async(page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=15000)

        # The cached Concept keeps its snapshot and gains the new title.
        wait_for_async(page,
            """([id, title]) => window.createPrksOfflineStore().getEntity('concept', id)
                .then(row => !!row && (row.value.mentions || []).some(m => m.title === title))""",
            arg=[child, renamed],
            timeout=15000,
        )
        # The domain's coherence generation DOES advance -- that fence stops a
        # Concept GET issued before the acknowledgement from publishing its
        # pre-rename body afterwards. It is not an invalidation: the wait above
        # already proved the snapshot survived and gained the exact new title,
        # which is the whole difference between reconciling and discarding.
        self.assertGreater(_concept_domain_generation(page), generation_before,
                           "the domain holding this Work is fenced")

        # And the Concept still serves offline, now showing the new title.
        context.set_offline(True)
        _open_concept(page, child)
        _wait_content_contains(page, renamed)
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
        wait_for_async(page,
            "id => window.createPrksOfflineStore().getEntity('work', id).then(row => row === null)",
            arg=work_a,
            timeout=15000,
        )
        self.assertEqual(_concept_domain_generation(page), generation_before)
        self.assertIsNotNone(_cached_entity(page, "concept", child))


class OfflinePositionTests(unittest.TestCase):
    """Cached Position routes: #/positions and #/positions/:positionId (mutations are durable separately)."""

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

        # New Position stays LIVE: a Position is created under an id this
        # device mints, so it is real the moment it is written -- and can be an
        # Argument's target before any server has heard of it.
        new_btn = page.locator("#prks-position-new")
        self.assertFalse(new_btn.is_disabled())
        self.assertIsNone(new_btn.get_attribute("aria-disabled"))

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
        # A cached-empty list is an ANSWER, and creating into it works offline.
        self.assertFalse(page.locator("#prks-position-new-empty").is_disabled())
        self.assertFalse(page.locator("#prks-position-new").is_disabled())

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
        # Graph navigation remains enabled; its destination owns availability.
        page.wait_for_function(
            "() => !!document.querySelector('#prks-position-view-graph:not([disabled])')", timeout=20000
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

    def test_position_graph_action_navigates_to_uncached_graph_offline(self):
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
        self.assertFalse(page.locator("#prks-position-view-graph").is_disabled())
        page.locator("#prks-position-view-graph").click()
        _wait_offline_unavailable(page)
        self.assertIn("#/graph?focus=position:", page.evaluate("decodeURIComponent(location.hash)"))

    def test_offline_position_create_is_durable(self):
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
            self.assertFalse(btn.is_disabled())
            self.assertIsNone(btn.get_attribute("aria-disabled"))
            btn.click()
            prompt_input = page.locator("#prks-modal-confirm .prks-modal-prompt__input")
            prompt_input.wait_for()
            prompt_input.fill("Created with no server")
            page.locator("#prks-modal-confirm-ok").click()
            wait_for_async(
                page,
                "() => prksSync.store.listOperations().then(rows => rows.some("
                "  o => o.operation === 'CREATE_POSITION'))",
                timeout=30000, message="the creation was never enqueued")
            self.assertEqual(mutations, [],
                             "no canonical Position request left the browser")
        finally:
            _safe_unroute(page, "**/api/positions**", record_mutation)

    def test_disconnect_while_position_prompt_open_still_creates(self):
        """A prompt opened online and confirmed after the connection dropped
        must not lose what the user typed.

        This used to assert that the create was refused. The invariant it
        protects -- a disconnect mid-dialog never produces a silent half-action
        -- is unchanged; the correct outcome is now that the Position is
        created durably rather than discarded.
        """
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
            wait_for_async(
                page,
                "() => prksSync.store.listOperations().then(rows => rows.some("
                "  o => o.operation === 'CREATE_POSITION'))",
                timeout=30000, message="the creation was never enqueued")
            self.assertEqual(mutations, [],
                             "no canonical Position request may be attempted after disconnect")
        finally:
            _safe_unroute(page, "**/api/**", block_api)

        page.evaluate("""async () => { try { await window.prksRequest('/api/settings'); } catch (_e) {} }""")
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'online'",
            timeout=20000,
        )
        _drain_durable(page)
        names = page.evaluate("() => fetchPositions().then(items => items.map(p => p.name))")
        self.assertIn("Created while disconnected", names,
                      "what the user typed before the drop reaches the server after it")

    def test_live_position_pages_stay_usable_on_disconnect(self):
        """Pages mounted online stay fully usable when PRKS stops answering,
        without a reload.

        This used to assert they went read-only in place. The invariant is
        unchanged -- connectivity must reach a MOUNTED page, not only a freshly
        routed one -- but every Position control is durable, so the page settles
        to "still editable" rather than to "inert".
        """
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
            page.wait_for_timeout(300)
            self.assertFalse(page.locator("#prks-position-new").is_disabled())
        finally:
            _safe_unroute(page, "**/api/**", abort_api)

        page.evaluate("""async () => { await window.prksRequest('/api/settings'); }""")
        self.assertFalse(page.locator("#prks-position-new").is_disabled())

        # Detail navigation remains enabled; content stays readable.
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
                "() => !!document.querySelector('#prks-position-view-graph:not([disabled])')", timeout=20000
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

        page.evaluate("id => window.updatePosition(id, { description: 'Domain coherence check.' })",
                      position_a)
        _drain_durable(page)
        # The Positions domain generation moves, which is the FENCE that stops a
        # read begun before the acknowledgement from publishing afterwards. It
        # is not an invalidation any more: the answer states the changed row
        # exactly, so both the list and the Position that did not change are
        # PATCHED rather than dropped -- an offline device must not lose its
        # Position list to somebody else's description edit.
        self.assertGreater(_domain_generation(page, "positions"), generation_before)
        self.assertIsNotNone(_cached_entity(page, "position", position_b))
        cached_index = _cached_list(page, "positions:index")
        self.assertIsNotNone(cached_index)

        context.set_offline(True)
        _open_position(page, position_a)
        _wait_content_contains(page, "Domain coherence check.")

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
        _drain_durable(page)
        self.assertGreater(_domain_generation(page, "positions"), generation_before)
        _wait_entity_uncached(page, "position", position_a)
        _wait_list_uncached(page, "positions:index")

        context.set_offline(True)
        _open_position(page, position_a)
        _wait_offline_unavailable(page)
        self.assertNotIn(POSITION_ARGUMENT_NAME, _content_text(page))

    def test_failed_argument_update_retains_the_position_cache(self):
        server, page, context, _collector = self._start()
        position_a = server.ids["position_a"]
        argument_id = server.ids["position_argument"]

        _wait_sw_active(page)
        _open_position(page, position_a)
        _wait_entity_cached(page, "position", position_a)
        _open_argument(page, argument_id)
        _wait_entity_cached(page, "argument", argument_id)
        page.evaluate("id => prksReadArgumentState(id)", argument_id)
        _wait_entity_cached(page, "argument-state", argument_id)
        _open_position(page, position_a)
        _wait_content_contains(page, POSITION_A_NAME)
        generation_before = _domain_generation(page, "positions")
        context.set_offline(True)
        page.evaluate("() => prksOfflineNoteRequestFailure()")
        page.evaluate("id => window.updateArgument(id, { name: 'retained offline' })",
                      argument_id)
        wait_for_async(page, "() => prksSync.store.listOperations().then(rows => rows.some("
                       "op => op.operation === 'SET_ARGUMENT_FIELD' && op.status === 'pending'))")
        self.assertEqual(_domain_generation(page, "positions"), generation_before)
        self.assertIsNotNone(_cached_entity(page, "position", position_a))

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
        _drain_durable(page)
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
        _drain_durable(page)
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
        _drain_durable(page)
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
            _drain_durable(page)
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
        _drain_durable(page)
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

        concepts_before = _domain_generation(page, "concepts")
        page.evaluate(
            "id => window.updateConcept(id, { name: 'Domain independence rename' })", concept_child
        )
        _drain_durable(page)
        # The Concept domain moved -- a rename reaches every sibling that may
        # display the old name -- and Positions did not move at all.
        self.assertGreater(_domain_generation(page, "concepts"), concepts_before)
        self.assertEqual(_domain_generation(page, "positions"), positions_before)
        self.assertFalse(_domain_blocked(page, "positions"))
        self.assertIsNotNone(_cached_entity(page, "position", position_a))

        context.set_offline(True)
        _open_position(page, position_a)
        _wait_offline_banner(page)
        _wait_content_contains(page, POSITION_A_NAME)


class OfflineArgumentTests(unittest.TestCase):
    """Cached Argument/Stance routes: #/arguments and #/arguments/:id (mutations are durable separately)."""

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

        self.assertFalse(page.locator("#prks-argument-new").is_disabled())
        self.assertFalse(page.locator("#prks-stance-new").is_disabled())

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
        "No Stances yet." empty state -- with durable creation available."""
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
        self.assertFalse(page.locator("#prks-stance-new-empty").is_disabled())

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

    def test_argument_graph_action_navigates_to_uncached_graph_offline(self):
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
        self.assertFalse(page.locator("#prks-arg-view-graph").is_disabled())
        page.locator("#prks-arg-view-graph").click()
        _wait_offline_unavailable(page)
        self.assertIn("#/graph?focus=argument:", page.evaluate("decodeURIComponent(location.hash)"))

    def test_offline_argument_index_and_detail_mutate_through_durable_queue(self):
        server, page, context, _collector = self._start()
        argument_a = server.ids["argument_a"]

        _wait_sw_active(page)
        _open_argument_index(page)
        _wait_list_cached(page, "arguments:index")
        _open_argument(page, argument_a)
        _wait_entity_cached(page, "argument", argument_a)
        page.evaluate("id => prksReadArgumentState(id)", argument_a)
        _wait_entity_cached(page, "argument-state", argument_a)

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
                self.assertFalse(btn.is_disabled(), "%s must stay available offline" % selector)
                self.assertNotEqual(btn.get_attribute("aria-disabled"), "true", selector)
            page.locator("#prks-arg-edit").click()
            page.locator("#prks-arg-form").wait_for()
            self.assertFalse(page.locator("#prks-arg-form button[type=submit]").is_disabled())

            _open_argument_index(page)
            _wait_content_contains(page, ARGUMENT_A_NAME)
            for selector in ("#prks-argument-new", "#prks-stance-new"):
                btn = page.locator(selector)
                self.assertFalse(btn.is_disabled(), selector)

            # The Work Research Notes creation path is a second mutation surface.
            page.evaluate("() => window.prksCreateArgumentFromWork({ name: 'Offline argument' })")
            wait_for_async(page, "() => prksSync.store.listOperations().then(rows => rows.some("
                           "op => op.operation === 'CREATE_ARGUMENT'))")
            self.assertFalse(any(path.startswith('/api/arguments') for _method, path in mutations),
                             "durable mutation must not bypass /api/sync/operations")
        finally:
            _safe_unroute(page, "**/api/arguments**", record_mutation)

    def test_disconnect_while_argument_prompt_open_retains_the_create(self):
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
            wait_for_async(page, "() => prksSync.store.listOperations().then(rows => rows.some("
                           "op => op.operation === 'CREATE_ARGUMENT' &&"
                           "op.payload.name === 'Created while disconnected'))")
            self.assertFalse(any(path.startswith('/api/arguments') for _method, path in mutations),
                             "create is durable, never a direct canonical POST")
        finally:
            _safe_unroute(page, "**/api/**", block_api)

        page.evaluate("""async () => { try { await window.prksRequest('/api/settings'); } catch (_e) {} }""")
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'online'",
            timeout=20000,
        )
        page.evaluate("() => window.dispatchEvent(new Event('online'))")
        _drain_durable(page)
        names = page.evaluate("() => fetchArguments().then(items => items.map(a => a.name))")
        self.assertIn("Created while disconnected", names)

    def test_open_edit_form_survives_disconnect_without_losing_the_draft(self):
        """An editor mounted online keeps its unsaved values when PRKS stops
        answering, and Save records it durably."""
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
            # Draft and every durable editor control stay live.
            self.assertEqual(page.locator("#prks-arg-name").input_value(), draft)
            self.assertFalse(page.locator("#prks-arg-text").is_disabled())
            self.assertFalse(page.locator("#prks-arg-kind").is_disabled())
            self.assertFalse(page.locator("#prks-arg-add-target").is_disabled())
            self.assertFalse(page.locator("#prks-arg-cancel").is_disabled())
            page.locator("#prks-arg-form button[type=submit]").click()
            wait_for_async(page, "() => prksSync.store.listOperations().then(rows => rows.some("
                           "op => op.operation === 'SET_ARGUMENT_FIELD' &&"
                           "op.payload.field === 'name'))")
            self.assertFalse(any(path.startswith('/api/arguments') for _method, path in mutations))
        finally:
            _safe_unroute(page, "**/api/**", block_api)

        page.evaluate("() => window.dispatchEvent(new Event('online'))")
        _drain_durable(page)
        _open_argument(page, argument_a)
        _wait_content_contains(page, draft)

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

    def test_live_argument_pages_keep_durable_controls_across_connectivity(self):
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
            self.assertTrue(page.evaluate("() => navigator.onLine"))
            self.assertFalse(page.locator("#prks-argument-new").is_disabled())
            self.assertFalse(page.locator("#prks-stance-new").is_disabled())
        finally:
            _safe_unroute(page, "**/api/**", abort_api)

        page.evaluate("""async () => { await window.prksRequest('/api/settings'); }""")
        self.assertFalse(page.locator("#prks-argument-new").is_disabled())

        _open_argument(page, argument_a)
        _wait_content_contains(page, ARGUMENT_A_NAME)
        _wait_entity_cached(page, "argument", argument_a)
        page.route("**/api/**", abort_api)
        try:
            page.evaluate("""async () => { try { await window.prksRequest('/api/settings'); } catch (_e) {} }""")
            self.assertFalse(page.locator("#prks-arg-edit").is_disabled())
            for selector in ("#prks-arg-response", "#prks-arg-delete"):
                self.assertFalse(page.locator(selector).is_disabled(), selector)
            self.assertFalse(page.locator("#prks-arg-view-graph").is_disabled())
            # Read-only content and relationship links stay usable.
            body = _content_text(page)
            self.assertIn(ARGUMENT_A_TEXT, body)
            self.assertIn(POSITION_A_NAME, body)
            self.assertGreaterEqual(page.locator(".prks-research-row").count(), 3)
        finally:
            _safe_unroute(page, "**/api/**", abort_api)

        page.evaluate("""async () => { await window.prksRequest('/api/settings'); }""")
        self.assertFalse(page.locator("#prks-arg-edit").is_disabled())

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
        wait_for_async(page,
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
        # Leave first: after a domain fence the route may already be an
        # Arguments page, and a same-hash navigate would not remount/republish.
        page.evaluate("() => prksNavigate('#/folders')")
        page.wait_for_function("() => location.hash === '#/folders'", timeout=15000)
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
        page.wait_for_function(
            "() => !prksOfflineIsDomainBlocked('arguments')", timeout=20000)

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

        page.route("**/api/sync/operations", reject)
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
            wait_for_async(page, "() => prksSync.store.listOperations().then(rows => rows.some("
                           "op => op.status === 'pending' && op.last_error))")
            self.assertEqual(_domain_generation(page, "arguments"), before)
            self.assertIsNotNone(_cached_entity(page, "argument", argument_a))
        finally:
            _safe_unroute(page, "**/api/sync/operations", reject)

    def test_independent_durable_units_survive_one_transport_failure(self):
        """A field ACK controls its coherence while target intent stays durable."""
        server, page, _context, _collector = self._start()
        argument_a = server.ids["argument_a"]

        self._cache_arguments(page, server)
        before = _domain_generation(page, "arguments")

        def reject_targets(route):
            body = json.loads(route.request.post_data or "{}")
            if body.get("operation") == "SET_ARGUMENT_TARGETS":
                route.fulfill(status=503, content_type="application/json", body='{"error":"nope"}')
                return
            route.fallback()

        page.route("**/api/sync/operations", reject_targets)
        try:
            page.evaluate(
                """async (id) => {
                    await window.updateArgument(id, { name: 'Partially saved', kind: 'argument' });
                    await window.putArgumentTargets(id, []);
                }""",
                argument_a,
            )
            self._assert_arguments_invalidated(page, server, before)
            wait_for_async(page, "() => prksSync.store.listOperations().then(rows => rows.some("
                           "op => op.operation === 'SET_ARGUMENT_TARGETS' &&"
                           "op.status === 'pending' && op.last_error))")
        finally:
            _safe_unroute(page, "**/api/sync/operations", reject_targets)

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

        concepts_before = _domain_generation(page, "concepts")
        page.evaluate("id => window.updateConcept(id, { name: 'Domain isolation rename' })",
                      concept_child)
        _drain_durable(page)
        self.assertGreater(_domain_generation(page, "concepts"), concepts_before)
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
        _drain_durable(page)
        self._assert_arguments_invalidated(page, server, before)

        # A DESCRIPTION edit reaches none of that: an Argument target row names
        # the Position and nothing else about it.
        self._cache_arguments(page, server)
        before = _domain_generation(page, "arguments")
        page.evaluate(
            "id => window.updatePosition(id, { description: 'Not in any Argument.' })",
            server.ids["position_a"])
        _drain_durable(page)
        self.assertEqual(_domain_generation(page, "arguments"), before)
        self.assertIsNotNone(_cached_entity(page, "argument", server.ids["argument_a"]))

        # And an operation the server has not answered retains the cache, since
        # a durable write changes nothing cached until it is acknowledged.
        self._cache_arguments(page, server)
        before = _domain_generation(page, "arguments")

        def reject(route):
            if route.request.method == "POST":
                route.fulfill(status=500, content_type="application/json", body='{"error":"nope"}')
                return
            route.fallback()

        page.route("**/api/sync/operations**", reject)
        try:
            page.evaluate("id => window.updatePosition(id, { name: 'never applied' })",
                          server.ids["position_a"])
            wait_for_async(
                page,
                "() => prksSync.store.listOperations().then(rows => rows.some("
                "  o => o.operation === 'SET_POSITION_FIELD'))",
                timeout=30000, message="the rename was never enqueued")
            page.wait_for_timeout(600)
            self.assertEqual(_domain_generation(page, "arguments"), before)
            self.assertIsNotNone(_cached_entity(page, "argument", server.ids["argument_a"]))
        finally:
            _safe_unroute(page, "**/api/sync/operations**", reject)

    def test_a_rename_reconciles_arguments_and_concepts_but_not_positions(self):
        """A Work Title appears in cached Argument sources/mentions and cached
        Concept mentions -- and in neither Position field. It is local-first,
        so those caches are PATCHED with the exact new title rather than
        invalidated, and a Position, which names no Work, is not touched at
        all."""
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

        renamed = "Argument Source Title Changed"
        _open_work_from_home(page, WORK_A_TITLE)
        page.locator("#panel-content button", has_text="Edit metadata").click()
        _rename_work_durably(page, renamed)

        # Both collections of an Argument name the Work, under different
        # column names, and both are patched.
        wait_for_async(page,
            """([id, title]) => window.createPrksOfflineStore().getEntity('argument', id)
                .then(row => !!row && (
                    (row.value.sources || []).some(s => s.work_title === title) ||
                    (row.value.mentions || []).some(m => m.title === title)))""",
            arg=[server.ids["argument_a"], renamed],
            timeout=15000,
        )
        wait_for_async(page,
            """([id, title]) => window.createPrksOfflineStore().getEntity('concept', id)
                .then(row => !!row && (row.value.mentions || []).some(m => m.title === title))""",
            arg=[concept_child, renamed],
            timeout=15000,
        )
        # Patched, not dropped -- and a Position names no Work at all.
        self.assertIsNotNone(_cached_entity(page, "concept", concept_child))
        self.assertEqual(_domain_generation(page, "positions"), positions_before)
        self.assertIsNotNone(_cached_entity(page, "position", position_a))
        self.assertGreaterEqual(_domain_generation(page, "arguments"), arguments_before)
        self.assertGreaterEqual(_domain_generation(page, "concepts"), concepts_before)

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
        # The Playlist surface uses the same durable Title operation, so the
        # cached Argument is PATCHED rather than invalidated.
        wait_for_async(page,
            """([id, title]) => window.createPrksOfflineStore().getEntity('argument', id)
                .then(row => !!row && (
                    (row.value.sources || []).some(x => x.work_title === title) ||
                    (row.value.mentions || []).some(x => x.title === title)))""",
            arg=[server.ids["argument_a"], "Renamed From The Playlist"],
            timeout=15000,
        )
        self.assertIsNotNone(_cached_entity(page, "argument", server.ids["argument_a"]))

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

        def reject_sync(route):
            route.fulfill(status=500, content_type="application/json", body='{"error":"nope"}')

        page.route("**/api/sync/operations", reject_sync)
        try:
            page.locator(".work-notes-editor-wrap .CodeMirror").first.click()
            page.keyboard.press("Control+A")
            page.keyboard.insert_text("second save that stays local")
            page.evaluate("""() => {
                const ctx = window.prksGetFocusedTabContext();
                window.prksFlushPendingWorkResearchNotes(ctx);
            }""")
            wait_for_async(page,
                """() => prksSync.store.listOperations().then(rows =>
                    rows.some(r => r.operation === 'SET_WORK_RESEARCH_NOTE'))""",
                timeout=15000)
        finally:
            _safe_unroute(page, "**/api/sync/operations", reject_sync)

        # Save #1 changed canonical mention data; #2 staying local does not undo that.
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
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=60000,
            message="DELETE_WORK must acknowledge before arguments coherence",
        )
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
        # Coherence follows the CANONICAL change, which for a profile field is
        # now the acknowledgement rather than the Save click: Save writes the
        # intent, the coordinator sends it, and the reconciler is what patches
        # and invalidates. So the queue has to drain before any domain
        # assertion means anything.
        _wait_sync_settled(page)

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
    """Cached People routes: #/people, #/people/role/:role, #/people/:id (mutations are durable separately)."""

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

        # Editing an existing Person still needs the server, so its controls
        # stay disabled. CREATING one does not any more, and its control is
        # deliberately a different role for exactly that reason.
        page.wait_for_function(
            "() => { const create = document.querySelector("
            "          '[data-prks-role=\"person-create-control\"]');"
            "        return !!create && !create.disabled; }",
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
        # Editing an existing Person still needs the server, so its controls
        # stay disabled. CREATING one does not any more, and its control is
        # deliberately a different role for exactly that reason.
        page.wait_for_function(
            "() => { const create = document.querySelector("
            "          '[data-prks-role=\"person-create-control\"]');"
            "        return !!create && !create.disabled; }",
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
        wait_for_async(page,
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
        wait_for_async(page,
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
    """Person mutations are durable; an open editor keeps its draft when
    connectivity drops. Work-relationship editing from the profile stays
    connection-required."""

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

    def test_new_person_is_creatable_from_every_surface_offline(self):
        """Creating a Person is durable-first: the identity is chosen on this
        device, so the record is complete the moment it is written locally and
        the server never renames it.

        This replaces two tests that asserted the opposite -- that the modal
        could not open and that no create could be attempted. Both were correct
        before Person creation became local-first, and both would now pass only
        if the milestone had not shipped.
        """
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

        sends = []

        def record_mutation(route):
            if route.request.method in ("POST", "PATCH", "PUT", "DELETE"):
                sends.append((route.request.method, urlparse(route.request.url).path))
            route.fallback()

        page.route("**/api/persons**", record_mutation)
        try:
            btn = page.locator('[data-prks-role="person-create-control"]').first
            self.assertFalse(btn.is_disabled(), "creating a Person no longer needs a server")
            btn.click()
            page.locator("#person-modal:not(.hidden)").wait_for()
            page.locator("#person-lname").fill("Offline")
            page.locator("#person-fname").fill("Created")
            page.locator("#save-person-btn").click()

            # One durable operation, and no direct write of any kind.
            wait_for_async(
                page,
                "() => prksSync.store.listOperations().then(rows => rows.some("
                "  o => o.operation === 'CREATE_PERSON'))",
                message="the Person was not recorded durably",
            )
            self.assertEqual(sends, [], "creation goes through the queue, never a direct POST")
        finally:
            _safe_unroute(page, "**/api/persons**", record_mutation)

        # The Person exists for the user immediately, and survives a reload
        # while still offline -- it is durable local state, not screen state.
        _open_people_index(page)
        _wait_content_contains(page, "Created Offline")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#sidebar")
        _open_people_index(page)
        _wait_content_contains(page, "Created Offline")

    def test_cached_person_detail_allows_profile_and_group_edits_but_not_work_links(self):
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

        # Editing the PROFILE is durable now, so its control is enabled ...
        page.wait_for_function(
            "() => { const b = document.querySelector('#panel-content"
            " [data-prks-role=\"person-edit-control\"]'); return !!b && !b.disabled; }",
            timeout=20000,
        )
        # ... while every control that still needs the server is not. Asserted
        # over ALL of them rather than the first: the delete action lives
        # behind a menu, so which one `querySelector` happens to find is a
        # detail of the panel's markup rather than of this rule.
        page.wait_for_function(
            "() => Array.from(document.querySelectorAll('#panel-content"
            " [data-prks-role=\"person-mutation-control\"]')).every(b => b.disabled)",
            timeout=20000,
        )
        self.assertFalse(page.locator("#prks-person-view-graph").is_disabled())
        # Relationship editing cannot be entered ...
        page.evaluate("() => { try { prksTogglePersonWorksEdit(); } catch (_e) {} }")
        page.wait_for_timeout(200)
        self.assertEqual(page.locator(".person-profile__card-unlink").count(), 0)
        # ... but profile editing opens, because it is the same feature online
        # and offline now. Whether a SAVE can proceed is decided by whether
        # this device knows the profile's revisions, not by connectivity --
        # tests.e2e.test_person_edit_offline owns that behaviour.
        page.evaluate("() => { try { openPersonProfileEdit(); } catch (_e) {} }")
        page.locator(".person-panel-edit").wait_for(timeout=15000)
        # Group membership inside that editor is durable too now: a membership
        # is its own operation keyed by the (group, person) PAIR, so it queues
        # offline exactly as it sends online.
        self.assertFalse(page.locator("#pd-group-add-btn").is_disabled())
        self.assertFalse(page.locator("#pd-group-search").is_disabled())
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
    def test_person_graph_action_navigates_to_uncached_graph_offline(self):
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
        self.assertFalse(page.locator("#prks-person-view-graph").is_disabled())
        page.locator("#prks-person-view-graph").click()
        _wait_offline_unavailable(page)
        self.assertIn("#/graph?focus=person:", page.evaluate("decodeURIComponent(location.hash)"))

    def test_open_profile_editor_stays_usable_after_a_disconnect(self):
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
            # The draft is still there ...
            self.assertEqual(
                page.evaluate("() => document.getElementById('pd-about').value"), draft)
            # ... and so is the ability to save it. Every control in this
            # editor is durable now -- profile fields and group membership
            # alike -- so one that went inert on disconnect would take away a
            # change this device can perfectly well record.
            self.assertFalse(page.locator("#pd-first-name").is_disabled())
            self.assertFalse(page.locator("#pd-group-add-btn").is_disabled())
            self.assertFalse(page.locator("#pd-save-btn").is_disabled())
            # ... Cancel stays usable ...
            self.assertFalse(
                page.locator('.person-panel-edit [data-prks-person-cancel]').is_disabled()
            )
            # ... and saving records the intent WITHOUT any canonical request:
            # the durable queue is the only mutation boundary now.
            page.locator("#pd-save-btn").click()
            wait_for_async(
                page,
                "() => prksSync.store.listOperations().then(rows => rows.some("
                "  o => o.operation === 'SET_PERSON_METADATA_FIELD'))",
                message='the offline profile edit was never recorded durably')
            self.assertEqual(mutations, [])
        finally:
            _safe_unroute(page, "**/api/**", block_api)

        # Reconnecting sends what was recorded. The editor is already closed --
        # the save completed when it was written, not when it reached the
        # server -- so what is checked here is that the change actually
        # arrived, and that nothing was left pending behind it.
        page.evaluate("""async () => { await window.prksRequest('/api/settings'); }""")
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function'"
            " ? prksOfflineRuntimeState() : null) === 'online'", timeout=20000)
        _wait_sync_settled(page)
        _wait_content_contains(page, draft)

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

    def test_profile_group_creation_is_durable_after_disconnect(self):
        """Typing a new Group name in the Person editor creates it offline.

        Person Groups are durable: the id is minted on this device, so Add
        group is real with or without a server. Membership remains a separate
        Save decision (no ADD_PERSON_GROUP_MEMBER until then). The older
        "blocked after disconnect" contract is obsolete.
        """
        server, page, context, _collector = self._start()
        person_a = server.ids["person_a"]
        group_name = "Brand New Offline Group"

        _wait_sw_active(page)
        _open_person(page, person_a)
        _wait_content_contains(page, PERSON_DISPLAY)
        _wait_entity_cached(page, "person", person_a)
        _open_details_drawer_if_tiled(page)
        page.locator("#panel-content button", has_text="Edit profile").click()
        page.locator('.person-panel-edit[data-person-edit-id="%s"]' % person_a).wait_for()
        page.wait_for_function(
            "() => typeof document.querySelector('#pd-group-add-btn')?.onclick === 'function'"
        )
        # Warm the catalogue while online so Add does not need a dead fetch.
        page.evaluate(
            """async () => {
                if (typeof prksEnsureAllGroupsCache === 'function') {
                    await prksEnsureAllGroupsCache();
                }
            }"""
        )

        context.set_offline(True)
        page.evaluate("""async () => { try { await window.prksRequest('/api/settings'); } catch (_e) {} }""")
        page.wait_for_function(
            "() => (typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : null) === 'offline'",
            timeout=20000,
        )

        page.locator("#pd-group-search").fill(group_name)
        page.locator("#pd-group-add-btn").click()
        page.locator("#pd-group-chips", has_text=group_name).wait_for()
        self.assertEqual(page.locator("#pd-group-search").input_value(), "")

        wait_for_async(
            page,
            """() => prksSync.store.listOperations().then(rows => rows.some(
                o => o && o.operation === 'CREATE_PERSON_GROUP'
                    && o.payload && o.payload.name === %r
                    && o.status !== 'acknowledged'))"""
            % group_name,
            timeout=15000,
            message="CREATE_PERSON_GROUP never landed in the durable queue",
        )
        self.assertEqual(
            page.evaluate(
                """() => prksSync.store.listOperations().then(rows => rows.filter(
                    o => o && o.operation === 'ADD_PERSON_GROUP_MEMBER').length)"""
            ),
            0,
            "joining is a separate decision, recorded only on Save",
        )


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

    def _assert_people_reconciled(self, page, server, before):
        """A profile edit PATCHES the People read model rather than dropping it.

        The acknowledgement carries the exact new value, so discarding the
        cached Person and the cached index would cost the user both for a
        change already known in full -- and offline there is nothing to read
        them back from. The generation still advances, so a GET that began
        before the acknowledgement cannot publish its older body afterwards.
        """
        page.wait_for_function(
            "n => (typeof prksOfflineDomainGeneration === 'function'"
            " ? prksOfflineDomainGeneration('people') : 0) > n",
            arg=before,
            timeout=20000,
        )
        self.assertIsNotNone(_cached_entity(page, "person", server.ids["person_a"]))
        self.assertIsNotNone(_cached_list(page, "people:index"))

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
        self._assert_people_reconciled(page, server, before)
        self.assertEqual(_domain_generation(page, "arguments"), arguments_before)
        self.assertIsNotNone(_cached_entity(page, "argument", server.ids["argument_a"]))

        # A canonical-name change stales both.
        self._cache_people(page, server)
        before = _domain_generation(page, "people")
        arguments_before = _domain_generation(page, "arguments")
        self._save_person_profile(page, person_a, "#pd-first-name", "Renamed")
        self._assert_people_reconciled(page, server, before)
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
        # The link is durable: it completes when the intent is WRITTEN, so a
        # canonical read straight afterwards is reading it too early.
        _wait_sync_settled(page)
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

    def test_a_rename_reconciles_the_cached_person_profile(self):
        """A Person profile embeds Work SUMMARIES, which carry the Title. It
        used to be invalidated by a metadata PATCH; the durable path patches
        the embedded row with the exact new title instead."""
        server, page, _context, _collector = self._start()

        self._cache_people(page, server)
        _open_work_from_home(page, WORK_A_TITLE)
        page.locator("#panel-content button", has_text="Edit metadata").click()
        _rename_work_durably(page, "Person Work Card Title Changed")

        wait_for_async(page,
            """([id, title]) => window.createPrksOfflineStore().getEntity('person', id)
                .then(row => !!row && (row.value.works || []).some(w => w.title === title))""",
            arg=[server.ids["person"], "Person Work Card Title Changed"],
            timeout=15000,
        )
        self.assertIsNotNone(_cached_entity(page, "person", server.ids["person"]),
                             "the profile was patched, not dropped")

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
        # Same durable Title operation, same reconciliation.
        wait_for_async(page,
            """([id, title]) => window.createPrksOfflineStore().getEntity('person', id)
                .then(row => !!row && (row.value.works || []).some(w => w.title === title))""",
            arg=[server.ids["person"], "Renamed From The Playlist"],
            timeout=15000,
        )

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
                /* The folder is durable: the server has to have been told about
                 * it before a canonical bulk move can name it. */
                const deadline = Date.now() + 30000;
                while (Date.now() < deadline) {
                    const rows = await prksSync.store.listOperations();
                    if (!rows.some(o => o.status !== 'conflict')) break;
                    await new Promise(r => setTimeout(r, 100));
                }
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
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=60000,
            message="DELETE_WORK must acknowledge before people coherence",
        )
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

    def test_group_membership_update_and_delete_reconcile_people(self):
        """Membership is durable now, so coherence follows the CANONICAL
        change -- the acknowledgement -- rather than the click. And the
        acknowledgement carries the exact new state, so the People rows this
        device holds are PATCHED: dropping them would leave a device that has
        just gone offline with no People index at all."""
        server, page, _context, _collector = self._start()
        group_id = server.ids["person_group"]
        person_b = server.ids["person_b"]

        self._cache_people(page, server)
        before = _domain_generation(page, "people")
        _set_group_membership(page, group_id, person_b, True)
        self._assert_people_reconciled(page, server, before)

        self._cache_people(page, server)
        before = _domain_generation(page, "people")
        _set_group_membership(page, group_id, person_b, False)
        self._assert_people_reconciled(page, server, before)

        # A RENAME reaches the group chips embedded in every People row. The
        # INDEX rows carry the new name exactly, so they are patched -- but a
        # Person DETAIL is keyed by id and there is no list of the cached ones
        # to walk, so those are dropped. Reconciliation only ever runs while
        # connected, which is exactly when a refetch is affordable.
        self._cache_people(page, server)
        before = _domain_generation(page, "people")
        _rename_group(page, group_id, "Renamed Group")
        self.assertGreater(_domain_generation(page, "people"), before)
        self.assertIsNotNone(_cached_list(page, "people:index"))
        _wait_entity_uncached(page, "person", server.ids["person_a"])

        # A DELETION removes the chip from the rows this device holds, and
        # stales every cached Person detail that may carry it -- there is no
        # list of those to walk.
        self._cache_people(page, server)
        before = _domain_generation(page, "people")
        _delete_group(page, group_id)
        self.assertGreater(_domain_generation(page, "people"), before)

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
        _set_group_membership(page, server.ids["person_group"],
                              server.ids["person_b"], True)
        self._assert_people_reconciled(page, server, before)
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
