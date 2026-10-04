"""Static contracts for Arguments & Stances Vue route-surface (#269 / #230)."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "js"
FEATURE = ROOT / "frontend-app" / "src" / "features" / "arguments"


class ArgumentsVueContracts(unittest.TestCase):
    def test_coordinator_owns_effective_argument_projection(self):
        app = (FRONTEND / "app.js").read_text()
        index = app[app.index("case 'arguments': {") : app.index("case 'argument-detail': {")]
        detail = app[app.index("case 'argument-detail': {") : app.index("case 'research-graph': {")]
        self.assertIn("prksEffectiveArgumentRows(", index)
        self.assertIn("prksFilterArgumentsByKind(allArguments, kind)", index)
        self.assertIn("prksPresentVueRoute(ctx, contentDiv, 'arguments'", index)
        self.assertIn("availability: 'unavailable'", index)
        self.assertIn("prksEffectiveArgumentDetail(item, argumentOps)", detail)
        self.assertIn("prksPendingCreatedArgument", detail)
        self.assertIn("prksApplyPendingPositionNamesToTargets", detail)
        self.assertIn("prksApplyPendingArgumentNamesToTargets", detail)
        self.assertIn("prksApplyPendingArgumentNames(effectiveArgument.responses)", detail)
        self.assertIn("prksEffectiveWorkReferences('argument', effectiveArgument)", detail)
        self.assertIn("ctx.ui.argumentEditing = false;", detail)
        self.assertIn("prksPresentVueRoute(ctx, contentDiv, 'argument-detail'", detail)
        self.assertIn("availability: 'not-found'", detail)
        self.assertNotIn("renderArgumentsIndex", app)
        self.assertNotIn("renderArgumentDetail", app)
        self.assertNotIn("renderArgumentNotFound", app)

    def test_arguments_route_reuses_host_and_dismisses_before_retry(self):
        app = (FRONTEND / "app.js").read_text()
        self.assertIn("sameArgumentsWorkspace", app)
        self.assertIn("__prksRetainArgumentsSurface", app)
        retained = app[app.index("const PRKS_RETAINED_VUE_ROUTE_FEATURES") : app.index("function prksPresentVueRoute(")]
        self.assertIn("'arguments',", retained)
        self.assertIn("'argument-detail',", retained)
        present = app[
            app.index("function prksPresentVueRoute(") : app.index("async function prksReloadTagsVocabulary")
        ]
        self.assertIn(":scope > [data-prks-vue-route-host]", present)
        self.assertIn("contentDiv.innerHTML = '';", present)
        self.assertLess(present.index("querySelector"), present.index("contentDiv.innerHTML = '';"))
        self.assertIn(
            "retainedRouteSurface && typeof window.prksVueDismissRoute",
            app,
        )
        self.assertIn(
            "sameArgumentsWorkspace ||",
            app,
        )
        people = (FRONTEND / "components" / "people.js").read_text()
        person_probe = people[
            people.index("function prksAssessPersonProfileLeave") : people.index(
                "function prksSyncPersonProfileDraftFromEditor"
            )
        ]
        work_src = (FRONTEND / "ui.js").read_text()
        work_probe = work_src[
            work_src.index("function prksAssessWorkMetadataLeave") : work_src.index(
                "function prksBindWorkMetaDraftEditor"
            )
        ]
        self.assertNotIn("argumentEditing", person_probe + work_probe)
        leave_src = (ROOT / "frontend-app" / "src" / "lifecycle" / "tab-leave.ts").read_text()
        self.assertNotIn("argumentEditing", leave_src)
        refresh = app[
            app.index("function prksOfflineMaybeRefreshFocusedRoute") : app.index(
                "function prksRenderConnectivityIndicator"
            )
        ]
        self.assertIn("ctx.ui.argumentEditing", refresh)

    def test_vue_does_not_own_durable_argument_state(self):
        combined = "\n".join(path.read_text() for path in FEATURE.rglob("*") if path.is_file())
        self.assertNotIn("listOperations", combined)
        self.assertNotIn("prksDurable", combined)
        self.assertNotIn("fetch(", combined)
        self.assertNotIn("prksRequest(", combined)
        self.assertNotIn("useDebounceFn", combined)
        self.assertNotIn("useEventListener", combined)
        intents = (FEATURE / "intents.ts").read_text()
        self.assertIn("prksCommitArgumentEditorDraft", intents)
        self.assertIn("deleteArgument", intents)
        self.assertNotIn("prksDeleteArgumentDurably", intents)
        self.assertIn("prksOpenResearchPicker", intents)
        self.assertIn("createArgument", intents)
        projection = (FEATURE / "projection.ts").read_text()
        self.assertIn("prksEffectiveArgumentRows", projection)
        self.assertIn("prksFilterArgumentsByKind", projection)
        self.assertNotIn("randomUUID()", projection)
        self.assertIn("createEditorRowKeys", projection)
        stub = (FRONTEND / "components" / "arguments.js").read_text()
        self.assertIn("prksCreateArgumentFromWork", stub)
        self.assertNotIn("function renderArgumentsIndex", stub)
        self.assertNotIn("argumentMutationBlocked", stub)
        state = (FRONTEND / "argument-state.js").read_text()
        self.assertIn("async function prepareArgumentEdit(", state)
        self.assertIn("async function commitArgumentEditorDraft(", state)
        self.assertIn("prksPrepareArgumentEdit: prepareArgumentEdit", state)
        self.assertIn("prksCommitArgumentEditorDraft: commitArgumentEditorDraft", state)
        detail = (FEATURE / "ArgumentDetailRoute.vue").read_text()
        self.assertNotIn("randomUUID()", detail)
        self.assertIn("createEditorRowKeys", detail)
        self.assertIn(':key="row.rowKey"', detail)
        self.assertNotIn(':key="index"', detail)
        self.assertIn("target:${row.rowKey}", detail)
        self.assertIn("source:${row.rowKey}", detail)
        self.assertIn("argumentEditorDraftFromForm", detail)
        self.assertIn(':for-id="`prks-arg-verdict-${row.rowKey}`"', detail)
        self.assertIn(':id="`prks-arg-verdict-${row.rowKey}`"', detail)
        self.assertIn(':label="\'Verdict\'"', detail)
        self.assertIn('aria-label="Verdict"', detail)
        self.assertIn('busy-label="Saving…"', detail)
        self.assertIn('busy-label="Editing…"', detail)
        self.assertIn('busy-label="Deleting…"', detail)
        self.assertIn('busy-label="Creating…"', detail)
        self.assertIn('busy-label="Choosing…"', detail)
        self.assertIn('busy-label="Adding…"', detail)
        self.assertIn("useArgumentPendingAction", detail)
        pending = (FEATURE / "pending-action.ts").read_text()
        self.assertIn("useArgumentPendingAction", pending)
        self.assertIn("usePendingAction", pending)
        shared = (ROOT / "frontend-app" / "src" / "route-surface" / "pending-action.ts").read_text()
        self.assertIn("finally", shared)
        self.assertIn("pending.value = null", shared)
        index = (FEATURE / "ArgumentsIndexRoute.vue").read_text()
        self.assertIn("useArgumentPendingAction", index)
        self.assertIn('busy-label="Creating…"', index)
        self.assertIn('id="prks-argument-new"', index)
        self.assertIn('id="prks-stance-new"', index)
        self.assertIn('id="prks-argument-new-empty"', index)
        self.assertIn('id="prks-stance-new-empty"', index)
        self.assertIn(
            "confirmLabel: argument.kind === 'stance' ? 'Delete Stance' : 'Delete Argument'",
            intents,
        )
