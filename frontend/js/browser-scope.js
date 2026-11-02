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
 * The session read goes through the request coordinator and is bounded:
 * past `sessionTimeoutMs` it counts as unreachable, and its late answer is
 * dropped unread, so it can never record over a newer one.
 *
 * The page's scope is fixed once resolved. `verify()` re-reads the session:
 *   - 'same'         the session is still this scope.
 *   - 'signed_out'   no session (401 or similar): the session expired. Sync
 *                    holds; rows stay, and the same account signing in again
 *                    resumes this page.
 *   - 'logged_out'   this account signed out explicitly (`signOut()`, in
 *                    this tab or another). The page's scope is retired.
 *   - 'changed'      another account signed in here. The page's scope is
 *                    retired.
 *   - 'unreachable'  the server did not answer.
 *   - 'unbound'      this page has no scope at all; only a reload binds one.
 * A retired scope's stores close and refuse to open, sync stops for good, and
 * nothing is deleted. A reload binds whoever is signed in then.
 *
 * The page re-verifies on its own lifecycle (visible again, focus, online,
 * page show, and once a minute while visible), not only before a send, so an
 * idle page with an empty queue also notices another account.
 *
 * The record of the last confirmed scope lives in its own small IndexedDB
 * database, so the service worker can read it too (`frontend/sw.js` names
 * the same database, stores and keys). The page also tells the worker which
 * scope it is bound to, so the worker serves each page its own PDF cache.
 */
