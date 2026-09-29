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
const {
    operationFingerprint,
    loseOwnershipOnNextOperationsRead,
    loseOwnershipOnNextOperationsDelete,
    loseOwnershipOnNextOperationsPut,
    loseOwnershipWhenWriteCommits,
    runtimeForStore,
} = require("./lib/ownership_flip.js");
const { createPrksLocalStore } = require("../../frontend/js/local-store.js");
require("../../frontend/js/sync-runtime.js");
require("../../frontend/js/person-state.js");
require("../../frontend/js/person-metadata-state.js");
require("../../frontend/js/person-group-state.js");
require("../../frontend/js/work-role-state.js");
globalThis.window = globalThis.window || globalThis;
require("../../frontend/js/components/people-groups.js");

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

/* ---- deleting a Person ---- */

async function deletingAPersonCancelsWhatWasNeverSent() {
    const store = newStore();
    await store.savePersonMetadataFields("P-1", { about: "Edited" },
        Object.fromEntries(globalThis.PRKS_PERSON_METADATA_FIELDS.map(
            name => [name, { value: "", revision: 0 }])));
    await store.setPersonGroupMember("PG-1", "P-1", true, { present: false, revision: 0 });
    const removal = await store.deletePerson("P-1");
    assert.equal(removal.operation, "DELETE_PERSON");
    assert.equal(removal.base_revision, null,
        "destruction addresses an identity, not a value");
    assert.equal((await store.listOperations()).length, 1,
        "an edit the deletion would immediately undo is not worth sending");
    assert.deepEqual(removal.depends_on, []);

    /* And nothing else may be enqueued against them: every later operation
     * naming this Person could only be refused. */
    await assert.rejects(
        () => store.setPersonGroupMember("PG-2", "P-1", true, { present: false, revision: 0 }),
        e => e.prksLocalStoreCode === "entity_deleted");
    assert.equal((await store.deletePerson("P-1")).op_id, removal.op_id,
        "deleting twice is one decision");
}

async function deletingAPersonCreatedHereFoldsThemAway() {
    const store = newStore();
    const created = await store.createPerson({ first_name: "Jane", last_name: "Doe" });
    await store.setPersonGroupMember("PG-1", created.entity_id, true,
        { present: false, revision: 0 });
    assert.equal(await store.deletePerson(created.entity_id), null);
    assert.deepEqual(await store.listOperations(), [],
        "nothing about this person ever reaches the server");
}

async function aSentLinkIsWaitedForRatherThanRewritten() {
    const store = newStore();
    const link = await store.saveWorkPersonRole("W-1",
        { person_id: "P-1", role_type: "Author", state: "Credited" },
        { state: null, revision: 0 });
    await store.updateOperationSyncState(link.op_id, { status: "syncing" });
    const removal = await store.deletePerson("P-1");
    assert.deepEqual(removal.depends_on, [link.op_id],
        "an envelope that may be on the wire stays immutable; the delete queues behind it");
    /* And the server will then refuse the deletion, because the link landed --
     * which is the honest outcome rather than a silent cascade. */
    assert.equal(removal.entity_type, "person");
}

