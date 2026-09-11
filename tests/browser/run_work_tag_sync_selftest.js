'use strict';
const strict = require('assert/strict');
/* Counted assertions: the run reports how many checks actually executed, so a
 * section that silently stops running cannot pass as green. */
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); },
    { get: (_target, key) => (...args) => { checks += 1; return strict[key](...args); } });
const { createFakeIndexedDBFactory } = require('./lib/fake_indexeddb.js');
const { createPrksLocalStore } = require('../../frontend/js/local-store.js');
const { createPrksOfflineStore } = require('../../frontend/js/offline-store.js');
const { createPrksOfflineRuntime } = require('../../frontend/js/offline-runtime.js');
require('../../frontend/js/work-tag-state.js');
require('../../frontend/js/sync-runtime.js');
let sequence = 0;
const uuid = () => '00000000-0000-4000-8000-' + (++sequence).toString(16).padStart(12, '0');
const tag = { id: 'T-A', name: 'Existing', color: '#123456', aliases: [] };
const ack = { code: 'ACKNOWLEDGED', work_id: 'W-A', tag_id: tag.id, present: true, server_revision: 1, tag };
async function main() {
    const factory = createFakeIndexedDBFactory();
    const store = createPrksLocalStore({ indexedDB: factory, uuid });
    const cache = createPrksOfflineStore({ indexedDB: factory });
    const opts = { work_id: 'W-A', assigned: [], known_absent: {} };
    await cache.putEntity('work', 'W-A', { id: 'W-A', title: 'Complete base', tags: [] });
    await cache.putEntity('work-tag-options', 'W-A', opts);
    let requestFails = false;
    let response = [tag];
    const offline = createPrksOfflineRuntime({ store: cache, window: null, prksRequest: async () => {
        if (requestFails) throw new Error();
        return { ok: true, status: 200, json: async () => response };
    } });
    assert(globalThis.prksIsTagsIndexShape([tag]));
    for (const bad of [{ ...tag, aliases: [3] }, { ...tag, name: null }, { ...tag, color: {} }, { ...tag, id: '' }]) {
        assert(!globalThis.prksIsTagsIndexShape([bad]));
    }
    assert(globalThis.prksIsWorkTagOptionsShape(opts, 'W-A'));
    assert(!globalThis.prksIsWorkTagOptionsShape({ ...opts, assigned: [{ tag_id: tag.id, relation_revision: -1 }] }));
    assert(!globalThis.prksIsWorkTagOptionsShape({ ...opts, known_absent: { 'T-A': 0 } }));
    await offline.readThroughList('tags:index', '/api/tags', { domain: 'tags', validate: globalThis.prksIsTagsIndexShape });
    // Flush the best-effort cache publication before testing poison prevention.
    await new Promise(resolve => setTimeout(resolve, 20));
    response = [{ ...tag, aliases: false }];
    await assert.rejects(offline.readThroughList('tags:index', '/api/tags', { domain: 'tags', validate: globalThis.prksIsTagsIndexShape }));
    assert.deepEqual((await cache.getList('tags:index')).value, [tag]);

    const op = await store.coalesceWorkTag('W-A', tag.id, true, false, 0, tag);
    let cacheWorks = false;
    let received = [];
    const runtime = globalThis.createPrksSyncRuntime({ store, online: () => true,
        request: async (path, init) => { received.push(JSON.parse(init.body)); return { ok: true, status: 200, json: async () => ack }; },
        reconcile: async result => cacheWorks && await offline.reconcileWorkTag(result),
    });
    await runtime.wake(); runtime.stop();
    assert.equal((await store.getOperation(op.op_id)).status, 'pending');
    assert.equal((await store.getOperation(op.op_id)).attempt_count, 1);
    assert.deepEqual(globalThis.prksEffectiveWorkTags({ id: 'W-A', tags: [] }, await store.listOperations()), [tag]);
    // Simulate an interrupted sender, then startup recovery resends the same id.
    await store.updateOperationSyncState(op.op_id, { status: 'syncing', attempt_count: 0 });
    cacheWorks = true;
    const recovered = globalThis.createPrksSyncRuntime({ store, online: () => true,
        request: async (path, init) => { received.push(JSON.parse(init.body)); return { ok: true, status: 200, json: async () => ack }; },
        reconcile: result => offline.reconcileWorkTag(result),
    });
    await recovered.wake(); recovered.stop();
    assert.equal(await store.getOperation(op.op_id), null, 'a reconciled ACK retires the local operation');
    assert.equal(received.length, 2);
    assert.deepEqual(received[0], received[1]);
    assert.equal((await cache.getEntity('work', 'W-A')).value.tags[0].id, tag.id);
    assert.deepEqual((await cache.getEntity('work-tag-options', 'W-A')).value.assigned, [{ tag_id: tag.id, relation_revision: 1 }]);
    await cache.clearAll();
    assert(await offline.reconcileWorkTag(ack));
    assert.equal(await cache.getEntity('work', 'W-A'), null, 'ACK cannot fabricate a complete Work');
    assert.deepEqual(await store.listOperations(), [], 'nothing outlives a reconciled acknowledgement');
    // Structured conflict retains optimistic removal.
    const remove = await store.coalesceWorkTag('W-A', tag.id, false, true, 1, tag);
    const conflicts = globalThis.createPrksSyncRuntime({ store, online: () => true,
        request: async () => ({ ok: false, status: 409, json: async () => ({
            work_id: 'W-A', tag_id: tag.id, code: 'REVISION_CONFLICT', current_revision: 3, current_state: true, requested_state: false,
        }) }), reconcile: async () => { throw new Error('must not reconcile conflict'); },
    });
    await conflicts.wake(); conflicts.stop();
    assert.equal((await store.getOperation(remove.op_id)).server_result.current_revision, 3);
    assert.deepEqual(globalThis.prksEffectiveWorkTags({ id: 'W-A', tags: [tag] }, await store.listOperations()), []);
    assert.equal((await store.getOperation(remove.op_id)).status, 'conflict');
    await connectivity();
    await transport();
    await lifecycle();
    await staleReads();
    await claimRace();
    console.log('All ' + checks + ' Work Tag sync checks passed');
}const tick = () => new Promise(resolve => setTimeout(resolve, 1));
/* The coordinator drains asynchronously off a connectivity notification, so
 * poll for the observable outcome instead of guessing a number of ticks. */
