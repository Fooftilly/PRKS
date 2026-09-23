#!/usr/bin/env node
'use strict';

const path = require('path');

const rootDir = path.resolve(__dirname, '../..');
const tc = require(path.join(rootDir, 'frontend/js/tab-context.js'));

const {
    createPrksTabContext,
    prksEnsureTabContext,
    prksGetTabContext,
    prksDestroyTabContext,
    prksDestroyAllTabContexts,
    prksMountTabContext,
    prksWarmParkTabContext,
    prksResumeWarmTabContext,
    prksUnmountTabContext,
    prksForEachMountedTabContext,
    prksForEachLiveTabContext,
    prksTabContextDebugSnapshot,
    prksContextFromElement,
} = tc;

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

function makeHost() {
    const kids = [];
    const host = {
        children: kids,
        innerHTML: '',
        appendChild: function (c) {
            kids.push(c);
            c.parentNode = host;
            return c;
        },
        removeChild: function (c) {
            const i = kids.indexOf(c);
            if (i >= 0) kids.splice(i, 1);
            c.parentNode = null;
            return c;
        },
        querySelector: function (sel) {
            if (sel && String(sel).indexOf('prks-tab-root') !== -1) return kids[0] || null;
            return null;
        },
        querySelectorAll: function () {
            return kids.slice();
        },
    };
    return host;
}

prksDestroyAllTabContexts();

const warmParking = makeHost();
const warmVisible = makeHost();
const warmContexts = [];
const warmRoots = [];
const warmRuntimes = [];
const warmDisposeCounts = [0, 0, 0, 0];
const warmResizeCounts = [0, 0, 0, 0];
for (let i = 0; i < 4; i++) {
    const ctx = prksEnsureTabContext('warm-' + i);
    ctx.mount(makeHost());
    ctx.beginRoute({ name: 'work', hash: '#/works/W' + i });
    const runtime = {
        resize: function () { warmResizeCounts[i] += 1; },
    };
    ctx.setResource('pdf', runtime, function () { warmDisposeCounts[i] += 1; });
    warmContexts.push(ctx);
    warmRoots.push(ctx.root);
    warmRuntimes.push(runtime);
    assert('warm park ' + i, prksWarmParkTabContext(ctx.tabId, warmParking));
}
assertEq('warm LRU bounded at three', prksTabContextDebugSnapshot().warmParkedCount, 3);
assert('oldest warm context evicted', warmContexts[0].suspended === false && warmContexts[0].root === null);
assertEq('oldest runtime destroyed once', warmDisposeCounts[0], 1);
assert('three newest stay suspended', warmContexts.slice(1).every(function (ctx) { return ctx.suspended; }));

const resumedRoot = warmContexts[1].root;
const resumedRuntime = warmContexts[1].getResource('pdf');
assert('warm resume succeeds', prksResumeWarmTabContext('warm-1', warmVisible) === warmContexts[1]);
assert('warm resume preserves context', prksGetTabContext('warm-1') === warmContexts[1]);
assert('warm resume preserves root', warmContexts[1].root === resumedRoot && resumedRoot === warmRoots[1]);
assert('warm resume preserves PDF runtime', warmContexts[1].getResource('pdf') === resumedRuntime && resumedRuntime === warmRuntimes[1]);
assertEq('warm resume requests resize once', warmResizeCounts[1], 1);
assertEq('warm resume does not dispose runtime', warmDisposeCounts[1], 0);
prksDestroyTabContext('warm-1');
assertEq('closing resumed warm PDF destroys once', warmDisposeCounts[1], 1);
prksDestroyTabContext('warm-2');
assertEq('closing parked warm PDF destroys once', warmDisposeCounts[2], 1);
prksDestroyAllTabContexts();
assertEq('warm cache empty after teardown', prksTabContextDebugSnapshot().warmParkedCount, 0);

