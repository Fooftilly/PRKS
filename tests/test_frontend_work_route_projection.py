"""Work route projection stays a typed owner boundary around the legacy painter."""
import json
import subprocess
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
        self.assertIn("lifecycle: 'pending-delete'", body)
        self.assertLess(body.index("lifecycle: 'pending-delete'"), body.index("prksOfflineRenderUnavailable(contentDiv, 'File not available offline')"))
        self.assertLess(body.index("if (!deletedProjection) return;"), body.index("prksOfflineRenderUnavailable(contentDiv, 'File not available offline')"))
        self.assertLess(body.index("prksApplyReadWorkOperations("), body.index("prksEffectiveWorkSync"))
        self.assertLess(body.index("prksApplyReadWorkOperations("), body.index("prksEffectiveWorkDetailRoles"))
        self.assertNotIn("prksSetPendingWorkMetadata(workOps)", body)
        self.assertNotIn("prksDurableOperationsOrNone()", body)
        self.assertIn("prksReadDurableOperations()", body)
        self.assertEqual(body.count("workOpsKnown = Array.isArray(workOps)"), 2)
        before_paint = body[:body.index("await renderWorkDetails")]
        self.assertEqual(before_paint.count("workOps = await workOpsPromise"), 2)
        self.assertNotIn("if (work && !workOps.length)", body)
        callback_at = body.index("void workOpsPromise.then(function (ops) {")
        callback = body[callback_at:body.index("                        });", callback_at)]
        self.assertNotIn("await ", callback)
        self.assertIn("if (stale()) return;", callback)
        self.assertIn("if (!Array.isArray(ops)) return;", callback)
        self.assertLess(callback.index("if (!Array.isArray(ops)) return;"), callback.index("lifecycle: 'pending-delete'"))
        self.assertLess(callback.index("lifecycle: 'pending-delete'"), callback.index("prksApplyReadWorkOperations"))
        self.assertNotIn("prksSetPendingWorkMetadata", callback)
        self.assertIn("work: base", callback)
        self.assertIn("effectiveWork: effective", callback)
        self.assertLess(callback.index("const refreshed = prksPublishWorkRouteProjection"), callback.index("updatePanelContent(panelTab)"))
        self.assertLess(callback.rindex("if (stale()) return;"), callback.index("updatePanelContent(panelTab)"))
        self.assertIn("ownedNow.ownerTabId !== ctx.tabId", callback)
        self.assertIn("const panelTab = (ctx.ui && ctx.ui.rightPanelTab) || 'details';", callback)
        self.assertNotIn("updatePanelContent('details')", callback)
        self.assertNotIn("panelTab === 'details'", callback)
        folder_at = body.index("void Promise.all([")
        folder = body[folder_at:body.index(".catch(function () { /* bookkeeping never breaks the page */ });", folder_at)]
        self.assertLess(folder.rindex("if (stale()) return;"), folder.index("updatePanelContent(panelTab)"))
        self.assertIn("still.ownerTabId !== ctx.tabId", folder)
        self.assertIn("const panelTab = (ctx.ui && ctx.ui.rightPanelTab) || 'details';", folder)
        self.assertIn("panelTab === 'details'", folder)
        self.assertNotIn("updatePanelContent('details')", folder)
        self.assertNotIn("updatePanelContent('details')", body)
        self.assertIn("if (work && workOpsKnown)", body)
        self.assertIn("effectiveWork: effectiveWork", body)
        self.assertNotIn("work = prksEffectiveWorkSync(work)", body)
        self.assertNotIn("work = prksEffectiveWorkDetailRoles(work)", body)
        reapply = body[body.index("prksReplaceWorkRoutePlacement(ctx, generation, placed)"):]
        self.assertIn("const panelTab = (ctx.ui && ctx.ui.rightPanelTab) || 'details';", reapply)
        self.assertIn("panelTab === 'details'", reapply)
        self.assertNotIn("updatePanelContent('details')", reapply)
        self.assertNotIn("prksHydratePendingWorkMetadata", body)
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
        publish = blob.split("export function publishWorkRouteProjection", 1)[1].split("export function replaceWorkRoutePlacement", 1)[0]
        self.assertNotIn("projectWorkRoute(", publish)
        self.assertIn("ctx.setEntity('work', projection.work)", publish)
        self.assertNotIn("effectiveWork", publish[publish.index("setEntity"):])
        main = _MAIN.read_text(encoding="utf-8")
        self.assertNotIn("features/work/projection", main)
        self.assertNotIn("features/work/browser-entry", main)
        self.assertNotIn("work-route-projection", main)
        self.assertIn("registerWorkPanelReadBridge", main)
        self.assertIn("features/work/panel-session", main)
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

    def test_fresh_context_projects_pending_metadata_and_roles(self):
        """A reload starts with empty overlay maps. Pending metadata and a
        pending role already in the durable read show up on the projection."""
        ops = [
            {
                "operation": "SET_WORK_METADATA_FIELD",
                "entity_type": "work",
                "entity_id": "w1",
                "status": "pending",
                "payload": {"field": "title", "value": "Pending title"},
            },
            {
                "operation": "ADD_WORK_PERSON_ROLE",
                "entity_type": "work",
                "entity_id": "w1",
                "status": "pending",
                "payload": {"person_id": "p1", "role_type": "Author", "credit_name": ""},
                "local_context": {
                    "person": {
                        "id": "p1",
                        "first_name": "Ada",
                        "last_name": "Lovelace",
                        "canonical_name": "Ada Lovelace",
                    }
                },
            },
        ]
        script = r"""
        const root = process.argv[1];
        require(root + '/frontend/js/date-format.js');
        require(root + '/frontend/js/work-metadata-state.js');
        require(root + '/frontend/js/work-role-state.js');
        require(root + '/frontend/js/person-metadata-state.js');
        require(root + '/frontend/js/work-route-projection.js');
        const ops = JSON.parse(process.argv[2]);
        const acknowledged = { id: 'w1', title: 'Acknowledged', roles: [] };
        if (globalThis.prksEffectiveWorkSync(acknowledged).title !== 'Acknowledged') {
            throw new Error('fresh metadata map was not empty');
        }
        if (globalThis.prksEffectiveWorkDetailRoles(acknowledged).roles.length !== 0) {
            throw new Error('fresh role map was not empty');
        }
        globalThis.prksSetPendingWorkMetadata(ops);
        globalThis.prksSetPendingWorkRoles(ops);
        globalThis.prksSetPendingPersonNames(ops);
        let effectiveWork = globalThis.prksEffectiveWorkSync(acknowledged);
        effectiveWork = globalThis.prksEffectiveWorkDetailRoles(effectiveWork);
        const ctx = {
            tabId: 'main',
            generation: 1,
            entity: null,
            resource: null,
            isCurrent(token) { return token === 1; },
            setEntity(_type, value) { this.entity = value; },
            getEntity() { return this.entity; },
            setResource(_name, value) { this.resource = value; },
            getResource() { return this.resource; },
        };
        const projection = globalThis.prksProjectWorkRoute({
            workId: 'w1',
            owner: { tabId: 'main', generation: 1 },
            availability: 'ready',
            lifecycle: 'ordinary',
            provenance: 'cache',
            work: acknowledged,
            effectiveWork: effectiveWork,
            recordOpen: false,
        });
        const published = globalThis.prksPublishWorkRouteProjection(ctx, 1, projection);
        const entity = ctx.entity;
        const role = published.effectiveWork && published.effectiveWork.roles && published.effectiveWork.roles[0];
        process.stdout.write(JSON.stringify({
            sameProjection: published === projection,
            effectiveTitle: published.effectiveWork && published.effectiveWork.title,
            roleId: role && role.id,
            roleType: role && role.role_type,
            roleFirst: role && role.first_name,
            entityTitle: entity && entity.title,
            entityRoles: entity && entity.roles ? entity.roles.length : null,
            entityIsEffective: entity === published.effectiveWork,
        }));
        """
        proc = subprocess.run(
            ["node", "-e", script, str(_PROJECT), json.dumps(ops)],
            cwd=_PROJECT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["sameProjection"])
        self.assertEqual(payload["effectiveTitle"], "Pending title")
        self.assertEqual(payload["roleId"], "p1")
        self.assertEqual(payload["roleType"], "Author")
        self.assertEqual(payload["roleFirst"], "Ada")
        self.assertEqual(payload["entityTitle"], "Acknowledged")
        self.assertEqual(payload["entityRoles"], 0)
        self.assertFalse(payload["entityIsEffective"])

    def test_failed_queue_read_keeps_pending_overlays(self):
        """A rejected listOperations() is not an empty queue. Pending maps
        stay, and metadata hydration is not promoted to ready-and-empty."""
        ops = [
            {
                "operation": "SET_WORK_METADATA_FIELD",
                "entity_type": "work",
                "entity_id": "w1",
                "status": "pending",
                "payload": {"field": "title", "value": "Pending title"},
            },
            {
                "operation": "ADD_WORK_PERSON_ROLE",
                "entity_type": "work",
                "entity_id": "w1",
                "status": "pending",
                "payload": {"person_id": "p1", "role_type": "Author", "credit_name": ""},
                "local_context": {
                    "person": {
                        "id": "p1",
                        "first_name": "Ada",
                        "last_name": "Lovelace",
                        "canonical_name": "Ada Lovelace",
                    }
                },
            },
        ]
        script = r"""
        const fs = require('fs');
        const root = process.argv[1];
        function loadTopLevel(source, name) {
            const at = source.indexOf('async function ' + name + '(');
            const atFn = at === -1 ? source.indexOf('function ' + name + '(') : at;
            if (atFn === -1) throw new Error('missing ' + name);
            const brace = source.indexOf('{', atFn);
            let depth = 0;
            for (let i = brace; i < source.length; i++) {
                if (source[i] === '{') depth += 1;
                else if (source[i] === '}') {
                    depth -= 1;
                    if (depth === 0) {
                        (0, eval)(source.slice(atFn, i + 1));
                        return;
                    }
                }
            }
            throw new Error('unclosed ' + name);
        }
        require(root + '/frontend/js/date-format.js');
        require(root + '/frontend/js/work-metadata-state.js');
        require(root + '/frontend/js/work-role-state.js');
        require(root + '/frontend/js/person-metadata-state.js');
        const app = fs.readFileSync(root + '/frontend/js/app.js', 'utf8');
        loadTopLevel(app, 'prksReadDurableOperations');
        loadTopLevel(app, 'prksApplyReadWorkOperations');
        const ops = JSON.parse(process.argv[2]);
        const acknowledged = { id: 'w1', title: 'Acknowledged', roles: [] };
        globalThis.prksSetPendingWorkMetadata(ops);
        globalThis.prksSetPendingWorkRoles(ops);
        globalThis.prksSync = {
            store: {
                listOperations() { return Promise.reject(new Error('idb blocked')); },
            },
        };
        globalThis.prksRefreshPendingWorkMetadata().then(function () {
            return prksReadDurableOperations();
        }).then(function (read) {
            const applied = prksApplyReadWorkOperations(read);
            const effective = globalThis.prksEffectiveWorkDetailRoles(
                globalThis.prksEffectiveWorkSync(acknowledged));
            const role = effective.roles && effective.roles[0];
            process.stdout.write(JSON.stringify({
                read: read,
                applied: applied,
                title: effective.title,
                roleType: role && role.role_type,
                hydration: globalThis.prksPendingWorkMetadataState(),
            }));
        }).catch(function (error) {
            console.error(error);
            process.exit(1);
        });
        """
        proc = subprocess.run(
            ["node", "-e", script, str(_PROJECT), json.dumps(ops)],
            cwd=_PROJECT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertIsNone(payload["read"])
        self.assertFalse(payload["applied"])
        self.assertEqual(payload["title"], "Pending title")
        self.assertEqual(payload["roleType"], "Author")
        self.assertEqual(payload["hydration"], "unavailable")
        self.assertNotEqual(payload["hydration"], "ready")


if __name__ == "__main__":
    unittest.main()
