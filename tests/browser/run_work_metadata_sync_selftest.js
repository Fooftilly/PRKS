'use strict';
const strict = require('assert/strict');
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); },
    { get: (_target, key) => (...args) => { checks += 1; return strict[key](...args); } });
const { createFakeIndexedDBFactory } = require('./lib/fake_indexeddb.js');
const { createPrksLocalStore } = require('../../frontend/js/local-store.js');
const { createPrksOfflineStore } = require('../../frontend/js/offline-store.js');
const { createPrksOfflineRuntime } = require('../../frontend/js/offline-runtime.js');
require('../../frontend/js/doc-types.js');
require('../../frontend/js/work-tag-state.js');
require('../../frontend/js/work-metadata-state.js');
require('../../frontend/js/sync-runtime.js');

/* The REAL credit helper, not a restatement of it. `work-cards.js` is browser
 * script code rather than a module, so it is evaluated in the global scope the
 * way a <script> tag would; its top-level function declarations then become
 * globals. Re-implementing the precedence rule here would prove only that the
 * test and the test agree. */
globalThis.window = globalThis;   // the file ends by exporting onto `window`
(0, eval)(require('fs').readFileSync(
    require('path').join(__dirname, '../../frontend/js/components/work-cards.js'), 'utf8'));
const creditLine = globalThis.prksWorkCardCreditLine;

/* The REAL browse-row shape validators. `thumb_page` is the first synchronized
 * field whose read models are TYPE-checked, so "does a pending value keep the
 * cached row valid?" has to be asked of the actual validator rather than of a
 * restatement of it. app.js is a large browser script; only the shape helpers
 * are needed, so they are extracted and evaluated on their own. */
const appSource = require('fs').readFileSync(
    require('path').join(__dirname, '../../frontend/js/app.js'), 'utf8');
for (const name of ['prksHasUsableRowId', 'prksIsOptionalString',
    'prksIsOptionalNonNegativeInteger', 'prksIsBrowseCardRowShape',
    'prksIsWorksBrowseRowShape', 'prksIsRecentRowShape',
    'prksIsRecentlyAddedRowShape', 'prksIsBrowseFolderId']) {
    const at = appSource.indexOf('function ' + name + '(');
    if (at === -1) throw new Error('shape helper not found in app.js: ' + name);
    // To the closing brace in column 0 -- these are top-level declarations.
    const end = appSource.indexOf('\n}', at);
    (0, eval)(appSource.slice(at, end + 2));
}
/* The card asks this which KIND of Work it is rendering, and a video takes a
 * different thumbnail branch entirely -- no page, just the provider's image. */
{
    const api = require('fs').readFileSync(
        require('path').join(__dirname, '../../frontend/js/api.js'), 'utf8');
    const at = api.indexOf('function prksInferWorkSourceKind(');
    (0, eval)(api.slice(at, api.indexOf('\n}', at) + 2));
}
const ROW_SHAPES = {
    'works-browse': globalThis.prksIsWorksBrowseRowShape,
    'recent': globalThis.prksIsRecentRowShape,
    'recently-added': globalThis.prksIsRecentlyAddedRowShape,
};

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

/* ---- source_url: provenance, and only where it IS provenance ---- */
function provenanceSourceUrl() {
    const op = { operation: 'SET_WORK_METADATA_FIELD', entity_id: 'W-P',
        payload: { field: 'source_url', value: 'https://example.com/a' } };
    /* The server refuses a field-scoped write to a VIDEO Work's URL. That is
     * terminal and well formed -- retrying would be refused identically
     * forever -- and it is not a conflict: there are not two values to choose
     * between. */
    assert.equal(globalThis.prksWorkMetadataSyncHandler.isResult(
        { code: 'WRONG_OPERATION_FOR_SOURCE', work_id: 'W-P', field: 'source_url' }, op), true);
    assert.equal(globalThis.prksWorkMetadataSyncHandler.isResult(
        { code: 'SOMETHING_ELSE', work_id: 'W-P', field: 'source_url' }, op), false);

    // It is byte-limited, so it gets the compact acknowledgement like the rest.
    assert.equal(globalThis.prksWorkMetadataSyncHandler.isResult(
        { code: 'ACKNOWLEDGED', work_id: 'W-P', field: 'source_url', server_revision: 1,
          changed: true, value_omitted: true }, op), true);
    assert.deepEqual(globalThis.prksMetadataStateAckPatch('source_url', 3, 'https://x'),
        { revision: 3 });

    // And it overlays the ordinary Work-shaped surfaces.
    globalThis.prksSetPendingWorkMetadata([{ operation: 'SET_WORK_METADATA_FIELD',
        entity_type: 'work', entity_id: 'W-P', status: 'pending',
        payload: { field: 'source_url', value: 'https://example.com/new' } }]);
    assert.equal(globalThis.prksEffectiveWorkSync({ id: 'W-P', source_url: 'https://old' })
        .source_url, 'https://example.com/new');
    /* A PDF stays a PDF: an explicit `source_kind` outranks the URL, so
     * editing provenance cannot turn the card or the viewer into a video. */
    const pdf = globalThis.prksEffectiveWorkSync(
        { id: 'W-P', source_kind: 'pdf', file_path: '/api/pdfs/x.pdf', source_url: 'https://old' });
    assert.equal(globalThis.prksInferWorkSourceKind(pdf), 'pdf',
        'provenance never changes what kind of Work this is');
    globalThis.prksSetPendingWorkMetadata([]);
}

/* ---- title: a Work value held by REFERENCE inside other entity families ----
 *
 * Every earlier field lived on Work-shaped rows. A Title also lives inside a
 * Concept's backlinks, an Argument's sources and mentions, and a Graph node's
 * `label` -- entities that are not Works, keyed by a foreign column, under
 * property names that disagree with each other. One registry describes all of
 * them so no component learns what a durable operation is.
 */
function titleReferenceOverlays() {
    globalThis.prksSetPendingWorkMetadata([{ operation: 'SET_WORK_METADATA_FIELD',
        entity_type: 'work', entity_id: 'W-T', status: 'pending',
        payload: { field: 'title', value: 'New Title' } }]);

    // The ordinary Work-shaped surfaces.
    assert.equal(globalThis.prksEffectiveWorkSync({ id: 'W-T', title: 'Old' }).title, 'New Title');
    for (const projection of ['works-browse', 'recent', 'recently-added']) {
        assert.equal(globalThis.prksEffectiveProjectionRows(
            [{ id: 'W-T', title: 'Old' }], projection)[0].title, 'New Title', projection);
    }
    assert.equal(globalThis.prksEffectiveWorkSummaries(
        [{ id: 'W-T', title: 'Old' }])[0].title, 'New Title');

    // A Concept's backlinks: `mentions[].title`.
    const concept = { id: 'C1', name: 'Idea', mentions: [
        { work_id: 'W-T', title: 'Old', occurrences: 3 },
        { work_id: 'W-OTHER', title: 'Other' },
    ] };
    const conceptFrozen = JSON.stringify(concept);
    const effConcept = globalThis.prksEffectiveWorkReferences('concept', concept);
    assert.equal(effConcept.mentions[0].title, 'New Title');
    assert.equal(effConcept.mentions[0].occurrences, 3, 'other columns survive');
    assert.equal(effConcept.mentions[1].title, 'Other', 'and only the edited Work');
    assert.equal(JSON.stringify(concept), conceptFrozen, 'the cached Concept is never mutated');

    /* An Argument holds TWO collections that disagree about the column name:
     * `sources[].work_title` and `mentions[].title`. */
    const argument = { id: 'A1',
        sources: [{ work_id: 'W-T', work_title: 'Old', pages: '1-2' }],
        mentions: [{ work_id: 'W-T', title: 'Old' }] };
    const argumentFrozen = JSON.stringify(argument);
    const effArgument = globalThis.prksEffectiveWorkReferences('argument', argument);
    assert.equal(effArgument.sources[0].work_title, 'New Title', 'sources use work_title');
    assert.equal(effArgument.sources[0].pages, '1-2');
    assert.equal(effArgument.mentions[0].title, 'New Title', 'mentions use title');
    assert.equal(JSON.stringify(argument), argumentFrozen, 'the cached Argument is untouched');

    // A Graph node's `label`.
    const snapshot = { nodes: [
        { id: 'work:W-T', record_id: 'W-T', type: 'work', label: 'Old', doc_type: 'article' },
        { id: 'concept:C1', record_id: 'W-T', type: 'concept', label: 'Idea' },
    ], edges: [] };
    for (const kind of ['research-graph-core', 'research-graph-people']) {
        const eff = globalThis.prksEffectiveWorkReferences(kind, snapshot);
        assert.equal(eff.nodes[0].label, 'New Title', kind + ' relabels the Work node');
        assert.equal(eff.nodes[0].doc_type, 'article', 'without touching its other columns');
        assert.equal(eff.nodes[1].label, 'Idea',
            'a CONCEPT node sharing a record id is not a Work node');
    }

    // An entity with no reference collections at all is returned unchanged.
    const bare = { id: 'C2', name: 'Empty' };
    assert.equal(globalThis.prksEffectiveWorkReferences('concept', bare), bare);

    globalThis.prksSetPendingWorkMetadata([]);
}