const iteratorParking = makeHost();
const iteratorA = prksEnsureTabContext('iterator-a');
const iteratorB = prksEnsureTabContext('iterator-b');
const iteratorC = prksEnsureTabContext('iterator-c');
const iteratorD = prksEnsureTabContext('iterator-d');
iteratorA.mount(makeHost());
iteratorB.mount(makeHost());
iteratorB.setResource('pdf', {}, function () {});
assert('iterator B warm-suspends', prksWarmParkTabContext(iteratorB.tabId, iteratorParking));
iteratorC.mount(makeHost());
iteratorC.unmount('cold-park');
iteratorD.mount(makeHost());
iteratorD.destroy();
const mountedIteratorIds = [];
const liveIteratorIds = [];
prksForEachMountedTabContext(function (ctx) { mountedIteratorIds.push(ctx.tabId); });
prksForEachLiveTabContext(function (ctx) { liveIteratorIds.push(ctx.tabId); });
assertEq('mounted iterator sees visible A only', mountedIteratorIds.join(','), 'iterator-a');
assertEq('live iterator sees visible A and warm B', liveIteratorIds.join(','), 'iterator-a,iterator-b');
prksDestroyAllTabContexts();

const a = createPrksTabContext('tab-1');
const b = createPrksTabContext('tab-2');
assert('ctx A != ctx B', a !== b);
assert('person drafts start empty', a.ui.personProfileDraft === null && b.ui.personProfileDraft === null);
a.ui.personProfileDraft = { personId: 'person-a', groups: [{ id: 'group-a', name: 'Alpha' }] };
b.ui.personProfileDraft = { personId: 'person-b', groups: [{ id: 'group-b', name: 'Beta' }] };
a.ui.personProfileDraft.groups.push({ id: 'group-c', name: 'Gamma' });
assert('person drafts are independent objects', a.ui.personProfileDraft !== b.ui.personProfileDraft);
assertEq('person draft group mutation stays isolated', b.ui.personProfileDraft.groups.length, 1);
assertEq('tabId A', a.tabId, 'tab-1');
assertEq('tabId B', b.tabId, 'tab-2');
assert('domId A pdf != B pdf', a.domId('pdf-viewer') !== b.domId('pdf-viewer'));
assertEq('domId A shape', a.domId('pdf-viewer'), 'prks-tab-tab-1-pdf-viewer');
assertEq('domId B shape', b.domId('research-notes-editor'), 'prks-tab-tab-2-research-notes-editor');
assert('domId sanitizes', a.domId('x y') !== a.domId('pdf-viewer'));

const genA0 = a.generation;
const genB0 = b.generation;
const genA1 = a.beginRoute({ name: 'work', hash: '#/works/W1' });
assertEq('A begin gen', genA1, genA0 + 1);
assertEq('B gen unchanged', b.generation, genB0);
assert('A isCurrent new', a.isCurrent(genA1) === false);
a.mounted = true;
assert('A isCurrent when mounted', a.isCurrent(genA1));
assert('A not current old', a.isCurrent(genA0) === false);
const foldA1 = a.beginFolderHierarchyRefresh('full');
const foldA2 = a.beginFolderHierarchyRefresh('full');
assert('folder hierarchy refresh older stale', a.isFolderHierarchyRefreshCurrent(foldA1) === false);
assert('folder hierarchy refresh newest current', a.isFolderHierarchyRefreshCurrent(foldA2) === true);
const foldSel = a.beginFolderHierarchyRefresh('selection');
assert('selection does not invalidate full', a.isFolderHierarchyRefreshCurrent(foldA2) === true);
assert('selection current alongside full', a.isFolderHierarchyRefreshCurrent(foldSel) === true);
const fullBeforeRoute = a.folderHierarchyFullGeneration;
const selBeforeRoute = a.folderHierarchySelectionGeneration;
a.beginRoute({ name: 'folder-detail', hash: '#/folders/x' });
assertEq('beginRoute advances full hierarchy gen', a.folderHierarchyFullGeneration, fullBeforeRoute + 1);
assertEq('beginRoute advances selection hierarchy gen', a.folderHierarchySelectionGeneration, selBeforeRoute + 1);
assert('beginRoute invalidates pre-route full', a.isFolderHierarchyRefreshCurrent(foldA2) === false);
assert('beginRoute invalidates pre-route selection', a.isFolderHierarchyRefreshCurrent(foldSel) === false);
a.mounted = false;

