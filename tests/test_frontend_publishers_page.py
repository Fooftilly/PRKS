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
        self.assertIn("window.prksVueDismissRoute(ctx)", app)
        processing = app[processing_at:search_at]
        self.assertIn("prksLoadProcessingInbox(routeSignal)", processing)
        self.assertIn("prksPresentVueProcessing", processing)
        self.assertNotIn("prksRenderProcessingFilesPageWithFetch", processing)

    def test_reload_keeps_the_list_when_the_publisher_read_fails(self):
        app = _read(_APP)
        start = app.index("async function prksReloadPublishersPage(")
        end = app.index("window.prksReloadPublishersPage = prksReloadPublishersPage;")
        reload = app[start:end]
        self.assertIn("errorOwner: errorOwner", reload)
        self.assertIn("prksConsumeApiError(errorOwner)", reload)
        self.assertIn("prksVueReportPublishersRefreshFailure", reload)
        self.assertNotIn("prksAlertMessage", reload)
        self.assertLess(reload.index("if (failure)"), reload.index("prksPresentVuePublishers"))

    def test_publishers_actions_stay_local_and_map_to_the_publishers_e2e(self):
        vue = _read(os.path.join(
            _PROJECT_DIR, "frontend-app", "src", "features", "publishers", "PublishersRoute.vue"))
        self.assertNotIn("prksAlertMessage", vue)
        self.assertIn("data-publishers-refresh-error", vue)
        self.assertIn("data-publishers-create-error", vue)
        self.assertIn("data-publishers-alias-add-error", vue)
        self.assertIn("data-publishers-alias-remove-error", vue)
        self.assertIn("data-publishers-delete-error", vue)
        self.assertIn('busy-label="Adding…"', vue)
        self.assertIn('busy-label="Deleting…"', vue)
        self.assertIn("Removing…", vue)
        self.assertIn('variant="danger"', vue)
        name = vue[vue.index('class="publishers-page__list-main"'):vue.index("publishers-page__list-actions")]
        self.assertIn('role="button"', name)
        self.assertIn(":data-prks-route", name)
        self.assertIn('data-prks-middleclick-nav="1"', name)
        card = vue[vue.index('class="project-card publishers-page__list-item"'):vue.index('class="publishers-page__list-main"')]
        self.assertNotIn("data-prks-route", card)
        policy = _read(os.path.join(_PROJECT_DIR, "tests", "e2e", "policy.py"))
        rule = policy[policy.index('"name": "publishers-vue"'):policy.index('"name": "processing-vue"')]
        self.assertIn('"features": ("publishers",)', rule)
        self.assertIn("test_publishers_route_surface", rule)
        self.assertNotIn('"features": ("browse",)', rule)

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
