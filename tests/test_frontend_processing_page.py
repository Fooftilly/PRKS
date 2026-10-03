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
        self.assertIn("Object.assign({ rescan: true }", app)
        self.assertIn("fetchProcessingFiles(fileRequest)", app)
        self.assertIn("async function prksReloadProcessingFiles(", app)
        self.assertIn("window.prksVueDismissRoute(ctx)", app)

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

    def test_route_unmount_releases_preview_resources(self):
        """Shared route dismiss unmounts the tree; the component releases the preview."""
        page = _read(os.path.join(_VUE, "ProcessingFilesRoute.vue"))
        unmount = page.split("onBeforeUnmount(() => {", 1)[1].split("})", 1)[0]
        self.assertIn("props.intents.releaseResources()", unmount)
        intents = _read(os.path.join(_VUE, "intents.ts"))
        release = intents.split("releaseResources() {", 1)[1].split("},", 1)[0]
        self.assertIn("window.prksProcessingReleaseResources?.(owner)", release)

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

    def test_failed_reads_keep_the_painted_inbox_and_people(self):
        app = _read(_APP)
        present = app.split("function prksPresentVueProcessing(", 1)[1].split(
            "async function prksReloadProcessingFiles(", 1
        )[0]
        self.assertIn("window.__prksProcessingPeople = Array.isArray(detail.people)", present)
        reload = app.split("async function prksReloadProcessingFiles(", 1)[1].split(
            "async function prksLoadProcessingInbox(", 1
        )[0]
        self.assertLess(reload.index("readError"), reload.index("ctx.routeSidebar"))
        self.assertLess(reload.index("if (readError) return readError;"), reload.index("prksPresentVueProcessing"))
        load = app.split("async function prksLoadProcessingInbox(", 1)[1].split(
            "window.prksReloadProcessingFiles", 1
        )[0]
        self.assertIn("errorOwner", load)
        self.assertIn("prksConsumeApiError", load)
        self.assertIn("filesError", load)
        api = _read(os.path.join(_PROJECT_DIR, "frontend", "js", "api.js"))
        fetch = api.split("async function fetchProcessingFiles(", 1)[1].split(
            "async function patchProcessingFile(", 1
        )[0]
        self.assertIn("return [];", fetch)
        quick = _read(_PROCESSING).split("async function prksProcessingQuickCreatePerson(", 1)[1].split(
            "window.prksProcessingRoleTypes", 1
        )[0]
        self.assertLess(
            quick.index("window.__prksProcessingPeople"),
            quick.index("await prksQuickCreatePersonForSearchField"),
        )
        folder = _read(_PROCESSING).split("async function prksProcessingQuickCreateFolder(", 1)[1].split(
            "async function prksProcessingQuickCreatePerson(", 1
        )[0]
        self.assertIn("foldersFailed", folder)
        self.assertLess(folder.index("if (!foldersFailed)"), folder.index("allFolders = folders"))
        card = _read(os.path.join(_VUE, "ProcessingFileCard.vue"))
        self.assertIn("processingPeopleAfterCreate", card)
        self.assertNotIn("created.people.length", card)
        self.assertIn("processingFoldersAfterCreate", card)

    def test_quick_create_failures_stay_on_the_card(self):
        processing = _read(_PROCESSING)
        folder = processing.split("async function prksProcessingQuickCreateFolder(", 1)[1].split(
            "async function prksProcessingQuickCreatePerson(", 1
        )[0]
        self.assertNotIn("prksAlertMessage", folder)
        self.assertIn("Enter folder title in search field first.", folder)
        self.assertIn("Could not create folder.", folder)
        person = processing.split("async function prksProcessingQuickCreatePerson(", 1)[1].split(
            "window.prksProcessingRoleTypes", 1
        )[0]
        self.assertIn("localError: true", person)
        self.assertIn("result.message", person)
        ui = _read(os.path.join(_PROJECT_DIR, "frontend", "js", "ui.js"))
        helper = ui.split("async function prksQuickCreatePersonForSearchField(", 1)[1].split(
            "async function prksQuickCreatePersonForRoleLink(", 1
        )[0]
        self.assertIn("options.localError", helper)
        self.assertIn("return { ok: false, message: 'Type a name in the Person field first.' }", helper)
        self.assertIn("return { ok: false, message: 'Could not create person.' }", helper)
        self.assertIn("await prksAlertMessage('Type a name in the Person field first.', 'Validation');", helper)
        self.assertIn("await prksAlertMessage('Could not create person.', 'Error');", helper)
        intents = _read(os.path.join(_VUE, "intents.ts"))
        create_tag = intents.split("async createTag(name)", 1)[1].split("async quickCreateFolder(", 1)[0]
        self.assertNotIn("prksAlertMessage", create_tag)
        self.assertIn("Could not create tag.", create_tag)
        card = _read(os.path.join(_VUE, "ProcessingFileCard.vue"))
        self.assertIn("creatingFolder", card)
        self.assertIn(':busy="creatingFolder"', card)
        self.assertIn(':disabled="creatingFolder"', card)
        self.assertIn('busy-label="Creating…"', card)
        self.assertIn("if (creatingFolder.value) return", card)


if __name__ == "__main__":
    unittest.main()