const hostA = makeHost();
const hostB = makeHost();
a.mount(hostA);
b.mount(hostB);
assert('A mounted', a.mounted);
assert('B mounted', b.mounted);
assert('A root != B root', a.root !== b.root);
assertEq('A root tab id', a.root.getAttribute('data-prks-tab-id'), 'tab-1');
assertEq('B root tab id', b.root.getAttribute('data-prks-tab-id'), 'tab-2');

a.root.querySelector = function (sel) {
    return sel === '[data-prks-role="pdf-viewer"]' ? { id: 'A-pdf' } : null;
};
b.root.querySelector = function (sel) {
    return sel === '[data-prks-role="pdf-viewer"]' ? { id: 'B-pdf' } : null;
};
assertEq('query A own', a.query('[data-prks-role="pdf-viewer"]').id, 'A-pdf');
assertEq('query B own', b.query('[data-prks-role="pdf-viewer"]').id, 'B-pdf');
assert('query A not B', a.query('[data-prks-role="pdf-viewer"]').id !== 'B-pdf');

const genA = a.beginRoute({ name: 'work' });
const genB = b.beginRoute({ name: 'person' });
const sigA = a.abortController.signal;
const sigB = b.abortController.signal;
a.abortController.abort();
assert('A aborted', !!(sigA.aborted || sigA.aborted === true));
assert('B abort isolated', !sigB.aborted);

let disposedA = 0;
let disposedB = 0;
a.setResource(
    'pdf',
    { id: 'pdf-a' },
    function () {
        disposedA += 1;
    }
);
b.setResource(
    'pdf',
    { id: 'pdf-b' },
    function () {
        disposedB += 1;
    }
);
assertEq('get A pdf', a.getResource('pdf').id, 'pdf-a');
assertEq('get B pdf', b.getResource('pdf').id, 'pdf-b');
a.clearResource('pdf');
assertEq('dispose A once', disposedA, 1);
assertEq('B pdf still live', b.getResource('pdf').id, 'pdf-b');
assertEq('B not disposed', disposedB, 0);

let cleanA = 0;
let cleanB = 0;
a.registerCleanup(function () {
    cleanA += 1;
});
b.registerCleanup(function () {
    cleanB += 1;
});
a.unmount('park');
a.unmount('park');
assertEq('unmount A cleanup once', cleanA, 1);
assertEq('unmount B cleanup untouched', cleanB, 0);
assertEq('A resources empty', a.resources.size, 0);
assertEq('A timers empty', a.timers.size, 0);
assert('A abort cleared', a.abortController === null);
assert('A not mounted', a.mounted === false);
assert('A entity cleared', a.entity === null);

let disposedGraph = 0;
b.setResource(
    'graph',
    { id: 'g' },
    function () {
        disposedGraph += 1;
    }
);
b.destroy();
b.destroy();
assertEq('destroy B pdf disposer once', disposedB, 1);
assertEq('destroy B graph disposer once', disposedGraph, 1);
assert('B destroyed flag', b.destroyed);

const reg = prksEnsureTabContext('tab-reg');
assert('ensure same', prksEnsureTabContext('tab-reg') === reg);
assert('get matches', prksGetTabContext('tab-reg') === reg);
prksDestroyTabContext('tab-reg');
assert('get after destroy', prksGetTabContext('tab-reg') === null);

const hostBg = makeHost();
prksEnsureTabContext('bg-1');
assert('background ensure not mounted', prksGetTabContext('bg-1').mounted === false);
assertEq('background no resources', prksGetTabContext('bg-1').resources.size, 0);
prksMountTabContext('bg-1', hostBg);
assert('first activation mounted', prksGetTabContext('bg-1').mounted === true);
prksUnmountTabContext('bg-1', 'park');
assert('parked still registered', !!prksGetTabContext('bg-1'));
assert('parked inert', prksGetTabContext('bg-1').mounted === false);
prksDestroyTabContext('bg-1');
assert('close removes registry', prksGetTabContext('bg-1') === null);

const snap = prksTabContextDebugSnapshot();
assert('debug has mountedCount', typeof snap.mountedCount === 'number');
assert('debug no route field', snap.contexts.every(function (row) {
    return !Object.prototype.hasOwnProperty.call(row, 'route');
}));

const fromEl = prksContextFromElement(null);
assert('fromElement null', fromEl === null);

