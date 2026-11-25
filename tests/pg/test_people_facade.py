"""Persons, Person Groups and publishers on PostgreSQL (L-P-1 #632).

:class:`AnswerTests` spells out the JSON each command answers. The rest
cover the bound actor and Library
isolation, identity keys, refusals of what PostgreSQL text cannot hold, the
revision decision tables the sync handlers will call, tombstones, and
concurrent writers (each race holds one transaction open until the other
is waiting on it).
"""

from __future__ import annotations

import re
import threading
import time
import unicodedata
from collections.abc import Callable
from functools import partial
from typing import Any, LiteralString
from unittest import mock

from backend.db.database import Isolation
from backend.domain.identity.authorization import AccessRefused, Capability, Refusal
from backend.domain.identity.library_scope import LibraryScope, library_command
from backend.domain.people import group_rules as group_rules
from backend.domain.people import person_metadata_rules as person_rules
from backend.domain.people import groups, persons, publishers, queries
from tests import acting_context as facade_context
from backend.domain.people.group_rules import GroupRuleError
from tests.pg.test_works_facade import WorksFacadeTestCase
from backend.web.etags import etag_for_representation
from tests.narrowing import require
from tests.pg.answers import AnswerAssertions
from backend.domain.people import group_commands
from backend.domain.people import person_commands
from backend.domain.people import publisher_commands
from backend.domain.people import role_commands
from tests import people_fixtures

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
        return next(row["id"] for row in group_commands.catalog(self.db, self.ctx) if row["name"] == name)

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
        person = people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee")
        group = group_commands.create(self.db, self.ctx, "Readers")
        with facade_context.acting_as(self.reader_context()):
            self.assertEqual(require(person_commands.detail(self.db, self.ctx, person, self.pdfs_dir))["last_name"], "Lee")
            self.assertEqual(len(group_commands.catalog(self.db, self.ctx)), 1)
            for call in (lambda: people_fixtures.add_person(self.db, self.ctx, "No", "One"),
                         lambda: group_commands.add_member(self.db, self.ctx, group, person),
                         lambda: person_commands.update_profile(self.db, self.ctx, person, {"about": "x"}),
                         lambda: person_commands.delete_if_unlinked(self.db, self.ctx, person),
                         lambda: publisher_commands.create(self.db, self.ctx, "Press")):
                with self.assertRaises(AccessRefused) as caught:
                    call()
                self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual(self.count("persons"), 1)
        self.assertEqual(self.count("person_group_members"), 0)
        self.assertEqual(self.count("publishers"), 0)

    def test_another_librarys_people_are_invisible_and_untouchable(self) -> None:
        person = people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee")
        group = group_commands.create(self.db, self.ctx, "Shared name")
        publisher = publisher_commands.create(self.db, self.ctx, "Shared Press")["id"]
        publisher_commands.add_alias(self.db, self.ctx, publisher, "Shared alias")
        group_commands.add_member(self.db, self.ctx, group, person)
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            self.assertEqual(person_commands.catalog(self.db, self.ctx), [])
            self.assertEqual(group_commands.catalog(self.db, self.ctx), [])
            self.assertEqual(publisher_commands.in_use(self.db, self.ctx), [])
            self.assertIsNone(person_commands.detail(self.db, self.ctx, person, self.pdfs_dir))
            self.assertIsNone(group_commands.detail(self.db, self.ctx, group))
            self.assertIsNone(person_commands.metadata_state(self.db, self.ctx, person))
            self.assertIsNone(group_commands.sync_state(self.db, self.ctx, group))
            with self.assertRaisesRegex(ValueError, "Person not found"):
                person_commands.delete_if_unlinked(self.db, self.ctx, person)
            with self.assertRaisesRegex(ValueError, "Group not found"):
                group_commands.update(self.db, self.ctx, group, {"name": "Taken over"})
            with self.assertRaisesRegex(ValueError, "Group not found"):
                group_commands.delete(self.db, self.ctx, group)
            with self.assertRaisesRegex(ValueError, "publisher not found"):
                publisher_commands.add_alias(self.db, self.ctx, publisher, "Alias")
            publisher_commands.delete(self.db, self.ctx, publisher)
            person_commands.update_profile(self.db, self.ctx, person, {"about": "overwritten"})
            # Names are unique per Library: the other Library has its own.
            theirs = group_commands.create(self.db, self.ctx, "Shared name")
            their_person = people_fixtures.add_person(self.db, self.ctx, "Bo", "Ng")
            self.assertFalse(publisher_commands.create(self.db, self.ctx, "Shared Press")["existed"])
            with self.assertRaisesRegex(ValueError, "Person or group not found"):
                group_commands.add_member(self.db, self.ctx, group, their_person)
            with self.assertRaisesRegex(ValueError, "Unknown group id"):
                person_commands.update_profile(self.db, self.ctx, their_person, {}, [group])
            group_commands.remove_member(self.db, self.ctx, group, person)
            self.assertFalse(publisher_commands.delete_alias(self.db, self.ctx, publisher, "Shared alias"))
            self.assertIsNone(group_commands.person_state(self.db, self.ctx, person))
            with self.assertRaisesRegex(ValueError, "Parent group not found"):
                group_commands.create(self.db, self.ctx, "Child", group)
            with self.assertRaisesRegex(ValueError, "Parent group not found"):
                group_commands.update(self.db, self.ctx, theirs, {"parent_id": group})
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, person, self.pdfs_dir))["about"], "")
        self.assertEqual(require(group_commands.detail(self.db, self.ctx, group))["name"], "Shared name")
        self.assertEqual([(p["id"], p["aliases"]) for p in publisher_commands.in_use(self.db, self.ctx)],
                         [(publisher, ["Shared alias"])])
        with self.assertRaisesRegex(ValueError, "Person or group not found"):
            group_commands.add_member(self.db, self.ctx, theirs, person)
        # Only this Library's own add left a membership and a revision.
        self.assertEqual(self.sql("SELECT library_id, group_id, person_id FROM prks.person_group_members"),
                         [{"library_id": self.actor.library_id, "group_id": group, "person_id": person}])
        self.assertEqual(self.sql("SELECT library_id, scope_id, revision FROM prks.entity_revisions"),
                         [{"library_id": self.actor.library_id, "scope_id": groups.member_scope(group, person),
                           "revision": 1}])
        self.assertEqual(require(group_commands.detail(self.db, self.ctx, group))["member_count"], 1)

    def test_a_call_without_a_bound_actor_is_refused(self) -> None:
        import contextvars

        for call in (lambda: person_commands.catalog(self.db, self.ctx), lambda: people_fixtures.add_person(self.db, self.ctx, "A", "B"),
                     lambda: publisher_commands.create(self.db, self.ctx, "P")):
            with self.assertRaises(facade_context.NoRequestContext):
                contextvars.Context().run(call)
        self.assertEqual(self.count("persons"), 0)


