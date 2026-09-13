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
const SHORT = id => 'https://youtu.be/' + id;
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
    /* An id is an IDENTIFIER: a bounded token, not "whatever sat after v=".
     * A conflict reports the server's identity EXACTLY while the whole
     * terminal result still has to fit the durable 2 KiB bound, and those two
     * promises only hold together if identity is bounded here, where an id
     * comes into existence. */
    const max = globalThis.PRKS_MAX_PROVIDER_ID_CHARS;
    assert.equal(globalThis.prksYoutubeVideoId(WATCH('B'.repeat(max))), 'B'.repeat(max));
    assert.equal(globalThis.prksYoutubeVideoId(WATCH('dQw4-_9WgXcQ')), 'dQw4-_9WgXcQ',
        'the safe alphabet itself stays accepted');
    for (const bad of ['B'.repeat(max + 1), 'B'.repeat(3000), '%01'.repeat(400),
        'a%20b', 'ab%22cd', 'ab%5Ccd', 'a.b', 'a+b']) {
        assert.equal(globalThis.prksYoutubeVideoId(WATCH(bad)), '',
            'an id that could not be reported back intact is no id: ' + bad.slice(0, 12));
        assert.equal(c(WATCH(bad)), null);
    }
    /* Every spelling is bounded, not only the query one -- `searchParams`
     * decodes percent-escapes and `pathname` does not, so a bound applied to
     * one and not the other would be two parsers again. */
    assert.equal(globalThis.prksYoutubeVideoId(SHORT('B'.repeat(max + 1))), '');
    assert.equal(globalThis.prksYoutubeVideoId(
        'https://www.youtube.com/embed/' + 'B'.repeat(max + 1)), '');

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
    /* The acknowledgement STATES the stored row. The client copies it rather
     * than rebuilding it from its own operation -- `urldate` it cannot derive
     * at all, and on a convergent write the stored URL is deliberately not the
     * one that was asked for. */
    const ack = { work_id: 'W-1', code: 'ACKNOWLEDGED', server_revision: 2, changed: true,
        provider: 'youtube', provider_id: 'BBB', source_kind: 'video',
        source_url: WATCH('BBB'), thumb_url: null, urldate: '2026-09-13' };
    assert.equal(handler.isResult(ack, operation), true);

    for (const missing of ['source_url', 'thumb_url', 'urldate']) {
        const partial = Object.assign({}, ack);
        delete partial[missing];
        assert.equal(handler.isResult(partial, operation), false,
            'every column the write touches must be stated: ' + missing);
    }
    /* An echoed row whose URL and id name different videos is the exact
     * contradiction this aggregate exists to prevent, and it is not made
     * acceptable by arriving from the server. */
    assert.equal(handler.isResult(Object.assign({}, ack, { provider_id: 'CCC' }), operation),
        false);
    assert.equal(handler.isResult(
        Object.assign({}, ack, { source_url: WATCH('CCC'), provider_id: 'CCC' }), operation),
        false, 'and it must be the video THIS operation named');
    assert.equal(handler.isResult(Object.assign({}, ack, { work_id: 'W-OTHER' }), operation), false);

    /* A convergent write: the same video, the server's own spelling. The
     * client must be able to accept a URL it did not send. */
    const convergent = Object.assign({}, ack, { changed: false, source_url: SHORT('BBB'),
        thumb_url: 'https://img/BBB.jpg' });
    assert.equal(handler.isResult(convergent, operation), true);

    const conflict = { work_id: 'W-1', code: 'SOURCE_REVISION_CONFLICT', current_revision: 3,
        current_provider: 'youtube', current_provider_id: 'CCC',
        current_preview: WATCH('CCC'), current_bytes: 43, requested_bytes: 43 };
    assert.equal(handler.isResult(conflict, operation), true);
    assert.equal(handler.isResult(Object.assign({ source_url: 'x' }, conflict), operation), false,
        'a conflict reports a bounded preview, never the value');
    /* The IDENTITY is reported exactly and is required. The preview is display
     * text that `fit_terminal_result` may shorten, so a client that had to
     * parse it to learn which video the server holds would be deriving
     * identity from a value designed to be truncated. */
    for (const missing of ['current_provider', 'current_provider_id']) {
        const partial = Object.assign({}, conflict);
        delete partial[missing];
        assert.equal(handler.isResult(partial, operation), false, missing);
    }
    assert.equal(globalThis.prksWorkSourceConflictIdentity(conflict),
        globalThis.prksWorkSourceIdentity(globalThis.prksCanonicalWorkSource(WATCH('CCC'))),
        'and it is spelled the same way an identity from a Work record is');
    for (const code of ['ENTITY_NOT_FOUND', 'UNSUPPORTED_SOURCE_TRANSITION']) {
        assert.equal(handler.isResult({ work_id: 'W-1', code }, operation), true, code);
    }
    assert.equal(handler.isResult({ work_id: 'W-1', code: 'SOMETHING' }, operation), false);

    // Every terminal outcome is the user's to resolve.
    assert.deepEqual(handler.terminal(conflict).conflict, {
        code: 'SOURCE_REVISION_CONFLICT', current_revision: 3,
        current_provider: 'youtube', current_provider_id: 'CCC',
        current_preview: WATCH('CCC'), current_bytes: 43, requested_bytes: 43 });
}

