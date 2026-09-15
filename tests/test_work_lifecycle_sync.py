"""DELETE_WORK: destruction of a Work identity."""
import tempfile
import unittest
import uuid

from backend import sync_protocol, work_lifecycle_sync
from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig
from backend.text_index import PRKSTextIndex
from backend.work_deletion import cleanup_after_work_delete, delete_work


class WorkLifecycleSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-work-del-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.device = str(uuid.uuid4())
        self.text_index = PRKSTextIndex(storage=self.db.storage)

    def send(self, work_id, payload=None, base=None, op_id=None):
        return sync_protocol.process_operation(self.db, dict(
            op_id=op_id or str(uuid.uuid4()), device_id=self.device,
            operation="DELETE_WORK", entity_type="work", entity_id=work_id,
            payload={} if payload is None else payload, base_revision=base,
            occurred_at="2026-09-15T10:00:00Z", created_at="2026-09-15T10:00:00Z",
            depends_on=[]))

    def test_registered(self):
        self.assertIn("DELETE_WORK", sync_protocol.supported_operations())

    def test_deleting_a_work_removes_it_and_cascades_links(self):
        work = self.db.add_work(title="Doomed")
        tag = self.db.add_tag("T")["id"]
        self.db.add_tag_to_work(work, tag)
        status, result = self.send(work)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["changed"])
        self.assertEqual(result["work_id"], work)
        self.assertIsNone(self.db.get_work(work))
        self.assertEqual(self.db.get_work_tags(work), [])
        self.assertIsNotNone(self.db.execute_query(
            "SELECT 1 FROM tags WHERE id = ?", (tag,)))

    def test_deleting_a_missing_work_is_convergence(self):
        status, result = self.send("W-missing")
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])

    def test_destruction_carries_no_base_revision(self):
        work = self.db.add_work(title="X")
        status, result = self.send(work, base=0)
        self.assertEqual((status, result["code"]), (400, "INVALID_BASE_REVISION"))
        self.assertIsNotNone(self.db.get_work(work))

    def test_ordinary_delete_shares_the_row_boundary(self):
        work = self.db.add_work(title="Shared")
        out = delete_work(self.db, self.text_index, work)
        self.assertTrue(out.existed)
        self.assertIsNone(self.db.get_work(work))

    def test_cleanup_helper_is_idempotent_after_sync_delete(self):
        work = self.db.add_work(title="Cleanup")
        status, result = self.send(work)
        self.assertTrue(result["changed"])
        cleanup_after_work_delete(
            self.db, self.text_index, work,
            file_path=result.get("file_path") or "",
            managed_pdf_still_referenced=bool(result.get("managed_pdf_still_referenced")),
            existed=True,
        )


if __name__ == "__main__":
    unittest.main()
