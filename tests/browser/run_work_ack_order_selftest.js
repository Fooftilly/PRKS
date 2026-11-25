'use strict';
/* #1106 / #1105 / #1425: a delayed or replayed Work source or Work note ACK
 * must never replace newer data this browser already read. Covers the cached
 * Work, browse rows and embedded summaries, the revision projections, and
 * the live owners (parked Work source, Work notes subscription). */
const strict = require('assert/strict');
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); },
    { get: (_target, key) => (...args) => { checks += 1; return strict[key](...args); } });
const { createFakeIndexedDBFactory } = require('./lib/fake_indexeddb.js');
const { createPrksOfflineStore } = require('../../frontend/js/offline-store.js');
const { createPrksOfflineRuntime } = require('../../frontend/js/offline-runtime.js');
globalThis.prksSyncReconcile = require('../../frontend/js/sync-reconcile.js');
require('../../frontend/js/work-source-state.js');
require('../../frontend/js/work-notes-state.js');

const core = globalThis.prksSyncReconcile;
const later = () => new Promise(resolve => setTimeout(resolve, 3));
const flush = () => new Promise(resolve => setTimeout(resolve, 0));
const createdNow = () => ({ created_at: new Date().toISOString() });
const WATCH = id => 'https://www.youtube.com/watch?v=' + id;
const video = id => ({ source_kind: 'video', provider: 'youtube', provider_id: id, source_url: WATCH(id),
    thumb_url: 'https://img/' + id + '.jpg', urldate: '2026-01-0' + (id === 'BBB' ? 2 : 1) });
const sourceAck = (revision, id) => ({ work_id: 'W', server_revision: revision, source: video(id) });

function setup() {
    const cache = createPrksOfflineStore({ dbName: 'prks-offline-v2:test', indexedDB: createFakeIndexedDBFactory() });
    const offline = createPrksOfflineRuntime({ store: cache, window: null,
        prksRequest: async () => { throw new Error('no reads in this scenario'); } });
    return { cache, offline };
}
const browseRow = id => { const row = { id: 'W', title: 'Clip', ...video(id) }; delete row.urldate; return row; };

async function sourceCache() {
    /* Device A sets video A (revision 1); the ACK is delayed. Device B sets
     * video B (revision 2). A reads B everywhere, then gets its old ACK. */
    {
        const { cache, offline } = setup();
        const op = createdNow();
        await later();
        await cache.putEntity('work', 'W', { id: 'W', title: 'Clip', ...video('BBB') });
        await cache.putEntity('work-source-state', 'W', { work_id: 'W', revision: 2 });
        await cache.putList('works-browse:index', [browseRow('BBB')], '');
        await cache.putEntity('playlist', 'PL', { id: 'PL', items: [{ id: 'W', ...video('BBB') }] });
        assert.equal(await offline.reconcileWorkSource(sourceAck(1, 'AAA'), op), true, 'the operation retires');
        assert.equal((await cache.getEntity('work', 'W')).value.provider_id, 'BBB', 'Work keeps the newer video');
        assert.equal((await cache.getEntity('work', 'W')).value.thumb_url, 'https://img/BBB.jpg');
        assert.equal((await cache.getEntity('work-source-state', 'W')).value.revision, 2, 'revision stays 2');
        assert.equal((await cache.getList('works-browse:index')).value[0].provider_id, 'BBB',
            'a browse row keeps the newer video');
        assert.equal((await cache.getEntity('playlist', 'PL')).value.items[0].provider_id, 'BBB',
            'an embedded summary keeps the newer video');
    }
    /* No source-state: nothing proves the revision, so every newer record is
     * dropped rather than rewritten with video A. */
    {
        const { cache, offline } = setup();
        const op = createdNow();
        await later();
        await cache.putEntity('work', 'W', { id: 'W', title: 'Clip', ...video('BBB') });
        await cache.putList('works-browse:index', [browseRow('BBB'), { id: 'OTHER' }], '');
        await cache.putEntity('folder', 'F', { id: 'F', works: [{ id: 'W', ...video('BBB') }] });
        assert.equal(await offline.reconcileWorkSource(sourceAck(1, 'AAA'), op), true);
        assert.equal(await cache.getEntity('work', 'W'), null, 'an unproven Work is dropped');
        assert.equal(await cache.getList('works-browse:index'), null, 'an unproven browse list is dropped');
        assert.equal(await cache.getEntity('folder', 'F'), null, 'an unproven Folder summary is dropped');
    }
    /* Forward and equal ACKs on records read before the operation apply. */
    {
        const { cache, offline } = setup();
        await cache.putEntity('work', 'W', { id: 'W', title: 'Clip', ...video('AAA') });
        await cache.putEntity('work-source-state', 'W', { work_id: 'W', revision: 1 });
        await cache.putList('works-browse:index', [browseRow('AAA')], '');
        await later();
        const op = createdNow();
        assert(await offline.reconcileWorkSource(sourceAck(2, 'BBB'), op));
        assert.equal((await cache.getEntity('work', 'W')).value.provider_id, 'BBB', 'a forward ACK applies');
        assert.equal((await cache.getList('works-browse:index')).value[0].provider_id, 'BBB');
        assert.equal((await cache.getEntity('work-source-state', 'W')).value.revision, 2);
        assert(await offline.reconcileWorkSource(sourceAck(2, 'BBB'), op), 'a replayed equal ACK settles');
        assert.equal((await cache.getEntity('work', 'W')).value.provider_id, 'BBB');
        assert(await offline.reconcileWorkSource(sourceAck(1, 'AAA'), op), 'an older replay settles');
        assert.equal((await cache.getEntity('work', 'W')).value.provider_id, 'BBB',
            'an older replay cannot bring video A back');
    }
}

