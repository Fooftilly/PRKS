"use strict";
/* Concepts: five shapes, and how they compose.
 *
 * What this covers and nothing else does cheaply: a Concept created here is a
 * valid parent immediately and everything put under it waits for it; the
 * definition is an ordinary scalar that coalesces and cancels; the NAME and
 * the ALIAS SET are one decision, because renaming writes an alias; the parent
 * set is a SET, so the same parents in a different order are the same choice;
 * and deleting reasons about every intent naming the Concept, including one
 * that gave it to another as a parent.
 */
const fs = require("fs");
const path = require("path");
const strict = require("assert/strict");
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); },
    { get: (_t, k) => (...args) => { checks += 1; return strict[k](...args); } });
const { createFakeIndexedDBFactory } = require("./lib/fake_indexeddb.js");
const { createPrksLocalStore } = require("../../frontend/js/local-store.js");
require("../../frontend/js/sync-runtime.js");
require("../../frontend/js/concept-state.js");

/* Production API wrappers live as browser <script> top-levels in api.js /
 * app.js. Evaluate the real functions here so cancel/same-set coverage cannot
 * drift from a hand-rebuilt composition of helpers. */
globalThis.window = globalThis;
function loadTopLevelFunction(source, name) {
    const markers = ["async function " + name + "(", "function " + name + "("];
    let at = -1;
    for (const marker of markers) {
        at = source.indexOf(marker);
        if (at !== -1) break;
    }
    if (at === -1) throw new Error("missing top-level function: " + name);
    const brace = source.indexOf("{", at);
    let depth = 0;
    for (let i = brace; i < source.length; i++) {
        const ch = source[i];
        if (ch === "{") depth += 1;
        else if (ch === "}") {
            depth -= 1;
            if (depth === 0) {
                (0, eval)(source.slice(at, i + 1));
                return;
            }
        }
    }
    throw new Error("unclosed top-level function: " + name);
}
{
    const apiSrc = fs.readFileSync(path.join(__dirname, "../../frontend/js/api.js"), "utf8");
    const appSrc = fs.readFileSync(path.join(__dirname, "../../frontend/js/app.js"), "utf8");
    loadTopLevelFunction(appSrc, "prksDurableOperationsOrNone");
    for (const name of [
        "prksConceptSaveMessage",
        "prksConceptBaseUnavailable",
        "updateConcept",
        "putConceptParents",
    ]) {
        loadTopLevelFunction(apiSrc, name);
    }
}

let sequence = 0;
const uuid = () => "00000000-0000-4000-8000-" + (++sequence).toString(16).padStart(12, "0");
const tick = () => new Promise(resolve => setTimeout(resolve, 1));
async function settle() { for (let i = 0; i < 16; i++) await tick(); }
const newStore = () => createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });

const fieldBase = (revision, value) => ({ description: { value: value || "", revision: revision || 0 } });
const identityBase = (name, aliases, revision) =>
    ({ name: name, aliases: aliases || [], revision: revision || 0 });
const parentsBase = (ids, revision) => ({ parent_ids: ids || [], revision: revision || 0 });

const rowsFor = async (store, operation) =>
    (await store.listOperations()).filter(r => r.operation === operation);

/** Mirror E2E prepare(): concept + concept-state already cached for wrappers. */
function installCachedConcept(id, options) {
    const description = options.description == null ? "" : String(options.description);
    const parentIds = Array.isArray(options.parent_ids) ? options.parent_ids.slice() : [];
    const descriptionRevision = options.descriptionRevision || 0;
    const parentsRevision = options.parentsRevision || 0;
    globalThis.prksOfflineReadEntity = async (kind, entityId) => {
        if (entityId !== id) return { value: null, source: "unavailable" };
        if (kind === "concept-state") {
            return {
                value: {
                    concept_id: id,
                    fields: { description: { revision: descriptionRevision } },
                    identity: { name: "Systems", aliases: [] },
                    identity_revision: 0,
                    parent_ids: parentIds.slice(),
                    parents_revision: parentsRevision,
                },
                source: "cache",
            };
        }
        if (kind === "concept") {
            return {
                value: { id: id, name: "Systems", description: description },
                source: "cache",
            };
        }
        return { value: null, source: "unavailable" };
    };
    globalThis.prksOfflineInvalidateEntity = async () => true;
}

