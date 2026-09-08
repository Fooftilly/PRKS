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

/* ------------------------------------------------------------------------
 * Fake IndexedDB: in-memory, asynchronous (setTimeout-scheduled) requests,
 * enough surface for offline-store.js's open/transaction/store.get/put/
 * delete/clear/count/getAll usage. Databases persist for the lifetime of one
 * factory instance (so a second open() with a higher version simulates a
 * real schema-upgrade against existing data, not a fresh store).
 * ------------------------------------------------------------------------ */

function keyOf(keyPath, value) {
    if (Array.isArray(keyPath)) return keyPath.map((k) => value[k]);
    return value[keyPath];
}

function keyEquals(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
}

/* IndexedDB key ordering, enough for the compound ["kind", "id"] keys used
 * here: arrays sort after strings, arrays compare element-wise, and a shorter
 * array sorts before a longer one sharing its prefix. deleteEntitiesByKind()'s
 * ["kind"] .. ["kind", []] bound depends on exactly those rules. */
function keyTypeRank(v) {
    if (Array.isArray(v)) return 3;
    if (typeof v === 'string') return 2;
    return 1;
}

function compareKeys(a, b) {
    const ra = keyTypeRank(a);
    const rb = keyTypeRank(b);
    if (ra !== rb) return ra < rb ? -1 : 1;
    if (ra === 3) {
        const n = Math.min(a.length, b.length);
        for (let i = 0; i < n; i++) {
            const c = compareKeys(a[i], b[i]);
            if (c !== 0) return c;
        }
        return a.length === b.length ? 0 : a.length < b.length ? -1 : 1;
    }
    return a < b ? -1 : a > b ? 1 : 0;
}

const FakeIDBKeyRange = {
    bound: function (lower, upper, lowerOpen, upperOpen) {
        return {
            lower: lower,
            upper: upper,
            lowerOpen: !!lowerOpen,
            upperOpen: !!upperOpen,
            includes: function (key) {
                const lo = compareKeys(key, lower);
                if (lo < 0 || (lo === 0 && this.lowerOpen)) return false;
                const hi = compareKeys(key, upper);
                if (hi > 0 || (hi === 0 && this.upperOpen)) return false;
                return true;
            },
        };
    },
};

function fireAsync(fn) {
    setTimeout(fn, 0);
}

class FakeObjectStore {
    constructor(name, keyPath) {
        this.name = name;
        this.keyPath = keyPath;
        this.rows = [];
        this.forceError = false;
    }
}

class FakeDatabase {
    constructor(name) {
        this.name = name;
        this.version = 0;
        this._stores = new Map();
        this.onversionchange = null;
        this.objectStoreNames = {
            contains: (n) => this._stores.has(n),
        };
    }
    createObjectStore(name, opts) {
        const store = new FakeObjectStore(name, opts && opts.keyPath);
        this._stores.set(name, store);
        return store;
    }
    transaction(storeNames, mode) {
        const db = this;
        const tx = {
            mode: mode,
            oncomplete: null,
            onerror: null,
            onabort: null,
            _aborted: false,
            objectStore: function (name) {
                const store = db._stores.get(name);
                if (!store) throw new Error('No such object store: ' + name);
                return makeStoreHandle(store, tx);
            },
            abort: function () {
                if (tx._aborted) return;
                tx._aborted = true;
                fireAsync(function () {
                    if (tx.onabort) tx.onabort({ target: tx });
                });
            },
        };
        fireAsync(function () {
            if (!tx._aborted && tx.oncomplete) tx.oncomplete({ target: tx });
        });
        return tx;
    }
    close() {}
}

function makeRequest() {
    return { result: undefined, error: undefined, onsuccess: null, onerror: null };
}

function makeStoreHandle(store, tx) {
    function op(fn) {
        const req = makeRequest();
        fireAsync(function () {
            if (tx._aborted) return;
            try {
                if (store.forceError) throw new Error('Simulated QuotaExceededError');
                const result = fn();
                req.result = result;
                if (req.onsuccess) req.onsuccess({ target: req });
            } catch (e) {
                req.error = e;
                if (req.onerror) req.onerror({ target: req });
            }
        });
        return req;
    }
    return {
        get: function (key) {
            return op(function () {
                return store.rows.find((r) => keyEquals(keyOf(store.keyPath, r), key));
            });
        },
        put: function (value) {
            return op(function () {
                const key = keyOf(store.keyPath, value);
                const idx = store.rows.findIndex((r) => keyEquals(keyOf(store.keyPath, r), key));
                if (idx >= 0) store.rows[idx] = value;
                else store.rows.push(value);
                return key;
            });
        },
        delete: function (key) {
            return op(function () {
                const idx = store.rows.findIndex((r) => keyEquals(keyOf(store.keyPath, r), key));
                if (idx >= 0) store.rows.splice(idx, 1);
                return undefined;
            });
        },
        clear: function () {
            return op(function () {
                store.rows = [];
                return undefined;
            });
        },
        count: function () {
            return op(function () {
                return store.rows.length;
            });
        },
        getAll: function () {
            return op(function () {
                return store.rows.slice();
            });
        },
        openCursor: function (range) {
            const req = makeRequest();
            const keys = store.rows
                .map(function (r) {
                    return keyOf(store.keyPath, r);
                })
                .filter(function (k) {
                    return !range || range.includes(k);
                })
                .sort(compareKeys);
            let idx = -1;
            function rowFor(key) {
                return store.rows.find(function (r) {
                    return keyEquals(keyOf(store.keyPath, r), key);
                });
            }
            function advance() {
                fireAsync(function () {
                    if (tx._aborted) return;
                    idx += 1;
                    while (idx < keys.length && !rowFor(keys[idx])) idx += 1;
                    if (idx >= keys.length) {
                        req.result = null;
                        if (req.onsuccess) req.onsuccess({ target: req });
                        return;
                    }
                    const key = keys[idx];
                    req.result = {
                        key: key,
                        value: rowFor(key),
                        delete: function () {
                            return op(function () {
                                const i = store.rows.findIndex(function (r) {
                                    return keyEquals(keyOf(store.keyPath, r), key);
                                });
                                if (i >= 0) store.rows.splice(i, 1);
                                return undefined;
                            });
                        },
                        continue: function () {
                            advance();
                        },
                    };
                    if (req.onsuccess) req.onsuccess({ target: req });
                });
            }
            advance();
            return req;
        },
    };
}

