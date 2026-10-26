"""Constraints of the Research schema (L-R-1 #634; ``130_research.sql``).

Each rule the schema owns is shown refused and its legal path shown to
commit: the one name-and-alias namespace of Concepts (``concept_keys``),
including under concurrency and across a rename, the Concept hierarchy,
Positions and Arguments with their targets and verdicts, Argument citations
with the #60 §8.5 identity, the derived mention tables, and Library
isolation of every reference. Keys of rows that own revision scopes never
cascade a delete, except the listed ones, whose owner's delete tombstones
their scopes.
"""

from __future__ import annotations

from typing import Any, Optional

import psycopg

from tests.pg.test_organization_schema import Conn, OrganizationSchemaTestCase
from tests.pg.test_people_schema import new_id


class ResearchSchemaTestCase(OrganizationSchemaTestCase):
    # -- Concepts ----------------------------------------------------------

    def create_concept(self, name: str, *, lib: Optional[str] = None, conn: Conn = None) -> str:
        """A Concept and its name's key row, in the caller's transaction."""
        concept_id = new_id("C")
        self.sql("INSERT INTO prks.concepts (library_id, id, name) VALUES (%s, %s, %s)",
                 (lib or self.lib, concept_id, name), conn)
        self.sql("INSERT INTO prks.concept_keys (library_id, key, concept_id) "
                 "SELECT library_id, name_key, id FROM prks.concepts WHERE id = %s", (concept_id,), conn)
        return concept_id

    def add_concept_alias(self, concept_id: str, alias: str, conn: Conn = None) -> None:
        self.sql("INSERT INTO prks.concept_aliases (library_id, concept_id, alias) "
                 "SELECT library_id, id, %s FROM prks.concepts WHERE id = %s", (alias, concept_id), conn)
        self.sql("INSERT INTO prks.concept_keys (library_id, key, concept_id, alias_key) "
                 "SELECT library_id, prks.identity_key(%s), id, prks.identity_key(%s) FROM prks.concepts "
                 "WHERE id = %s", (alias, alias, concept_id), conn)

    def concept(self, name: str, *aliases: str, lib: Optional[str] = None) -> str:
        with self.tx():
            concept_id = self.create_concept(name, lib=lib)
            for alias in aliases:
                self.add_concept_alias(concept_id, alias)
        return concept_id

    def concept_keys(self, concept_id: str) -> list[tuple[str, str]]:
        return [(row[0], row[1]) for row in self.sql(
            "SELECT kind, key FROM prks.concept_keys WHERE concept_id = %s ORDER BY kind, key",
            (concept_id,)).fetchall()]

    def edge(self, child: str, parent: str, lib: Optional[str] = None) -> None:
        self.sql("INSERT INTO prks.concept_parents (library_id, child_concept_id, parent_concept_id) "
                 "VALUES (%s, %s, %s)", (lib or self.lib, child, parent))

    # -- Positions and Arguments --------------------------------------------

    def position(self, name: str = "Position", *, lib: Optional[str] = None) -> str:
        position_id = new_id("P")
        self.sql("INSERT INTO prks.positions (library_id, id, name) VALUES (%s, %s, %s)",
                 (lib or self.lib, position_id, name))
        return position_id

    def argument(self, name: str = "Argument", kind: str = "argument", *, lib: Optional[str] = None) -> str:
        argument_id = new_id("A")
        self.sql("INSERT INTO prks.arguments (library_id, id, name, kind) VALUES (%s, %s, %s, %s)",
                 (lib or self.lib, argument_id, name, kind))
        return argument_id

    def target_position(self, argument_id: str, position_id: str, verdict: str = "supports",
                        lib: Optional[str] = None) -> None:
        self.sql("INSERT INTO prks.argument_target_positions (library_id, argument_id, position_id, verdict_id) "
                 "VALUES (%s, %s, %s, %s)", (lib or self.lib, argument_id, position_id, verdict))

    def target_argument(self, argument_id: str, target_id: str, verdict: str = "opposes",
                        lib: Optional[str] = None) -> None:
        self.sql("INSERT INTO prks.argument_target_arguments (library_id, argument_id, target_argument_id, "
                 "verdict_id) VALUES (%s, %s, %s, %s)", (lib or self.lib, argument_id, target_id, verdict))

    def cite(self, argument_id: str, order: int, work_id: str, manifestation_id: Optional[str] = None,
             pages: str = "", lib: Optional[str] = None) -> None:
        self.sql("INSERT INTO prks.argument_sources (library_id, argument_id, order_index, work_id, "
                 "manifestation_id, pages) VALUES (%s, %s, %s, %s, %s, %s)",
                 (lib or self.lib, argument_id, order, work_id, manifestation_id, pages))


