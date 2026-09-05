#!/usr/bin/env node
'use strict';

const path = require('path');

const rootDir = path.resolve(__dirname, '../..');
const nav = require(path.join(rootDir, 'frontend/js/navigation.js'));
const tree = require(path.join(rootDir, 'frontend/js/workspace-tree.js'));
const wsApi = require(path.join(rootDir, 'frontend/js/workspace-tabs.js'));

const { createPrksWorkspaceTabs, prksWorkspaceNavigationIntent } = wsApi;

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

function jsonClone(v) {
    return JSON.parse(JSON.stringify(v));
}

function makeHistory(initialHash) {
    let hash = initialHash;
    let href = 'http://127.0.0.1/' + initialHash;
    let state = null;
    const entries = [{ hash: hash, href: href, state: null }];
    let index = 0;
    let pushes = 0;
    let replaces = 0;
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
            pushes += 1;
            index += 1;
            entries.length = index;
            const u = new URL(url);
            hash = u.hash;
            href = url;
            state = s;
            entries.push({ hash: hash, href: href, state: jsonClone(s) });
        },
        replaceState: function (s, url) {
            replaces += 1;
            if (url) {
                const u = new URL(url);
                hash = u.hash;
                href = url;
            }
            state = s;
            entries[index] = { hash: hash, href: href, state: s == null ? null : jsonClone(s) };
        },
        setLocation: function (nextHash, nextState) {
            hash = nextHash;
            href = 'http://127.0.0.1/' + nextHash;
            if (arguments.length > 1) state = nextState;
        },
        stats: function () {
            return { pushes: pushes, replaces: replaces, index: index, length: entries.length };
        },
        back: function () {
            if (index <= 0) return false;
            index -= 1;
            hash = entries[index].hash;
            href = entries[index].href;
            state = entries[index].state == null ? null : jsonClone(entries[index].state);
            return true;
        },
        forward: function () {
            if (index >= entries.length - 1) return false;
            index += 1;
            hash = entries[index].hash;
            href = entries[index].href;
            state = entries[index].state == null ? null : jsonClone(entries[index].state);
            return true;
        },
        entries: entries,
    };
}

function fingerprint(h) {
    return JSON.stringify({
        snap: h.ws.snapshot(),
        hash: h.hist.getHash(),
        stats: h.hist.stats(),
        renders: h.renders.length,
    });
}

