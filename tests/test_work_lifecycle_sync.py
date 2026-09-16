"""DELETE_WORK: destruction of a Work identity."""
import json
import os
import tempfile
import unittest
import uuid

from backend import sync_protocol
from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig
from backend.text_index import PRKSTextIndex
from backend.work_deletion import cleanup_after_work_delete, delete_work


class WorkLifecycleSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-work-del-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        os.makedirs(self.db.storage.pdfs_dir, exist_ok=True)
        os.makedirs(self.db.storage.thumbs_dir, exist_ok=True)
        self.device = str(uuid.uuid4())
        self.text_index = PRKSTextIndex(storage=self.db.storage)

    def send(self, work_id, payload=None, base=None, op_id=None):
        return sync_protocol.process_operation(self.db, dict(
            op_id=op_id or str(uuid.uuid4()), device_id=self.device,
            operation="DELETE_WORK", entity_type="work", entity_id=work_id,
            payload={} if payload is None else payload, base_revision=base,
            occurred_at="2026-09-15T10:00:00Z", created_at="2026-09-15T10:00:00Z",
            depends_on=[]))

    def _write_managed_pdf(self, filename="shared.pdf"):
        pdf_abs = os.path.join(self.db.storage.pdfs_dir, filename)
        with open(pdf_abs, "wb") as f:
            f.write(b"%PDF-1.4\n%DEL\n%%EOF\n")
        return pdf_abs, f"/api/pdfs/{filename}"

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

    def test_replay_cleanup_preserves_pdf_now_referenced_by_another_work(self):
        """Exact op_id replay must not delete a PDF another Work now shares.

        Mirrors the post-commit window: ACK commits the row delete, cleanup is
        deferred/retried, another Work begins referencing the same managed
        file, then the identical op_id is replayed. Cleanup must re-check live
        references — never trust a deletion-time still-referenced=false.
        """
        pdf_abs, api_path = self._write_managed_pdf("replay-share.pdf")
        work_a = self.db.add_work(title="First", file_path=api_path)
        op_id = str(uuid.uuid4())

        status1, result1 = self.send(work_a, op_id=op_id)
        self.assertEqual((status1, result1["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result1["changed"])
        self.assertEqual(result1.get("file_path"), api_path)
        self.assertNotIn("managed_pdf_still_referenced", result1)
        self.assertIsNone(self.db.get_work(work_a))
        self.assertTrue(os.path.isfile(pdf_abs))

        work_b = self.db.add_work(title="Second", file_path=api_path)
        self.assertIsNotNone(self.db.get_work(work_b))

        status2, result2 = self.send(work_a, op_id=op_id)
        self.assertEqual(status2, 200)
        self.assertEqual(result2, {
            "code": "ACKNOWLEDGED",
            "work_id": work_a,
            "changed": True,
        })
        self.assertNotIn("file_path", result2)

        # Server-shaped cleanup on the replayed ACK (no ephemeral path).
        cleanup_after_work_delete(
            self.db, self.text_index, work_a,
            file_path=result2.get("file_path") or "",
            existed=True,
        )
        self.assertTrue(os.path.isfile(pdf_abs))

        # Even if a retry reuses the first-ACK path with a stale
        # still-referenced=false, live DB refs must keep the file.
        cleanup_after_work_delete(
            self.db, self.text_index, work_a,
            file_path=api_path,
            managed_pdf_still_referenced=False,
            existed=True,
        )
        self.assertTrue(os.path.isfile(pdf_abs))
        self.assertIsNotNone(self.db.get_work(work_b))

    def test_delete_work_ledger_omits_file_path(self):
        """Immortal sync_operations must not retain managed filenames/paths."""
        pdf_abs, api_path = self._write_managed_pdf("ledger-privacy.pdf")
        work = self.db.add_work(title="PrivatePath", file_path=api_path)
        op_id = str(uuid.uuid4())
        status, result = self.send(work, op_id=op_id)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(result.get("file_path"), api_path)

        rows = self.db.execute_query(
            "SELECT result_json FROM sync_operations WHERE op_id = ?",
            (op_id,),
        )
        self.assertEqual(len(rows), 1)
        stored = rows[0]["result_json"]
        parsed = json.loads(stored)
        self.assertEqual(parsed, {
            "code": "ACKNOWLEDGED",
            "work_id": work,
            "changed": True,
        })
        self.assertNotIn("file_path", stored)
        self.assertNotIn("ledger-privacy.pdf", stored)
        self.assertNotIn("/api/pdfs/", stored)
        self.assertTrue(os.path.isfile(pdf_abs))


if __name__ == "__main__":
    unittest.main()
