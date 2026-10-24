"""Behavioral Work PDF lifecycle. The Vue adapter only forwards to the existing owner."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = (ROOT / "frontend-app" / "src" / "features" / "work" / "pdf-adapter.ts").read_text(encoding="utf-8")
PDF = (ROOT / "frontend" / "js" / "components" / "works-pdf.js").read_text(encoding="utf-8")
MAIN = (ROOT / "frontend-app" / "src" / "main.ts").read_text(encoding="utf-8")
BUNDLE = (ROOT / "frontend" / "vue" / "prks-vue.js").read_text(encoding="utf-8")
SURFACE = (ROOT / "frontend-app" / "src" / "features" / "work" / "main-surface.ts").read_text(encoding="utf-8")
VIEW = (ROOT / "frontend-app" / "src" / "features" / "work" / "WorkMainSurface.vue").read_text(encoding="utf-8")

RETIRED_PDF_ADAPTER_GLOBALS = (
    "prksReadWorkPdf",
    "prksReadWorkPdfSearch",
    "prksReadWorkPdfAnnotationPopup",
    "prksReadWorkPdfAnnotationDrawer",
    "prksWorkPdfLeaveNeedsConfirm",
    "prksIntentMountWorkPdf",
    "prksIntentFlushWorkPdf",
    "prksIntentResizeWorkPdf",
    "prksIntentOpenWorkPdfSearch",
    "prksIntentCloseWorkPdfSearch",
    "prksIntentSetWorkPdfSearchQuery",
    "prksIntentWorkPdfSearchNext",
    "prksIntentWorkPdfSearchPrevious",
    "prksIntentSaveWorkPdfAnnotationComment",
    "prksIntentCloseWorkPdfAnnotationPopup",
    "prksIntentDeleteWorkPdfAnnotationPopup",
    "prksIntentCloseWorkPdfAnnotationDrawer",
    "prksIntentJumpWorkPdfAnnotation",
    "prksIntentEditWorkPdfAnnotationComment",
    "prksIntentDeleteWorkPdfAnnotation",
    "prksIntentCopyWorkPdfAnnotationLink",
    "prksIntentSetWorkPdfAnnotationDrawerPinned",
    "prksIntentResizeWorkPdfAnnotationDrawer",
)


class WorkPdfLifecycleTests(unittest.TestCase):
    def test_runtime_lifecycle(self):
        script = ROOT / "tests" / "browser" / "run_work_pdf_lifecycle_selftest.js"
        result = subprocess.run(
            ["node", str(script)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            self.fail(result.stdout + "\n" + result.stderr)

    def test_adapter_does_not_own_durable_annotation_writes(self):
        self.assertIn("export function intentMountWorkPdf", ADAPTER)
        self.assertIn("orchestration.initPdfViewerForWork(ctx, work)", ADAPTER)
        self.assertNotIn("store.savePdfAnnotation", ADAPTER)
        self.assertNotIn("savePdfAnnotation(", ADAPTER)
        self.assertNotIn("/annotations", ADAPTER)
        self.assertNotIn("works-pdf.js", ADAPTER)
        self.assertNotIn("setResource(", ADAPTER)
        self.assertNotIn("store.savePdfAnnotation", BUNDLE)
        self.assertNotIn("savePdfAnnotation(", BUNDLE)
        self.assertNotIn("savePdfAnnotation", SURFACE + VIEW)
        self.assertNotIn("works-pdf.js", SURFACE + VIEW)
        self.assertIn("`/api/works/${workId}/annotations`", PDF)
        self.assertIn("ctx.registerResource(_pdfTicket", PDF)
        self.assertIn("kind: 'pdf'", PDF)
        self.assertNotIn("ctx.setResource('pdf'", PDF)

    def test_retired_pdf_adapter_window_bridge_stays_absent(self):
        """Vue imports the adapter directly; nothing reads it from `window`.

        The bridge that copied these functions onto `window` had no
        production consumer and was retired in the #303 closeout. Classic
        PDF code talks to Vue only through the `prksVue*WorkPdfAnnotation*`
        sync/dismiss entries recorded in docs/frontend-migration-boundaries.md.
        """
        self.assertNotIn("registerWorkPdfAdapterBridge", MAIN)
        self.assertNotIn("registerWorkPdfAdapterBridge", ADAPTER)
        self.assertNotIn("registerWorkPdfAdapterBridge", BUNDLE)
        for name in RETIRED_PDF_ADAPTER_GLOBALS:
            with self.subTest(name=name):
                self.assertNotIn(name, BUNDLE)
                self.assertNotIn(name, ADAPTER)
                self.assertNotIn(name, PDF)


if __name__ == "__main__":
    unittest.main()
