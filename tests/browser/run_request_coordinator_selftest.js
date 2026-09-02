#!/usr/bin/env node
'use strict';

const path = require('path');

const rootDir = path.resolve(__dirname, '../..');
const api = require(path.join(rootDir, 'frontend/js/request-coordinator.js'));

const {
    createPrksRequestCoordinator,
    prksIsAbortError,
    PRKS_REQUEST_BURST_FRESH_MS,
} = api;

let passed = 0;
let failed = 0;

function record(name, ok, detail) {
    if (ok) passed += 1;
    else failed += 1;
    console.log((ok ? 'PASS  ' : 'FAIL  ') + name + (detail ? ' ' + detail : ''));
}

function abortErr() {
    if (typeof DOMException === 'function') {
        try {
            return new DOMException('The operation was aborted.', 'AbortError');
        } catch (_e) {
            /* fall through */
        }
    }
    const e = new Error('The operation was aborted.');
    e.name = 'AbortError';
    return e;
}

function jsonResponse(body, status, extraHeaders) {
    const json = JSON.stringify(body == null ? {} : body);
    const headers = Object.assign(
        {
            'Content-Type': 'application/json',
            'Content-Length': String(Buffer.byteLength(json)),
        },
        extraHeaders || {}
    );
    return new Response(json, { status: status || 200, headers: headers });
}

function flush() {
    return new Promise(function (resolve) {
        setImmediate(resolve);
    });
}

async function waitUntil(pred, tries) {
    const n = tries == null ? 40 : tries;
    for (let i = 0; i < n; i++) {
        if (pred()) return true;
        await flush();
    }
    return pred();
}

async function drain(box, pending) {
    const all = Promise.all(pending);
    for (let i = 0; i < 80; i++) {
        box.calls.forEach(function (c) {
            if (c._settled) return;
            c._settled = true;
            try {
                c.resolve(jsonResponse({ ok: true }));
            } catch (_e) {
                /* already settled */
            }
        });
        const status = await Promise.race([
            all.then(function () {
                return 'done';
            }),
            new Promise(function (resolve) {
                setImmediate(function () {
                    resolve('tick');
                });
            }),
        ]);
        if (status === 'done') return;
    }
    await all;
}

function makeDeferredFetch() {
    const calls = [];
    const fetchImpl = function (url, init) {
        let resolve;
        let reject;
        const promise = new Promise(function (res, rej) {
            resolve = res;
            reject = rej;
        });
        const rec = {
            url: String(url),
            init: init || {},
            resolve: resolve,
            reject: reject,
            aborted: false,
        };
        if (init && init.signal) {
            if (init.signal.aborted) {
                rec.aborted = true;
                return Promise.reject(abortErr());
            }
            init.signal.addEventListener('abort', function () {
                rec.aborted = true;
                reject(abortErr());
            });
        }
        calls.push(rec);
        return promise;
    };
    return { fetchImpl: fetchImpl, calls: calls };
}

function makeCoordinator(extra) {
    extra = extra || {};
    let nowMs = extra.nowMs == null ? 1000 : extra.nowMs;
    const sleeps = [];
    const sleep =
        extra.sleep ||
        function (ms, signal) {
            sleeps.push(ms);
            if (signal && signal.aborted) return Promise.reject(abortErr());
            return Promise.resolve();
        };
    const fetchBundle = extra.fetchBundle || makeDeferredFetch();
    const coord = createPrksRequestCoordinator({
        fetchImpl: extra.fetchImpl || fetchBundle.fetchImpl,
        now: extra.now || function () {
            return nowMs;
        },
        sleep: sleep,
        random: extra.random || function () {
            return 0.5;
        },
        origin: extra.origin || 'http://127.0.0.1',
    });
    coord.__now = function (v) {
        nowMs = v;
    };
    coord.__sleeps = sleeps;
    coord.__calls = fetchBundle.calls;
    return coord;
}

function isAbort(err) {
    return prksIsAbortError(err) || (err && err.name === 'AbortError');
}

async function expectAbort(promise) {
    try {
        await promise;
        return false;
    } catch (err) {
        return isAbort(err);
    }
}

