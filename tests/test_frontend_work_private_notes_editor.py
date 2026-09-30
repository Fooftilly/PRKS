"""Work private notes stay on the owning TabContext and the existing note API."""
import unittest
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[1]
_UI = (_PROJECT / "frontend" / "js" / "ui.js").read_text(encoding="utf-8")
_CONTEXT = (_PROJECT / "frontend" / "js" / "tab-context.js").read_text(encoding="utf-8")
_MAIN = (_PROJECT / "frontend-app" / "src" / "main.ts").read_text(encoding="utf-8")
_SESSION = (_PROJECT / "frontend-app" / "src" / "features" / "work" / "private-note-session.ts").read_text(encoding="utf-8")
_CARD = (_PROJECT / "frontend-app" / "src" / "features" / "work" / "WorkPrivateNotes.vue").read_text(encoding="utf-8")


class WorkPrivateNotesEditorContractTests(unittest.TestCase):
    def test_vue_card_does_not_own_a_second_queue(self):
        self.assertIn("registerWorkPrivateNotesBridge", _MAIN)
        self.assertIn("features/work/private-note-session", _MAIN)
        for token in ("listOperations", "prksRequest(", "fetch(", "createPinia", "vue-router", "@tanstack"):
            self.assertNotIn(token, _SESSION, token)
            self.assertNotIn(token, _CARD, token)
        self.assertNotIn("v-model", _CARD)
        self.assertIn('data-prks-role="work-private-notes-anchor"', _UI)
        self.assertIn("prksVuePresentWorkPrivateNotes", _UI)
        self.assertIn("prksVueDismissWorkPrivateNotes", _UI)
        self.assertIn("mounted.generation === generation", _SESSION)
        self.assertIn("if (anchor) render(null, anchor)", _SESSION)
        self.assertNotIn("anchor.isConnected", _SESSION)
        self.assertIn("function prksPrivateNotesRetryTarget(", _UI)
        self.assertIn("String(live.generation) !== String(editor.generation)", _UI)
        publish = _UI[_UI.index("function prksPublishWorkPanelRead("):_UI.index("function prksRefreshOwnedWorkPanelRead(")]
        early = publish.split("const panel = document.getElementById", 1)[0]
        self.assertIn("prksDismissWorkPanelReadSurface()", early)
        self.assertNotIn("prksVueDismissWorkPrivateNotes", early)
        self.assertNotIn("prksDismissWorkPanelRead()", early)
        self.assertIn("workPrivateNoteHolds", _UI)

    def test_work_save_stays_on_the_owner_session(self):
        start = _UI.index("function prksEnqueueWorkPrivateNoteSave(")
        body = _UI[start:_UI.index("function prksEnqueuePrivateNotesSave(", start)]
        self.assertIn("prksSaveWorkNoteDurably", body)
        self.assertIn("workPrivateNoteSession", body)
        self.assertIn("scope_busy", body)
        self.assertIn("prksSchedulePrivateNoteBusyRetry", body)
        self.assertIn("prksReconcileSavedWorkPrivateNote", body)
        self.assertIn("editor.dirty = true", body)
        self.assertNotIn("prksRequest(", body)
        self.assertNotIn("prksOfflineMarkEntityChanged", body)
        reset = _CONTEXT.split("function resetEditUi(ui)", 1)[1].split("function safeCall", 1)[0]
        self.assertNotIn("workPrivateNoteSession = null", reset)
        self.assertIn("workPrivateNoteSession: null", _CONTEXT)
        panel = _UI[_UI.index("function updatePanelContent(tabId) {"):_UI.index("\nfunction ", _UI.index("function updatePanelContent(tabId) {") + 10)]
        self.assertLess(panel.index("prksFlushPendingPrivateNotes(previousOwner)"), panel.index("panel.innerHTML = prksWorkRightPanelStackHtml"))
        self.assertLess(panel.index("initPrksPrivateNotesEditor('work', _cw.id, focusedCtx)"), panel.index("prksPublishWorkPanelRead(focusedCtx, _cw)"))


if __name__ == "__main__":
    unittest.main()
