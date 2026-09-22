"use strict";
const strict = require('assert/strict');
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); },
    { get: (_t, k) => (...args) => { checks += 1; return strict[k](...args); } });
const { createFakeIndexedDBFactory } = require('./lib/fake_indexeddb.js');
const { createPrksLocalStore } = require('../../frontend/js/local-store.js');
const { createPrksOfflineStore } = require('../../frontend/js/offline-store.js');
const { createPrksOfflineRuntime } = require('../../frontend/js/offline-runtime.js');
require('../../frontend/js/work-role-state.js');
require('../../frontend/js/work-metadata-state.js');
require('../../frontend/js/person-metadata-state.js');
/* The REAL credit helper, loaded from the shipped card module. It is a browser
 * script that assigns to `window` at load, so the global is provided rather
 * than the precedence rule being restated here -- a test that reimplemented
 * the rule could not catch the overlay feeding it the wrong input. */
globalThis.window = globalThis;
(0, eval)(require('fs').readFileSync(
    require('path').join(__dirname, '../../frontend/js/components/work-cards.js'), 'utf8'));
const creditLine = globalThis.prksWorkCardCreditLine;
strict.equal(typeof creditLine, 'function', 'the shipped credit helper must be loadable');

let sequence = 0;
const uuid = () => '00000000-0000-4000-8000-' + (++sequence).toString(16).padStart(12, '0');

const JANE = 'P-JANE', ED = 'P-ED';
const link = (person, role, canonical, credit) => ({
    person_id: person, role_type: role, order_index: 0,
    canonical_name: canonical, credit_name: credit || '',
    display_name: credit || canonical,
});
const op = (workId, operation, person, role, credit, extra) => Object.assign({
    operation, entity_type: 'work', entity_id: workId, status: 'pending',
    payload: Object.assign({ person_id: person, role_type: role },
        operation === 'REMOVE_WORK_PERSON_ROLE' ? {} : { credit_name: credit || '' }),
    local_context: { person: { canonical_name: person === JANE ? 'Jane Doe' : 'Ed Smith' } },
}, extra || {});

/* ---- the effective relationship overlay ---- */
function overlay() {
    const work = {
        id: 'W-1', author_text: 'Text Author',
        linked_people: [link(JANE, 'Author', 'Jane Doe')],
        linked_authors: 'Jane Doe', primary_author: 'Jane Doe', primary_editor: '',
    };
    const frozen = JSON.stringify(work);

    // A pending REMOVE drops exactly that element.
    globalThis.prksSetPendingWorkRoles([op('W-1', 'REMOVE_WORK_PERSON_ROLE', JANE, 'Author')]);
    let out = globalThis.prksEffectiveWorkRoles(work);
    assert.deepEqual(out.linked_people, []);
    assert.equal(out.linked_authors, '');
    assert.equal(out.primary_author, '');
    assert.equal(JSON.stringify(work), frozen, 'the acknowledged row is never mutated');

    // A pending ADD appends, the way the server appends.
    globalThis.prksSetPendingWorkRoles([op('W-1', 'ADD_WORK_PERSON_ROLE', ED, 'Author')]);
    out = globalThis.prksEffectiveWorkRoles(work);
    assert.equal(out.linked_authors, 'Jane Doe, Ed Smith');
    assert.equal(out.primary_author, 'Jane Doe', 'an addition never reorders what is there');

    /* A pending credit edit replaces the override and the resolved name, and
     * leaves the canonical one -- so CLEARING it can still reveal the Person's
     * own name, which "Mark Twain" alone could never be turned back into. */
    globalThis.prksSetPendingWorkRoles(
        [op('W-1', 'SET_WORK_PERSON_ROLE_CREDIT', JANE, 'Author', 'J. D.')]);
    out = globalThis.prksEffectiveWorkRoles(work);
    assert.equal(out.linked_people[0].credit_name, 'J. D.');
    assert.equal(out.linked_people[0].display_name, 'J. D.');
    assert.equal(out.linked_people[0].canonical_name, 'Jane Doe');
    assert.equal(out.linked_authors, 'J. D.');

    globalThis.prksSetPendingWorkRoles(
        [op('W-1', 'SET_WORK_PERSON_ROLE_CREDIT', JANE, 'Author', '')]);
    out = globalThis.prksEffectiveWorkRoles(work);
    assert.equal(out.linked_authors, 'Jane Doe', 'clearing reveals the canonical name');

    // A row that does not carry the structured links is returned untouched:
    // inventing columns there would fail its own shape validator.
    globalThis.prksSetPendingWorkRoles([op('W-1', 'ADD_WORK_PERSON_ROLE', ED, 'Author')]);
    const flat = { id: 'W-1', linked_authors: 'Jane Doe' };
    assert.equal(globalThis.prksEffectiveWorkRoles(flat), flat);

    // Other Works are untouched.
    const other = { id: 'W-2', linked_people: [], linked_authors: '' };
    assert.equal(globalThis.prksEffectiveWorkRoles(other), other);
    globalThis.prksSetPendingWorkRoles([]);
}

