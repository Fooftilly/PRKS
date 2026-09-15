"use strict";
/* The Tag VOCABULARY: creating one, deleting one, and what each means for the
 * relationships around it.
 *
 * The Work-Tag relationship has been durable since Phase 2. What this covers is
 * the other half: a Tag minted on this device is attachable immediately and the
 * attachment waits for it, and deleting a Tag reasons about every relationship
 * intent that names it rather than leaving the server work the next operation
 * destroys.
 */
const strict = require("assert/strict");
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); },
    { get: (_t, k) => (...args) => { checks += 1; return strict[k](...args); } });
const { createFakeIndexedDBFactory } = require("./lib/fake_indexeddb.js");
const { createPrksLocalStore } = require("../../frontend/js/local-store.js");
require("../../frontend/js/sync-runtime.js");
require("../../frontend/js/work-tag-state.js");
require("../../frontend/js/tag-vocabulary-state.js");

let sequence = 0;
const uuid = () => "00000000-0000-4000-8000-" + (++sequence).toString(16).padStart(12, "0");
const tick = () => new Promise(resolve => setTimeout(resolve, 1));
async function settle() { for (let i = 0; i < 16; i++) await tick(); }
const newStore = () => createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });

const rowsFor = async (store, operation) =>
    (await store.listOperations()).filter(r => r.operation === operation);

function tagAck(op) {
    return { code: "ACKNOWLEDGED", tag_id: op.entity_id, changed: true,
        tag: { id: op.entity_id, name: op.payload.name, color: op.payload.color } };
}
function relationAck(op) {
    return { code: "ACKNOWLEDGED", work_id: op.entity_id, tag_id: op.payload.tag_id,
        present: op.operation === "ADD_WORK_TAG", changed: true, server_revision: 1,
        tag: { id: op.payload.tag_id, name: "Epistemology", color: "#6d6cf7" } };
}

function runtimeFor(store, respond) {
    const silent = handler => Object.assign({}, handler, { reconcile: async () => true });
    return globalThis.createPrksSyncRuntime({
        store, online: () => true, request: respond,
        handlers: {
            CREATE_TAG: silent(globalThis.prksTagCreateSyncHandler),
            DELETE_TAG: silent(globalThis.prksTagDeleteSyncHandler),
            ADD_WORK_TAG: silent(globalThis.prksWorkTagSyncHandler),
            REMOVE_WORK_TAG: silent(globalThis.prksWorkTagSyncHandler),
        },
    });
}

/* ---- construction ---- */

async function aTagIsAttachableTheMomentItIsCreated() {
    const store = newStore();
    const created = await store.createTag({ name: "Epistemology" }, []);
    assert.match(created.entity_id, /^T-[0-9A-F]{32}$/,
        "a permanent distributed id, minted here -- never remapped later");
    assert.equal(created.base_revision, null, "construction is not mutation");

    const ops = await store.listOperations();
    const catalogue = globalThis.prksEffectiveTagCatalogue([], ops);
    assert.equal(catalogue.length, 1);
    assert.equal(catalogue[0].name, "Epistemology");
    assert.deepEqual(catalogue[0].aliases, [], "and it is a well-formed catalogue row");

    /* The relationship waits for the Tag, by the GENERIC mechanism: the server
     * cannot attach one it has never heard of. */
    const link = await store.coalesceWorkTag("W-1", created.entity_id, true, false, 0,
        { id: created.entity_id, name: "Epistemology" });
    assert.deepEqual(link.depends_on, [created.op_id]);

    const sent = [];
    const runtime = runtimeFor(store, async (_path, options) => {
        const body = JSON.parse(options.body);
        sent.push(body.operation);
        return { ok: true, status: 200, json: async () =>
            (body.operation === "CREATE_TAG" ? tagAck(body) : relationAck(body)) };
    });
    await runtime.wake();
    await settle();
    runtime.stop();
    assert.deepEqual(sent, ["CREATE_TAG", "ADD_WORK_TAG"],
        "the Tag exists on the server before anything is attached to it");
    assert.equal((await store.listOperations()).length, 0, "and both retire");
}

async function anObviousLocalCollisionIsRefusedEarly() {
    const store = newStore();
    const known = [{ id: "T-EXISTING", name: "Epistemology", color: "#6d6cf7", aliases: [] }];
    await assert.rejects(
        () => store.createTag({ name: "epistemology" }, known),
        e => e.prksLocalStoreCode === "name_taken");
    /* But the authoritative answer stays CANONICAL: only the server sees every
     * Tag, so an empty local catalogue is not evidence the name is free. */
    const created = await store.createTag({ name: "epistemology" }, []);
    assert.equal(created.payload.name, "epistemology");
}

async function aTagNeedsAName() {
    const store = newStore();
    await assert.rejects(() => store.createTag({ name: "   " }, []),
        e => e.prksLocalStoreCode === "invalid_envelope");
}

/* ---- destruction ---- */

