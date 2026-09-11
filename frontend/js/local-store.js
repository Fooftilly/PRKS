/**
 * PRKS local store -- DURABLE user-owned local state, IndexedDB only.
 *
 * This is NOT the offline cache. `offline-store.js` holds downloaded server
 * data in `prks-offline-v1`, which is deliberately disposable: it may be
 * discarded when corrupt, and "Clear offline cache" empties it. Unsynchronized
 * user work cannot share that lifecycle, so it lives in a **separate database**
 * (`prks-local-v1`). The separation is physical, not a convention: even a bug
 * in `offline-store.clearAll()` cannot reach another database.
 *
 * Scope:
 *   - Persistence only. No DOM, no routing, no connectivity policy, no HTTP,
 *     no server-specific mutation logic. Mirrors offline-store.js's discipline.
 *   - Stores semantic operation ENVELOPES describing domain intent, never
 *     serialized HTTP requests. Synchronization is not an HTTP replay queue.
 *
 * Failure contract -- deliberately the OPPOSITE of offline-store.js:
 *   offline-store degrades silently because a missing cache is survivable.
 *   Here a write that did not commit means the user's change does not exist,
 *   so every write either resolves with a committed result or REJECTS. Nothing
 *   may report "Saved locally" on a write this module did not commit.
 *
 * Commit semantics: a write resolves from the transaction's `oncomplete`, never
 * a request's `onsuccess` -- a request can succeed and its transaction still
 * abort, rolling the row back.
 *
 * Schema ("prks-local-v1", version 1):
 *   operations -- keyPath "op_id"  immutable semantic envelope + mutable sync state
 *   metadata   -- keyPath "key"    durable local identity/bookkeeping (device_id, sequence)
 *
 * Milestone 2A: persistence foundation only. No mutation UI consumes this yet;
 * PRKS remains read-only while the server is unreachable.
 */