/* ---- credit composition, through the REAL helper ---- */
function composition() {
    const credit = w => creditLine(globalThis.prksEffectiveWorkMetadata(
        globalThis.prksEffectiveWorkRoles(w), []));
    const base = extra => Object.assign({
        id: 'W-1', linked_people: [], linked_authors: '', primary_author: '',
        primary_editor: '', author_text: '',
    }, extra);

    /* The precedence rule stays where it already lives. These assert the
     * COMPOSITION, not a restatement of the algorithm: the overlay feeds the
     * shipped credit helper and the helper decides. */
    globalThis.prksSetPendingWorkRoles([op('W-1', 'ADD_WORK_PERSON_ROLE', JANE, 'Author')]);
    assert.equal(credit(base({ author_text: 'Text Author' })), 'Author: Jane Doe',
        'a pending Author outranks author_text at once');

    const linked = base({
        author_text: 'Text Author', linked_people: [link(JANE, 'Author', 'Jane Doe')],
        linked_authors: 'Jane Doe', primary_author: 'Jane Doe',
    });
    globalThis.prksSetPendingWorkRoles([op('W-1', 'REMOVE_WORK_PERSON_ROLE', JANE, 'Author')]);
    assert.equal(credit(linked), 'Author: Text Author',
        'removing the last Author reveals author_text');

    const withEditor = base({
        linked_people: [link(JANE, 'Author', 'Jane Doe'), link(ED, 'Editor', 'Ed Smith')],
        linked_authors: 'Jane Doe', primary_author: 'Jane Doe', primary_editor: 'Ed Smith',
    });
    assert.equal(credit(withEditor), 'Editor: Ed Smith',
        'with author_text empty, the Editor is the fallback');

    globalThis.prksSetPendingWorkRoles([]);
    assert.equal(credit(base({ author_text: 'Text Author', primary_editor: 'Ed Smith',
        linked_people: [link(ED, 'Editor', 'Ed Smith')] })), 'Author: Text Author',
        'author_text still outranks a linked Editor');
}

/* ---- pending roles and pending author_text compose ---- */
function crossFamily() {
    const work = {
        id: 'W-1', author_text: 'Old',
        linked_people: [link(JANE, 'Author', 'Jane Doe')],
        linked_authors: 'Jane Doe', primary_author: 'Jane Doe', primary_editor: '',
    };
    globalThis.prksSetPendingWorkRoles([op('W-1', 'REMOVE_WORK_PERSON_ROLE', JANE, 'Author')]);
    const metadataOps = [{
        operation: 'SET_WORK_METADATA_FIELD', entity_type: 'work', entity_id: 'W-1',
        status: 'pending', payload: { field: 'author_text', value: 'New' },
    }];
    /* Two families, one displayed credit. Composition has to use the effective
     * state of BOTH -- the relationship overlay decides there is no linked
     * Author left, and the metadata overlay decides what author_text now is. */
    const effective = globalThis.prksEffectiveWorkMetadata(
        globalThis.prksEffectiveWorkRoles(work), metadataOps);
    assert.equal(creditLine(effective), 'Author: New');
    globalThis.prksSetPendingWorkRoles([]);
}

/* ---- the Person direction ---- */
function personMembership() {
    const work = { id: 'W-1', linked_people: [link(JANE, 'Author', 'Jane Doe')] };
    globalThis.prksSetPendingWorkRoles([op('W-1', 'ADD_WORK_PERSON_ROLE', ED, 'Editor')]);
    assert.deepEqual(globalThis.prksEffectivePersonRoles(work, ED), ['Editor'],
        'a pending link makes the Work appear under that Person');
    assert.deepEqual(globalThis.prksEffectivePersonRoles(work, JANE), ['Author']);
    globalThis.prksSetPendingWorkRoles([op('W-1', 'REMOVE_WORK_PERSON_ROLE', JANE, 'Author')]);
    assert.deepEqual(globalThis.prksEffectivePersonRoles(work, JANE), [],
        'and a pending unlink hides it');
    globalThis.prksSetPendingWorkRoles([]);
}

