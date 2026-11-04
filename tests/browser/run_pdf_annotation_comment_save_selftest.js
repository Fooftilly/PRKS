#!/usr/bin/env node
'use strict';

/**
 * #401: editing an existing PDF annotation comment must record a durable
 * SET_PDF_ANNOTATION intent every time, not only the first time.
 *
 * Drives the real `window.savePdfAnnotationComment` from works-pdf.js against
 * a viewer stub, then performs the durable write the persistence worker's
 * `onAnnotationEvent` performs for the committed update: the acknowledged
 * base comes from `runtime.annotationCache.items` and the desired state from
 * the viewer. The old save patched that acknowledged cache in place, so the
 * base already equalled the edit and the store recorded nothing.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');
const strict = require('assert/strict');

let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); }, {
    get: (_t, k) => (...args) => { checks += 1; return strict[k](...args); },
});

const rootDir = path.resolve(__dirname, '../..');
global.window = global;

const { createFakeIndexedDBFactory } = require('./lib/fake_indexeddb.js');
const { createPrksLocalStore } = require(path.join(rootDir, 'frontend/js/local-store.js'));
require(path.join(rootDir, 'frontend/js/pdf-annotation-state.js'));

let pdfSource = fs.readFileSync(path.join(rootDir, 'frontend/js/components/works-pdf.js'), 'utf8');
pdfSource = pdfSource.replace(
    "import { prksPdfCacheInstallOutcome, prksPostPdfCacheInstall } from '/js/pdf-cache-install.js';\nimport { createPrksPdfViewer } from '/js/pdf-viewer-runtime.js';\n",
    'const prksPdfCacheInstallOutcome = null;\nconst prksPostPdfCacheInstall = null;\nconst createPrksPdfViewer = null;\n'
);
pdfSource = pdfSource.replace('export function initPdfViewerForWork', 'function initPdfViewerForWork');
// Same CommonJS loading as run_work_pdf_lifecycle_selftest.js (no dynamic eval).
const scriptDir = fs.mkdtempSync(path.join(os.tmpdir(), 'prks-pdf-comment-save-'));
const scriptFile = path.join(scriptDir, 'works-pdf.cjs');
fs.writeFileSync(scriptFile, pdfSource + '\nmodule.exports = {};\n');
require(scriptFile);
assert.equal(typeof window.savePdfAnnotationComment, 'function');

const WORK = 'W-401';
let sequence = 0;
const uuid = () => '00000000-0000-4000-8000-' + (++sequence).toString(16).padStart(12, '0');

function highlight(id, comment) {
    const out = {
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
    };
    if (comment != null) out.custom = { prksComment: comment };
    return out;
}

function clone(value) {
    return JSON.parse(JSON.stringify(value));
}

/** Viewer stub: getAnnotations returns live objects; update merges a patch. */
function makeViewer(initial) {
    const objects = initial.map(clone);
    const viewer = {
        refuse: false,
        throwOnUpdate: false,
        updates: [],
        getAnnotations: function () { return objects; },
        updateAnnotation: function (id, patch) {
            if (viewer.throwOnUpdate) throw new Error('viewer exploded');
            // Mirrors the vendor controller gate: a refused update is silent.
            if (viewer.refuse) return;
            const obj = objects.find(o => String(o.id) === String(id));
            if (!obj) return;
            Object.assign(obj, clone(patch));
            viewer.updates.push({ id: id, patch: clone(patch) });
        },
    };
    return viewer;
}

/** The persistence runtime surface savePdfAnnotationComment reads. */
function makeOwner(viewer, ackItems, state, opts) {
    const reported = [];
    const flushes = [];
    const runtime = {
        workId: WORK,
        viewer: viewer,
        mode: 'work',
        annotationMutationAllowed: true,
        annotationMutationDurable: !(opts && opts.legacy),
        annotationCache: { items: ackItems, allItems: ackItems, rawItems: ackItems },
        annotationState: state,
        annotationPopup: { open: true, annId: 'ann-1', epoch: 1, comment: '' },
        captureAnnotationPopupTicket: function () {
            return {
                annId: runtime.annotationPopup.annId,
                epoch: runtime.annotationPopup.epoch,
                direct: false,
                pageIndex: 0,
                generation: 1,
                viewer: runtime.viewer,
                custom: null,
            };
        },
        annotationPopupWriteStill: function (ticket) { return ticket.viewer === runtime.viewer; },
        annotationPopupStill: function (ticket) {
            return ticket.viewer === runtime.viewer && runtime.annotationPopup.open;
        },
        noteAnnotationPopupComment: function (_ticket, comment) {
            runtime.annotationPopup.comment = comment;
            return true;
        },
        reportAnnotationEditNotSaved: function () { reported.push(Date.now()); },
        flushAnnotations: async function () { flushes.push(Date.now()); },
    };
    const owner = {
        destroyed: false,
        generation: 1,
        isCurrent: function (g) { return g === 1; },
        getResource: function (kind) { return kind === 'pdf' ? runtime : null; },
    };
    return { owner: owner, runtime: runtime, reported: reported, flushes: flushes };
}

