'use strict';

const strict = require('assert/strict');
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); }, {
    get: (_t, k) => (...args) => { checks += 1; return strict[k](...args); },
});

const { createFakeIndexedDBFactory } = require('./lib/fake_indexeddb.js');
const { createPrksLocalStore } = require('../../frontend/js/local-store.js');
require('../../frontend/js/pdf-annotation-state.js');

let sequence = 0;
const uuid = () => '00000000-0000-4000-8000-' + (++sequence).toString(16).padStart(12, '0');
const tick = () => new Promise(resolve => setTimeout(resolve, 1));
async function settle() { for (let i = 0; i < 5; i++) await tick(); }

function highlight(id, comment) {
    return {
        id: id,
        type: 9,
        pageIndex: 0,
        contents: comment,
        color: '#FFCD45',
        strokeColor: '#FFCD45',
        opacity: 1,
        blendMode: 'Multiply',
        rect: { origin: { x: 72, y: 700 }, size: { width: 160, height: 14 } },
        segmentRects: [{ origin: { x: 72, y: 700 }, size: { width: 160, height: 14 } }],
        custom: { prksComment: comment },
    };
}

function codecParity() {
    const raw = highlight('ann-1', 'Hello');
    const view = globalThis.prksPdfAnnotationSemanticView(raw);
    assert.equal(view.id, 'ann-1');
    assert.equal(view.content, 'Hello');
    assert.equal(view.custom.prksComment, 'Hello');
    assert.ok(globalThis.prksPdfAnnotationsSemanticallyEqual(raw, globalThis.prksRoundTripPdfAnnotation(raw)));
}

function effectiveOverlay() {
    const ack = [highlight('a', 'A'), highlight('b', 'B')];
    const frozen = JSON.stringify(ack);
    globalThis.prksSetPendingPdfAnnotations([
        {
            operation: 'SET_PDF_ANNOTATION', entity_type: 'work', entity_id: 'W-1',
            status: 'pending', sequence: 1,
            payload: { annotation_id: 'a', annotation: highlight('a', 'A2') },
        },
        {
            operation: 'DELETE_PDF_ANNOTATION', entity_type: 'work', entity_id: 'W-1',
            status: 'pending', sequence: 2, payload: { annotation_id: 'b' },
        },
        {
            operation: 'CREATE_PDF_ANNOTATION', entity_type: 'work', entity_id: 'W-1',
            status: 'pending', sequence: 3,
            payload: { annotation_id: 'c', annotation: highlight('c', 'C') },
        },
    ]);
    const effective = globalThis.prksEffectiveWorkAnnotations(ack, 'W-1');
    assert.equal(JSON.stringify(ack), frozen, 'acknowledged list stays frozen');
    const ids = effective.map(a => a.id).sort();
    assert.deepEqual(ids, ['a', 'c']);
    assert.equal(effective.find(a => a.id === 'a').contents, 'A2');
}

async function coalesceCreateSetDelete() {
    const idb = createFakeIndexedDBFactory();
    const store = createPrksLocalStore({ dbName: 'prks-local-v2:test', indexedDB: idb, uuid: uuid, now: () => Date.now() });
    await store.getOrCreateDeviceId();
    const observedAbsent = { present: false, revision: 0, annotation: null, annotation_id: 'ann-x' };
    const first = highlight('ann-x', 'one');
    await store.savePdfAnnotation('W-1', { annotation_id: 'ann-x', annotation: first }, observedAbsent);
    let rows = await store.listOperations();
    assert.equal(rows.length, 1);
    assert.equal(rows[0].operation, 'CREATE_PDF_ANNOTATION');
    assert.equal(rows[0].base_revision, null);

    const second = highlight('ann-x', 'two');
    await store.savePdfAnnotation('W-1', { annotation_id: 'ann-x', annotation: second }, observedAbsent);
    rows = await store.listOperations();
    assert.equal(rows.length, 1);
    assert.equal(rows[0].operation, 'CREATE_PDF_ANNOTATION');
    assert.equal(rows[0].payload.annotation.contents, 'two');

    await store.savePdfAnnotation('W-1', null, Object.assign({}, observedAbsent, { annotation_id: 'ann-x' }));
    rows = await store.listOperations();
    assert.equal(rows.length, 0, 'CREATE+DELETE cancels');
}

