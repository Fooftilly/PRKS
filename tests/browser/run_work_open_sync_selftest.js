'use strict';
const strict = require('assert/strict');
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); },
    { get: (_target, key) => (...args) => { checks += 1; return strict[key](...args); } });
const { createFakeIndexedDBFactory } = require('./lib/fake_indexeddb.js');
const { createPrksLocalStore } = require('../../frontend/js/local-store.js');
const { createPrksOfflineStore } = require('../../frontend/js/offline-store.js');
const { createPrksOfflineRuntime } = require('../../frontend/js/offline-runtime.js');

/* The browser supplies these from app.js, where they live beside every other
 * browse-row validator. `tests/test_frontend_work_open_sync.py` pins that they
 * exist and that work-open-state.js consults them rather than trusting cached
 * rows; here they stand in so the guarded paths actually execute. */
globalThis.prksIsRecentRowShape = row => !!row && typeof row === 'object' &&
    typeof row.id === 'string' && !!row.id &&
    typeof row.last_opened_at === 'string' && !!row.last_opened_at.trim();
globalThis.prksIsRecentIndexShape = rows => Array.isArray(rows) && rows.every(globalThis.prksIsRecentRowShape);

require('../../frontend/js/work-tag-state.js');
require('../../frontend/js/work-open-state.js');
require('../../frontend/js/sync-runtime.js');

let sequence = 0;
const uuid = () => '00000000-0000-4000-8000-' + (++sequence).toString(16).padStart(12, '0');
const tick = () => new Promise(resolve => setTimeout(resolve, 1));
async function settle() { for (let i = 0; i < 5; i++) await tick(); }

function row(id, at, extra) {
    return Object.assign({ id, title: 'Work ' + id, last_opened_at: at }, extra || {});
}
function ack(workId, at, item) {
    return { code: 'ACKNOWLEDGED', work_id: workId, changed: true, effective_opened_at: at,
        recent_item: item === undefined ? row(workId, at) : item };
}
function openRuntime(store, request, reconcile) {
    return globalThis.createPrksSyncRuntime({ store, online: () => true, request,
        handlers: { MARK_WORK_OPENED: Object.assign({}, globalThis.prksWorkOpenSyncHandler, { reconcile }) } });
}

/* ---- the durable event: one effective never-sent open per Work ---- */
async function recording() {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    const first = await store.recordWorkOpened('W-1', '2026-09-11T10:00:00.000Z', null);
    const second = await store.recordWorkOpened('W-1', '2026-09-11T10:05:00.000Z', null);
    const third = await store.recordWorkOpened('W-1', '2026-09-11T10:10:00.000Z', null);
    let rows = await store.listOperations();
    assert.equal(rows.length, 1, 'three opens of one Work are one effective event');
    assert.equal(rows[0].op_id, third.op_id);
    assert.equal(rows[0].occurred_at, '2026-09-11T10:10:00.000Z');
    assert.notEqual(third.op_id, first.op_id, 'coalescing writes a new envelope, never edits one');
    assert.notEqual(third.op_id, second.op_id);
    assert.equal(await store.getOperation(first.op_id), null);

    // A clock that jumped backwards must not lose the later event.
    const kept = await store.recordWorkOpened('W-1', '2026-09-11T09:00:00.000Z', null);
    assert.equal(kept.op_id, third.op_id);
    assert.equal((await store.getOperation(third.op_id)).occurred_at, '2026-09-11T10:10:00.000Z');

    // Different Works are independent events.
    await store.recordWorkOpened('W-2', '2026-09-11T10:20:00.000Z', null);
    await store.recordWorkOpened('W-1', '2026-09-11T10:30:00.000Z', null);
    rows = await store.listOperations();
    assert.equal(rows.length, 2);
    assert.deepEqual(rows.map(r => r.entity_id).sort(), ['W-1', 'W-2']);
    assert.equal(rows.find(r => r.entity_id === 'W-1').occurred_at, '2026-09-11T10:30:00.000Z');

    /* A SENT event is left alone rather than cancelled: it may already be
     * ledgered, and it does not need cancelling anyway, because the server
     * takes the maximum event time. */
    const sent = rows.find(r => r.entity_id === 'W-2');
    await store.claimOperation(sent.op_id);
    await store.updateOperationSyncState(sent.op_id, { status: 'pending' });
    const independent = await store.recordWorkOpened('W-2', '2026-09-11T11:00:00.000Z', null);
    assert.notEqual(independent.op_id, sent.op_id);
    assert.equal((await store.getOperation(sent.op_id)).occurred_at, '2026-09-11T10:20:00.000Z',
        'an already-sent event keeps its identity and its timestamp');
    assert.equal((await store.listOperations()).filter(r => r.entity_id === 'W-2').length, 2);

    // Envelope shape: no payload, no revision scope.
    assert.deepEqual(independent.payload, {});
    assert.equal(independent.base_revision, null);
    await assert.rejects(store.recordWorkOpened('W-3', 'whenever', null),
        err => err.prksLocalStoreCode === 'invalid_envelope');
    await assert.rejects(store.recordWorkOpened('', '2026-09-11T10:00:00.000Z', null),
        err => err.prksLocalStoreCode === 'invalid_envelope');
}