async function notesCache() {
    const research = { operation: 'SET_WORK_RESEARCH_NOTE', entity_id: 'W', payload: { text: 'A' } };
    const ack = revision => ({ code: 'ACKNOWLEDGED', work_id: 'W', note_kind: 'work-research-note',
        changed: true, server_revision: revision, value_omitted: true });
    /* Device B saved body B at revision 2 and this browser read it, then the
     * delayed revision-1 ACK for body A arrives. */
    {
        const { cache, offline } = setup();
        const op = { ...research, ...createdNow() };
        await later();
        await cache.putEntity('work', 'W', { id: 'W', text_content: 'B', private_notes: 'P' });
        await cache.putEntity('work-notes-state', 'W',
            { work_id: 'W', research_note_revision: 2, private_note_revision: 0 });
        assert.equal(await offline.reconcileWorkNote(ack(1), op), true);
        assert.equal((await cache.getEntity('work', 'W')).value.text_content, 'B', 'the newer body stays');
        assert.equal((await cache.getEntity('work-notes-state', 'W')).value.research_note_revision, 2);
    }
    /* Missing notes-state: the newer body cannot be proven and is dropped. */
    {
        const { cache, offline } = setup();
        const op = { ...research, ...createdNow() };
        await later();
        await cache.putEntity('work', 'W', { id: 'W', text_content: 'B', private_notes: 'P' });
        assert.equal(await offline.reconcileWorkNote(ack(1), op), true);
        assert.equal(await cache.getEntity('work', 'W'), null, 'an unproven body is dropped, not overwritten');
    }
    /* Private notes share the rule. */
    {
        const { cache, offline } = setup();
        const op = { operation: 'SET_WORK_PRIVATE_NOTE', entity_id: 'W', payload: { text: 'old' }, ...createdNow() };
        await later();
        await cache.putEntity('work', 'W', { id: 'W', text_content: 'R', private_notes: 'newer' });
        await cache.putEntity('work-notes-state', 'W',
            { work_id: 'W', research_note_revision: 0, private_note_revision: 4 });
        assert(await offline.reconcilePrivateNote({ ...ack(3), note_kind: 'work-private-note' }, op));
        assert.equal((await cache.getEntity('work', 'W')).value.private_notes, 'newer');
        assert.equal((await cache.getEntity('work-notes-state', 'W')).value.private_note_revision, 4);
    }
}

function ownerCtx(work, resources) {
    const cleanups = [];
    const ctx = {
        destroyed: false, mounted: true, entity: work, resources: resources || {},
        getEntity: () => ctx.entity,
        setEntity: (_type, value) => { ctx.entity = value; },
        getResource: name => ctx.resources[name] || null,
        setResource: (name, value) => { ctx.resources[name] = value; },
        registerCleanup: fn => cleanups.push(fn),
    };
    return ctx;
}