async function waitFor(predicate, label) {
    for (let i = 0; i < 200; i++) {
        if (await predicate()) return;
        await tick();
    }
    throw new Error('timed out waiting for: ' + label);
}
async function settle() { for (let i = 0; i < 5; i++) await tick(); }

function syncRuntime(store, request, reconcile) {
    return globalThis.createPrksSyncRuntime({ store, online: () => true, request, reconcile });
}

/* PHASE A -- reachability is observed, never assumed.
 *
 * The regression this pins: the coordinator could claim and send a pending
 * operation on a fresh page before any probe had answered. A claim moves
 * attempt_count off 0, and from that moment the client must assume the server
 * may already hold the operation -- so it may no longer cancel or coalesce it
 * locally. A user who edits offline, reloads, and changes their mind would
 * then hit `scope_busy` over a request that never left the machine. */
async function connectivity() {
    const factory = createFakeIndexedDBFactory();
    const store = createPrksLocalStore({ indexedDB: factory, uuid });
    let reachable = false;
    const offline = createPrksOfflineRuntime({
        store: createPrksOfflineStore({ indexedDB: factory }), window: null,
        setTimeout: () => null, clearTimeout: () => {},
        prksRequest: async () => {
            if (!reachable) throw new Error('unreachable');
            return { ok: true, status: 200, json: async () => [] };
        },
    });
    assert.equal(offline.getState(), 'online', 'startup state is provisional, not observed');

    let runtime = null;
    const gate = globalThis.createPrksConnectivityGate(offline.subscribe, offline.getState,
        () => { if (runtime) runtime.changed(); });
    assert.equal(gate(), false, 'provisional online is not an observed reachability result');

    const op = await store.coalesceWorkTag('W-G', tag.id, true, false, 0, tag);
    assert.equal(op.attempt_count, 0);
    let sent = 0;
    runtime = globalThis.createPrksSyncRuntime({ store, online: gate,
        request: async () => { sent += 1; return { ok: true, status: 200, json: async () => ({ ...ack, work_id: 'W-G' }) }; },
        reconcile: async () => true,
    });

    await runtime.wake();
    await settle();
    let row = await store.getOperation(op.op_id);
    assert.equal(sent, 0, 'no /api/sync/operations request before connectivity is observed');
    assert.equal(row.status, 'pending');
    assert.equal(row.attempt_count, 0, 'the operation is still never-sent');
    assert.equal(row.last_attempt_at, null);

    // An observed OFFLINE result must leave it never-sent too.
    offline.init();
    await waitFor(() => offline.getState() === 'offline', 'an observed offline result');
    await settle();
    assert.equal(gate(), false);
    row = await store.getOperation(op.op_id);
    assert.equal(sent, 0);
    assert.equal(row.attempt_count, 0);
    assert.equal(row.last_attempt_at, null);

    // ...which is the whole point: the opposite intent still cancels the
    // operation locally instead of failing with scope_busy.
    assert.equal(await store.coalesceWorkTag('W-G', tag.id, false, false, 0, tag), null);
    assert.deepEqual(await store.listOperations(), [], 'an unsent operation stays coalescible');

    // An observed ONLINE result wakes the coordinator and sends normally.
    const resumed = await store.coalesceWorkTag('W-G', tag.id, true, false, 0, tag);
    reachable = true;
    offline.noteRequestSuccess();
    assert.equal(offline.getState(), 'online');
    assert.equal(gate(), true);
    await waitFor(() => sent === 1, 'observed reachability releasing the queue');
    await waitFor(async () => (await store.getOperation(resumed.op_id)) === null,
        'the acknowledged operation being retired');
    runtime.stop();
}

