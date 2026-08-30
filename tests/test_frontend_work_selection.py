"""Structural + Node regressions for bulk work selection."""
import os
import shutil
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_INDEX = os.path.join(_FRONTEND, "index.html")
_CARDS = os.path.join(_FRONTEND, "js", "components", "work-cards.js")
_SEL = os.path.join(_FRONTEND, "js", "work-selection.js")
_APP = os.path.join(_FRONTEND, "js", "app.js")
_API = os.path.join(_FRONTEND, "js", "api.js")
_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_work_selection_selftest.js")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendWorkSelectionTests(unittest.TestCase):
    def test_card_emits_data_work_id_without_checkbox(self):
        src = _read(_CARDS)
        self.assertIn('data-work-id="${wid}"', src)
        self.assertIn("project-card--work-card", src)
        self.assertIn("onclick=\"window.location.hash='#/works/${wid}'\"", src)
        self.assertNotIn('type="checkbox"', src)
        self.assertNotIn("onclick.split", src)

    def test_selection_module_loads_before_app(self):
        html = _read(_INDEX)
        sel_at = html.find('src="/js/work-selection.js"')
        app_at = html.find('src="/js/app.js"')
        cards_at = html.find('src="/js/components/work-cards.js"')
        self.assertNotEqual(sel_at, -1)
        self.assertNotEqual(app_at, -1)
        self.assertLess(cards_at, sel_at)
        self.assertLess(sel_at, app_at)
        self.assertTrue(os.path.isfile(_SEL))

    def test_supported_routes_are_explicit(self):
        src = _read(_SEL)
        self.assertIn("'folder-detail'", src)
        self.assertIn("'recent'", src)
        self.assertIn("'type-detail'", src)
        self.assertIn("'progress'", src)
        self.assertIn("'search'", src)
        self.assertIn("PRKS_BULK_SUPPORTED_ROUTES", src)
        self.assertIn("prksParseRoute", src)
        self.assertNotIn("hash.startsWith", src)
        self.assertIn("prksCaptureCurrentRouteState", src)
        self.assertIn("replace: true", src)
        self.assertIn("Escape", src)
        self.assertIn('type = \'checkbox\'', src)
        self.assertIn("Select all visible", src)
        self.assertNotIn("Delete selected", src)
        self.assertNotIn("add_to_playlist", src)
        self.assertNotIn("delete_everything", src)
        self.assertIn("/api/works/bulk", src)
        self.assertNotIn("localStorage", src)
        self.assertNotIn("app_settings", src)

    def test_api_uses_single_bulk_endpoint(self):
        api = _read(_API)
        self.assertIn("/api/works/bulk", api)
        self.assertIn("function bulkUpdateWorks", api)
        app = _read(_APP)
        self.assertNotIn("/api/works/bulk", app)

    def test_node_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for work-selection tests")
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
