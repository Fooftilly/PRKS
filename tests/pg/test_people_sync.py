"""The People sync handlers on PostgreSQL (L-P-1 #632, PR 4).

:class:`AnswerTests` spells out what each People operation answers. The
rest cover races decided by
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

from backend.domain.people import person_metadata_rules as person_rules
from backend.sync import envelope
from backend.domain.works import metadata_rules as results
from backend.domain.people import role_rules
from backend.db.database import Isolation
from backend.db.errors import ConstraintViolationError
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.people import groups, persons, roles
from backend.domain.entity_ids import generate, is_distributed
from tests import acting_context as facade_context
from backend.sync import protocol
from backend.sync.families import Environment, FamilyHandler, Result
from backend.sync.handlers import person as person_handlers
from backend.sync.handlers import person_group as group_handlers
from backend.sync.handlers.work_role import RoleFiling
from tests.pg.test_people_facade import PeopleFacadeTestCase
from tests.pg.test_works_sync import StandInFiling, SyncTestCase, create_payload
from tests.narrowing import require
from backend.domain.identity.library_scope import library_query
from backend.domain.people import group_commands
from backend.domain.people import person_commands
from backend.domain.people import role_commands
from tests import people_fixtures
from backend.domain.works import work_commands
from tests import works_fixtures

_ENTITY_TYPES = {"CREATE_PERSON": "person", "DELETE_PERSON": "person",
                 "SET_PERSON_METADATA_FIELD": "person"}
_GROUP_OPERATIONS = ("CREATE_PERSON_GROUP", "SET_PERSON_GROUP_FIELD", "ADD_PERSON_GROUP_MEMBER",
                     "REMOVE_PERSON_GROUP_MEMBER", "DELETE_PERSON_GROUP")
_NAME_KEY = "person_groups_library_id_name_key_key"


_MISSING_GROUP = "PG-" + "0" * 32


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


class PeopleSyncTestCase(SyncTestCase, PeopleFacadeTestCase):  # pyright: ignore[reportIncompatibleMethodOverride]  # MRO picks the first base
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
        op = envelope.normalize_envelope(data)
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
        person_commands.update_profile(self.db, self.ctx, person_id, {"last_name": "Leigh"})
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
        work_id = works_fixtures.add_work(self.db, self.ctx, self.storage, "Essay")
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
        self.assertEqual(require(group_commands.person_state(self.db, self.ctx, person_id))["groups"],
                         [{"group_id": group_id, "revision": 1, "present": True}])
        self.assertEqual(require(role_commands.work_state(self.db, self.ctx, work_id))["scopes"],
                         [{"person_id": person_id, "role_type": "Author", "revision": 1, "present": True}])
        self.assertEqual(require(person_commands.metadata_state(self.db, self.ctx, person_id))["fields"]["aliases"]["revision"], 1)

    def test_a_membership_acknowledgement_states_the_group_name(self) -> None:
        """The chip a client patches comes from the ACK, not from a cache it may lack."""
        person_id = self.create_person()
        group_id = self.create_group("Readers")
        self.send(self.group_field(group_id, "name", "Circle", 0))
        status, result = self.send(self.member("ADD_PERSON_GROUP_MEMBER", group_id, person_id, 0))
        self.assertEqual((status, result["code"], result["present"], result["group_name"],
                          result["group_name_revision"]), (200, "ACKNOWLEDGED", True, "Circle", 1))
        status, result = self.send(self.member("REMOVE_PERSON_GROUP_MEMBER", group_id, person_id, 1))
        self.assertEqual((status, result["present"], result["group_name"], result["group_name_revision"]),
                         (200, False, "Circle", 1))

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
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, person_id))["about"], "x" * 20000)
        self.send(self.person_field(person_id, "about", "A" * 40000, 1))
        status, result = self.send(self.person_field(person_id, "about", "B" * 40000, 0))
        self.assertEqual((status, "current_preview" in result, "current_value" in result), (409, True, False))
        self.assertLessEqual(results.serialized_result_bytes(result), results.MAX_DURABLE_RESULT_BYTES)
        self.send(self.group_field(group_id, "description", "A" * 2000, 0))
        status, result = self.send(self.group_field(group_id, "description", "B" * 2000, 0))
        self.assertEqual(status, 409)
        self.assertLessEqual(results.serialized_result_bytes(result), results.MAX_DURABLE_RESULT_BYTES)

    def test_a_role_may_depend_on_the_creation_of_its_person(self) -> None:
        work_id, person_id = works_fixtures.add_work(self.db, self.ctx, self.storage, "Essay"), "P-" + uuid.uuid4().hex.upper()
        create = self.envelope("CREATE_PERSON", person_id, person_payload())
        role = self.role("ADD_WORK_PERSON_ROLE", work_id, person_id, 0, credit="") | {"depends_on": [create["op_id"]]}
        self.assertEqual(self.send(role), (400, {"code": "UNSATISFIED_DEPENDENCY"}))
        self.assertEqual((person_commands.detail(self.db, self.ctx, person_id), library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, work_id))), (None, []))
        self.assertEqual(self.send(create)[1]["code"], "ACKNOWLEDGED")
        status, result = self.send(role)
        self.assertEqual((status, result["code"], result["present"]), (200, "ACKNOWLEDGED", True))
        self.assertEqual([(row["id"], row["role_type"]) for row in library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, work_id))],
                         [(person_id, "Author")])
        self.assertTrue(is_distributed(people_fixtures.add_person(self.db, self.ctx, "Online", "Person"), "P"))

    def test_a_membership_row_without_a_revision_reads_as_revision_0(self) -> None:
        person_id, group_id = self.create_person(), self.create_group("Analysts")
        self.sql("INSERT INTO prks.person_group_members (library_id, person_id, group_id) VALUES (%s, %s, %s)",
                 (self.actor.library_id, person_id, group_id))
        self.assertEqual(require(group_commands.sync_state(self.db, self.ctx, group_id))["members"],
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
        self.work_id = works_fixtures.add_work(self.db, self.ctx, self.storage, "Essay")

    def test_a_delete_waiting_for_a_link_refuses_with_links(self) -> None:
        holder, contender = self.race(
            lambda scope: roles.set_role(scope, roles.Role(self.work_id, self.person_id, "Author"), True),
            lambda: self.send(self.envelope("DELETE_PERSON", self.person_id, {})))
        self.assertTrue(holder)
        self.assertEqual(contender, (409, {"code": "PERSON_HAS_LINKS", "person_id": self.person_id,
                                           "current_revision": 1}))
        self.assertIsNotNone(person_commands.detail(self.db, self.ctx, self.person_id))
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
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, self.person_id))["aliases"], "A. Lee")

    def test_a_repeated_create_waiting_for_a_delete_creates_the_person_again(self) -> None:
        holder, contender = self.race(
            lambda scope: persons.delete_person(scope, self.person_id),
            lambda: self.send(self.envelope("CREATE_PERSON", self.person_id, person_payload(last_name="Again"))))
        self.assertEqual(holder, (True, 0))
        self.assertEqual((contender[0], contender[1]["changed"], contender[1]["person"]["last_name"]),
                         (200, True, "Again"))
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, self.person_id))["last_name"], "Again")

    def test_a_delete_waiting_for_a_repeated_create_follows_its_acknowledgement(self) -> None:
        holder, contender = self.race(
            self.applying(person_handlers.CREATE_HANDLER,
                          self.envelope("CREATE_PERSON", self.person_id, person_payload(last_name="Other"))),
            lambda: self.send(self.envelope("DELETE_PERSON", self.person_id, {})))
        self.assertEqual((holder[0], holder[1]["changed"], holder[1]["person"]["last_name"]), (200, False, "Lee"))
        self.assertEqual(contender, (200, {"code": "ACKNOWLEDGED", "person_id": self.person_id,
                                           "changed": True}))
        self.assertIsNone(person_commands.detail(self.db, self.ctx, self.person_id))

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
        self.assertEqual(require(group_commands.detail(self.db, self.ctx, second))["name"], "Two")

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
                group_commands.delete(self.db, self.ctx, group_id)

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
        self.assertIsNone(group_commands.detail(self.db, self.ctx, group_id))

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
        self.assertEqual(require(group_commands.person_state(self.db, self.ctx, person_id))["groups"],
                         [{"group_id": group_id, "revision": 2, "present": False}])


class RecreationTests(PeopleSyncTestCase):
    def test_a_recreated_person_refuses_every_base_from_before_its_delete(self) -> None:
        person_id = self.create_person()
        group_id = self.create_group("Readers")
        work_id = works_fixtures.add_work(self.db, self.ctx, self.storage, "Essay")
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
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, person_id))["about"], "")
        self.assertEqual(library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, work_id)), [])

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
        self.assertEqual(require(group_commands.detail(self.db, self.ctx, group_id))["members"], [])


class IsolationTests(PeopleSyncTestCase):
    def test_another_library_reaches_nothing_of_this_one(self) -> None:
        person_id = self.create_person()
        group_id = self.create_group("Readers")
        work_id = works_fixtures.add_work(self.db, self.ctx, self.storage, "Essay")
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
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, person_id))["about"], "")
        self.assertEqual(require(group_commands.detail(self.db, self.ctx, group_id))["members"], [])
        self.assertEqual(self.revisions("person-field"), {})
        self.assertEqual({row["library_id"] for row in self.ledger()} - {self.actor.library_id},
                         {other.library_id})
        with facade_context.acting_as(other.context):
            self.assertEqual(person_commands.catalog(self.db, self.ctx), [])

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
        self.env = Environment(filing=self.filing)

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
                          for row in library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, work_id))],
                         [(bo, "Editor", 0), (ann, "Author", 1)])
        # Construction: the roles read at revision 0.
        self.assertEqual([scope["revision"] for scope in require(role_commands.work_state(self.db, self.ctx, work_id))["scopes"]],
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
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, smith))["aliases"] or "", "")
        self.assertEqual([row["credit_name"] for row in library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, work_id))], ["Smith, John"])

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
        self.work = works_fixtures.add_work(self.db, self.ctx, self.storage, "Paper")
        self.jane = people_fixtures.add_person(self.db, self.ctx, "Jane", "Doe")
        self.ed = people_fixtures.add_person(self.db, self.ctx, "Ed", "Smith")

    def link(self, present: bool, base: Optional[int] = 0, person: Optional[str] = None, role: Any = "Author",
             credit: Optional[str] = "", work_id: Optional[str] = None) -> Result:
        operation = "ADD_WORK_PERSON_ROLE" if present else "REMOVE_WORK_PERSON_ROLE"
        return self.send(self.role(operation, work_id or self.work, person or self.jane, base or 0, role,
                                   credit if present else None) | {"base_revision": base})

    def credit(self, credit: str, base: int, person: Optional[str] = None) -> Result:
        return self.send(self.role("SET_WORK_PERSON_ROLE_CREDIT", self.work, person or self.jane, base,
                                   credit=credit))

    def scopes(self, work_id: Optional[str] = None) -> dict[tuple[str, str], dict[str, Any]]:
        state = role_commands.work_state(self.db, self.ctx, work_id or self.work)
        assert state is not None
        return {(s["person_id"], s["role_type"]): s for s in state["scopes"]}

    def revision(self, person: Optional[str] = None, role: str = "Author") -> int:
        scope = self.scopes().get((person or self.jane, role))
        return scope["revision"] if scope else 0

    def linked(self) -> set[tuple[str, str]]:
        return {(row["id"], row["role_type"]) for row in library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, self.work))}

    def row(self) -> dict[str, Any]:
        return next(work for work in work_commands.catalog(self.db, self.ctx) if work["id"] == self.work)

    def aliases_revision(self, person: Optional[str] = None) -> int:
        return require(person_commands.metadata_state(self.db, self.ctx, person or self.jane))["fields"]["aliases"]["revision"]

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
        indexes = [row["order_index"] for row in library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, self.work))]
        self.assertEqual(indexes, sorted(indexes))
        self.link(False, 1, person=self.jane, role="Author")
        self.assertEqual(self.linked(), {(self.ed, "Author"), (self.jane, "Translator")})
        other = works_fixtures.add_work(self.db, self.ctx, self.storage, "Other")
        self.link(True, person=self.ed, work_id=other)
        self.assertEqual([s["person_id"] for s in require(role_commands.work_state(self.db, self.ctx, other))["scopes"]], [self.ed])
        self.assertIsNone(role_commands.work_state(self.db, self.ctx, generate("W")))

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
        too_long = "x" * (role_rules.MAX_CREDIT_NAME_BYTES + 1)
        self.assertEqual(self.link(True, credit=too_long), (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual((self.linked(), self.ledger()), (set(), []))
        for call in (lambda: role_commands.add(self.db, self.ctx, self.jane, self.work, "Producer"),
                     lambda: people_fixtures.insert_initial_role(self.db, self.ctx, self.work, self.jane, "Producer"),
                     lambda: role_commands.add(self.db, self.ctx, self.jane, self.work, "Author", too_long),
                     lambda: people_fixtures.insert_initial_role(self.db, self.ctx, self.work, self.jane, "Author", credit_name=too_long)):
            with self.assertRaises(ValueError):
                call()
        self.assertEqual((self.linked(), self.revision()), (set(), 0))
        role_commands.add(self.db, self.ctx, self.jane, self.work, "Author")
        with self.assertRaises(ValueError):
            role_commands.update_credit(self.db, self.ctx, self.work, self.jane, "Author", too_long)
        role_commands.add(self.db, self.ctx, self.ed, self.work, "Editor", "x" * role_rules.MAX_CREDIT_NAME_BYTES)
        self.assertEqual((self.revision(), len(library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, self.work)))), (1, 2))
        ann = people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee")
        for good in role_rules.ROLE_TYPES:
            with self.subTest(role=good):
                self.assertEqual(self.link(True, person=ann, role=good)[0], 200)

    def test_the_ordinary_endpoints_share_the_revision_whatever_order_index_they_hold(self) -> None:
        role_commands.add(self.db, self.ctx, self.jane, self.work, "Author")
        role_commands.add(self.db, self.ctx, self.ed, self.work, "Author")
        self.assertEqual(self.row()["linked_authors"], "Jane Doe, Ed Smith")  # appended, never placed
        self.assertTrue(role_commands.update_credit(self.db, self.ctx, self.work, self.ed, "Author", "E. Smith"))
        self.assertEqual((self.revision(self.ed), self.revision()), (2, 1))
        self.assertTrue(role_commands.update_credit(self.db, self.ctx, self.work, self.jane, "Author", "Jane D."))
        self.assertIn("Jane D.", require(person_commands.detail(self.db, self.ctx, self.jane))["aliases"])
        self.assertTrue(role_commands.update_credit(self.db, self.ctx, self.work, self.jane, "Author", ""))
        self.assertTrue(role_commands.remove(self.db, self.ctx, self.work, self.jane, "Author"))
        self.assertEqual((self.revision(), self.linked()), (4, {(self.ed, "Author")}))
        self.assertEqual(self.link(True, 4)[1]["server_revision"], 5)
        self.assertFalse(role_commands.update_credit(self.db, self.ctx, self.work, self.jane, "Editor", "X"))
        self.assertEqual(self.revision(self.jane, "Editor"), 0)
        self.assertNotIn((self.jane, "Editor"), self.linked())

    def test_a_credit_override_is_the_links_state_and_its_alias(self) -> None:
        status, result = self.link(True, credit="Mark Twain")
        self.assertEqual((status, result["credit_name"], result["aliases_revision"], result["server_revision"]),
                         (200, "Mark Twain", 1, 1))
        self.assertEqual((require(person_commands.detail(self.db, self.ctx, self.jane))["aliases"], self.row()["linked_authors"]),
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
        person_commands.update_profile(self.db, self.ctx, self.jane, {"aliases": "Samuel Clemens, Mark Twain"})
        self.assertEqual(self.aliases_revision(), 1)
        self.assertNotIn("aliases_revision", self.link(True, credit="Mark Twain")[1])  # already known
        self.assertEqual(self.aliases_revision(), 1)
        self.assertEqual(self.credit("S. L. Clemens", 1)[1]["aliases_revision"], 2)
        self.assertEqual(role_rules.parse_person_aliases(require(person_commands.detail(self.db, self.ctx, self.jane))["aliases"]),
                         ["Samuel Clemens", "Mark Twain", "S. L. Clemens"])
        # A comma-bearing credit stays on the role on every path.
        status, result = self.link(True, person=self.ed, credit="Smith, John")
        self.assertEqual((status, "aliases_revision" in result), (200, False))
        role_commands.add(self.db, self.ctx, self.ed, self.work, "Editor")
        self.assertTrue(role_commands.update_credit(self.db, self.ctx, self.work, self.ed, "Editor", "Smith, John"))
        born = works_fixtures.add_work(self.db, self.ctx, self.storage, "Born inverted")
        people_fixtures.insert_initial_role(self.db, self.ctx, born, self.ed, "Author", order_index=0, credit_name="Smith, John")
        self.assertEqual((require(person_commands.detail(self.db, self.ctx, self.ed))["aliases"] or "", self.aliases_revision(self.ed)), ("", 0))
        self.assertIn("Smith, John", self.row()["linked_authors"])
        # Construction is no role revision, but a promoted credit is a Person change.
        other = people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee")
        born = works_fixtures.add_work(self.db, self.ctx, self.storage, "Born with credit")
        people_fixtures.insert_initial_role(self.db, self.ctx, born, other, "Author", order_index=0, credit_name="A. Lee")
        self.assertEqual((self.scopes(born)[(other, "Author")]["revision"], self.aliases_revision(other)), (0, 1))

    def test_construction_keeps_the_callers_order_and_mutation_moves_one_scope(self) -> None:
        people_fixtures.insert_initial_role(self.db, self.ctx, self.work, self.ed, "Author", order_index=1)
        people_fixtures.insert_initial_role(self.db, self.ctx, self.work, self.jane, "Author", order_index=0)
        row = self.row()
        self.assertEqual((row["linked_authors"], row["primary_author"]), ("Jane Doe, Ed Smith", "Jane Doe"))
        names = [link["display_name"] for link in row["linked_people"] if link["role_type"] == "Author"]
        self.assertEqual((", ".join(names), [link["order_index"] for link in row["linked_people"]]),
                         (row["linked_authors"], [0, 1]))
        self.assertEqual({key: scope["revision"] for key, scope in self.scopes().items()},
                         {(self.jane, "Author"): 0, (self.ed, "Author"): 0})
        self.link(False, 0)
        self.assertEqual((self.revision(), self.revision(self.ed)), (1, 0))


class AnswerTests(PeopleSyncTestCase):
    """What each People operation answers, spelled out as decision tables."""

    def test_create_person(self) -> None:
        person_id = generate("P")
        payload = person_payload(first_name="Ann", last_name="Lee", aliases="A. Lee, Annie",
                                 image_url="  https://example.org/ann.jpg  ", about="Bio")
        status, first = self.send(self.envelope("CREATE_PERSON", person_id, payload))
        self.assertEqual((status, first["code"], first["changed"], first["person_id"]),
                         (200, "ACKNOWLEDGED", True, person_id))
        person = first["person"]
        self.assertEqual({key: person[key] for key in ("first_name", "last_name", "aliases", "about", "image_url",
                                                        "assigned_roles", "groups", "works")},
                         {"first_name": "Ann", "last_name": "Lee", "aliases": "A. Lee, Annie", "about": "Bio",
                          "image_url": "https://example.org/ann.jpg", "assigned_roles": [], "groups": [],
                          "works": []})
        status, again = self.send(self.envelope("CREATE_PERSON", person_id, person_payload(last_name="Other")))
        self.assertEqual((status, again["changed"], again["person"]["last_name"]), (200, False, "Lee"))
        for entity_id, bad, base, code in (("P-not-distributed", payload, None, "INVALID_ENVELOPE"),
                                           (person_id, payload, 0, "INVALID_BASE_REVISION"),
                                           (person_id, {"first_name": "Ann"}, None, "INVALID_ENVELOPE"),
                                           (person_id, person_payload(about=7), None, "INVALID_ENVELOPE"),
                                           (person_id, person_payload(image_url="javascript:x"), None,
                                            "INVALID_ENVELOPE")):
            with self.subTest(payload=str(bad)[:40], base=base):
                self.assertEqual(self.send(self.envelope("CREATE_PERSON", entity_id, bad, base)), (400, {"code": code}))

    def test_person_metadata_decision_table(self) -> None:
        person_id = self.create_person(first_name="Ann", last_name="Lee")
        ack = {"code": "ACKNOWLEDGED", "person_id": person_id, "value_omitted": True, "server_revision": 1}
        conflict = {"person_id": person_id, "field": "about", "current_revision": 1, "current_value": "Bio",
                    "requested_value": "Other"}
        for field, value, base, answer in (
                ("about", "Bio", 0, (200, {**ack, "field": "about", "changed": True})),
                ("about", "Bio", 1, (200, {**ack, "field": "about", "changed": False})),
                ("about", "Bio", 0, (200, {**ack, "field": "about", "changed": False})),  # a stale base that agrees
                ("about", "Other", 0, (409, {**conflict, "code": "REVISION_CONFLICT"})),
                ("about", "Other", 9, (400, {**conflict, "code": "FUTURE_REVISION"})),
                ("image_url", " https://example.org/a.png ", 0, (200, {**ack, "field": "image_url", "changed": True})),
                ("aliases", "A. Lee, Annie", 0, (200, {**ack, "field": "aliases", "changed": True})),
                ("last_name", "", 0, (200, {**ack, "field": "last_name", "changed": True}))):
            with self.subTest(field=field, value=value, base=base):
                self.assertEqual(self.send(self.person_field(person_id, field, value, base)), answer)
        for payload, base, code in (({"field": "id", "value": "x"}, 0, "INVALID_ENVELOPE"),
                                    ({"field": "about", "value": 1}, 0, "INVALID_ENVELOPE"),
                                    ({"field": "image_url", "value": "javascript:x"}, 0, "INVALID_ENVELOPE"),
                                    ({"field": "about", "value": "x"}, None, "INVALID_BASE_REVISION")):
            with self.subTest(payload=payload, base=base):
                self.assertEqual(self.send(self.envelope("SET_PERSON_METADATA_FIELD", person_id, payload, base)),
                                 (400, {"code": code}))
        missing = generate("P")
        self.assertEqual(self.send(self.person_field(missing, "about", "x", 0)),
                         (404, {"code": "ENTITY_NOT_FOUND", "person_id": missing, "field": "about"}))
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, person_id))["image_url"], "https://example.org/a.png")

    def test_delete_person(self) -> None:
        person_id, group_id = self.create_person(last_name="Lee"), self.create_group("Readers")
        work_id = works_fixtures.add_work(self.db, self.ctx, self.storage, "Essay")
        self.send(self.member("ADD_PERSON_GROUP_MEMBER", group_id, person_id, 0))
        self.send(self.role("ADD_WORK_PERSON_ROLE", work_id, person_id, 0, credit=""))
        self.assertEqual(self.send(self.envelope("DELETE_PERSON", person_id, {})),
                         (409, {"code": "PERSON_HAS_LINKS", "person_id": person_id, "current_revision": 1}))
        self.send(self.role("REMOVE_WORK_PERSON_ROLE", work_id, person_id, 1))
        self.assertEqual(self.send(self.envelope("DELETE_PERSON", person_id, {})),
                         (200, {"code": "ACKNOWLEDGED", "person_id": person_id, "changed": True}))
        self.assertEqual(self.send(self.envelope("DELETE_PERSON", person_id, {}))[1]["changed"], False)
        self.assertEqual(self.send(self.envelope("DELETE_PERSON", person_id, {"x": 1})), (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual(self.send(self.envelope("DELETE_PERSON", person_id, {}, 0)), (400, {"code": "INVALID_BASE_REVISION"}))
        self.assertEqual(require(group_commands.detail(self.db, self.ctx, group_id))["members"], [])

    def test_create_person_group(self) -> None:
        parent = self.create_group("Parent")
        child = generate("PG")
        status, created = self.send(self.envelope("CREATE_PERSON_GROUP", child,
                                                  group_payload("  Child  ", parent_id=parent, description=" About ")))
        self.assertEqual((status, created["code"], created["changed"], created["group_id"]),
                         (200, "ACKNOWLEDGED", True, child))
        self.assertEqual({key: created["group"][key] for key in ("child_count", "description", "id", "member_count",
                                                                 "name", "parent_id")},
                         {"child_count": 0, "description": "About", "id": child, "member_count": 0, "name": "Child",
                          "parent_id": parent})
        self.assertEqual(self.send(self.envelope("CREATE_PERSON_GROUP", child, group_payload("Else")))[1]["changed"], False)
        taken, orphan = generate("PG"), generate("PG")
        self.assertEqual(self.send(self.envelope("CREATE_PERSON_GROUP", taken, group_payload("PARENT"))), (409, {
            "code": "NAME_TAKEN", "group_id": taken, "message": "A group with this name already exists."}))
        self.assertEqual(self.send(self.envelope("CREATE_PERSON_GROUP", orphan,
                                                 group_payload("Orphan", parent_id=generate("PG")))),
                         (404, {"code": "PARENT_NOT_FOUND", "group_id": orphan, "message": "Parent group not found."}))
        for group_id, payload, base, code in (("PG-not-distributed", group_payload("X"), None, "INVALID_ENVELOPE"),
                                              (generate("PG"), group_payload(""), None, "INVALID_ENVELOPE"),
                                              (generate("PG"), group_payload("Y"), 0, "INVALID_BASE_REVISION"),
                                              (generate("PG"), {"name": "Z"}, None, "INVALID_ENVELOPE")):
            with self.subTest(payload=payload, base=base):
                self.assertEqual(self.send(self.envelope("CREATE_PERSON_GROUP", group_id, payload, base)),
                                 (400, {"code": code}))

    def test_person_group_field_decision_table(self) -> None:
        group_id, other = self.create_group("Readers"), self.create_group("Writers")
        child = self.create_group("Child", parent_id=group_id)
        ack = {"code": "ACKNOWLEDGED", "group_id": group_id, "value_omitted": True}
        name = {"group_id": group_id, "field": "name", "current_revision": 1, "current_value": "Circle",
                "requested_value": "Ring"}
        parent = {"group_id": group_id, "field": "parent_id", "current_revision": 0}
        for field, value, base, answer in (
                ("name", "Circle", 0, (200, {**ack, "field": "name", "changed": True, "server_revision": 1})),
                ("name", " Circle ", 1, (200, {**ack, "field": "name", "changed": False, "server_revision": 1})),
                ("name", "Circle", 0, (200, {**ack, "field": "name", "changed": False, "server_revision": 1})),
                ("name", "Ring", 0, (409, {**name, "code": "REVISION_CONFLICT"})),
                ("name", "Ring", 5, (400, {**name, "code": "FUTURE_REVISION"})),
                ("name", "writers", 1, (409, {"code": "NAME_TAKEN", "group_id": group_id, "field": "name",
                                              "current_revision": 1,
                                              "message": "A group with this name already exists."})),
                ("description", "About", 0, (200, {**ack, "field": "description", "changed": True,
                                                   "server_revision": 1})),
                ("parent_id", child, 0, (409, {**parent, "code": "PARENT_CYCLE",
                                               "message": "Cannot set parent to a subgroup (cycle)."})),
                ("parent_id", group_id, 0, (409, {**parent, "code": "PARENT_CYCLE",
                                                  "message": "A group cannot be its own parent."})),
                ("parent_id", _MISSING_GROUP, 0, (404, {**parent, "code": "PARENT_NOT_FOUND",
                                                        "message": "Parent group not found."})),
                ("parent_id", other, 0, (200, {**ack, "field": "parent_id", "changed": True, "server_revision": 1})),
                ("parent_id", "", 1, (200, {**ack, "field": "parent_id", "changed": True, "server_revision": 2}))):
            with self.subTest(field=field, value=value, base=base):
                self.assertEqual(self.send(self.group_field(group_id, field, value, base)), answer)
        missing = generate("PG")
        self.assertEqual(self.send(self.group_field(missing, "name", "x", 0)),
                         (404, {"code": "ENTITY_NOT_FOUND", "group_id": missing, "field": "name"}))
        for payload, base, code in (({"field": "name", "value": ""}, 0, "INVALID_ENVELOPE"),
                                    ({"field": "x", "value": "y"}, 0, "INVALID_ENVELOPE"),
                                    ({"field": "name", "value": "x"}, None, "INVALID_BASE_REVISION")):
            with self.subTest(payload=payload, base=base):
                self.assertEqual(self.send(self.envelope("SET_PERSON_GROUP_FIELD", group_id, payload, base)),
                                 (400, {"code": code}))

    def test_person_group_members(self) -> None:
        group_id, ann = self.create_group("Readers"), self.create_person(last_name="Lee")
        ack = {"code": "ACKNOWLEDGED", "group_id": group_id, "person_id": ann, "group_name": "Readers",
               "group_name_revision": 0}
        stale = {"group_id": group_id, "person_id": ann, "current_revision": 1, "current_present": True,
                 "requested_present": False}
        for operation, base, answer in (
                ("ADD_PERSON_GROUP_MEMBER", 0, (200, {**ack, "changed": True, "present": True, "server_revision": 1})),
                ("ADD_PERSON_GROUP_MEMBER", 1, (200, {**ack, "changed": False, "present": True, "server_revision": 1})),
                ("ADD_PERSON_GROUP_MEMBER", 0, (200, {**ack, "changed": False, "present": True, "server_revision": 1})),
                ("REMOVE_PERSON_GROUP_MEMBER", 0, (409, {**stale, "code": "REVISION_CONFLICT"})),
                ("REMOVE_PERSON_GROUP_MEMBER", 4, (400, {**stale, "code": "FUTURE_REVISION"})),
                ("REMOVE_PERSON_GROUP_MEMBER", 1, (200, {**ack, "changed": True, "present": False,
                                                         "server_revision": 2})),
                ("REMOVE_PERSON_GROUP_MEMBER", 2, (200, {**ack, "changed": False, "present": False,
                                                         "server_revision": 2}))):
            with self.subTest(operation=operation, base=base):
                self.assertEqual(self.send(self.member(operation, group_id, ann, base)), answer)
        missing_person, missing_group = generate("P"), generate("PG")
        self.assertEqual(self.send(self.member("ADD_PERSON_GROUP_MEMBER", group_id, missing_person, 0)),
                         (404, {"code": "PERSON_NOT_FOUND", "group_id": group_id, "person_id": missing_person}))
        self.assertEqual(self.send(self.member("ADD_PERSON_GROUP_MEMBER", missing_group, ann, 0)),
                         (404, {"code": "ENTITY_NOT_FOUND", "group_id": missing_group, "person_id": ann}))
        for payload, base, code in (({"person_id": " "}, 0, "INVALID_ENVELOPE"), ({}, 0, "INVALID_ENVELOPE"),
                                    ({"person_id": ann}, None, "INVALID_BASE_REVISION")):
            with self.subTest(payload=payload, base=base):
                self.assertEqual(self.send(self.envelope("ADD_PERSON_GROUP_MEMBER", group_id, payload, base)),
                                 (400, {"code": code}))
        self.assertEqual(self.send(self.member("ADD_PERSON_GROUP_MEMBER", group_id, ann, 2))[1]["server_revision"], 3)

    def test_delete_person_group(self) -> None:
        parent = self.create_group("Parent")
        group_id = self.create_group("Middle", parent_id=parent)
        child = self.create_group("Child", parent_id=group_id)
        ann = self.create_person(last_name="Lee")
        self.send(self.member("ADD_PERSON_GROUP_MEMBER", group_id, ann, 0))
        self.assertEqual(self.send(self.envelope("DELETE_PERSON_GROUP", group_id, {})),
                         (200, {"code": "ACKNOWLEDGED", "group_id": group_id, "changed": True}))
        self.assertEqual(self.send(self.envelope("DELETE_PERSON_GROUP", group_id, {}))[1]["changed"], False)
        self.assertEqual(self.send(self.envelope("DELETE_PERSON_GROUP", group_id, {}, 0)),
                         (400, {"code": "INVALID_BASE_REVISION"}))
        self.assertEqual(require(group_commands.detail(self.db, self.ctx, child))["parent_id"], parent)
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, ann))["groups"], [])

    def test_role_decision_table(self) -> None:
        ann = self.create_person(first_name="Ann", last_name="Lee")
        work_id = works_fixtures.add_work(self.db, self.ctx, self.storage, "Essay")
        role = {"person_id": ann, "role_type": "Author", "work_id": work_id}
        ack = {**role, "code": "ACKNOWLEDGED", "first_name": "Ann", "last_name": "Lee"}
        stale = {**role, "current": {"credit_name": "A. Lee", "present": True}, "current_revision": 1,
                 "requested": {"credit_name": "Ann L.", "present": True}}
        for operation, base, credit, answer in (
                ("SET_WORK_PERSON_ROLE_CREDIT", 0, "x", (409, {**role, "code": "ROLE_NOT_PRESENT",
                                                               "current_revision": 0})),
                ("ADD_WORK_PERSON_ROLE", 0, "A. Lee", (200, {**ack, "aliases_revision": 1, "changed": True,
                                                             "credit_name": "A. Lee", "present": True,
                                                             "server_revision": 1})),
                ("ADD_WORK_PERSON_ROLE", 1, "A. Lee", (200, {**ack, "changed": False, "credit_name": "A. Lee",
                                                             "present": True, "server_revision": 1})),
                ("ADD_WORK_PERSON_ROLE", 0, "A. Lee", (200, {**ack, "changed": False, "credit_name": "A. Lee",
                                                             "present": True, "server_revision": 1})),
                ("SET_WORK_PERSON_ROLE_CREDIT", 0, "Ann L.", (409, {**stale, "code": "REVISION_CONFLICT"})),
                ("SET_WORK_PERSON_ROLE_CREDIT", 7, "Ann L.", (400, {**stale, "code": "FUTURE_REVISION"})),
                ("SET_WORK_PERSON_ROLE_CREDIT", 1, "", (200, {**ack, "changed": True, "credit_name": "",
                                                              "present": True, "server_revision": 2})),
                ("REMOVE_WORK_PERSON_ROLE", 2, None, (200, {**ack, "changed": True, "credit_name": "",
                                                            "present": False, "server_revision": 3})),
                ("REMOVE_WORK_PERSON_ROLE", 3, None, (200, {**ack, "changed": False, "credit_name": "",
                                                            "present": False, "server_revision": 3}))):
            with self.subTest(operation=operation, base=base):
                self.assertEqual(self.send(self.role(operation, work_id, ann, base, credit=credit)), answer)
        self.assertEqual(self.send(self.role("ADD_WORK_PERSON_ROLE", work_id, ann, 0, "Editor", ""))[1]["server_revision"], 1)
        missing_person, missing_work = generate("P"), generate("W")
        self.assertEqual(self.send(self.role("ADD_WORK_PERSON_ROLE", work_id, missing_person, 0, credit="")),
                         (404, {"code": "PERSON_NOT_FOUND", "person_id": missing_person, "role_type": "Author",
                                "work_id": work_id}))
        self.assertEqual(self.send(self.role("ADD_WORK_PERSON_ROLE", missing_work, ann, 0, credit="")),
                         (404, {"code": "ENTITY_NOT_FOUND", "person_id": ann, "role_type": "Author",
                                "work_id": missing_work}))
        for payload, base, code in (({"person_id": ann, "role_type": "Boss", "credit_name": ""}, 0, "INVALID_ENVELOPE"),
                                    ({"person_id": ann, "role_type": "Author"}, 0, "INVALID_ENVELOPE"),
                                    ({"person_id": ann, "role_type": "Author", "credit_name": "x" * 501}, 0,
                                     "INVALID_ENVELOPE"),
                                    ({"person_id": ann, "role_type": "Author", "credit_name": ""}, None,
                                     "INVALID_BASE_REVISION")):
            with self.subTest(payload=str(payload)[:50], base=base):
                self.assertEqual(self.send(self.envelope("ADD_WORK_PERSON_ROLE", work_id, payload, base)),
                                 (400, {"code": code}))
