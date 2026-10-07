"use strict";
const fs = require('fs');
const os = require('os');
const path = require('path');
const strict = require('assert/strict');
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); },
    { get: (_t, k) => (...args) => { checks += 1; return strict[k](...args); } });
const { createFakeIndexedDBFactory } = require('./lib/fake_indexeddb.js');
const { createPrksLocalStore } = require('../../frontend/js/local-store.js');
const { createPrksOfflineStore } = require('../../frontend/js/offline-store.js');
const { createPrksOfflineRuntime } = require('../../frontend/js/offline-runtime.js');
require('../../frontend/js/work-notes-state.js');

let sequence = 0;
const uuid = () => '00000000-0000-4000-8000-' + (++sequence).toString(16).padStart(12, '0');
const tick = () => new Promise(resolve => setTimeout(resolve, 1));
async function settle() { for (let i = 0; i < 5; i++) await tick(); }
async function until(predicate, label) {
    for (let i = 0; i < 200; i++) {
        if (await predicate()) return;
        await tick();
    }
    assert.fail('timed out waiting for ' + label);
}

const RESEARCH = 'SET_WORK_RESEARCH_NOTE';
const PRIVATE = 'SET_WORK_PRIVATE_NOTE';
const RESEARCH_KIND = 'work-research-note';
const PRIVATE_KIND = 'work-private-note';

function observed(value, revision) {
    return { value, revision };
}

function noteRows(rows, operation, workId) {
    return (rows || []).filter(r => r.operation === operation && r.entity_id === workId);
}

/* ---- acknowledged base is the canonical Work, never the overlay ---- */
function acknowledgedBase() {
    const work = { id: 'W-1', text_content: 'A', private_notes: 'P' };
    const state = { work_id: 'W-1', research_note_revision: 3, private_note_revision: 4 };
    const ops = [
        { operation: RESEARCH, entity_type: 'work', entity_id: 'W-1', status: 'pending',
            payload: { text: 'B' } },
        { operation: PRIVATE, entity_type: 'work', entity_id: 'W-1', status: 'pending',
            payload: { text: 'Q' } },
    ];
    const effective = globalThis.prksEffectiveNoteWork(work, ops);
    assert.equal(effective.text_content, 'B');
    assert.equal(effective.private_notes, 'Q');
    assert.equal(JSON.stringify(work), JSON.stringify({ id: 'W-1', text_content: 'A', private_notes: 'P' }),
        'the acknowledged Work is never mutated');

    const base = globalThis.prksAcknowledgedWorkNoteBase(work, state);
    assert.deepEqual(base.research, { value: 'A', revision: 3 });
    assert.deepEqual(base.private, { value: 'P', revision: 4 });
    assert.notEqual(base.research.value, effective.text_content,
        'the base must not be derived from an already-effective Work');
    assert.equal(globalThis.prksAcknowledgedWorkNoteBase(effective, state).research.value, 'B',
        'feeding it an effective Work is a caller bug; the helper itself does not overlay');

    const empty = globalThis.prksAcknowledgedWorkNoteBase(
        { id: 'W-1', text_content: '', private_notes: null },
        { work_id: 'W-1', research_note_revision: 0, private_note_revision: 0 });
    assert.deepEqual(empty.private, { value: '', revision: 0 });
    assert.equal(globalThis.prksAcknowledgedWorkNoteBase(work, null), null);
}

/* ---- handler contract: compact results, never note bodies ---- */
function handlerContract() {
    const handler = globalThis.prksNoteSyncHandler;
    const researchOp = { operation: RESEARCH, entity_id: 'W-1', payload: { text: 'B' } };
    const privateOp = { operation: PRIVATE, entity_id: 'W-1', payload: { text: 'Q' } };

    const ack = { work_id: 'W-1', note_kind: RESEARCH_KIND, code: 'ACKNOWLEDGED',
        changed: true, server_revision: 2, value_omitted: true };
    assert.equal(handler.isResult(ack, researchOp), true);
    assert.equal(handler.isResult(ack, privateOp), false, 'kinds must not mix');
    assert.equal(handler.isResult(Object.assign({}, ack, { text: 'B' }), researchOp), false,
        'an acknowledgement must not carry the body');
    assert.equal(handler.isResult(Object.assign({}, ack, { value_omitted: false }), researchOp), false);
    assert.equal(handler.isResult(Object.assign({}, ack, { work_id: 'W-OTHER' }), researchOp), false);

    const privateAck = Object.assign({}, ack, { note_kind: PRIVATE_KIND, changed: false });
    assert.equal(handler.isResult(privateAck, privateOp), true);

    const conflict = { work_id: 'W-1', note_kind: RESEARCH_KIND, code: 'REVISION_CONFLICT',
        current_revision: 3, current_bytes: 10, requested_bytes: 12 };
    assert.equal(handler.isResult(conflict, researchOp), true);
    assert.equal(handler.isResult(Object.assign({ current_value: 'secret' }, conflict), researchOp),
        false, 'a conflict must not carry the body');
    assert.equal(handler.isResult(Object.assign({ current_preview: 'secret' }, conflict), researchOp),
        false);
    assert.deepEqual(handler.terminal(conflict).conflict, {
        code: 'REVISION_CONFLICT', current_revision: 3, current_bytes: 10, requested_bytes: 12 });

    const future = { work_id: 'W-1', note_kind: PRIVATE_KIND, code: 'FUTURE_REVISION',
        current_revision: 0, current_bytes: 0, requested_bytes: 1 };
    assert.equal(handler.isResult(future, privateOp), true);
    assert.equal(handler.isResult({ work_id: 'W-1', note_kind: RESEARCH_KIND,
        code: 'ENTITY_NOT_FOUND' }, researchOp), true);
    assert.equal(handler.isResult({ work_id: 'W-1', note_kind: RESEARCH_KIND, code: 'NOPE' },
        researchOp), false);
}