async function parkedSourceOwner() {
    globalThis.prksRightPanelOwnedBy = () => false;
    globalThis.prksOwnerTabIsFocused = () => false;
    let reads = 0;
    const fresh = { id: 'W', title: 'Clip', ...video('BBB') };
    globalThis.prksOfflineReadEntity = async () => {
        reads += 1;
        return { value: core.observe({ ...fresh }, Date.now()), source: 'server', cachedAt: Date.now() };
    };
    require('../../frontend/js/work-source-editor.js');
    const apply = globalThis.prksApplyWorkSourceEntityAck;
    const ack = { work_id: 'W', server_revision: 1, ...video('AAA') };
    const op = createdNow();
    await later();
    const newer = ownerCtx(core.observe({ id: 'W', title: 'Clip', ...video('BBB') }, Date.now()));
    apply(newer, ack, op);
    await flush();
    assert.equal(reads, 1, 'an unproven parked source ACK re-reads the Work');
    assert.equal(newer.entity.provider_id, 'BBB', 'the parked owner ends on the newer video');

    const older = ownerCtx(core.observe({ id: 'W', title: 'Clip', ...video('ZZZ') }, Date.now()));
    await later();
    apply(older, ack, createdNow());
    await flush();
    assert.equal(reads, 1, 'a proven ACK needs no re-read');
    assert.equal(older.entity.provider_id, 'AAA', 'a proven ACK applies');
}

async function notesOwner() {
    const listeners = [];
    globalThis.prksSync = { subscribe: fn => { listeners.push(fn); return () => {}; } };
    let reads = 0;
    globalThis.prksOfflineReadEntity = async () => {
        reads += 1;
        return { value: { id: 'W', text_content: 'server', private_notes: null }, source: 'server' };
    };
    const emit = event => listeners.forEach(fn => fn(event));
    const op = { op_id: 'o1', operation: 'SET_WORK_RESEARCH_NOTE', entity_id: 'W', payload: { text: 'A' },
        ...createdNow() };
    await later();

    /* The observed base already holds revision 2: the older ACK changes nothing. */
    const work = core.observe({ id: 'W', text_content: 'B', private_notes: null }, Date.now());
    const observed = { research: { value: 'B', revision: 2, source: 'server' },
        private: { value: '', revision: 0, source: 'server' } };
    const canonical = { id: 'W', text_content: 'B', private_notes: '', sources: { research: 'server' } };
    const ctx = ownerCtx(work, { workNotesObserved: observed, workNotesCanonical: canonical });
    globalThis.prksBindWorkNotesSync(ctx);
    emit({ acknowledged: { code: 'ACKNOWLEDGED', work_id: 'W', server_revision: 1 }, op });
    await flush();
    assert.equal(ctx.entity.text_content, 'B', 'the live Work keeps the newer body');
    assert.equal(canonical.text_content, 'B', 'the canonical body stays newer');
    assert.equal(observed.research.revision, 2, 'the observed base never moves back');
    assert.equal(observed.research.value, 'B');
    assert.equal(reads, 0);

    /* No base, Work read after the operation: patched, then re-read. */
    listeners.length = 0;
    const bare = ownerCtx(core.observe({ id: 'W', text_content: 'B', private_notes: null }, Date.now()),
        { workNotesCanonical: { id: 'W', text_content: 'B', private_notes: '', sources: {} } });
    globalThis.prksBindWorkNotesSync(bare);
    emit({ acknowledged: { code: 'ACKNOWLEDGED', work_id: 'W', server_revision: 1 }, op });
    await flush(); await flush();
    assert.equal(reads, 1, 'an unproven note ACK re-reads the Work');
    assert.equal(bare.entity.text_content, 'server', 'the server body wins');
    assert.equal(bare.resources.workNotesCanonical.text_content, 'server');
}

async function main() {
    await sourceCache();
    await notesCache();
    await parkedSourceOwner();
    await notesOwner();
    console.log('All ' + checks + ' Work source and note ACK ordering checks passed');
}

main().then(() => process.exit(0)).catch(error => { console.error(error); process.exit(1); });
