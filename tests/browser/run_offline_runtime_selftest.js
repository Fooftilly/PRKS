#!/usr/bin/env node
'use strict';

/* Deterministic coverage for the offline connectivity/read-through/mutation-
 * guard runtime (offline-runtime.js). Uses fake requestImpl/store/timers so
 * state transitions and policy decisions are exercised precisely, without a
 * real browser or network.
 */

const path = require('path');
const rootDir = path.resolve(__dirname, '../..');
const mod = require(path.join(rootDir, 'frontend/js/offline-runtime.js'));

let passed = 0;
let failed = 0;

function record(name, ok, detail) {
    if (ok) passed += 1;
    else failed += 1;
    console.log((ok ? 'PASS  ' : 'FAIL  ') + name + (detail ? ' ' + detail : ''));
}

function assertEq(name, got, want) {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    record(name, ok, ok ? '' : 'got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want));
}

function assert(name, ok, detail) {
    record(name, !!ok, ok ? '' : detail || '');
}

/* Fake manual timer queue: setTimeout/clearTimeout that only fire when
 * explicitly flushed, so probe backoff scheduling is deterministic. */
function makeFakeTimers() {
    let nextId = 1;
    const pending = new Map();
    return {
        setTimeout: function (fn, delay) {
            const id = nextId++;
            pending.set(id, { fn: fn, delay: delay });
            return id;
        },
        clearTimeout: function (id) {
            pending.delete(id);
        },
        flushAll: function () {
            const entries = Array.from(pending.entries());
            pending.clear();
            entries.forEach(function (e) {
                e[1].fn();
            });
        },
        pendingCount: function () {
            return pending.size;
        },
        pendingDelays: function () {
            return Array.from(pending.values()).map((e) => e.delay);
        },
    };
}

function makeFakeStore(overrides) {
    const entities = new Map();
    const lists = new Map();
    const base = {
        getEntity: async function (kind, id) {
            return entities.get(kind + ':' + id) || null;
        },
        putEntity: async function (kind, id, value, rev) {
            entities.set(kind + ':' + id, { value: value, cachedAt: 9999, sourceRevision: rev });
            return true;
        },
        deleteEntity: async function (kind, id) {
            entities.delete(kind + ':' + id);
            return true;
        },
        deleteEntitiesByKind: async function (kind) {
            Array.from(entities.keys()).forEach(function (key) {
                if (key.indexOf(kind + ':') === 0) entities.delete(key);
            });
            return true;
        },
        deleteList: async function (key) {
            lists.delete(key);
            return true;
        },
        getList: async function (key) {
            return lists.get(key) || null;
        },
        putList: async function (key, value, rev) {
            lists.set(key, { value: value, cachedAt: 9999, sourceRevision: rev });
            return true;
        },
        stats: async function () {
            return { available: true, entityCount: entities.size, listCount: lists.size, approxBytes: 0 };
        },
        clearAll: async function () {
            entities.clear();
            lists.clear();
            return true;
        },
        _entities: entities,
        _lists: lists,
    };
    return Object.assign(base, overrides || {});
}

/* createPrksOfflineRuntime falls back to the REAL global setTimeout/clearTimeout
 * whenever the option is falsy (so production callers can simply omit it) --
 * passing `setTimeout: null` from a test does NOT disable scheduling, it
 * silently arms a real timer. Blocks that don't care about probe scheduling
 * must pass explicit no-ops instead of null/undefined. */
function noopSetTimeout() {
    return 0;
}
function noopClearTimeout() {}

function okJsonResponse(body) {
    return {
        ok: true,
        status: 200,
        json: async function () {
            return body;
        },
    };
}

function domainErrorResponse(status) {
    return { ok: false, status: status };
}

async function run() {
    /* Fifth-domain cleanup is audited: no sweep may touch another domain. */
    {
        const domains = ['concepts', 'positions', 'arguments', 'people', 'person-groups'];
        const kinds = ['concept', 'position', 'argument', 'person', 'person-group'];
        const sweeps = [];
        const store = makeFakeStore({
            deleteEntitiesByKind: async kind => {
                sweeps.push(kind);
                if (kind !== 'person-group') throw Error('cross-domain entity sweep');
                return false;
            },
            deleteList: async key => {
                sweeps.push(key);
                if (key !== 'person-groups:index') throw Error('cross-domain list sweep');
                return false;
            },
        });
        for (const kind of [...kinds, 'work']) await store.putEntity(kind, '1', {id:'1'}, '');
        for (const domain of domains) await store.putList(domain + ':index', [], '');
        const runtime = mod.createPrksOfflineRuntime({
            store, prksRequest: () => Promise.reject(Error('offline')),
            setTimeout: noopSetTimeout, clearTimeout: noopClearTimeout,
            window: null, caches: null, navigator: null,
        });
        runtime.markDomainChanged('person-groups', {
            entityKinds:['person-group'], listKeys:['person-groups:index'],
        });
        await runtime._domainCleanup('person-groups');
        assertEq('Group cleanup touches exactly its kind and key', sweeps.sort(), ['person-group','person-groups:index']);
        assertEq('five generations remain independent', domains.map(d=>runtime.currentDomainGeneration(d)), [0,0,0,0,1]);
        assertEq('only Groups blocked after failed cleanup', domains.map(d=>runtime.isDomainBlocked(d)), [false,false,false,false,true]);
        for (let i=0; i<domains.length; i++) {
            const result = await runtime.readThroughEntity(kinds[i], '1', '/api/test', {domain:domains[i]});
            assertEq(domains[i] + ' fallback isolation', result.source, i===4 ? 'unavailable' : 'cache');
        }
        assertEq('Works unaffected by Group cleanup', (await runtime.readThroughEntity('work','1','/api/test')).source, 'cache');
        assertEq('Group constant', mod.PRKS_OFFLINE_DOMAIN_PERSON_GROUPS, 'person-groups');
        assertEq('Group list constant', mod.PRKS_OFFLINE_PERSON_GROUPS_LIST_KEY, 'person-groups:index');
        assert('Group canonical runtime helper exported', typeof mod.prksOfflineMarkPersonGroupsChanged === 'function');
    }
    /* The superseded-cleanup invariant is domain-generic, but each existing
     * case names an earlier domain, so one focused Group case keeps the fifth
     * domain covered without cloning the whole suite under a new name. */
    {
        const store = makeFakeStore();
        const releases = [];
        const slowStore = Object.assign({}, store, {
            deleteEntitiesByKind: function () {
                return new Promise(function (resolve) {
                    releases.push(function () { resolve(true); });
                });
            },
        });
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: () => Promise.reject(Error('offline')),
            store: slowStore, setTimeout: noopSetTimeout, clearTimeout: noopClearTimeout,
            window: null, caches: null, navigator: null,
        });
        // A Group rename, then a membership change: overlapping Person Groups
        // invalidations are the normal case here.
        const genA = runtime.markDomainChanged('person-groups', { entityKinds: ['person-group'] });
        const genB = runtime.markDomainChanged('person-groups', { entityKinds: ['person-group'] });
        assert('the second Groups invalidation has a newer generation', genB > genA);
        releases[0]();
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
        assertEq('a superseded Groups cleanup cannot unblock the newer one',
            runtime.isDomainBlocked('person-groups'), true);
        assertEq('a superseded completion never resets the generation',
            runtime.currentDomainGeneration('person-groups'), genB);
        releases[1]();
        await runtime._domainCleanup('person-groups');
        assertEq('the current Groups generation settles normally',
            runtime.isDomainBlocked('person-groups'), false);
    }
    /* ---- connectivity FSM: request failure -> offline, probe backoff, recovery -> online ---- */
    {
        const timers = makeFakeTimers();
        let requestQueue = [];
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                const behavior = requestQueue.shift();
                if (!behavior) return Promise.reject(new Error('no behavior queued'));
                return behavior();
            },
            store: makeFakeStore(),
            setTimeout: timers.setTimeout,
            clearTimeout: timers.clearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        const states = [];
        runtime.subscribe(function (s) {
            states.push(s);
        });

        assertEq('initial state is online', runtime.getState(), mod.PRKS_OFFLINE_STATE_ONLINE);

        runtime.noteRequestFailure();
        assertEq('state becomes offline after a real request failure', runtime.getState(), mod.PRKS_OFFLINE_STATE_OFFLINE);
        assert('a probe was scheduled after going offline', timers.pendingCount() === 1);
        assertEq('first probe backoff is the shortest interval', timers.pendingDelays(), [3000]);

        // First probe attempt fails (a real transport/fetch rejection, not just a
        // non-2xx HTTP response) -> stays offline, reschedules with a longer backoff.
        requestQueue.push(function () {
            return Promise.reject(new Error('still unreachable'));
        });
        timers.flushAll();
        await Promise.resolve();
        await Promise.resolve();
        assertEq('state after failed probe is still offline', runtime.getState(), mod.PRKS_OFFLINE_STATE_OFFLINE);
        assertEq('second probe backoff grows', timers.pendingDelays(), [6000]);

        // Second probe attempt succeeds -> online.
        requestQueue.push(function () {
            return Promise.resolve(okJsonResponse({}));
        });
        timers.flushAll();
        await Promise.resolve();
        await Promise.resolve();
        assertEq('state after a successful probe is online', runtime.getState(), mod.PRKS_OFFLINE_STATE_ONLINE);
        assertEq('no probe left pending once online', timers.pendingCount(), 0);
        assert(
            'observed transition sequence includes reconnecting before online',
            states.indexOf(mod.PRKS_OFFLINE_STATE_RECONNECTING) !== -1 &&
                states.indexOf(mod.PRKS_OFFLINE_STATE_RECONNECTING) < states.lastIndexOf(mod.PRKS_OFFLINE_STATE_ONLINE)
        );
    }

    /* ---- noteRequestSuccess cancels a pending probe and clears backoff ---- */
    {
        const timers = makeFakeTimers();
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('unused'));
            },
            store: makeFakeStore(),
            setTimeout: timers.setTimeout,
            clearTimeout: timers.clearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        runtime.noteRequestFailure();
        assertEq('offline after failure', runtime.getState(), mod.PRKS_OFFLINE_STATE_OFFLINE);
        runtime.noteRequestSuccess();
        assertEq('online again after an unrelated successful request', runtime.getState(), mod.PRKS_OFFLINE_STATE_ONLINE);
        assertEq('pending probe cleared by noteRequestSuccess', timers.pendingCount(), 0);
    }

    /* ---- runProbe reachability: an HTTP response of ANY status (even 4xx/5xx) means the server is reachable ---- */
    {
        const timers = makeFakeTimers();
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.resolve(domainErrorResponse(500));
            },
            store: makeFakeStore(),
            setTimeout: timers.setTimeout,
            clearTimeout: timers.clearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        runtime.noteRequestFailure(); // force offline + schedule a probe
        assertEq('forced offline before the probe runs', runtime.getState(), mod.PRKS_OFFLINE_STATE_OFFLINE);
        timers.flushAll();
        await Promise.resolve();
        await Promise.resolve();
        assertEq(
            'a resolved HTTP 500 probe response means the PRKS process is reachable -> online',
            runtime.getState(),
            mod.PRKS_OFFLINE_STATE_ONLINE
        );
    }

    /* ---- runProbe reachability: only a rejected fetch (no transport response) means unreachable ---- */
    {
        const timers = makeFakeTimers();
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('getaddrinfo ENOTFOUND'));
            },
            store: makeFakeStore(),
            setTimeout: timers.setTimeout,
            clearTimeout: timers.clearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        runtime._runProbe();
        await Promise.resolve();
        await Promise.resolve();
        assertEq('a rejected fetch (no HTTP response at all) means unreachable -> offline', runtime.getState(), mod.PRKS_OFFLINE_STATE_OFFLINE);
    }

    /* ---- init(): begins a real probe immediately, so an unreachable server is detected without any prior request ---- */
    {
        const timers = makeFakeTimers();
        let requestCount = 0;
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                requestCount += 1;
                return Promise.reject(new Error('unreachable at startup'));
            },
            store: makeFakeStore(),
            setTimeout: timers.setTimeout,
            clearTimeout: timers.clearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        assertEq('state defaults to online before init', runtime.getState(), mod.PRKS_OFFLINE_STATE_ONLINE);
        runtime.init();
        assertEq('init() immediately moves to reconnecting while the startup probe is in flight', runtime.getState(), mod.PRKS_OFFLINE_STATE_RECONNECTING);
        await Promise.resolve();
        await Promise.resolve();
        assert('init() actually issued a real probe request', requestCount === 1);
        assertEq('unreachable-at-startup resolves to offline without any Work navigation first', runtime.getState(), mod.PRKS_OFFLINE_STATE_OFFLINE);
    }

    /* ---- init(): browser online AND offline hints both trigger reconnecting + a real probe, not just 'online' ---- */
    {
        const timers = makeFakeTimers();
        const listeners = {};
        const fakeWindow = {
            addEventListener: function (name, fn) {
                listeners[name] = listeners[name] || [];
                listeners[name].push(fn);
            },
        };
        let requestCount = 0;
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                requestCount += 1;
                return Promise.resolve(okJsonResponse({}));
            },
            store: makeFakeStore(),
            setTimeout: timers.setTimeout,
            clearTimeout: timers.clearTimeout,
            window: fakeWindow,
            caches: null,
            navigator: null,
        });
        runtime.init();
        await Promise.resolve();
        await Promise.resolve();
        assert('online listener registered', Array.isArray(listeners.online) && listeners.online.length === 1);
        assert('offline listener registered', Array.isArray(listeners.offline) && listeners.offline.length === 1);
        requestCount = 0;
        listeners.offline.forEach(function (fn) {
            fn();
        });
        await Promise.resolve();
        await Promise.resolve();
        assert('the browser offline hint triggers a real probe (not just navigator.onLine trust)', requestCount === 1);
        requestCount = 0;
        listeners.online.forEach(function (fn) {
            fn();
        });
        await Promise.resolve();
        await Promise.resolve();
        assert('the browser online hint also triggers a real probe rather than flipping state directly', requestCount === 1);
    }

    /* ---- readThroughEntity: online success renders server value + writes cache, never fails the read on cache-write failure ---- */
    {
        const store = makeFakeStore({
            putEntity: async function () {
                throw new Error('simulated cache write failure (e.g. quota)');
            },
        });
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.resolve(okJsonResponse({ id: 'W-1', title: 'Live Work' }));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        const result = await runtime.readThroughEntity('work', 'W-1', '/api/works/W-1');
        assertEq('online read returns server provenance', result.source, 'server');
        assertEq('online read returns the live value', result.value, { id: 'W-1', title: 'Live Work' });
        assert('online read reports a cachedAt timestamp', typeof result.cachedAt === 'number');
    }

    /* ---- readThroughEntity: network failure falls back to a cached value, marked as cache ---- */
    {
        const store = makeFakeStore();
        await store.putEntity('work', 'W-2', { id: 'W-2', title: 'Cached Work' }, 'rev-1');
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('network unreachable'));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        const result = await runtime.readThroughEntity('work', 'W-2', '/api/works/W-2');
        assertEq('offline fallback returns cache provenance', result.source, 'cache');
        assertEq('offline fallback returns the previously cached value', result.value, { id: 'W-2', title: 'Cached Work' });
        assertEq('runtime transitioned to offline after the network failure', runtime.getState(), mod.PRKS_OFFLINE_STATE_OFFLINE);
    }

    /* ---- explicit cache coherence: complete authoritative Work replaces cache; partial success invalidates it ---- */
    {
        const store = makeFakeStore();
        await store.putEntity('work', 'W-coherent', { id: 'W-coherent', title: 'Old' }, '');
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('unused'));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        const fresh = { id: 'W-coherent', title: 'New', tags: [{ id: 'tag-1' }] };
        assertEq('authoritative Work replacement writes cache', await runtime.cacheEntity('work', 'W-coherent', fresh), true);
        assertEq('authoritative Work replacement overwrites stale cache', (await store.getEntity('work', 'W-coherent')).value, fresh);
        assertEq('cache helper does not mutate supplied Work', fresh, { id: 'W-coherent', title: 'New', tags: [{ id: 'tag-1' }] });
        assertEq('partial successful Work mutation invalidates cache', await runtime.invalidateEntity('work', 'W-coherent'), true);
        assertEq('invalidated Work cannot serve offline fallback', await store.getEntity('work', 'W-coherent'), null);
        assertEq('null is never cached as an entity replacement', await runtime.cacheEntity('work', 'W-coherent', null), false);
    }

    /* ---- cache-maintenance failures are non-fatal and never change connectivity ---- */
    {
        const store = makeFakeStore({
            putEntity: async function () {
                throw new Error('quota');
            },
            deleteEntity: async function () {
                throw new Error('blocked');
            },
        });
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('unused'));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        assertEq('cache write failure resolves false', await runtime.cacheEntity('work', 'W-fail', { id: 'W-fail' }), false);
        assertEq('cache delete failure resolves false', await runtime.invalidateEntity('work', 'W-fail'), false);
        assertEq('cache maintenance failure keeps connectivity unchanged', runtime.getState(), mod.PRKS_OFFLINE_STATE_ONLINE);
    }

    /* ---- canonical mutation generations block fallback and stale post-mutation GETs ---- */
    {
        const store = makeFakeStore();
        await store.putEntity('work', 'W-race', { id: 'W-race', title: 'v0' }, '');
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('offline'));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        const tokenA = runtime.markEntityChanged('work', 'W-race');
        const tokenB = runtime.markEntityChanged('work', 'W-race');
        assert('later canonical mutation has a newer coherence token', tokenB > tokenA);
        assertEq(
            'stale GET A cannot repopulate after mutation B',
            await runtime.cacheEntityIfCurrent('work', 'W-race', { id: 'W-race', title: 'version A' }, tokenA),
            false
        );
        assertEq(
            'only current authoritative GET B becomes eligible',
            await runtime.cacheEntityIfCurrent('work', 'W-race', { id: 'W-race', title: 'version B' }, tokenB),
            true
        );
        assertEq('cache holds current generation B only', (await store.getEntity('work', 'W-race')).value.title, 'version B');
    }

    /* ---- even a failed IndexedDB delete cannot serve an invalidated row in this runtime ---- */
    {
        const entities = new Map();
        entities.set('work:W-delete-fail', { value: { id: 'W-delete-fail', title: 'old' }, cachedAt: 1 });
        const store = makeFakeStore({
            getEntity: async function (kind, id) {
                return entities.get(kind + ':' + id) || null;
            },
            deleteEntity: async function () {
                return false;
            },
        });
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('offline'));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        runtime.markEntityChanged('work', 'W-delete-fail');
        const result = await runtime.readThroughEntity('work', 'W-delete-fail', '/api/works/W-delete-fail');
        assertEq('invalidated row is unavailable despite delete failure', result.source, 'unavailable');
    }

    /* ---- readThroughEntity: network failure with nothing cached -> unavailable, not a fake success ---- */
    {
        const store = makeFakeStore();
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('network unreachable'));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        const result = await runtime.readThroughEntity('work', 'W-missing', '/api/works/W-missing');
        assertEq('no cache and no network yields unavailable provenance', result.source, 'unavailable');
        assertEq('unavailable result carries no value', result.value, null);
    }

    /* ---- readThroughEntity: a 404 is a normal not-found result from a reachable server, never a cache fallback ---- */
    {
        const store = makeFakeStore();
        await store.putEntity('work', 'W-3', { id: 'W-3', title: 'Stale cached copy' }, '');
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.resolve(domainErrorResponse(404));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        const result = await runtime.readThroughEntity('work', 'W-3', '/api/works/W-3');
        assertEq('a 404 domain response resolves to a null server value (not-found), not a throw', result, {
            value: null,
            source: 'server',
            cachedAt: null,
        });
        assertEq('a 404 domain response keeps state online (server is reachable)', runtime.getState(), mod.PRKS_OFFLINE_STATE_ONLINE);
    }

    /* ---- readThroughEntity: a non-404 domain error (e.g. 500) propagates -- must never masquerade as "not found" ---- */
    {
        const store = makeFakeStore();
        await store.putEntity('work', 'W-4', { id: 'W-4', title: 'Stale cached copy' }, '');
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.resolve(domainErrorResponse(500));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        let threw = null;
        try {
            await runtime.readThroughEntity('work', 'W-4', '/api/works/W-4');
        } catch (e) {
            threw = e;
        }
        assert('a 500 domain response throws rather than silently becoming not-found', !!threw);
        assertEq('a 500 response does not carry the cached fallback value', threw && threw.isPrksDomainError, true);
        assertEq('a 500 domain response keeps state online (server is reachable, just erroring)', runtime.getState(), mod.PRKS_OFFLINE_STATE_ONLINE);
    }

    /* ---- readThroughEntity: an invalid-JSON response propagates too, never a fake not-found/cache fallback ---- */
    {
        const store = makeFakeStore();
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.resolve({
                    ok: true,
                    status: 200,
                    json: async function () {
                        throw new SyntaxError('Unexpected end of JSON input');
                    },
                });
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        let threw = null;
        try {
            await runtime.readThroughEntity('work', 'W-5', '/api/works/W-5');
        } catch (e) {
            threw = e;
        }
        assert('invalid JSON throws rather than becoming not-found/cached', !!threw);
        assertEq('invalid JSON keeps state online (server responded, body was just bad)', runtime.getState(), mod.PRKS_OFFLINE_STATE_ONLINE);
    }

    /* ---- readThroughList mirrors the same entity policy for named list snapshots ---- */
    {
        const store = makeFakeStore();
        await store.putList('works:recent', { ids: ['W-1', 'W-2'] }, '');
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('offline'));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        const result = await runtime.readThroughList('works:recent', '/api/works?recent=1');
        assertEq('list fallback returns cache provenance', result.source, 'cache');
        assertEq('list fallback returns the cached snapshot', result.value, { ids: ['W-1', 'W-2'] });
    }

    /* ---- mutation guard: blocked while offline, shows the standard message, never queues ---- */
    {
        const alerts = [];
        const fakeRoot = { prksAlertMessage: (msg, title) => alerts.push({ msg, title }) };
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('offline'));
            },
            store: makeFakeStore(),
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: fakeRoot,
            caches: null,
            navigator: null,
        });
        // createPrksOfflineRuntime reads root.prksAlertMessage from its outer module
        // closure (root = the module's own global), so attach there for this check.
        const globalRoot = typeof globalThis !== 'undefined' ? globalThis : this;
        const prevAlert = globalRoot.prksAlertMessage;
        globalRoot.prksAlertMessage = (msg, title) => alerts.push({ msg, title });
        try {
            assertEq('mutation not blocked while online', runtime.isMutationBlocked(), false);
            runtime.noteRequestFailure();
            assertEq('mutation blocked once offline', runtime.isMutationBlocked(), true);
            const blocked = runtime.guardMutation();
            assert('guardMutation returns true (blocked) while offline', blocked === true);
            assert('guardMutation surfaced exactly one alert', alerts.length === 1);
            assertEq('guardMutation uses the standard requires-connection message', alerts[0].msg, 'This change requires a connection to PRKS.');
        } finally {
            globalRoot.prksAlertMessage = prevAlert;
        }
    }
    {
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.resolve(okJsonResponse({}));
            },
            store: makeFakeStore(),
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        assertEq('guardMutation is a no-op (returns false) while online', runtime.guardMutation(), false);
    }

    /* ---- diagnostics: aggregate/privacy-safe only, no cached entity content leaks ---- */
    {
        const store = makeFakeStore();
        await store.putEntity('work', 'W-1', { title: 'Secret Research Title' }, '');
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: null,
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        const diag = await runtime.diagnostics();
        assertEq('diagnostics reports entity count', diag.entityCount, 1);
        assertEq('diagnostics reports availability', diag.available, true);
        assert('diagnostics has no free-form value/content field', !('value' in diag) && !('title' in diag));
        assert('diagnostics JSON never contains cached title text', JSON.stringify(diag).indexOf('Secret Research Title') === -1);
    }

    /* ---- clearCache: browser-side only, delegates to the store + PDF cache, never touches network ---- */
    {
        const store = makeFakeStore();
        await store.putEntity('work', 'W-1', { title: 'x' }, '');
        let deletedCacheName = null;
        const fakeCaches = {
            delete: async function (name) {
                deletedCacheName = name;
                return true;
            },
            open: async function () {
                return { keys: async () => [] };
            },
        };
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: null,
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: fakeCaches,
            navigator: null,
        });
        const ok = await runtime.clearCache();
        assert('clearCache resolves true', ok === true);
        assertEq('clearCache removes the pdf cache by its documented name', deletedCacheName, mod.PRKS_OFFLINE_PDF_CACHE_NAME);
        assertEq('clearCache actually empties the store', store._entities.size, 0);
    }

    /* ---- shape validation happens BEFORE cache publication: a malformed 200 cannot destroy a good cache ---- */
    {
        const store = makeFakeStore();
        await store.putList('concepts:index', [{ id: 'C-1', name: 'Good' }], '');
        let body = [{ id: 'C-1', name: 'Good' }];
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.resolve(okJsonResponse(body));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        const isArrayShape = function (v) {
            return Array.isArray(v);
        };
        // A reachable server answering 200 with the WRONG shape.
        body = { error: 'unexpected shape' };
        let threw = null;
        try {
            await runtime.readThroughList('concepts:index', '/api/concepts', {
                domain: 'concepts',
                validate: isArrayShape,
            });
        } catch (e) {
            threw = e;
        }
        assert('a wrong-shaped authoritative list throws like any other domain error', !!threw);
        assertEq('the shape rejection is a domain error, not a transport failure', threw && threw.isPrksDomainError, true);
        assertEq('a wrong-shaped 200 keeps state online (the server did answer)', runtime.getState(), mod.PRKS_OFFLINE_STATE_ONLINE);
        await Promise.resolve();
        await Promise.resolve();
        assertEq(
            'the previously good cached list is left completely untouched',
            store._lists.get('concepts:index').value,
            [{ id: 'C-1', name: 'Good' }]
        );
        // ... and the good cache is still what an offline read falls back to.
        const offlineRuntime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('offline'));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        const fallback = await offlineRuntime.readThroughList('concepts:index', '/api/concepts', {
            domain: 'concepts',
            validate: isArrayShape,
        });
        assertEq('the good snapshot still serves offline', fallback.source, 'cache');
        assertEq('the good snapshot is unchanged', fallback.value, [{ id: 'C-1', name: 'Good' }]);
        // A later well-shaped response publishes normally.
        body = [{ id: 'C-1', name: 'Fresh' }];
        const good = await runtime.readThroughList('concepts:index', '/api/concepts', {
            domain: 'concepts',
            validate: isArrayShape,
        });
        assertEq('a well-shaped response still renders', good.source, 'server');
        await Promise.resolve();
        await Promise.resolve();
        assertEq('a well-shaped response publishes to cache', store._lists.get('concepts:index').value, [
            { id: 'C-1', name: 'Fresh' },
        ]);
    }

    /* ---- same rule for one entity: a malformed/mismatched 200 never replaces the cached Concept ---- */
    {
        const store = makeFakeStore();
        await store.putEntity('concept', 'C-1', { id: 'C-1', name: 'Good' }, '');
        let body = { id: 'C-1', name: 'Good' };
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.resolve(okJsonResponse(body));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        const isConcept = function (v) {
            return !!(v && typeof v === 'object' && !Array.isArray(v) && String(v.id) === 'C-1');
        };
        body = { error: 'unexpected shape' };
        let threw = null;
        try {
            await runtime.readThroughEntity('concept', 'C-1', '/api/concepts/C-1', {
                domain: 'concepts',
                validate: isConcept,
            });
        } catch (e) {
            threw = e;
        }
        assert('a wrong-shaped authoritative entity throws', !!threw);
        await Promise.resolve();
        await Promise.resolve();
        assertEq(
            'the previously good cached Concept is untouched by a malformed 200',
            store._entities.get('concept:C-1').value,
            { id: 'C-1', name: 'Good' }
        );
        // An id that does not match the requested Concept is equally unacceptable.
        body = { id: 'C-OTHER', name: 'Wrong record' };
        threw = null;
        try {
            await runtime.readThroughEntity('concept', 'C-1', '/api/concepts/C-1', {
                domain: 'concepts',
                validate: isConcept,
            });
        } catch (e) {
            threw = e;
        }
        assert('a mismatched-id authoritative entity throws', !!threw);
        await Promise.resolve();
        await Promise.resolve();
        assertEq(
            'a mismatched-id response never becomes this Concept cache',
            store._entities.get('concept:C-1').value,
            { id: 'C-1', name: 'Good' }
        );
    }

    /* ---- a validator that throws is treated as rejection, never as acceptance ---- */
    {
        const store = makeFakeStore();
        await store.putEntity('concept', 'C-1', { id: 'C-1', name: 'Good' }, '');
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.resolve(okJsonResponse({ id: 'C-1', name: 'Replacement' }));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        let threw = null;
        try {
            await runtime.readThroughEntity('concept', 'C-1', '/api/concepts/C-1', {
                validate: function () {
                    throw new Error('bad validator');
                },
            });
        } catch (e) {
            threw = e;
        }
        assert('a throwing validator rejects the response rather than accepting it', !!threw);
        await Promise.resolve();
        await Promise.resolve();
        assertEq('a throwing validator leaves the cache untouched', store._entities.get('concept:C-1').value, {
            id: 'C-1',
            name: 'Good',
        });
    }

    /* ---- reads without a validator keep their existing behavior (Work is unchanged) ---- */
    {
        const store = makeFakeStore();
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.resolve(okJsonResponse({ id: 'W-1', title: 'Live Work' }));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        const result = await runtime.readThroughEntity('work', 'W-1', '/api/works/W-1');
        assertEq('an unvalidated read still returns server provenance', result.source, 'server');
        await Promise.resolve();
        await Promise.resolve();
        assertEq('an unvalidated read still publishes to cache', store._entities.get('work:W-1').value, {
            id: 'W-1',
            title: 'Live Work',
        });
    }

    /* ---- domain coherence: a domain-scoped read caches normally while its generation is current ---- */
    {
        const store = makeFakeStore();
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.resolve(okJsonResponse([{ id: 'C-1', name: 'Alpha' }]));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        assertEq('a fresh domain starts at generation 0', runtime.currentDomainGeneration('concepts'), 0);
        assertEq('a fresh domain is not blocked', runtime.isDomainBlocked('concepts'), false);
        const res = await runtime.readThroughList('concepts:index', '/api/concepts', { domain: 'concepts' });
        assertEq('domain list read returns server provenance', res.source, 'server');
        await Promise.resolve();
        await Promise.resolve();
        assertEq('domain list read published its snapshot', store._lists.get('concepts:index').value, [
            { id: 'C-1', name: 'Alpha' },
        ]);
    }

    /* ---- markDomainChanged: generation bump and fallback block are synchronous ---- */
    {
        const store = makeFakeStore();
        await store.putEntity('concept', 'C-1', { id: 'C-1', name: 'old' }, '');
        await store.putEntity('concept', 'C-2', { id: 'C-2', name: 'sibling' }, '');
        await store.putList('concepts:index', [{ id: 'C-1' }, { id: 'C-2' }], '');
        let releaseDelete = null;
        const slowStore = Object.assign({}, store, {
            deleteEntitiesByKind: function (kind) {
                return new Promise(function (resolve) {
                    releaseDelete = function () {
                        Array.from(store._entities.keys()).forEach(function (key) {
                            if (key.indexOf(kind + ':') === 0) store._entities.delete(key);
                        });
                        resolve(true);
                    };
                });
            },
        });
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('offline'));
            },
            store: slowStore,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        const gen = runtime.markDomainChanged('concepts', {
            entityKinds: ['concept'],
            listKeys: ['concepts:index'],
        });
        assertEq('markDomainChanged returns the new generation', gen, 1);
        assertEq('domain generation increments synchronously', runtime.currentDomainGeneration('concepts'), 1);
        assertEq('domain is blocked synchronously', runtime.isDomainBlocked('concepts'), true);
        // The physical delete has NOT run yet -- the rows are still in the store.
        assert('rows are still physically present mid-invalidation', !!store._entities.get('concept:C-1'));
        const mutated = await runtime.readThroughEntity('concept', 'C-1', '/api/concepts/C-1', { domain: 'concepts' });
        assertEq('the mutated Concept cannot fall back to its stale row', mutated.source, 'unavailable');
        const sibling = await runtime.readThroughEntity('concept', 'C-2', '/api/concepts/C-2', { domain: 'concepts' });
        assertEq(
            'a sibling in the same domain is conservatively unavailable too (it may show the old name)',
            sibling.source,
            'unavailable'
        );
        const list = await runtime.readThroughList('concepts:index', '/api/concepts', { domain: 'concepts' });
        assertEq('the domain list cannot fall back either', list.source, 'unavailable');
        releaseDelete();
        await runtime._domainCleanup('concepts');
        assertEq('completed cleanup unblocks the domain', runtime.isDomainBlocked('concepts'), false);
        assertEq('cleanup physically removed the entity rows', store._entities.get('concept:C-1'), undefined);
        assertEq('cleanup physically removed the list row', store._lists.get('concepts:index'), undefined);
    }

    /* ---- a read begun before the mutation cannot repopulate the domain afterwards ---- */
    {
        const store = makeFakeStore();
        let releaseGet = null;
        let nextResponse = null;
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                if (nextResponse) return Promise.resolve(nextResponse);
                return new Promise(function (resolve) {
                    releaseGet = function () {
                        resolve(okJsonResponse({ id: 'C-1', name: 'pre-mutation name' }));
                    };
                });
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        const pending = runtime.readThroughEntity('concept', 'C-1', '/api/concepts/C-1', { domain: 'concepts' });
        runtime.markDomainChanged('concepts', { entityKinds: ['concept'], listKeys: ['concepts:index'] });
        await runtime._domainCleanup('concepts');
        releaseGet();
        const stale = await pending;
        assertEq('the stale response still resolves to its caller', stale.source, 'server');
        await Promise.resolve();
        await Promise.resolve();
        assertEq('a pre-mutation read never becomes eligible cache data', store._entities.get('concept:C-1'), undefined);
        // A later authoritative read, begun in the new generation on the SAME
        // runtime, caches normally once the sweep has settled the domain.
        nextResponse = okJsonResponse({ id: 'C-1', name: 'post-mutation name' });
        const fresh = await runtime.readThroughEntity('concept', 'C-1', '/api/concepts/C-1', { domain: 'concepts' });
        assertEq('a fresh-generation read still returns server data', fresh.source, 'server');
        await Promise.resolve();
        await Promise.resolve();
        assertEq(
            'a fresh-generation read may publish cache normally',
            store._entities.get('concept:C-1').value.name,
            'post-mutation name'
        );
    }

    /* ---- an older invalidation completing later must not settle a newer one ---- */
    {
        const store = makeFakeStore();
        const releases = [];
        const slowStore = Object.assign({}, store, {
            deleteEntitiesByKind: function () {
                return new Promise(function (resolve) {
                    releases.push(function () {
                        resolve(true);
                    });
                });
            },
        });
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('offline'));
            },
            store: slowStore,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        const genA = runtime.markDomainChanged('concepts', { entityKinds: ['concept'] });
        const genB = runtime.markDomainChanged('concepts', { entityKinds: ['concept'] });
        assert('the second invalidation has a newer generation', genB > genA);
        // Settle the FIRST invalidation only.
        releases[0]();
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
        assertEq(
            'a superseded invalidation completing cannot unblock the newer generation',
            runtime.isDomainBlocked('concepts'),
            true
        );
        assertEq('a superseded completion never resets the generation', runtime.currentDomainGeneration('concepts'), genB);
        releases[1]();
        await runtime._domainCleanup('concepts');
        assertEq('the current generation settles the domain normally', runtime.isDomainBlocked('concepts'), false);
    }

    /* ---- a failed cleanup leaves the domain conservatively blocked for this runtime ---- */
    {
        const store = makeFakeStore({
            deleteEntitiesByKind: async function () {
                return false;
            },
        });
        await store.putEntity('concept', 'C-1', { id: 'C-1', name: 'stale' }, '');
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('offline'));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        runtime.markDomainChanged('concepts', { entityKinds: ['concept'], listKeys: ['concepts:index'] });
        await runtime._domainCleanup('concepts');
        assertEq('a failed sweep keeps the domain blocked', runtime.isDomainBlocked('concepts'), true);
        const result = await runtime.readThroughEntity('concept', 'C-1', '/api/concepts/C-1', { domain: 'concepts' });
        assertEq(
            'unavailable beats known-stale when the disposable cleanup could not complete',
            result.source,
            'unavailable'
        );
        assertEq('online reads keep working after a failed sweep', runtime.getState(), mod.PRKS_OFFLINE_STATE_OFFLINE);
    }

    /* ---- a partially failed sweep (entities gone, list delete failed) still leaves the domain blocked ---- */
    {
        const store = makeFakeStore({
            deleteList: async function () {
                return false;
            },
        });
        await store.putList('concepts:index', [{ id: 'C-1' }], '');
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('offline'));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        runtime.markDomainChanged('concepts', { entityKinds: ['concept'], listKeys: ['concepts:index'] });
        await runtime._domainCleanup('concepts');
        assertEq(
            'the domain stays blocked when any part of its sweep did not complete',
            runtime.isDomainBlocked('concepts'),
            true
        );
        const list = await runtime.readThroughList('concepts:index', '/api/concepts', { domain: 'concepts' });
        assertEq('a list whose delete never committed is unavailable, never served stale', list.source, 'unavailable');
    }

    /* ---- MULTI-DOMAIN: generations are per-domain, never one shared counter ---- */
    {
        const store = makeFakeStore();
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('offline'));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        runtime.markDomainChanged('concepts', { entityKinds: ['concept'] });
        runtime.markDomainChanged('concepts', { entityKinds: ['concept'] });
        runtime.markDomainChanged('positions', { entityKinds: ['position'] });
        assertEq('concepts advanced only for its own invalidations', runtime.currentDomainGeneration('concepts'), 2);
        assertEq('positions advanced only for its own invalidations', runtime.currentDomainGeneration('positions'), 1);
        assertEq('an unrelated domain stays at zero', runtime.currentDomainGeneration('people'), 0);
    }

    /* ---- MULTI-DOMAIN: invalidating Positions leaves cached Concepts fully eligible ---- */
    {
        const store = makeFakeStore();
        await store.putEntity('concept', 'C-1', { id: 'C-1', name: 'Concept' }, '');
        await store.putList('concepts:index', [{ id: 'C-1' }], '');
        await store.putEntity('position', 'P-1', { id: 'P-1', name: 'Position' }, '');
        await store.putList('positions:index', [{ id: 'P-1' }], '');
        let releasePositions = null;
        const slowStore = Object.assign({}, store, {
            deleteEntitiesByKind: function (kind) {
                if (kind !== 'position') {
                    throw new Error('only the invalidated domain may sweep, got: ' + kind);
                }
                return new Promise(function (resolve) {
                    releasePositions = function () {
                        store._entities.delete('position:P-1');
                        resolve(true);
                    };
                });
            },
            deleteList: async function (key) {
                if (key !== 'positions:index') {
                    throw new Error('only the invalidated domain may sweep, got: ' + key);
                }
                store._lists.delete(key);
                return true;
            },
        });
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('offline'));
            },
            store: slowStore,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        runtime.markDomainChanged('positions', {
            entityKinds: ['position'],
            listKeys: ['positions:index'],
        });
        assertEq('the invalidated domain blocks immediately', runtime.isDomainBlocked('positions'), true);
        assertEq('the other domain is not blocked by it', runtime.isDomainBlocked('concepts'), false);
        assertEq('the other domain generation is untouched', runtime.currentDomainGeneration('concepts'), 0);

        const position = await runtime.readThroughEntity('position', 'P-1', '/api/positions/P-1', {
            domain: 'positions',
        });
        assertEq('the invalidated Position cannot serve its cached row', position.source, 'unavailable');
        const concept = await runtime.readThroughEntity('concept', 'C-1', '/api/concepts/C-1', {
            domain: 'concepts',
        });
        assertEq('a Concept in another domain still serves from cache', concept.source, 'cache');
        assertEq('the cached Concept value is intact', concept.value, { id: 'C-1', name: 'Concept' });
        const conceptList = await runtime.readThroughList('concepts:index', '/api/concepts', {
            domain: 'concepts',
        });
        assertEq('the other domain list still serves from cache', conceptList.source, 'cache');

        releasePositions();
        await runtime._domainCleanup('positions');
        assertEq('a completed sweep unblocks only its own domain', runtime.isDomainBlocked('positions'), false);
        assert('the other domain cache rows were never swept', !!store._entities.get('concept:C-1'));
        assert('the other domain list row was never swept', !!store._lists.get('concepts:index'));
    }

    /* ---- MULTI-DOMAIN: the reverse direction -- Concepts invalidation leaves Positions eligible ---- */
    {
        const store = makeFakeStore();
        await store.putEntity('concept', 'C-1', { id: 'C-1' }, '');
        await store.putEntity('position', 'P-1', { id: 'P-1', name: 'Position' }, '');
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('offline'));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        runtime.markDomainChanged('concepts', { entityKinds: ['concept'], listKeys: ['concepts:index'] });
        await runtime._domainCleanup('concepts');
        assertEq('positions is never blocked by a Concepts invalidation', runtime.isDomainBlocked('positions'), false);
        assertEq('positions generation is untouched by Concepts', runtime.currentDomainGeneration('positions'), 0);
        const position = await runtime.readThroughEntity('position', 'P-1', '/api/positions/P-1', {
            domain: 'positions',
        });
        assertEq('the cached Position still serves offline', position.source, 'cache');
        assertEq('the cached Position value is intact', position.value, { id: 'P-1', name: 'Position' });
        assertEq('the invalidated Concept row really was swept', store._entities.get('concept:C-1'), undefined);
    }

    /* ---- MULTI-DOMAIN: a failed sweep degrades only its own domain ---- */
    {
        const store = makeFakeStore({
            deleteEntitiesByKind: async function (kind) {
                if (kind === 'position') return false;
                return true;
            },
        });
        await store.putEntity('position', 'P-1', { id: 'P-1' }, '');
        await store.putEntity('concept', 'C-1', { id: 'C-1' }, '');
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('offline'));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        runtime.markDomainChanged('positions', { entityKinds: ['position'], listKeys: ['positions:index'] });
        await runtime._domainCleanup('positions');
        assertEq('a failed sweep keeps its own domain blocked', runtime.isDomainBlocked('positions'), true);
        assertEq('a failed sweep never blocks a different domain', runtime.isDomainBlocked('concepts'), false);
        const concept = await runtime.readThroughEntity('concept', 'C-1', '/api/concepts/C-1', {
            domain: 'concepts',
        });
        assertEq('the healthy domain keeps serving offline', concept.source, 'cache');
        // ... and a later Concepts invalidation can still settle normally.
        runtime.markDomainChanged('concepts', { entityKinds: ['concept'] });
        await runtime._domainCleanup('concepts');
        assertEq('the healthy domain settles normally', runtime.isDomainBlocked('concepts'), false);
        assertEq('the degraded domain is still blocked', runtime.isDomainBlocked('positions'), true);
    }

    /* ---- MULTI-DOMAIN: a superseded Positions cleanup cannot settle a newer Positions generation ---- */
    {
        const store = makeFakeStore();
        const releases = [];
        const slowStore = Object.assign({}, store, {
            deleteEntitiesByKind: function () {
                return new Promise(function (resolve) {
                    releases.push(function () {
                        resolve(true);
                    });
                });
            },
        });
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('offline'));
            },
            store: slowStore,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        // Editing one Argument really does issue several canonical requests
        // (update + targets), so overlapping Positions invalidations are normal.
        const genA = runtime.markDomainChanged('positions', { entityKinds: ['position'] });
        const genB = runtime.markDomainChanged('positions', { entityKinds: ['position'] });
        assert('the second Positions invalidation has a newer generation', genB > genA);
        releases[0]();
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
        assertEq('a superseded Positions cleanup cannot unblock the newer one', runtime.isDomainBlocked('positions'), true);
        assertEq('a superseded completion never resets the generation', runtime.currentDomainGeneration('positions'), genB);
        releases[1]();
        await runtime._domainCleanup('positions');
        assertEq('the current Positions generation settles normally', runtime.isDomainBlocked('positions'), false);
    }

    /* ---- MULTI-DOMAIN: a Position read begun before an invalidation cannot repopulate it ---- */
    {
        const store = makeFakeStore();
        let releaseGet = null;
        let nextResponse = null;
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                if (nextResponse) return Promise.resolve(nextResponse);
                return new Promise(function (resolve) {
                    releaseGet = function () {
                        resolve(okJsonResponse({ id: 'P-1', name: 'pre-mutation', arguments: [] }));
                    };
                });
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        const pending = runtime.readThroughEntity('position', 'P-1', '/api/positions/P-1', { domain: 'positions' });
        // An Argument-side mutation invalidates Positions while that GET is open.
        runtime.markDomainChanged('positions', { entityKinds: ['position'], listKeys: ['positions:index'] });
        await runtime._domainCleanup('positions');
        releaseGet();
        const stale = await pending;
        assertEq('the pre-mutation Position response still resolves to its caller', stale.source, 'server');
        await Promise.resolve();
        await Promise.resolve();
        assertEq('a pre-mutation Position read never becomes eligible cache data', store._entities.get('position:P-1'), undefined);
        nextResponse = okJsonResponse({ id: 'P-1', name: 'post-mutation', arguments: [] });
        const fresh = await runtime.readThroughEntity('position', 'P-1', '/api/positions/P-1', { domain: 'positions' });
        assertEq('a fresh-generation Position read still renders', fresh.source, 'server');
        await Promise.resolve();
        await Promise.resolve();
        assertEq(
            'a fresh-generation Position read publishes normally',
            store._entities.get('position:P-1').value.name,
            'post-mutation'
        );
    }

    /* ---- THREE DOMAINS: generations stay independent, and a sweep touches only its own ---- */
    {
        const store = makeFakeStore();
        await store.putEntity('concept', 'C-1', { id: 'C-1' }, '');
        await store.putEntity('position', 'P-1', { id: 'P-1' }, '');
        await store.putEntity('argument', 'A-1', { id: 'A-1', kind: 'argument' }, '');
        await store.putList('concepts:index', [{ id: 'C-1' }], '');
        await store.putList('positions:index', [{ id: 'P-1' }], '');
        await store.putList('arguments:index', [{ id: 'A-1' }], '');
        const sweptKinds = [];
        const sweptLists = [];
        const auditedStore = Object.assign({}, store, {
            deleteEntitiesByKind: async function (kind) {
                sweptKinds.push(kind);
                Array.from(store._entities.keys()).forEach(function (key) {
                    if (key.indexOf(kind + ':') === 0) store._entities.delete(key);
                });
                return true;
            },
            deleteList: async function (key) {
                sweptLists.push(key);
                store._lists.delete(key);
                return true;
            },
        });
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('offline'));
            },
            store: auditedStore,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        // Drive each domain a different number of times.
        runtime.markDomainChanged('concepts', { entityKinds: ['concept'], listKeys: ['concepts:index'] });
        runtime.markDomainChanged('positions', { entityKinds: ['position'], listKeys: ['positions:index'] });
        runtime.markDomainChanged('positions', { entityKinds: ['position'], listKeys: ['positions:index'] });
        const argGen = runtime.markDomainChanged('arguments', {
            entityKinds: ['argument'],
            listKeys: ['arguments:index'],
        });
        assertEq('concepts generation counts only its own', runtime.currentDomainGeneration('concepts'), 1);
        assertEq('positions generation counts only its own', runtime.currentDomainGeneration('positions'), 2);
        assertEq('arguments generation counts only its own', runtime.currentDomainGeneration('arguments'), 1);
        assertEq('markDomainChanged returned the arguments generation', argGen, 1);
        await runtime._domainCleanup('arguments');
        assertEq(
            'the arguments sweep touched exactly its own kind',
            sweptKinds.filter(function (k) {
                return k === 'argument';
            }).length,
            1
        );
        assert('the arguments sweep never touched another kind', sweptKinds.indexOf('work') === -1);
        assert(
            'the arguments sweep removed only its own list key',
            sweptLists.indexOf('arguments:index') !== -1
        );
    }

    /* ---- THREE DOMAINS: an Arguments invalidation leaves Concepts and Positions eligible ---- */
    {
        const store = makeFakeStore();
        await store.putEntity('concept', 'C-1', { id: 'C-1', name: 'Concept' }, '');
        await store.putEntity('position', 'P-1', { id: 'P-1', name: 'Position' }, '');
        await store.putEntity('argument', 'A-1', { id: 'A-1', name: 'Argument' }, '');
        const auditedStore = Object.assign({}, store, {
            deleteEntitiesByKind: async function (kind) {
                if (kind !== 'argument') {
                    throw new Error('only the invalidated domain may sweep, got: ' + kind);
                }
                store._entities.delete('argument:A-1');
                return true;
            },
            deleteList: async function (key) {
                if (key !== 'arguments:index') {
                    throw new Error('only the invalidated domain may sweep, got: ' + key);
                }
                return true;
            },
        });
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('offline'));
            },
            store: auditedStore,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        runtime.markDomainChanged('arguments', {
            entityKinds: ['argument'],
            listKeys: ['arguments:index'],
        });
        assertEq('arguments blocks immediately', runtime.isDomainBlocked('arguments'), true);
        assertEq('concepts is untouched by an Arguments invalidation', runtime.isDomainBlocked('concepts'), false);
        assertEq('positions is untouched by an Arguments invalidation', runtime.isDomainBlocked('positions'), false);
        const arg = await runtime.readThroughEntity('argument', 'A-1', '/api/arguments/A-1', { domain: 'arguments' });
        assertEq('the invalidated Argument cannot serve its cached row', arg.source, 'unavailable');
        const concept = await runtime.readThroughEntity('concept', 'C-1', '/api/concepts/C-1', { domain: 'concepts' });
        assertEq('a Concept in another domain still serves from cache', concept.source, 'cache');
        const position = await runtime.readThroughEntity('position', 'P-1', '/api/positions/P-1', {
            domain: 'positions',
        });
        assertEq('a Position in another domain still serves from cache', position.source, 'cache');
        await runtime._domainCleanup('arguments');
        assertEq('a completed Arguments sweep unblocks only Arguments', runtime.isDomainBlocked('arguments'), false);
        assert('the Concept row was never swept', !!store._entities.get('concept:C-1'));
        assert('the Position row was never swept', !!store._entities.get('position:P-1'));
    }

    /* ---- THREE DOMAINS: a failed Arguments sweep degrades only Arguments ---- */
    {
        const store = makeFakeStore({
            deleteEntitiesByKind: async function (kind) {
                return kind !== 'argument';
            },
        });
        await store.putEntity('argument', 'A-1', { id: 'A-1' }, '');
        await store.putEntity('concept', 'C-1', { id: 'C-1' }, '');
        await store.putEntity('position', 'P-1', { id: 'P-1' }, '');
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('offline'));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        runtime.markDomainChanged('arguments', { entityKinds: ['argument'], listKeys: ['arguments:index'] });
        await runtime._domainCleanup('arguments');
        assertEq('a failed Arguments sweep keeps Arguments blocked', runtime.isDomainBlocked('arguments'), true);
        assertEq('a failed Arguments sweep never blocks Concepts', runtime.isDomainBlocked('concepts'), false);
        assertEq('a failed Arguments sweep never blocks Positions', runtime.isDomainBlocked('positions'), false);
        const concept = await runtime.readThroughEntity('concept', 'C-1', '/api/concepts/C-1', { domain: 'concepts' });
        assertEq('the healthy Concepts domain keeps serving offline', concept.source, 'cache');
        const position = await runtime.readThroughEntity('position', 'P-1', '/api/positions/P-1', {
            domain: 'positions',
        });
        assertEq('the healthy Positions domain keeps serving offline', position.source, 'cache');
    }

    /* ---- THREE DOMAINS: a superseded Arguments cleanup cannot settle a newer generation ---- */
    {
        const releases = [];
        const store = makeFakeStore({
            deleteEntitiesByKind: function () {
                return new Promise(function (resolve) {
                    releases.push(function () {
                        resolve(true);
                    });
                });
            },
        });
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('offline'));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        // One Argument save legitimately issues updateArgument + putArgumentTargets
        // + putArgumentSources, so overlapping invalidations are the normal case.
        const genA = runtime.markDomainChanged('arguments', { entityKinds: ['argument'] });
        const genB = runtime.markDomainChanged('arguments', { entityKinds: ['argument'] });
        assert('the second Arguments invalidation has a newer generation', genB > genA);
        releases[0]();
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
        assertEq('a superseded Arguments cleanup cannot unblock the newer one', runtime.isDomainBlocked('arguments'), true);
        assertEq('a superseded completion never resets the generation', runtime.currentDomainGeneration('arguments'), genB);
        releases[1]();
        await runtime._domainCleanup('arguments');
        assertEq('the current Arguments generation settles normally', runtime.isDomainBlocked('arguments'), false);
    }

    /* ---- THREE DOMAINS: pre-invalidation Argument reads (entity AND list) cannot publish ---- */
    {
        const store = makeFakeStore();
        let releaseEntity = null;
        let releaseList = null;
        let mode = 'entity';
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                if (mode === 'entity') {
                    return new Promise(function (resolve) {
                        releaseEntity = function () {
                            resolve(okJsonResponse({ id: 'A-1', name: 'pre-mutation' }));
                        };
                    });
                }
                return new Promise(function (resolve) {
                    releaseList = function () {
                        resolve(okJsonResponse([{ id: 'A-1', name: 'pre-mutation' }]));
                    };
                });
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        const pendingEntity = runtime.readThroughEntity('argument', 'A-1', '/api/arguments/A-1', {
            domain: 'arguments',
        });
        mode = 'list';
        const pendingList = runtime.readThroughList('arguments:index', '/api/arguments', { domain: 'arguments' });
        runtime.markDomainChanged('arguments', {
            entityKinds: ['argument'],
            listKeys: ['arguments:index'],
        });
        await runtime._domainCleanup('arguments');
        releaseEntity();
        releaseList();
        const staleEntity = await pendingEntity;
        const staleList = await pendingList;
        assertEq('the pre-mutation Argument entity response still resolves', staleEntity.source, 'server');
        assertEq('the pre-mutation Argument list response still resolves', staleList.source, 'server');
        await Promise.resolve();
        await Promise.resolve();
        assertEq(
            'a pre-mutation Argument entity read never becomes eligible cache data',
            store._entities.get('argument:A-1'),
            undefined
        );
        assertEq(
            'a pre-mutation Argument list read never becomes eligible cache data',
            store._lists.get('arguments:index'),
            undefined
        );
    }

    /* ---- FOUR DOMAINS: a fourth domain reveals no hard-coded assumptions about three ---- */
    {
        const store = makeFakeStore();
        await store.putEntity('concept', 'C-1', { id: 'C-1' }, '');
        await store.putEntity('position', 'P-1', { id: 'P-1' }, '');
        await store.putEntity('argument', 'A-1', { id: 'A-1' }, '');
        await store.putEntity('person', 'PE-1', { id: 'PE-1', name: 'Person' }, '');
        await store.putList('people:index', [{ id: 'PE-1' }], '');
        const sweptKinds = [];
        const sweptLists = [];
        const auditedStore = Object.assign({}, store, {
            deleteEntitiesByKind: async function (kind) {
                sweptKinds.push(kind);
                Array.from(store._entities.keys()).forEach(function (key) {
                    if (key.indexOf(kind + ':') === 0) store._entities.delete(key);
                });
                return true;
            },
            deleteList: async function (key) {
                sweptLists.push(key);
                store._lists.delete(key);
                return true;
            },
        });
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('offline'));
            },
            store: auditedStore,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        runtime.markDomainChanged('concepts', { entityKinds: ['concept'] });
        runtime.markDomainChanged('positions', { entityKinds: ['position'] });
        runtime.markDomainChanged('positions', { entityKinds: ['position'] });
        runtime.markDomainChanged('arguments', { entityKinds: ['argument'] });
        runtime.markDomainChanged('arguments', { entityKinds: ['argument'] });
        runtime.markDomainChanged('arguments', { entityKinds: ['argument'] });
        const peopleGen = runtime.markDomainChanged('people', {
            entityKinds: ['person'],
            listKeys: ['people:index'],
        });
        assertEq('four domains keep four independent generations', [
            runtime.currentDomainGeneration('concepts'),
            runtime.currentDomainGeneration('positions'),
            runtime.currentDomainGeneration('arguments'),
            runtime.currentDomainGeneration('people'),
        ], [1, 2, 3, 1]);
        assertEq('markDomainChanged returned the people generation', peopleGen, 1);
        await runtime._domainCleanup('people');
        assert('the people sweep touched its own kind', sweptKinds.indexOf('person') !== -1);
        assert('the people sweep removed only its own list key', sweptLists.every(function (k) {
            return k === 'people:index';
        }));
        assertEq('the people sweep removed the person row', store._entities.get('person:PE-1'), undefined);
        assertEq('a completed people sweep unblocks only people', runtime.isDomainBlocked('people'), false);
        assertEq('a people invalidation never advances another domain', runtime.currentDomainGeneration('concepts'), 1);
        // Each domain swept exactly its own kind, once per invalidation, and
        // nothing else -- no domain reached into another's entities.
        assertEq('every sweep stayed inside its own domain', sweptKinds.slice().sort(), [
            'argument',
            'argument',
            'argument',
            'concept',
            'person',
            'position',
            'position',
        ]);
        assertEq('only the domain with a list key swept a list', sweptLists, ['people:index']);
    }

    /* ---- FOUR DOMAINS: a failed People sweep degrades only People ---- */
    {
        const store = makeFakeStore({
            deleteEntitiesByKind: async function (kind) {
                return kind !== 'person';
            },
        });
        await store.putEntity('person', 'PE-1', { id: 'PE-1' }, '');
        await store.putEntity('argument', 'A-1', { id: 'A-1' }, '');
        await store.putEntity('concept', 'C-1', { id: 'C-1' }, '');
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('offline'));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        runtime.markDomainChanged('people', { entityKinds: ['person'], listKeys: ['people:index'] });
        await runtime._domainCleanup('people');
        assertEq('a failed People sweep keeps People blocked', runtime.isDomainBlocked('people'), true);
        assertEq('a failed People sweep never blocks Arguments', runtime.isDomainBlocked('arguments'), false);
        assertEq('a failed People sweep never blocks Concepts', runtime.isDomainBlocked('concepts'), false);
        const person = await runtime.readThroughEntity('person', 'PE-1', '/api/persons/PE-1', { domain: 'people' });
        assertEq('People degrade to unavailable, never known-stale', person.source, 'unavailable');
        const argument = await runtime.readThroughEntity('argument', 'A-1', '/api/arguments/A-1', {
            domain: 'arguments',
        });
        assertEq('the healthy Arguments domain keeps serving offline', argument.source, 'cache');
        // A later Concepts invalidation still settles normally alongside it.
        runtime.markDomainChanged('concepts', { entityKinds: ['concept'] });
        await runtime._domainCleanup('concepts');
        assertEq('another domain settles while People stays degraded', runtime.isDomainBlocked('concepts'), false);
        assertEq('People is still degraded', runtime.isDomainBlocked('people'), true);
    }

    /* ---- FOUR DOMAINS: superseded People cleanup + stale People reads (list AND entity) ---- */
    {
        const store = makeFakeStore();
        const releases = [];
        let releaseEntity = null;
        let releaseList = null;
        let mode = 'entity';
        const slowStore = Object.assign({}, store, {
            deleteEntitiesByKind: function () {
                return new Promise(function (resolve) {
                    releases.push(function () {
                        resolve(true);
                    });
                });
            },
        });
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                if (mode === 'entity') {
                    return new Promise(function (resolve) {
                        releaseEntity = function () {
                            resolve(okJsonResponse({ id: 'PE-1', name: 'pre-mutation' }));
                        };
                    });
                }
                return new Promise(function (resolve) {
                    releaseList = function () {
                        resolve(okJsonResponse([{ id: 'PE-1' }]));
                    };
                });
            },
            store: slowStore,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        const pendingEntity = runtime.readThroughEntity('person', 'PE-1', '/api/persons/PE-1', { domain: 'people' });
        mode = 'list';
        const pendingList = runtime.readThroughList('people:index', '/api/persons', { domain: 'people' });
        // A role mutation, then a Work metadata save: overlapping People
        // invalidations are the normal case here.
        const genA = runtime.markDomainChanged('people', { entityKinds: ['person'] });
        const genB = runtime.markDomainChanged('people', { entityKinds: ['person'] });
        assert('the second People invalidation has a newer generation', genB > genA);
        releases[0]();
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
        assertEq('a superseded People cleanup cannot unblock the newer one', runtime.isDomainBlocked('people'), true);
        releases[1]();
        await runtime._domainCleanup('people');
        assertEq('the current People generation settles normally', runtime.isDomainBlocked('people'), false);
        releaseEntity();
        releaseList();
        const staleEntity = await pendingEntity;
        const staleList = await pendingList;
        assertEq('the pre-mutation Person response still resolves', staleEntity.source, 'server');
        assertEq('the pre-mutation People list response still resolves', staleList.source, 'server');
        await Promise.resolve();
        await Promise.resolve();
        assertEq('a pre-mutation Person read never becomes eligible cache data', store._entities.get('person:PE-1'), undefined);
        assertEq('a pre-mutation People list read never becomes eligible cache data', store._lists.get('people:index'), undefined);
    }

    /* ---- the People domain shape is defined once, alongside the other three ---- */
    {
        const globalRoot = typeof globalThis !== 'undefined' ? globalThis : this;
        assert(
            'prksOfflineMarkPeopleChanged is exported for every canonical caller to share',
            typeof globalRoot.prksOfflineMarkPeopleChanged === 'function'
        );
        assertEq('the People domain has a stable name', mod.PRKS_OFFLINE_DOMAIN_PEOPLE, 'people');
        assertEq('the People index uses a stable list key', mod.PRKS_OFFLINE_PEOPLE_LIST_KEY, 'people:index');
        const names = [
            mod.PRKS_OFFLINE_DOMAIN_CONCEPTS,
            mod.PRKS_OFFLINE_DOMAIN_POSITIONS,
            mod.PRKS_OFFLINE_DOMAIN_ARGUMENTS,
            mod.PRKS_OFFLINE_DOMAIN_PEOPLE,
        ];
        assertEq('the four domains are four distinct names', new Set(names).size, 4);
    }

    /* ---- the Arguments domain shape is defined once, alongside the other two ---- */
    {
        const globalRoot = typeof globalThis !== 'undefined' ? globalThis : this;
        assert(
            'prksOfflineMarkArgumentsChanged is exported for every canonical caller to share',
            typeof globalRoot.prksOfflineMarkArgumentsChanged === 'function'
        );
        assertEq('the Arguments domain has a stable name', mod.PRKS_OFFLINE_DOMAIN_ARGUMENTS, 'arguments');
        assertEq('the Argument index uses a stable list key', mod.PRKS_OFFLINE_ARGUMENTS_LIST_KEY, 'arguments:index');
        const names = [
            mod.PRKS_OFFLINE_DOMAIN_CONCEPTS,
            mod.PRKS_OFFLINE_DOMAIN_POSITIONS,
            mod.PRKS_OFFLINE_DOMAIN_ARGUMENTS,
        ];
        assertEq('the three domains are three distinct names', new Set(names).size, 3);
    }

    /* ---- the Positions domain shape is defined once, alongside (never merged with) Concepts ---- */
    {
        const globalRoot = typeof globalThis !== 'undefined' ? globalThis : this;
        assert(
            'prksOfflineMarkPositionsChanged is exported for every canonical caller to share',
            typeof globalRoot.prksOfflineMarkPositionsChanged === 'function'
        );
        assertEq('the Positions domain has a stable name', mod.PRKS_OFFLINE_DOMAIN_POSITIONS, 'positions');
        assertEq('the Position index uses a stable list key', mod.PRKS_OFFLINE_POSITIONS_LIST_KEY, 'positions:index');
        assert(
            'the two domains are distinct names',
            mod.PRKS_OFFLINE_DOMAIN_POSITIONS !== mod.PRKS_OFFLINE_DOMAIN_CONCEPTS
        );
    }

    /* ---- reads with no domain are unaffected by another domain's invalidation ---- */
    {
        const store = makeFakeStore();
        await store.putEntity('work', 'W-1', { id: 'W-1', title: 'Cached Work' }, '');
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return Promise.reject(new Error('offline'));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        runtime.markDomainChanged('concepts', { entityKinds: ['concept'], listKeys: ['concepts:index'] });
        const work = await runtime.readThroughEntity('work', 'W-1', '/api/works/W-1');
        assertEq('Work offline behavior is unchanged by a Concepts invalidation', work.source, 'cache');
        assertEq('the cached Work value is still served', work.value, { id: 'W-1', title: 'Cached Work' });
    }

    /* ---- the Concepts domain shape is defined once, and covers list + entities together ---- */
    {
        const globalRoot = typeof globalThis !== 'undefined' ? globalThis : this;
        assert(
            'prksOfflineMarkConceptsChanged is exported for every canonical caller to share',
            typeof globalRoot.prksOfflineMarkConceptsChanged === 'function'
        );
        assertEq('the Concepts domain has a stable name', mod.PRKS_OFFLINE_DOMAIN_CONCEPTS, 'concepts');
        assertEq('the Concept index uses a stable list key', mod.PRKS_OFFLINE_CONCEPTS_LIST_KEY, 'concepts:index');
    }

    /* ---- module boundaries: no DOM writes, no canonical persistence, memory-only re: request coordinator ---- */
    const src = require('fs').readFileSync(path.join(rootDir, 'frontend/js/offline-runtime.js'), 'utf8');
    assert('offline-runtime.js does not create its own IndexedDB connection', src.indexOf('indexedDB.open') === -1);
    assert('offline-runtime.js delegates persistence to createPrksOfflineStore', src.indexOf('createPrksOfflineStore') !== -1);

    if (failed) {
        console.log(failed + ' failed, ' + passed + ' passed');
        process.exit(1);
    }
    console.log('All ' + passed + ' offline runtime checks passed, 0 failed');
    process.exit(0);
}

run().catch(function (err) {
    console.error(err && err.stack ? err.stack : err);
    console.log('1 failed, ' + passed + ' passed');
    process.exit(1);
});