async function coalescingFor(operation, kindLabel) {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    globalThis.prksSync = { store };
    const save = text => store.saveWorkNote('W-1', operation, text, observed('A', 0));
    const rowsOf = async () => noteRows(await store.listOperations(), operation, 'W-1');

    /* A -> B is one intent naming B. */
    const first = await save('B');
    assert.equal((await rowsOf()).length, 1, kindLabel + ' A->B');
    assert.equal((await rowsOf())[0].payload.text, 'B');
    assert.equal((await rowsOf())[0].base_revision, 0);

    /* A -> B -> C is ONE intent naming C. Two rows sharing one base would
     * send B, advance the revision, and make C conflict with an edit the
     * user had already replaced. */
    await save('C');
    let rows = await rowsOf();
    assert.equal(rows.length, 1, kindLabel + ' A->B->C is one row');
    assert.equal(rows[0].payload.text, 'C');
    assert.equal(rows[0].base_revision, 0);
    assert.notEqual(rows[0].op_id, first.op_id, 'a rewritten intent is a new id');

    /* The same desired value keeps the row rather than minting a second op_id. */
    const before = rows[0].op_id;
    const again = await save('C');
    rows = await rowsOf();
    assert.equal(rows.length, 1, kindLabel + ' duplicate desired value');
    assert.equal(rows[0].op_id, before);
    assert.equal(again.op_id, before);

    /* A -> B -> A is not two changes; it is none. */
    await save('A');
    assert.equal((await rowsOf()).length, 0, kindLabel + ' A->B->A cancels');

    /* A POSSIBLY SENT row is immutable. */
    await save('B');
    const claimed = (await rowsOf())[0];
    await store.updateOperationSyncState(claimed.op_id, { status: 'pending', last_error: 'x' });
    await store.claimOperation(claimed.op_id);
    await assert.rejects(() => save('D'), e => e.prksLocalStoreCode === 'scope_busy');
    rows = await rowsOf();
    assert.equal(rows.length, 1, kindLabel + ' immutable after first attempt');
    assert.equal(rows[0].payload.text, 'B', 'the sent intent is untouched');

    await store.updateOperationSyncState(claimed.op_id, { status: 'conflict',
        server_result: { code: 'REVISION_CONFLICT', current_revision: 7,
            current_bytes: 1, requested_bytes: 1 } });
    await assert.rejects(() => save('E'), e => e.prksLocalStoreCode === 'scope_busy');
    assert.equal((await rowsOf())[0].payload.text, 'B', kindLabel + ' conflict stays');

    delete globalThis.prksSync;
}

async function scopesDoNotBlockEachOther() {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    await store.saveWorkNote('W-1', RESEARCH, 'R-B', observed('A', 0));
    const research = (await store.listOperations()).find(r => r.operation === RESEARCH);
    await store.claimOperation(research.op_id);
    await store.saveWorkNote('W-1', PRIVATE, 'P-B', observed('A', 0));
    const rows = await store.listOperations();
    assert.equal(noteRows(rows, RESEARCH, 'W-1').length, 1);
    assert.equal(noteRows(rows, PRIVATE, 'W-1').length, 1, 'Private still saves while Research syncs');
    assert.equal(noteRows(rows, PRIVATE, 'W-1')[0].payload.text, 'P-B');

    await assert.rejects(
        () => store.saveWorkNote('W-1', RESEARCH, 'R-C', observed('A', 0)),
        e => e.prksLocalStoreCode === 'scope_busy');
    await store.saveWorkNote('W-1', PRIVATE, 'P-C', observed('A', 0));
    assert.equal(noteRows(await store.listOperations(), PRIVATE, 'W-1')[0].payload.text, 'P-C');
}

async function byteLimits() {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    const researchLimit = globalThis.PRKS_LOCAL_WORK_RESEARCH_NOTE_BYTES;
    const privateLimit = globalThis.PRKS_LOCAL_WORK_PRIVATE_NOTE_BYTES;
    assert.equal(researchLimit, 32 * 1024 * 1024);
    assert.equal(privateLimit, 64 * 1024);
    assert.equal(globalThis.PRKS_MAX_RESEARCH_NOTE_BYTES, researchLimit);
    assert.equal(globalThis.PRKS_MAX_PRIVATE_NOTE_BYTES, privateLimit);

    const overPrivate = 'p'.repeat(privateLimit + 1);
    await assert.rejects(
        () => store.saveWorkNote('W-1', PRIVATE, overPrivate, observed('', 0)),
        e => e.prksLocalStoreCode === 'payload_too_large');

    const atPrivate = 'q'.repeat(privateLimit);
    const savedPrivate = await store.saveWorkNote('W-1', PRIVATE, atPrivate, observed('', 0));
    assert.equal(savedPrivate.payload.text.length, privateLimit);

    /* Research uses its own 32 MiB cap, not the generic 64 KiB envelope cap.
     * A body just over 64 KiB must save. */
    const overGeneric = 'r'.repeat(64 * 1024 + 1);
    const savedResearch = await store.saveWorkNote('W-1', RESEARCH, overGeneric, observed('', 0));
    assert.equal(savedResearch.payload.text.length, 64 * 1024 + 1);

    await assert.rejects(() => store.enqueueOperation({
        operation: RESEARCH, entity_type: 'work', entity_id: 'W-2',
        payload: { text: overGeneric, extra: 'x'.repeat(1024) }, base_revision: 0,
    }), e => e.prksLocalStoreCode === 'payload_too_large',
        'the allowance belongs to an exact shape');

    await assert.rejects(
        () => store.saveWorkNote('W-1', RESEARCH, 'B', { revision: 0 }),
        e => e.prksLocalStoreCode === 'invalid_base',
        'observed must carry the acknowledged value, not revision alone');
}

