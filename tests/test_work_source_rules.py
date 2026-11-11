"""The video source rules the PostgreSQL source family uses (kept by PG-C2 #641).

``backend/work_source_sync.py`` holds the one parser that turns a source
intent into an identity, its bound, and the conflict shape. These are pure
functions; ``tests/pg/test_works_sync.py`` exercises them through the
PostgreSQL boundary.
"""

from __future__ import annotations

import unittest

from backend import work_metadata_sync as meta
from backend import work_source_sync as src

WATCH = "https://www.youtube.com/watch?v=%s"


class SourceRuleTests(unittest.TestCase):
    def test_one_parser_accepts_every_spelling_and_refuses_lookalikes(self) -> None:
        for url in (WATCH % "ABC", "https://youtu.be/ABC", "https://www.youtube.com/embed/ABC",
                    "https://m.youtube.com/watch?v=ABC"):
            with self.subTest(url=url):
                source = src.canonical_source({"kind": "video", "url": url})
                assert source is not None
                self.assertEqual((source["provider"], source["provider_id"]), ("youtube", "ABC"))
        for bad in ("https://notyoutube.com/watch?v=ABC", "https://youtube.com.evil.org/watch?v=A",
                    "https://example.com/video", "ftp://youtube.com/watch?v=A", "", "not a url"):
            with self.subTest(url=bad):
                self.assertIsNone(src.canonical_source({"kind": "video", "url": bad}))

    def test_the_payload_carries_intent_never_derived_values(self) -> None:
        for payload in ({"kind": "video", "url": WATCH % "A", "provider_id": "B"}, {"kind": "video"},
                        {"url": WATCH % "A"}, {"kind": "pdf", "url": WATCH % "A"}, "not an object", None):
            with self.subTest(payload=repr(payload)[:40]):
                self.assertIsNone(src.canonical_source(payload))

    def test_an_identifier_too_large_or_strange_to_report_has_no_identity(self) -> None:
        for bad in ("B" * (src.MAX_PROVIDER_ID_CHARS + 1), "B" * 3000, "%01" * 400, "ab%22cd", "ab%5Ccd",
                    "a%20b", "a.b"):
            with self.subTest(bad=bad[:20]):
                self.assertIsNone(src.canonical_source({"kind": "video", "url": WATCH % bad}))
        biggest = src.canonical_source({"kind": "video", "url": WATCH % ("B" * src.MAX_PROVIDER_ID_CHARS)})
        assert biggest is not None
        self.assertEqual(biggest["provider_id"], "B" * src.MAX_PROVIDER_ID_CHARS)

    def test_the_largest_representable_conflict_fits_the_durable_bound(self) -> None:
        biggest = "B" * src.MAX_PROVIDER_ID_CHARS
        current = src.canonical_source({"kind": "video", "url": WATCH % biggest + "&pad=" + "x" * 4000})
        desired = src.canonical_source({"kind": "video", "url": "https://youtu.be/"
                                        + "C" * src.MAX_PROVIDER_ID_CHARS + "?pad=" + "y" * 4000})
        assert current is not None and desired is not None
        result = {"work_id": "W-1", "code": "SOURCE_REVISION_CONFLICT", "current_revision": 2 ** 53 - 1}
        result.update(src.disagreement(current, desired))
        fitted = meta.fit_terminal_result(result, current["source_url"], desired["source_url"])
        self.assertLessEqual(meta.serialized_result_bytes(fitted), meta.MAX_DURABLE_RESULT_BYTES)
        self.assertEqual((fitted["current_provider_id"], fitted["current_provider"]), (biggest, "youtube"))
        self.assertLess(len(fitted["current_preview"]), len(current["source_url"]))


if __name__ == "__main__":
    unittest.main()
