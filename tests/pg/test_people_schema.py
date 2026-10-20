"""Constraints of the People schema (L-P-1 #632; ``110_people.sql``).

Each rule the schema owns is shown refused and its legal path shown to
commit: Persons and their alias rows, roles ordered without ``rowid``,
Person Groups unique by identity key, and the one name-and-alias namespace
of publishers (``publisher_keys``), including under concurrency. Keys of
rows that own revision scopes never cascade a delete, except the listed role
cascade, which the Work delete tombstones.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Optional

import psycopg

from tests.pg.harness import PostgresTestCase, fetch_one
from tests.pg.test_works_facade import WorksFacadeTestCase


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex.upper()}"


class PeopleSchemaTestCase(PostgresTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.conn = self.connect(autocommit=True)
        self.lib = self.library()

    # -- helpers -----------------------------------------------------------

    def sql(self, statement: str, params: Any = None, conn: Optional[psycopg.Connection[Any]] = None
            ) -> psycopg.Cursor[Any]:
        return (conn or self.conn).execute(statement.encode(), params)

    @contextmanager
    def tx(self) -> Iterator[None]:
        with self.conn.transaction():
            yield

    def refused(self, error: type[psycopg.Error], *statements: tuple[str, Any]) -> None:
        """The statements fail with ``error``, at a statement or at COMMIT, and change nothing."""
        with self.assertRaises(error):
            with self.tx():
                for statement, params in statements:
                    self.sql(statement, params)

    def refused_at_commit(self, error: type[psycopg.Error], *statements: tuple[str, Any]) -> None:
        """Each statement runs; only COMMIT refuses (a deferred constraint)."""
        reached = False
        with self.assertRaises(error):
            with self.tx():
                for statement, params in statements:
                    self.sql(statement, params)
                reached = True
        self.assertTrue(reached)

    def library(self) -> str:
        library_id = "LIB-" + uuid.uuid4().hex.upper()
        self.sql("INSERT INTO prks.libraries (id, name) VALUES (%s, 'L')", (library_id,))
        return library_id

    def value(self, statement: str, params: Any = None) -> Any:
        return fetch_one(self.sql(statement, params))[0]

    def count(self, table: str, column: str, value: str) -> int:
        return int(self.value(f"SELECT count(*) FROM prks.{table} WHERE {column} = %s", (value,)))

    def person(self, lib: Optional[str] = None, last_name: str = "Person") -> str:
        person_id = new_id("P")
        self.sql("INSERT INTO prks.persons (library_id, id, last_name) VALUES (%s, %s, %s)",
                 (lib or self.lib, person_id, last_name))
        return person_id

    def work(self, lib: Optional[str] = None) -> tuple[str, str]:
        """A committed Work with its primary Manifestation."""
        work_id, manifestation_id = new_id("W"), new_id("MF")
        with self.tx():
            self.sql("INSERT INTO prks.works (library_id, id, title, primary_manifestation_id) "
                     "VALUES (%s, %s, 'Title', %s)", (lib or self.lib, work_id, manifestation_id))
            self.sql("INSERT INTO prks.manifestations (library_id, id, work_id) VALUES (%s, %s, %s)",
                     (lib or self.lib, manifestation_id, work_id))
        return work_id, manifestation_id

    def group(self, name: str, *, parent_id: Optional[str] = None, lib: Optional[str] = None) -> str:
        group_id = new_id("PG")
        self.sql("INSERT INTO prks.person_groups (library_id, id, name, parent_id) VALUES (%s, %s, %s, %s)",
                 (lib or self.lib, group_id, name, parent_id))
        return group_id


INSERT_ROLE = ("INSERT INTO prks.roles (library_id, work_id, person_id, role_type, order_index, "
               "credit_name, manifestation_id) VALUES (%s, %s, %s, %s, %s, %s, %s)")


class PersonTests(PeopleSchemaTestCase):
    def test_a_person_has_a_distributed_id_and_no_null_profile_field(self) -> None:
        person_id = self.person()
        row = fetch_one(self.sql("SELECT first_name, about, image_url, birth_date FROM prks.persons "
                                 "WHERE id = %s", (person_id,)))
        self.assertEqual(tuple(row), ("", "", "", ""))
        for bad in ("P-1234ABCD", "P-" + "a" * 32, "PG-" + "A" * 32):
            with self.subTest(id=bad):
                self.refused(psycopg.errors.CheckViolation,
                             ("INSERT INTO prks.persons (library_id, id, last_name) VALUES (%s, %s, 'X')",
                              (self.lib, bad)))
        self.refused(psycopg.errors.NotNullViolation,
                     ("UPDATE prks.persons SET about = NULL WHERE id = %s", (person_id,)))


class PersonAliasTests(PeopleSchemaTestCase):
    def alias(self, person_id: str, position: int, alias: str, lib: Optional[str] = None) -> tuple[str, Any]:
        return ("INSERT INTO prks.person_aliases (library_id, person_id, position, alias) VALUES (%s, %s, %s, %s)",
                (lib or self.lib, person_id, position, alias))

    def aliases(self, person_id: str) -> list[str]:
        return [row[0] for row in self.sql("SELECT alias FROM prks.person_aliases WHERE person_id = %s "
                                           "ORDER BY position", (person_id,)).fetchall()]

    def test_aliases_are_ordered_rows(self) -> None:
        person_id = self.person()
        with self.tx():
            for position, alias in enumerate(("Mark Twain", "Марк Твен", "Μαρκ Τουαίην")):
                self.sql(*self.alias(person_id, position, alias))
        self.assertEqual(self.aliases(person_id), ["Mark Twain", "Марк Твен", "Μαρκ Τουαίην"])

    def test_one_spelling_per_person_and_shared_spellings_across_people(self) -> None:
        first, second = self.person(), self.person()
        self.sql(*self.alias(first, 0, "Σωκράτης"))
        for variant in ("ΣΩΚΡΆΤΗΣ", "  σωκράτης ", "Σωκράτης"):
            with self.subTest(variant=variant):
                self.refused(psycopg.errors.UniqueViolation, self.alias(first, 1, variant))
        # Different identity: diacritics are not folded (§8.1).
        self.sql(*self.alias(first, 1, "Sokrates"))
        self.sql(*self.alias(first, 2, "Sókrates"))
        # Two people may share an alias; aliases are not a namespace.
        self.sql(*self.alias(second, 0, "σωκράτης"))
        self.assertEqual(self.aliases(second), ["σωκράτης"])

    def test_an_alias_is_one_alias_and_not_empty(self) -> None:
        person_id = self.person()
        for bad in ("Smith, John", "", "   "):
            with self.subTest(alias=bad):
                self.refused(psycopg.errors.CheckViolation, self.alias(person_id, 0, bad))

    def test_a_rewrite_may_renumber_and_commits_once_positions_are_unique(self) -> None:
        person_id = self.person()
        with self.tx():
            self.sql(*self.alias(person_id, 0, "A"))
            self.sql(*self.alias(person_id, 1, "B"))
        with self.tx():
            self.sql("UPDATE prks.person_aliases SET position = 1 - position WHERE person_id = %s", (person_id,))
        self.assertEqual(self.aliases(person_id), ["B", "A"])
        self.refused_at_commit(psycopg.errors.UniqueViolation,
                               ("UPDATE prks.person_aliases SET position = 0 WHERE person_id = %s", (person_id,)))

    def test_aliases_go_with_their_person_and_stay_in_its_library(self) -> None:
        person_id = self.person()
        self.sql(*self.alias(person_id, 0, "A"))
        self.refused(psycopg.errors.ForeignKeyViolation, self.alias(person_id, 1, "B", lib=self.library()))
        self.sql("DELETE FROM prks.persons WHERE id = %s", (person_id,))
        self.assertEqual(self.count("person_aliases", "person_id", person_id), 0)


class RoleTests(PeopleSchemaTestCase):
    def role(self, work_id: str, person_id: str, order_index: int, *, role_type: str = "Author",
             credit_name: Optional[str] = None, manifestation_id: Optional[str] = None,
             lib: Optional[str] = None) -> tuple[str, Any]:
        return (INSERT_ROLE, (lib or self.lib, work_id, person_id, role_type, order_index, credit_name,
                              manifestation_id))

    def ordered(self, work_id: str) -> list[tuple[str, str]]:
        return [(row[0], row[1]) for row in self.sql(
            "SELECT person_id, role_type FROM prks.roles WHERE work_id = %s ORDER BY order_index",
            (work_id,)).fetchall()]

    def test_one_row_per_work_person_and_role(self) -> None:
        work_id, _ = self.work()
        person_id = self.person()
        self.sql(*self.role(work_id, person_id, 0))
        self.refused(psycopg.errors.UniqueViolation, self.role(work_id, person_id, 1))
        self.sql(*self.role(work_id, person_id, 1, role_type="Editor"))
        self.assertEqual(self.ordered(work_id), [(person_id, "Author"), (person_id, "Editor")])

    def test_role_vocabulary_and_credit_contract(self) -> None:
        work_id, _ = self.work()
        person_id = self.person()
        for role_type in ("author", "Contributor", ""):
            with self.subTest(role_type=role_type):
                self.refused(psycopg.errors.CheckViolation, self.role(work_id, person_id, 0, role_type=role_type))
        # NULL is "no override"; "" would be a second spelling of it.
        for credit in ("", "x" * 501, "é" * 251):
            with self.subTest(credit=len(credit)):
                self.refused(psycopg.errors.CheckViolation, self.role(work_id, person_id, 0, credit_name=credit))
        self.sql(*self.role(work_id, person_id, 0, credit_name="é" * 250))
        self.refused(psycopg.errors.CheckViolation, self.role(work_id, self.person(), -1))

    def test_order_is_total_without_rowid(self) -> None:
        work_id, _ = self.work()
        first, second = self.person(), self.person()
        self.sql(*self.role(work_id, first, 0))
        # A tie within the Work fails at COMMIT, so a renumbering can pass through one.
        self.refused_at_commit(psycopg.errors.UniqueViolation, self.role(work_id, second, 0, role_type="Editor"))
        self.sql(*self.role(work_id, second, 1, role_type="Editor"))
        with self.tx():
            self.sql("UPDATE prks.roles SET order_index = 1 - order_index WHERE work_id = %s", (work_id,))
        self.assertEqual(self.ordered(work_id), [(second, "Editor"), (first, "Author")])
        # The same index on another Work is a different order.
        other, _ = self.work()
        self.sql(*self.role(other, first, 0))

    def test_a_role_stays_in_its_library_and_its_work(self) -> None:
        work_id, _ = self.work()
        other_work, other_manifestation = self.work()
        lib_b = self.library()
        stranger = self.person(lib=lib_b)
        self.refused(psycopg.errors.ForeignKeyViolation, self.role(work_id, stranger, 0))
        self.refused(psycopg.errors.ForeignKeyViolation, self.role(work_id, self.person(), 0, lib=lib_b))
        # A Manifestation-scoped credit names a Manifestation of the same Work.
        self.refused(psycopg.errors.ForeignKeyViolation,
                     self.role(work_id, self.person(), 0, manifestation_id=other_manifestation))
        self.assertEqual(self.count("roles", "work_id", other_work), 0)

    def test_a_credited_person_cannot_be_deleted(self) -> None:
        work_id, _ = self.work()
        person_id = self.person()
        self.sql(*self.role(work_id, person_id, 0))
        self.refused(psycopg.errors.ForeignKeyViolation,
                     ("DELETE FROM prks.persons WHERE id = %s", (person_id,)))
        self.assertEqual(self.count("persons", "id", person_id), 1)

    def test_a_manifestation_credit_follows_its_manifestation_to_another_work(self) -> None:
        work_id, _ = self.work()
        target, _ = self.work()
        sibling = new_id("MF")
        self.sql("INSERT INTO prks.manifestations (library_id, id, work_id) VALUES (%s, %s, %s)",
                 (self.lib, sibling, work_id))
        person_id = self.person()
        self.sql(*self.role(work_id, person_id, 3, manifestation_id=sibling))
        self.sql("UPDATE prks.manifestations SET work_id = %s WHERE id = %s", (target, sibling))
        self.assertEqual(self.value("SELECT work_id FROM prks.roles WHERE person_id = %s", (person_id,)), target)

    def test_roles_go_with_their_work(self) -> None:
        work_id, primary = self.work()
        sibling = new_id("MF")
        self.sql("INSERT INTO prks.manifestations (library_id, id, work_id) VALUES (%s, %s, %s)",
                 (self.lib, sibling, work_id))
        whole, scoped = self.person(), self.person()
        self.sql(*self.role(work_id, whole, 0))
        self.sql(*self.role(work_id, scoped, 1, manifestation_id=sibling))
        with self.tx():  # the Work delete's levels (backend/domain/works/commands.py)
            self.sql("DELETE FROM prks.manifestations WHERE work_id = %s", (work_id,))
            self.sql("DELETE FROM prks.works WHERE id = %s", (work_id,))
        self.assertEqual(self.count("roles", "work_id", work_id), 0)
        self.assertEqual(self.count("persons", "id", whole) + self.count("persons", "id", scoped), 2)
        self.assertEqual(self.count("manifestations", "id", primary), 0)


class PersonGroupTests(PeopleSchemaTestCase):
    def test_group_names_are_unique_by_identity_key(self) -> None:
        self.group("Франкфуртска школа")
        self.group("Σχολή της Φρανκφούρτης")
        for variant in ("ФРАНКФУРТСКА ШКОЛА", " франкфуртска   школа ", "ΣΧΟΛΉ ΤΗΣ ΦΡΑΝΚΦΟΎΡΤΗΣ",
                        "σχολή της φρανκφούρτησ"):
            with self.subTest(variant=variant):
                self.refused(psycopg.errors.UniqueViolation,
                             ("INSERT INTO prks.person_groups (library_id, id, name) VALUES (%s, %s, %s)",
                              (self.lib, new_id("PG"), variant)))
        # Diacritics and scripts are not folded: these are other names (§8.1).
        self.group("Frankfurtska škola")
        self.group("Frankfurtska skola")
        # Names are unique per Library.
        self.group("Франкфуртска школа", lib=self.library())

    def test_a_rename_into_a_taken_key_is_refused(self) -> None:
        self.group("Vienna Circle")
        group_id = self.group("Berlin Circle")
        self.refused(psycopg.errors.UniqueViolation,
                     ("UPDATE prks.person_groups SET name = 'VIENNA circle' WHERE id = %s", (group_id,)))
        self.sql("UPDATE prks.person_groups SET name = 'BERLIN circle' WHERE id = %s", (group_id,))

    def test_name_and_description_bounds(self) -> None:
        for name in ("", " ", "x" * 201, "ж" * 101):
            with self.subTest(name=len(name)):
                self.refused(psycopg.errors.CheckViolation,
                             ("INSERT INTO prks.person_groups (library_id, id, name) VALUES (%s, %s, %s)",
                              (self.lib, new_id("PG"), name)))
        self.group("ж" * 100)
        self.refused(psycopg.errors.CheckViolation,
                     ("INSERT INTO prks.person_groups (library_id, id, name, description) VALUES (%s, %s, 'D', %s)",
                      (self.lib, new_id("PG"), "x" * 2001)))

    def test_parents_stay_in_the_library_and_are_not_the_group(self) -> None:
        parent = self.group("Parent")
        child = self.group("Child", parent_id=parent)
        self.refused(psycopg.errors.CheckViolation,
                     ("UPDATE prks.person_groups SET parent_id = id WHERE id = %s", (child,)))
        foreign = self.group("Foreign", lib=self.library())
        self.refused(psycopg.errors.ForeignKeyViolation,
                     ("UPDATE prks.person_groups SET parent_id = %s WHERE id = %s", (foreign, child)))

    def test_a_delete_reparents_children_and_removes_members_first(self) -> None:
        grandparent = self.group("Grandparent")
        parent = self.group("Parent", parent_id=grandparent)
        child = self.group("Child", parent_id=parent)
        person_id = self.person()
        self.sql("INSERT INTO prks.person_group_members (library_id, group_id, person_id) VALUES (%s, %s, %s)",
                 (self.lib, parent, person_id))
        delete = ("DELETE FROM prks.person_groups WHERE id = %s", (parent,))
        self.refused(psycopg.errors.ForeignKeyViolation, delete)
        self.refused(psycopg.errors.ForeignKeyViolation,
                     ("UPDATE prks.person_groups SET parent_id = %s WHERE id = %s", (grandparent, child)), delete)
        with self.tx():  # the group delete's order: reparent, remove members, delete
            self.sql("UPDATE prks.person_groups SET parent_id = %s WHERE id = %s", (grandparent, child))
            self.sql("DELETE FROM prks.person_group_members WHERE group_id = %s", (parent,))
            self.sql(*delete)
        self.assertEqual(self.value("SELECT parent_id FROM prks.person_groups WHERE id = %s", (child,)), grandparent)

    def test_members_stay_in_the_library_and_block_a_person_delete(self) -> None:
        group_id = self.group("Group")
        person_id = self.person()
        insert = ("INSERT INTO prks.person_group_members (library_id, group_id, person_id) VALUES (%s, %s, %s)")
        self.refused(psycopg.errors.ForeignKeyViolation, (insert, (self.lib, group_id, self.person(lib=self.library()))))
        self.sql(insert, (self.lib, group_id, person_id))
        self.refused(psycopg.errors.UniqueViolation, (insert, (self.lib, group_id, person_id)))
        self.refused(psycopg.errors.ForeignKeyViolation, ("DELETE FROM prks.persons WHERE id = %s", (person_id,)))
        self.sql("DELETE FROM prks.person_group_members WHERE person_id = %s", (person_id,))
        self.sql("DELETE FROM prks.persons WHERE id = %s", (person_id,))


class RevisionOwnershipTests(PeopleSchemaTestCase):
    """§13.2: no key of a scope-owning People row cascades a delete, except the listed role keys."""

    SCOPE_OWNERS = ("persons", "roles", "person_groups", "person_group_members")
    # Listed with the command that tombstones them: the Work delete advances every scope
    # whose id starts with the Work's id, and a role scope is [work_id, person_id, role_type].
    LISTED = {("prks.roles", "roles_manifestation_fkey"), ("prks.roles", "roles_work_fkey")}

    def test_only_the_listed_role_keys_cascade(self) -> None:
        rows = self.sql(
            "SELECT conrelid::regclass::text, conname FROM pg_constraint "
            "WHERE contype = 'f' AND confdeltype IN ('c', 'n', 'd') AND conrelid = ANY(%s::regclass[])",
            ([f"prks.{table}" for table in self.SCOPE_OWNERS],)).fetchall()
        self.assertEqual({(row[0], row[1]) for row in rows}, self.LISTED)


class PublisherTestCase(PeopleSchemaTestCase):
    """A publisher name and every alias share one key namespace (``publisher_keys``)."""

    def create(self, name: str, *, lib: Optional[str] = None,
               conn: Optional[psycopg.Connection[Any]] = None) -> str:
        """A publisher and its name's key row, in the caller's transaction."""
        publisher_id = new_id("R")
        self.sql("INSERT INTO prks.publishers (library_id, id, name) VALUES (%s, %s, %s)",
                 (lib or self.lib, publisher_id, name), conn)
        self.sql("INSERT INTO prks.publisher_keys (library_id, key, publisher_id) "
                 "SELECT library_id, name_key, id FROM prks.publishers WHERE id = %s", (publisher_id,), conn)
        return publisher_id

    def add_alias(self, publisher_id: str, alias: str, conn: Optional[psycopg.Connection[Any]] = None) -> str:
        alias_id = new_id("M")
        self.sql("INSERT INTO prks.publisher_aliases (library_id, id, publisher_id, alias) "
                 "SELECT library_id, %s, id, %s FROM prks.publishers WHERE id = %s",
                 (alias_id, alias, publisher_id), conn)
        self.sql("INSERT INTO prks.publisher_keys (library_id, key, publisher_id, alias_id) "
                 "SELECT library_id, alias_key, publisher_id, id FROM prks.publisher_aliases WHERE id = %s",
                 (alias_id,), conn)
        return alias_id

    def publisher(self, name: str, *aliases: str) -> str:
        with self.tx():
            publisher_id = self.create(name)
            for alias in aliases:
                self.add_alias(publisher_id, alias)
        return publisher_id

    def keys(self, publisher_id: str) -> list[tuple[str, str]]:
        return [(row[0], row[1]) for row in self.sql(
            "SELECT kind, key FROM prks.publisher_keys WHERE publisher_id = %s ORDER BY kind, key",
            (publisher_id,)).fetchall()]

    def assert_refused(self, error: type[psycopg.Error], body: Callable[[], object]) -> None:
        with self.assertRaises(error):
            with self.tx():
                body()



class PublisherTests(PublisherTestCase):
    def test_a_name_and_its_aliases_hold_one_key_each(self) -> None:
        publisher_id = self.publisher("Éditions Lumière", "Lumière", "Издательство Свет")
        self.assertEqual(self.keys(publisher_id), [
            ("alias", "lumière"), ("alias", "издательство свет"), ("name", "éditions lumière")])

    def test_a_name_without_its_key_row_fails_at_commit(self) -> None:
        self.refused_at_commit(psycopg.errors.ForeignKeyViolation,
                               ("INSERT INTO prks.publishers (library_id, id, name) VALUES (%s, %s, 'P')",
                                (self.lib, new_id("R"))))
        publisher_id = self.publisher("Penguin")
        alias_id = new_id("M")
        self.refused_at_commit(psycopg.errors.ForeignKeyViolation,
                               ("INSERT INTO prks.publisher_aliases (library_id, id, publisher_id, alias) "
                                "VALUES (%s, %s, %s, 'Penguin Books')", (self.lib, alias_id, publisher_id)))

    def test_names_and_aliases_never_share_a_key(self) -> None:
        penguin = self.publisher("Penguin", "Penguin Books")
        other = self.publisher("Издательство")
        for name in ("PENGUIN", "penguin books", "ИЗДАТЕЛЬСТВО"):
            with self.subTest(name=name):
                self.assert_refused(psycopg.errors.UniqueViolation, lambda name=name: self.create(name))
        for alias in ("penguin", "Penguin  Books", "издательство"):
            with self.subTest(alias=alias):
                self.assert_refused(psycopg.errors.UniqueViolation,
                                    lambda alias=alias: self.add_alias(other, alias))
        # An alias equal to its own publisher's name is refused too: one namespace.
        self.assert_refused(psycopg.errors.UniqueViolation, lambda: self.add_alias(penguin, "PENGUIN"))
        # Greek final sigma and capital sigma are one key.
        self.publisher("Εκδόσεις Κέδρος")
        self.assert_refused(psycopg.errors.UniqueViolation, lambda: self.create("ΕΚΔΌΣΕΙΣ ΚΈΔΡΟΣ"))
        # Diacritics are not folded, and each Library is its own namespace.
        self.publisher("Penguín")
        with self.tx():
            self.create("Penguin", lib=self.library())

    def test_a_key_row_is_held_by_exactly_one_name_or_alias(self) -> None:
        first = self.publisher("First", "Alias")
        second = self.publisher("Second")
        alias_id = self.value("SELECT id FROM prks.publisher_aliases WHERE publisher_id = %s", (first,))
        # A second name row for a publisher.
        self.refused(psycopg.errors.UniqueViolation,
                     ("INSERT INTO prks.publisher_keys (library_id, key, publisher_id) VALUES (%s, 'spare', %s)",
                      (self.lib, first)))
        # An alias row claiming another publisher's alias.
        unkeyed = new_id("M")
        self.refused(psycopg.errors.ForeignKeyViolation,
                     ("INSERT INTO prks.publisher_aliases (library_id, id, publisher_id, alias) "
                      "VALUES (%s, %s, %s, 'Spare')", (self.lib, unkeyed, first)),
                     ("INSERT INTO prks.publisher_keys (library_id, key, publisher_id, alias_id) "
                      "VALUES (%s, 'spare', %s, %s)", (self.lib, second, unkeyed)))
        # A second key row for one alias.
        self.refused(psycopg.errors.UniqueViolation,
                     ("INSERT INTO prks.publisher_keys (library_id, key, publisher_id, alias_id) "
                      "VALUES (%s, 'spare', %s, %s)", (self.lib, first, alias_id)))
        # A key row that no longer matches its name or alias fails at commit.
        self.refused_at_commit(psycopg.errors.ForeignKeyViolation,
                               ("UPDATE prks.publisher_keys SET key = 'stale' WHERE publisher_id = %s "
                                "AND alias_id IS NULL", (second,)))
        self.refused_at_commit(psycopg.errors.ForeignKeyViolation,
                               ("UPDATE prks.publisher_keys SET key = 'stale' WHERE alias_id = %s", (alias_id,)))

    def test_a_rename_moves_its_key_row_in_the_same_transaction(self) -> None:
        publisher_id = self.publisher("Old Name")
        rename = ("UPDATE prks.publishers SET name = 'New Name' WHERE id = %s", (publisher_id,))
        self.refused_at_commit(psycopg.errors.ForeignKeyViolation, rename)
        with self.tx():
            self.sql(*rename)
            self.sql("UPDATE prks.publisher_keys k SET key = p.name_key FROM prks.publishers p "
                     "WHERE p.id = %s AND k.publisher_id = p.id AND k.alias_id IS NULL", (publisher_id,))
        self.assertEqual(self.keys(publisher_id), [("name", "new name")])
        # The old key is free again.
        self.publisher("OLD NAME")

    def test_a_delete_frees_every_key(self) -> None:
        publisher_id = self.publisher("Gone", "Gone Press")
        alias_id = self.value("SELECT id FROM prks.publisher_aliases WHERE publisher_id = %s", (publisher_id,))
        self.sql("DELETE FROM prks.publisher_aliases WHERE id = %s", (alias_id,))
        self.assertEqual(self.keys(publisher_id), [("name", "gone")])
        self.publisher("Gone Press")
        self.sql("DELETE FROM prks.publishers WHERE id = %s", (publisher_id,))
        self.assertEqual(self.keys(publisher_id), [])
        self.publisher("GONE")


class PublisherKeyConcurrencyTests(PublisherTestCase):
    """A publisher name and another publisher's alias never share a key under concurrency (§7.6)."""

    def wait_for_lock_waiter(self) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.value("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                          "AND wait_event_type = 'Lock'"):
                return
            time.sleep(0.02)
        self.fail("the second writer never waited on the key")

    def race(self, first: Callable[[psycopg.Connection[Any]], object],
             second: Callable[[psycopg.Connection[Any]], object]) -> list[BaseException]:
        """``first`` holds its key uncommitted while ``second`` tries the same key; then ``first`` commits."""
        holder, contender = self.connect(), self.connect()
        errors: list[BaseException] = []

        def run_second() -> None:
            try:
                with contender.transaction():
                    second(contender)
            except BaseException as exc:  # noqa: BLE001 - reported to the test thread
                errors.append(exc)

        with holder.transaction():
            first(holder)
            thread = threading.Thread(target=run_second)
            thread.start()
            self.wait_for_lock_waiter()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        return errors

    def assert_one_holder(self, key: str) -> None:
        self.assertEqual(self.value("SELECT count(*) FROM prks.publisher_keys WHERE library_id = %s AND key = %s",
                                    (self.lib, key)), 1)

    def test_a_name_and_a_concurrent_alias_of_another_publisher(self) -> None:
        other = self.publisher("Other")
        errors = self.race(lambda conn: self.create("Νέα Σκέψη", conn=conn),
                           lambda conn: self.add_alias(other, "ΝΈΑ ΣΚΈΨΗ", conn=conn))
        self.assertEqual([type(error) for error in errors], [psycopg.errors.UniqueViolation])
        self.assert_one_holder("νέα σκέψη")
        self.assertEqual(self.count("publisher_aliases", "publisher_id", other), 0)

    def test_an_alias_and_a_concurrent_publisher_of_that_name(self) -> None:
        other = self.publisher("Other")
        errors = self.race(lambda conn: self.add_alias(other, "Просвета", conn=conn),
                           lambda conn: self.create("ПРОСВЕТА", conn=conn))
        self.assertEqual([type(error) for error in errors], [psycopg.errors.UniqueViolation])
        self.assert_one_holder("просвета")
        self.assertEqual(self.value("SELECT count(*) FROM prks.publishers WHERE library_id = %s", (self.lib,)), 1)


class WorkDeleteTombstonesRolesTests(WorksFacadeTestCase):
    """The listed role cascade: the Work delete removes the roles and tombstones their scopes."""

    def test_deleting_a_work_tombstones_its_role_scopes(self) -> None:
        work_id = self.library.add_work("Credited")
        library_id = self.sql("SELECT library_id FROM prks.works WHERE id = %s", (work_id,))[0]["library_id"]
        person_id = new_id("P")
        scope_id = f'["{work_id}","{person_id}","Author"]'
        self.sql("INSERT INTO prks.persons (library_id, id, last_name) VALUES (%s, %s, 'Author')",
                 (library_id, person_id))
        self.sql(INSERT_ROLE, (library_id, work_id, person_id, "Author", 0, None, None))
        self.sql("INSERT INTO prks.entity_revisions (library_id, scope_type, scope_id, revision) "
                 "VALUES (%s, 'work-person-role', %s, 2)", (library_id, scope_id))
        self.assertIsNotNone(self.library.delete_work_record(work_id))
        self.assertEqual(self.sql("SELECT count(*) AS n FROM prks.roles WHERE work_id = %s", (work_id,))[0]["n"], 0)
        self.assertEqual(self.sql("SELECT revision FROM prks.entity_revisions WHERE scope_id = %s",
                                  (scope_id,))[0]["revision"], 3)
        self.assertEqual(self.sql("SELECT count(*) AS n FROM prks.persons WHERE id = %s", (person_id,))[0]["n"], 1)
