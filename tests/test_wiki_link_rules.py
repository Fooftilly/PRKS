"""The pure ``[[wiki link]]`` marker scan (kept by PG-C2 #641).

``backend.domain.works.rules.wiki_link_spans`` finds the markers both the
PostgreSQL renderer and the retired SQLite one render; it needs no database.
Resolution itself is checked on PostgreSQL in ``tests/pg/test_works_wiki_links.py``.
"""

from __future__ import annotations

import itertools
import re
import time
import unittest

from backend.domain.works.rules import wiki_link_spans


class WikiLinkSpanTests(unittest.TestCase):
    def test_spans_are_the_markers_the_pattern_finds(self) -> None:
        pattern = re.compile(r"\[\[(.*?)\]\]")
        for size in range(8):
            for chars in itertools.product("[]\na", repeat=size):
                text = "".join(chars)
                expected = [(m.start(), m.end(), m.group(1)) for m in pattern.finditer(text)]
                self.assertEqual(list(wiki_link_spans(text)), expected, repr(text))

    def test_unclosed_markers_scan_in_linear_time(self) -> None:
        # The pattern took about 30 s on 40,000 unclosed markers.
        for text in ("[[" * 200_000, "[[x\n" * 100_000 + "]]", "[[" * 100_000 + "\n[[a]]"):
            started = time.monotonic()
            spans = list(wiki_link_spans(text))
            self.assertLess(time.monotonic() - started, 2.0)
            self.assertEqual(spans, [(len(text) - 5, len(text), "a")] if text.endswith("[[a]]") else [])


if __name__ == "__main__":
    unittest.main()
