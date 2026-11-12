"""Processing Files: the coordinator rescans the inbox and Vue paints it."""
import json
import os
import shutil
import subprocess
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
        load = app.split("async function prksLoadProcessingInbox(", 1)[1].split(
            "window.prksReloadProcessingFiles", 1
        )[0]
        self.assertIn("window.prksProcessingRecords", load)
        self.assertIn("records.inbox({ rescan: true, signal: signal || undefined })", load)
        self.assertIn("async function prksReloadProcessingFiles(", app)
        self.assertIn("window.prksVueDismissRoute(ctx)", app)

    def test_legacy_module_owns_preview_lifetime_and_catalog_helpers(self):
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
        # Save and import belong to the Processing records service.
        for retired in (
            "prksProcessingSave",
            "prksProcessingImport",
            "prksProcessingNormalizeDraft",
            "patchProcessingFile",
            "importProcessingFile",
        ):
            self.assertNotIn(retired, src)
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
        self.assertIn("TabContextHostRootTests", rule)
        self.assertIn("#/processing-files", rule)
        self.assertNotIn('"features": ("browse",)', rule)
        e2e = _read(os.path.join(_PROJECT_DIR, "tests", "e2e", "test_app.py"))
        self.assertIn('a.nav-link[href="#/processing-files"]', e2e)
        self.assertIn("data-prks-processing-queue", e2e)

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
        # A failed or aborted inbox read paints no rows; only a failure has a message.
        self.assertIn("if (!prksAbortFallback(err)) filesError = records.actionMessage(err, filesFallback);", load)
        self.assertIn("return [];", load)
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


    def test_processing_records_own_inbox_reads_and_file_writes(self):
        api = _read(os.path.join(_PROJECT_DIR, "frontend", "js", "api.js"))
        for retired in ("fetchProcessingFiles", "patchProcessingFile", "importProcessingFile", "'processing-files':"):
            self.assertNotIn(retired, api)
        marks = api.split("function prksMarkProcessingImportChanged(", 1)[1].split("\n}\n", 1)[0]
        for mark in (
            "prksMarkFoldersDomainChanged()",
            "prksMarkPeopleDomainChanged()",
            "prksMarkPersonGroupsDomainChanged()",
            "prksMarkWorksBrowseChanged()",
            "prksMarkRecentlyAddedChanged()",
        ):
            self.assertIn(mark, marks)
        self.assertNotIn("prksMarkRecentChanged()", marks)
        app = _read(_APP)
        badge = app.split("async function prksRefreshProcessingNavBadge(", 1)[1].split(
            "async function prksRefreshSyncQueueCue(", 1
        )[0]
        self.assertIn("window.prksProcessingRecords", badge)
        self.assertNotIn("fetchProcessingFiles", app)
        intents = _read(os.path.join(_VUE, "intents.ts"))
        self.assertIn("records.save(fileId, draft)", intents)
        self.assertIn("records.importFile(fileId)", intents)
        self.assertNotIn("window.prksProcessingSave", intents)
        self.assertNotIn("window.prksProcessingImport", intents)
        records = _read(os.path.join(_VUE, "records.ts"))
        self.assertIn("clientErrorSource: 'processing-files.fetch'", records)
        self.assertIn("prksQueryKeys.processingFiles.all()", records)
        self.assertIn("window.prksMarkProcessingImportChanged?.()", records)
        session = _read(os.path.join(_VUE, "session.ts"))
        self.assertIn("target.prksProcessingRecords = processingRecords()", session)

    @unittest.skipUnless(shutil.which("node"), "node is required")
    def test_content_mode_waits_for_a_confirmed_answer_and_learns_it_without_reload(self):
        """An offline-bound page names no content (neither Processing page),
        and learns it from the first confirmed answer; no scope is SQLite."""
        app = _read(_APP)
        fn = "async function prksLibraryContentMode(" + app.split("async function prksLibraryContentMode(", 1)[1].split(
            "\n}\n", 1
        )[0] + "\n}\n"
        script = fn + """
const out = [];
function stub(bound, answers) {
    let content = null;
    return {
        resolve: async () => bound,
        libraryContent: () => content,
        verify: async () => { content = answers.shift() || null; return 'same'; },
    };
}
(async () => {
    globalThis.window = {};
    out.push(await prksLibraryContentMode());
    window.prksBrowserScope = stub(null, []);
    out.push(await prksLibraryContentMode());
    window.prksBrowserScope = stub({ libraryId: 'L' }, [null, 'postgres']);
    out.push(await prksLibraryContentMode());
    out.push(await prksLibraryContentMode());
    out.push(await prksLibraryContentMode());
    console.log(JSON.stringify(out));
})();
"""
        done = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), ["sqlite", "sqlite", None, "postgres", "postgres"])
        route = app[app.index("case 'processing-files':"):app.index("case 'search':")]
        self.assertIn("if (!contentMode) throw new Error(", route)
        badge = app.split("async function prksRefreshProcessingNavBadge(", 1)[1].split(
            "async function prksRefreshSyncQueueCue(", 1
        )[0]
        self.assertIn("if (!mode) {", badge)
        self.assertIn("prksSetProcessingAttentionCount(null)", badge)


if __name__ == "__main__":
    unittest.main()
