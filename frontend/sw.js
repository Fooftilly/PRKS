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
    // Install-time-only staging buckets (AGENTS.md "Make failed SW installs
    // unable to poison the active shell cache"): a currently-active worker
    // may still be serving fetches out of SHELL_CACHE/STATIC_CACHE while a
    // new worker's install runs. performInstall() writes exclusively into
    // these distinct staging names -- never the live ones -- so a failed
    // install (even one that already wrote some, but not all, required
    // paths) can never leave the live shell/static cache the old worker is
    // reading from in a partially-overwritten state. Only a *successful*
    // install reaches activate, where performActivate() promotes staging
    // into the live cache names and deletes the staging buckets.
    const SHELL_STAGING_CACHE = SHELL_CACHE + '-staging';
    const STATIC_STAGING_CACHE = STATIC_CACHE + '-staging';
    const CURRENT_CACHES = [SHELL_CACHE, STATIC_CACHE, PDF_CACHE];
    const RETIRE_PREFIXES = ['prks-shell-', 'prks-static-'];

    // Navigation-fallback entry points only. handleNavigation() falls back to
    // these from SHELL_CACHE; everything else the shell needs (CSS/JS/font)
    // is precached into STATIC_CACHE below so handleStatic()'s own
    // STATIC_CACHE fallback actually finds it -- precaching into the wrong
    // cache bucket would silently defeat offline launch.
    const SHELL_PRECACHE_PATHS = ['/', '/index.html'];

    // Explicit static-shell asset manifest: every same-origin CSS/JS/font file
    // the ordinary PRKS shell needs to boot and render the full Phase-1
    // read-only offline surface, precached eagerly on install so the shell
    // launches offline without depending on a page having already loaded
    // once under an active, controlling service worker (see
    // tests/test_frontend_service_worker.py's ShellManifestMatchesIndexHtml
    // tests, which fail this file's own build if index.html gains a
    // <script src> / <link href> that is not listed here).
    //
    // Deliberately excluded: the PDF-viewer-specific bundle
    // (`/vendor/prks-pdf-viewer/*`, including pdfium.wasm) is not needed to
    // launch the ordinary shell -- `pdf-viewer-runtime.js` only `import()`s it
    // lazily the first time a PDF viewer is actually created. It stays
    // cache-on-first-PDF-use via the static-path rule below.
    // Decorative subset of STATIC_PRECACHE_PATHS (manifest/icons): the shell
    // boots and works fully offline without any of these, so they stay
    // best-effort during install and must never fail/block it. Every other
    // STATIC_PRECACHE_PATHS entry (below) is essential JS/CSS/font and is
    // treated as required -- see performInstall().
    const STATIC_PRECACHE_OPTIONAL_PATHS = [
        '/manifest.webmanifest',
        '/favicon.svg',
        '/logo.svg',
        '/icons/icon-192.png',
        '/icons/icon-512.png',
    ];

    const STATIC_PRECACHE_PATHS = [
        '/manifest.webmanifest',
        '/favicon.svg',
        '/logo.svg',
        '/icons/icon-192.png',
        '/icons/icon-512.png',
        // <link rel="stylesheet"> / font
        '/vendor/inter/inter.css',
        '/vendor/inter/InterVariable.woff2',
        '/css/style.css',
        '/vendor/easymde/easymde.min.css',
        '/vendor/codemirror/show-hint.css',
        // <script src> in index.html, in source order
        '/vendor/codemirror/codemirror.js',
        '/vendor/codemirror/show-hint.js',
        '/vendor/easymde/easymde.min.js',
        '/vendor/dompurify/purify.min.js',
        '/js/markdown-sanitize.js',
        '/vendor/lucide/lucide.min.js',
        '/js/icons.js',
        '/js/date-format.js',
        '/js/navigation.js',
        '/js/workspace-tree.js',
        '/js/workspace-persistence.js',
        '/js/workspace-tabs.js',
        '/js/tab-context.js',
        '/js/workspace-tiling.js',
        '/js/workspace-split.js',
        '/js/workspace-tab-menu.js',
        '/js/workspace-drag.js',
        '/js/pdf-work-runtime.js',
        '/js/request-coordinator.js',
        '/js/offline-store.js',
        '/js/offline-runtime.js',
        '/js/local-store.js',
        '/js/work-tag-state.js',
        '/js/work-open-state.js',
        '/js/work-metadata-state.js',
    '/js/work-source-state.js',
    '/js/work-role-state.js',
    '/js/work-role-editor.js',
    '/js/person-state.js',
        '/js/work-metadata-editor.js',
    '/js/work-source-editor.js',
        '/js/sync-diagnostics.js',
        '/js/sync-runtime.js',
        '/js/work-tag-editor.js',
        '/js/api.js',
        '/js/doc-types.js',
        '/js/ui.js',
        '/js/ribbon-create.js',
        '/js/components/work-cards.js',
        '/js/components/folders.js',
        '/js/research-links.js',
        '/js/components/works.js',
        '/js/components/concepts.js',
        '/js/components/positions.js',
        '/js/components/arguments.js',
        '/vendor/cytoscape/cytoscape.min.js',
        '/js/components/research-graph.js',
        '/js/components/works-pdf.js',
        // Statically import()ed by works-pdf.js -- needed to boot that module,
        // not the lazy heavy PDF-viewer bundle it in turn loads on demand.
        '/js/pdf-viewer-runtime.js',
        '/js/components/playlists.js',
        '/js/components/people.js',
        '/js/components/people-groups.js',
        '/js/components/search.js',
        '/js/components/publishers.js',
        '/js/components/tags.js',
        '/js/components/types.js',
        '/js/components/progress.js',
        '/js/components/processing-files.js',
        '/js/work-selection.js',
        '/js/saved-views.js',
        '/js/command-palette.js',
        '/js/app.js',
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

    // Precaches every path in `paths` and rejects the whole call if any one
    // fetch throws or resolves to a non-ok response -- the same
    // atomic-or-nothing contract as the native Cache.addAll(), but built on
    // an injectable fetchImpl so it is exercisable from a plain Node test
    // without a real ServiceWorkerGlobalScope/CacheStorage.
    function precacheRequiredPaths(cache, fetchImpl, paths) {
        return Promise.all(
            paths.map(function (path) {
                return Promise.resolve(fetchImpl(path)).then(function (response) {
                    if (!response || !response.ok) {
                        throw new Error('required shell precache fetch failed: ' + path);
                    }
                    return cache.put(path, response.clone ? response.clone() : response);
                });
            })
        );
    }

    // Best-effort precache for purely decorative assets (manifest/icons):
    // one missing/failing asset must never fail install.
    function precacheOptionalPaths(cache, fetchImpl, paths) {
        return Promise.all(
            paths.map(function (path) {
                return Promise.resolve(fetchImpl(path))
                    .then(function (response) {
                        if (!response || !response.ok) return undefined;
                        return cache.put(path, response.clone ? response.clone() : response);
                    })
                    .catch(function () {
                        /* Decorative asset: best-effort only, must never block install. */
                    });
            })
        );
    }

    // The full install-time precache. Every SHELL_PRECACHE_PATHS entry and
    // every non-optional STATIC_PRECACHE_PATHS entry (essential JS/CSS/font)
    // is required: if any one of those fetches fails, this Promise rejects
    // so the caller's `event.waitUntil()` fails the whole installation --
    // it is better to keep the previous, still-working service worker than
    // to activate a new one that claims offline support but is missing
    // app.js/style.css/etc. Only the small decorative STATIC_PRECACHE_OPTIONAL_PATHS
    // subset (manifest/icons) is best-effort.
    //
    // Writes go into SHELL_STAGING_CACHE / STATIC_STAGING_CACHE, never the
    // live SHELL_CACHE / STATIC_CACHE an already-active worker may be
    // concurrently reading from -- see performActivate() for the promotion
    // step that only runs once install has fully succeeded. Any staging
    // cache left over from a previous failed install is cleared first so a
    // stale entry never survives into a later successful install.
    function performInstall(cachesApi, fetchImpl) {
        if (!cachesApi || typeof fetchImpl !== 'function') return Promise.resolve();
        const requiredStaticPaths = STATIC_PRECACHE_PATHS.filter(function (path) {
            return STATIC_PRECACHE_OPTIONAL_PATHS.indexOf(path) === -1;
        });
        const optionalStaticPaths = STATIC_PRECACHE_PATHS.filter(function (path) {
            return STATIC_PRECACHE_OPTIONAL_PATHS.indexOf(path) !== -1;
        });
        return Promise.all([
            Promise.resolve(cachesApi.delete(SHELL_STAGING_CACHE))
                .catch(function () {})
                .then(function () {
                    return cachesApi.open(SHELL_STAGING_CACHE);
                })
                .then(function (cache) {
                    return precacheRequiredPaths(cache, fetchImpl, SHELL_PRECACHE_PATHS);
                }),
            Promise.resolve(cachesApi.delete(STATIC_STAGING_CACHE))
                .catch(function () {})
                .then(function () {
                    return cachesApi.open(STATIC_STAGING_CACHE);
                })
                .then(function (cache) {
                    return Promise.all([
                        precacheRequiredPaths(cache, fetchImpl, requiredStaticPaths),
                        precacheOptionalPaths(cache, fetchImpl, optionalStaticPaths),
                    ]);
                }),
        ]);
    }

    /** Copies every staging entry into the live cache, then deletes the staging cache. */
    function promoteStagingCache(cachesApi, stagingName, liveName) {
        return cachesApi.open(stagingName).then(function (staging) {
            return cachesApi.open(liveName).then(function (live) {
                return staging.keys().then(function (requests) {
                    return Promise.all(
                        requests.map(function (req) {
                            return staging.match(req).then(function (res) {
                                if (res) return live.put(req, res);
                                return undefined;
                            });
                        })
                    );
                });
            });
        }).then(function () {
            return cachesApi.delete(stagingName);
        });
    }

    // Runs once a new worker's install has fully succeeded (activate never
    // fires for a rejected install): promotes both staging caches into the
    // live SHELL_CACHE/STATIC_CACHE names, then retires any genuinely
    // older-versioned cache names still around (RETIRE_PREFIXES).
    function performActivate(cachesApi) {
        if (!cachesApi) return Promise.resolve();
        return Promise.all([
            promoteStagingCache(cachesApi, SHELL_STAGING_CACHE, SHELL_CACHE),
            promoteStagingCache(cachesApi, STATIC_STAGING_CACHE, STATIC_CACHE),
        ])
            .then(function () {
                return cachesApi.keys();
            })
            .then(function (names) {
                return Promise.all(
                    names.filter(shouldRetireCache).map(function (name) {
                        return cachesApi.delete(name);
                    })
                );
            });
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
                performInstall(scope.caches, function (path) {
                    return scope.fetch(path);
                })
            );
        });

        scope.addEventListener('activate', function (event) {
            if (typeof event.waitUntil !== 'function' || !scope.caches) {
                scope.clients && scope.clients.claim && scope.clients.claim();
                return;
            }
            event.waitUntil(
                performActivate(scope.caches).then(function () {
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
        performInstall: performInstall,
        performActivate: performActivate,
        promoteStagingCache: promoteStagingCache,
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
        PRKS_SW_SHELL_STAGING_CACHE: SHELL_STAGING_CACHE,
        PRKS_SW_STATIC_STAGING_CACHE: STATIC_STAGING_CACHE,
        PRKS_SW_SHELL_PRECACHE_PATHS: SHELL_PRECACHE_PATHS,
        PRKS_SW_STATIC_PRECACHE_PATHS: STATIC_PRECACHE_PATHS,
        PRKS_SW_STATIC_PRECACHE_OPTIONAL_PATHS: STATIC_PRECACHE_OPTIONAL_PATHS,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }

    attachServiceWorkerListeners(root);
})(typeof self !== 'undefined' ? self : typeof window !== 'undefined' ? window : globalThis);
