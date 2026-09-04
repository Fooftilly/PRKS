/**
 * Root Main/Secondary divider. Owns the canonical ratio's DOM presentation only;
 * canonical `mainSplitRatio` state lives in workspace-tabs.js. This is the one
 * separator implementation: pointer drag, keyboard resize, ARIA, and min-width
 * clamping. Tile-local consumers (PDF viewer, EasyMDE, etc.) merely react to
 * their own container resizing; they must not duplicate this logic.
 *
 * Layout-only: dragging never mounts/unmounts a TabContext, never renders a
 * route, and never triggers a leave guard.
 */
(function (root) {
    'use strict';

    /* Single source of truth for minimum pane widths (do not scatter literals in CSS/JS). */
    const PRKS_SPLIT_MAIN_MIN_PX = 360;
    const PRKS_SPLIT_SECONDARY_MIN_PX = 320;
    /* Must match the --prks-workspace-separator-size default in style.css. */
    const PRKS_SPLIT_SEPARATOR_TRACK_PX = 1;
    const PRKS_SPLIT_STEP = 0.02;
    const PRKS_SPLIT_STEP_SHIFT = 0.05;
    const DEFAULT_RATIO_FALLBACK = 0.58;

    let canvasRef = null;
    let separatorRef = null;

    function doc() {
        return typeof document !== 'undefined' ? document : null;
    }

    /** Dynamic ratio bounds from measured usable width. Impossible splits collapse to a safe midpoint. */
    function prksSplitComputeBounds(usableWidth) {
        const w = Number(usableWidth);
        if (!(w > 0)) return { minRatio: 0, maxRatio: 1 };
        let minRatio = PRKS_SPLIT_MAIN_MIN_PX / w;
        let maxRatio = 1 - PRKS_SPLIT_SECONDARY_MIN_PX / w;
        if (minRatio > maxRatio) {
            const mid = Math.max(0, Math.min(1, (minRatio + maxRatio) / 2));
            return { minRatio: mid, maxRatio: mid };
        }
        minRatio = Math.max(0, Math.min(1, minRatio));
        maxRatio = Math.max(0, Math.min(1, maxRatio));
        return { minRatio: minRatio, maxRatio: maxRatio };
    }

    function prksSplitClampRatio(ratio, bounds) {
        const r = Number(ratio);
        const b = bounds || { minRatio: 0, maxRatio: 1 };
        if (!Number.isFinite(r)) return b.minRatio;
        return Math.min(b.maxRatio, Math.max(b.minRatio, r));
    }

    function getCurrentRatio() {
        if (typeof root.prksWorkspaceGetSplitRatio === 'function') return root.prksWorkspaceGetSplitRatio();
        return DEFAULT_RATIO_FALLBACK;
    }

    function measureUsable(canvas) {
        if (!canvas) return 0;
        let w = 0;
        if (typeof canvas.getBoundingClientRect === 'function') {
            const rect = canvas.getBoundingClientRect();
            w = rect ? rect.width : 0;
        }
        if (!w) w = canvas.clientWidth || 0;
        return Math.max(0, w - PRKS_SPLIT_SEPARATOR_TRACK_PX);
    }

    function updateAria(el, ratio, bounds) {
        if (!el) return;
        const now = Math.round(ratio * 100);
        const min = Math.round(bounds.minRatio * 100);
        const max = Math.round(bounds.maxRatio * 100);
        el.setAttribute('aria-valuemin', String(min));
        el.setAttribute('aria-valuemax', String(max));
        el.setAttribute('aria-valuenow', String(now));
        el.setAttribute('aria-valuetext', 'Main ' + now + '%, secondary ' + (100 - now) + '%');
    }

    /** Applies a ratio to the canvas CSS var + separator ARIA. Returns the clamped ratio actually applied. */
    function applyRatioToDom(canvas, el, ratio) {
        const usable = measureUsable(canvas);
        const bounds = prksSplitComputeBounds(usable);
        const clamped = prksSplitClampRatio(ratio, bounds);
        const mainPx = usable * clamped;
        if (canvas && canvas.style && typeof canvas.style.setProperty === 'function') {
            canvas.style.setProperty('--prks-main-split-width', mainPx + 'px');
        }
        updateAria(el, clamped, bounds);
        return clamped;
    }

    /** Applies + persists (silently) the canonical ratio. Used by drag/keyboard/reset/canvas-resize. */
    function commitRatio(ratio) {
        if (!canvasRef || !separatorRef) return ratio;
        const clamped = applyRatioToDom(canvasRef, separatorRef, ratio);
        if (typeof root.prksWorkspaceSetMainSplitRatio === 'function') {
            root.prksWorkspaceSetMainSplitRatio(clamped, { paint: false });
        }
        return clamped;
    }

    function liveAnnounce(text) {
        const d = doc();
        if (!d) return;
        const el = d.getElementById('prks-workspace-live');
        if (!el) return;
        el.textContent = '';
        el.textContent = text;
    }

    function beginDragCursor() {
        const d = doc();
        if (d && d.body && d.body.classList) d.body.classList.add('prks-resizing-split');
    }

    function endDragCursor() {
        const d = doc();
        if (d && d.body && d.body.classList) d.body.classList.remove('prks-resizing-split');
    }

    function onSeparatorKeydown(el, e) {
        const canvas = el.closest ? el.closest('.prks-workspace-canvas') : canvasRef;
        if (!canvas) return;
        const key = e.key;
        if (key !== 'ArrowLeft' && key !== 'ArrowRight' && key !== 'Home' && key !== 'End') return;
        canvasRef = canvas;
        separatorRef = el;
        const usable = measureUsable(canvas);
        const bounds = prksSplitComputeBounds(usable);
        const current = getCurrentRatio();
        e.preventDefault();
        if (key === 'ArrowLeft' || key === 'ArrowRight') {
            const step = e.shiftKey ? PRKS_SPLIT_STEP_SHIFT : PRKS_SPLIT_STEP;
            const delta = key === 'ArrowLeft' ? -step : step;
            commitRatio(current + delta);
            return;
        }
        e.stopPropagation();
        if (key === 'Home') commitRatio(bounds.minRatio);
        else commitRatio(bounds.maxRatio);
    }

    function onSeparatorDblClick(el, e) {
        e.preventDefault();
        const canvas = el.closest ? el.closest('.prks-workspace-canvas') : canvasRef;
        if (!canvas) return;
        canvasRef = canvas;
        separatorRef = el;
        const def =
            typeof root.prksWorkspaceDefaultSplitRatio === 'function'
                ? root.prksWorkspaceDefaultSplitRatio()
                : DEFAULT_RATIO_FALLBACK;
        commitRatio(def);
        liveAnnounce('Split size reset.');
    }

    function onSeparatorPointerDown(el, e) {
        if (e.pointerType === 'mouse' && typeof e.button === 'number' && e.button !== 0) return;
        const canvas = el.closest ? el.closest('.prks-workspace-canvas') : null;
        if (!canvas) return;
        e.preventDefault();
        canvasRef = canvas;
        separatorRef = el;
        let dragging = true;
        if (el.classList) el.classList.add('is-dragging');
        beginDragCursor();
        try {
            if (typeof el.setPointerCapture === 'function') el.setPointerCapture(e.pointerId);
        } catch (_err) {}

        function ratioFromClientX(clientX) {
            const rect = canvas.getBoundingClientRect();
            const usable = Math.max(0, rect.width - PRKS_SPLIT_SEPARATOR_TRACK_PX);
            if (usable <= 0) return getCurrentRatio();
            const mainWidth = clientX - rect.left;
            return mainWidth / usable;
        }

        function onMove(ev) {
            if (!dragging) return;
            ev.preventDefault();
            commitRatio(ratioFromClientX(ev.clientX));
        }

        function endDrag(ev) {
            if (!dragging) return;
            dragging = false;
            if (el.classList) el.classList.remove('is-dragging');
            endDragCursor();
            const d = doc();
            if (d) {
                d.removeEventListener('pointermove', onMove, true);
                d.removeEventListener('pointerup', endDrag, true);
                d.removeEventListener('pointercancel', endDrag, true);
            }
            if (el.removeEventListener) el.removeEventListener('lostpointercapture', onLost);
            if (ev && typeof ev.pointerId === 'number') {
                try {
                    if (typeof el.releasePointerCapture === 'function') el.releasePointerCapture(ev.pointerId);
                } catch (_err2) {}
            }
        }

        function onLost() {
            endDrag({});
        }

        if (el.addEventListener) el.addEventListener('lostpointercapture', onLost);
        const d = doc();
        if (d) {
            d.addEventListener('pointermove', onMove, true);
            d.addEventListener('pointerup', endDrag, true);
            d.addEventListener('pointercancel', endDrag, true);
        }
    }

    function bindSeparatorEvents(el) {
        el.addEventListener('pointerdown', function (e) {
            onSeparatorPointerDown(el, e);
        });
        el.addEventListener('keydown', function (e) {
            onSeparatorKeydown(el, e);
        });
        el.addEventListener('dblclick', function (e) {
            onSeparatorDblClick(el, e);
        });
    }

    function createSeparator() {
        const d = doc();
        const el = d.createElement('div');
        el.className = 'prks-splitter prks-splitter--vertical';
        el.setAttribute('role', 'separator');
        el.setAttribute('tabindex', '0');
        el.setAttribute('aria-label', 'Resize split view');
        el.setAttribute('aria-orientation', 'vertical');
        bindSeparatorEvents(el);
        return el;
    }

    function findSeparator(canvas) {
        if (!canvas || typeof canvas.querySelector !== 'function') return null;
        return canvas.querySelector('.prks-splitter');
    }

    function positionSeparator(canvas, el) {
        const secondary = canvas.querySelector('.prks-tile--secondary');
        if (secondary && secondary.parentNode === canvas) {
            if (el.nextSibling !== secondary) canvas.insertBefore(el, secondary);
        } else if (el.parentNode !== canvas) {
            canvas.appendChild(el);
        }
    }

    function releaseDragState(el) {
        if (el && el.classList) el.classList.remove('is-dragging');
        endDragCursor();
    }

    /** Called by workspace-tiling.js after it positions Main/Secondary tiles. */
    function prksWorkspaceSyncSplitSeparator(canvas, visualTiled, snap) {
        if (!canvas) return;
        const existing = findSeparator(canvas);
        if (!visualTiled) {
            if (existing) {
                releaseDragState(existing);
                if (existing.parentNode) existing.parentNode.removeChild(existing);
            }
            if (canvasRef === canvas) {
                canvasRef = null;
                separatorRef = null;
            }
            return;
        }
        const el = existing || createSeparator();
        positionSeparator(canvas, el);
        canvasRef = canvas;
        separatorRef = el;
        const ratio = snap && typeof snap.mainSplitRatio === 'number' ? snap.mainSplitRatio : getCurrentRatio();
        applyRatioToDom(canvas, el, ratio);
    }

    /** Called by workspace-tiling.js's canvas ResizeObserver. Recomputes bounds and reclamps; no remount. */
    function prksWorkspaceReapplySplitRatio(canvas) {
        if (!canvas) return;
        const el = findSeparator(canvas);
        if (!el) return;
        canvasRef = canvas;
        separatorRef = el;
        commitRatio(getCurrentRatio());
    }

    const api = {
        prksWorkspaceSyncSplitSeparator: prksWorkspaceSyncSplitSeparator,
        prksWorkspaceReapplySplitRatio: prksWorkspaceReapplySplitRatio,
        prksSplitComputeBounds: prksSplitComputeBounds,
        prksSplitClampRatio: prksSplitClampRatio,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
