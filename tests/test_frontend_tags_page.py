"""Tags vocabulary page: the coordinator loads used tags and Vue paints them."""
import os
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP = os.path.join(_PROJECT_DIR, "frontend", "js", "app.js")
_TAGS = os.path.join(_PROJECT_DIR, "frontend", "js", "components", "tags.js")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendTagsPageTests(unittest.TestCase):
    def test_route_loads_used_tags_and_paints_vue(self):
        app = _read(_APP)
        tags_at = app.index("case 'tags':")
        publishers_at = app.index("case 'publishers':")
        types_at = app.index("case 'types':")
        processing_at = app.index("case 'processing-files':")
        search_at = app.index("case 'search':")
        tags_body = app[tags_at:publishers_at]
        self.assertIn("fetchTags({ used: true, signal: routeSignal })", tags_body)
        self.assertIn("prksPresentVueTags", tags_body)
        self.assertNotIn("renderTagsPage", tags_body)
        self.assertIn("async function prksReloadTagsVocabulary(", app)
        self.assertIn("prksVueDismissTags", app)
        publishers = app[publishers_at:types_at]
        self.assertIn("renderPublishersPage", publishers)
        self.assertNotIn("prksPresentVue", publishers)
        processing = app[processing_at:search_at]
        self.assertIn("prksRenderProcessingFilesPageWithFetch", processing)
        self.assertNotIn("prksPresentVue", processing)

    def test_legacy_module_keeps_canonical_writes_only(self):
        tags = _read(_TAGS)
        self.assertNotIn("renderTagsPage", tags)
        self.assertNotIn("prksTagsPageCtx", tags)
        self.assertIn("prksDeleteTagDurably(", tags)
        self.assertIn("mergeTags(", tags)
        self.assertIn("prksOfflineGuardMutation", tags)
        self.assertIn("prksOfflineMarkTagsChanged", tags)
        self.assertNotIn("'/api/tags/merge'", tags)
        self.assertIn("window.prksCloseTagsAliasModal", tags)
        self.assertIn("window.prksCloseTagsMergeModal", tags)
        self.assertIn("prksVueCloseTagsAliasModal(modal)", tags)
        self.assertIn("prksVueCloseTagsMergeModal(modal)", tags)

    def test_reload_keeps_the_list_when_the_tag_read_fails(self):
        app = _read(_APP)
        start = app.index("async function prksReloadTagsVocabulary(")
        end = app.index("window.prksReloadTagsVocabulary = prksReloadTagsVocabulary;")
        reload = app[start:end]
        self.assertIn("errorOwner: errorOwner", reload)
        self.assertIn("prksConsumeApiError(errorOwner)", reload)
        self.assertIn("prksVueReportTagsRefreshFailure", reload)
        self.assertLess(reload.index("if (failure)"), reload.index("prksPresentVueTags"))
        ui = _read(os.path.join(_PROJECT_DIR, "frontend", "js", "ui.js"))
        closer = ui[ui.index("function prksCloseStandalonePageModal"):ui.index("function prksDismissModalInnerEscapeLayer")]
        self.assertIn("closer(modal)", closer)

    def test_tags_dialogs_do_not_raise_the_global_alert(self):
        vue = _read(os.path.join(
            _PROJECT_DIR, "frontend-app", "src", "features", "tags", "TagsRoute.vue"))
        self.assertNotIn("prksAlertMessage", vue)
        self.assertIn("data-tags-alias-add-error", vue)
        self.assertIn("data-tags-alias-remove-error", vue)
        self.assertIn("data-tags-alias-delete-error", vue)
        self.assertIn("data-tags-merge-error", vue)
        self.assertIn('busy-label="Deleting…"', vue)
        self.assertIn('busy-label="Merging…"', vue)
        self.assertIn("Removing…", vue)
        self.assertIn('variant="danger"', vue)
        policy = _read(os.path.join(_PROJECT_DIR, "tests", "e2e", "policy.py"))
        rule = policy[policy.index('"name": "tags-vue"'):policy.index('"name": "concepts-vue"')]
        self.assertIn('"features": ("folders",)', rule)
        self.assertIn("test_folders_offline", rule)
        self.assertNotIn("Types slice", rule)


if __name__ == "__main__":
    unittest.main()