/* ---- the optimistic Recent overlay ---- */
async function overlay() {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    const base = [row('W-A', '2026-09-11 12:00:00.000'), row('W-B', '2026-09-11 11:00:00.000')];

    assert.deepEqual(globalThis.prksEffectiveRecent(base, []).map(r => r.id), ['W-A', 'W-B']);

    // An open moves a Work the list already carries.
    await store.recordWorkOpened('W-B', '2026-09-11T13:00:00.000Z', null);
    let effective = globalThis.prksEffectiveRecent(base, await store.listOperations());
    assert.deepEqual(effective.map(r => r.id), ['W-B', 'W-A']);
    assert.equal(effective[0].last_opened_at, '2026-09-11 13:00:00.000');
    assert.equal(base[1].last_opened_at, '2026-09-11 11:00:00.000', 'the cached list is not rewritten');

    // A Work the list does not carry needs its own bounded display snapshot.
    await store.recordWorkOpened('W-C', '2026-09-11T14:00:00.000Z',
        { recent_item: row('W-C', '2026-09-11 14:00:00.000') });
    effective = globalThis.prksEffectiveRecent(base, await store.listOperations());
    assert.deepEqual(effective.map(r => r.id), ['W-C', 'W-B', 'W-A']);

    // With no snapshot and no base row there is nothing honest to render.
    await store.recordWorkOpened('W-D', '2026-09-11T15:00:00.000Z', null);
    effective = globalThis.prksEffectiveRecent(base, await store.listOperations());
    assert.deepEqual(effective.map(r => r.id), ['W-C', 'W-B', 'W-A'],
        'an open event alone is not enough to invent a Work card');

    // Acknowledged events are the server's job, not the overlay's.
    const rows = await store.listOperations();
    await store.updateOperationSyncState(rows.find(r => r.entity_id === 'W-C').op_id, { status: 'acknowledged' });
    assert.deepEqual(globalThis.prksEffectiveRecent(base, await store.listOperations()).map(r => r.id),
        ['W-B', 'W-A']);

    // Canonical order and limit, reproduced exactly.
    const many = [];
    for (let i = 0; i < 40; i++) many.push(row('W-' + (100 + i), '2026-09-11 10:' + String(i).padStart(2, '0') + ':00.000'));
    const ordered = globalThis.prksOrderRecentRows(many);
    assert.equal(ordered.length, globalThis.PRKS_RECENT_OVERLAY_LIMIT);
    assert.equal(ordered[0].id, 'W-139');
    // Ties break on id ASC, like the canonical projection.
    const tied = globalThis.prksOrderRecentRows([row('W-z', '2026-09-11 10:00:00'), row('W-a', '2026-09-11 10:00:00')]);
    assert.deepEqual(tied.map(r => r.id), ['W-a', 'W-z']);
    // Mixed second and millisecond precision sorts chronologically.
    assert.deepEqual(globalThis.prksOrderRecentRows([
        row('a', '2026-09-11 12:00:00'), row('b', '2026-09-11 12:00:00.250'),
        row('c', '2026-09-11 12:00:00.900'), row('d', '2026-09-11 12:00:01'),
    ]).map(r => r.id), ['d', 'c', 'b', 'a']);
}

