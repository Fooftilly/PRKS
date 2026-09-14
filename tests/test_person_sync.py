"""CREATE_PERSON: client-generated identity and the first dependent role link."""
import tempfile
import unittest
import uuid

from backend import entity_ids, person_sync, sync_protocol
from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig


def person_payload(**overrides):
    body = {name: "" for name in person_sync.FIELDS}
    body["first_name"] = "Ada"
    body["last_name"] = "Lovelace"
    body.update(overrides)
    return body


class PersonSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-person-sync-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.work = self.db.add_work("Person sync work")
        self.device = str(uuid.uuid4())

    def create_envelope(self, person_id=None, **payload):
        return dict(
            op_id=str(uuid.uuid4()),
            device_id=self.device,
            operation="CREATE_PERSON",
            entity_type="person",
            entity_id=person_id or entity_ids.generate("P"),
            payload=person_payload(**payload),
            base_revision=None,
            occurred_at="2026-09-14T10:00:00Z",
            created_at="2026-09-14T10:00:00Z",
            depends_on=[],
        )

    def role_envelope(self, person_id, depends_on):
        return dict(
            op_id=str(uuid.uuid4()),
            device_id=self.device,
            operation="ADD_WORK_PERSON_ROLE",
            entity_type="work",
            entity_id=self.work,
            payload={"person_id": person_id, "role_type": "Author", "credit_name": ""},
            base_revision=0,
            occurred_at="2026-09-14T10:00:01Z",
            created_at="2026-09-14T10:00:01Z",
            depends_on=list(depends_on),
        )

    def test_create_stores_the_client_id(self):
        person_id = entity_ids.generate("P")
        status, result = sync_protocol.process_operation(
            self.db, self.create_envelope(person_id))
        self.assertEqual(status, 200)
        self.assertEqual(result["code"], "ACKNOWLEDGED")
        self.assertEqual(result["person_id"], person_id)
        self.assertTrue(result["changed"])
        stored = self.db.get_person(person_id)
        self.assertEqual(stored["first_name"], "Ada")
        self.assertEqual(stored["last_name"], "Lovelace")
        self.assertEqual(result["person"]["id"], person_id)
        self.assertEqual(result["person"]["assigned_roles"], [])
        self.assertEqual(result["person"]["groups"], [])

    def test_legacy_eight_hex_id_is_refused_for_creation(self):
        envelope = self.create_envelope("P-A1B2C3D4")
        self.assertEqual(sync_protocol.process_operation(self.db, envelope),
                         (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual(self.db.execute_query("SELECT * FROM persons"), [])

    def test_base_revision_is_construction(self):
        envelope = self.create_envelope()
        envelope["base_revision"] = 0
        self.assertEqual(sync_protocol.process_operation(self.db, envelope),
                         (400, {"code": "INVALID_BASE_REVISION"}))

    def test_replay_is_exact(self):
        envelope = self.create_envelope()
        first = sync_protocol.process_operation(self.db, envelope)
        self.assertEqual(sync_protocol.process_operation(self.db, envelope), first)
        self.assertEqual(len(self.db.execute_query("SELECT id FROM persons")), 1)

    def test_second_envelope_for_an_existing_id_does_not_overwrite(self):
        person_id = entity_ids.generate("P")
        first = self.create_envelope(person_id, about="original")
        sync_protocol.process_operation(self.db, first)
        second = self.create_envelope(person_id, about="changed")
        status, result = sync_protocol.process_operation(self.db, second)
        self.assertEqual(status, 200)
        self.assertFalse(result["changed"])
        self.assertEqual(self.db.get_person(person_id)["about"], "original")

    def test_ordinary_add_person_still_mints_a_distributed_id(self):
        person_id = self.db.add_person("Online", "Person")
        self.assertTrue(entity_ids.is_distributed(person_id, "P"))

    def test_role_before_create_is_unsatisfied(self):
        person_id = entity_ids.generate("P")
        create = self.create_envelope(person_id)
        role = self.role_envelope(person_id, [create["op_id"]])
        self.assertEqual(sync_protocol.process_operation(self.db, role),
                         (400, {"code": "UNSATISFIED_DEPENDENCY"}))
        self.assertIsNone(self.db.get_person(person_id))
        self.assertEqual(self.db.get_work_roles(self.work), [])

    def test_create_then_dependent_role(self):
        person_id = entity_ids.generate("P")
        create = self.create_envelope(person_id)
        self.assertEqual(sync_protocol.process_operation(self.db, create)[1]["code"],
                         "ACKNOWLEDGED")
        role = self.role_envelope(person_id, [create["op_id"]])
        status, result = sync_protocol.process_operation(self.db, role)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["present"])
        roles = self.db.get_work_roles(self.work)
        self.assertEqual([(r["id"], r["role_type"]) for r in roles],
                         [(person_id, "Author")])
