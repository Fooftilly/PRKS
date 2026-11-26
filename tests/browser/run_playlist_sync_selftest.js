"use strict";
/* Playlists: five shapes, and how they compose.
 *
 * What this covers and nothing else does cheaply: a playlist created here is a
 * valid destination immediately and everything added to it waits for it; the
 * three columns are independent FIELD edits that cancel when taken back; which
 * playlist a video is in is a scalar on the VIDEO, so adding, moving and
 * removing are one operation; the ORDER is one aggregate that coalesces rather
 * than racing position by position; and deleting reasons about every intent
 * naming the playlist.
 */
const strict = require("assert/strict");
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); },
    { get: (_t, k) => (...args) => { checks += 1; return strict[k](...args); } });
const { createFakeIndexedDBFactory } = require("./lib/fake_indexeddb.js");
const { createPrksLocalStore } = require("../../frontend/js/local-store.js");
require("../../frontend/js/sync-runtime.js");
require("../../frontend/js/playlist-state.js");


/* Exercise the shipped classic-frontend API entrypoints rather than only
 * local-store coalescing. The removed E2Es likewise invoked these wrappers
 * directly, without clicking the playlist editor or drag controls. */
globalThis.window = globalThis;
function loadTopLevelFunction(source, name) {
    const marker = "function " + name + "(";
    let at = source.indexOf("async " + marker);
    if (at < 0) at = source.indexOf(marker);
    if (at < 0) throw new Error("missing production wrapper " + name);
    const brace = source.indexOf("{", at);
    let depth = 0;
    for (let i = brace; i < source.length; i++) {
        if (source[i] === "{") depth++;
        if (source[i] === "}" && --depth === 0) {
            (0, eval)(source.slice(at, i + 1));
            return;
        }
    }
    throw new Error("unclosed production wrapper " + name);
}
{
    const fs = require("node:fs");
    const path = require("node:path");
    const app = fs.readFileSync(path.join(__dirname, "../../frontend/js/app.js"), "utf8");
    const playlist = fs.readFileSync(path.join(__dirname, "../../frontend/js/components/playlists.js"), "utf8");
    loadTopLevelFunction(app, "prksDurableOperationsOrNone");
    for (const name of [
        "prksPlaylistSaveMessage", "prksPlaylistBaseUnavailable",
        "createPlaylist", "updatePlaylist", "prksPinPlaylistEdit", "prksSetWorkPlaylist",
        "addWorkToPlaylist", "reorderPlaylist", "deletePlaylistCanonical"
    ]) {
        loadTopLevelFunction(playlist, name);
    }
}

/* The disposable cache behind prksOfflinePeekEntity / prksOfflineCacheEntity. */
const heldEntities = new Map();
globalThis.prksOfflinePeekEntity = async (kind, id) => heldEntities.get(kind + ":" + id) || null;
globalThis.prksOfflineCacheEntity = async (kind, id, value) => { heldEntities.set(kind + ":" + id, value); return true; };
globalThis.prksOfflineMergeEntity = async (kind, id, merge) => {
    const next = merge(heldEntities.get(kind + ":" + id) || null);
    if (next != null) heldEntities.set(kind + ":" + id, next);
    return true;
};

let sequence = 0;
const uuid = () => "00000000-0000-4000-8000-" + (++sequence).toString(16).padStart(12, "0");
const tick = () => new Promise(resolve => setTimeout(resolve, 1));
async function settle() { for (let i = 0; i < 16; i++) await tick(); }
const newStore = () => createPrksLocalStore({ dbName: 'prks-local-v2:test', indexedDB: createFakeIndexedDBFactory(), uuid });

function baseAt(revisions, values) {
    const base = {};
    globalThis.PRKS_PLAYLIST_FIELDS.forEach(function (name) {
        base[name] = {
            value: (values && values[name]) || "",
            revision: (revisions && revisions[name]) || 0,
        };
    });
    return base;
}

const rowsFor = async (store, operation) =>
    (await store.listOperations()).filter(r => r.operation === operation);

const item = (id, title) => ({ id: id, title: title || id, position: 0 });

function playlistAck(op) {
    return { code: "ACKNOWLEDGED", playlist_id: op.entity_id, changed: true,
        playlist: { id: op.entity_id, title: op.payload.title,
            description: op.payload.description,
            original_url: op.payload.original_url || null, item_count: 0 } };
}
function membershipAck(op, revision) {
    return { code: "ACKNOWLEDGED", work_id: op.entity_id, changed: true,
        playlist_id: op.payload.playlist_id, playlist_title: "Lectures",
        server_revision: revision };
}

function runtimeFor(store, respond) {
    const silent = handler => Object.assign({}, handler, { reconcile: async () => true });
    return globalThis.createPrksSyncRuntime({
        store, online: () => true, request: respond,
        handlers: {
            CREATE_PLAYLIST: silent(globalThis.prksPlaylistCreateSyncHandler),
            SET_PLAYLIST_FIELD: silent(globalThis.prksPlaylistFieldSyncHandler),
            REORDER_PLAYLIST_ITEMS: silent(globalThis.prksPlaylistOrderSyncHandler),
            DELETE_PLAYLIST: silent(globalThis.prksPlaylistDeleteSyncHandler),
            SET_WORK_PLAYLIST: silent(globalThis.prksWorkPlaylistSyncHandler),
        },
    });
}

/* ---- construction ---- */

async function aPlaylistIsAValidDestinationImmediately() {
    const store = newStore();
    const created = await store.createPlaylist({ title: "Lectures", description: "Term one" });
    assert.match(created.entity_id, /^PL-[0-9A-F]{32}$/,
        "a permanent distributed id, minted here -- never remapped later");
    assert.equal(created.base_revision, null, "construction is not mutation");

    const ops = await store.listOperations();
    const index = globalThis.prksEffectivePlaylists([], ops);
    assert.equal(index.length, 1);
    assert.equal(index[0].title, "Lectures");
    assert.equal(index[0].item_count, 0, "a playlist minted here genuinely holds nothing");

    /* Adding a video to it waits for it, by the GENERIC mechanism: the server
     * cannot put anything in a playlist it has never heard of. */
    const added = await store.setWorkPlaylist("W-1", created.entity_id,
        { playlist_id: "", revision: 0 });
    assert.deepEqual(added.depends_on, [created.op_id]);

    const sent = [];
    const runtime = runtimeFor(store, async (_path, options) => {
        const body = JSON.parse(options.body);
        sent.push(body.operation);
        return { ok: true, status: 200, json: async () =>
            (body.operation === "CREATE_PLAYLIST" ? playlistAck(body) : membershipAck(body, 1)) };
    });
    await runtime.wake();
    await settle();
    runtime.stop();
    assert.deepEqual(sent, ["CREATE_PLAYLIST", "SET_WORK_PLAYLIST"],
        "the creation goes first, because the membership depends on it");
}

