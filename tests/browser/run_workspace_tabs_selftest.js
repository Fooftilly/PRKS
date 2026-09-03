#!/usr/bin/env node
'use strict';

const path = require('path');

const rootDir = path.resolve(__dirname, '../..');
const nav = require(path.join(rootDir, 'frontend/js/navigation.js'));
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
    assert('no split node type', src.indexOf("type: 'split'") === -1 && src.indexOf('type: "split"') === -1);

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

    const leaveTile = makeHarness({ hash: '#/works/WA' });
    await leaveTile.ws.navigate('#/works/WB', { target: 'tile' });
    const bId = leaveTile.ws.snapshot().secondaryTree.tabId;
    const aId = leaveTile.ws.snapshot().mainTabId;
    const hrefLeaveTile = leaveTile.hist.getHash();
    const rendersLeaveTile = leaveTile.renders.length;
    leaveTile.setCanLeave(false);
    const deniedTile = await leaveTile.ws.navigate('#/works/WC', { target: 'tile' });
    assertEq('replace tile denied', deniedTile, false);
    assertEq('replace tile still B', leaveTile.ws.snapshot().secondaryTree.tabId, bId);
    assertEq('replace tile main A', leaveTile.ws.snapshot().mainTabId, aId);
    assertEq('replace tile url', leaveTile.hist.getHash(), hrefLeaveTile);
    assertEq('replace tile no render', leaveTile.renders.length, rendersLeaveTile);
    assertEq('replace tile leave tab', leaveTile.lastLeaveTabId(), bId);
    leaveTile.setCanLeave(true);
    const allowedTile = await leaveTile.ws.navigate('#/works/WC', { target: 'tile' });
    assert('replace tile allowed', !!allowedTile);
    assert('C is secondary', leaveTile.ws.snapshot().secondaryTree.tabId !== bId);
    assertEq('A still main after replace', leaveTile.ws.snapshot().mainTabId, aId);
    assert('B still present parked', leaveTile.ws.snapshot().tabs.some(function (t) {
        return t.id === bId;
    }));

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
    }

    console.log('\n' + passed + ' passed, ' + failed + ' failed');
    process.exit(failed ? 1 : 0);
}

run().catch(function (err) {
    console.error(err);
    process.exit(1);
});
