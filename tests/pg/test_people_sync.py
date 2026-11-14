"""The People sync handlers on PostgreSQL (L-P-1 #632, PR 4).

``tests/test_people_sync_parity.py`` proves each People operation answers
as on SQLite. These tests cover what only PostgreSQL has: races decided by
row locks, SERIALIZABLE and the unique index, each held open until the
other side waits (§7.1); one ``op_id`` replayed, reused or sent
concurrently; a recreated id refusing bases from before its delete; Library
isolation, a reader and the epoch; the rerun the protocol gives a race the
index or key decides; NUL, which PostgreSQL cannot store; and
``CREATE_WORK`` filing roles through :class:`RoleFiling`.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from unittest import mock

from backend import person_metadata_sync as person_rules
from backend import sync_protocol
from backend import work_metadata_sync as results
from backend import work_role_sync
from backend.db.database import Isolation
from backend.db.errors import ConstraintViolationError
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.people import groups, persons, roles
from backend.entity_ids import generate, is_distributed
from tests import acting_context as facade_context
from backend.sync import protocol
from backend.sync.families import Environment, FamilyHandler, Result
from backend.sync.handlers import person as person_handlers
from backend.sync.handlers import person_group as group_handlers
from backend.sync.handlers.work_role import RoleFiling
from tests.pg.test_people_facade import PeopleFacadeTestCase
from tests.pg.test_works_sync import StandInFiling, SyncTestCase, create_payload

_ENTITY_TYPES = {"CREATE_PERSON": "person", "DELETE_PERSON": "person",
                 "SET_PERSON_METADATA_FIELD": "person"}
_GROUP_OPERATIONS = ("CREATE_PERSON_GROUP", "SET_PERSON_GROUP_FIELD", "ADD_PERSON_GROUP_MEMBER",
                     "REMOVE_PERSON_GROUP_MEMBER", "DELETE_PERSON_GROUP")
_NAME_KEY = "person_groups_library_id_name_key_key"


def person_payload(**changes: Any) -> dict[str, Any]:
    payload = {name: "" for name in persons.FIELDS}
    payload.update(first_name="Ann", last_name="Lee")
    payload.update(changes)
    return payload


def group_payload(name: str, **changes: Any) -> dict[str, Any]:
    return {"name": name, "description": "", "parent_id": "", **changes}


class ComposedFiling:
    """The Organization stand-in, then the People half, as L-O-1 will compose them."""

    def __init__(self) -> None:
        self.folders = StandInFiling()
        self.roles = RoleFiling()

    def prepare(self, scope: LibraryScope, work_id: str, fields: dict[str, Any]) -> dict[str, Any] | Result:
        folder = self.folders.prepare(scope, work_id, fields)
        if isinstance(folder, tuple):
            return folder
        people = self.roles.prepare(scope, work_id, fields)
        return people if isinstance(people, tuple) else {**folder, **people}

    def file(self, scope: LibraryScope, work_id: str, fields: dict[str, Any],
             prepared: dict[str, Any]) -> dict[str, Any]:
        return {**self.folders.file(scope, work_id, fields, prepared),
                **self.roles.file(scope, work_id, fields, prepared)}


class PeopleSyncTestCase(SyncTestCase, PeopleFacadeTestCase):
    def envelope(self, operation: str, entity_id: str, payload: dict[str, Any],
                 base: Optional[int] = None, **changes: Any) -> dict[str, Any]:
        entity_type = ("person-group" if operation in _GROUP_OPERATIONS
                       else _ENTITY_TYPES.get(operation, "work"))
        return super().envelope(operation, entity_id, payload, base, entity_type=entity_type, **changes)

    def create_person(self, person_id: Optional[str] = None, **fields: Any) -> str:
        person_id = person_id or "P-" + uuid.uuid4().hex.upper()
        status, result = self.send(self.envelope("CREATE_PERSON", person_id, person_payload(**fields)))
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"), result)
        return person_id

    def create_group(self, name: str, group_id: Optional[str] = None, **fields: Any) -> str:
        group_id = group_id or "PG-" + uuid.uuid4().hex.upper()
        status, result = self.send(self.envelope("CREATE_PERSON_GROUP", group_id, group_payload(name, **fields)))
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"), result)
        return group_id

    def person_field(self, person_id: str, field: str, value: str, base: int, **changes: Any) -> dict[str, Any]:
        return self.envelope("SET_PERSON_METADATA_FIELD", person_id, {"field": field, "value": value}, base,
                             **changes)

    def group_field(self, group_id: str, field: str, value: str, base: int) -> dict[str, Any]:
        return self.envelope("SET_PERSON_GROUP_FIELD", group_id, {"field": field, "value": value}, base)

    def member(self, operation: str, group_id: str, person_id: str, base: int) -> dict[str, Any]:
        return self.envelope(operation, group_id, {"person_id": person_id}, base)

    def role(self, operation: str, work_id: str, person_id: str, base: int, role_type: str = "Author",
             credit: Optional[str] = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"person_id": person_id, "role_type": role_type}
        if credit is not None:
            payload["credit_name"] = credit
        return self.envelope(operation, work_id, payload, base)

    def applying(self, handler: FamilyHandler, data: dict[str, Any]) -> Any:
        """A holder that applies ``data`` with ``handler`` in the race's open transaction."""
        op = sync_protocol.normalize_envelope(data)
        return lambda scope: handler.apply(scope, op, datetime.now(timezone.utc), self.env)

    def every_operation(self, person_id: str, group_id: str, work_id: str) -> list[dict[str, Any]]:
        """One operation of each People family that changes an existing entity."""
        return [
            self.person_field(person_id, "about", "x", 0),
            self.group_field(group_id, "description", "x", 0),
            self.member("ADD_PERSON_GROUP_MEMBER", group_id, person_id, 0),
            self.member("REMOVE_PERSON_GROUP_MEMBER", group_id, person_id, 0),
            self.role("ADD_WORK_PERSON_ROLE", work_id, person_id, 0, credit=""),
            self.role("SET_WORK_PERSON_ROLE_CREDIT", work_id, person_id, 0, credit="A. Lee"),
            self.role("REMOVE_WORK_PERSON_ROLE", work_id, person_id, 0),
        ]


