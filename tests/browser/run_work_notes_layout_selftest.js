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
    prksForEachLiveTabContext,
    prksForEachMountedTabContext,
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
    const handleListeners = Object.create(null);
    const handleClasses = new Set();
    let captured = null;
    const handle = {
        offsetHeight: 11,
        offsetWidth: 16,
        setAttribute: function () {},
        addEventListener: function (type, fn) { (handleListeners[type] || (handleListeners[type] = [])).push(fn); },
        removeEventListener: function (type, fn) {
            handleListeners[type] = (handleListeners[type] || []).filter(function (x) { return x !== fn; });
        },
        dispatch: function (type, ev) { (handleListeners[type] || []).slice().forEach(function (fn) { fn(ev || {}); }); },
        setPointerCapture: function (id) { captured = id; },
        hasPointerCapture: function (id) { return captured === id; },
        releasePointerCapture: function (id) { if (captured === id) captured = null; },
        classList: {
            add: function (c) { handleClasses.add(c); },
            remove: function (c) { handleClasses.delete(c); },
            contains: function (c) { return handleClasses.has(c); },
        },
        querySelector: function () {
            return null;
        },
    };
    const ws = {
        workId: id,
        clientWidth: 800,
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
        closest: function () { return ws; },
        _vars: styleVars,
        _handle: handle,
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

const documentListeners = Object.create(null);
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
        addEventListener: function (type, fn) { (documentListeners[type] || (documentListeners[type] = [])).push(fn); },
        removeEventListener: function (type, fn) {
            documentListeners[type] = (documentListeners[type] || []).filter(function (x) { return x !== fn; });
        },
        dispatch: function (type, ev) { (documentListeners[type] || []).slice().forEach(function (fn) { fn(ev || {}); }); },
        createElement: function () {
            return { style: {}, setAttribute: function () {}, classList: { add: function () {}, remove: function () {}, contains: function () { return false; } } };
        },
    },
    localStorage: { getItem: function () { return null; }, setItem: function () {} },
    requestAnimationFrame: function (fn) {
        fn();
    },
    addEventListener: function () {},
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
    prksForEachLiveTabContext: prksForEachLiveTabContext,
    prksForEachMountedTabContext: prksForEachMountedTabContext,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.runInNewContext(
    worksSrc +
        '\nthis.prksReapplyWorkNotesSplitLayout = prksReapplyWorkNotesSplitLayout;' +
        '\nthis.setupWorkNotesSplitResize = setupWorkNotesSplitResize;',
    sandbox
);

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
    assertEq('same-generation duplicate reuses token', tokenA, tokenB);
    assertEq('same-generation token stays newest', dup.latestSaveToken, tokenB);
    assertEq('same-generation sends one PATCH', pending.length, 5);
    pending[4]({ ok: true });
    await tick();
    assertEq('same-generation request settled', dup.settledSaveToken, tokenB);
    assertEq('same-generation idle pending', dup.latestSaveToken > dup.settledSaveToken, false);
    assertEq('same-generation idle drafting', dup.drafting, false);
    assertEq('same-generation success status', statusEl.innerText, 'All changes saved');
    assertEq('committed transient draft overlays stale overlapping GET', sandbox.prksResearchNotesTextForWork('W-save', 'OLD'), 'same');
    assertEq('matching server response observes committed transient text', sandbox.prksResearchNotesTextForWork('W-save', 'same'), 'same');
    assertEq('observed committed draft no longer masks later server text', sandbox.prksResearchNotesTextForWork('W-save', 'SERVER-NEXT'), 'SERVER-NEXT');

    sandbox.prksResetResearchDraftsForTest();
    let newestText = 'older';
    const newerDraft = {
        workId: 'W-newer-draft',
        editor: { value: function () { return newestText; } },
        editGeneration: 0,
        saveSequence: 0,
        latestSaveToken: 0,
        latestSaveEditGeneration: 0,
        settledSaveToken: 0,
        drafting: false,
        saveError: false,
        pendingSave: false,
    };
    ctxS.setEntity('work', { id: 'W-newer-draft' });
    ctxS.setResource('workNotes', newerDraft);
    sandbox.prksWorkNotesMarkEdit(newerDraft, 'W-newer-draft', newestText);
    sandbox.prksEnqueueWorkResearchNotesSave(ctxS, 'W-newer-draft');
    newestText = 'newest';
    sandbox.prksWorkNotesMarkEdit(newerDraft, 'W-newer-draft', newestText);
    pending[5]({ ok: false });
    await tick();
    assertEq('failed older save keeps newer unsent edit drafting', newerDraft.drafting, true);
    assertEq('failed older save does not mark newer edit error', newerDraft.saveError, false);
    assertEq('failed older save leaves drafting status', statusEl.innerText, 'Drafting...');
    sandbox.prksEnqueueWorkResearchNotesSave(ctxS, 'W-newer-draft');
    pending[6]({ ok: true });
    await tick();
    assertEq('newer edit settles after older failure', newerDraft.drafting, false);
    assertEq('newer edit success clears error', newerDraft.saveError, false);
}