async function anEmptyTitleBecomesThePlaceholder() {
    const store = newStore();
    const created = await store.createPlaylist({ title: "   ", description: "" });
    assert.equal(created.payload.title, "Untitled playlist",
        "the same placeholder the ordinary endpoint substitutes");
}

/* ---- fields ---- */

async function fieldEditsCoalesceAndCancel() {
    const store = newStore();
    const id = "PL-" + "A".repeat(32);
    const base = baseAt({ title: 3 }, { title: "Lectures" });

    await store.savePlaylistFields(id, { title: "Seminars" }, base);
    assert.equal((await rowsFor(store, "SET_PLAYLIST_FIELD")).length, 1);

    /* A -> B -> C is still ONE operation, now carrying C: the user made one
     * decision about this field and has not sent any of it. */
    await store.savePlaylistFields(id, { title: "Workshops" }, base);
    const after = await rowsFor(store, "SET_PLAYLIST_FIELD");
    assert.equal(after.length, 1);
    assert.equal(after[0].payload.value, "Workshops");
    assert.equal(after[0].base_revision, 3, "still measured against what was acknowledged");

    /* B -> A, never sent, is ZERO operations: nothing about this field should
     * ever reach the server. */
    await store.savePlaylistFields(id, { title: "Lectures" }, base);
    assert.equal((await rowsFor(store, "SET_PLAYLIST_FIELD")).length, 0);
}

async function oneSyncingFieldDoesNotBlockAnother() {
    const store = newStore();
    const id = "PL-" + "B".repeat(32);
    const base = baseAt({}, {});
    const busy = await store.savePlaylistFields(id, { description: "Term two" }, base);
    await store.updateOperationSyncState(busy[0].op_id, { status: "syncing" });

    await store.savePlaylistFields(id, { title: "Seminars" }, base);
    assert.equal((await rowsFor(store, "SET_PLAYLIST_FIELD")).length, 2,
        "one decision is one conflict unit: a syncing description blocks nothing");

    await assert.rejects(() => store.savePlaylistFields(id, { description: "Term three" }, base),
        error => error.prksLocalStoreCode === "scope_busy",
        "but editing the field that is in flight is refused");
}

/* Edit opened on this device's own unsent A -> B edit pins A with B pending.
 * Going back to A cancels B while it is unsent; once B has left the queue the
 * server holds it, so going back to A is a change and is recorded. */
