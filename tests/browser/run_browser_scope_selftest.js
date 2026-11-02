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
    record('the worker reads the last confirmed scope from the record',
        (await sw.readScopeState(idb)).pointerKey === KEY_A);

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
        record('after a ' + label + ' the worker has no last confirmed scope', (await sw.readScopeState(fresh)).pointerKey === null);
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

async function testBoundedRead() {
    const idb = createFakeIndexedDBFactory();
    await scopeApi.createPrksBrowserScope({ indexedDB: idb, fetchImpl: sessionFetch([ok(SESSION_A)]) }).resolve();
    let signal = null;
    const stalled = scopeApi.createPrksBrowserScope({
        indexedDB: idb, sessionTimeoutMs: 20,
        fetchImpl: (url, init) => { signal = init.signal; return new Promise(() => {}); },
    });
    const reused = await stalled.resolve();
    record('a session read that never settles counts as unreachable at its deadline',
        !!reused && reused.key === KEY_A, reused && reused.key);
    record('the stalled read is aborted', !!signal && signal.aborted === true);

    let answerLate = null;
    const late = scopeApi.createPrksBrowserScope({
        indexedDB: idb, sessionTimeoutMs: 20,
        fetchImpl: () => new Promise((res) => { answerLate = () => res(json(SESSION_B)); }),
    });
    const first = await late.resolve();
    answerLate();
    await new Promise((r) => setTimeout(r, 30));
    record('a late answer does not change the bound scope', first.key === KEY_A && late.current().key === KEY_A);
    record('a late answer does not overwrite the record', (await sw.readScopeState(idb)).pointerKey === KEY_A);

    const refusedLate = scopeApi.createPrksBrowserScope({
        indexedDB: idb, sessionTimeoutMs: 20,
        fetchImpl: () => new Promise((res) => setTimeout(() => res(json({ error: 'not_authenticated' }, 401)), 40)),
    });
    await refusedLate.resolve();
    await new Promise((r) => setTimeout(r, 50));
    record('a late refusal does not forget the record either', (await sw.readScopeState(idb)).pointerKey === KEY_A);
}

function channelPair() {
    const members = [];
    function make() {
        const ch = {
            onmessage: null,
            postMessage: (data) => members.filter((m) => m !== ch).forEach((m) => m.onmessage && m.onmessage({ data })),
        };
        members.push(ch);
        return ch;
    }
    return make;
}

async function testSignOut() {
    const idb = createFakeIndexedDBFactory();
    const channel = channelPair();
    const posted = [];
    const fetchFor = (answers) => {
        const f = sessionFetch(answers);
        return (url, init) => {
            if (url === '/api/logout') { posted.push(init.method); return Promise.resolve(json({ signed_out: true })); }
            return f(url, init);
        };
    };
    const signedOut = status(401, { error: 'not_authenticated' });
    const tab1 = scopeApi.createPrksBrowserScope({ indexedDB: idb, channel: channel(), fetchImpl: fetchFor([ok(SESSION_A)]) });
    const tab2 = scopeApi.createPrksBrowserScope({ indexedDB: idb, channel: channel(), fetchImpl: fetchFor([ok(SESSION_A), signedOut]) });
    const asleep = scopeApi.createPrksBrowserScope({ indexedDB: idb, fetchImpl: fetchFor([ok(SESSION_A), signedOut]) });
    await Promise.all([tab1.resolve(), tab2.resolve(), asleep.resolve()]);
    const events = [];
    tab2.subscribe((e) => events.push(e.reason));

    await tab1.signOut();
    record('sign-out ends the server session', posted.length === 1 && posted[0] === 'POST');
    record('sign-out retires its own page', tab1.isRetired() && tab1.retiredReason() === 'logged_out');
    record('sign-out retires every other open page of the scope at once',
        tab2.isRetired() && events.join(',') === 'logged_out', events.join(','));
    record('a retired signed-out page opens no store', (await tab2.dbName('local')) === null);
    record('sign-out forgets the last confirmed scope', (await sw.readScopeState(idb)).pointerKey === null);
    record('a page that missed the broadcast retires when it next checks',
        (await asleep.verify()) === 'logged_out' && asleep.isRetired());

    // The same account signing in again ends the sign-out record: a later
    // expiry holds again instead of retiring.
    const again = scopeApi.createPrksBrowserScope({ indexedDB: idb, fetchImpl: fetchFor([ok(SESSION_A), signedOut]) });
    await again.resolve();
    record('after signing in again an expiry only holds', (await again.verify()) === 'signed_out' && !again.isRetired());

    // Another account's sign-out leaves this one alone.
    const idb2 = createFakeIndexedDBFactory();
    const ch = channelPair();
    const b = scopeApi.createPrksBrowserScope({ indexedDB: idb2, channel: ch(), fetchImpl: fetchFor([ok(SESSION_B)]) });
    const a = scopeApi.createPrksBrowserScope({ indexedDB: idb2, channel: ch(), fetchImpl: fetchFor([ok(SESSION_A)]) });
    await a.resolve();
    await b.resolve();
    await b.signOut();
    record("another account's sign-out does not retire this page", !a.isRetired());
}

