"use strict";
/* Folders: four shapes, and how they compose.
 *
 * What this covers and nothing else does cheaply: a folder created here is a
 * valid destination immediately and everything filed into it waits for it;
 * moving a folder is a FIELD edit that cancels when taken back; which folder a
 * Work is in is a scalar on the WORK, so filing, moving and clearing are one
 * operation; and deleting reasons about every intent naming the folder rather
 * than leaving the server work the next operation destroys.
 */
const strict = require("assert/strict");
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); },
    { get: (_t, k) => (...args) => { checks += 1; return strict[k](...args); } });
const { createFakeIndexedDBFactory } = require("./lib/fake_indexeddb.js");
const { createPrksLocalStore } = require("../../frontend/js/local-store.js");
require("../../frontend/js/sync-runtime.js");
require("../../frontend/js/folder-state.js");

let sequence = 0;
const uuid = () => "00000000-0000-4000-8000-" + (++sequence).toString(16).padStart(12, "0");
const tick = () => new Promise(resolve => setTimeout(resolve, 1));
async function settle() { for (let i = 0; i < 16; i++) await tick(); }
const newStore = () => createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });

function baseAt(revisions, values) {
    const base = {};
    globalThis.PRKS_FOLDER_FIELDS.forEach(function (name) {
        base[name] = {
            value: (values && values[name]) || "",
            revision: (revisions && revisions[name]) || 0,
        };
    });
    return base;
}

const rowsFor = async (store, operation) =>
    (await store.listOperations()).filter(r => r.operation === operation);

function folderAck(op) {
    return { code: "ACKNOWLEDGED", folder_id: op.entity_id, changed: true,
        folder: { id: op.entity_id, title: op.payload.title,
            description: op.payload.description, private_notes: op.payload.private_notes,
            parent_id: op.payload.parent_id || null, work_count: 0, child_count: 0 } };
}
function filingAck(op, revision) {
    return { code: "ACKNOWLEDGED", work_id: op.entity_id, changed: true,
        folder_id: op.payload.folder_id, folder_title: "Drafts",
        server_revision: revision };
}

function runtimeFor(store, respond) {
    const silent = handler => Object.assign({}, handler, { reconcile: async () => true });
    return globalThis.createPrksSyncRuntime({
        store, online: () => true, request: respond,
        handlers: {
            CREATE_FOLDER: silent(globalThis.prksFolderCreateSyncHandler),
            SET_FOLDER_FIELD: silent(globalThis.prksFolderFieldSyncHandler),
            DELETE_FOLDER: silent(globalThis.prksFolderDeleteSyncHandler),
            SET_WORK_FOLDER: silent(globalThis.prksWorkFolderSyncHandler),
        },
    });
}

/* ---- construction ---- */

async function aFolderIsAValidDestinationImmediately() {
    const store = newStore();
    const created = await store.createFolder({ title: "Drafts", description: "WIP" });
    assert.match(created.entity_id, /^F-[0-9A-F]{32}$/,
        "a permanent distributed id, minted here -- never remapped later");
    assert.equal(created.base_revision, null, "construction is not mutation");

    const ops = await store.listOperations();
    const library = globalThis.prksEffectiveFolders([], ops);
    assert.equal(library.length, 1);
    assert.equal(library[0].title, "Drafts");

    /* Filing a Work into it waits for it, by the GENERIC mechanism: the server
     * cannot file anything in a folder it has never heard of. */
    const filed = await store.setWorkFolder("W-1", created.entity_id,
        { folder_id: "", revision: 0 });
    assert.deepEqual(filed.depends_on, [created.op_id]);

    const sent = [];
    const runtime = runtimeFor(store, async (_path, options) => {
        const body = JSON.parse(options.body);
        sent.push(body.operation);
        return { ok: true, status: 200, json: async () =>
            (body.operation === "CREATE_FOLDER" ? folderAck(body) : filingAck(body, 1)) };
    });
    await runtime.wake();
    await settle();
    runtime.stop();
    assert.deepEqual(sent, ["CREATE_FOLDER", "SET_WORK_FOLDER"],
        "the folder exists on the server before anything is filed in it");
    assert.equal((await store.listOperations()).length, 0, "and both retire");
}