/* ---- the Work DETAIL panel's own shape ---- */
function detailRoles() {
    const work = { id: 'W-1', roles: [
        { id: 'P-JANE', role_type: 'Author', order_index: 0, credit_name: '',
          first_name: 'Jane', last_name: 'Doe' },
    ] };
    const frozen = JSON.stringify(work);
    globalThis.prksSetPendingWorkRoles([op('W-1', 'REMOVE_WORK_PERSON_ROLE', JANE, 'Author')]);
    assert.deepEqual(globalThis.prksEffectiveWorkDetailRoles(work).roles, []);
    assert.equal(JSON.stringify(work), frozen, 'the acknowledged entity is never mutated');

    /* A pending ADD renders from the Person row captured WITH the intent --
     * the detail panel draws profile links and alias tooltips from it, and a
     * Person cache may simply not be present. */
    const withPerson = op('W-1', 'ADD_WORK_PERSON_ROLE', ED, 'Editor');
    withPerson.local_context = { person: { id: ED, first_name: 'Ed', last_name: 'Smith',
        canonical_name: 'Ed Smith' } };
    globalThis.prksSetPendingWorkRoles([withPerson]);
    const out = globalThis.prksEffectiveWorkDetailRoles(work).roles;
    assert.equal(out.length, 2);
    assert.equal(out[1].id, ED);
    assert.equal(out[1].role_type, 'Editor');
    assert.equal(out[1].last_name, 'Smith');

    // Without that context the optimistic row is withheld rather than blank.
    const contextless = op('W-1', 'ADD_WORK_PERSON_ROLE', ED, 'Editor');
    contextless.local_context = null;
    globalThis.prksSetPendingWorkRoles([contextless]);
    assert.equal(globalThis.prksEffectiveWorkDetailRoles(work).roles.length, 1);
    globalThis.prksSetPendingWorkRoles([]);
}

/* ---- the Person's own page ---- */
function personPage() {
    const person = { id: JANE, works: [{ id: 'W-1', title: 'Old paper' }] };
    const summary = { id: 'W-2', title: 'New paper' };

    // A pending link makes the Work appear on that Person's page at once.
    const adding = op('W-2', 'ADD_WORK_PERSON_ROLE', JANE, 'Author');
    adding.local_context = { person: { canonical_name: 'Jane Doe' }, work: summary };
    globalThis.prksSetPendingWorkRoles([adding]);
    let works = globalThis.prksEffectivePersonWorks(person, person.works);
    assert.deepEqual(works.map(w => w.id), ['W-1', 'W-2']);

    // Without a captured summary it is not invented.
    const bare = op('W-2', 'ADD_WORK_PERSON_ROLE', JANE, 'Author');
    bare.local_context = { person: { canonical_name: 'Jane Doe' } };
    globalThis.prksSetPendingWorkRoles([bare]);
    assert.deepEqual(globalThis.prksEffectivePersonWorks(person, person.works)
        .map(w => w.id), ['W-1']);

    // A pending unlink hides it.
    globalThis.prksSetPendingWorkRoles([op('W-1', 'REMOVE_WORK_PERSON_ROLE', JANE, 'Author')]);
    assert.deepEqual(globalThis.prksEffectivePersonWorks(person, person.works), []);

    /* ... but only when NO effective role of this Person remains on it.
     * Unlinking an Author from a file they also translated must not remove the
     * file from their page. */
    const alsoTranslator = op('W-1', 'ADD_WORK_PERSON_ROLE', JANE, 'Translator');
    alsoTranslator.local_context = { person: { canonical_name: 'Jane Doe' } };
    globalThis.prksSetPendingWorkRoles([
        op('W-1', 'REMOVE_WORK_PERSON_ROLE', JANE, 'Author'), alsoTranslator]);
    assert.deepEqual(globalThis.prksEffectivePersonWorks(person, person.works)
        .map(w => w.id), ['W-1']);

    // Another Person's page is untouched.
    globalThis.prksSetPendingWorkRoles([op('W-1', 'REMOVE_WORK_PERSON_ROLE', JANE, 'Author')]);
    assert.deepEqual(globalThis.prksEffectivePersonWorks({ id: ED, works: person.works },
        person.works).map(w => w.id), ['W-1']);
    globalThis.prksSetPendingWorkRoles([]);
}

