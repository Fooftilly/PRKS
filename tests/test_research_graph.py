"""Research Graph projection: canonical edges, derived mentions, privacy, bounds."""
import io
import logging
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.db_manager import PRKS_SCHEMA_VERSION, PRKSDatabase
from backend.db_migrations import LATEST_SCHEMA_VERSION
from backend.log_safety import safe_route
from backend.research_graph import (
    MAX_GRAPH_EDGES,
    MAX_GRAPH_NODES,
    GraphTooLargeError,
    ResearchGraphBuilder,
    graph_node_id,
)
from backend.research_index import PRKSResearchIndex
from backend.research_network import (
    create_argument,
    create_concept,
    create_position,
    replace_concept_parents,
    save_work_notes,
)
from backend.storage.config import StorageConfig

_SCHEMA_PATH = os.path.join(_PROJECT_DIR, "backend", "db_schema.sql")

PRIVATE_CONCEPT = "PRIVATE_CONCEPT_GRAPH_X9Q7"
PRIVATE_ARGUMENT = "PRIVATE_ARGUMENT_GRAPH_X9Q7"
PRIVATE_WORK = "PRIVATE_WORK_GRAPH_X9Q7"


class _FailingIndex:
    def aggregate_concept_mention_edges(self):
        raise RuntimeError("derived index exploded")

    def aggregate_argument_mention_edges(self):
        raise RuntimeError("derived index exploded")


class ResearchGraphTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="prks-graph-")
        self.storage = StorageConfig.for_testing(self._tmpdir)
        os.makedirs(self.storage.pdfs_dir, exist_ok=True)
        self.db = PRKSDatabase(storage=self.storage, schema_path=_SCHEMA_PATH)
        self.index = PRKSResearchIndex(storage=self.storage)
        self.builder = ResearchGraphBuilder()

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _work(self, title="Dialectic"):
        return self.db.add_work(title=title)

    def _network(self):
        parent = create_concept(self.db, "Critical Theory")
        child = create_concept(self.db, PRIVATE_CONCEPT)
        replace_concept_parents(self.db, child["id"], [parent["id"]])
        pos = create_position(self.db, "Standardization thesis")
        work_x = self._work(PRIVATE_WORK)
        work_y = self._work("Minima Moralia")
        arg1 = create_argument(
            self.db,
            name=PRIVATE_ARGUMENT,
            kind="argument",
            sources=[{"work_id": work_x, "pages": "12-14"}],
            targets=[{"type": "position", "id": pos["id"], "verdict_id": "supports"}],
        )
        arg2 = create_argument(
            self.db,
            name="Counter",
            kind="stance",
            targets=[{"type": "argument", "id": arg1["id"], "verdict_id": "opposes"}],
        )
        save_work_notes(
            self.db,
            work_x,
            "[[concept:%s]] [[concept:%s]] [[concept:%s]]"
            % (PRIVATE_CONCEPT, PRIVATE_CONCEPT, PRIVATE_CONCEPT),
        )
        save_work_notes(self.db, work_y, "See [[argument:%s]]" % arg1["id"])
        self.index.sync_work(work_x, self.db.get_work(work_x)["text_content"], self.db)
        self.index.sync_work(work_y, self.db.get_work(work_y)["text_content"], self.db)
        return {
            "parent": parent,
            "child": child,
            "pos": pos,
            "work_x": work_x,
            "work_y": work_y,
            "arg1": arg1,
            "arg2": arg2,
        }

    def test_schema_version_unchanged(self):
        self.assertEqual(PRKS_SCHEMA_VERSION, 17)
        self.assertEqual(LATEST_SCHEMA_VERSION, 17)

    def test_representative_network(self):
        n = self._network()
        snap = self.builder.build(self.db, self.index).to_dict()
        nodes = {item["id"]: item for item in snap["nodes"]}
        edges = {item["id"]: item for item in snap["edges"]}
        self.assertIn(graph_node_id("concept", n["parent"]["id"]), nodes)
        self.assertIn(graph_node_id("concept", n["child"]["id"]), nodes)
        self.assertIn(graph_node_id("position", n["pos"]["id"]), nodes)
        self.assertIn(graph_node_id("argument", n["arg1"]["id"]), nodes)
        self.assertIn(graph_node_id("argument", n["arg2"]["id"]), nodes)
        self.assertEqual(nodes[graph_node_id("argument", n["arg2"]["id"])]["kind"], "stance")
        self.assertIn(graph_node_id("work", n["work_x"]), nodes)
        self.assertIn(graph_node_id("work", n["work_y"]), nodes)
        self.assertNotIn("main_text", nodes[graph_node_id("argument", n["arg1"]["id"])])
        self.assertNotIn("description", nodes[graph_node_id("concept", n["child"]["id"])])
        parent_edge = edges[
            "concept_parent:%s>%s"
            % (
                graph_node_id("concept", n["child"]["id"]),
                graph_node_id("concept", n["parent"]["id"]),
            )
        ]
        self.assertEqual(parent_edge["type"], "concept_parent")
        supports = [
            e
            for e in snap["edges"]
            if e["type"] == "argument_position" and e["source"] == graph_node_id("argument", n["arg1"]["id"])
        ]
        self.assertEqual(len(supports), 1)
        self.assertEqual(supports[0]["target"], graph_node_id("position", n["pos"]["id"]))
        self.assertEqual(supports[0]["verdict_id"], "supports")
        self.assertEqual(supports[0]["verdict_label"], "Supports")
        response = [
            e
            for e in snap["edges"]
            if e["type"] == "argument_argument"
        ]
        self.assertEqual(len(response), 1)
        self.assertEqual(response[0]["source"], graph_node_id("argument", n["arg2"]["id"]))
        self.assertEqual(response[0]["target"], graph_node_id("argument", n["arg1"]["id"]))
        source = [
            e
            for e in snap["edges"]
            if e["type"] == "argument_source"
        ]
        self.assertEqual(len(source), 1)
        self.assertEqual(source[0]["source"], graph_node_id("argument", n["arg1"]["id"]))
        self.assertEqual(source[0]["target"], graph_node_id("work", n["work_x"]))
        self.assertEqual(source[0]["pages"], "12-14")
        mention_c = [
            e
            for e in snap["edges"]
            if e["type"] == "mentions_concept"
        ]
        self.assertEqual(len(mention_c), 1)
        self.assertEqual(mention_c[0]["source"], graph_node_id("work", n["work_x"]))
        self.assertEqual(mention_c[0]["target"], graph_node_id("concept", n["child"]["id"]))
        self.assertEqual(mention_c[0]["count"], 3)
        mention_a = [
            e
            for e in snap["edges"]
            if e["type"] == "mentions_argument"
        ]
        self.assertEqual(len(mention_a), 1)
        self.assertEqual(mention_a[0]["source"], graph_node_id("work", n["work_y"]))
        self.assertEqual(mention_a[0]["target"], graph_node_id("argument", n["arg1"]["id"]))
        self.assertTrue(snap["meta"]["derived_note_edges_available"])
        self.assertFalse(snap["meta"]["people_included"])
        types = [item["type"] for item in snap["nodes"]]
        self.assertEqual(types, sorted(types))

    def test_namespaced_ids_survive_person_position_collision(self):
        with self.db.connection() as conn:
            conn.execute(
                "INSERT INTO persons (id, first_name, last_name) VALUES (?, ?, ?)",
                ("P-123", "Ada", "Lovelace"),
            )
            conn.execute(
                "INSERT INTO positions (id, name) VALUES (?, ?)",
                ("P-123", "Collision Position"),
            )
            conn.commit()
        work = self._work("Linked")
        create_concept(self.db, PRIVATE_CONCEPT)
        save_work_notes(self.db, work, "[[concept:%s]]" % PRIVATE_CONCEPT)
        self.index.sync_work(work, self.db.get_work(work)["text_content"], self.db)
        self.db.add_role("P-123", work, "Author")
        snap = self.builder.build(self.db, self.index, include_people=True).to_dict()
        ids = {n["id"] for n in snap["nodes"]}
        self.assertIn("person:P-123", ids)
        self.assertIn("position:P-123", ids)
        self.assertNotEqual("person:P-123", "position:P-123")
        person = next(n for n in snap["nodes"] if n["id"] == "person:P-123")
        position = next(n for n in snap["nodes"] if n["id"] == "position:P-123")
        self.assertEqual(person["record_id"], "P-123")
        self.assertEqual(position["record_id"], "P-123")
        self.assertEqual(person["type"], "person")
        self.assertEqual(position["type"], "position")

    def test_no_duplicate_mention_edges(self):
        n = self._network()
        snap = self.builder.build(self.db, self.index).to_dict()
        mention_ids = [
            e["id"]
            for e in snap["edges"]
            if e["type"] == "mentions_concept" and e["source"] == graph_node_id("work", n["work_x"])
        ]
        self.assertEqual(len(mention_ids), 1)
        self.assertEqual(len(set(mention_ids)), 1)

    def test_argument_response_direction(self):
        n = self._network()
        snap = self.builder.build(self.db, self.index).to_dict()
        edge = next(e for e in snap["edges"] if e["type"] == "argument_argument")
        self.assertEqual(edge["source"], graph_node_id("argument", n["arg2"]["id"]))
        self.assertEqual(edge["target"], graph_node_id("argument", n["arg1"]["id"]))

    def test_source_and_mention_are_distinct(self):
        n = self._network()
        save_work_notes(
            self.db,
            n["work_x"],
            "[[concept:%s]] [[argument:%s]]" % (PRIVATE_CONCEPT, n["arg1"]["id"]),
        )
        self.index.sync_work(n["work_x"], self.db.get_work(n["work_x"])["text_content"], self.db)
        snap = self.builder.build(self.db, self.index).to_dict()
        src = graph_node_id("argument", n["arg1"]["id"])
        work = graph_node_id("work", n["work_x"])
        source_edges = [
            e for e in snap["edges"] if e["type"] == "argument_source" and e["source"] == src and e["target"] == work
        ]
        mention_edges = [
            e
            for e in snap["edges"]
            if e["type"] == "mentions_argument" and e["source"] == work and e["target"] == src
        ]
        self.assertEqual(len(source_edges), 1)
        self.assertEqual(len(mention_edges), 1)
        self.assertNotEqual(source_edges[0]["id"], mention_edges[0]["id"])

    def test_unrelated_work_omitted(self):
        self._network()
        extra = self._work("Unrelated library file")
        snap = self.builder.build(self.db, self.index).to_dict()
        ids = {n["id"] for n in snap["nodes"]}
        self.assertNotIn(graph_node_id("work", extra), ids)

    def test_people_optional_authors_only(self):
        n = self._network()
        author = self.db.add_person("Max", "Horkheimer")
        editor = self.db.add_person("Ed", "Itor")
        self.db.add_role(author, n["work_x"], "Author")
        self.db.add_role(editor, n["work_x"], "Editor")
        off = self.builder.build(self.db, self.index, include_people=False).to_dict()
        self.assertFalse(off["meta"]["people_included"])
        self.assertFalse(any(item["type"] == "person" for item in off["nodes"]))
        on = self.builder.build(self.db, self.index, include_people=True).to_dict()
        self.assertTrue(on["meta"]["people_included"])
        people = [item for item in on["nodes"] if item["type"] == "person"]
        self.assertEqual({p["record_id"] for p in people}, {author})
        authors = [e for e in on["edges"] if e["type"] == "work_author"]
        self.assertEqual(len(authors), 1)
        self.assertEqual(authors[0]["source"], graph_node_id("person", author))
        self.assertEqual(authors[0]["target"], graph_node_id("work", n["work_x"]))

    def test_derived_index_failure_is_fail_soft(self):
        n = self._network()
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        log = logging.getLogger("prks.research_graph")
        log.addHandler(handler)
        try:
            snap = self.builder.build(self.db, _FailingIndex()).to_dict()
        finally:
            log.removeHandler(handler)
        self.assertFalse(snap["meta"]["derived_note_edges_available"])
        types = {e["type"] for e in snap["edges"]}
        self.assertIn("concept_parent", types)
        self.assertIn("argument_position", types)
        self.assertIn("argument_argument", types)
        self.assertIn("argument_source", types)
        self.assertNotIn("mentions_concept", types)
        self.assertNotIn("mentions_argument", types)
        self.assertIn(graph_node_id("work", n["work_x"]), {item["id"] for item in snap["nodes"]})
        text = buf.getvalue()
        self.assertNotIn(PRIVATE_CONCEPT, text)
        self.assertNotIn(PRIVATE_ARGUMENT, text)
        self.assertNotIn(PRIVATE_WORK, text)

    def test_query_count_is_bounded(self):
        def fill(prefix, count):
            for i in range(count):
                c = create_concept(self.db, "%s concept %s" % (prefix, i))
                w = self._work("%s work %s" % (prefix, i))
                create_argument(
                    self.db,
                    name="%s arg %s" % (prefix, i),
                    kind="argument",
                    sources=[{"work_id": w, "pages": "1"}],
                )
                save_work_notes(self.db, w, "[[concept:%s]]" % c["name"])
                self.index.sync_work(w, self.db.get_work(w)["text_content"], self.db)

        fill("small", 8)
        small = ResearchGraphBuilder()
        small.build(self.db, self.index)
        small_n = small.last_main_query_count
        fill("big", 24)
        big = ResearchGraphBuilder()
        big.build(self.db, self.index)
        self.assertLessEqual(small_n, 12)
        self.assertEqual(big.last_main_query_count, small_n)
        self.assertGreaterEqual(small_n, 8)

    def test_privacy_sentinels_stay_out_of_logs(self):
        self._network()
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        log = logging.getLogger("prks.research_graph")
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        try:
            snap = self.builder.build(self.db, self.index).to_dict()
        finally:
            log.removeHandler(handler)
        blob = str(snap)
        self.assertIn(PRIVATE_CONCEPT, blob)
        self.assertIn(PRIVATE_ARGUMENT, blob)
        self.assertIn(PRIVATE_WORK, blob)
        text = buf.getvalue()
        self.assertNotIn(PRIVATE_CONCEPT, text)
        self.assertNotIn(PRIVATE_ARGUMENT, text)
        self.assertNotIn(PRIVATE_WORK, text)
        self.assertIn("node_count=", text)
        self.assertIn("edge_count=", text)
        from backend.performance import reset as perf_reset
        from backend.performance import snapshot as perf_snapshot

        blob = str(perf_snapshot())
        self.assertNotIn(PRIVATE_CONCEPT, blob)
        self.assertNotIn(PRIVATE_ARGUMENT, blob)
        self.assertNotIn(PRIVATE_WORK, blob)
        self.assertGreaterEqual(
            perf_snapshot()["spans"].get("research_graph_build", {}).get("count", 0), 1
        )
        perf_reset()

    def test_graph_too_large_reports_counts_only(self):
        create_concept(self.db, PRIVATE_CONCEPT)
        create_concept(self.db, "Other")
        with patch("backend.research_graph.MAX_GRAPH_NODES", 1):
            with self.assertRaises(GraphTooLargeError) as ctx:
                self.builder.build(self.db, self.index)
        extra = ctx.exception.extra
        self.assertEqual(ctx.exception.code, "graph_too_large")
        self.assertGreaterEqual(extra["node_count"], 2)
        self.assertNotIn(PRIVATE_CONCEPT, str(ctx.exception))
        self.assertNotIn(PRIVATE_CONCEPT, str(extra))

    def test_safe_route(self):
        self.assertEqual(safe_route("/api/research-graph"), "/api/research-graph")
        self.assertEqual(safe_route("/api/research-graph?people=1"), "/api/research-graph")

    def test_bounds_constants(self):
        self.assertEqual(MAX_GRAPH_NODES, 2500)
        self.assertEqual(MAX_GRAPH_EDGES, 7500)

    def test_no_write_api(self):
        public = [n for n in dir(ResearchGraphBuilder) if not n.startswith("_")]
        self.assertIn("build", public)
        banned = ("save", "write", "delete", "insert", "update", "mutate")
        for name in public:
            self.assertFalse(any(b in name.lower() for b in banned), name)


if __name__ == "__main__":
    unittest.main()
