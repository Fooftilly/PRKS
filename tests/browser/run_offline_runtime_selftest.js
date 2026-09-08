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
