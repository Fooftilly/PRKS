"""Persons, Person Groups and publishers on PostgreSQL: what parity cannot show (L-P-1 #632).

``tests/test_people_core_parity.py`` proves the facade returns SQLite's JSON.
These tests cover what only PostgreSQL has: the bound actor and Library
isolation, identity keys, refusals of what PostgreSQL text cannot hold, the
revision decision tables the sync handlers will call, tombstones, and
concurrent writers (each race holds one transaction open until the other
is waiting on it).
"""

from __future__ import annotations

import threading
import time
import unicodedata
from collections.abc import Callable
from typing import Any

from backend.db.database import Isolation
from backend.domain.identity.authorization import AccessRefused, Capability, Refusal
from backend.domain.identity.library_scope import LibraryScope, library_command
from backend.domain.people import groups, persons, publishers
from backend.pg_facade import NotPortedError
from backend.pg_facade import context as facade_context
from backend.person_group_sync import GroupRuleError
from tests.pg.test_works_facade import WorksFacadeTestCase

_MISSING_GROUP = "PG-" + "0" * 32


class PeopleFacadeTestCase(WorksFacadeTestCase):
    def run_command(self, fn: Callable[[LibraryScope], Any], *, serializable: bool = False) -> Any:
        isolation = Isolation.SERIALIZABLE if serializable else Isolation.READ_COMMITTED
        return library_command(self.db, self.actor.context, Capability.EDIT_LIBRARY, fn, isolation=isolation)

    def revisions(self, scope_type: str) -> dict[str, int]:
        rows = self.sql("SELECT scope_id, revision FROM prks.entity_revisions WHERE scope_type = %s",
                        (scope_type,))
        return {row["scope_id"]: int(row["revision"]) for row in rows}

    def group_id(self, name: str) -> str:
        return next(row["id"] for row in self.library.get_all_person_groups() if row["name"] == name)

    def wait_for_lock_waiter(self) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            rows = self.sql("SELECT count(*) AS n FROM pg_stat_activity WHERE datname = current_database()"
                            " AND wait_event_type = 'Lock'")
            if rows[0]["n"]:
                return
            time.sleep(0.02)
        self.fail("the second writer never waited")

    def race(self, holder: Callable[[LibraryScope], Any], contender: Callable[[], Any]) -> tuple[Any, Any]:
        """``holder`` runs in a transaction kept open until ``contender`` waits on it.

        Returns each side's result, or the exception it raised.
        """
        held, release = threading.Event(), threading.Event()
        outcomes: dict[str, Any] = {}

        def hold(scope: LibraryScope) -> Any:
            result = holder(scope)
            held.set()
            release.wait(10)
            return result

        def run_holder() -> None:
            try:
                outcomes["holder"] = self.run_command(hold)
            except BaseException as exc:  # noqa: BLE001 - reported to the test thread
                outcomes["holder"] = exc
                held.set()

        def run_contender() -> None:
            with facade_context.acting_as(self.actor.context):
                try:
                    outcomes["contender"] = contender()
                except BaseException as exc:  # noqa: BLE001 - reported to the test thread
                    outcomes["contender"] = exc

        first = threading.Thread(target=run_holder)
        first.start()
        held.wait(10)
        second = threading.Thread(target=run_contender)
        second.start()
        try:
            self.wait_for_lock_waiter()
        finally:
            release.set()
            first.join(10)
            second.join(10)
        return outcomes.get("holder"), outcomes.get("contender")


