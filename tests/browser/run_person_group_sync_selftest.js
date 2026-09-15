"use strict";
/* Person Groups: four operation shapes over one entity.
 *
 * What this covers and nothing else does cheaply: a group created here is
 * usable immediately and orders everything behind it; a field edit cancels when
 * it is taken back; a membership is a PAIR and not a replacement; and deleting
 * a group reasons about every unsynchronized operation naming it rather than
 * leaving the server work whose result the next operation destroys.
 */
const strict = require("assert/strict");
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); },
    { get: (_t, k) => (...args) => { checks += 1; return strict[k](...args); } });
const { createFakeIndexedDBFactory } = require("./lib/fake_indexeddb.js");
const { createPrksLocalStore } = require("../../frontend/js/local-store.js");
require("../../frontend/js/sync-runtime.js");
require("../../frontend/js/person-state.js");
require("../../frontend/js/person-metadata-state.js");
require("../../frontend/js/person-group-state.js");

let sequence = 0;
const uuid = () => "00000000-0000-4000-8000-" + (++sequence).toString(16).padStart(12, "0");
const tick = () => new Promise(resolve => setTimeout(resolve, 1));
async function settle() { for (let i = 0; i < 16; i++) await tick(); }
const newStore = () => createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });

function baseAt(revisions, values) {
    const base = {};
    globalThis.PRKS_PERSON_GROUP_FIELDS.forEach(function (name) {
        base[name] = {
            value: (values && values[name]) || "",
            revision: (revisions && revisions[name]) || 0,
        };
    });
    return base;
}

const rowsFor = async (store, operation) =>
    (await store.listOperations()).filter(r => r.operation === operation);

function groupAck(op) {
    return { code: "ACKNOWLEDGED", group_id: op.entity_id, changed: true,
        group: { id: op.entity_id, name: op.payload.name, parent_id: op.payload.parent_id || null,
            description: op.payload.description, member_count: 0, child_count: 0 } };
}
function fieldAck(op, revision) {
    return { code: "ACKNOWLEDGED", group_id: op.entity_id, field: op.payload.field,
        changed: true, server_revision: revision, value_omitted: true };
}
function memberAck(op, revision) {
    return { code: "ACKNOWLEDGED", group_id: op.entity_id, person_id: op.payload.person_id,
        changed: true, present: op.operation === "ADD_PERSON_GROUP_MEMBER",
        server_revision: revision };
}
function personAck(op) {
    return { code: "ACKNOWLEDGED", person_id: op.entity_id, changed: true,
        person: { id: op.entity_id, first_name: "Ada", last_name: "Lovelace" } };
}

function runtimeFor(store, respond) {
    const silent = handler => Object.assign({}, handler, { reconcile: async () => true });
    return globalThis.createPrksSyncRuntime({
        store, online: () => true, request: respond,
        handlers: {
            CREATE_PERSON: silent(globalThis.prksPersonSyncHandler),
            CREATE_PERSON_GROUP: silent(globalThis.prksPersonGroupCreateSyncHandler),
            SET_PERSON_GROUP_FIELD: silent(globalThis.prksPersonGroupFieldSyncHandler),
            ADD_PERSON_GROUP_MEMBER: silent(globalThis.prksPersonGroupMemberSyncHandler),
            REMOVE_PERSON_GROUP_MEMBER: silent(globalThis.prksPersonGroupMemberSyncHandler),
            DELETE_PERSON_GROUP: silent(globalThis.prksPersonGroupDeleteSyncHandler),
        },
    });
}

/* ---- construction ---- */

async function aGroupIsUsableTheMomentItIsCreated() {
    const store = newStore();
    const created = await store.createPersonGroup({ name: "Analysts", description: "Engine" });
    assert.match(created.entity_id, /^PG-[0-9A-F]{32}$/,
        "a permanent distributed id, minted here -- never remapped later");
    assert.equal(created.base_revision, null, "construction is not mutation");

    const ops = await store.listOperations();
    const catalogue = globalThis.prksEffectivePersonGroups([], ops);
    assert.equal(catalogue.length, 1);
    assert.equal(catalogue[0].name, "Analysts");
    assert.equal(catalogue[0].member_count, 0);

    /* And a group created UNDER one this device also created waits for it: the
     * server validates the hierarchy, and a parent it has never heard of is a
     * refusal rather than a tree. */
    const child = await store.createPersonGroup({ name: "Juniors", parent_id: created.entity_id });
    assert.deepEqual(child.depends_on, [created.op_id]);
    const tree = globalThis.prksEffectivePersonGroups([], await store.listOperations());
    assert.equal(tree.length, 2);
    assert.equal(tree.find(g => g.name === "Juniors").parent_id, created.entity_id);
}