/* ---- one active intent per Work ---- */
async function coalescing() {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    globalThis.prksSync = { store };
    const identity = url => globalThis.prksWorkSourceIdentity(
        globalThis.prksCanonicalWorkSource(url));
    const save = url => store.saveWorkSource('W-1',
        { kind: 'video', url, identity: identity(url) },
        { identity: identity(WATCH('AAA')), revision: 0 });
    const sourceRows = async () => (await store.listOperations())
        .filter(r => r.operation === 'SET_WORK_SOURCE');

    /* A -> B -> C is ONE intent naming C. Two rows sharing one base revision
     * is the defect: the coordinator sends B, the revision advances, and the
     * user's own C then arrives stale and conflicts with an edit they had
     * already replaced -- while the screen said C the whole time. */
    await save(WATCH('BBB'));
    await save(WATCH('CCC'));
    let rows = await sourceRows();
    assert.equal(rows.length, 1, 'one unsent intent per Work');
    assert.equal(rows[0].payload.source.url, WATCH('CCC'));
    assert.equal(rows[0].base_revision, 0, 'still measured against the acknowledged base');
    globalThis.prksSetPendingWorkSources(rows);
    assert.equal(globalThis.prksEffectiveWorkSource({ id: 'W-1' }).provider_id, 'CCC');

    // The same video in the same spelling keeps the row rather than minting a
    // second op_id -- a retry must not become a second ledger entry.
    const before = rows[0].op_id;
    await save(WATCH('CCC'));
    rows = await sourceRows();
    assert.equal(rows.length, 1);
    assert.equal(rows[0].op_id, before);

    // A -> B -> A is not two changes; it is none.
    await save(SHORT('AAA'));
    assert.equal((await sourceRows()).length, 0,
        'returning to the acknowledged video leaves no intent at all');

    /* A POSSIBLY SENT row is immutable. It may already be in the server's
     * ledger, so rewriting it would make one operation mean two things. */
    await save(WATCH('BBB'));
    const claimed = (await sourceRows())[0];
    await store.updateOperationSyncState(claimed.op_id, { status: 'pending', last_error: 'x' });
    await store.claimOperation(claimed.op_id);
    await assert.rejects(() => save(WATCH('DDD')), e => e.prksLocalStoreCode === 'scope_busy');
    rows = await sourceRows();
    assert.equal(rows.length, 1);
    assert.equal(rows[0].payload.source.url, WATCH('BBB'), 'the sent intent is untouched');

    /* A history with more than one active row is AMBIGUOUS, not a choice.
     * `getAll()` order is not a decision, so a store written before coalescing
     * existed must refuse rather than resolve it differently per device. */
    const legacy = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    for (const url of [WATCH('BBB'), WATCH('CCC')]) {
        await legacy.enqueueOperation({
            operation: 'SET_WORK_SOURCE', entity_type: 'work', entity_id: 'W-9',
            payload: { source: { kind: 'video', url } }, base_revision: 0,
        });
    }
    await assert.rejects(() => legacy.saveWorkSource('W-9',
        { kind: 'video', url: WATCH('DDD'), identity: identity(WATCH('DDD')) },
        { identity: identity(WATCH('AAA')), revision: 0 }),
        e => e.prksLocalStoreCode === 'scope_busy' && /2 unsynchronized/.test(e.message));
    assert.equal((await legacy.listOperations()).length, 2,
        'and it repairs nothing by guesswork -- the rows are user intent');

    delete globalThis.prksSync;
    globalThis.prksSetPendingWorkSources([]);
}