/* Regression: a Research Notes save started while a PDF Work is mounted must finish
 * coherently after the Work becomes warm-suspended (tab switched away before the PATCH
 * resolves). Warm suspension must not pause or orphan the in-flight save. */
async function runWarmSaveSettlement() {
    const pending = [];
    sandbox.prksRequest = function () {
        return new Promise(function (resolve) {
            pending.push(resolve);
        });
    };
    let fetchCalls = 0;
    sandbox.fetchWorkDetails = function () {
        fetchCalls += 1;
        return Promise.resolve({ text_content: 'warm note', research_refs: ['concept:warm-ref'] });
    };
    sandbox.prksWorkspaceRefreshTabStatus = function () {};
    sandbox.window.prksWorkspaceRefreshTabStatus = sandbox.prksWorkspaceRefreshTabStatus;

    const ctxWarm = prksEnsureTabContext('warm-notes-a');
    ctxWarm.mounted = true;
    ctxWarm.suspended = false;
    ctxWarm.tabId = 'warm-notes-a';
    const statusEl = { innerText: '' };
    ctxWarm.query = function () {
        return statusEl;
    };
    ctxWarm.setEntity('work', { id: 'W-warm-a', research_refs: [] });
    const notes = {
        editor: {
            value: function () {
                return 'warm note';
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
    ctxWarm.setResource('workNotes', notes);

    sandbox.prksWorkNotesMarkEdit(notes, 'W-warm-a', 'warm note');
    sandbox.prksEnqueueWorkResearchNotesSave(ctxWarm, 'W-warm-a');
    assertEq('warm-save PATCH issued while mounted', pending.length, 1);
    assertEq('warm-save status shows Saving while mounted', statusEl.innerText, 'Saving...');

    /* Switch to another Work: A becomes warm-suspended before the PATCH resolves. */
    ctxWarm.mounted = false;
    ctxWarm.suspended = true;

    pending[0]({ ok: true });
    await tick();
    await tick();

    assert('A remains suspended after warm settle', ctxWarm.suspended);
    assertEq('A is not remounted by save settlement', ctxWarm.mounted, false);
    assertEq('workNotes resource settles while warm', notes.settledSaveToken, notes.latestSaveToken);
    assertEq('warm preserved status settles to saved', statusEl.innerText, 'All changes saved');
    assertEq('post-save Work refresh still runs while warm', fetchCalls, 1);
    const liveWork = ctxWarm.getEntity('work');
    assertEq(
        'research_refs refresh while warm',
        JSON.stringify(liveWork.research_refs),
        JSON.stringify(['concept:warm-ref'])
    );

    /* Resume A: same DOM root reused, status already correct, no rerender needed. */
    ctxWarm.mounted = true;
    ctxWarm.suspended = false;
    assertEq('resumed status remains saved (no stale Saving...)', statusEl.innerText, 'All changes saved');
}

/* Regression: a Research Notes save that fails while its Work is warm-suspended must
 * settle the preserved editor status to an error state, and resuming the Work must not
 * revert that to a stale "Saving..." status. */
async function runWarmSaveErrorSettlement() {
    const pending = [];
    sandbox.prksRequest = function () {
        return new Promise(function (_resolve, reject) {
            pending.push(reject);
        });
    };
    sandbox.fetchWorkDetails = function () {
        return Promise.resolve(null);
    };
    sandbox.prksWorkspaceRefreshTabStatus = function () {};
    sandbox.window.prksWorkspaceRefreshTabStatus = sandbox.prksWorkspaceRefreshTabStatus;

    const ctxWarmErr = prksEnsureTabContext('warm-notes-err');
    ctxWarmErr.mounted = true;
    ctxWarmErr.suspended = false;
    ctxWarmErr.tabId = 'warm-notes-err';
    const statusEl = { innerText: '' };
    ctxWarmErr.query = function () {
        return statusEl;
    };
    ctxWarmErr.setEntity('work', { id: 'W-warm-err', research_refs: [] });
    const notes = {
        editor: {
            value: function () {
                return 'warm error note';
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
    ctxWarmErr.setResource('workNotes', notes);

    sandbox.prksWorkNotesMarkEdit(notes, 'W-warm-err', 'warm error note');
    sandbox.prksEnqueueWorkResearchNotesSave(ctxWarmErr, 'W-warm-err');
    assertEq('warm-error PATCH issued', pending.length, 1);

    ctxWarmErr.mounted = false;
    ctxWarmErr.suspended = true;

    pending[0](new Error('network down'));
    await tick();
    await tick();

    assert('warm error context remains suspended', ctxWarmErr.suspended);
    assertEq('warm error marks workNotes.saveError', notes.saveError, true);
    assertEq('warm error preserved status text', statusEl.innerText, 'Error saving changes');

    ctxWarmErr.mounted = true;
    ctxWarmErr.suspended = false;
    assertEq('resumed error status does not revert to Saving...', statusEl.innerText, 'Error saving changes');
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

function runHorizontalSplitterCleanup() {
    const ctx = prksEnsureTabContext('notes-pointer');
    const ws = makeWorkspace('W-pointer');
    ctx.root = makeRoot(ws);
    ctx.mounted = true;
    ctx.query = function (sel) { return ctx.root.querySelector(sel); };
    sandbox.setupWorkNotesSplitResize(ctx, 'W-pointer');
    const down = {
        button: 0,
        pointerId: 41,
        clientX: 0,
        clientY: 100,
        preventDefault: function () {},
        stopPropagation: function () {},
    };
    ws._handle.dispatch('pointerdown', down);
    assertEq('horizontal pointer drag adds document move listener', (documentListeners.pointermove || []).length, 1);
    assert('horizontal pointer drag class set', ws._handle.classList.contains('dragging'));
    sandbox.document.dispatch('pointercancel', { pointerId: 41 });
    assertEq('pointercancel removes document move listener', (documentListeners.pointermove || []).length, 0);
    assert('pointercancel clears dragging class', !ws._handle.classList.contains('dragging'));

    ws._handle.dispatch('pointerdown', Object.assign({}, down, { pointerId: 42 }));
    assertEq('second pointer drag armed', (documentListeners.pointermove || []).length, 1);
    ctx.unmount('park');
    assertEq('TabContext teardown removes active pointer listener', (documentListeners.pointermove || []).length, 0);
    assert('TabContext teardown clears dragging class', !ws._handle.classList.contains('dragging'));
    sandbox.document.dispatch('pointermove', { clientY: 1, preventDefault: function () {} });
    assertEq('post-unmount pointer move cannot alter detached height', ws._vars['--work-notes-height'], '320px');
}

(async function () {
    try {
        await runSaveGenerationRace();
        await runWarmSaveSettlement();
        await runWarmSaveErrorSettlement();
        runDebounceBookkeeping();
        runHorizontalSplitterCleanup();
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
