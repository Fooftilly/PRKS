"use strict";
/* Person profile editing, end to end through the store and the coordinator.
 *
 * Three things this milestone has to get right and nothing else checks
 * cheaply: the conflict unit is one FIELD, an edit to a Person created on this
 * device is ORDERED behind that creation by the generic dependency mechanism,
 * and the effective profile a user sees is the acknowledged record plus their
 * own unsynchronized intent.
 */
const strict = require('assert/strict');
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); },
    { get: (_t, k) => (...args) => { checks += 1; return strict[k](...args); } });
const { createFakeIndexedDBFactory } = require('./lib/fake_indexeddb.js');
const { createPrksLocalStore } = require('../../frontend/js/local-store.js');
require('../../frontend/js/sync-runtime.js');
require('../../frontend/js/person-state.js');
require('../../frontend/js/person-metadata-state.js');
require('../../frontend/js/work-role-state.js');

let sequence = 0;
const uuid = () => '00000000-0000-4000-8000-' + (++sequence).toString(16).padStart(12, '0');
const tick = () => new Promise(resolve => setTimeout(resolve, 1));
async function settle() { for (let i = 0; i < 12; i++) await tick(); }

const newStore = () => createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });

function baseAt(revisions, values) {
    const base = {};
    globalThis.PRKS_PERSON_METADATA_FIELDS.forEach(function (name) {
        base[name] = {
            value: (values && values[name]) || '',
            revision: (revisions && revisions[name]) || 0,
        };
    });
    return base;
}

function fieldAck(op, revision) {
    return { code: 'ACKNOWLEDGED', person_id: op.entity_id, field: op.payload.field,
        changed: true, server_revision: revision, value_omitted: true };
}
function personAck(op) {
    return { code: 'ACKNOWLEDGED', person_id: op.entity_id, changed: true,
        person: { id: op.entity_id, first_name: 'Jane', last_name: 'Doe' } };
}

function runtimeFor(store, respond) {
    const handlers = {
        CREATE_PERSON: Object.assign({}, globalThis.prksPersonSyncHandler,
            { reconcile: async () => true }),
        SET_PERSON_METADATA_FIELD: Object.assign({}, globalThis.prksPersonMetadataSyncHandler,
            { reconcile: async () => true }),
    };
    return globalThis.createPrksSyncRuntime({ store, online: () => true, handlers,
        request: respond });
}

/* ---- one field is one conflict unit ---- */
async function fieldsAreIndependent() {
    const store = newStore();
    const written = await store.savePersonMetadataFields('P-1',
        { about: 'A philosopher', birth_date: '1815-12-10' },
        baseAt({ about: 4, birth_date: 9 }));
    assert.equal(written.length, 2, 'two decisions, two operations');
    const byField = new Map(written.map(op => [op.payload.field, op]));
    /* Each carries ITS OWN base. One profile-wide revision would tell two
     * devices that changed different things that they had disagreed. */
    assert.equal(byField.get('about').base_revision, 4);
    assert.equal(byField.get('birth_date').base_revision, 9);
    assert.equal(byField.get('about').entity_type, 'person');

    // A busy scope blocks its own field and nothing else.
    await store.updateOperationSyncState(byField.get('about').op_id, { status: 'syncing' });
    await assert.rejects(
        () => store.savePersonMetadataFields('P-1', { about: 'Changed again' },
            baseAt({ about: 4 })),
        e => e.prksLocalStoreCode === 'scope_busy');
    const other = await store.savePersonMetadataFields('P-1', { birth_date: '1815-12-11' },
        baseAt({ birth_date: 9 }));
    assert.equal(other.length, 1, 'the rest of the form stays editable');
}

/* ---- two edits to one field are one intent ---- */
async function repeatedEditsCoalesce() {
    const store = newStore();
    await store.savePersonMetadataFields('P-1', { about: 'First' }, baseAt());
    await store.savePersonMetadataFields('P-1', { about: 'Second' }, baseAt());
    const rows = (await store.listOperations())
        .filter(r => r.operation === 'SET_PERSON_METADATA_FIELD');
    assert.equal(rows.length, 1, 'a never-sent row is rewritten, not stacked');
    assert.equal(rows[0].payload.value, 'Second');

    /* Edited back to what the server already holds. A -> B -> A is not two
     * changes, it is none -- and leaving an operation behind would send the
     * server a write it does not need and a revision it would advance. */
    await store.savePersonMetadataFields('P-1', { about: '' }, baseAt());
    assert.equal((await store.listOperations())
        .filter(r => r.operation === 'SET_PERSON_METADATA_FIELD').length, 0);
}

