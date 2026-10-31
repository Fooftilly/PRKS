/**
 * PRKS browser scope -- which account's browser storage this page may open.
 *
 * Every durable or cached browser store belongs to one scope,
 * `(instance_id, library_id, user_id)`, read from `GET /api/session`
 * (docs/postgresql-architecture.md §10.3). Stores are named per scope, so a
 * page never opens another account's queue, cache or PDF cache, and pending
 * rows of another account stay where they are until that account signs in
 * here again.
 *
 * Fail closed. A page gets a scope only from a confirmed session:
 *   - 200 with an instance, a Library and a user: that scope. It is also
 *     recorded as the browser's last confirmed scope (ids only, no secrets).
 *   - The server unreachable (network error, 502, 503, 504): the last
 *     confirmed scope, if one is recorded, so an offline reload still opens
 *     that account's data. Nothing else.
 *   - Any other answer (401, 403, 404, a malformed body): no scope, and the
 *     record is forgotten. A server without accounts, a signed-out browser or
 *     an unknown answer never opens any store.
 *
 * The page's scope is fixed once resolved. `verify()` re-reads the session:
 *   - 'same'         the session is still this scope.
 *   - 'signed_out'   no session (401 or similar). Sync holds; rows stay.
 *   - 'changed'      another account signed in here. The page's scope is
 *                    retired: its stores close and refuse to open, and sync
 *                    stops for good. A reload binds the new account.
 *   - 'unreachable'  the server did not answer.
 *   - 'unbound'      this page has no scope at all; only a reload binds one.
 *
 * The record of the last confirmed scope lives in its own small IndexedDB
 * database, so the service worker can read it too (`frontend/sw.js` names
 * the same database, store and key).
 */
