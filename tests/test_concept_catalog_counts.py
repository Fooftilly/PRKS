"""Concept catalog child counts come from the one hierarchy pass (#141).

``list_concepts`` used to compute ``subconcept_count`` with a correlated
``(SELECT COUNT(*) FROM concept_parents ...)`` per Concept while also loading
the full ``concept_parents`` relation for the ``parents`` projection. The count
is now derived from that same parent-edge pass. These tests pin the observable
output against a frozen copy of the old implementation and pin the query shape
(a constant number of statements, none with a correlated scalar subquery).
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from typing import Dict, List
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.db_manager import PRKSDatabase
from backend.research_network import (
    create_concept,
    list_concepts,
    replace_concept_aliases,
    replace_concept_parents,
)
from backend.storage.config import StorageConfig

_SCHEMA_PATH = os.path.join(_PROJECT_DIR, "backend", "db_schema.sql")


def _old_list_concepts(db: PRKSDatabase) -> List[dict]:
    """Oracle: ``list_concepts`` as it was before #141 (correlated count)."""
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.name, c.description, c.created_at, c.updated_at,
                   (SELECT COUNT(*) FROM concept_parents p WHERE p.parent_concept_id = c.id)
                       AS subconcept_count
            FROM concepts c
            ORDER BY LOWER(c.name) ASC, c.id ASC
            """
        ).fetchall()
        parent_rows = conn.execute(
            """
            SELECT cp.child_concept_id AS child_id, p.id AS id, p.name AS name
            FROM concept_parents cp
            JOIN concepts p ON p.id = cp.parent_concept_id
            ORDER BY LOWER(p.name) ASC, p.id ASC
            """
        ).fetchall()
        alias_rows = conn.execute(
            "SELECT concept_id, alias FROM concept_aliases ORDER BY LOWER(alias) ASC"
        ).fetchall()
    parents: Dict[str, List[dict]] = {}
    for r in parent_rows:
        parents.setdefault(r["child_id"], []).append({"id": r["id"], "name": r["name"]})
    aliases: Dict[str, List[str]] = {}
    for r in alias_rows:
        aliases.setdefault(r["concept_id"], []).append(r["alias"])
    out = []
    for r in rows:
        item = dict(r)
        item["parents"] = parents.get(r["id"], [])
        item["aliases"] = aliases.get(r["id"], [])
        item["subconcept_count"] = int(r["subconcept_count"] or 0)
        out.append(item)
    return out


class ConceptCatalogCountTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="prks-concept-counts-")
        self.storage = StorageConfig.for_testing(self._tmpdir)
        os.makedirs(self.storage.pdfs_dir, exist_ok=True)
        self.db = PRKSDatabase(storage=self.storage, schema_path=_SCHEMA_PATH)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _concept(self, name: str) -> str:
        return create_concept(self.db, name)["id"]

    def _counts(self) -> Dict[str, int]:
        return {c["name"]: c["subconcept_count"] for c in list_concepts(self.db)}

    def _assert_parity(self):
        new = list_concepts(self.db)
        old = _old_list_concepts(self.db)
        self.assertEqual(new, old)
        # dict equality ignores key order, but /api/concepts serializes it.
        self.assertEqual(json.dumps(new), json.dumps(old))
        for item in new:
            self.assertIs(type(item["subconcept_count"]), int)

    def test_no_concepts(self):
        self.assertEqual(list_concepts(self.db), [])
        self._assert_parity()

    def test_single_concept_has_zero_children(self):
        self._concept("Lonely")
        items = list_concepts(self.db)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["subconcept_count"], 0)
        self.assertIs(type(items[0]["subconcept_count"]), int)
        self.assertEqual(items[0]["parents"], [])
        self._assert_parity()

    def test_one_direct_child(self):
        parent = self._concept("Parent")
        child = self._concept("Child")
        replace_concept_parents(self.db, child, [parent])
        self.assertEqual(self._counts(), {"Child": 0, "Parent": 1})
        self._assert_parity()

    def test_several_direct_children(self):
        parent = self._concept("Parent")
        for name in ("A", "B", "C"):
            replace_concept_parents(self.db, self._concept(name), [parent])
        self.assertEqual(self._counts(), {"A": 0, "B": 0, "C": 0, "Parent": 3})
        self._assert_parity()

    def test_multi_parent_child_counts_once_per_parent(self):
        p1 = self._concept("Left")
        p2 = self._concept("Right")
        child = self._concept("Shared")
        replace_concept_parents(self.db, child, [p1, p2])
        self.assertEqual(self._counts(), {"Left": 1, "Right": 1, "Shared": 0})
        self._assert_parity()

    def test_only_direct_children_count(self):
        root = self._concept("Root")
        mid = self._concept("Mid")
        leaf = self._concept("Leaf")
        replace_concept_parents(self.db, mid, [root])
        replace_concept_parents(self.db, leaf, [mid])
        self.assertEqual(self._counts(), {"Leaf": 0, "Mid": 1, "Root": 1})
        self._assert_parity()

    def _build_graph(self) -> Dict[str, str]:
        # Mixed-case names exercise LOWER() ordering of both concepts and parents.
        names = ["zeta", "Alpha", "beta", "Gamma", "delta", "Epsilon", "eta", "Theta", "iota"]
        ids = {n: self._concept(n) for n in names}
        edges = {
            "beta": ["Alpha"],
            "Gamma": ["Alpha", "zeta"],
            "delta": ["beta", "Gamma", "Alpha"],
            "Epsilon": ["delta"],
            "eta": ["delta", "zeta"],
            "Theta": ["Epsilon"],
            "iota": ["Theta", "eta", "beta"],
        }
        for child, parents in edges.items():
            replace_concept_parents(self.db, ids[child], [ids[p] for p in parents])
        replace_concept_aliases(self.db, ids["Gamma"], ["gamma-2", "Gamma One"])
        replace_concept_aliases(self.db, ids["iota"], ["jot"])
        return ids

    def test_ordering_parents_and_output_parity_over_graph(self):
        self._build_graph()
        new = list_concepts(self.db)
        old = _old_list_concepts(self.db)
        self.assertEqual([c["id"] for c in new], [c["id"] for c in old])
        self.assertEqual(
            {c["id"]: c["parents"] for c in new},
            {c["id"]: c["parents"] for c in old},
        )
        self.assertEqual(new, old)
        self.assertEqual(json.dumps(new), json.dumps(old))
        self.assertEqual(
            self._counts(),
            {
                "Alpha": 3,
                "beta": 2,
                "delta": 2,
                "Epsilon": 1,
                "eta": 1,
                "Gamma": 1,
                "iota": 0,
                "Theta": 1,
                "zeta": 2,
            },
        )
        by_name = {c["name"]: c for c in new}
        self.assertEqual(
            [p["name"] for p in by_name["delta"]["parents"]], ["Alpha", "beta", "Gamma"]
        )
        self.assertEqual(by_name["Gamma"]["aliases"], ["Gamma One", "gamma-2"])

    def _traced_list_concepts(self) -> List[str]:
        statements: List[str] = []
        real_get = self.db.get_connection

        def wrapped_get():
            conn = real_get()
            conn.set_trace_callback(lambda sql: statements.append(str(sql)))
            return conn

        with patch.object(self.db, "get_connection", side_effect=wrapped_get):
            list_concepts(self.db)
        return [
            s for s in statements if s.lstrip().upper().startswith(("SELECT", "WITH"))
        ]

    def test_query_shape_is_constant_and_uncorrelated(self):
        self._concept("Solo")
        small = self._traced_list_concepts()
        self._build_graph()
        large = self._traced_list_concepts()
        # Same statements regardless of catalog size: no per-Concept probes.
        self.assertEqual(small, large)
        self.assertEqual(len(large), 3)
        conn = sqlite3.connect(self.storage.db_path)
        try:
            for sql in large:
                self.assertNotIn("(SELECT", sql.upper())
                plan = conn.execute("EXPLAIN QUERY PLAN " + sql).fetchall()
                details = " | ".join(str(row[-1]).upper() for row in plan)
                self.assertNotIn("CORRELATED", details, sql)
        finally:
            conn.close()

    def test_old_query_plan_was_correlated(self):
        """Guard the guard: the plan check above would catch the old shape."""
        conn = sqlite3.connect(self.storage.db_path)
        try:
            plan = conn.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT c.id,
                       (SELECT COUNT(*) FROM concept_parents p
                        WHERE p.parent_concept_id = c.id) AS subconcept_count
                FROM concepts c
                """
            ).fetchall()
        finally:
            conn.close()
        self.assertIn("CORRELATED", " | ".join(str(row[-1]).upper() for row in plan))


if __name__ == "__main__":
    unittest.main()