/* ---- reload durability of the overlay ---- */
async function reload() {
    const factory = createFakeIndexedDBFactory();
    const store = createPrksLocalStore({ indexedDB: factory, uuid });
    const base = [row('W-A', '2026-09-11 12:00:00.000')];
    await store.recordWorkOpened('W-A', '2026-09-11T18:00:00.000Z', null);
    const afterReload = createPrksLocalStore({ indexedDB: factory, uuid });
    const effective = globalThis.prksEffectiveRecent(base, await afterReload.listOperations());
    assert.equal(effective[0].last_opened_at, '2026-09-11 18:00:00.000',
        'the overlay is reconstructed from prks-local-v1, not from tab memory');
}

/* ---- acknowledgement: reconcile, then retire ---- */
async function acknowledgement() {
    const factory = createFakeIndexedDBFactory();
    const store = createPrksLocalStore({ indexedDB: factory, uuid });
    const cache = createPrksOfflineStore({ indexedDB: factory });
    await cache.putList('recent:index', [row('W-A', '2026-09-11 12:00:00.000'),
        row('W-B', '2026-09-11 11:00:00.000')], '');
    const offline = createPrksOfflineRuntime({ store: cache, window: null,
        prksRequest: async () => { throw new Error('no reads in this scenario'); } });

    const op = await store.recordWorkOpened('W-B', '2026-09-11T13:00:00.000Z', null);
    const sent = [];
    const runtime = openRuntime(store, async (path, init) => {
        assert.equal(path, '/api/sync/operations');
        const body = JSON.parse(init.body);
        sent.push(body.op_id);
        assert.deepEqual(body.payload, {});
        assert.equal(body.base_revision, null);
        return { ok: true, status: 200, json: async () => ack('W-B', '2026-09-11 13:00:00.000') };
    }, result => offline.reconcileRecentOpen(result));
    await runtime.wake(); await settle(); runtime.stop();

    assert.deepEqual(sent, [op.op_id]);
    assert.equal(await store.getOperation(op.op_id), null, 'a reconciled open event retires locally');
    const cached = (await cache.getList('recent:index')).value;
    assert.deepEqual(cached.map(r => r.id), ['W-B', 'W-A']);
    assert.equal(cached[0].last_opened_at, '2026-09-11 13:00:00.000');

    // No cached Recent list: nothing to reconcile is success, and no partial
    // list is fabricated from one event.
    await cache.deleteList('recent:index');
    assert.equal(await offline.reconcileRecentOpen(ack('W-Z', '2026-09-11 14:00:00.000')), true);
    assert.equal(await cache.getList('recent:index'), null);
}

/* ---- a GET that began before the ACK cannot publish over it ---- */
async function staleRecentRead() {
    const factory = createFakeIndexedDBFactory();
    const cache = createPrksOfflineStore({ indexedDB: factory });
    await cache.putList('recent:index', [row('W-A', '2026-09-11 12:00:00.000')], '');
    let release = null;
    const inFlight = new Promise(resolve => { release = resolve; });
    const offline = createPrksOfflineRuntime({ store: cache, window: null, prksRequest: async () => {
        await inFlight;
        return { ok: true, status: 200, json: async () => [row('W-A', '2026-09-11 12:00:00.000')] };
    } });

    const reading = offline.readThroughList('recent:index', '/api/recent',
        { domain: 'recent', validate: globalThis.prksIsRecentIndexShape });
    await settle();
    assert.equal(await offline.reconcileRecentOpen(ack('W-A', '2026-09-11 16:00:00.000')), true);
    release();
    await reading;
    await settle();
    assert.equal((await cache.getList('recent:index')).value[0].last_opened_at, '2026-09-11 16:00:00.000',
        'a stale /api/recent response cannot beat the acknowledgement');
}

