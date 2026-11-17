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
const signInApi = require(path.join(rootDir, 'frontend/js/sign-in.js'));
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
        && (await a.dbName('pdf')) === 'prks-pdf-v3:' + KEY_A + ':1');
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

    // Another tab's session read was answered before a sign-out but recorded
    // after it: the sign-out wins, in every tab.
    {
        const idb3 = createFakeIndexedDBFactory();
        let clock = 1000;
        const now = () => clock;
        let releaseLate;
        const late = new Promise((r) => { releaseLate = r; });
        const signer = scopeApi.createPrksBrowserScope({ indexedDB: idb3, now, fetchImpl: fetchFor([ok(SESSION_A)]) });
        const slow = scopeApi.createPrksBrowserScope({
            indexedDB: idb3, now, fetchImpl: fetchFor([ok(SESSION_A), () => late.then(() => json(SESSION_A, 200))]),
        });
        await signer.resolve();
        await slow.resolve();
        clock = 2000;
        const pending = slow.verify(); // its read begins now, before the sign-out
        clock = 3000;
        await signer.signOut();
        clock = 4000;
        releaseLate();
        const verdict = await pending;
        record('a session answer older than another tab\'s sign-out retires the page',
            verdict === 'logged_out' && slow.isRetired(), verdict);
        record('it does not restore the last confirmed scope', (await sw.readScopeState(idb3)).pointerKey === null);
        const reload = scopeApi.createPrksBrowserScope({ indexedDB: idb3, now, fetchImpl: fetchFor([offline()]) });
        record('an offline reload after it opens no store', (await reload.resolve()) === null && (await reload.dbName('local')) === null);
        const missed = scopeApi.createPrksBrowserScope({ indexedDB: idb3, now, fetchImpl: fetchFor([ok(SESSION_A), signedOut]) });
        clock = 4500;
        // A read that begins after the sign-out is a new session.
        await missed.resolve();
        record('a session confirmed after the sign-out ends it', (await sw.readScopeState(idb3)).pointerKey === KEY_A);
        record('and a later expiry only holds', (await missed.verify()) === 'signed_out' && !missed.isRetired());
    }

    // An older answer from one tab never undoes a newer confirmation by another.
    {
        const KEY_B = SESSION_B.instance_id + '.' + SESSION_B.library.id + '.' + SESSION_B.user.id;
        const idb4 = createFakeIndexedDBFactory();
        let clock = 1000;
        const now = () => clock;
        const deferred = () => {
            let release;
            const gate = new Promise((r) => { release = r; });
            return { release, answer: (body, code) => () => gate.then(() => json(body, code)) };
        };
        const late401 = deferred();
        // The browser's one session cookie: after B signs in, a new read in
        // this tab answers B too.
        const expired = scopeApi.createPrksBrowserScope({
            indexedDB: idb4, now,
            fetchImpl: fetchFor([ok(SESSION_A), late401.answer({ error: 'not_authenticated' }, 401), ok(SESSION_B)]),
        });
        await expired.resolve();
        clock = 2000;
        const pendingA = expired.verify(); // A's read begins; its 401 is held
        await new Promise((r) => setImmediate(r));
        clock = 3000;
        const tabB = scopeApi.createPrksBrowserScope({ indexedDB: idb4, now, fetchImpl: fetchFor([ok(SESSION_B)]) });
        await tabB.resolve(); // B signs in and is confirmed by a later read
        clock = 4000;
        late401.release();
        const verdictA = await pendingA;
        record('the older refusal is not acted on: the page reads again and sees the new account',
            verdictA === 'changed' && expired.isRetired(), verdictA);
        record("an older refusal does not forget another account's newer record",
            (await sw.readScopeState(idb4)).pointerKey === KEY_B);
        const reloadB = scopeApi.createPrksBrowserScope({ indexedDB: idb4, now, fetchImpl: fetchFor([offline()]) });
        const boundB = await reloadB.resolve();
        record('an offline reload still binds the newer account', !!boundB && boundB.key === KEY_B);

        const late200 = deferred();
        const old = scopeApi.createPrksBrowserScope({
            indexedDB: idb4, now, fetchImpl: fetchFor([ok(SESSION_B), late200.answer(SESSION_A, 200), ok(SESSION_B)]),
        });
        clock = 5000;
        await old.resolve();
        clock = 6000;
        const pendingOld = old.verify(); // reads A's session from 6000; answer held
        await new Promise((r) => setImmediate(r));
        clock = 7000;
        const tabB2 = scopeApi.createPrksBrowserScope({ indexedDB: idb4, now, fetchImpl: fetchFor([ok(SESSION_B)]) });
        await tabB2.resolve();
        late200.release();
        const verdictOld = await pendingOld;
        record("an older confirmation does not replace a newer account's record",
            (await sw.readScopeState(idb4)).pointerKey === KEY_B);
        record('an older confirmation is not acted on: the page reads again',
            verdictOld === 'same' && !old.isRetired(), verdictOld);
    }

    // A page's first read, answered for A but delivered after another tab
    // confirmed B, never binds A.
    {
        const KEY_B = SESSION_B.instance_id + '.' + SESSION_B.library.id + '.' + SESSION_B.user.id;
        const idb5 = createFakeIndexedDBFactory();
        let clock = 1000;
        const now = () => clock;
        let releaseA;
        const gate = new Promise((r) => { releaseA = r; });
        const bindings = [];
        const firstA = scopeApi.createPrksBrowserScope({
            indexedDB: idb5, now,
            fetchImpl: fetchFor([() => gate.then(() => json(SESSION_A, 200)), ok(SESSION_B)]),
            serviceWorker: { controller: { postMessage: (m) => bindings.push(m && m.key) }, addEventListener() {} },
        });
        const resolving = firstA.resolve(); // its read begins at 1000
        await new Promise((r) => setImmediate(r));
        clock = 2000;
        const tabB = scopeApi.createPrksBrowserScope({ indexedDB: idb5, now, fetchImpl: fetchFor([ok(SESSION_B)]) });
        await tabB.resolve();
        clock = 3000;
        releaseA();
        const bound = await resolving;
        record("a superseded first answer does not bind the older account",
            !!bound && bound.key === KEY_B, bound && bound.key);
        record("the page opens the newer account's stores, never the older one's",
            (await firstA.dbName('local')) === 'prks-local-v2:' + KEY_B && bindings.indexOf(KEY_A) === -1,
            JSON.stringify(bindings));
        record("the newer account's record survives", (await sw.readScopeState(idb5)).pointerKey === KEY_B);

        // Every answer outranked by a newer read: the page binds nothing.
        let n = 0;
        const always = scopeApi.createPrksBrowserScope({
            indexedDB: idb5, now,
            fetchImpl: () => {
                const began = clock;
                return (async () => {
                    clock = began + 10;
                    const other = scopeApi.createPrksBrowserScope({ indexedDB: idb5, now, fetchImpl: fetchFor([ok(SESSION_B)]) });
                    await other.resolve();
                    n += 1;
                    return json(SESSION_A, 200);
                })();
            },
        });
        clock = 4000;
        const none = await always.resolve();
        record('an answer outranked on every read binds nothing', none === null && n === 3, String(n));
    }

    // A confirmation delivered after a newer refusal in another tab never
    // brings the refused account back.
    {
        const idb6 = createFakeIndexedDBFactory();
        let clock = 1000;
        const now = () => clock;
        let releaseA;
        const gate = new Promise((r) => { releaseA = r; });
        const signedOut = status(401, { error: 'not_authenticated' });
        const seed = scopeApi.createPrksBrowserScope({ indexedDB: idb6, now, fetchImpl: fetchFor([ok(SESSION_A)]) });
        await seed.resolve();
        clock = 2000;
        const lateA = scopeApi.createPrksBrowserScope({
            indexedDB: idb6, now, fetchImpl: fetchFor([() => gate.then(() => json(SESSION_A, 200)), signedOut]),
        });
        const resolving = lateA.resolve(); // its read begins at 2000
        await new Promise((r) => setImmediate(r));
        clock = 3000;
        const refused = scopeApi.createPrksBrowserScope({ indexedDB: idb6, now, fetchImpl: fetchFor([signedOut]) });
        record('the newer refusal binds nothing', (await refused.resolve()) === null);
        clock = 4000;
        releaseA();
        const bound = await resolving;
        record('an older confirmation after a newer refusal binds nothing', bound === null, bound && bound.key);
        record('it does not restore the refused account', (await sw.readScopeState(idb6)).pointerKey === null);
        const reload = scopeApi.createPrksBrowserScope({ indexedDB: idb6, now, fetchImpl: fetchFor([offline()]) });
        record('an offline reload after it opens no store',
            (await reload.resolve()) === null && (await reload.dbName('local')) === null);
    }
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
    record('with the Library epoch it bound with', messages[0].epoch === '1');
    listeners.controllerchange();
    await new Promise((r) => setTimeout(r, 0));
    record('a new controlling worker is told again', messages.length === 2 && messages[1].key === KEY_A);
    await scope.verify();
    await scope.verify();
    const last = messages[messages.length - 1];
    record('a retired page tells the worker it has no scope', last.key === null && last.epoch === null && last.retired === true);

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
        epoch: '1',
        pageEpoch() { return retired || !key ? null : this.epoch; },
        latestEpoch() { return retired || !key ? null : this.epoch; },
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

    // The scope retires while a database open is still in flight: the open
    // that completes afterwards must not be used.
    const retiringOnOpen = (scope) => new Proxy(idb, {
        get(target, prop) {
            if (prop !== 'open') {
                const value = target[prop];
                return typeof value === 'function' ? value.bind(target) : value;
            }
            return function (name, version) {
                const req = target.open(name, version);
                scope.retire();
                return req;
            };
        },
    });
    const midOpen = stubScope('D');
    const d = createPrksLocalStore({ indexedDB: retiringOnOpen(midOpen), uuid, scope: midOpen });
    let midRefused = null;
    try { await d.recordWorkOpened('W-4', '2026-10-31T09:00:00.000Z'); } catch (err) { midRefused = err; }
    record('a queue open that completes after retirement is refused',
        !!midRefused && midRefused.prksLocalStoreCode === 'unavailable', midRefused && midRefused.prksLocalStoreCode);
    const dAgain = createPrksLocalStore({ indexedDB: idb, uuid, scope: stubScope('D') });
    record('and writes nothing into the retired queue', (await dAgain.listOperations()).length === 0);

    const midCacheScope = stubScope('E');
    const cacheE = createPrksOfflineStore({ indexedDB: retiringOnOpen(midCacheScope), scope: midCacheScope });
    record('a cache open that completes after retirement is refused',
        (await cacheE.putEntity('work', 'W-1', { title: 'T' }, '1')) === false);
    const cacheEAgain = createPrksOfflineStore({ indexedDB: idb, scope: stubScope('E') });
    record('and writes nothing into the retired cache', (await cacheEAgain.getEntity('work', 'W-1')) == null);
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
    const name = 'prks-pdf-v3:' + KEY_A + ':1';
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
        type: sw.PDF_CACHE_INSTALL_MESSAGE, pathname: '/api/pdfs/a.pdf', cacheName: 'prks-pdf-v3:other',
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
    const nameB = 'prks-pdf-v3:' + KEY_B + ':1';
    const bindings = new Map([
        ['tab-a', { key: KEY_A, epoch: '1', retired: false }],
        ['tab-b', { key: KEY_B, epoch: '1', retired: false }],
        ['tab-old', { key: null, epoch: null, retired: true }],
    ]);
    const state = { pointerKey: KEY_B, pointerEpoch: '1', bindings };
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
    // A live page that has not bound yet (still loading, or its bind is lost)
    // never borrows the last confirmed account's cache.
    const gap = { pointerKey: KEY_B, pointerEpoch: '1', bindings: new Map([['tab-b', { key: KEY_B, epoch: '1', retired: false }]]) };
    const gapLive = new Set(['tab-a', 'tab-b']);
    record('an unbound live page gets no cache even when every bound page agrees',
        sw.selectPdfCacheName(gap, 'tab-a', gapLive, false) === null);
    record('a worker gets no cache while any live page is unbound',
        sw.selectPdfCacheName(gap, 'worker-1', gapLive, false) === null);
    record('a client with no id gets no cache', sw.selectPdfCacheName(gap, '', new Set(['tab-b']), false) === null);
    record('a worker gets no cache with no live page at all', sw.selectPdfCacheName(gap, 'worker-1', new Set(), false) === null);
    record('no record and no binding: no cache',
        sw.selectPdfCacheName({ pointerKey: null, pointerEpoch: null, bindings: new Map() }, 'x', new Set(), false) === null);

    const storage2 = cacheStorage();
    const byClient = (clientId, strict) => Promise.resolve(sw.selectPdfCacheName(state, clientId, live, strict));
    const foreign = await sw.handlePdfCacheMessage({
        type: sw.PDF_CACHE_INSTALL_MESSAGE, pathname: '/api/pdfs/a.pdf', cacheName: nameB, buffer: Buffer.from('X'),
    }, storage2, byClient, 'tab-a');
    record("a page cannot install into another scope's cache", foreign === false && storage2.caches.size === 0);

    // A post-save install is stored only while the session is still the
    // cache's own scope: the save ran under whichever session the browser
    // holds, so another account's bytes never enter this page's cache.
    {
        const path = '/api/pdfs/post-save-switch.pdf';
        const storage4 = cacheStorage();
        const seed = await storage4.open(name);
        await seed.put(path, new Response('%PDF-A-OLD'));
        const own = async () => name;
        const install = (body, confirm) => sw.handlePdfCacheMessage({
            type: sw.PDF_CACHE_INSTALL_MESSAGE, pathname: path, cacheName: name, buffer: Buffer.from(body),
        }, storage4, own, 'tab-a', confirm);
        const headOf = async () => (await (await (await storage4.open(name)).match(path)).clone().text());
        const asked = [];
        const switched = await install('%PDF-B-SAVED', async (n) => { asked.push(n); return false; });
        record('an install after another account signed in is refused',
            switched === false && asked.length === 1 && asked[0] === name);
        record("the refused install leaves the page's cached bytes unchanged", (await headOf()) === '%PDF-A-OLD');
        const failing = await install('%PDF-B-SAVED', async () => { throw new TypeError('offline'); });
        record('an install whose session check fails is refused', failing === false && (await headOf()) === '%PDF-A-OLD');
        const unchecked = await install('%PDF-B-SAVED');
        record('an install without a session check is refused', unchecked === false && (await headOf()) === '%PDF-A-OLD');
        const before = sw.wholeFilePdfGeneration(path, name);
        const confirmed = await install('%PDF-A-NEW', async () => true);
        record('an install under the same session is stored', confirmed === true && (await headOf()) === '%PDF-A-NEW');
        record('a refused install still advances the generation', before === 3);
    }

    // A network PDF is cached only while the session is still the cache's scope.
    for (const [label, confirmed, stored] of [['confirmed', [true, true], true],
        ['changed before the fetch', [false, true], false], ['changed during the fetch', [true, false], false]]) {
        const storage3 = cacheStorage();
        const answers = confirmed.slice();
        const h = sw.createHandlers({
            caches: storage3,
            fetchImpl: async () => new Response('%PDF-NEW', { status: 200, headers: { 'Content-Type': 'application/pdf' } }),
            pdfCacheName: async () => name,
            confirmScope: async (cacheName) => cacheName === name && answers.shift(),
        });
        const got = await h.handlePdfRequest(request, '/api/pdfs/a.pdf', 'tab-a');
        await new Promise((r) => setTimeout(r, 20));
        const cache = storage3.caches.get(name);
        const has = !!(cache && cache.entries.get('/api/pdfs/a.pdf'));
        record('a network PDF, session ' + label + ': ' + (stored ? 'cached' : 'never cached') + ', still served',
            got.status === 200 && has === stored, String(has));
    }
    record('the worker reads a session body as its scope key',
        sw.sessionScopeKey(SESSION_A) === KEY_A && sw.sessionScopeKey({ user: null }) === null);
}