class ConceptTests(ResearchSchemaTestCase):
    def test_a_concept_has_a_distributed_id_and_a_canonical_bounded_name(self) -> None:
        concept_id = self.concept("Being")
        self.assertEqual(self.value("SELECT description FROM prks.concepts WHERE id = %s", (concept_id,)), "")
        for name in ("", " Lead", "Trail ", "Two  spaces", "Tab\there", "No break", "Café",
                     "x" * 161):
            with self.subTest(name=name):
                self.assert_refused(psycopg.errors.CheckViolation, lambda name=name: self.create_concept(name))
        # The bound is 160 characters, not bytes.
        self.concept("é" * 160)
        self.refused(psycopg.errors.CheckViolation,
                     ("INSERT INTO prks.concepts (library_id, id, name) VALUES (%s, 'C-1234ABCD', 'Legacy')",
                      (self.lib,)))
        self.refused(psycopg.errors.CheckViolation,
                     ("UPDATE prks.concepts SET description = %s WHERE id = %s", ("x" * 100_001, concept_id)))
        self.refused(psycopg.errors.CheckViolation,
                     ("UPDATE prks.concepts SET description = %s WHERE id = %s", ("bell\x07", concept_id)))
        self.sql("UPDATE prks.concepts SET description = %s WHERE id = %s", ("Line\nTab\t", concept_id))

    def test_a_name_and_its_aliases_hold_one_key_each(self) -> None:
        concept_id = self.concept("Ἀρετή", "Врлина", "Vrlina")
        self.assertEqual(self.concept_keys(concept_id),
                         [("alias", "vrlina"), ("alias", "врлина"), ("name", "ἀρετή")])
        for alias in (" Lead", "Two  spaces", "x" * 161):
            with self.subTest(alias=alias):
                self.assert_refused(psycopg.errors.CheckViolation,
                                    lambda alias=alias: self.add_concept_alias(concept_id, alias))

    def test_a_name_or_alias_without_its_key_row_fails_at_commit(self) -> None:
        self.refused_at_commit(psycopg.errors.ForeignKeyViolation,
                               ("INSERT INTO prks.concepts (library_id, id, name) VALUES (%s, %s, 'Unkeyed')",
                                (self.lib, new_id("C"))))
        owner = self.concept("Owner")
        self.refused_at_commit(psycopg.errors.ForeignKeyViolation,
                               ("INSERT INTO prks.concept_aliases (library_id, concept_id, alias) "
                                "VALUES (%s, %s, 'Unkeyed')", (self.lib, owner)))

    def test_names_and_aliases_never_share_a_key(self) -> None:
        virtue = self.concept("Virtue", "Excellence")
        other = self.concept("Врлина")
        for name in ("VIRTUE", "excellence", "ВРЛИНА"):
            with self.subTest(name=name):
                self.assert_refused(psycopg.errors.UniqueViolation, lambda name=name: self.create_concept(name))
        for alias in ("virtue", "EXCELLENCE", "врлина"):
            with self.subTest(alias=alias):
                self.assert_refused(psycopg.errors.UniqueViolation,
                                    lambda alias=alias: self.add_concept_alias(other, alias))
        # An alias equal to its own Concept's name is refused too: one namespace.
        self.assert_refused(psycopg.errors.UniqueViolation, lambda: self.add_concept_alias(virtue, "VIRTUE"))
        # Full case folding and NFC follow the shared key; diacritics are kept;
        # each Library is its own namespace.
        self.concept("Straße")
        self.assert_refused(psycopg.errors.UniqueViolation, lambda: self.create_concept("STRASSE"))
        self.concept("Cafe")
        self.concept("Café")
        self.concept("Virtue", lib=self.library())

    def test_a_key_row_is_held_by_exactly_one_name_or_alias(self) -> None:
        first = self.concept("First", "Alias")
        second = self.concept("Second")
        self.refused(psycopg.errors.UniqueViolation,
                     ("INSERT INTO prks.concept_keys (library_id, key, concept_id) VALUES (%s, 'spare', %s)",
                      (self.lib, first)))
        # An alias key row names an alias of its own Concept.
        self.refused(psycopg.errors.ForeignKeyViolation,
                     ("INSERT INTO prks.concept_keys (library_id, key, concept_id, alias_key) "
                      "VALUES (%s, 'alias2', %s, 'alias2')", (self.lib, second)))
        self.refused(psycopg.errors.CheckViolation,
                     ("INSERT INTO prks.concept_keys (library_id, key, concept_id, alias_key) "
                      "VALUES (%s, 'other', %s, 'alias')", (self.lib, first)))
        self.refused_at_commit(psycopg.errors.ForeignKeyViolation,
                               ("UPDATE prks.concept_keys SET key = 'stale' WHERE concept_id = %s "
                                "AND alias_key IS NULL", (second,)))

    def test_a_rename_moves_the_name_key_and_keeps_the_old_name_as_an_alias(self) -> None:
        concept_id = self.concept("Old Name", "Kept")
        with self.tx():
            # The rename command: the name and its key row, then the old name as an alias.
            self.sql("UPDATE prks.concepts SET name = 'New Name' WHERE id = %s", (concept_id,))
            self.sql("UPDATE prks.concept_keys SET key = 'new name' WHERE concept_id = %s AND alias_key IS NULL",
                     (concept_id,))
            self.add_concept_alias(concept_id, "Old Name")
        self.assertEqual(self.concept_keys(concept_id),
                         [("alias", "kept"), ("alias", "old name"), ("name", "new name")])
        self.assert_refused(psycopg.errors.UniqueViolation, lambda: self.create_concept("OLD NAME"))
        # A rename that forgets its key row fails at COMMIT.
        self.refused_at_commit(psycopg.errors.ForeignKeyViolation,
                               ("UPDATE prks.concepts SET name = 'Newer' WHERE id = %s", (concept_id,)))

    def test_a_delete_frees_every_key_but_not_while_in_the_hierarchy(self) -> None:
        parent, child = self.concept("Parent"), self.concept("Child", "Kid")
        self.edge(child, parent)
        for concept_id in (parent, child):
            with self.subTest(concept=concept_id):
                self.refused(psycopg.errors.ForeignKeyViolation,
                             ("DELETE FROM prks.concepts WHERE id = %s", (concept_id,)))
        with self.tx():
            self.sql("DELETE FROM prks.concept_parents WHERE child_concept_id = %s", (child,))
            self.sql("DELETE FROM prks.concepts WHERE id = %s", (child,))
        self.assertEqual((self.concept_keys(child), self.count("concept_aliases", "concept_id", child)), ([], 0))
        self.concept("CHILD", "kid")

    def test_the_hierarchy_has_no_self_edge_no_duplicate_and_stays_in_its_library(self) -> None:
        parent, child = self.concept("Parent"), self.concept("Child")
        theirs = self.concept("Theirs", lib=self.library())
        self.refused(psycopg.errors.CheckViolation,
                     ("INSERT INTO prks.concept_parents (library_id, child_concept_id, parent_concept_id) "
                      "VALUES (%s, %s, %s)", (self.lib, child, child)))
        self.edge(child, parent)
        self.refused(psycopg.errors.UniqueViolation,
                     ("INSERT INTO prks.concept_parents (library_id, child_concept_id, parent_concept_id) "
                      "VALUES (%s, %s, %s)", (self.lib, child, parent)))
        self.edge(parent, child)  # a longer cycle is the commands' to refuse
        self.refused(psycopg.errors.ForeignKeyViolation,
                     ("INSERT INTO prks.concept_parents (library_id, child_concept_id, parent_concept_id) "
                      "VALUES (%s, %s, %s)", (self.lib, child, theirs)))
        self.refused(psycopg.errors.ForeignKeyViolation,
                     ("INSERT INTO prks.concept_aliases (library_id, concept_id, alias) VALUES (%s, %s, 'A')",
                      (self.lib, theirs)))


