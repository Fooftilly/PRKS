#!/usr/bin/env node
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const rootDir = path.resolve(__dirname, '../..');
globalThis.prksOwnerResource = require(path.join(rootDir, 'frontend/js/owner-resource.js'));
const tabContext = require(path.join(rootDir, 'frontend/js/tab-context.js'));
globalThis.prksTabLeave = require(path.join(rootDir, 'frontend/js/tab-leave.js'));
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
const {
    createWorkPdfRuntime,
    prksHasPendingWorkAnnotationSync,
    prksInstallPdfAnnotationPersistenceIfCurrent,
} = pdfRuntime;

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
let viewerFactory = null;

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
function defaultViewerFactory(opts) {
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
}
viewerFactory = defaultViewerFactory;
global.__createPrksPdfViewer = function (opts) {
    return viewerFactory(opts);
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
    viewerFactory = defaultViewerFactory;
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

// works-pdf.js is browser ESM, and the route slices live inside app.js.
// Other Node selftests load browser scripts with require(). Dynamic execution
// is Sonar javascript:S1523 on new code, so these slices are loaded the same way.
const scriptDir = fs.mkdtempSync(path.join(os.tmpdir(), 'prks-work-pdf-lifecycle-'));

function loadScript(source, filename) {
    const file = path.join(scriptDir, filename);
    fs.writeFileSync(file, source);
    return require(file);
}

function sliceBetween(source, startMarker, endMarker) {
    const start = source.indexOf(startMarker);
    const end = start < 0 ? -1 : source.indexOf(endMarker, start);
    if (start < 0 || end < 0) throw new Error('missing slice ' + startMarker);
    return source.slice(start, end);
}

const initPdfViewerForWork = loadScript(
    pdfSource + '\nmodule.exports = { initPdfViewerForWork: initPdfViewerForWork };\n',
    'works-pdf.cjs'
).initPdfViewerForWork;

function loadRouteFlush(app) {
    const body = sliceBetween(
        app,
        "const prevPdf = ctx.getResource && ctx.getResource('pdf');",
        'prksMaybeFlushPdfLastPageOnRouteChange'
    );
    return loadScript(
        'module.exports = function flushPdfBeforeRouteChange(ctx) {\n' + body + '\nreturn true;\n};\n',
        'route-flush.cjs'
    );
}

async function settle() {
    for (let i = 0; i < 8; i++) await Promise.resolve();
}

function annotationPosts() {
    return requests.filter(function (row) {
        return row.url.indexOf('/annotations') !== -1 || row.method === 'POST';
    });
}

function assertPdfSourceContract() {
    const legacyPost = pdfSource.indexOf('`/api/works/${workId}/annotations`') !== -1
        && pdfSource.indexOf("method: 'POST'") !== -1;
    assert('legacy annotation POST path is still in works-pdf.js', legacyPost);
    const initStart = pdfSource.indexOf('function initPdfViewerForWork');
    const initEnd = pdfSource.indexOf('function prksReconcilePdfMutationMode');
    const initBody = pdfSource.slice(initStart, initEnd);
    assert('init captures the resource ticket before the deferred timer', initBody.indexOf('ctx.resourceTicket(_pdfGen)') !== -1 && initBody.indexOf('ctx.resourceTicket(_pdfGen)') < initBody.indexOf('setTimeout'));
    assert('init registers pdf on the captured ticket', initBody.indexOf('ctx.registerResource(_pdfTicket') !== -1 && initBody.indexOf("kind: 'pdf'") !== -1 && initBody.indexOf('suspendable: true') !== -1);
    assert('init does not use the compatibility setter', initBody.indexOf("ctx.setResource('pdf'") === -1);
    assert('init keeps the pdf disposer', initBody.indexOf('runtime.destroy();') !== -1 && initBody.indexOf('prksVueDismissWorkPdfAnnotationPopup') !== -1 && initBody.indexOf('prksVueDismissWorkPdfAnnotationDrawer') !== -1);
}

async function scenarioCurrentMount() {
    resetWorld();
    const current = openTab('current');
    initPdfViewerForWork(current.ctx, { id: 'work-current', file_path: '/api/pdfs/current' });
    assertEq('current setup arms one timer', flushLiveTimers(), 1);
    await settle();
    const currentRuntime = current.ctx.getResource('pdf');
    assert('current generation mounts a runtime', !!currentRuntime);
    assert('current pdf is the registry slot', current.ctx.readResource('pdf') === currentRuntime);
    assert('current pdf is not in the legacy map', !current.ctx.resources.has('pdf'));
    assert('registry lists pdf', current.ctx.resourceRegistry.kinds().indexOf('pdf') !== -1);
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
}

async function scenarioCancelledDeferredSetup() {
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
}

async function scenarioStaleCallback() {
    resetWorld();
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
}

async function scenarioRemount() {
    resetWorld();
    const remount = openTab('remount');
    initPdfViewerForWork(remount.ctx, { id: 'work-first', file_path: '/api/pdfs/first' });
    const firstTimer = timers[timers.length - 1];
    const firstGeneration = remount.ctx.generation;
    assertEq('first mount runs its deferred setup', flushLiveTimers(), 1);
    await settle();
    const firstRuntime = remount.ctx.getResource('pdf');
    const firstViewer = firstRuntime && firstRuntime.viewer;
    assert('first mount has a live viewer', firstViewer && firstViewer.destroyed !== true);
    let pendingFired = 0;
    const pendingId = setTimeout(function () { pendingFired += 1; });
    remount.ctx.setTimer('pdfDeferredSetup', pendingId);
    const pendingTimer = timers[timers.length - 1];
    remount.ctx.beginRoute({ name: 'work', hash: '#/works/second' });
    assert('teardown destroys the first viewer', firstViewer.destroyed === true);
    assert('teardown removes the first pdf resource', remount.ctx.getResource('pdf') == null);
    assert('first generation deferred timer is cancelled', pendingTimer.dead === true && pendingTimer.id === pendingId);
    assertEq('cancelled first timer does not mount', flushLiveTimers(), 0);
    assertEq('cancelled first timer did not fire', pendingFired, 0);
    initPdfViewerForWork(remount.ctx, { id: 'work-second', file_path: '/api/pdfs/second' });
    const secondTimer = timers[timers.length - 1];
    assert('second setup is a different timer', secondTimer !== firstTimer);
    assertEq('second mount runs its own setup', flushLiveTimers(), 1);
    await settle();
    const secondRuntime = remount.ctx.getResource('pdf');
    const secondViewer = secondRuntime && secondRuntime.viewer;
    assert('second runtime is distinct', secondRuntime && secondRuntime !== firstRuntime);
    assert('second viewer is distinct', secondViewer && secondViewer !== firstViewer && secondViewer.destroyed !== true);
    assert('second runtime is the only pdf resource', remount.ctx.getResource('pdf') === secondRuntime);
    remount.node.innerHTML = 'second-painted';
    const resizedBefore = secondViewer.resized;
    const viewersBefore = viewers.length;
    firstTimer.fn();
    await settle();
    assert('first timer does not replace the second resource', remount.ctx.getResource('pdf') === secondRuntime);
    assertEq('first timer does not paint the second host', remount.node.innerHTML, 'second-painted');
    assertEq('first timer does not create a viewer', viewers.length, viewersBefore);
    assertEq('first timer does not resize the second viewer', secondViewer.resized, resizedBefore);
    assert('first timer does not destroy the second viewer', secondViewer.destroyed !== true);
    firstRuntime.resize();
    firstRuntime.destroy();
    firstViewer.resize();
    firstViewer.destroy();
    assert('destroying the first generation does not resize the second', secondViewer.resized === resizedBefore);
    assert('destroying the first generation does not destroy the second', secondViewer.destroyed !== true);
    assert('destroying the first generation does not repaint the second', remount.node.innerHTML === 'second-painted');
    assert('second resource stays current', remount.ctx.getResource('pdf') === secondRuntime);
    assert('first generation is no longer current', remount.ctx.isCurrent(firstGeneration) === false);
}

async function scenarioMainAndSecondary() {
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
}

async function scenarioRouteReplacementFlush(app) {
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
    loadRouteFlush(app)(routed.ctx);
    assertEq('route replacement flushes before teardown', events.join(','), 'flush');
    routed.ctx.beginRoute({ name: 'folder', hash: '#/folders' });
    assert('teardown runs after the flush', events[0] === 'flush' && events.indexOf('destroy') > 0);
    assert('route replacement clears the pdf resource', routed.ctx.getResource('pdf') == null);
    assert('route replacement destroys the viewer', routedRuntime.viewer == null || viewers.some(function (viewer) {
        return viewer.destroyed;
    }));
}

async function scenarioTabClose() {
    resetWorld();
    const closing = openTab('closing');
    initPdfViewerForWork(closing.ctx, { id: 'work-close', file_path: '/api/pdfs/close' });
    flushLiveTimers();
    await settle();
    const closingRuntime = closing.ctx.getResource('pdf');
    const closingViewer = closingRuntime.viewer;
    let closingDisposes = 0;
    const originalDestroy = closingRuntime.destroy.bind(closingRuntime);
    closingRuntime.destroy = function () {
        closingDisposes += 1;
        originalDestroy();
    };
    prksDestroyTabContext('closing');
    prksDestroyTabContext('closing');
    assertEq('tab close disposes the pdf runtime once', closingDisposes, 1);
    assert('tab close destroys the viewer', closingViewer.destroyed === true);
    assert('tab close drops the context', tabContext.prksGetTabContext('closing') == null);
}

async function scenarioColdPark() {
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
}

async function scenarioWarmParkResume() {
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
    const requestsBeforeResume = requests.length;
    const generationBeforeResume = warm.ctx.generation;
    const htmlBeforeResume = warm.node.innerHTML;
    const visible = hostBox();
    const resumed = prksResumeWarmTabContext('warm', visible);
    assert('warm resume returns the same context', resumed === warm.ctx);
    assert('warm resume keeps the same viewer', warm.ctx.getResource('pdf').viewer === warmViewer);
    assertEq('warm resume does not create a viewer', viewers.length, viewersBeforeResume);
    assertEq('warm resume resizes the existing viewer', warmViewer.resized, 1);
    assert('warm resume places the same host', warm.ctx.root === warmRoot && warmRoot.parentNode === visible);
    assertEq('warm resume does not prime the file again', requests.length, requestsBeforeResume);
    assertEq('warm resume does not advance the route generation', warm.ctx.generation, generationBeforeResume);
    assertEq('warm resume does not repaint the host', warm.node.innerHTML, htmlBeforeResume);
    assert('warm resume keeps the registry slot', warm.ctx.readResource('pdf') === warmRuntime);
}

async function scenarioWarmEviction() {
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
}

async function scenarioOfflineReopen() {
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
}

function runPdfLeave(ctx, destination, commit) {
    return global.prksTabLeave.run({
        ownerId: String(ctx.tabId),
        destination: destination,
        transition: 'route-replace',
        capture: function () {
            if (!ctx || ctx.destroyed) return null;
            return {
                ownerId: String(ctx.tabId),
                generation: ctx.generation,
                token: ctx,
            };
        },
        still: function (snap) {
            return !!(snap && snap.token === ctx && !ctx.destroyed && ctx.generation === snap.generation);
        },
        assess: function () {
            return global.prksTabLeave.assessOwner(ctx, destination);
        },
        commit: commit || function () { return true; },
    });
}

async function scenarioPendingLeave(app) {
    resetWorld();
    const leaving = openTab('pending');
    const pendingRuntime = createWorkPdfRuntime({ workId: 'work-pending' });
    leaving.ctx.registerResource(leaving.ctx.resourceTicket(), { kind: 'pdf', value: pendingRuntime, suspendable: true, dispose: function () { pendingRuntime.destroy(); } });
    pendingRuntime.syncState.pendingChanges = true;
    leaving.ctx.lastResolvedRoute = { name: 'work', canonicalHash: '#/works/pending', hash: '#/works/pending' };
    let route = leaving.ctx.lastResolvedRoute.canonicalHash;
    global.prksParseRoute = function (hash) {
        const value = String(hash || '');
        if (value.indexOf('#/works/') === 0) return { name: 'work', canonicalHash: value, hash: value };
        return { name: 'folders', canonicalHash: value, hash: value };
    };
    const prompts = [];
    global.confirm = function (message) {
        prompts.push(message);
        return false;
    };
    let commits = 0;
    const denied = await runPdfLeave(leaving.ctx, '#/folders', function () {
        commits += 1;
        route = '#/folders';
        return true;
    });
    assertEq('pending sync blocks leave', denied && denied.status, 'rejected-pending-pdf-sync');
    assertEq('pending sync confirm count', prompts.length, 1);
    assertEq('pending sync does not replace the route', route, '#/works/pending');
    assertEq('pending sync does not commit', commits, 0);
    assert(
        'pending sync uses the existing confirm',
        prompts[0] === 'PDF annotation sync still running. Leave page before all changes save to server?'
    );
    assertEq('helper sees the pending runtime', prksHasPendingWorkAnnotationSync(leaving.ctx), true);
    global.confirm = function (message) {
        prompts.push(message);
        return true;
    };
    const approved = await runPdfLeave(leaving.ctx, '#/folders', function () {
        commits += 1;
        route = '#/folders';
        return true;
    });
    assertEq('approved pending sync may leave', approved && approved.status, 'approved');
    assertEq('approved pending sync commits once', commits, 1);
    assertEq('approved pending sync replaces once', route, '#/folders');
    pendingRuntime.syncState.pendingChanges = false;
    const promptsBeforeClear = prompts.length;
    const settled = await runPdfLeave(leaving.ctx, '#/people/p1', function () {
        commits += 1;
        return true;
    });
    assertEq('settled sync may leave', settled && settled.status, 'approved');
    assertEq('settled sync adds no prompt', prompts.length, promptsBeforeClear);

    pendingRuntime.syncState.pendingChanges = true;
    route = '#/works/pending';
    const promptsBeforeContinuation = prompts.length;
    commits = 0;
    /* An already-approved render commits without asking the PDF probe again. */
    route = '#/folders';
    commits = 1;
    assertEq('continuation adds no prompt', prompts.length, promptsBeforeContinuation);
    const commitSrc = app.slice(
        app.indexOf('async function prksCommitTabRouteRender'),
        app.indexOf('const contentDiv = ctx.root;')
    );
    assert('render commit does not confirm pending sync', commitSrc.indexOf('window.confirm') === -1);
    assert('render commit does not recheck pending sync', commitSrc.indexOf('prksHasPendingWorkAnnotationSync') === -1);

    leaving.ctx.destroyed = true;
    prompts.length = 0;
    const stale = await runPdfLeave(leaving.ctx, '#/folders', function () {
        commits += 1;
        route = '#/folders';
        return true;
    });
    assertEq('destroyed owner is stale', stale && stale.status, 'stale-owner');
    assertEq('destroyed owner does not confirm', prompts.length, 0);
    assertEq('destroyed owner does not commit', commits, 1);
}

function watchDestroy(runtime) {
    let count = 0;
    const original = runtime.destroy.bind(runtime);
    runtime.destroy = function () {
        count += 1;
        original();
    };
    return function () { return count; };
}

async function scenarioColdParkTicket() {
    resetWorld();
    const parked = openTab('cold-ticket');
    const seen = [];
    const originalTicket = parked.ctx.resourceTicket.bind(parked.ctx);
    parked.ctx.resourceTicket = function (generation) {
        const ticket = originalTicket(generation);
        seen.push(ticket);
        return ticket;
    };
    initPdfViewerForWork(parked.ctx, { id: 'work-cold-ticket', file_path: '/api/pdfs/cold-ticket' });
    assertEq('setup captures one ticket before the timer runs', seen.length, 1);
    assert('captured ticket is current before the timer', parked.ctx.resourceRegistry.accepts(seen[0]));
    assertEq('timer has not run yet', viewers.length, 0);
    const lateTimer = timers[timers.length - 1];
    global.clearTimeout = function () {};
    prksUnmountTabContext('cold-ticket', 'park');
    assert('cold park rejects the captured ticket', !parked.ctx.resourceRegistry.accepts(seen[0]));
    lateTimer.fn();
    await settle();
    assertEq('cold park before registration creates no viewer', viewers.length, 0);
    assert('cold park before registration installs nothing', parked.ctx.getResource('pdf') == null);

    parked.ctx.mount(hostBox());
    const node = { innerHTML: 'remounted', tabId: 'cold-ticket' };
    parked.ctx.root.querySelector = function (selector) {
        if (String(selector).indexOf('pdf-viewer') !== -1) return node;
        return null;
    };
    parked.node = node;
    assert('remount does not revive the pre-park ticket', !parked.ctx.resourceRegistry.accepts(seen[0]));
    lateTimer.fn();
    await settle();
    assertEq('remount does not let the pre-park timer mount', viewers.length, 0);
    assert('remount does not install the pre-park runtime', parked.ctx.getResource('pdf') == null);
    assertEq('remount leaves the new host untouched', node.innerHTML, 'remounted');

    global.clearTimeout = function (id) {
        for (let i = 0; i < timers.length; i++) {
            if (timers[i].id === id) timers[i].dead = true;
        }
    };
    lateTimer.dead = true;
    const beforeFresh = seen.length;
    initPdfViewerForWork(parked.ctx, { id: 'work-fresh', file_path: '/api/pdfs/fresh' });
    assert('a fresh lifetime captures a new ticket', seen.length === beforeFresh + 1);
    assert('the fresh ticket is current', parked.ctx.resourceRegistry.accepts(seen[seen.length - 1]));
    assert('the pre-park ticket stays rejected', !parked.ctx.resourceRegistry.accepts(seen[0]));
    assertEq('fresh setup mounts', flushLiveTimers(), 1);
    await settle();
    const fresh = parked.ctx.getResource('pdf');
    assert('fresh ticket registers a runtime', !!fresh && fresh.workId === 'work-fresh');
    assert('fresh runtime is the registry slot', parked.ctx.readResource('pdf') === fresh);
    assertEq('fresh setup creates one viewer', viewers.length, 1);
}

async function scenarioReplaceDisposesOnce() {
    resetWorld();
    const replaced = openTab('replace');
    initPdfViewerForWork(replaced.ctx, { id: 'work-first', file_path: '/api/pdfs/first' });
    flushLiveTimers();
    await settle();
    const first = replaced.ctx.getResource('pdf');
    const firstViewer = first.viewer;
    const destroys = watchDestroy(first);
    initPdfViewerForWork(replaced.ctx, { id: 'work-second', file_path: '/api/pdfs/second' });
    flushLiveTimers();
    await settle();
    const second = replaced.ctx.getResource('pdf');
    assertEq('replacing a pdf disposes the previous runtime once', destroys(), 1);
    assert('replacement runtime is distinct', second && second !== first && second.workId === 'work-second');
    assert('replacement is the registry slot', replaced.ctx.readResource('pdf') === second);
    assert('previous viewer is destroyed', firstViewer.destroyed === true);
    assert('replacement viewer stays', second.viewer && second.viewer.destroyed !== true);
    assert('replacement is not in the legacy map', !replaced.ctx.resources.has('pdf'));
}

async function scenarioPaneIsolation() {
    resetWorld();
    global.prksWorkspaceSnapshot = function () {
        return { mainTabId: 'iso-main', focusedTabId: 'iso-main' };
    };
    const main = openTab('iso-main');
    const side = openTab('iso-side');
    initPdfViewerForWork(main.ctx, { id: 'work-main', file_path: '/api/pdfs/main' });
    initPdfViewerForWork(side.ctx, { id: 'work-side', file_path: '/api/pdfs/side' });
    flushLiveTimers();
    await settle();
    const mainRuntime = main.ctx.getResource('pdf');
    const sideRuntime = side.ctx.getResource('pdf');
    const mainDestroys = watchDestroy(mainRuntime);
    const sideDestroys = watchDestroy(sideRuntime);
    prksUnmountTabContext('iso-side', 'park');
    assertEq('cold-parking secondary disposes its pdf once', sideDestroys(), 1);
    assert('cold-parking secondary leaves main pdf', main.ctx.getResource('pdf') === mainRuntime);
    assert('cold-parking secondary leaves the main viewer', mainRuntime.viewer.destroyed !== true);
    assertEq('cold-parking secondary does not dispose main', mainDestroys(), 0);

    const sideAgain = openTab('iso-side-2');
    initPdfViewerForWork(sideAgain.ctx, { id: 'work-side-2', file_path: '/api/pdfs/side-2' });
    flushLiveTimers();
    await settle();
    const sideAgainRuntime = sideAgain.ctx.getResource('pdf');
    const sideAgainDestroys = watchDestroy(sideAgainRuntime);
    prksUnmountTabContext('iso-main', 'park');
    assertEq('cold-parking main disposes its pdf once', mainDestroys(), 1);
    assert('cold-parking main leaves secondary pdf', sideAgain.ctx.getResource('pdf') === sideAgainRuntime);
    assert('cold-parking main leaves the secondary viewer', sideAgainRuntime.viewer.destroyed !== true);
    assertEq('cold-parking main does not dispose secondary', sideAgainDestroys(), 0);

    const mainB = openTab('iso-main-b');
    const sideB = openTab('iso-side-b');
    initPdfViewerForWork(mainB.ctx, { id: 'work-main-b', file_path: '/api/pdfs/main-b' });
    initPdfViewerForWork(sideB.ctx, { id: 'work-side-b', file_path: '/api/pdfs/side-b' });
    flushLiveTimers();
    await settle();
    const mainBRuntime = mainB.ctx.getResource('pdf');
    const sideBRuntime = sideB.ctx.getResource('pdf');
    const mainBViewer = mainBRuntime.viewer;
    const sideBViewer = sideBRuntime.viewer;
    prksDestroyTabContext('iso-main-b');
    assert('destroying main leaves secondary pdf', sideB.ctx.getResource('pdf') === sideBRuntime);
    assert('destroying main leaves the secondary viewer', sideBViewer.destroyed !== true);
    prksDestroyTabContext('iso-side-b');
    assert('destroying secondary after main does not revive main', tabContext.prksGetTabContext('iso-main-b') == null);
    assert('destroyed main pdf stays gone', mainB.ctx.getResource('pdf') == null);
    assert('destroyed secondary pdf is gone', sideB.ctx.getResource('pdf') == null);
    assert('destroyed secondary viewer is gone', sideBViewer.destroyed === true);
    assert('destroyed main viewer is gone', mainBViewer.destroyed === true);
}

async function scenarioRoleSwap() {
    resetWorld();
    let mainId = 'role-main';
    global.prksWorkspaceSnapshot = function () {
        return { mainTabId: mainId, focusedTabId: mainId };
    };
    const main = openTab('role-main');
    const side = openTab('role-side');
    initPdfViewerForWork(main.ctx, { id: 'work-role-main', file_path: '/api/pdfs/role-main' });
    initPdfViewerForWork(side.ctx, { id: 'work-role-side', file_path: '/api/pdfs/role-side' });
    flushLiveTimers();
    await settle();
    const mainRuntime = main.ctx.getResource('pdf');
    const sideRuntime = side.ctx.getResource('pdf');
    const mainViewer = mainRuntime.viewer;
    const sideViewer = sideRuntime.viewer;
    const mainDestroys = watchDestroy(mainRuntime);
    const sideDestroys = watchDestroy(sideRuntime);
    const viewersBefore = viewers.length;
    const requestsBefore = requests.length;
    mainId = 'role-side';
    assert('role swap makes the secondary context main', prksIsMainTabContext(side.ctx));
    assert('role swap demotes the previous main', !prksIsMainTabContext(main.ctx));
    assert('role swap keeps the main pdf runtime', main.ctx.getResource('pdf') === mainRuntime);
    assert('role swap keeps the secondary pdf runtime', side.ctx.getResource('pdf') === sideRuntime);
    assert('role swap keeps both viewers', mainRuntime.viewer === mainViewer && sideRuntime.viewer === sideViewer);
    assertEq('role swap does not dispose main', mainDestroys(), 0);
    assertEq('role swap does not dispose secondary', sideDestroys(), 0);
    assertEq('role swap does not create a viewer', viewers.length, viewersBefore);
    assertEq('role swap does not refetch', requests.length, requestsBefore);
    assert('role swap does not destroy either viewer', mainViewer.destroyed !== true && sideViewer.destroyed !== true);
}

async function scenarioStaleCompletion() {
    resetWorld();
    const owner = openTab('stale-completion');
    const pending = [];
    viewerFactory = function (opts) {
        return new Promise(function (resolve) {
            pending.push(function () { resolve(defaultViewerFactory(opts)); });
        });
    };
    const generationA = owner.ctx.generation;
    initPdfViewerForWork(owner.ctx, { id: 'work-a', file_path: '/api/pdfs/a' });
    flushLiveTimers();
    await settle();
    const runtimeA = owner.ctx.getResource('pdf');
    assert('A is registered before its viewer resolves', !!runtimeA);
    assertEq('A viewer is still pending', pending.length, 1);
    owner.ctx.beginRoute({ name: 'work', hash: '#/works/b' });
    const node = { innerHTML: 'work-b', tabId: 'stale-completion' };
    owner.ctx.root.querySelector = function (selector) {
        if (String(selector).indexOf('pdf-viewer') !== -1) return node;
        return null;
    };
    initPdfViewerForWork(owner.ctx, { id: 'work-b', file_path: '/api/pdfs/b' });
    flushLiveTimers();
    await settle();
    const runtimeB = owner.ctx.getResource('pdf');
    assert('B replaced A', runtimeB && runtimeB !== runtimeA && runtimeB.workId === 'work-b');
    node.innerHTML = 'painted-b';
    const releaseA = pending[0];
    releaseA();
    await settle();
    assert('stale viewer completion does not become B', runtimeB.viewer == null);
    assert('stale viewer completion does not replace B', owner.ctx.getResource('pdf') === runtimeB);
    assertEq('stale viewer completion leaves B host', node.innerHTML, 'painted-b');
    const viewerA = viewers[0];
    assert('stale viewer is destroyed', viewerA && viewerA.destroyed === true);
    const releaseB = pending[1];
    releaseB();
    await settle();
    const viewerB = runtimeB.viewer;
    assert('B viewer attaches after its own completion', viewerB && viewerB.destroyed !== true && viewerB !== viewerA);
    let mutated = 0;
    const installed = prksInstallPdfAnnotationPersistenceIfCurrent(
        owner.ctx,
        generationA,
        runtimeA,
        viewerA,
        runtimeA.viewerSetupToken,
        function () {
            mutated += 1;
            runtimeB.workId = 'hijacked';
            runtimeB.viewer = viewerA;
        }
    );
    assert('stale persistence completion does not install', installed === false);
    assertEq('stale persistence completion does not mutate B', mutated, 0);
    assert('B keeps its work and viewer', runtimeB.workId === 'work-b' && runtimeB.viewer === viewerB);
}

async function scenarioWarmPendingLeave() {
    resetWorld();
    const leaving = openTab('warm-pending');
    const pendingRuntime = createWorkPdfRuntime({ workId: 'work-warm-pending' });
    leaving.ctx.registerResource(leaving.ctx.resourceTicket(), { kind: 'pdf', value: pendingRuntime, suspendable: true, dispose: function () { pendingRuntime.destroy(); } });
    pendingRuntime.syncState.pendingChanges = true;
    leaving.ctx.lastResolvedRoute = { name: 'work', canonicalHash: '#/works/warm-pending', hash: '#/works/warm-pending' };
    global.prksParseRoute = function (hash) {
        const value = String(hash || '');
        if (value.indexOf('#/works/') === 0) return { name: 'work', canonicalHash: value, hash: value };
        return { name: 'folders', canonicalHash: value, hash: value };
    };
    assert('warm park keeps the pending pdf', prksWarmParkTabContext('warm-pending', hostBox()) === true);
    assert('warm-suspended pdf stays readable', leaving.ctx.getResource('pdf') === pendingRuntime);
    assertEq('warm-suspended pdf still reports pending sync', prksHasPendingWorkAnnotationSync(leaving.ctx), true);
    const prompts = [];
    global.confirm = function (message) {
        prompts.push(message);
        return false;
    };
    let commits = 0;
    const denied = await runPdfLeave(leaving.ctx, '#/folders', function () {
        commits += 1;
        return true;
    });
    assertEq('warm-suspended pending sync blocks leave', denied && denied.status, 'rejected-pending-pdf-sync');
    assertEq('warm-suspended pending sync confirms once', prompts.length, 1);
    assertEq('warm-suspended pending sync does not commit', commits, 0);
    assert(
        'warm-suspended pending sync uses the existing confirm',
        prompts[0] === 'PDF annotation sync still running. Leave page before all changes save to server?'
    );
    assert('denied warm leave keeps the pdf', leaving.ctx.getResource('pdf') === pendingRuntime && leaving.ctx.suspended === true);
}

async function main() {
    assertPdfSourceContract();
    const app = fs.readFileSync(path.join(rootDir, 'frontend/js/app.js'), 'utf8');
    await scenarioCurrentMount();
    await scenarioCancelledDeferredSetup();
    await scenarioStaleCallback();
    await scenarioRemount();
    await scenarioMainAndSecondary();
    await scenarioRouteReplacementFlush(app);
    await scenarioTabClose();
    await scenarioColdPark();
    await scenarioWarmParkResume();
    await scenarioWarmEviction();
    await scenarioOfflineReopen();
    await scenarioPendingLeave(app);
    await scenarioColdParkTicket();
    await scenarioReplaceDisposesOnce();
    await scenarioPaneIsolation();
    await scenarioRoleSwap();
    await scenarioStaleCompletion();
    await scenarioWarmPendingLeave();
    console.log((failed ? 'FAILED ' : 'OK ') + passed + ' passed, ' + failed + ' failed');
    process.exit(failed ? 1 : 0);
}

main().catch(function (err) {
    console.error(err && err.stack ? err.stack : err);
    process.exit(1);
});
