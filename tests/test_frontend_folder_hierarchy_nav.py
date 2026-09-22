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

    def test_css_exposes_compact_switcher(self):
        css = _read(_CSS)
        for cls in (
            ".prks-folder-nav__trigger",
            ".prks-folder-nav__panel",
            ".prks-folder-nav__option",
            ".prks-folder-nav__option.is-current",
        ):
            self.assertIn(cls, css)
        # No permanent sidebar tree for this feature.
        self.assertNotIn(".prks-folder-nav__sidebar", css)

    def test_user_guide_mentions_switcher(self):
        wiki = _read(_WIKI)
        self.assertIn("folder navigation", wiki.lower())

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