class ConceptKeyConcurrencyTests(ResearchSchemaTestCase):
    """A Concept name and another Concept's alias never share a key under concurrency (§7.6)."""

    def assert_one_holder(self, key: str) -> None:
        self.assertEqual(self.value("SELECT count(*) FROM prks.concept_keys WHERE library_id = %s AND key = %s",
                                    (self.lib, key)), 1)

    def test_a_name_and_a_concurrent_alias_of_another_concept(self) -> None:
        other = self.concept("Other")
        errors = self.race(lambda conn: self.create_concept("Ἀρετή", conn=conn),
                           lambda conn: self.add_concept_alias(other, "ἈΡΕΤΉ", conn=conn))
        self.assertEqual([type(error) for error in errors], [psycopg.errors.UniqueViolation])
        self.assert_one_holder("ἀρετή")
        self.assertEqual(self.count("concept_aliases", "concept_id", other), 0)

    def test_an_alias_and_a_concurrent_concept_of_that_name(self) -> None:
        other = self.concept("Other")
        errors = self.race(lambda conn: self.add_concept_alias(other, "Врлина", conn=conn),
                           lambda conn: self.create_concept("ВРЛИНА", conn=conn))
        self.assertEqual([type(error) for error in errors], [psycopg.errors.UniqueViolation])
        self.assert_one_holder("врлина")
        self.assertEqual(self.count("concepts", "library_id", self.lib), 1)

    def test_two_concurrent_concepts_of_one_name(self) -> None:
        errors = self.race(lambda conn: self.create_concept("Logos", conn=conn),
                           lambda conn: self.create_concept("LOGOS", conn=conn))
        self.assertEqual([type(error) for error in errors], [psycopg.errors.UniqueViolation])
        self.assert_one_holder("logos")