const captured = { annId: 'ann-1', epoch: 1, generation: 1 };

/** What onAnnotationEvent does for one committed viewer update (durable). */
async function durableWriteForCommittedUpdate(store, runtime, annId) {
    const observed = window.prksAcknowledgedPdfAnnotationBase(
        runtime.annotationCache.items, runtime.annotationState, annId
    );
    const raw = runtime.viewer.getAnnotations().find(o => String(o.id) === annId);
    return store.savePdfAnnotation(WORK, { annotation_id: annId, annotation: clone(raw) }, observed);
}

/** The server's ACK: the stored row replaces the cache entry, revision + 1. */
function acknowledge(runtime, row, revision, generation) {
    const prev = window.prksForEachLiveTabContext;
    window.prksForEachLiveTabContext = function (fn) {
        fn({ getResource: () => runtime });
    };
    try {
        window.prksApplyPdfAnnotationAckToLiveRuntimes({
            work_id: WORK,
            annotation_id: row.payload.annotation_id,
            present: true,
            annotation: clone(row.payload.annotation),
            server_revision: revision,
            changed: true,
            canonical_annotation_set_revision: generation,
        });
    } finally {
        window.prksForEachLiveTabContext = prev;
    }
}

/** The sync engine's success path: ACK the row, then drop it. */
async function settleAcknowledged(store, row) {
    await store.updateOperationSyncState(row.op_id, { status: 'acknowledged' });
    await store.deleteAcknowledgedOperation(row.op_id);
}

async function freshStore() {
    const store = createPrksLocalStore({ dbName: 'prks-local-v2:test',
        indexedDB: createFakeIndexedDBFactory(),
        uuid: uuid,
        now: () => Date.now(),
    });
    await store.getOrCreateDeviceId();
    return store;
}

async function repeatedEditsEachRecordASet(label, startComment) {
    const store = await freshStore();
    const ack = [highlight('ann-1', startComment)];
    const state = { work_id: WORK, annotations: [{ annotation_id: 'ann-1', revision: 1 }], known_absent: {} };
    const viewer = makeViewer(ack);
    const world = makeOwner(viewer, ack, state);
    const frozenAck = JSON.stringify(ack);

    let revision = 1;
    for (const text of ['first comment', 'second comment', 'third comment']) {
        const ackBefore = JSON.stringify(world.runtime.annotationCache.items);
        await window.savePdfAnnotationComment(world.owner, text, captured);
        assert.equal(
            JSON.stringify(world.runtime.annotationCache.items), ackBefore,
            label + ': save must not rewrite the acknowledged base (' + text + ')'
        );
        assert.equal(world.runtime.annotationPopup.comment, text, label + ': popup shows the saved text');
        const live = viewer.getAnnotations()[0];
        assert.equal(live.contents, text);
        assert.equal(live.custom.prksComment, text);

        const row = await durableWriteForCommittedUpdate(store, world.runtime, 'ann-1');
        assert.ok(row, label + ': "' + text + '" must record an intent, not a no-op');
        assert.equal(row.operation, 'SET_PDF_ANNOTATION');
        assert.equal(row.base_revision, revision, label + ': intent measured against the ACK revision');
        assert.equal(row.payload.annotation.contents, text);
        assert.equal(row.payload.annotation.custom.prksComment, text);

        // Server accepts it; the store row settles and the ACK lands.
        await settleAcknowledged(store, row);
        revision += 1;
        acknowledge(world.runtime, row, revision, revision);
        assert.equal(world.runtime.annotationCache.items[0].contents, text);
    }
    assert.equal(JSON.stringify(ack), frozenAck, label + ': the original ACK array is never mutated');
    assert.equal(world.reported.length, 0, label + ': nothing reported as unsaved');
    assert.equal(world.flushes.length, 0, label + ': durable save does not force an empty materialize');

    // Saving the same text again is a real no-op: nothing to send, nothing lost.
    await window.savePdfAnnotationComment(world.owner, 'third comment', captured);
    assert.equal(await durableWriteForCommittedUpdate(store, world.runtime, 'ann-1'), null);
}