async function aRevertAfterTheShownEditSyncedIsSaved() {
    const core = require("../../frontend/js/sync-reconcile.js");
    const id = "PL-" + "R".repeat(32);
    const shown = { title: "Seminars", description: "", original_url: "" };
    const read = baseAt({ title: 3 }, { title: "Lectures" });
    const pinFor = async store => core.pinEditBase(shown, read, Object.fromEntries((await rowsFor(store,
        "SET_PLAYLIST_FIELD")).map(r => [r.payload.field, { value: r.payload.value, revision: r.base_revision, pendingOp: r.op_id }])));

    {
        const store = newStore();
        await store.savePlaylistFields(id, { title: "Seminars" }, read);
        const pinned = await pinFor(store);
        assert.equal(pinned.title.pending, "Seminars", "the pin knows the form showed an unsent edit");
        const edits = core.pinnedEdits(shown, Object.assign({}, shown, { title: "Lectures" }), pinned);
        await store.savePlaylistFields(id, edits.changes, edits.base);
        assert.equal((await rowsFor(store, "SET_PLAYLIST_FIELD")).length, 0, "unsent: the revert cancels it");
    }
    {
        const store = newStore();
        const [sent] = await store.savePlaylistFields(id, { title: "Seminars" }, read);
        const pinned = await pinFor(store);
        await store.updateOperationSyncState(sent.op_id, { status: "acknowledged", server_revision: 4 });
        const edits = core.pinnedEdits(shown, Object.assign({}, shown, { title: "Lectures" }), pinned);
        await store.savePlaylistFields(id, edits.changes, edits.base);
        const rows = (await rowsFor(store, "SET_PLAYLIST_FIELD")).filter(r => r.status !== "acknowledged");
        assert.equal(rows.length, 1, "acknowledged: the revert is recorded, not dropped");
        assert.equal(rows[0].payload.value, "Lectures");
        assert.equal(rows[0].base_revision, 4, "measured against the shown edit's ACK");
    }
    /* Another pane queues the same value at the old base after that ACK. */
    {
        const store = newStore();
        const [sent] = await store.savePlaylistFields(id, { title: "Seminars" }, read);
        const pinned = await pinFor(store);
        await store.updateOperationSyncState(sent.op_id, { status: "acknowledged", server_revision: 4 });
        await store.savePlaylistFields(id, { title: "Seminars" }, read);
        const edits = core.pinnedEdits(shown, Object.assign({}, shown, { title: "Lectures" }), pinned);
        await store.savePlaylistFields(id, edits.changes, edits.base);
        const rows = (await rowsFor(store, "SET_PLAYLIST_FIELD")).filter(r => r.status !== "acknowledged");
        assert.equal(rows.length, 1, "a matching queued row never drops the revert after the ACK");
        assert.equal(rows[0].payload.value, "Lectures");
        assert.equal(rows[0].base_revision, 4);
    }
    /* Opened on the unsent edit, which is then acknowledged: a new value
     * (not a revert) is measured from that ACK too, never from the revision
     * the edit replaced, which would conflict with this device's own write. */
    {
        const store = newStore();
        const [sent] = await store.savePlaylistFields(id, { title: "Seminars" }, read);
        const pinned = await pinFor(store);
        await store.updateOperationSyncState(sent.op_id, { status: "acknowledged", server_revision: 4 });
        const edits = core.pinnedEdits(shown, Object.assign({}, shown, { title: "Talks" }), pinned);
        await store.savePlaylistFields(id, edits.changes, edits.base);
        const rows = (await rowsFor(store, "SET_PLAYLIST_FIELD")).filter(r => r.status !== "acknowledged");
        assert.equal(rows.length, 1);
        assert.equal(rows[0].payload.value, "Talks");
        assert.equal(rows[0].base_revision, 4, "a new value after the ACK is measured from it");
    }
    {
        const store = newStore();
        const [sent] = await store.savePlaylistFields(id, { title: "Seminars" }, read);
        const pinned = await pinFor(store);
        await store.updateOperationSyncState(sent.op_id, { status: "acknowledged", server_revision: 4 });
        await store.deleteAcknowledgedOperation(sent.op_id);
        const edits = core.pinnedEdits(shown, Object.assign({}, shown, { title: "Lectures" }), pinned);
        await store.savePlaylistFields(id, edits.changes, edits.base);
        const rows = await rowsFor(store, "SET_PLAYLIST_FIELD");
        assert.equal(rows.length, 1, "retired: the revert is still recorded");
        assert.equal(rows[0].base_revision, 3,
            "against the pinned revision: a server that moved on refuses it as a conflict");
    }
    /* The retired edit's revision is taken from the edit-base snapshot its
     * own ACK moved. A snapshot holding any other value keeps the pin. */
    {
        const store = newStore();
        const [sent] = await store.savePlaylistFields(id, { title: "Seminars" }, read);
        const pinned = await pinFor(store);
        await store.updateOperationSyncState(sent.op_id, { status: "acknowledged", server_revision: 4 });
        await store.deleteAcknowledgedOperation(sent.op_id);
        const hold = (title, revision) => heldEntities.set("playlist-edit-base:" + id, { playlist_id: id,
            fields: Object.fromEntries(globalThis.PRKS_PLAYLIST_FIELDS.map(name => [name,
                name === "title" ? { value: title, revision } : { value: "", revision: 0 }])) });
        try {
            hold("Seminars", 4);
            const settled = await globalThis.prksSettlePinnedPlaylistBase(id, pinned, await store.listOperations());
            assert.deepEqual(settled.title, { value: "Seminars", revision: 4 },
                "a retired own edit is measured from the revision it produced");
            const edits = core.pinnedEdits(shown, Object.assign({}, shown, { title: "Talks" }), settled);
            await store.savePlaylistFields(id, edits.changes, edits.base);
            const [row] = await rowsFor(store, "SET_PLAYLIST_FIELD");
            assert.equal(row.base_revision, 4, "so this device's next write does not conflict with itself");
            const padded = Object.assign({}, pinned, { title: Object.assign({}, pinned.title, { pending: "  Seminars " }) });
            hold("  Seminars ", 4);
            assert.equal((await globalThis.prksSettlePinnedPlaylistBase(id, padded, [])).title.revision, 4,
                "the ACK holds the queued value as it was sent");
            hold("Seminars\uFEFF", 5);
            assert.deepEqual((await globalThis.prksSettlePinnedPlaylistBase(id, pinned, [])).title, pinned.title,
                "a value the server stores differently keeps the pinned revision");
            hold("Elsewhere", 5);
            assert.deepEqual((await globalThis.prksSettlePinnedPlaylistBase(id, pinned, [])).title, pinned.title,
                "a snapshot holding another device's value keeps the pinned revision");
            heldEntities.delete("playlist-edit-base:" + id);
            assert.deepEqual((await globalThis.prksSettlePinnedPlaylistBase(id, pinned, [])).title, pinned.title,
                "no snapshot keeps the pinned revision");
        } finally {
            heldEntities.delete("playlist-edit-base:" + id);
        }
    }
    /* The shown edit retired; a stale pane then queued the same value at the
     * same base. That row is not the shown edit, so the revert is kept. */
    {
        const store = newStore();
        const [sent] = await store.savePlaylistFields(id, { title: "Seminars" }, read);
        const pinned = await pinFor(store);
        await store.updateOperationSyncState(sent.op_id, { status: "acknowledged", server_revision: 4 });
        await store.deleteAcknowledgedOperation(sent.op_id);
        const [stale] = await store.savePlaylistFields(id, { title: "Seminars" }, read);
        assert.notEqual(stale.op_id, sent.op_id);
        const edits = core.pinnedEdits(shown, Object.assign({}, shown, { title: "Lectures" }), pinned);
        await store.savePlaylistFields(id, edits.changes, edits.base);
        const rows = await rowsFor(store, "SET_PLAYLIST_FIELD");
        assert.deepEqual(rows.map(r => [r.payload.value, r.base_revision]), [["Lectures", 3]],
            "with no snapshot, the revert is queued at the pin and a moved server refuses it");

        const wrapper = newStore();
        const [first] = await wrapper.savePlaylistFields(id, { title: "Seminars" }, read);
        const pinnedFirst = await pinFor(wrapper);
        await wrapper.updateOperationSyncState(first.op_id, { status: "acknowledged", server_revision: 4 });
        await wrapper.deleteAcknowledgedOperation(first.op_id);
        await wrapper.savePlaylistFields(id, { title: "Seminars" }, read);
        heldEntities.set("playlist-edit-base:" + id, { playlist_id: id,
            fields: Object.fromEntries(globalThis.PRKS_PLAYLIST_FIELDS.map(name => [name,
                name === "title" ? { value: "Seminars", revision: 4 } : { value: "", revision: 0 }])) });
        const sync = globalThis.prksSync;
        globalThis.prksSync = { store: wrapper };
        try {
            await globalThis.updatePlaylist(id, { title: "Lectures" }, { base: pinnedFirst });
        } finally {
            globalThis.prksSync = sync;
            heldEntities.delete("playlist-edit-base:" + id);
        }
        assert.deepEqual((await rowsFor(wrapper, "SET_PLAYLIST_FIELD")).map(r => [r.payload.value, r.base_revision]),
            [["Lectures", 4]], "updatePlaylist settles from the snapshot and writes the revert at its ACK");
    }
    /* Another pane replaced the synced edit (Seminars -> Talks) before this
     * form, opened on the unsent edit, returned to Lectures: the revert is a
     * write measured from the ACK, not a silent cancellation. */
    {
        const store = newStore();
        const [sent] = await store.savePlaylistFields(id, { title: "Seminars" }, read);
        const pinned = await pinFor(store);
        await store.updateOperationSyncState(sent.op_id, { status: "acknowledged", server_revision: 4 });
        await store.savePlaylistFields(id, { title: "Talks" }, baseAt({ title: 4 }, { title: "Seminars" }));
        const edits = core.pinnedEdits(shown, Object.assign({}, shown, { title: "Lectures" }), pinned);
        await store.savePlaylistFields(id, edits.changes, edits.base);
        const rows = (await rowsFor(store, "SET_PLAYLIST_FIELD")).filter(r => r.status !== "acknowledged");
        assert.equal(rows.length, 1, "the revert replaces the other pane's edit");
        assert.equal(rows[0].payload.value, "Lectures");
        assert.equal(rows[0].base_revision, 4, "measured against the shown edit's ACK");
    }
    /* A title cleared offline shows as the default name: reopening Edit pins
     * it, so a new title replaces the unsent edit. */
    {
        const store = newStore();
        await store.savePlaylistFields(id, { title: "" }, read);
        const state = { playlist_id: id, order_revision: 0, fields: Object.fromEntries(
            globalThis.PRKS_PLAYLIST_FIELDS.map(name => [name, { revision: name === "title" ? 3 : 0 }])) };
        const previous = globalThis.prksOfflineReadEntity;
        globalThis.prksOfflineReadEntity = async kind => (kind === "playlist-state"
            ? { value: state, source: "server" }
            : { value: { id, title: "Lectures", description: "", original_url: "" }, source: "server" });
        const ops = await rowsFor(store, "SET_PLAYLIST_FIELD");
        const cleared = { title: "Untitled playlist", description: "", original_url: "" };
        const pinned = await globalThis.prksPinPlaylistEditBase(id, cleared, ops);
        globalThis.prksOfflineReadEntity = previous;
        assert.deepEqual(pinned.title, { value: "Lectures", revision: 3, pending: "", pendingOp: ops[0].op_id },
            "the default name stands for the pending empty title");
        const edits = core.pinnedEdits(cleared, Object.assign({}, cleared, { title: "Talks" }), pinned);
        assert.deepEqual(edits.unknown || [], [], "a new title is not refused as changed elsewhere");
        assert.equal(edits.changes.title, "Talks");
        await store.savePlaylistFields(id, edits.changes, edits.base);
        const queued = await rowsFor(store, "SET_PLAYLIST_FIELD");
        assert.equal(queued.length, 1, "the new title replaces the queued empty one");
        assert.equal(queued[0].payload.value, "Talks");
        assert.equal(queued[0].base_revision, 3, "measured against the base the empty title was");
    }
    /* A title and URL saved with surrounding spaces show trimmed: reopening
     * Edit still pins them, keeping the raw pending text as the base. */
    {
        const store = newStore();
        await store.savePlaylistFields(id, { title: "  Seminars  ", original_url: " https://example.org/x " }, read);
        const state = { playlist_id: id, order_revision: 0, fields: Object.fromEntries(
            globalThis.PRKS_PLAYLIST_FIELDS.map(name => [name, { revision: name === "title" ? 3 : 0 }])) };
        const previous = globalThis.prksOfflineReadEntity;
        globalThis.prksOfflineReadEntity = async kind => (kind === "playlist-state"
            ? { value: state, source: "server" }
            : { value: { id, title: "Lectures", description: "", original_url: "" }, source: "server" });
        const ops = await rowsFor(store, "SET_PLAYLIST_FIELD");
        const trimmed = { title: "Seminars", description: "", original_url: "https://example.org/x" };
        const pinned = await globalThis.prksPinPlaylistEditBase(id, trimmed, ops);
        globalThis.prksOfflineReadEntity = previous;
        assert.deepEqual(pinned.title, { value: "Lectures", revision: 3, pending: "  Seminars  ", pendingOp: ops.find(r => r.payload.field === "title").op_id },
            "the trimmed title stands for the raw pending one");
        assert.deepEqual(pinned.original_url, { value: "", revision: 0, pending: " https://example.org/x ",
            pendingOp: ops.find(r => r.payload.field === "original_url").op_id },
            "and the trimmed URL for its raw pending value");
        const edits = core.pinnedEdits(trimmed, Object.assign({}, trimmed, { title: "Talks" }), pinned);
        assert.deepEqual(edits.unknown || [], [], "a new title is not refused as changed elsewhere");
        await store.savePlaylistFields(id, edits.changes, edits.base);
        const titles = (await rowsFor(store, "SET_PLAYLIST_FIELD")).filter(r => r.payload.field === "title");
        assert.equal(titles.length, 1, "the new title replaces the queued padded one");
        assert.equal(titles[0].payload.value, "Talks");
        assert.equal(titles[0].base_revision, 3);
    }
    /* Nothing pending: the acknowledged title and URL alone. Whitespace the
     * form trims still pins the raw stored text; a genuinely different stored
     * value is not pinned, so a save from that form is refused as changed
     * elsewhere instead of overwriting it. */
    {
        const store = newStore();
        const state = { playlist_id: id, order_revision: 0, fields: Object.fromEntries(
            globalThis.PRKS_PLAYLIST_FIELDS.map(name => [name, { revision: name === "description" ? 0 : 2 }])) };
        const previous = globalThis.prksOfflineReadEntity;
        globalThis.prksOfflineReadEntity = async kind => (kind === "playlist-state"
            ? { value: state, source: "server" }
            : { value: { id, title: "  Lectures  ", description: "", original_url: " https://example.org/y " },
                source: "server" });
        const ops = await rowsFor(store, "SET_PLAYLIST_FIELD");
        assert.equal(ops.length, 0, "nothing is pending");
        const trimmed = { title: "Lectures", description: "", original_url: "https://example.org/y" };
        const pinned = await globalThis.prksPinPlaylistEditBase(id, trimmed, ops);
        assert.deepEqual(pinned.title, { value: "  Lectures  ", revision: 2 },
            "the trimmed title stands for the stored padded one");
        assert.deepEqual(pinned.original_url, { value: " https://example.org/y ", revision: 2 },
            "and the trimmed URL for the stored padded URL");
        const edits = core.pinnedEdits(trimmed, Object.assign({}, trimmed, { title: "Talks" }), pinned);
        assert.deepEqual(edits.unknown || [], [], "a new title is measured from the stored one");
        assert.equal(edits.base.title.revision, 2);
        const other = { title: "Other", description: "", original_url: "https://example.org/z" };
        const stale = await globalThis.prksPinPlaylistEditBase(id, other, ops);
        globalThis.prksOfflineReadEntity = previous;
        assert.equal(stale.title, null, "a different stored title is not pinned");
        assert.equal(stale.original_url, null, "nor a different stored URL");
        const refused = core.pinnedEdits(other, Object.assign({}, other, { title: "Talks" }), stale);
        assert.deepEqual(refused.unknown, ["title"], "a save from it is refused, never sent over the stored title");
    }
}

