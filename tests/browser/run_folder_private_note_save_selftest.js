"use strict";
/* Folder Reminders: the save contract browser-local recovery needs (#534).
 *
 * Folder private notes are the `private_notes` FIELD of a Folder, saved as one
 * SET_FOLDER_FIELD row against that field's revision. Recovery needs every save
 * to name the exact row holding its text, to tell "nothing needed" from "in
 * flight" from "needs resolution" from "failed", and an acknowledgement it can
 * match to one row and one text. These checks run the real local store and the
 * real sync runtime over a fake IndexedDB.
 */
const strict = require("assert/strict");
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); },
    { get: (_t, k) => (...args) => { checks += 1; return strict[k](...args); } });
const { createFakeIndexedDBFactory } = require("./lib/fake_indexeddb.js");
const { createPrksLocalStore } = require("../../frontend/js/local-store.js");
require("../../frontend/js/sync-runtime.js");
require("../../frontend/js/folder-state.js");

const FOLDER = "F-00000000000000000000000000000001";
let sequence = 0;
const uuid = () => "00000000-0000-4000-8000-" + (++sequence).toString(16).padStart(12, "0");
const tick = () => new Promise(resolve => setTimeout(resolve, 1));
async function settle() { for (let i = 0; i < 16; i++) await tick(); }
const newStore = () => createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });

const save = (text, observed) =>
    globalThis.prksSaveFolderPrivateNoteDurably(FOLDER, text, observed);

/* The page's `prksSync`: the store, and a `changed` the save must call. */
function install(store) {
    const sync = { store, changedCalls: 0, changed() { sync.changedCalls += 1; } };
    globalThis.prksSync = sync;
    return sync;
}

const rows = async (store) => store.listOperations();
const noteRows = async (store) =>
    globalThis.prksFolderPrivateNoteOperations(await rows(store), FOLDER);

function fieldAck(op, revision) {
    return { code: "ACKNOWLEDGED", folder_id: op.entity_id, field: op.payload.field,
        changed: true, server_revision: revision, value_omitted: true };
}

/* ---- the queued row is named exactly ---- */

async function aSaveNamesTheExactRowHoldingItsText() {
    const store = newStore();
    const sync = install(store);
    const first = await save("Buy paper", { value: "", revision: 7 });
    assert.equal(first.code, "queued");
    assert.equal(typeof first.opId, "string");
    assert.equal(first.text, "Buy paper");
    assert.equal(first.baseRevision, 7, "measured against the private_notes field revision");
    assert.equal(sync.changedCalls, 1, "a queued row wakes sync");
    const [row] = await noteRows(store);
    assert.equal(row.op_id, first.opId, "the opId is the row in the queue");
    assert.deepEqual(row.payload, { field: "private_notes", value: "Buy paper" },
        "and its payload is exactly the saved text");
    assert.equal(row.base_revision, 7);

    const again = await save("Buy paper", { value: "", revision: 7 });
    assert.equal(again.code, "queued");
    assert.equal(again.opId, first.opId, "the never-sent row already holding the text is named");

    const edited = await save("Buy paper and ink", { value: "", revision: 7 });
    assert.equal(edited.code, "queued");
    assert.notEqual(edited.opId, first.opId, "new text is a new row");
    const now = await noteRows(store);
    assert.deepEqual(now.map(r => r.op_id), [edited.opId], "the never-sent row it replaced is gone");
    assert.equal(now[0].payload.value, "Buy paper and ink");
}

async function returningToTheBaseLeavesNothingQueued() {
    const store = newStore();
    install(store);
    const base = { value: "Original", revision: 3 };
    const typed = await save("Changed", base);
    assert.equal(typed.code, "queued");
    const back = await save("Original", base);
    assert.deepEqual(back, { code: "unchanged", opId: null, text: "Original", baseRevision: 3 },
        "A -> B -> A: equal to the supplied base, so no row is needed");
    assert.equal((await noteRows(store)).length, 0, "and the withdrawn row is gone");
    const untouched = await save("Original", base);
    assert.equal(untouched.code, "unchanged", "an unchanged body never queues a row");
    assert.equal((await rows(store)).length, 0);
}

