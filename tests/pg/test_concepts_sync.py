"""The Concept sync handlers on PostgreSQL (L-R-1 #634).

``tests/test_concepts_parity.py`` proves each Concept operation answers as on
SQLite. These tests cover what only PostgreSQL has: races decided by the
key index (and the rerun that ledgers the refusal), the hierarchy lock and
row locks, each held open until the other side waits (§7.1); one ``op_id``
replayed or sent concurrently; a stale operation after a Concept was
deleted and created again under its id; Library isolation and a reader;
and NUL, which PostgreSQL cannot store.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from backend import sync_protocol
from backend.concept_sync import scope_key
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.research import concepts
from backend.entity_ids import generate
from backend.sync.families import FamilyHandler
from backend.sync.handlers import concept as concept_handlers
from tests.pg.test_concepts import ConceptTestCase
from tests.pg.test_works_sync import SyncTestCase


class ConceptSyncTestCase(SyncTestCase, ConceptTestCase):
    def envelope(self, operation: str, entity_id: str, payload: dict[str, Any],
                 base: Optional[int] = None, **changes: Any) -> dict[str, Any]:
        changes.setdefault("entity_type", "concept")
        return super().envelope(operation, entity_id, payload, base, **changes)

    def create(self, name: str = "Concept", concept_id: Optional[str] = None,
               description: str = "") -> dict[str, Any]:
        return self.envelope("CREATE_CONCEPT", concept_id or generate("C"),
                             {"name": name, "description": description})

    def create_concept(self, name: str) -> str:
        data = self.create(name)
        status, result = self.send(data)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"), result)
        return data["entity_id"]

    def identity_op(self, concept_id: str, name: str, aliases: list[str], base: int) -> dict[str, Any]:
        return self.envelope("SET_CONCEPT_IDENTITY", concept_id, {"name": name, "aliases": aliases}, base)

    def parents_op(self, concept_id: str, parent_ids: list[str], base: int) -> dict[str, Any]:
        return self.envelope("SET_CONCEPT_PARENTS", concept_id, {"parent_ids": parent_ids}, base)

    def applying(self, handler: FamilyHandler, data: dict[str, Any]) -> Any:
        """A holder that applies ``data`` with ``handler`` in the race's open transaction."""
        op = sync_protocol.normalize_envelope(data)
        return lambda scope: handler.apply(scope, op, datetime.now(timezone.utc), self.env)


