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
 * One cache per signed-in scope, `prks-offline-v2:<scope key>`
 * (browser-scope.js). Without a scope the cache is unavailable.
 *
 * Schema (version 1):
 *   entities  -- keyPath ["kind", "id"]    envelope: { kind, id, value, cachedAt, sourceRevision, libraryEpoch }
 *   lists     -- keyPath "listKey"          envelope: { listKey, value, cachedAt, sourceRevision, libraryEpoch }
 *   metadata  -- keyPath "key"               small operational key/value (no private content)
 *
 * Library epoch (#629, docs/postgresql-architecture.md §10.4): every
 * envelope records the epoch of the page that cached it
 * (`prksBrowserScope.pageEpoch()`), and a read returns only envelopes of the
 * page's own epoch: what was cached before a restore describes the Library
 * that was replaced, and an old page never feeds a new one nor overwrites
 * what a newer page cached at the same key. The first use
 * under a newer epoch than metadata `library_epoch` also drops every older
 * envelope, since nothing can read them again.
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

    const META_LIBRARY_EPOCH = 'library_epoch';
    const EPOCH_PATTERN = /^[1-9][0-9]*$/;

    function epochOf(value) {
        return typeof value === 'string' && EPOCH_PATTERN.test(value) ? value : null;
    }

    /** Orders two canonical epochs (they may exceed a double's integers). */
    function compareEpochs(a, b) {
        if (a.length !== b.length) return a.length < b.length ? -1 : 1;
        return a < b ? -1 : a > b ? 1 : 0;
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
     *
     * `tx.done` is observed as soon as the transaction is created: a request
     * rejection (or a throw from `fn`) aborts the transaction and rejects
     * `done`, and that rejection must not escape as an unhandledrejection
     * while the public API still resolves fail-soft.
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
                const done = tx && tx.done;
                const hasDone = !!(done && typeof done.then === 'function');
                // Settle to a boolean so both success and abort are consumed.
                // Attached immediately — before fn/outcome — so a rejected
                // request cannot leave done unhandled while we fail-soft.
                const doneSettled = hasDone
                    ? done.then(
                        function () {
                            return true;
                        },
                        function () {
                            return false;
                        }
                    )
                    : null;

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
                    if (doneSettled) {
                        return doneSettled.then(function () {
                            return { ok: false, value: null };
                        });
                    }
                    return { ok: false, value: null };
                }
                const waitsForCommit = mode === 'readwrite';
                return Promise.resolve(outcome)
                    .then(function (value) {
                        if (!waitsForCommit) {
                            // doneSettled already converts abort to a resolved
                            // false — no unhandledrejection either way. Do not
                            // delay successful reads on commit.
                            return { ok: true, value: value };
                        }
                        if (doneSettled) {
                            return doneSettled.then(function (committed) {
                                return committed
                                    ? { ok: true, value: value }
                                    : { ok: false, value: null };
                            });
                        }
                        // Fail closed if the wrapper did not attach done.
                        return { ok: false, value: null };
                    })
                    .catch(function () {
                        if (doneSettled) {
                            return doneSettled.then(function () {
                                return { ok: false, value: null };
                            });
                        }
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
     * Reads the cache's `library_epoch` in the same transaction first: on a
     * page older than the cache (`epoch` below it) nothing of the page's own
     * Library is left, so the sweep is skipped (true) and the newer page's
     * entries stay.
     */
    function deleteEntitiesByKindOnRawDb(raw, wanted, range, epoch) {
        return new Promise(function (resolve) {
            let tx;
            try {
                tx = raw.transaction([STORE_ENTITIES, STORE_METADATA], 'readwrite');
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
            function sweep() {
                if (range && typeof store.openCursor === 'function') {
                    if (sweepKindViaCursor(store, range, finish, markSwept)) return;
                }
                sweepKindViaGetAll(store, wanted, finish, markSwept);
            }
            if (!epoch) {
                sweep();
                return;
            }
            let metaReq;
            try {
                metaReq = tx.objectStore(STORE_METADATA).get(META_LIBRARY_EPOCH);
            } catch (_e) {
                finish(false);
                return;
            }
            metaReq.onsuccess = function () {
                const row = metaReq.result;
                const seen = row ? epochOf(row.value) : null;
                if (seen && compareEpochs(seen, epoch) > 0) {
                    markSwept();
                    return;
                }
                sweep();
            };
            metaReq.onerror = function () {
                finish(false);
            };
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
        // A name the caller gives (tests), or the signed-in scope's cache
        // (`prks-offline-v2:<scope key>`, browser-scope.js). Without a scope
        // the cache is unavailable, never an unscoped database.
        const scope = options.dbName ? null : (options.scope || root.prksBrowserScope || null);
        // The Library epoch this page caches under: the scope's page epoch,
        // or the caller's (tests). A scope that names none has no cache.
        const ownEpoch = options.dbName ? (epochOf(options.libraryEpoch) || null) : null;
        function pageEpoch() {
            if (!scope) return ownEpoch;
            try {
                return typeof scope.pageEpoch === 'function' ? epochOf(scope.pageEpoch()) : null;
            } catch (_e) {
                return null;
            }
        }
        /** Cached under this page's epoch: anything else is not this Library. */
        function current(envelope) {
            return !!envelope && (envelope.libraryEpoch == null ? null : envelope.libraryEpoch) === pageEpoch();
        }
        let fenced = null;
        const dbVersion = options.dbVersion || DB_VERSION;
        const keyRangeApi = defaultKeyRangeApi(options);

        let dbPromise = null;
        let openDbHandle = null;
        let unavailable = !idbFactory || !wrap;
        // Bumped when the scope retires: an open still in flight then closes
        // its connection on arrival instead of handing it to its caller.
        let openGeneration = 0;

        function closeHandle() {
            openGeneration += 1;
            if (openDbHandle) {
                try {
                    openDbHandle.close();
                } catch (_e) {
                    /* ignore */
                }
                openDbHandle = null;
            }
            dbPromise = null;
        }

        // Another account signed in: this page's cache closes for good.
        if (scope && typeof scope.subscribe === 'function') {
            scope.subscribe(function (event) { if (event && event.type === 'retired') closeHandle(); });
        }

        function resolveDbName() {
            if (options.dbName) return Promise.resolve(options.dbName);
            if (!scope || typeof scope.dbName !== 'function') return Promise.resolve(null);
            return scope.dbName('offline').then(function (name) {
                return name && pageEpoch() ? name : null;
            }).catch(function () { return null; });
        }

        function rawDb(db) {
            return unwrapRawDb(db, unwrap);
        }

        function openDb() {
            if (unavailable) return Promise.resolve(null);
            if (dbPromise) return dbPromise;
            const generation = openGeneration;
            dbPromise = resolveDbName().then(function (dbName) { return new Promise(function (resolve) {
                if (!dbName || generation !== openGeneration) {
                    dbPromise = null;
                    resolve(null);
                    return;
                }
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
                        if (generation !== openGeneration) {
                            // The scope retired while this open was in flight.
                            try {
                                if (raw) raw.close();
                            } catch (_e) {
                                /* ignore */
                            }
                            finish(null);
                            return;
                        }
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
            }); });
            return dbPromise;
        }

        function runRequest(storeName, mode, fn) {
            return fence().then(function () {
                return runIdbStoreRequest(openDb, storeName, mode, fn);
            });
        }

        /**
         * Once per store: under a newer epoch than the cache last saw, every
         * envelope is from the Library a restore replaced and is dropped.
         * Reads never depend on it (`current`); a page older than the cache
         * leaves it to the newer page's.
         */
        function fence() {
            if (fenced) return fenced;
            // Opening the cache waits for the scope to resolve, so the page
            // epoch read after it is the bound one, never a not-yet-known null.
            const run = openDb().then(function (db) {
                const epoch = pageEpoch();
                if (!db || !epoch) {
                    if (fenced === run) fenced = null;
                    return;
                }
                // One transaction over the metadata and both caches: two
                // stores fencing at once cannot both read the older epoch,
                // so neither clears what the other cached after its fence.
                let tx;
                try {
                    tx = db.transaction([STORE_METADATA, STORE_ENTITIES, STORE_LISTS], 'readwrite');
                } catch (_e) {
                    if (fenced === run) fenced = null;
                    return;
                }
                const done = tx.done.then(function () { return true; }, function () { return false; });
                return tx.objectStore(STORE_METADATA).get(META_LIBRARY_EPOCH).then(function (row) {
                    const seen = row ? epochOf(row.value) : null;
                    if (seen && compareEpochs(seen, epoch) >= 0) return undefined;
                    return Promise.all([
                        tx.objectStore(STORE_ENTITIES).clear(),
                        tx.objectStore(STORE_LISTS).clear(),
                        tx.objectStore(STORE_METADATA).put({ key: META_LIBRARY_EPOCH, value: epoch }),
                    ]);
                }).then(function () {
                    return done;
                }, function () {
                    try {
                        tx.abort();
                    } catch (_e) {
                        /* already finished */
                    }
                    return done.then(function () { return false; });
                }).then(function (ok) {
                    if (!ok && fenced === run) fenced = null;
                });
            }).then(function () {}, function () {
                if (fenced === run) fenced = null;
            });
            fenced = run;
            return run;
        }

        /**
         * Runs `op(store)` on `storeName` in one readwrite transaction with the
         * cache's `library_epoch`. A page older than the cache (a tab loaded
         * before a restore) never touches what a newer page cached: its op is
         * skipped and the call resolves to `whenNewer`. Otherwise it resolves
         * true only when the transaction committed.
         */
        function inCurrentEpoch(storeName, op, whenNewer) {
            return fence().then(openDb).then(function (db) {
                if (!db) return false;
                // A scope without an epoch opens no database (resolveDbName);
                // only a caller-named one (tests) may cache without an epoch.
                const epoch = pageEpoch();
                let tx;
                try {
                    tx = db.transaction([storeName, STORE_METADATA], 'readwrite');
                } catch (_e) {
                    return false;
                }
                const done = tx.done.then(function () { return true; }, function () { return false; });
                let newer = false;
                return tx.objectStore(STORE_METADATA).get(META_LIBRARY_EPOCH).then(function (row) {
                    const seen = row ? epochOf(row.value) : null;
                    if (epoch && seen && compareEpochs(seen, epoch) > 0) {
                        newer = true;
                        return undefined;
                    }
                    return op(tx.objectStore(storeName), epoch);
                }).then(function () {
                    return done.then(function (ok) { return ok && (newer ? whenNewer : true); });
                }, function () {
                    try {
                        tx.abort();
                    } catch (_e) {
                        /* already finished */
                    }
                    return done.then(function () { return false; });
                });
            }).catch(function () {
                return false;
            });
        }

        /** Writes one envelope stamped with this page's epoch; refused (false) on a page older than the cache. */
        function putCurrent(storeName, envelope) {
            return inCurrentEpoch(storeName, function (store, epoch) {
                envelope.libraryEpoch = epoch;
                return store.put(envelope);
            }, false);
        }

        /**
         * Deletes one key. On a page older than the cache nothing of its own
         * Library is left there (the newer page's fence cleared it), so the
         * delete is skipped and reports true: what the newer page cached stays.
         */
        function deleteCurrent(storeName, key) {
            return inCurrentEpoch(storeName, function (store) {
                return store.delete(key);
            }, true);
        }

        function putEntity(kind, id, value, sourceRevision) {
            const envelope = {
                kind: String(kind),
                id: String(id),
                value: value,
                cachedAt: now(),
                sourceRevision: sourceRevision != null ? String(sourceRevision) : '',
                libraryEpoch: null,
            };
            return putCurrent(STORE_ENTITIES, envelope);
        }

        function getEntity(kind, id) {
            return runRequest(STORE_ENTITIES, 'readonly', function (store) {
                return store.get([String(kind), String(id)]);
            }).then(function (r) {
                return r.ok && current(r.value) ? r.value : null;
            });
        }

        function deleteEntity(kind, id) {
            return deleteCurrent(STORE_ENTITIES, [String(kind), String(id)]);
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
                return r.value.filter(function (row) {
                    return current(row) && (range || String(row.kind) === String(kind));
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
            return fence().then(openDb)
                .then(function (db) {
                    // Cursor multi-request sweeps stay on the raw IDB handle.
                    const raw = rawDb(db);
                    if (!raw) return false;
                    return deleteEntitiesByKindOnRawDb(raw, wanted, kindKeyRange(wanted), pageEpoch());
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
                libraryEpoch: null,
            };
            return putCurrent(STORE_LISTS, envelope);
        }

        function getList(listKey) {
            return runRequest(STORE_LISTS, 'readonly', function (store) {
                return store.get(String(listKey));
            }).then(function (r) {
                return r.ok && current(r.value) ? r.value : null;
            });
        }

        function deleteList(listKey) {
            return deleteCurrent(STORE_LISTS, String(listKey));
        }

        /**
         * Empties the cache in one transaction, keeping the `library_epoch`
         * row: the epoch the cache was fenced at still holds, so a store that
         * already fenced need not fence again, and the next one does not take
         * the missing row for a restore and drop what was cached since.
         */
        function clearAll() {
            return fence().then(openDb).then(function (db) {
                if (!db) return false;
                let tx;
                try {
                    tx = db.transaction([STORE_METADATA, STORE_ENTITIES, STORE_LISTS], 'readwrite');
                } catch (_e) {
                    return false;
                }
                const done = tx.done.then(function () { return true; }, function () { return false; });
                const meta = tx.objectStore(STORE_METADATA);
                return meta.get(META_LIBRARY_EPOCH).then(function (row) {
                    return Promise.all([
                        tx.objectStore(STORE_ENTITIES).clear(),
                        tx.objectStore(STORE_LISTS).clear(),
                        meta.clear(),
                    ]).then(function () {
                        return row ? meta.put(row) : undefined;
                    });
                }).then(function () {
                    return done;
                }, function () {
                    try {
                        tx.abort();
                    } catch (_e) {
                        /* already finished */
                    }
                    return done.then(function () { return false; });
                });
            }).catch(function () {
                return false;
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
            return resolveDbName().then(function (dbName) { return new Promise(function (resolve) {
                if (!dbName || !idbFactory) {
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
            }); });
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
        PRKS_OFFLINE_DB_VERSION: DB_VERSION,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof self !== 'undefined' ? self : typeof window !== 'undefined' ? window : globalThis);
