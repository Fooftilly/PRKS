"""Regression coverage for ``extract_pdf`` page-loop bookkeeping (#100).

These tests drive ``extract_pdf`` against a synthetic ``pymupdf`` module so they
can (a) count how often each extracted page string is measured and (b) compare
results against a frozen copy of the pre-#100 quadratic implementation.
"""

import itertools
import sys
import types
import unittest
from typing import Callable, Iterator, List, Optional, Sequence
from unittest.mock import patch

from backend.log_safety import safe_error_type
from backend.text_index import (
    PDFTextExtraction,
    PDFTextExtractionError,
    PDFTextExtractorUnavailable,
    extract_pdf,
)


class _CountingStr(str):
    """A page string that records every ``len()`` (and truthiness) probe."""

    calls: int

    def __new__(cls, value: str) -> "_CountingStr":
        obj = super().__new__(cls, value)
        obj.calls = 0
        return obj

    def __len__(self) -> int:
        self.calls += 1
        return super().__len__()


class _FakePage:
    def __init__(self, text: Optional[str], on_get_text: Optional[Callable[[], None]] = None):
        self._text = text
        self._on_get_text = on_get_text
        self.get_text_calls: List[str] = []

    def get_text(self, kind: str) -> Optional[str]:
        self.get_text_calls.append(kind)
        if self._on_get_text is not None:
            self._on_get_text()
        return self._text


class _FakeDoc:
    def __init__(self, pages: Sequence[_FakePage]):
        self._pages = list(pages)
        self.pages_yielded = 0

    def __enter__(self) -> "_FakeDoc":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def __iter__(self) -> Iterator[_FakePage]:
        for page in self._pages:
            self.pages_yielded += 1
            yield page


def _fake_pymupdf(doc: _FakeDoc) -> types.ModuleType:
    module = types.ModuleType("pymupdf")
    setattr(module, "open", lambda _path: doc)
    return module


def _old_extract_pdf(page_texts: Sequence[Optional[str]], max_chars: int) -> PDFTextExtraction:
    """Frozen pre-#100 loop (O(n^2) bookkeeping), used only as a parity oracle."""
    parts: List[str] = []
    for text in page_texts:
        parts.append(text or "")
        if sum(len(p) for p in parts) >= max_chars:
            break
    txt = "\n".join(parts)
    truncated = len(txt) > max_chars
    if truncated:
        txt = txt[:max_chars]
    if not txt.strip():
        return PDFTextExtraction(text="", empty=True, truncated=False)
    return PDFTextExtraction(text=txt, empty=False, truncated=truncated)


def _run(page_texts: Sequence[Optional[str]], max_chars: int):
    pages = [_FakePage(t) for t in page_texts]
    doc = _FakeDoc(pages)
    with patch.dict(sys.modules, {"pymupdf": _fake_pymupdf(doc)}):
        result = extract_pdf("synthetic.pdf", max_chars=max_chars)
    return result, doc, pages


class ExtractPdfLinearBookkeepingTests(unittest.TestCase):
    def test_each_page_is_measured_a_constant_number_of_times(self):
        page_count = 400
        texts = [_CountingStr("x") for _ in range(page_count)]
        result, doc, _pages = _run(texts, max_chars=10**9)

        self.assertEqual(doc.pages_yielded, page_count)
        self.assertEqual(len(result.text), 2 * page_count - 1)
        # The old ``sum(len(p) for p in parts)`` measured page i on every later
        # iteration (page 0 would be measured page_count times; ~n^2/2 total).
        # A running counter measures each page O(1) times (len + truthiness).
        per_page = [t.calls for t in texts]
        self.assertLessEqual(max(per_page), 2, per_page[:5])
        self.assertLessEqual(sum(per_page), 2 * page_count)

    def test_oracle_instrumentation_detects_quadratic_shape(self):
        # Sanity check that the instrumentation would catch the old loop.
        page_count = 50
        texts = [_CountingStr("x") for _ in range(page_count)]
        _old_extract_pdf(texts, max_chars=10**9)
        self.assertGreaterEqual(texts[0].calls, page_count)
        self.assertGreater(sum(t.calls for t in texts), 2 * page_count)

    def test_stops_iterating_once_limit_reached(self):
        texts = ["abc"] * 10
        result, doc, pages = _run(texts, max_chars=7)
        # 3 + 3 = 6 < 7, 9 >= 7 → stop after the third page.
        self.assertEqual(doc.pages_yielded, 3)
        self.assertEqual([p.get_text_calls for p in pages[:3]], [["text"]] * 3)
        self.assertEqual(pages[3].get_text_calls, [])
        self.assertEqual(result, PDFTextExtraction(text="abc\nabc", empty=False, truncated=True))


