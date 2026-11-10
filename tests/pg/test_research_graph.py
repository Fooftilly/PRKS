"""Research Graph and mention reads on PostgreSQL (L-R-1 #634, PR 5; §8.4).

What only PostgreSQL has: the reads follow the derived index, so a Work
whose ``research.reindex_work`` has not run shows its previous note (and
``research_refs`` reports ``pending``); rows a pending reindex has not
removed yet (a deleted Work, a Concept deleted after its note stopped
naming it) are left out by the joins; each Library reads its own rows; a
large network keeps the snapshot's order. ``tests/test_research_graph_parity.py``
compares the answers with SQLite's.
"""

from __future__ import annotations

import io
import logging
from typing import Any
from unittest import mock

from backend import research_graph
from backend.domain.identity.library_scope import LibraryScope
from backend.pg_facade.research_index import PostgresResearchIndex
from backend.pg_facade import context as facade_context
from backend.research_graph import graph_node_id
from tests.pg.test_research_index import ResearchIndexTestCase


class GraphReadTestCase(ResearchIndexTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.index = PostgresResearchIndex(self.db)

    def graph(self, include_people: bool = False) -> dict[str, Any]:
        return self.index.build_graph(include_people=include_people).to_dict()

    def edges(self, edge_type: str) -> dict[tuple[str, str], dict[str, Any]]:
        return {(edge["source"], edge["target"]): edge for edge in self.graph()["edges"] if edge["type"] == edge_type}

    def node_ids(self) -> set[str]:
        return {node["id"] for node in self.graph()["nodes"]}


class FenceTests(GraphReadTestCase):
    def test_the_reads_follow_the_index_while_its_job_is_pending(self) -> None:
        ethics = self.concept("Ethics")
        work = self.library.add_work("W")
        self.research_note(work, "[[concept:Ethics]]")
        # Committed, not indexed yet: the reads show no mention, and the fence says so.
        self.assertEqual(self.index.work_research_refs(work, None)["pending"], True)
        self.assertEqual(self.index.concept_mention_counts(), {})
        self.assertNotIn(graph_node_id("work", work), self.node_ids())
        self.run_jobs()
        self.assertEqual(self.index.work_research_refs(work, None), {
            "concepts": [{"id": ethics, "name": "Ethics", "aliases": []}], "arguments": [], "pending": False})
        self.assertEqual(self.index.concept_mention_counts(), {ethics: 1})
        mention = (graph_node_id("work", work), graph_node_id("concept", ethics))
        self.assertEqual(self.edges("mentions_concept")[mention]["count"], 1)
        # An edit shows the indexed note until its own job runs.
        self.research_note(work, "[[concept:Ethics]] and [[concept:ethics]]")
        self.assertEqual(self.index.work_research_refs(work, None)["pending"], True)
        self.assertEqual(self.index.mention_count_for_concept(ethics), 1)
        self.run_jobs()
        self.assertEqual(self.index.mention_count_for_concept(ethics), 2)
        self.assertEqual(self.edges("mentions_concept")[mention]["count"], 2)
        self.assertEqual([len(row["occurrences"]) for row in self.index.concept_backlinks(ethics, None)], [2])

    def test_a_pending_edit_cuts_no_snippet_from_the_new_note(self) -> None:
        ethics = self.concept("Ethics")
        work = self.library.add_work("W")
        self.research_note(work, "[[concept:Ethics]] opens the note.")
        self.run_jobs()
        self.research_note(work, "An unrelated opening sentence, then [[concept:Ethics]].")
        # The indexed offsets are the old note's: no snippet is cut from the new one.
        [row] = self.index.concept_backlinks(ethics, None)
        self.assertEqual(row["occurrences"], [{"start": 0, "end": 18, "snippet": ""}])
        self.run_jobs()
        [row] = self.index.concept_backlinks(ethics, None)
        self.assertEqual(row["occurrences"][0]["start"], 36)
        self.assertIn("[[concept:Ethics]]", row["occurrences"][0]["snippet"])

    def test_a_work_that_does_not_exist_has_no_references(self) -> None:
        self.assertEqual(self.index.work_research_refs("W-" + "0" * 32, None),
                         {"concepts": [], "arguments": [], "pending": False})
        self.assertEqual(self.index.work_research_refs("  ", None),
                         {"concepts": [], "arguments": [], "pending": False})


class StaleRowTests(GraphReadTestCase):
    def test_a_deleted_work_leaves_the_reads_before_its_reindex_runs(self) -> None:
        ethics = self.concept("Ethics")
        claim = self.argument("Claim")
        work = self.library.add_work("W")
        self.research_note(work, f"[[concept:Ethics]] [[argument:{claim}]]")
        self.run_jobs()
        self.library.delete_work_record(work)
        self.assertGreater(self.derived_rows(work), 0)  # the delete's reindex has not run
        self.assertEqual(self.index.concept_mention_counts(), {})
        self.assertEqual(self.index.argument_mention_counts(), {})
        self.assertEqual(self.index.mention_count_for_argument(claim), 0)
        self.assertEqual(self.index.concept_backlinks(ethics, None), [])
        self.assertNotIn(graph_node_id("work", work), self.node_ids())
        self.run_jobs()
        self.assertEqual(self.derived_rows(work), 0)

    def test_a_concept_deleted_before_the_reindex_of_its_last_note_runs_leaves_the_reads(self) -> None:
        temp = self.concept("Temporary")
        work = self.library.add_work("W")
        self.research_note(work, "[[concept:Temporary]]")
        self.run_jobs()
        self.research_note(work, "")  # the note no longer names it, so the delete is allowed
        self.library.delete_concept(temp)
        self.assertEqual(self.mentions(work)["concepts"][0][0], temp)  # stale until the job runs
        self.assertEqual(self.index.concept_mention_counts(), {})
        self.assertEqual(self.index.mention_count_for_concept(temp), 0)
        self.assertEqual(self.index.concept_backlinks(temp, None), [])
        self.assertEqual(self.graph()["edges"], [])
        self.assertEqual(self.index.work_research_refs(work, None)["concepts"], [])

    def test_an_argument_deleted_before_the_reindex_of_its_last_note_runs_leaves_the_reads(self) -> None:
        claim = self.argument("Claim")
        work = self.library.add_work("W")
        self.research_note(work, f"[[argument:{claim}]]")
        self.run_jobs()
        self.research_note(work, "")
        self.library.delete_argument(claim)
        self.assertEqual(self.index.argument_mention_counts(), {})
        self.assertEqual(self.index.mention_count_for_argument(claim), 0)
        self.assertEqual(self.index.argument_backlinks(claim, None), [])
        self.assertEqual(self.graph()["edges"], [])


class IsolationTests(GraphReadTestCase):
    def test_each_library_reads_its_own_network(self) -> None:
        ours = self.concept("Shared")
        work = self.library.add_work("Ours")
        self.research_note(work, "[[concept:Shared]]")
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            theirs = self.concept("Shared")
            their_work = self.library.add_work("Theirs")
            self.library.update_work_metadata(their_work, {"text_content": "[[concept:Shared]] [[concept:Shared]]"})
        self.run_jobs()
        self.assertEqual(self.index.concept_mention_counts(), {ours: 1})
        self.assertEqual(self.node_ids(), {graph_node_id("concept", ours), graph_node_id("work", work)})
        self.assertEqual(self.index.concept_backlinks(theirs, None), [])
        self.assertEqual(self.index.work_research_refs(their_work, None)["concepts"], [])
        with facade_context.acting_as(other.context):
            self.assertEqual(self.index.concept_mention_counts(), {theirs: 2})
            self.assertEqual(self.node_ids(), {graph_node_id("concept", theirs), graph_node_id("work", their_work)})
            self.assertEqual(self.index.concept_backlinks(ours, None), [])


class LargeNetworkTests(GraphReadTestCase):
    def test_a_large_network_keeps_the_snapshot_order(self) -> None:
        names = [f"Concept {n:03d}" for n in range(300)]
        ids = [self.concept(name) for name in names]
        for child, parent in zip(ids[1:], ids):
            self.library.replace_concept_parents(child, [parent])
        work = self.library.add_work("Index")
        self.research_note(work, " ".join(f"[[concept:{name}]] [[concept:{name.upper()}]]" for name in names))
        self.run_jobs()
        graph = self.graph()
        self.assertEqual(graph["meta"]["node_count"], 301)
        self.assertEqual(graph["meta"]["edge_count"], 299 + 300)
        self.assertEqual([node["label"] for node in graph["nodes"]], names + ["Index"])
        self.assertEqual([(edge["type"], edge["source"], edge["target"]) for edge in graph["edges"]],
                         sorted((edge["type"], edge["source"], edge["target"]) for edge in graph["edges"]))
        self.assertEqual(set(self.index.concept_mention_counts().values()), {2})
        [backlink] = self.index.concept_backlinks(ids[150], None)
        self.assertEqual(len(backlink["occurrences"]), 2)
        self.assertIn("[[concept:Concept 150]]", backlink["occurrences"][0]["snippet"])


class BacklinkTests(GraphReadTestCase):
    def test_backlinks_are_ordered_by_work_then_offset(self) -> None:
        ethics = self.concept("Ethics")
        works = sorted([self.library.add_work("First"), self.library.add_work("Second")])
        for work in reversed(works):
            self.research_note(work, "Before [[concept:Ethics]] between [[concept:ethics]] after.")
        self.run_jobs()
        rows = self.index.concept_backlinks(ethics, None)
        self.assertEqual([row["work_id"] for row in rows], works)
        self.assertEqual([[o["start"] for o in row["occurrences"]] for row in rows], [[7, 34], [7, 34]])
        self.assertEqual(rows[0]["occurrences"][0]["snippet"],
                         "Before [[concept:Ethics]] between [[concept:ethics]] after.")


PRIVATE_CONCEPT = "PRIVATE_CONCEPT_GRAPH_X9Q7"
PRIVATE_ARGUMENT = "PRIVATE_ARGUMENT_GRAPH_X9Q7"
PRIVATE_WORK = "PRIVATE_WORK_GRAPH_X9Q7"


class ProjectionTests(GraphReadTestCase):
    """The projection's nodes, edges, people, bounds and privacy (the retired ``tests/test_research_graph.py``)."""

    def network(self) -> dict[str, str]:
        parent, child = self.concept("Critical Theory"), self.concept(PRIVATE_CONCEPT)
        self.library.replace_concept_parents(child, [parent])
        pos = self.position("Standardization thesis")
        work_x, work_y = self.library.add_work(PRIVATE_WORK), self.library.add_work("Minima Moralia")
        arg1 = self.argument(PRIVATE_ARGUMENT, sources=[{"work_id": work_x, "pages": "12-14"}],
                             targets=[{"type": "position", "id": pos, "verdict_id": "supports"}])
        arg2 = self.argument("Counter", kind="stance", targets=[{"type": "argument", "id": arg1, "verdict_id": "opposes"}])
        self.research_note(work_x, "[[concept:%s]] [[concept:%s]] [[concept:%s]]" % ((PRIVATE_CONCEPT,) * 3))
        self.research_note(work_y, "See [[argument:%s]]" % arg1)
        self.run_jobs()
        return {"parent": parent, "child": child, "pos": pos, "work_x": work_x, "work_y": work_y,
                "arg1": arg1, "arg2": arg2}

    def of_type(self, snap: dict[str, Any], edge_type: str) -> list[dict[str, Any]]:
        return [edge for edge in snap["edges"] if edge["type"] == edge_type]

    def test_a_representative_network(self) -> None:
        n = self.network()
        self.library.add_work("Unrelated library file")
        snap = self.graph()
        nodes = {item["id"]: item for item in snap["nodes"]}
        g = graph_node_id
        self.assertEqual(set(nodes), {g("concept", n["parent"]), g("concept", n["child"]), g("position", n["pos"]),
                                      g("argument", n["arg1"]), g("argument", n["arg2"]),
                                      g("work", n["work_x"]), g("work", n["work_y"])})
        self.assertEqual(nodes[g("argument", n["arg2"])]["kind"], "stance")
        self.assertNotIn("main_text", nodes[g("argument", n["arg1"])])
        self.assertNotIn("description", nodes[g("concept", n["child"])])
        edges = {item["id"]: item for item in snap["edges"]}
        parent_edge = "concept_parent:%s>%s" % (g("concept", n["child"]), g("concept", n["parent"]))
        self.assertEqual(edges[parent_edge]["type"], "concept_parent")
        (supports,) = self.of_type(snap, "argument_position")
        self.assertEqual((supports["source"], supports["target"], supports["verdict_id"], supports["verdict_label"]),
                         (g("argument", n["arg1"]), g("position", n["pos"]), "supports", "Supports"))
        (response,) = self.of_type(snap, "argument_argument")
        self.assertEqual((response["source"], response["target"]), (g("argument", n["arg2"]), g("argument", n["arg1"])))
        (source,) = self.of_type(snap, "argument_source")
        self.assertEqual((source["source"], source["target"], source["pages"]),
                         (g("argument", n["arg1"]), g("work", n["work_x"]), "12-14"))
        (mention_c,) = self.of_type(snap, "mentions_concept")
        self.assertEqual((mention_c["source"], mention_c["target"], mention_c["count"]),
                         (g("work", n["work_x"]), g("concept", n["child"]), 3))
        (mention_a,) = self.of_type(snap, "mentions_argument")
        self.assertEqual((mention_a["source"], mention_a["target"]), (g("work", n["work_y"]), g("argument", n["arg1"])))
        self.assertEqual((snap["meta"]["derived_note_edges_available"], snap["meta"]["people_included"]), (True, False))
        types = [item["type"] for item in snap["nodes"]]
        self.assertEqual(types, sorted(types))

    def test_a_citation_and_a_mention_of_one_pair_are_two_edges(self) -> None:
        n = self.network()
        self.research_note(n["work_x"], "[[concept:%s]] [[argument:%s]]" % (PRIVATE_CONCEPT, n["arg1"]))
        self.run_jobs()
        snap = self.graph()
        src, work = graph_node_id("argument", n["arg1"]), graph_node_id("work", n["work_x"])
        sources = [e for e in self.of_type(snap, "argument_source") if (e["source"], e["target"]) == (src, work)]
        mentions = [e for e in self.of_type(snap, "mentions_argument") if (e["source"], e["target"]) == (work, src)]
        self.assertEqual((len(sources), len(mentions)), (1, 1))
        self.assertNotEqual(sources[0]["id"], mentions[0]["id"])

    def test_people_are_optional_and_authors_only(self) -> None:
        n = self.network()
        author = self.library.add_person("Max", "Horkheimer")
        editor = self.library.add_person("Ed", "Itor")
        self.library.add_role(author, n["work_x"], "Author")
        self.library.add_role(editor, n["work_x"], "Editor")
        off = self.graph(include_people=False)
        self.assertFalse(off["meta"]["people_included"])
        self.assertFalse(any(item["type"] == "person" for item in off["nodes"]))
        on = self.graph(include_people=True)
        self.assertTrue(on["meta"]["people_included"])
        self.assertEqual({p["record_id"] for p in on["nodes"] if p["type"] == "person"}, {author})
        (authored,) = self.of_type(on, "work_author")
        self.assertEqual((authored["source"], authored["target"]),
                         (graph_node_id("person", author), graph_node_id("work", n["work_x"])))

    def test_a_person_and_a_position_of_one_id_are_two_nodes(self) -> None:
        person = self.library.add_person("Ada", "Lovelace")
        position = self.position("Collision Position")
        self.sql("UPDATE prks.positions SET id = %s WHERE id = %s", (person, position))
        work = self.library.add_work("Linked")
        self.concept(PRIVATE_CONCEPT)
        self.research_note(work, "[[concept:%s]]" % PRIVATE_CONCEPT)
        self.run_jobs()
        self.library.add_role(person, work, "Author")
        nodes = {node["id"]: node for node in self.graph(include_people=True)["nodes"]}
        for kind in ("person", "position"):
            node = nodes[kind + ":" + person]
            self.assertEqual((node["record_id"], node["type"]), (person, kind))

    def test_the_statement_count_does_not_grow_with_the_network(self) -> None:
        def fill(prefix: str, count: int) -> None:
            for i in range(count):
                name = "%s concept %s" % (prefix, i)
                self.concept(name)
                work = self.library.add_work("%s work %s" % (prefix, i))
                self.argument("%s arg %s" % (prefix, i), sources=[{"work_id": work, "pages": "1"}])
                self.research_note(work, "[[concept:%s]]" % name)
            self.run_jobs()

        def statements() -> int:
            real, seen = LibraryScope.execute, []

            def record(scope: LibraryScope, query: Any, params: Any = None) -> Any:
                seen.append(query)
                return real(scope, query, params)

            with mock.patch.object(LibraryScope, "execute", autospec=True, side_effect=record):
                self.graph()
            return len(seen)

        fill("small", 8)
        small = statements()
        fill("big", 24)
        self.assertEqual(statements(), small)
        self.assertLessEqual(small, 12)

    def test_the_names_stay_out_of_logs_and_a_graph_too_large_reports_counts_only(self) -> None:
        self.network()
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        previous = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            blob = str(self.graph())
            with mock.patch.object(research_graph, "MAX_GRAPH_NODES", 1), \
                    self.assertRaises(research_graph.GraphTooLargeError) as caught:
                self.graph()
        finally:
            root.removeHandler(handler)
            root.setLevel(previous)
        for sentinel in (PRIVATE_CONCEPT, PRIVATE_ARGUMENT, PRIVATE_WORK):
            self.assertIn(sentinel, blob)
            self.assertNotIn(sentinel, stream.getvalue())
            self.assertNotIn(sentinel, str(caught.exception))
            self.assertNotIn(sentinel, str(caught.exception.extra))
        self.assertEqual(caught.exception.code, "graph_too_large")
        self.assertGreaterEqual(caught.exception.extra["node_count"], 2)