async function anEmptyTitleBecomesThePlaceholder() {
    const store = newStore();
    const created = await store.createFolder({ title: "   " });
    assert.equal(created.payload.title, "Untitled Folder",
        "the same substitution the ordinary endpoint has always made");
}

async function aFolderInsideALocalFolderWaitsForIt() {
    const store = newStore();
    const parent = await store.createFolder({ title: "Top" });
    const child = await store.createFolder({ title: "Child", parent_id: parent.entity_id });
    assert.deepEqual(child.depends_on, [parent.op_id]);
    const tree = globalThis.prksEffectiveFolders([], await store.listOperations());
    assert.equal(tree.find(f => f.title === "Child").parent_id, parent.entity_id);
}

/* ---- fields ---- */

async function fieldEditsCoalesceAndCancel() {
    const store = newStore();
    const base = baseAt({ title: 4 }, { title: "Drafts" });
    await store.saveFolderFields("F-1", { title: "Working" }, base);
    await store.saveFolderFields("F-1", { title: "In progress" }, base);
    let rows = await rowsFor(store, "SET_FOLDER_FIELD");
    assert.equal(rows.length, 1, "a never-sent row is rewritten, not stacked");
    assert.equal(rows[0].payload.value, "In progress");
    assert.equal(rows[0].base_revision, 4);

    // Renamed back to what the server already holds: not two changes, none.
    await store.saveFolderFields("F-1", { title: "Drafts" }, base);
    assert.equal((await rowsFor(store, "SET_FOLDER_FIELD")).length, 0);

    // Two fields are two decisions, each with its own base.
    await store.saveFolderFields("F-1",
        { title: "Working", description: "Now described" },
        baseAt({ title: 4, description: 9 }, { title: "Drafts" }));
    rows = await rowsFor(store, "SET_FOLDER_FIELD");
    assert.equal(rows.length, 2);
    const byField = new Map(rows.map(r => [r.payload.field, r]));
    assert.equal(byField.get("description").base_revision, 9);

    /* A busy field blocks its own scope and nothing else. */
    await store.updateOperationSyncState(byField.get("title").op_id, { status: "syncing" });
    await assert.rejects(
        () => store.saveFolderFields("F-1", { title: "Again" }, baseAt({ title: 4 })),
        e => e.prksLocalStoreCode === "scope_busy");
    await store.saveFolderFields("F-1", { description: "Changed" },
        baseAt({ description: 9 }));
    assert.equal((await rowsFor(store, "SET_FOLDER_FIELD")).length, 2);
}

async function movingIsAFieldEditThatWaitsForALocalParent() {
    const store = newStore();
    const parent = await store.createFolder({ title: "Top" });
    const [move] = await store.saveFolderFields("F-existing",
        { parent_id: parent.entity_id }, baseAt({ parent_id: 2 }));
    assert.equal(move.payload.field, "parent_id",
        "the hierarchy is a parent pointer on one row, so a move is one value");
    assert.deepEqual(move.depends_on, [parent.op_id]);
}

/* ---- which folder a Work is in ---- */

