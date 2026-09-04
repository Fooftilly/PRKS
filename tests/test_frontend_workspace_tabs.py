"""Structural + Node regressions for stacked workspace tabs."""
import os
import re
import shutil
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_INDEX = os.path.join(_FRONTEND, "index.html")
_WS = os.path.join(_FRONTEND, "js", "workspace-tabs.js")
_TILING = os.path.join(_FRONTEND, "js", "workspace-tiling.js")
_NAV = os.path.join(_FRONTEND, "js", "navigation.js")
_APP = os.path.join(_FRONTEND, "js", "app.js")
_COORD = os.path.join(_FRONTEND, "js", "request-coordinator.js")
_API = os.path.join(_FRONTEND, "js", "api.js")
_DESIGN = os.path.join(_PROJECT_DIR, "DESIGN.md")
_AGENTS = os.path.join(_PROJECT_DIR, "AGENTS.md")
_README = os.path.join(_PROJECT_DIR, "README.md")
_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_workspace_tabs_selftest.js")

_HASH_ASSIGN_RE = re.compile(r"(?:window\.)?location\.hash\s*=(?!=)")
_OPEN_BLANK_RE = re.compile(r"""window\.open\s*\([^)]*['_"]_blank['_"]""")

_LOW_LEVEL_HASH_FILES = {
    os.path.join(_FRONTEND, "js", "navigation.js"),
    os.path.join(_FRONTEND, "js", "workspace-tabs.js"),
    os.path.join(_FRONTEND, "js", "app.js"),
}


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _frontend_js_files():
    root = os.path.join(_FRONTEND, "js")
    out = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.endswith(".js"):
                out.append(os.path.join(dirpath, name))
    out.sort()
    return out


def _line_at(src: str, index: int) -> str:
    start = src.rfind("\n", 0, index) + 1
    end = src.find("\n", index)
    if end < 0:
        end = len(src)
    return src[start:end]


