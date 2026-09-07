/**
 * PRKS app-shell / PDF service worker.
 *
 * Scope (see AGENTS.md "Offline / PWA" invariants):
 *   - App-shell + static-resource availability, plus a focused managed-PDF
 *     response cache. Nothing else.
 *   - Never queues API mutations. Never generically caches /api/... JSON --
 *     app code/IndexedDB own structured offline records (offline-store.js /
 *     offline-runtime.js).
 *   - Eligibility is deliberately narrow and lives in small pure functions
 *     so it can be exercised without a real ServiceWorkerGlobalScope
 *     (tests/browser/run_sw_selftest.js).
 */
(function (root) {
    'use strict';

    const SHELL_CACHE = 'prks-shell-v1';
    const STATIC_CACHE = 'prks-static-v1';
    const PDF_CACHE = 'prks-pdf-v1';
    const CURRENT_CACHES = [SHELL_CACHE, STATIC_CACHE, PDF_CACHE];
    const RETIRE_PREFIXES = ['prks-shell-', 'prks-static-'];

    // Minimal explicit shell entry points. Everything else needed to render the
    // SPA (core JS/CSS/vendor runtime, including the PDF viewer + pdfium.wasm)
    // is picked up by the static-path cache-as-you-go rule below the first time
    // it is actually requested by a page that loaded successfully -- a fixed,
    // hand-maintained manifest of every bundle file would go stale quietly.
    const SHELL_PRECACHE_PATHS = [
        '/',
        '/index.html',
        '/manifest.webmanifest',
        '/favicon.svg',
        '/logo.svg',
        '/icons/icon-192.png',
        '/icons/icon-512.png',
    ];

    const STATIC_PATH_PREFIXES = ['/js/', '/vendor/', '/css/', '/icons/'];
    const STATIC_EXTRA_PATHS = ['/manifest.webmanifest', '/favicon.svg', '/logo.svg'];

    function isGetRequest(request) {
        return !!(request && String(request.method || 'GET').toUpperCase() === 'GET');
    }

    function isSameOriginUrl(rawUrl, origin) {
        try {
            const u = new URL(rawUrl, origin);
            return u.origin === origin;
        } catch (_e) {
            return false;
        }
    }

    function pathnameOf(rawUrl, origin) {
        try {
            return new URL(rawUrl, origin).pathname;
        } catch (_e) {
            return '';
        }
    }

    function isNavigationRequest(request) {
        return !!(request && request.mode === 'navigate');
    }

    function isStaticEligiblePath(pathname) {
        if (STATIC_EXTRA_PATHS.indexOf(pathname) !== -1) return true;
        return STATIC_PATH_PREFIXES.some(function (prefix) {
            return pathname.indexOf(prefix) === 0;
        });
    }

    /** Canonical managed-PDF route only: /api/pdfs/<one filename segment>. */
    function isManagedPdfPath(pathname) {
        return /^\/api\/pdfs\/[^/]+$/.test(pathname);
    }

    function hasRangeHeader(request) {
        return !!(
            request &&
            request.headers &&
            typeof request.headers.get === 'function' &&
            request.headers.get('range')
        );
    }

    /** Parses a single "bytes=start-end" / "bytes=start-" / "bytes=-suffix" Range header value. */
    function parseRangeHeaderValue(value, totalSize) {
        if (!value || !(totalSize > 0)) return null;
        const m = /^bytes=(\d*)-(\d*)$/.exec(String(value).trim());
        if (!m || (m[1] === '' && m[2] === '')) return null;
        let start = m[1] === '' ? null : parseInt(m[1], 10);
        let end = m[2] === '' ? null : parseInt(m[2], 10);
        if (start == null) {
            // Suffix range: the last `end` bytes.
            start = Math.max(0, totalSize - end);
            end = totalSize - 1;
        } else if (end == null || end >= totalSize) {
            end = totalSize - 1;
        }
        if (!(start >= 0) || !(end >= start) || start >= totalSize) return null;
        return { start: start, end: end };
    }

    /** Never cache non-GET, opaque/cross-origin, redirected, or error/4xx/5xx responses. */
    function isCacheableStaticResponse(response) {
        if (!response) return false;
        if (response.status !== 200) return false;
        if (response.type === 'opaque' || response.type === 'error') return false;
        if (response.redirected) return false;
        return true;
    }

    /** Only a complete (non-Range) 200 PDF response may enter the whole-file PDF cache. */
    function isWholeFilePdfResponse(request, response) {
        if (!response || response.status !== 200) return false;
        if (hasRangeHeader(request)) return false;
        if (response.headers && typeof response.headers.get === 'function' && response.headers.get('content-range')) {
            return false;
        }
        return true;
    }

    function shouldRetireCache(name) {
        if (CURRENT_CACHES.indexOf(name) !== -1) return false;
        return RETIRE_PREFIXES.some(function (prefix) {
            return name.indexOf(prefix) === 0;
        });
    }

    /**
     * Handlers take an explicit env ({ caches, fetchImpl }) rather than closing
     * over ambient self/caches so they can run against fakes in Node tests.
     */
    function createHandlers(env) {
        const cachesApi = env.caches;
        const fetchImpl = env.fetchImpl;

        async function handleNavigation(request) {
            try {
                const res = await fetchImpl(request);
                if (isCacheableStaticResponse(res)) {
                    const cache = await cachesApi.open(SHELL_CACHE);
                    cache.put('/', res.clone()).catch(function () {});
                }
                return res;
            } catch (_e) {
                const cache = await cachesApi.open(SHELL_CACHE);
                const cached = (await cache.match('/')) || (await cache.match('/index.html'));
                if (cached) return cached;
                return new Response('PRKS is offline and no cached app shell is available yet.', {
                    status: 503,
                    headers: { 'Content-Type': 'text/plain' },
                });
            }
        }

        async function handleStatic(request) {
            const cache = await cachesApi.open(STATIC_CACHE);
            try {
                const res = await fetchImpl(request);
                if (isCacheableStaticResponse(res)) {
                    cache.put(request, res.clone()).catch(function () {});
                }
                return res;
            } catch (err) {
                const cached = await cache.match(request);
                if (cached) return cached;
                throw err;
            }
        }

        /** Synthesizes a 206 (or whole-file 200) response for a Range request from a cached whole PDF. */
        async function servePartialFromCached(cachedResponse, rangeHeaderValue) {
            const buf = await cachedResponse.clone().arrayBuffer();
            const total = buf.byteLength;
            const range = parseRangeHeaderValue(rangeHeaderValue, total);
            if (!range) {
                return new Response(buf, {
                    status: 200,
                    headers: { 'Content-Type': 'application/pdf', 'Content-Length': String(total), 'Accept-Ranges': 'bytes' },
                });
            }
            const slice = buf.slice(range.start, range.end + 1);
            return new Response(slice, {
                status: 206,
                headers: {
                    'Content-Type': 'application/pdf',
                    'Content-Range': 'bytes ' + range.start + '-' + range.end + '/' + total,
                    'Content-Length': String(slice.byteLength),
                    'Accept-Ranges': 'bytes',
                },
            });
        }

        async function handlePdfRequest(request, pathname) {
            const cache = await cachesApi.open(PDF_CACHE);
            if (hasRangeHeader(request)) {
                // Range requests never write to the whole-file cache entry -- only a complete
                // 200 response (below) may do that. Offline, a Range request is instead served
                // by slicing the whole-file cache entry, so the PDF viewer's progressive loader
                // keeps working without ever writing a partial file into the cache.
                try {
                    return await fetchImpl(request);
                } catch (_e) {
                    const cached = await cache.match(pathname);
                    if (cached) {
                        try {
                            return await servePartialFromCached(cached, request.headers.get('range'));
                        } catch (_e2) {
                            /* fall through to unavailable */
                        }
                    }
                    return new Response(null, { status: 503, statusText: 'PDF is not available offline.' });
                }
            }
            try {
                const res = await fetchImpl(request);
                if (isWholeFilePdfResponse(request, res)) {
                    cache.put(pathname, res.clone()).catch(function () {});
                }
                return res;
            } catch (_e) {
                const cached = await cache.match(pathname);
                if (cached) return cached;
                return new Response(null, { status: 503, statusText: 'PDF is not available offline.' });
            }
        }

        return {
            handleNavigation: handleNavigation,
            handleStatic: handleStatic,
            handlePdfRequest: handlePdfRequest,
        };
    }

    function attachServiceWorkerListeners(scope) {
        if (!scope || typeof scope.addEventListener !== 'function') return;

        const origin = (scope.location && scope.location.origin) || '';
        const handlers = createHandlers({
            caches: scope.caches,
            fetchImpl: function (request) {
                return scope.fetch(request);
            },
        });

        scope.addEventListener('install', function (event) {
            scope.skipWaiting();
            if (!scope.caches || typeof event.waitUntil !== 'function') return;
            event.waitUntil(
                scope.caches.open(SHELL_CACHE).then(function (cache) {
                    return Promise.all(
                        SHELL_PRECACHE_PATHS.map(function (path) {
                            return cache.add(path).catch(function () {
                                /* Best-effort precache: one missing asset must not block install. */
                            });
                        })
                    );
                })
            );
        });

        scope.addEventListener('activate', function (event) {
            if (typeof event.waitUntil !== 'function' || !scope.caches) {
                scope.clients && scope.clients.claim && scope.clients.claim();
                return;
            }
            event.waitUntil(
                scope.caches
                    .keys()
                    .then(function (names) {
                        return Promise.all(
                            names.filter(shouldRetireCache).map(function (name) {
                                return scope.caches.delete(name);
                            })
                        );
                    })
                    .then(function () {
                        return scope.clients && scope.clients.claim ? scope.clients.claim() : undefined;
                    })
            );
        });

        scope.addEventListener('fetch', function (event) {
            const request = event.request;
            if (!isGetRequest(request)) return;
            const url = request.url;
            if (!isSameOriginUrl(url, origin)) return;
            const pathname = pathnameOf(url, origin);

            if (isManagedPdfPath(pathname)) {
                event.respondWith(handlers.handlePdfRequest(request, pathname));
                return;
            }
            if (pathname === '/' || pathname === '/index.html' || isNavigationRequest(request)) {
                event.respondWith(handlers.handleNavigation(request));
                return;
            }
            if (isStaticEligiblePath(pathname)) {
                event.respondWith(handlers.handleStatic(request));
            }
            // Everything else -- notably /api/... JSON -- passes straight through.
        });
    }

    const api = {
        createHandlers: createHandlers,
        attachServiceWorkerListeners: attachServiceWorkerListeners,
        isGetRequest: isGetRequest,
        isSameOriginUrl: isSameOriginUrl,
        pathnameOf: pathnameOf,
        isNavigationRequest: isNavigationRequest,
        isStaticEligiblePath: isStaticEligiblePath,
        isManagedPdfPath: isManagedPdfPath,
        hasRangeHeader: hasRangeHeader,
        parseRangeHeaderValue: parseRangeHeaderValue,
        isCacheableStaticResponse: isCacheableStaticResponse,
        isWholeFilePdfResponse: isWholeFilePdfResponse,
        shouldRetireCache: shouldRetireCache,
        PRKS_SW_SHELL_CACHE: SHELL_CACHE,
        PRKS_SW_STATIC_CACHE: STATIC_CACHE,
        PRKS_SW_PDF_CACHE: PDF_CACHE,
        PRKS_SW_SHELL_PRECACHE_PATHS: SHELL_PRECACHE_PATHS,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }

    attachServiceWorkerListeners(root);
})(typeof self !== 'undefined' ? self : typeof window !== 'undefined' ? window : globalThis);