async function aGroupNeedsAName() {
    const store = newStore();
    await assert.rejects(() => store.createPersonGroup({ name: "   " }),
        e => e.prksLocalStoreCode === "invalid_envelope");
}

/* ---- fields ---- */

async function fieldEditsCoalesceAndCancel() {
    const store = newStore();
    const base = baseAt({ name: 4 }, { name: "Analysts" });
    await store.savePersonGroupFields("PG-1", { name: "Engineers" }, base);
    await store.savePersonGroupFields("PG-1", { name: "Mathematicians" }, base);
    let rows = await rowsFor(store, "SET_PERSON_GROUP_FIELD");
    assert.equal(rows.length, 1, "a never-sent row is rewritten, not stacked");
    assert.equal(rows[0].payload.value, "Mathematicians");
    assert.equal(rows[0].base_revision, 4, "measured against the acknowledged revision");

    // A -> B -> A is not two changes, it is none.
    await store.savePersonGroupFields("PG-1", { name: "Analysts" }, base);
    assert.equal((await rowsFor(store, "SET_PERSON_GROUP_FIELD")).length, 0);

    // Two fields are two decisions, each with its own base.
    await store.savePersonGroupFields("PG-1",
        { name: "Engineers", description: "Engine people" },
        baseAt({ name: 4, description: 9 }, { name: "Analysts" }));
    rows = await rowsFor(store, "SET_PERSON_GROUP_FIELD");
    assert.equal(rows.length, 2);
    const byField = new Map(rows.map(r => [r.payload.field, r]));
    assert.equal(byField.get("name").base_revision, 4);
    assert.equal(byField.get("description").base_revision, 9);

    /* A busy field blocks its own scope and nothing else -- which is the whole
     * point of a per-field conflict unit. */
    await store.updateOperationSyncState(byField.get("name").op_id, { status: "syncing" });
    await assert.rejects(
        () => store.savePersonGroupFields("PG-1", { name: "Again" }, baseAt({ name: 4 })),
        e => e.prksLocalStoreCode === "scope_busy");
    await store.savePersonGroupFields("PG-1", { description: "Changed" },
        baseAt({ description: 9 }));
    assert.equal((await rowsFor(store, "SET_PERSON_GROUP_FIELD")).length, 2);
}

async function movingIntoALocalGroupWaitsForIt() {
    const store = newStore();
    const parent = await store.createPersonGroup({ name: "Top" });
    const [move] = await store.savePersonGroupFields("PG-existing",
        { parent_id: parent.entity_id }, baseAt({ parent_id: 2 }));
    assert.deepEqual(move.depends_on, [parent.op_id],
        "the server cannot put a group inside one it has never heard of");
}

async function editsFollowTheGroupsOwnCreation() {
    const store = newStore();
    const created = await store.createPersonGroup({ name: "Analysts" });
    const [edit] = await store.savePersonGroupFields(created.entity_id,
        { description: "Written before it ever synchronized" }, baseAt());
    assert.deepEqual(edit.depends_on, [created.op_id]);
    const stored = (await store.listOperations()).find(r => r.op_id === created.op_id);
    assert.equal(stored.payload.description, "",
        "never folded into the creation: two decisions stay two operations");

    const sent = [];
    const runtime = runtimeFor(store, async (_path, options) => {
        const body = JSON.parse(options.body);
        sent.push(body.operation);
        return { ok: true, status: 200, json: async () =>
            (body.operation === "CREATE_PERSON_GROUP" ? groupAck(body) : fieldAck(body, 1)) };
    });
    await runtime.wake();
    await settle();
    runtime.stop();
    assert.deepEqual(sent, ["CREATE_PERSON_GROUP", "SET_PERSON_GROUP_FIELD"],
        "the group exists on the server before anything edits it");
    assert.equal((await store.listOperations()).length, 0, "and both retire");
}

