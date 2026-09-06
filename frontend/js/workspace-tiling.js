/**
 * Workspace tile DOM/layout only. Canonical workspace state (including the recursive
 * Secondary `secondaryTree`) lives in workspace-tabs.js. This module recursively
 * reconciles that tree into DOM: one stable tile host per visible leaf (keyed by
 * `data-prks-tab-id`) and one `.prks-workspace-split` container per internal split
 * node (keyed by `data-prks-split-id`), reusing/reparenting existing nodes rather
 * than destroying and recreating them so TabContexts/PDF/EasyMDE runtimes never
 * remount because of an unrelated tree mutation.
 */
(function (root) {
    'use strict';

    const NARROW_PX = 720;
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

    function iconHtml(name) {
        if (typeof root.prksIcon === 'function') {
            return root.prksIcon(name, { size: 'sm', className: 'prks-tile-header__icon-svg' });
        }
        return '';
    }

    function pageContent() {
        const d = doc();
        return d ? d.getElementById('page-content') : null;
    }

    function ensureCanvas() {
        const host = pageContent();
        if (!host) return null;
        let canvas = host.querySelector(':scope > .prks-workspace-canvas');
        if (!canvas) {
            canvas = host.querySelector('.prks-workspace-canvas');
        }
        if (!canvas) {
            canvas = doc().createElement('div');
            canvas.className = 'prks-workspace-canvas prks-workspace-canvas--stacked';
            host.appendChild(canvas);
        }
        watchCanvas(canvas);
        return canvas;
    }

    /* ---------- Whole-subtree DOM lookup (direct-children recursion; no querySelector
     * dependency, so this stays correct once nested split containers exist). ---------- */
    function findInSubtree(node, matches) {
        if (!node) return null;
        if (matches(node)) return node;
        const kids = node.children;
        if (!kids) return null;
        for (let i = 0; i < kids.length; i++) {
            const found = findInSubtree(kids[i], matches);
            if (found) return found;
        }
        return null;
    }

    function isTileFor(tabId) {
        return function (node) {
            return !!(node.getAttribute && node.getAttribute('data-prks-tab-id') === tabId);
        };
    }

    function isSplitContainerFor(splitId) {
        return function (node) {
            return !!(
                node.getAttribute &&
                node.getAttribute('data-prks-split-id') === splitId &&
                node.className &&
                node.className.indexOf('prks-workspace-split') !== -1
            );
        };
    }

    function isAnyTile(node) {
        return !!(node.getAttribute && node.getAttribute('data-prks-tab-id'));
    }

    function isAnySplitContainer(node) {
        return !!(node.getAttribute && node.getAttribute('data-prks-split-id') && node.className && node.className.indexOf('prks-workspace-split') !== -1);
    }

    function findTile(canvas, tabId) {
        if (!canvas || !tabId) return null;
        return findInSubtree(canvas, isTileFor(tabId));
    }

    function findSplitContainer(canvas, splitId) {
        if (!canvas || !splitId) return null;
        return findInSubtree(canvas, isSplitContainerFor(splitId));
    }

    function collectAll(canvas, matches) {
        const out = [];
        (function walk(node) {
            if (!node) return;
            if (node !== canvas && matches(node)) out.push(node);
            const kids = node.children;
            if (!kids) return;
            for (let i = 0; i < kids.length; i++) walk(kids[i]);
        })(canvas);
        return out;
    }

    function headerTitle(snap, tabId) {
        if (!snap || !Array.isArray(snap.tabs)) return 'Page';
        for (let i = 0; i < snap.tabs.length; i++) {
            if (snap.tabs[i].id === tabId) return snap.tabs[i].title || 'Page';
        }
        return 'Page';
    }

    function headerIcon(snap, tabId) {
        if (!snap || !Array.isArray(snap.tabs)) return 'file-text';
        for (let i = 0; i < snap.tabs.length; i++) {
            if (snap.tabs[i].id === tabId) return snap.tabs[i].icon || 'file-text';
        }
        return 'file-text';
    }

    function mainPaneLabel(snap, tabId) {
        return 'Main pane: ' + headerTitle(snap, tabId);
    }

    function fillHeader(header, snap, tabId, role, visualTiled) {
        const token = (visualTiled ? role : 'stacked') + ':' + String(tabId);
        if (header.getAttribute('data-prks-header') !== token) {
            header.setAttribute('data-prks-header', token);
            header.replaceChildren();
            if (!visualTiled) {
                header.hidden = true;
                header.removeAttribute('aria-label');
                return;
            }
            header.hidden = false;

            if (role !== 'main') {
                /* Restrained drag grip (spec: pane drag starts only from this handle, never from
                 * the tile body/PDF/Research Notes/links/buttons/text/scrollbars). Not a
                 * keyboard-operable control -- it has no click behavior of its own -- so it's
                 * hidden from the accessibility tree; the equivalent non-drag operations
                 * ("Hide from split", "Move tab left/right") stay reachable via the tab/pane
                 * menus for keyboard and screen-reader users. workspace-drag.js binds pointerdown
                 * on this element by class, not by wiring a listener per-header here. */
                const grip = doc().createElement('button');
                grip.type = 'button';
                grip.className = 'prks-tile-header__grip';
                grip.tabIndex = -1;
                grip.setAttribute('aria-hidden', 'true');
                grip.title = 'Drag to move or park this pane';
                grip.innerHTML = iconHtml('grip-vertical');
                header.appendChild(grip);
            }

            const icon = doc().createElement('span');
            icon.className = 'prks-tile-header__icon';
            icon.setAttribute('aria-hidden', 'true');
            icon.setAttribute('data-icon', headerIcon(snap, tabId));
            icon.innerHTML = iconHtml(headerIcon(snap, tabId));

            const title = doc().createElement('span');
            title.className = 'prks-tile-header__title';
            title.textContent = headerTitle(snap, tabId);

            header.appendChild(icon);
            header.appendChild(title);

            if (role === 'main') {
                header.setAttribute('aria-label', mainPaneLabel(snap, tabId));
            } else {
                header.removeAttribute('aria-label');
                const actions = doc().createElement('span');
                actions.className = 'prks-tile-header__actions';
                const more = doc().createElement('button');
                more.type = 'button';
                more.className = 'prks-icon-btn prks-icon-btn--ghost prks-tile-header__menu';
                more.setAttribute('aria-label', 'Pane actions');
                more.title = 'Pane actions';
                more.setAttribute('aria-haspopup', 'menu');
                more.setAttribute('aria-expanded', 'false');
                more.innerHTML = iconHtml('ellipsis');
                more.addEventListener('click', function (e) {
                    e.preventDefault();
                    e.stopPropagation();
                    if (typeof root.prksWorkspaceOpenTabMenu === 'function') {
                        root.prksWorkspaceOpenTabMenu(tabId, e, more);
                    }
                });
                const close = doc().createElement('button');
                close.type = 'button';
                close.className = 'prks-icon-btn prks-icon-btn--ghost prks-tile-header__close';
                close.setAttribute('aria-label', 'Close');
                close.title = 'Close';
                close.innerHTML = iconHtml('x');
                close.addEventListener('click', function (e) {
                    e.preventDefault();
                    e.stopPropagation();
                    if (typeof root.prksWorkspaceCloseTab === 'function') {
                        void root.prksWorkspaceCloseTab(tabId);
                    }
                });
                actions.appendChild(more);
                actions.appendChild(close);
                header.appendChild(actions);
            }
            if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(header);
            return;
        }
        header.hidden = !visualTiled;
        if (!visualTiled) {
            header.removeAttribute('aria-label');
            return;
        }
        const titleText = headerTitle(snap, tabId);
        const titleEl = header.querySelector('.prks-tile-header__title');
        if (titleEl) titleEl.textContent = titleText;
        if (role === 'main') header.setAttribute('aria-label', mainPaneLabel(snap, tabId));
        else header.removeAttribute('aria-label');
        const iconEl = header.querySelector('.prks-tile-header__icon');
        const wantIcon = headerIcon(snap, tabId);
        if (iconEl && iconEl.getAttribute('data-icon') !== wantIcon) {
            iconEl.setAttribute('data-icon', wantIcon);
            iconEl.innerHTML = iconHtml(wantIcon);
            if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(iconEl);
        }
    }

    function createTile(tabId) {
        const section = doc().createElement('section');
        section.className = 'prks-tile';
        section.setAttribute('data-prks-tab-id', tabId);
        section.setAttribute('tabindex', '-1');

        const header = doc().createElement('header');
        header.className = 'prks-tile-header';
        header.hidden = true;

        const body = doc().createElement('div');
        body.className = 'prks-tile__body';

        section.appendChild(header);
        section.appendChild(body);
        return section;
    }

    /** Finds the existing tile for `tabId` anywhere under `canvas`, or creates one (unattached
     * -- the caller places it: either directly under canvas for Main, or as a split-container
     * child while walking the Secondary tree). */
    function ensureTile(canvas, tabId) {
        if (!canvas || !tabId) return null;
        let tile = findTile(canvas, tabId);
        if (!tile) tile = createTile(tabId);
        return tile;
    }

    function ensureSplitContainer(canvas, splitId) {
        let el = findSplitContainer(canvas, splitId);
        if (!el) {
            el = doc().createElement('div');
            el.className = 'prks-workspace-split';
            el.setAttribute('data-prks-split-id', splitId);
        }
        return el;
    }

    function tileBody(tile) {
        if (!tile) return null;
        return tile.querySelector(':scope > .prks-tile__body') || tile.querySelector('.prks-tile__body');
    }

    function prksWorkspaceHostForTab(tabId) {
        if (!tabId) return null;
        const canvas = ensureCanvas();
        if (!canvas) return null;
        let tile = ensureTile(canvas, tabId);
        if (tile.parentNode !== canvas && !tile.parentNode) {
            /* Defensive fallback only: normal callers run a full syncTiles paint (which
             * positions every tile correctly) before requesting a host. */
            canvas.appendChild(tile);
        }
        return tileBody(tile);
    }

    function applyTileClasses(tile, snap, tabId, visualTiled) {
        if (!tile || !snap) return;
        const isMain = tabId === snap.mainTabId;
        const isFocused = tabId === snap.focusedTabId;
        /* Toggle only the flag classes this function owns -- an ordinary reconciling paint must
         * never clobber an unrelated transient class an external module applied directly to
         * this same, reused tile node (e.g. workspace-drag.js's `is-drag-source` while this pane
         * is the live drag source). Do not replace `className` wholesale. */
        tile.classList.add('prks-tile');
        tile.classList.toggle('prks-tile--main', isMain);
        tile.classList.toggle('prks-tile--secondary', !isMain);
        tile.classList.toggle('prks-tile--focused', isFocused);
        tile.setAttribute('data-prks-tab-id', tabId);
        if (visualTiled && isMain) tile.setAttribute('aria-label', mainPaneLabel(snap, tabId));
        else tile.removeAttribute('aria-label');
        const header = tile.querySelector(':scope > .prks-tile-header') || tile.querySelector('.prks-tile-header');
        if (header) fillHeader(header, snap, tabId, isMain ? 'main' : 'secondary', visualTiled);
    }

    /** Recursively reconciles `node` (leaf or split) into DOM under `canvas`, reusing/reparenting
     * existing hosts. Returns the DOM element representing `node`'s root (a tile for a leaf, a
     * `.prks-workspace-split` container for a split) -- never destroyed/recreated for an
     * unrelated sibling mutation. */
    function renderTreeNode(canvas, node, snap, visualTiled) {
        if (!node) return null;
        if (node.type === 'leaf') {
            const tile = ensureTile(canvas, node.tabId);
            applyTileClasses(tile, snap, node.tabId, visualTiled);
            return tile;
        }
        if (node.type !== 'split') return null;
        const container = ensureSplitContainer(canvas, node.id);
        container.setAttribute('data-prks-axis', node.axis);
        watchNestedSplit(node.id, container);
        const firstEl = renderTreeNode(canvas, node.first, snap, visualTiled);
        const secondEl = renderTreeNode(canvas, node.second, snap, visualTiled);
        if (firstEl && secondEl && typeof root.prksWorkspaceSyncNestedSeparator === 'function') {
            root.prksWorkspaceSyncNestedSeparator(container, node, firstEl, secondEl);
        }
        return container;
    }

    function clearSecondaryRootMarker(canvas, keep) {
        const kids = canvas.children;
        if (!kids) return;
        for (let i = 0; i < kids.length; i++) {
            const kid = kids[i];
            if (kid !== keep && kid.getAttribute && kid.getAttribute('data-prks-secondary-root') === '1') {
                kid.removeAttribute('data-prks-secondary-root');
            }
        }
    }

    /** Removes DOM hosts (tiles + split containers) no longer present in the current tree.
     * Runs after rendering, once every surviving node has already been reparented into its new
     * position, so anything still found here is genuinely orphaned garbage. */
    function pruneStale(canvas, keepTabIds, keepSplitIds) {
        const staleTiles = collectAll(canvas, function (n) {
            return isAnyTile(n) && !keepTabIds[n.getAttribute('data-prks-tab-id')];
        });
        const staleContainers = collectAll(canvas, function (n) {
            return isAnySplitContainer(n) && !keepSplitIds[n.getAttribute('data-prks-split-id')];
        });
        /* Defensive lifecycle integration (drag-and-drop workspace management #3): a workspace
         * mutation (close, park, make-main, ...) can remove a Secondary tile that happens to be
         * the live drag source before another pointer event ever fires. Cancel before any actual
         * DOM removal below -- but only when there is genuinely stale DOM to remove. pruneStale()
         * also runs on every ordinary prksWorkspaceSyncTiles() paint (e.g. a resolved-title
         * update) where nothing is stale; an active drag must survive those unconditionally.
         * Idempotent/no-op when no drag is active, and already a no-op on a normal successful
         * drop commit (that path already ran cleanup() itself before calling into the canonical
         * mutation that lands here). */
        if ((staleTiles.length || staleContainers.length) && typeof root.prksWorkspaceCancelActiveDrag === 'function') {
            root.prksWorkspaceCancelActiveDrag();
        }
        for (let i = 0; i < staleTiles.length; i++) {
            const el = staleTiles[i];
            if (el.parentNode) el.parentNode.removeChild(el);
        }
        for (let i = 0; i < staleContainers.length; i++) {
            const el = staleContainers[i];
            if (typeof root.prksWorkspaceReleaseNestedSeparator === 'function') {
                root.prksWorkspaceReleaseNestedSeparator(el);
            }
            disconnectNestedObserver(el.getAttribute('data-prks-split-id'));
            if (el.parentNode) el.parentNode.removeChild(el);
        }
    }

    function prksWorkspaceApplyFocus(snap, options) {
        const d = doc();
        if (!d || !snap) return;
        const opts = options || {};
        const visualTiled = opts.visualMode === 'tiled';
        const canvas = ensureCanvas();
        if (canvas) {
            const ids = [snap.mainTabId].concat(
                visualTiled && typeof root.collectLeafTabIds === 'function' ? root.collectLeafTabIds(snap.secondaryTree) : []
            );
            for (let i = 0; i < ids.length; i++) {
                if (!ids[i]) continue;
                const tile = findTile(canvas, ids[i]);
                if (!tile) continue;
                tile.classList.toggle('prks-tile--focused', ids[i] === snap.focusedTabId);
                tile.classList.toggle('prks-tile--main', ids[i] === snap.mainTabId);
                tile.classList.toggle('prks-tile--secondary', ids[i] !== snap.mainTabId);
            }
        }
        const list = d.getElementById('prks-workspace-tabs');
        if (!list) return;
        const tabs = list.querySelectorAll('.prks-workspace-tab[data-tab-id]');
        for (let i = 0; i < tabs.length; i++) {
            const id = tabs[i].getAttribute('data-tab-id');
            tabs[i].classList.toggle('is-focused', id === snap.focusedTabId);
        }
    }

    function prksWorkspaceSyncTiles(snap, options) {
        const canvas = ensureCanvas();
        if (!canvas) return;
        const beforeParents = snapshotTileParents(canvas);
        const opts = options || {};
        const visualTiled = opts.visualMode === 'tiled';
        canvas.classList.toggle('prks-workspace-canvas--tiled', visualTiled);
        canvas.classList.toggle('prks-workspace-canvas--stacked', !visualTiled);
        if (typeof root.prksSyncDenseWorkspaceShell === 'function') {
            root.prksSyncDenseWorkspaceShell(visualTiled);
        }

        const mainTile = ensureTile(canvas, snap.mainTabId);
        applyTileClasses(mainTile, snap, snap.mainTabId, visualTiled);
        if (mainTile.parentNode !== canvas) canvas.appendChild(mainTile);

        const treeLeafIds = visualTiled && typeof root.collectLeafTabIds === 'function' ? root.collectLeafTabIds(snap.secondaryTree) : [];
        const treeSplitIds = visualTiled && typeof root.collectSplitIds === 'function' ? root.collectSplitIds(snap.secondaryTree) : [];

        let secondaryRoot = null;
        if (visualTiled && snap.secondaryTree) {
            secondaryRoot = renderTreeNode(canvas, snap.secondaryTree, snap, visualTiled);
        }

        const keepTabIds = Object.create(null);
        keepTabIds[snap.mainTabId] = true;
        for (let i = 0; i < treeLeafIds.length; i++) keepTabIds[treeLeafIds[i]] = true;
        const keepSplitIds = Object.create(null);
        for (let i = 0; i < treeSplitIds.length; i++) keepSplitIds[treeSplitIds[i]] = true;

        if (secondaryRoot) {
            secondaryRoot.setAttribute('data-prks-secondary-root', '1');
            clearSecondaryRootMarker(canvas, secondaryRoot);
            if (secondaryRoot.parentNode !== canvas) canvas.appendChild(secondaryRoot);
        } else {
            clearSecondaryRootMarker(canvas, null);
        }

        pruneStale(canvas, keepTabIds, keepSplitIds);

        /* Order canvas's direct children: [mainTile, separator (if any), secondaryRoot (if any)]. */
        if (secondaryRoot && canvas.children[0] !== mainTile) {
            canvas.insertBefore(mainTile, canvas.children[0] || null);
        }
        if (secondaryRoot && mainTile.nextSibling !== secondaryRoot) {
            const existingSep =
                mainTile.nextSibling && mainTile.nextSibling.className && mainTile.nextSibling.className.indexOf('prks-splitter') !== -1
                    ? mainTile.nextSibling
                    : null;
            canvas.insertBefore(secondaryRoot, existingSep ? existingSep.nextSibling : mainTile.nextSibling);
        }

        if (typeof root.prksWorkspaceSyncSplitSeparator === 'function') {
            root.prksWorkspaceSyncSplitSeparator(canvas, visualTiled, snap);
        }

        armClickFallback(beforeParents, canvas);
    }

    /** Snapshot of every currently-mounted tile's parent, keyed by tab id -- taken before a paint
     * reconciles the tree, so the paint can tell which surviving tiles it actually moved to a new
     * parent (e.g. an existing Secondary leaf nested into a brand-new split container) versus
     * left alone. */
    function snapshotTileParents(canvas) {
        const map = Object.create(null);
        const tiles = collectAll(canvas, isTileHost);
        for (let i = 0; i < tiles.length; i++) {
            map[tiles[i].getAttribute('data-prks-tab-id')] = tiles[i].parentNode;
        }
        return map;
    }

    /** Stricter than isAnyTile: only the tile host itself, never a same-tabId descendant such as
     * tab-context.js's `.prks-tab-root` mount point (which also carries `data-prks-tab-id`, for
     * its own unrelated purposes, and gets reshuffled by ordinary in-tab rendering). */
    function isTileHost(node) {
        return !!(node.getAttribute && node.getAttribute('data-prks-tab-id') && node.className && node.className.indexOf('prks-tile') !== -1);
    }

    /** Chromium leaves stale hit-test state on an existing element that gets reparented (moved to
     * a different parent node, e.g. an existing Secondary leaf nested into a brand-new split
     * container by renderTreeNode/insertBefore above): mousedown/pointerup keep targeting it
     * correctly, but the browser silently never synthesizes the follow-up `click` -- so the
     * tile's own header buttons (pane menu, close) go dead on the very next real click after a
     * Split, while a brand-new tile (never reparented) is unaffected.
     *
     * This does not try to pre-emptively "fix" that browser-internal state (extensive testing
     * found no way to do that both reliably and safely: every timing that reliably clears it --
     * a chain of requestIdleCallback passes, confirmed necessary and confirmed sufficient in
     * isolation -- takes long enough that it can land in the middle of a *different*, unrelated
     * click happening elsewhere in the workspace and silently break that click instead, via this
     * same mechanism). Instead it treats the symptom directly and safely: arm a one-shot
     * `pointerup` listener on the reparented tile, and if the real `click` this browser owes that
     * gesture hasn't shown up by the very next tick, dispatch it by calling `.click()` on the
     * pressed button ourselves. `.click()` synthesizes a proper click through the normal DOM path
     * (bubbles, real target, real listeners) without depending on the browser's native hit-test
     * pipeline at all, so it fires regardless of that pipeline's stale state. Scoped to a single
     * real gesture on the affected tile, so it can never touch, delay, or interfere with anything
     * else happening in the workspace. */
    function armClickFallback(beforeParents, canvas) {
        if (!beforeParents) return;
        const tiles = collectAll(canvas, isTileHost);
        for (let i = 0; i < tiles.length; i++) {
            const tile = tiles[i];
            const before = beforeParents[tile.getAttribute('data-prks-tab-id')];
            if (before === undefined || before === tile.parentNode) continue;
            if (typeof tile.addEventListener !== 'function') continue;
            tile.addEventListener('pointerup', onReparentedTilePointerUp, true);
        }
    }

    function onReparentedTilePointerUp(ev) {
        const tile = ev.currentTarget;
        tile.removeEventListener('pointerup', onReparentedTilePointerUp, true);
        const target = ev.target && typeof ev.target.closest === 'function' ? ev.target.closest('button') : null;
        if (!target || typeof target.click !== 'function') return;
        let clicked = false;
        const onClick = function () {
            clicked = true;
        };
        target.addEventListener('click', onClick, true);
        root.setTimeout(function () {
            target.removeEventListener('click', onClick, true);
            const d = doc();
            if (!clicked && d && d.contains(target)) target.click();
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
        const narrow = canvas.clientWidth < NARROW_PX;
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
        d.addEventListener('focusin', onFocusIn);
    }

    function prksWorkspaceCanvasIsNarrow() {
        const canvas =
            typeof document !== 'undefined' && document.querySelector
                ? document.querySelector('.prks-workspace-canvas')
                : null;
        const measured = canvas && canvas.clientWidth > 0 ? canvas.clientWidth : 0;
        const width = measured || (typeof root.innerWidth === 'number' ? root.innerWidth : 0);
        if (!(width > 0)) return false;
        return width < NARROW_PX;
    }

    function prksWorkspaceInitTiles() {
        ensureCanvas();
        bindFocusLayer();
        if (typeof root.prksWorkspaceSnapshot === 'function') {
            const snap = root.prksWorkspaceSnapshot();
            let visualTiled = !!(snap && snap.mode === 'tiled');
            if (typeof root.prksWorkspaceVisualTiled === 'function') {
                visualTiled = !!root.prksWorkspaceVisualTiled();
            }
            prksWorkspaceSyncTiles(snap, { visualMode: visualTiled ? 'tiled' : 'stacked' });
        }
    }

    const api = {
        prksWorkspaceHostForTab: prksWorkspaceHostForTab,
        prksWorkspaceSyncTiles: prksWorkspaceSyncTiles,
        prksWorkspaceApplyFocus: prksWorkspaceApplyFocus,
        prksWorkspaceInitTiles: prksWorkspaceInitTiles,
        prksWorkspaceEnsureTileHost: ensureTile,
        prksWorkspaceCanvasIsNarrow: prksWorkspaceCanvasIsNarrow,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
