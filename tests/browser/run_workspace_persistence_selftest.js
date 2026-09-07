#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

const rootDir = path.resolve(__dirname, '../..');

class MemoryStorage {
    constructor() {
        this.store = Object.create(null);
        this.writes = 0;
        this.throwOnSet = false;
    }
    getItem(k) {
        return Object.prototype.hasOwnProperty.call(this.store, k) ? this.store[k] : null;
    }
    setItem(k, v) {
        if (this.throwOnSet) {
            const err = new Error('quota');
            err.name = 'QuotaExceededError';
            throw err;
        }
        this.writes += 1;
        this.store[k] = String(v);
    }
    removeItem(k) {
        delete this.store[k];
    }
}

const store = new MemoryStorage();
global.localStorage = store;

const nav = require(path.join(rootDir, 'frontend/js/navigation.js'));
const tree = require(path.join(rootDir, 'frontend/js/workspace-tree.js'));
const persist = require(path.join(rootDir, 'frontend/js/workspace-persistence.js'));
const wsApi = require(path.join(rootDir, 'frontend/js/workspace-tabs.js'));

const { createPrksWorkspaceTabs } = wsApi;
const KEY = persist.PRKS_WORKSPACE_PERSISTENCE_KEY;

let passed = 0;
let failed = 0;

function record(name, ok, detail) {
    if (ok) passed += 1;
    else failed += 1;
    console.log((ok ? 'PASS  ' : 'FAIL  ') + name + (ok ? '' : ' ' + (detail || '')));
}

