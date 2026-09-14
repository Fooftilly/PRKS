"use strict";
/* Operation DEPENDENCIES in the coordinator.
 *
 * An operation may name prerequisites -- a link to a Person this device created
 * and has not yet synchronized depends on that creation. The coordinator owns
 * three facts about them: order (never send before a prerequisite has
 * succeeded), outcome (a prerequisite the server REFUSED must not unlock
 * anything), and retention (a prerequisite stays until its dependents are gone).
 */
const strict = require('assert/strict');
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); },
    { get: (_t, k) => (...args) => { checks += 1; return strict[k](...args); } });
const { createFakeIndexedDBFactory } = require('./lib/fake_indexeddb.js');
const { createPrksLocalStore } = require('../../frontend/js/local-store.js');
require('../../frontend/js/sync-runtime.js');
require('../../frontend/js/person-state.js');
require('../../frontend/js/work-role-state.js');

let sequence = 0;
const uuid = () => '00000000-0000-4000-8000-' + (++sequence).toString(16).padStart(12, '0');
const tick = () => new Promise(resolve => setTimeout(resolve, 1));
async function settle() { for (let i = 0; i < 12; i++) await tick(); }

function personAck(op) {
    return { code: 'ACKNOWLEDGED', person_id: op.entity_id, changed: true,
        person: { id: op.entity_id, first_name: 'Jane', last_name: 'Doe' } };
}
function roleAck(op) {
    return { code: 'ACKNOWLEDGED', work_id: op.entity_id,
        person_id: op.payload.person_id, role_type: op.payload.role_type,
        changed: true, server_revision: 1, present: true, credit_name: '',
        first_name: 'Jane', last_name: 'Doe' };
}

function runtimeFor(store, respond, reconciled) {
    const handlers = {
        CREATE_PERSON: Object.assign({}, globalThis.prksPersonSyncHandler,
            { reconcile: async () => { reconciled.push('person'); return true; } }),
        ADD_WORK_PERSON_ROLE: Object.assign({}, globalThis.prksWorkRoleSyncHandler,
            { reconcile: async () => { reconciled.push('role'); return true; } }),
    };
    return globalThis.createPrksSyncRuntime({ store, online: () => true, handlers,
        request: respond });
}

/* ---- order: the prerequisite goes first ---- */
async function ordering() {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    const created = await store.createPerson({ first_name: 'Jane', last_name: 'Doe' });
    const role = await store.saveWorkPersonRole('W-1',
        { person_id: created.entity_id, role_type: 'Author', state: '' },
        { state: null, revision: 0 }, null);
    assert.deepEqual(role.depends_on, [created.op_id]);

    const sent = [];
    const runtime = runtimeFor(store, async (_path, options) => {
        const body = JSON.parse(options.body);
        sent.push(body.operation);
        return { ok: true, status: 200, json: async () =>
            (body.operation === 'CREATE_PERSON' ? personAck(body) : roleAck(body)) };
    }, []);
    await runtime.wake();
    await settle();
    runtime.stop();
    assert.deepEqual(sent, ['CREATE_PERSON', 'ADD_WORK_PERSON_ROLE'],
        'the Person is created on the server before anything links to it');
    assert.equal((await store.listOperations()).length, 0, 'and both retire');
}

/* ---- retention: a prerequisite outlives its own acknowledgement ---- */
async function retention() {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    const created = await store.createPerson({ first_name: 'Jane', last_name: 'Doe' });
    await store.saveWorkPersonRole('W-1',
        { person_id: created.entity_id, role_type: 'Author', state: '' },
        { state: null, revision: 0 }, null);

    let allowRole = false;
    const runtime = runtimeFor(store, async (_path, options) => {
        const body = JSON.parse(options.body);
        if (body.operation === 'ADD_WORK_PERSON_ROLE' && !allowRole) {
            return { ok: false, status: 503, json: async () => ({}) };
        }
        return { ok: true, status: 200, json: async () =>
            (body.operation === 'CREATE_PERSON' ? personAck(body) : roleAck(body)) };
    }, []);
    await runtime.wake();
    await settle();
    runtime.stop();
    /* The creation succeeded but its dependent has not. Deleting it would
     * leave the role pointing at a prerequisite nothing can verify. */
    const rows = await store.listOperations();
    const prerequisite = rows.find(r => r.op_id === created.op_id);
    assert.ok(prerequisite, 'an acknowledged prerequisite is kept while a dependent needs it');
    assert.equal(prerequisite.status, 'acknowledged');
}

