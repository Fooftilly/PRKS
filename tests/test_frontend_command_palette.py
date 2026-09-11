"""Structural + Node regressions for the command palette and simplified chrome."""
import os
import shutil
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_INDEX = os.path.join(_FRONTEND, "index.html")
_APP = os.path.join(_FRONTEND, "js", "app.js")
_NAV = os.path.join(_FRONTEND, "js", "navigation.js")
_PALETTE = os.path.join(_FRONTEND, "js", "command-palette.js")
_CSS = os.path.join(_FRONTEND, "css", "style.css")
_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_command_palette_selftest.js")
_README = os.path.join(_PROJECT_DIR, "README.md")
_AGENTS = os.path.join(_PROJECT_DIR, "AGENTS.md")
_SCHEMA = os.path.join(_PROJECT_DIR, "backend", "db_migrations.py")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendCommandPaletteTests(unittest.TestCase):
    def test_palette_module_loads_before_app(self):
        html = _read(_INDEX)
        pal_at = html.find('src="/js/command-palette.js"')
        app_at = html.find('src="/js/app.js"')
        sel_at = html.find('src="/js/work-selection.js"')
        sv_at = html.find('src="/js/saved-views.js"')
        self.assertNotEqual(pal_at, -1)
        self.assertNotEqual(app_at, -1)
        self.assertNotEqual(sv_at, -1)
        self.assertLess(sel_at, sv_at)
        self.assertLess(sv_at, pal_at)
        self.assertLess(pal_at, app_at)
        self.assertTrue(os.path.isfile(_PALETTE))

    def test_old_sidebar_search_removed(self):
        html = _read(_INDEX)
        app = _read(_APP)
        self.assertNotIn('id="global-search"', html)
        self.assertNotIn("id=\"prks-global-search-mode\"", html)
        self.assertNotIn("id=\"prks-global-search-run\"", html)
        self.assertNotIn("id=\"prks-global-search\"", html)
        self.assertNotIn("function initSearch(", app)
        self.assertNotIn("prks.search.mode", app)

    def test_ribbon_is_launcher_new_file_and_create_menu(self):
        html = _read(_INDEX)
        self.assertIn('id="prks-command-palette-launch"', html)
        self.assertIn("Search or jump", html)
        self.assertIn('id="prks-ribbon-create"', html)
        self.assertIn('id="prks-ribbon-new-file"', html)
        self.assertIn("openModal('work-modal')", html)
        self.assertIn('id="prks-ribbon-new-more"', html)
        self.assertIn('aria-haspopup="menu"', html)
        self.assertNotIn("New…", html.split("top-ribbon__center", 1)[1].split("top-ribbon__right", 1)[0])
        center = html.split('top-ribbon__center', 1)[1].split("top-ribbon__right", 1)[0]
        self.assertIn("New File", center)
        self.assertIn('id="prks-ribbon-new-file"', center)
        self.assertIn('id="prks-ribbon-new-more"', center)
        self.assertEqual(center.count('id="prks-ribbon-new-file"'), 1)
        self.assertEqual(center.count('id="prks-ribbon-new-more"'), 1)
        self.assertNotIn("Link Person to Work", center)
        self.assertNotIn("openModal('folder-modal')", center)
        self.assertNotIn("openModal('person-modal')", center)
        self.assertNotIn("openModal('group-modal')", center)
        self.assertIn('id="prks-mobile-nav-btn"', html)
        self.assertIn('id="prks-mobile-details-btn"', html)
        self.assertIn('id="role-modal"', html)
        self.assertIn("work-link-person-btn", _read(os.path.join(_FRONTEND, "js", "ui.js")))
        self.assertIn('src="/js/ribbon-create.js"', html)
        pal_at = html.find('src="/js/command-palette.js"')
        ribbon_at = html.find('src="/js/ribbon-create.js"')
        self.assertLess(ribbon_at, pal_at)

    def test_people_and_progress_disclosures(self):
        html = _read(_INDEX)
        nav = _read(_NAV)
        src = _read(_PALETTE)
        self.assertIn('data-nav-disclosure="people"', html)
        self.assertIn('data-nav-disclosure-toggle="people"', html)
        self.assertIn('id="prks-nav-people-children"', html)
        self.assertIn('aria-controls="prks-nav-people-children"', html)
        self.assertIn('data-nav-disclosure="progress"', html)
        self.assertIn('id="prks-nav-progress-children"', html)
        self.assertIn("prks.nav.peopleExpanded", nav)
        self.assertIn("prks.nav.progressExpanded", nav)
        self.assertIn("prks.nav.researchExpanded", nav)
        self.assertIn('data-nav-disclosure="research"', html)
        self.assertIn('id="prks-nav-research-children"', html)
        self.assertIn("navigate-concepts", src)
        self.assertIn("prksSyncNavDisclosures", nav)
        self.assertIn("prksInitNavDisclosures", nav)
        self.assertIn("Folders</span>", html)
        self.assertIn("Organize", html)
        self.assertNotIn("All Folders", html)
        self.assertNotIn("All tags", html)

    def test_palette_allowlist_and_no_backend_api(self):
        src = _read(_PALETTE)
        self.assertIn("prksNavigate", src)
        self.assertIn("prksOpenCommandPalette", src)
        self.assertIn("Open in split view", src)
        self.assertIn("prksWorkspaceTileTab", src)
        self.assertIn("prksWorkspaceFindTabByRoute", src)
        self.assertNotIn("eval(", src)
        self.assertNotIn("new Function", src)
        self.assertNotIn("window[", src)
        self.assertNotIn("/api/command-palette", src)
        self.assertNotIn("/api/quick-open", src)
        self.assertNotIn("Fuse.js", src)
        self.assertNotIn("MiniSearch", src)
        self.assertNotIn("localStorage.setItem", src)
        self.assertIn("URLSearchParams", src)
        self.assertNotIn("prksNavigate(hash, { replace: true })", src)
        self.assertNotIn("prksNavigate(cmd.hash, { replace: true })", src)

    def test_no_schema_bump(self):
        schema = _read(_SCHEMA)
        self.assertIn("LATEST_SCHEMA_VERSION = 14", schema)

    def test_docs(self):
        readme = _read(_README)
        agents = _read(_AGENTS)
        self.assertIn("## Command palette", readme)
        self.assertIn("Ctrl+K", readme)
        self.assertIn("command palette", agents.lower())
        self.assertIn("prksNavigate()", agents)
        self.assertIn("ephemeral", agents.lower())

    def test_css_has_palette_and_disclosure(self):
        css = _read(_CSS)
        self.assertIn("#prks-command-palette", css)
        self.assertIn(".nav-disclosure__toggle", css)
        self.assertIn(".prks-palette-launch", css)
        self.assertNotIn(".prks-global-search__chip", css)

    def test_node_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for command-palette tests")
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