async function aRefusedCreationTakesItsEditsDownVisibly() {
    const store = newStore();
    const created = await store.createPersonGroup({ name: "Analysts" });
    const [edit] = await store.savePersonGroupFields(created.entity_id,
        { description: "Never arrives" }, baseAt());

    const sent = [];
    const runtime = runtimeFor(store, async (_path, options) => {
        const body = JSON.parse(options.body);
        sent.push(body.operation);
        return { ok: false, status: 409, json: async () => ({
            code: "NAME_TAKEN", group_id: body.entity_id,
            message: "A group with this name already exists." }) };
    });
    await runtime.wake();
    await settle();
    runtime.stop();
    assert.deepEqual(sent, ["CREATE_PERSON_GROUP"], "the edit is never attempted");

    /* A name collision is the user's to decide, not something to discard for
     * them: only the server sees every group, so this is the first they can
     * know of it -- and the description and parent they also chose would go
     * with a silent discard. */
    const creation = (await store.listOperations()).find(r => r.op_id === created.op_id);
    assert.equal(creation.status, "conflict");
    assert.equal(creation.server_result.code, "NAME_TAKEN");
    assert.equal((await store.listOperations()).find(r => r.op_id === edit.op_id).status,
        "pending", "its edit waits on a decision rather than failing on its own");

    // Discarding the creation settles everything that was waiting behind it.
    await store.resolveConflict(created.op_id, null);
    const after = (await store.listOperations()).find(r => r.op_id === edit.op_id);
    assert.equal(after.status, "conflict", "a decision, not a permanent wait");
    assert.equal(after.server_result.code, "DEPENDENCY_FAILED");

    /* And the group is gone from what the user sees: the creation was the only
     * thing that ever made it exist on this device. */
    assert.deepEqual(
        globalThis.prksEffectivePersonGroups([], await store.listOperations()), []);
}

async function aFailedCreationRefusesNewDependentsOutright() {
    const store = newStore();
    const created = await store.createPersonGroup({ name: "Analysts" });
    /* Exactly the state a creation the server refused TERMINALLY is left in:
     * acknowledged, with a recorded result, and retained for whatever already
     * depends on it. */
    await store.updateOperationSyncState(created.op_id,
        { status: "acknowledged", server_result: { code: "NAME_TAKEN" } });
    await assert.rejects(
        () => store.savePersonGroupFields(created.entity_id, { name: "Retry" }, baseAt()),
        e => e.prksLocalStoreCode === "dependency_failed" && /group/i.test(e.message));
    await assert.rejects(
        () => store.setPersonGroupMember(created.entity_id, "P-A", true,
            { present: false, revision: 0 }),
        e => e.prksLocalStoreCode === "dependency_failed" && /group/i.test(e.message));
}

/* ---- membership ---- */

async function membershipIsAPairAndNotAReplacement() {
    const store = newStore();
    const absent = { present: false, revision: 0 };
    const added = await store.setPersonGroupMember("PG-1", "P-A", true, absent);
    assert.equal(added.operation, "ADD_PERSON_GROUP_MEMBER");
    assert.equal(added.base_revision, 0);
    await store.setPersonGroupMember("PG-1", "P-B", true, absent);
    assert.equal((await store.listOperations()).length, 2,
        "two people, two independent decisions -- never one replacement");

    /* Added and taken back before either was sent. Not two changes: none. */
    await store.setPersonGroupMember("PG-1", "P-A", false, absent);
    const remaining = await store.listOperations();
    assert.equal(remaining.length, 1);
    assert.equal(remaining[0].payload.person_id, "P-B");

    // Removing someone who is already out of the group leaves nothing behind.
    assert.equal(await store.setPersonGroupMember("PG-1", "P-C", false, absent), null);

    // A removal from a group they ARE in carries the pair's own revision.
    const present = { present: true, revision: 3 };
    const removed = await store.setPersonGroupMember("PG-1", "P-D", false, present);
    assert.equal(removed.operation, "REMOVE_PERSON_GROUP_MEMBER");
    assert.equal(removed.base_revision, 3);
}

async function aMembershipWaitsForBothSidesToExist() {
    const store = newStore();
    const group = await store.createPersonGroup({ name: "Analysts" });
    const person = await store.createPerson({ first_name: "Ada", last_name: "Lovelace" });
    const op = await store.setPersonGroupMember(group.entity_id, person.entity_id, true,
        { present: false, revision: 0 });
    assert.deepEqual(op.depends_on.slice().sort(),
        [group.op_id, person.op_id].sort(),
        "a relationship between two things this device invented waits for both");

    const sent = [];
    const runtime = runtimeFor(store, async (_path, options) => {
        const body = JSON.parse(options.body);
        sent.push(body.operation);
        const answer = body.operation === "CREATE_PERSON" ? personAck(body)
            : body.operation === "CREATE_PERSON_GROUP" ? groupAck(body)
                : memberAck(body, 1);
        return { ok: true, status: 200, json: async () => answer };
    });
    await runtime.wake();
    await settle();
    runtime.stop();
    assert.equal(sent[sent.length - 1], "ADD_PERSON_GROUP_MEMBER",
        "the membership is last, whatever order the two creations went in");
    assert.equal((await store.listOperations()).length, 0);
}

