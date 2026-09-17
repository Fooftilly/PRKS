"""PDF annotation sync family: CREATE / SET / DELETE + HTTP replace parity."""
from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
import uuid

from backend import pdf_annotation_sync as anns
from backend import sync_protocol
from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig

ROOT = pathlib.Path(__file__).resolve().parents[1]

_HIGHLIGHT = {
    "id": "ann-hi-1",
    "type": 9,
    "pageIndex": 0,
    "contents": "Highlight comment",
    "color": "#FFCD45",
    "strokeColor": "#FFCD45",
    "opacity": 1,
    "blendMode": "Multiply",
    "segmentRects": [
        {"origin": {"x": 72, "y": 700}, "size": {"width": 160, "height": 14}}
    ],
    "rect": {"origin": {"x": 72, "y": 700}, "size": {"width": 160, "height": 14}},
    "custom": {"prksComment": "Highlight comment"},
}


def _ann(overrides=None):
    item = json.loads(json.dumps(_HIGHLIGHT))
    if overrides:
        item.update(overrides)
        if "id" in overrides:
            item["id"] = overrides["id"]
    return item


class PdfAnnotationSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-pdf-ann-sync-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.device = str(uuid.uuid4())
        self.work_id = self.db.add_work(title="Annotated")

    def send(self, operation, work_id, payload, base=None, op_id=None):
        return sync_protocol.process_operation(
            self.db,
            dict(
                op_id=op_id or str(uuid.uuid4()),
                device_id=self.device,
                operation=operation,
                entity_type="work",
                entity_id=work_id,
                payload=payload,
                base_revision=base,
                occurred_at="2026-09-17T10:00:00Z",
                created_at="2026-09-17T10:00:00Z",
                depends_on=[],
            ),
        )

    def create(self, annotation, work_id=None):
        work_id = work_id or self.work_id
        return self.send(
            "CREATE_PDF_ANNOTATION",
            work_id,
            {"annotation_id": annotation["id"], "annotation": annotation},
            None,
        )

    def set_ann(self, annotation, base, work_id=None):
        work_id = work_id or self.work_id
        return self.send(
            "SET_PDF_ANNOTATION",
            work_id,
            {"annotation_id": annotation["id"], "annotation": annotation},
            base,
        )

    def delete(self, annotation_id, base, work_id=None):
        work_id = work_id or self.work_id
        return self.send(
            "DELETE_PDF_ANNOTATION",
            work_id,
            {"annotation_id": annotation_id},
            base,
        )

    def revision(self, annotation_id, work_id=None):
        work_id = work_id or self.work_id
        with self.db.connection() as conn:
            return anns.get_revision(conn, work_id, annotation_id)

    def stored(self, annotation_id):
        rows = self.db.execute_query(
            "SELECT * FROM annotations WHERE id = ?", (annotation_id,)
        )
        return rows[0] if rows else None

    # ---- CREATE -----------------------------------------------------------

    def test_create_stores_under_client_id_at_revision_zero(self):
        status, result = self.create(_ann())
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["changed"])
        self.assertEqual(result["server_revision"], 0)
        self.assertEqual(self.revision("ann-hi-1"), 0)
        self.assertIsNotNone(self.stored("ann-hi-1"))
        self.assertEqual(result["annotation"]["custom"]["prksComment"], "Highlight comment")

    def test_create_idempotent_when_identical(self):
        self.create(_ann())
        status, result = self.create(_ann())
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])
        self.assertEqual(self.revision("ann-hi-1"), 0)

    def test_create_colliding_different_state_is_named(self):
        self.create(_ann())
        other = _ann({"contents": "Different", "custom": {"prksComment": "Different"}})
        status, result = self.create(other)
        self.assertEqual((status, result["code"]), (409, "ANNOTATION_EXISTS"))

    def test_create_rejects_non_null_base(self):
        status, result = self.send(
            "CREATE_PDF_ANNOTATION",
            self.work_id,
            {"annotation_id": "ann-hi-1", "annotation": _ann()},
            0,
        )
        self.assertEqual((status, result["code"]), (400, "INVALID_BASE_REVISION"))

    def test_create_missing_work(self):
        status, result = self.create(_ann(), work_id="missing-work")
        self.assertEqual((status, result["code"]), (404, "ENTITY_NOT_FOUND"))

    def test_create_cross_work_id_conflict(self):
        other = self.db.add_work(title="Other")
        self.create(_ann(), work_id=other)
        status, result = self.create(_ann())
        self.assertEqual((status, result["code"]), (409, "ANNOTATION_ID_CONFLICT"))

    # ---- SET --------------------------------------------------------------

    def test_set_advances_revision_on_change(self):
        self.create(_ann())
        edited = _ann({"contents": "Edited", "custom": {"prksComment": "Edited"}})
        status, result = self.set_ann(edited, 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["changed"])
        self.assertEqual(result["server_revision"], 1)
        self.assertEqual(self.revision("ann-hi-1"), 1)

    def test_set_identical_is_ack_without_advance(self):
        self.create(_ann())
        status, result = self.set_ann(_ann(), 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])
        self.assertEqual(self.revision("ann-hi-1"), 0)

    def test_set_stale_identical_converges(self):
        self.create(_ann())
        edited = _ann({"contents": "Edited", "custom": {"prksComment": "Edited"}})
        self.set_ann(edited, 0)
        # Stale base but same as current server state → convergent ACK
        status, result = self.set_ann(edited, 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])
        self.assertEqual(result["server_revision"], 1)

    def test_set_stale_identical_can_ack_far_above_base_plus_one(self):
        """Convergent ACK returns the real server revision, not base+1."""
        self.create(_ann())
        body = _ann()
        # Advance server revision without changing semantic body meaning for
        # a later identical stale write: mutate then mutate back.
        self.set_ann(
            _ann({"contents": "Mid", "custom": {"prksComment": "Mid"}}), 0
        )
        self.set_ann(body, 1)
        self.set_ann(
            _ann({"contents": "Mid2", "custom": {"prksComment": "Mid2"}}), 2
        )
        self.set_ann(body, 3)
        self.assertEqual(self.revision("ann-hi-1"), 4)
        # Stale identical from base 2 → ACK at server_revision 4 (not 3).
        status, result = self.set_ann(body, 2)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])
        self.assertEqual(result["server_revision"], 4)

    def test_coherent_snapshot_is_one_transaction(self):
        self.create(_ann())
        snap = self.db.get_work_annotations_snapshot(self.work_id)
        self.assertIsNotNone(snap)
        self.assertEqual(snap["work_id"], self.work_id)
        self.assertEqual(len(snap["items"]), 1)
        self.assertEqual(snap["items"][0]["id"], "ann-hi-1")
        self.assertEqual(len(snap["annotations"]), 1)
        self.assertEqual(snap["annotations"][0]["revision"], 0)
        self.assertIn("canonical_annotation_set_revision", snap)
        self.assertIn("materialized_pdf_annotation_revision", snap)
        self.assertGreaterEqual(snap["canonical_annotation_set_revision"], 1)
        state = self.db.get_work_annotations_state(self.work_id)
        self.assertEqual(state["annotations"], snap["annotations"])
        self.assertEqual(state["known_absent"], snap["known_absent"])

    def test_set_stale_different_conflicts(self):
        self.create(_ann())
        self.set_ann(
            _ann({"contents": "Server", "custom": {"prksComment": "Server"}}), 0
        )
        status, result = self.set_ann(
            _ann({"contents": "Client", "custom": {"prksComment": "Client"}}), 0
        )
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(result["current_revision"], 1)
        self.assertIn("current_annotation", result)

    def test_set_future_revision(self):
        self.create(_ann())
        status, result = self.set_ann(_ann(), 5)
        self.assertEqual((status, result["code"]), (400, "FUTURE_REVISION"))

    def test_set_requires_base(self):
        self.create(_ann())
        status, result = self.send(
            "SET_PDF_ANNOTATION",
            self.work_id,
            {"annotation_id": "ann-hi-1", "annotation": _ann()},
            None,
        )
        self.assertEqual((status, result["code"]), (400, "INVALID_BASE_REVISION"))

    def test_set_missing_annotation(self):
        status, result = self.set_ann(_ann(), 0)
        self.assertEqual((status, result["code"]), (404, "ENTITY_NOT_FOUND"))

    # ---- DELETE -----------------------------------------------------------

    def test_delete_advances_and_removes_row(self):
        self.create(_ann())
        status, result = self.delete("ann-hi-1", 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["changed"])
        self.assertEqual(result["server_revision"], 1)
        self.assertIsNone(self.stored("ann-hi-1"))
        self.assertEqual(self.revision("ann-hi-1"), 1)

    def test_delete_already_absent_converges(self):
        self.create(_ann())
        self.delete("ann-hi-1", 0)
        status, result = self.delete("ann-hi-1", 1)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])

    def test_delete_stale_while_present_conflicts(self):
        self.create(_ann())
        self.set_ann(
            _ann({"contents": "Moved", "custom": {"prksComment": "Moved"}}), 0
        )
        status, result = self.delete("ann-hi-1", 0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))

    def test_create_after_delete_refuses_reuse(self):
        self.create(_ann())
        self.delete("ann-hi-1", 0)
        status, result = self.create(_ann())
        self.assertEqual((status, result["code"]), (409, "ANNOTATION_ID_REUSED"))
        self.assertEqual(result["current_revision"], 1)

    # ---- Ledger replay ----------------------------------------------------

    def test_exact_replay_preserves_original_result(self):
        op_id = str(uuid.uuid4())
        status1, result1 = self.send(
            "CREATE_PDF_ANNOTATION",
            self.work_id,
            {"annotation_id": "ann-hi-1", "annotation": _ann()},
            None,
            op_id=op_id,
        )
        status2, result2 = self.send(
            "CREATE_PDF_ANNOTATION",
            self.work_id,
            {"annotation_id": "ann-hi-1", "annotation": _ann()},
            None,
            op_id=op_id,
        )
        self.assertEqual(status1, status2)
        self.assertEqual(result1["code"], result2["code"])
        self.assertTrue(result1["changed"])
        # Replay returns the ledgered result (changed=True from first apply).
        self.assertEqual(result2.get("changed"), result1.get("changed"))

    # ---- Direct HTTP replace advances same revisions ----------------------

    def test_full_list_replace_advances_revisions_like_sync(self):
        self.db.save_work_annotations(self.work_id, json.dumps([_ann()]))
        self.assertEqual(self.revision("ann-hi-1"), 0)
        edited = _ann({"contents": "Via HTTP", "custom": {"prksComment": "Via HTTP"}})
        self.db.save_work_annotations(self.work_id, json.dumps([edited]))
        self.assertEqual(self.revision("ann-hi-1"), 1)
        self.db.save_work_annotations(self.work_id, json.dumps([]))
        self.assertEqual(self.revision("ann-hi-1"), 2)
        self.assertIsNone(self.stored("ann-hi-1"))
        state = self.db.get_work_annotations_state(self.work_id)
        self.assertEqual(state["known_absent"].get("ann-hi-1"), 2)

    def test_identical_full_list_replace_does_not_advance(self):
        self.db.save_work_annotations(self.work_id, json.dumps([_ann()]))
        self.db.save_work_annotations(self.work_id, json.dumps([_ann()]))
        self.assertEqual(self.revision("ann-hi-1"), 0)

    def test_annotations_state_lists_present(self):
        self.create(_ann())
        state = self.db.get_work_annotations_state(self.work_id)
        self.assertEqual(state["work_id"], self.work_id)
        self.assertEqual(
            state["annotations"],
            [{"annotation_id": "ann-hi-1", "revision": 0}],
        )


if __name__ == "__main__":
    unittest.main()
