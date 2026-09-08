/**
 * Client request coordinator for ordinary same-origin PRKS /api traffic.
 * Does not monkeypatch fetch. Persistent caching/offline sync is out of scope.
 * Best-effort reachability signalling (dynamic lookups of
 * prksOfflineNoteRequestSuccess / prksOfflineNoteRequestFailure)
 * is transport-health only: a resolved Response means PRKS answered; a
 * non-abort transport rejection after retries are exhausted means it did not.
 * Managed PDF GETs (/api/pdfs/...) are excluded: the service worker may
 * resolve those from Cache Storage without the PRKS server answering.
 */
(function (root) {
    'use strict';

    const PRKS_REQUEST_MAX_READS = 4;
    const PRKS_REQUEST_MAX_BACKGROUND_READS = 1;
    const PRKS_REQUEST_BURST_FRESH_MS = 1500;
    const PRKS_REQUEST_CACHE_MAX_BYTES = 2 * 1024 * 1024;
    const PRKS_REQUEST_CACHE_MAX_ENTRIES = 32;
    const PRKS_REQUEST_RETRY_MAX = 2;
    const PRKS_REQUEST_RETRY_DELAYS_MS = [250, 750];
    const PRKS_REQUEST_RETRY_STATUSES = { 502: true, 503: true, 504: true };

    const DEDUPE_HEADER_NAMES = [
        'accept',
        'authorization',
        'cache-control',
        'content-type',
        'if-modified-since',
        'if-none-match',
        'pragma',
    ];

    function makeAbortError() {
        if (typeof DOMException === 'function') {
            try {
                return new DOMException('The operation was aborted.', 'AbortError');
            } catch (_e) {
                /* fall through */
            }
        }
        const err = new Error('The operation was aborted.');
        err.name = 'AbortError';
        return err;
    }

    function prksIsAbortError(error) {
        if (!error) return false;
        if (error.name === 'AbortError') return true;
        if (typeof DOMException === 'function' && error instanceof DOMException) {
            return error.name === 'AbortError' || error.code === 20;
        }
        return false;
    }

    function reportPrksReachable() {
        if (typeof root.prksOfflineNoteRequestSuccess === 'function') {
            root.prksOfflineNoteRequestSuccess();
        }
    }

    function reportPrksUnreachable(error) {
        if (prksIsAbortError(error)) return;
        if (typeof root.prksOfflineNoteRequestFailure === 'function') {
            root.prksOfflineNoteRequestFailure();
        }
    }

    function defaultNow() {
        if (typeof Date !== 'undefined' && typeof Date.now === 'function') return Date.now();
        return 0;
    }

    function defaultRandom() {
        return Math.random();
    }

    function defaultSleep(ms, signal) {
        return new Promise(function (resolve, reject) {
            if (signal && signal.aborted) {
                reject(makeAbortError());
                return;
            }
            var timer = setTimeout(function () {
                if (signal && typeof signal.removeEventListener === 'function' && onAbort) {
                    signal.removeEventListener('abort', onAbort);
                }
                resolve();
            }, ms);
            function onAbort() {
                clearTimeout(timer);
                if (signal && typeof signal.removeEventListener === 'function') {
                    signal.removeEventListener('abort', onAbort);
                }
                reject(makeAbortError());
            }
            if (signal && typeof signal.addEventListener === 'function') {
                signal.addEventListener('abort', onAbort);
            }
        });
    }

    function defaultFetch(url, init) {
        return fetch(url, init);
    }

    function defaultOrigin() {
        if (typeof location !== 'undefined' && location && location.origin) return location.origin;
        return 'http://127.0.0.1';
    }

    function methodOf(fetchOptions) {
        const raw = fetchOptions && fetchOptions.method != null ? String(fetchOptions.method) : 'GET';
        return raw.toUpperCase() || 'GET';
    }

    function isPersonProfileImagePath(pathname) {
        return /^\/api\/persons\/[^/]+\/profile-image$/.test(pathname);
    }

    function isWorkThumbnailPath(pathname) {
        return /^\/api\/works\/[^/]+\/thumbnail$/.test(pathname);
    }

    function isManagedPdfHref(href) {
        const path = String(href || '').split('?')[0];
        return /^\/api\/pdfs\/[^/]+$/.test(path);
    }

    function isProcessingFilesRescan(pathname, searchParams) {
        return pathname === '/api/processing-files' && searchParams && searchParams.get('rescan') === '1';
    }

    function classifyRequest(method, parsed) {
        const pathname = parsed.pathname;
        if (isPersonProfileImagePath(pathname) || isWorkThumbnailPath(pathname)) return 'mutation';
        if ((method === 'GET' || method === 'HEAD') && isProcessingFilesRescan(pathname, parsed.searchParams)) {
            return 'mutation';
        }
        if (method === 'GET' || method === 'HEAD') return 'read';
        if (method === 'POST' || method === 'PATCH' || method === 'PUT' || method === 'DELETE') return 'mutation';
        return 'mutation';
    }

    function headerMap(headers) {
        const out = Object.create(null);
        if (!headers) return out;
        if (typeof Headers === 'function' && headers instanceof Headers) {
            headers.forEach(function (value, name) {
                out[String(name).toLowerCase()] = String(value);
            });
            return out;
        }
        if (Array.isArray(headers)) {
            headers.forEach(function (pair) {
                if (!pair || pair.length < 2) return;
                out[String(pair[0]).toLowerCase()] = String(pair[1]);
            });
            return out;
        }
        if (typeof headers === 'object') {
            Object.keys(headers).forEach(function (name) {
                const value = headers[name];
                if (value == null) return;
                out[String(name).toLowerCase()] = String(value);
            });
        }
        return out;
    }

    function hasRangeHeader(headers) {
        const map = headerMap(headers);
        return Object.prototype.hasOwnProperty.call(map, 'range') && map.range !== '';
    }

    function hasRequestBody(fetchOptions) {
        if (!fetchOptions) return false;
        const body = fetchOptions.body;
        return body != null && body !== '';
    }

    function resolveApiUrl(url, origin) {
        if (url == null) {
            throw new TypeError('prksRequest requires a same-origin /api URL.');
        }
        const raw = String(url);
        let parsed;
        try {
            parsed = new URL(raw, origin);
        } catch (_e) {
            throw new TypeError('prksRequest requires a same-origin /api URL.');
        }
        if (parsed.origin !== origin) {
            throw new TypeError('prksRequest only accepts same-origin /api URLs.');
        }
        if (parsed.pathname !== '/api' && parsed.pathname.indexOf('/api/') !== 0) {
            throw new TypeError('prksRequest only accepts same-origin /api URLs.');
        }
        return parsed;
    }

    function identityPath(parsed) {
        return parsed.pathname + parsed.search;
    }

    function relevantHeaderKey(headers) {
        const map = headerMap(headers);
        const parts = [];
        DEDUPE_HEADER_NAMES.forEach(function (name) {
            if (Object.prototype.hasOwnProperty.call(map, name) && map[name] !== '') {
                parts.push(name + ':' + map[name]);
            }
        });
        return parts.join('\n');
    }

    function isJsonContentType(headers) {
        if (!headers || typeof headers.get !== 'function') return false;
        const raw = headers.get('Content-Type') || headers.get('content-type') || '';
        const type = String(raw).split(';')[0].trim().toLowerCase();
        return type === 'application/json';
    }

    function contentLengthBytes(headers) {
        if (!headers || typeof headers.get !== 'function') return null;
        const raw = headers.get('Content-Length') || headers.get('content-length');
        if (raw == null || raw === '') return null;
        const n = Number(raw);
        if (!Number.isFinite(n) || n < 0) return null;
        return n;
    }

    function jitteredDelay(baseMs, random) {
        const span = baseMs * 0.25;
        const delta = (random() * 2 - 1) * span;
        const ms = Math.round(baseMs + delta);
        return ms < 0 ? 0 : ms;
    }

    function isRetryableNetworkError(error) {
        if (!error) return false;
        if (prksIsAbortError(error)) return false;
        return true;
    }

    function emptyCounts() {
        return {
            started: 0,
            completed: 0,
            failed: 0,
            aborted: 0,
            retries: 0,
            dedupeJoins: 0,
            burstCacheHits: 0,
            coalescedMutations: 0,
        };
    }

    function createPrksRequestCoordinator(deps) {
        const options = deps && typeof deps === 'object' ? deps : {};
        const fetchImpl = options.fetchImpl || defaultFetch;
        const now = options.now || defaultNow;
        const sleep = options.sleep || defaultSleep;
        const random = options.random || defaultRandom;
        const origin = options.origin || defaultOrigin();

        let mutationEpoch = 0;
        const inFlightReads = Object.create(null);
        const cacheEntries = [];
        const fgQueue = [];
        const bgQueue = [];
        const mutationQueue = [];
        let activeFgReads = 0;
        let activeBgReads = 0;
        let activeMutation = 0;

        let measuredFrom = now();
        let counts = emptyCounts();
        const peaks = { activeReads: 0, queuedReads: 0, queuedMutations: 0 };
        const waitSums = { readMs: 0, readN: 0, readMaxMs: 0, mutationMs: 0, mutationN: 0, mutationMaxMs: 0 };

        function recordReadWait(ms) {
            const n = ms < 0 ? 0 : ms;
            waitSums.readMs += n;
            waitSums.readN += 1;
            if (n > waitSums.readMaxMs) waitSums.readMaxMs = n;
        }

        function recordMutationWait(ms) {
            const n = ms < 0 ? 0 : ms;
            waitSums.mutationMs += n;
            waitSums.mutationN += 1;
            if (n > waitSums.mutationMaxMs) waitSums.mutationMaxMs = n;
        }

        function queuedReads() {
            return fgQueue.length + bgQueue.length;
        }

        function touchPeaks() {
            const activeReads = activeFgReads + activeBgReads;
            if (activeReads > peaks.activeReads) peaks.activeReads = activeReads;
            const qr = queuedReads();
            if (qr > peaks.queuedReads) peaks.queuedReads = qr;
            if (mutationQueue.length > peaks.queuedMutations) peaks.queuedMutations = mutationQueue.length;
        }

        function bumpEpochAndClearCache() {
            mutationEpoch += 1;
            cacheEntries.length = 0;
        }

        function evictCache(nowMs) {
            let i = 0;
            while (i < cacheEntries.length) {
                if (cacheEntries[i].expiresAt <= nowMs) cacheEntries.splice(i, 1);
                else i += 1;
            }
            while (cacheEntries.length > PRKS_REQUEST_CACHE_MAX_ENTRIES) {
                cacheEntries.shift();
            }
        }

        function cacheGet(key, nowMs) {
            evictCache(nowMs);
            for (let i = 0; i < cacheEntries.length; i++) {
                const entry = cacheEntries[i];
                if (entry.key === key && entry.expiresAt > nowMs) return entry.response;
            }
            return null;
        }

        function cacheSet(key, response, freshForMs, nowMs) {
            if (!(freshForMs > 0)) return;
            evictCache(nowMs);
            let clone;
            try {
                clone = response.clone();
            } catch (_e) {
                return;
            }
            for (let i = cacheEntries.length - 1; i >= 0; i--) {
                if (cacheEntries[i].key === key) cacheEntries.splice(i, 1);
            }
            cacheEntries.push({
                key: key,
                response: clone,
                storedAt: nowMs,
                expiresAt: nowMs + freshForMs,
            });
            evictCache(nowMs);
        }

        function readDedupeKey(method, parsed, fetchOptions) {
            const credentials = fetchOptions && fetchOptions.credentials != null ? String(fetchOptions.credentials) : '';
            const cacheMode = fetchOptions && fetchOptions.cache != null ? String(fetchOptions.cache) : '';
            return [
                method,
                identityPath(parsed),
                relevantHeaderKey(fetchOptions && fetchOptions.headers),
                credentials,
                cacheMode,
                String(mutationEpoch),
            ].join('\n');
        }

        function canDedupe(method, fetchOptions, policy, mode) {
            if (mode !== 'read') return false;
            if (policy && policy.dedupe === false) return false;
            if (method !== 'GET' && method !== 'HEAD') return false;
            if (hasRequestBody(fetchOptions)) return false;
            if (hasRangeHeader(fetchOptions && fetchOptions.headers)) return false;
            return true;
        }

        function canRetry(method, policy, mode) {
            if (mode !== 'read') return false;
            if (policy && policy.retry === false) return false;
            if (method !== 'GET' && method !== 'HEAD') return false;
            return true;
        }

        function detachSubscriber(sub) {
            if (!sub || !sub.signal || !sub.onAbort) return;
            if (typeof sub.signal.removeEventListener === 'function') {
                sub.signal.removeEventListener('abort', sub.onAbort);
            }
            sub.onAbort = null;
        }

        function rejectAborted(sub) {
            counts.aborted += 1;
            detachSubscriber(sub);
            sub.reject(makeAbortError());
        }

        function deliverClone(sub, response) {
            detachSubscriber(sub);
            try {
                sub.resolve(response.clone());
                counts.completed += 1;
            } catch (err) {
                counts.failed += 1;
                sub.reject(err);
            }
        }

        function deliverError(sub, error) {
            detachSubscriber(sub);
            if (prksIsAbortError(error)) {
                counts.aborted += 1;
                sub.reject(error);
                return;
            }
            counts.failed += 1;
            sub.reject(error);
        }

        function removeQueuedRead(flight) {
            function drop(queue) {
                for (let i = queue.length - 1; i >= 0; i--) {
                    if (queue[i].flight === flight) queue.splice(i, 1);
                }
            }
            drop(fgQueue);
            drop(bgQueue);
        }

        function abortUnderlyingIfOrphaned(flight) {
            if (!flight || flight.settled) return;
            if (flight.subscribers.length > 0) return;
            flight.cancelled = true;
            try {
                flight.controller.abort();
            } catch (_e) {
                /* ignore */
            }
            removeQueuedRead(flight);
            if (flight.dedupeKey && inFlightReads[flight.dedupeKey] === flight) {
                delete inFlightReads[flight.dedupeKey];
            }
        }

        function addReadSubscriber(flight, signal) {
            return new Promise(function (resolve, reject) {
                const sub = { resolve: resolve, reject: reject, signal: signal || null, onAbort: null };
                if (signal && signal.aborted) {
                    rejectAborted(sub);
                    abortUnderlyingIfOrphaned(flight);
                    return;
                }
                if (flight.settled) {
                    if (flight.error) deliverError(sub, flight.error);
                    else deliverClone(sub, flight.response);
                    return;
                }
                if (signal) {
                    sub.onAbort = function () {
                        const idx = flight.subscribers.indexOf(sub);
                        if (idx >= 0) flight.subscribers.splice(idx, 1);
                        rejectAborted(sub);
                        abortUnderlyingIfOrphaned(flight);
                    };
                    signal.addEventListener('abort', sub.onAbort);
                }
                flight.subscribers.push(sub);
            });
        }

        function settleFlightSuccess(flight, response) {
            if (flight.settled) return;
            flight.settled = true;
            flight.response = response;
            const subs = flight.subscribers.splice(0, flight.subscribers.length);
            subs.forEach(function (sub) {
                if (sub.signal && sub.signal.aborted) rejectAborted(sub);
                else deliverClone(sub, response);
            });
        }

        function settleFlightError(flight, error) {
            if (flight.settled) return;
            flight.settled = true;
            flight.error = error;
            const subs = flight.subscribers.splice(0, flight.subscribers.length);
            subs.forEach(function (sub) {
                if (sub.signal && sub.signal.aborted) rejectAborted(sub);
                else deliverError(sub, error);
            });
        }

        function networkInit(fetchOptions, signal) {
            const init = {};
            if (fetchOptions && typeof fetchOptions === 'object') {
                Object.keys(fetchOptions).forEach(function (key) {
                    if (key === 'signal') return;
                    init[key] = fetchOptions[key];
                });
            }
            init.signal = signal;
            return init;
        }

        function shouldRetryStatus(status) {
            return !!PRKS_REQUEST_RETRY_STATUSES[status];
        }

        async function performReadNetwork(href, fetchOptions, retryEnabled, controller) {
            const signal = controller.signal;
            let attempt = 0;
            let lastError = null;
            while (attempt < 1 + (retryEnabled ? PRKS_REQUEST_RETRY_MAX : 0)) {
                if (signal.aborted) throw makeAbortError();
                try {
                    const response = await fetchImpl(href, networkInit(fetchOptions, signal));
                    if (!isManagedPdfHref(href)) reportPrksReachable();
                    if (retryEnabled && shouldRetryStatus(response.status) && attempt < PRKS_REQUEST_RETRY_MAX) {
                        counts.retries += 1;
                        attempt += 1;
                        const delay = jitteredDelay(PRKS_REQUEST_RETRY_DELAYS_MS[attempt - 1] || 750, random);
                        await sleep(delay, signal);
                        continue;
                    }
                    return response;
                } catch (err) {
                    if (prksIsAbortError(err) || signal.aborted) throw makeAbortError();
                    lastError = err;
                    if (retryEnabled && isRetryableNetworkError(err) && attempt < PRKS_REQUEST_RETRY_MAX) {
                        counts.retries += 1;
                        attempt += 1;
                        const delay = jitteredDelay(PRKS_REQUEST_RETRY_DELAYS_MS[attempt - 1] || 750, random);
                        await sleep(delay, signal);
                        continue;
                    }
                    if (!isManagedPdfHref(href)) reportPrksUnreachable(err);
                    throw err;
                }
            }
            if (lastError) {
                if (!isManagedPdfHref(href)) reportPrksUnreachable(lastError);
                throw lastError;
            }
            throw new Error('prksRequest retry exhausted.');
        }

        function maybeCacheResponse(key, response, freshForMs, nowMs) {
            if (!(freshForMs > 0) || !response || !response.ok) return;
            if (!isJsonContentType(response.headers)) return;
            const size = contentLengthBytes(response.headers);
            if (size == null || size < 0 || size > PRKS_REQUEST_CACHE_MAX_BYTES) return;
            cacheSet(key, response, freshForMs, nowMs);
        }

        function pumpReads() {
            while (activeFgReads + activeBgReads < PRKS_REQUEST_MAX_READS && fgQueue.length) {
                const job = fgQueue.shift();
                runReadJob(job, false);
            }
            while (
                bgQueue.length &&
                activeBgReads < PRKS_REQUEST_MAX_BACKGROUND_READS &&
                activeFgReads + activeBgReads < PRKS_REQUEST_MAX_READS &&
                fgQueue.length === 0
            ) {
                const job = bgQueue.shift();
                runReadJob(job, true);
            }
            touchPeaks();
        }

        function pumpMutations() {
            if (activeMutation !== 0) return;
            if (!mutationQueue.length) return;
            const job = mutationQueue.shift();
            runMutationJob(job);
            touchPeaks();
        }

        function pump() {
            pumpMutations();
            pumpReads();
        }

        async function runReadJob(job, isBackground) {
            if (isBackground) activeBgReads += 1;
            else activeFgReads += 1;
            touchPeaks();
            recordReadWait(now() - job.enqueuedAt);
            const flight = job.flight;
            try {
                if (flight.cancelled || flight.controller.signal.aborted || flight.subscribers.length === 0) {
                    settleFlightError(flight, makeAbortError());
                    return;
                }
                const response = await performReadNetwork(
                    job.href,
                    job.fetchOptions,
                    job.retryEnabled,
                    flight.controller
                );
                maybeCacheResponse(job.cacheKey, response, job.freshForMs, now());
                settleFlightSuccess(flight, response);
            } catch (err) {
                settleFlightError(flight, prksIsAbortError(err) ? makeAbortError() : err);
            } finally {
                if (job.dedupeKey && inFlightReads[job.dedupeKey] === flight) {
                    delete inFlightReads[job.dedupeKey];
                }
                if (isBackground) activeBgReads -= 1;
                else activeFgReads -= 1;
                pump();
            }
        }

        function enqueueRead(job, isBackground) {
            job.enqueuedAt = now();
            if (isBackground) bgQueue.push(job);
            else fgQueue.push(job);
            touchPeaks();
            pump();
        }

        async function runMutationJob(job) {
            activeMutation = 1;
            touchPeaks();
            recordMutationWait(now() - job.enqueuedAt);
            bumpEpochAndClearCache();
            let response = null;
            let error = null;
            let networkFailed = false;
            try {
                response = await fetchImpl(job.href, networkInit(job.fetchOptions, undefined));
                if (!isManagedPdfHref(job.href)) reportPrksReachable();
            } catch (err) {
                error = err;
                networkFailed = !prksIsAbortError(err);
                if (!isManagedPdfHref(job.href)) reportPrksUnreachable(err);
            }
            if (response && response.ok) {
                bumpEpochAndClearCache();
            } else if (networkFailed) {
                bumpEpochAndClearCache();
            }
            const waiters = job.waiters.splice(0, job.waiters.length);
            waiters.forEach(function (waiter) {
                if (error) {
                    counts.failed += 1;
                    waiter.reject(error);
                } else {
                    try {
                        waiter.resolve(response.clone());
                        counts.completed += 1;
                    } catch (cloneErr) {
                        counts.failed += 1;
                        waiter.reject(cloneErr);
                    }
                }
            });
            activeMutation = 0;
            pump();
        }

        function enqueueMutation(job) {
            if (job.coalesceKey) {
                for (let i = 0; i < mutationQueue.length; i++) {
                    const queued = mutationQueue[i];
                    if (queued.coalesceKey === job.coalesceKey) {
                        queued.href = job.href;
                        queued.fetchOptions = job.fetchOptions;
                        queued.waiters.push.apply(queued.waiters, job.waiters);
                        counts.coalescedMutations += 1;
                        touchPeaks();
                        return;
                    }
                }
            }
            job.enqueuedAt = now();
            mutationQueue.push(job);
            touchPeaks();
            pump();
        }

        function prksRequest(url, fetchOptions, policy) {
            const opts = fetchOptions && typeof fetchOptions === 'object' ? fetchOptions : {};
            const pol = policy && typeof policy === 'object' ? policy : {};
            const parsed = resolveApiUrl(url, origin);
            const method = methodOf(opts);
            const mode = classifyRequest(method, parsed);
            const href = parsed.pathname + parsed.search + parsed.hash;
            const callerSignal = opts.signal || null;
            const priority = pol.priority === 'background' ? 'background' : 'foreground';
            const freshForMs = Number(pol.freshForMs);
            const ttl = Number.isFinite(freshForMs) && freshForMs > 0 ? freshForMs : 0;
            const coalesceKey = mode === 'mutation' && typeof pol.coalesceKey === 'string' && pol.coalesceKey
                ? pol.coalesceKey
                : '';

            counts.started += 1;

            if (callerSignal && callerSignal.aborted) {
                counts.aborted += 1;
                return Promise.reject(makeAbortError());
            }

            if (mode === 'mutation') {
                return new Promise(function (resolve, reject) {
                    enqueueMutation({
                        href: href,
                        fetchOptions: opts,
                        coalesceKey: coalesceKey,
                        waiters: [{ resolve: resolve, reject: reject }],
                    });
                });
            }

            const dedupeEnabled = canDedupe(method, opts, pol, mode);
            const retryEnabled = canRetry(method, pol, mode);
            const cacheKey = readDedupeKey(method, parsed, opts);

            if (ttl > 0 && dedupeEnabled) {
                const cached = cacheGet(cacheKey, now());
                if (cached) {
                    counts.burstCacheHits += 1;
                    counts.completed += 1;
                    try {
                        return Promise.resolve(cached.clone());
                    } catch (err) {
                        counts.failed += 1;
                        return Promise.reject(err);
                    }
                }
            }

            if (dedupeEnabled && inFlightReads[cacheKey]) {
                counts.dedupeJoins += 1;
                return addReadSubscriber(inFlightReads[cacheKey], callerSignal);
            }

            const flight = {
                controller: new AbortController(),
                subscribers: [],
                settled: false,
                response: null,
                error: null,
                cancelled: false,
                dedupeKey: dedupeEnabled ? cacheKey : '',
            };
            if (dedupeEnabled) inFlightReads[cacheKey] = flight;
            const consumer = addReadSubscriber(flight, callerSignal);
            enqueueRead(
                {
                    href: href,
                    fetchOptions: opts,
                    retryEnabled: retryEnabled,
                    freshForMs: ttl,
                    cacheKey: cacheKey,
                    dedupeKey: dedupeEnabled ? cacheKey : '',
                    flight: flight,
                },
                priority === 'background'
            );
            return consumer;
        }

        function snapshot() {
            const t = now();
            return {
                measuredForMs: Math.max(0, t - measuredFrom),
                counts: {
                    started: counts.started,
                    completed: counts.completed,
                    failed: counts.failed,
                    aborted: counts.aborted,
                    retries: counts.retries,
                    dedupeJoins: counts.dedupeJoins,
                    burstCacheHits: counts.burstCacheHits,
                    coalescedMutations: counts.coalescedMutations,
                },
                current: {
                    activeReads: activeFgReads + activeBgReads,
                    activeBackgroundReads: activeBgReads,
                    activeMutation: activeMutation ? 1 : 0,
                    queuedForegroundReads: fgQueue.length,
                    queuedBackgroundReads: bgQueue.length,
                    queuedMutations: mutationQueue.length,
                },
                peaks: {
                    activeReads: peaks.activeReads,
                    queuedReads: peaks.queuedReads,
                    queuedMutations: peaks.queuedMutations,
                },
                waits: {
                    readAverageMs: waitSums.readN ? waitSums.readMs / waitSums.readN : 0,
                    readMaxMs: waitSums.readMaxMs,
                    mutationAverageMs: waitSums.mutationN ? waitSums.mutationMs / waitSums.mutationN : 0,
                    mutationMaxMs: waitSums.mutationMaxMs,
                },
            };
        }

        function resetDiagnostics() {
            measuredFrom = now();
            counts = emptyCounts();
            peaks.activeReads = activeFgReads + activeBgReads;
            peaks.queuedReads = queuedReads();
            peaks.queuedMutations = mutationQueue.length;
            waitSums.readMs = 0;
            waitSums.readN = 0;
            waitSums.readMaxMs = 0;
            waitSums.mutationMs = 0;
            waitSums.mutationN = 0;
            waitSums.mutationMaxMs = 0;
        }

        return {
            prksRequest: prksRequest,
            snapshot: snapshot,
            resetDiagnostics: resetDiagnostics,
        };
    }

    const production = createPrksRequestCoordinator({
        fetchImpl: defaultFetch,
        now: defaultNow,
        sleep: defaultSleep,
        random: defaultRandom,
        origin: defaultOrigin(),
    });

    function prksRequest(url, fetchOptions, policy) {
        return production.prksRequest(url, fetchOptions, policy);
    }

    function prksRequestCoordinatorSnapshot() {
        return production.snapshot();
    }

    function prksResetRequestCoordinatorDiagnostics() {
        production.resetDiagnostics();
    }

    const api = {
        prksRequest: prksRequest,
        prksRequestCoordinatorSnapshot: prksRequestCoordinatorSnapshot,
        prksResetRequestCoordinatorDiagnostics: prksResetRequestCoordinatorDiagnostics,
        prksIsAbortError: prksIsAbortError,
        createPrksRequestCoordinator: createPrksRequestCoordinator,
        PRKS_REQUEST_MAX_READS: PRKS_REQUEST_MAX_READS,
        PRKS_REQUEST_MAX_BACKGROUND_READS: PRKS_REQUEST_MAX_BACKGROUND_READS,
        PRKS_REQUEST_BURST_FRESH_MS: PRKS_REQUEST_BURST_FRESH_MS,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
