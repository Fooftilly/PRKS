"use strict";
/*
 * Real ACK-driven cache coherence for Argument sources vs targets (#740).
 *
 * The removed E2Es observed only the cache's domain generation after a
 * direct API call. This test drives the actual offline-runtime production
 * singleton through its exported reconciliation bridge, with a tiny disposable
 * cache, and asserts retained/evicted records. It intentionally does not
 * reconstruct the decision rules inside the test.
 */
const assert = require("node:assert/strict");
const path = require("node:path");

const entities = new Map();
const lists = new Map();
const invalidations = [];
const key = (kind, id) => kind + ":" + id;
const store = {
    isAvailable: async () => true,
    getEntity: async (kind, id) => entities.get(key(kind, id)) || null,
    putEntity: async (kind, id, value, revision) => {
        entities.set(key(kind, id), {value, cachedAt: 1, sourceRevision: revision});
        return true;
    },
    deleteEntity: async (kind, id) => {
        entities.delete(key(kind, id)); return true;
    },
    deleteEntitiesByKind: async kind => {
        invalidations.push({kind});
        for (const id of entities.keys()) {
            if (id.startsWith(kind + ":")) entities.delete(id);
        }
        return true;
    },
    getList: async name => lists.get(name) || null,
    putList: async (name, value, revision) => {
        lists.set(name, {value, cachedAt: 1, sourceRevision: revision});
        return true;
    },
    deleteList: async name => {
        invalidations.push({list: name});
        lists.delete(name); return true;
    },
};
globalThis.createPrksOfflineStore = () => store;
// This module constructs its real singleton at load time using the injected
// store. The ACK handlers therefore affect that same production singleton.
const runtime = require(path.join(__dirname, "../../frontend/js/offline-runtime.js"));
const {createFakeIndexedDBFactory} = require("./lib/fake_indexeddb.js");
const {createPrksLocalStore} = require("../../frontend/js/local-store.js");
require("../../frontend/js/sync-runtime.js");
require("../../frontend/js/argument-state.js");
const fs = require("node:fs");
const registry = fs.readFileSync("frontend/js/sync-runtime.js", "utf8");
// The test creates a coordinator with the production handler. Also fence the
// actual browser registry: a test-only mapping must not mask lost wiring.
assert.equal(registry.split(
    "SET_ARGUMENT_SOURCES: root.prksArgumentSourcesSyncHandler,"
).length - 1, 1, "browser must register its production Argument-source handler");
let sequence = 0;
const uuid = () => "00000000-0000-4000-8000-" +
    (++sequence).toString(16).padStart(12, "0");

async function seed() {
    entities.clear();
    lists.clear();
    invalidations.length = 0;
    await store.putEntity("argument", "A-1", {id:"A-1", kind:"argument"}, "");
    await store.putEntity("argument-state", "A-1", {
        argument_id: "A-1", sources: {revision: 1}, targets: {revision: 1}
    }, "");
    await store.putList("arguments:index", [{id:"A-1",kind:"argument"}], "");
    await store.putEntity("position", "P-1", {id:"P-1", arguments:[]}, "");
    await store.putList("positions:index", [{id:"P-1",name:"Target"}], "");
    await store.putEntity("concept", "C-1", {id:"C-1",name:"Context"}, "");
    await store.putList("concepts:index", [{id:"C-1",name:"Context"}], "");
    await store.putEntity("person", "PER-1", {id:"PER-1"}, "");
    await store.putList("people:index", [{id:"PER-1"}], "");
    await store.putEntity(runtime.PRKS_OFFLINE_DOMAIN_RESEARCH_GRAPH_CORE,
        "G-1", {id:"G-1"}, "");
}

async function flush() {
    // Domain cleanup is asynchronous. The fake store writes settle in a
    // microtask; setImmediate also lets markDomainChanged finish its cleanup.
    await new Promise(resolve => setImmediate(resolve));
}

async function unchangedAcknowledgementKeepsAllCaches() {
    await seed();
    assert.equal(await runtime.prksOfflineReconcileArgumentSources({
        argument_id: "A-1", changed: false, server_revision: 2
    }), true);
    await flush();
    assert.deepEqual(invalidations, [],
        "an unchanged source ACK must never evict a cached domain");
    assert.ok(await store.getEntity("argument", "A-1"));
    assert.ok(await store.getEntity("position", "P-1"));
    assert.ok(await store.getList("arguments:index"));
    assert.ok(await store.getList("positions:index"));
    assert.equal((await store.getEntity("argument-state", "A-1")).value.sources.revision, 2,
        "the acknowledged base revision is updated even if nothing changed");
}

