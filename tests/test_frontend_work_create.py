"""Structural + Node regressions for New File creation UX."""
from __future__ import annotations

import os
import shutil
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_INDEX = os.path.join(_FRONTEND, "index.html")
_UI = os.path.join(_FRONTEND, "js", "ui.js")
_APP = os.path.join(_FRONTEND, "js", "app.js")
_CSS = os.path.join(_FRONTEND, "css", "style.css")
_DESIGN = os.path.join(_PROJECT_DIR, "DESIGN.md")
_AGENTS = os.path.join(_PROJECT_DIR, "AGENTS.md")
_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_ribbon_create_selftest.js")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendWorkCreateTests(unittest.TestCase):
    def test_single_ribbon_create_affordance(self):
        html = _read(_INDEX)
        center = html.split("top-ribbon__center", 1)[1].split("top-ribbon__right", 1)[0]
        self.assertIn('id="prks-ribbon-create"', center)
        self.assertIn('id="prks-ribbon-new-file"', center)
        self.assertIn('id="prks-ribbon-new-more"', center)
        self.assertIn("openModal('work-modal')", center)
        self.assertNotIn("New…", center)
        self.assertIn('aria-haspopup="menu"', center)
        self.assertEqual(center.count('id="prks-ribbon-new-file"'), 1)
        self.assertEqual(center.count('id="prks-ribbon-new-more"'), 1)

    def test_modal_primary_and_advanced_hierarchy(self):
        html = _read(_INDEX)
        modal = html.split('id="work-modal"', 1)[1].split('id="folder-modal"', 1)[0]
        self.assertIn("work-create-source-heading", modal)
        self.assertIn("work-create-basic-heading", modal)
        self.assertIn(">Source<", modal)
        self.assertIn("Basic information", modal)
        self.assertIn('id="work-title"', modal)
        self.assertIn('id="work-folder-search"', modal)
        self.assertIn('id="work-doc-type"', modal)
        self.assertIn('id="upload-person-search"', modal)
        self.assertIn('id="work-upload-biblio-details"', modal)
        self.assertIn('id="work-upload-more-details"', modal)
        self.assertIn('id="work-publisher"', modal)
        self.assertIn('id="work-doi"', modal)
        self.assertIn('id="work-isbn"', modal)
        self.assertIn("inert", modal)
        self.assertIn("Create File", modal)
        self.assertIn("Cancel", modal)
        self.assertNotIn("Add File", modal)
        self.assertIn("modal-footer--sticky", modal)
        footer = modal.split("modal-footer", 1)[1]
        self.assertIn("save-work-btn", footer)
        self.assertIn("work-modal-cancel", footer)
        self.assertIn("prks-btn--primary", footer)
        self.assertNotIn("prks-btn--danger", footer.split("work-modal-cancel", 1)[1][:200])
        body_before_footer = modal.split("modal-footer", 1)[0]
        self.assertNotIn("save-work-btn", body_before_footer)

    def test_selected_source_markup(self):
        html = _read(_INDEX)
        modal = html.split('id="work-modal"', 1)[1].split('id="folder-modal"', 1)[0]
        self.assertIn('id="upload-selected-file"', modal)
        self.assertIn('id="upload-selected-file-name"', modal)
        self.assertIn('id="upload-selected-file-size"', modal)
        self.assertIn('id="upload-pdf-change-btn"', modal)
        self.assertIn("Change", modal)
        self.assertIn('id="work-file-error"', modal)
        self.assertIn('id="work-video-url-error"', modal)
        self.assertIn('id="work-folder-error"', modal)
        self.assertIn("Uncategorized", modal)
        self.assertNotIn("Library root", modal)

    def test_js_preserves_canonical_create_path(self):
        app = _read(_APP)
        ui = _read(_UI)
        work_chunk = app.split("document.getElementById('save-work-btn')", 1)[1].split(
            "const folderTitleInput", 1
        )[0]
        self.assertIn("__prksWorkCreateInFlight", work_chunk)
        self.assertIn("prksSetWorkModalCreateBusy", work_chunk)
        self.assertIn("Choose a PDF file.", work_chunk)
        self.assertIn("Enter a valid YouTube URL.", work_chunk)
        self.assertIn("prksIsWorkModalFolderCommitted", work_chunk)
        self.assertIn("Choose a folder from the list", work_chunk)
        self.assertIn("Could not read this PDF", work_chunk)
        self.assertIn("prksNavigate", work_chunk)
        self.assertNotIn("window.location.reload()", work_chunk)
        self.assertIn("Creating…", ui)
        self.assertIn("prksShowUploadPdfSelected", ui)
        self.assertIn("prksSetWorkModalFolderFromId", ui)
        self.assertIn("prksFolderIdFromFocusedContext", ui)
        self.assertIn("prksCanonicalUncategorizedFolder", ui)
        self.assertIn("prksIsWorkModalFolderCommitted", ui)
        self.assertIn("prksIsValidYoutubeUrl", ui)
        self.assertIn("prksIsRecognizedYoutubeHost", ui)
        self.assertNotIn("Library root", ui)
        self.assertIn("prksSyncWorkModalDisclosureInert", ui)
        self.assertIn("switched: true", ui)

    def test_folder_combobox_commit_semantics(self):
        ui = _read(_UI)
        # Free text must not be treated as a selection: oninput clears both
        # the hidden folder ID and the explicit default-commit flag.
        oninput_chunk = ui.split(
            "hidden.value = '';\n        if (type === 'folder') delete input.dataset.prksFolderDefault;",
            1,
        )
        self.assertEqual(len(oninput_chunk), 2, "expected exactly one folder-aware oninput clearer")
        # Selecting a real folder result clears the default flag too.
        self.assertIn("delete input.dataset.prksFolderDefault", ui)
        # The synthetic default row is suppressed when a real Uncategorized
        # folder already exists in loaded data (no duplicate-looking rows).
        self.assertIn("prksCanonicalUncategorizedFolder(data)", ui)

    def test_no_meaningless_for_attribute_on_error_paragraphs(self):
        ui = _read(_UI)
        set_field_error = ui.split("function prksSetWorkModalFieldError", 1)[1].split(
            "\n}\n", 1
        )[0]
        self.assertNotIn("setAttribute('for'", set_field_error)
        self.assertIn("aria-describedby", set_field_error)
        self.assertIn("aria-invalid", set_field_error)

    def test_video_source_is_youtube_only_contract(self):
        html = _read(_INDEX)
        modal = html.split('id="work-modal"', 1)[1].split('id="folder-modal"', 1)[0]
        self.assertIn(">YouTube<", modal)
        self.assertIn("YouTube URL", modal)
        self.assertIn('aria-describedby="work-video-url-error"', modal)
        self.assertIn('aria-describedby="work-folder-error"', modal)
        self.assertIn('aria-describedby="work-date-error"', modal)

    def test_backend_video_url_is_authoritative(self):
        server = _read(os.path.join(_PROJECT_DIR, "backend", "server.py"))
        self.assertIn("_validate_youtube_url", server)
        self.assertIn("Invalid YouTube URL", server)
        # Hostname matching must be an explicit set, not substring matching.
        self.assertIn("_YOUTUBE_HOSTS", server)
        self.assertNotIn("'youtube.com' in host", server)

    def test_css_sticky_create_footer(self):
        css = _read(_CSS)
        self.assertIn("#work-modal.modal--create-file", css)
        self.assertIn(".modal-footer--sticky", css)
        self.assertIn(".upload-selected-file", css)
        self.assertIn(".prks-split-btn", css)
        self.assertIn(".prks-create-section__title", css)

    def test_docs(self):
        design = _read(_DESIGN)
        agents = _read(_AGENTS)
        self.assertIn("New File / Work creation", design)
        self.assertIn("sticky modal footer", design.lower())
        self.assertIn("canonical Work creation path", agents)
        self.assertIn("ribbon-create.js", agents)

    def test_ribbon_create_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for ribbon-create tests")
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


if __name__ == "__main__":
    unittest.main()