/* ---- the Research Graph ---- */
function graphOverlay() {
    const snapshot = () => ({
        meta: { people_included: true, node_count: 2, edge_count: 0 },
        nodes: [
            { id: 'work:W-1', record_id: 'W-1', type: 'work', label: 'Paper', route: '#/works/W-1' },
            { id: 'person:P-ED', record_id: 'P-ED', type: 'person', label: 'Ed Smith',
              route: '#/people/P-ED' },
        ],
        edges: [],
    });
    const withPerson = op('W-1', 'ADD_WORK_PERSON_ROLE', ED, 'Author');
    withPerson.local_context = { person: { first_name: 'Ed', last_name: 'Smith' } };

    // A pending Author link draws its edge against the node already present.
    globalThis.prksSetPendingWorkRoles([withPerson]);
    let out = globalThis.prksEffectiveResearchGraphRoles(snapshot());
    assert.equal(out.edges.length, 1);
    assert.equal(out.edges[0].id, 'work_author:person:P-ED>work:W-1');
    assert.equal(out.edges[0].type, 'work_author');
    assert.equal(out.meta.edge_count, 1, 'meta counts stay honest');

    /* Only the AUTHOR role produces an edge: the people layer indexes that role
     * alone, so a pending Editor link drawing one would show a relationship the
     * Graph does not represent. */
    const editor = op('W-1', 'ADD_WORK_PERSON_ROLE', ED, 'Editor');
    editor.local_context = withPerson.local_context;
    globalThis.prksSetPendingWorkRoles([editor]);
    assert.equal(globalThis.prksEffectiveResearchGraphRoles(snapshot()).edges.length, 0);

    // A snapshot without the people layer is never touched.
    const core = snapshot(); core.meta.people_included = false;
    globalThis.prksSetPendingWorkRoles([withPerson]);
    assert.equal(globalThis.prksEffectiveResearchGraphRoles(core), core);

    /* A Person the snapshot does not hold is CONSTRUCTED from the captured
     * name parts -- the same node the server emits, not an approximation. */
    const jane = op('W-1', 'ADD_WORK_PERSON_ROLE', JANE, 'Author');
    jane.local_context = { person: { first_name: 'Jane', last_name: 'Doe' } };
    globalThis.prksSetPendingWorkRoles([jane]);
    out = globalThis.prksEffectiveResearchGraphRoles(snapshot());
    const node = out.nodes.find(n => n.id === 'person:P-JANE');
    assert.deepEqual(node, { id: 'person:P-JANE', record_id: 'P-JANE', type: 'person',
        label: 'Jane Doe', route: '#/people/P-JANE' });
    assert.equal(out.edges.length, 1);

    /* Without name parts there is nothing EXACT to draw. The intent is still
     * durable and still synchronizes; only its optimistic edge is withheld --
     * a placeholder label would be a visible lie that corrected itself on
     * acknowledgement. */
    const nameless = op('W-1', 'ADD_WORK_PERSON_ROLE', JANE, 'Author');
    nameless.local_context = null;
    globalThis.prksSetPendingWorkRoles([nameless]);
    out = globalThis.prksEffectiveResearchGraphRoles(snapshot());
    assert.equal(out.edges.length, 0);
    assert.equal(out.nodes.length, 2);

    // A Work the snapshot does not hold has no edge to draw either.
    const elsewhere = op('W-OTHER', 'ADD_WORK_PERSON_ROLE', ED, 'Author');
    elsewhere.local_context = withPerson.local_context;
    globalThis.prksSetPendingWorkRoles([elsewhere]);
    assert.equal(globalThis.prksEffectiveResearchGraphRoles(snapshot()).edges.length, 0);

    // A pending unlink hides an acknowledged edge.
    const linked = snapshot();
    linked.edges.push({ id: 'work_author:person:P-ED>work:W-1', type: 'work_author',
        source: 'person:P-ED', target: 'work:W-1' });
    globalThis.prksSetPendingWorkRoles([op('W-1', 'REMOVE_WORK_PERSON_ROLE', ED, 'Author')]);
    assert.equal(globalThis.prksEffectiveResearchGraphRoles(linked).edges.length, 0);
    globalThis.prksSetPendingWorkRoles([]);
}

