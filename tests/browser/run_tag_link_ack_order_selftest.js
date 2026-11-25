'use strict';
/* #1088 / #1425: a delayed or replayed Tag-link ACK must never replace a newer
 * Work or Folder detail. Covers the cached detail and options (offline
 * runtime) and the parked Work owner, all through the shared reconcile core. */
const strict = require('assert/strict');
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); },
    { get: (_target, key) => (...args) => { checks += 1; return strict[key](...args); } });
const { createFakeIndexedDBFactory } = require('./lib/fake_indexeddb.js');
const { createPrksOfflineStore } = require('../../frontend/js/offline-store.js');
const { createPrksOfflineRuntime } = require('../../frontend/js/offline-runtime.js');
require('../../frontend/js/work-tag-state.js');
require('../../frontend/js/folder-tag-state.js');
globalThis.prksSyncReconcile = require('../../frontend/js/sync-reconcile.js');

const tag = { id: 'T-A', name: 'Existing', color: null, aliases: [] };
const later = () => new Promise(resolve => setTimeout(resolve, 3));
const createdNow = () => ({ created_at: new Date().toISOString() });

const FAMILIES = [
    { name: 'Work', kind: 'work', options: 'work-tag-options', idKey: 'work_id',
        reconcile: (offline, ack, op) => offline.reconcileWorkTag(ack, op) },
    { name: 'Folder', kind: 'folder', options: 'folder-tag-options', idKey: 'folder_id',
        reconcile: (offline, ack, op) => offline.reconcileFolderTag(ack, op) },
];

function setup() {
    const cache = createPrksOfflineStore({ dbName: 'prks-offline-v2:test', indexedDB: createFakeIndexedDBFactory() });
    const offline = createPrksOfflineRuntime({ store: cache, window: null,
        prksRequest: async () => { throw new Error('no reads in this scenario'); } });
    return { cache, offline };
}
function ackFor(family, id, revision, present) {
    return { code: 'ACKNOWLEDGED', [family.idKey]: id, tag_id: tag.id, present, server_revision: revision, tag };
}
function options(family, id, assigned, knownAbsent) {
    return { [family.idKey]: id, assigned, known_absent: knownAbsent || {} };
}
async function tagsOf(cache, family, id) {
    const row = await cache.getEntity(family.kind, id);
    return row ? row.value.tags.map(t => t.id) : null;
}

async function cacheOrdering(family) {
    const label = family.name + ': ';
    /* Device A adds the Tag (revision 1); its ACK is delayed. Device B removes
     * it (revision 2). A then reads the newer detail -- no Tag -- without the
     * options projection, and only now reconciles its old ACK. */
    {
        const { cache, offline } = setup();
        const op = createdNow();
        await later();
        await cache.putEntity(family.kind, 'X', { id: 'X', tags: [] });
        assert.equal(await family.reconcile(offline, ackFor(family, 'X', 1, true), op), true,
            label + 'the operation still retires');
        assert.equal(await tagsOf(cache, family, 'X'), null,
            label + 'a newer detail with no options is dropped, not overwritten by the older ACK');
        assert.equal(await cache.getEntity(family.options, 'X'), null, label + 'missing options are not invented');
    }
    /* Same, but the options projection is cached and older than the detail. */
    {
        const { cache, offline } = setup();
        const op = createdNow();
        await later();
        await cache.putEntity(family.options, 'X', options(family, 'X', []));
        await cache.putEntity(family.kind, 'X', { id: 'X', tags: [] });
        assert.equal(await family.reconcile(offline, ackFor(family, 'X', 1, true), op), true);
        assert.equal(await tagsOf(cache, family, 'X'), null,
            label + 'stale options cannot vouch for a detail read after the operation');
        assert.deepEqual((await cache.getEntity(family.options, 'X')).value.assigned,
            [{ tag_id: tag.id, relation_revision: 1 }], label + 'the options projection takes its own revision');
    }
    /* Options already newer than the ACK: nothing moves anywhere. */
    {
        const { cache, offline } = setup();
        await cache.putEntity(family.kind, 'X', { id: 'X', tags: [] });
        await cache.putEntity(family.options, 'X', options(family, 'X', [], { [tag.id]: 2 }));
        await later();
        assert.equal(await family.reconcile(offline, ackFor(family, 'X', 1, true), createdNow()), true);
        assert.deepEqual(await tagsOf(cache, family, 'X'), [], label + 'a superseded ACK leaves the detail');
        assert.deepEqual((await cache.getEntity(family.options, 'X')).value.known_absent, { [tag.id]: 2 },
            label + 'a superseded ACK leaves the options');
    }
    /* Forward ACK on a detail read before the operation: applied, and the
     * detail keeps its original read time so the next ACK can be proven. */
    {
        const { cache, offline } = setup();
        await cache.putEntity(family.kind, 'X', { id: 'X', tags: [] });
        const read = (await cache.getEntity(family.kind, 'X')).observedAt;
        await later();
        const first = createdNow();
        const second = createdNow();
        assert.equal(await family.reconcile(offline, ackFor(family, 'X', 1, true), first), true);
        assert.deepEqual(await tagsOf(cache, family, 'X'), [tag.id], label + 'a forward ACK applies');
        assert.equal((await cache.getEntity(family.kind, 'X')).observedAt, read,
            label + 'an ACK patch keeps the read time of the detail it patched');
        assert.equal(await family.reconcile(offline, ackFor(family, 'X', 2, false), second), true);
        assert.deepEqual(await tagsOf(cache, family, 'X'), [], label + 'a second queued ACK is still proven');
    }
    /* Replay with options present: the second delivery is superseded. */
    {
        const { cache, offline } = setup();
        await cache.putEntity(family.kind, 'X', { id: 'X', tags: [] });
        await cache.putEntity(family.options, 'X', options(family, 'X', []));
        await later();
        const first = createdNow();
        const second = createdNow();
        assert(await family.reconcile(offline, ackFor(family, 'X', 1, true), first));
        assert(await family.reconcile(offline, ackFor(family, 'X', 2, false), second));
        assert(await family.reconcile(offline, ackFor(family, 'X', 1, true), first));
        assert.deepEqual(await tagsOf(cache, family, 'X'), [], label + 'a replayed older ACK cannot re-add the Tag');
        assert.deepEqual((await cache.getEntity(family.options, 'X')).value.known_absent, { [tag.id]: 2 },
            label + 'a replayed older ACK cannot move the options base back');
    }
    /* A detail that already agrees needs no proof and is kept. */
    {
        const { cache, offline } = setup();
        const op = createdNow();
        await later();
        await cache.putEntity(family.kind, 'X', { id: 'X', tags: [tag] });
        assert(await family.reconcile(offline, ackFor(family, 'X', 1, true), op));
        assert.deepEqual(await tagsOf(cache, family, 'X'), [tag.id], label + 'an agreeing detail is kept');
    }
    /* The unproven detail must be GONE before the operation retires. A
     * deletion that does not commit keeps the operation; its replay deletes. */
    {
        const { cache, offline } = setup();
        const op = createdNow();
        await later();
        await cache.putEntity(family.kind, 'X', { id: 'X', tags: [] });
        const deleteEntity = cache.deleteEntity;
        cache.deleteEntity = async () => false;
        assert.equal(await family.reconcile(offline, ackFor(family, 'X', 1, true), op), false,
            label + 'a failed deletion keeps the operation retryable');
        cache.deleteEntity = deleteEntity;
        assert.equal(await family.reconcile(offline, ackFor(family, 'X', 1, true), op), true,
            label + 'the replay retires once the deletion commits');
        assert.equal(await tagsOf(cache, family, 'X'), null, label + 'the replay deleted the unproven detail');
    }
    /* No operation (a resolved conflict's server state): unproven unless it agrees. */
    {
        const { cache, offline } = setup();
        await cache.putEntity(family.kind, 'X', { id: 'X', tags: [] });
        assert(await family.reconcile(offline, ackFor(family, 'X', 3, true), undefined));
        assert.equal(await tagsOf(cache, family, 'X'), null, label + 'without an operation the detail is dropped');
    }
}