class ActorAndIsolationTests(PeopleFacadeTestCase):
    def test_a_reader_reads_people_but_cannot_change_them(self) -> None:
        person = self.library.add_person("Ann", "Lee")
        group = self.library.add_person_group_with_parent_options("Readers")
        with facade_context.acting_as(self.reader_context()):
            self.assertEqual(self.library.get_person(person)["last_name"], "Lee")
            self.assertEqual(len(self.library.get_all_person_groups()), 1)
            for call in (lambda: self.library.add_person("No", "One"),
                         lambda: self.library.add_person_to_group(person, group),
                         lambda: self.library.update_person_profile(person, {"about": "x"}),
                         lambda: self.library.delete_person_if_unlinked(person),
                         lambda: self.library.add_publisher("Press")):
                with self.assertRaises(AccessRefused) as caught:
                    call()
                self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual(self.count("persons"), 1)
        self.assertEqual(self.count("person_group_members"), 0)
        self.assertEqual(self.count("publishers"), 0)

    def test_another_librarys_people_are_invisible_and_untouchable(self) -> None:
        person = self.library.add_person("Ann", "Lee")
        group = self.library.add_person_group_with_parent_options("Shared name")
        publisher = self.library.add_publisher("Shared Press")["id"]
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            self.assertEqual(self.library.get_all_persons(), [])
            self.assertEqual(self.library.get_all_person_groups(), [])
            self.assertEqual(self.library.get_publishers_in_use(), [])
            self.assertIsNone(self.library.get_person(person))
            self.assertIsNone(self.library.get_person_group(group))
            self.assertIsNone(self.library.get_person_metadata_state(person))
            self.assertIsNone(self.library.get_person_group_sync_state(group))
            with self.assertRaisesRegex(ValueError, "Person not found"):
                self.library.delete_person_if_unlinked(person)
            with self.assertRaisesRegex(ValueError, "Group not found"):
                self.library.update_person_group(group, {"name": "Taken over"})
            with self.assertRaisesRegex(ValueError, "Group not found"):
                self.library.delete_person_group(group)
            with self.assertRaisesRegex(ValueError, "publisher not found"):
                self.library.add_publisher_alias(publisher, "Alias")
            self.library.delete_publisher(publisher)
            self.library.update_person_profile(person, {"about": "overwritten"})
            # Names are unique per Library: the other Library has its own.
            theirs = self.library.add_person_group_with_parent_options("Shared name")
            their_person = self.library.add_person("Bo", "Ng")
            self.assertFalse(self.library.add_publisher("Shared Press")["existed"])
            with self.assertRaisesRegex(ValueError, "Person or group not found"):
                self.library.add_person_to_group(their_person, group)
            with self.assertRaisesRegex(ValueError, "Unknown group id"):
                self.library.update_person_profile(their_person, {}, [group])
        self.assertEqual(self.library.get_person(person)["about"], "")
        self.assertEqual(self.library.get_person_group(group)["name"], "Shared name")
        self.assertEqual([p["id"] for p in self.library.get_publishers_in_use()], [publisher])
        with self.assertRaisesRegex(ValueError, "Person or group not found"):
            self.library.add_person_to_group(person, theirs)
        self.assertEqual(self.count("person_group_members"), 0)

    def test_a_call_without_a_bound_actor_is_refused(self) -> None:
        import contextvars

        for call in (self.library.get_all_persons, lambda: self.library.add_person("A", "B"),
                     lambda: self.library.add_publisher("P")):
            with self.assertRaises(facade_context.NoRequestContext):
                contextvars.Context().run(call)
        self.assertEqual(self.count("persons"), 0)


