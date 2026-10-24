"""Linear [[label]] extraction from PDF bytes (ReDoS-safe)."""

from __future__ import annotations

import unittest

from backend.pdf_byte_mentions import (
    iter_double_bracket_inners,
    iter_pdf_mentioned_labels,
    normalize_pdf_mentioned_label,
)


class PdfByteMentionsTests(unittest.TestCase):
    def test_extracts_simple_labels(self) -> None:
        data = b"hello [[Ada Lovelace]] and [[Curie]] end"
        self.assertEqual(
            list(iter_pdf_mentioned_labels(data)),
            ["Ada Lovelace", "Curie"],
        )

    def test_skips_nested_or_broken_brackets(self) -> None:
        data = b"[[a[[b]] [[ok]] [[x]y]]"
        self.assertEqual(list(iter_pdf_mentioned_labels(data)), ["ok"])

    def test_unclosed_open_is_ignored(self) -> None:
        data = b"[[" + (b"[[a" * 5000)
        self.assertEqual(list(iter_double_bracket_inners(data)), [])

    def test_oversized_inner_skipped(self) -> None:
        data = b"[[" + (b"a" * 500) + b"]] [[Bob]]"
        self.assertEqual(list(iter_pdf_mentioned_labels(data)), ["Bob"])

    def test_normalize_strips_and_filters(self) -> None:
        self.assertEqual(normalize_pdf_mentioned_label(b"  Ann-Marie  "), "Ann-Marie")
        self.assertEqual(normalize_pdf_mentioned_label(b"A@B#C"), "ABC")
        self.assertEqual(normalize_pdf_mentioned_label(b"   "), "")

    def test_custom_raw_byte_limit_is_inclusive(self) -> None:
        # The bound is on raw bytes, not decoded Unicode characters.
        payload = "é".encode("utf-8") * 100  # exactly 200 bytes
        data = b"[[" + payload + b"]] [[After]]"
        self.assertEqual(list(iter_double_bracket_inners(data)), [payload, b"After"])
        self.assertEqual(list(iter_pdf_mentioned_labels(data)), ["é" * 100, "After"])

        too_large = b"[[" + payload + b"x]] [[Next]]"
        self.assertEqual(list(iter_double_bracket_inners(too_large)), [b"Next"])

        limited = b"[[AB]] [[C]] [[]]"
        self.assertEqual(
            list(iter_double_bracket_inners(limited, max_inner=1)), [b"C", b""]
        )
        self.assertEqual(
            list(iter_double_bracket_inners(limited, max_inner=0)), [b""]
        )
        self.assertEqual(
            list(iter_double_bracket_inners(limited, max_inner=-1)), []
        )

    def test_invalid_utf8_and_empty_filtered_labels_do_not_hide_later_mentions(self) -> None:
        data = b"[[\xff\xfe]] [[!@#$]] [[A@B]] [[Ren\xc3\xa9]] [[After]]"
        self.assertEqual(
            list(iter_pdf_mentioned_labels(data)), ["AB", "René", "After"]
        )
        self.assertEqual(normalize_pdf_mentioned_label(b"\xff\xfe"), "")
        self.assertEqual(normalize_pdf_mentioned_label(b"!@#$"), "")
        self.assertEqual(normalize_pdf_mentioned_label(b" -- "), "--")

    def test_pathological_prefix_stays_linear(self) -> None:
        # Former ReDoS shape: many '[[a' without closing. Must finish quickly.
        data = b"[[" + (b"[[a" * 20000)
        self.assertEqual(list(iter_double_bracket_inners(data)), [])


if __name__ == "__main__":
    unittest.main()
