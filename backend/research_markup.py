"""Authoritative research-note markup parser.

Recognizes explicit [[concept:Name]] and [[argument:A-ID|Label]] references
outside fenced/inline code and escaped \\[[ sequences.

Python and the frontend preview parser must agree on which spans are references.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Tuple

CONCEPT_REF_MAX = 160
ARGUMENT_LABEL_MAX = 240
ARGUMENT_ID_RE = re.compile(r"^A-[A-Za-z0-9]{1,32}$")

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


def parse_research_markup(text: str) -> ResearchMarkup:
    if not text:
        return ResearchMarkup((), ())
    live = _live_mask(text)
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
        if i + 11 <= n and live[i:i + 11] == "Y" * 11 and text.startswith("[[concept:", i):
            parsed = _try_concept_ref(text, live, i)
            if parsed is not None:
                concepts.append(parsed)
                i = parsed.end
                continue
        if i + 12 <= n and text.startswith("[[argument:", i):
            parsed_arg = _try_argument_ref(text, live, i)
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


def _try_concept_ref(text: str, live: str, start: int) -> Optional[ConceptRef]:
    inner_start = start + len("[[concept:")
    close = text.find("]]", inner_start)
    if close < 0:
        return None
    end = close + 2
    if not _span_live(live, start, end):
        return None
    if "\n" in text[start:end]:
        return None
    inner = text[inner_start:close]
    name = canonical_concept_name(inner)
    if not name or len(name) > CONCEPT_REF_MAX:
        return None
    return ConceptRef(start=start, end=end, raw=text[start:end], name=name)


def _try_argument_ref(text: str, live: str, start: int) -> Optional[ArgumentRef]:
    inner_start = start + len("[[argument:")
    close = text.find("]]", inner_start)
    if close < 0:
        return None
    end = close + 2
    if not _span_live(live, start, end):
        return None
    if "\n" in text[start:end]:
        return None
    inner = text[inner_start:close]
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
