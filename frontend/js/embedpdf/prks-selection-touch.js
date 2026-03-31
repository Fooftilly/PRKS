/**
 * Touch-oriented text selection: word snap after drag, snap button, adjust handles.
 * Depends on EmbedPDF registry (selection, scroll, store) — same as works-pdf.js.
 */

import {
    prksComputeRectsAndSlices,
    prksDispatchSelectionRange,
    prksGlyphAt,
    prksSnapSelectionRangeToWords,
} from './prks-selection-geometry.js';

/** @param {any} viewer */
function prksResolvePdfHostEl(viewer) {
    if (viewer === window.uploadViewer) {
        return document.getElementById('upload-viewer');
    }
    return document.getElementById('pdf-viewer');
}

/** @param {HTMLElement} root */
function prksFindScrollHost(root) {
    if (!root) return null;
    let best = null;
    let bestScroll = 0;

    function consider(el) {
        if (!el || el.nodeType !== 1) return;
        const sh = el.scrollHeight;
        const ch = el.clientHeight;
        if (sh > ch + 40 && el.clientWidth > 80) {
            const excess = sh - ch;
            if (excess > bestScroll) {
                bestScroll = excess;
                best = el;
            }
        }
    }

    function walk(node) {
        if (!node) return;
        consider(node);
        if (node.shadowRoot) walk(node.shadowRoot);
        const kids = node.children;
        if (!kids) return;
        for (let i = 0; i < kids.length; i++) walk(kids[i]);
    }

    walk(root);
    return best || root;
}

/** @param {any} state @param {string} docId */
function prksGetCoreDoc(state, docId) {
    if (!state || !docId) return null;
    const d = state.core && state.core.documents && state.core.documents[docId];
    return d || null;
}

function prksEffectiveRotation(coreDoc, pageIndex) {
    if (!coreDoc || !coreDoc.document || !coreDoc.document.pages) return 0;
    const page = coreDoc.document.pages[pageIndex];
    const pr = page && page.rotation != null ? Number(page.rotation) : 0;
    const dr = coreDoc.rotation != null ? Number(coreDoc.rotation) : 0;
    return ((pr + dr) % 4 + 4) % 4;
}

/**
 * Scroll content coords → PDF page coords (rotation 0 only).
 * @returns {{ page: number, x: number, y: number } | null}
 */
function prksContentPointToPagePoint(registry, docId, coreDoc, scroll, cx, cy) {
    if (!registry || !docId || !coreDoc || !scroll) return null;
    const pages = coreDoc.document && coreDoc.document.pages;
    if (!pages || pages.length === 0) return null;
    const scale = Number(coreDoc.scale) || 1;

    const metrics = typeof scroll.getMetrics === 'function' ? scroll.getMetrics() : null;
    const vis = metrics && Array.isArray(metrics.visiblePages) ? metrics.visiblePages : null;
    const indices =
        vis && vis.length > 0
            ? vis.map((pn) => Number(pn) - 1)
            : Array.from({ length: pages.length }, (_, i) => i);

    for (let k = 0; k < indices.length; k++) {
        const p = indices[k];
        if (!Number.isFinite(p) || p < 0 || p >= pages.length) continue;
        if (prksEffectiveRotation(coreDoc, p) !== 0) continue;

        const pageW = pages[p].size.width;
        const pageH = pages[p].size.height;

        const tl = scroll.getRectPositionForPage(p, {
            origin: { x: 0, y: 0 },
            size: { width: 0, height: 0 },
        });
        const br = scroll.getRectPositionForPage(p, {
            origin: { x: pageW, y: pageH },
            size: { width: 0, height: 0 },
        });
        if (!tl || !br) continue;

        const minX = Math.min(tl.origin.x, br.origin.x);
        const maxX = Math.max(tl.origin.x, br.origin.x);
        const minY = Math.min(tl.origin.y, br.origin.y);
        const maxY = Math.max(tl.origin.y, br.origin.y);

        if (cx >= minX - 2 && cx <= maxX + 2 && cy >= minY - 2 && cy <= maxY + 2) {
            const pdfX = (cx - tl.origin.x) / scale;
            const pdfY = (cy - tl.origin.y) / scale;
            if (pdfX >= -20 && pdfX <= pageW + 20 && pdfY >= -20 && pdfY <= pageH + 20) {
                return { page: p, x: pdfX, y: pdfY };
            }
        }
    }

    return null;
}

