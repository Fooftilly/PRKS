"""The Position and Argument sync handlers on PostgreSQL (L-R-1 #634).

``tests/test_arguments_parity.py`` proves each operation answers as on
SQLite. These tests cover what only PostgreSQL has: a refused create that
leaves nothing although its refusal is ledgered, ids of another Library,
citations a legacy list cannot pin and Version-aware lists (§8.5), Works
merged away (§14.2),
the isolation of the two families that add citations (§7.1), races, and a
stale operation after a record was deleted and created again under its id.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from backend import sync_protocol
from backend.db.database import Isolation
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.works import commands, lifecycle, merge
from backend.entity_ids import generate
from backend.sync import protocol
from backend.sync.families import FamilyHandler
from backend.sync.handlers import argument as argument_handlers
from backend import argument_sync
from tests.pg.test_arguments import ResearchTestCase, pinned, source, target
from tests.pg.test_works_sync import SyncTestCase


class ResearchSyncTestCase(SyncTestCase, ResearchTestCase):
    def envelope(self, operation: str, entity_id: str, payload: dict[str, Any],
                 base: Optional[int] = None, **changes: Any) -> dict[str, Any]:
        changes.setdefault("entity_type", "position" if "POSITION" in operation else "argument")
        return super().envelope(operation, entity_id, payload, base, **changes)

    def create_position_op(self, name: str = "Claim", position_id: Optional[str] = None) -> dict[str, Any]:
        return self.envelope("CREATE_POSITION", position_id or generate("P"), {"name": name, "description": ""})

    def create_argument_op(self, name: str = "Argument", argument_id: Optional[str] = None, *,
                           sources: Optional[list[Any]] = None,
                           targets: Optional[list[Any]] = None) -> dict[str, Any]:
        return self.envelope("CREATE_ARGUMENT", argument_id or generate("A"),
                             {"name": name, "kind": "argument", "main_text": "", "sources": sources or [],
                              "targets": targets or []})

    def sent(self, data: dict[str, Any]) -> str:
        status, result = self.send(data)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"), result)
        return data["entity_id"]

    def sources_op(self, argument_id: str, sources: list[Any], base: int) -> dict[str, Any]:
        return self.envelope("SET_ARGUMENT_SOURCES", argument_id, {"sources": sources}, base)

    def targets_op(self, argument_id: str, targets: list[Any], base: int) -> dict[str, Any]:
        return self.envelope("SET_ARGUMENT_TARGETS", argument_id, {"targets": targets}, base)

    def applying(self, handler: FamilyHandler, data: dict[str, Any]) -> Any:
        """A holder that applies ``data`` with ``handler`` in the race's open transaction."""
        op = sync_protocol.normalize_envelope(data)
        return lambda scope: handler.apply(scope, op, datetime.now(timezone.utc), self.env)


class PositionSyncTests(ResearchSyncTestCase):
    def test_create_field_and_delete(self) -> None:
        data = self.create_position_op("  A   claim ")
        position_id = data["entity_id"]
        first = self.send(data)
        self.assertEqual(first, (200, {"position_id": position_id, "code": "ACKNOWLEDGED", "changed": True,
                                       "position": {"id": position_id, "name": "A claim", "description": ""}}))
        self.assertEqual(self.send(data), first)  # a replay
        self.assertEqual(self.send(self.create_position_op("Other", position_id))[1]["changed"], False)
        field = self.envelope("SET_POSITION_FIELD", position_id, {"field": "name", "value": "B"}, 0)
        self.assertEqual(self.send(field)[1]["server_revision"], 1)
        stale = self.send(self.envelope("SET_POSITION_FIELD", position_id, {"field": "name", "value": "C"}, 0))
        self.assertEqual(stale[1]["code"], "REVISION_CONFLICT")
        a = self.argument("A", targets=[target("position", position_id)])
        delete = self.envelope("DELETE_POSITION", position_id, {})
        self.assertEqual(self.send(delete), (409, {"position_id": position_id, "code": "POSITION_IN_USE"}))
        self.library.replace_argument_targets(a, [])
        self.assertEqual(self.send(self.envelope("DELETE_POSITION", position_id, {}))[1]["changed"], True)
        self.assertEqual(self.send(self.envelope("DELETE_POSITION", position_id, {}))[1]["changed"], False)
        # A base from before the delete never lands on a Position created again.
        self.sent(self.create_position_op("Again", position_id))
        description = {"field": "description", "value": "Old"}
        self.assertEqual(self.send(self.envelope("SET_POSITION_FIELD", position_id, description, 0))[1]["code"],
                         "REVISION_CONFLICT")

    def test_another_librarys_id_is_refused_unledgered(self) -> None:
        other = self.other_actor()
        theirs = generate("P")
        self.assertEqual(self.send(self.create_position_op("Theirs", theirs)
                                   | {"device_id": str(other.context.device_id)}, other.context)[0], 200)
        before = len(self.ledger())
        with self.assertRaises(AccessRefused) as caught:
            self.send(self.create_position_op("Mine", theirs))
        self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual((len(self.ledger()), self.library.list_positions()), (before, []))


