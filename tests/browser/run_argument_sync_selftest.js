"use strict";
const strict = require("assert/strict");
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); },
    { get: (_t, key) => (...args) => { checks += 1; return strict[key](...args); } });
const { createFakeIndexedDBFactory } = require("./lib/fake_indexeddb.js");
const { createPrksLocalStore } = require("../../frontend/js/local-store.js");
require("../../frontend/js/sync-runtime.js");
require("../../frontend/js/position-state.js");
require("../../frontend/js/argument-state.js");

let sequence = 0;
const uuid = () => "00000000-0000-4000-8000-" + (++sequence).toString(16).padStart(12, "0");
const newStore = () => createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
const tick = () => new Promise(resolve => setTimeout(resolve, 1));
async function settle() { for (let i = 0; i < 20; i += 1) await tick(); }
const operations = store => store.listOperations();
const rowsFor = async (store, family) => (await operations(store)).filter(op => op.operation === family);

function fieldBase(values, revisions) {
    const out = {};
    for (const field of globalThis.PRKS_ARGUMENT_FIELDS) {
        out[field] = { value: String((values && values[field]) || ""),
            revision: (revisions && revisions[field]) || 0 };
    }
    return out;
}
const sourcesBase = (sources, revision=0) => ({ sources: sources || [], revision });
const targetsBase = (targets, revision=0) => ({ targets: targets || [], revision });

async function constructionAndDependencies() {
    const store = newStore();
    const position = await store.createPosition({ name: "Realism" });
    const a = await store.createArgument({ name: "A", kind: "stance", main_text: "Body",
        sources: [{ work_id: "W-1", pages: "10-12" }],
        targets: [{ type: "position", id: position.entity_id, verdict_id: "holds" }] });
    assert.match(a.entity_id, /^A-[0-9A-F]{32}$/);
    assert.deepEqual(a.payload, { name: "A", kind: "stance", main_text: "Body",
        sources: [{ work_id: "W-1", pages: "10-12" }],
        targets: [{ type: "position", id: position.entity_id, verdict_id: "holds" }] },
        "construction carries every initial conflict unit atomically");
    assert.deepEqual(a.depends_on, [position.op_id], "pending Position dependency attaches");

    const b = await store.createArgument({ name: "B",
        targets: [{ type: "argument", id: a.entity_id, verdict_id: "opposes" }] });
    assert.deepEqual(b.depends_on, [a.op_id], "pending Argument dependency attaches");
    assert.deepEqual([a.depends_on[0], b.depends_on[0]], [position.op_id, a.op_id],
        "real Position -> Argument A -> Argument B chain");

    const edit = await store.saveArgumentFields(a.entity_id, { main_text: "Later" },
        fieldBase({ main_text: "Body" }));
    assert.deepEqual(edit[0].depends_on, [a.op_id], "later edit waits on construction");
}

async function scalarCoalescingAndIndependence() {
    const store = newStore();
    const id = "A-" + "1".repeat(32);
    const base = fieldBase({ name: "A", kind: "argument", main_text: "Text" }, { name: 4 });
    await store.saveArgumentFields(id, { name: "B" }, base);
    await store.saveArgumentFields(id, { name: "C" }, base);
    let rows = await rowsFor(store, "SET_ARGUMENT_FIELD");
    assert.equal(rows.length, 1);
    assert.equal(rows[0].payload.value, "C", "A -> B -> C replaces never-sent intent");
    assert.equal(rows[0].base_revision, 4);
    await store.saveArgumentFields(id, { name: "A" }, base);
    assert.equal((await rowsFor(store, "SET_ARGUMENT_FIELD")).length, 0,
        "A -> B -> A cancels never-sent scalar");

    const busy = await store.saveArgumentFields(id, { main_text: "Busy" }, base);
    await store.updateOperationSyncState(busy[0].op_id, { status: "syncing" });
    await store.saveArgumentFields(id, { name: "Independent" }, base);
    rows = await rowsFor(store, "SET_ARGUMENT_FIELD");
    assert.equal(rows.length, 2, "busy main_text does not block changed name");
    await assert.rejects(() => store.saveArgumentFields(id, { main_text: "Again" }, base),
        error => error.prksLocalStoreCode === "scope_busy");
}

