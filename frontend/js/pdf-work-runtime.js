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

    function prksPdfPersistenceStillLive(ctx, generation, runtime) {
        if (!runtime || runtime._destroyed) return false;
        if (ctx && ctx.destroyed) return false;
        if (ctx && typeof ctx.isCurrent === 'function' && typeof generation === 'number' && !ctx.isCurrent(generation)) {
            return false;
        }
        if (ctx && typeof ctx.getResource === 'function' && ctx.getResource('pdf') !== runtime) {
            return false;
        }
        return true;
    }

    function prksInstallPdfAnnotationPersistenceIfCurrent(ctx, generation, runtime, installer) {
        if (!prksPdfPersistenceStillLive(ctx, generation, runtime)) return false;
        if (typeof installer === 'function') installer();
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

        worker.scheduleRetry = function () {
            if (isDead()) return;
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

    function createWorkPdfRuntime(options) {
        const opts = options || {};
        const workId = String(opts.workId || '');
        const runtime = {
            viewer: opts.viewer || null,
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
        };

        runtime.hasPendingSync = function () {
            const st = runtime.syncState;
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

        runtime.destroy = function () {
            if (runtime._destroyed) return;
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
        if (typeof root.prksForEachMountedTabContext === 'function') {
            root.prksForEachMountedTabContext(function (c) {
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
        prksInstallPdfAnnotationPersistenceIfCurrent: prksInstallPdfAnnotationPersistenceIfCurrent,
        prksHasPendingWorkAnnotationSync: prksHasPendingWorkAnnotationSync,
        prksEmptyPdfAnnotationCache: emptyAnnotationCache,
    };
    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