async function coalesceSetCancelAndSentSuccessor() {
    const idb = createFakeIndexedDBFactory();
    const store = createPrksLocalStore({ dbName: 'prks-local-v2:test', indexedDB: idb, uuid: uuid, now: () => Date.now() });
    await store.getOrCreateDeviceId();
    const body = highlight('ann-y', 'base');
    const observed = { present: true, revision: 2, annotation: body, annotation_id: 'ann-y' };
    const edited = highlight('ann-y', 'edit');
    await store.savePdfAnnotation('W-1', { annotation_id: 'ann-y', annotation: edited }, observed);
    let rows = await store.listOperations();
    assert.equal(rows[0].operation, 'SET_PDF_ANNOTATION');
    assert.equal(rows[0].base_revision, 2);

    // A→B→A cancels
    await store.savePdfAnnotation('W-1', { annotation_id: 'ann-y', annotation: body }, observed);
    rows = await store.listOperations();
    assert.equal(rows.length, 0);

    await store.savePdfAnnotation('W-1', { annotation_id: 'ann-y', annotation: edited }, observed);
    rows = await store.listOperations();
    assert.equal(rows.length, 1);
    const firstId = rows[0].op_id;
    await store.claimOperation(firstId);
    await settle();

    // SENT stays immutable; a new desired state becomes a dependent successor.
    const again = highlight('ann-y', 'again');
    const successor = await store.savePdfAnnotation(
        'W-1', { annotation_id: 'ann-y', annotation: again }, observed
    );
    rows = await store.listOperations();
    assert.equal(rows.length, 2, 'SENT + successor');
    const sent = rows.find(r => r.op_id === firstId);
    const dep = rows.find(r => r.op_id === successor.op_id);
    assert.ok(sent);
    assert.ok(dep);
    assert.equal(sent.payload.annotation.contents, 'edit');
    assert.equal(dep.operation, 'SET_PDF_ANNOTATION');
    assert.equal(dep.payload.annotation.contents, 'again');
    assert.equal(dep.base_revision, 3, 'provisional successor base before ACK rebase');
    assert.deepEqual(dep.depends_on, [firstId]);

    // Predecessor ACKs at a far-newer convergent revision (base 2 → server 5).
    // Never-sent successor must be re-enveloped against the actual ACK revision.
    await store.updateOperationSyncState(firstId, {
        status: 'acknowledged', last_error: null, server_revision: 5,
    });
    const replaced = await store.rebasePdfAnnotationDependents(firstId, 5);
    assert.equal(replaced.length, 1);
    rows = await store.listOperations();
    const afterRebase = rows.filter(r => r.status === 'pending');
    assert.equal(afterRebase.length, 1);
    assert.equal(afterRebase[0].base_revision, 5, 'successor wire base is ACK server_revision');
    assert.equal(afterRebase[0].payload.annotation.contents, 'again');
    assert.deepEqual(afterRebase[0].depends_on, [firstId]);
    assert.notEqual(afterRebase[0].op_id, successor.op_id, 're-envelope mints a new op_id');

    // Overlay prefers the latest sequence (successor).
    globalThis.prksSetPendingPdfAnnotations(rows);
    const effective = globalThis.prksEffectiveWorkAnnotations([body], 'W-1');
    assert.equal(effective.find(a => a.id === 'ann-y').contents, 'again');
}

function liveRuntime(id, body, revision) {
    return {
        workId: 'W-1',
        annotationCache: { items: [body], allItems: [body], rawItems: [body] },
        annotationState: { work_id: 'W-1', annotations: [{ annotation_id: id, revision: revision }], known_absent: {} },
    };
}

function runtimeBase(runtime, id) {
    return globalThis.prksAcknowledgedPdfAnnotationBase(runtime.annotationCache.items, runtime.annotationState, id);
}

