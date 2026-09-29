"""Work metadata editor session: baseline, token, and owner checks."""
import unittest
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[1]
_UI = (_PROJECT / "frontend" / "js" / "ui.js").read_text(encoding="utf-8")
_APP = (_PROJECT / "frontend" / "js" / "app.js").read_text(encoding="utf-8")
_META = (_PROJECT / "frontend" / "js" / "work-metadata-editor.js").read_text(encoding="utf-8")
_SOURCE = (_PROJECT / "frontend" / "js" / "work-source-editor.js").read_text(encoding="utf-8")
_DRAFT = (_PROJECT / "frontend-app" / "src" / "features" / "work" / "metadata-draft.ts").read_text(encoding="utf-8")
_SESSION = (_PROJECT / "frontend-app" / "src" / "features" / "work" / "metadata-session.ts").read_text(encoding="utf-8")


class WorkMetadataEditorContractTests(unittest.TestCase):
    def test_leave_guard_measures_the_session_baseline(self):
        dirty = _UI[_UI.index("function prksWorkMetaDraftIsDirty"):_UI.index("function prksBeginWorkMetaSession")]
        self.assertIn("workMetaBaseline", dirty)
        self.assertNotIn("prksWorkMetaDraftFromWork(work)", dirty)
        self.assertIn("function prksRetainWorkMetaEditAcrossRefresh", _UI)
        self.assertIn("prksRetainWorkMetaEditAcrossRefresh(ctx, previousWorkMetaEditing, previousWorkMetaSaved)", _APP)
        self.assertIn("workMetaEditSession", _UI)

    def test_save_checks_the_session_before_the_durable_write(self):
        save = _META[_META.index("async function save(workId, groupName)"):_META.index("function resolveFieldConflict")]
        write = save.index("store.saveWorkMetadataFields")
        self.assertLess(save.index("if (!sessionOwned()) return;"), write)
        self.assertIn("prksObservedWorkFields", save)
        self.assertNotIn("prksEffectiveWorkSync", save)
        self.assertIn("prksCommitWorkMetaBaseline", save)
        self.assertIn("scope_busy", save)
        changed = save.index("prksSync.changed()", write)
        self.assertLess(changed, save.index("if (!sessionOwned()) return;", changed))
        source = _SOURCE[_SOURCE.index("async function save(workId)"):_SOURCE.index("root.prksResolveWorkSourceConflict")]
        self.assertLess(source.index("if (!still()) return;"), source.index("store.saveWorkSource"))
        self.assertIn("workMetaDraft.source_url", source)
        source_write = source.index("store.saveWorkSource")
        source_changed = source.index("prksSync.changed()", source_write)
        self.assertLess(source_changed, source.index("if (!still()) return;", source_changed))

    def test_vue_session_does_not_read_the_queue(self):
        for source in (_DRAFT, _SESSION):
            self.assertNotIn("listOperations", source)
            self.assertNotIn("saveWorkMetadataFields", source)
        self.assertIn("workMetaSessionStill", _DRAFT)
        self.assertIn("registerWorkMetadataEditorBridge", _SESSION)
        self.assertIn("data-prks-role=\"work-metadata-editor-anchor\"", _UI)

    def test_route_teardown_keeps_a_monotonic_edit_session(self):
        context = (_PROJECT / "frontend" / "js" / "tab-context.js").read_text(encoding="utf-8")
        reset = context.split("function resetEditUi(ui)", 1)[1].split("function safeCall", 1)[0]
        self.assertNotIn("workMetaEditSession = 0", reset)
        self.assertIn("prksWorkMetaRetainWorkId(ctx)", _APP)
        retain = _UI[_UI.index("function prksWorkMetaRetainWorkId"):_UI.index("function prksRetainWorkMetaEditAcrossRefresh")]
        self.assertIn("workMetaDraftWorkId", retain)

    def test_vue_owned_errors_are_not_cleared_in_the_dom(self):
        clear = _META[_META.index("function clearFieldErrors"):_META.index("const PREVIEW_CHARS")]
        owns = clear.index("prksVueWorkMetadataEditorOwns")
        self.assertLess(owns, clear.index("textContent"))
        self.assertIn("return", clear[owns:clear.index("textContent")])
        bind = _UI[_UI.index("function prksBindWorkMetaDraftEditor"):_UI.index("function prksDismissWorkMetadataEditor")]
        date_clear = bind[bind.index("const clearDateError"):]
        vue_owns = date_clear.index("prksVueWorkMetadataEditorOwns")
        self.assertLess(vue_owns, date_clear.index("textContent"))
        self.assertLess(date_clear.index("prksVueSetWorkMetadataFieldError"), date_clear.index("textContent"))
        self.assertLess(date_clear.index("return"), date_clear.index("removeAttribute('aria-invalid')"))

    def test_conflict_continuations_stay_on_the_captured_session(self):
        resolve = _META[_META.index("async function actionResolve"):_META.index("root.prksResolveWorkMetadataFieldConflict")]
        self.assertLess(resolve.index("const still"), resolve.index("await root.prksOfflineReconcileWorkField"))
        self.assertLess(resolve.index("prksSync.changed()"), resolve.index("if (!still()) return;"))
        source = _SOURCE[_SOURCE.index("async function resolveSource"):_SOURCE.index("function writeInput")]
        self.assertLess(source.index("const still"), source.index("await root.prksSync.store.resolveConflict"))
        self.assertLess(source.index("prksSync.changed()"), source.index("state.error = null"))
        base = _SOURCE[_SOURCE.index("async function readBase"):_SOURCE.index("async function prepare")]
        self.assertLess(base.index("options.still"), base.index("ctx.setEntity"))

    def test_rejected_base_reads_stay_on_the_current_read(self):
        prepare = _META[_META.index("async function prepare"):_META.index("function mount")]
        catch = prepare[prepare.index("} catch"):]
        self.assertLess(catch.index("readVersion !== state.readVersion"), catch.index("state.observed = null"))
        self.assertLess(catch.index("return"), catch.index("state.observed = null"))
        base = _SOURCE[_SOURCE.index("async function readBase"):_SOURCE.index("async function prepare")]
        source_catch = base[base.index("} catch"):]
        self.assertLess(source_catch.index("readVersion !== state.readVersion"), source_catch.index("state.observed = null"))
        self.assertLess(source_catch.index("options.still"), source_catch.index("state.observed = null"))
        self.assertLess(source_catch.index("return false"), source_catch.index("state.observed = null"))
        source_prepare = _SOURCE[_SOURCE.index("async function prepare"):_SOURCE.index("function mount")]
        self.assertLess(source_prepare.index("if (!(await readBase"), source_prepare.index("safePaint"))


if __name__ == "__main__":
    unittest.main()