function prksNormalizeRange(start, end) {
    if (
        start.page < end.page ||
        (start.page === end.page && start.index <= end.index)
    ) {
        return { start: { ...start }, end: { ...end } };
    }
    return { start: { ...end }, end: { ...start } };
}

function prksPagePointToContentXY(registry, docId, scroll, coreDoc, pageIndex, pdfX, pdfY) {
    if (!scroll || !coreDoc) return null;
    if (prksEffectiveRotation(coreDoc, pageIndex) !== 0) return null;
    const r = scroll.getRectPositionForPage(pageIndex, {
        origin: { x: pdfX, y: pdfY },
        size: { width: 0, height: 0 },
    });
    return r ? { x: r.origin.x, y: r.origin.y } : null;
}

function prksContentToClient(scrollHost, cx, cy) {
    const br = scrollHost.getBoundingClientRect();
    return {
        x: br.left + (cx - scrollHost.scrollLeft),
        y: br.top + (cy - scrollHost.scrollTop),
    };
}

function prksClientToContent(scrollHost, clientX, clientY) {
    const br = scrollHost.getBoundingClientRect();
    return {
        x: clientX - br.left + scrollHost.scrollLeft,
        y: clientY - br.top + scrollHost.scrollTop,
    };
}

function prksResolveCharInRuns(geo, charIndex) {
    if (!geo || !Array.isArray(geo.runs)) return null;
    for (let runIdx = 0; runIdx < geo.runs.length; runIdx++) {
        const run = geo.runs[runIdx];
        const localIdx = charIndex - run.charStart;
        if (localIdx >= 0 && localIdx < run.glyphs.length) return { runIdx, localIdx, run };
    }
    return null;
}

/**
 * @param {any} viewer
 * @param {() => string | null} findDocumentIdFromState
 */