async function withLiveRuntime(runtime, fn) {
    const prev = globalThis.prksForEachLiveTabContext;
    globalThis.prksForEachLiveTabContext = function (visit) { visit({ getResource: () => runtime }); };
    try {
        return await fn();
    } finally {
        if (prev) globalThis.prksForEachLiveTabContext = prev;
        else delete globalThis.prksForEachLiveTabContext;
    }
}

/* Builds the store the way the app does, and runs `act` once, right after the
 * save's transaction has read the outbox and before it reads the base. */
async function storeActingAfterOutboxRead() {
    const idb = createFakeIndexedDBFactory();
    const store = createPrksLocalStore({ dbName: 'prks-local-v2:test', indexedDB: idb, uuid: uuid, now: () => Date.now() });
    await store.getOrCreateDeviceId();
    /* Record the page's epoch now, so the save's first outbox read is its own
     * and not the epoch move's. */
    await store.advanceEpoch('1');
    const operations = idb.__databases.get('prks-local-v2:test')._stores.get('operations');
    return {
        store,
        once(act) {
            operations.afterGetAll = function () {
                operations.afterGetAll = null;
                act();
            };
        },
    };
}

function writeBase(runtime, id) {
    const captured = runtimeBase(runtime, id);
    return () => globalThis.prksPdfAnnotationBaseForWrite(runtime, id, captured, runtimeBase(runtime, id));
}

function ack(id, body, revision, setRevision) {
    return {
        work_id: 'W-1', annotation_id: id, present: true, annotation: body,
        server_revision: revision, changed: true, canonical_annotation_set_revision: setRevision,
    };
}

/* The back-to-back save E2E flake: the second save's base was read before the
 * first save's ACK reached the runtime, and the write ran after it, so it sent
 * the old revision and the server refused it with a 409 conflict. The base is
 * read inside the write, after the outbox, and this device's own ACK moves it. */
async function ownAckDuringTheWriteMovesTheBase() {
    const runtime = liveRuntime('ann-z', highlight('ann-z', 'third'), 2);
    const { store, once } = await storeActingAfterOutboxRead();
    once(() => globalThis.prksApplyPdfAnnotationAckToLiveRuntimes(
        ack('ann-z', highlight('ann-z', 'fourth'), 3, 7), { op_id: 'op-earlier', operation: 'SET_PDF_ANNOTATION', base_revision: 2 }
    ));
    await withLiveRuntime(runtime, () => store.savePdfAnnotation(
        'W-1', { annotation_id: 'ann-z', annotation: highlight('ann-z', 'fifth') }, writeBase(runtime, 'ann-z')
    ));
    const rows = await store.listOperations();
    assert.equal(rows.length, 1);
    assert.equal(rows[0].operation, 'SET_PDF_ANNOTATION');
    assert.equal(rows[0].base_revision, 3, 'the own ACK that landed during the write is the base');
    assert.equal(rows[0].payload.annotation.contents, 'fifth');
}

/* Another device's edit that a catch-up snapshot loads during the write is not
 * this device's ACK: the write keeps the base the user edited, so the server
 * reports the conflict instead of the save overwriting an edit never seen. */