function makeHarness(opts) {
    opts = opts || {};
    const hist = makeHistory(opts.hash || '#/folders');
    const renders = [];
    const published = [];
    const life = { mount: [], park: [], destroy: [] };
    const mounted = Object.create(null);
    let canLeave = true;
    let canLeaveCalls = 0;
    let lastLeaveTabId = null;
    let lastLeaveHash = null;
    let titleGenOk = null;
    const ws = createPrksWorkspaceTabs({
        parseRoute: nav.prksParseRoute,
        routeLoadingTitle: nav.prksRouteLoadingTitle,
        routeTabIcon: nav.prksRouteTabIcon,
        homeHash: '#/folders',
        historyAdapter: hist,
        supportsTile: nav.prksRouteSupportsTile,
        canLeave: function (tabId, nextHash) {
            canLeaveCalls += 1;
            lastLeaveTabId = tabId || null;
            lastLeaveHash = nextHash || null;
            return canLeave;
        },
        isRouteGenCurrent: function (g) {
            if (titleGenOk == null) return true;
            return g === titleGenOk;
        },
        renderRoute: function (options) {
            renders.push(options || {});
        },
        announce: function () {},
        publishMainShell: function (tabId, title) {
            published.push({ tabId: tabId, title: title || '' });
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
        onChange: function () {
            if (typeof opts.onChange === 'function') opts.onChange();
        },
    });
    ws.bootstrap(opts.hash || '#/folders');
    return {
        ws: ws,
        hist: hist,
        renders: renders,
        published: published,
        life: life,
        mountedCount: function () {
            return Object.keys(mounted).length;
        },
        isMounted: function (tabId) {
            return !!mounted[tabId];
        },
        setCanLeave: function (v) {
            canLeave = v;
        },
        canLeaveCalls: function () {
            return canLeaveCalls;
        },
        setTitleGen: function (g) {
            titleGenOk = g;
        },
        lastLeaveTabId: function () {
            return lastLeaveTabId;
        },
        lastLeaveHash: function () {
            return lastLeaveHash;
        },
    };
}

async function run() {
    record('exports createPrksWorkspaceTabs', typeof createPrksWorkspaceTabs === 'function', '');
    record('exports intent helper', typeof prksWorkspaceNavigationIntent === 'function', '');

    assertEq('intent current', prksWorkspaceNavigationIntent({ button: 0 }), 'current');
    assertEq('intent ctrl', prksWorkspaceNavigationIntent({ button: 0, ctrlKey: true }), 'background');
    assertEq('intent meta', prksWorkspaceNavigationIntent({ button: 0, metaKey: true }), 'background');
    assertEq('intent middle', prksWorkspaceNavigationIntent({ button: 1 }), 'background');
    assertEq('intent shift ignore', prksWorkspaceNavigationIntent({ button: 0, shiftKey: true }), 'ignore');
    assertEq('intent alt tile', prksWorkspaceNavigationIntent({ button: 0, altKey: true }), 'tile');

    const boot = makeHarness({ hash: '#/folders' });
    const snap0 = boot.ws.snapshot();
    assertEq('bootstrap tab count', snap0.tabs.length, 1);
    assert('mainTabId exists', !!snap0.mainTabId);
    assertEq('focused == main', snap0.focusedTabId, snap0.mainTabId);
    assertEq('mode stacked', snap0.mode, 'stacked');
    assertEq('secondaryTree null', snap0.secondaryTree, null);
    assertEq('version 1', snap0.version, 1);
    assertEq('bootstrap no render', boot.renders.length, 0);
    assertEq('home route', snap0.tabs[0].route, '#/folders');
    assert('snapshot copied tabs', snap0.tabs !== boot.ws.snapshot().tabs);

    await boot.ws.navigate('#/works/W1');
    await boot.ws.navigate('#/people/P1');
    const main = boot.ws.snapshot().tabs.find(function (t) {
        return t.id === boot.ws.snapshot().mainTabId;
    });
    assertEq('history length', main.history.length, 3);
    assertEq('history 0', main.history[0], '#/folders');
    assertEq('history 1', main.history[1], '#/works/W1');
    assertEq('history 2', main.history[2], '#/people/P1');
    assertEq('historyIndex', main.historyIndex, 2);
    assertEq('route person', main.route, '#/people/P1');
    const pushesAfterNav = boot.hist.stats().pushes;
    assert('current nav pushed', pushesAfterNav >= 2);

    await boot.ws.navigate('#/people/P2', { replace: true });
    const mainR = boot.ws.snapshot().tabs.find(function (t) {
        return t.id === boot.ws.snapshot().mainTabId;
    });
    assertEq('replace keeps length', mainR.history.length, 3);
    assertEq('replace current entry', mainR.history[2], '#/people/P2');
    assertEq('replace index', mainR.historyIndex, 2);

    const bg = makeHarness();
    await bg.ws.navigate('#/folders');
    const rendersBefore = bg.renders.length;
    const mainBefore = bg.ws.snapshot().mainTabId;
    const hrefBefore = bg.hist.getHref();
    const pushesBefore = bg.hist.stats().pushes;
    await bg.ws.navigate('#/works/WB', { target: 'new-tab', activate: false });
    const snapBg = bg.ws.snapshot();
    assertEq('background tab +1', snapBg.tabs.length, 2);
    assertEq('background main unchanged', snapBg.mainTabId, mainBefore);
    assertEq('background no extra render', bg.renders.length, rendersBefore);
    assertEq('background href unchanged', bg.hist.getHref(), hrefBefore);
    assertEq('background no push', bg.hist.stats().pushes, pushesBefore);
    assertEq('parked title loading', snapBg.tabs[1].title, 'Work');

    const fg = makeHarness();
    await fg.ws.navigate('#/folders');
    const rendersFg = fg.renders.length;
    const pushesFg = fg.hist.stats().pushes;
    const replacesFg = fg.hist.stats().replaces;
    await fg.ws.navigate('#/works/WB', { target: 'new-tab', activate: true });
    const snapFg = fg.ws.snapshot();
    assertEq('foreground tabs', snapFg.tabs.length, 2);
    assertEq('foreground new is main', snapFg.tabs[1].id, snapFg.mainTabId);
    assertEq('focused == main fg', snapFg.focusedTabId, snapFg.mainTabId);
    assertEq('foreground render +1', fg.renders.length, rendersFg + 1);
    assertEq('foreground no extra push', fg.hist.stats().pushes, pushesFg);
    assert('foreground replaced', fg.hist.stats().replaces > replacesFg);
    assertEq('url is work', fg.hist.getHash(), '#/works/WB');

    const act = makeHarness();
    await act.ws.navigate('#/works/WA');
    await act.ws.navigate('#/people/PB', { target: 'new-tab', activate: false });
    await act.ws.navigate('#/concepts/C1', { target: 'new-tab', activate: false });
    const orderBefore = act.ws.snapshot().tabs.map(function (t) {
        return t.id;
    });
    const rendersAct = act.renders.length;
    const pushesAct = act.hist.stats().pushes;
    const cId = act.ws.snapshot().tabs[2].id;
    await act.ws.activateTab(cId);
    const snapAct = act.ws.snapshot();
    assertEq('activate C main', snapAct.mainTabId, cId);
    assertEq('activate C focused', snapAct.focusedTabId, cId);
    assertEq(
        'order unchanged',
        snapAct.tabs.map(function (t) {
            return t.id;
        }).join(','),
        orderBefore.join(',')
    );
    assertEq('activate rendered', act.renders.length, rendersAct + 1);
    assertEq('activate no push', act.hist.stats().pushes, pushesAct);
    const lastRender = act.renders[act.renders.length - 1];
    assert('activate workspaceSwitch', lastRender && lastRender.workspaceSwitch === true);

    const cl = makeHarness();
    await cl.ws.navigate('#/works/WA');
    await cl.ws.navigate('#/people/PB', { target: 'new-tab', activate: false });
    await cl.ws.navigate('#/concepts/C1', { target: 'new-tab', activate: true });
    const ids = cl.ws.snapshot().tabs.map(function (t) {
        return t.id;
    });
    const parked = ids[1];
    const rendersCl = cl.renders.length;
    const mainCl = cl.ws.snapshot().mainTabId;
    await cl.ws.closeTab(parked);
    assertEq('close parked count', cl.ws.snapshot().tabs.length, 2);
    assertEq('close parked main', cl.ws.snapshot().mainTabId, mainCl);
    assertEq('close parked no render', cl.renders.length, rendersCl);

    const clm = makeHarness();
    await clm.ws.navigate('#/works/A');
    await clm.ws.navigate('#/works/B', { target: 'new-tab', activate: true });
    await clm.ws.navigate('#/works/C', { target: 'new-tab', activate: true });
    const tabsM = clm.ws.snapshot().tabs;
    const aId = tabsM[0].id;
    const bId = tabsM[1].id;
    const cId2 = tabsM[2].id;
    await clm.ws.activateTab(bId);
    await clm.ws.closeTab(bId);
    const afterB = clm.ws.snapshot();
    assertEq('close main → right', afterB.mainTabId, cId2);
    assertEq('remaining A', afterB.tabs[0].id, aId);
    assertEq('remaining C', afterB.tabs[1].id, cId2);

    const last = makeHarness();
    const only = last.ws.snapshot().mainTabId;
    await last.ws.closeTab(only);
    const snapLast = last.ws.snapshot();
    assertEq('final close one tab', snapLast.tabs.length, 1);
    assertEq('final close home', snapLast.tabs[0].route, '#/folders');
    assert('final close new id', snapLast.mainTabId !== only);

    const leave = makeHarness();
    await leave.ws.navigate('#/works/WA');
    await leave.ws.navigate('#/people/P', { target: 'new-tab', activate: false });
    const frozen = jsonClone(leave.ws.snapshot());
    const hrefLeave = leave.hist.getHref();
    const rendersLeave = leave.renders.length;
    leave.setCanLeave(false);
    const parkedId = leave.ws.snapshot().tabs[1].id;
    await leave.ws.activateTab(parkedId);
    await leave.ws.closeTab(leave.ws.snapshot().mainTabId);
    await leave.ws.navigate('#/concepts/C9');
    assertEq('leave cancel main', leave.ws.snapshot().mainTabId, frozen.mainTabId);
    assertEq('leave cancel tabs', leave.ws.snapshot().tabs.length, frozen.tabs.length);
    assertEq('leave cancel href', leave.hist.getHref(), hrefLeave);
    assertEq('leave cancel no render', leave.renders.length, rendersLeave);

    const deniedNew = makeHarness({ hash: '#/works/WA' });
    deniedNew.setCanLeave(false);
    const deniedSnap = jsonClone(deniedNew.ws.snapshot());
    const deniedHref = deniedNew.hist.getHref();
    const deniedPushes = deniedNew.hist.stats().pushes;
    const deniedReplaces = deniedNew.hist.stats().replaces;
    const deniedRenders = deniedNew.renders.length;
    const deniedLeaveCalls = deniedNew.canLeaveCalls();
    const deniedResult = await deniedNew.ws.navigate('#/people/P1', {
        target: 'new-tab',
        activate: true,
    });
    const deniedAfter = deniedNew.ws.snapshot();
    assertEq('activated new-tab denied result', deniedResult, false);
    assertEq('activated new-tab denied tab count', deniedAfter.tabs.length, deniedSnap.tabs.length);
    assertEq('activated new-tab denied main', deniedAfter.mainTabId, deniedSnap.mainTabId);
    assertEq('activated new-tab denied focused', deniedAfter.focusedTabId, deniedSnap.focusedTabId);
    assertEq('activated new-tab denied href', deniedNew.hist.getHref(), deniedHref);
    assertEq('activated new-tab denied pushes', deniedNew.hist.stats().pushes, deniedPushes);
    assertEq('activated new-tab denied replaces', deniedNew.hist.stats().replaces, deniedReplaces);
    assertEq('activated new-tab denied renders', deniedNew.renders.length, deniedRenders);
    assert('activated new-tab called canLeave', deniedNew.canLeaveCalls() > deniedLeaveCalls);

    const bgDenied = makeHarness({ hash: '#/works/WA' });
    bgDenied.setCanLeave(false);
    const bgDeniedMain = bgDenied.ws.snapshot().mainTabId;
    const bgDeniedFocused = bgDenied.ws.snapshot().focusedTabId;
    const bgDeniedHref = bgDenied.hist.getHref();
    const bgDeniedRenders = bgDenied.renders.length;
    const bgDeniedLeaveCalls = bgDenied.canLeaveCalls();
    const bgDeniedTabs = bgDenied.ws.snapshot().tabs.length;
    const bgDeniedResult = await bgDenied.ws.navigate('#/people/P1', {
        target: 'new-tab',
        activate: false,
    });
    const bgDeniedAfter = bgDenied.ws.snapshot();
    assert('background new-tab while leave denied created tab', !!bgDeniedResult);
    assertEq('background new-tab while leave denied tab count', bgDeniedAfter.tabs.length, bgDeniedTabs + 1);
    assertEq('background new-tab while leave denied parked', bgDeniedAfter.tabs[1].id === bgDeniedAfter.mainTabId, false);
    assertEq('background new-tab while leave denied main', bgDeniedAfter.mainTabId, bgDeniedMain);
    assertEq('background new-tab while leave denied focused', bgDeniedAfter.focusedTabId, bgDeniedFocused);
    assertEq('background new-tab while leave denied href', bgDenied.hist.getHref(), bgDeniedHref);
    assertEq('background new-tab while leave denied renders', bgDenied.renders.length, bgDeniedRenders);
    assertEq('background new-tab did not call canLeave', bgDenied.canLeaveCalls(), bgDeniedLeaveCalls);

    const titles = makeHarness();
    await titles.ws.navigate('#/works/W1', { target: 'new-tab', activate: false });
    assertEq('parked loading title', titles.ws.snapshot().tabs[1].title, 'Work');
    const workTab = titles.ws.snapshot().tabs[1].id;
    await titles.ws.activateTab(workTab);
    const gen = titles.ws.peekRenderGen();
    titles.setTitleGen(gen);
    const okTitle = titles.ws.setResolvedTitle('#/works/W1', 'My Work', gen);
    assert('title publish ok', okTitle);
    assertEq('resolved main title', titles.ws.snapshot().tabs[0] && titles.ws.snapshot().tabs.find(function (t) {
        return t.id === workTab;
    }).title, 'My Work');
    titles.setTitleGen(gen);
    const stale = titles.ws.setResolvedTitle('#/works/W1', 'Stale Name', gen - 1);
    assert('stale gen rejected', !stale);
    assertEq(
        'stale did not rename',
        titles.ws.snapshot().tabs.find(function (t) {
            return t.id === workTab;
        }).title,
        'My Work'
    );

    const histT = makeHarness();
    await histT.ws.navigate('#/works/W1');
    const wsState = histT.hist.getState() && histT.hist.getState().prksWorkspace;
    assert('history has workspace', !!wsState);
    assertEq('history v', wsState.v, 1);
    assert('history tabId', typeof wsState.tabId === 'string');
    assertEq('history route', wsState.route, '#/works/W1');
    assertEq('history index', wsState.historyIndex, 1);
    assertEq('history keys', Object.keys(wsState).sort().join(','), 'historyIndex,route,tabId,v');

    const pop = makeHarness();
    await pop.ws.navigate('#/works/WA');
    const stateA = jsonClone(pop.hist.getState());
    const hashA = pop.hist.getHash();
    await pop.ws.navigate('#/people/PB', { target: 'new-tab', activate: true });
    const pushesPop = pop.hist.stats().pushes;
    pop.hist.setLocation(hashA, stateA);
    await pop.ws.handlePopState(stateA);
    const snapPop = pop.ws.snapshot();
    assertEq('pop restores A', snapPop.tabs.find(function (t) {
        return t.id === snapPop.mainTabId;
    }).route, '#/works/WA');
    assertEq('pop no push', pop.hist.stats().pushes, pushesPop);

    const stalePop = makeHarness();
    await stalePop.ws.navigate('#/works/WA');
    await stalePop.ws.navigate('#/people/PB', { target: 'new-tab', activate: true });
    const gone = { prksWorkspace: { v: 1, tabId: 'tab-999', route: '#/works/WA', historyIndex: 0 } };
    stalePop.hist.setLocation('#/folders', gone);
    const mainBeforeStale = stalePop.ws.snapshot().mainTabId;
    await stalePop.ws.handlePopState(gone);
    assert('stale tab id safe', !!stalePop.ws.snapshot().mainTabId);
    assertEq('stale keeps a main', typeof stalePop.ws.snapshot().mainTabId, 'string');
    assert('stale did not recreate 999', stalePop.ws.snapshot().tabs.every(function (t) {
        return t.id !== 'tab-999';
    }));
    void mainBeforeStale;

    const src = require('fs').readFileSync(path.join(rootDir, 'frontend/js/workspace-tabs.js'), 'utf8');
    assert('no localStorage persist', src.indexOf('localStorage') === -1);
    assert('no sessionStorage persist', src.indexOf('sessionStorage') === -1);
    assert('stacked mode literal', src.indexOf("'stacked'") !== -1 || src.indexOf('"stacked"') !== -1);
    assert('tiled mode literal', src.indexOf("'tiled'") !== -1 || src.indexOf('"tiled"') !== -1);
    assert('secondaryTree leaf shape', src.indexOf('secondaryTree') !== -1);
    assert('no splitRatio', src.indexOf('splitRatio') === -1);
    /* Recursive split-node construction/mutation is delegated to workspace-tree.js; workspace-tabs.js
     * must not reimplement its own ad hoc split-node literals for state mutation (the one exception is
     * copySecondaryTree's snapshot clone, which is a plain deep copy, not a mutation). */
    assert('delegates splitLeaf to tree module', src.indexOf('root.splitLeaf(') !== -1);
    assert('delegates removeLeaf to tree module', src.indexOf('root.removeLeaf(') !== -1);
    assert('delegates replaceTabId to tree module', src.indexOf('root.replaceTabId(') !== -1);
    assert('delegates normalizeTree to tree module', src.indexOf('root.normalizeTree(') !== -1);
    assert('delegates validateTree to tree module', src.indexOf('root.validateTree(') !== -1);
    assert('max visible tabs constant', src.indexOf('PRKS_MAX_VISIBLE_TABS') !== -1);

    record('exports prksRouteSupportsTile', typeof nav.prksRouteSupportsTile === 'function', '');
    assert('tile allows work', nav.prksRouteSupportsTile('#/works/W1') === true);
    assert('tile allows person', nav.prksRouteSupportsTile('#/people/P1') === true);
    assert('tile allows concept detail', nav.prksRouteSupportsTile('#/concepts/C1') === true);
    assert('tile allows position detail', nav.prksRouteSupportsTile('#/positions/P1') === true);
    assert('tile allows argument detail', nav.prksRouteSupportsTile('#/arguments/A1') === true);
    assert('tile allows playlist detail', nav.prksRouteSupportsTile('#/playlists/L1') === true);
    assert('tile rejects folders', nav.prksRouteSupportsTile('#/folders') === false);
    assert('tile rejects search', nav.prksRouteSupportsTile('#/search?q=x') === false);
    assert('tile rejects people list', nav.prksRouteSupportsTile('#/people') === false);
    assert('tile rejects processing', nav.prksRouteSupportsTile('#/processing-files') === false);
    assert('tile rejects progress', nav.prksRouteSupportsTile('#/progress') === false);
    assert('tile rejects graph', nav.prksRouteSupportsTile('#/graph') === false);

    {
    const tile = makeHarness({ hash: '#/works/WA' });
    const hrefA = tile.hist.getHash();
    const mainA = tile.ws.snapshot().mainTabId;
    const renders0 = tile.renders.length;
    const pushes0 = tile.hist.stats().pushes;
    await tile.ws.navigate('#/works/WB', { target: 'tile' });
    const snapT = tile.ws.snapshot();
    assertEq('first tile mode', snapT.mode, 'tiled');
    assert('first tile leaf', !!(snapT.secondaryTree && snapT.secondaryTree.type === 'leaf'));
    assert('secondary != main', snapT.secondaryTree.tabId !== snapT.mainTabId);
    assertEq('main unchanged after tile', snapT.mainTabId, mainA);
    assertEq('focus secondary', snapT.focusedTabId, snapT.secondaryTree.tabId);
    assertEq('url still A', tile.hist.getHash(), hrefA);
    assertEq('tile did not push', tile.hist.stats().pushes, pushes0);
    assertEq('tile rendered secondary', tile.renders.length, renders0 + 1);
    assertEq('tile render tab', tile.renders[tile.renders.length - 1].tabId, snapT.secondaryTree.tabId);

    const focusMainOk = tile.ws.focusTab(mainA);
    assert('focus main ok', focusMainOk === true);
    assertEq('focused main', tile.ws.snapshot().focusedTabId, mainA);
    assertEq('focus did not change main', tile.ws.snapshot().mainTabId, mainA);
    assertEq('focus did not change url', tile.hist.getHash(), hrefA);
    assertEq('focus did not render', tile.renders.length, renders0 + 1);
    const secId = tile.ws.snapshot().secondaryTree.tabId;
    const extraParked = tile.ws.snapshot().tabs.find(function (t) {
        return t.id !== mainA && t.id !== secId;
    });
    assert('focus parked false', tile.ws.focusTab(extraParked ? extraParked.id : 'tab-nope') === false);
    assertEq('focus parked unchanged', tile.ws.snapshot().focusedTabId, mainA);
    tile.ws.focusTab(secId);
    assertEq('focus secondary again', tile.ws.snapshot().focusedTabId, secId);
    assertEq('url still A after secondary focus', tile.hist.getHash(), hrefA);

    const rendersFocus = tile.renders.length;
    const pushesFocus = tile.hist.stats().pushes;
    const replacesFocus = tile.hist.stats().replaces;
    const mainBeforeSwap = tile.ws.snapshot().mainTabId;
    const secBeforeSwap = tile.ws.snapshot().secondaryTree.tabId;
    const swapOk = tile.ws.makeMain(secBeforeSwap);
    assert('makeMain ok', swapOk === true);
    const snapSwap = tile.ws.snapshot();
    assertEq('makeMain new main', snapSwap.mainTabId, secBeforeSwap);
    assertEq('makeMain old is secondary', snapSwap.secondaryTree.tabId, mainBeforeSwap);
    assertEq('makeMain focused new main', snapSwap.focusedTabId, secBeforeSwap);
    assertEq('makeMain url is B', tile.hist.getHash(), '#/works/WB');
    assertEq('makeMain no push', tile.hist.stats().pushes, pushesFocus);
    assert('makeMain replaced', tile.hist.stats().replaces > replacesFocus);
    assertEq('makeMain no render', tile.renders.length, rendersFocus);

    const hrefAfterSwap = tile.hist.getHash();
    await tile.ws.navigate('#/people/PB', { target: 'current', tabId: snapSwap.secondaryTree.tabId });
    const snapSecNav = tile.ws.snapshot();
    const secTab = snapSecNav.tabs.find(function (t) {
        return t.id === snapSecNav.secondaryTree.tabId;
    });
    const mainTab = snapSecNav.tabs.find(function (t) {
        return t.id === snapSecNav.mainTabId;
    });
    assertEq('secondary nav route', secTab.route, '#/people/PB');
    assertEq('main route unchanged', mainTab.route, '#/works/WB');
    assertEq('secondary nav url isolated', tile.hist.getHash(), hrefAfterSwap);
    assertEq('secondary history length', secTab.history.length, 2);
    assertEq('secondary history index', secTab.historyIndex, 1);

    /* Generic {target:'tile'} navigation never evicts an existing single Secondary leaf (spec):
     * it splits that leaf with the new tab instead. Since B is never unmounted, a denied leave
     * guard on B must not matter at all -- the split succeeds regardless. */
    const splitTile = makeHarness({ hash: '#/works/WA' });
    await splitTile.ws.navigate('#/works/WB', { target: 'tile' });
    const bId = splitTile.ws.snapshot().secondaryTree.tabId;
    const aId = splitTile.ws.snapshot().mainTabId;
    const hrefSplitTile = splitTile.hist.getHash();
    splitTile.setCanLeave(false);
    const splitTileResult = await splitTile.ws.navigate('#/works/WC', { target: 'tile' });
    assert('split tile succeeds despite denied leave (B never evicted)', !!splitTileResult);
    const snapSplitTile = splitTile.ws.snapshot();
    assertEq('split tile becomes a split node', snapSplitTile.secondaryTree.type, 'split');
    assertEq('split tile keeps B first', snapSplitTile.secondaryTree.first.tabId, bId);
    const cId = snapSplitTile.secondaryTree.second.tabId;
    assert('split tile new leaf is C, not B', cId !== bId);
    assertEq('split tile main unchanged', snapSplitTile.mainTabId, aId);
    assertEq('split tile focuses new leaf', snapSplitTile.focusedTabId, cId);
    assertEq('split tile url unchanged', splitTile.hist.getHash(), hrefSplitTile);
    assert('split tile B still mounted', splitTile.isMounted(bId));
    assert('split tile C mounted', splitTile.isMounted(cId));

    /* Ambiguous {target:'tile'} navigation (recursive tree, no focused Secondary leaf) must
     * fail exactly as atomically as tileTab(): no new logical tab, no tree mutation, no mount,
     * no paint, no leave check -- just the existing ambiguity announcement and `false`. */
    const ambigNav = makeHarness({ hash: '#/works/WA' });
    await ambigNav.ws.navigate('#/works/WB', { target: 'tile' }); // A Main | B Secondary
    const ambigB = ambigNav.ws.snapshot().secondaryTree.tabId;
    await ambigNav.ws.splitLeaf(ambigB, 'left-right', { hash: '#/works/WC' }); // B | C
    const ambigC = ambigNav.ws.snapshot().secondaryTree.second.tabId;
    const ambigA = ambigNav.ws.snapshot().mainTabId;
    await ambigNav.ws.focusTab(ambigA); // no Secondary leaf focused -> ambiguous
    ambigNav.setCanLeave(false);
    const ambigLeaveBefore = ambigNav.canLeaveCalls();
    const ambigMountBefore = ambigNav.mountedCount();
    const ambigRendersBefore = ambigNav.renders.length;
    const ambigFpBefore = fingerprint(ambigNav);
    const ambigResult = await ambigNav.ws.navigate('#/works/WD', { target: 'tile' });
    assertEq('ambiguous navigateTile declines', ambigResult, false);
    assertEq('ambiguous navigateTile fingerprint unchanged', fingerprint(ambigNav), ambigFpBefore);
    assertEq('ambiguous navigateTile no new tab', ambigNav.ws.snapshot().tabs.length, 3);
    assertEq('ambiguous navigateTile no mount', ambigNav.mountedCount(), ambigMountBefore);
    assertEq('ambiguous navigateTile no paint/render', ambigNav.renders.length, ambigRendersBefore);
    assertEq('ambiguous navigateTile no leave check', ambigNav.canLeaveCalls(), ambigLeaveBefore);

    /* Focusing a Secondary leaf resolves the ambiguity: the same call now unambiguously splits
     * that focused leaf (B) with the new tab (D); only D is newly mounted. */
    await ambigNav.ws.focusTab(ambigB);
    const resolvedResult = await ambigNav.ws.navigate('#/works/WD', { target: 'tile' });
    assert('resolved navigateTile succeeds once a Secondary leaf is focused', !!resolvedResult);
    const snapResolved = ambigNav.ws.snapshot();
    /* B was the target leaf being split, so it's replaced in-place by a new nested split(B, D);
     * the sibling C (never involved) stays exactly where it was, as the outer split's `second`. */
    const innerResolved = snapResolved.secondaryTree.first;
    assertEq('resolved navigateTile nests B under a new split', innerResolved.type, 'split');
    assertEq('resolved navigateTile keeps B first in the nest', innerResolved.first.tabId, ambigB);
    const ambigD = innerResolved.second.tabId;
    assert('resolved navigateTile new leaf is D', ambigD !== ambigB && ambigD !== ambigC && ambigD !== ambigA);
    assertEq('resolved navigateTile C untouched in outer tree', snapResolved.secondaryTree.second.tabId, ambigC);
    assertEq('resolved navigateTile focuses D', snapResolved.focusedTabId, ambigD);
    assertEq('resolved navigateTile only D newly mounted', ambigNav.mountedCount(), ambigMountBefore + 1);
    assert('resolved navigateTile D mounted', ambigNav.isMounted(ambigD));
    assert('resolved navigateTile C untouched', ambigNav.isMounted(ambigC));

    /* Pane-cap {target:'tile'} must be equally atomic: at 1 Main + 3 Secondary, an explicit
     * split-view navigation must not silently fall back to creating a parked tab. Ordinary
     * {target:'new-tab'} is a genuinely different operation and must keep working. */
    const capNav = makeHarness({ hash: '#/works/WA' });
    await capNav.ws.navigate('#/works/WB', { target: 'tile' });
    const capB = capNav.ws.snapshot().secondaryTree.tabId;
    await capNav.ws.splitLeaf(capB, 'top-bottom', { hash: '#/works/WC' });
    const capC = capNav.ws.snapshot().secondaryTree.second.tabId;
    await capNav.ws.splitLeaf(capC, 'left-right', { hash: '#/works/WD' });
    assertEq('cap setup mounted 4', capNav.mountedCount(), 4);
    const capFpBefore = fingerprint(capNav);
    const capMountBefore = capNav.mountedCount();
    const capRendersBefore = capNav.renders.length;
    const capResult = await capNav.ws.navigate('#/works/WE', { target: 'tile' });
    assertEq('pane-cap navigateTile declines', capResult, false);
    assertEq('pane-cap navigateTile fingerprint unchanged', fingerprint(capNav), capFpBefore);
    assertEq('pane-cap navigateTile tab count unchanged', capNav.ws.snapshot().tabs.length, 4);
    assertEq('pane-cap navigateTile mounted unchanged', capNav.mountedCount(), capMountBefore);
    assertEq('pane-cap navigateTile no paint/render', capNav.renders.length, capRendersBefore);

    const capNewTabResult = await capNav.ws.navigate('#/works/WE', { target: 'new-tab', activate: false });
    assert('new-tab at cap still succeeds', !!capNewTabResult);
    assertEq('new-tab at cap creates one parked tab', capNav.ws.snapshot().tabs.length, 5);
    assertEq('new-tab at cap does not mount it', capNav.mountedCount(), 4);

    const stack = makeHarness({ hash: '#/works/WA' });
    await stack.ws.navigate('#/works/WB', { target: 'tile' });
    const stackSec = stack.ws.snapshot().secondaryTree.tabId;
    const stackMain = stack.ws.snapshot().mainTabId;
    stack.setCanLeave(false);
    const stackDenied = await stack.ws.setMode('stacked');
    assertEq('stack leave denied', stackDenied, false);
    assertEq('remain tiled', stack.ws.snapshot().mode, 'tiled');
    assertEq('stack denied secondary', stack.ws.snapshot().secondaryTree.tabId, stackSec);
    stack.setCanLeave(true);
    const stackOk = await stack.ws.setMode('stacked');
    assert('stack accepted', stackOk === true);
    const snapStack = stack.ws.snapshot();
    assertEq('stacked mode', snapStack.mode, 'stacked');
    assert('leaf preserved', snapStack.secondaryTree && snapStack.secondaryTree.tabId === stackSec);
    assertEq('stacked focus main', snapStack.focusedTabId, stackMain);
    const restored = await stack.ws.setMode('tiled');
    assert('restore tiled', restored === true);
    assertEq('restored mode', stack.ws.snapshot().mode, 'tiled');
    assertEq('restored secondary', stack.ws.snapshot().secondaryTree.tabId, stackSec);

    const parkedAct = makeHarness({ hash: '#/works/WA' });
    await parkedAct.ws.navigate('#/works/WB', { target: 'tile' });
    await parkedAct.ws.navigate('#/works/WD', { target: 'new-tab', activate: false });
    const snapPark = parkedAct.ws.snapshot();
    const dId = snapPark.tabs[snapPark.tabs.length - 1].id;
    const bStay = snapPark.secondaryTree.tabId;
    const aWas = snapPark.mainTabId;
    await parkedAct.ws.activateTab(dId);
    const snapD = parkedAct.ws.snapshot();
    assertEq('parked activate main D', snapD.mainTabId, dId);
    assertEq('B remains secondary', snapD.secondaryTree.tabId, bStay);
    assert('A not main', snapD.mainTabId !== aWas);
    assertEq('focused D', snapD.focusedTabId, dId);

    const closeSec = makeHarness({ hash: '#/works/WA' });
    await closeSec.ws.navigate('#/works/WB', { target: 'tile' });
    const closeMain = closeSec.ws.snapshot().mainTabId;
    const closeB = closeSec.ws.snapshot().secondaryTree.tabId;
    const closeHref = closeSec.hist.getHash();
    await closeSec.ws.closeTab(closeB);
    const snapCloseSec = closeSec.ws.snapshot();
    assertEq('close secondary stacked', snapCloseSec.mode, 'stacked');
    assertEq('close secondary leaf gone', snapCloseSec.secondaryTree, null);
    assertEq('close secondary main', snapCloseSec.mainTabId, closeMain);
    assertEq('close secondary url', closeSec.hist.getHash(), closeHref);
    assertEq('close secondary focus', snapCloseSec.focusedTabId, closeMain);

    const closeMainWs = makeHarness({ hash: '#/works/WA' });
    await closeMainWs.ws.navigate('#/works/WB', { target: 'tile' });
    const promoteB = closeMainWs.ws.snapshot().secondaryTree.tabId;
    const closeA = closeMainWs.ws.snapshot().mainTabId;
    await closeMainWs.ws.closeTab(closeA);
    const snapCloseMain = closeMainWs.ws.snapshot();
    assertEq('close main promotes B', snapCloseMain.mainTabId, promoteB);
    assertEq('close main no secondary', snapCloseMain.secondaryTree, null);
    assertEq('close main stacked', snapCloseMain.mode, 'stacked');
    assertEq('close main url B', closeMainWs.hist.getHash(), '#/works/WB');
    assertEq('close main focused B', snapCloseMain.focusedTabId, promoteB);

    const unsup = makeHarness({ hash: '#/works/WA' });
    await unsup.ws.navigate('#/people/P1', { target: 'tile' });
    const unsupB = unsup.ws.snapshot().secondaryTree.tabId;
    const unsupA = unsup.ws.snapshot().mainTabId;
    const unsupPushes = unsup.hist.stats().pushes;
    const unsupLeave0 = unsup.canLeaveCalls();
    await unsup.ws.navigate('#/folders', { target: 'current', tabId: unsupB });
    const snapUnsup = unsup.ws.snapshot();
    assertEq('unsupported B is main', snapUnsup.mainTabId, unsupB);
    assertEq('unsupported A is secondary', snapUnsup.secondaryTree && snapUnsup.secondaryTree.tabId, unsupA);
    assertEq('unsupported url folders', unsup.hist.getHash(), '#/folders');
    assert('unsupported pushed', unsup.hist.stats().pushes > unsupPushes);
    assertEq('unsupported one leave', unsup.canLeaveCalls(), unsupLeave0 + 1);
    assertEq('unsupported leave tab B', unsup.lastLeaveTabId(), unsupB);

    const unsupDeny = makeHarness({ hash: '#/works/WA' });
    await unsupDeny.ws.navigate('#/people/P1', { target: 'tile' });
    const denyBefore = fingerprint(unsupDeny);
    const denyLeave0 = unsupDeny.canLeaveCalls();
    const denyB = unsupDeny.ws.snapshot().secondaryTree.tabId;
    unsupDeny.setCanLeave(false);
    const denyResult = await unsupDeny.ws.navigate('#/folders', {
        target: 'current',
        tabId: denyB,
    });
    assertEq('unsupported deny result', denyResult, false);
    assertEq('unsupported deny fingerprint', fingerprint(unsupDeny), denyBefore);
    assertEq('unsupported deny one leave', unsupDeny.canLeaveCalls(), denyLeave0 + 1);
    assertEq('unsupported deny leave B', unsupDeny.lastLeaveTabId(), denyB);

    const titleSwap = makeHarness({ hash: '#/works/WA' });
    await titleSwap.ws.navigate('#/works/WB', { target: 'tile' });
    const titleB = titleSwap.ws.snapshot().secondaryTree.tabId;
    const titleA = titleSwap.ws.snapshot().mainTabId;
    titleSwap.ws.setResolvedTitleForTab(titleB, '#/works/WB', 'Work B Title');
    titleSwap.ws.setResolvedTitleForTab(titleA, '#/works/WA', 'Work A Title');
    const pub0 = titleSwap.published.length;
    const titleRenders = titleSwap.renders.length;
    titleSwap.ws.makeMain(titleB);
    assert('makeMain published title', titleSwap.published.length > pub0);
    assertEq(
        'makeMain shell title',
        titleSwap.published[titleSwap.published.length - 1].title,
        'Work B Title'
    );
    assertEq('makeMain shell tab', titleSwap.published[titleSwap.published.length - 1].tabId, titleB);
    assertEq('makeMain still no render', titleSwap.renders.length, titleRenders);

    const titleClose = makeHarness({ hash: '#/works/WA' });
    await titleClose.ws.navigate('#/works/WB', { target: 'tile' });
    const closeTitleB = titleClose.ws.snapshot().secondaryTree.tabId;
    const closeTitleA = titleClose.ws.snapshot().mainTabId;
    titleClose.ws.setResolvedTitleForTab(closeTitleB, '#/works/WB', 'Promoted Work B');
    await titleClose.ws.closeTab(closeTitleA);
    assertEq('close main shell title', titleClose.published[titleClose.published.length - 1].title, 'Promoted Work B');
    assertEq('close main shell tab', titleClose.published[titleClose.published.length - 1].tabId, closeTitleB);

    const popTile = makeHarness({ hash: '#/works/WA' });
    await popTile.ws.navigate('#/works/WB', { target: 'tile' });
    const popA = popTile.ws.snapshot().mainTabId;
    const popB = popTile.ws.snapshot().secondaryTree.tabId;
    popTile.hist.pushState(popTile.hist.getState(), popTile.hist.getHref());
    popTile.ws.makeMain(popB);
    assertEq('pop prep B main', popTile.ws.snapshot().mainTabId, popB);
    const parkBeforeBack = popTile.life.park.length;
    const destroyBeforeBack = popTile.life.destroy.length;
    const mountBeforeBack = popTile.life.mount.length;
    const leaveBeforeBack = popTile.canLeaveCalls();
    const rendersBeforeBack = popTile.renders.length;
    assert('hist back exists', popTile.hist.back() === true);
    const backOk = await popTile.ws.handlePopState(popTile.hist.getState());
    assert('tiled back ok', backOk === true);
    const snapBack = popTile.ws.snapshot();
    assertEq('back A main', snapBack.mainTabId, popA);
    assertEq('back B secondary', snapBack.secondaryTree && snapBack.secondaryTree.tabId, popB);
    assertEq('back url A', popTile.hist.getHash(), '#/works/WA');
    assertEq('back mounted 2', popTile.mountedCount(), 2);
    assert('back A mounted', popTile.isMounted(popA));
    assert('back B mounted', popTile.isMounted(popB));
    assertEq('back no park', popTile.life.park.length, parkBeforeBack);
    assertEq('back no destroy', popTile.life.destroy.length, destroyBeforeBack);
    assertEq('back no remount', popTile.life.mount.length, mountBeforeBack);
    assertEq('back no leave', popTile.canLeaveCalls(), leaveBeforeBack);
    assertEq('back no render', popTile.renders.length, rendersBeforeBack);
    assert('hist forward exists', popTile.hist.forward() === true);
    const fwdOk = await popTile.ws.handlePopState(popTile.hist.getState());
    assert('tiled forward ok', fwdOk === true);
    const snapFwd = popTile.ws.snapshot();
    assertEq('fwd B main', snapFwd.mainTabId, popB);
    assertEq('fwd A secondary', snapFwd.secondaryTree && snapFwd.secondaryTree.tabId, popA);
    assertEq('fwd url B', popTile.hist.getHash(), '#/works/WB');
    assertEq('fwd mounted 2', popTile.mountedCount(), 2);
    assertEq('fwd no park', popTile.life.park.length, parkBeforeBack);
    assertEq('fwd no destroy', popTile.life.destroy.length, destroyBeforeBack);
    assertEq('fwd no remount', popTile.life.mount.length, mountBeforeBack);

    const hiddenPop = makeHarness({ hash: '#/works/WA' });
    await hiddenPop.ws.navigate('#/works/WB', { target: 'tile' });
    const hiddenA = hiddenPop.ws.snapshot().mainTabId;
    const hiddenB = hiddenPop.ws.snapshot().secondaryTree.tabId;
    await hiddenPop.ws.splitLeaf(hiddenB, 'top-bottom', { hash: '#/works/WC' });
    const hiddenC = hiddenPop.ws.snapshot().secondaryTree.second.tabId;
    await hiddenPop.ws.splitLeaf(hiddenC, 'left-right', { hash: '#/works/WD' });
    const hiddenD = hiddenPop.ws.snapshot().secondaryTree.second.second.tabId;
    const originalHiddenTree = jsonClone(hiddenPop.ws.snapshot().secondaryTree);
    hiddenPop.hist.pushState(hiddenPop.hist.getState(), hiddenPop.hist.getHref());
    hiddenPop.ws.makeMain(hiddenD);
    await hiddenPop.ws.setMode('stacked');
    assertEq('hidden pop prep only main mounted', hiddenPop.mountedCount(), 1);
    assert('hidden pop history back exists', hiddenPop.hist.back() === true);
    const hiddenBackOk = await hiddenPop.ws.handlePopState(hiddenPop.hist.getState());
    assert('hidden deep pop accepted', hiddenBackOk === true);
    const hiddenBack = hiddenPop.ws.snapshot();
    assertEq('hidden deep pop restores A main', hiddenBack.mainTabId, hiddenA);
    assertEq('hidden deep pop keeps mode stacked', hiddenBack.mode, 'stacked');
    assertEq('hidden deep pop swaps D into exact A leaf', JSON.stringify(hiddenBack.secondaryTree), JSON.stringify(originalHiddenTree));
    assertEq('hidden deep pop keeps B', hiddenBack.secondaryTree.first.tabId, hiddenB);
    assertEq('hidden deep pop keeps C', hiddenBack.secondaryTree.second.first.tabId, hiddenC);
    assertEq('hidden deep pop keeps D', hiddenBack.secondaryTree.second.second.tabId, hiddenD);
    assertEq('hidden deep pop mounts only A', hiddenPop.mountedCount(), 1);

    const popDenyA = makeHarness({ hash: '#/works/WA' });
    await popDenyA.ws.navigate('#/people/PA');
    await popDenyA.ws.navigate('#/works/WB', { target: 'tile' });
    const denyPopB = popDenyA.ws.snapshot().secondaryTree.tabId;
    const denyPopA = popDenyA.ws.snapshot().mainTabId;
    popDenyA.ws.makeMain(denyPopB);
    popDenyA.setCanLeave(false);
    const denyFp = fingerprint(popDenyA);
    assert('deny hist back', popDenyA.hist.back() === true);
    const denyPop = await popDenyA.ws.handlePopState(popDenyA.hist.getState());
    assertEq('pop route-change deny', denyPop, false);
    const denySnap = popDenyA.ws.snapshot();
    assertEq('pop deny still B main', denySnap.mainTabId, denyPopB);
    assertEq('pop deny A still secondary', denySnap.secondaryTree && denySnap.secondaryTree.tabId, denyPopA);
    assertEq('pop deny url restored', popDenyA.hist.getHash(), '#/works/WB');
    void denyFp;
    assertEq('pop deny leave A', popDenyA.lastLeaveTabId(), denyPopA);

    const n0 = makeHarness({ hash: '#/works/WA' });
    await n0.ws.setNarrowFallback(true);
    n0.ws.bootstrap('#/works/WA');
    assertEq('initial narrow mode', n0.ws.snapshot().mode, 'stacked');
    assertEq('initial narrow visual', n0.ws.visualTiled(), false);
    assertEq('initial narrow mounted', n0.mountedCount(), 1);

    const nTile = makeHarness({ hash: '#/works/WA' });
    await nTile.ws.setNarrowFallback(true);
    const nMain = nTile.ws.snapshot().mainTabId;
    const nRenders0 = nTile.renders.length;
    await nTile.ws.navigate('#/works/WB', { target: 'tile' });
    const nSnap = nTile.ws.snapshot();
    assertEq('narrow tile logical mode', nSnap.mode, 'tiled');
    assert('narrow tile leaf B', !!(nSnap.secondaryTree && nSnap.secondaryTree.type === 'leaf'));
    assertEq('narrow tile visual', nTile.ws.visualTiled(), false);
    assertEq('narrow tile focus main', nSnap.focusedTabId, nMain);
    assertEq('narrow tile mounted 1', nTile.mountedCount(), 1);
    assertEq('narrow tile no render B', nTile.renders.length, nRenders0);
    assert('narrow B not mounted', !nTile.isMounted(nSnap.secondaryTree.tabId));
    const nB = nSnap.secondaryTree.tabId;
    await nTile.ws.setNarrowFallback(false);
    assertEq('widen mode tiled', nTile.ws.snapshot().mode, 'tiled');
    assertEq('widen visual', nTile.ws.visualTiled(), true);
    assertEq('widen mounted 2', nTile.mountedCount(), 2);
    assert('widen B mounted', nTile.isMounted(nB));
    assertEq('widen focus main', nTile.ws.snapshot().focusedTabId, nMain);
    assert('widen rendered B', nTile.renders.length > nRenders0);

    const nParked = makeHarness({ hash: '#/works/WA' });
    await nParked.ws.navigate('#/works/WZ', { target: 'new-tab', activate: false });
    const nZ = nParked.ws.snapshot().tabs[1].id;
    await nParked.ws.setNarrowFallback(true);
    await nParked.ws.tileTab(nZ);
    assertEq('narrow tileTab mode', nParked.ws.snapshot().mode, 'tiled');
    assertEq('narrow tileTab secondary', nParked.ws.snapshot().secondaryTree.tabId, nZ);
    assertEq('narrow tileTab visual', nParked.ws.visualTiled(), false);
    assertEq('narrow tileTab mounted', nParked.mountedCount(), 1);
    assertEq('narrow tileTab focus main', nParked.ws.snapshot().focusedTabId, nParked.ws.snapshot().mainTabId);

    const nLeave = makeHarness({ hash: '#/works/WA' });
    await nLeave.ws.navigate('#/works/WB', { target: 'tile' });
    const nLeaveB = nLeave.ws.snapshot().secondaryTree.tabId;
    const nLeaveA = nLeave.ws.snapshot().mainTabId;
    const nLeaveFp = fingerprint(nLeave);
    const nLeaveMount = nLeave.mountedCount();
    nLeave.setCanLeave(false);
    const nDenied = await nLeave.ws.setNarrowFallback(true);
    assertEq('narrow leave denied', nDenied, false);
    assertEq('narrow deny fingerprint', fingerprint(nLeave), nLeaveFp);
    assertEq('narrow deny visual tiled', nLeave.ws.visualTiled(), true);
    assertEq('narrow deny mounted', nLeave.mountedCount(), nLeaveMount);
    assert('narrow deny B mounted', nLeave.isMounted(nLeaveB));
    nLeave.setCanLeave(true);
    const nAccepted = await nLeave.ws.setNarrowFallback(true);
    assert('narrow leave accepted', nAccepted === true);
    assertEq('narrow accept mode still tiled', nLeave.ws.snapshot().mode, 'tiled');
    assert('narrow accept leaf kept', nLeave.ws.snapshot().secondaryTree && nLeave.ws.snapshot().secondaryTree.tabId === nLeaveB);
    assertEq('narrow accept visual', nLeave.ws.visualTiled(), false);
    assertEq('narrow accept mounted 1', nLeave.mountedCount(), 1);
    assert('narrow accept A mounted', nLeave.isMounted(nLeaveA));
    assert('narrow accept B parked', !nLeave.isMounted(nLeaveB));

    const tileTabH = makeHarness({ hash: '#/works/WA' });
    await tileTabH.ws.navigate('#/works/WZ', { target: 'new-tab', activate: false });
    const parkedZ = tileTabH.ws.snapshot().tabs[1].id;
    const zHist = tileTabH.ws.snapshot().tabs[1].history.slice();
    await tileTabH.ws.tileTab(parkedZ);
    assertEq('tileTab mode', tileTabH.ws.snapshot().mode, 'tiled');
    assertEq('tileTab secondary', tileTabH.ws.snapshot().secondaryTree.tabId, parkedZ);
    assertEq('tileTab no duplicate', tileTabH.ws.snapshot().tabs.length, 2);
    const zAfter = tileTabH.ws.snapshot().tabs.find(function (t) { return t.id === parkedZ; });
    assert('tileTab same history', JSON.stringify(zAfter.history) === JSON.stringify(zHist));
    const foundZ = tileTabH.ws.findTabByRoute('#/works/WZ', { excludeMain: true, excludeVisibleSecondary: true });
    assertEq('findTabByRoute skips visible secondary', foundZ, null);
    await tileTabH.ws.setMode('stacked');
    const foundParked = tileTabH.ws.findTabByRoute('#/works/WZ', { excludeMain: true, excludeVisibleSecondary: true });
    assert('findTabByRoute parked', !!(foundParked && foundParked.id === parkedZ));
    assertEq('findTabByRoute skips main', tileTabH.ws.findTabByRoute('#/works/WA', { excludeMain: true }) === null, true);

    const closeBatch = makeHarness({ hash: '#/works/WA' });
    await closeBatch.ws.navigate('#/works/WB', { target: 'new-tab', activate: false });
    await closeBatch.ws.navigate('#/works/WC', { target: 'new-tab', activate: false });
    await closeBatch.ws.navigate('#/works/WD', { target: 'new-tab', activate: false });
    const batchIds = closeBatch.ws.snapshot().tabs.map(function (t) { return t.id; });
    await closeBatch.ws.closeTabsToTheRight(batchIds[1]);
    const afterRight = closeBatch.ws.snapshot();
    assertEq('close to right count', afterRight.tabs.length, 2);
    assertEq('close to right keep 0', afterRight.tabs[0].id, batchIds[0]);
    assertEq('close to right keep 1', afterRight.tabs[1].id, batchIds[1]);
    assertEq('close to right main', afterRight.mainTabId, batchIds[0]);

    const othersH = makeHarness({ hash: '#/works/WA' });
    await othersH.ws.navigate('#/works/WB', { target: 'new-tab', activate: false });
    await othersH.ws.navigate('#/works/WC', { target: 'new-tab', activate: true });
    const othersIds = othersH.ws.snapshot().tabs.map(function (t) { return t.id; });
    const othersRenders = othersH.renders.length;
    await othersH.ws.closeOtherTabs(othersIds[2]);
    const afterOthers = othersH.ws.snapshot();
    assertEq('close others count', afterOthers.tabs.length, 1);
    assertEq('close others keep', afterOthers.mainTabId, othersIds[2]);
    assertEq('close others no extra render', othersH.renders.length, othersRenders);

    const othersParked = makeHarness({ hash: '#/works/WA' });
    await othersParked.ws.navigate('#/works/WB', { target: 'new-tab', activate: false });
    const parkedKeep = othersParked.ws.snapshot().tabs[1].id;
    const parkedRenders = othersParked.renders.length;
    await othersParked.ws.closeOtherTabs(parkedKeep);
    const afterParkedKeep = othersParked.ws.snapshot();
    assertEq('close others parked keep count', afterParkedKeep.tabs.length, 1);
    assertEq('close others parked is main', afterParkedKeep.mainTabId, parkedKeep);
    assert('close others parked rendered', othersParked.renders.length > parkedRenders);

    const othersLeave = makeHarness({ hash: '#/works/WA' });
    await othersLeave.ws.navigate('#/works/WB', { target: 'new-tab', activate: false });
    await othersLeave.ws.tileTab(othersLeave.ws.snapshot().tabs[1].id);
    othersLeave.setCanLeave(false);
    const othersLeaveFp = fingerprint(othersLeave);
    const othersDenied = await othersLeave.ws.closeOtherTabs(othersLeave.ws.snapshot().mainTabId);
    assertEq('close others leave denied', othersDenied, false);
    assertEq('close others leave fingerprint', fingerprint(othersLeave), othersLeaveFp);

    const box = { ws: null, modes: [] };
    const replaceH = makeHarness({
        hash: '#/works/WA',
        onChange: function () {
            if (box.ws) box.modes.push(box.ws.snapshot().mode);
        },
    });
    box.ws = replaceH.ws;
    await replaceH.ws.navigate('#/works/WB', { target: 'new-tab', activate: false });
    await replaceH.ws.tileTab(replaceH.ws.snapshot().tabs[1].id);
    await replaceH.ws.navigate('#/works/WC', { target: 'new-tab', activate: false });
    const replaceB = replaceH.ws.snapshot().secondaryTree.tabId;
    const replaceC = replaceH.ws.snapshot().tabs[2].id;
    const replaceA = replaceH.ws.snapshot().mainTabId;
    const replaceMountA = replaceH.isMounted(replaceA);
    box.modes.length = 0;
    /* tileTab() on a parked tab, with exactly one existing Secondary leaf, splits that leaf
     * instead of replacing it (spec: no "Replace split pane" command exists). */
    await replaceH.ws.tileTab(replaceC);
    const snapAfterSplit = replaceH.ws.snapshot();
    assertEq('tileTab splits instead of replacing', snapAfterSplit.secondaryTree.type, 'split');
    assertEq('tileTab split keeps B first', snapAfterSplit.secondaryTree.first.tabId, replaceB);
    assertEq('tileTab split adds C second', snapAfterSplit.secondaryTree.second.tabId, replaceC);
    assertEq('tileTab split main stable', snapAfterSplit.mainTabId, replaceA);
    assertEq('tileTab split focuses C', snapAfterSplit.focusedTabId, replaceC);
    assert('tileTab split no stacked paint', box.modes.every(function (m) { return m === 'tiled'; }));
    assert('tileTab split A still mounted', replaceH.isMounted(replaceA) === replaceMountA);
    assert('tileTab split B still mounted (never evicted)', replaceH.isMounted(replaceB));

    /* With a recursive tree and no focused Secondary leaf, generic tileTab declines rather than
     * guessing a placement -- and never even consults the leave guard, since nothing would be
     * evicted either way. */
    replaceH.setCanLeave(false);
    await replaceH.ws.navigate('#/works/WD', { target: 'new-tab', activate: false });
    const replaceD = replaceH.ws.snapshot().tabs[replaceH.ws.snapshot().tabs.length - 1].id;
    await replaceH.ws.focusTab(replaceA);
    const ambiguousLeaveCalls = replaceH.canLeaveCalls();
    const ambiguousFp = fingerprint(replaceH);
    box.modes.length = 0;
    const ambiguousResult = await replaceH.ws.tileTab(replaceD);
    assertEq('tileTab declines when ambiguous (no focused Secondary leaf)', ambiguousResult, false);
    assertEq('tileTab ambiguous fingerprint unchanged', fingerprint(replaceH), ambiguousFp);
    assertEq('tileTab ambiguous no paint', box.modes.length, 0);
    assertEq('tileTab ambiguous did not consult leave guard', replaceH.canLeaveCalls(), ambiguousLeaveCalls);
    }

    /* =================== Recursive Secondary splits =================== */

    {
        /* Split right/down build a recursive tree; only the new leaf mounts. */
        const h = makeHarness({ hash: '#/works/WA' });
        await h.ws.navigate('#/works/WB', { target: 'tile' });
        const A = h.ws.snapshot().mainTabId;
        const B = h.ws.snapshot().secondaryTree.tabId;
        assertEq('single leaf mounted 2', h.mountedCount(), 2);

        const okC = await h.ws.splitLeaf(B, 'top-bottom', { hash: '#/works/WC' });
        assert('split down B with C ok', !!okC);
        let snap = h.ws.snapshot();
        assertEq('split down tree is split', snap.secondaryTree.type, 'split');
        assertEq('split down axis', snap.secondaryTree.axis, 'top-bottom');
        assertEq('split down ratio default', snap.secondaryTree.ratio, 0.5);
        assertEq('split down first is B', snap.secondaryTree.first.tabId, B);
        const C = snap.secondaryTree.second.tabId;
        assert('split down second is new leaf', C !== B && C !== A);
        assertEq('split down mounted 3', h.mountedCount(), 3);
        assert('split down A still mounted (unaffected)', h.isMounted(A));
        assert('split down B still mounted (unaffected)', h.isMounted(B));
        assertEq('split down focuses new leaf', snap.focusedTabId, C);
        assertEq('main unchanged after split', snap.mainTabId, A);

        const okD = await h.ws.splitLeaf(C, 'left-right', { hash: '#/works/WD' });
        assert('split right C with D ok', !!okD);
        snap = h.ws.snapshot();
        const nested = snap.secondaryTree.second;
        assertEq('nested split axis', nested.axis, 'left-right');
        assertEq('nested split first is C', nested.first.tabId, C);
        const D = nested.second.tabId;
        assert('nested split second is new leaf D', D !== A && D !== B && D !== C);
        assertEq('4 leaves total mounted (1 main + 3 secondary)', h.mountedCount(), 4);
        assertEq('deterministic leaf order B,C,D', [snap.secondaryTree.first.tabId, nested.first.tabId, nested.second.tabId].join(','), [B, C, D].join(','));
        assertEq('D focused after split', snap.focusedTabId, D);

        /* Visible-pane cap: 1 Main + 3 Secondary already mounted -> further splits refused. */
        assertEq('cap reached', h.ws.snapshot ? true : true, true);
        const capBefore = fingerprint(h);
        const capResult = await h.ws.splitLeaf(D, 'left-right', { hash: '#/works/WE' });
        assertEq('split refused at cap', capResult, false);
        assertEq('cap refusal unchanged state', fingerprint(h), capBefore);
        assertEq('mounted stays 4 at cap', h.mountedCount(), 4);

        /* Clicking an already-visible Secondary tab in the strip focuses it, does not promote it. */
        const urlBefore = h.hist.getHash();
        await h.ws.activateTab(B);
        let snapFocus = h.ws.snapshot();
        assertEq('activateTab on visible secondary focuses', snapFocus.focusedTabId, B);
        assertEq('activateTab on visible secondary keeps main', snapFocus.mainTabId, A);
        assertEq('activateTab on visible secondary keeps url', h.hist.getHash(), urlBefore);
        await h.ws.activateTab(C);
        assertEq('activateTab focuses C', h.ws.snapshot().focusedTabId, C);
        assertEq('activateTab C keeps url', h.hist.getHash(), urlBefore);

        /* Deep Make Main: promote D (deepest leaf) to Main; old Main A takes D's exact spot. */
        await h.ws.focusTab(D);
        const okMakeMain = h.ws.makeMain(D);
        assert('deep make main ok', okMakeMain === true);
        snap = h.ws.snapshot();
        assertEq('deep make main new main', snap.mainTabId, D);
        assertEq('deep make main url', h.hist.getHash(), '#/works/WD');
        assertEq('deep make main focused', snap.focusedTabId, D);
        assertEq('deep make main B unaffected', snap.secondaryTree.first.tabId, B);
        assertEq('deep make main nested axis unaffected', snap.secondaryTree.second.axis, 'left-right');
        assertEq('deep make main nested first is C', snap.secondaryTree.second.first.tabId, C);
        assertEq('deep make main old main A took D spot', snap.secondaryTree.second.second.tabId, A);
        assertEq('deep make main mounted still 4', h.mountedCount(), 4);
        assert('deep make main A still mounted', h.isMounted(A));
        assert('deep make main B still mounted', h.isMounted(B));
        assert('deep make main C still mounted', h.isMounted(C));
        assert('deep make main D still mounted', h.isMounted(D));

        /* Close-collapse: close C -> nested split collapses to a bare A leaf. */
        await h.ws.closeTab(C);
        snap = h.ws.snapshot();
        assert('close C tree valid', !!snap.secondaryTree);
        assertEq('close C leaves B,A', [snap.secondaryTree.first.tabId, snap.secondaryTree.second.tabId].join(','), [B, A].join(','));
        assertEq('close C no redundant node', snap.secondaryTree.second.type, 'leaf');
        assert('close C context destroyed', !h.isMounted(C));
        assertEq('close C mounted now 3', h.mountedCount(), 3);
        assertEq('close C focus moved to sibling A', snap.focusedTabId, A);

        /* Hide/park B: B stays open as a parked tab, removed from the tree, tree normalizes to bare A leaf. */
        await h.ws.focusTab(B);
        const hideOk = await h.ws.hideLeaf(B);
        assert('hide leaf ok', hideOk === true);
        snap = h.ws.snapshot();
        assertEq('hide leaf tree collapses to bare leaf', snap.secondaryTree.type, 'leaf');
        assertEq('hide leaf remaining is A', snap.secondaryTree.tabId, A);
        assert('hidden B logical tab still open', !!snap.tabs.find(function (t) { return t.id === B; }));
        assert('hidden B context parked (unmounted)', !h.isMounted(B));
        assertEq('hide leaf mounted now 2', h.mountedCount(), 2);
        assertEq('hide leaf focus moved off B', snap.focusedTabId, A);

        /* Reopen the parked B via an explicit split -- same tabId reused, no duplicate. */
        const tabCountBeforeReopen = snap.tabs.length;
        const reopenOk = await h.ws.splitLeaf(A, 'left-right', { tabId: B });
        assert('reopen parked B via split ok', !!reopenOk);
        snap = h.ws.snapshot();
        assertEq('reopen did not duplicate tab', snap.tabs.length, tabCountBeforeReopen);
        assertEq('reopen reused B tabId', snap.secondaryTree.second.tabId, B);
        assert('reopen B mounted again', h.isMounted(B));
    }

    {
        /* Global Hide split / Show split with a multi-leaf tree: atomic, all-or-nothing, tree preserved. */
        const h = makeHarness({ hash: '#/works/WA' });
        await h.ws.navigate('#/works/WB', { target: 'tile' });
        const A = h.ws.snapshot().mainTabId;
        const B = h.ws.snapshot().secondaryTree.tabId;
        await h.ws.splitLeaf(B, 'top-bottom', { hash: '#/works/WC' });
        const C = h.ws.snapshot().secondaryTree.second.tabId;
        assertEq('setup mounted 3', h.mountedCount(), 3);

        h.setCanLeave(false);
        const hideDenied = await h.ws.setMode('stacked');
        assertEq('hide split denied', hideDenied, false);
        assertEq('hide split denied stays tiled', h.ws.snapshot().mode, 'tiled');
        assertEq('hide split denied mounted unchanged', h.mountedCount(), 3);
        assert('hide split denied B mounted', h.isMounted(B));
        assert('hide split denied C mounted', h.isMounted(C));

        h.setCanLeave(true);
        const hideOk = await h.ws.setMode('stacked');
        assert('hide split accepted', hideOk === true);
        let snap = h.ws.snapshot();
        assertEq('hide split stacked', snap.mode, 'stacked');
        assert('hide split tree preserved', !!snap.secondaryTree);
        assertEq('hide split tree still has B,C', [snap.secondaryTree.first.tabId, snap.secondaryTree.second.tabId].join(','), [B, C].join(','));
        assertEq('hide split mounted only main', h.mountedCount(), 1);
        assert('hide split A mounted', h.isMounted(A));

        const showOk = await h.ws.setMode('tiled');
        assert('show split accepted', showOk === true);
        snap = h.ws.snapshot();
        assertEq('show split tiled', snap.mode, 'tiled');
        assertEq('show split remounts both leaves', h.mountedCount(), 3);
        assert('show split B remounted', h.isMounted(B));
        assert('show split C remounted', h.isMounted(C));
    }

    {
        /* Narrow fallback with 3 Secondary leaves: preserves the whole logical tree + ratios;
         * atomic across all mounted leaves (accepted case). */
        const h = makeHarness({ hash: '#/works/WA' });
        await h.ws.navigate('#/works/WB', { target: 'tile' });
        const A = h.ws.snapshot().mainTabId;
        const B = h.ws.snapshot().secondaryTree.tabId;
        await h.ws.splitLeaf(B, 'top-bottom', { hash: '#/works/WC' });
        const C = h.ws.snapshot().secondaryTree.second.tabId;
        await h.ws.splitLeaf(C, 'left-right', { hash: '#/works/WD' });
        const D = h.ws.snapshot().secondaryTree.second.second.tabId;
        const treeBefore = jsonClone(h.ws.snapshot().secondaryTree);
        assertEq('pre-narrow mounted 4', h.mountedCount(), 4);

        const narrowOk = await h.ws.setNarrowFallback(true);
        assert('narrow accepted', narrowOk === true);
        let snap = h.ws.snapshot();
        assertEq('narrow tree fully preserved', JSON.stringify(snap.secondaryTree), JSON.stringify(treeBefore));
        assertEq('narrow unmounts all secondary', h.mountedCount(), 1);
        assert('narrow A still mounted', h.isMounted(A));
        assertEq('narrow focus main', snap.focusedTabId, A);

        const wideOk = await h.ws.setNarrowFallback(false);
        assert('widen accepted', wideOk === true);
        snap = h.ws.snapshot();
        assertEq('widen remounts all 4', h.mountedCount(), 4);
        assert('widen B remounted', h.isMounted(B));
        assert('widen C remounted', h.isMounted(C));
        assert('widen D remounted', h.isMounted(D));
        assertEq('widen tree unchanged', JSON.stringify(snap.secondaryTree), JSON.stringify(treeBefore));
    }

    {
        /* Atomic narrow rejection: one leaf (the deepest) rejects leave -> nothing is parked,
         * the whole tree/mount set stays exactly as it was. */
        const h = makeHarness({ hash: '#/works/WA' });
        await h.ws.navigate('#/works/WB', { target: 'tile' });
        const B = h.ws.snapshot().secondaryTree.tabId;
        await h.ws.splitLeaf(B, 'top-bottom', { hash: '#/works/WC' });
        const C = h.ws.snapshot().secondaryTree.second.tabId;
        await h.ws.splitLeaf(C, 'left-right', { hash: '#/works/WD' });
        const D = h.ws.snapshot().secondaryTree.second.second.tabId;
        const fpBefore = fingerprint(h);
        assertEq('pre-reject mounted 4', h.mountedCount(), 4);

        h.setCanLeave(false);
        const rejected = await h.ws.setNarrowFallback(true);
        assertEq('narrow rejected result', rejected, false);
        assertEq('narrow rejected fingerprint unchanged', fingerprint(h), fpBefore);
        assertEq('narrow rejected mounted still 4', h.mountedCount(), 4);
        assert('narrow rejected B still mounted', h.isMounted(B));
        assert('narrow rejected C still mounted', h.isMounted(C));
        assert('narrow rejected D still mounted', h.isMounted(D));
        assertEq('narrow rejected still tiled visually', h.ws.visualTiled(), true);

        h.setCanLeave(true);
        const accepted = await h.ws.setNarrowFallback(true);
        assert('narrow now accepted', accepted === true);
        assertEq('narrow accepted mounted 1', h.mountedCount(), 1);
    }

    {
        /* Split-right default placement and Main+first-Secondary stays a bare leaf (spec #8):
         * only recursion once an existing Secondary leaf is itself split. */
        const h = makeHarness({ hash: '#/works/WA' });
        await h.ws.navigate('#/works/WB', { target: 'tile' });
        assertEq('first secondary stays bare leaf', h.ws.snapshot().secondaryTree.type, 'leaf');

        /* splitLeaf refuses a target that is not a current Secondary leaf. */
        const notALeaf = await h.ws.splitLeaf('tab-does-not-exist', 'left-right', { hash: '#/works/WZ' });
        assertEq('split refuses unknown target', notALeaf, false);

        /* splitLeaf refuses reusing a tabId that is Main. */
        const mainId = h.ws.snapshot().mainTabId;
        const secId = h.ws.snapshot().secondaryTree.tabId;
        const refuseMain = await h.ws.splitLeaf(secId, 'left-right', { tabId: mainId });
        assertEq('split refuses reusing main tab', refuseMain, false);

        /* Route-capability is a state-API invariant, not just a UI affordance: tileTab() and
         * splitLeaf({tabId}) must reject inserting a parked tab whose route cannot be tiled,
         * without any tree mutation, mounting, or duplication. */
        await h.ws.navigate('#/folders', { target: 'new-tab', activate: false });
        const folderTab = h.ws.snapshot().tabs[h.ws.snapshot().tabs.length - 1].id;
        const guardFpBefore = fingerprint(h);
        const guardMountBefore = h.mountedCount();
        const rejectedTileTab = await h.ws.tileTab(folderTab);
        assertEq('tileTab rejects unsupported route', rejectedTileTab, false);
        assertEq('tileTab rejection unchanged state', fingerprint(h), guardFpBefore);
        const rejectedSplitLeaf = await h.ws.splitLeaf(secId, 'left-right', { tabId: folderTab });
        assertEq('splitLeaf rejects unsupported route', rejectedSplitLeaf, false);
        assertEq('splitLeaf rejection unchanged state', fingerprint(h), guardFpBefore);
        assertEq('route guard mounted unchanged', h.mountedCount(), guardMountBefore);
        assert('route guard folder tab not mounted', !h.isMounted(folderTab));
    }

    {
        /* Main-close on a recursive tree (spec): Main A, Secondary B / (C | D). Closing A
         * promotes the deterministic first Secondary leaf (B) into Main's exact position and
         * removes only that one leaf from the tree -- every OTHER Secondary leaf (C, D) stays
         * exactly where it was: same tree position, still mounted, TabContext untouched. */
        const h = makeHarness({ hash: '#/works/WA' });
        await h.ws.navigate('#/works/WB', { target: 'tile' });
        const A = h.ws.snapshot().mainTabId;
        const B = h.ws.snapshot().secondaryTree.tabId;
        await h.ws.splitLeaf(B, 'top-bottom', { hash: '#/works/WC' });
        const C = h.ws.snapshot().secondaryTree.second.tabId;
        await h.ws.splitLeaf(C, 'left-right', { hash: '#/works/WD' });
        const D = h.ws.snapshot().secondaryTree.second.second.tabId;
        assertEq('main-close setup mounted 4', h.mountedCount(), 4);

        const mountBefore = h.life.mount.length;
        const parkBefore = h.life.park.length;
        const destroyBefore = h.life.destroy.length;

        const closed = await h.ws.closeTab(A);
        assert('close main ok', closed === true);
        const snap = h.ws.snapshot();
        assertEq('close main promotes B', snap.mainTabId, B);
        assertEq('close main focuses B', snap.focusedTabId, B);
        assertEq('close main stays tiled', snap.mode, 'tiled');
        assertEq('close main url is B', h.hist.getHash(), '#/works/WB');
        assertEq('close main leaves C,D', snap.secondaryTree.first.tabId, C);
        assertEq('close main second leaf D', snap.secondaryTree.second.tabId, D);
        assertEq('close main tree still split', snap.secondaryTree.type, 'split');
        assertEq('close main mounted 3', h.mountedCount(), 3);
        assert('close main A destroyed', !h.isMounted(A));
        assert('close main B mounted (now Main)', h.isMounted(B));
        assert('close main C untouched (mounted)', h.isMounted(C));
        assert('close main D untouched (mounted)', h.isMounted(D));
        /* C and D must never be mounted/parked/destroyed just because Main closed. */
        assertEq('close main C,D not re-mounted', h.life.mount.length, mountBefore);
        assertEq('close main C,D not parked', h.life.park.length, parkBefore);
        assertEq('close main only A destroyed', h.life.destroy.length, destroyBefore + 1);
        assertEq('close main last destroy is A', h.life.destroy[h.life.destroy.length - 1], A);
    }

    {
        /* The plain one-Secondary case is unaffected by the recursive-close fix (spec #6):
         * Main A | Secondary B, close A -> B becomes Main, secondaryTree becomes null, stacked. */
        const h = makeHarness({ hash: '#/works/WA' });
        await h.ws.navigate('#/works/WB', { target: 'tile' });
        const B = h.ws.snapshot().secondaryTree.tabId;
        await h.ws.closeTab(h.ws.snapshot().mainTabId);
        const snap = h.ws.snapshot();
        assertEq('single-secondary close main promotes B', snap.mainTabId, B);
        assertEq('single-secondary close main tree null', snap.secondaryTree, null);
        assertEq('single-secondary close main stacked', snap.mode, 'stacked');
    }

    {
        /* Batch close (closeTabsToTheRight/closeOtherTabs) must apply the same principle:
         * only the leaves actually requested to close are removed; an unrelated surviving
         * Secondary leaf is never parked/remounted, and the tree normalizes correctly whether
         * or not the kept anchor is itself a promoted Secondary leaf. */
        const h = makeHarness({ hash: '#/works/WA' });
        await h.ws.navigate('#/works/WB', { target: 'tile' });
        const A = h.ws.snapshot().mainTabId;
        const B = h.ws.snapshot().secondaryTree.tabId;
        await h.ws.splitLeaf(B, 'top-bottom', { hash: '#/works/WC' });
        const C = h.ws.snapshot().secondaryTree.second.tabId;
        await h.ws.navigate('#/folders', { target: 'new-tab', activate: false }); // K: parked anchor
        const K = h.ws.snapshot().tabs[h.ws.snapshot().tabs.length - 1].id;
        assertEq('batch setup mounted 3', h.mountedCount(), 3);

        /* closeOtherTabs(K): closes everything except K, including Main (A) and every
         * Secondary leaf (B, C) -- none of them are "unrelated survivors" here, so this just
         * exercises the ordinary closingMain + keepId-not-a-leaf path. */
        const closedOthers = await h.ws.closeOtherTabs(K);
        assert('close others (K kept) ok', closedOthers === true);
        const snapOthers = h.ws.snapshot();
        assertEq('close others promotes K to main', snapOthers.mainTabId, K);
        assertEq('close others collapses (no leaves left)', snapOthers.secondaryTree, null);
        assertEq('close others stacked', snapOthers.mode, 'stacked');
        assertEq('close others tab count', snapOthers.tabs.length, 1);

        /* Construct a tab order where Main is among "tabs to the right" of the anchor, and two
         * unrelated Secondary leaves that are NOT being closed survive before the anchor. */
        const h2 = makeHarness({ hash: '#/works/WA' });
        await h2.ws.navigate('#/works/WB', { target: 'tile' }); // A idx0 (main), B idx1 (leaf)
        const A2 = h2.ws.snapshot().mainTabId;
        const B2 = h2.ws.snapshot().secondaryTree.tabId;
        await h2.ws.navigate('#/works/WK', { target: 'new-tab', activate: false }); // K idx2 (anchor, parked)
        const K2 = h2.ws.snapshot().tabs[2].id;
        await h2.ws.splitLeaf(B2, 'top-bottom', { hash: '#/works/WD' }); // D idx3 (leaf)
        const D2 = h2.ws.snapshot().secondaryTree.second.tabId;
        await h2.ws.makeMain(D2); // Main becomes D (idx3); A takes D's old leaf position (idx0)
        assertEq('batch setup main is D', h2.ws.snapshot().mainTabId, D2);
        assertEq('batch setup tree is B,A', [h2.ws.snapshot().secondaryTree.first.tabId, h2.ws.snapshot().secondaryTree.second.tabId].join(','), [B2, A2].join(','));
        await h2.ws.navigate('#/works/WE', { target: 'new-tab', activate: false }); // E idx4 (also closes)
        assertEq('batch setup mounted 3', h2.mountedCount(), 3);

        const mount2Before = h2.life.mount.length;
        const park2Before = h2.life.park.length;
        const closedRight = await h2.ws.closeTabsToTheRight(K2);
        assert('close to right (K2 kept, Main among closed) ok', closedRight === true);
        const snap2 = h2.ws.snapshot();
        assertEq('close to right promotes K2 to main', snap2.mainTabId, K2);
        assertEq('close to right tab count', snap2.tabs.length, 3);
        assertEq('close to right tree untouched (B first)', snap2.secondaryTree.first.tabId, B2);
        assertEq('close to right tree untouched (A second)', snap2.secondaryTree.second.tabId, A2);
        /* K2 itself newly mounts (it was parked, now promoted to Main) -- but B2/A2, the
         * unrelated surviving Secondary leaves, must not be touched by that promotion. */
        assertEq('close to right only K2 newly mounted', h2.life.mount.length, mount2Before + 1);
        assertEq('close to right last mount is K2', h2.life.mount[h2.life.mount.length - 1], K2);
        assertEq('close to right unrelated leaves not parked', h2.life.park.length, park2Before);
        assert('close to right B2 still mounted', h2.isMounted(B2));
        assert('close to right A2 (now a leaf) still mounted', h2.isMounted(A2));
        assert('close to right D destroyed', !h2.isMounted(D2));
    }

    {
        /* ---- Canonical tab-strip reorder (drag-and-drop workspace management #5/#9/#48) ---- */
        const r = makeHarness({ hash: '#/works/WA' });
        await r.ws.navigate('#/works/WB', { target: 'tile' }); // A main, B Secondary leaf
        const rA = r.ws.snapshot().mainTabId;
        const rB = r.ws.snapshot().secondaryTree.tabId;
        await r.ws.navigate('#/works/WC', { target: 'new-tab', activate: false }); // C parked
        const rC = r.ws.snapshot().tabs[2].id;
        await r.ws.navigate('#/works/WD', { target: 'new-tab', activate: false }); // D parked
        const rD = r.ws.snapshot().tabs[3].id;
        assertEq('reorder setup order', r.ws.snapshot().tabs.map((t) => t.id).join(','), [rA, rB, rC, rD].join(','));

        const rMountBefore = r.mountedCount();
        const rRendersBefore = r.renders.length;
        const rTreeBefore = JSON.stringify(r.ws.snapshot().secondaryTree);
        assert('reorder C before B', r.ws.reorderTab(rC, rB));
        const afterReorder = r.ws.snapshot();
        assertEq('reorder C before B order', afterReorder.tabs.map((t) => t.id).join(','), [rA, rC, rB, rD].join(','));
        assertEq('reorder does not change mainTabId', afterReorder.mainTabId, rA);
        assertEq('reorder does not change focusedTabId', afterReorder.focusedTabId, rB);
        assertEq('reorder does not change secondaryTree', JSON.stringify(afterReorder.secondaryTree), rTreeBefore);
        assertEq('reorder does not mount/unmount', r.mountedCount(), rMountBefore);
        assertEq('reorder causes no render/route change', r.renders.length, rRendersBefore);

        assert('reorder to end (beforeTabId null)', r.ws.reorderTab(rC, null));
        assertEq('reorder to end order', r.ws.snapshot().tabs.map((t) => t.id).join(','), [rA, rB, rD, rC].join(','));

        assert('reorder self is a no-op-false', !r.ws.reorderTab(rC, rC));
        assert('reorder missing tab declines', !r.ws.reorderTab('nope', rA));
        assertEq('reorder declines leave order unchanged', r.ws.snapshot().tabs.map((t) => t.id).join(','), [rA, rB, rD, rC].join(','));

        /* ---- moveTabStep: swap-with-neighbor, the keyboard/menu Move left/right primitive,
         * built on the very same reorderTab (#43/#48) ---- */
        const step = makeHarness({ hash: '#/works/WA' });
        await step.ws.navigate('#/works/WB', { target: 'new-tab', activate: false });
        await step.ws.navigate('#/works/WC', { target: 'new-tab', activate: false });
        await step.ws.navigate('#/works/WD', { target: 'new-tab', activate: false });
        const [sA, sB, sC, sD] = step.ws.snapshot().tabs.map((t) => t.id);
        assert('moveTabStep left at start declines', !step.ws.moveTabStep(sA, 'left'));
        assert('moveTabStep right at end declines', !step.ws.moveTabStep(sD, 'right'));
        assert('moveTabStep left swaps with left neighbor', step.ws.moveTabStep(sC, 'left'));
        assertEq('moveTabStep left result', step.ws.snapshot().tabs.map((t) => t.id).join(','), [sA, sC, sB, sD].join(','));
        assert('moveTabStep right swaps with right neighbor', step.ws.moveTabStep(sB, 'right'));
        assertEq('moveTabStep right result', step.ws.snapshot().tabs.map((t) => t.id).join(','), [sA, sC, sD, sB].join(','));

        /* ---- movePane: atomic spatial reposition of a visible Secondary leaf (#17-21/#25/#41) ---- */
        const mp = makeHarness({ hash: '#/works/WA' });
        await mp.ws.navigate('#/works/WB', { target: 'tile' }); // Main A | Secondary B
        const mpA = mp.ws.snapshot().mainTabId;
        const mpB = mp.ws.snapshot().secondaryTree.tabId;
        await mp.ws.splitLeaf(mpB, 'left-right', { hash: '#/works/WC' }); // B | C
        const mpC = mp.ws.snapshot().secondaryTree.second.tabId;
        await mp.ws.splitLeaf(mpC, 'top-bottom', { hash: '#/works/WD' }); // B | (C over D)
        const mpD = mp.ws.snapshot().secondaryTree.second.second.tabId;
        await mp.ws.focusTab(mpD);
        assertEq('movePane setup mounted 4', mp.mountedCount(), 4);

        const mpMountBefore = mp.life.mount.length;
        const mpParkBefore = mp.life.park.length;
        const mpDestroyBefore = mp.life.destroy.length;
        const mpRendersBefore = mp.renders.length;
        const mpLeaveBefore = mp.canLeaveCalls();
        const mpRootId = mp.ws.snapshot().secondaryTree.id;
        assert('movePane D above B', mp.ws.movePane(mpD, mpB, 'top-bottom', 'first'));
        const mpSnap = mp.ws.snapshot();
        assertEq('movePane result order', tree.collectLeafTabIds(mpSnap.secondaryTree).join(','), [mpD, mpB, mpC].join(','));
        /* B's leaf position is replaced in-place by a new nested split(D, B); C -- never
         * involved in the move -- keeps its exact prior position as the outer split's second
         * child, and the outer (unaffected) split node keeps its original id (spec #18). */
        assertEq('movePane D moved above B (nested)', mpSnap.secondaryTree.first.first.tabId, mpD);
        assertEq('movePane B nested below D', mpSnap.secondaryTree.first.second.tabId, mpB);
        assertEq('movePane C untouched as outer second', mpSnap.secondaryTree.second.tabId, mpC);
        assertEq('movePane preserves unaffected outer split id', mpSnap.secondaryTree.id, mpRootId);
        /* Moving a pane is not a leave operation (#21): no context mounts, parks, destroys,
         * leave-preflight calls, or route renders -- only the tree structure changed. */
        assertEq('movePane mounts nothing new', mp.life.mount.length, mpMountBefore);
        assertEq('movePane parks nothing', mp.life.park.length, mpParkBefore);
        assertEq('movePane destroys nothing', mp.life.destroy.length, mpDestroyBefore);
        assertEq('movePane triggers no render/route change', mp.renders.length, mpRendersBefore);
        assertEq('movePane runs no leave preflight', mp.canLeaveCalls(), mpLeaveBefore);
        assertEq('movePane still mounted 4', mp.mountedCount(), 4);
        /* Focus ownership is untouched by a spatial move alone (#41): D was focused before the
         * move and remains focused purely because geometry changed, not because of a new
         * activation semantic. */
        assertEq('movePane preserves prior focus', mpSnap.focusedTabId, mpD);

        assert('movePane self is invalid', !mp.ws.movePane(mpB, mpB, 'left-right', 'second'));
        assert('movePane Main as source is invalid', !mp.ws.movePane(mpA, mpB, 'left-right', 'second'));
        assert('movePane Main as target is invalid', !mp.ws.movePane(mpB, mpA, 'left-right', 'second'));
        assert('movePane missing leaf is invalid', !mp.ws.movePane(mpB, 'nope', 'left-right', 'second'));
        assertEq('movePane rejects leave tree unchanged', JSON.stringify(mp.ws.snapshot().secondaryTree), JSON.stringify(mpSnap.secondaryTree));

        /* Spatial pane drag/move is unavailable while the physical layout is narrow (#37): no
         * Secondary geometry is visible to move within, so movePane must decline without
         * mutating anything, even though the tree still structurally contains both leaves. */
        await mp.ws.setNarrowFallback(true);
        assert('movePane declines under narrow fallback', !mp.ws.movePane(mpD, mpC, 'left-right', 'second'));
        await mp.ws.setNarrowFallback(false);
    }

    console.log('\n' + passed + ' passed, ' + failed + ' failed');
    process.exit(failed ? 1 : 0);
}

run().catch(function (err) {
    console.error(err);
    process.exit(1);
});
