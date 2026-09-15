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

let sequence = 0;
const uuid = () => "00000000-0000-4000-8000-" + (++sequence).toString(16).padStart(12, "0");
const tick = () => new Promise(resolve => setTimeout(resolve, 1));
async function settle() { for (let i = 0; i < 16; i++) await tick(); }
const newStore = () => createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });

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
    assert.deepEqual(
        globalThis.prksDirtyPlaylistFields(created.entity_id, draft, base, ops), {},
        "reopening the form and saving changes nothing");
    assert.deepEqual(
        globalThis.prksDirtyPlaylistFields(created.entity_id,
            Object.assign({}, draft, { description: "Term two" }), base, ops),
        { description: "Term two" }, "only what was typed");

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
}

async function main() {
    await aPlaylistIsAValidDestinationImmediately();
    await anEmptyTitleBecomesThePlaceholder();
    await fieldEditsCoalesceAndCancel();
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
    console.log("All " + checks + " playlist checks passed");
}

main()
    .then(() => process.exit(0))
    .catch(error => { console.error(error); process.exit(1); });
