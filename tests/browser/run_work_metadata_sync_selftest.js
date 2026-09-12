'use strict';
const strict = require('assert/strict');
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); },
    { get: (_target, key) => (...args) => { checks += 1; return strict[key](...args); } });
const { createFakeIndexedDBFactory } = require('./lib/fake_indexeddb.js');
const { createPrksLocalStore } = require('../../frontend/js/local-store.js');
const { createPrksOfflineStore } = require('../../frontend/js/offline-store.js');
const { createPrksOfflineRuntime } = require('../../frontend/js/offline-runtime.js');
require('../../frontend/js/work-tag-state.js');
require('../../frontend/js/work-metadata-state.js');
require('../../frontend/js/sync-runtime.js');

let sequence = 0;
const uuid = () => '00000000-0000-4000-8000-' + (++sequence).toString(16).padStart(12, '0');
const tick = () => new Promise(resolve => setTimeout(resolve, 1));
async function settle() { for (let i = 0; i < 5; i++) await tick(); }

function base(overrides) {
    const fields = {};
    globalThis.PRKS_SYNCED_WORK_FIELDS.forEach(field => { fields[field] = { value: '', revision: 0 }; });
    Object.assign(fields, overrides || {});
    return fields;
}
function ack(field, value, revision, changed) {
    return { code: 'ACKNOWLEDGED', work_id: 'W-M', field, value,
        server_revision: revision, changed: changed !== false };
}
function metaRuntime(store, request, reconcile) {
    return globalThis.createPrksSyncRuntime({ store, online: () => true, request,
        handlers: { SET_WORK_METADATA_FIELD: Object.assign({}, globalThis.prksWorkMetadataSyncHandler, { reconcile }) } });
}
const fieldsOf = rows => rows.map(r => r.payload.field + '=' + r.payload.value).sort();

/* ---- one effective never-sent operation per FIELD ---- */
async function coalescing() {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    const observed = base({ doi: { value: 'A', revision: 4 } });

    const first = await store.saveWorkMetadataFields('W-M', { doi: 'B' }, observed);
    assert.equal(first.length, 1);
    assert.equal(first[0].base_revision, 4, 'the operation carries the observed base');
    assert.deepEqual(first[0].payload, { field: 'doi', value: 'B' });

    const second = await store.saveWorkMetadataFields('W-M', { doi: 'C' }, observed);
    let rows = await store.listOperations();
    assert.equal(rows.length, 1, 'A -> B -> C is one pending operation');
    assert.equal(rows[0].payload.value, 'C');
    assert.notEqual(second[0].op_id, first[0].op_id, 'coalescing writes a new envelope, never edits one');
    assert.equal(rows[0].base_revision, 4, 'still measured against the same observed base');

    await store.saveWorkMetadataFields('W-M', { doi: 'C' }, observed);
    rows = await store.listOperations();
    assert.equal(rows.length, 1, 'repeating the same intent adds nothing');
    assert.equal(rows[0].op_id, second[0].op_id, 'and does not churn the operation id');

    await store.saveWorkMetadataFields('W-M', { doi: 'A' }, observed);
    assert.deepEqual(await store.listOperations(), [], 'editing back to the base leaves no intent');

    // A field already equal to the base produces nothing at all.
    assert.deepEqual(await store.saveWorkMetadataFields('W-M', { isbn: '' }, observed), []);
    assert.deepEqual(await store.listOperations(), []);
}

