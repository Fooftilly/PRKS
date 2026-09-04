#!/usr/bin/env node
'use strict';

/* Deterministic Node coverage for workspace-drag.js's pure geometry + exported API surface.
 * Real gesture flows (pointerdown/move/up sequences, DOM reconciliation, overlays, autoscroll,
 * Escape cancellation, click suppression) belong in Playwright E2E -- this file intentionally
 * does not build a fake browser to re-test those; it only proves the pure math the whole
 * targeting system is built on, plus that the module's exported contract is intact. */

const path = require('path');

const rootDir = path.resolve(__dirname, '../..');
const drag = require(path.join(rootDir, 'frontend/js/workspace-drag.js'));

let passed = 0;
let failed = 0;

function record(name, ok, detail) {
    if (ok) passed += 1;
    else failed += 1;
    console.log((ok ? 'PASS  ' : 'FAIL  ') + name + (detail ? ' ' + detail : ''));
}

function assertEq(name, got, want) {
    const ok = got === want;
    record(name, ok, ok ? '' : 'got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want));
}

function assert(name, ok, detail) {
    record(name, !!ok, ok ? '' : detail || '');
}

/* ---- A. computeEdgeZone ---- */

const RECT = { left: 100, top: 200, width: 300, height: 150 }; // right=400, bottom=350

assertEq('edge zone: center is null', drag.prksWorkspaceComputeEdgeZone(RECT, 250, 275), null);
assertEq('edge zone: left edge', drag.prksWorkspaceComputeEdgeZone(RECT, 105, 275), 'left');
assertEq('edge zone: right edge', drag.prksWorkspaceComputeEdgeZone(RECT, 395, 275), 'right');
assertEq('edge zone: top edge', drag.prksWorkspaceComputeEdgeZone(RECT, 250, 205), 'above');
assertEq('edge zone: bottom edge', drag.prksWorkspaceComputeEdgeZone(RECT, 250, 345), 'below');

/* Corners: the point nearest edge (normalized by that dimension) wins deterministically. A
 * square-ish rect keeps this simple to reason about: 200x200, band 0.28 -> band width 56px. */
const SQUARE = { left: 0, top: 0, width: 200, height: 200 };
/* Top-left corner, closer to the left edge than the top edge in normalized terms. */
assertEq('edge zone: corner nearer left wins', drag.prksWorkspaceComputeEdgeZone(SQUARE, 10, 30), 'left');
/* Same corner region, but now nearer the top edge in normalized terms. */
assertEq('edge zone: corner nearer top wins', drag.prksWorkspaceComputeEdgeZone(SQUARE, 30, 10), 'above');
/* Top-right corner. */
assertEq('edge zone: corner nearer right wins', drag.prksWorkspaceComputeEdgeZone(SQUARE, 190, 30), 'right');
assertEq('edge zone: corner nearer top (right side) wins', drag.prksWorkspaceComputeEdgeZone(SQUARE, 170, 10), 'above');
/* Bottom-left / bottom-right corners. */
assertEq('edge zone: corner nearer left (bottom) wins', drag.prksWorkspaceComputeEdgeZone(SQUARE, 10, 170), 'left');
assertEq('edge zone: corner nearer bottom (left side) wins', drag.prksWorkspaceComputeEdgeZone(SQUARE, 30, 190), 'below');
assertEq('edge zone: corner nearer right (bottom) wins', drag.prksWorkspaceComputeEdgeZone(SQUARE, 190, 170), 'right');
assertEq('edge zone: corner nearer bottom (right side) wins', drag.prksWorkspaceComputeEdgeZone(SQUARE, 170, 190), 'below');

/* Out of bounds / degenerate rects. */
assertEq('edge zone: outside rect is null', drag.prksWorkspaceComputeEdgeZone(RECT, 50, 275), null);
assertEq('edge zone: null rect is null', drag.prksWorkspaceComputeEdgeZone(null, 0, 0), null);
assertEq('edge zone: zero-size rect is null', drag.prksWorkspaceComputeEdgeZone({ left: 0, top: 0, width: 0, height: 0 }, 0, 0), null);

/* Custom band widens/narrows the edge region without changing the winner logic. A point 35% of
 * the way in from the left (and far from top/bottom, so left is unambiguously nearest) sits
 * outside the default 0.28 band but inside a wider 0.4 band. */
const WIDE = { left: 0, top: 0, width: 1000, height: 10 };
assertEq('edge zone: default band excludes 0.35-from-edge point', drag.prksWorkspaceComputeEdgeZone(WIDE, 350, 5), null);
assertEq('edge zone: custom wider band includes the same point', drag.prksWorkspaceComputeEdgeZone(WIDE, 350, 5, 0.4), 'left');

/* ---- B. computeReorderIndex ---- */

/* Four tabs A B C D, each 60px wide with no gaps: A[0,60] B[60,120] C[120,180] D[180,240]. */
const RECTS = [
    { id: 'A', left: 0, right: 60 },
    { id: 'B', left: 60, right: 120 },
    { id: 'C', left: 120, right: 180 },
    { id: 'D', left: 180, right: 240 },
];

assertEq('reorder index: before A (x=0)', drag.prksWorkspaceComputeTabReorderIndex(RECTS, 0), 0);
assertEq('reorder index: before A midpoint boundary', drag.prksWorkspaceComputeTabReorderIndex(RECTS, 29), 0);
assertEq('reorder index: between A/B (just past A midpoint)', drag.prksWorkspaceComputeTabReorderIndex(RECTS, 31), 1);
assertEq('reorder index: between B/C (just past B midpoint)', drag.prksWorkspaceComputeTabReorderIndex(RECTS, 91), 2);
assertEq('reorder index: between C/D (just past C midpoint)', drag.prksWorkspaceComputeTabReorderIndex(RECTS, 151), 3);
assertEq('reorder index: after D (past D midpoint)', drag.prksWorkspaceComputeTabReorderIndex(RECTS, 211), 4);
assertEq('reorder index: far past the end', drag.prksWorkspaceComputeTabReorderIndex(RECTS, 10000), 4);
assertEq('reorder index: empty rects is 0', drag.prksWorkspaceComputeTabReorderIndex([], 50), 0);
assertEq('reorder index: null rects is 0', drag.prksWorkspaceComputeTabReorderIndex(null, 50), 0);

/* Dragged tab is excluded by the caller before this is called -- three-rect list simulating B
 * being the drag source with A, C, D as the remaining strip. */
const RECTS_MINUS_B = [
    { id: 'A', left: 0, right: 60 },
    { id: 'C', left: 60, right: 120 },
    { id: 'D', left: 120, right: 180 },
];
assertEq('reorder index: works with a hole left by the dragged tab', drag.prksWorkspaceComputeTabReorderIndex(RECTS_MINUS_B, 91), 2);

/* ---- C. API surface ---- */

assert('exports prksWorkspaceInitDrag', typeof drag.prksWorkspaceInitDrag === 'function');
assert('exports prksWorkspaceCancelActiveDrag', typeof drag.prksWorkspaceCancelActiveDrag === 'function');
assert('exports prksWorkspaceComputeEdgeZone', typeof drag.prksWorkspaceComputeEdgeZone === 'function');
assert('exports prksWorkspaceComputeTabReorderIndex', typeof drag.prksWorkspaceComputeTabReorderIndex === 'function');

/* prksWorkspaceCancelActiveDrag must be a safe no-op with no drag active and no DOM present
 * (this file runs under plain Node, so `document` is undefined inside the module). */
let cancelThrew = false;
try {
    drag.prksWorkspaceCancelActiveDrag();
} catch (_e) {
    cancelThrew = true;
}
assert('prksWorkspaceCancelActiveDrag is a no-op with nothing active', !cancelThrew);

/* prksWorkspaceInitDrag must not throw when there is no `document` global either -- it is
 * required to check for one and simply decline to bind. */
let initThrew = false;
try {
    drag.prksWorkspaceInitDrag();
} catch (_e) {
    initThrew = true;
}
assert('prksWorkspaceInitDrag is a no-op without document', !initThrew);

/* ---- D. Module hygiene ---- */

const fs = require('fs');
const src = fs.readFileSync(path.join(rootDir, 'frontend/js/workspace-drag.js'), 'utf8');
assert('workspace-drag.js has no localStorage', src.indexOf('localStorage') === -1);
assert('workspace-drag.js has no sessionStorage', src.indexOf('sessionStorage') === -1);
assert('workspace-drag.js has no indexedDB', src.indexOf('indexedDB') === -1);

console.log('');
console.log((failed === 0 ? 'All ' + passed + ' workspace drag checks passed, 0 failed' : passed + ' passed, ' + failed + ' failed'));
process.exit(failed === 0 ? 0 : 1);
