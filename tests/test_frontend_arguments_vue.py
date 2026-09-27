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
        self.assertIn("prksPresentVueArguments(", index)
        self.assertIn("availability: 'unavailable'", index)
        self.assertIn("prksEffectiveArgumentDetail(item, argumentOps)", detail)
        self.assertIn("prksPendingCreatedArgument", detail)
        self.assertIn("prksApplyPendingPositionNamesToTargets", detail)
        self.assertIn("prksApplyPendingArgumentNamesToTargets", detail)
        self.assertIn("prksApplyPendingArgumentNames(effectiveArgument.responses)", detail)
        self.assertIn("prksEffectiveWorkReferences('argument', effectiveArgument)", detail)
        self.assertIn("ctx.ui.argumentEditing = false;", detail)
        self.assertIn("prksPresentVueArguments(", detail)
        self.assertIn("availability: 'not-found'", detail)
        self.assertNotIn("renderArgumentsIndex", app)
        self.assertNotIn("renderArgumentDetail", app)
        self.assertNotIn("renderArgumentNotFound", app)

    def test_arguments_route_reuses_host_and_dismisses_before_retry(self):
        app = (FRONTEND / "app.js").read_text()
        self.assertIn("sameArgumentsWorkspace", app)
        self.assertIn("__prksRetainArgumentsSurface", app)
        present = app[
            app.index("function prksPresentVueArguments") : app.index("function prksRenderRouteLoading")
        ]
        self.assertIn(":scope > [data-prks-vue-route-host]", present)
        self.assertIn("contentDiv.innerHTML = '';", present)
        self.assertLess(present.index("querySelector"), present.index("contentDiv.innerHTML = '';"))
        self.assertIn(
            "sameArgumentsWorkspace && typeof window.prksVueDismissArguments",
            app,
        )
        owned = app[
            app.index("function prksCanLeaveTabContextOwnedDraft") : app.index(
                "async function prksRenderTabRoute"
            )
        ]
        self.assertNotIn("argumentEditing", owned)
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
        self.assertIn("prksDeleteArgumentDurably", intents)
        self.assertIn("prksOpenResearchPicker", intents)
        self.assertIn("createArgument", intents)
        projection = (FEATURE / "projection.ts").read_text()
        self.assertIn("prksEffectiveArgumentRows", projection)
        self.assertIn("prksFilterArgumentsByKind", projection)
        stub = (FRONTEND / "components" / "arguments.js").read_text()
        self.assertIn("prksCreateArgumentFromWork", stub)
        self.assertNotIn("function renderArgumentsIndex", stub)
        self.assertNotIn("argumentMutationBlocked", stub)
        state = (FRONTEND / "argument-state.js").read_text()
        self.assertIn("async function prepareArgumentEdit(", state)
        self.assertIn("async function commitArgumentEditorDraft(", state)
        self.assertIn("prksPrepareArgumentEdit: prepareArgumentEdit", state)
        self.assertIn("prksCommitArgumentEditorDraft: commitArgumentEditorDraft", state)