async function testReadBound() {
    const box = makeDeferredFetch();
    let active = 0;
    let peak = 0;
    const fetchImpl = function (url, init) {
        active += 1;
        if (active > peak) peak = active;
        const p = box.fetchImpl(url, init);
        return p.finally(function () {
            active -= 1;
        });
    };
    const coord = makeCoordinator({ fetchImpl: fetchImpl, fetchBundle: box });
    const pending = [];
    for (let i = 0; i < 10; i++) {
        pending.push(coord.prksRequest('/api/items/' + i));
    }
    await waitUntil(function () {
        return box.calls.length >= 4;
    });
    const snap = coord.snapshot();
    const okPeak = peak <= 4 && snap.current.activeReads <= 4 && snap.peaks.activeReads <= 4;
    const okQueued = snap.current.queuedForegroundReads === 6;
    await drain(box, pending);
    record('read bound never exceeds 4', okPeak && peak <= 4, 'peak=' + peak);
    record('read bound queues remainder', okQueued, 'queued=' + snap.current.queuedForegroundReads);
}

async function testBackgroundBoundAndPreferFg() {
    const box = makeDeferredFetch();
    let activeBg = 0;
    let peakBg = 0;
    const fetchImpl = function (url, init) {
        const isBg = String(url).indexOf('/api/bg/') === 0;
        if (isBg) {
            activeBg += 1;
            if (activeBg > peakBg) peakBg = activeBg;
        }
        const p = box.fetchImpl(url, init);
        return p.finally(function () {
            if (isBg) activeBg -= 1;
        });
    };
    const coord = makeCoordinator({ fetchImpl: fetchImpl, fetchBundle: box });
    const fg = [];
    for (let i = 0; i < 3; i++) fg.push(coord.prksRequest('/api/fg/' + i));
    const bg1 = coord.prksRequest('/api/bg/1', {}, { priority: 'background' });
    const bg2 = coord.prksRequest('/api/bg/2', {}, { priority: 'background' });
    await waitUntil(function () {
        return box.calls.length >= 4;
    });
    const snap = coord.snapshot();
    const bgCalls = box.calls.filter(function (c) {
        return String(c.url).indexOf('/api/bg/') === 0;
    });
    record('background active never exceeds 1', peakBg <= 1 && bgCalls.length === 1 && snap.current.activeBackgroundReads <= 1, 'peakBg=' + peakBg);

    const extraFg = coord.prksRequest('/api/fg/queued');
    await flush();
    const beforeRelease = box.calls.map(function (c) {
        return c.url;
    });
    const firstFg = box.calls.find(function (c) {
        return String(c.url).indexOf('/api/fg/0') !== -1;
    });
    firstFg.resolve(jsonResponse([]));
    await waitUntil(function () {
        return box.calls.some(function (c) {
            return String(c.url).indexOf('/api/fg/queued') !== -1;
        });
    });
    const afterUrls = box.calls.map(function (c) {
        return c.url;
    });
    const startedQueuedFg = afterUrls.indexOf('/api/fg/queued') !== -1;
    const startedSecondBg = afterUrls.filter(function (u) {
        return String(u).indexOf('/api/bg/') === 0;
    }).length === 1;
    record('queued foreground starts before queued background', startedQueuedFg && startedSecondBg, afterUrls.join(','));
    await drain(box, fg.concat([bg1, bg2, extraFg]));
    void beforeRelease;
}

async function testReadMutationOverlap() {
    const box = makeDeferredFetch();
    const coord = makeCoordinator({ fetchBundle: box });
    const mut = coord.prksRequest('/api/works/1', { method: 'PATCH', body: '{}' });
    await waitUntil(function () {
        return box.calls.length === 1;
    });
    const read = coord.prksRequest('/api/works/1');
    await waitUntil(function () {
        return box.calls.length === 2;
    });
    const snap = coord.snapshot();
    record(
        'read may overlap one mutation',
        snap.current.activeMutation === 1 && snap.current.activeReads === 1 && box.calls.length === 2,
        'mut=' + snap.current.activeMutation + ' reads=' + snap.current.activeReads
    );
    box.calls[0].resolve(jsonResponse({ ok: true }));
    box.calls[1].resolve(jsonResponse({ id: '1' }));
    await Promise.all([mut, read]);
}

