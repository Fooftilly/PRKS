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

async function main() {
    await constructionAndDependencies();
    await scalarCoalescingAndIndependence();
    await orderedAggregatesCoalesceAndCancel();
    await deletionAndImmutability();
    await validation();
    await failedRootBlocksDescendants();
    console.log("All " + checks + " argument checks passed");
}

main().then(() => process.exit(0)).catch(error => { console.error(error); process.exit(1); });