class PositionEnvelopeTests(ResearchSyncTestCase):
    """What the retired SQLite ``tests/test_position_sync.py`` pinned beyond the round trip above."""

    def field_op(self, position_id: str, field: str, value: Any, base: Optional[int]) -> dict[str, Any]:
        return self.envelope("SET_POSITION_FIELD", position_id, {"field": field, "value": value}, base)

    def test_refused_envelopes_change_nothing_and_are_never_ledgered(self) -> None:
        position_id = self.sent(self.create_position_op("Realism is false"))
        before = len(self.ledger())
        for data in (
                self.create_position_op("X") | {"entity_type": "person"},  # a Person shares the P- prefix
                self.create_position_op("   "),
                self.field_op(position_id, "id", "x", 0),
                self.field_op(position_id, "created_at", "x", 0),
                self.field_op(position_id, "updated_at", "x", 0),
                self.field_op(position_id, "name", "   ", 0)):
            with self.subTest(operation=data["operation"], payload=data["payload"]):
                status, result = self.send(data)
                self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))
        status, result = self.send(self.envelope("DELETE_POSITION", position_id, {}, 0))
        self.assertEqual((status, result["code"]), (400, "INVALID_BASE_REVISION"))
        status, result = self.send(self.envelope("DELETE_POSITION", position_id, {"cascade": True}))
        self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))
        self.assertEqual(len(self.ledger()), before)
        self.assertEqual([(row["id"], row["name"]) for row in self.library.list_positions()],
                         [(position_id, "Realism is false")])

    def test_a_repeated_name_is_accepted(self) -> None:
        self.sent(self.create_position_op("Realism is false"))
        self.sent(self.create_position_op("Realism is false"))
        self.assertEqual([row["name"] for row in self.library.list_positions()], ["Realism is false"] * 2)

    def test_both_paths_normalize_identically_and_spacing_is_no_change(self) -> None:
        data = self.envelope("CREATE_POSITION", generate("P"),
                             {"name": "  Realism   is   false  ", "description": "  A   claim.  "})
        position_id = self.sent(data)
        ordinary = self.library.create_position("  Realism   is   false  ", "  A   claim.  ")
        synced = self.library.get_position(position_id)
        self.assertEqual((synced["name"], synced["description"]), (ordinary["name"], ordinary["description"]))
        self.assertEqual(synced["name"], "Realism is false")
        status, result = self.send(self.field_op(position_id, "name", "Realism    is false", 0))
        self.assertEqual((status, result["code"], result["changed"], result["server_revision"]),
                         (200, "ACKNOWLEDGED", False, 0))

    def test_ordinary_edits_and_sync_share_each_fields_revision(self) -> None:
        position_id = self.sent(self.envelope("CREATE_POSITION", generate("P"),
                                              {"name": "Realism is false", "description": "A claim."}))
        self.library.update_position(position_id, name="Realism is false")  # the same value
        self.library.update_position(position_id, name="Renamed by the API")
        self.assertEqual(self.library.get_position_sync_state(position_id),
                         {"position_id": position_id,
                          "fields": {"description": {"revision": 0}, "name": {"revision": 1}}})
        status, result = self.send(self.field_op(position_id, "name", "Renamed offline", 0))
        self.assertEqual((status, result["code"], result["current_value"], result["current_revision"]),
                         (409, "REVISION_CONFLICT", "Renamed by the API", 1))
        status, result = self.send(self.field_op(position_id, "description", "x" * 500, 0))
        self.assertEqual((status, result["code"], result["server_revision"]), (200, "ACKNOWLEDGED", 1))
        self.assertTrue(result["value_omitted"])
        self.assertNotIn("value", result)
        self.library.update_position(position_id, name="Both", description="Changed")
        self.assertEqual(self.library.get_position_sync_state(position_id)["fields"],
                         {"description": {"revision": 2}, "name": {"revision": 2}})
        self.assertIsNone(self.library.get_position_sync_state(generate("P")))