class IsolationDeclarationTests(unittest.TestCase):
    def test_the_peer_table_writers_of_the_protected_person_delete_are_serializable(self) -> None:
        # §7.1: the delete reads roles and person_group_members as sets, so
        # every operation that adds a row to them runs SERIALIZABLE.
        serializable = {operation for operation, handler in protocol._HANDLERS.items()
                        if handler.isolation is Isolation.SERIALIZABLE}
        # The Tag merge's peer-table writers (L-O-1) are listed too, and the
        # Work merge's (a Work filed in a Folder, a first note, a citation).
        self.assertEqual(serializable, {"DELETE_PERSON", "ADD_WORK_PERSON_ROLE", "ADD_PERSON_GROUP_MEMBER",
                                        "CREATE_WORK", "CREATE_TAG", "MERGE_TAG", "ADD_WORK_TAG",
                                        "ADD_FOLDER_TAG", "SET_WORK_FOLDER", "SET_WORK_PLAYLIST",
                                        "SET_WORK_RESEARCH_NOTE", "SET_WORK_PRIVATE_NOTE",
                                        "CREATE_ARGUMENT", "SET_ARGUMENT_SOURCES"})


class LedgerTests(PeopleSyncTestCase):
    def test_a_replay_answers_as_the_first_time_and_a_reuse_is_refused(self) -> None:
        person_id = "P-" + uuid.uuid4().hex.upper()
        data = self.envelope("CREATE_PERSON", person_id, person_payload())
        first = self.send(data)
        self.library.update_person_profile(person_id, {"last_name": "Leigh"})
        self.assertEqual(self.send(data), first)  # the stored body, not today's row
        self.assertEqual(self.send(dict(data, payload=person_payload(first_name="Bo"))),
                         (409, {"code": "OP_ID_REUSE"}))
        edit = self.person_field(person_id, "about", "Bio", 0)
        self.assertEqual(self.send(edit)[1]["server_revision"], 1)
        self.assertEqual(self.send(edit)[1]["server_revision"], 1)
        self.assertEqual(len(self.ledger()), 2)

    def test_each_operation_sent_concurrently_applies_once(self) -> None:
        person_id = self.create_person()
        group_id = self.create_group("Readers")
        work_id = self.library.add_work("Essay")
        for data in [self.envelope("CREATE_PERSON", "P-" + uuid.uuid4().hex.upper(), person_payload()),
                     self.envelope("CREATE_PERSON_GROUP", "PG-" + uuid.uuid4().hex.upper(),
                                   group_payload("Writers")),
                     self.group_field(group_id, "name", "Circle", 0),
                     self.member("ADD_PERSON_GROUP_MEMBER", group_id, person_id, 0),
                     self.role("ADD_WORK_PERSON_ROLE", work_id, person_id, 0, credit="A. Lee")]:
            with self.subTest(operation=data["operation"]):
                results = self.concurrently([lambda data=data: self.send(data) for _ in range(4)])
                self.assertTrue(all(result == results[0] for result in results), results)
                self.assertEqual(results[0][1]["code"], "ACKNOWLEDGED")
        self.assertEqual(self.library.get_person_groups_state(person_id)["groups"],
                         [{"group_id": group_id, "revision": 1, "present": True}])
        self.assertEqual(self.library.get_work_roles_state(work_id)["scopes"],
                         [{"person_id": person_id, "role_type": "Author", "revision": 1, "present": True}])
        self.assertEqual(self.library.get_person_metadata_state(person_id)["fields"]["aliases"]["revision"], 1)

    def test_nul_is_refused_before_the_ledger(self) -> None:
        person_id = self.create_person()
        group_id = self.create_group("Readers")
        for data in (self.envelope("CREATE_PERSON", "P-" + uuid.uuid4().hex.upper(),
                                   person_payload(about="a\x00b")),
                     self.person_field(person_id, "about", "a\x00b", 0),
                     self.group_field(group_id, "name", "a\x00b", 0),
                     self.member("ADD_PERSON_GROUP_MEMBER", group_id, "P-\x00", 0)):
            with self.subTest(operation=data["operation"]):
                self.assertEqual(self.send(data), (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual(len(self.ledger()), 2)


class EnvelopeAndBoundTests(PeopleSyncTestCase):
    """What the People envelopes refuse and what their results hold (retired SQLite people sync tests)."""

    def test_ids_fields_and_base_revisions_are_refused_before_the_ledger(self) -> None:
        person_id, group_id = self.create_person(), self.create_group("Analysts")
        before = self.ledger()
        refused = [("legacy id", self.envelope("CREATE_PERSON", "P-A1B2C3D4", person_payload()), "INVALID_ENVELOPE"),
                   ("person base", self.person_field(person_id, "about", "x", 0) | {"base_revision": None},
                    "INVALID_BASE_REVISION"),
                   ("portrait", self.person_field(person_id, "image_url", "javascript:alert(1)", 0),
                    "INVALID_ENVELOPE"),
                   ("group base", self.group_field(group_id, "name", "X", 0) | {"base_revision": None},
                    "INVALID_BASE_REVISION"),
                   ("empty name", self.group_field(group_id, "name", "   ", 0), "INVALID_ENVELOPE"),
                   ("person delete base", self.envelope("DELETE_PERSON", person_id, {}, 0), "INVALID_BASE_REVISION"),
                   ("group delete base", self.envelope("DELETE_PERSON_GROUP", group_id, {}, 0),
                    "INVALID_BASE_REVISION")]
        for field in ("id", "created_at", "updated_at", "aliases; DROP TABLE persons", ""):
            refused.append(("person field " + field, self.person_field(person_id, field, "x", 0), "INVALID_ENVELOPE"))
        for field in ("id", "created_at", "updated_at", "name; DROP TABLE person_groups", ""):
            refused.append(("group field " + field, self.group_field(group_id, field, "x", 0), "INVALID_ENVELOPE"))
        for label, data, code in refused:
            with self.subTest(label):
                self.assertEqual(self.send(data), (400, {"code": code}))
        self.assertEqual(self.ledger(), before)
        self.assertEqual(person_rules.SYNCED_FIELDS, frozenset(persons.FIELDS))

    def test_a_long_value_is_never_echoed_and_a_long_conflict_still_fits(self) -> None:
        person_id, group_id = self.create_person(), self.create_group("Analysts")
        status, result = self.send(self.person_field(person_id, "about", "x" * 20000, 0))
        self.assertEqual((status, result["value_omitted"], "value" in result), (200, True, False))
        self.assertLessEqual(results.serialized_result_bytes(result), results.MAX_DURABLE_RESULT_BYTES)
        self.assertEqual(self.library.get_person(person_id)["about"], "x" * 20000)
        self.send(self.person_field(person_id, "about", "A" * 40000, 1))
        status, result = self.send(self.person_field(person_id, "about", "B" * 40000, 0))
        self.assertEqual((status, "current_preview" in result, "current_value" in result), (409, True, False))
        self.assertLessEqual(results.serialized_result_bytes(result), results.MAX_DURABLE_RESULT_BYTES)
        self.send(self.group_field(group_id, "description", "A" * 2000, 0))
        status, result = self.send(self.group_field(group_id, "description", "B" * 2000, 0))
        self.assertEqual(status, 409)
        self.assertLessEqual(results.serialized_result_bytes(result), results.MAX_DURABLE_RESULT_BYTES)

    def test_a_role_may_depend_on_the_creation_of_its_person(self) -> None:
        work_id, person_id = self.library.add_work("Essay"), "P-" + uuid.uuid4().hex.upper()
        create = self.envelope("CREATE_PERSON", person_id, person_payload())
        role = self.role("ADD_WORK_PERSON_ROLE", work_id, person_id, 0, credit="") | {"depends_on": [create["op_id"]]}
        self.assertEqual(self.send(role), (400, {"code": "UNSATISFIED_DEPENDENCY"}))
        self.assertEqual((self.library.get_person(person_id), self.library.get_work_roles(work_id)), (None, []))
        self.assertEqual(self.send(create)[1]["code"], "ACKNOWLEDGED")
        status, result = self.send(role)
        self.assertEqual((status, result["code"], result["present"]), (200, "ACKNOWLEDGED", True))
        self.assertEqual([(row["id"], row["role_type"]) for row in self.library.get_work_roles(work_id)],
                         [(person_id, "Author")])
        self.assertTrue(is_distributed(self.library.add_person("Online", "Person"), "P"))

    def test_a_membership_row_without_a_revision_reads_as_revision_0(self) -> None:
        person_id, group_id = self.create_person(), self.create_group("Analysts")
        self.sql("INSERT INTO prks.person_group_members (library_id, person_id, group_id) VALUES (%s, %s, %s)",
                 (self.actor.library_id, person_id, group_id))
        self.assertEqual(self.library.get_person_group_sync_state(group_id)["members"],
                         [{"person_id": person_id, "revision": 0, "present": True}])


class RaceRerunTests(PeopleSyncTestCase):
    """The protocol reruns an operation once when a constraint the family names refuses it."""

    def renaming_into(self, taken: str, target: str) -> tuple[FamilyHandler, list[int]]:
        calls: list[int] = []

        def apply(scope: LibraryScope, op: dict[str, Any], received_at: Any, env: Environment) -> Result:
            calls.append(1)
            scope.execute("UPDATE prks.person_groups SET name = %(name)s"
                          " WHERE library_id = %(library_id)s AND id = %(group_id)s",
                          {"name": taken, "group_id": target})
            return 200, {"code": "ACKNOWLEDGED"}

        return FamilyHandler(lambda op: None, apply, retry_constraints=frozenset({_NAME_KEY})), calls

    def test_a_named_constraint_reruns_once_and_others_never(self) -> None:
        self.create_group("Taken")
        target = self.create_group("Free")
        handler, calls = self.renaming_into("Taken", target)
        data = self.group_field(target, "name", "Taken", 0)
        with mock.patch.dict(protocol._HANDLERS, {"SET_PERSON_GROUP_FIELD": handler}):
            with self.assertRaises(ConstraintViolationError):
                self.send(data)
        self.assertEqual(len(calls), 2)
        unlisted = FamilyHandler(handler.validate_fn, handler.apply_fn)
        with mock.patch.dict(protocol._HANDLERS, {"SET_PERSON_GROUP_FIELD": unlisted}):
            with self.assertRaises(ConstraintViolationError):
                self.send(data)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(self.ledger()), 2)  # the two creates; never a half-applied claim


class PersonRaceTests(PeopleSyncTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.person_id = self.create_person()
        self.work_id = self.library.add_work("Essay")

    def test_a_delete_waiting_for_a_link_refuses_with_links(self) -> None:
        holder, contender = self.race(
            lambda scope: roles.set_role(scope, roles.Role(self.work_id, self.person_id, "Author"), True),
            lambda: self.send(self.envelope("DELETE_PERSON", self.person_id, {})))
        self.assertTrue(holder)
        self.assertEqual(contender, (409, {"code": "PERSON_HAS_LINKS", "person_id": self.person_id,
                                           "current_revision": 1}))
        self.assertIsNotNone(self.library.get_person(self.person_id))
        self.assertEqual(self.ledger()[-1]["status"], "PERSON_HAS_LINKS")

    def test_a_link_waiting_for_a_delete_finds_no_person(self) -> None:
        holder, contender = self.race(
            lambda scope: persons.delete_person(scope, self.person_id),
            lambda: self.send(self.role("ADD_WORK_PERSON_ROLE", self.work_id, self.person_id, 0, credit="")))
        self.assertEqual(holder, (True, 0))
        self.assertEqual(contender[0], 404)
        self.assertEqual(contender[1]["code"], "PERSON_NOT_FOUND")
        self.assertEqual(self.count("roles"), 0)

    def test_a_delete_waiting_for_a_membership_removes_it(self) -> None:
        group_id = self.create_group("Readers")
        holder, contender = self.race(
            lambda scope: groups.set_member(scope, group_id, self.person_id, True),
            lambda: self.send(self.envelope("DELETE_PERSON", self.person_id, {})))
        self.assertTrue(holder)
        self.assertEqual(contender, (200, {"code": "ACKNOWLEDGED", "person_id": self.person_id,
                                           "changed": True}))
        self.assertEqual(self.count("person_group_members"), 0)
        pair = groups.member_scope(group_id, self.person_id)
        self.assertEqual(self.revisions("person-group-member"), {pair: 2})  # the add, then its tombstone

    def test_a_membership_waiting_for_a_delete_finds_no_person(self) -> None:
        group_id = self.create_group("Readers")
        holder, contender = self.race(
            lambda scope: persons.delete_person(scope, self.person_id),
            lambda: self.send(self.member("ADD_PERSON_GROUP_MEMBER", group_id, self.person_id, 0)))
        self.assertEqual(holder, (True, 0))
        self.assertEqual(contender, (404, {"code": "PERSON_NOT_FOUND", "group_id": group_id,
                                           "person_id": self.person_id}))
        self.assertEqual((self.count("person_group_members"), self.revisions("person-group-member")), (0, {}))

    def test_a_credit_change_waiting_for_a_rename_keeps_both(self) -> None:
        self.send(self.role("ADD_WORK_PERSON_ROLE", self.work_id, self.person_id, 0, credit=""))

        def rename(scope: LibraryScope) -> Any:
            persons.lock(scope, self.person_id)
            return persons.set_field(scope, self.person_id, "last_name", "Leigh")

        holder, contender = self.race(rename, lambda: self.send(
            self.role("SET_WORK_PERSON_ROLE_CREDIT", self.work_id, self.person_id, 1, credit="A. Lee")))
        self.assertEqual(holder, (True, 1))
        self.assertEqual((contender[0], contender[1]["server_revision"], contender[1]["last_name"]),
                         (200, 2, "Leigh"))
        self.assertEqual(self.library.get_person(self.person_id)["aliases"], "A. Lee")

    def test_a_repeated_create_waiting_for_a_delete_creates_the_person_again(self) -> None:
        holder, contender = self.race(
            lambda scope: persons.delete_person(scope, self.person_id),
            lambda: self.send(self.envelope("CREATE_PERSON", self.person_id, person_payload(last_name="Again"))))
        self.assertEqual(holder, (True, 0))
        self.assertEqual((contender[0], contender[1]["changed"], contender[1]["person"]["last_name"]),
                         (200, True, "Again"))
        self.assertEqual(self.library.get_person(self.person_id)["last_name"], "Again")

    def test_a_delete_waiting_for_a_repeated_create_follows_its_acknowledgement(self) -> None:
        holder, contender = self.race(
            self.applying(person_handlers.CREATE_HANDLER,
                          self.envelope("CREATE_PERSON", self.person_id, person_payload(last_name="Other"))),
            lambda: self.send(self.envelope("DELETE_PERSON", self.person_id, {})))
        self.assertEqual((holder[0], holder[1]["changed"], holder[1]["person"]["last_name"]), (200, False, "Lee"))
        self.assertEqual(contender, (200, {"code": "ACKNOWLEDGED", "person_id": self.person_id,
                                           "changed": True}))
        self.assertIsNone(self.library.get_person(self.person_id))

    def test_creates_of_one_id_queue_and_the_second_converges(self) -> None:
        person_id = "P-" + uuid.uuid4().hex.upper()
        holder, contender = self.race(
            lambda scope: persons.create_person(scope, person_id, persons.canonical_fields(
                person_payload(first_name="First"))),
            lambda: self.send(self.envelope("CREATE_PERSON", person_id, person_payload(first_name="Second"))))
        self.assertTrue(holder)
        self.assertEqual((contender[0], contender[1]["changed"], contender[1]["person"]["first_name"]),
                         (200, False, "First"))


class GroupRaceTests(PeopleSyncTestCase):
    def test_a_rename_that_loses_the_name_is_ledgered_name_taken(self) -> None:
        first = self.create_group("One")
        second = self.create_group("Two")
        holder, contender = self.race(lambda scope: groups.set_field(scope, first, "name", "Circle"),
                                      lambda: self.send(self.group_field(second, "name", "circle", 0)))
        self.assertEqual(holder, (True, 1))
        self.assertEqual((contender[0], contender[1]["code"]), (409, "NAME_TAKEN"))
        self.assertEqual(self.ledger()[-1]["status"], "NAME_TAKEN")
        self.assertEqual(self.library.get_person_group(second)["name"], "Two")

    def test_a_create_that_loses_the_name_is_ledgered_name_taken(self) -> None:
        group_id = "PG-" + uuid.uuid4().hex.upper()
        holder, contender = self.race(
            lambda scope: groups.insert_group(scope, "PG-" + uuid.uuid4().hex.upper(), "Circle"),
            lambda: self.send(self.envelope("CREATE_PERSON_GROUP", group_id, group_payload("CIRCLE"))))
        self.assertIsNotNone(holder)
        self.assertEqual(contender, (409, {"code": "NAME_TAKEN", "group_id": group_id,
                                           "message": "A group with this name already exists."}))
        self.assertEqual(self.count("person_groups"), 1)
        self.assertEqual(self.ledger()[-1]["status"], "NAME_TAKEN")

    def test_creates_of_one_id_converge(self) -> None:
        group_id = "PG-" + uuid.uuid4().hex.upper()
        for name in ("Circle", "Another name"):
            with self.subTest(name=name):
                holder, contender = self.race(
                    lambda scope: groups.insert_group(scope, group_id, "Circle"),
                    lambda name=name: self.send(self.envelope("CREATE_PERSON_GROUP", group_id,
                                                              group_payload(name))))
                self.assertEqual(holder, group_id)
                self.assertEqual((contender[0], contender[1]["changed"], contender[1]["group"]["name"]),
                                 (200, False, "Circle"))
                self.library.delete_person_group(group_id)

    def test_a_repeated_create_waiting_for_a_delete_creates_the_group_again(self) -> None:
        group_id = self.create_group("Readers")
        holder, contender = self.race(
            lambda scope: groups.delete_group(scope, group_id),
            lambda: self.send(self.envelope("CREATE_PERSON_GROUP", group_id, group_payload("Again"))))
        self.assertTrue(holder)
        self.assertEqual((contender[0], contender[1]["changed"], contender[1]["group"]["name"]),
                         (200, True, "Again"))

    def test_a_delete_waiting_for_a_repeated_create_follows_its_acknowledgement(self) -> None:
        group_id = self.create_group("Readers")
        self.create_group("Child", parent_id=group_id)
        person_id = self.create_person()
        self.send(self.member("ADD_PERSON_GROUP_MEMBER", group_id, person_id, 0))
        holder, contender = self.race(
            self.applying(group_handlers.CREATE_HANDLER,
                          self.envelope("CREATE_PERSON_GROUP", group_id, group_payload("Other"))),
            lambda: self.send(self.envelope("DELETE_PERSON_GROUP", group_id, {})))
        group = holder[1]["group"]
        self.assertEqual((holder[1]["changed"], group["name"], group["member_count"], group["child_count"]),
                         (False, "Readers", 1, 1))
        self.assertEqual(contender, (200, {"code": "ACKNOWLEDGED", "group_id": group_id, "changed": True}))
        self.assertIsNone(self.library.get_person_group(group_id))

    def test_a_membership_waiting_for_a_group_delete_finds_no_group(self) -> None:
        group_id = self.create_group("Readers")
        person_id = self.create_person()
        holder, contender = self.race(lambda scope: groups.delete_group(scope, group_id),
                                      lambda: self.send(self.member("ADD_PERSON_GROUP_MEMBER", group_id,
                                                                    person_id, 0)))
        self.assertTrue(holder)
        self.assertEqual(contender, (404, {"code": "ENTITY_NOT_FOUND", "group_id": group_id,
                                           "person_id": person_id}))
        self.assertEqual(self.count("person_group_members"), 0)

    def test_a_group_delete_waiting_for_a_membership_removes_it(self) -> None:
        group_id = self.create_group("Readers")
        person_id = self.create_person()
        holder, contender = self.race(lambda scope: groups.set_member(scope, group_id, person_id, True),
                                      lambda: self.send(self.envelope("DELETE_PERSON_GROUP", group_id, {})))
        self.assertTrue(holder)
        self.assertEqual(contender, (200, {"code": "ACKNOWLEDGED", "group_id": group_id, "changed": True}))
        self.assertEqual(self.library.get_person_groups_state(person_id)["groups"],
                         [{"group_id": group_id, "revision": 2, "present": False}])


class RecreationTests(PeopleSyncTestCase):
    def test_a_recreated_person_refuses_every_base_from_before_its_delete(self) -> None:
        person_id = self.create_person()
        group_id = self.create_group("Readers")
        work_id = self.library.add_work("Essay")
        self.assertEqual(self.send(self.person_field(person_id, "about", "Bio", 0))[1]["server_revision"], 1)
        self.send(self.member("ADD_PERSON_GROUP_MEMBER", group_id, person_id, 0))
        self.send(self.role("ADD_WORK_PERSON_ROLE", work_id, person_id, 0, credit=""))
        self.send(self.role("REMOVE_WORK_PERSON_ROLE", work_id, person_id, 1))
        self.assertEqual(self.send(self.envelope("DELETE_PERSON", person_id, {}))[1]["changed"], True)
        self.assertEqual(self.send(self.envelope("CREATE_PERSON", person_id, person_payload()))[1]["changed"],
                         True)
        stale = {
            "about": self.send(self.person_field(person_id, "about", "Old", 1)),
            "never edited": self.send(self.person_field(person_id, "birth_date", "1900", 0)),
            "membership": self.send(self.member("ADD_PERSON_GROUP_MEMBER", group_id, person_id, 1)),
            "role": self.send(self.role("ADD_WORK_PERSON_ROLE", work_id, person_id, 2, credit="")),
        }
        for name, (status, result) in stale.items():
            with self.subTest(base=name):
                self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"), result)
        self.assertEqual(self.library.get_person(person_id)["about"], "")
        self.assertEqual(self.library.get_work_roles(work_id), [])

    def test_a_recreated_group_refuses_every_base_from_before_its_delete(self) -> None:
        group_id = self.create_group("Readers")
        person_id = self.create_person()
        self.send(self.member("ADD_PERSON_GROUP_MEMBER", group_id, person_id, 0))
        self.send(self.envelope("DELETE_PERSON_GROUP", group_id, {}))
        self.create_group("Readers", group_id)
        for data in (self.group_field(group_id, "description", "Old", 0),
                     self.member("ADD_PERSON_GROUP_MEMBER", group_id, person_id, 1)):
            with self.subTest(operation=data["operation"]):
                self.assertEqual(self.send(data)[0], 409)
        self.assertEqual(self.library.get_person_group(group_id)["members"], [])


class IsolationTests(PeopleSyncTestCase):
    def test_another_library_reaches_nothing_of_this_one(self) -> None:
        person_id = self.create_person()
        group_id = self.create_group("Readers")
        work_id = self.library.add_work("Essay")
        other = self.other_actor()
        device = str(other.context.device_id)
        for data in self.every_operation(person_id, group_id, work_id):
            with self.subTest(operation=data["operation"]):
                status, result = self.send(dict(data, device_id=device), other.context)
                self.assertEqual(status, 404, result)
        for data in (self.envelope("DELETE_PERSON", person_id, {}),
                     self.envelope("DELETE_PERSON_GROUP", group_id, {})):
            with self.subTest(operation=data["operation"]):
                self.assertEqual(self.send(dict(data, device_id=device), other.context)[1]["changed"], False)
        for data in (self.envelope("CREATE_PERSON", person_id, person_payload()),
                     self.envelope("CREATE_PERSON_GROUP", group_id, group_payload("Mine"))):
            with self.subTest(operation=data["operation"]):
                # Ids are unique across Libraries: refused, unledgered.
                with self.assertRaises(AccessRefused) as caught:
                    self.send(dict(data, device_id=device), other.context)
                self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual(self.library.get_person(person_id)["about"], "")
        self.assertEqual(self.library.get_person_group(group_id)["members"], [])
        self.assertEqual(self.revisions("person-field"), {})
        self.assertEqual({row["library_id"] for row in self.ledger()} - {self.actor.library_id},
                         {other.library_id})
        with facade_context.acting_as(other.context):
            self.assertEqual(self.library.get_all_persons(), [])

    def test_a_reader_cannot_send_people_operations(self) -> None:
        person_id = self.create_person()
        reader = self.reader_context()
        with self.assertRaises(AccessRefused) as caught:
            self.send(self.person_field(person_id, "about", "x", 0), reader)
        self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual(len(self.ledger()), 1)

    def test_an_old_epoch_is_refused_and_the_op_id_survives(self) -> None:
        person_id = self.create_person()
        self.sql("UPDATE prks.libraries SET epoch = epoch + 1 WHERE id = %s", (self.actor.library_id,))
        data = self.person_field(person_id, "about", "x", 0)
        with self.assertRaises(AccessRefused) as caught:
            self.send(data)
        self.assertIs(caught.exception.refusal, Refusal.LIBRARY_EPOCH_CHANGED)
        self.assertEqual(len(self.ledger()), 1)
        self.assertEqual(self.revisions("person-field"), {})


class CreateWorkRoleFilingTests(PeopleSyncTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.filing = ComposedFiling()
        self.env = Environment(pdfs_dir=self.storage.pdfs_dir, filing=self.filing)

    def test_a_new_work_is_born_with_its_roles(self) -> None:
        ann = self.create_person(first_name="Ann", last_name="Lee")
        bo = self.create_person(first_name="Bo", last_name="Ng")
        work_id = "W-" + uuid.uuid4().hex.upper()
        roles_payload = [{"person_id": bo, "role_type": "Editor", "credit_name": ""},
                         {"person_id": ann, "role_type": "Author", "credit_name": "A. Lee"},
                         {"person_id": bo, "role_type": "Editor", "credit_name": "dup"}]
        status, result = self.send(self.envelope("CREATE_WORK", work_id, create_payload(roles=roles_payload)))
        self.assertEqual(status, 200)
        self.assertEqual((result["changed"], result["role_count"], result["aliases_revisions"]),
                         (True, 2, {ann: 1}))
        self.assertEqual([(row["id"], row["role_type"], row["order_index"])
                          for row in self.library.get_work_roles(work_id)],
                         [(bo, "Editor", 0), (ann, "Author", 1)])
        # Construction: the roles read at revision 0.
        self.assertEqual([scope["revision"] for scope in self.library.get_work_roles_state(work_id)["scopes"]],
                         [0, 0])
        again = self.send(self.envelope("CREATE_WORK", work_id, create_payload(roles=roles_payload)))
        self.assertEqual((again[1]["changed"], again[1]["role_count"], "aliases_revisions" in again[1]),
                         (False, 2, False))
        self.assertEqual(self.count("roles"), 2)

    def test_a_comma_bearing_credit_stays_on_the_role_and_advances_no_alias(self) -> None:
        smith = self.create_person(first_name="Ed", last_name="Smith")
        work_id = "W-" + uuid.uuid4().hex.upper()
        status, result = self.send(self.envelope("CREATE_WORK", work_id, create_payload(
            roles=[{"person_id": smith, "role_type": "Author", "credit_name": "Smith, John"}])))
        self.assertEqual((status, result["role_count"], "aliases_revisions" in result), (200, 1, False))
        self.assertEqual(self.library.get_person(smith)["aliases"] or "", "")
        self.assertEqual([row["credit_name"] for row in self.library.get_work_roles(work_id)], ["Smith, John"])

    def test_a_missing_person_refuses_the_create_after_the_folder(self) -> None:
        ann = self.create_person()
        missing = "P-" + "0" * 32
        work_id = "W-" + uuid.uuid4().hex.upper()
        roles_payload = [{"person_id": ann, "role_type": "Author"},
                         {"person_id": missing, "role_type": "Editor"}]
        refused = self.send(self.envelope("CREATE_WORK", work_id, create_payload(roles=roles_payload)))
        self.assertEqual(refused, (404, {"code": "PERSON_NOT_FOUND", "work_id": work_id, "person_id": missing}))
        folder_first = self.send(self.envelope("CREATE_WORK", work_id, create_payload(
            roles=roles_payload, folder_id="F-MISSING")))
        self.assertEqual(folder_first[1]["code"], "FOLDER_NOT_FOUND")
        self.assertEqual((self.count("works"), self.count("roles")), (0, 0))

    def test_a_person_deleted_while_the_work_is_created_refuses_it(self) -> None:
        ann = self.create_person()
        work_id = "W-" + uuid.uuid4().hex.upper()
        holder, contender = self.race(
            lambda scope: persons.delete_person(scope, ann),
            lambda: self.send(self.envelope("CREATE_WORK", work_id, create_payload(
                roles=[{"person_id": ann, "role_type": "Author"}]))))
        self.assertEqual(holder, (True, 0))
        self.assertEqual(contender, (404, {"code": "PERSON_NOT_FOUND", "work_id": work_id, "person_id": ann}))
        self.assertEqual((self.count("works"), self.count("roles")), (0, 0))


class WorkRoleSyncTests(PeopleSyncTestCase):
    """Work-Person roles as element scopes (the retired ``tests/test_work_role_sync.py``)."""

    def setUp(self) -> None:
        super().setUp()
        self.work = self.library.add_work("Paper")
        self.jane = self.library.add_person("Jane", "Doe")
        self.ed = self.library.add_person("Ed", "Smith")

    def link(self, present: bool, base: Optional[int] = 0, person: Optional[str] = None, role: Any = "Author",
             credit: Optional[str] = "", work_id: Optional[str] = None) -> Result:
        operation = "ADD_WORK_PERSON_ROLE" if present else "REMOVE_WORK_PERSON_ROLE"
        return self.send(self.role(operation, work_id or self.work, person or self.jane, base or 0, role,
                                   credit if present else None) | {"base_revision": base})

    def credit(self, credit: str, base: int, person: Optional[str] = None) -> Result:
        return self.send(self.role("SET_WORK_PERSON_ROLE_CREDIT", self.work, person or self.jane, base,
                                   credit=credit))

    def scopes(self, work_id: Optional[str] = None) -> dict[tuple[str, str], dict[str, Any]]:
        state = self.library.get_work_roles_state(work_id or self.work)
        return {(s["person_id"], s["role_type"]): s for s in state["scopes"]}

    def revision(self, person: Optional[str] = None, role: str = "Author") -> int:
        scope = self.scopes().get((person or self.jane, role))
        return scope["revision"] if scope else 0

    def linked(self) -> set[tuple[str, str]]:
        return {(row["id"], row["role_type"]) for row in self.library.get_work_roles(self.work)}

    def row(self) -> dict[str, Any]:
        return next(work for work in self.library.get_all_works() if work["id"] == self.work)

    def aliases_revision(self, person: Optional[str] = None) -> int:
        return self.library.get_person_metadata_state(person or self.jane)["fields"]["aliases"]["revision"]

    def test_the_revision_outlives_the_relationship(self) -> None:
        self.link(True)
        self.assertEqual(self.link(False, 1)[1]["server_revision"], 2)
        self.assertEqual((self.linked(), self.scopes()[(self.jane, "Author")]),
                         (set(), {"person_id": self.jane, "role_type": "Author", "revision": 2, "present": False}))
        self.link(True, 2)
        status, result = self.link(False, 1)
        self.assertEqual((status, result["code"], result["current"]["present"], result["requested"]["present"],
                          result["current_revision"]), (409, "REVISION_CONFLICT", True, False, 3))
        self.assertEqual(self.linked(), {(self.jane, "Author")})

    def test_each_person_and_role_is_its_own_scope_appended_in_arrival_order(self) -> None:
        self.link(True, person=self.jane, role="Author")
        self.link(True, person=self.ed, role="Author")
        self.link(True, person=self.jane, role="Translator")
        self.assertEqual((self.revision(self.jane), self.revision(self.ed), self.revision(self.jane, "Translator"),
                          self.revision(self.jane, "Editor")), (1, 1, 1, 0))
        self.assertEqual((self.row()["linked_authors"], self.row()["primary_author"]), ("Jane Doe, Ed Smith", "Jane Doe"))
        indexes = [row["order_index"] for row in self.library.get_work_roles(self.work)]
        self.assertEqual(indexes, sorted(indexes))
        self.link(False, 1, person=self.jane, role="Author")
        self.assertEqual(self.linked(), {(self.ed, "Author"), (self.jane, "Translator")})
        other = self.library.add_work("Other")
        self.link(True, person=self.ed, work_id=other)
        self.assertEqual([s["person_id"] for s in self.library.get_work_roles_state(other)["scopes"]], [self.ed])
        self.assertIsNone(self.library.get_work_roles_state(generate("W")))

    def test_the_role_vocabulary_and_the_envelope_are_closed_on_every_path(self) -> None:
        for bad in ("author", "Auther", "", "Producer", None, 7):
            with self.subTest(role=repr(bad)):
                self.assertEqual(self.link(True, role=bad), (400, {"code": "INVALID_ENVELOPE"}))
        for payload in ({"person_id": "P-1"}, {"role_type": "Author"},
                        {"person_id": "P-1", "role_type": "Author", "order_index": 3},
                        {"person_id": "P-1", "role_type": "Author"},
                        {"person_id": "", "role_type": "Author", "credit_name": ""}):
            with self.subTest(payload=payload):
                data = self.role("ADD_WORK_PERSON_ROLE", self.work, self.jane, 0, credit="") | {"payload": payload}
                self.assertEqual(self.send(data), (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual(self.link(True, None), (400, {"code": "INVALID_BASE_REVISION"}))
        too_long = "x" * (work_role_sync.MAX_CREDIT_NAME_BYTES + 1)
        self.assertEqual(self.link(True, credit=too_long), (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual((self.linked(), self.ledger()), (set(), []))
        for call in (lambda: self.library.add_role(self.jane, self.work, "Producer"),
                     lambda: self.library.insert_initial_role(self.work, self.jane, "Producer"),
                     lambda: self.library.add_role(self.jane, self.work, "Author", credit_name=too_long),
                     lambda: self.library.insert_initial_role(self.work, self.jane, "Author", credit_name=too_long)):
            with self.assertRaises(ValueError):
                call()
        self.assertEqual((self.linked(), self.revision()), (set(), 0))
        self.library.add_role(self.jane, self.work, "Author")
        with self.assertRaises(ValueError):
            self.library.update_role_credit_name(self.work, self.jane, "Author", 0, too_long)
        self.library.add_role(self.ed, self.work, "Editor", credit_name="x" * work_role_sync.MAX_CREDIT_NAME_BYTES)
        self.assertEqual((self.revision(), len(self.library.get_work_roles(self.work))), (1, 2))
        ann = self.library.add_person("Ann", "Lee")
        for good in work_role_sync.ROLE_TYPES:
            with self.subTest(role=good):
                self.assertEqual(self.link(True, person=ann, role=good)[0], 200)

    def test_the_ordinary_endpoints_share_the_revision_whatever_order_index_they_hold(self) -> None:
        self.library.add_role(self.jane, self.work, "Author")
        self.library.add_role(self.ed, self.work, "Author", order_index=0)
        self.assertEqual(self.row()["linked_authors"], "Jane Doe, Ed Smith")  # appended, never placed
        self.assertTrue(self.library.update_role_credit_name(self.work, self.ed, "Author", 0, "E. Smith"))
        self.assertEqual((self.revision(self.ed), self.revision()), (2, 1))
        self.assertTrue(self.library.update_role_credit_name(self.work, self.jane, "Author", 7, "Jane D."))
        self.assertIn("Jane D.", self.library.get_person(self.jane)["aliases"])
        self.assertTrue(self.library.update_role_credit_name(self.work, self.jane, "Author", 0, ""))
        self.assertTrue(self.library.delete_work_role(self.work, self.jane, "Author", 5))
        self.assertEqual((self.revision(), self.linked()), (4, {(self.ed, "Author")}))
        self.assertEqual(self.link(True, 4)[1]["server_revision"], 5)
        self.assertFalse(self.library.update_role_credit_name(self.work, self.jane, "Editor", 0, "X"))
        self.assertEqual(self.revision(self.jane, "Editor"), 0)
        self.assertNotIn((self.jane, "Editor"), self.linked())

    def test_a_credit_override_is_the_links_state_and_its_alias(self) -> None:
        status, result = self.link(True, credit="Mark Twain")
        self.assertEqual((status, result["credit_name"], result["aliases_revision"], result["server_revision"]),
                         (200, "Mark Twain", 1, 1))
        self.assertEqual((self.library.get_person(self.jane)["aliases"], self.row()["linked_authors"]),
                         ("Mark Twain", "Mark Twain"))
        link = self.row()["linked_people"][0]
        self.assertEqual((link["person_id"], link["role_type"], link["credit_name"], link["display_name"],
                          link["canonical_name"]), (self.jane, "Author", "Mark Twain", "Mark Twain", "Jane Doe"))
        status, result = self.link(True, 0, credit="S. Clemens")
        self.assertEqual((status, result["current"], result["requested"]),
                         (409, {"present": True, "credit_name": "Mark Twain"},
                          {"present": True, "credit_name": "S. Clemens"}))
        status, result = self.link(True, 0, credit="Mark Twain")
        self.assertEqual((status, result["changed"], "aliases_revision" in result), (200, False, False))
        # The credit-derived alias makes a stale offline alias edit conflict.
        status, result = self.send(self.person_field(self.jane, "aliases", "Samuel Clemens", 0))
        self.assertEqual((status, result["code"], result["current_revision"], result["current_value"]),
                         (409, "REVISION_CONFLICT", 1, "Mark Twain"))
        self.assertEqual(self.credit("M. Twain", 1)[1]["changed"], True)
        self.credit("", 2)
        self.assertEqual((self.row()["linked_authors"], self.revision(), self.aliases_revision()),
                         ("Jane Doe", 3, 2))
        self.assertEqual(self.credit("X", 0, self.ed)[1]["code"], "ROLE_NOT_PRESENT")

    def test_which_credits_become_aliases(self) -> None:
        self.library.update_person_profile(self.jane, {"aliases": "Samuel Clemens, Mark Twain"})
        self.assertEqual(self.aliases_revision(), 1)
        self.assertNotIn("aliases_revision", self.link(True, credit="Mark Twain")[1])  # already known
        self.assertEqual(self.aliases_revision(), 1)
        self.assertEqual(self.credit("S. L. Clemens", 1)[1]["aliases_revision"], 2)
        self.assertEqual(work_role_sync.parse_person_aliases(self.library.get_person(self.jane)["aliases"]),
                         ["Samuel Clemens", "Mark Twain", "S. L. Clemens"])
        # A comma-bearing credit stays on the role on every path.
        status, result = self.link(True, person=self.ed, credit="Smith, John")
        self.assertEqual((status, "aliases_revision" in result), (200, False))
        self.library.add_role(self.ed, self.work, "Editor")
        self.assertTrue(self.library.update_role_credit_name(self.work, self.ed, "Editor", 0, "Smith, John"))
        born = self.library.add_work("Born inverted")
        self.library.insert_initial_role(born, self.ed, "Author", 0, credit_name="Smith, John")
        self.assertEqual((self.library.get_person(self.ed)["aliases"] or "", self.aliases_revision(self.ed)), ("", 0))
        self.assertIn("Smith, John", self.row()["linked_authors"])
        # Construction is no role revision, but a promoted credit is a Person change.
        other = self.library.add_person("Ann", "Lee")
        born = self.library.add_work("Born with credit")
        self.library.insert_initial_role(born, other, "Author", 0, credit_name="A. Lee")
        self.assertEqual((self.scopes(born)[(other, "Author")]["revision"], self.aliases_revision(other)), (0, 1))

    def test_construction_keeps_the_callers_order_and_mutation_moves_one_scope(self) -> None:
        self.library.insert_initial_role(self.work, self.ed, "Author", 1)
        self.library.insert_initial_role(self.work, self.jane, "Author", 0)
        row = self.row()
        self.assertEqual((row["linked_authors"], row["primary_author"]), ("Jane Doe, Ed Smith", "Jane Doe"))
        names = [link["display_name"] for link in row["linked_people"] if link["role_type"] == "Author"]
        self.assertEqual((", ".join(names), [link["order_index"] for link in row["linked_people"]]),
                         (row["linked_authors"], [0, 1]))
        self.assertEqual({key: scope["revision"] for key, scope in self.scopes().items()},
                         {(self.jane, "Author"): 0, (self.ed, "Author"): 0})
        self.link(False, 0)
        self.assertEqual((self.revision(), self.revision(self.ed)), (1, 0))
