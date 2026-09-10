"""Graph snapshot contract, coherence and ownership regressions."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ResearchGraphOfflineTests(unittest.TestCase):
    def test_node_contract(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node unavailable')
        result = subprocess.run([node, str(ROOT / 'tests/browser/run_research_graph_offline_selftest.js')],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_bounds_match_backend(self):
        from backend.research_graph import MAX_GRAPH_NODES, MAX_GRAPH_EDGES
        app = (ROOT / 'frontend/js/app.js').read_text()
        self.assertIn('PRKS_RESEARCH_GRAPH_MAX_NODES = %d;' % MAX_GRAPH_NODES, app)
        self.assertIn('PRKS_RESEARCH_GRAPH_MAX_EDGES = %d;' % MAX_GRAPH_EDGES, app)

    def test_exclusions_and_navigation(self):
        for name in ('concepts', 'positions', 'arguments', 'people'):
            source = (ROOT / ('frontend/js/components/%s.js' % name)).read_text()
            self.assertNotIn('_ONLINE_ONLY_ROLE', source)
            self.assertNotIn('Graph requires a connection', source)
            self.assertIn('_MUTATION_ROLE', source)
        for name in ('playlists', 'people-groups', 'works-pdf'):
            source = (ROOT / ('frontend/js/components/%s.js' % name)).read_text()
            self.assertNotIn('prksMarkResearchGraph', source)
        app = (ROOT / 'frontend/js/app.js').read_text()
        self.assertNotIn('prksMarkResearchGraph', app)  # Work creation never hooks Graph.
        sw = (ROOT / 'frontend/sw.js').read_text()
        self.assertNotIn('/api/research-graph', sw)
