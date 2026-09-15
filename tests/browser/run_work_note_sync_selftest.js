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
    const tmp = path.join(os.tmpdir(), 'prks-mutated-note-store-' + process.pid + '.js');
    fs.writeFileSync(tmp, mutated);
    try {
        const { createPrksLocalStore: createMutated } = require(tmp);
        const store = createMutated({ indexedDB: createFakeIndexedDBFactory(), uuid });
        await store.saveWorkNote('W-1', RESEARCH, 'B', observed('A', 0));
        await store.saveWorkNote('W-1', RESEARCH, 'A', observed('A', 0));
        const rows = noteRows(await store.listOperations(), RESEARCH, 'W-1');
        assert.equal(rows.length, 1, 'without the cancel, A->B->A leaves an A operation');
        assert.equal(rows[0].payload.text, 'A');
    } finally {
        try { fs.unlinkSync(tmp); } catch (_e) { /* best-effort */ }
    }
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

    const privateOp = { operation: PRIVATE, entity_id: 'W-1', payload: { text: 'Q' } };
    const privateAck = { work_id: 'W-1', note_kind: PRIVATE_KIND, code: 'ACKNOWLEDGED',
        changed: true, server_revision: 3, value_omitted: true };
    const conceptsMid = offline.currentDomainGeneration('concepts');
    const argumentsMid = offline.currentDomainGeneration('arguments');
    const graphMid = offline.currentDomainGeneration('research-graph-core');
    assert.equal(await offline.reconcilePrivateNote(privateAck, privateOp), true);
    assert.equal((await cache.getEntity('work', 'W-1')).value.private_notes, 'Q');
    assert.equal((await cache.getEntity('work', 'W-1')).value.text_content, 'B [[concept:New]]');
    assert.equal((await cache.getEntity('work-notes-state', 'W-1')).value.private_note_revision, 3);
    assert.equal((await cache.getEntity('work-notes-state', 'W-1')).value.research_note_revision, 2);
    assert.equal(offline.currentDomainGeneration('concepts'), conceptsMid,
        'a Private ACK must not fence Concepts');
    assert.equal(offline.currentDomainGeneration('arguments'), argumentsMid);
    assert.equal(offline.currentDomainGeneration('research-graph-core'), graphMid);

    /* An older acknowledgement must not move the base backwards. */
    await offline.reconcileWorkNote(
        Object.assign({}, researchAck, { server_revision: 1, changed: false }), researchOp);
    assert.equal((await cache.getEntity('work-notes-state', 'W-1')).value.research_note_revision, 2);

    /* A convergent Research write still patches text but fences nothing. */
    const cache2 = createPrksOfflineStore({ indexedDB: createFakeIndexedDBFactory() });
    await cache2.putEntity('work', 'W-2', { id: 'W-2', text_content: 'Same' });
    await cache2.putEntity('work-notes-state', 'W-2', {
        work_id: 'W-2', research_note_revision: 4, private_note_revision: 0,
    });
    const offline2 = createPrksOfflineRuntime({
        store: cache2, window: null,
        prksRequest: async () => { throw new Error('no reads'); },
    });
    const gen = offline2.currentDomainGeneration('concepts');
    assert.equal(await offline2.reconcileWorkNote({
        work_id: 'W-2', note_kind: RESEARCH_KIND, code: 'ACKNOWLEDGED',
        changed: false, server_revision: 4, value_omitted: true,
    }, { operation: RESEARCH, entity_id: 'W-2', payload: { text: 'Same' } }), true);
    assert.equal(offline2.currentDomainGeneration('concepts'), gen,
        'an unchanged Research write does not drop derived projections');
}

async function main() {
    acknowledgedBase();
    handlerContract();
    await coalescingFor(RESEARCH, 'Research');
    await coalescingFor(PRIVATE, 'Private');
    await scopesDoNotBlockEachOther();
    await byteLimits();
    await mutationTestAtoBtoA();
    await reconciliation();
    console.log('All ' + checks + ' Work note checks passed');
}

main().catch(error => { console.error(error); process.exit(1); });