/* ---- per-field revisions, and the other fields ---- */

async function otherFieldsAreNeitherSentNorBlocking() {
    const store = newStore();
    install(store);
    /* A pending rename, and a description already in flight. */
    const fields = globalThis.PRKS_FOLDER_FIELDS;
    const base = {};
    fields.forEach(name => { base[name] = { value: "", revision: 0 }; });
    base.title = { value: "Drafts", revision: 11 };
    base.description = { value: "", revision: 2 };
    const [title] = await store.saveFolderFields(FOLDER, { title: "Drafts 2" }, base);
    const [description] = await store.saveFolderFields(FOLDER, { description: "WIP" }, base);
    await store.claimOperation(description.op_id);

    const saved = await save("Call the printer", { value: "", revision: 5 });
    assert.equal(saved.code, "queued", "another field in flight does not make Reminders busy");
    const all = await rows(store);
    const note = all.find(r => r.op_id === saved.opId);
    assert.equal(note.base_revision, 5, "Reminders carry their own field revision, not the title's");
    assert.deepEqual(note.payload, { field: "private_notes", value: "Call the printer" });
    const kept = all.filter(r => r.op_id !== saved.opId).map(r => [r.op_id, r.payload.field, r.payload.value]);
    const byField = (a, b) => a[1].localeCompare(b[1]);
    assert.deepEqual(kept.sort(byField), [
        [description.op_id, "description", "WIP"],
        [title.op_id, "title", "Drafts 2"],
    ], "the title and description rows are untouched");
    assert.deepEqual(globalThis.prksFolderPrivateNoteOperations(all, FOLDER).map(r => r.op_id),
        [saved.opId], "only the Reminders row is a Reminders row");
}

async function theOrdinaryFolderSaveIsUnchanged() {
    const store = newStore();
    install(store);
    const fields = globalThis.PRKS_FOLDER_FIELDS;
    const base = {};
    fields.forEach(name => { base[name] = { value: "", revision: 1 }; });
    const [row] = await store.saveFolderFields(FOLDER, { private_notes: "x" }, base);
    await store.claimOperation(row.op_id);
    /* The canonical Folder form still sees the same refusal it always did. */
    await assert.rejects(store.saveFolderFields(FOLDER, { private_notes: "y" }, base),
        error => error.prksLocalStoreCode === "scope_busy" &&
            error.message === "This field is syncing or needs resolution.");
}

/* ---- busy, conflicted, failed, unknown ---- */

async function aRowInFlightIsBusyAndNamed() {
    const store = newStore();
    install(store);
    const first = await save("One", { value: "", revision: 0 });
    await store.claimOperation(first.opId);
    const busy = await save("One and two", { value: "", revision: 0 });
    assert.deepEqual(busy, { code: "scope_busy", opId: first.opId },
        "a sent row is never rewritten, and the save says which row holds the field");
    const [still] = await noteRows(store);
    assert.equal(still.payload.value, "One", "nothing was written");
}

async function aRowNeedingResolutionIsAConflict() {
    const store = newStore();
    install(store);
    const first = await save("One", { value: "", revision: 0 });
    await store.updateOperationSyncState(first.opId, {
        status: "conflict", server_result: { code: "REVISION_CONFLICT", current_revision: 4 },
    });
    const refused = await save("Two", { value: "", revision: 0 });
    assert.deepEqual(refused, { code: "conflict", opId: first.opId });
}

