"""Static contracts for the Playlists Vue route surface (#276 / #230)."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "js"
FEATURE = ROOT / "frontend-app" / "src" / "features" / "playlists"


class PlaylistsVueContracts(unittest.TestCase):
    def test_coordinator_owns_effective_playlist_projection(self):
        app = (FRONTEND / "app.js").read_text()
        index = app[app.index("case 'playlists': {") : app.index("case 'playlist-detail': {")]
        detail = app[app.index("case 'playlist-detail': {") : app.index("case 'folder-detail': {")]
        self.assertIn("prksEffectivePlaylistRows(", index)
        self.assertIn("prksPresentVuePlaylists(", index)
        self.assertIn("availability: 'unavailable'", index)
        self.assertIn("renderPlaylistsIndex(pls, contentDiv, ctx)", index)
        self.assertIn("prksEffectivePlaylistDetail(", detail)
        self.assertIn("prksPendingCreatedPlaylist", detail)
        self.assertIn("prksHydratePendingWorkMetadata()", detail)
        self.assertIn("renderPlaylistDetail(ctx, pl, contentDiv", detail)
        self.assertIn("availability: 'unavailable'", detail)
        self.assertIn("ctx.ui.playlistEditing = false;", detail)
        self.assertLess(
            detail.index("prksHydratePendingWorkMetadata()"),
            detail.index("renderPlaylistDetail(ctx, pl, contentDiv"),
        )

    def test_playlists_route_reuses_host_and_dismisses_before_retry(self):
        app = (FRONTEND / "app.js").read_text()
        self.assertIn("samePlaylistsWorkspace", app)
        self.assertIn("__prksRetainPlaylistsSurface", app)
        present = app[
            app.index("function prksPresentVuePlaylists") : app.index("function prksRenderRouteLoading")
        ]
        self.assertIn(":scope > [data-prks-vue-route-host]", present)
        self.assertIn("contentDiv.innerHTML = '';", present)
        self.assertLess(present.index("querySelector"), present.index("contentDiv.innerHTML = '';"))
        self.assertIn(
            "samePlaylistsWorkspace && typeof window.prksVueDismissPlaylists",
            app,
        )
        refresh = app[
            app.index("function prksOfflineMaybeRefreshFocusedRoute") : app.index(
                "function prksRenderConnectivityIndicator"
            )
        ]
        self.assertIn("ctx.ui.playlistEditing", refresh)

    def test_vue_calls_public_wrappers_and_not_the_durable_store(self):
        combined = "\n".join(path.read_text() for path in FEATURE.rglob("*") if path.is_file())
        self.assertNotIn("listOperations", combined)
        self.assertNotIn("prksDurable", combined)
        self.assertNotIn("fetch(", combined)
        self.assertNotIn("prksRequest(", combined)
        self.assertNotIn("useDebounceFn", combined)
        self.assertNotIn("useEventListener", combined)
        self.assertNotIn("createPinia", combined)
        self.assertNotIn("vue-router", combined)
        intents = (FEATURE / "intents.ts").read_text()
        for wrapper in (
            "updatePlaylist",
            "addWorkToPlaylist",
            "removeWorkFromPlaylist",
            "reorderPlaylist",
            "deletePlaylistFromDetail",
            "prksOpenNewPlaylistModalFromPlaylistsPage",
            "prksSaveWorkFieldDurably",
        ):
            self.assertIn(wrapper, intents, wrapper)
        self.assertNotIn("prksSavePlaylistFieldsDurably", intents)
        self.assertNotIn("prksReorderPlaylistItemsDurably", intents)
        self.assertNotIn("prksSetWorkPlaylistDurably", intents)
        self.assertNotIn("prksDeletePlaylistDurably", intents)
        index = (FEATURE / "PlaylistsIndexRoute.vue").read_text()
        self.assertIn('id="prks-playlists-header-new"', index)
        self.assertIn('id="prks-playlists-empty-new"', index)
        self.assertIn("No playlists yet.", index)
        self.assertIn("New playlist", index)
        self.assertIn('data-prks-role="offline-unavailable"', index)
        detail = (FEATURE / "PlaylistDetailRoute.vue").read_text()
        self.assertIn('id="prks-playlist-edit-title"', detail)
        self.assertIn('id="prks-playlist-edit-desc"', detail)
        self.assertIn('id="prks-playlist-edit-original-url"', detail)
        self.assertIn('id="prks-playlist-edit-save"', detail)
        self.assertIn('id="prks-playlist-add-search"', detail)
        add_at = detail.index('id="prks-playlist-add-search"')
        add_tag = detail[add_at : detail.index(">", add_at)]
        self.assertNotIn("disabled", add_tag)
        self.assertIn(':key="item.id"', detail)
        self.assertNotIn(':key="index"', detail)
        stub = (FRONTEND / "components" / "playlists.js").read_text()
        self.assertIn("async function updatePlaylist(", stub)
        self.assertIn("async function createPlaylist(", stub)
        self.assertIn("function renderPlaylistAttachControlsHtml(", stub)
        self.assertIn("function mountPlaylistAttachControls(", stub)
        self.assertIn("window.prksOpenNewPlaylistModalFromPlaylistsPage", stub)
        self.assertIn("async function prksReloadPlaylistDetail(", stub)
        self.assertIn("async function deletePlaylistFromDetail(", stub)
        sidebar = (FRONTEND / "ui.js").read_text()
        self.assertIn('id="prks-playlist-edit-btn"', sidebar)
        self.assertIn('id="prks-create-playlist-btn"', sidebar)
