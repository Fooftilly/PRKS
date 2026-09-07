/**
 * PRKS offline runtime -- connectivity state and read-through/write-through
 * policy for the offline/PWA subsystem.
 *
 * Scope (see AGENTS.md "Offline / PWA" invariants):
 *   - Owns online/offline/reconnecting state. navigator.onLine is only ever
 *     an early hint here, never authoritative -- reachability is decided by
 *     real PRKS request success/failure.
 *   - Integrates with the read path by wrapping individual API calls, never
 *     by changing request-coordinator.js (which stays memory-only) and
 *     never by turning existing api.js fetchers into caching functions.
 *   - Delegates all persistence to offline-store.js. This module holds no
 *     canonical domain data itself.
 *   - Cached values are never handed back with server provenance. Callers
 *     always receive { value, source: 'server' | 'cache' | 'unavailable', cachedAt }.
 */
(function (root) {
    'use strict';

    const STATE_ONLINE = 'online';
    const STATE_OFFLINE = 'offline';
    const STATE_RECONNECTING = 'reconnecting';

    const PROBE_PATH = '/api/settings';
    const PROBE_BACKOFF_MS = [3000, 6000, 12000, 30000, 60000];
    const PDF_CACHE_NAME = 'prks-pdf-v1';

    function defaultNow() {
        return Date.now();
    }

    function isAbort(root2, err) {
        if (typeof root2.prksIsAbortError === 'function') return root2.prksIsAbortError(err);
        return !!(err && err.name === 'AbortError');
    }

    function createPrksOfflineRuntime(deps) {
        const options = deps && typeof deps === 'object' ? deps : {};
        const now = options.now || defaultNow;
        const requestImpl = Object.prototype.hasOwnProperty.call(options, 'prksRequest')
            ? options.prksRequest
            : typeof root.prksRequest === 'function'
              ? root.prksRequest
              : null;
        const store =
            Object.prototype.hasOwnProperty.call(options, 'store')
                ? options.store
                : typeof root.createPrksOfflineStore === 'function'
                  ? root.createPrksOfflineStore()
                  : null;
        const win = Object.prototype.hasOwnProperty.call(options, 'window')
            ? options.window
            : typeof window !== 'undefined'
              ? window
              : null;
        const setTimer = options.setTimeout || (typeof setTimeout !== 'undefined' ? setTimeout : null);
        const clearTimer = options.clearTimeout || (typeof clearTimeout !== 'undefined' ? clearTimeout : null);
        const cachesApi = Object.prototype.hasOwnProperty.call(options, 'caches')
            ? options.caches
            : typeof caches !== 'undefined'
              ? caches
              : null;
        const navigatorApi = Object.prototype.hasOwnProperty.call(options, 'navigator')
            ? options.navigator
            : typeof navigator !== 'undefined'
              ? navigator
              : null;

        let state = STATE_ONLINE;
        const listeners = [];
        let probeTimer = null;
        let probeAttempt = 0;
        let probeInFlight = false;
        let bound = false;

        function setState(next) {
            if (state === next) return;
            state = next;
            listeners.slice().forEach(function (fn) {
                try {
                    fn(state);
                } catch (_e) {
                    /* a bad subscriber must not break connectivity tracking */
                }
            });
        }

        function subscribe(fn) {
            if (typeof fn !== 'function') return function () {};
            listeners.push(fn);
            return function () {
                const i = listeners.indexOf(fn);
                if (i >= 0) listeners.splice(i, 1);
            };
        }

        function clearProbeTimer() {
            if (probeTimer && clearTimer) clearTimer(probeTimer);
            probeTimer = null;
        }

        function scheduleProbe() {
            if (!setTimer) return;
            clearProbeTimer();
            const delay = PROBE_BACKOFF_MS[Math.min(probeAttempt, PROBE_BACKOFF_MS.length - 1)];
            probeTimer = setTimer(runProbe, delay);
        }

        function runProbe() {
            probeTimer = null;
            if (probeInFlight || !requestImpl) return;
            probeInFlight = true;
            setState(STATE_RECONNECTING);
            let p;
            try {
                p = requestImpl(PROBE_PATH, {}, { priority: 'background' });
            } catch (_e) {
                p = Promise.reject(_e);
            }
            Promise.resolve(p)
                .then(function (res) {
                    probeInFlight = false;
                    // Connectivity reflects PRKS server reachability, not
                    // application-level health: the request resolving with
                    // *any* real HTTP response (2xx, 4xx, or 5xx) means the
                    // PRKS process answered. Only a rejected request (no
                    // transport response at all) means the server is
                    // unreachable.
                    if (res) {
                        probeAttempt = 0;
                        setState(STATE_ONLINE);
                    } else {
                        probeAttempt += 1;
                        setState(STATE_OFFLINE);
                        scheduleProbe();
                    }
                })
                .catch(function () {
                    probeInFlight = false;
                    probeAttempt += 1;
                    setState(STATE_OFFLINE);
                    scheduleProbe();
                });
        }

        function noteRequestSuccess() {
            probeAttempt = 0;
            clearProbeTimer();
            if (state !== STATE_ONLINE) setState(STATE_ONLINE);
        }

        function noteRequestFailure() {
            if (state === STATE_ONLINE) {
                setState(STATE_OFFLINE);
                scheduleProbe();
            }
            /* Already offline/reconnecting: an already-scheduled probe owns recovery. */
        }

        function bindEarlyHints() {
            if (bound || !win || typeof win.addEventListener !== 'function') return;
            bound = true;
            /* navigator.onLine / the browser's online+offline events are hints
             * only, never authoritative -- either one moves straight into
             * reconnecting and kicks off a real probe rather than trusting
             * the browser's own guess (runProbe() itself sets reconnecting
             * before the request settles). */
            win.addEventListener('online', runProbe);
            win.addEventListener('offline', runProbe);
        }

        async function fetchJsonStrict(path, opts) {
            if (!requestImpl) {
                const err = new Error('No request implementation configured.');
                throw err;
            }
            const res = await requestImpl(path, { signal: opts && opts.signal });
            if (!res || !res.ok) {
                const err = new Error('Request failed (' + (res ? res.status : 0) + ')');
                err.isPrksDomainError = true;
                err.status = res ? res.status : 0;
                throw err;
            }
            try {
                return await res.json();
            } catch (_e) {
                const err = new Error('Received invalid server response.');
                err.isPrksDomainError = true;
                err.status = res.status;
                throw err;
            }
        }

        function classifyError(err) {
            if (isAbort(root, err)) return 'abort';
            if (err && err.isPrksDomainError) return 'domain';
            return 'network';
        }

        /**
         * Reads one entity through the normal online path; on a real
         * network/server-unreachable failure, falls back to the offline
         * store. A real HTTP domain response (404/400/etc.) is never
         * treated as an offline condition: a 404 resolves to the caller's
         * normal not-found result, and every other domain/parse failure
         * (400/403/409/500, invalid JSON) propagates to the caller instead
         * of masquerading as "not found" or a cached/offline fallback.
         */
        async function readThroughEntity(kind, id, path, opts) {
            const options2 = opts && typeof opts === 'object' ? opts : {};
            let raw;
            try {
                raw = await fetchJsonStrict(path, options2);
            } catch (err) {
                const kindOfError = classifyError(err);
                if (kindOfError === 'abort') throw err;
                if (kindOfError === 'domain') {
                    noteRequestSuccess();
                    if (err.status === 404) {
                        return { value: null, source: 'server', cachedAt: null };
                    }
                    throw err;
                }
                noteRequestFailure();
                if (store) {
                    const cached = await store.getEntity(kind, id).catch(function () {
                        return null;
                    });
                    if (cached) {
                        return { value: cached.value, source: 'cache', cachedAt: cached.cachedAt };
                    }
                }
                return { value: null, source: 'unavailable', cachedAt: null };
            }
            noteRequestSuccess();
            if (store) {
                store.putEntity(kind, id, raw, '').catch(function () {
                    /* Caching failure must never fail the online read. */
                });
            }
            return { value: raw, source: 'server', cachedAt: now() };
        }

        /** Same policy as readThroughEntity but for a named list snapshot. */
        async function readThroughList(listKey, path, opts) {
            const options2 = opts && typeof opts === 'object' ? opts : {};
            let raw;
            try {
                raw = await fetchJsonStrict(path, options2);
            } catch (err) {
                const kindOfError = classifyError(err);
                if (kindOfError === 'abort') throw err;
                if (kindOfError === 'domain') {
                    noteRequestSuccess();
                    if (err.status === 404) {
                        return { value: null, source: 'server', cachedAt: null };
                    }
                    throw err;
                }
                noteRequestFailure();
                if (store) {
                    const cached = await store.getList(listKey).catch(function () {
                        return null;
                    });
                    if (cached) {
                        return { value: cached.value, source: 'cache', cachedAt: cached.cachedAt };
                    }
                }
                return { value: null, source: 'unavailable', cachedAt: null };
            }
            noteRequestSuccess();
            if (store) {
                store.putList(listKey, raw, '').catch(function () {});
            }
            return { value: raw, source: 'server', cachedAt: now() };
        }

        function isMutationBlocked() {
            return state !== STATE_ONLINE;
        }

        /**
         * Call before a canonical mutation. Returns true (and surfaces the
         * standard message) when the mutation must be blocked. Never queues
         * or fakes success -- Phase 1 has no offline mutation outbox.
         */
        function guardMutation(message) {
            if (!isMutationBlocked()) return false;
            if (typeof root.prksAlertMessage === 'function') {
                root.prksAlertMessage(message || 'This change requires a connection to PRKS.', 'Offline');
            }
            return true;
        }

        async function diagnostics() {
            const storeStats = store
                ? await store.stats().catch(function () {
                      return null;
                  })
                : null;
            let pdfCount = 0;
            if (cachesApi) {
                try {
                    const c = await cachesApi.open(PDF_CACHE_NAME);
                    const keys = await c.keys();
                    pdfCount = keys.length;
                } catch (_e) {
                    pdfCount = 0;
                }
            }
            let approxBytes = storeStats ? storeStats.approxBytes : 0;
            if (navigatorApi && navigatorApi.storage && typeof navigatorApi.storage.estimate === 'function') {
                try {
                    const est = await navigatorApi.storage.estimate();
                    if (est && typeof est.usage === 'number') approxBytes = est.usage;
                } catch (_e) {
                    /* keep the store-derived approximation */
                }
            }
            return {
                state: state,
                available: !!(storeStats && storeStats.available),
                entityCount: storeStats ? storeStats.entityCount : 0,
                listCount: storeStats ? storeStats.listCount : 0,
                pdfCount: pdfCount,
                approxBytes: approxBytes,
            };
        }

        /** Browser-side only. Never touches server/SQLite/PDF storage. */
        async function clearCache() {
            let ok = true;
            if (store) {
                ok = await store.clearAll().catch(function () {
                    return false;
                });
            }
            if (cachesApi) {
                try {
                    await cachesApi.delete(PDF_CACHE_NAME);
                } catch (_e) {
                    ok = false;
                }
            }
            return ok;
        }

        function getState() {
            return state;
        }

        function init() {
            bindEarlyHints();
            // Begin a real reachability probe immediately: a runtime that
            // starts up while the PRKS server is unreachable must reach
            // STATE_OFFLINE on its own, without needing a failed Work
            // request first.
            runProbe();
        }

        return {
            init: init,
            getState: getState,
            subscribe: subscribe,
            noteRequestSuccess: noteRequestSuccess,
            noteRequestFailure: noteRequestFailure,
            readThroughEntity: readThroughEntity,
            readThroughList: readThroughList,
            isMutationBlocked: isMutationBlocked,
            guardMutation: guardMutation,
            diagnostics: diagnostics,
            clearCache: clearCache,
            /* test/inspection hooks */
            _runProbe: runProbe,
        };
    }

    function formatCachedAt(ms) {
        if (!ms) return '';
        try {
            return new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        } catch (_e) {
            return '';
        }
    }

    const production = createPrksOfflineRuntime({});

    function prksOfflineRuntimeInit() {
        production.init();
    }
    function prksOfflineRuntimeState() {
        return production.getState();
    }
    function prksOfflineRuntimeSubscribe(fn) {
        return production.subscribe(fn);
    }
    function prksOfflineNoteRequestSuccess() {
        production.noteRequestSuccess();
    }
    function prksOfflineNoteRequestFailure() {
        production.noteRequestFailure();
    }
    function prksOfflineReadEntity(kind, id, path, opts) {
        return production.readThroughEntity(kind, id, path, opts);
    }
    function prksOfflineReadList(listKey, path, opts) {
        return production.readThroughList(listKey, path, opts);
    }
    function prksOfflineIsMutationBlocked() {
        return production.isMutationBlocked();
    }
    function prksOfflineGuardMutation(message) {
        return production.guardMutation(message);
    }
    function prksOfflineDiagnostics() {
        return production.diagnostics();
    }
    function prksOfflineClearCache() {
        return production.clearCache();
    }

    const api = {
        createPrksOfflineRuntime: createPrksOfflineRuntime,
        prksOfflineFormatCachedAt: formatCachedAt,
        prksOfflineRuntimeInit: prksOfflineRuntimeInit,
        prksOfflineRuntimeState: prksOfflineRuntimeState,
        prksOfflineRuntimeSubscribe: prksOfflineRuntimeSubscribe,
        prksOfflineNoteRequestSuccess: prksOfflineNoteRequestSuccess,
        prksOfflineNoteRequestFailure: prksOfflineNoteRequestFailure,
        prksOfflineReadEntity: prksOfflineReadEntity,
        prksOfflineReadList: prksOfflineReadList,
        prksOfflineIsMutationBlocked: prksOfflineIsMutationBlocked,
        prksOfflineGuardMutation: prksOfflineGuardMutation,
        prksOfflineDiagnostics: prksOfflineDiagnostics,
        prksOfflineClearCache: prksOfflineClearCache,
        PRKS_OFFLINE_STATE_ONLINE: STATE_ONLINE,
        PRKS_OFFLINE_STATE_OFFLINE: STATE_OFFLINE,
        PRKS_OFFLINE_STATE_RECONNECTING: STATE_RECONNECTING,
        PRKS_OFFLINE_PDF_CACHE_NAME: PDF_CACHE_NAME,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
