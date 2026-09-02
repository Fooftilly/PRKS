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
        entries: entries,
    };
}

function makeHarness(opts) {
    opts = opts || {};
    const hist = makeHistory(opts.hash || '#/folders');
    const renders = [];
    let canLeave = true;
    let titleGenOk = null;
    const ws = createPrksWorkspaceTabs({
        parseRoute: nav.prksParseRoute,
        routeLoadingTitle: nav.prksRouteLoadingTitle,
        routeTabIcon: nav.prksRouteTabIcon,
        homeHash: '#/folders',
        historyAdapter: hist,
        canLeave: function () {
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
    });
    ws.bootstrap(opts.hash || '#/folders');
    return {
        ws: ws,
        hist: hist,
        renders: renders,
        setCanLeave: function (v) {
            canLeave = v;
        },
        setTitleGen: function (g) {
            titleGenOk = g;
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
    assertEq('intent alt ignore', prksWorkspaceNavigationIntent({ button: 0, altKey: true }), 'ignore');

    const boot = makeHarness({ hash: '#/folders' });
    const snap0 = boot.ws.snapshot();
    assertEq('bootstrap tab count', snap0.tabs.length, 1);
    assert('mainTabId exists', !!snap0.mainTabId);
    assertEq('focused == main', snap0.focusedTabId, snap0.mainTabId);
    assertEq('mode stacked', snap0.mode, 'stacked');
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
    assert('no secondaryTree', src.indexOf('secondaryTree') === -1);
    assert('no splitRatio', src.indexOf('splitRatio') === -1);

    console.log('\n' + passed + ' passed, ' + failed + ' failed');
    process.exit(failed ? 1 : 0);
}

run().catch(function (err) {
    console.error(err);
    process.exit(1);
});
