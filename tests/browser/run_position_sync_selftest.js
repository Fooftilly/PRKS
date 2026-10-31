"use strict";
/* Positions: three shapes, and why this domain stays small.
 *
 * `name` and `description` are INDEPENDENT fields, so the load-bearing checks
 * here are the ones that would fail if they were ever joined into an
 * aggregate: each coalesces and cancels on its own, and one busy field never
 * blocks the other.
 */
const strict = require("assert/strict");
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); },
    { get: (_t, k) => (...args) => { checks += 1; return strict[k](...args); } });
const { createFakeIndexedDBFactory } = require("./lib/fake_indexeddb.js");
const { createPrksLocalStore } = require("../../frontend/js/local-store.js");
require("../../frontend/js/sync-runtime.js");
require("../../frontend/js/position-state.js");


/* The removed Chromium cases invoked api.js functions, not store methods.
 * Evaluate those exact production wrappers so a broken dirty/base/writer
 * connection fails the fast test even if local-store coalescing still works.
 * This follows run_concept_sync_selftest.js; no reconstructed wrapper logic.
 */
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
    const api = fs.readFileSync(path.join(__dirname, "../../frontend/js/api.js"), "utf8");
    loadTopLevelFunction(app, "prksDurableOperationsOrNone");
    for (const name of ["prksPositionSaveMessage", "prksPositionBaseUnavailable",
                        "createPosition", "updatePosition", "deletePosition"]) {
        loadTopLevelFunction(api, name);
    }
}

let sequence = 0;
const uuid = () => "00000000-0000-4000-8000-" + (++sequence).toString(16).padStart(12, "0");
const tick = () => new Promise(resolve => setTimeout(resolve, 1));
async function settle() { for (let i = 0; i < 16; i++) await tick(); }
const newStore = () => createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });

function baseAt(revisions, values) {
    const base = {};
    globalThis.PRKS_POSITION_FIELDS.forEach(function (name) {
        base[name] = {
            value: (values && values[name]) || "",
            revision: (revisions && revisions[name]) || 0,
        };
    });
    return base;
}

const rowsFor = async (store, operation) =>
    (await store.listOperations()).filter(r => r.operation === operation);

function positionAck(op) {
    return { code: "ACKNOWLEDGED", position_id: op.entity_id, changed: true,
        position: { id: op.entity_id, name: op.payload.name,
            description: op.payload.description } };
}
function fieldAck(op, revision) {
    return { code: "ACKNOWLEDGED", position_id: op.entity_id, field: op.payload.field,
        changed: true, server_revision: revision, value_omitted: true };
}

function runtimeFor(store, respond) {
    const silent = handler => Object.assign({}, handler, { reconcile: async () => true });
    return globalThis.createPrksSyncRuntime({
        store, online: () => true, request: respond,
        handlers: {
            CREATE_POSITION: silent(globalThis.prksPositionCreateSyncHandler),
            SET_POSITION_FIELD: silent(globalThis.prksPositionFieldSyncHandler),
            DELETE_POSITION: silent(globalThis.prksPositionDeleteSyncHandler),
        },
    });
}

/* ---- construction ---- */

async function aPositionIsRealImmediately() {
    const store = newStore();
    const created = await store.createPosition({ name: "Realism is false", description: "A claim." });
    assert.match(created.entity_id, /^P-[0-9A-F]{32}$/,
        "a permanent distributed id, minted here -- never remapped later");
    assert.equal(created.base_revision, null, "construction is not mutation");

    const ops = await store.listOperations();
    const list = globalThis.prksEffectivePositions([], ops);
    assert.equal(list.length, 1);
    assert.equal(list[0].name, "Realism is false");

    /* An edit before it is sent waits for the creation. */
    const edited = await store.savePositionFields(created.entity_id,
        { description: "Refined." }, baseAt({}, { description: "A claim." }));
    assert.deepEqual(edited[0].depends_on, [created.op_id]);

    const sent = [];
    const runtime = runtimeFor(store, async (_path, options) => {
        const body = JSON.parse(options.body);
        sent.push(body.operation);
        return { ok: true, status: 200, json: async () =>
            (body.operation === "CREATE_POSITION" ? positionAck(body) : fieldAck(body, 1)) };
    });
    await runtime.wake();
    await settle();
    runtime.stop();
    assert.deepEqual(sent, ["CREATE_POSITION", "SET_POSITION_FIELD"],
        "the creation goes first, because the edit depends on it");
}