async function sourcesInvalidateArgumentsButNotPositions() {
    await seed();
    assert.equal(await runtime.prksOfflineReconcileArgumentSources({
        argument_id: "A-1", changed: true, server_revision: 2
    }), true);
    await flush();
    assert.equal(await store.getEntity("argument", "A-1"), null);
    assert.equal(await store.getList("arguments:index"), null);
    assert.equal(await store.getEntity(runtime.PRKS_OFFLINE_DOMAIN_RESEARCH_GRAPH_CORE, "G-1"), null);
    assert.ok(await store.getEntity("position", "P-1"),
        "Position detail is independent of a source Work relationship");
    assert.ok(await store.getList("positions:index"));
    assert.ok(await store.getEntity("concept", "C-1"));
    assert.ok(await store.getList("concepts:index"));
    assert.ok(await store.getEntity("person", "PER-1"));
    assert.ok(await store.getList("people:index"));
    assert.equal((await store.getEntity("argument-state", "A-1")).value.sources.revision, 2);
    assert.deepEqual(invalidations.map(x => x.kind || x.list).sort(), [
        "argument", "arguments:index", "research-graph-core"
    ].sort(), "source ACK has precisely the intended invalidation footprint");
}

async function targetsInvalidateArgumentsAndPositionsOnly() {
    await seed();
    assert.equal(await runtime.prksOfflineReconcileArgumentTargets({
        argument_id: "A-1", changed: true, server_revision: 4
    }), true);
    await flush();
    for (const kind of ["argument", "position"]) {
        assert.equal(await store.getEntity(kind, kind === "argument" ? "A-1" : "P-1"), null,
            kind + " cached entity must be invalidated");
    }
    for (const name of ["arguments:index", "positions:index"]) {
        assert.equal(await store.getList(name), null, name + " must be invalidated");
    }
    assert.equal(await store.getEntity(runtime.PRKS_OFFLINE_DOMAIN_RESEARCH_GRAPH_CORE, "G-1"), null);
    assert.ok(await store.getEntity("concept", "C-1"));
    assert.ok(await store.getList("concepts:index"));
    assert.ok(await store.getEntity("person", "PER-1"));
    assert.ok(await store.getList("people:index"));
    assert.equal((await store.getEntity("argument-state", "A-1")).value.targets.revision, 4);
    assert.deepEqual(invalidations.map(x => x.kind || x.list).sort(), [
        "argument", "arguments:index", "position", "positions:index", "research-graph-core"
    ].sort(), "target ACK adds Positions without staling unrelated domains");
}

/* Full source-write -> durable IDB envelope -> production coordinator ->
 * validated server acknowledgement -> cache reconciliation. The existing
 * cache-only test above did not exercise dispatch, queue retirement, or
 * refusal of malformed server responses. */
async function integratedSourceAck(reply) {
    await seed();
    const local = createPrksLocalStore({
        indexedDB: createFakeIndexedDBFactory(), uuid,
    });
    const sent = [];
    const acknowledged = [];
    let reconciled = 0;
    let online = false;
    const original = globalThis.prksOfflineReconcileArgumentSources;
    globalThis.prksOfflineReconcileArgumentSources = async (...args) => {
        reconciled++;
        return original(...args);
    };
    const sourceHandler = globalThis.prksArgumentSourcesSyncHandler;
    assert.equal(typeof sourceHandler.isResult, "function");
    assert.equal(typeof sourceHandler.reconcile, "function");
    const coordinator = globalThis.createPrksSyncRuntime({
        store: local,
        online: () => online,
        handlers: {SET_ARGUMENT_SOURCES: sourceHandler},
        request: async (url, options) => {
            assert.equal(url, "/api/sync/operations");
            assert.equal(options.method, "POST");
            const envelope = JSON.parse(options.body);
            sent.push(envelope);
            return {ok: true, status: 200,
                json: async () => reply(envelope)};
        },
    });
    globalThis.prksSync = coordinator;
    const unsubscribe = coordinator.subscribe(event => {
        if (event && event.acknowledged) acknowledged.push(event);
    });
    const sources = [{work_id: "W-2", pages: "3-4"}];
    try {
        const before = runtime.prksOfflineDomainGeneration("arguments");
        const positionsBefore = runtime.prksOfflineDomainGeneration("positions");
        const conceptsBefore = runtime.prksOfflineDomainGeneration("concepts");
        const op = await globalThis.prksSetArgumentSourcesDurably(
            "A-1", sources, {sources: [{work_id: "W-1", pages: "1"}], revision: 1});
        assert.equal(op.operation, "SET_ARGUMENT_SOURCES");
        const queued = await local.listOperations();
        assert.equal(queued.length, 1, "real writer commits exactly one operation");
        assert.equal(queued[0].status, "pending");
        assert.equal(queued[0].attempt_count, 0, "no send before connectivity");
        assert.equal(queued[0].base_revision, 1);
        assert.deepEqual(queued[0].payload, {sources});
        assert(await store.getEntity("argument", "A-1"),
            "enqueue cannot invalidate the Argument before ACK");
        online = true;
        await coordinator.wake();
        await flush();
        assert.equal(sent.length, 1, "production coordinator sends once");
        assert.equal(sent[0].op_id, queued[0].op_id);
        assert.equal(sent[0].operation, "SET_ARGUMENT_SOURCES");
        assert.equal(sent[0].entity_id, "A-1");
        assert.equal(sent[0].base_revision, 1);
        assert.deepEqual(sent[0].payload, {sources});
        assert.equal(runtime.prksOfflineDomainGeneration("positions"), positionsBefore,
            "a source-only edit must not invalidate Position projections");
        assert.equal(runtime.prksOfflineDomainGeneration("concepts"), conceptsBefore,
            "a source-only edit must not invalidate Concept projections");
        return {local, acknowledged, reconciled, before, sent};
    } finally {
        unsubscribe();
        coordinator.stop();
        delete globalThis.prksSync;
        globalThis.prksOfflineReconcileArgumentSources = original;
    }
}