/* ---- doc_type: Types membership, and a Work value inside the Graph ---- */
function docTypeMembershipAndGraph() {
    const pend = value => globalThis.prksSetPendingWorkMetadata([{
        operation: 'SET_WORK_METADATA_FIELD', entity_type: 'work', entity_id: 'W-D',
        status: 'pending', payload: { field: 'doc_type', value } }]);

    // Only canonical values become operations; the control offers no others.
    assert.equal(globalThis.prksWorkFieldToCanonical('doc_type', 'book'), 'book');
    for (const bad of ['BOOK', 'bogus', '', '  ']) {
        assert.equal(globalThis.prksWorkFieldToCanonical('doc_type', bad), null, bad);
    }

    /* MEMBERSHIP. Types groups on the value in the row, exactly as Progress
     * groups on Status, so the Work has to leave one group and join another
     * before anything is sent. */
    pend('book');
    const catalog = [
        { id: 'W-D', doc_type: 'article' },
        { id: 'W-STAY', doc_type: 'article' },
        { id: 'W-BOOK', doc_type: 'book' },
    ];
    const frozen = JSON.stringify(catalog);
    const group = type => globalThis.prksEffectiveProjectionRows(catalog, 'works-browse')
        .filter(w => w.doc_type === type).map(w => w.id);
    assert.deepEqual(group('article'), ['W-STAY'], 'the edited Work left Articles');
    assert.deepEqual(group('book'), ['W-D', 'W-BOOK'], 'and joined Books');
    assert.equal(JSON.stringify(catalog), frozen, 'the snapshot is untouched');
    for (const projection of ['recent', 'recently-added']) {
        assert.equal(globalThis.prksEffectiveProjectionRows(
            [{ id: 'W-D', doc_type: 'article' }], projection)[0].doc_type, 'book', projection);
    }
    assert.equal(globalThis.prksEffectiveWorkSummaries(
        [{ id: 'W-D', doc_type: 'article' }])[0].doc_type, 'book', 'embedded summaries too');

    /* THE GRAPH. A cached snapshot holds Work metadata on nodes identified by
     * `record_id`, inside an entity that is not a Work. */
    const snapshot = { nodes: [
        { id: 'work:W-D', record_id: 'W-D', type: 'work', doc_type: 'article', label: 'Paper' },
        { id: 'work:W-OTHER', record_id: 'W-OTHER', type: 'work', doc_type: 'article' },
        { id: 'person:P1', record_id: 'W-D', type: 'person', doc_type: 'article' },
    ], edges: [] };
    const snapFrozen = JSON.stringify(snapshot);
    for (const kind of ['research-graph-core', 'research-graph-people']) {
        const eff = globalThis.prksEffectiveWorkReferences(kind, snapshot);
        assert.equal(eff.nodes[0].doc_type, 'book', kind + ' patches the Work node');
        assert.equal(eff.nodes[1].doc_type, 'article', 'and only the edited Work');
        assert.equal(eff.nodes[2].doc_type, 'article',
            'a PERSON node sharing a record id is not a Work node');
        assert.equal(eff.nodes[0].label, 'Paper', 'untouched columns survive');
        assert.equal(JSON.stringify(snapshot), snapFrozen, 'the cached snapshot is never mutated');
    }
    assert.deepEqual(globalThis.PRKS_WORK_REFERENCE_KINDS,
        ['research-graph-core', 'research-graph-people', 'concept', 'argument']);
    // A field no reference shape carries touches none of them.
    assert.deepEqual(globalThis.prksWorkReferencePatches('research-graph-core', 'doi', 'x'), []);
    assert.deepEqual(globalThis.prksWorkReferencePatches('concept', 'doc_type', 'book'), [],
        'a Concept backlink shows a Title, never a doc type');

    globalThis.prksSetPendingWorkMetadata([]);
}

/* ---- thumb_page: the wire value is not the entity value ----
 *
 * Every synchronized field before this one was a string in the editor, on the
 * wire and in the column, so those could be the same value without anyone
 * having to say so. Here they genuinely differ: the wire carries "3", the
 * column is INTEGER NULL, and the read models are TYPE-checked. A wire string
 * reaching a cached row does not merely look odd -- it makes the row fail its
 * own shape validator and be discarded as corrupt.
 */
function thumbPageCodec() {
    const canonical = v => globalThis.prksWorkFieldToCanonical('thumb_page', v);
    const entity = v => globalThis.prksWorkFieldToEntity('thumb_page', v);

    // Editor -> wire. "" is the one spelling of "no explicit page".
    for (const [draft, wire] of [['', ''], ['1', '1'], ['3', '3'], ['003', '3'],
        [' 3 ', '3'], ['  ', ''], [null, '']]) {
        assert.equal(canonical(draft), wire, JSON.stringify(draft));
    }
    /* Anything else is REFUSED, never read as "clear the field". That
     * conflation is the bug an uninterpretable Published Date used to have,
     * and the one the ordinary PATCH had for this very field: it silently
     * turned page 0 into NULL. */
    for (const bad of ['0', '-1', '1.5', 'abc', '3abc', '+3', '１', '1e3']) {
        assert.equal(canonical(bad), null, bad);
    }

    // Wire -> entity, and back for display.
    assert.equal(entity('3'), 3);
    assert.equal(entity(''), null);
    assert.equal(entity('0'), null);
    assert.equal(globalThis.prksWorkFieldToDisplay('thumb_page', 3), '3');
    assert.equal(globalThis.prksWorkFieldToDisplay('thumb_page', null), '');
    // A field without a codec is the same string everywhere.
    assert.equal(globalThis.prksWorkFieldToEntity('doi', '10.1/x'), '10.1/x');

    /* Dirty comparison happens on CANONICAL WIRE values, so a draft that
     * merely SPELLS the stored page differently is not an edit. */
    const observed = { fields: base({ thumb_page: { value: '3', revision: 2 } }) };
    for (const same of ['3', '003', ' 3 ']) {
        assert.deepEqual(globalThis.prksDirtyWorkMetadataFields({ thumb_page: same }, observed),
            {}, same + ' is the page already stored');
    }
    assert.deepEqual(globalThis.prksDirtyWorkMetadataFields({ thumb_page: '4' }, observed),
        { thumb_page: '4' });
    assert.deepEqual(globalThis.prksDirtyWorkMetadataFields({ thumb_page: '' }, observed),
        { thumb_page: '' }, 'clearing is a real change');
    const cleared = { fields: base({ thumb_page: { value: '', revision: 2 } }) };
    assert.deepEqual(globalThis.prksDirtyWorkMetadataFields({ thumb_page: '' }, cleared), {},
        'and an untouched empty page is not dirty');
}

/* THE MANDATORY REGRESSION: a pending value must leave every cached row
 * satisfying its own shape validator. These are the app's real validators, not
 * a restatement -- a string reaching `thumb_page` makes the row invalid and
 * the whole cached catalog is then discarded as corrupt. */