async function aStoreFailureIsACodeNotAThrow() {
    install({
        async saveFolderFields() {
            throw Object.assign(new Error("disk full"), { prksLocalStoreCode: "write_failed" });
        },
    });
    const failed = await save("Anything", { value: "", revision: 0 });
    assert.deepEqual(failed, { code: "failed", opId: null, storeCode: "write_failed", error: "disk full" });

    const store = newStore();
    install(store);
    await store.deleteFolder(FOLDER);
    const deleted = await save("Late", { value: "", revision: 0 });
    assert.equal(deleted.code, "failed", "a folder being deleted refuses the edit");
    assert.equal(deleted.storeCode, "entity_deleted");
}

async function noBaseNoSave() {
    const store = newStore();
    const sync = install(store);
    for (const observed of [null, undefined, {}, { value: "x" }, { value: "x", revision: -1 },
        { value: 3, revision: 1 }, { value: "x", revision: 1.5 }]) {
        const result = await save("Text", observed);
        assert.deepEqual(result, { code: "unknown_base", opId: null });
    }
    assert.equal((await rows(store)).length, 0, "a guessed base would overwrite another device");
    assert.equal(sync.changedCalls, 0);
    assert.deepEqual(await globalThis.prksSaveFolderPrivateNoteDurably("", "x", { value: "", revision: 0 }),
        { code: "invalid", opId: null });
    assert.deepEqual(await save(5, { value: "", revision: 0 }), { code: "invalid", opId: null });
    const tooLong = await save("é".repeat(2001), { value: "", revision: 0 });
    assert.deepEqual(tooLong, { code: "too-long", opId: null }, "4000 UTF-8 bytes, as the server counts");
    assert.equal((await save("e".repeat(4000) + "   ", { value: "", revision: 0 })).code, "queued",
        "surrounding whitespace is not stored, so it is not counted");
    globalThis.prksSync = undefined;
    assert.deepEqual(await save("x", { value: "", revision: 0 }), { code: "unavailable", opId: null });
}

async function anUnprovenRowIsNotPassedOffAsTheText() {
    install({ async saveFolderFields() { return [{ op_id: "op-x", operation: "SET_FOLDER_FIELD",
        entity_type: "folder", entity_id: FOLDER, payload: { field: "private_notes", value: "other" } }]; } });
    assert.deepEqual(await save("mine", { value: "", revision: 0 }), { code: "unproven", opId: null });
    install({ async saveFolderFields() { return []; } });
    assert.deepEqual(await save("mine", { value: "", revision: 0 }), { code: "unproven", opId: null },
        "no row, and the text is not the base: nothing proves what will acknowledge it");
}

/* ---- acknowledgements ---- */

async function anOlderAckNeverCoversANewerEdit() {
    const store = newStore();
    install(store);
    let release = null;
    const held = new Promise(resolve => { release = resolve; });
    const runtime = globalThis.createPrksSyncRuntime({
        store, online: () => true,
        request: async (_path, options) => {
            const body = JSON.parse(options.body);
            await held;
            return { ok: true, status: 200, json: async () => fieldAck(body, 8) };
        },
        handlers: { SET_FOLDER_FIELD: Object.assign({}, globalThis.prksFolderFieldSyncHandler,
            { reconcile: async () => true }) },
    });
    const events = [];
    runtime.subscribe(event => { if (event && event.acknowledged) events.push(event); });

    const older = await save("Generation one", { value: "", revision: 7 });
    const sending = runtime.wake();
    await settle();
    const newer = await save("Generation two", { value: "", revision: 7 });
    assert.deepEqual(newer, { code: "scope_busy", opId: older.opId },
        "the newer edit cannot ride on the row already sent");

    release();
    await sending;
    await settle();
    runtime.stop();
    assert.equal(events.length, 1);
    const ack = globalThis.prksFolderPrivateNoteAck(events[0]);
    assert.deepEqual(ack, { folderId: FOLDER, opId: older.opId, text: "Generation one",
        stored: "Generation one", revision: 8, changed: true });
    assert.notEqual(ack.text, "Generation two",
        "the acknowledgement names the older row and text: it says nothing about the newer edit");

    const retried = await save("Generation two", { value: ack.stored, revision: ack.revision });
    assert.equal(retried.code, "queued");
    assert.notEqual(retried.opId, older.opId);
    assert.equal(retried.baseRevision, 8, "measured against the acknowledged revision");
}