class IdentityTests(PeopleFacadeTestCase):
    def test_group_names_compare_by_identity_key(self) -> None:
        group_commands.create(self.db, self.ctx, "Straße")
        decomposed = unicodedata.normalize("NFD", "Résumé")
        group_commands.create(self.db, self.ctx, "Résumé")
        for taken in ("STRASSE", "  strasse  ", decomposed, "RÉSUMÉ"):
            with self.subTest(name=taken):
                with self.assertRaises(GroupRuleError) as caught:
                    group_commands.create(self.db, self.ctx, taken)
                self.assertEqual(caught.exception.code, "NAME_TAKEN")
        group_commands.create(self.db, self.ctx, "Resume")  # diacritics are not folded
        child = group_commands.create(self.db, self.ctx, "Child", None, "RÉSUMÉ")
        self.assertEqual(require(group_commands.detail(self.db, self.ctx, child))["parent"]["name"], "Résumé")
        with self.assertRaisesRegex(ValueError, "same name"):
            group_commands.create(self.db, self.ctx, "Ωμέγα", None, "ΩΜΈΓΑ")
        self.assertEqual(self.count("person_groups"), 4)

    def test_publisher_names_and_aliases_share_one_namespace(self) -> None:
        verso = publisher_commands.create(self.db, self.ctx, "Éditions Verso")["id"]
        self.assertTrue(publisher_commands.create(self.db, self.ctx, "ÉDITIONS   VERSO")["existed"])
        publisher_commands.add_alias(self.db, self.ctx, verso, "Verso Straße")
        with self.assertRaisesRegex(ValueError, "another publisher's alias"):
            publisher_commands.create(self.db, self.ctx, "verso strasse")
        with self.assertRaisesRegex(ValueError, "matches canonical"):
            publisher_commands.add_alias(self.db, self.ctx, verso, unicodedata.normalize("NFD", "éditions verso"))
        self.assertTrue(publisher_commands.delete_alias(self.db, self.ctx, verso, "VERSO STRASSE"))
        self.assertFalse(publisher_commands.create(self.db, self.ctx, "Verso Strasse")["existed"])
        self.assertEqual(self.count("publisher_keys"), 2)

    def test_aliases_are_rows_with_one_spelling_per_key(self) -> None:
        person = people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee", aliases=" A. Lee ,a. lee, ,Anna  Lee,ANNA LEE")
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, person, self.pdfs_dir))["aliases"], "A. Lee, Anna  Lee")
        rows = self.sql("SELECT position, alias FROM prks.person_aliases ORDER BY position")
        self.assertEqual([(r["position"], r["alias"]) for r in rows], [(0, "A. Lee"), (1, "Anna  Lee")])
        # Re-spacing the same aliases, or repeating one, is no change.
        person_commands.update_profile(self.db, self.ctx, person, {"aliases": "A. Lee,Anna  Lee, a. LEE"})
        self.assertEqual(require(person_commands.metadata_state(self.db, self.ctx, person))["fields"]["aliases"]["revision"], 0)
        person_commands.update_profile(self.db, self.ctx, person, {"aliases": ""})
        self.assertEqual(self.count("person_aliases"), 0)
        self.assertEqual(require(person_commands.metadata_state(self.db, self.ctx, person))["fields"]["aliases"]["revision"], 1)


class InvalidInputTests(PeopleFacadeTestCase):
    def test_what_postgresql_text_cannot_hold_is_refused_before_any_write(self) -> None:
        person = people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee")
        group = group_commands.create(self.db, self.ctx, "Group")
        publisher = publisher_commands.create(self.db, self.ctx, "Press")["id"]
        refusals: list[Callable[[], Any]] = [
            lambda: people_fixtures.add_person(self.db, self.ctx, "A\x00", "B"),
            lambda: person_commands.update_profile(self.db, self.ctx, person, {"about": "a\x00b"}),
            lambda: person_commands.update_profile(self.db, self.ctx, person, {"aliases": "ok, a\x00b"}, []),
            lambda: group_commands.create(self.db, self.ctx, "bad\x00name"),
            lambda: group_commands.update(self.db, self.ctx, group, {"description": "\x00"}),
            lambda: publisher_commands.create(self.db, self.ctx, "Press\x00"),
            lambda: publisher_commands.add_alias(self.db, self.ctx, publisher, "Alias\x00"),
            lambda: people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee", image_url="ftp://example.org/x.png"),
            lambda: people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee", person_id="P-1"),
        ]
        for call in refusals:
            with self.assertRaises(ValueError):
                call()
        self.assertEqual(self.count("persons"), 1)
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, person, self.pdfs_dir))["about"], "")
        self.assertEqual(self.count("person_groups"), 1)
        self.assertEqual(self.count("publisher_aliases"), 0)
        self.assertEqual(self.sql("SELECT count(*) AS n FROM prks.entity_revisions")[0]["n"], 0)

    def test_a_portrait_url_is_normalized_on_create(self) -> None:
        person = people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee", image_url="  https://example.org/a.png  ")
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, person, self.pdfs_dir))["image_url"], "https://example.org/a.png")


class RevisionDecisionTests(PeopleFacadeTestCase):
    """The decision tables PR 4's sync handlers call, on the same commands."""

    def test_person_field_decisions(self) -> None:
        person = people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee")
        person_commands.update_profile(self.db, self.ctx, person, {"about": "one"})

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
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, person, self.pdfs_dir))["about"], "two")

    def test_aliases_converge_on_their_stored_spelling(self) -> None:
        person = people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee", aliases="A, B")
        person_commands.update_profile(self.db, self.ctx, person, {"aliases": "A, B, C"})
        status, result = self.run_command(lambda scope: persons.apply_field_operation(
            scope, person, "aliases", "A,B ,  C,c", 0))
        self.assertEqual((status, result["code"], result["changed"]), (200, "ACKNOWLEDGED", False))

    def test_group_field_and_member_decisions(self) -> None:
        group = group_commands.create(self.db, self.ctx, "G")
        person = people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee")
        group_commands.update(self.db, self.ctx, group, {"description": "one"})
        status, result = self.run_command(lambda scope: groups.apply_field_operation(
            scope, group, "description", "two", 0))
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        status, result = self.run_command(lambda scope: groups.apply_field_operation(
            scope, group, "parent_id", group, 0))
        self.assertEqual((status, result["code"]), (409, "PARENT_CYCLE"))
        group_commands.add_member(self.db, self.ctx, group, person)
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


class PhantomScopeTests(PeopleFacadeTestCase):
    def test_a_no_op_membership_change_leaves_no_revision_row(self) -> None:
        group = group_commands.create(self.db, self.ctx, "G")
        person = people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee")
        group_commands.remove_member(self.db, self.ctx, group, person)
        status, result = self.run_command(lambda scope: groups.apply_member_operation(
            scope, group, person, False, 0), serializable=True)
        self.assertEqual((status, result["code"], result["changed"]), (200, "ACKNOWLEDGED", False))
        self.assertEqual(self.revisions("person-group-member"), {})
        self.assertEqual(require(group_commands.sync_state(self.db, self.ctx, group))["members"], [])
        self.assertEqual(require(group_commands.person_state(self.db, self.ctx, person))["groups"], [])


