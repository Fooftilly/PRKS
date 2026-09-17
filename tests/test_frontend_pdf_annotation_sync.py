"""Frontend PDF annotation durable family: registration + Node selftests."""
import pathlib
import subprocess
import unittest

from backend import sync_protocol

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "js"


class PdfAnnotationSyncFrontendTests(unittest.TestCase):
    def test_runtime_selftests(self):
        proc = subprocess.run(
            ["node", str(ROOT / "tests" / "browser" / "run_pdf_annotation_sync_selftest.js")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("checks passed", proc.stdout)

    def test_the_family_is_registered_everywhere_it_must_be(self):
        families = (
            "CREATE_PDF_ANNOTATION",
            "SET_PDF_ANNOTATION",
            "DELETE_PDF_ANNOTATION",
        )
        store = (FRONTEND / "local-store.js").read_text(encoding="utf-8")
        runtime = (FRONTEND / "sync-runtime.js").read_text(encoding="utf-8")
        diagnostics = (FRONTEND / "sync-diagnostics.js").read_text(encoding="utf-8")
        index = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        self.assertIn("pdf-annotation-state.js", index)
        for family in families:
            with self.subTest(family=family):
                self.assertIn(family, sync_protocol.supported_operations())
                self.assertIn("'%s'," % family, store)
                self.assertIn("%s: root.prksPdfAnnotationSyncHandler" % family, runtime)
                self.assertIn(family, diagnostics)
        coordinator = runtime[: runtime.index("root.createPrksSyncRuntime = createRuntime;")]
        for leaked in families + ("annotation_id", "pdf-annotation"):
            self.assertNotIn(leaked, coordinator, leaked)


if __name__ == "__main__":
    unittest.main()
