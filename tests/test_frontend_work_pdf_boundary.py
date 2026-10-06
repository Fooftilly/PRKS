"""Work PDF ownership boundary. The viewer runtime and annotation storage stay put."""
import unittest
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[1]
_WORKS = (_PROJECT / "frontend" / "js" / "components" / "works.js").read_text(encoding="utf-8")
_PDF = (_PROJECT / "frontend" / "js" / "components" / "works-pdf.js").read_text(encoding="utf-8")
_APP = (_PROJECT / "frontend" / "js" / "app.js").read_text(encoding="utf-8")
_TABS = (_PROJECT / "frontend" / "js" / "tab-context.js").read_text(encoding="utf-8")
_WORKSPACE = (_PROJECT / "frontend" / "js" / "workspace-tabs.js").read_text(encoding="utf-8")
_RUNTIME = (_PROJECT / "frontend" / "js" / "pdf-work-runtime.js").read_text(encoding="utf-8")
_STORE = (_PROJECT / "frontend" / "js" / "local-store.js").read_text(encoding="utf-8")
_SURFACE = (_PROJECT / "frontend-app" / "src" / "features" / "work" / "main-surface.ts").read_text(encoding="utf-8")
_VIEW = (_PROJECT / "frontend-app" / "src" / "features" / "work" / "WorkMainSurface.vue").read_text(encoding="utf-8")
_LIFECYCLE = (_PROJECT / "frontend-app" / "src" / "features" / "work" / "detail-lifecycle.ts").read_text(encoding="utf-8")
_SESSION = (_PROJECT / "frontend-app" / "src" / "features" / "work" / "session.ts").read_text(encoding="utf-8")


def _between(src, start, end):
    i = src.index(start)
    return src[i:src.index(end, i)]


class WorkPdfBoundaryTests(unittest.TestCase):
    def test_mount_uses_the_deferred_pane_host(self):
        kind_gate = _LIFECYCLE.index("if (inferredKind === 'pdf' && current.file_path)")
        call = _LIFECYCLE.index("initPdf(ctx, current)")
        self.assertLess(kind_gate, call)
        self.assertIn("'/js/components/works-pdf.js'", _LIFECYCLE[kind_gate:call])
        self.assertIn("pdfModule.initPdfViewerForWork", _LIFECYCLE[kind_gate:call])
        attach = _LIFECYCLE.index("const attach = () => {")
        present = _LIFECYCLE.index("presentWork(ctx, contentDiv,", attach)
        self.assertLess(attach, call)
        self.assertLess(call, present)
        painted = _SESSION.index("const painted = presentRouteSurface(")
        attach_call = _SESSION.index("input.attach()", painted)
        self.assertLess(painted, attach_call)
        self.assertIn("if (!painted) return false", _SESSION[painted:attach_call])
        self.assertNotIn("function renderWorkDetails", _WORKS)

        setup = _between(_PDF, "export function initPdfViewerForWork", "function prksReconcilePdfMutationMode")
        self.assertIn("pdfDeferredSetup", setup)
        self.assertIn("if (_pdfStale()) return;", setup)
        self.assertIn('ctx.query(\'[data-prks-role="pdf-viewer"]\')', setup)
        self.assertIn("ctx.resourceTicket(_pdfGen)", setup)
        self.assertIn("ctx.registerResource(_pdfTicket", setup)
        self.assertIn("kind: 'pdf'", setup)
        self.assertIn("suspendable: true", setup)
        self.assertNotIn("ctx.setResource('pdf'", setup)
        self.assertIn("runtime.destroy();", setup)
        self.assertIn("prksRequest(String(work.file_path)", setup)
        self.assertNotIn("savePdfAnnotation", _SURFACE + _VIEW)
        self.assertNotIn("works-pdf.js", _SURFACE + _VIEW)

    def test_route_change_flushes_last_page_before_the_next_paint(self):
        route = _between(_APP, "async function prksCommitTabRouteRender", "const contentDiv = ctx.root;")
        self.assertIn("ctx.getResource('pdf')", route)
        self.assertIn("prevPdf.flushLastPage()", route)
        self.assertIn("const prevRoute = ctx.lastResolvedRoute || null", route)
        self.assertLess(route.find("const prevRoute"), route.find("prevHash = prevRoute"))
        self.assertNotIn("prksHasPendingWorkAnnotationSync", route)
        wrapper = _between(_APP, "async function prksRenderTabRoute", "async function prksCommitTabRouteRender")
        self.assertIn("prksReadTabLeave()", wrapper)
        self.assertIn("leaveApi.run", wrapper)
        self.assertIn("{ cancelled: true, reason: 'cancelled' }", wrapper)
        self.assertIn("leaveApproved", wrapper)
        self.assertIn("function prksHasPendingWorkAnnotationSync(ctx)", _RUNTIME)
        self.assertIn("function savePdfAnnotation(workId, desired, observed)", _STORE)

    def test_tab_close_disposes_the_pdf_resource(self):
        close = _between(_WORKSPACE, "function closeTab(tabId)", "function closeTabIds")
        self.assertIn("destroyContext(tabId)", close)
        destroy = _between(_WORKSPACE, "function destroyContext(tabId)", "function resetAllContexts")
        self.assertIn("prksDestroyTabContext(tabId)", destroy)
        teardown = _between(_TABS, "function teardownRuntime()", "ctx.beginRoute = function")
        self.assertIn("clearAllResources();", teardown)

    def test_hide_unmounts_and_warm_park_keeps_the_host(self):
        hide = _between(_WORKSPACE, "function hideLeaf(tabId)", "function tileTab(tabId)")
        self.assertIn("coldParkContext(tabId)", hide)
        self.assertIn("refreshFocusedPanel()", hide)
        cold = _between(_WORKSPACE, "function coldParkContext(tabId)", "function warmParkContext(tabId)")
        self.assertIn("prksUnmountTabContext(tabId, 'park')", cold)
        warm = _between(_TABS, "function prksWarmParkTabContext", "function prksNotifyTabHostReparent")
        self.assertIn("ctx.getResource('pdf')", warm)
        self.assertIn("ctx.suspend(parking)", warm)
        self.assertNotIn("clearResource('pdf')", warm)
        self.assertNotIn("updatePanelContent", warm)

    def test_stale_setup_timer_does_not_mount_after_a_to_b(self):
        begin = _between(_TABS, "ctx.beginRoute = function", "ctx.mount = function")
        self.assertIn("teardownRuntime();", begin)
        self.assertIn("ctx.generation += 1;", begin)
        teardown = _between(_TABS, "function teardownRuntime()", "ctx.beginRoute = function")
        self.assertIn("clearAllTimers();", teardown)
        setup = _between(_PDF, "export function initPdfViewerForWork", "function prksReconcilePdfMutationMode")
        self.assertLess(setup.index("const _pdfGen = ctx.generation;"), setup.index("setTimeout"))
        self.assertLess(setup.index("ctx.resourceTicket(_pdfGen)"), setup.index("setTimeout"))
        self.assertLess(setup.index("if (_pdfStale()) return;"), setup.index("ctx.registerResource(_pdfTicket"))


if __name__ == "__main__":
    unittest.main()