async function catchUpDuringTheWriteKeepsTheBase() {
    const runtime = liveRuntime('ann-c', highlight('ann-c', 'mine'), 2);
    const { store, once } = await storeActingAfterOutboxRead();
    once(() => {
        const theirs = highlight('ann-c', 'theirs');
        runtime.annotationCache = { items: [theirs], allItems: [theirs], rawItems: [theirs] };
        runtime.annotationState = { work_id: 'W-1', annotations: [{ annotation_id: 'ann-c', revision: 3 }], known_absent: {} };
    });
    await withLiveRuntime(runtime, () => store.savePdfAnnotation(
        'W-1', { annotation_id: 'ann-c', annotation: highlight('ann-c', 'edited') }, writeBase(runtime, 'ann-c')
    ));
    const row = (await store.listOperations()).find(r => r.payload.annotation_id === 'ann-c');
    assert.equal(row.base_revision, 2, 'a snapshot revision is never taken as the base');

    /* Own ACKs chain: 2 -> 3 -> 4 is explained, a snapshot step after it is not. */
    const chained = liveRuntime('ann-k', highlight('ann-k', 'a'), 2);
    const captured = runtimeBase(chained, 'ann-k');
    await withLiveRuntime(chained, () => {
        globalThis.prksApplyPdfAnnotationAckToLiveRuntimes(ack('ann-k', highlight('ann-k', 'b'), 3, 8), { op_id: 'op-1', operation: 'SET_PDF_ANNOTATION', base_revision: 2 });
        globalThis.prksApplyPdfAnnotationAckToLiveRuntimes(ack('ann-k', highlight('ann-k', 'c'), 4, 9), { op_id: 'op-2', operation: 'SET_PDF_ANNOTATION', base_revision: 3 });
    });
    let base = globalThis.prksPdfAnnotationBaseForWrite(chained, 'ann-k', captured, runtimeBase(chained, 'ann-k'));
    assert.equal(base.revision, 4);
    assert.equal(base.acknowledged_op_id, 'op-2');
    chained.annotationState = { work_id: 'W-1', annotations: [{ annotation_id: 'ann-k', revision: 6 }], known_absent: {} };
    base = globalThis.prksPdfAnnotationBaseForWrite(chained, 'ann-k', captured, runtimeBase(chained, 'ann-k'));
    assert.equal(base.revision, 2, 'an unexplained step keeps the captured base');

    /* Another pane's save went out against revision 3, which this pane (still
     * at 2) never saw: its ACK at 4 is not this pane's step from 2. */
    const behind = liveRuntime('ann-b', highlight('ann-b', 'a'), 2);
    const behindBase = runtimeBase(behind, 'ann-b');
    await withLiveRuntime(behind, () => {
        globalThis.prksApplyPdfAnnotationAckToLiveRuntimes(ack('ann-b', highlight('ann-b', 'c'), 4, 12),
            { op_id: 'op-other-pane', operation: 'SET_PDF_ANNOTATION', base_revision: 3 });
    });
    base = globalThis.prksPdfAnnotationBaseForWrite(behind, 'ann-b', behindBase, runtimeBase(behind, 'ann-b'));
    assert.equal(base.revision, 2, 'an ACK sent against an unseen revision keeps the captured base');

    /* A CREATE's ACK starts from a never-held annotation (absent, revision 0). */
    const fresh = { workId: 'W-1', annotationCache: { items: [] },
        annotationState: { work_id: 'W-1', annotations: [], known_absent: {} } };
    const freshBase = runtimeBase(fresh, 'ann-n');
    await withLiveRuntime(fresh, () => {
        globalThis.prksApplyPdfAnnotationAckToLiveRuntimes(ack('ann-n', highlight('ann-n', 'new'), 0, 13),
            { op_id: 'op-create', operation: 'CREATE_PDF_ANNOTATION', base_revision: null });
    });
    base = globalThis.prksPdfAnnotationBaseForWrite(fresh, 'ann-n', freshBase, runtimeBase(fresh, 'ann-n'));
    assert.equal(base.present, true);
    assert.equal(base.acknowledged_op_id, 'op-create');
    /* The same CREATE ACK does not explain a write captured at a tombstone. */
    const tombstone = { annotation_id: 'ann-n', present: false, revision: 5, annotation: null };
    base = globalThis.prksPdfAnnotationBaseForWrite(fresh, 'ann-n', tombstone, runtimeBase(fresh, 'ann-n'));
    assert.equal(base.present, false, 'a CREATE step starts only at absent revision 0');
    assert.equal(base.revision, 5);

    /* Any id string works, including ones an object inherits. */
    for (const id of ['__proto__', 'constructor', 'toString']) {
        const odd = liveRuntime(id, highlight(id, 'a'), 1);
        const before = runtimeBase(odd, id);
        await withLiveRuntime(odd, () => {
            assert.equal(globalThis.prksApplyPdfAnnotationAckToLiveRuntimes(
                ack(id, highlight(id, 'b'), 2, 11), { op_id: 'op-' + id, operation: 'SET_PDF_ANNOTATION', base_revision: 1 }).length, 1);
        });
        base = globalThis.prksPdfAnnotationBaseForWrite(odd, id, before, runtimeBase(odd, id));
        assert.equal(base.revision, 2);
        assert.equal(base.acknowledged_op_id, 'op-' + id);
    }
}

