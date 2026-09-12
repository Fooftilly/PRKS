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
    globalThis.PRKS_SYNCED_WORK_FIELDS.forEach(field => {
        // A byte-limited field contributes its revision only; its value lives
        // on the Work record rather than in this projection.
        fields[field] = globalThis.PRKS_BYTE_LIMITED_WORK_FIELDS.has(field)
            ? { revision: 0 } : { value: '', revision: 0 };
    });
    Object.assign(fields, overrides || {});
    return fields;
}
/** The store needs a value for every field; the projection omits some. */
function resolved(fields) {
    const out = {};
    Object.entries(fields).forEach(([field, entry]) => {
        out[field] = { revision: entry.revision, value: entry.value == null ? '' : entry.value };
    });
    return out;
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
    const observed = resolved(base({ doi: { value: 'A', revision: 4 } }));

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
    const observed = resolved(base({ doi: { value: 'D0', revision: 2 }, isbn: { value: 'I0', revision: 5 } }));

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
    const observed = resolved(base());
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
    const observed = resolved(base({ doi: { value: 'server-doi', revision: 1 }, isbn: { value: 'server-isbn', revision: 1 } }));

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
     * nine every time, and nine operations per Save would be nine chances
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

    const observed = resolved(base({ doi: { value: 'old', revision: 3 } }));
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
    const observed = resolved(base({ doi: { value: 'D0', revision: 1 } }));
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

/* ---- PHASE H/I: a pending value reaches ANOTHER cached projection ----
 *
 * Publisher is carried by `recently-added:index` because that tab filters
 * locally over it. The acknowledged snapshot must stay untouched while the
 * effective rows the user sees and searches carry the pending value.
 */
async function crossProjection() {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    globalThis.prksSync = { store };
    const acknowledged = [
        { id: 'W-1', title: 'Alpha', publisher: 'Elsevier', created_at: '2026-09-01' },
        { id: 'W-2', title: 'Beta', publisher: 'Springer', created_at: '2026-08-01' },
    ];
    const frozen = JSON.parse(JSON.stringify(acknowledged));

    await globalThis.prksRefreshPendingWorkMetadata();
    assert.deepEqual(globalThis.prksEffectiveWorkMetadataRows(acknowledged, ['publisher']), acknowledged,
        'with nothing pending the acknowledged rows are returned as they are');

    const observed = resolved(base({ publisher: { value: 'Elsevier', revision: 2 } }));
    await store.saveWorkMetadataFields('W-1', { publisher: 'Springer' }, observed);
    const before = globalThis.prksPendingWorkMetadataGeneration();
    await globalThis.prksRefreshPendingWorkMetadata();
    assert(globalThis.prksPendingWorkMetadataGeneration() > before,
        'the overlay generation moves so memoized renders can be refused');

    const effective = globalThis.prksEffectiveWorkMetadataRows(acknowledged, ['publisher']);
    assert.equal(effective[0].publisher, 'Springer', 'the effective row carries the pending value');
    assert.equal(effective[1].publisher, 'Springer', 'an untouched row is untouched');
    assert.equal(effective[0].title, 'Alpha', 'the rest of the row survives the overlay');
    assert.deepEqual(acknowledged, frozen, 'the acknowledged snapshot is never mutated');
    assert.notEqual(effective[0], acknowledged[0], 'an edited row is a copy, not the original');
    assert.equal(effective[1], acknowledged[1], 'an unedited row is not needlessly copied');

    // Only the fields the caller asked for are overlaid.
    await store.saveWorkMetadataFields('W-1', { doi: '10.1/x' }, observed);
    await globalThis.prksRefreshPendingWorkMetadata();
    const publisherOnly = globalThis.prksEffectiveWorkMetadataRows(acknowledged, ['publisher'])[0];
    assert.equal(publisherOnly.publisher, 'Springer');
    assert.equal(publisherOnly.doi, undefined, 'a projection gets only the fields it carries');

    /* PHASE I: the filter is the point. A pending Publisher must match, and
     * the value it replaced must stop matching, before anything synchronizes. */
    const matches = (row, query) => [row.title, row.publisher]
        .some(v => v != null && String(v).toLowerCase().includes(query.toLowerCase()));
    assert.equal(matches(effective[0], 'Springer'), true, 'the pending publisher is searchable');
    assert.equal(matches(effective[0], 'Elsevier'), false, 'the replaced publisher stops matching');
    assert.equal(matches(acknowledged[0], 'Elsevier'), true, 'the cached row still says Elsevier');

    // Acknowledged operations belong to the server, not the overlay.
    const rows = await store.listOperations();
    for (const op of rows) await store.updateOperationSyncState(op.op_id, { status: 'acknowledged' });
    await globalThis.prksRefreshPendingWorkMetadata();
    assert.deepEqual(globalThis.prksEffectiveWorkMetadataRows(acknowledged, ['publisher']), acknowledged);
    delete globalThis.prksSync;
}

/* ---- a full Abstract acknowledgement, through the coordinator ---- */
async function abstractAcknowledgement() {
    const factory = createFakeIndexedDBFactory();
    const store = createPrksLocalStore({ indexedDB: factory, uuid });
    const cache = createPrksOfflineStore({ indexedDB: factory });
    globalThis.prksSync = { store };
    await cache.putEntity('work', 'W-X', { id: 'W-X', title: 'Paper', abstract: 'old' });
    await cache.putEntity('work-metadata-state', 'W-X', { work_id: 'W-X', fields: base() });
    await cache.putList('works-browse:index', [{ id: 'W-X', title: 'Paper', abstract_excerpt: 'old' }], '');
    const offline = createPrksOfflineRuntime({ store: cache, window: null,
        prksRequest: async () => { throw new Error('no reads in this scenario'); } });

    // Deliberately across the old 64 KiB durable ceiling.
    const big = 'A'.repeat(120 * 1024);
    const op = await store.saveWorkMetadataFields('W-X', { abstract: big }, resolved(base()));
    assert.equal(op.length, 1, 'a 120 KiB Abstract reaches the durable queue');

    const runtime = globalThis.createPrksSyncRuntime({ store, online: () => true,
        request: async () => ({ ok: true, status: 200, json: async () => ({
            code: 'ACKNOWLEDGED', work_id: 'W-X', field: 'abstract',
            server_revision: 1, changed: true, value_omitted: true }) }),
        // The real handler's validation and reconstruction, pointed at this
        // scenario's cache rather than the production singleton.
        handlers: { SET_WORK_METADATA_FIELD: Object.assign({}, globalThis.prksWorkMetadataSyncHandler, {
            reconcile: (data, op) => offline.reconcileWorkField(
                globalThis.prksEffectiveMetadataAck(data, op)),
        }) } });
    await runtime.wake(); await settle(); runtime.stop();

    assert.equal(await store.getOperation(op[0].op_id), null, 'the operation retires');
    assert.equal((await cache.getEntity('work', 'W-X')).value.abstract, big,
        'the cached Work holds the full Abstract, reconstructed from the operation');
    const fields = (await cache.getEntity('work-metadata-state', 'W-X')).value.fields;
    assert.deepEqual(fields.abstract, { revision: 1 });
    assert.equal(Object.prototype.hasOwnProperty.call(fields.abstract, 'value'), false,
        'the projection never acquires a value key');
    assert.equal(globalThis.prksIsWorkMetadataStateShape(
        (await cache.getEntity('work-metadata-state', 'W-X')).value, 'W-X'), true,
        'and the projection still validates after reconciliation');
    assert.equal((await cache.getList('works-browse:index')).value[0].abstract_excerpt,
        globalThis.prksAbstractExcerpt(big), 'the derived excerpt was reconciled too');
    delete globalThis.prksSync;
}

/* ---- PHASE L/M/N: acknowledgement reaches the projection too ---- */
async function projectionReconciliation() {
    const factory = createFakeIndexedDBFactory();
    const store = createPrksLocalStore({ indexedDB: factory, uuid });
    const cache = createPrksOfflineStore({ indexedDB: factory });
    await cache.putEntity('work', 'W-M', { id: 'W-M', title: 'Paper', publisher: 'Elsevier' });
    await cache.putEntity('work-metadata-state', 'W-M',
        { work_id: 'W-M', fields: base({ publisher: { value: 'Elsevier', revision: 2 } }) });
    await cache.putList('recently-added:index', [
        { id: 'W-M', title: 'Paper', publisher: 'Elsevier' },
        { id: 'W-OTHER', title: 'Other', publisher: 'Wiley' },
    ], '');
    const offline = createPrksOfflineRuntime({ store: cache, window: null,
        prksRequest: async () => { throw new Error('no reads in this scenario'); } });

    const acknowledgement = { code: 'ACKNOWLEDGED', work_id: 'W-M', field: 'publisher',
        value: 'Springer', server_revision: 3, changed: true };
    assert.equal(await offline.reconcileWorkField(acknowledgement), true);
    assert.equal((await cache.getEntity('work', 'W-M')).value.publisher, 'Springer');
    assert.deepEqual((await cache.getEntity('work-metadata-state', 'W-M')).value.fields.publisher,
        { value: 'Springer', revision: 3 });
    const list = (await cache.getList('recently-added:index')).value;
    assert.equal(list[0].publisher, 'Springer', 'the acknowledged projection row is patched in place');
    assert.equal(list[1].publisher, 'Wiley', 'other rows are untouched');
    assert.equal(list.length, 2, 'the snapshot is patched, never dropped');

    /* A detail-only field must not drag an unrelated projection into its
     * reconciliation -- Location is the control case for exactly that. */
    const locationAck = { code: 'ACKNOWLEDGED', work_id: 'W-M', field: 'location',
        value: 'Amsterdam', server_revision: 1, changed: true };
    const generationBefore = offline.currentDomainGeneration('recently-added');
    assert.equal(await offline.reconcileWorkField(locationAck), true);
    assert.equal(offline.currentDomainGeneration('recently-added'), generationBefore,
        'Location leaves the Recently Added domain completely alone');
    assert.equal((await cache.getEntity('work', 'W-M')).value.location, 'Amsterdam');

    // A Work the projection does not carry is not a failure, and adds nothing.
    assert.equal(await offline.reconcileWorkField(
        { ...acknowledgement, work_id: 'W-ABSENT', server_revision: 1 }), true);
    assert.equal((await cache.getList('recently-added:index')).value.length, 2);

    // No cached projection at all: nothing to reconcile, nothing invented.
    await cache.deleteList('recently-added:index');
    assert.equal(await offline.reconcileWorkField({ ...acknowledgement, server_revision: 4 }), true);
    assert.equal(await cache.getList('recently-added:index'), null);
}

/* A GET that began before the acknowledgement cannot publish over it. */
async function staleProjectionRead() {
    const factory = createFakeIndexedDBFactory();
    const cache = createPrksOfflineStore({ indexedDB: factory });
    const stale = [{ id: 'W-M', title: 'Paper', publisher: 'Elsevier' }];
    await cache.putEntity('work', 'W-M', { id: 'W-M', title: 'Paper', publisher: 'Elsevier' });
    await cache.putList('recently-added:index', stale, '');
    let release = null;
    const inFlight = new Promise(resolve => { release = resolve; });
    const offline = createPrksOfflineRuntime({ store: cache, window: null, prksRequest: async () => {
        await inFlight;
        return { ok: true, status: 200, json: async () => stale };
    } });

    const reading = offline.readThroughList('recently-added:index', '/api/recently-added',
        { domain: 'recently-added', validate: rows => Array.isArray(rows) });
    await settle();
    assert.equal(await offline.reconcileWorkField({ code: 'ACKNOWLEDGED', work_id: 'W-M',
        field: 'publisher', value: 'Springer', server_revision: 3, changed: true }), true);
    release();
    await reading;
    await settle();
    assert.equal((await cache.getList('recently-added:index')).value[0].publisher, 'Springer',
        'a stale /api/recently-added response cannot beat the acknowledgement');
}

/* ---- hydration: "not read yet" must never read as "nothing pending" ----
 *
 * The synchronous overlay exists because Recently Added filters on every
 * keystroke. Its cost is that an un-hydrated empty map is indistinguishable
 * from a genuinely empty one, so anything that must be CORRECT rather than
 * merely fast waits for the one shared hydration.
 */
async function hydration() {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    const observed = resolved(base({ publisher: { value: 'Elsevier', revision: 2 } }));
    await store.saveWorkMetadataFields('W-H', { publisher: 'Springer' }, observed);
    const durableRows = await store.listOperations();

    // A store whose read is held open by the test, so the race is constructed
    // rather than waited for.
    let release = null;
    let reads = 0;
    const gate = new Promise(resolve => { release = resolve; });
    globalThis.prksSync = { store: { listOperations: async () => { reads += 1; await gate; return durableRows; } } };
    delete require.cache[require.resolve('../../frontend/js/work-metadata-state.js')];
    require('../../frontend/js/work-metadata-state.js');

    const work = { id: 'W-H', title: 'Paper', publisher: 'Elsevier' };
    assert.equal(globalThis.prksPendingWorkMetadataState(), 'unread', 'nothing has read the queue yet');

    // The mount begins hydration, exactly as the editor's paint does.
    const mounting = globalThis.prksRefreshPendingWorkMetadata();
    // The user reaches Edit metadata before that read has landed.
    let settled = false;
    const waiting = globalThis.prksEnsurePendingWorkMetadata().then(() => { settled = true; });
    await new Promise(resolve => setTimeout(resolve, 5));
    assert.equal(settled, false, 'hydration does not resolve before the read lands');
    assert.equal(reads, 1, 'the waiter joins the read in flight; it never starts a second one');
    assert.equal(globalThis.prksPendingWorkMetadataState(), 'loading');

    release();
    await mounting; await waiting;
    assert.equal(settled, true);
    assert.equal(reads, 1, 'still one read for the one event');
    assert.equal(globalThis.prksPendingWorkMetadataState(), 'ready');
    // ...and only now does the synchronous overlay speak for the durable queue.
    assert.equal(globalThis.prksEffectiveWorkSync(work).publisher, 'Springer');
    assert.equal(work.publisher, 'Elsevier', 'the acknowledged Work is untouched');

    // Once hydrated, waiting is free: no await, no read.
    const after = globalThis.prksEnsurePendingWorkMetadata();
    await after;
    assert.equal(reads, 1);

    /* A FAILED read is not an empty queue. It settles -- nothing is served by
     * hanging the UI on a read that already failed -- but it must not license
     * anyone to act as though nothing is pending. */
    delete require.cache[require.resolve('../../frontend/js/work-metadata-state.js')];
    let failing = true;
    globalThis.prksSync = { store: { listOperations: async () => {
        if (failing) throw new Error('IndexedDB is unavailable.');
        return durableRows;
    } } };
    require('../../frontend/js/work-metadata-state.js');
    assert.equal(globalThis.prksPendingWorkMetadataState(), 'unread');
    await globalThis.prksEnsurePendingWorkMetadata();
    assert.equal(globalThis.prksPendingWorkMetadataState(), 'unavailable',
        'a failed read is unavailable, never ready');
    assert.equal(globalThis.prksPendingWorkMetadataSettled(), true, 'but waiters are released');

    /* A retry recovers without a reload. */
    failing = false;
    await globalThis.prksRefreshPendingWorkMetadata();
    assert.equal(globalThis.prksPendingWorkMetadataState(), 'ready');
    assert.equal(globalThis.prksEffectiveWorkSync(work).publisher, 'Springer',
        'and the pending value appears once the queue is readable again');

    /* A later failure must not erase what is already known to be pending. */
    failing = true;
    await globalThis.prksRefreshPendingWorkMetadata();
    assert.equal(globalThis.prksPendingWorkMetadataState(), 'unavailable');
    assert.equal(globalThis.prksEffectiveWorkSync(work).publisher, 'Springer',
        'the last known pending map survives a failed refresh');

    // No sync runtime at all is equally "could not read", not "nothing there".
    delete require.cache[require.resolve('../../frontend/js/work-metadata-state.js')];
    delete globalThis.prksSync;
    require('../../frontend/js/work-metadata-state.js');
    await globalThis.prksEnsurePendingWorkMetadata();
    assert.equal(globalThis.prksPendingWorkMetadataState(), 'unavailable');

    // Restore the module the rest of the suite shares.
    delete require.cache[require.resolve('../../frontend/js/work-metadata-state.js')];
    require('../../frontend/js/work-metadata-state.js');
}

/* ---- Abstract: large scalar + DERIVED projection ---- */
async function abstracts() {
    const excerpt = globalThis.prksAbstractExcerpt;
    // The canonical rule: first 100 Unicode CODE POINTS, matching SQLite.
    assert.equal(Array.from(excerpt('x'.repeat(150))).length, 100);
    assert.equal(Array.from(excerpt('\u{1F9EA}'.repeat(150))).length, 100,
        'astral characters count once, exactly as SQLite counts them');
    assert.equal(excerpt('short'), 'short');
    assert.equal(excerpt(null), '');
    const boundary = 'y'.repeat(99) + '\u{1F9EA}' + 'tail';
    assert.equal(Array.from(excerpt(boundary)).length, 100);
    const last = excerpt(boundary).charCodeAt(excerpt(boundary).length - 1);
    assert(!(last >= 0xD800 && last <= 0xDBFF), 'never ends mid surrogate pair');

    // The byte limit is measured in bytes, not characters.
    const limit = globalThis.PRKS_MAX_ABSTRACT_UTF8_BYTES;
    assert.equal(limit, 1024 * 1024);
    assert.equal(globalThis.prksWorkFieldLimitError('abstract', 'x'.repeat(limit)), null);
    assert(globalThis.prksWorkFieldLimitError('abstract', 'x'.repeat(limit + 1)));
    const multibyte = '\u65e5'.repeat(Math.floor(limit / 3) + 10);
    assert(multibyte.length < limit, 'under the limit by character count');
    assert(globalThis.prksWorkFieldUtf8Bytes(multibyte) > limit, '...but over it in bytes');
    assert(globalThis.prksWorkFieldLimitError('abstract', multibyte),
        'a limit enforced in characters would not exist for the users most likely to hit it');
    assert.equal(globalThis.prksWorkFieldLimitError('doi', 'x'.repeat(limit)), null,
        'only byte-limited fields are checked here');

    // Pending Abstract overlays the Work, and DERIVES the browse excerpt.
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    globalThis.prksSync = { store };
    const observed = resolved(base());
    const pendingText = '\u{1F9EA}'.repeat(150);
    await store.saveWorkMetadataFields('W-A', { abstract: pendingText }, observed);
    await globalThis.prksRefreshPendingWorkMetadata();

    const work = { id: 'W-A', title: 'Paper', abstract: 'server abstract' };
    assert.equal(globalThis.prksEffectiveWorkSync(work).abstract, pendingText);
    assert.equal(work.abstract, 'server abstract', 'the acknowledged Work is untouched');

    const catalog = [{ id: 'W-A', title: 'Paper', abstract_excerpt: 'server abstract' },
        { id: 'W-B', title: 'Other', abstract_excerpt: 'untouched' }];
    const frozen = JSON.parse(JSON.stringify(catalog));
    const effective = globalThis.prksEffectiveProjectionRows(catalog, 'works-browse');
    assert.equal(effective[0].abstract_excerpt, excerpt(pendingText),
        'the projection receives the DERIVED excerpt, not the Abstract');
    assert.equal(Array.from(effective[0].abstract_excerpt).length, 100);
    assert.equal(effective[0].abstract, undefined, 'and never the full text');
    assert.equal(effective[1].abstract_excerpt, 'untouched');
    assert.deepEqual(catalog, frozen, 'the acknowledged catalog is never mutated');

    // Recently Added must not acquire an abstract column from this.
    assert.equal(globalThis.prksEffectiveProjectionRows(catalog, 'recently-added')[0].abstract_excerpt,
        'server abstract', 'a field reaches only the projections that carry it');

    // The acknowledged patch uses the same derivation, so nothing visibly
    // changes at acknowledgement.
    assert.deepEqual(globalThis.prksProjectionFieldPatch('works-browse', 'abstract', pendingText),
        { abstract_excerpt: excerpt(pendingText) });
    assert.equal(globalThis.prksProjectionFieldPatch('works-browse', 'doi', 'x'), null);
    /* PHASE 15: deriving an excerpt must not expand the whole Abstract. A
     * megabyte turned into a million-entry array on every Progress render
     * would make one pending operation everyone else's problem. */
    const huge = 'x'.repeat(1024 * 1024);
    const started = Date.now();
    for (let i = 0; i < 200; i++) excerpt(huge);
    assert(Date.now() - started < 500, 'excerpt derivation stays bounded for a 1 MiB Abstract');
    assert.equal(excerpt(huge), 'x'.repeat(100));
    /* ---- the compact Abstract acknowledgement ----
     *
     * The server does not echo the Abstract back, so the ledger never becomes
     * a permanent second copy of the text. The client reconstructs the value
     * from its own immutable operation payload -- which the server has just
     * confirmed it applied -- rather than fetching it again.
     */
    const abstractOp = { operation: 'SET_WORK_METADATA_FIELD', entity_id: 'W-A',
        payload: { field: 'abstract', value: pendingText } };
    const compact = { code: 'ACKNOWLEDGED', work_id: 'W-A', field: 'abstract',
        server_revision: 4, changed: true, value_omitted: true };
    assert.equal(globalThis.prksWorkMetadataSyncHandler.isResult(compact, abstractOp), true);
    assert.equal(globalThis.prksEffectiveMetadataAck(compact, abstractOp).value, pendingText,
        'the effective value comes from the immutable operation');
    assert.equal(compact.value, undefined, 'and the server result is left alone');

    /* An omission must be DECLARED. A merely missing value is indistinguishable
     * from a malformed response, and reconstructing from that would invent a
     * value the server never confirmed. */
    const ambiguous = { code: 'ACKNOWLEDGED', work_id: 'W-A', field: 'abstract',
        server_revision: 4, changed: true };
    assert.equal(globalThis.prksWorkMetadataSyncHandler.isResult(ambiguous, abstractOp), false);
    assert.equal(globalThis.prksEffectiveMetadataAck(ambiguous, abstractOp), ambiguous,
        'an undeclared omission is not reconstructed');
    // ...and a byte-limited ACK must not carry a value either way.
    assert.equal(globalThis.prksWorkMetadataSyncHandler.isResult(
        Object.assign({ value: pendingText }, compact), abstractOp), false);
    // A small field keeps the full-value contract.
    const doiOp = { operation: 'SET_WORK_METADATA_FIELD', entity_id: 'W-A',
        payload: { field: 'doi', value: '10.1/x' } };
    assert.equal(globalThis.prksWorkMetadataSyncHandler.isResult(
        { code: 'ACKNOWLEDGED', work_id: 'W-A', field: 'doi', value: '10.1/x',
            server_revision: 1, changed: true }, doiOp), true);
    assert.equal(globalThis.prksWorkMetadataSyncHandler.isResult(
        { code: 'ACKNOWLEDGED', work_id: 'W-A', field: 'doi', server_revision: 1,
            changed: true, value_omitted: true }, doiOp), false,
        'only byte-limited fields may omit their value');

    /* ---- the metadata-state projection keeps its shape ---- */
    assert.deepEqual(globalThis.prksMetadataStateAckPatch('abstract', 7, pendingText),
        { revision: 7 }, 'a byte-limited field carries its revision only');
    assert.deepEqual(globalThis.prksMetadataStateAckPatch('doi', 7, '10.1/x'),
        { value: '10.1/x', revision: 7 });
    delete globalThis.prksSync;
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
    await crossProjection();
    await projectionReconciliation();
    await staleProjectionRead();
    await hydration();
    await abstracts();
    await abstractAcknowledgement();
    console.log('All ' + checks + ' Work metadata checks passed');
}

main().then(() => process.exit(0))
    .catch(error => { console.error(error); process.exit(1); });
