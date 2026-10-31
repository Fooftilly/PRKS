"""Static contract for the Vue create-form slice (#1036)."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
INDEX = ROOT / "frontend" / "index.html"
UI = ROOT / "frontend" / "js" / "ui.js"
APP = ROOT / "frontend" / "js" / "app.js"
FORMS = ROOT / "frontend-app" / "src" / "features" / "create-forms"


class CreateFormVueContractTests(unittest.TestCase):
    def test_shells_stay_and_bodies_leave_index_html(self):
        index = INDEX.read_text(encoding="utf-8")
        for modal_id, mount_id in (
            ("folder-modal", "folder-modal-vue"),
            ("person-modal", "person-modal-vue"),
            ("group-modal", "group-modal-vue"),
            ("playlist-modal", "playlist-modal-vue"),
        ):
            self.assertIn(f'id="{modal_id}"', index)
            self.assertIn(f'id="{mount_id}"', index)
        self.assertNotIn('id="folder-title"', index)
        self.assertNotIn('id="save-folder-btn"', index)
        self.assertNotIn('id="person-fname"', index)
        self.assertNotIn('id="save-person-btn"', index)
        self.assertNotIn('id="group-name"', index)
        self.assertNotIn('id="save-group-btn"', index)
        self.assertNotIn('id="playlist-title"', index)
        self.assertNotIn('id="save-playlist-btn"', index)
        self.assertIn('id="work-modal"', index)
        self.assertIn('id="save-work-btn"', index)
        self.assertIn('id="saved-view-modal"', index)

    def test_vue_owns_the_fields_and_the_durable_writers(self):
        folder = (FORMS / "FolderCreateForm.vue").read_text(encoding="utf-8")
        person = (FORMS / "PersonCreateForm.vue").read_text(encoding="utf-8")
        group = (FORMS / "GroupCreateForm.vue").read_text(encoding="utf-8")
        playlist = (FORMS / "PlaylistCreateForm.vue").read_text(encoding="utf-8")
        submit = (FORMS / "submit.ts").read_text(encoding="utf-8")
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
        self.assertIn("prksCreatePersonDurably", submit)
        self.assertIn('id="group-name"', group)
        self.assertIn('id="save-group-btn"', group)
        self.assertIn('variant="primary"', group)
        self.assertIn(':busy="groupBusy"', group)
        self.assertIn('busy-label="Creating…"', group)
        self.assertIn("<PrksButton", group)
        self.assertNotIn("<button", group)
        self.assertIn("prksCreatePersonGroupDurably", submit)
        self.assertIn('id="playlist-title"', playlist)
        self.assertIn('id="save-playlist-btn"', playlist)
        self.assertIn('variant="primary"', playlist)
        self.assertIn(':busy="playlistBusy"', playlist)
        self.assertIn('busy-label="Creating…"', playlist)
        self.assertIn("<PrksButton", playlist)
        self.assertNotIn("<button", playlist)
        self.assertIn("<button", folder)
        self.assertIn("same-turn", folder)
        self.assertIn("reopen-and-save", folder)
        self.assertIn("createPlaylist", submit)
        self.assertIn("Retire the teleport", host)
        self.assertNotIn("POST /api/", submit)

    def test_classic_shell_delegates_dirty_and_does_not_bind_the_buttons(self):
        ui = UI.read_text(encoding="utf-8")
        app = APP.read_text(encoding="utf-8")
        self.assertIn("forms.owns(modalId)", ui)
        self.assertIn("forms.isDirty(modalId)", ui)
        self.assertIn("releaseFolderSave", ui)
        self.assertIn("afterPersonReset()", ui)
        self.assertIn("el.value = ''", ui)
        self.assertNotIn("getElementById('save-folder-btn')", app)
        self.assertNotIn("getElementById('save-person-btn')", app)
        self.assertNotIn("getElementById('save-group-btn')", app)
        self.assertNotIn("getElementById('save-playlist-btn')", app)
        self.assertIn("id=\"save-work-btn\"", (ROOT / "frontend" / "index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