async function deletingCancelsTheRelationshipsItMakesPointless() {
    const store = newStore();
    await store.coalesceWorkTag("W-1", "T-1", true, false, 0, { id: "T-1", name: "X" });
    const removal = await store.deleteTag("T-1");
    assert.equal(removal.operation, "DELETE_TAG");
    assert.equal(removal.base_revision, null,
        "destruction addresses an identity, not a value");
    assert.equal((await store.listOperations()).length, 1,
        "attaching a Tag immediately before deleting it is work the delete undoes");

    /* And nothing else may be enqueued against it: the only outcome would be
     * TAG_DELETED. */
    await assert.rejects(
        () => store.coalesceWorkTag("W-2", "T-1", true, false, 0, { id: "T-1", name: "X" }),
        e => e.prksLocalStoreCode === "entity_deleted");
    assert.equal((await store.deleteTag("T-1")).op_id, removal.op_id,
        "deleting twice is one decision");
}

async function deletingATagCreatedHereFoldsItAway() {
    const store = newStore();
    const created = await store.createTag({ name: "Mistake" }, []);
    await store.coalesceWorkTag("W-1", created.entity_id, true, false, 0,
        { id: created.entity_id, name: "Mistake" });
    assert.equal(await store.deleteTag(created.entity_id), null);
    assert.deepEqual(await store.listOperations(), [],
        "nothing about this tag ever reaches the server");
}

async function aSentAttachmentIsWaitedForRatherThanRewritten() {
    const store = newStore();
    const link = await store.coalesceWorkTag("W-1", "T-1", true, false, 0,
        { id: "T-1", name: "X" });
    await store.updateOperationSyncState(link.op_id, { status: "syncing" });
    const removal = await store.deleteTag("T-1");
    assert.deepEqual(removal.depends_on, [link.op_id],
        "an envelope that may be on the wire stays immutable");
}

async function aPendingDeletionHidesTheTagEverywhere() {
    const store = newStore();
    await store.deleteTag("T-1");
    const ops = await store.listOperations();
    const catalogue = [
        { id: "T-1", name: "Doomed", color: "#6d6cf7", aliases: [] },
        { id: "T-2", name: "Kept", color: "#6d6cf7", aliases: [] },
    ];
    assert.deepEqual(
        globalThis.prksEffectiveTagCatalogue(catalogue, ops).map(t => t.id), ["T-2"]);
    assert.equal(catalogue.length, 2,
        "nothing is destroyed: a refusal restores it by doing nothing");
    /* And the chip goes with it: the relationship row is still in the cache,
     * and leaving the chip would show a Tag the user has already removed from
     * the vocabulary. */
    assert.deepEqual(
        globalThis.prksEffectiveTagChips(
            [{ id: "T-1", name: "Doomed" }, { id: "T-2", name: "Kept" }], ops)
            .map(t => t.id),
        ["T-2"]);
}

async function aRefusedCreationTakesItsAttachmentsDownVisibly() {
    const store = newStore();
    const created = await store.createTag({ name: "Epistemology" }, []);
    const link = await store.coalesceWorkTag("W-1", created.entity_id, true, false, 0,
        { id: created.entity_id, name: "Epistemology" });

    const sent = [];
    const runtime = runtimeFor(store, async (_path, options) => {
        const body = JSON.parse(options.body);
        sent.push(body.operation);
        return { ok: false, status: 409, json: async () => ({
            code: "NAME_TAKEN", tag_id: body.entity_id, target_tag_id: "T-OTHER" }) };
    });
    await runtime.wake();
    await settle();
    runtime.stop();
    assert.deepEqual(sent, ["CREATE_TAG"], "the attachment is never attempted");
    const creation = (await store.listOperations()).find(r => r.op_id === created.op_id);
    assert.equal(creation.status, "conflict");
    assert.equal(creation.server_result.code, "NAME_TAKEN");

    await store.resolveConflict(created.op_id, null);
    const after = (await store.listOperations()).find(r => r.op_id === link.op_id);
    assert.equal(after.status, "conflict", "a decision, not a permanent wait");
    assert.equal(after.server_result.code, "DEPENDENCY_FAILED");
    assert.deepEqual(globalThis.prksEffectiveTagCatalogue([], await store.listOperations()), [],
        "and the tag is gone from what the user sees");
}

async function main() {
    await aTagIsAttachableTheMomentItIsCreated();
    await anObviousLocalCollisionIsRefusedEarly();
    await aTagNeedsAName();
    await deletingCancelsTheRelationshipsItMakesPointless();
    await deletingATagCreatedHereFoldsItAway();
    await aSentAttachmentIsWaitedForRatherThanRewritten();
    await aPendingDeletionHidesTheTagEverywhere();
    await aRefusedCreationTakesItsAttachmentsDownVisibly();
    console.log("All " + checks + " tag vocabulary checks passed");
}

main()
    .then(() => process.exit(0))
    .catch(error => { console.error(error); process.exit(1); });