/* ---- the acknowledgement patches the cached Graph ---- */
async function graphReconciliation() {
    const cache = createPrksOfflineStore({ indexedDB: createFakeIndexedDBFactory() });
    const snapshot = {
        meta: { people_included: true, node_count: 2, edge_count: 0 },
        nodes: [
            { id: 'work:W-1', record_id: 'W-1', type: 'work', label: 'Paper', route: '#/works/W-1' },
            { id: 'person:P-ED', record_id: 'P-ED', type: 'person', label: 'Ed Smith',
              route: '#/people/P-ED' },
        ],
        edges: [],
    };
    await cache.putEntity('research-graph-people', 'snapshot',
        JSON.parse(JSON.stringify(snapshot)));
    const offline = createPrksOfflineRuntime({ store: cache, window: null,
        prksRequest: async () => { throw new Error('no reads'); } });

    assert.equal(await offline.reconcileWorkRole({ work_id: 'W-1', person_id: ED,
        role_type: 'Author', present: true, credit_name: '', server_revision: 1 }), true);
    let edges = (await cache.getEntity('research-graph-people', 'snapshot')).value.edges;
    assert.equal(edges.length, 1);
    assert.equal(edges[0].id, 'work_author:person:P-ED>work:W-1');

    // ... and removing it takes the edge away again.
    assert.equal(await offline.reconcileWorkRole({ work_id: 'W-1', person_id: ED,
        role_type: 'Author', present: false, credit_name: '', server_revision: 2 }), true);
    edges = (await cache.getEntity('research-graph-people', 'snapshot')).value.edges;
    assert.equal(edges.length, 0);

    /* An UNREADABLE Graph cache is not "nothing to patch". Retiring there
     * would leave the snapshot claiming a relationship the server no longer
     * has, with no operation left to correct it. */
    const unreadable = Object.assign(Object.create(Object.getPrototypeOf(cache)), cache, {
        getEntity: async (kind, id) => {
            if (kind === 'research-graph-people') throw new Error('unreadable');
            return cache.getEntity(kind, id);
        },
    });
    const blocked = createPrksOfflineRuntime({ store: unreadable, window: null,
        prksRequest: async () => { throw new Error('no reads'); } });
    assert.equal(await blocked.reconcileWorkRole({ work_id: 'W-1', person_id: ED,
        role_type: 'Author', present: true, credit_name: '', server_revision: 4 }), false);

    /* A role that produces no edge leaves the snapshot alone entirely, and a
     * Person the snapshot does not hold cannot gain an exact node from an
     * acknowledgement -- so that is left for the next read. */
    for (const result of [
        { person_id: ED, role_type: 'Editor', present: true },
        { person_id: 'P-UNKNOWN', role_type: 'Author', present: true },
    ]) {
        assert.equal(await offline.reconcileWorkRole(Object.assign({ work_id: 'W-1',
            credit_name: '', server_revision: 3 }, result)), true);
        assert.equal((await cache.getEntity('research-graph-people', 'snapshot'))
            .value.edges.length, 0);
    }
}

/* ---- one active intent per element ---- */
async function coalescing() {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    const rows = async () => (await store.listOperations())
        .filter(r => globalThis.PRKS_WORK_ROLE_OPERATION_TYPES.indexOf(r.operation) !== -1);
    const save = (state, observed) => store.saveWorkPersonRole('W-1',
        { person_id: JANE, role_type: 'Author', state },
        { state: observed.state, revision: observed.revision },
        { person: { canonical_name: 'Jane Doe' } });

    // absent -> ADD -> REMOVE is not two changes; it is none.
    const absent = { state: null, revision: 0 };
    await save('', absent);
    assert.equal((await rows())[0].operation, 'ADD_WORK_PERSON_ROLE');
    await save(null, absent);
    assert.equal((await rows()).length, 0, 'returning to the base leaves no intent');

    // present -> REMOVE -> ADD likewise.
    const present = { state: '', revision: 3 };
    await save(null, present);
    assert.equal((await rows())[0].operation, 'REMOVE_WORK_PERSON_ROLE');
    await save('', present);
    assert.equal((await rows()).length, 0);

    /* Changing one's mind about a credit twice is ONE operation naming the
     * last choice -- and it is a CREDIT operation, because the element was
     * already present. An ADD that silently edited an existing link would be
     * harder to reason about on replay. */
    const credited = { state: 'Mark Twain', revision: 5 };
    await save('Samuel Clemens', credited);
    await save('', credited);
    let list = await rows();
    assert.equal(list.length, 1);
    assert.equal(list[0].operation, 'SET_WORK_PERSON_ROLE_CREDIT');
    assert.equal(list[0].payload.credit_name, '');
    assert.equal(list[0].base_revision, 5, 'still measured against the acknowledged base');

    // The same desired state keeps the row rather than minting a second op_id.
    const before = list[0].op_id;
    await save('', credited);
    list = await rows();
    assert.equal(list.length, 1);
    assert.equal(list[0].op_id, before);

    // A POSSIBLY SENT row is immutable: it may already be ledgered.
    await store.claimOperation(before);
    await assert.rejects(() => save('Anything', credited),
        e => e.prksLocalStoreCode === 'scope_busy');
    assert.equal((await rows())[0].payload.credit_name, '');

    // Elements are independent: another role on the same Person is its own scope.
    await store.saveWorkPersonRole('W-1',
        { person_id: JANE, role_type: 'Translator', state: '' },
        { state: null, revision: 0 }, null);
    assert.equal((await rows()).length, 2);
}