class DeletionTests(PeopleFacadeTestCase):
    def link_role(self, person_id: str) -> str:
        work_id = self.library.add_work("Linked")
        role_commands.add(self.db, self.ctx, person_id, work_id, "Author")
        return work_id

    def test_a_person_with_roles_is_kept(self) -> None:
        person = people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee")
        group = group_commands.create(self.db, self.ctx, "G")
        group_commands.add_member(self.db, self.ctx, group, person)
        work = self.link_role(person)
        with self.assertRaisesRegex(ValueError, "Cannot delete person with linked works"):
            person_commands.delete_if_unlinked(self.db, self.ctx, person)
        self.assertEqual(self.count("persons"), 1)
        self.assertEqual(self.count("person_group_members"), 1)
        self.assertEqual(person_commands.catalog(self.db, self.ctx)[0]["assigned_roles"], ["Author"])
        self.assertEqual([(w["id"], w["role_type"]) for w in require(person_commands.detail(self.db, self.ctx, person, self.pdfs_dir))["works"]],
                         [(work, "Author")])

    def test_a_person_without_works_reads_without_a_data_root(self) -> None:
        person = people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee")
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, person, None))["works"], [])
        self.link_role(person)
        # Finishing a Work summary needs the PDF directory.
        with self.assertRaisesRegex(RuntimeError, "no data root"):
            person_commands.detail(self.db, self.ctx, person, None)

    def test_a_deleted_person_leaves_tombstones(self) -> None:
        person = people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee", aliases="A")
        group = group_commands.create(self.db, self.ctx, "G")
        person_commands.update_profile(self.db, self.ctx, person, {"about": "x"}, [group])
        person_commands.delete_if_unlinked(self.db, self.ctx, person)
        # Every field scope advanced once, the never-edited ones included.
        expected = {persons.scope_id(person, field): 1 for field in person_rules.SYNCED_FIELDS}
        expected[persons.scope_id(person, "about")] = 2
        self.assertEqual(self.revisions("person-field"), expected)
        self.assertEqual(self.revisions("person-group-member"), {groups.member_scope(group, person): 2})
        self.assertEqual((self.count("persons"), self.count("person_aliases")), (0, 0))

    def test_a_deleted_group_leaves_tombstones_and_moves_its_children(self) -> None:
        top = group_commands.create(self.db, self.ctx, "Top")
        middle = group_commands.create(self.db, self.ctx, "Middle", top)
        bottom = group_commands.create(self.db, self.ctx, "Bottom", None, "Middle")
        person = people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee")
        group_commands.add_member(self.db, self.ctx, middle, person)
        group_commands.update(self.db, self.ctx, middle, {"description": "d"})
        group_commands.delete(self.db, self.ctx, middle)
        self.assertEqual(require(group_commands.detail(self.db, self.ctx, bottom))["parent_id"], top)
        expected = {groups.field_scope(middle, field): 1 for field in group_rules.FIELDS}
        expected[groups.field_scope(middle, "description")] = 2
        expected[groups.field_scope(bottom, "parent_id")] = 1
        self.assertEqual(self.revisions("person-group-field"), expected)
        self.assertEqual(self.revisions("person-group-member"), {groups.member_scope(middle, person): 2})
        self.assertEqual(require(group_commands.person_state(self.db, self.ctx, person))["groups"],
                         [{"group_id": middle, "revision": 2, "present": False}])

    def test_a_failed_group_delete_rolls_back_the_reparenting(self) -> None:
        parent = group_commands.create(self.db, self.ctx, "Parent")
        group = group_commands.create(self.db, self.ctx, "Group", parent)
        child = group_commands.create(self.db, self.ctx, "Child", group)
        before = self.revisions("person-group-field")
        # The group's own DELETE runs after its children moved and its scopes
        # advanced, so failing it is exactly the partial-commit window.
        failing = "SELECT 1 / 0 WHERE %(group_id)s::text IS NOT NULL"
        with mock.patch.object(groups, "_DELETE_SQL", failing):
            with self.assertRaises(Exception):
                group_commands.delete(self.db, self.ctx, group)
        self.assertIsNotNone(group_commands.detail(self.db, self.ctx, group))
        self.assertEqual(require(group_commands.detail(self.db, self.ctx, child))["parent_id"], group)
        self.assertEqual(self.revisions("person-group-field"), before)
        # The ordinary delete still works once the injected failure is gone.
        group_commands.delete(self.db, self.ctx, group)
        self.assertEqual(require(group_commands.detail(self.db, self.ctx, child))["parent_id"], parent)

    def stale_add(self, group_id: str, person_id: str, base: int) -> tuple[int, dict[str, Any]]:
        return self.run_command(lambda scope: groups.apply_member_operation(
            scope, group_id, person_id, True, base), serializable=True)

    def test_a_recreated_person_refuses_a_membership_base_from_before_the_delete(self) -> None:
        person = people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee")
        kept, removed = (group_commands.create(self.db, self.ctx, name) for name in ("Kept", "Gone"))
        group_commands.add_member(self.db, self.ctx, kept, person)
        group_commands.add_member(self.db, self.ctx, removed, person)
        group_commands.remove_member(self.db, self.ctx, removed, person)
        before = {g["group_id"]: g["revision"] for g in require(group_commands.person_state(self.db, self.ctx, person))["groups"]}
        self.assertEqual(before, {kept: 1, removed: 2})
        person_commands.delete_if_unlinked(self.db, self.ctx, person)
        # Every pair advanced once: the present one and the one removed earlier.
        self.assertEqual(self.revisions("person-group-member"),
                         {groups.member_scope(kept, person): 2, groups.member_scope(removed, person): 3})
        self.assertEqual(people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee", person_id=person), person)
        for group_id, base in before.items():
            status, result = self.stale_add(group_id, person, base)
            self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"), group_id)
        self.assertEqual(self.count("person_group_members"), 0)

    def test_a_recreated_group_refuses_a_membership_base_from_before_the_delete(self) -> None:
        group = group_commands.create(self.db, self.ctx, "Readers")
        present, removed = people_fixtures.add_person(self.db, self.ctx, "A", "One"), people_fixtures.add_person(self.db, self.ctx, "B", "Two")
        group_commands.add_member(self.db, self.ctx, group, present)
        group_commands.add_member(self.db, self.ctx, group, removed)
        group_commands.remove_member(self.db, self.ctx, group, removed)
        before = {m["person_id"]: m["revision"] for m in require(group_commands.sync_state(self.db, self.ctx, group))["members"]}
        self.assertEqual(before, {present: 1, removed: 2})
        group_commands.delete(self.db, self.ctx, group)
        self.assertEqual(self.revisions("person-group-member"),
                         {groups.member_scope(group, present): 2, groups.member_scope(group, removed): 3})
        self.run_command(lambda scope: groups.insert_group(scope, group, "Readers"))
        for person_id, base in before.items():
            status, result = self.stale_add(group, person_id, base)
            self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"), person_id)
        status, result = self.stale_add(group, present, 2)  # a base that saw the delete
        self.assertEqual((status, result["code"], result["server_revision"]), (200, "ACKNOWLEDGED", 3))

    def test_a_recreated_person_refuses_a_field_base_0_from_before_the_delete(self) -> None:
        person = people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee")
        before = require(person_commands.metadata_state(self.db, self.ctx, person))["fields"]
        self.assertEqual({field: state["revision"] for field, state in before.items()},
                         dict.fromkeys(person_rules.SYNCED_FIELDS, 0))
        person_commands.delete_if_unlinked(self.db, self.ctx, person)
        self.assertEqual(people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee", person_id=person), person)
        for field in sorted(person_rules.SYNCED_FIELDS):
            value = "https://example.org/a.png" if field == "image_url" else "stale " + field
            status, result = self.run_command(lambda scope, field=field, value=value: persons.apply_field_operation(
                scope, person, field, persons.canonical_field(field, value), 0))
            self.assertEqual((status, result["code"], result["current_revision"]),
                             (409, "REVISION_CONFLICT", 1), field)
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, person, self.pdfs_dir))["about"], "")
        status, result = self.run_command(lambda scope: persons.apply_field_operation(
            scope, person, "about", "seen the delete", 1))
        self.assertEqual((status, result["code"], result["server_revision"]), (200, "ACKNOWLEDGED", 2))

    def test_a_recreated_group_refuses_a_field_base_0_from_before_the_delete(self) -> None:
        group = group_commands.create(self.db, self.ctx, "Readers")
        other = group_commands.create(self.db, self.ctx, "Other")
        before = require(group_commands.sync_state(self.db, self.ctx, group))["fields"]
        self.assertEqual({field: state["revision"] for field, state in before.items()},
                         dict.fromkeys(group_rules.FIELDS, 0))
        group_commands.delete(self.db, self.ctx, group)
        self.run_command(lambda scope: groups.insert_group(scope, group, "Readers"))
        stale = {"name": "Renamed", "description": "stale", "parent_id": other}
        for field, value in sorted(stale.items()):
            status, result = self.run_command(lambda scope, field=field, value=value: groups.apply_field_operation(
                scope, group, field, value, 0))
            self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"), field)
        recreated = group_commands.detail(self.db, self.ctx, group)
        assert recreated is not None
        self.assertEqual((recreated["name"], recreated["description"], recreated["parent_id"]),
                         ("Readers", "", None))

    def test_a_deleted_publisher_takes_its_aliases_and_keys(self) -> None:
        publisher = publisher_commands.create(self.db, self.ctx, "Press")["id"]
        publisher_commands.add_alias(self.db, self.ctx, publisher, "The Press")
        publisher_commands.delete(self.db, self.ctx, publisher)
        for table in ("publishers", "publisher_aliases", "publisher_keys"):
            self.assertEqual(self.count(table), 0, table)