async function mutationTestAtoBtoA() {
    const src = fs.readFileSync(path.join(__dirname, '../../frontend/js/local-store.js'), 'utf8');
    const needle = 'if (text === observed.value) { setResult(null); return; }';
    assert.ok(src.includes(needle), 'cancel-to-base check must exist to mutation-test');
    const mutated = src.replace(needle, '/* mutated: no cancel to observed.value */');
    assert.notEqual(mutated, src);
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'prks-mutated-note-store-'));
    const tmp = path.join(tmpDir, 'local-store.js');
    fs.writeFileSync(tmp, mutated, { mode: 0o600 });
    try {
        const { createPrksLocalStore: createMutated } = require(tmp);
        const store = createMutated({ indexedDB: createFakeIndexedDBFactory(), uuid });
        await store.saveWorkNote('W-1', RESEARCH, 'B', observed('A', 0));
        await store.saveWorkNote('W-1', RESEARCH, 'A', observed('A', 0));
        const rows = noteRows(await store.listOperations(), RESEARCH, 'W-1');
        assert.equal(rows.length, 1, 'without the cancel, A->B->A leaves an A operation');
        assert.equal(rows[0].payload.text, 'A');
    } finally {
        try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch (_e) { /* best-effort */ }
    }
}

/* ---- editor save path: enqueue selects acknowledged observed, not effective ---- */
function enqueueSelectsAcknowledgedObserved() {
    const works = fs.readFileSync(
        path.join(__dirname, '../../frontend/js/components/works.js'), 'utf8');
    const start = works.indexOf('function prksEnqueueWorkResearchNotesSave(');
    assert.ok(start >= 0, 'enqueue must exist');
    const end = works.indexOf('function prksFlushPendingWorkResearchNotes(', start);
    assert.ok(end > start, 'flush must follow enqueue');
    const body = works.slice(start, end);
    assert.ok(body.includes("prksWorkNoteObserved(owner, 'work-research-note')"),
        'enqueue must measure against the acknowledged observed base');
    assert.ok(body.includes("prksSaveWorkNoteDurably(id, 'work-research-note', content, observed)"),
        'enqueue must pass that observed into the durable save');
    assert.ok(!body.includes('prksEffectiveNoteWork'),
        'enqueue must not derive observed from an already-effective Work');
    const flushStart = works.indexOf('function prksFlushPendingWorkResearchNotes(');
    const flushEnd = works.indexOf('\nwindow.prksEnqueueWorkResearchNotesSave', flushStart);
    assert.ok(flushEnd > flushStart, 'flush must be followed by its window export');
    const flushBody = works.slice(flushStart, flushEnd);
    assert.ok(flushBody.includes('prksEnqueueWorkResearchNotesSave(owner, id)'),
        'flush must hand off to the same enqueue path');
}

async function durableSaveThroughObservedBaseCancels() {
    /* Mirrors prksEnqueueWorkResearchNotesSave: editor text + prksWorkNoteObserved
     * → prksSaveWorkNoteDurably. Store-only coalescing is covered above; this
     * proves the acknowledged-base selection the editor path relies on. */
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    globalThis.prksSync = { store, changed() {} };
    const resources = {
        workNotesObserved: {
            research: { value: 'A', revision: 0 },
            private: { value: '', revision: 0 },
        },
    };
    const ctx = {
        getResource: key => resources[key],
        setResource: (key, value) => { resources[key] = value; },
    };
    const observedOf = () => globalThis.prksWorkNoteObserved(ctx, RESEARCH_KIND);

    async function saveEditorText(text) {
        const result = await globalThis.prksSaveWorkNoteDurably(
            'W-1', RESEARCH_KIND, text, observedOf());
        assert.equal(result.code, 'saved', 'editor-path save of ' + JSON.stringify(text));
    }

    await saveEditorText('B');
    let rows = noteRows(await store.listOperations(), RESEARCH, 'W-1');
    assert.equal(rows.length, 1, 'A->B through durable save leaves one intent');
    assert.equal(rows[0].payload.text, 'B');
    assert.equal(observedOf().value, 'A',
        'pending B must not rewrite the acknowledged observed base');
    const effective = globalThis.prksEffectiveNoteWork(
        { id: 'W-1', text_content: 'A', private_notes: '' }, rows);
    assert.equal(effective.text_content, 'B');
    assert.notEqual(observedOf().value, effective.text_content,
        'observed stays on A while the overlay shows B');

    await saveEditorText('A');
    rows = noteRows(await store.listOperations(), RESEARCH, 'W-1');
    assert.equal(rows.length, 0, 'A->B->A through acknowledged observed cancels');

    /* Failure mode Codex called out: feeding the effective body as observed
     * makes editing back to A look like a new change. */
    const poison = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    await poison.saveWorkNote('W-1', RESEARCH, 'B', observed('A', 0));
    await poison.saveWorkNote('W-1', RESEARCH, 'A', observed('B', 0));
    const poisoned = noteRows(await poison.listOperations(), RESEARCH, 'W-1');
    assert.equal(poisoned.length, 1, 'effective-as-observed would leave an A operation');
    assert.equal(poisoned[0].payload.text, 'A');

    delete globalThis.prksSync;
}

