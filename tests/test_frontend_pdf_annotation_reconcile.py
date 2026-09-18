"""Static contract for PDF annotation viewer reconciliation (Slice D)."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PdfAnnotationReconcileContractTests(unittest.TestCase):
    def test_reconcile_module_loaded_before_works_pdf(self) -> None:
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        state = html.find('src="/js/pdf-annotation-state.js"')
        reconcile = html.find('src="/js/pdf-annotation-reconcile.js"')
        works = html.find('src="/js/components/works-pdf.js"')
        self.assertGreater(state, 0)
        self.assertGreater(reconcile, state)
        self.assertGreater(works, reconcile)

    def test_reconcile_exports_and_suppress(self) -> None:
        src = (ROOT / "frontend" / "js" / "pdf-annotation-reconcile.js").read_text(
            encoding="utf-8"
        )
        for needle in (
            "prksReconcileViewerAnnotations",
            "prksViewerIsReconcilingAnnotations",
            "prksBeginViewerAnnotationReconcile",
            "__prksManagedAnnotationIds",
            "Never touch",
        ):
            self.assertIn(needle, src)

    def test_works_pdf_suppresses_reconcile_events(self) -> None:
        src = (ROOT / "frontend" / "js" / "components" / "works-pdf.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("prksViewerIsReconcilingAnnotations", src)
        self.assertIn("prksReconcileViewerAnnotations", src)
        self.assertIn("prksEffectiveWorkAnnotations", src)
        self.assertIn("prksKnownAbsentAnnotationSeed", src)
        self.assertIn("knownAbsent: prksKnownAbsentAnnotationSeed(runtime)", src)
        # Every reconcile call must seed known-absent (4 call sites).
        self.assertEqual(src.count("knownAbsent: prksKnownAbsentAnnotationSeed(runtime)"), 4)

    def test_reconcile_accepts_known_absent_seed(self) -> None:
        src = (ROOT / "frontend" / "js" / "pdf-annotation-reconcile.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("opts.knownAbsent", src)
        self.assertIn("seedManagedIds", src)

    def test_selftest_exists(self) -> None:
        path = ROOT / "tests" / "browser" / "run_pdf_annotation_reconcile_selftest.js"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertIn("link preserved", text)
        self.assertIn("seedManagedIds", text)
        self.assertIn("knownAbsentRemovesStaleDeletedMarkup", text)
        self.assertIn("knownAbsent:", text)


if __name__ == "__main__":
    unittest.main()