class IdentityTests(PeopleFacadeTestCase):
    def test_group_names_compare_by_identity_key(self) -> None:
        self.library.add_person_group_with_parent_options("Straße")
        decomposed = unicodedata.normalize("NFD", "Résumé")
        self.library.add_person_group_with_parent_options("Résumé")
        for taken in ("STRASSE", "  strasse  ", decomposed, "RÉSUMÉ"):
            with self.subTest(name=taken):
                with self.assertRaises(GroupRuleError) as caught:
                    self.library.add_person_group_with_parent_options(taken)
                self.assertEqual(caught.exception.code, "NAME_TAKEN")
        self.library.add_person_group_with_parent_options("Resume")  # diacritics are not folded
        child = self.library.add_person_group_with_parent_options("Child", parent_name="RÉSUMÉ")
        self.assertEqual(self.library.get_person_group(child)["parent"]["name"], "Résumé")
        with self.assertRaisesRegex(ValueError, "same name"):
            self.library.add_person_group_with_parent_options("Ωμέγα", parent_name="ΩΜΈΓΑ")
        self.assertEqual(self.count("person_groups"), 4)

    def test_publisher_names_and_aliases_share_one_namespace(self) -> None:
        verso = self.library.add_publisher("Éditions Verso")["id"]
        self.assertTrue(self.library.add_publisher("ÉDITIONS   VERSO")["existed"])
        self.library.add_publisher_alias(verso, "Verso Straße")
        with self.assertRaisesRegex(ValueError, "another publisher's alias"):
            self.library.add_publisher("verso strasse")
        with self.assertRaisesRegex(ValueError, "matches canonical"):
            self.library.add_publisher_alias(verso, unicodedata.normalize("NFD", "éditions verso"))
        self.assertTrue(self.library.delete_publisher_alias(verso, "VERSO STRASSE"))
        self.assertFalse(self.library.add_publisher("Verso Strasse")["existed"])
        self.assertEqual(self.count("publisher_keys"), 2)

    def test_aliases_are_rows_with_one_spelling_per_key(self) -> None:
        person = self.library.add_person("Ann", "Lee", aliases=" A. Lee ,a. lee, ,Anna  Lee,ANNA LEE")
        self.assertEqual(self.library.get_person(person)["aliases"], "A. Lee, Anna  Lee")
        rows = self.sql("SELECT position, alias FROM prks.person_aliases ORDER BY position")
        self.assertEqual([(r["position"], r["alias"]) for r in rows], [(0, "A. Lee"), (1, "Anna  Lee")])
        # Re-spacing the same aliases, or repeating one, is no change.
        self.library.update_person_profile(person, {"aliases": "A. Lee,Anna  Lee, a. LEE"})
        self.assertEqual(self.library.get_person_metadata_state(person)["fields"]["aliases"]["revision"], 0)
        self.library.update_person_profile(person, {"aliases": ""})
        self.assertEqual(self.count("person_aliases"), 0)
        self.assertEqual(self.library.get_person_metadata_state(person)["fields"]["aliases"]["revision"], 1)


class InvalidInputTests(PeopleFacadeTestCase):
    def test_what_postgresql_text_cannot_hold_is_refused_before_any_write(self) -> None:
        person = self.library.add_person("Ann", "Lee")
        group = self.library.add_person_group_with_parent_options("Group")
        publisher = self.library.add_publisher("Press")["id"]
        refusals: list[Callable[[], Any]] = [
            lambda: self.library.add_person("A\x00", "B"),
            lambda: self.library.update_person_profile(person, {"about": "a\x00b"}),
            lambda: self.library.update_person_profile(person, {"aliases": "ok, a\x00b"}, []),
            lambda: self.library.add_person_group_with_parent_options("bad\x00name"),
            lambda: self.library.update_person_group(group, {"description": "\x00"}),
            lambda: self.library.add_publisher("Press\x00"),
            lambda: self.library.add_publisher_alias(publisher, "Alias\x00"),
            lambda: self.library.add_person("Ann", "Lee", image_url="ftp://example.org/x.png"),
            lambda: self.library.add_person("Ann", "Lee", person_id="P-1"),
        ]
        for call in refusals:
            with self.assertRaises(ValueError):
                call()
        self.assertEqual(self.count("persons"), 1)
        self.assertEqual(self.library.get_person(person)["about"], "")
        self.assertEqual(self.count("person_groups"), 1)
        self.assertEqual(self.count("publisher_aliases"), 0)
        self.assertEqual(self.sql("SELECT count(*) AS n FROM prks.entity_revisions")[0]["n"], 0)

    def test_a_portrait_url_is_normalized_on_create(self) -> None:
        person = self.library.add_person("Ann", "Lee", image_url="  https://example.org/a.png  ")
        self.assertEqual(self.library.get_person(person)["image_url"], "https://example.org/a.png")


