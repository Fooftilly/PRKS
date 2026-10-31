#!/usr/bin/env node
/**
 * Browser scope (#629 L-F-1): which account's browser storage a page may open.
 *
 * Fail closed: only a confirmed session gives a scope; an unreachable server
 * reuses the last confirmed one; every other answer gives none and forgets
 * it. The service worker reads the same record.
 */
'use strict';

const path = require('path');

const rootDir = path.resolve(__dirname, '..', '..');
const scopeApi = require(path.join(rootDir, 'frontend/js/browser-scope.js'));
const sw = require(path.join(rootDir, 'frontend/sw.js'));
const { createFakeIndexedDBFactory } = require(path.join(rootDir, 'tests/browser/lib/fake_indexeddb.js'));
const { createPrksLocalStore } = require(path.join(rootDir, 'frontend/js/local-store.js'));
const { createPrksOfflineStore } = require(path.join(rootDir, 'frontend/js/offline-store.js'));
require(path.join(rootDir, 'frontend/js/sync-runtime.js'));
const createPrksSyncRuntime = globalThis.createPrksSyncRuntime;

let passed = 0;
let failed = 0;

function record(name, ok, detail) {
    if (ok) passed += 1;
    else failed += 1;
    console.log((ok ? 'PASS  ' : 'FAIL  ') + name + (detail ? ' ' + detail : ''));
}

