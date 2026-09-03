#!/usr/bin/env node
'use strict';

const path = require('path');

const rootDir = path.resolve(__dirname, '../..');
const tc = require(path.join(rootDir, 'frontend/js/tab-context.js'));
const pdfRt = require(path.join(rootDir, 'frontend/js/pdf-work-runtime.js'));

const {
    prksEnsureTabContext,
    prksDestroyAllTabContexts,
    prksForEachMountedTabContext,
} = tc;
const { createWorkPdfRuntime, prksHasPendingWorkAnnotationSync } = pdfRt;

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
assertEq('pending any mounted', prksHasPendingWorkAnnotationSync(), true);

let visited = 0;
prksForEachMountedTabContext(function (ctx) {
    const pdf = ctx.getResource('pdf');
    if (pdf && typeof pdf.flushLastPage === 'function') {
        pdf.flushLastPage();
        visited += 1;
    }
});
assertEq('visibility flush visits both', visited, 2);
assertEq('flush last A', flushPageA, 1);
assertEq('flush last B', flushPageB, 1);

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
