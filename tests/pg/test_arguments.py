"""Positions, Arguments and Stances, their targets and citations on PostgreSQL: what parity cannot show (L-R-1 #634).

``tests/test_arguments_parity.py`` proves the facade returns SQLite's JSON.
These tests cover what only PostgreSQL has: refused creates that leave
nothing, scopes a delete moves on, citations pinned to a Version
(``docs/work-identity-model.md`` §8.5), legacy or Version-aware, and the
Work delete that removes them, Library isolation, and concurrent writers
(each race holds one transaction open until the other is waiting on it;
the merge race waits on events, never on time).
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from backend.db.errors import DatabaseError
from backend.domain.identity.authorization import AccessRefused
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.research import arguments, positions
from backend.domain.works import commands, merge, notes
from backend.entity_ids import generate
from backend.pg_facade import context as facade_context
from backend.research_network import ResearchError
from tests.pg.test_concepts import ConceptTestCase


class ResearchTestCase(ConceptTestCase):
    def position(self, name: str, description: str = "") -> str:
        return self.library.create_position(name, description)["id"]

    def argument(self, name: str, kind: str = "argument", **lists: Any) -> str:
        return self.library.create_argument(name=name, kind=kind, **lists)["id"]

    def primary(self, work_id: str) -> str:
        return self.sql("SELECT primary_manifestation_id AS id FROM prks.works WHERE id = %s", (work_id,))[0]["id"]

    def add_manifestation(self, work_id: str) -> str:
        manifestation_id = generate("MF")
        self.sql("INSERT INTO prks.manifestations (library_id, id, work_id) VALUES (%s, %s, %s)",
                 (self.actor.library_id, manifestation_id, work_id))
        return manifestation_id

    def citations(self, argument_id: str) -> list[tuple[int, str, Optional[str], str]]:
        rows = self.sql("SELECT order_index, work_id, manifestation_id, pages FROM prks.argument_sources"
                        " WHERE argument_id = %s ORDER BY order_index", (argument_id,))
        return [(row["order_index"], row["work_id"], row["manifestation_id"], row["pages"]) for row in rows]

    def cite(self, argument_id: str, order: int, work_id: str, manifestation_id: Optional[str],
             pages: str) -> None:
        self.sql("INSERT INTO prks.argument_sources (library_id, argument_id, order_index, work_id,"
                 " manifestation_id, pages) VALUES (%s, %s, %s, %s, %s, %s)",
                 (self.actor.library_id, argument_id, order, work_id, manifestation_id, pages))

    def argument_state(self, argument_id: str) -> dict[str, Any]:
        state = self.library.get_argument_sync_state(argument_id)
        assert state is not None
        return state


def target(kind: str, entity_id: str, verdict: str = "supports") -> dict[str, str]:
    return {"type": kind, "id": entity_id, "verdict_id": verdict}


def source(work_id: str, pages: str = "") -> dict[str, str]:
    return {"work_id": work_id, "pages": pages}


def pinned(work_id: str, manifestation_id: Any, pages: Any = "") -> dict[str, Any]:
    """A Version-aware entry; ``None`` cites the Work in general (malformed values are tests' to send)."""
    return {"work_id": work_id, "manifestation_id": manifestation_id, "pages": pages}


class PositionTests(ResearchTestCase):
    def test_create_read_update_and_list(self) -> None:
        claim = self.library.create_position("  A   claim ", "About")
        self.assertEqual((claim["name"], claim["description"], claim["arguments"]), ("A claim", "About", []))
        self.position("b claim")
        self.position("C claim")
        self.assertEqual([row["name"] for row in self.library.list_positions()], ["A claim", "b claim", "C claim"])
        self.library.update_position(claim["id"], name="A claim", description="About")
        self.assertEqual(self.revisions("position-field"), {})
        self.library.update_position(claim["id"], name="Renamed")
        self.assertEqual(self.library.get_position_sync_state(claim["id"]),
                         {"position_id": claim["id"],
                          "fields": {"description": {"revision": 0}, "name": {"revision": 1}}})
        for code, call, args, kwargs in (
                ("invalid_name", self.library.create_position, ("  ",), {}),
                ("invalid_name", self.library.create_position, ("a\x00b",), {}),
                ("invalid_name", self.library.create_position, ("x" * 241,), {}),
                ("invalid_text", self.library.create_position, ("Ok", "\x07"), {}),
                ("nothing_to_update", self.library.update_position, (claim["id"],), {}),
                ("not_found", self.library.update_position, (generate("P"),), {"name": "X"}),
                ("not_found", self.library.update_position, ("P-\x00",), {"name": "X"}),
                ("not_found", self.library.delete_position, ("P-\x00",), {})):
            with self.subTest(code=code, args=[str(arg)[:8] for arg in args]):
                self.refused(code, call, *args, **kwargs)
        self.assertIsNone(self.library.get_position("P-\x00"))
        self.assertEqual(self.count("positions"), 3)

    def test_a_targeted_position_refuses_its_delete_and_a_delete_moves_every_scope_on(self) -> None:
        claim = self.position("Claim")
        answer = self.argument("Answer", targets=[target("position", claim, "opposes")])
        self.assertEqual(self.library.get_position(claim)["arguments"],
                         [{"id": answer, "name": "Answer", "kind": "argument", "verdict_id": "opposes",
                           "verdict_label": "Opposes"}])
        self.assertEqual(self.refused("position_in_use", self.library.delete_position, claim).http_status, 409)
        self.library.replace_argument_targets(answer, [])
        self.library.delete_position(claim)
        self.assertIsNone(self.library.get_position(claim))
        # Never edited, and still each moved on, so no base outlives the delete.
        self.assertEqual(sorted(self.revisions("position-field").values()), [1, 1])
        self.refused("not_found", self.library.delete_position, claim)


class ArgumentTests(ResearchTestCase):
    def test_create_with_sources_and_targets_and_read(self) -> None:
        work = self.library.add_work("Republic")
        claim = self.position("Claim")
        base = self.argument("Base", "stance")
        reply = self.library.create_argument(
            name=" Reply ", kind="argument", main_text="Body",
            sources=[source(f" {work} ", " 514a ")],
            targets=[target("argument", base, "opposes"), target("position", claim)])
        self.assertEqual(reply["sources"], [{"work_id": work, "manifestation_id": self.primary(work), "pages": "514a",
                                             "order_index": 0, "work_title": "Republic", "authors": []}])
        self.assertEqual([(t["type"], t["id"], t["order_index"]) for t in reply["targets"]],
                         [("argument", base, 0), ("position", claim, 1)])
        self.assertEqual(self.library.get_argument(base)["responses"],
                         [{"id": reply["id"], "name": "Reply", "kind": "argument", "verdict_id": "opposes",
                           "verdict_label": "Opposes"}])
        self.assertEqual([row["id"] for row in self.library.list_arguments("stance")], [base])
        listed = {row["id"]: row for row in self.library.list_arguments()}
        self.assertEqual((listed[base]["response_count"], listed[reply["id"]]["sources"]),
                         (1, reply["sources"]))
        self.refused("invalid_kind", self.library.list_arguments, "essay")
        self.assertEqual(self.library.list_verdicts()[0], {"id": "supports", "label": "Supports",
                                                           "sort_order": 1, "enabled": 1})
        # Construction is not mutation.
        for scope_type in ("argument-field", "argument-sources", "argument-targets"):
            self.assertEqual(self.revisions(scope_type), {})

    def test_a_refused_create_leaves_nothing(self) -> None:
        work = self.library.add_work("W")
        claim = self.position("Claim")
        for code, lists in (("work_not_found", {"sources": [source(generate("W"))]}),
                            ("invalid_sources", {"sources": [source(work), source(work, "2")]}),
                            ("invalid_pages", {"sources": [source(work, "x" * 101)]}),
                            ("position_not_found", {"targets": [target("position", generate("P"))]}),
                            ("argument_not_found", {"targets": [target("argument", generate("A"))]}),
                            ("invalid_verdict", {"targets": [target("position", claim, "maybe")]}),
                            ("invalid_targets", {"targets": [target("position", claim)] * 2}),
                            ("invalid_targets", {"sources": [], "targets": "none"})):
            with self.subTest(code=code):
                self.refused(code, self.library.create_argument, name="X", kind="argument", **lists)
        self.assertEqual((self.count("arguments"), self.count("argument_sources")), (0, 0))

    def test_a_cycle_is_refused_at_any_depth(self) -> None:
        a = self.argument("A")
        b = self.argument("B", targets=[target("argument", a)])
        c = self.argument("C", targets=[target("argument", b)])
        self.refused("argument_cycle", self.library.replace_argument_targets, a, [target("argument", c)])
        self.refused("argument_cycle", self.library.replace_argument_targets, a, [target("argument", a)])
        self.refused("invalid_targets", self.library.replace_argument_targets, a, [target("argument", "A-x!")])
        # A diamond is no cycle.
        self.library.replace_argument_targets(c, [target("argument", b), target("argument", a, "qualifies")])
        self.assertEqual(self.library.get_argument(a)["responses"][0]["verdict_id"], "supports")
        self.assertEqual(self.count("argument_target_arguments"), 3)

    def test_only_a_change_advances_a_scope(self) -> None:
        w1, w2 = self.library.add_work("One"), self.library.add_work("Two")
        claim = self.position("Claim")
        a = self.argument("A", sources=[source(w1), source(w2, "3")], targets=[target("position", claim)])
        self.library.replace_argument_sources(a, [source(w1), source(w2, " 3 ")])
        self.library.replace_argument_targets(a, [target("position", claim, " supports ")])
        self.library.update_argument(a, name="A", kind="argument", main_text="")
        self.assertEqual((self.revisions("argument-sources"), self.revisions("argument-targets"),
                          self.revisions("argument-field")), ({}, {}, {}))
        self.library.replace_argument_sources(a, [source(w2, "3"), source(w1)])  # order is the value
        self.library.replace_argument_targets(a, [target("position", claim, "holds")])
        self.library.update_argument(a, kind="stance")
        state = self.argument_state(a)
        self.assertEqual((state["sources"], state["targets"], state["fields"]["kind"], state["fields"]["name"]),
                         ({"revision": 1}, {"revision": 1}, {"revision": 1}, {"revision": 0}))

    def test_a_refused_field_rolls_back_every_field(self) -> None:
        a = self.argument("A")
        self.refused("invalid_kind", self.library.update_argument, a, name="New", kind="essay")
        self.assertEqual((self.library.get_argument(a)["name"], self.revisions("argument-field")), ("A", {}))
        self.refused("nothing_to_update", self.library.update_argument, a)
        self.refused("not_found", self.library.update_argument, generate("A"))

    def test_an_answered_or_noted_argument_refuses_its_delete(self) -> None:
        a = self.argument("A")
        b = self.argument("B", targets=[target("argument", a)])
        work = self.library.add_work("W")
        self.assertEqual(self.refused("argument_targeted", self.library.delete_argument, a).http_status, 409)
        self.library.replace_argument_targets(b, [])
        self.research_note(work, f"Against [[argument:{a}|A]].")
        self.refused("argument_in_use", self.library.delete_argument, a)
        # Markup in code does not count; nor does a Private Note.
        self.research_note(work, f"`[[argument:{a}]]`")
        self.run_command(lambda scope: notes.set_note(scope, work, notes.PRIVATE, f"[[argument:{a}]]"))
        self.library.delete_argument(a)
        self.assertIsNone(self.library.get_argument(a))
        self.refused("not_found", self.library.delete_argument, a)
        # Every scope it owned moved on, never-edited ones too.
        self.assertEqual(self.revisions("argument-sources")[a], 1)
        self.assertEqual(self.revisions("argument-targets"), {a: 1, b: 1})
        self.assertEqual(sorted(k for k in self.revisions("argument-field") if a in k),
                         [f'["{a}","{field}"]' for field in ("kind", "main_text", "name")])


class CitationTests(ResearchTestCase):
    def test_a_citation_credits_the_authors_of_the_primary_version(self) -> None:
        # The Work's own credits (roles.credits_by_work): Work-scoped roles
        # and the primary Version's, never another Version's.
        work = self.library.add_work("W")
        other = self.add_manifestation(work)
        ada, bea = self.library.add_person("Ada", "A"), self.library.add_person("Bea", "B")
        for order, (person, manifestation) in enumerate(((ada, self.primary(work)), (bea, other))):
            self.sql("INSERT INTO prks.roles (library_id, work_id, person_id, role_type, order_index,"
                     " manifestation_id) VALUES (%s, %s, %s, 'Author', %s, %s)",
                     (self.actor.library_id, work, person, order, manifestation))
        a = self.argument("A", sources=[source(work)])
        self.assertEqual([author["id"] for author in self.library.get_argument(a)["sources"][0]["authors"]],
                         [ada])

    def test_a_pinpoint_pins_the_only_version_and_a_bare_entry_cites_the_work(self) -> None:
        work = self.library.add_work("W")
        a = self.argument("A", sources=[source(work, "12")])
        self.assertEqual(self.citations(a), [(0, work, self.primary(work), "12")])
        self.library.replace_argument_sources(a, [source(work)])
        self.assertEqual(self.citations(a), [(0, work, None, "")])

    def test_a_replaced_entry_keeps_its_version(self) -> None:
        w1, w2 = self.library.add_work("One"), self.library.add_work("Two")
        second = self.add_manifestation(w1)
        a = self.argument("A")
        self.cite(a, 0, w1, second, "12")
        self.library.replace_argument_sources(a, [source(w1, "12")])  # unchanged: nothing written
        self.assertEqual((self.citations(a), self.revisions("argument-sources")), ([(0, w1, second, "12")], {}))
        self.library.replace_argument_sources(a, [source(w2, "4"), source(w1, "12")])
        self.assertEqual(self.citations(a), [(0, w2, self.primary(w2), "4"), (1, w1, second, "12")])

    def test_a_version_the_legacy_list_cannot_name_refuses_the_list(self) -> None:
        work = self.library.add_work("W")
        second = self.add_manifestation(work)
        a = self.argument("A", sources=[source(work)])  # Work-level: no Version to name
        error = self.refused("argument_source_version_required", self.library.replace_argument_sources, a,
                             [source(work, "5")])
        self.assertEqual(error.http_status, 409)
        self.refused("argument_source_version_required", self.library.create_argument, name="B", kind="argument",
                     sources=[source(work, "5")])
        # Two stored rows the legacy shape cannot tell apart.
        b = self.argument("B")
        self.cite(b, 0, work, self.primary(work), "5")
        self.cite(b, 1, work, second, "5")
        self.refused("argument_source_version_required", self.library.replace_argument_sources, b,
                     [source(work, "5")])
        self.assertEqual(len(self.citations(b)), 2)
        self.library.replace_argument_sources(b, [])
        self.assertEqual(self.citations(b), [])

    def test_a_pinned_citation_follows_its_version_to_another_work(self) -> None:
        origin, other = self.library.add_work("Origin"), self.library.add_work("Other")
        moved = self.add_manifestation(origin)
        a = self.argument("A")
        self.cite(a, 0, origin, moved, "7")
        self.sql("UPDATE prks.manifestations SET work_id = %s WHERE id = %s", (other, moved))
        self.assertEqual(self.citations(a), [(0, other, moved, "7")])
        # The legacy list names it by its new Work, and keeps the pin.
        self.library.replace_argument_sources(a, [source(other, "7"), source(origin)])
        self.assertEqual(self.citations(a), [(0, other, moved, "7"), (1, origin, None, "")])

    def test_a_work_delete_removes_its_citations_and_advances_each_list(self) -> None:
        cited, kept = self.library.add_work("Cited"), self.library.add_work("Kept")
        a = self.argument("A", sources=[source(cited, "1"), source(kept)])
        b = self.argument("B", sources=[source(cited)])
        c = self.argument("C", sources=[source(kept)])
        self.library.delete_work_record(cited)
        self.assertEqual(self.citations(a), [(1, kept, None, "")])
        self.assertEqual(self.citations(b), [])
        self.assertEqual(self.revisions("argument-sources"), {a: 1, b: 1})
        self.assertEqual(self.library.get_argument(a)["sources"][0]["work_id"], kept)
        self.assertEqual(self.citations(c), [(0, kept, None, "")])

    def test_malformed_lists_are_refused_in_sqlites_order(self) -> None:
        work = self.library.add_work("W")
        a = self.argument("A")
        for code, sources in (("invalid_sources", "W"), ("invalid_sources", [work]),
                              ("invalid_sources", [{"pages": "1"}]), ("invalid_sources", [source("  ")]),
                              ("work_not_found", [source("W-\x00")]),
                              ("work_not_found", [source(generate("W"), "\x07")]),
                              ("invalid_pages", [source(work, "\x07")])):
            with self.subTest(code=code, sources=str(sources)[:20]):
                self.refused(code, self.library.replace_argument_sources, a, sources)
        self.refused("not_found", self.library.replace_argument_sources, generate("A"), "W")


class VersionAwareCitationTests(ResearchTestCase):
    """The Version-aware list ``[{work_id, manifestation_id, pages}]`` (§8.5)."""

    def read_back(self, argument_id: str) -> list[tuple[str, Optional[str], str]]:
        return [(row["work_id"], row["manifestation_id"], row["pages"])
                for row in self.library.get_argument(argument_id)["sources"]]

    def test_a_work_with_one_version(self) -> None:
        work = self.library.add_work("W")
        only = self.primary(work)
        a = self.argument("A", sources=[pinned(work, only, "12"), pinned(work, None, "514a")])
        self.assertEqual(self.citations(a), [(0, work, only, "12"), (1, work, None, "514a")])
        self.assertEqual(self.read_back(a), [(work, only, "12"), (work, None, "514a")])
        self.assertEqual(self.revisions("argument-sources"), {})  # construction advances nothing
        # The legacy list of one Work names the same rows.
        b = self.argument("B", sources=[source(work, "12")])
        self.assertEqual(self.read_back(b), [(work, only, "12")])

    def test_a_work_with_several_versions_is_cited_version_by_version(self) -> None:
        work = self.library.add_work("W")
        first, second = self.primary(work), self.add_manifestation(work)
        a = self.argument("A")
        wanted = [pinned(work, None), pinned(work, second, "5"), pinned(work, first, "5"), pinned(work, second, "9")]
        detail = self.library.replace_argument_sources(a, wanted)
        expected = [(work, None, ""), (work, second, "5"), (work, first, "5"), (work, second, "9")]
        self.assertEqual([(row["work_id"], row["manifestation_id"], row["pages"]) for row in detail["sources"]],
                         expected)
        self.assertEqual([row["order_index"] for row in detail["sources"]], [0, 1, 2, 3])
        self.assertEqual(self.revisions("argument-sources"), {a: 1})
        # The same list again changes nothing.
        self.library.replace_argument_sources(a, wanted)
        self.assertEqual(self.revisions("argument-sources"), {a: 1})
        self.assertEqual(self.read_back(a), expected)

    def test_a_non_primary_version_survives_a_change_of_pages_or_order(self) -> None:
        w1, w2 = self.library.add_work("One"), self.library.add_work("Two")
        second = self.add_manifestation(w1)
        a = self.argument("A", sources=[pinned(w1, second, "5"), pinned(w2, None)])
        self.library.replace_argument_sources(a, [pinned(w2, None), pinned(w1, second, "6-7")])
        self.assertEqual(self.citations(a), [(0, w2, None, ""), (1, w1, second, "6-7")])
        self.assertEqual(self.revisions("argument-sources"), {a: 1})
        # A legacy edit of the same list keeps the pin too.
        self.library.replace_argument_sources(a, [source(w1, "6-7"), source(w2)])
        self.assertEqual(self.citations(a), [(0, w1, second, "6-7"), (1, w2, None, "")])
        # Only the pin changing is a change.
        self.library.replace_argument_sources(a, [pinned(w1, self.primary(w1), "6-7"), pinned(w2, None)])
        self.assertEqual(self.citations(a), [(0, w1, self.primary(w1), "6-7"), (1, w2, None, "")])
        self.assertEqual(self.revisions("argument-sources"), {a: 3})

    def test_a_version_missing_or_of_another_work_or_library_refuses_the_list(self) -> None:
        w1, w2 = self.library.add_work("One"), self.library.add_work("Two")
        a = self.argument("A", sources=[pinned(w1, None, "1")])
        with facade_context.acting_as(self.other_actor().context):
            foreign_work = self.library.add_work("Theirs")
            foreign = self.sql("SELECT primary_manifestation_id AS id FROM prks.works WHERE id = %s",
                               (foreign_work,))[0]["id"]
        cases = (("argument_source_version_mismatch", 409, [pinned(w1, None), pinned(w2, self.primary(w1))]),
                 ("argument_source_version_not_found", 404, [pinned(w1, foreign)]),
                 ("argument_source_version_not_found", 404, [pinned(w1, generate("MF"))]),
                 ("work_not_found", 404, [pinned(foreign_work, foreign)]))
        for code, status, sources in cases:
            with self.subTest(code=code, sources=len(sources)):
                self.assertEqual(self.refused(code, self.library.replace_argument_sources, a, sources).http_status,
                                 status)
                self.refused(code, self.library.create_argument, name="B", kind="argument", sources=sources)
        # Nothing changed, nothing was left behind.
        self.assertEqual((self.citations(a), self.revisions("argument-sources")), ([(0, w1, None, "1")], {}))
        self.assertEqual(self.count("arguments"), 1)

    def test_malformed_version_aware_lists_are_refused(self) -> None:
        work = self.library.add_work("W")
        only = self.primary(work)
        a = self.argument("A")
        for message, sources in (
                ("Every source must name its Version, or none may.", [pinned(work, only), source(work, "2")]),
                ("A source's manifestation_id must be a Version id or null.", [pinned(work, 3)]),
                ("A source's manifestation_id must be a Version id or null.", [pinned(work, "  ")]),
                ("Duplicate citation.", [pinned(work, only, "2"), pinned(work, only, " 2 ")]),
                ("Duplicate citation.", [pinned(work, None), pinned(work, None)])):
            with self.subTest(message=message):
                self.assertEqual(str(self.refused("invalid_sources", self.library.replace_argument_sources, a,
                                                  sources)), message)
        self.refused("invalid_pages", self.library.replace_argument_sources, a, [pinned(work, only, "\x07")])
        self.assertEqual(self.citations(a), [])

    def test_a_legacy_list_cannot_rewrite_a_list_citing_one_work_twice(self) -> None:
        work = self.library.add_work("W")
        second = self.add_manifestation(work)
        a = self.argument("A", sources=[pinned(work, None), pinned(work, second, "3")])
        self.refused("invalid_sources", self.library.replace_argument_sources, a, [source(work), source(work, "3")])
        # The unambiguous legacy entry still resolves: the stored row it matches keeps its pin.
        self.library.replace_argument_sources(a, [source(work, "3")])
        self.assertEqual(self.citations(a), [(0, work, second, "3")])
        self.refused("argument_source_version_required", self.library.replace_argument_sources, a,
                     [source(work, "4")])
        self.library.replace_argument_sources(a, [])
        self.assertEqual(self.citations(a), [])

    def test_a_pinned_version_cannot_be_deleted_and_follows_a_move(self) -> None:
        origin, other = self.library.add_work("Origin"), self.library.add_work("Other")
        moved = self.add_manifestation(origin)
        a = self.argument("A", sources=[pinned(origin, moved, "7")])
        with self.assertRaises(DatabaseError) as caught:
            self.sql("DELETE FROM prks.manifestations WHERE id = %s", (moved,))
        self.assertEqual((caught.exception.sqlstate, caught.exception.constraint),
                         ("23503", "argument_sources_manifestation_fkey"))
        self.sql("UPDATE prks.manifestations SET work_id = %s WHERE id = %s", (other, moved))
        self.assertEqual(self.read_back(a), [(other, moved, "7")])
        # A list naming the Version under its old Work is refused; under its new one it is unchanged.
        self.refused("argument_source_version_mismatch", self.library.replace_argument_sources, a,
                     [pinned(origin, moved, "7")])
        self.library.replace_argument_sources(a, [pinned(other, moved, "7")])
        self.assertEqual(self.revisions("argument-sources"), {})


class IsolationTests(ResearchTestCase):
    def test_another_librarys_records_are_invisible_and_untouchable(self) -> None:
        work = self.library.add_work("W")
        claim = self.position("Claim")
        a = self.argument("A", sources=[source(work)], targets=[target("position", claim)])
        with facade_context.acting_as(self.other_actor().context):
            self.assertEqual((self.library.list_positions(), self.library.list_arguments()), ([], []))
            self.assertIsNone(self.library.get_argument(a))
            self.assertIsNone(self.library.get_position_sync_state(claim))
            self.refused("work_not_found", self.library.create_argument, name="B", kind="argument",
                         sources=[source(work)])
            self.refused("position_not_found", self.library.create_argument, name="B", kind="argument",
                         targets=[target("position", claim)])
            self.refused("argument_not_found", self.library.create_argument, name="B", kind="argument",
                         targets=[target("argument", a)])
            for call, args in (("delete_argument", (a,)), ("delete_position", (claim,)),
                               ("replace_argument_sources", (a, [])), ("replace_argument_targets", (a, []))):
                with self.subTest(call=call):
                    self.refused("not_found", getattr(self.library, call), *args)
        self.assertEqual(len(self.library.get_argument(a)["targets"]), 1)

    def test_a_reader_reads_but_cannot_change(self) -> None:
        a = self.argument("A")
        with facade_context.acting_as(self.reader_context()):
            self.assertEqual(len(self.library.list_arguments()), 1)
            for call, args, kwargs in (("create_position", ("P",), {}), ("delete_argument", (a,), {}),
                                       ("create_argument", (), {"name": "B", "kind": "stance"})):
                with self.subTest(call=call), self.assertRaises(AccessRefused):
                    getattr(self.library, call)(*args, **kwargs)


class ConcurrencyTests(ResearchTestCase):
    def test_two_target_changes_that_together_close_a_cycle(self) -> None:
        a, b = self.argument("A"), self.argument("B")
        holder, contender = self.race(lambda scope: arguments.replace_targets(scope, a, [target("argument", b)]),
                                      lambda: self.library.replace_argument_targets(b, [target("argument", a)]))
        self.assertEqual(len(holder["targets"]), 1)
        self.assertIsInstance(contender, ResearchError)
        self.assertEqual(contender.code, "argument_cycle")
        self.assertEqual(self.count("argument_target_arguments"), 1)

    def test_a_position_delete_racing_a_target_naming_it(self) -> None:
        claim, a = self.position("Claim"), self.argument("A")
        holder, contender = self.race(lambda scope: positions.remove(scope, claim),
                                      lambda: self.library.replace_argument_targets(a, [target("position", claim)]))
        self.assertIsNone(holder)
        self.assertEqual(contender.code, "position_not_found")

    def test_a_target_racing_a_delete_of_the_position_it_names(self) -> None:
        claim, a = self.position("Claim"), self.argument("A")
        holder, contender = self.race(
            lambda scope: arguments.replace_targets(scope, a, [target("position", claim)]),
            lambda: self.library.delete_position(claim))
        self.assertEqual(len(holder["targets"]), 1)
        self.assertEqual(contender.code, "position_in_use")

    def test_an_argument_delete_and_a_target_naming_it(self) -> None:
        a, b = self.argument("A"), self.argument("B")
        holder, contender = self.race(lambda scope: arguments.replace_targets(scope, b, [target("argument", a)]),
                                      lambda: self.library.delete_argument(a))
        self.assertEqual(contender.code, "argument_targeted")
        c = self.argument("C")
        holder, contender = self.race(lambda scope: arguments.remove(scope, c),
                                      lambda: self.library.replace_argument_targets(b, [target("argument", c)]))
        self.assertIsNone(holder)
        self.assertEqual(contender.code, "argument_not_found")

    def test_a_work_delete_racing_a_citation_of_it(self) -> None:
        work, a = self.library.add_work("W"), self.argument("A")
        holder, contender = self.race(lambda scope: commands.delete_work(scope, work),
                                      lambda: self.library.replace_argument_sources(a, [source(work)]))
        self.assertIsNotNone(holder)
        self.assertEqual(contender.code, "work_not_found")
        self.assertEqual(self.citations(a), [])

    def test_a_citation_racing_a_delete_of_its_work(self) -> None:
        work, a = self.library.add_work("W"), self.argument("A")
        holder, contender = self.race(lambda scope: arguments.replace_sources(scope, a, [source(work)]),
                                      lambda: self.library.delete_work_record(work), serializable=True)
        self.assertEqual(len(holder["sources"]), 1)
        self.assertIsNotNone(contender)
        # The delete waited, then removed the citation and advanced the list.
        self.assertEqual((self.citations(a), self.revisions("argument-sources")), ([], {a: 2}))

    def test_a_list_change_racing_a_work_delete_that_drops_its_old_citation(self) -> None:
        old, new = self.library.add_work("Old"), self.library.add_work("New")
        a = self.argument("A", sources=[source(old)])
        holder, contender = self.race(lambda scope: arguments.replace_sources(scope, a, [source(new)]),
                                      lambda: self.library.delete_work_record(old), serializable=True)
        self.assertEqual(holder["sources"][0]["work_id"], new)
        self.assertIsNotNone(contender)
        self.assertEqual((self.citations(a), self.revisions("argument-sources")), ([(0, new, None, "")], {a: 1}))

    def test_a_version_move_waits_for_the_citation_pinning_it(self) -> None:
        origin, other = self.library.add_work("Origin"), self.library.add_work("Other")
        moved = self.add_manifestation(origin)
        a = self.argument("A")
        holder, contender = self.race(
            lambda scope: arguments.replace_sources(scope, a, [pinned(origin, moved, "7")]),
            lambda: self.sql("UPDATE prks.manifestations SET work_id = %s WHERE id = %s", (other, moved)),
            serializable=True)
        self.assertEqual(holder["sources"][0]["manifestation_id"], moved)
        self.assertEqual(contender, [])
        # The move waited, then carried the citation with it.
        self.assertEqual(self.citations(a), [(0, other, moved, "7")])

    def test_a_citation_racing_a_move_of_its_version(self) -> None:
        origin, other = self.library.add_work("Origin"), self.library.add_work("Other")
        moved = self.add_manifestation(origin)
        a = self.argument("A")
        holder, contender = self.race(
            lambda scope: scope.execute("UPDATE prks.manifestations SET work_id = %(other)s"
                                        " WHERE library_id = %(library_id)s AND id = %(moved)s",
                                        {"other": other, "moved": moved}),
            lambda: self.library.replace_argument_sources(a, [pinned(origin, moved, "7")]))
        self.assertIsNotNone(holder)
        self.assertIsInstance(contender, ResearchError)
        self.assertEqual(contender.code, "argument_source_version_mismatch")
        self.assertEqual((self.citations(a), self.revisions("argument-sources")), ([], {}))

    def test_a_version_delete_waits_for_the_citation_pinning_it_and_is_refused(self) -> None:
        work = self.library.add_work("W")
        second = self.add_manifestation(work)
        a = self.argument("A")
        holder, contender = self.race(
            lambda scope: arguments.replace_sources(scope, a, [pinned(work, second, "1")]),
            lambda: self.sql("DELETE FROM prks.manifestations WHERE id = %s", (second,)),
            serializable=True)
        self.assertEqual(len(holder["sources"]), 1)
        self.assertIsInstance(contender, DatabaseError)
        self.assertEqual((contender.sqlstate, contender.constraint), ("23503", "argument_sources_manifestation_fkey"))
        self.assertEqual(self.citations(a), [(0, work, second, "1")])

    def test_two_version_aware_lists_of_one_argument_queue(self) -> None:
        work = self.library.add_work("W")
        second = self.add_manifestation(work)
        a = self.argument("A")
        holder, contender = self.race(
            lambda scope: arguments.replace_sources(scope, a, [pinned(work, second, "1")]),
            lambda: self.library.replace_argument_sources(a, [pinned(work, None), pinned(work, second, "1")]),
            serializable=True)
        self.assertEqual(len(holder["sources"]), 1)
        self.assertEqual(len(contender["sources"]), 2)
        self.assertEqual((self.citations(a), self.revisions("argument-sources")),
                         ([(0, work, None, ""), (1, work, second, "1")], {a: 2}))

    def test_a_citation_after_a_merge_snapshot_fails_the_merge_and_its_retry_sees_it(self) -> None:
        # §7.1: argument_sources is a peer table of the merge, and its writers are SERIALIZABLE.
        source_work, target_work = self.library.add_work("S"), self.library.add_work("T")
        second = self.add_manifestation(source_work)
        a = self.argument("A")
        snapshot_taken, cited = threading.Event(), threading.Event()
        plans: list[merge.MergePlan] = []

        def run(scope: LibraryScope) -> None:
            if not plans:
                snapshot_taken.set()
                cited.wait(30)
            plans.append(merge.prepare(scope, source_work, target_work))
            scope.execute("UPDATE prks.works SET updated_at = now() WHERE library_id = %(library_id)s"
                          " AND id = %(work_id)s", {"work_id": source_work})

        thread = threading.Thread(target=lambda: self.run_command(run, serializable=True))
        thread.start()
        try:
            self.assertTrue(snapshot_taken.wait(30))
            self.library.replace_argument_sources(a, [pinned(source_work, second, "3")])
        finally:
            cited.set()
            thread.join(60)
        self.assertEqual(len(plans), 2, "the first attempt must fail with 40001 and be retried")
        self.assertEqual((plans[0].citing_arguments, plans[1].citing_arguments), ((), (a,)))

    def test_an_argument_delete_and_a_research_note_naming_it(self) -> None:
        a, work = self.argument("A"), self.library.add_work("W")
        holder, contender = self.race(
            lambda scope: notes.set_note(scope, work, notes.RESEARCH, f"[[argument:{a}]]"),
            lambda: self.library.delete_argument(a))
        self.assertEqual(holder, (True, 1))
        self.assertEqual(contender.code, "argument_in_use")
        b = self.argument("B")
        holder, contender = self.race(
            lambda scope: arguments.remove(scope, b),
            lambda: self.run_command(lambda scope: notes.set_note(scope, work, notes.RESEARCH,
                                                                  f"[[argument:{b}]]")))
        self.assertIsNone(holder)
        # The note waited for the delete; whether it may name a gone
        # Argument is the notes slice's rule.
        self.assertEqual(contender, (True, 2))
        self.assertIsNone(self.library.get_argument(b))
