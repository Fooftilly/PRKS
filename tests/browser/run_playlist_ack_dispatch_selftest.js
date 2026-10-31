"use strict";
/* The production Playlist API wrapper -> local durable operation -> sync
 * coordinator -> ACK validator/reconciler -> renderable cache contract.
 * Previously three Chromium E2Es invoked these wrappers with page.evaluate
 * but inspected only disposable IndexedDB cache footprints. */
const strict = require("node:assert/strict");
let checks = 0;
const assert = Object.assign(
    (...args) => { checks++; return strict(...args); },
    {
        equal(...args) { checks++; return strict.equal(...args); },
        notEqual(...args) { checks++; return strict.notEqual(...args); },
        deepEqual(...args) { checks++; return strict.deepEqual(...args); },
        fail(...args) { checks++; return strict.fail(...args); },
    });
// The Unit/API runner sets cwd to the repository root; use fixed repository paths.
const fs = require("node:fs");
const {createFakeIndexedDBFactory} = require("./lib/fake_indexeddb.js");
const {createPrksLocalStore} = require("../../frontend/js/local-store.js");

const entities = new Map(), lists = new Map();
const entityKey = (kind, id) => kind + ":" + id;
const cache = {
    isAvailable: async () => true,
    getEntity: async (kind, id) => entities.get(entityKey(kind, id)) || null,
    putEntity: async (kind, id, value, revision) => {
        entities.set(entityKey(kind, id), {value, cachedAt: 1, sourceRevision: revision});
        return true;
    },
    deleteEntity: async (kind, id) => { entities.delete(entityKey(kind, id)); return true; },
    getEntitiesByKind: async kind => [...entities.entries()]
        .filter(([k]) => k.startsWith(kind + ":"))
        .map(([k, value]) => ({id: k.slice(kind.length + 1), ...value})),
    deleteEntitiesByKind: async kind => {
        for (const k of [...entities.keys()]) if (k.startsWith(kind + ":")) entities.delete(k);
        return true;
    },
    getList: async key => lists.get(key) || null,
    putList: async (key, value, revision) => {
        lists.set(key, {value, cachedAt: 1, sourceRevision: revision});
        return true;
    },
    deleteList: async key => { lists.delete(key); return true; },
};
globalThis.createPrksOfflineStore = () => cache;
// Create the real offline-runtime singleton on this seeded disposable store.
const offline = require("../../frontend/js/offline-runtime.js");
require("../../frontend/js/sync-runtime.js");
require("../../frontend/js/playlist-state.js");
globalThis.window = globalThis;

/* The browser binds these wrappers on window. Node imports the same actual
 * production functions through CommonJS exports, without code-string execution.
 * This shim represents the app.js store lookup, not playlist save logic. */
globalThis.prksDurableOperationsOrNone = async () => {
    try {
        const sync = globalThis.prksSync;
        return sync && sync.store ? await sync.store.listOperations() : [];
    } catch (_error) { return []; }
};
const {updatePlaylist, reorderPlaylist} =
    require("../../frontend/js/components/playlists.js");
// Reuse the exact production predicates from the existing 102-case shape test.
const validation = require("./run_offline_read_model_shape_selftest.js");
const syncSource = fs.readFileSync("frontend/js/sync-runtime.js", "utf8");
const registryStart = syncSource.indexOf("runtime = createRuntime({ store, online,");
const registryEnd = syncSource.indexOf("\n        lock:", registryStart);
assert(registryStart >= 0 && registryEnd > registryStart, "production sync registry");
const registry = syncSource.slice(registryStart, registryEnd);
for (const mapping of [
    "SET_PLAYLIST_FIELD: root.prksPlaylistFieldSyncHandler,",
    "REORDER_PLAYLIST_ITEMS: root.prksPlaylistOrderSyncHandler,",
]) {
    assert.equal(registry.split(mapping).length - 1, 1,
        "production registry must dispatch " + mapping);
}
const ID = "PL-SAVED", W1 = "W-1", W2 = "W-2", OTHER = "W-OTHER";
let nextId = 0;
const uuid = () => "00000000-0000-4000-8000-" +
    (++nextId).toString(16).padStart(12, "0");
