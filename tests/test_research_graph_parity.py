"""Research Graph and mention reads: PostgreSQL answers as SQLite does (L-R-1 #634, PR 5).

Each side builds the same Research network (Concepts with a hierarchy,
Positions, Arguments with targets and citations, Works with Research Notes
and Authors) one command at a time. The SQLite sides read through
``PRKSResearchIndex`` (kept as ``server.py`` keeps it: ``sync_work`` after
a note, ``remove_*`` after a delete) and ``ResearchGraphBuilder``; the
``postgres`` leg reads through ``PostgresResearchIndex`` after its
``research.reindex_work`` jobs ran. Results must be equal once generated
ids become labels. Edges are compared as sets: their order is by node id,
and the ids differ per side. Inputs avoid the documented differences
(``backend/pg_facade/research_index.py``): one alias per Concept, and
``pending`` is compared apart. ``tests/pg/test_research_graph.py`` covers
what only PostgreSQL has (the fence while a job is pending, stale rows,
isolation).
"""

from __future__ import annotations

import inspect
import os
import re
import sys
import unittest
from typing import Any
from unittest import mock

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend import research_graph
from backend.research_graph import GraphTooLargeError, ResearchGraphBuilder
from backend.research_index import PRKSResearchIndex
from backend.research_network import save_work_notes
from backend.storage.config import StorageConfig
from tests.backend_parity import POSTGRES, parametrize_backends
from tests.test_arguments_parity import ArgumentParityTestCase, ArgumentSide

Pair = tuple[str, str]
# A snippet can cut an id short; the cut ids differ per side.
_CUT_ID = re.compile(r"\b[ACPW]-[0-9A-F]{1,31}…")


