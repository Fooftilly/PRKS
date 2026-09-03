"""Real Chromium + real PRKS server scenarios. Collected only when PRKS_E2E=1."""
from __future__ import annotations

import os
import re
import time
import unittest
from urllib.parse import unquote, urlparse

from tests.e2e.fixtures import (
    MINIMAL_PDF,
    PERSON_DISPLAY,
    WORK_A_TITLE,
    WORK_B_TITLE,
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

_FOCUSED_PDF = """(() => {
    const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
    return (ctx && ctx.getResource) ? ctx.getResource('pdf') : null;
})()"""

_FOCUSED_VIEWER = """(() => {
    const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
    const pdf = (ctx && ctx.getResource) ? ctx.getResource('pdf') : null;
    return pdf && pdf.viewer ? pdf.viewer : null;
})()"""

_FOCUSED_WORK_NOTES = """(() => {
    const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
    const wn = (ctx && ctx.getResource) ? ctx.getResource('workNotes') : null;
    return wn && wn.editor ? wn.editor : wn;
})()"""


def load_tests(loader, standard_tests, pattern):
    if os.environ.get("PRKS_E2E") != "1":
        return unittest.TestSuite()
    return standard_tests


_GRAPH_HAS_NODES = """() => {
    const d = window.prksGetResearchGraphDebug && window.prksGetResearchGraphDebug();
    return !!(d && d.cy && d.cy.nodes().length > 0);
}"""

_GRAPH_DESTROYED = """() => {
    const d = window.prksGetResearchGraphDebug && window.prksGetResearchGraphDebug();
    return !d || !d.cy;
}"""


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
        page.locator('[data-prks-role="editor-status"]', has_text="All changes saved").wait_for()
        page.wait_for_function(
            """() => {
                const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                const work = ctx && ctx.getEntity ? ctx.getEntity('work') : null;
                const refs = work && work.research_refs;
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
        page.wait_for_function(_GRAPH_HAS_NODES)
        page.locator('[data-prks-role="graph-find"]').fill("Culture Industry")
        page.locator(".research-graph__find-hit", has_text="Culture Industry").click()
        page.locator("#prks-graph-inspector-title", has_text="Culture Industry").wait_for()
        topo = page.evaluate(
            """() => {
                const d = window.prksGetResearchGraphDebug && window.prksGetResearchGraphDebug();
                const cy = d && d.cy;
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
        saved = page.evaluate("() => { const ed = %s; return ed && ed.value && ed.value(); }" % _FOCUSED_WORK_NOTES)
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
        page.wait_for_function(_GRAPH_HAS_NODES)
        people = page.locator('[data-graph-filter="people"]')
        self.assertTrue(people.is_checked())
        page.locator("#prks-graph-inspector-title", has_text=PERSON_DISPLAY).wait_for()
        topo = page.evaluate(
            """() => {
                const d = window.prksGetResearchGraphDebug && window.prksGetResearchGraphDebug();
                const cy = d && d.cy;
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
        page.wait_for_function(_GRAPH_DESTROYED)
        page.wait_for_selector(".prks-folder-library, #page-content")
        self.assertNotIn("graph", page.evaluate("() => location.hash"))


class ResearchGraphContextTests(_BrowserE2E):
    def test_node_selection_dims_without_mass_edge_labels(self):
        server, page, _collector = self._start_app(seed_fn=seed_graph_context_library)
        work_id = server.ids["work_a"]
        _expand_research(page)
        page.locator('#prks-nav-research-children a.nav-link[href="#/graph"]').click()
        page.wait_for_function("() => location.hash === '#/graph' || location.hash.indexOf('#/graph?') === 0")
        page.wait_for_function(_GRAPH_HAS_NODES)
        work_node = "work:" + work_id
        page.evaluate(
            """(wid) => {
                window.selectGraphNode(wid, { center: false });
            }""",
            arg=work_node,
        )
        state = page.evaluate(
            """(wid) => {
                const d = window.prksGetResearchGraphDebug && window.prksGetResearchGraphDebug();
                const cy = d && d.cy;
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
                const d = window.prksGetResearchGraphDebug && window.prksGetResearchGraphDebug();
                const cy = d && d.cy;
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
            """() => {
                const d = window.prksGetResearchGraphDebug && window.prksGetResearchGraphDebug();
                const cy = d && d.cy;
                return {
                    node: window.getSelectedGraphNodeId(),
                    edge: window.getSelectedGraphEdgeId(),
                    labeled: cy ? cy.edges().filter(function (e) {
                        return e.hasClass('graph-edge--label-on');
                    }).length : 0,
                    dimmed: cy ? cy.nodes().filter(function (n) {
                        return n.hasClass('graph-dim');
                    }).length : 0,
                };
            }"""
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
        saved = page.evaluate("() => { const ed = %s; return ed && ed.value && ed.value(); }" % _FOCUSED_WORK_NOTES)
        self.assertIn("[[concept:", saved)
        page.locator(".editor-toolbar button.prks-insert-argument").click()
        page.wait_for_selector("#prks-research-picker .prks-dialog")
        page.locator("#prks-research-picker input.prks-input").fill("E2E Picker Argument")
        page.locator("#prks-research-picker [data-create='argument']").click()
        page.wait_for_function("() => !document.getElementById('prks-research-picker')")
        page.wait_for_function(
            """() => {
                const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                const wn = ctx && ctx.getResource ? ctx.getResource('workNotes') : null;
                const v = wn && wn.editor && wn.editor.value ? wn.editor.value() : '';
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
                const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                const wn = ctx && ctx.getResource ? ctx.getResource('workNotes') : null;
                const v = wn && wn.editor && wn.editor.value ? wn.editor.value() : '';
                return v && v.indexOf('E2E Search Created Stance') !== -1;
            }"""
        )
        page.keyboard.press("Escape")


def _wait_pdf_viewer(page):
    page.wait_for_selector('[data-prks-role="pdf-viewer"] .prks-pdf-page', timeout=30000)
    page.wait_for_selector('[data-prks-role="pdf-viewer"] .prks-pdf-render-image', timeout=30000)
    page.wait_for_function(
        """() => {
            const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
            const pdf = ctx && ctx.getResource ? ctx.getResource('pdf') : null;
            const v = pdf && pdf.viewer;
            return !!(v && v.getPageCount && v.getPageCount() >= 1);
        }""",
        timeout=30000,
    )
    page.wait_for_function(
        """() => {
            const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
            const pdf = ctx && ctx.getResource ? ctx.getResource('pdf') : null;
            return !!(pdf && pdf.syncState);
        }""",
        timeout=30000,
    )


def _open_annotations_tab(page):
    page.locator(".tab-btn[data-target='annotations']").click()
    page.wait_for_selector("#annotation-fallback-list")


def _sync_success_at(page):
    return int(
        page.evaluate(
            "() => { const pdf = %s; return (pdf && pdf.syncState && pdf.syncState.lastSuccessAt) || 0; }"
            % _FOCUSED_PDF
        )
        or 0
    )


def _viewer_annotation_count(page):
    return int(
        page.evaluate(
            """() => {
                const v = %s;
                if (!v || typeof v.getAnnotations !== 'function') return 0;
                return (v.getAnnotations() || []).length;
            }"""
            % _FOCUSED_VIEWER
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
            const viewer = document.querySelector('[data-prks-role="pdf-viewer"]');
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
            const viewer = document.querySelector('[data-prks-role="pdf-viewer"]');
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
            const viewer = document.querySelector('[data-prks-role="pdf-viewer"]');
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
    pointer = page.locator('[data-prks-role="pdf-viewer"] .prks-pdf-toolbar [aria-label="Pointer"]')
    pointer.wait_for(state="visible")
    pointer.click()
    page.wait_for_function(
        """() => {
            const b = document.querySelector('[data-prks-role="pdf-viewer"] .prks-pdf-toolbar [aria-label="Pointer"]');
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
            const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
            const pdf = ctx && ctx.getResource ? ctx.getResource('pdf') : null;
            const v = pdf && pdf.viewer;
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
                const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                const pdf = ctx && ctx.getResource ? ctx.getResource('pdf') : null;
                const v = pdf && pdf.viewer;
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


def _release_held_routes(held):
    for route in held:
        try:
            route.abort("canceled")
        except Exception:
            try:
                route.fallback()
            except Exception:
                pass


def _continue_held_routes(held):
    for route in list(held):
        try:
            route.continue_()
        except Exception:
            try:
                route.fallback()
            except Exception:
                pass


class RequestCoordinatorTests(_BrowserE2E):
    def test_identical_work_detail_gets_dedupe_to_one_network_request(self):
        server, page, _collector = self._start_app()
        work_id = server.ids["work_a"]
        seen = []

        def on_request(req):
            if req.method == "GET" and urlparse(req.url).path == "/api/works/" + work_id:
                seen.append(req.url)

        page.on("request", on_request)
        result = page.evaluate(
            """async (id) => {
                const rows = await Promise.all([
                    fetchWorkDetails(id),
                    fetchWorkDetails(id),
                    fetchWorkDetails(id),
                ]);
                return {
                    ids: rows.map(function (r) { return r && r.id; }),
                    titles: rows.map(function (r) { return r && r.title; }),
                };
            }""",
            work_id,
        )
        self.assertEqual(len(seen), 1)
        self.assertEqual(result["ids"], [work_id, work_id, work_id])
        self.assertTrue(all(t == WORK_A_TITLE for t in result["titles"]))

    def test_catalog_burst_cache_skips_immediate_repeat_get(self):
        server, page, _collector = self._start_app()
        seen = []

        def on_request(req):
            if req.method == "GET" and urlparse(req.url).path == "/api/works":
                seen.append(req.url)

        page.on("request", on_request)
        page.evaluate(
            """async () => {
                await prksRequest('/api/diagnostics/performance/reset', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({}),
                });
            }"""
        )
        first = page.evaluate("async () => (await fetchWorks()).length")
        after_first = len(seen)
        self.assertGreaterEqual(after_first, 1)
        second = page.evaluate("async () => (await fetchWorks()).length")
        self.assertEqual(len(seen), after_first)
        self.assertEqual(first, second)
        self.assertGreaterEqual(first, 1)
        page.evaluate(
            """async () => {
                await prksRequest('/api/diagnostics/performance/reset', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({}),
                });
            }"""
        )
        third = page.evaluate("async () => (await fetchWorks()).length")
        self.assertEqual(len(seen), after_first + 1)
        self.assertEqual(third, first)

    def test_route_abort_does_not_stale_paint_or_warn(self):
        server, page, _collector = self._start_app()
        work_id = server.ids["work_a"]
        held = []
        client_errors = []

        def on_request(req):
            if req.method == "POST" and urlparse(req.url).path == "/api/client-errors":
                client_errors.append(req.url)

        def hold_details(route):
            req = route.request
            path = urlparse(req.url).path
            if req.method == "GET" and path == "/api/works/" + work_id:
                held.append(route)
                return
            route.fallback()

        page.on("request", on_request)
        page.route("**/api/works/*", hold_details)
        try:
            page.evaluate(
                "id => { location.hash = '#/works/' + encodeURIComponent(id); }",
                work_id,
            )
            deadline = time.time() + 8
            while time.time() < deadline and not held:
                page.wait_for_timeout(50)
            self.assertTrue(held, "work-detail GET was not intercepted")
            page.locator('#sidebar a.nav-link[href="#/folders"]').click()
            page.wait_for_function("() => location.hash === '#/folders'")
            page.wait_for_selector(".prks-folder-library")
            self.assertEqual(page.locator(".api-warning-banner").count(), 0)
            self.assertEqual(page.locator("#page-content .work-workspace").count(), 0)
            self.assertEqual(page.locator("#page-content .page-header--work-title").count(), 0)
            self.assertEqual(client_errors, [])
        finally:
            _release_held_routes(held)
            try:
                page.unroute("**/api/works/*", hold_details)
            except Exception:
                pass


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


def _workspace_tab_count(page):
    return page.locator(".prks-workspace-tab").count()


def _main_tab_title(page):
    return page.locator(".prks-workspace-tab.is-main .prks-workspace-tab__title").inner_text()


def _open_recent_work_card(page, title):
    page.locator('#sidebar a.nav-link[href="#/folders"]').click()
    page.wait_for_function("() => location.hash === '#/folders'")
    page.locator('.prks-folder-library__tab-btn[data-tab="recently-added"]').click()
    page.locator(".card-title", has_text=title).wait_for()


class WorkspaceTabsTests(_BrowserE2E):
    def test_ctrl_click_opens_background_work_without_fetch_or_browser_page(self):
        server, page, _collector = self._start_app()
        work_id = server.ids["work_a"]
        extra_pages = []
        page.context.on("page", lambda p: extra_pages.append(p))
        detail_gets = []

        def on_request(req):
            path = urlparse(req.url).path
            if req.method == "GET" and path == "/api/works/%s" % work_id:
                detail_gets.append(req.url)

        page.on("request", on_request)
        _open_recent_work_card(page, WORK_A_TITLE)
        self.assertEqual(_workspace_tab_count(page), 1)
        page.locator(".card-title", has_text=WORK_A_TITLE).click(modifiers=["Control"])
        page.wait_for_function("() => document.querySelectorAll('.prks-workspace-tab').length === 2")
        self.assertEqual(page.evaluate("() => location.hash"), "#/folders")
        self.assertEqual(_main_tab_title(page), "Folders")
        self.assertEqual(page.locator(".work-detail").count(), 0)
        self.assertEqual(detail_gets, [])
        self.assertEqual(extra_pages, [])
        self.assertEqual(len(page.context.pages), 1)

        page.locator(".prks-workspace-tab").nth(1).locator(".prks-workspace-tab__activate").click()
        page.wait_for_function("() => location.hash.indexOf('#/works/') === 0")
        page.wait_for_selector(".work-detail")
        self.assertTrue(any(work_id in u for u in detail_gets))
        page.locator(".prks-workspace-tab.is-main .prks-workspace-tab__title", has_text=WORK_A_TITLE).wait_for()
        self.assertEqual(page.locator(".prks-folder-library").count(), 0)

        page.locator(".prks-workspace-tab").nth(0).locator(".prks-workspace-tab__activate").click()
        page.wait_for_function("() => location.hash === '#/folders'")
        self.assertEqual(page.locator(".work-detail").count(), 0)
        self.assertEqual(page.locator('[data-prks-role="pdf-viewer"]').count(), 0)

    def test_middle_click_opens_background_person(self):
        server, page, _collector = self._start_app()
        person_id = server.ids["person"]
        extra_pages = []
        page.context.on("page", lambda p: extra_pages.append(p))
        person_gets = []

        def on_request(req):
            path = urlparse(req.url).path
            if req.method == "GET" and path == "/api/persons/%s" % person_id:
                person_gets.append(req.url)

        page.on("request", on_request)
        page.locator('#sidebar a.nav-link[href="#/people"]').click()
        page.wait_for_function("() => location.hash === '#/people'")
        page.locator(".prks-people-list__title", has_text=PERSON_DISPLAY).wait_for()
        before = _workspace_tab_count(page)
        page.locator(".prks-people-list__title", has_text=PERSON_DISPLAY).click(button="middle")
        page.wait_for_function(
            "n => document.querySelectorAll('.prks-workspace-tab').length === n",
            arg=before + 1,
        )
        self.assertEqual(page.evaluate("() => location.hash"), "#/people")
        self.assertEqual(page.locator(".person-profile").count(), 0)
        self.assertEqual(person_gets, [])
        self.assertEqual(extra_pages, [])
        self.assertEqual(len(page.context.pages), 1)

    def test_close_selects_right_neighbor_then_home(self):
        server, page, _collector = self._start_app()
        work_id = server.ids["work_a"]
        person_id = server.ids["person"]
        page.wait_for_selector(".prks-workspace-tab")
        page.evaluate(
            """async ({ workId, personId }) => {
                await window.prksNavigate('#/people/' + personId, { target: 'new-tab', activate: false });
                await window.prksNavigate('#/works/' + workId, { target: 'new-tab', activate: false });
            }""",
            arg={"workId": work_id, "personId": person_id},
        )
        page.wait_for_function("() => document.querySelectorAll('.prks-workspace-tab').length === 3")
        tabs = page.locator(".prks-workspace-tab")
        tabs.nth(1).locator(".prks-workspace-tab__activate").click()
        page.wait_for_function("() => location.hash.indexOf('#/people/') === 0")
        tabs.nth(1).locator(".prks-workspace-tab__close").click()
        page.wait_for_function("() => document.querySelectorAll('.prks-workspace-tab').length === 2")
        self.assertIn("/works/", page.evaluate("() => location.hash"))
        self.assertEqual(_workspace_tab_count(page), 2)
        hash_before_parked = page.evaluate("() => location.hash")
        page.locator(".prks-workspace-tab").nth(0).locator(".prks-workspace-tab__close").click()
        page.wait_for_function("() => document.querySelectorAll('.prks-workspace-tab').length === 1")
        self.assertEqual(page.evaluate("() => location.hash"), hash_before_parked)
        page.locator(".prks-workspace-tab__close").click()
        page.wait_for_function("() => location.hash === '#/folders'")
        self.assertEqual(_workspace_tab_count(page), 1)
        self.assertEqual(_main_tab_title(page), "Folders")

    def test_new_tab_palette_opens_person_as_main(self):
        server, page, _collector = self._start_app()
        page.wait_for_selector("#prks-workspace-new-tab")
        page.locator("#prks-workspace-new-tab").click()
        page.wait_for_selector("#prks-command-palette:not([hidden])")
        self.assertIn("Open in new tab", page.locator("#prks-command-palette-title").text_content())
        page.locator("#prks-command-palette-input").fill(PERSON_DISPLAY)
        label = page.locator(
            ".prks-command-palette__option-label",
            has_text=re.compile("^" + re.escape(PERSON_DISPLAY) + "$"),
        )
        label.wait_for()
        page.locator(".prks-command-palette__option").filter(has=label).click()
        page.wait_for_function("() => location.hash.indexOf('#/people/') === 0")
        self.assertGreaterEqual(_workspace_tab_count(page), 2)
        page.locator(".person-profile__summary").wait_for()
        self.assertNotEqual(page.evaluate("() => location.hash"), "#/folders")
        self.assertEqual(page.locator(".prks-route-loading").count(), 0)

    def test_pending_research_notes_flush_on_tab_switch(self):
        server, page, _collector = self._start_app()
        person_id = server.ids["person"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.wait_for_selector(".CodeMirror")
        page.evaluate(
            """(pid) => window.prksNavigate('#/people/' + pid, { target: 'new-tab', activate: false })""",
            arg=person_id,
        )
        page.wait_for_function("() => document.querySelectorAll('.prks-workspace-tab').length === 2")
        unique = "FAST-SWITCH-NOTE-%s" % int(time.time() * 1000)
        page.locator(".CodeMirror").click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(unique)
        page.locator('[data-prks-role="editor-status"]', has_text="Drafting").wait_for()
        with page.expect_response(
            lambda r: r.request.method == "PATCH" and "/api/works/" in r.url and r.ok
        ):
            page.locator(".prks-workspace-tab").nth(1).locator(".prks-workspace-tab__activate").click()
        page.wait_for_function("() => location.hash.indexOf('#/people/') === 0")
        page.locator(".prks-workspace-tab").nth(0).locator(".prks-workspace-tab__activate").click()
        page.wait_for_selector(".CodeMirror")
        page.wait_for_function(
            """(text) => {
                const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                const wn = ctx && ctx.getResource ? ctx.getResource('workNotes') : null;
                const ed = wn && wn.editor ? wn.editor : wn;
                return !!(ed && ed.value && ed.value().indexOf(text) !== -1);
            }""",
            arg=unique,
        )

    def test_pdf_unmounts_when_parked_and_remounts(self):
        server, page, _collector = self._start_app()
        person_id = server.ids["person"]
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        page.evaluate(
            """(pid) => window.prksNavigate('#/people/' + pid, { target: 'new-tab', activate: true })""",
            arg=person_id,
        )
        page.wait_for_function("() => location.hash.indexOf('#/people/') === 0")
        self.assertEqual(page.locator('[data-prks-role="pdf-viewer"]').count(), 0)
        page.locator(".prks-workspace-tab").nth(0).locator(".prks-workspace-tab__activate").click()
        page.wait_for_function("() => location.hash.indexOf('#/works/') === 0")
        _wait_pdf_viewer(page)

    def test_tab_switch_does_not_rewrite_contextual_back(self):
        server, page, _collector = self._start_app()
        person_id = server.ids["person"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.wait_for_selector(".prks-nav-back")
        work_back = page.locator(".prks-nav-back").get_attribute("href")
        page.evaluate(
            """async (pid) => {
                await window.prksNavigate('#/people', { target: 'new-tab', activate: true });
                await window.prksNavigate('#/people/' + pid);
            }""",
            arg=person_id,
        )
        page.wait_for_function("() => location.hash.indexOf('#/people/') === 0")
        page.wait_for_selector(".prks-nav-back")
        person_back = page.locator(".prks-nav-back").get_attribute("href")
        page.locator(".prks-workspace-tab").nth(0).locator(".prks-workspace-tab__activate").click()
        page.wait_for_function("() => location.hash.indexOf('#/works/') === 0")
        page.wait_for_selector(".prks-nav-back")
        self.assertEqual(page.locator(".prks-nav-back").get_attribute("href"), work_back)
        page.locator(".prks-workspace-tab").nth(1).locator(".prks-workspace-tab__activate").click()
        page.wait_for_function("() => location.hash.indexOf('#/people/') === 0")
        page.wait_for_selector(".prks-nav-back")
        self.assertEqual(page.locator(".prks-nav-back").get_attribute("href"), person_back)

    def test_browser_back_forward_keeps_parked_tab(self):
        server, page, _collector = self._start_app()
        person_id = server.ids["person"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.wait_for_function("() => location.hash.indexOf('#/works/') === 0")
        page.evaluate(
            """(pid) => window.prksNavigate('#/people/' + pid)""",
            arg=person_id,
        )
        page.wait_for_function("() => location.hash.indexOf('#/people/') === 0")
        page.evaluate("() => window.prksNavigate('#/concepts', { target: 'new-tab', activate: false })")
        page.wait_for_function("() => document.querySelectorAll('.prks-workspace-tab').length === 2")
        page.go_back()
        page.wait_for_function("() => location.hash.indexOf('#/works/') === 0")
        self.assertEqual(_workspace_tab_count(page), 2)
        page.go_forward()
        page.wait_for_function("() => location.hash.indexOf('#/people/') === 0")
        self.assertEqual(_workspace_tab_count(page), 2)

    def test_pdf_leave_guard_skips_parked_open_blocks_activated(self):
        server, page, _collector = self._start_app()
        work_id = server.ids["work_a"]
        person_id = server.ids["person"]
        work_hash = "#/works/" + work_id
        person_gets = []

        def on_request(req):
            path = urlparse(req.url).path
            if req.method == "GET" and path == "/api/persons/%s" % person_id:
                person_gets.append(req.url)

        page.on("request", on_request)
        dialogs = []

        def on_dialog(dialog):
            dialogs.append(dialog.message)
            dialog.dismiss()

        page.on("dialog", on_dialog)
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        page.wait_for_selector("a.work-linked-persons__chip-link")
        page.evaluate(
            """() => {
                window.prksHasPendingWorkAnnotationSync = function () { return true; };
            }"""
        )
        tabs_before = _workspace_tab_count(page)
        denied = page.evaluate(
            """async (pid) => {
                return await window.prksNavigate('#/people/' + pid, { target: 'new-tab', activate: true });
            }""",
            arg=person_id,
        )
        self.assertFalse(denied)
        self.assertEqual(_workspace_tab_count(page), tabs_before)
        self.assertEqual(page.evaluate("() => location.hash"), work_hash)
        self.assertGreaterEqual(page.locator(".work-detail").count(), 1)
        self.assertEqual(page.locator(".person-profile").count(), 0)
        self.assertEqual(person_gets, [])
        self.assertEqual(len(dialogs), 1)
        self.assertIn("PDF annotation sync", dialogs[0])

        page.locator("a.work-linked-persons__chip-link", has_text=PERSON_DISPLAY).click(
            modifiers=["Control"]
        )
        page.wait_for_function("() => document.querySelectorAll('.prks-workspace-tab').length === 2")
        self.assertEqual(page.evaluate("() => location.hash"), work_hash)
        self.assertEqual(_workspace_tab_count(page), tabs_before + 1)
        self.assertGreaterEqual(page.locator(".work-detail").count(), 1)
        self.assertEqual(page.locator(".person-profile").count(), 0)
        self.assertEqual(person_gets, [])
        self.assertEqual(len(dialogs), 1)

        page.locator(".prks-workspace-tab").nth(1).locator(".prks-workspace-tab__activate").click()
        self.assertEqual(len(dialogs), 2)
        self.assertEqual(page.evaluate("() => location.hash"), work_hash)
        self.assertEqual(_workspace_tab_count(page), tabs_before + 1)
        self.assertGreaterEqual(page.locator(".work-detail").count(), 1)
        self.assertEqual(page.locator(".person-profile").count(), 0)
        self.assertEqual(person_gets, [])


_ONE_STACKED_ROOT = """() => document.querySelectorAll('.prks-tab-root').length === 1"""


def _tab_root_id(page):
    return page.evaluate(
        """() => {
            const el = document.querySelector('.prks-tab-root');
            return el ? el.getAttribute('data-prks-tab-id') : null;
        }"""
    )


def _host_child_classes(page):
    return page.evaluate(
        """() => {
            const host = document.getElementById('page-content');
            return Array.from(host.children).map((el) => el.className || '');
        }"""
    )


class TabContextHostRootTests(_BrowserE2E):
    def _assert_single_mounted_root(self, page, expected_id=None):
        page.wait_for_function(_ONE_STACKED_ROOT)
        self.assertEqual(page.locator(".prks-tab-root").count(), 1)
        classes = _host_child_classes(page)
        self.assertEqual(len(classes), 1)
        self.assertIn("prks-workspace-canvas", classes[0])
        rid = _tab_root_id(page)
        if expected_id is not None:
            self.assertEqual(rid, expected_id)
        return rid

    def test_person_edit_keeps_same_tab_root(self):
        _server, page, _collector = self._start_app()
        page.locator('#sidebar a.nav-link[href="#/people"]').click()
        page.wait_for_function("() => location.hash === '#/people'")
        page.locator(".prks-people-list__title", has_text=PERSON_DISPLAY).click()
        page.wait_for_function("() => location.hash.indexOf('#/people/') === 0")
        page.locator(".person-profile__summary").wait_for()
        root_id = self._assert_single_mounted_root(page)
        self.assertGreaterEqual(
            page.locator(".prks-tab-root .person-profile").count(),
            1,
        )
        page.locator(".person-sidebar-summary .prks-btn--secondary", has_text="Edit works").click()
        page.wait_for_selector(".person-profile__work-card-wrap, .person-profile__role-block")
        self._assert_single_mounted_root(page, root_id)
        self.assertGreaterEqual(
            page.locator(".prks-tab-root .person-profile").count(),
            1,
        )
        page.locator(".person-sidebar-summary .prks-btn--secondary", has_text="Done").click()
        self._assert_single_mounted_root(page, root_id)
        page.locator('.person-sidebar-summary .prks-btn--primary', has_text="Edit profile").click()
        page.wait_for_selector("#pd-about")
        page.fill("#pd-about", "Host-root profile save")
        page.locator("#pd-save-btn").click()
        page.wait_for_selector(".person-profile__summary")
        page.locator(".person-profile__about", has_text="Host-root profile save").wait_for()
        self._assert_single_mounted_root(page, root_id)
        self.assertGreaterEqual(
            page.locator(".prks-tab-root .person-profile").count(),
            1,
        )

    def test_playlist_refresh_keeps_same_tab_root(self):
        _server, page, _collector = self._start_app()
        page.locator('#sidebar a.nav-link[href="#/playlists"]').click()
        page.wait_for_function("() => location.hash === '#/playlists'")
        page.evaluate("() => openModal('playlist-modal')")
        page.wait_for_selector("#playlist-title")
        page.fill("#playlist-title", "E2E Host Playlist")
        page.locator("#save-playlist-btn").click()
        page.wait_for_function("() => location.hash.indexOf('#/playlists/') === 0")
        page.locator(".prks-playlist-detail").wait_for()
        root_id = self._assert_single_mounted_root(page)
        page.locator("#prks-playlist-edit-btn").click()
        page.wait_for_selector("#prks-playlist-edit-desc")
        page.fill("#prks-playlist-edit-desc", "Refreshed in owner root")
        page.locator("#prks-playlist-edit-save").click()
        page.wait_for_function(
            """() => {
                const el = document.querySelector('.prks-tab-root');
                return !!(el && el.innerText && el.innerText.indexOf('Refreshed in owner root') !== -1);
            }"""
        )
        self._assert_single_mounted_root(page, root_id)
        self.assertGreaterEqual(
            page.locator(".prks-tab-root .prks-playlist-detail").count(),
            1,
        )

    def test_folder_create_refresh_stays_in_tab_root(self):
        _server, page, _collector = self._start_app()
        page.locator('#sidebar a.nav-link[href="#/folders"]').click()
        page.wait_for_function("() => location.hash === '#/folders'")
        page.wait_for_selector(".prks-folder-library")
        root_id = self._assert_single_mounted_root(page)
        page.evaluate("() => openModal('folder-modal')")
        page.wait_for_selector("#folder-title")
        page.fill("#folder-title", "E2E Host Folder")
        page.locator("#save-folder-btn").click()
        page.wait_for_selector(".prks-folder-library")
        page.get_by_text("E2E Host Folder").wait_for()
        self._assert_single_mounted_root(page, root_id)
        self.assertGreaterEqual(
            page.locator(".prks-tab-root .prks-folder-library").count(),
            1,
        )
        self.assertEqual(page.evaluate("() => location.hash"), "#/folders")

    def test_processing_files_refresh_keeps_tab_root(self):
        _server, page, _collector = self._start_app()
        page.locator('#sidebar a.nav-link[href="#/processing-files"]').click()
        page.wait_for_function("() => location.hash === '#/processing-files'")
        page.locator("#prks-processing-refresh").wait_for()
        page.locator("h2.prks-page-title", has_text="Files for Processing").wait_for()
        root_id = self._assert_single_mounted_root(page)
        page.locator("#prks-processing-refresh").click()
        page.wait_for_function(
            """() => {
                const b = document.getElementById('prks-processing-refresh');
                return !!(b && !b.disabled && b.textContent && b.textContent.indexOf('Refresh') !== -1);
            }"""
        )
        self._assert_single_mounted_root(page, root_id)
        self.assertGreaterEqual(
            page.locator(".prks-tab-root #prks-processing-refresh").count(),
            1,
        )

    def test_right_panel_tab_restored_per_context(self):
        server, page, _collector = self._start_app()
        person_id = server.ids["person"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.wait_for_selector(".work-detail")
        page.locator('#right-panel .tab-btn[data-target="annotations"]').click()
        page.wait_for_selector("#annotation-fallback-list")
        self.assertTrue(
            page.locator('#right-panel .tab-btn[data-target="annotations"]').evaluate(
                "el => el.classList.contains('active')"
            )
        )
        page.evaluate(
            """(pid) => window.prksNavigate('#/people/' + pid, { target: 'new-tab', activate: true })""",
            arg=person_id,
        )
        page.wait_for_function("() => document.querySelectorAll('.prks-workspace-tab').length === 2")
        page.wait_for_function("() => location.hash.indexOf('#/people/') === 0")
        page.locator(".person-profile__summary").wait_for()
        page.wait_for_function(
            """() => {
                const btn = document.querySelector('#right-panel .tab-btn[data-target="details"]');
                return !!(btn && btn.classList.contains('active'));
            }"""
        )
        page.locator(".prks-workspace-tab").nth(0).locator(".prks-workspace-tab__activate").click()
        page.wait_for_function("() => location.hash.indexOf('#/works/') === 0")
        page.wait_for_selector(".work-detail")
        page.wait_for_function(
            """() => {
                const btn = document.querySelector('#right-panel .tab-btn[data-target="annotations"]');
                return !!(btn && btn.classList.contains('active'));
            }"""
        )
        page.wait_for_selector("#annotation-fallback-list")
        page.locator(".prks-workspace-tab").nth(1).locator(".prks-workspace-tab__activate").click()
        page.wait_for_function("() => location.hash.indexOf('#/people/') === 0")
        page.locator(".person-profile__summary").wait_for()
        page.wait_for_function(
            """() => {
                const btn = document.querySelector('#right-panel .tab-btn[data-target="details"]');
                return !!(btn && btn.classList.contains('active'));
            }"""
        )

    def test_work_meta_save_does_not_publish_into_other_work(self):
        server, page, _collector = self._start_app()
        work_a = server.ids["work_a"]
        work_b = server.ids["work_b"]
        held = []

        def hold_patch(route):
            req = route.request
            path = urlparse(req.url).path
            if req.method == "PATCH" and path == "/api/works/" + work_a:
                held.append(route)
                return
            route.fallback()

        _open_work_from_home(page, WORK_A_TITLE)
        page.wait_for_selector(".work-detail")
        page.locator("#panel-content button.inline-action-btn", has_text="Edit").click()
        page.wait_for_selector("#meta-title")
        page.fill("#meta-title", "E2E Research Work Saved")
        page.route("**/api/works/*", hold_patch)
        try:
            page.locator("#inline-save-metadata-btn").click()
            deadline = time.time() + 8
            while time.time() < deadline and not held:
                page.wait_for_timeout(50)
            self.assertTrue(held, "Work A metadata PATCH was not intercepted")
            page.evaluate(
                """(id) => window.prksNavigate('#/works/' + id, { target: 'new-tab', activate: true })""",
                arg=work_b,
            )
            page.wait_for_function("() => document.querySelectorAll('.prks-workspace-tab').length === 2")
            page.wait_for_function(
                "id => location.hash.indexOf('#/works/' + id) === 0",
                arg=work_b,
            )
            page.wait_for_selector(".work-detail")
            page.get_by_text(WORK_B_TITLE).first.wait_for()
            before = page.evaluate(
                """() => {
                    const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                    const work = ctx && ctx.getEntity ? ctx.getEntity('work') : null;
                    const panel = document.getElementById('panel-content');
                    return {
                        workId: work && work.id,
                        title: work && work.title,
                        tab: ctx && ctx.ui ? ctx.ui.rightPanelTab : null,
                        panel: panel ? panel.innerText : '',
                    };
                }"""
            )
            self.assertEqual(before["workId"], work_b)
            self.assertEqual(before["title"], WORK_B_TITLE)
            _continue_held_routes(held)
            page.wait_for_timeout(400)
            after = page.evaluate(
                """() => {
                    const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                    const work = ctx && ctx.getEntity ? ctx.getEntity('work') : null;
                    const panel = document.getElementById('panel-content');
                    return {
                        hash: location.hash,
                        workId: work && work.id,
                        title: work && work.title,
                        tab: ctx && ctx.ui ? ctx.ui.rightPanelTab : null,
                        panel: panel ? panel.innerText : '',
                    };
                }"""
            )
            self.assertIn(work_b, after["hash"])
            self.assertEqual(after["workId"], work_b)
            self.assertEqual(after["title"], WORK_B_TITLE)
            self.assertEqual(after["tab"], before["tab"])
            self.assertEqual(after["panel"], before["panel"])
            self.assertNotIn("E2E Research Work Saved", after["panel"])
        finally:
            _continue_held_routes(held)
            try:
                page.unroute("**/api/works/*", hold_patch)
            except Exception:
                pass
        page.locator(".prks-workspace-tab").nth(0).locator(".prks-workspace-tab__activate").click()
        page.wait_for_function(
            "id => location.hash.indexOf('#/works/' + id) === 0",
            arg=work_a,
        )
        page.wait_for_selector(".work-detail")
        self.assertGreaterEqual(page.locator(".work-detail").count(), 1)

    def test_non_focused_work_render_does_not_steal_right_panel(self):
        server, page, _collector = self._start_app()
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.wait_for_selector(".work-detail")
        page.locator('#right-panel .tab-btn[data-target="annotations"]').click()
        page.wait_for_selector("#annotation-fallback-list")
        result = page.evaluate(
            """async (workBId) => {
                const focused = window.prksGetFocusedTabContext();
                const beforeTab = focused && focused.ui ? focused.ui.rightPanelTab : null;
                const beforeWork = focused && focused.getEntity ? focused.getEntity('work') : null;
                const host = document.createElement('div');
                host.id = 'prks-test-secondary-host';
                host.style.display = 'none';
                document.body.appendChild(host);
                const ctxB = window.prksEnsureTabContext('prks-test-secondary');
                ctxB.mount(host);
                const workB = await fetchWorkDetails(workBId);
                await renderWorkDetails(ctxB, workB, { generation: ctxB.generation });
                const afterFocused = window.prksGetFocusedTabContext();
                const afterWork = afterFocused && afterFocused.getEntity ? afterFocused.getEntity('work') : null;
                const annBtn = document.querySelector('#right-panel .tab-btn[data-target="annotations"]');
                const out = {
                    focusedTab: afterFocused && afterFocused.ui ? afterFocused.ui.rightPanelTab : null,
                    focusedWorkId: afterWork && afterWork.id,
                    beforeTab: beforeTab,
                    beforeWorkId: beforeWork && beforeWork.id,
                    bWorkId: ctxB.getEntity && ctxB.getEntity('work') && ctxB.getEntity('work').id,
                    annotationsActive: !!(annBtn && annBtn.classList.contains('active')),
                    annotationList: !!document.getElementById('annotation-fallback-list'),
                };
                if (typeof window.prksDestroyTabContext === 'function') {
                    window.prksDestroyTabContext('prks-test-secondary');
                }
                if (host.parentNode) host.parentNode.removeChild(host);
                return out;
            }""",
            arg=work_b,
        )
        self.assertEqual(result["beforeTab"], "annotations")
        self.assertEqual(result["focusedTab"], "annotations")
        self.assertEqual(result["focusedWorkId"], result["beforeWorkId"])
        self.assertEqual(result["bWorkId"], work_b)
        self.assertNotEqual(result["bWorkId"], result["focusedWorkId"])
        self.assertTrue(result["annotationsActive"])
        self.assertTrue(result["annotationList"])

    def test_person_group_save_does_not_navigate_other_tab(self):
        _server, page, _collector = self._start_app()
        held = []
        page.evaluate("() => window.prksNavigate('#/people/groups')")
        page.wait_for_function("() => location.hash === '#/people/groups'")
        page.wait_for_selector(".prks-group-library")
        page.evaluate("() => openModal('group-modal')")
        page.wait_for_selector("#group-name")
        page.fill("#group-name", "E2E Owner Group")
        page.locator("#save-group-btn").click()
        page.wait_for_function("() => location.hash.indexOf('#/people/groups/') === 0")
        group_id = page.evaluate("() => location.hash.split('/')[3]")
        page.locator("#panel-content button", has_text="Edit group").click()
        page.wait_for_selector("#gd-save-btn")

        def hold_group_patch(route):
            req = route.request
            path = urlparse(req.url).path
            if req.method == "PATCH" and path == "/api/person-groups/" + group_id:
                held.append(route)
                return
            route.fallback()

        page.route("**/api/person-groups/*", hold_group_patch)
        try:
            page.locator("#gd-save-btn").click()
            deadline = time.time() + 8
            while time.time() < deadline and not held:
                page.wait_for_timeout(50)
            self.assertTrue(held, "Person Group PATCH was not intercepted")
            page.evaluate("() => window.prksNavigate('#/folders', { target: 'new-tab', activate: true })")
            page.wait_for_function("() => document.querySelectorAll('.prks-workspace-tab').length === 2")
            page.wait_for_function("() => location.hash === '#/folders'")
            _continue_held_routes(held)
            page.wait_for_timeout(400)
            self.assertEqual(page.evaluate("() => location.hash"), "#/folders")
        finally:
            _continue_held_routes(held)
            try:
                page.unroute("**/api/person-groups/*", hold_group_patch)
            except Exception:
                pass


def _wait_pdf_tab(page, tab_id):
    page.wait_for_function(
        """(tabId) => {
            const ctx = window.prksGetTabContext && window.prksGetTabContext(tabId);
            const pdf = ctx && ctx.getResource ? ctx.getResource('pdf') : null;
            const v = pdf && pdf.viewer;
            return !!(v && v.getPageCount && v.getPageCount() >= 1 && pdf.syncState);
        }""",
        arg=tab_id,
        timeout=30000,
    )


def _workspace_ids(page):
    return page.evaluate(
        """() => {
            const snap = window.prksWorkspaceSnapshot && window.prksWorkspaceSnapshot();
            const debug = window.prksTabContextDebugSnapshot && window.prksTabContextDebugSnapshot();
            return {
                mode: snap && snap.mode,
                mainTabId: snap && snap.mainTabId,
                focusedTabId: snap && snap.focusedTabId,
                secondaryTabId: snap && snap.secondaryTree && snap.secondaryTree.tabId,
                hash: location.hash,
                title: document.title,
                mountedCount: debug && debug.mountedCount,
            };
        }"""
    )


class WorkspaceTilingTests(_BrowserE2E):
    def test_work_work_runtimes_focus_notes_make_main(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_a = server.ids["work_a"]
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        extra_pages = []
        page.context.on("page", lambda p: extra_pages.append(p))
        page.evaluate(
            """(id) => window.prksNavigate('#/works/' + id, { target: 'tile' })""",
            arg=work_b,
        )
        page.wait_for_function("() => document.querySelectorAll('.work-detail').length === 2")
        page.wait_for_function(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                return snap && snap.mode === 'tiled' && snap.secondaryTree && snap.secondaryTree.type === 'leaf';
            }"""
        )
        ids = _workspace_ids(page)
        self.assertEqual(ids["mode"], "tiled")
        self.assertEqual(ids["mountedCount"], 2)
        self.assertNotEqual(ids["mainTabId"], ids["secondaryTabId"])
        self.assertEqual(ids["focusedTabId"], ids["secondaryTabId"])
        self.assertIn(work_a, ids["hash"])
        self.assertIn(WORK_A_TITLE, ids["title"])
        _wait_pdf_tab(page, ids["mainTabId"])
        _wait_pdf_tab(page, ids["secondaryTabId"])
        self.assertEqual(page.locator('[data-prks-role="pdf-viewer"]').count(), 2)
        self.assertEqual(len(page.context.pages), 1)
        self.assertEqual(extra_pages, [])

        isolated = page.evaluate(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                const a = window.prksGetTabContext(snap.mainTabId);
                const b = window.prksGetTabContext(snap.secondaryTree.tabId);
                const aPdf = a.getResource('pdf');
                const bPdf = b.getResource('pdf');
                const aNotes = a.getResource('workNotes');
                const bNotes = b.getResource('workNotes');
                window.__prksTileRt = { aPdf: aPdf, bPdf: bPdf, aNotes: aNotes, bNotes: bNotes };
                return {
                    ctx: a !== b,
                    pdf: aPdf !== bPdf,
                    notes: aNotes !== bNotes,
                    timers: a.timers !== b.timers,
                    mounted: window.prksTabContextDebugSnapshot().mountedCount,
                };
            }"""
        )
        self.assertTrue(isolated["ctx"])
        self.assertTrue(isolated["pdf"])
        self.assertTrue(isolated["notes"])
        self.assertEqual(isolated["mounted"], 2)

        page.locator(".prks-tile--main").click(position={"x": 24, "y": 80})
        page.wait_for_function("() => window.prksWorkspaceSnapshot().focusedTabId === window.prksWorkspaceSnapshot().mainTabId")
        page.locator('#right-panel .tab-btn[data-target="annotations"]').click()
        page.wait_for_selector("#annotation-fallback-list")
        self.assertIn(work_a, page.evaluate("() => location.hash"))
        page.locator(".prks-tile--secondary").click(position={"x": 24, "y": 80})
        page.wait_for_function(
            "() => window.prksWorkspaceSnapshot().focusedTabId === window.prksWorkspaceSnapshot().secondaryTree.tabId"
        )
        self.assertIn(work_a, page.evaluate("() => location.hash"))
        self.assertEqual(page.locator('[data-prks-role="pdf-viewer"]').count(), 2)

        unique_a = "TILE-NOTE-A-%s" % int(time.time() * 1000)
        unique_b = "TILE-NOTE-B-%s" % int(time.time() * 1000)
        page.locator(".prks-tile--main .CodeMirror").click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(unique_a)
        page.locator(".prks-tile--secondary .CodeMirror").click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(unique_b)
        page.wait_for_function(
            """(pair) => {
                function val(tabId) {
                    const ctx = window.prksGetTabContext(tabId);
                    const wn = ctx && ctx.getResource('workNotes');
                    const ed = wn && wn.editor ? wn.editor : wn;
                    return ed && ed.value ? ed.value() : '';
                }
                const snap = window.prksWorkspaceSnapshot();
                return val(snap.mainTabId).indexOf(pair.a) !== -1 && val(snap.secondaryTree.tabId).indexOf(pair.b) !== -1;
            }""",
            arg={"a": unique_a, "b": unique_b},
        )
        page.evaluate(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                if (window.prksFlushPendingWorkResearchNotes) {
                    window.prksFlushPendingWorkResearchNotes(window.prksGetTabContext(snap.mainTabId));
                    window.prksFlushPendingWorkResearchNotes(window.prksGetTabContext(snap.secondaryTree.tabId));
                }
                const a = window.prksGetTabContext(snap.mainTabId);
                const b = window.prksGetTabContext(snap.secondaryTree.tabId);
                window.__prksTileRt = {
                    aPdf: a.getResource('pdf'),
                    bPdf: b.getResource('pdf'),
                    aNotes: a.getResource('workNotes'),
                    bNotes: b.getResource('workNotes'),
                };
            }"""
        )

        page.locator(".prks-tile--secondary .prks-tile-header__make-main").click()
        page.wait_for_function(
            """(b) => {
                const snap = window.prksWorkspaceSnapshot();
                return snap.mainTabId && location.hash.indexOf(b) !== -1 && snap.secondaryTree && snap.mainTabId !== snap.secondaryTree.tabId;
            }""",
            arg=work_b,
        )
        same = page.evaluate(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                const a = window.prksGetTabContext(snap.secondaryTree.tabId);
                const b = window.prksGetTabContext(snap.mainTabId);
                const rt = window.__prksTileRt;
                return {
                    aPdf: a.getResource('pdf') === rt.aPdf,
                    bPdf: b.getResource('pdf') === rt.bPdf,
                    aNotes: a.getResource('workNotes') === rt.aNotes,
                    bNotes: b.getResource('workNotes') === rt.bNotes,
                    mounted: window.prksTabContextDebugSnapshot().mountedCount,
                };
            }"""
        )
        self.assertTrue(same["aPdf"])
        self.assertTrue(same["bPdf"])
        self.assertTrue(same["aNotes"])
        self.assertTrue(same["bNotes"])
        self.assertEqual(same["mounted"], 2)
        self.assertIn(work_b, page.evaluate("() => location.hash"))
        self.assertIn(WORK_B_TITLE, page.evaluate("() => document.title"))
        self.assertNotEqual(page.evaluate("() => document.title"), "Work — PRKS")
        self.assertEqual(len(page.context.pages), 1)

    def test_alt_click_opens_tile_and_ctrl_still_parks(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        extra_pages = []
        page.context.on("page", lambda p: extra_pages.append(p))
        _open_work_from_home(page, WORK_A_TITLE)
        page.wait_for_selector("a.work-linked-persons__chip-link")
        work_hash = page.evaluate("() => location.hash")
        page.locator("a.work-linked-persons__chip-link", has_text=PERSON_DISPLAY).click(modifiers=["Alt"])
        page.wait_for_selector(".prks-tile--secondary .person-profile")
        page.wait_for_function(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                return snap && snap.mode === 'tiled' && document.querySelectorAll('.prks-tab-root').length === 2;
            }"""
        )
        ids = _workspace_ids(page)
        self.assertEqual(ids["hash"], work_hash)
        self.assertEqual(ids["mountedCount"], 2)
        self.assertEqual(ids["focusedTabId"], ids["secondaryTabId"])
        self.assertGreaterEqual(page.locator(".person-profile").count(), 1)
        self.assertGreaterEqual(page.locator(".work-detail").count(), 1)
        self.assertEqual(len(page.context.pages), 1)
        self.assertEqual(extra_pages, [])

        before = _workspace_tab_count(page)
        page.locator(".prks-tile--main a[href^='#/']").first.click(modifiers=["Control"])
        page.wait_for_function(
            "n => document.querySelectorAll('.prks-workspace-tab').length === n",
            arg=before + 1,
        )
        self.assertEqual(page.evaluate("() => location.hash"), work_hash)
        self.assertEqual(_workspace_ids(page)["mountedCount"], 2)
        self.assertEqual(len(page.context.pages), 1)

    def test_secondary_navigation_does_not_change_url(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_b = server.ids["work_b"]
        person_id = server.ids["person"]
        _open_work_from_home(page, WORK_A_TITLE)
        work_hash = page.evaluate("() => location.hash")
        page.evaluate(
            """(id) => window.prksNavigate('#/works/' + id, { target: 'tile' })""",
            arg=work_b,
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'tiled'")
        page.evaluate(
            """(pid) => {
                const snap = window.prksWorkspaceSnapshot();
                return window.prksNavigate('#/people/' + pid, { target: 'current', tabId: snap.secondaryTree.tabId });
            }""",
            arg=person_id,
        )
        page.wait_for_selector(".prks-tile--secondary .person-profile")
        self.assertEqual(page.evaluate("() => location.hash"), work_hash)
        page.evaluate(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                window.prksWorkspaceMakeMain(snap.secondaryTree.tabId);
            }"""
        )
        page.wait_for_function("() => location.hash.indexOf('#/people/') === 0")
        self.assertIn("/people/", page.evaluate("() => location.hash"))

    def test_tile_replace_and_stack_leave_guards(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_b = server.ids["work_b"]
        person_id = server.ids["person"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.evaluate(
            """(id) => window.prksNavigate('#/works/' + id, { target: 'tile' })""",
            arg=work_b,
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'tiled'")
        dialogs = []
        accept_next = {"v": False}

        def on_dialog(dialog):
            dialogs.append(dialog.message)
            if accept_next["v"]:
                dialog.accept()
            else:
                dialog.dismiss()

        page.on("dialog", on_dialog)
        page.evaluate(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                const ctx = window.prksGetTabContext(snap.secondaryTree.tabId);
                window.prksHasPendingWorkAnnotationSync = function (c) {
                    return !!(c && ctx && c.tabId === ctx.tabId);
                };
            }"""
        )
        before = _workspace_ids(page)
        denied = page.evaluate(
            """(pid) => window.prksNavigate('#/people/' + pid, { target: 'tile' })""",
            arg=person_id,
        )
        self.assertFalse(denied)
        after = _workspace_ids(page)
        self.assertEqual(after["secondaryTabId"], before["secondaryTabId"])
        self.assertEqual(after["mainTabId"], before["mainTabId"])
        self.assertEqual(after["focusedTabId"], before["focusedTabId"])
        self.assertEqual(after["hash"], before["hash"])
        self.assertEqual(len(dialogs), 1)

        stack_denied = page.evaluate("() => window.prksWorkspaceSetMode('stacked')")
        self.assertFalse(stack_denied)
        self.assertEqual(page.evaluate("() => window.prksWorkspaceSnapshot().mode"), "tiled")
        self.assertEqual(len(dialogs), 2)

        accept_next["v"] = True
        stacked = page.evaluate("() => window.prksWorkspaceSetMode('stacked')")
        self.assertTrue(stacked)
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'stacked'")
        snap = page.evaluate("() => window.prksWorkspaceSnapshot()")
        self.assertEqual(snap["mode"], "stacked")
        self.assertTrue(snap["secondaryTree"] and snap["secondaryTree"]["tabId"] == before["secondaryTabId"])
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 1)
        restored = page.evaluate("() => window.prksWorkspaceSetMode('tiled')")
        self.assertTrue(restored)
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'tiled'")
        page.wait_for_function("() => window.prksTabContextDebugSnapshot().mountedCount === 2")
        page.evaluate("() => { window.prksHasPendingWorkAnnotationSync = function () { return false; }; }")
        page.evaluate(
            """(pid) => window.prksNavigate('#/people/' + pid, { target: 'tile' })""",
            arg=person_id,
        )
        page.wait_for_selector(".prks-tile--secondary .person-profile")
        self.assertEqual(page.evaluate("() => location.hash"), before["hash"])

    def test_close_secondary_and_hide_keep_main_url(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.evaluate(
            """(id) => window.prksNavigate('#/works/' + id, { target: 'tile' })""",
            arg=work_b,
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'tiled'")
        work_hash = page.evaluate("() => location.hash")
        page.locator(".prks-tile--secondary .prks-tile-header__hide").click()
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'stacked'")
        snap = page.evaluate("() => window.prksWorkspaceSnapshot()")
        self.assertTrue(snap["secondaryTree"])
        self.assertEqual(page.evaluate("() => location.hash"), work_hash)
        page.evaluate("() => window.prksWorkspaceSetMode('tiled')")
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'tiled'")
        sec = page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree.tabId")
        page.evaluate("(id) => window.prksWorkspaceCloseTab(id)", arg=sec)
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'stacked'")
        self.assertIsNone(page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree"))
        self.assertEqual(page.evaluate("() => location.hash"), work_hash)
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 1)

    def test_unsupported_secondary_nav_respects_leave(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.evaluate(
            """(id) => window.prksNavigate('#/works/' + id, { target: 'tile' })""",
            arg=work_b,
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'tiled'")
        ids = _workspace_ids(page)
        _wait_pdf_tab(page, ids["secondaryTabId"])
        dialogs = []

        def on_dialog(dialog):
            dialogs.append(dialog.message)
            dialog.dismiss()

        page.on("dialog", on_dialog)
        page.evaluate(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                const ctx = window.prksGetTabContext(snap.secondaryTree.tabId);
                window.prksHasPendingWorkAnnotationSync = function (c) {
                    return !!(c && ctx && c.tabId === ctx.tabId);
                };
            }"""
        )
        before = _workspace_ids(page)
        denied = page.evaluate(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                return window.prksNavigate('#/folders', { target: 'current', tabId: snap.secondaryTree.tabId });
            }"""
        )
        self.assertFalse(denied)
        after = _workspace_ids(page)
        self.assertEqual(after["mainTabId"], before["mainTabId"])
        self.assertEqual(after["secondaryTabId"], before["secondaryTabId"])
        self.assertEqual(after["focusedTabId"], before["focusedTabId"])
        self.assertEqual(after["mode"], "tiled")
        self.assertEqual(after["hash"], before["hash"])
        self.assertEqual(after["mountedCount"], 2)
        self.assertEqual(len(dialogs), 1)

    def test_browser_back_forward_after_make_main(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_a = server.ids["work_a"]
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        page.evaluate(
            """(id) => window.prksNavigate('#/works/' + id, { target: 'tile' })""",
            arg=work_b,
        )
        page.wait_for_function("() => document.querySelectorAll('.work-detail').length === 2")
        ids = _workspace_ids(page)
        _wait_pdf_tab(page, ids["mainTabId"])
        _wait_pdf_tab(page, ids["secondaryTabId"])
        page.evaluate(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                const a = window.prksGetTabContext(snap.mainTabId);
                const b = window.prksGetTabContext(snap.secondaryTree.tabId);
                window.__prksHistRt = {
                    aId: snap.mainTabId,
                    bId: snap.secondaryTree.tabId,
                    aPdf: a.getResource('pdf'),
                    bPdf: b.getResource('pdf'),
                };
                history.pushState(history.state, '', location.href);
                window.prksWorkspaceMakeMain(snap.secondaryTree.tabId);
            }"""
        )
        page.wait_for_function(
            """(b) => location.hash.indexOf(b) !== -1 && window.prksWorkspaceSnapshot().mainTabId === window.__prksHistRt.bId""",
            arg=work_b,
        )
        page.go_back()
        page.wait_for_function(
            """(a) => {
                const snap = window.prksWorkspaceSnapshot();
                return snap.mainTabId === window.__prksHistRt.aId && location.hash.indexOf(a) !== -1;
            }""",
            arg=work_a,
        )
        back = page.evaluate(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                const rt = window.__prksHistRt;
                const a = window.prksGetTabContext(rt.aId);
                const b = window.prksGetTabContext(rt.bId);
                return {
                    main: snap.mainTabId,
                    secondary: snap.secondaryTree && snap.secondaryTree.tabId,
                    mounted: window.prksTabContextDebugSnapshot().mountedCount,
                    aPdf: a.getResource('pdf') === rt.aPdf,
                    bPdf: b.getResource('pdf') === rt.bPdf,
                    hash: location.hash,
                };
            }"""
        )
        self.assertEqual(back["main"], page.evaluate("() => window.__prksHistRt.aId"))
        self.assertEqual(back["secondary"], page.evaluate("() => window.__prksHistRt.bId"))
        self.assertEqual(back["mounted"], 2)
        self.assertTrue(back["aPdf"])
        self.assertTrue(back["bPdf"])
        self.assertIn(work_a, back["hash"])
        page.go_forward()
        page.wait_for_function(
            """(b) => {
                const snap = window.prksWorkspaceSnapshot();
                return snap.mainTabId === window.__prksHistRt.bId && location.hash.indexOf(b) !== -1;
            }""",
            arg=work_b,
        )
        fwd = page.evaluate(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                const rt = window.__prksHistRt;
                const a = window.prksGetTabContext(rt.aId);
                const b = window.prksGetTabContext(rt.bId);
                return {
                    main: snap.mainTabId,
                    secondary: snap.secondaryTree && snap.secondaryTree.tabId,
                    mounted: window.prksTabContextDebugSnapshot().mountedCount,
                    aPdf: a.getResource('pdf') === rt.aPdf,
                    bPdf: b.getResource('pdf') === rt.bPdf,
                };
            }"""
        )
        self.assertEqual(fwd["main"], page.evaluate("() => window.__prksHistRt.bId"))
        self.assertEqual(fwd["secondary"], page.evaluate("() => window.__prksHistRt.aId"))
        self.assertEqual(fwd["mounted"], 2)
        self.assertTrue(fwd["aPdf"])
        self.assertTrue(fwd["bPdf"])

    def test_close_main_promotes_secondary_title(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.evaluate(
            """(id) => window.prksNavigate('#/works/' + id, { target: 'tile' })""",
            arg=work_b,
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'tiled'")
        page.wait_for_function(
            """(title) => {
                const snap = window.prksWorkspaceSnapshot();
                const tab = snap.tabs.find(function (t) { return t.id === snap.secondaryTree.tabId; });
                return !!(tab && tab.title === title);
            }""",
            arg=WORK_B_TITLE,
        )
        page.evaluate(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                return window.prksWorkspaceCloseTab(snap.mainTabId);
            }"""
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'stacked'")
        self.assertIn(WORK_B_TITLE, page.evaluate("() => document.title"))
        self.assertNotEqual(page.evaluate("() => document.title"), "Work — PRKS")
        self.assertIn(work_b, page.evaluate("() => location.hash"))
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 1)

