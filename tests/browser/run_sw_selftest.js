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
    {
        // A whole-file GET that started before the post-save install must not
        // commit its pre-edit body after that install.
        const cacheStorage = makeFakeCacheStorage();
        const pathname = '/api/pdfs/race-inflight.pdf';
        let releaseFetch;
        const fetchGate = new Promise(function (resolve) { releaseFetch = resolve; });
        const handlers = sw.createHandlers({
            caches: cacheStorage,
            fetchImpl: function () {
                return fetchGate.then(function () {
                    return new Response(Buffer.from('OLD-BYTES'), {
                        status: 200,
                        headers: { 'Content-Type': 'application/pdf' },
                    });
                });
            },
        });
        const pending = handlers.handlePdfRequest(
            fakeRequest({ url: 'https://prks.example' + pathname }),
            pathname
        );
        const pdfCache = await cacheStorage.open(sw.PRKS_SW_PDF_CACHE);
        const installed = await sw.installAuthoritativeWholeFilePdf(
            pdfCache,
            pathname,
            Buffer.from('NEW-BYTES')
        );
        assert('authoritative install during in-flight GET commits', installed === true);
        releaseFetch();
        await pending;
        await sw.settleWholeFilePdfWrites(pathname);
        const cached = Buffer.from(await (await pdfCache.match(pathname)).arrayBuffer());
        assert(
            'stale in-flight whole-file GET does not overwrite post-save bytes',
            cached.equals(Buffer.from('NEW-BYTES'))
        );
        assertEq(
            'post-save install advanced the per-path generation',
            sw.wholeFilePdfGeneration(pathname) > 0,
            true
        );
    }
    {
        // A network put already queued on the path still loses to a later
        // authoritative install: the chain runs the install after it and
        // advances the generation first.
        const cacheStorage = makeFakeCacheStorage();
        const pathname = '/api/pdfs/race-queued.pdf';
        let releasePut;
        const putGate = new Promise(function (resolve) { releasePut = resolve; });
        let puts = 0;
        const realOpen = cacheStorage.open.bind(cacheStorage);
        cacheStorage.open = async function (name) {
            const cache = await realOpen(name);
            if (!cache._raceWrapped) {
                const orig = cache.put.bind(cache);
                cache.put = async function (key, response) {
                    puts += 1;
                    if (puts === 1) await putGate;
                    return orig(key, response);
                };
                cache._raceWrapped = true;
            }
            return cache;
        };
        const handlers = sw.createHandlers({
            caches: cacheStorage,
            fetchImpl: function () {
                return Promise.resolve(new Response(Buffer.from('OLD-QUEUED'), {
                    status: 200,
                    headers: { 'Content-Type': 'application/pdf' },
                }));
            },
        });
        const pending = handlers.handlePdfRequest(
            fakeRequest({ url: 'https://prks.example' + pathname }),
            pathname
        );
        await pending;
        const pdfCache = await cacheStorage.open(sw.PRKS_SW_PDF_CACHE);
        const installPromise = sw.installAuthoritativeWholeFilePdf(
            pdfCache,
            pathname,
            Buffer.from('NEW-QUEUED')
        );
        releasePut();
        assert('queued network put then install still commits', (await installPromise) === true);
        await sw.settleWholeFilePdfWrites(pathname);
        const cached = Buffer.from(await (await pdfCache.match(pathname)).arrayBuffer());
        assert(
            'authoritative install replaces a whole-file put that was already queued',
            cached.equals(Buffer.from('NEW-QUEUED'))
        );
    }
    {
        const cacheStorage = makeFakeCacheStorage();
        const pathname = '/api/pdfs/message-install.pdf';
        const ok = await sw.handlePdfCacheMessage({
            type: sw.PDF_CACHE_INSTALL_MESSAGE,
            pathname: pathname,
            buffer: Buffer.from('FROM-MESSAGE'),
        }, cacheStorage);
        assert('pdf cache install message stores the posted bytes', ok === true);
        const pdfCache = await cacheStorage.open(sw.PRKS_SW_PDF_CACHE);
        const cached = Buffer.from(await (await pdfCache.match(pathname)).arrayBuffer());
        assert('pdf cache install message body matches', cached.equals(Buffer.from('FROM-MESSAGE')));
        assert('unrelated worker message is ignored', sw.handlePdfCacheMessage({ type: 'other' }, cacheStorage) === null);
        const rejected = await sw.handlePdfCacheMessage({
            type: sw.PDF_CACHE_INSTALL_MESSAGE,
            pathname: '/api/works/not-a-pdf',
            buffer: Buffer.from('NOPE'),
        }, cacheStorage);
        assert('pdf cache install message rejects a non-pdf path', rejected === false);
    }
    {
        // The page used to treat a 2s acknowledgement timeout as a failed
        // cache write. A put that finishes after that must still be 'ok',
        // and that 'ok' must not become "PDF cache update failed".
        const fs = require('fs');
        const vm = require('vm');
        const { MessageChannel } = require('worker_threads');
        const worksPdfSrc = fs.readFileSync(path.join(rootDir, 'frontend/js/components/works-pdf.js'), 'utf8');
        const beginMark = '/* prks-pdf-cache-install-page-begin */';
        const endMark = '/* prks-pdf-cache-install-page-end */';
        const begin = worksPdfSrc.indexOf(beginMark);
        const end = worksPdfSrc.indexOf(endMark);
        assert('page install helper is marked for extraction', begin !== -1 && end > begin);
        const slice = worksPdfSrc.slice(begin + beginMark.length, end);
        // The vm context's own ArrayBuffer would fail `instanceof` on a body
        // created in this script. Use this realm's constructor.
        const context = vm.createContext({
            MessageChannel: MessageChannel,
            setTimeout: setTimeout,
            clearTimeout: clearTimeout,
        });
        context.ArrayBuffer = ArrayBuffer;
        vm.runInContext(
            slice + '\nthis.api = { prksPostPdfCacheInstall: prksPostPdfCacheInstall, prksPdfCacheInstallOutcome: prksPdfCacheInstallOutcome, PDF_CACHE_INSTALL_ACK_TIMEOUT_MS: PDF_CACHE_INSTALL_ACK_TIMEOUT_MS };',
            context
        );
        const pageApi = context.api;
        assertEq('install ack timeout is the communication-loss bound', pageApi.PDF_CACHE_INSTALL_ACK_TIMEOUT_MS, 120000);

        function delayPut(cacheStorage, ms) {
            const realOpen = cacheStorage.open.bind(cacheStorage);
            cacheStorage.open = async function (name) {
                const cache = await realOpen(name);
                if (!cache._delayWrapped) {
                    const orig = cache.put.bind(cache);
                    cache.put = function (key, response) {
                        return new Promise(function (resolve, reject) {
                            setTimeout(function () {
                                Promise.resolve(orig(key, response)).then(resolve, reject);
                            }, ms);
                        });
                    };
                    cache._delayWrapped = true;
                }
                return cache;
            };
            return cacheStorage;
        }

        function controllerDeliveringTo(cachesApi, reply) {
            const seen = { transferIncludesBuffer: false, detached: false };
            const controller = {
                postMessage: function (data, transfer) {
                    const pagePort = (transfer || []).filter(function (item) {
                        return item && typeof item.postMessage === 'function';
                    })[0];
                    seen.transferIncludesBuffer = (transfer || []).indexOf(data.buffer) !== -1;
                    const bridge = new MessageChannel();
                    bridge.port1.onmessage = function (event) {
                        if (!reply) return;
                        Promise.resolve(sw.handlePdfCacheMessage(event.data, cachesApi)).then(function (ok) {
                            pagePort.postMessage({ ok: !!ok });
                        }, function () {
                            pagePort.postMessage({ ok: false });
                        });
                    };
                    bridge.port2.postMessage({
                        type: data.type,
                        pathname: data.pathname,
                        buffer: data.buffer,
                    }, [data.buffer]);
                    seen.detached = data.buffer.byteLength === 0;
                },
            };
            return { controller: controller, seen: seen };
        }

        const slowStorage = delayPut(makeFakeCacheStorage(), 2500);
        const pathname = '/api/pdfs/slow-install.pdf';
        const payload = Buffer.from('SLOW-BUT-STORED');
        const body = payload.buffer.slice(payload.byteOffset, payload.byteOffset + payload.byteLength);
        const slow = controllerDeliveringTo(slowStorage, true);
        const started = Date.now();
        const outcome = await pageApi.prksPostPdfCacheInstall(slow.controller, pathname, body);
        const elapsed = Date.now() - started;
        assert('slow install transfers the already-copied buffer', slow.seen.transferIncludesBuffer && slow.seen.detached);
        assert('slow install past the old 2s timeout is acknowledged', outcome === 'ok' && elapsed >= 2000, 'outcome=' + outcome + ' elapsed=' + elapsed);
        let materializationFailure = '';
        try {
            const cached = pageApi.prksPdfCacheInstallOutcome(outcome);
            if (!cached) materializationFailure = 'PDF cache update failed';
        } catch (err) {
            materializationFailure = err && err.message ? err.message : 'threw';
        }
        assert('slow successful install is not a materialization failure', materializationFailure === '');
        const pdfCache = await slowStorage.open(sw.PRKS_SW_PDF_CACHE);
        const cachedBytes = Buffer.from(await (await pdfCache.match(pathname)).arrayBuffer());
        assert('slow install stores the transferred bytes', cachedBytes.equals(payload));

        const silent = controllerDeliveringTo(makeFakeCacheStorage(), false);
        const lostBody = new ArrayBuffer(8);
        const lost = await pageApi.prksPostPdfCacheInstall(silent.controller, pathname, lostBody, { timeoutMs: 40 });
        assert('communication loss is unacknowledged, not a put rejection', lost === 'unacknowledged');
        let lostMessage = '';
        try {
            pageApi.prksPdfCacheInstallOutcome(lost);
        } catch (err) {
            lostMessage = err && err.message ? err.message : '';
        }
        assert(
            'unacknowledged install is distinct from PDF cache update failed',
            lostMessage === 'PDF cache install unacknowledged'
        );

        const rejectedBody = new ArrayBuffer(4);
        const rejecting = controllerDeliveringTo(makeFakeCacheStorage(), true);
        const rejectedOutcome = await pageApi.prksPostPdfCacheInstall(
            rejecting.controller,
            '/api/works/not-a-pdf',
            rejectedBody
        );
        assert('worker put rejection stays rejected', rejectedOutcome === 'rejected');
        assert('worker put rejection maps to a failed cache write', pageApi.prksPdfCacheInstallOutcome(rejectedOutcome) === false);
    }

    /* ---- performInstall(): required-asset failure fails the whole install,
     * decorative optional-asset failure never blocks it (AGENTS.md "Make
     * shell precache success meaningful"). Writes go into the *staging*
     * caches only -- never the live SHELL_CACHE/STATIC_CACHE names -- so a
     * concurrently-active old worker's cache is never touched by install;
     * see the "failed installs cannot poison the active cache" block below
     * for performActivate()'s promotion step. ---- */
    {
        // Happy path: every required and optional asset fetches fine ->
        // performInstall() resolves and every path lands in the staging cache.
        const cacheStorage = makeFakeCacheStorage();
        const fetched = [];
        await sw.performInstall(cacheStorage, function (path) {
            fetched.push(path);
            return Promise.resolve(new Response('body:' + path, { status: 200 }));
        });
        const shellStaging = await cacheStorage.open(sw.PRKS_SW_SHELL_STAGING_CACHE);
        const staticStaging = await cacheStorage.open(sw.PRKS_SW_STATIC_STAGING_CACHE);
        let allShellCached = true;
        for (const p of sw.PRKS_SW_SHELL_PRECACHE_PATHS) {
            if (!(await shellStaging.match(p))) allShellCached = false;
        }
        assert('performInstall happy path stages every shell path', allShellCached);
        let allStaticCached = true;
        for (const p of sw.PRKS_SW_STATIC_PRECACHE_PATHS) {
            if (!(await staticStaging.match(p))) allStaticCached = false;
        }
        assert('performInstall happy path stages every static path (required + optional)', allStaticCached);
        const shellLive = await cacheStorage.open(sw.PRKS_SW_SHELL_CACHE);
        const staticLive = await cacheStorage.open(sw.PRKS_SW_STATIC_CACHE);
        assertEq('performInstall alone never writes into the live shell cache', (await shellLive.keys()).length, 0);
        assertEq('performInstall alone never writes into the live static cache', (await staticLive.keys()).length, 0);
    }
    await assertRejectsOrThrows(
        'performInstall rejects when a required (non-optional) static asset fetch fails',
        async function () {
            const cacheStorage = makeFakeCacheStorage();
            const optional = sw.PRKS_SW_STATIC_PRECACHE_OPTIONAL_PATHS;
            await sw.performInstall(cacheStorage, function (path) {
                if (path === '/js/app.js') return Promise.reject(new Error('network down'));
                return Promise.resolve(new Response('body:' + path, { status: 200 }));
            });
        }
    );
    await assertRejectsOrThrows(
        'performInstall rejects when a required static asset responds non-ok',
        async function () {
            const cacheStorage = makeFakeCacheStorage();
            await sw.performInstall(cacheStorage, function (path) {
                if (path === '/css/style.css') return Promise.resolve(new Response('nope', { status: 404 }));
                return Promise.resolve(new Response('body:' + path, { status: 200 }));
            });
        }
    );
    await assertRejectsOrThrows('performInstall rejects when a shell navigation path fetch fails', async function () {
        const cacheStorage = makeFakeCacheStorage();
        await sw.performInstall(cacheStorage, function (path) {
            if (path === '/index.html') return Promise.reject(new Error('network down'));
            return Promise.resolve(new Response('body:' + path, { status: 200 }));
        });
    });
    {
        // The inverse: a failing *optional* decorative asset (manifest/icon)
        // must never fail the install, and every required asset still lands
        // in staging.
        const cacheStorage = makeFakeCacheStorage();
        const optionalSet = new Set(sw.PRKS_SW_STATIC_PRECACHE_OPTIONAL_PATHS);
        await sw.performInstall(cacheStorage, function (path) {
            if (optionalSet.has(path)) return Promise.reject(new Error('icon missing'));
            return Promise.resolve(new Response('body:' + path, { status: 200 }));
        });
        const staticStaging = await cacheStorage.open(sw.PRKS_SW_STATIC_STAGING_CACHE);
        let allRequiredCached = true;
        for (const p of sw.PRKS_SW_STATIC_PRECACHE_PATHS) {
            if (optionalSet.has(p)) continue;
            if (!(await staticStaging.match(p))) allRequiredCached = false;
        }
        assert('performInstall tolerates a failing optional asset and still stages every required one', allRequiredCached);
        let noOptionalCached = true;
        for (const p of optionalSet) {
            if (await staticStaging.match(p)) noOptionalCached = false;
        }
        assert('performInstall never stages a failing optional asset', noOptionalCached);
    }

    /* ---- performActivate(): promotes staging -> live and retires stale
     * caches (AGENTS.md "Make failed SW installs unable to poison the
     * active shell cache") ---- */
    {
        const cacheStorage = makeFakeCacheStorage();
        await sw.performInstall(cacheStorage, function (path) {
            return Promise.resolve(new Response('body:' + path, { status: 200 }));
        });
        await sw.performActivate(cacheStorage);
        const shellLive = await cacheStorage.open(sw.PRKS_SW_SHELL_CACHE);
        const staticLive = await cacheStorage.open(sw.PRKS_SW_STATIC_CACHE);
        let allShellPromoted = true;
        for (const p of sw.PRKS_SW_SHELL_PRECACHE_PATHS) {
            if (!(await shellLive.match(p))) allShellPromoted = false;
        }
        assert('performActivate promotes every staged shell path into the live cache', allShellPromoted);
        let allStaticPromoted = true;
        for (const p of sw.PRKS_SW_STATIC_PRECACHE_PATHS) {
            if (!(await staticLive.match(p))) allStaticPromoted = false;
        }
        assert('performActivate promotes every staged static path into the live cache', allStaticPromoted);
        assertEq(
            'performActivate deletes the shell staging cache afterward',
            cacheStorage._stores.has(sw.PRKS_SW_SHELL_STAGING_CACHE),
            false
        );
        assertEq(
            'performActivate deletes the static staging cache afterward',
            cacheStorage._stores.has(sw.PRKS_SW_STATIC_STAGING_CACHE),
            false
        );
    }
    {
        // Old-versioned cache names (a real release version bump) are
        // retired once activation completes.
        const cacheStorage = makeFakeCacheStorage();
        await cacheStorage.open('prks-shell-v0');
        await cacheStorage.open('prks-static-v0');
        await sw.performInstall(cacheStorage, function (path) {
            return Promise.resolve(new Response('body:' + path, { status: 200 }));
        });
        await sw.performActivate(cacheStorage);
        assertEq('performActivate retires an old-versioned shell cache', cacheStorage._stores.has('prks-shell-v0'), false);
        assertEq('performActivate retires an old-versioned static cache', cacheStorage._stores.has('prks-static-v0'), false);
    }

    /* ---- SW rollback regression: a failed new-worker install must never
     * partially rewrite the cache an already-active old worker is serving
     * from (AGENTS.md "Make failed SW installs unable to poison the active
     * shell cache") ---- */
    {
        const cacheStorage = makeFakeCacheStorage();

        // Seed "old active" shell/static caches with identifiable old
        // contents, as if an earlier worker's successful install+activate
        // already ran.
        const shellLive = await cacheStorage.open(sw.PRKS_SW_SHELL_CACHE);
        const staticLive = await cacheStorage.open(sw.PRKS_SW_STATIC_CACHE);
        for (const p of sw.PRKS_SW_SHELL_PRECACHE_PATHS) {
            await shellLive.put(p, new Response('OLD:' + p, { status: 200 }));
        }
        for (const p of sw.PRKS_SW_STATIC_PRECACHE_PATHS) {
            await staticLive.put(p, new Response('OLD:' + p, { status: 200 }));
        }
        const snapshotBefore = {};
        for (const p of sw.PRKS_SW_SHELL_PRECACHE_PATHS.concat(sw.PRKS_SW_STATIC_PRECACHE_PATHS)) {
            const cache = sw.PRKS_SW_SHELL_PRECACHE_PATHS.indexOf(p) !== -1 ? shellLive : staticLive;
            snapshotBefore[p] = await (await cache.match(p)).text();
        }

        // Attempt installing a new revision: most required files succeed,
        // one required file (/js/app.js) fails.
        await assertRejectsOrThrows('rollback: new-worker install rejects on one failing required file', async function () {
            await sw.performInstall(cacheStorage, function (path) {
                if (path === '/js/app.js') return Promise.reject(new Error('network down'));
                return Promise.resolve(new Response('NEW:' + path, { status: 200 }));
            });
        });

        // Old cache entries must be byte-for-byte unchanged.
        let oldUnchanged = true;
        for (const p of sw.PRKS_SW_SHELL_PRECACHE_PATHS) {
            const got = await (await shellLive.match(p)).text();
            if (got !== snapshotBefore[p]) oldUnchanged = false;
        }
        for (const p of sw.PRKS_SW_STATIC_PRECACHE_PATHS) {
            const got = await (await staticLive.match(p)).text();
            if (got !== snapshotBefore[p]) oldUnchanged = false;
        }
        assert('rollback: old live cache entries are byte-for-byte unchanged after a failed install', oldUnchanged);

        // Partial new entries exist only in the staging cache, never live.
        const staticStagingAfterFailure = await cacheStorage.open(sw.PRKS_SW_STATIC_STAGING_CACHE);
        const someStagedEntry = await staticStagingAfterFailure.match('/css/style.css');
        assert('rollback: a successfully-fetched required path still lands in the staging cache', !!someStagedEntry);
        const liveStyleAfterFailure = await staticLive.match('/css/style.css');
        assertEq(
            'rollback: the live cache keeps its old content for a path the failed install also touched',
            liveStyleAfterFailure ? await liveStyleAfterFailure.text() : null,
            'OLD:/css/style.css'
        );

        // Now retry with a fully successful install + activate: the live
        // cache becomes complete with the new content, and staging is
        // cleaned up.
        await sw.performInstall(cacheStorage, function (path) {
            return Promise.resolve(new Response('NEW:' + path, { status: 200 }));
        });
        await sw.performActivate(cacheStorage);
        let liveComplete = true;
        for (const p of sw.PRKS_SW_SHELL_PRECACHE_PATHS.concat(sw.PRKS_SW_STATIC_PRECACHE_PATHS)) {
            const cache = sw.PRKS_SW_SHELL_PRECACHE_PATHS.indexOf(p) !== -1 ? shellLive : staticLive;
            const res = await cache.match(p);
            if (!res || (await res.text()) !== 'NEW:' + p) liveComplete = false;
        }
        assert('rollback: a subsequent successful install+activate makes the live cache complete with new content', liveComplete);
        assertEq(
            'rollback: staging caches are retired after the successful activate',
            cacheStorage._stores.has(sw.PRKS_SW_STATIC_STAGING_CACHE),
            false
        );
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
