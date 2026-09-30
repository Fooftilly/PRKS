#!/usr/bin/env node
'use strict';

const { spawnSync } = require('child_process');
const path = require('path');

const rootDir = path.resolve(__dirname, '../..');
const tabContext = require(path.join(rootDir, 'frontend/js/tab-context.js'));
const pdfRuntime = require(path.join(rootDir, 'frontend/js/pdf-work-runtime.js'));

const { prksMountTabContext, prksDestroyAllTabContexts } = tabContext;
const {
    createWorkPdfRuntime,
    bindPdfSurfaceSearch,
    prksPdfSearchStill,
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

function viewerStub(extra) {
    const viewer = {
        id: extra && extra.id ? extra.id : 1,
        commits: [],
        clears: 0,
        opens: 0,
        closes: 0,
        nexts: 0,
        prevs: 0,
        destroyed: false,
        openSearch: function () { viewer.opens += 1; },
        closeSearch: function () { viewer.closes += 1; },
        clearSearchMatches: function () { viewer.clears += 1; },
        commitSearch: function (query, epoch) { viewer.commits.push({ query: query, epoch: epoch }); },
        searchNext: function () {
            viewer.nexts += 1;
            return extra && typeof extra.next === 'function' ? extra.next(viewer) : 1;
        },
        searchPrevious: function () {
            viewer.prevs += 1;
            return extra && typeof extra.prev === 'function' ? extra.prev(viewer) : 0;
        },
        destroy: function () { viewer.destroyed = true; },
    };
    return viewer;
}

function element(tag) {
    return {
        tagName: String(tag || 'div').toUpperCase(),
        dataset: {},
        attrs: {},
        parentNode: null,
        listeners: [],
        isContentEditable: false,
        getAttribute: function (name) {
            return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
        },
        setAttribute: function (name, value) {
            this.attrs[name] = String(value);
        },
        addEventListener: function (type, fn, capture) {
            this.listeners.push({ type: type, fn: fn, capture: !!capture });
        },
        removeEventListener: function (type, fn) {
            this.listeners = this.listeners.filter(function (listener) {
                return listener.fn !== fn || listener.type !== type;
            });
        },
        appendChild: function (child) {
            child.parentNode = this;
            return child;
        },
        select: function () { this.selected = (this.selected || 0) + 1; },
    };
}

function fireKey(target, event) {
    event.target = target;
    event.stopped = false;
    event.prevented = false;
    event.preventDefault = function () { event.prevented = true; };
    event.stopPropagation = function () { event.stopped = true; };
    const chain = [];
    let node = target;
    while (node) {
        chain.push(node);
        node = node.parentNode;
    }
    for (let i = chain.length - 1; i >= 0; i -= 1) {
        const listeners = chain[i].listeners || [];
        for (let j = 0; j < listeners.length; j += 1) {
            const listener = listeners[j];
            if (listener.type === 'keydown' && listener.capture) listener.fn(event);
            if (event.stopped) return event;
        }
    }
    return event;
}

function findEvent(over) {
    return Object.assign({ key: 'f', ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, repeat: false }, over || {});
}

function hostBox() {
    const kids = [];
    return {
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

global.window = global;
global.document = {
    visibilityState: 'visible',
    getElementById: function () { return null; },
};

function testQueryChangesDoNotReplaceTheViewer() {
    const runtime = createWorkPdfRuntime({ workId: 'work-a' });
    const viewer = viewerStub();
    const cache = runtime.annotationCache;
    runtime.viewer = viewer;
    runtime.viewerSetupToken = 4;
    assert('open search', runtime.openSearch());
    assertEq('viewer opened once', viewer.opens, 1);
    assert('query a', runtime.setSearchQuery('a'));
    assert('query ab', runtime.setSearchQuery('alpha beta'));
    assertEq('two commits', viewer.commits.length, 2);
    assertEq('first query', viewer.commits[0].query, 'a');
    assertEq('second query', viewer.commits[1].query, 'alpha beta');
    assert('epochs differ', viewer.commits[0].epoch !== viewer.commits[1].epoch);
    assertEq('status pending', runtime.readSearch().status, 'pending');
    assert('stale epoch dropped', runtime.applySearchResult({
        epoch: viewer.commits[0].epoch,
        total: 9,
        activeIndex: 0,
        viewer: viewer,
    }) === false);
    assertEq('still pending', runtime.readSearch().total, 0);
    assert('current epoch applied', runtime.applySearchResult({
        epoch: viewer.commits[1].epoch,
        total: 3,
        activeIndex: 0,
        viewer: viewer,
    }));
    assertEq('ready count', runtime.readSearch().matchCountLabel, '1 of 3');
    assertEq('same viewer', runtime.viewer, viewer);
    assertEq('token unchanged', runtime.viewerSetupToken, 4);
    assertEq('annotation cache untouched', runtime.annotationCache, cache);
    assertEq('sync not pending', runtime.syncState.pendingChanges, false);
    assert('clear query', runtime.setSearchQuery('   '));
    assertEq('idle after clear', runtime.readSearch().status, 'idle');
    assertEq('cleared matches', viewer.clears, 1);
    assert('close search', runtime.closeSearch());
    assertEq('closed once', viewer.closes, 1);
    assertEq('viewer after close', runtime.viewer, viewer);
    assertEq('token after close', runtime.viewerSetupToken, 4);
    assert('attach refuses a different viewer', runtime.attachSearchViewer(viewerStub({ id: 2 })) === false);
    runtime.destroy();
    assert('destroyed refuses query', runtime.setSearchQuery('later') === false);
}

function testNextPreviousAndNoResults() {
    const runtime = createWorkPdfRuntime({ workId: 'work-a' });
    let index = 0;
    const viewer = viewerStub({
        next: function () {
            index = (index + 1) % 3;
            return index;
        },
        prev: function () {
            index = (index + 2) % 3;
            return index;
        },
    });
    runtime.viewer = viewer;
    runtime.openSearch();
    runtime.setSearchQuery('missing');
    assert('no results', runtime.applySearchResult({
        epoch: runtime.search.epoch,
        total: 0,
        activeIndex: -1,
        viewer: viewer,
    }));
    assertEq('empty status', runtime.readSearch().status, 'empty');
    assertEq('empty label', runtime.readSearch().matchCountLabel, 'No matches');
    assert('next is a no-op', runtime.searchNext() === false);
    assert('previous is a no-op', runtime.searchPrevious() === false);
    assertEq('no navigation calls', viewer.nexts + viewer.prevs, 0);

    runtime.setSearchQuery('found');
    runtime.applySearchResult({
        epoch: runtime.search.epoch,
        total: 3,
        activeIndex: 0,
        viewer: viewer,
    });
    assert('next', runtime.searchNext());
    assertEq('active is viewer index', runtime.readSearch().activeIndex, 1);
    assertEq('label follows viewer', runtime.readSearch().matchCountLabel, '2 of 3');
    assert('previous', runtime.searchPrevious());
    assertEq('previous uses the viewer index', runtime.readSearch().activeIndex, 0);
    assertEq('same viewer after navigation', runtime.viewer, viewer);

    const local = createWorkPdfRuntime({ workId: 'local' });
    local.openSearch();
    local.search.total = 3;
    local.search.activeIndex = 0;
    local.search.status = 'ready';
    assert('local next', local.searchNext());
    assertEq('local wrapped index', local.readSearch().activeIndex, 1);
    assert('local previous from start', (function () {
        local.search.activeIndex = 0;
        return local.searchPrevious();
    })());
    assertEq('local wrap to end', local.readSearch().activeIndex, 2);
}

function testRouteReplacementDropsTheSearch() {
    prksDestroyAllTabContexts();
    const ctx = prksMountTabContext('route', hostBox());
    ctx.beginRoute({ name: 'work', hash: '#/works/route' });
    const runtime = createWorkPdfRuntime({ workId: 'work-a' });
    const viewer = viewerStub();
    runtime.viewer = viewer;
    runtime.viewerSetupToken = 2;
    ctx.setResource('pdf', runtime, function () { runtime.destroy(); });
    const generation = ctx.generation;
    bindPdfSurfaceSearch(ctx, runtime, element('div'), generation);
    runtime.openSearch();
    runtime.setSearchQuery('page');
    const epoch = runtime.search.epoch;
    ctx.beginRoute({ name: 'work', hash: '#/works/route-b' });
    assert('old runtime destroyed', runtime._destroyed);
    assert('old search closed', viewer.closes >= 1);
    assert('old viewer destroyed by runtime', viewer.destroyed);
    const next = createWorkPdfRuntime({ workId: 'work-b' });
    const nextViewer = viewerStub({ id: 9 });
    next.viewer = nextViewer;
    ctx.setResource('pdf', next, function () { next.destroy(); });
    assert('late result ignored', runtime.applySearchResult({
        epoch: epoch,
        total: 4,
        activeIndex: 1,
        viewer: viewer,
        ownerGeneration: generation,
        ctx: ctx,
    }) === false);
    assertEq('new search stays closed', next.readSearch().open, false);
    assertEq('new query empty', next.readSearch().query, '');
    assertEq('new viewer not opened', nextViewer.opens, 0);
    assert('stale generation still fails', prksPdfSearchStill(ctx, generation, next) === false);
    assert('current generation of the new runtime passes', prksPdfSearchStill(ctx, ctx.generation, next));
    prksDestroyAllTabContexts();
}

function testIndependentPanes() {
    prksDestroyAllTabContexts();
    const main = prksMountTabContext('main', hostBox());
    const side = prksMountTabContext('side', hostBox());
    main.beginRoute({ name: 'work' });
    side.beginRoute({ name: 'work' });
    const mainRuntime = createWorkPdfRuntime({ workId: 'main-work' });
    const sideRuntime = createWorkPdfRuntime({ workId: 'side-work' });
    const mainViewer = viewerStub({ id: 'main' });
    const sideViewer = viewerStub({ id: 'side' });
    mainRuntime.viewer = mainViewer;
    sideRuntime.viewer = sideViewer;
    main.setResource('pdf', mainRuntime, function () { mainRuntime.destroy(); });
    side.setResource('pdf', sideRuntime, function () { sideRuntime.destroy(); });

    const mainSurface = element('div');
    const sideSurface = element('div');
    const mainPage = element('div');
    const sidePage = element('div');
    const sideNote = element('textarea');
    mainSurface.appendChild(mainPage);
    sideSurface.appendChild(sidePage);
    sideSurface.appendChild(sideNote);
    bindPdfSurfaceSearch(main, mainRuntime, mainSurface, main.generation);
    bindPdfSurfaceSearch(side, sideRuntime, sideSurface, side.generation);
    assertEq('main owner', mainSurface.dataset.prksOwnerTabId, 'main');
    assertEq('side owner', sideSurface.dataset.prksOwnerTabId, 'side');
    assertEq('main generation stamp', mainSurface.dataset.prksOwnerGeneration, String(main.generation));

    const mainEvent = fireKey(mainPage, findEvent({ ctrlKey: true }));
    assert('main find prevented', mainEvent.prevented);
    assertEq('main search open', mainRuntime.readSearch().open, true);
    assertEq('side still closed', sideRuntime.readSearch().open, false);

    const sideEvent = fireKey(sidePage, findEvent({ metaKey: true }));
    assert('side find prevented', sideEvent.prevented);
    assertEq('side search open', sideRuntime.readSearch().open, true);
    mainRuntime.setSearchQuery('main-term');
    sideRuntime.setSearchQuery('side-term');
    assertEq('main query', mainRuntime.readSearch().query, 'main-term');
    assertEq('side query', sideRuntime.readSearch().query, 'side-term');
    assertEq('main commits', mainViewer.commits.length, 1);
    assertEq('side commits', sideViewer.commits.length, 1);
    assertEq('main commit text', mainViewer.commits[0].query, 'main-term');
    assertEq('side commit text', sideViewer.commits[0].query, 'side-term');

    mainRuntime.applySearchResult({ epoch: mainRuntime.search.epoch, total: 2, activeIndex: 0, viewer: mainViewer });
    sideRuntime.applySearchResult({ epoch: sideRuntime.search.epoch, total: 0, activeIndex: -1, viewer: sideViewer });
    assertEq('main label', mainRuntime.readSearch().matchCountLabel, '1 of 2');
    assertEq('side empty', sideRuntime.readSearch().status, 'empty');
    assert('side next no-op', sideRuntime.searchNext() === false);
    assert('main next', mainRuntime.searchNext());
    assertEq('side viewer not navigated', sideViewer.nexts, 0);

    const shifted = fireKey(mainPage, findEvent({ ctrlKey: true, shiftKey: true }));
    assert('shift find ignored', !shifted.prevented);
    const foreign = fireKey(sideNote, findEvent({ ctrlKey: true }));
    assert('annotation-like field keeps the shortcut', !foreign.prevented);
    assertEq('side stays the side query', sideRuntime.readSearch().query, 'side-term');

    const staleChild = element('div');
    staleChild.dataset.prksOwnerTabId = 'main';
    staleChild.dataset.prksOwnerGeneration = String(main.generation + 1);
    mainSurface.appendChild(staleChild);
    const staleEvent = fireKey(staleChild, findEvent({ ctrlKey: true }));
    assert('stale owner generation ignored', !staleEvent.prevented);

    sideRuntime.destroy();
    const after = fireKey(sidePage, findEvent({ metaKey: true }));
    assert('destroyed pane does not handle find', !after.prevented);
    assertEq('main viewer survives side destroy', mainRuntime.viewer, mainViewer);
    prksDestroyAllTabContexts();
}

function testSearchBeforeViewerAttachesToTheSameInstance() {
    const runtime = createWorkPdfRuntime({ workId: 'early' });
    runtime.openSearch();
    runtime.setSearchQuery('early');
    const viewer = viewerStub();
    runtime.viewer = viewer;
    runtime.viewerSetupToken = 1;
    assert('attach same viewer', runtime.attachSearchViewer(viewer));
    assertEq('opened on attach', viewer.opens, 1);
    assertEq('committed on attach', viewer.commits.length, 1);
    assertEq('attach query', viewer.commits[0].query, 'early');
    assertEq('still that viewer', runtime.viewer, viewer);
}

function testCommitSearchFailurePaths() {
    const helper = path.join(rootDir, 'tools/pdf-viewer/src/search-commit.ts');
    const runner = [
        'import { commitPdfSearch } from ' + JSON.stringify(helper) + ';',
        'const rows = [];',
        'function run(name, scope, currentSeq, want) {',
        '  const settled = [];',
        '  let seq = 0;',
        '  commitPdfSearch({',
        '    query: "term",',
        '    epoch: 4,',
        '    scope: scope,',
        '    beginSeq: function () { seq += 1; return seq; },',
        '    currentSeq: function () { return currentSeq(seq); },',
        '    settle: function (result) { settled.push(result); },',
        '  });',
        '  rows.push({ name: name, settled: settled, want: want });',
        '}',
        'const empty = [{ epoch: 4, total: 0, activeIndex: -1 }];',
        'const throwing = {',
        '  searchAllPages: function () { throw new Error("sync"); },',
        '  getState: function () { return null; },',
        '};',
        'run("missing scope", null, function (seq) { return seq; }, empty);',
        'run("missing scope stale", undefined, function () { return 99; }, []);',
        'run("sync throw", throwing, function (seq) { return seq; }, empty);',
        'run("sync throw stale", throwing, function () { return 99; }, []);',
        'process.stdout.write(JSON.stringify(rows));',
    ].join('\n');
    const result = spawnSync(
        process.execPath,
        ['--experimental-strip-types', '--input-type=module', '-e', runner],
        { cwd: rootDir, encoding: 'utf8' }
    );
    let rows = [];
    try {
        rows = JSON.parse(result.stdout || '[]');
    } catch (_e) {
        rows = [];
    }
    assert(
        'commit search failure runner',
        result.status === 0 && rows.length === 4,
        (result.stderr || '') + (result.stdout || '')
    );
    rows.forEach(function (row) {
        assertEq(
            'commit ' + row.name,
            JSON.stringify(row.settled),
            JSON.stringify(row.want)
        );
    });
}

testQueryChangesDoNotReplaceTheViewer();
testNextPreviousAndNoResults();
testRouteReplacementDropsTheSearch();
testIndependentPanes();
testSearchBeforeViewerAttachesToTheSameInstance();
testCommitSearchFailurePaths();

console.log((failed ? 'FAILED ' : 'OK ') + passed + ' passed, ' + failed + ' failed');
if (failed) process.exit(1);
