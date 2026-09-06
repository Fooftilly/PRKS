#!/usr/bin/env node
'use strict';

const path = require('path');

const rootDir = path.resolve(__dirname, '../..');
const tc = require(path.join(rootDir, 'frontend/js/tab-context.js'));
const pdfRt = require(path.join(rootDir, 'frontend/js/pdf-work-runtime.js'));

const {
    prksEnsureTabContext,
    prksDestroyAllTabContexts,
    prksForEachLiveTabContext,
    prksWarmParkTabContext,
    prksUnmountTabContext,
} = tc;
const { createWorkPdfRuntime, prksHasPendingWorkAnnotationSync, createPdfAnnotationPersistenceWorker, prksPdfPersistenceStillLive, prksInstallPdfAnnotationPersistenceIfCurrent } = pdfRt;

let passed = 0;
let failed = 0;

function record(name, ok, detail) {
    if (ok) passed += 1;
    else failed += 1;
    console.log((ok ? 'PASS  ' : 'FAIL  ') + name + (detail ? ' ' + detail : ''));
}

function assert(name, ok, detail) {
    record(name, !!ok, ok ? '' : detail || '');
}

function assertEq(name, got, want) {
    const ok = got === want;
    record(name, ok, ok ? '' : 'got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want));
}

function makeHost() {
    const kids = [];
    return {
        children: kids,
        innerHTML: '',
        appendChild: function (c) {
            kids.push(c);
            c.parentNode = this;
            return c;
        },
        removeChild: function (c) {
            const i = kids.indexOf(c);
            if (i >= 0) kids.splice(i, 1);
            c.parentNode = null;
            return c;
        },
    };
}

prksDestroyAllTabContexts();

const pendingWarm = prksEnsureTabContext('pending-warm');
pendingWarm.mount(makeHost());
pendingWarm.setResource('pdf', {
    hasPendingSync: function () { return true; },
});
assert('pending PDF warm-suspends', prksWarmParkTabContext(pendingWarm.tabId, makeHost()));
assertEq('global pending check sees warm PDF', prksHasPendingWorkAnnotationSync(), true);
prksUnmountTabContext(pendingWarm.tabId, 'cold-park');
assertEq('global pending check ignores cold PDF', prksHasPendingWorkAnnotationSync(), false);
prksDestroyAllTabContexts();

const ctxA = prksEnsureTabContext('pdf-a');
const ctxB = prksEnsureTabContext('pdf-b');
ctxA.mount(makeHost());
ctxB.mount(makeHost());
ctxA.beginRoute({ name: 'work', hash: '#/works/WA' });
ctxB.beginRoute({ name: 'work', hash: '#/works/WB' });

let flushAnnA = 0;
let flushAnnB = 0;
let flushPageA = 0;
let flushPageB = 0;

const rtA = createWorkPdfRuntime({
    workId: 'WA',
    flushAnnotations: async function () {
        flushAnnA += 1;
    },
});
const rtB = createWorkPdfRuntime({
    workId: 'WB',
    flushAnnotations: async function () {
        flushAnnB += 1;
    },
});
const origFlushPageA = rtA.flushLastPage.bind(rtA);
rtA.flushLastPage = function () {
    flushPageA += 1;
    origFlushPageA();
};
const origFlushPageB = rtB.flushLastPage.bind(rtB);
rtB.flushLastPage = function () {
    flushPageB += 1;
    origFlushPageB();
};

rtA.pageSession = { workId: 'WA', pageNumber: 3, totalPages: 10 };
rtB.pageSession = { workId: 'WB', pageNumber: 7, totalPages: 20 };
rtA.annotationCache = {
    allItems: [{ id: 'ann-a', pageIndex: 0, contents: 'A note' }],
    rawItems: [{ id: 'ann-a', pageIndex: 0, contents: 'A note' }],
    items: [{ id: 'ann-a', pageIndex: 0, contents: 'A note' }],
    docId: 'doc-a',
    workId: 'WA',
};
rtB.annotationCache = {
    allItems: [{ id: 'ann-b', pageIndex: 1, contents: 'B note' }],
    rawItems: [{ id: 'ann-b', pageIndex: 1, contents: 'B note' }],
    items: [{ id: 'ann-b', pageIndex: 1, contents: 'B note' }],
    docId: 'doc-b',
    workId: 'WB',
};
rtA.syncState.pendingChanges = false;
rtB.syncState.pendingChanges = true;

ctxA.setResource('pdf', rtA, function () {
    rtA.destroy();
});
ctxB.setResource('pdf', rtB, function () {
    rtB.destroy();
});

assert('cache A != B', ctxA.getResource('pdf').annotationCache !== ctxB.getResource('pdf').annotationCache);
assertEq('cache A work', ctxA.getResource('pdf').annotationCache.workId, 'WA');
assertEq('cache B work', ctxB.getResource('pdf').annotationCache.workId, 'WB');
assertEq('page A', ctxA.getResource('pdf').pageSession.pageNumber, 3);
assertEq('page B', ctxB.getResource('pdf').pageSession.pageNumber, 7);
assert('page session A != B', ctxA.getResource('pdf').pageSession !== ctxB.getResource('pdf').pageSession);

void ctxA.getResource('pdf').flushAnnotations();
assertEq('flush A annotations once', flushAnnA, 1);
assertEq('flush B annotations untouched', flushAnnB, 0);

assertEq('pending A', prksHasPendingWorkAnnotationSync(ctxA), false);
assertEq('pending B', prksHasPendingWorkAnnotationSync(ctxB), true);
assertEq('pending any live', prksHasPendingWorkAnnotationSync(), true);

let visited = 0;
prksForEachLiveTabContext(function (ctx) {
    const pdf = ctx.getResource('pdf');
    if (pdf && typeof pdf.flushLastPage === 'function') {
        pdf.flushLastPage();
        visited += 1;
    }
});
assertEq('live flush visits both mounted PDFs', visited, 2);
assertEq('flush last A', flushPageA, 1);
assertEq('flush last B', flushPageB, 1);

let queuedRetry = [];
let flushFromRetryA = 0;
let flushFromRetryB = 0;
const workerA = createPdfAnnotationPersistenceWorker({
    runtime: rtA,
    retryDelayMs: 1,
    schedule: function (fn) {
        queuedRetry.push(fn);
        return queuedRetry.length;
    },
    unschedule: function () {},
    onFlush: function () {
        flushFromRetryA += 1;
    },
});
const workerB = createPdfAnnotationPersistenceWorker({
    runtime: rtB,
    retryDelayMs: 1,
    schedule: function (fn) {
        queuedRetry.push(fn);
        return queuedRetry.length;
    },
    unschedule: function () {},
    onFlush: function () {
        flushFromRetryB += 1;
    },
});
rtA.annotationPersistence = workerA;
rtB.annotationPersistence = workerB;
workerA.scheduleRetry();
assert('A retry timer armed', workerA.retryTimer != null);
rtA.destroy();
assert('A worker destroyed', workerA.destroyed === true);
queuedRetry.slice().forEach(function (fn) {
    fn();
});
assertEq('destroyed A retry does not flush', flushFromRetryA, 0);
assertEq('B retry flush untouched', flushFromRetryB, 0);
assertEq('B pending still true after A retry fire', prksHasPendingWorkAnnotationSync(ctxB), true);

const genBeforeInstall = ctxA.generation;
let installedAfterStale = 0;
ctxA.beginRoute({ name: 'work', hash: '#/works/WA-stale' });
const staleRt = createWorkPdfRuntime({ workId: 'WA-stale' });
assert(
    'stale live check false',
    prksPdfPersistenceStillLive(ctxA, genBeforeInstall, staleRt) === false
);
prksInstallPdfAnnotationPersistenceIfCurrent(ctxA, genBeforeInstall, staleRt, function () {
    installedAfterStale += 1;
    staleRt.annotationPersistence = { attached: true };
});
assertEq('stale GET does not install persistence', installedAfterStale, 0);
assert('stale runtime has no worker', staleRt.annotationPersistence == null);

(async function () {
    function fakeAnnotationGetThenInstall(ctx, generation, runtime) {
        return Promise.resolve({ annotations_json: '[]' }).then(function () {
            if (!prksPdfPersistenceStillLive(ctx, generation, runtime)) return 'stale';
            let attached = false;
            prksInstallPdfAnnotationPersistenceIfCurrent(ctx, generation, runtime, function () {
                attached = true;
                runtime.annotationPersistence = { attached: true };
                runtime.viewerOn = true;
            });
            return attached ? 'installed' : 'skipped';
        });
    }

    const genDuringGet = ctxA.generation;
    const pendingRt = createWorkPdfRuntime({ workId: 'WA-get' });
    ctxA.setResource('pdf', pendingRt, function () {
        pendingRt.destroy();
    });
    const getInFlight = fakeAnnotationGetThenInstall(ctxA, genDuringGet, pendingRt);
    ctxA.beginRoute({ name: 'work', hash: '#/works/WA-after-get' });
    const getResult = await getInFlight;
    assertEq('stale GET resolve does not attach worker', getResult, 'stale');
    assert('pending runtime has no listener after stale GET', pendingRt.viewerOn !== true);
    assert('pending runtime has no persistence after stale GET', pendingRt.annotationPersistence == null);

    const rtLive = createWorkPdfRuntime({ workId: 'WA-live' });
    ctxA.setResource('pdf', rtLive, function () {
        rtLive.destroy();
    });
    let installedLive = 0;
    prksInstallPdfAnnotationPersistenceIfCurrent(ctxA, ctxA.generation, rtLive, function () {
        installedLive += 1;
    });
    assertEq('current ctx installs persistence', installedLive, 1);

    const genA = ctxA.generation;
    let lateDestroyed = false;
    const lateViewer = {
        destroy: function () {
            lateDestroyed = true;
        },
    };
    ctxA.beginRoute({ name: 'work', hash: '#/works/WA2' });
    assert('A generation advanced', ctxA.generation !== genA);
    assert('A pdf disposed', ctxA.getResource('pdf') === undefined);
    assertEq('B pdf still live after A dispose', ctxB.getResource('pdf').workId, 'WB');
    if (!ctxA.isCurrent(genA) || ctxA.getResource('pdf') !== rtA) {
        lateViewer.destroy();
    }
    assert('late A viewer destroyed', lateDestroyed);
    assertEq('B pending still true', prksHasPendingWorkAnnotationSync(ctxB), true);
    assertEq('B cache still B', ctxB.getResource('pdf').annotationCache.workId, 'WB');

    prksDestroyAllTabContexts();

    if (failed) {
        console.log(failed + ' failed, ' + passed + ' passed');
        process.exit(1);
    }
    console.log('All ' + passed + ' PDF runtime isolation checks passed');
})().catch(function (err) {
    console.error(err && err.stack ? err.stack : err);
    process.exit(1);
});
