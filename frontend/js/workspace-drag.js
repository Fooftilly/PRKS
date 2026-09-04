/**
 * Workspace pointer drag/drop. Transient interaction state only -- never persisted, never a
 * parallel layout model. A drag *previews* intent (insertion marker, edge-zone overlay, empty-
 * Secondary overlay, floating preview chip); only `pointerup` commits, and it commits by calling
 * exactly the same canonical APIs as the existing non-drag workflows:
 *   - tab-strip reorder      -> prksWorkspaceReorderTab (workspace-tabs.js)
 *   - parked tab -> pane     -> prksWorkspaceSplitLeaf   (Split right/down's own state API)
 *   - parked tab -> empty    -> prksWorkspaceTileTab     (same as clicking "Open in split view")
 *   - visible pane move      -> prksWorkspaceMovePane    (one atomic tree transaction, no leave)
 *   - pane -> tab strip park -> prksWorkspaceHideLeaf     (same as "Hide from split", with preflight)
 * `secondaryTree`/`state.tabs` are never mutated while the pointer is merely crossing zones --
 * only on a successful drop. This module owns no canonical state and stores nothing outside its
 * own closure; `pending` below disappears completely once a drag ends or cancels.
 *
 * `prksWorkspaceCancelActiveDrag` (== `cancel()`) is exported specifically so two OTHER modules
 * can defensively end an active drag before a lifecycle event that would otherwise leave stale
 * geometry/overlays behind -- it is always idempotent/a no-op when nothing is active:
 *   - workspace-tiling.js's `applyNarrow()` calls it right before an actual physical wide<->
 *     narrow transition (the real source of truth for PRKS responsive fallback, not a raw
 *     window resize event).
 *   - workspace-tiling.js's `pruneStale()` calls it right before removing any stale tile/split
 *     DOM, in case the node being pruned is the live drag source.
 * This module never calls back into responsive-fallback or canonical mutation APIs from either
 * integration point -- it only tears down its own transient state.
 */