/* ---- fields are independent, including when one is busy ---- */
async function independence() {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    const observed = base({ doi: { value: 'D0', revision: 2 }, isbn: { value: 'I0', revision: 5 } });

    await store.saveWorkMetadataFields('W-M', { doi: 'D1', isbn: 'I1', journal: 'J1' }, observed);
    let rows = await store.listOperations();
    assert.equal(rows.length, 3, 'one operation per changed field');
    assert.deepEqual(fieldsOf(rows), ['doi=D1', 'isbn=I1', 'journal=J1']);
    assert.deepEqual(rows.map(r => r.base_revision).sort(), [0, 2, 5],
        'each field carries its OWN base revision');

    /* A field whose operation may already have reached the server is busy --
     * but only that field. The point of the milestone is that the others stay
     * editable while one is stuck. */
    const doi = rows.find(r => r.payload.field === 'doi');
    await store.claimOperation(doi.op_id);
    await store.updateOperationSyncState(doi.op_id, { status: 'pending' });
    await assert.rejects(store.saveWorkMetadataFields('W-M', { doi: 'D2' }, observed),
        err => err.prksLocalStoreCode === 'scope_busy');
    await store.saveWorkMetadataFields('W-M', { journal: 'J2' }, observed);
    rows = await store.listOperations();
    assert.equal(rows.find(r => r.payload.field === 'journal').payload.value, 'J2',
        'a busy DOI does not freeze the rest of the form');
    assert.equal(rows.find(r => r.payload.field === 'doi').payload.value, 'D1',
        'and the possibly-sent operation is left exactly as it was');

    // A conflicted field is equally the user's to resolve, not to overwrite.
    const isbn = rows.find(r => r.payload.field === 'isbn');
    await store.updateOperationSyncState(isbn.op_id, { status: 'conflict', server_result: {
        code: 'REVISION_CONFLICT', current_revision: 6, current_value: 'I-server', requested_value: 'I1' } });
    await assert.rejects(store.saveWorkMetadataFields('W-M', { isbn: 'I2' }, observed),
        err => err.prksLocalStoreCode === 'scope_busy');
}

/* ---- one Save, one transaction ---- */
async function atomicity() {
    const factory = createFakeIndexedDBFactory();
    const store = createPrksLocalStore({ indexedDB: factory, uuid });
    const observed = base();
    await store.saveWorkMetadataFields('W-M', { doi: 'keep' }, observed);
    assert.equal((await store.listOperations()).length, 1);

    /* The user pressed one button. Durably storing two of their three edits
     * and then reporting "Saved locally" is a lie that only surfaces later. */
    const db = factory.__databases.get('prks-local-v1');
    db._stores.get('operations').failCommit = true;
    await assert.rejects(
        store.saveWorkMetadataFields('W-M', { journal: 'J', volume: 'V', issue: 'I' }, observed),
        err => err.prksLocalStoreCode === 'write_failed');
    db._stores.get('operations').failCommit = false;

    const rows = await store.listOperations();
    assert.equal(rows.length, 1, 'a failed multi-field save commits NOTHING');
    assert.equal(rows[0].payload.field, 'doi', 'and leaves the earlier save intact');

    // Invalid observed state is refused before anything is written.
    await assert.rejects(store.saveWorkMetadataFields('W-M', { doi: 'x' }, { doi: { value: 'a' } }),
        err => err.prksLocalStoreCode === 'invalid_base');
    await assert.rejects(store.saveWorkMetadataFields('W-M', { doi: 7 }, observed),
        err => err.prksLocalStoreCode === 'invalid_base');
    assert.equal((await store.listOperations()).length, 1);
}

