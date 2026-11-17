"""The Research Graph rules both backends share (kept by PG-C2 #641).

``backend/research_graph.py`` holds the node and edge ids, the size bounds
and the snapshot assembly ``backend/domain/research/graph.py`` uses on
PostgreSQL. These checks are pure; ``tests/pg/test_research_graph.py``
reads the graph through the PostgreSQL boundary.
"""

from __future__ import annotations

import ast
import pathlib
import re
import unittest

from backend.domain.research import graph
from backend.log_safety import safe_route
from backend.research_graph import MAX_GRAPH_EDGES, MAX_GRAPH_NODES, graph_node_id

_GRAPH = pathlib.Path(__file__).resolve().parents[1] / "backend" / "domain" / "research" / "graph.py"


class GraphRuleTests(unittest.TestCase):
    def test_the_route_is_logged_without_its_query(self) -> None:
        self.assertEqual(safe_route("/api/research-graph"), "/api/research-graph")
        self.assertEqual(safe_route("/api/research-graph?people=1"), "/api/research-graph")

    def test_the_bounds(self) -> None:
        self.assertEqual((MAX_GRAPH_NODES, MAX_GRAPH_EDGES), (2500, 7500))

    def test_node_ids_are_namespaced_by_kind(self) -> None:
        self.assertEqual(graph_node_id("person", "P-1"), "person:P-1")
        self.assertNotEqual(graph_node_id("person", "P-1"), graph_node_id("position", "P-1"))

    def test_the_postgresql_graph_never_writes(self) -> None:
        tree = ast.parse(_GRAPH.read_text(encoding="utf-8"))
        statements = [node.value.upper() for node in ast.walk(tree)
                      if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        self.assertTrue(any(text.lstrip().startswith("SELECT") for text in statements))
        for text in statements:
            self.assertIsNone(re.search(r"\b(INSERT\s+INTO|UPDATE\s+\w|DELETE\s+FROM)\b", text), text[:60])
        public = [name for name in dir(graph) if not name.startswith("_") and callable(getattr(graph, name))]
        self.assertIn("snapshot", public)

if __name__ == "__main__":
    unittest.main()