function thumbPageKeepsRowsValid() {
    globalThis.prksSetPendingWorkMetadata([{ operation: 'SET_WORK_METADATA_FIELD',
        entity_type: 'work', entity_id: 'W-T', status: 'pending',
        payload: { field: 'thumb_page', value: '3' } }]);

    const row = () => ({
        id: 'W-T', title: 'Paper', status: 'Planned', doc_type: 'book',
        file_path: '/api/pdfs/x.pdf', source_kind: 'pdf', source_url: null,
        thumb_url: null, thumb_page: 9, author_text: 'A', year: '1999',
        published_date: '1999-01-01', abstract_excerpt: '', publisher: 'P',
        last_opened_at: '2026-01-01T00:00:00Z', created_at: '2026-01-01T00:00:00Z',
        folder_id: null,
    });
    for (const [projection, isShape] of Object.entries(ROW_SHAPES)) {
        const acknowledged = [row()];
        assert.equal(isShape(acknowledged[0]), true, projection + ' fixture is valid to begin with');
        const effective = globalThis.prksEffectiveProjectionRows(acknowledged, projection)[0];
        assert.equal(effective.thumb_page, 3, projection + ' carries the pending page');
        assert.equal(typeof effective.thumb_page, 'number', projection + ' as a NUMBER');
        assert.equal(isShape(effective), true,
            projection + ' row is still valid with a pending page');
        assert.equal(acknowledged[0].thumb_page, 9, 'the snapshot is untouched');
    }

    // A pending CLEAR must be null, not "" -- and null is still valid.
    globalThis.prksSetPendingWorkMetadata([{ operation: 'SET_WORK_METADATA_FIELD',
        entity_type: 'work', entity_id: 'W-T', status: 'pending',
        payload: { field: 'thumb_page', value: '' } }]);
    for (const [projection, isShape] of Object.entries(ROW_SHAPES)) {
        const effective = globalThis.prksEffectiveProjectionRows([row()], projection)[0];
        assert.equal(effective.thumb_page, null, projection + ' clears to null');
        assert.equal(isShape(effective), true, projection + ' row is still valid when cleared');
    }
    // And the string the overlay must never produce would indeed be rejected,
    // so the assertions above are not passing for a trivial reason.
    const poisoned = Object.assign(row(), { thumb_page: '3' });
    assert.equal(globalThis.prksIsWorksBrowseRowShape(poisoned), false,
        'a wire string in a cached row IS invalid -- that is what this prevents');

    // Embedded Folder/Person/Playlist summaries get the same treatment.
    const summary = globalThis.prksEffectiveWorkSummaries([{ id: 'W-T', thumb_page: 9 }])[0];
    assert.equal(summary.thumb_page, null);
    assert.equal(globalThis.prksEffectiveWorkSync({ id: 'W-T', thumb_page: 9 }).thumb_page, null);

    globalThis.prksSetPendingWorkMetadata([]);
}

/* ---- every effective-Work helper produces ENTITY values ----
 *
 * `prksEffectiveWorkMetadata()` takes an explicit operation list rather than
 * the shared pending map -- the metadata editor holds its own -- and that
 * second path copied wire values straight into a Work-like object. The result
 * was `thumb_page === "5"` from one helper and `5` from every other, which is
 * the entity contract broken by the helper the EDITOR uses.
 */
function everyEffectiveHelperIsTyped() {
    const op = (field, value) => ({ operation: 'SET_WORK_METADATA_FIELD',
        entity_type: 'work', entity_id: 'W-E', status: 'pending',
        payload: { field, value } });
    const work = { id: 'W-E', thumb_page: 2, status: 'Planned',
        author_text: 'Old', year: '1999' };

    // The editor's helper, given operations directly.
    const paged = globalThis.prksEffectiveWorkMetadata(work, [op('thumb_page', '5')]);
    assert.equal(paged.thumb_page, 5);
    assert.equal(typeof paged.thumb_page, 'number', 'a NUMBER, not the wire string');
    const cleared = globalThis.prksEffectiveWorkMetadata(
        { id: 'W-E', thumb_page: 5 }, [op('thumb_page', '')]);
    assert.equal(cleared.thumb_page, null, 'a pending clear is null, never ""');

    // Fields that need no conversion are untouched by the codec layer.
    const others = globalThis.prksEffectiveWorkMetadata(work,
        [op('status', 'Completed'), op('author_text', 'Jane'), op('year', '2020')]);
    assert.equal(others.status, 'Completed');
    assert.equal(others.author_text, 'Jane');
    assert.equal(others.year, '2020');
    assert.equal(others.thumb_page, 2, 'and an untouched field keeps its entity value');
    assert.deepEqual(work, { id: 'W-E', thumb_page: 2, status: 'Planned',
        author_text: 'Old', year: '1999' }, 'the acknowledged Work is never mutated');

    /* EVERY constructor agrees. This is the assertion that would have caught
     * the leak: one helper disagreeing with the others is the defect, not the
     * value any single one returns. */
    globalThis.prksSetPendingWorkMetadata([op('thumb_page', '5')]);
    const viaMap = globalThis.prksEffectiveWorkSync({ id: 'W-E', thumb_page: 2 });
    const viaList = globalThis.prksEffectiveWorkMetadata({ id: 'W-E', thumb_page: 2 },
        [op('thumb_page', '5')]);
    const viaRows = globalThis.prksEffectiveWorksSync([{ id: 'W-E', thumb_page: 2 }])[0];
    const viaSummaries = globalThis.prksEffectiveWorkSummaries([{ id: 'W-E', thumb_page: 2 }])[0];
    const viaProjection = globalThis.prksEffectiveProjectionRows(
        [{ id: 'W-E', thumb_page: 2 }], 'works-browse')[0];
    for (const [label, row] of [['sync', viaMap], ['operation list', viaList],
        ['rows', viaRows], ['summaries', viaSummaries], ['projection', viaProjection]]) {
        assert.equal(row.thumb_page, 5, label + ' value');
        assert.equal(typeof row.thumb_page, 'number', label + ' type');
    }
    globalThis.prksSetPendingWorkMetadata([]);
}

/* ---- metadata-state stores CANONICAL WIRE values ----
 *
 * That projection is synchronization bookkeeping: its `value` is what a base
 * revision was observed against, in the representation the protocol uses. So
 * "003" is as wrong there as "abc" -- neither is something the server emits --
 * and an integer is wrong too, because that is the ENTITY representation and
 * belongs on the Work record.
 *
 * Validation asks whether the stored value is valid, never whether it could be
 * repaired: silently canonicalizing corrupt acknowledged state would hide the
 * corruption and leave the observed base disagreeing with the server.
 */
function metadataStateWireValidation() {
    const state = value => ({ work_id: 'W-S',
        fields: base({ thumb_page: { value, revision: 4 } }) });
    const valid = v => globalThis.prksIsWorkMetadataStateShape(state(v), 'W-S');

    for (const good of ['', '1', '3', '15', '1000']) {
        assert.equal(valid(good), true, JSON.stringify(good) + ' is canonical');
    }
    for (const bad of ['003', '0', '-1', 'abc', '1.5', ' 3 ', '+3', '01']) {
        assert.equal(valid(bad), false, JSON.stringify(bad) + ' is not canonical wire');
    }
    // The ENTITY representation is not the wire representation.
    assert.equal(valid(3), false, 'an integer belongs on the Work record, not here');
    assert.equal(valid(null), false);

    // A field whose codec has no opinion accepts any string, as always.
    const doiState = value => ({ work_id: 'W-S',
        fields: base({ doi: { value, revision: 4 } }) });
    assert.equal(globalThis.prksIsWorkMetadataStateShape(doiState('003'), 'W-S'), true,
        'an ordinary scalar has no canonical form to violate');
    assert.equal(globalThis.prksIsWorkMetadataStateShape(doiState(3), 'W-S'), false,
        'though it still has to be a string');

    // The rule lives with the FIELD, not in the shape validator.
    assert.equal(globalThis.prksIsCanonicalWorkFieldWire('thumb_page', '003'), false);
    assert.equal(globalThis.prksIsCanonicalWorkFieldWire('doi', '003'), true);
}

/* ---- thumbnail resource identity comes from the EFFECTIVE Work ----
 *
 * A URL with no page means "whatever page the server currently has stored",
 * which is not an identity the client can reason about and is wrong the moment
 * an edit is pending: with page 5 stored and a clear pending, the effective
 * value is null -- page 1 -- but a page-less URL still renders page 5 until
 * the server hears about it. So the page is always stated.
 */
