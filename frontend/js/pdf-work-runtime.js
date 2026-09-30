/**
 * Per-tab PDF runtime. Viewer, page session, annotation cache, and
 * persistence queue live here — never as window globals.
 */
(function (root) {
    'use strict';

    function emptyAnnotationCache(workId) {
        return {
            allItems: [],
            rawItems: [],
            items: [],
            docId: null,
            workId: String(workId || ''),
        };
    }

    function emptySyncState(workId) {
        return {
            workId: String(workId || ''),
            pendingChanges: false,
            inFlight: false,
            lastError: '',
            lastSuccessAt: 0,
            lastConfirmedToken: '',
            activeToken: '',
            localMutationSeen: false,
        };
    }

    /**
     * `viewer`/`setupToken` are optional identity pins: when supplied, this
     * requires not merely that `runtime` is still the ctx's current PDF
     * resource, but that `runtime.viewer` is still the exact viewer instance
     * (and `runtime.viewerSetupToken` the exact setup generation) this call
     * was bound to. An async persistence setup started for one viewer must
     * never install/keep a worker bound to a runtime that has since moved on
     * to a different viewer instance.
     */
    function prksPdfPersistenceStillLive(ctx, generation, runtime, viewer, setupToken) {
        if (!runtime || runtime._destroyed) return false;
        if (ctx && ctx.destroyed) return false;
        if (ctx && typeof ctx.isCurrent === 'function' && typeof generation === 'number' && !ctx.isCurrent(generation)) {
            return false;
        }
        if (ctx && typeof ctx.getResource === 'function' && ctx.getResource('pdf') !== runtime) {
            return false;
        }
        if (viewer !== undefined && runtime.viewer !== viewer) return false;
        if (setupToken !== undefined && runtime.viewerSetupToken !== setupToken) return false;
        return true;
    }

    function prksInstallPdfAnnotationPersistenceIfCurrent(ctx, generation, runtime, viewer, setupToken, installer) {
        if (!prksPdfPersistenceStillLive(ctx, generation, runtime, viewer, setupToken)) return false;
        if (typeof installer === 'function') installer();
        return true;
    }

    /**
     * Setup-time eligibility for *installing a new* annotation-persistence
     * worker. Stricter than prksPdfPersistenceStillLive(): an async setup
     * that began while the runtime was online/'work' must re-verify, after
     * every await boundary and immediately before installing, that none of
     * that has changed underneath it. This deliberately does NOT belong on
     * prksPdfPersistenceStillLive() itself, because an *already-installed*
     * worker must keep passing that check (and simply stay paused) while
     * offline -- only a not-yet-installed setup needs to abandon outright.
     *
     * Durable startup is special: hydrate + bridge install run while mode is
     * still preview (`online_awaiting_base` / `*_awaiting_bridge`). Those
     * reasons must remain eligible online (or offline_awaiting_bridge with a
     * cached base) so setMutationEnabled(true) can happen only after the
     * bridge is ready — never earlier.
     */
    function prksPdfPersistenceSetupEligible(ctx, generation, runtime, viewer, setupToken) {
        if (!prksPdfPersistenceStillLive(ctx, generation, runtime, viewer, setupToken)) return false;
        const reason = runtime && runtime.annotationMutationReason
            ? String(runtime.annotationMutationReason)
            : '';
        const awaitingDurableStartup =
            reason === 'online_awaiting_base' ||
            reason === 'online_awaiting_bridge' ||
            reason === 'offline_awaiting_bridge';
        if (runtime.annotationMutationDurable === true || awaitingDurableStartup) {
            if (reason === 'online_awaiting_base' || reason === 'online_awaiting_bridge') {
                if (typeof root.prksOfflineRuntimeState === 'function' &&
                    root.prksOfflineRuntimeState() !== 'online') {
                    return false;
                }
            }
            return true;
        }
        if (runtime.mode !== 'work') return false;
        // Legacy full-list flush stays online-only.
        if (typeof root.prksOfflineRuntimeState === 'function' && root.prksOfflineRuntimeState() !== 'online') {
            return false;
        }
        return true;
    }

    function createPdfAnnotationPersistenceWorker(options) {
        const opts = options || {};
        const runtime = opts.runtime || null;
        const schedule = typeof opts.schedule === 'function' ? opts.schedule : setTimeout;
        const unschedule = typeof opts.unschedule === 'function' ? opts.unschedule : clearTimeout;
        const retryDelayMs = Number.isFinite(opts.retryDelayMs) ? opts.retryDelayMs : 2200;

        const worker = {
            destroyed: false,
            paused: false,
            retryTimer: null,
            flushPasses: 0,
            retriesFired: 0,
        };

        function isDead() {
            return !!(worker.destroyed || (runtime && runtime._destroyed));
        }

        worker.requestFlush = function (reason) {
            if (isDead()) return undefined;
            worker.flushPasses += 1;
            if (typeof opts.onFlush === 'function') return opts.onFlush(reason);
            return undefined;
        };

        /**
         * PRKS is offline/reconnecting: stop the network retry loop
         * immediately (no retry storm against an unreachable server) while
         * keeping every bit of state a resume needs -- pendingChanges, the
         * viewer/document, and any unsaved annotation edits already made
         * while online. This never destroys the worker or the viewer.
         */
        worker.pause = function () {
            if (isDead() || worker.paused) return;
            worker.paused = true;
            if (worker.retryTimer != null) {
                try {
                    unschedule(worker.retryTimer);
                } catch (_e) {}
                worker.retryTimer = null;
            }
            if (typeof opts.clearTimer === 'function') {
                try {
                    opts.clearTimer();
                } catch (_e2) {}
            }
        };

        /** Online again: resume normal retry/flush behavior; if a change was
         * left pending while paused, request exactly one flush. */
        worker.resume = function () {
            if (isDead() || !worker.paused) return;
            worker.paused = false;
            if (typeof opts.hasPendingChanges === 'function' && opts.hasPendingChanges()) {
                void worker.requestFlush('resume');
            }
        };

        worker.scheduleRetry = function () {
            if (isDead() || worker.paused) return;
            if (worker.retryTimer != null) return;
            const timerId = schedule(function () {
                worker.retryTimer = null;
                if (typeof opts.clearTimer === 'function') {
                    try {
                        opts.clearTimer();
                    } catch (_e) {}
                }
                if (isDead()) return;
                worker.retriesFired += 1;
                void worker.requestFlush('retry');
            }, retryDelayMs);
            worker.retryTimer = timerId;
            if (typeof opts.setTimer === 'function') {
                try {
                    opts.setTimer(timerId);
                } catch (_e2) {}
            }
        };

        worker.flush = function () {
            return worker.requestFlush('manual');
        };

        worker.destroy = function () {
            if (worker.destroyed) return;
            worker.destroyed = true;
            if (worker.retryTimer != null) {
                try {
                    unschedule(worker.retryTimer);
                } catch (_e) {}
                worker.retryTimer = null;
            }
            if (typeof opts.clearTimer === 'function') {
                try {
                    opts.clearTimer();
                } catch (_e3) {}
            }
            if (typeof opts.onDestroy === 'function') {
                try {
                    opts.onDestroy();
                } catch (_e4) {}
            }
        };

        return worker;
    }

    function emptyPdfSearchState() {
        return {
            open: false,
            query: '',
            total: 0,
            activeIndex: -1,
            status: 'idle',
            epoch: 0,
        };
    }

    function pdfSearchMatchLabel(search) {
        if (!search || !search.open) return '';
        if (search.status === 'pending') return 'Searching';
        if (search.status === 'empty') return 'No matches';
        if (search.status === 'ready' && search.total > 0 && search.activeIndex >= 0) {
            return String(search.activeIndex + 1) + ' of ' + String(search.total);
        }
        return '';
    }

    /**
     * Search may still paint only for this generation, this runtime, and this
     * viewer. Opening or closing search does not change that identity.
     */
    function prksPdfSearchStill(ctx, generation, runtime) {
        if (!runtime || runtime._destroyed) return false;
        return prksPdfPersistenceStillLive(
            ctx,
            generation,
            runtime,
            runtime.viewer,
            runtime.viewerSetupToken
        );
    }

    function isPdfFindShortcut(event) {
        if (!event || event.repeat) return false;
        const key = event.key;
        if (key !== 'f' && key !== 'F') return false;
        if (!(event.ctrlKey || event.metaKey)) return false;
        if (event.altKey || event.shiftKey) return false;
        return true;
    }

    function pdfSearchRole(node) {
        if (!node || typeof node.getAttribute !== 'function') return '';
        return String(node.getAttribute('data-prks-role') || '');
    }

    function isForeignPdfEditable(node) {
        let current = node;
        while (current) {
            const role = pdfSearchRole(current);
            if (role === 'pdf-search-query' || role === 'pdf-search') return false;
            const tag = current.tagName ? String(current.tagName).toLowerCase() : '';
            if (tag === 'input' || tag === 'textarea' || tag === 'select' || current.isContentEditable === true) {
                return true;
            }
            current = current.parentNode || null;
        }
        return false;
    }

    function pdfSurfaceOwner(node) {
        let current = node;
        while (current) {
            const data = current.dataset;
            if (data && data.prksOwnerTabId) {
                return {
                    tabId: String(data.prksOwnerTabId),
                    generation: data.prksOwnerGeneration == null ? '' : String(data.prksOwnerGeneration),
                };
            }
            current = current.parentNode || null;
        }
        return null;
    }

    /**
     * Ctrl/Cmd+F inside this PDF surface opens this runtime's search.
     * The listener does not create a viewer. A foreign editable, including an
     * annotation field, keeps the shortcut.
     */
    function bindPdfSurfaceSearch(ctx, runtime, surface, generation) {
        if (!runtime || runtime._destroyed || typeof runtime.openSearch !== 'function' || !surface) {
            return function () {};
        }
        runtime._searchGeneration = generation;
        if (surface.dataset) {
            surface.dataset.prksOwnerTabId = String(ctx && ctx.tabId != null ? ctx.tabId : '');
            surface.dataset.prksOwnerGeneration = String(generation);
        }
        if (typeof surface.addEventListener !== 'function') return function () {};
        function onKey(event) {
            if (!isPdfFindShortcut(event)) return;
            const target = event.target || surface;
            if (isForeignPdfEditable(target)) return;
            const owner = pdfSurfaceOwner(target) || pdfSurfaceOwner(surface);
            const tabId = String(ctx && ctx.tabId != null ? ctx.tabId : '');
            if (!owner || owner.tabId !== tabId || owner.generation !== String(generation)) return;
            if (!prksPdfSearchStill(ctx, generation, runtime)) return;
            if (typeof event.preventDefault === 'function') event.preventDefault();
            if (typeof event.stopPropagation === 'function') event.stopPropagation();
            runtime.openSearch();
            if (pdfSearchRole(target) === 'pdf-search-query' && typeof target.select === 'function') {
                try {
                    target.select();
                } catch (_e) {}
            }
        }
        surface.addEventListener('keydown', onKey, true);
        const unbind = function () {
            if (typeof surface.removeEventListener === 'function') {
                surface.removeEventListener('keydown', onKey, true);
            }
        };
        runtime._unbindSearch = unbind;
        return unbind;
    }

    function createWorkPdfRuntime(options) {
        const opts = options || {};
        const workId = String(opts.workId || '');
        const runtime = {
            viewer: opts.viewer || null,
            // Bumped every time `runtime.viewer` is (re)published. Lets an
            // in-flight async annotation-persistence setup started for a
            // prior viewer instance detect it has been superseded and must
            // not install itself. See prksPdfPersistenceStillLive().
            viewerSetupToken: 0,
            // 'work' | 'preview' -- the last mutation-capability mode this
            // runtime's single viewer instance was reconciled to. Distinct
            // from mutationEnabled-in-flight bookkeeping: this is the
            // settled value used to decide whether a reconcile is a no-op.
            mode: null,
            workId: workId,
            pageSession: opts.pageSession || {
                workId: workId,
                pageNumber: 1,
                totalPages: undefined,
            },
            lastPage: opts.lastPage || null,
            annotationCache: opts.annotationCache || emptyAnnotationCache(workId),
            annotationEditorState: opts.annotationEditorState || null,
            syncState: opts.syncState || emptySyncState(workId),
            _destroyed: false,
            _flushAnnotationsImpl: typeof opts.flushAnnotations === 'function' ? opts.flushAnnotations : null,
            search: emptyPdfSearchState(),
            _searchGeneration: null,
            _unbindSearch: null,
        };

        runtime.hasPendingSync = function () {
            const st = runtime.syncState;
            // Durable path: semantic intent already lives in prks-local-v1.
            // Materialization lag is not unload-blocking data loss. Only a
            // failed local durable write (provisional viewer state) blocks.
            if (runtime.annotationMutationDurable === true) {
                return !!(st && st.lastError === 'local_save_failed');
            }
            return !!(st && (st.pendingChanges || st.inFlight));
        };

        runtime.flushAnnotations = async function () {
            if (typeof runtime._flushAnnotationsImpl === 'function') {
                return runtime._flushAnnotationsImpl();
            }
            return undefined;
        };

        runtime.flushLastPage = function () {
            if (runtime.lastPage && typeof runtime.lastPage.debounceClear === 'function') {
                try {
                    runtime.lastPage.debounceClear();
                } catch (_e) {}
            }
            if (runtime.lastPage && typeof runtime.lastPage.persistNow === 'function') {
                try {
                    runtime.lastPage.persistNow();
                } catch (_e) {}
            }
        };

        runtime.getAnnotationHints = function () {
            const c = runtime.annotationCache;
            const items = c && Array.isArray(c.items) ? c.items : [];
            const out = [];
            for (let idx = 0; idx < items.length; idx++) {
                const item = items[idx];
                if (!item || typeof item !== 'object') continue;
                const id = item.id || item.uuid || item.annotationId || item._id;
                if (id == null || id === '') continue;
                const sid = String(id);
                const page = item.pageIndex ?? item.page ?? item.pageNumber ?? item.page_index;
                const pageDisp = page !== undefined && page !== null ? Number(page) + 1 : '?';
                let text = '';
                if (typeof root.annotationToText === 'function') {
                    try {
                        text = root.annotationToText(item) || '';
                    } catch (_e) {
                        text = '';
                    }
                }
                if (!text) text = item.contents || item.content || item.comment || 'Annotation ' + (idx + 1);
                const short = String(text).replace(/\s+/g, ' ').trim().slice(0, 48);
                out.push({
                    id: sid,
                    displayText: short + ' - p. ' + pageDisp,
                });
            }
            return out;
        };

        runtime.resize = function () {
            if (runtime._destroyed) return;
            if (runtime.viewer && typeof runtime.viewer.resize === 'function') {
                runtime.viewer.resize();
            }
        };

        function callViewerSearch(name, args) {
            const viewer = runtime.viewer;
            if (!viewer || runtime._destroyed) return undefined;
            const fn = viewer[name];
            if (typeof fn !== 'function') return undefined;
            return fn.apply(viewer, args || []);
        }

        runtime.readSearch = function () {
            const search = runtime.search || emptyPdfSearchState();
            return {
                open: !!search.open,
                query: search.query || '',
                total: search.total || 0,
                activeIndex: typeof search.activeIndex === 'number' ? search.activeIndex : -1,
                status: search.status || 'idle',
                matchCountLabel: pdfSearchMatchLabel(search),
            };
        };

        runtime.openSearch = function () {
            if (runtime._destroyed) return false;
            runtime.search.open = true;
            callViewerSearch('openSearch');
            return true;
        };

        runtime.closeSearch = function () {
            if (runtime._destroyed) return false;
            runtime.search.epoch += 1;
            runtime.search.open = false;
            runtime.search.query = '';
            runtime.search.total = 0;
            runtime.search.activeIndex = -1;
            runtime.search.status = 'idle';
            callViewerSearch('closeSearch');
            return true;
        };

        runtime.setSearchQuery = function (query) {
            if (runtime._destroyed || !runtime.search.open) return false;
            const next = query == null ? '' : String(query);
            runtime.search.epoch += 1;
            const epoch = runtime.search.epoch;
            runtime.search.query = next;
            if (!next.trim()) {
                runtime.search.total = 0;
                runtime.search.activeIndex = -1;
                runtime.search.status = 'idle';
                callViewerSearch('clearSearchMatches');
                return true;
            }
            runtime.search.total = 0;
            runtime.search.activeIndex = -1;
            runtime.search.status = 'pending';
            callViewerSearch('commitSearch', [next, epoch]);
            return true;
        };

        runtime.applySearchResult = function (result) {
            if (runtime._destroyed || !result || !runtime.search.open) return false;
            if (result.epoch !== runtime.search.epoch) return false;
            if (result.viewer != null && result.viewer !== runtime.viewer) return false;
            const generation = typeof result.ownerGeneration === 'number'
                ? result.ownerGeneration
                : runtime._searchGeneration;
            if (
                typeof runtime._searchGeneration === 'number' &&
                typeof generation === 'number' &&
                generation !== runtime._searchGeneration
            ) {
                return false;
            }
            if (result.ctx && !prksPdfSearchStill(result.ctx, generation, runtime)) return false;
            const total = Number(result.total);
            const count = Number.isFinite(total) && total > 0 ? Math.floor(total) : 0;
            runtime.search.total = count;
            if (count < 1) {
                runtime.search.activeIndex = -1;
                runtime.search.status = 'empty';
                return true;
            }
            const index = Number(result.activeIndex);
            const active = Number.isFinite(index) ? Math.floor(index) : 0;
            runtime.search.activeIndex = Math.min(count - 1, Math.max(0, active));
            runtime.search.status = 'ready';
            return true;
        };

        function stepSearch(direction) {
            if (runtime._destroyed || !runtime.search.open) return false;
            if (runtime.search.status !== 'ready' || runtime.search.total < 1) return false;
            const viewer = runtime.viewer;
            const method = direction < 0 ? 'searchPrevious' : 'searchNext';
            if (viewer && typeof viewer[method] === 'function') {
                const index = viewer[method]();
                if (runtime._destroyed || runtime.viewer !== viewer) return false;
                if (Number.isFinite(index) && index >= 0) runtime.search.activeIndex = index;
                return true;
            }
            const total = runtime.search.total;
            const current = runtime.search.activeIndex >= 0 ? runtime.search.activeIndex : 0;
            runtime.search.activeIndex = (current + direction + total) % total;
            return true;
        }

        runtime.searchNext = function () {
            return stepSearch(1);
        };

        runtime.searchPrevious = function () {
            return stepSearch(-1);
        };

        /**
         * The mounted viewer is already `runtime.viewer`. This does not
         * replace it. A search that opened before the viewer was ready is
         * applied to that same instance.
         */
        runtime.attachSearchViewer = function (viewer) {
            if (runtime._destroyed || !viewer || runtime.viewer !== viewer) return false;
            if (!runtime.search.open) return true;
            if (typeof viewer.openSearch === 'function') viewer.openSearch();
            if (runtime.search.query.trim() && typeof viewer.commitSearch === 'function') {
                viewer.commitSearch(runtime.search.query, runtime.search.epoch);
            }
            return true;
        };

        runtime.destroy = function () {
            if (runtime._destroyed) return;
            if (typeof runtime._unbindSearch === 'function') {
                try {
                    runtime._unbindSearch();
                } catch (_e0) {}
                runtime._unbindSearch = null;
            }
            try {
                runtime.closeSearch();
            } catch (_e1) {}
            runtime._destroyed = true;
            if (runtime.annotationPersistence && typeof runtime.annotationPersistence.destroy === 'function') {
                try {
                    runtime.annotationPersistence.destroy();
                } catch (_e) {}
            }
            runtime.annotationPersistence = null;
            try {
                runtime.flushLastPage();
            } catch (_e) {}
            if (runtime.lastPage && typeof runtime.lastPage.detach === 'function') {
                try {
                    runtime.lastPage.detach();
                } catch (_e) {}
            }
            runtime.lastPage = null;
            if (runtime.viewer && typeof runtime.viewer.destroy === 'function') {
                try {
                    runtime.viewer.destroy();
                } catch (_e) {}
            }
            runtime.viewer = null;
        };

        return runtime;
    }

    function prksHasPendingWorkAnnotationSync(ctx) {
        if (ctx && typeof ctx.getResource === 'function') {
            const pdf = ctx.getResource('pdf');
            return !!(pdf && typeof pdf.hasPendingSync === 'function' && pdf.hasPendingSync());
        }
        let pending = false;
        if (typeof root.prksForEachLiveTabContext === 'function') {
            root.prksForEachLiveTabContext(function (c) {
                const pdf = c && typeof c.getResource === 'function' ? c.getResource('pdf') : null;
                if (pdf && typeof pdf.hasPendingSync === 'function' && pdf.hasPendingSync()) {
                    pending = true;
                }
            });
        }
        return pending;
    }

    const api = {
        createWorkPdfRuntime: createWorkPdfRuntime,
        createPdfAnnotationPersistenceWorker: createPdfAnnotationPersistenceWorker,
        prksPdfPersistenceStillLive: prksPdfPersistenceStillLive,
        prksPdfPersistenceSetupEligible: prksPdfPersistenceSetupEligible,
        prksInstallPdfAnnotationPersistenceIfCurrent: prksInstallPdfAnnotationPersistenceIfCurrent,
        prksHasPendingWorkAnnotationSync: prksHasPendingWorkAnnotationSync,
        prksEmptyPdfAnnotationCache: emptyAnnotationCache,
        prksPdfSearchStill: prksPdfSearchStill,
        bindPdfSurfaceSearch: bindPdfSurfaceSearch,
    };
    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
