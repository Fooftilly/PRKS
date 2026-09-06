#!/usr/bin/env node
'use strict';

/* Regression for warm-PDF status hardening: the workspace tab-strip status pill must stay
 * visible for a warm-suspended tab (it is still live: Research Notes / PDF annotation sync
 * can still be in flight), and must never appear for a cold-parked or destroyed tab. */

const path = require('path');

const rootDir = path.resolve(__dirname, '../..');
const wsApi = require(path.join(rootDir, 'frontend/js/workspace-tabs.js'));

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

function fakeCreateElement() {
    return {
        className: '',
        hidden: false,
        title: '',
        setAttribute: function (k, v) {
            this['attr_' + k] = v;
        },
    };
}

function makeTabWrapRecord(tabId) {
    let statusEl = null;
    const closeEl = { role: 'close' };
    const activateEl = {
        role: 'activate',
        setAttribute: function (k, v) {
            this['attr_' + k] = v;
        },
        removeAttribute: function (k) {
            delete this['attr_' + k];
        },
    };
    const wrap = {
        tabId: tabId,
        querySelector: function (sel) {
            if (sel === ':scope > .prks-workspace-tab__status') return statusEl;
            if (sel === '.prks-workspace-tab__activate') return activateEl;
            if (sel === '.prks-workspace-tab__close') return closeEl;
            return null;
        },
        insertBefore: function (el) {
            statusEl = el;
            return el;
        },
    };
    return {
        wrap: wrap,
        activateEl: activateEl,
        getStatusEl: function () {
            return statusEl;
        },
    };
}

const tabWraps = Object.create(null);
function registerTab(tabId) {
    const rec = makeTabWrapRecord(tabId);
    tabWraps[tabId] = rec;
    return rec;
}

const tabList = {
    clientWidth: 0,
    scrollWidth: 0,
    querySelector: function (sel) {
        const m = String(sel).match(/data-tab-id="([^"]+)"/);
        if (m && tabWraps[m[1]]) return tabWraps[m[1]].wrap;
        return null;
    },
};

global.document = {
    getElementById: function (id) {
        if (id === 'prks-workspace-tabs') return tabList;
        return null;
    },
    createElement: fakeCreateElement,
};

const contexts = Object.create(null);
global.prksGetTabContext = function (tabId) {
    return Object.prototype.hasOwnProperty.call(contexts, tabId) ? contexts[tabId] : null;
};

function makeCtx(opts) {
    const notes = opts.notes || null;
    const pdf = opts.pdf || null;
    return {
        destroyed: !!opts.destroyed,
        mounted: !!opts.mounted,
        suspended: !!opts.suspended,
        getResource: function (name) {
            if (name === 'workNotes') return notes;
            if (name === 'pdf') return pdf;
            return undefined;
        },
    };
}

function savingNotes() {
    return {
        saveError: false,
        latestSaveToken: 2,
        settledSaveToken: 1,
        editGeneration: 0,
        latestSaveEditGeneration: 0,
        drafting: false,
    };
}

function errorNotes() {
    return {
        saveError: true,
        latestSaveToken: 1,
        settledSaveToken: 1,
        editGeneration: 0,
        latestSaveEditGeneration: 0,
        drafting: false,
    };
}

function idleNotes() {
    return {
        saveError: false,
        latestSaveToken: 1,
        settledSaveToken: 1,
        editGeneration: 0,
        latestSaveEditGeneration: 0,
        drafting: false,
    };
}

/* A mounted, B warm-suspended, equivalent pending Research Notes saves; C cold parked. */
registerTab('tab-a');
registerTab('tab-b');
registerTab('tab-c');
registerTab('tab-destroyed');

contexts['tab-a'] = makeCtx({ mounted: true, notes: savingNotes() });
contexts['tab-b'] = makeCtx({ mounted: false, suspended: true, notes: savingNotes() });
contexts['tab-c'] = makeCtx({ mounted: false, suspended: false, notes: savingNotes() });
contexts['tab-destroyed'] = makeCtx({ destroyed: true, notes: savingNotes() });

wsApi.prksWorkspaceRefreshTabStatus('tab-a');
wsApi.prksWorkspaceRefreshTabStatus('tab-b');
wsApi.prksWorkspaceRefreshTabStatus('tab-c');
wsApi.prksWorkspaceRefreshTabStatus('tab-destroyed');

const aStatus = tabWraps['tab-a'].getStatusEl();
const bStatus = tabWraps['tab-b'].getStatusEl();
const cStatus = tabWraps['tab-c'].getStatusEl();
const dStatus = tabWraps['tab-destroyed'].getStatusEl();

assert('mounted tab shows a status pill', !!aStatus);
assert('warm-suspended tab shows a status pill', !!bStatus);
assertEq('cold parked tab has no status pill', cStatus, null);
assertEq('destroyed tab has no status pill', dStatus, null);

if (aStatus) assertEq('mounted tab status is saving', aStatus.className.indexOf('--saving') !== -1, true);
if (bStatus) assertEq('warm-suspended tab status is saving', bStatus.className.indexOf('--saving') !== -1, true);
assertEq(
    'mounted busy tab marks activate aria-busy',
    tabWraps['tab-a'].activateEl['attr_aria-busy'],
    'true'
);
assertEq(
    'warm-suspended busy tab marks activate aria-busy',
    tabWraps['tab-b'].activateEl['attr_aria-busy'],
    'true'
);

/* Error priority: a warm-suspended tab with a save error must still surface as 'error'. */
contexts['tab-b'] = makeCtx({ mounted: false, suspended: true, notes: errorNotes() });
wsApi.prksWorkspaceRefreshTabStatus('tab-b');
const bErrorStatus = tabWraps['tab-b'].getStatusEl();
assert('warm-suspended error tab still shows a status pill', !!bErrorStatus);
if (bErrorStatus) assertEq('warm-suspended tab status is error', bErrorStatus.className.indexOf('--error') !== -1, true);

/* An idle warm-suspended tab (nothing pending) must not show a stale status. */
contexts['tab-a'] = makeCtx({ mounted: true, notes: idleNotes() });
wsApi.prksWorkspaceRefreshTabStatus('tab-a');
const aIdleStatus = tabWraps['tab-a'].getStatusEl();
assertEq('idle mounted tab status hidden', !aIdleStatus || aIdleStatus.hidden, true);

console.log('\n' + passed + ' passed, ' + failed + ' failed');
process.exit(failed ? 1 : 0);