/* ---- membership ---- */

async function aVideosPlaylistIsAScalarThatCancels() {
    const store = newStore();
    const first = "PL-" + "C".repeat(32);
    const second = "PL-" + "D".repeat(32);

    await store.setWorkPlaylist("W-1", second, { playlist_id: first, revision: 4 });
    let rows = await rowsFor(store, "SET_WORK_PLAYLIST");
    assert.equal(rows.length, 1);
    assert.equal(rows[0].entity_type, "work", "the scope is the VIDEO, not the playlist");
    assert.equal(rows[0].base_revision, 4);

    /* Moving it on again is still one operation: a video is in at most one
     * playlist, so there is only ever one answer in flight. */
    await store.setWorkPlaylist("W-1", "", { playlist_id: first, revision: 4 });
    rows = await rowsFor(store, "SET_WORK_PLAYLIST");
    assert.equal(rows.length, 1);
    assert.equal(rows[0].payload.playlist_id, "", "removing is the same scalar, emptied");

    /* Back where it started, never sent: zero operations. */
    await store.setWorkPlaylist("W-1", first, { playlist_id: first, revision: 4 });
    assert.equal((await rowsFor(store, "SET_WORK_PLAYLIST")).length, 0);
}

async function aPendingMembershipShowsOnBothEnds() {
    const store = newStore();
    const id = "PL-" + "E".repeat(32);
    await store.setWorkPlaylist("W-1", id, { playlist_id: "", revision: 0 });
    const ops = await store.listOperations();

    /* The video's own page names the playlist ... */
    const work = globalThis.prksEffectiveWorkPlaylist(
        { id: "W-1", playlist_id: null, playlist_title: null }, ops,
        [{ id: id, title: "Lectures" }]);
    assert.equal(work.playlist_id, id);
    assert.equal(work.playlist_title, "Lectures");

    /* ... and the playlist's own page lists the video, from a row that came
     * from a catalogue this device actually holds. */
    const detail = globalThis.prksEffectivePlaylistDetail(
        { id: id, title: "Lectures", items: [] }, ops, [item("W-1", "Episode one")]);
    assert.deepEqual(detail.items.map(row => row.id), ["W-1"]);
    assert.equal(detail.item_count, 1);

    /* Nothing is invented for a video whose row this device does not have. */
    const blind = globalThis.prksEffectivePlaylistDetail(
        { id: id, title: "Lectures", items: [] }, ops, []);
    assert.deepEqual(blind.items, []);
}

