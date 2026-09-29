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
        source = _SOURCE[_SOURCE.index("async function save(workId)"):_SOURCE.index("root.prksResolveWorkSourceConflict")]
        self.assertLess(source.index("if (!still()) return;"), source.index("store.saveWorkSource"))
        self.assertIn("workMetaDraft.source_url", source)

    def test_vue_session_does_not_read_the_queue(self):
        for source in (_DRAFT, _SESSION):
            self.assertNotIn("listOperations", source)
            self.assertNotIn("saveWorkMetadataFields", source)
        self.assertIn("workMetaSessionStill", _DRAFT)
        self.assertIn("registerWorkMetadataEditorBridge", _SESSION)
        self.assertIn("data-prks-role=\"work-metadata-editor-anchor\"", _UI)


if __name__ == "__main__":
    unittest.main()