class RevisionDecisionTests(PeopleFacadeTestCase):
    """The decision tables PR 4's sync handlers call, on the same commands."""

    def test_person_field_decisions(self) -> None:
        person = self.library.add_person("Ann", "Lee")
        self.library.update_person_profile(person, {"about": "one"})

        def apply(value: str, base: int) -> tuple[int, dict[str, Any]]:
            return self.run_command(lambda scope: persons.apply_field_operation(
                scope, person, "about", persons.canonical_field("about", value), base))

        status, result = apply("two", 5)
        self.assertEqual((status, result["code"], result["current_revision"]), (400, "FUTURE_REVISION", 1))
        status, result = apply("two", 0)
        self.assertEqual((status, result["code"], result["current_value"], result["requested_value"]),
                         (409, "REVISION_CONFLICT", "one", "two"))
        status, result = apply("one", 0)  # stale but converged
        self.assertEqual((status, result["code"], result["changed"]), (200, "ACKNOWLEDGED", False))
        status, result = apply("two", 1)
        self.assertEqual((status, result["server_revision"], result["changed"]), (200, 2, True))
        status, result = self.run_command(lambda scope: persons.apply_field_operation(
            scope, "P-" + "0" * 32, "about", "x", 0))
        self.assertEqual((status, result["code"]), (404, "ENTITY_NOT_FOUND"))
        self.assertEqual(self.library.get_person(person)["about"], "two")

    def test_aliases_converge_on_their_stored_spelling(self) -> None:
        person = self.library.add_person("Ann", "Lee", aliases="A, B")
        self.library.update_person_profile(person, {"aliases": "A, B, C"})
        status, result = self.run_command(lambda scope: persons.apply_field_operation(
            scope, person, "aliases", "A,B ,  C,c", 0))
        self.assertEqual((status, result["code"], result["changed"]), (200, "ACKNOWLEDGED", False))

    def test_group_field_and_member_decisions(self) -> None:
        group = self.library.add_person_group_with_parent_options("G")
        person = self.library.add_person("Ann", "Lee")
        self.library.update_person_group(group, {"description": "one"})
        status, result = self.run_command(lambda scope: groups.apply_field_operation(
            scope, group, "description", "two", 0))
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        status, result = self.run_command(lambda scope: groups.apply_field_operation(
            scope, group, "parent_id", group, 0))
        self.assertEqual((status, result["code"]), (409, "PARENT_CYCLE"))
        self.library.add_person_to_group(person, group)
        status, result = self.run_command(lambda scope: groups.apply_member_operation(
            scope, group, person, False, 0), serializable=True)
        self.assertEqual((status, result["code"], result["current_present"]), (409, "REVISION_CONFLICT", True))
        status, result = self.run_command(lambda scope: groups.apply_member_operation(
            scope, group, person, True, 0), serializable=True)
        self.assertEqual((status, result["code"], result["changed"]), (200, "ACKNOWLEDGED", False))
        status, result = self.run_command(lambda scope: groups.apply_member_operation(
            scope, group, person, False, 1), serializable=True)
        self.assertEqual((status, result["server_revision"], result["present"]), (200, 2, False))
        status, result = self.run_command(lambda scope: groups.apply_member_operation(
            scope, group, "P-" + "0" * 32, True, 0), serializable=True)
        self.assertEqual((status, result["code"]), (404, "PERSON_NOT_FOUND"))
        status, result = self.run_command(lambda scope: groups.apply_member_operation(
            scope, _MISSING_GROUP, person, True, 0), serializable=True)
        self.assertEqual((status, result["code"]), (404, "ENTITY_NOT_FOUND"))


