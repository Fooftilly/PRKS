"use strict";
const strict = require('assert/strict');
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); },
    { get: (_t, k) => (...args) => { checks += 1; return strict[k](...args); } });
const { createFakeIndexedDBFactory } = require('./lib/fake_indexeddb.js');
const { createPrksLocalStore } = require('../../frontend/js/local-store.js');
const { createPrksOfflineStore } = require('../../frontend/js/offline-store.js');
const { createPrksOfflineRuntime } = require('../../frontend/js/offline-runtime.js');
require('../../frontend/js/work-source-state.js');

let sequence = 0;
const uuid = () => '00000000-0000-4000-8000-' + (++sequence).toString(16).padStart(12, '0');
const tick = () => new Promise(resolve => setTimeout(resolve, 1));
async function settle() { for (let i = 0; i < 5; i++) await tick(); }

const WATCH = id => 'https://www.youtube.com/watch?v=' + id;
const op = (workId, url, extra) => Object.assign({
    operation: 'SET_WORK_SOURCE', entity_type: 'work', entity_id: workId,
    status: 'pending', payload: { source: { kind: 'video', url } },
}, extra || {});

/* ---- canonical identity: the URL is a spelling, the id is the thing ---- */
function canonicalIdentity() {
    const c = globalThis.prksCanonicalWorkSource;
    for (const url of [WATCH('ABC'), 'https://youtu.be/ABC',
        'https://www.youtube.com/embed/ABC', 'https://m.youtube.com/watch?v=ABC']) {
        const source = c(url);
        assert.equal(source.provider_id, 'ABC', url);
        assert.equal(source.provider, 'youtube');
        assert.equal(source.source_kind, 'video');
        assert.equal(source.source_url, url, 'the URL is kept as the user wrote it');
    }
    /* Explicit hosts, never substring matching: "notyoutube.com" and
     * "youtube.com.example.org" are not YouTube. */
    for (const bad of ['https://notyoutube.com/watch?v=A', 'https://youtube.com.evil.org/watch?v=A',
        'https://example.com/v', 'ftp://youtube.com/watch?v=A', '', '   ', 'nonsense',
        'https://www.youtube.com/watch', 'https://youtu.be/']) {
        assert.equal(c(bad), null, bad);
    }
    // Identity ignores spelling entirely.
    const id = globalThis.prksWorkSourceIdentity;
    assert.equal(id(c(WATCH('ABC'))), id(c('https://youtu.be/ABC')));
    assert.notEqual(id(c(WATCH('ABC'))), id(c(WATCH('XYZ'))));
}

/* ---- the overlay produces a WHOLE identity, never a half of one ---- */
function effectiveSourceOverlay() {
    const work = { id: 'W-1', title: 'Clip', source_kind: 'video', provider: 'youtube',
        provider_id: 'AAA', source_url: WATCH('AAA'), thumb_url: 'https://img/AAA.jpg' };
    const frozen = JSON.stringify(work);
    globalThis.prksSetPendingWorkSources([op('W-1', WATCH('BBB'))]);

    const effective = globalThis.prksEffectiveWorkSource(work);
    assert.equal(effective.provider_id, 'BBB');
    assert.equal(effective.source_url, WATCH('BBB'));
    assert.equal(effective.provider, 'youtube');
    assert.equal(effective.source_kind, 'video');
    /* THE INVARIANT: never a URL naming one video beside an id naming
     * another. That contradiction is the entire reason this is an aggregate. */
    assert.equal(globalThis.prksYoutubeVideoId(effective.source_url), effective.provider_id,
        'the URL and the id always name the same video');
    assert.equal(effective.title, 'Clip', 'fields outside the aggregate are untouched');
    assert.equal(effective.thumb_url, 'https://img/AAA.jpg',
        'the old thumbnail stays until acknowledgement -- stale presentation, not identity');
    assert.equal(JSON.stringify(work), frozen, 'the acknowledged Work is never mutated');

    // Rows, and Works with no pending source.
    const rows = [{ id: 'W-1', provider_id: 'AAA' }, { id: 'W-2', provider_id: 'ZZZ' }];
    const out = globalThis.prksEffectiveWorkSources(rows);
    assert.equal(out[0].provider_id, 'BBB');
    assert.equal(out[1].provider_id, 'ZZZ');

    // An unparseable pending URL produces no overlay at all.
    globalThis.prksSetPendingWorkSources([op('W-1', 'https://example.com/no')]);
    assert.equal(globalThis.prksEffectiveWorkSource(work).provider_id, 'AAA');
    globalThis.prksSetPendingWorkSources([]);
}

