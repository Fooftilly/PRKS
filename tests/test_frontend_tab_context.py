"""TabContext unit selftest (Node) + structural guards for the runtime migration."""
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_JS = os.path.join(ROOT, "frontend", "js")
SELFTEST = os.path.join(ROOT, "tests", "browser", "run_tab_context_selftest.js")

# Step-11 regex: zero runtime matches expected after migration.
_MIGRATED_GLOBALS_RE = re.compile(
    r"window\.(currentWork|currentFolder|currentPerson|currentPersonGroup"
    r"|currentPlaylist|currentPdfViewer|workNotesEasyMDE|saveNotesTimeout"
    r"|annotationSyncInterval|__prks(RouteGen|RouteAbortController"
    r"|LastResolvedHash|RouteSidebar|WorkAnnotationSyncState"
    r"|PdfAnnotationEditorState|WikiTitleMap|WikiWorkList"
    r"|ConceptHintList|ArgumentHintList))"
)


def _scan_frontend_js(pattern):
    """Return list of (relpath, lineno, line) for every match in frontend/js/."""
    hits = []
    for dirpath, _dirs, files in os.walk(FRONTEND_JS):
        for fname in sorted(files):
            if not fname.endswith(".js"):
                continue
            fpath = os.path.join(dirpath, fname)
            rel = os.path.relpath(fpath, ROOT)
            with open(fpath, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    if pattern.search(line):
                        hits.append((rel, i, line.rstrip()))
    return hits


class TestTabContextSelftest(unittest.TestCase):
    """Run the Node-based selftest for TabContext primitives."""

    def test_selftest_passes(self):
        self.assertTrue(os.path.isfile(SELFTEST), "selftest script missing")
        result = subprocess.run(
            ["node", SELFTEST],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            self.fail(
                f"TabContext selftest failed (exit {result.returncode}):\n"
                f"{result.stdout}\n{result.stderr}"
            )


class TestMigratedGlobalsAbsent(unittest.TestCase):
    """Structural guard: no migrated window globals remain in frontend/js."""

    def test_no_runtime_globals(self):
        hits = _scan_frontend_js(_MIGRATED_GLOBALS_RE)
        if hits:
            lines = "\n".join(f"  {r}:{n}: {l}" for r, n, l in hits[:20])
            self.fail(
                f"{len(hits)} migrated-global reference(s) found:\n{lines}"
            )


class TestTabContextResourceAPI(unittest.TestCase):
    """Structural guard: tab-context.js exports focused resource helpers."""

    def test_focused_resource_exported(self):
        tc_path = os.path.join(FRONTEND_JS, "tab-context.js")
        with open(tc_path, encoding="utf-8") as fh:
            src = fh.read()
        for name in (
            "prksFocusedResource",
            "prksSetFocusedResource",
            "prksClearFocusedResource",
            "prksFocusedTimer",
            "prksClearFocusedTimer",
            "prksFocusedRouteSidebar",
        ):
            self.assertIn(name, src, f"{name} not found in tab-context.js")


class TestResearchGraphNoSingletonFallback(unittest.TestCase):
    """Graph cleanup: activeRuntime singleton must not be used as fallback."""

    def test_no_active_runtime_variable(self):
        rg_path = os.path.join(
            FRONTEND_JS, "components", "research-graph.js"
        )
        with open(rg_path, encoding="utf-8") as fh:
            src = fh.read()
        # The variable declaration `let activeRuntime = null;` must be gone.
        self.assertNotIn(
            "let activeRuntime", src,
            "activeRuntime singleton variable still present"
        )
        # resolveActiveRuntime must not fall back to activeRuntime.
        resolve_fn = re.search(
            r"function resolveActiveRuntime\(\)\s*\{(.*?)\}", src, re.S
        )
        self.assertIsNotNone(resolve_fn, "resolveActiveRuntime not found")
        self.assertNotIn(
            "activeRuntime", resolve_fn.group(1),
            "resolveActiveRuntime still references activeRuntime"
        )


class TestLatePdfInitProtection(unittest.TestCase):
    """Late PDF init: initPdfViewerForWork must destroy viewer if stale."""

    def test_stale_check_before_set(self):
        pdf_path = os.path.join(
            FRONTEND_JS, "components", "works-pdf.js"
        )
        with open(pdf_path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("_pdfStale()", src, "late-init stale guard missing")
        self.assertIn("ctx.getResource('pdf') !== runtime", src)
        stale_block = re.search(
            r"if\s*\(_pdfStale\(\)\s*\|\|\s*ctx\.getResource\('pdf'\)\s*!==\s*runtime\)\s*\{(.*?)return;",
            src,
            re.S,
        )
        self.assertIsNotNone(stale_block, "stale guard block not found")
        self.assertIn(
            "viewer.destroy", stale_block.group(1),
            "viewer.destroy missing in stale guard"
        )


class TestWorkPageLocalIdsGone(unittest.TestCase):
    """Work route-local IDs must be data-prks-role, not globally fixed."""

    def test_works_markup_uses_roles(self):
        w_path = os.path.join(FRONTEND_JS, "components", "works.js")
        with open(w_path, encoding="utf-8") as fh:
            src = fh.read()
        for forbidden in (
            'id="pdf-viewer"',
            'id="research-notes-editor"',
            'id="annotation-sync-status"',
            'id="editor-status"',
            'id="work-notes-collapse-btn"',
            'id="work-notes-editor-region"',
            'id="work-header-doc-type-slot"',
        ):
            self.assertNotIn(forbidden, src, forbidden + " still globally fixed")
        self.assertIn('data-prks-role="pdf-viewer"', src)
        self.assertIn('data-prks-role="research-notes-editor"', src)
        self.assertIn("ctx.query('[data-prks-role=\"research-notes-editor\"]')", src)

    def test_pdf_init_takes_ctx(self):
        pdf_path = os.path.join(FRONTEND_JS, "components", "works-pdf.js")
        with open(pdf_path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("export function initPdfViewerForWork(ctx, work)", src)
        self.assertIn('ctx.setResource(\'pdf\'', src)
        self.assertIn("if (_pdfStale())", src)
        self.assertIn("viewer.destroy", src)


class TestScriptOrderAndRenderer(unittest.TestCase):
    def test_tab_context_script_order(self):
        index = os.path.join(ROOT, "frontend", "index.html")
        with open(index, encoding="utf-8") as fh:
            html = fh.read()
        nav = html.find('src="/js/navigation.js"')
        ws = html.find('src="/js/workspace-tabs.js"')
        tc = html.find('src="/js/tab-context.js"')
        pdf_rt = html.find('src="/js/pdf-work-runtime.js"')
        app = html.find('src="/js/app.js"')
        self.assertNotEqual(nav, -1)
        self.assertLess(nav, ws)
        self.assertLess(ws, tc)
        self.assertLess(tc, pdf_rt)
        self.assertLess(pdf_rt, app)

    def test_render_tab_route_signature(self):
        app = os.path.join(FRONTEND_JS, "app.js")
        with open(app, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("async function prksRenderTabRoute(ctx, hash, options)", src)
        self.assertIn("return { cancelled: true, reason: 'pending-sync' }", src)
        self.assertNotIn("window.location.hash = revertHash", src)


class TestEntityMigrationIntegration(unittest.TestCase):
    """Integration: ctx.setEntity/getEntity used for entity ownership."""

    def test_works_uses_ctx_set_entity(self):
        w_path = os.path.join(FRONTEND_JS, "components", "works.js")
        with open(w_path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn(
            "ctx.setEntity('work'", src,
            "works.js must use ctx.setEntity for work ownership"
        )

    def test_folders_uses_ctx_set_entity(self):
        f_path = os.path.join(FRONTEND_JS, "components", "folders.js")
        with open(f_path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn(
            "ctx.setEntity('folder'", src,
            "folders.js must use ctx.setEntity for folder"
        )

    def test_people_uses_ctx_set_entity(self):
        p_path = os.path.join(FRONTEND_JS, "components", "people.js")
        with open(p_path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn(
            "ctx.setEntity('person'", src,
            "people.js must use ctx.setEntity for person"
        )


_PAGE_CONTENT_HOST_FILES = {
    "frontend/js/tab-context.js",
    "frontend/js/workspace-tabs.js",
    "frontend/js/app.js",
    "frontend/js/work-selection.js",
    "frontend/js/components/folders.js",
}

_ROUTE_RUNTIME_GLOBALS_RE = re.compile(
    r"window\.(__prksPdfPageSession|__prksPdfLastPageDetach|"
    r"__prksPdfLastPageDebounceClear|__prksAnnotationListCache|"
    r"__prksFlushWorkAnnotationPersistence|__prksPersonDetailEditing|"
    r"__prksPersonWorksEditing|__prksPlaylistDetailEditing|"
    r"__prksArgumentDetailEditing|__prksWorkFolderEdit|"
    r"__prksWorkPlaylistEdit|__prksPersonGroupDetailEditing)"
)

_FOCUSED_WORK_ENTITY_RE = re.compile(r"prksSetFocusedEntity\(\s*['\"]work['\"]")
_RIGHT_PANEL_TAB_WRITE_RE = re.compile(r"ctx\.ui\.rightPanelTab\s*=")
_RIGHT_PANEL_TAB_READ_RE = re.compile(r"(?:ctx|focusedCtx)\.ui\.rightPanelTab")
_WORK_DETAIL_EDIT_GLOBALS_RE = re.compile(
    r"__prksWorkFolderEdit|__prksWorkPlaylistEdit|__prksPersonGroupDetailEditing"
)

_PAGE_CONTENT_GET_RE = re.compile(r"getElementById\(\s*['\"]page-content['\"]\s*\)")


class TestPageContentHostOnly(unittest.TestCase):
    """Feature renderers must not treat #page-content as a route content target."""

    def test_feature_files_do_not_lookup_page_content(self):
        hits = _scan_frontend_js(_PAGE_CONTENT_GET_RE)
        bad = [(r, n, l) for r, n, l in hits if r not in _PAGE_CONTENT_HOST_FILES]
        if bad:
            lines = "\n".join(f"  {r}:{n}: {l}" for r, n, l in bad[:20])
            self.fail(f"{len(bad)} feature #page-content lookup(s):\n{lines}")

    def test_host_files_classified(self):
        hits = _scan_frontend_js(_PAGE_CONTENT_GET_RE)
        found = {r for r, _n, _l in hits}
        for required in (
            "frontend/js/tab-context.js",
            "frontend/js/workspace-tabs.js",
            "frontend/js/app.js",
        ):
            self.assertIn(required, found, required + " should still resolve the host")


class TestRouteRuntimeGlobalsAbsent(unittest.TestCase):
    """Multi-instance blockers: PDF/detail edit flags must not be window globals."""

    def test_no_route_runtime_globals(self):
        hits = _scan_frontend_js(_ROUTE_RUNTIME_GLOBALS_RE)
        if hits:
            lines = "\n".join(f"  {r}:{n}: {l}" for r, n, l in hits[:20])
            self.fail(f"{len(hits)} route-runtime global(s):\n{lines}")

    def test_no_work_or_person_group_edit_globals(self):
        hits = _scan_frontend_js(_WORK_DETAIL_EDIT_GLOBALS_RE)
        if hits:
            lines = "\n".join(f"  {r}:{n}: {l}" for r, n, l in hits[:20])
            self.fail(f"{len(hits)} Work/Person Group edit global(s):\n{lines}")


class TestPdfRuntimeShape(unittest.TestCase):
    def test_resource_is_runtime_not_viewer(self):
        pdf_path = os.path.join(FRONTEND_JS, "components", "works-pdf.js")
        with open(pdf_path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("createWorkPdfRuntime", src)
        self.assertIn("ctx.setResource('pdf', runtime", src)
        self.assertIn("runtime.viewer = viewer", src)
        self.assertIn("openPdfAnnotationEditorById(ctx, info.annotationId)", src)
        self.assertIn("runtime.annotationPersistence", src)
        self.assertIn("prksPdfPersistenceStillLive", src)
        self.assertIn("prksInstallPdfAnnotationPersistenceIfCurrent", src)
        self.assertIn("if (!stillLive()) return", src)
        self.assertNotIn("window.prksHasPendingWorkAnnotationSync = function", src)

    def test_pdf_runtime_module_exports(self):
        rt_path = os.path.join(FRONTEND_JS, "pdf-work-runtime.js")
        with open(rt_path, encoding="utf-8") as fh:
            src = fh.read()
        for name in (
            "createWorkPdfRuntime",
            "prksHasPendingWorkAnnotationSync",
            "hasPendingSync",
            "flushAnnotations",
            "flushLastPage",
            "getAnnotationHints",
            "createPdfAnnotationPersistenceWorker",
            "prksPdfPersistenceStillLive",
            "prksInstallPdfAnnotationPersistenceIfCurrent",
        ):
            self.assertIn(name, src, name + " missing from pdf-work-runtime.js")


class TestPdfAndNotesIsolationSelftests(unittest.TestCase):
    def test_pdf_runtime_selftest(self):
        script = os.path.join(ROOT, "tests", "browser", "run_pdf_runtime_selftest.js")
        result = subprocess.run(["node", script], capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            self.fail(result.stdout + "\n" + result.stderr)

    def test_work_notes_layout_selftest(self):
        script = os.path.join(ROOT, "tests", "browser", "run_work_notes_layout_selftest.js")
        result = subprocess.run(["node", script], capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            self.fail(result.stdout + "\n" + result.stderr)


class TestNoAsyncWorkFocusedEntity(unittest.TestCase):
    def test_no_prks_set_focused_entity_work_calls(self):
        hits = _scan_frontend_js(_FOCUSED_WORK_ENTITY_RE)
        if hits:
            lines = "\n".join(f"  {r}:{n}: {l}" for r, n, l in hits[:20])
            self.fail(f"{len(hits)} prksSetFocusedEntity('work' call(s):\n{lines}")


class TestRightPanelTabContextOwned(unittest.TestCase):
    def test_right_panel_tab_is_read_and_written(self):
        ui_path = os.path.join(FRONTEND_JS, "ui.js")
        with open(ui_path, encoding="utf-8") as fh:
            ui = fh.read()
        self.assertRegex(ui, _RIGHT_PANEL_TAB_WRITE_RE, "rightPanelTab never written in ui.js")
        self.assertIn("focusedCtx.ui.rightPanelTab", ui, "getActiveRightPanelTab must read focusedCtx.ui.rightPanelTab")
        works_path = os.path.join(FRONTEND_JS, "components", "works.js")
        with open(works_path, encoding="utf-8") as fh:
            works = fh.read()
        self.assertIn("ctx.ui.rightPanelTab", works)


if __name__ == "__main__":
    unittest.main()