/* ---- a source conflict the user can actually answer ---- */
async function conflictResolution() {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    const identity = url => globalThis.prksWorkSourceIdentity(
        globalThis.prksCanonicalWorkSource(url));
    const enqueue = () => store.saveWorkSource('W-1',
        { kind: 'video', url: WATCH('BBB'), identity: identity(WATCH('BBB')) },
        { identity: identity(WATCH('AAA')), revision: 0 });

    /* "Apply my source" re-sends the SAME intent against the revision the
     * server reported. The reappliable codes are per FAMILY: this conflict is
     * SOURCE_REVISION_CONFLICT, and a store that only knew the field-scoped
     * REVISION_CONFLICT offered the button and then threw. */
    let row = await enqueue();
    await store.updateOperationSyncState(row.op_id, { status: 'conflict',
        server_result: { code: 'SOURCE_REVISION_CONFLICT', current_revision: 7,
            current_provider: 'youtube', current_provider_id: 'CCC',
            current_preview: WATCH('CCC'), current_bytes: 43, requested_bytes: 43 } });
    const replacement = await store.resolveConflict(row.op_id, true);
    assert.ok(replacement, 'the conflict is reappliable');
    assert.equal(replacement.payload.source.url, WATCH('BBB'), 'the same intent');
    assert.equal(replacement.base_revision, 7, 'against the revision the server reported');
    assert.notEqual(replacement.op_id, row.op_id, 'a new operation, never a rewritten one');
    assert.equal(await store.getOperation(row.op_id), null, 'and the conflict is retired');

    // "Use server" discards the intent and creates nothing.
    row = await store.saveWorkSource('W-1',
        { kind: 'video', url: WATCH('DDD'), identity: identity(WATCH('DDD')) },
        { identity: identity(WATCH('BBB')), revision: 7 });
    await store.updateOperationSyncState(row.op_id, { status: 'conflict',
        server_result: { code: 'SOURCE_REVISION_CONFLICT', current_revision: 9,
            current_provider: 'youtube', current_provider_id: 'EEE',
            current_preview: WATCH('EEE'), current_bytes: 43, requested_bytes: 43 } });
    assert.equal(await store.resolveConflict(row.op_id, false), null);
    assert.equal((await store.listOperations()).length, 0);

    /* After Apply, the replacement is still NEVER SENT -- so it is still
     * coalescible, and the base it must coalesce against is the SERVER's, not
     * the one the editor started from. */
    row = await store.saveWorkSource('W-2',
        { kind: 'video', url: WATCH('BBB'), identity: identity(WATCH('BBB')) },
        { identity: identity(WATCH('AAA')), revision: 0 });
    await store.updateOperationSyncState(row.op_id, { status: 'conflict',
        server_result: { code: 'SOURCE_REVISION_CONFLICT', current_revision: 4,
            current_provider: 'youtube', current_provider_id: 'CCC',
            current_preview: WATCH('CCC'), current_bytes: 43, requested_bytes: 43 } });
    const applied = await store.resolveConflict(row.op_id, true);
    const serverBase = { identity: identity(WATCH('CCC')), revision: applied.base_revision };

    // Changing one's mind again rewrites it against the SERVER's revision.
    const rewritten = await store.saveWorkSource('W-2',
        { kind: 'video', url: WATCH('DDD'), identity: identity(WATCH('DDD')) }, serverBase);
    assert.equal(rewritten.base_revision, 4,
        'not the revision the editor held before the conflict');
    assert.equal((await store.listOperations())
        .filter(r => r.entity_id === 'W-2').length, 1, 'still one intent');

    /* And choosing the SERVER's own video is now a cancellation. With a stale
     * base the editor would have read it as a change and sent it, conflicting
     * with C all over again. */
    assert.equal(await store.saveWorkSource('W-2',
        { kind: 'video', url: SHORT('CCC'), identity: identity(SHORT('CCC')) }, serverBase), null);
    assert.equal((await store.listOperations()).filter(r => r.entity_id === 'W-2').length, 0);

    /* Codes that name no revision to overwrite are NOT reappliable: there is
     * nothing to apply against, and offering it would loop forever. */
    for (const code of ['ENTITY_NOT_FOUND', 'UNSUPPORTED_SOURCE_TRANSITION']) {
        const stuck = await store.saveWorkSource('W-1',
            { kind: 'video', url: WATCH('FFF'), identity: identity(WATCH('FFF')) },
            { identity: identity(WATCH('BBB')), revision: 7 });
        await store.updateOperationSyncState(stuck.op_id,
            { status: 'conflict', server_result: { code } });
        await assert.rejects(() => store.resolveConflict(stuck.op_id, true),
            e => e.prksLocalStoreCode === 'invalid_resolution', code);
        assert.equal(await store.resolveConflict(stuck.op_id, false), null);
    }
}