function theAckIsOnlyAReminderAck() {
    const op = { op_id: "op-1", operation: "SET_FOLDER_FIELD", entity_type: "folder", entity_id: FOLDER,
        payload: { field: "private_notes", value: "  Keep the receipt \n" } };
    const event = { acknowledged: fieldAck(op, 3), operation: op.operation, op };
    assert.deepEqual(globalThis.prksFolderPrivateNoteAck(event), {
        folderId: FOLDER, opId: "op-1", text: "  Keep the receipt \n", stored: "Keep the receipt",
        revision: 3, changed: true,
    }, "the exact payload for matching a draft, and what the server stores");
    const title = Object.assign({}, op, { payload: { field: "title", value: "T" } });
    assert.equal(globalThis.prksFolderPrivateNoteAck({ acknowledged: fieldAck(title, 3), op: title }), null);
    const work = Object.assign({}, op, { operation: "SET_WORK_PRIVATE_NOTE", entity_type: "work",
        payload: { text: "x" } });
    assert.equal(globalThis.prksFolderPrivateNoteAck({ acknowledged: { code: "ACKNOWLEDGED",
        server_revision: 3 }, op: work }), null, "a Work note revision is not a Folder field revision");
    assert.equal(globalThis.prksFolderPrivateNoteAck({ op }), null, "a change event is not an ack");
    assert.equal(globalThis.prksFolderPrivateNoteAck({ acknowledged: Object.assign(fieldAck(op, 3),
        { folder_id: "F-OTHER" }), op }), null);
    assert.equal(globalThis.prksFolderPrivateNoteAck({ acknowledged: Object.assign(fieldAck(op, 3),
        { server_revision: -1 }), op }), null);
    assert.equal(globalThis.prksFolderPrivateNoteAck({ acknowledged: Object.assign(fieldAck(op, 3),
        { code: "REVISION_CONFLICT" }), op }), null);
    assert.equal(globalThis.prksCanonicalFolderFieldValue("title", "  "), "Untitled Folder");
    assert.equal(globalThis.prksCanonicalFolderFieldValue("private_notes", null), "");
}

function theQueueReaderIsOldestFirstAndUnsettledOnly() {
    const row = (id, sequenceNo, field, status, folder) => ({ op_id: id, sequence: sequenceNo,
        operation: "SET_FOLDER_FIELD", entity_type: "folder", entity_id: folder || FOLDER,
        status: status || "pending", payload: { field, value: id } });
    const ops = [row("b", 2, "private_notes"), row("a", 1, "private_notes", "syncing"),
        row("c", 3, "private_notes", "acknowledged"), row("d", 4, "title"),
        row("e", 5, "private_notes", "pending", "F-OTHER"), row("f", 6, "private_notes", "conflict")];
    assert.deepEqual(globalThis.prksFolderPrivateNoteOperations(ops, FOLDER).map(r => r.op_id),
        ["a", "b", "f"]);
}

async function main() {
    await aSaveNamesTheExactRowHoldingItsText();
    await returningToTheBaseLeavesNothingQueued();
    await otherFieldsAreNeitherSentNorBlocking();
    await theOrdinaryFolderSaveIsUnchanged();
    await aRowInFlightIsBusyAndNamed();
    await aRowNeedingResolutionIsAConflict();
    await aStoreFailureIsACodeNotAThrow();
    await noBaseNoSave();
    await anUnprovenRowIsNotPassedOffAsTheText();
    await anOlderAckNeverCoversANewerEdit();
    theAckIsOnlyAReminderAck();
    theQueueReaderIsOldestFirstAndUnsettledOnly();
    console.log("All " + checks + " folder private-note save checks passed");
}

main()
    .then(() => process.exit(0))
    .catch(error => { console.error(error); process.exit(1); });
