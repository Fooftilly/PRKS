/**
 * Workspace drag geometry + cancel/init contract (#256).
 *
 * Sensors/lifecycle live in `frontend-app/src/workspace-dnd/` (Pragmatic DnD),
 * bundled into `frontend/vue/prks-vue.js` and bound by Vue WorkspaceShell.
 * This classic script keeps the pure geometry helpers and the stable window
 * API surface that Node selftests and pre-Vue bootstrap callers expect.
 *
 * `prksWorkspaceInitDrag` / `prksWorkspaceCancelActiveDrag` are no-ops here
 * until the Vue shell overwrites them with the production adapter hooks.
 * Cancel remains safe to call when idle.
 */
(function (root) {
    'use strict';

    const EDGE_BAND = 0.28;

    /** Which edge band (if any) `(x, y)` falls in within `rect`. */
    function computeEdgeZone(rect, x, y, band) {
        if (!rect || !rect.width || !rect.height) return null;
        const b = typeof band === 'number' && band > 0 && band < 0.5 ? band : EDGE_BAND;
        const px = x - rect.left;
        const py = y - rect.top;
        if (px < 0 || py < 0 || px > rect.width || py > rect.height) return null;
        const dTop = py / rect.height;
        const dBottom = (rect.height - py) / rect.height;
        const dLeft = px / rect.width;
        const dRight = (rect.width - px) / rect.width;
        const min = Math.min(dTop, dBottom, dLeft, dRight);
        if (min > b) return null;
        if (min === dLeft) return 'left';
        if (min === dRight) return 'right';
        if (min === dTop) return 'above';
        return 'below';
    }

    /** Insertion index for tab-strip reordering by midpoint geometry. */
    function computeReorderIndex(rects, x) {
        if (!rects || !rects.length) return 0;
        for (let i = 0; i < rects.length; i++) {
            const mid = (rects[i].left + rects[i].right) / 2;
            if (x < mid) return i;
        }
        return rects.length;
    }

    function prksWorkspaceInitDrag() {
        /* Vue WorkspaceShell owns Pragmatic binding; safe no-op before mount. */
    }

    function prksWorkspaceCancelActiveDrag() {
        /* Vue WorkspaceShell overwrites this with the live adapter cancel. */
    }

    const api = {
        prksWorkspaceInitDrag: prksWorkspaceInitDrag,
        prksWorkspaceCancelActiveDrag: prksWorkspaceCancelActiveDrag,
        prksWorkspaceComputeEdgeZone: computeEdgeZone,
        prksWorkspaceComputeTabReorderIndex: computeReorderIndex,
    };
    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