function ensureWorksEnqueueLoaded() {
    if (typeof globalThis.prksEnqueueWorkResearchNotesSave === 'function') return;
    globalThis.window = globalThis;
    if (!globalThis.document) {
        globalThis.document = {
            documentElement: { classList: { contains: () => false, toggle: () => {} } },
            getElementById: () => null,
            querySelector: () => null,
            querySelectorAll: () => [],
            addEventListener: () => {},
            removeEventListener: () => {},
            createElement: () => ({
                style: {},
                setAttribute: () => {},
                classList: { add: () => {}, remove: () => {}, contains: () => false },
            }),
        };
    }
    if (!globalThis.localStorage) {
        globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
    }
    if (!globalThis.requestAnimationFrame) {
        globalThis.requestAnimationFrame = (fn) => { fn(); return 0; };
    }
    if (!globalThis.ResizeObserver) {
        globalThis.ResizeObserver = function () {
            this.observe = function () {};
            this.disconnect = function () {};
        };
    }
    require('../../frontend/js/components/works.js');
    assert.equal(typeof globalThis.prksEnqueueWorkResearchNotesSave, 'function',
        'production enqueue must load under Node stubs');
}

async function enqueuePathAtoBtoACancels() {
    /* Production caller Codex/CodeRabbit asked for: editor context →
     * prksEnqueueWorkResearchNotesSave → pending returns to zero on A→B→A.
     * Keeps durableSaveThroughObservedBaseCancels (direct durable save) intact. */
    ensureWorksEnqueueLoaded();
    if (typeof globalThis.prksResetResearchDraftsForTest === 'function') {
        globalThis.prksResetResearchDraftsForTest();
    }
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    globalThis.prksSync = { store, changed() {} };

    let editorText = 'A';
    const notes = {
        editor: { value: () => editorText },
        editGeneration: 0,
        saveSequence: 0,
        latestSaveToken: 0,
        latestSaveEditGeneration: 0,
        settledSaveToken: 0,
        drafting: false,
        saveError: false,
        pendingSave: false,
        workId: 'W-1',
    };
    const resources = {
        workNotes: notes,
        workNotesObserved: {
            research: { value: 'A', revision: 0 },
            private: { value: '', revision: 0 },
        },
        workNotesCanonical: { id: 'W-1', text_content: 'A', private_notes: '' },
    };
    const statusEl = { innerText: '' };
    const ctx = {
        timers: new Map(),
        tabId: 'notes-enqueue-cancel',
        getResource: key => resources[key],
        setResource: (key, value) => { resources[key] = value; },
        getEntity: () => ({ id: 'W-1', text_content: 'A', private_notes: '' }),
        query: () => statusEl,
        setTimer(name, tid) { this.timers.set(name, tid); },
        clearTimer(name) {
            const tid = this.timers.get(name);
            if (tid) clearTimeout(tid);
            this.timers.delete(name);
        },
        isCurrent: () => true,
    };

    async function enqueueEditorText(text) {
        editorText = text;
        notes.editGeneration = (Number(notes.editGeneration) || 0) + 1;
        const promise = globalThis.prksEnqueueWorkResearchNotesSave(ctx, 'W-1');
        assert.ok(promise && typeof promise.then === 'function',
            'production enqueue must return the durable save promise');
        const result = await promise;
        assert.equal(result.code, 'saved', 'enqueue save of ' + JSON.stringify(text));
    }

    await enqueueEditorText('B');
    assert.equal(noteRows(await store.listOperations(), RESEARCH, 'W-1').length, 1,
        'enqueue A->B leaves one pending op');

    await enqueueEditorText('A');
    assert.equal(noteRows(await store.listOperations(), RESEARCH, 'W-1').length, 0,
        'enqueue A->B->A returns pending op count to zero');

    if (typeof globalThis.prksResetResearchDraftsForTest === 'function') {
        globalThis.prksResetResearchDraftsForTest();
    }
    delete globalThis.prksSync;
}

/* ---- #465: a scope_busy body is retried, never dropped or called saved ---- */
const STILL_SYNCING = 'Still syncing — wait or resolve the conflict in Diagnostics';

/** A TabContext-shaped owner for the production Research Notes save path:
 * generation, timers, cleanups and the `workNotes` slot behave like
 * tab-context.js so retry ownership is observable. */
