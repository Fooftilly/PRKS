'use strict';

const strict = require('assert/strict');
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); }, {
    get: (_t, k) => (...args) => { checks += 1; return strict[k](...args); },
});

require('../../frontend/js/pdf-annotation-state.js');

const fakeCaches = new Map();

function coherentSnap(workId) {
    return {
        work_id: workId,
        items: [],
        annotations: [],
        known_absent: {},
        canonical_annotation_set_revision: 0,
        materialized_pdf_annotation_revision: 0,
    };
}

function installGlobals(opts) {
    globalThis.prksOfflineRuntimeState = () => opts.state || 'online';
    globalThis.prksIsLivePendingWorkDeletion = (id) => !!(opts.pendingDelete && opts.pendingDelete.has(id));
    globalThis.prksSync = {
        store: {
            isAvailable: async () => opts.durable !== false,
            savePdfAnnotation: async () => ({ op_id: 'x' }),
            listOperations: async () => opts.ops || [],
        },
        changed() { opts.changedCalls = (opts.changedCalls || 0) + 1; },
    };
    globalThis.PRKS_OFFLINE_PDF_CACHE_NAME = 'prks-pdf-v1';
    globalThis.caches = {
        open: async (name) => ({
            match: async (path) => {
                const bag = fakeCaches.get(name) || new Map();
                return bag.has(path) ? { ok: true } : undefined;
            },
        }),
    };
    globalThis.prksOfflinePeekEntity = async (kind, id) => {
        if (!opts.entities) return null;
        const key = kind + ':' + id;
        return Object.prototype.hasOwnProperty.call(opts.entities, key) ? opts.entities[key] : null;
    };
    globalThis.prksOfflineCacheEntity = async () => true;
}

async function onlineAwaitingBaseIsPreview() {
    installGlobals({ state: 'online', durable: true });
    const cap = await globalThis.prksResolvePdfAnnotationMutationCapability(
        { id: 'W1', file_path: '/api/pdfs/a.pdf' },
        {}
    );
    assert.equal(cap.mode, 'preview');
    assert.equal(cap.durable, false);
    assert.equal(cap.reason, 'online_awaiting_base');
}

async function onlineAwaitingBridgeIsPreview() {
    installGlobals({
        state: 'online',
        durable: true,
        entities: { 'work-annotations-snapshot:W1': coherentSnap('W1') },
    });
    const cap = await globalThis.prksResolvePdfAnnotationMutationCapability(
        { id: 'W1', file_path: '/api/pdfs/a.pdf' },
        { annotationBaseReady: true, annotationState: coherentSnap('W1'),
          annotationCache: { items: [] } }
    );
    // Runtime has base but bridge not marked ready.
    assert.equal(cap.mode, 'preview');
    assert.equal(cap.durable, true);
    assert.equal(cap.reason, 'online_awaiting_bridge');
}

async function onlineDurableWithBridge() {
    installGlobals({
        state: 'online',
        durable: true,
        entities: { 'work-annotations-snapshot:W1': coherentSnap('W1') },
    });
    const runtime = {
        annotationBaseReady: true,
        annotationDurableBridgeReady: true,
        annotationState: { work_id: 'W1', annotations: [], known_absent: {} },
        annotationCache: { items: [] },
    };
    const cap = await globalThis.prksResolvePdfAnnotationMutationCapability(
        { id: 'W1', file_path: '/api/pdfs/a.pdf' },
        runtime
    );
    assert.equal(cap.mode, 'work');
    assert.equal(cap.durable, true);
    assert.equal(cap.reason, 'online_durable');
}

async function onlineLegacyWithoutDurable() {
    installGlobals({ state: 'online', durable: false });
    const cap = await globalThis.prksResolvePdfAnnotationMutationCapability(
        { id: 'W1', file_path: '/api/pdfs/a.pdf' },
        {}
    );
    assert.equal(cap.mode, 'work');
    assert.equal(cap.durable, false);
    assert.equal(cap.reason, 'online_legacy');
}