function thumbnailResourceIdentity() {
    const pending = value => globalThis.prksSetPendingWorkMetadata([{
        operation: 'SET_WORK_METADATA_FIELD', entity_type: 'work', entity_id: 'W-P',
        status: 'pending', payload: { field: 'thumb_page', value } }]);
    const card = (work, options) => globalThis.prksWorkCardHtml(
        globalThis.prksEffectiveWorkSync(work), options || {});
    const pdf = { id: 'W-P', title: 'Paper', file_path: '/api/pdfs/x.pdf', thumb_page: 5 };
    const srcOf = html => {
        const m = /data-prks-thumb-src="([^"]*)"/.exec(html);
        return m ? m[1] : '';
    };

    // Acknowledged: the stored page, stated.
    globalThis.prksSetPendingWorkMetadata([]);
    assert.equal(srcOf(card(pdf)), '/api/works/W-P/thumbnail?page=5');
    // Acknowledged null is page 1 EXPLICITLY, not an absent page.
    assert.equal(srcOf(card({ id: 'W-P', file_path: '/api/pdfs/x.pdf', thumb_page: null })),
        '/api/works/W-P/thumbnail?page=1');

    // Pending page: the new page, before the server knows anything.
    pending('3');
    assert.equal(srcOf(card(pdf)), '/api/works/W-P/thumbnail?page=3');

    /* THE REGRESSION: a pending CLEAR while the server still stores page 5.
     * A page-less URL would render 5; the effective value is null, so the
     * resource is page 1 -- the same page the server will choose once the
     * clear is acknowledged. */
    pending('');
    assert.equal(srcOf(card(pdf)), '/api/works/W-P/thumbnail?page=1',
        'a pending clear requests page 1, not the page still stored');

    /* OFFLINE SUPPRESSION IS ABSOLUTE and happens BEFORE any URL exists. A
     * cached card must not ask PRKS for bytes it cannot obtain, and a pending
     * metadata edit is not a reason to start. */
    pending('4');
    const suppressed = card(pdf, { suppressThumbnail: true });
    assert.equal(suppressed.indexOf('thumbnail'), -1,
        'no thumbnail URL at all is emitted for a cached card');
    assert.equal(suppressed.indexOf('page=4'), -1);
    pending('');
    const clearedOffline = card(pdf, { suppressThumbnail: true });
    assert.equal(clearedOffline.indexOf('page=1'), -1,
        'and a pending clear does not emit one either');
    assert.equal(clearedOffline.indexOf('work-card__thumb--empty') !== -1, true,
        'the layout is unchanged -- only the source is removed');

    // A non-PDF Work has no page resource at all.
    globalThis.prksSetPendingWorkMetadata([]);
    assert.equal(srcOf(card({ id: 'W-V', source_kind: 'video', source_url: 'https://x/v',
        thumb_url: 'https://img/1.jpg' })), 'https://img/1.jpg');

    /* The request coordinator classifies by PATHNAME, so the added query
     * cannot change how a thumbnail request is treated. */
    assert.equal(new URL('/api/works/W-P/thumbnail?page=3', 'http://x').pathname,
        '/api/works/W-P/thumbnail');
}

/* ---- author_text: a stored value that is not necessarily the displayed one ----
 *
 * PRKS composes a Work's credit as linked Author(s) -> author_text -> linked
 * Editor. Synchronization changes the FIELD; the existing credit helper decides
 * what the user sees. These cases pin that the two compose in that order and
 * that neither learns the other's job.
 */
function authorTextComposition() {
    const pending = value => globalThis.prksSetPendingWorkMetadata([{
        operation: 'SET_WORK_METADATA_FIELD', entity_type: 'work', entity_id: 'W-A',
        status: 'pending', payload: { field: 'author_text', value } }]);
    const credit = row => creditLine(globalThis.prksEffectiveWorkSync(row));

    // 1. No linked Author: the pending text IS the credit, immediately.
    pending('New Author');
    assert.equal(credit({ id: 'W-A', author_text: 'Old Author' }), 'Author: New Author');

    /* 2. A linked Author OUTRANKS it. The field still changes -- the editor
     * shows the new text -- but the card must not start crediting someone the
     * user did not link. This is the central invariant of the milestone. */
    const linked = { id: 'W-A', author_text: 'Old Author', linked_authors: 'Jane Smith' };
    assert.equal(credit(linked), 'Author: Jane Smith', 'a linked Author masks pending text');
    assert.equal(globalThis.prksEffectiveWorkSync(linked).author_text, 'New Author',
        'while the FIELD itself is the pending value');
    assert.equal(credit({ id: 'W-A', author_text: 'Old', primary_author: 'Solo Person' }),
        'Author: Solo Person', 'primary_author outranks it too');

    // 3. Clearing it reveals the linked Editor.
    pending('');
    const withEditor = { id: 'W-A', author_text: 'Text Author', primary_editor: 'Editor Person' };
    assert.equal(credit(withEditor), 'Editor: Editor Person',
        'a cleared author_text falls through to the Editor');
    assert.equal(creditLine(withEditor), 'Author: Text Author',
        'and without the overlay the acknowledged text still wins -- so the '
        + 'overlay is doing the work, not the helper');

    // 4. Cleared with nothing to fall back to is no credit at all.
    assert.equal(credit({ id: 'W-A', author_text: 'Text Author' }), '');

    // 5. Whitespace-only is empty, exactly as the editor has always sent it.
    assert.equal(globalThis.prksWorkFieldToCanonical('author_text', '   '), '');
    assert.equal(globalThis.prksWorkFieldToCanonical('author_text', '  Jane  '), 'Jane');

    // 6. The three browse catalogs and embedded summaries carry the field.
    pending('New Author');
    for (const projection of ['works-browse', 'recent', 'recently-added']) {
        const rows = [{ id: 'W-A', author_text: 'Old Author', linked_authors: 'Jane Smith' }];
        const frozen = JSON.parse(JSON.stringify(rows));
        const out = globalThis.prksEffectiveProjectionRows(rows, projection);
        assert.equal(out[0].author_text, 'New Author', projection);
        assert.equal(creditLine(out[0]), 'Author: Jane Smith',
            projection + ' still credits the linked Author');
        assert.deepEqual(rows, frozen, projection + ' snapshot untouched');
    }
    const summaries = [{ id: 'W-A', author_text: 'Old Author' }];
    assert.equal(globalThis.prksEffectiveWorkSummaries(summaries)[0].author_text, 'New Author');
    assert.equal(globalThis.prksEffectiveWorksSync(
        [{ id: 'W-A', author_text: 'Old Author' }])[0].author_text, 'New Author',
        'server search results are overlaid too');

    /* 7. A local filter indexes the RAW field, which is deliberately not the
     * same as the displayed credit: a Work whose card credits a linked Author
     * can still match on its hidden textual author. Preserved, not redesigned. */
    const row = globalThis.prksEffectiveProjectionRows(
        [{ id: 'W-A', author_text: 'Old Author', linked_authors: 'Jane Smith' }],
        'recently-added')[0];
    assert.equal(row.author_text, 'New Author', 'the filter sees the pending raw value');
    assert.equal(creditLine(row), 'Author: Jane Smith', 'though the card shows the linked one');

    globalThis.prksSetPendingWorkMetadata([]);
}