function createFakeIndexedDBFactory() {
    const databases = new Map();
    return {
        open: function (name, version) {
            const req = makeRequest();
            fireAsync(function () {
                let db = databases.get(name);
                const isNew = !db;
                if (!db) {
                    db = new FakeDatabase(name);
                    databases.set(name, db);
                }
                const needsUpgrade = isNew || version > db.version;
                if (needsUpgrade) {
                    db.version = version;
                    req.result = db;
                    if (req.onupgradeneeded) req.onupgradeneeded({ target: req });
                }
                req.result = db;
                if (req.onsuccess) req.onsuccess({ target: req });
            });
            return req;
        },
        deleteDatabase: function (name) {
            const req = makeRequest();
            fireAsync(function () {
                databases.delete(name);
                if (req.onsuccess) req.onsuccess({ target: req });
            });
            return req;
        },
        __databases: databases,
    };
}

/* ------------------------------------------------------------------------ */

function loadOfflineStoreModule() {
    // offline-store.js self-registers onto (self||window||globalThis) as a side
    // effect of require(); use a fresh require each time isn't possible (module
    // cache), so we only need the exported factory -- deps are always passed
    // explicitly per test, never relying on that global registration.
    return require(path.join(rootDir, 'frontend/js/offline-store.js'));
}

async function run() {
    const mod = loadOfflineStoreModule();

    /* ---- open DB / write entity / read entity ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksOfflineStore({ indexedDB: idb, now: () => 1000 });
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
        const store = mod.createPrksOfflineStore({ indexedDB: idb });
        const got = await store.getEntity('work', 'W-does-not-exist');
        assertEq('getEntity missing item returns null', got, null);
        const gotList = await store.getList('search:none');
        assertEq('getList missing item returns null', gotList, null);
    }

    /* ---- overwrite with newer cache (last write wins, full replace not merge) ---- */
    {
        const idb = createFakeIndexedDBFactory();
        let clock = 1;
        const store = mod.createPrksOfflineStore({ indexedDB: idb, now: () => clock });
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
        const store = mod.createPrksOfflineStore({ indexedDB: idb });
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
        const store = mod.createPrksOfflineStore({ indexedDB: idb });
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
        const storeV1 = mod.createPrksOfflineStore({ indexedDB: idb, dbVersion: 1 });
        await storeV1.putEntity('concept', 'C-1', { name: 'Realism' }, '');
        const storeV2 = mod.createPrksOfflineStore({ indexedDB: idb, dbVersion: 2 });
        const got = await storeV2.getEntity('concept', 'C-1');
        assert('data written under v1 is visible after opening at v2', !!got);
        assertEq('upgraded-open value survives unchanged', got.value, { name: 'Realism' });
        const canWriteAfterUpgrade = await storeV2.putEntity('concept', 'C-2', { name: 'Idealism' }, '');
        assert('can still write after a version bump', canWriteAfterUpgrade === true);
    }

    /* ---- IndexedDB unavailable: every call degrades to a safe result, never throws ---- */
    {
        const store = mod.createPrksOfflineStore({ indexedDB: null });
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
        const store = mod.createPrksOfflineStore({ indexedDB: throwingFactory });
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
        const store = mod.createPrksOfflineStore({ indexedDB: erroringFactory });
        assertEq('getEntity degrades to null on open() onerror', await store.getEntity('work', 'W-1'), null);
    }

    /* ---- quota/write failure never fails the online flow (store-level: put resolves false, does not throw) ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksOfflineStore({ indexedDB: idb });
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
        const store = mod.createPrksOfflineStore({ indexedDB: idb });
        await store.putEntity('work', 'W-1', { title: 'x' }, '');
        const ok = await store.deleteDatabase();
        assert('deleteDatabase resolves true', ok === true);
        assertEq('database removed from the fake factory registry', idb.__databases.has(mod.PRKS_OFFLINE_DB_NAME), false);
    }

    /* ---- deleteEntitiesByKind: removes one whole kind, leaves every other kind intact ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksOfflineStore({ indexedDB: idb, idbKeyRange: FakeIDBKeyRange });
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
        const store = mod.createPrksOfflineStore({ indexedDB: idb, idbKeyRange: FakeIDBKeyRange });
        await store.putEntity('work', 'W-1', { id: 'W-1' }, '');
        assertEq('sweeping a kind with no rows resolves true', await store.deleteEntitiesByKind('concept'), true);
        assert('unrelated kind still present', !!(await store.getEntity('work', 'W-1')));
    }

    /* ---- deleteEntitiesByKind: falls back to a getAll scan when no key range is available ---- */
    {
        const idb = createFakeIndexedDBFactory();
        const store = mod.createPrksOfflineStore({ indexedDB: idb, idbKeyRange: null });
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
        const store = mod.createPrksOfflineStore({ indexedDB: idb, idbKeyRange: FakeIDBKeyRange });
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

    /* ---- deleteEntitiesByKind: no IndexedDB at all degrades to false, never a throw ---- */
    {
        const store = mod.createPrksOfflineStore({ indexedDB: null });
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