/* ---- a link to a Person this device has not synchronized yet ---- */
async function creationDependency() {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    const created = await store.createPerson({ first_name: 'Jane', last_name: 'Doe' });

    /* The role must WAIT for the creation. Without the dependency the
     * coordinator is free to send the link first, and the server refuses a
     * relationship to a Person it has never heard of -- so the user's link is
     * lost for a reason they cannot see or fix.
     *
     * This is the case the first implementation could never produce: the
     * prerequisite was searched for inside an array already filtered to the
     * three role operation types, so `depends_on` was silently always empty. */
    const role = await store.saveWorkPersonRole('W-1',
        { person_id: created.entity_id, role_type: 'Author', state: '' },
        { state: null, revision: 0 }, null);
    assert.deepEqual(role.depends_on, [created.op_id],
        'the link depends on the creation that gives its Person an identity');

    // A Person the server already knows needs no prerequisite at all.
    const existing = await store.saveWorkPersonRole('W-1',
        { person_id: 'P-' + 'a'.repeat(32), role_type: 'Editor', state: '' },
        { state: null, revision: 0 }, null);
    assert.deepEqual(existing.depends_on, [],
        'an ordinary link carries no dependency to wait on');

    /* An ACKNOWLEDGED creation is no longer a prerequisite: the server holds
     * that Person, so a later link stands on its own. Keeping the dependency
     * would pin a retired row in the store forever. */
    await store.updateOperationSyncState(created.op_id, { status: 'acknowledged' });
    const later = await store.saveWorkPersonRole('W-2',
        { person_id: created.entity_id, role_type: 'Author', state: '' },
        { state: null, revision: 0 }, null);
    assert.deepEqual(later.depends_on, []);
}

/* ---- the handler's result contract ---- */
function handlerContract() {
    const handler = globalThis.prksWorkRoleSyncHandler;
    const operation = op('W-1', 'ADD_WORK_PERSON_ROLE', JANE, 'Author', 'Mark Twain');
    /* The acknowledgement states the Person's own name too: a cached Work
     * detail that does not yet hold this link cannot build its row without it,
     * and the alternative is a blank chip or discarding the whole cached Work. */
    const ack = { work_id: 'W-1', person_id: JANE, role_type: 'Author',
        code: 'ACKNOWLEDGED', changed: true, server_revision: 2,
        present: true, credit_name: 'Mark Twain',
        first_name: 'Jane', last_name: 'Doe' };
    assert.equal(handler.isResult(ack, operation), true);
    assert.equal(handler.isResult(Object.assign({}, ack, { aliases_revision: 1 }), operation), true,
        'optional aliases_revision from credit promotion');
    assert.equal(handler.isResult(Object.assign({}, ack, { aliases_revision: -1 }), operation), false);
    for (const wrong of [{ work_id: 'W-2' }, { person_id: 'P-X' }, { role_type: 'Editor' },
        { present: 'yes' }, { credit_name: null }, { server_revision: -1 },
        { first_name: null }, { last_name: 7 }]) {
        assert.equal(handler.isResult(Object.assign({}, ack, wrong), operation), false,
            JSON.stringify(wrong));
    }
    const conflict = { work_id: 'W-1', person_id: JANE, role_type: 'Author',
        code: 'REVISION_CONFLICT', current_revision: 4,
        current: { present: true, credit_name: 'Mark Twain' },
        requested: { present: false, credit_name: '' } };
    assert.equal(handler.isResult(conflict, operation), true);
    assert.equal(handler.isResult(Object.assign({}, conflict, { current: true }), operation), false,
        'presence alone was never the state');
    for (const code of ['ENTITY_NOT_FOUND', 'PERSON_NOT_FOUND']) {
        assert.equal(handler.isResult(
            { work_id: 'W-1', person_id: JANE, role_type: 'Author', code }, operation), true, code);
    }
    assert.equal(handler.isResult({ work_id: 'W-1', person_id: JANE,
        role_type: 'Author', code: 'SOMETHING' }, operation), false);
    assert.equal(handler.terminal(conflict).conflict.current_value, 'Mark Twain');
}

