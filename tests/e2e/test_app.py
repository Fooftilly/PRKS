"""Real Chromium + real PRKS server scenarios. Collected only when PRKS_E2E=1."""
from __future__ import annotations

import os
import re
import unittest
from urllib.parse import unquote

from tests.e2e.fixtures import (
    MINIMAL_PDF,
    PERSON_DISPLAY,
    WORK_A_TITLE,
    seed_graph_context_library,
    seed_library,
)
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
    def _start_app(self, seed_fn=seed_library):
        server = AppServer(seed_fn=seed_fn)
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
        page.locator(".person-profile__summary").wait_for()
        page.locator(".person-profile__about").wait_for()
        page.locator(".person-external-links", has_text="References").wait_for()
        page.locator("#person-profile-works-heading", has_text="Linked files").wait_for()
        self.assertTrue(page.locator(".person-sidebar-summary").count() >= 1)
        self.assertTrue(page.locator(".person-sidebar__stats").count() >= 1)
        self.assertGreaterEqual(
            page.locator('.person-sidebar-summary .prks-btn--primary', has_text="Edit profile").count(),
            1,
        )
        self.assertIn(
            "Edit using template",
            page.locator(".person-sidebar-summary").inner_text(),
        )
        self.assertNotIn(
            "Biography, portrait, and external links are in the main column.",
            page.locator(".person-sidebar-summary").inner_text(),
        )
        page.locator('.person-sidebar-summary .prks-btn--primary', has_text="Edit profile").click()
        page.wait_for_selector("#pd-first-name")
        page.locator(".person-panel-edit button", has_text="Cancel").click()
        page.locator("#prks-person-view-graph").wait_for()
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


class ResearchGraphContextTests(_BrowserE2E):
    def test_node_selection_dims_without_mass_edge_labels(self):
        server, page, _collector = self._start_app(seed_fn=seed_graph_context_library)
        work_id = server.ids["work_a"]
        _expand_research(page)
        page.locator('#prks-nav-research-children a.nav-link[href="#/graph"]').click()
        page.wait_for_function("() => location.hash === '#/graph' || location.hash.indexOf('#/graph?') === 0")
        page.wait_for_function("() => window.__prksResearchGraphCy && window.__prksResearchGraphCy.nodes().length > 0")
        work_node = "work:" + work_id
        page.evaluate(
            """(wid) => {
                window.selectGraphNode(wid, { center: false });
            }""",
            arg=work_node,
        )
        state = page.evaluate(
            """(wid) => {
                const cy = window.__prksResearchGraphCy;
                const mention = cy.edges().filter(function (e) {
                    return e.data('type') === 'mentions_concept' && e.source().id() === wid;
                });
                const labeled = cy.edges().filter(function (e) {
                    return e.hasClass('graph-edge--label-on');
                });
                const dimmed = cy.nodes().filter(function (n) {
                    return n.hasClass('graph-dim');
                });
                const focusEdges = cy.edges().filter(function (e) {
                    return e.hasClass('graph-focus');
                });
                const sample = cy.edges()[0];
                return {
                    selected: window.getSelectedGraphNodeId(),
                    mentionCount: mention.length,
                    labeled: labeled.map(function (e) { return e.id(); }),
                    dimmed: dimmed.map(function (n) { return n.id(); }),
                    focusEdges: focusEdges.map(function (e) { return e.id(); }),
                    rotation: sample ? sample.style('text-rotation') : '',
                };
            }""",
            arg=work_node,
        )
        self.assertEqual(state["selected"], work_node)
        self.assertEqual(state["mentionCount"], 2)
        self.assertEqual(state["labeled"], [])
        self.assertEqual(len(state["focusEdges"]), 2)
        self.assertTrue(any("position:" in n or "argument:" in n for n in state["dimmed"]))
        hover = page.evaluate(
            """(wid) => {
                const cy = window.__prksResearchGraphCy;
                const mention = cy.edges().filter(function (e) {
                    return e.data('type') === 'mentions_concept' && e.source().id() === wid;
                });
                const first = mention[0];
                const firstId = first.id();
                cy.getElementById(firstId).emit('mouseover');
                const labeled = cy.edges().filter(function (e) {
                    return e.hasClass('graph-edge--label-on');
                });
                return {
                    labeled: labeled.map(function (e) {
                        return { id: e.id(), label: e.data('canvasLabel') };
                    }),
                    rotation: first.style('text-rotation'),
                };
            }""",
            arg=work_node,
        )
        self.assertEqual(len(hover["labeled"]), 1)
        self.assertTrue(str(hover["labeled"][0]["label"]).startswith("Note mention"))
        self.assertEqual(hover["rotation"], "none")
        page.evaluate(
            """(eid) => { window.selectGraphEdge(eid); }""",
            arg=hover["labeled"][0]["id"],
        )
        page.locator("#prks-graph-inspector-title", has_text="Mentioned in research notes").wait_for()
        inspector = page.locator("#prks-graph-inspector").inner_text()
        self.assertIn("Mentioned in research notes", inspector)
        self.assertIn(WORK_A_TITLE, inspector)
        self.assertTrue("Culture" in inspector or "Philosophy" in inspector)
        page.evaluate("() => window.clearGraphSelection()")
        cleared = page.evaluate(
            """() => ({
                node: window.getSelectedGraphNodeId(),
                edge: window.getSelectedGraphEdgeId(),
                labeled: window.__prksResearchGraphCy.edges().filter(function (e) {
                    return e.hasClass('graph-edge--label-on');
                }).length,
                dimmed: window.__prksResearchGraphCy.nodes().filter(function (n) {
                    return n.hasClass('graph-dim');
                }).length,
            })"""
        )
        self.assertEqual(cleared["node"], "")
        self.assertEqual(cleared["edge"], "")
        self.assertEqual(cleared["labeled"], 0)
        self.assertEqual(cleared["dimmed"], 0)