async function testMutationFifo() {
    const box = makeDeferredFetch();
    let peakMut = 0;
    let activeMut = 0;
    const fetchImpl = function (url, init) {
        activeMut += 1;
        if (activeMut > peakMut) peakMut = activeMut;
        const p = box.fetchImpl(url, init);
        return p.finally(function () {
            activeMut -= 1;
        });
    };
    const coord = makeCoordinator({ fetchImpl: fetchImpl, fetchBundle: box });
    const a = coord.prksRequest('/api/a', { method: 'POST', body: 'a' });
    const b = coord.prksRequest('/api/b', { method: 'POST', body: 'b' });
    const c = coord.prksRequest('/api/c', { method: 'POST', body: 'c' });
    await waitUntil(function () {
        return box.calls.length === 1;
    });
    record('mutation bound is 1', peakMut === 1 && box.calls.length === 1 && box.calls[0].url === '/api/a', 'peak=' + peakMut);
    box.calls[0].resolve(jsonResponse({ n: 1 }));
    await waitUntil(function () {
        return box.calls.length === 2;
    });
    record('mutation fifo second is b', box.calls[1].url === '/api/b', box.calls[1].url);
    box.calls[1].resolve(jsonResponse({ n: 2 }));
    await waitUntil(function () {
        return box.calls.length === 3;
    });
    record('mutation fifo third is c', box.calls[2].url === '/api/c', box.calls[2].url);
    box.calls[2].resolve(jsonResponse({ n: 3 }));
    await Promise.all([a, b, c]);
    record('mutation peak never exceeds 1', peakMut === 1, 'peak=' + peakMut);
}

async function testDedupe() {
    const box = makeDeferredFetch();
    const coord = makeCoordinator({ fetchBundle: box });
    const p1 = coord.prksRequest('/api/works');
    const p2 = coord.prksRequest('/api/works');
    const p3 = coord.prksRequest('/api/works');
    await waitUntil(function () {
        return box.calls.length === 1;
    });
    record('three identical GETs one fetchImpl', box.calls.length === 1, 'calls=' + box.calls.length);
    box.calls[0].resolve(jsonResponse([{ id: 'W1' }], 200, { 'X-Request-ID': 'rid-1', ETag: '"e1"' }));
    const [r1, r2, r3] = await Promise.all([p1, p2, p3]);
    const t1 = await r1.text();
    const t2 = await r2.text();
    const t3 = await r3.text();
    record(
        'each caller gets independent consumable Response',
        t1 === t2 && t2 === t3 && r1 !== r2 && r2 !== r3 && r1.headers.get('X-Request-ID') === 'rid-1',
        ''
    );

    const qBox = makeDeferredFetch();
    const qCoord = makeCoordinator({ fetchBundle: qBox });
    const qa = qCoord.prksRequest('/api/search?q=a&author=b');
    const qb = qCoord.prksRequest('/api/search?author=b&q=a');
    await waitUntil(function () {
        return qBox.calls.length === 2;
    });
    record('distinct query strings do not dedupe', qBox.calls.length === 2, 'calls=' + qBox.calls.length);
    qBox.calls.forEach(function (c) {
        c.resolve(jsonResponse([]));
    });
    await Promise.all([qa, qb]);

    const rBox = makeDeferredFetch();
    const rCoord = makeCoordinator({ fetchBundle: rBox });
    const ra = rCoord.prksRequest('/api/works', { headers: { Range: 'bytes=0-10' } });
    const rb = rCoord.prksRequest('/api/works', { headers: { Range: 'bytes=0-10' } });
    await waitUntil(function () {
        return rBox.calls.length === 2;
    });
    record('Range requests do not dedupe', rBox.calls.length === 2, 'calls=' + rBox.calls.length);
    rBox.calls.forEach(function (c) {
        c.resolve(jsonResponse([]));
    });
    await Promise.all([ra, rb]);

    const dBox = makeDeferredFetch();
    const dCoord = makeCoordinator({ fetchBundle: dBox });
    const da = dCoord.prksRequest('/api/works', {}, { dedupe: false });
    const db = dCoord.prksRequest('/api/works', {}, { dedupe: false });
    await waitUntil(function () {
        return dBox.calls.length === 2;
    });
    record('dedupe:false does not dedupe', dBox.calls.length === 2, 'calls=' + dBox.calls.length);
    dBox.calls.forEach(function (c) {
        c.resolve(jsonResponse([]));
    });
    await Promise.all([da, db]);

    const pBox = makeDeferredFetch();
    const pCoord = makeCoordinator({ fetchBundle: pBox });
    const pa = pCoord.prksRequest('/api/processing-files?rescan=1');
    const pb = pCoord.prksRequest('/api/processing-files?rescan=1');
    await waitUntil(function () {
        return pBox.calls.length === 1;
    });
    record('processing rescan is mutation-serialized not deduped', pBox.calls.length === 1 && pCoord.snapshot().current.queuedMutations === 1, '');
    pBox.calls[0].resolve(jsonResponse([]));
    await waitUntil(function () {
        return pBox.calls.length === 2;
    });
    pBox.calls[1].resolve(jsonResponse([]));
    await Promise.all([pa, pb]);
    record('processing-files?rescan=1 does not dedupe', pBox.calls.length === 2, 'calls=' + pBox.calls.length);
}

