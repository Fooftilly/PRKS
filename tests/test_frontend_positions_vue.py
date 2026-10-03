"""Static contracts for Positions Vue route-surface (#266 / #230)."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "js"
FEATURE = ROOT / "frontend-app" / "src" / "features" / "positions"


class PositionsVueContracts(unittest.TestCase):
    def test_coordinator_owns_effective_position_projection(self):
        app = (FRONTEND / "app.js").read_text()
        detail = app[app.index("case 'position-detail': {") : app.index("case 'arguments': {")]
        self.assertIn("prksPendingCreatedPosition", detail)
        self.assertIn("prksHydratePendingWorkMetadata()", detail)
        self.assertIn("prksEffectivePositionDetail", detail)
        self.assertIn("prksApplyPendingArgumentNames", detail)
        self.assertIn("prksPresentVueRoute(ctx, contentDiv, 'position-detail'", detail)
        self.assertIn("availability: 'unavailable'", detail)
        self.assertIn("availability: 'not-found'", detail)
        self.assertIn("prksOfflinePrependBanner(contentDiv, null)", detail)
        index = app[app.index("case 'positions': {") : app.index("case 'position-detail': {")]
        self.assertIn("prksEffectivePositionRows", index)
        self.assertIn("prksPresentVueRoute(ctx, contentDiv, 'positions'", index)
        self.assertNotIn("renderPositionsIndex", app)
        self.assertNotIn("renderPositionDetail", app)
        self.assertNotIn("renderPositionNotFound", app)

    def test_positions_route_reuses_host_on_in_place_refresh(self):
        app = (FRONTEND / "app.js").read_text()
        self.assertIn("samePositionsWorkspace", app)
        self.assertIn("__prksRetainPositionsSurface", app)
        retained = app[app.index("const PRKS_RETAINED_VUE_ROUTE_FEATURES") : app.index("function prksPresentVueRoute(")]
        self.assertIn("'positions',", retained)
        self.assertIn("'position-detail',", retained)
        present = app[
            app.index("function prksPresentVueRoute(") : app.index("async function prksReloadTagsVocabulary")
        ]
        self.assertIn(":scope > [data-prks-vue-route-host]", present)
        self.assertIn("contentDiv.innerHTML = '';", present)
        self.assertLess(present.index("querySelector"), present.index("contentDiv.innerHTML = '';"))
        self.assertIn("samePositionsWorkspace ||", app)
        self.assertIn(
            "retainedRouteSurface && typeof window.prksVueDismissRoute",
            app,
        )

    def test_vue_does_not_own_durable_position_state(self):
        combined = "\n".join(path.read_text() for path in FEATURE.rglob("*") if path.is_file())
        self.assertNotIn("prksDurable", combined)
        self.assertNotIn("fetch(", combined)
        self.assertNotIn("prksRequest(", combined)
        self.assertIn("createPosition", (FEATURE / "intents.ts").read_text())
        projection = (FEATURE / "projection.ts").read_text()
        self.assertIn("prksEffectivePositionRows", projection)
        self.assertIn("Argument-name overlay", projection)
        self.assertIn("Work-metadata hydration", projection)
        self.assertNotIn("useDebounceFn", combined)
        self.assertNotIn("useEventListener", combined)
        stub = (FRONTEND / "components" / "positions.js").read_text()
        self.assertNotIn("function renderPositionsIndex", stub)
        self.assertNotIn("positionMutationBlocked", stub)