/* ---- overlay and dirty-field diffing ---- */
async function overlay() {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    const work = { id: 'W-M', title: 'Paper', doi: 'server-doi', isbn: 'server-isbn' };
    const observed = base({ doi: { value: 'server-doi', revision: 1 }, isbn: { value: 'server-isbn', revision: 1 } });

    assert.deepEqual(globalThis.prksEffectiveWorkMetadata(work, []), work);
    await store.saveWorkMetadataFields('W-M', { doi: 'local-doi' }, observed);
    let effective = globalThis.prksEffectiveWorkMetadata(work, await store.listOperations());
    assert.equal(effective.doi, 'local-doi');
    assert.equal(effective.isbn, 'server-isbn', 'untouched fields keep the acknowledged value');
    assert.equal(effective.title, 'Paper');
    assert.equal(work.doi, 'server-doi', 'the cached record is never rewritten');

    // Another Work's pending edits never leak across.
    const other = globalThis.prksEffectiveWorkMetadata({ id: 'W-OTHER', doi: 'x' }, await store.listOperations());
    assert.equal(other.doi, 'x');

    // Acknowledged rows belong to the server, not the overlay.
    const rows = await store.listOperations();
    await store.updateOperationSyncState(rows[0].op_id, { status: 'acknowledged' });
    assert.equal(globalThis.prksEffectiveWorkMetadata(work, await store.listOperations()).doi, 'server-doi');

    /* Only genuinely dirty fields become operations: the form submits all
     * seven every time, and seven operations per Save would be seven chances
     * to conflict over nothing. */
    const draft = { doi: 'server-doi', isbn: 'changed', journal: '', volume: '', issue: '', pages: '', edition: '' };
    assert.deepEqual(globalThis.prksDirtyWorkMetadataFields(draft, { fields: observed }), { isbn: 'changed' });
    // Empty string and a missing server value are one logical value.
    assert.deepEqual(globalThis.prksDirtyWorkMetadataFields({ journal: '' }, { fields: observed }), {});

    /* The diff is against what the form is SHOWING, not against the server.
     * Measuring against the server base would leave a pending operation in
     * place the moment a user typed their way back to the server's value. */
    const pendingOps = [{ operation: 'SET_WORK_METADATA_FIELD', status: 'pending',
        payload: { field: 'doi', value: 'local-doi' } }];
    assert.deepEqual(globalThis.prksDirtyWorkMetadataFields({ doi: 'local-doi' }, { fields: observed }, pendingOps), {},
        'an untouched pending value is not a new edit');
    assert.deepEqual(globalThis.prksDirtyWorkMetadataFields({ doi: 'server-doi' }, { fields: observed }, pendingOps),
        { doi: 'server-doi' }, 'editing back to the server value IS a change to record');
    // ...and recording it removes the pending operation rather than adding one.
    const cancelStore = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    await cancelStore.saveWorkMetadataFields('W-M', { doi: 'local-doi' }, observed);
    assert.equal((await cancelStore.listOperations()).length, 1);
    await cancelStore.saveWorkMetadataFields('W-M', { doi: 'server-doi' }, observed);
    assert.deepEqual(await cancelStore.listOperations(), []);
    assert.equal(globalThis.prksCanonicalWorkField(null), '');
    assert.equal(globalThis.prksCanonicalWorkField(' kept '), ' kept ', 'whitespace is never invented away');
}

/* ---- acknowledgement reconciles both cached records, then retires ---- */
async function acknowledgement() {
    const factory = createFakeIndexedDBFactory();
    const store = createPrksLocalStore({ indexedDB: factory, uuid });
    const cache = createPrksOfflineStore({ indexedDB: factory });
    await cache.putEntity('work', 'W-M', { id: 'W-M', title: 'Paper', doi: 'old', isbn: 'keep' });
    await cache.putEntity('work-metadata-state', 'W-M', { work_id: 'W-M', fields: base({ doi: { value: 'old', revision: 3 } }) });
    const offline = createPrksOfflineRuntime({ store: cache, window: null,
        prksRequest: async () => { throw new Error('no reads in this scenario'); } });

    const observed = base({ doi: { value: 'old', revision: 3 } });
    const op = await store.saveWorkMetadataFields('W-M', { doi: 'new' }, observed);
    let cacheWorks = false;
    const sent = [];
    const runtime = metaRuntime(store, async (path, init) => {
        sent.push(JSON.parse(init.body).op_id);
        return { ok: true, status: 200, json: async () => ack('doi', 'new', 4) };
    }, async result => cacheWorks && offline.reconcileWorkField(result));

    /* The server committed but the cache write failed: the durable operation
     * must survive with its identity intact so the replay can finish the job. */
    await runtime.wake(); await settle();
    let row = await store.getOperation(op[0].op_id);
    assert.equal(row.status, 'pending');
    assert.equal(row.op_id, op[0].op_id, 'the same op_id is retained');
    assert.equal((await cache.getEntity('work', 'W-M')).value.doi, 'old');

    cacheWorks = true;
    await store.updateOperationSyncState(op[0].op_id, { attempt_count: 0 });
    await runtime.wake(); await settle(); runtime.stop();
    assert.deepEqual(sent, [op[0].op_id, op[0].op_id], 'the replay reuses the operation id');
    assert.equal(await store.getOperation(op[0].op_id), null, 'a reconciled edit retires locally');
    assert.equal((await cache.getEntity('work', 'W-M')).value.doi, 'new');
    assert.equal((await cache.getEntity('work', 'W-M')).value.isbn, 'keep');
    const state = (await cache.getEntity('work-metadata-state', 'W-M')).value.fields;
    assert.deepEqual(state.doi, { value: 'new', revision: 4 });
    assert.deepEqual(state.isbn, { value: '', revision: 0 }, 'other fields are untouched');

    // A superseded acknowledgement must not move the field backwards.
    assert.equal(await offline.reconcileWorkField(ack('doi', 'stale', 2)), true);
    assert.equal((await cache.getEntity('work', 'W-M')).value.doi, 'new');

    // No cached base: nothing to reconcile, and nothing invented either.
    await cache.clearAll();
    assert.equal(await offline.reconcileWorkField(ack('doi', 'new', 5)), true);
    assert.equal(await cache.getEntity('work', 'W-M'), null);
}