const SESSION_A = {
    auth_mode: 'session',
    instance_id: '11111111-1111-4111-8111-111111111111',
    user: { id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', display_name: 'A' },
    library: { id: '22222222-2222-4222-8222-222222222222', name: 'L', role: 'owner', epoch: 1 },
    expires_at: '2026-11-01T00:00:00+00:00',
};
const SESSION_B = Object.assign({}, SESSION_A, {
    user: { id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', display_name: 'B' },
});
const KEY_A = SESSION_A.instance_id + '.' + SESSION_A.library.id + '.' + SESSION_A.user.id;

function json(body, status) {
    return new Response(JSON.stringify(body), { status: status || 200, headers: { 'Content-Type': 'application/json' } });
}

/** A fetch that answers /api/session from `answers` in order (the last one repeats). */
function sessionFetch(answers) {
    const calls = [];
    const fetchImpl = function (url) {
        calls.push(url);
        const next = answers.length > 1 ? answers.shift() : answers[0];
        return typeof next === 'function' ? next() : Promise.resolve(next());
    };
    fetchImpl.calls = calls;
    return fetchImpl;
}

const ok = (body) => () => json(body, 200);
const status = (code, body) => () => json(body || { error: 'x' }, code);
const offline = () => () => Promise.reject(new TypeError('Failed to fetch'));

async function testResolve() {
    const idb = createFakeIndexedDBFactory();
    const a = scopeApi.createPrksBrowserScope({ indexedDB: idb, fetchImpl: sessionFetch([ok(SESSION_A)]) });
    const scope = await a.resolve();
    record('a confirmed session gives its scope', !!scope && scope.key === KEY_A, scope && scope.key);
    record('the scoped store name carries the scope key',
        (await a.dbName('local')) === 'prks-local-v2:' + KEY_A
        && (await a.dbName('offline')) === 'prks-offline-v2:' + KEY_A
        && (await a.dbName('pdf')) === 'prks-pdf-v2:' + KEY_A);
    record('an unknown store kind has no name', (await a.dbName('other')) === null);

    // Offline reload: the last confirmed scope, read from the record.
    const b = scopeApi.createPrksBrowserScope({ indexedDB: idb, fetchImpl: sessionFetch([offline()]) });
    const reused = await b.resolve();
    record('an unreachable server reuses the last confirmed scope', !!reused && reused.key === KEY_A);
    const c = scopeApi.createPrksBrowserScope({ indexedDB: idb, fetchImpl: sessionFetch([status(503)]) });
    record('a 503 is unreachable too', !!(await c.resolve()));

    // The service worker reads the same record.
    record('the worker reads the scoped pdf cache from the record',
        (await sw.readScopedPdfCacheName(idb)) === 'prks-pdf-v2:' + KEY_A);

    // Any refusal gives no scope and forgets the record.
    for (const [label, answer] of [['401', status(401, { error: 'not_authenticated' })],
        ['403', status(403, { error: 'forbidden' })], ['404', status(404, { error: 'not_found' })]]) {
        const fresh = createFakeIndexedDBFactory();
        await scopeApi.createPrksBrowserScope({ indexedDB: fresh, fetchImpl: sessionFetch([ok(SESSION_A)]) }).resolve();
        const refused = scopeApi.createPrksBrowserScope({ indexedDB: fresh, fetchImpl: sessionFetch([answer]) });
        record('a ' + label + ' gives no scope', (await refused.resolve()) === null);
        record('a ' + label + ' opens no store name', (await refused.dbName('local')) === null);
        const after = scopeApi.createPrksBrowserScope({ indexedDB: fresh, fetchImpl: sessionFetch([offline()]) });
        record('after a ' + label + ' an offline reload opens nothing', (await after.resolve()) === null);
        record('after a ' + label + ' the worker has no pdf cache', (await sw.readScopedPdfCacheName(fresh)) === null);
    }

    const none = scopeApi.createPrksBrowserScope({ indexedDB: createFakeIndexedDBFactory(), fetchImpl: sessionFetch([offline()]) });
    record('offline with no record gives no scope', (await none.resolve()) === null);

    for (const [label, body] of [['no user', Object.assign({}, SESSION_A, { user: null })],
        ['no library', Object.assign({}, SESSION_A, { library: {} })],
        ['an unsafe id', Object.assign({}, SESSION_A, { instance_id: 'a:b/c' })]]) {
        const odd = scopeApi.createPrksBrowserScope({ indexedDB: createFakeIndexedDBFactory(), fetchImpl: sessionFetch([ok(body)]) });
        record('a session body with ' + label + ' gives no scope', (await odd.resolve()) === null);
    }

    const noIdb = scopeApi.createPrksBrowserScope({ indexedDB: null, fetchImpl: sessionFetch([ok(SESSION_A)]) });
    record('a confirmed session still scopes the page without the record',
        !!(await noIdb.resolve()));
}

async function testVerify() {
    const answers = [ok(SESSION_A), ok(SESSION_A), status(401, { error: 'not_authenticated' }), offline(), ok(SESSION_A), ok(SESSION_B), ok(SESSION_A)];
    const scope = scopeApi.createPrksBrowserScope({ indexedDB: createFakeIndexedDBFactory(), fetchImpl: sessionFetch(answers) });
    const events = [];
    scope.subscribe((event) => events.push(event.type));
    await scope.resolve();
    record('verify: the same session', (await scope.verify()) === 'same');
    record('verify: signed out', (await scope.verify()) === 'signed_out');
    record('signed out does not retire the page', !scope.isRetired() && !!scope.current());
    record('verify: unreachable', (await scope.verify()) === 'unreachable');
    record('verify: signed in again as the same account', (await scope.verify()) === 'same');
    record('verify: another account', (await scope.verify()) === 'changed');
    record('another account retires the page', scope.isRetired() && scope.current() === null);
    record('retiring is announced once', events.length === 1 && events[0] === 'retired', events.join(','));
    record('a retired page opens no store', (await scope.dbName('local')) === null);
    record('a retired page stays retired', (await scope.verify()) === 'changed');

    const unbound = scopeApi.createPrksBrowserScope({ indexedDB: createFakeIndexedDBFactory(), fetchImpl: sessionFetch([status(401)]) });
    record('a page without a scope is unbound', (await unbound.verify()) === 'unbound');
}

/** A session stub for stores and the runtime. */
function stubScope(key) {
    const listeners = new Set();
    let retired = false;
    const scope = { key: key };
    return {
        verifyAnswers: [],
        resolve: async () => (key ? scope : null),
        current: () => (retired || !key ? null : scope),
        isRetired: () => retired,
        dbName: async (kind) => (key && !retired ? scopeApi.PRKS_SCOPE_STORE_BASES[kind] + ':' + key : null),
        subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
        async verify() { return this.verifyAnswers.length ? this.verifyAnswers.shift() : 'same'; },
        retire() { retired = true; listeners.forEach((fn) => fn({ type: 'retired' })); },
    };
}

async function testStores() {
    const idb = createFakeIndexedDBFactory();
    const uuid = (() => { let n = 0; return () => '00000000-0000-4000-8000-' + String(++n).padStart(12, '0'); })();
    const a = createPrksLocalStore({ indexedDB: idb, uuid, scope: stubScope('A') });
    const b = createPrksLocalStore({ indexedDB: idb, uuid, scope: stubScope('B') });
    await a.recordWorkOpened('W-1', '2026-10-31T09:00:00.000Z');
    record('each scope has its own queue database',
        idb.__databases.has('prks-local-v2:A') && !idb.__databases.has('prks-local-v2:B'));
    record('another scope never sees the queue', (await b.listOperations()).length === 0
        && (await a.listOperations()).length === 1);
    record('no unscoped queue database exists', !idb.__databases.has('prks-local-v1'));

    const none = createPrksLocalStore({ indexedDB: idb, uuid, scope: stubScope(null) });
    let refused = null;
    try { await none.recordWorkOpened('W-2', '2026-10-31T09:00:00.000Z'); } catch (err) { refused = err; }
    record('without a scope a write is refused, never kept unscoped', !!refused && refused.prksLocalStoreCode === 'unavailable',
        refused && refused.prksLocalStoreCode);
    record('without a scope the store is unavailable', (await none.isAvailable()) === false);

    const retiring = stubScope('C');
    const c = createPrksLocalStore({ indexedDB: idb, uuid, scope: retiring });
    await c.recordWorkOpened('W-3', '2026-10-31T09:00:00.000Z');
    retiring.retire();
    let afterRetire = null;
    try { await c.listOperations(); } catch (err) { afterRetire = err; }
    record('a retired scope closes its queue', !!afterRetire);
    const reopened = createPrksLocalStore({ indexedDB: idb, uuid, scope: stubScope('C') });
    record('retiring keeps every row', (await reopened.listOperations()).length === 1);

    const cacheA = createPrksOfflineStore({ indexedDB: idb, scope: stubScope('A') });
    const cacheNone = createPrksOfflineStore({ indexedDB: idb, scope: stubScope(null) });
    await cacheA.putEntity('work', 'W-1', { title: 'T' }, '1');
    record('each scope has its own offline cache', idb.__databases.has('prks-offline-v2:A'));
    record('without a scope the cache is unavailable', (await cacheNone.isAvailable()) === false
        && (await cacheNone.putEntity('work', 'W-1', { title: 'T' }, '1')) === false);
}

function fakeOp(store) {
    return store.recordWorkOpened('W-1', '2026-10-31T09:00:00.000Z');
}

async function testHold() {
    const handlers = {
        MARK_WORK_OPENED: {
            isResult: (data) => !!data && typeof data.code === 'string' && (data.code === 'ACKNOWLEDGED' || data.code === 'REJECTED'),
            reconcile: async () => true,
            terminal: () => ({ discard: 'refused' }),
        },
    };
    const uuid = (() => { let n = 0; return () => '00000000-0000-4000-8000-' + String(++n).padStart(12, '0'); })();

    async function runWith(answer, verifyAnswers) {
        const store = createPrksLocalStore({ dbName: 'prks-local-v2:test', indexedDB: createFakeIndexedDBFactory(), uuid });
        await fakeOp(store);
        const session = stubScope('A');
        session.verifyAnswers = (verifyAnswers || []).slice();
        const sent = [];
        const runtime = createPrksSyncRuntime({
            store, handlers, online: () => true, session,
            request: async (url, init) => { sent.push(JSON.parse(init.body)); return answer(); },
        });
        await runtime.wake();
        runtime.stop();
        return { store, runtime, sent, rows: await store.listOperations() };
    }

    for (const [label, answer, reason] of [
        ['401', () => json({ error: 'not_authenticated', code: 'not_authenticated' }, 401), 'signed_out'],
        ['403', () => json({ error: 'forbidden', code: 'forbidden' }, 403), 'refused'],
        ['an epoch refusal', () => json({ error: 'LIBRARY_EPOCH_CHANGED', code: 'LIBRARY_EPOCH_CHANGED' }, 409), 'epoch'],
        ['a missing epoch', () => json({ error: 'LIBRARY_EPOCH_REQUIRED', code: 'LIBRARY_EPOCH_REQUIRED' }, 400), 'epoch'],
    ]) {
        const r = await runWith(answer);
        record('a ' + label + ' holds the queue', r.runtime.held() === reason, String(r.runtime.held()));
        record('a ' + label + ' keeps the row pending, never settled',
            r.rows.length === 1 && r.rows[0].status === 'pending' && !r.rows[0].server_result, JSON.stringify(r.rows[0] && r.rows[0].status));
        record('a ' + label + ' gives back the never-run attempt', r.sent.length === 1 && r.rows[0].attempt_count === 0,
            String(r.rows[0] && r.rows[0].attempt_count));
    }

    const signedOut = await runWith(() => json({ code: 'ACKNOWLEDGED' }), ['signed_out']);
    record('a signed-out session sends nothing', signedOut.sent.length === 0 && signedOut.runtime.held() === 'signed_out');
    record('a held row keeps its attempt count', signedOut.rows[0].attempt_count === 0, String(signedOut.rows[0].attempt_count));

    const changed = await runWith(() => json({ code: 'ACKNOWLEDGED' }), ['changed']);
    record('another account signed in: nothing is sent', changed.sent.length === 0 && changed.runtime.held() === 'changed');
    record('another account signed in: the row stays', changed.rows.length === 1 && changed.rows[0].status === 'pending');

    const unreachable = await runWith(() => json({ code: 'ACKNOWLEDGED' }), ['unreachable']);
    record('an unreachable session check sends nothing and does not hold',
        unreachable.sent.length === 0 && unreachable.runtime.held() === null);

    // Held, then signed in again: delivered.
    const store = createPrksLocalStore({ dbName: 'prks-local-v2:test', indexedDB: createFakeIndexedDBFactory(), uuid });
    await fakeOp(store);
    const session = stubScope('A');
    session.verifyAnswers = ['signed_out'];
    const sent = [];
    const runtime = createPrksSyncRuntime({
        store, handlers, online: () => true, session,
        request: async (url, init) => { sent.push(init.body); return json({ code: 'ACKNOWLEDGED', server_revision: 1 }); },
    });
    await runtime.wake();
    record('held while signed out', runtime.held() === 'signed_out' && sent.length === 0);
    await runtime.wake();
    runtime.stop();
    record('signed in again: the held row is delivered', sent.length === 1 && runtime.held() === null
        && (await store.listOperations()).length === 0);

    // A page whose scope another account replaced sends nothing from then on.
    const retiring = stubScope('A');
    const store2 = createPrksLocalStore({ dbName: 'prks-local-v2:test', indexedDB: createFakeIndexedDBFactory(), uuid });
    await fakeOp(store2);
    const sent2 = [];
    const runtime2 = createPrksSyncRuntime({
        store: store2, handlers, online: () => true, session: retiring,
        request: async (url, init) => { sent2.push(init.body); return json({ code: 'ACKNOWLEDGED' }); },
    });
    retiring.retire();
    await runtime2.wake();
    runtime2.stop();
    record('a retired scope holds for good and sends nothing', runtime2.held() === 'changed' && sent2.length === 0);

    const unbound = createPrksSyncRuntime({
        store: { listOperations: async () => { throw new Error('must not open'); } },
        handlers, online: () => true, session: stubScope(null),
        request: async () => { throw new Error('must not send'); },
    });
    await unbound.wake();
    unbound.stop();
    record('a page without a scope never opens its store', unbound.held() === 'unbound');
}

async function testWorker() {
    const name = 'prks-pdf-v2:' + KEY_A;
    function cacheStorage() {
        const caches = new Map();
        return {
            caches,
            open: async (n) => {
                if (!caches.has(n)) {
                    const entries = new Map();
                    caches.set(n, {
                        entries,
                        match: async (k) => entries.get(String(k && k.url ? new URL(k.url).pathname : k)),
                        put: async (k, v) => { entries.set(String(k && k.url ? new URL(k.url).pathname : k), v); },
                    });
                }
                return caches.get(n);
            },
        };
    }
    const storage = cacheStorage();
    const wrong = await sw.handlePdfCacheMessage({
        type: sw.PDF_CACHE_INSTALL_MESSAGE, pathname: '/api/pdfs/a.pdf', cacheName: 'prks-pdf-v2:other',
        buffer: Buffer.from('X'),
    }, storage, async () => name);
    record('the worker refuses an install naming another scope', wrong === false && storage.caches.size === 0);
    const none = await sw.handlePdfCacheMessage({
        type: sw.PDF_CACHE_INSTALL_MESSAGE, pathname: '/api/pdfs/a.pdf', cacheName: name, buffer: Buffer.from('X'),
    }, storage, async () => null);
    record('the worker refuses an install without a signed-in scope', none === false && storage.caches.size === 0);

    let fetched = 0;
    const handlers = sw.createHandlers({
        caches: storage,
        fetchImpl: async () => { fetched += 1; throw new TypeError('offline'); },
        pdfCacheName: async () => null,
    });
    const request = { url: 'https://prks.example/api/pdfs/a.pdf', method: 'GET', headers: { get: () => null } };
    const res = await handlers.handlePdfRequest(request, '/api/pdfs/a.pdf');
    record('without a scope the worker opens no pdf cache', storage.caches.size === 0 && fetched === 1 && res.status === 503);
}

async function main() {
    await testResolve();
    await testVerify();
    await testStores();
    await testHold();
    await testWorker();
    console.log(passed + ' passed, ' + failed + ' failed');
    if (failed) process.exit(1);
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});
