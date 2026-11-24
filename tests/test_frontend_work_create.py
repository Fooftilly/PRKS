"""Structural + Node regressions for New File creation UX."""
from __future__ import annotations

import json
import os
import re
import subprocess
import unittest

from tests.narrowing import must_search

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_INDEX = os.path.join(_FRONTEND, "index.html")
_CREATE_FORMS = os.path.join(_PROJECT_DIR, "frontend-app", "src", "features", "create-forms")
_NEW_FILE_FORM = os.path.join(_CREATE_FORMS, "NewFileForm.vue")
_NEW_FILE_DIR = os.path.join(_CREATE_FORMS, "new-file")
_MODAL_DIALOG = os.path.join(_PROJECT_DIR, "frontend-app", "src", "modal", "ModalDialog.vue")
_CREATE_HOST = os.path.join(_CREATE_FORMS, "CreateFormsHost.vue")
_FOOTER_TELEPORT = '<Teleport defer to="#work-modal-footer">'
_WORK_DIALOG = """<ModalDialog
    id="work-modal"
    title="New File"
    dialog-class="modal-wide modal--create-file"
    body-class="modal-body--scroll"
    with-footer
  >
    <NewFileForm />
  </ModalDialog>"""

def _nf(name: str) -> str:
    """A New File module under create-forms/new-file."""
    return _read(os.path.join(_NEW_FILE_DIR, name))


def _template(source: str) -> str:
    return source.split("<template>", 1)[1].rsplit("</template>", 1)[0]


def _work_modal():
    """The New File dialog as the page renders it.

    ModalHost renders it as a ModalDialog (#1036): the form is the dialog
    body and portals its footer into the dialog's ``#work-modal-footer``.
    Splice the section components into the form and the form into the
    dialog template, so order checks (essentials before disclosures, body
    before footer) still hold.
    """
    host = _read(_CREATE_HOST)
    if _WORK_DIALOG not in host:
        raise AssertionError("New File is no longer a ModalDialog with a scrolling body and footer")
    dialog = _template(_read(_MODAL_DIALOG))
    form = _template(_read(_NEW_FILE_FORM))
    for name in ("NewFileSource", "NewFileFolder", "NewFilePeople", "NewFilePlaylist", "NewFileTags"):
        tag = must_search(r"<%s\b[^>]*/>" % name, form).group(0)
        form = form.replace(tag, _template(_nf(name + ".vue")))
    body, footer = form.split(_FOOTER_TELEPORT, 1)
    footer = footer.rsplit("</Teleport>", 1)[0]
    if "<slot />" not in dialog or 'class="modal-footer modal-footer--sticky prks-form-actions"\n    ></div>' not in dialog:
        raise AssertionError("ModalDialog lost its body slot or footer")
    dialog = dialog.replace("<slot />", body)
    return dialog.replace(
        'class="modal-footer modal-footer--sticky prks-form-actions"\n    ></div>',
        'class="modal-footer modal-footer--sticky prks-form-actions"\n    >' + footer + "</div>",
    )

_UI = os.path.join(_FRONTEND, "js", "ui.js")
_APP = os.path.join(_FRONTEND, "js", "app.js")
_CSS = os.path.join(_FRONTEND, "css", "style.css")
_DESIGN = os.path.join(_PROJECT_DIR, "DESIGN.md")
_AGENTS = os.path.join(_PROJECT_DIR, "AGENTS.md")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _extract_show_inline_combobox_results() -> str:
    ui = _read(_UI)
    start = "function prksIsInlineComboboxPanel(results) {"
    chunk = ui.split(start, 1)[1]
    # Through the closing brace of prksShowInlineComboboxResults (next top-level fn).
    body, _rest = chunk.split("\nfunction initSearchableCombobox", 1)
    return start + body


