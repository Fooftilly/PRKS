'use strict';
const assert = require('assert/strict');
const { createFakeIndexedDBFactory } = require('./lib/fake_indexeddb.js');
const { createPrksLocalStore } = require('../../frontend/js/local-store.js');
const { createPrksOfflineStore } = require('../../frontend/js/offline-store.js');
const { createPrksOfflineRuntime } = require('../../frontend/js/offline-runtime.js');
require('../../frontend/js/work-tag-state.js');
require('../../frontend/js/sync-runtime.js');
let sequence = 0;
const uuid = () => '00000000-0000-4000-8000-' + (++sequence).toString(16).padStart(12, '0');
const tag = { id: 'T-A', name: 'Existing', color: '#123456', aliases: [] };
const ack = { code: 'ACKNOWLEDGED', work_id: 'W-A', tag_id: tag.id, present: true, server_revision: 1, tag };
async function main() {
    const factory = createFakeIndexedDBFactory();
    const store = createPrksLocalStore({ indexedDB: factory, uuid });
    const cache = createPrksOfflineStore({ indexedDB: factory });
    const opts = { work_id: 'W-A', assigned: [], known_absent: {} };
    await cache.putEntity('work', 'W-A', { id: 'W-A', title: 'Complete base', tags: [] });
    await cache.putEntity('work-tag-options', 'W-A', opts);
    let requestFails = false;
    let response = [tag];
    const offline = createPrksOfflineRuntime({ store: cache, window: null, prksRequest: async () => {
        if (requestFails) throw new Error();
        return { ok: true, status: 200, json: async () => response };
    } });
    assert(globalThis.prksIsTagsIndexShape([tag]));
    for (const bad of [{ ...tag, aliases: [3] }, { ...tag, name: null }, { ...tag, color: {} }, { ...tag, id: '' }]) {
        assert(!globalThis.prksIsTagsIndexShape([bad]));
    }
    assert(globalThis.prksIsWorkTagOptionsShape(opts, 'W-A'));
    assert(!globalThis.prksIsWorkTagOptionsShape({ ...opts, assigned: [{ tag_id: tag.id, relation_revision: -1 }] }));
    assert(!globalThis.prksIsWorkTagOptionsShape({ ...opts, known_absent: { 'T-A': 0 } }));
    await offline.readThroughList('tags:index', '/api/tags', { domain: 'tags', validate: globalThis.prksIsTagsIndexShape });
    // Flush the best-effort cache publication before testing poison prevention.
    await new Promise(resolve => setTimeout(resolve, 20));
    response = [{ ...tag, aliases: false }];
    await assert.rejects(offline.readThroughList('tags:index', '/api/tags', { domain: 'tags', validate: globalThis.prksIsTagsIndexShape }));
    assert.deepEqual((await cache.getList('tags:index')).value, [tag]);

    const op = await store.coalesceWorkTag('W-A', tag.id, true, false, 0, tag);
    let cacheWorks = false;
    let received = [];
    const runtime = globalThis.createPrksSyncRuntime({ store, online: () => true,
        request: async (path, init) => { received.push(JSON.parse(init.body)); return { ok: true, status: 200, json: async () => ack }; },
        reconcile: async result => cacheWorks && await offline.reconcileWorkTag(result),
    });
    await runtime.wake(); runtime.stop();
    assert.equal((await store.getOperation(op.op_id)).status, 'pending');
    assert.equal((await store.getOperation(op.op_id)).attempt_count, 1);
    assert.deepEqual(globalThis.prksEffectiveWorkTags({ id: 'W-A', tags: [] }, await store.listOperations()), [tag]);
    // Simulate an interrupted sender, then startup recovery resends the same id.
    await store.updateOperationSyncState(op.op_id, { status: 'syncing', attempt_count: 0 });
    cacheWorks = true;
    const recovered = globalThis.createPrksSyncRuntime({ store, online: () => true,
        request: async (path, init) => { received.push(JSON.parse(init.body)); return { ok: true, status: 200, json: async () => ack }; },
        reconcile: result => offline.reconcileWorkTag(result),
    });
    await recovered.wake(); recovered.stop();
    assert.equal((await store.getOperation(op.op_id)).status, 'acknowledged');
    assert.equal(received.length, 2);
    assert.deepEqual(received[0], received[1]);
    assert.equal((await cache.getEntity('work', 'W-A')).value.tags[0].id, tag.id);
    assert.deepEqual((await cache.getEntity('work-tag-options', 'W-A')).value.assigned, [{ tag_id: tag.id, relation_revision: 1 }]);
    await cache.clearAll();
    assert(await offline.reconcileWorkTag(ack));
    assert.equal(await cache.getEntity('work', 'W-A'), null, 'ACK cannot fabricate a complete Work');
    assert.equal((await store.listOperations()).length, 1);
    // Structured conflict retains optimistic removal.
    const remove = await store.coalesceWorkTag('W-A', tag.id, false, true, 1, tag);
    const conflicts = globalThis.createPrksSyncRuntime({ store, online: () => true,
        request: async () => ({ ok: false, status: 409, json: async () => ({
            work_id: 'W-A', tag_id: tag.id, code: 'REVISION_CONFLICT', current_revision: 3, current_state: true, requested_state: false,
        }) }), reconcile: async () => { throw new Error('must not reconcile conflict'); },
    });
    await conflicts.wake(); conflicts.stop();
    assert.equal((await store.getOperation(remove.op_id)).server_result.current_revision, 3);
    assert.deepEqual(globalThis.prksEffectiveWorkTags({ id: 'W-A', tags: [tag] }, await store.listOperations()), []);
    assert.equal((await store.getOperation(remove.op_id)).status, 'conflict');
    console.log('Work Tag sync selftests passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