/** Bindings persist in the scope database and drop clients that are gone. */
async function testWorkerBindings() {
    const KEY_B = SESSION_B.instance_id + '.' + SESSION_B.library.id + '.' + SESSION_B.user.id;
    const idb = createFakeIndexedDBFactory();
    await scopeApi.createPrksBrowserScope({ indexedDB: idb, fetchImpl: sessionFetch([ok(SESSION_A)]) }).resolve();
    await sw.writeClientBinding(idb, 'tab-a', KEY_A, '1', false, new Set(['tab-a']));
    await sw.writeClientBinding(idb, 'tab-gone', KEY_A, '1', false, new Set(['tab-a', 'tab-gone']));
    await sw.writeClientBinding(idb, 'tab-b', KEY_B, '2', false, new Set(['tab-a', 'tab-b']));
    const read = await sw.readScopeState(idb);
    record('the worker keeps each live binding', read.bindings.get('tab-a').key === KEY_A && read.bindings.get('tab-b').key === KEY_B);
    record('with the epoch each page bound with', read.bindings.get('tab-a').epoch === '1' && read.bindings.get('tab-b').epoch === '2');
    record('and the last confirmed scope\'s epoch', read.pointerKey === KEY_A && read.pointerEpoch === '1');
    record('the binding of a client that is gone is dropped', !read.bindings.has('tab-gone'));
    await sw.writeClientBinding(idb, 'tab-a', null, null, true, null);
    record('a retired page is recorded as retired', (await sw.readScopeState(idb)).bindings.get('tab-a').retired === true);
    await sw.writeClientBinding(idb, 'tab-x', 'not a key', '1', false, null);
    record('an invalid key binds no scope', (await sw.readScopeState(idb)).bindings.get('tab-x').key === null);
}

