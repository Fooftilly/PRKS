'use strict';

const strict = require('assert/strict');
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); }, {
    get: (_t, k) => (...args) => { checks += 1; return strict[k](...args); },
});

require('../../frontend/js/pdf-annotation-state.js');

const fakeCaches = new Map();

function installGlobals(opts) {
    globalThis.prksOfflineRuntimeState = () => opts.state || 'online';
    globalThis.prksIsLivePendingWorkDeletion = (id) => !!(opts.pendingDelete && opts.pendingDelete.has(id));
    globalThis.prksSync = {
        store: {
            isAvailable: async () => opts.durable !== false,
            savePdfAnnotation: async () => ({ op_id: 'x' }),
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

async function onlineDurable() {
    installGlobals({ state: 'online', durable: true });
    const cap = await globalThis.prksResolvePdfAnnotationMutationCapability(
        { id: 'W1', file_path: '/api/pdfs/a.pdf' },
        {}
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

    installGlobals({
        state: 'offline',
        durable: true,
        entities: { 'work-annotations:W1': [] },
    });
    fakeCaches.set('prks-pdf-v1', new Map([['/api/pdfs/a.pdf', true]]));
    cap = await globalThis.prksResolvePdfAnnotationMutationCapability(
        { id: 'W1', file_path: '/api/pdfs/a.pdf' },
        {}
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
        entities: { 'work-annotations:W1': [] },
    });
    fakeCaches.set('prks-pdf-v1', new Map([['/api/pdfs/a.pdf', true]]));
    const cap = await globalThis.prksResolvePdfAnnotationMutationCapability(
        { id: 'W1', file_path: '/api/pdfs/a.pdf' },
        {}
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

(async () => {
    await onlineDurable();
    await onlineLegacyWithoutDurable();
    await offlineNeedsPdfAndBase();
    await pendingDeleteBlocks();
    await saveWakesSyncViaChanged();
    console.log(checks + ' checks passed');
})().catch((err) => {
    console.error(err);
    process.exit(1);
});
