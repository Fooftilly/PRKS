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
        # Preserve path must also release against existingMain before the rewrite.
        folders = _read(_FOLDERS)
        self.assertIn("prksReleaseWorkThumbPreview(existingMain)", folders)
        rewrite = folders.find("existingMain.innerHTML = prksFolderDetailMainInnerHtml")
        self.assertGreater(rewrite, 0)
        self.assertLess(
            folders.find("prksReleaseWorkThumbPreview(existingMain)"),
            rewrite,
        )

    def test_saved_view_detail_exposes_browse_mode_toggle(self):
        sv = _read(os.path.join(_FRONTEND, "js", "saved-views.js"))
        self.assertIn("prks-work-browse-mode-saved-view", sv)
        self.assertIn("prksWorkBrowseModeToggleHtml", sv)
        self.assertIn("prksBindWorkBrowseMode", sv)

    def test_folder_files_and_recently_added_use_browse_collection(self):
        src = _read(_FOLDERS)
        self.assertIn("prksWorkBrowseCollectionClass", src)
        self.assertIn("prksWorkBrowseModeToggleHtml", src)
        self.assertIn("prks-work-browse-mode-folder-files", src)
        self.assertIn("prks-work-browse-mode-recently-added", src)

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
        src = _read(_FOLDERS)
        self.assertIn("function prksRecentlyAddedDateLabel", src)
        self.assertIn("Added ${dateLabel}", src)
        self.assertNotIn("Added: ${dateStr}", src)

    def test_empty_folder_library_exposes_new_folder_action(self):
        src = _read(_FOLDERS)
        at = src.find("function prksFolderLibraryTreeInnerHtml")
        self.assertNotEqual(at, -1)
        block = src[at : at + 700]
        self.assertIn("No folders yet.", block)
        self.assertIn("data-prks-create-folder-query", block)
        self.assertIn("New folder", block)

    def test_empty_recently_added_exposes_new_file_action(self):
        src = _read(_FOLDERS)
        at = src.find("function prksRenderFolderLibraryRecentlyAdded")
        self.assertNotEqual(at, -1)
        # Bounded by the function, not by a character count: a comment added
        # above the branch should not be able to hide it from this check.
        end = src.index("\nasync function prksLoadFolderLibraryRecentlyAdded(", at)
        block = src[at:end]
        self.assertIn("No files in the library yet.", block)
        self.assertIn("openModal(", block)
        self.assertIn("work-modal", block)
        self.assertIn("No files match your search.", block)
        # Filtered empty state (a search with no matches) is its own statement,
        # distinct from the empty-library branch — it must not also carry the
        # New File creation CTA.
        filtered_line = next(
            line for line in block.splitlines() if "No files match your search." in line
        )
        self.assertNotIn("openModal", filtered_line)
        self.assertNotIn("work-modal", filtered_line)


if __name__ == "__main__":
    unittest.main()