/* ---- a read that began before the ACK cannot publish over it ---- */
async function staleReads() {
    const factory = createFakeIndexedDBFactory();
    const cache = createPrksOfflineStore({ indexedDB: factory });
    const stale = { id: 'W-M', title: 'Paper', doi: 'old' };
    await cache.putEntity('work', 'W-M', { id: 'W-M', title: 'Paper', doi: 'old' });
    await cache.putEntity('work-metadata-state', 'W-M', { work_id: 'W-M', fields: base({ doi: { value: 'old', revision: 1 } }) });
    let release = null;
    const inFlight = new Promise(resolve => { release = resolve; });
    const offline = createPrksOfflineRuntime({ store: cache, window: null, prksRequest: async path => {
        await inFlight;
        return { ok: true, status: 200, json: async () => (path.indexOf('metadata-state') === -1
            ? stale : { work_id: 'W-M', fields: base({ doi: { value: 'old', revision: 1 } }) }) };
    } });

    const detail = offline.readThroughEntity('work', 'W-M', '/api/works/W-M');
    const projection = offline.readThroughEntity('work-metadata-state', 'W-M', '/api/works/W-M/metadata-state',
        { validate: v => globalThis.prksIsWorkMetadataStateShape(v, 'W-M') });
    await settle();

    assert.equal(await offline.reconcileWorkField(ack('doi', 'new', 2)), true);
    release();
    await detail; await projection;
    await settle();
    assert.equal((await cache.getEntity('work', 'W-M')).value.doi, 'new',
        'a stale Work response cannot beat the acknowledgement');
    assert.deepEqual((await cache.getEntity('work-metadata-state', 'W-M')).value.fields.doi,
        { value: 'new', revision: 2 }, 'nor can a stale metadata-state response');
}

/* ---- the conflict belongs to the field ---- */
async function conflicts() {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    const observed = base({ doi: { value: 'D0', revision: 1 } });
    const saved = await store.saveWorkMetadataFields('W-M', { doi: 'mine', journal: 'J' }, observed);
    const doi = saved.find(r => r.payload.field === 'doi');

    const runtime = metaRuntime(store, async (path, init) => {
        const body = JSON.parse(init.body);
        if (body.payload.field === 'journal') {
            return { ok: true, status: 200, json: async () => ack('journal', 'J', 1) };
        }
        return { ok: false, status: 409, json: async () => ({
            work_id: 'W-M', field: 'doi', code: 'REVISION_CONFLICT',
            current_revision: 7, current_value: 'theirs', requested_value: 'mine' }) };
    }, async () => true);
    await runtime.wake(); await settle(); runtime.stop();

    const rows = await store.listOperations();
    assert.equal(rows.length, 1, 'the field that synchronized is gone; the conflicted one remains');
    assert.equal(rows[0].payload.field, 'doi');
    assert.equal(rows[0].status, 'conflict');
    assert.deepEqual(rows[0].server_result, { code: 'REVISION_CONFLICT', current_revision: 7,
        current_value: 'theirs', requested_value: 'mine' });

    // Apply my value creates a NEW operation against the reported revision.
    const replacement = await store.resolveConflict(doi.op_id, true);
    assert.notEqual(replacement.op_id, doi.op_id, 'the conflicted id is never reused');
    assert.equal(replacement.base_revision, 7);
    assert.deepEqual(replacement.payload, { field: 'doi', value: 'mine' });
    assert.equal(await store.getOperation(doi.op_id), null);

    // Use server / discard removes the intent without creating anything.
    await store.updateOperationSyncState(replacement.op_id, { status: 'conflict', server_result: {
        code: 'REVISION_CONFLICT', current_revision: 8, current_value: 'theirs', requested_value: 'mine' } });
    assert.equal(await store.resolveConflict(replacement.op_id, false), null);
    assert.deepEqual(await store.listOperations(), []);
}

