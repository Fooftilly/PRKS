"""The PDF annotation rules both backends share (kept by PG-C2 #641).

``backend/pdf_annotations.py`` parses and normalizes a viewer annotation into
its columns and geometry and reconstructs it; ``backend/pdf_annotation_adopt.py``
decides which viewer items are user markup. These are pure;
``tests/pg/test_annotations.py`` and ``tests/pg/test_annotations_materialize.py``
exercise them through the PostgreSQL boundary.
"""

from __future__ import annotations

import json
import unittest

from backend import pdf_annotation_adopt
from backend.pdf_annotations import (
    WorkAnnotationError,
    normalize_annotation,
    parse_annotations_json,
    reconstruct_annotation,
)


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


class ClassifierTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
