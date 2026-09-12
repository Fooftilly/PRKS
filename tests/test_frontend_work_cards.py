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

    def test_title_clamp_present(self):
        css = _read(_CSS)
        at = css.find(".project-card--work-card .card-title {")
        self.assertNotEqual(at, -1)
        block = css[at : at + 300]
        self.assertIn("-webkit-line-clamp: 2", block)

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