async function aPendingDeletionHidesThePersonEverywhere() {
    const store = newStore();
    await store.deletePerson("P-1");
    const ops = await store.listOperations();
    const people = [{ id: "P-1", first_name: "Ada", last_name: "Lovelace" },
        { id: "P-2", first_name: "Grace", last_name: "Hopper" }];
    const effective = globalThis.prksEffectivePeople(people, ops);
    assert.deepEqual(effective.map(p => p.id), ["P-2"]);
    assert.equal(people.length, 2,
        "nothing is destroyed: a refusal restores them by doing nothing");

    /* Including the Group page, which is one of the places a Person is read --
     * a tombstone that held only on the People index would leave them visible
     * on exactly the page that names their memberships. */
    const detail = globalThis.prksEffectivePersonGroupDetail(
        { id: "PG-1", name: "Analysts", members: people.slice() }, ops, people);
    assert.deepEqual(detail.members.map(m => m.id), ["P-2"]);
    assert.equal(detail.member_count, 1);
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

async function anOlderSessionCannotCommitAMembershipDuringTheRead() {
    const idb = createFakeIndexedDBFactory();
    const store = createPrksLocalStore({ indexedDB: idb, uuid });
    runtimeForStore(store);
    const absent = { present: false, revision: 0 };
    const seeded = await store.setPersonGroupMember("PG-1", "P-A", true, absent);
    assert.equal(seeded.operation, "ADD_PERSON_GROUP_MEMBER");
    const before = operationFingerprint(await store.listOperations());
    const session = { owned: true };
    loseOwnershipOnNextOperationsRead(idb, session);
    const removed = await globalThis.prksSetPersonGroupMemberDurably(
        "PG-1", "P-A", false, absent, () => session.owned);
    assert.equal(removed, null);
    assert.equal(session.owned, false, "ownership changed during the awaited read");
    assert.equal(operationFingerprint(await store.listOperations()), before,
        "the older session did not delete the membership operation");

    const insertSession = { owned: true };
    loseOwnershipOnNextOperationsRead(idb, insertSession);
    const added = await globalThis.prksSetPersonGroupMemberDurably(
        "PG-1", "P-B", true, absent, () => insertSession.owned);
    assert.equal(added, null);
    assert.equal(
        (await store.listOperations()).some(r => r.payload && r.payload.person_id === "P-B"),
        false, "the older session did not insert a membership");

    globalThis.prksOfflineInvalidateEntity = async () => {};
    const cachedGroupState = async (kind, id) => {
        if (kind !== "person-group-state") return { value: null, source: "unavailable" };
        return {
            value: {
                group_id: id,
                fields: {
                    name: { revision: 0 },
                    description: { revision: 0 },
                    parent_id: { revision: 0 },
                },
                members: [],
            },
            source: "cache",
        };
    };
    let storeCalls = 0;
    const inner = store.setPersonGroupMember.bind(store);
    store.setPersonGroupMember = function () {
        storeCalls += 1;
        return inner.apply(store, arguments);
    };
    const early = { owned: true };
    globalThis.prksOfflineReadEntity = async (kind, id) => {
        early.owned = false;
        return cachedGroupState(kind, id);
    };
    const observed = await globalThis.prksAcknowledgedPersonGroupMembership(
        "PG-9", "P-D", [], () => early.owned);
    assert.equal(observed && observed.prksOwnershipLost, true);
    const earlyResult = await globalThis.prksSetPersonGroupMemberDurably(
        "PG-9", "P-D", true, absent, () => early.owned);
    assert.equal(earlyResult, null);
    assert.equal(storeCalls, 0, "a read that drops ownership does not start the mutation");
    store.setPersonGroupMember = inner;

    const kept = await store.setPersonGroupMember("PG-1", "P-E", true, absent);
    assert.equal(kept.operation, "ADD_PERSON_GROUP_MEMBER",
        "omitting the predicate still records a membership");
}

async function anOlderSessionCannotCommitAHalfAppliedMembershipReplacement() {
    const idb = createFakeIndexedDBFactory();
    const store = createPrksLocalStore({ indexedDB: idb, uuid });
    runtimeForStore(store);
    const seeded = await store.setPersonGroupMember("PG-1", "P-A", true,
        { present: false, revision: 0 });
    assert.equal(seeded.operation, "ADD_PERSON_GROUP_MEMBER");
    const before = operationFingerprint(await store.listOperations());
    const duringDelete = { owned: true };
    loseOwnershipOnNextOperationsDelete(idb, duringDelete);
    const removed = await globalThis.prksSetPersonGroupMemberDurably(
        "PG-1", "P-A", false, { present: true, revision: 4 }, () => duringDelete.owned);
    assert.equal(removed, null);
    assert.equal(duringDelete.owned, false, "ownership changed during the delete");
    let after = await store.listOperations();
    assert.equal(operationFingerprint(after), before,
        "aborting during the delete leaves the queued membership");
    assert.equal(after.some(r => r.operation === "REMOVE_PERSON_GROUP_MEMBER"), false);
    assert.equal(after.find(r => r.payload && r.payload.person_id === "P-A").operation,
        "ADD_PERSON_GROUP_MEMBER");

    const duringInsert = { owned: true };
    loseOwnershipOnNextOperationsPut(idb, duringInsert);
    const replaced = await globalThis.prksSetPersonGroupMemberDurably(
        "PG-1", "P-A", false, { present: true, revision: 4 }, () => duringInsert.owned);
    assert.equal(replaced, null);
    assert.equal(duringInsert.owned, false, "ownership changed during the insert");
    after = await store.listOperations();
    assert.equal(operationFingerprint(after), before,
        "aborting during the insert leaves the queued membership");
    assert.equal(after.some(r => r.operation === "REMOVE_PERSON_GROUP_MEMBER"), false);
    assert.equal(after.find(r => r.payload && r.payload.person_id === "P-A").operation,
        "ADD_PERSON_GROUP_MEMBER");
}

async function aCommittedMembershipWriteStillNotifiesAfterTheEditorMovesOn() {
    const idb = createFakeIndexedDBFactory();
    const store = createPrksLocalStore({ indexedDB: idb, uuid });
    const runtime = runtimeForStore(store);
    await store.listOperations();
    const session = { owned: true };
    loseOwnershipWhenWriteCommits(idb, session);
    const op = await globalThis.prksSetPersonGroupMemberDurably(
        "PG-1", "P-A", true, { present: false, revision: 0 }, () => session.owned);
    assert.equal(session.owned, false, "ownership ended as the commit was delivered");
    assert.equal(op && op.operation, "ADD_PERSON_GROUP_MEMBER");
    assert.equal(
        (await store.listOperations()).some(r => r.operation === "ADD_PERSON_GROUP_MEMBER"),
        true);
    assert.ok(runtime.notifications() >= 1, "a committed membership still notifies sync");
}

async function anOlderSessionCannotCommitAGroupFieldDuringTheRead() {
    const idb = createFakeIndexedDBFactory();
    const store = createPrksLocalStore({ indexedDB: idb, uuid });
    runtimeForStore(store);
    const base = baseAt({ name: 1 }, { name: "Analysts", description: "Engine" });
    const seeded = await store.savePersonGroupFields("PG-1", { name: "Engineers" }, base);
    assert.equal(seeded[0].operation, "SET_PERSON_GROUP_FIELD");
    const before = operationFingerprint(await store.listOperations());
    const session = { owned: true };
    loseOwnershipOnNextOperationsRead(idb, session);
    const written = await globalThis.prksSavePersonGroupFieldsDurably(
        "PG-1", { name: "Mathematicians" }, base, () => session.owned);
    assert.deepEqual(written, []);
    assert.equal(session.owned, false, "ownership changed during the awaited field read");
    assert.equal(operationFingerprint(await store.listOperations()), before,
        "the older session did not replace the field operation");

    const omitted = await store.savePersonGroupFields("PG-1", { description: "Still" }, base);
    assert.equal(omitted[0].payload.field, "description",
        "omitting the predicate still records a field edit");
}

async function anOlderSessionCannotCommitAHalfAppliedGroupFieldReplacement() {
    const idb = createFakeIndexedDBFactory();
    const store = createPrksLocalStore({ indexedDB: idb, uuid });
    runtimeForStore(store);
    const base = baseAt({ name: 1 }, { name: "Analysts" });
    await store.savePersonGroupFields("PG-1", { name: "Engineers" }, base);
    const before = operationFingerprint(await store.listOperations());
    const duringDelete = { owned: true };
    loseOwnershipOnNextOperationsDelete(idb, duringDelete);
    const removed = await globalThis.prksSavePersonGroupFieldsDurably(
        "PG-1", { name: "Mathematicians" }, base, () => duringDelete.owned);
    assert.deepEqual(removed, []);
    assert.equal(duringDelete.owned, false, "ownership changed during the field delete");
    let after = await store.listOperations();
    assert.equal(operationFingerprint(after), before,
        "aborting during the delete leaves the queued field edit");
    assert.equal(after.find(r => r.payload && r.payload.field === "name").payload.value, "Engineers");

    const duringInsert = { owned: true };
    loseOwnershipOnNextOperationsPut(idb, duringInsert);
    const replaced = await globalThis.prksSavePersonGroupFieldsDurably(
        "PG-1", { name: "Mathematicians" }, base, () => duringInsert.owned);
    assert.deepEqual(replaced, []);
    assert.equal(duringInsert.owned, false, "ownership changed during the field insert");
    after = await store.listOperations();
    assert.equal(operationFingerprint(after), before,
        "aborting during the insert leaves the queued field edit");
    assert.equal(after.find(r => r.payload && r.payload.field === "name").payload.value, "Engineers");
    assert.equal(after.some(r => r.payload && r.payload.value === "Mathematicians"), false);
}

async function aCommittedGroupFieldWriteStillNotifiesAfterTheEditorMovesOn() {
    const idb = createFakeIndexedDBFactory();
    const store = createPrksLocalStore({ indexedDB: idb, uuid });
    const runtime = runtimeForStore(store);
    await store.listOperations();
    const session = { owned: true };
    loseOwnershipWhenWriteCommits(idb, session);
    const base = baseAt({ name: 1 }, { name: "Analysts" });
    const written = await globalThis.prksSavePersonGroupFieldsDurably(
        "PG-1", { name: "Engineers" }, base, () => session.owned);
    assert.equal(session.owned, false, "ownership ended as the field commit was delivered");
    assert.equal(written[0] && written[0].operation, "SET_PERSON_GROUP_FIELD");
    assert.equal(
        (await store.listOperations()).some(r => r.operation === "SET_PERSON_GROUP_FIELD"),
        true);
    assert.ok(runtime.notifications() >= 1, "a committed field edit still notifies sync");
}

async function aStaleGroupEditDoesNotCreateATypedParent() {
    const idb = createFakeIndexedDBFactory();
    const store = createPrksLocalStore({ indexedDB: idb, uuid });
    const runtime = runtimeForStore(store);
    await store.listOperations();
    const previous = {
        alert: globalThis.prksAlertMessage,
        ops: globalThis.prksDurableOperationsOrNone,
        base: globalThis.prksAcknowledgedPersonGroupBase,
        catalogue: globalThis.prksEffectivePersonGroupCatalogue,
    };
    const alerts = [];
    const base = baseAt(
        { name: 1, description: 1, parent_id: 1 },
        { name: "Group A", description: "Kept", parent_id: "" });
    const shown = { name: "Group A", description: "Kept", parent_id: "", parent_name: "" };
    globalThis.prksAlertMessage = async (message) => { alerts.push(String(message || "")); };
    globalThis.prksDurableOperationsOrNone = async () => [];
    globalThis.prksAcknowledgedPersonGroupBase = async () => base;

    function editingContext() {
        return {
            destroyed: false,
            ui: { personGroupEditing: true, personGroupEditSession: 4 },
            lastResolvedRoute: { name: "person-group-detail", params: { groupId: "PG-A" } },
            getEntity: () => ({ id: "PG-A", name: "Group A" }),
        };
    }
    function draft(parentName) {
        return { name: "Group A", description: "Kept", parent_id: "", parent_name: parentName };
    }
    function parentCreates(name) {
        return store.listOperations().then(rows => rows.filter(row =>
            row.operation === "CREATE_PERSON_GROUP" && row.payload && row.payload.name === name));
    }
    function fieldWrites() {
        return store.listOperations().then(rows => rows.filter(row =>
            row.operation === "SET_PERSON_GROUP_FIELD" && row.entity_id === "PG-A"));
    }

    try {
        const duringCatalogue = editingContext();
        let releaseCatalogue;
        globalThis.prksEffectivePersonGroupCatalogue = () => new Promise((resolve) => {
            releaseCatalogue = () => {
                duringCatalogue.ui.personGroupEditing = false;
                resolve([]);
            };
        });
        const catalogueSave = globalThis.savePersonGroupEditor(
            duringCatalogue, "PG-A", draft("Catalogue Parent"), shown,
            duringCatalogue.ui.personGroupEditSession);
        await settle();
        releaseCatalogue();
        const catalogueResult = await catalogueSave;
        assert.equal(catalogueResult && catalogueResult.quiet, true,
            "a catalogue that outlives the editor stays quiet");
        assert.deepEqual(await parentCreates("Catalogue Parent"), [],
            "losing the session during the catalogue does not create a parent");
        assert.deepEqual(await fieldWrites(), [],
            "losing the session during the catalogue does not write Group A");

        globalThis.prksEffectivePersonGroupCatalogue = async () => [];
        const duringCreate = editingContext();
        const gate = {
            set owned(value) {
                if (value === false) duringCreate.ui.personGroupEditing = false;
            },
        };
        loseOwnershipOnNextOperationsPut(idb, gate);
        const beforeNotify = runtime.notifications();
        const createResult = await globalThis.savePersonGroupEditor(
            duringCreate, "PG-A", draft("Idb Parent"), shown,
            duringCreate.ui.personGroupEditSession);
        assert.equal(createResult && createResult.quiet, true,
            "a parent create that loses the editor stays quiet");
        assert.equal(duringCreate.ui.personGroupEditing, false,
            "ownership changed during the parent create");
        assert.deepEqual(await parentCreates("Idb Parent"), [],
            "aborting the parent create leaves no CREATE_PERSON_GROUP");
        assert.deepEqual(await fieldWrites(), [],
            "aborting the parent create leaves no field write for Group A");
        assert.equal(runtime.notifications(), beforeNotify,
            "a rolled-back parent create does not notify sync");
        assert.deepEqual(alerts, [], "a stale parent create does not alert");

        const owned = editingContext();
        const saved = await globalThis.savePersonGroupEditor(
            owned, "PG-A", draft("Owned Parent"), shown, owned.ui.personGroupEditSession);
        assert.equal(saved && saved.ok, true, "an edit that still owns the session saves");
        const created = await parentCreates("Owned Parent");
        assert.equal(created.length, 1, "a live session still creates the typed parent");
        const fields = await fieldWrites();
        assert.equal(fields.length, 1, "a live session still writes Group A's parent");
        assert.equal(fields[0].payload.value, created[0].entity_id);
    } finally {
        globalThis.prksAlertMessage = previous.alert;
        globalThis.prksDurableOperationsOrNone = previous.ops;
        globalThis.prksAcknowledgedPersonGroupBase = previous.base;
        globalThis.prksEffectivePersonGroupCatalogue = previous.catalogue;
    }
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
    await deletingAPersonCancelsWhatWasNeverSent();
    await deletingAPersonCreatedHereFoldsThemAway();
    await aSentLinkIsWaitedForRatherThanRewritten();
    await aPendingDeletionHidesThePersonEverywhere();
    await theBaseIsAcknowledgedAndNeverGuessed();
    await anOlderSessionCannotCommitAMembershipDuringTheRead();
    await anOlderSessionCannotCommitAHalfAppliedMembershipReplacement();
    await aCommittedMembershipWriteStillNotifiesAfterTheEditorMovesOn();
    await anOlderSessionCannotCommitAGroupFieldDuringTheRead();
    await anOlderSessionCannotCommitAHalfAppliedGroupFieldReplacement();
    await aCommittedGroupFieldWriteStillNotifiesAfterTheEditorMovesOn();
    await aStaleGroupEditDoesNotCreateATypedParent();
    console.log("All " + checks + " person group checks passed");
}

main()
    .then(() => process.exit(0))
    .catch(error => { console.error(error); process.exit(1); });