/* A save queued behind a sent predecessor after sync's reconcile patched the
 * runtime and rebased dependents, but before it marked the row acknowledged,
 * takes that ACK's exact revision, not the provisional base + 1. A
 * stale-identical ACK (changed: false) can land far above base + 1. */
async function successorAfterTheRebaseUsesTheAckRevision() {
    const runtime = liveRuntime('ann-s', highlight('ann-s', 'v1'), 2);
    const { store } = await storeActingAfterOutboxRead();
    await store.savePdfAnnotation('W-1', { annotation_id: 'ann-s', annotation: highlight('ann-s', 'v2') },
        runtimeBase(runtime, 'ann-s'));
    const [first] = await store.listOperations();
    const claimed = await store.claimOperation(first.op_id);
    await settle();
    const prevSync = globalThis.prksSync;
    globalThis.prksSync = { store };
    try {
        await withLiveRuntime(runtime, async () => {
            const ok = await globalThis.prksPdfAnnotationSyncHandler.reconcile({
                code: 'ACKNOWLEDGED', work_id: 'W-1', annotation_id: 'ann-s', present: true,
                annotation: highlight('ann-s', 'v2'), server_revision: 9, changed: false,
                canonical_annotation_set_revision: 10,
            }, claimed || first);
            assert.equal(ok, true);
            await store.savePdfAnnotation('W-1', { annotation_id: 'ann-s', annotation: highlight('ann-s', 'v3') },
                writeBase(runtime, 'ann-s'));
        });
    } finally {
        if (prevSync) globalThis.prksSync = prevSync;
        else delete globalThis.prksSync;
    }
    /* Sync then settles the predecessor. */
    await store.updateOperationSyncState(first.op_id, { status: 'acknowledged', last_error: null, server_revision: 9 });
    const pending = (await store.listOperations()).filter(r => r.status === 'pending');
    assert.equal(pending.length, 1);
    assert.deepEqual(pending[0].depends_on, [first.op_id]);
    assert.equal(pending[0].payload.annotation.contents, 'v3');
    assert.equal(pending[0].base_revision, 9, 'the successor is sent with the ACK revision');
}

async function observedBaseForms() {
    const { store } = await storeActingAfterOutboxRead();
    /* A plain object is still taken as given. */
    await store.savePdfAnnotation('W-1', { annotation_id: 'ann-q', annotation: highlight('ann-q', 'x') },
        { present: true, revision: 2, annotation: highlight('ann-q', 'y'), annotation_id: 'ann-q' });
    const rows = await store.listOperations();
    assert.equal(rows.find(r => r.payload.annotation_id === 'ann-q').base_revision, 2);

    /* An invalid base read inside the write refuses the save. */
    let calls = 0;
    await assert.rejects(store.savePdfAnnotation(
        'W-1', { annotation_id: 'ann-r', annotation: highlight('ann-r', 'x') },
        () => (++calls === 1 ? { present: false, revision: 0, annotation: null, annotation_id: 'ann-r' } : null)
    ), err => err && err.prksLocalStoreCode === 'invalid_base');
}