async function aPositionNeedsAName() {
    const store = newStore();
    await assert.rejects(() => store.createPosition({ name: "   " }),
        error => error.prksLocalStoreCode === "invalid_envelope");
    const id = "P-" + "A".repeat(32);
    await assert.rejects(
        () => store.savePositionFields(id, { name: "  " }, baseAt({}, { name: "Was" })),
        error => error.prksLocalStoreCode === "invalid_envelope",
        "and it cannot be emptied by an edit either");
}

/* ---- fields ---- */

async function eachFieldCoalescesAndCancelsOnItsOwn() {
    const store = newStore();
    const id = "P-" + "B".repeat(32);
    const base = baseAt({ name: 3 }, { name: "Realism is false" });

    await store.savePositionFields(id, { name: "Realism is true" }, base);
    assert.equal((await rowsFor(store, "SET_POSITION_FIELD")).length, 1);

    /* A -> B -> C is still ONE operation, now carrying C. */
    await store.savePositionFields(id, { name: "Realism is unclear" }, base);
    const after = await rowsFor(store, "SET_POSITION_FIELD");
    assert.equal(after.length, 1);
    assert.equal(after[0].payload.value, "Realism is unclear");
    assert.equal(after[0].base_revision, 3, "still measured against what was acknowledged");

    /* B -> A, never sent, is ZERO operations. */
    await store.savePositionFields(id, { name: "Realism is false" }, base);
    assert.equal((await rowsFor(store, "SET_POSITION_FIELD")).length, 0);
}

async function oneBusyFieldDoesNotBlockTheOther() {
    /* The property that makes these two INDEPENDENT rather than an aggregate.
     * If they shared a scope, a syncing description would refuse a rename. */
    const store = newStore();
    const id = "P-" + "C".repeat(32);
    const base = baseAt({}, {});
    const busy = await store.savePositionFields(id, { description: "Syncing." }, base);
    await store.updateOperationSyncState(busy[0].op_id, { status: "syncing" });

    await store.savePositionFields(id, { name: "Renamed anyway" }, base);
    assert.equal((await rowsFor(store, "SET_POSITION_FIELD")).length, 2,
        "one decision is one conflict unit");

    await assert.rejects(
        () => store.savePositionFields(id, { description: "Again" }, base),
        error => error.prksLocalStoreCode === "scope_busy",
        "but editing the field that is in flight is refused");
}

async function bothFieldsSaveInOneCall() {
    const store = newStore();
    const id = "P-" + "D".repeat(32);
    const written = await store.savePositionFields(id,
        { name: "Renamed", description: "Changed" }, baseAt({}, {}));
    assert.equal(written.length, 2, "one Save, two independent operations");
    const fields = (await rowsFor(store, "SET_POSITION_FIELD"))
        .map(r => r.payload.field).sort();
    assert.deepEqual(fields, ["description", "name"]);
}

async function aPendingRenameReachesEverySurfaceThatNamesIt() {
    const store = newStore();
    globalThis.prksSync = { store: store };
    const id = "P-" + "E".repeat(32);
    await store.savePositionFields(id, { name: "Renamed offline" },
        baseAt({}, { name: "Realism is false" }));
    const ops = await store.listOperations();

    const list = globalThis.prksEffectivePositions(
        [{ id: id, name: "Realism is false" }], ops);
    assert.equal(list[0].name, "Renamed offline");

    await globalThis.prksRefreshPendingPositionNames();
    /* The Position list and picker rows ... */
    assert.equal(globalThis.prksApplyPendingPositionNames(
        [{ id: id, name: "Realism is false" }])[0].name, "Renamed offline");
    /* ... and an Argument's target rows, which name the Position they aim at. */
    const targets = globalThis.prksApplyPendingPositionNamesToTargets([
        { type: "position", id: id, name: "Realism is false", verdict_id: "supports" },
        { type: "argument", id: id, name: "A same-id argument", verdict_id: "opposes" },
    ]);
    assert.equal(targets[0].name, "Renamed offline");
    assert.equal(targets[1].name, "A same-id argument",
        "an Argument target is a different id space and must not be renamed");
}

