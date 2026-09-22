#!/usr/bin/env node
'use strict';

/* Visible narrow-split status: production announce() updates the SR live region and a
 * compact tab-strip status element. Layout reconciliation must not flood that status. */

const path = require('path');

const rootDir = path.resolve(__dirname, '../..');

function makeEl(id) {
    return {
        id: id,
        textContent: '',
        hidden: id === 'prks-workspace-status',
        setAttribute: function () {},
    };
}

const live = makeEl('prks-workspace-live');
const status = makeEl('prks-workspace-status');
const timers = [];

globalThis.document = {
    getElementById: function (id) {
        if (id === 'prks-workspace-live') return live;
        if (id === 'prks-workspace-status') return status;
        return null;
    },
};

const realSetTimeout = setTimeout;
const realClearTimeout = clearTimeout;
globalThis.setTimeout = function (fn, ms) {
    const handle = { fn: fn, ms: ms, cleared: false };
    timers.push(handle);
    return handle;
};
globalThis.clearTimeout = function (handle) {
    if (handle && typeof handle === 'object') handle.cleared = true;
};

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

function runPendingTimers() {
    const pending = timers.filter(function (t) {
        return !t.cleared;
    });
    timers.length = 0;
    pending.forEach(function (t) {
        t.fn();
    });
}

const msg = wsApi.prksWorkspaceNarrowSplitMessageForTest;
assert('exports narrow message', typeof msg === 'string' && msg.indexOf('wider workspace') !== -1);
assert('exports announce test seam', typeof wsApi.prksWorkspaceAnnounceForTest === 'function');
assert('exports status test seam', typeof wsApi.prksWorkspaceShowStatusForTest === 'function');

wsApi.prksWorkspaceAnnounceForTest('', 'narrow');
assertEq('live region text', live.textContent, msg);
assertEq('status text', status.textContent, msg);
assertEq('status visible', status.hidden, false);
assertEq('one dismiss timer', timers.filter(function (t) { return !t.cleared; }).length, 1);

wsApi.prksWorkspaceAnnounceForTest('', 'narrow');
assertEq('dedup keeps one live text', live.textContent, msg);
assertEq('dedup keeps one status text', status.textContent, msg);
assertEq('dedup refreshes to one active timer', timers.filter(function (t) { return !t.cleared; }).length, 1);

wsApi.prksWorkspaceAnnounceForTest('Work A', 'split');
assertEq('split announce updates live only', live.textContent, 'Opened Work A in split view');
assertEq('split clears prior status', status.textContent, '');
assertEq('status hidden after unrelated announce', status.hidden, true);

wsApi.prksWorkspaceAnnounceForTest('', 'narrow');
assertEq('status returns after narrow', status.textContent, msg);
assertEq('status visible again', status.hidden, false);
assertEq('dismiss timer armed', timers.filter(function (t) { return !t.cleared; }).length, 1);

runPendingTimers();
assertEq('status clears after timer', status.textContent, '');
assertEq('status hidden after timer', status.hidden, true);

wsApi.prksWorkspaceAnnounceForTest('', 'cap');
assertEq('cap is SR-only live text', live.textContent.indexOf('Maximum of 4') !== -1, true);
assertEq('cap leaves status empty', status.textContent, '');
assertEq('cap leaves status hidden', status.hidden, true);

wsApi.prksWorkspaceShowStatusForTest('');
assertEq('explicit clear hides status', status.hidden, true);

globalThis.setTimeout = realSetTimeout;
globalThis.clearTimeout = realClearTimeout;

console.log('Result: ' + passed + ' passed, ' + failed + ' failed');
process.exit(failed ? 1 : 0);