/* ---- an edit to a Person created here waits for that creation ---- */
async function editsFollowCreation() {
    const store = newStore();
    const created = await store.createPerson({ first_name: 'Jane', last_name: 'Doe' });
    /* The creation is still in flight, so the server has never heard of this
     * Person: every field IS at revision 0 -- known, not assumed. */
    const [edit] = await store.savePersonMetadataFields(created.entity_id,
        { about: 'Edited before it ever synchronized' }, baseAt());
    assert.deepEqual(edit.depends_on, [created.op_id],
        'ordered behind the creation by the GENERIC mechanism, not a hidden rule');
    /* The creation's payload is untouched. Folding the edit into it would
     * rewrite an envelope that may already be on the wire -- the one way to
     * apply it twice -- and would merge two decisions the user made
     * separately into one. */
    const stored = (await store.listOperations()).find(r => r.op_id === created.op_id);
    assert.equal(stored.payload.about, '');

    const sent = [];
    const runtime = runtimeFor(store, async (_path, options) => {
        const body = JSON.parse(options.body);
        sent.push(body.operation);
        return { ok: true, status: 200, json: async () =>
            (body.operation === 'CREATE_PERSON' ? personAck(body) : fieldAck(body, 1)) };
    });
    await runtime.wake();
    await settle();
    runtime.stop();
    assert.deepEqual(sent, ['CREATE_PERSON', 'SET_PERSON_METADATA_FIELD'],
        'the Person exists on the server before anything edits them');
    assert.equal((await store.listOperations()).length, 0, 'and both retire');
}

/* ---- a creation the server refused takes its edits down visibly ---- */
async function aRefusedCreationBlocksItsEdits() {
    const store = newStore();
    const created = await store.createPerson({ first_name: 'Jane', last_name: 'Doe' });
    const [edit] = await store.savePersonMetadataFields(created.entity_id,
        { about: 'Never reaches the server' }, baseAt());

    const sent = [];
    const runtime = runtimeFor(store, async (_path, options) => {
        const body = JSON.parse(options.body);
        sent.push(body.operation);
        return { ok: false, status: 400, json: async () => ({ code: 'INVALID_ENVELOPE' }) };
    });
    await runtime.wake();
    await settle();
    runtime.stop();
    assert.deepEqual(sent, ['CREATE_PERSON'], 'the edit is never attempted');
    const row = (await store.listOperations()).find(r => r.op_id === edit.op_id);
    assert.equal(row.status, 'conflict', 'and becomes a decision, not a permanent wait');
    assert.equal(row.server_result.code, 'DEPENDENCY_FAILED');

    /* And no NEW edit may be enqueued against that creation either: it would
     * be born unsendable. Refused in the terms the user was working in. */
    await assert.rejects(
        () => store.savePersonMetadataFields(created.entity_id, { about: 'Another try' },
            baseAt()),
        e => e.prksLocalStoreCode === 'dependency_failed' && /person/i.test(e.message));
}

/* ---- the effective profile: acknowledged plus unsynchronized intent ---- */
async function effectiveProfileComposes() {
    const store = newStore();
    await store.savePersonMetadataFields('P-1',
        { about: 'Pending biography' }, baseAt({}, { about: 'Server biography' }));
    await store.savePersonMetadataFields('P-1',
        { birth_date: '1815-12-10' }, baseAt());
    const ops = await store.listOperations();

    const cached = { id: 'P-1', first_name: 'Ada', last_name: 'Lovelace',
        about: 'Server biography', birth_date: '' };
    const effective = globalThis.prksEffectivePersonFields(cached, ops);
    assert.equal(effective.about, 'Pending biography');
    assert.equal(effective.birth_date, '1815-12-10', 'two fields compose');
    assert.equal(effective.last_name, 'Lovelace', 'and nothing else is touched');
    assert.equal(cached.about, 'Server biography',
        'the acknowledged record is never written through');

    // The same overlay reaches the catalogue rows the People index renders.
    const rows = globalThis.prksEffectivePersonRows(
        [{ id: 'P-1', about: 'Server biography' }, { id: 'P-2', about: 'Untouched' }], ops);
    assert.equal(rows[0].about, 'Pending biography');
    assert.equal(rows[1].about, 'Untouched');
}