/* ---- deletion ---- */

async function deletingCancelsWhatWasNeverSent() {
    const store = newStore();
    const id = "P-" + "F".repeat(32);
    await store.savePositionFields(id, { name: "Doomed" }, baseAt({}, {}));
    const deletion = await store.deletePosition(id);
    assert.equal((await rowsFor(store, "SET_POSITION_FIELD")).length, 0);
    assert.equal(deletion.base_revision, null, "destruction addresses an identity");
}

async function aSentEditIsWaitedForRatherThanRewritten() {
    const store = newStore();
    const id = "P-" + "1".repeat(32);
    const edit = await store.savePositionFields(id, { name: "Renamed" }, baseAt({}, {}));
    await store.updateOperationSyncState(edit[0].op_id, { status: "syncing" });
    const deletion = await store.deletePosition(id);
    assert.equal((await rowsFor(store, "SET_POSITION_FIELD")).length, 1,
        "a row that may already be on the wire stays immutable");
    assert.deepEqual(deletion.depends_on, [edit[0].op_id]);
}

async function deletingAPositionCreatedHereFoldsItAway() {
    const store = newStore();
    const created = await store.createPosition({ name: "Typed by accident" });
    assert.equal(await store.deletePosition(created.entity_id), null);
    assert.deepEqual(await store.listOperations(), []);
}

async function aPendingDeletionIsATombstoneUntilItIsRefused() {
    const store = newStore();
    const id = "P-" + "2".repeat(32);
    const deletion = await store.deletePosition(id);
    const rows = [{ id: id, name: "Realism is false" }];

    let ops = await store.listOperations();
    assert.deepEqual(globalThis.prksEffectivePositions(rows, ops), [],
        "hidden while the answer is still owed");

    await assert.rejects(
        () => store.savePositionFields(id, { name: "Renamed" }, baseAt({}, {})),
        error => error.prksLocalStoreCode === "entity_deleted");

    /* The server refuses -- an Argument still targets it -- so it comes back. */
    await store.updateOperationSyncState(deletion.op_id,
        { status: "conflict", server_result: { code: "POSITION_IN_USE" } });
    ops = await store.listOperations();
    assert.deepEqual(globalThis.prksEffectivePositions(rows, ops).map(r => r.id), [id],
        "refused, so the Position is visible again");
}

/* ---- bases ---- */

async function theBaseIsAcknowledgedAndNeverGuessed() {
    const store = newStore();
    globalThis.prksSync = { store: store };
    const created = await store.createPosition({ name: "Realism is false", description: "A claim." });
    const ops = await store.listOperations();

    const base = await globalThis.prksAcknowledgedPositionBase(created.entity_id, ops);
    assert.equal(base.name.value, "Realism is false", "the construction payload IS the base");
    assert.equal(base.name.revision, 0, "at revision 0 -- known, not assumed");
    assert.deepEqual(
        globalThis.prksDirtyPositionFields(created.entity_id,
            { name: "Realism is false", description: "A claim." }, base, ops), {},
        "reopening the form and saving changes nothing");
    assert.deepEqual(
        globalThis.prksDirtyPositionFields(created.entity_id,
            { name: "Realism is false", description: "Refined." }, base, ops),
        { description: "Refined." }, "only what was typed");

    globalThis.prksOfflineReadEntity = async () => ({ value: null, source: "unavailable" });
    globalThis.prksOfflineInvalidateEntity = async () => true;
    assert.equal(await globalThis.prksAcknowledgedPositionBase("P-UNKNOWN", ops), null,
        "guessing a revision would silently overwrite another device");
}