async function busyNoteHarness(tabId, options) {
    ensureWorksEnqueueLoaded();
    globalThis.prksResetResearchDraftsForTest();
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    const listeners = new Set();
    let saveCalls = 0;
    const realSave = store.saveWorkNote;
    store.saveWorkNote = function (...args) { saveCalls += 1; return realSave.apply(store, args); };
    globalThis.prksSync = {
        store,
        changed() {},
        subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    };
    let editorText = 'A';
    const notes = {
        workId: 'W-1',
        editor: { value: () => editorText },
        editGeneration: 0, saveSequence: 0, latestSaveToken: 0,
        latestSaveEditGeneration: 0, settledSaveToken: 0,
        drafting: false, saveError: false, pendingSave: false,
    };
    const resources = {
        workNotes: notes,
        workNotesObserved: {
            research: { value: '', revision: 0 },
            private: { value: '', revision: 0 },
        },
    };
    const statusEl = { innerText: '' };
    const cleanups = new Set();
    const ctx = {
        tabId,
        generation: 1,
        mounted: true,
        destroyed: false,
        timers: new Map(),
        ui: {},
        getResource: key => resources[key],
        setResource: (key, value) => { resources[key] = value; },
        getEntity: () => ({ id: 'W-1' }),
        query: () => statusEl,
        isCurrent(generation) {
            if (this.destroyed || !this.mounted) return false;
            return typeof generation !== 'number' || generation === this.generation;
        },
        setTimer(name, tid) { this.clearTimer(name); this.timers.set(name, tid); },
        clearTimer(name) {
            const tid = this.timers.get(name);
            if (tid) clearTimeout(tid);
            this.timers.delete(name);
        },
        registerCleanup(fn) { cleanups.add(fn); return () => cleanups.delete(fn); },
        /* tab-context.js teardownRuntime: timers, the editor slot, cleanups. */
        teardown() {
            for (const name of Array.from(this.timers.keys())) this.clearTimer(name);
            delete resources.workNotes;
            const fns = Array.from(cleanups);
            cleanups.clear();
            fns.forEach(fn => fn());
        },
    };
    /* A was sent once and failed: attempted, so immutable in the store. By
     * default this session queued A itself; `foreignA` queues it as another
     * tab or pane would, straight into the shared store. */
    let a;
    if (options && options.foreignA) {
        a = await store.saveWorkNote('W-1', RESEARCH, 'A', observed('', 0));
    } else {
        globalThis.prksWorkNotesMarkEdit(notes, 'W-1', 'A', ctx);
        assert.equal((await globalThis.prksEnqueueWorkResearchNotesSave(ctx, 'W-1')).code, 'saved');
        await until(() => ctx.ui.workResearchNoteSession.state === 'committed', 'A to settle');
        a = (await store.listOperations()).find(r => r.operation === RESEARCH);
        statusEl.innerText = '';
    }
    await store.claimOperation(a.op_id);
    await store.updateOperationSyncState(a.op_id, { status: 'pending', last_error: 'Sync failed; retry scheduled.' });
    const h = {
        store, ctx, notes, resources, statusEl, listeners, cleanups, a,
        saves: () => saveCalls,
        type(text) {
            editorText = text;
            /* initEasyMDE's change handler: mark, then (re)arm the debounce. */
            globalThis.prksWorkNotesMarkEdit(notes, 'W-1', text, ctx);
            globalThis.prksScheduleWorkResearchNotesSave(ctx, 'W-1');
        },
        session: () => ctx.ui.workResearchNoteSession,
        emit(event) { Array.from(listeners).forEach(fn => fn(event || {})); },
        async rows() { return noteRows(await store.listOperations(), RESEARCH, 'W-1'); },
        /* sync-runtime: acknowledge, retire, then emit with the op. */
        async ack(row, revision) {
            await store.updateOperationSyncState(row.op_id, { status: 'acknowledged', last_error: null });
            await store.deleteAcknowledgedOperation(row.op_id);
            /* acceptAck advances the observed slot in place. */
            Object.assign(resources.workNotesObserved.research, { value: row.payload.text, revision });
            const event = { acknowledged: { code: 'ACKNOWLEDGED', server_revision: revision },
                operation: RESEARCH, op: row };
            return event;
        },
        async blockB() {
            h.type('A B');
            ctx.clearTimer('saveNotesTimeout');
            const result = await globalThis.prksEnqueueWorkResearchNotesSave(ctx, 'W-1');
            assert.equal(result.code, 'scope_busy', 'B is refused while attempted A holds the aggregate');
            await settle();
        },
        done() {
            ctx.teardown();
            globalThis.prksResetResearchDraftsForTest();
            delete globalThis.prksSync;
        },
    };
    return h;
}

async function scopeBusyBodyRetriesAfterBlockingSaveSettles() {
    const h = await busyNoteHarness('busy-retry');
    await h.blockB();
    assert.equal(h.session().state, 'blocked', 'B stays an unsaved draft, not a dead error');
    assert.equal(h.session().text, 'A B');
    assert.equal(h.notes.saveError, false);
    assert.equal(h.notes.drafting, true, 'the tab status keeps showing an unsaved draft');
    assert.equal(h.statusEl.innerText, STILL_SYNCING);
    assert.ok(h.ctx.timers.has('researchNotesBusyRetry'), 'a fallback retry is armed on the owner');
    assert.deepEqual((await h.rows()).map(r => r.payload.text), ['A'], 'B never entered the queue');

    /* A sync event while A is still occupying the aggregate does not resend. */
    const savesBefore = h.saves();
    h.emit();
    await settle();
    assert.equal(h.saves(), savesBefore, 'no retry while the earlier row is unsettled');

    /* A recovers and acknowledges. No further keystroke. */
    const ackA = await h.ack(h.a, 1);
    assert.equal(globalThis.prksResearchNotesSyncEventStatus(h.ctx, h.notes, ackA), null,
        'an acknowledgement of A never reads as All changes saved while B is the editor body');
    h.emit(ackA);
    await until(() => h.session().state === 'committed', 'the retried save to settle');
    const rows = await h.rows();
    assert.deepEqual(rows.map(r => r.payload.text), ['A B'],
        'B is enqueued once A settles, without another edit');
    assert.equal(rows[0].base_revision, 1, 'B is measured against A\'s acknowledged revision');
    assert.equal(h.session().state, 'committed');
    assert.equal(h.statusEl.innerText, 'Waiting to sync', 'B is queued, not yet saved');
    assert.equal(h.ctx.timers.has('researchNotesBusyRetry'), false, 'the retry timer is gone');
    assert.equal(h.listeners.size, 0, 'the retry subscription is gone');

    const ackB = await h.ack(rows[0], 2);
    assert.equal(globalThis.prksResearchNotesSyncEventStatus(h.ctx, h.notes, ackB), 'All changes saved',
        'saved once the acknowledged body is the editor body');
    h.done();
}

async function ackOfOlderBodyNeverPaintsSaved() {
    const h = await busyNoteHarness('busy-ack-older');
    /* No session at all: the editor body alone decides. */
    h.type('A B');
    h.ctx.clearTimer('saveNotesTimeout');
    globalThis.prksResetResearchDraftsForTest();
    const ackA = await h.ack(h.a, 1);
    assert.equal(globalThis.prksResearchNotesSyncEventStatus(h.ctx, h.notes, ackA), null,
        'ack of A while the editor shows A B is not All changes saved');
    h.type('A');
    h.ctx.clearTimer('saveNotesTimeout');
    assert.equal(globalThis.prksResearchNotesSyncEventStatus(h.ctx, h.notes, ackA), null,
        'a drafting session is not saved even when its text matches');
    h.done();
}