class ConcurrencyTests(PeopleFacadeTestCase):
    def test_two_reparents_that_would_close_a_cycle(self) -> None:
        first = group_commands.create(self.db, self.ctx, "A")
        second = group_commands.create(self.db, self.ctx, "B")
        holder, contender = self.race(
            lambda scope: groups.set_field(scope, first, "parent_id", second),
            lambda: group_commands.update(self.db, self.ctx, second, {"parent_id": first}))
        self.assertEqual(holder, (True, 1))
        self.assertIsInstance(contender, GroupRuleError)
        self.assertEqual(contender.code, "PARENT_CYCLE")
        self.assertEqual(require(group_commands.detail(self.db, self.ctx, first))["parent_id"], second)
        self.assertIsNone(require(group_commands.detail(self.db, self.ctx, second))["parent_id"])

    def test_two_creates_of_one_name(self) -> None:
        holder, contender = self.race(
            lambda scope: groups.insert_group(scope, "PG-" + "1" * 32, "Circle"),
            lambda: group_commands.create(self.db, self.ctx, "CIRCLE"))
        self.assertEqual(holder, "PG-" + "1" * 32)
        self.assertIsInstance(contender, GroupRuleError)
        self.assertEqual(contender.code, "NAME_TAKEN")
        self.assertEqual(self.count("person_groups"), 1)

    def test_a_rename_racing_a_rename(self) -> None:
        first = group_commands.create(self.db, self.ctx, "One")
        second = group_commands.create(self.db, self.ctx, "Two")
        holder, contender = self.race(
            lambda scope: groups.set_field(scope, first, "name", "Same"),
            lambda: group_commands.update(self.db, self.ctx, second, {"name": "same"}))
        self.assertEqual(holder, (True, 1))
        self.assertIsInstance(contender, GroupRuleError)
        self.assertEqual(contender.code, "NAME_TAKEN")
        self.assertEqual(require(group_commands.detail(self.db, self.ctx, second))["name"], "Two")
        self.assertEqual(self.revisions("person-group-field"), {groups.field_scope(first, "name"): 1})

    def test_a_parent_found_by_name_while_another_creates_it(self) -> None:
        holder, contender = self.race(
            lambda scope: groups.insert_group(scope, "PG-" + "2" * 32, "Parent"),
            lambda: group_commands.create(self.db, self.ctx, "Child", None, "parent"))
        self.assertEqual(holder, "PG-" + "2" * 32)
        self.assertEqual(require(group_commands.detail(self.db, self.ctx, contender))["parent_id"], "PG-" + "2" * 32)
        self.assertEqual(self.count("person_groups"), 2)

    def test_two_publishers_of_one_name(self) -> None:
        holder, contender = self.race(
            lambda scope: publishers.create_publisher(scope, "R-" + "1" * 32, "Verso"),
            lambda: publisher_commands.create(self.db, self.ctx, "VERSO"))
        self.assertFalse(holder["existed"])
        self.assertEqual(contender, {"id": "R-" + "1" * 32, "name": "Verso", "existed": True})
        self.assertEqual(self.count("publishers"), 1)

    def test_an_alias_racing_a_publisher_of_the_same_name(self) -> None:
        verso = publisher_commands.create(self.db, self.ctx, "Verso")["id"]
        holder, contender = self.race(
            lambda scope: publishers.add_alias(scope, "M-" + "1" * 32, verso, "Penguin"),
            lambda: publisher_commands.create(self.db, self.ctx, "penguin"))
        self.assertIsNone(holder)
        self.assertIsInstance(contender, ValueError)
        self.assertIn("alias", str(contender))
        self.assertEqual(self.count("publishers"), 1)

    def test_two_field_edits_of_one_person_queue(self) -> None:
        person = people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee")

        def edit(scope: LibraryScope) -> Any:
            persons.lock(scope, person)
            return persons.set_field(scope, person, "about", "first")

        holder, contender = self.race(edit, lambda: person_commands.update_profile(self.db, self.ctx, person, {"about": "second"}))
        self.assertEqual(holder, (True, 1))
        self.assertIsNone(contender)
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, person, self.pdfs_dir))["about"], "second")
        self.assertEqual(require(person_commands.metadata_state(self.db, self.ctx, person))["fields"]["about"]["revision"], 2)

    def test_a_member_added_while_the_person_is_deleted(self) -> None:
        person = people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee")
        group = group_commands.create(self.db, self.ctx, "G")
        holder, contender = self.race(
            lambda scope: persons.delete_person(scope, person),
            lambda: group_commands.add_member(self.db, self.ctx, group, person))
        self.assertEqual(holder, (True, 0))
        self.assertIsInstance(contender, ValueError)
        self.assertEqual(str(contender), "Person or group not found.")
        self.assertEqual(self.count("person_group_members"), 0)

    def test_a_member_added_while_the_group_is_deleted(self) -> None:
        person = people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee")
        group = group_commands.create(self.db, self.ctx, "G")
        holder, contender = self.race(
            lambda scope: groups.delete_group(scope, group),
            lambda: group_commands.add_member(self.db, self.ctx, group, person))
        self.assertTrue(holder)
        self.assertIsInstance(contender, ValueError)
        self.assertEqual(self.count("person_group_members"), 0)

    def test_a_group_deleted_while_a_member_is_added(self) -> None:
        person = people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee")
        group = group_commands.create(self.db, self.ctx, "G")

        def add(scope: LibraryScope) -> bool:
            groups.lock_pair(scope, group, person)
            return groups.set_member(scope, group, person, True)

        holder, contender = self.race(add, lambda: group_commands.delete(self.db, self.ctx, group))
        self.assertTrue(holder)
        self.assertIsNone(contender)
        self.assertEqual(self.count("person_group_members"), 0)
        self.assertEqual(self.revisions("person-group-member"), {groups.member_scope(group, person): 2})


