#!/usr/bin/env node
'use strict';

const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const rootDir = path.resolve(__dirname, '../..');
globalThis.prksOwnerResource = require(path.join(rootDir, 'frontend/js/owner-resource.js'));
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

function testCowRemountRehydratesQueryAndRebindsFind() {
    prksDestroyAllTabContexts();
    const ctx = prksMountTabContext('main', hostBox());
    ctx.beginRoute({ name: 'work' });
    const runtime = createWorkPdfRuntime({ workId: 'cow' });
    ctx.setResource('pdf', runtime, function () { runtime.destroy(); });
    const generation = ctx.generation;
    const oldSurface = element('div');
    const oldPage = element('div');
    oldSurface.appendChild(oldPage);
    bindPdfSurfaceSearch(ctx, runtime, oldSurface, generation);
    runtime.viewerSetupToken = 1;
    runtime.openSearch();
    runtime.setSearchQuery('cow term');
    const oldViewer = viewerStub({ id: 'old' });
    runtime.viewer = oldViewer;
    assert('cow pre-ready attach', runtime.attachSearchViewer(oldViewer));
    assertEq('cow pre-ready query', oldViewer.commits[0].query, 'cow term');
    assertEq('cow pre-ready count', runtime.readSearch().matchCountLabel, 'Searching');

    const staging = element('div');
    const newPage = element('div');
    staging.appendChild(newPage);
    const newViewer = viewerStub({ id: 'new' });
    runtime.viewer = newViewer;
    assertEq('cow remount keeps the setup token', runtime.viewerSetupToken, 1);
    bindPdfSurfaceSearch(ctx, runtime, staging, generation);
    assert('cow remount replays the runtime query', runtime.attachSearchViewer(newViewer));
    assertEq('cow old surface dropped its listener', oldSurface.listeners.length, 0);
    assertEq('cow new surface has one listener', staging.listeners.length, 1);
    const stale = fireKey(oldPage, findEvent({ ctrlKey: true }));
    assert('cow old surface ignores find', !stale.prevented);
    assertEq('cow old viewer is not asked to highlight again', oldViewer.commits.length, 1);
    assertEq('cow new input query', newViewer.commits[0].query, 'cow term');
    assertEq('cow runtime query', runtime.readSearch().query, 'cow term');
    assertEq('cow runtime count', runtime.readSearch().matchCountLabel, 'Searching');
    const opens = newViewer.opens;
    const current = fireKey(newPage, findEvent({ metaKey: true }));
    assert('cow new surface handles find', current.prevented);
    assertEq('cow find did not stack', newViewer.opens, opens + 1);
    prksDestroyAllTabContexts();
}

function testRebindDoesNotStackListeners() {
    prksDestroyAllTabContexts();
    const ctx = prksMountTabContext('main', hostBox());
    ctx.beginRoute({ name: 'work' });
    const runtime = createWorkPdfRuntime({ workId: 'rebind' });
    const viewer = viewerStub();
    runtime.viewer = viewer;
    runtime.viewerSetupToken = 1;
    ctx.setResource('pdf', runtime, function () { runtime.destroy(); });
    const first = element('div');
    const firstPage = element('div');
    first.appendChild(firstPage);
    const second = element('div');
    const secondPage = element('div');
    second.appendChild(secondPage);
    bindPdfSurfaceSearch(ctx, runtime, first, ctx.generation);
    bindPdfSurfaceSearch(ctx, runtime, second, ctx.generation);
    assertEq('old surface dropped its listener', first.listeners.length, 0);
    assertEq('new surface has one listener', second.listeners.length, 1);
    const stale = fireKey(firstPage, findEvent({ ctrlKey: true }));
    assert('old surface does not open search', !stale.prevented);
    assertEq('search stays closed from the old surface', runtime.readSearch().open, false);
    bindPdfSurfaceSearch(ctx, runtime, second, ctx.generation);
    assertEq('rebind keeps a single listener', second.listeners.length, 1);
    const opens = viewer.opens;
    const current = fireKey(secondPage, findEvent({ ctrlKey: true }));
    assert('rebound surface opens search', current.prevented);
    assertEq('rebind did not stack find handlers', viewer.opens, opens + 1);
    prksDestroyAllTabContexts();
}