async function leaveFlushSendsBlockedBodyWithoutTimer() {
    const h = await busyNoteHarness('busy-leave');
    await h.blockB();
    assert.equal(h.ctx.timers.has('saveNotesTimeout'), false, 'no debounce timer remains');
    /* A settles with no sync event reaching this owner. */
    await h.ack(h.a, 1);
    globalThis.prksFlushPendingWorkResearchNotes(h.ctx);
    await until(() => h.session().state === 'committed', 'the flushed save to settle');
    assert.deepEqual((await h.rows()).map(r => r.payload.text), ['A B'],
        'leaving flushes the blocked body');
    assert.equal(h.ctx.timers.has('researchNotesBusyRetry'), false);
    assert.equal(h.listeners.size, 0);
    h.done();
}

async function blockedRetryKeepsItsBaseOverAForeignRefresh() {
    /* A same-Work refresh rebuilds the observed base from a newer body C
     * another device wrote after A. The retry must not send B against C's
     * revision (a silent overwrite); it keeps the base B was refused against,
     * so the server reports the conflict. */
    const h = await busyNoteHarness('busy-foreign-refresh');
    await h.blockB();
    await until(() => h.session().blockedBehindText === 'A', 'the blocking body to be remembered');
    assert.deepEqual(h.session().blockedBase, { value: '', revision: 0 });
    await h.store.updateOperationSyncState(h.a.op_id, { status: 'acknowledged', last_error: null });
    await h.store.deleteAcknowledgedOperation(h.a.op_id);
    h.resources.workNotesObserved = {
        research: { value: 'C', revision: 2 },
        private: { value: '', revision: 0 },
    };
    h.emit();
    await until(() => h.session().state === 'committed', 'the retried save after a refresh');
    const rows = await h.rows();
    assert.deepEqual(rows.map(r => r.payload.text), ['A B']);
    assert.equal(rows[0].base_revision, 0, 'B keeps the base it was refused against, not C\'s');
    h.done();
}

async function refreshedBaseCountsOnlyForThisSessionsOwnBlockingSave() {
    const retire = async (h, row) => {
        await h.store.updateOperationSyncState(row.op_id, { status: 'acknowledged', last_error: null });
        await h.store.deleteAcknowledgedOperation(row.op_id);
    };
    const refreshTo = (h, value, revision) => {
        h.resources.workNotesObserved = {
            research: { value, revision },
            private: { value: '', revision: 0 },
        };
    };

    /* The blocking row A is another tab's (this session never queued it).
     * Another runtime acknowledges it, then a same-Work refresh rebuilds the
     * base as {A, 1}. B must not go out against r1: that would silently
     * replace A, which this owner never saw. It keeps r0, so the server
     * reports the conflict. */
    let h = await busyNoteHarness('busy-foreign-blocking-refresh', { foreignA: true });
    await h.blockB();
    await until(() => h.session().blockedBehindText === 'A', 'the blocking body to be remembered');
    await retire(h, h.a);
    refreshTo(h, 'A', 1);
    h.emit();
    await until(() => h.session().state === 'committed', 'the retried save after a refresh');
    let rows = await h.rows();
    assert.deepEqual(rows.map(r => r.payload.text), ['A B']);
    assert.equal(rows[0].base_revision, 0, 'another tab\'s A never becomes B\'s base through a refresh');
    h.done();

    /* Two panes in one runtime: pane 2 owns attempted A, this pane owns
     * blocked B. A's acknowledgement in this runtime advances this pane's
     * observed slot in place (acceptAck runs for every same-Work owner). B
     * still keeps r0 and becomes a conflict rather than replacing A. */
    h = await busyNoteHarness('busy-foreign-blocking-ack', { foreignA: true });
    await h.blockB();
    await until(() => h.session().blockedBehindText === 'A', 'the blocking body to be remembered');
    const foreignAck = await h.ack(h.a, 1);
    h.emit(foreignAck);
    await until(() => h.session().state === 'committed', 'the retried save after a foreign ack');
    rows = await h.rows();
    assert.deepEqual(rows.map(r => r.payload.text), ['A B']);
    assert.equal(rows[0].base_revision, 0, 'another pane\'s acknowledged A never becomes B\'s base');
    h.done();

    /* This session queued A itself; the same refresh is then A's own advance. */
    h = await busyNoteHarness('busy-own-blocking-refresh');
    await retire(h, h.a);
    h.type('A2');
    h.ctx.clearTimer('saveNotesTimeout');
    assert.equal((await globalThis.prksEnqueueWorkResearchNotesSave(h.ctx, 'W-1')).code, 'saved');
    await until(() => h.session().state === 'committed', 'A2 to settle');
    const [a2] = await h.rows();
    await h.store.claimOperation(a2.op_id);
    await h.store.updateOperationSyncState(a2.op_id, { status: 'pending', last_error: 'Sync failed; retry scheduled.' });
    h.type('A2 B');
    h.ctx.clearTimer('saveNotesTimeout');
    assert.equal((await globalThis.prksEnqueueWorkResearchNotesSave(h.ctx, 'W-1')).code, 'scope_busy');
    await until(() => h.session().blockedBehindText === 'A2', 'the own blocking body to be remembered');
    await retire(h, a2);
    refreshTo(h, 'A2', 1);
    h.emit();
    await until(() => h.session().state === 'committed', 'the retried save after an own refresh');
    rows = await h.rows();
    assert.deepEqual(rows.map(r => r.payload.text), ['A2 B']);
    assert.equal(rows[0].base_revision, 1, 'this session\'s own A2 advanced the base');
    h.done();
}

