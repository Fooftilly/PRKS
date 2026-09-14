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

        const a = await store.enqueueOperation(tagOp('W-1', 'T-1'));
        const b = await store.enqueueOperation(tagOp('W-1', 'T-2'));
        const c = await store.enqueueOperation(
            { operation: 'MARK_WORK_OPENED', entity_type: 'work', entity_id: 'W-2' });

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
            (await reopened.enqueueOperation(tagOp('W-3', 'T-9'))).sequence, 4);
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
        const op = await local.enqueueOperation(tagOp('W-1', 'T-1'));
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

    /* ---- explicit durable reset: the ONLY way to drop local state ----
     * It must also close THIS store's own connection first. IndexedDB blocks
     * deleteDatabase() on every open connection, including the deleting
     * page's own, so a store that never closed its handle blocks itself. The
     * fake models that; a connection in another tab is still a legitimate
     * `blocked` the caller has to handle. */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });
        await store.enqueueOperation(tagOp('W-1', 'T-1'));
        const db = idb.__databases.get('prks-local-v1');
        assert('the store holds an open connection', db._openConnections > 0);

        // Recorded rather than awaited bare: a store that failed to close its
        // own handle rejects with `blocked`, and that must surface as a FAIL
        // rather than aborting the run.
        let outcome;
        try {
            outcome = { ok: await store.resetDurableLocalState() };
        } catch (e) {
            outcome = { ok: false, code: e && e.prksLocalStoreCode };
        }
        assert('reset succeeds despite our own open connection', outcome.ok === true,
            outcome.code ? 'rejected with code=' + outcome.code : '');

        if (outcome.ok === true) {
            assert('the database is really gone', !idb.__databases.has('prks-local-v1'));
            const fresh = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });
            assertEq('operations are gone after an explicit reset',
                (await fresh.listOperations()).length, 0);
            assertEq('the store reopens cleanly after a reset',
                (await store.enqueueOperation(tagOp('W-9', 'T-9'))).sequence, 1);
        }
    }

    /* ---- the store OWNS device identity: null is unrepresentable ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });

        // Enqueue FIRST, with no prior getOrCreateDeviceId() call: the store
        // must mint and attach one itself, in the same transaction.
        const op = await store.enqueueOperation(tagOp('W-1', 'T-1'));
        assert('device_id is attached without the caller supplying one',
            typeof op.device_id === 'string' && op.device_id.length > 0);
        assertEq('the attached id is the durable device id',
            op.device_id, await store.getOrCreateDeviceId());
        assertEq('the stored row carries it too',
            (await store.getOperation(op.op_id)).device_id, op.device_id);

        // The public API takes no device id at all any more.
        assertEq('enqueueOperation accepts exactly one argument',
            store.enqueueOperation.length, 1);

        // A second operation reuses the same identity rather than minting one.
        const second = await store.enqueueOperation(tagOp('W-2', 'T-2'));
        assertEq('device identity is stable across operations', second.device_id, op.device_id);

        // Every persisted row has one -- no null is reachable.
        const rows = await store.listOperations();
        assert('no stored operation has a null device_id',
            rows.every((r) => typeof r.device_id === 'string' && r.device_id.length > 0));
    }

    /* ---- the payload limit is measured in UTF-8 BYTES ----
     * String .length counts UTF-16 code units, so a CJK payload is ~2.1x
     * larger on the wire than .length reports. A limit documented in bytes
     * that is enforced in code units does not exist for the users most
     * likely to reach it. */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });
        const limit = mod.PRKS_LOCAL_MAX_PAYLOAD_BYTES;

        // Three UTF-8 bytes per character: a string whose .length is well
        // under the limit but whose byte length is well over it.
        const cjkChars = Math.ceil(limit / 3) + 100;
        const cjk = { note: '\u65e5'.repeat(cjkChars) };
        assert('the multibyte payload would have passed a .length check',
            JSON.stringify(cjk).length < limit);
        assert('... but genuinely exceeds the byte limit',
            Buffer.byteLength(JSON.stringify(cjk), 'utf8') > limit);
        await assertRejects('an oversized multibyte payload is refused',
            store.enqueueOperation(tagOp('W-1', 'T-1', { payload: cjk })), 'payload_too_large');

        // A multibyte payload that genuinely fits is still accepted.
        const small = { note: '\u65e5'.repeat(10) };
        const ok = await store.enqueueOperation(tagOp('W-2', 'T-2', { payload: small }));
        assertEq('a small multibyte payload round-trips intact', ok.payload, small);

        // Astral characters (surrogate pairs) are 4 bytes, not 2 x 3.
        const astral = { note: '\ud83d\ude80'.repeat(Math.ceil(limit / 4) + 100) };
        await assertRejects('an oversized astral payload is refused',
            store.enqueueOperation(tagOp('W-3', 'T-3', { payload: astral })), 'payload_too_large');
    }

    /* ---- tightened envelope invariants ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });

        await assertRejects('a negative base_revision is refused',
            store.enqueueOperation(tagOp('W-1', 'T-1', { base_revision: -1 })), 'invalid_envelope');
        assertEq('base_revision 0 is legitimate',
            (await store.enqueueOperation(tagOp('W-1', 'T-1', { base_revision: 0 }))).base_revision, 0);

        await assertRejects('a non-timestamp occurred_at is refused',
            store.enqueueOperation(tagOp('W-2', 'T-2', { occurred_at: 'yesterday-ish' })),
            'invalid_envelope');
        await assertRejects('an empty occurred_at is refused',
            store.enqueueOperation(tagOp('W-2', 'T-2', { occurred_at: '   ' })), 'invalid_envelope');
        const dated = await store.enqueueOperation(
            tagOp('W-2', 'T-2', { occurred_at: '2026-09-11T16:04:05.123Z' }));
        assertEq('a real ISO timestamp is accepted',
            dated.occurred_at, '2026-09-11T16:04:05.123Z');

        await assertRejects('a non-UUID dependency is refused',
            store.enqueueOperation(tagOp('W-3', 'T-3', { depends_on: ['not-an-op-id'] })),
            'invalid_envelope');
        /* A well-formed id that names NOTHING is refused too.
         *
         * It used to be accepted, which contradicted the documented invariant
         * and was worse than a typo: nothing will ever acknowledge an operation
         * that does not exist, so its dependent could never be sent and the
         * user's change was stranded with no way to see why. Requiring the
         * prerequisite to exist already is also what makes cycles impossible
         * without walking a graph -- a later operation can only ever name an
         * earlier one. */
        await assertRejects('a dependency that names nothing is refused',
            store.enqueueOperation(tagOp('W-3', 'T-3',
                { depends_on: ['22222222-3333-4444-8555-666666666666'] })),
            'invalid_envelope');

        const prerequisite = await store.enqueueOperation(tagOp('W-4', 'T-4'));
        assertEq('a dependency on an existing operation is accepted',
            (await store.enqueueOperation(tagOp('W-3', 'T-3',
                { depends_on: [prerequisite.op_id] }))).depends_on,
            [prerequisite.op_id]);
        await assertRejects('a repeated dependency is refused',
            store.enqueueOperation(tagOp('W-5', 'T-5',
                { depends_on: [prerequisite.op_id, prerequisite.op_id] })),
            'invalid_envelope');
        const selfId = '33333333-4444-4555-8666-777777777777';
        await assertRejects('an operation cannot wait for itself',
            store.enqueueOperation(tagOp('W-6', 'T-6',
                { op_id: selfId, depends_on: [selfId] })),
            'invalid_envelope');
    }

    /* Real transactional coalescing, including a new store after reload. */
    {
        const factory = createFakeIndexedDBFactory();
        const opts = { indexedDB: factory, uuid: seqUuid };
        let store = mod.createPrksLocalStore(opts);
        const tag = { id: 'T-C', name: 'Coalesce', color: '#112233', aliases: [] };
        for (const base of [false, true]) {
            const first = await store.coalesceWorkTag('W-C', tag.id, !base, base, 8, tag);
            await store.coalesceWorkTag('W-C', tag.id, !base, base, 8, tag);
            assertEq('repeated intent has one operation', (await store.listOperations()).length, 1);
            store = mod.createPrksLocalStore(opts);
            await store.coalesceWorkTag('W-C', tag.id, base, base, 8, tag);
            assertEq('opposite intent cancels across reload', (await store.listOperations()).length, 0);
        }
        const op = await store.coalesceWorkTag('W-C', tag.id, true, false, 0, tag);
        await store.claimOperation(op.op_id);
        await assertRejects('syncing cannot coalesce', store.coalesceWorkTag('W-C', tag.id, false, false, 0, tag), 'scope_busy');
        await store.updateOperationSyncState(op.op_id, { status: 'pending' });
        await assertRejects('lost-response pending cannot coalesce', store.coalesceWorkTag('W-C', tag.id, false, false, 0, tag), 'scope_busy');
        await store.updateOperationSyncState(op.op_id, { status: 'conflict', server_result: {
            code: 'REVISION_CONFLICT', current_revision: 3, current_state: false, requested_state: true,
        } });
        await assertRejects('structured result rejects arbitrary fields', store.updateOperationSyncState(op.op_id, { server_result: { raw: 'body' } }), 'invalid_result');
        const replacement = await store.resolveConflict(op.op_id, true);
        assert('explicit reapply creates new id', replacement.op_id !== op.op_id);
        assertEq('explicit reapply uses current server base', replacement.base_revision, 3);
        assertEq('retired conflict removed atomically', await store.getOperation(op.op_id), null);
    }

    /* ---- the byte-limited payload allowance: abstract ----
     *
     * The 64 KiB bound stays the ordinary limit. The fields in BYTE_LIMITS are
     * the exception, and the allowance is granted to an exact operation SHAPE
     * rather than to a size, so no future family inherits a large payload
     * merely by existing. What is bounded is the VALUE, not its JSON encoding.
     * `author_text` has its own section below; this one covers the largest.
     */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });
        const abstractOp = (value) => ({
            operation: 'SET_WORK_METADATA_FIELD', entity_type: 'work', entity_id: 'W-A',
            payload: { field: 'abstract', value }, base_revision: 0,
        });
        const normal = mod.PRKS_LOCAL_MAX_PAYLOAD_BYTES;
        const limit = mod.PRKS_LOCAL_MAX_ABSTRACT_VALUE_BYTES;
        assertEq('the ordinary payload limit is unchanged', normal, 64 * 1024);
        assertEq('the Abstract allowance is the product limit', limit, 1024 * 1024);

        await assertRejects('an ordinary operation over 64 KiB is still refused',
            store.enqueueOperation(tagOp('W-1', 'T-1', { payload: { note: 'x'.repeat(normal + 100) } })),
            'payload_too_large');
        // ...including another metadata field, which has no such allowance.
        await assertRejects('a non-Abstract metadata field gets no allowance',
            store.enqueueOperation({
                operation: 'SET_WORK_METADATA_FIELD', entity_type: 'work', entity_id: 'W-A',
                payload: { field: 'doi', value: 'x'.repeat(normal + 100) }, base_revision: 0,
            }), 'payload_too_large');
        // ...and neither does an Abstract payload of the wrong shape.
        await assertRejects('an extra payload key forfeits the allowance',
            store.enqueueOperation({
                operation: 'SET_WORK_METADATA_FIELD', entity_type: 'work', entity_id: 'W-A',
                payload: { field: 'abstract', value: 'x'.repeat(normal + 100), extra: 1 },
                base_revision: 0,
            }), 'payload_too_large');

        /* THE REGRESSION: 64 KiB to 1 MiB. The editor and the server both
         * accepted this range; durable storage did not, so the save failed at
         * the one step the user was told had already succeeded. */
        for (const size of [100 * 1024, 512 * 1024, limit]) {
            const stored = await store.enqueueOperation(abstractOp('x'.repeat(size)));
            assertEq('an Abstract of ' + Math.round(size / 1024) + ' KiB is stored',
                stored.payload.value.length, size);
        }
        await assertRejects('an Abstract over 1 MiB is refused',
            store.enqueueOperation(abstractOp('x'.repeat(limit + 1))), 'payload_too_large');

        /* The bound is on the VALUE, not the serialized object: every quote and
         * backslash doubles under JSON escaping, so measuring the encoded form
         * would refuse a value that is exactly at the stated limit. */
        const quoted = '"\\'.repeat(limit / 2);
        assertEq('the escaped form is far larger than the value', quoted.length, limit);
        assert('...and its JSON encoding exceeds the limit',
            JSON.stringify({ field: 'abstract', value: quoted }).length > limit);
        const escaped = await store.enqueueOperation(abstractOp(quoted));
        assertEq('an Abstract at the limit is stored whatever it escapes to',
            escaped.payload.value.length, limit);

        // Bytes, not characters: three bytes per CJK character.
        const cjk = '\u65e5'.repeat(Math.floor(limit / 3) + 10);
        assert('the multibyte Abstract is under the limit by character count', cjk.length < limit);
        await assertRejects('...but over it in bytes, so it is refused',
            store.enqueueOperation(abstractOp(cjk)), 'payload_too_large');
        const fits = '\u65e5'.repeat(1000);
        assertEq('a multibyte Abstract that genuinely fits round-trips',
            (await store.enqueueOperation(abstractOp(fits))).payload.value, fits);
    }

    /* ---- the same allowance, derived from a registry rather than a branch ----
     *
     * `author_text` is the second byte-limited field. Before it had a stated
     * limit the server accepted any length and this store decided -- so the
     * same Author name was savable online and impossible offline, which is the
     * split contract the whole design removes.
     */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });
        const authorOp = (value) => ({
            operation: 'SET_WORK_METADATA_FIELD', entity_type: 'work', entity_id: 'W-A',
            payload: { field: 'author_text', value }, base_revision: 0,
        });
        const limits = mod.PRKS_LOCAL_WORK_FIELD_VALUE_BYTES;
        assertEq('the registry holds every byte-limited field',
            Object.keys(limits).sort().join(','), 'abstract,author_text,source_url,title');
        const limit = limits.author_text;
        assertEq('the author_text allowance is the product limit', limit, 64 * 1024);

        for (const size of [10 * 1024, limit]) {
            const stored = await store.enqueueOperation(authorOp('x'.repeat(size)));
            assertEq('an author_text of ' + Math.round(size / 1024) + ' KiB is stored',
                stored.payload.value.length, size);
        }
        await assertRejects('one byte over the limit is refused',
            store.enqueueOperation(authorOp('x'.repeat(limit + 1))), 'payload_too_large');

        /* THE CASE THE ORDINARY BOUND WOULD GET WRONG: a value at exactly the
         * limit whose JSON encoding is twice that. An Author name full of
         * quotation marks must not fail for a reason no user could see. */
        const quoted = '"\\'.repeat(limit / 2);
        assertEq('the value is exactly at the limit', quoted.length, limit);
        assert('...while its JSON encoding is far over it',
            JSON.stringify({ field: 'author_text', value: quoted }).length > limit * 1.5);
        assertEq('it is stored anyway, because the VALUE is what is bounded',
            (await store.enqueueOperation(authorOp(quoted))).payload.value.length, limit);

        // Bytes, not characters.
        const cjk = '\u65e5'.repeat(Math.floor(limit / 3) + 10);
        assert('the multibyte value is under the limit by character count', cjk.length < limit);
        await assertRejects('...but over it in bytes, so it is refused',
            store.enqueueOperation(authorOp(cjk)), 'payload_too_large');

        // The allowance is shape-scoped: it cannot be used to smuggle a payload.
        await assertRejects('an extra payload key forfeits the allowance',
            store.enqueueOperation({
                operation: 'SET_WORK_METADATA_FIELD', entity_type: 'work', entity_id: 'W-A',
                payload: { field: 'author_text', value: 'x'.repeat(70 * 1024), extra: 1 },
                base_revision: 0,
            }), 'payload_too_large');
        await assertRejects('and a field outside the registry gets none of it',
            store.enqueueOperation({
                operation: 'SET_WORK_METADATA_FIELD', entity_type: 'work', entity_id: 'W-A',
                payload: { field: 'publisher', value: 'x'.repeat(70 * 1024) }, base_revision: 0,
            }), 'payload_too_large');
    }

    /* ---- the durable terminal-result bound is a real refusal ----
     *
     * A conflict the store will not accept is worse than either value winning:
     * the settle fails, the coordinator reads that as a failed sync, and the
     * operation goes back to pending -- forever, because the same oversized
     * result arrives on every retry. The user never reaches the conflict UI.
     * The server is what guarantees the fit (see `fit_terminal_result`); this
     * pins that the refusal it is protecting against is real, and exactly
     * where it falls.
     */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksLocalStore({ indexedDB: idb, uuid: seqUuid });
        const enqueue = () => store.enqueueOperation({
            operation: 'SET_WORK_METADATA_FIELD', entity_type: 'work', entity_id: 'W-R',
            payload: { field: 'author_text', value: 'mine' }, base_revision: 0,
        });
        /* Exactly the shape the store receives: the handler's `terminal()`
         * projects the server's answer down to the allowlisted keys first, so
         * `work_id` and `field` never reach durable storage. */
        const conflict = (preview) => ({
            code: 'REVISION_CONFLICT', current_revision: 4, current_preview: preview,
            current_bytes: 5000, requested_bytes: 4,
        });

        // 400 characters of ASCII: what the character cap was designed for.
        const okRow = await enqueue();
        const settled = await store.updateOperationSyncState(okRow.op_id, {
            status: 'conflict', server_result: conflict('A'.repeat(400)) });
        assertEq('an ASCII preview at the character cap is storable',
            settled.server_result.current_preview.length, 400);

        /* THE REGRESSION: the same 400 CHARACTERS, each serializing to six
         * bytes as `\u0001`. The cap was in characters and the bound is in
         * serialized bytes, and those are not the same measurement. */
        const control = '\u0001'.repeat(400);
        assert('400 control characters serialize past the durable bound',
            Buffer.byteLength(JSON.stringify(conflict(control)), 'utf8') > 2048);
        const badRow = await enqueue();
        await assertRejects('...so the store refuses it, and the conflict never lands',
            store.updateOperationSyncState(badRow.op_id, {
                status: 'conflict', server_result: conflict(control) }),
            'invalid_result');
        const untouched = await store.getOperation(badRow.op_id);
        assert('the operation is left exactly as it was', !untouched.server_result);
        assertEq('...and never became conflicted', untouched.status, 'pending');

        // The server's bounded answer for that same input does land.
        const fitted = await enqueue();
        const okConflict = await store.updateOperationSyncState(fitted.op_id, {
            status: 'conflict', server_result: conflict('\u0001'.repeat(300)) });
        assertEq('a preview shortened to fit is accepted',
            okConflict.status, 'conflict');
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
