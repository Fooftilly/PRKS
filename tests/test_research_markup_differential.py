"""Differential fuzzing: the Python and JavaScript research-markup parsers agree.

``backend/domain/research/research_markup.py`` decides which spans of a note are Concept and
Argument references (the research index and graph); ``frontend/js/research-links.js``
decides the same for the preview. ``tests/fixtures/research_markup.json`` pins
chosen cases; these properties draw notes from the markers, code fences,
escapes, percent-encodings and whitespace both parsers branch on and require
the same references, at the same code-point offsets, with the same names and
labels. A second pair of properties requires the preview's Concept key to
group names exactly as ``normalize_concept_key`` (``prks.identity_key``) does.

The JavaScript side runs in one Node process per test class
(``tests/browser/research_markup_oracle.js``). Profiles and replay:
``tests/hypothesis_profiles.py``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import unicodedata
import unittest
from collections import defaultdict
from typing import Any

from hypothesis import example, given, strategies as st

from backend.domain.research.research_markup import normalize_concept_key, parse_research_markup
from tests.hypothesis_profiles import FUZZ

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ORACLE = os.path.join(_PROJECT_DIR, "tests", "browser", "research_markup_oracle.js")

# Python's whitespace (str.split) and JavaScript's \s, both sides of the difference.
_SPACES = ["\t", "\n", "\x0b", "\x0c", "\r", "\x1c", "\x1f", " ", "\x85", "\xa0", " ", " ",
           " ", " ", " ", " ", " ", "　", "﻿", "​"]
_TOKENS = [
    "[[concept:", "[[concept-encoded:", "[[argument:", "]]", "]", "[", "[[", "\\", "\\[[", "|",
    "`", "``", "```", "~~~", "    ", "\n```\n", "\n~~~~\n", "A-1", "A-x9", "A-", "a-1",
    "%41", "%e2%82%ac", "%C3", "%ZZ", "%", "%00", "%7F", "%C2%85", "Concept", "ß", "ẞ", "ı", "İ",
    "Σ", "ς", "é", "\U0001f600", "x" * 30, "y" * 170,
] + _SPACES
# No surrogates: PostgreSQL text cannot hold a lone one, and JSON would pair two.
_CHARS = st.characters(blacklist_categories=("Cs",))
_NOTES = st.one_of(
    st.lists(st.one_of(st.sampled_from(_TOKENS), st.text(_CHARS, max_size=3)), max_size=40).map("".join),
    st.text(_CHARS, max_size=200),
)
_NAME_PIECES = ["ß", "ss", "SS", "ẞ", "ı", "i", "I", "İ", "i̇", "Σ", "σ", "ς", "ſ", "K", "k",
                "K", "Å", "Å", "å", "é", "é", "ﬁ", "fi", "Ꭰ", "ꭰ", "ǅ", "ǆ", "Ǆ",
                "µ", "μ", "ϐ", "β", "ẞ", "ͅ", "ι", " ", " ", "\x1c", "﻿", "a", "A"]
_NAMES = st.lists(st.one_of(st.sampled_from(_NAME_PIECES), st.text(_CHARS, max_size=2)),
                  max_size=8).map("".join)


def _python_parse(text: str) -> dict[str, list[list[Any]]]:
    markup = parse_research_markup(text)
    return {
        "concepts": [[r.start, r.end, r.name] for r in markup.concept_refs],
        "arguments": [[r.start, r.end, r.argument_id, r.label] for r in markup.argument_refs],
    }


class ResearchMarkupDifferential(unittest.TestCase):
    oracle: subprocess.Popen[str]

    @classmethod
    def setUpClass(cls) -> None:
        node = shutil.which("node")
        assert node is not None, "node is required for the research-markup differential"
        cls.oracle = subprocess.Popen([node, _ORACLE], cwd=_PROJECT_DIR, stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, text=True, encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        assert cls.oracle.stdin is not None
        cls.oracle.stdin.close()
        cls.oracle.wait(timeout=30)

    def ask(self, request: dict[str, Any]) -> dict[str, Any]:
        assert self.oracle.stdin is not None and self.oracle.stdout is not None
        self.oracle.stdin.write(json.dumps(request) + "\n")
        self.oracle.stdin.flush()
        return json.loads(self.oracle.stdout.readline())

    @FUZZ
    @given(text=_NOTES)
    @example(text="[[concept:a\x1cb]]")
    @example(text="[[concept:﻿X]]")
    @example(text="[[argument:A-1|" + "\U0001f600" * 130 + "]]")
    @example(text="[[argument:A-1|﻿x﻿]]")
    @example(text="[[argument:\x1cA-1]]")
    @example(text="`[[concept:a]]` [[concept:b]]\n```\n[[argument:A-1]]\n```\n\\[[concept:c]]")
    def test_both_parsers_find_the_same_references(self, text: str) -> None:
        self.assertEqual(self.ask({"parse": text}), _python_parse(text), repr(text))

    @FUZZ
    @given(names=st.lists(_NAMES, min_size=2, max_size=6))
    @example(names=["Straße", "STRASSE", "straẞe"])
    @example(names=["ı", "i", "I", "İ"])
    def test_the_preview_groups_concept_names_like_the_index(self, names: list[str]) -> None:
        js_keys = self.ask({"keys": names})["keys"]
        py_keys = [normalize_concept_key(name) for name in names]
        for a in range(len(names)):
            for b in range(a + 1, len(names)):
                self.assertEqual(js_keys[a] == js_keys[b], py_keys[a] == py_keys[b],
                                 (names[a], names[b], py_keys[a], py_keys[b], js_keys[a], js_keys[b]))

    def test_every_assigned_code_point_folds_into_the_same_class(self) -> None:
        """Case folding is per code point, so agreement on each one is the base case."""
        started = time.monotonic()
        chars = [chr(c) for c in range(0x110000)
                 if not 0xD800 <= c < 0xE000 and unicodedata.category(chr(c)) != "Cn"]
        js_keys = self.ask({"keys": chars})["keys"]
        by_python: dict[str, set[str]] = defaultdict(set)
        by_js: dict[str, set[str]] = defaultdict(set)
        for char, js_key in zip(chars, js_keys):
            by_python[normalize_concept_key(char)].add(char)
            by_js[js_key].add(char)
        python_classes = {frozenset(group) for group in by_python.values()}
        js_classes = {frozenset(group) for group in by_js.values()}
        differing = sorted((sorted(group) for group in python_classes ^ js_classes), key=lambda g: g[0])
        self.assertEqual(differing[:10], [])
        self.assertLess(time.monotonic() - started, 60)


if __name__ == "__main__":
    unittest.main()
