"""In-document PDF search stays on the viewer runtime."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (ROOT / "frontend" / "js" / "pdf-work-runtime.js").read_text(encoding="utf-8")
PDF = (ROOT / "frontend" / "js" / "components" / "works-pdf.js").read_text(encoding="utf-8")
ADAPTER = (ROOT / "frontend-app" / "src" / "features" / "work" / "pdf-adapter.ts").read_text(encoding="utf-8")
MAIN = (ROOT / "frontend-app" / "src" / "main.ts").read_text(encoding="utf-8")
BUNDLE = (ROOT / "frontend" / "vue" / "prks-vue.js").read_text(encoding="utf-8")
VIEWER = (ROOT / "tools" / "pdf-viewer" / "src" / "viewer.tsx").read_text(encoding="utf-8")
PLUGINS = (ROOT / "tools" / "pdf-viewer" / "src" / "plugins.ts").read_text(encoding="utf-8")
BAR = (ROOT / "tools" / "pdf-viewer" / "src" / "search-bar.tsx").read_text(encoding="utf-8")
BAR_VIEW = (ROOT / "tools" / "pdf-viewer" / "src" / "search-bar-view.ts").read_text(encoding="utf-8")


class WorkPdfSearchTests(unittest.TestCase):
    def test_search_session(self):
        # parity: pdf-document-search
        script = ROOT / "tests" / "browser" / "run_work_pdf_search_selftest.js"
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

    def test_search_owner_is_the_runtime_not_annotations_or_vue_state(self):
        self.assertIn("function bindPdfSurfaceSearch", RUNTIME)
        self.assertIn("runtime.readSearch", RUNTIME)
        self.assertIn("runtime.setSearchQuery", RUNTIME)
        self.assertIn("prksPdfSearchStill", RUNTIME)
        self.assertNotIn("store.savePdfAnnotation", RUNTIME)
        self.assertNotIn("savePdfAnnotation(", RUNTIME)
        self.assertNotIn("annotationCache", BAR)
        self.assertIn("bindPdfSurfaceSearch", PDF)
        self.assertIn("prksAttachPdfSearch", PDF)
        self.assertIn("setSearchDriver", PDF)
        self.assertIn("ownerTabId:", PDF)
        self.assertIn("ownerGeneration:", PDF)
        self.assertNotIn("createPrksPdfViewer", BAR)
        self.assertIn("SearchPluginPackage", PLUGINS)
        self.assertIn("SearchLayer", (ROOT / "tools" / "pdf-viewer" / "src" / "page-view.tsx").read_text(encoding="utf-8"))
        self.assertNotIn("renderTree()", VIEWER[VIEWER.index("openSearch:") : VIEWER.index("searchPrevious:")])
        self.assertIn("export function readWorkPdfSearch", ADAPTER)
        self.assertIn("export function intentSetWorkPdfSearchQuery", ADAPTER)
        self.assertNotIn("store.savePdfAnnotation", ADAPTER)
        self.assertNotIn("savePdfAnnotation(", ADAPTER)
        self.assertNotIn("createPrksPdfViewer", ADAPTER)
        self.assertIn("registerWorkPdfAdapterBridge", MAIN)
        self.assertIn("prksReadWorkPdfSearch", BUNDLE)
        self.assertIn("prksIntentSetWorkPdfSearchQuery", BUNDLE)
        self.assertIn("data-prks-role=\"pdf-search\"", BAR)
        self.assertIn("data-prks-owner-tab-id", BAR)
        self.assertIn("Find in document", BAR)
        self.assertIn("pdfSearchBarView", BAR)
        self.assertIn("No matches", BAR_VIEW)
        self.assertIn("setQuery:", BAR)


if __name__ == "__main__":
    unittest.main()