async function assertSourceAckDomainFootprint() {
    assert.equal(await store.getEntity("argument", "A-1"), null,
        "Argument detail is stale after source ACK");
    assert.equal(await store.getList("arguments:index"), null,
        "Argument index is stale after source ACK");
    assert.ok(await store.getEntity("position", "P-1"),
        "Position detail must survive a source ACK");
    assert.ok(await store.getList("positions:index"),
        "Position index must survive a source ACK");
    assert.ok(await store.getEntity("concept", "C-1"),
        "Concept detail must survive a source ACK");
    assert.ok(await store.getList("concepts:index"),
        "Concept index must survive a source ACK");
    assert.ok(await store.getEntity("person", "PER-1"),
        "Person detail must survive a source ACK");
    assert.ok(await store.getList("people:index"),
        "People index must survive a source ACK");
}

async function productionSourceAckDispatchesAndRejectsBadResponses() {
    const success = await integratedSourceAck(envelope => ({
        code: "ACKNOWLEDGED", argument_id: envelope.entity_id,
        changed: true, server_revision: 2,
    }));
    assert.equal(success.acknowledged.length, 1, "ACK emits once");
    assert.equal(success.acknowledged[0].op.op_id, success.sent[0].op_id);
    assert.equal(success.reconciled, 1, "production handler reconciles exactly once");
    assert.deepEqual(await success.local.listOperations(), [],
        "ACK operation retires only after reconciliation");
    assert(runtime.prksOfflineDomainGeneration("arguments") > success.before);
    assert.equal((await store.getEntity("argument-state", "A-1")).value.sources.revision, 2);
    assert.equal((await store.getEntity("argument-state", "A-1")).value.targets.revision, 1);
    await assertSourceAckDomainFootprint();

    // Mutation negative control: a reconciler that sweeps Positions would
    // break the exact retained-domain assertions above.
    await store.deleteEntity("position", "P-1");
    await assert.rejects(assertSourceAckDomainFootprint,
        /Position detail must survive/, "extra invalidation must be detected");

    for (const badReply of [
        envelope => ({code: "ACKNOWLEDGED", argument_id: "A-FOREIGN",
            changed: true, server_revision: 2}),
        envelope => ({code: "ACKNOWLEDGED", argument_id: envelope.entity_id,
            changed: true, server_revision: "2"}),
    ]) {
        const refused = await integratedSourceAck(badReply);
        const rows = await refused.local.listOperations();
        assert.equal(rows.length, 1, "invalid ACK cannot retire durable intent");
        assert.equal(rows[0].status, "pending");
        assert.equal(rows[0].attempt_count, 1);
        assert.ok(rows[0].last_error, "invalid ACK is retryable");
        assert.equal(refused.acknowledged.length, 0, "no false ACK event");
        assert.equal(refused.reconciled, 0, "no cache write on invalid ACK");
        assert.equal(runtime.prksOfflineDomainGeneration("arguments"), refused.before,
            "invalid ACK cannot invalidate Argument cache");
        assert.ok(await store.getEntity("argument", "A-1"));
        assert.ok(await store.getList("arguments:index"));
        assert.ok(await store.getEntity("position", "P-1"));
        assert.ok(await store.getEntity("concept", "C-1"));
    }
}

async function main() {
    await unchangedAcknowledgementKeepsAllCaches();
    await sourcesInvalidateArgumentsButNotPositions();
    await targetsInvalidateArgumentsAndPositionsOnly();
    await productionSourceAckDispatchesAndRejectsBadResponses();
    console.log("All 4 production Argument ACK coherence scenarios passed");
}
main().catch(err => {console.error(err); process.exitCode = 1;});
