"""Structural + Node regressions for Library Navigation V1 (Folder hierarchy switcher)."""
from __future__ import annotations

import os
import subprocess
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INDEX = os.path.join(_ROOT, "frontend", "index.html")
_FOLDERS = os.path.join(_ROOT, "frontend", "js", "components", "folders.js")
_NAV = os.path.join(_ROOT, "frontend", "js", "folder-hierarchy-nav.js")
_CSS = os.path.join(_ROOT, "frontend", "css", "style.css")
_WIKI = os.path.join(_ROOT, "docs", "wiki", "User-Guide.md")
_RUNNER = os.path.join(_ROOT, "tests", "browser", "run_folder_hierarchy_nav_selftest.js")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendFolderHierarchyNavTests(unittest.TestCase):
    def test_module_loads_before_folders_component(self):
        html = _read(_INDEX)
        nav_at = html.find('src="/js/folder-hierarchy-nav.js"')
        folders_at = html.find('src="/js/components/folders.js"')
        self.assertNotEqual(nav_at, -1)
        self.assertNotEqual(folders_at, -1)
        self.assertLess(nav_at, folders_at)
        self.assertTrue(os.path.isfile(_NAV))

    def test_folder_detail_mounts_switcher(self):
        folders = _read(_FOLDERS)
        self.assertIn("prksFolderNavTriggerHtml", folders)
        self.assertIn("prksMountFolderHierarchyNav", folders)
        self.assertIn("prks-folder-detail__header", folders)
        self.assertIn("data-prks-role=\"folder-hierarchy-nav\"", folders)
        self.assertIn("tabId: ctx && ctx.tabId", folders)
        # Desktop IA: persistent hierarchy tree | contents; compact band is fallback.
        self.assertIn("data-prks-folder-detail-tree-host", folders)
        self.assertIn("prksBindFolderDetailLayout", folders)
        self.assertIn("prksFillFolderDetailTree", folders)
        self.assertIn("PRKS_FOLDER_DETAIL_NARROW_PX", folders)
        self.assertIn("data-prks-folder-layout-lock", folders)
        self.assertIn("prksLoadFolderHierarchyCatalogue", folders)
        self.assertIn("hierarchyBaseGeneration", _read(_NAV))
        self.assertIn("genAtStart", _read(_NAV))
        # First sync event (undefined fingerprint) must bump generation too.
        self.assertIn("No baseline yet", _read(_NAV))
        # Folder→Folder in-place workspace (no generic Loading wipe).
        self.assertIn("preserveFolderWorkspace", folders)
        self.assertIn("prksFolderDetailSelectInTree", folders)
        self.assertIn("selectionOnly", folders)
        # New folder on detail clears modal via shared helper + default parent.
        self.assertIn("prksOpenFolderModalFromLibrarySearch('')", folders)
        app = _read(os.path.join(_ROOT, "frontend", "js", "app.js"))
        self.assertIn("sameFolderWorkspace", app)
        self.assertIn("skipPageEnter: sameFolderWorkspace", app)
        # Preserved shells must replace route-scoped banners, not stack them.
        self.assertIn("prksClearRouteScopedApiWarningBanners", app)
        prepend_at = app.index("function prksOfflinePrependBanner")
        prepend_body = app[prepend_at : app.index("function prksOfflineRenderUnavailable")]
        self.assertIn('offline-provenance-banner', prepend_body)
        self.assertIn(".forEach(function (el) {\n            el.remove();", prepend_body)
        nav_js = _read(os.path.join(_ROOT, "frontend", "js", "navigation.js"))
        self.assertIn("skipPageEnter", nav_js)

    def test_switcher_aria_uses_dialog_and_listbox(self):
        nav = _read(_NAV)
        self.assertIn("role', 'dialog'", nav)
        self.assertIn("role', 'listbox'", nav)
        self.assertIn("LISTBOX_ID", nav)
        self.assertIn("aria-haspopup=\"dialog\"", nav)
        self.assertIn("data-prks-folder-nav-tab-id", nav)
        # Filter must not be a descendant of the listbox role host.
        self.assertIn("Filter stays outside the listbox", nav)
        self.assertIn("'folder-detail': true", _read(os.path.join(_ROOT, "frontend", "js", "navigation.js")))
        # No feature-code hash writes; navigate only through prksNavigate.
        self.assertNotIn("location.hash =", nav)
        self.assertIn("registerCleanup", nav)
        # Hierarchy fetch follows the owning TabContext AbortSignal (cold-park).
        self.assertIn("signalForOwner", nav)
        self.assertIn("abortController.signal", nav)
        self.assertNotIn("sibling Folder pane can still warm", nav)
        # Failed folders:index must not leave the Nearby band on Loading forever.
        self.assertIn("Could not load nearby folders", nav)
        self.assertIn("hierarchyLoadError", nav)
        # Unavailable offline reads must not collapse via fetchFolders() → [].
        self.assertIn("offlineUnavailable", nav)
        self.assertIn("Do NOT fall through to fetchFolders()", nav)
        # Folder create/rename/delete must invalidate the cached hierarchy base.
        self.assertIn("folderStructureOpsFingerprint", nav)
        self.assertIn("ensureHierarchySyncBound", nav)
        self.assertIn("CREATE_FOLDER", nav)
        self.assertIn("prksLoadFolderHierarchyCatalogue", nav)
        sw = _read(os.path.join(_ROOT, "frontend", "sw.js"))
        self.assertIn("'/js/folder-hierarchy-nav.js'", sw)
        # Must be a STATIC_PRECACHE_PATHS entry (shell-manifest coverage), not
        # merely mentioned elsewhere in the service worker.
        self.assertRegex(
            sw,
            r"STATIC_PRECACHE_PATHS\s*=\s*\[[^\]]*'/js/folder-hierarchy-nav\.js'",
        )

    def test_css_exposes_compact_switcher(self):
        css = _read(_CSS)
        for cls in (
            ".prks-folder-detail",
            ".prks-folder-detail__tree-pane",
            ".prks-folder-detail__main",
            ".prks-folder-nav__band",
            ".prks-folder-nav__crumbs",
            ".prks-folder-nav__nearby",
            ".prks-folder-nav__chip",
            ".prks-folder-nav__trigger",
            ".prks-folder-nav__panel",
            ".prks-folder-nav__option",
            ".prks-folder-nav__option.is-current",
        ):
            self.assertIn(cls, css)
        # Wide layout hides compact band; narrow hides persistent tree.
        self.assertIn('.prks-folder-detail[data-prks-folder-layout="wide"] .prks-folder-nav', css)
        self.assertIn(
            '.prks-folder-detail[data-prks-folder-layout="narrow"] .prks-folder-detail__tree-pane',
            css,
        )
        # No alternate permanent sidebar class for the compact nav itself.
        self.assertNotIn(".prks-folder-nav__sidebar", css)

    def test_user_guide_mentions_switcher(self):
        wiki = _read(_WIKI)
        self.assertIn("folder navigation", wiki.lower())
        self.assertIn("hierarchy tree", wiki.lower())
        self.assertIn("narrow", wiki.lower())

    def test_runtime_selftests(self):
        proc = subprocess.run(
            ["node", _RUNNER],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            self.fail(proc.stdout + "\n" + proc.stderr)


if __name__ == "__main__":
    unittest.main()