async function refusedViewerUpdateIsReported() {
    const ack = [highlight('ann-1', 'kept')];
    const state = { work_id: WORK, annotations: [{ annotation_id: 'ann-1', revision: 3 }], known_absent: {} };
    const viewer = makeViewer(ack);
    viewer.refuse = true;
    const world = makeOwner(viewer, ack, state);
    world.runtime.annotationPopup.comment = 'kept';
    await window.savePdfAnnotationComment(world.owner, 'lost?', captured);
    assert.equal(world.reported.length, 1, 'a refused viewer update is reported, not dropped');
    assert.equal(world.runtime.annotationPopup.comment, 'kept', 'popup is not told the edit was saved');
    assert.equal(viewer.getAnnotations()[0].contents, 'kept');
}

async function throwingViewerUpdateIsReported() {
    const ack = [highlight('ann-1', 'kept')];
    const state = { work_id: WORK, annotations: [{ annotation_id: 'ann-1', revision: 3 }], known_absent: {} };
    const viewer = makeViewer(ack);
    viewer.throwOnUpdate = true;
    const world = makeOwner(viewer, ack, state);
    world.runtime.annotationPopup.comment = 'kept';
    await window.savePdfAnnotationComment(world.owner, 'boom', captured);
    assert.equal(world.reported.length, 1, 'a throwing save is reported, not swallowed');
    assert.equal(world.runtime.annotationPopup.comment, 'kept');
}

async function legacyModeStillFlushes() {
    const ack = [highlight('ann-1', 'old')];
    const state = { work_id: WORK, annotations: [], known_absent: {} };
    const viewer = makeViewer(ack);
    const world = makeOwner(viewer, ack, state, { legacy: true });
    await window.savePdfAnnotationComment(world.owner, 'legacy edit', captured);
    assert.equal(world.flushes.length, 1, 'legacy full-set save keeps its flush');
    assert.equal(world.reported.length, 0);
    assert.equal(viewer.getAnnotations()[0].contents, 'legacy edit');
}

async function importedAnnotationEditedRepeatedly() {
    // Imported highlight: in the PDF bytes but not yet a canonical row. The
    // first edit creates it; every later edit must still be a SET.
    const store = await freshStore();
    const ack = [];
    const state = { work_id: WORK, annotations: [], known_absent: {} };
    const imported = highlight('ann-1', null);
    imported.contents = 'Imported note';
    const viewer = makeViewer([imported]);
    const world = makeOwner(viewer, ack, state);
    let revision = 0;
    for (const text of ['imported first', 'imported second', 'imported third']) {
        await window.savePdfAnnotationComment(world.owner, text, captured);
        const row = await durableWriteForCommittedUpdate(store, world.runtime, 'ann-1');
        assert.ok(row, 'imported: "' + text + '" must record an intent');
        assert.equal(row.operation, revision === 0 ? 'CREATE_PDF_ANNOTATION' : 'SET_PDF_ANNOTATION');
        assert.equal(row.payload.annotation.contents, text);
        await settleAcknowledged(store, row);
        revision += 1;
        acknowledge(world.runtime, row, revision, revision);
    }
    assert.equal(world.reported.length, 0);
}

async function main() {
    await repeatedEditsEachRecordASet('PRKS highlight with a comment', 'existing');
    await repeatedEditsEachRecordASet('PRKS highlight without a comment', '');
    await importedAnnotationEditedRepeatedly();
    await refusedViewerUpdateIsReported();
    await throwingViewerUpdateIsReported();
    await legacyModeStillFlushes();
    console.log(checks + ' checks passed');
}

main().catch(err => {
    console.error(err && err.stack ? err.stack : err);
    process.exit(1);
});
