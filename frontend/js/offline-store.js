/**
 * PRKS offline store -- disposable client-side cache, IndexedDB only.
 *
 * Scope (see AGENTS.md "Offline / PWA" invariants):
 *   - Persistence only. No DOM, no routing, no connectivity policy.
 *   - Never canonical. SQLite + managed files on the PRKS server remain the
 *     source of truth. Deleting this browser storage must never affect
 *     PRKS data.
 *   - Must never block the app: every public method resolves (never
 *     rejects) and degrades to a safe "unavailable" result if IndexedDB is
 *     missing, blocked, corrupt, or over quota.
 *
 * Schema ("prks-offline-v1", version 1):
 *   entities  -- keyPath ["kind", "id"]    envelope: { kind, id, value, cachedAt, sourceRevision }
 *   lists     -- keyPath "listKey"          envelope: { listKey, value, cachedAt, sourceRevision }
 *   metadata  -- keyPath "key"               small operational key/value (no private content)
 *
 * IndexedDB Promise plumbing (#181): simple single-store get/put/delete/clear
 * /count/getAll paths use the thin `idb` wrapper (`wrap` + `tx.done`) so
 * request vs commit semantics stay explicit without hand-rolled onsuccess
 * listeners. Kind sweeps (`deleteEntitiesByKind`) and database delete keep
 * raw IDB handles -- cursor multi-request transactions and close-before-delete
 * are PRKS-owned, not generic Promise sugar.
 */