class DeletionTests(PeopleFacadeTestCase):
    def link_role(self, person_id: str) -> str:
        work_id = self.library.add_work("Linked")
        self.sql("INSERT INTO prks.roles (library_id, work_id, person_id, role_type, order_index)"
                 " VALUES (%s, %s, %s, 'Author', 0)", (self.actor.library_id, work_id, person_id))
        return work_id

    def test_a_person_with_roles_is_kept(self) -> None:
        person = self.library.add_person("Ann", "Lee")
        group = self.library.add_person_group_with_parent_options("G")
        self.library.add_person_to_group(person, group)
        self.link_role(person)
        with self.assertRaisesRegex(ValueError, "Cannot delete person with linked works"):
            self.library.delete_person_if_unlinked(person)
        self.assertEqual(self.count("persons"), 1)
        self.assertEqual(self.count("person_group_members"), 1)
        self.assertEqual(self.library.get_all_persons()[0]["assigned_roles"], ["Author"])
        # A Person's Works are the roles projection, which is not ported yet.
        with self.assertRaises(NotPortedError):
            self.library.get_person(person)

    def test_a_deleted_person_leaves_tombstones(self) -> None:
        person = self.library.add_person("Ann", "Lee", aliases="A")
        group = self.library.add_person_group_with_parent_options("G")
        self.library.update_person_profile(person, {"about": "x"}, [group])
        self.library.delete_person_if_unlinked(person)
        fields = self.revisions("person-field")
        self.assertEqual(fields, {persons.scope_id(person, "about"): 2})
        self.assertEqual(self.revisions("person-group-member"), {groups.member_scope(group, person): 2})
        self.assertEqual((self.count("persons"), self.count("person_aliases")), (0, 0))

    def test_a_deleted_group_leaves_tombstones_and_moves_its_children(self) -> None:
        top = self.library.add_person_group_with_parent_options("Top")
        middle = self.library.add_person_group_with_parent_options("Middle", parent_id=top)
        bottom = self.library.add_person_group_with_parent_options("Bottom", parent_name="Middle")
        person = self.library.add_person("Ann", "Lee")
        self.library.add_person_to_group(person, middle)
        self.library.update_person_group(middle, {"description": "d"})
        self.library.delete_person_group(middle)
        self.assertEqual(self.library.get_person_group(bottom)["parent_id"], top)
        self.assertEqual(self.revisions("person-group-field"), {
            groups.field_scope(middle, "description"): 2,
            groups.field_scope(bottom, "parent_id"): 1,
        })
        self.assertEqual(self.revisions("person-group-member"), {groups.member_scope(middle, person): 2})
        self.assertEqual(self.library.get_person_groups_state(person)["groups"],
                         [{"group_id": middle, "revision": 2, "present": False}])

    def test_a_deleted_publisher_takes_its_aliases_and_keys(self) -> None:
        publisher = self.library.add_publisher("Press")["id"]
        self.library.add_publisher_alias(publisher, "The Press")
        self.library.delete_publisher(publisher)
        for table in ("publishers", "publisher_aliases", "publisher_keys"):
            self.assertEqual(self.count(table), 0, table)


