"""CSL-JSON items from a citation record (``backend.domain.works.csl_format``, #41)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from backend.dependency_gate import CSL_VENDOR_FILES
from backend.domain.works.citation_styles import CITATION_STYLES, DEFAULT_CITATION_STYLE
from backend.domain.works.csl_format import CSL_TYPES, format_csl
from backend.work_metadata_sync import DOC_TYPES

_ROOT = Path(__file__).resolve().parents[1]


def item(work=None, roles=(), *, video=False):
    return format_csl("W-1", {"title": "T", **(work or {})}, list(roles), video=video)


def role(role_type, first="", last="", credit=""):
    return {"role_type": role_type, "first_name": first, "last_name": last, "credit_name": credit}


class CslFormatTests(unittest.TestCase):
    def test_every_entry_type_has_a_csl_type(self):
        self.assertEqual(set(CSL_TYPES), set(DOC_TYPES))
        for doc_type in DOC_TYPES:
            self.assertEqual(item({"doc_type": doc_type})["type"], CSL_TYPES[doc_type])
        self.assertEqual(item({"doc_type": "nonsense"})["type"], "document")
        self.assertEqual(item({"doc_type": "phdthesis"})["genre"], "PhD thesis")
        self.assertEqual(item({"doc_type": "mastersthesis"})["genre"], "Master's thesis")

    def test_a_stream_is_a_motion_picture_whose_text_credit_is_one_name(self):
        cited = item({"doc_type": "online", "author_text": "Some Channel; Ltd"}, video=True)
        self.assertEqual(cited["type"], "motion_picture")
        self.assertEqual(cited["author"], [{"literal": "Some Channel; Ltd"}])

    def test_credits_cite_in_role_order_and_prefer_the_credit_name(self):
        cited = item(roles=[
            role("Author", "Ada", "Byron"),
            role("Editor", credit="Smith, J."),
            role("Author", credit="Grace Brewster Hopper"),
            role("Translator", last="Plato"),
            role("Foreword", "Not", "Cited"),
            role("Author"),
        ], work={"author_text": "Ignored"})
        self.assertEqual(cited["author"], [{"family": "Byron", "given": "Ada"},
                                           {"family": "Hopper", "given": "Grace Brewster"}])
        self.assertEqual(cited["editor"], [{"family": "Smith", "given": "J."}])
        self.assertEqual(cited["translator"], [{"family": "Plato"}])

    def test_with_no_linked_author_the_text_credit_is_split_on_semicolons(self):
        cited = item({"author_text": " Doe,  Jane ;Madonna; ; Roe, "})
        self.assertEqual(cited["author"], [{"family": "Doe", "given": "Jane"}, {"family": "Madonna"},
                                           {"family": "Roe"}])
        self.assertNotIn("author", item({"author_text": " ; "}))

    def test_issued_takes_the_valid_part_of_the_date_else_the_year(self):
        self.assertEqual(item({"published_date": "2020-05-02"})["issued"], {"date-parts": [[2020, 5, 2]]})
        self.assertEqual(item({"published_date": "2020-13-02"})["issued"], {"date-parts": [[2020]]})
        self.assertEqual(item({"published_date": "2020-04-40"})["issued"], {"date-parts": [[2020, 4]]})
        self.assertEqual(item({"published_date": "", "year": "1999"})["issued"], {"date-parts": [[1999]]})
        self.assertEqual(item({"published_date": "spring", "year": "c. 1900"}).get("issued"), None)
        # A standalone year may have fewer than four digits; a date may not.
        self.assertEqual(item({"year": "950"})["issued"], {"date-parts": [[950]]})
        self.assertEqual(item({"year": "2020–2021"})["issued"], {"date-parts": [[2020], [2021]]})
        self.assertEqual(item({"year": "1998-99"})["issued"], {"date-parts": [[1998], [1999]]})
        self.assertEqual(item({"year": "1999–00"})["issued"], {"date-parts": [[1999], [2000]]})
        self.assertEqual(item({"year": "950–951"})["issued"], {"date-parts": [[950], [951]]})
        self.assertEqual(item({"year": "950-51"})["issued"], {"date-parts": [[950], [951]]})
        # A Year that reads as an ISO month is that month, never a range into the next century.
        self.assertEqual(item({"year": "2020-05"})["issued"], {"date-parts": [[2020, 5]]})
        self.assertEqual(item({"year": "2020-12-24"})["issued"], {"date-parts": [[2020, 12, 24]]})
        self.assertEqual(item({"year": "2020-21"})["issued"], {"date-parts": [[2020], [2021]]})
        self.assertEqual(item({"year": "399 BCE"})["issued"], {"date-parts": [[-399]]})
        self.assertEqual(item({"year": "44 bc"})["issued"], {"date-parts": [[-44]]})
        self.assertEqual(item({"year": "800 AD"})["issued"], {"date-parts": [[800]]})
        for unparsed in ("12345", "19th century", "c. 1900", "2021–2020", "1990s"):
            with self.subTest(year=unparsed):
                self.assertEqual(item({"year": unparsed}).get("issued"), None)
        self.assertEqual(item({"published_date": "950-01-01"}).get("issued"), None)

    def test_fields_map_and_empty_ones_are_left_out(self):
        cited = item({"journal": " J ", "publisher": "", "location": "Paris;  ;New   York", "pages": "3--4",
                      "isbn": "1", "doi": "10.1/x", "abstract": "A", "edition": "2", "volume": "", "issue": "7"})
        self.assertEqual({k: v for k, v in cited.items() if k not in ("id", "type", "title")}, {
            "container-title": "J", "publisher-place": "Paris; New York", "page": "3-4", "ISBN": "1",
            "DOI": "10.1/x", "abstract": "A", "edition": "2", "issue": "7"})
        self.assertNotIn("title", format_csl("W-2", {"title": "  "}, [], video=False))

    def test_accessed_is_the_last_edit_date_only_beside_a_url(self):
        self.assertEqual(item({"source_url": "https://e.x", "updated_at": "2026-11-21T10:00:00Z"})["accessed"],
                         {"date-parts": [[2026, 11, 21]]})
        self.assertNotIn("accessed", item({"updated_at": "2026-11-21T10:00:00Z"}))
        self.assertNotIn("accessed", item({"source_url": "https://e.x", "updated_at": "2026"}))


class CitationExportLimitTests(unittest.TestCase):
    def test_the_client_limit_is_the_bulk_selection_limit(self):
        from backend.domain.works.rules import PRKS_BULK_WORK_MAX

        source = (_ROOT / "frontend-app" / "src" / "api" / "citations.ts").read_text(encoding="utf-8")
        self.assertIn(f"export const CITATION_EXPORT_MAX = {PRKS_BULK_WORK_MAX}", source)


class CitationStyleRegistryTests(unittest.TestCase):
    def test_the_vue_registry_lists_the_same_styles_in_the_same_order(self):
        source = (_ROOT / "frontend-app" / "src" / "features" / "citations" / "styles.ts").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"\{ id: '([^']+)', label:", source), list(CITATION_STYLES))
        self.assertIn(f"DEFAULT_CITATION_STYLE = '{DEFAULT_CITATION_STYLE}'", source)

    def test_every_style_is_vendored_and_registered(self):
        self.assertEqual([f"csl/{style}.csl" for style in CITATION_STYLES] + ["csl/locales-en-US.xml"],
                         list(CSL_VENDOR_FILES))
        for style in CITATION_STYLES:
            text = (_ROOT / "frontend" / "vendor" / "csl" / f"{style}.csl").read_text(encoding="utf-8")
            with self.subTest(style=style):
                self.assertIn('xmlns="http://purl.org/net/xbiblio/csl"', text)
                # An independent style: a dependent one would need its parent too.
                self.assertNotIn('rel="independent-parent"', text)
                self.assertIn("<bibliography", text)


if __name__ == "__main__":
    unittest.main()
