#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const rootDir = path.resolve(__dirname, '../..');
const tc = require(path.join(rootDir, 'frontend/js/tab-context.js'));
const worksSrc = fs.readFileSync(path.join(rootDir, 'frontend/js/components/works.js'), 'utf8');

const {
    prksEnsureTabContext,
    prksDestroyAllTabContexts,
} = tc;

let passed = 0;
let failed = 0;
let bannedQuery = 0;

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

function makeWorkspace(id) {
    const styleVars = {};
    const handle = {
        setAttribute: function () {},
        addEventListener: function () {},
        classList: { add: function () {}, remove: function () {} },
        querySelector: function () {
            return null;
        },
    };
    const ws = {
        workId: id,
        classList: {
            contains: function () {
                return false;
            },
            add: function () {},
            remove: function () {},
            toggle: function () {},
        },
        getAttribute: function (k) {
            return k === 'data-work-id' ? id : null;
        },
        querySelector: function (sel) {
            if (sel === '.work-split-handle') return handle;
            if (sel === '.work-notes-pane') return { getBoundingClientRect: function () { return { width: 280, height: 320, top: 0, bottom: 320 }; } };
            return null;
        },
        style: {
            setProperty: function (k, v) {
                styleVars[k] = v;
            },
            getPropertyValue: function (k) {
                return styleVars[k] || '';
            },
        },
        getBoundingClientRect: function () {
            return { width: 800, height: 600, top: 0, bottom: 600 };
        },
        _vars: styleVars,
    };
    return ws;
}

function makeRoot(ws) {
    return {
        querySelector: function (sel) {
            if (String(sel).indexOf('.work-workspace') !== -1) return ws;
            return null;
        },
        querySelectorAll: function () {
            return [];
        },
    };
}

prksDestroyAllTabContexts();
const ctxA = prksEnsureTabContext('notes-a');
const ctxB = prksEnsureTabContext('notes-b');
const wsA = makeWorkspace('WA');
const wsB = makeWorkspace('WB');
ctxA.root = makeRoot(wsA);
ctxB.root = makeRoot(wsB);
ctxA.mounted = true;
ctxB.mounted = true;
ctxA.query = function (sel) {
    return ctxA.root.querySelector(sel);
};
ctxB.query = function (sel) {
    return ctxB.root.querySelector(sel);
};

let refreshA = 0;
let refreshB = 0;
ctxA.setResource('workNotes', {
    codemirror: {
        refresh: function () {
            refreshA += 1;
        },
    },
});
ctxB.setResource('workNotes', {
    codemirror: {
        refresh: function () {
            refreshB += 1;
        },
    },
});