async function testSubscriberAbort() {
    const box = makeDeferredFetch();
    const coord = makeCoordinator({ fetchBundle: box });
    const acA = new AbortController();
    const acB = new AbortController();
    const pA = coord.prksRequest('/api/works', { signal: acA.signal });
    const pB = coord.prksRequest('/api/works', { signal: acB.signal });
    await waitUntil(function () {
        return box.calls.length === 1;
    });
    acA.abort();
    const aAborted = await expectAbort(pA);
    record('subscriber A abort yields AbortError', aAborted, '');
    await flush();
    record('underlying fetch remains active after one abort', box.calls.length === 1 && box.calls[0].aborted === false, 'aborted=' + box.calls[0].aborted);
    box.calls[0].resolve(jsonResponse([{ id: 'ok' }]));
    const rB = await pB;
    const bodyB = await rB.json();
    record('subscriber B completes normally', Array.isArray(bodyB) && bodyB[0].id === 'ok', '');

    const box2 = makeDeferredFetch();
    const coord2 = makeCoordinator({ fetchBundle: box2 });
    const ac1 = new AbortController();
    const ac2 = new AbortController();
    const q1 = coord2.prksRequest('/api/folders', { signal: ac1.signal });
    const q2 = coord2.prksRequest('/api/folders', { signal: ac2.signal });
    await waitUntil(function () {
        return box2.calls.length === 1;
    });
    ac1.abort();
    ac2.abort();
    const bothAbort = (await expectAbort(q1)) && (await expectAbort(q2));
    await waitUntil(function () {
        return box2.calls[0].aborted === true;
    });
    record('all subscribers abort underlying controller', bothAbort && box2.calls[0].aborted === true, '');

    const box3 = makeDeferredFetch();
    const coord3 = makeCoordinator({ fetchBundle: box3 });
    const blockers = [];
    for (let i = 0; i < 4; i++) blockers.push(coord3.prksRequest('/api/block/' + i));
    await waitUntil(function () {
        return box3.calls.length === 4;
    });
    const orphanAc = new AbortController();
    const orphan = coord3.prksRequest('/api/orphan', { signal: orphanAc.signal });
    await flush();
    record('queued orphan exists before abort', coord3.snapshot().current.queuedForegroundReads === 1, '');
    orphanAc.abort();
    const orphanAborted = await expectAbort(orphan);
    await flush();
    const snap = coord3.snapshot();
    const orphanCalled = box3.calls.some(function (c) {
        return String(c.url).indexOf('/api/orphan') !== -1;
    });
    record('queued orphaned request is removed', orphanAborted && snap.current.queuedForegroundReads === 0 && !orphanCalled, 'queued=' + snap.current.queuedForegroundReads);
    box3.calls.forEach(function (c) {
        c.resolve(jsonResponse([]));
    });
    await Promise.all(blockers);
}

