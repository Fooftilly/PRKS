"""How ``tests/pg/answers.py`` compares an answer with its spelled-out expectation (#1426)."""

from __future__ import annotations

import os
import sys
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from tests.pg.answers import Exact, _mismatch


class MismatchTests(unittest.TestCase):
    def test_a_row_may_carry_fields_the_expectation_leaves_out(self):
        self.assertIsNone(_mismatch({"id": "<W a>", "title": "A", "new_field": 1}, {"title": "A"}, "answer"))

    def test_an_empty_expected_map_matches_only_an_empty_map(self):
        self.assertIsNone(_mismatch({"known_absent": {}}, {"known_absent": {}}, "answer"))
        self.assertIsNotNone(_mismatch({"meta": {"x": 1}}, {"meta": {}}, "answer"))

    def test_a_nonempty_collection_map_must_hold_exactly_its_keys(self):
        expected = {"known_absent": {"ann-hi-1": 2}}
        self.assertIsNone(_mismatch({"known_absent": {"ann-hi-1": 2}}, expected, "answer"))
        found = _mismatch({"known_absent": {"ann-hi-1": 2, "ann-never": 1}}, expected, "answer")
        self.assertEqual(found, "answer['known_absent']: unexpected keys ['ann-never'] in "
                                "{'ann-hi-1': 2, 'ann-never': 1}")
        self.assertIsNotNone(_mismatch({"known_absent": {"ann-hi-1": 3}}, expected, "answer"))

    def test_a_collection_inside_a_list_is_exact_too(self):
        expected = [{"state": {"known_absent": {"<T a>": 2}}}]
        self.assertIsNotNone(_mismatch([{"state": {"known_absent": {"<T a>": 2, "<T b>": 1}}}], expected, "answer"))

    def test_an_exact_map_must_hold_exactly_its_keys(self):
        self.assertIsNone(_mismatch({"counts": {"a": 1}}, {"counts": Exact(a=1)}, "answer"))
        self.assertIsNotNone(_mismatch({"counts": {"a": 1, "b": 2}}, {"counts": Exact(a=1)}, "answer"))


if __name__ == "__main__":
    unittest.main()
