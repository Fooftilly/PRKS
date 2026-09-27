/**
 * Workspace canvas geometry and focus gestures.
 *
 * Production painting of the tab strip and the recursive Secondary tree lives
 * in the Vue workspace shell (`frontend-app/src/workspace-shell/`). This file
 * no longer builds tiles, split containers, or pane headers.
 *
 * Removed from the production renderer (frozen for the Node split/menu oracle
 * in `tests/browser/fixtures/workspace-tiling-legacy-painter.js`):
 * `renderTreeNode`, `createTile`, `ensureTile`, `fillHeader`, `applyTileClasses`,
 * `pruneStale`, `prksWorkspaceHostForTab` (now `workspace-hosts.js`), and the
 * tree-reconciling bodies of `prksWorkspaceSyncTiles` / `prksWorkspaceApplyFocus`.
 *
 * What stays here: the narrow-width predicate, the canvas ResizeObserver,
 * nested-split ResizeObservers the shell attaches by split id, pane focus
 * from pointer/focusin, and the Chromium click fallback for pane-header
 * buttons. `prksWorkspaceSyncTiles` and `prksWorkspaceApplyFocus` remain as
 * no-ops so existing callers do not throw; they do not paint.
 *
 * Temporary drag hooks (until #234) are the Vue DOM the shell renders:
 * `#prks-workspace-tabs`, `.prks-workspace-tab`, `.prks-tile[data-prks-tab-id]`,
 * `.prks-tile-header__grip`, `.prks-workspace-canvas`. `workspace-drag.js`
 * still binds those selectors. Context menus still open through
 * `prksWorkspaceOpenTabMenu`.
 */
