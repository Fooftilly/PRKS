"""Viewer-owned annotation drawer stays on the pdf runtime."""
import json
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

    def test_open_details_panel_keeps_secondary_annotations_clear(self):
        css = (ROOT / "frontend" / "css" / "style.css").read_text(encoding="utf-8")
        rule = css[
            css.index("body.prks-right-panel-open #app-container.app-container--tiled .prks-workspace-canvas--tiled > :nth-child(3)") :
            css.index("/* Discoverable Close")
        ]
        self.assertIn('.prks-pdf-toolbar__group:has(> button[aria-label="Annotations"])', rule)
        self.assertIn("order: -1;", rule)
        self.assertNotIn("width:", rule)
        self.assertNotIn(".work-pdf-pane", rule)

    def test_show_on_pdf_opens_the_panel_owner(self):
        start = UI.index("function prksOpenAnnotationDrawerForPanel(button) {")
        end = UI.index("function updatePanelContent(tabId) {", start)
        source = UI[start:end]
        click = UI[UI.index('data-prks-role="open-pdf-annotation-drawer"'):UI.index("applyCachedAnnotationListToPanel")]
        self.assertIn("prksOpenAnnotationDrawerForPanel(openDrawer)", click)
        self.assertNotIn("prksOpenAnnotationDrawer(focusedCtx)", click)
        script = r"""
const vm = require('vm');
const source = %s;
const opened = [];
const panel = {
  dataset: { prksOwnerTabId: 'side', prksOwnerGeneration: '4' },
  contains(node) { return node === button; },
};
const button = { id: 'show' };
const owners = {
  side: { tabId: 'side', generation: 4, mounted: true, destroyed: false, isCurrent() { return true; } },
};
const context = {
  document: { getElementById(id) { return id === 'panel-content' ? panel : null; } },
  prksGetTabContext(id) { return owners[id] || null; },
  window: { prksOpenAnnotationDrawer(ctx) { opened.push(ctx); } },
  opened,
  button,
  panel,
  owners,
};
vm.createContext(context);
vm.runInContext(source + '; this.open = prksOpenAnnotationDrawerForPanel;', context);
context.open(button);
if (opened.length !== 1 || opened[0] !== owners.side) {
  throw new Error('expected the panel owner, got ' + opened.length);
}
opened.length = 0;
owners.side.destroyed = true;
context.open(button);
if (opened.length !== 0) throw new Error('destroyed owner opened');
owners.side.destroyed = false;
panel.dataset.prksOwnerGeneration = '9';
context.open(button);
if (opened.length !== 0) throw new Error('stale panel generation opened');
panel.dataset.prksOwnerGeneration = '4';
context.open({ id: 'outside' });
if (opened.length !== 0) throw new Error('control outside the panel opened');
panel.dataset.prksOwnerTabId = '';
context.open(button);
if (opened.length !== 0) throw new Error('missing owner opened');
console.log('panel-owner-ok');
""" % json.dumps(source)
        result = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            self.fail(result.stdout + "\n" + result.stderr)
        self.assertIn("panel-owner-ok", result.stdout)

    def test_valid_snapshots_publish_an_empty_list(self):
        apply = PDF[
            PDF.index("function applyAnnotationSnapshotToRuntime")
            : PDF.index("async function hydrateAnnotationBaseFromServer")
        ]
        self.assertNotIn("saved.length > 0", apply)
        self.assertIn("renderAnnotationFallbackList(saved, docId || 'DB', workId, ctx);", apply)
        keep = PDF[
            PDF.index("let nextList = ackList.filter")
            : PDF.index("await window.prksSync.store.resolveConflict")
        ]
        self.assertLess(
            keep.index("await window.prksReconcileViewerAnnotations"),
            keep.index("renderAnnotationFallbackList(nextList, publishedDocId, workId, ctx);"),
        )

    def test_popup_session_republishes_the_drawer(self):
        sync = PDF[
            PDF.index("function prksSyncAnnotationPopup")
            : PDF.index("function prksAnnotationDrawerMetadataLabels")
        ]
        self.assertIn("prksVueSyncWorkPdfAnnotationPopup", sync)
        self.assertIn("prksSyncAnnotationDrawer(ctx);", sync)
        script = r"""
const vm = require('vm');
const source = %s;
const calls = [];
const context = {
  window: { prksVueSyncWorkPdfAnnotationPopup(ctx) { calls.push(['popup', ctx]); } },
  prksSyncAnnotationDrawer(ctx) { calls.push(['drawer', ctx]); },
  calls,
};
vm.createContext(context);
vm.runInContext(source + '; this.sync = prksSyncAnnotationPopup;', context);
const owner = { tabId: 'side' };
context.sync(owner);
if (calls.length !== 2 || calls[0][0] !== 'popup' || calls[1][0] !== 'drawer' || calls[1][1] !== owner) {
  throw new Error('popup sync did not republish the drawer: ' + JSON.stringify(calls));
}
console.log('popup-republish-ok');
""" % json.dumps(sync)
        result = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            self.fail(result.stdout + "\n" + result.stderr)
        self.assertIn("popup-republish-ok", result.stdout)

    def test_confirm_delete_focus_stays_on_the_drawer_owner(self):
        hide = UI[UI.index("function prksHideModalConfirm"):UI.index("function prksFinishModalConfirm")]
        self.assertNotIn(
            'document.querySelector(\'[data-prks-role="pdf-annotation-drawer"] .annotation-row__delete\')',
            hide,
        )
        self.assertIn("prksReplacementAnnotationDelete(opener, ownerTabId)", hide)
        dialog = UI[UI.index("function prksConfirmDialog"):UI.index("function prksAlertDialog")]
        self.assertIn("prksRememberModalConfirmOpener(document.activeElement);", dialog)


if __name__ == "__main__":
    unittest.main()
