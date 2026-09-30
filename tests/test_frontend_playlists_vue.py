"""Static contracts for the Playlists Vue route surface (#276 / #230)."""
from __future__ import annotations

import subprocess
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
        self.assertIn("prksOfflinePrependBanner(contentDiv, null)", index)
        self.assertIn("notFoundTitle: 'Playlists not available offline'", index)
        self.assertIn("renderPlaylistsIndex(pls, contentDiv, ctx)", index)
        self.assertIn("prksEffectivePlaylistDetail(", detail)
        self.assertIn("prksPendingCreatedPlaylist", detail)
        self.assertIn("prksHydratePendingWorkMetadata()", detail)
        self.assertIn("renderPlaylistDetail(ctx, pl, contentDiv", detail)
        self.assertIn("availability: 'unavailable'", detail)
        self.assertIn("prksOfflinePrependBanner(contentDiv, null)", detail)
        self.assertIn("notFoundTitle: 'Playlist not available offline'", detail)
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
        self.assertIn(
            "Edit the title, description, and videos in the playlist. Details stays a summary.",
            sidebar,
        )
        self.assertNotIn(
            "Edit title/description and add videos from the Details panel.",
            sidebar,
        )

    def test_new_playlist_navigates_the_originating_owner(self):
        app = (FRONTEND / "app.js").read_text()
        save = app[app.index("let attachedWork") : app.index("} catch (e)", app.index("let attachedWork"))]
        self.assertIn("prksTakePlaylistIndexCreateTabId", save)
        self.assertIn("prksNavigate('#/playlists/' + encodeURIComponent(newId), { tabId: createTabId })", save)
        self.assertNotIn("location.hash", save)
        stub = (FRONTEND / "components" / "playlists.js").read_text()
        take = stub[
            stub.index("function prksTakePlaylistIndexCreateTabId()") : stub.index(
                "function prksOpenNewPlaylistModalFromPlaylistsPage"
            )
        ]
        self.assertIn("origin.suppressed", take)
        self.assertIn("ctx.generation !== origin.generation", take)
        self.assertIn("route.name !== 'playlists'", take)
        self.assertIn("window.prksTakePlaylistIndexCreateTabId", stub)
        self.assertIn("prksSuppressPlaylistIndexCreateOrigin()", stub)
        delete_fn = stub[
            stub.index("async function deletePlaylistFromDetail(") : stub.index(
                "function prksPlaylistIndexCreateOwner"
            )
        ]
        self.assertNotIn("prksSetButtonBusy", delete_fn)
        self.assertIn("will stay in your library", delete_fn)
        detail = (FEATURE / "PlaylistDetailRoute.vue").read_text()
        self.assertIn("Deleting…", detail)
        for label in ("Saving…", "Adding…", "Removing…", "Reordering…", "Renaming…"):
            self.assertIn(f'busy-label="{label}"', detail)
        self.assertIn("prksOfflineRuntimeSubscribe", detail)
        self.assertNotIn("addEventListener('online'", detail)
        self.assertNotIn(":key=\"playlist.id\"", detail)
        self.assertIn('role="link"', detail)
        self.assertIn('@keydown.enter.prevent="activateRouteLink"', detail)
        index = (FEATURE / "PlaylistsIndexRoute.vue").read_text()
        self.assertIn('role="link"', index)
        self.assertIn('tabindex="0"', index)
        self.assertIn('@keydown.enter.prevent="activateRouteLink"', index)
        agents = (ROOT / "frontend" / "AGENTS.md").read_text()
        self.assertIn("does not read `location.hash`", agents)

    def test_stale_delete_confirmation_does_not_delete_or_navigate(self):
        script = r"""
const fs = require('fs');
const vm = require('vm');
const context = { console };
context.window = context;
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync('frontend/js/components/playlists.js', 'utf8'), context);
const playlist = { id: 'PL-A', title: 'A', items: [{ id: 'W1' }] };
const ctx = {
  tabId: 'side',
  generation: 4,
  ui: { playlistEditing: true },
  isCurrent(generation) { return generation === 4 && context.owns; },
};
let confirm;
context.owns = true;
context.deleted = false;
context.navigated = null;
context.calls = [];
context.prksTabContextOwnsEntityRoute = (owner, generation, type, id, route) => {
  context.calls.push({ generation, type, id, route, tabId: owner && owner.tabId });
  return context.owns;
};
context.prksDeletePlaylistDurably = async () => { context.deleted = true; };
context.prksConfirmDestructive = () => new Promise((resolve) => { confirm = resolve; });
context.prksNavigate = (hash, opts) => { context.navigated = { hash, tabId: opts && opts.tabId }; };

(async () => {
  const stale = context.deletePlaylistFromDetail(ctx, playlist, 4);
  await Promise.resolve();
  context.owns = false;
  ctx.generation = 9;
  confirm(true);
  await stale;
  if (context.deleted) throw new Error('stale confirmation deleted the playlist');
  if (context.navigated) throw new Error('stale confirmation navigated');
  if (ctx.ui.playlistEditing !== true) throw new Error('stale confirmation cleared editing');
  if (context.calls.length !== 1) throw new Error('expected one ownership check, got ' + context.calls.length);
  const check = context.calls[0];
  if (check.generation !== 4 || check.type !== 'playlist' || check.id !== 'PL-A' || check.route !== 'playlist-detail' || check.tabId !== 'side') {
    throw new Error('ownership check args ' + JSON.stringify(check));
  }

  context.owns = true;
  context.deleted = false;
  context.navigated = null;
  context.calls = [];
  ctx.generation = 4;
  ctx.ui.playlistEditing = true;
  context.prksConfirmDestructive = async () => true;
  await context.deletePlaylistFromDetail(ctx, playlist, 4);
  if (!context.deleted) throw new Error('owned confirmation did not delete');
  if (!context.navigated || context.navigated.tabId !== 'side' || context.navigated.hash !== '#/playlists') {
    throw new Error('owned confirmation did not navigate the owner');
  }
  if (ctx.ui.playlistEditing !== false) throw new Error('owned delete left editing on');
  if (context.calls.length < 2) throw new Error('expected a check before navigation');

  context.owns = true;
  context.deleted = false;
  context.navigated = null;
  ctx.ui.playlistEditing = true;
  context.prksDeletePlaylistDurably = async () => {
    context.owns = false;
    context.deleted = true;
  };
  await context.deletePlaylistFromDetail(ctx, playlist, 4);
  if (!context.deleted) throw new Error('delete should start while the route is still owned');
  if (context.navigated) throw new Error('navigated after ownership was lost during delete');
  if (ctx.ui.playlistEditing !== true) throw new Error('cleared editing after the route was replaced');
})().catch((err) => {
  console.error(err && err.stack || err);
  process.exit(1);
});
"""
        proc = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
