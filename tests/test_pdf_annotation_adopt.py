"""Slice G: adopt byte-only PDF user markup into canonical metadata.

Four fixture cases:
1. Metadata-only — already in annotations; adopt must not delete it.
2. Byte-only user markup — in viewer list, missing from DB → adopted.
3. Both present (same id) — skipped_existing; no duplicate row.
4. Non-user Link/widget — never adopted into annotations.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from backend import pdf_annotation_adopt, pdf_materialization
from backend.db_manager import PRKSDatabase
from backend.pdf_annotations import WorkAnnotationError
from backend.storage.config import StorageConfig

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _highlight(ann_id: str, comment: str, *, x: float = 72.0) -> dict:
    return {
        "id": ann_id,
        "type": 9,
        "pageIndex": 0,
        "contents": comment,
        "color": "#FFCD45",
        "strokeColor": "#FFCD45",
        "opacity": 1,
        "blendMode": "Multiply",
        "rect": {"origin": {"x": x, "y": 700}, "size": {"width": 160, "height": 14}},
        "segmentRects": [
            {"origin": {"x": x, "y": 700}, "size": {"width": 160, "height": 14}}
        ],
        "custom": {"prksComment": comment},
    }


def _link(ann_id: str) -> dict:
    return {
        "id": ann_id,
        "type": 2,
        "pageIndex": 0,
        "uri": "https://example.test/doc",
        "rect": {"origin": {"x": 10, "y": 10}, "size": {"width": 40, "height": 12}},
    }


class PdfAnnotationAdoptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-pdf-adopt-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.work_id = self.db.add_work(title="Adopt Work")

    def _ids(self) -> set[str]:
        rows = self.db.execute_query(
            "SELECT id FROM annotations WHERE work_id = ?",
            (self.work_id,),
        )
        return {row["id"] for row in rows}

    def test_four_cases_metadata_byte_both_and_link(self) -> None:
        meta_only = _highlight("ann-meta", "Meta only", x=10)
        both = _highlight("ann-both", "Both sides", x=200)
        byte_only = _highlight("ann-byte", "Byte only", x=400)
        link = _link("ann-link")

        # Case 1 seed: metadata-only + already-synced "both".
        self.db.sync_work_annotations(self.work_id, [meta_only, both])
        # Adoption requires current materialization (canonical == materialized).
        self.db.mark_work_pdf_materialized(self.work_id)
        before_ids = self._ids()
        self.assertEqual(before_ids, {"ann-meta", "ann-both"})

        # Viewer discovers: both, byte-only, and a Link (not meta-only).
        result = self.db.adopt_byte_only_user_markup(
            self.work_id,
            [both, byte_only, link, {"not": "an annotation"}],
        )

        after_ids = self._ids()
        # Case 1: metadata-only survives (adopt never deletes).
        self.assertIn("ann-meta", after_ids)
        # Case 2: byte-only adopted.
        self.assertIn("ann-byte", after_ids)
        self.assertIn("ann-byte", result["adopted"])
        self.assertEqual(result["adopted_count"], 1)
        # Case 3: both already present → skipped, not duplicated.
        self.assertEqual(
            len(
                self.db.execute_query(
                    "SELECT id FROM annotations WHERE work_id = ? AND id = ?",
                    (self.work_id, "ann-both"),
                )
            ),
            1,
        )
        self.assertGreaterEqual(result["skipped_existing"], 1)
        # Case 4: Link never enters annotations.
        self.assertNotIn("ann-link", after_ids)
        self.assertGreaterEqual(result["skipped_non_user"], 1)
        self.assertEqual(after_ids, {"ann-meta", "ann-both", "ann-byte"})

    def test_adopt_refuses_when_materialization_stale(self) -> None:
        """Stale materialization must NOT adopt missing byte markup."""
        self.db.sync_work_annotations(self.work_id, [_highlight("a", "A")])
        mat = self.db.get_work_pdf_materialization(self.work_id)
        self.assertTrue(mat["stale"])

        with self.assertRaises(WorkAnnotationError) as ctx:
            self.db.adopt_byte_only_user_markup(
                self.work_id, [_highlight("byte", "Byte")]
            )
        self.assertEqual(ctx.exception.code, pdf_materialization.STALE_CODE)
        self.assertNotIn("byte", self._ids())
        mat2 = self.db.get_work_pdf_materialization(self.work_id)
        self.assertEqual(
            mat2["canonical_annotation_set_revision"],
            mat["canonical_annotation_set_revision"],
        )
        self.assertEqual(
            mat2["materialized_pdf_annotation_revision"],
            mat["materialized_pdf_annotation_revision"],
        )

    def test_adopt_does_not_recreate_deleted_annotation_from_stale_bytes(self) -> None:
        """A canonical+materialized → delete ACK → stale bytes still contain A →
        reopen must not adopt A back into metadata."""
        self.db.sync_work_annotations(self.work_id, [_highlight("ann-a", "A")])
        self.db.mark_work_pdf_materialized(self.work_id)
        mat = self.db.get_work_pdf_materialization(self.work_id)
        self.assertFalse(mat["stale"])

        with self.db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM annotations WHERE id = ? AND work_id = ?",
                ("ann-a", self.work_id),
            )
            pdf_materialization.bump_canonical_annotation_set_on_conn(conn, self.work_id)
            conn.commit()

        mat2 = self.db.get_work_pdf_materialization(self.work_id)
        self.assertTrue(mat2["stale"])
        self.assertNotIn("ann-a", self._ids())

        with self.assertRaises(WorkAnnotationError) as ctx:
            self.db.adopt_byte_only_user_markup(
                self.work_id, [_highlight("ann-a", "A")]
            )
        self.assertEqual(ctx.exception.code, pdf_materialization.STALE_CODE)
        self.assertNotIn("ann-a", self._ids())

    def test_adopt_does_not_resurrect_known_absent_when_materialization_current(self) -> None:
        """CREATE → mark → DELETE → mark (gens equal) must not resurrect from viewer."""
        from backend import pdf_annotation_sync

        self.db.sync_work_annotations(self.work_id, [_highlight("ann-a", "A")])
        self.db.mark_work_pdf_materialized(self.work_id)
        # Full-list delete advances per-annotation revision (tombstone).
        self.db.sync_work_annotations(self.work_id, [])
        self.db.mark_work_pdf_materialized(self.work_id)

        mat = self.db.get_work_pdf_materialization(self.work_id)
        self.assertFalse(mat["stale"])
        self.assertNotIn("ann-a", self._ids())
        with self.db.connection() as conn:
            self.assertGreater(
                pdf_annotation_sync.get_revision(conn, self.work_id, "ann-a"), 0
            )

        result = self.db.adopt_byte_only_user_markup(
            self.work_id, [_highlight("ann-a", "A")]
        )
        self.assertEqual(result["adopted"], [])
        self.assertEqual(result["adopted_count"], 0)
        self.assertGreaterEqual(result.get("skipped_known_absent", 0), 1)
        self.assertNotIn("ann-a", self._ids())
        mat_after = self.db.get_work_pdf_materialization(self.work_id)
        self.assertEqual(
            mat_after["canonical_annotation_set_revision"],
            mat["canonical_annotation_set_revision"],
        )
        self.assertEqual(
            mat_after["materialized_pdf_annotation_revision"],
            mat["materialized_pdf_annotation_revision"],
        )
        self.assertFalse(mat_after["stale"])

    def test_adopt_bumps_canonical_only_not_materialized(self) -> None:
        """A client viewer list must not jointly advance materialization gens."""
        self.db.mark_work_pdf_materialized(self.work_id)
        mat = self.db.get_work_pdf_materialization(self.work_id)
        self.assertFalse(mat["stale"])

        self.db.adopt_byte_only_user_markup(
            self.work_id, [_highlight("byte", "Byte")]
        )
        mat2 = self.db.get_work_pdf_materialization(self.work_id)
        self.assertIn("byte", self._ids())
        self.assertGreater(
            mat2["canonical_annotation_set_revision"],
            mat["canonical_annotation_set_revision"],
        )
        self.assertEqual(
            mat2["materialized_pdf_annotation_revision"],
            mat["materialized_pdf_annotation_revision"],
        )
        self.assertTrue(mat2["stale"])

    def test_noop_when_viewer_empty_and_current(self) -> None:
        self.db.sync_work_annotations(self.work_id, [_highlight("a", "A")])
        self.db.mark_work_pdf_materialized(self.work_id)
        before = self.db.get_work_pdf_materialization(self.work_id)
        result = self.db.adopt_byte_only_user_markup(self.work_id, [])
        self.assertEqual(result["adopted_count"], 0)
        after = self.db.get_work_pdf_materialization(self.work_id)
        self.assertEqual(
            before["canonical_annotation_set_revision"],
            after["canonical_annotation_set_revision"],
        )

    def test_empty_viewer_still_refused_when_stale(self) -> None:
        self.db.sync_work_annotations(self.work_id, [_highlight("a", "A")])
        with self.assertRaises(WorkAnnotationError) as ctx:
            self.db.adopt_byte_only_user_markup(self.work_id, [])
        self.assertEqual(ctx.exception.code, pdf_materialization.STALE_CODE)

    def test_classifier_rejects_links(self) -> None:
        self.assertTrue(pdf_annotation_adopt.default_is_non_user(_link("L1")))
        self.assertFalse(
            pdf_annotation_adopt.default_is_user_markup(_link("L1"))
        )
        self.assertTrue(
            pdf_annotation_adopt.default_is_user_markup(_highlight("H1", "x"))
        )

    def test_classifier_rejects_type1_and_flattened_links(self) -> None:
        """Browser prksIsPdfLinkAnnotation treats type 1 and flattened type 2 as links."""
        type1_no_uri = {
            "id": "link-type1",
            "type": 1,
            "pageIndex": 0,
            "rect": {"origin": {"x": 10, "y": 10}, "size": {"width": 40, "height": 12}},
        }
        type1_string = {
            "id": "link-type1-str",
            "type": "1",
            "pageIndex": 0,
            "segmentRects": [
                {"origin": {"x": 10, "y": 10}, "size": {"width": 40, "height": 12}}
            ],
            "rect": {"origin": {"x": 10, "y": 10}, "size": {"width": 40, "height": 12}},
        }
        type2_flat_no_uri = {
            "id": "link-type2-flat",
            "type": 2,
            "pageIndex": 0,
            "rect": {"origin": {"x": 20, "y": 20}, "size": {"width": 30, "height": 10}},
        }
        label_link = {
            "id": "link-label",
            "type": 9,
            "pageIndex": 0,
            "contents": "Link",
            "segmentRects": [
                {"origin": {"x": 1, "y": 1}, "size": {"width": 2, "height": 2}}
            ],
            "rect": {"origin": {"x": 1, "y": 1}, "size": {"width": 2, "height": 2}},
        }
        dest_link = {
            "id": "link-dest",
            "type": 5,
            "pageIndex": 0,
            "dest": [0, {"name": "XYZ"}, 0, 0, 0],
            "rect": {"origin": {"x": 5, "y": 5}, "size": {"width": 10, "height": 10}},
        }
        for item in (
            type1_no_uri,
            type1_string,
            type2_flat_no_uri,
            label_link,
            dest_link,
            _link("L-uri"),
        ):
            with self.subTest(ann_id=item["id"]):
                self.assertTrue(
                    pdf_annotation_adopt.is_pdf_link_annotation(item),
                    msg="expected link for %s" % item["id"],
                )
                self.assertFalse(
                    pdf_annotation_adopt.default_is_user_markup(item),
                    msg="must not adopt link %s" % item["id"],
                )

        self.db.mark_work_pdf_materialized(self.work_id)
        result = self.db.adopt_byte_only_user_markup(
            self.work_id,
            [type1_no_uri, type1_string, type2_flat_no_uri, label_link, dest_link],
        )
        self.assertEqual(result["adopted"], [])
        self.assertEqual(result["skipped_non_user"], 5)
        self.assertEqual(self._ids(), set())

    def test_classifier_adopts_managed_user_types(self) -> None:
        """Server classifier must match browser-managed user markup types."""
        cases = [
            {
                "id": "ink-1",
                "type": 15,
                "pageIndex": 0,
                "inkList": [[[0, 0], [10, 10]]],
                "rect": {"origin": {"x": 0, "y": 0}, "size": {"width": 10, "height": 10}},
            },
            {
                "id": "ft-1",
                "type": 3,
                "pageIndex": 0,
                "contents": "Free text",
                "rect": {"origin": {"x": 1, "y": 1}, "size": {"width": 40, "height": 12}},
            },
            {
                "id": "stamp-1",
                "type": 13,
                "pageIndex": 0,
                "subtype": "Stamp",
                "rect": {"origin": {"x": 2, "y": 2}, "size": {"width": 40, "height": 40}},
            },
            {
                "id": "sq-1",
                "type": 4,
                "pageIndex": 0,
                "subtype": "Square",
                "rect": {"origin": {"x": 3, "y": 3}, "size": {"width": 20, "height": 20}},
            },
            {
                "id": "strike-1",
                "type": 11,
                "pageIndex": 0,
                "subtype": "StrikeOut",
                "segmentRects": [
                    {"origin": {"x": 4, "y": 4}, "size": {"width": 80, "height": 10}}
                ],
                "rect": {"origin": {"x": 4, "y": 4}, "size": {"width": 80, "height": 10}},
            },
            {
                "id": "squig-1",
                "type": 12,
                "pageIndex": 0,
                "subtype": "Squiggly",
                "segmentRects": [
                    {"origin": {"x": 5, "y": 5}, "size": {"width": 80, "height": 10}}
                ],
                "rect": {"origin": {"x": 5, "y": 5}, "size": {"width": 80, "height": 10}},
            },
            {
                "id": "note-1",
                "type": "note",
                "pageIndex": 0,
                "contents": "A sticky note",
            },
        ]
        for item in cases:
            with self.subTest(ann_id=item["id"]):
                self.assertTrue(
                    pdf_annotation_adopt.default_is_user_markup(item),
                    msg="expected user markup for %s" % item["id"],
                )

        self.db.mark_work_pdf_materialized(self.work_id)
        result = self.db.adopt_byte_only_user_markup(self.work_id, cases)
        self.assertEqual(sorted(result["adopted"]), sorted(c["id"] for c in cases))
        self.assertEqual(self._ids(), {c["id"] for c in cases})

    def test_http_adopt_endpoint(self) -> None:
        # Fresh Work: both revisions at 0 (current). Adoption is allowed.
        result = self.db.adopt_byte_only_user_markup(
            self.work_id,
            [_highlight("from-http", "Via API"), _link("skip-me")],
        )
        self.assertEqual(result["adopted"], ["from-http"])
        self.assertIn("from-http", self._ids())
        self.assertNotIn("skip-me", self._ids())


if __name__ == "__main__":
    unittest.main()
