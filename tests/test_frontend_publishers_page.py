"""Publishers page: Vue reads and writes through the typed client and TanStack Query."""
import os
import re
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP = os.path.join(_PROJECT_DIR, "frontend", "js", "app.js")
_FEATURE = os.path.join(_PROJECT_DIR, "frontend-app", "src", "features", "publishers")


def _read(*parts: str) -> str:
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


class FrontendPublishersPageTests(unittest.TestCase):
    def test_route_keeps_the_offline_gate_and_does_not_fetch(self):
        app = _read(_APP)
        body = app[app.index("case 'publishers':"):app.index("case 'types':")]
        self.assertIn("Publishers require a connection", body)
        self.assertLess(
            body.index("prksOfflineRenderUnavailable"),
            body.index("prksPresentVueRoute(ctx, contentDiv, 'publishers'"),
        )
        self.assertNotIn("await ", body)
        self.assertNotIn("publishers:", body)
        self.assertIn("window.prksVueDismissRoute(ctx)", app)

    def test_classic_publishers_plumbing_is_gone(self):
        self.assertFalse(
            os.path.exists(os.path.join(_PROJECT_DIR, "frontend", "js", "components", "publishers.js"))
        )
        gone = (
            "fetchPublishersInUse",
            "prksReloadPublishersPage",
            "prksVueReportPublishersRefreshFailure",
            "prksPublishersCreate",
            "prksPublishersAddAlias",
            "prksPublishersRemoveAlias",
            "prksPublishersDelete",
            "prksClosePublishersAliasModal",
            "components/publishers.js",
        )
        sources = {
            "app.js": _read(_PROJECT_DIR, "frontend", "js", "app.js"),
            "api.js": _read(_PROJECT_DIR, "frontend", "js", "api.js"),
            "ui.js": _read(_PROJECT_DIR, "frontend", "js", "ui.js"),
            "index.html": _read(_PROJECT_DIR, "frontend", "index.html"),
            "sw.js": _read(_PROJECT_DIR, "frontend", "sw.js"),
            "env.d.ts": _read(_PROJECT_DIR, "frontend-app", "env.d.ts"),
        }
        for name in os.listdir(_FEATURE):
            sources[name] = _read(_FEATURE, name)
        for source, text in sources.items():
            for token in gone:
                self.assertNotIn(token, text, f"{token} in {source}")
        self.assertIn(
            "'publishers-page-alias-modal': 'prksVueClosePublishersAliasModal'",
            sources["ui.js"],
        )

    def test_reads_and_writes_go_through_the_typed_client_and_one_query_key(self):
        catalog = _read(_FEATURE, "usePublishersCatalog.ts")
        intents = _read(_FEATURE, "intents.ts")
        self.assertIn("prksQueryKeys.publishers.inUse()", catalog)
        self.assertIn("listPublishersInUse(signal)", catalog)
        self.assertIn("refetchOnMount: 'always'", catalog)
        self.assertIn("prksQueryKeys.publishers.all()", intents)
        self.assertIn("new MutationObserver(queryClient", intents)
        self.assertIn("prksOfflineGuardMutation", intents)
        for name in os.listdir(_FEATURE):
            text = _read(_FEATURE, name)
            self.assertNotRegex(text, r"\bfetch\(", name)
            self.assertNotIn("prksRequest", text, name)
            self.assertIsNone(re.search(r"queryKey: \[", text), name)

    def test_publishers_actions_stay_local_and_map_to_the_publishers_e2e(self):
        vue = _read(_FEATURE, "PublishersRoute.vue")
        self.assertNotIn("prksAlertMessage", vue)
        for marker in (
            "data-publishers-loading",
            "data-publishers-load-error",
            "data-publishers-refresh-error",
            "data-publishers-create-error",
            "data-publishers-alias-add-error",
            "data-publishers-alias-remove-error",
            "data-publishers-delete-error",
        ):
            self.assertIn(marker, vue)
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
        policy = _read(_PROJECT_DIR, "tests", "e2e", "policy.py")
        rule = policy[policy.index('"name": "publishers-vue"'):policy.index('"name": "processing-vue"')]
        self.assertIn('"features": ("publishers",)', rule)
        self.assertIn("test_publishers_route_surface", rule)
        self.assertNotIn('"features": ("browse",)', rule)


if __name__ == "__main__":
    unittest.main()