function conceptAck(op) {
    return { code: "ACKNOWLEDGED", concept_id: op.entity_id, changed: true,
        concept: { id: op.entity_id, name: op.payload.name,
            description: op.payload.description } };
}
function parentsAck(op, revision) {
    return { code: "ACKNOWLEDGED", concept_id: op.entity_id, changed: true,
        server_revision: revision, parent_ids: op.payload.parent_ids };
}

function runtimeFor(store, respond) {
    const silent = handler => Object.assign({}, handler, { reconcile: async () => true });
    return globalThis.createPrksSyncRuntime({
        store, online: () => true, request: respond,
        handlers: {
            CREATE_CONCEPT: silent(globalThis.prksConceptCreateSyncHandler),
            SET_CONCEPT_FIELD: silent(globalThis.prksConceptFieldSyncHandler),
            SET_CONCEPT_IDENTITY: silent(globalThis.prksConceptIdentitySyncHandler),
            SET_CONCEPT_PARENTS: silent(globalThis.prksConceptParentsSyncHandler),
            DELETE_CONCEPT: silent(globalThis.prksConceptDeleteSyncHandler),
        },
    });
}

/* ---- construction ---- */

async function aConceptIsAValidParentImmediately() {
    const store = newStore();
    const created = await store.createConcept({ name: "Systems", description: "A definition." });
    assert.match(created.entity_id, /^C-[0-9A-F]{32}$/,
        "a permanent distributed id, minted here -- never remapped later");
    assert.equal(created.base_revision, null, "construction is not mutation");

    const ops = await store.listOperations();
    const vocabulary = globalThis.prksEffectiveConcepts([], ops);
    assert.equal(vocabulary.length, 1);
    assert.equal(vocabulary[0].name, "Systems");

    /* Putting another Concept under it waits for it, by the GENERIC mechanism:
     * the server cannot put anything under a parent it has never heard of. */
    const child = await store.createConcept({ name: "Emergence" });
    const reparent = await store.setConceptParents(child.entity_id, [created.entity_id],
        parentsBase([], 0));
    assert.deepEqual(reparent.depends_on.sort(), [child.op_id, created.op_id].sort(),
        "it waits for BOTH creations: its own and its parent's");

    const sent = [];
    const runtime = runtimeFor(store, async (_path, options) => {
        const body = JSON.parse(options.body);
        sent.push(body.operation);
        return { ok: true, status: 200, json: async () =>
            (body.operation === "CREATE_CONCEPT" ? conceptAck(body) : parentsAck(body, 1)) };
    });
    await runtime.wake();
    await settle();
    runtime.stop();
    assert.equal(sent[sent.length - 1], "SET_CONCEPT_PARENTS",
        "both Concepts exist on the server before either is put under the other");
}

async function aConceptNeedsAName() {
    const store = newStore();
    await assert.rejects(() => store.createConcept({ name: "   " }),
        error => error.prksLocalStoreCode === "invalid_envelope",
        "a Concept's name IS its identity -- there is no placeholder to invent");
}

/* ---- the definition ---- */

async function theDefinitionCoalescesAndCancels() {
    const store = newStore();
    const id = "C-" + "A".repeat(32);
    const base = fieldBase(3, "First");

    await store.saveConceptFields(id, { description: "Second" }, base);
    assert.equal((await rowsFor(store, "SET_CONCEPT_FIELD")).length, 1);

    /* A -> B -> C is still ONE operation, now carrying C. */
    await store.saveConceptFields(id, { description: "Third" }, base);
    const after = await rowsFor(store, "SET_CONCEPT_FIELD");
    assert.equal(after.length, 1);
    assert.equal(after[0].payload.value, "Third");
    assert.equal(after[0].base_revision, 3, "still measured against what was acknowledged");

    /* B -> A, never sent, is ZERO operations. */
    await store.saveConceptFields(id, { description: "First" }, base);
    assert.equal((await rowsFor(store, "SET_CONCEPT_FIELD")).length, 0);
}

/* The deleted E2E called production updateConcept(...). Store/helper arithmetic
 * alone would miss a wrapper regression (wrong observed base, skipping dirty
 * fields, broken durable wiring). Call the real api.js wrapper here. */