/* PHASE B -- transport failures return intent to a retryable pending row.
 * None of these is a conflict, and none may mint a new op_id: only the server
 * ledger knows whether the ORIGINAL envelope applied.
 *
 * Each case gets its own store so the exponential retry backoff -- which is
 * itself asserted below -- cannot suppress the send under test. */
async function transport() {
    const failures = [
        ['network exception', () => { throw new TypeError('Failed to fetch'); }],
        ['aborted request', () => { const e = new Error('aborted'); e.name = 'AbortError'; throw e; }],
        ['HTTP 500', async () => ({ ok: false, status: 500, json: async () => ack })],
        ['HTTP 503 with no body', async () => ({ ok: false, status: 503, json: async () => { throw new SyntaxError('empty'); } })],
        ['malformed JSON on 200', async () => ({ ok: true, status: 200, json: async () => { throw new SyntaxError('nope'); } })],
        ['unexpected JSON on 200', async () => ({ ok: true, status: 200, json: async () => ({ hello: 'world' }) })],
        ['ACK for another relationship', async () => ({ ok: true, status: 200, json: async () => ({ ...ack, work_id: 'W-T', tag_id: 'T-OTHER' }) })],
        ['ACK contradicting the requested state', async () => ({ ok: true, status: 200, json: async () => ({ ...ack, work_id: 'W-T', present: false }) })],
        ['ACK with a non-integer revision', async () => ({ ok: true, status: 200, json: async () => ({ ...ack, work_id: 'W-T', server_revision: 'one' }) })],
    ];
    for (const [label, handler] of failures) {
        const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
        const op = await store.coalesceWorkTag('W-T', tag.id, true, false, 0, tag);
        let calls = 0;
        const runtime = syncRuntime(store, async (path, init) => {
            calls += 1;
            assert.equal(path, '/api/sync/operations');
            assert.equal(JSON.parse(init.body).op_id, op.op_id);
            return handler();
        }, async () => { throw new Error('a failed transport must not reconcile'); });
        await runtime.wake(); await settle();
        const row = await store.getOperation(op.op_id);
        assert.equal(calls, 1, label + ' was attempted once');
        assert.equal(row.status, 'pending', label + ' stays retryable rather than conflicted');
        assert.equal(row.attempt_count, 1, label + ' counts the attempt');
        assert(!row.server_result, label + ' records no semantic result');
        assert(typeof row.last_error === 'string' && row.last_error.length <= 500, label + ' bounds last_error');
        assert.deepEqual({ operation: row.operation, entity_id: row.entity_id, payload: row.payload,
            base_revision: row.base_revision, created_at: row.created_at, depends_on: row.depends_on },
            { operation: op.operation, entity_id: op.entity_id, payload: op.payload,
                base_revision: op.base_revision, created_at: op.created_at, depends_on: op.depends_on },
            label + ' leaves the semantic envelope untouched');

        // The retry is DEFERRED, not abandoned: an immediate re-drain sends
        // nothing, and the row is still the queue's next pending operation.
        await runtime.wake(); await settle();
        assert.equal(calls, 1, label + ' backs off instead of hot-looping');
        assert.equal((await store.getOperation(op.op_id)).attempt_count, 1);
        assert.equal((await store.listOperations({ status: 'pending' }))[0].op_id, op.op_id,
            label + ' remains queued for retry');
        runtime.stop();
    }

    /* Recognized terminal protocol errors must not enter a transport retry
     * loop; they persist a bounded structured result for the user instead. */
    for (const code of ['OP_ID_REUSE', 'INVALID_ENVELOPE', 'INVALID_BASE_REVISION', 'UNSUPPORTED_DEPENDENCIES']) {
        const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
        const op = await store.coalesceWorkTag('W-T', tag.id, true, false, 0, tag);
        let calls = 0;
        const runtime = syncRuntime(store,
            async () => { calls += 1; return { ok: false, status: code === 'OP_ID_REUSE' ? 409 : 400, json: async () => ({ code }) }; },
            async () => { throw new Error('must not reconcile a protocol error'); });
        await runtime.wake(); await settle(); runtime.stop();
        const row = await store.getOperation(op.op_id);
        assert.equal(calls, 1, code + ' is not retried as a transport failure');
        assert.equal(row.status, 'conflict', code + ' is terminal, not pending');
        assert.deepEqual(row.server_result, { code }, code + ' persists a bounded structured result');
    }

    /* FUTURE_REVISION is a domain outcome, not a transport fault. */
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    const op = await store.coalesceWorkTag('W-T', tag.id, true, false, 0, tag);
    const runtime = syncRuntime(store, async () => ({ ok: false, status: 400, json: async () => ({
        work_id: 'W-T', tag_id: tag.id, code: 'FUTURE_REVISION',
        current_revision: 0, current_state: false, requested_state: true }) }),
        async () => { throw new Error('must not reconcile a conflict'); });
    await runtime.wake(); await settle(); runtime.stop();
    const row = await store.getOperation(op.op_id);
    assert.equal(row.status, 'conflict');
    assert.deepEqual(row.server_result, { code: 'FUTURE_REVISION', current_revision: 0,
        current_state: false, requested_state: true });
}

