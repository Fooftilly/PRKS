"""Slice A fidelity helpers and contracts for PDF annotation local-first."""
from __future__ import annotations

import json
import os
import unittest

from run_tests import apply_isolated_test_env

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
apply_isolated_test_env(_PROJECT_DIR)

from backend.pdf_annotations import (
    annotations_semantically_equal,
    round_trip_annotation,
    semantic_annotation_view,
)


_HIGHLIGHT = {
    "id": "ann-hi",
    "type": 9,
    "pageIndex": 0,
    "contents": "My comment",
    "color": "#FFCD45",
    "strokeColor": "#FFCD45",
    "opacity": 1,
    "blendMode": "Multiply",
    "segmentRects": [
        {"origin": {"x": 72, "y": 700}, "size": {"width": 160, "height": 14}}
    ],
    "rect": {"origin": {"x": 72, "y": 700}, "size": {"width": 160, "height": 14}},
    "custom": {"prksComment": "My comment"},
}

_UNDERLINE = {
    "id": "ann-un",
    "type": 10,
    "pageIndex": 0,
    "contents": "Under note",
    "color": "#2563eb",
    "strokeColor": "#2563eb",
    "opacity": 1,
    "segmentRects": [
        {"origin": {"x": 80, "y": 650}, "size": {"width": 180, "height": 12}}
    ],
    "rect": {"origin": {"x": 80, "y": 650}, "size": {"width": 180, "height": 12}},
    "custom": {"prksComment": "Under note"},
}


class TestAnnotationFidelityCodec(unittest.TestCase):
    def test_round_trip_preserves_highlight_semantics(self):
        got = round_trip_annotation(_HIGHLIGHT)
        self.assertTrue(annotations_semantically_equal(_HIGHLIGHT, got))
        view = semantic_annotation_view(got)
        self.assertEqual(view["id"], "ann-hi")
        self.assertEqual(view["type"], "9")
        self.assertEqual(view["content"], "My comment")
        self.assertEqual(view["color"], "#FFCD45")
        self.assertEqual(view["custom"]["prksComment"], "My comment")
        self.assertEqual(view["rect"], _HIGHLIGHT["rect"])
        self.assertEqual(view["segmentRects"], _HIGHLIGHT["segmentRects"])
        self.assertEqual(view["blendMode"], "Multiply")

    def test_round_trip_preserves_underline_semantics(self):
        got = round_trip_annotation(_UNDERLINE)
        self.assertTrue(annotations_semantically_equal(_UNDERLINE, got))
        self.assertEqual(semantic_annotation_view(got)["type"], "10")

    def test_semantic_equality_ignores_viewer_bookkeeping(self):
        left = dict(_HIGHLIGHT)
        right = dict(_HIGHLIGHT)
        right["created"] = "2020-01-01T00:00:00.000Z"
        right["author"] = "someone"
        right["engineNoise"] = {"x": 1}
        # engineNoise is geometry — would break equality; strip via round_trip first
        # Bookkeeping that is NOT in fidelity keys should not matter if absent from both views.
        # `created`/`author` land in geometry unless stripped; round-trip keeps unknown keys.
        # Equality compares fidelity keys only — extra geometry keys outside the allowlist
        # are ignored by semantic_annotation_view.
        self.assertTrue(annotations_semantically_equal(left, right))

    def test_semantic_inequality_on_geometry(self):
        other = dict(_HIGHLIGHT)
        other = json.loads(json.dumps(other))
        other["rect"] = {
            "origin": {"x": 1, "y": 2},
            "size": {"width": 3, "height": 4},
        }
        self.assertFalse(annotations_semantically_equal(_HIGHLIGHT, other))

    def test_semantic_inequality_on_ink_list_and_vertices(self):
        ink_a = {
            "id": "ann-ink",
            "type": 15,
            "pageIndex": 0,
            "inkList": [[[0, 0], [10, 10]]],
            "rect": {"origin": {"x": 0, "y": 0}, "size": {"width": 10, "height": 10}},
        }
        ink_b = dict(ink_a)
        ink_b = json.loads(json.dumps(ink_b))
        ink_b["inkList"] = [[[0, 0], [20, 20]]]
        self.assertFalse(annotations_semantically_equal(ink_a, ink_b))
        self.assertTrue(
            annotations_semantically_equal(ink_a, round_trip_annotation(ink_a))
        )

        poly_a = {
            "id": "ann-poly",
            "type": 7,
            "pageIndex": 0,
            "vertices": [[0, 0], [10, 0], [10, 10]],
            "rect": {"origin": {"x": 0, "y": 0}, "size": {"width": 10, "height": 10}},
        }
        poly_b = json.loads(json.dumps(poly_a))
        poly_b["vertices"] = [[0, 0], [10, 0], [5, 10]]
        self.assertFalse(annotations_semantically_equal(poly_a, poly_b))
        view = semantic_annotation_view(poly_a)
        self.assertEqual(view["vertices"], poly_a["vertices"])
        self.assertIn("inkList", semantic_annotation_view(ink_a))

    def test_fidelity_harness_artifacts_exist(self):
        browser = os.path.join(_PROJECT_DIR, "tests", "browser")
        self.assertTrue(
            os.path.isfile(os.path.join(browser, "pdf_annotation_fidelity.html"))
        )
        self.assertTrue(
            os.path.isfile(os.path.join(browser, "run_pdf_annotation_fidelity.py"))
        )
        self.assertTrue(
            os.path.isfile(os.path.join(browser, "assets", "with_link.pdf"))
        )
        self.assertTrue(
            os.path.isfile(os.path.join(browser, "assets", "minimal.pdf"))
        )


# parity: annotation-fidelity-codec

if __name__ == "__main__":
    unittest.main()
