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

from typing import Any

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
        self.assertEqual(self.graph()["edges"], [])
        self.assertEqual(self.index.work_research_refs(work, None)["concepts"], [])


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