class ArgumentCreateTests(ResearchSyncTestCase):
    def test_a_refused_create_is_ledgered_and_leaves_nothing(self) -> None:
        data = self.create_argument_op(sources=[source(generate("W"))])
        self.assertEqual(self.send(data), (404, {"argument_id": data["entity_id"], "code": "WORK_NOT_FOUND"}))
        self.assertEqual((self.count("arguments"), self.ledger()[-1]["status"]), (0, "WORK_NOT_FOUND"))
        self.assertEqual(self.send(data)[1]["code"], "WORK_NOT_FOUND")  # a replay
        cycle = self.create_argument_op()
        cycle["payload"]["targets"] = [target("argument", cycle["entity_id"])]
        self.assertEqual(self.send(cycle)[1]["code"], "ARGUMENT_CYCLE")
        self.assertEqual(self.count("arguments"), 0)

    def test_a_create_acknowledges_and_converges(self) -> None:
        work, claim = self.library.add_work("W"), self.position("Claim")
        data = self.create_argument_op(sources=[source(work, "3")], targets=[target("position", claim)])
        self.assertEqual(self.send(data), (200, {"argument_id": data["entity_id"], "code": "ACKNOWLEDGED",
                                                 "changed": True}))
        self.assertEqual(self.send(self.create_argument_op("Other", data["entity_id"]))[1]["changed"], False)
        self.assertEqual(self.citations(data["entity_id"]), [(0, work, self.primary(work), "3")])

    def test_another_librarys_id_is_refused_unledgered_whatever_its_lists(self) -> None:
        other = self.other_actor()
        theirs = generate("A")
        self.send(self.create_argument_op("Theirs", theirs) | {"device_id": str(other.context.device_id)},
                  other.context)
        before = len(self.ledger())
        for data in (self.create_argument_op("Mine", theirs),
                     self.create_argument_op("Mine", theirs, sources=[source(generate("W"))])):
            with self.subTest(sources=data["payload"]["sources"]), self.assertRaises(AccessRefused):
                self.send(data)
        self.assertEqual(len(self.ledger()), before)

    def test_two_creates_of_one_id_make_one_argument(self) -> None:
        argument_id = generate("A")
        holder, contender = self.race(
            self.applying(argument_handlers.CREATE_HANDLER, self.create_argument_op("A", argument_id)),
            lambda: self.send(self.create_argument_op("B", argument_id)), serializable=True)
        self.assertEqual(holder[1]["changed"], True)
        self.assertEqual(contender[1]["changed"], False)
        self.assertEqual(self.library.get_argument(argument_id)["name"], "A")

    def test_one_create_sent_concurrently_applies_once(self) -> None:
        data = self.create_argument_op("Once")
        results = self.concurrently([lambda: self.send(data) for _ in range(4)])
        self.assertTrue(all(result == results[0] for result in results), results)
        self.assertEqual((self.count("arguments"), len(self.ledger())), (1, 1))