(function (root) {
    'use strict';

    const SESSION_PATH = '/api/session';
    const LOGOUT_PATH = '/api/logout';
    const POINTER_DB_NAME = 'prks-browser-scope-v1';
    // Version 2 adds the worker's per-client bindings (`clients`); the worker
    // opens the same version.
    const POINTER_DB_VERSION = 2;
    const POINTER_STORE = 'pointer';
    const CLIENTS_STORE = 'clients';
    const POINTER_KEY = 'current';
    // An explicit sign-out of one scope, so a page that missed the broadcast
    // still retires (instead of holding as for an expired session).
    const LOGOUT_KEY = 'logged_out';
    const SESSION_TIMEOUT_MS = 10000;
    const LIFECYCLE_MIN_INTERVAL_MS = 2000;
    const LIFECYCLE_POLL_MS = 60000;
    const CHANNEL_NAME = 'prks-browser-scope';
    const BIND_MESSAGE = 'prks-scope-bind';
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

    // Through the request coordinator (frontend/AGENTS.md transport boundary),
    // never deduplicated or cached: each read is a fresh answer.
    function defaultFetch(url, init) {
        if (typeof root.prksRequest !== 'function') return Promise.reject(new Error('the request coordinator is unavailable'));
        return root.prksRequest(url, init, { dedupe: false });
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
                    if (!db.objectStoreNames.contains(CLIENTS_STORE)) {
                        db.createObjectStore(CLIENTS_STORE, { keyPath: 'clientId' });
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
                    // A confirmed session of this scope ends its sign-out.
                    const marker = store.get(LOGOUT_KEY);
                    marker.onsuccess = function () {
                        if (marker.result && marker.result.scopeKey === scope.key) store.delete(LOGOUT_KEY);
                    };
                });
            },
            forget: function () {
                return withStore('readwrite', function (store) {
                    store.delete(POINTER_KEY);
                });
            },
            markLoggedOut: function (scope) {
                return withStore('readwrite', function (store) {
                    store.delete(POINTER_KEY);
                    store.put({ key: LOGOUT_KEY, scopeKey: scope.key });
                });
            },
            loggedOut: function (scope) {
                return withStore('readonly', function (store, done) {
                    const get = store.get(LOGOUT_KEY);
                    get.onsuccess = function () { done(!!get.result && get.result.scopeKey === scope.key); };
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
        const sessionTimeoutMs = options.sessionTimeoutMs > 0 ? options.sessionTimeoutMs : SESSION_TIMEOUT_MS;
        const serviceWorker = options.serviceWorker || null;
        const channel = options.channel || null;

        let resolving = null;
        let bound = null;
        let retired = false;
        let retiredReason = null;
        // Each session read is numbered; only a read newer than the last one
        // recorded may change the record.
        let readSeq = 0;
        let recordedSeq = 0;

        function emit(event) {
            listeners.forEach(function (fn) {
                try { fn(event); } catch (_e) { /* subscriber isolation */ }
            });
        }

        /**
         * One bounded session read, recorded: { kind: 'session'|'none'|'unreachable', scope }.
         * Past the deadline it is unreachable; its late answer is dropped.
         */
        function readSession() {
            const seq = ++readSeq;
            const controller = typeof AbortController === 'function' ? new AbortController() : null;
            let expired = false;
            let timer = null;
            const deadline = new Promise(function (resolveDeadline) {
                timer = setTimeout(function () {
                    expired = true;
                    if (controller) controller.abort();
                    resolveDeadline({ kind: 'unreachable', scope: null });
                }, sessionTimeoutMs);
            });
            const read = fetchAnswer(controller).then(function (answer) {
                if (expired) return null;
                clearTimeout(timer);
                if (seq < recordedSeq) return answer;
                recordedSeq = seq;
                return record(answer).then(function () { return answer; });
            });
            return Promise.race([read, deadline]).then(function (answer) {
                return answer || { kind: 'unreachable', scope: null };
            });
        }

        function fetchAnswer(controller) {
            return Promise.resolve()
                .then(function () {
                    return fetchImpl(SESSION_PATH, {
                        method: 'GET',
                        credentials: 'same-origin',
                        cache: 'no-store',
                        headers: { Accept: 'application/json' },
                        signal: controller ? controller.signal : undefined,
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
                if (answer.kind === 'session') return answer.scope;
                if (answer.kind === 'unreachable') {
                    return pointer.read().catch(function () { return null; });
                }
                return null;
            }).then(function (scope) {
                bound = scope || null;
                announce();
                return bound;
            });
            return resolving;
        }

        /** Re-reads the session against the page's scope. */
        function verify() {
            return resolve().then(function (scope) {
                if (retired) return retiredReason;
                if (!scope) return 'unbound';
                return readSession().then(function (answer) {
                    if (retired) return retiredReason;
                    if (answer.kind === 'unreachable') return 'unreachable';
                    if (answer.kind === 'none') {
                        // Expired, or signed out on purpose? Only an explicit
                        // sign-out of this scope retires the page.
                        return pointer.loggedOut(scope).catch(function () { return false; }).then(function (out) {
                            if (!out) return 'signed_out';
                            retire('logged_out');
                            return retiredReason;
                        });
                    }
                    if (sameScope(answer.scope, scope)) return 'same';
                    retire('changed');
                    return retiredReason;
                });
            });
        }

        function retire(reason) {
            if (retired) return;
            retired = true;
            retiredReason = reason;
            announce();
            emit({ type: 'retired', reason: reason });
        }

        /**
         * Explicit sign-out: ends the server session, records the sign-out of
         * this scope, and retires this page and every other page of the scope
         * (broadcast now; the record for a page that misses it). Nothing is
         * deleted: the scope's rows wait until the account signs in again.
         */
        function signOut() {
            return resolve().then(function (scope) {
                const send = Promise.resolve().then(function () {
                    return fetchImpl(LOGOUT_PATH, {
                        method: 'POST',
                        credentials: 'same-origin',
                        headers: { Accept: 'application/json' },
                    });
                });
                return send.then(function (response) {
                    if (!response || !response.ok) throw new Error('sign_out_failed');
                    if (!scope) return;
                    return pointer.markLoggedOut(scope).catch(function () {}).then(function () {
                        if (channel) {
                            try { channel.postMessage({ type: 'logged_out', key: scope.key }); } catch (_e) { /* best effort */ }
                        }
                        retire('logged_out');
                    });
                });
            });
        }

        function onChannelMessage(data) {
            if (!data || data.type !== 'logged_out' || !bound || retired) return;
            if (data.key === bound.key) retire('logged_out');
        }
        if (channel) {
            channel.onmessage = function (event) { onChannelMessage(event && event.data); };
        }

        /** Tells the controlling service worker which scope this page is bound to. */
        function announce() {
            if (!serviceWorker) return;
            const controller = serviceWorker.controller;
            if (!controller || typeof controller.postMessage !== 'function') return;
            try {
                controller.postMessage({
                    type: BIND_MESSAGE,
                    key: bound && !retired ? bound.key : null,
                    retired: retired,
                });
            } catch (_e) { /* the worker then serves this page no cache */ }
        }
        if (serviceWorker && typeof serviceWorker.addEventListener === 'function') {
            serviceWorker.addEventListener('controllerchange', function () {
                if (resolving) resolving.then(announce, announce);
            });
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
            signOut: signOut,
            dbName: dbName,
            current: function () { return retired ? null : bound; },
            isRetired: function () { return retired; },
            retiredReason: function () { return retiredReason; },
            subscribe: function (fn) {
                listeners.add(fn);
                return function () { listeners.delete(fn); };
            },
        };
    }

    /**
     * Re-verifies `scope` on the page's lifecycle: visible again, focus,
     * online, page show, and every `pollMs` while visible. Calls closer than
     * `minIntervalMs` collapse into one. Returns a detach function.
     */
    function watchLifecycle(scope, win, doc, timing) {
        const t = timing || {};
        const minInterval = t.minIntervalMs >= 0 ? t.minIntervalMs : LIFECYCLE_MIN_INTERVAL_MS;
        const pollMs = t.pollMs > 0 ? t.pollMs : LIFECYCLE_POLL_MS;
        const now = t.now || Date.now;
        let last = -Infinity;
        function visible() {
            return !doc || doc.visibilityState === undefined || doc.visibilityState === 'visible';
        }
        function check() {
            if (scope.isRetired() || !visible()) return;
            const at = now();
            if (at - last < minInterval) return;
            last = at;
            scope.verify().catch(function () { /* the next event tries again */ });
        }
        const winEvents = ['focus', 'online', 'pageshow'];
        winEvents.forEach(function (name) { if (win && win.addEventListener) win.addEventListener(name, check); });
        if (doc && doc.addEventListener) doc.addEventListener('visibilitychange', check);
        const poll = setInterval(check, pollMs);
        return function detach() {
            clearInterval(poll);
            winEvents.forEach(function (name) { if (win && win.removeEventListener) win.removeEventListener(name, check); });
            if (doc && doc.removeEventListener) doc.removeEventListener('visibilitychange', check);
        };
    }

    const api = {
        createPrksBrowserScope: createPrksBrowserScope,
        prksWatchBrowserScopeLifecycle: watchLifecycle,
        PRKS_SCOPE_BIND_MESSAGE: BIND_MESSAGE,
        PRKS_SCOPE_CLIENTS_STORE: CLIENTS_STORE,
        PRKS_SCOPE_LOGOUT_KEY: LOGOUT_KEY,
        PRKS_SCOPE_POINTER_DB_VERSION: POINTER_DB_VERSION,
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
    if (root.document) {
        let channel = null;
        try {
            channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel(CHANNEL_NAME) : null;
        } catch (_e) {
            channel = null;
        }
        const serviceWorker = root.navigator && root.navigator.serviceWorker ? root.navigator.serviceWorker : null;
        root.prksBrowserScope = createPrksBrowserScope({ channel: channel, serviceWorker: serviceWorker });
        watchLifecycle(root.prksBrowserScope, root, root.document);
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof self !== 'undefined' ? self : typeof window !== 'undefined' ? window : globalThis);
