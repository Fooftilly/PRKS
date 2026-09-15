"""Positions: three shapes, and parity with the ordinary endpoints.

The decision worth recording is why this domain is SMALL: `name` and
`description` are independent fields rather than one aggregate, because nothing
in the schema or the API links them.
"""
import pathlib
import tempfile
import unittest
import uuid

from backend import entity_ids, position_sync as positions, sync_protocol
from backend.db_manager import PRKSDatabase
from backend.research_network import (
    ResearchError,
    create_argument,
    create_position,
    delete_position,
    get_position,
    update_position,
)
from backend.storage.config import StorageConfig

ROOT = pathlib.Path(__file__).resolve().parents[1]


class PositionSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-position-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.device = str(uuid.uuid4())

    # ---- helpers ----------------------------------------------------------

    def send(self, operation, entity_id, payload, base=None, op_id=None):
        return sync_protocol.process_operation(self.db, dict(
            op_id=op_id or str(uuid.uuid4()), device_id=self.device, operation=operation,
            entity_type="position", entity_id=entity_id, payload=payload,
            base_revision=base, occurred_at="2026-09-15T10:00:00Z",
            created_at="2026-09-15T10:00:00Z", depends_on=[]))

    def create(self, name, description="", position_id=None):
        pid = position_id or entity_ids.generate("P")
        status, result = self.send("CREATE_POSITION", pid,
                                   dict(name=name, description=description), None)
        return pid, status, result

    def field(self, position_id, name, value, base):
        return self.send("SET_POSITION_FIELD", position_id,
                         dict(field=name, value=value), base)

    def stored(self, position_id):
        rows = self.db.execute_query("SELECT * FROM positions WHERE id = ?", (position_id,))
        return rows[0] if rows else None

    def revision(self, position_id, field):
        with self.db.connection() as conn:
            return positions.get_revision(conn, position_id, field)

    # ---- construction -----------------------------------------------------

    def test_a_position_is_created_under_the_id_the_client_minted(self):
        pid, status, result = self.create("Realism is false", "A claim.")
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["changed"])
        self.assertEqual(result["position"]["name"], "Realism is false")
        self.assertEqual(self.stored(pid)["description"], "A claim.")
        for name in positions.FIELDS:
            self.assertEqual(self.revision(pid, name), 0, name)

    def test_creation_requires_a_collision_resistant_id(self):
        for bad in ("P-ABCD1234", "realism", "C-" + "A" * 32):
            with self.subTest(bad=bad):
                _, status, result = self.create("X", position_id=bad)
                self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    def test_a_position_shares_the_person_id_prefix_without_colliding(self):
        """Persons and Positions both use `P-`, so the entity TYPE registered
        with the operation is what tells them apart -- never the prefix."""
        pid, _, _ = self.create("Realism is false")
        self.assertTrue(pid.startswith("P-"))
        status, result = sync_protocol.process_operation(self.db, dict(
            op_id=str(uuid.uuid4()), device_id=self.device, operation="CREATE_POSITION",
            entity_type="person", entity_id=entity_ids.generate("P"),
            payload=dict(name="X", description=""), base_revision=None,
            occurred_at="2026-09-15T10:00:00Z", created_at="2026-09-15T10:00:00Z",
            depends_on=[]))
        self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    def test_a_repeated_name_is_accepted(self):
        """Positions have never been unique by name -- `positions.name` carries
        no UNIQUE constraint and `create_position` performs no lookup. Inventing
        that rule would refuse something the ordinary endpoint accepts."""
        self.create("Realism is false")
        _, status, result = self.create("Realism is false")
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(len(self.db.execute_query("SELECT id FROM positions")), 2)

    def test_an_empty_name_is_refused(self):
        _, status, result = self.create("   ")
        self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    def test_replaying_a_creation_changes_nothing_twice(self):
        pid = entity_ids.generate("P")
        op_id = str(uuid.uuid4())
        first = self.send("CREATE_POSITION", pid, dict(name="Once", description=""),
                          None, op_id=op_id)
        second = self.send("CREATE_POSITION", pid, dict(name="Once", description=""),
                           None, op_id=op_id)
        self.assertEqual(first, second)
        self.assertEqual(len(self.db.execute_query("SELECT id FROM positions")), 1)

    def test_a_second_delivery_of_the_same_creation_is_idempotent(self):
        pid, _, _ = self.create("Realism is false")
        _, status, result = self.create("Something else", position_id=pid)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])
        self.assertEqual(self.stored(pid)["name"], "Realism is false")

    # ---- fields -----------------------------------------------------------

    def test_each_field_carries_its_own_revision(self):
        """The reason this domain is not an aggregate: nothing links the two,
        so an unrelated description edit must not conflict with a rename."""
        pid, _, _ = self.create("Realism is false")
        status, result = self.field(pid, "description", "A claim.", 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(result["server_revision"], 1)
        self.assertEqual(self.revision(pid, "name"), 0,
                         "one decision moves one conflict unit")

    def test_the_value_is_not_echoed_back(self):
        pid, _, _ = self.create("Realism is false")
        _, result = self.field(pid, "description", "x" * 500, 0)
        self.assertTrue(result["value_omitted"])
        self.assertNotIn("value", result)

    def test_a_stale_base_against_a_different_value_conflicts(self):
        pid, _, _ = self.create("Realism is false")
        self.field(pid, "name", "Realism is true", 0)
        status, result = self.field(pid, "name", "Realism is unclear", 0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(result["current_value"], "Realism is true")
        self.assertEqual(self.stored(pid)["name"], "Realism is true")

    def test_a_stale_base_that_agrees_converges(self):
        pid, _, _ = self.create("Realism is false")
        self.field(pid, "name", "Realism is true", 0)
        status, result = self.field(pid, "name", "Realism is true", 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])
        self.assertEqual(result["server_revision"], 1)

    def test_a_base_from_the_future_is_refused(self):
        pid, _, _ = self.create("Realism is false")
        status, result = self.field(pid, "name", "Other", 9)
        self.assertEqual((status, result["code"]), (400, "FUTURE_REVISION"))

    def test_an_unknown_position_is_a_domain_answer(self):
        status, result = self.field(entity_ids.generate("P"), "name", "X", 0)
        self.assertEqual((status, result["code"]), (404, "ENTITY_NOT_FOUND"))

    def test_only_the_two_editable_columns_are_fields(self):
        pid, _, _ = self.create("Realism is false")
        for bad in ("id", "created_at", "updated_at"):
            with self.subTest(field=bad):
                status, result = self.field(pid, bad, "x", 0)
                self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    def test_an_empty_name_is_refused_as_an_envelope_error(self):
        """Never ledgered as a domain outcome, and never reported under a code
        that says something else."""
        pid, _, _ = self.create("Realism is false")
        status, result = self.field(pid, "name", "   ", 0)
        self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))
        self.assertEqual(self.stored(pid)["name"], "Realism is false")

    def test_both_paths_normalize_a_name_the_same_way(self):
        pid, _, _ = self.create("  Realism   is   false  ")
        self.assertEqual(self.stored(pid)["name"], "Realism is false")
        ordinary = create_position(self.db, "  Realism   is   true  ")
        self.assertEqual(ordinary["name"], "Realism is true")
        # And a spacing-only edit is therefore not a change at all.
        _, result = self.field(pid, "name", "Realism    is false", 0)
        self.assertFalse(result["changed"])

    def test_the_ordinary_endpoint_advances_the_same_revision(self):
        pid, _, _ = self.create("Realism is false")
        update_position(self.db, pid, name="Renamed elsewhere")
        self.assertEqual(self.revision(pid, "name"), 1)
        status, result = self.field(pid, "name", "Mine", 0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(result["current_value"], "Renamed elsewhere")

    def test_the_ordinary_endpoint_moves_only_the_field_it_wrote(self):
        pid, _, _ = self.create("Realism is false")
        update_position(self.db, pid, description="Changed elsewhere")
        self.assertEqual(self.revision(pid, "description"), 1)
        self.assertEqual(self.revision(pid, "name"), 0)

    # ---- construction and edit parity --------------------------------------

    def test_both_construction_paths_share_one_boundary(self):
        """The ordinary POST and the durable operation must not drift on what a
        legal Position is."""
        source = (ROOT / "backend" / "research_network.py").read_text()
        at = source.index("def create_position(")
        body = source[at: source.index("\ndef ", at + 5)]
        self.assertIn("position_sync.insert_position_on_conn(conn, pid, name, description)", body)
        self.assertNotIn("INSERT INTO positions", body)
        # And the edit path uses the revision-aware boundary, once per field.
        at = source.index("def update_position(")
        body = source[at: source.index("\ndef ", at + 5)]
        self.assertEqual(body.count("position_sync.set_field_on_conn(conn, pid,"), 2)
        self.assertNotIn("UPDATE positions SET", body)
        # And the delete path shares the protection rather than repeating it.
        at = source.index("def delete_position(")
        body = source[at: source.index("\ndef ", at + 5)]
        self.assertIn("position_sync.delete_position_on_conn(conn, pid)", body)
        self.assertNotIn("argument_target_positions", body)

    def test_both_paths_normalize_identically(self):
        ordinary = create_position(self.db, "  Realism   is   false  ",
                                   "  A   claim.  ")
        pid, _, _ = self.create("  Realism   is   false  ", "  A   claim.  ")
        stored = self.stored(pid)
        self.assertEqual(stored["name"], ordinary["name"])
        self.assertEqual(stored["description"], ordinary["description"])

    def test_a_durable_edit_observes_an_ordinary_edit(self):
        """The whole point of sharing the boundary: an offline device measuring
        against a value the ordinary endpoint has since changed must be told."""
        pid, _, _ = self.create("Realism is false")
        update_position(self.db, pid, name="Renamed by the API")
        status, result = self.field(pid, "name", "Renamed offline", 0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(result["current_value"], "Renamed by the API")
        self.assertEqual(result["current_revision"], 1)

    def test_an_ordinary_name_edit_advances_only_the_name(self):
        pid, _, _ = self.create("Realism is false", "A claim.")
        update_position(self.db, pid, name="Renamed")
        self.assertEqual(self.revision(pid, "name"), 1)
        self.assertEqual(self.revision(pid, "description"), 0)
        # So a durable description edit measured against 0 still applies.
        status, result = self.field(pid, "description", "Edited elsewhere", 0)
        self.assertEqual(status, 200)

    def test_an_ordinary_description_edit_advances_only_the_description(self):
        pid, _, _ = self.create("Realism is false", "A claim.")
        update_position(self.db, pid, description="Changed")
        self.assertEqual(self.revision(pid, "description"), 1)
        self.assertEqual(self.revision(pid, "name"), 0)
        status, result = self.send("SET_POSITION_FIELD", pid,
                                   dict(field="name", value="Renamed offline"), 0)
        self.assertEqual(status, 200)

    def test_an_ordinary_edit_of_both_advances_both_separately(self):
        pid, _, _ = self.create("Realism is false", "A claim.")
        update_position(self.db, pid, name="Renamed", description="Changed")
        self.assertEqual(self.revision(pid, "name"), 1)
        self.assertEqual(self.revision(pid, "description"), 1)

    def test_an_ordinary_edit_to_the_same_value_advances_nothing(self):
        pid, _, _ = self.create("Realism is false", "A claim.")
        update_position(self.db, pid, name="Realism is false")
        self.assertEqual(self.revision(pid, "name"), 0,
                         "agreeing with what is stored is not a change")

    def test_the_sync_state_exposes_both_revisions_without_the_values(self):
        pid, _, _ = self.create("Realism is false", "A claim.")
        update_position(self.db, pid, name="Renamed")
        state = self.db.get_position_sync_state(pid)
        self.assertEqual(state, {
            "position_id": pid,
            "fields": {"description": {"revision": 0}, "name": {"revision": 1}},
        }, "revisions only -- the Position detail already carries both values")

    # ---- deletion ---------------------------------------------------------

    def test_deleting_a_position_works(self):
        pid, _, _ = self.create("Realism is false")
        status, result = self.send("DELETE_POSITION", pid, {}, None)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["changed"])
        self.assertIsNone(self.stored(pid))

    def test_a_position_an_argument_targets_is_refused(self):
        pid, _, _ = self.create("Realism is false")
        create_argument(self.db, name="An argument", kind="argument",
                        targets=[{"type": "position", "id": pid,
                                  "verdict_id": "supports"}])
        status, result = self.send("DELETE_POSITION", pid, {}, None)
        self.assertEqual((status, result["code"]), (409, "POSITION_IN_USE"))
        self.assertIsNotNone(self.stored(pid))

    def test_deleting_twice_is_idempotent(self):
        pid, _, _ = self.create("Realism is false")
        self.send("DELETE_POSITION", pid, {}, None)
        status, result = self.send("DELETE_POSITION", pid, {}, None)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])

    def test_deletion_carries_no_base_revision_and_no_payload(self):
        pid, _, _ = self.create("Realism is false")
        status, result = self.send("DELETE_POSITION", pid, {}, 0)
        self.assertEqual((status, result["code"]), (400, "INVALID_BASE_REVISION"))
        status, result = self.send("DELETE_POSITION", pid, {"cascade": True}, None)
        self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))
        self.assertIsNotNone(self.stored(pid))

    def test_the_ordinary_delete_keeps_its_protection(self):
        pid, _, _ = self.create("Realism is false")
        create_argument(self.db, name="An argument", kind="argument",
                        targets=[{"type": "position", "id": pid,
                                  "verdict_id": "supports"}])
        with self.assertRaises(ResearchError) as caught:
            delete_position(self.db, pid)
        self.assertEqual(caught.exception.code, "position_in_use")

    # ---- state ------------------------------------------------------------

    def test_the_sync_state_reports_revisions_only(self):
        pid, _, _ = self.create("Realism is false")
        self.field(pid, "description", "A claim.", 0)
        state = self.db.get_position_sync_state(pid)
        self.assertEqual(sorted(state["fields"]), sorted(positions.FIELDS))
        self.assertEqual(state["fields"]["description"]["revision"], 1)
        self.assertEqual(state["fields"]["name"]["revision"], 0)
        self.assertNotIn("name", state, "the Position detail already carries the values")

    def test_the_sync_state_of_an_unknown_position_is_none(self):
        self.assertIsNone(self.db.get_position_sync_state(entity_ids.generate("P")))

    def test_the_detail_still_returns_what_the_route_serializes(self):
        pid, _, _ = self.create("Realism is false", "A claim.")
        detail = get_position(self.db, pid)
        self.assertEqual(detail["name"], "Realism is false")
        self.assertEqual(detail["description"], "A claim.")


if __name__ == "__main__":
    unittest.main()