async function membershipComposesIntoBothSurfaces() {
    const store = newStore();
    await store.setPersonGroupMember("PG-1", "P-A", true, { present: false, revision: 0 });
    await store.setPersonGroupMember("PG-2", "P-A", false, { present: true, revision: 1 });
    const ops = await store.listOperations();

    const catalogue = [
        { id: "PG-1", name: "Analysts", parent_id: null, member_count: 0, child_count: 0 },
        { id: "PG-2", name: "Engineers", parent_id: null, member_count: 1, child_count: 0 },
    ];
    const effective = globalThis.prksEffectivePersonGroups(catalogue, ops);
    assert.equal(effective.find(g => g.id === "PG-1").member_count, 1);
    assert.equal(effective.find(g => g.id === "PG-2").member_count, 0);
    assert.equal(catalogue[0].member_count, 0,
        "the acknowledged catalogue is never written through");

    const detail = globalThis.prksEffectivePersonGroupDetail(
        { id: "PG-1", name: "Analysts", members: [] }, ops,
        [{ id: "P-A", first_name: "Ada", last_name: "Lovelace" }]);
    assert.deepEqual(detail.members.map(m => m.id), ["P-A"]);
    assert.equal(detail.member_count, 1);

    const person = globalThis.prksEffectivePersonGroupChips(
        { id: "P-A", groups: [{ id: "PG-2", name: "Engineers" }] }, ops, effective);
    assert.deepEqual(person.groups.map(g => g.id), ["PG-1"],
        "joined one group and left the other, on the Person's own page");
    assert.equal(person.groups[0].name, "Analysts",
        "named from the effective catalogue, so a group created here is not a blank chip");
}

async function aPendingRenameReachesTheChipsThatCarryTheName() {
    const store = newStore();
    await store.savePersonGroupFields("PG-1", { name: "Engineers" },
        baseAt({ name: 2 }, { name: "Analysts" }));
    const ops = await store.listOperations();

    /* A chip carries the group's NAME, and the Person's page is one of the
     * places a renamed group is read. Membership did not change, so an overlay
     * that only tracked membership would leave the old name on screen until
     * the rename happened to reach the server. */
    const person = globalThis.prksEffectivePersonGroupChips(
        { id: "P-A", groups: [{ id: "PG-1", name: "Analysts" }] }, ops, []);
    assert.deepEqual(person.groups, [{ id: "PG-1", name: "Engineers" }]);
    const rows = globalThis.prksEffectivePersonGroupChipRows(
        [{ id: "P-A", groups: [{ id: "PG-1", name: "Analysts" }] },
            { id: "P-B", groups: [] }], ops, []);
    assert.equal(rows[0].groups[0].name, "Engineers");
    assert.deepEqual(rows[1].groups, [], "and a Person in no group is untouched");
}

/* ---- deletion ---- */

async function deletingCancelsWhatWasNeverSent() {
    const store = newStore();
    await store.savePersonGroupFields("PG-1", { name: "Renamed" }, baseAt({ name: 1 }));
    await store.setPersonGroupMember("PG-1", "P-A", true, { present: false, revision: 0 });
    const removal = await store.deletePersonGroup("PG-1");
    assert.equal(removal.operation, "DELETE_PERSON_GROUP");
    assert.equal(removal.base_revision, null,
        "destruction addresses an identity, not a value");
    const rows = await store.listOperations();
    assert.equal(rows.length, 1,
        "a rename the server would immediately undo is not worth sending");
    assert.deepEqual(removal.depends_on, []);

    /* And nothing else may be enqueued against it: the only outcome would be
     * ENTITY_NOT_FOUND -- born unsendable. */
    await assert.rejects(
        () => store.savePersonGroupFields("PG-1", { name: "Too late" }, baseAt({ name: 1 })),
        e => e.prksLocalStoreCode === "entity_deleted");
    await assert.rejects(
        () => store.setPersonGroupMember("PG-1", "P-B", true, { present: false, revision: 0 }),
        e => e.prksLocalStoreCode === "entity_deleted");
    // Deleting twice is one decision.
    assert.equal((await store.deletePersonGroup("PG-1")).op_id, removal.op_id);
}

