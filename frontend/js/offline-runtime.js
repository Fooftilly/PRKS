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
 *   - Owns offline coherence domains: some cached read models span many
 *     canonical records, so one canonical change can stale a whole group of
 *     cached entities/lists at once. markDomainChanged() blocks that group
 *     synchronously and sweeps the disposable cache afterwards.
 */
(function (root) {
    'use strict';

    const STATE_ONLINE = 'online';
    const STATE_OFFLINE = 'offline';
    const STATE_RECONNECTING = 'reconnecting';

    const PROBE_PATH = '/api/settings';
    const DOMAIN_FOLDERS = 'folders';
    const FOLDERS_LIST_KEY = 'folders:index';
    /* Three INDEPENDENT browse projections, deliberately not one catalog:
     * a single one carrying `last_opened_at` would make merely OPENING a Work
     * invalidate Progress/Types/Recently-added too. */
    const DOMAIN_WORKS_BROWSE = 'works-browse';
    const WORKS_BROWSE_LIST_KEY = 'works-browse:index';
    const DOMAIN_RECENT = 'recent';
    const RECENT_LIST_KEY = 'recent:index';
    const DOMAIN_RECENTLY_ADDED = 'recently-added';
    const RECENTLY_ADDED_LIST_KEY = 'recently-added:index';
    const DOMAIN_CONCEPTS = 'concepts';
    const CONCEPTS_LIST_KEY = 'concepts:index';
    const DOMAIN_POSITIONS = 'positions';
    const POSITIONS_LIST_KEY = 'positions:index';
    const DOMAIN_ARGUMENTS = 'arguments';
    const ARGUMENTS_LIST_KEY = 'arguments:index';
    const DOMAIN_PEOPLE = 'people';
    const PEOPLE_LIST_KEY = 'people:index';
    const DOMAIN_PERSON_GROUPS = 'person-groups';
    const PERSON_GROUPS_LIST_KEY = 'person-groups:index';
    const DOMAIN_RESEARCH_GRAPH_CORE = 'research-graph-core';
    const DOMAIN_RESEARCH_GRAPH_PEOPLE = 'research-graph-people';
    const DOMAIN_PLAYLISTS = 'playlists';
    const PLAYLISTS_LIST_KEY = 'playlists:index';
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
        // Runtime-only coherence state. A canonical mutation makes an older
        // IndexedDB row ineligible immediately, even while its delete request
        // is still settling. Nothing here is canonical or persisted.
        const entityCoherence = new Map();
        const ineligibleEntities = new Set();
        // Domain-level coherence. Some cached read models span many canonical
        // records (a Concept rename changes that Concept's detail, the Concept
        // index, and every cached relative that displays its name), so
        // per-entity invalidation is not enough. A domain is a named group of
        // entity kinds + list keys that are invalidated together.
        const domainGeneration = new Map();
        const blockedDomains = new Set();
        const domainInvalidation = new Map();

        function entityKey(kind, id) {
            return String(kind) + '\u0000' + String(id);
        }

        function currentEntityGeneration(kind, id) {
            return entityCoherence.get(entityKey(kind, id)) || 0;
        }

        function currentDomainGeneration(domain) {
            if (!domain) return 0;
            return domainGeneration.get(String(domain)) || 0;
        }

        /** True while a domain's cached rows are ineligible for offline fallback. */
        function isDomainBlocked(domain) {
            return !!domain && blockedDomains.has(String(domain));
        }

        /** A read may publish its result only from the domain generation it began in. */
        function domainCacheEligible(domain, capturedGeneration) {
            if (!domain) return true;
            const key = String(domain);
            if (capturedGeneration !== currentDomainGeneration(key)) return false;
            return !blockedDomains.has(key);
        }

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
         * Shape acceptance for an authoritative response, applied BEFORE any
         * cache publication. A reachable server that answers 200 with a body
         * of the wrong shape is a domain error, not cache material: publishing
         * it first would destroy a previously good snapshot and leave the
         * route offline-unavailable later. Rejection propagates like any other
         * non-404 domain failure, so the caller's normal error handling runs
         * and the previous cache entry is left untouched.
         */
        function assertAcceptableShape(validate, raw) {
            if (typeof validate !== 'function') return;
            let ok = false;
            try {
                ok = !!validate(raw);
            } catch (_e) {
                ok = false;
            }
            if (ok) return;
            const err = new Error('Received an unexpected server response.');
            err.isPrksDomainError = true;
            err.isPrksShapeError = true;
            err.status = 200;
            throw err;
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
            const domain = options2.domain ? String(options2.domain) : '';
            const coherenceToken = currentEntityGeneration(kind, id);
            const domainToken = currentDomainGeneration(domain);
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
                if (ineligibleEntities.has(entityKey(kind, id)) || isDomainBlocked(domain)) {
                    return { value: null, source: 'unavailable', cachedAt: null };
                }
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
            // Validate before publishing: a malformed 200 must never replace a
            // good cached entity.
            assertAcceptableShape(options2.validate, raw);
            void cacheEntityForDomain(kind, id, raw, coherenceToken, domain, domainToken);
            return { value: raw, source: 'server', cachedAt: now() };
        }

        /** Best-effort persistence for a complete authoritative entity value. */
        function cacheEntity(kind, id, value) {
            return cacheEntityIfCurrent(kind, id, value, currentEntityGeneration(kind, id));
        }

        /** Cache only if no later canonical change has superseded this value. */
        function cacheEntityIfCurrent(kind, id, value, coherenceToken) {
            if (value == null || !store || typeof store.putEntity !== 'function') return Promise.resolve(false);
            const key = entityKey(kind, id);
            if (coherenceToken !== currentEntityGeneration(kind, id)) return Promise.resolve(false);
            try {
                return Promise.resolve(store.putEntity(kind, id, value, coherenceToken)).then(function (ok) {
                    if (ok && coherenceToken === currentEntityGeneration(kind, id)) {
                        ineligibleEntities.delete(key);
                        return true;
                    }
                    return false;
                }).catch(function () {
                    return false;
                });
            } catch (_e) {
                return Promise.resolve(false);
            }
        }

        /* Reconcile an authoritative relationship response without inventing a
         * complete Work/options snapshot when the disposable base is missing.
         * Publication generations fence pre-ACK GETs. The caller retains its
         * durable operation unless every required cache write commits. */
        async function reconcileWorkTag(result) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.work_id;
            const kinds = ['work', 'work-tag-options'];
            const tokens = kinds.map(kind => {
                const key = entityKey(kind, id);
                const token = currentEntityGeneration(kind, id) + 1;
                entityCoherence.set(key, token);
                return token;
            });
            const snapshots = await Promise.all(kinds.map(kind => store.getEntity(kind, id)));
            const work = snapshots[0] && snapshots[0].value;
            const options = snapshots[1] && snapshots[1].value;
            if (options && typeof root.prksIsWorkTagOptionsShape === 'function' && !root.prksIsWorkTagOptionsShape(options, id)) {
                return false;
            }
            let revision = 0;
            if (options) {
                const assigned = options.assigned.find(r => r.tag_id === result.tag_id);
                revision = assigned ? assigned.relation_revision : (options.known_absent[result.tag_id] || 0);
            }
            if (revision > result.server_revision) return true;
            if (work) {
                if (!Array.isArray(work.tags)) return false;
                work.tags = work.tags.filter(t => t.id !== result.tag_id);
                if (result.present) work.tags.push(result.tag);
            }
            if (options) {
                options.assigned = options.assigned.filter(t => t.tag_id !== result.tag_id);
                delete options.known_absent[result.tag_id];
                if (result.present) options.assigned.push({ tag_id: result.tag_id, relation_revision: result.server_revision });
                else if (result.server_revision) options.known_absent[result.tag_id] = result.server_revision;
            }
            const values = [work, options];
            for (let i = 0; i < kinds.length; i++) {
                if (values[i] && !await cacheEntityIfCurrent(kinds[i], id, values[i], tokens[i])) return false;
            }
            return true;
        }

        /* Reconcile one acknowledged field edit into both cached records that
         * carry it: the Work itself and its metadata-state projection.
         *
         * Both generations are bumped BEFORE the reads, so a GET that began
         * earlier cannot publish its pre-acknowledgement body over either. A
         * missing base stays missing -- one field is not enough to invent a
         * Work from -- and a partial write is reported as failure so the
         * caller keeps the durable operation and replays it.
         */
        async function reconcileWorkField(result) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.work_id;
            const kinds = ['work', 'work-metadata-state'];
            const tokens = kinds.map(function (kind) {
                const token = currentEntityGeneration(kind, id) + 1;
                entityCoherence.set(entityKey(kind, id), token);
                return token;
            });
            const snapshots = await Promise.all(kinds.map(kind => store.getEntity(kind, id)));
            const work = snapshots[0] && snapshots[0].value;
            const state = snapshots[1] && snapshots[1].value;
            if (state) {
                if (typeof root.prksIsWorkMetadataStateShape === 'function' &&
                    !root.prksIsWorkMetadataStateShape(state, id)) return false;
                const entry = state.fields[result.field];
                if (!entry) return false;
                // An acknowledgement older than what the cache already holds
                // has been superseded; applying it would move the field back.
                if (entry.revision > result.server_revision) return true;
                entry.value = result.value;
                entry.revision = result.server_revision;
            }
            if (work) work[result.field] = result.value;
            const values = [work, state];
            for (let i = 0; i < kinds.length; i++) {
                if (values[i] && !await cacheEntityIfCurrent(kinds[i], id, values[i], tokens[i])) return false;
            }
            return reconcileFieldProjections(result);
        }

        /* Some synchronized fields also live in a cached LIST. `publisher` is
         * carried by `recently-added:index` because that tab filters locally
         * over it, so an acknowledgement has to land there too -- otherwise the
         * pending overlay disappears on retirement and the row reverts to a
         * value the server no longer holds.
         *
         * The row is PATCHED rather than the whole list invalidated: dropping
         * the snapshot would cost Recently Added its offline availability for a
         * change we already know the exact shape of.
         */
        async function reconcileFieldProjections(result) {
            const projections = (root.PRKS_SYNCED_WORK_FIELD_PROJECTIONS || {})[result.field];
            if (!projections || projections.indexOf(DOMAIN_RECENTLY_ADDED) === -1) return true;
            const token = currentDomainGeneration(DOMAIN_RECENTLY_ADDED) + 1;
            domainGeneration.set(DOMAIN_RECENTLY_ADDED, token);
            const cached = await store.getList(RECENTLY_ADDED_LIST_KEY).catch(function () { return null; });
            // No snapshot is nothing to reconcile, not a failure: one field is
            // not enough to invent a Recently Added page from, and the next
            // authoritative fetch carries the value anyway.
            if (!cached) return true;
            const rows = cached.value;
            if (!Array.isArray(rows)) return false;
            if (!rows.some(row => row && row.id === result.work_id)) return true;
            const merged = rows.map(row => (row && row.id === result.work_id
                ? Object.assign({}, row, { [result.field]: result.value }) : row));
            return cacheListForDomain(RECENTLY_ADDED_LIST_KEY, merged, DOMAIN_RECENTLY_ADDED, token);
        }

        /* Reconcile an acknowledged open event into the cached Recent list.
         *
         * The domain generation is bumped BEFORE the read, so a GET /api/recent
         * that began earlier cannot publish its pre-acknowledgement body over
         * this. The domain is deliberately NOT blocked: this publishes a
         * known-good value rather than invalidating one, and blocking would
         * make Recent unavailable offline for no reason.
         *
         * A missing Recent snapshot is success, not failure: there is nothing
         * to reconcile, and one open event is not enough to invent a Recent
         * page from. The next authoritative fetch carries the right state.
         */
        async function reconcileRecentOpen(result) {
            if (!store || !await store.isAvailable()) return false;
            const token = currentDomainGeneration(DOMAIN_RECENT) + 1;
            domainGeneration.set(DOMAIN_RECENT, token);
            const cached = await store.getList(RECENT_LIST_KEY).catch(function () { return null; });
            if (!cached) return true;
            const rows = cached.value;
            if (typeof root.prksIsRecentIndexShape === 'function' && !root.prksIsRecentIndexShape(rows)) {
                return false;
            }
            const merged = typeof root.prksMergeRecentOpen === 'function'
                ? root.prksMergeRecentOpen(rows, result) : null;
            if (!merged) return false;
            return cacheListForDomain(RECENT_LIST_KEY, merged, DOMAIN_RECENT, token);
        }

        /** Remove a disposable entity snapshot. Never changes connectivity or server state. */
        function invalidateEntity(kind, id) {
            if (!store || typeof store.deleteEntity !== 'function') return Promise.resolve(false);
            try {
                return Promise.resolve(store.deleteEntity(kind, id)).catch(function () {
                    return false;
                });
            } catch (_e) {
                return Promise.resolve(false);
            }
        }

        /**
         * Call immediately after a successful canonical mutation. This must
         * happen before any UI ownership test. The returned token gates a
         * later complete Work GET so an older response cannot undo a newer
         * invalidation.
         */
        function markEntityChanged(kind, id) {
            const key = entityKey(kind, id);
            const next = currentEntityGeneration(kind, id) + 1;
            entityCoherence.set(key, next);
            ineligibleEntities.add(key);
            void invalidateEntity(kind, id);
            return next;
        }

        /** Remove a disposable list snapshot. Never changes connectivity or server state. */
        function invalidateList(listKey) {
            if (!store || typeof store.deleteList !== 'function') return Promise.resolve(false);
            try {
                return Promise.resolve(store.deleteList(listKey)).catch(function () {
                    return false;
                });
            } catch (_e) {
                return Promise.resolve(false);
            }
        }

        /**
         * Entity cache publication gated on BOTH its own entity generation and
         * its domain generation. A write that finishes after a domain
         * invalidation swept the store could otherwise survive as a stale row
         * that becomes servable again the moment cleanup unblocks the domain,
         * so such a write is removed again rather than published.
         */
        function cacheEntityForDomain(kind, id, value, coherenceToken, domain, domainToken) {
            if (!domainCacheEligible(domain, domainToken)) return Promise.resolve(false);
            return cacheEntityIfCurrent(kind, id, value, coherenceToken).then(function (ok) {
                if (!ok) return false;
                if (domainCacheEligible(domain, domainToken)) return true;
                ineligibleEntities.add(entityKey(kind, id));
                void invalidateEntity(kind, id);
                return false;
            });
        }

        /** Same publication gate as cacheEntityForDomain, for a named list snapshot. */
        function cacheListForDomain(listKey, value, domain, domainToken) {
            if (!store || typeof store.putList !== 'function') return Promise.resolve(false);
            if (!domainCacheEligible(domain, domainToken)) return Promise.resolve(false);
            try {
                return Promise.resolve(store.putList(listKey, value, ''))
                    .then(function (ok) {
                        if (!ok) return false;
                        if (domainCacheEligible(domain, domainToken)) return true;
                        void invalidateList(listKey);
                        return false;
                    })
                    .catch(function () {
                        return false;
                    });
            } catch (_e) {
                return Promise.resolve(false);
            }
        }

        /**
         * Call immediately after a successful canonical mutation whose effect
         * spans a whole cached read model rather than one row. The generation
         * bump and the fallback block are synchronous, so the stale domain is
         * ineligible the instant the mutation was acknowledged -- long before
         * the IndexedDB sweep settles. The domain unblocks only when the sweep
         * for this same generation completes successfully; a failed sweep
         * leaves it conservatively blocked (unavailable beats known-stale) and
         * a superseded generation may never settle a newer one.
         */
        function markDomainChanged(domain, spec) {
            const key = String(domain);
            const options3 = spec && typeof spec === 'object' ? spec : {};
            const next = currentDomainGeneration(key) + 1;
            domainGeneration.set(key, next);
            blockedDomains.add(key);
            const kinds = Array.isArray(options3.entityKinds) ? options3.entityKinds : [];
            const listKeys = Array.isArray(options3.listKeys) ? options3.listKeys : [];
            const jobs = [];
            kinds.forEach(function (kind) {
                if (!store || typeof store.deleteEntitiesByKind !== 'function') {
                    jobs.push(Promise.resolve(!store));
                    return;
                }
                jobs.push(
                    Promise.resolve(store.deleteEntitiesByKind(kind)).catch(function () {
                        return false;
                    })
                );
            });
            listKeys.forEach(function (listKey) {
                if (!store || typeof store.deleteList !== 'function') {
                    jobs.push(Promise.resolve(!store));
                    return;
                }
                jobs.push(invalidateList(listKey));
            });
            const cleanup = (jobs.length ? Promise.all(jobs) : Promise.resolve([]))
                .then(function (results) {
                    return results.every(function (ok) {
                        return ok !== false;
                    });
                })
                .catch(function () {
                    return false;
                })
                .then(function (ok) {
                    // Only the current generation may settle the domain: an
                    // older invalidation completing later must never unblock,
                    // reset, or publish eligibility for a newer one.
                    if (currentDomainGeneration(key) !== next) return ok;
                    if (ok) {
                        blockedDomains.delete(key);
                        domainInvalidation.delete(key);
                    }
                    return ok;
                });
            domainInvalidation.set(key, { generation: next, promise: cleanup });
            return next;
        }

        /** Same policy as readThroughEntity but for a named list snapshot. */
        async function readThroughList(listKey, path, opts) {
            const options2 = opts && typeof opts === 'object' ? opts : {};
            const domain = options2.domain ? String(options2.domain) : '';
            const domainToken = currentDomainGeneration(domain);
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
                if (isDomainBlocked(domain)) {
                    return { value: null, source: 'unavailable', cachedAt: null };
                }
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
            // Validate before publishing: a malformed 200 must never replace a
            // good cached list snapshot.
            assertAcceptableShape(options2.validate, raw);
            void cacheListForDomain(listKey, raw, domain, domainToken);
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
            reconcileWorkTag,
            reconcileWorkField,
            reconcileRecentOpen,
            cacheEntity: cacheEntity,
            cacheEntityIfCurrent: cacheEntityIfCurrent,
            invalidateEntity: invalidateEntity,
            invalidateList: invalidateList,
            markEntityChanged: markEntityChanged,
            markDomainChanged: markDomainChanged,
            currentDomainGeneration: currentDomainGeneration,
            isDomainBlocked: isDomainBlocked,
            isMutationBlocked: isMutationBlocked,
            guardMutation: guardMutation,
            diagnostics: diagnostics,
            clearCache: clearCache,
            /* test/inspection hooks */
            _runProbe: runProbe,
            _domainCleanup: function (domain) {
                const rec = domainInvalidation.get(String(domain));
                return rec ? rec.promise : Promise.resolve(true);
            },
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
    function prksOfflineCacheEntity(kind, id, value) {
        return production.cacheEntity(kind, id, value);
    }
    function prksOfflineCacheEntityIfCurrent(kind, id, value, coherenceToken) {
        return production.cacheEntityIfCurrent(kind, id, value, coherenceToken);
    }
    function prksOfflineInvalidateEntity(kind, id) {
        return production.invalidateEntity(kind, id);
    }
    function prksOfflineInvalidateList(listKey) {
        return production.invalidateList(listKey);
    }
    function prksOfflineMarkEntityChanged(kind, id) {
        return production.markEntityChanged(kind, id);
    }
    function prksOfflineMarkDomainChanged(domain, spec) {
        return production.markDomainChanged(domain, spec);
    }
    function prksOfflineDomainGeneration(domain) {
        return production.currentDomainGeneration(domain);
    }
    function prksOfflineIsDomainBlocked(domain) {
        return production.isDomainBlocked(domain);
    }
    /**
     * The one place that spells out what the Concepts offline domain contains,
     * so every canonical caller that can stale it (Concept mutations,
     * successful Research Notes saves, Work deletion, Work metadata changes
     * that alter mention titles) invalidates exactly the same set.
     */
    function prksOfflineMarkConceptsChanged() {
        return production.markDomainChanged(DOMAIN_CONCEPTS, {
            entityKinds: ['concept'],
            listKeys: [CONCEPTS_LIST_KEY],
        });
    }
    /**
     * The one place that spells out what the Positions offline domain contains,
     * so every canonical caller that can stale it invalidates exactly the same
     * set: Position mutations, plus the Argument mutations whose results are
     * embedded in a cached Position detail (name/kind/verdict/target
     * membership). Domains are independent -- this never touches Concepts.
     */
    function prksOfflineMarkPositionsChanged() {
        return production.markDomainChanged(DOMAIN_POSITIONS, {
            entityKinds: ['position'],
            listKeys: [POSITIONS_LIST_KEY],
        });
    }
    /**
     * The one place that spells out what the Arguments offline domain contains.
     * Stances are Arguments with `kind: 'stance'` -- one record family, one
     * domain, because the read model is fully interconnected (an Argument can
     * target or respond to a Stance and vice versa). Its cached data embeds
     * Position names, Work titles, Work authors and note mentions, so a wide
     * set of canonical callers invalidates it; see AGENTS.md.
     */
    function prksOfflineMarkArgumentsChanged() {
        return production.markDomainChanged(DOMAIN_ARGUMENTS, {
            entityKinds: ['argument'],
            listKeys: [ARGUMENTS_LIST_KEY],
        });
    }
    /**
     * The one place that spells out what the People offline domain contains.
     * A cached Person embeds Work-card summaries, role assignments and Group
     * memberships, so it is staled by a wide set of canonical callers -- every
     * Work-role mutation (not just Author), Work metadata/status/PDF changes,
     * and Group membership/rename/deletion. See AGENTS.md.
     */
    function prksOfflineMarkPeopleChanged() {
        return production.markDomainChanged(DOMAIN_PEOPLE, {
            entityKinds: ['person'],
            listKeys: [PEOPLE_LIST_KEY],
        });
    }
    function prksOfflineIsMutationBlocked() {
        return production.isMutationBlocked();
    }
    function prksOfflineMarkPersonGroupsChanged() {
        return production.markDomainChanged(DOMAIN_PERSON_GROUPS, {
            entityKinds: ['person-group'],
            listKeys: [PERSON_GROUPS_LIST_KEY],
        });
    }
    function prksOfflineMarkFoldersChanged() {
        return production.markDomainChanged(DOMAIN_FOLDERS, {
            entityKinds: ['folder'],
            listKeys: [FOLDERS_LIST_KEY],
        });
    }
    function prksOfflineMarkWorksBrowseChanged() {
        return production.markDomainChanged(DOMAIN_WORKS_BROWSE, {
            entityKinds: [], listKeys: [WORKS_BROWSE_LIST_KEY],
        });
    }
    function prksOfflineMarkRecentChanged() {
        return production.markDomainChanged(DOMAIN_RECENT, {
            entityKinds: [], listKeys: [RECENT_LIST_KEY],
        });
    }
    function prksOfflineMarkRecentlyAddedChanged() {
        return production.markDomainChanged(DOMAIN_RECENTLY_ADDED, {
            entityKinds: [], listKeys: [RECENTLY_ADDED_LIST_KEY],
        });
    }
    function prksOfflineMarkPlaylistsChanged() {
        return production.markDomainChanged(DOMAIN_PLAYLISTS, {
            entityKinds: ['playlist'],
            listKeys: [PLAYLISTS_LIST_KEY],
        });
    }
    function prksOfflineMarkResearchGraphCoreChanged() {
        return production.markDomainChanged(DOMAIN_RESEARCH_GRAPH_CORE, {
            entityKinds: [DOMAIN_RESEARCH_GRAPH_CORE], listKeys: [],
        });
    }
    function prksOfflineMarkResearchGraphPeopleChanged() {
        return production.markDomainChanged(DOMAIN_RESEARCH_GRAPH_PEOPLE, {
            entityKinds: [DOMAIN_RESEARCH_GRAPH_PEOPLE], listKeys: [],
        });
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
        PRKS_OFFLINE_DOMAIN_RESEARCH_GRAPH_CORE: DOMAIN_RESEARCH_GRAPH_CORE,
        PRKS_OFFLINE_DOMAIN_RESEARCH_GRAPH_PEOPLE: DOMAIN_RESEARCH_GRAPH_PEOPLE,
        prksOfflineMarkResearchGraphCoreChanged: prksOfflineMarkResearchGraphCoreChanged,
        prksOfflineMarkResearchGraphPeopleChanged: prksOfflineMarkResearchGraphPeopleChanged,
        prksOfflineFormatCachedAt: formatCachedAt,
        prksOfflineRuntimeInit: prksOfflineRuntimeInit,
        prksOfflineRuntimeState: prksOfflineRuntimeState,
        prksOfflineRuntimeSubscribe: prksOfflineRuntimeSubscribe,
        prksOfflineNoteRequestSuccess: prksOfflineNoteRequestSuccess,
        prksOfflineNoteRequestFailure: prksOfflineNoteRequestFailure,
        prksOfflineReadEntity: prksOfflineReadEntity,
        prksOfflineReadList: prksOfflineReadList,
        prksOfflineCacheEntity: prksOfflineCacheEntity,
        prksOfflineReconcileWorkTag: result => production.reconcileWorkTag(result),
        prksOfflineReconcileWorkField: result => production.reconcileWorkField(result),
        prksOfflineReconcileRecentOpen: result => production.reconcileRecentOpen(result),
        prksOfflineMarkTagsChanged: () => production.markDomainChanged('tags', { entityKinds: [], listKeys: ['tags:index'] }),
        prksOfflineCacheEntityIfCurrent: prksOfflineCacheEntityIfCurrent,
        prksOfflineInvalidateEntity: prksOfflineInvalidateEntity,
        prksOfflineInvalidateList: prksOfflineInvalidateList,
        prksOfflineMarkEntityChanged: prksOfflineMarkEntityChanged,
        prksOfflineMarkDomainChanged: prksOfflineMarkDomainChanged,
        prksOfflineDomainGeneration: prksOfflineDomainGeneration,
        prksOfflineIsDomainBlocked: prksOfflineIsDomainBlocked,
        prksOfflineMarkConceptsChanged: prksOfflineMarkConceptsChanged,
        prksOfflineMarkPositionsChanged: prksOfflineMarkPositionsChanged,
        prksOfflineMarkArgumentsChanged: prksOfflineMarkArgumentsChanged,
        prksOfflineMarkPeopleChanged: prksOfflineMarkPeopleChanged,
        prksOfflineMarkPersonGroupsChanged: prksOfflineMarkPersonGroupsChanged,
        prksOfflineMarkPlaylistsChanged: prksOfflineMarkPlaylistsChanged,
        prksOfflineIsMutationBlocked: prksOfflineIsMutationBlocked,
        prksOfflineGuardMutation: prksOfflineGuardMutation,
        prksOfflineDiagnostics: prksOfflineDiagnostics,
        prksOfflineClearCache: prksOfflineClearCache,
        PRKS_OFFLINE_STATE_ONLINE: STATE_ONLINE,
        PRKS_OFFLINE_STATE_OFFLINE: STATE_OFFLINE,
        PRKS_OFFLINE_STATE_RECONNECTING: STATE_RECONNECTING,
        PRKS_OFFLINE_PDF_CACHE_NAME: PDF_CACHE_NAME,
        PRKS_OFFLINE_DOMAIN_CONCEPTS: DOMAIN_CONCEPTS,
        PRKS_OFFLINE_CONCEPTS_LIST_KEY: CONCEPTS_LIST_KEY,
        PRKS_OFFLINE_DOMAIN_POSITIONS: DOMAIN_POSITIONS,
        PRKS_OFFLINE_POSITIONS_LIST_KEY: POSITIONS_LIST_KEY,
        PRKS_OFFLINE_DOMAIN_ARGUMENTS: DOMAIN_ARGUMENTS,
        PRKS_OFFLINE_ARGUMENTS_LIST_KEY: ARGUMENTS_LIST_KEY,
        PRKS_OFFLINE_DOMAIN_PEOPLE: DOMAIN_PEOPLE,
        PRKS_OFFLINE_PEOPLE_LIST_KEY: PEOPLE_LIST_KEY,
        PRKS_OFFLINE_DOMAIN_PERSON_GROUPS: DOMAIN_PERSON_GROUPS,
        PRKS_OFFLINE_PERSON_GROUPS_LIST_KEY: PERSON_GROUPS_LIST_KEY,
        PRKS_OFFLINE_DOMAIN_PLAYLISTS: DOMAIN_PLAYLISTS,
        PRKS_OFFLINE_PLAYLISTS_LIST_KEY: PLAYLISTS_LIST_KEY,
        PRKS_OFFLINE_DOMAIN_FOLDERS: DOMAIN_FOLDERS,
        PRKS_OFFLINE_FOLDERS_LIST_KEY: FOLDERS_LIST_KEY,
        prksOfflineMarkFoldersChanged: prksOfflineMarkFoldersChanged,
        PRKS_OFFLINE_DOMAIN_WORKS_BROWSE: DOMAIN_WORKS_BROWSE,
        PRKS_OFFLINE_WORKS_BROWSE_LIST_KEY: WORKS_BROWSE_LIST_KEY,
        prksOfflineMarkWorksBrowseChanged: prksOfflineMarkWorksBrowseChanged,
        PRKS_OFFLINE_DOMAIN_RECENT: DOMAIN_RECENT,
        PRKS_OFFLINE_RECENT_LIST_KEY: RECENT_LIST_KEY,
        prksOfflineMarkRecentChanged: prksOfflineMarkRecentChanged,
        PRKS_OFFLINE_DOMAIN_RECENTLY_ADDED: DOMAIN_RECENTLY_ADDED,
        PRKS_OFFLINE_RECENTLY_ADDED_LIST_KEY: RECENTLY_ADDED_LIST_KEY,
        prksOfflineMarkRecentlyAddedChanged: prksOfflineMarkRecentlyAddedChanged,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