async function aWorksFolderIsAScalarThatCancels() {
    const store = newStore();
    const filed = await store.setWorkFolder("W-1", "F-2", { folder_id: "F-1", revision: 3 });
    assert.equal(filed.operation, "SET_WORK_FOLDER");
    assert.equal(filed.base_revision, 3);
    assert.equal(filed.payload.folder_id, "F-2");

    // Filing it somewhere else again rewrites the one intent.
    await store.setWorkFolder("W-1", "F-3", { folder_id: "F-1", revision: 3 });
    let rows = await rowsFor(store, "SET_WORK_FOLDER");
    assert.equal(rows.length, 1, "a Work is in at most one folder, so one intent");
    assert.equal(rows[0].payload.folder_id, "F-3");

    // Filed back where it started: not two changes, none.
    await store.setWorkFolder("W-1", "F-1", { folder_id: "F-1", revision: 3 });
    assert.equal((await rowsFor(store, "SET_WORK_FOLDER")).length, 0);

    /* Clearing is the SAME operation with an empty value, not a second family. */
    const cleared = await store.setWorkFolder("W-1", "", { folder_id: "F-1", revision: 3 });
    assert.equal(cleared.payload.folder_id, "");
}

async function aPendingFilingShowsWhereTheFileWillBe() {
    const store = newStore();
    await store.setWorkFolder("W-1", "F-2", { folder_id: "F-1", revision: 3 });
    const ops = await store.listOperations();
    const catalogue = [{ id: "F-1", title: "Old" }, { id: "F-2", title: "New" }];
    const work = { id: "W-1", folder_id: "F-1", folder_title: "Old" };
    const effective = globalThis.prksEffectiveWorkFolder(work, ops, catalogue);
    assert.equal(effective.folder_id, "F-2");
    assert.equal(effective.folder_title, "New",
        "named from the catalogue, so a folder created here is not a blank label");
    assert.equal(work.folder_title, "Old",
        "the acknowledged record is never written through");
    const rows = globalThis.prksEffectiveWorkFolderRows(
        [work, { id: "W-2", folder_id: "F-1", folder_title: "Old" }], ops, catalogue);
    assert.equal(rows[0].folder_title, "New");
    assert.equal(rows[1].folder_title, "Old", "and an untouched file stays put");
}

/* ---- destruction ---- */

async function aPendingFilingShowsOnTheFolderPage() {
    const store = newStore();
    await store.setWorkFolder("W-1", "F-2", { folder_id: "F-1", revision: 3 });
    const ops = await store.listOperations();
    const catalogue = [{ id: "W-1", title: "Moved in" }];

    const destination = globalThis.prksEffectiveFolderDetail(
        { id: "F-2", title: "New", works: [] }, ops, catalogue);
    assert.deepEqual(destination.works.map(w => w.id), ["W-1"],
        "the folder it was moved INTO shows it straight away");
    assert.equal(destination.work_count, 1);

    const source = globalThis.prksEffectiveFolderDetail(
        { id: "F-1", title: "Old", works: [{ id: "W-1", title: "Moved in" }] }, ops, catalogue);
    assert.deepEqual(source.works, [], "and the one it left stops claiming it");

    /* A file whose row this device does not hold is NOT invented: the
     * operation names an id, and the page renders a card. */
    const unnamed = globalThis.prksEffectiveFolderDetail(
        { id: "F-2", title: "New", works: [] }, ops, []);
    assert.deepEqual(unnamed.works, []);
}

async function deletingCancelsWhatWasNeverSent() {
    const store = newStore();
    await store.saveFolderFields("F-1", { title: "Renamed" }, baseAt({ title: 1 }));
    await store.setWorkFolder("W-1", "F-1", { folder_id: "", revision: 0 });
    const removal = await store.deleteFolder("F-1");
    assert.equal(removal.base_revision, null,
        "destruction addresses an identity, not a value");
    assert.equal((await store.listOperations()).length, 1,
        "filing into a folder about to be deleted is work the delete undoes -- " +
        "and would make the deletion fail, because a folder holding files is protected");

    await assert.rejects(
        () => store.saveFolderFields("F-1", { title: "Too late" }, baseAt({ title: 1 })),
        e => e.prksLocalStoreCode === "entity_deleted");
    await assert.rejects(
        () => store.setWorkFolder("W-2", "F-1", { folder_id: "", revision: 0 }),
        e => e.prksLocalStoreCode === "entity_deleted");
    assert.equal((await store.deleteFolder("F-1")).op_id, removal.op_id);
}