/* ---- order ---- */

async function theOrderIsOneAggregateThatCoalesces() {
    const store = newStore();
    const id = "PL-" + "F".repeat(32);
    const observed = { work_ids: ["W-1", "W-2", "W-3"], revision: 2 };

    const first = await store.reorderPlaylistItems(id, ["W-2", "W-1", "W-3"], observed);
    assert.equal(first.base_revision, 2);
    assert.deepEqual(first.payload.work_ids, ["W-2", "W-1", "W-3"],
        "the whole order travels, not one moved index");

    /* A second drag REPLACES the first: both describe the same decision --
     * "this is the order" -- and the later one is what the user is seeing. */
    await store.reorderPlaylistItems(id, ["W-3", "W-2", "W-1"], observed);
    const rows = await rowsFor(store, "REORDER_PLAYLIST_ITEMS");
    assert.equal(rows.length, 1);
    assert.deepEqual(rows[0].payload.work_ids, ["W-3", "W-2", "W-1"]);

    /* Dragging it back to where the server has it leaves no intent. */
    await store.reorderPlaylistItems(id, ["W-1", "W-2", "W-3"], observed);
    assert.equal((await rowsFor(store, "REORDER_PLAYLIST_ITEMS")).length, 0);
}

async function aPendingOrderShowsOnThePlaylistPage() {
    const store = newStore();
    const id = "PL-" + "1".repeat(32);
    await store.reorderPlaylistItems(id, ["W-3", "W-1"],
        { work_ids: ["W-1", "W-2", "W-3"], revision: 0 });
    const ops = await store.listOperations();
    const detail = globalThis.prksEffectivePlaylistDetail(
        { id: id, title: "Lectures", items: [item("W-1"), item("W-2"), item("W-3")] },
        ops, []);
    assert.deepEqual(detail.items.map(row => row.id), ["W-3", "W-1", "W-2"],
        "a video the order omitted keeps its relative place, exactly as the server resolves it");
}

async function anOrderIsNotAMembershipChange() {
    const store = newStore();
    const id = "PL-" + "2".repeat(32);
    await store.reorderPlaylistItems(id, ["W-9", "W-2"],
        { work_ids: ["W-1", "W-2"], revision: 0 });
    const ops = await store.listOperations();
    const detail = globalThis.prksEffectivePlaylistDetail(
        { id: id, title: "Lectures", items: [item("W-1"), item("W-2")] }, ops, []);
    assert.deepEqual(detail.items.map(row => row.id), ["W-2", "W-1"],
        "an id the playlist does not hold is ignored rather than added");
}

