/* Shared in-memory fake IndexedDB for Node selftests.
 *
 * Asynchronous (setTimeout-scheduled) rather than synchronous, so behavior is
 * exercised through real IndexedDB-shaped request/transaction semantics rather
 * than source-string assertions. Databases persist for the lifetime of one
 * factory instance, so a second open() with a higher version simulates a real
 * schema upgrade against existing data rather than a fresh store.
 *
 * Two knobs make the important failure modes testable:
 *   store.forceError  -- the REQUEST fails
 *   store.failCommit  -- the requests succeed and the TRANSACTION then aborts
 * The second is what separates "the write was reported" from "the write
 * committed", which durable local state must never confuse.
 *
 * Used by run_offline_store_selftest.js and run_local_store_selftest.js.
 */
'use strict';

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
        // Real IndexedDB blocks deleteDatabase() on every OPEN connection --
        // including the deleting page's own. Modelling that is what makes a
        // "close your handle before deleting" regression meaningful.
        this._openConnections = 0;
        this.objectStoreNames = {
            contains: (n) => this._stores.has(n),
        };
    }
    createObjectStore(name, opts) {
        const store = new FakeObjectStore(name, opts && opts.keyPath);
        this._stores.set(name, store);
        return store;
    }
    /* Models the request/commit distinction real IndexedDB has: `oncomplete`
     * fires only once every request scheduled in the transaction has settled
     * (not immediately on creation), so tests can tell "the delete request
     * succeeded" apart from "the transaction committed". A store's
     * `failCommit` flag lets a transaction abort AFTER its requests succeeded,
     * which is exactly the case domain-cleanup coherence must survive. */
    transaction(storeNames, mode) {
        const db = this;
        const names = Array.isArray(storeNames) ? storeNames : [storeNames];
        const tx = {
            mode: mode,
            oncomplete: null,
            onerror: null,
            onabort: null,
            _aborted: false,
            _settled: false,
            _pending: 0,
            _started: false,
            // Writes are staged the way a real transaction stages them: an
            // abort rolls the object store back, so a caller that trusted a
            // request-level success would be reasoning about rows that still
            // exist.
            _snapshots: new Map(),
            _snapshot: function (st) {
                if (!tx._snapshots.has(st)) tx._snapshots.set(st, st.rows.slice());
            },
            _rollback: function () {
                tx._snapshots.forEach(function (rows, st) {
                    st.rows = rows;
                });
                tx._snapshots.clear();
            },
            objectStore: function (name) {
                const store = db._stores.get(name);
                if (!store) throw new Error('No such object store: ' + name);
                return makeStoreHandle(store, tx);
            },
            _maybeSettle: function () {
                if (tx._settled || tx._aborted || tx._pending > 0) return;
                const shouldFailCommit = names.some(function (n) {
                    const st = db._stores.get(n);
                    return !!(st && st.failCommit);
                });
                tx._settled = true;
                if (shouldFailCommit) {
                    tx._aborted = true;
                    tx._rollback();
                    if (tx.onabort) tx.onabort({ target: tx });
                    return;
                }
                tx._snapshots.clear();
                if (tx.oncomplete) tx.oncomplete({ target: tx });
            },
            abort: function () {
                if (tx._aborted) return;
                tx._aborted = true;
                tx._settled = true;
                tx._rollback();
                fireAsync(function () {
                    if (tx.onabort) tx.onabort({ target: tx });
                });
            },
        };
        // A transaction with no requests at all still completes on its own.
        fireAsync(function () {
            tx._started = true;
            tx._maybeSettle();
        });
        return tx;
    }
    close() {
        if (this._openConnections > 0) this._openConnections -= 1;
    }
}

function makeRequest() {
    return { result: undefined, error: undefined, onsuccess: null, onerror: null };
}

function makeStoreHandle(store, tx) {
    function op(fn) {
        const req = makeRequest();
        tx._pending += 1;
        fireAsync(function () {
            if (tx._aborted) {
                tx._pending -= 1;
                return;
            }
            try {
                if (store.forceError) throw new Error('Simulated QuotaExceededError');
                tx._snapshot(store);
                const result = fn();
                req.result = result;
                if (req.onsuccess) req.onsuccess({ target: req });
            } catch (e) {
                req.error = e;
                if (req.onerror) req.onerror({ target: req });
            }
            tx._pending -= 1;
            // Only settle once the transaction has had a chance to schedule
            // follow-up requests (a cursor continues from within onsuccess).
            fireAsync(function () {
                if (tx._started) tx._maybeSettle();
            });
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
        getAll: function (range, count) {
            // Real `getAll` filters by the key range and yields rows in key
            // order. A fake that returned everything unfiltered would let a
            // broken range pass -- and code that relies on the range would
            // then only fail in a real browser.
            return op(function () {
                const rows = store.rows
                    .filter(function (r) {
                        return !range || range.includes(keyOf(store.keyPath, r));
                    })
                    .sort(function (a, b) {
                        return compareKeys(keyOf(store.keyPath, a), keyOf(store.keyPath, b));
                    });
                return typeof count === 'number' ? rows.slice(0, count) : rows;
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
                // Cursor iteration keeps its transaction alive exactly like a
                // pending request does, so the fake must not let the
                // transaction commit between two cursor steps.
                tx._pending += 1;
                fireAsync(function () {
                    if (tx._aborted) {
                        tx._pending -= 1;
                        return;
                    }
                    idx += 1;
                    while (idx < keys.length && !rowFor(keys[idx])) idx += 1;
                    if (idx >= keys.length) {
                        req.result = null;
                        if (req.onsuccess) req.onsuccess({ target: req });
                        tx._pending -= 1;
                        fireAsync(function () {
                            if (tx._started) tx._maybeSettle();
                        });
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
                    tx._pending -= 1;
                    fireAsync(function () {
                        if (tx._started) tx._maybeSettle();
                    });
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
                db._openConnections += 1;
                if (req.onsuccess) req.onsuccess({ target: req });
            });
            return req;
        },
        deleteDatabase: function (name) {
            const req = makeRequest();
            fireAsync(function () {
                const db = databases.get(name);
                if (db && db._openConnections > 0) {
                    // A caller that never closed its own handle blocks itself.
                    if (req.onblocked) req.onblocked({ target: req });
                    return;
                }
                databases.delete(name);
                if (req.onsuccess) req.onsuccess({ target: req });
            });
            return req;
        },
        __databases: databases,
    };
}

module.exports = {
    keyOf: keyOf,
    keyEquals: keyEquals,
    compareKeys: compareKeys,
    FakeIDBKeyRange: FakeIDBKeyRange,
    FakeDatabase: FakeDatabase,
    createFakeIndexedDBFactory: createFakeIndexedDBFactory,
};
