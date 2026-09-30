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
        self.assertNotIn("savePdfAnnotation", ADAPTER)
        self.assertNotIn("/annotations", ADAPTER)
        self.assertNotIn("works-pdf.js", ADAPTER)
        self.assertNotIn("setResource(", ADAPTER)
        self.assertIn("registerWorkPdfAdapterBridge", MAIN)
        self.assertIn("prksReadWorkPdf", BUNDLE)
        self.assertIn("prksIntentMountWorkPdf", BUNDLE)
        self.assertNotIn("savePdfAnnotation", BUNDLE)
        self.assertNotIn("savePdfAnnotation", SURFACE + VIEW)
        self.assertNotIn("works-pdf.js", SURFACE + VIEW)
        self.assertIn("`/api/works/${workId}/annotations`", PDF)
        self.assertIn("ctx.setResource('pdf', runtime, function () {", PDF)


if __name__ == "__main__":
    unittest.main()
