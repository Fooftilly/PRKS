"""The Work tile shell is Vue. The video pane is Vue. The PDF runtime stays classic."""
import unittest
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[1]
_WORKS = (_PROJECT / "frontend" / "js" / "components" / "works.js").read_text(encoding="utf-8")
_APP = (_PROJECT / "frontend" / "js" / "app.js").read_text(encoding="utf-8")
_PDF = (_PROJECT / "frontend" / "js" / "components" / "works-pdf.js").read_text(encoding="utf-8")
_FEATURE = _PROJECT / "frontend-app" / "src" / "features" / "work"
_SURFACE = (_FEATURE / "main-surface.ts").read_text(encoding="utf-8")
_LIFECYCLE = (_FEATURE / "detail-lifecycle.ts").read_text(encoding="utf-8")
_SESSION = (_FEATURE / "session.ts").read_text(encoding="utf-8")
_VIEW = (_FEATURE / "WorkMainSurface.vue").read_text(encoding="utf-8")
_PANE = (_FEATURE / "WorkVideoPane.vue").read_text(encoding="utf-8")
_EMBED = (_FEATURE / "video-embed.ts").read_text(encoding="utf-8")
_MAIN = (_PROJECT / "frontend-app" / "src" / "main.ts").read_text(encoding="utf-8")
_BUNDLE = (_PROJECT / "frontend" / "vue" / "prks-vue.js").read_text(encoding="utf-8")


class WorkMainSurfaceContractTests(unittest.TestCase):
    def test_the_shell_keeps_pdf_classic_and_owns_the_video_pane(self):
        present = _LIFECYCLE.rindex("presentWork(ctx, contentDiv,")
        pdf = _LIFECYCLE.index("initPdf(ctx, current)")
        painted = _SESSION.index("const painted = presentRouteSurface(")
        attach_call = _SESSION.index("input.attach()", painted)
        self.assertLess(painted, attach_call)
        self.assertIn("if (!painted) return false", _SESSION[painted:attach_call])
        self.assertLess(pdf, present)
        self.assertNotIn("prksWorkDetailsShellHtml", _WORKS)
        self.assertNotIn("function renderWorkDetails", _WORKS)
        self.assertNotIn("prksVuePresentWorkMainSurface", _WORKS + _APP + _LIFECYCLE + _SESSION)
        self.assertIn('data-prks-role="pdf-viewer"', _VIEW)
        self.assertIn("WorkVideoPane", _VIEW)
        self.assertNotIn("v-html", _PANE)
        self.assertNotIn("viewerHtml", _VIEW + _SURFACE + _LIFECYCLE + _PANE)
        self.assertNotIn("renderVideoViewerPane", _VIEW + _SURFACE + _LIFECYCLE + _PANE + _EMBED + _BUNDLE)
        self.assertNotIn("works-video.js", _LIFECYCLE + _BUNDLE)
        self.assertIn("resolveWorkVideoEmbed", _EMBED)
        self.assertIn("https://www.youtube.com/embed/", _EMBED)
        self.assertNotIn("youtube.com/embed", _VIEW + _LIFECYCLE + _SURFACE)
        self.assertFalse((_PROJECT / "frontend" / "js" / "components" / "works-video.js").exists())
        self.assertIn("export function initPdfViewerForWork(ctx, work)", _PDF)
        self.assertNotIn("savePdfAnnotation", _SURFACE + _VIEW + _LIFECYCLE + _PANE)

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
        self.assertIn("video: described.kind === 'video'", _LIFECYCLE)
        self.assertIn(":video=\"surface.video\"", (_FEATURE / "WorkDetailRoute.vue").read_text(encoding="utf-8"))

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