/* ---- Status: a pending value that changes GROUP MEMBERSHIP ---- */
function statusMembership() {
    const ops = [{ operation: 'SET_WORK_METADATA_FIELD', entity_type: 'work',
        entity_id: 'W-S', status: 'pending',
        payload: { field: 'status', value: 'Completed' } }];
    globalThis.prksSetPendingWorkMetadata(ops);

    // 1. The Work itself.
    const work = { id: 'W-S', title: 'Paper', status: 'Planned', year: '1999' };
    const frozenWork = JSON.parse(JSON.stringify(work));
    assert.equal(globalThis.prksEffectiveWorkSync(work).status, 'Completed');
    assert.equal(globalThis.prksEffectiveWorkSync(work).year, '1999', 'other fields survive');
    assert.deepEqual(work, frozenWork, 'the acknowledged Work is never mutated');

    // 2. All three browse catalogs -- Progress reads the first of them.
    for (const projection of ['works-browse', 'recent', 'recently-added']) {
        const rows = [{ id: 'W-S', status: 'Planned' }, { id: 'W-OTHER', status: 'Planned' }];
        const frozen = JSON.parse(JSON.stringify(rows));
        const out = globalThis.prksEffectiveProjectionRows(rows, projection);
        assert.equal(out[0].status, 'Completed', projection + ' carries the pending Status');
        assert.equal(out[1].status, 'Planned', 'and only for the Work that was edited');
        assert.deepEqual(rows, frozen, projection + ' snapshot is untouched');
    }

    /* 3. GROUP MEMBERSHIP. This is what makes Status different from every
     * earlier synchronized field: Progress does not merely render the value,
     * it selects on it. The filter below is exactly what
     * `renderProgressByStatus` applies to the rows it is handed, so a Work
     * must LEAVE the group the server put it in and JOIN the pending one. */
    const catalog = [
        { id: 'W-S', status: 'Planned' },
        { id: 'W-STAY', status: 'Planned' },
        { id: 'W-DONE', status: 'Completed' },
    ];
    const groupOf = status => globalThis.prksEffectiveProjectionRows(catalog, 'works-browse')
        .filter(w => w.status === status).map(w => w.id);
    assert.deepEqual(groupOf('Planned'), ['W-STAY'], 'the edited Work left its old group');
    assert.deepEqual(groupOf('Completed'), ['W-S', 'W-DONE'], 'and joined the pending one');

    // The reverse direction has to work identically.
    globalThis.prksSetPendingWorkMetadata([{ operation: 'SET_WORK_METADATA_FIELD',
        entity_type: 'work', entity_id: 'W-DONE', status: 'pending',
        payload: { field: 'status', value: 'Planned' } }]);
    assert.deepEqual(groupOf('Planned'), ['W-S', 'W-STAY', 'W-DONE']);
    assert.deepEqual(groupOf('Completed'), []);

    // 4. Embedded Work summaries -- Folder, Person, Playlist all render a badge.
    globalThis.prksSetPendingWorkMetadata(ops);
    const summaries = [{ id: 'W-S', status: 'Planned', year: '1999' }];
    const frozenSummaries = JSON.parse(JSON.stringify(summaries));
    assert.equal(globalThis.prksEffectiveWorkSummaries(summaries)[0].status, 'Completed');
    assert.deepEqual(summaries, frozenSummaries);

    // 5. Server-backed search results, which are never cached at all.
    const results = [{ id: 'W-S', title: 'Paper', status: 'Planned', doi: '10.1/x' }];
    const effective = globalThis.prksEffectiveWorksSync(results);
    assert.equal(effective[0].status, 'Completed', 'a fresh server result is overlaid too');
    assert.equal(effective[0].doi, '10.1/x');
    assert.equal(results[0].status, 'Planned', 'and the response itself is not rewritten');

    /* 6. Only an allowlisted value can become an operation. The control offers
     * five choices, so this is unreachable through the UI -- which is why it
     * belongs at the boundary rather than in the control. */
    for (const bad of ['Finished', 'completed', '', 'Done']) {
        assert.equal(globalThis.prksWorkFieldToCanonical('status', bad), null, bad);
    }
    for (const good of globalThis.PRKS_WORK_STATUSES) {
        assert.equal(globalThis.prksWorkFieldToCanonical('status', good), good);
    }
    // An untouched Status is not dirty, and a real change is recorded.
    const observed = { fields: base({ status: { value: 'Planned', revision: 2 } }) };
    assert.deepEqual(globalThis.prksDirtyWorkMetadataFields({ status: 'Planned' }, observed), {});
    assert.deepEqual(globalThis.prksDirtyWorkMetadataFields({ status: 'Paused' }, observed),
        { status: 'Paused' });

    globalThis.prksSetPendingWorkMetadata([]);
}

/* ---- acknowledgement reaches every cached representation ---- */
async function embeddedReconciliation() {
    const factory = createFakeIndexedDBFactory();
    const cache = createPrksOfflineStore({ indexedDB: factory });
    const summary = extra => Object.assign({ id: 'W-Y', title: 'Paper', year: '2020' }, extra || {});
    await cache.putEntity('work', 'W-Y', { id: 'W-Y', title: 'Paper', year: '2020' });
    await cache.putEntity('work-metadata-state', 'W-Y', { work_id: 'W-Y', fields: base() });
    await cache.putEntity('folder', 'F1', { id: 'F1', works: [summary(), { id: 'W-OTHER', year: '1990' }] });
    await cache.putEntity('folder', 'F2', { id: 'F2', works: [{ id: 'W-OTHER', year: '1990' }] });
    await cache.putEntity('person', 'P1', { id: 'P1', works: [summary()] });
    await cache.putEntity('playlist', 'PL1', { id: 'PL1', items: [summary()] });
    await cache.putList('works-browse:index', [summary()], '');
    await cache.putList('recent:index', [summary()], '');
    await cache.putList('recently-added:index', [summary()], '');
    const offline = createPrksOfflineRuntime({ store: cache, window: null,
        prksRequest: async () => { throw new Error('no reads in this scenario'); } });

    const ack = { code: 'ACKNOWLEDGED', work_id: 'W-Y', field: 'year',
        value: '1998', server_revision: 1, changed: true };
    assert.equal(await offline.reconcileWorkField(ack), true);

    assert.equal((await cache.getEntity('work', 'W-Y')).value.year, '1998');
    assert.deepEqual((await cache.getEntity('work-metadata-state', 'W-Y')).value.fields.year,
        { value: '1998', revision: 1 });
    for (const key of ['works-browse:index', 'recent:index', 'recently-added:index']) {
        assert.equal((await cache.getList(key)).value[0].year, '1998', key + ' was patched');
    }
    /* Status rides the SAME generic path: a registry entry, and no
     * Status-specific reconciliation code anywhere. */
    assert.equal(await offline.reconcileWorkField({ code: 'ACKNOWLEDGED', work_id: 'W-Y',
        field: 'status', value: 'Completed', server_revision: 1, changed: true }), true);
    for (const key of ['works-browse:index', 'recent:index', 'recently-added:index']) {
        assert.equal((await cache.getList(key)).value[0].status, 'Completed', key + ' status');
    }
    assert.equal((await cache.getEntity('folder', 'F1')).value.works[0].status, 'Completed');
    assert.equal((await cache.getEntity('person', 'P1')).value.works[0].status, 'Completed');
    assert.equal((await cache.getEntity('playlist', 'PL1')).value.items[0].status, 'Completed');

    /* And so does `author_text` -- the same registry-only change. The value it
     * writes is the FIELD; what any of these rows end up CREDITING is still
     * decided afterwards by the credit helper from linked role data. */
    assert.equal(await offline.reconcileWorkField({ code: 'ACKNOWLEDGED', work_id: 'W-Y',
        field: 'author_text', value: 'Acknowledged Author', server_revision: 1,
        changed: true }), true);
    for (const key of ['works-browse:index', 'recent:index', 'recently-added:index']) {
        assert.equal((await cache.getList(key)).value[0].author_text, 'Acknowledged Author', key);
    }
    assert.equal((await cache.getEntity('folder', 'F1')).value.works[0].author_text,
        'Acknowledged Author');
    assert.equal((await cache.getEntity('person', 'P1')).value.works[0].author_text,
        'Acknowledged Author');
    assert.equal((await cache.getEntity('playlist', 'PL1')).value.items[0].author_text,
        'Acknowledged Author');

    const folder = (await cache.getEntity('folder', 'F1')).value;
    assert.equal(folder.works[0].year, '1998', 'the embedded Folder summary was patched');
    assert.equal(folder.works[1].year, '1990', 'other Works in that Folder are untouched');
    assert.equal((await cache.getEntity('folder', 'F2')).value.works[0].year, '1990',
        'a Folder that does not contain the Work is left alone');
    assert.equal((await cache.getEntity('person', 'P1')).value.works[0].year, '1998');
    assert.equal((await cache.getEntity('playlist', 'PL1')).value.items[0].year, '1998');

    /* A field no summary carries must not drag those domains into its
     * reconciliation -- DOI is the control case. */
    const before = ['folders', 'people', 'playlists'].map(d => offline.currentDomainGeneration(d));
    assert.equal(await offline.reconcileWorkField({ code: 'ACKNOWLEDGED', work_id: 'W-Y',
        field: 'doi', value: '10.1/x', server_revision: 1, changed: true }), true);
    assert.deepEqual(['folders', 'people', 'playlists'].map(d => offline.currentDomainGeneration(d)),
        before, 'a detail-only field leaves embedded domains completely alone');

    // Nothing cached of a kind is nothing to reconcile, not a failure.
    await cache.deleteEntity('person', 'P1');
    assert.equal(await offline.reconcileWorkField(
        Object.assign({}, ack, { value: '1997', server_revision: 2 })), true);
    assert.equal(await cache.getEntity('person', 'P1'), null, 'and nothing is fabricated');
}

/* An acknowledgement cannot retire while a cached representation of it is
 * unreadable: the durable operation stays, and the next attempt reconciles. */