class ConcurrencyTests(PeopleFacadeTestCase):
    def test_two_reparents_that_would_close_a_cycle(self) -> None:
        first = self.library.add_person_group_with_parent_options("A")
        second = self.library.add_person_group_with_parent_options("B")
        holder, contender = self.race(
            lambda scope: groups.set_field(scope, first, "parent_id", second),
            lambda: self.library.update_person_group(second, {"parent_id": first}))
        self.assertEqual(holder, (True, 1))
        self.assertIsInstance(contender, GroupRuleError)
        self.assertEqual(contender.code, "PARENT_CYCLE")
        self.assertEqual(self.library.get_person_group(first)["parent_id"], second)
        self.assertIsNone(self.library.get_person_group(second)["parent_id"])

    def test_two_creates_of_one_name(self) -> None:
        holder, contender = self.race(
            lambda scope: groups.insert_group(scope, "PG-" + "1" * 32, "Circle"),
            lambda: self.library.add_person_group_with_parent_options("CIRCLE"))
        self.assertEqual(holder, "PG-" + "1" * 32)
        self.assertIsInstance(contender, GroupRuleError)
        self.assertEqual(contender.code, "NAME_TAKEN")
        self.assertEqual(self.count("person_groups"), 1)

    def test_a_rename_racing_a_rename(self) -> None:
        first = self.library.add_person_group_with_parent_options("One")
        second = self.library.add_person_group_with_parent_options("Two")
        holder, contender = self.race(
            lambda scope: groups.set_field(scope, first, "name", "Same"),
            lambda: self.library.update_person_group(second, {"name": "same"}))
        self.assertEqual(holder, (True, 1))
        self.assertIsInstance(contender, GroupRuleError)
        self.assertEqual(contender.code, "NAME_TAKEN")
        self.assertEqual(self.library.get_person_group(second)["name"], "Two")
        self.assertEqual(self.revisions("person-group-field"), {groups.field_scope(first, "name"): 1})

    def test_a_parent_found_by_name_while_another_creates_it(self) -> None:
        holder, contender = self.race(
            lambda scope: groups.insert_group(scope, "PG-" + "2" * 32, "Parent"),
            lambda: self.library.add_person_group_with_parent_options("Child", parent_name="parent"))
        self.assertEqual(holder, "PG-" + "2" * 32)
        self.assertEqual(self.library.get_person_group(contender)["parent_id"], "PG-" + "2" * 32)
        self.assertEqual(self.count("person_groups"), 2)

    def test_two_publishers_of_one_name(self) -> None:
        holder, contender = self.race(
            lambda scope: publishers.create_publisher(scope, "R-" + "1" * 32, "Verso"),
            lambda: self.library.add_publisher("VERSO"))
        self.assertFalse(holder["existed"])
        self.assertEqual(contender, {"id": "R-" + "1" * 32, "name": "Verso", "existed": True})
        self.assertEqual(self.count("publishers"), 1)

    def test_an_alias_racing_a_publisher_of_the_same_name(self) -> None:
        verso = self.library.add_publisher("Verso")["id"]
        holder, contender = self.race(
            lambda scope: publishers.add_alias(scope, "M-" + "1" * 32, verso, "Penguin"),
            lambda: self.library.add_publisher("penguin"))
        self.assertIsNone(holder)
        self.assertIsInstance(contender, ValueError)
        self.assertIn("alias", str(contender))
        self.assertEqual(self.count("publishers"), 1)

    def test_two_field_edits_of_one_person_queue(self) -> None:
        person = self.library.add_person("Ann", "Lee")

        def edit(scope: LibraryScope) -> Any:
            persons.lock(scope, person)
            return persons.set_field(scope, person, "about", "first")

        holder, contender = self.race(edit, lambda: self.library.update_person_profile(
            person, {"about": "second"}))
        self.assertEqual(holder, (True, 1))
        self.assertIsNone(contender)
        self.assertEqual(self.library.get_person(person)["about"], "second")
        self.assertEqual(self.library.get_person_metadata_state(person)["fields"]["about"]["revision"], 2)

    def test_a_member_added_while_the_person_is_deleted(self) -> None:
        person = self.library.add_person("Ann", "Lee")
        group = self.library.add_person_group_with_parent_options("G")
        holder, contender = self.race(
            lambda scope: persons.delete_person(scope, person),
            lambda: self.library.add_person_to_group(person, group))
        self.assertEqual(holder, (True, 0))
        self.assertIsInstance(contender, ValueError)
        self.assertEqual(str(contender), "Person or group not found.")
        self.assertEqual(self.count("person_group_members"), 0)

    def test_a_member_added_while_the_group_is_deleted(self) -> None:
        person = self.library.add_person("Ann", "Lee")
        group = self.library.add_person_group_with_parent_options("G")
        holder, contender = self.race(
            lambda scope: groups.delete_group(scope, group),
            lambda: self.library.add_person_to_group(person, group))
        self.assertTrue(holder)
        self.assertIsInstance(contender, ValueError)
        self.assertEqual(self.count("person_group_members"), 0)

    def test_a_group_deleted_while_a_member_is_added(self) -> None:
        person = self.library.add_person("Ann", "Lee")
        group = self.library.add_person_group_with_parent_options("G")

        def add(scope: LibraryScope) -> bool:
            groups.lock_pair(scope, group, person)
            return groups.set_member(scope, group, person, True)

        holder, contender = self.race(add, lambda: self.library.delete_person_group(group))
        self.assertTrue(holder)
        self.assertIsNone(contender)
        self.assertEqual(self.count("person_group_members"), 0)
        self.assertEqual(self.revisions("person-group-member"), {groups.member_scope(group, person): 2})