class CatalogTestCase(PeopleFacadeTestCase):
    def statements(self, call: Callable[[], Any]) -> tuple[Any, list[str]]:
        """Run ``call``; returns its result and the scoped statements it issued."""
        seen: list[str] = []
        real = LibraryScope.execute

        def record(scope: LibraryScope, query: Any, params: Any = None) -> Any:
            seen.append(str(query))
            return real(scope, query, params)

        with mock.patch.object(LibraryScope, "execute", autospec=True, side_effect=record):
            return call(), seen

    def plan(self, sql: LiteralString) -> str:
        rows = self.db.command(lambda tx: tx.execute("EXPLAIN " + sql, {"library_id": self.actor.library_id})
                               .fetchall())
        return "\n".join(str(next(iter(row.values()))) for row in rows)


class GroupCatalogTests(CatalogTestCase):
    """The set-based catalog counts (#153), read in one statement."""

    _CORRELATED = """
    SELECT g.id,
           (SELECT count(*) FROM prks.person_group_members m
             WHERE m.library_id = g.library_id AND m.group_id = g.id) AS member_count,
           (SELECT count(*) FROM prks.person_groups c
             WHERE c.library_id = g.library_id AND c.parent_id = g.id) AS child_count
    FROM prks.person_groups g WHERE g.library_id = %s
    """

    def seed(self, groups_: int = 6, people: int = 8, prefix: str = "Group") -> list[str]:
        ids: list[str] = []
        for i in range(groups_):
            parent = ids[i // 2] if i else None
            ids.append(group_commands.create(self.db, self.ctx, f"{prefix} {i:03d}", parent))
        for i in range(people):
            person = people_fixtures.add_person(self.db, self.ctx, "P", f"{prefix} person {i}")
            for group in ids[: i % len(ids)]:
                group_commands.add_member(self.db, self.ctx, group, person)
        return ids

    def correlated(self) -> dict[str, tuple[int, int]]:
        rows = self.sql(self._CORRELATED, (self.actor.library_id,))
        return {row["id"]: (int(row["member_count"]), int(row["child_count"])) for row in rows}

    def test_the_catalog_matches_correlated_counts(self) -> None:
        ids = self.seed()
        empty_parent = group_commands.create(self.db, self.ctx, "aa empty parent")
        group_commands.create(self.db, self.ctx, "AB leaf", empty_parent)
        rows = group_commands.catalog(self.db, self.ctx)
        self.assertEqual({row["id"]: (row["member_count"], row["child_count"]) for row in rows}, self.correlated())
        self.assertEqual([row["name"] for row in rows][:2], ["aa empty parent", "AB leaf"])
        by_id = {row["id"]: row for row in rows}
        self.assertEqual((by_id[empty_parent]["member_count"], by_id[empty_parent]["child_count"]), (0, 1))
        self.assertEqual(by_id[ids[-1]]["child_count"], 0)
        self.assertTrue(all(type(row["member_count"]) is int and type(row["child_count"]) is int for row in rows))

    def test_an_empty_catalog(self) -> None:
        self.assertEqual(self.statements(partial(group_commands.catalog, self.db, self.ctx)), ([], [queries._GROUP_CATALOG_SQL]))

    def test_the_catalog_reads_each_relationship_once_however_large(self) -> None:
        self.seed(groups_=4, people=4)
        small = self.statements(partial(group_commands.catalog, self.db, self.ctx))[1]
        self.seed(groups_=120, people=60, prefix="Bulk")
        rows, large = self.statements(partial(group_commands.catalog, self.db, self.ctx))
        self.assertEqual((len(small), len(large)), (1, 1))
        self.assertEqual({row["id"]: (row["member_count"], row["child_count"]) for row in rows}, self.correlated())
        plan = self.plan(queries._GROUP_CATALOG_SQL)
        self.assertNotIn("SubPlan", plan)  # no per-group correlated count
        # One scan of the memberships, whichever way the planner reads them.
        self.assertEqual(len(re.findall(r" on (?:prks\.)?person_group_members\b", plan)), 1, plan)


class PersonCatalogTests(CatalogTestCase):
    def test_a_membership_committed_during_the_read_is_not_half_seen(self) -> None:
        person = people_fixtures.add_person(self.db, self.ctx, "Ada", "Snapshot")
        kept = group_commands.create(self.db, self.ctx, "Kept Group")
        added = group_commands.create(self.db, self.ctx, "Added During Read")
        group_commands.add_member(self.db, self.ctx, kept, person)
        real = persons.persons
        committed: list[Any] = []

        def read_then_write(scope: LibraryScope) -> list[dict[str, Any]]:
            rows = real(scope)
            # Another writer commits a membership after the profiles are read
            # and before their group chips are.
            def write() -> None:
                with facade_context.acting_as(self.actor.context):
                    try:
                        group_commands.add_member(self.db, self.ctx, added, person)
                        committed.append(True)
                    except BaseException as exc:  # noqa: BLE001 - reported to the test thread
                        committed.append(exc)
            writer = threading.Thread(target=write)
            writer.start()
            writer.join(10)
            return rows

        with mock.patch.object(persons, "persons", side_effect=read_then_write):
            rows = person_commands.catalog(self.db, self.ctx)
        self.assertEqual(committed, [True])
        row = next(item for item in rows if item["id"] == person)
        self.assertEqual([group["id"] for group in row["groups"]], [kept])
        self.assertEqual(row["assigned_roles"], [])
        # The next read sees the committed membership.
        row = next(item for item in person_commands.catalog(self.db, self.ctx) if item["id"] == person)
        self.assertEqual(sorted(group["id"] for group in row["groups"]), sorted([kept, added]))


class PublisherCatalogTests(CatalogTestCase):
    """The publisher catalog in a bounded number of statements (#186)."""

    def publisher(self, name: str, *aliases: str) -> str:
        publisher_id = publisher_commands.create(self.db, self.ctx, name)["id"]
        for alias in aliases:
            publisher_commands.add_alias(self.db, self.ctx, publisher_id, alias)
        return publisher_id

    def works(self, *labels: str) -> None:
        for i, label in enumerate(labels):
            self.library.add_work(f"Work {label!r} {i}", publisher=label)

    def by_id(self) -> dict[str, dict[str, Any]]:
        return {row["id"]: row for row in publisher_commands.in_use(self.db, self.ctx)}

    def test_names_and_aliases_count_each_work_once(self) -> None:
        oup = self.publisher("Oxford University Press", "OUP", "Oxford UP")
        mit = self.publisher("MIT Press")
        verso = self.publisher("Verso", "Verso Books")
        nobody = self.publisher("Nobody Prints")
        eds = self.publisher("Éditions Lumière")
        self.works("Oxford University Press", "oxford university press", "  OUP ", "oup", "Oxford UP",
                   "MIT Press", "mit press", "Verso", "verso", "Verso Books",
                   "ÉDITIONS LUMIÈRE", "Éditions Lumière", "Editions Lumiere", "Unknown Publisher", "", "   ")
        rows = self.by_id()
        self.assertEqual({key: rows[key]["work_count"] for key in (oup, mit, verso, nobody, eds)},
                         {oup: 5, mit: 2, verso: 3, nobody: 0, eds: 2})  # case folds; diacritics do not
        self.assertEqual(rows[nobody], {"id": nobody, "name": "Nobody Prints", "aliases": [], "work_count": 0})
        self.assertEqual([row["name"] for row in publisher_commands.in_use(self.db, self.ctx)],
                         ["MIT Press", "Nobody Prints", "Oxford University Press", "Verso", "Éditions Lumière"])

    def test_alias_order_survives_interleaving_across_publishers(self) -> None:
        alpha = self.publisher("Alpha Press", "foxtrot", "bravo", "Delta")
        beta = self.publisher("Beta Press", "Echo", "Alpha", "charlie")
        rows = self.by_id()
        self.assertEqual(rows[alpha]["aliases"], ["bravo", "Delta", "foxtrot"])
        self.assertEqual(rows[beta]["aliases"], ["Alpha", "charlie", "Echo"])

    def test_the_catalog_name_is_trimmed(self) -> None:
        spaced = self.publisher("  Spaced Press  ")
        self.works("Spaced Press", " spaced press ")
        self.assertEqual((self.by_id()[spaced]["name"], self.by_id()[spaced]["work_count"]), ("Spaced Press", 2))

    def test_an_empty_catalog_issues_one_statement(self) -> None:
        rows, seen = self.statements(partial(publisher_commands.in_use, self.db, self.ctx))
        self.assertEqual((rows, len(seen)), ([], 1))

    def test_the_statement_count_is_bounded_as_publishers_grow(self) -> None:
        self.publisher("Verso", "Verso Books")
        self.works("Verso")
        small = self.statements(partial(publisher_commands.in_use, self.db, self.ctx))[1]
        for i in range(80):
            self.publisher(f"Bulk Press {i}", f"Bulk {i}")
            self.works(f"Bulk Press {i}", f"bulk {i}")
        rows, large = self.statements(partial(publisher_commands.in_use, self.db, self.ctx))
        self.assertEqual((len(small), len(large)), (3, 3))
        self.assertTrue(all(row["work_count"] == 2 for row in rows if row["name"].startswith("Bulk Press")))


_PROFILE = {
    "first_name": "Hannah", "aliases": "H. Arendt, Hannah Arendt", "about": "Political theorist",
    "image_url": "https://example.org/arendt.jpg", "link_wikipedia": "https://en.wikipedia.org/wiki/HA",
    "link_stanford_encyclopedia": "https://plato.stanford.edu/arendt",
    "link_iep": "https://iep.utm.edu/arendt", "links_other": "https://example.org/more",
    "birth_date": "1906-10-14", "death_date": "1975-12-04",
}
_EMPTY_PROFILE = {field: "" for field in _PROFILE}
_MISSING_PERSON = "P-" + "0" * 32


class AnswerTests(AnswerAssertions, PeopleFacadeTestCase):
    """The JSON each Person, Person Group and publisher command answers, spelled out."""

    def person(self, last_name: str, **fields: Any) -> str:
        fields.setdefault("first_name", "")
        return self.labels.name(people_fixtures.add_person(self.db, self.ctx, last_name=last_name, **fields), f"<P {last_name}>")

    def group(self, name: str, **fields: Any) -> str:
        return self.labels.name(group_commands.create(self.db, self.ctx, name, **fields), f"<PG {name}>")

    def refused_with(self, message: str, call: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        self.assert_refused(lambda: call(*args, **kwargs), message)

    def test_a_person_and_its_states(self) -> None:
        arendt = self.person("Arendt", **_PROFILE)
        self.answer(person_commands.detail(self.db, self.ctx, arendt, self.pdfs_dir), {
            **_PROFILE, "created_at": "<s>", "groups": [], "id": "<P Arendt>", "last_name": "Arendt",
            "updated_at": "<s>", "works": []})
        self.answer(person_commands.metadata_state(self.db, self.ctx, arendt), {
            "fields": {field: {"revision": 0} for field in sorted([*_PROFILE, "last_name"])},
            "person_id": "<P Arendt>"})
        for read in (lambda person_id: person_commands.detail(self.db, self.ctx, person_id, self.pdfs_dir), partial(person_commands.metadata_state, self.db, self.ctx),
                     partial(group_commands.person_state, self.db, self.ctx)):
            self.assertIsNone(read(_MISSING_PERSON))
        self.refused_with("javascript:alert(1) is not a valid value for image_url", partial(person_commands.update_profile, self.db, self.ctx), arendt,
                          {"image_url": "javascript:alert(1)"})
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, arendt, self.pdfs_dir))["image_url"], _PROFILE["image_url"])

    def test_the_person_catalog(self) -> None:
        for name in ("beta", "Alpha", "_under", "Zed", "Ärzt"):
            self.person(name)
        thinkers = self.group("Thinkers")
        rows = person_commands.catalog(self.db, self.ctx)
        self.assertEqual([row["last_name"] for row in rows], ["Alpha", "Zed", "_under", "beta", "Ärzt"])
        group_commands.add_member(self.db, self.ctx, thinkers, rows[0]["id"])
        rows = person_commands.catalog(self.db, self.ctx)
        self.answer(rows[0], {**_EMPTY_PROFILE, "assigned_roles": [], "created_at": "<s>",
                              "groups": [{"id": "<PG Thinkers>", "name": "Thinkers"}], "id": "<P Alpha>",
                              "last_name": "Alpha", "updated_at": "<s>"})
        self.assertNotIn("works", rows[0])
        self.assertEqual(etag_for_representation('persons', rows), etag_for_representation("persons", rows))

    def test_profile_edits_and_their_groups(self) -> None:
        weil = self.person("Weil", first_name="Simone")
        for fields in ({"about": "Philosopher"}, {"about": "Philosopher"},
                       {"first_name": "S.", "aliases": "Simone Weil"}, {"not_a_field": "ignored"}):
            self.assertIsNone(person_commands.update_profile(self.db, self.ctx, weil, fields))
        revisions = {field: value["revision"]
                     for field, value in require(person_commands.metadata_state(self.db, self.ctx, weil))["fields"].items()}
        self.assertEqual({field: n for field, n in revisions.items() if n}, {"about": 1, "first_name": 1, "aliases": 1})
        adorno = self.person("Adorno")
        frankfurt, critical = self.group("Frankfurt"), self.group("Critical theory")
        person_commands.update_profile(self.db, self.ctx, adorno, {"about": "T"}, [frankfurt, critical, frankfurt, ""])
        person_commands.update_profile(self.db, self.ctx, adorno, {}, [critical])
        state = self.labels.show(require(group_commands.person_state(self.db, self.ctx, adorno)))
        self.assertEqual(sorted(state["groups"], key=lambda group: group["group_id"]), [
            {"group_id": "<PG Critical theory>", "present": True, "revision": 1},
            {"group_id": "<PG Frankfurt>", "present": False, "revision": 2}])
        self.answer(require(person_commands.detail(self.db, self.ctx, adorno, self.pdfs_dir)), {
            "about": "T", "groups": [{"id": "<PG Critical theory>", "name": "Critical theory", "parent_id": None}]})
        benjamin = self.person("Benjamin")
        self.refused_with(f"Unknown group id: {_MISSING_GROUP}", partial(person_commands.update_profile, self.db, self.ctx), benjamin,
                          {"about": "Critic"}, [_MISSING_GROUP])
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, benjamin, self.pdfs_dir))["about"], "")
        self.refused_with("group_ids must be a JSON array", partial(person_commands.update_profile, self.db, self.ctx), benjamin, {},
                          "not-a-list")
        # An unknown Person is refused with groups and ignored without.
        self.refused_with("Person not found.", partial(person_commands.update_profile, self.db, self.ctx), _MISSING_PERSON, {"about": "x"}, [])
        self.assertIsNone(person_commands.update_profile(self.db, self.ctx, _MISSING_PERSON, {"about": "x"}))

    def test_a_person_delete(self) -> None:
        leaving, club = self.person("Leaving"), self.group("Club")
        group_commands.add_member(self.db, self.ctx, club, leaving)
        self.assertIsNone(person_commands.delete_if_unlinked(self.db, self.ctx, leaving))
        self.assertIsNone(person_commands.detail(self.db, self.ctx, leaving, self.pdfs_dir))
        self.answer(group_commands.detail(self.db, self.ctx, club), {"member_count": 0, "members": []})
        self.answer(group_commands.sync_state(self.db, self.ctx, club), {
            "fields": {"description": {"revision": 0}, "name": {"revision": 0}, "parent_id": {"revision": 0}},
            "group_id": "<PG Club>", "members": [{"person_id": "<P Leaving>", "present": False, "revision": 2}]})
        for missing in (leaving, ""):
            self.refused_with("Person not found.", partial(person_commands.delete_if_unlinked, self.db, self.ctx), missing)

    def test_groups_their_parents_and_the_catalog(self) -> None:
        child = self.group("Frankfurt School", parent_name="Philosophy", description="Critical theory")
        parent = self.labels.name(self.group_id("Philosophy"), "<PG Philosophy>")
        self.answer(group_commands.detail(self.db, self.ctx, child), {
            "children": [], "created_at": "<s>", "description": "Critical theory", "id": "<PG Frankfurt School>",
            "member_count": 0, "members": [], "name": "Frankfurt School",
            "parent": {"id": "<PG Philosophy>", "name": "Philosophy"}, "parent_id": "<PG Philosophy>",
            "updated_at": "<s>"})
        self.answer(require(group_commands.detail(self.db, self.ctx, parent))["children"], [
            {"description": "Critical theory", "id": "<PG Frankfurt School>", "member_count": 0,
             "name": "Frankfurt School", "parent_id": "<PG Philosophy>"}])
        rows = group_commands.catalog(self.db, self.ctx)
        self.answer(rows, [
            {"child_count": 0, "created_at": "<s>", "description": "Critical theory", "id": "<PG Frankfurt School>",
             "member_count": 0, "name": "Frankfurt School", "parent_id": "<PG Philosophy>", "updated_at": "<s>"},
            {"child_count": 1, "created_at": "<s>", "description": "", "id": "<PG Philosophy>", "member_count": 0,
             "name": "Philosophy", "parent_id": None, "updated_at": "<s>"}])
        self.assertEqual(etag_for_representation('person-groups', rows), etag_for_representation("person-groups", rows))
        # An existing parent is found by name.
        self.group("Ethics", parent_name="philosophy")
        self.assertEqual(require(group_commands.detail(self.db, self.ctx, parent))["children"][0]["name"], "Ethics")
        for fields, message in (({"name": "frankfurt school"}, "A group with this name already exists."),
                                ({"name": ""}, "Group name is required."),
                                ({"name": "Same", "parent_name": "SAME"},
                                 "Parent group cannot have the same name as the new group."),
                                ({"name": "x" * 201}, "Group name exceeds 200 bytes."),
                                ({"name": "Orphan", "parent_id": _MISSING_GROUP}, "Parent group not found."),
                                ({"name": "Long", "description": "d" * 2001}, "Group description exceeds 2000 bytes.")):
            with self.subTest(message=message):
                self.refused_with(message, partial(group_commands.create, self.db, self.ctx), **fields)

    def test_group_edits_the_hierarchy_and_a_delete(self) -> None:
        group = self.group("Old name")
        self.group("Other")
        group_commands.update(self.db, self.ctx, group, {"name": "New name", "description": "About"})
        group_commands.update(self.db, self.ctx, group, {"name": "New name"})
        group_commands.update(self.db, self.ctx, group, {"parent_name": "Created parent"})
        self.answer(group_commands.sync_state(self.db, self.ctx, group), {
            "fields": {"description": {"revision": 1}, "name": {"revision": 1}, "parent_id": {"revision": 1}}})
        self.refused_with("A group with this name already exists.", partial(group_commands.update, self.db, self.ctx), group,
                          {"name": "other"})
        self.refused_with("Group not found.", partial(group_commands.update, self.db, self.ctx), _MISSING_GROUP, {"name": "x"})
        group_commands.update(self.db, self.ctx, group, {"parent_id": None})
        self.assertIsNone(require(group_commands.detail(self.db, self.ctx, group))["parent"])
        top = self.group("Top")
        middle = self.group("Middle", parent_name="Top")
        bottom = self.group("Bottom", parent_name="Middle")
        self.refused_with("Cannot set parent to a subgroup (cycle).", partial(group_commands.update, self.db, self.ctx), top,
                          {"parent_name": "Bottom"})
        self.refused_with("A group cannot be its own parent.", partial(group_commands.update, self.db, self.ctx), top,
                          {"parent_name": "Top"})
        member = self.person("Member")
        group_commands.add_member(self.db, self.ctx, middle, member)
        # A delete moves the children to its parent and drops its members.
        self.assertIsNone(group_commands.delete(self.db, self.ctx, middle))
        self.answer(group_commands.detail(self.db, self.ctx, bottom), {"parent": {"id": "<PG Top>", "name": "Top"},
                                                           "parent_id": "<PG Top>"})
        self.assertEqual(require(group_commands.sync_state(self.db, self.ctx, bottom))["fields"]["parent_id"],
                         {"revision": 1})
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, member, self.pdfs_dir))["groups"], [])
        self.refused_with("Group not found.", partial(group_commands.delete, self.db, self.ctx), middle)

    def test_membership_from_both_ends(self) -> None:
        readers = self.group("Readers")
        people = [self.person(name) for name in ("b", "A", "c")]
        for person in (*people, people[0]):
            group_commands.add_member(self.db, self.ctx, readers, person)
        for _ in range(2):
            group_commands.remove_member(self.db, self.ctx, readers, people[2])
        detail = require(group_commands.detail(self.db, self.ctx, readers))
        self.answer(detail, {"member_count": 2, "members": [
            {**_EMPTY_PROFILE, "assigned_roles": [], "created_at": "<s>",
             "groups": [{"id": "<PG Readers>", "name": "Readers"}], "id": "<P A>", "last_name": "A",
             "updated_at": "<s>"},
            {"id": "<P b>", "last_name": "b"}]})
        state = require(group_commands.sync_state(self.db, self.ctx, readers))
        # Members come in id order, which the run makes up.
        self.answer(sorted(self.labels.show(state["members"]), key=lambda member: member["person_id"]), [
            {"person_id": "<P A>", "present": True, "revision": 1},
            {"person_id": "<P b>", "present": True, "revision": 1},
            {"person_id": "<P c>", "present": False, "revision": 2}])
        self.answer(group_commands.person_state(self.db, self.ctx, people[2]), {
            "groups": [{"group_id": "<PG Readers>", "present": False, "revision": 2}], "person_id": "<P c>"})
        self.answer([row["groups"] for row in person_commands.catalog(self.db, self.ctx)],
                    [[{"id": "<PG Readers>", "name": "Readers"}]] * 2 + [[]])
        self.refused_with("Person or group not found.", lambda person_id, group_id: group_commands.add_member(self.db, self.ctx, group_id, person_id), people[0], _MISSING_GROUP)
        self.refused_with("Person or group not found.", lambda person_id, group_id: group_commands.add_member(self.db, self.ctx, group_id, person_id), _MISSING_PERSON, readers)

    def test_publishers(self) -> None:
        created = publisher_commands.create(self.db, self.ctx, "Verso")
        verso = self.labels.name(created["id"], "<R Verso>")
        self.answer(created, {"existed": False, "id": "<R Verso>", "name": "Verso"})
        self.answer(publisher_commands.create(self.db, self.ctx, "  verso "), {"existed": True, "id": "<R Verso>", "name": "Verso"})
        self.refused_with("publisher name is empty", partial(publisher_commands.create, self.db, self.ctx), "   ")
        penguin = publisher_commands.create(self.db, self.ctx, "Penguin")["id"]
        publisher_commands.add_alias(self.db, self.ctx, verso, "Verso Books")
        self.assertIsNone(publisher_commands.add_alias(self.db, self.ctx, verso, "verso books"))
        for publisher_id, alias, message in ((verso, "", "alias is empty"),
                                             (verso, "VERSO", "alias matches canonical publisher name"),
                                             (verso, "penguin", "alias conflicts with another publisher name"),
                                             ("R-" + "0" * 32, "x", "publisher not found"),
                                             (penguin, "Verso Books", "alias already used")):
            with self.subTest(alias=alias):
                self.refused_with(message, partial(publisher_commands.add_alias, self.db, self.ctx), publisher_id, alias)
        self.labels.name(publisher_commands.create(self.db, self.ctx, "beta press")["id"], "<R beta>")
        publisher_commands.delete(self.db, self.ctx, penguin)
        for title, publisher in (("one", "Verso"), ("two", " verso books "), ("three", "Unknown"), ("four", "")):
            self.library.add_work(title, publisher=publisher)
        self.answer(publisher_commands.in_use(self.db, self.ctx), [
            {"aliases": [], "id": "<R beta>", "name": "beta press", "work_count": 0},
            {"aliases": ["Verso Books"], "id": "<R Verso>", "name": "Verso", "work_count": 2}])
        self.assertIs(publisher_commands.delete_alias(self.db, self.ctx, verso, "VERSO BOOKS"), True)
        self.assertIs(publisher_commands.delete_alias(self.db, self.ctx, verso, "Verso Books"), False)
        self.assertIs(publisher_commands.delete_alias(self.db, self.ctx, verso, "  "), False)
        publisher_commands.add_alias(self.db, self.ctx, verso, "Verso Books")
        self.assertIsNone(publisher_commands.delete(self.db, self.ctx, verso))
        self.assertIsNone(publisher_commands.delete(self.db, self.ctx, verso))
        # The alias went with its publisher, so another may take it.
        other = publisher_commands.create(self.db, self.ctx, "Other")["id"]
        self.assertIsNone(publisher_commands.add_alias(self.db, self.ctx, other, "Verso Books"))