async function orderedAggregatesCoalesceAndCancel() {
    const store = newStore();
    const id = "A-" + "2".repeat(32);
    const sa = [{ work_id: "W-1", pages: "1" }, { work_id: "W-2", pages: "2" }];
    const sb = [sa[1], sa[0]];
    await store.setArgumentSources(id, sb, sourcesBase(sa, 3));
    let row = (await rowsFor(store, "SET_ARGUMENT_SOURCES"))[0];
    assert.deepEqual(row.payload.sources, sb, "source order is value");
    await store.setArgumentSources(id, sa, sourcesBase(sa, 3));
    assert.equal((await rowsFor(store, "SET_ARGUMENT_SOURCES")).length, 0,
        "source A -> B -> A cancels");

    const ta = [
        { type: "position", id: "P-1", verdict_id: "supports" },
        { type: "argument", id: "A-X", verdict_id: "opposes" },
    ];
    const tb = [ta[1], ta[0]];
    await store.setArgumentTargets(id, tb, targetsBase(ta, 7));
    row = (await rowsFor(store, "SET_ARGUMENT_TARGETS"))[0];
    assert.deepEqual(row.payload.targets, tb, "mixed target order is one aggregate value");
    await store.setArgumentTargets(id, ta, targetsBase(ta, 7));
    assert.equal((await rowsFor(store, "SET_ARGUMENT_TARGETS")).length, 0,
        "target A -> B -> A cancels");

    assert(globalThis.prksDirtyArgumentSources(id, sb, sourcesBase(sa), []));
    assert(globalThis.prksDirtyArgumentTargets(id, tb, targetsBase(ta), []));
    assert(!globalThis.prksDirtyArgumentSources(id, sa, sourcesBase(sa), []));
    assert(!globalThis.prksDirtyArgumentTargets(id, ta, targetsBase(ta), []));
}

async function deletionAndImmutability() {
    const store = newStore();
    const created = await store.createArgument({ name: "Temporary" });
    await store.saveArgumentFields(created.entity_id, { name: "Still temporary" },
        fieldBase({ name: "Temporary" }));
    assert.equal(await store.deleteArgument(created.entity_id), null);
    assert.deepEqual(await operations(store), [], "unsent create + edits + delete fold away");

    const id = "A-" + "3".repeat(32);
    const edit = await store.saveArgumentFields(id, { name: "B" }, fieldBase({ name: "A" }));
    await store.updateOperationSyncState(edit[0].op_id, { status: "syncing" });
    await assert.rejects(() => store.saveArgumentFields(id, { name: "C" }, fieldBase({ name: "A" })),
        error => error.prksLocalStoreCode === "scope_busy",
        "sent envelope is immutable");
    const deletion = await store.deleteArgument(id);
    const canonical = [{ id, name: "A", kind: "argument", sources: [], targets: [] }];
    assert.deepEqual(globalThis.prksEffectiveArguments(canonical, await operations(store)), [],
        "pending delete tombstones");
    await store.updateOperationSyncState(deletion.op_id,
        { status: "conflict", server_result: { code: "ARGUMENT_TARGETED" } });
    assert.deepEqual(globalThis.prksEffectiveArguments(canonical, await operations(store)).map(x => x.id), [id],
        "conflicted delete restores visibility");
}

async function validation() {
    const store = newStore();
    const id = "A-" + "4".repeat(32);
    await assert.rejects(() => store.setArgumentTargets(id,
        [{ type: "argument", id, verdict_id: "opposes" }], targetsBase([])),
        error => error.prksLocalStoreCode === "invalid_envelope");
    await assert.rejects(() => store.createArgument({ name: "Duplicates", sources: [
        { work_id: "W-1", pages: "1" }, { work_id: "W-1", pages: "2" }] }),
        error => error.prksLocalStoreCode === "invalid_envelope");
    await assert.rejects(() => store.createArgument({ name: "Duplicates", targets: [
        { type: "position", id: "P-1", verdict_id: "supports" },
        { type: "position", id: "P-1", verdict_id: "opposes" }] }),
        error => error.prksLocalStoreCode === "invalid_envelope");
}