async function unreadableSummariesBlockRetirement() {
    const factory = createFakeIndexedDBFactory();
    const cache = createPrksOfflineStore({ indexedDB: factory });
    await cache.putEntity('work', 'W-Y', { id: 'W-Y', year: '2020' });
    await cache.putEntity('folder', 'F1', { id: 'F1', works: [{ id: 'W-Y', year: '2020' }] });
    const unreadable = Object.assign(Object.create(Object.getPrototypeOf(cache)), cache, {
        // What the store reports when the read itself failed -- NOT the empty
        // array it reports for a kind that simply has nothing cached.
        getEntitiesByKind: async () => null,
    });
    const offline = createPrksOfflineRuntime({ store: unreadable, window: null,
        prksRequest: async () => { throw new Error('no reads in this scenario'); } });
    assert.equal(await offline.reconcileWorkField({ code: 'ACKNOWLEDGED', work_id: 'W-Y',
        field: 'year', value: '1998', server_revision: 1, changed: true }), false,
        'reconciliation reports failure, so the caller keeps the operation');
    assert.equal((await cache.getEntity('folder', 'F1')).value.works[0].year, '2020',
        'and nothing was half-written');
}

/* An acknowledgement reaches Work values held by REFERENCE in other entity
 * families -- the cached Research Graph snapshots -- and an older graph GET
 * cannot publish over it. */
async function graphReferenceReconciliation() {
    const factory = createFakeIndexedDBFactory();
    const cache = createPrksOfflineStore({ indexedDB: factory });
    const snapshot = () => ({ nodes: [
        { id: 'work:W-G', record_id: 'W-G', type: 'work', doc_type: 'article', label: 'Paper' },
        { id: 'work:W-OTHER', record_id: 'W-OTHER', type: 'work', doc_type: 'article' },
        { id: 'concept:C1', record_id: 'C1', type: 'concept' },
    ], edges: [] });
    await cache.putEntity('work', 'W-G', { id: 'W-G', doc_type: 'article' });
    await cache.putEntity('research-graph-core', 'snapshot', snapshot());
    await cache.putEntity('research-graph-people', 'snapshot', snapshot());
    const offline = createPrksOfflineRuntime({ store: cache, window: null,
        prksRequest: async () => { throw new Error('no reads in this scenario'); } });

    assert.equal(await offline.reconcileWorkField({ code: 'ACKNOWLEDGED', work_id: 'W-G',
        field: 'doc_type', value: 'book', server_revision: 1, changed: true }), true);
    for (const kind of ['research-graph-core', 'research-graph-people']) {
        const nodes = (await cache.getEntity(kind, 'snapshot')).value.nodes;
        assert.equal(nodes[0].doc_type, 'book', kind + ' Work node was patched');
        assert.equal(nodes[1].doc_type, 'article', 'and only the acknowledged Work');
        assert.equal(nodes[2].type, 'concept', 'other node kinds are untouched');
        assert.equal(nodes[0].label, 'Paper', 'and so are other columns');
    }

    /* A field no Graph node carries must not drag those domains into its
     * reconciliation -- DOI is the control case. */
    const before = ['research-graph-core', 'research-graph-people']
        .map(d => offline.currentDomainGeneration(d));
    assert.equal(await offline.reconcileWorkField({ code: 'ACKNOWLEDGED', work_id: 'W-G',
        field: 'doi', value: '10.1/x', server_revision: 1, changed: true }), true);
    assert.deepEqual(['research-graph-core', 'research-graph-people']
        .map(d => offline.currentDomainGeneration(d)), before,
        'a field the Graph does not render leaves both graph domains alone');

    // A missing snapshot is nothing to reconcile, not a failure.
    await cache.deleteEntity('research-graph-people', 'snapshot');
    assert.equal(await offline.reconcileWorkField({ code: 'ACKNOWLEDGED', work_id: 'W-G',
        field: 'doc_type', value: 'online', server_revision: 2, changed: true }), true);
    assert.equal(await cache.getEntity('research-graph-people', 'snapshot'), null,
        'and nothing is fabricated');
}

/* A Title acknowledgement reaches Concept and Argument caches too. */
async function titleReferenceReconciliation() {
    const factory = createFakeIndexedDBFactory();
    const cache = createPrksOfflineStore({ indexedDB: factory });
    await cache.putEntity('work', 'W-T', { id: 'W-T', title: 'Old' });
    await cache.putEntity('concept', 'C1', { id: 'C1', mentions: [
        { work_id: 'W-T', title: 'Old' }, { work_id: 'W-OTHER', title: 'Other' }] });
    await cache.putEntity('concept', 'C2', { id: 'C2', mentions: [{ work_id: 'W-OTHER', title: 'x' }] });
    await cache.putEntity('argument', 'A1', {
        id: 'A1', sources: [{ work_id: 'W-T', work_title: 'Old' }],
        mentions: [{ work_id: 'W-T', title: 'Old' }] });
    await cache.putEntity('research-graph-core', 'snapshot', { nodes: [
        { id: 'work:W-T', record_id: 'W-T', type: 'work', label: 'Old' }], edges: [] });
    const offline = createPrksOfflineRuntime({ store: cache, window: null,
        prksRequest: async () => { throw new Error('no reads in this scenario'); } });

    assert.equal(await offline.reconcileWorkField({ code: 'ACKNOWLEDGED', work_id: 'W-T',
        field: 'title', value: 'New Title', server_revision: 1, changed: true }), true);

    const concept = (await cache.getEntity('concept', 'C1')).value;
    assert.equal(concept.mentions[0].title, 'New Title', 'the Concept backlink was patched');
    assert.equal(concept.mentions[1].title, 'Other', 'and only the acknowledged Work');
    assert.equal((await cache.getEntity('concept', 'C2')).value.mentions[0].title, 'x',
        'a Concept that does not reference the Work is left alone');
    const argument = (await cache.getEntity('argument', 'A1')).value;
    assert.equal(argument.sources[0].work_title, 'New Title', 'both Argument collections');
    assert.equal(argument.mentions[0].title, 'New Title');
    assert.equal((await cache.getEntity('research-graph-core', 'snapshot')).value.nodes[0].label,
        'New Title', 'and the Graph label');

    /* A field no reference shape carries must not drag those domains into its
     * reconciliation. */
    const before = ['concepts', 'arguments'].map(d => offline.currentDomainGeneration(d));
    assert.equal(await offline.reconcileWorkField({ code: 'ACKNOWLEDGED', work_id: 'W-T',
        field: 'doi', value: '10.1/x', server_revision: 1, changed: true }), true);
    assert.deepEqual(['concepts', 'arguments'].map(d => offline.currentDomainGeneration(d)),
        before, 'a DOI reaches no Concept or Argument');

    /* An unreadable reference cache blocks retirement: the operation must be
     * replayed rather than retired believing it patched what it could not read. */
    const unreadable = Object.assign(Object.create(Object.getPrototypeOf(cache)), cache, {
        getEntitiesByKind: async kind => (kind === 'concept' ? null : []),
    });
    const blocked = createPrksOfflineRuntime({ store: unreadable, window: null,
        prksRequest: async () => { throw new Error('no reads'); } });
    assert.equal(await blocked.reconcileWorkField({ code: 'ACKNOWLEDGED', work_id: 'W-T',
        field: 'title', value: 'Later', server_revision: 2, changed: true }), false);
}

/* A graph GET that began before the acknowledgement must lose. */
async function staleGraphRead() {
    const factory = createFakeIndexedDBFactory();
    const cache = createPrksOfflineStore({ indexedDB: factory });
    const stale = { nodes: [{ id: 'work:W-G', record_id: 'W-G', type: 'work',
        doc_type: 'article', label: 'Paper' }], edges: [] };
    await cache.putEntity('research-graph-core', 'snapshot', JSON.parse(JSON.stringify(stale)));
    await cache.putEntity('work', 'W-G', { id: 'W-G', doc_type: 'article' });
    let release = null;
    const inFlight = new Promise(resolve => { release = resolve; });
    const offline = createPrksOfflineRuntime({ store: cache, window: null, prksRequest: async () => {
        await inFlight;
        return { ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(stale)) };
    } });

    const reading = offline.readThroughEntity('research-graph-core', 'snapshot',
        '/api/research-graph', { domain: 'research-graph-core' });
    await settle();
    assert.equal(await offline.reconcileWorkField({ code: 'ACKNOWLEDGED', work_id: 'W-G',
        field: 'doc_type', value: 'book', server_revision: 1, changed: true }), true);
    release();
    await reading;
    await settle();
    assert.equal((await cache.getEntity('research-graph-core', 'snapshot')).value.nodes[0].doc_type,
        'book', 'a stale graph response cannot beat the acknowledgement');
}

