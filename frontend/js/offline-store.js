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
        const keyRangeApi = Object.prototype.hasOwnProperty.call(options, 'idbKeyRange')
            ? options.idbKeyRange
            : typeof IDBKeyRange !== 'undefined'
              ? IDBKeyRange
              : root && root.IDBKeyRange
                ? root.IDBKeyRange
                : null;

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

        /**
         * Runs one IDB request inside its own transaction; never rejects.
         *
         * A request's `onsuccess` means the request ran, NOT that the database
         * modification committed -- a transaction can still abort afterwards.
         * Callers that act on a reported write (offline coherence unblocks a
         * domain only once its cleanup physically completed) need the stronger
         * boundary, so a `readwrite` transaction resolves from `oncomplete`
         * and reports false on `onerror`/`onabort`. Reads have no commit to
         * wait for and resolve as soon as the request produces its result.
         */
        function runRequest(storeName, mode, fn) {
            return openDb()
                .then(function (db) {
                    if (!db) return { ok: false, value: null };
                    return new Promise(function (resolve) {
                        const waitsForCommit = mode === 'readwrite';
                        let tx;
                        try {
                            tx = db.transaction([storeName], mode);
                        } catch (_e) {
                            resolve({ ok: false, value: null });
                            return;
                        }
                        let settled = false;
                        let pendingResult = null;
                        function finish(result) {
                            if (settled) return;
                            settled = true;
                            resolve(result);
                        }
                        tx.oncomplete = function () {
                            finish(pendingResult || { ok: false, value: null });
                        };
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
                            const result = { ok: true, value: request.result };
                            if (waitsForCommit) {
                                pendingResult = result;
                                return;
                            }
                            finish(result);
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

        /** Bounded compound-key range covering every ["kind", *] row, so a
         * whole-kind sweep needs no schema/index change. IndexedDB array-key
         * ordering puts ["kind"] before every ["kind", <string id>], and
         * ["kind", []] after every one of them (arrays sort after strings). */
        function kindKeyRange(kind) {
            if (!keyRangeApi || typeof keyRangeApi.bound !== 'function') return null;
            try {
                return keyRangeApi.bound([kind], [kind, []], false, false);
            } catch (_e) {
                return null;
            }
        }

        /**
         * Removes every cached entity of one kind. Used by domain-level offline
         * coherence (see offline-runtime.js): some cached read models span many
         * canonical records, so one canonical change can stale a whole kind
         * rather than a single row. Follows the store contract -- always
         * resolves, never throws to the caller -- and resolves true only when
         * the physical cleanup actually completed, so a caller may keep a
         * domain conservatively blocked when it did not.
         */
        function deleteEntitiesByKind(kind) {
            const wanted = String(kind);
            return openDb()
                .then(function (db) {
                    if (!db) return false;
                    return new Promise(function (resolve) {
                        let tx;
                        try {
                            tx = db.transaction([STORE_ENTITIES], 'readwrite');
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
                        // The sweep is only "done" once the transaction
                        // COMMITS: individual delete requests succeeding does
                        // not guarantee the rows are gone, and a caller that
                        // unblocks a coherence domain on a sweep that later
                        // aborted would republish known-stale rows.
                        let swept = false;
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
                        const range = kindKeyRange(wanted);
                        let cursorReq = null;
                        if (range && typeof store.openCursor === 'function') {
                            try {
                                cursorReq = store.openCursor(range);
                            } catch (_e) {
                                cursorReq = null;
                            }
                        }
                        if (cursorReq) {
                            cursorReq.onerror = function () {
                                finish(false);
                            };
                            cursorReq.onsuccess = function () {
                                const cursor = cursorReq.result;
                                if (!cursor) {
                                    swept = true;
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
                            return;
                        }
                        /* Engines without a usable key range/cursor: read the
                         * rows once, then delete the matching keys in order. */
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
                                    swept = true;
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
                    });
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
