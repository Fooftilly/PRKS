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

    function emptyAnnotationPopup() {
        return {
            open: false,
            annId: '',
            pageIndex: null,
            comment: '',
            meta: '',
            epoch: 0,
            generation: null,
            custom: null,
            docId: null,
            deletable: false,
        };
    }

    var PRKS_PDF_DRAWER_DEFAULT_WIDTH = 352;
    var PRKS_PDF_DRAWER_MIN_WIDTH = 240;
    var PRKS_PDF_DRAWER_MAX_WIDTH = 480;
    var PRKS_PDF_DRAWER_MIN_VIEWER = 320;
    var PRKS_PDF_DRAWER_PIN_MIN_PANE = PRKS_PDF_DRAWER_MIN_WIDTH + PRKS_PDF_DRAWER_MIN_VIEWER;
    var PRKS_PDF_DRAWER_STORAGE_KEY = 'prks.pdf.annotationDrawer';

    function clampAnnotationDrawerWidth(width) {
        const n = Number(width);
        if (!Number.isFinite(n)) return PRKS_PDF_DRAWER_DEFAULT_WIDTH;
        return Math.min(PRKS_PDF_DRAWER_MAX_WIDTH, Math.max(PRKS_PDF_DRAWER_MIN_WIDTH, Math.round(n)));
    }

    function readAnnotationDrawerPreference(storage) {
        const pref = { pinned: false, width: PRKS_PDF_DRAWER_DEFAULT_WIDTH };
        if (!storage || typeof storage.getItem !== 'function') return pref;
        try {
            const raw = storage.getItem(PRKS_PDF_DRAWER_STORAGE_KEY);
            if (!raw) return pref;
            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== 'object') return pref;
            pref.pinned = parsed.pinned === true;
            pref.width = clampAnnotationDrawerWidth(parsed.width);
        } catch (_e) {}
        return pref;
    }

    function writeAnnotationDrawerPreference(storage, drawer) {
        if (!storage || typeof storage.setItem !== 'function' || !drawer) return;
        try {
            storage.setItem(
                PRKS_PDF_DRAWER_STORAGE_KEY,
                JSON.stringify({
                    pinned: drawer.pinned === true,
                    width: drawer.width,
                })
            );
        } catch (_e) {}
    }

    /**
     * Closed and overlay do not take viewer width. Pinned does, and only when
     * the pane can hold at least the minimum drawer plus the minimum viewer.
     * A wider remembered width is capped for that pane so the viewer stays at
     * its minimum. A phone or forced-mobile shell uses a full-pane sheet.
     * Unknown pane width favors overlay until a real measurement arrives.
     */
    function annotationDrawerCanPin(drawer) {
        if (!drawer || drawer.mobile) return false;
        const pane = Number(drawer.paneWidth);
        return Number.isFinite(pane) && pane >= PRKS_PDF_DRAWER_PIN_MIN_PANE;
    }

    function annotationDrawerPlacement(drawer) {
        if (!drawer || !drawer.open) return 'closed';
        if (drawer.mobile) return 'sheet';
        if (!annotationDrawerCanPin(drawer)) return 'overlay';
        return drawer.pinned ? 'pinned' : 'overlay';
    }

    function annotationDrawerPinEnabled(drawer) {
        return annotationDrawerCanPin(drawer);
    }

    /**
     * Pixels the pinned drawer may take while leaving the viewer its minimum.
     * Null when the drawer is not pinned or the pane cannot hold that pair.
     */
    function annotationDrawerPaneRoom(drawer) {
        if (!drawer || annotationDrawerPlacement(drawer) !== 'pinned') return null;
        const room = Math.floor(Number(drawer.paneWidth) - PRKS_PDF_DRAWER_MIN_VIEWER);
        if (!(room >= PRKS_PDF_DRAWER_MIN_WIDTH)) return null;
        return room;
    }

    /**
     * Preference width, clamped to the global limits. While pinned, also
     * capped so this pane keeps PRKS_PDF_DRAWER_MIN_VIEWER for the PDF.
     * The stored preference is not rewritten by that pane cap.
     */
    function annotationDrawerLayoutWidth(drawer, requested) {
        const preferred = clampAnnotationDrawerWidth(
            requested != null ? requested : drawer && drawer.width
        );
        const room = annotationDrawerPaneRoom(drawer);
        if (room == null) return preferred;
        return Math.min(preferred, room);
    }

    /**
     * Widest width the handle can reach on this pane. Pinned mode cannot
     * offer more than the room beside the minimum viewer. Overlay and sheet
     * keep the global maximum because they do not take viewer width.
     */
    function annotationDrawerInteractionMax(drawer) {
        const room = annotationDrawerPaneRoom(drawer);
        if (room == null) return PRKS_PDF_DRAWER_MAX_WIDTH;
        return Math.min(PRKS_PDF_DRAWER_MAX_WIDTH, room);
    }

    /**
     * Fit Width and Fit Page recompute when the viewer box changes.
     * An explicit percentage is preserved. The caller must not turn either
     * result into a new viewer or a numeric zoom assignment.
     */
    function prksPdfAnnotationDrawerZoomAction(layout) {
        if (!layout || layout.kind == null || layout.kind === '') return 'follow-container';
        if (layout.kind === 'percent') return 'preserve';
        if (layout.kind === 'fit-width' || layout.kind === 'fit-page') return 'recompute';
        return 'follow-container';
    }

    function prksApplyPdfAnnotationDrawerChrome(pane, read) {
        if (!pane || !pane.dataset) return false;
        const placement = read && read.placement ? String(read.placement) : 'closed';
        if (placement === 'closed') {
            delete pane.dataset.prksAnnotationDrawer;
        } else {
            pane.dataset.prksAnnotationDrawer = placement;
        }
        if (pane.style && typeof pane.style.setProperty === 'function') {
            const width = read && Number(read.layoutWidth != null ? read.layoutWidth : read.width);
            const px = (Number.isFinite(width) ? Math.round(width) : PRKS_PDF_DRAWER_DEFAULT_WIDTH) + 'px';
            if (placement === 'overlay' || placement === 'pinned') {
                pane.style.setProperty('--pdf-annotation-drawer-width', px);
            } else if (typeof pane.style.removeProperty === 'function') {
                pane.style.removeProperty('--pdf-annotation-drawer-width');
            }
        }
        return placement === 'pinned';
    }

    function emptyAnnotationDrawer(pref) {
        const source = pref || { pinned: false, width: PRKS_PDF_DRAWER_DEFAULT_WIDTH };
        return {
            open: false,
            epoch: 0,
            pinned: source.pinned === true,
            width: clampAnnotationDrawerWidth(source.width),
            paneWidth: 0,
            mobile: false,
        };
    }

    function annotationDrawerItemId(item) {
        if (!item || typeof item !== 'object') return '';
        const id = item.id || item.uuid || item.annotationId || item._id;
        if (id == null || id === '') return '';
        return String(id);
    }

    function projectAnnotationDrawerItems(runtime) {
        const cache = runtime.annotationCache;
        const items = cache && Array.isArray(cache.items) ? cache.items : [];
        const project = typeof root.prksProjectPdfAnnotationDrawerItem === 'function'
            ? root.prksProjectPdfAnnotationDrawerItem
            : null;
        const out = [];
        for (let index = 0; index < items.length; index++) {
            const item = items[index];
            const id = annotationDrawerItemId(item);
            if (!id) continue;
            if (project) {
                let row = null;
                try {
                    row = project(item, index);
                } catch (_e) {
                    row = null;
                }
                if (row && row.id) {
                    out.push(row);
                    continue;
                }
            }
            out.push({
                id: id,
                index: index,
                text: id,
                comment: '',
                pageLabel: '',
                pageIndex: null,
                wikiLink: '[[pdf:' + id + ']]',
                metadataLabels: [],
            });
        }
        return out;
    }

    function annotationDrawerStatus(runtime) {
        const cache = runtime.annotationCache;
        const count = cache && Array.isArray(cache.items) ? cache.items.length : 0;
        return count + (count === 1 ? ' annotation' : ' annotations');
    }

    function annotationDrawerPublished(runtime) {
        const cache = runtime.annotationCache;
        return !!(cache && cache.listPublished);
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
        if (typeof runtime._unbindSearch === 'function') {
            const previous = runtime._unbindSearch;
            runtime._unbindSearch = null;
            try {
                previous();
            } catch (_e) {}
        }
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
        const drawerPref = readAnnotationDrawerPreference(opts.drawerStorage);
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
            annotationPopup: emptyAnnotationPopup(),
            annotationDrawer: emptyAnnotationDrawer(drawerPref),
            _drawerLayoutSignature: 'closed',
            _drawerStorage: opts.drawerStorage || null,
            _drawerPaneObserver: null,
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

        function pdfAnnotationHintId(item) {
            const id = item.id || item.uuid || item.annotationId || item._id;
            if (id == null || id === '') return '';
            return String(id);
        }

        function pdfAnnotationHintPage(item) {
            const page = item.pageIndex ?? item.page ?? item.pageNumber ?? item.page_index;
            if (page === undefined || page === null) return '?';
            return Number(page) + 1;
        }

        function pdfAnnotationHintText(item, idx) {
            let text = '';
            if (typeof root.annotationToText === 'function') {
                try {
                    text = root.annotationToText(item) || '';
                } catch (_e) {
                    text = '';
                }
            }
            if (!text) text = item.contents || item.content || item.comment || 'Annotation ' + (idx + 1);
            return String(text).replace(/\s+/g, ' ').trim().slice(0, 48);
        }

        function pdfAnnotationHint(item, idx) {
            if (!item || typeof item !== 'object') return null;
            const id = pdfAnnotationHintId(item);
            if (!id) return null;
            return {
                id: id,
                displayText: pdfAnnotationHintText(item, idx) + ' - p. ' + pdfAnnotationHintPage(item),
            };
        }

        runtime.getAnnotationHints = function () {
            const c = runtime.annotationCache;
            const items = c && Array.isArray(c.items) ? c.items : [];
            const out = [];
            for (let idx = 0; idx < items.length; idx++) {
                const hint = pdfAnnotationHint(items[idx], idx);
                if (hint) out.push(hint);
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

        function publishAnnotationEditorState() {
            const popup = runtime.annotationPopup;
            if (!popup || !popup.open || !popup.annId) {
                runtime.annotationEditorState = null;
                return;
            }
            runtime.annotationEditorState = {
                annId: String(popup.annId),
                pageIndex: popup.pageIndex,
                docId: popup.docId,
                custom: popup.custom && typeof popup.custom === 'object' ? popup.custom : {},
                epoch: popup.epoch,
                open: true,
            };
        }

        function annotationPopupViewerMatches(ticket) {
            if (!ticket || !ticket.annId) return false;
            if (runtime._destroyed) return false;
            if (ticket.viewer != null && ticket.viewer !== runtime.viewer) return false;
            if (typeof ticket.viewerToken === 'number' && ticket.viewerToken !== runtime.viewerSetupToken) {
                return false;
            }
            return true;
        }

        /**
         * Comment popup session for this runtime. Opening and closing do not
         * replace the viewer or change viewerSetupToken.
         */
        runtime.readAnnotationPopup = function () {
            const popup = runtime.annotationPopup || emptyAnnotationPopup();
            const page = Number(popup.pageIndex);
            const pageIndex = Number.isFinite(page) ? page : null;
            return {
                open: !!popup.open,
                annId: popup.open && popup.annId ? String(popup.annId) : '',
                pageIndex: popup.open ? pageIndex : null,
                comment: popup.open ? String(popup.comment || '') : '',
                meta: popup.open ? String(popup.meta || '') : '',
                epoch: typeof popup.epoch === 'number' ? popup.epoch : 0,
                deletable: !!(popup.open && popup.deletable),
            };
        };

        runtime.openAnnotationPopup = function (info) {
            if (runtime._destroyed || !info) return false;
            const annId = info.annId == null ? '' : String(info.annId);
            if (!annId) return false;
            const popup = runtime.annotationPopup;
            const same = !!(popup.open && popup.annId === annId);
            if (!same) {
                popup.epoch += 1;
                popup.open = true;
                popup.annId = annId;
                popup.comment = info.comment == null ? '' : String(info.comment);
                popup.custom = null;
                popup.deletable = info.deletable === true;
            } else if (typeof info.deletable === 'boolean') {
                popup.deletable = info.deletable;
            }
            if (info.pageIndex != null && Number.isFinite(Number(info.pageIndex))) {
                popup.pageIndex = Number(info.pageIndex);
            }
            if (info.meta != null) popup.meta = String(info.meta);
            if (info.custom && typeof info.custom === 'object') popup.custom = info.custom;
            if (info.docId != null) popup.docId = info.docId;
            if (typeof info.generation === 'number') popup.generation = info.generation;
            publishAnnotationEditorState();
            return true;
        };

        runtime.closeAnnotationPopup = function (annId) {
            if (runtime._destroyed) return false;
            const popup = runtime.annotationPopup;
            if (!popup.open) return false;
            if (annId != null && annId !== '' && String(annId) !== popup.annId) return false;
            popup.epoch += 1;
            popup.open = false;
            popup.annId = '';
            popup.comment = '';
            popup.meta = '';
            popup.pageIndex = null;
            popup.custom = null;
            popup.docId = null;
            popup.deletable = false;
            publishAnnotationEditorState();
            return true;
        };

        runtime.captureAnnotationPopupTicket = function (captured) {
            if (runtime._destroyed) return null;
            const popup = runtime.annotationPopup;
            const directId = captured && captured.directId != null ? String(captured.directId) : '';
            if (directId) {
                const blocked = popup.open && popup.annId === directId && popup.deletable !== true;
                return {
                    annId: directId,
                    epoch: null,
                    direct: true,
                    deletable: !blocked,
                    pageIndex: captured.pageIndex != null && Number.isFinite(Number(captured.pageIndex))
                        ? Number(captured.pageIndex)
                        : null,
                    generation: typeof captured.generation === 'number' ? captured.generation : null,
                    viewer: runtime.viewer,
                    viewerToken: runtime.viewerSetupToken,
                    custom: null,
                };
            }
            if (!popup.open || !popup.annId) return null;
            if (captured && captured.annId != null && String(captured.annId) !== popup.annId) return null;
            if (captured && captured.epoch != null && Number(captured.epoch) !== popup.epoch) return null;
            if (
                captured &&
                typeof captured.generation === 'number' &&
                typeof popup.generation === 'number' &&
                captured.generation !== popup.generation
            ) {
                return null;
            }
            return {
                annId: popup.annId,
                epoch: popup.epoch,
                direct: false,
                pageIndex: popup.pageIndex,
                generation: popup.generation,
                viewer: runtime.viewer,
                viewerToken: runtime.viewerSetupToken,
                custom: popup.custom && typeof popup.custom === 'object' ? popup.custom : {},
                deletable: popup.deletable === true,
            };
        };

        /** Viewer identity still matches the ticket. Independent of which popup is open. */
        runtime.annotationPopupWriteStill = function (ticket) {
            return annotationPopupViewerMatches(ticket);
        };

        /** The open popup is still the ticket's annotation and epoch. */
        runtime.annotationPopupStill = function (ticket) {
            if (!annotationPopupViewerMatches(ticket)) return false;
            if (ticket.direct || ticket.epoch == null) return false;
            const popup = runtime.annotationPopup;
            if (!popup.open) return false;
            if (String(popup.annId) !== String(ticket.annId)) return false;
            if (popup.epoch !== ticket.epoch) return false;
            return true;
        };

        runtime.noteAnnotationPopupComment = function (ticket, comment) {
            if (!runtime.annotationPopupStill(ticket)) return false;
            runtime.annotationPopup.comment = comment == null ? '' : String(comment);
            return true;
        };

        /**
         * Annotation list for this runtime. Opening, closing, pinning, and
         * resizing do not replace the viewer or change viewerSetupToken.
         * Overlay and sheet do not take viewer width. Pinned does.
         */
        runtime.readAnnotationDrawer = function () {
            const drawer = runtime.annotationDrawer || emptyAnnotationDrawer();
            const popup = runtime.annotationPopup;
            const selectedId = popup && popup.open && popup.annId ? String(popup.annId) : '';
            const placement = annotationDrawerPlacement(drawer);
            return {
                open: !!drawer.open,
                epoch: typeof drawer.epoch === 'number' ? drawer.epoch : 0,
                viewerToken: runtime.viewerSetupToken,
                selectedId: drawer.open ? selectedId : '',
                status: drawer.open ? annotationDrawerStatus(runtime) : '',
                published: drawer.open ? annotationDrawerPublished(runtime) : false,
                items: drawer.open ? projectAnnotationDrawerItems(runtime) : [],
                pinned: drawer.pinned === true,
                width: drawer.width,
                layoutWidth: annotationDrawerLayoutWidth(drawer),
                paneWidth: Number(drawer.paneWidth) || 0,
                minWidth: PRKS_PDF_DRAWER_MIN_WIDTH,
                maxWidth: PRKS_PDF_DRAWER_MAX_WIDTH,
                interactionMax: annotationDrawerInteractionMax(drawer),
                defaultWidth: PRKS_PDF_DRAWER_DEFAULT_WIDTH,
                placement: placement,
                pinEnabled: annotationDrawerPinEnabled(drawer),
            };
        };

        /** Last measured pane. A zero width leaves the previous measurement. */
        runtime.noteAnnotationDrawerFrame = function (frame) {
            if (runtime._destroyed) return runtime.readAnnotationDrawer();
            const drawer = runtime.annotationDrawer;
            const width = frame && Number(frame.paneWidth);
            if (Number.isFinite(width) && width > 0) drawer.paneWidth = width;
            if (frame && frame.mobile != null) drawer.mobile = !!frame.mobile;
            return runtime.readAnnotationDrawer();
        };

        runtime.setAnnotationDrawerPinned = function (pinned) {
            if (runtime._destroyed) return false;
            const drawer = runtime.annotationDrawer;
            const next = !!pinned;
            if (drawer.pinned !== next) {
                drawer.pinned = next;
                writeAnnotationDrawerPreference(runtime._drawerStorage, drawer);
            }
            return true;
        };

        runtime.setAnnotationDrawerWidth = function (width, opts) {
            if (runtime._destroyed) return false;
            const drawer = runtime.annotationDrawer;
            drawer.width = clampAnnotationDrawerWidth(width);
            if (!opts || opts.persist !== false) {
                writeAnnotationDrawerPreference(runtime._drawerStorage, drawer);
            }
            return true;
        };

        /** Painted width for a requested preference, including the pane cap. */
        runtime.annotationDrawerLayoutWidth = function (requested) {
            if (runtime._destroyed) return PRKS_PDF_DRAWER_DEFAULT_WIDTH;
            return annotationDrawerLayoutWidth(runtime.annotationDrawer, requested);
        };

        /**
         * True only when the viewer box must change: entering or leaving
         * pinned layout, changing the painted width while pinned, or changing
         * the measured pane while it stays pinned. Overlay width, sheet, and
         * open/close of an overlay do not.
         */
        runtime.annotationDrawerLayoutEffect = function () {
            const read = runtime.readAnnotationDrawer();
            const signature = read.placement === 'pinned'
                ? 'pinned:' + String(read.layoutWidth) + ':' + String(read.paneWidth)
                : read.placement;
            const previous = runtime._drawerLayoutSignature || 'closed';
            runtime._drawerLayoutSignature = signature;
            const wasPinned = previous.indexOf('pinned:') === 0;
            const nowPinned = signature.indexOf('pinned:') === 0;
            return {
                read: read,
                resized: signature !== previous && (wasPinned || nowPinned),
            };
        };

        runtime.openAnnotationDrawer = function () {
            if (runtime._destroyed) return false;
            const drawer = runtime.annotationDrawer;
            if (!drawer.open) {
                drawer.epoch += 1;
                drawer.open = true;
            }
            return true;
        };

        runtime.closeAnnotationDrawer = function () {
            if (runtime._destroyed) return false;
            const drawer = runtime.annotationDrawer;
            if (!drawer.open) return false;
            drawer.epoch += 1;
            drawer.open = false;
            return true;
        };

        runtime.toggleAnnotationDrawer = function () {
            if (runtime._destroyed) return false;
            if (runtime.annotationDrawer.open) return runtime.closeAnnotationDrawer();
            return runtime.openAnnotationDrawer();
        };

        runtime.annotationDrawerStill = function (ticket) {
            if (!ticket || runtime._destroyed) return false;
            if (ticket.viewer != null && ticket.viewer !== runtime.viewer) return false;
            if (typeof ticket.viewerToken === 'number' && ticket.viewerToken !== runtime.viewerSetupToken) {
                return false;
            }
            const drawer = runtime.annotationDrawer;
            if (!drawer || !drawer.open) return false;
            if (typeof ticket.epoch === 'number' && ticket.epoch !== drawer.epoch) return false;
            return true;
        };

        function invokePdfRuntimeHook(owner, method) {
            if (!owner || typeof owner[method] !== 'function') return;
            try {
                owner[method]();
            } catch (_e) {}
        }

        function releasePdfSearch() {
            if (typeof runtime._unbindSearch === 'function') {
                try {
                    runtime._unbindSearch();
                } catch (_e) {}
                runtime._unbindSearch = null;
            }
            try {
                runtime.closeSearch();
            } catch (_e) {}
        }

        runtime.destroy = function () {
            if (runtime._destroyed) return;
            if (runtime._drawerPaneObserver && typeof runtime._drawerPaneObserver.disconnect === 'function') {
                try {
                    runtime._drawerPaneObserver.disconnect();
                } catch (_eDrawerPane) {}
                runtime._drawerPaneObserver = null;
            }
            releasePdfSearch();
            try {
                runtime.closeAnnotationPopup();
            } catch (_ePopup) {}
            try {
                runtime.closeAnnotationDrawer();
            } catch (_eDrawer) {}
            runtime._destroyed = true;
            invokePdfRuntimeHook(runtime.annotationPersistence, 'destroy');
            runtime.annotationPersistence = null;
            try {
                runtime.flushLastPage();
            } catch (_e) {}
            invokePdfRuntimeHook(runtime.lastPage, 'detach');
            runtime.lastPage = null;
            invokePdfRuntimeHook(runtime.viewer, 'destroy');
            runtime.viewer = null;
        };

        return runtime;
    }

    /**
     * PDF pending-sync leave answer. Reports the existing runtime flag and,
     * only when this owner is actually leaving its Work route, asks with the
     * one native confirm. Draft probes are separate and run after this one.
     * Retires when the Vue PDF adapter registers this probe itself.
     */
    function prksAssessPendingPdfSyncLeave(ctx, nextHash) {
        const prevRoute = ctx && ctx.lastResolvedRoute;
        const parse = typeof root.prksParseRoute === 'function' ? root.prksParseRoute : null;
        const route = parse ? parse(nextHash || '#/folders') : null;
        const leavingWorkPage = !!(
            prevRoute &&
            prevRoute.name === 'work' &&
            route &&
            route.canonicalHash !== prevRoute.canonicalHash
        );
        const pendingFn =
            typeof root.prksHasPendingWorkAnnotationSync === 'function'
                ? root.prksHasPendingWorkAnnotationSync
                : prksHasPendingWorkAnnotationSync;
        if (!leavingWorkPage || !pendingFn(ctx)) return null;
        const ask = typeof window !== 'undefined' && window.confirm ? window.confirm : root.confirm;
        if (typeof ask !== 'function') {
            return { status: 'rejected-pending-pdf-sync', feature: 'pdf-sync' };
        }
        const ok = ask(
            'PDF annotation sync still running. Leave page before all changes save to server?'
        );
        if (!ok) return { status: 'rejected-pending-pdf-sync', feature: 'pdf-sync' };
        return null;
    }

    function prksInstallPdfLeaveProbe(api) {
        const target = api || root.prksTabLeave;
        if (!target || typeof target.registerProbe !== 'function') return;
        target.registerProbe({
            id: 'pdf-sync',
            order: 10,
            assess: prksAssessPendingPdfSyncLeave,
        });
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
        prksAssessPendingPdfSyncLeave: prksAssessPendingPdfSyncLeave,
        prksInstallPdfLeaveProbe: prksInstallPdfLeaveProbe,
        prksEmptyPdfAnnotationCache: emptyAnnotationCache,
        prksPdfSearchStill: prksPdfSearchStill,
        bindPdfSurfaceSearch: bindPdfSurfaceSearch,
        prksPdfAnnotationDrawerZoomAction: prksPdfAnnotationDrawerZoomAction,
        prksApplyPdfAnnotationDrawerChrome: prksApplyPdfAnnotationDrawerChrome,
        PRKS_PDF_DRAWER_DEFAULT_WIDTH: PRKS_PDF_DRAWER_DEFAULT_WIDTH,
        PRKS_PDF_DRAWER_MIN_WIDTH: PRKS_PDF_DRAWER_MIN_WIDTH,
        PRKS_PDF_DRAWER_MAX_WIDTH: PRKS_PDF_DRAWER_MAX_WIDTH,
        PRKS_PDF_DRAWER_PIN_MIN_PANE: PRKS_PDF_DRAWER_PIN_MIN_PANE,
    };
    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
    prksInstallPdfLeaveProbe();
})(typeof window !== 'undefined' ? window : globalThis);
