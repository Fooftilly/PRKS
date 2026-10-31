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
require(path.join(rootDir, 'frontend/js/folder-state.js'));

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
        flushDelay: function (delay) {
            const entries = Array.from(pending.entries()).filter(function (e) {
                return e[1].delay === delay;
            });
            entries.forEach(function (e) {
                pending.delete(e[0]);
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

async function verifyCanonicalFolderFieldAcknowledgements() {
    // These are cache ACK paths, not pending drafts: server canonical_wire
    // strips Python whitespace and substitutes a title for an empty result.
    const store = makeFakeStore({ isAvailable: async () => true });
    const runtime = mod.createPrksOfflineRuntime({
        store, prksRequest: () => Promise.reject(Error('offline')),
        setTimeout: noopSetTimeout, clearTimeout: noopClearTimeout,
        window: null, caches: null, navigator: null,
    });
    const id = 'F-CANONICAL';
    const untouched = { id: 'F-OTHER', title: 'Unchanged', description: 'Keep' };
    const cases = [
        ['title', ' \t Reading list \u00a0 ', 'Reading list'],
        ['title', ' \u001c \u001f ', 'Untitled Folder'],
        ['description', ' \u001c Description \u001f ', 'Description'],
        ['description', ' \t \u00a0 ', ''],
        ['private_notes', ' \t Notes \u00a0 ', 'Notes'],
    ];
    for (const [field, raw, expected] of cases) {
        const base = { id, title: 'Old title', description: 'Old description',
            private_notes: 'Old notes', parent_id: null };
        // List and detail are distinct snapshots: an accidental in-place
        // mutation in one projection must not satisfy the other's assertion.
        await store.putList('folders:index', [{ ...base }, { ...untouched }], '');
        await store.putEntity('folder', id, { ...base }, '');
        const reconciled = await runtime.reconcileFolderField({
            code: 'ACKNOWLEDGED', folder_id: id, field,
            server_revision: 7, member_work_ids: [],
        }, { payload: { value: raw } });
        assertEq(field + ' canonical ACK applied', reconciled, true);
        const list = (await store.getList('folders:index')).value;
        assertEq(field + ' canonical Folder list row', list[0][field], expected);
        assertEq(field + ' other Folder untouched', list[1], untouched);
        const detail = (await store.getEntity('folder', id)).value;
        assertEq(field + ' canonical Folder detail', detail[field], expected);
    }
    // Parent IDs are pointers, not text; the ACK invalidates stale parent
    // objects in the detail cache instead of treating their ID as a title.
    await store.putList('folders:index', [{ id, parent_id: null }], '');
    await store.putEntity('folder', id, { id, parent: { id: 'F-OLD' } }, '');
    const moved = await runtime.reconcileFolderField({
        code: 'ACKNOWLEDGED', folder_id: id,
        field: 'parent_id', server_revision: 8,
    }, { payload: { value: 'F-PARENT' } });
    assertEq('parent-id ACK applied', moved, true);
    assertEq('parent-id list row retains the pointer',
        (await store.getList('folders:index')).value[0].parent_id, 'F-PARENT');
    assertEq('parent-id ACK invalidates stale detail', await store.getEntity('folder', id), null);
}

async function run() {
    await verifyCanonicalFolderFieldAcknowledgements();
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
    /* Sixth-domain cleanup is audited: no sweep may touch another domain. */
    {
        const domains = ['concepts', 'positions', 'arguments', 'people', 'person-groups', 'playlists'];
        const kinds = ['concept', 'position', 'argument', 'person', 'person-group', 'playlist'];
        const sweeps = [];
        const store = makeFakeStore({
            deleteEntitiesByKind: async kind => {
                sweeps.push(kind);
                if (kind !== 'playlist') throw Error('cross-domain entity sweep');
                return false;
            },
            deleteList: async key => {
                sweeps.push(key);
                if (key !== 'playlists:index') throw Error('cross-domain list sweep');
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
        runtime.markDomainChanged('playlists', {
            entityKinds:['playlist'], listKeys:['playlists:index'],
        });
        await runtime._domainCleanup('playlists');
        assertEq('Playlist cleanup touches exactly its kind and key', sweeps.sort(), ['playlist','playlists:index']);
        assertEq('six generations remain independent',
            domains.map(d=>runtime.currentDomainGeneration(d)), [0,0,0,0,0,1]);
        assertEq('only Playlists blocked after failed cleanup',
            domains.map(d=>runtime.isDomainBlocked(d)), [false,false,false,false,false,true]);
        for (let i=0; i<domains.length; i++) {
            const result = await runtime.readThroughEntity(kinds[i], '1', '/api/test', {domain:domains[i]});
            assertEq(domains[i] + ' fallback isolation', result.source, i===5 ? 'unavailable' : 'cache');
        }
        assertEq('Works unaffected by Playlist cleanup',
            (await runtime.readThroughEntity('work','1','/api/test')).source, 'cache');
        assertEq('Playlist constant', mod.PRKS_OFFLINE_DOMAIN_PLAYLISTS, 'playlists');
        assertEq('Playlist list constant', mod.PRKS_OFFLINE_PLAYLISTS_LIST_KEY, 'playlists:index');
        assert('Playlist canonical runtime helper exported',
            typeof mod.prksOfflineMarkPlaylistsChanged === 'function');
    }
    /* Folders is the ninth domain (Graph contributes two). Same audit: its
     * sweep must be kind-exact and every other domain must stay usable. */
    {
        const domains = ['concepts', 'positions', 'arguments', 'people', 'person-groups',
                         'playlists', 'folders'];
        const kinds = ['concept', 'position', 'argument', 'person', 'person-group',
                       'playlist', 'folder'];
        const sweeps = [];
        const store = makeFakeStore({
            deleteEntitiesByKind: async kind => {
                sweeps.push(kind);
                if (kind !== 'folder') throw Error('cross-domain entity sweep');
                return false;
            },
            deleteList: async key => {
                sweeps.push(key);
                if (key !== 'folders:index') throw Error('cross-domain list sweep');
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
        runtime.markDomainChanged('folders', {
            entityKinds:['folder'], listKeys:['folders:index'],
        });
        await runtime._domainCleanup('folders');
        assertEq('Folder cleanup touches exactly its kind and key', sweeps.sort(), ['folder','folders:index']);
        assertEq('Folder generation is independent',
            domains.map(d=>runtime.currentDomainGeneration(d)), [0,0,0,0,0,0,1]);
        assertEq('only Folders blocked after failed cleanup',
            domains.map(d=>runtime.isDomainBlocked(d)), [false,false,false,false,false,false,true]);
        for (let i=0; i<domains.length; i++) {
            const result = await runtime.readThroughEntity(kinds[i], '1', '/api/test', {domain:domains[i]});
            assertEq(domains[i] + ' fallback isolation', result.source, i===6 ? 'unavailable' : 'cache');
        }
        for (let i=0; i<domains.length; i++) {
            const result = await runtime.readThroughList(
                domains[i] + ':index', '/api/test', {domain:domains[i]});
            assertEq(domains[i] + ' list fallback isolation',
                result.source, i===6 ? 'unavailable' : 'cache');
        }
        assertEq('Works unaffected by Folder cleanup',
            (await runtime.readThroughEntity('work','1','/api/test')).source, 'cache');
        assertEq('Folder constant', mod.PRKS_OFFLINE_DOMAIN_FOLDERS, 'folders');
        assertEq('Folder list constant', mod.PRKS_OFFLINE_FOLDERS_LIST_KEY, 'folders:index');
        assert('Folder canonical runtime helper exported',
            typeof mod.prksOfflineMarkFoldersChanged === 'function');
    }
    /* The three browse projections are LIST-ONLY domains and must be mutually
     * independent: opening a Work may cost `recent` and nothing else. */
    {
        const domains = ['works-browse', 'recent', 'recently-added', 'folders'];
        const keys = ['works-browse:index', 'recent:index', 'recently-added:index', 'folders:index'];
        const sweeps = [];
        const store = makeFakeStore({
            deleteEntitiesByKind: async kind => {
                sweeps.push('KIND:' + kind);
                throw Error('a list-only domain must sweep no entity kind');
            },
            deleteList: async key => {
                sweeps.push(key);
                if (key !== 'recent:index') throw Error('cross-domain list sweep');
                return false;
            },
        });
        for (const kind of ['work', 'folder', 'concept']) await store.putEntity(kind, '1', {id:'1'}, '');
        for (const key of keys) await store.putList(key, [], '');
        const runtime = mod.createPrksOfflineRuntime({
            store, prksRequest: () => Promise.reject(Error('offline')),
            setTimeout: noopSetTimeout, clearTimeout: noopClearTimeout,
            window: null, caches: null, navigator: null,
        });
        // The explicit open event stamps last_opened_at -> Recent only.
        runtime.markDomainChanged('recent', { entityKinds: [], listKeys: ['recent:index'] });
        await runtime._domainCleanup('recent');
        assertEq('Recent cleanup touches exactly its own key', sweeps.sort(), ['recent:index']);
        assertEq('browse generations stay independent',
            domains.map(d => runtime.currentDomainGeneration(d)), [0, 1, 0, 0]);
        assertEq('only Recent blocked after a failed sweep',
            domains.map(d => runtime.isDomainBlocked(d)), [false, true, false, false]);
        for (let i = 0; i < keys.length; i++) {
            const result = await runtime.readThroughList(keys[i], '/api/test', { domain: domains[i] });
            assertEq(domains[i] + ' fallback isolation', result.source, i === 1 ? 'unavailable' : 'cache');
        }
        assertEq('a Work entity is untouched by a Recent sweep',
            (await runtime.readThroughEntity('work', '1', '/api/test')).source, 'cache');
        assertEq('works-browse constant', mod.PRKS_OFFLINE_DOMAIN_WORKS_BROWSE, 'works-browse');
        assertEq('works-browse list constant', mod.PRKS_OFFLINE_WORKS_BROWSE_LIST_KEY, 'works-browse:index');
        assertEq('recent constant', mod.PRKS_OFFLINE_DOMAIN_RECENT, 'recent');
        assertEq('recent list constant', mod.PRKS_OFFLINE_RECENT_LIST_KEY, 'recent:index');
        assertEq('recently-added constant', mod.PRKS_OFFLINE_DOMAIN_RECENTLY_ADDED, 'recently-added');
        assertEq('recently-added list constant', mod.PRKS_OFFLINE_RECENTLY_ADDED_LIST_KEY, 'recently-added:index');
        for (const fn of ['prksOfflineMarkWorksBrowseChanged', 'prksOfflineMarkRecentChanged',
                          'prksOfflineMarkRecentlyAddedChanged']) {
            assert(fn + ' exported', typeof mod[fn] === 'function');
        }
    }
    /* A pre-mutation browse read must not repopulate an invalidated list. */
    {
        const store = makeFakeStore();
        const gate = {};
        gate.promise = new Promise(resolve => { gate.resolve = resolve; });
        const runtime = mod.createPrksOfflineRuntime({
            store,
            prksRequest: async () => { await gate.promise; return jsonResponse(200, [{id:'W-1'}]); },
            setTimeout: noopSetTimeout, clearTimeout: noopClearTimeout,
            window: null, caches: null, navigator: null,
        });
        const pending = runtime.readThroughList(
            'works-browse:index', '/api/works?projection=browse', { domain: 'works-browse' });
        runtime.markDomainChanged('works-browse', {
            entityKinds: [], listKeys: ['works-browse:index'],
        });
        gate.resolve();
        await pending;
        assertEq('stale browse GET does not repopulate',
            await store.getList('works-browse:index'), null);
    }
    /* A list read that starts during the current sweep still publishes once
     * that sweep's delete has committed, but the server value returns first.
     * Showing Recent after an open hits this: the open blocks recent:index,
     * the page fetches, and the write used to be dropped for the whole
     * blocked window. Paint must not wait on the delete. */
    {
        const store = makeFakeStore();
        let releaseDelete = null;
        const slowStore = Object.assign({}, store, {
            deleteList: function (key) {
                return new Promise(function (resolve) {
                    releaseDelete = function () {
                        resolve(store.deleteList(key));
                    };
                });
            },
        });
        const runtime = mod.createPrksOfflineRuntime({
            store: slowStore,
            prksRequest: async () => okJsonResponse([{ id: 'W-1', last_opened_at: '2026-01-01' }]),
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null,
            caches: null,
            navigator: null,
        });
        runtime.markDomainChanged('recent', { entityKinds: [], listKeys: ['recent:index'] });
        const result = await runtime.readThroughList('recent:index', '/api/recent', { domain: 'recent' });
        assertEq('an in-sweep Recent read returns server data before the delete', result.source, 'server');
        assert('the Recent sweep is still holding the list delete', typeof releaseDelete === 'function');
        assertEq('Recent is not cached while the sweep still holds the delete',
            await store.getList('recent:index'), null);
        releaseDelete();
        await runtime._domainCleanup('recent');
        for (let i = 0; i < 6; i++) await Promise.resolve();
        const cached = await store.getList('recent:index');
        assertEq('Recent publishes after the sweep deletes the old list',
            cached && cached.value, [{ id: 'W-1', last_opened_at: '2026-01-01' }]);
    }
    /* A pre-mutation Folder read -- list AND entity -- can still resolve and
     * repaint, but must never become eligible cache data. */
    {
        const store = makeFakeStore();
        const gate = {};
        gate.promise = new Promise(resolve => { gate.resolve = resolve; });
        const runtime = mod.createPrksOfflineRuntime({
            store,
            prksRequest: async () => { await gate.promise; return jsonResponse(200, {id:'F-1'}); },
            setTimeout: noopSetTimeout, clearTimeout: noopClearTimeout,
            window: null, caches: null, navigator: null,
        });
        const pendingList = runtime.readThroughList('folders:index', '/api/folders', { domain: 'folders' });
        const pendingEntity = runtime.readThroughEntity('folder', 'F-1', '/api/folders/F-1', { domain: 'folders' });
        runtime.markDomainChanged('folders', {
            entityKinds:['folder'], listKeys:['folders:index'],
        });
        gate.resolve();
        await pendingList;
        await pendingEntity;
        assertEq('stale Folder list GET does not repopulate', await store.getList('folders:index'), null);
        assertEq('stale Folder entity GET does not repopulate', await store.getEntity('folder','F-1'), null);
    }
    /* A pre-mutation Playlist read -- list AND entity -- can still resolve and
     * repaint, but must never become eligible cache data. */
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
                            resolve(okJsonResponse({ id: 'PL-1', title: 'pre-mutation', items: [] }));
                        };
                    });
                }
                return new Promise(function (resolve) {
                    releaseList = function () { resolve(okJsonResponse([{ id: 'PL-1', item_count: 0 }])); };
                });
            },
            store, setTimeout: noopSetTimeout, clearTimeout: noopClearTimeout,
            window: null, caches: null, navigator: null,
        });
        const pendingEntity = runtime.readThroughEntity('playlist', 'PL-1', '/api/playlists/PL-1', { domain: 'playlists' });
        mode = 'list';
        const pendingList = runtime.readThroughList('playlists:index', '/api/playlists', { domain: 'playlists' });
        runtime.markDomainChanged('playlists', { entityKinds: ['playlist'], listKeys: ['playlists:index'] });
        await runtime._domainCleanup('playlists');
        releaseEntity();
        releaseList();
        const staleEntity = await pendingEntity;
        const staleList = await pendingList;
        assertEq('the pre-mutation Playlist response still resolves', staleEntity.source, 'server');
        assertEq('the pre-mutation Playlists list response still resolves', staleList.source, 'server');
        await Promise.resolve();
        await Promise.resolve();
        assertEq('a pre-mutation Playlist read never becomes eligible cache data',
            store._entities.get('playlist:PL-1'), undefined);
        assertEq('a pre-mutation Playlists list read never becomes eligible cache data',
            store._lists.get('playlists:index'), undefined);
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

    /* ---- stale in-flight probe cannot undo newer confirmed API reachability (#818) ---- */
    {
        function makeDeferredProbeFixture() {
            const timers = makeFakeTimers();
            const deferred = [];
            const runtime = mod.createPrksOfflineRuntime({
                prksRequest: function () {
                    return new Promise(function (resolve, reject) {
                        deferred.push({ resolve: resolve, reject: reject });
                    });
                },
                store: makeFakeStore(),
                setTimeout: timers.setTimeout,
                clearTimeout: timers.clearTimeout,
                window: null, caches: null, navigator: null,
            });
            runtime.noteRequestFailure();
            timers.flushAll();
            assertEq('probe race fixture starts reconnecting', runtime.getState(), 'reconnecting');
            assertEq('probe race fixture dispatches one reachability request', deferred.length, 1);
            return { timers: timers, deferred: deferred, runtime: runtime };
        }
        // Rejection of a probe started BEFORE a successful API request is
        // stale evidence. It cannot downgrade online or schedule more probes.
        {
            const c = makeDeferredProbeFixture();
            c.runtime.noteRequestSuccess();
            assertEq('newer foreground response restores online', c.runtime.getState(), 'online');
            c.deferred[0].reject(new TypeError('old reachability probe failed'));
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            assertEq('stale failed probe leaves newer online state intact', c.runtime.getState(), 'online');
            assertEq('stale failed probe schedules no retry', c.timers.pendingCount(), 0);
            assertEq('sync/mutation gate stays open after newer success', c.runtime.isMutationBlocked(), false);
            // Clearing stale probeInFlight is essential: a subsequent real
            // browser connectivity hint must be able to start a new probe.
            c.runtime._runProbe();
            assertEq('new hint may start a fresh probe', c.deferred.length, 2);
            assertEq('fresh hint probes rather than trusting navigator', c.runtime.getState(), 'reconnecting');
            c.deferred[1].resolve(okJsonResponse({}));
            await Promise.resolve();
            await Promise.resolve();
            assertEq('fresh successful probe correctly restores online', c.runtime.getState(), 'online');
        }
        // A falsy/empty result is transport-unreachable evidence too; it is
        // equally stale once a newer response confirmed server reachability.
        {
            const c = makeDeferredProbeFixture();
            c.runtime.noteRequestSuccess();
            c.deferred[0].resolve(null);
            await Promise.resolve();
            await Promise.resolve();
            assertEq('stale falsy probe cannot override newer success', c.runtime.getState(), 'online');
            assertEq('stale falsy probe schedules no backoff', c.timers.pendingCount(), 0);
        }
        // A genuinely LATER foreground failure must still put PRKS offline.
        // The obsolete older probe must not reschedule or extend backoff.
        {
            const c = makeDeferredProbeFixture();
            c.runtime.noteRequestSuccess();
            c.runtime.noteRequestFailure();
            assertEq('newer transport failure still marks offline', c.runtime.getState(), 'offline');
            assertEq('newer failure owns first retry alongside older probe watchdog',
                c.timers.pendingDelays(), [15000, 3000]);
            c.deferred[0].reject(new TypeError('obsolete probe rejection'));
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            assertEq('obsolete probe cannot change newer offline state', c.runtime.getState(), 'offline');
            assertEq('obsolete probe cannot extend newer retry timer', c.timers.pendingDelays(), [3000]);
        }
        // Regression: the newer failure's retry timer can expire while the
        // old probe is still in flight. That timer must not be lost forever.
        for (const settle of ['reject', 'falsy', 'response']) {
            const c = makeDeferredProbeFixture();
            c.runtime.noteRequestSuccess();
            c.runtime.noteRequestFailure();
            assertEq('newer failure schedules retry before old probe ' + settle,
                c.timers.pendingDelays(), [15000, 3000]);
            c.timers.flushDelay(3000); // only retry; not the older probe deadline
            assertEq('older pending probe prevents overlap for ' + settle, c.deferred.length, 1);
            assertEq('expired retry leaves only old probe watchdog ' + settle,
                c.timers.pendingDelays(), [15000]);
            if (settle === 'reject') c.deferred[0].reject(new TypeError('old probe failed'));
            else c.deferred[0].resolve(settle === 'falsy' ? null : okJsonResponse({}));
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            assertEq('stale ' + settle + ' settles without overriding newer offline state',
                c.runtime.getState(), 'offline');
            assertEq('stale ' + settle + ' restores one recovery timer', c.timers.pendingDelays(), [3000]);
            c.timers.flushAll();
            assertEq('restored backoff actually starts replacement probe', c.deferred.length, 2);
            c.deferred[1].resolve(okJsonResponse({}));
            await Promise.resolve();
            await Promise.resolve();
            assertEq('replacement probe can restore connectivity', c.runtime.getState(), 'online');
            assertEq('successful replacement leaves no retry timer', c.timers.pendingCount(), 0);
        }
        // A real probe failure WITHOUT newer success must still mark offline.
        {
            const c = makeDeferredProbeFixture();
            c.deferred[0].reject(new TypeError('server remains unavailable'));
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            assertEq('uncontested probe failure marks offline', c.runtime.getState(), 'offline');
            assertEq('uncontested probe failure backs off', c.timers.pendingDelays(), [6000]);
        }
    }

    /* ---- live request coordinator: probe transport cannot signal offline
     * before the runtime's generation fence settles the old request. ---- */
    {
        const requests = require(path.join(rootDir, 'frontend/js/request-coordinator.js'));
        async function integratedProbeCase(laterForegroundFailure) {
            const timers = makeFakeTimers();
            let rejectOldProbe = null;
            let probeAttempts = 0;
            let failForeground = false;
            const coordinator = requests.createPrksRequestCoordinator({
                origin: 'http://127.0.0.1',
                random: function () { return 0.5; },
                sleep: async function () {},
                fetchImpl: function (url) {
                    if (url === '/api/settings') {
                        probeAttempts += 1;
                        if (probeAttempts === 1) {
                            return new Promise(function (_resolve, reject) {
                                rejectOldProbe = reject;
                            });
                        }
                        return Promise.reject(new TypeError('old probe retry failed'));
                    }
                    if (url === '/api/works') {
                        if (failForeground) return Promise.reject(new TypeError('foreground failed'));
                        return Promise.resolve(new Response('[]', {
                            status: 200, headers: { 'Content-Type': 'application/json' },
                        }));
                    }
                    throw new Error('unexpected request ' + url);
                },
            });
            const runtime = mod.createPrksOfflineRuntime({
                prksRequest: function (url, init, policy) {
                    return coordinator.prksRequest(url, init, policy);
                },
                store: makeFakeStore(),
                setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout,
                window: null, caches: null, navigator: null,
            });
            const originalSuccess = global.prksOfflineNoteRequestSuccess;
            const originalFailure = global.prksOfflineNoteRequestFailure;
            global.prksOfflineNoteRequestSuccess = runtime.noteRequestSuccess;
            global.prksOfflineNoteRequestFailure = runtime.noteRequestFailure;
            try {
                runtime.noteRequestFailure();
                timers.flushAll();
                for (let i = 0; i < 12 && !rejectOldProbe; i++) {
                    await new Promise(function (resolve) { setImmediate(resolve); });
                }
                if (!rejectOldProbe) throw Error('coordinator did not start deferred reachability GET');
                assertEq('real coordinator starts runtime reachability probe', runtime.getState(), 'reconnecting');
                await coordinator.prksRequest('/api/works');
                assertEq('real foreground response marks online during old probe', runtime.getState(), 'online');
                if (laterForegroundFailure) {
                    failForeground = true;
                    let err = null;
                    try { await coordinator.prksRequest('/api/works'); } catch (e) { err = e; }
                    assert('real later failed GET is an ordinary transport failure', err instanceof TypeError);
                    assertEq('real later failed foreground request marks offline', runtime.getState(), 'offline');
                    assertEq('real newer failure owns a probe timer', timers.pendingDelays(), [15000, 3000]);
                    timers.flushDelay(3000); // older probe remains in flight
                    assertEq('real timer can expire while old probe remains pending',
                        timers.pendingDelays(), [15000]);
                }
                rejectOldProbe(new TypeError('stale reachability transport rejection'));
                // The isolated health probe is one-shot; ordinary GETs keep
                // coordinator retry policy. The probe never emits global
                // reachability reports before the runtime's generation fence.
                for (let i = 0; i < 12; i++) {
                    await new Promise(function (resolve) { setImmediate(resolve); });
                }
                assertEq('real coordinator probe has one transport attempt', probeAttempts, 1);
                assertEq('old coordinator failure cannot override newer reachability',
                    runtime.getState(), laterForegroundFailure ? 'offline' : 'online');
                assertEq('real probe settlement keeps required recovery timer state',
                    timers.pendingDelays(), laterForegroundFailure ? [3000] : []);
                assertEq('real runtime mutation gate reflects freshest foreground evidence',
                    runtime.isMutationBlocked(), laterForegroundFailure);
            } finally {
                if (originalSuccess === undefined) delete global.prksOfflineNoteRequestSuccess;
                else global.prksOfflineNoteRequestSuccess = originalSuccess;
                if (originalFailure === undefined) delete global.prksOfflineNoteRequestFailure;
                else global.prksOfflineNoteRequestFailure = originalFailure;
            }
        }
        await integratedProbeCase(false);
        await integratedProbeCase(true);
    }

    /* A real retryable 503 response proves reachability immediately. A
     * second coordinator attempt that hangs must not cause a false timeout. */
    {
        const requests = require(path.join(rootDir, 'frontend/js/request-coordinator.js'));
        const timers = makeFakeTimers();
        let attempts = 0;
        const coordinator = requests.createPrksRequestCoordinator({
            origin: 'http://127.0.0.1',
            fetchImpl: function (url) {
                assertEq('HTTP probe uses settings path', url, '/api/settings');
                attempts += 1;
                if (attempts === 1) {
                    return Promise.resolve(new Response('{}', {
                        status: 503, headers: { 'Content-Type': 'application/json' },
                    }));
                }
                return new Promise(function () {}); // should never be reached
            },
        });
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function (url, init, policy) {
                assertEq('one-shot health probe has retries disabled', policy.retry, false);
                return coordinator.prksRequest(url, init, policy);
            },
            store: makeFakeStore(),
            setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout,
            window: null, caches: null, navigator: null,
        });
        runtime.noteRequestFailure();
        timers.flushDelay(3000);
        for (let i = 0; i < 12 && runtime.getState() !== 'online'; i++) {
            await new Promise(function (resolve) { setImmediate(resolve); });
        }
        assertEq('HTTP 503 proves reachability before any retry', runtime.getState(), 'online');
        assertEq('retryable HTTP status never starts another health fetch', attempts, 1);
        assertEq('successful HTTP 503 health result clears watchdog', timers.pendingCount(), 0);
    }

    /* ---- #819: a permanently stalled probe cannot monopolize recovery ---- */
    {
        const timers = makeFakeTimers();
        let attempts = 0;
        let firstResolve = null;
        let firstSignal = null;
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function (_url, init, policy) {
                attempts += 1;
                assertEq('probe uses the isolated background policy', policy.reportReachability, false);
                if (attempts === 1) {
                    firstSignal = init.signal;
                    // Deliberately ignore AbortSignal to prove even a
                    // non-cooperative promise cannot publish a late answer.
                    return new Promise(function (resolve) {
                        firstResolve = resolve;
                    });
                }
                return Promise.resolve(okJsonResponse({}));
            },
            store: makeFakeStore(),
            setTimeout: timers.setTimeout,
            clearTimeout: timers.clearTimeout,
            window: null, caches: null, navigator: null,
        });
        runtime.noteRequestFailure();
        timers.flushDelay(3000);
        assertEq('stalled probe enters reconnecting', runtime.getState(), 'reconnecting');
        assertEq('stalled probe sets a bounded deadline', timers.pendingDelays(), [15000]);
        runtime._runProbe();
        assertEq('hints cannot start a duplicate probe before deadline', attempts, 1);
        timers.flushDelay(15000);
        assertEq('deadline moves stalled probe offline', runtime.getState(), 'offline');
        assert('deadline aborts first stalled probe transport', firstSignal && firstSignal.aborted);
        assertEq('deadline schedules normal growing retry backoff', timers.pendingDelays(), [6000]);
        timers.flushDelay(6000);
        assertEq('deadline released in-flight guard so recovery can run', attempts, 2);
        await Promise.resolve();
        await Promise.resolve();
        assertEq('replacement successful response restores online', runtime.getState(), 'online');
        assertEq('replacement success cancels all timers', timers.pendingCount(), 0);
        firstResolve(null);
        await Promise.resolve();
        await Promise.resolve();
        assertEq('late null completion does not undo recovery', runtime.getState(), 'online');
        assertEq('late completion does not add retry timer', timers.pendingCount(), 0);
    }
    {
        const timers = makeFakeTimers();
        let lateResolve = null;
        let started = 0;
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                started += 1;
                if (started === 1) {
                    return new Promise(function (resolve) { lateResolve = resolve; });
                }
                return Promise.resolve(okJsonResponse({}));
            },
            store: makeFakeStore(),
            setTimeout: timers.setTimeout,
            clearTimeout: timers.clearTimeout,
            window: null, caches: null, navigator: null,
        });
        runtime.noteRequestFailure();
        timers.flushDelay(3000);
        runtime.noteRequestSuccess();
        assertEq('new foreground success restores online during stalled probe', runtime.getState(), 'online');
        timers.flushDelay(15000);
        assertEq('old timed-out probe cannot override newer success', runtime.getState(), 'online');
        assertEq('superseded timeout schedules no retry', timers.pendingCount(), 0);
        // The expired probe must release probeInFlight, even though a newer
        // success superseded its deadline: a genuinely newer failure still
        // needs to launch another reachability check.
        runtime.noteRequestFailure();
        assertEq('later genuine failure still schedules recovery after superseded timeout',
            timers.pendingDelays(), [3000]);
        timers.flushDelay(3000);
        assertEq('new probe starts after superseded watchdog releases guard', started, 2);
        await Promise.resolve();
        await Promise.resolve();
        assertEq('replacement probe restores online', runtime.getState(), 'online');
        lateResolve(null);
        await Promise.resolve();
        await Promise.resolve();
        assertEq('late superseded probe result still cannot mark offline', runtime.getState(), 'online');
        assertEq('late result cannot add another timer', timers.pendingCount(), 0);
    }
    {
        const timers = makeFakeTimers();
        const deferred = [];
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                return new Promise(function (resolve, reject) { deferred.push({ resolve, reject }); });
            },
            store: makeFakeStore(),
            setTimeout: timers.setTimeout,
            clearTimeout: timers.clearTimeout,
            window: null, caches: null, navigator: null,
        });
        runtime.noteRequestFailure();
        timers.flushDelay(3000);
        timers.flushDelay(15000);
        timers.flushDelay(6000);
        assertEq('second deferred probe started after timed-out first', deferred.length, 2);
        deferred[0].reject(new TypeError('late timeout rejection'));
        await Promise.resolve();
        await Promise.resolve();
        assertEq('late rejection cannot interrupt replacement reconnect', runtime.getState(), 'reconnecting');
        assertEq('replacement owns exactly one watchdog', timers.pendingDelays(), [15000]);
        deferred[1].resolve(domainErrorResponse(503));
        await Promise.resolve();
        await Promise.resolve();
        assertEq('HTTP 503 response still proves server reachable', runtime.getState(), 'online');
        assertEq('HTTP-success replacement clears deadline', timers.pendingCount(), 0);
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

    /* ---- opt-in confirmed-offline reads bypass repeated failed requests only for eligible cache ---- */
    {
        const store = makeFakeStore();
        await store.putEntity('argument-state', 'A-1', { argument_id: 'A-1', revision: 7 }, '');
        let requests = 0;
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                requests += 1;
                return Promise.reject(new Error('offline'));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null, caches: null, navigator: null,
        });
        runtime.noteRequestFailure();
        const fast = await runtime.readThroughEntity(
            'argument-state', 'A-1', '/api/arguments/A-1/sync-state',
            { preferCacheWhenOffline: true });
        assertEq('opt-in offline read retains cache provenance', fast.source, 'cache');
        assertEq('opt-in offline read returns stored revision', fast.value.revision, 7);
        assertEq('opt-in offline read skips a known-failing network attempt', requests, 0);
        const regular = await runtime.readThroughEntity(
            'argument-state', 'A-1', '/api/arguments/A-1/sync-state');
        assertEq('default read stays server-first even while offline', requests, 1);
        assertEq('default offline failure retains cache fallback', regular.source, 'cache');

        // Opt-in must still attempt the server if the cache entry is absent.
        const missing = await runtime.readThroughEntity(
            'argument-state', 'A-missing', '/api/arguments/A-missing/sync-state',
            { preferCacheWhenOffline: true });
        assertEq('missing base is unavailable, never synthesized', missing.source, 'unavailable');
        assertEq('missing cache uses existing read-through behavior', requests, 2);
    }

    /* ---- opt-in never bypasses server if online or a cache generation is invalid ---- */
    {
        const store = makeFakeStore({
            deleteEntity: async function () { return false; }, // invalid rows remain on disk
        });
        await store.putEntity('argument', 'A-stale', { id: 'A-stale', name: 'old' }, '');
        let requests = 0;
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                requests += 1;
                return Promise.reject(new Error('offline'));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null, caches: null, navigator: null,
        });
        runtime.markEntityChanged('argument', 'A-stale');
        runtime.noteRequestFailure();
        const invalidated = await runtime.readThroughEntity(
            'argument', 'A-stale', '/api/arguments/A-stale',
            { preferCacheWhenOffline: true });
        assertEq('invalidated row cannot be used by cache-first path', invalidated.source, 'unavailable');
        assertEq('invalidated row follows normal read-through attempt', requests, 1);
    }
    {
        const store = makeFakeStore();
        await store.putEntity('argument', 'A-blocked', { id: 'A-blocked', name: 'old' }, '');
        let requests = 0;
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                requests += 1;
                return Promise.reject(new Error('offline'));
            },
            store: Object.assign(store, {
                deleteEntitiesByKind: async function () { return false; },
            }),
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null, caches: null, navigator: null,
        });
        runtime.noteRequestFailure();
        await runtime.markDomainChanged('arguments', { entityKinds: ['argument'] });
        const blocked = await runtime.readThroughEntity(
            'argument', 'A-blocked', '/api/arguments/A-blocked',
            { domain: 'arguments', preferCacheWhenOffline: true });
        assertEq('blocked domain refuses cache-first read', blocked.source, 'unavailable');
        assertEq('blocked domain still attempts normal request', requests, 1);
    }
    {
        const store = makeFakeStore();
        await store.putEntity('argument', 'A-online', { id: 'A-online', name: 'old' }, '');
        let requests = 0;
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: function () {
                requests += 1;
                return Promise.resolve(okJsonResponse({ id: 'A-online', name: 'new' }));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null, caches: null, navigator: null,
        });
        const fresh = await runtime.readThroughEntity(
            'argument', 'A-online', '/api/arguments/A-online',
            { preferCacheWhenOffline: true });
        assertEq('online opt-in always uses server data', fresh.value.name, 'new');
        assertEq('online opt-in retains server provenance', fresh.source, 'server');
        assertEq('online opt-in performs one request', requests, 1);
    }

    /* ---- a reconnect while IndexedDB is pending rejects stale cache-first result ---- */
    {
        let resumeCache;
        const store = makeFakeStore({
            getEntity: function () {
                return new Promise(resolve => { resumeCache = resolve; });
            },
        });
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: () => Promise.resolve(okJsonResponse({ id: 'A-race', name: 'live' })),
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null, caches: null, navigator: null,
        });
        runtime.noteRequestFailure();
        const read = runtime.readThroughEntity(
            'argument', 'A-race', '/api/arguments/A-race',
            { preferCacheWhenOffline: true });
        runtime.noteRequestSuccess();
        resumeCache({ value: { id: 'A-race', name: 'stale' }, cachedAt: 1 });
        const result = await read;
        assertEq('reconnected read ignores pending stale cache', result.value.name, 'live');
        assertEq('reconnected read restores server provenance', result.source, 'server');
    }

    /* ---- a full offline -> online -> offline cycle must invalidate a pending fast path ---- */
    {
        let resumeCache;
        let reads = 0;
        const store = makeFakeStore({
            getEntity: function () {
                reads += 1;
                if (reads === 1) return new Promise(resolve => { resumeCache = resolve; });
                return Promise.resolve(null);
            },
        });
        let requests = 0;
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: () => {
                requests += 1;
                return Promise.resolve(okJsonResponse({ id: 'A-cycle', name: 'fresh' }));
            },
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null, caches: null, navigator: null,
        });
        runtime.noteRequestFailure();
        const pending = runtime.readThroughEntity(
            'argument', 'A-cycle', '/api/arguments/A-cycle',
            { preferCacheWhenOffline: true });
        runtime.noteRequestSuccess();
        runtime.noteRequestFailure();
        resumeCache({ value: { id: 'A-cycle', name: 'stale' }, cachedAt: 1 });
        const result = await pending;
        assertEq('offline/online/offline cycle discards old pending cache', result.value.name, 'fresh');
        assertEq('offline/online/offline cycle rechecks the server', requests, 1);
        assertEq('cycle read keeps truthful server provenance', result.source, 'server');
    }

    /* ---- coherence invalidation while a fast cache read is pending is fail-closed ---- */
    {
        let resumeCache;
        const store = makeFakeStore({
            getEntity: function () {
                return new Promise(resolve => { resumeCache = resolve; });
            },
            deleteEntity: async function () { return false; },
        });
        const runtime = mod.createPrksOfflineRuntime({
            prksRequest: () => Promise.reject(new Error('offline')),
            store: store,
            setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout,
            window: null, caches: null, navigator: null,
        });
        runtime.noteRequestFailure();
        const pending = runtime.readThroughEntity(
            'argument', 'A-invalidate', '/api/arguments/A-invalidate',
            { preferCacheWhenOffline: true });
        runtime.markEntityChanged('argument', 'A-invalidate');
        resumeCache({ value: { id: 'A-invalidate', name: 'stale' }, cachedAt: 1 });
        const result = await pending;
        assertEq('invalidation during cache read refuses stale return', result.source, 'unavailable');
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


    /* ---- confirmed-offline cache-first LIST reads: Recently Added only ---- */
    {
        const key = 'recently-added:index';
        const path = '/api/recently-added';
        const domain = 'recently-added';
        const rows = [{ id: 'W-1', title: 'Acknowledged Work' }];
        const validate = value => Array.isArray(value) &&
            value.every(row => row && typeof row.id === 'string');
        const readOpts = { domain, validate, preferCacheWhenOffline: true };
        function makeRuntime(store, request) {
            return mod.createPrksOfflineRuntime({
                store, prksRequest: request,
                setTimeout: noopSetTimeout, clearTimeout: noopClearTimeout,
                window: null, caches: null, navigator: null,
            });
        }

        // A real request failure confirms offline; subsequent validated reads
        // use the stored projection and do not enter the coordinator retry lane.
        {
            const store = makeFakeStore();
            await store.putList(key, rows, '');
            let requests = 0;
            const runtime = makeRuntime(store, () => {
                requests++;
                return Promise.reject(new Error('network unreachable'));
            });
            runtime.noteRequestFailure();
            const cached = await runtime.readThroughList(key, path, readOpts);
            assertEq('confirmed-offline Recently Added comes from validated cache', cached.source, 'cache');
            assertEq('confirmed-offline Recently Added preserves rows', cached.value, rows);
            assertEq('confirmed-offline Recently Added makes no request', requests, 0);
            assertEq('confirmed-offline Recently Added preserves provenance timestamp', cached.cachedAt, 9999);
            const normal = await runtime.readThroughList(key, path, { domain, validate });
            assertEq('non-opt-in list stays server-first offline', requests, 1);
            assertEq('non-opt-in list can still fall back to cache', normal.source, 'cache');
            const cold = await runtime.readThroughList('recently-added:missing', path, readOpts);
            assertEq('missing list still makes normal network attempt', requests, 2);
            assertEq('missing list is unavailable, not fabricated', cold.source, 'unavailable');
        }

        // Initial/reconnecting/online reads never trust even a valid cache.
        {
            const store = makeFakeStore();
            await store.putList(key, rows, '');
            let requests = 0;
            const runtime = makeRuntime(store, () => {
                requests++;
                return Promise.resolve(okJsonResponse([{ id: 'W-fresh', title: 'Server Work' }]));
            });
            const online = await runtime.readThroughList(key, path, readOpts);
            assertEq('initial online list remains server-first', online.source, 'server');
            assertEq('initial online list uses server version', online.value[0].id, 'W-fresh');
            assertEq('initial online list performs request', requests, 1);
        }

        // Invalid cached shape cannot skip the server, and a reachable 4xx
        // remains a domain error rather than becoming cached success.
        {
            const store = makeFakeStore();
            await store.putList(key, [{ title: 'invalid, missing ID' }], '');
            let requests = 0;
            const runtime = makeRuntime(store, () => {
                requests++;
                return Promise.resolve(domainErrorResponse(403));
            });
            runtime.noteRequestFailure();
            let error = null;
            try { await runtime.readThroughList(key, path, readOpts); }
            catch (err) { error = err; }
            assertEq('malformed cache goes through server-first path', requests, 1);
            assert('malformed cache does not mask HTTP 403', error && error.status === 403);
            assertEq('HTTP domain error proves server reachability', runtime.getState(), 'online');
        }

        // Failed cache access must not prevent an authoritative server read.
        {
            const store = makeFakeStore({ getList: () => Promise.reject(Error('IDB failure')) });
            let requests = 0;
            const runtime = makeRuntime(store, () => {
                requests++;
                return Promise.resolve(okJsonResponse(rows));
            });
            runtime.noteRequestFailure();
            const result = await runtime.readThroughList(key, path, readOpts);
            assertEq('IndexedDB error falls back to authoritative request', result.source, 'server');
            assertEq('IndexedDB error does not block server request', requests, 1);
        }

        // A blocked domain must refuse its old row even when a failed sweep
        // leaves bytes present on disk.
        {
            const store = makeFakeStore({ deleteList: async () => false });
            await store.putList(key, rows, '');
            let requests = 0;
            const runtime = makeRuntime(store, () => {
                requests++;
                return Promise.reject(new Error('offline'));
            });
            runtime.noteRequestFailure();
            runtime.markDomainChanged(domain, { listKeys: [key] });
            await runtime._domainCleanup(domain);
            const blocked = await runtime.readThroughList(key, path, readOpts);
            assertEq('failed domain sweep leaves list blocked', runtime.isDomainBlocked(domain), true);
            assertEq('blocked list cannot render old snapshot', blocked.source, 'unavailable');
            assertEq('blocked list uses normal request attempt', requests, 1);
        }

        // Pause precisely after IndexedDB captures an acknowledged row.
        // Every asynchronous completion must re-evaluate connectivity,
        // coherence and route cancellation before releasing that row.
        async function pausedFastList(request, extraOpts) {
            const store = makeFakeStore();
            await store.putList(key, rows, '');
            const originalGet = store.getList.bind(store);
            let resume = null;
            let cacheReads = 0;
            store.getList = function (k) {
                cacheReads++;
                if (cacheReads !== 1) return originalGet(k);
                const snapshot = originalGet(k);
                return new Promise(resolve => {
                    resume = () => { void snapshot.then(resolve); };
                });
            };
            let requests = 0;
            const runtime = makeRuntime(store, () => {
                requests++;
                return request();
            });
            runtime.noteRequestFailure();
            const pending = runtime.readThroughList(key, path,
                Object.assign({}, readOpts, extraOpts || {}));
            assert('cache-first list enters asynchronous IDB read', typeof resume === 'function');
            return { runtime, store, resume, pending, getRequests: () => requests };
        }
        {
            const f = await pausedFastList(() => Promise.reject(Error('offline')));
            f.resume();
            const result = await f.pending;
            assertEq('stable paused list returns cached projection', result.source, 'cache');
            assertEq('stable paused list never requests server', f.getRequests(), 0);
        }
        {
            const f = await pausedFastList(() => Promise.reject(Error('offline')));
            f.runtime.markDomainChanged(domain, { listKeys: [key] });
            await f.runtime._domainCleanup(domain);
            f.resume();
            const result = await f.pending;
            assertEq('completed sweep cannot resurrect pending list read', result.source, 'unavailable');
            assertEq('invalidated pending list uses normal transport', f.getRequests(), 1);
        }
        {
            const f = await pausedFastList(() =>
                Promise.resolve(okJsonResponse([{ id: 'W-live', title: 'Fresh' }])));
            f.runtime.noteRequestSuccess();
            f.resume();
            const result = await f.pending;
            assertEq('reconnection during IDB read returns server row', result.value[0].id, 'W-live');
            assertEq('reconnection during IDB read keeps server provenance', result.source, 'server');
            assertEq('reconnection during IDB read requests server', f.getRequests(), 1);
        }
        {
            const f = await pausedFastList(() =>
                Promise.resolve(okJsonResponse([{ id: 'W-cycle', title: 'New' }])));
            f.runtime.noteRequestSuccess();
            f.runtime.noteRequestFailure();
            f.resume();
            const result = await f.pending;
            assertEq('online/offline cycle fences stale list cache', result.value[0].id, 'W-cycle');
            assertEq('online/offline cycle invokes server read', f.getRequests(), 1);
        }
        {
            const controller = new AbortController();
            const f = await pausedFastList(() => Promise.reject(Error('must not request')),
                { signal: controller.signal });
            controller.abort();
            f.resume();
            let error = null;
            try { await f.pending; } catch (err) { error = err; }
            assertEq('route abort while IndexedDB pending rejects AbortError',
                error && error.name, 'AbortError');
            assertEq('aborted list does not send request', f.getRequests(), 0);
        }
        {
            const store = makeFakeStore();
            await store.putList(key, rows, '');
            let requests = 0;
            const runtime = makeRuntime(store, () => {
                requests++;
                return Promise.reject(Error('must not request'));
            });
            runtime.noteRequestFailure();
            const controller = new AbortController();
            controller.abort();
            let error = null;
            try {
                await runtime.readThroughList(key, path,
                    Object.assign({}, readOpts, { signal: controller.signal }));
            } catch (err) { error = err; }
            assertEq('pre-aborted list read returns AbortError', error && error.name, 'AbortError');
            assertEq('pre-aborted list read does not reach network', requests, 0);
        }
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
            scope: { dbName: async (kind) => (kind === 'pdf' ? 'prks-pdf-v2:S' : null) },
        });
        const ok = await runtime.clearCache();
        assert('clearCache resolves true', ok === true);
        assertEq('clearCache removes the signed-in scope\'s pdf cache', deletedCacheName, 'prks-pdf-v2:S');
        deletedCacheName = null;
        const unscoped = mod.createPrksOfflineRuntime({
            prksRequest: null, store: makeFakeStore(), setTimeout: noopSetTimeout,
            clearTimeout: noopClearTimeout, window: null, caches: fakeCaches, navigator: null,
            scope: { dbName: async () => null },
        });
        await unscoped.clearCache();
        assertEq('without a scope no pdf cache is touched', deletedCacheName, null);
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

    /* ---- real coordinator + offline runtime: reconnecting Person retry correctness ----
     * The browser A/B in #776 measured fully-offline latency only. This test
     * exercises the ACTUAL request coordinator and offline runtime together
     * with deterministic failed-then-successful transports. No retry contract
     * or production state machine is patched or replaced. */
    {
        const requestModule = require(path.join(rootDir, 'frontend/js/request-coordinator.js'));
        const personPath = '/api/persons/P-1';
        const oldPerson = { id: 'P-1', name: 'cached-before-reconnect' };
        const livePerson = { id: 'P-1', name: 'recovered-server-value' };

        function httpJson(value, status) {
            return new Response(JSON.stringify(value), {
                status: status || 200,
                headers: { 'Content-Type': 'application/json' },
            });
        }

        async function fixture(transport, customStore) {
            const store = customStore || makeFakeStore();
            await store.putEntity('person', 'P-1', oldPerson, '');
            const attempts = [];
            const coordinator = requestModule.createPrksRequestCoordinator({
                origin: 'http://127.0.0.1',
                random: function () { return 0.5; },
                sleep: async function () {},
                fetchImpl: function (url, opts) {
                    attempts.push({ url: url, signal: opts.signal });
                    return transport(attempts.length, url, opts);
                },
            });
            let rejectProbe = null;
            const runtime = mod.createPrksOfflineRuntime({
                store: store,
                // A real reachability probe begins at init(). Its response
                // is held open by default, but tests can reject it to simulate
                // an unsuccessful reconnecting -> offline transition.
                prksRequest: function (url, opts, policy) {
                    if (url === '/api/settings') {
                        return new Promise(function (_resolve, reject) { rejectProbe = reject; });
                    }
                    return coordinator.prksRequest(url, opts, policy);
                },
                setTimeout: noopSetTimeout, clearTimeout: noopClearTimeout,
                window: null, caches: null, navigator: null,
            });
            runtime.noteRequestFailure();
            runtime.init();
            assertEq('Person test starts with a real pending probe', runtime.getState(), 'reconnecting');
            return {
                store: store, runtime: runtime, coordinator: coordinator,
                attempts: attempts,
                failProbe: function () { rejectProbe(new TypeError('probe unreachable')); },
            };
        }

        function readPerson(c, options) {
            return c.runtime.readThroughEntity('person', 'P-1', personPath,
                Object.assign({
                    domain: 'people',
                    preferCacheWhenOffline: true,
                    validate: function (value) { return !!value && value.id === 'P-1'; },
                }, options || {}));
        }

        // A brief transport failure is NOT equivalent to the server being
        // offline. With retries the live answer supersedes the cached value.
        {
            const c = await fixture(function (n) {
                return n === 1 ? Promise.reject(new TypeError('temporary network error'))
                    : Promise.resolve(httpJson(livePerson));
            });
            const r = await readPerson(c);
            assertEq('Person default policy recovered live value on second GET', r.value, livePerson);
            assertEq('Person default policy attributes recovered data to server', r.source, 'server');
            assertEq('Person default policy made two GET attempts', c.attempts.length, 2);
            assertEq('Person successful recovery transitions online', c.runtime.getState(), 'online');
        }

        // The existing no-retry policy is faster when disconnected, but can
        // show an older cached record after a transient first-attempt failure.
        {
            const c = await fixture(function (n) {
                return n === 1 ? Promise.reject(new TypeError('temporary network error'))
                    : Promise.resolve(httpJson(livePerson));
            });
            const r = await readPerson(c, { requestPolicy: { retry: false } });
            assertEq('Person single attempt returned cached value after transient error', r.value, oldPerson);
            assertEq('Person single attempt truthfully labels the cached fallback', r.source, 'cache');
            assertEq('Person single attempt does not retry', c.attempts.length, 1);
            assertEq('Person single attempt does not assert server reachability', c.runtime.getState(), 'reconnecting');
        }

        // A reachable 503 is a domain response, never an offline cache
        // fallback. The coordinator retries 503 by default; disabling it
        // leaves the runtime's domain-error path intact (no stale masking).
        {
            const transport = function (n) {
                return Promise.resolve(n === 1 ? httpJson({}, 503) : httpJson(livePerson));
            };
            const c = await fixture(transport);
            const r = await readPerson(c);
            assertEq('Person default retry recovers from a transient 503', r.source, 'server');
            assertEq('Person default retry recovered fresh 503-followup value', r.value, livePerson);
            assertEq('Person default retry performs second GET after 503', c.attempts.length, 2);
        }
        {
            const c = await fixture(function (n) {
                return Promise.resolve(n === 1 ? httpJson({}, 503) : httpJson(livePerson));
            });
            let error = null;
            try {
                await readPerson(c, { requestPolicy: { retry: false } });
            } catch (err) { error = err; }
            assert('Person single-attempt 503 fails as domain error, not cache fallback',
                !!error && error.isPrksDomainError && error.status === 503);
            assertEq('Person single-attempt 503 sends one GET', c.attempts.length, 1);
            assertEq('Person 503 does not discard cached entity', (await c.store.getEntity('person','P-1')).value, oldPerson);
        }

        // A mutation/domain sweep that fails to delete a stale cached row
        // MUST keep People blocked. A pending no-retry transport failure
        // cannot surface that row as a seemingly valid offline fallback.
        {
            let rejectPending = null;
            const stuckStore = makeFakeStore({
                deleteEntitiesByKind: async function () { return false; },
            });
            const c = await fixture(function () {
                return new Promise(function (_resolve, reject) { rejectPending = reject; });
            }, stuckStore);
            const pending = readPerson(c, { requestPolicy: { retry: false } });
            for (let i = 0; i < 12 && !rejectPending; i++) {
                await new Promise(function (resolve) { setImmediate(resolve); });
            }
            if (!rejectPending) throw new Error('Person test transport never started');
            c.runtime.markDomainChanged('people', { entityKinds: ['person'] });
            await c.runtime._domainCleanup('people');
            assert('failed People sweep remains blocked', c.runtime.isDomainBlocked('people'));
            rejectPending(new TypeError('network unavailable'));
            const r = await pending;
            assertEq('pending failed read cannot return blocked stale Person', r.source, 'unavailable');
            assertEq('blocked Person result has no cached payload', r.value, null);
            assertEq('failed sweep deliberately retained stale cache row', (await stuckStore.getEntity('person', 'P-1')).value, oldPerson);
        }

        // The network can fail, then IndexedDB may pause BEFORE the cached
        // value is returned. Keep a snapshot of the original row while its
        // read is unresolved, just as an IDB transaction can finish after
        // another route invalidates the record.
        async function pausedCacheFallback(extraReadOptions) {
            const store = makeFakeStore();
            const originalGet = store.getEntity.bind(store);
            let resume = null;
            store.getEntity = function (kind, id) {
                const snapshot = originalGet(kind, id);
                return new Promise(function (resolve) {
                    resume = function () { void snapshot.then(resolve); };
                });
            };
            const c = await fixture(function () {
                return Promise.reject(new TypeError('network unreachable'));
            }, store);
            const pending = readPerson(c, Object.assign(
                { requestPolicy: { retry: false } }, extraReadOptions || {}));
            for (let i = 0; i < 12 && !resume; i++) {
                await new Promise(function (resolve) { setImmediate(resolve); });
            }
            if (!resume) throw new Error('Person fallback never began its deferred cache read');
            return { c, pending, resume };
        }

        // A failed connectivity probe is not a successful reconnect.
        // A pending fallback must still return a valid cached row through
        // reconnecting -> offline, and even an offline -> reconnecting hint.
        {
            const f = await pausedCacheFallback();
            f.c.failProbe();
            await new Promise(function (resolve) { setImmediate(resolve); });
            assertEq('failed probe settles offline during Person cache read',
                f.c.runtime.getState(), 'offline');
            f.c.runtime.init();
            assertEq('fresh offline hint starts another pending probe', f.c.runtime.getState(), 'reconnecting');
            f.resume();
            const r = await f.pending;
            assertEq('unsuccessful reconnect probe does not hide valid cached Person', r.source, 'cache');
            assertEq('failed probe retains cached Person content', r.value, oldPerson);
        }

        {
            const f = await pausedCacheFallback();
            f.c.runtime.markEntityChanged('person', 'P-1');
            f.resume();
            const r = await f.pending;
            assertEq('entity mutation during pending fallback fails closed', r.source, 'unavailable');
            assertEq('mutated Person fallback contains no stale data', r.value, null);
        }

        {
            const f = await pausedCacheFallback();
            f.c.runtime.markDomainChanged('people', { entityKinds: ['person'] });
            await f.c.runtime._domainCleanup('people');
            assert('successful domain sweep unblocks People', !f.c.runtime.isDomainBlocked('people'));
            // The row was removed from the store, but its older asynchronous
            // getEntity had already captured it. The generation still fences.
            assert('swept Person row is absent from store',
                !f.c.store._entities.has('person:P-1'));
            f.resume();
            const r = await f.pending;
            assertEq('domain change during pending fallback fails closed after unblock', r.source, 'unavailable');
            assertEq('unblocked Person does not resurrect old IDB snapshot', r.value, null);
        }

        {
            const f = await pausedCacheFallback();
            f.c.runtime.noteRequestSuccess();
            assertEq('separate successful request restores online state', f.c.runtime.getState(), 'online');
            f.resume();
            const r = await f.pending;
            assertEq('reconnect while fallback cache pending fails closed', r.source, 'unavailable');
            assertEq('reconnected Person fallback cannot show stale record', r.value, null);
        }

        {
            const f = await pausedCacheFallback();
            f.c.runtime.noteRequestSuccess();
            f.c.runtime.noteRequestFailure();
            f.resume();
            const r = await f.pending;
            assertEq('online-offline cycle fences pending stale Person', r.source, 'unavailable');
        }

        {
            const controller = new AbortController();
            const f = await pausedCacheFallback({ signal: controller.signal });
            controller.abort();
            f.resume();
            let error = null;
            try { await f.pending; } catch (err) { error = err; }
            assertEq('route abort while fallback cache pending rejects AbortError',
                error && error.name, 'AbortError');
        }

        // A list fallback has the same async IndexedDB race as an entity.
        // A completed People sweep must fence an already-started list read,
        // even when that snapshot resolves after the domain unblocks.
        async function pausedListFallback(extraReadOptions) {
            const store = makeFakeStore();
            await store.putList('people:index', [oldPerson], '');
            const originalGet = store.getList.bind(store);
            let resume = null;
            store.getList = function (key) {
                const snapshot = originalGet(key);
                return new Promise(function (resolve) {
                    resume = function () { void snapshot.then(resolve); };
                });
            };
            const c = await fixture(function () {
                return Promise.reject(new TypeError('network unreachable'));
            }, store);
            const pending = c.runtime.readThroughList(
                'people:index', '/api/persons',
                Object.assign({
                    domain: 'people',
                    requestPolicy: { retry: false },
                }, extraReadOptions || {}));
            for (let i = 0; i < 12 && !resume; i++) {
                await new Promise(function (resolve) { setImmediate(resolve); });
            }
            if (!resume) throw new Error('People list fallback never began its deferred cache read');
            return { c, pending, resume };
        }

        {
            const f = await pausedListFallback();
            f.resume();
            const r = await f.pending;
            assertEq('unchanged People list still permits valid cached fallback', r.source, 'cache');
            assertEq('unchanged People list preserves its cached contents', r.value, [oldPerson]);
        }

        {
            const f = await pausedListFallback();
            f.c.failProbe();
            await new Promise(function (resolve) { setImmediate(resolve); });
            assertEq('failed probe settles offline during People list read',
                f.c.runtime.getState(), 'offline');
            f.resume();
            const r = await f.pending;
            assertEq('offline transition still serves valid cached People list', r.source, 'cache');
            assertEq('failed probe retains People list contents', r.value, [oldPerson]);
        }

        {
            const f = await pausedListFallback();
            f.c.runtime.markDomainChanged('people', { listKeys: ['people:index'] });
            await f.c.runtime._domainCleanup('people');
            assert('successful People list sweep unblocks domain', !f.c.runtime.isDomainBlocked('people'));
            assert('swept People list row removed from store', !f.c.store._lists.has('people:index'));
            f.resume();
            const r = await f.pending;
            assertEq('stale People list cannot return after sweep unblocks', r.source, 'unavailable');
            assertEq('invalidation during list fallback returns no old snapshot', r.value, null);
        }

        {
            const f = await pausedListFallback();
            f.c.runtime.noteRequestSuccess();
            f.resume();
            const r = await f.pending;
            assertEq('reconnect during cached People list read fails closed', r.source, 'unavailable');
        }

        {
            const controller = new AbortController();
            const f = await pausedListFallback({ signal: controller.signal });
            controller.abort();
            f.resume();
            let error = null;
            try { await f.pending; } catch (err) { error = err; }
            assertEq('abort during cached People list read rejects AbortError',
                error && error.name, 'AbortError');
        }

        // An aborted route remains aborted, not a cached read. In particular,
        // a per-request retry policy must not weaken AbortSignal handling.
        {
            const c = await fixture(function () { throw new Error('should not fetch'); });
            const controller = new AbortController();
            controller.abort();
            let error = null;
            try {
                await readPerson(c, { signal: controller.signal, requestPolicy: { retry: false } });
            } catch (err) { error = err; }
            assertEq('pre-aborted reconnecting Person read rejects AbortError', error && error.name, 'AbortError');
            assertEq('pre-aborted Person request causes zero fetches', c.attempts.length, 0);
        }
    }

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