class PositionArgumentTests(ResearchSchemaTestCase):
    def test_names_kinds_and_text_are_bounded(self) -> None:
        for table, prefix, bound, extra in (("positions", "P", 240, ""), ("arguments", "A", 200, "argument")):
            columns = "library_id, id, name" + (", kind" if extra else "")
            values = "%s, %s, %s" + (", %s" if extra else "")

            def insert(name: str, row_id: str = "", table: str = table, prefix: str = prefix,
                       columns: str = columns, values: str = values, extra: str = extra) -> None:
                params: tuple[Any, ...] = (self.lib, row_id or new_id(prefix), name) + ((extra,) if extra else ())
                self.sql(f"INSERT INTO prks.{table} ({columns}) VALUES ({values})", params)

            with self.subTest(table=table):
                insert("é" * bound)
                insert("Ordinary name")
                for name in ("", " Lead", "Two  spaces", "x" * (bound + 1)):
                    self.assert_refused(psycopg.errors.CheckViolation, lambda name=name: insert(name))
                self.assert_refused(psycopg.errors.CheckViolation, lambda prefix=prefix: insert("Legacy", f"{prefix}-1234ABCD"))
        self.refused(psycopg.errors.CheckViolation,
                     ("INSERT INTO prks.arguments (library_id, id, name, kind) VALUES (%s, %s, 'A', 'claim')",
                      (self.lib, new_id("A"))))
        argument_id = self.argument(kind="stance")
        self.refused(psycopg.errors.CheckViolation,
                     ("UPDATE prks.arguments SET main_text = %s WHERE id = %s", ("x" * 100_001, argument_id)))

    def test_the_verdicts_are_sqlites_and_a_target_names_one(self) -> None:
        self.assertEqual([tuple(row) for row in self.sql(
            "SELECT id, label, sort_order, enabled FROM prks.argument_verdicts ORDER BY sort_order")],
            [("supports", "Supports", 1, True), ("opposes", "Opposes", 2, True),
             ("qualifies", "Qualifies", 3, True), ("holds", "Holds", 4, True)])
        argument_id, position_id = self.argument(), self.position()
        self.refused(psycopg.errors.ForeignKeyViolation,
                     ("INSERT INTO prks.argument_target_positions (library_id, argument_id, position_id, verdict_id) "
                      "VALUES (%s, %s, %s, 'refutes')", (self.lib, argument_id, position_id)))

    def test_a_target_refuses_its_delete_and_the_list_follows_its_argument(self) -> None:
        argument_id, answered, position_id = self.argument(), self.argument("Answered"), self.position()
        self.target_position(argument_id, position_id)
        self.target_argument(argument_id, answered)
        self.refused(psycopg.errors.ForeignKeyViolation, ("DELETE FROM prks.positions WHERE id = %s", (position_id,)))
        self.refused(psycopg.errors.ForeignKeyViolation, ("DELETE FROM prks.arguments WHERE id = %s", (answered,)))
        self.sql("DELETE FROM prks.arguments WHERE id = %s", (argument_id,))
        self.assertEqual((self.count("argument_target_positions", "argument_id", argument_id),
                          self.count("argument_target_arguments", "argument_id", argument_id)), (0, 0))
        self.sql("DELETE FROM prks.positions WHERE id = %s", (position_id,))
        self.sql("DELETE FROM prks.arguments WHERE id = %s", (answered,))

    def test_targets_are_unique_never_self_and_stay_in_their_library(self) -> None:
        argument_id, position_id, answered = self.argument(), self.position(), self.argument("Answered")
        other = self.library()
        their_position, their_argument = self.position(lib=other), self.argument(lib=other)
        self.target_position(argument_id, position_id)
        self.refused(psycopg.errors.UniqueViolation,
                     ("INSERT INTO prks.argument_target_positions (library_id, argument_id, position_id, verdict_id) "
                      "VALUES (%s, %s, %s, 'opposes')", (self.lib, argument_id, position_id)))
        self.refused(psycopg.errors.CheckViolation,
                     ("INSERT INTO prks.argument_target_arguments (library_id, argument_id, target_argument_id, "
                      "verdict_id) VALUES (%s, %s, %s, 'opposes')", (self.lib, argument_id, argument_id)))
        self.target_argument(answered, argument_id)
        self.target_argument(argument_id, answered)  # a longer cycle is the commands' to refuse
        for library_id in (self.lib, other):
            with self.subTest(library=library_id):
                self.assertRaises(psycopg.errors.ForeignKeyViolation,
                                  self.target_position, argument_id, their_position, lib=library_id)
                self.assertRaises(psycopg.errors.ForeignKeyViolation,
                                  self.target_argument, argument_id, their_argument, lib=library_id)