async function busyRetryIsOwnerScoped() {
    /* Cold release / destroy: teardown stops timer and subscription. */
    let h = await busyNoteHarness('busy-teardown');
    await h.blockB();
    assert.equal(h.listeners.size, 1);
    assert.equal(h.cleanups.size, 1, 'the retry registers one owner cleanup');
    h.ctx.teardown();
    h.ctx.mounted = false;
    assert.equal(h.listeners.size, 0, 'teardown stops the retry subscription');
    assert.equal(h.ctx.timers.size, 0, 'teardown clears the retry timer');
    let ack = await h.ack(h.a, 1);
    const savesAfterTeardown = h.saves();
    h.emit(ack);
    await settle();
    assert.equal(h.saves(), savesAfterTeardown, 'a released owner never sends');
    h.done();

    /* Generation change (route to another Work in the same tab). */
    h = await busyNoteHarness('busy-generation');
    await h.blockB();
    h.ctx.generation += 1;
    ack = await h.ack(h.a, 1);
    const savesAfterGeneration = h.saves();
    h.emit(ack);
    await settle();
    assert.equal(h.saves(), savesAfterGeneration, 'an older generation does not retry into the new one');
    assert.equal(h.listeners.size, 0, 'the stale retry stops itself');
    assert.equal(h.cleanups.size, 0, 'and unregisters its cleanup');
    h.done();

    /* A newer edit takes over: the debounce owns the newest body. */
    h = await busyNoteHarness('busy-newer-edit');
    await h.blockB();
    h.type('A B C');
    assert.equal(h.listeners.size, 0, 'a new edit stops the busy retry');
    assert.equal(h.ctx.timers.has('researchNotesBusyRetry'), false);
    assert.ok(h.ctx.timers.has('saveNotesTimeout'), 'the ordinary debounce is armed');
    h.done();

    /* The editor slot disposed (warm park keeps it; a cold dispose does not). */
    h = await busyNoteHarness('busy-disposed');
    await h.blockB();
    delete h.resources.workNotes;
    ack = await h.ack(h.a, 1);
    const savesAfterDispose = h.saves();
    h.emit(ack);
    await settle();
    assert.equal(h.saves(), savesAfterDispose, 'no editor, no retry: an empty body is never sent');
    assert.equal(h.listeners.size, 0);
    h.done();
}

