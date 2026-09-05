/**
 * Unified ribbon create control: primary New File, chevron menu for other create actions.
 * Reuses existing openModal handlers. Menu styling matches workspace menus.
 */
(function (root) {
    'use strict';

    const CREATE_ACTIONS = [
        { id: 'new-file', label: 'New File', icon: 'file-plus', modalId: 'work-modal' },
        { id: 'new-folder', label: 'New Folder', icon: 'folder', modalId: 'folder-modal' },
        { id: 'new-person', label: 'New Person', icon: 'user', modalId: 'person-modal' },
        { id: 'new-group', label: 'New Group', icon: 'users', modalId: 'group-modal' },
    ];

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

    function ensureMenu() {
        const d = doc();
        if (!d || !d.body) return null;
        if (menuEl && d.body.contains(menuEl)) return menuEl;
        menuEl = d.createElement('div');
        menuEl.id = 'prks-create-menu';
        menuEl.className = 'prks-workspace-menu';
        menuEl.setAttribute('role', 'menu');
        menuEl.setAttribute('aria-label', 'Create');
        menuEl.hidden = true;
        d.body.appendChild(menuEl);
        return menuEl;
    }

    function clearExpanded() {
        const chevron = doc() && doc().getElementById('prks-ribbon-new-more');
        if (chevron) chevron.setAttribute('aria-expanded', 'false');
    }

    function closeMenu() {
        if (!menuEl || menuEl.hidden) {
            restoreTarget = null;
            clearExpanded();
            return;
        }
        menuEl.hidden = true;
        menuEl.replaceChildren();
        menuItems = [];
        clearExpanded();
        const target = restoreTarget;
        restoreTarget = null;
        const d = doc();
        if (target && typeof target.focus === 'function' && d && d.contains(target)) {
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

    function runAction(modalId) {
        closeMenu();
        if (typeof root.openModal === 'function') root.openModal(modalId);
    }

    function positionMenu(anchorRect) {
        const d = doc();
        const menu = ensureMenu();
        if (!menu) return;
        menu.hidden = false;
        const vw = (d.documentElement && d.documentElement.clientWidth) || root.innerWidth || 800;
        const vh = (d.documentElement && d.documentElement.clientHeight) || root.innerHeight || 600;
        const mw = menu.offsetWidth || 200;
        const mh = menu.offsetHeight || 160;
        let x = anchorRect ? anchorRect.left : 8;
        let y = anchorRect ? anchorRect.bottom : 8;
        if (x + mw > vw - 8) x = Math.max(8, vw - mw - 8);
        if (y + mh > vh - 8) y = Math.max(8, (anchorRect ? anchorRect.top - mh : 8));
        if (x < 8) x = 8;
        if (y < 8) y = 8;
        menu.style.left = Math.round(x) + 'px';
        menu.style.top = Math.round(y) + 'px';
    }

    function openMenu(ev) {
        const d = doc();
        const chevron = d && d.getElementById('prks-ribbon-new-more');
        const menu = ensureMenu();
        if (!chevron || !menu) return;
        if (!menu.hidden) {
            closeMenu();
            return;
        }
        restoreTarget = chevron;
        chevron.setAttribute('aria-expanded', 'true');
        menu.replaceChildren();
        menuItems = [];
        CREATE_ACTIONS.forEach(function (spec) {
            const btn = d.createElement('button');
            btn.type = 'button';
            btn.className = 'prks-workspace-menu__item';
            btn.setAttribute('role', 'menuitem');
            btn.setAttribute('data-create-id', spec.id);
            const ic = d.createElement('span');
            ic.className = 'prks-workspace-menu__icon';
            ic.setAttribute('aria-hidden', 'true');
            ic.innerHTML = iconHtml(spec.icon);
            btn.appendChild(ic);
            const label = d.createElement('span');
            label.className = 'prks-workspace-menu__label';
            label.textContent = spec.label;
            btn.appendChild(label);
            btn.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                runAction(spec.modalId);
            });
            menu.appendChild(btn);
            menuItems.push(btn);
        });
        if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(menu);
        const rect = typeof chevron.getBoundingClientRect === 'function' ? chevron.getBoundingClientRect() : null;
        positionMenu(rect);
        setActiveItem(0);
        if (ev && typeof ev.preventDefault === 'function') ev.preventDefault();
    }

    function onDocPointer(ev) {
        if (!menuEl || menuEl.hidden) return;
        const t = ev.target;
        if (menuEl.contains(t)) return;
        const chevron = doc().getElementById('prks-ribbon-new-more');
        if (chevron && (chevron === t || (chevron.contains && chevron.contains(t)))) return;
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

    function onChevronClick(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        openMenu(ev);
    }

    function onChevronKey(ev) {
        if (ev.key === 'ArrowDown' || ev.key === 'Enter' || ev.key === ' ') {
            if (menuEl && !menuEl.hidden) return;
            ev.preventDefault();
            openMenu(ev);
        }
    }

    function init() {
        const d = doc();
        if (!d || bound) return;
        const chevron = d.getElementById('prks-ribbon-new-more');
        const main = d.getElementById('prks-ribbon-new-file');
        if (!chevron) return;
        bound = true;
        ensureMenu();
        chevron.addEventListener('click', onChevronClick);
        chevron.addEventListener('keydown', onChevronKey);
        if (main) {
            main.addEventListener('click', function () {
                if (menuEl && !menuEl.hidden) closeMenu();
            });
        }
        d.addEventListener('pointerdown', onDocPointer, true);
        d.addEventListener('keydown', onDocKey, true);
    }

    function resetForTests() {
        closeMenu();
        bound = false;
        menuEl = null;
        menuItems = [];
        menuIndex = 0;
        restoreTarget = null;
    }

    const api = {
        PRKS_RIBBON_CREATE_ACTIONS: CREATE_ACTIONS,
        prksInitRibbonCreate: init,
        prksCloseRibbonCreateMenu: closeMenu,
        prksOpenRibbonCreateMenu: openMenu,
        prksRibbonCreateResetForTests: resetForTests,
    };
    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