async function anOrderTooLongToTravelIsRefused() {
    const store = newStore();
    const id = "PL-" + "3".repeat(32);
    const tooMany = [];
    for (let i = 0; i <= globalThis.PRKS_LOCAL_PLAYLIST_MAX_ITEMS; i += 1) {
        tooMany.push("W-" + i);
    }
    await assert.rejects(
        () => store.reorderPlaylistItems(id, tooMany, { work_ids: [], revision: 0 }),
        error => error.prksLocalStoreCode === "invalid_envelope",
        "an order travels as ONE payload, so its length is bounded by the envelope");
}

/* ---- deletion ---- */

async function deletingCancelsWhatWasNeverSent() {
    const store = newStore();
    const id = "PL-" + "4".repeat(32);
    await store.savePlaylistFields(id, { title: "Seminars" }, baseAt({}, {}));
    await store.setWorkPlaylist("W-1", id, { playlist_id: "", revision: 0 });

    const deletion = await store.deletePlaylist(id);
    assert.equal((await rowsFor(store, "SET_PLAYLIST_FIELD")).length, 0);
    assert.equal((await rowsFor(store, "SET_WORK_PLAYLIST")).length, 0,
        "sending 'put this here' immediately before 'delete this' asks the server "
        + "to do work the next operation destroys");
    assert.equal(deletion.base_revision, null, "destruction addresses an identity");
    assert.deepEqual(deletion.depends_on, []);
}

async function aSentMembershipIsWaitedForRatherThanRewritten() {
    const store = newStore();
    const id = "PL-" + "5".repeat(32);
    const added = await store.setWorkPlaylist("W-1", id, { playlist_id: "", revision: 0 });
    await store.updateOperationSyncState(added.op_id, { status: "syncing" });

    const deletion = await store.deletePlaylist(id);
    assert.equal((await rowsFor(store, "SET_WORK_PLAYLIST")).length, 1,
        "a row that may already be on the wire stays immutable");
    assert.deepEqual(deletion.depends_on, [added.op_id],
        "so the deletion is ordered behind it instead");
}

async function deletingAPlaylistCreatedHereFoldsItAway() {
    const store = newStore();
    const created = await store.createPlaylist({ title: "Typed by accident" });
    await store.savePlaylistFields(created.entity_id, { description: "oops" },
        baseAt({}, {}));
    assert.equal(await store.deletePlaylist(created.entity_id), null);
    assert.deepEqual(await store.listOperations(), [],
        "nothing about this playlist should ever reach the server");
}

async function aPendingDeletionIsATombstone() {
    const store = newStore();
    const id = "PL-" + "6".repeat(32);
    await store.deletePlaylist(id);
    const ops = await store.listOperations();
    const index = globalThis.prksEffectivePlaylists(
        [{ id: id, title: "Lectures", item_count: 2 }], ops);
    assert.deepEqual(index, [], "hidden, with nothing acknowledged destroyed");

    /* And a video that names it stops naming it -- the memberships went with
     * the playlist, so showing its old title would be a value nothing holds. */
    const work = globalThis.prksEffectiveWorkPlaylist(
        { id: "W-1", playlist_id: id, playlist_title: "Lectures" }, ops, []);
    assert.equal(work.playlist_id, null);
    assert.equal(work.playlist_title, null);

    await assert.rejects(
        () => store.savePlaylistFields(id, { title: "Seminars" }, baseAt({}, {})),
        error => error.prksLocalStoreCode === "entity_deleted");
}

/* ---- bases ---- */