(function (root) {
    'use strict';

    const NARROW_PX = 720;
    /* Shared with works.js notes layout and CSS @container (max-width: 720px).
     * CSS max-width is inclusive: 720px is narrow. JS must use <=, not <.
     * workspace-tabs.js prefers prksWorkspaceCanvasIsNarrow(); this named
     * export is the single JS authority for the pixel value and predicate. */
    root.PRKS_WORKSPACE_NARROW_PX = NARROW_PX;

    /** True when width is in the CSS max-width:NARROW_PX band (inclusive). */
    function prksWorkspaceWidthIsNarrow(width) {
        return typeof width === 'number' && width > 0 && width <= NARROW_PX;
    }
    let bound = false;
    let resizeObserver = null;
    let observedCanvas = null;
    let lastNarrow = null;
    let pendingNarrow = null;
    /* One ResizeObserver per live nested split container, keyed by split.id, so a container's
     * own geometry change (ancestor resize, window resize, a sibling pane changing size) can
     * reclamp that divider's effective ratio/ARIA without a paint. Created once when the
     * container is first rendered; disconnected the moment its split node is pruned so repeated
     * split/close cycles never leak observers. */
    const nestedObservers = Object.create(null);

    function disconnectNestedObserver(splitId) {
        const entry = splitId && nestedObservers[splitId];
        if (!entry) return;
        try {
            entry.observer.disconnect();
        } catch (_e) {}
        delete nestedObservers[splitId];
    }

    function watchNestedSplit(splitId, container) {
        if (!splitId || !container || typeof ResizeObserver === 'undefined') return;
        const entry = nestedObservers[splitId];
        if (entry && entry.container === container) return;
        if (entry) disconnectNestedObserver(splitId);
        const observer = new ResizeObserver(function () {
            if (typeof root.prksWorkspaceReclampNestedSplit === 'function') {
                root.prksWorkspaceReclampNestedSplit(container);
            }
        });
        observer.observe(container);
        nestedObservers[splitId] = { observer: observer, container: container };
    }

    function doc() {
        return typeof document !== 'undefined' ? document : null;
    }
    function pageContent() {
        const d = doc();
        return d ? d.getElementById('page-content') : null;
    }

    function existingCanvas() {
        const host = pageContent();
        if (!host || !host.querySelector) return null;
        return host.querySelector(':scope > .prks-workspace-canvas') || host.querySelector('.prks-workspace-canvas');
    }

    const CLICK_FALLBACK_SELECTOR = '.prks-tile-header__menu, .prks-tile-header__close';
    const CLICK_FALLBACK_THRESHOLD_PX = 6;
    /* At most one candidate live at a time -- a complete click-like gesture (pointerdown ->
     * pointerup on the SAME header button, same pointerId, released within a small click
     * threshold of where it started) tracked from pointerdown so the fallback below can tell a
     * genuine click apart from a drag whose pointerup happens to land over a button. Bound once
     * at the document level, never scoped to (or re-attached per) any one tile, precisely so
     * reparenting/rebuilding a tile can never affect whether this fires -- the button element
     * itself, not its tile ancestor, is all that's ever tracked. pointerup/pointercancel are only
     * ever listened for while a candidate is actually armed, and are removed the instant it
     * resolves one way or the other, so a release outside every tile (which would otherwise never
     * reach a tile-scoped listener) still reliably clears it -- no stale candidate can survive to
     * be accidentally consumed by a later, unrelated pointer gesture that happens to reuse the
     * same pointerId (as a mouse's pointerId typically does across separate clicks/drags).
     * onClickFallbackPointerDown also unconditionally drops any leftover candidate before
     * possibly arming a new one, as a second, independent guard against the same thing. Does not
     * use setPointerCapture(): that can itself change native click-targeting semantics, which is
     * exactly what this fallback must never interfere with. Deliberately pointerdown/pointerup/
     * pointercancel only -- this module is DOM/layout only (see file banner); it never tracks an
     * in-progress pointer path (that belongs to workspace-drag.js), it only ever compares a
     * gesture's start and end points. */
    let clickFallbackCandidate = null;

    /** Chromium leaves stale hit-test state on a tile that a paint moves or recreates -- an
     * existing Secondary leaf nested into a brand-new split container by
     * renderTreeNode/insertBefore, or a tile pruned by Hide Split and rebuilt from scratch by
     * Show Split: mousedown/pointerup keep targeting the tile's buttons correctly, but the
     * browser silently never synthesizes the follow-up `click` -- so the tile's own header
     * buttons (pane menu, close) go dead on the next real click. Reparenting the exact same
     * element is not the only trigger (a freshly rebuilt tile, with no prior DOM identity to have
     * been "reparented" from, exhibits it too).
     *
     * This does not try to pre-emptively "fix" that browser-internal state (extensive testing
     * found no way to do that both reliably and safely). Instead it treats the symptom directly
     * and safely: only a genuine, complete click-like gesture on ONE of the two supported header
     * buttons (pane menu, close) ever arms the fallback -- tracked from pointerdown (button,
     * pointerId, start position), cleared on pointercancel, and only actually dispatched from
     * pointerup once that same candidate is confirmed intact: same pointerId, the release point
     * is still over that SAME button (not just anywhere within the click threshold -- a pointer
     * that lifts a few px outside the button it went down on is not a click on that button, even
     * if it stayed close by), and that button is still connected and enabled. This deliberately
     * does NOT arm on an arbitrary button inside a tile, and deliberately does NOT arm when the
     * gesture started elsewhere (e.g. the `.prks-tile-header__grip` drag handle) -- a pane drag
     * that happens to release over Close or Pane actions therefore can never trigger this
     * fallback, since its pointerdown target was never one of these two buttons in the first
     * place (workspace-drag.js only ever arms a drag from the grip or the tab strip, never from
     * these header buttons, so a gesture that starts on one of them is never hijacked into a drag
     * either). On pointerup, verify within the very next tick that the real `click` this browser
     * owes that gesture actually showed up; if it didn't, dispatch it by calling `.click()` on the
     * pressed button ourselves. `.click()` synthesizes a proper click through the normal DOM path
     * without depending on the browser's native hit-test pipeline at all, so it fires regardless
     * of that pipeline's stale state. A button whose native click already works incurs no
     * double-fire, since the synthesized click is skipped whenever the real one already landed. */
    function clearClickFallbackCandidate() {
        if (!clickFallbackCandidate) return;
        clickFallbackCandidate = null;
        const d = doc();
        if (!d) return;
        d.removeEventListener('pointerup', onClickFallbackPointerUp, true);
        d.removeEventListener('pointercancel', onClickFallbackPointerCancel, true);
    }

    function onClickFallbackPointerDown(ev) {
        /* A pointerdown always starts a fresh gesture for this pointerId -- any previous
         * candidate is necessarily left over from an already-finished gesture, so drop it
         * unconditionally before possibly arming a new one (belt-and-suspenders alongside the
         * pointerup/pointercancel-while-armed tracking above). */
        clearClickFallbackCandidate();
        if (ev.button !== 0 || (typeof ev.isPrimary === 'boolean' && !ev.isPrimary)) return;
        const target =
            ev.target && typeof ev.target.closest === 'function' ? ev.target.closest(CLICK_FALLBACK_SELECTOR) : null;
        if (!target || target.disabled) return;
        clickFallbackCandidate = {
            pointerId: ev.pointerId,
            button: target,
            startX: ev.clientX,
            startY: ev.clientY,
        };
        const d = doc();
        if (!d) return;
        d.addEventListener('pointerup', onClickFallbackPointerUp, true);
        d.addEventListener('pointercancel', onClickFallbackPointerCancel, true);
    }

    function onClickFallbackPointerCancel(ev) {
        const candidate = clickFallbackCandidate;
        if (!candidate || ev.pointerId !== candidate.pointerId) return;
        clearClickFallbackCandidate();
    }

    function onClickFallbackPointerUp(ev) {
        const candidate = clickFallbackCandidate;
        if (!candidate || ev.pointerId !== candidate.pointerId) return;
        clearClickFallbackCandidate();
        const releasedButton =
            ev.target && typeof ev.target.closest === 'function' ? ev.target.closest(CLICK_FALLBACK_SELECTOR) : null;
        if (releasedButton !== candidate.button) return;
        const dx = ev.clientX - candidate.startX;
        const dy = ev.clientY - candidate.startY;
        if (Math.hypot(dx, dy) > CLICK_FALLBACK_THRESHOLD_PX) return;
        const target = candidate.button;
        const d = doc();
        if (!target || typeof target.click !== 'function' || target.disabled || !d || !d.contains(target)) return;
        let clicked = false;
        const onClick = function () {
            clicked = true;
        };
        target.addEventListener('click', onClick, true);
        root.setTimeout(function () {
            target.removeEventListener('click', onClick, true);
            if (!clicked && d.contains(target) && !target.disabled) target.click();
        }, 0);
    }

    function tabIdFromEvent(ev) {
        if (!ev || !ev.target || !ev.target.closest) return null;
        const tile = ev.target.closest('.prks-tile[data-prks-tab-id]');
        if (!tile) return null;
        return tile.getAttribute('data-prks-tab-id');
    }

    function onPointerDownCapture(ev) {
        const tabId = tabIdFromEvent(ev);
        if (!tabId) return;
        if (
            ev.target &&
            ev.target.closest &&
            ev.target.closest('.prks-tile-header__menu, .prks-tile-header__close')
        ) {
            return;
        }
        if (typeof root.prksWorkspaceFocusTab === 'function') {
            root.prksWorkspaceFocusTab(tabId);
        }
    }

    function onFocusIn(ev) {
        const tabId = tabIdFromEvent(ev);
        if (!tabId) return;
        if (typeof root.prksWorkspaceFocusTab === 'function') {
            root.prksWorkspaceFocusTab(tabId);
        }
    }

    function applyNarrow(canvas) {
        if (!canvas) return;
        if (canvas.clientWidth <= 0) return;
        const narrow = prksWorkspaceWidthIsNarrow(canvas.clientWidth);
        if (narrow === lastNarrow && pendingNarrow === null) return;
        if (pendingNarrow === narrow) return;
        if (typeof root.prksWorkspaceSetNarrowFallback !== 'function') {
            lastNarrow = narrow;
            pendingNarrow = null;
            return;
        }
        /* Drag-and-drop workspace management #2/#18/#19: this is the actual physical
         * wide<->narrow transition (guarded by the early returns above, not every
         * ResizeObserver callback) -- an active spatial pane drag's overlays/preview reference
         * geometry that is about to change or disappear, so cancel it first, before the
         * existing narrow leave-preflight logic below even starts. The drag controller never
         * touches narrow-fallback state itself; this is the one integration point going the
         * other direction. */
        if (typeof root.prksWorkspaceCancelActiveDrag === 'function') {
            root.prksWorkspaceCancelActiveDrag();
        }
        pendingNarrow = narrow;
        Promise.resolve(root.prksWorkspaceSetNarrowFallback(narrow)).then(function () {
            if (pendingNarrow !== narrow) return;
            pendingNarrow = null;
            lastNarrow = narrow;
        });
    }

    function watchCanvas(canvas) {
        if (!canvas || typeof ResizeObserver === 'undefined') return;
        if (observedCanvas === canvas && resizeObserver) return;
        if (resizeObserver) {
            try {
                resizeObserver.disconnect();
            } catch (_e) {}
            resizeObserver = null;
        }
        observedCanvas = canvas;
        resizeObserver = new ResizeObserver(function () {
            applyNarrow(canvas);
            if (typeof root.prksWorkspaceReapplySplitRatio === 'function') {
                /* ResizeObserver reports passive geometry changes. Clamp only effective DOM/ARIA;
                 * canonical preference changes exclusively through direct divider input. */
                root.prksWorkspaceReapplySplitRatio(canvas, { commit: false });
            }
        });
        resizeObserver.observe(canvas);
        applyNarrow(canvas);
    }

    function bindFocusLayer() {
        const d = doc();
        if (!d || bound) return;
        bound = true;
        d.addEventListener('pointerdown', onPointerDownCapture, true);
        d.addEventListener('pointerdown', onClickFallbackPointerDown, true);
        d.addEventListener('focusin', onFocusIn);
    }

    function prksWorkspaceCanvasIsNarrow() {
        const canvas =
            typeof document !== 'undefined' && document.querySelector
                ? document.querySelector('.prks-workspace-canvas')
                : null;
        const measured = canvas && canvas.clientWidth > 0 ? canvas.clientWidth : 0;
        const width = measured || (typeof root.innerWidth === 'number' ? root.innerWidth : 0);
        return prksWorkspaceWidthIsNarrow(width);
    }

    function republishNothing() {}

    function prksWorkspaceWatchNestedSplit(splitId, container) {
        watchNestedSplit(splitId, container);
    }

    function prksWorkspaceUnwatchNestedSplit(splitId) {
        disconnectNestedObserver(splitId);
    }

    function prksWorkspaceWatchCanvas(canvas) {
        watchCanvas(canvas);
    }

    function prksWorkspaceInitTiles() {
        bindFocusLayer();
        /* The Vue shell owns the canvas element. Do not create a second one here. */
        const canvas = existingCanvas();
        if (canvas) watchCanvas(canvas);
    }

    const api = {
        prksWorkspaceSyncTiles: republishNothing,
        prksWorkspaceApplyFocus: republishNothing,
        prksWorkspaceInitTiles: prksWorkspaceInitTiles,
        prksWorkspaceWatchNestedSplit: prksWorkspaceWatchNestedSplit,
        prksWorkspaceUnwatchNestedSplit: prksWorkspaceUnwatchNestedSplit,
        prksWorkspaceWatchCanvas: prksWorkspaceWatchCanvas,
        prksWorkspaceCanvasIsNarrow: prksWorkspaceCanvasIsNarrow,
        prksWorkspaceWidthIsNarrow: prksWorkspaceWidthIsNarrow,
        PRKS_WORKSPACE_NARROW_PX: NARROW_PX,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
