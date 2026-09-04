/**
 * Workspace tab context menu and overflow menu. Reads workspace snapshot; does not own tab state.
 */
(function (root) {
    'use strict';

    let bound = false;
    let menuEl = null;
    let menuItems = [];
    let menuIndex = 0;
    let restoreTarget = null;

    function doc() {
        return typeof document !== 'undefined' ? document : null;
    }

    function iconHtml(name) {
        if (typeof root.prksIcon === 'function') {
            return root.prksIcon(name, { size: 'sm', className: 'prks-workspace-menu__icon-svg' });
        }
        return '';
    }

    function snapshot() {
        return typeof root.prksWorkspaceSnapshot === 'function' ? root.prksWorkspaceSnapshot() : null;
    }

    function visualTiled(snap) {
        if (typeof root.prksWorkspaceVisualTiled === 'function') return !!root.prksWorkspaceVisualTiled();
        return !!(snap && snap.mode === 'tiled');
    }

    function visibleSecondaryIds(snap) {
        if (!snap) return [];
        return typeof root.collectLeafTabIds === 'function' ? root.collectLeafTabIds(snap.secondaryTree) : [];
    }

    function findTab(snap, tabId) {
        if (!snap || !Array.isArray(snap.tabs)) return null;
        for (let i = 0; i < snap.tabs.length; i++) {
            if (snap.tabs[i].id === tabId) return snap.tabs[i];
        }
        return null;
    }

    function tabIndexOf(snap, tabId) {
        if (!snap || !Array.isArray(snap.tabs)) return -1;
        for (let i = 0; i < snap.tabs.length; i++) {
            if (snap.tabs[i].id === tabId) return i;
        }
        return -1;
    }

    function roleOf(snap, tab) {
        if (!snap || !tab) return 'parked';
        if (tab.id === snap.mainTabId) return 'main';
        if (visualTiled(snap) && visibleSecondaryIds(snap).indexOf(tab.id) !== -1) return 'secondary';
        return 'parked';
    }

    function canSplitRoute(tab) {
        if (!tab) return false;
        return typeof root.prksRouteSupportsTile === 'function' && root.prksRouteSupportsTile(tab.route);
    }

    function ensureMenu() {
        const d = doc();
        if (!d) return null;
        if (menuEl && d.body.contains(menuEl)) return menuEl;
        menuEl = d.createElement('div');
        menuEl.id = 'prks-workspace-menu';
        menuEl.className = 'prks-workspace-menu';
        menuEl.setAttribute('role', 'menu');
        menuEl.hidden = true;
        d.body.appendChild(menuEl);
        return menuEl;
    }

    function closeMenu() {
        if (!menuEl || menuEl.hidden) {
            restoreTarget = null;
            return;
        }
        menuEl.hidden = true;
        menuEl.replaceChildren();
        menuEl.removeAttribute('data-kind');
        menuItems = [];
        const overflow = doc() && doc().getElementById('prks-workspace-tab-overflow');
        if (overflow) overflow.setAttribute('aria-expanded', 'false');
        const target = restoreTarget;
        restoreTarget = null;
        if (target && typeof target.focus === 'function' && doc().contains(target)) {
            target.focus({ preventScroll: true });
        }
    }

    function setActiveItem(index) {
        if (!menuItems.length) return;
        let i = index;
        if (i < 0) i = menuItems.length - 1;
        if (i >= menuItems.length) i = 0;
        menuIndex = i;
        menuItems.forEach(function (btn, n) {
            btn.classList.toggle('is-active', n === i);
            btn.tabIndex = n === i ? 0 : -1;
            if (n === i) btn.focus();
        });
    }

    function runAction(action) {
        closeMenu();
        if (!action) return;
        if (action.kind === 'activate' && typeof root.prksWorkspaceActivateTab === 'function') {
            void root.prksWorkspaceActivateTab(action.tabId);
            return;
        }
        if (action.kind === 'focus' && typeof root.prksWorkspaceFocusTab === 'function') {
            root.prksWorkspaceFocusTab(action.tabId);
            if (typeof root.prksWorkspaceRestoreFocus === 'function') {
                root.prksWorkspaceRestoreFocus(action.tabId);
            }
            return;
        }
        if (action.kind === 'make-main' && typeof root.prksWorkspaceMakeMain === 'function') {
            void root.prksWorkspaceMakeMain(action.tabId);
            return;
        }
        if (action.kind === 'tile' && typeof root.prksWorkspaceTileTab === 'function') {
            void root.prksWorkspaceTileTab(action.tabId);
            return;
        }
        if (action.kind === 'hide-leaf' && typeof root.prksWorkspaceHideLeaf === 'function') {
            void root.prksWorkspaceHideLeaf(action.tabId);
            return;
        }
        if (
            (action.kind === 'split-right' || action.kind === 'split-down') &&
            typeof root.prksOpenCommandPalette === 'function'
        ) {
            root.prksOpenCommandPalette({
                scope: 'all',
                navigationTarget: 'tile',
                splitPlacement: {
                    targetLeafTabId: action.tabId,
                    axis: action.kind === 'split-down' ? 'top-bottom' : 'left-right',
                    placement: 'second',
                },
            });
            return;
        }
        if (action.kind === 'close' && typeof root.prksWorkspaceCloseTab === 'function') {
            void root.prksWorkspaceCloseTab(action.tabId);
            return;
        }
        if (action.kind === 'close-others' && typeof root.prksWorkspaceCloseOtherTabs === 'function') {
            void root.prksWorkspaceCloseOtherTabs(action.tabId);
            return;
        }
        if (action.kind === 'close-right' && typeof root.prksWorkspaceCloseTabsToTheRight === 'function') {
            void root.prksWorkspaceCloseTabsToTheRight(action.tabId);
            return;
        }
        if (action.kind === 'split-picker' && typeof root.prksOpenCommandPalette === 'function') {
            root.prksOpenCommandPalette({ scope: 'all', navigationTarget: 'tile' });
        }
    }

    function addItem(host, spec) {
        const btn = doc().createElement('button');
        btn.type = 'button';
        btn.className = 'prks-workspace-menu__item';
        btn.setAttribute('role', 'menuitem');
        if (spec.disabled) {
            btn.disabled = true;
            btn.setAttribute('aria-disabled', 'true');
            if (spec.disabledTitle) btn.title = spec.disabledTitle;
        }
        if (spec.icon) {
            const ic = doc().createElement('span');
            ic.className = 'prks-workspace-menu__icon';
            ic.setAttribute('aria-hidden', 'true');
            ic.innerHTML = iconHtml(spec.icon);
            btn.appendChild(ic);
        }
        const label = doc().createElement('span');
        label.className = 'prks-workspace-menu__label';
        label.textContent = spec.label;
        btn.appendChild(label);
        btn.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            runAction(spec.action);
        });
        host.appendChild(btn);
        menuItems.push(btn);
        return btn;
    }

    function addSep(host) {
        const sep = doc().createElement('div');
        sep.className = 'prks-workspace-menu__sep';
        sep.setAttribute('role', 'separator');
        host.appendChild(sep);
    }

    function contextSpecs(tabId) {
        const snap = snapshot();
        const tab = findTab(snap, tabId);
        if (!tab) return [];
        const role = roleOf(snap, tab);
        const idx = tabIndexOf(snap, tabId);
        const specs = [];
        if (role === 'parked') {
            specs.push({ label: 'Make main', icon: 'arrow-left', action: { kind: 'activate', tabId: tabId } });
            if (canSplitRoute(tab)) {
                specs.push({
                    label: 'Open in split view',
                    icon: 'columns-2',
                    action: { kind: 'tile', tabId: tabId },
                });
            }
            specs.push({ label: 'Close', icon: 'x', action: { kind: 'close', tabId: tabId } });
            if (snap.tabs.length > 1) {
                specs.push({
                    label: 'Close other tabs',
                    action: { kind: 'close-others', tabId: tabId },
                });
            }
            if (idx >= 0 && idx < snap.tabs.length - 1) {
                specs.push({
                    label: 'Close tabs to the right',
                    action: { kind: 'close-right', tabId: tabId },
                });
            }
            return specs;
        }
        if (role === 'secondary') {
            const canSplit = typeof root.prksWorkspaceCanAddSecondaryLeaf !== 'function' || root.prksWorkspaceCanAddSecondaryLeaf();
            const capTitle = 'Maximum of 4 visible panes. Close or hide a pane to split again.';
            if (snap.focusedTabId !== tabId) {
                specs.push({ label: 'Focus', action: { kind: 'focus', tabId: tabId } });
            }
            specs.push({ label: 'Make main', icon: 'arrow-left', action: { kind: 'make-main', tabId: tabId } });
            specs.push({
                label: 'Split right',
                icon: 'columns-2',
                action: { kind: 'split-right', tabId: tabId },
                disabled: !canSplit,
                disabledTitle: capTitle,
            });
            specs.push({
                label: 'Split down',
                icon: 'rows-2',
                action: { kind: 'split-down', tabId: tabId },
                disabled: !canSplit,
                disabledTitle: capTitle,
            });
            specs.push({ label: 'Hide from split', icon: 'eye-off', action: { kind: 'hide-leaf', tabId: tabId } });
            specs.push({ label: 'Close', action: { kind: 'close', tabId: tabId } });
            return specs;
        }
        specs.push({
            label: 'Open another tab in split view',
            icon: 'columns-2',
            action: { kind: 'split-picker', tabId: tabId },
        });
        specs.push({ label: 'Close', icon: 'x', action: { kind: 'close', tabId: tabId } });
        if (snap.tabs.length > 1) {
            specs.push({
                label: 'Close other tabs',
                action: { kind: 'close-others', tabId: tabId },
            });
        }
        if (idx >= 0 && idx < snap.tabs.length - 1) {
            specs.push({
                label: 'Close tabs to the right',
                action: { kind: 'close-right', tabId: tabId },
            });
        }
        return specs;
    }

    function positionMenu(anchorRect, clientX, clientY) {
        const d = doc();
        const menu = ensureMenu();
        if (!menu) return;
        menu.hidden = false;
        const vw = d.documentElement.clientWidth || root.innerWidth || 800;
        const vh = d.documentElement.clientHeight || root.innerHeight || 600;
        const mw = menu.offsetWidth || 200;
        const mh = menu.offsetHeight || 160;
        let x = clientX;
        let y = clientY;
        if (anchorRect && (clientX == null || clientY == null)) {
            x = anchorRect.left;
            y = anchorRect.bottom;
        }
        if (x + mw > vw - 8) x = Math.max(8, vw - mw - 8);
        if (y + mh > vh - 8) y = Math.max(8, vh - mh - 8);
        if (x < 8) x = 8;
        menu.style.left = Math.round(x) + 'px';
        menu.style.top = Math.round(y) + 'px';
    }

    function fillAndShow(specs, anchorRect, clientX, clientY, label) {
        const menu = ensureMenu();
        if (!menu) return;
        menu.replaceChildren();
        menuItems = [];
        menu.setAttribute('aria-label', label || 'Workspace tab');
        for (let i = 0; i < specs.length; i++) addItem(menu, specs[i]);
        if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(menu);
        positionMenu(anchorRect, clientX, clientY);
        setActiveItem(0);
    }

    function prksWorkspaceOpenTabMenu(tabId, ev) {
        const d = doc();
        if (!d || !tabId) return;
        const wrap = d.querySelector('.prks-workspace-tab[data-tab-id="' + String(tabId).replace(/"/g, '') + '"]');
        const specs = contextSpecs(tabId);
        if (!specs.length) return;
        restoreTarget = (ev && ev.currentTarget) || (wrap && wrap.querySelector('.prks-workspace-tab__activate'));
        const menu = ensureMenu();
        if (menu) menu.removeAttribute('data-kind');
        const rect = wrap ? wrap.getBoundingClientRect() : null;
        const x = ev && typeof ev.clientX === 'number' && ev.clientX ? ev.clientX : null;
        const y = ev && typeof ev.clientY === 'number' && ev.clientY ? ev.clientY : null;
        fillAndShow(specs, rect, x, y, 'Tab actions');
    }

    function overflowSpecs() {
        const snap = snapshot();
        if (!snap || !Array.isArray(snap.tabs)) return [];
        const out = [];
        for (let i = 0; i < snap.tabs.length; i++) {
            const tab = snap.tabs[i];
            const role = roleOf(snap, tab);
            out.push({
                tab: tab,
                role: role,
                mark: role === 'main' ? 'Main' : role === 'secondary' ? 'Split' : '',
            });
        }
        return out;
    }

    function openOverflow(ev) {
        const d = doc();
        const btn = d && d.getElementById('prks-workspace-tab-overflow');
        const menu = ensureMenu();
        if (!btn || !menu) return;
        if (!menu.hidden && menu.getAttribute('data-kind') === 'overflow') {
            closeMenu();
            return;
        }
        const rows = overflowSpecs();
        menu.replaceChildren();
        menuItems = [];
        menu.setAttribute('aria-label', 'Open tabs');
        menu.setAttribute('data-kind', 'overflow');
        restoreTarget = btn;
        btn.setAttribute('aria-expanded', 'true');
        rows.forEach(function (row) {
            const host = d.createElement('div');
            host.className = 'prks-workspace-menu__overflow-row';
            const item = d.createElement('button');
            item.type = 'button';
            item.className = 'prks-workspace-menu__item';
            item.setAttribute('role', 'menuitem');
            const ic = d.createElement('span');
            ic.className = 'prks-workspace-menu__icon';
            ic.setAttribute('aria-hidden', 'true');
            ic.innerHTML = iconHtml(row.tab.icon || 'file-text');
            const label = d.createElement('span');
            label.className = 'prks-workspace-menu__label';
            label.textContent = row.tab.title || 'Page';
            item.appendChild(ic);
            item.appendChild(label);
            if (row.mark) {
                const mark = d.createElement('span');
                mark.className = 'prks-workspace-menu__mark';
                mark.textContent = row.mark;
                item.appendChild(mark);
            }
            item.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                if (row.role === 'secondary') runAction({ kind: 'focus', tabId: row.tab.id });
                else runAction({ kind: 'activate', tabId: row.tab.id });
            });
            host.appendChild(item);
            menuItems.push(item);
            if (row.role === 'parked' && canSplitRoute(row.tab)) {
                const split = d.createElement('button');
                split.type = 'button';
                split.className = 'prks-workspace-menu__overflow-close';
                split.setAttribute('role', 'menuitem');
                split.setAttribute('aria-label', 'Open ' + (row.tab.title || 'page') + ' in split view');
                split.title = 'Open in split view';
                split.innerHTML = iconHtml('columns-2');
                split.addEventListener('click', function (e) {
                    e.preventDefault();
                    e.stopPropagation();
                    runAction({ kind: 'tile', tabId: row.tab.id });
                });
                host.appendChild(split);
                menuItems.push(split);
            }
            const close = d.createElement('button');
            close.type = 'button';
            close.className = 'prks-workspace-menu__overflow-close';
            close.setAttribute('role', 'menuitem');
            close.setAttribute('aria-label', 'Close ' + (row.tab.title || 'tab'));
            close.title = 'Close';
            close.innerHTML = iconHtml('x');
            close.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                runAction({ kind: 'close', tabId: row.tab.id });
            });
            host.appendChild(close);
            menuItems.push(close);
            menu.appendChild(host);
        });
        if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(menu);
        const rect = btn.getBoundingClientRect();
        const x = ev && ev.clientX ? ev.clientX : rect.left;
        const y = ev && ev.clientY ? ev.clientY : rect.bottom;
        positionMenu(rect, x, y);
        setActiveItem(0);
    }

    function onDocPointer(ev) {
        if (!menuEl || menuEl.hidden) return;
        if (menuEl.contains(ev.target)) return;
        const overflow = doc().getElementById('prks-workspace-tab-overflow');
        if (overflow && overflow.contains(ev.target)) return;
        closeMenu();
    }

    function onDocKey(ev) {
        if (!menuEl || menuEl.hidden) return;
        if (ev.key === 'Escape') {
            ev.preventDefault();
            closeMenu();
            return;
        }
        if (ev.key === 'ArrowDown') {
            ev.preventDefault();
            setActiveItem(menuIndex + 1);
            return;
        }
        if (ev.key === 'ArrowUp') {
            ev.preventDefault();
            setActiveItem(menuIndex - 1);
            return;
        }
        if (ev.key === 'Home') {
            ev.preventDefault();
            setActiveItem(0);
            return;
        }
        if (ev.key === 'End') {
            ev.preventDefault();
            setActiveItem(menuItems.length - 1);
            return;
        }
        if (ev.key === 'Enter' || ev.key === ' ') {
            ev.preventDefault();
            const focused = ev.target && menuItems.indexOf(ev.target) >= 0 ? ev.target : menuItems[menuIndex];
            if (focused) focused.click();
        }
    }

    function prksWorkspaceInitTabMenus() {
        const d = doc();
        if (!d || bound) return;
        bound = true;
        ensureMenu();
        d.addEventListener('pointerdown', onDocPointer, true);
        d.addEventListener('keydown', onDocKey, true);
        const overflow = d.getElementById('prks-workspace-tab-overflow');
        if (overflow && overflow.getAttribute('data-workspace-bound') !== '1') {
            overflow.setAttribute('data-workspace-bound', '1');
            overflow.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                openOverflow(e);
            });
        }
    }

    const api = {
        prksWorkspaceInitTabMenus: prksWorkspaceInitTabMenus,
        prksWorkspaceOpenTabMenu: prksWorkspaceOpenTabMenu,
        prksWorkspaceCloseTabMenu: closeMenu,
    };
    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