async function testAnnounceAndLifecycle() {
    const messages = [];
    const listeners = {};
    const serviceWorker = {
        controller: { postMessage: (m) => messages.push(m) },
        addEventListener: (name, fn) => { listeners[name] = fn; },
    };
    const scope = scopeApi.createPrksBrowserScope({
        indexedDB: createFakeIndexedDBFactory(), serviceWorker,
        fetchImpl: sessionFetch([ok(SESSION_A), ok(SESSION_A), ok(SESSION_B)]),
    });
    await scope.resolve();
    record('a bound page tells the worker its scope',
        messages.length === 1 && messages[0].type === scopeApi.PRKS_SCOPE_BIND_MESSAGE && messages[0].key === KEY_A && !messages[0].retired);
    listeners.controllerchange();
    await new Promise((r) => setTimeout(r, 0));
    record('a new controlling worker is told again', messages.length === 2 && messages[1].key === KEY_A);
    await scope.verify();
    await scope.verify();
    const last = messages[messages.length - 1];
    record('a retired page tells the worker it has no scope', last.key === null && last.retired === true);

    const unboundMessages = [];
    const unbound = scopeApi.createPrksBrowserScope({
        indexedDB: createFakeIndexedDBFactory(), fetchImpl: sessionFetch([status(401)]),
        serviceWorker: { controller: { postMessage: (m) => unboundMessages.push(m) } },
    });
    await unbound.resolve();
    record('a page without a scope tells the worker so', unboundMessages.length === 1 && unboundMessages[0].key === null);

    // Lifecycle re-verification, independent of the queue.
    function target() {
        const fns = {};
        return {
            fns,
            addEventListener: (n, f) => { (fns[n] = fns[n] || []).push(f); },
            removeEventListener: (n, f) => { fns[n] = (fns[n] || []).filter((x) => x !== f); },
            fire: (n) => (fns[n] || []).forEach((f) => f()),
        };
    }
    const win = target();
    const doc = Object.assign(target(), { visibilityState: 'visible' });
    let verifies = 0;
    let clock = 0;
    const fake = { isRetired: () => false, verify: async () => { verifies += 1; return 'same'; } };
    const detach = scopeApi.prksWatchBrowserScopeLifecycle(fake, win, doc, { minIntervalMs: 1000, pollMs: 3600000, now: () => clock });
    win.fire('focus');
    record('focus re-verifies the session', verifies === 1);
    win.fire('online');
    record('checks closer than the minimum interval collapse', verifies === 1);
    clock = 5000;
    doc.fire('visibilitychange');
    record('becoming visible re-verifies', verifies === 2);
    clock = 10000;
    doc.visibilityState = 'hidden';
    doc.fire('visibilitychange');
    record('a hidden page does not check', verifies === 2);
    doc.visibilityState = 'visible';
    clock = 20000;
    win.fire('pageshow');
    record('page show re-verifies', verifies === 3);
    detach();
    clock = 30000;
    win.fire('focus');
    record('a detached watcher checks nothing', verifies === 3);

    // An idle page with an empty queue notices another account on focus.
    const idle = scopeApi.createPrksBrowserScope({
        indexedDB: createFakeIndexedDBFactory(), fetchImpl: sessionFetch([ok(SESSION_A), ok(SESSION_B)]),
    });
    await idle.resolve();
    const w2 = target();
    const stop = scopeApi.prksWatchBrowserScopeLifecycle(idle, w2, null, { minIntervalMs: 0, pollMs: 3600000 });
    w2.fire('focus');
    await new Promise((r) => setTimeout(r, 10));
    stop();
    record('focus retires an idle page another account replaced', idle.isRetired() && idle.retiredReason() === 'changed');
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
    let reason = null;
    const scope = { key: key };
    return {
        verifyAnswers: [],
        resolve: async () => (key ? scope : null),
        current: () => (retired || !key ? null : scope),
        isRetired: () => retired,
        dbName: async (kind) => (key && !retired ? scopeApi.PRKS_SCOPE_STORE_BASES[kind] + ':' + key : null),
        subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
        async verify() { return this.verifyAnswers.length ? this.verifyAnswers.shift() : 'same'; },
        retiredReason: () => (retired ? reason : null),
        retire(why) { retired = true; reason = why || 'changed'; listeners.forEach((fn) => fn({ type: 'retired', reason })); },
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

    // Signed out on purpose: held for good, like another account.
    const loggingOut = stubScope('A');
    const store3 = createPrksLocalStore({ dbName: 'prks-local-v2:test', indexedDB: createFakeIndexedDBFactory(), uuid });
    await fakeOp(store3);
    const sent3 = [];
    const runtime3 = createPrksSyncRuntime({
        store: store3, handlers, online: () => true, session: loggingOut,
        request: async (url, init) => { sent3.push(init.body); return json({ code: 'ACKNOWLEDGED' }); },
    });
    loggingOut.retire('logged_out');
    await runtime3.wake();
    runtime3.stop();
    record('an explicit sign-out holds for good and sends nothing', runtime3.held() === 'logged_out' && sent3.length === 0);
    record('an explicit sign-out keeps the row', (await store3.listOperations()).length === 1);
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

    // Per-client selection.
    const KEY_B = SESSION_B.instance_id + '.' + SESSION_B.library.id + '.' + SESSION_B.user.id;
    const nameB = 'prks-pdf-v2:' + KEY_B;
    const bindings = new Map([
        ['tab-a', { key: KEY_A, retired: false }],
        ['tab-b', { key: KEY_B, retired: false }],
        ['tab-old', { key: null, retired: true }],
    ]);
    const state = { pointerKey: KEY_B, bindings };
    const live = new Set(['tab-a', 'tab-b', 'tab-old']);
    record("a bound page gets its own scope's cache, not the last confirmed one",
        sw.selectPdfCacheName(state, 'tab-a', live, false) === name);
    record('the current account gets its cache', sw.selectPdfCacheName(state, 'tab-b', live, false) === nameB);
    record('a retired page gets no cache', sw.selectPdfCacheName(state, 'tab-old', live, false) === null);
    record('an unbound client gets no cache while live pages disagree',
        sw.selectPdfCacheName(state, 'worker-1', live, false) === null);
    record('an unbound client gets the last confirmed scope when every live page agrees',
        sw.selectPdfCacheName(state, 'worker-1', new Set(['tab-b']), false) === nameB);
    record('an install needs the sender bound to that scope',
        sw.selectPdfCacheName(state, 'worker-1', new Set(['tab-b']), true) === null
        && sw.selectPdfCacheName(state, 'tab-b', live, true) === nameB);
    record('no record and no binding: no cache',
        sw.selectPdfCacheName({ pointerKey: null, bindings: new Map() }, 'x', new Set(), false) === null);

    // Bindings persist in the scope database and drop clients that are gone.
    const idb = createFakeIndexedDBFactory();
    await scopeApi.createPrksBrowserScope({ indexedDB: idb, fetchImpl: sessionFetch([ok(SESSION_A)]) }).resolve();
    await sw.writeClientBinding(idb, 'tab-a', KEY_A, false, new Set(['tab-a']));
    await sw.writeClientBinding(idb, 'tab-gone', KEY_A, false, new Set(['tab-a', 'tab-gone']));
    await sw.writeClientBinding(idb, 'tab-b', KEY_B, false, new Set(['tab-a', 'tab-b']));
    const read = await sw.readScopeState(idb);
    record('the worker keeps each live binding', read.bindings.get('tab-a').key === KEY_A && read.bindings.get('tab-b').key === KEY_B);
    record('the binding of a client that is gone is dropped', !read.bindings.has('tab-gone'));
    await sw.writeClientBinding(idb, 'tab-a', null, true, null);
    record('a retired page is recorded as retired', (await sw.readScopeState(idb)).bindings.get('tab-a').retired === true);
    await sw.writeClientBinding(idb, 'tab-x', 'not a key', false, null);
    record('an invalid key binds no scope', (await sw.readScopeState(idb)).bindings.get('tab-x').key === null);

    const storage2 = cacheStorage();
    const byClient = (clientId, strict) => Promise.resolve(sw.selectPdfCacheName(state, clientId, live, strict));
    const foreign = await sw.handlePdfCacheMessage({
        type: sw.PDF_CACHE_INSTALL_MESSAGE, pathname: '/api/pdfs/a.pdf', cacheName: nameB, buffer: Buffer.from('X'),
    }, storage2, byClient, 'tab-a');
    record("a page cannot install into another scope's cache", foreign === false && storage2.caches.size === 0);
}

async function main() {
    await testResolve();
    await testBoundedRead();
    await testSignOut();
    await testAnnounceAndLifecycle();
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
