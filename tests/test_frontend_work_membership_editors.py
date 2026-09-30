"""Work tag, folder, and playlist edits recheck the owner before the durable call."""
import subprocess
import unittest
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[1]
_TAGS = (_PROJECT / "frontend" / "js" / "work-tag-editor.js").read_text(encoding="utf-8")
_FOLDERS = (_PROJECT / "frontend" / "js" / "components" / "folders.js").read_text(encoding="utf-8")
_PLAYLISTS = (_PROJECT / "frontend" / "js" / "components" / "playlists.js").read_text(encoding="utf-8")
_UI = (_PROJECT / "frontend" / "js" / "ui.js").read_text(encoding="utf-8")
_API = (_PROJECT / "frontend" / "js" / "api.js").read_text(encoding="utf-8")
_APP = (_PROJECT / "frontend" / "js" / "app.js").read_text(encoding="utf-8")
_FOLDER_STATE = (_PROJECT / "frontend" / "js" / "folder-state.js").read_text(encoding="utf-8")
_PLAYLIST_STATE = (_PROJECT / "frontend" / "js" / "playlist-state.js").read_text(encoding="utf-8")


class WorkMembershipEditorContractTests(unittest.TestCase):
    def test_tag_write_rechecks_the_full_owner_before_coalesce(self):
        edit = _TAGS[_TAGS.index("async function edit("):_TAGS.index("root.prksMountWorkTags")]
        write = edit.index("store.coalesceWorkTag")
        owns_at = edit.rindex("if (!owns(ctx, state)) return;")
        self.assertLess(edit.index("if (!live(ctx, state)) return;"), owns_at)
        self.assertLess(owns_at, write)
        self.assertNotIn("prksOwnerTabId", edit[edit.index("const base"):write])
        self.assertNotIn("prksVueRefreshWorkPanelRead", _TAGS)
        paint = _TAGS[_TAGS.index("async function paint("):_TAGS.index("function acceptAck")]
        self.assertIn("prksRefreshOwnedWorkPanelTags", paint)
        self.assertIn("useLegacyTags = refreshed == null", paint)
        self.assertLess(paint.index("prksRefreshOwnedWorkPanelTags"), paint.index("renderWorkTagsChips"))
        refresh = _UI[_UI.index("function prksRefreshOwnedWorkPanelTags"):_UI.index("function prksFolderRightPanelStackHtml")]
        self.assertNotIn("listOperations", refresh)
        self.assertIn("prksRightPanelOwnedBy", refresh)
        call = refresh.index("prksVueRefreshWorkPanelRead({")
        self.assertLess(refresh.index("return null"), call)
        self.assertGreater(refresh.index("=== true"), call)

    def test_folder_and_playlist_recheck_after_the_base_read(self):
        file_in = _API[_API.index("async function prksFileWorkInFolder"):_API.index("async function addWorkToFolder")]
        self.assertLess(file_in.index("await prksAcknowledgedWorkFolder"), file_in.index("typeof still === 'function'"))
        self.assertLess(file_in.index("typeof still === 'function'"), file_in.index("if (!observed)"))
        self.assertLess(file_in.index("if (!observed)"), file_in.index("prksSetWorkFolderDurably"))
        set_playlist = _PLAYLISTS[_PLAYLISTS.index("async function prksSetWorkPlaylist"):_PLAYLISTS.index("async function addWorkToPlaylist")]
        self.assertLess(set_playlist.index("prksAcknowledgedWorkPlaylist"), set_playlist.index("typeof still === 'function'"))
        self.assertLess(set_playlist.index("typeof still === 'function'"), set_playlist.index("if (!observed)"))
        self.assertLess(set_playlist.index("if (!observed)"), set_playlist.index("prksSetWorkPlaylistDurably"))
        remove = _PLAYLISTS[_PLAYLISTS.index("async function removeWorkFromPlaylist"):_PLAYLISTS.index("async function reorderPlaylist")]
        self.assertLess(remove.index("await prksAcknowledgedWorkPlaylist"), remove.index("typeof still === 'function'"))
        self.assertLess(remove.index("typeof still === 'function'"), remove.index("prksSetWorkPlaylist("))
        mount = _FOLDERS[_FOLDERS.index("async function mountFolderAttachControlsForWork"):]
        folder_set = mount[mount.index("setBtn.onclick"):mount.index("clearBtn.onclick")]
        folder_clear = mount[mount.index("clearBtn.onclick"):]
        self.assertLess(folder_set.index("folderStill()"), folder_set.index("patchWorkFolder(wid, pid, folderStill)"))
        self.assertIn("patchWorkFolder(wid, pid, folderStill)", folder_set)
        self.assertLess(folder_clear.index("folderStill()"), folder_clear.index("patchWorkFolder(wid, null, folderStill)"))
        self.assertIn("prksTabContextOwnsEntityRoute", mount[mount.index("function folderStill()"):mount.index("setBtn.onclick")])
        playlist = _PLAYLISTS[_PLAYLISTS.index("async function mountPlaylistAttachControls("):]
        playlist_set = playlist[playlist.index("setBtn.onclick"):playlist.index("clearBtn.onclick")]
        playlist_clear = playlist[playlist.index("clearBtn.onclick"):playlist.index("newBtn.onclick")]
        self.assertLess(playlist_set.index("playlistStill()"), playlist_set.index("addWorkToPlaylist(pid, wid, playlistStill)"))
        self.assertLess(playlist_clear.index("playlistStill()"), playlist_clear.index("removeWorkFromPlaylist(currentPid, wid, playlistStill)"))
        folder_writer = _FOLDER_STATE[_FOLDER_STATE.index("async function setWorkFolderDurably"):_FOLDER_STATE.index("async function deleteFolderDurably")]
        playlist_writer = _PLAYLIST_STATE[_PLAYLIST_STATE.index("async function setWorkPlaylistDurably"):_PLAYLIST_STATE.index("async function reorderPlaylistItemsDurably")]
        for body, name in ((folder_writer, "folder"), (playlist_writer, "playlist")):
            self.assertNotIn("prksRightPanelOwnedBy", body, name)
            self.assertNotIn("prksTabContextOwnsEntityRoute", body, name)

    def test_new_folder_and_playlist_capture_the_opener(self):
        mount = _FOLDERS[_FOLDERS.index("newBtn.onclick"):_FOLDERS.index("input.onfocus")]
        self.assertIn("workId: wid", mount)
        self.assertIn("tabId:", mount)
        self.assertIn("generation: generation", mount)
        playlist = _PLAYLISTS[_PLAYLISTS.index("newBtn.onclick"):]
        self.assertIn("workId: wid", playlist)
        self.assertIn("tabId:", playlist)
        self.assertIn("generation: generation", playlist)
        folder_save = _APP[_APP.index("const pending = window.__prksPendingWorkFolderAttach"):_APP.index("const playlistBtn")]
        self.assertLess(folder_save.index("prksCapturedWorkPanelStill"), folder_save.index("patchWorkFolder(attachWid, data.id, still)"))
        self.assertLess(folder_save.index("if (!still()) return;"), folder_save.index("patchWorkFolder(attachWid, data.id, still)"))
        playlist_save = _APP[_APP.index("const pending = window.__prksPendingPlaylistAttach"):_APP.index("const personFname")]
        self.assertLess(playlist_save.index("prksCapturedWorkPanelStill"), playlist_save.index("addWorkToPlaylist(newId, pending.workId, still)"))
        self.assertIn("if (pending && pending.workId && still())", playlist_save)

    def test_runtime_stale_membership_does_not_write(self):
        proc = subprocess.run(
            ["node", str(_PROJECT / "tests" / "browser" / "run_work_membership_editor_selftest.js")],
            cwd=_PROJECT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("checks passed", proc.stdout)


if __name__ == "__main__":
    unittest.main()