class ArgumentSourceTests(ResearchSchemaTestCase):
    def manifestation(self, work_id: str) -> str:
        manifestation_id = new_id("MF")
        self.sql("INSERT INTO prks.manifestations (library_id, id, work_id) VALUES (%s, %s, %s)",
                 (self.lib, manifestation_id, work_id))
        return manifestation_id

    def test_a_work_and_several_of_its_versions_may_each_be_cited_once_per_pinpoint(self) -> None:
        argument_id = self.argument()
        work_id, primary = self.work()
        other = self.manifestation(work_id)
        self.cite(argument_id, 0, work_id, pages="514a")
        self.cite(argument_id, 1, work_id)
        self.cite(argument_id, 2, work_id, primary, "45")
        self.cite(argument_id, 3, work_id, other, "45")
        self.cite(argument_id, 4, work_id, other, "46")
        for order, manifestation_id, pages in ((5, None, "514a"), (5, None, ""), (5, other, "45")):
            with self.subTest(manifestation=manifestation_id, pages=pages):
                self.assertRaises(psycopg.errors.UniqueViolation,
                                  self.cite, argument_id, order, work_id, manifestation_id, pages)
        self.assertRaises(psycopg.errors.UniqueViolation, self.cite, argument_id, 0, work_id, None, "1")
        for pages in (" 1", "x" * 101, "bell\x07"):
            with self.subTest(pages=pages):
                self.assertRaises(psycopg.errors.CheckViolation, self.cite, argument_id, 9, work_id, None, pages)
        self.assertRaises(psycopg.errors.CheckViolation, self.cite, argument_id, -1, work_id, None, "2")

    def test_a_pinned_version_is_one_of_the_cited_works_and_every_reference_stays_in_its_library(self) -> None:
        argument_id = self.argument()
        work_id, _ = self.work()
        _, elsewhere = self.work()
        other = self.library()
        their_work, their_manifestation = self.work(lib=other)
        self.assertRaises(psycopg.errors.ForeignKeyViolation, self.cite, argument_id, 0, work_id, elsewhere)
        self.assertRaises(psycopg.errors.ForeignKeyViolation, self.cite, argument_id, 0, their_work)
        self.assertRaises(psycopg.errors.ForeignKeyViolation,
                          self.cite, argument_id, 0, their_work, their_manifestation, "", other)
        self.assertRaises(psycopg.errors.ForeignKeyViolation, self.cite, self.argument(lib=other), 0, work_id)

    def test_a_cited_work_and_a_pinned_version_refuse_their_delete(self) -> None:
        argument_id = self.argument()
        work_id, primary = self.work()
        extra = self.manifestation(work_id)
        self.cite(argument_id, 0, work_id)
        self.cite(argument_id, 1, work_id, extra, "3")
        self.refused(psycopg.errors.ForeignKeyViolation, ("DELETE FROM prks.manifestations WHERE id = %s", (extra,)))
        delete = (("DELETE FROM prks.manifestations WHERE work_id = %s", (work_id,)),
                  ("DELETE FROM prks.works WHERE id = %s", (work_id,)))
        self.refused(psycopg.errors.ForeignKeyViolation, *delete)
        with self.tx():
            self.sql("DELETE FROM prks.argument_sources WHERE work_id = %s", (work_id,))
            for statement, params in delete:
                self.sql(statement, params)
        self.assertEqual(self.count("works", "id", work_id) + self.count("manifestations", "id", primary), 0)

    def test_a_pinned_citation_follows_its_version_to_another_work(self) -> None:
        argument_id = self.argument()
        source, _ = self.work()
        target, _ = self.work()
        moved = self.manifestation(source)
        self.cite(argument_id, 0, source, moved, "12")
        self.sql("UPDATE prks.manifestations SET work_id = %s WHERE id = %s", (target, moved))
        self.assertEqual(self.value("SELECT work_id FROM prks.argument_sources WHERE argument_id = %s",
                                    (argument_id,)), target)

    def test_the_list_follows_its_argument(self) -> None:
        argument_id = self.argument()
        work_id, _ = self.work()
        self.cite(argument_id, 0, work_id)
        self.sql("DELETE FROM prks.arguments WHERE id = %s", (argument_id,))
        self.assertEqual(self.count("argument_sources", "argument_id", argument_id), 0)


