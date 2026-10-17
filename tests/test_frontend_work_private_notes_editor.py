"""Work private notes stay on the owning TabContext and the existing note API."""
import unittest
import subprocess
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

    def test_committed_work_reminders_survive_stale_generation_reads_without_new_save(self):
        """Execute the actual shared JS session helpers, not a copied Python model."""
        javascript = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const src = fs.readFileSync(process.argv[1] + '/frontend/js/ui.js', 'utf8');
const from = src.indexOf('function prksPrivateNoteFamily(');
const to = src.indexOf('/** The pane\'s Folder Reminders', from);
assert.ok(from > 0 && to > from, 'runtime session functions still discoverable');
const family = {
    entityType: 'work', idField: 'workId',
    sessionSlot: 'workPrivateNoteSession', holdsSlot: 'workPrivateNoteHolds',
    holdBasesSlot: null, tracksOwnQueued: false, recovery: null,
    pendingText: (_id, text) => text,
};
const api = { PRKS_PRIVATE_NOTE_FAMILIES: { work: family } };
vm.runInNewContext(src.slice(from, to), api, { filename: 'ui.js:private-sessions' });
function old(overrides = {}) {
    return {
        entityType: 'work', entityId: 'a', workId: 'a',
        ownerTabId: 'tab', ownerGeneration: 1, draftText: 'my saved text',
        state: 'committed', dirty: false, retired: false, promise: null,
        saveSequence: 1, latestSaveToken: 1, settledSaveToken: 1,
        ...overrides,
    };
}
function owner(previous, generation = 2) {
    return { tabId: 'tab', generation,
        ui: { workPrivateNoteSession: previous, workPrivateNoteHolds: {} } };
}
const ctx = owner(old());
const remount = api.prksEnsurePrivateNoteSession(family, ctx, 'a', 'stale server text');
assert.equal(remount.draftText, 'my saved text');
assert.equal(remount.dirty, false, 'no second write should be enqueued');
assert.equal(remount.state, 'committed');
assert.equal(remount.settledSaveToken, 1, 'keep local save provenance');
assert.equal(api.prksPrivateNoteText(family, ctx, 'a', 'stale server text'), 'my saved text');
assert.equal(remount.retired, false);
ctx.generation = 3;
const remountAgain = api.prksEnsurePrivateNoteSession(family, ctx, 'a', 'stale server text');
assert.equal(remountAgain.draftText, 'my saved text');
assert.equal(remountAgain.dirty, false);
assert.equal(api.prksPrivateNoteText(family, ctx, 'a', 'my saved text'), 'my saved text');
assert.equal(remountAgain.retired, true, 'matching stored body retires the overlay');
for (const previous of [
    old({saveSequence:0, latestSaveToken:0, settledSaveToken:0}),
    old({state:'committed', retired:true}),
    old({state:'saving', promise:Promise.resolve()}),
    old({latestSaveToken:2}),
    old({entityId:'other', workId:'other'}),
    old({ownerTabId:'other-tab'}),
]) {
    const clean = owner(previous);
    const result = api.prksEnsurePrivateNoteSession(family, clean, 'a', 'server now');
    assert.equal(result.draftText, 'server now', 'only a locally settled Work save may override');
    assert.equal(result.dirty, false);
}
const unsaved = owner(old({state:'drafting', dirty:true, settledSaveToken:0}));
const inherited = api.prksEnsurePrivateNoteSession(family, unsaved, 'a', 'old');
assert.equal(inherited.draftText, 'my saved text');
assert.equal(inherited.dirty, true, 'unsettled drafts still use recovery handoff');
assert.equal(inherited.state, 'drafting');
console.log('Work Reminders generation-handoff checks passed');
"""
        result = subprocess.run(
            ["node", "-e", javascript, str(_PROJECT)], cwd=_PROJECT,
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("generation-handoff checks passed", result.stdout)

    def test_work_save_stays_on_the_owner_session(self):
        families = _UI[_UI.index("const PRKS_PRIVATE_NOTE_FAMILIES"):_UI.index("function prksPrivateNoteFamily(")]
        work = families[families.index("work: Object.freeze({"):families.index("folder: Object.freeze({")]
        self.assertIn("sessionSlot: 'workPrivateNoteSession',", work)
        self.assertIn("save: prksSaveWorkPrivateNoteForSession,", work)
        start = _UI.index("async function prksSaveWorkPrivateNoteForSession(")
        save = _UI[start:_UI.index("\n}\n", start)]
        self.assertIn("prksSaveWorkNoteDurably", save)
        start = _UI.index("function prksEnqueueWorkPrivateNoteSave(")
        wrapper = _UI[start:_UI.index("function prksEnqueuePrivateNotesSave(", start)]
        self.assertIn("PRKS_PRIVATE_NOTE_FAMILIES.work", wrapper)
        start = _UI.index("function prksEnqueuePrivateNoteSave(family, editor)")
        body = _UI[start:_UI.index("function prksEnqueueWorkPrivateNoteSave(", start)]
        self.assertIn("editor.ctx.ui[family.sessionSlot]", body)
        self.assertIn("scope_busy", body)
        self.assertIn("prksSchedulePrivateNoteBusyRetry", body)
        self.assertIn("prksReconcileSavedPrivateNote", body)
        self.assertIn("editor.dirty = true", body)
        for text in (save, body):
            self.assertNotIn("prksRequest(", text)
            self.assertNotIn("prksOfflineMarkEntityChanged", text)
        reset = _CONTEXT.split("function resetEditUi(ui)", 1)[1].split("function safeCall", 1)[0]
        self.assertNotIn("workPrivateNoteSession = null", reset)
        self.assertIn("workPrivateNoteSession: null", _CONTEXT)
        panel = _UI[_UI.index("function updatePanelContent(tabId) {"):_UI.index("\nfunction ", _UI.index("function updatePanelContent(tabId) {") + 10)]
        self.assertLess(panel.index("prksFlushPendingPrivateNotes(previousOwner)"), panel.index("panel.innerHTML = prksWorkRightPanelStackHtml"))
        self.assertLess(panel.index("initPrksPrivateNotesEditor('work', _cw.id, focusedCtx)"), panel.index("prksPublishWorkPanelRead(focusedCtx, _cw)"))


if __name__ == "__main__":
    unittest.main()