async function theDefinitionCancelGoesThroughApiWrapper() {
    const store = newStore();
    globalThis.prksSync = { store: store };
    const id = "C-" + "A".repeat(31) + "B";
    installCachedConcept(id, {
        description: "First",
        descriptionRevision: 3,
        parent_ids: [],
        parentsRevision: 0,
    });

    await globalThis.updateConcept(id, { description: "Temporary." });
    assert.equal((await rowsFor(store, "SET_CONCEPT_FIELD")).length, 1,
        "wrapper enqueues a pending definition edit");

    /* Helper arithmetic still matters: dirty must see the pending overlay. */
    const pending = await store.listOperations();
    const base = await globalThis.prksAcknowledgedConceptFields(id, pending);
    assert.deepEqual(
        globalThis.prksDirtyConceptFields(id, { description: "Temporary." }, base, pending),
        {},
        "an untouched pending value is not a new edit");
    assert.deepEqual(
        globalThis.prksDirtyConceptFields(id, { description: "First" }, base, pending),
        { description: "First" },
        "editing back to the acknowledged value IS a change the wrapper must forward");

    await globalThis.updateConcept(id, { description: "First" });
    assert.equal((await rowsFor(store, "SET_CONCEPT_FIELD")).length, 0,
        "production updateConcept A→B→A cancel leaves no intent");
}

async function theDefinitionIsNotTheIdentity() {
    const store = newStore();
    const id = "C-" + "B".repeat(32);
    await store.saveConceptFields(id, { description: "A definition." }, fieldBase(0));
    await store.setConceptIdentity(id, "Renamed", [], identityBase("Systems", [], 0));
    assert.equal((await rowsFor(store, "SET_CONCEPT_FIELD")).length, 1);
    assert.equal((await rowsFor(store, "SET_CONCEPT_IDENTITY")).length, 1,
        "two independent decisions, two conflict units");

    await assert.rejects(
        () => store.saveConceptFields(id, { name: "Nope" }, { name: { value: "", revision: 0 } }),
        error => error.prksLocalStoreCode === "unknown_field",
        "the name is not a field -- it belongs to the identity aggregate");
}

/* ---- identity ---- */

async function theIdentityIsOneDecision() {
    const store = newStore();
    const id = "C-" + "C".repeat(32);
    const base = identityBase("Emergence", [], 4);

    const renamed = await store.setConceptIdentity(id, "Emergent behaviour", [], base);
    assert.equal(renamed.base_revision, 4);
    assert.deepEqual(renamed.payload, { name: "Emergent behaviour", aliases: [] });

    /* An alias edit REPLACES the pending rename rather than queueing beside it:
     * both describe the same scope, and the server would add the old name to
     * the very set the second one is choosing. */
    await store.setConceptIdentity(id, "Emergence", ["Self-organization"], base);
    const rows = await rowsFor(store, "SET_CONCEPT_IDENTITY");
    assert.equal(rows.length, 1);
    assert.deepEqual(rows[0].payload.aliases, ["Self-organization"]);

    /* Back to exactly what was acknowledged: zero operations. */
    await store.setConceptIdentity(id, "Emergence", [], base);
    assert.equal((await rowsFor(store, "SET_CONCEPT_IDENTITY")).length, 0);
}

async function aPendingRenameReachesEverySurfaceThatNamesIt() {
    const store = newStore();
    globalThis.prksSync = { store: store };
    const id = "C-" + "D".repeat(32);
    await store.setConceptIdentity(id, "Emergent behaviour", ["Emergence"],
        identityBase("Emergence", [], 0));
    const ops = await store.listOperations();

    const vocabulary = globalThis.prksEffectiveConcepts(
        [{ id: id, name: "Emergence", aliases: [] }], ops);
    assert.equal(vocabulary[0].name, "Emergent behaviour");
    assert.deepEqual(vocabulary[0].aliases, ["Emergence"],
        "the alias set travels with the name, because it is the same decision");

    /* And the synchronous map every chip renders from. */
    await globalThis.prksRefreshPendingConceptNames();
    const chips = globalThis.prksApplyPendingConceptNames([{ id: id, name: "Emergence" }]);
    assert.equal(chips[0].name, "Emergent behaviour");
}