/* PHASE C/D -- the acknowledged-operation lifecycle and the cache-write race.
 * Retirement happens only AFTER reconciliation and the live ACK, so a crash
 * anywhere earlier leaves a replayable row rather than a lost edit. */
async function lifecycle() {
    const factory = createFakeIndexedDBFactory();
    const store = createPrksLocalStore({ indexedDB: factory, uuid });
    const cache = createPrksOfflineStore({ indexedDB: factory });
    await cache.putEntity('work', 'W-L', { id: 'W-L', title: 'Lifecycle', tags: [] });
    await cache.putEntity('work-tag-options', 'W-L', { work_id: 'W-L', assigned: [], known_absent: {} });
    const offline = createPrksOfflineRuntime({ store: cache, window: null,
        prksRequest: async () => { throw new Error('no reads in this scenario'); } });
    const acked = { ...ack, work_id: 'W-L' };

    let cacheWorks = false;
    const order = [];
    const sent = [];
    const runtime = syncRuntime(store,
        async (path, init) => { sent.push(JSON.parse(init.body).op_id); return { ok: true, status: 200, json: async () => acked }; },
        async result => { order.push('reconcile'); return cacheWorks && offline.reconcileWorkTag(result); });
    runtime.subscribe(event => { if (event.acknowledged) order.push('ack'); });

    const op = await store.coalesceWorkTag('W-L', tag.id, true, false, 0, tag);
    // The server committed, but the cache write failed. The durable operation
    // must survive with its identity intact.
    await runtime.wake(); await settle();
    let row = await store.getOperation(op.op_id);
    assert.equal(row.status, 'pending', 'a failed reconciliation keeps the operation retryable');
    assert.equal(row.op_id, op.op_id, 'the same op_id is retained');
    assert.deepEqual(order, ['reconcile'], 'no ACK reaches the UI without a committed reconciliation');

    // Restore cache writes. Clearing attempt_count models the scheduled retry
    // timer elapsing; it is the backoff, not the protocol, being skipped here.
    cacheWorks = true;
    await store.updateOperationSyncState(op.op_id, { attempt_count: 0 });
    await runtime.wake(); await settle();
    assert.deepEqual(sent, [op.op_id, op.op_id], 'the retry replays the same op_id -- never a replacement');
    assert.deepEqual(order, ['reconcile', 'reconcile', 'ack'], 'reconcile, then ACK, then retire');
    assert.equal(await store.getOperation(op.op_id), null, 'a completed operation is retired locally');
    assert.deepEqual((await cache.getEntity('work', 'W-L')).value.tags.map(t => t.id), [tag.id],
        'the server relationship was applied exactly once and the cache reflects it');
    assert.equal((await cache.getEntity('work-tag-options', 'W-L')).value.assigned[0].relation_revision, 1,
        'the revision advanced exactly once across the replay');
    assert.equal((await store.stats()).byStatus.acknowledged, 0, 'no unbounded local history accumulates');
    runtime.stop();

    /* Crash residue: a row acknowledged but not yet retired is cleaned at
     * startup and is NOT resent -- its reconciliation already committed. */
    const residue = await store.coalesceWorkTag('W-L', tag.id, false, true, 1, tag);
    await store.updateOperationSyncState(residue.op_id, { status: 'acknowledged', server_revision: 2 });
    let resent = 0;
    const restarted = syncRuntime(store,
        async () => { resent += 1; return { ok: true, status: 200, json: async () => acked }; },
        async () => true);
    await restarted.wake(); await settle(); restarted.stop();
    assert.equal(resent, 0, 'an acknowledged row is residue, not work');
    assert.equal(await store.getOperation(residue.op_id), null, 'startup retires acknowledged residue');

    /* Recovering a `syncing` row must NOT reset its attempt count. It may
     * already be ledgered on the server, so it stays non-coalescible until it
     * settles -- exactly the property the connectivity gate protects for rows
     * that were never sent at all. */
    const interrupted = await store.coalesceWorkTag('W-L', tag.id, true, false, 0, tag);
    await store.claimOperation(interrupted.op_id);
    const resumeRuntime = syncRuntime(store, async () => { throw new TypeError('offline'); }, async () => true);
    await resumeRuntime.wake(); await settle(); resumeRuntime.stop();
    const resumed = await store.getOperation(interrupted.op_id);
    assert.equal(resumed.status, 'pending');
    assert(resumed.attempt_count >= 1, 'a resumed row remembers it may already have been sent');
    await assert.rejects(store.coalesceWorkTag('W-L', tag.id, false, true, 1, tag),
        err => err.prksLocalStoreCode === 'scope_busy');
}