const tick = () => new Promise(resolve => setTimeout(resolve, 1));
const item = (id, position) => ({
    id, title: id, author_text: "", published_date: "", position,
});
async function seed() {
    entities.clear();
    lists.clear();
    const row = {id: ID, title: "Original title",
        description: "Old description", original_url: null, item_count: 2};
    const detail = {...row, items: [item(W1, 0), item(W2, 1)]};
    const control = {id: "PL-CONTROL", title: "Unchanged",
        description: "", original_url: null, item_count: 0};
    await cache.putList("playlists:index", [row, control], "");
    await cache.putEntity("playlist", ID, detail, "");
    await cache.putEntity("playlist", control.id, {...control, items: []}, "");
    await cache.putEntity("playlist-state", ID, globalThis.prksNewPlaylistState(ID), "");
    for (const id of [W1, W2, OTHER]) {
        await cache.putEntity("work", id, {id, playlist_id: id === OTHER ? null : ID}, "");
    }
    await cache.putEntity("concept", "C-1", {id: "C-1"}, "");
    await cache.putList("concepts:index", [{id: "C-1"}], "");
    assert(validation.prksIsPlaylistsIndexShape((await cache.getList("playlists:index")).value));
    assert(validation.prksIsPlaylistShape((await cache.getEntity("playlist", ID)).value, ID));
}
async function waitForRetirement(store) {
    for (let i = 0; i < 80; i++) {
        if (!(await store.listOperations()).length) return;
        await tick();
    }
    assert.fail("ACK did not retire the durable Playlist operation");
}
async function scenario(name, write, expectedOperations = 1) {
    await seed();
    const local = createPrksLocalStore({indexedDB: createFakeIndexedDBFactory(), uuid});
    const envelopes = [], events = [];
    let reconciled = 0;
    // Only the network READ adapter is stubbed; it returns the same validated
    // cache that a cold offline client would read, not a guessed revision.
    const originalRead = globalThis.prksOfflineReadEntity;
    const originalField = globalThis.prksOfflineReconcilePlaylistField;
    const originalOrder = globalThis.prksOfflineReconcilePlaylistOrder;
    globalThis.prksOfflineReadEntity = async (kind, id) => {
        const r = await cache.getEntity(kind, id);
        return {value: r ? r.value : null, source: r ? "cache" : "unavailable"};
    };
    globalThis.prksOfflineReconcilePlaylistField = async (...args) => {
        reconciled++;
        return originalField(...args);
    };
    globalThis.prksOfflineReconcilePlaylistOrder = async (...args) => {
        reconciled++;
        return originalOrder(...args);
    };
    const runtime = globalThis.createPrksSyncRuntime({
        store: local, online: () => true,
        handlers: {
            SET_PLAYLIST_FIELD: globalThis.prksPlaylistFieldSyncHandler,
            REORDER_PLAYLIST_ITEMS: globalThis.prksPlaylistOrderSyncHandler,
        },
        request: async (_url, request) => {
            const op = JSON.parse(request.body);
            envelopes.push(op);
            const data = op.operation === "REORDER_PLAYLIST_ITEMS"
                ? {code: "ACKNOWLEDGED", changed: true, playlist_id: ID,
                    server_revision: 1, work_ids: [W2, W1]}
                : {code: "ACKNOWLEDGED", changed: true, playlist_id: ID,
                    field: op.payload.field, server_revision: 1, value_omitted: true};
            return {ok: true, status: 200, json: async () => data};
        },
    });
    globalThis.prksSync = runtime;
    const unsubscribe = runtime.subscribe(e => {if (e && e.acknowledged) events.push(e);});
    try {
        const before = offline.prksOfflineDomainGeneration("playlists");
        await write();
        await waitForRetirement(local);
        assert.equal(envelopes.length, expectedOperations,
            name + " sends exactly the expected durable envelopes");
        assert.equal(events.length, expectedOperations, name + " emits every ACK");
        assert.deepEqual(events.map(e => e.op.op_id), envelopes.map(e => e.op_id),
            name + " emits matching operation identities in order");
        assert.equal(reconciled, expectedOperations,
            name + " dispatches each operation through production handlers");
        const state = (await cache.getEntity("playlist-state", ID)).value;
        const detail = (await cache.getEntity("playlist", ID)).value;
        assert(validation.prksIsPlaylistShape(detail, ID), name + " keeps detail renderable");
        assert.deepEqual((await cache.getEntity("playlist", "PL-CONTROL")).value.items, []);
        for (const k of ["concepts:index"]) {
            assert(await cache.getList(k), name + " unrelated domain list retained");
        }
        assert(await cache.getEntity("concept", "C-1"), name + " unrelated entity retained");
        return {before, state, detail, envelope: envelopes[0], envelopes};
    } finally {
        unsubscribe();
        runtime.stop();
        delete globalThis.prksSync;
        globalThis.prksOfflineReadEntity = originalRead;
        globalThis.prksOfflineReconcilePlaylistField = originalField;
        globalThis.prksOfflineReconcilePlaylistOrder = originalOrder;
    }
}
async function main() {
    await scenario("description", async () => {
        await updatePlaylist(ID, {description: "New description"});
    }).then(async ({before, state, detail, envelope}) => {
        assert.equal(envelope.operation, "SET_PLAYLIST_FIELD");
        assert.deepEqual(envelope.payload, {field: "description", value: "New description"});
        assert.equal(detail.description, "New description");
        assert.equal(state.fields.description.revision, 1);
        const list = (await cache.getList("playlists:index")).value;
        assert(validation.prksIsPlaylistsIndexShape(list));
        assert.equal(list[0].description, "New description");
        assert(await cache.getEntity("work", W1), "description retains member Work");
        assert(await cache.getEntity("work", OTHER));
        assert.equal(offline.prksOfflineDomainGeneration("playlists"), before + 1);
    });
    await scenario("title", async () => {
        // Original Chromium case changed both title and description in one
        // wrapper call. Keep the two independent durable operations, ACKs and
        // revision fences; testing only title would silently lose coverage.
        await updatePlaylist(ID,
            {title: "Renamed title", description: ""});
    }, 2).then(async ({state, detail, envelopes}) => {
        assert.deepEqual(envelopes.map(e => e.operation),
            ["SET_PLAYLIST_FIELD", "SET_PLAYLIST_FIELD"]);
        assert.deepEqual(envelopes.map(e => e.payload), [
            {field: "title", value: "Renamed title"},
            {field: "description", value: ""},
        ]);
        assert.equal(detail.title, "Renamed title");
        assert.equal(detail.description, "");
        assert.equal(state.fields.title.revision, 1);
        assert.equal(state.fields.description.revision, 1);
        const list = (await cache.getList("playlists:index")).value;
        assert(validation.prksIsPlaylistsIndexShape(list));
        assert.equal(list[0].title, "Renamed title");
        assert.equal(list[0].description, "");
        assert.equal(await cache.getEntity("work", W1), null, "title evicts first member Work");
        assert.equal(await cache.getEntity("work", W2), null, "title evicts second member Work");
        assert(await cache.getEntity("work", OTHER), "title keeps outside Work");
    });
    await scenario("reorder", async () => {
        await reorderPlaylist(ID, [W2, W1]);
    }).then(async ({state, detail, envelope}) => {
        assert.equal(envelope.operation, "REORDER_PLAYLIST_ITEMS");
        assert.deepEqual(envelope.payload.work_ids, [W2, W1]);
        assert.deepEqual(detail.items.map(x => x.id), [W2, W1]);
        assert.equal(state.order_revision, 1);
        assert.equal(await cache.getList("playlists:index"), null,
            "reorder fences stale index, not detail");
        assert(await cache.getEntity("work", W1), "reorder keeps member Work");
        assert(await cache.getEntity("work", W2), "reorder keeps second member Work");
        assert(await cache.getEntity("work", OTHER), "reorder keeps outside Work");
    });
    console.log("All " + checks + " Playlist ACK-through-sync checks passed");
}
main().catch(e => {console.error(e); process.exitCode = 1;});