async function anIdentityEditSurvivesAReload() {
    const store = newStore();
    const id = "C-" + "E".repeat(32);
    await store.setConceptIdentity(id, "Renamed", ["Old"], identityBase("Old", [], 2));
    /* A second store over the SAME durable database is what a reload is. */
    const rows = await store.listOperations();
    assert.equal(rows.length, 1);
    assert.equal(rows[0].payload.name, "Renamed");
    assert.deepEqual(rows[0].payload.aliases, ["Old"]);
    assert.equal(rows[0].base_revision, 2, "and it still knows what it was measured against");
}

/* ---- hierarchy ---- */

async function theParentSetIsASet() {
    const store = newStore();
    const id = "C-" + "F".repeat(32);
    const base = parentsBase(["C-1", "C-2"], 5);

    /* The same parents in a different order is the SAME choice, so nothing is
     * queued -- two devices that picked these parents agreed. */
    assert.equal(await store.setConceptParents(id, ["C-2", "C-1"], base), null);

    const changed = await store.setConceptParents(id, ["C-3"], base);
    assert.deepEqual(changed.payload.parent_ids, ["C-3"]);
    assert.equal(changed.base_revision, 5);

    /* A second reparent replaces the first: one structural judgement. */
    await store.setConceptParents(id, ["C-4"], base);
    const rows = await rowsFor(store, "SET_CONCEPT_PARENTS");
    assert.equal(rows.length, 1);
    assert.deepEqual(rows[0].payload.parent_ids, ["C-4"]);

    /* Back to exactly the acknowledged set (any order) cancels: A → B → A
     * never sent is ZERO operations -- same cancel contract as the definition. */
    assert.equal(await store.setConceptParents(id, ["C-2", "C-1"], base), null);
    assert.equal((await rowsFor(store, "SET_CONCEPT_PARENTS")).length, 0);
}

/* Same as definition cancel: the deleted E2E called putConceptParents. Store
 * same-set arithmetic is necessary but not sufficient — exercise the real
 * api.js wrapper against a cached acknowledged parent set. */
async function theParentSetCancelGoesThroughApiWrapper() {
    const store = newStore();
    globalThis.prksSync = { store: store };
    const id = "C-" + "F".repeat(31) + "0";
    installCachedConcept(id, {
        description: "",
        descriptionRevision: 0,
        parent_ids: ["C-1", "C-2"],
        parentsRevision: 5,
    });

    await globalThis.putConceptParents(id, ["C-2", "C-1"]);
    assert.equal((await rowsFor(store, "SET_CONCEPT_PARENTS")).length, 0,
        "production putConceptParents treats order-insensitive same set as no intent");

    await globalThis.putConceptParents(id, ["C-3"]);
    assert.equal((await rowsFor(store, "SET_CONCEPT_PARENTS")).length, 1);

    await globalThis.putConceptParents(id, ["C-2", "C-1"]);
    assert.equal((await rowsFor(store, "SET_CONCEPT_PARENTS")).length, 0,
        "production putConceptParents A→B→A cancel leaves no intent");
}

async function aConceptCannotBeItsOwnParent() {
    const store = newStore();
    const id = "C-" + "1".repeat(32);
    await assert.rejects(() => store.setConceptParents(id, [id], parentsBase([], 0)),
        error => error.prksLocalStoreCode === "invalid_envelope",
        "the one cycle this device can see for itself");
}

async function aPendingHierarchyShowsAtBothEnds() {
    const store = newStore();
    const parent = "C-" + "2".repeat(32);
    const child = "C-" + "3".repeat(32);
    await store.setConceptParents(child, [parent], parentsBase([], 0));
    const ops = await store.listOperations();
    const catalogue = [{ id: parent, name: "Systems" }, { id: child, name: "Emergence" }];

    /* The child names its new parent ... */
    const childDetail = globalThis.prksEffectiveConceptDetail(
        { id: child, name: "Emergence", parents: [], children: [] }, ops, catalogue);
    assert.deepEqual(childDetail.parents, [{ id: parent, name: "Systems" }]);

    /* ... and the parent gains the child, because it is one table seen from
     * two sides. */
    const parentDetail = globalThis.prksEffectiveConceptDetail(
        { id: parent, name: "Systems", parents: [], children: [] }, ops, catalogue);
    assert.deepEqual(parentDetail.children, [{ id: child, name: "Emergence" }]);

    /* Nothing is invented for a Concept whose name this device does not hold. */
    const blind = globalThis.prksEffectiveConceptDetail(
        { id: child, name: "Emergence", parents: [], children: [] }, ops, []);
    assert.deepEqual(blind.parents, []);
}

