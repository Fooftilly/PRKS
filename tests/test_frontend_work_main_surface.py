"""The Work tile shell is Vue. Video HTML and the PDF runtime stay where they are."""
import unittest
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[1]
_WORKS = (_PROJECT / "frontend" / "js" / "components" / "works.js").read_text(encoding="utf-8")
_APP = (_PROJECT / "frontend" / "js" / "app.js").read_text(encoding="utf-8")
_VIDEO = (_PROJECT / "frontend" / "js" / "components" / "works-video.js").read_text(encoding="utf-8")
_PDF = (_PROJECT / "frontend" / "js" / "components" / "works-pdf.js").read_text(encoding="utf-8")
_FEATURE = _PROJECT / "frontend-app" / "src" / "features" / "work"
_SURFACE = (_FEATURE / "main-surface.ts").read_text(encoding="utf-8")
_LIFECYCLE = (_FEATURE / "detail-lifecycle.ts").read_text(encoding="utf-8")
_SESSION = (_FEATURE / "session.ts").read_text(encoding="utf-8")
_VIEW = (_FEATURE / "WorkMainSurface.vue").read_text(encoding="utf-8")
_MAIN = (_PROJECT / "frontend-app" / "src" / "main.ts").read_text(encoding="utf-8")
_BUNDLE = (_PROJECT / "frontend" / "vue" / "prks-vue.js").read_text(encoding="utf-8")


class WorkMainSurfaceContractTests(unittest.TestCase):
    def test_the_shell_uses_the_existing_viewer_functions(self):
        video = _LIFECYCLE.index("classic('renderVideoViewerPane')")
        present = _LIFECYCLE.rindex("presentWork(ctx, contentDiv,")
        pdf = _LIFECYCLE.index("initPdf(ctx, current)")
        self.assertLess(video, present)
        painted = _SESSION.index("const painted = presentRouteSurface(")
        attach_call = _SESSION.index("input.attach()", painted)
        self.assertLess(painted, attach_call)
        self.assertIn("if (!painted) return false", _SESSION[painted:attach_call])
        self.assertLess(pdf, present)
        self.assertNotIn("prksWorkDetailsShellHtml", _WORKS)
        self.assertNotIn("function renderWorkDetails", _WORKS)
        self.assertNotIn("prksVuePresentWorkMainSurface", _WORKS + _APP + _LIFECYCLE + _SESSION)
        self.assertIn('data-prks-role="pdf-viewer"', _VIEW)
        self.assertIn("function renderVideoViewerPane(work)", _VIDEO)
        self.assertIn("https://www.youtube.com/embed/", _VIDEO)
        self.assertNotIn("youtube.com/embed", _SURFACE + _LIFECYCLE)
        self.assertNotIn("youtube.com/embed", _VIEW)
        self.assertNotIn("prksYoutubeEmbedUrl", _SURFACE + _VIEW + _LIFECYCLE)
        self.assertIn("export function initPdfViewerForWork(ctx, work)", _PDF)
        self.assertNotIn("savePdfAnnotation", _SURFACE + _VIEW + _LIFECYCLE)

    def test_the_bridge_is_registered_for_the_owning_tile(self):
        self.assertIn("registerWorkDetailBridge", _MAIN)
        self.assertNotIn("registerWorkMainSurfaceBridge", _MAIN)
        self.assertIn("prksMountWorkDetail", _BUNDLE)
        self.assertNotIn("prksVuePresentWorkMainSurface", _BUNDLE)
        self.assertNotIn("prksVueDismissWorkMainSurface", _BUNDLE)
        self.assertIn("owner.isCurrent(input.generation)", _SESSION)
        self.assertIn("presentRouteSurface", _SESSION)
        self.assertNotIn("workMainSurface", _SURFACE + _SESSION + _LIFECYCLE)
        self.assertIn('data-prks-role="pdf-viewer"', _VIEW)
        self.assertIn('data-prks-role="work-research-notes-anchor"', _VIEW)
        self.assertIn("viewerHtml", _VIEW)

    def test_a_rejected_generation_does_not_paint_a_legacy_shell(self):
        self.assertNotIn("prksWorkDetailsShellHtml", _WORKS + _LIFECYCLE + _SESSION + _VIEW)
        self.assertNotIn("container.innerHTML", _LIFECYCLE)
        self.assertIn("if (!painted) return false", _SESSION)
        self.assertIn('role="separator"', _VIEW)
        self.assertNotIn('role="slider"', _VIEW)
        self.assertNotIn("aria-valuemin", _VIEW)
        self.assertNotIn("aria-valuemax", _VIEW)
        self.assertNotIn("aria-valuenow", _VIEW)
        self.assertNotIn("aria-orientation", _VIEW)


if __name__ == "__main__":
    unittest.main()
