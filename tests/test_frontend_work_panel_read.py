"""Work right-panel read surface stays owner-scoped and leaves mutation mounts in place."""
import unittest
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[1]
_UI = (_PROJECT / "frontend" / "js" / "ui.js").read_text(encoding="utf-8")
_META = (_PROJECT / "frontend" / "js" / "work-metadata-editor.js").read_text(encoding="utf-8")
_ROLES = (_PROJECT / "frontend" / "js" / "work-role-editor.js").read_text(encoding="utf-8")
_TAGS = (_PROJECT / "frontend" / "js" / "work-tag-editor.js").read_text(encoding="utf-8")
_MAIN = (_PROJECT / "frontend-app" / "src" / "main.ts").read_text(encoding="utf-8")
_READ = (_PROJECT / "frontend-app" / "src" / "features" / "work" / "panel-read.ts").read_text(encoding="utf-8")
_SESSION = (_PROJECT / "frontend-app" / "src" / "features" / "work" / "panel-session.ts").read_text(encoding="utf-8")
_ENTRY = (_PROJECT / "frontend-app" / "src" / "features" / "work" / "browser-entry.ts").read_text(encoding="utf-8")


def _update_panel(source: str) -> str:
    start = source.index("function updatePanelContent(tabId) {")
    nxt = source.index("\nfunction ", start + len("function updatePanelContent"))
    return source[start:nxt]


class WorkPanelReadContractTests(unittest.TestCase):
    def test_focus_switch_captures_draft_and_flushes_notes_before_replace(self):
        """Call order only. The executable path is panel-focus-switch.test.ts."""
        body = _update_panel(_UI)
        capture = body.index("prksCaptureWorkMetaDraft(previousOwner)")
        flush = body.index("prksFlushPendingPrivateNotes(previousOwner)")
        dismiss = body.index("prksDismissWorkPanelRead()")
        html = body.index("panel.innerHTML = prksWorkRightPanelStackHtml")
        publish = body.index("prksPublishWorkPanelRead(focusedCtx, _cw)")
        self.assertLess(capture, flush)
        self.assertLess(flush, dismiss)
        self.assertLess(dismiss, html)
        self.assertLess(html, publish)
        for mount in (
            "initPrksPrivateNotesEditor('work', _cw.id, focusedCtx)",
            "initWorkTagCombobox(_cw.id, focusedCtx)",
            "prksMountWorkMetadataEditor(focusedCtx, _cw.id)",
            "prksMountWorkSourceEditor(focusedCtx, _cw.id)",
            "prksMountWorkRoleEditor(focusedCtx, _cw.id",
            "mountPlaylistAttachControls(_cw, focusedCtx)",
            "mountFolderAttachControlsForWork(_cw, focusedCtx)",
        ):
            self.assertLess(body.index(mount), publish, mount)

    def test_read_surface_does_not_own_mutations_or_the_queue(self):
        for source in (_READ, _SESSION):
            self.assertNotIn("listOperations", source)
            self.assertNotIn("saveWorkMetadataFields", source)
            self.assertNotIn("prksSaveWorkPersonRoleDurably", source)
            self.assertNotIn("coalesceWorkTag", source)
            self.assertNotIn("prksSetWorkFolderDurably", source)
            self.assertNotIn("prksSetWorkPlaylistDurably", source)
        self.assertNotIn("panel-session", _ENTRY)
        self.assertIn("registerWorkPanelReadBridge", _MAIN)
        self.assertIn("export interface WorkPanelEditorBase", _READ)
        self.assertIn("readonly display: WorkPanelDisplay", _READ)

    def test_failed_metadata_read_does_not_refresh_the_panel_as_empty(self):
        self.assertIn("prksPendingWorkMetadataState", _META)
        self.assertIn("hydration !== 'unavailable'", _META)
        refresh = _META.index("root.prksVueRefreshWorkPanelRead({")
        self.assertLess(_META.index("hydration !== 'unavailable'"), refresh)
        self.assertLess(_META.index("delete effectiveMetadata.roles"), refresh)
        self.assertIn("prksDocTypeMeta", _META)
        self.assertIn("PRKS_PROGRESS_STATUS_ICON", _META)
        publish = _UI[_UI.index("function prksPublishWorkPanelRead"):_UI.index("function prksFolderRightPanelStackHtml")]
        self.assertIn("installed === projection.work", publish)
        self.assertIn("prksEffectiveWorkSync", publish)
        self.assertIn("prksEffectiveWorkDetailRoles", publish)
        self.assertNotIn("people: []", _ROLES)
        self.assertNotIn("prksVueRefreshWorkPanelRead", _ROLES)
        self.assertIn("prksRefreshOwnedWorkPanelRead", _ROLES)
        self.assertIn("prksEffectiveWorkDetailRoles", _UI)
        self.assertNotIn("prksVueRefreshWorkPanelRead", _TAGS)
        self.assertIn("prksRefreshOwnedWorkPanelTags", _TAGS)
        self.assertLess(_TAGS.index("if (!live(ctx, state)) return;"), _TAGS.index("store.coalesceWorkTag"))
        self.assertIn("prksEffectiveWorkTags(work, state.operations)", _TAGS)

    def test_view_shell_keeps_editor_hosts(self):
        shell = _UI[_UI.index("function prksWorkPanelViewShellHtml"):_UI.index("function renderWorkMetaTab")]
        self.assertIn('data-prks-role="work-bib-rows"', shell)
        self.assertIn('class="work-linked-persons-by-role"', shell)
        self.assertIn('id="work-tags-list"', shell)
        self.assertIn("Edit metadata", shell)
        self.assertIn("Manage relationships", shell)
        self.assertIn("Manage tags", shell)
        self.assertIn("copy-bibtex-btn", shell)
        self.assertIn("delete-work-btn", shell)
        self.assertIn('data-prks-role="work-panel-read-anchor"', _UI)
        self.assertIn('data-prks-role="work-folder-read"', (_PROJECT / "frontend/js/components/folders.js").read_text(encoding="utf-8"))
        self.assertIn('data-prks-role="work-playlist-read"', (_PROJECT / "frontend/js/components/playlists.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