/* ---- acknowledgement reaches every cached representation ---- */
async function reconciliation() {
    const cache = createPrksOfflineStore({ indexedDB: createFakeIndexedDBFactory() });
    const row = () => ({ id: 'W-1', title: 'Paper',
        linked_people: [link(JANE, 'Author', 'Jane Doe')],
        linked_authors: 'Jane Doe', primary_author: 'Jane Doe', primary_editor: '' });
    await cache.putEntity('work', 'W-1', row());
    await cache.putList('works-browse:index', [row(), { id: 'W-2', linked_people: [] }], '');
    await cache.putList('recent:index', [row()], '');
    await cache.putEntity('folder', 'F1', { id: 'F1', works: [row()] });
    await cache.putEntity('playlist', 'PL1', { id: 'PL1', items: [row()] });
    await cache.putEntity('work-people-state', 'W-1',
        { work_id: 'W-1', scopes: [{ person_id: JANE, role_type: 'Author',
            revision: 1, present: true }] });
    const offline = createPrksOfflineRuntime({ store: cache, window: null,
        prksRequest: async () => { throw new Error('no reads in this scenario'); } });

    assert.equal(await offline.reconcileWorkRole({ work_id: 'W-1', person_id: ED,
        role_type: 'Editor', present: true, credit_name: '', server_revision: 1,
        canonical_name: 'Ed Smith', first_name: 'Ed', last_name: 'Smith' }), true);

    const coherent = r => {
        assert.equal(r.linked_people.length, 2);
        assert.equal(r.primary_editor, 'Ed Smith');
        assert.equal(r.linked_authors, 'Jane Doe',
            'the flattened columns are DERIVED, so a row cannot disagree with itself');
    };
    coherent((await cache.getEntity('work', 'W-1')).value);
    coherent((await cache.getList('works-browse:index')).value.find(r => r.id === 'W-1'));
    coherent((await cache.getList('recent:index')).value[0]);
    coherent((await cache.getEntity('folder', 'F1')).value.works[0]);
    coherent((await cache.getEntity('playlist', 'PL1')).value.items[0]);
    assert.equal((await cache.getList('works-browse:index')).value
        .find(r => r.id === 'W-2').linked_people.length, 0, 'other Works are untouched');

    /* The base the NEXT edit is measured against. Left behind, a second change
     * is created against a revision the server has already passed -- so the
     * user conflicts with their own previous acknowledgement. */
    const state = (await cache.getEntity('work-people-state', 'W-1')).value;
    const scope = state.scopes.find(s => s.person_id === ED);
    assert.deepEqual(scope, { person_id: ED, role_type: 'Editor', revision: 1, present: true });

    // A removal drops the element and re-derives the columns.
    await offline.reconcileWorkRole({ work_id: 'W-1', person_id: JANE, role_type: 'Author',
        present: false, credit_name: '', server_revision: 2 });
    const after = (await cache.getEntity('work', 'W-1')).value;
    assert.equal(after.linked_authors, '');
    assert.equal(after.primary_author, '');
    assert.equal(after.linked_people.length, 1);

    /* An UNREADABLE cache is not "nothing to patch": the operation must replay
     * rather than retire believing it patched what it could not read. */
    const unreadable = Object.assign(Object.create(Object.getPrototypeOf(cache)), cache, {
        getEntitiesByKind: async () => null,
    });
    const blocked = createPrksOfflineRuntime({ store: unreadable, window: null,
        prksRequest: async () => { throw new Error('no reads'); } });
    assert.equal(await blocked.reconcileWorkRole({ work_id: 'W-1', person_id: ED,
        role_type: 'Reviewer', present: true, credit_name: '', server_revision: 3 }), false);
}

