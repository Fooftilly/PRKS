"""Authoritative research-note markup parser.

Recognizes explicit [[concept:Name]], [[concept-encoded:URIComponent(Name)]]
and [[argument:A-ID|Label]] references outside fenced/inline code and escaped
\\[[ sequences. The encoded spelling is unambiguous for embedded delimiters;
legacy Concept names (including literal backslashes) are never reinterpreted.

Python and the frontend preview parser must agree on which spans are references.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Tuple
from urllib.parse import unquote

CONCEPT_REF_MAX = 160
ARGUMENT_LABEL_MAX = 240
ARGUMENT_ID_RE = re.compile(r"^A-[A-Za-z0-9]{1,32}$")
# Explicitly versioned marker: no legacy literal backslash or bracket sequence
# in [[concept:Name]] can be confused with the encoded form.
_ENCODED_CONCEPT_PREFIX = "[[concept-encoded:"
_ENCODED_BODY_MAX = CONCEPT_REF_MAX * 12
# A scanner is easier to audit than a backtracking regex for untrusted notes.
_URL_COMPONENT_SAFE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.!~*'()-")
_URL_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")

_FENCE_CHARS = frozenset("`~")


@dataclass(frozen=True)
class ConceptRef:
    start: int
    end: int
    raw: str
    name: str


@dataclass(frozen=True)
class ArgumentRef:
    start: int
    end: int
    raw: str
    argument_id: str
    label: str


@dataclass(frozen=True)
class ResearchMarkup:
    concept_refs: Tuple[ConceptRef, ...]
    argument_refs: Tuple[ArgumentRef, ...]


def canonical_concept_name(raw: str) -> str:
    text = unicodedata.normalize("NFC", raw or "")
    return " ".join(text.split())


def normalize_concept_key(raw: str) -> str:
    return canonical_concept_name(raw).casefold()


class _NextOccurrence:
    """The first index of ``needle`` at or after ``start``, for a scan whose
    ``start`` never decreases: each part of the text is searched once, so a
    note full of unclosed markers stays linear."""

    __slots__ = ("_text", "_needle", "_found")

    def __init__(self, text: str, needle: str) -> None:
        self._text = text
        self._needle = needle
        self._found = -2

    def at_or_after(self, start: int) -> int:
        if self._found == -1 or self._found >= start:
            return self._found
        self._found = self._text.find(self._needle, start)
        return self._found


@dataclass(frozen=True)
class _Scan:
    text: str
    live: str
    close: _NextOccurrence
    newline: _NextOccurrence
    dead: _NextOccurrence


def _marker_end(scan: _Scan, start: int, inner_start: int) -> Optional[int]:
    """The end of a legacy or argument marker at ``start``: its first ``]]``,
    with the whole span on one line and outside code. ``None`` otherwise."""
    close = scan.close.at_or_after(inner_start)
    if close < 0:
        return None
    end = close + 2
    newline = scan.newline.at_or_after(start)
    if 0 <= newline < end:
        return None
    dead = scan.dead.at_or_after(start)
    if 0 <= dead < end:
        return None
    return end


def parse_research_markup(text: str) -> ResearchMarkup:
    if not text:
        return ResearchMarkup((), ())
    live = _live_mask(text)
    scan = _Scan(text, live, _NextOccurrence(text, "]]"), _NextOccurrence(text, "\n"),
                 _NextOccurrence(live, "N"))
    concepts: List[ConceptRef] = []
    arguments: List[ArgumentRef] = []
    n = len(text)
    i = 0
    while i < n:
        if live[i] != "Y":
            i += 1
            continue
        if text[i] == "\\" and i + 2 < n and text[i + 1] == "[" and text[i + 2] == "[":
            i += 3
            continue
        if text.startswith(_ENCODED_CONCEPT_PREFIX, i):
            parsed = _try_encoded_concept_ref(text, live, i)
            if parsed is not None:
                concepts.append(parsed)
                i = parsed.end
                continue
        if i + 11 <= n and live[i:i + 11] == "Y" * 11 and text.startswith("[[concept:", i):
            parsed = _try_concept_ref(scan, i)
            if parsed is not None:
                concepts.append(parsed)
                i = parsed.end
                continue
        if i + 12 <= n and text.startswith("[[argument:", i):
            parsed_arg = _try_argument_ref(scan, i)
            if parsed_arg is not None:
                arguments.append(parsed_arg)
                i = parsed_arg.end
                continue
        i += 1
    return ResearchMarkup(tuple(concepts), tuple(arguments))


def _at_line_start(text: str, i: int) -> bool:
    return i == 0 or text[i - 1] == "\n"


def _live_mask(text: str) -> str:
    n = len(text)
    flags = ["Y"] * n
    i = 0
    while i < n:
        if _at_line_start(text, i):
            fence = _open_fence(text, i)
            if fence is not None:
                start, closer = fence
                for k in range(start, closer):
                    flags[k] = "N"
                i = closer
                continue
        if text[i] == "`":
            inline = _close_inline_code(text, i)
            if inline is not None:
                end = inline
                for k in range(i, end):
                    flags[k] = "N"
                i = end
                continue
        i += 1
    return "".join(flags)


def _open_fence(text: str, i: int) -> Optional[Tuple[int, int]]:
    n = len(text)
    j = i
    spaces = 0
    while spaces < 3 and j < n and text[j] == " ":
        spaces += 1
        j += 1
    if j >= n or text[j] not in _FENCE_CHARS:
        return None
    ch = text[j]
    k = j
    while k < n and text[k] == ch:
        k += 1
    flen = k - j
    if flen < 3:
        return None
    while k < n and text[k] != "\n":
        k += 1
    if k < n:
        k += 1
    closer = _find_fence_close(text, k, ch, flen)
    return (i, closer)


def _find_fence_close(text: str, start: int, ch: str, flen: int) -> int:
    n = len(text)
    i = start
    while i < n:
        if _at_line_start(text, i):
            j = i
            spaces = 0
            while spaces < 3 and j < n and text[j] == " ":
                spaces += 1
                j += 1
            k = j
            while k < n and text[k] == ch:
                k += 1
            if k - j >= flen:
                rest = k
                while rest < n and text[rest] in " \t":
                    rest += 1
                if rest >= n or text[rest] == "\n":
                    return n if rest >= n else rest + 1
        i += 1
    return n


def _close_inline_code(text: str, i: int) -> Optional[int]:
    n = len(text)
    if i >= n or text[i] != "`":
        return None
    k = i
    while k < n and text[k] == "`":
        k += 1
    run = k - i
    if run >= 3 and _at_line_start(text, i):
        return None
    j = k
    while j < n:
        if text[j] == "`":
            m = j
            while m < n and text[m] == "`":
                m += 1
            if m - j == run:
                return m
            j = m
            continue
        j += 1
    return None


def _span_live(live: str, start: int, end: int) -> bool:
    return live[start:end] == "Y" * (end - start)


def _try_concept_ref(scan: _Scan, start: int) -> Optional[ConceptRef]:
    # Legacy references remain completely literal. In particular a backslash
    # immediately before a bracket is part of the name, not a new escape.
    text = scan.text
    inner_start = start + len("[[concept:")
    end = _marker_end(scan, start, inner_start)
    if end is None:
        return None
    name = canonical_concept_name(text[inner_start:end - 2])
    if not name or len(name) > CONCEPT_REF_MAX:
        return None
    return ConceptRef(start=start, end=end, raw=text[start:end], name=name)


def _valid_encoded_concept_body(body: str) -> bool:
    """Validate percent-encoded UTF-8 syntax in linear time."""
    if not body:
        return False
    i = 0
    while i < len(body):
        if body[i] == "%":
            if i + 2 >= len(body) or body[i + 1] not in _URL_HEX_DIGITS or body[i + 2] not in _URL_HEX_DIGITS:
                return False
            i += 3
        elif body[i] in _URL_COMPONENT_SAFE:
            i += 1
        else:
            return False
    return True


def _try_encoded_concept_ref(text: str, live: str, start: int) -> Optional[ConceptRef]:
    inner_start = start + len(_ENCODED_CONCEPT_PREFIX)
    close = text.find("]]", inner_start, inner_start + _ENCODED_BODY_MAX + 2)
    if close < 0:
        return None
    end = close + 2
    if not _span_live(live, start, end):
        return None
    body = text[inner_start:close]
    if not _valid_encoded_concept_body(body):
        return None
    try:
        decoded = unquote(body, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    # Concept creation rejects C0 controls and DEL, but permits C1 (except
    # U+0085, which canonical_concept_name folds to a space). Keep both
    # boundaries aligned so existing valid names remain referenceable.
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in decoded):
        return None
    name = canonical_concept_name(decoded)
    if not name or len(name) > CONCEPT_REF_MAX:
        return None
    return ConceptRef(start=start, end=end, raw=text[start:end], name=name)


def _try_argument_ref(scan: _Scan, start: int) -> Optional[ArgumentRef]:
    text = scan.text
    inner_start = start + len("[[argument:")
    end = _marker_end(scan, start, inner_start)
    if end is None:
        return None
    inner = text[inner_start:end - 2]
    pipe = inner.find("|")
    if pipe >= 0:
        id_raw = inner[:pipe].strip()
        label = inner[pipe + 1 :].strip()
    else:
        id_raw = inner.strip()
        label = ""
    if not ARGUMENT_ID_RE.fullmatch(id_raw):
        return None
    if len(label) > ARGUMENT_LABEL_MAX:
        return None
    return ArgumentRef(
        start=start,
        end=end,
        raw=text[start:end],
        argument_id=id_raw,
        label=label,
    )