async function failedRootBlocksDescendants() {
    const store = newStore();
    const p = await store.createPosition({ name: "Root" });
    const a = await store.createArgument({ name: "A", targets: [
        { type: "position", id: p.entity_id, verdict_id: "supports" }] });
    const b = await store.createArgument({ name: "B", targets: [
        { type: "argument", id: a.entity_id, verdict_id: "opposes" }] });
    const silent = handler => Object.assign({}, handler, { reconcile: async () => true });
    const runtime = globalThis.createPrksSyncRuntime({ store, online: () => true,
        request: async (_path, options) => {
            const op = JSON.parse(options.body);
            if (op.operation === "CREATE_POSITION") {
                return { ok: false, status: 400, json: async () => ({ code: "INVALID_ENVELOPE" }) };
            }
            throw new Error("dependent operation reached transport");
        },
        handlers: {
            CREATE_POSITION: Object.assign({}, silent(globalThis.prksPositionCreateSyncHandler),
                { terminal: () => ({ discard: "INVALID_ENVELOPE" }) }),
            CREATE_ARGUMENT: silent(globalThis.prksArgumentCreateSyncHandler),
            SET_ARGUMENT_FIELD: silent(globalThis.prksArgumentFieldSyncHandler),
            SET_ARGUMENT_SOURCES: silent(globalThis.prksArgumentSourcesSyncHandler),
            SET_ARGUMENT_TARGETS: silent(globalThis.prksArgumentTargetsSyncHandler),
            DELETE_ARGUMENT: silent(globalThis.prksArgumentDeleteSyncHandler),
        } });
    await runtime.wake();
    await settle();
    runtime.stop();
    const byId = new Map((await operations(store)).map(op => [op.op_id, op]));
    assert.equal(byId.get(a.op_id).server_result.code, "DEPENDENCY_FAILED");
    assert.equal(byId.get(b.op_id).server_result.code, "DEPENDENCY_FAILED");
    assert.equal(byId.get(a.op_id).status, "conflict");
    assert.equal(byId.get(b.op_id).status, "conflict");
}

