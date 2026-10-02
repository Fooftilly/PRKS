"""Structural + Node regressions for the Research Graph UI."""
import os
import shutil
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_INDEX = os.path.join(_FRONTEND, "index.html")
_NAV = os.path.join(_FRONTEND, "js", "navigation.js")
_APP = os.path.join(_FRONTEND, "js", "app.js")
_API = os.path.join(_FRONTEND, "js", "api.js")
_PALETTE = os.path.join(_FRONTEND, "js", "command-palette.js")
_GRAPH = os.path.join(_FRONTEND, "js", "components", "research-graph.js")
_CONCEPTS = os.path.join(_FRONTEND, "js", "components", "concepts.js")
_CONCEPTS_VUE_DETAIL = os.path.join(
    _PROJECT_DIR, "frontend-app", "src", "features", "concepts", "ConceptDetailRoute.vue"
)
_CONCEPTS_VUE_INTENTS = os.path.join(
    _PROJECT_DIR, "frontend-app", "src", "features", "concepts", "intents.ts"
)
_POSITIONS_VUE_DETAIL = os.path.join(
    _PROJECT_DIR, "frontend-app", "src", "features", "positions", "PositionDetailRoute.vue"
)
_POSITIONS_VUE_INTENTS = os.path.join(
    _PROJECT_DIR, "frontend-app", "src", "features", "positions", "intents.ts"
)
_ARGS = os.path.join(
    _PROJECT_DIR, "frontend-app", "src", "features", "arguments", "ArgumentDetailRoute.vue"
)
_PEOPLE = os.path.join(_FRONTEND, "js", "components", "people.js")
_VENDOR = os.path.join(_FRONTEND, "vendor", "cytoscape")
_CSS = os.path.join(_FRONTEND, "css", "style.css")
_WIKI_RESEARCH = os.path.join(_PROJECT_DIR, "docs", "wiki", "Research-Network.md")
_AGENTS = os.path.join(_PROJECT_DIR, "frontend", "AGENTS.md")
_SCHEMA = os.path.join(_PROJECT_DIR, "backend", "db_migrations.py")
_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_research_graph_selftest.js")
_GRAPH_VUE = os.path.join(
    _PROJECT_DIR, "frontend-app", "src", "features", "research-graph", "ResearchGraphRoute.vue"
)
_GRAPH_SESSION = os.path.join(
    _PROJECT_DIR, "frontend-app", "src", "features", "research-graph", "session.ts"
)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendResearchGraphTests(unittest.TestCase):
    def test_local_cytoscape_pin(self):
        import json

        with open(
            os.path.join(_PROJECT_DIR, "tools", "research-graph", "package.json"),
            encoding="utf-8",
        ) as fh:
            want = json.load(fh)["dependencies"]["cytoscape"]
        js = os.path.join(_VENDOR, "cytoscape.min.js")
        version = _read(os.path.join(_VENDOR, "VERSION"))
        license_txt = _read(os.path.join(_VENDOR, "LICENSE"))
        self.assertTrue(os.path.isfile(js))
        self.assertGreater(os.path.getsize(js), 10000)
        self.assertIn(want, version)
        self.assertIn("cytoscape.js", version)
        self.assertIn("sha256:", version)
        self.assertIn("MIT", license_txt)
        self.assertNotIn("cdn.jsdelivr.net", version)
        self.assertNotIn("unpkg.com", version)
        self.assertNotIn("fetched:", version.lower())

    def test_index_loads_local_cytoscape_before_graph(self):
        html = _read(_INDEX)
        cy = html.find('src="/vendor/cytoscape/cytoscape.min.js"')
        graph = html.find('src="/js/components/research-graph.js"')
        app = html.find('src="/js/app.js"')
        self.assertNotEqual(cy, -1)
        self.assertNotEqual(graph, -1)
        self.assertLess(cy, graph)
        self.assertLess(graph, app)
        self.assertNotIn("cdn.jsdelivr.net/npm/cytoscape", html)
        self.assertIn('href="#/graph"', html)
        self.assertIn("Graph", html)

    def test_no_cdn_cytoscape_in_graph_module(self):
        src = _read(_GRAPH)
        self.assertNotIn("cdn.jsdelivr.net", src)
        self.assertNotIn("unpkg.com", src)
        self.assertNotIn("https://cdn", src)
        self.assertIn("function renderResearchGraph", src)
        self.assertIn("function destroyResearchGraph", src)
        self.assertIn("peopleRequiredForFocus", src)
        self.assertIn("reloadGraphFailureMessage", src)
        self.assertIn("startsWith('person:')", src)
        self.assertIn("reloadGeneration", src)
        self.assertIn("graphReloadIsStale", src)
        self.assertIn(".destroy()", src)
        self.assertIn("__prksResearchGraphLiveCount", src)
        self.assertIn("display", src)
        self.assertIn("cose", src)
        self.assertIn("Mentioned in research notes", src)
        self.assertIn("Made/taken in", src)
        self.assertIn("prksNavigate", src)
        self.assertNotIn("fcose", src)
        self.assertNotIn("dagre", src)

    def test_route_is_real(self):
        nav = _read(_NAV)
        app = _read(_APP)
        graph = _read(_GRAPH)
        self.assertIn("research-graph", nav)
        self.assertIn("navHref: '#/graph'", nav)
        self.assertIn("prksParseGraphFocus", nav)
        self.assertIn("prksGraphCanonical", nav)
        self.assertIn("person):[A-Za-z0-9]", nav)
        self.assertIn("case 'research-graph':", app)
        self.assertNotIn("route.name === 'graph' || route.canonicalize", app)
        self.assertIn("destroyResearchGraph", graph)
        self.assertIn("prksResearchRouteForcesOpen", nav)
        self.assertIn("route.name === 'research-graph'", nav)

    def test_view_in_graph_actions(self):
        self.assertIn("prks-concept-view-graph", _read(_CONCEPTS_VUE_DETAIL))
        self.assertIn("prks-position-view-graph", _read(_POSITIONS_VUE_DETAIL))
        self.assertIn("prksGraphFocusHash", _read(_POSITIONS_VUE_INTENTS))
        self.assertIn("prks-arg-view-graph", _read(_ARGS))
        self.assertIn("prks-person-view-graph", _read(_PEOPLE))
        self.assertIn("prksGraphFocusHash", _read(_CONCEPTS_VUE_INTENTS))
        self.assertIn("prksGraphFocusHash('person'", _read(_PEOPLE))
        self.assertIn("navigate-research-graph", _read(_PALETTE))
        self.assertIn("View this record in graph", _read(_PALETTE))
        self.assertIn("fetchResearchGraph", _read(_API))
        self.assertIn("/api/research-graph", _read(_API))

    def test_person_profile_hierarchy(self):
        people = _read(_PEOPLE)
        sidebar = people.split("function renderPersonProfileDetailsSidebarHtml", 1)[1].split(
            "function renderPersonProfileEditFormHtml", 1
        )[0]
        self.assertNotIn("Biography, portrait, and external links are in the main column.", sidebar)
        self.assertIn("prks-btn--primary", sidebar)
        self.assertIn("Edit using template", sidebar)
        self.assertIn("person-sidebar__advanced", sidebar)
        self.assertNotIn("Edit works", sidebar)
        self.assertIn("openPersonProfileEdit()", sidebar)
        self.assertIn("prks-person-view-graph", sidebar)
        detail = people.split("function prksPersonViewRecord", 1)[1].split(
            "function prksRefreshPersonDetailMain", 1
        )[0]
        vue = _read(os.path.join(
            _PROJECT_DIR, "frontend-app", "src", "features", "people", "PersonDetailRoute.vue"
        ))
        self.assertIn("person-profile__summary", vue)
        self.assertIn("person-profile__about", vue)
        self.assertIn("person-profile__works-head", vue)
        self.assertIn("prksUniquePersonWorks", detail)
        self.assertIn("prksPersonWorkRolesById", detail)
        self.assertIn("worksEditing ? (person.works || []) : prksUniquePersonWorks(person)", detail)
        self.assertNotIn("acc[role].push(w)", detail)
        self.assertIn("prks-people-list__lifespan", people)
        self.assertNotIn("/api/persons/", people.split("function buildPersonListRowHtml", 1)[1].split("window.buildPersonListRowHtml", 1)[0])
        self.assertIn("LATEST_SCHEMA_VERSION = 17", _read(_SCHEMA))

    def test_docs(self):
        wiki = _read(_WIKI_RESEARCH)
        agents = _read(_AGENTS)
        self.assertIn("## Research Graph", wiki)
        self.assertIn("read-only", wiki.lower())
        self.assertIn("[[concept:", wiki)
        self.assertIn("read-only derived projection", agents)
        self.assertIn("namespaced by entity type", agents)
        self.assertIn("Do not add canonical graph persistence", agents)

    def test_css_graph_layout(self):
        css = _read(_CSS)
        self.assertIn(".research-graph", css)
        self.assertIn(".research-graph__inspector", css)
        self.assertIn(".research-graph__legend", css)
        self.assertIn(".prks-filter-toggle", css)
        graph = _read(_GRAPH)
        self.assertIn("'text-rotation': 'none'", graph)
        self.assertNotIn("'text-rotation': 'autorotate'", graph)
        self.assertIn("canvasLabel", graph)
        self.assertIn("graph-dim", graph)
        self.assertIn("nodeDimensionsIncludeLabels: true", graph)
        self.assertIn('data-prks-role="graph-fit">Fit', graph)
        self.assertIn("legendIcon", graph)
        self.assertIn("legendIcon('network'", graph)
        self.assertIn("prksLucideSvgDataUri", graph)
        self.assertIn("background-image", graph)
        self.assertIn("background-clip", graph)
        self.assertIn("shape: 'ellipse'", graph)
        # The inspector is plain right-panel content, not a card-inside-panel: no
        # doc-meta-card wrapper around it any more (the host element already carries
        # right-panel-stack -- see ui.js assertions below).
        self.assertNotIn("doc-meta-card", graph)
        self.assertIn("renderGraphInspector", graph)
        self.assertNotIn('prks-panel__header">Inspector', graph)
        ui = _read(os.path.join(_FRONTEND, "js", "ui.js"))
        self.assertIn("case 'research-graph':", ui)
        self.assertIn("return 'graph'", ui)
        self.assertIn("isResearchGraphHash", ui)
        self.assertIn('id="prks-graph-inspector"', ui)
        self.assertIn('class="right-panel-stack research-graph__inspector"', ui)

    def test_compact_toolbar_and_disclosure_panels(self):
        graph = _read(_GRAPH)
        # Permanent primary toolbar: Find, Fit, Reset layout, Filters, Legend toggles.
        self.assertIn('data-prks-role="graph-find"', graph)
        self.assertIn('data-prks-role="graph-fit">Fit', graph)
        self.assertIn('data-prks-role="graph-reset">Reset layout', graph)
        self.assertIn('data-prks-role="graph-filters-toggle"', graph)
        self.assertIn('data-prks-role="graph-legend-toggle"', graph)
        self.assertIn(">Filters</button>", graph)
        self.assertIn(">Legend</button>", graph)
        # Filters/legend are real disclosure buttons, not a floating popover.
        self.assertIn("aria-expanded=\"false\"", graph)
        self.assertIn("aria-controls=", graph)
        # Filter checkboxes and legend content are disclosed, hidden by default.
        self.assertIn('data-prks-role="graph-filters-panel" hidden', graph)
        self.assertIn('data-prks-role="graph-legend-panel"', graph)
        self.assertIn("toggleAuxPanel", graph)
        self.assertIn("setAuxPanelOpen", graph)
        self.assertIn("Escape", graph)
        # CSS makes the disclosure panels actually collapse (not just an empty flex box).
        css = _read(_CSS)
        self.assertIn(".research-graph__filters-panel[hidden]", css)
        self.assertIn(".research-graph__legend[hidden]", css)

    def test_selection_driven_inspector_contract(self):
        graph = _read(_GRAPH)
        # No empty "Select a node or edge" placeholder card any more.
        self.assertNotIn("Select a node or edge", graph)
        self.assertIn("hasSelection", graph)
        self.assertIn("prksResearchGraphHasInspectorSelection", graph)
        self.assertIn("data-graph-clear-selection", graph)
        # A visible "Clear selection" ghost/text action, distinct from the right panel's
        # own Close control -- not an ambiguous second X (see completion criteria).
        self.assertIn("Clear selection", graph)
        self.assertIn("prks-btn--ghost", graph)
        # Status messages live in a dedicated graph-local region, not the inspector.
        self.assertIn('data-prks-role="graph-status"', graph)
        self.assertIn("renderStatusMessage", graph)
        # Selection changes resync right-panel visibility without forcing fit()/layout.
        self.assertIn("syncInspectorVisibility", graph)
        self.assertIn("prksRefreshFocusedRightPanelVisibility", graph)
        self.assertIn("cy.resize", graph)

        ui = _read(os.path.join(_FRONTEND, "js", "ui.js"))
        self.assertIn("function prksRefreshFocusedRightPanelVisibility", ui)
        self.assertIn("prksResearchGraphHasInspectorSelection", ui)
        # Graph route visibility is selection-aware, not unconditionally actionable.
        actionable = ui.split("function prksRightPanelHasActionableContent", 1)[1].split(
            "function prksShouldHideRightPanel", 1
        )[0]
        self.assertNotIn("if (isResearchGraphHash(h)) return true;", actionable)
        self.assertIn("isResearchGraphHash(h)", actionable)

    def test_vue_graph_resource_lifetime(self):
        app = _read(_APP)
        graph = _read(_GRAPH)
        vue = _read(_GRAPH_VUE)
        session = _read(_GRAPH_SESSION)
        agents = _read(_AGENTS)
        self.assertIn("function prksPresentVueResearchGraph", app)
        self.assertIn("prksVuePresentResearchGraph", app)
        self.assertIn("ctx.clearResource('researchGraph')", app)
        self.assertIn("data-prks-research-graph", vue)
        self.assertIn('data-prks-role="graph-canvas"', vue)
        self.assertIn("prksReleaseResearchGraph", vue)
        self.assertNotIn("fetch(", vue)
        self.assertNotIn("fetch(", session)
        self.assertNotIn("vue-router", session)
        self.assertNotIn("pinia", session)
        self.assertIn("presentRouteSurface", session)
        self.assertIn("renderResearchGraph", session)
        self.assertIn("retainLiveGraph", graph)
        self.assertIn("releaseLiveGraph", graph)
        self.assertIn("disconnectResizeObserver", graph)
        self.assertIn("cancelAnimationFrame", graph)
        self.assertIn("function prksReleaseResearchGraph", graph)
        self.assertIn("data-prks-research-graph", graph)
        self.assertNotIn("__prksResearchGraphLiveCount = 1", graph)
        self.assertNotIn("__prksResearchGraphLiveCount = 0", graph)
        self.assertIn("shellHtml", agents)
        self.assertIn("#303 B2", agents)

    def test_vue_graph_host_fills_the_pane_and_refreshes_icons(self):
        css = _read(_CSS)
        host = css.split(
            ".prks-tab-root > [data-prks-vue-route-host]:has(.research-graph)", 1
        )[1].split("}", 1)[0]
        self.assertIn("flex: 1 1 auto", host)
        self.assertIn("min-height: 0", host)
        self.assertIn("display: flex", host)
        self.assertIn("flex-direction: column", host)
        body = css.split('.research-graph > [data-prks-role="graph-body"]', 1)[1].split("}", 1)[0]
        self.assertIn("flex: 1 1 auto", body)
        self.assertIn("min-height: 0", body)
        self.assertIn("display: flex", body)
        self.assertIn("flex-direction: column", body)
        vue = _read(_GRAPH_VUE)
        self.assertIn("onMounted", vue)
        self.assertIn("prksRefreshIcons", vue)
        self.assertIn('data-prks-role="graph-body"', vue)
        self.assertIn('data-prks-role="graph-legend-panel"', vue)
        self.assertIn("Graph UI unavailable.", vue)

    def test_node_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for research graph tests")
        proc = subprocess.run(
            [node, _RUNNER],
            cwd=_PROJECT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertIn("passed", proc.stdout)
        self.assertIn(", 0 failed", proc.stdout)
        self.assertNotIn("FAIL  ", proc.stdout)


if __name__ == "__main__":
    unittest.main()
