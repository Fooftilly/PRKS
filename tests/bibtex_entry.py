"""A structural check that an exported string is exactly one BibTeX entry.

BibTeX balances braces without regard to a preceding backslash, so ``\\{``
counts like ``{`` here too. An entry is valid only when it is a header, then
``  name = {value},`` fields whose value closes exactly at its ``},``, then
the closing brace as the last character.
"""

from __future__ import annotations

import re
import unittest

_HEADER = re.compile(r"@([a-z]+)\{([\w\-.:]+),\n")
_FIELD = re.compile(r"  [a-z]+ = \{")


def assert_one_bibtex_entry(case: unittest.TestCase, entry: str) -> str:
    """Fail ``case`` unless ``entry`` is one well-formed entry; returns its key."""
    header = _HEADER.match(entry)
    case.assertIsNotNone(header, f"no entry header in {entry!r}")
    assert header is not None
    index = header.end()
    while index < len(entry) - 1:
        field = _FIELD.match(entry, index)
        case.assertIsNotNone(field, f"stray text at {index} in {entry!r}")
        assert field is not None
        index, depth = field.end(), 1
        while depth:
            case.assertLess(index, len(entry), f"field never closes in {entry!r}")
            depth += {"{": 1, "}": -1}.get(entry[index], 0)
            index += 1
        case.assertEqual(entry[index:index + 2], ",\n", f"field closes early at {index} in {entry!r}")
        index += 2
    case.assertEqual(entry[index:], "}", f"entry does not close at its end: {entry!r}")
    return header.group(2)
