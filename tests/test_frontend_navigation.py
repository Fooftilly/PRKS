"""Structural + Node regressions for hash-route navigation."""
import os
import shutil
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_INDEX = os.path.join(_FRONTEND, "index.html")
_NAV = os.path.join(_FRONTEND, "js", "navigation.js")
_APP = os.path.join(_FRONTEND, "js", "app.js")
_SELFTEST = os.path.join(_PROJECT_DIR, "tests", "browser", "navigation_selftest.js")
_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_navigation_selftest.js")
_FIXTURE = os.path.join(_PROJECT_DIR, "tests", "browser", "navigation.html")
_PEOPLE = os.path.join(_FRONTEND, "js", "components", "people.js")
_FOLDERS = os.path.join(_FRONTEND, "js", "components", "folders.js")
_GROUPS = os.path.join(_FRONTEND, "js", "components", "people-groups.js")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendNavigationTests(unittest.TestCase):
    def test_navigation_module_exists_and_loads_before_app(self):
        html = _read(_INDEX)
        nav_at = html.find('src="/js/navigation.js"')
        app_at = html.find('src="/js/app.js"')
        self.assertNotEqual(nav_at, -1)
        self.assertNotEqual(app_at, -1)
        self.assertLess(nav_at, app_at)
        self.assertTrue(os.path.isfile(_NAV))
        src = _read(_NAV)
        self.assertIn("function prksParseRoute", src)
        self.assertIn("function prksNavigate", src)
        self.assertIn("function prksSyncSidebarActive", src)
        self.assertIn("prks.routeStates.v1", src)

    def test_handle_route_uses_parser_not_hash_split_dispatch(self):
        app = _read(_APP)
        self.assertIn("prksParseRoute(", app)
        self.assertIn("switch (route.name)", app)
        self.assertIn("prksHasPendingWorkAnnotationSync", app)
        self.assertIn("prksMaybeFlushPdfLastPageOnRouteChange", app)
        self.assertIn("prksCaptureCurrentRouteState", app)
        self.assertIn("prksFinishRouteRender", app)
        self.assertNotIn("hash.split('/')[3]", app)
        self.assertNotIn("hash.startsWith('#/people/groups/')", app)

    def test_contextual_back_is_in_app_hash_not_history_back(self):
        nav = _read(_NAV)
        self.assertIn("prks-nav-back", nav)
        self.assertIn("Back to ", nav)
        self.assertNotIn("history.back()", nav)
        app = _read(_APP)
        self.assertIn("prksHasPendingWorkAnnotationSync", app)
        pending_at = app.find("prksHasPendingWorkAnnotationSync")
        finish_at = app.find("prksFinishRouteRender")
        self.assertNotEqual(pending_at, -1)
        self.assertLess(pending_at, finish_at)

    def test_component_filter_keys_remain(self):
        people = _read(_PEOPLE)
        folders = _read(_FOLDERS)
        groups = _read(_GROUPS)
        self.assertIn("PRKS_PEOPLE_LIBRARY_FILTER_KEY", people)
        self.assertIn("PRKS_FOLDER_LIBRARY_FILTER_KEY", folders)
        self.assertIn("PRKS_GROUP_LIBRARY_FILTER_KEY", groups)
        self.assertNotIn("PRKS_PEOPLE_LIBRARY_FILTER_KEY", _read(_NAV))

    def test_fixture_loads_production_navigation(self):
        html = _read(_FIXTURE)
        self.assertIn("/frontend/js/navigation.js", html)
        self.assertIn("/tests/browser/navigation_selftest.js", html)
        self.assertTrue(os.path.isfile(_SELFTEST))

    def test_node_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for navigation parser tests")
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