/* ---- terminal outcomes have no conflict UI ---- */
async function terminal() {
    for (const [code, status] of [['ENTITY_NOT_FOUND', 404], ['INVALID_ENVELOPE', 400], ['OP_ID_REUSE', 409]]) {
        const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
        const op = await store.recordWorkOpened('W-GONE', '2026-09-11T10:00:00.000Z', null);
        let calls = 0;
        const runtime = openRuntime(store, async () => {
            calls += 1;
            return { ok: false, status, json: async () => ({ code, work_id: 'W-GONE' }) };
        }, async () => { throw new Error('must not reconcile a terminal result'); });
        await runtime.wake(); await settle();
        assert.equal(calls, 1, code + ' is not retried');
        assert.deepEqual(await store.listOperations(), [],
            code + ' is consumed: an open event has no resolution to offer');
        assert.deepEqual(runtime.discarded(),
            [{ operation: 'MARK_WORK_OPENED', entity_id: 'W-GONE', code }],
            code + ' stays visible in diagnostics');
        runtime.stop();
    }

    // Transport faults are still ordinary retryable failures.
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    const op = await store.recordWorkOpened('W-1', '2026-09-11T10:00:00.000Z', null);
    const runtime = openRuntime(store, async () => { throw new TypeError('Failed to fetch'); }, async () => true);
    await runtime.wake(); await settle(); runtime.stop();
    const kept = await store.getOperation(op.op_id);
    assert.equal(kept.status, 'pending');
    assert.equal(kept.attempt_count, 1);
    assert.deepEqual(runtime.discarded(), []);

    /* A lost response is not a reason to mint a replacement open event.
     * The server may already have ledgered the first envelope, so retry must
     * preserve the exact op_id and semantic envelope. Backend
     * WorkOpenSyncTests.test_replay_is_exact_after_the_work_is_reopened proves
     * that replaying that op_id is idempotent at the ledger boundary. */
    {
        const retryStore = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
        const retryOp = await retryStore.recordWorkOpened(
            'W-LOST', '2026-09-11T10:00:00.000Z', null);
        const sent = [];
        let loseFirstResponse = true;
        const retryRuntime = openRuntime(retryStore, async (_path, init) => {
            const envelope = JSON.parse(init.body);
            sent.push(envelope);
            if (loseFirstResponse) {
                loseFirstResponse = false;
                throw new TypeError('response lost after send');
            }
            return {
                ok: true, status: 200,
                json: async () => ack('W-LOST', '2026-09-11 10:00:00.000'),
            };
        }, async () => true);

        await retryRuntime.wake();
        await settle();
        let pending = await retryStore.getOperation(retryOp.op_id);
        assert.equal(pending.status, 'pending',
            'lost response leaves the original open event retryable');
        assert.equal(sent.length, 1);
        assert.equal(sent[0].op_id, retryOp.op_id);

        // Skip only the timer delay; do not replace or rewrite the event.
        await retryStore.updateOperationSyncState(retryOp.op_id, { attempt_count: 0 });
        await retryRuntime.wake();
        await settle();
        retryRuntime.stop();

        assert.equal(sent.length, 2,
            'the same open event is retried once connectivity recovers');
        assert.equal(sent[1].op_id, retryOp.op_id,
            'retry preserves op_id for server idempotency');
        assert.deepEqual(sent[1], sent[0],
            'retry preserves the complete semantic envelope');
        assert.equal(await retryStore.getOperation(retryOp.op_id), null,
            'the acknowledged replay is reconciled and retired');
    }
}