class ResearchPickerTests(_BrowserE2E):
    def test_insert_concept_and_argument_pickers(self):
        server, page, _collector = self._start_app()
        _open_work_from_home(page, WORK_A_TITLE)
        page.wait_for_selector(".editor-toolbar button.prks-insert-concept")
        page.evaluate(
            """() => {
                localStorage.setItem('prks-theme', 'dark');
                document.documentElement.setAttribute('data-theme', 'dark');
            }"""
        )
        page.locator(".editor-toolbar button.prks-insert-concept").click()
        page.wait_for_selector("#prks-research-picker .prks-dialog")
        q = page.locator("#prks-research-picker input.prks-input")
        self.assertTrue(q.count() >= 1)
        bg = page.evaluate(
            """() => {
                const el = document.querySelector('#prks-research-picker input.prks-input');
                return el ? getComputedStyle(el).backgroundColor : '';
            }"""
        )
        self.assertNotEqual(bg, "rgb(255, 255, 255)")
        self.assertNotEqual(bg, "rgba(0, 0, 0, 0)")
        q.fill("E2E")
        q.press("Enter")
        page.wait_for_function("() => !document.getElementById('prks-research-picker')")
        saved = page.evaluate("() => window.workNotesEasyMDE && window.workNotesEasyMDE.value()")
        self.assertIn("[[concept:", saved)
        page.locator(".editor-toolbar button.prks-insert-argument").click()
        page.wait_for_selector("#prks-research-picker .prks-dialog")
        page.locator("#prks-research-picker input.prks-input").fill("E2E Picker Argument")
        page.locator("#prks-research-picker [data-create='argument']").click()
        page.wait_for_function("() => !document.getElementById('prks-research-picker')")
        page.wait_for_function(
            """() => {
                const v = window.workNotesEasyMDE && window.workNotesEasyMDE.value();
                return v && v.indexOf('[[argument:') !== -1 && v.indexOf('E2E Picker Argument') !== -1;
            }"""
        )
        page.locator(".editor-toolbar button.prks-insert-argument").click()
        page.wait_for_selector("#prks-research-picker")
        page.locator("#prks-research-picker input.prks-input").fill("E2E Search Created Stance")
        self.assertGreaterEqual(page.locator("#prks-research-picker [data-create='stance']").count(), 1)
        self.assertEqual(page.locator("#prks-research-picker [data-new]").count(), 0)
        page.locator("#prks-research-picker [data-create='stance']").click()
        page.wait_for_function("() => !document.getElementById('prks-research-picker')")
        page.wait_for_function(
            """() => {
                const v = window.workNotesEasyMDE && window.workNotesEasyMDE.value();
                return v && v.indexOf('E2E Search Created Stance') !== -1;
            }"""
        )
        page.keyboard.press("Escape")


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


