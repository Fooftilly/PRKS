"""Text identity keys in Python against the shared vectors (#624, §8.1).

The PostgreSQL half of the parity check is tests/pg/test_text_identity.py,
which runs the same vector file through prks.identity_key.
"""

from __future__ import annotations

import json
import os
import sys
import unicodedata
import unittest

from backend.domain.text_identity import WHITESPACE, UnsupportedTextError, identity_key
from backend.research_markup import normalize_concept_key

VECTORS_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "text_identity_vectors.json")


def load_vectors() -> list[dict[str, str]]:
    with open(VECTORS_PATH, encoding="utf-8") as handle:
        return json.load(handle)["vectors"]


class TextIdentityVectorTests(unittest.TestCase):
    def test_every_vector_maps_to_its_key(self) -> None:
        vectors = load_vectors()
        self.assertGreaterEqual(len(vectors), 25)
        for vector in vectors:
            with self.subTest(vector["note"]):
                self.assertEqual(identity_key(vector["input"]), vector["key"])

    def test_vectors_cover_the_required_scripts(self) -> None:
        # §8.1 names these classes; a vector file without them proves little.
        text = "".join(v["input"] for v in load_vectors())
        for sample in ("ς", "ΐ", "Ђ", "Đ", "ß", "İ", "ı", "\u0301", "\U00010400"):
            with self.subTest(codepoint=hex(ord(sample))):
                self.assertIn(sample, text)

    def test_whitespace_is_what_str_split_splits_on(self) -> None:
        expected = {chr(c) for c in range(sys.maxunicode + 1) if chr(c).isspace()}
        self.assertEqual(set(WHITESPACE), expected)
        self.assertEqual(len(WHITESPACE), len(set(WHITESPACE)))

    def test_matches_todays_concept_rule(self) -> None:
        for vector in load_vectors():
            with self.subTest(vector["note"]):
                self.assertEqual(identity_key(vector["input"]), normalize_concept_key(vector["input"]))

    def test_no_diacritic_or_compatibility_folding(self) -> None:
        self.assertNotEqual(identity_key("Résumé"), identity_key("Resume"))
        self.assertNotEqual(identity_key("ћ"), identity_key("ч"))
        self.assertEqual(identity_key("Ａ"), "ａ")

    def test_code_points_unknown_to_this_python_are_refused(self) -> None:
        # U+0378 has never been assigned; U+A7CB (Unicode 16.0) folds to
        # U+0264 in PostgreSQL 18 but is unassigned before Python 3.14.
        with self.assertRaises(UnsupportedTextError):
            identity_key("tag" + chr(0x0378))
        if unicodedata.category(chr(0xA7CB)) == "Cn":
            with self.assertRaises(UnsupportedTextError):
                identity_key(chr(0xA7CB))
        else:
            self.assertEqual(identity_key(chr(0xA7CB)), chr(0x0264))

    def test_text_postgresql_cannot_store_is_refused(self) -> None:
        for value in ("a\x00b", "a" + chr(0xD800), chr(0xDFFF)):
            with self.subTest(codepoints=[hex(ord(c)) for c in value]):
                with self.assertRaises(UnsupportedTextError):
                    identity_key(value)


if __name__ == "__main__":
    unittest.main()