/* ---- a rename reaches rows that only DISPLAY the name ---- */
async function pendingRenamesReachCreditRows() {
    const store = newStore();
    await store.savePersonMetadataFields('P-1',
        { last_name: 'Byron', about: 'Not a display field' }, baseAt());
    globalThis.prksSetPendingPersonNames(await store.listOperations());

    const roles = [
        { person_id: 'P-1', role_type: 'Author', first_name: 'Ada', last_name: 'Lovelace',
          about: 'ignored here' },
        { person_id: 'P-2', role_type: 'Editor', first_name: 'Grace', last_name: 'Hopper' },
    ];
    const patched = globalThis.prksApplyPendingPersonNames(roles);
    assert.equal(patched[0].last_name, 'Byron', 'the credit row shows the pending name');
    assert.equal(patched[0].first_name, 'Ada', 'and keeps what did not change');
    /* Only the NAME travels to these rows. A Work card, a Graph label and a
     * cached Argument source display nothing else from a profile, and copying
     * a biography into them would be inventing a read model. */
    assert.equal(patched[0].about, 'ignored here');
    assert.equal(patched[1].last_name, 'Hopper', 'other people are untouched');
    /* Rows nobody renamed come back as the SAME array. The overlay runs on
     * every card render, and allocating a new one each time would make every
     * consumer's identity check report a change that did not happen. */
    const untouched = [{ person_id: 'P-9', last_name: 'Hopper' }];
    assert.equal(globalThis.prksApplyPendingPersonNames(untouched), untouched);
}

/* ---- a rename is independent of whether anything was linked offline ---- */
async function renamesApplyWithNoPendingLinks() {
    const store = newStore();
    await store.savePersonMetadataFields('P-1', { last_name: 'Byron' }, baseAt());
    globalThis.prksSetPendingPersonNames(await store.listOperations());
    /* No relationship intents at all -- the ordinary case for a rename. The
     * Work-detail overlay used to return the Work untouched whenever its
     * pending-link map was empty, which skipped the name overlay entirely and
     * left every Work still crediting the OLD name. That surface has no other
     * source for a pending rename: the acknowledgement path invalidates those
     * rows rather than patching them, because they are keyed by Work. */
    globalThis.prksSetPendingWorkRoles([]);
    const work = { id: 'W-1', roles: [
        { person_id: 'P-1', role_type: 'Author', first_name: 'Ada', last_name: 'Lovelace' },
    ] };
    const effective = globalThis.prksEffectiveWorkDetailRoles(work);
    assert.equal(effective.roles[0].last_name, 'Byron');
    assert.equal(work.roles[0].last_name, 'Lovelace', 'and the cached Work is untouched');
}

/* ---- the observed base, and what makes one unknowable ---- */
async function observedBaseIsValueAndRevision() {
    const person = { id: 'P-1', about: 'Server biography', last_name: 'Lovelace' };
    const state = { person_id: 'P-1', fields: { about: { revision: 7 } } };
    const base = globalThis.prksObservedPersonFields(person, state);
    assert.equal(base.about.value, 'Server biography', 'the value comes from the record');
    assert.equal(base.about.revision, 7, 'the revision from the state projection');
    assert.equal(base.last_name.value, 'Lovelace');
    assert.equal(base.last_name.revision, 0, 'a field never changed is at revision 0');
    assert.equal(base.birth_date.value, '', 'and an absent value is empty, not undefined');

    /* The state projection is revisions ONLY. The Person record already
     * carries all eleven values and none of them is bounded, so echoing them
     * would make a second copy of the whole profile. */
    assert.equal(globalThis.prksIsPersonMetadataStateShape(
        globalThis.prksNewPersonMetadataState('P-1'), 'P-1'), true);
    assert.equal(globalThis.prksIsPersonMetadataStateShape(
        { person_id: 'P-1', fields: { about: { revision: 0 } } }, 'P-1'), false,
        'a projection missing a field is not a usable base');
}

/* ---- an unknown field never reaches the queue ---- */
async function onlyProfileFieldsAreWritable() {
    const store = newStore();
    for (const field of ['id', 'created_at', 'groups', '']) {
        await assert.rejects(
            () => store.savePersonMetadataFields('P-1', { [field]: 'x' }, baseAt()),
            e => e.prksLocalStoreCode === 'unknown_field', field);
    }
}

async function main() {
    await fieldsAreIndependent();
    await repeatedEditsCoalesce();
    await editsFollowCreation();
    await aRefusedCreationBlocksItsEdits();
    await effectiveProfileComposes();
    await pendingRenamesReachCreditRows();
    await renamesApplyWithNoPendingLinks();
    await observedBaseIsValueAndRevision();
    await onlyProfileFieldsAreWritable();
    console.log('All ' + checks + ' person profile checks passed');
}

main()
    .then(() => process.exit(0))
    .catch(error => { console.error(error); process.exit(1); });
