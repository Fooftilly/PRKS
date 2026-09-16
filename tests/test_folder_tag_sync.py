"""Folder-Tag durable sync: shared write boundary, revisions, lifecycle."""
import tempfile
import unittest
import uuid

from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig
from backend import folder_tag_sync as sync
from backend import sync_protocol


class FolderTagSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-folder-tag-sync-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.folder = self.db.add_folder("Sync folder")
        if isinstance(self.folder, dict):
            self.folder = self.folder["id"]
        self.tag = self.db.add_tag("Sync tag")["id"]

    def op(self, present=True, base=0, **changes):
        return dict(
            op_id=str(uuid.uuid4()), device_id=str(uuid.uuid4()),
            operation="ADD_FOLDER_TAG" if present else "REMOVE_FOLDER_TAG",
            entity_type="folder", entity_id=self.folder, payload={"tag_id": self.tag},
            base_revision=base, occurred_at="2026-09-11T00:00:00Z",
            created_at="2026-09-11T00:00:00Z", depends_on=[], **changes,
        )

    def revision(self, tag=None, folder=None):
        with self.db.connection() as conn:
            return sync.get_revision(conn, folder or self.folder, tag or self.tag)

    def test_direct_add_remove_advance_revision_once(self):
        self.db.add_tag_to_folder(self.folder, self.tag)
        self.db.add_tag_to_folder(self.folder, self.tag)
        self.assertEqual(self.revision(), 1)
        self.db.remove_tag_from_folder(self.folder, self.tag)
        self.db.remove_tag_from_folder(self.folder, self.tag)
        self.assertEqual(self.revision(), 2)

    def test_sync_ack_and_conflict_matrix(self):
        status, result = sync_protocol.process_operation(self.db, self.op(present=True, base=0))
        self.assertEqual(status, 200)
        self.assertEqual(result["code"], "ACKNOWLEDGED")
        self.assertTrue(result["present"])
        self.assertEqual(result["server_revision"], 1)
        self.assertEqual(self.revision(), 1)

        status, result = sync_protocol.process_operation(self.db, self.op(present=False, base=0))
        self.assertEqual(status, 409)
        self.assertEqual(result["code"], "REVISION_CONFLICT")
        self.assertEqual(result["current_revision"], 1)
        self.assertTrue(result["current_state"])

        status, result = sync_protocol.process_operation(self.db, self.op(present=False, base=1))
        self.assertEqual(status, 200)
        self.assertEqual(result["code"], "ACKNOWLEDGED")
        self.assertFalse(result["present"])
        self.assertEqual(self.revision(), 2)

    def test_null_base_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            sync.validate(self.op(base=None))
        self.assertEqual(str(ctx.exception), "INVALID_BASE_REVISION")

    def test_merge_and_delete_advance_folder_tag_revisions(self):
        other = self.db.add_tag("Target")["id"]
        self.db.add_tag_to_folder(self.folder, self.tag)
        self.assertEqual(self.revision(), 1)
        self.db.merge_tags_into(self.tag, other)
        self.assertEqual(self.revision(tag=self.tag), 2)
        self.assertEqual(self.revision(tag=other), 1)
        tags = {t["id"] for t in self.db.get_folder_tags(self.folder)}
        self.assertEqual(tags, {other})

        self.db.delete_tag(other)
        self.assertEqual(self.revision(tag=other), 2)
        self.assertEqual(self.db.get_folder_tags(self.folder), [])

    def test_tag_options_shape_and_tombstone(self):
        self.db.add_tag_to_folder(self.folder, self.tag)
        self.db.remove_tag_from_folder(self.folder, self.tag)
        options = self.db.get_folder_tag_options(self.folder)
        self.assertEqual(options["folder_id"], self.folder)
        self.assertEqual(options["assigned"], [])
        self.assertEqual(options["known_absent"][self.tag], 2)

    def test_entity_type_is_folder(self):
        self.assertIn("ADD_FOLDER_TAG", sync_protocol.supported_operations())
        self.assertIn("REMOVE_FOLDER_TAG", sync_protocol.supported_operations())
        bad = self.op()
        bad["entity_type"] = "work"
        with self.assertRaises(ValueError) as ctx:
            sync_protocol.normalize_envelope(bad)
        self.assertEqual(str(ctx.exception), "INVALID_ENVELOPE")


if __name__ == "__main__":
    unittest.main()