/* An embedded-entity GET that began before the acknowledgement must lose. */
async function staleEmbeddedRead() {
    const factory = createFakeIndexedDBFactory();
    const cache = createPrksOfflineStore({ indexedDB: factory });
    const stale = { id: 'F1', works: [{ id: 'W-Y', title: 'Paper', year: '2020' }] };
    await cache.putEntity('folder', 'F1', JSON.parse(JSON.stringify(stale)));
    await cache.putEntity('work', 'W-Y', { id: 'W-Y', year: '2020' });
    let release = null;
    const inFlight = new Promise(resolve => { release = resolve; });
    const offline = createPrksOfflineRuntime({ store: cache, window: null, prksRequest: async () => {
        await inFlight;
        return { ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(stale)) };
    } });

    const reading = offline.readThroughEntity('folder', 'F1', '/api/folders/F1', { domain: 'folders' });
    await settle();
    assert.equal(await offline.reconcileWorkField({ code: 'ACKNOWLEDGED', work_id: 'W-Y',
        field: 'year', value: '1998', server_revision: 1, changed: true }), true);
    release();
    await reading;
    await settle();
    assert.equal((await cache.getEntity('folder', 'F1')).value.works[0].year, '1998',
        'a stale Folder response cannot beat the acknowledgement');
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

/* A stale catalog response must not move a Work back into its old Progress
 * GROUP. Reverting a rendered value is bad; reverting membership makes the
 * Work vanish from the list the user is looking at and reappear in one they
 * are not. */
async function staleReadCannotRestoreTheOldGroup() {
    const factory = createFakeIndexedDBFactory();
    const cache = createPrksOfflineStore({ indexedDB: factory });
    const stale = [{ id: 'W-G', title: 'Paper', status: 'Planned' },
                   { id: 'W-OTHER', title: 'Other', status: 'Planned' }];
    await cache.putEntity('work', 'W-G', { id: 'W-G', status: 'Planned' });
    await cache.putList('works-browse:index', JSON.parse(JSON.stringify(stale)), '');
    let release = null;
    const inFlight = new Promise(resolve => { release = resolve; });
    const offline = createPrksOfflineRuntime({ store: cache, window: null, prksRequest: async () => {
        await inFlight;
        return { ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(stale)) };
    } });

    const reading = offline.readThroughList('works-browse:index', '/api/works?projection=browse',
        { domain: 'works-browse', validate: rows => Array.isArray(rows) });
    await settle();
    assert.equal(await offline.reconcileWorkField({ code: 'ACKNOWLEDGED', work_id: 'W-G',
        field: 'status', value: 'Completed', server_revision: 2, changed: true }), true);
    release();
    await reading;
    await settle();

    const rows = (await cache.getList('works-browse:index')).value;
    const group = status => rows.filter(w => w.status === status).map(w => w.id);
    assert.deepEqual(group('Completed'), ['W-G'], 'the Work stayed in its acknowledged group');
    assert.deepEqual(group('Planned'), ['W-OTHER'], 'and the old response did not drag it back');
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

    /* ---- a conflict result is ONE shape, never a mixture ----
     *
     * The shape is decided by what the result CARRIES, not by the field's
     * type: a small scalar arrives bounded when the full form would not fit
     * the durable result limit. But a result carrying members of both shapes
     * leaves which one to trust undecided, so each refuses the other's
     * members outright rather than quietly preferring one.
     */
    const doiConflictOp = { operation: 'SET_WORK_METADATA_FIELD', entity_id: 'W-A',
        payload: { field: 'doi', value: 'mine' } };
    const isResultOf = data => globalThis.prksWorkMetadataSyncHandler.isResult(data, doiConflictOp);
    const fullShape = { code: 'REVISION_CONFLICT', work_id: 'W-A', field: 'doi',
        current_revision: 4, current_value: 'theirs', requested_value: 'mine' };
    const boundedShape = { code: 'REVISION_CONFLICT', work_id: 'W-A', field: 'doi',
        current_revision: 4, current_preview: 'theirs',
        current_bytes: 6, requested_bytes: 4 };
    assert.equal(isResultOf(fullShape), true, 'the full shape is well formed');
    assert.equal(isResultOf(boundedShape), true,
        'and so is the bounded one, for a small scalar the server had to degrade');
    for (const [label, extra] of [
        ['current_value', { current_value: 'theirs' }],
        ['requested_value', { requested_value: 'mine' }],
    ]) {
        assert.equal(isResultOf(Object.assign({}, boundedShape, extra)), false,
            'a bounded result carrying ' + label + ' is a mixture');
    }
    for (const [label, extra] of [
        ['current_preview', { current_preview: 'theirs' }],
        ['current_bytes', { current_bytes: 6 }],
        ['requested_bytes', { requested_bytes: 4 }],
    ]) {
        assert.equal(isResultOf(Object.assign({}, fullShape, extra)), false,
            'a full result carrying ' + label + ' is a mixture');
    }
    // A byte-limited field is bounded ALWAYS -- accepting the full shape there
    // would admit a megabyte into the durable row.
    const bigOp = { operation: 'SET_WORK_METADATA_FIELD', entity_id: 'W-A',
        payload: { field: 'abstract', value: 'mine' } };
    assert.equal(globalThis.prksWorkMetadataSyncHandler.isResult(
        Object.assign({}, fullShape, { field: 'abstract' }), bigOp), false);
    assert.equal(globalThis.prksWorkMetadataSyncHandler.isResult(
        Object.assign({}, boundedShape, { field: 'abstract' }), bigOp), true);
    // Neither shape at all is not a result either.
    assert.equal(isResultOf({ code: 'REVISION_CONFLICT', work_id: 'W-A', field: 'doi',
        current_revision: 4 }), false);

    /* ---- the metadata-state projection keeps its shape ---- */
    assert.deepEqual(globalThis.prksMetadataStateAckPatch('abstract', 7, pendingText),
        { revision: 7 }, 'a byte-limited field carries its revision only');
    assert.deepEqual(globalThis.prksMetadataStateAckPatch('doi', 7, '10.1/x'),
        { value: '10.1/x', revision: 7 });

    /* ---- author_text rides exactly the same machinery ----
     *
     * It is byte-limited too, so it gets the compact acknowledgement, the
     * revision-only projection entry and the bounded conflict WITHOUT a branch
     * of its own -- membership of the registry is the whole mechanism. If any
     * of these needed field-specific code, the abstraction would be the thing
     * to fix.
     */
    const bigAuthor = 'Q'.repeat(20 * 1024);
    const authorOp = { operation: 'SET_WORK_METADATA_FIELD', entity_id: 'W-A',
        payload: { field: 'author_text', value: bigAuthor } };
    const authorAck = { code: 'ACKNOWLEDGED', work_id: 'W-A', field: 'author_text',
        server_revision: 4, changed: true, value_omitted: true };
    assert.equal(globalThis.prksWorkMetadataSyncHandler.isResult(authorAck, authorOp), true);
    assert.equal(globalThis.prksEffectiveMetadataAck(authorAck, authorOp).value, bigAuthor,
        'reconstructed from the immutable operation, not re-fetched');
    assert.equal(globalThis.prksWorkMetadataSyncHandler.isResult(
        Object.assign({ value: bigAuthor }, authorAck), authorOp), false,
        'a byte-limited ACK must not carry the value either way');
    assert.deepEqual(globalThis.prksMetadataStateAckPatch('author_text', 7, bigAuthor),
        { revision: 7 }, 'the projection carries the revision only');

    /* The observed base after a reload: the projection has no value for this
     * field, so it is read from the cached WORK record instead. Getting this
     * wrong would measure the next save against an empty string and enqueue a
     * change the user never made. */
    const state = { work_id: 'W-A', fields: base({ author_text: { revision: 3 } }) };
    const observedFields = globalThis.prksObservedWorkFields(
        state, { id: 'W-A', author_text: bigAuthor, doi: '10.1/x' });
    assert.deepEqual(observedFields.author_text, { value: bigAuthor, revision: 3 });
    assert.deepEqual(globalThis.prksDirtyWorkMetadataFields(
        { author_text: bigAuthor }, { fields: observedFields }), {},
        'an untouched large Author is not dirty after a reload');
    assert.deepEqual(globalThis.prksDirtyWorkMetadataFields(
        { author_text: 'Jane' }, { fields: observedFields }), { author_text: 'Jane' });

    // The editor refuses over-limit values before they can become operations.
    const authorLimit = globalThis.PRKS_WORK_FIELD_BYTE_LIMITS.author_text;
    assert.equal(authorLimit, 64 * 1024);
    assert.equal(globalThis.prksWorkFieldLimitError('author_text', 'x'.repeat(authorLimit)), null);
    const refusal = globalThis.prksWorkFieldLimitError(
        'author_text', 'x'.repeat(authorLimit + 1024));
    assert.match(refusal, /^Author is too long to save \(65 KB of 64 KB allowed\)\.$/);
    assert.match(
        globalThis.prksWorkFieldLimitError(
            'author_text', 'x'.repeat(authorLimit + 1024), 'Channel name'),
        /^Channel name is too long/, 'named the way the form names it');
    // Bytes, not characters.
    assert.equal(globalThis.prksWorkFieldLimitError('author_text', '\u65e5'.repeat(30000)) === null,
        false, 'a multibyte value over the byte limit is refused');

    delete globalThis.prksSync;
}