/* ---- the exact payload shape gets the URL's own allowance ---- */
async function payloadAllowance() {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    const limit = globalThis.PRKS_LOCAL_WORK_SOURCE_URL_BYTES;
    /* The server accepts a URL up to this many BYTES, so the durable store has
     * to accept the same one: a value savable online and impossible offline is
     * the split contract local-first exists to remove. What is bounded is the
     * URL ITSELF -- the envelope's own keys, and any JSON escaping, must not
     * eat into the user's allowance. */
    const base = WATCH('BBB') + '&x=';
    const padded = base + 'p'.repeat(limit - base.length);
    assert.equal(Buffer.byteLength(padded, 'utf8'), limit, 'exactly at the stated limit');
    assert.ok(Buffer.byteLength(JSON.stringify({ source: { kind: 'video', url: padded } }), 'utf8')
        > limit, 'while the serialized envelope is over the generic bound');
    const saved = await store.enqueueOperation({
        operation: 'SET_WORK_SOURCE', entity_type: 'work', entity_id: 'W-1',
        payload: { source: { kind: 'video', url: padded } }, base_revision: 0,
    });
    assert.equal(saved.payload.source.url, padded);

    // The allowance belongs to an exact SHAPE, so it cannot be used to smuggle
    // an unbounded payload.
    await assert.rejects(() => store.enqueueOperation({
        operation: 'SET_WORK_SOURCE', entity_type: 'work', entity_id: 'W-2',
        payload: { source: { kind: 'video', url: padded }, extra: 'x'.repeat(1024) },
        base_revision: 0,
    }), e => e.prksLocalStoreCode === 'payload_too_large');
    await assert.rejects(() => store.enqueueOperation({
        operation: 'SET_WORK_SOURCE', entity_type: 'work', entity_id: 'W-3',
        payload: { source: { kind: 'video', url: 'u'.repeat(limit + 1) } }, base_revision: 0,
    }), e => e.prksLocalStoreCode === 'payload_too_large');
}

