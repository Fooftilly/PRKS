#!/usr/bin/env node
'use strict';

/* Deterministic coverage for the disposable IndexedDB client cache
 * (offline-store.js). Uses a small in-memory fake IndexedDB factory (async
 * via setTimeout, not synchronous) so behavior is exercised through real
 * IndexedDB-shaped request/transaction semantics, not source-string
 * assertions.
 */

const path = require('path');
const rootDir = path.resolve(__dirname, '../..');

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

const {
    FakeIDBKeyRange,
    createFakeIndexedDBFactory,
    installFakeIdbGlobals,
} = require(path.join(rootDir, 'tests/browser/lib/fake_indexeddb.js'));

installFakeIdbGlobals(globalThis);

const idbApi = require(path.join(rootDir, 'frontend/vendor/idb/idb.min.js'));

/* ------------------------------------------------------------------------ */

function loadOfflineStoreModule() {
    // offline-store.js self-registers onto (self||window||globalThis) as a side
    // effect of require(); use a fresh require each time isn't possible (module
    // cache), so we only need the exported factory -- deps are always passed
    // explicitly per test, never relying on that global registration.
    return require(path.join(rootDir, 'frontend/js/offline-store.js'));
}

function withIdb(opts) {
    return Object.assign({ idb: idbApi }, opts || {});
}