class CreateTests(ConceptSyncTestCase):
    def test_creation_acknowledges_converges_and_refuses_a_taken_name(self) -> None:
        data = self.create("  Λόγος  ", description="About")
        concept_id = data["entity_id"]
        first = self.send(data)
        self.assertEqual(first, (200, {"concept_id": concept_id, "code": "ACKNOWLEDGED", "changed": True,
                                       "concept": {"id": concept_id, "name": "Λόγος", "description": "About"}}))
        self.assertEqual(self.send(data), first)  # a replay
        again = self.send(self.create("Other", concept_id))
        self.assertEqual((again[1]["changed"], again[1]["concept"]["name"]), (False, "Λόγος"))
        taken = self.create("ΛΌΓΟΣ")
        self.assertEqual(self.send(taken), (409, {"concept_id": taken["entity_id"], "code": "CONCEPT_EXISTS"}))
        self.assertEqual(self.count("concepts"), 1)
        # Construction advances nothing.
        self.assertEqual(self.revisions("concept-identity"), {})

    def test_another_librarys_id_is_refused_unledgered(self) -> None:
        other = self.other_actor()
        theirs = generate("C")
        self.assertEqual(self.send(self.create("Theirs", theirs) | {"device_id": str(other.context.device_id)},
                                   other.context)[0], 200)
        before = len(self.ledger())
        with self.assertRaises(AccessRefused) as caught:
            self.send(self.create("Mine", theirs))
        self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual((len(self.ledger()), self.library.list_concepts()), (before, []))
        # The id is decided before the name: a name this Library holds does
        # not turn the foreign id into a ledgered CONCEPT_EXISTS.
        self.create_concept("Taken")
        before = len(self.ledger())
        with self.assertRaises(AccessRefused):
            self.send(self.create("TAKEN", theirs))
        self.assertEqual(len(self.ledger()), before)

    def test_nul_and_malformed_payloads_are_invalid_envelopes(self) -> None:
        for data in (self.create("a\x00b"), self.create("x" * 161), self.create("  "),
                     self.envelope("CREATE_CONCEPT", "C-1", {"name": "A", "description": ""})):
            with self.subTest(name=data["payload"]["name"][:8]):
                self.assertEqual(self.send(data), (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual(self.ledger(), [])

    def test_two_creates_of_one_name_make_one_concept(self) -> None:
        first, second = self.create("Drafts"), self.create("DRAFTS")
        holder, contender = self.race(self.applying(concept_handlers.CREATE_HANDLER, first),
                                      lambda: self.send(second))
        self.assertEqual(holder[1]["changed"], True)
        self.assertEqual(contender, (409, {"concept_id": second["entity_id"], "code": "CONCEPT_EXISTS"}))
        self.assertEqual(self.count("concepts"), 1)
        self.assert_integrity()

    def test_two_creates_of_one_id_make_one_concept(self) -> None:
        concept_id = generate("C")
        holder, contender = self.race(self.applying(concept_handlers.CREATE_HANDLER, self.create("A", concept_id)),
                                      lambda: self.send(self.create("B", concept_id)))
        self.assertEqual(holder[1]["changed"], True)
        self.assertEqual((contender[1]["changed"], contender[1]["concept"]["name"]), (False, "A"))
        self.assertEqual(self.count("concepts"), 1)

    def test_one_create_sent_concurrently_applies_once(self) -> None:
        data = self.create("Once")
        results = self.concurrently([lambda: self.send(data) for _ in range(4)])
        self.assertTrue(all(result == results[0] for result in results), results)
        self.assertEqual((results[0][1]["changed"], self.count("concepts"), len(self.ledger())), (True, 1, 1))


class IdentityTests(ConceptSyncTestCase):
    def test_a_rename_lost_to_a_concurrent_writer_of_the_key_is_ledgered_as_refused(self) -> None:
        first, second = self.create_concept("First"), self.create_concept("Second")
        holder, contender = self.race(self.applying(concept_handlers.IDENTITY_HANDLER,
                                                    self.identity_op(first, "First", ["Shared"], 0)),
                                      lambda: self.send(self.identity_op(second, "SHARED", [], 0)))
        self.assertEqual(holder[1]["aliases"], ["Shared"])
        self.assertEqual(contender, (409, {"concept_id": second, "code": "CONCEPT_EXISTS"}))
        self.assertEqual(self.ledger()[-1]["status"], "CONCEPT_EXISTS")
        self.assertEqual(self.state(second)["identity_revision"], 0)
        self.assert_integrity()

    def test_an_alias_lost_to_a_concurrent_create_is_ledgered_as_refused(self) -> None:
        ethics = self.create_concept("Ethics")
        holder, contender = self.race(self.applying(concept_handlers.CREATE_HANDLER, self.create("Morals")),
                                      lambda: self.send(self.identity_op(ethics, "Ethics", ["MORALS"], 0)))
        self.assertEqual(holder[1]["changed"], True)
        self.assertEqual(contender, (409, {"concept_id": ethics, "code": "ALIAS_CONFLICT"}))
        self.assert_integrity()

    def test_a_stale_identity_always_conflicts(self) -> None:
        ethics = self.create_concept("Ethics")
        self.assertEqual(self.send(self.identity_op(ethics, "Morals", [], 0))[1]["aliases"], ["Ethics"])
        stale = self.send(self.identity_op(ethics, "Morals", ["Ethics"], 0))
        self.assertEqual(stale, (409, {"concept_id": ethics, "code": "REVISION_CONFLICT", "current_revision": 1,
                                       "current_value": "Morals", "requested_value": "Morals"}))


class ParentsTests(ConceptSyncTestCase):
    def test_two_parent_changes_that_together_close_a_cycle(self) -> None:
        a, b = self.create_concept("A"), self.create_concept("B")
        holder, contender = self.race(self.applying(concept_handlers.PARENTS_HANDLER, self.parents_op(a, [b], 0)),
                                      lambda: self.send(self.parents_op(b, [a], 0)))
        self.assertEqual(holder[1]["parent_ids"], [b])
        # The contender waited for the hierarchy, then read B's advanced scope.
        self.assertEqual(contender, (409, {"concept_id": b, "code": "REVISION_CONFLICT", "current_revision": 1,
                                           "current_count": 0, "requested_count": 1}))
        self.assertEqual(self.send(self.parents_op(b, [a], 1)),
                         (409, {"concept_id": b, "code": "CONCEPT_CYCLE"}))

    def test_a_stale_base_with_the_same_set_converges(self) -> None:
        a, b, c = self.create_concept("A"), self.create_concept("B"), self.create_concept("C")
        self.send(self.parents_op(c, [a], 0))
        self.send(self.parents_op(b, [a], 0))  # advances A's scope, not C's
        self.assertEqual(self.send(self.parents_op(c, [a], 0))[1],
                         {"concept_id": c, "code": "ACKNOWLEDGED", "changed": False, "server_revision": 1,
                          "parent_ids": [a]})
        self.assertEqual(self.revisions("concept-parents"), {a: 2, b: 1, c: 1})


class DeleteTests(ConceptSyncTestCase):
    def test_in_use_refused_and_absence_converges(self) -> None:
        ethics = self.create_concept("Ethics")
        work = self.library.add_work("W")
        self.research_note(work, "[[concept:ethics]]")
        delete = self.envelope("DELETE_CONCEPT", ethics, {})
        self.assertEqual(self.send(delete), (409, {"concept_id": ethics, "code": "CONCEPT_IN_USE"}))
        self.research_note(work, "")
        self.assertEqual(self.send(self.envelope("DELETE_CONCEPT", ethics, {}))[1]["changed"], True)
        self.assertEqual(self.send(self.envelope("DELETE_CONCEPT", ethics, {}))[1]["changed"], False)

    def test_a_base_from_before_a_delete_never_lands_on_a_recreated_concept(self) -> None:
        concept_id = self.create_concept("Ethics")
        self.send(self.envelope("DELETE_CONCEPT", concept_id, {}))
        self.assertEqual(self.send(self.create("Ethics", concept_id))[1]["changed"], True)
        description = self.envelope("SET_CONCEPT_FIELD", concept_id, {"field": "description", "value": "Old"}, 0)
        for data in (description, self.identity_op(concept_id, "Renamed", [], 0),
                     self.parents_op(concept_id, [self.create_concept("Other")], 0)):
            with self.subTest(operation=data["operation"]):
                self.assertEqual(self.send(data)[1]["code"], "REVISION_CONFLICT")
        self.assertEqual(self.revisions("concept-field"), {scope_key(concept_id, "description"): 1})

    def test_a_delete_racing_an_edit_of_the_definition(self) -> None:
        concept_id = self.create_concept("Ethics")
        edit = self.envelope("SET_CONCEPT_FIELD", concept_id, {"field": "description", "value": "New"}, 0)
        holder, contender = self.race(lambda scope: concepts.delete(scope, concept_id), lambda: self.send(edit))
        self.assertEqual(holder, (True, None))
        self.assertEqual(contender, (404, {"concept_id": concept_id, "field": "description",
                                           "code": "ENTITY_NOT_FOUND"}))


class AccessTests(ConceptSyncTestCase):
    def test_a_reader_cannot_send_operations(self) -> None:
        with self.assertRaises(AccessRefused):
            self.send(self.create("Ethics"), self.reader_context())
        self.assertEqual((self.ledger(), self.count("concepts")), ([], 0))

    def test_another_librarys_concept_is_not_found(self) -> None:
        ethics = self.create_concept("Ethics")
        other = self.other_actor()
        device = str(other.context.device_id)
        for data in (self.identity_op(ethics, "X", [], 0), self.parents_op(ethics, [], 0),
                     self.envelope("SET_CONCEPT_FIELD", ethics, {"field": "description", "value": "X"}, 0)):
            with self.subTest(operation=data["operation"]):
                self.assertEqual(self.send(data | {"device_id": device}, other.context)[1]["code"],
                                 "ENTITY_NOT_FOUND")
        self.assertEqual(self.send(self.envelope("DELETE_CONCEPT", ethics, {}) | {"device_id": device},
                                   other.context)[1]["changed"], False)
        self.assertEqual(self.library.get_concept(ethics)["name"], "Ethics")