/* ---- year + published_date: one field, many cached surfaces ---- */
async function highFanOut() {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    globalThis.prksSync = { store };
    const observed = resolved(base({
        year: { value: '', revision: 0 },
        published_date: { value: '2020-05-01', revision: 2 },
    }));
    await store.saveWorkMetadataFields('W-Y', { year: '1998' }, observed);
    await globalThis.prksRefreshPendingWorkMetadata();

    /* The card's displayed year is DERIVED -- explicit Year, else the date's
     * year -- and that rule stays in the renderer. The overlay only supplies
     * effective field values. */
    const displayedYear = work => {
        const effective = globalThis.prksEffectiveWorkSync(work);
        const year = String(effective.year || '').trim();
        if (year) return year;
        const match = String(effective.published_date || '').match(/^(\d{4})/);
        return match ? match[1] : '';
    };
    const server = { id: 'W-Y', title: 'Paper', year: '', published_date: '2020-05-01' };
    assert.equal(displayedYear(server), '1998', 'a pending Year overrides the acknowledged date');
    assert.equal(server.year, '', 'the acknowledged Work is untouched');

    // Clearing Year reveals the date's year again.
    await store.saveWorkMetadataFields('W-Y', { year: '' },
        resolved(base({ year: { value: '1998', revision: 1 },
            published_date: { value: '2020-05-01', revision: 2 } })));
    await globalThis.prksRefreshPendingWorkMetadata();
    assert.equal(displayedYear({ id: 'W-Y', year: '1998', published_date: '2020-05-01' }), '2020',
        'a pending cleared Year falls back to the date');

    // A pending date alone moves the displayed year.
    const store2 = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    globalThis.prksSync = { store: store2 };
    await store2.saveWorkMetadataFields('W-Y', { published_date: '2022-09-01' },
        resolved(base({ published_date: { value: '2020-05-01', revision: 1 } })));
    await globalThis.prksRefreshPendingWorkMetadata();
    assert.equal(displayedYear({ id: 'W-Y', year: '', published_date: '2020-05-01' }), '2022');
    // ...but an explicit pending Year still wins over a pending date.
    await store2.saveWorkMetadataFields('W-Y', { year: '1999' }, resolved(base()));
    await globalThis.prksRefreshPendingWorkMetadata();
    assert.equal(displayedYear({ id: 'W-Y', year: '', published_date: '2020-05-01' }), '1999');

    /* All three browse lists carry both fields, because every Work card shows
     * a year. Order of independent operations never matters. */
    for (const projection of ['works-browse', 'recent', 'recently-added']) {
        const rows = [{ id: 'W-Y', title: 'Paper', year: '2020', published_date: '2020-05-01' },
            { id: 'W-Z', title: 'Other', year: '2001', published_date: '2001-01-01' }];
        const frozen = JSON.parse(JSON.stringify(rows));
        const effective = globalThis.prksEffectiveProjectionRows(rows, projection);
        assert.equal(effective[0].year, '1999', projection + ' carries the pending Year');
        assert.equal(effective[0].published_date, '2022-09-01', projection + ' carries the pending date');
        assert.equal(effective[1].year, '2001', 'untouched rows are untouched');
        assert.deepEqual(rows, frozen, projection + ' snapshot is never mutated');
    }

    /* Cached Folder/Person/Playlist details embed Work SUMMARIES. One helper
     * serves all three, so no component reads the durable queue itself. */
    const summaries = [{ id: 'W-Y', title: 'Paper', year: '2020', published_date: '2020-05-01',
        publisher: 'Elsevier' }];
    const frozenSummaries = JSON.parse(JSON.stringify(summaries));
    const effectiveSummaries = globalThis.prksEffectiveWorkSummaries(summaries);
    assert.equal(effectiveSummaries[0].year, '1999');
    assert.equal(effectiveSummaries[0].published_date, '2022-09-01');
    assert.equal(effectiveSummaries[0].publisher, 'Elsevier', 'untouched fields survive');
    assert.deepEqual(summaries, frozenSummaries, 'the cached entity is never mutated');
    assert.deepEqual(globalThis.PRKS_WORK_SUMMARY_FIELDS,
        ['title', 'status', 'doc_type', 'thumb_page', 'author_text', 'year',
         'published_date', 'publisher', 'source_url']);

    /* The Published Date codec: the editor spells it dd/mm/yyyy, the wire and
     * the column are ISO, and comparing the spellings would make an untouched
     * date dirty on every save. */
    /* The REAL date module, never a hand-written double: the stub this
     * replaced answered `null` for an unreadable date while production
     * answered `''`, so the contract looked pinned while the editor was
     * actually treating `31/02/2026` as "clear the field". */
    require('../../frontend/js/date-format.js');
    assert.equal(globalThis.prksWorkFieldToDisplay('published_date', '2026-09-12'), '12/09/2026');
    assert.equal(globalThis.prksWorkFieldToCanonical('published_date', '12/09/2026'), '2026-09-12');
    assert.equal(globalThis.prksWorkFieldToCanonical('published_date', ''), '',
        'a cleared date is the empty string, like every other field');
    assert.equal(globalThis.prksWorkFieldToCanonical('published_date', '31/02/2026'), null,
        'an uninterpretable date is null, so the caller can refuse it');
    assert.equal(globalThis.prksWorkFieldToCanonical('year', '1998'), '1998', 'other fields are identity');

    const displayDraft = { published_date: '12/09/2026', year: '1999' };
    const dateObserved = { fields: base({ published_date: { value: '2026-09-12', revision: 1 },
        year: { value: '1999', revision: 1 } }) };
    assert.deepEqual(globalThis.prksDirtyWorkMetadataFields(displayDraft, dateObserved), {},
        'an untouched date is not dirty merely because it is spelled differently');
    assert.deepEqual(globalThis.prksDirtyWorkMetadataFields(
        { published_date: '01/01/2000' }, dateObserved), { published_date: '2000-01-01' },
        'and a real change is recorded canonically');
    assert.deepEqual(globalThis.prksDirtyWorkMetadataFields(
        { published_date: '31/02/2026' }, dateObserved), {},
        'an uninterpretable draft produces no operation');
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
    await staleReadCannotRestoreTheOldGroup();
    await hydration();
    await abstracts();
    await abstractAcknowledgement();
    await highFanOut();
    statusMembership();
    authorTextComposition();
    provenanceSourceUrl();
    titleReferenceOverlays();
    docTypeMembershipAndGraph();
    thumbPageCodec();
    thumbPageKeepsRowsValid();
    thumbnailResourceIdentity();
    everyEffectiveHelperIsTyped();
    metadataStateWireValidation();
    await embeddedReconciliation();
    await unreadableSummariesBlockRetirement();
    await staleEmbeddedRead();
    await graphReferenceReconciliation();
    await titleReferenceReconciliation();
    await staleGraphRead();
    console.log('All ' + checks + ' Work metadata checks passed');
}

main().then(() => process.exit(0))
    .catch(error => { console.error(error); process.exit(1); });
