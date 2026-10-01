"""Viewer-owned annotation drawer stays on the pdf runtime."""
import re
import unittest
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (ROOT / "frontend" / "js" / "pdf-work-runtime.js").read_text(encoding="utf-8")
PDF = (ROOT / "frontend" / "js" / "components" / "works-pdf.js").read_text(encoding="utf-8")
ADAPTER = (ROOT / "frontend-app" / "src" / "features" / "work" / "pdf-adapter.ts").read_text(encoding="utf-8")
DRAWER = (ROOT / "frontend-app" / "src" / "features" / "work" / "pdf-annotation-drawer.ts").read_text(encoding="utf-8")
VIEW = (ROOT / "frontend-app" / "src" / "features" / "work" / "WorkPdfAnnotationDrawer.vue").read_text(encoding="utf-8")
UI = (ROOT / "frontend" / "js" / "ui.js").read_text(encoding="utf-8")
MAIN = (ROOT / "frontend-app" / "src" / "main.ts").read_text(encoding="utf-8")
TOOLBAR = (ROOT / "tools" / "pdf-viewer" / "src" / "toolbar.tsx").read_text(encoding="utf-8")
CONTROLLER = (ROOT / "tools" / "pdf-viewer" / "src" / "controller.ts").read_text(encoding="utf-8")


class WorkPdfAnnotationDrawerTests(unittest.TestCase):
    def test_drawer_session(self):
        script = ROOT / "tests" / "browser" / "run_work_pdf_annotation_drawer_selftest.js"
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

    def test_drawer_owner_is_the_runtime(self):
        self.assertIn("runtime.openAnnotationDrawer", RUNTIME)
        self.assertIn("runtime.closeAnnotationDrawer", RUNTIME)
        self.assertIn("runtime.annotationDrawerStill", RUNTIME)
        self.assertNotIn("savePdfAnnotation", RUNTIME)
        self.assertNotIn("createPrksPdfViewer", RUNTIME)
        open_body = RUNTIME[RUNTIME.index("runtime.openAnnotationDrawer"):RUNTIME.index("runtime.closeAnnotationDrawer")]
        close_body = RUNTIME[RUNTIME.index("runtime.closeAnnotationDrawer"):RUNTIME.index("runtime.toggleAnnotationDrawer")]
        for body in (open_body, close_body):
            self.assertNotIn("goToPage", body)
            self.assertNotIn("zoom", body)
            self.assertNotIn("resize", body)
            self.assertNotIn("viewerSetupToken", body)
        self.assertIn("window.prksOpenAnnotationDrawer", PDF)
        self.assertIn("window.deletePdfAnnotationFromList", PDF)
        self.assertIn("window.copyPdfAnnotationWikiLink", PDF)
        self.assertIn("onAnnotationDrawerToggle", PDF)
        toggle_at = PDF.index("onAnnotationDrawerToggle")
        toggle = PDF[toggle_at:PDF.index("onError:", toggle_at)]
        self.assertNotIn("createPrksPdfViewer", toggle)
        self.assertNotIn("goToPage", toggle)
        mount_at = PDF.index("runtime.viewer = viewer;")
        mount = PDF[mount_at:PDF.index("prksAttachPdfSearch", mount_at)]
        self.assertLess(mount.index("runtime.viewerSetupToken"), mount.index("prksSyncAnnotationDrawer"))
        delete = PDF[PDF.index("window.deletePdfAnnotationFromList"):PDF.index("function prksAnnotationPopupGenerationCurrent")]
        self.assertLess(delete.index("captureAnnotationPopupTicket"), delete.index("prksConfirmDeletePdfAnnotation"))
        self.assertLess(delete.index("prksConfirmDeletePdfAnnotation"), delete.index("await prksWaitOutAnnotationMaterialization"))
        self.assertIn("const viewer = ticket.viewer", delete)
        self.assertIn("pdf.viewer === viewer", delete)
        self.assertNotIn("fetch(", delete)
        copy = PDF[PDF.index("window.copyPdfAnnotationWikiLink"):PDF.index("window.deletePdfAnnotationFromList")]
        self.assertNotIn("prksFlashButtonLabel", copy)
        self.assertIn("export function readWorkPdfAnnotationDrawer", ADAPTER)
        self.assertIn("export function intentDeleteWorkPdfAnnotation", ADAPTER)
        self.assertIn("annotationDrawerStill", ADAPTER)
        self.assertNotIn(".savePdfAnnotation(", ADAPTER)
        self.assertNotIn("createPrksPdfViewer", ADAPTER)
        self.assertNotIn("fetch(", DRAWER)
        self.assertNotIn("savePdfAnnotation", DRAWER)
        self.assertIn("data-prks-role=\"pdf-annotation-drawer\"", VIEW)
        self.assertIn("prksFlashButtonLabel", VIEW)
        self.assertIn("data-prks-list-published", VIEW)
        self.assertIn("Annotations are listed on the PDF.", UI)
        self.assertIn('data-prks-role="open-pdf-annotation-drawer"', UI)
        tab = UI[UI.index("function renderWorkAnnotationsTab"):UI.index("// Advanced Upload Logic")]
        self.assertNotIn("annotation-row__delete", tab)
        self.assertIn("registerWorkPdfAnnotationDrawerBridge", MAIN)
        self.assertIn('aria-label="Annotations"', TOOLBAR)
        self.assertIn("setAnnotationDrawerOpen", CONTROLLER)

    def test_popup_host_stays_usable_when_drawer_fills_the_pane(self):
        css = (ROOT / "frontend" / "css" / "style.css").read_text(encoding="utf-8")
        popup = (
            ROOT / "frontend-app" / "src" / "features" / "work" / "WorkPdfAnnotationPopup.vue"
        ).read_text(encoding="utf-8")
        pane = re.search(
            r"(?m)^\.document-view--work \.work-pdf-pane \{$(.*?)^\}",
            css,
            re.S | re.M,
        )
        self.assertIsNotNone(pane)
        self.assertIn("container-type: inline-size;", pane.group(1))
        self.assertIn("container-name: prks-pdf-pane;", pane.group(1))
        drawer = re.search(
            r"(?m)^\.pdf-annotation-drawer \{$(.*?)^\}",
            css,
            re.S | re.M,
        )
        self.assertIsNotNone(drawer)
        self.assertIn("width: min(22rem, 100%);", drawer.group(1))
        host_at = css.index('.document-view--work [data-prks-role="pdf-annotation-popup-host"]')
        popup_at = css.index(".pdf-annotation-popup {", host_at)
        host = css[host_at:popup_at]
        query = "@container prks-pdf-pane (min-width: calc(22rem + 160px))"
        base, marker, gated = host.partition(query)
        self.assertTrue(marker)
        self.assertIn("inset: 0;", base)
        self.assertIn("overflow: visible;", base)
        self.assertNotIn("right:", base)
        self.assertNotIn("overflow: hidden", base)
        self.assertNotIn("right: min(22rem, 100%)", host)
        self.assertIn(
            '.work-pdf-pane:has([data-prks-role="pdf-annotation-drawer"]) [data-prks-role="pdf-annotation-popup-host"]',
            gated,
        )
        self.assertIn("right: 22rem;", gated)
        self.assertIn("overflow: hidden;", gated)
        self.assertIn("if (width < 160) return pane", popup)


if __name__ == "__main__":
    unittest.main()