async function parkedWorkOwner() {
    const fresh = { id: 'W', tags: [], folder_id: 'SERVER-FOLDER' };
    let reads = 0;
    globalThis.prksOfflineReadEntity = async () => {
        reads += 1;
        return { value: globalThis.prksSyncReconcile.observe(fresh, Date.now()), source: 'server', cachedAt: Date.now() };
    };
    globalThis.prksRightPanelOwnedBy = () => false;
    globalThis.prksOwnerTabIsFocused = () => false;
    require('../../frontend/js/work-tag-editor.js');
    const apply = globalThis.prksApplyWorkTagEntityAck;
    assert.equal(typeof apply, 'function');
    const core = globalThis.prksSyncReconcile;
    function owner(work) {
        const ctx = { entity: work, getEntity: () => ctx.entity, setEntity: (_type, value) => { ctx.entity = value; } };
        return ctx;
    }
    const flush = () => new Promise(resolve => setTimeout(resolve, 0));
    const ack = { work_id: 'W', tag_id: tag.id, present: true, server_revision: 1, tag };

    /* Read before the operation: patched in place, no re-read. */
    const before = owner(core.observe({ id: 'W', tags: [] }, Date.now()));
    await later();
    apply(before, ack, createdNow());
    await flush();
    assert.deepEqual(before.entity.tags.map(t => t.id), [tag.id], 'parked owner takes a proven ACK');
    assert.equal(reads, 0, 'a proven ACK needs no re-read');

    /* Read after the operation (the newer detail): the server read wins. */
    const op = createdNow();
    await later();
    const after = owner(core.observe({ id: 'W', tags: [], folder_id: 'PENDING-FOLDER' }, Date.now()));
    apply(after, ack, op);
    await flush();
    assert.equal(reads, 1, 'an unproven ACK re-reads the Work');
    assert.deepEqual(after.entity.tags, fresh.tags, 'the parked owner ends on the server links, not the older ACK');
    assert.equal(after.entity.folder_id, 'PENDING-FOLDER',
        'the re-read takes only the links: a pending Folder move on the held Work survives');
    assert(core.observedAt(after.entity) >= core.observedAt(fresh),
        'the merged Work carries the re-read time');

    /* A Work replaced while the re-read was in flight keeps its replacement. */
    const raced = owner({ id: 'W', tags: [] });
    apply(raced, ack, op);
    const replacement = { id: 'W', tags: [], replaced: true };
    raced.entity = replacement;
    await flush();
    assert.equal(raced.entity, replacement, 'a late re-read never undoes a newer owner change');

    /* Another Work, or an ACK without a revision, is ignored. */
    const other = owner({ id: 'OTHER', tags: [] });
    apply(other, ack, op);
    apply(before, { ...ack, server_revision: undefined }, op);
    assert.deepEqual(other.entity.tags, [], 'an ACK for another Work is ignored');
}

async function main() {
    for (const family of FAMILIES) await cacheOrdering(family);
    await parkedWorkOwner();
    console.log('All ' + checks + ' Tag link ACK ordering checks passed');
}

main().then(() => process.exit(0)).catch(error => { console.error(error); process.exit(1); });