/* ---- acknowledgement reaches every cached representation, coherently ---- */
async function reconciliation() {
    const cache = createPrksOfflineStore({ indexedDB: createFakeIndexedDBFactory() });
    /* A cached video row carries the identity AND the presentation the server
     * derives from it: the thumbnail of the video it is, and the date it was
     * accessed. Both are rewritten by a source change. */
    const videoRow = () => ({ id: 'W-1', title: 'Clip', source_kind: 'video',
        provider: 'youtube', provider_id: 'AAA', source_url: WATCH('AAA'),
        thumb_url: 'https://img/AAA.jpg', urldate: '2024-01-01' });
    // A browse row carries `thumb_url` but has no `urldate` column at all.
    const browseRow = () => { const r = videoRow(); delete r.urldate; return r; };
    await cache.putEntity('work', 'W-1', videoRow());
    await cache.putList('works-browse:index', [browseRow(), { id: 'W-2', provider_id: 'ZZZ' }], '');
    await cache.putList('recent:index', [browseRow()], '');
    await cache.putList('recently-added:index', [browseRow()], '');
    await cache.putEntity('playlist', 'PL1', { id: 'PL1', items: [videoRow()] });
    await cache.putEntity('folder', 'F1', { id: 'F1', works: [videoRow()] });
    await cache.putEntity('work-source-state', 'W-1', { work_id: 'W-1', revision: 4 });
    const offline = createPrksOfflineRuntime({ store: cache, window: null,
        prksRequest: async () => { throw new Error('no reads in this scenario'); } });

    // Exactly the shape an acknowledgement carries.
    const ack = { work_id: 'W-1', source: { source_kind: 'video', provider: 'youtube',
        provider_id: 'BBB', source_url: WATCH('BBB'), thumb_url: null, urldate: '2026-09-13' },
        server_revision: 5 };
    assert.equal(await offline.reconcileWorkSource(ack), true);

    const coherent = row => {
        assert.equal(row.provider_id, 'BBB');
        assert.equal(row.source_url, WATCH('BBB'));
        assert.equal(globalThis.prksYoutubeVideoId(row.source_url), row.provider_id,
            'no row ever names one video while identifying another');
        /* The PREVIOUS video's image is not presentation that may lag: a row
         * claiming video B while showing video A's picture is a lie the user
         * can see, and the server has already cleared it. */
        assert.equal(row.thumb_url, null, 'the old video\'s thumbnail is gone');
    };
    const work = (await cache.getEntity('work', 'W-1')).value;
    coherent(work);
    assert.equal(work.urldate, '2026-09-13', 'the server\'s own access date, copied');
    for (const key of ['works-browse:index', 'recent:index', 'recently-added:index']) {
        const rows = (await cache.getList(key)).value;
        const row = rows.find(r => r.id === 'W-1');
        coherent(row);
        assert.equal('urldate' in row, false,
            'a column this projection does not carry is never invented');
    }
    assert.equal((await cache.getList('works-browse:index')).value
        .find(r => r.id === 'W-2').provider_id, 'ZZZ', 'other Works are untouched');
    coherent((await cache.getEntity('playlist', 'PL1')).value.items[0]);
    coherent((await cache.getEntity('folder', 'F1')).value.works[0]);

    /* The base the NEXT edit is measured against. Left at 4, a second change
     * would be created against a revision the server has already moved past,
     * and the user would conflict with their own previous edit. */
    assert.equal((await cache.getEntity('work-source-state', 'W-1')).value.revision, 5);

    // An acknowledgement older than the cache has been superseded.
    await offline.reconcileWorkSource(Object.assign({}, ack, { server_revision: 2 }));
    assert.equal((await cache.getEntity('work-source-state', 'W-1')).value.revision, 5,
        'the base never moves backwards');

    // A missing cache is nothing to patch, not a failure.
    await cache.deleteEntity('folder', 'F1');
    assert.equal(await offline.reconcileWorkSource({ work_id: 'W-1', server_revision: 6,
        source: { source_kind: 'video', provider: 'youtube', provider_id: 'CCC',
            source_url: WATCH('CCC'), thumb_url: null, urldate: '2026-09-13' } }), true);

    /* An UNREADABLE cache is a different answer: the operation must be
     * replayed rather than retired believing it patched what it could not
     * read. */
    const unreadable = Object.assign(Object.create(Object.getPrototypeOf(cache)), cache, {
        getEntitiesByKind: async () => null,
    });
    const blocked = createPrksOfflineRuntime({ store: unreadable, window: null,
        prksRequest: async () => { throw new Error('no reads'); } });
    assert.equal(await blocked.reconcileWorkSource({ work_id: 'W-1', server_revision: 7,
        source: { source_kind: 'video', provider: 'youtube', provider_id: 'DDD',
            source_url: WATCH('DDD'), thumb_url: null, urldate: '2026-09-13' } }), false);
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
    assert.equal(await offline.reconcileWorkSource({ work_id: 'W-1', server_revision: 1,
        source: { source_kind: 'video', provider: 'youtube', provider_id: 'BBB',
            source_url: WATCH('BBB'), thumb_url: null, urldate: '2026-09-13' } }), true);
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
    await conflictResolution();
    await payloadAllowance();
    await reconciliation();
    await staleRead();
    console.log('All ' + checks + ' Work source checks passed');
}

main().catch(error => { console.error(error); process.exit(1); });
