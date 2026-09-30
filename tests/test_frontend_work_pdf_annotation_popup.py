"""Anchored annotation comment popup stays on the pdf runtime."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (ROOT / "frontend" / "js" / "pdf-work-runtime.js").read_text(encoding="utf-8")
PDF = (ROOT / "frontend" / "js" / "components" / "works-pdf.js").read_text(encoding="utf-8")
ADAPTER = (ROOT / "frontend-app" / "src" / "features" / "work" / "pdf-adapter.ts").read_text(encoding="utf-8")
POPUP = (ROOT / "frontend-app" / "src" / "features" / "work" / "pdf-annotation-popup.ts").read_text(encoding="utf-8")
FLOATING = (ROOT / "frontend-app" / "src" / "floating" / "bind-floating-position.ts").read_text(encoding="utf-8")
MENU = (ROOT / "tools" / "pdf-viewer" / "src" / "annotation-menu.tsx").read_text(encoding="utf-8")
UI = (ROOT / "frontend" / "js" / "ui.js").read_text(encoding="utf-8")
MAIN = (ROOT / "frontend-app" / "src" / "main.ts").read_text(encoding="utf-8")


class WorkPdfAnnotationPopupTests(unittest.TestCase):
    def test_popup_session(self):
        script = ROOT / "tests" / "browser" / "run_work_pdf_annotation_popup_selftest.js"
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

    def test_popup_owner_is_the_runtime(self):
        self.assertIn("runtime.openAnnotationPopup", RUNTIME)
        self.assertIn("runtime.closeAnnotationPopup", RUNTIME)
        self.assertIn("runtime.annotationPopupStill", RUNTIME)
        self.assertIn("runtime.annotationPopupWriteStill", RUNTIME)
        self.assertNotIn("savePdfAnnotation", RUNTIME)
        self.assertNotIn("createPrksPdfViewer", RUNTIME)
        self.assertIn("prksOpenAnnotationPopupSession", PDF)
        session = PDF[PDF.index("function prksOpenAnnotationPopupSession"):PDF.index("window.closePdfAnnotationEditor")]
        self.assertLess(session.index("viewer.goToPage"), session.index("const opened = pdf.openAnnotationPopup"))
        self.assertIn("!(opts && opts.reason === 'viewer')", session)
        self.assertIn("annotationPopupStill", PDF)
        self.assertIn("ticket.annId", PDF)
        self.assertIn("ticket.deletable !== true", PDF)
        self.assertIn("prksVueDismissWorkPdfAnnotationPopup", PDF)
        self.assertNotIn("pdf-annotation-editor", PDF)
        self.assertNotIn("id=\"pdf-annotation-editor\"", UI)
        self.assertIn("data-prks-role=\"pdf-annotation-anchor\"", MENU)
        self.assertIn("deletable:", MENU)
        self.assertNotIn("Edit comment", MENU)
        cleanup = MENU[MENU.index("return () => {"):MENU.index("}, [commentable, id, pageIndex]")]
        self.assertLess(cleanup.index("getSelectedAnnotations"), cleanup.index("dismissRef.current"))
        self.assertIn("if (!stillSelected)", cleanup)
        self.assertIn("export function readWorkPdfAnnotationPopup", ADAPTER)
        self.assertIn("export function intentSaveWorkPdfAnnotationComment", ADAPTER)
        self.assertIn("savePdfAnnotationComment", ADAPTER)
        self.assertNotIn(".savePdfAnnotation(", ADAPTER)
        self.assertNotIn("store.savePdfAnnotation", ADAPTER)
        self.assertNotIn("createPrksPdfViewer", ADAPTER)
        self.assertNotIn("@floating-ui", ADAPTER)
        self.assertIn("computePosition", FLOATING)
        self.assertNotIn("savePdfAnnotation", FLOATING)
        self.assertNotIn("annotationPopup", FLOATING)
        self.assertIn("intentSaveWorkPdfAnnotationComment", POPUP)
        self.assertNotIn("savePdfAnnotation", POPUP)
        self.assertIn("registerWorkPdfAnnotationPopupBridge", MAIN)


if __name__ == "__main__":
    unittest.main()