async function offlineNeedsPdfAndBase() {
    installGlobals({
        state: 'offline',
        durable: true,
        entities: {},
    });
    fakeCaches.set('prks-pdf-v1', new Map());
    let cap = await globalThis.prksResolvePdfAnnotationMutationCapability(
        { id: 'W1', file_path: '/api/pdfs/a.pdf' },
        {}
    );
    assert.equal(cap.mode, 'preview');
    assert.equal(cap.reason, 'pdf_bytes_unavailable');

    fakeCaches.set('prks-pdf-v1', new Map([['/api/pdfs/a.pdf', true]]));
    cap = await globalThis.prksResolvePdfAnnotationMutationCapability(
        { id: 'W1', file_path: '/api/pdfs/a.pdf' },
        {}
    );
    assert.equal(cap.reason, 'annotation_base_unavailable');

    // List-only / empty array is not a coherent snapshot.
    installGlobals({
        state: 'offline',
        durable: true,
        entities: { 'work-annotations-snapshot:W1': [] },
    });
    fakeCaches.set('prks-pdf-v1', new Map([['/api/pdfs/a.pdf', true]]));
    cap = await globalThis.prksResolvePdfAnnotationMutationCapability(
        { id: 'W1', file_path: '/api/pdfs/a.pdf' },
        {}
    );
    assert.equal(cap.reason, 'annotation_base_unavailable');

    installGlobals({
        state: 'offline',
        durable: true,
        entities: { 'work-annotations-snapshot:W1': coherentSnap('W1') },
    });
    fakeCaches.set('prks-pdf-v1', new Map([['/api/pdfs/a.pdf', true]]));
    cap = await globalThis.prksResolvePdfAnnotationMutationCapability(
        { id: 'W1', file_path: '/api/pdfs/a.pdf' },
        {
            annotationBaseReady: true,
            annotationDurableBridgeReady: true,
            annotationState: { work_id: 'W1', annotations: [], known_absent: {} },
            annotationCache: { items: [] },
        }
    );
    assert.equal(cap.mode, 'work');
    assert.equal(cap.durable, true);
    assert.equal(cap.reason, 'offline_durable');
}

async function pendingDeleteBlocks() {
    installGlobals({
        state: 'offline',
        durable: true,
        pendingDelete: new Set(['W1']),
        entities: { 'work-annotations-snapshot:W1': coherentSnap('W1') },
    });
    fakeCaches.set('prks-pdf-v1', new Map([['/api/pdfs/a.pdf', true]]));
    const cap = await globalThis.prksResolvePdfAnnotationMutationCapability(
        { id: 'W1', file_path: '/api/pdfs/a.pdf' },
        { annotationDurableBridgeReady: true }
    );
    assert.equal(cap.mode, 'preview');
    assert.equal(cap.reason, 'work_pending_delete');
}

async function saveWakesSyncViaChanged() {
    const opts = { state: 'online', durable: true, changedCalls: 0 };
    installGlobals(opts);
    await globalThis.prksSavePdfAnnotationDurably(
        'W1',
        { annotation_id: 'a1', annotation: { id: 'a1', type: 9 } },
        { annotation_id: 'a1', present: false, revision: 0, annotation: null }
    );
    assert.equal(opts.changedCalls, 1);
}

async function unresolvedOpsHelper() {
    installGlobals({
        state: 'online',
        durable: true,
        ops: [
            { operation: 'SET_PDF_ANNOTATION', entity_type: 'work', entity_id: 'W1', status: 'pending' },
        ],
    });
    assert.equal(await globalThis.prksWorkHasUnresolvedPdfAnnotationOps('W1'), true);
    installGlobals({ state: 'online', durable: true, ops: [] });
    assert.equal(await globalThis.prksWorkHasUnresolvedPdfAnnotationOps('W1'), false);
}

(async () => {
    await onlineAwaitingBaseIsPreview();
    await onlineAwaitingBridgeIsPreview();
    await onlineDurableWithBridge();
    await onlineLegacyWithoutDurable();
    await offlineNeedsPdfAndBase();
    await pendingDeleteBlocks();
    await saveWakesSyncViaChanged();
    await unresolvedOpsHelper();
    console.log(checks + ' checks passed');
})().catch((err) => {
    console.error(err);
    process.exit(1);
});
