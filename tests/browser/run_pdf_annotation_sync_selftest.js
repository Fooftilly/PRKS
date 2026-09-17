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
    const store = createPrksLocalStore({ indexedDB: idb, uuid: uuid, now: () => Date.now() });
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
    const store = createPrksLocalStore({ indexedDB: idb, uuid: uuid, now: () => Date.now() });
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
    assert.equal(dep.base_revision, 3, 'successor measured after SENT advances');
    assert.deepEqual(dep.depends_on, [firstId]);

    // Overlay prefers the latest sequence (successor).
    globalThis.prksSetPendingPdfAnnotations(rows);
    const effective = globalThis.prksEffectiveWorkAnnotations([body], 'W-1');
    assert.equal(effective.find(a => a.id === 'ann-y').contents, 'again');
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
        current_annotation: { id: 'ann-1' },
    });
    assert.equal(termConflict.conflict.code, 'REVISION_CONFLICT');
    const termGone = h.terminal({ code: 'ENTITY_NOT_FOUND' });
    assert.equal(termGone.discard, 'ENTITY_NOT_FOUND');
    const termReuse = h.terminal({ code: 'ANNOTATION_ID_REUSED', current_revision: 1 });
    assert.equal(termReuse.discard, 'ANNOTATION_ID_REUSED');
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

async function main() {
    codecParity();
    effectiveOverlay();
    await coalesceCreateSetDelete();
    await coalesceSetCancelAndSentSuccessor();
    handlerShapes();
    applyAckUpdatesLiveRuntimeRevision();
    console.log(checks + ' checks passed');
}

main().catch(err => {
    console.error(err);
    process.exit(1);
});
