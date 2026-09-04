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

    def test_research_notes_debounce_save_does_not_patch_on_tab_switch(self):
        server, page, _collector = self._start_app()
        person_id = server.ids["person"]
        work_a = server.ids["work_a"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.wait_for_selector(".CodeMirror")
        page.evaluate(
            """(pid) => window.prksNavigate('#/people/' + pid, { target: 'new-tab', activate: false })""",
            arg=person_id,
        )
        page.wait_for_function("() => document.querySelectorAll('.prks-workspace-tab').length === 2")
        patches = []

        def on_request(req):
            path = urlparse(req.url).path
            if req.method == "PATCH" and path == "/api/works/" + work_a:
                patches.append(path)

        page.on("request", on_request)
        unique = "DEBOUNCE-SAVE-NOTE-%s" % int(time.time() * 1000)
        page.locator(".CodeMirror").click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(unique)
        page.locator('[data-prks-role="editor-status"]', has_text="Drafting").wait_for()
        page.wait_for_function(
            """() => {
                const el = document.querySelector('[data-prks-role="editor-status"]');
                return !!(el && /All changes saved/i.test(el.innerText || ''));
            }""",
            timeout=15000,
        )
        after_save = len(patches)
        self.assertGreaterEqual(after_save, 1)
        page.locator(".prks-workspace-tab").nth(1).locator(".prks-workspace-tab__activate").click()
        page.wait_for_function("() => location.hash.indexOf('#/people/') === 0")
        self.assertEqual(len(patches), after_save)

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


def _open_work_work_split(page, server):
    """Work A Main, Work B Secondary, both PDFs ready. Returns (work_a, work_b, ids)."""
    work_a = server.ids["work_a"]
    work_b = server.ids["work_b"]
    _open_work_from_home(page, WORK_A_TITLE)
    _wait_pdf_viewer(page)
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
    _wait_pdf_tab(page, ids["mainTabId"])
    _wait_pdf_tab(page, ids["secondaryTabId"])
    page.wait_for_selector(".prks-splitter")
    page.wait_for_function("() => document.querySelectorAll('.CodeMirror').length === 2")
    return work_a, work_b, ids


def _build_three_leaf_tree(page, server):
    """Main = Work A. Secondary tree: B (person) on top; bottom split into C (position) | D
    (Work B). Matches the recursive-split spec example (split B down with C, then split C
    right with D). D is a real Work/PDF leaf deep in the tree so Work-runtime-preservation and
    leave-guard behavior can be exercised on more than a top-level leaf."""
    work_a = server.ids["work_a"]
    work_b = server.ids["work_b"]
    person_id = server.ids["person"]
    position_id = server.ids["position"]
    _open_work_from_home(page, WORK_A_TITLE)
    _wait_pdf_viewer(page)
    page.evaluate(
        """(id) => window.prksNavigate('#/people/' + id, { target: 'tile' })""",
        arg=person_id,
    )
    page.wait_for_function(
        """() => {
            const snap = window.prksWorkspaceSnapshot();
            return snap && snap.mode === 'tiled' && snap.secondaryTree && snap.secondaryTree.type === 'leaf';
        }"""
    )
    main_id = page.evaluate("() => window.prksWorkspaceSnapshot().mainTabId")
    b_id = page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree.tabId")
    c_tab = page.evaluate(
        """(a) => window.prksWorkspaceSplitLeaf(a.target, 'top-bottom', { hash: '#/positions/' + a.position })""",
        arg={"target": b_id, "position": position_id},
    )
    c_id = c_tab["id"]
    page.wait_for_function("() => window.prksTabContextDebugSnapshot().mountedCount === 3")
    d_tab = page.evaluate(
        """(a) => window.prksWorkspaceSplitLeaf(a.target, 'left-right', { hash: '#/works/' + a.work })""",
        arg={"target": c_id, "work": work_b},
    )
    d_id = d_tab["id"]
    page.wait_for_function("() => window.prksTabContextDebugSnapshot().mountedCount === 4")
    _wait_pdf_tab(page, main_id)
    _wait_pdf_tab(page, d_id)
    page.wait_for_function(
        """(ids) => {
            const a = window.prksGetTabContext(ids.main);
            const d = window.prksGetTabContext(ids.d);
            return !!(a && a.getResource('workNotes') && d && d.getResource('workNotes'));
        }""",
        arg={"main": main_id, "d": d_id},
    )
    return {
        "work_a": work_a,
        "work_b": work_b,
        "person": person_id,
        "position": position_id,
        "main_id": main_id,
        "b_id": b_id,
        "c_id": c_id,
        "d_id": d_id,
    }


def _capture_divider_runtime_ids(page):
    page.evaluate(
        """() => {
            const snap = window.prksWorkspaceSnapshot();
            const a = window.prksGetTabContext(snap.mainTabId);
            const b = window.prksGetTabContext(snap.secondaryTree.tabId);
            window.__prksDividerRt = {
                aPdf: a.getResource('pdf'),
                bPdf: b.getResource('pdf'),
                aNotes: a.getResource('workNotes'),
                bNotes: b.getResource('workNotes'),
            };
        }"""
    )


def _divider_runtime_ids_unchanged(page):
    return page.evaluate(
        """() => {
            const snap = window.prksWorkspaceSnapshot();
            const a = window.prksGetTabContext(snap.mainTabId);
            const b = window.prksGetTabContext(snap.secondaryTree.tabId);
            const rt = window.__prksDividerRt;
            return {
                aPdf: a.getResource('pdf') === rt.aPdf,
                bPdf: b.getResource('pdf') === rt.bPdf,
                aNotes: a.getResource('workNotes') === rt.aNotes,
                bNotes: b.getResource('workNotes') === rt.bNotes,
                mounted: window.prksTabContextDebugSnapshot().mountedCount,
            };
        }"""
    )


def _drag_divider(page, dx):
    sep = page.locator(".prks-splitter")
    box = sep.bounding_box()
    start_x = box["x"] + box["width"] / 2
    start_y = box["y"] + box["height"] / 2
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(start_x + dx, start_y, steps=10)
    page.mouse.up()


# ---- workspace-drag.js E2E helpers: real Playwright pointer gestures, never a direct call into
# prksWorkspaceReorderTab/prksWorkspaceMovePane/etc. -- those are exercised by the drag controller
# itself, exactly the way a user would trigger them. ----


def _tab_box(page, tab_id):
    return page.locator('.prks-workspace-tab[data-tab-id="%s"]' % tab_id).bounding_box()


def _tile_box(page, tab_id):
    return page.locator('.prks-tile[data-prks-tab-id="%s"]' % tab_id).bounding_box()


def _grip_box(page, tab_id):
    return page.locator('.prks-tile[data-prks-tab-id="%s"] .prks-tile-header__grip' % tab_id).bounding_box()


def _center(box):
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def _tab_grab_point(box):
    """A point safely inside a workspace tab wrap, biased toward its icon/title on the leading
    edge -- away from the fixed-position Split-view toggle at the tab strip's trailing edge,
    which can visually overlap the last tab's own bounding box when the strip is full-width."""
    return box["x"] + min(24, box["width"] / 3), box["y"] + box["height"] / 2


def _edge_point(box, zone):
    """A point well inside the 0.28 default edge band for `zone`, and centered on the cross
    axis so that edge is unambiguously nearest."""
    cx = box["x"] + box["width"] / 2
    cy = box["y"] + box["height"] / 2
    if zone == "left":
        return box["x"] + box["width"] * 0.08, cy
    if zone == "right":
        return box["x"] + box["width"] * 0.92, cy
    if zone == "above":
        return cx, box["y"] + box["height"] * 0.08
    if zone == "below":
        return cx, box["y"] + box["height"] * 0.92
    raise ValueError(zone)


def _begin_pointer_drag(page, start_xy):
    """pointerdown + enough movement to cross workspace-drag.js's 6px threshold and arm an
    active drag. Caller continues with further page.mouse.move()/page.mouse.up() calls."""
    sx, sy = start_xy
    page.mouse.move(sx, sy)
    page.mouse.down()
    page.mouse.move(sx + 12, sy + 12, steps=3)


def _pointer_drag(page, start_xy, hover_xy, release_xy=None, pre_release=None):
    """A full drag gesture: down at `start_xy`, cross the movement threshold, move to
    `hover_xy` (where a caller-supplied `pre_release` callback can inspect live drag state),
    then move to `release_xy` (defaults to `hover_xy`) and release there."""
    _begin_pointer_drag(page, start_xy)
    hx, hy = hover_xy
    page.mouse.move(hx, hy, steps=10)
    if pre_release is not None:
        pre_release()
    rx, ry = release_xy if release_xy is not None else (hx, hy)
    if (rx, ry) != (hx, hy):
        page.mouse.move(rx, ry, steps=6)
    page.mouse.up()


def _drag_dom_residue(page):
    return page.evaluate(
        """() => ({
            preview: document.querySelectorAll('.prks-drag-preview').length,
            marker: document.querySelectorAll('#prks-drag-insertion-marker').length,
            edge: document.querySelectorAll('#prks-drag-edge-overlay').length,
            empty: document.querySelectorAll('#prks-drag-empty-overlay').length,
            dragSource: document.querySelectorAll('.is-drag-source').length,
            bodyDragging: document.body.classList.contains('prks-workspace-dragging'),
            parkTarget: document.querySelectorAll('#prks-workspace-tabs.is-drop-target-park').length,
        })"""
    )


def _assert_no_drag_residue(test, page, msg=""):
    residue = _drag_dom_residue(page)
    test.assertEqual(
        residue,
        {
            "preview": 0,
            "marker": 0,
            "edge": 0,
            "empty": 0,
            "dragSource": 0,
            "bodyDragging": False,
            "parkTarget": 0,
        },
        msg,
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

    def test_stack_hide_split_leave_guard_blocks_and_confirms(self):
        # Global "Hide split" (stacked mode) unmounts every visible Secondary leaf, so it is
        # still leave-guarded. Generic split placement is not: it never evicts an existing
        # leaf (see test_open_in_split_view_adds_beside_existing_leaf), so there is nothing to
        # guard there any more.
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_b = server.ids["work_b"]
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

        stack_denied = page.evaluate("() => window.prksWorkspaceSetMode('stacked')")
        self.assertFalse(stack_denied)
        self.assertEqual(page.evaluate("() => window.prksWorkspaceSnapshot().mode"), "tiled")
        self.assertEqual(len(dialogs), 1)

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

    def test_open_in_split_view_adds_beside_existing_leaf(self):
        """Spec workflow regression: A Main, B Secondary, C parked. Invoking C's tab-strip
        "Open in split view" action must split B with C -- B stays first/mounted, C becomes
        the second leaf/focused -- never evict B. Since B is never unmounted, no leave guard
        fires even though B has a pending (simulated) unsaved change."""
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
        before = _workspace_ids(page)
        main_id = before["mainTabId"]
        b_id = before["secondaryTabId"]
        _wait_pdf_tab(page, b_id)

        page.evaluate(
            """(pid) => window.prksNavigate('#/people/' + pid, { target: 'new-tab', activate: false })""",
            arg=person_id,
        )
        page.wait_for_function("() => document.querySelectorAll('.prks-workspace-tab').length === 3")
        c_id = page.evaluate(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                return snap.tabs.find(function (t) {
                    return t.id !== snap.mainTabId && t.id !== snap.secondaryTree.tabId;
                }).id;
            }"""
        )
        tabs_before_split = page.evaluate("() => window.prksWorkspaceSnapshot().tabs.length")

        dialogs = []
        page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        page.evaluate(
            """(bId) => {
                const ctx = window.prksGetTabContext(bId);
                window.prksHasPendingWorkAnnotationSync = function (c) {
                    return !!(c && ctx && c.tabId === ctx.tabId);
                };
                window.__prksOpenSplitB = ctx.getResource('pdf');
            }""",
            arg=b_id,
        )

        page.locator('.prks-workspace-tab[data-tab-id="' + c_id + '"] .prks-workspace-tab__split').click()
        page.wait_for_function(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                return snap.secondaryTree && snap.secondaryTree.type === 'split';
            }"""
        )

        self.assertEqual(dialogs, [])
        tree = page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree")
        self.assertEqual(tree["axis"], "left-right")
        self.assertEqual(tree["first"]["tabId"], b_id)
        self.assertEqual(tree["second"]["tabId"], c_id)
        snap = page.evaluate("() => window.prksWorkspaceSnapshot()")
        self.assertEqual(snap["mainTabId"], main_id)
        self.assertEqual(snap["focusedTabId"], c_id)
        self.assertEqual(len(snap["tabs"]), tabs_before_split)  # C reused, never duplicated
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 3)
        self.assertEqual(page.locator(".prks-tile").count(), 3)
        self.assertEqual(page.evaluate("() => location.hash"), before["hash"])
        self.assertTrue(
            page.evaluate(
                "(id) => window.prksGetTabContext(id).getResource('pdf') === window.__prksOpenSplitB",
                arg=b_id,
            )
        )

    def test_navigate_tile_target_splits_single_secondary_leaf(self):
        """Alt-click / generic {target:'tile'} navigation shares tileTab()'s default-placement
        rule: with exactly one existing Secondary leaf, it splits that leaf with the new tab
        instead of replacing it."""
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
        b_id = page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree.tabId")
        main_id = page.evaluate("() => window.prksWorkspaceSnapshot().mainTabId")
        tabs_before = page.evaluate("() => window.prksWorkspaceSnapshot().tabs.length")
        work_hash = page.evaluate("() => location.hash")

        page.evaluate(
            """(pid) => window.prksNavigate('#/people/' + pid, { target: 'tile' })""",
            arg=person_id,
        )
        page.wait_for_function(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                return snap.secondaryTree && snap.secondaryTree.type === 'split';
            }"""
        )
        tree = page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree")
        self.assertEqual(tree["first"]["tabId"], b_id)
        self.assertNotEqual(tree["second"]["tabId"], b_id)
        snap = page.evaluate("() => window.prksWorkspaceSnapshot()")
        self.assertEqual(snap["mainTabId"], main_id)
        self.assertEqual(snap["focusedTabId"], tree["second"]["tabId"])
        self.assertEqual(len(snap["tabs"]), tabs_before + 1)
        self.assertEqual(page.evaluate("() => location.hash"), work_hash)
        self.assertEqual(page.locator(".prks-tile").count(), 3)

    def test_close_secondary_tile_header_keeps_main_url(self):
        # The per-tile header "x" closes that Secondary leaf's tab outright (distinct from the
        # global Split toolbar button's Hide/Show split, which is covered elsewhere).
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
        sec_before = page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree.tabId")
        page.locator(".prks-tile--secondary .prks-tile-header__close").click()
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'stacked'")
        snap = page.evaluate("() => window.prksWorkspaceSnapshot()")
        self.assertIsNone(snap["secondaryTree"])
        self.assertFalse(any(t["id"] == sec_before for t in snap["tabs"]))
        self.assertEqual(page.evaluate("() => location.hash"), work_hash)
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 1)

    def test_hide_leaf_parks_tab_without_closing_it(self):
        # Local "Hide from split" (per-pane park) keeps the logical tab open, distinct from
        # Close (which destroys it) and from the global Hide split (which parks every pane but
        # preserves the whole tree). Reopening reuses the same tab id.
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
        sec = page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree.tabId")
        page.evaluate("(id) => window.prksWorkspaceHideLeaf(id)", arg=sec)
        page.wait_for_function("() => window.prksWorkspaceSnapshot().secondaryTree === null")
        snap = page.evaluate("() => window.prksWorkspaceSnapshot()")
        self.assertTrue(any(t["id"] == sec for t in snap["tabs"]))
        self.assertEqual(snap["mode"], "stacked")
        self.assertEqual(page.evaluate("() => location.hash"), work_hash)
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 1)

        reopened = page.evaluate("(id) => window.prksWorkspaceTileTab(id)", arg=sec)
        self.assertIsNotNone(reopened)
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'tiled'")
        after = page.evaluate("() => window.prksWorkspaceSnapshot()")
        self.assertEqual(after["secondaryTree"]["tabId"], sec)
        self.assertEqual(len(after["tabs"]), len(snap["tabs"]))

    def test_recursive_multi_pane_tree_focus_and_request_count(self):
        server, page, _collector = self._start_app(seed_fn=seed_graph_context_library)
        page.set_viewport_size({"width": 1600, "height": 900})
        tree = _build_three_leaf_tree(page, server)
        work_hash = page.evaluate("() => location.hash")

        snap = page.evaluate("() => window.prksWorkspaceSnapshot()")
        self.assertEqual(snap["mode"], "tiled")
        self.assertEqual(snap["mainTabId"], tree["main_id"])
        self.assertEqual(snap["focusedTabId"], tree["d_id"])  # newly split D becomes focused
        self.assertIn(server.ids["work_a"], work_hash)
        leaves = page.evaluate(
            "() => window.collectLeafTabIds(window.prksWorkspaceSnapshot().secondaryTree)"
        )
        self.assertEqual(sorted(leaves), sorted([tree["b_id"], tree["c_id"], tree["d_id"]]))
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 4)
        self.assertEqual(page.locator(".prks-tile").count(), 4)

        seen = []

        def on_request(req):
            if req.method == "GET":
                seen.append(urlparse(req.url).path)

        page.on("request", on_request)

        # Focus B then C; right panel/focus follows, Main/URL do not move.
        page.evaluate("(id) => window.prksWorkspaceFocusTab(id)", arg=tree["b_id"])
        self.assertEqual(page.evaluate("() => window.prksWorkspaceSnapshot().focusedTabId"), tree["b_id"])
        page.evaluate("(id) => window.prksWorkspaceFocusTab(id)", arg=tree["c_id"])
        self.assertEqual(page.evaluate("() => window.prksWorkspaceSnapshot().focusedTabId"), tree["c_id"])
        self.assertEqual(page.evaluate("() => location.hash"), work_hash)

        # Resize the nested C|D separator; Main/Secondary root ratio is unaffected.
        inner_split_id = page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree.second.id")
        inner_sep = page.locator('[data-prks-split-id="' + inner_split_id + '"][role="separator"]')
        box = inner_sep.bounding_box()
        start_x = box["x"] + box["width"] / 2
        start_y = box["y"] + box["height"] / 2
        page.mouse.move(start_x, start_y)
        page.mouse.down()
        page.mouse.move(start_x + 40, start_y, steps=6)
        page.mouse.up()

        # Make D Main, then close its former sibling C. Deep tree mutations must not reload
        # any unaffected Work.
        page.evaluate("(id) => window.prksWorkspaceMakeMain(id)", arg=tree["d_id"])
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mainTabId !== undefined")
        self.assertEqual(page.evaluate("() => window.prksWorkspaceSnapshot().mainTabId"), tree["d_id"])
        self.assertIn(server.ids["work_b"], page.evaluate("() => location.hash"))
        page.evaluate("(id) => window.prksWorkspaceCloseTab(id)", arg=tree["c_id"])
        page.wait_for_function("() => window.prksTabContextDebugSnapshot().mountedCount === 3")

        for path in ("/api/works/" + server.ids["work_a"], "/api/works/" + server.ids["work_b"]):
            self.assertNotIn(path, seen, "unaffected leaf reloaded during focus/resize/make-main/close: " + path)

    def test_make_main_deep_leaf_preserves_runtimes(self):
        server, page, _collector = self._start_app(seed_fn=seed_graph_context_library)
        page.set_viewport_size({"width": 1600, "height": 900})
        tree = _build_three_leaf_tree(page, server)

        page.evaluate(
            """(ids) => {
                window.__prksMultiRt = {
                    aPdf: window.prksGetTabContext(ids.main).getResource('pdf'),
                    aNotes: window.prksGetTabContext(ids.main).getResource('workNotes'),
                    dPdf: window.prksGetTabContext(ids.d).getResource('pdf'),
                    dNotes: window.prksGetTabContext(ids.d).getResource('workNotes'),
                };
            }""",
            arg={"main": tree["main_id"], "d": tree["d_id"]},
        )

        made_main = page.evaluate("(id) => window.prksWorkspaceMakeMain(id)", arg=tree["d_id"])
        self.assertTrue(made_main)
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mainTabId !== undefined")
        snap = page.evaluate("() => window.prksWorkspaceSnapshot()")
        self.assertEqual(snap["mainTabId"], tree["d_id"])
        self.assertEqual(snap["focusedTabId"], tree["d_id"])
        self.assertIn(server.ids["work_b"], page.evaluate("() => location.hash"))

        # Old Main (A) now occupies D's exact former leaf position; tree shape (B on top,
        # bottom split of C|A) is otherwise unchanged -- no rebuild, no reorder.
        shape_ok = page.evaluate(
            """(ids) => {
                const tree = window.prksWorkspaceSnapshot().secondaryTree;
                if (!tree || tree.type !== 'split' || tree.axis !== 'top-bottom') return false;
                if (!tree.first || tree.first.type !== 'leaf' || tree.first.tabId !== ids.b) return false;
                const inner = tree.second;
                if (!inner || inner.type !== 'split' || inner.axis !== 'left-right') return false;
                if (!inner.first || inner.first.tabId !== ids.c) return false;
                if (!inner.second || inner.second.tabId !== ids.oldMain) return false;
                return true;
            }""",
            arg={"b": tree["b_id"], "c": tree["c_id"], "oldMain": tree["main_id"]},
        )
        self.assertTrue(shape_ok, "expected an in-place role swap, not a rebuilt tree")

        identity = page.evaluate(
            """(ids) => {
                const rt = window.__prksMultiRt;
                const a = window.prksGetTabContext(ids.main);
                const d = window.prksGetTabContext(ids.d);
                return {
                    aPdfSame: a.getResource('pdf') === rt.aPdf,
                    aNotesSame: a.getResource('workNotes') === rt.aNotes,
                    dPdfSame: d.getResource('pdf') === rt.dPdf,
                    dNotesSame: d.getResource('workNotes') === rt.dNotes,
                    mounted: window.prksTabContextDebugSnapshot().mountedCount,
                };
            }""",
            arg={"main": tree["main_id"], "d": tree["d_id"]},
        )
        self.assertTrue(identity["aPdfSame"])
        self.assertTrue(identity["aNotesSame"])
        self.assertTrue(identity["dPdfSame"])
        self.assertTrue(identity["dNotesSame"])
        self.assertEqual(identity["mounted"], 4)

    def test_close_leaf_collapses_tree_without_remount(self):
        server, page, _collector = self._start_app(seed_fn=seed_graph_context_library)
        page.set_viewport_size({"width": 1600, "height": 900})
        tree = _build_three_leaf_tree(page, server)

        page.evaluate(
            """(ids) => {
                window.__prksCollapseRt = {
                    bCtx: window.prksGetTabContext(ids.b),
                    dCtx: window.prksGetTabContext(ids.d),
                };
            }""",
            arg={"b": tree["b_id"], "d": tree["d_id"]},
        )
        closed = page.evaluate("(id) => window.prksWorkspaceCloseTab(id)", arg=tree["c_id"])
        self.assertTrue(closed)
        page.wait_for_function("() => window.prksTabContextDebugSnapshot().mountedCount === 3")

        shape_ok = page.evaluate(
            """(ids) => {
                const tree = window.prksWorkspaceSnapshot().secondaryTree;
                return !!(
                    tree && tree.type === 'split' &&
                    tree.first && tree.first.type === 'leaf' && tree.first.tabId === ids.b &&
                    tree.second && tree.second.type === 'leaf' && tree.second.tabId === ids.d
                );
            }""",
            arg={"b": tree["b_id"], "d": tree["d_id"]},
        )
        self.assertTrue(shape_ok, "expected a collapsed 2-leaf tree with no redundant split node")

        identity = page.evaluate(
            """(ids) => {
                const rt = window.__prksCollapseRt;
                return {
                    bSame: window.prksGetTabContext(ids.b) === rt.bCtx,
                    dSame: window.prksGetTabContext(ids.d) === rt.dCtx,
                };
            }""",
            arg={"b": tree["b_id"], "d": tree["d_id"]},
        )
        self.assertTrue(identity["bSame"])
        self.assertTrue(identity["dSame"])
        self.assertEqual(page.locator(".prks-tile").count(), 3)
        # Workspace dividers only (excludes each Work pane's own internal PDF/notes resizer).
        self.assertEqual(page.locator(".prks-splitter").count(), 2)

    def test_visible_secondary_tab_click_focuses_not_promotes(self):
        server, page, _collector = self._start_app(seed_fn=seed_graph_context_library)
        page.set_viewport_size({"width": 1600, "height": 900})
        tree = _build_three_leaf_tree(page, server)
        work_hash = page.evaluate("() => location.hash")

        page.locator('.prks-workspace-tab[data-tab-id="' + tree["c_id"] + '"] .prks-workspace-tab__activate').click()
        snap = page.evaluate("() => window.prksWorkspaceSnapshot()")
        self.assertEqual(snap["focusedTabId"], tree["c_id"])
        self.assertEqual(snap["mainTabId"], tree["main_id"])
        self.assertEqual(page.evaluate("() => location.hash"), work_hash)
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 4)

        # Clicking Main's own tab entry just focuses Main back; no remount, no history entry.
        page.locator('.prks-workspace-tab[data-tab-id="' + tree["main_id"] + '"] .prks-workspace-tab__activate').click()
        self.assertEqual(page.evaluate("() => window.prksWorkspaceSnapshot().focusedTabId"), tree["main_id"])
        self.assertEqual(page.evaluate("() => location.hash"), work_hash)
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 4)

    def test_nested_divider_pointer_and_keyboard_resize(self):
        server, page, _collector = self._start_app(seed_fn=seed_graph_context_library)
        # Wide enough that the doubly-nested C|D pane clears its own left-right minimum-width
        # bounds (280px per side) even after the shell sidebar and the root 58/42 Main/Secondary
        # split; otherwise its ratio legitimately (and correctly) clamps to a safe 50/50 midpoint.
        page.set_viewport_size({"width": 2200, "height": 900})
        tree = _build_three_leaf_tree(page, server)

        root_ratio_before = page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio")
        top_split_id = page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree.id")
        inner_split_id = page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree.second.id")

        top_sep = page.locator('[data-prks-split-id="' + top_split_id + '"][role="separator"]')
        self.assertEqual(top_sep.get_attribute("aria-orientation"), "horizontal")
        inner_sep = page.locator('[data-prks-split-id="' + inner_split_id + '"][role="separator"]')
        self.assertEqual(inner_sep.get_attribute("aria-orientation"), "vertical")

        def ratio_of(split_id):
            return page.evaluate(
                "(id) => window.findNodeById(window.prksWorkspaceSnapshot().secondaryTree, id).ratio",
                arg=split_id,
            )

        # Pointer-drag the nested vertical (C|D) separator; the sibling B and the root
        # Main/Secondary ratio are unaffected.
        box = inner_sep.bounding_box()
        start_x = box["x"] + box["width"] / 2
        start_y = box["y"] + box["height"] / 2
        page.mouse.move(start_x, start_y)
        page.mouse.down()
        page.mouse.move(start_x + 60, start_y, steps=8)
        page.mouse.up()
        self.assertNotEqual(ratio_of(inner_split_id), 0.5)
        self.assertEqual(ratio_of(top_split_id), 0.5)
        self.assertEqual(page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio"), root_ratio_before)

        # Keyboard resize on the nested horizontal (B / C+D) separator: arrows, shift+arrows,
        # Home/End, and ARIA values track the ratio.
        top_sep.focus()
        page.keyboard.press("ArrowDown")
        after_down = ratio_of(top_split_id)
        self.assertGreater(after_down, 0.5)
        self.assertEqual(top_sep.get_attribute("aria-valuenow"), str(round(after_down * 100)))
        page.keyboard.press("ArrowUp")
        page.keyboard.press("Shift+ArrowDown")
        after_shift = ratio_of(top_split_id)
        self.assertGreater(after_shift, 0.5)
        page.keyboard.press("Home")
        min_ratio = ratio_of(top_split_id)
        self.assertLess(min_ratio, 0.5)
        page.keyboard.press("End")
        max_ratio = ratio_of(top_split_id)
        self.assertGreater(max_ratio, 0.5)

        # Double-click resets that split back to 0.5; the other nested split is untouched.
        top_sep.dblclick()
        self.assertAlmostEqual(ratio_of(top_split_id), 0.5, places=2)
        self.assertNotEqual(ratio_of(inner_split_id), 0.5)

        # Resizing never fetches a route/PDF.
        seen = []
        page.on("request", lambda req: seen.append(req.url))
        page.mouse.move(start_x, start_y)
        page.mouse.down()
        page.mouse.move(start_x - 30, start_y, steps=5)
        page.mouse.up()
        self.assertEqual(seen, [])

    def test_narrow_fallback_preserves_recursive_tree_and_atomic_rejection(self):
        server, page, _collector = self._start_app(seed_fn=seed_graph_context_library)
        page.set_viewport_size({"width": 1600, "height": 900})
        tree = _build_three_leaf_tree(page, server)
        tree_before = page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree")

        dialogs = []

        def on_dialog(dialog):
            dialogs.append(dialog.message)
            dialog.dismiss()

        page.on("dialog", on_dialog)

        # D (a real Work/PDF leaf, deep in the tree) has a pending PDF annotation sync.
        page.evaluate(
            """(id) => {
                const target = window.prksGetTabContext(id);
                window.prksHasPendingWorkAnnotationSync = function (c) {
                    return !!(c && target && c.tabId === target.tabId);
                };
            }""",
            arg=tree["d_id"],
        )
        rejected = page.evaluate("() => window.prksWorkspaceSetNarrowFallback(true)")
        self.assertFalse(rejected)
        self.assertEqual(len(dialogs), 1)
        self.assertTrue(page.evaluate("() => window.prksWorkspaceVisualTiled()"))
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 4)
        self.assertEqual(page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree"), tree_before)

        # Ordinary paints (a sibling focus change) must not re-trigger the rejected prompt.
        page.evaluate("(id) => window.prksWorkspaceFocusTab(id)", arg=tree["b_id"])
        self.assertEqual(len(dialogs), 1)

        # Clear the guard and retry: succeeds, every leaf unmounts, the tree survives logically.
        page.evaluate("() => { window.prksHasPendingWorkAnnotationSync = function () { return false; }; }")
        ok = page.evaluate("() => window.prksWorkspaceSetNarrowFallback(true)")
        self.assertTrue(ok)
        page.wait_for_function("() => window.prksWorkspaceVisualTiled() === false")
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 1)
        self.assertEqual(page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree"), tree_before)

        widened = page.evaluate("() => window.prksWorkspaceSetNarrowFallback(false)")
        self.assertTrue(widened)
        page.wait_for_function("() => window.prksWorkspaceVisualTiled() === true")
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 4)
        self.assertEqual(page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree"), tree_before)

    def test_visible_pane_cap_blocks_split(self):
        server, page, _collector = self._start_app(seed_fn=seed_graph_context_library)
        page.set_viewport_size({"width": 1600, "height": 900})
        tree = _build_three_leaf_tree(page, server)  # 1 Main + 3 Secondary already mounted
        self.assertFalse(page.evaluate("() => window.prksWorkspaceCanAddSecondaryLeaf()"))

        blocked = page.evaluate(
            """(a) => window.prksWorkspaceSplitLeaf(a.target, 'left-right', { hash: '#/works/' + a.workA })""",
            arg={"target": tree["b_id"], "workA": server.ids["work_a"]},
        )
        self.assertFalse(blocked)
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 4)

        split_btn = page.locator('.prks-tile[data-prks-tab-id="' + tree["b_id"] + '"] .prks-tile-header__split')
        self.assertTrue(split_btn.is_disabled())
        self.assertIn("Maximum of 4 visible panes", split_btn.get_attribute("title") or "")

        # Ordinary New Tab still works, and the new tab stays parked (not mounted).
        page.evaluate("() => window.prksNavigate('#/folders', { target: 'new-tab', activate: false })")
        page.wait_for_function("() => document.querySelectorAll('.prks-workspace-tab').length === 5")
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 4)

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

    def test_parked_tab_split_action_reuses_tab(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_a = server.ids["work_a"]
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.evaluate(
            """(id) => window.prksNavigate('#/works/' + id, { target: 'new-tab', activate: false })""",
            arg=work_b,
        )
        page.wait_for_function("() => document.querySelectorAll('.prks-workspace-tab').length === 2")
        before = page.evaluate(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                const parked = snap.tabs.find(function (t) { return t.id !== snap.mainTabId; });
                return {
                    tabCount: snap.tabs.length,
                    parkedId: parked && parked.id,
                    parkedHistory: parked && parked.history.slice(),
                    hash: location.hash,
                };
            }"""
        )
        split = page.locator(".prks-workspace-tab.is-parked .prks-workspace-tab__split")
        self.assertEqual(split.count(), 1)
        self.assertEqual(page.locator(".prks-workspace-tab.is-parked .prks-workspace-tab__split button").count(), 0)
        self.assertIn("Open", split.get_attribute("aria-label") or "")
        split.focus()
        page.keyboard.press("Enter")
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'tiled'")
        page.wait_for_selector(".prks-tile--secondary .work-detail")
        after = _workspace_ids(page)
        self.assertEqual(after["mode"], "tiled")
        self.assertEqual(after["mountedCount"], 2)
        self.assertIn(work_a, after["hash"])
        self.assertEqual(after["secondaryTabId"], before["parkedId"])
        self.assertEqual(after["focusedTabId"], before["parkedId"])
        self.assertNotEqual(after["mainTabId"], after["secondaryTabId"])
        snap = page.evaluate("() => window.prksWorkspaceSnapshot()")
        self.assertEqual(len(snap["tabs"]), before["tabCount"])
        parked = [t for t in snap["tabs"] if t["id"] == before["parkedId"]][0]
        self.assertEqual(parked["history"], before["parkedHistory"])
        self.assertEqual(page.locator(".prks-tile--secondary .work-detail").count(), 1)

    def test_split_control_opens_palette_without_alt(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        work_hash = page.evaluate("() => location.hash")
        page.locator("#prks-workspace-tile-layout").click()
        page.wait_for_selector("#prks-command-palette:not([hidden])")
        self.assertIn("Open in split view", page.locator("#prks-command-palette-title").text_content())
        page.locator("#prks-command-palette-input").fill(WORK_B_TITLE)
        work_label = page.locator(
            ".prks-command-palette__option-label",
            has_text=re.compile("^" + re.escape(WORK_B_TITLE) + "$"),
        )
        work_label.wait_for()
        page.locator(".prks-command-palette__option").filter(has=work_label).click()
        page.wait_for_function("() => document.querySelectorAll('.work-detail').length === 2")
        ids = _workspace_ids(page)
        self.assertEqual(ids["mode"], "tiled")
        self.assertEqual(ids["hash"], work_hash)
        self.assertEqual(ids["mountedCount"], 2)
        self.assertEqual(len(page.context.pages), 1)
        self.assertIn(work_b, page.evaluate("() => window.prksWorkspaceSnapshot().tabs.map(t => t.route).join(' ')"))

    def test_split_palette_reuses_open_tab(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.evaluate(
            """(id) => window.prksNavigate('#/works/' + id, { target: 'new-tab', activate: false })""",
            arg=work_b,
        )
        page.wait_for_function("() => document.querySelectorAll('.prks-workspace-tab').length === 2")
        b_id = page.evaluate(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                return snap.tabs.find(function (t) { return t.id !== snap.mainTabId; }).id;
            }"""
        )
        page.locator("#prks-workspace-tile-layout").click()
        page.wait_for_selector("#prks-command-palette:not([hidden])")
        heading = page.locator(".prks-command-palette__heading", has_text="Open tabs")
        heading.wait_for()
        page.locator(".prks-command-palette__option").first.click()
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'tiled'")
        snap = page.evaluate("() => window.prksWorkspaceSnapshot()")
        self.assertEqual(len(snap["tabs"]), 2)
        self.assertEqual(snap["secondaryTree"]["tabId"], b_id)

    def test_folders_parked_tab_has_no_split_action(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        _open_work_from_home(page, WORK_A_TITLE)
        page.evaluate("() => window.prksNavigate('#/folders', { target: 'new-tab', activate: false })")
        page.wait_for_function("() => document.querySelectorAll('.prks-workspace-tab').length === 2")
        self.assertEqual(page.locator(".prks-workspace-tab.is-parked .prks-workspace-tab__split").count(), 0)
        page.locator("#prks-workspace-tile-layout").click()
        page.wait_for_selector("#prks-command-palette:not([hidden])")
        labels = page.locator(".prks-command-palette__option-label").all_text_contents()
        self.assertFalse(any(t.strip() == "Folders" for t in labels))

    def test_narrow_split_announces_instead_of_hiding(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.evaluate(
            """(id) => window.prksNavigate('#/works/' + id, { target: 'tile' })""",
            arg=work_b,
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'tiled'")
        page.evaluate("() => window.prksWorkspaceSetNarrowFallback(true)")
        page.wait_for_function("() => window.prksWorkspaceVisualTiled() === false")
        btn = page.locator("#prks-workspace-tile-layout")
        self.assertEqual(btn.get_attribute("aria-pressed"), "false")
        self.assertIn("unavailable", (btn.get_attribute("aria-label") or "").lower())
        btn.click()
        page.wait_for_function(
            """() => (document.getElementById('prks-workspace-live') || {}).textContent.indexOf('wider window') !== -1"""
        )
        self.assertFalse(page.evaluate("() => window.prksWorkspaceVisualTiled()"))
        self.assertEqual(page.evaluate("() => window.prksWorkspaceSnapshot().mode"), "tiled")

    def test_many_tabs_overflow_reveals_main(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1100, "height": 900})
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.evaluate(
            """(id) => {
                const jobs = [];
                for (let i = 0; i < 12; i++) {
                    jobs.push(window.prksNavigate('#/works/' + id, { target: 'new-tab', activate: false }));
                }
                return Promise.all(jobs);
            }""",
            arg=work_b,
        )
        page.wait_for_function("() => document.querySelectorAll('.prks-workspace-tab').length >= 12")
        overflow = page.evaluate(
            """() => {
                const list = document.getElementById('prks-workspace-tabs');
                return !!(list && list.scrollWidth > list.clientWidth + 2);
            }"""
        )
        self.assertTrue(overflow)
        last_id = page.evaluate("() => window.prksWorkspaceSnapshot().tabs.slice(-1)[0].id")
        page.evaluate("(id) => window.prksWorkspaceActivateTab(id)", arg=last_id)
        page.wait_for_function(
            "(id) => window.prksWorkspaceSnapshot().mainTabId === id",
            arg=last_id,
        )
        visible = page.evaluate(
            """(id) => {
                const list = document.getElementById('prks-workspace-tabs');
                const wrap = list && list.querySelector('.prks-workspace-tab[data-tab-id="' + id + '"]');
                if (!list || !wrap) return false;
                const lr = list.getBoundingClientRect();
                const wr = wrap.getBoundingClientRect();
                return wr.left >= lr.left - 2 && wr.right <= lr.right + 2;
            }""",
            arg=last_id,
        )
        self.assertTrue(visible)
        overflow_btn = page.locator("#prks-workspace-tab-overflow")
        self.assertEqual(overflow_btn.count(), 1)

    def test_overflow_menu_keyboard_split_and_escape(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.evaluate(
            """(id) => {
                const jobs = [];
                for (let i = 0; i < 12; i++) {
                    jobs.push(window.prksNavigate('#/works/' + id, { target: 'new-tab', activate: false }));
                }
                return Promise.all(jobs);
            }""",
            arg=work_b,
        )
        page.wait_for_function("() => document.querySelectorAll('.prks-workspace-tab').length >= 12")
        page.evaluate(
            """() => {
                const list = document.getElementById('prks-workspace-tabs');
                if (list) list.style.maxWidth = '12rem';
                const snap = window.prksWorkspaceSnapshot();
                if (snap && snap.mainTabId && window.prksWorkspaceActivateTab) {
                    return window.prksWorkspaceActivateTab(snap.mainTabId);
                }
            }"""
        )
        page.wait_for_function(
            """() => {
                const btn = document.getElementById('prks-workspace-tab-overflow');
                const list = document.getElementById('prks-workspace-tabs');
                return !!(btn && !btn.hidden && list && list.scrollWidth > list.clientWidth + 2);
            }"""
        )
        page.locator("#prks-workspace-tab-overflow").focus()
        page.keyboard.press("Enter")
        page.wait_for_selector("#prks-workspace-menu:not([hidden])")
        page.keyboard.press("Escape")
        page.wait_for_function(
            """() => {
                const menu = document.getElementById('prks-workspace-menu');
                return !!(menu && menu.hidden && document.activeElement && document.activeElement.id === 'prks-workspace-tab-overflow');
            }"""
        )
        page.keyboard.press("Enter")
        page.wait_for_selector("#prks-workspace-menu:not([hidden])")
        steps = page.evaluate(
            """() => {
                const items = Array.prototype.slice.call(
                    document.querySelectorAll('#prks-workspace-menu [role="menuitem"]')
                );
                return items.findIndex(function (el) {
                    const label = (el.getAttribute('aria-label') || '') + ' ' + (el.textContent || '');
                    return /split view/i.test(label);
                });
            }"""
        )
        self.assertGreaterEqual(steps, 0)
        for _ in range(steps):
            page.keyboard.press("ArrowDown")
        page.wait_for_function(
            """() => {
                const ae = document.activeElement;
                if (!ae) return false;
                const label = (ae.getAttribute('aria-label') || '') + ' ' + (ae.textContent || '');
                return ae.getAttribute('role') === 'menuitem' && /split view/i.test(label);
            }"""
        )
        page.keyboard.press("Enter")
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'tiled'")
        self.assertEqual(page.locator(".prks-tile").count(), 2)
        self.assertEqual(page.evaluate("() => window.prksWorkspaceSnapshot().mode"), "tiled")

    def test_tab_trailing_actions_keyboard_only(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.evaluate(
            """(id) => window.prksNavigate('#/works/' + id, { target: 'new-tab', activate: false })""",
            arg=work_b,
        )
        page.wait_for_function("() => document.querySelectorAll('.prks-workspace-tab').length === 2")
        page.locator(".prks-workspace-tab.is-main .prks-workspace-tab__activate").focus()
        page.keyboard.press("Enter")
        page.keyboard.press("ArrowRight")
        page.wait_for_function(
            """() => {
                const ae = document.activeElement;
                return !!(ae && ae.classList && ae.classList.contains('prks-workspace-tab__activate')
                    && ae.closest('.prks-workspace-tab.is-parked'));
            }"""
        )
        page.keyboard.press("Tab")
        page.wait_for_function(
            """() => {
                const ae = document.activeElement;
                return !!(ae && ae.classList && ae.classList.contains('prks-workspace-tab__split'));
            }"""
        )
        page.keyboard.press("Enter")
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'tiled'")
        self.assertEqual(page.locator(".prks-tile").count(), 2)
        page.locator(".prks-workspace-tab.is-tiled .prks-workspace-tab__activate").focus()
        page.keyboard.press("Tab")
        page.wait_for_function(
            """() => {
                const ae = document.activeElement;
                return !!(ae && ae.classList && ae.classList.contains('prks-workspace-tab__close')
                    && ae.closest('.prks-workspace-tab.is-tiled'));
            }"""
        )
        page.keyboard.press("Enter")
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'stacked'")
        self.assertEqual(page.evaluate("() => window.prksWorkspaceSnapshot().tabs.length"), 1)

    def test_narrow_rejected_leave_does_not_reprompt_on_paint(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.evaluate(
            """(id) => window.prksNavigate('#/works/' + id, { target: 'tile' })""",
            arg=work_b,
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'tiled'")
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
                window.__prksNarrowCalls = 0;
                const orig = window.prksWorkspaceSetNarrowFallback;
                window.prksWorkspaceSetNarrowFallback = function (n) {
                    window.__prksNarrowCalls += 1;
                    return orig(n);
                };
            }"""
        )
        before = _workspace_ids(page)
        page.set_viewport_size({"width": 500, "height": 900})
        page.wait_for_function("() => window.__prksNarrowCalls >= 1")
        self.assertEqual(len(dialogs), 1)
        after = _workspace_ids(page)
        self.assertEqual(after["mode"], "tiled")
        self.assertEqual(after["secondaryTabId"], before["secondaryTabId"])
        self.assertEqual(after["mountedCount"], 2)
        self.assertTrue(page.evaluate("() => window.prksWorkspaceVisualTiled()"))
        calls_after_reject = page.evaluate("() => window.__prksNarrowCalls")
        page.evaluate(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                for (let i = 0; i < 5; i++) {
                    window.prksWorkspaceSyncTiles(snap, { visualMode: 'tiled' });
                    window.prksWorkspaceApplyFocus(snap, { visualMode: 'tiled' });
                    window.prksWorkspaceFocusTab(snap.focusedTabId);
                    const tab = snap.tabs.find(function (t) { return t.id === snap.mainTabId; });
                    if (tab && window.prksWorkspaceSetResolvedTitleForTab) {
                        window.prksWorkspaceSetResolvedTitleForTab(snap.mainTabId, tab.route, tab.title);
                    }
                }
            }"""
        )
        self.assertEqual(len(dialogs), 1)
        self.assertEqual(page.evaluate("() => window.__prksNarrowCalls"), calls_after_reject)
        self.assertEqual(_workspace_ids(page)["mountedCount"], 2)
        self.assertTrue(page.evaluate("() => window.prksWorkspaceVisualTiled()"))
        page.set_viewport_size({"width": 1600, "height": 900})
        page.wait_for_function(
            """(n) => {
                const c = document.querySelector('.prks-workspace-canvas');
                return !!(c && c.clientWidth >= 720 && window.__prksNarrowCalls > n);
            }""",
            arg=calls_after_reject,
        )
        calls_wide = page.evaluate("() => window.__prksNarrowCalls")
        page.set_viewport_size({"width": 500, "height": 900})
        page.wait_for_function(
            """(n) => {
                const c = document.querySelector('.prks-workspace-canvas');
                return !!(c && c.clientWidth > 0 && c.clientWidth < 720 && window.__prksNarrowCalls > n);
            }""",
            arg=calls_wide,
        )
        self.assertGreaterEqual(len(dialogs), 2)
        still = _workspace_ids(page)
        self.assertEqual(still["secondaryTabId"], before["secondaryTabId"])
        self.assertEqual(still["mountedCount"], 2)

    def test_close_secondary_restores_focus_to_main(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.evaluate(
            """(id) => window.prksNavigate('#/works/' + id, { target: 'tile' })""",
            arg=work_b,
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'tiled'")
        page.locator(".prks-tile--secondary").click(position={"x": 24, "y": 80})
        page.wait_for_function(
            "() => window.prksWorkspaceSnapshot().focusedTabId === window.prksWorkspaceSnapshot().secondaryTree.tabId"
        )
        page.evaluate(
            """() => window.prksWorkspaceCloseTab(window.prksWorkspaceSnapshot().secondaryTree.tabId)"""
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'stacked'")
        page.wait_for_function(
            """() => {
                const ae = document.activeElement;
                if (!ae) return false;
                return !!(
                    (ae.closest && ae.closest('.prks-tile--main')) ||
                    (ae.closest && ae.closest('.prks-workspace-tab.is-main'))
                );
            }"""
        )

    def test_close_main_promotes_secondary_without_home_flash(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.evaluate(
            """(id) => window.prksNavigate('#/works/' + id, { target: 'tile' })""",
            arg=work_b,
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'tiled'")
        hashes = page.evaluate(
            """() => {
                window.__prksHashLog = [];
                const orig = history.replaceState.bind(history);
                history.replaceState = function () {
                    orig.apply(this, arguments);
                    window.__prksHashLog.push(location.hash);
                };
                const snap = window.prksWorkspaceSnapshot();
                return window.prksWorkspaceCloseTab(snap.mainTabId).then(function () {
                    return window.__prksHashLog.slice();
                });
            }"""
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'stacked'")
        self.assertTrue(all("/folders" not in h for h in hashes))
        self.assertIn(work_b, page.evaluate("() => location.hash"))

    def test_close_main_recursive_preserves_secondary_leaves(self):
        """Closing Main on a recursive tree (Main A, Secondary B / (C | D)) must not hide or
        remount unrelated surviving Secondary leaves: only A is destroyed, the deterministic
        first Secondary leaf B is promoted into Main's exact position, and the C|D split
        survives untouched -- same tree position, still mounted, no route/PDF reload."""
        server, page, _collector = self._start_app(seed_fn=seed_graph_context_library)
        page.set_viewport_size({"width": 1600, "height": 900})
        tree = _build_three_leaf_tree(page, server)

        page.evaluate(
            """(ids) => {
                window.__prksMainCloseRt = {
                    bCtx: window.prksGetTabContext(ids.b),
                    cCtx: window.prksGetTabContext(ids.c),
                    dCtx: window.prksGetTabContext(ids.d),
                    dPdf: window.prksGetTabContext(ids.d).getResource('pdf'),
                    dNotes: window.prksGetTabContext(ids.d).getResource('workNotes'),
                };
            }""",
            arg={"b": tree["b_id"], "c": tree["c_id"], "d": tree["d_id"]},
        )

        seen_gets = []
        page.on("request", lambda req: seen_gets.append(req.url) if req.method == "GET" else None)

        closed = page.evaluate("(id) => window.prksWorkspaceCloseTab(id)", arg=tree["main_id"])
        self.assertTrue(closed)
        page.wait_for_function("() => window.prksTabContextDebugSnapshot().mountedCount === 3")

        snap = page.evaluate("() => window.prksWorkspaceSnapshot()")
        self.assertEqual(snap["mainTabId"], tree["b_id"])
        self.assertEqual(snap["focusedTabId"], tree["b_id"])
        self.assertEqual(snap["mode"], "tiled")
        self.assertIn(server.ids["person"], page.evaluate("() => location.hash"))

        shape_ok = page.evaluate(
            """(ids) => {
                const tree = window.prksWorkspaceSnapshot().secondaryTree;
                return !!(
                    tree && tree.type === 'split' &&
                    tree.first && tree.first.tabId === ids.c &&
                    tree.second && tree.second.tabId === ids.d
                );
            }""",
            arg={"c": tree["c_id"], "d": tree["d_id"]},
        )
        self.assertTrue(shape_ok, "expected surviving C|D split; B was promoted out of the tree")
        self.assertEqual(page.locator(".prks-tile").count(), 3)

        identity = page.evaluate(
            """(ids) => {
                const rt = window.__prksMainCloseRt;
                const b = window.prksGetTabContext(ids.b);
                const c = window.prksGetTabContext(ids.c);
                const d = window.prksGetTabContext(ids.d);
                return {
                    bSame: b === rt.bCtx,
                    cSame: c === rt.cCtx,
                    dSame: d === rt.dCtx,
                    dPdfSame: d.getResource('pdf') === rt.dPdf,
                    dNotesSame: d.getResource('workNotes') === rt.dNotes,
                };
            }""",
            arg={"b": tree["b_id"], "c": tree["c_id"], "d": tree["d_id"]},
        )
        self.assertTrue(identity["bSame"], "B's TabContext must survive being promoted to Main")
        self.assertTrue(identity["cSame"], "C is unrelated to the close and must not remount")
        self.assertTrue(identity["dSame"], "D is unrelated to the close and must not remount")
        self.assertTrue(identity["dPdfSame"])
        self.assertTrue(identity["dNotesSame"])

        for path in (
            "/api/persons/" + server.ids["person"],
            "/api/positions/" + server.ids["position"],
            "/api/works/" + server.ids["work_b"],
        ):
            self.assertFalse(
                any(path in u for u in seen_gets),
                "surviving unrelated Secondary leaf reloaded during Main close: " + path,
            )

    def test_close_tabs_to_the_right_preserves_unrelated_secondary(self):
        """closeTabsToTheRight must apply the same principle as single-tab Main close: only the
        leaves actually requested to close are removed from the tree. Build a tab order where
        Main is among "tabs to the right" of the anchor while two unrelated Secondary leaves
        positioned before the anchor survive untouched."""
        server, page, _collector = self._start_app(seed_fn=seed_graph_context_library)
        page.set_viewport_size({"width": 1600, "height": 900})
        work_a = server.ids["work_a"]
        work_b = server.ids["work_b"]
        person_id = server.ids["person"]
        position_id = server.ids["position"]
        argument_id = server.ids["argument"]

        # A idx0 (Main), B idx1 (Secondary leaf).
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        page.evaluate(
            """(id) => window.prksNavigate('#/people/' + id, { target: 'tile' })""",
            arg=person_id,
        )
        page.wait_for_function(
            "() => window.prksWorkspaceSnapshot().secondaryTree && window.prksWorkspaceSnapshot().secondaryTree.type === 'leaf'"
        )
        a_id = page.evaluate("() => window.prksWorkspaceSnapshot().mainTabId")
        b_id = page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree.tabId")

        # K idx2 (parked anchor).
        page.evaluate(
            """(id) => window.prksNavigate('#/positions/' + id, { target: 'new-tab', activate: false })""",
            arg=position_id,
        )
        k_id = page.evaluate("() => window.prksWorkspaceSnapshot().tabs[2].id")

        # D idx3 (Secondary leaf, splits B).
        d_tab = page.evaluate(
            """(a) => window.prksWorkspaceSplitLeaf(a.target, 'top-bottom', { hash: '#/works/' + a.work })""",
            arg={"target": b_id, "work": work_b},
        )
        d_id = d_tab["id"]
        page.wait_for_function("() => window.prksTabContextDebugSnapshot().mountedCount === 3")

        # Make D Main: D keeps its array index (3); A (old Main) takes D's former leaf slot, so
        # the surviving tree becomes split(B, A) -- both B and A now sit BEFORE the anchor K.
        page.evaluate("(id) => window.prksWorkspaceMakeMain(id)", arg=d_id)
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mainTabId !== undefined")
        self.assertEqual(page.evaluate("() => window.prksWorkspaceSnapshot().mainTabId"), d_id)

        # E idx4 (parked, also closes alongside Main).
        page.evaluate(
            """(id) => window.prksNavigate('#/arguments/' + id, { target: 'new-tab', activate: false })""",
            arg=argument_id,
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().tabs.length === 5")

        page.evaluate(
            """(ids) => {
                window.__prksBatchCloseRt = {
                    bCtx: window.prksGetTabContext(ids.b),
                    aCtx: window.prksGetTabContext(ids.a),
                };
            }""",
            arg={"b": b_id, "a": a_id},
        )

        seen_gets = []
        page.on("request", lambda req: seen_gets.append(req.url) if req.method == "GET" else None)

        closed = page.evaluate("(id) => window.prksWorkspaceCloseTabsToTheRight(id)", arg=k_id)
        self.assertTrue(closed)
        page.wait_for_function("() => window.prksWorkspaceSnapshot().tabs.length === 3")

        snap = page.evaluate("() => window.prksWorkspaceSnapshot()")
        self.assertEqual(snap["mainTabId"], k_id, "K must be promoted to Main in D's place")
        self.assertEqual(snap["secondaryTree"]["first"]["tabId"], b_id)
        self.assertEqual(snap["secondaryTree"]["second"]["tabId"], a_id)
        self.assertEqual(page.locator(".prks-tile").count(), 3)

        identity = page.evaluate(
            """(ids) => {
                const rt = window.__prksBatchCloseRt;
                return {
                    bSame: window.prksGetTabContext(ids.b) === rt.bCtx,
                    aSame: window.prksGetTabContext(ids.a) === rt.aCtx,
                };
            }""",
            arg={"b": b_id, "a": a_id},
        )
        self.assertTrue(identity["bSame"], "B is unrelated to the close and must not remount")
        self.assertTrue(identity["aSame"], "A (now a leaf) is unrelated to the close and must not remount")

        for path in ("/api/persons/" + person_id, "/api/works/" + work_a):
            self.assertFalse(
                any(path in u for u in seen_gets),
                "unrelated surviving Secondary leaf reloaded during batch close: " + path,
            )

    def test_tile_tab_default_split_does_not_remount_main(self):
        """prksWorkspaceTileTab() on a parked tab, with exactly one existing Secondary leaf,
        splits that leaf instead of replacing it -- and never touches Main's DOM/runtime."""
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_b = server.ids["work_b"]
        person_id = server.ids["person"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.evaluate(
            """(id) => window.prksNavigate('#/works/' + id, { target: 'tile' })""",
            arg=work_b,
        )
        page.wait_for_function("() => document.querySelectorAll('.work-detail').length === 2")
        page.evaluate(
            """(id) => window.prksNavigate('#/people/' + id, { target: 'new-tab', activate: false })""",
            arg=person_id,
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().tabs.length >= 3")
        roots = page.evaluate(
            """() => {
                const main = document.querySelector('.prks-tile--main .prks-tab-root');
                window.__prksMainRoot = main;
                return {
                    mainRoot: !!main,
                    mainWork: !!document.querySelector('.prks-tile--main .work-detail'),
                };
            }"""
        )
        self.assertTrue(roots["mainRoot"])
        b_id = page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree.tabId")
        parked = page.evaluate(
            """() => window.prksWorkspaceSnapshot().tabs.find(function (t) {
                return t.id !== window.prksWorkspaceSnapshot().mainTabId
                    && t.id !== window.prksWorkspaceSnapshot().secondaryTree.tabId;
            }).id"""
        )
        page.evaluate("(id) => window.prksWorkspaceTileTab(id)", arg=parked)
        page.wait_for_function(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                return snap.secondaryTree && snap.secondaryTree.type === 'split';
            }"""
        )
        tree = page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree")
        self.assertEqual(tree["first"]["tabId"], b_id)
        self.assertEqual(tree["second"]["tabId"], parked)
        same = page.evaluate(
            """() => {
                const main = document.querySelector('.prks-tile--main .prks-tab-root');
                return {
                    sameRoot: main === window.__prksMainRoot,
                    work: !!document.querySelector('.prks-tile--main .work-detail'),
                    tiles: document.querySelectorAll('.prks-tile').length,
                };
            }"""
        )
        self.assertTrue(same["sameRoot"])
        self.assertTrue(same["work"])
        self.assertEqual(same["tiles"], 3)

    def test_work_status_isolated_per_tab(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.evaluate(
            """(id) => window.prksNavigate('#/works/' + id, { target: 'tile' })""",
            arg=work_b,
        )
        page.wait_for_function("() => document.querySelectorAll('.CodeMirror').length === 2")
        page.locator(".prks-tile--main .CodeMirror").click()
        page.keyboard.insert_text("STATUS-A")
        page.wait_for_function(
            """() => !!document.querySelector('.prks-workspace-tab.is-main .prks-workspace-tab__status--drafting')"""
        )
        isolated = page.evaluate(
            """() => {
                const main = document.querySelector('.prks-workspace-tab.is-main .prks-workspace-tab__status--drafting');
                const sec = document.querySelector('.prks-workspace-tab.is-tiled .prks-workspace-tab__status--drafting');
                const snap = window.prksWorkspaceSnapshot();
                const a = window.prksGetTabContext(snap.mainTabId).getResource('workNotes');
                const b = window.prksGetTabContext(snap.secondaryTree.tabId).getResource('workNotes');
                return {
                    mainDot: !!main,
                    secDot: !!sec,
                    aDraft: !!(a && a.drafting),
                    bDraft: !!(b && b.drafting),
                };
            }"""
        )
        self.assertTrue(isolated["mainDot"])
        self.assertFalse(isolated["secDot"])
        self.assertTrue(isolated["aDraft"])
        self.assertFalse(isolated["bDraft"])

    def test_narrow_wide_restores_secondary(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.evaluate(
            """(id) => window.prksNavigate('#/works/' + id, { target: 'tile' })""",
            arg=work_b,
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'tiled'")
        main_id = page.evaluate("() => window.prksWorkspaceSnapshot().mainTabId")
        sec_id = page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree.tabId")
        page.evaluate("() => window.prksWorkspaceSetNarrowFallback(true)")
        page.wait_for_function("() => window.prksWorkspaceVisualTiled() === false")
        btn = page.locator("#prks-workspace-tile-layout")
        self.assertNotIn("Hide", btn.get_attribute("aria-label") or "")
        self.assertEqual(page.evaluate("() => window.prksWorkspaceSnapshot().focusedTabId"), main_id)
        self.assertEqual(page.locator(".prks-tile").count(), 1)
        page.evaluate("() => window.prksWorkspaceSetNarrowFallback(false)")
        page.wait_for_function("() => window.prksWorkspaceVisualTiled() === true")
        after = _workspace_ids(page)
        self.assertEqual(after["mainTabId"], main_id)
        self.assertEqual(after["secondaryTabId"], sec_id)
        self.assertEqual(after["focusedTabId"], main_id)
        self.assertEqual(page.locator(".prks-tile").count(), 2)
        self.assertEqual(page.locator(".prks-tile--main.prks-tile--focused").count(), 1)

    def test_tab_context_menu_commands(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.evaluate(
            """(id) => window.prksNavigate('#/works/' + id, { target: 'new-tab', activate: false })""",
            arg=work_b,
        )
        page.wait_for_function("() => document.querySelectorAll('.prks-workspace-tab').length === 2")
        page.locator(".prks-workspace-tab.is-parked").click(button="right")
        page.wait_for_selector("#prks-workspace-menu:not([hidden])")
        labels = page.locator("#prks-workspace-menu .prks-workspace-menu__item").all_text_contents()
        self.assertTrue(any("Make main" in t for t in labels))
        self.assertTrue(any("Open in split view" in t for t in labels))
        self.assertTrue(any(t.strip() == "Close" for t in labels))
        page.locator("#prks-workspace-menu .prks-workspace-menu__item", has_text="Open in split view").click()
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'tiled'")
        self.assertEqual(page.locator(".prks-tile").count(), 2)
        page.locator(".prks-workspace-tab.is-tiled").click(button="right")
        page.wait_for_selector("#prks-workspace-menu:not([hidden])")
        tiled_labels = page.locator("#prks-workspace-menu .prks-workspace-menu__item").all_text_contents()
        self.assertTrue(any("Hide from split" in t for t in tiled_labels))
        self.assertTrue(any("Split right" in t for t in tiled_labels))
        self.assertTrue(any("Split down" in t for t in tiled_labels))
        self.assertFalse(any("tileTab" in t or "secondaryTree" in t for t in tiled_labels))

    def test_loading_secondary_leaves_main_intact(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_b = server.ids["work_b"]
        person_id = server.ids["person"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.evaluate(
            """(id) => window.prksNavigate('#/works/' + id, { target: 'tile' })""",
            arg=work_b,
        )
        page.wait_for_function("() => document.querySelectorAll('.work-detail').length === 2")
        page.evaluate(
            """() => {
                window.__prksMainRoot = document.querySelector('.prks-tile--main .prks-tab-root');
                window.__prksMainHeader = document.querySelector('.prks-tile--main .prks-tile-header');
            }"""
        )
        page.evaluate(
            """(id) => window.prksNavigate('#/people/' + id, { target: 'current', tabId: window.prksWorkspaceSnapshot().secondaryTree.tabId })""",
            arg=person_id,
        )
        page.wait_for_function(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                const tab = snap.tabs.find(function (t) { return t.id === snap.secondaryTree.tabId; });
                return !!(tab && tab.route.indexOf('/people/') !== -1);
            }"""
        )
        intact = page.evaluate(
            """() => {
                return {
                    sameRoot: document.querySelector('.prks-tile--main .prks-tab-root') === window.__prksMainRoot,
                    sameHeader: document.querySelector('.prks-tile--main .prks-tile-header') === window.__prksMainHeader,
                    work: !!document.querySelector('.prks-tile--main .work-detail'),
                    tiles: document.querySelectorAll('.prks-tile').length,
                    hash: location.hash,
                };
            }"""
        )
        self.assertTrue(intact["sameRoot"])
        self.assertTrue(intact["sameHeader"])
        self.assertTrue(intact["work"])
        self.assertEqual(intact["tiles"], 2)
        self.assertIn(server.ids["work_a"], intact["hash"])

    def test_main_and_focused_secondary_are_distinct(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.evaluate(
            """(id) => window.prksNavigate('#/works/' + id, { target: 'tile' })""",
            arg=work_b,
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'tiled'")
        page.locator(".prks-tile--secondary").click(position={"x": 24, "y": 80})
        page.wait_for_function(
            "() => window.prksWorkspaceSnapshot().focusedTabId === window.prksWorkspaceSnapshot().secondaryTree.tabId"
        )
        dist = page.evaluate(
            """() => {
                const mainTab = document.querySelector('.prks-workspace-tab.is-main');
                const secTab = document.querySelector('.prks-workspace-tab.is-tiled');
                const mainTile = document.querySelector('.prks-tile--main');
                const secTile = document.querySelector('.prks-tile--secondary');
                return {
                    mainIsMain: mainTab && mainTab.classList.contains('is-main'),
                    secIsMain: secTab && secTab.classList.contains('is-main'),
                    secFocused: secTab && secTab.classList.contains('is-focused'),
                    mainFocusedTab: mainTab && mainTab.classList.contains('is-focused'),
                    mainTileFocused: mainTile && mainTile.classList.contains('prks-tile--focused'),
                    secTileFocused: secTile && secTile.classList.contains('prks-tile--focused'),
                    mainHasRole: !!(mainTile && mainTile.querySelector('.prks-tile-header__role')),
                    secHasMakeMain: !!(secTile && secTile.querySelector('.prks-tile-header__make-main')),
                };
            }"""
        )
        self.assertTrue(dist["mainIsMain"])
        self.assertFalse(dist["secIsMain"])
        self.assertTrue(dist["secFocused"])
        self.assertTrue(dist["secTileFocused"])
        self.assertFalse(dist["mainTileFocused"])
        self.assertTrue(dist["mainHasRole"])
        self.assertTrue(dist["secHasMakeMain"])


class MainSecondaryDividerTests(_BrowserE2E):
    def test_divider_hidden_in_stacked_and_present_in_split_with_aria(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        _open_work_from_home(page, WORK_A_TITLE)
        self.assertEqual(page.locator(".prks-splitter").count(), 0)

        _open_work_work_split(page, server)
        sep = page.locator(".prks-splitter")
        self.assertEqual(sep.count(), 1)
        self.assertEqual(sep.get_attribute("role"), "separator")
        self.assertEqual(sep.get_attribute("aria-orientation"), "vertical")
        self.assertEqual(sep.get_attribute("tabindex"), "0")
        now = int(sep.get_attribute("aria-valuenow"))
        self.assertTrue(50 <= now <= 66, "default ~58%% got %s" % now)
        text = sep.get_attribute("aria-valuetext") or ""
        self.assertIn("Main", text)
        self.assertIn("secondary", text)

        page.evaluate("() => window.prksWorkspaceSetMode('stacked')")
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'stacked'")
        self.assertEqual(page.locator(".prks-splitter").count(), 0)

    def test_divider_pointer_drag_resizes_live_and_preserves_runtime(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        _open_work_work_split(page, server)
        _capture_divider_runtime_ids(page)

        ratio_before = page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio")
        main_before = page.locator(".prks-tile--main").bounding_box()
        sec_before = page.locator(".prks-tile--secondary").bounding_box()

        _drag_divider(page, 150)
        page.wait_for_function(
            "r => window.prksWorkspaceSnapshot().mainSplitRatio > r + 0.02",
            arg=ratio_before,
        )
        ratio_after = page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio")
        main_after = page.locator(".prks-tile--main").bounding_box()
        sec_after = page.locator(".prks-tile--secondary").bounding_box()
        self.assertGreater(ratio_after, ratio_before)
        self.assertGreater(main_after["width"], main_before["width"])
        self.assertLess(sec_after["width"], sec_before["width"])
        self.assertGreater(main_after["width"], 0)
        self.assertGreater(sec_after["width"], 0)
        self.assertEqual(page.locator(".prks-tile").count(), 2)

        _drag_divider(page, -220)
        page.wait_for_function(
            "r => window.prksWorkspaceSnapshot().mainSplitRatio < r - 0.02",
            arg=ratio_after,
        )
        ratio_final = page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio")
        main_final = page.locator(".prks-tile--main").bounding_box()
        self.assertLess(main_final["width"], main_after["width"])

        same = _divider_runtime_ids_unchanged(page)
        self.assertTrue(same["aPdf"])
        self.assertTrue(same["bPdf"])
        self.assertTrue(same["aNotes"])
        self.assertTrue(same["bNotes"])
        self.assertEqual(same["mounted"], 2)
        self.assertNotEqual(ratio_final, ratio_before)

    def test_divider_keyboard_resize_home_end_and_aria(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        _open_work_work_split(page, server)
        sep = page.locator(".prks-splitter")
        sep.focus()
        focused_before = page.evaluate("() => window.prksWorkspaceSnapshot().focusedTabId")

        ratio0 = page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio")
        page.keyboard.press("ArrowRight")
        page.wait_for_function("r => window.prksWorkspaceSnapshot().mainSplitRatio > r", arg=ratio0)
        ratio1 = page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio")
        self.assertAlmostEqual(ratio1 - ratio0, 0.02, delta=0.005)

        page.keyboard.press("Shift+ArrowRight")
        page.wait_for_function("r => window.prksWorkspaceSnapshot().mainSplitRatio > r", arg=ratio1)
        ratio2 = page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio")
        self.assertAlmostEqual(ratio2 - ratio1, 0.05, delta=0.005)

        page.keyboard.press("ArrowLeft")
        page.wait_for_function("r => window.prksWorkspaceSnapshot().mainSplitRatio < r", arg=ratio2)
        ratio3 = page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio")
        now_attr = int(sep.get_attribute("aria-valuenow"))
        self.assertEqual(now_attr, round(ratio3 * 100))

        page.keyboard.press("Home")
        page.wait_for_function(
            """() => {
                const s = document.querySelector('.prks-splitter');
                return s && s.getAttribute('aria-valuenow') === s.getAttribute('aria-valuemin');
            }"""
        )
        min_ratio = page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio")

        page.keyboard.press("End")
        page.wait_for_function(
            """() => {
                const s = document.querySelector('.prks-splitter');
                return s && s.getAttribute('aria-valuenow') === s.getAttribute('aria-valuemax');
            }"""
        )
        max_ratio = page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio")
        self.assertGreater(max_ratio, min_ratio)

        focused_after = page.evaluate("() => window.prksWorkspaceSnapshot().focusedTabId")
        self.assertEqual(focused_after, focused_before)
        self.assertEqual(page.locator(".prks-tile").count(), 2)

    def test_divider_double_click_resets_default(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        _open_work_work_split(page, server)
        _capture_divider_runtime_ids(page)

        _drag_divider(page, 250)
        page.wait_for_function(
            "() => Math.abs(window.prksWorkspaceSnapshot().mainSplitRatio - 0.58) > 0.05"
        )
        page.locator(".prks-splitter").dblclick()
        page.wait_for_function(
            "() => Math.abs(window.prksWorkspaceSnapshot().mainSplitRatio - 0.58) < 0.02"
        )
        live_text = (page.locator("#prks-workspace-live").inner_text() or "").lower()
        self.assertIn("reset", live_text)
        self.assertEqual(page.locator(".prks-tile").count(), 2)
        same = _divider_runtime_ids_unchanged(page)
        self.assertTrue(same["aPdf"])
        self.assertTrue(same["bPdf"])
        self.assertEqual(same["mounted"], 2)

    def test_divider_hide_show_preserves_ratio(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        _open_work_work_split(page, server)

        _drag_divider(page, -140)
        page.wait_for_function(
            "() => Math.abs(window.prksWorkspaceSnapshot().mainSplitRatio - 0.58) > 0.03"
        )
        custom_ratio = page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio")

        page.locator("#prks-workspace-tile-layout").click()
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'stacked'")
        self.assertEqual(page.locator(".prks-splitter").count(), 0)
        self.assertAlmostEqual(
            page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio"), custom_ratio, delta=1e-6
        )

        page.locator("#prks-workspace-tile-layout").click()
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'tiled'")
        page.wait_for_selector(".prks-splitter")
        self.assertAlmostEqual(
            page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio"), custom_ratio, delta=0.02
        )

    def test_divider_make_main_preserves_ratio_and_runtime(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        _work_a, work_b, _ids0 = _open_work_work_split(page, server)

        _drag_divider(page, 130)
        page.wait_for_function(
            "() => Math.abs(window.prksWorkspaceSnapshot().mainSplitRatio - 0.58) > 0.03"
        )
        custom_ratio = page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio")
        _capture_divider_runtime_ids(page)

        page.locator(".prks-tile--secondary .prks-tile-header__make-main").click()
        page.wait_for_function(
            """(b) => {
                const snap = window.prksWorkspaceSnapshot();
                return snap.mainTabId && location.hash.indexOf(b) !== -1 && snap.secondaryTree
                    && snap.mainTabId !== snap.secondaryTree.tabId;
            }""",
            arg=work_b,
        )
        self.assertAlmostEqual(
            page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio"), custom_ratio, delta=1e-6
        )
        same = page.evaluate(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                const a = window.prksGetTabContext(snap.secondaryTree.tabId);
                const b = window.prksGetTabContext(snap.mainTabId);
                const rt = window.__prksDividerRt;
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
        self.assertEqual(page.locator(".prks-splitter").count(), 1)

    def test_divider_narrow_fallback_preserves_ratio(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        _open_work_work_split(page, server)

        _drag_divider(page, 140)
        page.wait_for_function(
            "() => Math.abs(window.prksWorkspaceSnapshot().mainSplitRatio - 0.58) > 0.03"
        )
        custom_ratio = page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio")

        page.evaluate("() => window.prksWorkspaceSetNarrowFallback(true)")
        page.wait_for_function("() => window.prksWorkspaceVisualTiled() === false")
        self.assertEqual(page.locator(".prks-splitter").count(), 0)
        self.assertAlmostEqual(
            page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio"), custom_ratio, delta=1e-6
        )

        page.evaluate("() => window.prksWorkspaceSetNarrowFallback(false)")
        page.wait_for_function("() => window.prksWorkspaceVisualTiled() === true")
        page.wait_for_selector(".prks-splitter")
        self.assertAlmostEqual(
            page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio"), custom_ratio, delta=0.02
        )

    def test_divider_resize_does_not_trigger_leave_prompt(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        _open_work_work_split(page, server)

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
        _drag_divider(page, 150)
        page.wait_for_timeout(200)
        self.assertEqual(dialogs, [])
        self.assertEqual(page.locator(".prks-tile").count(), 2)

    def test_divider_pointer_cancel_recovers_cleanly(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        _open_work_work_split(page, server)
        sep = page.locator(".prks-splitter")
        box = sep.bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + 80, box["y"] + box["height"] / 2, steps=5)
        result = page.evaluate(
            """() => {
                const s = document.querySelector('.prks-splitter');
                s.dispatchEvent(new PointerEvent('pointercancel', { pointerId: 1, bubbles: true }));
                return {
                    dragging: s.classList.contains('is-dragging'),
                    bodyResizing: document.body.classList.contains('prks-resizing-split'),
                    ratio: window.prksWorkspaceSnapshot().mainSplitRatio,
                };
            }"""
        )
        page.mouse.up()
        self.assertFalse(result["dragging"])
        self.assertFalse(result["bodyResizing"])
        self.assertGreaterEqual(result["ratio"], 0)
        self.assertLessEqual(result["ratio"], 1)
        self.assertEqual(page.locator(".prks-tile").count(), 2)

    def test_divider_narrow_viewport_transition_preserves_preferred_ratio(self):
        """A real width transition (not a direct prksWorkspaceSetNarrowFallback call) must
        preserve the user's preferred ratio through an accepted narrow fallback and restore it
        (clamped only if genuinely necessary) once the canvas widens again."""
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        _open_work_work_split(page, server)

        _drag_divider(page, 130)
        page.wait_for_function(
            "() => Math.abs(window.prksWorkspaceSnapshot().mainSplitRatio - 0.58) > 0.03"
        )
        custom_ratio = page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio")

        page.set_viewport_size({"width": 500, "height": 900})
        page.wait_for_function("() => window.prksWorkspaceVisualTiled() === false")
        self.assertEqual(page.locator(".prks-splitter").count(), 0)
        self.assertIsNotNone(page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree"))
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 1)
        self.assertAlmostEqual(
            page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio"), custom_ratio, delta=1e-6
        )

        page.set_viewport_size({"width": 1600, "height": 900})
        page.wait_for_function("() => window.prksWorkspaceVisualTiled() === true")
        page.wait_for_selector(".prks-splitter")
        page.wait_for_function("() => window.prksTabContextDebugSnapshot().mountedCount === 2")
        self.assertAlmostEqual(
            page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio"), custom_ratio, delta=0.02
        )

    def test_divider_narrow_viewport_rejected_transition_preserves_preferred_ratio(self):
        """A rejected leave during a real narrow width transition must keep the Secondary
        mounted, keep the visual split alive, and never mutate the preferred ratio -- including
        across the ordinary workspace paints that follow the rejection."""
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        _open_work_work_split(page, server)

        _drag_divider(page, 130)
        page.wait_for_function(
            "() => Math.abs(window.prksWorkspaceSnapshot().mainSplitRatio - 0.58) > 0.03"
        )
        custom_ratio = page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio")

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
                window.__prksNarrowCalls = 0;
                const orig = window.prksWorkspaceSetNarrowFallback;
                window.prksWorkspaceSetNarrowFallback = function (n) {
                    window.__prksNarrowCalls += 1;
                    return orig(n);
                };
            }"""
        )

        page.set_viewport_size({"width": 500, "height": 900})
        page.wait_for_function("() => window.__prksNarrowCalls >= 1")
        self.assertEqual(len(dialogs), 1)
        self.assertEqual(page.evaluate("() => window.prksWorkspaceSnapshot().mode"), "tiled")
        self.assertTrue(page.evaluate("() => window.prksWorkspaceVisualTiled()"))
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 2)
        self.assertEqual(page.locator(".prks-splitter").count(), 1)
        self.assertAlmostEqual(
            page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio"), custom_ratio, delta=1e-6
        )

        page.evaluate(
            """() => {
                const snap = window.prksWorkspaceSnapshot();
                for (let i = 0; i < 5; i++) {
                    window.prksWorkspaceSyncTiles(snap, { visualMode: 'tiled' });
                    window.prksWorkspaceApplyFocus(snap, { visualMode: 'tiled' });
                    window.prksWorkspaceFocusTab(snap.focusedTabId);
                }
            }"""
        )
        self.assertEqual(len(dialogs), 1, "ordinary paints must not reprompt")
        self.assertAlmostEqual(
            page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio"), custom_ratio, delta=1e-6
        )

        page.set_viewport_size({"width": 1600, "height": 900})
        page.wait_for_function(
            "(r) => Math.abs(window.prksWorkspaceSnapshot().mainSplitRatio - r) < 0.02",
            arg=custom_ratio,
        )
        self.assertEqual(page.locator(".prks-splitter").count(), 1)
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 2)

    def test_divider_drag_causes_no_additional_route_or_pdf_requests(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        _open_work_work_split(page, server)

        requests = []

        def on_request(req):
            path = urlparse(req.url).path
            if req.method == "GET" and (path.startswith("/api/works/") or path.startswith("/api/pdfs/")):
                requests.append(path)

        page.on("request", on_request)
        before = len(requests)

        _drag_divider(page, 150)
        page.wait_for_timeout(150)
        _drag_divider(page, -220)
        page.wait_for_timeout(150)
        _drag_divider(page, 90)
        page.wait_for_timeout(150)

        self.assertEqual(len(requests), before, "divider drags must not fetch a Work route or reload a PDF")
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 2)


class WorkspaceDragDropTests(_BrowserE2E):
    """Real-pointer coverage for workspace-drag.js. Every test drives an actual
    page.mouse down/move/up sequence through the DOM -- never a direct call into
    prksWorkspaceReorderTab/prksWorkspaceMovePane/prksWorkspaceHideLeaf/prksWorkspaceSplitLeaf,
    which would only prove those canonical APIs work (already covered by the state/tree
    selftests), not that the drag controller's geometry/targeting correctly drives them."""

    def test_tab_reorder_with_real_pointer_and_close_to_the_right(self):
        server, page, _collector = self._start_app(seed_fn=seed_graph_context_library)
        page.set_viewport_size({"width": 1600, "height": 900})
        person_id = server.ids["person"]
        position_id = server.ids["position"]
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        work_hash = page.evaluate("() => location.hash")
        for hash_ in ("#/people/" + person_id, "#/positions/" + position_id, "#/works/" + work_b):
            page.evaluate(
                """(h) => window.prksNavigate(h, { target: 'new-tab', activate: false })""",
                arg=hash_,
            )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().tabs.length === 4")
        before = page.evaluate("() => window.prksWorkspaceSnapshot()")
        a_id, b_id, c_id, d_id = [t["id"] for t in before["tabs"]]
        self.assertIsNone(before["secondaryTree"])

        seen_gets = []
        page.on("request", lambda req: seen_gets.append(req.url) if req.method == "GET" else None)

        # Real pointer drag: D onto the strip, dropped just inside B's leading edge -> A, D, B, C.
        d_box = _tab_box(page, d_id)
        b_box = _tab_box(page, b_id)
        _pointer_drag(page, _tab_grab_point(d_box), (b_box["x"] + 4, b_box["y"] + b_box["height"] / 2))
        page.wait_for_function(
            "(ids) => window.prksWorkspaceSnapshot().tabs.map(t => t.id).join(',') === ids",
            arg=",".join([a_id, d_id, b_id, c_id]),
        )
        _assert_no_drag_residue(self, page, "after a completed tab reorder")

        after = page.evaluate("() => window.prksWorkspaceSnapshot()")
        self.assertEqual(after["mainTabId"], before["mainTabId"])
        self.assertEqual(after["focusedTabId"], before["focusedTabId"])
        self.assertIsNone(after["secondaryTree"])
        self.assertEqual(page.evaluate("() => location.hash"), work_hash)
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 1)
        for path in ("/api/works/", "/api/persons/", "/api/positions/"):
            self.assertFalse(any(path in u for u in seen_gets), "tab reorder must not fetch any route: " + path)

        # Close tabs to the right of D (its NEW position) must use the new order, closing B/C.
        closed = page.evaluate("(id) => window.prksWorkspaceCloseTabsToTheRight(id)", arg=d_id)
        self.assertTrue(closed)
        page.wait_for_function("() => window.prksWorkspaceSnapshot().tabs.length === 2")
        remaining = [t["id"] for t in page.evaluate("() => window.prksWorkspaceSnapshot().tabs")]
        self.assertEqual(remaining, [a_id, d_id])

    def test_drag_parked_tab_creates_first_secondary(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        person_id = server.ids["person"]
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        work_hash = page.evaluate("() => location.hash")
        page.evaluate(
            """(id) => window.prksNavigate('#/people/' + id, { target: 'new-tab', activate: false })""",
            arg=person_id,
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().tabs.length === 2")
        b_id = page.evaluate("() => window.prksWorkspaceSnapshot().tabs[1].id")
        self.assertIsNone(page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree"))

        b_box = _tab_box(page, b_id)
        canvas = page.locator(".prks-workspace-canvas").bounding_box()
        drop_x = canvas["x"] + canvas["width"] * 0.85
        drop_y = canvas["y"] + canvas["height"] / 2
        _pointer_drag(page, _tab_grab_point(b_box), (drop_x, drop_y))

        page.wait_for_function(
            """(id) => {
                const snap = window.prksWorkspaceSnapshot();
                return !!(snap.secondaryTree && snap.secondaryTree.type === 'leaf' && snap.secondaryTree.tabId === id);
            }""",
            arg=b_id,
        )
        _assert_no_drag_residue(self, page, "after creating the first Secondary via drag")
        snap = page.evaluate("() => window.prksWorkspaceSnapshot()")
        self.assertEqual(len(snap["tabs"]), 2, "no duplicate logical tab was created")
        self.assertEqual(snap["focusedTabId"], b_id)
        self.assertNotEqual(snap["mainTabId"], b_id)
        self.assertEqual(page.evaluate("() => location.hash"), work_hash)
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 2)
        self.assertEqual(page.locator(".prks-tile").count(), 2)

    def test_drag_parked_tab_splits_below_existing_leaf(self):
        server, page, _collector = self._start_app(seed_fn=seed_graph_context_library)
        page.set_viewport_size({"width": 1600, "height": 900})
        position_id = server.ids["position"]
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        page.evaluate(
            """(id) => window.prksNavigate('#/people/' + id, { target: 'tile' })""",
            arg=server.ids["person"],
        )
        page.wait_for_function(
            "() => window.prksWorkspaceSnapshot().secondaryTree && window.prksWorkspaceSnapshot().secondaryTree.type === 'leaf'"
        )
        b_id = page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree.tabId")
        c_tab = page.evaluate(
            """(a) => window.prksWorkspaceSplitLeaf(a.target, 'left-right', { hash: '#/positions/' + a.position })""",
            arg={"target": b_id, "position": position_id},
        )
        c_id = c_tab["id"]
        page.wait_for_function("() => window.prksTabContextDebugSnapshot().mountedCount === 3")
        page.evaluate(
            """(id) => window.prksNavigate('#/works/' + id, { target: 'new-tab', activate: false })""",
            arg=work_b,
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().tabs.length === 4")
        d_id = page.evaluate("() => window.prksWorkspaceSnapshot().tabs[3].id")

        page.evaluate(
            """(ids) => {
                window.__prksSplitRt = {
                    bCtx: window.prksGetTabContext(ids.b),
                    cCtx: window.prksGetTabContext(ids.c),
                };
            }""",
            arg={"b": b_id, "c": c_id},
        )

        d_box = _tab_box(page, d_id)
        c_box = _tile_box(page, c_id)
        _pointer_drag(page, _tab_grab_point(d_box), _edge_point(c_box, "below"))

        page.wait_for_function("() => window.prksTabContextDebugSnapshot().mountedCount === 4")
        _assert_no_drag_residue(self, page, "after a parked-tab nested split via drag")

        shape = page.evaluate(
            """(ids) => {
                const t = window.prksWorkspaceSnapshot().secondaryTree;
                if (!t || t.type !== 'split' || t.axis !== 'left-right') return { ok: false };
                if (!t.first || t.first.tabId !== ids.b) return { ok: false, step: 'first' };
                const inner = t.second;
                if (!inner || inner.type !== 'split' || inner.axis !== 'top-bottom') return { ok: false, step: 'inner-axis' };
                return {
                    ok: inner.first && inner.first.tabId === ids.c && inner.second && inner.second.tabId === ids.d,
                    innerFirst: inner.first && inner.first.tabId,
                    innerSecond: inner.second && inner.second.tabId,
                };
            }""",
            arg={"b": b_id, "c": c_id, "d": d_id},
        )
        self.assertTrue(shape["ok"], "expected B | (C over D): " + repr(shape))

        snap = page.evaluate("() => window.prksWorkspaceSnapshot()")
        self.assertEqual(snap["focusedTabId"], d_id)
        self.assertEqual(len(snap["tabs"]), 4, "no duplicate logical tab was created")

        identity = page.evaluate(
            """(ids) => ({
                bSame: window.prksGetTabContext(ids.b) === window.__prksSplitRt.bCtx,
                cSame: window.prksGetTabContext(ids.c) === window.__prksSplitRt.cCtx,
            })""",
            arg={"b": b_id, "c": c_id},
        )
        self.assertTrue(identity["bSame"])
        self.assertTrue(identity["cSame"])

    def test_drag_visible_pane_move_preserves_runtimes(self):
        server, page, _collector = self._start_app(seed_fn=seed_graph_context_library)
        page.set_viewport_size({"width": 1600, "height": 900})
        tree = _build_three_leaf_tree(page, server)  # Main A; Secondary B / (C | D)

        page.evaluate(
            """(ids) => {
                window.__prksMoveRt = {
                    aCtx: window.prksGetTabContext(ids.main),
                    bCtx: window.prksGetTabContext(ids.b),
                    cCtx: window.prksGetTabContext(ids.c),
                    dPdf: window.prksGetTabContext(ids.d).getResource('pdf'),
                    dNotes: window.prksGetTabContext(ids.d).getResource('workNotes'),
                };
            }""",
            arg={"main": tree["main_id"], "b": tree["b_id"], "c": tree["c_id"], "d": tree["d_id"]},
        )
        tree_before = page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree")

        dialogs = []
        page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        seen_gets = []
        page.on("request", lambda req: seen_gets.append(req.url) if req.method == "GET" else None)

        d_grip = _grip_box(page, tree["d_id"])
        b_box = _tile_box(page, tree["b_id"])
        _pointer_drag(page, _center(d_grip), _edge_point(b_box, "above"))

        page.wait_for_function(
            """(ids) => {
                const leaves = window.collectLeafTabIds(window.prksWorkspaceSnapshot().secondaryTree);
                return leaves.length === 3 && leaves[0] === ids.d;
            }""",
            arg={"d": tree["d_id"]},
        )
        _assert_no_drag_residue(self, page, "after a visible pane move via drag")
        self.assertEqual(dialogs, [], "a spatial pane move must not trigger a leave prompt")

        expected = page.evaluate(
            """(a) => {
                const moved = window.moveLeafRelativeToTarget(a.before, a.d, a.b, { axis: 'top-bottom', placement: 'first' });
                const strip = function (n) {
                    if (!n) return null;
                    if (n.type === 'leaf') return { type: 'leaf', tabId: n.tabId };
                    return { type: 'split', axis: n.axis, first: strip(n.first), second: strip(n.second) };
                };
                return strip(moved);
            }""",
            arg={"before": tree_before, "d": tree["d_id"], "b": tree["b_id"]},
        )
        actual = page.evaluate(
            """() => {
                const strip = function (n) {
                    if (!n) return null;
                    if (n.type === 'leaf') return { type: 'leaf', tabId: n.tabId };
                    return { type: 'split', axis: n.axis, first: strip(n.first), second: strip(n.second) };
                };
                return strip(window.prksWorkspaceSnapshot().secondaryTree);
            }"""
        )
        self.assertEqual(actual, expected, "drag-driven move must match moveLeafRelativeToTarget(D, B, above)")

        identity = page.evaluate(
            """(ids) => ({
                aSame: window.prksGetTabContext(ids.main) === window.__prksMoveRt.aCtx,
                bSame: window.prksGetTabContext(ids.b) === window.__prksMoveRt.bCtx,
                cSame: window.prksGetTabContext(ids.c) === window.__prksMoveRt.cCtx,
                dPdfSame: window.prksGetTabContext(ids.d).getResource('pdf') === window.__prksMoveRt.dPdf,
                dNotesSame: window.prksGetTabContext(ids.d).getResource('workNotes') === window.__prksMoveRt.dNotes,
                mounted: window.prksTabContextDebugSnapshot().mountedCount,
            })""",
            arg={"main": tree["main_id"], "b": tree["b_id"], "c": tree["c_id"], "d": tree["d_id"]},
        )
        self.assertTrue(identity["aSame"])
        self.assertTrue(identity["bSame"])
        self.assertTrue(identity["cSame"])
        self.assertTrue(identity["dPdfSame"], "moving a pane must not reload its PDF")
        self.assertTrue(identity["dNotesSame"], "moving a pane must not remount its notes editor")
        self.assertEqual(identity["mounted"], 4, "moving a visible pane must not mount/unmount anything")
        for path in ("/api/works/", "/api/persons/", "/api/positions/", "/api/pdfs/"):
            self.assertFalse(any(path in u for u in seen_gets), "pane move must not fetch: " + path)

    def test_drag_global_tab_of_visible_secondary_also_moves_it(self):
        server, page, _collector = self._start_app(seed_fn=seed_graph_context_library)
        page.set_viewport_size({"width": 1600, "height": 900})
        person_id = server.ids["person"]
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        page.evaluate(
            """(id) => window.prksNavigate('#/people/' + id, { target: 'tile' })""",
            arg=person_id,
        )
        page.wait_for_function(
            "() => window.prksWorkspaceSnapshot().secondaryTree && window.prksWorkspaceSnapshot().secondaryTree.type === 'leaf'"
        )
        b_id = page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree.tabId")
        c_tab = page.evaluate(
            """(a) => window.prksWorkspaceSplitLeaf(a.target, 'left-right', { hash: '#/positions/' + a.position })""",
            arg={"target": b_id, "position": server.ids["position"]},
        )
        c_id = c_tab["id"]
        page.wait_for_function("() => window.prksTabContextDebugSnapshot().mountedCount === 3")
        b_ctx_before = page.evaluate("(id) => !!window.prksGetTabContext(id)", arg=b_id)
        self.assertTrue(b_ctx_before)

        # Drag C's GLOBAL workspace tab (not its pane grip) onto B's left edge.
        c_tab_box = _tab_box(page, c_id)
        b_tile_box = _tile_box(page, b_id)
        _pointer_drag(page, _tab_grab_point(c_tab_box), _edge_point(b_tile_box, "left"))

        page.wait_for_function(
            """(ids) => {
                const t = window.prksWorkspaceSnapshot().secondaryTree;
                return !!(t && t.type === 'split' && t.first && t.first.tabId === ids.c && t.second && t.second.tabId === ids.b);
            }""",
            arg={"c": c_id, "b": b_id},
        )
        _assert_no_drag_residue(self, page, "after a global-tab-driven pane move")
        snap = page.evaluate("() => window.prksWorkspaceSnapshot()")
        self.assertEqual(len(page.evaluate("() => window.collectLeafTabIds(window.prksWorkspaceSnapshot().secondaryTree)")), 2)
        self.assertEqual(len(snap["tabs"]), 3, "no duplicate leaf/tab was created")
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 3)

    def test_drag_pane_to_tab_strip_parks_it(self):
        server, page, _collector = self._start_app(seed_fn=seed_graph_context_library)
        page.set_viewport_size({"width": 1600, "height": 900})
        tree = _build_three_leaf_tree(page, server)  # Main A; Secondary B / (C | D)

        page.evaluate(
            """(ids) => {
                window.__prksParkRt = {
                    bCtx: window.prksGetTabContext(ids.b),
                    cCtx: window.prksGetTabContext(ids.c),
                };
            }""",
            arg={"b": tree["b_id"], "c": tree["c_id"]},
        )

        d_grip = _grip_box(page, tree["d_id"])
        strip = page.locator("#prks-workspace-tabs").bounding_box()
        _pointer_drag(page, _center(d_grip), _center(strip))

        page.wait_for_function("() => window.prksTabContextDebugSnapshot().mountedCount === 3")
        _assert_no_drag_residue(self, page, "after parking a pane via drag")

        snap = page.evaluate("() => window.prksWorkspaceSnapshot()")
        leaves = page.evaluate("() => window.collectLeafTabIds(window.prksWorkspaceSnapshot().secondaryTree)")
        self.assertEqual(sorted(leaves), sorted([tree["b_id"], tree["c_id"]]))
        self.assertTrue(any(t["id"] == tree["d_id"] for t in snap["tabs"]), "D's logical tab remains open")
        self.assertTrue(
            page.locator('.prks-workspace-tab[data-tab-id="%s"].is-parked' % tree["d_id"]).count() == 1
        )
        identity = page.evaluate(
            """(ids) => {
                const dCtx = window.prksGetTabContext(ids.d);
                return {
                    bSame: window.prksGetTabContext(ids.b) === window.__prksParkRt.bCtx,
                    cSame: window.prksGetTabContext(ids.c) === window.__prksParkRt.cCtx,
                    dUnmounted: !dCtx || dCtx.mounted === false,
                };
            }""",
            arg={"b": tree["b_id"], "c": tree["c_id"], "d": tree["d_id"]},
        )
        self.assertTrue(identity["bSame"])
        self.assertTrue(identity["cSame"])
        self.assertTrue(
            identity["dUnmounted"], "D's TabContext must actually unmount (park), not just detach visually"
        )

    def test_drag_park_rejected_by_leave_guard_is_atomic(self):
        server, page, _collector = self._start_app(seed_fn=seed_graph_context_library)
        page.set_viewport_size({"width": 1600, "height": 900})
        tree = _build_three_leaf_tree(page, server)  # Main A; Secondary B / (C | D)
        tree_before = page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree")
        before = page.evaluate("() => window.prksWorkspaceSnapshot()")

        dialogs = []

        def on_dialog(dialog):
            dialogs.append(dialog.message)
            dialog.dismiss()

        page.on("dialog", on_dialog)
        page.evaluate(
            """(dId) => {
                const ctx = window.prksGetTabContext(dId);
                window.prksHasPendingWorkAnnotationSync = function (c) {
                    return !!(c && ctx && c.tabId === ctx.tabId);
                };
            }""",
            arg=tree["d_id"],
        )

        d_grip = _grip_box(page, tree["d_id"])
        strip = page.locator("#prks-workspace-tabs").bounding_box()
        _pointer_drag(page, _center(d_grip), _center(strip))

        page.wait_for_function("() => document.querySelectorAll('.prks-drag-preview').length === 0")
        self.assertEqual(len(dialogs), 1)
        _assert_no_drag_residue(self, page, "after a rejected park")

        after = page.evaluate("() => window.prksWorkspaceSnapshot()")
        self.assertEqual(after["secondaryTree"], tree_before, "tree must be exactly unchanged on a rejected park")
        self.assertEqual(after["focusedTabId"], before["focusedTabId"])
        self.assertEqual([t["id"] for t in after["tabs"]], [t["id"] for t in before["tabs"]])
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 4)
        self.assertIsNotNone(page.evaluate("(id) => window.prksGetTabContext(id)", arg=tree["d_id"]))

    def test_drag_pane_cap_blocks_addition_but_allows_move(self):
        server, page, _collector = self._start_app(seed_fn=seed_graph_context_library)
        # Wide enough that all 5 workspace tabs (Main + 3 Secondary + the parked cap-test tab)
        # stay unclipped in the strip -- this test drags the tab itself, not just its pane.
        page.set_viewport_size({"width": 2400, "height": 900})
        tree = _build_three_leaf_tree(page, server)  # 1 Main + 3 Secondary already mounted
        self.assertFalse(page.evaluate("() => window.prksWorkspaceCanAddSecondaryLeaf()"))
        # Use a tile-capable route (an Argument) so only the CAP -- not route eligibility -- is
        # under test.
        page.evaluate(
            """(id) => window.prksNavigate('#/arguments/' + id, { target: 'new-tab', activate: false })""",
            arg=server.ids["argument"],
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().tabs.length === 5")
        e_id = page.evaluate("() => window.prksWorkspaceSnapshot().tabs[4].id")

        e_box = _tab_box(page, e_id)
        b_box = _tile_box(page, tree["b_id"])
        overlay_class_at_hover = {}

        def check_overlay():
            overlay_class_at_hover["cls"] = page.evaluate(
                """() => {
                    const el = document.getElementById('prks-drag-edge-overlay');
                    return el ? el.className : null;
                }"""
            )

        _pointer_drag(page, _tab_grab_point(e_box), _edge_point(b_box, "right"), pre_release=check_overlay)
        self.assertIsNotNone(overlay_class_at_hover.get("cls"))
        self.assertIn("is-invalid", overlay_class_at_hover["cls"], "cap must be visibly invalid while hovering")

        page.wait_for_function("() => document.querySelectorAll('.prks-drag-preview').length === 0")
        _assert_no_drag_residue(self, page, "after a capped drop attempt")
        leaves_after = page.evaluate("() => window.collectLeafTabIds(window.prksWorkspaceSnapshot().secondaryTree)")
        self.assertEqual(sorted(leaves_after), sorted([tree["b_id"], tree["c_id"], tree["d_id"]]))
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 4)
        self.assertTrue(
            page.locator('.prks-workspace-tab[data-tab-id="%s"].is-parked' % e_id).count() == 1,
            "E must remain parked, the cap must block the addition",
        )

        # Rearranging an ALREADY-VISIBLE pane remains allowed at the cap.
        d_grip = _grip_box(page, tree["d_id"])
        _pointer_drag(page, _center(d_grip), _edge_point(b_box, "above"))
        page.wait_for_function(
            """(ids) => window.collectLeafTabIds(window.prksWorkspaceSnapshot().secondaryTree)[0] === ids.d""",
            arg={"d": tree["d_id"]},
        )
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 4)

    def test_drag_invalid_targets(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        person_id = server.ids["person"]
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        main_id = page.evaluate("() => window.prksWorkspaceSnapshot().mainTabId")

        # A. Dragging a parked tab over Main has no valid Secondary drop.
        page.evaluate(
            """(id) => window.prksNavigate('#/people/' + id, { target: 'new-tab', activate: false })""",
            arg=person_id,
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().tabs.length === 2")
        b_id = page.evaluate("() => window.prksWorkspaceSnapshot().tabs[1].id")
        before_hash = page.evaluate("() => location.hash")
        b_box = _tab_box(page, b_id)
        main_tile = _tile_box(page, main_id)
        _pointer_drag(page, _tab_grab_point(b_box), _center(main_tile))
        page.wait_for_function("() => document.querySelectorAll('.prks-drag-preview').length === 0")
        _assert_no_drag_residue(self, page, "after dragging over Main")
        self.assertIsNone(page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree"))
        self.assertEqual(page.evaluate("() => location.hash"), before_hash)
        self.assertTrue(page.locator('.prks-workspace-tab[data-tab-id="%s"].is-parked' % b_id).count() == 1)

        # Build a Secondary leaf reusing B's exact tab id (not a duplicate) so a self-drop can
        # be attempted next.
        page.evaluate("(id) => window.prksWorkspaceTileTab(id)", arg=b_id)
        page.wait_for_function(
            """(id) => {
                const t = window.prksWorkspaceSnapshot().secondaryTree;
                return !!(t && t.type === 'leaf' && t.tabId === id);
            }""",
            arg=b_id,
        )
        tree_snap = page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree")

        # B. Self-drop: dragging B's own pane grip over B's own tile must never mutate.
        b_grip = _grip_box(page, tree_snap["tabId"])
        b_tile = _tile_box(page, tree_snap["tabId"])
        _pointer_drag(page, _center(b_grip), _center(b_tile))
        page.wait_for_function("() => document.querySelectorAll('.prks-drag-preview').length === 0")
        _assert_no_drag_residue(self, page, "after a self-drop attempt")
        self.assertEqual(page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree"), tree_snap)
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 2)

        # C. Unsupported (non-tile-capable) parked route cannot enter Secondary.
        page.evaluate("() => window.prksNavigate('#/folders', { target: 'new-tab', activate: false })")
        page.wait_for_function("() => window.prksWorkspaceSnapshot().tabs.length === 3")
        folders_id = page.evaluate("() => window.prksWorkspaceSnapshot().tabs[2].id")
        folders_box = _tab_box(page, folders_id)
        sec_tile = _tile_box(page, tree_snap["tabId"])
        _pointer_drag(page, _center(folders_box), _edge_point(sec_tile, "right"))
        page.wait_for_function("() => document.querySelectorAll('.prks-drag-preview').length === 0")
        _assert_no_drag_residue(self, page, "after dragging an unsupported route toward Secondary")
        leaves = page.evaluate("() => window.collectLeafTabIds(window.prksWorkspaceSnapshot().secondaryTree)")
        self.assertNotIn(folders_id, leaves)
        self.assertTrue(page.locator('.prks-workspace-tab[data-tab-id="%s"].is-parked' % folders_id).count() == 1)

    def test_drag_escape_cancellation_preserves_state_and_click_still_works(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        person_id = server.ids["person"]
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        page.evaluate(
            """(id) => window.prksNavigate('#/people/' + id, { target: 'new-tab', activate: false })""",
            arg=person_id,
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().tabs.length === 2")
        before = page.evaluate("() => window.prksWorkspaceSnapshot()")
        b_id = before["tabs"][1]["id"]

        canvas = page.locator(".prks-workspace-canvas").bounding_box()
        b_box = _tab_box(page, b_id)
        _begin_pointer_drag(page, _center(b_box))
        page.mouse.move(canvas["x"] + canvas["width"] * 0.85, canvas["y"] + canvas["height"] / 2, steps=10)
        page.wait_for_selector("#prks-drag-empty-overlay")
        page.keyboard.press("Escape")
        page.mouse.up()  # the drag already ended; this mouseup must be inert

        page.wait_for_function("() => document.querySelectorAll('.prks-drag-preview').length === 0")
        _assert_no_drag_residue(self, page, "after Escape cancellation")
        after = page.evaluate("() => window.prksWorkspaceSnapshot()")
        self.assertEqual([t["id"] for t in after["tabs"]], [t["id"] for t in before["tabs"]])
        self.assertEqual(after["mainTabId"], before["mainTabId"])
        self.assertEqual(after["focusedTabId"], before["focusedTabId"])
        self.assertIsNone(after["secondaryTree"])

        # A normal click on the cancelled drag's source must not be swallowed by leftover
        # click-suppression state (spec item 4).
        page.locator('.prks-workspace-tab[data-tab-id="%s"]' % b_id).click()
        page.wait_for_function(
            "(id) => window.prksWorkspaceSnapshot().mainTabId === id",
            arg=b_id,
        )

    def test_drag_click_after_real_drop_is_suppressed_once(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        person_id = server.ids["person"]
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        page.evaluate(
            """(id) => window.prksNavigate('#/people/' + id, { target: 'new-tab', activate: false })""",
            arg=person_id,
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().tabs.length === 2")
        main_id_before = page.evaluate("() => window.prksWorkspaceSnapshot().mainTabId")
        b_id = page.evaluate("() => window.prksWorkspaceSnapshot().tabs[1].id")

        # A real drag that crosses the movement threshold but releases back over its own tab
        # wrap -- mousedown and mouseup target the same element, exactly the case click
        # suppression exists for: a drag happened, so the browser's own synthesized click on
        # that same element (which would otherwise activate a parked tab as Main) must not fire.
        start = _center(_tab_box(page, b_id))
        page.mouse.move(*start)
        page.mouse.down()
        page.mouse.move(start[0] + 10, start[1] - 10, steps=4)
        page.mouse.move(start[0], start[1], steps=4)
        page.mouse.up()

        page.wait_for_function("() => document.querySelectorAll('.prks-drag-preview').length === 0")
        page.wait_for_timeout(50)
        self.assertEqual(
            page.evaluate("() => window.prksWorkspaceSnapshot().mainTabId"),
            main_id_before,
            "the pointerup-synthesized click on the drag source must not also activate it",
        )

    def test_drag_responsive_cancellation_before_narrow_fallback(self):
        server, page, _collector = self._start_app(seed_fn=seed_graph_context_library)
        page.set_viewport_size({"width": 1600, "height": 900})
        person_id = server.ids["person"]
        position_id = server.ids["position"]
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        page.evaluate("(id) => window.prksNavigate('#/people/' + id, { target: 'tile' })", arg=person_id)
        page.wait_for_function(
            "() => window.prksWorkspaceSnapshot().secondaryTree && window.prksWorkspaceSnapshot().secondaryTree.type === 'leaf'"
        )
        b_id = page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree.tabId")
        c_tab = page.evaluate(
            """(a) => window.prksWorkspaceSplitLeaf(a.target, 'left-right', { hash: '#/positions/' + a.position })""",
            arg={"target": b_id, "position": position_id},
        )
        c_id = c_tab["id"]
        page.wait_for_function("() => window.prksTabContextDebugSnapshot().mountedCount === 3")

        c_grip = _grip_box(page, c_id)
        b_tile = _tile_box(page, b_id)
        _begin_pointer_drag(page, _center(c_grip))
        left = _edge_point(b_tile, "left")
        page.mouse.move(left[0], left[1], steps=10)
        page.wait_for_selector("#prks-drag-edge-overlay")

        # Cross into narrow fallback WHILE the drag is still held.
        page.set_viewport_size({"width": 500, "height": 900})
        page.wait_for_function("() => window.prksWorkspaceSnapshot().mode !== 'tiled' || window.prksWorkspaceVisualTiled() === false")

        _assert_no_drag_residue(self, page, "drag must be cancelled before the narrow transition completes")
        page.mouse.up()  # the drag already ended; this mouseup must be inert
        _assert_no_drag_residue(self, page, "still clean after the now-inert mouseup")

        leaves = page.evaluate("() => window.collectLeafTabIds(window.prksWorkspaceSnapshot().secondaryTree)")
        self.assertEqual(sorted(leaves), sorted([b_id, c_id]), "responsive cancellation must not mutate the tree")

        page.set_viewport_size({"width": 1600, "height": 900})
        page.wait_for_function("() => window.prksWorkspaceVisualTiled() === true")
        self.assertEqual(page.evaluate("() => window.prksTabContextDebugSnapshot().mountedCount"), 3)

    def test_drag_cancel_active_drag_cleans_up_without_mutation(self):
        """Covers the pointercancel/lostpointercapture paths: both handlers simply call the
        same cancel() this test invokes directly (workspace-drag.js exports it specifically so
        other lifecycle code can call it defensively -- see prksWorkspaceCancelActiveDrag's own
        callers in workspace-tiling.js). A literal browser pointercancel/lostpointercapture
        event is not reliably synthesizable through Playwright's mouse API, which always
        completes a normal gesture."""
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        person_id = server.ids["person"]
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        page.evaluate(
            """(id) => window.prksNavigate('#/people/' + id, { target: 'new-tab', activate: false })""",
            arg=person_id,
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().tabs.length === 2")
        before = page.evaluate("() => window.prksWorkspaceSnapshot()")
        b_id = before["tabs"][1]["id"]

        b_box = _tab_box(page, b_id)
        _begin_pointer_drag(page, _center(b_box))
        page.mouse.move(b_box["x"] + 200, b_box["y"], steps=10)
        page.wait_for_selector(".prks-drag-preview")

        page.evaluate("() => window.prksWorkspaceCancelActiveDrag()")
        _assert_no_drag_residue(self, page, "after a defensive cancel mid-drag")
        page.mouse.up()  # inert: pending is already null
        _assert_no_drag_residue(self, page, "still clean after the now-inert mouseup")

        after = page.evaluate("() => window.prksWorkspaceSnapshot()")
        self.assertEqual([t["id"] for t in after["tabs"]], [t["id"] for t in before["tabs"]])
        self.assertEqual(after["mainTabId"], before["mainTabId"])

    def test_drag_tab_strip_autoscroll(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1100, "height": 900})
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        _wait_pdf_viewer(page)
        page.evaluate(
            """(id) => {
                const jobs = [];
                for (let i = 0; i < 14; i++) {
                    jobs.push(window.prksNavigate('#/works/' + id, { target: 'new-tab', activate: false }));
                }
                return Promise.all(jobs);
            }""",
            arg=work_b,
        )
        page.wait_for_function("() => document.querySelectorAll('.prks-workspace-tab').length >= 15")
        overflow = page.evaluate(
            """() => {
                const list = document.getElementById('prks-workspace-tabs');
                return !!(list && list.scrollWidth > list.clientWidth + 2);
            }"""
        )
        self.assertTrue(overflow)

        first_id = page.evaluate("() => window.prksWorkspaceSnapshot().tabs[0].id")
        last_id = page.evaluate("() => window.prksWorkspaceSnapshot().tabs.slice(-1)[0].id")
        strip = page.locator("#prks-workspace-tabs").bounding_box()
        scroll_before = page.evaluate("() => document.getElementById('prks-workspace-tabs').scrollLeft")

        first_box = _tab_box(page, first_id)
        _begin_pointer_drag(page, _center(first_box))
        # Hold near the right edge of the strip long enough for several autoscroll frames.
        edge_x = strip["x"] + strip["width"] - 10
        edge_y = strip["y"] + strip["height"] / 2
        page.mouse.move(edge_x, edge_y, steps=5)
        page.wait_for_function(
            "(before) => document.getElementById('prks-workspace-tabs').scrollLeft > before",
            arg=scroll_before,
        )
        scroll_mid = page.evaluate("() => document.getElementById('prks-workspace-tabs').scrollLeft")
        self.assertGreater(scroll_mid, scroll_before)

        # Keep holding at the edge until autoscroll has actually brought the last tab into
        # view (a handful of RAF frames is not enough by itself for a strip this long).
        page.wait_for_function(
            """() => {
                const l = document.getElementById('prks-workspace-tabs');
                return l.scrollLeft >= l.scrollWidth - l.clientWidth - 5;
            }""",
            timeout=15000,
        )

        # Drop onto the last tab, now scrolled into view under the still-stationary pointer.
        last_box_now = _tab_box(page, last_id)
        page.mouse.move(last_box_now["x"] + last_box_now["width"] - 4, last_box_now["y"] + last_box_now["height"] / 2, steps=6)
        page.mouse.up()

        page.wait_for_function(
            """(id) => window.prksWorkspaceSnapshot().tabs.slice(-1)[0].id === id""",
            arg=first_id,
        )
        _assert_no_drag_residue(self, page, "after an autoscrolled reorder")

        # Autoscroll must actually stop once the drag ends (no further scrollLeft growth).
        scroll_after_drop = page.evaluate("() => document.getElementById('prks-workspace-tabs').scrollLeft")
        page.wait_for_timeout(200)
        self.assertEqual(
            page.evaluate("() => document.getElementById('prks-workspace-tabs').scrollLeft"),
            scroll_after_drop,
            "autoscroll RAF must stop after drop",
        )

    def test_move_tab_context_menu_left_right(self):
        server, page, _collector = self._start_app()
        page.set_viewport_size({"width": 1600, "height": 900})
        work_b = server.ids["work_b"]
        _open_work_from_home(page, WORK_A_TITLE)
        page.evaluate(
            """(id) => window.prksNavigate('#/works/' + id, { target: 'new-tab', activate: false })""",
            arg=work_b,
        )
        page.wait_for_function("() => window.prksWorkspaceSnapshot().tabs.length === 2")
        a_id, b_id = [t["id"] for t in page.evaluate("() => window.prksWorkspaceSnapshot().tabs")]

        # Main (A, idx 0): "Move tab left" must be disabled at the start.
        page.locator('.prks-workspace-tab[data-tab-id="%s"]' % a_id).click(button="right")
        page.wait_for_selector("#prks-workspace-menu:not([hidden])")
        left_item = page.locator("#prks-workspace-menu .prks-workspace-menu__item", has_text="Move tab left")
        self.assertEqual(left_item.count(), 1)
        self.assertTrue(left_item.get_attribute("aria-disabled") == "true" or left_item.is_disabled())
        page.keyboard.press("Escape")
        page.wait_for_selector("#prks-workspace-menu[hidden]", state="attached")

        # Parked (B, idx 1): "Move tab right" must be disabled at the end; "Move tab left" works.
        page.locator('.prks-workspace-tab[data-tab-id="%s"]' % b_id).click(button="right")
        page.wait_for_selector("#prks-workspace-menu:not([hidden])")
        right_item = page.locator("#prks-workspace-menu .prks-workspace-menu__item", has_text="Move tab right")
        self.assertTrue(right_item.get_attribute("aria-disabled") == "true" or right_item.is_disabled())
        page.locator("#prks-workspace-menu .prks-workspace-menu__item", has_text="Move tab left").click()
        page.wait_for_function(
            "(ids) => window.prksWorkspaceSnapshot().tabs.map(t => t.id).join(',') === ids",
            arg=",".join([b_id, a_id]),
        )
        self.assertEqual(page.evaluate("() => window.prksWorkspaceSnapshot().mainTabId"), a_id)

        # Existing non-drag Split/Hide/Make main workflows remain available alongside drag.
        page.locator('.prks-workspace-tab[data-tab-id="%s"]' % a_id).click(button="right")
        page.wait_for_selector("#prks-workspace-menu:not([hidden])")
        labels = page.locator("#prks-workspace-menu .prks-workspace-menu__item").all_text_contents()
        self.assertTrue(any("split view" in t for t in labels), labels)
        page.keyboard.press("Escape")