/* ---- the handler's result contract ---- */
function handlerContract() {
    const handler = globalThis.prksWorkSourceSyncHandler;
    const operation = op('W-1', WATCH('BBB'));
    const ack = { work_id: 'W-1', code: 'ACKNOWLEDGED', server_revision: 2, changed: true,
        provider: 'youtube', provider_id: 'BBB', source_kind: 'video', value_omitted: true };
    assert.equal(handler.isResult(ack, operation), true);

    // The URL is never echoed: the ledger is not a second copy of it.
    assert.equal(handler.isResult(Object.assign({ source_url: WATCH('BBB') }, ack), operation),
        false);
    assert.equal(handler.isResult(Object.assign({}, ack, { value_omitted: undefined }), operation),
        false, 'an omission must be DECLARED, never merely absent');
    /* A server answering with a DIFFERENT video than the one requested is a
     * protocol error, not an acknowledgement -- accepting it would let the
     * client believe it applied an identity it never asked for. */
    assert.equal(handler.isResult(Object.assign({}, ack, { provider_id: 'CCC' }), operation),
        false);
    assert.equal(handler.isResult(Object.assign({}, ack, { work_id: 'W-OTHER' }), operation), false);

    const conflict = { work_id: 'W-1', code: 'SOURCE_REVISION_CONFLICT', current_revision: 3,
        current_preview: WATCH('CCC'), current_bytes: 43, requested_bytes: 43 };
    assert.equal(handler.isResult(conflict, operation), true);
    assert.equal(handler.isResult(Object.assign({ source_url: 'x' }, conflict), operation), false,
        'a conflict reports a bounded preview, never the value');
    for (const code of ['ENTITY_NOT_FOUND', 'UNSUPPORTED_SOURCE_TRANSITION']) {
        assert.equal(handler.isResult({ work_id: 'W-1', code }, operation), true, code);
    }
    assert.equal(handler.isResult({ work_id: 'W-1', code: 'SOMETHING' }, operation), false);

    // Every terminal outcome is the user's to resolve.
    assert.deepEqual(handler.terminal(conflict).conflict, {
        code: 'SOURCE_REVISION_CONFLICT', current_revision: 3,
        current_preview: WATCH('CCC'), current_bytes: 43, requested_bytes: 43 });
}

/* ---- one active intent per Work ---- */
async function coalescing() {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    globalThis.prksSync = { store };
    const enqueue = url => store.enqueueOperation({
        operation: 'SET_WORK_SOURCE', entity_type: 'work', entity_id: 'W-1',
        payload: { source: { kind: 'video', url } }, base_revision: 0,
    });
    await enqueue(WATCH('BBB'));
    await enqueue(WATCH('CCC'));
    const rows = (await store.listOperations())
        .filter(r => r.operation === 'SET_WORK_SOURCE');
    assert.equal(rows.length, 2, 'the durable store keeps envelopes immutable');
    /* The pending MAP is what the UI reads, and it holds one intent per Work:
     * the last one wins, so a user who changed their mind twice before the
     * first send sees one answer rather than two. */
    globalThis.prksSetPendingWorkSources(rows);
    assert.equal(globalThis.prksEffectiveWorkSource({ id: 'W-1' }).provider_id, 'CCC');
    delete globalThis.prksSync;
    globalThis.prksSetPendingWorkSources([]);
}

