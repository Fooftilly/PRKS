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
    const DOMAIN_TAGS = 'tags';
    const TAGS_LIST_KEY = 'tags:index';
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

        /** Read a disposable cached entity value with no network. Null if missing. */
        async function peekEntity(kind, id) {
            if (!store || typeof store.getEntity !== 'function') return null;
            try {
                const snap = await store.getEntity(kind, id);
                if (!snap) return null;
                return snap.value;
            } catch (_e) {
                return null;
            }
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

        async function reconcileFolderTag(result) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.folder_id;
            const kinds = ['folder', 'folder-tag-options'];
            const tokens = kinds.map(kind => {
                const key = entityKey(kind, id);
                const token = currentEntityGeneration(kind, id) + 1;
                entityCoherence.set(key, token);
                return token;
            });
            const snapshots = await Promise.all(kinds.map(kind => store.getEntity(kind, id)));
            const folder = snapshots[0] && snapshots[0].value;
            const options = snapshots[1] && snapshots[1].value;
            if (options && typeof root.prksIsFolderTagOptionsShape === 'function' &&
                !root.prksIsFolderTagOptionsShape(options, id)) {
                return false;
            }
            let revision = 0;
            if (options) {
                const assigned = options.assigned.find(r => r.tag_id === result.tag_id);
                revision = assigned ? assigned.relation_revision : (options.known_absent[result.tag_id] || 0);
            }
            if (revision > result.server_revision) return true;
            if (folder) {
                if (!Array.isArray(folder.tags)) return false;
                folder.tags = folder.tags.filter(t => t.id !== result.tag_id);
                if (result.present) folder.tags.push(result.tag);
            }
            if (options) {
                options.assigned = options.assigned.filter(t => t.tag_id !== result.tag_id);
                delete options.known_absent[result.tag_id];
                if (result.present) {
                    options.assigned.push({ tag_id: result.tag_id, relation_revision: result.server_revision });
                } else if (result.server_revision) {
                    options.known_absent[result.tag_id] = result.server_revision;
                }
            }
            const values = [folder, options];
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
                // The projection's shape belongs to work-metadata-state.js: a
                // byte-limited field keeps its revision ONLY, and writing a
                // value here would make the cached projection fail its own
                // validator on the next read.
                state.fields[result.field] = root.prksMetadataStateAckPatch(
                    result.field, result.server_revision, result.value);
            }
            /* The acknowledgement carries the WIRE value; a Work record
             * carries the ENTITY one. Identical for every field but
             * `thumb_page`, where writing the wire string would put "3" where
             * the row's shape validator, and every renderer, expect the
             * integer 3. `prksProjectionFieldPatch` performs the same
             * conversion for browse rows through the projection transform;
             * this is it for the record itself. */
            if (work) work[result.field] = root.prksWorkFieldToEntity(result.field, result.value);
            const values = [work, state];
            for (let i = 0; i < kinds.length; i++) {
                if (values[i] && !await cacheEntityIfCurrent(kinds[i], id, values[i], tokens[i])) return false;
            }
            if (!await reconcileFieldProjections(result)) return false;
            if (!await reconcileEmbeddedSummaries(result)) return false;
            return reconcileWorkReferences(result);
        }

        /* Cached Folder, Person and Playlist details embed Work SUMMARIES, so a
         * field those rows carry has to be patched there too. We know the exact
         * new value, so the snapshots are corrected rather than dropped:
         * invalidating three whole domains would cost the user every cached
         * Folder, profile and playlist for a one-field edit.
         *
         * Each domain's generation is advanced BEFORE its rows are read, so a
         * GET that began earlier cannot publish its pre-acknowledgement body
         * afterwards. The domain is NOT blocked -- we are replacing rows with
         * known-good values, not invalidating them.
         */
        /* A reference kind's coherence DOMAIN, where it differs from the
         * entity kind: `concept` entities live in the `concepts` domain. The
         * Graph snapshots are their own domain and kind at once. */
        const REFERENCE_DOMAINS = {
            concept: DOMAIN_CONCEPTS,
            argument: DOMAIN_ARGUMENTS,
        };

        const SUMMARY_ENTITIES = [
            { kind: 'folder', domain: DOMAIN_FOLDERS, rows: value => value.works },
            { kind: 'person', domain: DOMAIN_PEOPLE, rows: value => value.works },
            { kind: 'playlist', domain: DOMAIN_PLAYLISTS, rows: value => value.items },
        ];

        /* Work values held by REFERENCE inside other entity families -- today
         * the two Research Graph snapshots. The rows are not Work summaries:
         * they name the Work by a foreign key and often under a different
         * column, so the shape registry in `work-metadata-state.js` owns the
         * traversal and this only drives it.
         *
         * Patched rather than invalidated, for the same reason as the embedded
         * summaries: dropping a Graph snapshot would cost the user the whole
         * cached Graph for a one-field edit whose exact new value is known.
         * Each domain's generation advances BEFORE its snapshot is read, so a
         * GET that began earlier cannot publish its pre-acknowledgement body
         * afterwards. */
        /* An acknowledged SOURCE identity, patched into every cached
         * representation that carries source columns.
         *
         * All four columns are written TOGETHER, per row. A row that briefly
         * said `source_url = B` while `provider_id` still said A would be the
         * contradiction the aggregate exists to prevent, and "briefly" is not
         * a defence when a render can happen in between.
         */
        /* Patch ONLY the columns a row already carries.
         *
         * The acknowledgement states six: the four identity columns plus
         * `thumb_url` and `urldate`, which the write also rewrites. Different
         * cached representations carry different subsets -- a browse row has
         * `thumb_url` but no `urldate` -- and writing a column the server never
         * sends to that projection would make the row fail its own shape
         * validator, which discards the whole catalog. So the row's existing
         * keys decide what is patched.
         */
        function patchedWithSource(row, source) {
            const next = Object.assign({}, row);
            let touched = false;
            for (const column of Object.keys(source)) {
                if (!Object.prototype.hasOwnProperty.call(row, column)) continue;
                next[column] = source[column];
                touched = true;
            }
            return touched ? next : null;
        }

        /**
         * A Work-Person role acknowledgement, applied to every acknowledged
         * representation that carries the relationship.
         *
         * The structured `linked_people` list is the source of truth on a row;
         * the flattened credit columns are DERIVED from it by the same helper
         * the overlay uses, so a patched row cannot disagree with itself about
         * who is credited.
         */
        /* The Work DETAIL entity's `roles[]`. The shape differs from a browse
         * row's `linked_people[]` -- whole Person rows keyed by `id`, because
         * the panel renders profile links from them -- and the patch lives in
         * `work-role-state.js` so the live editor's tab entity and this cache
         * cannot disagree about the same acknowledged link. */
        function patchedDetailRoles(row, result) {
            return typeof root.prksPatchWorkDetailRoles === 'function'
                ? root.prksPatchWorkDetailRoles(row, result) : null;
        }

        function patchedWithRole(row, result) {
            if (!row) return null;
            if (!Array.isArray(row.linked_people)) return patchedDetailRoles(row, result);
            const links = row.linked_people.filter(link => !(link &&
                link.person_id === result.person_id && link.role_type === result.role_type));
            const previous = row.linked_people.find(link => link &&
                link.person_id === result.person_id && link.role_type === result.role_type);
            if (result.present) {
                const canonical = (previous && previous.canonical_name) ||
                    result.canonical_name || '';
                links.push({
                    person_id: result.person_id, role_type: result.role_type,
                    order_index: previous ? previous.order_index : null,
                    canonical_name: canonical,
                    credit_name: result.credit_name,
                    display_name: result.credit_name || canonical,
                });
                /* Re-sorted the way the server orders: existing placement
                 * first, a new link appended after everything. */
                links.sort((a, b) => {
                    const ai = Number.isInteger(a.order_index) ? a.order_index : Infinity;
                    const bi = Number.isInteger(b.order_index) ? b.order_index : Infinity;
                    return ai - bi;
                });
            }
            const flattened = typeof root.prksFlattenedWorkCredit === 'function'
                ? root.prksFlattenedWorkCredit(links) : {};
            return Object.assign({}, row, { linked_people: links }, flattened);
        }

        /**
         * The Graph's Work-Person edge, patched in the cached snapshots.
         *
         * Only the `Author` role produces one, and only in the snapshot built
         * with people included -- both facts come from the builder, not from
         * the edge's name. A snapshot that does not hold the Work has no edge
         * to draw, and one that does not already hold the Person cannot gain
         * an exact node from an acknowledgement alone, so those are left for
         * the next read rather than drawn approximately.
         */
        async function reconcileGraphAuthorEdge(result) {
            if (result.role_type !== 'Author') return true;
            const kind = DOMAIN_RESEARCH_GRAPH_PEOPLE;
            const token = currentDomainGeneration(kind) + 1;
            domainGeneration.set(kind, token);
            const cached = await store.getEntity(kind, 'snapshot').catch(function () {
                return undefined;
            });
            if (cached === undefined) return false;   // a FAILED read is not "nothing to do"
            if (cached === null) return true;         // no snapshot is nothing to patch
            const snapshot = cached.value;
            if (!snapshot || !Array.isArray(snapshot.nodes) || !Array.isArray(snapshot.edges)) {
                return true;
            }
            const source = 'person:' + result.person_id;
            const target = 'work:' + result.work_id;
            const edgeId = 'work_author:' + source + '>' + target;
            const hasWork = snapshot.nodes.some(n => n && n.id === target);
            const hasPerson = snapshot.nodes.some(n => n && n.id === source);
            const present = snapshot.edges.some(e => e && e.id === edgeId);
            if (!hasWork) return true;
            let edges = snapshot.edges;
            if (result.present && !present) {
                if (!hasPerson) return true;          // no exact node to attach it to
                edges = snapshot.edges.concat([{ id: edgeId, type: 'work_author',
                    source, target }]).sort((a, b) =>
                        a.type.localeCompare(b.type) || a.source.localeCompare(b.source) ||
                        a.target.localeCompare(b.target) || a.id.localeCompare(b.id));
            } else if (!result.present && present) {
                edges = snapshot.edges.filter(e => !(e && e.id === edgeId));
            } else {
                return true;
            }
            const next = Object.assign({}, snapshot, { edges,
                meta: Object.assign({}, snapshot.meta, { edge_count: edges.length }) });
            const entityToken = currentEntityGeneration(kind, 'snapshot') + 1;
            entityCoherence.set(entityKey(kind, 'snapshot'), entityToken);
            return cacheEntityIfCurrent(kind, 'snapshot', next, entityToken);
        }

        async function reconcileWorkRole(result) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.work_id;
            /* The relationship-state projection carries the base the NEXT edit
             * is measured against. Left behind, a second change is created
             * against a revision the server has already passed -- so the user
             * conflicts with their own previous acknowledgement. */
            const kinds = ['work', 'work-people-state'];
            const tokens = kinds.map(function (kind) {
                const token = currentEntityGeneration(kind, id) + 1;
                entityCoherence.set(entityKey(kind, id), token);
                return token;
            });
            const snapshots = await Promise.all(kinds.map(kind => store.getEntity(kind, id)));
            const work = snapshots[0] && snapshots[0].value;
            const state = snapshots[1] && snapshots[1].value;
            if (work) {
                /* The Work entity carries BOTH shapes: the browse-style
                 * `linked_people[]` and the detail's `roles[]`. Patch each that
                 * is present, so the panel and the cards cannot disagree. */
                let patched = patchedWithRole(work, result) || work;
                patched = patchedDetailRoles(patched, result) || patched;
                if (patched !== work &&
                    !await cacheEntityIfCurrent('work', id, patched, tokens[0])) {
                    return false;
                }
            }
            if (state && Array.isArray(state.scopes) &&
                Number.isSafeInteger(result.server_revision)) {
                const scopes = state.scopes.filter(scope => !(scope &&
                    scope.person_id === result.person_id &&
                    scope.role_type === result.role_type));
                scopes.push({ person_id: result.person_id, role_type: result.role_type,
                    revision: result.server_revision, present: !!result.present });
                scopes.sort((a, b) => (a.person_id + a.role_type)
                    .localeCompare(b.person_id + b.role_type));
                const next = Object.assign({}, state, { scopes });
                if (!await cacheEntityIfCurrent('work-people-state', id, next, tokens[1])) {
                    return false;
                }
            }
            for (const [domain, listKey] of Object.entries(FIELD_PROJECTION_LISTS)) {
                const token = currentDomainGeneration(domain) + 1;
                domainGeneration.set(domain, token);
                const cached = await store.getList(listKey).catch(function () { return null; });
                if (!cached) continue;   // a missing snapshot is nothing to patch
                const rows = cached.value;
                if (!Array.isArray(rows)) return false;
                let touched = false;
                const merged = rows.map(row => {
                    if (!row || row.id !== id) return row;
                    const patched = patchedWithRole(row, result);
                    if (!patched) return row;
                    touched = true;
                    return patched;
                });
                if (!touched) continue;
                if (!await cacheListForDomain(listKey, merged, domain, token)) return false;
            }
            for (const spec of SUMMARY_ENTITIES) {
                if (typeof store.getEntitiesByKind !== 'function') continue;
                const token = currentDomainGeneration(spec.domain) + 1;
                domainGeneration.set(spec.domain, token);
                const cached = await store.getEntitiesByKind(spec.kind)
                    .catch(function () { return null; });
                // Nothing cached of this kind is nothing to reconcile; a FAILED
                // read is not the same answer and must not retire the operation.
                if (!Array.isArray(cached)) return false;
                for (const row of cached) {
                    if (!row || !row.value) continue;
                    const summaries = spec.rows(row.value);
                    if (!Array.isArray(summaries)) continue;
                    let touched = false;
                    summaries.forEach(function (summary, index) {
                        if (!summary || summary.id !== id) return;
                        const patched = patchedWithRole(summary, result);
                        if (!patched) return;
                        summaries[index] = patched;
                        touched = true;
                    });
                    if (!touched) continue;
                    const entityToken = currentEntityGeneration(spec.kind, row.id) + 1;
                    entityCoherence.set(entityKey(spec.kind, row.id), entityToken);
                    if (!await cacheEntityIfCurrent(spec.kind, row.id, row.value, entityToken)) {
                        return false;
                    }
                }
            }
            /* The Person's own cached detail lists the Works they are credited
             * on, and that membership is exactly what just changed. The rows
             * there are Work summaries, so the loop above already patched the
             * ones this Work appears in -- what remains is the case where the
             * Work is NOT in that list and now should be, or the reverse. That
             * cannot be built from a role acknowledgement alone (the summary
             * would have to be invented), so the Person is marked changed and
             * re-read rather than guessed at. */
            markEntityChanged('person', result.person_id);
            if (!await reconcileGraphAuthorEdge(result)) return false;
            /* The remaining dependencies, through the ONE helper that owns what
             * a role change stales -- Person Groups, and for an Author the
             * cached Argument source authors. Those render a credit from
             * projections the reference-shape registry does not cover, so the
             * exact new value cannot be written into them and re-reading is the
             * honest answer. Deliberately NOT the helper that also evicts the
             * Work: this pass just patched it with the exact new links, and
             * invalidating would throw that away. */
            if (typeof root.prksMarkWorkRoleDependenciesChanged === 'function') {
                root.prksMarkWorkRoleDependenciesChanged(result.role_type);
            } else {
                markDomainChanged(DOMAIN_PEOPLE);
            }
            return true;
        }

        async function reconcilePdfAnnotation(result) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.work_id;
            const annotationId = result.annotation_id;
            if (!id || !annotationId || !Number.isSafeInteger(result.server_revision)) {
                return false;
            }
            const kind = 'work-annotations-snapshot';
            const token = currentEntityGeneration(kind, id) + 1;
            entityCoherence.set(entityKey(kind, id), token);
            const envelope = await store.getEntity(kind, id);
            const snap = envelope && envelope.value;
            if (!snap || typeof snap !== 'object' || !Array.isArray(snap.items) ||
                !Array.isArray(snap.annotations)) {
                /* No coherent base cached — ACK still succeeds; next hydrate
                 * repopulates. Do not invent a partial snapshot. */
                return true;
            }
            const present = result.present !== false && !!result.annotation;
            let nextItems = snap.items.filter(function (item) {
                if (!item || typeof item !== 'object') return true;
                const itemId = item.id || item.uuid || item.annotationId || item.annotation_id;
                return String(itemId) !== String(annotationId);
            });
            if (present) nextItems = nextItems.concat([result.annotation]);
            const annotations = snap.annotations.filter(function (row) {
                return !(row && row.annotation_id === annotationId);
            });
            const knownAbsent = Object.assign({}, snap.known_absent || {});
            if (present) {
                annotations.push({
                    annotation_id: annotationId,
                    revision: result.server_revision,
                });
                delete knownAbsent[annotationId];
            } else {
                knownAbsent[annotationId] = result.server_revision;
            }
            annotations.sort(function (a, b) {
                return String(a.annotation_id).localeCompare(String(b.annotation_id));
            });
            const nextSnap = Object.assign({}, snap, {
                items: nextItems,
                annotations: annotations,
                known_absent: knownAbsent,
            });
            // Patch the ACKed annotation body, but do not relabel the cached
            // object as a newer coherent snapshot unless generation continuity
            // proves no unseen set changes (cached gen + 1 === ACK gen) and the
            // ACK changed something (changed:false cannot own a gen increment).
            if (Number.isSafeInteger(result.canonical_annotation_set_revision)) {
                const nextGen = result.canonical_annotation_set_revision;
                const prevGen = Number.isSafeInteger(snap.canonical_annotation_set_revision)
                    ? snap.canonical_annotation_set_revision
                    : null;
                if (prevGen === null || nextGen === prevGen) {
                    nextSnap.canonical_annotation_set_revision = nextGen;
                } else if (result.changed === true && nextGen === prevGen + 1) {
                    nextSnap.canonical_annotation_set_revision = nextGen;
                }
                // else: keep snap.canonical_annotation_set_revision unchanged
            }
            if (!await cacheEntityIfCurrent(kind, id, nextSnap, token)) {
                return false;
            }
            return true;
        }

        async function reconcileWorkSource(result) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.work_id;
            const source = result.source;
            if (!source) return false;
            /* The source-state projection is a REVISION and nothing else, and
             * it is the base the next edit is measured against. Leaving it at
             * the old number means a second change is created against a
             * revision the server has already moved past -- and the user's own
             * previous edit becomes the thing they conflict with. */
            const kinds = ['work', 'work-source-state'];
            const tokens = kinds.map(function (kind) {
                const token = currentEntityGeneration(kind, id) + 1;
                entityCoherence.set(entityKey(kind, id), token);
                return token;
            });
            const snapshots = await Promise.all(kinds.map(kind => store.getEntity(kind, id)));
            const work = snapshots[0] && snapshots[0].value;
            const state = snapshots[1] && snapshots[1].value;
            if (work) {
                const patched = patchedWithSource(work, source);
                if (patched && !await cacheEntityIfCurrent('work', id, patched, tokens[0])) {
                    return false;
                }
            }
            if (state && Number.isSafeInteger(result.server_revision)) {
                // An acknowledgement older than the cache has been superseded;
                // applying it would move the base backwards.
                if (!Number.isSafeInteger(state.revision) ||
                    state.revision <= result.server_revision) {
                    const next = Object.assign({}, state, { revision: result.server_revision });
                    if (!await cacheEntityIfCurrent('work-source-state', id, next, tokens[1])) {
                        return false;
                    }
                }
            }
            for (const [domain, listKey] of Object.entries(FIELD_PROJECTION_LISTS)) {
                const token = currentDomainGeneration(domain) + 1;
                domainGeneration.set(domain, token);
                const cached = await store.getList(listKey).catch(function () { return null; });
                if (!cached) continue;   // a missing snapshot is nothing to patch
                const rows = cached.value;
                if (!Array.isArray(rows)) return false;
                if (!rows.some(row => row && row.id === id)) continue;
                const merged = rows.map(row => (row && row.id === id
                    ? (patchedWithSource(row, source) || row) : row));
                if (!await cacheListForDomain(listKey, merged, domain, token)) return false;
            }
            for (const spec of SUMMARY_ENTITIES) {
                if (typeof store.getEntitiesByKind !== 'function') continue;
                const token = currentDomainGeneration(spec.domain) + 1;
                domainGeneration.set(spec.domain, token);
                const cached = await store.getEntitiesByKind(spec.kind)
                    .catch(function () { return null; });
                if (!Array.isArray(cached)) return false;
                for (const row of cached) {
                    if (!row || !row.value) continue;
                    const summaries = spec.rows(row.value);
                    if (!Array.isArray(summaries)) continue;
                    let touched = false;
                    summaries.forEach(function (summary, index) {
                        if (!summary || summary.id !== id) return;
                        const patched = patchedWithSource(summary, source);
                        if (!patched) return;
                        summaries[index] = patched;
                        touched = true;
                    });
                    if (!touched) continue;
                    const entityToken = currentEntityGeneration(spec.kind, row.id) + 1;
                    entityCoherence.set(entityKey(spec.kind, row.id), entityToken);
                    if (!await cacheEntityIfCurrent(spec.kind, row.id, row.value, entityToken)) {
                        return false;
                    }
                }
            }
            return true;
        }

        /**
         * Patch one whole-document note into the cached Work and notes-state.
         *
         * The ACK omits the body (`value_omitted`); the immutable operation
         * carries the text the server applied. Notes-state holds revisions
         * only. Research Notes also fence Concept / Argument / Graph
         * projections because the server ran canonical markup processing --
         * this client does not parse the body to decide what those caches
         * contain.
         *
         * `changed` is not enough. A stale device that independently
         * wrote the same text the server already holds is ACKNOWLEDGED
         * with `changed=false` at a *newer* revision. That device's
         * Concept/Argument/Graph caches may still be derived from the
         * older body it observed. Fence when the body changed *or* the
         * canonical revision advanced past this operation's observed
         * base. A same-revision no-op (`changed=false` and
         * `server_revision === base_revision`) does not.
         */
        async function reconcileWorkNoteBody(result, op, field, revisionKey, fenceResearch) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.work_id;
            if (!id || id !== op.entity_id) return false;
            const text = op.payload && typeof op.payload.text === 'string' ? op.payload.text : null;
            if (text === null) return false;
            if (!Number.isSafeInteger(result.server_revision) || result.server_revision < 0) {
                return false;
            }
            const kinds = ['work', 'work-notes-state'];
            const tokens = kinds.map(function (kind) {
                const token = currentEntityGeneration(kind, id) + 1;
                entityCoherence.set(entityKey(kind, id), token);
                return token;
            });
            const snapshots = await Promise.all(kinds.map(kind => store.getEntity(kind, id)));
            const work = snapshots[0] && snapshots[0].value;
            const state = snapshots[1] && snapshots[1].value;
            if (work) {
                const nextWork = Object.assign({}, work);
                nextWork[field] = field === 'private_notes' ? (text || null) : text;
                if (fenceResearch && result.research_refs &&
                    typeof result.research_refs === 'object' &&
                    !Array.isArray(result.research_refs)) {
                    nextWork.research_refs = result.research_refs;
                }
                if (!await cacheEntityIfCurrent('work', id, nextWork, tokens[0])) return false;
            }
            if (state) {
                if (typeof root.prksNoteStateShape === 'function' &&
                    !root.prksNoteStateShape(state, id)) return false;
                const current = state[revisionKey];
                if (!Number.isSafeInteger(current) || current <= result.server_revision) {
                    const next = Object.assign({}, state);
                    next[revisionKey] = result.server_revision;
                    if (!await cacheEntityIfCurrent('work-notes-state', id, next, tokens[1])) {
                        return false;
                    }
                }
            }
            const canonicalAdvanced =
                Number.isSafeInteger(op.base_revision) &&
                result.server_revision > op.base_revision;
            if (fenceResearch && (result.changed || canonicalAdvanced)) {
                markDomainChanged(DOMAIN_CONCEPTS, {
                    entityKinds: ['concept'], listKeys: [CONCEPTS_LIST_KEY],
                });
                markDomainChanged(DOMAIN_ARGUMENTS, {
                    entityKinds: ['argument'], listKeys: [ARGUMENTS_LIST_KEY],
                });
                markDomainChanged(DOMAIN_RESEARCH_GRAPH_CORE, {
                    entityKinds: [DOMAIN_RESEARCH_GRAPH_CORE], listKeys: [],
                });
                markDomainChanged(DOMAIN_RESEARCH_GRAPH_PEOPLE, {
                    entityKinds: [DOMAIN_RESEARCH_GRAPH_PEOPLE], listKeys: [],
                });
            }
            return true;
        }

        async function reconcileWorkNote(result, op) {
            return reconcileWorkNoteBody(result, op, 'text_content',
                'research_note_revision', true);
        }

        async function reconcilePrivateNote(result, op) {
            return reconcileWorkNoteBody(result, op, 'private_notes',
                'private_note_revision', false);
        }

        async function reconcileWorkReferences(result) {
            const kinds = root.PRKS_WORK_REFERENCE_KINDS || [];
            for (const kind of kinds) {
                const patches = root.prksWorkReferencePatches(kind, result.field, result.value);
                if (!patches.length) continue;   // this field reaches no row of this kind
                const domain = REFERENCE_DOMAINS[kind] || kind;
                const token = currentDomainGeneration(domain) + 1;
                domainGeneration.set(domain, token);
                const cached = typeof store.getEntitiesByKind === 'function'
                    ? await store.getEntitiesByKind(kind).catch(function () { return null; })
                    : null;
                // Nothing cached of this kind is nothing to reconcile; a
                // FAILED read is not the same answer and must not retire the
                // operation.
                if (!Array.isArray(cached)) return false;
                for (const row of cached) {
                    if (!row || !row.value) continue;
                    let touched = false;
                    for (const patch of patches) {
                        const rows = row.value[patch.path];
                        if (!Array.isArray(rows)) continue;
                        rows.forEach(function (item, index) {
                            if (!item || item[patch.key] !== result.work_id) return;
                            if (patch.accepts && !patch.accepts(item)) return;
                            rows[index] = Object.assign({}, item, { [patch.column]: patch.value });
                            touched = true;
                        });
                    }
                    if (!touched) continue;
                    const entityToken = currentEntityGeneration(kind, row.id) + 1;
                    entityCoherence.set(entityKey(kind, row.id), entityToken);
                    if (!await cacheEntityIfCurrent(kind, row.id, row.value, entityToken)) return false;
                }
            }
            return true;
        }

        async function reconcileEmbeddedSummaries(result) {
            const fields = root.PRKS_WORK_SUMMARY_FIELDS || [];
            if (fields.indexOf(result.field) === -1) return true;
            for (const spec of SUMMARY_ENTITIES) {
                if (!await reconcileSummaryKind(result, spec)) return false;
            }
            return true;
        }

        async function reconcileSummaryKind(result, spec) {
            if (typeof store.getEntitiesByKind !== 'function') return true;
            const token = currentDomainGeneration(spec.domain) + 1;
            domainGeneration.set(spec.domain, token);
            const cached = await store.getEntitiesByKind(spec.kind).catch(function () { return null; });
            // Nothing cached of this kind is nothing to reconcile, not a
            // failure -- and one field is never enough to invent a snapshot.
            if (!Array.isArray(cached)) return false;
            for (const row of cached) {
                if (!row || !row.value) continue;
                const summaries = spec.rows(row.value);
                if (!Array.isArray(summaries)) continue;
                let touched = false;
                summaries.forEach(function (summary, index) {
                    if (!summary || summary.id !== result.work_id) return;
                    summaries[index] = Object.assign({}, summary,
                        { [result.field]: root.prksWorkFieldToEntity(result.field, result.value) });
                    touched = true;
                });
                if (!touched) continue;
                const entityToken = currentEntityGeneration(spec.kind, row.id) + 1;
                entityCoherence.set(entityKey(spec.kind, row.id), entityToken);
                if (!await cacheEntityIfCurrent(spec.kind, row.id, row.value, entityToken)) return false;
            }
            return true;
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
        const FIELD_PROJECTION_LISTS = {
            'recently-added': RECENTLY_ADDED_LIST_KEY,
            'works-browse': WORKS_BROWSE_LIST_KEY,
            'recent': RECENT_LIST_KEY,
        };

        async function reconcileFieldProjections(result) {
            const projections = (root.PRKS_SYNCED_WORK_FIELD_PROJECTIONS || {})[result.field] || [];
            for (const domain of projections) {
                const listKey = FIELD_PROJECTION_LISTS[domain];
                // A declared projection we cannot address is a wiring error,
                // and skipping it would leave that list serving a value the
                // server no longer holds -- exactly the drift this reconciles.
                if (!listKey) return false;
                if (!await reconcileProjectionList(result, domain, listKey)) return false;
            }
            return true;
        }

        async function reconcileProjectionList(result, domain, listKey) {
            const token = currentDomainGeneration(domain) + 1;
            domainGeneration.set(domain, token);
            const cached = await store.getList(listKey).catch(function () { return null; });
            // No snapshot is nothing to reconcile, not a failure: one field is
            // not enough to invent a catalog from, and the next authoritative
            // fetch carries the value anyway.
            if (!cached) return true;
            const rows = cached.value;
            if (!Array.isArray(rows)) return false;
            if (!rows.some(row => row && row.id === result.work_id)) return true;
            // The SAME derivation the overlay used, so the row does not visibly
            // change at acknowledgement.
            const patch = root.prksProjectionFieldPatch(domain, result.field, result.value);
            if (!patch) return true;
            const merged = rows.map(row => (row && row.id === result.work_id
                ? Object.assign({}, row, patch) : row));
            return cacheListForDomain(listKey, merged, domain, token);
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

        async function reconcileCreatedPerson(result) {
            if (!store || !await store.isAvailable()) return false;
            const person = result && result.person;
            if (!person || person.id !== result.person_id) return false;
            const token = currentDomainGeneration(DOMAIN_PEOPLE) + 1;
            domainGeneration.set(DOMAIN_PEOPLE, token);
            const cached = await store.getList(PEOPLE_LIST_KEY).catch(function () { return null; });
            if (cached) {
                const rows = cached.value;
                if (typeof root.prksIsPeopleIndexShape === 'function' &&
                    Array.isArray(rows) && !root.prksIsPeopleIndexShape(rows)) {
                    return false;
                }
                const merged = typeof root.prksMergeCreatedPerson === 'function'
                    ? root.prksMergeCreatedPerson(rows, result) : null;
                if (!merged) return false;
                if (!await cacheListForDomain(PEOPLE_LIST_KEY, merged, DOMAIN_PEOPLE, token)) {
                    return false;
                }
            }
            if (typeof root.prksIsPersonShape === 'function' &&
                !root.prksIsPersonShape(person, result.person_id)) {
                return true;
            }
            const entityToken = currentEntityGeneration('person', result.person_id) + 1;
            entityCoherence.set(entityKey('person', result.person_id), entityToken);
            return cacheEntityIfCurrent('person', result.person_id, person, entityToken);
        }

        /**
         * A Person profile field the server has applied.
         *
         * The value comes from the OPERATION, not the answer: the
         * acknowledgement deliberately omits it (no profile field has a length
         * bound, and a result the client cannot durably store is read as a
         * failed sync and retried forever). The client already holds the
         * authoritative copy in its own immutable payload.
         *
         * Snapshots that hold the value are PATCHED; read models that merely
         * display the Person's NAME are invalidated instead. The difference is
         * not laziness: a credit line, a Graph label and a cached Argument
         * source embed the name in rows keyed by Work, so there is no precise
         * patch to make -- and this only ever runs while connected, which is
         * exactly when a refetch is affordable. The ordinary PATCH boundary
         * draws the same line for the same reason.
         */
        async function reconcilePersonField(result, op) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.person_id;
            const field = result.field;
            const value = String((op && op.payload && op.payload.value) || '');
            if (!root.prksIsSupportedPersonField || !root.prksIsSupportedPersonField(field)) {
                return false;
            }
            const peopleToken = currentDomainGeneration(DOMAIN_PEOPLE) + 1;
            domainGeneration.set(DOMAIN_PEOPLE, peopleToken);
            const kinds = ['person', 'person-metadata-state'];
            const tokens = kinds.map(function (kind) {
                const token = currentEntityGeneration(kind, id) + 1;
                entityCoherence.set(entityKey(kind, id), token);
                return token;
            });
            const snapshots = await Promise.all(kinds.map(kind => store.getEntity(kind, id)));
            const person = snapshots[0] && snapshots[0].value;
            const state = snapshots[1] && snapshots[1].value;
            if (state) {
                if (typeof root.prksIsPersonMetadataStateShape === 'function' &&
                    !root.prksIsPersonMetadataStateShape(state, id)) return false;
                const entry = state.fields[field];
                if (!entry) return false;
                // An acknowledgement older than what the cache already holds
                // has been superseded; applying it would move the field back.
                if (entry.revision > result.server_revision) return true;
                state.fields[field] = { revision: result.server_revision };
            }
            if (person) person[field] = value;
            const values = [person, state];
            for (let i = 0; i < kinds.length; i++) {
                if (values[i] && !await cacheEntityIfCurrent(kinds[i], id, values[i], tokens[i])) {
                    return false;
                }
            }
            const cached = await store.getList(PEOPLE_LIST_KEY).catch(function () { return null; });
            if (cached) {
                const rows = cached.value;
                if (typeof root.prksIsPeopleIndexShape === 'function' &&
                    Array.isArray(rows) && !root.prksIsPeopleIndexShape(rows)) return false;
                const patched = (Array.isArray(rows) ? rows : []).map(function (row) {
                    if (!row || row.id !== id) return row;
                    const next = Object.assign({}, row);
                    next[field] = value;
                    return next;
                });
                if (!await cacheListForDomain(PEOPLE_LIST_KEY, patched, DOMAIN_PEOPLE, peopleToken)) {
                    return false;
                }
            }
            /* A cached Group detail embeds WHOLE People index rows, so ANY
             * profile field stales it -- this reconciler patches the People
             * index itself, but not the copies of those rows held inside
             * Group snapshots. Ungated for that reason, unlike the rest. */
            prksOfflineMarkPersonGroupsChanged();
            const displayed = root.PRKS_PERSON_DISPLAY_FIELDS || [];
            if (displayed.indexOf(field) !== -1) {
                prksOfflineMarkResearchGraphPeopleChanged();
                prksOfflineMarkArgumentsChanged();
                prksOfflineMarkWorksBrowseChanged();
                prksOfflineMarkRecentChanged();
                prksOfflineMarkRecentlyAddedChanged();
                prksOfflineMarkFoldersChanged();
            }
            return true;
        }

        /**
         * A Person the server has removed.
         *
         * The People index this device holds is PATCHED, and the Person's own
         * snapshot dropped -- there is nothing left to show on it. Group member
         * lists and the group catalogue's counts are staled: a Person appears
         * inside whole People rows embedded in a cached Group detail, and there
         * is no precise patch for a row that is no longer there.
         */
        async function reconcileDeletedPerson(result) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.person_id;
            const cached = await store.getList(PEOPLE_LIST_KEY)
                .catch(function () { return null; });
            const rows = cached && Array.isArray(cached.value) ? cached.value : null;
            if (rows) {
                const token = currentDomainGeneration(DOMAIN_PEOPLE) + 1;
                domainGeneration.set(DOMAIN_PEOPLE, token);
                const remaining = rows.filter(row => row && row.id !== id);
                if (!await cacheListForDomain(PEOPLE_LIST_KEY, remaining,
                    DOMAIN_PEOPLE, token)) return false;
            }
            await invalidateEntity('person', id);
            await invalidateEntity('person-metadata-state', id);
            await invalidateEntity('person-group-memberships', id);
            prksOfflineMarkPersonGroupsChanged();
            /* The name is gone from every read model that merely displayed it.
             * Those rows are keyed by Work, so there is no precise patch -- and
             * a deletable Person is credited on nothing, so in practice this
             * only stales the Graph's people layer. */
            prksOfflineMarkResearchGraphPeopleChanged();
            return true;
        }

        /* ---- Folders ------------------------------------------------------ */

        async function cachedFolderRows() {
            const cached = await store.getList(FOLDERS_LIST_KEY)
                .catch(function () { return null; });
            if (!cached) return null;
            return Array.isArray(cached.value) ? cached.value : null;
        }

        async function writeFolderRows(rows) {
            const token = currentDomainGeneration(DOMAIN_FOLDERS) + 1;
            domainGeneration.set(DOMAIN_FOLDERS, token);
            return cacheListForDomain(FOLDERS_LIST_KEY, rows, DOMAIN_FOLDERS, token);
        }

        /**
         * A Folder the server has accepted.
         *
         * The acknowledgement carries the stored row, so the hierarchy is
         * PATCHED rather than dropped -- the Folder Library is PRKS's home
         * route, and discarding it would land an offline launch on an empty
         * library.
         */
        async function reconcileCreatedFolder(result) {
            if (!store || !await store.isAvailable()) return false;
            const folder = result && result.folder;
            if (!folder || folder.id !== result.folder_id) return false;
            const rows = await cachedFolderRows();
            if (rows) {
                const merged = rows.filter(row => row && row.id !== folder.id).concat([folder]);
                if (!await writeFolderRows(merged)) return false;
            }
            if (result.changed && typeof root.prksNewFolderState === 'function') {
                await patchEntity('folder-state', folder.id,
                    () => root.prksNewFolderState(folder.id));
            }
            return true;
        }

        /** One Folder field the server has applied. */
        async function reconcileFolderField(result, op) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.folder_id;
            const field = result.field;
            if (!root.prksIsSupportedFolderField ||
                !root.prksIsSupportedFolderField(field)) return false;
            const raw = String((op && op.payload && op.payload.value) || '');
            const value = field === 'parent_id' ? (raw || null) : raw;
            await patchEntity('folder-state', id, function (state) {
                if (typeof root.prksIsFolderStateShape === 'function' &&
                    !root.prksIsFolderStateShape(state, id)) return null;
                const entry = state.fields[field];
                if (!entry || entry.revision > result.server_revision) return null;
                const next = Object.assign({}, state,
                    { fields: Object.assign({}, state.fields) });
                next.fields[field] = { revision: result.server_revision };
                return next;
            });
            const rows = await cachedFolderRows();
            if (rows && rows.some(row => row && row.id === id)) {
                const patched = rows.map(function (row) {
                    if (!row || row.id !== id) return row;
                    const next = Object.assign({}, row);
                    next[field] = value;
                    return next;
                });
                if (!await writeFolderRows(patched)) return false;
            }
            if (field === 'parent_id') {
                /* The cached detail carries a `parent` OBJECT this
                 * acknowledgement does not name. */
                await invalidateEntity('folder', id);
            } else {
                await patchEntity('folder', id, function (folder) {
                    if (!folder || folder.id !== id) return null;
                    const next = Object.assign({}, folder);
                    next[field] = value;
                    return next;
                });
            }
            if (field === 'title') {
                /* A cached Work detail embeds `folder_title`, and so does every
                 * card that names a file's folder. The answer NAMES the members
                 * rather than making this device guess, so exactly those Works
                 * are staled and the rest of the cache is untouched. */
                const members = Array.isArray(result.member_work_ids)
                    ? result.member_work_ids : [];
                for (let i = 0; i < members.length; i += 1) {
                    await invalidateEntity('work', members[i]);
                }
                if (members.length) {
                    prksOfflineMarkWorksBrowseChanged();
                    prksOfflineMarkRecentChanged();
                    prksOfflineMarkRecentlyAddedChanged();
                }
            }
            return true;
        }

        /** Which folder a Work is in, as the server now has it. */
        async function reconcileWorkFolder(result, op) {
            if (!store || !await store.isAvailable()) return false;
            const workId = result.work_id;
            await patchEntity('work-folder-state', workId, function (state) {
                if (typeof root.prksIsWorkFolderStateShape === 'function' &&
                    !root.prksIsWorkFolderStateShape(state, workId)) return null;
                if (state.revision > result.server_revision) return null;
                return Object.assign({}, state, { folder_id: result.folder_id,
                    revision: result.server_revision });
            });
            /* The Work's own snapshot and its cards carry the folder's TITLE,
             * and the acknowledgement states it exactly -- so they are patched
             * rather than dropped. */
            await patchEntity('work', workId, function (work) {
                if (!work || work.id !== workId) return null;
                return Object.assign({}, work, {
                    folder_id: result.folder_id || null,
                    folder_title: result.folder_title || '',
                });
            });
            if (!result.changed) return true;
            /* Both folders' contents changed, and this device does not know
             * which folder the Work left -- the answer names only where it
             * landed -- so the hierarchy's counts are what go stale.
             *
             * Among the browse projections ONLY Recently added carries
             * `folder_id`: it filters locally over the folder title, while
             * Progress and File types never render a folder at all. Staling
             * those two would cost the user their offline browse pages for a
             * value neither of them shows. */
            prksOfflineMarkFoldersChanged();
            prksOfflineMarkRecentlyAddedChanged();
            return true;
        }

        /** A Folder the server has removed. It was empty, so nothing moved. */
        async function reconcileDeletedFolder(result) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.folder_id;
            const rows = await cachedFolderRows();
            if (rows) {
                if (!await writeFolderRows(rows.filter(row => row && row.id !== id))) {
                    return false;
                }
            }
            await invalidateEntity('folder', id);
            await invalidateEntity('folder-state', id);
            return true;
        }

        /* ---- Positions ---------------------------------------------------- */

        async function cachedPositionRows() {
            const cached = await store.getList(POSITIONS_LIST_KEY)
                .catch(function () { return null; });
            return cached && Array.isArray(cached.value) ? cached.value : null;
        }

        async function writePositionRows(rows) {
            const token = currentDomainGeneration(DOMAIN_POSITIONS) + 1;
            domainGeneration.set(DOMAIN_POSITIONS, token);
            return cacheListForDomain(POSITIONS_LIST_KEY, rows, DOMAIN_POSITIONS, token);
        }

        /* A Position node already IN the cached Graph snapshot, patched in
         * place. `record_id` is the Position's own id -- a node's `id` is the
         * namespaced `position:<id>`, because the projection holds several
         * record types in one list. Nothing is ever synthesized: a Position the
         * snapshot does not contain is one the server did not put there. */
        async function patchGraphPositionLabel(positionId, name) {
            const kinds = ['research-graph-core', 'research-graph-people'];
            for (let i = 0; i < kinds.length; i += 1) {
                await patchEntity(kinds[i], 'snapshot', function (snapshot) {
                    if (!snapshot || !Array.isArray(snapshot.nodes)) return null;
                    let changed = false;
                    const nodes = snapshot.nodes.map(function (node) {
                        if (!node || node.type !== 'position') return node;
                        if (node.record_id !== positionId) return node;
                        if (node.label === name) return node;
                        changed = true;
                        return Object.assign({}, node, { label: name });
                    });
                    return changed ? Object.assign({}, snapshot, { nodes: nodes }) : null;
                });
            }
        }

        /** A Position the server has accepted. */
        async function reconcileCreatedPosition(result) {
            if (!store || !await store.isAvailable()) return false;
            const position = result && result.position;
            if (!position || position.id !== result.position_id) return false;
            const rows = await cachedPositionRows();
            if (rows) {
                const merged = rows.filter(row => row && row.id !== position.id)
                    .concat([position]);
                merged.sort(function (a, b) {
                    return String(a.name || '').localeCompare(String(b.name || ''),
                        undefined, { sensitivity: 'base' });
                });
                if (!await writePositionRows(merged)) return false;
            }
            if (result.changed && typeof root.prksNewPositionState === 'function') {
                await patchEntity('position-state', position.id,
                    () => root.prksNewPositionState(position.id));
            }
            /* A brand-new Position is in NO cached Graph snapshot -- the server
             * computed those before it existed -- and must not be invented into
             * one. The snapshots go stale instead. */
            if (result.changed) prksOfflineMarkResearchGraphCoreChanged();
            return true;
        }

        /** One Position field the server has applied. */
        async function reconcilePositionField(result, op) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.position_id;
            const field = result.field;
            if (!root.prksIsSupportedPositionField ||
                !root.prksIsSupportedPositionField(field)) return false;
            const value = String((op && op.payload && op.payload.value) || '');
            await patchEntity('position-state', id, function (state) {
                if (typeof root.prksIsPositionStateShape === 'function' &&
                    !root.prksIsPositionStateShape(state, id)) return null;
                const entry = state.fields[field];
                if (!entry || entry.revision > result.server_revision) return null;
                const next = Object.assign({}, state,
                    { fields: Object.assign({}, state.fields) });
                next.fields[field] = { revision: result.server_revision };
                return next;
            });
            const rows = await cachedPositionRows();
            if (rows && rows.some(row => row && row.id === id)) {
                const patched = rows.map(function (row) {
                    if (!row || row.id !== id) return row;
                    const next = Object.assign({}, row);
                    next[field] = value;
                    return next;
                });
                patched.sort(function (a, b) {
                    return String(a.name || '').localeCompare(String(b.name || ''),
                        undefined, { sensitivity: 'base' });
                });
                if (!await writePositionRows(patched)) return false;
            }
            await patchEntity('position', id, function (position) {
                if (!position || position.id !== id) return null;
                const next = Object.assign({}, position);
                next[field] = value;
                return next;
            });
            if (field !== 'name' || !result.changed) return true;
            /* A cached ARGUMENT embeds the name of every Position it targets,
             * and this device cannot patch those without knowing which
             * Arguments point here -- the Position detail does not say. So the
             * Arguments domain is fenced, exactly as the ordinary rename has
             * always done. A description edit reaches none of that. */
            prksOfflineMarkArgumentsChanged();
            /* The Graph node carries the LABEL, which the acknowledgement's own
             * operation states exactly, so it is corrected in place. */
            await patchGraphPositionLabel(id, value);
            return true;
        }

        /** A Position the server has removed. */
        async function reconcileDeletedPosition(result) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.position_id;
            const rows = await cachedPositionRows();
            if (rows) {
                if (!await writePositionRows(rows.filter(row => row && row.id !== id))) {
                    return false;
                }
            }
            await invalidateEntity('position', id);
            await invalidateEntity('position-state', id);
            /* The server refuses a Position an Argument targets, so a deletion
             * that got this far was targeted by none -- but a cached Argument
             * list may still have been built when it existed. */
            prksOfflineMarkArgumentsChanged();
            prksOfflineMarkResearchGraphCoreChanged();
            return true;
        }

        /* ---- Arguments and Stances ---------------------------------------- */

        /* An Argument node already IN the cached Graph snapshot, patched in
         * place. `record_id` is the Argument's own id -- a node's `id` is the
         * namespaced `argument:<id>`. Stances are `argument` nodes too, so a
         * kind change moves nothing here. Nothing is ever synthesized. */
        async function patchGraphArgumentLabel(argumentId, name) {
            const kinds = ['research-graph-core', 'research-graph-people'];
            for (let i = 0; i < kinds.length; i += 1) {
                await patchEntity(kinds[i], 'snapshot', function (snapshot) {
                    if (!snapshot || !Array.isArray(snapshot.nodes)) return null;
                    let changed = false;
                    const nodes = snapshot.nodes.map(function (node) {
                        if (!node || node.type !== 'argument') return node;
                        if (node.record_id !== argumentId) return node;
                        if (node.label === name) return node;
                        changed = true;
                        return Object.assign({}, node, { label: name });
                    });
                    return changed ? Object.assign({}, snapshot, { nodes: nodes }) : null;
                });
            }
        }

        async function patchArgumentStateRevision(argumentId, mutate) {
            await patchEntity('argument-state', argumentId, function (state) {
                if (typeof root.prksIsArgumentStateShape === 'function' &&
                    !root.prksIsArgumentStateShape(state, argumentId)) return null;
                return mutate(state);
            });
        }

        /**
         * An Argument the server has accepted.
         *
         * The cached index is FENCED rather than patched. An index row carries
         * the names of everything the Argument targets, the titles and authors
         * of the Works it cites, and a response count -- all derived from rows
         * this device may never have seen, so a synthesized row would be a
         * guess presented as a fact. The Positions domain goes with it because
         * a Position detail lists the Arguments answering it.
         */
        async function reconcileCreatedArgument(result) {
            if (!store || !await store.isAvailable()) return false;
            if (!result || typeof result.argument_id !== 'string') return false;
            if (!result.changed) return true;
            prksOfflineMarkArgumentsChanged();
            prksOfflineMarkPositionsChanged();
            /* A brand-new Argument is in NO cached Graph snapshot -- the server
             * computed those before it existed -- and must not be invented into
             * one. The snapshots go stale instead. */
            prksOfflineMarkResearchGraphCoreChanged();
            return true;
        }

        /** One Argument field the server has applied. */
        async function reconcileArgumentField(result, op) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.argument_id;
            const field = result.field;
            if (!root.prksIsSupportedArgumentField ||
                !root.prksIsSupportedArgumentField(field)) return false;
            const value = String((op && op.payload && op.payload.value) || '');
            await patchArgumentStateRevision(id, function (state) {
                const entry = state.fields[field];
                if (!entry || entry.revision > result.server_revision) return null;
                const next = Object.assign({}, state,
                    { fields: Object.assign({}, state.fields) });
                next.fields[field] = { revision: result.server_revision };
                return next;
            });
            await patchEntity('argument', id, function (argument) {
                if (!argument || argument.id !== id) return null;
                const next = Object.assign({}, argument);
                next[field] = value;
                return next;
            });
            if (!result.changed) return true;
            if (field === 'main_text') {
                /* The body is shown on the Argument's own page and nowhere
                 * else: no index row carries it, and no other record embeds
                 * it. Patching the detail above is the whole consequence. */
                prksOfflineMarkResearchGraphCoreChanged();
                return true;
            }
            /* A name or a kind, on the other hand, is embedded by every record
             * that points here -- other Arguments' target and response lists,
             * Position details, note mentions -- and this device cannot
             * enumerate those, because nothing it holds says who points at
             * this Argument. So the two domains are fenced, exactly as the
             * ordinary edit has always done. */
            prksOfflineMarkArgumentsChanged();
            prksOfflineMarkPositionsChanged();
            if (field === 'name') {
                /* The Graph node carries the LABEL, which the acknowledgement's
                 * own operation states exactly, so it is corrected in place. */
                await patchGraphArgumentLabel(id, value);
                prksOfflineMarkResearchGraphPeopleChanged();
            } else {
                prksOfflineMarkResearchGraphCoreChanged();
            }
            return true;
        }

        /** The whole citation list the server has applied. */
        async function reconcileArgumentSources(result, op) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.argument_id;
            await patchArgumentStateRevision(id, function (state) {
                const entry = state.sources;
                if (!entry || entry.revision > result.server_revision) return null;
                return Object.assign({}, state,
                    { sources: { revision: result.server_revision } });
            });
            if (!result.changed) return true;
            /* A cached source row carries the Work's title and its authors,
             * which this operation names by id alone -- so the rows cannot be
             * rebuilt here, and the domain is fenced. Positions are untouched:
             * which Works an Argument cites changes nothing a Position shows,
             * which is why the ordinary endpoint does not stale them either. */
            prksOfflineMarkArgumentsChanged();
            prksOfflineMarkResearchGraphCoreChanged();
            return true;
        }

        /** The whole target list the server has applied. */
        async function reconcileArgumentTargets(result, op) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.argument_id;
            await patchArgumentStateRevision(id, function (state) {
                const entry = state.targets;
                if (!entry || entry.revision > result.server_revision) return null;
                return Object.assign({}, state,
                    { targets: { revision: result.server_revision } });
            });
            if (!result.changed) return true;
            /* Targets are the one thing that changes what a POSITION shows --
             * its list of answering Arguments is this relationship read from
             * the other end -- and the Graph's edges are this list too. */
            prksOfflineMarkArgumentsChanged();
            prksOfflineMarkPositionsChanged();
            prksOfflineMarkResearchGraphCoreChanged();
            return true;
        }

        /** An Argument the server has removed. */
        async function reconcileDeletedArgument(result) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.argument_id;
            await invalidateEntity('argument', id);
            await invalidateEntity('argument-state', id);
            prksOfflineMarkArgumentsChanged();
            prksOfflineMarkPositionsChanged();
            prksOfflineMarkResearchGraphCoreChanged();
            return true;
        }

        /* ---- Concepts ----------------------------------------------------- */

        async function cachedConceptRows() {
            const cached = await store.getList(CONCEPTS_LIST_KEY)
                .catch(function () { return null; });
            return cached && Array.isArray(cached.value) ? cached.value : null;
        }

        async function writeConceptRows(rows) {
            const token = currentDomainGeneration(DOMAIN_CONCEPTS) + 1;
            domainGeneration.set(DOMAIN_CONCEPTS, token);
            return cacheListForDomain(CONCEPTS_LIST_KEY, rows, DOMAIN_CONCEPTS, token);
        }

        /* A Concept node already IN the cached Graph snapshot, patched in
         * place. Nothing is ever synthesized: a Concept the snapshot does not
         * contain is a Concept the server did not put there, and inventing a
         * node from a domain cache would show a graph nobody computed. */
        async function patchGraphConceptLabel(conceptId, name) {
            const kinds = ['research-graph-core', 'research-graph-people'];
            for (let i = 0; i < kinds.length; i += 1) {
                await patchEntity(kinds[i], 'snapshot', function (snapshot) {
                    if (!snapshot || !Array.isArray(snapshot.nodes)) return null;
                    let changed = false;
                    const nodes = snapshot.nodes.map(function (node) {
                        /* `record_id` is the Concept's own id. A node's `id` is
                         * the NAMESPACED `concept:<id>`, because the projection
                         * holds several record types in one list -- matching on
                         * it would silently patch nothing. */
                        if (!node || node.type !== 'concept') return node;
                        if (node.record_id !== conceptId) return node;
                        if (node.label === name) return node;
                        changed = true;
                        return Object.assign({}, node, { label: name });
                    });
                    return changed ? Object.assign({}, snapshot, { nodes: nodes }) : null;
                });
            }
        }

        /** A Concept the server has accepted. */
        async function reconcileCreatedConcept(result) {
            if (!store || !await store.isAvailable()) return false;
            const concept = result && result.concept;
            if (!concept || concept.id !== result.concept_id) return false;
            const rows = await cachedConceptRows();
            if (rows) {
                const merged = rows.filter(row => row && row.id !== concept.id)
                    .concat([Object.assign({ aliases: [] }, concept)]);
                merged.sort(function (a, b) {
                    return String(a.name || '').localeCompare(String(b.name || ''),
                        undefined, { sensitivity: 'base' });
                });
                if (!await writeConceptRows(merged)) return false;
            }
            if (result.changed && typeof root.prksNewConceptState === 'function') {
                await patchEntity('concept-state', concept.id,
                    () => root.prksNewConceptState(concept.id, concept));
            }
            /* A brand-new Concept is in NO cached Graph snapshot -- the server
             * computed those before it existed -- and must not be invented
             * into one. The snapshots go stale instead, which is what every
             * Concept creation has always done. */
            if (result.changed) prksOfflineMarkResearchGraphCoreChanged();
            return true;
        }

        /** One Concept field the server has applied. */
        async function reconcileConceptField(result, op) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.concept_id;
            const field = result.field;
            if (!root.prksIsSupportedConceptField ||
                !root.prksIsSupportedConceptField(field)) return false;
            const value = String((op && op.payload && op.payload.value) || '');
            await patchEntity('concept-state', id, function (state) {
                if (typeof root.prksIsConceptStateShape === 'function' &&
                    !root.prksIsConceptStateShape(state, id)) return null;
                const entry = state.fields[field];
                if (!entry || entry.revision > result.server_revision) return null;
                const next = Object.assign({}, state,
                    { fields: Object.assign({}, state.fields) });
                next.fields[field] = { revision: result.server_revision };
                return next;
            });
            const rows = await cachedConceptRows();
            if (rows && rows.some(row => row && row.id === id)) {
                const patched = rows.map(function (row) {
                    if (!row || row.id !== id) return row;
                    const next = Object.assign({}, row);
                    next[field] = value;
                    return next;
                });
                if (!await writeConceptRows(patched)) return false;
            }
            await patchEntity('concept', id, function (concept) {
                if (!concept || concept.id !== id) return null;
                const next = Object.assign({}, concept);
                next[field] = value;
                return next;
            });
            /* A definition is not a Graph label and not a note reference, so no
             * projection outside the Concept domain shows it. */
            return true;
        }

        /** A Concept's name and alias set, as the server now has them. */
        async function reconcileConceptIdentity(result, op) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.concept_id;
            const name = result.name;
            const aliases = Array.isArray(result.aliases) ? result.aliases.slice() : [];
            await patchEntity('concept-state', id, function (state) {
                if (typeof root.prksIsConceptStateShape === 'function' &&
                    !root.prksIsConceptStateShape(state, id)) return null;
                if (state.identity_revision > result.server_revision) return null;
                return Object.assign({}, state, {
                    identity: { name: name, aliases: aliases },
                    identity_revision: result.server_revision,
                });
            });
            /* The answer states the stored name and the stored alias set
             * exactly, so both the catalogue and the detail are PATCHED. */
            const rows = await cachedConceptRows();
            if (rows && rows.some(row => row && row.id === id)) {
                const patched = rows.map(function (row) {
                    if (!row || row.id !== id) return row;
                    return Object.assign({}, row, { name: name, aliases: aliases.slice() });
                });
                patched.sort(function (a, b) {
                    return String(a.name || '').localeCompare(String(b.name || ''),
                        undefined, { sensitivity: 'base' });
                });
                if (!await writeConceptRows(patched)) return false;
            }
            await patchEntity('concept', id, function (concept) {
                if (!concept || concept.id !== id) return null;
                return Object.assign({}, concept, { name: name, aliases: aliases.slice() });
            });
            if (!result.changed) return true;
            /* Every OTHER Concept's cached detail may name this one as a parent
             * or a child, and this device cannot know which without reading
             * them all -- so the domain's entities go stale while the
             * catalogue, whose exact new row is known, is kept. */
            await invalidateConceptNeighbours(id);
            /* A Graph node carries the LABEL, which the answer states, so the
             * node is corrected in place rather than the snapshot dropped. An
             * alias is not a Graph label and moves nothing there. */
            await patchGraphConceptLabel(id, name);
            return true;
        }

        /** Cached Concept details other than `id` that may embed its name. */
        async function invalidateConceptNeighbours(conceptId) {
            const rows = await cachedConceptRows();
            if (!rows) return;
            for (let i = 0; i < rows.length; i += 1) {
                const other = rows[i] && rows[i].id;
                if (other && other !== conceptId) await invalidateEntity('concept', other);
            }
        }

        /** A Concept's parent set, as the server now has it. */
        async function reconcileConceptParents(result, op) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.concept_id;
            const parentIds = Array.isArray(result.parent_ids) ? result.parent_ids.slice() : [];
            await patchEntity('concept-state', id, function (state) {
                if (typeof root.prksIsConceptStateShape === 'function' &&
                    !root.prksIsConceptStateShape(state, id)) return null;
                if (state.parents_revision > result.server_revision) return null;
                return Object.assign({}, state, {
                    parent_ids: parentIds, parents_revision: result.server_revision,
                });
            });
            if (!result.changed) return true;
            /* An edge moves BOTH ends and the answer names only the ids, not
             * the names a rendered hierarchy needs -- so the Concept details
             * are dropped rather than half-patched. The catalogue carries no
             * hierarchy and is deliberately kept: an offline device must not
             * lose its Concept list to a reparent. */
            await invalidateEntity('concept', id);
            for (let i = 0; i < parentIds.length; i += 1) {
                await invalidateEntity('concept', parentIds[i]);
            }
            const previous = Array.isArray(op && op.local_context && op.local_context.previous)
                ? op.local_context.previous : [];
            for (let i = 0; i < previous.length; i += 1) {
                if (parentIds.indexOf(previous[i]) === -1) {
                    await invalidateEntity('concept', previous[i]);
                }
            }
            /* The hierarchy IS Graph structure, and edges are not something a
             * client may compute -- the projection has its own rules. */
            prksOfflineMarkResearchGraphCoreChanged();
            return true;
        }

        /** A Concept the server has removed. */
        async function reconcileDeletedConcept(result) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.concept_id;
            const rows = await cachedConceptRows();
            if (rows) {
                if (!await writeConceptRows(rows.filter(row => row && row.id !== id))) {
                    return false;
                }
            }
            await invalidateEntity('concept', id);
            await invalidateEntity('concept-state', id);
            /* Its edges went with it, and every other cached Concept detail may
             * have named it. */
            await invalidateConceptNeighbours(id);
            prksOfflineMarkResearchGraphCoreChanged();
            return true;
        }

        /* ---- Playlists --------------------------------------------------- */

        async function cachedPlaylistRows() {
            const cached = await store.getList(PLAYLISTS_LIST_KEY)
                .catch(function () { return null; });
            return cached && Array.isArray(cached.value) ? cached.value : null;
        }

        async function writePlaylistRows(rows) {
            const token = currentDomainGeneration(DOMAIN_PLAYLISTS) + 1;
            domainGeneration.set(DOMAIN_PLAYLISTS, token);
            return cacheListForDomain(PLAYLISTS_LIST_KEY, rows, DOMAIN_PLAYLISTS, token);
        }

        /**
         * A Playlist the server has accepted.
         *
         * The acknowledgement carries the stored row, so the catalogue is
         * PATCHED rather than dropped -- discarding it would leave an offline
         * device with no playlist index at all.
         */
        async function reconcileCreatedPlaylist(result) {
            if (!store || !await store.isAvailable()) return false;
            const playlist = result && result.playlist;
            if (!playlist || playlist.id !== result.playlist_id) return false;
            const rows = await cachedPlaylistRows();
            if (rows) {
                /* Newest first, as `get_all_playlists` orders by updated_at. */
                const merged = [playlist].concat(
                    rows.filter(row => row && row.id !== playlist.id));
                if (!await writePlaylistRows(merged)) return false;
            }
            if (result.changed && typeof root.prksNewPlaylistState === 'function') {
                await patchEntity('playlist-state', playlist.id,
                    () => root.prksNewPlaylistState(playlist.id));
            }
            return true;
        }

        /** One Playlist field the server has applied. */
        async function reconcilePlaylistField(result, op) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.playlist_id;
            const field = result.field;
            if (!root.prksIsSupportedPlaylistField ||
                !root.prksIsSupportedPlaylistField(field)) return false;
            const raw = String((op && op.payload && op.payload.value) || '');
            const value = field === 'original_url' ? (raw || null) : raw;
            await patchEntity('playlist-state', id, function (state) {
                if (typeof root.prksIsPlaylistStateShape === 'function' &&
                    !root.prksIsPlaylistStateShape(state, id)) return null;
                const entry = state.fields[field];
                if (!entry || entry.revision > result.server_revision) return null;
                const next = Object.assign({}, state,
                    { fields: Object.assign({}, state.fields) });
                next.fields[field] = { revision: result.server_revision };
                return next;
            });
            const rows = await cachedPlaylistRows();
            if (rows && rows.some(row => row && row.id === id)) {
                const patched = rows.map(function (row) {
                    if (!row || row.id !== id) return row;
                    const next = Object.assign({}, row);
                    next[field] = value;
                    return next;
                });
                if (!await writePlaylistRows(patched)) return false;
            }
            await patchEntity('playlist', id, function (playlist) {
                if (!playlist || playlist.id !== id) return null;
                const next = Object.assign({}, playlist);
                next[field] = value;
                return next;
            });
            if (field === 'title' && result.changed) {
                /* `get_work()` embeds `playlist_title`, so renaming a playlist
                 * stales the cached Work of every video in it. Unlike the
                 * Folder family the acknowledgement does not name the members
                 * -- the detail this device holds does, and when it holds none
                 * there is nothing cached to be wrong. */
                const cached = await store.getEntity('playlist', id)
                    .catch(function () { return null; });
                const items = cached && cached.value && Array.isArray(cached.value.items)
                    ? cached.value.items : [];
                for (let i = 0; i < items.length; i += 1) {
                    const workId = items[i] && items[i].id;
                    if (workId) await invalidateEntity('work', workId);
                }
                if (items.length) {
                    prksOfflineMarkWorksBrowseChanged();
                    prksOfflineMarkRecentChanged();
                    prksOfflineMarkRecentlyAddedChanged();
                }
            }
            return true;
        }

        /** Which playlist a Work is in, as the server now has it. */
        async function reconcileWorkPlaylist(result, op) {
            if (!store || !await store.isAvailable()) return false;
            const workId = result.work_id;
            await patchEntity('work-playlist-state', workId, function (state) {
                if (typeof root.prksIsWorkPlaylistStateShape === 'function' &&
                    !root.prksIsWorkPlaylistStateShape(state, workId)) return null;
                if (state.revision > result.server_revision) return null;
                return Object.assign({}, state, { playlist_id: result.playlist_id,
                    revision: result.server_revision });
            });
            /* The Work's own snapshot carries the playlist's TITLE, and the
             * acknowledgement states it exactly -- so it is patched rather than
             * dropped. */
            await patchEntity('work', workId, function (work) {
                if (!work || work.id !== workId) return null;
                return Object.assign({}, work, {
                    playlist_id: result.playlist_id || null,
                    playlist_title: result.playlist_title || null,
                });
            });
            if (!result.changed) return true;
            /* Both playlists' contents and order changed, and this device does
             * not know which playlist the video left -- the answer names only
             * where it landed. Their cached details and the index go stale
             * together, which is what the Playlists domain covers. */
            prksOfflineMarkPlaylistsChanged();
            return true;
        }

        /** A Playlist's order, as the server now has it. */
        async function reconcilePlaylistOrder(result, op) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.playlist_id;
            await patchEntity('playlist-state', id, function (state) {
                if (typeof root.prksIsPlaylistStateShape === 'function' &&
                    !root.prksIsPlaylistStateShape(state, id)) return null;
                if (state.order_revision > result.server_revision) return null;
                return Object.assign({}, state, { order_revision: result.server_revision });
            });
            /* The answer names the order the server ENDED with, resolved
             * against what it actually holds -- so the cached detail is patched
             * into that order rather than dropped, and an offline device keeps
             * its playlist page. */
            const order = Array.isArray(result.work_ids) ? result.work_ids : null;
            if (order && typeof root.prksApplyPlaylistOrder === 'function') {
                await patchEntity('playlist', id, function (playlist) {
                    if (!playlist || playlist.id !== id ||
                        !Array.isArray(playlist.items)) return null;
                    return Object.assign({}, playlist,
                        { items: root.prksApplyPlaylistOrder(playlist.items, order) });
                });
            } else if (result.changed) {
                await invalidateEntity('playlist', id);
            }
            /* Reordering bumps `updated_at`, which reorders the index -- and no
             * Work snapshot carries a position, so nothing else is stale. The
             * INDEX alone is fenced: the playlist's own snapshot was patched
             * just above, and dropping it would cost an offline device the page
             * it had just reordered. */
            if (result.changed) {
                if (order) prksOfflineMarkPlaylistsIndexChanged();
                else prksOfflineMarkPlaylistsChanged();
            }
            return true;
        }

        /**
         * A Playlist the server has removed.
         *
         * Its memberships went with it, so every video it held loses its
         * `playlist_title` -- and this device knows exactly which ones from the
         * detail it cached.
         */
        async function reconcileDeletedPlaylist(result) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.playlist_id;
            const cached = await store.getEntity('playlist', id)
                .catch(function () { return null; });
            const items = cached && cached.value && Array.isArray(cached.value.items)
                ? cached.value.items : [];
            for (let i = 0; i < items.length; i += 1) {
                const workId = items[i] && items[i].id;
                if (!workId) continue;
                await patchEntity('work', workId, function (work) {
                    if (!work || work.id !== workId || work.playlist_id !== id) return null;
                    return Object.assign({}, work,
                        { playlist_id: null, playlist_title: null });
                });
                await invalidateEntity('work-playlist-state', workId);
            }
            const rows = await cachedPlaylistRows();
            if (rows) {
                if (!await writePlaylistRows(rows.filter(row => row && row.id !== id))) {
                    return false;
                }
            }
            await invalidateEntity('playlist', id);
            await invalidateEntity('playlist-state', id);
            return true;
        }

        /* ---- the Tag vocabulary ------------------------------------------ */

        /**
         * A Tag the server has accepted.
         *
         * The acknowledgement carries the stored row, so the catalogue is
         * PATCHED rather than dropped: discarding it would leave an offline
         * device with no Tag list, and the picker unable to offer anything.
         */
        async function reconcileCreatedTag(result) {
            if (!store || !await store.isAvailable()) return false;
            const tag = result && result.tag;
            if (!tag || tag.id !== result.tag_id) return false;
            const cached = await store.getList(TAGS_LIST_KEY).catch(function () { return null; });
            if (cached) {
                const rows = Array.isArray(cached.value) ? cached.value : [];
                const token = currentDomainGeneration(DOMAIN_TAGS) + 1;
                domainGeneration.set(DOMAIN_TAGS, token);
                const merged = rows.filter(row => row && row.id !== tag.id)
                    .concat([Object.assign({ aliases: [] }, tag)]);
                if (!await cacheListForDomain(TAGS_LIST_KEY, merged, DOMAIN_TAGS, token)) {
                    return false;
                }
            }
            return true;
        }

        /**
         * A Tag the server has destroyed.
         *
         * Its relationships went with it, so every Work or Folder that carried
         * it has a stale chip and a stale tag-options snapshot. The
         * acknowledgement names exactly those entities, so only they are
         * staled -- the rest of the cache is untouched.
         */
        async function reconcileDeletedTag(result) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.tag_id;
            const cached = await store.getList(TAGS_LIST_KEY).catch(function () { return null; });
            if (cached) {
                const rows = Array.isArray(cached.value) ? cached.value : [];
                const token = currentDomainGeneration(DOMAIN_TAGS) + 1;
                domainGeneration.set(DOMAIN_TAGS, token);
                if (!await cacheListForDomain(TAGS_LIST_KEY,
                    rows.filter(row => row && row.id !== id), DOMAIN_TAGS, token)) return false;
            }
            const works = Array.isArray(result.affected_work_ids)
                ? result.affected_work_ids : [];
            const folders = Array.isArray(result.affected_folder_ids)
                ? result.affected_folder_ids : [];
            const optionWorks = Array.isArray(result.affected_tag_options_work_ids)
                ? result.affected_tag_options_work_ids : works;
            const optionFolders = Array.isArray(result.affected_tag_options_folder_ids)
                ? result.affected_tag_options_folder_ids : folders;
            for (let i = 0; i < works.length; i += 1) {
                await invalidateEntity('work', works[i]);
            }
            for (let i = 0; i < optionWorks.length; i += 1) {
                await invalidateEntity('work-tag-options', optionWorks[i]);
            }
            for (let i = 0; i < folders.length; i += 1) {
                await invalidateEntity('folder', folders[i]);
            }
            for (let i = 0; i < optionFolders.length; i += 1) {
                await invalidateEntity('folder-tag-options', optionFolders[i]);
            }
            if (works.length) prksOfflineMarkWorksBrowseChanged();
            if (folders.length) prksOfflineMarkFoldersChanged();
            return true;
        }

        /**
         * A Tag the server has merged into another.
         *
         * The source identity is gone; its name becomes an alias of the
         * canonical target when the names differ. Relationship chips on every
         * named Work/Folder are staled exactly as delete does -- the answer
         * lists them -- and tag-options projections that held the source scope
         * go with them.
         */
        async function reconcileMergedTag(result) {
            if (!store || !await store.isAvailable()) return false;
            const sourceId = result.tag_id;
            const targetId = result.canonical_tag_id;
            if (!sourceId || !targetId) return false;
            const cached = await store.getList(TAGS_LIST_KEY).catch(function () { return null; });
            if (cached) {
                const rows = Array.isArray(cached.value) ? cached.value : [];
                const token = currentDomainGeneration(DOMAIN_TAGS) + 1;
                domainGeneration.set(DOMAIN_TAGS, token);
                let sourceName = null;
                const withoutSource = [];
                for (let i = 0; i < rows.length; i += 1) {
                    const row = rows[i];
                    if (!row) continue;
                    if (row.id === sourceId) {
                        sourceName = row.name;
                        continue;
                    }
                    withoutSource.push(row);
                }
                const next = withoutSource.map(function (row) {
                    if (row.id !== targetId || !sourceName) return row;
                    const aliases = Array.isArray(row.aliases) ? row.aliases.slice() : [];
                    const lower = String(sourceName).toLowerCase();
                    if (String(row.name || '').toLowerCase() === lower) return row;
                    if (aliases.some(a => String(a || '').toLowerCase() === lower)) return row;
                    return Object.assign({}, row, { aliases: aliases.concat([sourceName]) });
                });
                if (!await cacheListForDomain(TAGS_LIST_KEY, next, DOMAIN_TAGS, token)) {
                    return false;
                }
            }
            const works = Array.isArray(result.affected_work_ids)
                ? result.affected_work_ids : [];
            const folders = Array.isArray(result.affected_folder_ids)
                ? result.affected_folder_ids : [];
            const optionWorks = Array.isArray(result.affected_tag_options_work_ids)
                ? result.affected_tag_options_work_ids : works;
            const optionFolders = Array.isArray(result.affected_tag_options_folder_ids)
                ? result.affected_tag_options_folder_ids : folders;
            for (let i = 0; i < works.length; i += 1) {
                await invalidateEntity('work', works[i]);
            }
            for (let i = 0; i < optionWorks.length; i += 1) {
                await invalidateEntity('work-tag-options', optionWorks[i]);
            }
            for (let i = 0; i < folders.length; i += 1) {
                await invalidateEntity('folder', folders[i]);
            }
            for (let i = 0; i < optionFolders.length; i += 1) {
                await invalidateEntity('folder-tag-options', optionFolders[i]);
            }
            if (works.length) prksOfflineMarkWorksBrowseChanged();
            if (folders.length) prksOfflineMarkFoldersChanged();
            return true;
        }

        /**
         * A Work the server has destroyed.
         *
         * Cascades already removed relationships; every projection that could
         * have shown this Work is staled the same way the online DELETE path
         * published coherence — Concepts, Arguments, People, Groups, Folders,
         * Playlists, browse catalogs and Research Graph core.
         */
        async function reconcileDeletedWork(result) {
            if (!store || !await store.isAvailable()) return false;
            const id = result && result.work_id;
            if (!id) return false;
            await invalidateEntity('work', id);
            await invalidateEntity('work-metadata-state', id);
            await invalidateEntity('work-source-state', id);
            await invalidateEntity('work-tag-options', id);
            await invalidateEntity('work-people-state', id);
            await invalidateEntity('work-folder-state', id);
            await invalidateEntity('work-playlist-state', id);
            await invalidateEntity('work-notes-state', id);
            if (typeof prksMarkResearchGraphCoreChanged === 'function') {
                prksMarkResearchGraphCoreChanged();
            }
            if (typeof prksOfflineMarkConceptsChanged === 'function') {
                prksOfflineMarkConceptsChanged();
            }
            if (typeof prksOfflineMarkArgumentsChanged === 'function') {
                prksOfflineMarkArgumentsChanged();
            }
            if (typeof prksOfflineMarkPeopleChanged === 'function') {
                prksOfflineMarkPeopleChanged();
            }
            if (typeof prksOfflineMarkPersonGroupsChanged === 'function') {
                prksOfflineMarkPersonGroupsChanged();
            }
            if (typeof prksMarkFoldersDomainChanged === 'function') {
                prksMarkFoldersDomainChanged();
            }
            if (typeof prksMarkWorkBrowseDisplayChanged === 'function') {
                prksMarkWorkBrowseDisplayChanged();
            }
            if (typeof prksOfflineMarkPlaylistsChanged === 'function') {
                prksOfflineMarkPlaylistsChanged();
            }
            return true;
        }

        /**
         * A Work this device constructed that the server has accepted.
         *
         * Mirrors the online create coherence publish: folders always, browse
         * + recently-added always, playlists when construction carried a
         * playlist, people / person-groups when it carried roles. Recent is
         * deliberately untouched (last_opened_at is still NULL).
         */
        async function reconcileCreatedWork(result) {
            if (!result || !result.work_id) return false;
            if (typeof prksMarkWorksBrowseChanged === 'function') {
                prksMarkWorksBrowseChanged();
            }
            if (typeof prksMarkRecentlyAddedChanged === 'function') {
                prksMarkRecentlyAddedChanged();
            }
            if (typeof prksMarkFoldersDomainChanged === 'function') {
                prksMarkFoldersDomainChanged();
            }
            if (result.playlist_id && typeof prksOfflineMarkPlaylistsChanged === 'function') {
                prksOfflineMarkPlaylistsChanged();
            }
            if (result.role_count > 0) {
                if (typeof prksOfflineMarkPeopleChanged === 'function') {
                    prksOfflineMarkPeopleChanged();
                }
                if (typeof prksOfflineMarkPersonGroupsChanged === 'function') {
                    prksOfflineMarkPersonGroupsChanged();
                }
            }
            return true;
        }

        /* ---- Person Groups ---------------------------------------------- */

        /** The cached Group catalogue, or null when this device holds none. */
        async function cachedGroupRows() {
            const cached = await store.getList(PERSON_GROUPS_LIST_KEY)
                .catch(function () { return null; });
            if (!cached) return null;
            return Array.isArray(cached.value) ? cached.value : null;
        }

        async function writeGroupRows(rows) {
            const token = currentDomainGeneration(DOMAIN_PERSON_GROUPS) + 1;
            domainGeneration.set(DOMAIN_PERSON_GROUPS, token);
            return cacheListForDomain(PERSON_GROUPS_LIST_KEY, rows,
                DOMAIN_PERSON_GROUPS, token);
        }

        async function patchEntity(kind, id, mutate) {
            const token = currentEntityGeneration(kind, id) + 1;
            entityCoherence.set(entityKey(kind, id), token);
            const snapshot = await store.getEntity(kind, id).catch(function () { return null; });
            const value = snapshot && snapshot.value;
            if (!value) return true;
            const next = mutate(value);
            if (next === null) return true;
            return cacheEntityIfCurrent(kind, id, next, token);
        }

        /**
         * A Group the server has accepted.
         *
         * The acknowledgement carries the stored row, so the catalogue is
         * PATCHED rather than dropped -- discarding it would leave an offline
         * device with no Group list at all, which is the one thing it cannot
         * refetch. The sync-state projection is written too: a group that has
         * just been created has every field at revision 0 and no members, which
         * is KNOWN rather than assumed, and is what lets the next edit be made
         * before anything else is read.
         */
        async function reconcileCreatedPersonGroup(result) {
            if (!store || !await store.isAvailable()) return false;
            const group = result && result.group;
            if (!group || group.id !== result.group_id) return false;
            const rows = await cachedGroupRows();
            if (rows) {
                const merged = rows.filter(row => row && row.id !== group.id).concat([group]);
                if (!await writeGroupRows(merged)) return false;
            }
            if (result.changed && typeof root.prksNewPersonGroupState === 'function') {
                await patchEntity('person-group-state', group.id,
                    () => root.prksNewPersonGroupState(group.id));
            }
            return true;
        }

        /**
         * One Group field the server has applied.
         *
         * The value comes from the OPERATION, not the answer: a description has
         * no length bound worth echoing into a ledger with no retention policy,
         * so the acknowledgement omits it and the client reads the
         * authoritative copy out of its own immutable payload.
         */
        async function reconcilePersonGroupField(result, op) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.group_id;
            const field = result.field;
            if (!root.prksIsSupportedPersonGroupField ||
                !root.prksIsSupportedPersonGroupField(field)) return false;
            const raw = String((op && op.payload && op.payload.value) || '');
            const value = field === 'parent_id' ? (raw || null) : raw;
            await patchEntity('person-group-state', id, function (state) {
                if (typeof root.prksIsPersonGroupStateShape === 'function' &&
                    !root.prksIsPersonGroupStateShape(state, id)) return null;
                const entry = state.fields[field];
                // An acknowledgement older than what the cache already holds has
                // been superseded; applying it would move the field back.
                if (!entry || entry.revision > result.server_revision) return null;
                const next = Object.assign({}, state,
                    { fields: Object.assign({}, state.fields) });
                next.fields[field] = { revision: result.server_revision };
                return next;
            });
            const rows = await cachedGroupRows();
            if (rows && rows.some(row => row && row.id === id)) {
                const patched = rows.map(function (row) {
                    if (!row || row.id !== id) return row;
                    const next = Object.assign({}, row);
                    next[field] = value;
                    return next;
                });
                if (!await writeGroupRows(patched)) return false;
            }
            if (field === 'parent_id') {
                /* The cached detail carries a `parent` OBJECT, which this
                 * acknowledgement does not name -- there is no precise patch to
                 * make, and reconciliation only runs while connected, which is
                 * exactly when a refetch is affordable. */
                await invalidateEntity('person-group', id);
            } else {
                await patchEntity('person-group', id, function (group) {
                    if (!group || group.id !== id) return null;
                    const next = Object.assign({}, group);
                    next[field] = value;
                    return next;
                });
            }
            if (field === 'name') {
                /* A group chip carries the NAME, and chips are embedded in the
                 * People index rows and in every cached Person detail. The rows
                 * this device holds are patched; a Person snapshot is keyed by
                 * id and there is no list of them to walk, so the People domain
                 * is staled for those. */
                const people = await store.getList(PEOPLE_LIST_KEY)
                    .catch(function () { return null; });
                const peopleRows = people && Array.isArray(people.value) ? people.value : null;
                if (peopleRows) {
                    const token = currentDomainGeneration(DOMAIN_PEOPLE) + 1;
                    domainGeneration.set(DOMAIN_PEOPLE, token);
                    const patched = peopleRows.map(function (row) {
                        if (!row || !Array.isArray(row.groups)) return row;
                        if (!row.groups.some(g => g && g.id === id)) return row;
                        return Object.assign({}, row, {
                            groups: row.groups.map(g => (g && g.id === id
                                ? Object.assign({}, g, { name: raw }) : g)),
                        });
                    });
                    if (!await cacheListForDomain(PEOPLE_LIST_KEY, patched,
                        DOMAIN_PEOPLE, token)) return false;
                }
                await production.markDomainChanged(DOMAIN_PEOPLE, {
                    entityKinds: ['person'], listKeys: [],
                });
            }
            return true;
        }

        /** One membership the server has applied, in both directions. */
        async function reconcilePersonGroupMember(result, op) {
            if (!store || !await store.isAvailable()) return false;
            const groupId = result.group_id;
            const personId = result.person_id;
            const present = result.present === true;
            await patchEntity('person-group-state', groupId, function (state) {
                if (typeof root.prksIsPersonGroupStateShape === 'function' &&
                    !root.prksIsPersonGroupStateShape(state, groupId)) return null;
                const members = (state.members || [])
                    .filter(m => m.person_id !== personId)
                    .concat([{ person_id: personId, revision: result.server_revision,
                        present: present }]);
                members.sort((a, b) => a.person_id.localeCompare(b.person_id));
                return Object.assign({}, state, { members: members });
            });
            await patchEntity('person-group-memberships', personId, function (state) {
                if (typeof root.prksIsPersonGroupMembershipStateShape === 'function' &&
                    !root.prksIsPersonGroupMembershipStateShape(state, personId)) return null;
                const groups = (state.groups || [])
                    .filter(g => g.group_id !== groupId)
                    .concat([{ group_id: groupId, revision: result.server_revision,
                        present: present }]);
                groups.sort((a, b) => a.group_id.localeCompare(b.group_id));
                return Object.assign({}, state, { groups: groups });
            });
            if (!result.changed) return true;
            const people = await store.getList(PEOPLE_LIST_KEY)
                .catch(function () { return null; });
            const peopleRows = people && Array.isArray(people.value) ? people.value : null;
            const person = peopleRows
                ? peopleRows.find(row => row && row.id === personId) : null;
            const rows = await cachedGroupRows();
            const group = rows ? rows.find(row => row && row.id === groupId) : null;
            if (rows && group) {
                const patched = rows.map(function (row) {
                    if (!row || row.id !== groupId) return row;
                    return Object.assign({}, row, {
                        member_count: Math.max(0,
                            Number(row.member_count || 0) + (present ? 1 : -1)),
                    });
                });
                if (!await writeGroupRows(patched)) return false;
            }
            if (peopleRows && person) {
                const token = currentDomainGeneration(DOMAIN_PEOPLE) + 1;
                domainGeneration.set(DOMAIN_PEOPLE, token);
                const chip = { id: groupId, name: group ? String(group.name || '') : '' };
                const patched = peopleRows.map(function (row) {
                    if (!row || row.id !== personId) return row;
                    const chips = Array.isArray(row.groups) ? row.groups : [];
                    const without = chips.filter(g => !g || g.id !== groupId);
                    return Object.assign({}, row,
                        { groups: present ? without.concat([chip]) : without });
                });
                if (!await cacheListForDomain(PEOPLE_LIST_KEY, patched,
                    DOMAIN_PEOPLE, token)) return false;
            }
            /* The Group detail embeds whole People rows and the Person detail
             * embeds group chips. Both are patched from what this device
             * already holds; neither is invented when it does not. */
            await patchEntity('person-group', groupId, function (value) {
                if (!value || value.id !== groupId) return null;
                const members = Array.isArray(value.members) ? value.members : [];
                if (present) {
                    if (members.some(m => m && m.id === personId)) return null;
                    if (!person) return null;
                    const next = Object.assign({}, value,
                        { members: members.concat([person]) });
                    next.member_count = next.members.length;
                    return next;
                }
                const remaining = members.filter(m => !m || m.id !== personId);
                if (remaining.length === members.length) return null;
                const next = Object.assign({}, value, { members: remaining });
                next.member_count = remaining.length;
                return next;
            });
            await patchEntity('person', personId, function (value) {
                if (!value || value.id !== personId) return null;
                const chips = Array.isArray(value.groups) ? value.groups : [];
                const without = chips.filter(g => !g || g.id !== groupId);
                if (present) {
                    if (without.length !== chips.length) return null;
                    return Object.assign({}, value, {
                        groups: chips.concat([{ id: groupId,
                            name: group ? String(group.name || '') : '' }]),
                    });
                }
                if (without.length === chips.length) return null;
                return Object.assign({}, value, { groups: without });
            });
            return true;
        }

        /**
         * A Group the server has removed.
         *
         * Its children are reparented to its own parent, exactly as the
         * canonical delete does -- the effective hierarchy the user was already
         * looking at becomes the acknowledged one, rather than briefly growing
         * an orphan.
         */
        async function reconcileDeletedPersonGroup(result) {
            if (!store || !await store.isAvailable()) return false;
            const id = result.group_id;
            const rows = await cachedGroupRows();
            if (rows) {
                const gone = rows.find(row => row && row.id === id);
                const inherited = gone ? (gone.parent_id || null) : null;
                const remaining = rows
                    .filter(row => row && row.id !== id)
                    .map(row => (row.parent_id === id
                        ? Object.assign({}, row, { parent_id: inherited }) : row));
                if (!await writeGroupRows(remaining)) return false;
            }
            await invalidateEntity('person-group', id);
            await invalidateEntity('person-group-state', id);
            const people = await store.getList(PEOPLE_LIST_KEY)
                .catch(function () { return null; });
            const peopleRows = people && Array.isArray(people.value) ? people.value : null;
            if (peopleRows && peopleRows.some(row => row && Array.isArray(row.groups) &&
                    row.groups.some(g => g && g.id === id))) {
                const token = currentDomainGeneration(DOMAIN_PEOPLE) + 1;
                domainGeneration.set(DOMAIN_PEOPLE, token);
                const patched = peopleRows.map(function (row) {
                    if (!row || !Array.isArray(row.groups)) return row;
                    const without = row.groups.filter(g => !g || g.id !== id);
                    return without.length === row.groups.length
                        ? row : Object.assign({}, row, { groups: without });
                });
                if (!await cacheListForDomain(PEOPLE_LIST_KEY, patched,
                    DOMAIN_PEOPLE, token)) return false;
            }
            /* Every cached Person detail may carry this group's chip, and there
             * is no list of them to walk. */
            await production.markDomainChanged(DOMAIN_PEOPLE, {
                entityKinds: ['person'], listKeys: [],
            });
            return true;
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
         * Call before a canonical server-bound mutation. Returns true (and
         * surfaces the standard message) when the mutation must be blocked.
         * Never queues or fakes success. Durable local-first ops use
         * `local-store.js` instead of this guard.
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
            reconcileFolderTag,
            reconcileWorkField,
            reconcileWorkSource,
            reconcileWorkNote,
            reconcilePrivateNote,
            reconcileWorkRole,
            reconcilePdfAnnotation,
            reconcileRecentOpen,
            reconcileCreatedFolder,
            reconcileFolderField,
            reconcileWorkFolder,
            reconcileDeletedFolder,
            reconcileCreatedPlaylist,
            reconcilePlaylistField,
            reconcileWorkPlaylist,
            reconcilePlaylistOrder,
            reconcileDeletedPlaylist,
            reconcileCreatedConcept,
            reconcileConceptField,
            reconcileConceptIdentity,
            reconcileConceptParents,
            reconcileDeletedConcept,
            reconcileCreatedPosition,
            reconcilePositionField,
            reconcileDeletedPosition,
            reconcileCreatedArgument,
            reconcileArgumentField,
            reconcileArgumentSources,
            reconcileArgumentTargets,
            reconcileDeletedArgument,
            reconcileCreatedTag,
            reconcileDeletedTag,
            reconcileMergedTag,
            reconcileCreatedWork,
            reconcileDeletedWork,
            reconcileCreatedPerson,
            reconcilePersonField,
            reconcileDeletedPerson,
            reconcileCreatedPersonGroup,
            reconcilePersonGroupField,
            reconcilePersonGroupMember,
            reconcileDeletedPersonGroup,
            cacheEntity: cacheEntity,
            cacheEntityIfCurrent: cacheEntityIfCurrent,
            peekEntity: peekEntity,
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
    function prksOfflinePeekEntity(kind, id) {
        return production.peekEntity(kind, id);
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
    /* The index alone, for a change whose effect on the playlists themselves
     * this device can state exactly. Reordering bumps `updated_at`, which
     * reorders the index -- but the new ORDER is in the acknowledgement, so the
     * playlist's own snapshot is patched rather than thrown away. Dropping it
     * would cost an offline device the page it had just reordered. */
    function prksOfflineMarkPlaylistsIndexChanged() {
        return production.markDomainChanged(DOMAIN_PLAYLISTS, {
            entityKinds: [], listKeys: [PLAYLISTS_LIST_KEY],
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
        prksOfflinePeekEntity: prksOfflinePeekEntity,
        prksOfflineReconcileWorkTag: result => production.reconcileWorkTag(result),
        prksOfflineReconcileFolderTag: result => production.reconcileFolderTag(result),
        prksOfflineReconcileWorkField: result => production.reconcileWorkField(result),
        prksOfflineReconcileWorkSource: result => production.reconcileWorkSource(result),
        prksOfflineReconcileWorkNote: (result, op) => production.reconcileWorkNote(result, op),
        prksOfflineReconcilePrivateNote: (result, op) =>
            production.reconcilePrivateNote(result, op),
        prksOfflineReconcileWorkRole: result => production.reconcileWorkRole(result),
        prksOfflineReconcilePdfAnnotation: result => production.reconcilePdfAnnotation(result),
        prksOfflineReconcileRecentOpen: result => production.reconcileRecentOpen(result),
        prksOfflineReconcileCreatedFolder: result => production.reconcileCreatedFolder(result),
        prksOfflineReconcileFolderField: (result, op) =>
            production.reconcileFolderField(result, op),
        prksOfflineReconcileWorkFolder: (result, op) =>
            production.reconcileWorkFolder(result, op),
        prksOfflineReconcileDeletedFolder: result => production.reconcileDeletedFolder(result),
        prksOfflineReconcileCreatedPlaylist: result =>
            production.reconcileCreatedPlaylist(result),
        prksOfflineReconcilePlaylistField: (result, op) =>
            production.reconcilePlaylistField(result, op),
        prksOfflineReconcileWorkPlaylist: (result, op) =>
            production.reconcileWorkPlaylist(result, op),
        prksOfflineReconcilePlaylistOrder: (result, op) =>
            production.reconcilePlaylistOrder(result, op),
        prksOfflineReconcileDeletedPlaylist: result =>
            production.reconcileDeletedPlaylist(result),
        prksOfflineReconcileCreatedConcept: result =>
            production.reconcileCreatedConcept(result),
        prksOfflineReconcileConceptField: (result, op) =>
            production.reconcileConceptField(result, op),
        prksOfflineReconcileConceptIdentity: (result, op) =>
            production.reconcileConceptIdentity(result, op),
        prksOfflineReconcileConceptParents: (result, op) =>
            production.reconcileConceptParents(result, op),
        prksOfflineReconcileDeletedConcept: result =>
            production.reconcileDeletedConcept(result),
        prksOfflineReconcileCreatedPosition: result =>
            production.reconcileCreatedPosition(result),
        prksOfflineReconcilePositionField: (result, op) =>
            production.reconcilePositionField(result, op),
        prksOfflineReconcileDeletedPosition: result =>
            production.reconcileDeletedPosition(result),
        prksOfflineReconcileCreatedArgument: result =>
            production.reconcileCreatedArgument(result),
        prksOfflineReconcileArgumentField: (result, op) =>
            production.reconcileArgumentField(result, op),
        prksOfflineReconcileArgumentSources: (result, op) =>
            production.reconcileArgumentSources(result, op),
        prksOfflineReconcileArgumentTargets: (result, op) =>
            production.reconcileArgumentTargets(result, op),
        prksOfflineReconcileDeletedArgument: result =>
            production.reconcileDeletedArgument(result),
        prksOfflineReconcileCreatedTag: result => production.reconcileCreatedTag(result),
        prksOfflineReconcileDeletedTag: result => production.reconcileDeletedTag(result),
        prksOfflineReconcileMergedTag: result => production.reconcileMergedTag(result),
        prksOfflineReconcileCreatedWork: result => production.reconcileCreatedWork(result),
        prksOfflineReconcileDeletedWork: result => production.reconcileDeletedWork(result),
        prksOfflineReconcileCreatedPerson: result => production.reconcileCreatedPerson(result),
        prksOfflineReconcilePersonField: (result, op) => production.reconcilePersonField(result, op),
        prksOfflineReconcileDeletedPerson: result => production.reconcileDeletedPerson(result),
        prksOfflineReconcileCreatedPersonGroup: result =>
            production.reconcileCreatedPersonGroup(result),
        prksOfflineReconcilePersonGroupField: (result, op) =>
            production.reconcilePersonGroupField(result, op),
        prksOfflineReconcilePersonGroupMember: (result, op) =>
            production.reconcilePersonGroupMember(result, op),
        prksOfflineReconcileDeletedPersonGroup: result =>
            production.reconcileDeletedPersonGroup(result),
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
        prksOfflineMarkPlaylistsIndexChanged: prksOfflineMarkPlaylistsIndexChanged,
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
