"""Structural + Node regressions for Saved Views."""
import os
import shutil
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_INDEX = os.path.join(_FRONTEND, "index.html")
_APP = os.path.join(_FRONTEND, "js", "app.js")
_NAV = os.path.join(_FRONTEND, "js", "navigation.js")
_SV = os.path.join(_FRONTEND, "js", "saved-views.js")
_SEARCH = os.path.join(_FRONTEND, "js", "components", "search.js")
_API = os.path.join(_FRONTEND, "js", "api.js")
_WIKI_USER = os.path.join(_PROJECT_DIR, "docs", "wiki", "User-Guide.md")
_AGENTS = os.path.join(_PROJECT_DIR, "AGENTS.md")
_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_saved_views_selftest.js")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendSavedViewsTests(unittest.TestCase):
    def test_module_and_chrome(self):
        html = _read(_INDEX)
        self.assertIn('src="/js/saved-views.js"', html)
        self.assertIn('href="#/views"', html)
        self.assertIn("Saved Views", html)
        self.assertIn('id="saved-view-modal"', html)
        self.assertLess(html.find('href="#/recent"'), html.find('href="#/views"'))
        self.assertEqual(html.count('href="#/views"'), 1)
        app = _read(_APP)
        self.assertIn("saved-views", app)
        self.assertIn("saved-view-detail", app)
        self.assertIn("fetchSearch(", app)
        self.assertNotIn("/api/saved-views/:id/results", app)
        nav = _read(_NAV)
        self.assertIn("'saved-views'", nav)
        self.assertIn("'saved-view-detail'", nav)
        self.assertIn("#/views", nav)
        search = _read(_SEARCH)
        self.assertIn("prks-save-view-btn", search)
        self.assertIn("Save View", search)
        api = _read(_API)
        self.assertIn("function fetchSavedViews", api)
        self.assertIn("function createSavedView", api)
        self.assertIn("saved-views.fetch", api)
        src = _read(_SV)
        self.assertIn("prksSearchDefinitionFromRoute", src)
        self.assertIn("prksSearchHashFromDefinition", src)
        self.assertNotIn("saved_view_works", src)
        self.assertNotIn(":id/results", src)

    def test_docs(self):
        wiki = _read(_WIKI_USER)
        agents = _read(_AGENTS)
        self.assertIn("## Saved Views", wiki)
        self.assertIn("Save View", wiki)
        self.assertIn("search definition", wiki.lower())
        self.assertIn("Saved Views store search definitions", agents)
        self.assertIn("never cached work membership", agents)

    def test_node_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for Saved Views tests")
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
