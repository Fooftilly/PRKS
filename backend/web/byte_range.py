"""The PDF ``Range`` header (RFC 9110 §14), as a pure function.

Every Work and Processing PDF route reads it before streaming
(``backend.server``). It is kept free of the server so its unit and
property tests (``tests/test_byte_range_properties.py``) can be a mutation
testing target (``scripts/ci/mutation_testing.py``).
"""

from __future__ import annotations

from dataclasses import dataclass

# Longer digit runs are clamped: no file is that large, and int() refuses
# strings past sys.get_int_max_str_digits().
_BYTE_RANGE_MAX_DIGITS = 30


@dataclass(frozen=True)
class ByteRangeAnswer:
    """How to answer a GET for ``size`` bytes: 200 whole, 206 ``start..end``, or 416."""

    status: int
    start: int = 0
    end: int = -1


def _is_byte_position(text: str) -> bool:
    """1*DIGIT, ASCII only (``str.isdigit`` alone accepts other scripts' digits)."""
    return text.isascii() and text.isdigit()


def _significant(digits: str) -> str:
    return digits.lstrip("0") or "0"


def _position_order(digits: str) -> tuple[int, str]:
    """Orders decimal positions of any length without converting them."""
    significant = _significant(digits)
    return (len(significant), significant)


def _byte_position(digits: str) -> int:
    significant = _significant(digits)
    if len(significant) > _BYTE_RANGE_MAX_DIGITS:
        return 10 ** _BYTE_RANGE_MAX_DIGITS
    return int(significant)


def parse_byte_range(header: str | None, size: int) -> ByteRangeAnswer:
    """The answer to a ``Range`` header for a representation of ``size`` bytes (RFC 9110 §14).

    * No header, another unit, a malformed or invalid range-set (signs, digits
      outside ASCII, ``last-pos < first-pos``), or more than one range: the
      header is ignored and the whole file is sent (200). A multi-range
      request would need ``multipart/byteranges``, which is not served.
    * One satisfiable range that is not the whole file: 206 with inclusive
      ``start``/``end``, ``end`` clamped to the last byte.
    * One valid range that is unsatisfiable (``first-pos >= size``, a
      zero-length suffix, any range of an empty file): 416.
    """
    whole = ByteRangeAnswer(200, 0, size - 1)
    if not header:
        return whole
    unit, sep, range_set = header.strip(" \t").partition("=")
    if not sep or unit.lower() != "bytes":
        return whole
    specs = [spec.strip(" \t") for spec in range_set.split(",")]
    specs = [spec for spec in specs if spec]
    if len(specs) != 1:
        return whole
    first, dash, last = specs[0].partition("-")
    if not dash or not (first or last):
        return whole
    if (first and not _is_byte_position(first)) or (last and not _is_byte_position(last)):
        return whole
    if first and last and _position_order(last) < _position_order(first):
        return whole
    if first == "":
        suffix = _byte_position(last)
        if suffix == 0 or size == 0:
            return ByteRangeAnswer(416)
        start, end = max(0, size - suffix), size - 1
    else:
        start = _byte_position(first)
        end = _byte_position(last) if last else size - 1
        if start >= size:
            return ByteRangeAnswer(416)
        end = min(end, size - 1)
    if start == 0 and end == size - 1:
        return whole
    return ByteRangeAnswer(206, start, end)