/** Library epoch (#629, §10.4): each epoch has its own PDF cache. */
async function testWorkerEpochs() {
    const KEY_B = SESSION_B.instance_id + '.' + SESSION_B.library.id + '.' + SESSION_B.user.id;
    const name = 'prks-pdf-v3:' + KEY_A + ':1';
    const nameB = 'prks-pdf-v3:' + KEY_B + ':1';
    const epochs = new Map([
        ['tab-old-lib', { key: KEY_B, epoch: '1', retired: false }],
        ['tab-new-lib', { key: KEY_B, epoch: '2', retired: false }],
        ['tab-no-epoch', { key: KEY_B, epoch: null, retired: false }],
    ]);
    const restored = { pointerKey: KEY_B, pointerEpoch: '2', bindings: epochs };
    const all = new Set(['tab-old-lib', 'tab-new-lib', 'tab-no-epoch']);
    record('a page of the restored Library gets its epoch\'s cache',
        sw.selectPdfCacheName(restored, 'tab-new-lib', all, false) === 'prks-pdf-v3:' + KEY_B + ':2');
    record('a page of the earlier Library keeps its own, never the newer one',
        sw.selectPdfCacheName(restored, 'tab-old-lib', all, false) === nameB);
    record('a page bound without an epoch gets no cache', sw.selectPdfCacheName(restored, 'tab-no-epoch', all, false) === null);
    record('a worker gets no cache while live pages are on different epochs',
        sw.selectPdfCacheName(restored, 'worker-1', new Set(['tab-old-lib', 'tab-new-lib']), false) === null);
    record('a worker borrows the confirmed epoch when every page is on it',
        sw.selectPdfCacheName(restored, 'worker-1', new Set(['tab-new-lib']), false) === 'prks-pdf-v3:' + KEY_B + ':2');
    record('a record without an epoch lends no cache',
        sw.selectPdfCacheName({ pointerKey: KEY_B, pointerEpoch: null, bindings: new Map() }, 'worker-1', new Set(['tab-new-lib']), false) === null);
    const names = ['prks-pdf-v3:' + KEY_B + ':1', 'prks-pdf-v3:' + KEY_B + ':9', 'prks-pdf-v3:' + KEY_B + ':10',
        'prks-pdf-v3:' + KEY_B + ':11', 'prks-pdf-v3:' + KEY_A + ':1', 'prks-static-x', 'prks-pdf-v3:' + KEY_B + ':x'];
    record('the caches of earlier epochs of that scope are the ones dropped',
        JSON.stringify(sw.olderEpochPdfCaches(names, KEY_B, '10'))
            === JSON.stringify(['prks-pdf-v3:' + KEY_B + ':1', 'prks-pdf-v3:' + KEY_B + ':9']));
    const deleted = [];
    await sw.dropOlderEpochPdfCaches({ keys: async () => names, delete: async (n) => { deleted.push(n); return true; } }, KEY_B, '2');
    record('a newer binding deletes them', JSON.stringify(deleted) === JSON.stringify(['prks-pdf-v3:' + KEY_B + ':1']));
    record('an invalid epoch drops nothing', sw.olderEpochPdfCaches(names, KEY_B, 'x').length === 0);
    record('a numeric epoch drops the same caches',
        JSON.stringify(sw.olderEpochPdfCaches(names, KEY_B, 10))
            === JSON.stringify(['prks-pdf-v3:' + KEY_B + ':1', 'prks-pdf-v3:' + KEY_B + ':9']));
    record('the session names the cache of its scope and epoch',
        sw.sessionPdfCacheName(SESSION_A) === name
        && sw.sessionPdfCacheName(Object.assign({}, SESSION_A, { library: Object.assign({}, SESSION_A.library, { epoch: 2 }) }))
            === 'prks-pdf-v3:' + KEY_A + ':2'
        && sw.sessionPdfCacheName(Object.assign({}, SESSION_A, { library: Object.assign({}, SESSION_A.library, { epoch: undefined }) })) === null);
    record('the per-scope caches of builds without an epoch are retired',
        sw.shouldRetireCache('prks-pdf-v2:' + KEY_A) && sw.shouldRetireCache('prks-pdf-v1')
        && !sw.shouldRetireCache('prks-pdf-v3:' + KEY_A + ':1'));
}

