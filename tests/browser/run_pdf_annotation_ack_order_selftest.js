'use strict';
/* #1074 / #1425: a delayed or replayed PDF annotation ACK must never replace
 * a newer annotation body, revision or tombstone in the cached snapshot. */
const strict = require('assert/strict');
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); },
    { get: (_target, key) => (...args) => { checks += 1; return strict[key](...args); } });
const { createFakeIndexedDBFactory } = require('./lib/fake_indexeddb.js');
const { createPrksOfflineStore } = require('../../frontend/js/offline-store.js');
const { createPrksOfflineRuntime } = require('../../frontend/js/offline-runtime.js');
globalThis.prksSyncReconcile = require('../../frontend/js/sync-reconcile.js');

const later = () => new Promise(resolve => setTimeout(resolve, 3));
const createdNow = () => ({ created_at: new Date().toISOString() });
const KIND = 'work-annotations-snapshot';
const body = (id, text) => ({ id, comment: text });

function setup() {
    const cache = createPrksOfflineStore({ dbName: 'prks-offline-v2:test', indexedDB: createFakeIndexedDBFactory() });
    const offline = createPrksOfflineRuntime({ store: cache, window: null,
        prksRequest: async () => { throw new Error('no reads in this scenario'); } });
    return { cache, offline };
}
function snapshot(rows, knownAbsent, generation) {
    return {
        items: rows.map(([id, , text]) => body(id, text)),
        annotations: rows.map(([id, revision]) => ({ annotation_id: id, revision })),
        known_absent: knownAbsent || {},
        canonical_annotation_set_revision: generation,
    };
}
const ack = (id, revision, text, extra) => ({ code: 'ACKNOWLEDGED', work_id: 'W', annotation_id: id,
    present: text !== null, annotation: text === null ? null : body(id, text), server_revision: revision,
    changed: true, ...(extra || {}) });
async function held(cache, id) {
    const snap = (await cache.getEntity(KIND, 'W')).value;
    const row = snap.annotations.find(r => r.annotation_id === id);
    const item = snap.items.find(i => i.id === id);
    return { revision: row ? row.revision : null, text: item ? item.comment : null,
        absent: snap.known_absent[id] === undefined ? null : snap.known_absent[id] };
}

async function main() {
    /* SET: another device moved A to revision 2 and this browser hydrated it. */
    {
        const { cache, offline } = setup();
        const op = createdNow();
        await later();
        await cache.putEntity(KIND, 'W', snapshot([['A', 2, 'newer']], {}, 5));
        assert.equal(await offline.reconcilePdfAnnotation(ack('A', 1, 'older', { canonical_annotation_set_revision: 4 }), op),
            true, 'the operation retires');
        assert.deepEqual(await held(cache, 'A'), { revision: 2, text: 'newer', absent: null },
            'a delayed SET keeps the newer body and revision');
        assert.equal((await cache.getEntity(KIND, 'W')).value.canonical_annotation_set_revision, 5);
    }
    /* DELETE: the snapshot holds A live at revision 3; a revision-2 DELETE is late. */
    {
        const { cache, offline } = setup();
        await cache.putEntity(KIND, 'W', snapshot([['A', 3, 'restored']], {}, 7));
        assert(await offline.reconcilePdfAnnotation(ack('A', 2, null), createdNow()));
        assert.deepEqual(await held(cache, 'A'), { revision: 3, text: 'restored', absent: null },
            'a delayed DELETE cannot remove a newer annotation');
    }
    /* Tombstone: A was deleted at revision 4; an older SET cannot revive it. */
    {
        const { cache, offline } = setup();
        await cache.putEntity(KIND, 'W', snapshot([], { A: 4 }, 9));
        assert(await offline.reconcilePdfAnnotation(ack('A', 3, 'ghost'), createdNow()));
        assert.deepEqual(await held(cache, 'A'), { revision: null, text: null, absent: 4 },
            'a tombstone outranks an older ACK');
    }
    /* Equal revision: a live row is never turned into a deletion by a tie. */
    {
        const { cache, offline } = setup();
        await cache.putEntity(KIND, 'W', snapshot([['A', 2, 'live']], {}, 3));
        assert(await offline.reconcilePdfAnnotation(ack('A', 2, null), createdNow()));
        assert.equal((await held(cache, 'A')).text, 'live');
    }
    /* Forward SET and DELETE apply. */
    {
        const { cache, offline } = setup();
        await cache.putEntity(KIND, 'W', snapshot([['A', 1, 'first']], {}, 1));
        assert(await offline.reconcilePdfAnnotation(ack('A', 2, 'second'), createdNow()));
        assert.deepEqual(await held(cache, 'A'), { revision: 2, text: 'second', absent: null }, 'a forward SET applies');
        assert(await offline.reconcilePdfAnnotation(ack('A', 3, null), createdNow()));
        assert.deepEqual(await held(cache, 'A'), { revision: null, text: null, absent: 3 }, 'a forward DELETE applies');
        assert(await offline.reconcilePdfAnnotation(ack('A', 2, 'second'), createdNow()));
        assert.deepEqual(await held(cache, 'A'), { revision: null, text: null, absent: 3 },
            'a replayed older SET cannot revive it');
    }
    /* CREATE of an id the snapshot does not name: proven by read time. */
    {
        const { cache, offline } = setup();
        await cache.putEntity(KIND, 'W', snapshot([], {}, 1));
        await later();
        assert(await offline.reconcilePdfAnnotation(ack('N', 1, 'new'), createdNow()));
        assert.deepEqual(await held(cache, 'N'), { revision: 1, text: 'new', absent: null },
            'a CREATE on a snapshot read before it applies');
    }
    {
        const { cache, offline } = setup();
        const op = createdNow();
        await later();
        await cache.putEntity(KIND, 'W', snapshot([], {}, 1));
        assert(await offline.reconcilePdfAnnotation(ack('N', 1, 'new'), op));
        assert.equal(await cache.getEntity(KIND, 'W'), null,
            'a snapshot read after the CREATE that does not name it is re-hydrated');
    }
    {
        const { cache, offline } = setup();
        const op = createdNow();
        await later();
        await cache.putEntity(KIND, 'W', snapshot([], {}, 1));
        cache.deleteEntity = async () => { throw new Error('quota'); };
        assert.equal(await offline.reconcilePdfAnnotation(ack('N', 1, 'new'), op), false,
            'a snapshot that could not be dropped keeps the operation queued');
    }
    console.log('All ' + checks + ' PDF annotation ACK ordering checks passed');
}

main().then(() => process.exit(0)).catch(error => { console.error(error); process.exit(1); });