def _pdf_text_band_fractions():
    """Selectable-text location on the seeded minimal.pdf, as fractions of page size.

    PyMuPDF uses a top-left origin matching the rendered page image.
    """
    import fitz

    doc = fitz.open(str(MINIMAL_PDF))
    try:
        page = doc[0]
        words = page.get_text("words")
        if len(words) < 2:
            raise AssertionError("seeded PDF has no selectable words")
        pw, ph = float(page.rect.width), float(page.rect.height)
        x0, y0, _x1, y1 = words[0][:4]
        x1 = words[min(2, len(words) - 1)][2]
        return {
            "fx0": float(x0) / pw,
            "fx1": min(0.95, float(x1) / pw + 0.04),
            "fy": ((float(y0) + float(y1)) / 2.0) / ph,
        }
    finally:
        doc.close()


def _pdf_selection_geometry(page):
    """Viewport coordinates for a real-mouse drag across rendered PDF text."""
    band = _pdf_text_band_fractions()
    page.evaluate(
        """(band) => {
            const viewer = document.querySelector('#pdf-viewer');
            const pageEl = viewer && viewer.querySelector('.prks-pdf-page');
            const vp = viewer && viewer.querySelector('.prks-pdf-viewport');
            if (!pageEl || !vp) return;
            const host = pageEl.firstElementChild instanceof Element ? pageEl.firstElementChild : pageEl;
            const yOnPage = host.offsetHeight * band.fy;
            vp.scrollTop = Math.max(0, yOnPage - vp.clientHeight * 0.35);
        }""",
        band,
    )
    page.wait_for_function(
        """(band) => {
            const viewer = document.querySelector('#pdf-viewer');
            const pageEl = viewer && viewer.querySelector('.prks-pdf-page');
            const vp = viewer && viewer.querySelector('.prks-pdf-viewport');
            if (!pageEl || !vp) return false;
            const host = pageEl.firstElementChild instanceof Element ? pageEl.firstElementChild : pageEl;
            const hr = host.getBoundingClientRect();
            const vr = vp.getBoundingClientRect();
            const y = hr.top + hr.height * band.fy;
            return y >= vr.top + 8 && y <= vr.bottom - 8;
        }""",
        arg=band,
        timeout=5000,
    )
    geo = page.evaluate(
        """(band) => {
            const viewer = document.querySelector('#pdf-viewer');
            const pageEl = viewer && viewer.querySelector('.prks-pdf-page');
            if (!viewer || !pageEl) return { error: 'missing page' };
            const host = pageEl.firstElementChild instanceof Element ? pageEl.firstElementChild : pageEl;
            const vp = viewer.querySelector('.prks-pdf-viewport') || viewer;
            const hr = host.getBoundingClientRect();
            const vr = vp.getBoundingClientRect();
            const clampX = (x) => Math.min(Math.max(x, vr.left + 6), vr.right - 6);
            const clampY = (y) => Math.min(Math.max(y, vr.top + 6), vr.bottom - 6);
            const sx = clampX(hr.left + hr.width * band.fx0);
            const ex = clampX(hr.left + hr.width * band.fx1);
            const y = clampY(hr.top + hr.height * band.fy);
            return {
                sx: sx,
                sy: y,
                ex: ex,
                ey: y,
                source: 'pdf-text-band',
                band: band,
                host: { x: hr.x, y: hr.y, w: hr.width, h: hr.height },
                viewport: { x: vr.x, y: vr.y, w: vr.width, h: vr.height },
            };
        }""",
        band,
    )
    if not geo or geo.get("error"):
        raise AssertionError("could not measure PDF text selection geometry: %s" % geo)
    if abs(geo["ex"] - geo["sx"]) < 20:
        raise AssertionError("PDF selection drag is too short: %s" % geo)
    return geo


