"""Real Chromium + real PRKS server scenarios. Collected only when PRKS_E2E=1."""
from __future__ import annotations

import os
import re
import unittest
from urllib.parse import unquote

from tests.e2e.fixtures import PERSON_DISPLAY, WORK_A_TITLE, seed_library
from tests.e2e.harness import (
    AppServer,
    FixtureServer,
    open_app_page,
    require_chromium,
)

NOTES = """# E2E Research Note

This note links [[concept:Culture Industry]] to the work.

**Bold text**

| A | B |
|---|---|
| 1 | 2 |

<img src=x onerror=alert(1)>
"""


def load_tests(loader, standard_tests, pattern):
    if os.environ.get("PRKS_E2E") != "1":
        return unittest.TestSuite()
    return standard_tests


def _expand_research(page):
    toggle = page.locator('[data-nav-disclosure-toggle="research"]')
    if toggle.get_attribute("aria-expanded") != "true":
        toggle.click()
    page.locator("#prks-nav-research-children").wait_for(state="visible")


def _open_work_from_home(page, title):
    page.locator('#sidebar a.nav-link[href="#/folders"]').click()
    page.wait_for_function("() => location.hash === '#/folders'")
    page.locator('.prks-folder-library__tab-btn[data-tab="recently-added"]').click()
    page.locator(".card-title", has_text=title).wait_for()
    page.locator(".card-title", has_text=title).click()
    page.wait_for_function(
        "title => decodeURIComponent(location.hash).indexOf('/works/') !== -1 && document.body.innerText.indexOf(title) !== -1",
        arg=title,
    )


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


class _BrowserE2E(unittest.TestCase):
    def _start_app(self):
        server = AppServer(seed_fn=seed_library)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin)
        self.addCleanup(context.close)
        self.addCleanup(collector.assert_clean)
        return server, page, collector


class AppShellAndNavigationTests(_BrowserE2E):
    def test_app_loads_and_real_navigation(self):
        server, page, _collector = self._start_app()
        page.wait_for_selector("#sidebar")
        self.assertIn("PRKS", page.locator(".sidebar-header").inner_text())
        self.assertEqual(page.evaluate("() => location.hash"), "#/folders")
        page.locator(".prks-folder-library__tab-btn[data-tab='recently-added']").click()
        page.locator(".card-title", has_text=WORK_A_TITLE).wait_for()
        page.locator(".card-title", has_text=WORK_A_TITLE).click()
        page.wait_for_function("() => location.hash.indexOf('#/works/') === 0")
        self.assertIn(server.ids["work_a"], page.evaluate("() => location.hash"))
        page.wait_for_selector(".prks-nav-back")
        self.assertTrue(page.get_by_text(WORK_A_TITLE).count() >= 1)
        page.locator(".prks-nav-back").click()
        page.wait_for_function("() => location.hash === '#/folders'")
        _expand_research(page)
        page.locator('#prks-nav-research-children a.nav-link[href="#/concepts"]').click()
        page.wait_for_function("() => location.hash === '#/concepts'")
        page.locator("h2", has_text="Concepts").wait_for()
        page.locator('#prks-nav-research-children a.nav-link[href="#/graph"]').click()
        page.wait_for_function("() => location.hash === '#/graph' || location.hash.indexOf('#/graph?') === 0")
        page.locator("h2", has_text="Research Graph").wait_for()

    def test_command_palette_opens_seeded_work(self):
        server, page, _collector = self._start_app()
        page.keyboard.press("Control+k")
        page.wait_for_selector("#prks-command-palette:not([hidden])")
        page.locator("#prks-command-palette-input").fill(WORK_A_TITLE)
        work_label = page.locator(
            ".prks-command-palette__option-label",
            has_text=re.compile("^" + re.escape(WORK_A_TITLE) + "$"),
        )
        work_label.wait_for()
        page.locator(".prks-command-palette__option").filter(has=work_label).click()
        page.wait_for_function("() => location.hash.indexOf('#/works/') === 0")
        page.locator("#prks-command-palette").wait_for(state="hidden")
        self.assertIn(server.ids["work_a"], page.evaluate("() => location.hash"))
        page.locator(".prks-pdf-toolbar__title, h2.page-header--work-title", has_text=WORK_A_TITLE).first.wait_for()


