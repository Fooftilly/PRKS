import json
import os
import shutil
import sys
import tempfile
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.db_manager import PRKSDatabase, WorkAnnotationError
from backend.db_migrations import table_exists
from backend.pdf_annotations import (
    normalize_annotation,
    parse_annotations_json,
    reconstruct_annotation,
)
from backend.storage.config import StorageConfig

_SCHEMA_PATH = os.path.join(_PROJECT_DIR, "backend", "db_schema.sql")

_REALISTIC = {
    "id": "ann-1",
    "type": 9,
    "pageIndex": 0,
    "contents": "My comment",
    "color": "#FFCD45",
    "strokeColor": "#FFCD45",
    "opacity": 1,
    "blendMode": "Multiply",
    "segmentRects": [
        {
            "origin": {"x": 72, "y": 700},
            "size": {"width": 160, "height": 14},
        }
    ],
    "rect": {
        "origin": {"x": 72, "y": 700},
        "size": {"width": 160, "height": 14},
    },
    "custom": {"prksComment": "My comment"},
}


class TestAnnotationNormalizer(unittest.TestCase):
    def test_parse_rejects_non_list_json(self):
        for raw in ('{"id":"a"}', "1", '"x"', "null", "true"):
            with self.subTest(raw=raw):
                with self.assertRaises(WorkAnnotationError) as ctx:
                    parse_annotations_json(raw)
                self.assertEqual(ctx.exception.http_status, 400)
                self.assertEqual(ctx.exception.code, "malformed_annotation_payload")

    def test_parse_rejects_invalid_json_and_non_string(self):
        with self.assertRaises(WorkAnnotationError) as ctx:
            parse_annotations_json("{")
        self.assertEqual(ctx.exception.http_status, 400)
        with self.assertRaises(WorkAnnotationError) as ctx2:
            parse_annotations_json([{"id": "a"}])
        self.assertEqual(ctx2.exception.http_status, 400)

    def test_normalize_strips_aliases_into_geometry(self):
        n = normalize_annotation(
            {
                "uuid": "x1",
                "annotationType": "highlight",
                "comment": "hi",
                "pageNumber": 2,
                "color": "#fff",
                "segmentRects": [{"origin": {"x": 1, "y": 2}, "size": {"width": 3, "height": 4}}],
                "work_id": "W-SHOULD-NOT-STORE",
            }
        )
        self.assertEqual(n["id"], "x1")
        self.assertEqual(n["type"], "highlight")
        self.assertEqual(n["content"], "hi")
        self.assertEqual(n["page_index"], 2)
        self.assertEqual(n["color"], "#fff")
        self.assertEqual(
            n["geometry"]["segmentRects"],
            [{"origin": {"x": 1, "y": 2}, "size": {"width": 3, "height": 4}}],
        )
        for banned in (
            "uuid",
            "id",
            "annotationType",
            "type",
            "comment",
            "contents",
            "pageNumber",
            "page",
            "pageIndex",
            "color",
            "work_id",
        ):
            self.assertNotIn(banned, n["geometry"])

    def test_missing_type_is_empty_not_highlight(self):
        n = normalize_annotation({"id": "a1", "pageIndex": 0})
        self.assertEqual(n["type"], "")

    def test_missing_id_fails(self):
        with self.assertRaises(WorkAnnotationError) as ctx:
            normalize_annotation({"type": "note", "pageIndex": 0})
        self.assertEqual(ctx.exception.http_status, 400)

    def test_malformed_page_fails(self):
        with self.assertRaises(WorkAnnotationError) as ctx:
            normalize_annotation({"id": "a1", "pageIndex": "nope"})
        self.assertEqual(ctx.exception.http_status, 400)
        with self.assertRaises(WorkAnnotationError):
            normalize_annotation({"id": "a1", "pageIndex": -1})

    def test_reconstruct_canonical_wins_over_stale_geometry(self):
        item = reconstruct_annotation(
            {
                "id": "a1",
                "type": "9",
                "content": "from-column",
                "page_index": 0,
                "color": "#aaa",
                "geometry_json": json.dumps(
                    {
                        "id": "other",
                        "type": "highlight",
                        "contents": "stale",
                        "pageIndex": 99,
                        "color": "#000",
                        "segmentRects": [1],
                    }
                ),
                "updated_at": "ts",
            }
        )
        self.assertEqual(item["id"], "a1")
        self.assertEqual(item["type"], 9)
        self.assertEqual(item["contents"], "from-column")
        self.assertEqual(item["pageIndex"], 0)
        self.assertEqual(item["color"], "#aaa")
        self.assertEqual(item["segmentRects"], [1])
        self.assertNotIn("page", item)