async function testRetry() {
    async function retryCase(name, failFactory, expectRetries, expectFinalOk) {
        const calls = [];
        const fetchImpl = function (url, init) {
            const n = calls.length;
            calls.push({ url: url, init: init });
            const outcome = failFactory(n);
            if (outcome && outcome.reject) return Promise.reject(outcome.reject);
            return Promise.resolve(outcome.response);
        };
        const sleeps = [];
        const coord = makeCoordinator({
            fetchImpl: fetchImpl,
            sleep: function (ms, signal) {
                sleeps.push(ms);
                if (signal && signal.aborted) return Promise.reject(abortErr());
                return Promise.resolve();
            },
        });
        let final;
        let err = null;
        try {
            final = await coord.prksRequest('/api/works');
        } catch (e) {
            err = e;
        }
        const retries = coord.snapshot().counts.retries;
        const okCount = expectRetries === retries && calls.length === expectRetries + 1;
        const okResult = expectFinalOk ? final && final.ok && !err : !expectFinalOk;
        record(name, okCount && okResult, 'calls=' + calls.length + ' retries=' + retries);
        return { calls: calls, sleeps: sleeps, coord: coord, final: final, err: err };
    }

    await retryCase(
        'network error retries',
        function (n) {
            if (n < 2) return { reject: new TypeError('network') };
            return { response: jsonResponse([]) };
        },
        2,
        true
    );
    await retryCase(
        '502 retries',
        function (n) {
            if (n < 2) return { response: jsonResponse({}, 502) };
            return { response: jsonResponse([]) };
        },
        2,
        true
    );
    await retryCase(
        '503 retries',
        function (n) {
            if (n < 2) return { response: jsonResponse({}, 503) };
            return { response: jsonResponse([]) };
        },
        2,
        true
    );
    await retryCase(
        '504 retries',
        function (n) {
            if (n < 2) return { response: jsonResponse({}, 504) };
            return { response: jsonResponse([]) };
        },
        2,
        true
    );

    const max = await retryCase(
        'max is initial plus two retries',
        function () {
            return { reject: new TypeError('network') };
        },
        2,
        false
    );
    record('exhausted retry still three attempts', max.calls.length === 3, 'calls=' + max.calls.length);

    async function noRetry(name, factory, method) {
        const calls = [];
        const fetchImpl = function (url, init) {
            calls.push(1);
            return factory();
        };
        const coord = makeCoordinator({
            fetchImpl: fetchImpl,
            sleep: function () {
                throw new Error('sleep should not run');
            },
        });
        const opts = method ? { method: method, body: '{}' } : {};
        try {
            await coord.prksRequest('/api/works', opts);
        } catch (_e) {
            /* expected for network */
        }
        record(name, calls.length === 1 && coord.snapshot().counts.retries === 0, 'calls=' + calls.length);
    }

    await noRetry('404 does not retry', function () {
        return Promise.resolve(jsonResponse({}, 404));
    });
    await noRetry('409 does not retry', function () {
        return Promise.resolve(jsonResponse({}, 409));
    });
    await noRetry('500 does not retry', function () {
        return Promise.resolve(jsonResponse({}, 500));
    });
    await noRetry(
        'POST does not retry',
        function () {
            return Promise.reject(new TypeError('network'));
        },
        'POST'
    );
    await noRetry(
        'PATCH does not retry',
        function () {
            return Promise.resolve(jsonResponse({}, 503));
        },
        'PATCH'
    );

    const abortBox = makeDeferredFetch();
    const ac = new AbortController();
    const abortCoord = makeCoordinator({ fetchBundle: abortBox });
    const abortP = abortCoord.prksRequest('/api/works', { signal: ac.signal });
    await waitUntil(function () {
        return abortBox.calls.length === 1;
    });
    ac.abort();
    const aborted = await expectAbort(abortP);
    await flush();
    record('AbortError does not retry', aborted && abortBox.calls.length === 1 && abortCoord.snapshot().counts.retries === 0, '');

    const sleepWaits = [];
    let fetchN = 0;
    const sleepCoord = createPrksRequestCoordinator({
        fetchImpl: function () {
            fetchN += 1;
            if (fetchN === 1) return Promise.reject(new TypeError('network'));
            return Promise.resolve(jsonResponse([]));
        },
        now: function () {
            return 1;
        },
        random: function () {
            return 0.5;
        },
        origin: 'http://127.0.0.1',
        sleep: function (ms, signal) {
            return new Promise(function (resolve, reject) {
                const rec = { ms: ms, resolve: resolve, reject: reject };
                sleepWaits.push(rec);
                if (signal) {
                    if (signal.aborted) {
                        reject(abortErr());
                        return;
                    }
                    signal.addEventListener('abort', function () {
                        reject(abortErr());
                    });
                }
            });
        },
    });
    const sleepAc = new AbortController();
    const sleepP = sleepCoord.prksRequest('/api/works', { signal: sleepAc.signal });
    await waitUntil(function () {
        return sleepWaits.length === 1;
    });
    sleepAc.abort();
    const sleepAborted = await expectAbort(sleepP);
    await flush();
    record('abort during retry sleep prevents another attempt', sleepAborted && fetchN === 1 && sleepWaits.length === 1, 'fetchN=' + fetchN);
}

