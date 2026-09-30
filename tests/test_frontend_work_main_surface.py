"""The Work tile shell is Vue. Video HTML and the PDF runtime stay where they are."""
import unittest
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[1]
_WORKS = (_PROJECT / "frontend" / "js" / "components" / "works.js").read_text(encoding="utf-8")
_VIDEO = (_PROJECT / "frontend" / "js" / "components" / "works-video.js").read_text(encoding="utf-8")
_PDF = (_PROJECT / "frontend" / "js" / "components" / "works-pdf.js").read_text(encoding="utf-8")
_SURFACE = (_PROJECT / "frontend-app" / "src" / "features" / "work" / "main-surface.ts").read_text(encoding="utf-8")
_VIEW = (_PROJECT / "frontend-app" / "src" / "features" / "work" / "WorkMainSurface.vue").read_text(encoding="utf-8")
_MAIN = (_PROJECT / "frontend-app" / "src" / "main.ts").read_text(encoding="utf-8")
_BUNDLE = (_PROJECT / "frontend" / "vue" / "prks-vue.js").read_text(encoding="utf-8")


class WorkMainSurfaceContractTests(unittest.TestCase):
    def test_the_shell_uses_the_existing_viewer_functions(self):
        body = _WORKS[_WORKS.index("async function renderWorkDetails"):_WORKS.index("function prksPaintEasyMDEToolbarIcons")]
        video = body.index("window.renderVideoViewerPane(work)")
        present = body.index("prksVuePresentWorkMainSurface")
        pdf = body.index("initPdfViewerForWork")
        self.assertLess(video, present)
        self.assertLess(present, pdf)
        self.assertIn("prksWorkDetailsShellHtml(shell)", body)
        self.assertIn('data-prks-role="pdf-viewer"', _WORKS)
        self.assertIn("function renderVideoViewerPane(work)", _VIDEO)
        self.assertIn("https://www.youtube.com/embed/", _VIDEO)
        self.assertNotIn("youtube.com/embed", _SURFACE)
        self.assertNotIn("youtube.com/embed", _VIEW)
        self.assertNotIn("prksYoutubeEmbedUrl", _SURFACE + _VIEW)
        self.assertIn("export function initPdfViewerForWork(ctx, work)", _PDF)
        self.assertNotIn("savePdfAnnotation", _SURFACE + _VIEW)

    def test_the_bridge_is_registered_for_the_owning_tile(self):
        self.assertIn("registerWorkMainSurfaceBridge", _MAIN)
        self.assertIn("prksVuePresentWorkMainSurface", _BUNDLE)
        self.assertIn("ctx.isCurrent(model.generation)", _SURFACE)
        self.assertIn("registerCleanup", _SURFACE)
        self.assertNotIn("workMainSurface", _SURFACE)
        self.assertIn('data-prks-role="pdf-viewer"', _VIEW)
        self.assertIn('data-prks-role="work-research-notes-anchor"', _VIEW)
        self.assertIn("viewerHtml", _VIEW)

    def test_a_rejected_vue_surface_does_not_paint_the_legacy_shell(self):
        body = _WORKS[_WORKS.index("async function renderWorkDetails"):_WORKS.index("function prksPaintEasyMDEToolbarIcons")]
        gate = body.index("typeof prksVuePresentWorkMainSurface !== 'function'")
        reject = body.index("prksVuePresentWorkMainSurface(ctx, shell) !== true", gate)
        fallback = body[gate:reject]
        self.assertLess(fallback.index("if (!isCurrent()) return;"), fallback.index("getEntity('work')"))
        self.assertLess(fallback.index("getEntity('work')"), fallback.index("container.innerHTML = prksWorkDetailsShellHtml(shell)"))
        self.assertNotIn("prksVueDismissWorkMainSurface", body[gate:reject + 80])
        self.assertIn('role="separator"', _VIEW)
        self.assertNotIn('role="slider"', _VIEW)
        self.assertNotIn("aria-valuemin", _VIEW)
        self.assertNotIn("aria-valuemax", _VIEW)
        self.assertNotIn("aria-valuenow", _VIEW)
        self.assertNotIn("aria-orientation", _VIEW)


if __name__ == "__main__":
    unittest.main()