/* ---- deletion ---- */

async function deletingCancelsWhatWasNeverSent() {
    const store = newStore();
    const id = "C-" + "4".repeat(32);
    const other = "C-" + "5".repeat(32);
    await store.saveConceptFields(id, { description: "Doomed" }, fieldBase(0));
    await store.setConceptParents(other, [id], parentsBase([], 0));

    const deletion = await store.deleteConcept(id);
    assert.equal((await rowsFor(store, "SET_CONCEPT_FIELD")).length, 0);
    assert.equal((await rowsFor(store, "SET_CONCEPT_PARENTS")).length, 0,
        "putting another Concept under one that is about to be deleted is work "
        + "the deletion only undoes");
    assert.equal(deletion.base_revision, null, "destruction addresses an identity");
}

async function aSentParentAssignmentIsWaitedForRatherThanRewritten() {
    const store = newStore();
    const id = "C-" + "6".repeat(32);
    const other = "C-" + "7".repeat(32);
    const assigned = await store.setConceptParents(other, [id], parentsBase([], 0));
    await store.updateOperationSyncState(assigned.op_id, { status: "syncing" });

    const deletion = await store.deleteConcept(id);
    assert.equal((await rowsFor(store, "SET_CONCEPT_PARENTS")).length, 1,
        "a row that may already be on the wire stays immutable");
    assert.deepEqual(deletion.depends_on, [assigned.op_id],
        "so the deletion is ordered behind it instead");
}

async function deletingAConceptCreatedHereFoldsItAway() {
    const store = newStore();
    const created = await store.createConcept({ name: "Typed by accident" });
    await store.saveConceptFields(created.entity_id, { description: "oops" }, fieldBase(0));
    assert.equal(await store.deleteConcept(created.entity_id), null);
    assert.deepEqual(await store.listOperations(), [],
        "nothing about this Concept should ever reach the server");
}

async function aPendingDeletionIsATombstone() {
    const store = newStore();
    const id = "C-" + "8".repeat(32);
    await store.deleteConcept(id);
    const ops = await store.listOperations();
    assert.deepEqual(globalThis.prksEffectiveConcepts([{ id: id, name: "Emergence" }], ops), [],
        "hidden, with nothing acknowledged destroyed");

    /* And it stops being anyone's parent or child on screen. */
    const detail = globalThis.prksEffectiveConceptDetail(
        { id: "C-9", name: "Other", parents: [{ id: id, name: "Emergence" }],
          children: [{ id: id, name: "Emergence" }] }, ops, []);
    assert.deepEqual(detail.parents, []);
    assert.deepEqual(detail.children, []);

    await assert.rejects(
        () => store.setConceptIdentity(id, "Renamed", [], identityBase("Emergence", [], 0)),
        error => error.prksLocalStoreCode === "entity_deleted");
    await assert.rejects(
        () => store.setConceptParents("C-OTHER", [id], parentsBase([], 0)),
        error => error.prksLocalStoreCode === "entity_deleted",
        "nor may it be chosen as a parent while it is being deleted");
}

