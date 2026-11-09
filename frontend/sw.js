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

    // DEPENDENCY_REVISION is derived from frontend/vendor/DEPENDENCY-MANIFEST.json
    // (sha256 prefix). scripts/dependency_gate.py --write-manifest keeps it in sync;
    // --repo fails if it drifts. Changing manifest bytes (vendor files, the
    // committed Vue bundle, frontend/js/workspace-model.js,
    // frontend/js/work-route-projection.js,
    // frontend/js/search-query-codec.js,
    // frontend/js/tab-leave.js,
    // frontend/js/owner-resource.js,
    // frontend/js/editor-recovery.js, or
    // frontend/js/route-model.js) retires old
    // shell/static caches.
    const DEPENDENCY_REVISION = 'abcdc36522a4';
    const SHELL_CACHE = 'prks-shell-' + DEPENDENCY_REVISION;
    const STATIC_CACHE = 'prks-static-' + DEPENDENCY_REVISION;
    // Managed PDFs are cached per signed-in scope and Library epoch,
    // `prks-pdf-v3:<scope key>:<epoch>` (frontend/js/browser-scope.js). Each
    // page tells the worker which scope and epoch it is bound to
    // (PRKS_SCOPE_BIND_MESSAGE); the worker keeps that per client in the
    // scope database, so a page is only ever served its own scope's cache of
    // its own Library, and a retired page none. A request from a client that
    // never bound (a dedicated worker, a page still loading) gets the last
    // confirmed scope and epoch only when every live page bound to a scope
    // agrees with both; otherwise, and with no record at all, the worker
    // neither reads nor writes any PDF cache. A page bound to a newer epoch
    // drops its scope's caches of earlier epochs: they hold the files of the
    // Library a restore replaced (docs/postgresql-architecture.md §10.4).
    // The caches of earlier builds (unscoped v1, per-scope v2 without an
    // epoch) are retired, never read.
    const PDF_CACHE_BASE = 'prks-pdf-v3';
    const RETIRED_PDF_CACHE = 'prks-pdf-v1';
    const RETIRED_PDF_CACHE_PREFIX = 'prks-pdf-v2';
    const SCOPE_POINTER_DB = 'prks-browser-scope-v1';
    const SCOPE_POINTER_DB_VERSION = 2;
    const SCOPE_POINTER_STORE = 'pointer';
    const SCOPE_CLIENTS_STORE = 'clients';
    const SCOPE_POINTER_KEY = 'current';
    const SCOPE_BIND_MESSAGE = 'prks-scope-bind';
    const SESSION_PATH = '/api/session';
    // How long a page's PDF request waits for that page's own binding.
    const BIND_WAIT_MS = 3000;
    const SCOPE_ID_PATTERN = /^[A-Za-z0-9_-]{1,128}$/;
    // The server's grammar for a Library epoch: 1 to the largest bigint.
    const EPOCH_PATTERN = /^[1-9][0-9]{0,18}$/;
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
    const CURRENT_CACHES = [SHELL_CACHE, STATIC_CACHE];
    const RETIRE_PREFIXES = ['prks-shell-', 'prks-static-'];

    // Navigation-fallback entry points only. handleNavigation() falls back to
    // these from SHELL_CACHE; everything else the shell needs (CSS/JS/font)
    // is precached into STATIC_CACHE below so handleStatic()'s own
    // STATIC_CACHE fallback actually finds it -- precaching into the wrong
    // cache bucket would silently defeat offline launch.
    const SHELL_PRECACHE_PATHS = ['/', '/index.html'];

    // Explicit static-shell asset manifest: every same-origin CSS/JS/font file
    // the ordinary PRKS shell needs to boot and render the offline read cache
    // plus durable local-first UI, precached eagerly on install so the shell
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
        '/js/route-model.js',
        '/js/navigation.js',
        '/js/workspace-hosts.js',
        '/js/workspace-model.js',
        '/js/workspace-tree.js',
        '/js/workspace-persistence.js',
        '/js/workspace-tabs.js',
        '/js/owner-resource.js',
        '/js/tab-context.js',
        '/js/workspace-tiling.js',
        '/js/workspace-split.js',
        '/js/workspace-tab-menu.js',
        '/js/workspace-drag.js',
        '/js/workspace-overview.js',
        '/js/tab-leave.js',
        '/js/pdf-work-runtime.js',
        '/js/request-coordinator.js',
        '/js/browser-scope.js',
        '/vendor/idb/idb.min.js',
        '/js/offline-store.js',
        '/js/offline-runtime.js',
        '/js/local-store.js',
        '/js/editor-recovery.js',
        '/js/work-tag-state.js',
        '/js/folder-tag-state.js',
        '/js/work-lifecycle-state.js',
        '/js/tag-vocabulary-state.js',
        '/js/folder-state.js',
        '/js/playlist-state.js',
        '/js/concept-state.js',
        '/js/position-state.js',
        '/js/argument-state.js',
        '/js/work-open-state.js',
        '/js/work-metadata-state.js',
        '/js/work-source-state.js',
        '/js/work-notes-state.js',
        '/js/work-note-recovery.js',
        '/js/work-role-state.js',
    '/js/work-role-editor.js',
    '/js/person-state.js',
    '/js/person-metadata-state.js',
    '/js/person-group-state.js',
        '/js/work-metadata-editor.js',
    '/js/work-source-editor.js',
        '/js/pdf-annotation-state.js',
        '/js/pdf-annotation-reconcile.js',
        '/js/sync-diagnostics.js',
        '/js/sync-runtime.js',
        '/js/work-tag-editor.js',
        '/js/folder-tag-editor.js',
        '/js/api.js',
        '/js/doc-types.js',
        '/js/overview-primitives.js',
        '/js/combobox-keys.js',
        '/js/ui.js',
        '/js/components/work-cards.js',
        '/js/folder-hierarchy-nav.js',
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
        '/js/pdf-cache-install.js',
        '/js/components/playlists.js',
        '/js/components/people.js',
        '/js/components/people-groups.js',
        '/js/components/tags.js',
        '/js/components/types.js',
        '/js/components/processing-files.js',
        '/js/work-selection.js',
        '/js/search-query-codec.js',
        '/js/saved-views.js',
        '/js/command-palette.js',
        '/js/work-route-projection.js',
        '/js/app.js',
        // Vue bootstrap module from the committed Vite build. Required because
        // index.html loads it; not a legacy route module.
        '/vue/prks-vue.js',
    ];

    const STATIC_PATH_PREFIXES = ['/js/', '/vendor/', '/css/', '/icons/', '/vue/'];
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

    // Per-cache, per-path generation for whole-file writes into a scoped PDF
    // cache. The page and the network GET do not share a heap, so every
    // whole-file put goes through this worker: a post-save install advances
    // the generation before its put, and a network GET that snapshotted an
    // older generation skips its put. A chain per cache and path keeps those
    // puts from interleaving. Each account has its own cache, so one
    // account's install never cancels another account's write at the same
    // path.
    const PDF_CACHE_INSTALL_MESSAGE = 'prks-pdf-cache-install';
    const pdfWholeFileGeneration = Object.create(null);
    const pdfWholeFileWriteChain = Object.create(null);

    function wholeFilePdfKey(cacheName, pathname) {
        return String(cacheName || '') + '\n' + String(pathname || '');
    }

    function wholeFilePdfGeneration(pathname, cacheName) {
        const n = pdfWholeFileGeneration[wholeFilePdfKey(cacheName, pathname)];
        return Number.isSafeInteger(n) && n >= 0 ? n : 0;
    }

    function advanceWholeFilePdfGeneration(pathname, cacheName) {
        const next = wholeFilePdfGeneration(pathname, cacheName) + 1;
        pdfWholeFileGeneration[wholeFilePdfKey(cacheName, pathname)] = next;
        return next;
    }

    function enqueueWholeFilePdfWrite(pathname, cacheName, task) {
        const key = wholeFilePdfKey(cacheName, pathname);
        const prev = pdfWholeFileWriteChain[key] || Promise.resolve();
        const run = prev.then(task, task);
        pdfWholeFileWriteChain[key] = run.then(function () {
            return undefined;
        }, function () {
            return undefined;
        });
        return run;
    }

    function settleWholeFilePdfWrites(pathname, cacheName) {
        return pdfWholeFileWriteChain[wholeFilePdfKey(cacheName, pathname)] || Promise.resolve();
    }

    function copyPdfBytes(buffer) {
        if (!buffer || typeof buffer.byteLength !== 'number' || buffer.byteLength < 1) {
            return null;
        }
        if (typeof ArrayBuffer !== 'undefined' && buffer instanceof ArrayBuffer) {
            return buffer.slice(0);
        }
        if (buffer.buffer && typeof buffer.byteOffset === 'number') {
            return buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
        }
        return null;
    }

    function wholeFilePdfResponse(bytes) {
        return new Response(bytes, {
            status: 200,
            headers: {
                'Content-Type': 'application/pdf',
                'Content-Length': String(bytes.byteLength || 0),
            },
        });
    }

    /** Post-save bytes. Generation advances before the put, on the path's chain. */
    function installAuthoritativeWholeFilePdf(cache, pathname, bytes, cacheName) {
        const path = String(pathname || '');
        const body = copyPdfBytes(bytes);
        if (!cache || !isManagedPdfPath(path) || !body) return Promise.resolve(false);
        return enqueueWholeFilePdfWrite(path, cacheName, function () {
            advanceWholeFilePdfGeneration(path, cacheName);
            return cache.put(path, wholeFilePdfResponse(body)).then(function () {
                return true;
            });
        });
    }

    /**
     * Network whole-file GET. `generationAtStart` is the value observed before
     * the fetch. A newer post-save install skips this put.
     */
    function commitNetworkWholeFilePdf(cache, pathname, response, generationAtStart, cacheName) {
        const path = String(pathname || '');
        const snapshot = generationAtStart;
        return enqueueWholeFilePdfWrite(path, cacheName, function () {
            if (wholeFilePdfGeneration(path, cacheName) !== snapshot) return false;
            return cache.put(path, response).then(function () {
                return true;
            });
        });
    }

    /**
     * Page → worker install. Returns null for any other message so the
     * listener can ignore it. Resolves false when the bytes were not stored.
     *
     * The bytes come from a save the page made over the network, under
     * whichever session the browser holds now. They enter the sender's cache
     * only when `confirmScope` shows that session is still the cache's own
     * scope. Without that check (another account signed in, the session
     * expired, or the server cannot be reached) nothing is stored. A refused
     * install still advances the path's generation, so a network GET that
     * started before the save cannot put older bytes back afterwards.
     */
    function handlePdfCacheMessage(data, cachesApi, pdfCacheName, clientId, confirmScope) {
        if (!data || data.type !== PDF_CACHE_INSTALL_MESSAGE) return null;
        if (!cachesApi || typeof cachesApi.open !== 'function') return Promise.resolve(false);
        const pathname = typeof data.pathname === 'string' ? data.pathname : '';
        if (!isManagedPdfPath(pathname)) return Promise.resolve(false);
        const bytes = copyPdfBytes(data.buffer);
        if (!bytes) return Promise.resolve(false);
        const wanted = typeof data.cacheName === 'string' ? data.cacheName : '';
        return Promise.resolve(pdfCacheName ? pdfCacheName(clientId || '', true) : null).then(function (name) {
            // Only into the sending page's own scope cache, and only the one it named.
            if (!name || name !== wanted) return false;
            const confirm = typeof confirmScope === 'function'
                ? Promise.resolve(confirmScope(name)).catch(function () { return false; })
                : Promise.resolve(false);
            return confirm.then(function (confirmed) {
                if (confirmed !== true) {
                    return enqueueWholeFilePdfWrite(pathname, name, function () {
                        advanceWholeFilePdfGeneration(pathname, name);
                        return false;
                    });
                }
                return cachesApi.open(name).then(function (cache) {
                    return installAuthoritativeWholeFilePdf(cache, pathname, bytes, name);
                });
            });
        }).then(function (ok) {
            return !!ok;
        }, function () {
            return false;
        });
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
        if (name === RETIRED_PDF_CACHE) return true;
        if (name === RETIRED_PDF_CACHE_PREFIX || name.indexOf(RETIRED_PDF_CACHE_PREFIX + ':') === 0) return true;
        if (CURRENT_CACHES.indexOf(name) !== -1) return false;
        return RETIRE_PREFIXES.some(function (prefix) {
            return name.indexOf(prefix) === 0;
        });
    }

    function validScopeKey(key) {
        if (typeof key !== 'string') return false;
        const ids = key.split('.');
        return ids.length === 3 && ids.every(function (id) { return SCOPE_ID_PATTERN.test(id); });
    }

    function epochOf(value) {
        if (typeof value === 'number' && Number.isSafeInteger(value) && value > 0) value = String(value);
        return typeof value === 'string' && EPOCH_PATTERN.test(value) ? value : null;
    }

    /** Orders two canonical epochs (they may exceed a double's integers). */
    function compareEpochs(a, b) {
        if (a.length !== b.length) return a.length < b.length ? -1 : 1;
        return a < b ? -1 : a > b ? 1 : 0;
    }

    /** `prks-pdf-v3:<scope key>:<epoch>`, or null unless both are valid. */
    function pdfCacheNameOf(key, epoch) {
        return validScopeKey(key) && epochOf(epoch) ? PDF_CACHE_BASE + ':' + key + ':' + epoch : null;
    }

    /** The PDF caches of `key` under an epoch older than `epoch`, among `names`. */
    function olderEpochPdfCaches(names, key, epoch) {
        const current = epochOf(epoch);
        if (!validScopeKey(key) || !current) return [];
        const prefix = PDF_CACHE_BASE + ':' + key + ':';
        return (names || []).filter(function (name) {
            if (typeof name !== 'string' || name.indexOf(prefix) !== 0) return false;
            const other = epochOf(name.slice(prefix.length));
            return !!other && compareEpochs(other, current) < 0;
        });
    }

    /** Opens the scope database (the page's version), or resolves null. */
    function openScopeDb(idbFactory) {
        return new Promise(function (resolve) {
            if (!idbFactory || typeof idbFactory.open !== 'function') {
                resolve(null);
                return;
            }
            let req;
            try {
                req = idbFactory.open(SCOPE_POINTER_DB, SCOPE_POINTER_DB_VERSION);
            } catch (_e) {
                resolve(null);
                return;
            }
            req.onupgradeneeded = function () {
                const db = req.result;
                if (!db.objectStoreNames.contains(SCOPE_POINTER_STORE)) {
                    db.createObjectStore(SCOPE_POINTER_STORE, { keyPath: 'key' });
                }
                if (!db.objectStoreNames.contains(SCOPE_CLIENTS_STORE)) {
                    db.createObjectStore(SCOPE_CLIENTS_STORE, { keyPath: 'clientId' });
                }
            };
            req.onerror = req.onblocked = function () { resolve(null); };
            req.onsuccess = function () { resolve(req.result); };
        });
    }

    /**
     * The last confirmed scope and epoch, and every client binding:
     * { pointerKey, pointerEpoch, bindings: Map<clientId, { key, epoch, retired }> }.
     * Resolves, never rejects: any failure reads as nothing recorded.
     */
    function readScopeState(idbFactory) {
        const empty = { pointerKey: null, pointerEpoch: null, bindings: new Map() };
        return openScopeDb(idbFactory).then(function (db) {
            if (!db) return empty;
            return new Promise(function (resolve) {
                let tx;
                try {
                    tx = db.transaction([SCOPE_POINTER_STORE, SCOPE_CLIENTS_STORE], 'readonly');
                } catch (_e) {
                    db.close();
                    resolve(empty);
                    return;
                }
                const state = { pointerKey: null, pointerEpoch: null, bindings: new Map() };
                const get = tx.objectStore(SCOPE_POINTER_STORE).get(SCOPE_POINTER_KEY);
                get.onsuccess = function () {
                    const scope = get.result && get.result.scope;
                    const key = scope ? [scope.instanceId, scope.libraryId, scope.userId].join('.') : null;
                    state.pointerKey = validScopeKey(key) ? key : null;
                    state.pointerEpoch = get.result ? epochOf(get.result.epoch) : null;
                };
                const all = tx.objectStore(SCOPE_CLIENTS_STORE).getAll();
                all.onsuccess = function () {
                    (all.result || []).forEach(function (row) {
                        if (!row || typeof row.clientId !== 'string') return;
                        state.bindings.set(row.clientId, {
                            key: validScopeKey(row.key) ? row.key : null,
                            epoch: epochOf(row.epoch),
                            retired: row.retired === true,
                        });
                    });
                };
                tx.oncomplete = function () { db.close(); resolve(state); };
                tx.onabort = tx.onerror = function () { db.close(); resolve(empty); };
            });
        });
    }

    /**
     * Records which scope and epoch `clientId` is bound to (null key: none)
     * and drops the bindings of clients that are gone. Resolves true when
     * written.
     */
    function writeClientBinding(idbFactory, clientId, key, epoch, retired, liveClientIds) {
        if (typeof clientId !== 'string' || !clientId) return Promise.resolve(false);
        return openScopeDb(idbFactory).then(function (db) {
            if (!db) return false;
            return new Promise(function (resolve) {
                let tx;
                try {
                    tx = db.transaction(SCOPE_CLIENTS_STORE, 'readwrite');
                } catch (_e) {
                    db.close();
                    resolve(false);
                    return;
                }
                const store = tx.objectStore(SCOPE_CLIENTS_STORE);
                const bound = validScopeKey(key) && !retired;
                store.put({
                    clientId: clientId,
                    key: bound ? key : null,
                    epoch: bound ? epochOf(epoch) : null,
                    retired: retired === true,
                });
                if (liveClientIds) {
                    const rows = store.getAll();
                    rows.onsuccess = function () {
                        (rows.result || []).forEach(function (row) {
                            const id = row && row.clientId;
                            if (typeof id === 'string' && id !== clientId && !liveClientIds.has(id)) store.delete(id);
                        });
                    };
                }
                tx.oncomplete = function () { db.close(); resolve(true); };
                tx.onabort = tx.onerror = function () { db.close(); resolve(false); };
            });
        });
    }

    /**
     * The PDF cache a request from `clientId` may use, or null.
     * - A bound client: its own scope's cache of its own epoch; a retired,
     *   unscoped or epoch-less one none.
     * - `strict` (installs): only a bound client's own cache.
     * - Any other client: the last confirmed scope and epoch, only when every
     *   live page that has bound agrees with both (none retired, none on
     *   another scope or epoch).
     */
    function selectPdfCacheName(state, clientId, liveWindowIds, strict) {
        const own = clientId ? state.bindings.get(clientId) : null;
        if (own) return own.key && !own.retired ? pdfCacheNameOf(own.key, own.epoch) : null;
        const confirmed = pdfCacheNameOf(state.pointerKey, state.pointerEpoch);
        if (strict || !confirmed || !clientId) return null;
        const live = liveWindowIds || new Set();
        // An unbound page has no identity of its own: never another one's cache.
        if (live.has(clientId)) return null;
        // Any other client (a dedicated worker) borrows the last confirmed
        // scope only when every live page is bound to it.
        let agreed = live.size > 0;
        live.forEach(function (id) {
            const binding = state.bindings.get(id);
            if (!binding || binding.retired || pdfCacheNameOf(binding.key, binding.epoch) !== confirmed) agreed = false;
        });
        return agreed ? confirmed : null;
    }

    /** The scope key a GET /api/session body names, or null (the page's own check, browser-scope.js). */
    function sessionScopeKey(body) {
        if (!body || typeof body !== 'object') return null;
        const key = [body.instance_id, body.library && body.library.id, body.user && body.user.id].join('.');
        return validScopeKey(key) ? key : null;
    }

    /** The PDF cache a GET /api/session body is the scope and Library epoch of, or null. */
    function sessionPdfCacheName(body) {
        return pdfCacheNameOf(sessionScopeKey(body), body && body.library && body.library.epoch);
    }

    /**
     * Handlers take an explicit env ({ caches, fetchImpl }) rather than closing
     * over ambient self/caches so they can run against fakes in Node tests.
     */
    function createHandlers(env) {
        const cachesApi = env.caches;
        const fetchImpl = env.fetchImpl;
        const pdfCacheName = env.pdfCacheName || function () { return Promise.resolve(null); };
        // Whether the server's session is still the scope of `cacheName`. A
        // network PDF is written into a cache only when it is, before and
        // after the fetch: a tab whose account changed under it must not
        // store the new account's bytes under the old scope.
        const confirmScope = env.confirmScope || function () { return Promise.resolve(true); };

        async function handleNavigation(request) {
            try {
                const res = await fetchImpl(request);
                // Only an actual HTML shell response can refresh the offline shell.
                // Navigation to API or non-shell paths must not poison '/'.
                const path = pathnameOf(request.url, env.origin || 'http://localhost/');
                const type = res && res.headers && typeof res.headers.get === 'function'
                    ? (res.headers.get('Content-Type') || '') : '';
                if ((path === '/' || path === '/index.html') &&
                    /^text\/html(?:\s*;|$)/i.test(type) && isCacheableStaticResponse(res)) {
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

        async function handlePdfRequest(request, pathname, clientId) {
            const cacheName = await pdfCacheName(clientId || '', false);
            // Snapshot this cache's generation before the fetch starts, so an
            // install into the same cache that lands while this GET is still
            // draining is visible at put time. Installs into another
            // account's cache do not touch it.
            const generationAtStart = wholeFilePdfGeneration(pathname, cacheName);
            if (!cacheName) {
                // No signed-in scope: the network only, no cache read or write.
                try {
                    return await fetchImpl(request);
                } catch (_e) {
                    return new Response(null, { status: 503, statusText: 'PDF is not available offline.' });
                }
            }
            const cache = await cachesApi.open(cacheName);
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
                const confirmedBefore = Promise.resolve(confirmScope(cacheName)).catch(function () { return false; });
                const res = await fetchImpl(request);
                if (isWholeFilePdfResponse(request, res)) {
                    let cloned = null;
                    try {
                        cloned = res.clone();
                    } catch (_eClone) {
                        cloned = null;
                    }
                    if (cloned) {
                        Promise.all([confirmedBefore, confirmScope(cacheName)]).then(function (checks) {
                            if (!checks[0] || !checks[1]) return false;
                            return commitNetworkWholeFilePdf(cache, pathname, cloned, generationAtStart, cacheName);
                        }).catch(function () {});
                    }
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

    /** Deletes the PDF caches of `key` under epochs older than `epoch`. Never rejects. */
    function dropOlderEpochPdfCaches(cachesApi, key, epoch) {
        if (!cachesApi || typeof cachesApi.keys !== 'function') return Promise.resolve();
        return cachesApi.keys().then(function (names) {
            return Promise.all(olderEpochPdfCaches(names, key, epoch).map(function (name) {
                return cachesApi.delete(name);
            }));
        }).then(function () {}, function () {});
    }

    function attachServiceWorkerListeners(scope) {
        if (!scope || typeof scope.addEventListener !== 'function') return;

        const origin = (scope.location && scope.location.origin) || '';
        function liveClientIds(type) {
            if (!scope.clients || typeof scope.clients.matchAll !== 'function') return Promise.resolve(new Set());
            return scope.clients.matchAll({ type: type, includeUncontrolled: true }).then(function (list) {
                return new Set((list || []).map(function (c) { return c.id; }));
            }, function () {
                return new Set();
            });
        }
        // A page that is still binding: its request waits briefly for its own
        // binding instead of borrowing any other scope.
        const bindWaiters = new Map();
        function waitForBinding(clientId) {
            return new Promise(function (resolve) {
                let timer = null;
                function settle() {
                    clearTimeout(timer);
                    const left = (bindWaiters.get(clientId) || []).filter(function (fn) { return fn !== settle; });
                    if (left.length) bindWaiters.set(clientId, left);
                    else bindWaiters.delete(clientId);
                    resolve();
                }
                bindWaiters.set(clientId, (bindWaiters.get(clientId) || []).concat([settle]));
                timer = setTimeout(settle, BIND_WAIT_MS);
            });
        }
        function bindingWritten(clientId) {
            (bindWaiters.get(clientId) || []).slice().forEach(function (settle) { settle(); });
        }
        function selectNow(clientId, strict) {
            return Promise.all([readScopeState(scope.indexedDB), liveClientIds('window')]).then(function (parts) {
                return { state: parts[0], live: parts[1], name: selectPdfCacheName(parts[0], clientId, parts[1], strict) };
            });
        }
        const pdfCacheName = function (clientId, strict) {
            return selectNow(clientId, strict).then(function (first) {
                const unboundPage = !!clientId && first.live.has(clientId) && !first.state.bindings.has(clientId);
                if (!unboundPage) return first.name;
                return waitForBinding(clientId).then(function () {
                    return selectNow(clientId, strict).then(function (again) { return again.name; });
                });
            });
        };
        const confirmScope = function (cacheName) {
            if (!cacheName) return Promise.resolve(false);
            return scope.fetch(SESSION_PATH, {
                method: 'GET', credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' },
            }).then(function (response) {
                if (!response || response.status !== 200) return false;
                return response.json().then(function (body) {
                    // The same account AND the same Library epoch: after a
                    // restore, a page of the earlier Library stores nothing.
                    const name = sessionPdfCacheName(body);
                    return !!name && name === cacheName;
                });
            }).catch(function () {
                return false;
            });
        };
        const handlers = createHandlers({
            caches: scope.caches,
            fetchImpl: function (request) {
                return scope.fetch(request);
            },
            pdfCacheName: pdfCacheName,
            confirmScope: confirmScope,
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
                event.respondWith(handlers.handlePdfRequest(request, pathname, event.clientId || ''));
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

        scope.addEventListener('message', function (event) {
            const sourceId = event.source && typeof event.source.id === 'string' ? event.source.id : '';
            const data = event.data;
            if (data && data.type === SCOPE_BIND_MESSAGE) {
                const retired = data.retired === true;
                const bind = liveClientIds('all').then(function (live) {
                    return writeClientBinding(scope.indexedDB, sourceId, data.key, data.epoch, retired, live);
                }).then(function (written) {
                    bindingWritten(sourceId);
                    // A page of a newer Library epoch: its scope's caches of
                    // earlier epochs hold the replaced Library's files.
                    if (!retired && scope.caches) return dropOlderEpochPdfCaches(scope.caches, data.key, data.epoch).then(function () { return written; });
                    return written;
                });
                if (typeof event.waitUntil === 'function') event.waitUntil(bind);
                return;
            }
            if (!scope.caches) return;
            const done = handlePdfCacheMessage(data, scope.caches, pdfCacheName, sourceId, confirmScope);
            if (!done) return;
            const port = event.ports && event.ports[0];
            const task = Promise.resolve(done).then(function (ok) {
                if (port && typeof port.postMessage === 'function') {
                    port.postMessage({ ok: !!ok });
                }
            }, function () {
                if (port && typeof port.postMessage === 'function') {
                    port.postMessage({ ok: false });
                }
            });
            if (typeof event.waitUntil === 'function') event.waitUntil(task);
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
        installAuthoritativeWholeFilePdf: installAuthoritativeWholeFilePdf,
        commitNetworkWholeFilePdf: commitNetworkWholeFilePdf,
        settleWholeFilePdfWrites: settleWholeFilePdfWrites,
        handlePdfCacheMessage: handlePdfCacheMessage,
        wholeFilePdfGeneration: wholeFilePdfGeneration,
        PDF_CACHE_INSTALL_MESSAGE: PDF_CACHE_INSTALL_MESSAGE,
        PRKS_SW_SHELL_CACHE: SHELL_CACHE,
        PRKS_SW_STATIC_CACHE: STATIC_CACHE,
        PRKS_SW_PDF_CACHE_BASE: PDF_CACHE_BASE,
        PRKS_SW_RETIRED_PDF_CACHE: RETIRED_PDF_CACHE,
        PRKS_SW_SCOPE_POINTER_DB: SCOPE_POINTER_DB,
        PRKS_SW_SCOPE_POINTER_STORE: SCOPE_POINTER_STORE,
        PRKS_SW_SCOPE_POINTER_KEY: SCOPE_POINTER_KEY,
        readScopeState: readScopeState,
        writeClientBinding: writeClientBinding,
        selectPdfCacheName: selectPdfCacheName,
        sessionScopeKey: sessionScopeKey,
        sessionPdfCacheName: sessionPdfCacheName,
        pdfCacheNameOf: pdfCacheNameOf,
        olderEpochPdfCaches: olderEpochPdfCaches,
        dropOlderEpochPdfCaches: dropOlderEpochPdfCaches,
        PRKS_SW_SCOPE_BIND_MESSAGE: SCOPE_BIND_MESSAGE,
        PRKS_SW_SCOPE_CLIENTS_STORE: SCOPE_CLIENTS_STORE,
        PRKS_SW_SCOPE_POINTER_DB_VERSION: SCOPE_POINTER_DB_VERSION,
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
