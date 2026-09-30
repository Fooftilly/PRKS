#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const rootDir = path.resolve(__dirname, '../..');
const tabContext = require(path.join(rootDir, 'frontend/js/tab-context.js'));
const pdfRuntime = require(path.join(rootDir, 'frontend/js/pdf-work-runtime.js'));

const {
    prksEnsureTabContext,
    prksMountTabContext,
    prksDestroyTabContext,
    prksDestroyAllTabContexts,
    prksWarmParkTabContext,
    prksResumeWarmTabContext,
    prksUnmountTabContext,
    prksIsMainTabContext,
} = tabContext;
const { createWorkPdfRuntime, prksHasPendingWorkAnnotationSync } = pdfRuntime;

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

const requests = [];
const viewers = [];
let viewerSeq = 0;

global.window = global;
global.localStorage = {
    getItem: function () { return null; },
    setItem: function () {},
    removeItem: function () {},
};
global.document = {
    visibilityState: 'visible',
    getElementById: function () { return null; },
};
global.prksPdfCacheInstallOutcome = function () { return null; };
global.prksPostPdfCacheInstall = function () {};
global.__prksPdfCacheInstallOutcome = global.prksPdfCacheInstallOutcome;
global.__prksPostPdfCacheInstall = global.prksPostPdfCacheInstall;
global.prksRequest = function (url, opts, extra) {
    requests.push({
        url: String(url),
        method: opts && opts.method ? String(opts.method) : 'GET',
        priority: extra && extra.priority ? extra.priority : '',
    });
    return Promise.resolve({ ok: true });
};
global.__createPrksPdfViewer = function (opts) {
    const viewer = {
        id: ++viewerSeq,
        target: opts.target,
        src: opts.src,
        resized: 0,
        destroyed: false,
        resize: function () { viewer.resized += 1; },
        destroy: function () { viewer.destroyed = true; },
        setMutationEnabled: function () {},
    };
    viewers.push(viewer);
    return Promise.resolve(viewer);
};

const timers = [];
let timerSeq = 1;
global.setTimeout = function (fn) {
    const id = timerSeq++;
    timers.push({ id: id, fn: fn, dead: false });
    return id;
};
global.clearTimeout = function (id) {
    for (let i = 0; i < timers.length; i++) {
        if (timers[i].id === id) timers[i].dead = true;
    }
};
global.setInterval = function () { return 0; };
global.clearInterval = function () {};
global.requestAnimationFrame = function (fn) {
    fn();
    return 1;
};

function flushLiveTimers() {
    const due = timers.splice(0, timers.length).filter(function (timer) { return !timer.dead; });
    due.forEach(function (timer) { timer.fn(); });
    return due.length;
}

function resetWorld() {
    prksDestroyAllTabContexts();
    requests.length = 0;
    viewers.length = 0;
    timers.length = 0;
}

function hostBox() {
    const kids = [];
    return {
        children: kids,
        appendChild: function (child) {
            kids.push(child);
            child.parentNode = this;
            return child;
        },
        removeChild: function (child) {
            const index = kids.indexOf(child);
            if (index >= 0) kids.splice(index, 1);
            child.parentNode = null;
            return child;
        },
    };
}

function openTab(tabId) {
    const ctx = prksEnsureTabContext(tabId);
    prksMountTabContext(tabId, hostBox());
    ctx.beginRoute({ name: 'work', hash: '#/works/' + tabId });
    const node = { innerHTML: 'placeholder', tabId: tabId };
    ctx.root.querySelector = function (selector) {
        if (String(selector).indexOf('pdf-viewer') !== -1) return node;
        return null;
    };
    return { ctx: ctx, node: node };
}

