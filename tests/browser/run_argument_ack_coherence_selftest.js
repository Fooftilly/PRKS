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

async function main() {
    await unchangedAcknowledgementKeepsAllCaches();
    await sourcesInvalidateArgumentsButNotPositions();
    await targetsInvalidateArgumentsAndPositionsOnly();
    console.log("All 3 production Argument ACK coherence scenarios passed");
}
main().catch(err => {console.error(err); process.exitCode = 1;});