@parametrize_backends
class ResearchReadParityTests(ArgumentParityTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ids: dict[str, Pair] = {}
        self.indexes: dict[int, Any] = {
            id(self.leg): self._leg_index(),
            id(self.ref): PRKSResearchIndex(storage=StorageConfig.for_testing(os.path.join(self.tmp, "ref"))),
        }

    def _leg_index(self) -> Any:
        if self.parity_backend == POSTGRES:
            from backend.pg_facade.research_index import PostgresResearchIndex

            return PostgresResearchIndex(self.leg.db.parity_library._database)
        return PRKSResearchIndex(storage=StorageConfig.for_testing(os.path.join(self.tmp, "leg")))

    def on_postgres(self, side: ArgumentSide) -> bool:
        return side is self.leg and self.parity_backend == POSTGRES

    def run_jobs(self) -> None:
        """Run the leg's queued reindex jobs (none on SQLite, which indexes inline)."""
        if self.parity_backend != POSTGRES:
            return
        from backend.domain.research import reindex  # noqa: F401  registers research.reindex_work
        from backend.jobs import queue
        from backend.jobs.runner import Outcome, run_job
        from tests.pg.jobs_fixtures import LEASE

        database = self.leg.db.parity_library._database
        while (job := queue.claim(database, "parity-worker", lease=LEASE)) is not None:
            self.assertIs(run_job(database, job, lease=LEASE), Outcome.SUCCEEDED)

    # -- building both sides ------------------------------------------------------

    def keep(self, label: str, pair: Pair) -> Pair:
        self.ids[label] = pair
        return pair

    def concept(self, label: str, alias: str = "") -> Pair:
        pair = self.keep(label, self.concept_both(label))
        if alias:
            for side, cid in zip(self.sides(), pair):
                side.db.replace_concept_aliases(cid, [alias])
        return pair

    def work(self, label: str, **fields: Any) -> Pair:
        return self.keep(label, (self.leg.work(label, title=label, **fields),
                                 self.ref.work(label, title=label, **fields)))

    def note(self, work: Pair, template: str) -> None:
        """Write ``template`` (``{label}`` filled by each side's ids) as both Works' Research Note."""
        for side, wid in zip(self.sides(), work):
            text = template.format(**{label: ids[side is self.ref] for label, ids in self.ids.items()})
            if self.on_postgres(side):
                side.db.update_work_metadata(wid, {"text_content": text})
            else:
                save_work_notes(side.db, wid, text)
                self.indexes[id(side)].sync_work(wid, text, side.db)
        self.run_jobs()

    def delete_work(self, work: Pair) -> None:
        for side, wid in zip(self.sides(), work):
            side.db.delete_work_record(wid)
            if not self.on_postgres(side):
                self.indexes[id(side)].remove_work(wid)
        self.run_jobs()

    def network(self) -> dict[str, Pair]:
        """Concepts, Positions, Arguments, cited and noted Works, Authors."""
        theory = self.concept("Theory")
        ethics = self.concept("Ethics", alias="Morals")
        for side, child, parent in zip(self.sides(), ethics, theory):
            side.db.replace_concept_parents(child, [parent])
        self.concept("Unmentioned")
        thesis = self.keep("Thesis", self.position_both("Thesis"))
        source = self.work("Source", doc_type="book")
        self.work("Commentary")
        claim = self.keep("Claim", self.argument_both(
            "Claim", sources=tuple([{"work_id": wid, "pages": "1-2"}] for wid in source),
            targets=tuple([{"type": "position", "id": pid, "verdict_id": "supports"}] for pid in thesis)))
        self.keep("Reply", self.argument_both(
            "Reply", "stance", targets=tuple([{"type": "argument", "id": aid, "verdict_id": "opposes"}]
                                             for aid in claim)))
        author = self.keep("Adorno", self.person_both("Adorno", first_name="Theodor"))
        for side, pid, wid in zip(self.sides(), author, source):
            side.db.add_role(pid, wid, "Author")
        self.note(source, "On [[concept:Theory]], [[concept:morals]] and [[concept:ETHICS]]; "
                          "see [[argument:{Claim}|the claim]]. `[[concept:Code]]` is no reference.")
        self.note(self.ids["Commentary"], "Again [[concept:Ethics]], against [[argument:{Reply}]]"
                                          " and [[argument:{Claim}]] and [[argument:A-gone]].")
        return self.ids

    # -- reading both sides -------------------------------------------------------

    def graph(self, side: ArgumentSide, include_people: bool = False) -> Any:
        index = self.indexes[id(side)]
        if self.on_postgres(side):
            return index.build_graph(include_people=include_people)
        return ResearchGraphBuilder().build(side.db, index, include_people=include_people)

    def same_graph(self, include_people: bool = False) -> dict[str, Any]:
        out = []
        for side in self.sides():
            snapshot = side.normal(self.graph(side, include_people).to_dict())
            snapshot["edges"] = sorted(snapshot["edges"], key=repr)
            out.append(snapshot)
        self.assertEqual(out[0], out[1])
        return out[0]

    def same_read(self, call: str, *pairs: Pair) -> Any:
        """``index.call(pair[side]...)`` on both sides; equal once normalized."""
        out = []
        for position, side in enumerate(self.sides()):
            out.append(side.normal(getattr(self.indexes[id(side)], call)(*(pair[position] for pair in pairs))))
        self.assertEqual(out[0], out[1], call)
        return out[0]

    def same_backlinks(self, call: str, pair: Pair) -> list[dict[str, Any]]:
        """Backlinks are ordered by Work id, which differs per side: compared by title."""
        out = []
        for position, side in enumerate(self.sides()):
            rows = side.normal(getattr(self.indexes[id(side)], call)(pair[position], side.db))
            for row in rows:
                for occurrence in row["occurrences"]:
                    occurrence["snippet"] = _CUT_ID.sub("<cut id>", occurrence["snippet"])
            out.append(sorted(rows, key=lambda row: row["title"]))
        self.assertEqual(out[0], out[1], call)
        return out[0]

    # -- tests ----------------------------------------------------------------------

    def test_the_graph_matches_with_and_without_people(self) -> None:
        self.network()
        graph = self.same_graph()
        types = sorted({edge["type"] for edge in graph["edges"]})
        self.assertEqual(types, ["argument_argument", "argument_position", "argument_source", "concept_parent",
                                 "mentions_argument", "mentions_concept"])
        self.assertEqual([node["label"] for node in graph["nodes"]],
                         ["Claim", "Reply", "Ethics", "Theory", "Unmentioned", "Thesis", "Commentary", "Source"])
        mentions = {(edge["source"], edge["target"]): edge["count"] for edge in graph["edges"]
                    if edge["type"].startswith("mentions_")}
        self.assertEqual(mentions, {
            ("work:<W Source>", "concept:<C Theory>"): 1, ("work:<W Source>", "concept:<C Ethics>"): 2,
            ("work:<W Source>", "argument:<A Claim>"): 1, ("work:<W Commentary>", "concept:<C Ethics>"): 1,
            ("work:<W Commentary>", "argument:<A Reply>"): 1, ("work:<W Commentary>", "argument:<A Claim>"): 1})
        source = next(edge for edge in graph["edges"] if edge["type"] == "argument_source")
        self.assertEqual(source["pages"], "1-2")
        people = self.same_graph(include_people=True)
        self.assertTrue(people["meta"]["people_included"])
        self.assertIn({"id": "person:<P Adorno>", "record_id": "<P Adorno>", "type": "person",
                       "label": "Theodor Adorno", "route": "#/people/<P Adorno>"}, people["nodes"])
        self.assertIn("work_author", {edge["type"] for edge in people["edges"]})

    def test_counts_backlinks_and_research_refs_match(self) -> None:
        ids = self.network()
        counts = self.same_read("concept_mention_counts")
        self.assertEqual(counts, {"<C Theory>": 1, "<C Ethics>": 3})
        self.same_read("mention_count_for_concept", ids["Ethics"])
        self.assertEqual(self.same_read("mention_count_for_concept", ids["Unmentioned"]), 0)
        self.same_read("mention_count_for_argument", ids["Claim"])
        backlinks = self.same_backlinks("concept_backlinks", ids["Ethics"])
        self.assertEqual(sorted(len(work["occurrences"]) for work in backlinks), [1, 2])
        self.same_backlinks("argument_backlinks", ids["Claim"])
        self.assertEqual(self.same_backlinks("argument_backlinks", ids["Reply"])[0]["title"], "Commentary")
        # SQLite also counts ``A-gone``, an id no Argument has; PostgreSQL counts live Arguments only.
        argument_counts = [{key: count for key, count in side.normal(self.indexes[id(side)].argument_mention_counts())
                            .items() if key.startswith("<")} for side in self.sides()]
        self.assertEqual(argument_counts[0], argument_counts[1])
        self.assertEqual(argument_counts[0], {"<A Claim>": 2, "<A Reply>": 1})
        for work in ("Source", "Commentary"):
            out = []
            for position, side in enumerate(self.sides()):
                refs = self.indexes[id(side)].work_research_refs(ids[work][position], side.db)
                if self.on_postgres(side):
                    self.assertIs(refs.pop("pending"), False)
                out.append(side.normal(refs))
            self.assertEqual(out[0], out[1], work)

    def test_a_deleted_work_and_a_dropped_reference_leave_the_reads(self) -> None:
        ids = self.network()
        self.delete_work(ids["Commentary"])
        self.assertEqual(self.same_read("concept_mention_counts"), {"<C Theory>": 1, "<C Ethics>": 2})
        self.assertEqual(len(self.same_backlinks("concept_backlinks", ids["Ethics"])), 1)
        self.assertEqual(self.same_backlinks("argument_backlinks", ids["Reply"]), [])
        self.assertNotIn("Commentary", [node["label"] for node in self.same_graph()["nodes"]])
        # A note that stops naming a Concept lets it go; it then leaves the graph too.
        self.note(ids["Source"], "Only [[concept:Ethics]].")
        for side, cid in zip(self.sides(), ids["Theory"]):
            side.db.delete_concept(cid)
            if not self.on_postgres(side):
                self.indexes[id(side)].remove_concept_mentions(cid)
        self.assertEqual(self.same_read("concept_mention_counts"), {"<C Ethics>": 1})
        self.assertNotIn("Theory", [node["label"] for node in self.same_graph()["nodes"]])

    def test_an_empty_library_has_an_empty_graph(self) -> None:
        graph = self.same_graph()
        self.assertEqual(graph, {"nodes": [], "edges": [], "meta": {
            "node_count": 0, "edge_count": 0, "derived_note_edges_available": True, "people_included": False}})
        self.assertEqual(self.same_read("concept_mention_counts"), {})

    def test_a_graph_past_the_bounds_is_refused_alike(self) -> None:
        self.network()
        errors = []
        with mock.patch.object(research_graph, "MAX_GRAPH_NODES", 3):
            for side in self.sides():
                with self.assertRaises(GraphTooLargeError) as caught:
                    self.graph(side)
                errors.append((caught.exception.code, caught.exception.http_status, caught.exception.extra))
        self.assertEqual(errors[0], errors[1])
        self.assertEqual(errors[0][:2], ("graph_too_large", 413))


class AdapterShapeTests(unittest.TestCase):
    def test_the_postgres_reads_keep_the_sqlite_index_signatures(self) -> None:
        from backend.pg_facade.research_index import PostgresResearchIndex

        mirrored = [name for name in vars(PostgresResearchIndex)
                    if not name.startswith("_") and name != "build_graph"]
        self.assertEqual(sorted(mirrored), [
            "argument_backlinks", "argument_mention_counts", "concept_backlinks", "concept_mention_counts",
            "mention_count_for_argument", "mention_count_for_concept", "work_research_refs"])
        for name in mirrored:
            mine = list(inspect.signature(getattr(PostgresResearchIndex, name)).parameters)
            theirs = list(inspect.signature(getattr(PRKSResearchIndex, name)).parameters)
            self.assertEqual(mine, theirs, name)
        self.assertEqual(list(inspect.signature(PostgresResearchIndex.build_graph).parameters),
                         ["self", "include_people"])