const sandbox = {
    window: {},
    document: {
        documentElement: { classList: { contains: function () { return false; }, toggle: function () {} } },
        getElementById: function () {
            return null;
        },
        querySelector: function () {
            bannedQuery += 1;
            throw new Error('document.querySelector must not be used for Work notes layout');
        },
        querySelectorAll: function () {
            return [];
        },
        addEventListener: function () {},
        createElement: function () {
            return { style: {}, setAttribute: function () {}, classList: { add: function () {}, remove: function () {}, contains: function () { return false; } } };
        },
    },
    localStorage: { getItem: function () { return null; }, setItem: function () {} },
    requestAnimationFrame: function (fn) {
        fn();
    },
    console: console,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    ResizeObserver: function () {
        this.observe = function () {};
        this.disconnect = function () {};
    },
    prksGetFocusedTabContext: function () {
        throw new Error('must not depend on focusedTabId');
    },
    prksFocusedResource: function () {
        throw new Error('must not use focused resource fallback');
    },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.runInNewContext(worksSrc + '\nthis.prksReapplyWorkNotesSplitLayout = prksReapplyWorkNotesSplitLayout;', sandbox);

sandbox.prksReapplyWorkNotesSplitLayout(ctxA);
assert('A workspace height set', !!wsA._vars['--work-notes-height']);
assert('B workspace untouched', !wsB._vars['--work-notes-height']);
assertEq('A editor refreshed', refreshA >= 1, true);
assertEq('B editor not refreshed', refreshB, 0);
assertEq('no document.querySelector', bannedQuery, 0);

sandbox.prksReapplyWorkNotesSplitLayout(ctxB);
assert('B workspace height set', !!wsB._vars['--work-notes-height']);
assertEq('still no document.querySelector', bannedQuery, 0);

function tick() {
    return new Promise(function (resolve) {
        setTimeout(resolve, 0);
    });
}

async function runSaveGenerationRace() {
    const pending = [];
    sandbox.prksRequest = function () {
        return new Promise(function (resolve) {
            pending.push(resolve);
        });
    };
    sandbox.fetchWorkDetails = function () {
        return Promise.resolve(null);
    };
    sandbox.prksWorkspaceRefreshTabStatus = function () {};
    sandbox.window.prksWorkspaceRefreshTabStatus = sandbox.prksWorkspaceRefreshTabStatus;

    const ctxS = prksEnsureTabContext('notes-save');
    ctxS.mounted = true;
    ctxS.tabId = 'notes-save';
    const statusEl = { innerText: '' };
    ctxS.query = function () {
        return statusEl;
    };
    const notes = {
        editor: {
            value: function () {
                return 'note';
            },
        },
        editGeneration: 0,
        saveSequence: 0,
        latestSaveToken: 0,
        latestSaveEditGeneration: 0,
        settledSaveToken: 0,
        drafting: false,
        saveError: false,
        pendingSave: false,
    };
    ctxS.setResource('workNotes', notes);

    sandbox.prksWorkNotesMarkEdit(notes);
    sandbox.prksEnqueueWorkResearchNotesSave(ctxS, 'W-save');
    assertEq('A save token', notes.latestSaveToken, 1);
    assertEq('A pending after enqueue', notes.latestSaveToken > notes.settledSaveToken, true);
    assertEq('one delayed mutation', pending.length, 1);

    sandbox.prksWorkNotesMarkEdit(notes);
    assertEq('edit while A in flight drafts', notes.drafting, true);
    sandbox.prksEnqueueWorkResearchNotesSave(ctxS, 'W-save');
    assertEq('B save token newest', notes.latestSaveToken, 2);
    assertEq('two delayed mutations', pending.length, 2);

    pending[0]({ ok: true });
    await tick();
    assertEq('stale A does not settle newest', notes.settledSaveToken, 0);
    assertEq('stale A leaves pending', notes.latestSaveToken > notes.settledSaveToken, true);
    assert('status after stale A still busy', !!(notes.drafting || notes.latestSaveToken > notes.settledSaveToken));
    assertEq('stale A does not mark saved', statusEl.innerText === 'All changes saved', false);

    pending[1]({ ok: true });
    await tick();
    assertEq('B settles newest', notes.settledSaveToken, 2);
    assertEq('B clears pending', notes.latestSaveToken > notes.settledSaveToken, false);
    assertEq('B clears drafting', notes.drafting, false);
    assertEq('B clears error', notes.saveError, false);
    assertEq('newest success status', statusEl.innerText, 'All changes saved');

    sandbox.prksWorkNotesMarkEdit(notes);
    sandbox.prksEnqueueWorkResearchNotesSave(ctxS, 'W-save');
    sandbox.prksWorkNotesMarkEdit(notes);
    sandbox.prksEnqueueWorkResearchNotesSave(ctxS, 'W-save');
    assertEq('retry queued two more', pending.length, 4);
    pending[2]({ ok: false });
    await tick();
    assertEq('stale failure ignored', notes.saveError, false);
    assertEq('stale failure does not settle B', notes.settledSaveToken, 2);
    pending[3]({ ok: true });
    await tick();
    assertEq('newest retry settled', notes.settledSaveToken, 4);
    assertEq('newest retry not error', notes.saveError, false);

    const dup = {
        editor: {
            value: function () {
                return 'same';
            },
        },
        editGeneration: 1,
        saveSequence: 0,
        latestSaveToken: 0,
        latestSaveEditGeneration: 0,
        settledSaveToken: 0,
        drafting: false,
        saveError: false,
        pendingSave: false,
    };
    ctxS.setResource('workNotes', dup);
    statusEl.innerText = '';
    sandbox.prksEnqueueWorkResearchNotesSave(ctxS, 'W-save');
    const tokenA = dup.latestSaveToken;
    sandbox.prksEnqueueWorkResearchNotesSave(ctxS, 'W-save');
    const tokenB = dup.latestSaveToken;
    assert('same-generation A token != B token', tokenA !== tokenB);
    assertEq('same-generation B is newest', dup.latestSaveToken, tokenB);
    assertEq('same-generation two PATCHes', pending.length, 6);
    pending[4]({ ok: false });
    await tick();
    assertEq('same-generation A does not settle B', dup.settledSaveToken, 0);
    assertEq('same-generation still pending', dup.latestSaveToken > dup.settledSaveToken, true);
    assertEq('same-generation A failure hidden', dup.saveError, false);
    assertEq('same-generation A cannot mark saved', statusEl.innerText === 'All changes saved', false);
    pending[5]({ ok: true });
    await tick();
    assertEq('same-generation B settled', dup.settledSaveToken, tokenB);
    assertEq('same-generation idle pending', dup.latestSaveToken > dup.settledSaveToken, false);
    assertEq('same-generation idle drafting', dup.drafting, false);
    assertEq('same-generation B success status', statusEl.innerText, 'All changes saved');
}

function runDebounceBookkeeping() {
    const fakeTimers = [];
    let nextTid = 1000;
    sandbox.setTimeout = function (fn, ms) {
        const id = ++nextTid;
        fakeTimers.push({ id: id, fn: fn, ms: ms, cleared: false });
        return id;
    };
    sandbox.clearTimeout = function (id) {
        for (let i = 0; i < fakeTimers.length; i++) {
            if (fakeTimers[i].id === id) fakeTimers[i].cleared = true;
        }
    };

    let patches = 0;
    sandbox.prksRequest = function () {
        assertEq('timer key gone at PATCH', ctxD.timers.has('saveNotesTimeout'), false);
        patches += 1;
        return Promise.resolve({ ok: true });
    };

    const ctxD = prksEnsureTabContext('notes-debounce');
    ctxD.mounted = true;
    ctxD.tabId = 'notes-debounce';
    ctxD.setEntity('work', { id: 'W-debounce' });
    const statusEl = { innerText: '' };
    ctxD.query = function () {
        return statusEl;
    };
    const notes = {
        editor: {
            value: function () {
                return 'debounced';
            },
        },
        editGeneration: 0,
        saveSequence: 0,
        latestSaveToken: 0,
        latestSaveEditGeneration: 0,
        settledSaveToken: 0,
        drafting: false,
        saveError: false,
        pendingSave: false,
    };
    ctxD.setResource('workNotes', notes);

    sandbox.prksWorkNotesMarkEdit(notes);
    sandbox.prksScheduleWorkResearchNotesSave(ctxD, 'W-debounce');
    assertEq('debounce timer key exists', ctxD.timers.has('saveNotesTimeout'), true);
    assertEq('debounce delay', fakeTimers.length === 1 && fakeTimers[0].ms, 2000);
    assertEq('no PATCH before fire', patches, 0);

    fakeTimers[0].fn();
    assertEq('timer key removed before enqueue', ctxD.timers.has('saveNotesTimeout'), false);
    assertEq('exactly one PATCH after fire', patches, 1);

    sandbox.prksFlushPendingWorkResearchNotes(ctxD);
    assertEq('flush after fire does not PATCH again', patches, 1);
}

(async function () {
    try {
        await runSaveGenerationRace();
        runDebounceBookkeeping();
    } catch (err) {
        console.error(err);
        process.exit(1);
    }
    prksDestroyAllTabContexts();
    if (failed) {
        console.log(failed + ' failed, ' + passed + ' passed');
        process.exit(1);
    }
    console.log('All ' + passed + ' Work notes layout isolation checks passed');
})();
