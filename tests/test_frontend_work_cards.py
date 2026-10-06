"""Structural + Node regressions for the shared Work-card renderer and Folder Library empty states."""
import os
import shutil
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_WORK_CARDS = os.path.join(_FRONTEND, "js", "components", "work-cards.js")
_FOLDERS = os.path.join(_FRONTEND, "js", "components", "folders.js")
_CSS = os.path.join(_FRONTEND, "css", "style.css")
_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_work_cards_selftest.js")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendWorkCardTests(unittest.TestCase):
    def test_node_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for work-card structural tests")
        proc = subprocess.run(
            [node, _RUNNER],
            cwd=_PROJECT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertIn("passed", proc.stdout)
        self.assertIn(", 0 failed", proc.stdout)
        self.assertNotIn("FAIL  ", proc.stdout)

    def test_meta_and_context_are_separate_css_areas(self):
        css = _read(_CSS)
        self.assertIn(".work-card__meta {", css)
        self.assertIn(".work-card__context {", css)

    def test_thumb_source_classes_and_frame_css_present(self):
        css = _read(_CSS)
        self.assertIn(".work-card__thumb--pdf", css)
        self.assertIn(".work-card__thumb--video", css)
        self.assertIn("object-fit: contain", css)
        # Video keeps full-bleed cover; only the PDF frame gets internal padding.
        pdf_at = css.find(".work-card__thumb--pdf {")
        self.assertNotEqual(pdf_at, -1)
        self.assertIn("padding", css[pdf_at : pdf_at + 200])
        self.assertIn(".work-card__thumb--loading", css)
        self.assertIn(".work-browse-collection--list", css)
        self.assertIn(".work-card-preview", css)
        # Error and empty must stay visually distinct (Unavailable vs kind label).
        self.assertIn('content: "Unavailable"', css)
        err_at = css.find(".work-card__thumb--error::before")
        empty_at = css.find(".work-card__thumb--empty::before")
        self.assertNotEqual(err_at, -1)
        self.assertNotEqual(empty_at, -1)

    def test_work_browse_mode_helpers_exported(self):
        src = _read(_WORK_CARDS)
        self.assertIn("prks.ui.workBrowseMode", src)
        self.assertIn("function prksGetWorkBrowseMode", src)
        self.assertIn("function prksWorkBrowseCollectionClass", src)
        self.assertIn("function prksWorkBrowseModeToggleHtml", src)
        self.assertIn("function prksBindWorkBrowseMode", src)
        self.assertIn("function prksShowWorkThumbPreview", src)
        self.assertIn("function prksWorkThumbFromHoverTarget", src)
        self.assertIn("function prksWorkCardFromEventTarget", src)
        self.assertIn("prksForgetPreviewImgSrc", src)
        self.assertIn("function prksReleaseWorkThumbPreview", src)
        self.assertIn("function prksReleaseLazyWorkThumbs", src)
        self.assertIn("prksPruneDisconnectedLazyWorkThumbs", src)
        self.assertIn("__prksWorkThumbObserved", src)
        self.assertIn('aria-hidden', src)
        self.assertNotIn("role', 'dialog'", src)
        self.assertNotIn('role", "dialog"', src)
        # Preference change must sync every mounted collection, not only the local host.
        self.assertIn("prksApplyWorkBrowseModeToDom(document, next)", src)
        # No URL-bearing thumb attrs that get read back into src/HTML sinks.
        self.assertNotIn("data-prks-thumb-preview-src", src)
        self.assertNotIn("data-prks-thumb-src=", src)
        self.assertIn("data-prks-thumb-lazy", src)
        # Preview release must run even on Folder→Folder preserve (sameFolderWorkspace).
        app = _read(os.path.join(_FRONTEND, "js", "app.js"))
        self.assertIn("prksReleaseWorkThumbPreview(contentDiv)", app)
        # Must not share the lazy-thumb `!sameFolderWorkspace` skip — preview has
        # no prune-on-init fallback.
        marker = "prksReleaseWorkThumbPreview(contentDiv)"
        at = app.find(marker)
        self.assertGreater(at, 0)
        window = app[max(0, at - 280) : at]
        self.assertNotIn("!sameFolderWorkspace && typeof window.prksReleaseWorkThumbPreview", window)
        # Folder→Folder keeps the shell. Collection lifetime still follows the
        # work/thumb fingerprint; Folder A→B preview dismiss is owned by the
        # presentation boundary while the previous thumb is still connected.
        detail = _read(os.path.join(
            _PROJECT_DIR, "frontend-app", "src", "features", "folder-detail", "FolderDetailRoute.vue"))
        self.assertIn("useWorkCardCollection(mainEl", detail)
        self.assertIn("workCardCollectionFingerprint", detail)
        self.assertIn("PrksWorkCard", detail)
        self.assertNotIn("prksReleaseWorkThumbPreview", detail)
        session = _read(os.path.join(
            _PROJECT_DIR, "frontend-app", "src", "features", "folder-detail", "session.ts"))
        read_at = session.find("readRouteSurface(input.owner)")
        present_at = session.find("presentRouteSurface({")
        self.assertGreater(read_at, 0)
        self.assertGreater(present_at, read_at)
        self.assertIn("prksReleaseWorkThumbPreview", session[read_at:present_at])
        self.assertIn("previous.params.folderId", session[read_at:present_at])
        self.assertIn("previous.mounted", session[read_at:present_at])
        self.assertIn("previous.generation", session[read_at:present_at])
        self.assertIn("input.host.isConnected", session[read_at:present_at])
        self.assertIn("input.host", session[read_at:present_at])
        lifetime = _read(os.path.join(
            _PROJECT_DIR, "frontend-app", "src", "components", "use-work-card-collection.ts"))
        self.assertIn("flush: 'pre'", lifetime)
        self.assertIn("flush: 'post'", lifetime)
        self.assertNotIn("onBeforeUpdate", lifetime)
        self.assertNotIn("onUpdated", lifetime)
        self.assertIn("prksReleaseWorkThumbPreview", lifetime)
        self.assertIn("function prksWorkThumbPreviewSourceConnected", src)
        hover_fn = src.find("function prksWorkThumbFromHoverTarget")
        hover_end = src.find("function prksWorkThumbPointerStillInside")
        self.assertGreater(hover_fn, 0)
        self.assertGreater(hover_end, hover_fn)
        self.assertIn(
            "t.closest('.work-card__thumb[data-prks-thumb-preview-kind]')",
            src[hover_fn:hover_end],
        )
        self.assertNotIn("prksWorkThumbFromCard", src[hover_fn:hover_end])
        self.assertNotIn("prksWorkThumbPreviewHoverArmed", src)
        self.assertNotIn("function prksWorkCardPointerStillInside", src)
        pointerout_at = src.find("'pointerout'")
        pointerout_end = src.find("'focusout'", pointerout_at)
        self.assertGreater(pointerout_at, 0)
        self.assertGreater(pointerout_end, pointerout_at)
        pointerout = src[pointerout_at:pointerout_end]
        self.assertIn("prksWorkThumbFromHoverTarget", pointerout)
        self.assertIn("prksWorkThumbPointerStillInside", pointerout)
        self.assertNotIn("prksWorkCardFromNode", pointerout)
        self.assertNotIn("prksWorkThumbFromCard", pointerout)
        self.assertIn("prksReleaseLazyWorkThumbs", lifetime)
        unmount = detail.find("onBeforeUnmount")
        self.assertEqual(unmount, -1)
        recent = _read(os.path.join(
            _PROJECT_DIR, "frontend-app", "src", "features", "recent", "RecentRoute.vue"))
        self.assertIn("useWorkCardCollection(rootEl", recent)
        self.assertIn("workCardCollectionFingerprint", recent)
        self.assertIn("PrksWorkCard", recent)
        self.assertIn("initWhen: () => !offlineCached.value", recent)
        consumers = [
            os.path.join(_PROJECT_DIR, "frontend-app", "src", "features", "search", "SearchResultsCollection.vue"),
            os.path.join(_PROJECT_DIR, "frontend-app", "src", "features", "progress", "ProgressView.vue"),
            os.path.join(_PROJECT_DIR, "frontend-app", "src", "features", "types", "TypeDetailRoute.vue"),
            os.path.join(_PROJECT_DIR, "frontend-app", "src", "features", "people", "PersonDetailRoute.vue"),
            os.path.join(_PROJECT_DIR, "frontend-app", "src", "features", "folder-library", "RecentlyAddedPane.vue"),
        ]
        for path in consumers:
            src = _read(path)
            self.assertIn("useWorkCardCollection", src, path)
            self.assertIn("workCardCollectionFingerprint", src, path)
            self.assertIn("source:", src, path)

    def test_saved_view_detail_exposes_browse_mode_toggle(self):
        sv = _read(os.path.join(
            _PROJECT_DIR, "frontend-app", "src", "features", "saved-views", "SavedViewDetailRoute.vue"))
        self.assertIn("prks-work-browse-mode-saved-view", sv)
        self.assertIn("prksWorkBrowseModeToggleHtml", sv)
        self.assertIn("prksBindWorkBrowseMode", sv)

    def test_folder_files_and_recently_added_use_browse_collection(self):
        route = _read(os.path.join(
            _PROJECT_DIR, "frontend-app", "src", "features", "folder-library", "FolderLibraryRoute.vue"))
        pane = _read(os.path.join(
            _PROJECT_DIR, "frontend-app", "src", "features", "folder-library", "RecentlyAddedPane.vue"))
        self.assertIn("prksWorkBrowseCollectionClass", pane)
        self.assertIn("prksWorkBrowseModeToggleHtml", route)
        self.assertIn("prks-work-browse-mode-recently-added", route)
        detail = _read(os.path.join(
            _PROJECT_DIR, "frontend-app", "src", "features", "folder-detail", "FolderDetailRoute.vue"))
        self.assertIn("prks-work-browse-mode-folder-files", detail)
        self.assertIn("prksWorkBrowseCollectionClass", detail)

    def test_title_clamp_present(self):
        css = _read(_CSS)
        marker = "\n.project-card--work-card .card-title {"
        at = css.find(marker)
        self.assertNotEqual(at, -1)
        block = css[at : at + 300]
        self.assertIn("-webkit-line-clamp: 2", block)
        # Compact list tightens to a single title line without replacing the card rule.
        list_marker = "\n.work-browse-collection--list .project-card--work-card .card-title {"
        list_at = css.find(list_marker)
        self.assertNotEqual(list_at, -1)
        self.assertIn("-webkit-line-clamp: 1", css[list_at : list_at + 120])

    def test_recently_added_uses_concise_date_helper(self):
        folders = _read(_FOLDERS)
        self.assertNotIn("prksRecentlyAddedDateLabel", folders)
        helper = _read(os.path.join(
            _PROJECT_DIR, "frontend-app", "src", "features", "folder-library", "recently-added.ts"))
        self.assertIn("export function recentlyAddedDateLabel", helper)
        pane = _read(os.path.join(
            _PROJECT_DIR, "frontend-app", "src", "features", "folder-library", "RecentlyAddedPane.vue"))
        self.assertIn("Added ${dateLabel}", pane)
        self.assertNotIn("Added: ${dateStr}", pane)
        self.assertNotIn("Added: ${dateLabel}", pane)

    def test_empty_folder_library_exposes_new_folder_action(self):
        src = _read(_FOLDERS)
        at = src.find("function prksFolderLibraryTreeInnerHtml")
        self.assertNotEqual(at, -1)
        block = src[at : at + 700]
        self.assertIn("No folders yet.", block)
        self.assertIn("data-prks-create-folder-query", block)
        self.assertIn("New folder", block)

    def test_empty_recently_added_exposes_new_file_action(self):
        pane = _read(os.path.join(
            _PROJECT_DIR, "frontend-app", "src", "features", "folder-library", "RecentlyAddedPane.vue"))
        self.assertIn("No files in the library yet.", pane)
        self.assertIn("openWorkModal", pane)
        self.assertIn("work-modal", _read(os.path.join(
            _PROJECT_DIR, "frontend-app", "src", "features", "folder-library", "intents.ts")))
        self.assertIn("No files match your search.", pane)
        self.assertIn('data-prks-role="new-work-from-recently-added"', pane)
        filtered = next(
            line for line in pane.splitlines() if "No files match your search." in line
        )
        self.assertNotIn("openModal", filtered)
        self.assertNotIn("work-modal", filtered)
        self.assertNotIn("new-work-from-recently-added", filtered)

    def test_vue_work_card_owns_card_markup(self):
        vue = _read(os.path.join(_PROJECT_DIR, "frontend-app", "src", "components", "PrksWorkCard.vue"))
        cards = _read(_WORK_CARDS)
        self.assertNotIn("function prksWorkCardHtml", cards)
        self.assertIn('class="work-card__link"', vue)
        self.assertIn("data-work-id", vue)
        self.assertIn("work-card__meta", vue)
        self.assertIn("work-card__context", vue)
        self.assertIn("work-card__badges", vue)
        self.assertIn("work-card__thumb--empty", vue)
        self.assertIn("work-card__thumb--loading", vue)
        self.assertIn('alt=""', vue)
        self.assertNotIn("function prksWorkCardHtml", vue)


if __name__ == "__main__":
    unittest.main()
