"""Structural + Node regressions for New File creation UX."""
from __future__ import annotations

import os
import re
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
        self.assertIn("Publication details", modal)
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

    def test_essentials_first_enrichment_in_disclosures(self):
        """#85: essentials first; enrichment lives in collapsed Optional disclosures."""
        html = _read(_INDEX)
        modal = html.split('id="work-modal"', 1)[1].split('id="folder-modal"', 1)[0]
        body = modal.split("modal-footer", 1)[0]
        first_details = body.index('id="work-upload-biblio-details"')
        essentials = body[:first_details]
        for control in ('id="work-title"', 'id="work-folder-search"', 'id="work-doc-type"',
                        'id="work-year"', 'id="upload-person-search"', 'id="work-video-url"'):
            self.assertIn(control, essentials, control)
        enrichment = body[first_details:]
        for control in ('id="work-date"', 'id="work-publisher"', 'id="work-doi"',
                        'id="work-video-channel"', 'id="work-video-published-date"',
                        'id="work-video-urldate"', 'id="work-video-playlist-search"',
                        'id="upload-tag-search"', 'id="work-status"', 'id="work-abstract"'):
            self.assertIn(control, enrichment, control)
        self.assertEqual(body.count("work-upload-meta__summary-optional"), 2)
        self.assertEqual(body.count("data-prks-count-for="), 2)
        # Requiredness is text, and the controls say so to assistive tech.
        self.assertIn('class="prks-field__required"', essentials)
        folder = re.search(r'<input[^>]*id="work-folder-search"[^>]*>', modal).group(0)
        self.assertIn('aria-required="true"', folder)
        url = re.search(r'<input[^>]*id="work-video-url"[^>]*>', modal).group(0)
        self.assertIn('aria-required="true"', url)

    def test_people_have_no_role_surface_before_a_person(self):
        html = _read(_INDEX)
        modal = html.split('id="work-modal"', 1)[1].split('id="folder-modal"', 1)[0]
        # No always-visible role tiles and no separate Link step.
        self.assertNotIn("upload-role-seg-mount", modal)
        self.assertNotIn("prks-upload-role-seg", modal)
        self.assertNotIn("addRoleToUploadList()", modal)
        self.assertNotIn("Search, then Link", modal)
        self.assertIn('id="upload-roles-list"', modal)
        roles_list = re.search(r'<ul[^>]*id="upload-roles-list"[^>]*>', modal).group(0)
        self.assertIn("hidden", roles_list)
        # The shared credit picker is parked (hidden) until a row asks for it.
        parking = modal.split('id="upload-role-credit-parking"', 1)[1][:40]
        self.assertIn("hidden", parking)
        self.assertIn('id="upload-role-credit-wrap"', modal)
        css = _read(_CSS)
        self.assertIn("#work-modal .prks-role-credit-picker[hidden]", css)

    def test_people_rows_keep_role_and_credit_semantics(self):
        ui = _read(_UI)
        chunk = ui.split("function prksUploadRoleLabels()", 1)[1].split(
            "function prksBindUploadPeopleUi", 1
        )[0]
        self.assertIn("prksWorkHasRoleLink", chunk)
        self.assertIn("prksResolveRoleCreditNameForLink('upload-role'", chunk)
        self.assertIn("prksRefreshRoleCreditPicker('upload-role', person)", chunk)
        self.assertIn("prksSetRoleCreditPickerValue('upload-role'", chunk)
        self.assertIn("prksReadRoleCreditName('upload-role')", chunk)
        self.assertIn("PRKS_UPLOAD_ROLE_LABELS", chunk)
        pick = ui.split("initSearchableCombobox('upload-person-search'", 1)[1].split("});", 2)
        self.assertIn("onPersonPick", "".join(pick[:2]))
        self.assertIn("addRoleToUploadList()", "".join(pick[:2]))
        # Only the fields the canonical create reads are sent.
        app = _read(_APP)
        work_chunk = app.split("document.getElementById('save-work-btn')", 1)[1]
        self.assertIn("credit_name: r.credit_name || ''", work_chunk)

    def test_keyboard_and_escape_layers(self):
        ui = _read(_UI)
        self.assertIn("function prksBindWorkModalComboboxKeys", ui)
        for input_id in ("work-folder-search", "upload-person-search", "upload-tag-search",
                         "work-video-playlist-search"):
            self.assertIn("prksBindWorkModalComboboxKeys('%s'" % input_id, ui)
        keys = ui.split("function prksBindWorkModalComboboxKeys", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("aria-activedescendant", keys)
        self.assertIn("result-item--create", keys)
        escape = ui.split("function prksDismissModalInnerEscapeLayer", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("prksIsComboboxPanelOpen", escape)
        form = ui.split("function prksBindWorkModalFormUi", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("e.ctrlKey || e.metaKey", form)
        self.assertIn("work-title", form)

    def test_errors_inside_disclosures_open_them_first(self):
        ui = _read(_UI)
        focus = ui.split("function prksFocusWorkModalControl", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("details.open = true", focus)
        self.assertIn("prksSyncWorkModalDisclosureInert", focus)

    def test_outcome_messages_say_whether_a_work_exists(self):
        app = _read(_APP)
        self.assertIn("Nothing was saved.", app)
        self.assertIn("FOLDER_NOT_FOUND", app)
        self.assertIn("This folder no longer exists", app)
        self.assertIn("The file was created, but", app)
        # A lost response or a server error does not prove nothing was saved.
        self.assertIn("Could not confirm whether the file was saved", app)
        self.assertIn("res.status >= 500", app)
        self.assertNotIn("Could not reach PRKS. Nothing was saved", app)
        ui = _read(_UI)
        quick = ui.split("onQuickCreate: (typedName) => {\n            const search", 1)[1].split("onPersonPick", 1)[0]
        self.assertIn("search.readOnly = true", quick)
        self.assertIn("finally", quick)
        self.assertIn("window.__prksUploadPersonPending = pending", ui)
        # Create waits for an in-flight quick-create before building the payload.
        save = app.split("document.getElementById('save-work-btn').onclick", 1)[1]
        self.assertLess(save.index("__prksUploadPersonPending"), save.index("const payload"))

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
        self.assertIn("prksCreateWorkDurably", work_chunk)
        self.assertIn("{ tags: selectedTags }", work_chunk)
        self.assertIn("batch.create.entity_id", work_chunk)
        self.assertNotIn("attachWorkTagsAfterCreate", work_chunk)
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

    def test_work_modal_open_settles_after_exactly_once(self):
        """The work-modal open path must run its `after` continuation once."""
        ui = _read(_UI)
        chunk = ui.split("const after = () => {", 1)[1].split("} else if (id ===", 1)[0]
        # Two-argument then: one of the two handlers runs, never both.
        self.assertIn("populateUploadComboboxes().then(after, after);", chunk)
        # Negative checks look at code only -- the source comment right above
        # the call names the rejected form in order to explain it.
        code = re.sub(r"//[^\n]*", "", chunk)
        # .then(after).catch(after) would re-run `after` when the fulfilment
        # call itself throws, re-focusing the modal and re-capturing its
        # baseline over the state the first run already established.
        self.assertNotIn(".catch(after)", code)
        # populateUploadComboboxes is async, so there is no non-promise arm to
        # fall back to; a resurrected one would be dead code.
        self.assertNotIn("typeof p.then === 'function'", code)

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
        self.assertRegex(modal, r'id="work-video-url"[^>]*aria-describedby="[^"]*\bwork-video-url-error\b')
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