function positionDetailUsesGuardedOfflineRead() {
    // Keep the explicit opt-in scoped to this route and use the existing
    // validators and invalidation domain; do not silently cache-first
    // regular reads, indexes, or the acknowledged field writer.
    const fs = require("node:fs");
    const path = require("node:path");
    const app = fs.readFileSync(
        path.join(__dirname, "../../frontend/js/app.js"), "utf8");
    const start = app.indexOf("case 'position-detail': {");
    const end = app.indexOf("case 'arguments': {", start);
    assert(start >= 0 && end > start, "Position detail route exists");
    const route = app.slice(start, end);
    assert.match(route, /prksOfflineDetailFetch\(/);
    assert.match(route, /domain:\s*PRKS_POSITIONS_DOMAIN/);
    assert.match(route, /preferCacheWhenOffline:\s*true/);
    assert.match(route, /prksIsPositionShape\(value, positionId\)/);
    assert.match(route, /prksPendingPositionDeletions/);
    assert.match(route, /prksPendingPositionCreates/);
    assert.match(route, /prksResolveOfflinePosition\(/);
    const indexStart = app.indexOf("case 'positions': {");
    assert(indexStart >= 0 && indexStart < start);
    assert.doesNotMatch(app.slice(indexStart, start),
        /preferCacheWhenOffline:\s*true/,
        "Position index must retain server-first and cache-warming behavior");
}


/* Contract transfer: these former E2Es called production updatePosition,
 * createPosition and deletePosition (not DOM controls). The real wrappers
 * run against the same fake-IndexedDB store the other fast tests use. */
async function positionApiCancellationAndCreateDeleteContract() {
    const store = newStore();
    globalThis.prksSync = { store };
    const existing = "P-" + "E".repeat(32);
    const state = globalThis.prksNewPositionState(existing);
    state.fields.name.revision = 3;
    globalThis.prksOfflineReadEntity = async (kind, id) => {
        if (id !== existing) return { value: null, source: "unavailable" };
        if (kind === "position-state") return { value: state, source: "cache" };
        if (kind === "position") return {
            value: { id: existing, name: "Original", description: "" }, source: "cache"
        };
        return { value: null, source: "unavailable" };
    };
    globalThis.prksOfflineInvalidateEntity = async () => true;

    await globalThis.updatePosition(existing, { name: "Temporary" });
    let rows = await rowsFor(store, "SET_POSITION_FIELD");
    assert.equal(rows.length, 1, "real API enqueues a Position rename");
    assert.equal(rows[0].payload.field, "name");
    assert.equal(rows[0].payload.value, "Temporary");
    assert.equal(rows[0].base_revision, 3);

    // Mutation negative-control: if the production wrapper stops forwarding
    // the second save, the same zero-pending assertion must catch it.
    const write = globalThis.prksSavePositionFieldsDurably;
    try {
        globalThis.prksSavePositionFieldsDurably = async () => null;
        await globalThis.updatePosition(existing, { name: "Original" });
        rows = await rowsFor(store, "SET_POSITION_FIELD");
        assert.throws(() => assert.deepEqual(rows, []), /AssertionError|Expected values/);
    } finally {
        globalThis.prksSavePositionFieldsDurably = write;
    }
    await globalThis.updatePosition(existing, { name: "Original" });
    assert.deepEqual(await rowsFor(store, "SET_POSITION_FIELD"), [],
        "production updatePosition A→B→A has no remaining durable intent");

    const created = await globalThis.createPosition({ name: "Never sent" });
    assert.match(created.id, /^P-[0-9A-F]{32}$/);
    assert.equal((await rowsFor(store, "CREATE_POSITION")).length, 1);
    assert.deepEqual(await globalThis.deletePosition(created.id), { status: "deleted" });
    assert.deepEqual(await store.listOperations(), [],
        "production create→delete folds without a request to the server");
    delete globalThis.prksSync;
}

async function main() {
    positionDetailUsesGuardedOfflineRead();
    await aPositionIsRealImmediately();
    await aPositionNeedsAName();
    await eachFieldCoalescesAndCancelsOnItsOwn();
    await oneBusyFieldDoesNotBlockTheOther();
    await bothFieldsSaveInOneCall();
    await aPendingRenameReachesEverySurfaceThatNamesIt();
    await deletingCancelsWhatWasNeverSent();
    await aSentEditIsWaitedForRatherThanRewritten();
    await deletingAPositionCreatedHereFoldsItAway();
    await aPendingDeletionIsATombstoneUntilItIsRefused();
    await theBaseIsAcknowledgedAndNeverGuessed();
    await positionApiCancellationAndCreateDeleteContract();
    console.log("All " + checks + " position checks passed");
}

main()
    .then(() => process.exit(0))
    .catch(error => { console.error(error); process.exit(1); });