def _run_show_inline_combobox_case(js_body: str) -> None:
    """Invoke prksShowInlineComboboxResults in a minimal DOM fixture (Node vm)."""
    source = _extract_show_inline_combobox_results()
    script = r"""
const vm = require('vm');
const source = %s;
function makeClassList(initial) {
    const set = new Set(initial || []);
    return {
        contains(name) { return set.has(name); },
        add(name) { set.add(name); },
        remove(name) { set.delete(name); },
        _values() { return Array.from(set); },
    };
}
function makeResults(classes) {
    return {
        classList: makeClassList(classes),
        __prksCollapseTimer: null,
        get offsetHeight() { return 1; },
    };
}
const context = {
    clearTimeout() {},
    setTimeout() { return 0; },
    window: {
        setTimeout() { return 0; },
        clearTimeout() {},
    },
    makeResults,
};
vm.createContext(context);
vm.runInContext(
    source + '; this.prksShowInlineComboboxResults = prksShowInlineComboboxResults;',
    context,
);
(() => {
%s
})();
""" % (
        json.dumps(source),
        js_body,
    )
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)


class FrontendWorkCreateTests(unittest.TestCase):
    def test_single_ribbon_create_affordance(self):
        ribbon = _read(os.path.join(_PROJECT_DIR, "frontend-app", "src", "app-shell", "TopRibbon.vue"))
        center = ribbon.split("top-ribbon__center", 1)[1].split("top-ribbon__right", 1)[0]
        self.assertIn('id="prks-ribbon-create"', center)
        self.assertIn('id="prks-ribbon-new-file"', center)
        self.assertIn('id="prks-ribbon-new-more"', center)
        self.assertIn("openModal?.('work-modal')", ribbon)
        self.assertNotIn("New…", center)
        self.assertIn('aria-haspopup="menu"', center)
        self.assertEqual(center.count('id="prks-ribbon-new-file"'), 1)
        self.assertEqual(center.count('id="prks-ribbon-new-more"'), 1)
        self.assertNotIn("ribbon-create.js", _read(_INDEX))

    def test_modal_primary_and_advanced_hierarchy(self):
        modal = _work_modal()
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
        modal = _work_modal()
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
        folder = must_search(r'<input[^>]*id="work-folder-search"[^>]*>', modal).group(0)
        self.assertIn('aria-required="true"', folder)
        url = must_search(r'<input[^>]*id="work-video-url"[^>]*>', modal).group(0)
        self.assertIn('aria-required="true"', url)

    def test_people_have_no_role_surface_before_a_person(self):
        modal = _work_modal()
        # No always-visible role tiles and no separate Link step.
        self.assertNotIn("upload-role-seg-mount", modal)
        self.assertNotIn("prks-upload-role-seg", modal)
        self.assertNotIn("addRoleToUploadList()", modal)
        self.assertNotIn("Search, then Link", modal)
        self.assertIn('id="upload-roles-list"', modal)
        roles_list = must_search(r'<ul[^>]*id="upload-roles-list"[^>]*>', modal).group(0)
        self.assertIn("hidden", roles_list)
        # The shared credit picker is parked (hidden) until a row asks for it.
        parking = modal.split('id="upload-role-credit-parking"', 1)[1][:40]
        self.assertIn("hidden", parking)
        self.assertIn('id="upload-role-credit-wrap"', modal)
        css = _read(_CSS)
        self.assertIn("#work-modal .prks-role-credit-picker[hidden]", css)

    def test_people_rows_keep_role_and_credit_semantics(self):
        people = _nf("NewFilePeople.vue")
        self.assertIn("prksWorkHasRoleLink", people)
        self.assertIn("w.prksResolveRoleCreditNameForLink(PREFIX, personId, 'upload-person-search')", people)
        self.assertIn("refreshPicker(person)", people)
        self.assertIn("w.prksSetRoleCreditPickerValue?.(PREFIX", people)
        self.assertIn("runtime().prksReadRoleCreditName", people)
        self.assertIn("const PREFIX = 'upload-role'", people)
        self.assertIn("import { PEOPLE_ROLES } from '../../../domain/people-roles'", people)
        self.assertNotIn("PRKS_UPLOAD_ROLE_LABELS", people)
        # Choosing a person IS adding them: no separate Link step.
        pick = people.split("function pick(row: PersonOption)", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("addPicked()", pick)
        # The one credit picker moves under the row being edited.
        self.assertIn('<Teleport defer :to="creditTarget"', people)
        # Only the fields the canonical create reads are sent.
        save = _nf("save.ts")
        self.assertIn("credit_name: role.credit_name || ''", save)
        # YouTube Works store their accessed date (urldate) at creation.
        self.assertIn("urldate: deps.today(),", save)
        self.assertIn("today: () => new Date().toISOString().slice(0, 10)", _read(_NEW_FILE_FORM))

    def test_keyboard_and_escape_layers(self):
        combobox = _nf("combobox.ts")
        self.assertIn("bindComboboxKeys(inputId, resultsId", combobox)
        for name, input_id in (("NewFileFolder.vue", "work-folder-search"),
                               ("NewFilePeople.vue", "upload-person-search"),
                               ("NewFileTags.vue", "upload-tag-search"),
                               ("NewFilePlaylist.vue", "work-video-playlist-search")):
            self.assertIn("useInlineCombobox('%s'" % input_id, _nf(name), name)
        self.assertIn("enterCreates: true", _nf("NewFileTags.vue"))
        self.assertIn("enterCreates: true", _nf("NewFilePlaylist.vue"))
        # People too (maintainer decision on #1289): combobox-keys.js picks
        # the first real match before a create row, so Enter quick-creates
        # only when nothing matches.
        self.assertIn("enterCreates: true", _nf("NewFilePeople.vue"))
        keys = _read(os.path.join(_FRONTEND, "js", "combobox-keys.js"))
        self.assertIn("aria-activedescendant", keys)
        self.assertIn("result-item--create", keys)
        # Typing marks the open rows stale until the list re-renders, so a
        # quick Enter cannot pick a row from the previous query.
        self.assertIn("stale = true", keys)
        self.assertIn("stale = false", keys)
        self.assertIn("if (stale)", keys)
        self.assertIn("Escape", keys)
        self.assertIn("markComboboxRendered(inputId, nextTick())", combobox)
        self.assertIn("if (seq !== renderSeq) return", _nf("NewFileTags.vue"))
        # An open list is Escape's inner layer, before the dialog.
        modal_host = _read(os.path.join(_PROJECT_DIR, "frontend-app", "src", "modal", "ModalHost.vue"))
        self.assertIn("dismissDialogInnerLayer(openDialog)", modal_host)
        form = _read(_NEW_FILE_FORM)
        self.assertIn("dismissInnerLayer: closeOpenList", form)
        self.assertIn("createSubmitKeyHandler(['work-title']", form)
        keyboard = _read(os.path.join(_CREATE_FORMS, "keyboard.ts"))
        self.assertIn("event.ctrlKey || event.metaKey", keyboard)

    def test_source_kind_switch_is_a_radio_group(self):
        """It switches fields across the form, not two tab panels."""
        source = _nf("NewFileSource.vue")
        self.assertIn('role="radiogroup" aria-labelledby="work-file-kind-label"', source)
        self.assertEqual(source.count('role="radio"'), 2)
        self.assertNotIn('role="tab', source)
        self.assertNotIn("aria-selected", source)
        for kind in ("pdf", "video"):
            self.assertIn(":aria-checked=\"state.kind.value === '%s'" % kind, source)
            self.assertIn(":tabindex=\"state.kind.value === '%s' ? 0 : -1\"" % kind, source)
        for key in ("ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown", "'Home'", "'End'"):
            self.assertIn(key, source)

    def test_inline_combobox_is_open_is_synchronous(self):
        """Keyboard readiness must not wait a frame after rows exist (#WorkCreate E2E)."""
        ui = _read(_UI)
        show = ui.split("function prksShowInlineComboboxResults", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("results.classList.add('is-open')", show)
        # The open class is applied in this turn after a forced reflow — not
        # deferred to requestAnimationFrame / microtask, which raced Enter/ArrowDown.
        self.assertNotIn("requestAnimationFrame", show)
        self.assertNotIn("Promise.resolve", show)
        self.assertIn("void results.offsetHeight", show)
        # Runtime: is-open must be present immediately when the helper returns
        # (source-text checks alone would miss a Promise.then deferral).
        _run_show_inline_combobox_case(
            r"""
            const results = makeResults(['hidden', 'combobox-results--tag-panel']);
            context.prksShowInlineComboboxResults(null, results);
            if (!results.classList.contains('is-open')) {
                throw new Error(
                    'expected is-open synchronously after show; classes=' +
                    results.classList._values().join(',')
                );
            }
            if (results.classList.contains('hidden')) {
                throw new Error('expected hidden cleared synchronously');
            }
            """
        )

    def test_stale_blur_does_not_close_a_refocused_list(self):
        ui = _read(_UI)
        blur = ui.split("// Hide results when focus moves away", 1)[1].split("function renderResults", 1)[0]
        self.assertIn("document.activeElement === input", blur)

    def test_errors_inside_disclosures_open_them_first(self):
        form = _read(_NEW_FILE_FORM)
        focus = form.split("function focusControl(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("details.open = true", focus)
        self.assertIn("syncDisclosureInert()", focus)
        # Pointing at a field ends the submit: a frozen form refuses focus.
        self.assertIn("setBusy(false)", focus)

    def test_outcome_messages_say_whether_a_work_exists(self):
        save = _nf("save.ts")
        self.assertIn("Nothing was saved.", save)
        self.assertIn("FOLDER_NOT_FOUND", save)
        self.assertIn("FOLDER_GONE", save)
        self.assertIn("This folder no longer exists", _nf("folder.ts"))
        self.assertIn("The file was created, but", save)
        # A lost response or a server error does not prove nothing was saved.
        self.assertIn("Could not confirm whether the file was saved", save)
        self.assertIn("error.status >= 500 ? SERVER_FAILED", save)
        # The create goes through the typed src/api service, never raw transport.
        self.assertNotIn("prksRequest", save)
        self.assertNotIn("prksRequest", _read(_NEW_FILE_FORM))
        self.assertIn("import { attachTagToWork, createPdfWork } from '../../api/works-create'", _read(_NEW_FILE_FORM))
        self.assertNotIn("Could not reach PRKS. Nothing was saved", save)
        people = _nf("NewFilePeople.vue")
        quick = people.split("function quickCreate(): void {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("state.personLocked.value = true", quick)
        self.assertIn("finally", quick)
        self.assertIn("state.setPersonPending(opening, done)", quick)
        self.assertIn("{ stillApplies }", quick)
        self.assertIn("addPicked() === true", quick)
        # Scoped to one opening: a new opening drops the pending create.
        state = _nf("state.ts")
        reset = state.split("function reset(): void {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("personPending = null", reset)
        self.assertIn("quickCreates = []", reset)
        self.assertIn("personLocked.value = false", reset)
        self.assertIn("personPending.opening === opening", state)
        # Tag and playlist quick-creates are awaited by Create, per opening.
        self.assertIn("state.trackQuickCreate(", _nf("NewFileTags.vue"))
        self.assertIn("state.trackQuickCreate(", _nf("NewFilePlaylist.vue"))
        # Create waits for them before it reads the form, and a failed
        # person quick-create stops Create rather than dropping the person.
        self.assertLess(save.index("deps.pendingPerson()"), save.index("deps.readDraft()"))
        self.assertLess(save.index("deps.pendingQuickCreates()"), save.index("deps.readDraft()"))
        self.assertIn("(await pendingPerson) === true", save)
        # The in-flight mark names its opening.
        self.assertIn("if (deps.inFlight() === opening) return", save)
        self.assertIn("for (const tag of tagsToAttach)", save)
        # Every wait re-checks the opening before it touches the form.
        self.assertGreaterEqual(save.count("if (!stillOpening()) return"), 8)
        source = _nf("NewFileSource.vue")
        preview = source.split("async function previewVideo(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("state.isOpening(opening) && state.isPreviewSeq(seq)", preview)
        self.assertEqual(preview.count("if (!still()) return"), 3)

    def test_selected_source_markup(self):
        modal = _work_modal()
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
        save = _nf("save.ts")
        form = _read(_NEW_FILE_FORM)
        self.assertIn("setInFlight(opening)", save)
        self.assertIn("deps.setBusy(true)", save)
        self.assertIn("'Choose a PDF file.'", save)
        self.assertIn("'Enter a valid YouTube URL.'", save)
        self.assertIn("draft.folderCommitted", save)
        self.assertIn("FOLDER_NOT_PICKED", save)
        self.assertIn("Choose a folder from the list", _nf("folder.ts"))
        self.assertIn("Could not read this PDF", save)
        self.assertIn("deps.closeAndOpen(", save)
        self.assertIn("w.prksNavigate(`#/works/${encodeURIComponent(workId)}`)", form)
        self.assertIn("w.prksCreateWorkDurably(fields, { tags: selectedTags })", form)
        self.assertIn("batch.create.entity_id", save)
        self.assertNotIn("attachWorkTagsAfterCreate", save)
        self.assertNotIn("location.reload()", save + form)
        self.assertIn("'Creating…'", form)
        folder = _nf("folder.ts")
        self.assertIn("export function canonicalUncategorizedFolder", folder)
        self.assertIn("export function defaultFolderId", folder)
        self.assertIn("export function folderChoiceFor", folder)
        self.assertIn("function isCommitted()", _nf("NewFileFolder.vue"))
        self.assertNotIn("Library root", folder + _nf("NewFileFolder.vue"))
        ui = _read(_UI)
        self.assertIn("prksIsValidYoutubeUrl", ui)
        self.assertIn("prksIsRecognizedYoutubeHost", ui)
        self.assertIn("prksExtractYoutubeVideoId", _nf("NewFileSource.vue"))
        self.assertIn("syncDisclosureInert", form)
        # A kind switch drops the other source's entry.
        source = _nf("NewFileSource.vue")
        self.assertIn("if (switched && next !== prev)", source)
        # The classic New File module is gone.
        app = _read(_APP)
        for retired in ("uploadRoles", "uploadTagsSelected", "__prksPendingUploadPdfFile",
                        "__prksUploadVideoMeta", "__prksWorkCreateInFlight", "resetUploadModal",
                        "populateUploadComboboxes", "prksHandleVideoUrlInput", "initUploadDragAndDrop",
                        "prksSetWorkModalCreateBusy", "__prksUploadPersonPending"):
            self.assertNotIn(retired, ui, retired)
            self.assertNotIn(retired, app, retired)

    def test_work_modal_open_settles_after_exactly_once(self):
        """A dialog opening runs its settle step once (#1036)."""
        dialogs = _read(os.path.join(_PROJECT_DIR, "frontend-app", "src", "modal", "dialogs.ts"))
        chunk = dialogs.split("export function beginOpening(id: string): void {", 1)[1].split("\n}\n", 1)[0]
        # Two-argument then: one of the two handlers runs, never both.
        self.assertIn("pending.then(settle, settle)", chunk)
        # Negative checks look at code only -- the source comment right above
        # the call names the rejected form in order to explain it.
        code = re.sub(r"//[^\n]*", "", chunk)
        # .then(settle).catch(settle) would re-run settle when the fulfilment
        # call itself throws, re-focusing the dialog and re-capturing its
        # baseline over the state the first run already established.
        self.assertNotIn(".catch(settle)", code)
        # New File hands its prepare the opening's own currency check.
        self.assertIn("opened: (opening) => prepare(opening.isCurrent)", _read(_NEW_FILE_FORM))
        self.assertNotIn("prepareNewFile", _read(_UI))
        # The Vue form drops a load that finished after a newer opening.
        form = _read(_NEW_FILE_FORM)
        prepare = form.split("async function prepare(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("if (!stillOpening()) return", prepare)

    def test_folder_combobox_commit_semantics(self):
        folder = _nf("NewFileFolder.vue")
        # Free text must not be treated as a selection: typing clears both the
        # picked folder ID and the explicit default-commit flag.
        on_input = folder.split("function onInput(): void {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("setFieldValue('work-folder-id', '')", on_input)
        self.assertIn("state.folderDefault.value = false", on_input)
        # The synthetic default row is suppressed when a real Uncategorized
        # folder already exists in loaded data (no duplicate-looking rows).
        self.assertIn("if (!canonicalUncategorizedFolder(all))", folder)
        committed = folder.split("function isCommitted(): boolean {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("fieldValue('work-folder-id').trim()", committed)
        self.assertIn("state.folderDefault.value", committed)
        # The shared person/work combobox no longer knows about folders.
        ui = _read(_UI)
        combobox = ui.split("function initSearchableCombobox(", 1)[1].split("\n}\n", 1)[0]
        self.assertNotIn("folder", combobox)

    def test_no_meaningless_for_attribute_on_error_paragraphs(self):
        modal = _work_modal()
        for error_id in ("work-file-error", "work-video-url-error", "work-folder-error",
                         "work-date-error", "work-video-published-date-error"):
            paragraph = must_search(r'<p[^>]*id="%s"[^>]*>' % error_id, modal).group(0)
            self.assertNotIn(" for=", paragraph, error_id)
            self.assertIn('role="alert"', paragraph, error_id)
        # Each control names its error and says when it is invalid.
        for control_id, error_id, field in (("upload-drop-zone", "work-file-error", "file"),
                                            ("work-video-url", "work-video-url-error", "videoUrl"),
                                            ("work-folder-search", "work-folder-error", "folder"),
                                            ("work-date", "work-date-error", "date"),
                                            ("work-video-published-date",
                                             "work-video-published-date-error", "videoDate")):
            tag = must_search(r'<(?:input|div)[^>]*id="%s"[^>]*>' % control_id, modal).group(0)
            self.assertIn(error_id, tag, control_id)
            # Its own error key, so one field's error never marks another invalid.
            self.assertIn(':aria-invalid="state.errors.%s ?' % field, tag, control_id)

    def test_video_source_is_youtube_only_contract(self):
        modal = _work_modal()
        self.assertIn(">YouTube<", modal)
        self.assertIn("YouTube URL", modal)
        self.assertRegex(modal, r'id="work-video-url"[^>]*aria-describedby="[^"]*\bwork-video-url-error\b')
        self.assertIn('aria-describedby="work-folder-error"', modal)
        self.assertIn('aria-describedby="work-date-error"', modal)

    def test_backend_video_url_is_authoritative(self):
        works = _read(os.path.join(_PROJECT_DIR, "backend", "api", "works.py"))
        self.assertIn("validate_youtube_url", works)
        self.assertIn("Invalid YouTube URL", works)
        # Hostname matching must be an explicit set, not substring matching.
        self.assertIn("is_youtube_host", works)
        self.assertNotIn("'youtube.com' in host", works)
        rules = _read(os.path.join(_PROJECT_DIR, "backend", "work_source_sync.py"))
        self.assertIn("YOUTUBE_HOSTS = frozenset(", rules)

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
        self.assertIn("app-shell", agents)

    def test_ribbon_create_lives_in_vue_shell(self):
        chrome = _read(os.path.join(_PROJECT_DIR, "frontend-app", "src", "app-shell", "chrome.ts"))
        self.assertIn("id: 'new-file'", chrome)
        self.assertIn("modalId: 'work-modal'", chrome)
        self.assertIn("id: 'new-folder'", chrome)
        self.assertIn("id: 'new-person'", chrome)
        self.assertIn("id: 'new-group'", chrome)
        self.assertFalse(os.path.isfile(os.path.join(_FRONTEND, "js", "ribbon-create.js")))


if __name__ == "__main__":
    unittest.main()