async function deletingAFolderCreatedHereFoldsItAway() {
    const store = newStore();
    const created = await store.createFolder({ title: "Mistake" });
    await store.saveFolderFields(created.entity_id, { description: "Typo" }, baseAt());
    assert.equal(await store.deleteFolder(created.entity_id), null);
    assert.deepEqual(await store.listOperations(), [],
        "nothing about this folder ever reaches the server");
}

async function aSentFilingIsWaitedForRatherThanRewritten() {
    const store = newStore();
    const filed = await store.setWorkFolder("W-1", "F-1", { folder_id: "", revision: 0 });
    await store.updateOperationSyncState(filed.op_id, { status: "syncing" });
    const removal = await store.deleteFolder("F-1");
    assert.deepEqual(removal.depends_on, [filed.op_id],
        "an envelope that may be on the wire stays immutable");
}

async function aPendingDeletionIsATombstone() {
    const store = newStore();
    await store.deleteFolder("F-2");
    const ops = await store.listOperations();
    const catalogue = [
        { id: "F-1", title: "Kept", parent_id: null },
        { id: "F-2", title: "Doomed", parent_id: null },
    ];
    assert.deepEqual(globalThis.prksEffectiveFolders(catalogue, ops).map(f => f.id), ["F-1"]);
    assert.equal(catalogue.length, 2,
        "nothing is destroyed: a refusal restores it by doing nothing");
    /* And a file that was in it stops claiming to be. */
    const work = globalThis.prksEffectiveWorkFolder(
        { id: "W-1", folder_id: "F-2", folder_title: "Doomed" }, ops, catalogue);
    assert.equal(work.folder_id, null);
    assert.equal(work.folder_title, "");
}

/* ---- bases ---- */

async function theBaseIsAcknowledgedAndNeverGuessed() {
    const store = newStore();
    const created = await store.createFolder({ title: "Drafts", description: "WIP" });
    const ops = await store.listOperations();
    const base = await globalThis.prksAcknowledgedFolderBase(created.entity_id, ops);
    assert.equal(base.title.value, "Drafts", "the construction payload IS the base");
    assert.equal(base.title.revision, 0, "at revision 0 -- known, not assumed");

    const draft = { title: "Drafts", description: "WIP", private_notes: "", parent_id: "" };
    assert.deepEqual(
        globalThis.prksDirtyFolderFields(created.entity_id, draft, base, ops), {},
        "reopening the form and saving changes nothing");
    assert.deepEqual(
        globalThis.prksDirtyFolderFields(created.entity_id,
            Object.assign({}, draft, { title: "Working" }), base, ops),
        { title: "Working" }, "only what was typed");

    globalThis.prksOfflineReadEntity = async () => ({ value: null, source: "unavailable" });
    globalThis.prksOfflineInvalidateEntity = async () => true;
    assert.equal(await globalThis.prksAcknowledgedFolderBase("F-UNKNOWN", ops), null);
}

async function main() {
    await aFolderIsAValidDestinationImmediately();
    await anEmptyTitleBecomesThePlaceholder();
    await aFolderInsideALocalFolderWaitsForIt();
    await fieldEditsCoalesceAndCancel();
    await movingIsAFieldEditThatWaitsForALocalParent();
    await aWorksFolderIsAScalarThatCancels();
    await aPendingFilingShowsWhereTheFileWillBe();
    await aPendingFilingShowsOnTheFolderPage();
    await deletingCancelsWhatWasNeverSent();
    await deletingAFolderCreatedHereFoldsItAway();
    await aSentFilingIsWaitedForRatherThanRewritten();
    await aPendingDeletionIsATombstone();
    await theBaseIsAcknowledgedAndNeverGuessed();
    console.log("All " + checks + " folder checks passed");
}

main()
    .then(() => process.exit(0))
    .catch(error => { console.error(error); process.exit(1); });
