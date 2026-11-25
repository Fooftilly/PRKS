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
    globalThis.prksOfflineReadEntity = async kind => {
        if (kind === 'work-notes-state') {
            return { value: { work_id: 'W', research_note_revision: 3, private_note_revision: 0 },
                source: 'server' };
        }
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

/* The corrective note read: re-establishes the base with the body it shows,
 * carries the references resolved for that body, and never lets a read that
 * began before a later ACK undo it. */
async function notesOwnerCorrectiveRead() {
    const listeners = [];
    globalThis.prksSync = { subscribe: fn => { listeners.push(fn); return () => {}; } };
    const emit = event => listeners.forEach(fn => fn(event));
    const opFor = (id, text) => ({ op_id: id, operation: 'SET_WORK_RESEARCH_NOTE', entity_id: 'W',
        payload: { text }, ...createdNow() });
    const ackOf = (revision, refs) => ({ code: 'ACKNOWLEDGED', work_id: 'W', server_revision: revision,
        ...(refs ? { research_refs: refs } : {}) });
    const ownerWith = body => {
        const observed = { research: { value: body, revision: 0, source: 'server' },
            private: { value: '', revision: 0, source: 'server' } };
        const canonical = { id: 'W', text_content: body, private_notes: '', sources: { research: 'server' } };
        return ownerCtx(core.observe({ id: 'W', text_content: body, private_notes: null }, Date.now()),
            { workNotesObserved: observed, workNotesCanonical: canonical });
    };

    /* rev 1 links Concept A (delayed ACK); rev 2 links Concept B. */
    {
        listeners.length = 0;
        const requests = [];
        globalThis.prksOfflineReadEntity = async (kind, _id, _path, options) => {
            requests.push([kind, options && options.requestPolicy && options.requestPolicy.dedupe]);
            if (kind === 'work-notes-state') {
                return { value: { work_id: 'W', research_note_revision: 2, private_note_revision: 0 },
                    source: 'server' };
            }
            return { value: { id: 'W', text_content: 'see [[B]]', private_notes: null,
                research_refs: { b: 'concept-B' } }, source: 'server' };
        };
        const op = opFor('o1', 'see [[A]]');
        await later();
        const ctx = ownerWith('older');
        globalThis.prksBindWorkNotesSync(ctx);
        emit({ acknowledged: ackOf(1, { a: 'concept-A' }), op });
        await flush(); await flush();
        assert.deepEqual(requests.map(r => r[0]), ['work-notes-state', 'work'],
            'the revision is read before the body');
        assert.deepEqual(requests.map(r => r[1]), [false, false], 'neither read joins an older GET');
        assert.equal(ctx.entity.text_content, 'see [[B]]', 'the server body is adopted');
        assert.deepEqual(ctx.entity.research_refs, { b: 'concept-B' }, 'with the references resolved for it');
        const base = ctx.resources.workNotesObserved.research;
        assert.equal(base.value, 'see [[B]]', 'the base holds the body now shown');
        assert.equal(base.revision, 2, 'at the revision read with it');
        assert.equal(ctx.resources.workNotesCanonical.text_content, 'see [[B]]');
    }
    /* A server body without references drops the ACK's: they belong to A. */
    {
        listeners.length = 0;
        globalThis.prksOfflineReadEntity = async kind => (kind === 'work-notes-state'
            ? { value: { work_id: 'W', research_note_revision: 2, private_note_revision: 0 }, source: 'server' }
            : { value: { id: 'W', text_content: 'B', private_notes: null }, source: 'server' });
        const op = opFor('o2', 'A');
        await later();
        const ctx = ownerWith('older');
        globalThis.prksBindWorkNotesSync(ctx);
        emit({ acknowledged: ackOf(1, { a: 'concept-A' }), op });
        await flush(); await flush();
        assert.equal(ctx.entity.text_content, 'B');
        assert.equal(ctx.entity.research_refs, undefined, 'no stale references for the new body');
    }
    /* A revision that could not be read from the server adopts nothing. */
    {
        listeners.length = 0;
        let bodyReads = 0;
        globalThis.prksOfflineReadEntity = async kind => {
            if (kind === 'work-notes-state') {
                return { value: { work_id: 'W', research_note_revision: 2, private_note_revision: 0 },
                    source: 'cache' };
            }
            bodyReads += 1;
            return { value: { id: 'W', text_content: 'B', private_notes: null }, source: 'server' };
        };
        const op = opFor('o3', 'A');
        await later();
        const ctx = ownerWith('older');
        globalThis.prksBindWorkNotesSync(ctx);
        emit({ acknowledged: ackOf(1), op });
        await flush(); await flush();
        assert.equal(bodyReads, 0, 'no body is read without its revision');
        assert.equal(ctx.entity.text_content, 'A', 'the Work keeps the ACK it was patched with');
        assert.equal(ctx.resources.workNotesObserved.research.value, 'A');
        assert.equal(ctx.resources.workNotesObserved.research.revision, 1, 'and the matching base');
    }
    /* The rev-1 read is delayed past the rev-2 ACK, then answers with rev 1. */
    {
        listeners.length = 0;
        let release;
        const gate = new Promise(resolve => { release = resolve; });
        globalThis.prksOfflineReadEntity = async kind => {
            await gate;
            return kind === 'work-notes-state'
                ? { value: { work_id: 'W', research_note_revision: 1, private_note_revision: 0 }, source: 'server' }
                : { value: { id: 'W', text_content: 'A', private_notes: null }, source: 'server' };
        };
        const first = opFor('o4', 'A');
        await later();
        const ctx = ownerWith('older');
        globalThis.prksBindWorkNotesSync(ctx);
        emit({ acknowledged: ackOf(1), op: first });
        await flush();
        await later();
        const work = ctx.entity;
        emit({ acknowledged: ackOf(2), op: opFor('o5', 'C') });
        assert.equal(ctx.entity, work, 'the later ACK patches the same Work in place');
        assert.equal(work.text_content, 'C');
        release();
        await flush(); await flush(); await flush();
        assert.equal(ctx.entity.text_content, 'C', 'the earlier read cannot undo the later ACK');
        assert.equal(ctx.resources.workNotesCanonical.text_content, 'C');
        assert.equal(ctx.resources.workNotesObserved.research.value, 'C');
        assert.equal(ctx.resources.workNotesObserved.research.revision, 2);
    }
}

/* A source re-read takes only the source columns: a note ACK that landed on
 * the held Work while the read was in flight stays. */
async function sourceRereadKeepsOtherFamilies() {
    globalThis.prksRightPanelOwnedBy = () => false;
    globalThis.prksOwnerTabIsFocused = () => false;
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    globalThis.prksOfflineReadEntity = async () => {
        await gate;
        return { value: core.observe({ id: 'W', title: 'Clip', text_content: 'note A', folders: [],
            ...video('BBB') }, Date.now()), source: 'server' };
    };
    const ack = { work_id: 'W', server_revision: 1, ...video('AAA') };
    const op = createdNow();
    await later();
    const ctx = ownerCtx(core.observe({ id: 'W', title: 'Clip', text_content: 'note A',
        folders: [{ id: 'F-pending' }], ...video('ZZZ') }, Date.now()));
    globalThis.prksApplyWorkSourceEntityAck(ctx, ack, op);
    ctx.entity.text_content = 'note B';
    release();
    await flush(); await flush();
    assert.equal(ctx.entity.provider_id, 'BBB', 'the server source is adopted');
    assert.equal(ctx.entity.thumb_url, 'https://img/BBB.jpg');
    assert.equal(ctx.entity.text_content, 'note B', 'a note acknowledged meanwhile stays');
    assert.deepEqual(ctx.entity.folders, [{ id: 'F-pending' }], 'pending placement stays');
}

/* Cached records: an absent and an empty private note agree; a patched list
 * keeps the time of the read behind it; a list that could not be dropped
 * keeps the operation queued. */
async function cacheEdges() {
    {
        const { cache, offline } = setup();
        const op = { operation: 'SET_WORK_PRIVATE_NOTE', entity_id: 'W', payload: { text: '' }, ...createdNow() };
        await later();
        await cache.putEntity('work', 'W', { id: 'W', text_content: 'R', private_notes: '' });
        assert(await offline.reconcilePrivateNote({ code: 'ACKNOWLEDGED', work_id: 'W',
            note_kind: 'work-private-note', changed: true, server_revision: 1, value_omitted: true }, op));
        assert.notEqual(await cache.getEntity('work', 'W'), null, 'an empty ACK agrees with an empty cached note');
    }
    {
        const { cache, offline } = setup();
        const readAt = Date.now() - 60000;
        globalThis.prksIsRecentIndexShape = Array.isArray;
        globalThis.prksMergeRecentOpen = (rows, result) => [{ id: result.work_id }, ...rows];
        await cache.putList('recent:index', [{ id: 'OLD' }], '', readAt);
        assert(await offline.reconcileRecentOpen({ work_id: 'W' }));
        const after = await cache.getList('recent:index');
        assert.deepEqual(after.value.map(row => row.id), ['W', 'OLD'], 'Recent is patched');
        assert.equal(after.observedAt, readAt, 'Recent keeps the time of its read');
    }
    {
        const { cache, offline } = setup();
        const readAt = Date.now() - 60000;
        globalThis.PRKS_SYNCED_WORK_FIELD_PROJECTIONS = { title: ['works-browse'] };
        globalThis.prksProjectionFieldPatch = (_domain, field, value) => ({ [field]: value });
        globalThis.prksWorkFieldToEntity = (_field, value) => value;
        await cache.putList('works-browse:index', [browseRow('AAA')], '', readAt);
        assert(await offline.reconcileWorkField({ work_id: 'W', field: 'title', value: 'New', server_revision: 1 }));
        const after = await cache.getList('works-browse:index');
        assert.equal(after.value[0].title, 'New', 'the browse row is patched');
        assert.equal(after.observedAt, readAt, 'a patched browse list keeps the time of its read');
    }
    {
        const { cache, offline } = setup();
        const op = createdNow();
        await later();
        await cache.putList('works-browse:index', [browseRow('BBB')], '');
        cache.deleteList = async () => { throw new Error('quota'); };
        assert.equal(await offline.reconcileWorkSource(sourceAck(1, 'AAA'), op), false,
            'an unproven list that could not be dropped keeps the operation queued');
    }
}

async function main() {
    await sourceCache();
    await notesCache();
    await parkedSourceOwner();
    await notesOwner();
    await notesOwnerCorrectiveRead();
    await sourceRereadKeepsOtherFamilies();
    await cacheEdges();
    console.log('All ' + checks + ' Work source and note ACK ordering checks passed');
}

main().then(() => process.exit(0)).catch(error => { console.error(error); process.exit(1); });