(function (root) {
    'use strict';

    const THRESHOLD_PX = 6;
    const EDGE_BAND = 0.28;
    const AUTOSCROLL_EDGE_PX = 48;
    const AUTOSCROLL_MAX_SPEED = 14;
    const STRIP_HOVER_PAD = 14;

    const EDGE_ZONE_TO_SPLIT = {
        left: { axis: 'left-right', placement: 'first' },
        right: { axis: 'left-right', placement: 'second' },
        above: { axis: 'top-bottom', placement: 'first' },
        below: { axis: 'top-bottom', placement: 'second' },
    };

    const ZONE_LABEL = { left: 'left of', right: 'right of', above: 'above', below: 'below' };

    /* ---- Pure geometry helpers (no DOM). Exported + unit tested independently. ---- */

    /** Which edge band (if any) `(x, y)` falls in within `rect` (`{left,top,width,height}`).
     * Bands are `band` (default 0.28) of the nearer dimension from each edge; the point nearest
     * edge wins deterministically at corners. The center region (all four distances beyond the
     * band) is intentionally "no drop" rather than a guess. Returns 'left' | 'right' | 'above' |
     * 'below' | null. */
    function computeEdgeZone(rect, x, y, band) {
        if (!rect || !rect.width || !rect.height) return null;
        const b = typeof band === 'number' && band > 0 && band < 0.5 ? band : EDGE_BAND;
        const px = x - rect.left;
        const py = y - rect.top;
        if (px < 0 || py < 0 || px > rect.width || py > rect.height) return null;
        const dTop = py / rect.height;
        const dBottom = (rect.height - py) / rect.height;
        const dLeft = px / rect.width;
        const dRight = (rect.width - px) / rect.width;
        const min = Math.min(dTop, dBottom, dLeft, dRight);
        if (min > b) return null;
        if (min === dLeft) return 'left';
        if (min === dRight) return 'right';
        if (min === dTop) return 'above';
        return 'below';
    }

    /** Insertion index for tab-strip reordering: `rects` is an ordered array of
     * `{id, left, right}` for every OTHER tab (the dragged tab already excluded by the caller).
     * Uses each tab's midpoint, matching the spec's "insertion position by tab midpoint
     * geometry". Returns an index into `rects` (0..rects.length); `rects.length` means "at the
     * end". */
    function computeReorderIndex(rects, x) {
        if (!rects || !rects.length) return 0;
        for (let i = 0; i < rects.length; i++) {
            const mid = (rects[i].left + rects[i].right) / 2;
            if (x < mid) return i;
        }
        return rects.length;
    }

    /* ---- DOM-dependent controller. No-op everywhere below if there is no `document`. ---- */

    function doc() {
        return typeof document !== 'undefined' ? document : null;
    }

    /* Single active drag session; `null` whenever nothing is armed or dragging. Never touched by
     * canonical workspace state and never read by it -- workspace-tabs.js/workspace-tree.js have
     * no knowledge this module exists. */
    let pending = null;
    let bound = false;
    /* Outlives `pending` by design: armed only in onPointerUp() for a gesture that actually
     * completed a drag (see onPointerUp below), consumed by the very next capture-phase click
     * (the one that same pointerup synthesizes). A cancelled drag (Escape/pointercancel/
     * lostpointercapture/blur/responsive transition) never arms this, so it never swallows the
     * user's next intentional click. */
    let suppressClickTarget = null;

    function cssEscape(id) {
        if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') return CSS.escape(String(id));
        return String(id).replace(/["\\]/g, '\\$&');
    }

    function announce(text) {
        const el = doc() && doc().getElementById('prks-workspace-live');
        if (!el) return;
        el.textContent = '';
        el.textContent = String(text || '');
    }

    function currentSnapshot() {
        return typeof root.prksWorkspaceSnapshot === 'function' ? root.prksWorkspaceSnapshot() : null;
    }

    function findTab(snap, tabId) {
        if (!snap || !Array.isArray(snap.tabs)) return null;
        for (let i = 0; i < snap.tabs.length; i++) {
            if (snap.tabs[i].id === tabId) return snap.tabs[i];
        }
        return null;
    }

    function titleFor(tabId) {
        const tab = findTab(currentSnapshot(), tabId);
        return (tab && tab.title) || 'page';
    }

    function sourceTab() {
        return pending ? findTab(currentSnapshot(), pending.source.tabId) : null;
    }

    function routeSupportsTile(route) {
        return typeof root.prksRouteSupportsTile === 'function' && !!root.prksRouteSupportsTile(route);
    }

    function isNarrow() {
        return typeof root.prksWorkspaceIsNarrowFallback === 'function' && root.prksWorkspaceIsNarrowFallback();
    }

    function leafIds(snap) {
        return typeof root.collectLeafTabIds === 'function' ? root.collectLeafTabIds(snap.secondaryTree) : [];
    }

    /* ---- Arming / threshold ---- */

    function onPointerDown(e) {
        if (e.button !== 0 || pending) return;
        const target = e.target;
        if (!target || typeof target.closest !== 'function') return;
        const grip = target.closest('.prks-tile-header__grip');
        if (grip) {
            const tile = grip.closest('[data-prks-tab-id]');
            const tabId = tile && tile.getAttribute('data-prks-tab-id');
            if (tabId) arm(e, { kind: 'pane', tabId: tabId }, tile);
            return;
        }
        const tabWrap = target.closest('.prks-workspace-tab');
        if (
            tabWrap &&
            !target.closest('.prks-workspace-tab__close, .prks-workspace-tab__split, .prks-workspace-tab__status')
        ) {
            const tabId = tabWrap.getAttribute('data-tab-id');
            if (tabId) arm(e, { kind: 'tab', tabId: tabId }, tabWrap);
        }
    }

    function arm(e, source, sourceEl) {
        pending = {
            source: source,
            sourceEl: sourceEl,
            pointerId: e.pointerId,
            originX: e.clientX,
            originY: e.clientY,
            active: false,
            target: null,
            previewEl: null,
            rafId: null,
            autoscroll: null,
        };
        const d = doc();
        d.addEventListener('pointermove', onPointerMove);
        d.addEventListener('pointerup', onPointerUp);
        d.addEventListener('pointercancel', onPointerCancelEvt);
        d.addEventListener('lostpointercapture', onLostCapture);
    }

    /** Called when a pointerdown never crossed the movement threshold: a plain click. No
     * listeners beyond the ones `arm()` added were ever created, so this is just their removal
     * -- no preview, overlay, body class, or source styling ever existed to clean up. */
    function disarm() {
        if (!pending) return;
        const d = doc();
        d.removeEventListener('pointermove', onPointerMove);
        d.removeEventListener('pointerup', onPointerUp);
        d.removeEventListener('pointercancel', onPointerCancelEvt);
        d.removeEventListener('lostpointercapture', onLostCapture);
        pending = null;
    }

    function onPointerMove(e) {
        if (!pending || e.pointerId !== pending.pointerId) return;
        if (pending.active && pending.sourceEl && !doc().contains(pending.sourceEl)) {
            cancel();
            return;
        }
        if (!pending.active) {
            const dx = e.clientX - pending.originX;
            const dy = e.clientY - pending.originY;
            if (Math.hypot(dx, dy) < THRESHOLD_PX) return;
            beginDrag(e);
        }
        updateDrag(e);
    }

    /* ---- Begin real drag ---- */

    function beginDrag(e) {
        pending.active = true;
        try {
            pending.sourceEl.setPointerCapture(pending.pointerId);
        } catch (_err) {
            /* Pointer capture is a precision nicety; the drag still works via document-level
             * listeners already bound in arm() if it's unavailable/rejected. */
        }
        const d = doc();
        d.addEventListener('keydown', onKeyDown, true);
        root.addEventListener('blur', onWindowBlur);
        d.body.classList.add('prks-workspace-dragging');
        applySourceStyle(true);
        createPreview();
        pending.lastX = e.clientX;
        pending.lastY = e.clientY;
        updatePreviewPosition(e.clientX, e.clientY);
        /* Click suppression is armed later, only for a completed pointerup gesture (see
         * onPointerUp) -- not here, so a drag cancelled by Escape/pointercancel/lost-capture/
         * blur/responsive-transition never swallows the user's next intentional click. */
        announce('Dragging ' + titleFor(pending.source.tabId) + '.');
    }

    function applySourceStyle(on) {
        if (!pending) return;
        const selector =
            pending.source.kind === 'pane'
                ? '.prks-tile[data-prks-tab-id="' + cssEscape(pending.source.tabId) + '"]'
                : '.prks-workspace-tab[data-tab-id="' + cssEscape(pending.source.tabId) + '"]';
        const el = doc().querySelector(selector);
        if (el) el.classList.toggle('is-drag-source', !!on);
    }

    function createPreview() {
        const d = doc();
        const el = d.createElement('div');
        el.className = 'prks-drag-preview';
        el.setAttribute('aria-hidden', 'true');
        const icon = d.createElement('span');
        icon.className = 'prks-drag-preview__icon';
        const tab = sourceTab();
        if (tab && typeof root.prksIcon === 'function') {
            icon.innerHTML = root.prksIcon(tab.icon || 'file-text', { size: 'sm' });
        }
        const label = d.createElement('span');
        label.className = 'prks-drag-preview__label';
        label.textContent = (tab && tab.title) || 'Page';
        el.appendChild(icon);
        el.appendChild(label);
        d.body.appendChild(el);
        if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(el);
        pending.previewEl = el;
    }

    function updatePreviewPosition(x, y) {
        if (!pending || !pending.previewEl) return;
        pending.previewEl.style.transform = 'translate(' + Math.round(x + 14) + 'px, ' + Math.round(y + 10) + 'px)';
    }

    /* ---- Live target computation (preview only -- never mutates canonical state) ---- */

    function updateDrag(e) {
        pending.lastX = e.clientX;
        pending.lastY = e.clientY;
        updatePreviewPosition(e.clientX, e.clientY);
        runAutoscroll(e.clientX, e.clientY);
        refreshTargetFromLastPointer();
    }

    /** Recomputes the semantic drop target from the most recently known pointer position and
     * updates preview visuals/announcements if it changed. Called both from ordinary pointer
     * movement and from each autoscroll animation frame (spec #5): while the strip scrolls
     * underneath a stationary pointer, the tab positions -- and therefore the insertion index --
     * change even though no pointermove fires. Never mutates canonical state; only preview. */
    function refreshTargetFromLastPointer() {
        if (!pending) return;
        const nextTarget = computeTarget(pending.lastX, pending.lastY);
        if (!sameTarget(nextTarget, pending.target)) {
            pending.target = nextTarget;
            renderTargetVisuals(nextTarget);
            announceTargetChange(nextTarget);
        }
    }

    function sameTarget(a, b) {
        if (a === b) return true;
        if (!a || !b) return false;
        return (
            a.kind === b.kind &&
            a.tabId === b.tabId &&
            a.beforeTabId === b.beforeTabId &&
            a.zone === b.zone &&
            a.valid === b.valid
        );
    }

    function computeTarget(x, y) {
        const stripTarget = computeTabStripTarget(x, y);
        if (stripTarget) return stripTarget;
        return computeSpatialTarget(x, y);
    }

    function computeTabStripTarget(x, y) {
        const list = doc().getElementById('prks-workspace-tabs');
        if (!list) return null;
        const rect = list.getBoundingClientRect();
        if (
            x < rect.left - STRIP_HOVER_PAD ||
            x > rect.right + STRIP_HOVER_PAD ||
            y < rect.top - STRIP_HOVER_PAD ||
            y > rect.bottom + STRIP_HOVER_PAD
        ) {
            return null;
        }
        /* A pane dragged by its handle onto the strip parks it (spec #22-24) -- distinct from an
         * ordinary tab reorder, which is any global tab (Main/Secondary/parked alike) dragged
         * within the strip (spec #5, #9, #24). */
        if (pending.source.kind === 'pane') return { kind: 'park' };
        const wraps = Array.prototype.slice.call(list.querySelectorAll('.prks-workspace-tab'));
        const rects = [];
        for (let i = 0; i < wraps.length; i++) {
            const id = wraps[i].getAttribute('data-tab-id');
            if (!id || id === pending.source.tabId) continue;
            const r = wraps[i].getBoundingClientRect();
            rects.push({ id: id, left: r.left, right: r.right });
        }
        const idx = computeReorderIndex(rects, x);
        return { kind: 'tab-reorder', beforeTabId: rects[idx] ? rects[idx].id : null, index: idx };
    }

    function computeSpatialTarget(x, y) {
        if (isNarrow()) return null;
        const snap = currentSnapshot();
        if (!snap) return null;
        /* Main is never spatially draggable into secondaryTree (spec #8, #26): no valid target
         * regardless of which pane the pointer is over. */
        if (pending.source.tabId === snap.mainTabId) return null;
        const ids = leafIds(snap);
        const isMove = ids.indexOf(pending.source.tabId) !== -1;
        for (let i = 0; i < ids.length; i++) {
            const leafId = ids[i];
            /* Self-drop is invalid (spec #15): simply never offer a target over the leaf being
             * dragged, rather than special-casing it later. */
            if (leafId === pending.source.tabId) continue;
            const tile = doc().querySelector('.prks-tile[data-prks-tab-id="' + cssEscape(leafId) + '"]');
            if (!tile) continue;
            const rect = tile.getBoundingClientRect();
            if (x < rect.left || x > rect.right || y < rect.top || y > rect.bottom) continue;
            const zone = computeEdgeZone(rect, x, y);
            if (!zone) return null; /* center of a pane: deterministic no-drop, not a guess */
            const map = EDGE_ZONE_TO_SPLIT[zone];
            const capped = !isMove && typeof root.prksWorkspaceCanAddSecondaryLeaf === 'function' && !root.prksWorkspaceCanAddSecondaryLeaf();
            const ineligibleRoute = !isMove && !routeSupportsTile(sourceRoute());
            return {
                kind: 'secondary-edge',
                tabId: leafId,
                axis: map.axis,
                placement: map.placement,
                zone: zone,
                valid: !capped && !ineligibleRoute,
                reason: capped ? 'cap' : ineligibleRoute ? 'route' : null,
            };
        }
        /* First-Secondary drop (spec #16): only offered while there is no Secondary tree at all,
         * and only for a parked, tile-eligible source. */
        if (!snap.secondaryTree && pending.source.kind === 'tab' && routeSupportsTile(sourceRoute())) {
            const canvas = doc().querySelector('.prks-workspace-canvas');
            if (canvas) {
                const rect = canvas.getBoundingClientRect();
                const zoneStart = rect.left + rect.width * 0.6;
                if (x >= zoneStart && x <= rect.right && y >= rect.top && y <= rect.bottom) {
                    return { kind: 'secondary-empty', valid: true };
                }
            }
        }
        return null;
    }

    function sourceRoute() {
        const tab = sourceTab();
        return tab ? tab.route : null;
    }

    /* ---- Visual feedback (restrained; only exists during an active drag) ---- */

    function renderTargetVisuals(target) {
        clearTargetVisuals();
        if (!target) return;
        if (target.kind === 'tab-reorder') {
            showInsertionMarker(target);
            return;
        }
        if (target.kind === 'park') {
            const list = doc().getElementById('prks-workspace-tabs');
            if (list) list.classList.add('is-drop-target-park');
            return;
        }
        if (target.kind === 'secondary-empty') {
            showEmptySecondaryOverlay();
            return;
        }
        if (target.kind === 'secondary-edge') {
            showEdgeOverlay(target);
        }
    }

    function clearTargetVisuals() {
        const d = doc();
        const marker = d.getElementById('prks-drag-insertion-marker');
        if (marker) marker.remove();
        const list = d.getElementById('prks-workspace-tabs');
        if (list) list.classList.remove('is-drop-target-park');
        const edge = d.getElementById('prks-drag-edge-overlay');
        if (edge) edge.remove();
        const empty = d.getElementById('prks-drag-empty-overlay');
        if (empty) empty.remove();
    }

    function showInsertionMarker(target) {
        const list = doc().getElementById('prks-workspace-tabs');
        if (!list) return;
        const marker = doc().createElement('div');
        marker.id = 'prks-drag-insertion-marker';
        marker.className = 'prks-drag-insertion-marker';
        marker.setAttribute('aria-hidden', 'true');
        let refWrap = null;
        if (target.beforeTabId) {
            refWrap = list.querySelector('.prks-workspace-tab[data-tab-id="' + cssEscape(target.beforeTabId) + '"]');
        }
        if (refWrap) list.insertBefore(marker, refWrap);
        else list.appendChild(marker);
    }

    function bandRectFor(rect, zone) {
        if (zone === 'left') return { left: rect.left, top: rect.top, width: rect.width / 2, height: rect.height };
        if (zone === 'right') {
            return { left: rect.left + rect.width / 2, top: rect.top, width: rect.width / 2, height: rect.height };
        }
        if (zone === 'above') return { left: rect.left, top: rect.top, width: rect.width, height: rect.height / 2 };
        return { left: rect.left, top: rect.top + rect.height / 2, width: rect.width, height: rect.height / 2 };
    }

    function showEdgeOverlay(target) {
        const tile = doc().querySelector('.prks-tile[data-prks-tab-id="' + cssEscape(target.tabId) + '"]');
        if (!tile) return;
        const rect = bandRectFor(tile.getBoundingClientRect(), target.zone);
        const overlay = doc().createElement('div');
        overlay.id = 'prks-drag-edge-overlay';
        overlay.className = 'prks-drag-edge-overlay' + (target.valid ? '' : ' is-invalid');
        overlay.setAttribute('aria-hidden', 'true');
        overlay.style.left = Math.round(rect.left) + 'px';
        overlay.style.top = Math.round(rect.top) + 'px';
        overlay.style.width = Math.round(rect.width) + 'px';
        overlay.style.height = Math.round(rect.height) + 'px';
        doc().body.appendChild(overlay);
    }

    function showEmptySecondaryOverlay() {
        const canvas = doc().querySelector('.prks-workspace-canvas');
        if (!canvas) return;
        const rect = canvas.getBoundingClientRect();
        const overlay = doc().createElement('div');
        overlay.id = 'prks-drag-empty-overlay';
        overlay.className = 'prks-drag-empty-overlay';
        overlay.setAttribute('aria-hidden', 'true');
        overlay.textContent = 'Open in split view';
        overlay.style.left = Math.round(rect.left + rect.width * 0.6) + 'px';
        overlay.style.top = Math.round(rect.top) + 'px';
        overlay.style.width = Math.round(rect.width * 0.4) + 'px';
        overlay.style.height = Math.round(rect.height) + 'px';
        doc().body.appendChild(overlay);
    }

    function announceTargetChange(target) {
        if (!target) {
            announce('No drop target.');
            return;
        }
        if (target.kind === 'tab-reorder') {
            announce(target.beforeTabId ? 'Move tab before ' + titleFor(target.beforeTabId) + '.' : 'Move tab to the end.');
            return;
        }
        if (target.kind === 'park') {
            announce('Park pane.');
            return;
        }
        if (target.kind === 'secondary-empty') {
            announce('Open in split view.');
            return;
        }
        if (target.kind === 'secondary-edge') {
            if (!target.valid) {
                announce(target.reason === 'cap' ? 'Maximum of 4 visible panes.' : 'This page cannot be split.');
                return;
            }
            announce('Split ' + ZONE_LABEL[target.zone] + ' ' + titleFor(target.tabId) + '.');
        }
    }

    /* ---- Tab-strip edge autoscroll ---- */

    function runAutoscroll(x, y) {
        /* A pane's grip only ever targets the tab strip as a Park drop (spec #4): its insertion
         * position within the strip is irrelevant, so scrolling the strip while hovering near an
         * edge would be a surprising side effect with no purpose. Tab-source dragging (ordinary
         * reorder) still autoscrolls; pane-to-pane spatial movement never touches the strip at
         * all and is unaffected either way. */
        if (pending.source.kind === 'pane') {
            stopAutoscroll();
            return;
        }
        const list = doc().getElementById('prks-workspace-tabs');
        if (!list || list.scrollWidth <= list.clientWidth) {
            stopAutoscroll();
            return;
        }
        const rect = list.getBoundingClientRect();
        if (y < rect.top - 20 || y > rect.bottom + 20) {
            stopAutoscroll();
            return;
        }
        let speed = 0;
        if (x < rect.left + AUTOSCROLL_EDGE_PX) {
            speed = -AUTOSCROLL_MAX_SPEED * (1 - Math.max(0, x - rect.left) / AUTOSCROLL_EDGE_PX);
        } else if (x > rect.right - AUTOSCROLL_EDGE_PX) {
            speed = AUTOSCROLL_MAX_SPEED * (1 - Math.max(0, rect.right - x) / AUTOSCROLL_EDGE_PX);
        }
        if (!speed) {
            stopAutoscroll();
            return;
        }
        pending.autoscroll = { list: list, speed: speed };
        if (!pending.rafId) startAutoscrollLoop();
    }

    function startAutoscrollLoop() {
        function step() {
            if (!pending || !pending.autoscroll) {
                if (pending) pending.rafId = null;
                return;
            }
            pending.autoscroll.list.scrollLeft += pending.autoscroll.speed;
            /* Spec #5: the tabs move underneath a pointer that hasn't itself moved, so the
             * semantic target (insertion index) must be recomputed here, not only on the next
             * pointermove. Tab order itself is still untouched -- preview only. */
            refreshTargetFromLastPointer();
            pending.rafId = root.requestAnimationFrame(step);
        }
        pending.rafId = root.requestAnimationFrame(step);
    }

    function stopAutoscroll() {
        if (!pending) return;
        pending.autoscroll = null;
    }

    /* ---- Commit / cancel / cleanup ---- */

    function onPointerUp(e) {
        if (!pending || e.pointerId !== pending.pointerId) return;
        if (!pending.active) {
            disarm();
            return;
        }
        const finalTarget = computeTarget(e.clientX, e.clientY);
        const source = pending.source;
        /* Click suppression is armed here and only here (spec #4): this is the one path where
         * the browser is about to synthesize a click for the same gesture that just committed a
         * real drag. Every other exit (Escape/pointercancel/lost-capture/blur/responsive
         * cancellation) goes through cancel() below, which explicitly leaves this cleared, so a
         * cancelled drag never swallows the user's next intentional click on the source. */
        suppressClickTarget = pending.sourceEl;
        cleanup();
        commit(source, finalTarget);
    }

    function onPointerCancelEvt(e) {
        if (!pending || e.pointerId !== pending.pointerId) return;
        cancel();
    }

    function onLostCapture(e) {
        if (!pending || e.pointerId !== pending.pointerId || !pending.active) return;
        cancel();
    }

    function onKeyDown(e) {
        if (e.key !== 'Escape' || !pending || !pending.active) return;
        e.preventDefault();
        cancel();
    }

    function onWindowBlur() {
        if (pending && pending.active) cancel();
    }

    /** Cancels an in-progress drag (Escape/pointercancel/lost-capture/blur/responsive
     * transition/external tile removal/destroy): workspace state is left completely untouched,
     * and every transient visual/listener this module created is removed. Safe to call when
     * nothing is active -- callers (workspace-tiling.js's narrow-fallback transition and stale-
     * tile pruning) invoke this defensively and unconditionally. Responsive-width cancellation
     * is triggered by workspace-tiling.js's own ResizeObserver-driven narrow-fallback transition
     * (the actual source of truth for PRKS's responsive layout), not by a raw window resize
     * event here -- this module never mutates that responsive state itself. */
    function cancel() {
        if (!pending) return;
        const wasActive = pending.active;
        const srcEl = pending.sourceEl;
        cleanup();
        /* Cancellation never completes a gesture, so it never suppresses a future click either
         * (spec #4) -- clear defensively even though the active-drag paths above no longer set
         * this in the first place. */
        suppressClickTarget = null;
        if (wasActive) {
            announce('Move cancelled.');
            if (srcEl && typeof srcEl.focus === 'function' && doc().contains(srcEl)) {
                try {
                    srcEl.focus({ preventScroll: true });
                } catch (_e) {}
            }
        }
    }

    /** One idempotent cleanup for every piece of transient drag state: pointer capture,
     * document/window listeners, preview, overlays, insertion marker, autoscroll loop, source
     * styling, body drag class. Safe to call more than once (e.g. cancel() then a defensive
     * cleanup elsewhere) -- every step already checks what actually exists. */
    function cleanup() {
        if (!pending) return;
        const d = doc();
        if (pending.rafId) {
            root.cancelAnimationFrame(pending.rafId);
            pending.rafId = null;
        }
        pending.autoscroll = null;
        try {
            if (pending.sourceEl && pending.sourceEl.releasePointerCapture) {
                pending.sourceEl.releasePointerCapture(pending.pointerId);
            }
        } catch (_e) {}
        d.removeEventListener('pointermove', onPointerMove);
        d.removeEventListener('pointerup', onPointerUp);
        d.removeEventListener('pointercancel', onPointerCancelEvt);
        d.removeEventListener('lostpointercapture', onLostCapture);
        d.removeEventListener('keydown', onKeyDown, true);
        root.removeEventListener('blur', onWindowBlur);
        d.body.classList.remove('prks-workspace-dragging');
        applySourceStyle(false);
        if (pending.previewEl && pending.previewEl.parentNode) pending.previewEl.parentNode.removeChild(pending.previewEl);
        clearTargetVisuals();
        pending = null;
    }

    /* ---- Commit: the ONLY place canonical state is touched, and only once. ---- */

    function commit(source, target) {
        if (!target) {
            announce('Move cancelled.');
            return;
        }
        if (target.kind === 'tab-reorder') {
            const ok = typeof root.prksWorkspaceReorderTab === 'function' && root.prksWorkspaceReorderTab(source.tabId, target.beforeTabId);
            announce(ok ? titleFor(source.tabId) + ' moved.' : 'Move cancelled.');
            return;
        }
        if (target.kind === 'park') {
            if (source.kind !== 'pane' || typeof root.prksWorkspaceHideLeaf !== 'function') {
                announce('Move cancelled.');
                return;
            }
            Promise.resolve(root.prksWorkspaceHideLeaf(source.tabId)).then(function (ok) {
                announce(ok ? titleFor(source.tabId) + ' parked.' : 'Move cancelled.');
            });
            return;
        }
        if (target.kind === 'secondary-empty') {
            if (source.kind !== 'tab' || typeof root.prksWorkspaceTileTab !== 'function') {
                announce('Move cancelled.');
                return;
            }
            Promise.resolve(root.prksWorkspaceTileTab(source.tabId)).then(function (ok) {
                announce(ok ? titleFor(source.tabId) + ' opened in split view.' : 'Move cancelled.');
            });
            return;
        }
        if (target.kind === 'secondary-edge') {
            if (!target.valid) {
                announce('Move cancelled.');
                return;
            }
            const snap = currentSnapshot();
            const isMove = snap && leafIds(snap).indexOf(source.tabId) !== -1;
            if (isMove) {
                const ok = typeof root.prksWorkspaceMovePane === 'function' && root.prksWorkspaceMovePane(source.tabId, target.tabId, target.axis, target.placement);
                announce(ok ? titleFor(source.tabId) + ' moved.' : 'Move cancelled.');
                return;
            }
            if (typeof root.prksWorkspaceSplitLeaf !== 'function') {
                announce('Move cancelled.');
                return;
            }
            Promise.resolve(
                root.prksWorkspaceSplitLeaf(target.tabId, target.axis, { tabId: source.tabId, placement: target.placement })
            ).then(function (ok) {
                announce(ok ? titleFor(source.tabId) + ' added to split view.' : 'Move cancelled.');
            });
            return;
        }
        announce('Move cancelled.');
    }

    /* ---- Click suppression: the pointerup-synthesized click after a real drag is not a click. ---- */

    function onGlobalClickCapture(e) {
        if (!suppressClickTarget) return;
        const target = suppressClickTarget;
        suppressClickTarget = null;
        if (target === e.target || (target.contains && target.contains(e.target))) {
            e.preventDefault();
            e.stopPropagation();
        }
    }

    /* ---- Init ---- */

    function prksWorkspaceInitDrag() {
        if (bound || typeof document === 'undefined') return;
        bound = true;
        document.addEventListener('pointerdown', onPointerDown);
        document.addEventListener('click', onGlobalClickCapture, true);
    }

    const api = {
        prksWorkspaceInitDrag: prksWorkspaceInitDrag,
        prksWorkspaceCancelActiveDrag: cancel,
        prksWorkspaceComputeEdgeZone: computeEdgeZone,
        prksWorkspaceComputeTabReorderIndex: computeReorderIndex,
    };
    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