def _commit_pdf_highlight(page):
    """Pointer mode → real mouse text selection → selection popup Highlight."""
    _wait_pdf_viewer(page)
    pointer = page.locator('#pdf-viewer .prks-pdf-toolbar [aria-label="Pointer"]')
    pointer.wait_for(state="visible")
    pointer.click()
    page.wait_for_function(
        """() => {
            const b = document.querySelector('#pdf-viewer .prks-pdf-toolbar [aria-label="Pointer"]');
            return b && b.getAttribute('aria-pressed') === 'true';
        }"""
    )
    before = _viewer_annotation_count(page)
    geo = _pdf_selection_geometry(page)
    page.mouse.move(geo["sx"], geo["sy"])
    page.mouse.down()
    page.mouse.move(geo["ex"], geo["ey"], steps=12)
    page.mouse.up()
    page.wait_for_timeout(400)
    popup = page.locator(".prks-pdf-selection-popup")
    try:
        popup.wait_for(state="visible", timeout=8000)
    except Exception as exc:
        raise AssertionError(
            "selection popup did not appear after mouse text selection: %s" % geo
        ) from exc
    highlight = popup.locator("[aria-label='Highlight']")
    highlight.wait_for(state="visible")
    highlight.click()
    page.wait_for_function(
        """(n) => {
            const v = window.currentPdfViewer;
            return !!(v && v.getAnnotations && v.getAnnotations().length > n);
        }""",
        arg=before,
        timeout=10000,
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


_GALLERY_SECTIONS = (
    "typography",
    "surfaces",
    "buttons",
    "entity-choice",
    "icon-buttons",
    "fields",
    "segmented",
    "tabs",
    "cards",
    "rows",
    "tags",
    "chips",
    "badges",
    "status",
    "nav",
    "page-header",
    "toolbar",
    "panels",
    "states",
    "dialogs",
    "research",
    "workspace",
)


class DesignSystemGalleryTests(_BrowserE2E):
    def test_gallery_themes_and_widths(self):
        server = FixtureServer()
        self.addCleanup(server.stop)
        server.start()
        widths = (1440, 900, 600, 390)
        for theme in ("light", "dark"):
            page = _BROWSER.new_page()
            errors = []
            failed = []
            page.on("pageerror", lambda err: errors.append(str(err)))
            page.on(
                "requestfailed",
                lambda req: failed.append(req.url) if req.url.startswith(server.origin) else None,
            )
            try:
                page.set_viewport_size({"width": 1440, "height": 900})
                page.goto(
                    server.origin + "/tests/browser/design_system.html?theme=" + theme,
                    wait_until="domcontentloaded",
                )
                page.wait_for_selector(".prks-gallery__wrap")
                self.assertEqual(
                    page.evaluate("() => document.documentElement.getAttribute('data-theme')"),
                    theme,
                )
                for section in _GALLERY_SECTIONS:
                    loc = page.locator('[data-gallery-section="%s"]' % section)
                    self.assertEqual(loc.count(), 1, section)
                    self.assertTrue(loc.first.is_visible(), section)
                self.assertEqual(errors, [])
                self.assertEqual(failed, [])
                for width in widths:
                    page.set_viewport_size({"width": width, "height": 900})
                    overflow = page.evaluate(
                        """() => {
                            const root = document.documentElement;
                            return root.scrollWidth - root.clientWidth;
                        }"""
                    )
                    self.assertLessEqual(overflow, 2, "theme=%s width=%s overflow=%s" % (theme, width, overflow))
            finally:
                page.close()