(function (root) {
    'use strict';

    const SESSION_PATH = '/api/session';
    const POINTER_DB_NAME = 'prks-browser-scope-v1';
    const POINTER_DB_VERSION = 1;
    const POINTER_STORE = 'pointer';
    const POINTER_KEY = 'current';
    const UNREACHABLE_STATUSES = [502, 503, 504];
    // A scope id is the server's identifier; these characters keep it safe in
    // a database and cache name.
    const ID_PATTERN = /^[A-Za-z0-9_-]{1,128}$/;

    /** Base names of the per-scope stores. The full name is `<base>:<scope key>`. */
    const STORE_BASES = Object.freeze({
        local: 'prks-local-v2',
        offline: 'prks-offline-v2',
        pdf: 'prks-pdf-v2',
    });

    function defaultFetch(url, init) {
        if (typeof root.fetch !== 'function') return Promise.reject(new Error('fetch is unavailable'));
        return root.fetch(url, init);
    }

    function defaultIndexedDB() {
        if (typeof indexedDB !== 'undefined') return indexedDB;
        if (root && root.indexedDB) return root.indexedDB;
        return null;
    }

    function validId(value) {
        return typeof value === 'string' && ID_PATTERN.test(value);
    }

    /** The scope a session body names, or null when it does not name one completely. */
    function scopeFromSession(body) {
        if (!body || typeof body !== 'object') return null;
        const library = body.library;
        const user = body.user;
        const instanceId = body.instance_id;
        const libraryId = library && library.id;
        const userId = user && user.id;
        if (!validId(instanceId) || !validId(libraryId) || !validId(userId)) return null;
        return makeScope(instanceId, libraryId, userId);
    }

    function makeScope(instanceId, libraryId, userId) {
        return Object.freeze({
            instanceId: instanceId,
            libraryId: libraryId,
            userId: userId,
            key: instanceId + '.' + libraryId + '.' + userId,
        });
    }

    /** A recorded scope, checked as strictly as a session body. */
    function scopeFromRecord(record) {
        if (!record || typeof record !== 'object') return null;
        const s = record.scope;
        if (!s || !validId(s.instanceId) || !validId(s.libraryId) || !validId(s.userId)) return null;
        return makeScope(s.instanceId, s.libraryId, s.userId);
    }

    function sameScope(a, b) {
        return !!a && !!b && a.key === b.key;
    }

    function storeName(base, scope) {
        return base + ':' + scope.key;
    }

    /** The last confirmed scope record. Every call opens and closes its own connection. */
    function createPointer(idbFactory) {
        function withStore(mode, fn) {
            return new Promise(function (resolve, reject) {
                if (!idbFactory) {
                    reject(new Error('IndexedDB is unavailable'));
                    return;
                }
                let req;
                try {
                    req = idbFactory.open(POINTER_DB_NAME, POINTER_DB_VERSION);
                } catch (e) {
                    reject(e);
                    return;
                }
                req.onupgradeneeded = function () {
                    const db = req.result;
                    if (!db.objectStoreNames.contains(POINTER_STORE)) {
                        db.createObjectStore(POINTER_STORE, { keyPath: 'key' });
                    }
                };
                req.onerror = function () { reject(req.error || new Error('open failed')); };
                req.onblocked = function () { reject(new Error('blocked')); };
                req.onsuccess = function () {
                    const db = req.result;
                    let tx;
                    try {
                        tx = db.transaction(POINTER_STORE, mode);
                    } catch (e) {
                        db.close();
                        reject(e);
                        return;
                    }
                    let value;
                    tx.oncomplete = function () { db.close(); resolve(value); };
                    tx.onabort = tx.onerror = function () { db.close(); reject(tx.error || new Error('aborted')); };
                    fn(tx.objectStore(POINTER_STORE), function (v) { value = v; });
                };
            });
        }
        return {
            read: function () {
                return withStore('readonly', function (store, done) {
                    const get = store.get(POINTER_KEY);
                    get.onsuccess = function () { done(scopeFromRecord(get.result)); };
                });
            },
            write: function (scope) {
                return withStore('readwrite', function (store) {
                    store.put({
                        key: POINTER_KEY,
                        scope: { instanceId: scope.instanceId, libraryId: scope.libraryId, userId: scope.userId },
                    });
                });
            },
            forget: function () {
                return withStore('readwrite', function (store) {
                    store.delete(POINTER_KEY);
                });
            },
        };
    }

    function createPrksBrowserScope(deps) {
        const options = deps && typeof deps === 'object' ? deps : {};
        const fetchImpl = options.fetchImpl || defaultFetch;
        const idbFactory = Object.prototype.hasOwnProperty.call(options, 'indexedDB')
            ? options.indexedDB
            : defaultIndexedDB();
        const pointer = createPointer(idbFactory);
        const listeners = new Set();

        let resolving = null;
        let bound = null;
        let retired = false;

        function emit(event) {
            listeners.forEach(function (fn) {
                try { fn(event); } catch (_e) { /* subscriber isolation */ }
            });
        }

        /** One session read: { kind: 'session'|'none'|'unreachable', scope }. */
        function readSession() {
            return Promise.resolve()
                .then(function () {
                    return fetchImpl(SESSION_PATH, {
                        method: 'GET',
                        credentials: 'same-origin',
                        cache: 'no-store',
                        headers: { Accept: 'application/json' },
                    });
                })
                .then(function (response) {
                    if (UNREACHABLE_STATUSES.indexOf(response.status) !== -1) return { kind: 'unreachable', scope: null };
                    if (!response.ok) return { kind: 'none', scope: null };
                    return response.json().then(function (body) {
                        const scope = scopeFromSession(body);
                        return scope ? { kind: 'session', scope: scope } : { kind: 'none', scope: null };
                    }, function () {
                        return { kind: 'none', scope: null };
                    });
                }, function () {
                    return { kind: 'unreachable', scope: null };
                });
        }

        function record(answer) {
            // The record follows the server's answer; a failed write only costs
            // an offline reload, never a wrong scope.
            if (answer.kind === 'session') return pointer.write(answer.scope).catch(function () {});
            if (answer.kind === 'none') return pointer.forget().catch(function () {});
            return Promise.resolve();
        }

        /** The page's scope, or null. Resolved once per page. */
        function resolve() {
            if (resolving) return resolving;
            resolving = readSession().then(function (answer) {
                return record(answer).then(function () {
                    if (answer.kind === 'session') return answer.scope;
                    if (answer.kind === 'unreachable') {
                        return pointer.read().catch(function () { return null; });
                    }
                    return null;
                });
            }).then(function (scope) {
                bound = scope || null;
                return bound;
            });
            return resolving;
        }

        /** Re-reads the session against the page's scope. */
        function verify() {
            return resolve().then(function (scope) {
                if (retired) return 'changed';
                if (!scope) return 'unbound';
                return readSession().then(function (answer) {
                    return record(answer).then(function () {
                        if (answer.kind === 'unreachable') return 'unreachable';
                        if (answer.kind === 'none') return 'signed_out';
                        if (sameScope(answer.scope, scope)) return 'same';
                        retire();
                        return 'changed';
                    });
                });
            });
        }

        function retire() {
            if (retired) return;
            retired = true;
            emit({ type: 'retired' });
        }

        /** `<base>:<scope key>` for a live scope, else null (never an unscoped name). */
        function dbName(kind) {
            const base = STORE_BASES[kind];
            if (!base) return Promise.resolve(null);
            return resolve().then(function (scope) {
                return scope && !retired ? storeName(base, scope) : null;
            });
        }

        return {
            resolve: resolve,
            verify: verify,
            dbName: dbName,
            current: function () { return retired ? null : bound; },
            isRetired: function () { return retired; },
            subscribe: function (fn) {
                listeners.add(fn);
                return function () { listeners.delete(fn); };
            },
        };
    }

    const api = {
        createPrksBrowserScope: createPrksBrowserScope,
        prksScopeFromSession: scopeFromSession,
        prksScopedStoreName: storeName,
        PRKS_SCOPE_STORE_BASES: STORE_BASES,
        PRKS_SCOPE_POINTER_DB_NAME: POINTER_DB_NAME,
        PRKS_SCOPE_POINTER_STORE: POINTER_STORE,
        PRKS_SCOPE_POINTER_KEY: POINTER_KEY,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    // One scope per page. Workers and Node tests build their own.
    if (root.document) root.prksBrowserScope = createPrksBrowserScope();

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof self !== 'undefined' ? self : typeof window !== 'undefined' ? window : globalThis);
