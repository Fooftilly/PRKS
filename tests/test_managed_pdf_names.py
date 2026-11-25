"""The managed-PDF name rules (``backend/pdf/managed_pdf_names.py``).

Pure rules with no database: which ``/api/pdfs/`` spellings name a managed
file, which basename a row owns or protects, and the storage key a route name gives.
Moved out of ``tests/test_work_deletion.py`` and ``tests/test_db_manager.py``
when PG-C2 (#641) retired their SQLite tests; the rules stay in use on PostgreSQL.
"""

import unittest

from backend.pdf.managed_pdf_names import (
    managed_basenames_protected_by,
    managed_pdf_filename,
    owned_managed_pdf_basename,
    referenced_managed_pdf_filename,
    row_references_managed_pdf,
    row_strongly_references_managed_pdf,
    managed_pdf_key,
)
from backend.storage.objects import NAMESPACE_ASSET_OBJECTS


class TestManagedPdfPathHelpers(unittest.TestCase):
    def test_managed_pdf_filename_canonical_success(self):
        self.assertEqual(managed_pdf_filename("/api/pdfs/example.pdf"), "example.pdf")

    def test_managed_pdf_filename_rejects_noncanonical(self):
        rejected = (
            "/api/pdfs/../example.pdf",
            "/api/pdfs/sub/example.pdf",
            "/api/pdfs/foo%2Fexample.pdf",
            "/api/pdfs/foo%5Cexample.pdf",
            "/api/pdfs/..",
            "/api/pdfs/%2e%2e",
            " /api/pdfs/victim.pdf ",
            "/api/pdfs/bad\x00name.pdf",
            # urlparse strips these before the HTTP PDF route sees the path.
            "/api/pdfs/example.pdf?x",
            "/api/pdfs/example.pdf;bar",
            "/api/pdfs/example.pdf#frag",
            "/api/pdfs/foo.pdf?x=1#y",
        )
        for path in rejected:
            with self.subTest(path=path):
                self.assertIsNone(managed_pdf_filename(path))

    def test_owned_managed_pdf_basename_keeps_legacy_delimiters(self):
        """Physical cleanup ownership still names delimiter-bearing basenames."""
        self.assertEqual(
            owned_managed_pdf_basename("/api/pdfs/legacy.pdf?x"),
            "legacy.pdf?x",
        )
        self.assertEqual(
            owned_managed_pdf_basename("/api/pdfs/legacy.pdf;bar"),
            "legacy.pdf;bar",
        )
        self.assertEqual(
            owned_managed_pdf_basename("/api/pdfs/legacy.pdf#frag"),
            "legacy.pdf#frag",
        )
        # Route-addressable rules still refuse the same spellings.
        self.assertIsNone(managed_pdf_filename("/api/pdfs/legacy.pdf?x"))
        # Traversal / encoding still refused for physical ownership too.
        self.assertIsNone(owned_managed_pdf_basename("/api/pdfs/../x.pdf"))
        self.assertIsNone(owned_managed_pdf_basename("/api/pdfs/foo%2Fx.pdf"))

    def test_referenced_managed_pdf_filename_pins(self):
        pinned = (
            ("/api/pdfs/victim.pdf", "victim.pdf"),
            ("/api/pdfs/subdir/victim.pdf", "victim.pdf"),
            ("/api/pdfs/../victim.pdf", "victim.pdf"),
            ("/api/pdfs/foo%2Fvictim.pdf", "victim.pdf"),
            (" /api/pdfs/victim.pdf ", "victim.pdf"),
            ("\t/api/pdfs/victim.pdf\n", "victim.pdf"),
            # Serving identity: urlparse strips delimiters like the PDF route.
            ("/api/pdfs/victim.pdf?q", "victim.pdf"),
            ("/api/pdfs/victim.pdf;bar", "victim.pdf"),
            ("/api/pdfs/victim.pdf#frag", "victim.pdf"),
        )
        for path, expected in pinned:
            with self.subTest(path=path):
                self.assertEqual(referenced_managed_pdf_filename(path), expected)

    def test_row_references_protects_serving_and_physical_identities(self):
        # Surviving delimiter spelling protects the stem the route would serve.
        self.assertTrue(
            row_references_managed_pdf("/api/pdfs/x.pdf?q", "x.pdf")
        )
        # Physical legacy ownership protects the on-disk delimiter basename.
        self.assertTrue(
            row_references_managed_pdf("/api/pdfs/legacy.pdf?x", "legacy.pdf?x")
        )
        self.assertFalse(
            row_references_managed_pdf("/api/pdfs/other.pdf", "x.pdf")
        )
        # Weak aliases fail-closed-block but are not strong ownership.
        self.assertTrue(
            row_references_managed_pdf("/api/pdfs/foo%2Fx.pdf", "x.pdf")
        )
        self.assertFalse(
            row_strongly_references_managed_pdf("/api/pdfs/foo%2Fx.pdf", "x.pdf")
        )
        self.assertTrue(
            row_strongly_references_managed_pdf("/api/pdfs/x.pdf?q", "x.pdf")
        )

    def test_managed_basenames_protected_by_lists_physical_and_serving(self):
        self.assertEqual(
            managed_basenames_protected_by("/api/pdfs/foo.pdf"),
            ("foo.pdf",),
        )
        self.assertEqual(
            managed_basenames_protected_by("/api/pdfs/foo.pdf?x"),
            ("foo.pdf?x", "foo.pdf"),
        )
        self.assertEqual(
            managed_basenames_protected_by("/api/pdfs/foo.pdf;bar"),
            ("foo.pdf;bar", "foo.pdf"),
        )
        # Weak aliases are never positive deletion authority.
        self.assertEqual(managed_basenames_protected_by("/api/pdfs/../x.pdf"), ())
        self.assertEqual(
            managed_basenames_protected_by("/api/pdfs/subdir/x.pdf"), ()
        )
        self.assertEqual(
            managed_basenames_protected_by("/api/pdfs/foo%2Fx.pdf"), ()
        )

    def test_managed_pdf_key_names_one_asset_object(self):
        key = managed_pdf_key("keep.pdf")
        self.assertEqual((key.namespace, key.name) if key else None, (NAMESPACE_ASSET_OBJECTS, "keep.pdf"))
        decoded = managed_pdf_key("a%20b.pdf")
        self.assertEqual(decoded.name if decoded else None, "a b.pdf")

    def test_managed_pdf_key_refuses_path_shaped_input(self):
        for unsafe in (
            "",
            "..",
            "%2e%2e",
            "../keep.pdf",
            "%2e%2e%2fkeep.pdf",
            "nested/keep.pdf",
            "nested%2Fkeep.pdf",
            r"nested\\keep.pdf",
            "bad\x00name.pdf",
            "bad%00name.pdf",
            " victim.pdf",
            "victim.pdf ",
            "%20victim.pdf",
            ".prks-write-x.tmp",
        ):
            with self.subTest(unsafe=unsafe):
                self.assertIsNone(managed_pdf_key(unsafe))


if __name__ == "__main__":
    unittest.main()
