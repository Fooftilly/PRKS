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

    function approxJsonBytes(value) {
        try {
            const s = JSON.stringify(value);
            return s ? s.length : 0;
        } catch (_e) {
            return 0;
        }
    }

    function createPrksOfflineStore(deps) {
        const options = deps && typeof deps === 'object' ? deps : {};
        const idbFactory = Object.prototype.hasOwnProperty.call(options, 'indexedDB')
            ? options.indexedDB
            : defaultIndexedDB();
        const now = options.now || defaultNow;
        const dbName = options.dbName || DB_NAME;
        const dbVersion = options.dbVersion || DB_VERSION;

        let dbPromise = null;
        let unavailable = !idbFactory;

        function ensureStores(db) {
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

        function openDb() {
            if (unavailable) return Promise.resolve(null);
            if (dbPromise) return dbPromise;
            dbPromise = new Promise(function (resolve) {
                let req;
                try {
                    req = idbFactory.open(dbName, dbVersion);
                } catch (_e) {
                    unavailable = true;
                    resolve(null);
                    return;
                }
                if (!req) {
                    unavailable = true;
                    resolve(null);
                    return;
                }
                req.onupgradeneeded = function () {
                    try {
                        ensureStores(req.result);
                    } catch (_e) {
                        /* Corrupt/blocked upgrade: fail closed, do not touch canonical data. */
                    }
                };
                req.onsuccess = function () {
                    const db = req.result;
                    if (!db) {
                        unavailable = true;
                        resolve(null);
                        return;
                    }
                    db.onversionchange = function () {
                        try {
                            db.close();
                        } catch (_e) {
                            /* ignore */
                        }
                        dbPromise = null;
                    };
                    resolve(db);
                };
                req.onerror = function () {
                    unavailable = true;
                    resolve(null);
                };
                req.onblocked = function () {
                    unavailable = true;
                    resolve(null);
                };
            });
            return dbPromise;
        }

        /** Runs one IDB request inside its own transaction; never rejects. */
        function runRequest(storeName, mode, fn) {
            return openDb()
                .then(function (db) {
                    if (!db) return { ok: false, value: null };
                    return new Promise(function (resolve) {
                        let tx;
                        try {
                            tx = db.transaction([storeName], mode);
                        } catch (_e) {
                            resolve({ ok: false, value: null });
                            return;
                        }
                        let settled = false;
                        function finish(result) {
                            if (settled) return;
                            settled = true;
                            resolve(result);
                        }
                        tx.onerror = function () {
                            finish({ ok: false, value: null });
                        };
                        tx.onabort = function () {
                            finish({ ok: false, value: null });
                        };
                        let store;
                        let request;
                        try {
                            store = tx.objectStore(storeName);
                            request = fn(store);
                        } catch (_e) {
                            finish({ ok: false, value: null });
                            return;
                        }
                        if (!request) {
                            finish({ ok: false, value: null });
                            return;
                        }
                        request.onsuccess = function () {
                            finish({ ok: true, value: request.result });
                        };
                        request.onerror = function () {
                            try {
                                tx.abort();
                            } catch (_e) {
                                /* ignore */
                            }
                            finish({ ok: false, value: null });
                        };
                    });
                })
                .catch(function () {
                    return { ok: false, value: null };
                });
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
            deleteEntity: deleteEntity,
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
