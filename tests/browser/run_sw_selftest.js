#!/usr/bin/env node
'use strict';

/* Deterministic coverage for the app-shell/PDF service worker (sw.js).
 * Eligibility rules are pure functions -- exercised directly, no real
 * ServiceWorkerGlobalScope needed. createHandlers() integration tests use a
 * tiny in-memory fake Cache Storage plus Node's built-in fetch API classes
 * (Response) so response semantics (status/type/redirected/clone/arrayBuffer)
 * are real, not hand-waved.
 */

const path = require('path');
const rootDir = path.resolve(__dirname, '../..');
const sw = require(path.join(rootDir, 'frontend/sw.js'));

let passed = 0;
let failed = 0;

function record(name, ok, detail) {
    if (ok) passed += 1;
    else failed += 1;
    console.log((ok ? 'PASS  ' : 'FAIL  ') + name + (detail ? ' ' + detail : ''));
}

function assertEq(name, got, want) {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    record(name, ok, ok ? '' : 'got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want));
}

function assert(name, ok, detail) {
    record(name, !!ok, ok ? '' : detail || '');
}

async function assertRejectsOrThrows(name, fn) {
    try {
        await fn();
        record(name, false, 'did not throw');
    } catch (_e) {
        record(name, true, '');
    }
}

function fakeHeaders(map) {
    const m = new Map(Object.entries(map || {}));
    return {
        get: function (k) {
            const v = m.get(String(k).toLowerCase());
            return v == null ? null : v;
        },
    };
}

function fakeRequest(opts) {
    const o = opts || {};
    return {
        method: o.method || 'GET',
        url: o.url || 'https://prks.example/',
        mode: o.mode || 'same-origin',
        headers: fakeHeaders(o.headers),
    };
}

function makeFakeCache() {
    // Real Cache Storage hands back an independently-readable Response object on every
    // match(), even though it stores one logical entry -- reading one match()'d response's
    // body must never disturb a later match() of the same key. Clone on the way out (never
    // on the way in) so tests that read a matched response's body more than once behave like
    // a real browser Cache, not like a naive object-identity Map.
    const map = new Map();
    return {
        put: async function (key, response) {
            map.set(typeof key === 'string' ? key : key.url, response);
        },
        match: async function (key) {
            const stored = map.get(typeof key === 'string' ? key : key.url);
            return stored ? stored.clone() : undefined;
        },
        delete: async function (key) {
            return map.delete(typeof key === 'string' ? key : key.url);
        },
        keys: async function () {
            return Array.from(map.keys());
        },
        _map: map,
    };
}

function makeFakeCacheStorage() {
    const stores = new Map();
    return {
        open: async function (name) {
            if (!stores.has(name)) stores.set(name, makeFakeCache());
            return stores.get(name);
        },
        delete: async function (name) {
            return stores.delete(name);
        },
        keys: async function () {
            return Array.from(stores.keys());
        },
        _stores: stores,
    };
}

async function run() {
    /* ---- isGetRequest ---- */
    assert('GET is a get request', sw.isGetRequest(fakeRequest({ method: 'GET' })));
    assert('missing method defaults to GET', sw.isGetRequest({ url: 'https://x/' }));
    assert('POST is not a get request', !sw.isGetRequest(fakeRequest({ method: 'POST' })));
    assert('DELETE is not a get request', !sw.isGetRequest(fakeRequest({ method: 'DELETE' })));
    assert('PATCH is not a get request', !sw.isGetRequest(fakeRequest({ method: 'PATCH' })));
    assert('null request is not a get request', !sw.isGetRequest(null));

    /* ---- isSameOriginUrl ---- */
    const origin = 'https://prks.example';
    assert('same-origin absolute url', sw.isSameOriginUrl('https://prks.example/js/app.js', origin));
    assert('same-origin relative url', sw.isSameOriginUrl('/js/app.js', origin));
    assert('cross-origin url rejected', !sw.isSameOriginUrl('https://evil.example/x', origin));
    assert('malformed base origin rejected', !sw.isSameOriginUrl('/js/app.js', 'not-a-valid-origin'));

    /* ---- pathnameOf ---- */
    assertEq('pathnameOf strips query', sw.pathnameOf('/api/pdfs/abc.pdf?x=1', origin), '/api/pdfs/abc.pdf');
    assertEq('pathnameOf absolute url', sw.pathnameOf('https://prks.example/js/app.js', origin), '/js/app.js');

    /* ---- isNavigationRequest ---- */
    assert('navigate mode is navigation', sw.isNavigationRequest(fakeRequest({ mode: 'navigate' })));
    assert('same-origin mode is not navigation', !sw.isNavigationRequest(fakeRequest({ mode: 'same-origin' })));

    /* ---- isStaticEligiblePath ---- */
    assert('js path eligible', sw.isStaticEligiblePath('/js/app.js'));
    assert('vendor path eligible', sw.isStaticEligiblePath('/vendor/pdfium.wasm'));
    assert('css path eligible', sw.isStaticEligiblePath('/css/style.css'));
    assert('icons path eligible', sw.isStaticEligiblePath('/icons/icon-192.png'));
    assert('manifest extra path eligible', sw.isStaticEligiblePath('/manifest.webmanifest'));
    assert('favicon extra path eligible', sw.isStaticEligiblePath('/favicon.svg'));
    assert('api path not static-eligible', !sw.isStaticEligiblePath('/api/works'));
    assert('unrelated path not static-eligible', !sw.isStaticEligiblePath('/random.txt'));

    /* ---- isManagedPdfPath ---- */
    assert('managed pdf path matches', sw.isManagedPdfPath('/api/pdfs/abc123.pdf'));
    assert('nested pdf path rejected', !sw.isManagedPdfPath('/api/pdfs/abc/def.pdf'));
    assert('bare pdfs root rejected', !sw.isManagedPdfPath('/api/pdfs'));
    assert('trailing-slash pdfs root rejected', !sw.isManagedPdfPath('/api/pdfs/'));
    assert('unrelated api path rejected', !sw.isManagedPdfPath('/api/works/W-1'));

    /* ---- hasRangeHeader ---- */
    assert('range header present', sw.hasRangeHeader(fakeRequest({ headers: { range: 'bytes=0-99' } })));
    assert('range header absent', !sw.hasRangeHeader(fakeRequest({})));
    assert('null request has no range header', !sw.hasRangeHeader(null));

    /* ---- parseRangeHeaderValue ---- */
    assertEq('range start-end', sw.parseRangeHeaderValue('bytes=0-99', 1000), { start: 0, end: 99 });
    assertEq('range open-ended', sw.parseRangeHeaderValue('bytes=100-', 1000), { start: 100, end: 999 });
    assertEq('range suffix', sw.parseRangeHeaderValue('bytes=-500', 1000), { start: 500, end: 999 });
    assertEq('range end clamped to total', sw.parseRangeHeaderValue('bytes=900-999999', 1000), { start: 900, end: 999 });
    assertEq('range malformed unit', sw.parseRangeHeaderValue('items=0-99', 1000), null);
    assertEq('range garbage value', sw.parseRangeHeaderValue('bytes=abc', 1000), null);
    assertEq('range zero total', sw.parseRangeHeaderValue('bytes=0-99', 0), null);
    assertEq('range start beyond total', sw.parseRangeHeaderValue('bytes=2000-2005', 1000), null);
    assertEq('range null value', sw.parseRangeHeaderValue(null, 1000), null);
    assertEq('range empty spec', sw.parseRangeHeaderValue('bytes=-', 1000), null);

    /* ---- isCacheableStaticResponse ---- */
    assert('200 default response cacheable', sw.isCacheableStaticResponse({ status: 200, type: 'default', redirected: false }));
    assert('404 not cacheable', !sw.isCacheableStaticResponse({ status: 404, type: 'default', redirected: false }));
    assert('500 not cacheable', !sw.isCacheableStaticResponse({ status: 500, type: 'default', redirected: false }));
    assert('opaque response not cacheable', !sw.isCacheableStaticResponse({ status: 200, type: 'opaque', redirected: false }));
    assert('error response type not cacheable', !sw.isCacheableStaticResponse({ status: 200, type: 'error', redirected: false }));
    assert('redirected response not cacheable', !sw.isCacheableStaticResponse({ status: 200, type: 'default', redirected: true }));
    assert('null response not cacheable', !sw.isCacheableStaticResponse(null));

    /* ---- isWholeFilePdfResponse ---- */
    assert(
        'plain 200 pdf response is whole-file',
        sw.isWholeFilePdfResponse(fakeRequest({}), { status: 200, headers: fakeHeaders({}) })
    );
    assert(
        'ranged request response not whole-file',
        !sw.isWholeFilePdfResponse(fakeRequest({ headers: { range: 'bytes=0-1' } }), { status: 200, headers: fakeHeaders({}) })
    );
    assert(
        'content-range response not whole-file',
        !sw.isWholeFilePdfResponse(fakeRequest({}), { status: 200, headers: fakeHeaders({ 'content-range': 'bytes 0-1/2' }) })
    );
    assert(
        '206 response not whole-file',
        !sw.isWholeFilePdfResponse(fakeRequest({}), { status: 206, headers: fakeHeaders({}) })
    );

    /* ---- shouldRetireCache ---- */
    assert('old shell cache retired', sw.shouldRetireCache('prks-shell-v0'));
    assert('old static cache retired', sw.shouldRetireCache('prks-static-v0'));
    assert('current shell cache kept', !sw.shouldRetireCache(sw.PRKS_SW_SHELL_CACHE));
    assert('current static cache kept', !sw.shouldRetireCache(sw.PRKS_SW_STATIC_CACHE));
    assert('current pdf cache kept', !sw.shouldRetireCache(sw.PRKS_SW_PDF_CACHE));
    assert('unrelated cache name kept', !sw.shouldRetireCache('some-other-cache'));

    /* ==================================================================
     * Integration tests through createHandlers() with a fake Cache Storage
     * and real Response objects.
     * ================================================================== */

    /* ---- handleStatic: network success caches, failure falls back to cache ---- */
    {
        const cacheStorage = makeFakeCacheStorage();
        let networkCalls = 0;
        const handlers = sw.createHandlers({
            caches: cacheStorage,
            fetchImpl: function () {
                networkCalls += 1;
                return Promise.resolve(new Response('body-a', { status: 200 }));
            },
        });
        const req = fakeRequest({ url: 'https://prks.example/js/app.js' });
        const res1 = await handlers.handleStatic(req);
        assertEq('handleStatic online returns network body', await res1.text(), 'body-a');
        assertEq('handleStatic online called network once', networkCalls, 1);
        const staticCache = await cacheStorage.open(sw.PRKS_SW_STATIC_CACHE);
        const cachedEntry = await staticCache.match(req);
        assert('handleStatic online populated static cache', !!cachedEntry);
    }
    {
        const cacheStorage = makeFakeCacheStorage();
        const staticCache = await cacheStorage.open(sw.PRKS_SW_STATIC_CACHE);
        const req = fakeRequest({ url: 'https://prks.example/js/app.js' });
        await staticCache.put(req, new Response('cached-body', { status: 200 }));
        const handlers = sw.createHandlers({
            caches: cacheStorage,
            fetchImpl: function () {
                return Promise.reject(new Error('offline'));
            },
        });
        const res = await handlers.handleStatic(req);
        assertEq('handleStatic offline serves cached body', await res.text(), 'cached-body');
    }
    await assertRejectsOrThrows('handleStatic offline without cache throws', async function () {
        const cacheStorage = makeFakeCacheStorage();
        const handlers = sw.createHandlers({
            caches: cacheStorage,
            fetchImpl: function () {
                return Promise.reject(new Error('offline'));
            },
        });
        await handlers.handleStatic(fakeRequest({ url: 'https://prks.example/js/missing.js' }));
    });
    {
        // A non-cacheable network response (e.g. 404) must not be written to the static cache.
        const cacheStorage = makeFakeCacheStorage();
        const handlers = sw.createHandlers({
            caches: cacheStorage,
            fetchImpl: function () {
                return Promise.resolve(new Response('not found', { status: 404 }));
            },
        });
        const req = fakeRequest({ url: 'https://prks.example/js/missing.js' });
        await handlers.handleStatic(req);
        const staticCache = await cacheStorage.open(sw.PRKS_SW_STATIC_CACHE);
        assert('handleStatic never caches a 404 response', !(await staticCache.match(req)));
    }

    /* ---- handleNavigation: network first, cached shell fallback, 503 if nothing cached ---- */
    {
        const cacheStorage = makeFakeCacheStorage();
        const handlers = sw.createHandlers({
            caches: cacheStorage,
            fetchImpl: function () {
                return Promise.resolve(new Response('<html>live</html>', { status: 200 }));
            },
        });
        const res = await handlers.handleNavigation(fakeRequest({ mode: 'navigate' }));
        assertEq('handleNavigation online returns live shell', await res.text(), '<html>live</html>');
        const shellCache = await cacheStorage.open(sw.PRKS_SW_SHELL_CACHE);
        assert('handleNavigation online caches shell under /', !!(await shellCache.match('/')));
    }
    {
        const cacheStorage = makeFakeCacheStorage();
        const shellCache = await cacheStorage.open(sw.PRKS_SW_SHELL_CACHE);
        await shellCache.put('/', new Response('<html>cached-shell</html>', { status: 200 }));
        const handlers = sw.createHandlers({
            caches: cacheStorage,
            fetchImpl: function () {
                return Promise.reject(new Error('offline'));
            },
        });
        const res = await handlers.handleNavigation(fakeRequest({ mode: 'navigate' }));
        assertEq('handleNavigation offline falls back to cached shell', await res.text(), '<html>cached-shell</html>');
    }
    {
        const cacheStorage = makeFakeCacheStorage();
        const handlers = sw.createHandlers({
            caches: cacheStorage,
            fetchImpl: function () {
                return Promise.reject(new Error('offline'));
            },
        });
        const res = await handlers.handleNavigation(fakeRequest({ mode: 'navigate' }));
        assertEq('handleNavigation offline with no cached shell returns 503', res.status, 503);
    }

    /* ---- handlePdfRequest: whole-file caching + offline Range slicing ---- */
    {
        const cacheStorage = makeFakeCacheStorage();
        const pdfBody = Buffer.from('PDF-WHOLE-FILE-CONTENT-0123456789');
        let networkCalls = 0;
        const handlers = sw.createHandlers({
            caches: cacheStorage,
            fetchImpl: function () {
                networkCalls += 1;
                return Promise.resolve(
                    new Response(pdfBody, { status: 200, headers: { 'Content-Type': 'application/pdf' } })
                );
            },
        });
        const pathname = '/api/pdfs/abc123.pdf';
        const wholeReq = fakeRequest({ url: 'https://prks.example' + pathname });
        const res1 = await handlers.handlePdfRequest(wholeReq, pathname);
        const buf1 = Buffer.from(await res1.arrayBuffer());
        assert('handlePdfRequest online whole-file returns full body', buf1.equals(pdfBody));

        const pdfCache = await cacheStorage.open(sw.PRKS_SW_PDF_CACHE);
        assert('handlePdfRequest online whole-file populated pdf cache', !!(await pdfCache.match(pathname)));

        // Now simulate a Range request while online: the live network wins, and a Range
        // response must never be written into the whole-file cache slot.
        const beforeRangeCached = await (await pdfCache.match(pathname)).clone().arrayBuffer();
        const rangeReqOnline = fakeRequest({ url: 'https://prks.example' + pathname, headers: { range: 'bytes=0-3' } });
        await handlers.handlePdfRequest(rangeReqOnline, pathname);
        const afterRangeCached = await (await pdfCache.match(pathname)).clone().arrayBuffer();
        assertEq(
            'handlePdfRequest online range request never overwrites whole-file cache entry',
            Buffer.from(afterRangeCached).equals(Buffer.from(beforeRangeCached)),
            true
        );

        // Offline whole-file request: served straight from the whole-file cache.
        const offlineHandlers = sw.createHandlers({
            caches: cacheStorage,
            fetchImpl: function () {
                return Promise.reject(new Error('offline'));
            },
        });
        const res2 = await offlineHandlers.handlePdfRequest(wholeReq, pathname);
        const buf2 = Buffer.from(await res2.arrayBuffer());
        assert('handlePdfRequest offline whole-file serves cached body', buf2.equals(pdfBody));

        // Offline Range request: synthesized 206 sliced from the cached whole file.
        const rangeReqOffline = fakeRequest({ url: 'https://prks.example' + pathname, headers: { range: 'bytes=4-7' } });
        const res3 = await offlineHandlers.handlePdfRequest(rangeReqOffline, pathname);
        assertEq('handlePdfRequest offline range response status is 206', res3.status, 206);
        assertEq(
            'handlePdfRequest offline range response content-range header',
            res3.headers.get('content-range'),
            'bytes 4-7/' + pdfBody.length
        );
        const sliceBuf = Buffer.from(await res3.arrayBuffer());
        assert('handlePdfRequest offline range slice matches source bytes', sliceBuf.equals(pdfBody.slice(4, 8)));

        assertEq('handlePdfRequest online network called exactly for whole-file + online range', networkCalls, 2);
    }
    {
        // Offline with nothing cached at all: explicit unavailable response, never a fake success.
        const cacheStorage = makeFakeCacheStorage();
        const handlers = sw.createHandlers({
            caches: cacheStorage,
            fetchImpl: function () {
                return Promise.reject(new Error('offline'));
            },
        });
        const pathname = '/api/pdfs/never-opened.pdf';
        const res = await handlers.handlePdfRequest(fakeRequest({ url: 'https://prks.example' + pathname }), pathname);
        assertEq('handlePdfRequest offline uncached pdf returns 503', res.status, 503);
    }
    {
        // A Range (partial) network response must never be written into the whole-file slot,
        // even the very first time the PDF is requested online.
        const cacheStorage = makeFakeCacheStorage();
        const handlers = sw.createHandlers({
            caches: cacheStorage,
            fetchImpl: function () {
                return Promise.resolve(
                    new Response(Buffer.from('partial'), {
                        status: 206,
                        headers: { 'Content-Range': 'bytes 0-6/100' },
                    })
                );
            },
        });
        const pathname = '/api/pdfs/ranged-first.pdf';
        await handlers.handlePdfRequest(
            fakeRequest({ url: 'https://prks.example' + pathname, headers: { range: 'bytes=0-6' } }),
            pathname
        );
        const pdfCache = await cacheStorage.open(sw.PRKS_SW_PDF_CACHE);
        assert('range-only first response never seeds the whole-file pdf cache', !(await pdfCache.match(pathname)));
    }

    /* ---- source has no generic /api/... JSON caching and no mutation queueing ---- */
    const src = require('fs').readFileSync(path.join(rootDir, 'frontend/sw.js'), 'utf8');
    assert('sw.js has no localStorage', src.indexOf('localStorage') === -1);
    assert('sw.js has no indexedDB usage', src.indexOf('indexedDB') === -1);
    assert('sw.js fetch handler comments out generic /api passthrough', src.indexOf("notably /api/... JSON -- passes straight through") !== -1);

    if (failed) {
        console.log(failed + ' failed, ' + passed + ' passed');
        process.exit(1);
    }
    console.log('All ' + passed + ' service worker checks passed, 0 failed');
    process.exit(0);
}

run().catch(function (err) {
    console.error(err && err.stack ? err.stack : err);
    console.log('1 failed, ' + passed + ' passed');
    process.exit(1);
});
