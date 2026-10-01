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
_SEARCH = os.path.join(_PROJECT_DIR, "frontend-app", "src", "features", "search", "SearchRoute.vue")
_SEARCH_INTENTS = os.path.join(_PROJECT_DIR, "frontend-app", "src", "features", "search", "intents.ts")
_SV_DETAIL = os.path.join(_PROJECT_DIR, "frontend-app", "src", "features", "saved-views", "SavedViewDetailRoute.vue")
_SV_INDEX = os.path.join(_PROJECT_DIR, "frontend-app", "src", "features", "saved-views", "SavedViewsIndexRoute.vue")
_SV_INTENTS = os.path.join(_PROJECT_DIR, "frontend-app", "src", "features", "saved-views", "intents.ts")
_SV_PROJECTION = os.path.join(_PROJECT_DIR, "frontend-app", "src", "features", "saved-views", "projection.ts")
_TAB_CONTEXT = os.path.join(_FRONTEND, "js", "tab-context.js")
_API = os.path.join(_FRONTEND, "js", "api.js")
_WIKI_USER = os.path.join(_PROJECT_DIR, "docs", "wiki", "User-Guide.md")
_AGENTS = os.path.join(_PROJECT_DIR, "frontend", "AGENTS.md")
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

    def test_search_and_saved_view_detail_share_one_result_read(self):
        """Search and Saved View detail run one coordinator read and one Vue
        result collection. The replaced painters are gone."""
        html = _read(_INDEX)
        self.assertNotIn("/js/components/search.js", html)
        self.assertFalse(os.path.exists(os.path.join(_FRONTEND, "js", "components", "search.js")))
        app = _read(_APP)
        self.assertIn("async function prksEffectiveSearchResults(", app)
        for marker in ("case 'search': {", "case 'saved-view-detail': {"):
            at = app.index(marker)
            body = app[at: at + 2400]
            self.assertIn("prksEffectiveSearchResults(", body, marker)
            self.assertNotIn("await fetchSearch(", body, marker)
        self.assertIn("ctx.setEntity('savedView'", app)
        self.assertNotIn("currentSavedView", app)
        self.assertNotIn("currentSavedView", _read(_TAB_CONTEXT))
        for name in ("renderSearch(", "renderSavedViewDetail", "renderSavedViewNotFound"):
            self.assertNotIn(name, app)
        src = _read(_SV)
        for name in ("function renderSavedViewDetail", "function renderSavedViewNotFound",
                     "prksSearchResultCardsHtml", "__prksCurrentSavedView"):
            self.assertNotIn(name, src)
        self.assertIn("function prksDeleteSavedViewFromDetail", src)
        detail = _read(_SV_DETAIL)
        search = _read(_SEARCH)
        for vue in (detail, search):
            self.assertIn("SearchResultsCollection", vue)
        self.assertIn("prksDeleteSavedViewFromDetail", _read(_SV_INTENTS))
        self.assertIn("prksSearchHashFromDefinition", _read(_SEARCH_INTENTS))
        detail = _read(_SV_DETAIL)
        self.assertIn("Delete Saved View", detail)
        self.assertIn('variant="danger"', detail)
        self.assertIn("Deleting…", detail)
        self.assertIn("work-html-slot", detail)
        self.assertNotIn('style="display: contents"', detail)
        self.assertNotIn('style="display: contents"', search)
        self.assertIn("work-html-slot", search)
        self.assertIn("confirmLabel: 'Delete Saved View'", src)
        policy = _read(os.path.join(_PROJECT_DIR, "tests", "e2e", "policy.py"))
        self.assertNotIn("frontend/js/components/search.js", policy)

    def test_saved_views_index_is_a_vue_surface(self):
        """The index is the Vue list. The replaced painter and its edit/delete
        helpers are gone. Delete refreshes in place."""
        app = _read(_APP)
        self.assertIn("function prksPresentVueSavedViewsIndex(", app)
        case_at = app.index("case 'saved-views': {")
        case_body = app[case_at: case_at + 1200]
        self.assertIn("fetchSavedViews(", case_body)
        self.assertIn("prksPresentVueSavedViewsIndex(", case_body)
        self.assertNotIn("renderSavedViewsIndex", app)
        src = _read(_SV)
        for name in (
            "function renderSavedViewsIndex",
            "function bindIndexActions",
            "function openEditById",
            "function confirmDelete",
            "prksOpenSavedViewIndexEdit",
        ):
            self.assertNotIn(name, src)
        self.assertIn("function prksDeleteSavedViewFromIndex", src)
        self.assertNotIn("prksCurrentCanonicalHash", src)
        self.assertIn("prksNavigate('#/views'", src)
        index = _read(_SV_INDEX)
        self.assertIn("saved-views-page", index)
        self.assertIn("SAVED_VIEWS_EMPTY", index)
        projection = _read(_SV_PROJECTION)
        self.assertIn("No Saved Views yet.", projection)
        self.assertIn("removeFromIndex", _read(_SV_INTENTS))
        self.assertIn("prksSearchSummaryText", projection)
        agents = _read(_AGENTS)
        self.assertIn("prksDeleteSavedViewFromIndex", agents)
        self.assertIn(
            "`renderSavedViewsIndex`, `bindIndexActions`, `openEditById`, and `confirmDelete` are removed.",
            agents,
        )

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