async function commitWritesOnlyDirtyUnits() {
    const store = newStore();
    globalThis.prksSync = { store: store };
    const created = await store.createArgument({
        name: "Base", kind: "argument", main_text: "Text",
        sources: [{ work_id: "W-1", pages: "1" }],
        targets: [{ type: "position", id: "P-1", verdict_id: "supports" }],
    });
    const id = created.entity_id;
    const calls = [];
    const origSave = store.saveArgumentFields.bind(store);
    const origSources = store.setArgumentSources.bind(store);
    const origTargets = store.setArgumentTargets.bind(store);
    store.saveArgumentFields = async function (argumentId, changes, base) {
        calls.push(["field", changes]);
        return origSave(argumentId, changes, base);
    };
    store.setArgumentSources = async function (argumentId, sources, observed) {
        calls.push(["sources", sources]);
        return origSources(argumentId, sources, observed);
    };
    store.setArgumentTargets = async function (argumentId, targets, observed) {
        calls.push(["targets", targets]);
        return origTargets(argumentId, targets, observed);
    };
    const shown = {
        name: "Base", kind: "argument", main_text: "Text",
        sources: [{ work_id: "W-1", work_title: "Work", pages: "1" }],
        targets: [{ type: "position", id: "P-1", name: "Position", verdict_id: "supports" }],
    };
    await globalThis.prksCommitArgumentEditorDraft(id, shown);
    assert.deepEqual(calls, [], "an unchanged draft writes nothing");

    await globalThis.prksCommitArgumentEditorDraft(id, Object.assign({}, shown, { name: "Renamed" }));
    assert.deepEqual(calls.map(row => row[0]), ["field"]);
    assert.deepEqual(calls[0][1], { name: "Renamed" });

    calls.length = 0;
    await globalThis.prksCommitArgumentEditorDraft(id, Object.assign({}, shown, {
        name: "Renamed", kind: "stance",
    }));
    assert.deepEqual(calls.map(row => row[0]), ["field"]);
    assert.deepEqual(calls[0][1], { kind: "stance" });

    calls.length = 0;
    await globalThis.prksCommitArgumentEditorDraft(id, Object.assign({}, shown, {
        name: "Renamed", kind: "stance", main_text: "Later",
    }));
    assert.deepEqual(calls[0][1], { main_text: "Later" });

    calls.length = 0;
    await globalThis.prksCommitArgumentEditorDraft(id, Object.assign({}, shown, {
        name: "Renamed", kind: "stance", main_text: "Later",
        targets: [
            { type: "position", id: "", verdict_id: "supports" },
            { type: "position", id: "P-2", verdict_id: "opposes", name: "Other" },
        ],
    }));
    assert.deepEqual(calls.map(row => row[0]), ["targets"]);
    assert.equal(calls[0][1].length, 1);
    assert.equal(calls[0][1][0].id, "P-2");

    calls.length = 0;
    await globalThis.prksCommitArgumentEditorDraft(id, Object.assign({}, shown, {
        name: "Renamed", kind: "stance", main_text: "Later",
        targets: [{ type: "position", id: "P-2", verdict_id: "opposes" }],
        sources: [{ work_id: "W-2", pages: "9", work_title: "Next" }],
    }));
    assert.deepEqual(calls.map(row => row[0]), ["sources"]);

    calls.length = 0;
    await globalThis.prksCommitArgumentEditorDraft(id, {
        name: "Both", kind: "argument", main_text: "Both text",
        sources: [{ work_id: "W-3", pages: "" }],
        targets: [{ type: "argument", id: "A-OTHER", verdict_id: "supports" }],
    });
    const kinds = calls.map(row => row[0]);
    assert.ok(kinds.indexOf("field") !== -1);
    assert.ok(kinds.indexOf("sources") !== -1);
    assert.ok(kinds.indexOf("targets") !== -1);

    store.saveArgumentFields = async function () { calls.push(["field-fail"]); throw new Error("field failed"); };
    calls.length = 0;
    let partial = null;
    try {
        await globalThis.prksCommitArgumentEditorDraft(id, {
            name: "Again", kind: "argument", main_text: "Both text",
            sources: [{ work_id: "W-4", pages: "1" }],
            targets: [{ type: "argument", id: "A-OTHER", verdict_id: "supports" }],
        });
    } catch (error) { partial = error; }
    assert.equal(partial && partial.message, "field failed");
    assert.ok(calls.some(row => row[0] === "sources"), "source aggregate still attempted");

    const reads = [];
    globalThis.prksOfflineReadEntity = async function (kind, entityId) {
        reads.push(kind + ":" + entityId);
        return { value: null, source: "unavailable", cachedAt: null };
    };
    reads.length = 0;
    await globalThis.prksPrepareArgumentEdit(id);
    assert.deepEqual(reads, [], "a locally created Argument does not read the server to enter edit");
    await globalThis.prksPrepareArgumentEdit("A-NOBASE");
    assert.ok(reads.some(row => row.indexOf("argument-state:A-NOBASE") === 0));

    let unavailable = null;
    try {
        await globalThis.prksCommitArgumentEditorDraft("A-NOBASE", shown);
    } catch (error) { unavailable = error; }
    assert.equal(unavailable && unavailable.prksArgumentUnavailable, true);
    assert.match(String(unavailable && unavailable.message), /does not know its revision/);
}

async function main() {
    await constructionAndDependencies();
    await scalarCoalescingAndIndependence();
    await orderedAggregatesCoalesceAndCancel();
    await deletionAndImmutability();
    await validation();
    await failedRootBlocksDescendants();
    await commitWritesOnlyDirtyUnits();
    console.log("All " + checks + " argument checks passed");
}

main().then(() => process.exit(0)).catch(error => { console.error(error); process.exit(1); });
