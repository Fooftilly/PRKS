"""Work route projection stays a typed owner boundary around the legacy painter."""
import unittest
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[1]
_APP = _PROJECT / "frontend" / "js" / "app.js"
_WORKS = _PROJECT / "frontend" / "js" / "components" / "works.js"
_FEATURE = _PROJECT / "frontend-app" / "src" / "features" / "work"
_BUILT = _PROJECT / "frontend" / "js" / "work-route-projection.js"
_MAIN = _PROJECT / "frontend-app" / "src" / "main.ts"
_INDEX = _PROJECT / "frontend" / "index.html"
_SW = _PROJECT / "frontend" / "sw.js"

_FORBIDDEN = (
    "listOperations",
    "createPinia",
    "vue-router",
    "pinia",
    "@tanstack",
    "prksSync",
    "currentWorkProjection",
    "WorkStore",
    "fetch(",
)


def _work_case(app: str) -> str:
    at = app.index("case 'work': {")
    nxt = app.index("case 'concepts':", at)
    return app[at:nxt]


class WorkRouteProjectionContractTests(unittest.TestCase):
    def test_case_work_publishes_before_the_legacy_painter(self):
        body = _work_case(_APP.read_text(encoding="utf-8"))
        project_at = body.index("prksProjectWorkRoute(")
        publish_at = body.index("prksPublishWorkRouteProjection(ctx, generation, workProjection)")
        refuse_at = body.index("if (!publishedWork) return;")
        paint_at = body.index("await renderWorkDetails(ctx, work,")
        self.assertLess(project_at, publish_at)
        self.assertLess(publish_at, refuse_at)
        self.assertLess(refuse_at, paint_at)
        self.assertIn("sourcePrepared: true", body)
        self.assertIn("ctx.setEntity('work', null)", body)
        self.assertIn("prksWorkOpenShouldRecord(internalRefresh, offlineWork.value)", body)
        self.assertIn("void prksRecordWorkOpened(offlineWork.value)", body)
        self.assertNotIn("await prksRecordWorkOpened(", body)
        for helper in (
            "prksEffectiveWorkSync",
            "prksEffectiveWorkDetailRoles",
            "prksEffectiveWorkSource",
            "prksApplyPendingWorkFolders",
            "prksApplyPendingWorkPlaylists",
            "prksPendingWorkDetail",
        ):
            self.assertIn(helper, body, helper)
        self.assertLess(body.index("prksRefreshPendingWorkFolders"), body.index("await prksRefreshPendingWorkSources"))
        self.assertLess(body.index("await prksRefreshPendingWorkSources"), publish_at)
        self.assertIn("prksReplaceWorkRoutePlacement(ctx, generation, next)", body)
        self.assertIn("prksReplaceWorkRoutePlacement(ctx, generation, placed)", body)
        self.assertIn("if (!workUnsentEarly && !workDeletedEarly)", body)
        self.assertIn("lifecycle: workLifecycle", body)
        self.assertIn("workDeleted ? 'pending-delete'", body)
        self.assertNotIn("currentWorkProjection", body)
        self.assertNotIn(".listOperations(", body)

    def test_render_work_details_stays_the_painter(self):
        works = _WORKS.read_text(encoding="utf-8")
        self.assertIn("async function renderWorkDetails(ctx, work, requestCtx)", works)
        self.assertIn("const sourcePrepared = !!(requestCtx && requestCtx.sourcePrepared);", works)
        self.assertIn("if (typeof ctx.setEntity === 'function') ctx.setEntity('work', work);", works)
        self.assertIn("initPdfViewerForWork", works)
        app = _APP.read_text(encoding="utf-8")
        self.assertEqual(app.count("renderWorkDetails("), 1)

    def test_typed_boundary_does_not_own_durable_state_or_vue(self):
        sources = sorted(_FEATURE.glob("*.ts"))
        self.assertGreaterEqual(len(sources), 3)
        blob = "\n".join(path.read_text(encoding="utf-8") for path in sources)
        for token in _FORBIDDEN:
            self.assertNotIn(token, blob, token)
        main = _MAIN.read_text(encoding="utf-8")
        self.assertNotIn("features/work", main)
        built = _BUILT.read_text(encoding="utf-8")
        for name in (
            "prksProjectWorkRoute",
            "prksPublishWorkRouteProjection",
            "prksReplaceWorkRoutePlacement",
            "prksAdoptPaintedWorkRoute",
            "prksWorkOpenShouldRecord",
        ):
            self.assertIn(name, built, name)
        html = _INDEX.read_text(encoding="utf-8")
        script = html.index('src="/js/work-route-projection.js"')
        app_script = html.index('src="/js/app.js"')
        self.assertLess(script, app_script)
        sw = _SW.read_text(encoding="utf-8")
        precache = sw.index("'/js/work-route-projection.js'")
        app_precache = sw.index("'/js/app.js'")
        self.assertLess(precache, app_precache)

    def test_dependency_manifest_hashes_the_classic_script(self):
        from backend.dependency_gate import build_dependency_manifest

        manifest = build_dependency_manifest(_PROJECT)
        entry = next(item for item in manifest["dependencies"] if item["name"] == "prks-work-route-projection")
        self.assertEqual(entry["runtime_files"][0]["path"], "/js/work-route-projection.js")
        self.assertEqual(len(entry["runtime_files"][0]["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