class ResearchNoteConceptGraphTests(_BrowserE2E):
    def test_research_note_creates_concept_and_graph_edge(self):
        server, page, _collector = self._start_app()
        work_id = server.ids["work_a"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.wait_for_selector(".CodeMirror")
        page.locator(".CodeMirror").click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(NOTES)
        page.locator("#editor-status", has_text="All changes saved").wait_for()
        page.wait_for_function(
            """() => {
                const refs = window.currentWork && window.currentWork.research_refs;
                const cs = refs && refs.concepts;
                return Array.isArray(cs) && cs.some(function (c) {
                    return String(c && c.name || '').indexOf('Culture Industry') !== -1;
                });
            }"""
        )
        page.locator(".EasyMDEContainer button.preview").click()
        preview = page.locator(".editor-preview, .editor-preview-active").first
        preview.wait_for(state="visible")
        self.assertTrue(preview.locator("h1", has_text="E2E Research Note").count() >= 1)
        self.assertTrue(preview.locator("strong", has_text="Bold text").count() >= 1)
        self.assertTrue(preview.locator("table").count() >= 1)
        self.assertEqual(preview.locator("img[onerror]").count(), 0)
        concept_link = preview.locator("a.wiki-link-internal", has_text="Culture Industry")
        self.assertEqual(concept_link.count(), 1)
        href = concept_link.get_attribute("href") or ""
        self.assertTrue(href.startswith("#/concepts/"))
        concept_link.click()
        page.wait_for_function("() => location.hash.indexOf('#/concepts/') === 0")
        page.locator("h2", has_text="Culture Industry").wait_for()
        page.locator("#prks-concept-view-graph").click()
        page.wait_for_function("() => location.hash.indexOf('#/graph') === 0")
        page.wait_for_function("() => window.__prksResearchGraphCy && window.__prksResearchGraphCy.nodes().length > 0")
        page.locator("#prks-graph-find").fill("Culture Industry")
        page.locator(".research-graph__find-hit", has_text="Culture Industry").click()
        page.locator("#prks-graph-inspector-title", has_text="Culture Industry").wait_for()
        topo = page.evaluate(
            """() => {
                const cy = window.__prksResearchGraphCy;
                const nodes = cy.nodes().map(n => n.id());
                const edges = cy.edges().map(e => ({
                    type: e.data('type'),
                    source: e.source().id(),
                    target: e.target().id(),
                }));
                return { nodes, edges };
            }"""
        )
        concept_id = unquote(href.split("/concepts/", 1)[-1])
        concept_node = "concept:" + concept_id
        work_node = "work:" + work_id
        self.assertIn(concept_node, topo["nodes"])
        self.assertIn(work_node, topo["nodes"])
        mention = [
            e
            for e in topo["edges"]
            if e["type"] == "mentions_concept"
            and e["source"] == work_node
            and e["target"] == concept_node
        ]
        self.assertEqual(len(mention), 1, topo["edges"])
        _open_work_from_home(page, WORK_A_TITLE)
        page.wait_for_selector(".CodeMirror")
        saved = page.evaluate("() => window.workNotesEasyMDE && window.workNotesEasyMDE.value()")
        self.assertIn("[[concept:Culture Industry]]", saved)


class PersonGraphFocusTests(_BrowserE2E):
    def test_person_view_in_graph_includes_people_and_focus(self):
        server, page, _collector = self._start_app()
        person_id = server.ids["person"]
        work_id = server.ids["work_a"]
        page.locator('#sidebar a.nav-link[href="#/people"]').click()
        page.wait_for_function("() => location.hash === '#/people'")
        page.locator(".prks-people-list__title", has_text=PERSON_DISPLAY).click()
        page.wait_for_function("() => location.hash.indexOf('#/people/') === 0")
        page.locator("#prks-person-view-graph").click()
        page.wait_for_function(
            """(pid) => {
                const h = decodeURIComponent(location.hash || '');
                return h === '#/graph?focus=person:' + pid;
            }""",
            arg=person_id,
        )
        page.wait_for_function("() => window.__prksResearchGraphCy && window.__prksResearchGraphCy.nodes().length > 0")
        people = page.locator('[data-graph-filter="people"]')
        self.assertTrue(people.is_checked())
        page.locator("#prks-graph-inspector-title", has_text=PERSON_DISPLAY).wait_for()
        topo = page.evaluate(
            """() => {
                const cy = window.__prksResearchGraphCy;
                const nodes = cy.nodes().map(n => n.id());
                const edges = cy.edges().map(e => ({
                    type: e.data('type'),
                    source: e.source().id(),
                    target: e.target().id(),
                }));
                return { nodes, edges };
            }"""
        )
        person_node = "person:" + person_id
        work_node = "work:" + work_id
        self.assertIn(person_node, topo["nodes"])
        self.assertIn(work_node, topo["nodes"])
        authors = [
            e
            for e in topo["edges"]
            if e["type"] == "work_author"
            and e["source"] == person_node
            and e["target"] == work_node
        ]
        self.assertEqual(len(authors), 1, topo["edges"])
        page.locator('#sidebar a.nav-link[href="#/folders"]').click()
        page.wait_for_function("() => location.hash === '#/folders'")
        page.wait_for_function("() => window.__prksResearchGraphCy == null")
        page.wait_for_selector(".prks-folder-library, #page-content")
        self.assertNotIn("graph", page.evaluate("() => location.hash"))


def _wait_pdf_viewer(page):
    page.wait_for_selector("#pdf-viewer .prks-pdf-page", timeout=30000)
    page.wait_for_selector("#pdf-viewer .prks-pdf-render-image", timeout=30000)
    page.wait_for_function("() => window.currentPdfViewer != null", timeout=30000)
    page.wait_for_function(
        "() => window.currentPdfViewer && window.currentPdfViewer.getPageCount && window.currentPdfViewer.getPageCount() >= 1",
        timeout=30000,
    )
    page.wait_for_function("() => window.__prksWorkAnnotationSyncState != null", timeout=30000)


def _open_annotations_tab(page):
    page.locator(".tab-btn[data-target='annotations']").click()
    page.wait_for_selector("#annotation-fallback-list")


def _sync_success_at(page):
    return int(
        page.evaluate(
            "() => (window.__prksWorkAnnotationSyncState && window.__prksWorkAnnotationSyncState.lastSuccessAt) || 0"
        )
        or 0
    )


def _viewer_annotation_count(page):
    return int(
        page.evaluate(
            """() => {
                const v = window.currentPdfViewer;
                if (!v || typeof v.getAnnotations !== 'function') return 0;
                return (v.getAnnotations() || []).length;
            }"""
        )
        or 0
    )


def _drag_pdf_text_selection(page):
    host = page.locator("#pdf-viewer .prks-pdf-page > *").first
    host.wait_for()
    box = host.bounding_box()
    if not box:
        return
    sx = box["x"] + min(90, max(20, box["width"] * 0.15))
    sy = box["y"] + min(95, max(20, box["height"] * 0.12))
    page.mouse.move(sx, sy)
    page.mouse.down()
    page.mouse.move(sx + min(210, box["width"] * 0.45), sy, steps=10)
    page.mouse.up()
    page.wait_for_timeout(400)


def _commit_pdf_highlight(page):
    """Select text with a real mouse; fall back to the viewer's createAnnotation API."""
    _wait_pdf_viewer(page)
    page.evaluate(
        """() => {
            const v = window.currentPdfViewer;
            if (v && typeof v.setInteractionMode === 'function') v.setInteractionMode('pointer');
        }"""
    )
    pointer_btn = page.locator('#pdf-viewer [aria-label="Pointer"]')
    if pointer_btn.count():
        pointer_btn.first.click()
    before = _viewer_annotation_count(page)
    toolbar_hi = page.locator('#pdf-viewer .prks-pdf-toolbar__secondary [aria-label="Highlight"]')
    if toolbar_hi.count():
        toolbar_hi.first.click()
    _drag_pdf_text_selection(page)
    popup = page.locator(".prks-pdf-selection-popup [aria-label='Highlight']")
    if popup.count():
        popup.click()
    try:
        page.wait_for_function(
            """(n) => {
                const v = window.currentPdfViewer;
                return !!(v && v.getAnnotations && v.getAnnotations().length > n);
            }""",
            arg=before,
            timeout=5000,
        )
    except Exception:
        created = page.evaluate(
            """async () => {
                const v = window.currentPdfViewer;
                if (!v || typeof v.createAnnotation !== 'function') return false;
                const id = (crypto.randomUUID && crypto.randomUUID()) || ('e2e-' + Date.now());
                const payload = {
                    id: id,
                    type: 9,
                    pageIndex: 0,
                    rect: { origin: { x: 72, y: 700 }, size: { width: 160, height: 14 } },
                    segmentRects: [{ origin: { x: 72, y: 700 }, size: { width: 160, height: 14 } }],
                    opacity: 1,
                    strokeColor: '#FFCD45',
                    color: '#FFCD45',
                    created: new Date(),
                };
                const result = v.createAnnotation(0, payload);
                if (result && typeof result.then === 'function') await result;
                if (result && typeof result.toPromise === 'function') await result.toPromise();
                return (v.getAnnotations() || []).some((a) => a && (a.id === id || (a.raw && a.raw.id === id)));
            }"""
        )
        if not created:
            raise AssertionError("could not create a PDF highlight")
        page.wait_for_function(
            """(n) => {
                const v = window.currentPdfViewer;
                return !!(v && v.getAnnotations && v.getAnnotations().length > n);
            }""",
            arg=before,
            timeout=8000,
        )
    page.evaluate(
        """() => {
            const st = window.__prksWorkAnnotationSyncState;
            if (st && (st.pendingChanges || st.inFlight || st.localMutationSeen)) return;
            if (typeof window.__prksFlushWorkAnnotationPersistence === 'function') {
                void window.__prksFlushWorkAnnotationPersistence();
            }
        }"""
    )


class PdfPersistenceTests(_BrowserE2E):
    def test_highlight_persists_through_handshake_reload_and_delete(self):
        server, page, collector = self._start_app()
        work_id = server.ids["work_a"]
        page.on("dialog", lambda dialog: dialog.accept())
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        collector.reset_handshake()
        since = _sync_success_at(page)
        _commit_pdf_highlight(page)
        _open_annotations_tab(page)
        page.wait_for_selector(".annotation-row")
        collector.wait_pdf_handshake(page, since_ms=since)
        page.reload(wait_until="domcontentloaded")
        self.assertIn(work_id, page.evaluate("() => location.hash"))
        _wait_pdf_viewer(page)
        _open_annotations_tab(page)
        page.wait_for_selector(".annotation-row")
        page.locator(".annotation-row__jump").first.click()
        page.wait_for_function(
            """() => {
                const v = window.currentPdfViewer;
                if (!v || typeof v.getSelectedAnnotation !== 'function') return true;
                return !!v.getSelectedAnnotation();
            }"""
        )
        collector.reset_handshake()
        since_delete = _sync_success_at(page)
        page.locator(".annotation-row__delete").first.click()
        page.wait_for_function(
            "() => document.querySelectorAll('.annotation-row').length === 0"
        )
        collector.wait_pdf_handshake(page, since_ms=since_delete)
        page.reload(wait_until="domcontentloaded")
        self.assertIn(work_id, page.evaluate("() => location.hash"))
        _wait_pdf_viewer(page)
        _open_annotations_tab(page)
        page.wait_for_selector("#annotation-fallback-list")
        self.assertEqual(page.locator(".annotation-row").count(), 0)


class MarkdownFixtureTests(_BrowserE2E):
    def test_markdown_security_fixture_variants(self):
        server = FixtureServer()
        self.addCleanup(server.stop)
        server.start()
        for suffix in ("", "?dompurify=absent", "?dompurify=unsupported"):
            page = _BROWSER.new_page()
            try:
                page.goto(
                    server.origin + "/tests/browser/markdown_security.html" + suffix,
                    wait_until="domcontentloaded",
                )
                page.wait_for_function(
                    """() => {
                        const el = document.getElementById('summary');
                        return el && /^PASS/.test(el.textContent || '');
                    }""",
                    timeout=20000,
                )
                summary = page.locator("#summary").inner_text()
                self.assertTrue(summary.startswith("PASS"), summary)
            finally:
                page.close()
