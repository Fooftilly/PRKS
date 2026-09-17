'use strict';

const strict = require('assert/strict');
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); }, {
    get: (_t, k) => (...args) => { checks += 1; return strict[k](...args); },
});

require('../../frontend/js/pdf-annotation-state.js');
require('../../frontend/js/pdf-annotation-reconcile.js');

function highlight(id, comment, y) {
    return {
        id: id,
        type: 9,
        pageIndex: 0,
        contents: comment,
        color: '#FFCD45',
        strokeColor: '#FFCD45',
        opacity: 1,
        blendMode: 'Multiply',
        rect: { origin: { x: 72, y: y || 700 }, size: { width: 160, height: 14 } },
        segmentRects: [{ origin: { x: 72, y: y || 700 }, size: { width: 160, height: 14 } }],
        custom: { prksComment: comment },
    };
}

function linkAnn() {
    return {
        id: 'link-1',
        type: 2,
        pageIndex: 0,
        uri: 'https://example.test',
        rect: { origin: { x: 10, y: 10 }, size: { width: 40, height: 12 } },
    };
}

function fakeViewer(initial) {
    const store = new Map();
    (initial || []).forEach((a) => store.set(String(a.id), JSON.parse(JSON.stringify(a))));
    const events = [];
    return {
        events,
        getAnnotations() {
            return Array.from(store.values()).map((raw) => ({ raw }));
        },
        createAnnotation(_page, ann) {
            const copy = JSON.parse(JSON.stringify(ann));
            store.set(String(copy.id), copy);
            events.push({ kind: 'create', id: copy.id });
        },
        updateAnnotation(id, patch) {
            const cur = store.get(String(id));
            if (!cur) throw new Error('missing ' + id);
            Object.assign(cur, patch);
            events.push({ kind: 'update', id: String(id) });
        },
        async deleteAnnotation(id) {
            store.delete(String(id));
            events.push({ kind: 'delete', id: String(id) });
        },
    };
}

async function createUpdateIdempotent() {
    const viewer = fakeViewer([linkAnn()]);
    const a = highlight('a', 'A');
    const b = highlight('b', 'B', 650);
    let stats = await globalThis.prksReconcileViewerAnnotations(viewer, [a, b]);
    assert.equal(stats.created, 2);
    assert.equal(stats.deleted, 0);
    assert.ok(viewer.getAnnotations().some((x) => x.raw.id === 'link-1'), 'link preserved');
    assert.equal(viewer.getAnnotations().length, 3);

    // Second pass: idempotent
    viewer.events.length = 0;
    stats = await globalThis.prksReconcileViewerAnnotations(viewer, [a, b]);
    assert.equal(stats.created, 0);
    assert.equal(stats.updated, 0);
    assert.equal(stats.deleted, 0);
    assert.equal(stats.skipped, 2);
    assert.equal(viewer.events.length, 0, 'no viewer ops on no-op reconcile');
}

async function updateAndManagedDelete() {
    const viewer = fakeViewer([]);
    const a = highlight('a', 'A');
    const b = highlight('b', 'B', 650);
    await globalThis.prksReconcileViewerAnnotations(viewer, [a, b]);

    const a2 = highlight('a', 'A2');
    let stats = await globalThis.prksReconcileViewerAnnotations(viewer, [a2, b]);
    assert.equal(stats.updated, 1);
    assert.equal(stats.skipped, 1);
    const liveA = viewer.getAnnotations().find((x) => x.raw.id === 'a').raw;
    assert.equal(liveA.contents, 'A2');

    // Drop b from effective: previously managed → delete. Keep unknown legacy.
    viewer.createAnnotation(0, highlight('legacy', 'L', 600));
    stats = await globalThis.prksReconcileViewerAnnotations(viewer, [a2]);
    assert.equal(stats.deleted, 1);
    const ids = viewer.getAnnotations().map((x) => x.raw.id).sort((a, b) => a.localeCompare(b));
    assert.deepEqual(ids, ['a', 'legacy']);
}

async function suppressDepth() {
    const viewer = fakeViewer([]);
    assert.equal(globalThis.prksViewerIsReconcilingAnnotations(viewer), false);
    globalThis.prksBeginViewerAnnotationReconcile(viewer);
    assert.equal(globalThis.prksViewerIsReconcilingAnnotations(viewer), true);
    globalThis.prksBeginViewerAnnotationReconcile(viewer);
    globalThis.prksEndViewerAnnotationReconcile(viewer);
    assert.equal(globalThis.prksViewerIsReconcilingAnnotations(viewer), true);
    globalThis.prksEndViewerAnnotationReconcile(viewer);
    assert.equal(globalThis.prksViewerIsReconcilingAnnotations(viewer), false);

    // reconcile itself wraps suppress
    let sawInside = false;
    const origCreate = viewer.createAnnotation.bind(viewer);
    viewer.createAnnotation = function (page, ann) {
        sawInside = globalThis.prksViewerIsReconcilingAnnotations(viewer);
        return origCreate(page, ann);
    };
    await globalThis.prksReconcileViewerAnnotations(viewer, [highlight('z', 'Z')]);
    assert.equal(sawInside, true);
    assert.equal(globalThis.prksViewerIsReconcilingAnnotations(viewer), false);
}

async function neverTouchLink() {
    const viewer = fakeViewer([linkAnn(), highlight('a', 'A')]);
    await globalThis.prksReconcileViewerAnnotations(viewer, []);
    // 'a' was not previously managed on this viewer, so it stays (legacy).
    // Link always stays.
    const ids = viewer.getAnnotations().map((x) => x.raw.id).sort((a, b) => a.localeCompare(b));
    assert.deepEqual(ids, ['a', 'link-1']);

    // Seed managed then empty effective deletes 'a' only.
    await globalThis.prksReconcileViewerAnnotations(viewer, [], {
        seedManagedIds: ['a'],
    });
    assert.deepEqual(
        viewer.getAnnotations().map((x) => x.raw.id),
        ['link-1']
    );
}

(async () => {
    await createUpdateIdempotent();
    await updateAndManagedDelete();
    await suppressDepth();
    await neverTouchLink();
    console.log(checks + ' checks passed');
})().catch((err) => {
    console.error(err);
    process.exit(1);
});
