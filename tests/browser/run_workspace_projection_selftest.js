#!/usr/bin/env node
'use strict';

/* Publication contract for the workspace coordinator. Usable without Vue.
 * One canonical state, detached frozen snapshots, multiple subscribers, and a
 * rejected leave that does not publish a new presentation.
 */

const path = require('path');

const rootDir = path.resolve(__dirname, '../..');
const nav = require(path.join(rootDir, 'frontend/js/navigation.js'));
require(path.join(rootDir, 'frontend/js/workspace-model.js'));
require(path.join(rootDir, 'frontend/js/workspace-tree.js'));
const wsApi = require(path.join(rootDir, 'frontend/js/workspace-tabs.js'));

const { createPrksWorkspaceTabs } = wsApi;

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

function makeHistory(initialHash) {
    let hash = initialHash;
    return {
        getHash: function () {
            return hash;
        },
        getHref: function () {
            return 'http://127.0.0.1/' + hash;
        },
        getState: function () {
            return null;
        },
        pushState: function (_state, url) {
            const i = String(url).indexOf('#');
            hash = i >= 0 ? String(url).slice(i) : hash;
        },
        replaceState: function (_state, url) {
            const i = String(url).indexOf('#');
            hash = i >= 0 ? String(url).slice(i) : hash;
        },
    };
}

function makeWorkspace() {
    let allowLeave = true;
    const ws = createPrksWorkspaceTabs({
        parseRoute: nav.prksParseRoute,
        routeLoadingTitle: nav.prksRouteLoadingTitle,
        routeTabIcon: nav.prksRouteTabIcon,
        homeHash: '#/folders',
        historyAdapter: makeHistory('#/folders'),
        supportsTile: nav.prksRouteSupportsTile,
        canLeave: function () {
            return allowLeave;
        },
        renderRoute: function () {},
        announce: function () {},
        publishMainShell: function () {},
        loadSnapshot: function () {
            return null;
        },
    });
    ws.bootstrap('#/folders');
    return {
        ws: ws,
        setAllowLeave: function (value) {
            allowLeave = !!value;
        },
    };
}

(async function () {
    const h = makeWorkspace();
    const first = [];
    const second = [];
    const unsubFirst = h.ws.subscribe(function (projection) {
        first.push(projection);
    });
    const unsubSecond = h.ws.subscribe(function (projection) {
        second.push(projection);
    });
    assert('subscribe delivers the current projection', first.length === 1 && second.length === 1);
    assert('immediate snapshots are detached copies', first[0] !== second[0]);
    assert('snapshot state is frozen', Object.isFrozen(first[0]) && Object.isFrozen(first[0].state));
    let threw = false;
    try {
        first[0].state.mainTabId = 'tab-other';
    } catch (_e) {
        threw = true;
    }
    assert('mutating a published snapshot throws', threw);
    assert('canonical main is unchanged', h.ws.snapshot().mainTabId === first[0].state.mainTabId);

    const renamed = h.ws.setResolvedTitle('#/folders', 'Library');
    assert('title commit publishes', renamed === true);
    assert('both subscribers see the same publish', first[1] && first[1] === second[1]);
    assert('published title is the committed one', first[1].state.tabs[0].title === 'Library');
    assert('canonical snapshot is a different object', h.ws.snapshot() !== first[1].state);

    unsubFirst();
    const before = first.length;
    h.ws.setResolvedTitle('#/folders', 'Library again');
    assert('unsubscribed listener is not called', first.length === before);
    assert('remaining subscriber still receives the publish', second.length === first.length + 1);
    unsubSecond();

    const parked = await h.ws.openTab('#/people/1', { activate: false });
    const mainBefore = h.ws.snapshot().mainTabId;
    let afterReject = null;
    h.ws.subscribe(function (projection) {
        afterReject = projection;
    });
    h.setAllowLeave(false);
    const activated = await h.ws.activateTab(parked.id);
    assert('rejected leave returns false', activated === false);
    assert('rejected leave keeps Main', h.ws.snapshot().mainTabId === mainBefore);
    assert('rejected leave does not replace the published main', afterReject.state.mainTabId === mainBefore);

    if (failed) {
        console.log(failed + ' failed, ' + passed + ' passed');
        process.exit(1);
    }
    console.log('All ' + passed + ' workspace projection checks passed, 0 failed');
})().catch(function (err) {
    console.error(err);
    process.exit(1);
});