/* ---- each family answers only its own results ---- */
async function isolation() {
    const handler = globalThis.prksWorkOpenSyncHandler;
    const op = { operation: 'MARK_WORK_OPENED', entity_id: 'W-1', payload: {} };
    assert.equal(handler.isResult(ack('W-1', '2026-09-11 10:00:00.000'), op), true);
    assert.equal(handler.isResult({ code: 'ENTITY_NOT_FOUND', work_id: 'W-1' }, op), true);
    for (const bad of [
        null,
        { code: 'ACKNOWLEDGED', work_id: 'W-OTHER', changed: true, effective_opened_at: 'x', recent_item: null },
        { code: 'ACKNOWLEDGED', work_id: 'W-1', changed: 'yes', effective_opened_at: 'x', recent_item: null },
        { code: 'ACKNOWLEDGED', work_id: 'W-1', changed: true, effective_opened_at: '', recent_item: null },
        { code: 'ACKNOWLEDGED', work_id: 'W-1', changed: true, effective_opened_at: 'x', recent_item: row('W-OTHER', 'x') },
        // Work-Tag outcomes are meaningless here and must not be accepted.
        { code: 'REVISION_CONFLICT', work_id: 'W-1', current_revision: 2, current_state: true, requested_state: false },
        { code: 'TAG_MERGED', work_id: 'W-1', target_tag_id: 'T-2' },
        { code: 'TAG_DELETED', work_id: 'W-1' },
    ]) {
        assert.equal(handler.isResult(bad, op), false, JSON.stringify(bad));
    }
    assert.deepEqual(handler.terminal({ code: 'ENTITY_NOT_FOUND' }), { discard: 'ENTITY_NOT_FOUND' });

    // And the Work-Tag family rejects an open-event acknowledgement.
    const tagOp = { operation: 'ADD_WORK_TAG', entity_id: 'W-1', payload: { tag_id: 'T-1' } };
    assert.equal(globalThis.prksWorkTagSyncHandler.isResult(ack('W-1', 'x'), tagOp), false);
    assert.deepEqual(globalThis.prksWorkTagSyncHandler.terminal({ code: 'TAG_DELETED' }),
        { conflict: { code: 'TAG_DELETED' } });

    // An unregistered family is never claimed or sent.
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    const opened = await store.recordWorkOpened('W-1', '2026-09-11T10:00:00.000Z', null);
    let sent = 0;
    const runtime = globalThis.createPrksSyncRuntime({ store, online: () => true,
        request: async () => { sent += 1; return { ok: true, status: 200, json: async () => ({}) }; },
        handlers: { ADD_WORK_TAG: globalThis.prksWorkTagSyncHandler } });
    await runtime.wake(); await settle(); runtime.stop();
    assert.equal(sent, 0, 'a coordinator with no handler for a family sends nothing');
    const untouched = await store.getOperation(opened.op_id);
    assert.equal(untouched.attempt_count, 0, 'and never claims it either');
}

/* ---- merge helper ---- */
async function merge() {
    const rows = [row('W-A', '2026-09-11 12:00:00.000'), row('W-B', '2026-09-11 11:00:00.000')];
    const merged = globalThis.prksMergeRecentOpen(rows, ack('W-B', '2026-09-11 13:00:00.000'));
    assert.deepEqual(merged.map(r => r.id), ['W-B', 'W-A']);
    assert.equal(rows[1].last_opened_at, '2026-09-11 11:00:00.000', 'the input list is not mutated');
    assert.equal(globalThis.prksMergeRecentOpen(rows, ack('W-B', 'x', null)), null,
        'an acknowledgement with no canonical row cannot be merged');
    assert.equal(globalThis.prksMergeRecentOpen(rows, ack('W-B', 'x', row('W-OTHER', 'x'))), null);
}

async function main() {
    await recording();
    await overlay();
    await reload();
    await acknowledgement();
    await staleRecentRead();
    await terminal();
    await isolation();
    await merge();
    console.log('All ' + checks + ' Work open checks passed');
}

main().then(() => process.exit(0))
    .catch(error => { console.error(error); process.exit(1); });
