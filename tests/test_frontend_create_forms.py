"""Static contract for the Vue create-form slice (#1036)."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
INDEX = ROOT / "frontend" / "index.html"
UI = ROOT / "frontend" / "js" / "ui.js"
APP = ROOT / "frontend" / "js" / "app.js"
FORMS = ROOT / "frontend-app" / "src" / "features" / "create-forms"


class CreateFormVueContractTests(unittest.TestCase):
    DIALOGS = (
        ("work-modal", "New File"),
        ("folder-modal", "Add New Folder"),
        ("playlist-modal", "New playlist"),
        ("saved-view-modal", ":title"),
        ("group-modal", "New people group"),
        ("person-modal", "Add New Person"),
        ("person-template-modal", "Edit Profile Using Template"),
        ("role-modal", "Link Person to Work"),
    )

    def test_modal_host_renders_the_dialog_shells(self):
        """No create-form shell or teleport mount is left in index.html (#1036)."""
        index = INDEX.read_text(encoding="utf-8")
        host = (FORMS / "CreateFormsHost.vue").read_text(encoding="utf-8")
        self.assertNotIn("<Teleport", host)
        for modal_id, title in self.DIALOGS:
            self.assertNotIn(f'id="{modal_id}"', index, modal_id)
            self.assertNotIn(f'id="{modal_id}-vue"', index, modal_id)
            self.assertIn(f'<ModalDialog id="{modal_id}"', host.replace("<ModalDialog\n    id=", "<ModalDialog id="), modal_id)
            self.assertIn(title, host, modal_id)
        self.assertNotIn("-modal-vue", index)
        self.assertNotIn("work-modal-footer-vue", index)
        # Settings keeps its classic shell.
        self.assertIn('id="settings-modal"', index)
        app = (ROOT / "frontend-app" / "src" / "App.vue").read_text(encoding="utf-8")
        self.assertIn("<ModalHost>\n    <CreateFormsHost />\n  </ModalHost>", app)
        modal_host = (ROOT / "frontend-app" / "src" / "modal" / "ModalHost.vue").read_text(encoding="utf-8")
        self.assertIn("<slot />", modal_host)
        for name in ("ownsDialog", "showDialog", "restoreDialog", "hideDialogs", "setDialogInert"):
            self.assertIn(name, modal_host, name)
        self.assertNotIn('id="folder-title"', index)
        self.assertNotIn('id="save-folder-btn"', index)
        self.assertNotIn('id="person-fname"', index)
        self.assertNotIn('id="save-person-btn"', index)
        self.assertNotIn('id="group-name"', index)
        self.assertNotIn('id="save-group-btn"', index)
        self.assertNotIn('id="playlist-title"', index)
        self.assertNotIn('id="save-playlist-btn"', index)
        self.assertNotIn('id="work-title"', index)
        self.assertNotIn('id="save-work-btn"', index)

    def test_vue_owns_the_fields_and_the_durable_writers(self):
        folder = (FORMS / "FolderCreateForm.vue").read_text(encoding="utf-8")
        person = (FORMS / "PersonCreateForm.vue").read_text(encoding="utf-8")
        group = (FORMS / "GroupCreateForm.vue").read_text(encoding="utf-8")
        playlist = (FORMS / "PlaylistCreateForm.vue").read_text(encoding="utf-8")
        submit = (FORMS / "submit.ts").read_text(encoding="utf-8")
        person_save = (FORMS / "person-save.ts").read_text(encoding="utf-8")
        group_save = (FORMS / "group-save.ts").read_text(encoding="utf-8")
        playlist_save = (FORMS / "playlist-save.ts").read_text(encoding="utf-8")
        host = (FORMS / "CreateFormsHost.vue").read_text(encoding="utf-8")
        self.assertIn('id="folder-title"', folder)
        self.assertIn('id="save-folder-btn"', folder)
        self.assertIn("createFolder", folder)
        self.assertIn('id="person-fname"', person)
        self.assertIn('id="save-person-btn"', person)
        self.assertIn('variant="primary"', person)
        self.assertIn(':busy="personBusy"', person)
        self.assertIn('busy-label="Saving…"', person)
        self.assertIn("<PrksButton", person)
        self.assertNotIn("<button", person)
        self.assertIn("createPersonSaveGate", person)
        self.assertIn("prksCreatePersonDurably", submit)
        self.assertIn("prksOpenGeneration", person_save)
        self.assertIn('id="group-name"', group)
        self.assertIn('id="save-group-btn"', group)
        self.assertIn('variant="primary"', group)
        self.assertIn(':busy="groupBusy"', group)
        self.assertIn('busy-label="Creating…"', group)
        self.assertIn("<PrksButton", group)
        self.assertNotIn("<button", group)
        self.assertIn("createGroupSaveGate", group)
        self.assertIn("prksCreatePersonGroupDurably", submit)
        self.assertIn("prksOpenGeneration", group_save)
        self.assertIn('id="playlist-title"', playlist)
        self.assertIn('id="save-playlist-btn"', playlist)
        self.assertIn('variant="primary"', playlist)
        self.assertIn(':busy="playlistBusy"', playlist)
        self.assertIn('busy-label="Creating…"', playlist)
        self.assertIn("<PrksButton", playlist)
        self.assertNotIn("<button", playlist)
        self.assertIn("createPlaylistSaveGate", playlist)
        self.assertIn("createPlaylist", playlist)
        self.assertIn("<button", folder)
        self.assertIn("same-turn", folder)
        self.assertIn("reopen-and-save", folder)
        self.assertIn("pendingAttach()", playlist_save)
        self.assertIn("clearPendingIfSame", playlist_save)
        self.assertIn("prksOpenGeneration", playlist_save)
        self.assertIn("ModalHost renders this inside its own layer", host)
        self.assertNotIn("POST /api/", submit)
        self.assertNotIn("POST /api/", playlist_save)

    def test_classic_modal_lifecycle_calls_the_create_form_hooks(self):
        """#603/#589, #373 and #467: the shell hands each moment to the Vue forms."""
        ui = UI.read_text(encoding="utf-8")
        open_modal = ui[ui.index("function openModal(id, options) {") : ui.index("// —— In-app help hints")]
        # The host shows its dialog as a new opening before anything else
        # reads it, and focus moves into the first field after that.
        self.assertLess(
            open_modal.index("openGeneration = dialogHost.showDialog(id);"),
            open_modal.index("const isCurrentOpening"),
        )
        self.assertLess(
            open_modal.index("openGeneration = dialogHost.showDialog(id);"),
            open_modal.index("window.prksCreateForms.focusInitial(id);"),
        )
        self.assertIn("window.prksCreateForms.owns(id)", open_modal)
        close = ui[ui.index("function closeModals() {") :]
        close = close[: close.index("\n}\n")]
        self.assertLess(
            close.index("prksHideAllModalShells();"),
            close.index("window.prksCreateForms.resetClosed();"),
        )
        inner = ui[ui.index("function prksDismissModalInnerEscapeLayer() {") :]
        inner = inner[: inner.index("\n}\n")]
        self.assertIn("window.prksCreateForms.dismissInnerLayer(modal.id)", inner)
        self.assertLess(
            inner.index("dismissInnerLayer(modal.id)"),
            inner.index("const openPanel"),
        )

    def test_classic_shell_delegates_dirty_and_does_not_bind_the_buttons(self):
        ui = UI.read_text(encoding="utf-8")
        app = APP.read_text(encoding="utf-8")
        self.assertIn("forms.owns(modalId)", ui)
        self.assertIn("forms.isDirty(modalId)", ui)
        self.assertIn("releaseFolderSave", ui)
        self.assertIn("afterPersonReset()", ui)
        self.assertIn("el.value = ''", ui)
        self.assertIn("if (!window.prksCreateForms)", app)
        self.assertIn("Create Link is the Vue role link form's", app)
        fallback = app.split("if (!window.prksCreateForms)", 1)[1].split(
            "Create Link is the Vue role link form's", 1
        )[0]
        self.assertIn("getElementById('save-folder-btn')", fallback)
        self.assertIn("getElementById('save-playlist-btn')", fallback)
        self.assertNotIn("getElementById('save-person-btn')", app)
        self.assertNotIn("getElementById('save-group-btn')", app)
        # New File's Create is the Vue form's: no classic handler is left.
        self.assertNotIn("getElementById('save-work-btn')", app)
        self.assertNotIn("initUploadDragAndDrop", app)
        self.assertIn('id="save-work-btn"', (FORMS / "NewFileForm.vue").read_text(encoding="utf-8"))
        # Create Link is the Vue role link form's; no classic save handler remains.
        self.assertNotIn("getElementById('save-role-btn')", app)
        self.assertNotIn("prepareRoleModal", ui)
        self.assertIn("window.prksCreateForms.prepareRoleLink(", ui)
        self.assertIn('id="save-role-btn"', (FORMS / "RoleLinkForm.vue").read_text(encoding="utf-8"))

    def test_person_template_body_is_vue(self):
        """Edit Profile Using Template is the Vue form's (#1036)."""
        index = INDEX.read_text(encoding="utf-8")
        shell = index
        self.assertNotIn('id="person-template-modal"', index)
        for gone in (
            "applyPersonProfileTemplateFromModal",
            "insertPersonProfileTemplateFromCurrentData",
            "copyPersonTemplateGuideText",
            'id="person-template-json"',
        ):
            self.assertNotIn(gone, shell, gone)
        people = (ROOT / "frontend" / "js" / "components" / "people.js").read_text(encoding="utf-8")
        for gone in (
            "function openPersonProfileTemplateModal",
            "PERSON_TEMPLATE_HELP_TEXT",
            "parsePersonStandardTemplateJson",
            "setPersonTemplateFeedback",
        ):
            self.assertNotIn(gone, people, gone)
        # The sidebar still opens it through the global the form installs.
        self.assertIn('onclick="openPersonProfileTemplateModal()"', people)
        form = (FORMS / "PersonTemplateForm.vue").read_text(encoding="utf-8")
        self.assertIn("runtime().openPersonProfileTemplateModal = open", form)
        self.assertIn('id="person-template-feedback"', form)
        self.assertIn("fillProfileEditor(parsed.values)", form)
        # The editor's Save is the only write: no durable call from the dialog.
        template = (FORMS / "person-template" / "template.ts").read_text(encoding="utf-8")
        for banned in (
            "prksSavePersonFieldsDurably",
            "savePersonProfileDraft",
            "prksSync",
            "fetch(",
            "/api/",
            "prksDurable",
            "XMLHttpRequest",
        ):
            self.assertNotIn(banned, form, banned)
            self.assertNotIn(banned, template, banned)
        host = (FORMS / "CreateFormsHost.vue").read_text(encoding="utf-8")
        self.assertIn('<ModalDialog id="person-template-modal" title="Edit Profile Using Template">', host)
        ui = UI.read_text(encoding="utf-8")
        confirm = ui[ui.index("const PRKS_MODAL_UNSAVED_CONFIRM_IDS") : ui.index("]);", ui.index("const PRKS_MODAL_UNSAVED_CONFIRM_IDS"))]
        self.assertNotIn("person-template-modal", confirm)


if __name__ == "__main__":
    unittest.main()