function handlerShapes() {
    const h = globalThis.prksPdfAnnotationSyncHandler;
    const createOp = {
        entity_id: 'W-1',
        operation: 'CREATE_PDF_ANNOTATION',
        payload: { annotation_id: 'ann-1', annotation: highlight('ann-1', 'x') },
    };
    assert.ok(h.isResult({
        code: 'ACKNOWLEDGED', work_id: 'W-1', annotation_id: 'ann-1',
        changed: true, server_revision: 0, annotation: highlight('ann-1', 'x'),
        canonical_annotation_set_revision: 1,
    }, createOp));
    assert.ok(h.isResult({
        code: 'REVISION_CONFLICT', work_id: 'W-1', annotation_id: 'ann-1',
        current_revision: 3,
    }, createOp));
    assert.equal(h.isResult({ code: 'ACKNOWLEDGED', work_id: 'W-1' }, createOp), false);
    const termConflict = h.terminal({
        code: 'REVISION_CONFLICT', current_revision: 3,
        current_state: true, requested_state: true,
        current_value: JSON.stringify({ id: 'ann-1', type: 'highlight', content: 'S' }),
        requested_value: JSON.stringify({ id: 'ann-1', type: 'highlight', content: 'C' }),
        // Object-valued keys must be stripped — STRUCTURED_RESULT_KEYS rejects them.
        current_annotation: { id: 'ann-1', nested: true },
        requested_annotation: { id: 'ann-1' },
    });
    assert.equal(termConflict.conflict.code, 'REVISION_CONFLICT');
    assert.equal(termConflict.conflict.current_revision, 3);
    assert.equal(termConflict.conflict.current_state, true);
    assert.equal(typeof termConflict.conflict.current_value, 'string');
    assert.equal(termConflict.conflict.current_annotation, undefined);
    assert.equal(termConflict.conflict.requested_annotation, undefined);
    const termGone = h.terminal({ code: 'ENTITY_NOT_FOUND' });
    assert.equal(termGone.discard, 'ENTITY_NOT_FOUND');
    const termReuse = h.terminal({ code: 'ANNOTATION_ID_REUSED', current_revision: 1 });
    assert.equal(termReuse.discard, 'ANNOTATION_ID_REUSED');
}

/**
 * P1 regression: terminal → settle → updateOperationSyncState must persist a
 * conflict. Object-valued current_annotation previously made settle throw
 * invalid_result and left the op retrying forever.
 */
async function conflictTerminalSettlesThroughStore() {
    const idb = createFakeIndexedDBFactory();
    const store = createPrksLocalStore({ dbName: 'prks-local-v2:test', indexedDB: idb, uuid: uuid, now: () => Date.now() });
    await store.getOrCreateDeviceId();
    const body = highlight('ann-settle', 'base');
    const edited = highlight('ann-settle', 'mine');
    const observed = { present: true, revision: 0, annotation: body, annotation_id: 'ann-settle' };
    const created = await store.savePdfAnnotation(
        'W-settle',
        { annotation_id: 'ann-settle', annotation: edited },
        observed
    );
    assert.ok(created && created.op_id);
    const h = globalThis.prksPdfAnnotationSyncHandler;
    assert.ok(h && typeof h.terminal === 'function');
    const disposition = h.terminal({
        code: 'REVISION_CONFLICT',
        work_id: 'W-settle',
        annotation_id: 'ann-settle',
        current_revision: 2,
        current_state: true,
        requested_state: true,
        current_value: JSON.stringify({
            id: 'ann-settle', type: 'highlight', content: 'server', page_index: 0, color: null,
        }),
        requested_value: JSON.stringify({
            id: 'ann-settle', type: 'highlight', content: 'mine', page_index: 0, color: null,
        }),
        current_annotation: { id: 'ann-settle', contents: 'server' },
        requested_annotation: { id: 'ann-settle', contents: 'mine' },
    });
    assert.ok(disposition.conflict);
    assert.equal(disposition.conflict.current_annotation, undefined);
    // Mirror sync-runtime settle(op, disposition) for a conflict outcome.
    const settled = await store.updateOperationSyncState(created.op_id, {
        status: 'conflict',
        server_result: disposition.conflict,
        last_error: null,
    });
    assert.equal(settled.status, 'conflict');
    assert.equal(settled.server_result.code, 'REVISION_CONFLICT');
    assert.equal(settled.server_result.current_revision, 2);
    assert.equal(typeof settled.server_result.current_value, 'string');
    assert.equal(settled.server_result.current_annotation, undefined);
    const rows = await store.listOperations();
    const row = rows.find((r) => r.op_id === created.op_id);
    assert.ok(row);
    assert.equal(row.status, 'conflict');
    assert.equal(row.server_result.code, 'REVISION_CONFLICT');
}