(function (root) {
    'use strict';

    const DB_NAME = 'prks-local-v1';
    const DB_VERSION = 1;
    const STORE_OPERATIONS = 'operations';
    const STORE_METADATA = 'metadata';

    const META_DEVICE_ID = 'device_id';
    const META_SEQUENCE = 'op_sequence';

    /** Operation lifecycle. Kept minimal: a retryable failure is `pending`
     *  plus `last_error`/`attempt_count`, not a separate persisted status. */
    const STATUS_PENDING = 'pending';
    const STATUS_SYNCING = 'syncing';
    const STATUS_ACKNOWLEDGED = 'acknowledged';
    const STATUS_CONFLICT = 'conflict';
    const STATUS_FAILED = 'failed';
    const STATUSES = Object.freeze([
        STATUS_PENDING, STATUS_SYNCING, STATUS_ACKNOWLEDGED, STATUS_CONFLICT, STATUS_FAILED,
    ]);

    /* Prototype allow-list. An operation type reaches durable storage only if
     * it is registered here, so a typo or a half-built feature cannot persist
     * an envelope no coordinator knows how to send. Milestone 2A registers the
     * two operations whose design is settled; the rest arrive with 2B. */
    const OPERATION_TYPES = Object.freeze([
        'MARK_WORK_OPENED',
        'ADD_WORK_TAG',
        'REMOVE_WORK_TAG',
    ]);

    /* Bounds the ledger long before text/CRDT operations exist. A payload this
     * large is a bug, not a legitimate semantic operation. */
    const MAX_PAYLOAD_BYTES = 64 * 1024;
    const MAX_ERROR_CHARS = 500;

    function defaultIndexedDB() {
        if (typeof indexedDB !== 'undefined') return indexedDB;
        if (root && root.indexedDB) return root.indexedDB;
        return null;
    }

    function defaultNow() {
        return Date.now();
    }

    /**
     * High-entropy operation id. Unlike PRKS entity ids (a prefix plus 32 bits)
     * these are generated independently on every device with no coordination,
     * so a full 122-bit UUID is the right size -- see docs/local-first-sync.md.
     */
    function defaultUuid() {
        const c = (typeof crypto !== 'undefined' && crypto) || (root && root.crypto) || null;
        if (c && typeof c.randomUUID === 'function') return c.randomUUID();
        if (c && typeof c.getRandomValues === 'function') {
            const bytes = new Uint8Array(16);
            c.getRandomValues(bytes);
            bytes[6] = (bytes[6] & 0x0f) | 0x40;
            bytes[8] = (bytes[8] & 0x3f) | 0x80;
            const hex = [];
            for (let i = 0; i < bytes.length; i++) hex.push((bytes[i] + 0x100).toString(16).slice(1));
            return (
                hex.slice(0, 4).join('') + '-' + hex.slice(4, 6).join('') + '-' +
                hex.slice(6, 8).join('') + '-' + hex.slice(8, 10).join('') + '-' +
                hex.slice(10, 16).join('')
            );
        }
        // No CSPRNG: refuse rather than mint a weak id that must stay unique
        // across devices forever.
        throw new Error('PRKS local store requires a cryptographic RNG for operation ids.');
    }

    const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

    function isNonBlankString(v) {
        return typeof v === 'string' && v.trim().length > 0;
    }

    function isPlainObject(v) {
        return !!v && typeof v === 'object' && !Array.isArray(v);
    }

    function jsonByteLength(value) {
        const s = JSON.stringify(value);
        return s ? s.length : 0;
    }

    /** Thrown for anything the caller could have prevented; carries a code. */
    function localStoreError(code, message) {
        const err = new Error(message);
        err.prksLocalStoreCode = code;
        return err;
    }

    /**
     * Normalizes and validates an operation envelope before it can be
     * persisted. Deliberately strict: durable user data with a malformed
     * envelope is worse than a refused write, because the refusal is visible
     * and the malformed row is not.
     *
     * Generic fields only -- per-operation payload validation belongs to the
     * operation's own validator (server-side authoritative) and arrives with
     * Milestone 2B.
     */
    function normalizeOperationEnvelope(input, context) {
        const ctx = context && typeof context === 'object' ? context : {};
        if (!isPlainObject(input)) {
            throw localStoreError('invalid_envelope', 'Operation envelope must be an object.');
        }
        const operation = input.operation;
        if (!isNonBlankString(operation) || OPERATION_TYPES.indexOf(operation) === -1) {
            throw localStoreError(
                'unknown_operation',
                'Unknown operation type: ' + String(operation)
            );
        }
        if (!isNonBlankString(input.entity_type)) {
            throw localStoreError('invalid_envelope', 'entity_type is required.');
        }
        if (!isNonBlankString(input.entity_id)) {
            throw localStoreError('invalid_envelope', 'entity_id is required.');
        }
        const payload = Object.prototype.hasOwnProperty.call(input, 'payload') ? input.payload : {};
        if (!isPlainObject(payload)) {
            throw localStoreError('invalid_envelope', 'payload must be an object.');
        }
        let payloadBytes;
        try {
            payloadBytes = jsonByteLength(payload);
        } catch (_e) {
            throw localStoreError('invalid_envelope', 'payload must be JSON-serializable.');
        }
        if (payloadBytes > MAX_PAYLOAD_BYTES) {
            throw localStoreError('payload_too_large', 'Operation payload exceeds the local limit.');
        }
        const dependsOn = Object.prototype.hasOwnProperty.call(input, 'depends_on')
            ? input.depends_on
            : [];
        if (!Array.isArray(dependsOn) || !dependsOn.every(isNonBlankString)) {
            throw localStoreError('invalid_envelope', 'depends_on must be an array of op ids.');
        }
        const opId = Object.prototype.hasOwnProperty.call(input, 'op_id') ? input.op_id : ctx.opId;
        if (!isNonBlankString(opId) || !UUID_RE.test(opId)) {
            throw localStoreError('invalid_envelope', 'op_id must be a UUID.');
        }
        const baseRevision = Object.prototype.hasOwnProperty.call(input, 'base_revision')
            ? input.base_revision
            : null;
        if (baseRevision !== null && !Number.isInteger(baseRevision)) {
            throw localStoreError('invalid_envelope', 'base_revision must be an integer or null.');
        }
        const occurredAt = Object.prototype.hasOwnProperty.call(input, 'occurred_at')
            ? input.occurred_at
            : ctx.createdAt;
        if (occurredAt != null && !isNonBlankString(occurredAt)) {
            throw localStoreError('invalid_envelope', 'occurred_at must be an ISO string.');
        }
        return {
            // --- immutable semantic envelope ---
            op_id: opId,
            device_id: ctx.deviceId || null,
            operation: operation,
            entity_type: input.entity_type.trim(),
            entity_id: input.entity_id.trim(),
            payload: JSON.parse(JSON.stringify(payload)),
            base_revision: baseRevision,
            occurred_at: occurredAt || ctx.createdAt,
            created_at: ctx.createdAt,
            sequence: ctx.sequence,
            depends_on: dependsOn.slice(),
            // --- mutable synchronization state ---
            status: STATUS_PENDING,
            attempt_count: 0,
            last_attempt_at: null,
            last_error: null,
            acknowledged_at: null,
            server_revision: null,
        };
    }

    function createPrksLocalStore(deps) {
        const options = deps && typeof deps === 'object' ? deps : {};
        const idbFactory = Object.prototype.hasOwnProperty.call(options, 'indexedDB')
            ? options.indexedDB
            : defaultIndexedDB();
        const now = options.now || defaultNow;
        const uuid = options.uuid || defaultUuid;
        const dbName = options.dbName || DB_NAME;
        const dbVersion = options.dbVersion || DB_VERSION;

        let dbPromise = null;

        function ensureStores(db) {
            if (!db.objectStoreNames.contains(STORE_OPERATIONS)) {
                db.createObjectStore(STORE_OPERATIONS, { keyPath: 'op_id' });
            }
            if (!db.objectStoreNames.contains(STORE_METADATA)) {
                db.createObjectStore(STORE_METADATA, { keyPath: 'key' });
            }
        }

        /**
         * Opens the durable database. Unlike the offline cache this REJECTS
         * when storage is unusable: a caller about to record a user's change
         * must find out, not receive a silent "unavailable" and carry on.
         */
        function openDb() {
            if (dbPromise) return dbPromise;
            dbPromise = new Promise(function (resolve, reject) {
                if (!idbFactory) {
                    reject(localStoreError('unavailable', 'IndexedDB is unavailable.'));
                    return;
                }
                let req;
                try {
                    req = idbFactory.open(dbName, dbVersion);
                } catch (e) {
                    reject(localStoreError('unavailable', 'Could not open local storage.'));
                    return;
                }
                if (!req) {
                    reject(localStoreError('unavailable', 'Could not open local storage.'));
                    return;
                }
                req.onupgradeneeded = function () {
                    ensureStores(req.result);
                };
                req.onsuccess = function () {
                    const db = req.result;
                    if (!db) {
                        reject(localStoreError('unavailable', 'Could not open local storage.'));
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
                    reject(localStoreError('unavailable', 'Could not open local storage.'));
                };
                req.onblocked = function () {
                    reject(localStoreError('blocked', 'Local storage is blocked by another tab.'));
                };
            });
            // A failed open must not be cached forever: a later attempt (after
            // the blocking tab closes, or quota is freed) should retry.
            dbPromise.catch(function () {
                dbPromise = null;
            });
            return dbPromise;
        }

        /**
         * Runs `fn(store...)` in one transaction and resolves ONLY from
         * `oncomplete`, with whatever `fn` recorded via `setResult`. Rejects on
         * request error, transaction error, and transaction abort -- so a
         * request that succeeded inside a transaction that later aborted is
         * reported as the failure it is.
         */
        function runTransaction(storeNames, mode, fn) {
            return openDb().then(function (db) {
                return new Promise(function (resolve, reject) {
                    const names = Array.isArray(storeNames) ? storeNames : [storeNames];
                    let tx;
                    try {
                        tx = db.transaction(names, mode);
                    } catch (e) {
                        reject(localStoreError('transaction_failed', 'Could not start a local transaction.'));
                        return;
                    }
                    let settled = false;
                    let result;
                    let captured = null;
                    function fail(code, message) {
                        if (settled) return;
                        settled = true;
                        // A domain error (bad envelope, duplicate op_id) is the
                        // real reason this transaction aborted, so report it
                        // rather than the generic rollback it triggered.
                        reject(captured || localStoreError(code, message));
                    }
                    tx.oncomplete = function () {
                        if (settled) return;
                        settled = true;
                        if (captured) {
                            reject(captured);
                            return;
                        }
                        resolve(result);
                    };
                    tx.onerror = function () {
                        fail('write_failed', 'The local transaction failed.');
                    };
                    tx.onabort = function () {
                        fail('write_failed', 'The local transaction was rolled back.');
                    };

                    function request(storeName, run) {
                        return new Promise(function (res, rej) {
                            let r;
                            try {
                                r = run(tx.objectStore(storeName));
                            } catch (e) {
                                rej(e);
                                return;
                            }
                            if (!r) {
                                rej(new Error('No request produced.'));
                                return;
                            }
                            r.onsuccess = function () {
                                res(r.result);
                            };
                            r.onerror = function () {
                                try {
                                    tx.abort();
                                } catch (_e) {
                                    /* ignore */
                                }
                                rej(new Error('The local request failed.'));
                            };
                        });
                    }

                    let outcome;
                    try {
                        outcome = fn(request, function setResult(v) {
                            result = v;
                        });
                    } catch (e) {
                        captured = e;
                        try {
                            tx.abort();
                        } catch (_e) {
                            /* ignore */
                        }
                        return;
                    }
                    Promise.resolve(outcome).catch(function (e) {
                        captured = e && e.prksLocalStoreCode
                            ? e
                            : localStoreError('write_failed', (e && e.message) || 'Local write failed.');
                        try {
                            tx.abort();
                        } catch (_e) {
                            /* ignore */
                        }
                    });
                });
            });
        }

        function readMetadata(key) {
            return runTransaction(STORE_METADATA, 'readonly', function (request, setResult) {
                return request(STORE_METADATA, function (store) {
                    return store.get(key);
                }).then(function (row) {
                    setResult(row ? row.value : null);
                });
            });
        }

        /**
         * Stable per-device identity, created once and persisted durably.
         *
         * This is a SYNCHRONIZATION and DIAGNOSTICS identity only -- never
         * trust, login, or authorization. It is random, not derived from any
         * browser/hardware/network characteristic, so it identifies an install
         * rather than fingerprinting a user. It survives reload, browser
         * restart and "Clear offline cache"; only an explicit reset of durable
         * local state removes it.
         */
        function getOrCreateDeviceId() {
            return runTransaction(STORE_METADATA, 'readwrite', function (request, setResult) {
                return request(STORE_METADATA, function (store) {
                    return store.get(META_DEVICE_ID);
                }).then(function (row) {
                    if (row && isNonBlankString(row.value)) {
                        setResult(row.value);
                        return null;
                    }
                    const value = uuid();
                    setResult(value);
                    return request(STORE_METADATA, function (store) {
                        return store.put({ key: META_DEVICE_ID, value: value, created_at: nowIso() });
                    });
                });
            });
        }

        function nowIso() {
            return new Date(now()).toISOString();
        }

        /**
         * Persists one semantic operation. Resolves with the stored envelope
         * only after the transaction COMMITTED; rejects otherwise. A caller may
         * show "Saved locally" only on the resolved path.
         *
         * `op_id` is allocated here if absent, and the monotonic `sequence`
         * counter is advanced in the SAME transaction as the operation row, so
         * a crash cannot hand two operations the same sequence.
         */
        function enqueueOperation(envelope, deviceId) {
            let prepared;
            return Promise.resolve()
                .then(function () {
                    const opId =
                        isPlainObject(envelope) && isNonBlankString(envelope.op_id)
                            ? envelope.op_id
                            : uuid();
                    return runTransaction(
                        [STORE_OPERATIONS, STORE_METADATA],
                        'readwrite',
                        function (request, setResult) {
                            return request(STORE_METADATA, function (store) {
                                return store.get(META_SEQUENCE);
                            })
                                .then(function (row) {
                                    const next = (row && Number.isInteger(row.value) ? row.value : 0) + 1;
                                    prepared = normalizeOperationEnvelope(envelope, {
                                        opId: opId,
                                        deviceId: deviceId || null,
                                        createdAt: nowIso(),
                                        sequence: next,
                                    });
                                    return request(STORE_OPERATIONS, function (store) {
                                        return store.get(prepared.op_id);
                                    }).then(function (existing) {
                                        if (existing) {
                                            throw localStoreError(
                                                'duplicate_op_id',
                                                'An operation with this op_id already exists.'
                                            );
                                        }
                                        return request(STORE_METADATA, function (store) {
                                            return store.put({ key: META_SEQUENCE, value: next });
                                        });
                                    });
                                })
                                .then(function () {
                                    return request(STORE_OPERATIONS, function (store) {
                                        return store.put(prepared);
                                    });
                                })
                                .then(function () {
                                    setResult(prepared);
                                });
                        }
                    );
                });
        }

        function getOperation(opId) {
            return runTransaction(STORE_OPERATIONS, 'readonly', function (request, setResult) {
                return request(STORE_OPERATIONS, function (store) {
                    return store.get(String(opId));
                }).then(function (row) {
                    setResult(row || null);
                });
            });
        }

        /**
         * All operations in durable `sequence` order -- the order the user
         * performed them, which dependency resolution and coalescing both
         * need. Optionally filtered by status.
         */
        function listOperations(filter) {
            const opts = isPlainObject(filter) ? filter : {};
            return runTransaction(STORE_OPERATIONS, 'readonly', function (request, setResult) {
                return request(STORE_OPERATIONS, function (store) {
                    return store.getAll();
                }).then(function (rows) {
                    let list = Array.isArray(rows) ? rows.slice() : [];
                    if (isNonBlankString(opts.status)) {
                        list = list.filter(function (r) {
                            return r && r.status === opts.status;
                        });
                    }
                    list.sort(function (a, b) {
                        const sa = Number.isInteger(a && a.sequence) ? a.sequence : 0;
                        const sb = Number.isInteger(b && b.sequence) ? b.sequence : 0;
                        if (sa !== sb) return sa - sb;
                        return String(a && a.op_id).localeCompare(String(b && b.op_id));
                    });
                    setResult(list);
                });
            });
        }

        /**
         * Updates ONLY the mutable synchronization fields. The semantic
         * envelope (operation, entity, payload, created_at, base_revision,
         * dependencies) is immutable once persisted: rewriting history in place
         * would make a partially-synced queue unreconstructable. Coalescing, if
         * introduced, must be an explicit transaction that writes new rows.
         */
        function updateOperationSyncState(opId, patch) {
            const changes = isPlainObject(patch) ? patch : {};
            if (Object.prototype.hasOwnProperty.call(changes, 'status') &&
                STATUSES.indexOf(changes.status) === -1) {
                return Promise.reject(localStoreError('invalid_status', 'Unknown operation status.'));
            }
            return runTransaction(STORE_OPERATIONS, 'readwrite', function (request, setResult) {
                return request(STORE_OPERATIONS, function (store) {
                    return store.get(String(opId));
                }).then(function (row) {
                    if (!row) {
                        throw localStoreError('not_found', 'No such operation.');
                    }
                    const next = Object.assign({}, row);
                    if (Object.prototype.hasOwnProperty.call(changes, 'status')) {
                        next.status = changes.status;
                    }
                    if (Object.prototype.hasOwnProperty.call(changes, 'attempt_count')) {
                        next.attempt_count = Number.isInteger(changes.attempt_count)
                            ? changes.attempt_count
                            : row.attempt_count;
                    }
                    if (changes.bump_attempt === true) {
                        next.attempt_count = (Number.isInteger(row.attempt_count) ? row.attempt_count : 0) + 1;
                        next.last_attempt_at = nowIso();
                    }
                    if (Object.prototype.hasOwnProperty.call(changes, 'last_error')) {
                        // Diagnostics only: truncated, and never a place to put
                        // credentials or raw response bodies.
                        next.last_error =
                            changes.last_error == null
                                ? null
                                : String(changes.last_error).slice(0, MAX_ERROR_CHARS);
                    }
                    if (Object.prototype.hasOwnProperty.call(changes, 'server_revision')) {
                        next.server_revision = Number.isInteger(changes.server_revision)
                            ? changes.server_revision
                            : null;
                    }
                    if (changes.status === STATUS_ACKNOWLEDGED) {
                        next.acknowledged_at = nowIso();
                    }
                    return request(STORE_OPERATIONS, function (store) {
                        return store.put(next);
                    }).then(function () {
                        setResult(next);
                    });
                });
            });
        }

        /**
         * Removes an operation the server has acknowledged. Refuses anything
         * else: dropping a pending or conflicted operation would silently
         * discard the user's change.
         */
        function deleteAcknowledgedOperation(opId) {
            return runTransaction(STORE_OPERATIONS, 'readwrite', function (request, setResult) {
                return request(STORE_OPERATIONS, function (store) {
                    return store.get(String(opId));
                }).then(function (row) {
                    if (!row) {
                        throw localStoreError('not_found', 'No such operation.');
                    }
                    if (row.status !== STATUS_ACKNOWLEDGED) {
                        throw localStoreError(
                            'not_acknowledged',
                            'Only an acknowledged operation may be removed.'
                        );
                    }
                    return request(STORE_OPERATIONS, function (store) {
                        return store.delete(String(opId));
                    }).then(function () {
                        setResult(true);
                    });
                });
            });
        }

        /** Counts by status, for the Settings "unsynchronized changes" surface. */
        function stats() {
            return listOperations().then(function (list) {
                const byStatus = {};
                STATUSES.forEach(function (s) {
                    byStatus[s] = 0;
                });
                let bytes = 0;
                list.forEach(function (row) {
                    if (Object.prototype.hasOwnProperty.call(byStatus, row.status)) {
                        byStatus[row.status] += 1;
                    }
                    try {
                        bytes += jsonByteLength(row);
                    } catch (_e) {
                        /* a single unmeasurable row must not break the report */
                    }
                });
                return {
                    total: list.length,
                    pendingTotal: list.filter(function (r) {
                        return r.status !== STATUS_ACKNOWLEDGED;
                    }).length,
                    byStatus: byStatus,
                    approxBytes: bytes,
                };
            });
        }

        /** True when durable local storage is usable at all. */
        function isAvailable() {
            return openDb().then(
                function () {
                    return true;
                },
                function () {
                    return false;
                }
            );
        }

        /**
         * Destroys ALL durable local state. Never called by "Clear offline
         * cache" -- that clears the disposable cache only. Reserved for an
         * explicit, separately-confirmed user action.
         */
        function resetDurableLocalState() {
            return new Promise(function (resolve, reject) {
                if (!idbFactory) {
                    reject(localStoreError('unavailable', 'IndexedDB is unavailable.'));
                    return;
                }
                dbPromise = null;
                let req;
                try {
                    req = idbFactory.deleteDatabase(dbName);
                } catch (e) {
                    reject(localStoreError('unavailable', 'Could not reset local state.'));
                    return;
                }
                req.onsuccess = function () {
                    resolve(true);
                };
                req.onerror = function () {
                    reject(localStoreError('write_failed', 'Could not reset local state.'));
                };
                req.onblocked = function () {
                    reject(localStoreError('blocked', 'Local state is in use by another tab.'));
                };
            });
        }

        return {
            getOrCreateDeviceId: getOrCreateDeviceId,
            enqueueOperation: enqueueOperation,
            getOperation: getOperation,
            listOperations: listOperations,
            updateOperationSyncState: updateOperationSyncState,
            deleteAcknowledgedOperation: deleteAcknowledgedOperation,
            stats: stats,
            isAvailable: isAvailable,
            resetDurableLocalState: resetDurableLocalState,
        };
    }

    const api = {
        createPrksLocalStore: createPrksLocalStore,
        prksNormalizeOperationEnvelope: normalizeOperationEnvelope,
        PRKS_LOCAL_DB_NAME: DB_NAME,
        PRKS_LOCAL_DB_VERSION: DB_VERSION,
        PRKS_LOCAL_OPERATION_TYPES: OPERATION_TYPES,
        PRKS_LOCAL_OPERATION_STATUSES: STATUSES,
        PRKS_LOCAL_MAX_PAYLOAD_BYTES: MAX_PAYLOAD_BYTES,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof self !== 'undefined' ? self : typeof window !== 'undefined' ? window : globalThis);