async function theBaseIsAcknowledgedAndNeverGuessed() {
    const store = newStore();
    globalThis.prksSync = { store: store };
    const created = await store.createPlaylist({ title: "Lectures", description: "Term one" });
    const ops = await store.listOperations();

    const base = await globalThis.prksAcknowledgedPlaylistBase(created.entity_id, ops);
    assert.equal(base.title.value, "Lectures", "the construction payload IS the base");
    assert.equal(base.title.revision, 0, "at revision 0 -- known, not assumed");

    const draft = { title: "Lectures", description: "Term one", original_url: "" };
    const pinned = await globalThis.prksPinPlaylistEditBase(created.entity_id, draft, ops);
    const core = require("../../frontend/js/sync-reconcile.js");
    assert.deepEqual(core.pinnedEdits(draft, draft, pinned).changes, {},
        "reopening the form and saving changes nothing");
    assert.deepEqual(core.pinnedEdits(draft, Object.assign({}, draft, { description: "Term two" }), pinned),
        { changes: { description: "Term two" }, base: pinned, unknown: [] }, "only what was typed");

    /* The order of a playlist created here is genuinely empty at revision 0:
     * nothing else can have written to an id no other device has seen. */
    assert.deepEqual(
        await globalThis.prksAcknowledgedPlaylistOrder(created.entity_id, ops),
        { work_ids: [], revision: 0 });

    globalThis.prksOfflineReadEntity = async () => ({ value: null, source: "unavailable" });
    globalThis.prksOfflineInvalidateEntity = async () => true;
    assert.equal(await globalThis.prksAcknowledgedPlaylistBase("PL-UNKNOWN", ops), null,
        "guessing a revision would silently overwrite another device");
    assert.equal(await globalThis.prksAcknowledgedPlaylistOrder("PL-UNKNOWN", ops), null);

    /* Revisions from the server with values from the cache are two snapshots:
     * the newer revision would vouch for older text. */
    const known = "PL-" + "K".repeat(32);
    const sources = { state: "server", record: "cache" };
    const recordOptions = [];
    globalThis.prksOfflineReadEntity = async (kind, id, _path, options) => {
        if (kind === "playlist-state") return { value: globalThis.prksNewPlaylistState(id), source: sources.state };
        recordOptions.push(options || {});
        return { value: { id, title: "Old", description: "", original_url: "", items: [] }, source: sources.record };
    };
    assert.equal(await globalThis.prksAcknowledgedPlaylistBase(known, []), null,
        "server revisions never pair with cached values");
    sources.record = "server";
    recordOptions.length = 0;
    assert.equal((await globalThis.prksAcknowledgedPlaylistBase(known, [])).title.value, "Old",
        "values read from the server after the revisions are one base");
    assert.equal(recordOptions[0].requestPolicy && recordOptions[0].requestPolicy.dedupe, false,
        "the record read never joins a flight older than the revisions");
    /* Offline, the base is the pair last read together, never cached
     * revisions beside a cached record: either may have been refreshed
     * without the other (a refused mixed read, an order read). */
    const offlineState = globalThis.prksNewPlaylistState(known);
    offlineState.fields.title.revision = 7;
    globalThis.prksOfflineReadEntity = async (kind, id) => (kind === "playlist-state"
        ? { value: offlineState, source: "cache" }
        : { value: { id, title: "Older", description: "", original_url: "", items: [] }, source: "cache" });
    assert.deepEqual((await globalThis.prksAcknowledgedPlaylistBase(known, [])).title, { value: "Old", revision: 0 },
        "the offline base is the pair read together, not revision 7 beside a cached record");
    heldEntities.delete("playlist-edit-base:" + known);
    assert.equal(await globalThis.prksAcknowledgedPlaylistBase(known, []), null,
        "with no pair read together, offline has no base");
    /* An ACK that lands while an online read is in flight is newer than it. */
    globalThis.prksOfflineReadEntity = async (kind, id) => (kind === "playlist-state"
        ? { value: globalThis.prksNewPlaylistState(id), source: "server" }
        : { value: { id, title: "Read", description: "Read", original_url: "", items: [] }, source: "server" });
    heldEntities.set("playlist-edit-base:" + known, { playlist_id: known, fields: {
        title: { value: "Mine", revision: 4 }, description: { value: "", revision: 0 }, original_url: { value: "", revision: 0 } } });
    await globalThis.prksAcknowledgedPlaylistBase(known, []);
    const kept = heldEntities.get("playlist-edit-base:" + known).fields;
    assert.deepEqual(kept.title, { value: "Mine", revision: 4 }, "a late read never rolls the acknowledged pair back");
    assert.deepEqual(kept.description, { value: "Read", revision: 0 }, "and still records the fields it read");
    heldEntities.delete("playlist-edit-base:" + known);
    /* Another device writes while the record is read: the values are past the revisions. */
    let racedRevision = 0;
    globalThis.prksOfflineReadEntity = async (kind, id) => {
        if (kind !== "playlist-state") {
            racedRevision += 1;
            return { value: { id, title: "Raced", description: "", original_url: "", items: [] }, source: "server" };
        }
        const state = globalThis.prksNewPlaylistState(id);
        state.fields.title.revision = racedRevision;
        return { value: state, source: "server" };
    };
    assert.equal(await globalThis.prksAcknowledgedPlaylistBase(known, []), null,
        "values read across a revision change form no base");
    assert.equal(heldEntities.get("playlist-edit-base:" + known), undefined, "and that pair is not kept");
    globalThis.prksOfflineReadEntity = async (kind, id) => (kind === "playlist-state"
        ? { value: globalThis.prksNewPlaylistState(id), source: "server" }
        : { value: { id, title: "Read", description: "Read", original_url: "", items: [] }, source: "server" });
    /* Another tab commits its ACK after this read and before this tab's write. */
    const merge = globalThis.prksOfflineMergeEntity;
    globalThis.prksOfflineMergeEntity = async (kind, id, fn) => {
        heldEntities.set(kind + ":" + id, { playlist_id: known, fields: {
            title: { value: "Theirs", revision: 4 }, description: { value: "", revision: 0 }, original_url: { value: "", revision: 0 } } });
        return merge(kind, id, fn);
    };
    try {
        await globalThis.prksAcknowledgedPlaylistBase(known, []);
    } finally {
        globalThis.prksOfflineMergeEntity = merge;
    }
    assert.deepEqual(heldEntities.get("playlist-edit-base:" + known).fields.title, { value: "Theirs", revision: 4 },
        "a delayed write merges against the pair another tab committed");
    heldEntities.delete("playlist-edit-base:" + known);
}


/* The four relocated E2Es called these public wrappers with page.evaluate;
 * retain their observable queue/revision contract here with no browser/server. */
