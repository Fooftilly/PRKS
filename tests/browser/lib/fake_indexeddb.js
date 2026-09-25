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
 * EventTarget-shaped requests/transactions (addEventListener) and global
 * IDB* constructors are installed so thin Promise wrappers such as `idb`
 * (instanceof + addEventListener) can exercise the same fake as onsuccess
 * property handlers.
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

class FakeEventTarget {
    constructor() {
        this._listeners = Object.create(null);
    }
    addEventListener(type, fn) {
        if (typeof fn !== 'function') return;
        const key = String(type);
        if (!this._listeners[key]) this._listeners[key] = [];
        this._listeners[key].push(fn);
    }
    removeEventListener(type, fn) {
        const key = String(type);
        const list = this._listeners[key];
        if (!list) return;
        const idx = list.indexOf(fn);
        if (idx >= 0) list.splice(idx, 1);
    }
    _emit(type, event) {
        const key = String(type);
        const evt = event || { type: key, target: this };
        const prop = 'on' + key;
        if (typeof this[prop] === 'function') {
            try {
                this[prop](evt);
            } catch (_e) {
                /* ignore listener errors the way browsers often do for tests */
            }
        }
        const list = (this._listeners[key] || []).slice();
        for (let i = 0; i < list.length; i++) {
            try {
                list[i](evt);
            } catch (_e) {
                /* ignore */
            }
        }
    }
}

class FakeIDBRequest extends FakeEventTarget {
    constructor() {
        super();
        this.result = undefined;
        this.error = undefined;
        this.onsuccess = null;
        this.onerror = null;
        this.onupgradeneeded = null;
        this.onblocked = null;
    }
}

class FakeIDBTransaction extends FakeEventTarget {
    constructor() {
        super();
        this.oncomplete = null;
        this.onerror = null;
        this.onabort = null;
        this.error = null;
    }
}

class FakeIDBObjectStore {
    /* Prototype exists so idb can detect get/put/delete/clear/count/getAll. */
}
FakeIDBObjectStore.prototype.get = function () {};
FakeIDBObjectStore.prototype.getKey = function () {};
FakeIDBObjectStore.prototype.getAll = function () {};
FakeIDBObjectStore.prototype.getAllKeys = function () {};
FakeIDBObjectStore.prototype.count = function () {};
FakeIDBObjectStore.prototype.put = function () {};
FakeIDBObjectStore.prototype.add = function () {};
FakeIDBObjectStore.prototype.delete = function () {};
FakeIDBObjectStore.prototype.clear = function () {};
FakeIDBObjectStore.prototype.openCursor = function () {};
FakeIDBObjectStore.prototype.index = function () {};

class FakeIDBIndex {}
FakeIDBIndex.prototype.get = FakeIDBObjectStore.prototype.get;
FakeIDBIndex.prototype.getKey = FakeIDBObjectStore.prototype.getKey;
FakeIDBIndex.prototype.getAll = FakeIDBObjectStore.prototype.getAll;
FakeIDBIndex.prototype.getAllKeys = FakeIDBObjectStore.prototype.getAllKeys;
FakeIDBIndex.prototype.count = FakeIDBObjectStore.prototype.count;
FakeIDBIndex.prototype.openCursor = FakeIDBObjectStore.prototype.openCursor;

class FakeIDBCursor {}
FakeIDBCursor.prototype.advance = function () {};
FakeIDBCursor.prototype.continue = function () {};
FakeIDBCursor.prototype.continuePrimaryKey = function () {};

class FakeObjectStore {
    constructor(name, keyPath) {
        this.name = name;
        this.keyPath = keyPath;
        this.rows = [];
        this.forceError = false;
        this.failCommit = false;
    }
}