function testCommitSearchFailurePaths() {
    const helper = path.join(rootDir, 'tools/pdf-viewer/src/search-commit.ts');
    const view = path.join(rootDir, 'tools/pdf-viewer/src/search-bar-view.ts');
    const controllerPath = path.join(rootDir, 'tools/pdf-viewer/src/controller.ts');
    const runtimePath = path.join(rootDir, 'frontend/js/pdf-work-runtime.js');
    const runner = [
        'import { clearPdfSearchFlight, commitPdfSearch } from ' + JSON.stringify(helper) + ';',
        'import { pdfSearchBarView } from ' + JSON.stringify(view) + ';',
        'import { ViewerController } from ' + JSON.stringify(controllerPath) + ';',
        'import { createRequire } from "node:module";',
        'const require = createRequire(import.meta.url);',
        'const pdfRuntime = require(' + JSON.stringify(runtimePath) + ');',
        'const rows = [];',
        'function check(name, got, want) {',
        '  rows.push({ name: name, got: got, want: want });',
        '}',
        'function run(name, scope, currentSeq, want, flight) {',
        '  const settled = [];',
        '  let seq = 0;',
        '  let threw = false;',
        '  try {',
        '    commitPdfSearch({',
        '      query: "term",',
        '      epoch: 4,',
        '      scope: scope,',
        '      flight: flight,',
        '      beginSeq: function () { seq += 1; return seq; },',
        '      currentSeq: function () { return currentSeq(seq); },',
        '      settle: function (result) { settled.push(result); },',
        '    });',
        '  } catch (err) { threw = true; }',
        '  check(name + " threw", threw, false);',
        '  check(name, settled, want);',
        '}',
        'const empty = [{ epoch: 4, total: 0, activeIndex: -1 }];',
        'const throwing = {',
        '  searchAllPages: function () { throw new Error("sync"); },',
        '  getState: function () { return null; },',
        '};',
        'const throwingToPromise = {',
        '  searchAllPages: function () {',
        '    return {',
        '      toPromise: function () { throw new Error("toPromise sync"); },',
        '    };',
        '  },',
        '  getState: function () { return null; },',
        '};',
        'run("missing scope", null, function (seq) { return seq; }, empty);',
        'run("missing scope stale", undefined, function () { return 99; }, []);',
        'run("sync throw", throwing, function (seq) { return seq; }, empty);',
        'run("sync throw stale", throwing, function () { return 99; }, []);',
        'run("toPromise sync throw", throwingToPromise, function (seq) { return seq; }, empty);',
        'run("toPromise sync throw stale", throwingToPromise, function () { return 99; }, []);',
        'const failedView = pdfSearchBarView({',
        '  draft: "term",',
        '  plugin: { loading: true, total: 3, activeResultIndex: 1 },',
        '  settled: { total: 0, activeIndex: -1 },',
        '  pending: false,',
        '  followRuntime: true,',
        '});',
        'check("bar no matches label", failedView.label, "No matches");',
        'check("bar no matches disabled", failedView.matchesDisabled, true);',
        'function readyApi(extra) {',
        '  return Object.assign({',
        '    zoomIn: function () {}, zoomOut: function () {}, fitWidth: function () {}, fitPage: function () {},',
        '    goToPage: function () {}, getCurrentPage: function () { return 1; }, getPageCount: function () { return 1; },',
        '    setInteractionMode: function () {}, activateMarkupTool: function () {}, clearActiveTool: function () {},',
        '    undo: function () {}, redo: function () {}, getAnnotations: function () { return []; },',
        '    jumpToAnnotation: function () {}, updateAnnotation: function () {}, createAnnotation: function () {},',
        '    deleteAnnotation: function () { return Promise.resolve(); }, selectAnnotation: function () {},',
        '    saveCopy: function () { return Promise.resolve(new ArrayBuffer(0)); },',
        '    getDocumentId: function () { return "doc"; }, isSelecting: function () { return false; },',
        '    openSearch: function () {}, closeSearch: function () {},',
        '    commitSearch: function () {}, clearSearchMatches: function () {},',
        '    searchNext: function () { return -1; }, searchPrevious: function () { return -1; },',
        '  }, extra);',
        '}',
        'function mountViewer(onCommit, onClear) {',
        '  const shown = [];',
        '  const order = [];',
        '  let intents = 0;',
        '  const controller = new ViewerController();',
        '  controller.bindSearchChrome({',
        '    open: function () {}, close: function () {}, focus: function () {},',
        '    setQuery: function (query) { shown.push(query); order.push("input:" + query); },',
        '    applySettlement: function () {},',
        '  });',
        '  controller.setSearchDriver({',
        '    onQuery: function () { intents += 1; },',
        '    onNext: function () {}, onPrevious: function () {}, onClose: function () {}, onSettled: function () {},',
        '  });',
        '  controller.attach(readyApi({',
        '    commitSearch: function (query) { order.push("highlight:" + query); onCommit(query); },',
        '    clearSearchMatches: function () { if (onClear) onClear(); },',
        '  }), function () {});',
        '  return { handle: controller.asHandle(), shown: shown, order: order, intents: function () { return intents; } };',
        '}',
        'function barLabel(draft, pending, settled, plugin) {',
        '  return pdfSearchBarView({',
        '    draft: draft,',
        '    plugin: plugin,',
        '    settled: settled,',
        '    pending: pending,',
        '    followRuntime: true,',
        '  }).label;',
        '}',
        'const firstCommits = [];',
        'const first = mountViewer(function (query) { firstCommits.push(query); });',
        'const runtime = pdfRuntime.createWorkPdfRuntime({ workId: "display" });',
        'runtime.openSearch();',
        'runtime.setSearchQuery("before ready");',
        'runtime.viewer = first.handle;',
        'runtime.viewerSetupToken = 1;',
        'runtime.attachSearchViewer(first.handle);',
        'check("before-ready display", first.shown.slice(), ["before ready"]);',
        'check("before-ready commit", firstCommits.slice(), ["before ready"]);',
        'check("before-ready input before highlight", first.order.slice(), ["input:before ready", "highlight:before ready"]);',
        'check("before-ready count", barLabel("before ready", true, null, { loading: true, total: 0, activeResultIndex: -1 }), "Searching");',
        'check("before-ready intents", first.intents(), 0);',
        'runtime.setSearchQuery("programmatic");',
        'check("programmatic display", first.shown[first.shown.length - 1], "programmatic");',
        'check("programmatic input before highlight", first.order.slice().slice(-2), ["input:programmatic", "highlight:programmatic"]);',
        'check("programmatic intents", first.intents(), 0);',
        'check("programmatic count", barLabel("programmatic", true, null, { loading: false, total: 8, activeResultIndex: 3 }), "Searching");',
        'check("settled count ignores stale plugin", barLabel("programmatic", false, { total: 2, activeIndex: 0 }, { loading: false, total: 8, activeResultIndex: 3 }), "1 of 2");',
        'runtime.setSearchQuery("   ");',
        'check("clear display", first.shown[first.shown.length - 1], "");',
        'check("clear count", barLabel("", false, null, { loading: false, total: 8, activeResultIndex: 3 }), "");',
        'check("clear intents", first.intents(), 0);',
        'const secondCommits = [];',
        'const second = mountViewer(function (query) { secondCommits.push(query); });',
        'runtime.openSearch();',
        'runtime.viewer = first.handle;',
        'runtime.setSearchQuery("kept");',
        'const shownBeforeReplace = first.shown.slice();',
        'runtime.viewer = second.handle;',
        'runtime.viewerSetupToken = 2;',
        'runtime.attachSearchViewer(second.handle);',
        'check("cow remount display", second.shown.slice(), ["kept"]);',
        'check("cow remount commit", secondCommits.slice(), ["kept"]);',
        'check("cow remount input before highlight", second.order.slice(), ["input:kept", "highlight:kept"]);',
        'check("cow remount count", barLabel("kept", true, null, null), "Searching");',
        'check("cow remount intents", second.intents(), 0);',
        'check("cow remount old bar stays", first.shown.slice(), shownBeforeReplace);',
        'async function coalesce() {',
        '  let calls = 0;',
        '  let complete = false;',
        '  const pending = [];',
        '  const settled = [];',
        '  let seq = 0;',
        '  const flight = { current: null };',
        '  const scope = {',
        '    searchAllPages: function (query) {',
        '      calls += 1;',
        '      if (query.trim() === "term" && calls > 1) {',
        '        return { toPromise: function () { return Promise.resolve("partial"); } };',
        '      }',
        '      let resolve;',
        '      const promise = new Promise(function (res) { resolve = res; });',
        '      pending.push({ resolve: resolve, promise: promise });',
        '      return { toPromise: function () { return promise; } };',
        '    },',
        '    getState: function () {',
        '      return { total: complete ? 4 : 1, activeResultIndex: 0 };',
        '    },',
        '  };',
        '  function commit(epoch, query) {',
        '    commitPdfSearch({',
        '      query: query, epoch: epoch, scope: scope, flight: flight,',
        '      beginSeq: function () { seq += 1; return seq; },',
        '      currentSeq: function () { return seq; },',
        '      settle: function (result) { settled.push(result); },',
        '    });',
        '  }',
        '  commit(1, "term");',
        '  commit(2, "term ");',
        '  check("trimmed query stays one task", calls, 1);',
        '  check("partial state not settled", settled.slice(), []);',
        '  complete = true;',
        '  pending[0].resolve();',
        '  await pending[0].promise;',
        '  await Promise.resolve();',
        '  check("completed task settles latest epoch", settled.slice(), [{ epoch: 2, total: 4, activeIndex: 0 }]);',
        '}',
        'async function clearThenSameQuery() {',
        '  let calls = 0;',
        '  const queries = [];',
        '  const pending = [];',
        '  const settled = [];',
        '  let seq = 0;',
        '  let pluginTotal = 1;',
        '  const flight = { current: null };',
        '  const scope = {',
        '    searchAllPages: function (query) {',
        '      calls += 1;',
        '      queries.push(query);',
        '      let resolve;',
        '      const promise = new Promise(function (res) { resolve = res; });',
        '      pending.push({ resolve: resolve, promise: promise });',
        '      return { toPromise: function () { return promise; } };',
        '    },',
        '    getState: function () {',
        '      return { total: pluginTotal, activeResultIndex: 0 };',
        '    },',
        '  };',
        '  function commit(epoch, query) {',
        '    commitPdfSearch({',
        '      query: query, epoch: epoch, scope: scope, flight: flight,',
        '      beginSeq: function () { seq += 1; return seq; },',
        '      currentSeq: function () { return seq; },',
        '      settle: function (result) { settled.push(result); },',
        '    });',
        '  }',
        '  commit(1, "term");',
        '  check("clear starts from one search", calls, 1);',
        '  clearPdfSearchFlight(flight);',
        '  seq += 1;',
        '  pluginTotal = 0;',
        '  commit(2, "term");',
        '  check("clear then same query starts a fresh search", calls, 2);',
        '  check("fresh search query", queries[1], "term");',
        '  pending[0].resolve();',
        '  await pending[0].promise;',
        '  await Promise.resolve();',
        '  check("pre-clear task does not settle", settled.slice(), []);',
        '  pluginTotal = 3;',
        '  pending[1].resolve();',
        '  await pending[1].promise;',
        '  await Promise.resolve();',
        '  check("fresh search settles its epoch", settled.slice(), [{ epoch: 2, total: 3, activeIndex: 0 }]);',
        '}',
        'coalesce().then(function () { return clearThenSameQuery(); }).then(function () { process.stdout.write(JSON.stringify(rows)); }).catch(function (err) {',
        '  console.error(err);',
        '  process.exit(1);',
        '});',
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
        result.status === 0 && rows.length > 0,
        (result.stderr || '') + (result.stdout || '')
    );
    rows.forEach(function (row) {
        assertEq(row.name, JSON.stringify(row.got), JSON.stringify(row.want));
    });
}

