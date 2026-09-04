"""Structural + Node regressions for the resizable Main/Secondary divider."""
import os
import shutil
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_INDEX = os.path.join(_FRONTEND, "index.html")
_WS = os.path.join(_FRONTEND, "js", "workspace-tabs.js")
_TILING = os.path.join(_FRONTEND, "js", "workspace-tiling.js")
_SPLIT = os.path.join(_FRONTEND, "js", "workspace-split.js")
_CSS = os.path.join(_FRONTEND, "css", "style.css")
_DESIGN = os.path.join(_PROJECT_DIR, "DESIGN.md")
_AGENTS = os.path.join(_PROJECT_DIR, "AGENTS.md")
_README = os.path.join(_PROJECT_DIR, "README.md")
_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_workspace_split_selftest.js")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendWorkspaceSplitTests(unittest.TestCase):
    def test_module_exists_and_loads_after_tiling_before_menu(self):
        self.assertTrue(os.path.isfile(_SPLIT))
        self.assertTrue(os.path.isfile(_RUNNER))
        html = _read(_INDEX)
        tiling_at = html.find('src="/js/workspace-tiling.js"')
        split_at = html.find('src="/js/workspace-split.js"')
        menu_at = html.find('src="/js/workspace-tab-menu.js"')
        app_at = html.find('src="/js/app.js"')
        self.assertNotEqual(split_at, -1)
        self.assertLess(tiling_at, split_at)
        self.assertLess(split_at, menu_at)
        self.assertLess(split_at, app_at)

    def test_ratio_state_lives_in_workspace_tabs(self):
        src = _read(_WS)
        self.assertIn("mainSplitRatio", src)
        self.assertIn("DEFAULT_MAIN_SPLIT_RATIO", src)
        self.assertIn("0.58", src)
        self.assertIn("prksWorkspaceSetMainSplitRatio", src)
        self.assertIn("prksWorkspaceGetSplitRatio", src)
        self.assertIn("prksWorkspaceResetMainSplitRatio", src)
        # No persistence anywhere near the ratio implementation.
        self.assertNotIn("localStorage", src)
        self.assertNotIn("sessionStorage", src)
        self.assertNotIn("indexedDB", src)

    def test_tiling_module_delegates_and_stays_narrow(self):
        tiling = _read(_TILING)
        # Tiling calls into the one separator implementation instead of owning it.
        self.assertIn("prksWorkspaceSyncSplitSeparator", tiling)
        self.assertIn("prksWorkspaceReapplySplitRatio", tiling)
        self.assertIn("prks-splitter", tiling)
        # Pointer/keyboard/ARIA/persistence stay out of the tiling DOM module.
        self.assertNotIn("aria-valuenow", tiling)
        self.assertNotIn("pointermove", tiling)
        self.assertNotIn("is-dragging", tiling)
        self.assertNotIn("localStorage", tiling)

    def test_split_module_owns_pointer_keyboard_and_aria(self):
        split = _read(_SPLIT)
        self.assertIn("role", split)
        self.assertIn("separator", split)
        self.assertIn("aria-valuemin", split)
        self.assertIn("aria-valuemax", split)
        self.assertIn("aria-valuenow", split)
        self.assertIn("aria-valuetext", split)
        self.assertIn("setPointerCapture", split)
        self.assertIn("releasePointerCapture", split)
        self.assertIn("lostpointercapture", split)
        self.assertIn("ArrowLeft", split)
        self.assertIn("ArrowRight", split)
        self.assertIn("shiftKey", split)
        self.assertIn("'Home'", split)
        self.assertIn("'End'", split)
        self.assertIn("dblclick", split)
        self.assertIn("PRKS_SPLIT_MAIN_MIN_PX", split)
        self.assertIn("PRKS_SPLIT_SECONDARY_MIN_PX", split)
        self.assertIn("360", split)
        self.assertIn("320", split)
        self.assertNotIn("localStorage", split)
        self.assertNotIn("sessionStorage", split)
        self.assertNotIn("indexedDB", split)
        # Layout-only: never touches route/mount lifecycle.
        self.assertNotIn("prksMountTabContext", split)
        self.assertNotIn("prksUnmountTabContext", split)
        self.assertNotIn("prksWorkspaceRestoreFocus(", split)
        self.assertNotIn("handleRoute", split)

    def test_nested_separator_mechanics_shared_with_root(self):
        split = _read(_SPLIT)
        # Nested Secondary split dividers reuse the same pointer/keyboard engine as the root
        # divider (one separator implementation), parameterized by axis + node-local ratio.
        self.assertIn("prksWorkspaceSyncNestedSeparator", split)
        self.assertIn("prksWorkspaceReleaseNestedSeparator", split)
        self.assertIn("PRKS_NESTED_MIN_WIDTH_PX", split)
        self.assertIn("PRKS_NESTED_MIN_HEIGHT_PX", split)
        self.assertIn("beginPointerDrag", split)
        self.assertIn("prks-splitter--horizontal", split)
        self.assertIn("prksWorkspaceSetNestedSplitRatio", split)
        # Nested ratios are percentage-based (auto-reflow with ancestor resize, no JS needed);
        # only the root divider uses an exact px value.
        self.assertIn("--prks-split-first-size", split)
        self.assertIn("--prks-main-split-width", split)
        tiling = _read(_TILING)
        self.assertIn("renderTreeNode", tiling)
        self.assertIn("prks-workspace-split", tiling)
        self.assertIn("data-prks-split-id", tiling)
        self.assertIn("data-prks-secondary-root", tiling)
        self.assertIn("pruneStale", tiling)

    def test_nested_tiling_recursive_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for workspace tiling tests")
        runner = os.path.join(_PROJECT_DIR, "tests", "browser", "run_workspace_tiling_recursive_selftest.js")
        self.assertTrue(os.path.isfile(runner))
        proc = subprocess.run(
            [node, runner],
            cwd=_PROJECT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertIn(", 0 failed", proc.stdout)
        self.assertNotIn("FAIL  ", proc.stdout)

    def test_css_grid_and_min_hit_target(self):
        css = _read(_CSS)
        self.assertIn("--prks-workspace-separator-size", css)
        self.assertIn("--prks-main-split-width", css)
        self.assertIn(".prks-splitter", css)
        self.assertIn(".prks-splitter--vertical", css)
        self.assertIn("is-dragging", css)
        self.assertIn("prks-resizing-split", css)
        self.assertIn("col-resize", css)

    def test_docs_contract(self):
        design = _read(_DESIGN)
        self.assertIn("prks-splitter", design)
        self.assertIn("58/42", design)
        self.assertIn("drag", design.lower())
        agents = _read(_AGENTS)
        self.assertIn("workspace-owned", agents.lower())
        self.assertIn("mainSplitRatio", agents)
        self.assertIn("prks-splitter", agents)
        readme = _read(_README)
        self.assertIn("Resize split view", readme)
        self.assertIn("double-click", readme.lower())

    def test_node_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for workspace split tests")
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


if __name__ == "__main__":
    unittest.main()