async function playlistApiCancellationOrderMembershipAndDeleteContract() {
    const store = newStore();
    globalThis.prksSync = { store };
    const existing = "PL-" + "E".repeat(32);
    const other = "PL-" + "F".repeat(32);
    const work = "W-1";
    const state = globalThis.prksNewPlaylistState(existing);
    state.fields.title.revision = 3;
    state.order_revision = 2;
    globalThis.prksOfflineReadEntity = async (kind, id) => {
        if (kind === "work-playlist-state" && id === work) return {
            value: { work_id: work, playlist_id: existing, revision: 4 }, source: "cache"
        };
        if (id !== existing) return { value: null, source: "unavailable" };
        if (kind === "playlist-state") return { value: state, source: "cache" };
        if (kind === "playlist") return {
            value: {
                id: existing, title: "Lectures", description: "",
                original_url: "", items: [item("W-1"), item("W-2")]
            }, source: "cache"
        };
        return { value: null, source: "unavailable" };
    };
    globalThis.prksOfflineInvalidateEntity = async () => true;
    // Offline, the base is the pair an earlier online Edit read together.
    heldEntities.set("playlist-edit-base:" + existing, { playlist_id: existing, fields: {
        title: { value: "Lectures", revision: 3 },
        description: { value: "", revision: 0 },
        original_url: { value: "", revision: 0 },
    } });

    const shown = { title: "Lectures", description: "", original_url: "" };
    const base = await globalThis.prksPinPlaylistEdit(existing, shown);
    assert.deepEqual(base.title, { value: "Lectures", revision: 3 }, "Edit pins the shown revision");
    await globalThis.updatePlaylist(existing, { title: "Temporary" }, { base });
    let rows = await rowsFor(store, "SET_PLAYLIST_FIELD");
    assert.equal(rows.length, 1);
    assert.equal(rows[0].payload.field, "title");
    assert.equal(rows[0].payload.value, "Temporary");
    assert.equal(rows[0].base_revision, 3);

    // Mutation negative-control: disable the actual durable writer. The
    // contract must detect that a nominally successful wrapper call failed
    // to cancel its previously queued operation.
    const write = globalThis.prksSavePlaylistFieldsDurably;
    try {
        globalThis.prksSavePlaylistFieldsDurably = async () => null;
        await globalThis.updatePlaylist(existing, { title: "Lectures" }, { base });
        rows = await rowsFor(store, "SET_PLAYLIST_FIELD");
        assert.throws(() => assert.deepEqual(rows, []), /AssertionError|Expected values/);
    } finally {
        globalThis.prksSavePlaylistFieldsDurably = write;
    }
    await globalThis.updatePlaylist(existing, { title: "Lectures" }, { base });
    assert.deepEqual(await rowsFor(store, "SET_PLAYLIST_FIELD"), [],
        "real updatePlaylist A→B→A leaves no intent");

    /* A Save whose session ends while its base is settled writes nothing. */
    {
        let live = true;
        const settle = globalThis.prksSettlePinnedPlaylistBase;
        globalThis.prksSettlePinnedPlaylistBase = async (...args) => { live = false; return settle(...args); };
        try {
            await globalThis.updatePlaylist(existing, { title: "Cancelled" }, { base, still: () => live });
        } finally {
            globalThis.prksSettlePinnedPlaylistBase = settle;
        }
        assert.deepEqual(await rowsFor(store, "SET_PLAYLIST_FIELD"), [],
            "a Save cancelled during its base read queues nothing");
        live = true;
        const write = globalThis.prksSavePlaylistFieldsDurably;
        globalThis.prksSavePlaylistFieldsDurably = (...args) => { const saved = write(...args); live = false; return saved; };
        try {
            await globalThis.updatePlaylist(existing, { title: "Cancelled" }, { base, still: () => live });
        } finally {
            globalThis.prksSavePlaylistFieldsDurably = write;
        }
        assert.deepEqual(await rowsFor(store, "SET_PLAYLIST_FIELD"), [],
            "a Save cancelled during its queue read queues nothing");
    }

    /* A session that ends after its replacement deleted the queued row aborts the write. */
    {
        await store.savePlaylistFields(existing, { title: "Queued" }, base);
        let checks = 0;
        await assert.rejects(store.savePlaylistFields(existing, { title: "Replaced" }, base, () => ++checks < 2),
            err => err.prksLocalStoreCode === "ownership_lost");
        assert.deepEqual((await rowsFor(store, "SET_PLAYLIST_FIELD")).map(r => r.payload.value), ["Queued"],
            "the queued row comes back");
        await store.savePlaylistFields(existing, { title: base.title.value }, base);
    }

    /* #894: another tab saves the title after this editor opened. This
     * session's Save keeps the revision it was shown, so the server can
     * refuse it as a conflict instead of it replacing that save. */
    state.fields.title.revision = 4;
    await globalThis.updatePlaylist(existing, { title: "From a stale tab" }, { base });
    rows = await rowsFor(store, "SET_PLAYLIST_FIELD");
    assert.equal(rows.length, 1);
    assert.equal(rows[0].base_revision, 3, "a stale editor never adopts the newer revision");
    await globalThis.updatePlaylist(existing, { title: "Lectures" }, { base });
    assert.deepEqual(await rowsFor(store, "SET_PLAYLIST_FIELD"), []);
    state.fields.title.revision = 3;

    await globalThis.reorderPlaylist(existing, ["W-2", "W-1"]);
    rows = await rowsFor(store, "REORDER_PLAYLIST_ITEMS");
    assert.equal(rows.length, 1);
    assert.equal(rows[0].base_revision, 2);
    assert.deepEqual(rows[0].payload.work_ids, ["W-2", "W-1"]);
    await globalThis.reorderPlaylist(existing, ["W-1", "W-2"]);
    assert.deepEqual(await rowsFor(store, "REORDER_PLAYLIST_ITEMS"), [],
        "real reorderPlaylist second order supersedes first and cancels");

    await globalThis.addWorkToPlaylist(other, work);
    rows = await rowsFor(store, "SET_WORK_PLAYLIST");
    assert.equal(rows.length, 1);
    assert.equal(rows[0].payload.playlist_id, other);
    assert.equal(rows[0].base_revision, 4);
    await globalThis.addWorkToPlaylist(existing, work);
    assert.deepEqual(await rowsFor(store, "SET_WORK_PLAYLIST"), [],
        "real membership wrapper returns to server-known base with no request");

    const created = await globalThis.createPlaylist("Never sent", "Draft");
    assert.match(created, /^PL-[0-9A-F]{32}$/);
    assert.equal((await rowsFor(store, "CREATE_PLAYLIST")).length, 1);
    assert.deepEqual(await globalThis.deletePlaylistCanonical(created), { status: "deleted" });
    assert.deepEqual(await store.listOperations(), [],
        "real createPlaylist→deletePlaylistCanonical folds every unsent intent");
    // The deleted E2E also reconnected and checked the canonical DB. Reopen
    // transport on the empty durable queue: even a successfully running sync
    // worker must never attempt to send a folded create/delete pair.
    const sent = [];
    const runtime = runtimeFor(store, async envelope => {
        sent.push(envelope);
        throw new Error("folded create/delete reached transport");
    });
    try {
        await runtime.wake();
        await settle();
        assert.deepEqual(sent, [], "reconnect cannot transmit a folded operation");
    } finally {
        runtime.stop();
    }
    delete globalThis.prksSync;
}

async function main() {
    await aPlaylistIsAValidDestinationImmediately();
    await anEmptyTitleBecomesThePlaceholder();
    await fieldEditsCoalesceAndCancel();
    await aRevertAfterTheShownEditSyncedIsSaved();
    await oneSyncingFieldDoesNotBlockAnother();
    await aVideosPlaylistIsAScalarThatCancels();
    await aPendingMembershipShowsOnBothEnds();
    await theOrderIsOneAggregateThatCoalesces();
    await aPendingOrderShowsOnThePlaylistPage();
    await anOrderIsNotAMembershipChange();
    await anOrderTooLongToTravelIsRefused();
    await deletingCancelsWhatWasNeverSent();
    await aSentMembershipIsWaitedForRatherThanRewritten();
    await deletingAPlaylistCreatedHereFoldsItAway();
    await aPendingDeletionIsATombstone();
    await theBaseIsAcknowledgedAndNeverGuessed();
    await playlistApiCancellationOrderMembershipAndDeleteContract();
    console.log("All " + checks + " playlist checks passed");
}

main()
    .then(() => process.exit(0))
    .catch(error => { console.error(error); process.exit(1); });