async function reconciliation() {
    const cache = createPrksOfflineStore({ indexedDB: createFakeIndexedDBFactory() });
    await cache.putEntity('work', 'W-1', {
        id: 'W-1', title: 'Paper', text_content: 'A', private_notes: 'P',
    });
    await cache.putEntity('work-notes-state', 'W-1', {
        work_id: 'W-1', research_note_revision: 1, private_note_revision: 2,
    });
    await cache.putEntity('concept', 'C-1', { id: 'C-1', name: 'Old' });
    await cache.putList('concepts:index', [{ id: 'C-1', name: 'Old' }], '');
    await cache.putEntity('argument', 'A-1', { id: 'A-1', name: 'Arg' });
    await cache.putList('arguments:index', [{ id: 'A-1', name: 'Arg' }], '');
    await cache.putEntity('research-graph-core', 'snapshot', { nodes: [], edges: [] });
    await cache.putEntity('research-graph-people', 'snapshot', { nodes: [], edges: [] });
    const offline = createPrksOfflineRuntime({
        store: cache, window: null,
        prksRequest: async () => { throw new Error('no reads in this scenario'); },
    });

    const researchOp = { operation: RESEARCH, entity_id: 'W-1', payload: { text: 'B [[concept:New]]' } };
    const researchAck = { work_id: 'W-1', note_kind: RESEARCH_KIND, code: 'ACKNOWLEDGED',
        changed: true, server_revision: 2, value_omitted: true };
    const conceptsBefore = offline.currentDomainGeneration('concepts');
    const argumentsBefore = offline.currentDomainGeneration('arguments');
    const graphBefore = offline.currentDomainGeneration('research-graph-core');
    const peopleGraphBefore = offline.currentDomainGeneration('research-graph-people');
    assert.equal(await offline.reconcileWorkNote(researchAck, researchOp), true);
    assert.equal((await cache.getEntity('work', 'W-1')).value.text_content, 'B [[concept:New]]');
    assert.equal((await cache.getEntity('work', 'W-1')).value.private_notes, 'P',
        'a Research ACK must not rewrite Private Notes');
    assert.equal((await cache.getEntity('work-notes-state', 'W-1')).value.research_note_revision, 2);
    assert.equal((await cache.getEntity('work-notes-state', 'W-1')).value.private_note_revision, 2,
        'a Research ACK patches only its own revision');
    assert.ok(offline.currentDomainGeneration('concepts') > conceptsBefore);
    assert.ok(offline.currentDomainGeneration('arguments') > argumentsBefore);
    assert.ok(offline.currentDomainGeneration('research-graph-core') > graphBefore);
    assert.ok(offline.currentDomainGeneration('research-graph-people') > peopleGraphBefore);

    const privateOp = { operation: PRIVATE, entity_id: 'W-1', payload: { text: 'Q' } };
    const privateAck = { work_id: 'W-1', note_kind: PRIVATE_KIND, code: 'ACKNOWLEDGED',
        changed: true, server_revision: 3, value_omitted: true };
    const conceptsMid = offline.currentDomainGeneration('concepts');
    const argumentsMid = offline.currentDomainGeneration('arguments');
    const graphMid = offline.currentDomainGeneration('research-graph-core');
    const peopleGraphMid = offline.currentDomainGeneration('research-graph-people');
    /* Dispatch through the production handler + wrapper, not offline.reconcile*
     * directly. createPrksOfflineRuntime({}) owns the default root wrappers, so
     * rebind them to this scenario's runtime for the duration of the ACK. */
    const prevPrivate = globalThis.prksOfflineReconcilePrivateNote;
    const prevResearch = globalThis.prksOfflineReconcileWorkNote;
    globalThis.prksOfflineReconcilePrivateNote = (result, op) =>
        offline.reconcilePrivateNote(result, op);
    globalThis.prksOfflineReconcileWorkNote = (result, op) =>
        offline.reconcileWorkNote(result, op);
    try {
        assert.equal(
            await globalThis.prksNoteSyncHandler.reconcile(privateAck, privateOp),
            true,
            'Private ACK must travel through prksNoteSyncHandler.reconcile');
    } finally {
        globalThis.prksOfflineReconcilePrivateNote = prevPrivate;
        globalThis.prksOfflineReconcileWorkNote = prevResearch;
    }
    assert.equal((await cache.getEntity('work', 'W-1')).value.private_notes, 'Q');
    assert.equal((await cache.getEntity('work', 'W-1')).value.text_content, 'B [[concept:New]]');
    assert.equal((await cache.getEntity('work-notes-state', 'W-1')).value.private_note_revision, 3);
    assert.equal((await cache.getEntity('work-notes-state', 'W-1')).value.research_note_revision, 2);
    assert.equal(offline.currentDomainGeneration('concepts'), conceptsMid,
        'a Private ACK must not fence Concepts');
    assert.equal(offline.currentDomainGeneration('arguments'), argumentsMid);
    assert.equal(offline.currentDomainGeneration('research-graph-core'), graphMid);
    assert.equal(offline.currentDomainGeneration('research-graph-people'), peopleGraphMid);

    /* An older acknowledgement must not move the base backwards. */
    await offline.reconcileWorkNote(
        Object.assign({}, researchAck, { server_revision: 1, changed: false }), researchOp);
    assert.equal((await cache.getEntity('work-notes-state', 'W-1')).value.research_note_revision, 2);

    /* Same-revision no-op vs stale convergence: both ACK with
     * changed=false. Only the second must fence, because this device's
     * derived caches may still be from the older body it observed. */
    async function researchAckFence(workId, baseRevision, serverRevision) {
        const cacheN = createPrksOfflineStore({ indexedDB: createFakeIndexedDBFactory() });
        await cacheN.putEntity('work', workId, { id: workId, text_content: 'Same' });
        await cacheN.putEntity('work-notes-state', workId, {
            work_id: workId, research_note_revision: serverRevision, private_note_revision: 0,
        });
        await cacheN.putEntity('concept', 'C-old', { id: 'C-old', name: 'Old' });
        await cacheN.putList('concepts:index', [{ id: 'C-old', name: 'Old' }], '');
        await cacheN.putEntity('argument', 'A-old', { id: 'A-old', name: 'Arg' });
        await cacheN.putList('arguments:index', [{ id: 'A-old', name: 'Arg' }], '');
        await cacheN.putEntity('research-graph-core', 'snapshot', { nodes: [], edges: [] });
        await cacheN.putEntity('research-graph-people', 'snapshot', { nodes: [], edges: [] });
        const runtime = createPrksOfflineRuntime({
            store: cacheN, window: null,
            prksRequest: async () => { throw new Error('no reads'); },
        });
        const before = {
            concepts: runtime.currentDomainGeneration('concepts'),
            arguments: runtime.currentDomainGeneration('arguments'),
            graph: runtime.currentDomainGeneration('research-graph-core'),
            peopleGraph: runtime.currentDomainGeneration('research-graph-people'),
        };
        assert.equal(await runtime.reconcileWorkNote({
            work_id: workId, note_kind: RESEARCH_KIND, code: 'ACKNOWLEDGED',
            changed: false, server_revision: serverRevision, value_omitted: true,
        }, {
            operation: RESEARCH, entity_id: workId, payload: { text: 'Same' },
            base_revision: baseRevision,
        }), true);
        return {
            runtime,
            before,
            after: {
                concepts: runtime.currentDomainGeneration('concepts'),
                arguments: runtime.currentDomainGeneration('arguments'),
                graph: runtime.currentDomainGeneration('research-graph-core'),
                peopleGraph: runtime.currentDomainGeneration('research-graph-people'),
            },
        };
    }

    const sameRev = await researchAckFence('W-same', 4, 4);
    assert.equal(sameRev.after.concepts, sameRev.before.concepts,
        'a current-revision no-op does not fence Concepts');
    assert.equal(sameRev.after.arguments, sameRev.before.arguments,
        'a current-revision no-op does not fence Arguments');
    assert.equal(sameRev.after.graph, sameRev.before.graph,
        'a current-revision no-op does not fence Graph');
    assert.equal(sameRev.after.peopleGraph, sameRev.before.peopleGraph,
        'a current-revision no-op does not fence People Graph');

    const stale = await researchAckFence('W-stale', 3, 4);
    assert.ok(stale.after.concepts > stale.before.concepts,
        'stale convergence fences Concepts');
    assert.ok(stale.after.arguments > stale.before.arguments,
        'stale convergence fences Arguments');
    assert.ok(stale.after.graph > stale.before.graph,
        'stale convergence fences Graph');
    assert.ok(stale.after.peopleGraph > stale.before.peopleGraph,
        'stale convergence fences People Graph');
}

async function main() {
    acknowledgedBase();
    handlerContract();
    enqueueSelectsAcknowledgedObserved();
    await coalescingFor(RESEARCH, 'Research');
    await coalescingFor(PRIVATE, 'Private');
    await scopesDoNotBlockEachOther();
    await byteLimits();
    await mutationTestAtoBtoA();
    await durableSaveThroughObservedBaseCancels();
    await enqueuePathAtoBtoACancels();
    await scopeBusyBodyRetriesAfterBlockingSaveSettles();
    await ackOfOlderBodyNeverPaintsSaved();
    await leaveFlushSendsBlockedBodyWithoutTimer();
    await busyRetryIsOwnerScoped();
    await blockedRetryKeepsItsBaseOverAForeignRefresh();
    await refreshedBaseCountsOnlyForThisSessionsOwnBlockingSave();
    await reconciliation();
    console.log('All ' + checks + ' Work note checks passed');
}

main().catch(error => { console.error(error); process.exit(1); });
