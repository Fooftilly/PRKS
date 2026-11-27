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
        await cache.putEntity('work-source-state', 'W', { work_id: 'W', revision: 2, ...video('BBB') });
        await cache.putList('works-browse:index', [browseRow('BBB')], '');
        await cache.putEntity('playlist', 'PL', { id: 'PL', items: [{ id: 'W', ...video('BBB') }] });
        assert.equal(await offline.reconcileWorkSource(sourceAck(1, 'AAA'), op), true, 'the operation retires');
        assert.equal((await cache.getEntity('work', 'W')).value.provider_id, 'BBB', 'Work keeps the newer video');
        assert.equal((await cache.getEntity('work', 'W')).value.thumb_url, 'https://img/BBB.jpg');
        assert.equal((await cache.getEntity('work-source-state', 'W')).value.revision, 2, 'revision stays 2');
        assert.equal((await cache.getEntity('work-source-state', 'W')).value.provider_id, 'BBB',
            'with the source it belongs to');
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
        await cache.putEntity('work-source-state', 'W', { work_id: 'W', revision: 1, ...video('AAA') });
        await cache.putList('works-browse:index', [browseRow('AAA')], '');
        await later();
        const op = createdNow();
        assert(await offline.reconcileWorkSource(sourceAck(2, 'BBB'), op));
        assert.equal((await cache.getEntity('work', 'W')).value.provider_id, 'BBB', 'a forward ACK applies');
        assert.equal((await cache.getList('works-browse:index')).value[0].provider_id, 'BBB');
        assert.deepEqual((await cache.getEntity('work-source-state', 'W')).value,
            { work_id: 'W', revision: 2, ...video('BBB') }, 'the revision moves only with its source');
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
    const policies = [];
    const fresh = { id: 'W', title: 'Clip', ...video('BBB') };
    globalThis.prksOfflineReadEntity = async (_kind, _id, _path, options) => {
        reads += 1;
        policies.push(options && options.requestPolicy && options.requestPolicy.dedupe);
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
    assert.deepEqual(policies, [false], 'without joining an older GET');
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
        assert(after.observedAt > readAt, 'Recent takes the time of the row the open upserted');
        /* A delayed source ACK for that fresh row cannot prove itself. */
        globalThis.prksMergeRecentOpen = (rows, result) => [{ id: result.work_id, ...video('BBB') }, ...rows];
        const op = createdNow();
        await later();
        assert(await offline.reconcileRecentOpen({ work_id: 'W' }));
        assert(await offline.reconcileWorkSource(sourceAck(1, 'AAA'), op));
        assert.equal(await cache.getList('recent:index'), null, 'the fresh row is dropped, not rewritten');
    }
    {
        const { cache, offline } = setup();
        const readAt = Date.now() - 60000;
        globalThis.prksMergeRecentOpen = rows => rows.slice();
        await cache.putList('recent:index', [{ id: 'OLD' }], '', readAt);
        assert(await offline.reconcileRecentOpen({ work_id: 'W' }));
        assert.equal((await cache.getList('recent:index')).observedAt, readAt,
            'a merge that trimmed the opened row keeps the time of the read');
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

/* The mounted source editor's base (#1450 review): ONE source-state answer
 * carries the revision with the source it belongs to, so no read order or
 * read time has to prove that a separately read Work is not older. */
async function mountedSourceCorrectiveRead() {
    globalThis.prksRightPanelOwnedBy = () => false;
    globalThis.prksOwnerTabIsFocused = () => false;
    globalThis.prksRefreshPendingWorkSources = async () => [];
    globalThis.prksOfflineRuntimeState = () => 'online';
    globalThis.prksOfflineRuntimeSubscribe = () => () => {};
    const listeners = [];
    globalThis.prksSync = { subscribe: fn => { listeners.push(fn); return () => {}; } };
    const mountedOwner = work => {
        const ctx = ownerCtx(work);
        ctx.generation = 1;
        ctx.resourceTicket = () => ({});
        ctx.registerResource = (_ticket, entry) => { ctx.resources[entry.kind] = entry.value; return 'attached'; };
        return ctx;
    };
    const stateOf = (revision, id) => ({ work_id: 'W', revision, ...video(id) });
    const serverState = (revision, id) => ({ value: stateOf(revision, id), source: 'server' });
    const identity = id => globalThis.prksWorkSourceIdentity(globalThis.prksWorkSourceOf(video(id)));
    const ackAAA = { work_id: 'W', server_revision: 1, ...video('AAA') };
    const reads = [];
    const answer = result => async (kind, _id, _path, options) => {
        reads.push([kind, options && options.requestPolicy && options.requestPolicy.dedupe]);
        return typeof result === 'function' ? result(kind, options) : result;
    };
    const mounted = async id => {
        globalThis.prksOfflineReadEntity = answer(serverState(0, id));
        const ctx = mountedOwner(core.observe({ id: 'W', title: 'Clip', text_content: 'note', ...video(id) },
            Date.now()));
        globalThis.prksMountWorkSourceEditor(ctx, 'W', { editing: true });
        const state = ctx.resources.workSourceEditor;
        await state.preparing;
        reads.length = 0;
        return { ctx, state };
    };
    /* An unproven ACK for video AAA (revision 1) is re-read: the server now
     * holds CCC at revision 2. The base and the Work take that one answer. */
    {
        const { ctx, state } = await mounted('ZZZ');
        assert.equal(state.observed.revision, 0);
        globalThis.prksOfflineReadEntity = answer(serverState(2, 'CCC'));
        const op = createdNow();
        await later();
        ctx.entity = core.observe({ ...ctx.entity, ...video('BBB') }, Date.now());
        listeners.forEach(fn => fn({ operation: 'SET_WORK_SOURCE', acknowledged: ackAAA, op }));
        await flush(); await flush(); await flush();
        assert.deepEqual(reads, [['work-source-state', false], ['work', false]],
            'the base is one fresh source-state read; the Work is re-read after it, for the cache only');
        assert.deepEqual(state.observed, { revision: 2, identity: identity('CCC') },
            'the revision with the source it belongs to');
        assert.equal(ctx.entity.provider_id, 'CCC', 'the mounted owner shows the server video');
        assert.equal(ctx.entity.thumb_url, 'https://img/CCC.jpg');
        assert.equal(ctx.entity.text_content, 'note', 'only the source columns move');
        listeners.length = 0;
    }
    /* The same corrective read through the real runtime: each answer passes
     * its own validator, and the Work it re-reads lands in the offline cache
     * (the tab keeps its own copy). */
    {
        const { ctx, state } = await mounted('ZZZ');
        const cache = createPrksOfflineStore({ dbName: 'prks-offline-v2:test',
            indexedDB: createFakeIndexedDBFactory() });
        const served = { '/api/works/W/source-state': stateOf(2, 'CCC'),
            '/api/works/W': { id: 'W', title: 'Server title', ...video('CCC') } };
        const paths = [];
        const runtime = createPrksOfflineRuntime({ store: cache, window: null,
            prksRequest: async path => {
                paths.push(path);
                return { ok: true, status: 200, json: async () => served[path] };
            } });
        globalThis.prksOfflineReadEntity = runtime.readThroughEntity;
        const op = createdNow();
        await later();
        ctx.entity = core.observe({ ...ctx.entity, ...video('BBB') }, Date.now());
        listeners.forEach(fn => fn({ operation: 'SET_WORK_SOURCE', acknowledged: ackAAA, op }));
        for (let i = 0; i < 20 && paths.length < 2; i += 1) await flush();
        await flush(); await flush();
        assert.deepEqual(paths, ['/api/works/W/source-state', '/api/works/W']);
        assert.deepEqual(state.observed, { revision: 2, identity: identity('CCC') });
        const cachedWork = await cache.getEntity('work', 'W');
        assert.ok(cachedWork, 'the re-read Work is cached');
        assert.equal(cachedWork.value.provider_id, 'CCC', 'with the server source');
        assert.equal(cachedWork.value.title, 'Server title');
        assert.equal((await cache.getEntity('work-source-state', 'W')).value.revision, 2);
        assert.equal(ctx.entity.title, 'Clip', 'the tab keeps its own Work beyond the source');
        listeners.length = 0;
    }
    /* Cold-parked while the read is in flight: nothing is adopted. */
    {
        const { ctx, state } = await mounted('ZZZ');
        let release;
        const gate = new Promise(resolve => { release = resolve; });
        globalThis.prksOfflineReadEntity = answer(async () => { await gate; return serverState(2, 'CCC'); });
        const op = createdNow();
        await later();
        ctx.entity = core.observe({ ...ctx.entity, ...video('BBB') }, Date.now());
        listeners.forEach(fn => fn({ operation: 'SET_WORK_SOURCE', acknowledged: ackAAA, op }));
        await flush();
        delete ctx.resources.workSourceEditor;   // cold park disposes the editor
        release();
        await flush(); await flush(); await flush();
        assert.notEqual(ctx.entity.provider_id, 'CCC', 'a parked tab adopts nothing');
        assert.equal(state.observed.revision, 1, 'the parked editor keeps the ACK base');
        listeners.length = 0;
    }
    /* The user is typing in the source field: the newer source is not shown,
     * so the base keeps the ACK revision and a Save conflicts instead of
     * overwriting. */
    {
        const { ctx, state } = await mounted('ZZZ');
        globalThis.prksOfflineReadEntity = answer(serverState(2, 'CCC'));
        globalThis.prksRightPanelOwnedBy = () => true;
        globalThis.prksOwnerTabIsFocused = () => true;
        globalThis.document = { activeElement: { matches: () => true, value: WATCH('typed') },
            getElementById: () => null };
        const op = createdNow();
        await later();
        ctx.entity = core.observe({ ...ctx.entity, ...video('BBB') }, Date.now());
        listeners.forEach(fn => fn({ operation: 'SET_WORK_SOURCE', acknowledged: ackAAA, op }));
        await flush(); await flush(); await flush();
        delete globalThis.document;
        globalThis.prksRightPanelOwnedBy = () => false;
        globalThis.prksOwnerTabIsFocused = () => false;
        assert.equal(state.observed.revision, 1, 'the focused field keeps the ACK revision');
        /* The store cancels a save whose identity matches the base. With the
         * newer identity, a draft naming the ACK's video AAA is queued against
         * revision 1 and conflicts; a draft naming CCC is the server's own. */
        assert.equal(state.observed.identity, identity('CCC'), 'paired with the server identity, not the ACK one');
        assert.notEqual(state.observed.identity, identity('AAA'), 'a draft naming the ACK video is not cancelled');
        assert.equal(ctx.entity.provider_id, 'CCC', 'the Work still takes the server source');
        listeners.length = 0;
    }
    /* The same with focus moved to another field: unsaved source text is
     * the user's, so the corrective read does not overwrite it either. */
    {
        const { ctx, state } = await mounted('ZZZ');
        globalThis.prksOfflineReadEntity = answer(serverState(2, 'CCC'));
        globalThis.prksRightPanelOwnedBy = () => true;
        globalThis.prksOwnerTabIsFocused = () => true;
        const accepted = [];
        globalThis.prksVueWorkMetadataEditorOwns = () => true;
        globalThis.prksVueAcceptWorkMetadataField = (_ctx, field, value) => accepted.push([field, value]);
        globalThis.document = { activeElement: null, getElementById: () => null };
        ctx.ui = { workMetaDraftWorkId: 'W', workMetaDraft: { source_url: WATCH('typed') },
            workMetaBaseline: { source_url: WATCH('ZZZ') } };
        const op = createdNow();
        await later();
        ctx.entity = core.observe({ ...ctx.entity, ...video('BBB') }, Date.now());
        listeners.forEach(fn => fn({ operation: 'SET_WORK_SOURCE', acknowledged: ackAAA, op }));
        await flush(); await flush(); await flush();
        delete globalThis.document;
        delete globalThis.prksVueWorkMetadataEditorOwns;
        delete globalThis.prksVueAcceptWorkMetadataField;
        globalThis.prksRightPanelOwnedBy = () => false;
        globalThis.prksOwnerTabIsFocused = () => false;
        assert.deepEqual(accepted, [], 'the unsaved draft is not overwritten');
        assert.equal(state.observed.revision, 1, 'and keeps the ACK revision, as a focused field does');
        assert.equal(state.observed.identity, identity('CCC'));
        /* Returning to the pane prepares the editor again. The Work already
         * shows CCC, so nothing differs, but the draft still belongs to
         * revision 1: its Save must conflict, not replace CCC unseen. */
        globalThis.prksMountWorkSourceEditor(ctx, 'W', { editing: true });
        await state.preparing;
        assert.equal(ctx.entity.provider_id, 'CCC');
        assert.equal(state.observed.revision, 1, 'a second prepare keeps the draft on its revision');
        /* A reopened editor seeds its baseline from the source it shows. A
         * draft typed over the source the read returns takes that revision;
         * the session alone never rebases a draft typed over another one. */
        ctx.ui.workMetaEditSession = 7;
        globalThis.prksMountWorkSourceEditor(ctx, 'W', { editing: true });
        await state.preparing;
        assert.equal(state.observed.revision, 1, 'a new session alone keeps the draft on its base');
        ctx.ui.workMetaBaseline.source_url = WATCH('CCC');
        globalThis.prksMountWorkSourceEditor(ctx, 'W', { editing: true });
        await state.preparing;
        assert.equal(state.observed.revision, 2, 'a draft typed over the read source takes its revision');
        ctx.ui.workMetaBaseline.source_url = WATCH('ZZZ');
        state.observed = { revision: 1, identity: identity('CCC') };
        /* Without the draft the same prepare takes the server's revision. */
        ctx.ui.workMetaDraft.source_url = WATCH('ZZZ');
        globalThis.prksMountWorkSourceEditor(ctx, 'W', { editing: true });
        await state.preparing;
        assert.equal(state.observed.revision, 2, 'a clean editor takes the newer revision');
        listeners.length = 0;
    }
    /* A hidden pane (not the panel owner) with an unsaved draft: the read
     * finds a newer source while the user is elsewhere. Returning remounts
     * the same draft, so its Save must still conflict. */
    {
        const { ctx, state } = await mounted('ZZZ');
        ctx.ui = { workMetaDraftWorkId: 'W', workMetaDraft: { source_url: WATCH('typed') },
            workMetaBaseline: { source_url: WATCH('ZZZ') } };
        globalThis.prksOfflineReadEntity = answer(serverState(2, 'BBB'));
        globalThis.prksMountWorkSourceEditor(ctx, 'W', { editing: true });
        await state.preparing;
        assert.equal(ctx.entity.provider_id, 'BBB', 'the held Work shows the newer source');
        assert.equal(state.observed.revision, 0, 'the hidden draft keeps the revision it was typed over');
        assert.equal(state.observed.identity, identity('BBB'), 'and the identity the read returned');
        listeners.length = 0;
    }
    /* A queued replacement BBB is shown over the acknowledged ZZZ and the
     * draft was typed over BBB. A read finds DDD at revision 1: the draft's
     * baseline matches only the queued video, never the read one, so the
     * draft keeps revision 0 and its Save conflicts with DDD. */
    {
        const { ctx, state } = await mounted('ZZZ');
        const effective = globalThis.prksEffectiveWorkSource;
        globalThis.prksEffectiveWorkSource = work => ({ ...work, ...video('BBB') });
        ctx.entity = { ...ctx.entity, ...video('BBB') };
        ctx.ui = { workMetaDraftWorkId: 'W', workMetaDraft: { source_url: WATCH('CCC') },
            workMetaBaseline: { source_url: WATCH('BBB') } };
        globalThis.prksOfflineReadEntity = answer(serverState(1, 'DDD'));
        globalThis.prksMountWorkSourceEditor(ctx, 'W', { editing: true });
        await state.preparing;
        globalThis.prksEffectiveWorkSource = effective;
        assert.equal(state.observed.revision, 0, 'a queued video does not stand in for the read one');
        assert.equal(state.observed.identity, identity('DDD'),
            'paired with the read identity, so typing ZZZ back is a change to send, not a cancellation');
        listeners.length = 0;
    }
    /* "Use server" on a conflict is the user's explicit choice: it replaces
     * an unsaved draft and editing resumes on the server's source. */
    {
        const { ctx, state } = await mounted('ZZZ');
        globalThis.prksOfflineReadEntity = answer(serverState(2, 'DDD'));
        globalThis.prksRightPanelOwnedBy = () => true;
        globalThis.prksOwnerTabIsFocused = () => true;
        globalThis.prksGetFocusedTabContext = () => ctx;
        globalThis.prksOfflineMarkEntityChanged = () => {};
        const resolved = [];
        globalThis.prksSync.store = { resolveConflict: async (opId, apply) => { resolved.push([opId, apply]); } };
        globalThis.prksSync.changed = () => {};
        const accepted = [];
        globalThis.prksVueWorkMetadataEditorOwns = () => true;
        globalThis.prksVueAcceptWorkMetadataField = (_ctx, field, value) => accepted.push([field, value]);
        globalThis.document = { activeElement: null, getElementById: () => null };
        ctx.ui = { workMetaDraftWorkId: 'W', workMetaDraft: { source_url: WATCH('CCC') },
            workMetaBaseline: { source_url: WATCH('ZZZ') } };
        const conflict = { op_id: 'op-1', status: 'conflict', entity_id: 'W' };
        state.operations = [conflict];
        await globalThis.prksResolveWorkSourceConflict('op-1', false);
        delete globalThis.document;
        delete globalThis.prksVueWorkMetadataEditorOwns;
        delete globalThis.prksVueAcceptWorkMetadataField;
        delete globalThis.prksGetFocusedTabContext;
        globalThis.prksRightPanelOwnedBy = () => false;
        globalThis.prksOwnerTabIsFocused = () => false;
        assert.deepEqual(resolved, [['op-1', false]]);
        assert.deepEqual(accepted, [['source_url', WATCH('DDD')]], 'the server source replaces the draft');
        assert.deepEqual(state.observed, { revision: 2, identity: identity('DDD') }, 'editing resumes');
        listeners.length = 0;
    }
    /* Reopened offline: the cached Work may be older or newer than the
     * cached source state (a delayed Work answer, a Work-role ACK refreshing
     * its read time). The base is the cached pair, and the tab shows the
     * source that base belongs to, so a Save is never measured against a
     * video the user did not see. */
    {
        for (const [heldId, stateId] of [['AAA', 'BBB'], ['CCC', 'BBB'], ['BBB', 'BBB']]) {
            globalThis.prksOfflineReadEntity = answer({ value: stateOf(2, stateId), source: 'cache' });
            const ctx = mountedOwner(core.observe({ id: 'W', title: 'Clip', ...video(heldId) }, Date.now() + 1000));
            globalThis.prksMountWorkSourceEditor(ctx, 'W', { editing: true });
            const state = ctx.resources.workSourceEditor;
            await state.preparing;
            assert.deepEqual(state.observed, { revision: 2, identity: identity(stateId) },
                'the base is the cached pair, whatever the Work read time says');
            assert.equal(ctx.entity.provider_id, stateId, 'the tab shows the source its base belongs to');
        }
        /* A cached revision with no source is no base. The cache answer
         * comes back unvalidated, as readThroughEntity returns it offline. */
        globalThis.prksOfflineReadEntity = async () => ({ value: { work_id: 'W', revision: 2 }, source: 'cache' });
        const ctx = mountedOwner(core.observe({ id: 'W', title: 'Clip', ...video('ZZZ') }, Date.now()));
        globalThis.prksMountWorkSourceEditor(ctx, 'W', { editing: true });
        await ctx.resources.workSourceEditor.preparing;
        assert.equal(ctx.resources.workSourceEditor.observed, null, 'a bare revision never becomes a base');
        listeners.length = 0;
    }
    /* Reloaded offline with a queued replacement: the tab shows the pending
     * video over the acknowledged one, so preparing keeps it on screen and
     * measures a Save against the acknowledged pair. An untouched Save is
     * then the queued video again, never a return to the server's. */
    {
        const BBB = 'b'.repeat(11);
        globalThis.prksSetPendingWorkSources([{ operation: 'SET_WORK_SOURCE', entity_type: 'work',
            entity_id: 'W', status: 'pending', payload: { source: { kind: 'video', url: WATCH(BBB) } } }]);
        try {
            globalThis.prksOfflineReadEntity = answer({ value: stateOf(1, 'AAA'), source: 'cache' });
            const shown = globalThis.prksEffectiveWorkSource({ id: 'W', title: 'Clip', ...video('AAA') });
            assert.equal(shown.provider_id, BBB, 'the route publishes the queued video');
            const ctx = mountedOwner(core.observe(shown, Date.now()));
            globalThis.prksMountWorkSourceEditor(ctx, 'W', { editing: true });
            const state = ctx.resources.workSourceEditor;
            await state.preparing;
            assert.equal(ctx.entity.provider_id, BBB, 'the queued video stays on screen');
            assert.deepEqual(state.observed, { revision: 1, identity: identity('AAA') },
                'measured against the acknowledged pair');
            /* Cancelling the replacement by saving the server's video, then
             * choosing it again in the same editor, queues it again: the
             * second Save is measured against the base and the queue, not
             * against the replacement still held in the tab's Work. */
            let rows = [{ op_id: 'op-b', operation: 'SET_WORK_SOURCE', entity_type: 'work',
                entity_id: 'W', status: 'pending', attempt_count: 0,
                payload: { source: { kind: 'video', url: WATCH(BBB) } } }];
            const saves = [];
            globalThis.prksRefreshPendingWorkSources = async () => {
                globalThis.prksSetPendingWorkSources(rows);
                return rows;
            };
            globalThis.prksSync.changed = () => {};
            globalThis.prksSync.store = {
                saveWorkSource: async (_workId, source, observed) => {
                    saves.push(source.url);
                    rows = source.identity === observed.identity ? [] : [{ op_id: 'op-' + saves.length,
                        operation: 'SET_WORK_SOURCE', entity_type: 'work', entity_id: 'W',
                        status: 'pending', attempt_count: 0,
                        payload: { source: { kind: 'video', url: source.url } } }];
                },
            };
            globalThis.prksGetFocusedTabContext = () => ctx;
            globalThis.document = { activeElement: null, getElementById: () => null };
            ctx.ui = { workMetaDraft: { source_url: WATCH('AAA') } };
            await globalThis.prksSaveWorkSource('W');
            assert.deepEqual(rows, [], 'saving the server video cancels the replacement');
            assert.equal(ctx.entity.provider_id, 'AAA', 'and the tab shows the server video again');
            ctx.ui.workMetaDraft.source_url = WATCH(BBB);
            await globalThis.prksSaveWorkSource('W');
            assert.deepEqual(saves, [WATCH('AAA'), WATCH(BBB)], 'choosing the replacement again is saved');
            assert.equal(rows.length, 1, 'and queued again');
            assert.equal(rows[0].payload.source.url, WATCH(BBB));
            assert.equal(ctx.entity.provider_id, BBB, 'the tab shows the queued video');
        } finally {
            globalThis.prksSetPendingWorkSources([]);
            globalThis.prksRefreshPendingWorkSources = async () => [];
            delete globalThis.prksGetFocusedTabContext;
            delete globalThis.document;
            delete globalThis.prksSync.store;
        }
        listeners.length = 0;
    }
    /* The production fallback: a failed network read through the real
     * readThroughEntity answers from IndexedDB without running the validator.
     * A revision-only entry gives no base; a complete pair still does. */
    {
        for (const [cachedState, expected] of [
            [{ work_id: 'W', revision: 2 }, null],
            [stateOf(2, 'BBB'), { revision: 2, identity: identity('BBB') }],
        ]) {
            const cache = createPrksOfflineStore({ dbName: 'prks-offline-v2:test',
                indexedDB: createFakeIndexedDBFactory() });
            const runtime = createPrksOfflineRuntime({ store: cache, window: null,
                prksRequest: async () => { throw new TypeError('Failed to fetch'); } });
            await cache.putEntity('work-source-state', 'W', cachedState);
            globalThis.prksOfflineReadEntity = runtime.readThroughEntity;
            const ctx = mountedOwner(core.observe({ id: 'W', title: 'Clip', ...video('BBB') }, Date.now()));
            globalThis.prksMountWorkSourceEditor(ctx, 'W', { editing: true });
            const state = ctx.resources.workSourceEditor;
            await state.preparing;
            assert.deepEqual(state.observed, expected, expected
                ? 'a complete cached pair still prepares offline'
                : 'a cached revision without its source never becomes a base');
        }
        listeners.length = 0;
    }
    /* A source state answered from the cache adopts nothing after an ACK and
     * keeps the ACK's base. */
    {
        const { ctx, state } = await mounted('ZZZ');
        globalThis.prksOfflineReadEntity = answer({ value: stateOf(2, 'CCC'), source: 'cache' });
        const op = createdNow();
        await later();
        ctx.entity = core.observe({ ...ctx.entity, ...video('BBB') }, Date.now());
        listeners.forEach(fn => fn({ operation: 'SET_WORK_SOURCE', acknowledged: ackAAA, op }));
        await flush(); await flush(); await flush();
        assert.deepEqual(reads.map(r => r[0]), ['work-source-state'], 'no Work is read');
        assert.deepEqual(state.observed, { revision: 1, identity: identity('AAA') }, 'the ACK base stays');
        assert.notEqual(ctx.entity.provider_id, 'CCC');
    }
    listeners.length = 0;
}

/* A note re-read survives a source re-read replacing the Work object, and an
 * open Research Notes editor decides whether the body may become its base. */
async function notesEditorAndSourceReplacement() {
    const listeners = [];
    globalThis.prksSync = { subscribe: fn => { listeners.push(fn); return () => {}; } };
    const emit = event => listeners.forEach(fn => fn(event));
    const opFor = text => ({ op_id: 'n-' + text, operation: 'SET_WORK_RESEARCH_NOTE', entity_id: 'W',
        payload: { text }, ...createdNow() });
    const ackOf = revision => ({ code: 'ACKNOWLEDGED', work_id: 'W', server_revision: revision });
    const ownerWith = (body, extra) => {
        const observed = { research: { value: body, revision: 0, source: 'server' },
            private: { value: '', revision: 0, source: 'server' } };
        const canonical = { id: 'W', text_content: body, private_notes: '', sources: { research: 'server' } };
        return ownerCtx(core.observe({ id: 'W', text_content: body, private_notes: null }, Date.now()),
            Object.assign({ workNotesObserved: observed, workNotesCanonical: canonical }, extra || {}));
    };
    const gated = () => {
        let release;
        const gate = new Promise(resolve => { release = resolve; });
        globalThis.prksOfflineReadEntity = async kind => {
            await gate;
            return kind === 'work-notes-state'
                ? { value: { work_id: 'W', research_note_revision: 2, private_note_revision: 0 }, source: 'server' }
                : { value: { id: 'W', text_content: 'B', private_notes: null }, source: 'server' };
        };
        return release;
    };
    {
        listeners.length = 0;
        const release = gated();
        const op = opFor('A');
        await later();
        const ctx = ownerWith('older');
        globalThis.prksBindWorkNotesSync(ctx);
        emit({ acknowledged: ackOf(1), op });
        await flush();
        /* A source re-read replaces the Work object, notes untouched. */
        ctx.entity = Object.assign({}, ctx.entity, video('CCC'));
        release();
        await flush(); await flush(); await flush();
        assert.equal(ctx.entity.text_content, 'B', 'the note re-read still lands on the replacement Work');
        assert.equal(ctx.entity.provider_id, 'CCC', 'which keeps its source');
        assert.equal(ctx.resources.workNotesObserved.research.revision, 2);
    }
    const appliedTexts = [];
    let refsAtApply = null;
    let ownerAtApply = null;
    globalThis.prksResearchNotesApplyText = (notes, text) => {
        appliedTexts.push(text); notes.text = text;
        refsAtApply = ownerAtApply ? ownerAtApply.entity.research_refs : null;
    };
    const editorWith = text => ({ workId: 'W', drafting: false, pendingSave: false, text,
        editor: null });
    const withEditor = notes => { notes.editor = { value: () => notes.text }; return notes; };
    /* A clean editor showing the ACK's text takes the server body and its revision. */
    {
        listeners.length = 0;
        const release = gated();
        const op = opFor('A');
        await later();
        const notes = withEditor(editorWith('A'));
        const ctx = ownerWith('older', { workNotes: notes });
        ownerAtApply = ctx;
        globalThis.prksBindWorkNotesSync(ctx);
        const read = globalThis.prksOfflineReadEntity;
        globalThis.prksOfflineReadEntity = async (kind, ...rest) => {
            const answer = await read(kind, ...rest);
            if (kind === 'work') answer.value.research_refs = { b: 'concept-B' };
            return answer;
        };
        emit({ acknowledged: ackOf(1), op });
        release();
        await flush(); await flush(); await flush();
        assert.deepEqual(appliedTexts, ['B'], 'the clean editor shows the server body');
        assert.deepEqual(refsAtApply, { b: 'concept-B' }, 'its preview renders with the references for that body');
        assert.equal(ctx.resources.workNotesObserved.research.value, 'B');
        assert.equal(ctx.resources.workNotesObserved.research.revision, 2, 'at the revision read with it');
    }
    /* The tab's Research Notes session decides last: one holding work of
     * its own keeps the base; a clean one is retired with the old text. */
    {
        listeners.length = 0;
        appliedTexts.length = 0;
        const release = gated();
        const op = opFor('A');
        await later();
        const notes = withEditor(editorWith('A'));
        const ctx = ownerWith('older', { workNotes: notes });
        const asked = [];
        globalThis.prksResearchNotesAdoptBody = (_ctx, workId, shown) => { asked.push([workId, shown]); return false; };
        globalThis.prksBindWorkNotesSync(ctx);
        emit({ acknowledged: ackOf(1), op });
        release();
        await flush(); await flush(); await flush();
        delete globalThis.prksResearchNotesAdoptBody;
        assert.deepEqual(asked, [['W', 'A']], 'the session is asked about the text the editor shows');
        assert.deepEqual(appliedTexts, [], 'a session that keeps its text keeps the editor');
        assert.equal(ctx.resources.workNotesObserved.research.revision, 1, 'and the ACK base');
    }
    /* An editor with typing keeps its base, so its next save conflicts. */
    {
        listeners.length = 0;
        appliedTexts.length = 0;
        const release = gated();
        const op = opFor('A');
        await later();
        const notes = withEditor(editorWith('A'));
        const ctx = ownerWith('older', { workNotes: notes });
        globalThis.prksBindWorkNotesSync(ctx);
        emit({ acknowledged: ackOf(1), op });
        notes.text = 'A plus a sentence';
        notes.drafting = true;
        release();
        await flush(); await flush(); await flush();
        assert.deepEqual(appliedTexts, [], 'typing is never replaced');
        assert.equal(ctx.resources.workNotesObserved.research.value, 'A');
        assert.equal(ctx.resources.workNotesObserved.research.revision, 1,
            'the base stays on the ACK, so a save cannot overwrite revision 2 unseen');
        assert.equal(ctx.entity.text_content, 'A');
    }
    /* A refresh of the same Work starts a new route generation: the old
     * route's read adopts nothing into it. */
    {
        listeners.length = 0;
        const release = gated();
        const op = opFor('A');
        await later();
        const ctx = ownerWith('older');
        ctx.generation = 1;
        globalThis.prksBindWorkNotesSync(ctx);
        emit({ acknowledged: ackOf(1), op });
        await flush();
        ctx.generation = 2;
        ctx.entity = core.observe({ id: 'W', text_content: 'reloaded', private_notes: null }, Date.now());
        ctx.resources.workNotesObserved = { research: { value: 'reloaded', revision: 3, source: 'server' },
            private: { value: '', revision: 0, source: 'server' } };
        release();
        await flush(); await flush(); await flush();
        assert.equal(ctx.entity.text_content, 'reloaded', 'the new route keeps its own note');
        assert.equal(ctx.resources.workNotesObserved.research.revision, 3, 'and its own base');
    }
    /* An open Reminders session keeps the private note base the same way. */
    {
        listeners.length = 0;
        let release;
        const gate = new Promise(resolve => { release = resolve; });
        globalThis.prksOfflineReadEntity = async kind => {
            await gate;
            return kind === 'work-notes-state'
                ? { value: { work_id: 'W', research_note_revision: 0, private_note_revision: 2 }, source: 'server' }
                : { value: { id: 'W', text_content: '', private_notes: 'B' }, source: 'server' };
        };
        const op = { op_id: 'p1', operation: 'SET_WORK_PRIVATE_NOTE', entity_id: 'W', payload: { text: 'A' },
            ...createdNow() };
        await later();
        const ctx = ownerWith('');
        ctx.ui = { workPrivateNoteSession: { entityId: 'W', retired: false, draftText: 'A' } };
        globalThis.prksBindWorkNotesSync(ctx);
        emit({ acknowledged: ackOf(1), op });
        release();
        await flush(); await flush(); await flush();
        assert.equal(ctx.resources.workNotesObserved.private.revision, 1, 'the open session keeps the ACK base');
        assert.equal(ctx.entity.private_notes, 'A');
    }
    delete globalThis.prksResearchNotesApplyText;
    listeners.length = 0;
}

/* An unproven entity that could not be dropped keeps the operation queued,
 * and its replay drops it; a list being dropped is never served offline. */
async function failedDeletions() {
    const noteAck = revision => ({ code: 'ACKNOWLEDGED', work_id: 'W', note_kind: 'work-research-note',
        changed: true, server_revision: revision, value_omitted: true });
    for (const [label, seed, run] of [
        ['source Work', async cache => cache.putEntity('work', 'W', { id: 'W', title: 'Clip', ...video('BBB') }),
            (offline, op) => offline.reconcileWorkSource(sourceAck(1, 'AAA'), op)],
        ['source summary', async cache => cache.putEntity('folder', 'F', { id: 'F', works: [{ id: 'W', ...video('BBB') }] }),
            (offline, op) => offline.reconcileWorkSource(sourceAck(1, 'AAA'), op)],
        ['note Work', async cache => cache.putEntity('work', 'W', { id: 'W', text_content: 'B', private_notes: 'P' }),
            (offline, op) => offline.reconcileWorkNote(noteAck(1),
                { operation: 'SET_WORK_RESEARCH_NOTE', entity_id: 'W', payload: { text: 'A' }, ...op })],
    ]) {
        const { cache, offline } = setup();
        const op = createdNow();
        await later();
        await seed(cache);
        const deleteEntity = cache.deleteEntity;
        cache.deleteEntity = async () => { throw new Error('quota'); };
        assert.equal(await run(offline, op), false, label + ': a failed deletion keeps the operation queued');
        cache.deleteEntity = deleteEntity;
        assert.equal(await run(offline, op), true, label + ': the replay drops it and retires');
        const kind = label === 'source summary' ? 'folder' : 'work';
        assert.equal(await cache.getEntity(kind, kind === 'folder' ? 'F' : 'W'), null, label + ': gone');
    }
    {
        const { cache, offline } = setup();
        const op = createdNow();
        await later();
        await cache.putList('works-browse:index', [browseRow('BBB')], '');
        const deleteList = cache.deleteList;
        cache.deleteList = async () => false;
        assert.equal(await offline.reconcileWorkSource(sourceAck(1, 'AAA'), op), false);
        const read = await offline.readThroughList('works-browse:index', '/api/works?projection=browse',
            { domain: 'works-browse', validate: Array.isArray });
        assert.equal(read.source, 'unavailable', 'a list whose deletion failed is not served as a fallback');
        cache.deleteList = deleteList;
        assert.equal(await offline.reconcileWorkSource(sourceAck(1, 'AAA'), op), true, 'the replay drops it');
    }
}

/* A reconcile that starts a new domain generation while a sweep for the old
 * one is still deleting must not leave the domain blocked once it finishes. */
async function sweepSurvivesReconcile() {
    const { cache, offline } = setup();
    await cache.putList('recent:index', [{ id: 'OLD' }], '');
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    const deleteList = cache.deleteList.bind(cache);
    cache.deleteList = async key => { await gate; return deleteList(key); };
    offline.markDomainChanged('recent', { listKeys: ['recent:index'] });
    assert.equal(offline.isDomainBlocked('recent'), true);
    globalThis.prksIsRecentIndexShape = Array.isArray;
    globalThis.prksMergeRecentOpen = (rows, result) => [{ id: result.work_id }, ...rows];
    const swept = offline.currentDomainGeneration('recent');
    const reconciling = offline.reconcileRecentOpen({ work_id: 'W' });
    for (let i = 0; i < 5; i += 1) await later();
    const reconciled = offline.currentDomainGeneration('recent');
    assert.ok(reconciled > swept, 'the reconcile starts a newer generation');
    assert.equal(offline.isDomainBlocked('recent'), true, 'still blocked while the sweep runs');
    release();
    await reconciling;
    for (let i = 0; i < 5; i += 1) await later();
    assert.equal(offline.currentDomainGeneration('recent'), reconciled);
    assert.equal(offline.isDomainBlocked('recent'), false, 'the finished sweep unblocks the newer generation');
}

async function main() {
    await sourceCache();
    await notesCache();
    await parkedSourceOwner();
    await notesOwner();
    await notesOwnerCorrectiveRead();
    await sourceRereadKeepsOtherFamilies();
    await cacheEdges();
    await mountedSourceCorrectiveRead();
    await notesEditorAndSourceReplacement();
    await failedDeletions();
    await sweepSurvivesReconcile();
    console.log('All ' + checks + ' Work source and note ACK ordering checks passed');
}

main().then(() => process.exit(0)).catch(error => { console.error(error); process.exit(1); });
