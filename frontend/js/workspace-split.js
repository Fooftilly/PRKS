/**
 * Workspace divider mechanics. Owns every separator's DOM presentation and
 * interaction only; canonical ratio state lives in workspace-tabs.js
 * (`mainSplitRatio` for the root Main/Secondary divider, `secondaryTree`
 * split-node `ratio` for nested Secondary dividers). This is the one
 * separator implementation: pointer drag, keyboard resize, ARIA, and
 * min-size clamping, shared by the root divider and every nested Secondary
 * split divider. Tile-local consumers (PDF viewer, EasyMDE, etc.) merely
 * react to their own container resizing; they must not duplicate this logic.
 *
 * Layout-only: dragging never mounts/unmounts a TabContext, never renders a
 * route, and never triggers a leave guard.
 */
(function (root) {
    'use strict';

    /* Single source of truth for minimum pane widths (do not scatter literals in CSS/JS). */
    const PRKS_SPLIT_MAIN_MIN_PX = 360;
    const PRKS_SPLIT_SECONDARY_MIN_PX = 320;
    /* Single source of truth for minimum nested Secondary leaf dimensions. Nested splits are
     * symmetric (no Main/Secondary role distinction inside the Secondary region), so both
     * children of a nested split share the same minimum. */
    const PRKS_NESTED_MIN_WIDTH_PX = 280;
    const PRKS_NESTED_MIN_HEIGHT_PX = 200;
    /* Must match the --prks-workspace-separator-size default in style.css. */
    const PRKS_SPLIT_SEPARATOR_TRACK_PX = 1;
    const PRKS_SPLIT_STEP = 0.02;
    const PRKS_SPLIT_STEP_SHIFT = 0.05;
    const DEFAULT_RATIO_FALLBACK = 0.58;
    const DEFAULT_NESTED_RATIO_FALLBACK = 0.5;

    let canvasRef = null;
    let separatorRef = null;
    /* Single active-drag cleanup handle shared by the root divider and every nested Secondary
     * separator: only one physical pointer drag can be in flight at a time. Every termination
     * path (pointerup, pointercancel, lostpointercapture, or the separator being removed from
     * the DOM) funnels through this same idempotent function so no path can leave a stuck
     * cursor/class/capture behind. */
    let activeDragCleanup = null;

    function terminateActiveDrag() {
        if (typeof activeDragCleanup !== 'function') return;
        const fn = activeDragCleanup;
        activeDragCleanup = null;
        fn();
    }

    function doc() {
        return typeof document !== 'undefined' ? document : null;
    }

    /** Direct-child search (not a full-subtree querySelector): critical once nested split
     * containers exist, since a container's own separator/root marker must never be confused
     * with one belonging to a deeper descendant split node. */
    function directChild(container, predicate) {
        if (!container || !container.children) return null;
        const kids = container.children;
        for (let i = 0; i < kids.length; i++) {
            if (predicate(kids[i])) return kids[i];
        }
        return null;
    }

    function isSplitterEl(el) {
        return !!(el && el.className && el.className.indexOf('prks-splitter') !== -1);
    }

    /* Dynamic ratio bounds from a measured usable size and each side's minimum pixels.
     * Impossible splits collapse to a safe midpoint rather than negative/zero panes. */
    function computeBoundsGeneric(usable, minFirstPx, minSecondPx) {
        const w = Number(usable);
        if (!(w > 0)) return { minRatio: 0, maxRatio: 1 };
        let minRatio = minFirstPx / w;
        let maxRatio = 1 - minSecondPx / w;
        if (minRatio > maxRatio) {
            const mid = Math.max(0, Math.min(1, (minRatio + maxRatio) / 2));
            return { minRatio: mid, maxRatio: mid };
        }
        minRatio = Math.max(0, Math.min(1, minRatio));
        maxRatio = Math.max(0, Math.min(1, maxRatio));
        return { minRatio: minRatio, maxRatio: maxRatio };
    }

    /** Root Main/Secondary bounds from measured usable width. */
    function prksSplitComputeBounds(usableWidth) {
        return computeBoundsGeneric(usableWidth, PRKS_SPLIT_MAIN_MIN_PX, PRKS_SPLIT_SECONDARY_MIN_PX);
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

    function measureUsable(container, axis) {
        if (!container) return 0;
        let size = 0;
        if (typeof container.getBoundingClientRect === 'function') {
            const rect = container.getBoundingClientRect();
            size = rect ? (axis === 'top-bottom' ? rect.height : rect.width) : 0;
        }
        if (!size) size = axis === 'top-bottom' ? container.clientHeight || 0 : container.clientWidth || 0;
        return Math.max(0, size - PRKS_SPLIT_SEPARATOR_TRACK_PX);
    }

    function updateAria(el, ratio, bounds, axis, valueTextFor) {
        if (!el) return;
        const now = Math.round(ratio * 100);
        const min = Math.round(bounds.minRatio * 100);
        const max = Math.round(bounds.maxRatio * 100);
        el.setAttribute('aria-valuemin', String(min));
        el.setAttribute('aria-valuemax', String(max));
        el.setAttribute('aria-valuenow', String(now));
        el.setAttribute('aria-valuetext', valueTextFor(now));
        el.setAttribute('aria-orientation', axis === 'top-bottom' ? 'horizontal' : 'vertical');
    }

    /* ==================== Root Main/Secondary divider ==================== */

    /** Applies a ratio to the canvas CSS var + separator ARIA. Returns the clamped ratio actually applied. */
    function applyRatioToDom(canvas, el, ratio) {
        const usable = measureUsable(canvas, 'left-right');
        const bounds = prksSplitComputeBounds(usable);
        const clamped = prksSplitClampRatio(ratio, bounds);
        const mainPx = usable * clamped;
        if (canvas && canvas.style && typeof canvas.style.setProperty === 'function') {
            canvas.style.setProperty('--prks-main-split-width', mainPx + 'px');
        }
        updateAria(el, clamped, bounds, 'left-right', function (now) {
            return 'Main ' + now + '%, secondary ' + (100 - now) + '%';
        });
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

    function dragCursorClass(axis) {
        return axis === 'top-bottom' ? 'prks-resizing-split--horizontal' : 'prks-resizing-split';
    }

    function beginDragCursor(axis) {
        const d = doc();
        if (d && d.body && d.body.classList) d.body.classList.add(dragCursorClass(axis));
    }

    function endDragCursor(axis) {
        const d = doc();
        if (d && d.body && d.body.classList) d.body.classList.remove(dragCursorClass(axis));
    }

    /** Called whenever a separator is about to be removed (hide split, narrow fallback, a
     * Secondary leaf closing/collapsing). Must run before the DOM removal so a mid-drag
     * pointer never keeps a stale capture, cursor, or listener alive. */
    function releaseDragState(el, axis) {
        terminateActiveDrag();
        if (el && el.classList) el.classList.remove('is-dragging');
        endDragCursor(axis);
    }

    /**
     * Generic pointer-drag lifecycle shared by the root divider and every nested Secondary
     * separator. `cfg`: { el, axis, getContainer(), ratioFromClientPoint(container, clientX,
     * clientY), commit(ratio) }. Single active-drag cleanup owner (`activeDragCleanup`);
     * pointer capture; every termination path (pointerup/pointercancel/lostpointercapture)
     * converges on one idempotent cleanup.
     */
    function beginPointerDrag(cfg, e) {
        if (e.pointerType === 'mouse' && typeof e.button === 'number' && e.button !== 0) return;
        const container = cfg.getContainer();
        if (!container) return;
        e.preventDefault();
        /* Defensively terminate any stale drag (e.g. a lost pointerup) before starting a new one. */
        terminateActiveDrag();
        let dragging = true;
        const pointerId = e.pointerId;
        const el = cfg.el;
        if (el.classList) el.classList.add('is-dragging');
        beginDragCursor(cfg.axis);
        try {
            if (typeof el.setPointerCapture === 'function') el.setPointerCapture(pointerId);
        } catch (_err) {}

        function onMove(ev) {
            if (!dragging) return;
            ev.preventDefault();
            cfg.commit(cfg.ratioFromClientPoint(container, ev.clientX, ev.clientY));
        }

        /* Idempotent: safe to invoke more than once (pointerup+lostpointercapture can both fire,
         * and a separator removal mid-drag calls this too). */
        function cleanup() {
            if (!dragging) return;
            dragging = false;
            if (el.classList) el.classList.remove('is-dragging');
            endDragCursor(cfg.axis);
            const d = doc();
            if (d) {
                d.removeEventListener('pointermove', onMove, true);
                d.removeEventListener('pointerup', onPointerUp, true);
                d.removeEventListener('pointercancel', onPointerUp, true);
            }
            if (el.removeEventListener) el.removeEventListener('lostpointercapture', onLost);
            try {
                if (typeof el.releasePointerCapture === 'function') el.releasePointerCapture(pointerId);
            } catch (_err2) {}
            if (activeDragCleanup === cleanup) activeDragCleanup = null;
        }

        function onPointerUp() {
            cleanup();
        }

        function onLost() {
            cleanup();
        }

        activeDragCleanup = cleanup;

        if (el.addEventListener) el.addEventListener('lostpointercapture', onLost);
        const d = doc();
        if (d) {
            d.addEventListener('pointermove', onMove, true);
            d.addEventListener('pointerup', onPointerUp, true);
            d.addEventListener('pointercancel', onPointerUp, true);
        }
    }

    function onSeparatorKeydown(el, e) {
        const canvas = el.closest ? el.closest('.prks-workspace-canvas') : canvasRef;
        if (!canvas) return;
        const key = e.key;
        if (key !== 'ArrowLeft' && key !== 'ArrowRight' && key !== 'Home' && key !== 'End') return;
        canvasRef = canvas;
        separatorRef = el;
        const usable = measureUsable(canvas, 'left-right');
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

    function ratioFromClientX(canvas, clientX) {
        const rect = canvas.getBoundingClientRect();
        const usable = Math.max(0, rect.width - PRKS_SPLIT_SEPARATOR_TRACK_PX);
        if (usable <= 0) return getCurrentRatio();
        const mainWidth = clientX - rect.left;
        return mainWidth / usable;
    }

    function onSeparatorPointerDown(el, e) {
        const canvas = el.closest ? el.closest('.prks-workspace-canvas') : null;
        if (!canvas) return;
        canvasRef = canvas;
        separatorRef = el;
        beginPointerDrag(
            {
                el: el,
                axis: 'left-right',
                getContainer: function () {
                    return canvas;
                },
                ratioFromClientPoint: function (container, clientX) {
                    return ratioFromClientX(container, clientX);
                },
                commit: function (ratio) {
                    commitRatio(ratio);
                },
            },
            e
        );
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
        return directChild(canvas, isSplitterEl);
    }

    function isSecondaryRootEl(el) {
        return !!(el && el.getAttribute && el.getAttribute('data-prks-secondary-root') === '1');
    }

    function positionSeparator(canvas, el) {
        const secondary = directChild(canvas, function (c) {
            return c !== el && isSecondaryRootEl(c);
        });
        if (secondary && secondary.parentNode === canvas) {
            if (el.nextSibling !== secondary) canvas.insertBefore(el, secondary);
        } else if (el.parentNode !== canvas) {
            canvas.appendChild(el);
        }
    }

    /** Called by workspace-tiling.js after it positions Main/Secondary tiles. */
    function prksWorkspaceSyncSplitSeparator(canvas, visualTiled, snap) {
        if (!canvas) return;
        const existing = findSeparator(canvas);
        if (!visualTiled) {
            if (existing) {
                releaseDragState(existing, 'left-right');
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

    /**
     * Called by workspace-tiling.js's canvas ResizeObserver. Recomputes bounds and reclamps
     * for rendering; no remount.
     *
     * `options.commit` (default true) controls whether the clamped value is written back to
     * the canonical `mainSplitRatio`. Callers must pass `{ commit: false }` while a physical
     * narrow-fallback transition is in flight or already narrow: the DOM/ARIA still reflect a
     * geometrically safe value, but the user's preferred ratio is not overwritten just because
     * the Secondary happens to be temporarily hidden or a leave guard is being evaluated.
     */
    function prksWorkspaceReapplySplitRatio(canvas, options) {
        if (!canvas) return;
        const el = findSeparator(canvas);
        if (!el) return;
        canvasRef = canvas;
        separatorRef = el;
        const opts = options || {};
        const commit = opts.commit !== false;
        if (commit) {
            commitRatio(getCurrentRatio());
        } else {
            applyRatioToDom(canvas, el, getCurrentRatio());
        }
    }

    /* ==================== Nested Secondary split separators ==================== */

    function nestedMinPx(axis) {
        return axis === 'top-bottom' ? PRKS_NESTED_MIN_HEIGHT_PX : PRKS_NESTED_MIN_WIDTH_PX;
    }

    function nestedBounds(usable, axis) {
        const min = nestedMinPx(axis);
        return computeBoundsGeneric(usable, min, min);
    }

    function getNestedRatio(splitId) {
        if (typeof root.prksWorkspaceSnapshot !== 'function' || typeof root.findNodeById !== 'function') {
            return DEFAULT_NESTED_RATIO_FALLBACK;
        }
        const snap = root.prksWorkspaceSnapshot();
        const node = root.findNodeById(snap.secondaryTree, splitId);
        return node && Number.isFinite(node.ratio) ? node.ratio : DEFAULT_NESTED_RATIO_FALLBACK;
    }

    /** Applies a ratio to the split container's CSS var (percentage, so ancestor resizes such as
     * dragging the root divider reflow nested panes automatically with no JS) + separator ARIA.
     * Returns the clamped ratio actually applied. */
    function applyNestedRatioToDom(container, el, axis, ratio) {
        const usable = measureUsable(container, axis);
        const bounds = nestedBounds(usable, axis);
        const clamped = prksSplitClampRatio(ratio, bounds);
        if (container && container.style && typeof container.style.setProperty === 'function') {
            container.style.setProperty('--prks-split-first-size', clamped * 100 + '%');
        }
        const firstLabel = axis === 'top-bottom' ? 'top' : 'first';
        const secondLabel = axis === 'top-bottom' ? 'bottom' : 'second';
        updateAria(el, clamped, bounds, axis, function (now) {
            return firstLabel + ' pane ' + now + '%, ' + secondLabel + ' pane ' + (100 - now) + '%';
        });
        return clamped;
    }

    /** Applies + persists (silently) a nested split's ratio. Used by drag/keyboard/reset. */
    function commitNestedRatio(splitId, container, el, axis, ratio) {
        const clamped = applyNestedRatioToDom(container, el, axis, ratio);
        if (typeof root.prksWorkspaceSetNestedSplitRatio === 'function') {
            root.prksWorkspaceSetNestedSplitRatio(splitId, clamped, { paint: false });
        }
        return clamped;
    }

    function nestedContainerFor(el) {
        return el && el.parentNode && el.parentNode.getAttribute && el.parentNode.getAttribute('data-prks-split-id')
            ? el.parentNode
            : null;
    }

    function onNestedKeydown(splitId, axis, el, e) {
        const container = nestedContainerFor(el);
        if (!container) return;
        const isHorizontalAxis = axis === 'top-bottom';
        const forwardKey = isHorizontalAxis ? 'ArrowDown' : 'ArrowRight';
        const backwardKey = isHorizontalAxis ? 'ArrowUp' : 'ArrowLeft';
        const key = e.key;
        if (key !== forwardKey && key !== backwardKey && key !== 'Home' && key !== 'End') return;
        const usable = measureUsable(container, axis);
        const bounds = nestedBounds(usable, axis);
        const current = getNestedRatio(splitId);
        e.preventDefault();
        if (key === forwardKey || key === backwardKey) {
            const step = e.shiftKey ? PRKS_SPLIT_STEP_SHIFT : PRKS_SPLIT_STEP;
            const delta = key === backwardKey ? -step : step;
            commitNestedRatio(splitId, container, el, axis, current + delta);
            return;
        }
        e.stopPropagation();
        if (key === 'Home') commitNestedRatio(splitId, container, el, axis, bounds.minRatio);
        else commitNestedRatio(splitId, container, el, axis, bounds.maxRatio);
    }

    function onNestedDblClick(splitId, axis, el, e) {
        e.preventDefault();
        const container = nestedContainerFor(el);
        if (!container) return;
        commitNestedRatio(splitId, container, el, axis, DEFAULT_NESTED_RATIO_FALLBACK);
        liveAnnounce('Split size reset.');
    }

    function ratioFromClientPointNested(container, axis, clientX, clientY) {
        const rect = container.getBoundingClientRect();
        const usable =
            axis === 'top-bottom'
                ? Math.max(0, rect.height - PRKS_SPLIT_SEPARATOR_TRACK_PX)
                : Math.max(0, rect.width - PRKS_SPLIT_SEPARATOR_TRACK_PX);
        if (usable <= 0) return DEFAULT_NESTED_RATIO_FALLBACK;
        const firstSize = axis === 'top-bottom' ? clientY - rect.top : clientX - rect.left;
        return firstSize / usable;
    }

    function onNestedPointerDown(splitId, axis, el, e) {
        beginPointerDrag(
            {
                el: el,
                axis: axis,
                getContainer: function () {
                    return nestedContainerFor(el);
                },
                ratioFromClientPoint: function (container, clientX, clientY) {
                    return ratioFromClientPointNested(container, axis, clientX, clientY);
                },
                commit: function (ratio) {
                    const container = nestedContainerFor(el);
                    if (container) commitNestedRatio(splitId, container, el, axis, ratio);
                },
            },
            e
        );
    }

    /** Creates one nested Secondary separator for split node `splitId`/`axis`. Bound once;
     * `prksWorkspaceSyncNestedSeparator` repositions/restyles it on every paint. */
    function createNestedSeparator(splitId, axis) {
        const d = doc();
        const el = d.createElement('div');
        el.setAttribute('data-prks-split-id', splitId);
        el.className = 'prks-splitter ' + (axis === 'top-bottom' ? 'prks-splitter--horizontal' : 'prks-splitter--vertical');
        el.setAttribute('role', 'separator');
        el.setAttribute('tabindex', '0');
        el.setAttribute('aria-label', 'Resize split pane');
        el.setAttribute('aria-orientation', axis === 'top-bottom' ? 'horizontal' : 'vertical');
        el.addEventListener('pointerdown', function (e) {
            onNestedPointerDown(splitId, axis, el, e);
        });
        el.addEventListener('keydown', function (e) {
            onNestedKeydown(splitId, axis, el, e);
        });
        el.addEventListener('dblclick', function (e) {
            onNestedDblClick(splitId, axis, el, e);
        });
        return el;
    }

    /** Called by workspace-tiling.js once per split node while walking the Secondary tree.
     * `container` is that split node's own DOM container (already carries
     * `data-prks-split-id`/`data-prks-axis`); `firstEl`/`secondEl` are the already-rendered
     * child DOM nodes (leaf tile or nested split container) for `node.first`/`node.second`.
     * Ensures container children are ordered [firstEl, separator, secondEl] and applies the
     * node's current ratio. Returns the separator element (for keyed reuse bookkeeping only;
     * callers do not need to retain it). */
    function prksWorkspaceSyncNestedSeparator(container, node, firstEl, secondEl) {
        if (!container || !node || !firstEl || !secondEl) return null;
        let el = findSeparator(container);
        if (el && el.getAttribute('data-prks-split-id') !== node.id) {
            /* A stale separator for a different split id somehow ended up here (should not
             * happen given split ids are unique DOM keys) -- rebuild defensively. */
            releaseDragState(el, el.getAttribute('aria-orientation') === 'horizontal' ? 'top-bottom' : 'left-right');
            if (el.parentNode) el.parentNode.removeChild(el);
            el = null;
        }
        if (!el) el = createNestedSeparator(node.id, node.axis);
        if (firstEl.parentNode !== container || container.children[0] !== firstEl) {
            container.insertBefore(firstEl, container.firstChild);
        }
        if (el.parentNode !== container || firstEl.nextSibling !== el) {
            container.insertBefore(el, firstEl.nextSibling);
        }
        if (secondEl.parentNode !== container || el.nextSibling !== secondEl) {
            container.insertBefore(secondEl, el.nextSibling);
        }
        applyNestedRatioToDom(container, el, node.axis, node.ratio);
        return el;
    }

    /** Called by workspace-tiling.js when a split container is about to be removed from the DOM
     * (leaf close/hide collapse, tree normalization). Ensures no mid-drag pointer state leaks. */
    function prksWorkspaceReleaseNestedSeparator(container) {
        const el = findSeparator(container);
        if (!el) return;
        releaseDragState(el, el.getAttribute('aria-orientation') === 'horizontal' ? 'top-bottom' : 'left-right');
    }

    const api = {
        prksWorkspaceSyncSplitSeparator: prksWorkspaceSyncSplitSeparator,
        prksWorkspaceReapplySplitRatio: prksWorkspaceReapplySplitRatio,
        prksWorkspaceSyncNestedSeparator: prksWorkspaceSyncNestedSeparator,
        prksWorkspaceReleaseNestedSeparator: prksWorkspaceReleaseNestedSeparator,
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