/** A session stub whose epochs the test sets. */
function epochScope(key, pageEpoch, latest) {
    const stub = stubScope(key);
    stub.epoch = pageEpoch;
    stub.latest = latest || pageEpoch;
    stub.latestEpoch = function () { return this.latest; };
    return stub;
}

async function testEpoch() {
    // The scope binds the epoch its session names, and records it for an offline reload.
    const idb = createFakeIndexedDBFactory();
    const a = scopeApi.createPrksBrowserScope({ indexedDB: idb, fetchImpl: sessionFetch([ok(SESSION_A)]) });
    await a.resolve();
    record('the page binds its session\'s epoch', a.pageEpoch() === '1' && a.latestEpoch() === '1',
        String(a.pageEpoch()) + '/' + String(a.latestEpoch()));
    const offlineReload = scopeApi.createPrksBrowserScope({ indexedDB: idb, fetchImpl: sessionFetch([offline()]) });
    await offlineReload.resolve();
    record('an offline reload binds the recorded epoch', offlineReload.pageEpoch() === '1');
    record('a session without content names none, nor does an offline reload',
        a.libraryContent() === null && offlineReload.libraryContent() === null);
    const pg = Object.assign({}, SESSION_A, { library: Object.assign({}, SESSION_A.library, { content: 'postgres' }) });
    const pgPage = scopeApi.createPrksBrowserScope({ indexedDB: createFakeIndexedDBFactory(), fetchImpl: sessionFetch([ok(pg)]) });
    await pgPage.resolve();
    record('a confirmed session names the Library content', pgPage.libraryContent() === 'postgres');
    const oddContent = Object.assign({}, SESSION_A, { library: Object.assign({}, SESSION_A.library, { content: 'mysql' }) });
    const oddPage = scopeApi.createPrksBrowserScope({ indexedDB: createFakeIndexedDBFactory(), fetchImpl: sessionFetch([ok(oddContent)]) });
    await oddPage.resolve();
    record('an unknown content is not named', oddPage.libraryContent() === null && oddPage.current() !== null);
    // A page that started offline binds the recorded scope without content,
    // then learns it from its first confirmed same-scope answer, no reload.
    const pgIdb = createFakeIndexedDBFactory();
    await scopeApi.createPrksBrowserScope({ indexedDB: pgIdb, fetchImpl: sessionFetch([ok(pg)]) }).resolve();
    const lateStart = scopeApi.createPrksBrowserScope({ indexedDB: pgIdb, fetchImpl: sessionFetch([offline(), ok(pg)]) });
    const lateBound = await lateStart.resolve();
    const lateBefore = lateStart.libraryContent();
    const verdict = await lateStart.verify();
    record('an offline start learns the content once the session answers',
        lateBound !== null && lateBefore === null && verdict === 'same' && lateStart.libraryContent() === 'postgres',
        String(lateBefore) + '/' + verdict + '/' + String(lateStart.libraryContent()));
    const noEpoch = Object.assign({}, SESSION_A, { library: Object.assign({}, SESSION_A.library, { epoch: undefined }) });
    const odd = scopeApi.createPrksBrowserScope({ indexedDB: createFakeIndexedDBFactory(), fetchImpl: sessionFetch([ok(noEpoch)]) });
    record('a session without an epoch binds no scope', (await odd.resolve()) === null && odd.pageEpoch() === null);

    // A later answer names a newer epoch: announced once, and the page's stays.
    const restored = Object.assign({}, SESSION_A, { library: Object.assign({}, SESSION_A.library, { epoch: 2 }) });
    const at10 = Object.assign({}, SESSION_A, { library: Object.assign({}, SESSION_A.library, { epoch: 10 }) });
    const pageFetch = sessionFetch([ok(SESSION_A), ok(restored), ok(at10)]);
    const page = scopeApi.createPrksBrowserScope({ indexedDB: createFakeIndexedDBFactory(), fetchImpl: pageFetch });
    await page.resolve();
    const events = [];
    page.subscribe((event) => events.push(event));
    record('a verify of the same account is still the same scope', (await page.verify()) === 'same');
    record('a newer epoch is announced', events.length === 1 && events[0].type === 'epoch' && events[0].epoch === '2',
        JSON.stringify(events));
    record('the page keeps the epoch it bound with', page.pageEpoch() === '1' && page.latestEpoch() === '2');
    const readsBefore = pageFetch.calls.length;
    page.noteEpoch('1');
    page.noteEpoch('abc');
    page.noteEpoch('2');
    record('an older, equal or malformed header epoch changes nothing and reads nothing',
        events.length === 1 && page.latestEpoch() === '2' && pageFetch.calls.length === readsBefore);
    page.noteEpoch('10');
    record('a higher header epoch alone raises nothing', page.latestEpoch() === '2');
    await new Promise((r) => setTimeout(r, 0));
    await page.verify();
    record('the same account\'s session read raises it, ordered as numbers',
        page.latestEpoch() === '10' && events.length === 2 && events[1].epoch === '10', JSON.stringify(events));

    // Another account signed in under the page: its header epoch is not this Library's (#1115 review).
    const switched = scopeApi.createPrksBrowserScope({
        indexedDB: createFakeIndexedDBFactory(), fetchImpl: sessionFetch([ok(SESSION_A), ok(Object.assign({}, SESSION_B, { library: at10.library }))]),
    });
    await switched.resolve();
    const seen = [];
    switched.subscribe((event) => seen.push(event.type));
    switched.noteEpoch('10');
    for (let i = 0; i < 5 && !switched.isRetired(); i++) await new Promise((r) => setTimeout(r, 0));
    record('another account\'s header epoch retires the page instead of raising its epoch',
        switched.isRetired() && seen.indexOf('epoch') === -1, JSON.stringify(seen));

    // The coordinator sends the page's epoch and reports newer ones to the scope.
    const { createPrksLibraryEpoch } = require(path.join(rootDir, 'frontend/js/request-coordinator.js'));
    const sent = [];
    const scopeLike = epochScope('A', '3');
    const noted = [];
    scopeLike.noteEpoch = (e) => noted.push(e);
    const coordinator = createPrksLibraryEpoch({
        scope: () => scopeLike,
        fetchImpl: async (url, init) => {
            sent.push(init && init.headers);
            return new Response('{}', { status: 200, headers: { 'PRKS-Library-Epoch': '4' } });
        },
    });
    await coordinator.fetch('/api/tags', { method: 'POST' });
    await coordinator.fetch('/api/tags', { method: 'POST' });
    record('a mutation names the scope\'s page epoch, also after a newer answer',
        sent.length === 2 && sent.every((h) => h['PRKS-Library-Epoch'] === '3'), JSON.stringify(sent));
    record('every answer\'s epoch is reported to the scope', noted.length === 2 && noted[0] === '4', JSON.stringify(noted));

    // The queue: every operation records the page's epoch.
    const uuid = (() => { let n = 0; return () => '00000000-0000-4000-8000-' + String(++n).padStart(12, '0'); })();
    const qdb = createFakeIndexedDBFactory();
    const before = epochScope('Q', '1');
    const store = createPrksLocalStore({ indexedDB: qdb, uuid, scope: before });
    const opened = await store.recordWorkOpened('W-1', '2026-11-04T09:00:00.000Z');
    await store.saveWorkNote('W-1', 'SET_WORK_PRIVATE_NOTE', 'Remember this', { value: '', revision: 0 });
    record('an operation records the page\'s epoch', opened.library_epoch === '1');
    record('the store records its epoch', (await store.libraryEpoch()) === '1');

    // The server names a newer epoch: the older operations leave the queue for review.
    const moved = await store.advanceEpoch('2');
    const kept = await store.listRestored();
    record('a newer epoch moves every older operation out of the queue',
        moved === 2 && (await store.listOperations()).length === 0, String(moved));
    record('their text is kept for review', kept.length === 2 && kept.some((op) => op.payload && op.payload.text === 'Remember this')
        && kept.every((op) => op.superseded_by_epoch === '2' && op.library_epoch === '1'), JSON.stringify(kept.map((op) => op.operation)));
    record('an equal or older epoch moves nothing', (await store.advanceEpoch('2')) === 0 && (await store.advanceEpoch('1')) === 0);

    // A page loaded before the restore records nothing more.
    let refused = null;
    try { await store.saveWorkNote('W-1', 'SET_WORK_PRIVATE_NOTE', 'Typed on the old page', { value: '', revision: 0 }); } catch (err) { refused = err; }
    record('a page of an older epoch is refused', !!refused && refused.prksLocalStoreCode === 'library_restored',
        refused && refused.prksLocalStoreCode);
    record('and nothing of it is queued', (await store.listOperations()).length === 0);

    // A reloaded page of the new epoch queues again, under it.
    const reloaded = createPrksLocalStore({ indexedDB: qdb, uuid, scope: epochScope('Q', '2') });
    const fresh = await reloaded.saveWorkNote('W-1', 'SET_WORK_PRIVATE_NOTE', 'After the restore', { value: '', revision: 0 });
    record('a page of the new epoch queues under it', fresh.library_epoch === '2' && (await reloaded.listOperations()).length === 1);

    // A page of a newer epoch writing first moves the older operations itself.
    const pdb = createFakeIndexedDBFactory();
    const old = createPrksLocalStore({ indexedDB: pdb, uuid, scope: epochScope('P', '5') });
    await old.recordWorkOpened('W-9', '2026-11-04T09:00:00.000Z');
    const newer = createPrksLocalStore({ indexedDB: pdb, uuid, scope: epochScope('P', '6') });
    await newer.recordWorkOpened('W-2', '2026-11-04T10:00:00.000Z');
    const rows = await newer.listOperations();
    record('a write under a newer epoch first moves the older operations',
        rows.length === 1 && rows[0].library_epoch === '6' && (await newer.listRestored()).length === 1, JSON.stringify(rows.map((r) => r.library_epoch)));

    // Discarding is the user's choice, one change at a time.
    const first = kept[0].op_id;
    await store.discardRestored(first);
    record('a discarded change is gone from review', (await store.listRestored()).every((op) => op.op_id !== first));
    let gone = null;
    try { await store.discardRestored(first); } catch (err) { gone = err; }
    record('discarding it again is refused', !!gone && gone.prksLocalStoreCode === 'not_restored');

    // The runtime sends each operation under its own epoch.
    const handlers = {
        MARK_WORK_OPENED: {
            isResult: (data) => !!data && typeof data.code === 'string',
            reconcile: async () => true,
            terminal: () => ({ discard: 'refused' }),
        },
    };
    async function runtimeWith(latest, answers) {
        const db = createFakeIndexedDBFactory();
        const session = epochScope('R', '1', latest);
        const queue = createPrksLocalStore({ indexedDB: db, uuid, scope: session });
        await queue.recordWorkOpened('W-1', '2026-11-04T09:00:00.000Z');
        const requests = [];
        const runtime = createPrksSyncRuntime({
            store: queue, handlers, online: () => true, session,
            request: async (url, init) => {
                requests.push(init.headers);
                const next = answers.shift();
                return typeof next === 'function' ? next(session) : next;
            },
        });
        await runtime.wake();
        runtime.stop();
        return { requests, runtime, queue };
    }
    const same = await runtimeWith('1', [json({ code: 'ACKNOWLEDGED', server_revision: 1 })]);
    record('an operation is sent under its own epoch', same.requests.length === 1 && same.requests[0]['PRKS-Library-Epoch'] === '1',
        JSON.stringify(same.requests));
    const known = await runtimeWith('2', []);
    record('an operation of an older epoch than the server\'s is never sent', known.requests.length === 0);
    record('it waits for review instead', (await known.queue.listRestored()).length === 1
        && (await known.queue.listOperations()).length === 0);
    const refusedAnswer = (session) => { session.latest = '2'; return json({ error: 'LIBRARY_EPOCH_CHANGED', code: 'LIBRARY_EPOCH_CHANGED' }, 409); };
    const changed = await runtimeWith('1', [refusedAnswer]);
    record('after a 409 the session names the newer epoch and the operation goes to review',
        changed.requests.length === 1 && (await changed.queue.listRestored()).length === 1
        && (await changed.queue.listOperations()).length === 0 && changed.runtime.held() === null,
        String(changed.runtime.held()));
}

