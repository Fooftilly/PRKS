/**
 * Workspace tile DOM/layout only. Canonical workspace state lives in workspace-tabs.js.
 */
(function (root) {
    'use strict';

    const NARROW_PX = 720;
    let bound = false;
    let resizeObserver = null;
    let lastNarrow = null;

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

    function tileSelector(tabId) {
        return '.prks-tile[data-prks-tab-id="' + String(tabId).replace(/"/g, '') + '"]';
    }

    function findTile(canvas, tabId) {
        if (!canvas) return null;
        const kids = canvas.children;
        for (let i = 0; i < kids.length; i++) {
            if (kids[i].getAttribute && kids[i].getAttribute('data-prks-tab-id') === tabId) {
                return kids[i];
            }
        }
        return canvas.querySelector(tileSelector(tabId));
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

    function fillHeader(header, snap, tabId, role, visualTiled) {
        header.replaceChildren();
        if (!visualTiled) {
            header.hidden = true;
            return;
        }
        header.hidden = false;

        const icon = doc().createElement('span');
        icon.className = 'prks-tile-header__icon';
        icon.setAttribute('aria-hidden', 'true');
        icon.innerHTML = iconHtml(headerIcon(snap, tabId));

        const title = doc().createElement('span');
        title.className = 'prks-tile-header__title';
        title.textContent = headerTitle(snap, tabId);

        header.appendChild(icon);
        header.appendChild(title);

        const actions = doc().createElement('span');
        actions.className = 'prks-tile-header__actions';

        if (role === 'main') {
            const badge = doc().createElement('span');
            badge.className = 'prks-tile-header__role';
            badge.textContent = 'Main';
            actions.appendChild(badge);
        } else {
            const makeMain = doc().createElement('button');
            makeMain.type = 'button';
            makeMain.className = 'prks-btn prks-btn--secondary prks-tile-header__make-main';
            makeMain.textContent = 'Make main';
            makeMain.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                if (typeof root.prksWorkspaceMakeMain === 'function') {
                    void root.prksWorkspaceMakeMain(tabId);
                }
            });
            const hide = doc().createElement('button');
            hide.type = 'button';
            hide.className = 'prks-icon-btn prks-icon-btn--ghost prks-tile-header__hide';
            hide.setAttribute('aria-label', 'Hide split view');
            hide.title = 'Hide split view';
            hide.innerHTML = iconHtml('x');
            hide.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                if (typeof root.prksWorkspaceSetMode === 'function') {
                    void root.prksWorkspaceSetMode('stacked');
                }
            });
            actions.appendChild(makeMain);
            actions.appendChild(hide);
        }
        header.appendChild(actions);
        if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(header);
    }

    function createTile(tabId) {
        const section = doc().createElement('section');
        section.className = 'prks-tile';
        section.setAttribute('data-prks-tab-id', tabId);

        const header = doc().createElement('header');
        header.className = 'prks-tile-header';
        header.hidden = true;

        const body = doc().createElement('div');
        body.className = 'prks-tile__body';

        section.appendChild(header);
        section.appendChild(body);
        return section;
    }

    function ensureTile(tabId) {
        const canvas = ensureCanvas();
        if (!canvas || !tabId) return null;
        let tile = findTile(canvas, tabId);
        if (!tile) {
            tile = createTile(tabId);
            canvas.appendChild(tile);
        }
        return tile;
    }

    function tileBody(tile) {
        if (!tile) return null;
        return tile.querySelector(':scope > .prks-tile__body') || tile.querySelector('.prks-tile__body');
    }

    function secondaryId(snap) {
        const tree = snap && snap.secondaryTree;
        if (!tree || tree.type !== 'leaf' || !tree.tabId) return null;
        return tree.tabId;
    }

    function visibleIds(snap, visualTiled) {
        const ids = [];
        if (snap && snap.mainTabId) ids.push(snap.mainTabId);
        const sec = secondaryId(snap);
        if (visualTiled && sec && sec !== snap.mainTabId) ids.push(sec);
        return ids;
    }

    function prksWorkspaceHostForTab(tabId) {
        if (!tabId) return null;
        const tile = ensureTile(tabId);
        return tileBody(tile);
    }

    function applyTileClasses(tile, snap, tabId, visualTiled) {
        if (!tile || !snap) return;
        const isMain = tabId === snap.mainTabId;
        const isFocused = tabId === snap.focusedTabId;
        tile.className =
            'prks-tile' +
            (isMain ? ' prks-tile--main' : ' prks-tile--secondary') +
            (isFocused ? ' prks-tile--focused' : '');
        tile.setAttribute('data-prks-tab-id', tabId);
        const header = tile.querySelector(':scope > .prks-tile-header') || tile.querySelector('.prks-tile-header');
        if (header) fillHeader(header, snap, tabId, isMain ? 'main' : 'secondary', visualTiled);
    }

    function prksWorkspaceApplyFocus(snap, options) {
        const d = doc();
        if (!d || !snap) return;
        const opts = options || {};
        const visualTiled = opts.visualMode === 'tiled';
        const canvas = ensureCanvas();
        if (canvas) {
            const ids = visibleIds(snap, visualTiled);
            for (let i = 0; i < ids.length; i++) {
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
        const opts = options || {};
        const visualTiled = opts.visualMode === 'tiled';
        canvas.classList.toggle('prks-workspace-canvas--tiled', visualTiled);
        canvas.classList.toggle('prks-workspace-canvas--stacked', !visualTiled);

        const ids = visibleIds(snap, visualTiled);
        const keep = {};
        for (let i = 0; i < ids.length; i++) keep[ids[i]] = true;

        const existing = Array.prototype.slice.call(canvas.children);
        for (let i = 0; i < existing.length; i++) {
            const id = existing[i].getAttribute('data-prks-tab-id');
            if (!keep[id]) canvas.removeChild(existing[i]);
        }

        for (let i = 0; i < ids.length; i++) {
            const tile = ensureTile(ids[i]);
            applyTileClasses(tile, snap, ids[i], visualTiled);
        }

        const ordered = [];
        for (let i = 0; i < ids.length; i++) {
            const tile = findTile(canvas, ids[i]);
            if (tile) ordered.push(tile);
        }
        for (let i = 0; i < ordered.length; i++) {
            if (canvas.children[i] !== ordered[i]) canvas.appendChild(ordered[i]);
        }
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
        const narrow = canvas.clientWidth > 0 && canvas.clientWidth < NARROW_PX;
        if (lastNarrow === narrow) return;
        if (typeof root.prksWorkspaceSetNarrowFallback !== 'function') {
            lastNarrow = narrow;
            return;
        }
        Promise.resolve(root.prksWorkspaceSetNarrowFallback(narrow)).then(function (ok) {
            if (ok !== false) lastNarrow = narrow;
        });
    }

    function watchCanvas(canvas) {
        if (!canvas || typeof ResizeObserver === 'undefined') return;
        if (resizeObserver) {
            try {
                resizeObserver.disconnect();
            } catch (_e) {}
        }
        resizeObserver = new ResizeObserver(function () {
            applyNarrow(canvas);
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

    function prksWorkspaceInitTiles() {
        ensureCanvas();
        bindFocusLayer();
        if (typeof root.prksWorkspaceSnapshot === 'function') {
            const snap = root.prksWorkspaceSnapshot();
            prksWorkspaceSyncTiles(snap, { visualMode: snap && snap.mode === 'tiled' ? 'tiled' : 'stacked' });
        }
    }

    const api = {
        prksWorkspaceHostForTab: prksWorkspaceHostForTab,
        prksWorkspaceSyncTiles: prksWorkspaceSyncTiles,
        prksWorkspaceApplyFocus: prksWorkspaceApplyFocus,
        prksWorkspaceInitTiles: prksWorkspaceInitTiles,
        prksWorkspaceEnsureTileHost: ensureTile,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
