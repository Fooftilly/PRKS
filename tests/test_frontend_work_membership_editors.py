"""Work tag, folder, and playlist edits recheck the owner before the durable call."""
import unittest
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[1]
_TAGS = (_PROJECT / "frontend" / "js" / "work-tag-editor.js").read_text(encoding="utf-8")
_FOLDERS = (_PROJECT / "frontend" / "js" / "components" / "folders.js").read_text(encoding="utf-8")
_PLAYLISTS = (_PROJECT / "frontend" / "js" / "components" / "playlists.js").read_text(encoding="utf-8")
_UI = (_PROJECT / "frontend" / "js" / "ui.js").read_text(encoding="utf-8")


class WorkMembershipEditorContractTests(unittest.TestCase):
    def test_tag_write_rechecks_the_panel_owner(self):
        edit = _TAGS[_TAGS.index("async function edit("):_TAGS.index("root.prksMountWorkTags")]
        write = edit.index("store.coalesceWorkTag")
        self.assertLess(edit.index("if (!live(ctx, state)) return;"), write)
        self.assertLess(edit.index("prksOwnerTabId"), write)
        self.assertNotIn("prksVueRefreshWorkPanelRead", _TAGS)
        self.assertIn("prksRefreshOwnedWorkPanelTags", _TAGS)
        refresh = _UI[_UI.index("function prksRefreshOwnedWorkPanelTags"):_UI.index("function prksFolderRightPanelStackHtml")]
        self.assertNotIn("listOperations", refresh)
        self.assertIn("prksRightPanelOwnedBy", refresh)

    def test_folder_and_playlist_recheck_before_the_durable_call(self):
        mount = _FOLDERS[_FOLDERS.index("async function mountFolderAttachControlsForWork"):]
        folder_set = mount[mount.index("setBtn.onclick"):mount.index("clearBtn.onclick")]
        folder_clear = mount[mount.index("clearBtn.onclick"):]
        self.assertLess(folder_set.index("folderStill()"), folder_set.index("patchWorkFolder(wid, pid)"))
        self.assertLess(folder_clear.index("folderStill()"), folder_clear.index("patchWorkFolder(wid, null)"))
        self.assertIn("prksTabContextOwnsEntityRoute", mount[mount.index("function folderStill()"):mount.index("setBtn.onclick")])
        playlist = _PLAYLISTS[_PLAYLISTS.index("async function mountPlaylistAttachControls("):]
        playlist_set = playlist[playlist.index("setBtn.onclick"):playlist.index("clearBtn.onclick")]
        playlist_clear = playlist[playlist.index("clearBtn.onclick"):playlist.index("newBtn.onclick")]
        self.assertLess(playlist_set.index("ownsPanel(panel)"), playlist_set.index("addWorkToPlaylist(pid, wid)"))
        self.assertLess(playlist_clear.index("ownsPanel(panel)"), playlist_clear.index("removeWorkFromPlaylist(currentPid, wid)"))
        self.assertIn("prksSetWorkPlaylistDurably", _PLAYLISTS)
        self.assertIn("prksSetWorkFolderDurably", (_PROJECT / "frontend" / "js" / "api.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
