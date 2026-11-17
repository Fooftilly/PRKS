"""The managed-PDF name rules (``backend/managed_pdf_names.py``).

Pure rules with no database: which ``/api/pdfs/`` spellings name a managed
file, which basename a row owns or protects, and the containment check.
Moved out of ``tests/test_work_deletion.py`` and ``tests/test_db_manager.py``
when PG-C2 (#641) retired their SQLite tests; the rules stay in use on PostgreSQL.
"""

import os
import shutil
import tempfile
import unittest

from backend.managed_pdf_names import (
    managed_basenames_protected_by,
    managed_pdf_filename,
    owned_managed_pdf_basename,
    referenced_managed_pdf_filename,
    row_references_managed_pdf,
    row_strongly_references_managed_pdf,
    safe_pdf_path_under_dir,
)


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

    def test_safe_pdf_path_under_dir_rejects_nul(self):
        pdfs = tempfile.mkdtemp()
        try:
            self.assertIsNone(safe_pdf_path_under_dir(pdfs, "bad\x00name.pdf"))
        finally:
            shutil.rmtree(pdfs)

    def _assert_posix_child(self, actual, root, *parts):
        """Assert a POSIX acceptance result, skipping the check on Windows.

        ``windows_semantics=False`` disables drive-prefix *validation* only; the
        join still uses native ``Path`` semantics. On Windows a drive-looking
        segment re-anchors regardless, so the helper returns None (or another
        path) and there is no POSIX expectation to assert.
        """
        if os.name == "nt":
            return
        self.assertEqual(actual, os.path.realpath(os.path.join(root, *parts)))

    def test_safe_pdf_path_rejects_path_shaped_input(self):
        pdfs = tempfile.mkdtemp()
        try:
            for unsafe in (
                "..",
                "%2e%2e",
                "../keep.pdf",
                "%2e%2e%2fkeep.pdf",
                "nested/keep.pdf",
                "nested%2Fkeep.pdf",
                r"nested\\keep.pdf",
                "bad\x00name.pdf",
                " victim.pdf",
                "victim.pdf ",
            ):
                with self.subTest(unsafe=unsafe):
                    self.assertIsNone(safe_pdf_path_under_dir(pdfs, unsafe))
            safe = os.path.join(pdfs, "keep.pdf")
            with open(safe, "w", encoding="utf-8") as f:
                f.write("x")
            resolved = safe_pdf_path_under_dir(pdfs, "keep.pdf")
            self.assertEqual(resolved, os.path.realpath(safe))
        finally:
            shutil.rmtree(pdfs)

    def test_safe_pdf_path_rejects_a_drive_qualified_filename(self):
        """The managed-PDF validator shares the hazard.

        ``managed_pdf_filename("/api/pdfs/C:foo.pdf")`` returns ``C:foo.pdf``,
        and on Windows joining that onto pdfs/ resolves to ``pdfs/foo.pdf`` --
        a different managed PDF, silently, and still inside the root.
        """
        pdfs = tempfile.mkdtemp()
        try:
            for name in ("C:foo.pdf", "D:foo.pdf", "c:foo.pdf"):
                with self.subTest(name=name):
                    self.assertIsNone(
                        safe_pdf_path_under_dir(pdfs, name, windows_semantics=True)
                    )
                    self._assert_posix_child(
                        safe_pdf_path_under_dir(pdfs, name, windows_semantics=False),
                        pdfs,
                        name,
                    )
            self.assertEqual(
                safe_pdf_path_under_dir(pdfs, "keep.pdf", windows_semantics=True),
                os.path.realpath(os.path.join(pdfs, "keep.pdf")),
            )
        finally:
            shutil.rmtree(pdfs)


if __name__ == "__main__":
    unittest.main()
