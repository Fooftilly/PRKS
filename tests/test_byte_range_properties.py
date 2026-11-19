"""Fuzzing the PDF ``Range`` header parser (``backend.server.parse_byte_range``).

The PDF routes stream Work and Processing bytes with ``Range`` support, so the
header is attacker-chosen text read before any byte is sent. The contract,
from RFC 9110 §14, checked for every input:

* it never raises (a 500, or a dropped connection mid-stream);
* a 206 is one range inside the file and never the whole file;
* a single well-formed range answers exactly the bytes a slice of the file
  gives, or 416 when the slice is empty;
* anything else (another unit, signs, non-ASCII digits, ``last < first``,
  several ranges) is ignored: the whole file, 200, never a 416.

Profiles and replay: ``tests/hypothesis_profiles.py``.
"""

from __future__ import annotations

import re
import unittest

from hypothesis import assume, example, given, strategies as st

from backend.server import ByteRangeAnswer, parse_byte_range
from tests.hypothesis_profiles import FUZZ

_SIZES = st.integers(min_value=0, max_value=4096)
_POSITIONS = st.integers(min_value=0, max_value=10_000)


def _served(answer: ByteRangeAnswer, data: bytes) -> bytes | None:
    """The bytes the server sends for ``answer``, ``None`` for a 416."""
    if answer.status == 416:
        return None
    return data[answer.start:answer.end + 1]


class ByteRangeProperties(unittest.TestCase):
    @FUZZ
    @given(header=st.one_of(st.none(), st.text(), st.text().map(lambda t: "bytes=" + t)), size=_SIZES)
    @example(header="bytes=0-" + "9" * 5000, size=10)
    @example(header="bytes=-" + "9" * 5000, size=10)
    @example(header="bytes=" + "9" * 5000 + "-" + "8" * 5000, size=10)
    @example(header="bytes=" + "9" * 31 + "-" + "1" * 31, size=10)
    @example(header="bytes=٠-٤", size=10)
    def test_never_raises_and_a_partial_answer_is_inside_the_file(self, header: str | None, size: int) -> None:
        answer = parse_byte_range(header, size)
        self.assertIn(answer.status, (200, 206, 416))
        if answer.status == 200:
            self.assertEqual((answer.start, answer.end), (0, size - 1))
        if answer.status == 206:
            self.assertTrue(0 <= answer.start <= answer.end < size, answer)
            self.assertNotEqual((answer.start, answer.end), (0, size - 1))

    @FUZZ
    @given(first=_POSITIONS, last=st.one_of(st.none(), _POSITIONS), size=_SIZES,
           zeros=st.integers(min_value=0, max_value=3))
    @example(first=0, last=None, size=0, zeros=0)
    @example(first=5, last=3, size=10, zeros=0)
    def test_one_int_range_is_the_slice_it_names(self, first: int, last: int | None, size: int,
                                                 zeros: int) -> None:
        data = bytes(i % 251 for i in range(size))
        pad = "0" * zeros
        header = f"bytes={pad}{first}-" + ("" if last is None else f"{pad}{last}")
        answer = parse_byte_range(header, size)
        if last is not None and last < first:
            self.assertEqual(answer.status, 200, "an invalid range is ignored, not refused")
            return
        wanted = data[first:] if last is None else data[first:last + 1]
        self.assertEqual(_served(answer, data), wanted or None)

    @FUZZ
    @given(suffix=_POSITIONS, size=_SIZES)
    @example(suffix=0, size=10)
    @example(suffix=5, size=0)
    def test_one_suffix_range_is_the_tail_it_names(self, suffix: int, size: int) -> None:
        data = bytes(i % 251 for i in range(size))
        answer = parse_byte_range(f"bytes=-{suffix}", size)
        self.assertEqual(_served(answer, data), (data[-suffix:] if suffix else b"") or None)

    @FUZZ
    @given(spec=st.from_regex(r"\A[ \t]*[0-9]*-[0-9]*[ \t]*\Z"), size=_SIZES,
           noise=st.sampled_from(["+", "_", " ", "٠", "-", "x", "²"]),
           where=st.integers(min_value=0, max_value=10))
    def test_a_malformed_range_is_ignored(self, spec: str, size: int, noise: str, where: int) -> None:
        core = spec.strip(" \t")
        broken = core[:where] + noise + core[where:]
        assume(re.fullmatch(r"[0-9]*-[0-9]*", broken.strip(" \t"), re.ASCII) is None)
        self.assertEqual(parse_byte_range(f"bytes={broken}", size), ByteRangeAnswer(200, 0, size - 1))

    def test_ignored_headers_send_the_whole_file(self) -> None:
        for header in ("", "bytes", "bytes=", "bytes=-", "items=0-1", "bytes=+0-4", "bytes=1_0-20",
                       "bytes=٠-٤", "bytes=0-1,4-5", "bytes=5-3", "bytes=--5", "bytes=5--3",
                       "bytes=0-4;q=1", "bytes=" + "9" * 31 + "-" + "1" * 31,
                       "bytes=" + "9" * 5000 + "-" + "8" * 5000, "bytes=\u00b2-3", "bytes=0x1-2"):
            with self.subTest(header=header):
                self.assertEqual(parse_byte_range(header, 10), ByteRangeAnswer(200, 0, 9))

    def test_examples(self) -> None:
        for header, size, answer in (
            ("bytes=0-4", 10, ByteRangeAnswer(206, 0, 4)),
            ("Bytes=2-", 10, ByteRangeAnswer(206, 2, 9)),
            ("bytes= 3-5 ", 10, ByteRangeAnswer(206, 3, 5)),
            ("bytes=0-4,", 10, ByteRangeAnswer(206, 0, 4)),
            ("bytes=-3", 10, ByteRangeAnswer(206, 7, 9)),
            ("bytes=0-99", 10, ByteRangeAnswer(200, 0, 9)),
            ("bytes=-99", 10, ByteRangeAnswer(200, 0, 9)),
            ("bytes=10-", 10, ByteRangeAnswer(416)),
            ("bytes=-0", 10, ByteRangeAnswer(416)),
            ("bytes=0-", 0, ByteRangeAnswer(416)),
            ("bytes=" + "1" * 31 + "-" + "9" * 31, 10, ByteRangeAnswer(416)),
            ("bytes=0007-0008", 10, ByteRangeAnswer(206, 7, 8)),
        ):
            with self.subTest(header=header, size=size):
                self.assertEqual(parse_byte_range(header, size), answer)


if __name__ == "__main__":
    unittest.main()