function applyAckUpdatesLiveRuntimeRevision() {
    const runtime = {
        workId: 'W-1',
        annotationCache: {
            items: [highlight('ann-1', 'old')],
            allItems: [highlight('ann-1', 'old')],
            rawItems: [highlight('ann-1', 'old')],
        },
        annotationState: {
            work_id: 'W-1',
            annotations: [{ annotation_id: 'ann-1', revision: 0 }],
            known_absent: {},
        },
    };
    const contexts = [{ getResource: () => runtime }];
    const prev = globalThis.prksForEachLiveTabContext;
    globalThis.prksForEachLiveTabContext = function (fn) {
        contexts.forEach((ctx) => fn(ctx));
    };
    try {
        globalThis.prksApplyPdfAnnotationAckToLiveRuntimes({
            work_id: 'W-1',
            annotation_id: 'ann-1',
            present: true,
            annotation: highlight('ann-1', 'new'),
            server_revision: 1,
            changed: true,
            canonical_annotation_set_revision: 4,
        });
        assert.equal(runtime.annotationCache.items[0].contents, 'new');
        assert.equal(runtime.annotationState.annotations[0].revision, 1);
        assert.equal(runtime.acknowledgedAnnotationSetRevision, 4);
        const observed = globalThis.prksAcknowledgedPdfAnnotationBase(
            runtime.annotationCache.items,
            runtime.annotationState,
            'ann-1'
        );
        assert.equal(observed.revision, 1, 'second edit must see ACK revision');
    } finally {
        if (prev) globalThis.prksForEachLiveTabContext = prev;
        else delete globalThis.prksForEachLiveTabContext;
    }
}

