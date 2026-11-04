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
 * dropped unread, so it can never record over a newer one. Answers are
 * ordered by when their read began, across tabs: an answer that a later read
 * outranks is neither recorded nor acted on, and the page reads again.
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
 * Library epoch (§10.4, §11.4). A session answer names the Library's epoch,
 * and an answer without a valid one binds no scope. The page acts under the
 * epoch it bound with (`pageEpoch()`, recorded with the scope for an offline
 * reload). `latestEpoch()` is the highest epoch a later session read of the
 * same scope named; a higher one emits `{ type: 'epoch', epoch }`. A header
 * epoch (`noteEpoch()`, the request coordinator reports every answer's) only
 * prompts that read, since a header does not say whose Library it is. Once it is
 * above the page's epoch, the Library was restored: changes made under the
 * older epoch are never sent, and this page keeps refusing new ones until a
 * reload binds the new epoch.
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
    // Reads in a row that a newer read (in any tab) outranked before the page
    // gives up for now: it binds nothing, or a verify reports 'unreachable'.
    const MAX_SESSION_READS = 3;
    // A scope id is the server's identifier; these characters keep it safe in
    // a database and cache name.
    const ID_PATTERN = /^[A-Za-z0-9_-]{1,128}$/;
    // The server's grammar for an epoch: 1 to the largest bigint, in decimal.
    const EPOCH_PATTERN = /^[1-9][0-9]{0,18}$/;

    /**
     * Base names of the per-scope stores. The full name is `<base>:<scope key>`;
     * the PDF cache's is `<base>:<scope key>:<page epoch>` (§10.4): it holds
     * file bytes, which carry no epoch of their own to filter by, so each
     * Library epoch has its own cache (frontend/sw.js).
     */
    const STORE_BASES = Object.freeze({
        local: 'prks-local-v2',
        offline: 'prks-offline-v2',
        pdf: 'prks-pdf-v3',
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

    /** An epoch as its canonical decimal string, or null. */
    function epochOf(value) {
        if (typeof value === 'number' && Number.isSafeInteger(value) && value > 0) value = String(value);
        return typeof value === 'string' && EPOCH_PATTERN.test(value) ? value : null;
    }

    /** Orders two canonical epochs (they may exceed a double's integers). */
    function compareEpochs(a, b) {
        if (a.length !== b.length) return a.length < b.length ? -1 : 1;
        return a < b ? -1 : a > b ? 1 : 0;
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

    /**
     * The record after a refusal or a sign-out: no scope, but still the time
     * it was decided, so an answer from a read that began earlier is
     * outranked by it.
     */
    function noScopeRecord(at) {
        return { key: POINTER_KEY, scope: null, confirmedAt: at };
    }

    /** When the read that confirmed a record began (0 when unknown). */
    function confirmedAt(record) {
        return record && Number(record.confirmedAt) > 0 ? Number(record.confirmedAt) : 0;
    }

    /**
     * The last confirmed scope record. Every call opens and closes its own
     * connection. Each change is ordered by when its session read began, so
     * an older answer from any tab never undoes a newer one.
     */
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
            /** `{ scope, epoch }` of the last confirmed session, or null (a record without an epoch included). */
            read: function () {
                return withStore('readonly', function (store, done) {
                    const get = store.get(POINTER_KEY);
                    get.onsuccess = function () {
                        const scope = scopeFromRecord(get.result);
                        const epoch = get.result ? epochOf(get.result.epoch) : null;
                        done(scope && epoch ? { scope: scope, epoch: epoch } : null);
                    };
                });
            },
            /**
             * Records a confirmed session of `scope` read from `startedAt`.
             * Resolves with the outcome:
             *   'logged_out'  that scope was signed out at or after the read
             *                 began (in any tab): the answer predates the
             *                 sign-out, so nothing is written.
             *   'stale'       a read that began later (in any tab) confirmed
             *                 another scope, or was refused: that newer answer
             *                 outranks this one, which is not written.
             *   'written'     recorded (or a later read already confirmed the
             *                 same scope).
             * One readwrite transaction, so the checks and the write cannot
             * interleave with another tab's change.
             */
            write: function (scope, startedAt, epoch) {
                const began = Number(startedAt) || 0;
                return withStore('readwrite', function (store, done) {
                    const marker = store.get(LOGOUT_KEY);
                    marker.onsuccess = function () {
                        const out = marker.result;
                        if (out && out.scopeKey === scope.key && !(began > (Number(out.at) || 0))) {
                            done('logged_out');
                            return;
                        }
                        const current = store.get(POINTER_KEY);
                        current.onsuccess = function () {
                            if (out && out.scopeKey === scope.key) {
                                // A session confirmed after the sign-out ends it.
                                store.delete(LOGOUT_KEY);
                            }
                            if (confirmedAt(current.result) > began) {
                                done(sameScope(scopeFromRecord(current.result), scope) ? 'written' : 'stale');
                                return;
                            }
                            store.put({
                                key: POINTER_KEY,
                                scope: { instanceId: scope.instanceId, libraryId: scope.libraryId, userId: scope.userId },
                                epoch: epoch,
                                confirmedAt: began,
                            });
                            done('written');
                        };
                    };
                });
            },
            /**
             * A refusal read from `startedAt` forgets the scope and resolves
             * 'written'. Forgetting keeps the time (a record with no scope),
             * so a confirmation from a read that began before this refusal
             * cannot bring the scope back. A read that began later (in any
             * tab) and confirmed a session outranks this older answer, which
             * resolves 'stale' and changes nothing.
             */
            forget: function (startedAt) {
                const began = Number(startedAt) || 0;
                return withStore('readwrite', function (store, done) {
                    const current = store.get(POINTER_KEY);
                    current.onsuccess = function () {
                        if (confirmedAt(current.result) > began) {
                            done(scopeFromRecord(current.result) ? 'stale' : 'written');
                            return;
                        }
                        store.put(noScopeRecord(began));
                        done('written');
                    };
                });
            },
            /**
             * Records the sign-out of `scope` at `at` (after the server ended
             * the session). The browser had one session, so the record goes
             * whichever account it names (keeping the time, as `forget`
             * does), unless a read begun after the sign-out confirmed it (a
             * new session).
             */
            markLoggedOut: function (scope, at) {
                const when = Number(at) || 0;
                return withStore('readwrite', function (store) {
                    const current = store.get(POINTER_KEY);
                    current.onsuccess = function () {
                        if (!(confirmedAt(current.result) > when)) store.put(noScopeRecord(when));
                        store.put({ key: LOGOUT_KEY, scopeKey: scope.key, at: when });
                    };
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
        // Wall clock, shared by every tab of the origin: orders a session read
        // against another tab's sign-out.
        // Sub-millisecond where available, so a read begun just after a
        // sign-out is not mistaken for one begun before it.
        const now = typeof options.now === 'function' ? options.now : function () {
            const perf = root.performance;
            if (perf && typeof perf.now === 'function' && typeof perf.timeOrigin === 'number') {
                return perf.timeOrigin + perf.now();
            }
            return Date.now();
        };

        let resolving = null;
        let bound = null;
        // The epoch this page bound with, and the highest seen since.
        let pageEpoch = null;
        let latestEpoch = null;
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
         * One bounded session read, recorded: { kind: 'session'|'none'|'unreachable'|'superseded', scope }.
         * Past the deadline it is unreachable; its late answer is dropped.
         * An answer that a read begun later (in this tab or another) outranks
         * is never acted on: the page reads again, and after
         * MAX_SESSION_READS such answers it is 'superseded' (no scope).
         */
        function readSession(attempt) {
            const tries = (Number(attempt) || 0) + 1;
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
            const startedAt = now();
            const read = fetchAnswer(controller).then(function (answer) {
                if (expired) return null;
                clearTimeout(timer);
                // A newer read (in this tab or another) outranks this answer:
                // read again rather than act on a superseded session.
                const superseded = function () {
                    return tries < MAX_SESSION_READS ? readSession(tries) : { kind: 'superseded', scope: null };
                };
                if (seq < recordedSeq) return superseded();
                recordedSeq = seq;
                return record(answer, startedAt).then(function (outcome) {
                    // Answered before a sign-out of that scope: the session is over.
                    if (outcome === 'logged_out') return { kind: 'none', scope: null };
                    if (outcome === 'stale') return superseded();
                    return answer;
                });
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
                        const epoch = epochOf(body && body.library && body.library.epoch);
                        return scope && epoch ? { kind: 'session', scope: scope, epoch: epoch } : { kind: 'none', scope: null };
                    }, function () {
                        return { kind: 'none', scope: null };
                    });
                }, function () {
                    return { kind: 'unreachable', scope: null };
                });
        }

        /** Resolves 'written', 'stale' or 'logged_out' (see `pointer.write`). */
        function record(answer, startedAt) {
            // The record follows the server's answer; a failed write only costs
            // an offline reload, never a wrong scope.
            const written = function () { return 'written'; };
            if (answer.kind === 'session') return pointer.write(answer.scope, startedAt, answer.epoch).catch(written);
            if (answer.kind === 'none') return pointer.forget(startedAt).catch(written);
            return Promise.resolve('written');
        }

        /** The page's scope, or null. Resolved once per page. */
        function resolve() {
            if (resolving) return resolving;
            resolving = readSession().then(function (answer) {
                if (answer.kind === 'session') return answer;
                if (answer.kind === 'unreachable') {
                    return pointer.read().catch(function () { return null; });
                }
                return null;
            }).then(function (found) {
                bound = found ? found.scope : null;
                pageEpoch = bound ? found.epoch : null;
                if (pageEpoch && (!latestEpoch || compareEpochs(pageEpoch, latestEpoch) > 0)) latestEpoch = pageEpoch;
                announce();
                return bound;
            });
            return resolving;
        }

        /**
         * Records an epoch a same-scope session read named for this page's
         * Library. Only a higher one counts; it is announced to subscribers.
         */
        function raiseEpoch(value) {
            const epoch = epochOf(value);
            if (!epoch || retired) return;
            if (latestEpoch && compareEpochs(epoch, latestEpoch) <= 0) return;
            latestEpoch = epoch;
            if (bound) emit({ type: 'epoch', epoch: epoch });
        }

        /**
         * An epoch some answer's header named. A header does not say whose
         * Library it is (another account may have signed in under this
         * page), so a higher one only prompts a session read (`verify()`):
         * the same scope's answer raises the epoch, another account's
         * retires the page instead.
         */
        let hinting = null;
        function noteEpoch(value) {
            const epoch = epochOf(value);
            if (!epoch || retired || !bound || hinting) return;
            if (latestEpoch && compareEpochs(epoch, latestEpoch) <= 0) return;
            hinting = verify().catch(function () { return null; }).then(function () { hinting = null; });
        }

        /** Re-reads the session against the page's scope. */
        function verify() {
            return resolve().then(function (scope) {
                if (retired) return retiredReason;
                if (!scope) return 'unbound';
                return readSession().then(function (answer) {
                    if (retired) return retiredReason;
                    // No answer that outranks every other read: try again later.
                    if (answer.kind === 'unreachable' || answer.kind === 'superseded') return 'unreachable';
                    if (answer.kind === 'none') {
                        // Expired, or signed out on purpose? Only an explicit
                        // sign-out of this scope retires the page.
                        return pointer.loggedOut(scope).catch(function () { return false; }).then(function (out) {
                            if (!out) return 'signed_out';
                            retire('logged_out');
                            return retiredReason;
                        });
                    }
                    if (sameScope(answer.scope, scope)) {
                        raiseEpoch(answer.epoch);
                        return 'same';
                    }
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
                    // Stamped after the server ended the session: any read that
                    // began before this moment cannot restore the scope.
                    return pointer.markLoggedOut(scope, now()).catch(function () {}).then(function () {
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
                    epoch: bound && !retired ? pageEpoch : null,
                    retired: retired,
                });
            } catch (_e) { /* the worker then serves this page no cache */ }
        }
        if (serviceWorker && typeof serviceWorker.addEventListener === 'function') {
            serviceWorker.addEventListener('controllerchange', function () {
                if (resolving) resolving.then(announce, announce);
            });
        }

        /** `<base>:<scope key>` (the PDF cache: `:<page epoch>` too) for a live scope, else null (never an unscoped name). */
        function dbName(kind) {
            const base = STORE_BASES[kind];
            if (!base) return Promise.resolve(null);
            return resolve().then(function (scope) {
                if (!scope || retired) return null;
                if (kind !== 'pdf') return storeName(base, scope);
                // A page that knows no epoch has no PDF cache.
                return pageEpoch ? storeName(base, scope) + ':' + pageEpoch : null;
            });
        }

        return {
            resolve: resolve,
            verify: verify,
            signOut: signOut,
            dbName: dbName,
            current: function () { return retired ? null : bound; },
            pageEpoch: function () { return retired ? null : pageEpoch; },
            latestEpoch: function () { return retired ? null : latestEpoch; },
            noteEpoch: noteEpoch,
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
        prksEpochOf: epochOf,
        prksCompareEpochs: compareEpochs,
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
        // Bind at once, so the service worker knows this page before it asks
        // for a PDF (an unbound page is served no cache).
        root.prksBrowserScope.resolve().catch(function () { /* fails closed */ });
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof self !== 'undefined' ? self : typeof window !== 'undefined' ? window : globalThis);