(function (root) {
    'use strict';

    const DB_NAME = 'prks-offline-v1';
    const DB_VERSION = 1;
    const STORE_ENTITIES = 'entities';
    const STORE_LISTS = 'lists';
    const STORE_METADATA = 'metadata';

    function defaultIndexedDB() {
        if (typeof indexedDB !== 'undefined') return indexedDB;
        if (root && root.indexedDB) return root.indexedDB;
        return null;
    }

    function defaultNow() {
        return Date.now();
    }

    function defaultIdbApi() {
        if (root && root.idb && typeof root.idb.wrap === 'function') return root.idb;
        // Node selftests load the vendored UMD without a script tag.
        if (typeof module !== 'undefined' && module.exports && typeof require === 'function') {
            try {
                const loaded = require('../vendor/idb/idb.min.js');
                if (loaded && typeof loaded.wrap === 'function') return loaded;
            } catch (_e) {
                /* browser / missing vendor */
            }
        }
        return null;
    }

    function defaultKeyRangeApi(options) {
        if (Object.prototype.hasOwnProperty.call(options, 'idbKeyRange')) {
            return options.idbKeyRange;
        }
        if (typeof IDBKeyRange !== 'undefined') return IDBKeyRange;
        return root && root.IDBKeyRange ? root.IDBKeyRange : null;
    }

    function approxJsonBytes(value) {
        try {
            const s = JSON.stringify(value);
            return s ? s.length : 0;
        } catch (_e) {
            return 0;
        }
    }

    function ensureOfflineStores(db) {
        if (!db.objectStoreNames.contains(STORE_ENTITIES)) {
            db.createObjectStore(STORE_ENTITIES, { keyPath: ['kind', 'id'] });
        }
        if (!db.objectStoreNames.contains(STORE_LISTS)) {
            db.createObjectStore(STORE_LISTS, { keyPath: 'listKey' });
        }
        if (!db.objectStoreNames.contains(STORE_METADATA)) {
            db.createObjectStore(STORE_METADATA, { keyPath: 'key' });
        }
    }

    function unwrapRawDb(db, unwrap) {
        if (!db) return null;
        if (unwrap) {
            try {
                const unwrapped = unwrap(db);
                if (unwrapped) return unwrapped;
            } catch (_e) {
                /* fall through */
            }
        }
        return db;
    }

    /** Bounded compound-key range covering every ["kind", *] row. */
    function kindKeyRangeFor(keyRangeApi, kind) {
        if (!keyRangeApi || typeof keyRangeApi.bound !== 'function') return null;
        try {
            return keyRangeApi.bound([kind], [kind, []], false, false);
        } catch (_e) {
            return null;
        }
    }

    /**
     * Runs one IDB request inside its own transaction; never rejects.
     *
     * Uses `idb.wrap` so object-store methods return Promises, and waits on
     * `tx.done` for readwrite so a request-level success that later aborts is
     * reported as failure. Reads resolve when the request Promise settles.
     */
    function runIdbStoreRequest(openDb, storeName, mode, fn) {
        return openDb()
            .then(function (db) {
                if (!db) return { ok: false, value: null };
                let tx;
                try {
                    tx = db.transaction([storeName], mode);
                } catch (_e) {
                    return { ok: false, value: null };
                }
                let store;
                let outcome;
                try {
                    store = tx.objectStore(storeName);
                    outcome = fn(store);
                } catch (_e) {
                    try {
                        tx.abort();
                    } catch (_abortErr) {
                        /* ignore */
                    }
                    return { ok: false, value: null };
                }
                const waitsForCommit = mode === 'readwrite';
                return Promise.resolve(outcome)
                    .then(function (value) {
                        if (!waitsForCommit) {
                            return { ok: true, value: value };
                        }
                        const done = tx && tx.done;
                        if (done && typeof done.then === 'function') {
                            return done.then(function () {
                                return { ok: true, value: value };
                            });
                        }
                        // Fail closed if the wrapper did not attach done.
                        return { ok: false, value: null };
                    })
                    .catch(function () {
                        return { ok: false, value: null };
                    });
            })
            .catch(function () {
                return { ok: false, value: null };
            });
    }

    function sweepKindViaCursor(store, range, finish, markSwept) {
        let cursorReq;
        try {
            cursorReq = store.openCursor(range);
        } catch (_e) {
            return false;
        }
        cursorReq.onerror = function () {
            finish(false);
        };
        cursorReq.onsuccess = function () {
            const cursor = cursorReq.result;
            if (!cursor) {
                markSwept();
                return;
            }
            let del;
            try {
                del = cursor.delete();
            } catch (_e) {
                finish(false);
                return;
            }
            del.onerror = function () {
                finish(false);
            };
            del.onsuccess = function () {
                try {
                    cursor.continue();
                } catch (_e) {
                    finish(false);
                }
            };
        };
        return true;
    }

    function sweepKindViaGetAll(store, wanted, finish, markSwept) {
        let allReq;
        try {
            allReq = store.getAll ? store.getAll() : null;
        } catch (_e) {
            allReq = null;
        }
        if (!allReq) {
            finish(false);
            return;
        }
        allReq.onerror = function () {
            finish(false);
        };
        allReq.onsuccess = function () {
            const rows = Array.isArray(allReq.result) ? allReq.result : [];
            const keys = rows
                .filter(function (row) {
                    return row && String(row.kind) === wanted;
                })
                .map(function (row) {
                    return [String(row.kind), String(row.id)];
                });
            let i = 0;
            function step() {
                if (i >= keys.length) {
                    markSwept();
                    return;
                }
                let req;
                try {
                    req = store.delete(keys[i]);
                } catch (_e) {
                    finish(false);
                    return;
                }
                i += 1;
                req.onerror = function () {
                    finish(false);
                };
                req.onsuccess = step;
            }
            step();
        };
    }

    /**
     * Cursor multi-request kind sweep on a raw IDB database handle.
     * Resolves true only after the transaction commits with a completed sweep.
     */
    function deleteEntitiesByKindOnRawDb(raw, wanted, range) {
        return new Promise(function (resolve) {
            let tx;
            try {
                tx = raw.transaction([STORE_ENTITIES], 'readwrite');
            } catch (_e) {
                resolve(false);
                return;
            }
            let settled = false;
            function finish(ok) {
                if (settled) return;
                settled = true;
                resolve(ok);
            }
            // The sweep is only "done" once the transaction COMMITS.
            let swept = false;
            function markSwept() {
                swept = true;
            }
            tx.oncomplete = function () {
                finish(swept);
            };
            tx.onerror = function () {
                finish(false);
            };
            tx.onabort = function () {
                finish(false);
            };
            let store;
            try {
                store = tx.objectStore(STORE_ENTITIES);
            } catch (_e) {
                finish(false);
                return;
            }
            if (range && typeof store.openCursor === 'function') {
                if (sweepKindViaCursor(store, range, finish, markSwept)) return;
            }
            sweepKindViaGetAll(store, wanted, finish, markSwept);
        });
    }

    function bindOpenRequestLifecycle(req, onUpgrade, onBlocked) {
        if (typeof req.addEventListener === 'function') {
            req.addEventListener('upgradeneeded', onUpgrade);
            req.addEventListener('blocked', onBlocked);
            return;
        }
        req.onupgradeneeded = onUpgrade;
        req.onblocked = onBlocked;
    }

    function attachVersionChange(raw, onVersionChange) {
        if (!raw) return;
        if (typeof raw.addEventListener === 'function') {
            raw.addEventListener('versionchange', onVersionChange);
        }
        raw.onversionchange = onVersionChange;
    }

    function createPrksOfflineStore(deps) {
        const options = deps && typeof deps === 'object' ? deps : {};
        const idbFactory = Object.prototype.hasOwnProperty.call(options, 'indexedDB')
            ? options.indexedDB
            : defaultIndexedDB();
        const idbApi = Object.prototype.hasOwnProperty.call(options, 'idb')
            ? options.idb
            : defaultIdbApi();
        const wrap = idbApi && typeof idbApi.wrap === 'function' ? idbApi.wrap : null;
        const unwrap = idbApi && typeof idbApi.unwrap === 'function' ? idbApi.unwrap : null;
        const now = options.now || defaultNow;
        const dbName = options.dbName || DB_NAME;
        const dbVersion = options.dbVersion || DB_VERSION;
        const keyRangeApi = defaultKeyRangeApi(options);

        let dbPromise = null;
        let openDbHandle = null;
        let unavailable = !idbFactory || !wrap;

        function rawDb(db) {
            return unwrapRawDb(db, unwrap);
        }

        function openDb() {
            if (unavailable) return Promise.resolve(null);
            if (dbPromise) return dbPromise;
            dbPromise = new Promise(function (resolve) {
                let req;
                let settled = false;
                function finish(db) {
                    if (settled) return;
                    settled = true;
                    resolve(db);
                }
                function markUnavailable() {
                    unavailable = true;
                    finish(null);
                }
                try {
                    req = idbFactory.open(dbName, dbVersion);
                } catch (_e) {
                    markUnavailable();
                    return;
                }
                if (!req) {
                    markUnavailable();
                    return;
                }
                function onUpgrade() {
                    try {
                        ensureOfflineStores(req.result);
                    } catch (_e) {
                        /* Corrupt/blocked upgrade: fail closed. */
                    }
                }
                bindOpenRequestLifecycle(req, onUpgrade, markUnavailable);
                wrap(req)
                    .then(function (db) {
                        if (!db) {
                            markUnavailable();
                            return;
                        }
                        const raw = rawDb(db);
                        openDbHandle = raw;
                        attachVersionChange(raw, function onVersionChange() {
                            try {
                                if (raw) raw.close();
                            } catch (_e) {
                                /* ignore */
                            }
                            if (openDbHandle === raw) openDbHandle = null;
                            dbPromise = null;
                        });
                        finish(db);
                    })
                    .catch(markUnavailable);
            });
            return dbPromise;
        }

        function runRequest(storeName, mode, fn) {
            return runIdbStoreRequest(openDb, storeName, mode, fn);
        }

        function putEntity(kind, id, value, sourceRevision) {
            const envelope = {
                kind: String(kind),
                id: String(id),
                value: value,
                cachedAt: now(),
                sourceRevision: sourceRevision != null ? String(sourceRevision) : '',
            };
            return runRequest(STORE_ENTITIES, 'readwrite', function (store) {
                return store.put(envelope);
            }).then(function (r) {
                return r.ok;
            });
        }

        function getEntity(kind, id) {
            return runRequest(STORE_ENTITIES, 'readonly', function (store) {
                return store.get([String(kind), String(id)]);
            }).then(function (r) {
                return r.ok && r.value ? r.value : null;
            });
        }

        function deleteEntity(kind, id) {
            return runRequest(STORE_ENTITIES, 'readwrite', function (store) {
                return store.delete([String(kind), String(id)]);
            }).then(function (r) {
                return r.ok;
            });
        }

        function kindKeyRange(kind) {
            return kindKeyRangeFor(keyRangeApi, kind);
        }

        /**
         * Every cached entity of one kind, as stored envelopes.
         * Fail-soft: an unreadable cache yields null (failed) vs [] (empty).
         */
        function getEntitiesByKind(kind) {
            const range = kindKeyRange(String(kind));
            return runRequest(STORE_ENTITIES, 'readonly', function (store) {
                return range ? store.getAll(range) : store.getAll();
            }).then(function (r) {
                if (!r.ok || !Array.isArray(r.value)) return null;
                return range ? r.value : r.value.filter(function (row) {
                    return row && String(row.kind) === String(kind);
                });
            }).catch(function () {
                return null;
            });
        }

        /**
         * Removes every cached entity of one kind. Resolves true only when the
         * physical cleanup committed (see offline-runtime coherence).
         */
        function deleteEntitiesByKind(kind) {
            const wanted = String(kind);
            return openDb()
                .then(function (db) {
                    // Cursor multi-request sweeps stay on the raw IDB handle.
                    const raw = rawDb(db);
                    if (!raw) return false;
                    return deleteEntitiesByKindOnRawDb(raw, wanted, kindKeyRange(wanted));
                })
                .catch(function () {
                    return false;
                });
        }

        function putList(listKey, value, sourceRevision) {
            const envelope = {
                listKey: String(listKey),
                value: value,
                cachedAt: now(),
                sourceRevision: sourceRevision != null ? String(sourceRevision) : '',
            };
            return runRequest(STORE_LISTS, 'readwrite', function (store) {
                return store.put(envelope);
            }).then(function (r) {
                return r.ok;
            });
        }

        function getList(listKey) {
            return runRequest(STORE_LISTS, 'readonly', function (store) {
                return store.get(String(listKey));
            }).then(function (r) {
                return r.ok && r.value ? r.value : null;
            });
        }

        function deleteList(listKey) {
            return runRequest(STORE_LISTS, 'readwrite', function (store) {
                return store.delete(String(listKey));
            }).then(function (r) {
                return r.ok;
            });
        }

        function clearAll() {
            return Promise.all([
                runRequest(STORE_ENTITIES, 'readwrite', function (store) {
                    return store.clear();
                }),
                runRequest(STORE_LISTS, 'readwrite', function (store) {
                    return store.clear();
                }),
                runRequest(STORE_METADATA, 'readwrite', function (store) {
                    return store.clear();
                }),
            ]).then(function (results) {
                return results.every(function (r) {
                    return r.ok;
                });
            });
        }

        function isAvailable() {
            return openDb().then(function (db) {
                return !!db;
            });
        }

        /** Discards only the disposable offline cache. Canonical PRKS data is never touched. */
        function deleteDatabase() {
            if (openDbHandle) {
                try {
                    openDbHandle.close();
                } catch (_e) {
                    /* a close failure must not stop the delete attempt */
                }
                openDbHandle = null;
            }
            dbPromise = null;
            return new Promise(function (resolve) {
                if (!idbFactory) {
                    resolve(true);
                    return;
                }
                let req;
                try {
                    req = idbFactory.deleteDatabase(dbName);
                } catch (_e) {
                    resolve(false);
                    return;
                }
                req.onsuccess = function () {
                    resolve(true);
                };
                req.onerror = function () {
                    resolve(false);
                };
                req.onblocked = function () {
                    resolve(false);
                };
            });
        }

        function countAll(storeName) {
            return runRequest(storeName, 'readonly', function (store) {
                return store.count();
            }).then(function (r) {
                return r.ok && typeof r.value === 'number' ? r.value : 0;
            });
        }

        function approxBytes(storeName) {
            return runRequest(storeName, 'readonly', function (store) {
                return store.getAll ? store.getAll() : null;
            }).then(function (r) {
                if (!r.ok || !Array.isArray(r.value)) return 0;
                let total = 0;
                r.value.forEach(function (row) {
                    total += approxJsonBytes(row);
                });
                return total;
            });
        }

        /** Aggregate, privacy-safe: counts and an approximate byte size only. */
        function stats() {
            return Promise.all([
                countAll(STORE_ENTITIES),
                countAll(STORE_LISTS),
                approxBytes(STORE_ENTITIES),
                approxBytes(STORE_LISTS),
                isAvailable(),
            ]).then(function (results) {
                return {
                    available: results[4],
                    entityCount: results[0],
                    listCount: results[1],
                    approxBytes: results[2] + results[3],
                };
            });
        }

        return {
            isAvailable: isAvailable,
            putEntity: putEntity,
            getEntity: getEntity,
            getEntitiesByKind: getEntitiesByKind,
            deleteEntity: deleteEntity,
            deleteEntitiesByKind: deleteEntitiesByKind,
            putList: putList,
            getList: getList,
            deleteList: deleteList,
            clearAll: clearAll,
            deleteDatabase: deleteDatabase,
            stats: stats,
        };
    }

    const api = {
        createPrksOfflineStore: createPrksOfflineStore,
        PRKS_OFFLINE_DB_NAME: DB_NAME,
        PRKS_OFFLINE_DB_VERSION: DB_VERSION,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof self !== 'undefined' ? self : typeof window !== 'undefined' ? window : globalThis);