/* ---- acknowledgement reaches every cached representation, coherently ---- */
async function reconciliation() {
    const cache = createPrksOfflineStore({ indexedDB: createFakeIndexedDBFactory() });
    const videoRow = () => ({ id: 'W-1', title: 'Clip', source_kind: 'video',
        provider: 'youtube', provider_id: 'AAA', source_url: WATCH('AAA') });
    await cache.putEntity('work', 'W-1', videoRow());
    await cache.putList('works-browse:index', [videoRow(), { id: 'W-2', provider_id: 'ZZZ' }], '');
    await cache.putList('recent:index', [videoRow()], '');
    await cache.putList('recently-added:index', [videoRow()], '');
    await cache.putEntity('playlist', 'PL1', { id: 'PL1', items: [videoRow()] });
    await cache.putEntity('folder', 'F1', { id: 'F1', works: [videoRow()] });
    const offline = createPrksOfflineRuntime({ store: cache, window: null,
        prksRequest: async () => { throw new Error('no reads in this scenario'); } });

    const source = globalThis.prksCanonicalWorkSource(WATCH('BBB'));
    assert.equal(await offline.reconcileWorkSource({ work_id: 'W-1', source }), true);

    const coherent = row => {
        assert.equal(row.provider_id, 'BBB');
        assert.equal(row.source_url, WATCH('BBB'));
        assert.equal(globalThis.prksYoutubeVideoId(row.source_url), row.provider_id,
            'no row ever names one video while identifying another');
    };
    coherent((await cache.getEntity('work', 'W-1')).value);
    for (const key of ['works-browse:index', 'recent:index', 'recently-added:index']) {
        const rows = (await cache.getList(key)).value;
        coherent(rows.find(r => r.id === 'W-1'));
    }
    assert.equal((await cache.getList('works-browse:index')).value
        .find(r => r.id === 'W-2').provider_id, 'ZZZ', 'other Works are untouched');
    coherent((await cache.getEntity('playlist', 'PL1')).value.items[0]);
    coherent((await cache.getEntity('folder', 'F1')).value.works[0]);

    // A missing cache is nothing to patch, not a failure.
    await cache.deleteEntity('folder', 'F1');
    assert.equal(await offline.reconcileWorkSource(
        { work_id: 'W-1', source: globalThis.prksCanonicalWorkSource(WATCH('CCC')) }), true);

    /* An UNREADABLE cache is a different answer: the operation must be
     * replayed rather than retired believing it patched what it could not
     * read. */
    const unreadable = Object.assign(Object.create(Object.getPrototypeOf(cache)), cache, {
        getEntitiesByKind: async () => null,
    });
    const blocked = createPrksOfflineRuntime({ store: unreadable, window: null,
        prksRequest: async () => { throw new Error('no reads'); } });
    assert.equal(await blocked.reconcileWorkSource(
        { work_id: 'W-1', source: globalThis.prksCanonicalWorkSource(WATCH('DDD')) }), false);
}

/* ---- a GET that began before the acknowledgement must lose ---- */
async function staleRead() {
    const cache = createPrksOfflineStore({ indexedDB: createFakeIndexedDBFactory() });
    const stale = [{ id: 'W-1', provider_id: 'AAA', source_url: WATCH('AAA') }];
    await cache.putEntity('work', 'W-1', { id: 'W-1', provider_id: 'AAA' });
    await cache.putList('works-browse:index', JSON.parse(JSON.stringify(stale)), '');
    let release = null;
    const inFlight = new Promise(resolve => { release = resolve; });
    const offline = createPrksOfflineRuntime({ store: cache, window: null, prksRequest: async () => {
        await inFlight;
        return { ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(stale)) };
    } });

    const reading = offline.readThroughList('works-browse:index', '/api/works?projection=browse',
        { domain: 'works-browse', validate: rows => Array.isArray(rows) });
    await settle();
    assert.equal(await offline.reconcileWorkSource({ work_id: 'W-1',
        source: globalThis.prksCanonicalWorkSource(WATCH('BBB')) }), true);
    release();
    await reading;
    await settle();
    const row = (await cache.getList('works-browse:index')).value.find(r => r.id === 'W-1');
    assert.equal(row.provider_id, 'BBB', 'a stale response cannot beat the acknowledgement');
    assert.equal(row.source_url, WATCH('BBB'));
}

async function main() {
    canonicalIdentity();
    effectiveSourceOverlay();
    handlerContract();
    await coalescing();
    await reconciliation();
    await staleRead();
    console.log('All ' + checks + ' Work source checks passed');
}

main().catch(error => { console.error(error); process.exit(1); });
