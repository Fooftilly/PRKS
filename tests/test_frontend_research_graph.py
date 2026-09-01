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
_POSITIONS = os.path.join(_FRONTEND, "js", "components", "positions.js")
_ARGS = os.path.join(_FRONTEND, "js", "components", "arguments.js")
_PEOPLE = os.path.join(_FRONTEND, "js", "components", "people.js")
_VENDOR = os.path.join(_FRONTEND, "vendor", "cytoscape")
_CSS = os.path.join(_FRONTEND, "css", "style.css")
_README = os.path.join(_PROJECT_DIR, "README.md")
_AGENTS = os.path.join(_PROJECT_DIR, "AGENTS.md")
_SCHEMA = os.path.join(_PROJECT_DIR, "backend", "db_migrations.py")
_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_research_graph_selftest.js")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendResearchGraphTests(unittest.TestCase):
    def test_local_cytoscape_pin(self):
        js = os.path.join(_VENDOR, "cytoscape.min.js")
        version = _read(os.path.join(_VENDOR, "VERSION"))
        license_txt = _read(os.path.join(_VENDOR, "LICENSE"))
        self.assertTrue(os.path.isfile(js))
        self.assertGreater(os.path.getsize(js), 10000)
        self.assertIn("3.31.2", version)
        self.assertIn("cytoscape.js", version)
        self.assertIn("sha256:", version)
        self.assertIn("MIT", license_txt)
        self.assertNotIn("cdn.jsdelivr.net", version)
        self.assertNotIn("unpkg.com", version)

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
        self.assertIn("research-graph", nav)
        self.assertIn("navHref: '#/graph'", nav)
        self.assertIn("prksParseGraphFocus", nav)
        self.assertIn("prksGraphCanonical", nav)
        self.assertIn("person):[A-Za-z0-9]", nav)
        self.assertIn("case 'research-graph':", app)
        self.assertNotIn("route.name === 'graph' || route.canonicalize", app)
        self.assertIn("destroyResearchGraph", app)
        self.assertIn("prksResearchRouteForcesOpen", nav)
        self.assertIn("route.name === 'research-graph'", nav)

    def test_view_in_graph_actions(self):
        self.assertIn("prks-concept-view-graph", _read(_CONCEPTS))
        self.assertIn("prks-position-view-graph", _read(_POSITIONS))
        self.assertIn("prks-arg-view-graph", _read(_ARGS))
        self.assertIn("prks-person-view-graph", _read(_PEOPLE))
        self.assertIn("prksGraphFocusHash", _read(_CONCEPTS))
        self.assertIn("prksGraphFocusHash('person'", _read(_PEOPLE))
        self.assertIn("navigate-research-graph", _read(_PALETTE))
        self.assertIn("View this record in graph", _read(_PALETTE))
        self.assertIn("fetchResearchGraph", _read(_API))
        self.assertIn("/api/research-graph", _read(_API))

    def test_no_schema_bump(self):
        self.assertIn("LATEST_SCHEMA_VERSION = 13", _read(_SCHEMA))

    def test_docs(self):
        readme = _read(_README)
        agents = _read(_AGENTS)
        self.assertIn("## Research Graph", readme)
        self.assertIn("read-only", readme.lower())
        self.assertIn("[[concept:", readme)
        self.assertIn("read-only derived projection", agents)
        self.assertIn("namespaced by entity type", agents)
        self.assertIn("Do not add graph persistence", agents)

    def test_css_graph_layout(self):
        css = _read(_CSS)
        self.assertIn(".research-graph", css)
        self.assertIn(".research-graph__inspector", css)
        self.assertIn(".research-graph__legend", css)

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