/* PHASE D -- a read that began before an ACK cannot publish over it.
 * Both cached read models are fenced by the same publication generation, so
 * one race covers the Work detail and the tag-options projection together. */
async function staleReads() {
    const factory = createFakeIndexedDBFactory();
    const cache = createPrksOfflineStore({ indexedDB: factory });
    const stale = { work_id: 'W-S', assigned: [{ tag_id: tag.id, relation_revision: 5 }], known_absent: {} };
    await cache.putEntity('work', 'W-S', { id: 'W-S', title: 'Race', tags: [] });
    await cache.putEntity('work-tag-options', 'W-S', { work_id: 'W-S', assigned: [], known_absent: {} });
    let release = null;
    const inFlight = new Promise(resolve => { release = resolve; });
    const offline = createPrksOfflineRuntime({ store: cache, window: null, prksRequest: async path => {
        await inFlight;
        return { ok: true, status: 200, json: async () => (path.indexOf('tag-options') === -1
            ? { id: 'W-S', title: 'Race', tags: [] } : stale) };
    } });

    // Two GETs begin while the server still holds the pre-ACK state...
    const options = offline.readThroughEntity('work-tag-options', 'W-S', '/api/works/W-S/tag-options',
        { validate: v => globalThis.prksIsWorkTagOptionsShape(v, 'W-S') });
    const detail = offline.readThroughEntity('work', 'W-S', '/api/works/W-S');
    await settle();

    // ...and an ACK at revision 6 reconciles while they are still open.
    assert.equal(await offline.reconcileWorkTag({ ...ack, work_id: 'W-S', server_revision: 6 }), true);
    assert.deepEqual((await cache.getEntity('work-tag-options', 'W-S')).value.assigned,
        [{ tag_id: tag.id, relation_revision: 6 }]);

    release();
    await options; await detail;
    await settle();
    assert.deepEqual((await cache.getEntity('work-tag-options', 'W-S')).value.assigned,
        [{ tag_id: tag.id, relation_revision: 6 }], 'a stale tag-options response cannot beat the ACK');
    assert.deepEqual((await cache.getEntity('work', 'W-S')).value.tags.map(t => t.id), [tag.id],
        'a stale Work detail response cannot beat the ACK');

    // An ACK older than what the cache already holds changes nothing.
    assert.equal(await offline.reconcileWorkTag({ ...ack, work_id: 'W-S', server_revision: 4, present: false }), true);
    assert.deepEqual((await cache.getEntity('work-tag-options', 'W-S')).value.assigned,
        [{ tag_id: tag.id, relation_revision: 6 }], 'a superseded ACK is not applied');
}

/* PHASE L -- two store instances over one database: the two-tab case with no
 * Web Locks available. Exactly one may claim an operation. */
async function claimRace() {
    const factory = createFakeIndexedDBFactory();
    const a = createPrksLocalStore({ indexedDB: factory, uuid });
    const b = createPrksLocalStore({ indexedDB: factory, uuid });
    const op = await a.coalesceWorkTag('W-R', tag.id, true, false, 0, tag);
    const claims = await Promise.all([a.claimOperation(op.op_id), b.claimOperation(op.op_id)]);
    assert.equal(claims.filter(Boolean).length, 1, 'exactly one claimant wins');
    assert.equal(claims.filter(c => c === null).length, 1, 'the loser is told null, not handed a duplicate');
    const row = await b.getOperation(op.op_id);
    assert.equal(row.status, 'syncing');
    assert.equal(row.attempt_count, 1, 'a lost claim does not double-count the attempt');
    assert.equal(await a.claimOperation(op.op_id), null, 'a syncing row cannot be re-claimed');
}

main()
    // The coordinator legitimately keeps a retry timer armed; exit on the
    // result rather than waiting for the event loop to drain.
    .then(() => process.exit(0))
    .catch(error => { console.error(error); process.exit(1); });
