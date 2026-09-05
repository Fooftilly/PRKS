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
