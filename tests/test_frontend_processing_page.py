"""Processing Files: the coordinator rescans the inbox and Vue paints it."""
import os
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP = os.path.join(_PROJECT_DIR, "frontend", "js", "app.js")
_PROCESSING = os.path.join(_PROJECT_DIR, "frontend", "js", "components", "processing-files.js")
_VUE = os.path.join(_PROJECT_DIR, "frontend-app", "src", "features", "processing")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendProcessingPageTests(unittest.TestCase):
    def test_route_rescans_and_paints_vue(self):
        app = _read(_APP)
        processing_at = app.index("case 'processing-files':")
        search_at = app.index("case 'search':")
        body = app[processing_at:search_at]
        self.assertIn("prksLoadProcessingInbox(routeSignal)", body)
        self.assertIn("publishSidebar({ pendingCount: loaded.items.length })", body)
        self.assertIn("prksPresentVueProcessing", body)
        self.assertNotIn("prksRenderProcessingFilesPageWithFetch", body)
        self.assertNotIn("renderProcessingFilesPage", body)
        self.assertIn("async function prksLoadProcessingInbox(", app)
        self.assertIn("fetchProcessingFiles(Object.assign({ rescan: true }", app)
        self.assertIn("async function prksReloadProcessingFiles(", app)
        self.assertIn("prksVueDismissProcessing", app)

    def test_legacy_module_owns_preview_lifetime_and_upload_import(self):
        src = _read(_PROCESSING)
        self.assertIn("window.PRKS_PEOPLE_ROLES", src)
        self.assertNotRegex(
            src,
            r"const\s+PRKS_PROCESSING_ROLE_TYPES\s*=\s*\[",
            "Processing must not keep a second hard-coded role array",
        )
        self.assertNotIn("renderProcessingFilesPage", src)
        self.assertNotIn("prksRenderProcessingFilesPageWithFetch", src)
        self.assertNotIn("__prksProcessingResizeBound", src)
        self.assertIn("function prksProcessingAttachResources(", src)
        self.assertIn("function prksProcessingReleaseResources(", src)
        self.assertIn("removeEventListener('resize', rec.onResize)", src)
        self.assertIn("rec.frame.removeAttribute('src')", src)
        self.assertIn("function prksProcessingSave(", src)
        self.assertIn("patchProcessingFile(", src)
        self.assertIn("function prksProcessingImport(", src)
        self.assertIn("importProcessingFile(", src)
        self.assertIn("prksCreateTagDurably(", src)
        self.assertIn("createFolder(", src)
        self.assertIn("prksQuickCreatePersonForSearchField(", src)
        self.assertNotIn("/api/folders", src)
        self.assertNotIn("fetch(", src)

    def test_vue_sources_use_work_html_slot(self):
        """Injected widget and preview anchors use the shared class."""
        for name in ("ProcessingFilesRoute.vue", "ProcessingFileCard.vue"):
            vue = _read(os.path.join(_VUE, name))
            self.assertIn("work-html-slot", vue, name)
            self.assertNotIn('style="display: contents"', vue, name)
            self.assertNotIn("fetch(", vue, name)
        page = _read(os.path.join(_VUE, "ProcessingFilesRoute.vue"))
        self.assertIn('id="prks-processing-refresh"', page)
        self.assertIn('data-prks-processing-anchor="layout"', page)
        card = _read(os.path.join(_VUE, "ProcessingFileCard.vue"))
        self.assertIn("data-prks-processing-status-host", card)

    def test_processing_vue_maps_to_the_processing_files_e2e(self):
        policy = _read(os.path.join(_PROJECT_DIR, "tests", "e2e", "policy.py"))
        rule = policy[policy.index('"name": "processing-vue"'):policy.index('"name": "concepts-vue"')]
        self.assertIn('"features": ("processing",)', rule)
        self.assertIn("test_processing_route_surface", rule)
        self.assertIn("#/processing-files", rule)
        self.assertNotIn('"features": ("browse",)', rule)
        e2e = _read(os.path.join(_PROJECT_DIR, "tests", "e2e", "test_processing_route_surface.py"))
        self.assertIn("prksNavigate('#/processing-files')", e2e)
        self.assertIn("data-prks-processing-page", e2e)


if __name__ == "__main__":
    unittest.main()
