#!/usr/bin/env node
'use strict';

/* Deterministic coverage for the DURABLE local store (local-store.js).
 *
 * The property under test throughout is the one that separates this module
 * from the disposable offline cache: a write either committed or the caller
 * was told it failed. There is no silent degradation here, because a silently
 * dropped write is a silently lost user change.
 *
 * Uses the shared in-memory fake IndexedDB so request-vs-transaction semantics
 * are exercised for real rather than asserted against source strings.
 */

const path = require('path');
const fs = require('fs');
const rootDir = path.resolve(__dirname, '../..');
const { createFakeIndexedDBFactory } = require(path.join(rootDir, 'tests/browser/lib/fake_indexeddb.js'));

let passed = 0;
let failed = 0;

function record(name, ok, detail) {
    if (ok) passed += 1;
    else failed += 1;
    console.log((ok ? 'PASS  ' : 'FAIL  ') + name + (detail ? ' ' + detail : ''));
}

function assertEq(name, got, want) {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    record(name, ok, ok ? '' : 'got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want));
}

function assert(name, ok, detail) {
    record(name, !!ok, ok ? '' : detail || '');
}

async function assertRejects(name, promise, expectedCode) {
    try {
        await promise;
        record(name, false, 'expected rejection, got success');
    } catch (e) {
        const code = e && e.prksLocalStoreCode;
        record(name, !expectedCode || code === expectedCode,
            expectedCode && code !== expectedCode ? 'got code=' + String(code) : '');
    }
}

let uuidCounter = 0;
function seqUuid() {
    uuidCounter += 1;
    // Deterministic but shaped like a real UUID: the last group must be
    // exactly 12 hex characters or the envelope validator rejects it.
    const tail = ('00000000000' + uuidCounter.toString(16)).slice(-12);
    return '00000000-0000-4000-8000-' + tail;
}

function loadLocalStore() {
    return require(path.join(rootDir, 'frontend/js/local-store.js'));
}

function tagOp(workId, tagId, extra) {
    return Object.assign(
        { operation: 'ADD_WORK_TAG', entity_type: 'work', entity_id: workId, payload: { tag_id: tagId } },
        extra || {}
    );
}

async function run() {
    const mod = loadLocalStore();

    /* ---- physical separation from the disposable cache ---- */
    {
        assertEq('durable state uses its own database name', mod.PRKS_LOCAL_DB_NAME, 'prks-local-v1');
        const offline = require(path.join(rootDir, 'frontend/js/offline-store.js'));
        assert('local DB name differs from the offline cache DB name',
            mod.PRKS_LOCAL_DB_NAME !== 'prks-offline-v1');
        assert('offline cache still owns its own database',
            typeof offline.createPrksOfflineStore === 'function');
    }

    /* ---- device identity ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });
        const first = await store.getOrCreateDeviceId();
        assert('device id is created on first use', typeof first === 'string' && first.length > 0);
        const second = await store.getOrCreateDeviceId();
        assertEq('device id is stable within one store instance', second, first);

        // A brand-new store instance models a page reload / browser restart.
        const reopened = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });
        assertEq('device id survives a new store instance', await reopened.getOrCreateDeviceId(), first);
    }

    /* ---- enqueue / read / durable ordering ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });
        const deviceId = await store.getOrCreateDeviceId();

        const a = await store.enqueueOperation(tagOp('W-1', 'T-1'), deviceId);
        const b = await store.enqueueOperation(tagOp('W-1', 'T-2'), deviceId);
        const c = await store.enqueueOperation(
            { operation: 'MARK_WORK_OPENED', entity_type: 'work', entity_id: 'W-2' }, deviceId);

        assert('enqueue returns the stored envelope', !!a.op_id && a.status === 'pending');
        assertEq('device id is recorded on the operation', a.device_id, deviceId);
        assertEq('payload is preserved', a.payload, { tag_id: 'T-1' });
        assertEq('sequence is monotonic', [a.sequence, b.sequence, c.sequence], [1, 2, 3]);
        assert('op ids are distinct', a.op_id !== b.op_id && b.op_id !== c.op_id);
        assertEq('MARK_WORK_OPENED defaults its payload', c.payload, {});
        assert('occurred_at defaults to creation time', typeof c.occurred_at === 'string');

        const listed = await store.listOperations();
        assertEq('listOperations returns durable sequence order',
            listed.map((r) => r.op_id), [a.op_id, b.op_id, c.op_id]);

        const fetched = await store.getOperation(b.op_id);
        assertEq('getOperation round-trips', fetched.payload, { tag_id: 'T-2' });
        assertEq('getOperation returns null for an unknown id', await store.getOperation('nope'), null);

        // A fresh instance over the same database: the reload case.
        const reopened = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });
        const afterReload = await reopened.listOperations();
        assertEq('operations survive a new store instance',
            afterReload.map((r) => r.op_id), [a.op_id, b.op_id, c.op_id]);
        assertEq('sequence continues after reload',
            (await reopened.enqueueOperation(tagOp('W-3', 'T-9'), deviceId)).sequence, 4);
    }

    /* ---- envelope validation ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });

        await assertRejects('unregistered operation type is refused',
            store.enqueueOperation({ operation: 'DROP_DATABASE', entity_type: 'work', entity_id: 'W-1' }),
            'unknown_operation');
        await assertRejects('missing entity_type is refused',
            store.enqueueOperation({ operation: 'ADD_WORK_TAG', entity_id: 'W-1' }),
            'invalid_envelope');
        await assertRejects('missing entity_id is refused',
            store.enqueueOperation({ operation: 'ADD_WORK_TAG', entity_type: 'work' }),
            'invalid_envelope');
        await assertRejects('array payload is refused',
            store.enqueueOperation(tagOp('W-1', 'T-1', { payload: [] })),
            'invalid_envelope');
        await assertRejects('non-integer base_revision is refused',
            store.enqueueOperation(tagOp('W-1', 'T-1', { base_revision: 'seventeen' })),
            'invalid_envelope');
        await assertRejects('non-array depends_on is refused',
            store.enqueueOperation(tagOp('W-1', 'T-1', { depends_on: 'other-op' })),
            'invalid_envelope');
        await assertRejects('a non-UUID op_id is refused',
            store.enqueueOperation(tagOp('W-1', 'T-1', { op_id: 'W-SHORT' })),
            'invalid_envelope');
        await assertRejects('an oversized payload is refused',
            store.enqueueOperation(tagOp('W-1', 'T-1', { payload: { blob: 'x'.repeat(70000) } })),
            'payload_too_large');

        assertEq('no refused operation reached durable storage',
            (await store.listOperations()).length, 0);
    }

    /* ---- duplicate op_id ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });
        const fixed = '11111111-2222-4333-8444-555555555555';
        await store.enqueueOperation(tagOp('W-1', 'T-1', { op_id: fixed }));
        await assertRejects('re-enqueueing the same op_id is refused',
            store.enqueueOperation(tagOp('W-1', 'T-2', { op_id: fixed })),
            'duplicate_op_id');
        const rows = await store.listOperations();
        assertEq('the original row is untouched by the refused duplicate',
            [rows.length, rows[0].payload.tag_id], [1, 'T-1']);
    }

    /* ---- sync-state transitions ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });
        const op = await store.enqueueOperation(tagOp('W-1', 'T-1'));

        const syncing = await store.updateOperationSyncState(op.op_id, {
            status: 'syncing', bump_attempt: true,
        });
        assertEq('status advances to syncing', syncing.status, 'syncing');
        assertEq('attempt count bumps', syncing.attempt_count, 1);
        assert('last_attempt_at is recorded', typeof syncing.last_attempt_at === 'string');

        const retried = await store.updateOperationSyncState(op.op_id, {
            status: 'pending', last_error: 'network unreachable',
        });
        assertEq('a retryable failure returns to pending', retried.status, 'pending');
        assertEq('the error is retained for diagnostics', retried.last_error, 'network unreachable');

        const acked = await store.updateOperationSyncState(op.op_id, {
            status: 'acknowledged', server_revision: 18,
        });
        assertEq('acknowledgement records the server revision', acked.server_revision, 18);
        assert('acknowledged_at is stamped', typeof acked.acknowledged_at === 'string');

        await assertRejects('an unknown status is refused',
            store.updateOperationSyncState(op.op_id, { status: 'teleported' }), 'invalid_status');
        await assertRejects('updating an unknown operation is refused',
            store.updateOperationSyncState('00000000-0000-4000-8000-000000000999', { status: 'pending' }),
            'not_found');

        const long = 'e'.repeat(2000);
        const truncated = await store.updateOperationSyncState(op.op_id, { last_error: long });
        assert('last_error is bounded', truncated.last_error.length <= 500);
    }

    /* ---- the semantic envelope is immutable ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });
        const op = await store.enqueueOperation(tagOp('W-1', 'T-1', { base_revision: 7 }));
        await store.updateOperationSyncState(op.op_id, {
            status: 'syncing',
            operation: 'REMOVE_WORK_TAG',
            entity_id: 'W-OTHER',
            payload: { tag_id: 'T-EVIL' },
            base_revision: 99,
            created_at: 'rewritten',
            depends_on: ['x'],
        });
        const after = await store.getOperation(op.op_id);
        assertEq('operation type cannot be rewritten', after.operation, 'ADD_WORK_TAG');
        assertEq('entity cannot be rewritten', after.entity_id, 'W-1');
        assertEq('payload cannot be rewritten', after.payload, { tag_id: 'T-1' });
        assertEq('base_revision cannot be rewritten', after.base_revision, 7);
        assertEq('created_at cannot be rewritten', after.created_at, op.created_at);
        assertEq('dependencies cannot be rewritten', after.depends_on, []);
    }

    /* ---- deletion is restricted to acknowledged operations ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });
        const pending = await store.enqueueOperation(tagOp('W-1', 'T-1'));
        await assertRejects('a pending operation cannot be discarded',
            store.deleteAcknowledgedOperation(pending.op_id), 'not_acknowledged');
        await store.updateOperationSyncState(pending.op_id, { status: 'conflict' });
        await assertRejects('a conflicted operation cannot be discarded',
            store.deleteAcknowledgedOperation(pending.op_id), 'not_acknowledged');
        await store.updateOperationSyncState(pending.op_id, { status: 'acknowledged' });
        assertEq('an acknowledged operation is removable',
            await store.deleteAcknowledgedOperation(pending.op_id), true);
        assertEq('the row is gone', await store.getOperation(pending.op_id), null);
    }

    /* ---- TRANSACTION COMMIT SEMANTICS ----
     * The central durability property: a request that succeeded inside a
     * transaction that then aborted must be reported as a FAILURE, and the row
     * must not be readable afterwards. An offline cache may shrug this off; a
     * user's unsynchronized change may not. */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });
        await store.enqueueOperation(tagOp('W-1', 'T-1'));

        const db = idb.__databases.get('prks-local-v1');
        db._stores.get('operations').failCommit = true;
        await assertRejects('enqueue rejects when the transaction aborts after the request succeeded',
            store.enqueueOperation(tagOp('W-2', 'T-2')), 'write_failed');
        db._stores.get('operations').failCommit = false;

        const rows = await store.listOperations();
        assertEq('the rolled-back operation is not readable', rows.length, 1);
        assertEq('only the committed operation survives', rows[0].entity_id, 'W-1');
        assertEq('the rolled-back sequence was not consumed',
            (await store.enqueueOperation(tagOp('W-3', 'T-3'))).sequence, 2);
    }

    /* ---- request-level failure ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });
        await store.enqueueOperation(tagOp('W-1', 'T-1'));
        const db = idb.__databases.get('prks-local-v1');
        db._stores.get('operations').forceError = true;
        await assertRejects('enqueue rejects when the request itself fails',
            store.enqueueOperation(tagOp('W-2', 'T-2')), 'write_failed');
        db._stores.get('operations').forceError = false;
        assertEq('nothing partial was stored', (await store.listOperations()).length, 1);
    }

    /* ---- storage entirely unavailable ---- */
    {
        const store = mod.createPrksLocalStore({ indexedDB: null, uuid: seqUuid });
        assertEq('isAvailable reports false without IndexedDB', await store.isAvailable(), false);
        await assertRejects('enqueue rejects rather than silently dropping the change',
            store.enqueueOperation(tagOp('W-1', 'T-1')), 'unavailable');
        await assertRejects('device id creation rejects too',
            store.getOrCreateDeviceId(), 'unavailable');
    }

    /* ---- stats ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });
        const a = await store.enqueueOperation(tagOp('W-1', 'T-1'));
        await store.enqueueOperation(tagOp('W-1', 'T-2'));
        await store.updateOperationSyncState(a.op_id, { status: 'conflict' });
        const s = await store.stats();
        assertEq('stats counts by status', [s.total, s.byStatus.pending, s.byStatus.conflict],
            [2, 1, 1]);
        assertEq('pendingTotal excludes acknowledged only', s.pendingTotal, 2);
        assert('stats reports an approximate size', s.approxBytes > 0);
        const filtered = await store.listOperations({ status: 'conflict' });
        assertEq('listOperations filters by status', filtered.map((r) => r.op_id), [a.op_id]);
    }

    /* ---- INDEPENDENCE FROM THE DISPOSABLE CACHE ----
     * Clearing or deleting the offline cache database must be incapable of
     * touching durable local state. This is why they are separate databases. */
    {
        const idb = createFakeIndexedDBFactory();
        const offlineMod = require(path.join(rootDir, 'frontend/js/offline-store.js'));
        const cache = offlineMod.createPrksOfflineStore({ indexedDB: idb });
        const local = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });

        const deviceId = await local.getOrCreateDeviceId();
        const op = await local.enqueueOperation(tagOp('W-1', 'T-1'), deviceId);
        await cache.putEntity('work', 'W-1', { id: 'W-1', title: 'Cached' }, '');
        assert('cache holds the work snapshot', !!(await cache.getEntity('work', 'W-1')));

        assertEq('clearAll reports success', await cache.clearAll(), true);
        assertEq('the cached snapshot is gone', await cache.getEntity('work', 'W-1'), null);
        assertEq('the pending operation survives clearAll',
            (await local.getOperation(op.op_id)).op_id, op.op_id);
        assertEq('the device id survives clearAll', await local.getOrCreateDeviceId(), deviceId);

        // Even destroying the whole cache database leaves durable state alone.
        await cache.deleteDatabase();
        assert('the offline database is gone', !idb.__databases.has('prks-offline-v1'));
        assert('the local database is untouched', idb.__databases.has('prks-local-v1'));
        const reopened = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });
        assertEq('operations survive deletion of the cache database',
            (await reopened.listOperations()).map((r) => r.op_id), [op.op_id]);
        assertEq('device id survives deletion of the cache database',
            await reopened.getOrCreateDeviceId(), deviceId);
    }

    /* ---- explicit durable reset is the ONLY way to drop local state ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });
        await store.enqueueOperation(tagOp('W-1', 'T-1'));
        assertEq('reset succeeds', await store.resetDurableLocalState(), true);
        const fresh = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });
        assertEq('operations are gone after an explicit reset',
            (await fresh.listOperations()).length, 0);
    }

    /* ---- module hygiene: persistence only ---- */
    {
        const src = fs.readFileSync(path.join(rootDir, 'frontend/js/local-store.js'), 'utf8');
        assert('local-store.js has no fetch() calls', !/\bfetch\s*\(/.test(src));
        assert('local-store.js has no prksRequest usage', src.indexOf('prksRequest') === -1);
        assert('local-store.js has no prksNavigate usage', src.indexOf('prksNavigate') === -1);
        assert('local-store.js does not touch the DOM', !/\bdocument\./.test(src));
        assert('local-store.js does not read connectivity state',
            src.indexOf('prksOfflineRuntimeState') === -1);
        // The header comment names the cache DB to explain the separation;
        // what must not exist is a code reference that could reach it.
        const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '');
        assert('local-store.js never references the disposable cache database in code',
            code.indexOf('prks-offline-v1') === -1);
        assert('local-store.js never calls the offline cache store',
            code.indexOf('createPrksOfflineStore') === -1);
    }

    console.log('All ' + passed + ' local store checks passed, ' + failed + ' failed');
    if (failed > 0) process.exit(1);
}

run().catch(function (e) {
    console.error(e);
    process.exit(1);
});