/* ---- credit promotion patches person-metadata-state aliases revision ---- */
async function aliasesRevisionReconciliation() {
    const cache = createPrksOfflineStore({ indexedDB: createFakeIndexedDBFactory() });
    const fields = {};
    for (const name of globalThis.PRKS_PERSON_METADATA_FIELDS) {
        fields[name] = { revision: 0 };
    }
    await cache.putEntity('person-metadata-state', JANE,
        { person_id: JANE, fields: JSON.parse(JSON.stringify(fields)) });
    await cache.putEntity('work', 'W-1', { id: 'W-1', linked_people: [] });
    const offline = createPrksOfflineRuntime({ store: cache, window: null,
        prksRequest: async () => { throw new Error('no reads'); } });

    assert.equal(await offline.reconcileWorkRole({
        work_id: 'W-1', person_id: JANE, role_type: 'Author', present: true,
        credit_name: 'Mark Twain', server_revision: 1, first_name: 'Jane',
        last_name: 'Doe', aliases_revision: 1,
    }), true);
    const state = (await cache.getEntity('person-metadata-state', JANE)).value;
    assert.equal(state.fields.aliases.revision, 1,
        'ACK aliases_revision must become the offline aliases base');
    assert.equal(state.fields.first_name.revision, 0, 'other fields untouched');

    // Absent aliases_revision leaves the projection alone (no promotion).
    assert.equal(await offline.reconcileWorkRole({
        work_id: 'W-1', person_id: JANE, role_type: 'Author', present: true,
        credit_name: 'Mark Twain', server_revision: 1, first_name: 'Jane',
        last_name: 'Doe',
    }), true);
    assert.equal((await cache.getEntity('person-metadata-state', JANE))
        .value.fields.aliases.revision, 1);

    // CREATE_WORK construction with a promoted credit patches the same way.
    assert.equal(await offline.reconcileCreatedWork({
        work_id: 'W-2', changed: true, folder_id: 'F1', playlist_id: '',
        role_count: 1, aliases_revisions: { [ED]: 2 },
    }), true);
    // No ED metadata-state cached -- missing is fine.
    assert.equal(await cache.getEntity('person-metadata-state', ED), null);

    fields.aliases = { revision: 0 };
    await cache.putEntity('person-metadata-state', ED,
        { person_id: ED, fields: JSON.parse(JSON.stringify(fields)) });
    assert.equal(await offline.reconcileCreatedWork({
        work_id: 'W-3', changed: true, folder_id: 'F1', playlist_id: '',
        role_count: 1, aliases_revisions: { [ED]: 2 },
    }), true);
    assert.equal((await cache.getEntity('person-metadata-state', ED))
        .value.fields.aliases.revision, 2);
}

/* ---- a GET that began before the acknowledgement must lose ---- */
async function staleRead() {
    const cache = createPrksOfflineStore({ indexedDB: createFakeIndexedDBFactory() });
    const stale = [{ id: 'W-1', linked_people: [], linked_authors: '', primary_author: '',
        primary_editor: '' }];
    await cache.putList('works-browse:index', JSON.parse(JSON.stringify(stale)), '');
    let release = null;
    const inFlight = new Promise(resolve => { release = resolve; });
    const offline = createPrksOfflineRuntime({ store: cache, window: null, prksRequest: async () => {
        await inFlight;
        return { ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(stale)) };
    } });
    const reading = offline.readThroughList('works-browse:index', '/api/works?projection=browse',
        { domain: 'works-browse', validate: rows => Array.isArray(rows) });
    for (let i = 0; i < 5; i++) await new Promise(r => setTimeout(r, 1));
    assert.equal(await offline.reconcileWorkRole({ work_id: 'W-1', person_id: JANE,
        role_type: 'Author', present: true, credit_name: '', server_revision: 1,
        canonical_name: 'Jane Doe' }), true);
    release();
    await reading;
    for (let i = 0; i < 5; i++) await new Promise(r => setTimeout(r, 1));
    const row = (await cache.getList('works-browse:index')).value.find(r => r.id === 'W-1');
    assert.equal(row.primary_author, 'Jane Doe', 'a stale response cannot beat the acknowledgement');
}

async function main() {
    overlay();
    composition();
    crossFamily();
    personMembership();
    detailRoles();
    personPage();
    await coalescing();
    await creationDependency();
    handlerContract();
    await reconciliation();
    await aliasesRevisionReconciliation();
    graphOverlay();
    await graphReconciliation();
    await staleRead();
    console.log('All ' + checks + ' Work role checks passed');
}

main().catch(error => { console.error(error); process.exit(1); });
