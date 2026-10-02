"""Publishers page: the coordinator loads publishers in use and Vue paints them."""
import os
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP = os.path.join(_PROJECT_DIR, "frontend", "js", "app.js")
_PUBLISHERS = os.path.join(_PROJECT_DIR, "frontend", "js", "components", "publishers.js")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendPublishersPageTests(unittest.TestCase):
    def test_route_stays_online_only_and_paints_vue(self):
        app = _read(_APP)
        publishers_at = app.index("case 'publishers':")
        types_at = app.index("case 'types':")
        processing_at = app.index("case 'processing-files':")
        search_at = app.index("case 'search':")
        body = app[publishers_at:types_at]
        self.assertIn("Publishers require a connection", body)
        self.assertIn("fetchPublishersInUse({ signal: routeSignal })", body)
        self.assertIn("prksPresentVuePublishers", body)
        self.assertNotIn("renderPublishersPage", body)
        self.assertIn("async function prksReloadPublishersPage(", app)
        self.assertIn("prksVueDismissPublishers", app)
        processing = app[processing_at:search_at]
        self.assertIn("prksRenderProcessingFilesPageWithFetch", processing)
        self.assertNotIn("prksPresentVue", processing)

    def test_legacy_module_keeps_online_writes_only(self):
        publishers = _read(_PUBLISHERS)
        self.assertNotIn("renderPublishersPage", publishers)
        self.assertNotIn("prksPublishersPageCtx", publishers)
        self.assertIn("prksOfflineGuardMutation", publishers)
        self.assertIn("'/api/publishers'", publishers)
        self.assertIn("window.prksClosePublishersAliasModal", publishers)
        self.assertNotIn("prksDeleteTagDurably", publishers)
        self.assertNotIn("prksMergeTagDurably", publishers)


if __name__ == "__main__":
    unittest.main()
