"""PDF annotation materialization lag (Slice F)."""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
import uuid

from backend import pdf_annotation_sync, pdf_materialization
from backend.db_manager import PRKSDatabase
from backend.db_migrations import LATEST_SCHEMA_VERSION
from backend.storage.config import StorageConfig

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _highlight(ann_id: str, comment: str) -> dict:
    return {
        "id": ann_id,
        "type": 9,
        "pageIndex": 0,
        "contents": comment,
        "color": "#FFCD45",
        "strokeColor": "#FFCD45",
        "opacity": 1,
        "blendMode": "Multiply",
        "rect": {"origin": {"x": 72, "y": 700}, "size": {"width": 160, "height": 14}},
        "segmentRects": [
            {"origin": {"x": 72, "y": 700}, "size": {"width": 160, "height": 14}}
        ],
        "custom": {"prksComment": comment},
    }


class PdfMaterializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-pdf-mat-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.work_id = self.db.add_work(title="Mat Work")

    def test_schema_has_materialization_columns(self) -> None:
        self.assertEqual(LATEST_SCHEMA_VERSION, 15)
        row = self.db.execute_query(
            """
            SELECT canonical_annotation_set_revision, materialized_pdf_annotation_revision
            FROM works WHERE id = ?
            """,
            (self.work_id,),
        )[0]
        self.assertEqual(row["canonical_annotation_set_revision"], 0)
        self.assertEqual(row["materialized_pdf_annotation_revision"], 0)

    def test_create_bumps_canonical_not_materialized(self) -> None:
        op = {
            "entity_id": self.work_id,
            "base_revision": None,
            "payload": {
                "annotation_id": "ann-1",
                "annotation": _highlight("ann-1", "Hi"),
            },
        }
        with self.db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            status, result = pdf_annotation_sync.apply_create(self.db, conn, op, None)
            conn.commit()
        self.assertEqual(status, 200)
        self.assertTrue(result["changed"])
        mat = self.db.get_work_pdf_materialization(self.work_id)
        self.assertEqual(mat["canonical_annotation_set_revision"], 1)
        self.assertEqual(mat["materialized_pdf_annotation_revision"], 0)
        self.assertTrue(mat["stale"])
        self.assertEqual(mat["code"], pdf_materialization.STALE_CODE)

    def test_mark_materialized_clears_stale(self) -> None:
        self.db.sync_work_annotations(self.work_id, [_highlight("a", "A")])
        mat = self.db.get_work_pdf_materialization(self.work_id)
        self.assertTrue(mat["stale"])
        rev = self.db.mark_work_pdf_materialized(self.work_id)
        self.assertEqual(rev, mat["canonical_annotation_set_revision"])
        mat2 = self.db.get_work_pdf_materialization(self.work_id)
        self.assertFalse(mat2["stale"])
        self.assertIsNone(mat2["code"])

    def test_claimed_future_revision_rejected(self) -> None:
        self.db.sync_work_annotations(self.work_id, [_highlight("a", "A")])
        mat = self.db.get_work_pdf_materialization(self.work_id)
        future = mat["canonical_annotation_set_revision"] + 5
        with self.assertRaises(ValueError) as ctx:
            self.db.mark_work_pdf_materialized(self.work_id, at_revision=future)
        self.assertEqual(str(ctx.exception), pdf_materialization.STALE_CODE)
        mat2 = self.db.get_work_pdf_materialization(self.work_id)
        self.assertEqual(mat2["materialized_pdf_annotation_revision"], 0)
        self.assertTrue(mat2["stale"])

    def test_claimed_stale_revision_rejected(self) -> None:
        self.db.sync_work_annotations(self.work_id, [_highlight("a", "A")])
        # Advance canonical again so claim of 1 is behind.
        self.db.sync_work_annotations(self.work_id, [_highlight("a", "A2")])
        mat = self.db.get_work_pdf_materialization(self.work_id)
        self.assertGreaterEqual(mat["canonical_annotation_set_revision"], 2)
        with self.assertRaises(ValueError) as ctx:
            self.db.accept_work_pdf_materialization_claim(self.work_id, 1)
        self.assertEqual(str(ctx.exception), pdf_materialization.STALE_CODE)

    def test_exact_claim_marks_materialized(self) -> None:
        self.db.sync_work_annotations(self.work_id, [_highlight("a", "A")])
        mat = self.db.get_work_pdf_materialization(self.work_id)
        claim = mat["canonical_annotation_set_revision"]
        rev = self.db.mark_work_pdf_materialized_if_claim_current(self.work_id, claim)
        self.assertEqual(rev, claim)
        mat2 = self.db.get_work_pdf_materialization(self.work_id)
        self.assertFalse(mat2["stale"])

    def test_identical_set_does_not_bump_canonical(self) -> None:
        items = [_highlight("a", "A")]
        self.db.sync_work_annotations(self.work_id, items)
        after_create = self.db.get_work_pdf_materialization(self.work_id)[
            "canonical_annotation_set_revision"
        ]
        self.db.sync_work_annotations(self.work_id, items)
        after_noop = self.db.get_work_pdf_materialization(self.work_id)[
            "canonical_annotation_set_revision"
        ]
        self.assertEqual(after_create, after_noop)

    def test_require_current_raises_stale(self) -> None:
        self.db.sync_work_annotations(self.work_id, [_highlight("a", "A")])
        with self.db.connection() as conn:
            conn.execute("BEGIN")
            with self.assertRaises(ValueError) as ctx:
                pdf_materialization.require_current_materialization_on_conn(
                    conn, self.work_id
                )
            self.assertEqual(str(ctx.exception), pdf_materialization.STALE_CODE)


if __name__ == "__main__":
    unittest.main()