class ExtractPdfBehaviorParityTests(unittest.TestCase):
    def assertParity(self, texts: Sequence[Optional[str]], max_chars: int) -> PDFTextExtraction:
        result, _doc, _pages = _run(texts, max_chars)
        self.assertEqual(result, _old_extract_pdf(texts, max_chars), (texts, max_chars))
        return result

    def test_empty_document(self):
        self.assertEqual(self.assertParity([], 10), PDFTextExtraction("", True, False))

    def test_one_page_below_limit(self):
        self.assertEqual(self.assertParity(["hello"], 10), PDFTextExtraction("hello", False, False))

    def test_multiple_pages_below_limit(self):
        self.assertEqual(
            self.assertParity(["ab", "cd", "ef"], 10), PDFTextExtraction("ab\ncd\nef", False, False)
        )

    def test_limit_reached_exactly_on_single_page(self):
        self.assertEqual(
            self.assertParity(["abcde", "zzz"], 5), PDFTextExtraction("abcde", False, False)
        )

    def test_limit_reached_exactly_by_page_text_but_separators_truncate(self):
        # Page text sums to exactly 6 (stop), separators push the join to 7 → truncated.
        self.assertEqual(
            self.assertParity(["abc", "def", "never"], 6),
            PDFTextExtraction("abc\nde", False, True),
        )

    def test_limit_exceeded_on_later_page(self):
        self.assertEqual(
            self.assertParity(["ab", "cd", "efghij", "never"], 7),
            PDFTextExtraction("ab\ncd\ne", False, True),
        )

    def test_separators_not_counted_toward_stop_condition(self):
        # Joined text exceeds max_chars long before page text does; all pages
        # are still read because only page characters drive the stop check.
        texts = ["a"] * 6
        result, doc, _pages = _run(texts, max_chars=6)
        self.assertEqual(doc.pages_yielded, 6)
        self.assertEqual(result, PDFTextExtraction("a\na\na\n", False, True))
        self.assertEqual(result, _old_extract_pdf(texts, 6))

    def test_many_short_pages(self):
        texts = ["p%d" % i for i in range(500)]
        self.assertParity(texts, 10**6)
        self.assertParity(texts, 777)

    def test_many_empty_and_none_pages(self):
        texts: List[Optional[str]] = [None, ""] * 200
        self.assertEqual(self.assertParity(texts, 5), PDFTextExtraction("", True, False))
        self.assertParity(texts + ["tail"], 5)

    def test_whitespace_only_text_is_empty(self):
        self.assertEqual(
            self.assertParity([" ", "\n", "\t"], 100), PDFTextExtraction("", True, False)
        )

    def test_exhaustive_small_parity_sweep(self):
        alphabet: List[Optional[str]] = [None, "", " ", "a", "ab", "abc\n", "abcdef"]
        for length in range(0, 4):
            for combo in itertools.product(alphabet, repeat=length):
                for max_chars in range(0, 12):
                    self.assertParity(list(combo), max_chars)


class ExtractPdfErrorMappingTests(unittest.TestCase):
    def test_unavailable_when_pymupdf_import_fails(self):
        with patch.dict(sys.modules, {"pymupdf": None}):
            with self.assertRaises(PDFTextExtractorUnavailable):
                extract_pdf("synthetic.pdf")

    def test_page_error_maps_to_extraction_error(self):
        def boom() -> None:
            raise ValueError("private detail")

        doc = _FakeDoc([_FakePage("ok"), _FakePage("x", on_get_text=boom)])
        with patch.dict(sys.modules, {"pymupdf": _fake_pymupdf(doc)}):
            with self.assertRaises(PDFTextExtractionError) as ctx:
                extract_pdf("synthetic.pdf")
        self.assertEqual(ctx.exception.reason, safe_error_type(ValueError("x")))


if __name__ == "__main__":
    unittest.main()