async function deletingAGroupCreatedHereFoldsItAwayEntirely() {
    const store = newStore();
    const created = await store.createPersonGroup({ name: "Mistake" });
    await store.savePersonGroupFields(created.entity_id, { description: "Typo" }, baseAt());
    assert.equal(await store.deletePersonGroup(created.entity_id), null);
    assert.deepEqual(await store.listOperations(), [],
        "nothing about this group ever reaches the server");
}

async function aSentOperationIsWaitedForRatherThanRewritten() {
    const store = newStore();
    const [rename] = await store.savePersonGroupFields("PG-1", { name: "Renamed" },
        baseAt({ name: 1 }));
    await store.updateOperationSyncState(rename.op_id, { status: "syncing" });
    const removal = await store.deletePersonGroup("PG-1");
    assert.deepEqual(removal.depends_on, [rename.op_id],
        "an envelope that may be on the wire stays immutable; the delete queues behind it");
}

async function aPendingDeletionIsATombstoneNotADestruction() {
    const store = newStore();
    await store.deletePersonGroup("PG-MIDDLE");
    const ops = await store.listOperations();
    const catalogue = [
        { id: "PG-TOP", name: "Top", parent_id: null, member_count: 0, child_count: 1 },
        { id: "PG-MIDDLE", name: "Middle", parent_id: "PG-TOP", member_count: 0, child_count: 1 },
        { id: "PG-LEAF", name: "Leaf", parent_id: "PG-MIDDLE", member_count: 0, child_count: 0 },
    ];
    const effective = globalThis.prksEffectivePersonGroups(catalogue, ops);
    assert.deepEqual(effective.map(g => g.id).sort(), ["PG-LEAF", "PG-TOP"]);
    assert.equal(effective.find(g => g.id === "PG-LEAF").parent_id, "PG-TOP",
        "children are reparented exactly as the canonical delete does, not orphaned");
    assert.equal(catalogue.length, 3,
        "and nothing is destroyed: a refusal restores the group by doing nothing");
    const person = globalThis.prksEffectivePersonGroupChips(
        { id: "P-A", groups: [{ id: "PG-MIDDLE", name: "Middle" }] }, ops, effective);
    assert.deepEqual(person.groups, [], "the chip goes with it");
}

/* ---- bases ---- */

async function theBaseIsAcknowledgedAndNeverGuessed() {
    const store = newStore();
    const created = await store.createPersonGroup({ name: "Analysts", description: "Engine" });
    const ops = await store.listOperations();
    const base = await globalThis.prksAcknowledgedPersonGroupBase(created.entity_id, ops);
    assert.equal(base.name.value, "Analysts", "the construction payload IS the base");
    assert.equal(base.name.revision, 0, "at revision 0 -- known, not assumed");

    const draft = { name: "Analysts", description: "Engine", parent_id: "" };
    assert.deepEqual(globalThis.prksDirtyPersonGroupFields(created.entity_id, draft, base, ops), {},
        "reopening the form and saving changes nothing");
    assert.deepEqual(
        globalThis.prksDirtyPersonGroupFields(created.entity_id,
            Object.assign({}, draft, { name: "Engineers" }), base, ops),
        { name: "Engineers" }, "only what was typed");

    /* A group the server has never heard of and this device did not create is
     * UNKNOWN, not empty: guessing revision 0 would overwrite whatever another
     * device wrote. */
    globalThis.prksOfflineReadEntity = async () => ({ value: null, source: "unavailable" });
    globalThis.prksOfflineInvalidateEntity = async () => true;
    assert.equal(await globalThis.prksAcknowledgedPersonGroupBase("PG-UNKNOWN", ops), null);
}

async function main() {
    await aGroupIsUsableTheMomentItIsCreated();
    await aGroupNeedsAName();
    await fieldEditsCoalesceAndCancel();
    await movingIntoALocalGroupWaitsForIt();
    await editsFollowTheGroupsOwnCreation();
    await aRefusedCreationTakesItsEditsDownVisibly();
    await aFailedCreationRefusesNewDependentsOutright();
    await membershipIsAPairAndNotAReplacement();
    await aMembershipWaitsForBothSidesToExist();
    await membershipComposesIntoBothSurfaces();
    await aPendingRenameReachesTheChipsThatCarryTheName();
    await deletingCancelsWhatWasNeverSent();
    await deletingAGroupCreatedHereFoldsItAwayEntirely();
    await aSentOperationIsWaitedForRatherThanRewritten();
    await aPendingDeletionIsATombstoneNotADestruction();
    await theBaseIsAcknowledgedAndNeverGuessed();
    console.log("All " + checks + " person group checks passed");
}

main()
    .then(() => process.exit(0))
    .catch(error => { console.error(error); process.exit(1); });