function testClearAndCloseDropTheSearchFlight() {
    const src = fs.readFileSync(path.join(rootDir, 'tools/pdf-viewer/src/viewer.tsx'), 'utf8');
    function between(start, end) {
        const at = src.indexOf(start);
        const until = src.indexOf(end, at + start.length);
        return src.slice(at, until);
    }
    function dropsBeforeSeq(name, text) {
        const drop = text.indexOf('clearPdfSearchFlight(searchFlight.current)');
        const seq = text.indexOf('controller.nextSearchSeq()');
        assert(name + ' clears the flight', drop >= 0);
        assert(name + ' clears the flight before the sequence bump', seq >= 0 && drop < seq);
    }
    dropsBeforeSeq('close', between('closeSearch: () => {', 'clearSearchMatches: () => {'));
    dropsBeforeSeq('clear', between('clearSearchMatches: () => {', 'commitSearch: (query, epoch) => {'));
}

testQueryChangesDoNotReplaceTheViewer();
testNextPreviousAndNoResults();
testRouteReplacementDropsTheSearch();
testIndependentPanes();
testSearchBeforeViewerAttachesToTheSameInstance();
testCowRemountRehydratesQueryAndRebindsFind();
testRebindDoesNotStackListeners();
testCommitSearchFailurePaths();
testClearAndCloseDropTheSearchFlight();

console.log((failed ? 'FAILED ' : 'OK ') + passed + ' passed, ' + failed + ' failed');
if (failed) process.exit(1);