/* ---- outcome: a REFUSED prerequisite must not unlock its dependent ---- */
async function failedPrerequisite() {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    const created = await store.createPerson({ first_name: 'Jane', last_name: 'Doe' });
    await store.saveWorkPersonRole('W-1',
        { person_id: created.entity_id, role_type: 'Author', state: '' },
        { state: null, revision: 0 }, null);

    const sent = [];
    const runtime = runtimeFor(store, async (_path, options) => {
        const body = JSON.parse(options.body);
        sent.push(body.operation);
        if (body.operation === 'CREATE_PERSON') {
            /* A terminal refusal -- one the server can actually produce for
             * this family. This Person will never exist remotely. */
            return { ok: false, status: 400, json: async () =>
                ({ code: 'INVALID_ENVELOPE' }) };
        }
        return { ok: true, status: 200, json: async () => roleAck(body) };
    }, []);
    await runtime.wake();
    await settle();
    runtime.stop();

    /* The link must NOT be sent. A consumed operation is marked `acknowledged`
     * on its way out -- that is what "the server has spoken" means in this
     * store -- and reading that as success made a refused creation unlock the
     * very relationship it could never support. The server then refused that
     * too, which is a loop rather than a recovery. */
    assert.deepEqual(sent, ['CREATE_PERSON'], 'the dependent link is never attempted');
    const rows = await store.listOperations();
    const role = rows.find(r => r.operation === 'ADD_WORK_PERSON_ROLE');
    assert.ok(role, 'the user’s intent is kept, not silently dropped');
    assert.equal(role.status, 'conflict',
        'and surfaced as a decision rather than left stuck in "waiting to sync"');
    assert.equal(role.server_result.code, 'DEPENDENCY_FAILED');
}

/* ---- readiness is judged on OUTCOME, not on local status alone ---- */
async function readinessJudgesOutcome() {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    const created = await store.createPerson({ first_name: 'Jane', last_name: 'Doe' });
    /* A prerequisite the server REFUSED. It is marked `acknowledged` because
     * nothing further is owed on it -- that is what the status means here --
     * and it records why. A dependent enqueued against it must still not run:
     * reading the status alone cannot tell "applied" from "refused". */
    await store.updateOperationSyncState(created.op_id, {
        status: 'acknowledged', server_result: { code: 'INVALID_ENVELOPE' } });
    await store.saveWorkPersonRole('W-1',
        { person_id: created.entity_id, role_type: 'Author', state: '' },
        { state: null, revision: 0 }, null);

    const sent = [];
    const runtime = runtimeFor(store, async (_path, options) => {
        sent.push(JSON.parse(options.body).operation);
        return { ok: true, status: 200, json: async () => roleAck(JSON.parse(options.body)) };
    }, []);
    await runtime.wake();
    await settle();
    runtime.stop();
    assert.deepEqual(sent, [], 'a refused prerequisite never makes its dependent eligible');

    /* The same row, having actually SUCCEEDED, does unlock it. */
    await store.updateOperationSyncState(created.op_id, {
        status: 'acknowledged', server_result: null });
    const second = runtimeFor(store, async (_path, options) => {
        sent.push(JSON.parse(options.body).operation);
        return { ok: true, status: 200, json: async () => roleAck(JSON.parse(options.body)) };
    }, []);
    await second.wake();
    await settle();
    second.stop();
    assert.deepEqual(sent, ['ADD_WORK_PERSON_ROLE']);
}

/* ---- a server that disagrees about dependencies is terminal, not retried ---- */
async function unsatisfiedDependencyIsTerminal() {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    const created = await store.createPerson({ first_name: 'Jane', last_name: 'Doe' });
    await store.saveWorkPersonRole('W-1',
        { person_id: created.entity_id, role_type: 'Author', state: '' },
        { state: null, revision: 0 }, null);

    let attempts = 0;
    const runtime = runtimeFor(store, async (_path, options) => {
        const body = JSON.parse(options.body);
        if (body.operation === 'CREATE_PERSON') {
            return { ok: true, status: 200, json: async () => personAck(body) };
        }
        attempts += 1;
        return { ok: false, status: 400, json: async () => ({ code: 'UNSATISFIED_DEPENDENCY' }) };
    }, []);
    await runtime.wake();
    await settle();
    runtime.stop();
    /* The coordinator only sends what it believes is ready, so a server that
     * says otherwise is describing a state this device cannot argue with.
     * Retrying it forever burns the queue behind it.
     *
     * Asserted on the OUTCOME rather than the attempt count: a merely
     * unrecognized response also stops after one attempt in a short window --
     * it is just waiting out a backoff -- so counting attempts cannot tell a
     * terminal refusal from a scheduled retry. A terminal one settles. */
    assert.equal(attempts, 1);
    const rows = await store.listOperations();
    const role = rows.find(r => r.operation === 'ADD_WORK_PERSON_ROLE');
    assert.ok(role, 'the intent is kept for the user to resolve');
    assert.equal(role.status, 'conflict', 'settled, not left pending for another try');
    assert.equal(role.last_error, null, 'and not recorded as a transport failure');
}

async function main() {
    await ordering();
    await retention();
    await failedPrerequisite();
    await readinessJudgesOutcome();
    await unsatisfiedDependencyIsTerminal();
    console.log('All ' + checks + ' operation dependency checks passed');
}

main()
    .then(() => process.exit(0))
    .catch(error => { console.error(error); process.exit(1); });