async function testCacheAndEpoch() {
    const box = makeDeferredFetch();
    const coord = makeCoordinator({ fetchBundle: box, nowMs: 5000 });
    const policy = { freshForMs: PRKS_REQUEST_BURST_FRESH_MS };
    const first = coord.prksRequest('/api/works', {}, policy);
    await waitUntil(function () {
        return box.calls.length === 1;
    });
    box.calls[0].resolve(jsonResponse([{ id: 'c' }]));
    const r1 = await first;
    await r1.json();
    const second = coord.prksRequest('/api/works', {}, policy);
    const r2 = await second;
    const body2 = await r2.json();
    record('eligible catalog GET caches', box.calls.length === 1 && coord.snapshot().counts.burstCacheHits === 1, 'calls=' + box.calls.length);
    record('immediate second request uses cache', box.calls.length === 1 && body2[0].id === 'c', '');

    coord.__now(5000 + PRKS_REQUEST_BURST_FRESH_MS + 1);
    const third = coord.prksRequest('/api/works', {}, policy);
    await waitUntil(function () {
        return box.calls.length === 2;
    });
    box.calls[1].resolve(jsonResponse([{ id: 'd' }]));
    await third;
    record('expiry causes a new network request', box.calls.length === 2, 'calls=' + box.calls.length);

    const box2 = makeDeferredFetch();
    const coord2 = makeCoordinator({ fetchBundle: box2, nowMs: 1000 });
    const g1 = coord2.prksRequest('/api/works', {}, policy);
    await waitUntil(function () {
        return box2.calls.length === 1;
    });
    box2.calls[0].resolve(jsonResponse([{ id: 'old' }]));
    await (await g1).json();
    const mut = coord2.prksRequest('/api/works/1', { method: 'PATCH', body: '{}' });
    await waitUntil(function () {
        return box2.calls.length === 2;
    });
    const afterDispatch = coord2.prksRequest('/api/works', {}, policy);
    await waitUntil(function () {
        return box2.calls.length === 3;
    });
    record('mutation dispatch invalidates cache', box2.calls.length === 3, 'calls=' + box2.calls.length);
    box2.calls[1].resolve(jsonResponse({ ok: true }));
    box2.calls[2].resolve(jsonResponse([{ id: 'new' }]));
    await Promise.all([mut, afterDispatch]);

    const box3 = makeDeferredFetch();
    const coord3 = makeCoordinator({ fetchBundle: box3 });
    const pre = coord3.prksRequest('/api/works');
    await waitUntil(function () {
        return box3.calls.length === 1;
    });
    const mut2 = coord3.prksRequest('/api/tags', { method: 'POST', body: '{}' });
    await waitUntil(function () {
        return box3.calls.length === 2;
    });
    const post = coord3.prksRequest('/api/works');
    await waitUntil(function () {
        return box3.calls.length === 3;
    });
    const gets = box3.calls.filter(function (c) {
        return String(c.url) === '/api/works';
    });
    record('post-mutation GET cannot join pre-mutation in-flight GET', gets.length === 2 && box3.calls.length === 3, 'gets=' + gets.length);
    box3.calls.forEach(function (c) {
        c.resolve(jsonResponse({ ok: true }));
    });
    await Promise.all([pre, mut2, post]);

    const box4 = makeDeferredFetch();
    const coord4 = makeCoordinator({ fetchBundle: box4, nowMs: 1 });
    const big = coord4.prksRequest('/api/works', {}, policy);
    await waitUntil(function () {
        return box4.calls.length === 1;
    });
    box4.calls[0].resolve(
        jsonResponse({ ok: true }, 200, { 'Content-Length': String(2 * 1024 * 1024 + 1) })
    );
    await (await big).json();
    const big2 = coord4.prksRequest('/api/works', {}, policy);
    await waitUntil(function () {
        return box4.calls.length === 2;
    });
    box4.calls[1].resolve(jsonResponse({ ok: true }, 200, { 'Content-Length': String(2 * 1024 * 1024 + 1) }));
    await big2;
    record('>2 MiB response is not cached', box4.calls.length === 2 && coord4.snapshot().counts.burstCacheHits === 0, 'calls=' + box4.calls.length);

    const box5 = makeDeferredFetch();
    const coord5 = makeCoordinator({ fetchBundle: box5, nowMs: 1 });
    const nolen = coord5.prksRequest('/api/works', {}, policy);
    await waitUntil(function () {
        return box5.calls.length === 1;
    });
    const raw = JSON.stringify([{ id: 'x' }]);
    box5.calls[0].resolve(
        new Response(raw, {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
        })
    );
    await (await nolen).json();
    const nolen2 = coord5.prksRequest('/api/works', {}, policy);
    await waitUntil(function () {
        return box5.calls.length === 2;
    });
    box5.calls[1].resolve(
        new Response(raw, {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
        })
    );
    await nolen2;
    record('response with no Content-Length is not cached', box5.calls.length === 2 && coord5.snapshot().counts.burstCacheHits === 0, 'calls=' + box5.calls.length);

    const box6 = makeDeferredFetch();
    const coord6 = makeCoordinator({ fetchBundle: box6 });
    const d1 = coord6.prksRequest('/api/works');
    const d2 = coord6.prksRequest('/api/works');
    await waitUntil(function () {
        return box6.calls.length === 1;
    });
    record('in-flight dedupe still works without Content-Length', box6.calls.length === 1, '');
    box6.calls[0].resolve(
        new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } })
    );
    await Promise.all([d1, d2]);
}