/** PG-C2 #641: a 401 at start offers sign-in; `signIn` posts the login and reports the answer. */
async function testSignIn() {
    const cases = [
        ['a 401', status(401, { error: 'not_authenticated' }), true],
        ['a 403', status(403, { error: 'forbidden' }), false],
        ['a confirmed session', ok(SESSION_A), false],
        ['an unreachable server', offline(), false],
        ['a 503', status(503), false],
    ];
    for (const [label, answer, expected] of cases) {
        const scope = scopeApi.createPrksBrowserScope({ indexedDB: createFakeIndexedDBFactory(), fetchImpl: sessionFetch([answer]) });
        record('before resolve nothing asks for sign-in (' + label + ')', scope.needsSignIn() === false);
        await scope.resolve();
        record(label + (expected ? ' asks' : ' does not ask') + ' for sign-in', scope.needsSignIn() === expected);
    }

    const sent = [];
    function loginFetch(answer) {
        return function (url, init) {
            if (url === '/api/session') return Promise.resolve(json({ error: 'not_authenticated' }, 401));
            sent.push({ url: url, init: init });
            return typeof answer === 'function' ? answer() : Promise.resolve(answer);
        };
    }
    const good = scopeApi.createPrksBrowserScope({ indexedDB: createFakeIndexedDBFactory(), fetchImpl: loginFetch(json({ signed_in: true }, 200)) });
    const done = await good.signIn('alice', 'pw');
    const req = sent[0];
    record('a sign-in posts JSON to /api/login', !!req && req.url === '/api/login' && req.init.method === 'POST'
        && req.init.headers['Content-Type'] === 'application/json'
        && JSON.stringify(JSON.parse(req.init.body)) === JSON.stringify({ login: 'alice', password: 'pw' }));
    record('a 200 sign-in is ok', done.ok === true);
    record('a sign-in binds nothing (the page reloads)', good.current() === null);

    const wrong = await scopeApi.createPrksBrowserScope({ indexedDB: createFakeIndexedDBFactory(),
        fetchImpl: loginFetch(json({ error: 'invalid_credentials' }, 401)) }).signIn('alice', 'bad');
    record('a refused sign-in carries the server code', wrong.ok === false && wrong.error === 'invalid_credentials' && wrong.status === 401);
    const throttled = await scopeApi.createPrksBrowserScope({ indexedDB: createFakeIndexedDBFactory(),
        fetchImpl: loginFetch(new Response(JSON.stringify({ error: 'throttled' }), { status: 429, headers: { 'Retry-After': '30' } })) }).signIn('a', 'b');
    record('a throttled sign-in carries Retry-After', throttled.status === 429 && throttled.retryAfter === 30);
    const odd = await scopeApi.createPrksBrowserScope({ indexedDB: createFakeIndexedDBFactory(),
        fetchImpl: loginFetch(new Response('nope', { status: 429, headers: { 'Retry-After': 'soon' } })) }).signIn('a', 'b');
    record('a malformed answer is refused, with no Retry-After', odd.ok === false && odd.error === 'refused' && odd.retryAfter === null);
    const down = await scopeApi.createPrksBrowserScope({ indexedDB: createFakeIndexedDBFactory(),
        fetchImpl: loginFetch(() => Promise.reject(new TypeError('Failed to fetch'))) }).signIn('a', 'b');
    record('a sign-in the server never answers is unreachable', down.ok === false && down.error === 'unreachable');
    const gateway = await scopeApi.createPrksBrowserScope({ indexedDB: createFakeIndexedDBFactory(),
        fetchImpl: loginFetch(json({}, 502)) }).signIn('a', 'b');
    record('a 502 sign-in is unreachable', gateway.error === 'unreachable');

    const m = signInApi.prksSignInMessage;
    record('wrong credentials have a fixed message', m({ ok: false, error: 'invalid_credentials', status: 401 }) === 'The login or password is not right.');
    record('throttling names the wait', m({ ok: false, error: 'throttled', status: 429, retryAfter: 30 }) === 'Too many sign-in attempts. Try again in 30 seconds.');
    record('throttling with one second is singular', m({ ok: false, error: 'throttled', status: 429, retryAfter: 1 }).endsWith('1 second.'));
    record('throttling without a wait says later', m({ ok: false, status: 429, retryAfter: null }) === 'Too many sign-in attempts. Try again later.');
    record('an unreachable server has its message', m({ ok: false, error: 'unreachable' }).startsWith('PRKS did not answer.'));
    record('an unknown refusal never echoes the code', m({ ok: false, error: '<b>x</b>', status: 400 }) === 'PRKS could not sign you in. Try again.');
    record('success has no message', m({ ok: true }) === '');

    const fakeScope = (needs) => ({ resolve: () => Promise.resolve(null), needsSignIn: () => needs });
    record('startSignIn without a scope shows nothing', (await signInApi.prksStartSignIn(null, {}, () => {})) === false);
    record('startSignIn with a session shows nothing', (await signInApi.prksStartSignIn(fakeScope(false), {}, () => {})) === false);
}

async function main() {
    await testResolve();
    await testBoundedRead();
    await testSignOut();
    await testSignIn();
    await testAnnounceAndLifecycle();
    await testVerify();
    await testStores();
    await testHold();
    await testEpoch();
    await testWorker();
    await testWorkerBindings();
    await testWorkerEpochs();
    console.log(passed + ' passed, ' + failed + ' failed');
    if (failed) process.exit(1);
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});
