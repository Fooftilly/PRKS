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
        concepts_vue = (ROOT / 'frontend-app/src/features/concepts/ConceptsIndexRoute.vue').read_text()
        concepts_detail = (ROOT / 'frontend-app/src/features/concepts/ConceptDetailRoute.vue').read_text()
        positions_vue = (ROOT / 'frontend-app/src/features/positions/PositionsIndexRoute.vue').read_text()
        positions_detail = (ROOT / 'frontend-app/src/features/positions/PositionDetailRoute.vue').read_text()
        arguments_vue = (ROOT / 'frontend-app/src/features/arguments/ArgumentsIndexRoute.vue').read_text()
        arguments_detail = (ROOT / 'frontend-app/src/features/arguments/ArgumentDetailRoute.vue').read_text()
        for source in (
            concepts_vue, concepts_detail, positions_vue, positions_detail,
            arguments_vue, arguments_detail,
        ):
            self.assertNotIn('_ONLINE_ONLY_ROLE', source)
            self.assertNotIn('Graph requires a connection', source)
        # Mutation controls moved with the Vue surfaces. People still tags them
        # in the legacy script. The role string must stay on the owner that paints.
        for source in (
            concepts_vue, concepts_detail, positions_vue,
            arguments_vue, arguments_detail,
        ):
            self.assertIn('MUTATION_ROLE', source)
            self.assertIn('mutation-control', source)
        people = (ROOT / 'frontend/js/components/people.js').read_text()
        self.assertNotIn('_ONLINE_ONLY_ROLE', people)
        self.assertNotIn('Graph requires a connection', people)
        self.assertIn('_MUTATION_ROLE', people)
        for stub_name in ('positions', 'arguments'):
            stub = (ROOT / ('frontend/js/components/%s.js' % stub_name)).read_text()
            self.assertNotIn('_ONLINE_ONLY_ROLE', stub)
            self.assertNotIn('Graph requires a connection', stub)
        for name in ('playlists', 'people-groups', 'works-pdf'):
            source = (ROOT / ('frontend/js/components/%s.js' % name)).read_text()
            self.assertNotIn('prksMarkResearchGraph', source)
        app = (ROOT / 'frontend/js/app.js').read_text()
        self.assertNotIn('prksMarkResearchGraph', app)  # Work creation never hooks Graph.
        sw = (ROOT / 'frontend/sw.js').read_text()
        self.assertNotIn('/api/research-graph', sw)