class FrontendWorkspaceTabsTests(unittest.TestCase):
    def test_workspace_module_load_order(self):
        html = _read(_INDEX)
        nav_at = html.find('src="/js/navigation.js"')
        ws_at = html.find('src="/js/workspace-tabs.js"')
        tc_at = html.find('src="/js/tab-context.js"')
        tiling_at = html.find('src="/js/workspace-tiling.js"')
        menu_at = html.find('src="/js/workspace-tab-menu.js"')
        coord_at = html.find('src="/js/request-coordinator.js"')
        api_at = html.find('src="/js/api.js"')
        app_at = html.find('src="/js/app.js"')
        self.assertNotEqual(nav_at, -1)
        self.assertNotEqual(ws_at, -1)
        self.assertNotEqual(tiling_at, -1)
        self.assertNotEqual(menu_at, -1)
        self.assertNotEqual(coord_at, -1)
        self.assertNotEqual(api_at, -1)
        self.assertNotEqual(app_at, -1)
        self.assertLess(nav_at, ws_at)
        self.assertLess(ws_at, tc_at)
        self.assertLess(tc_at, tiling_at)
        self.assertLess(tiling_at, menu_at)
        self.assertLess(menu_at, app_at)
        self.assertLess(tiling_at, app_at)
        self.assertLess(ws_at, app_at)
        self.assertLess(coord_at, api_at)
        self.assertLess(api_at, app_at)
        self.assertTrue(os.path.isfile(_WS))
        self.assertTrue(os.path.isfile(_TILING))
        self.assertTrue(os.path.isfile(_RUNNER))
        self.assertTrue(os.path.isfile(_COORD))

    def test_tab_strip_markup(self):
        html = _read(_INDEX)
        self.assertIn('id="prks-workspace-tabs"', html)
        self.assertIn('class="prks-workspace-tabs"', html)
        self.assertIn('role="tablist"', html)
        self.assertIn('id="prks-workspace-tab-overflow"', html)
        self.assertIn('id="prks-workspace-new-tab"', html)
        self.assertIn('id="prks-workspace-tile-layout"', html)
        self.assertIn('id="prks-workspace-live"', html)
        self.assertIn("prks-workspace-tabs-shell", html)
        src = _read(_WS)
        self.assertIn("prks-workspace-tab__activate", src)
        self.assertIn("prks-workspace-tab__close", src)
        self.assertIn('role="tab"', src)

    def test_tiled_v1_state_contract(self):
        src = _read(_WS)
        tiling = _read(_TILING)
        self.assertIn("function createPrksWorkspaceTabs", src)
        self.assertIn("'stacked'", src)
        self.assertIn("'tiled'", src)
        self.assertIn("mainTabId", src)
        self.assertIn("focusedTabId", src)
        self.assertIn("secondaryTree", src)
        self.assertIn("type: 'leaf'", src)
        self.assertNotIn("type: 'split'", src)
        self.assertNotIn('type: "split"', src)
        self.assertNotIn("splitRatio", src)
        self.assertNotIn("localStorage", src)
        self.assertNotIn("sessionStorage", src)
        self.assertNotIn("indexedDB", src)
        self.assertIn("prksWorkspaceFocusTab", src)
        self.assertIn("prksWorkspaceMakeMain", src)
        self.assertIn("target: 'tile'", src)
        self.assertIn("prksWorkspaceHostForTab", src)
        self.assertIn("prksWorkspaceFindTabByRoute", src)
        self.assertIn("prksWorkspaceCloseOtherTabs", src)
        self.assertIn("prksWorkspaceCloseTabsToTheRight", src)
        self.assertIn("prks-workspace-tab__split", src)
        self.assertIn("syncTrailingTabStops", src)
        self.assertIn("Open split view", _read(_INDEX))
        refresh = src[src.find("function prksWorkspaceRefreshTabStatus") : src.find("function revealWorkspaceTab")]
        self.assertIn("updateTabOverflow()", refresh)
        kind = src[src.find("function tabStatusKind") : src.find("function statusLabel")]
        self.assertLess(kind.find("return 'error'"), kind.find("return 'saving'"))
        self.assertLess(kind.find("return 'saving'"), kind.find("return 'drafting'"))
        self.assertIn("prks-workspace-canvas", tiling)
        self.assertIn("observedCanvas", tiling)
        self.assertIn("prks-tile--main", tiling)
        self.assertIn("prks-tile--secondary", tiling)
        self.assertIn("prks-tile--focused", tiling)
        self.assertNotIn("type: 'split'", tiling)
        self.assertNotIn("aria-valuenow", tiling)
        self.assertNotIn("pointermove", tiling)
        self.assertNotIn("is-dragging", tiling)
        self.assertNotIn("localStorage", tiling)
        menu = _read(os.path.join(_FRONTEND, "js", "workspace-tab-menu.js"))
        self.assertNotIn("type: 'split'", menu)
        self.assertIn("Open in split view", menu)
        self.assertIn("Make main", menu)
        self.assertIn("menuItems.push(split)", menu)
        self.assertIn("menuItems.push(close)", menu)
        self.assertIn("role', 'menuitem'", menu)
        nav = _read(_NAV)
        self.assertIn("function prksRouteSupportsTile", nav)
        self.assertIn("function prksPublishMainShell", nav)

    def test_navigate_target_contract(self):
        src = _read(_WS)
        self.assertIn("new-tab", src)
        self.assertIn("'tile'", src)
        self.assertIn("activate", src)
        nav = _read(_NAV)
        self.assertIn("prksWorkspaceNavigate", nav)
        self.assertIn("prksResolvedRouteTitle", nav)
        self.assertIn("prksRouteTabIcon", nav)
        self.assertIn("prksRouteSupportsTile", nav)

    def test_old_browser_new_tab_helpers_removed(self):
        leftovers = []
        for path in _frontend_js_files():
            src = _read(path)
            if "prksOpenHashInNewTab" in src or "prksMaybeOpenHashInNewTab" in src:
                leftovers.append(os.path.relpath(path, _PROJECT_DIR))
        self.assertEqual(leftovers, [], leftovers)
        app = _read(_APP)
        self.assertNotIn("window.open(", app)

    def test_no_window_open_blank_for_internal_hash(self):
        leftovers = []
        for path in _frontend_js_files():
            src = _read(path)
            if _OPEN_BLANK_RE.search(src):
                leftovers.append(os.path.relpath(path, _PROJECT_DIR))
        self.assertEqual(leftovers, [], leftovers)

    def test_feature_files_do_not_assign_location_hash(self):
        leftovers = []
        for path in _frontend_js_files():
            if path in _LOW_LEVEL_HASH_FILES:
                continue
            src = _read(path)
            for match in _HASH_ASSIGN_RE.finditer(src):
                line = _line_at(src, match.start()).strip()
                leftovers.append("%s: %s" % (os.path.relpath(path, _PROJECT_DIR), line))
        self.assertEqual(
            leftovers,
            [],
            "feature files must not assign location.hash:\n" + "\n".join(leftovers),
        )

    def test_low_level_hash_writes_remain_narrow(self):
        app = _read(_APP)
        self.assertIn("prksCanLeaveCurrentRoute", app)
        self.assertIn("prksHasPendingWorkAnnotationSync", app)
        self.assertIn("workspaceSwitch", app)
        ws = _read(_WS)
        self.assertIn("createPrksWorkspaceTabs", ws)
        works = _read(os.path.join(_FRONTEND, "js", "components", "works.js"))
        self.assertIn("prksFlushPendingWorkResearchNotes", works)
        self.assertIn("work-research-notes:", works)
        self.assertIn("latestSaveToken", works)
        self.assertIn("saveSequence", works)

    def test_docs_and_design_contract(self):
        design = _read(_DESIGN)
        self.assertIn("Local / content tabs versus workspace tabs", design)
        self.assertIn("main/master tile owns the left column", design)
        self.assertIn("Main is not the same state as focus", design)
        self.assertIn("Never flash Home", design)
        self.assertIn(".prks-workspace-tabs", design)
        self.assertIn("stacked", design.lower())
        agents = _read(_AGENTS)
        self.assertIn("prksNavigate", agents)
        self.assertIn("Parked tabs", agents)
        self.assertIn("Secondary", agents)
        readme = _read(_README)
        self.assertIn("Workspace tabs", readme)
        self.assertIn("stacked", readme.lower())
        self.assertIn("Open in split view", readme)
        self.assertIn("Split view", readme)
        self.assertIn("Close other tabs", readme)
        self.assertIn("Close tabs to the right", readme)
        self.assertIn("overflow", readme.lower())
        self.assertIn("Shift+F10", readme)
        self.assertIn("drafting", readme.lower())

    def test_node_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for workspace tab tests")
        proc = subprocess.run(
            [node, _RUNNER],
            cwd=_PROJECT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertIn("passed", proc.stdout)
        self.assertIn(", 0 failed", proc.stdout)
        self.assertNotIn("FAIL  ", proc.stdout)

    def test_tiling_observer_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for workspace tiling tests")
        runner = os.path.join(_PROJECT_DIR, "tests", "browser", "run_workspace_tiling_selftest.js")
        proc = subprocess.run(
            [node, runner],
            cwd=_PROJECT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertNotIn("FAIL  ", proc.stdout)


if __name__ == "__main__":
    unittest.main()