/* ---- each family answers only its own results ---- */
async function isolation() {
    const handler = globalThis.prksWorkMetadataSyncHandler;
    const op = { operation: 'SET_WORK_METADATA_FIELD', entity_id: 'W-M', payload: { field: 'doi', value: 'mine' } };
    assert.equal(handler.isResult(ack('doi', 'mine', 3), op), true);
    assert.equal(handler.isResult({ code: 'ENTITY_NOT_FOUND', work_id: 'W-M', field: 'doi' }, op), true);
    assert.equal(handler.isResult({ work_id: 'W-M', field: 'doi', code: 'REVISION_CONFLICT',
        current_revision: 2, current_value: 'theirs', requested_value: 'mine' }, op), true);
    for (const bad of [
        null,
        ack('isbn', 'mine', 3),
        { ...ack('doi', 'other', 3) },
        { code: 'ACKNOWLEDGED', work_id: 'W-OTHER', field: 'doi', value: 'mine', server_revision: 3, changed: true },
        { code: 'ACKNOWLEDGED', work_id: 'W-M', field: 'doi', value: 'mine', server_revision: -1, changed: true },
        { work_id: 'W-M', field: 'doi', code: 'REVISION_CONFLICT', current_revision: 2,
            current_value: 'theirs', requested_value: 'not mine' },
        // Other families' outcomes are meaningless here.
        { code: 'TAG_MERGED', work_id: 'W-M', field: 'doi', target_tag_id: 'T-1' },
        { code: 'ACKNOWLEDGED', work_id: 'W-M', field: 'doi', changed: true, effective_opened_at: 'x' },
    ]) {
        assert.equal(handler.isResult(bad, op), false, JSON.stringify(bad));
    }
    /* Every terminal outcome is the user's to resolve: they typed this value
     * deliberately, so discarding it silently would lose real work. */
    assert.deepEqual(handler.terminal({ code: 'ENTITY_NOT_FOUND' }), { conflict: { code: 'ENTITY_NOT_FOUND' } });
    assert.deepEqual(handler.terminal({ code: 'FUTURE_REVISION', current_revision: 9,
        current_value: 's', requested_value: 'm' }),
        { conflict: { code: 'FUTURE_REVISION', current_revision: 9, current_value: 's', requested_value: 'm' } });
    // And the Work-Tag family rejects a metadata acknowledgement.
    assert.equal(globalThis.prksWorkTagSyncHandler.isResult(ack('doi', 'mine', 3),
        { operation: 'ADD_WORK_TAG', entity_id: 'W-M', payload: { tag_id: 'T-1' } }), false);

    // Shape validation of the cached projection.
    assert.equal(globalThis.prksIsWorkMetadataStateShape({ work_id: 'W-M', fields: base() }, 'W-M'), true);
    for (const bad of [
        null, { work_id: 'W-M' }, { work_id: 'W-M', fields: [] },
        { work_id: 'W-OTHER', fields: base() },
        { work_id: 'W-M', fields: base({ doi: { value: 'x', revision: -1 } }) },
        { work_id: 'W-M', fields: base({ doi: { value: 7, revision: 0 } }) },
        { work_id: 'W-M', fields: Object.assign(base(), { title: { value: 'x', revision: 0 } }) },
    ]) {
        assert.equal(globalThis.prksIsWorkMetadataStateShape(bad, 'W-M'), false, JSON.stringify(bad));
    }
    const missing = base();
    delete missing.doi;
    assert.equal(globalThis.prksIsWorkMetadataStateShape({ work_id: 'W-M', fields: missing }, 'W-M'), false);
}

async function main() {
    await coalescing();
    await independence();
    await atomicity();
    await overlay();
    await acknowledgement();
    await staleReads();
    await conflicts();
    await isolation();
    console.log('All ' + checks + ' Work metadata checks passed');
}

main().then(() => process.exit(0))
    .catch(error => { console.error(error); process.exit(1); });
