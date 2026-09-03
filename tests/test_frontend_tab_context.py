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
        # The .then() handler must check staleness and destroy viewer.
        self.assertIn("_pdfStale()", src, "late-init stale guard missing")
        # There should be a viewer.destroy() call inside the stale branch.
        stale_block = re.search(
            r"if\s*\(_pdfStale\(\)\)\s*\{(.*?)\}", src, re.S
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
        app = html.find('src="/js/app.js"')
        self.assertNotEqual(nav, -1)
        self.assertLess(nav, ws)
        self.assertLess(ws, tc)
        self.assertLess(tc, app)

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

    def test_folders_uses_set_focused_entity(self):
        f_path = os.path.join(FRONTEND_JS, "components", "folders.js")
        with open(f_path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn(
            "prksSetFocusedEntity('folder'", src,
            "folders.js must use prksSetFocusedEntity for folder"
        )

    def test_people_uses_set_focused_entity(self):
        p_path = os.path.join(FRONTEND_JS, "components", "people.js")
        with open(p_path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn(
            "prksSetFocusedEntity('person'", src,
            "people.js must use prksSetFocusedEntity for person"
        )


if __name__ == "__main__":
    unittest.main()