prksDestroyAllTabContexts();
assertEq('destroy all empty', prksTabContextDebugSnapshot().contexts.length, 0);

prksDestroyAllTabContexts();
const host1 = makeHost();
const host2 = makeHost();
const host3 = makeHost();
prksMountTabContext('stack-a', host1);
assertEq('stacked one mounted', prksTabContextDebugSnapshot().mountedCount, 1);
prksMountTabContext('tile-b', host2);
assertEq('tiled two mounted', prksTabContextDebugSnapshot().mountedCount, 2);
prksMountTabContext('over-c', host3);
assert('module allows third mount', prksTabContextDebugSnapshot().mountedCount >= 2);
prksUnmountTabContext('over-c', 'park');
prksUnmountTabContext('tile-b', 'park');
assertEq('parked B inert root', prksGetTabContext('tile-b').root, null);
assertEq('parked B no abort', prksGetTabContext('tile-b').abortController, null);
assertEq('parked B resources', prksGetTabContext('tile-b').resources.size, 0);
assertEq('parked B timers', prksGetTabContext('tile-b').timers.size, 0);
prksDestroyAllTabContexts();

prksDestroyAllTabContexts();

/* Warm suspend must dismiss a body-mounted quick preview keyed to this pane's
 * thumbs before moveRoot — source stays connected in parking, so prune-on-detach
 * would miss it. Scoped to ctx.root so another visible tile's preview is left alone. */
{
    const parkHost = makeHost();
    const visible = makeHost();
    const otherVisible = makeHost();
    let released = [];
    globalThis.prksReleaseWorkThumbPreview = function (rootEl) {
        released.push(rootEl);
        const src = globalThis.__prksWorkThumbPreviewSource;
        if (!src || !rootEl) return;
        let under = false;
        let n = src;
        while (n) {
            if (n === rootEl) {
                under = true;
                break;
            }
            n = n.parentNode;
        }
        if (under) globalThis.__prksWorkThumbPreviewSource = null;
    };

    const warmCtx = prksEnsureTabContext('preview-warm');
    warmCtx.mount(visible);
    warmCtx.setResource('pdf', {}, function () {});
    const thumb = { parentNode: null, isConnected: true };
    warmCtx.root.appendChild(thumb);
    globalThis.__prksWorkThumbPreviewSource = thumb;

    const otherCtx = prksEnsureTabContext('preview-other');
    otherCtx.mount(otherVisible);
    const otherThumb = { parentNode: null, isConnected: true };
    otherCtx.root.appendChild(otherThumb);

    assert('warm park with preview open', prksWarmParkTabContext(warmCtx.tabId, parkHost));
    assert('suspend released against warm root', released.indexOf(warmCtx.root) !== -1);
    assertEq('warm park cleared this pane preview source', globalThis.__prksWorkThumbPreviewSource, null);
    assert('warm root still alive after suspend', !!warmCtx.root && warmCtx.suspended);

    released = [];
    globalThis.__prksWorkThumbPreviewSource = otherThumb;
    assert('warm park other pane untouched source before', globalThis.__prksWorkThumbPreviewSource === otherThumb);
    /* Warm-parking the already-suspended context is a no-op; park a fresh PDF instead. */
    const warm2 = prksEnsureTabContext('preview-warm-2');
    warm2.mount(makeHost());
    warm2.setResource('pdf', {}, function () {});
    assert('warm park second PDF', prksWarmParkTabContext(warm2.tabId, parkHost));
    assert('second park release did not clear other tile source', globalThis.__prksWorkThumbPreviewSource === otherThumb);

    delete globalThis.prksReleaseWorkThumbPreview;
    delete globalThis.__prksWorkThumbPreviewSource;
    prksDestroyAllTabContexts();
}

let boom = 0;
const c = createPrksTabContext('tab-boom');
c.setResource('bad', {}, function () {
    boom += 1;
    throw new Error('cleanup boom');
});
c.setResource('ok', {}, function () {
    boom += 10;
});
c.destroy();
assertEq('cleanup continues after throw', boom, 11);

console.log('\n' + passed + ' passed, ' + failed + ' failed');
process.exit(failed ? 1 : 0);