async function aRefusedDeletionStopsHidingTheConcept() {
    /* The other half of the tombstone contract, and the easy one to miss. A
     * deletion hides its Concept while the server has not answered. Once the
     * server REFUSES -- `CONCEPT_IN_USE`, because notes still name it -- the
     * Concept must come back: the refusal is the server saying it is still
     * there, and a library that kept hiding it would disagree with both the
     * server and the Diagnostics entry explaining why. */
    const store = newStore();
    const id = "C-" + "A".repeat(31) + "9";
    const deletion = await store.deleteConcept(id);
    const rows = [{ id: id, name: "Emergence" }];

    let ops = await store.listOperations();
    assert.deepEqual(globalThis.prksEffectiveConcepts(rows, ops), [],
        "hidden while the answer is still owed");

    /* A transport failure is NOT an answer: the delete may still land, so it
     * goes on hiding. */
    await store.updateOperationSyncState(deletion.op_id,
        { status: "pending", last_error: "Sync failed; retry scheduled." });
    ops = await store.listOperations();
    assert.deepEqual(globalThis.prksEffectiveConcepts(rows, ops), [],
        "a failed attempt is not a refusal");

    await store.updateOperationSyncState(deletion.op_id,
        { status: "conflict", server_result: { code: "CONCEPT_IN_USE" } });
    ops = await store.listOperations();
    assert.deepEqual(globalThis.prksEffectiveConcepts(rows, ops).map(r => r.id), [id],
        "refused, so the Concept is visible again");

    /* And the conflict itself survives being visible -- the user still has a
     * decision to make. */
    const conflicted = (await store.listOperations())
        .filter(r => r.status === "conflict");
    assert.equal(conflicted.length, 1);
    assert.equal(conflicted[0].server_result.code, "CONCEPT_IN_USE");

    /* It also stops being anyone's phantom parent or child once refused. */
    const detail = globalThis.prksEffectiveConceptDetail(
        { id: "C-OTHER", name: "Other", parents: [{ id: id, name: "Emergence" }],
          children: [] }, ops, []);
    assert.deepEqual(detail.parents, [{ id: id, name: "Emergence" }]);
}

/* ---- bases ---- */

async function theBaseIsAcknowledgedAndNeverGuessed() {
    const store = newStore();
    globalThis.prksSync = { store: store };
    const created = await store.createConcept({ name: "Systems", description: "A definition." });
    const ops = await store.listOperations();

    const identity = await globalThis.prksAcknowledgedConceptIdentity(created.entity_id, ops);
    assert.deepEqual(identity, { name: "Systems", aliases: [], revision: 0 },
        "the construction payload IS the base, at a revision that is known");
    const parents = await globalThis.prksAcknowledgedConceptParents(created.entity_id, ops);
    assert.deepEqual(parents, { parent_ids: [], revision: 0 });

    const base = await globalThis.prksAcknowledgedConceptFields(created.entity_id, ops);
    assert.equal(base.description.value, "A definition.");
    assert.equal(base.description.revision, 0);
    assert.deepEqual(
        globalThis.prksDirtyConceptFields(created.entity_id,
            { description: "A definition." }, base, ops), {},
        "reopening the form and saving changes nothing");

    globalThis.prksOfflineReadEntity = async () => ({ value: null, source: "unavailable" });
    globalThis.prksOfflineInvalidateEntity = async () => true;
    assert.equal(await globalThis.prksAcknowledgedConceptIdentity("C-UNKNOWN", ops), null,
        "guessing a revision would silently overwrite another device");
    assert.equal(await globalThis.prksAcknowledgedConceptParents("C-UNKNOWN", ops), null);
    assert.equal(await globalThis.prksAcknowledgedConceptFields("C-UNKNOWN", ops), null);
}

async function main() {
    await aConceptIsAValidParentImmediately();
    await aConceptNeedsAName();
    await theDefinitionCoalescesAndCancels();
    await theDefinitionCancelGoesThroughApiWrapper();
    await theDefinitionIsNotTheIdentity();
    await theIdentityIsOneDecision();
    await aPendingRenameReachesEverySurfaceThatNamesIt();
    await anIdentityEditSurvivesAReload();
    await theParentSetIsASet();
    await theParentSetCancelGoesThroughApiWrapper();
    await aConceptCannotBeItsOwnParent();
    await aPendingHierarchyShowsAtBothEnds();
    await deletingCancelsWhatWasNeverSent();
    await aSentParentAssignmentIsWaitedForRatherThanRewritten();
    await deletingAConceptCreatedHereFoldsItAway();
    await aPendingDeletionIsATombstone();
    await aRefusedDeletionStopsHidingTheConcept();
    await theBaseIsAcknowledgedAndNeverGuessed();
    console.log("All " + checks + " concept checks passed");
}

main()
    .then(() => process.exit(0))
    .catch(error => { console.error(error); process.exit(1); });