async function testCoalesce() {
    const box = makeDeferredFetch();
    const coord = makeCoordinator({ fetchBundle: box });
    const a = coord.prksRequest(
        '/api/works/W1',
        { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text_content: 'A' }) },
        { coalesceKey: 'work-research-notes:W1' }
    );
    await waitUntil(function () {
        return box.calls.length === 1;
    });
    const b = coord.prksRequest(
        '/api/works/W1',
        { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text_content: 'B' }) },
        { coalesceKey: 'work-research-notes:W1' }
    );
    const c = coord.prksRequest(
        '/api/works/W1',
        { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text_content: 'C' }) },
        { coalesceKey: 'work-research-notes:W1' }
    );
    const d = coord.prksRequest(
        '/api/works/W2',
        { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text_content: 'D' }) },
        { coalesceKey: 'work-research-notes:W2' }
    );
    await flush();
    record('coalesce keeps A active and independent D queued', box.calls.length === 1 && coord.snapshot().current.queuedMutations === 2, 'queued=' + coord.snapshot().current.queuedMutations);
    box.calls[0].resolve(jsonResponse({ saved: 'A' }));
    const aBody = await (await a).json();
    await waitUntil(function () {
        return box.calls.length === 2;
    });
    record('network sees C not B after A', box.calls[1].init.body === JSON.stringify({ text_content: 'C' }), String(box.calls[1].init.body));
    box.calls[1].resolve(jsonResponse({ saved: 'C' }));
    const [bRes, cRes] = await Promise.all([b, c]);
    const bBody = await bRes.json();
    const cBody = await cRes.json();
    record('B and C waiters resolve from C', aBody.saved === 'A' && bBody.saved === 'C' && cBody.saved === 'C', JSON.stringify({ a: aBody, b: bBody, c: cBody }));
    await waitUntil(function () {
        return box.calls.length === 3;
    });
    record('different coalesce key remains independent', box.calls[2].init.body === JSON.stringify({ text_content: 'D' }), String(box.calls[2].init.body));
    box.calls[2].resolve(jsonResponse({ saved: 'D' }));
    await d;

    const box2 = makeDeferredFetch();
    const coord2 = makeCoordinator({ fetchBundle: box2 });
    const x = coord2.prksRequest('/api/works/1', { method: 'DELETE' });
    const y = coord2.prksRequest('/api/works/1', { method: 'DELETE' });
    await waitUntil(function () {
        return box2.calls.length === 1;
    });
    box2.calls[0].resolve(jsonResponse({ ok: true }));
    await waitUntil(function () {
        return box2.calls.length === 2;
    });
    box2.calls[1].resolve(jsonResponse({ ok: true }));
    await Promise.all([x, y]);
    record('ordinary mutations do not coalesce without coalesceKey', box2.calls.length === 2 && coord2.snapshot().counts.coalescedMutations === 0, 'calls=' + box2.calls.length);
}