class ArgumentEnvelopeAndFieldTests(ResearchSyncTestCase):
    """What the retired SQLite ``tests/test_argument_sync.py`` pinned beyond the tests above."""

    def field_op(self, argument_id: str, field: str, value: Any, base: Optional[int]) -> dict[str, Any]:
        return self.envelope("SET_ARGUMENT_FIELD", argument_id, {"field": field, "value": value}, base)

    def targets_of(self, argument_id: str) -> list[tuple[str, str, str]]:
        return [(row["type"], row["id"], row["verdict_id"]) for row in self.library.get_argument(argument_id)["targets"]]

    def test_refused_envelopes_change_nothing_and_are_never_ledgered(self) -> None:
        work, claim = self.library.add_work("W"), self.position("Claim")
        a = self.sent(self.create_argument_op())
        before = (len(self.ledger()), self.count("arguments"))
        missing_lists = self.create_argument_op()
        del missing_lists["payload"]["sources"], missing_lists["payload"]["targets"]
        unknown_kind = self.create_argument_op()
        unknown_kind["payload"]["kind"] = "opinion"
        for expected, data in (
                ("INVALID_ENVELOPE", self.create_argument_op("Short id", "A-1234ABCD")),
                ("INVALID_ENVELOPE", unknown_kind),
                ("INVALID_ENVELOPE", missing_lists),
                ("INVALID_ENVELOPE", self.create_argument_op() | {"entity_type": "position"}),
                ("INVALID_BASE_REVISION", self.create_argument_op() | {"base_revision": 0}),
                ("INVALID_BASE_REVISION", self.field_op(a, "name", "After", None)),
                ("INVALID_ENVELOPE", self.field_op(a, "colour", "red", 0)),
                ("INVALID_ENVELOPE", self.field_op(a, "kind", "opinion", 0)),
                ("INVALID_ENVELOPE", self.sources_op(a, [source(work), source(work, "2")], 0)),
                ("INVALID_ENVELOPE", self.targets_op(a, [{"type": "concept", "id": "C-1", "verdict_id": "supports"}],
                                                     0)),
                ("INVALID_ENVELOPE", self.targets_op(a, [target("position", claim),
                                                         target("position", claim, "opposes")], 0)),
                ("INVALID_BASE_REVISION", self.envelope("DELETE_ARGUMENT", a, {}, 0))):
            with self.subTest(code=expected, operation=data["operation"], payload=data["payload"]):
                self.assertEqual(self.send(data), (400, {"code": expected}))
        self.assertEqual((len(self.ledger()), self.count("arguments")), before)
        self.assertEqual((self.citations(a), self.targets_of(a)), ([], []))

    def test_every_argument_operation_is_registered_under_the_argument_type(self) -> None:
        self.assertLessEqual({"CREATE_ARGUMENT", "SET_ARGUMENT_FIELD", "SET_ARGUMENT_SOURCES",
                              "SET_ARGUMENT_TARGETS", "DELETE_ARGUMENT"}, protocol.supported_operations())
        a = self.sent(self.create_argument_op())
        for data in (self.create_argument_op(), self.field_op(a, "name", "X", 0), self.sources_op(a, [], 0),
                     self.targets_op(a, [], 0), self.envelope("DELETE_ARGUMENT", a, {})):
            for entity_type in ("position", "work"):
                with self.subTest(operation=data["operation"], entity_type=entity_type):
                    self.assertEqual(self.send(data | {"entity_type": entity_type}), (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual((self.count("arguments"), len(self.ledger())), (1, 1))

    def test_a_create_carries_its_lists_without_advancing_a_revision(self) -> None:
        work, claim = self.library.add_work("W"), self.position("Claim")
        data = self.envelope("CREATE_ARGUMENT", generate("A"), {
            "name": "  Spaced   out  ", "kind": "stance", "main_text": "Body.",
            "sources": [source(work, "4")], "targets": [target("position", claim)]})
        a = self.sent(data)
        ordinary = self.library.create_argument(name="  Spaced   out  ", kind="stance", main_text="Body.",
                                                sources=[source(work, "4")])
        synced = self.library.get_argument(a)
        self.assertEqual((synced["name"], synced["kind"], synced["main_text"]), ("Spaced out", "stance", "Body."))
        self.assertEqual(ordinary["name"], synced["name"])
        self.assertEqual(self.citations(a), [(0, work, self.primary(work), "4")])
        self.assertEqual([row[1:] for row in self.citations(a)], [row[1:] for row in self.citations(ordinary["id"])])
        self.assertEqual(self.targets_of(a), [("position", claim, "supports")])
        for argument_id in (a, ordinary["id"]):
            state = self.argument_state(argument_id)
            self.assertEqual((state["sources"], state["targets"]), ({"revision": 0}, {"revision": 0}))
            self.assertEqual({field: value["revision"] for field, value in state["fields"].items()},
                             {"kind": 0, "main_text": 0, "name": 0})
        refused = self.create_argument_op(targets=[target("position", generate("P"))])
        self.assertEqual(self.send(refused), (404, {"argument_id": refused["entity_id"], "code": "POSITION_NOT_FOUND"}))
        self.assertIsNone(self.library.get_argument(refused["entity_id"]))

    def test_each_field_is_its_own_scope_and_ordinary_edits_share_it(self) -> None:
        a = self.sent(self.envelope("CREATE_ARGUMENT", generate("A"), {
            "name": "Before", "kind": "argument", "main_text": "Body.", "sources": [], "targets": []}))
        for field, value in (("name", "After"), ("kind", "stance"), ("main_text", "x" * 5000)):
            with self.subTest(field=field):
                status, result = self.send(self.field_op(a, field, value, 0))
                self.assertEqual((status, result["code"], result["server_revision"]), (200, "ACKNOWLEDGED", 1))
                self.assertTrue(result["value_omitted"])
                self.assertNotIn("value", result)
                self.assertNotIn("current_value", result)
        synced = self.library.get_argument(a)
        self.assertEqual((synced["name"], synced["kind"], synced["main_text"]), ("After", "stance", "x" * 5000))
        status, result = self.send(self.field_op(a, "name", "Something else", 0))
        self.assertEqual((status, result["code"], result["current_value"], result["current_revision"]),
                         (409, "REVISION_CONFLICT", "After", 1))
        status, result = self.send(self.field_op(a, "name", "After", 0))
        self.assertEqual((status, result["code"], result["changed"], result["server_revision"]),
                         (200, "ACKNOWLEDGED", False, 1))
        self.assertEqual(self.send(self.field_op(a, "name", "Later", 7))[1]["code"], "FUTURE_REVISION")
        missing = generate("A")
        self.assertEqual(self.send(self.field_op(missing, "name", "X", 0)),
                         (404, {"argument_id": missing, "field": "name", "code": "ENTITY_NOT_FOUND"}))
        # An ordinary edit moves only what it changed, so a stale sync edit of it conflicts.
        self.library.update_argument(a, name="After")
        self.library.update_argument(a, name="Renamed online")
        status, result = self.send(self.field_op(a, "name", "Renamed offline", 1))
        self.assertEqual((status, result["code"], result["current_value"]),
                         (409, "REVISION_CONFLICT", "Renamed online"))
        state = self.argument_state(a)
        self.assertEqual({field: value["revision"] for field, value in state["fields"].items()},
                         {"kind": 1, "main_text": 1, "name": 2})
        self.assertNotIn("x" * 50, repr(state))
        self.assertIsNone(self.library.get_argument_sync_state(generate("A")))

    def test_the_target_list_is_one_aggregate_of_both_kinds(self) -> None:
        one, two = self.position("One"), self.position("Two")
        other = self.sent(self.create_argument_op("Other"))
        a = self.sent(self.create_argument_op("Mixed"))
        both_kinds = [target("position", one), target("argument", other, "opposes")]
        self.assertEqual(self.send(self.targets_op(a, both_kinds, 0))[1]["server_revision"], 1)
        self.assertEqual(self.targets_of(a), [("position", one, "supports"), ("argument", other, "opposes")])
        self.assertEqual(self.send(self.targets_op(a, both_kinds, 0))[1]["changed"], False)  # stale, the same list
        self.assertEqual(self.send(self.targets_op(a, [target("position", two)], 0))[1]["code"], "REVISION_CONFLICT")
        status, result = self.send(self.targets_op(a, [target("position", one, "qualifies")], 1))
        self.assertEqual((status, result["changed"], result["server_revision"]), (200, True, 2))
        self.assertEqual(self.targets_of(a), [("position", one, "qualifies")])
        self.assertEqual(self.send(self.targets_op(a, [target("position", one, "opposes")], 2))[1]["server_revision"], 3)
        for code, status, rows in (
                ("ARGUMENT_CYCLE", 409, [target("argument", a)]),
                ("POSITION_NOT_FOUND", 404, [target("position", generate("P"))]),
                ("TARGET_NOT_FOUND", 404, [target("argument", generate("A"))]),
                ("INVALID_VERDICT", 409, [target("position", one, "vibes")])):
            with self.subTest(code=code):
                self.assertEqual(self.send(self.targets_op(a, rows, 3)), (status, {"argument_id": a, "code": code}))
        self.assertEqual(self.targets_of(a), [("position", one, "opposes")])
        state = self.argument_state(a)
        self.assertEqual((state["targets"], state["sources"]), ({"revision": 3}, {"revision": 0}))
        self.assertEqual({value["revision"] for value in state["fields"].values()}, {0})

    def test_ordinary_list_replacements_advance_the_same_revisions(self) -> None:
        work, claim = self.library.add_work("W"), self.position("Claim")
        a = self.sent(self.create_argument_op())
        rows = [target("position", claim)]
        self.library.replace_argument_targets(a, rows)
        self.library.replace_argument_targets(a, rows)
        self.library.replace_argument_sources(a, [source(work, "7")])
        self.assertEqual(self.send(self.targets_op(a, [], 0))[1]["code"], "REVISION_CONFLICT")
        self.assertEqual(self.send(self.sources_op(a, [], 0))[1]["code"], "REVISION_CONFLICT")
        state = self.argument_state(a)
        self.assertEqual((state["targets"], state["sources"]), ({"revision": 1}, {"revision": 1}))
        self.assertEqual({value["revision"] for value in state["fields"].values()}, {0})
        self.assertNotIn(work, repr(state))


class ArgumentListTests(ResearchSyncTestCase):
    def test_the_lists_decision_tables(self) -> None:
        w1, w2 = self.library.add_work("One"), self.library.add_work("Two")
        a = self.sent(self.create_argument_op())
        self.assertEqual(self.send(self.sources_op(a, [source(w1)], 0))[1]["server_revision"], 1)
        # A stale base with the same list converges; another list conflicts.
        self.assertEqual(self.send(self.sources_op(a, [source(w1)], 0))[1],
                         {"argument_id": a, "code": "ACKNOWLEDGED", "changed": False, "server_revision": 1})
        self.assertEqual(self.send(self.sources_op(a, [source(w2)], 0))[1],
                         {"argument_id": a, "code": "REVISION_CONFLICT", "current_revision": 1,
                          "current_count": 1, "requested_count": 1})
        self.assertEqual(self.send(self.sources_op(a, [source(w2)], 4))[1]["code"], "FUTURE_REVISION")
        b = self.sent(self.create_argument_op("B"))
        self.assertEqual(self.send(self.targets_op(b, [target("argument", a)], 0))[1]["server_revision"], 1)
        self.assertEqual(self.send(self.targets_op(a, [target("argument", b)], 0)),
                         (409, {"argument_id": a, "code": "ARGUMENT_CYCLE"}))
        self.assertEqual(self.send(self.envelope("DELETE_ARGUMENT", a, {})),
                         (409, {"argument_id": a, "code": "ARGUMENT_TARGETED"}))

    def test_a_citation_the_legacy_list_cannot_pin_is_ledgered_as_refused(self) -> None:
        work = self.library.add_work("W")
        self.add_manifestation(work)
        a = self.sent(self.create_argument_op())
        self.assertEqual(self.send(self.sources_op(a, [source(work, "5")], 0)),
                         (409, {"argument_id": a, "code": "ARGUMENT_SOURCE_VERSION_REQUIRED"}))
        self.assertEqual(self.ledger()[-1]["status"], "ARGUMENT_SOURCE_VERSION_REQUIRED")
        self.assertEqual(self.send(self.sources_op(a, [source(work)], 0))[1]["changed"], True)

    def test_a_citation_of_a_merged_away_work_is_refused(self) -> None:
        source_work, target_work = self.library.add_work("Source"), self.library.add_work("Target")
        a = self.sent(self.create_argument_op())

        def merge_away(scope: Any) -> None:
            commands.delete_work(scope, source_work)
            lifecycle.mark_merged(scope, source_work, target_work)

        self.run_command(merge_away)
        merged = {"code": "WORK_MERGED", "work_id": source_work, "target_work_id": target_work}
        self.assertEqual(self.send(self.sources_op(a, [source(target_work), source(source_work)], 0)),
                         (409, merged))
        self.assertEqual(self.send(self.create_argument_op(sources=[source(source_work)])), (409, merged))
        self.assertEqual((self.citations(a), self.count("arguments")), ([], 1))

    def test_the_families_that_add_citations_are_serializable(self) -> None:
        for operation in ("CREATE_ARGUMENT", "SET_ARGUMENT_SOURCES"):
            with self.subTest(operation=operation):
                self.assertIn(operation, merge.PEER_WRITERS)
                self.assertIs(protocol._HANDLERS[operation].isolation, Isolation.SERIALIZABLE)
        for operation in ("SET_ARGUMENT_FIELD", "SET_ARGUMENT_TARGETS", "DELETE_ARGUMENT", "CREATE_POSITION",
                          "SET_POSITION_FIELD", "DELETE_POSITION"):
            with self.subTest(operation=operation):
                self.assertIs(protocol._HANDLERS[operation].isolation, Isolation.READ_COMMITTED)

    def test_a_citation_racing_a_delete_of_its_work(self) -> None:
        work = self.library.add_work("W")
        a = self.sent(self.create_argument_op())
        holder, contender = self.race(lambda scope: commands.delete_work(scope, work),
                                      lambda: self.send(self.sources_op(a, [source(work)], 0)))
        self.assertIsNotNone(holder)
        self.assertEqual(contender, (404, {"argument_id": a, "code": "WORK_NOT_FOUND"}))


class VersionAwareSourcesTests(ResearchSyncTestCase):
    """``{work_id, manifestation_id, pages}`` entries in ``CREATE_ARGUMENT`` and ``SET_ARGUMENT_SOURCES``."""

    def test_the_envelope_of_a_version_aware_list(self) -> None:
        work = self.library.add_work("W")
        only = self.primary(work)
        a = self.sent(self.create_argument_op())
        bad: list[list[Any]] = [
            [pinned(work, only), source(work, "2")],                    # the two shapes mixed
            [{**pinned(work, only), "extra": 1}],
            [pinned(work, 3)],
            [pinned(work, "")], [pinned(work, f" {only}")], [pinned(f" {work}", only)],
            [pinned(work, only, 5)],
            [pinned(work, only, "\x07")],
            [pinned(work, only, "2"), pinned(work, only, "2")],          # an exact repeat
            [pinned(work, None)] * 2,
            [pinned(work, None, str(n)) for n in range(argument_sync.MAX_SOURCES + 1)],
        ]
        for sources in bad:
            with self.subTest(sources=str(sources)[:60]):
                self.assertEqual(self.send(self.sources_op(a, sources, 0)), (400, {"code": "INVALID_ENVELOPE"}))
                self.assertEqual(self.send(self.create_argument_op(sources=sources)),
                                 (400, {"code": "INVALID_ENVELOPE"}))
        extra = self.envelope("SET_ARGUMENT_SOURCES", a, {"sources": [pinned(work, only)], "x": 1}, 0)
        self.assertEqual(self.send(extra), (400, {"code": "INVALID_ENVELOPE"}))
        unbased = self.envelope("SET_ARGUMENT_SOURCES", a, {"sources": [pinned(work, only)]})
        self.assertEqual(self.send(unbased)[1]["code"], "INVALID_BASE_REVISION")
        bad_field = self.envelope("CREATE_ARGUMENT", generate("A"), {
            "name": " ", "kind": "argument", "main_text": "", "sources": [pinned(work, only)], "targets": []})
        self.assertEqual(self.send(bad_field), (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual((self.citations(a), self.count("arguments")), ([], 1))

    def test_create_and_replace_with_pins_and_the_decision_table(self) -> None:
        work = self.library.add_work("W")
        first, second = self.primary(work), self.add_manifestation(work)
        a = self.sent(self.create_argument_op(sources=[pinned(work, second, "5"), pinned(work, None)]))
        self.assertEqual(self.citations(a), [(0, work, second, "5"), (1, work, None, "")])
        wanted = [pinned(work, None), pinned(work, second, "5"), pinned(work, first, "5")]
        self.assertEqual(self.send(self.sources_op(a, wanted, 0))[1],
                         {"argument_id": a, "code": "ACKNOWLEDGED", "changed": True, "server_revision": 1})
        # A stale base converges on the same pins, and conflicts when only a pin differs.
        self.assertEqual(self.send(self.sources_op(a, wanted, 0))[1]["changed"], False)
        repinned = [pinned(work, None), pinned(work, first, "5"), pinned(work, second, "5")]
        self.assertEqual(self.send(self.sources_op(a, repinned, 0))[1],
                         {"argument_id": a, "code": "REVISION_CONFLICT", "current_revision": 1,
                          "current_count": 3, "requested_count": 3})
        # At the current base a pin change is a change.
        self.assertEqual(self.send(self.sources_op(a, repinned, 1))[1]["server_revision"], 2)
        self.assertEqual(self.citations(a), [(0, work, None, ""), (1, work, first, "5"), (2, work, second, "5")])

    def test_a_version_refusal_is_ledgered_and_changes_nothing(self) -> None:
        w1, w2 = self.library.add_work("One"), self.library.add_work("Two")
        a = self.sent(self.create_argument_op(sources=[pinned(w1, None, "1")]))
        for code, status, sources in (
                ("ARGUMENT_SOURCE_VERSION_MISMATCH", 409, [pinned(w2, self.primary(w1))]),
                ("ARGUMENT_SOURCE_VERSION_NOT_FOUND", 404, [pinned(w1, generate("MF"))]),
                ("WORK_NOT_FOUND", 404, [pinned(generate("W"), None)])):
            with self.subTest(code=code):
                self.assertEqual(self.send(self.sources_op(a, sources, 0)), (status, {"argument_id": a, "code": code}))
                self.assertEqual(self.ledger()[-1]["status"], code)
                created = self.create_argument_op(sources=sources)
                self.assertEqual(self.send(created), (status, {"argument_id": created["entity_id"], "code": code}))
        self.assertEqual((self.citations(a), self.revisions("argument-sources"), self.count("arguments")),
                         ([(0, w1, None, "1")], {}, 1))

    def test_a_pin_of_a_merged_away_work_is_refused(self) -> None:
        source_work, target_work = self.library.add_work("Source"), self.library.add_work("Target")
        a = self.sent(self.create_argument_op())

        def merge_away(scope: Any) -> None:
            commands.delete_work(scope, source_work)
            lifecycle.mark_merged(scope, source_work, target_work)

        self.run_command(merge_away)
        self.assertEqual(self.send(self.sources_op(a, [pinned(source_work, None, "1")], 0)),
                         (409, {"code": "WORK_MERGED", "work_id": source_work, "target_work_id": target_work}))

    def test_two_lists_on_one_base_one_lands_the_other_conflicts(self) -> None:
        work = self.library.add_work("W")
        second = self.add_manifestation(work)
        a = self.sent(self.create_argument_op())
        holder, contender = self.race(
            self.applying(argument_handlers.SOURCES_HANDLER, self.sources_op(a, [pinned(work, second, "1")], 0)),
            lambda: self.send(self.sources_op(a, [pinned(work, None)], 0)), serializable=True)
        self.assertEqual(holder[1]["server_revision"], 1)
        self.assertEqual(contender[1]["code"], "REVISION_CONFLICT")
        self.assertEqual(self.citations(a), [(0, work, second, "1")])


class ArgumentDeleteTests(ResearchSyncTestCase):
    def test_in_use_refused_absence_converges_and_no_base_outlives_it(self) -> None:
        a = self.sent(self.create_argument_op())
        work = self.library.add_work("W")
        self.research_note(work, f"[[argument:{a}]]")
        self.assertEqual(self.send(self.envelope("DELETE_ARGUMENT", a, {})),
                         (409, {"argument_id": a, "code": "ARGUMENT_IN_USE"}))
        self.research_note(work, "")
        self.assertEqual(self.send(self.envelope("DELETE_ARGUMENT", a, {}))[1]["changed"], True)
        self.assertEqual(self.send(self.envelope("DELETE_ARGUMENT", a, {}))[1]["changed"], False)
        self.sent(self.create_argument_op("Again", a))
        for data in (self.envelope("SET_ARGUMENT_FIELD", a, {"field": "main_text", "value": "Old"}, 0),
                     self.sources_op(a, [source(work)], 0),
                     self.targets_op(a, [target("position", self.position("P"))], 0)):
            with self.subTest(operation=data["operation"]):
                self.assertEqual(self.send(data)[1]["code"], "REVISION_CONFLICT")