class FakeIDBDatabase extends FakeEventTarget {
    constructor(name) {
        super();
        this.name = name;
        this.version = 0;
        this._stores = new Map();
        this.onversionchange = null;
        this.onclose = null;
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
        const tx = new FakeIDBTransaction();
        tx.mode = mode;
        tx._aborted = false;
        tx._settled = false;
        tx._pending = 0;
        tx._started = false;
        // Writes are staged the way a real transaction stages them: an
        // abort rolls the object store back, so a caller that trusted a
        // request-level success would be reasoning about rows that still
        // exist.
        tx._snapshots = new Map();
        // idb's tx.store helper indexes objectStoreNames[0]/[1].
        tx.objectStoreNames = names.slice();
        tx._snapshot = function (st) {
            if (!tx._snapshots.has(st)) tx._snapshots.set(st, st.rows.slice());
        };
        tx._rollback = function () {
            tx._snapshots.forEach(function (rows, st) {
                st.rows = rows;
            });
            tx._snapshots.clear();
        };
        tx.objectStore = function (name) {
            const store = db._stores.get(name);
            if (!store) throw new Error('No such object store: ' + name);
            return makeStoreHandle(store, tx);
        };
        tx._maybeSettle = function () {
            if (tx._settled || tx._aborted || tx._pending > 0) return;
            const shouldFailCommit = names.some(function (n) {
                const st = db._stores.get(n);
                return !!(st && st.failCommit);
            });
            tx._settled = true;
            if (shouldFailCommit) {
                tx._aborted = true;
                tx._rollback();
                tx.error = new Error('Simulated transaction abort');
                tx._emit('abort', { target: tx, type: 'abort' });
                return;
            }
            tx._snapshots.clear();
            tx._emit('complete', { target: tx, type: 'complete' });
        };
        tx.abort = function () {
            if (tx._aborted) return;
            tx._aborted = true;
            tx._settled = true;
            tx._rollback();
            tx.error = tx.error || new Error('Aborted');
            fireAsync(function () {
                tx._emit('abort', { target: tx, type: 'abort' });
            });
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

/** @deprecated alias kept for older selftest imports */
const FakeDatabase = FakeIDBDatabase;

function makeRequest() {
    return new FakeIDBRequest();
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
                req._emit('success', { target: req, type: 'success' });
            } catch (e) {
                req.error = e;
                req._emit('error', { target: req, type: 'error' });
                tx._pending -= 1;
                // Real IndexedDB aborts the transaction when a request errors
                // without preventDefault. Completing instead would hide the
                // idb `tx.done` rejection path that production must consume.
                try {
                    tx.abort();
                } catch (_abortErr) {
                    /* ignore */
                }
                return;
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
    const handle = Object.create(FakeIDBObjectStore.prototype);
    handle.name = store.name;
    handle.keyPath = store.keyPath;
    handle.get = function (key) {
        return op(function () {
            return store.rows.find((r) => keyEquals(keyOf(store.keyPath, r), key));
        });
    };
    handle.put = function (value) {
        return op(function () {
            const key = keyOf(store.keyPath, value);
            const idx = store.rows.findIndex((r) => keyEquals(keyOf(store.keyPath, r), key));
            if (idx >= 0) store.rows[idx] = value;
            else store.rows.push(value);
            return key;
        });
    };
    handle.delete = function (key) {
        return op(function () {
            const idx = store.rows.findIndex((r) => keyEquals(keyOf(store.keyPath, r), key));
            if (idx >= 0) store.rows.splice(idx, 1);
            return undefined;
        });
    };
    handle.clear = function () {
        return op(function () {
            store.rows = [];
            return undefined;
        });
    };
    handle.count = function () {
        return op(function () {
            return store.rows.length;
        });
    };
    handle.getAll = function (range, count) {
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
    };
    handle.openCursor = function (range) {
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
                    req._emit('success', { target: req, type: 'success' });
                    tx._pending -= 1;
                    fireAsync(function () {
                        if (tx._started) tx._maybeSettle();
                    });
                    return;
                }
                const key = keys[idx];
                const cursor = Object.create(FakeIDBCursor.prototype);
                cursor.key = key;
                cursor.value = rowFor(key);
                cursor.request = req;
                cursor.delete = function () {
                    return op(function () {
                        const i = store.rows.findIndex(function (r) {
                            return keyEquals(keyOf(store.keyPath, r), key);
                        });
                        if (i >= 0) store.rows.splice(i, 1);
                        return undefined;
                    });
                };
                cursor.continue = function () {
                    advance();
                };
                req.result = cursor;
                req._emit('success', { target: req, type: 'success' });
                tx._pending -= 1;
                fireAsync(function () {
                    if (tx._started) tx._maybeSettle();
                });
            });
        }
        advance();
        return req;
    };
    return handle;
}

/**
 * Installs IDB* globals required by `idb`'s instanceof / prototype checks.
 * Safe to call more than once; overwrites prior fake globals.
 */
function installFakeIdbGlobals(globalObj) {
    const g = globalObj || globalThis;
    g.IDBRequest = FakeIDBRequest;
    g.IDBTransaction = FakeIDBTransaction;
    g.IDBObjectStore = FakeIDBObjectStore;
    g.IDBIndex = FakeIDBIndex;
    g.IDBCursor = FakeIDBCursor;
    g.IDBDatabase = FakeIDBDatabase;
    if (typeof g.IDBKeyRange === 'undefined') {
        g.IDBKeyRange = FakeIDBKeyRange;
    }
    return g;
}

function createFakeIndexedDBFactory() {
    // Thin Promise wrappers (idb) need IDB* constructors + addEventListener.
    // Install once per process so every selftest that builds a fake factory
    // can createPrksOfflineStore without repeating the polyfill.
    installFakeIdbGlobals(globalThis);
    const databases = new Map();
    return {
        open: function (name, version) {
            const req = makeRequest();
            fireAsync(function () {
                let db = databases.get(name);
                const isNew = !db;
                if (!db) {
                    db = new FakeIDBDatabase(name);
                    databases.set(name, db);
                }
                const oldVersion = db.version;
                const needsUpgrade = isNew || version > db.version;
                if (needsUpgrade) {
                    db.version = version;
                    req.result = db;
                    // Upgrade transactions are not fully modelled; createObjectStore
                    // runs against the database handle during upgradeneeded.
                    req.transaction = null;
                    req._emit('upgradeneeded', {
                        target: req,
                        type: 'upgradeneeded',
                        oldVersion: oldVersion,
                        newVersion: version,
                    });
                }
                req.result = db;
                db._openConnections += 1;
                req._emit('success', { target: req, type: 'success' });
            });
            return req;
        },
        deleteDatabase: function (name) {
            const req = makeRequest();
            fireAsync(function () {
                const db = databases.get(name);
                if (db && db._openConnections > 0) {
                    // A caller that never closed its own handle blocks itself.
                    req._emit('blocked', { target: req, type: 'blocked' });
                    return;
                }
                databases.delete(name);
                req._emit('success', { target: req, type: 'success' });
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
    FakeIDBDatabase: FakeIDBDatabase,
    FakeIDBRequest: FakeIDBRequest,
    FakeIDBTransaction: FakeIDBTransaction,
    FakeIDBObjectStore: FakeIDBObjectStore,
    installFakeIdbGlobals: installFakeIdbGlobals,
    createFakeIndexedDBFactory: createFakeIndexedDBFactory,
};