class DerivedMentionTests(ResearchSchemaTestCase):
    TABLES = ("work_note_state", "concept_mentions", "argument_mentions")

    def test_mentions_live_in_derived_with_no_key_to_canonical_rows(self) -> None:
        for table in self.TABLES:
            with self.subTest(table=table):
                self.assertIsNotNone(self.value("SELECT to_regclass(%s)", (f"derived.{table}",)))
                self.assertIsNone(self.value("SELECT to_regclass(%s)", (f"prks.{table}",)))
                self.assertEqual(self.value("SELECT count(*) FROM pg_constraint WHERE contype = 'f' "
                                            "AND conrelid = %s::regclass", (f"derived.{table}",)), 0)

    def test_a_mention_is_one_span_per_start_and_the_state_one_hash_per_work(self) -> None:
        work_id, concept_id = new_id("W"), new_id("C")
        insert = ("INSERT INTO derived.concept_mentions (library_id, work_id, concept_id, start_offset, end_offset) "
                  "VALUES (%s, %s, %s, %s, %s)")
        self.sql(insert, (self.lib, work_id, concept_id, 0, 12))
        self.refused(psycopg.errors.UniqueViolation, (insert, (self.lib, work_id, new_id("C"), 0, 9)))
        for start, end in ((-1, 3), (5, 5), (6, 5)):
            with self.subTest(start=start, end=end):
                self.refused(psycopg.errors.CheckViolation, (insert, (self.lib, work_id, concept_id, start, end)))
        state = "INSERT INTO derived.work_note_state (library_id, work_id, content_hash) VALUES (%s, %s, %s)"
        self.sql(state, (self.lib, work_id, "abc"))
        self.refused(psycopg.errors.UniqueViolation, (state, (self.lib, work_id, "def")))
        self.refused(psycopg.errors.CheckViolation, (state, (self.lib, new_id("W"), "")))


class RevisionOwnershipTests(ResearchSchemaTestCase):
    # Tables whose rows advance a revision scope when they change.
    SCOPE_OWNERS = ("concepts", "concept_aliases", "concept_keys", "concept_parents", "positions", "arguments",
                    "argument_target_positions", "argument_target_arguments", "argument_sources")
    # A Concept's aliases and key rows (`concept-identity`) and an Argument's
    # targets and sources (`argument-targets`, `argument-sources`) follow
    # their owner, whose delete tombstones those scopes.
    LISTED = {
        ("prks.concept_aliases", "concept_aliases_concept_fkey"),
        ("prks.concept_keys", "concept_keys_concept_fkey"),
        ("prks.concept_keys", "concept_keys_alias_fkey"),
        ("prks.argument_target_positions", "argument_target_positions_argument_fkey"),
        ("prks.argument_target_arguments", "argument_target_arguments_argument_fkey"),
        ("prks.argument_sources", "argument_sources_argument_fkey"),
    }

    def test_only_the_listed_keys_cascade(self) -> None:
        rows = self.sql(
            "SELECT conrelid::regclass::text, conname FROM pg_constraint "
            "WHERE contype = 'f' AND confdeltype IN ('c', 'n', 'd') AND conrelid = ANY(%s::regclass[])",
            ([f"prks.{table}" for table in self.SCOPE_OWNERS],)).fetchall()
        self.assertEqual({(row[0], row[1]) for row in rows}, self.LISTED)
