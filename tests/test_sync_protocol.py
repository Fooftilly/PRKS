"""The generic operation protocol: envelope, hashing, dispatch, isolation."""
import tempfile
import unittest
import uuid

from backend import person_sync, sync_protocol, work_metadata_sync, work_open_sync, work_tag_sync
from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig


class SyncProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-protocol-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.work = self.db.add_work("Protocol work")
        self.tag = self.db.add_tag("Protocol tag")["id"]

    def envelope(self, operation, payload, base_revision):
        return dict(op_id=str(uuid.uuid4()), device_id=str(uuid.uuid4()), operation=operation,
                    entity_type="work", entity_id=self.work, payload=payload,
                    base_revision=base_revision, occurred_at="2026-09-11T10:00:00Z",
                    created_at="2026-09-11T10:00:00Z", depends_on=[])

    def test_registered_families(self):
        self.assertEqual(sorted(sync_protocol.supported_operations()),
                         [
                          "ADD_PERSON_GROUP_MEMBER", "ADD_WORK_PERSON_ROLE", "ADD_WORK_TAG",
                          "CREATE_ARGUMENT", "CREATE_CONCEPT", "CREATE_FOLDER",
                          "CREATE_PERSON", "CREATE_PERSON_GROUP", "CREATE_PLAYLIST",
                          "CREATE_POSITION", "CREATE_TAG", "DELETE_ARGUMENT",
                          "DELETE_CONCEPT", "DELETE_FOLDER",
                          "DELETE_PERSON", "DELETE_PERSON_GROUP", "DELETE_PLAYLIST",
                          "DELETE_POSITION", "DELETE_TAG", "MARK_WORK_OPENED",
                          "REMOVE_PERSON_GROUP_MEMBER", "REMOVE_WORK_PERSON_ROLE", "REMOVE_WORK_TAG",
                          "REORDER_PLAYLIST_ITEMS", "SET_ARGUMENT_FIELD",
                          "SET_ARGUMENT_SOURCES", "SET_ARGUMENT_TARGETS",
                          "SET_CONCEPT_FIELD", "SET_CONCEPT_IDENTITY",
                          "SET_CONCEPT_PARENTS", "SET_FOLDER_FIELD", "SET_PERSON_GROUP_FIELD",
                          "SET_PERSON_METADATA_FIELD", "SET_PLAYLIST_FIELD", "SET_POSITION_FIELD",
                          "SET_WORK_FOLDER", "SET_WORK_METADATA_FIELD", "SET_WORK_PERSON_ROLE_CREDIT",
                          "SET_WORK_PLAYLIST", "SET_WORK_SOURCE"
                         ])

    def test_unregistered_operation_is_refused_and_unledgered(self):
        """A family the server does not implement must not reach a handler, and
        must not burn an op_id in the ledger."""
        for operation in ("MOVE_WORK", "UPDATE_CONCEPT", "", "mark_work_opened"):
            envelope = self.envelope(operation, {}, None)
            self.assertEqual(sync_protocol.process_operation(self.db, envelope),
                             (400, {"code": "INVALID_ENVELOPE"}), operation)
        self.assertEqual(self.db.execute_query("SELECT * FROM sync_operations"), [])

    def test_each_family_answers_only_its_own_shape(self):
        """Handler isolation: a Work-Tag acknowledgement never carries Recent
        state, and an open event never carries Tag lifecycle state."""
        tag_ack = sync_protocol.process_operation(
            self.db, self.envelope("ADD_WORK_TAG", {"tag_id": self.tag}, 0))[1]
        open_ack = sync_protocol.process_operation(
            self.db, self.envelope("MARK_WORK_OPENED", {}, None))[1]
        self.assertEqual(set(tag_ack) & {"recent_item", "effective_opened_at"}, set())
        self.assertEqual(set(open_ack) & {"tag", "tag_id", "server_revision", "present"}, set())
        self.assertIn("tag", tag_ack)
        self.assertIn("recent_item", open_ack)

    def test_base_revision_expectations_are_per_family(self):
        """The generic layer accepts both spellings; each family decides which
        one its own semantics can honor."""
        self.assertEqual(sync_protocol.process_operation(
            self.db, self.envelope("ADD_WORK_TAG", {"tag_id": self.tag}, None)),
            (400, {"code": "INVALID_BASE_REVISION"}))
        self.assertEqual(sync_protocol.process_operation(
            self.db, self.envelope("MARK_WORK_OPENED", {}, 0)),
            (400, {"code": "INVALID_BASE_REVISION"}))
        self.assertEqual(self.db.execute_query("SELECT * FROM sync_operations"), [])

    def test_normalization_makes_equivalent_spellings_one_operation(self):
        """The request hash is taken over the normalized envelope, so a retry
        that spells the same operation differently must not read as a new one."""
        envelope = self.envelope("MARK_WORK_OPENED", {}, None)
        first = sync_protocol.process_operation(self.db, envelope)
        variant = dict(reversed(list(envelope.items())))
        variant["op_id"] = envelope["op_id"].upper()
        variant["device_id"] = envelope["device_id"].upper()
        variant["occurred_at"] = "2026-09-11T12:00:00+02:00"
        variant["created_at"] = "2026-09-11T10:00:00+00:00"
        self.assertEqual(sync_protocol.process_operation(self.db, variant), first)
        self.assertEqual(len(self.db.execute_query("SELECT * FROM sync_operations")), 1)

    def test_registration_refuses_a_second_owner(self):
        with self.assertRaises(RuntimeError):
            sync_protocol.register("ADD_WORK_TAG", work_open_sync.HANDLER)

    def test_wrong_entity_type_is_an_envelope_error(self):
        envelope = self.envelope("MARK_WORK_OPENED", {}, None)
        envelope["entity_type"] = "person"
        self.assertEqual(sync_protocol.process_operation(self.db, envelope),
                         (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual(self.db.execute_query("SELECT * FROM sync_operations"), [])

    def test_unsatisfied_dependency_is_not_ledgered(self):
        envelope = self.envelope("MARK_WORK_OPENED", {}, None)
        envelope["depends_on"] = [str(uuid.uuid4())]
        self.assertEqual(sync_protocol.process_operation(self.db, envelope),
                         (400, {"code": "UNSATISFIED_DEPENDENCY"}))
        self.assertEqual(self.db.execute_query("SELECT * FROM sync_operations"), [])

    def test_self_dependency_is_an_envelope_error(self):
        envelope = self.envelope("MARK_WORK_OPENED", {}, None)
        envelope["depends_on"] = [envelope["op_id"]]
        self.assertEqual(sync_protocol.process_operation(self.db, envelope),
                         (400, {"code": "INVALID_ENVELOPE"}))

    def test_acknowledged_same_device_dependency_allows_apply(self):
        first = self.envelope("MARK_WORK_OPENED", {}, None)
        self.assertEqual(sync_protocol.process_operation(self.db, first)[1]["code"], "ACKNOWLEDGED")
        second = self.envelope("ADD_WORK_TAG", {"tag_id": self.tag}, 0)
        second["device_id"] = first["device_id"]
        second["depends_on"] = [first["op_id"]]
        status, result = sync_protocol.process_operation(self.db, second)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(len(self.db.execute_query("SELECT * FROM sync_operations")), 2)

    def test_other_device_dependency_is_unsatisfied(self):
        first = self.envelope("MARK_WORK_OPENED", {}, None)
        sync_protocol.process_operation(self.db, first)
        second = self.envelope("ADD_WORK_TAG", {"tag_id": self.tag}, 0)
        second["depends_on"] = [first["op_id"]]
        self.assertEqual(sync_protocol.process_operation(self.db, second),
                         (400, {"code": "UNSATISFIED_DEPENDENCY"}))
        self.assertEqual(len(self.db.execute_query("SELECT * FROM sync_operations")), 1)

    def test_domain_modules_do_not_reimplement_the_protocol(self):
        """Ledger, hashing and dispatch belong to exactly one module. A family
        that grew its own copy would drift from the others silently."""
        for module in (work_tag_sync, work_open_sync, work_metadata_sync, person_sync):
            for name in ("process_operation", "insert_result", "normalize_envelope", "request_hash"):
                self.assertFalse(hasattr(module, name), module.__name__ + "." + name)