// #1212: acknowledged state on a runtime only moves forward. An ACK older
// than the snapshot the runtime holds is not patched in, and a snapshot
// read before an ACK the runtime already applied is reported as stale.
function acknowledgedStateOnlyMovesForward() {
    const runtime = {
        workId: 'W-1',
        annotationCache: { items: [highlight('ann-1', 'newer')] },
        annotationState: {
            work_id: 'W-1',
            annotations: [{ annotation_id: 'ann-1', revision: 2 }],
            known_absent: {},
        },
    };
    globalThis.prksNotePdfAnnotationSnapshotRevision(runtime, 5);
    const prev = globalThis.prksForEachLiveTabContext;
    globalThis.prksForEachLiveTabContext = function (fn) {
        fn({ getResource: () => runtime });
    };
    try {
        const ack = (gen, id, comment, rev) => ({
            work_id: 'W-1', annotation_id: id, present: true,
            annotation: highlight(id, comment), server_revision: rev,
            changed: true, canonical_annotation_set_revision: gen,
        });
        // Older than the snapshot: ignored, the newer comment stays.
        assert.equal(globalThis.prksApplyPdfAnnotationAckToLiveRuntimes(
            ack(4, 'ann-1', 'older', 1)).length, 0);
        assert.equal(runtime.annotationCache.items[0].contents, 'newer');
        assert.equal(runtime.annotationState.annotations[0].revision, 2);
        // The set-revision guard on its own: an id the runtime holds nothing
        // for, so only the snapshot's set revision refuses it.
        assert.equal(globalThis.prksApplyPdfAnnotationAckToLiveRuntimes(
            ack(4, 'ann-stale', 'older', 0)).length, 0);
        assert.ok(!runtime.annotationCache.items.some(a => a.id === 'ann-stale'));
        // The snapshot's own revision is not older than itself.
        assert.equal(globalThis.prksPdfAnnotationSnapshotPredatesRuntime(runtime, 5), false);
        // A newer ACK applies and makes a snapshot read before it stale.
        assert.equal(globalThis.prksApplyPdfAnnotationAckToLiveRuntimes(
            ack(6, 'ann-2', 'added', 0)).length, 1);
        assert.deepEqual(runtime.annotationCache.items.map(a => a.id), ['ann-1', 'ann-2']);
        assert.equal(globalThis.prksPdfAnnotationSnapshotPredatesRuntime(runtime, 5), true);
        assert.equal(globalThis.prksPdfAnnotationSnapshotPredatesRuntime(runtime, 6), false);
        assert.equal(globalThis.prksPdfAnnotationSnapshotPredatesRuntime(runtime, 7), false);
        // No revision to compare: nothing is refused.
        assert.equal(globalThis.prksPdfAnnotationSnapshotPredatesRuntime(runtime, undefined), false);
        assert.equal(globalThis.prksPdfAnnotationSnapshotPredatesRuntime({}, 0), false);
        // Same annotation, ACKs applied out of order: gen 7 then gen 8, then
        // the delayed gen 7 must not put the older comment back.
        assert.equal(globalThis.prksApplyPdfAnnotationAckToLiveRuntimes(
            ack(7, 'ann-2', 'first edit', 1)).length, 1);
        assert.equal(globalThis.prksApplyPdfAnnotationAckToLiveRuntimes(
            ack(8, 'ann-2', 'second edit', 2)).length, 1);
        assert.equal(globalThis.prksApplyPdfAnnotationAckToLiveRuntimes(
            ack(7, 'ann-2', 'first edit', 1)).length, 0);
        const ann2 = () => runtime.annotationCache.items.find(a => a.id === 'ann-2');
        assert.equal(ann2().contents, 'second edit');
        assert.equal(runtime.annotationState.annotations
            .find(r => r.annotation_id === 'ann-2').revision, 2);
        // A delete tombstone is not undone by a delayed older present ACK.
        assert.equal(globalThis.prksApplyPdfAnnotationAckToLiveRuntimes({
            work_id: 'W-1', annotation_id: 'ann-2', present: false, server_revision: 3,
            changed: true, canonical_annotation_set_revision: 9,
        }).length, 1);
        assert.equal(globalThis.prksApplyPdfAnnotationAckToLiveRuntimes(
            ack(8, 'ann-2', 'second edit', 2)).length, 0);
        assert.equal(ann2(), undefined);
        assert.equal(runtime.annotationState.known_absent['ann-2'], 3);
        // CREATE at revision 0, then a delayed convergent DELETE of the
        // then-absent id (also revision 0): the created row stays.
        assert.equal(globalThis.prksApplyPdfAnnotationAckToLiveRuntimes(
            ack(10, 'ann-4', 'created', 0)).length, 1);
        assert.equal(globalThis.prksApplyPdfAnnotationAckToLiveRuntimes({
            work_id: 'W-1', annotation_id: 'ann-4', present: false, server_revision: 0,
            changed: false, canonical_annotation_set_revision: 9,
        }).length, 0);
        assert.ok(runtime.annotationCache.items.some(a => a.id === 'ann-4'));
        // An older set generation for a different annotation still applies.
        assert.equal(globalThis.prksApplyPdfAnnotationAckToLiveRuntimes(
            ack(6, 'ann-3', 'other', 0)).length, 1);
        assert.ok(runtime.annotationCache.items.some(a => a.id === 'ann-3'));
    } finally {
        if (prev) globalThis.prksForEachLiveTabContext = prev;
        else delete globalThis.prksForEachLiveTabContext;
    }
}

async function main() {
    codecParity();
    effectiveOverlay();
    await coalesceCreateSetDelete();
    await coalesceSetCancelAndSentSuccessor();
    await ownAckDuringTheWriteMovesTheBase();
    await catchUpDuringTheWriteKeepsTheBase();
    await successorAfterTheRebaseUsesTheAckRevision();
    await observedBaseForms();
    handlerShapes();
    await conflictTerminalSettlesThroughStore();
    applyAckUpdatesLiveRuntimeRevision();
    acknowledgedStateOnlyMovesForward();
    console.log(checks + ' checks passed');
}

main().catch(err => {
    console.error(err);
    process.exit(1);
});