let pdfSource = fs.readFileSync(path.join(rootDir, 'frontend/js/components/works-pdf.js'), 'utf8');
pdfSource = pdfSource.replace(
    "import { prksPdfCacheInstallOutcome, prksPostPdfCacheInstall } from '/js/pdf-cache-install.js';\nimport { createPrksPdfViewer } from '/js/pdf-viewer-runtime.js';\n",
    'const prksPdfCacheInstallOutcome = globalThis.__prksPdfCacheInstallOutcome;\n' +
        'const prksPostPdfCacheInstall = globalThis.__prksPostPdfCacheInstall;\n' +
        'const createPrksPdfViewer = globalThis.__createPrksPdfViewer;\n'
);
pdfSource = pdfSource.replace('export function initPdfViewerForWork', 'function initPdfViewerForWork');
vm.runInThisContext(pdfSource, { filename: 'works-pdf.js' });

async function settle() {
    for (let i = 0; i < 8; i++) await Promise.resolve();
}

function annotationPosts() {
    return requests.filter(function (row) {
        return row.url.indexOf('/annotations') !== -1 || row.method === 'POST';
    });
}

(async function () {
    const legacyPost = pdfSource.indexOf('`/api/works/${workId}/annotations`') !== -1
        && pdfSource.indexOf("method: 'POST'") !== -1;
    assert('legacy annotation POST path is still in works-pdf.js', legacyPost);
    assert('init still installs the pdf resource', pdfSource.indexOf("ctx.setResource('pdf', runtime, function () {") !== -1);

    resetWorld();
    const current = openTab('current');
    initPdfViewerForWork(current.ctx, { id: 'work-current', file_path: '/api/pdfs/current' });
    assertEq('current setup arms one timer', flushLiveTimers(), 1);
    await settle();
    const currentRuntime = current.ctx.getResource('pdf');
    assert('current generation mounts a runtime', !!currentRuntime);
    assertEq('current runtime work', currentRuntime && currentRuntime.workId, 'work-current');
    assertEq('current host is cleared for the viewer', current.node.innerHTML, '');
    assertEq('current viewer count', viewers.length, 1);
    assert('current viewer is mounted in this pane', viewers[0] && viewers[0].target === current.node);
    assertEq('current file prime', requests.length, 1);
    assertEq('current file prime url', requests[0] && requests[0].url, '/api/pdfs/current');
    assertEq('current file prime priority', requests[0] && requests[0].priority, 'background');
    assertEq('current mount does not post annotations', annotationPosts().length, 0);
    const currentViewer = currentRuntime && currentRuntime.viewer;
    assert('current viewer is the created viewer', currentViewer === viewers[0]);

    resetWorld();
    const stale = openTab('stale');
    const generation = stale.ctx.generation;
    initPdfViewerForWork(stale.ctx, { id: 'work-a', file_path: '/api/pdfs/a' });
    assertEq('stale setup queued', timers.length, 1);
    stale.ctx.beginRoute({ name: 'work', hash: '#/works/b' });
    assert('stale generation moved', stale.ctx.generation !== generation);
    assert('deferred timer is cancelled', timers.length === 1 && timers[0].dead);
    assertEq('cancelled timer does not mount', flushLiveTimers(), 0);
    assertEq('no viewer after cancelled A to B', viewers.length, 0);
    assert('no pdf resource after cancelled A to B', stale.ctx.getResource('pdf') == null);
    assertEq('host untouched when the timer never runs', stale.node.innerHTML, 'placeholder');

    const late = openTab('late');
    initPdfViewerForWork(late.ctx, { id: 'work-a', file_path: '/api/pdfs/a' });
    const lateTimer = timers[timers.length - 1];
    global.clearTimeout = function () {};
    late.ctx.beginRoute({ name: 'work', hash: '#/works/b' });
    lateTimer.fn();
    await settle();
    global.clearTimeout = function (id) {
        for (let i = 0; i < timers.length; i++) {
            if (timers[i].id === id) timers[i].dead = true;
        }
    };
    assertEq('stale callback does not create a viewer', viewers.length, 0);
    assert('stale callback does not install a pdf resource', late.ctx.getResource('pdf') == null);
    assertEq('stale callback leaves the host', late.node.innerHTML, 'placeholder');

    resetWorld();
    global.prksWorkspaceSnapshot = function () {
        return { mainTabId: 'main', focusedTabId: 'main' };
    };
    const main = openTab('main');
    const side = openTab('side');
    initPdfViewerForWork(main.ctx, { id: 'work-main', file_path: '/api/pdfs/main' });
    initPdfViewerForWork(side.ctx, { id: 'work-side', file_path: '/api/pdfs/side' });
    flushLiveTimers();
    await settle();
    assert('main is the main context', prksIsMainTabContext(main.ctx));
    assert('secondary is not the main context', !prksIsMainTabContext(side.ctx));
    assert('pane hosts differ', main.node !== side.node);
    assert('main viewer uses the main host', viewers[0] && viewers[0].target === main.node);
    assert('secondary viewer uses the secondary host', viewers[1] && viewers[1].target === side.node);
    const mainRuntime = main.ctx.getResource('pdf');
    const sideRuntime = side.ctx.getResource('pdf');
    assert('main and secondary runtimes differ', mainRuntime && sideRuntime && mainRuntime !== sideRuntime);
    assertEq('main work id', mainRuntime.workId, 'work-main');
    assertEq('secondary work id', sideRuntime.workId, 'work-side');
    const sideViewer = sideRuntime.viewer;
    prksDestroyTabContext('side');
    assert('secondary close destroys its viewer', sideViewer && sideViewer.destroyed);
    assert('main runtime remains after secondary close', main.ctx.getResource('pdf') === mainRuntime);
    assert('main viewer remains', mainRuntime.viewer && mainRuntime.viewer.destroyed !== true);

    resetWorld();
    const routed = openTab('routed');
    initPdfViewerForWork(routed.ctx, { id: 'work-routed', file_path: '/api/pdfs/routed' });
    flushLiveTimers();
    await settle();
    const routedRuntime = routed.ctx.getResource('pdf');
    const events = [];
    const originalFlush = routedRuntime.flushLastPage.bind(routedRuntime);
    routedRuntime.flushLastPage = function () {
        events.push('flush');
        originalFlush();
    };
    const originalDestroy = routedRuntime.destroy.bind(routedRuntime);
    routedRuntime.destroy = function () {
        events.push('destroy');
        originalDestroy();
    };
    const app = fs.readFileSync(path.join(rootDir, 'frontend/js/app.js'), 'utf8');
    const flushAt = app.indexOf("const prevPdf = ctx.getResource && ctx.getResource('pdf');");
    const flushEnd = app.indexOf('prksMaybeFlushPdfLastPageOnRouteChange', flushAt);
    const flushRoute = new Function('ctx', app.slice(flushAt, flushEnd) + '\nreturn true;');
    flushRoute(routed.ctx);
    assertEq('route replacement flushes before teardown', events.join(','), 'flush');
    routed.ctx.beginRoute({ name: 'folder', hash: '#/folders' });
    assert('teardown runs after the flush', events[0] === 'flush' && events.indexOf('destroy') > 0);
    assert('route replacement clears the pdf resource', routed.ctx.getResource('pdf') == null);
    assert('route replacement destroys the viewer', routedRuntime.viewer == null || viewers.some(function (viewer) {
        return viewer.destroyed;
    }));

    resetWorld();
    const closing = openTab('closing');
    initPdfViewerForWork(closing.ctx, { id: 'work-close', file_path: '/api/pdfs/close' });
    flushLiveTimers();
    await settle();
    const closingViewer = closing.ctx.getResource('pdf').viewer;
    prksDestroyTabContext('closing');
    assert('tab close destroys the viewer', closingViewer.destroyed === true);
    assert('tab close drops the context', tabContext.prksGetTabContext('closing') == null);

    resetWorld();
    const cold = openTab('cold');
    initPdfViewerForWork(cold.ctx, { id: 'work-cold', file_path: '/api/pdfs/cold' });
    flushLiveTimers();
    await settle();
    const coldViewer = cold.ctx.getResource('pdf').viewer;
    const coldRoot = cold.ctx.root;
    prksUnmountTabContext('cold', 'park');
    assert('cold park destroys the viewer', coldViewer.destroyed === true);
    assert('cold park clears the pdf resource', cold.ctx.getResource('pdf') == null);
    assert('cold park drops the host', cold.ctx.root == null);
    assert('cold park is not a warm suspend', cold.ctx.suspended !== true);
    assert('cold park detached the root', coldRoot.parentNode == null);

    resetWorld();
    const warm = openTab('warm');
    initPdfViewerForWork(warm.ctx, { id: 'work-warm', file_path: '/api/pdfs/warm' });
    flushLiveTimers();
    await settle();
    const warmRuntime = warm.ctx.getResource('pdf');
    const warmViewer = warmRuntime.viewer;
    const warmRoot = warm.ctx.root;
    const parking = hostBox();
    assert('warm park keeps the runtime', prksWarmParkTabContext('warm', parking) === true);
    assert('warm park keeps the same runtime object', warm.ctx.getResource('pdf') === warmRuntime);
    assert('warm park keeps the viewer', warmRuntime.viewer === warmViewer && warmViewer.destroyed !== true);
    assert('warm park keeps the host element', warm.ctx.root === warmRoot);
    assert('warm park moves the host into parking', warmRoot.parentNode === parking);
    const viewersBeforeResume = viewers.length;
    const visible = hostBox();
    const resumed = prksResumeWarmTabContext('warm', visible);
    assert('warm resume returns the same context', resumed === warm.ctx);
    assert('warm resume keeps the same viewer', warm.ctx.getResource('pdf').viewer === warmViewer);
    assertEq('warm resume does not create a viewer', viewers.length, viewersBeforeResume);
    assertEq('warm resume resizes the existing viewer', warmViewer.resized, 1);
    assert('warm resume places the same host', warm.ctx.root === warmRoot && warmRoot.parentNode === visible);

    resetWorld();
    const parked = [];
    for (let i = 1; i <= 4; i++) {
        const opened = openTab('warm-' + i);
        initPdfViewerForWork(opened.ctx, { id: 'work-' + i, file_path: '/api/pdfs/' + i });
        parked.push(opened);
    }
    flushLiveTimers();
    await settle();
    const evictedRuntime = parked[0].ctx.getResource('pdf');
    const evictedViewer = evictedRuntime.viewer;
    for (let i = 0; i < parked.length; i++) {
        assert('park ' + (i + 1), prksWarmParkTabContext(parked[i].ctx.tabId, hostBox()) === true);
    }
    assert('bounded eviction destroys the evicted viewer', evictedViewer.destroyed === true);
    assert('bounded eviction clears the evicted runtime', parked[0].ctx.getResource('pdf') == null);
    assert('bounded eviction is not still suspended', parked[0].ctx.suspended !== true);
    for (let i = 1; i < parked.length; i++) {
        assert('survivor ' + (i + 1) + ' keeps its runtime', parked[i].ctx.getResource('pdf') != null);
        assert('survivor ' + (i + 1) + ' viewer stays', parked[i].ctx.getResource('pdf').viewer.destroyed !== true);
    }

    resetWorld();
    const first = openTab('offline');
    initPdfViewerForWork(first.ctx, { id: 'work-offline', file_path: '/api/pdfs/offline' });
    flushLiveTimers();
    await settle();
    assertEq('first open primes the file', requests[0] && requests[0].url, '/api/pdfs/offline');
    prksDestroyTabContext('offline');
    requests.length = 0;
    const viewerCount = viewers.length;
    global.prksOfflineRuntimeState = function () { return 'offline'; };
    const again = openTab('offline-again');
    initPdfViewerForWork(again.ctx, { id: 'work-offline', file_path: '/api/pdfs/offline' });
    flushLiveTimers();
    await settle();
    delete global.prksOfflineRuntimeState;
    assertEq('offline reopen primes the same file', requests.length, 1);
    assertEq('offline reopen url', requests[0] && requests[0].url, '/api/pdfs/offline');
    assertEq('offline reopen method', requests[0] && requests[0].method, 'GET');
    assertEq('offline reopen is not an annotation post', annotationPosts().length, 0);
    assert('offline reopen creates a viewer', viewers.length === viewerCount + 1);
    assert('offline reopen viewer loads that file', String(viewers[viewers.length - 1].src).indexOf('/api/pdfs/offline') === 0);

    resetWorld();
    const leaving = openTab('pending');
    const pendingRuntime = createWorkPdfRuntime({ workId: 'work-pending' });
    leaving.ctx.setResource('pdf', pendingRuntime, function () { pendingRuntime.destroy(); });
    pendingRuntime.syncState.pendingChanges = true;
    leaving.ctx.lastResolvedRoute = { name: 'work', canonicalHash: '#/works/pending', hash: '#/works/pending' };
    global.prksParseRoute = function (hash) {
        const value = String(hash || '');
        if (value.indexOf('#/works/') === 0) return { name: 'work', canonicalHash: value, hash: value };
        return { name: 'folders', canonicalHash: value, hash: value };
    };
    global.prksCanLeaveTabContextOwnedDraft = function () { return true; };
    const leaveStart = app.indexOf('function prksCanLeaveTabContext(ctx, nextHash)');
    const leaveEnd = app.indexOf('function prksCanLeaveTabContextOwnedDraft', leaveStart);
    vm.runInThisContext(app.slice(leaveStart, leaveEnd), { filename: 'app-leave.js' });
    const prompts = [];
    global.confirm = function (message) {
        prompts.push(message);
        return false;
    };
    assertEq('pending sync blocks leave', prksCanLeaveTabContext(leaving.ctx, '#/folders'), false);
    assertEq('pending sync confirm count', prompts.length, 1);
    assert(
        'pending sync uses the existing confirm',
        prompts[0] === 'PDF annotation sync still running. Leave page before all changes save to server?'
    );
    assertEq('helper sees the pending runtime', prksHasPendingWorkAnnotationSync(leaving.ctx), true);
    global.confirm = function (message) {
        prompts.push(message);
        return true;
    };
    assertEq('approved pending sync may leave', prksCanLeaveTabContext(leaving.ctx, '#/folders'), true);
    pendingRuntime.syncState.pendingChanges = false;
    const promptsBeforeClear = prompts.length;
    assertEq('settled sync does not confirm', prksCanLeaveTabContext(leaving.ctx, '#/folders'), true);
    assertEq('settled sync adds no prompt', prompts.length, promptsBeforeClear);

    const pendingCall = 'window.prksHasPendingWorkAnnotationSync(ctx)';
    const renderCall = app.indexOf(pendingCall, app.indexOf(pendingCall) + 1);
    const renderIf = app.lastIndexOf('if (', renderCall);
    const renderFn = new Function(
        'ctx',
        'leavingWorkPage',
        app.slice(renderIf, app.indexOf('const draftLeaveApproved', renderCall)) + '\nreturn null;'
    );
    pendingRuntime.syncState.pendingChanges = true;
    prompts.length = 0;
    global.confirm = function () { prompts.push('render'); return false; };
    const cancelled = renderFn(leaving.ctx, true);
    assertEq('route leave cancel reason', cancelled && cancelled.reason, 'pending-sync');
    assertEq('route leave confirm ran', prompts.length, 1);

    console.log((failed ? 'FAILED ' : 'OK ') + passed + ' passed, ' + failed + ' failed');
    process.exit(failed ? 1 : 0);
})().catch(function (err) {
    console.error(err && err.stack ? err.stack : err);
    process.exit(1);
});