function assertEq(name, got, want) {
    const ok = got === want;
    record(name, ok, ok ? '' : 'got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want));
}

function assert(name, ok, detail) {
    record(name, !!ok, ok ? '' : detail || '');
}

function withFakeTimers(fn) {
    const realSet = global.setTimeout;
    const realClear = global.clearTimeout;
    const timers = [];
    let nextId = 1;
    global.setTimeout = function (cb, _ms) {
        const id = nextId;
        nextId += 1;
        timers.push({ id: id, cb: cb });
        return id;
    };
    global.clearTimeout = function (id) {
        for (let i = timers.length - 1; i >= 0; i--) {
            if (timers[i].id === id) timers.splice(i, 1);
        }
    };
    try {
        return fn({
            timers: timers,
            fire: function () {
                const batch = timers.splice(0, timers.length);
                for (let i = 0; i < batch.length; i++) batch[i].cb();
            },
        });
    } finally {
        global.setTimeout = realSet;
        global.clearTimeout = realClear;
    }
}

function collectKeys(value, out) {
    if (!value || typeof value !== 'object') return out;
    if (Array.isArray(value)) {
        for (let i = 0; i < value.length; i++) collectKeys(value[i], out);
        return out;
    }
    const keys = Object.keys(value);
    for (let i = 0; i < keys.length; i++) {
        out[keys[i]] = true;
        collectKeys(value[keys[i]], out);
    }
    return out;
}

function stripTree(node) {
    if (!node) return null;
    if (node.type === 'leaf') return { type: 'leaf', tabId: node.tabId };
    return {
        type: 'split',
        axis: node.axis,
        ratio: node.ratio,
        first: stripTree(node.first),
        second: stripTree(node.second),
    };
}

function makeHistory(initialHash) {
    let hash = initialHash;
    let href = 'http://127.0.0.1/' + initialHash;
    let state = null;
    return {
        getHash: function () {
            return hash;
        },
        getHref: function () {
            return href;
        },
        getState: function () {
            return state;
        },
        pushState: function (s, url) {
            const u = new URL(url);
            hash = u.hash;
            href = url;
            state = s;
        },
        replaceState: function (s, url) {
            if (url) {
                const u = new URL(url);
                hash = u.hash;
                href = url;
            }
            state = s;
        },
        setLocation: function (nextHash) {
            hash = nextHash;
            href = 'http://127.0.0.1/' + nextHash;
        },
    };
}

function makeHarness(opts) {
    opts = opts || {};
    const hist = makeHistory(opts.hash || '#/folders');
    const life = { mount: [], park: [], destroy: [] };
    const mounted = Object.create(null);
    const renders = [];
    const persistCalls = [];
    const ws = createPrksWorkspaceTabs({
        parseRoute: nav.prksParseRoute,
        routeLoadingTitle: nav.prksRouteLoadingTitle,
        routeTabIcon: nav.prksRouteTabIcon,
        homeHash: '#/folders',
        historyAdapter: hist,
        supportsTile: nav.prksRouteSupportsTile,
        loadSnapshot: opts.loadSnapshot,
        persistNotify: function (snap) {
            persistCalls.push(snap);
        },
        renderRoute: function (options) {
            renders.push(options || {});
        },
        onMountContext: function (tabId) {
            life.mount.push(tabId);
            mounted[tabId] = true;
        },
        onParkContext: function (tabId) {
            life.park.push(tabId);
            delete mounted[tabId];
        },
        onDestroyContext: function (tabId) {
            life.destroy.push(tabId);
            delete mounted[tabId];
        },
        onChange: function () {},
        announce: function () {},
        publishMainShell: function () {},
    });
    if (opts.skipBootstrap) {
        return { ws: ws, hist: hist, life: life, mounted: mounted, renders: renders, persistCalls: persistCalls };
    }
    ws.bootstrap(opts.hash || '#/folders');
    return {
        ws: ws,
        hist: hist,
        life: life,
        mounted: mounted,
        renders: renders,
        persistCalls: persistCalls,
        isMounted: function (tabId) {
            return !!mounted[tabId];
        },
        mountedCount: function () {
            return Object.keys(mounted).length;
        },
    };
}

/**
 * Unlike makeHarness() above (which spies on persistNotify), this harness omits
 * persistNotify entirely so workspace-tabs.js's noteCanonicalChange() falls through to the
 * real global scheduler (root.prksScheduleWorkspacePersistence /
 * root.prksFlushWorkspacePersistence from workspace-persistence.js, attached to the same
 * Node `global` both modules use as `root`). This exercises the real writeSnapshot() path
 * end to end, including its own validation/rejection behavior against the real MemoryStorage.
 */
function makeRealPersistHarness(opts) {
    opts = opts || {};
    const hist = makeHistory(opts.hash || '#/folders');
    const ws = createPrksWorkspaceTabs({
        parseRoute: nav.prksParseRoute,
        routeLoadingTitle: nav.prksRouteLoadingTitle,
        routeTabIcon: nav.prksRouteTabIcon,
        homeHash: '#/folders',
        historyAdapter: hist,
        supportsTile: nav.prksRouteSupportsTile,
        loadSnapshot: opts.loadSnapshot,
        canLeave: opts.canLeave || function () { return true; },
        renderRoute: function () {},
        onChange: function () {},
        announce: function () {},
        publishMainShell: function () {},
    });
    ws.bootstrap(opts.hash || '#/folders');
    return { ws: ws, hist: hist };
}

function validSnapshot() {
    return {
        version: 1,
        tabs: [
            { id: 'tab-1', route: '#/works/WA', title: 'The Culture Industry', icon: 'book' },
            { id: 'tab-2', route: '#/people/PA', title: 'Adorno', icon: 'user' },
            { id: 'tab-3', route: '#/positions/PO', title: 'Position', icon: 'landmark' },
            { id: 'tab-4', route: '#/works/WB', title: 'Work B', icon: 'book' },
        ],
        mainTabId: 'tab-1',
        secondaryTree: {
            type: 'split',
            axis: 'top-bottom',
            ratio: 0.62,
            first: { type: 'leaf', tabId: 'tab-2' },
            second: {
                type: 'split',
                axis: 'left-right',
                ratio: 0.55,
                first: { type: 'leaf', tabId: 'tab-3' },
                second: { type: 'leaf', tabId: 'tab-4' },
            },
        },
        mode: 'tiled',
        mainSplitRatio: 0.7,
    };
}

async function run() {
    const snap = validSnapshot();
    const serialized = persist.prksSerializeWorkspaceSnapshot(snap);
    const parsed = persist.prksValidateWorkspaceSnapshot(JSON.parse(JSON.stringify(serialized)));
    assert('round-trip validate', !!parsed);
    assertEq('round-trip tab count', parsed.tabs.length, 4);
    assertEq('round-trip tab order', parsed.tabs.map(function (t) { return t.id; }).join(','), 'tab-1,tab-2,tab-3,tab-4');
    assertEq('round-trip main', parsed.mainTabId, 'tab-1');
    assertEq('round-trip mode', parsed.mode, 'tiled');
    assertEq('round-trip root ratio', parsed.mainSplitRatio, 0.7);
    assertEq('round-trip tree axis', parsed.secondaryTree.axis, 'top-bottom');
    assertEq('round-trip nested axis', parsed.secondaryTree.second.axis, 'left-right');
    assertEq('round-trip nested ratio', parsed.secondaryTree.second.ratio, 0.55);
    assertEq('round-trip first leaf', parsed.secondaryTree.first.tabId, 'tab-2');
    assert('serialized tree has no split id', parsed.secondaryTree.id == null);
    assert('serialized nested has no split id', parsed.secondaryTree.second.id == null);

    const keys = collectKeys(serialized, Object.create(null));
    [
        'generation',
        'mounted',
        'controller',
        'runtime',
        'narrowFallback',
        'effectiveRatio',
        'history',
        'historyIndex',
        'titleRouteGen',
        'focusedTabId',
    ].forEach(function (k) {
        assert('runtime key absent: ' + k, !keys[k]);
    });

    const runtimeish = {
        version: 1,
        tabs: [
            {
                id: 'tab-1',
                route: '#/folders',
                title: 'Folders',
                icon: 'folder',
                history: ['#/folders'],
                historyIndex: 0,
                titleRouteGen: 9,
            },
        ],
        mainTabId: 'tab-1',
        focusedTabId: 'tab-1',
        secondaryTree: null,
        mode: 'stacked',
        mainSplitRatio: 0.58,
        narrowFallback: true,
    };
    const stripped = persist.prksSerializeWorkspaceSnapshot(runtimeish);
    const strippedKeys = collectKeys(stripped, Object.create(null));
    assert('serialize drops history', !strippedKeys.history && !strippedKeys.historyIndex);
    assert('serialize drops focused/narrow', !strippedKeys.focusedTabId && !strippedKeys.narrowFallback);
    assert('serialize drops titleRouteGen', !strippedKeys.titleRouteGen);

    const restoredTree = persist.prksRehydrateWorkspaceTree(snap.secondaryTree);
    assert('rehydrate ok', !!restoredTree && restoredTree.type === 'split');
    assert('rehydrate assigns split id', typeof restoredTree.id === 'string' && restoredTree.id.indexOf('split-') === 0);
    assert('rehydrate nested id', typeof restoredTree.second.id === 'string' && restoredTree.second.id !== restoredTree.id);
    assert('rehydrate keeps topology', JSON.stringify(stripTree(restoredTree)) === JSON.stringify(stripTree(snap.secondaryTree)));

    const h = makeHarness({
        hash: '#/works/WA',
        loadSnapshot: function () {
            return persist.prksValidateWorkspaceSnapshot(validSnapshot());
        },
    });
    const restored = h.ws.snapshot();
    assertEq('restore tab count', restored.tabs.length, 4);
    assertEq('restore order', restored.tabs.map(function (t) { return t.id; }).join(','), 'tab-1,tab-2,tab-3,tab-4');
    assertEq('restore main', restored.mainTabId, 'tab-1');
    assertEq('restore mode', restored.mode, 'tiled');
    assertEq('restore preferred ratio', restored.mainSplitRatio, 0.7);
    assertEq('restore tree axis', restored.secondaryTree.axis, 'top-bottom');
    assertEq('restore nested ratio', restored.secondaryTree.second.ratio, 0.55);
    assert('restore split ids exist', !!restored.secondaryTree.id && !!restored.secondaryTree.second.id);
    assertEq('restore history rebuilt', restored.tabs[0].history.join(','), '#/works/WA');
    assertEq('restore historyIndex', restored.tabs[0].historyIndex, 0);
    assertEq('restore mounted visible count', h.mountedCount(), 4);
    assert('restore main mounted', h.isMounted('tab-1'));
    assert('restore B mounted', h.isMounted('tab-2'));
    assert('restore C mounted', h.isMounted('tab-3'));
    assert('restore D mounted', h.isMounted('tab-4'));

    const opened = await h.ws.openTab('#/folders', { activate: false });
    assertEq('next id after restore is unique', opened.id, 'tab-5');
    assert('existing tabs unchanged', h.ws.snapshot().tabs.some(function (t) { return t.id === 'tab-1'; }));
    assertEq('new tab parked', h.isMounted(opened.id), false);
    assertEq('visible mounts unchanged by parked new tab', h.mountedCount(), 4);

    const hiddenSnap = validSnapshot();
    hiddenSnap.mode = 'stacked';
    const hidden = makeHarness({
        hash: '#/works/WA',
        loadSnapshot: function () {
            return persist.prksValidateWorkspaceSnapshot(hiddenSnap);
        },
    });
    assertEq('hidden split keeps tree', hidden.ws.snapshot().secondaryTree.first.tabId, 'tab-2');
    assertEq('hidden split mode stacked', hidden.ws.snapshot().mode, 'stacked');
    assertEq('hidden split mounts only Main', hidden.mountedCount(), 1);
    assert('hidden secondaries not mounted', !hidden.isMounted('tab-2') && !hidden.isMounted('tab-3') && !hidden.isMounted('tab-4'));
    assertEq('hidden split secondary renders', hidden.renders.length, 0);

    const parkedSnap = {
        version: 1,
        tabs: [
            { id: 'tab-1', route: '#/works/WA' },
            { id: 'tab-2', route: '#/works/WB' },
            { id: 'tab-8', route: '#/people/PA' },
        ],
        mainTabId: 'tab-1',
        secondaryTree: null,
        mode: 'stacked',
        mainSplitRatio: 0.58,
    };
    const parked = makeHarness({
        hash: '#/works/WA',
        loadSnapshot: function () {
            return persist.prksValidateWorkspaceSnapshot(parkedSnap);
        },
    });
    assertEq('parked restore mounts Main only', parked.mountedCount(), 1);
    assert('parked tabs stay unmounted', !parked.isMounted('tab-2') && !parked.isMounted('tab-8'));
    const afterParked = await parked.ws.openTab('#/folders', { activate: false });
    assertEq('id generator skips to tab-9', afterParked.id, 'tab-9');

    const gapSnap = {
        version: 1,
        tabs: [
            { id: 'tab-1', route: '#/works/WA' },
            { id: 'tab-2', route: '#/people/PA' },
            { id: 'tab-4', route: '#/works/WB' },
        ],
        mainTabId: 'tab-1',
        secondaryTree: null,
        mode: 'stacked',
        mainSplitRatio: 0.58,
    };
    const gap = makeHarness({
        hash: '#/works/WA',
        loadSnapshot: function () {
            return persist.prksValidateWorkspaceSnapshot(gapSnap);
        },
    });
    const restoredGapIds = gap.ws.snapshot().tabs.map(function (t) { return t.id; });
    const gapNext = await gap.ws.openTab('#/folders', { activate: false });
    assertEq('gap restore next id is tab-5', gapNext.id, 'tab-5');
    assert('gap restore next not a restored id', restoredGapIds.indexOf(gapNext.id) === -1);
    const gapNext2 = await gap.ws.openTab('#/positions/PO', { activate: false });
    assertEq('gap restore second next is tab-6', gapNext2.id, 'tab-6');
    const gapIds = gap.ws.snapshot().tabs.map(function (t) { return t.id; });
    assertEq('gap restore ids unique', new Set(gapIds).size, gapIds.length);
    assert('gap restore kept tab-1,2,4', restoredGapIds.join(',') === 'tab-1,tab-2,tab-4');

    const deepLink = makeHarness({
        hash: '#/people/PA',
        loadSnapshot: function () {
            return persist.prksValidateWorkspaceSnapshot(validSnapshot());
        },
    });
    assertEq('direct link promotes matching tab to Main', deepLink.ws.snapshot().mainTabId, 'tab-2');
    assertEq('direct link Main route', deepLink.ws.snapshot().tabs.filter(function (t) { return t.id === 'tab-2'; })[0].route, '#/people/PA');
    assert('direct link keeps tree', !!deepLink.ws.snapshot().secondaryTree);
    assert('direct link did not drop Work A', deepLink.ws.snapshot().tabs.some(function (t) { return t.route === '#/works/WA'; }));
    assertEq('direct link stays tiled', deepLink.ws.snapshot().mode, 'tiled');
    assertEq('direct link mounts visible secondaries', deepLink.mountedCount(), 4);

    const hiddenDeepSnap = validSnapshot();
    hiddenDeepSnap.mode = 'stacked';
    const hiddenDeep = makeHarness({
        hash: '#/people/PA',
        loadSnapshot: function () {
            return persist.prksValidateWorkspaceSnapshot(hiddenDeepSnap);
        },
    });
    assertEq('hidden deep-link Main is B', hiddenDeep.ws.snapshot().mainTabId, 'tab-2');
    assertEq('hidden deep-link stays stacked', hiddenDeep.ws.snapshot().mode, 'stacked');
    assertEq('hidden deep-link mounts only Main', hiddenDeep.mountedCount(), 1);
    assert('hidden deep-link secondaries unmounted', !hiddenDeep.isMounted('tab-1') && !hiddenDeep.isMounted('tab-3') && !hiddenDeep.isMounted('tab-4'));
    assertEq('hidden deep-link no secondary render', hiddenDeep.renders.length, 0);
    assertEq('hidden deep-link tree first is old Main', hiddenDeep.ws.snapshot().secondaryTree.first.tabId, 'tab-1');
    assertEq('hidden deep-link nested C', hiddenDeep.ws.snapshot().secondaryTree.second.first.tabId, 'tab-3');
    assertEq('hidden deep-link nested D', hiddenDeep.ws.snapshot().secondaryTree.second.second.tabId, 'tab-4');
    await hiddenDeep.ws.setMode('tiled');
    assertEq('show split after hidden deep-link', hiddenDeep.ws.snapshot().mode, 'tiled');
    assert('show split remounts A', hiddenDeep.isMounted('tab-1'));
    assert('show split remounts C', hiddenDeep.isMounted('tab-3'));
    assert('show split remounts D', hiddenDeep.isMounted('tab-4'));
    assertEq('show split keeps swapped topology', hiddenDeep.ws.snapshot().secondaryTree.first.tabId, 'tab-1');
    assertEq('show split nested C preserved', hiddenDeep.ws.snapshot().secondaryTree.second.first.tabId, 'tab-3');
    assertEq('show split nested D preserved', hiddenDeep.ws.snapshot().secondaryTree.second.second.tabId, 'tab-4');

    const foldersHiddenSnap = {
        version: 1,
        tabs: [
            { id: 'tab-1', route: '#/folders', title: 'Folders', icon: 'folder' },
            { id: 'tab-2', route: '#/works/WA', title: 'Work A', icon: 'book' },
            { id: 'tab-3', route: '#/people/PA', title: 'Adorno', icon: 'user' },
        ],
        mainTabId: 'tab-1',
        secondaryTree: {
            type: 'split',
            axis: 'left-right',
            ratio: 0.5,
            first: { type: 'leaf', tabId: 'tab-2' },
            second: { type: 'leaf', tabId: 'tab-3' },
        },
        mode: 'stacked',
        mainSplitRatio: 0.58,
    };
    assert('folders hidden snap validates', !!persist.prksValidateWorkspaceSnapshot(foldersHiddenSnap));
    const foldersDeep = makeHarness({
        hash: '#/people/PA',
        loadSnapshot: function () {
            return persist.prksValidateWorkspaceSnapshot(foldersHiddenSnap);
        },
    });
    const foldersDeepSnap = foldersDeep.ws.snapshot();
    assertEq('folders hidden deep-link Main is Person', foldersDeepSnap.mainTabId, 'tab-3');
    assertEq('folders hidden deep-link stays stacked', foldersDeepSnap.mode, 'stacked');
    assertEq('folders hidden deep-link mounts only Main', foldersDeep.mountedCount(), 1);
    assert('folders hidden deep-link Folders remains', foldersDeepSnap.tabs.some(function (t) { return t.id === 'tab-1' && t.route === '#/folders'; }));
    assert('folders hidden deep-link Folders not in tree', tree.collectLeafTabIds(foldersDeepSnap.secondaryTree).indexOf('tab-1') === -1);
    assertEq('folders hidden deep-link remaining leaf', foldersDeepSnap.secondaryTree && foldersDeepSnap.secondaryTree.tabId, 'tab-2');
    assert('folders hidden deep-link Person not in tree', tree.collectLeafTabIds(foldersDeepSnap.secondaryTree).indexOf('tab-3') === -1);
    assert('folders hidden deep-link Work parked', !foldersDeep.isMounted('tab-2'));
    assertEq('folders hidden deep-link no secondary render', foldersDeep.renders.length, 0);
    const foldersDeepStored = persist.prksSerializeWorkspaceSnapshot(foldersDeepSnap);
    assert('folders hidden deep-link persistable', !!persist.prksValidateWorkspaceSnapshot(foldersDeepStored));
    await foldersDeep.ws.setMode('tiled');
    assertEq('folders hidden deep-link show split', foldersDeep.ws.snapshot().mode, 'tiled');
    assertEq('folders hidden deep-link show split leaf', foldersDeep.ws.snapshot().secondaryTree.tabId, 'tab-2');
    assert('folders hidden deep-link show split mounts Work', foldersDeep.isMounted('tab-2'));
    assert('folders hidden deep-link show split Folders unmounted', !foldersDeep.isMounted('tab-1'));

    const staleMain = validSnapshot();
    staleMain.tabs[0].route = '#/works/WA';
    const noMatch = makeHarness({
        hash: '#/people/PX',
        loadSnapshot: function () {
            return persist.prksValidateWorkspaceSnapshot(staleMain);
        },
    });
    assertEq('unmatched deep link becomes Main route', noMatch.ws.snapshot().tabs.filter(function (t) { return t.id === noMatch.ws.snapshot().mainTabId; })[0].route, '#/people/PX');
    assertEq('unmatched deep link keeps Main id', noMatch.ws.snapshot().mainTabId, 'tab-1');
    assertEq('unmatched deep link keeps other tabs', noMatch.ws.snapshot().tabs.length, 4);
    assertEq('unmatched deep link hash', noMatch.hist.getHash(), '#/people/PX');

    function rejectCase(name, mutate) {
        const bad = validSnapshot();
        mutate(bad);
        const got = persist.prksValidateWorkspaceSnapshot(bad);
        assert('reject ' + name, got == null);
    }

    rejectCase('wrong version', function (s) { s.version = 2; });
    rejectCase('missing Main', function (s) { s.mainTabId = 'tab-99'; });
    rejectCase('duplicate tab ids', function (s) { s.tabs[1].id = 'tab-1'; });
    rejectCase('duplicate tree leaves', function (s) { s.secondaryTree.second.second.tabId = 'tab-2'; });
    rejectCase('Main inside tree', function (s) { s.secondaryTree.first.tabId = 'tab-1'; });
    rejectCase('nonexistent tree tab', function (s) { s.secondaryTree.first.tabId = 'tab-99'; });
    rejectCase('invalid ratio', function (s) { s.secondaryTree.ratio = 1.5; });
    rejectCase('invalid axis', function (s) { s.secondaryTree.axis = 'vertical'; });
    rejectCase('capacity exceeded', function (s) {
        s.tabs.push({ id: 'tab-5', route: '#/works/WC' });
        s.secondaryTree = {
            type: 'split',
            axis: 'left-right',
            ratio: 0.5,
            first: { type: 'leaf', tabId: 'tab-2' },
            second: {
                type: 'split',
                axis: 'left-right',
                ratio: 0.5,
                first: { type: 'leaf', tabId: 'tab-3' },
                second: {
                    type: 'split',
                    axis: 'left-right',
                    ratio: 0.5,
                    first: { type: 'leaf', tabId: 'tab-4' },
                    second: { type: 'leaf', tabId: 'tab-5' },
                },
            },
        };
    });
    rejectCase('missing tabs', function (s) { delete s.tabs; });
    rejectCase('history field', function (s) { s.tabs[0].history = ['#/works/WA']; });
    rejectCase('unknown route', function (s) { s.tabs[0].route = '#/not-a-route'; });
    rejectCase('tiled without tree', function (s) { s.secondaryTree = null; });
    rejectCase('unsafe tab-N suffix', function (s) {
        s.tabs[0].id = 'tab-9007199254740992';
        s.mainTabId = 'tab-9007199254740992';
    });
    rejectCase('zero tab-N suffix', function (s) {
        s.tabs[0].id = 'tab-0';
        s.mainTabId = 'tab-0';
    });
    rejectCase('leading-zero tab-N', function (s) {
        s.tabs[0].id = 'tab-01';
        s.mainTabId = 'tab-01';
    });

    const customIdSnap = validSnapshot();
    customIdSnap.tabs[0].id = 'home_tab';
    customIdSnap.mainTabId = 'home_tab';
    assert('non-generated tab id still valid', !!persist.prksValidateWorkspaceSnapshot(customIdSnap));
    const maxSafeSnap = validSnapshot();
    maxSafeSnap.tabs[3].id = 'tab-' + String(Number.MAX_SAFE_INTEGER);
    maxSafeSnap.secondaryTree.second.second.tabId = maxSafeSnap.tabs[3].id;
    assert('max safe tab-N still valid', !!persist.prksValidateWorkspaceSnapshot(maxSafeSnap));

    assert('invalid JSON validate', persist.prksValidateWorkspaceSnapshot(null) == null);
    assert('string validate', persist.prksValidateWorkspaceSnapshot('{x}') == null);

    persist.prksClearWorkspaceSnapshot();
    store.store[KEY] = '{not-json';
    const loadedCorrupt = persist.prksLoadWorkspaceSnapshot();
    assert('corrupt JSON load null', loadedCorrupt == null);
    assert('corrupt JSON cleared', store.getItem(KEY) == null);

    persist.prksClearWorkspaceSnapshot();
    store.store[KEY] = JSON.stringify({ version: 99, tabs: [{ id: 'tab-1', route: '#/folders' }], mainTabId: 'tab-1' });
    assert('wrong version load null', persist.prksLoadWorkspaceSnapshot() == null);
    assert('wrong version cleared', store.getItem(KEY) == null);

    const fallback = makeHarness({
        hash: '#/folders',
        loadSnapshot: function () {
            return persist.prksLoadWorkspaceSnapshot();
        },
    });
    assertEq('corrupt fallback one Main', fallback.ws.snapshot().tabs.length, 1);
    assertEq('corrupt fallback Main route', fallback.ws.snapshot().tabs[0].route, '#/folders');
    assertEq('corrupt fallback no tree', fallback.ws.snapshot().secondaryTree, null);

    persist.prksClearWorkspaceSnapshot();
    const unsafeStored = {
        version: 1,
        tabs: [{ id: 'tab-9007199254740992', route: '#/folders' }],
        mainTabId: 'tab-9007199254740992',
        secondaryTree: null,
        mode: 'stacked',
        mainSplitRatio: 0.58,
    };
    store.store[KEY] = JSON.stringify(unsafeStored);
    assert('unsafe tab-N load null', persist.prksLoadWorkspaceSnapshot() == null);
    assert('unsafe tab-N cleared', store.getItem(KEY) == null);
    const unsafeBoot = makeHarness({
        hash: '#/folders',
        loadSnapshot: function () {
            return persist.prksLoadWorkspaceSnapshot();
        },
    });
    assertEq('unsafe tab-N fallback one Main', unsafeBoot.ws.snapshot().tabs.length, 1);
    assertEq('unsafe tab-N fallback route', unsafeBoot.ws.snapshot().tabs[0].route, '#/folders');
    assert('unsafe tab-N fallback new id', unsafeBoot.ws.snapshot().tabs[0].id !== 'tab-9007199254740992');

    persist.prksClearWorkspaceSnapshot();
    store.writes = 0;
    withFakeTimers(function (clock) {
        const a = validSnapshot();
        a.mainSplitRatio = 0.41;
        const b = validSnapshot();
        b.mainSplitRatio = 0.52;
        const c = validSnapshot();
        c.mainSplitRatio = 0.63;
        persist.prksScheduleWorkspacePersistence(a);
        assertEq('trailing debounce no write after first', store.writes, 0);
        assertEq('trailing debounce one timer after first', clock.timers.length, 1);
        persist.prksScheduleWorkspacePersistence(b);
        assertEq('trailing debounce no write after second', store.writes, 0);
        assertEq('trailing debounce timer restarted', clock.timers.length, 1);
        persist.prksScheduleWorkspacePersistence(c);
        assertEq('trailing debounce no intermediate writes', store.writes, 0);
        assertEq('trailing debounce one timer after burst', clock.timers.length, 1);
        clock.fire();
        assertEq('trailing debounce one final write', store.writes, 1);
        assertEq('trailing debounce wrote latest', JSON.parse(store.getItem(KEY)).mainSplitRatio, 0.63);
        assertEq('trailing debounce timer cleared after fire', clock.timers.length, 0);
    });

    persist.prksClearWorkspaceSnapshot();
    store.writes = 0;
    withFakeTimers(function (clock) {
        const a = validSnapshot();
        a.mainSplitRatio = 0.41;
        const b = validSnapshot();
        b.mainSplitRatio = 0.77;
        persist.prksScheduleWorkspacePersistence(a);
        persist.prksScheduleWorkspacePersistence(b);
        persist.prksFlushWorkspacePersistence();
        assertEq('flush writes immediately', store.writes, 1);
        assertEq('flush wrote latest', JSON.parse(store.getItem(KEY)).mainSplitRatio, 0.77);
        assertEq('flush cleared timer', clock.timers.length, 0);
        clock.fire();
        assertEq('flush does not double-write', store.writes, 1);
    });

    persist.prksClearWorkspaceSnapshot();
    store.writes = 0;
    persist.prksScheduleWorkspacePersistence(validSnapshot());
    persist.prksScheduleWorkspacePersistence(validSnapshot());
    persist.prksFlushWorkspacePersistence();
    assertEq('debounce coalesces writes', store.writes, 1);
    const stored = JSON.parse(store.getItem(KEY));
    assert('stored has no split ids', stored.secondaryTree.id == null && stored.secondaryTree.second.id == null);
    assertEq('stored preferred ratio', stored.mainSplitRatio, 0.7);

    persist.prksClearWorkspaceSnapshot();
    store.throwOnSet = true;
    store.writes = 0;
    persist.prksScheduleWorkspacePersistence(validSnapshot());
    persist.prksFlushWorkspacePersistence();
    store.throwOnSet = false;
    const quotaHarness = makeHarness({ hash: '#/folders', loadSnapshot: function () { return null; } });
    assertEq('quota failure still bootstraps', quotaHarness.ws.snapshot().tabs.length, 1);
    persist.prksClearWorkspaceSnapshot();

    const tileSyncs = [];
    const prevNarrowFn = global.prksWorkspaceCanvasIsNarrow;
    const prevSyncFn = global.prksWorkspaceSyncTiles;
    global.prksWorkspaceCanvasIsNarrow = function () { return true; };
    global.prksWorkspaceSyncTiles = function (_snap, opts) {
        tileSyncs.push(opts && opts.visualMode);
    };
    try {
        const narrowStart = makeHarness({
            hash: '#/works/WA',
            loadSnapshot: function () {
                return persist.prksValidateWorkspaceSnapshot(validSnapshot());
            },
        });
        const bootstrapSyncs = tileSyncs.slice();
        assertEq('narrow restore logical tiled', narrowStart.ws.snapshot().mode, 'tiled');
        assertEq('narrow restore mounts Main only', narrowStart.mountedCount(), 1);
        assert('narrow restore keeps tree', !!narrowStart.ws.snapshot().secondaryTree);
        assertEq('narrow restore preferred ratio', narrowStart.ws.snapshot().mainSplitRatio, 0.7);
        assertEq('narrow restore nested ratio', narrowStart.ws.snapshot().secondaryTree.second.ratio, 0.55);
        assert('narrow restore first paints exist', bootstrapSyncs.length > 0);
        assert('narrow restore no tiled paint', bootstrapSyncs.every(function (m) { return m !== 'tiled'; }));
        assert('narrow restore no secondary mount', !narrowStart.isMounted('tab-2') && !narrowStart.isMounted('tab-3') && !narrowStart.isMounted('tab-4'));
        const widenOk = await narrowStart.ws.setNarrowFallback(false);
        assert('narrow restore widen ok', widenOk === true);
        assertEq('narrow restore widen mounts all', narrowStart.mountedCount(), 4);
        assertEq('narrow restore widen tree first', narrowStart.ws.snapshot().secondaryTree.first.tabId, 'tab-2');
        assertEq('narrow restore widen nested C', narrowStart.ws.snapshot().secondaryTree.second.first.tabId, 'tab-3');
        assertEq('narrow restore widen nested D', narrowStart.ws.snapshot().secondaryTree.second.second.tabId, 'tab-4');
        assertEq('narrow restore widen ratio', narrowStart.ws.snapshot().mainSplitRatio, 0.7);
        assertEq('narrow restore widen nested ratio', narrowStart.ws.snapshot().secondaryTree.second.ratio, 0.55);
    } finally {
        if (prevNarrowFn) global.prksWorkspaceCanvasIsNarrow = prevNarrowFn;
        else delete global.prksWorkspaceCanvasIsNarrow;
        if (prevSyncFn) global.prksWorkspaceSyncTiles = prevSyncFn;
        else delete global.prksWorkspaceSyncTiles;
    }

    /* ---- Real writer: live unknown-route persistence must never freeze on a stale snapshot ----
     * Exercises the actual workspace-persistence.js writer end to end (no persistNotify spy):
     * bootstrap/navigate schedule through the real global scheduler, and
     * prksFlushWorkspacePersistence() drives the real writeSnapshot(). */
    persist.prksClearWorkspaceSnapshot();
    store.writes = 0;
    {
        const real = makeRealPersistHarness({ hash: '#/works/WA' });

        /* A: save a good/known-route workspace, verify it lands in storage. */
        await real.ws.navigate('#/people/PA');
        persist.prksFlushWorkspacePersistence();
        assert('real writer: A good snapshot stored', store.getItem(KEY) != null);
        const storedGood = JSON.parse(store.getItem(KEY));
        assertEq(
            'real writer: A good snapshot route',
            storedGood.tabs.find(function (t) { return t.id === storedGood.mainTabId; }).route,
            '#/people/PA'
        );

        /* B: the live Main legitimately reaches an unknown route (the router's "Section In
         * Development" fallback), then mutates + flushes. The previous good snapshot must
         * NOT be left behind looking like it still describes the current workspace. */
        await real.ws.navigate('#/this-route-does-not-exist');
        assertEq(
            'real writer: B in-memory route really is unknown',
            real.ws.snapshot().tabs.find(function (t) { return t.id === real.ws.snapshot().mainTabId; }).route,
            '#/this-route-does-not-exist'
        );
        persist.prksFlushWorkspacePersistence();
        assertEq('real writer: B live unknown route invalidates the stale snapshot', store.getItem(KEY), null);

        /* C: returning to a known/persistable route and flushing again must succeed -- B must
         * not have globally disabled persistence. */
        await real.ws.navigate('#/works/WC');
        persist.prksFlushWorkspacePersistence();
        const storedAgain = store.getItem(KEY);
        assert('real writer: C persists again after returning to a known route', storedAgain != null);
        const parsedAgain = JSON.parse(storedAgain);
        assertEq(
            'real writer: C fresh snapshot route',
            parsedAgain.tabs.find(function (t) { return t.id === parsedAgain.mainTabId; }).route,
            '#/works/WC'
        );
    }
    persist.prksClearWorkspaceSnapshot();

    /* D: read-time rejection of an unknown/corrupt persisted route stays intact -- a stored
     * snapshot whose Main route is unknown must be rejected and discarded on load, exactly
     * like the corrupt-JSON / wrong-version cases above. */
    store.writes = 0;
    store.store[KEY] = JSON.stringify({
        version: 1,
        tabs: [{ id: 'tab-1', route: '#/this-route-does-not-exist' }],
        mainTabId: 'tab-1',
        secondaryTree: null,
        mode: 'stacked',
        mainSplitRatio: 0.58,
    });
    assert('read-time unknown route load null', persist.prksLoadWorkspaceSnapshot() == null);
    assert('read-time unknown route cleared', store.getItem(KEY) == null);

    const srcFiles = [
        'workspace-tabs.js',
        'workspace-tree.js',
        'workspace-tiling.js',
        'workspace-split.js',
        'workspace-drag.js',
        'workspace-tab-menu.js',
        'tab-context.js',
    ];
    srcFiles.forEach(function (name) {
        const src = fs.readFileSync(path.join(rootDir, 'frontend/js', name), 'utf8');
        assert(name + ' has no localStorage', src.indexOf('localStorage') === -1);
        assert(name + ' has no sessionStorage', src.indexOf('sessionStorage') === -1);
        assert(name + ' has no indexedDB', src.indexOf('indexedDB') === -1);
    });
    const persistSrc = fs.readFileSync(path.join(rootDir, 'frontend/js/workspace-persistence.js'), 'utf8');
    assert('persistence module owns localStorage', persistSrc.indexOf('localStorage') !== -1);
    assert('persistence documents last-writer-wins', persistSrc.indexOf('last-writer-wins') !== -1);
    assert('persistence binds pagehide', persistSrc.indexOf('pagehide') !== -1);

    if (failed) {
        console.log(failed + ' failed, ' + passed + ' passed');
        process.exit(1);
    }
    console.log('All ' + passed + ' workspace persistence checks passed, 0 failed');
}

run().catch(function (err) {
    console.error(err);
    process.exit(1);
});
