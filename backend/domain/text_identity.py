"""Text identity keys (``docs/postgresql-architecture.md`` §8.1, PG9).

One rule decides when two names are "the same name" for uniqueness::

    identity_key(s) = casefold(collapse_whitespace(trim(NFC(s))))

It is the Python twin of the SQL function ``prks.identity_key``
(``backend/db/schema/000_extensions.sql``). The two must agree on every vector
in ``tests/fixtures/text_identity_vectors.json``; the PostgreSQL tests run the
same file through SQL. The rule:

- composes to NFC first, so a decomposed and a precomposed letter are one name;
- treats exactly :data:`WHITESPACE` as whitespace (what ``str.split()`` splits
  on: Unicode White_Space plus U+001C..U+001F), trims it and collapses each run
  to one space, which is today's Concept rule (``research_markup.py``);
- applies Unicode full case folding (``"Straße"`` -> ``"strasse"``, final
  sigma -> sigma), and nothing else: no NFKC, no diacritic stripping.

The key is not re-normalized after folding, on either side.

Unicode version skew: Python and PostgreSQL can ship different Unicode data
(Python 3.12 has 15.0, PostgreSQL 18 has 16.0). Unicode's case-folding
stability policy keeps the folding of an assigned character fixed, so the two
can only disagree on a code point one side does not know yet. This module
therefore refuses text holding a code point unassigned in Python's
``unicodedata`` (:class:`UnsupportedTextError`) instead of returning a key
that ``prks.identity_key`` might not produce. The stored key is always the
SQL one.
"""

from __future__ import annotations

import re
import unicodedata

# Kept character-for-character equal to the class in prks.identity_key.
WHITESPACE = (
    "\x09\x0a\x0b\x0c\x0d\x1c\x1d\x1e\x1f\x20"
    "\x85\xa0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)

_WHITESPACE_RUN = re.compile("[" + re.escape(WHITESPACE) + "]+")


class UnsupportedTextError(ValueError):
    """The text holds a code point this Python's Unicode data does not know,
    or one PostgreSQL text cannot store (NUL, a lone surrogate)."""


def identity_key(value: str) -> str:
    """The identity key of ``value``; equal keys mean the same name.

    Raises :class:`UnsupportedTextError` for a code point unassigned in
    ``unicodedata.unidata_version`` (see the module docstring), for NUL and
    for a lone surrogate, none of which ``prks.identity_key`` can receive.
    """
    for char in value:
        category = unicodedata.category(char)
        if category == "Cn":
            raise UnsupportedTextError(
                f"U+{ord(char):04X} is not assigned in Unicode {unicodedata.unidata_version}"
            )
        if char == "\x00" or category == "Cs":
            # PostgreSQL text cannot hold NUL, and a lone surrogate is not UTF-8.
            raise UnsupportedTextError(f"U+{ord(char):04X} cannot be stored as PostgreSQL text")
    text = unicodedata.normalize("NFC", value)
    text = _WHITESPACE_RUN.sub(" ", text).strip(" ")
    return text.casefold()