export function installPrksEmbedPdfSelectionAssist(viewer, findDocumentIdFromState) {
    if (!viewer || typeof findDocumentIdFromState !== 'function') return;

    if (
        typeof window.__prksEmbedPdfSelectionAssistDetach === 'function' &&
        window.__prksEmbedPdfSelectionAssistViewer === viewer
    ) {
        try {
            window.__prksEmbedPdfSelectionAssistDetach();
        } catch (_e) {}
    }

    const hostEl = prksResolvePdfHostEl(viewer);
    if (!hostEl) return;

    const handleStart = document.createElement('div');
    const handleEnd = document.createElement('div');
    handleStart.className = 'prks-embedpdf-selection-handle prks-embedpdf-selection-handle--start';
    handleEnd.className = 'prks-embedpdf-selection-handle prks-embedpdf-selection-handle--end';
    handleStart.setAttribute('data-prks-handle', 'start');
    handleEnd.setAttribute('data-prks-handle', 'end');
    document.body.append(handleStart, handleEnd);

    let adjustMode = false;
    let unsubscribers = [];
    let rafId = 0;
    let scrollHost = null;
    /** Stable key for last completed (not mid-drag) selection — coarse touch only. */
    let lastCompletedSelKey = '';
    let snapOuterRaf = 0;
    let snapInnerRaf = 0;

    /** @type {'start' | 'end' | null} */
    let dragWhich = null;
    let dragSnapshot = null;
    const DRAG_MAX_INDEX_STEP = 48;

    const coarsePointer =
        typeof window !== 'undefined' &&
        typeof window.matchMedia === 'function' &&
        window.matchMedia('(pointer: coarse)').matches;

    async function getRegistry() {
        const reg = viewer.registry;
        if (reg && typeof reg.then === 'function') return reg;
        return reg;
    }

    function refreshScrollHost() {
        scrollHost = prksFindScrollHost(hostEl) || hostEl;
    }

    function hideHandles() {
        handleStart.classList.remove('is-visible');
        handleEnd.classList.remove('is-visible');
    }

    function showHandlesVisible(on) {
        if (!on) {
            hideHandles();
            return;
        }
        handleStart.classList.add('is-visible');
        handleEnd.classList.add('is-visible');
    }


    function layoutHandles() {
        if (!adjustMode) return;
        refreshScrollHost();
        const registry = viewer.__prksSelAssistRegistry;
        const docId = viewer.__prksSelAssistDocId;
        if (!registry || !docId || !scrollHost) return;

        const state = registry.store && registry.store.getState();
        const coreDoc = prksGetCoreDoc(state, docId);
        const scroll = registry.getPlugin('scroll')?.provides?.()?.forDocument?.(docId);
        const selPlug = registry.getPlugin('selection')?.provides?.()?.forDocument?.(docId);
        if (!coreDoc || !scroll || !selPlug || typeof selPlug.getState !== 'function') return;

        const docState = selPlug.getState();
        const sel = docState.selection;
        if (!sel) {
            hideHandles();
            return;
        }

        if (prksEffectiveRotation(coreDoc, sel.start.page) !== 0 || prksEffectiveRotation(coreDoc, sel.end.page) !== 0) {
            hideHandles();
            return;
        }

        const rectsStart = docState.rects[sel.start.page];
        const rectsEnd = docState.rects[sel.end.page];
        if (!rectsStart || !rectsStart.length || !rectsEnd || !rectsEnd.length) return;

        const r0 = rectsStart[0];
        const r1 = rectsEnd[rectsEnd.length - 1];

        const pStart = {
            x: r0.origin.x,
            y: r0.origin.y,
        };
        const pEnd = {
            x: r1.origin.x + r1.size.width,
            y: r1.origin.y + r1.size.height,
        };

        const c0 = prksPagePointToContentXY(registry, docId, scroll, coreDoc, sel.start.page, pStart.x, pStart.y);
        const c1 = prksPagePointToContentXY(registry, docId, scroll, coreDoc, sel.end.page, pEnd.x, pEnd.y);
        if (!c0 || !c1) return;

        const s0 = prksContentToClient(scrollHost, c0.x, c0.y);
        const s1 = prksContentToClient(scrollHost, c1.x, c1.y);

        const handleW = 24;
        const handleH = 28;
        const rightClearancePx = 10;
        const startTipInsetX = -9;
        const startTipInsetY = 12;
        const endTipInsetY = 7;
        const startLeft = s0.x - handleW - startTipInsetX;
        const startTop = s0.y - handleH + startTipInsetY;
        const endLeft = s1.x + rightClearancePx;
        const endTop = s1.y + endTipInsetY;
        handleStart.style.left = `${startLeft}px`;
        handleStart.style.top = `${startTop}px`;
        handleEnd.style.left = `${endLeft}px`;
        handleEnd.style.top = `${endTop}px`;

        showHandlesVisible(true);
    }

    function scheduleLayout() {
        if (rafId) cancelAnimationFrame(rafId);
        rafId = requestAnimationFrame(() => {
            rafId = 0;
            layoutHandles();
        });
    }

    function cancelScheduledCoarseSnap() {
        if (snapOuterRaf) cancelAnimationFrame(snapOuterRaf);
        if (snapInnerRaf) cancelAnimationFrame(snapInnerRaf);
        snapOuterRaf = 0;
        snapInnerRaf = 0;
    }

    /** After selection gesture ends, geometry/rects may lag one frame; double-rAF before snap. */
    function scheduleCoarseAutoSnap() {
        cancelScheduledCoarseSnap();
        snapOuterRaf = requestAnimationFrame(() => {
            snapOuterRaf = 0;
            snapInnerRaf = requestAnimationFrame(() => {
                snapInnerRaf = 0;
                runSnapWords();
            });
        });
    }

    function applySelectionWithUiSync(registry, docId, range, reason, fallbackGeometry) {
        const selectionPlugin = registry.getPlugin('selection');
        const hasApplyInstantSelection =
            !!selectionPlugin && typeof selectionPlugin.applyInstantSelection === 'function';
        const samePage = !!range && range.start.page === range.end.page;
        let usedInstant = false;
        let instantOk = false;
        if (hasApplyInstantSelection && samePage) {
            try {
                selectionPlugin.applyInstantSelection(
                    docId,
                    range.start.page,
                    range.start.index,
                    range.end.index,
                    'pointerMode',
                );
                usedInstant = true;
                instantOk = true;
            } catch (_e) {
                usedInstant = true;
                instantOk = false;
            }
        }
        if (!instantOk) {
            prksDispatchSelectionRange(registry.store, docId, range, fallbackGeometry);
        }
    }

    function runSnapWords() {
        const registry = viewer.__prksSelAssistRegistry;
        const docId = viewer.__prksSelAssistDocId;
        if (!registry || !docId || !registry.store) return;

        const selPlug = registry.getPlugin('selection')?.provides?.()?.forDocument?.(docId);
        if (!selPlug || typeof selPlug.getState !== 'function') return;

        const docState = selPlug.getState();
        if (!docState.selection) return;

        const snapped = prksSnapSelectionRangeToWords(docState);
        const a = docState.selection;
        const unchanged =
            !snapped ||
            (a.start.page === snapped.start.page &&
                a.end.page === snapped.end.page &&
                a.start.index === snapped.start.index &&
                a.end.index === snapped.end.index);

        if (!snapped || unchanged) return;

        applySelectionWithUiSync(registry, docId, snapped, 'snap-button-or-auto', docState.geometry);
        scheduleLayout();
    }

    function applyRangeFromDrag(which, glyphPage, glyphIdx) {
        const registry = viewer.__prksSelAssistRegistry;
        const docId = viewer.__prksSelAssistDocId;
        if (!registry || !docId || !dragSnapshot) return;

        const selPlug = registry.getPlugin('selection')?.provides?.()?.forDocument?.(docId);
        if (!selPlug) return;

        const docState = selPlug.getState();
        let start = { ...dragSnapshot.start };
        let end = { ...dragSnapshot.end };

        if (which === 'start') {
            if (glyphPage !== start.page) return;
            start = { page: glyphPage, index: glyphIdx };
        } else {
            if (glyphPage !== end.page) return;
            end = { page: glyphPage, index: glyphIdx };
        }

        const norm = prksNormalizeRange(start, end);
        const { allRects, allSlices } = prksComputeRectsAndSlices(norm, docState.geometry);

        registry.store.dispatch({
            type: 'SELECTION/SET_SELECTION',
            payload: { documentId: docId, selection: norm },
        });
        registry.store.dispatch({
            type: 'SELECTION/SET_RECTS',
            payload: { documentId: docId, rects: allRects },
        });
        registry.store.dispatch({
            type: 'SELECTION/SET_SLICES',
            payload: { documentId: docId, slices: allSlices },
        });

        scheduleLayout();
    }

    function onPointerDownHandle(which, ev) {
        ev.preventDefault();
        ev.stopPropagation();
        const registry = viewer.__prksSelAssistRegistry;
        const docId = viewer.__prksSelAssistDocId;
        const selPlug = registry?.getPlugin('selection')?.provides?.()?.forDocument?.(docId);
        if (!selPlug || typeof selPlug.getState !== 'function') return;

        const docState = selPlug.getState();
        if (!docState.selection) return;

        dragWhich = which;
        dragSnapshot = {
            start: { ...docState.selection.start },
            end: { ...docState.selection.end },
        };
        const el = which === 'start' ? handleStart : handleEnd;
        if (el.setPointerCapture && ev.pointerId != null) {
            try {
                el.setPointerCapture(ev.pointerId);
            } catch (_e) {}
        }
    }

    function onPointerMoveHandle(ev) {
        if (!dragWhich || !dragSnapshot) return;
        ev.preventDefault();

        refreshScrollHost();
        const registry = viewer.__prksSelAssistRegistry;
        const docId = viewer.__prksSelAssistDocId;
        const state = registry?.store?.getState?.();
        const coreDoc = prksGetCoreDoc(state, docId);
        const scroll = registry?.getPlugin('scroll')?.provides?.()?.forDocument?.(docId);
        if (!scrollHost || !coreDoc || !scroll) return;

        const { x: cx, y: cy } = prksClientToContent(scrollHost, ev.clientX, ev.clientY);
        const hit = prksContentPointToPagePoint(registry, docId, coreDoc, scroll, cx, cy);
        if (!hit) return;

        const geo = registry.getPlugin('selection')?.provides?.()?.forDocument?.(docId)?.getState?.()
            ?.geometry?.[hit.page];
        if (!geo) return;

        const g = prksGlyphAt(geo, { x: hit.x, y: hit.y }, 0.85);
        if (g < 0) return;
        const liveSel = registry?.getPlugin('selection')?.provides?.()?.forDocument?.(docId)?.getState?.()?.selection;
        const liveIdx =
            liveSel && dragWhich === 'start'
                ? liveSel.start.index
                : liveSel && dragWhich === 'end'
                  ? liveSel.end.index
                  : null;
        let nextIdx = g;
        if (liveIdx != null) {
            const lo = liveIdx - DRAG_MAX_INDEX_STEP;
            const hi = liveIdx + DRAG_MAX_INDEX_STEP;
            if (nextIdx < lo) nextIdx = lo;
            else if (nextIdx > hi) nextIdx = hi;
        }

        applyRangeFromDrag(dragWhich, hit.page, nextIdx);
    }

    function onPointerUpHandle(ev) {
        if (!dragWhich) return;
        ev.preventDefault();
        dragWhich = null;
        dragSnapshot = null;

        const registry = viewer.__prksSelAssistRegistry;
        const docId = viewer.__prksSelAssistDocId;
        const selPlug = registry?.getPlugin('selection')?.provides?.()?.forDocument?.(docId);
        if (!selPlug) return;

        const docState = selPlug.getState();
        if (!docState.selection) return;

        const snapped = prksSnapSelectionRangeToWords(docState);
        if (snapped) {
            applySelectionWithUiSync(registry, docId, snapped, 'drag-end', docState.geometry);
        }
        scheduleLayout();
    }

    void (async () => {
        const registry = await getRegistry();
        if (!registry || !registry.store) return;

        viewer.__prksSelAssistRegistry = registry;
        refreshScrollHost();

        const bindDoc = () => {
            const st = registry.store.getState();
            const docId = findDocumentIdFromState(st);
            viewer.__prksSelAssistDocId = docId || null;
            return docId;
        };

        bindDoc();

        const selCap = registry.getPlugin('selection')?.provides?.();
        if (!selCap || typeof selCap.onSelectionChange !== 'function') {
            handleStart.remove();
            handleEnd.remove();
            return;
        }

        function syncSelectionBar() {
            bindDoc();
            const docId = viewer.__prksSelAssistDocId;
            let s = null;
            if (docId) {
                try {
                    const scope = selCap.forDocument(docId);
                    s = scope && typeof scope.getState === 'function' ? scope.getState() : null;
                } catch (_e) {
                    s = null;
                }
            }
            const hasSel = !!(s && s.selection);
            const selecting = s && s.selecting === true;
            const selectionIdle = hasSel && !selecting;

            if (!hasSel) {
                lastCompletedSelKey = '';
                cancelScheduledCoarseSnap();
                adjustMode = false;
                hideHandles();
                return;
            }

            if (!coarsePointer) {
                if (adjustMode) {
                    adjustMode = false;
                    hideHandles();
                }
                return;
            }

            if (selectionIdle && s.selection) {
                const key = `${s.selection.start.page}:${s.selection.start.index}-${s.selection.end.page}:${s.selection.end.index}`;
                if (key !== lastCompletedSelKey) {
                    lastCompletedSelKey = key;
                    adjustMode = true;
                }
                scheduleCoarseAutoSnap();
            }

            if (adjustMode) {
                scheduleLayout();
            }
        }

        const unsubSel = selCap.onSelectionChange(() => syncSelectionBar());

        unsubscribers.push(() => {
            if (typeof unsubSel === 'function') unsubSel();
            else if (unsubSel && typeof unsubSel.unsubscribe === 'function') unsubSel.unsubscribe();
        });

        queueMicrotask(() => syncSelectionBar());

        if (typeof selCap.onEndSelection === 'function') {
            const hook = selCap.onEndSelection((evt) => {
                if (!coarsePointer) return;
                try {
                    const docId = evt && evt.documentId ? evt.documentId : findDocumentIdFromState(registry.store.getState());
                    if (!docId || !selCap.forDocument) return;
                    const sc = selCap.forDocument(docId);
                    const ds = sc && typeof sc.getState === 'function' ? sc.getState() : null;
                    if (!ds || !ds.selection) return;
                    // Defer to the same double-rAF path as syncSelectionBar so geometry matches EmbedPDF.
                    scheduleCoarseAutoSnap();
                    scheduleLayout();
                } catch (_e) {}
            });
            unsubscribers.push(() => {
                if (typeof hook === 'function') hook();
                else if (hook && typeof hook.unsubscribe === 'function') hook.unsubscribe();
            });
        }

        const scrollPlug = registry.getPlugin('scroll')?.provides?.();
        if (scrollPlug && typeof scrollPlug.onScroll === 'function') {
            const u = scrollPlug.onScroll(() => scheduleLayout());
            unsubscribers.push(() => {
                if (typeof u === 'function') u();
                else if (u && typeof u.unsubscribe === 'function') u.unsubscribe();
            });
        }
        const vpPlug = registry.getPlugin('viewport')?.provides?.();
        if (vpPlug && typeof vpPlug.onViewportChange === 'function') {
            const u = vpPlug.onViewportChange(() => scheduleLayout());
            unsubscribers.push(() => {
                if (typeof u === 'function') u();
                else if (u && typeof u.unsubscribe === 'function') u.unsubscribe();
            });
        }

        handleStart.addEventListener('pointerdown', (e) => onPointerDownHandle('start', e));
        handleEnd.addEventListener('pointerdown', (e) => onPointerDownHandle('end', e));
        window.addEventListener('pointermove', onPointerMoveHandle, { passive: false });
        window.addEventListener('pointerup', onPointerUpHandle, { passive: false });
        window.addEventListener('pointercancel', onPointerUpHandle, { passive: false });
        unsubscribers.push(() => {
            window.removeEventListener('pointermove', onPointerMoveHandle);
            window.removeEventListener('pointerup', onPointerUpHandle);
            window.removeEventListener('pointercancel', onPointerUpHandle);
        });
    })();

    function detach() {
        cancelScheduledCoarseSnap();
        if (rafId) cancelAnimationFrame(rafId);
        rafId = 0;
        for (const fn of unsubscribers) {
            try {
                fn();
            } catch (_e) {}
        }
        unsubscribers = [];
        handleStart.remove();
        handleEnd.remove();
        delete viewer.__prksSelAssistRegistry;
        delete viewer.__prksSelAssistDocId;
        if (window.__prksEmbedPdfSelectionAssistDetach === detach) {
            window.__prksEmbedPdfSelectionAssistDetach = null;
        }
        if (window.__prksEmbedPdfSelectionAssistViewer === viewer) {
            window.__prksEmbedPdfSelectionAssistViewer = null;
        }
    }

    window.__prksEmbedPdfSelectionAssistDetach = detach;
    window.__prksEmbedPdfSelectionAssistViewer = viewer;
}