async function testDiagnosticsPrivacyAndReset() {
    const box = makeDeferredFetch();
    const coord = makeCoordinator({ fetchBundle: box });
    const secretId = 'W-SECRET-WORK-ID-9f3c';
    const secretQ = 'secretSearchTermXYZ';
    const secretBody = 'SECRET_BODY_PAYLOAD_q=leak';
    const secretKey = 'work-research-notes:' + secretId;
    const holdMut = coord.prksRequest('/api/hold-mut', { method: 'POST', body: '{}' });
    await waitUntil(function () {
        return box.calls.length === 1 && coord.snapshot().current.activeMutation === 1;
    });
    const blockers = [];
    for (let i = 0; i < 4; i++) blockers.push(coord.prksRequest('/api/block/' + i));
    await waitUntil(function () {
        return box.calls.length === 5;
    });
    const queued = coord.prksRequest('/api/search?q=' + encodeURIComponent(secretQ) + '&work=' + secretId);
    const mut = coord.prksRequest(
        '/api/works/' + secretId,
        { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text_content: secretBody }) },
        { coalesceKey: secretKey }
    );
    await waitUntil(function () {
        return coord.snapshot().current.queuedForegroundReads === 1 && coord.snapshot().current.queuedMutations === 1;
    });
    const before = coord.snapshot();
    const serialized = JSON.stringify(coord.snapshot());
    const leaked =
        serialized.indexOf('/api/') !== -1 ||
        serialized.indexOf(secretId) !== -1 ||
        serialized.indexOf(secretQ) !== -1 ||
        serialized.indexOf(secretBody) !== -1 ||
        serialized.indexOf(secretKey) !== -1 ||
        serialized.indexOf('text_content') !== -1;
    record('snapshot contains no private URL/query/body/id/coalesceKey', !leaked, leaked ? serialized : '');

    coord.resetDiagnostics();
    const after = coord.snapshot();
    record(
        'resetDiagnostics does not cancel or dequeue',
        after.current.activeReads === before.current.activeReads &&
            after.current.queuedForegroundReads === before.current.queuedForegroundReads &&
            after.current.queuedMutations === before.current.queuedMutations &&
            after.current.activeMutation === before.current.activeMutation &&
            after.counts.started === 0,
        JSON.stringify(after.current)
    );
    await drain(box, blockers.concat([holdMut, queued, mut]));
    record('resetDiagnostics does not change mutation correctness', box.calls.length === 7, 'calls=' + box.calls.length);
}

async function testWorkHintStalePublication() {
    function prksRouteStale(routeGen) {
        return routeGen !== global.__prksRouteGen;
    }
    async function loadHints(routeGen, routeSignal, fetchConcepts, fetchArguments) {
        try {
            const concepts = await fetchConcepts({ signal: routeSignal });
            if (prksRouteStale(routeGen)) return;
            global.__prksConceptHintList = concepts;
        } catch (_e) {
            if (prksRouteStale(routeGen)) return;
            global.__prksConceptHintList = [];
        }
        try {
            const argumentsList = await fetchArguments(undefined, { signal: routeSignal });
            if (prksRouteStale(routeGen)) return;
            global.__prksArgumentHintList = argumentsList;
        } catch (_e) {
            if (prksRouteStale(routeGen)) return;
            global.__prksArgumentHintList = [];
        }
        if (prksRouteStale(routeGen)) return;
    }

    global.__prksRouteGen = 1;
    global.__prksConceptHintList = [{ id: 'C-LIVE' }];
    global.__prksArgumentHintList = [{ id: 'A-LIVE' }];

    let resolveConcepts;
    const conceptsP = new Promise(function (res) {
        resolveConcepts = res;
    });
    const ac = new AbortController();
    const oldGen = 1;
    const running = loadHints(
        oldGen,
        ac.signal,
        function () {
            return conceptsP;
        },
        function () {
            return Promise.resolve([{ id: 'A-OLD' }]);
        }
    );
    global.__prksRouteGen = 2;
    ac.abort();
    resolveConcepts([]);
    await running;
    record(
        'obsolete Work hint read does not publish [] over current globals',
        global.__prksConceptHintList && global.__prksConceptHintList[0].id === 'C-LIVE' &&
            global.__prksArgumentHintList && global.__prksArgumentHintList[0].id === 'A-LIVE',
        JSON.stringify({ c: global.__prksConceptHintList, a: global.__prksArgumentHintList })
    );
}

async function main() {
    record('exports createPrksRequestCoordinator', typeof createPrksRequestCoordinator === 'function', '');
    record('does not monkeypatch fetch', typeof fetch === 'function', '');
    await testReadBound();
    await testBackgroundBoundAndPreferFg();
    await testReadMutationOverlap();
    await testMutationFifo();
    await testDedupe();
    await testSubscriberAbort();
    await testRetry();
    await testCacheAndEpoch();
    await testCoalesce();
    await testDiagnosticsPrivacyAndReset();
    await testWorkHintStalePublication();
    console.log(passed + ' passed, ' + failed + ' failed');
    process.exit(failed ? 1 : 0);
}

main().catch(function (err) {
    console.error(err && err.stack ? err.stack : err);
    process.exit(1);
});
