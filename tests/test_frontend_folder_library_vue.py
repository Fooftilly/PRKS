"""Static contracts for Folder Library Vue route-surface (#261 / #170)."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "js"
FRONTEND_APP = ROOT / "frontend-app" / "src" / "features" / "folder-library"


class FolderLibraryVueContracts(unittest.TestCase):
    def test_coordinator_presents_vue_folder_library(self):
        app = (FRONTEND / "app.js").read_text()
        self.assertIn("function prksPresentVueFolderLibrary", app)
        self.assertIn("prksVuePresentFolderLibrary", app)
        self.assertIn("sameFolderLibraryWorkspace", app)
        self.assertIn("__prksRetainFolderLibrarySurface", app)
        folders_case = app[app.index("case 'folders': {"): app.index("case 'playlists': {")]
        self.assertIn("prksPresentVueFolderLibrary", folders_case)
        self.assertNotIn("renderDashboard(", folders_case)
        self.assertIn("skipPageEnter: sameFolderLibraryWorkspace", folders_case)
        # Retained surfaces must still dismiss on same-route error after retain.
        self.assertIn(
            "sameFolderLibraryWorkspace && typeof window.prksVueDismissFolderLibrary",
            app,
        )

    def test_folder_library_route_reuses_host_on_in_place_refresh(self):
        app = (FRONTEND / "app.js").read_text()
        present = app[
            app.index("function prksPresentVueFolderLibrary") : app.index(
                "function prksPresentVueConcepts"
            )
        ]
        self.assertIn(":scope > [data-prks-vue-route-host]", present)
        self.assertIn("contentDiv.innerHTML = '';", present)
        self.assertLess(present.index("querySelector"), present.index("contentDiv.innerHTML = '';"))

    def test_create_folder_success_navigates_instead_of_legacy_remount(self):
        app = (FRONTEND / "app.js").read_text()
        # create-folder success on #/folders must not call renderDashboard.
        create_region = app[app.index("ownerRoute.name === 'folders'"):]
        create_block = create_region[: create_region.index("if (typeof prksNavigate === 'function') prksNavigate('#/folders');") + 80]
        self.assertIn("prksNavigate('#/folders'", create_block)
        self.assertNotIn("renderDashboard(", create_block)

    def test_vue_feature_owns_preview_lifecycle_helpers(self):
        lifecycle = (FRONTEND_APP / "work-thumb-lifecycle.ts").read_text()
        self.assertIn("prksReleaseWorkThumbPreview", lifecycle)
        self.assertIn("prksReleaseLazyWorkThumbs", lifecycle)
        self.assertIn("prksInitLazyWorkThumbs", lifecycle)
        # Scoped release only — global hide would dismiss another pane's preview.
        self.assertNotIn("prksHideWorkThumbPreview()", lifecycle)
        pane = (FRONTEND_APP / "RecentlyAddedPane.vue").read_text()
        self.assertIn("releaseWorkThumbResources", pane)
        self.assertIn("initLazyWorkThumbs", pane)
        self.assertIn("overlayRevision", pane)
        # Cached paints must still init so IntersectionObserver prune runs (#170).
        self.assertNotIn("if (!props.offlineCached)", pane)
        folders = (FRONTEND / "components" / "folders.js").read_text()
        self.assertIn("prksToggleFolderNodeInHost", folders)
        self.assertIn("prksToggleAllFolderNodesInHost", folders)
        self.assertIn("st.vueOwned", folders)
        self.assertIn("delegateToggle", folders)
        tree = (FRONTEND_APP / "FolderTree.vue").read_text()
        self.assertIn("delegateToggle: true", tree)
        # Legacy tab helper must await the Vue switchTab bridge (load+paint).
        switch = folders[folders.index("function prksSwitchFolderLibraryTab") :]
        switch = switch[: switch.index("\nfunction prksFolderLibraryCatalogGlanceParts")]
        self.assertIn("st.switchTab", switch)
        self.assertIn("vueOwned", switch)
        route = (FRONTEND_APP / "FolderLibraryRoute.vue").read_text()
        self.assertIn("switchTab: (tab: string) =>", route)
        self.assertIn("Re-entering Recently Added always awaits load", route)

    def test_vueuse_is_selective_generic_only(self):
        route = (FRONTEND_APP / "FolderLibraryRoute.vue").read_text()
        tree = (FRONTEND_APP / "FolderTree.vue").read_text()
        pane = (FRONTEND_APP / "RecentlyAddedPane.vue").read_text()
        combined = route + tree + pane
        self.assertIn("useDebounceFn", combined)
        self.assertIn("useEventListener", combined)
        # Preview ownership must not be handed to VueUse.
        self.assertNotIn("useIntersectionObserver", combined)
        self.assertNotIn("useMouseInElement", combined)

    def test_bridge_publishes_loaded_rows_for_glance_without_legacy_paint(self):
        """Retained refresh must keep rows for glance; vueOwned blocks legacy DOM paint."""
        route = (FRONTEND_APP / "FolderLibraryRoute.vue").read_text()
        sync = route[route.index("function syncLegacyDashboardState") :]
        sync = sync[: sync.index("\nfunction paintCatalogGlance")]
        self.assertIn("recentlyAddedWorks: recentlyAddedWorks.value", sync)
        self.assertNotIn("recentlyAddedWorks: null", sync)
        self.assertIn("vueOwned: true", sync)
        folders = (FRONTEND / "components" / "folders.js").read_text()
        rerender = folders[
            folders.index("function prksRerenderFolderLibraryRecentlyAddedOnly") :
        ]
        rerender = rerender[: rerender.index("\nfunction prksApplyFolderLibraryFilesSearchFilter")]
        self.assertIn("if (st.vueOwned) return;", rerender)
        glance = folders[folders.index("async function prksCollectFolderLibraryGlanceExtras") :]
        glance = glance[: glance.index("\nasync function prksScheduleFolderLibraryGlance")]
        self.assertIn("stMem.recentlyAddedWorks.length", glance)

    def test_e2e_preview_seed_owns_source_under_recently_added_pane(self):
        e2e = (ROOT / "tests" / "e2e" / "test_folder_library_route_surface.py").read_text()
        self.assertIn("pane.appendChild(source)", e2e)
        self.assertIn('owned: !!(pane.contains(source))', e2e)
        self.assertIn('self.assertTrue(seeded["owned"])', e2e)


if __name__ == "__main__":
    unittest.main()