class TestCanonicalAnnotations(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="prks-ann-tests-")
        self.storage = StorageConfig.for_testing(self._tmpdir)
        os.makedirs(self.storage.pdfs_dir, exist_ok=True)
        self.db = PRKSDatabase(storage=self.storage, schema_path=_SCHEMA_PATH)

    def tearDown(self):
        if getattr(self, "_tmpdir", None):
            shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _got(self, work_id):
        return json.loads(self.db.get_work_annotations(work_id))

    def _by_id(self, work_id):
        return {item["id"]: item for item in self._got(work_id)}

    def _canon_rows(self, work_id):
        return self.db.execute_query(
            """
            SELECT id, type, content, page_index, color, geometry_json
            FROM annotations WHERE work_id = ? ORDER BY id
            """,
            (work_id,),
        )

    def test_work_annotations_table_does_not_exist(self):
        conn = self.db.get_connection()
        try:
            self.assertFalse(table_exists(conn, "work_annotations"))
        finally:
            conn.close()

    def test_realistic_semantic_round_trip(self):
        w_id = self.db.add_work(title="PDF Ann")
        self.db.save_work_annotations(w_id, json.dumps([_REALISTIC]))
        rows = self._canon_rows(w_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "ann-1")
        self.assertEqual(rows[0]["type"], "9")
        self.assertEqual(rows[0]["content"], "My comment")
        self.assertEqual(rows[0]["page_index"], 0)
        self.assertEqual(rows[0]["color"], "#FFCD45")
        geom = json.loads(rows[0]["geometry_json"])
        self.assertEqual(geom["segmentRects"], _REALISTIC["segmentRects"])
        self.assertEqual(geom["rect"], _REALISTIC["rect"])
        self.assertEqual(geom["custom"]["prksComment"], "My comment")
        self.assertEqual(geom["opacity"], 1)
        self.assertEqual(geom["strokeColor"], "#FFCD45")
        self.assertEqual(geom["blendMode"], "Multiply")
        for banned in ("id", "type", "contents", "pageIndex", "color", "work_id"):
            self.assertNotIn(banned, geom)

        got = self._by_id(w_id)["ann-1"]
        self.assertEqual(got["id"], "ann-1")
        self.assertEqual(got["type"], 9)
        self.assertEqual(got["contents"], "My comment")
        self.assertEqual(got["pageIndex"], 0)
        self.assertEqual(got["color"], "#FFCD45")
        self.assertEqual(got["strokeColor"], "#FFCD45")
        self.assertEqual(got["opacity"], 1)
        self.assertEqual(got["blendMode"], "Multiply")
        self.assertEqual(got["segmentRects"], _REALISTIC["segmentRects"])
        self.assertEqual(got["rect"], _REALISTIC["rect"])
        self.assertEqual(got["custom"]["prksComment"], "My comment")
        self.assertNotIn("page", got)
        self.assertNotIn("pageNumber", got)

    def test_replacement_updates_deletes_and_inserts(self):
        w_id = self.db.add_work(title="Replace")
        self.db.sync_work_annotations(
            w_id,
            [
                {"id": "A", "contents": "a0", "pageIndex": 0},
                {"id": "B", "contents": "b0", "pageIndex": 0},
                {"id": "C", "contents": "c0", "pageIndex": 1},
            ],
        )
        self.db.sync_work_annotations(
            w_id,
            [
                {"id": "A", "contents": "a1", "pageIndex": 0},
                {"id": "C", "contents": "c0", "pageIndex": 1},
                {"id": "D", "contents": "d0", "pageIndex": 2},
            ],
        )
        got = self._by_id(w_id)
        self.assertEqual(set(got), {"A", "C", "D"})
        self.assertEqual(got["A"]["contents"], "a1")
        self.assertEqual(got["C"]["contents"], "c0")
        self.assertEqual(got["D"]["contents"], "d0")
        ids = [row["id"] for row in self._canon_rows(w_id)]
        self.assertEqual(ids, ["A", "C", "D"])

    def test_empty_list_clears_canonical(self):
        w_id = self.db.add_work(title="Empty")
        self.db.sync_work_annotations(
            w_id,
            [
                {"id": "A", "contents": "a", "pageIndex": 0},
                {"id": "B", "contents": "b", "pageIndex": 0},
            ],
        )
        self.db.sync_work_annotations(w_id, [])
        self.assertEqual(self._got(w_id), [])
        self.assertEqual(self._canon_rows(w_id), [])

    def test_malformed_later_item_rolls_back(self):
        w_id = self.db.add_work(title="Rollback")
        original = [
            {"id": "A", "contents": "keep", "pageIndex": 0},
            {"id": "B", "contents": "also", "pageIndex": 0},
        ]
        self.db.sync_work_annotations(w_id, original)
        before = self._canon_rows(w_id)
        with self.assertRaises(WorkAnnotationError) as ctx:
            self.db.sync_work_annotations(
                w_id,
                [
                    {"id": "A", "contents": "changed", "pageIndex": 0},
                    {"type": "note", "contents": "no-id", "pageIndex": 0},
                ],
            )
        self.assertEqual(ctx.exception.http_status, 400)
        self.assertEqual(self._canon_rows(w_id), before)
        self.assertEqual(self._by_id(w_id)["A"]["contents"], "keep")

    def test_duplicate_incoming_id_is_400_without_mutation(self):
        w_id = self.db.add_work(title="Dup")
        self.db.sync_work_annotations(
            w_id, [{"id": "A", "contents": "keep", "pageIndex": 0}]
        )
        before = self._canon_rows(w_id)
        with self.assertRaises(WorkAnnotationError) as ctx:
            self.db.sync_work_annotations(
                w_id,
                [
                    {"id": "A", "contents": "new", "pageIndex": 0},
                    {"id": "A", "contents": "other", "pageIndex": 1},
                ],
            )
        self.assertEqual(ctx.exception.http_status, 400)
        self.assertEqual(ctx.exception.code, "malformed_annotation_payload")
        self.assertEqual(self._canon_rows(w_id), before)

    def test_cross_work_id_is_409_without_mutation(self):
        a = self.db.add_work(title="Owner")
        b = self.db.add_work(title="Other")
        self.db.sync_work_annotations(
            a, [{"id": "X", "contents": "owned", "pageIndex": 0}]
        )
        self.db.sync_work_annotations(
            b, [{"id": "Y", "contents": "other", "pageIndex": 0}]
        )
        before_a = self._canon_rows(a)
        before_b = self._canon_rows(b)
        with self.assertRaises(WorkAnnotationError) as ctx:
            self.db.sync_work_annotations(
                b, [{"id": "X", "contents": "steal", "pageIndex": 0}]
            )
        self.assertEqual(ctx.exception.http_status, 409)
        self.assertEqual(ctx.exception.code, "annotation_id_conflict")
        self.assertEqual(self._canon_rows(a), before_a)
        self.assertEqual(self._canon_rows(b), before_b)

    def test_missing_work_is_404_including_empty_list(self):
        with self.assertRaises(WorkAnnotationError) as ctx:
            self.db.sync_work_annotations("W-MISSING", [])
        self.assertEqual(ctx.exception.http_status, 404)
        self.assertEqual(ctx.exception.code, "work_not_found")
        with self.assertRaises(WorkAnnotationError) as ctx2:
            self.db.save_work_annotations(
                "W-MISSING", json.dumps([{"id": "A", "pageIndex": 0}])
            )
        self.assertEqual(ctx2.exception.http_status, 404)

    def test_invalid_json_is_400_and_leaves_canonical_intact(self):
        w_id = self.db.add_work(title="JSON")
        self.db.save_work_annotations(
            w_id, json.dumps([{"id": "A", "contents": "keep", "pageIndex": 0}])
        )
        before = self._canon_rows(w_id)
        with self.assertRaises(WorkAnnotationError) as ctx:
            self.db.save_work_annotations(w_id, "{")
        self.assertEqual(ctx.exception.http_status, 400)
        self.assertEqual(self._canon_rows(w_id), before)
        with self.assertRaises(WorkAnnotationError):
            self.db.save_work_annotations(w_id, json.dumps({"id": "A"}))
        self.assertEqual(self._canon_rows(w_id), before)

    def test_old_four_key_geometry_still_reconstructs(self):
        w_id = self.db.add_work(title="Legacy geom")
        geom = {
            "rects": [{"x": 1}],
            "quadPoints": None,
            "rect": {"origin": {"x": 1, "y": 2}, "size": {"width": 3, "height": 4}},
            "position": None,
        }
        self.db.execute_query(
            """
            INSERT INTO annotations (id, work_id, type, content, page_index, color, geometry_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("old-1", w_id, "highlight", "note", 0, "#fff", json.dumps(geom)),
        )
        got = self._by_id(w_id)["old-1"]
        self.assertEqual(got["type"], "highlight")
        self.assertEqual(got["contents"], "note")
        self.assertEqual(got["pageIndex"], 0)
        self.assertEqual(got["rect"], geom["rect"])
        self.assertEqual(got["rects"], [{"x": 1}])
        self.assertIsNone(got["quadPoints"])
        self.assertIsNone(got["position"])

    def test_get_order_is_page_then_id(self):
        w_id = self.db.add_work(title="Order")
        self.db.sync_work_annotations(
            w_id,
            [
                {"id": "b", "pageIndex": 1},
                {"id": "a", "pageIndex": 1},
                {"id": "c", "pageIndex": 0},
            ],
        )
        ids = [item["id"] for item in self._got(w_id)]
        self.assertEqual(ids, ["c", "a", "b"])

    def test_work_payload_uses_canonical_annotations(self):
        w_id = self.db.add_work(title="Work payload")
        self.db.save_work_annotations(w_id, json.dumps([_REALISTIC]))
        work = self.db.get_work(w_id)
        anns = json.loads(work["annotations"])
        self.assertEqual(anns[0]["custom"]["prksComment"], "My comment")