async function run() {
    const mod = loadOfflineStoreModule();

    /* ---- open DB / write entity / read entity ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksOfflineStore(withIdb({ indexedDB: idb, now: () => 1000 }));
        assert('isAvailable true with working fake IndexedDB', await store.isAvailable());
        const putOk = await store.putEntity('work', 'W-1', { title: 'Ontology of X' }, 'rev-1');
        assert('putEntity resolves true on success', putOk === true);
        const got = await store.getEntity('work', 'W-1');
        assert('getEntity returns the stored envelope', !!got);
        assertEq('getEntity value round-trips', got.value, { title: 'Ontology of X' });
        assertEq('getEntity kind round-trips', got.kind, 'work');
        assertEq('getEntity id round-trips', got.id, 'W-1');
        assertEq('getEntity cachedAt uses injected now()', got.cachedAt, 1000);
        assertEq('getEntity sourceRevision round-trips', got.sourceRevision, 'rev-1');
    }

    /* ---- missing item ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksOfflineStore(withIdb({ indexedDB: idb  }));
        const got = await store.getEntity('work', 'W-does-not-exist');
        assertEq('getEntity missing item returns null', got, null);
        const gotList = await store.getList('search:none');
        assertEq('getList missing item returns null', gotList, null);
    }

    /* ---- overwrite with newer cache (last write wins, full replace not merge) ---- */
    {
        const idb = createFakeIndexedDBFactory();
        let clock = 1;
        const store = mod.createPrksOfflineStore(withIdb({ indexedDB: idb, now: () => clock  }));
        await store.putEntity('work', 'W-2', { title: 'first version', tags: ['a'] }, 'rev-1');
        clock = 2;
        await store.putEntity('work', 'W-2', { title: 'second version' }, 'rev-2');
        const got = await store.getEntity('work', 'W-2');
        assertEq('overwrite replaces value entirely', got.value, { title: 'second version' });
        assertEq('overwrite updates cachedAt', got.cachedAt, 2);
        assertEq('overwrite updates sourceRevision', got.sourceRevision, 'rev-2');
    }

    /* ---- delete entity / delete list ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksOfflineStore(withIdb({ indexedDB: idb  }));
        await store.putEntity('person', 'P-1', { name: 'A. Researcher' }, '');
        await store.putList('works:recent', { ids: ['W-1', 'W-2'] }, '');
        assert('delete entity resolves true', (await store.deleteEntity('person', 'P-1')) === true);
        assertEq('entity gone after delete', await store.getEntity('person', 'P-1'), null);
        assert('delete list resolves true', (await store.deleteList('works:recent')) === true);
        assertEq('list gone after delete', await store.getList('works:recent'), null);
    }

    /* ---- clear cache ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksOfflineStore(withIdb({ indexedDB: idb  }));
        await store.putEntity('work', 'W-1', { title: 'x' }, '');
        await store.putEntity('work', 'W-2', { title: 'y' }, '');
        await store.putList('works:recent', { ids: ['W-1'] }, '');
        const statsBefore = await store.stats();
        assert('stats sees non-zero counts before clear', statsBefore.entityCount === 2 && statsBefore.listCount === 1);
        const cleared = await store.clearAll();
        assert('clearAll resolves true', cleared === true);
        const statsAfter = await store.stats();
        assertEq('stats entityCount is zero after clear', statsAfter.entityCount, 0);
        assertEq('stats listCount is zero after clear', statsAfter.listCount, 0);
        assertEq('entity actually gone after clearAll', await store.getEntity('work', 'W-1'), null);
    }

    /* ---- schema upgrade: existing data survives a version bump ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const storeV1 = mod.createPrksOfflineStore(withIdb({ indexedDB: idb, dbVersion: 1  }));
        await storeV1.putEntity('concept', 'C-1', { name: 'Realism' }, '');
        const storeV2 = mod.createPrksOfflineStore(withIdb({ indexedDB: idb, dbVersion: 2  }));
        const got = await storeV2.getEntity('concept', 'C-1');
        assert('data written under v1 is visible after opening at v2', !!got);
        assertEq('upgraded-open value survives unchanged', got.value, { name: 'Realism' });
        const canWriteAfterUpgrade = await storeV2.putEntity('concept', 'C-2', { name: 'Idealism' }, '');
        assert('can still write after a version bump', canWriteAfterUpgrade === true);
    }

    /* ---- IndexedDB unavailable: every call degrades to a safe result, never throws ---- */
    {
        const store = mod.createPrksOfflineStore(withIdb({ indexedDB: null  }));
        assertEq('isAvailable is false with no IndexedDB', await store.isAvailable(), false);
        assertEq('getEntity degrades to null with no IndexedDB', await store.getEntity('work', 'W-1'), null);
        assertEq('putEntity degrades to false with no IndexedDB', await store.putEntity('work', 'W-1', {}, ''), false);
        assertEq('deleteEntity degrades to false with no IndexedDB', await store.deleteEntity('work', 'W-1'), false);
        assertEq('clearAll degrades to false with no IndexedDB', await store.clearAll(), false);
        const stats = await store.stats();
        assertEq('stats reports unavailable with no IndexedDB', stats.available, false);
        assertEq('stats reports zero counts with no IndexedDB', stats.entityCount, 0);
    }

    /* ---- open() throwing synchronously also degrades safely ---- */
    {
        const throwingFactory = {
            open: function () {
                throw new Error('IndexedDB is blocked by browser policy');
            },
        };
        const store = mod.createPrksOfflineStore(withIdb({ indexedDB: throwingFactory  }));
        assertEq('getEntity degrades to null when open() throws', await store.getEntity('work', 'W-1'), null);
        assertEq('isAvailable is false when open() throws', await store.isAvailable(), false);
    }

    /* ---- open() reporting onerror also degrades safely ---- */
    {
        const erroringFactory = {
            open: function () {
                const req = makeRequest();
                fireAsync(function () {
                    if (req.onerror) req.onerror({ target: req });
                });
                return req;
            },
        };
        const store = mod.createPrksOfflineStore(withIdb({ indexedDB: erroringFactory  }));
        assertEq('getEntity degrades to null on open() onerror', await store.getEntity('work', 'W-1'), null);
    }

    /* ---- quota/write failure never fails the online flow (store-level: put resolves false, does not throw) ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksOfflineStore(withIdb({ indexedDB: idb  }));
        // Force the underlying entities store to fail every write, simulating a
        // quota-exceeded/blocked write, without touching the public store API.
        const dbEntry = idb.__databases;
        // Trigger the db to be created first.
        await store.isAvailable();
        const db = dbEntry.get(mod.PRKS_OFFLINE_DB_NAME);
        assert('fake db was created for quota-failure setup', !!db);
        db._stores.get('entities').forceError = true;

        let threw = false;
        let putResult;
        try {
            putResult = await store.putEntity('work', 'W-quota', { title: 'big note' }, '');
        } catch (_e) {
            threw = true;
        }
        assert('putEntity never throws on a simulated write failure', !threw);
        assertEq('putEntity resolves false on a simulated write failure', putResult, false);

        let readThrew = false;
        try {
            await store.getEntity('work', 'W-quota');
        } catch (_e) {
            readThrew = true;
        }
        assert('a failed write does not poison later reads (still no throw)', !readThrew);
    }

    /* ---- deleteDatabase discards only the disposable cache, resolves cleanly ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksOfflineStore(withIdb({ indexedDB: idb  }));
        await store.putEntity('work', 'W-1', { title: 'x' }, '');
        const ok = await store.deleteDatabase();
        assert('deleteDatabase resolves true', ok === true);
        assertEq('database removed from the fake factory registry', idb.__databases.has(mod.PRKS_OFFLINE_DB_NAME), false);
    }

    /* ---- getEntitiesByKind: every cached row of one kind, and only that kind ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksOfflineStore(withIdb({ indexedDB: idb, idbKeyRange: FakeIDBKeyRange  }));
        await store.putEntity('folder', 'F-1', { id: 'F-1', works: [{ id: 'W-1' }] }, '');
        await store.putEntity('folder', 'F-2', { id: 'F-2', works: [] }, '');
        await store.putEntity('person', 'P-1', { id: 'P-1', works: [] }, '');
        const rows = await store.getEntitiesByKind('folder');
        assertEq('getEntitiesByKind returns every row of the kind', rows.length, 2);
        assertEq('getEntitiesByKind returns them sorted by key',
            rows.map(r => r.id).sort(), ['F-1', 'F-2']);
        assert('rows carry their kind', rows.every(r => r.kind === 'folder'));
        assert('rows carry their cached value', !!rows[0].value.id);
        assertEq('a kind with nothing cached is an empty list, not null',
            await store.getEntitiesByKind('playlist'), []);
    }

    /* ---- getEntitiesByKind: without IDBKeyRange the scan must still not leak other kinds ----
     * The reconciler patches Work summaries embedded in these rows, so a
     * `person` row reaching the folder pass would be written back under the
     * wrong kind. */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksOfflineStore(withIdb({ indexedDB: idb, idbKeyRange: null  }));
        await store.putEntity('folder', 'F-1', { id: 'F-1' }, '');
        await store.putEntity('person', 'P-1', { id: 'P-1' }, '');
        const rows = await store.getEntitiesByKind('folder');
        assertEq('fallback scan returns only the requested kind', rows.map(r => r.id), ['F-1']);
    }

    /* ---- getEntitiesByKind: a failing read is an empty list, never a throw ----
     * Reconciliation calls this on the acknowledgement path; a rejection there
     * would surface as "could not save that locally" for a save that DID
     * succeed on the server. */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksOfflineStore(withIdb({ indexedDB: idb, idbKeyRange: FakeIDBKeyRange  }));
        await store.putEntity('folder', 'F-1', { id: 'F-1' }, '');
        idb.__databases.get(mod.PRKS_OFFLINE_DB_NAME)._stores.get('entities').forceError = true;
        let threw = false;
        let rows;
        try {
            rows = await store.getEntitiesByKind('folder');
        } catch (_e) {
            threw = true;
        }
        assert('getEntitiesByKind never throws to the caller', !threw);
        assertEq('a failed read reports null, not an empty list', rows, null);
    }

    /* ---- deleteEntitiesByKind: removes one whole kind, leaves every other kind intact ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksOfflineStore(withIdb({ indexedDB: idb, idbKeyRange: FakeIDBKeyRange  }));
        await store.putEntity('concept', 'C-1', { id: 'C-1', name: 'Alpha' }, '');
        await store.putEntity('concept', 'C-2', { id: 'C-2', name: 'Beta' }, '');
        await store.putEntity('work', 'W-1', { id: 'W-1', title: 'Kept Work' }, '');
        const ok = await store.deleteEntitiesByKind('concept');
        assert('deleteEntitiesByKind resolves true after a completed sweep', ok === true);
        assertEq('first Concept row removed', await store.getEntity('concept', 'C-1'), null);
        assertEq('second Concept row removed', await store.getEntity('concept', 'C-2'), null);
        const keptWork = await store.getEntity('work', 'W-1');
        assert('another kind is untouched by a kind sweep', !!keptWork);
        assertEq('untouched kind keeps its value', keptWork.value, { id: 'W-1', title: 'Kept Work' });
        assertEq('lists are not part of an entity-kind sweep', await store.getList('concepts:index'), null);
    }

    /* ---- deleteEntitiesByKind: an empty kind is a completed sweep, not a failure ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksOfflineStore(withIdb({ indexedDB: idb, idbKeyRange: FakeIDBKeyRange  }));
        await store.putEntity('work', 'W-1', { id: 'W-1' }, '');
        assertEq('sweeping a kind with no rows resolves true', await store.deleteEntitiesByKind('concept'), true);
        assert('unrelated kind still present', !!(await store.getEntity('work', 'W-1')));
    }

    /* ---- deleteEntitiesByKind: falls back to a getAll scan when no key range is available ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksOfflineStore(withIdb({ indexedDB: idb, idbKeyRange: null  }));
        await store.putEntity('concept', 'C-1', { id: 'C-1' }, '');
        await store.putEntity('concept', 'C-2', { id: 'C-2' }, '');
        await store.putEntity('work', 'W-1', { id: 'W-1' }, '');
        assertEq('kind sweep without IDBKeyRange still resolves true', await store.deleteEntitiesByKind('concept'), true);
        assertEq('fallback sweep removed the kind', await store.getEntity('concept', 'C-2'), null);
        assert('fallback sweep left other kinds alone', !!(await store.getEntity('work', 'W-1')));
    }

    /* ---- deleteEntitiesByKind: a failing sweep never throws and reports false so callers can stay conservative ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksOfflineStore(withIdb({ indexedDB: idb, idbKeyRange: FakeIDBKeyRange  }));
        await store.putEntity('concept', 'C-1', { id: 'C-1' }, '');
        const db = idb.__databases.get(mod.PRKS_OFFLINE_DB_NAME);
        assert('fake db was created for the sweep-failure setup', !!db);
        db._stores.get('entities').forceError = true;
        let threw = false;
        let result;
        try {
            result = await store.deleteEntitiesByKind('concept');
        } catch (_e) {
            threw = true;
        }
        assert('deleteEntitiesByKind never throws to the caller', !threw);
        assertEq('a failed sweep reports false', result, false);
    }

    /* ---- deleteEntitiesByKind: requests succeeding but the transaction aborting reports FALSE ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksOfflineStore(withIdb({ indexedDB: idb, idbKeyRange: FakeIDBKeyRange  }));
        await store.putEntity('concept', 'C-1', { id: 'C-1' }, '');
        await store.putEntity('concept', 'C-2', { id: 'C-2' }, '');
        const db = idb.__databases.get(mod.PRKS_OFFLINE_DB_NAME);
        assert('fake db created for the commit-failure setup', !!db);
        // Every delete request succeeds; the transaction then aborts at commit.
        db._stores.get('entities').failCommit = true;
        const swept = await store.deleteEntitiesByKind('concept');
        assertEq(
            'a sweep whose transaction aborts at commit reports false, not the request-level success',
            swept,
            false
        );
        db._stores.get('entities').failCommit = false;
        // The rows are still there, which is exactly why false had to be reported.
        assert('rows survive an aborted sweep transaction', !!(await store.getEntity('concept', 'C-1')));
    }

    /* ---- readwrite results come from transaction commit, not merely request success ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksOfflineStore(withIdb({ indexedDB: idb  }));
        await store.putEntity('work', 'W-1', { id: 'W-1' }, '');
        const db = idb.__databases.get(mod.PRKS_OFFLINE_DB_NAME);
        db._stores.get('entities').failCommit = true;
        assertEq('putEntity reports false when its transaction aborts at commit', await store.putEntity('work', 'W-2', { id: 'W-2' }, ''), false);
        assertEq('deleteEntity reports false when its transaction aborts at commit', await store.deleteEntity('work', 'W-1'), false);
        db._stores.get('lists').failCommit = true;
        assertEq('deleteList reports false when its transaction aborts at commit', await store.deleteList('concepts:index'), false);
        db._stores.get('entities').failCommit = false;
        db._stores.get('lists').failCommit = false;
        assertEq('reads still work after an aborted write transaction', (await store.getEntity('work', 'W-1')).value, { id: 'W-1' });
        assertEq('a later write commits normally', await store.putEntity('work', 'W-2', { id: 'W-2' }, ''), true);
    }

    /* ---- deleteEntitiesByKind: no IndexedDB at all degrades to false, never a throw ---- */
    {
        const store = mod.createPrksOfflineStore(withIdb({ indexedDB: null  }));
        assertEq('kind sweep without IndexedDB resolves false', await store.deleteEntitiesByKind('concept'), false);
    }

    /* ---- module boundaries: no DOM, no routing, no fetch/network ---- */
    const src = require('fs').readFileSync(path.join(rootDir, 'frontend/js/offline-store.js'), 'utf8');
    assert('offline-store.js has no document/DOM access', src.indexOf('document.') === -1);
    assert('offline-store.js has no fetch() calls', !/\bfetch\s*\(/.test(src));
    assert('offline-store.js has no prksNavigate usage', src.indexOf('prksNavigate') === -1);

    if (failed) {
        console.log(failed + ' failed, ' + passed + ' passed');
        process.exit(1);
    }
    console.log('All ' + passed + ' offline store checks passed, 0 failed');
    process.exit(0);
}

run().catch(function (err) {
    console.error(err && err.stack ? err.stack : err);
    console.log('1 failed, ' + passed + ' passed');
    process.exit(1);
});
