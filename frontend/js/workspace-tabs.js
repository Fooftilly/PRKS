/**
 * Stacked workspace tabs. Parked tabs are state only: no DOM, fetch, or render.
 * Tiling / TabContext / persistence are out of scope.
 */
(function (root) {
    'use strict';

    const WORKSPACE_VERSION = 1;
    const MODE_STACKED = 'stacked';
    const HOME_HASH = '#/folders';
    const INTERACTIVE_NEST =
        'button, a, input, select, textarea, [contenteditable="true"],' +
        '[role="button"], [role="link"], [role="menuitem"], [role="tab"],' +
        '[role="checkbox"], [role="radio"], [role="switch"]';

    function defaultHome() {
        if (typeof root.PRKS_HOME_HASH === 'string' && root.PRKS_HOME_HASH) return root.PRKS_HOME_HASH;
        return HOME_HASH;
    }

    function copyJson(value) {
        if (value == null) return value;
        try {
            return JSON.parse(JSON.stringify(value));
        } catch (_e) {
            return null;
        }
    }

    function copyTab(tab) {
        return {
            id: tab.id,
            route: tab.route,
            title: tab.title,
            icon: tab.icon,
            history: tab.history.slice(),
            historyIndex: tab.historyIndex,
            titleRouteGen: tab.titleRouteGen,
        };
    }

    function workspacePayload(tab) {
        return {
            v: WORKSPACE_VERSION,
            tabId: tab.id,
            route: tab.route,
            historyIndex: tab.historyIndex,
        };
    }

    function mergeHistoryState(existing, tab) {
        const base =
            existing && typeof existing === 'object' && !Array.isArray(existing) ? Object.assign({}, existing) : {};
        base.prksWorkspace = workspacePayload(tab);
        return base;
    }

    function hashFromHref(href, fallback) {
        if (href == null || href === '') return fallback;
        try {
            const u = new URL(String(href), 'http://127.0.0.1/');
            return u.hash || fallback;
        } catch (_e) {
            const s = String(href);
            const i = s.indexOf('#');
            return i >= 0 ? s.slice(i) : fallback;
        }
    }

    function prksWorkspaceNavigationIntent(ev) {
        if (!ev) return 'ignore';
        if (ev.shiftKey || ev.altKey) return 'ignore';
        const button = ev.button;
        if (button === 1) return 'background';
        if (button != null && button !== 0) return 'ignore';
        if (ev.ctrlKey || ev.metaKey) return 'background';
        return 'current';
    }

    function createPrksWorkspaceTabs(deps) {
        deps = deps || {};
        const parseRoute =
            deps.parseRoute ||
            function (hash) {
                return { canonicalHash: hash || defaultHome(), hash: hash || defaultHome(), name: 'unknown' };
            };
        const routeLoadingTitle =
            deps.routeLoadingTitle ||
            function () {
                return 'Loading';
            };
        const routeTabIcon =
            deps.routeTabIcon ||
            function () {
                return 'file-text';
            };
        const homeHash = deps.homeHash || defaultHome();
        const historyAdapter = deps.historyAdapter || {};
        const canLeave =
            deps.canLeave ||
            function () {
                return true;
            };
        const renderRoute =
            deps.renderRoute ||
            function () {
                return undefined;
            };
        const onChange = typeof deps.onChange === 'function' ? deps.onChange : function () {};
        const announce = typeof deps.announce === 'function' ? deps.announce : function () {};
        const isRouteGenCurrent =
            typeof deps.isRouteGenCurrent === 'function' ? deps.isRouteGenCurrent : null;

        let seq = 0;
        let lastHandledHref = '';
        let lastRenderGen = 0;
        const state = {
            version: WORKSPACE_VERSION,
            mode: MODE_STACKED,
            mainTabId: null,
            focusedTabId: null,
            tabs: [],
        };

        function nextId() {
            seq += 1;
            return 'tab-' + seq;
        }

        function getHash() {
            if (typeof historyAdapter.getHash === 'function') return historyAdapter.getHash() || homeHash;
            return homeHash;
        }

        function getHref() {
            if (typeof historyAdapter.getHref === 'function') return historyAdapter.getHref();
            return getHash();
        }

        function getBrowserState() {
            if (typeof historyAdapter.getState === 'function') return historyAdapter.getState();
            return null;
        }

        function hrefWithHash(hash) {
            const href = getHref() || 'http://127.0.0.1/';
            try {
                const u = new URL(href);
                u.hash = hash;
                return u.href;
            } catch (_e) {
                return hash;
            }
        }

        function canonical(raw) {
            const route = parseRoute(raw == null || raw === '' ? homeHash : raw);
            if (!route) return homeHash;
            const target = route.canonicalHash || homeHash;
            if (!target || target.charAt(0) !== '#' || target.charAt(1) !== '/') return homeHash;
            return target;
        }

        function tabIndex(tabId) {
            for (let i = 0; i < state.tabs.length; i++) {
                if (state.tabs[i].id === tabId) return i;
            }
            return -1;
        }

        function getTab(tabId) {
            const i = tabIndex(tabId);
            return i < 0 ? null : state.tabs[i];
        }

        function getMainTab() {
            return getTab(state.mainTabId);
        }

        function markHandled() {
            lastHandledHref = getHref();
        }

        function isDuplicateLocation() {
            return !!lastHandledHref && lastHandledHref === getHref();
        }

        function applyTabRoute(tab, hash) {
            tab.route = hash;
            tab.title = routeLoadingTitle(hash);
            tab.icon = routeTabIcon(hash);
            tab.titleRouteGen = null;
        }

        function makeTab(hash) {
            const route = canonical(hash);
            return {
                id: nextId(),
                route: route,
                title: routeLoadingTitle(route),
                icon: routeTabIcon(route),
                history: [route],
                historyIndex: 0,
                titleRouteGen: null,
            };
        }

        function snapshot() {
            return {
                version: state.version,
                mode: state.mode,
                mainTabId: state.mainTabId,
                focusedTabId: state.focusedTabId,
                tabs: state.tabs.map(copyTab),
            };
        }

        function commitUrl(tab, mode) {
            const href = hrefWithHash(tab.route);
            const nextState = mergeHistoryState(getBrowserState(), tab);
            if (mode === 'push' && typeof historyAdapter.pushState === 'function') {
                historyAdapter.pushState(nextState, href);
            } else if (typeof historyAdapter.replaceState === 'function') {
                historyAdapter.replaceState(nextState, href);
            }
            markHandled();
        }

        function patchHistoryState() {
            const tab = getMainTab();
            if (!tab || typeof historyAdapter.replaceState !== 'function') return;
            historyAdapter.replaceState(mergeHistoryState(getBrowserState(), tab), hrefWithHash(tab.route));
            markHandled();
        }

        function invokeRender(options) {
            lastRenderGen += 1;
            const opts = Object.assign({ leaveApproved: true, fromWorkspace: true }, options || {});
            return renderRoute(opts);
        }

        function setMain(tabId) {
            state.mainTabId = tabId;
            state.focusedTabId = tabId;
        }

        function navigateTabHistory(tab, hash, replace) {
            if (replace) {
                tab.history[tab.historyIndex] = hash;
                applyTabRoute(tab, hash);
                return;
            }
            if (tab.route === hash) {
                applyTabRoute(tab, hash);
                return;
            }
            tab.history = tab.history.slice(0, tab.historyIndex + 1);
            if (tab.history[tab.history.length - 1] === hash) {
                tab.historyIndex = tab.history.length - 1;
                applyTabRoute(tab, hash);
                return;
            }
            tab.history.push(hash);
            tab.historyIndex = tab.history.length - 1;
            applyTabRoute(tab, hash);
        }

        function awaitLeave(nextHash) {
            try {
                return Promise.resolve(canLeave(nextHash));
            } catch (_e) {
                return Promise.resolve(false);
            }
        }

        function bootstrap(initialHash) {
            const hash = canonical(initialHash != null ? initialHash : getHash());
            seq = 0;
            lastHandledHref = '';
            lastRenderGen = 0;
            const tab = makeTab(hash);
            state.tabs = [tab];
            setMain(tab.id);
            commitUrl(tab, 'replace');
            onChange();
            return snapshot();
        }

        function adoptLocation() {
            const hash = canonical(getHash());
            let tab = getMainTab();
            if (!tab) {
                bootstrap(hash);
                return;
            }
            if (tab.route !== hash) {
                navigateTabHistory(tab, hash, false);
            }
            patchHistoryState();
            onChange();
        }

        function openTab(hash, options) {
            const opts = options || {};
            const shouldActivate = opts.activate === true;
            const route = canonical(hash);
            if (!shouldActivate) {
                const tab = makeTab(route);
                state.tabs.push(tab);
                onChange();
                announce(tab.title);
                return Promise.resolve(copyTab(tab));
            }
            return awaitLeave(route).then(function (ok) {
                if (!ok) return false;
                const tab = makeTab(route);
                state.tabs.push(tab);
                setMain(tab.id);
                commitUrl(tab, 'replace');
                onChange();
                return Promise.resolve(invokeRender({ workspaceSwitch: true })).then(function () {
                    return copyTab(tab);
                });
            });
        }

        function navigate(hash, options) {
            const opts = options || {};
            const target = opts.target || 'current';
            if (target !== 'current' && target !== 'new-tab') return Promise.resolve(false);
            const route = canonical(hash);
            if (target === 'new-tab') {
                const activate = opts.activate === true;
                return openTab(route, { activate: activate });
            }
            const tab = getMainTab();
            if (!tab) {
                bootstrap(route);
                return Promise.resolve(invokeRender({ workspaceSwitch: false }));
            }
            const replace = !!opts.replace;
            const unchanged = tab.route === route && !replace;
            return awaitLeave(route).then(function (ok) {
                if (!ok) return false;
                const same = tab.route === route;
                navigateTabHistory(tab, route, replace || same);
                commitUrl(tab, replace || same ? 'replace' : 'push');
                onChange();
                if (unchanged && same && !replace) {
                    return Promise.resolve(invokeRender({ workspaceSwitch: false }));
                }
                return Promise.resolve(invokeRender({ workspaceSwitch: false }));
            });
        }

        function activateTab(tabId, options) {
            const opts = options || {};
            const tab = getTab(tabId);
            if (!tab) return Promise.resolve(false);
            if (tab.id === state.mainTabId && !opts.fromPopstate) {
                onChange();
                return Promise.resolve(true);
            }
            return awaitLeave(tab.route).then(function (ok) {
                if (!ok) return false;
                if (!getTab(tabId)) return false;
                setMain(tabId);
                commitUrl(tab, 'replace');
                onChange();
                return Promise.resolve(
                    invokeRender({
                        workspaceSwitch: !opts.fromPopstate,
                        fromPopstate: !!opts.fromPopstate,
                    })
                ).then(function () {
                    return true;
                });
            });
        }

        function closeTab(tabId) {
            const idx = tabIndex(tabId);
            if (idx < 0) return Promise.resolve(false);
            const closing = state.tabs[idx];
            const closingMain = closing.id === state.mainTabId;
            if (!closingMain) {
                state.tabs.splice(idx, 1);
                onChange();
                return Promise.resolve(true);
            }
            let successor = state.tabs[idx + 1] || state.tabs[idx - 1] || null;
            const nextHash = successor ? successor.route : homeHash;
            return awaitLeave(nextHash).then(function (ok) {
                if (!ok) return false;
                const i = tabIndex(tabId);
                if (i < 0) return false;
                if (!successor) {
                    state.tabs.splice(i, 1);
                    const home = makeTab(homeHash);
                    state.tabs.push(home);
                    setMain(home.id);
                    commitUrl(home, 'replace');
                    onChange();
                    return Promise.resolve(invokeRender({ workspaceSwitch: true })).then(function () {
                        return true;
                    });
                }
                const next = state.tabs[i + 1] || state.tabs[i - 1];
                state.tabs.splice(i, 1);
                setMain(next.id);
                commitUrl(next, 'replace');
                onChange();
                return Promise.resolve(invokeRender({ workspaceSwitch: true })).then(function () {
                    return true;
                });
            });
        }

        function setResolvedTitle(hash, title, routeGen) {
            const tab = getMainTab();
            if (!tab) return false;
            const want = canonical(hash);
            if (tab.route !== want) return false;
            if (routeGen != null) {
                if (isRouteGenCurrent) {
                    if (!isRouteGenCurrent(routeGen)) return false;
                } else if (tab.titleRouteGen != null && routeGen !== tab.titleRouteGen) {
                    return false;
                }
            }
            const text = String(title == null ? '' : title);
            if (!text) return false;
            tab.title = text;
            if (routeGen != null) tab.titleRouteGen = routeGen;
            onChange();
            return true;
        }

        function applyPopState(raw, locHash) {
            if (raw && raw.tabId) {
                const tab = getTab(raw.tabId);
                if (tab) {
                    const idx = Number(raw.historyIndex);
                    if (Number.isFinite(idx) && idx >= 0 && idx < tab.history.length) {
                        tab.historyIndex = idx;
                        applyTabRoute(tab, tab.history[idx]);
                    } else {
                        applyTabRoute(tab, locHash || canonical(raw.route));
                    }
                    setMain(tab.id);
                    markHandled();
                    onChange();
                    return invokeRender({ workspaceSwitch: false, fromPopstate: true });
                }
            }
            const main = getMainTab();
            if (!main) {
                bootstrap(locHash);
                return invokeRender({ fromPopstate: true });
            }
            applyTabRoute(main, locHash);
            if (main.history[main.historyIndex] !== locHash) {
                main.history[main.historyIndex] = locHash;
            }
            patchHistoryState();
            onChange();
            return invokeRender({ workspaceSwitch: false, fromPopstate: true });
        }

        function handlePopState(eventState) {
            const raw = eventState && eventState.prksWorkspace ? eventState.prksWorkspace : null;
            const locHash = canonical(getHash());
            return awaitLeave(locHash).then(function (ok) {
                if (!ok) {
                    const main = getMainTab();
                    if (main) commitUrl(main, 'replace');
                    return false;
                }
                return Promise.resolve(applyPopState(raw, locHash)).then(function () {
                    return true;
                });
            });
        }

        function handleHashChange() {
            if (isDuplicateLocation()) return true;
            return false;
        }

        function peekRenderGen() {
            return lastRenderGen;
        }

        return {
            bootstrap: bootstrap,
            navigate: navigate,
            openTab: openTab,
            activateTab: activateTab,
            closeTab: closeTab,
            setResolvedTitle: setResolvedTitle,
            snapshot: snapshot,
            adoptLocation: adoptLocation,
            handlePopState: handlePopState,
            handleHashChange: handleHashChange,
            isDuplicateLocation: isDuplicateLocation,
            markHandled: markHandled,
            peekRenderGen: peekRenderGen,
            getMainTabId: function () {
                return state.mainTabId;
            },
        };
    }

    function defaultHistoryAdapter() {
        return {
            getHash: function () {
                if (typeof root.location === 'undefined' || root.location.hash == null) return defaultHome();
                return root.location.hash || defaultHome();
            },
            getHref: function () {
                if (typeof root.location === 'undefined') return defaultHome();
                return root.location.href;
            },
            getState: function () {
                if (typeof root.history === 'undefined') return null;
                return root.history.state;
            },
            pushState: function (state, url) {
                if (typeof root.history !== 'undefined' && typeof root.history.pushState === 'function') {
                    root.history.pushState(state, '', url);
                    return;
                }
                if (typeof root.location !== 'undefined') root.location.hash = hashFromHref(url, defaultHome());
            },
            replaceState: function (state, url) {
                if (typeof root.history !== 'undefined' && typeof root.history.replaceState === 'function') {
                    root.history.replaceState(state, '', url);
                    return;
                }
                if (typeof root.location !== 'undefined') root.location.hash = hashFromHref(url, defaultHome());
            },
        };
    }

    let production = null;
    let productionReady = false;
    let tabFocusIndex = 0;
    const priorNavigate = root.prksNavigate;

    function iconHtml(name) {
        if (typeof root.prksIcon === 'function') {
            return root.prksIcon(name, { size: 'sm', className: 'prks-workspace-tab__icon-svg' });
        }
        return '';
    }

    function liveEl() {
        if (typeof document === 'undefined') return null;
        return document.getElementById('prks-workspace-live');
    }

    function announce(title) {
        const el = liveEl();
        if (!el) return;
        const label = String(title || 'page');
        el.textContent = '';
        el.textContent = 'Opened ' + label + ' in a new PRKS tab';
    }

    function paintProduction() {
        if (typeof document === 'undefined' || !production) return;
        const list = document.getElementById('prks-workspace-tabs');
        if (!list) return;
        const snap = production.snapshot();
        list.replaceChildren();
        snap.tabs.forEach(function (tab, i) {
            const isMain = tab.id === snap.mainTabId;
            const wrap = document.createElement('div');
            wrap.className = 'prks-workspace-tab' + (isMain ? ' is-main' : ' is-parked');
            wrap.setAttribute('data-tab-id', tab.id);

            const activate = document.createElement('button');
            activate.type = 'button';
            activate.className = 'prks-workspace-tab__activate';
            activate.setAttribute('role', 'tab');
            activate.setAttribute('aria-selected', isMain ? 'true' : 'false');
            activate.tabIndex = isMain ? 0 : -1;
            activate.title = tab.title;

            const icon = document.createElement('span');
            icon.className = 'prks-workspace-tab__icon';
            icon.setAttribute('aria-hidden', 'true');
            icon.innerHTML = iconHtml(tab.icon);

            const title = document.createElement('span');
            title.className = 'prks-workspace-tab__title';
            title.textContent = tab.title;

            activate.appendChild(icon);
            activate.appendChild(title);
            activate.addEventListener('click', function () {
                void production.activateTab(tab.id);
            });
            activate.addEventListener('keydown', onTabKeydown);

            const close = document.createElement('button');
            close.type = 'button';
            close.className = 'prks-workspace-tab__close';
            close.setAttribute('aria-label', 'Close ' + tab.title);
            close.title = 'Close';
            close.innerHTML = iconHtml('x');
            close.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                void production.closeTab(tab.id);
            });

            wrap.appendChild(activate);
            wrap.appendChild(close);
            list.appendChild(wrap);
            if (isMain) tabFocusIndex = i;
        });
        if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(list);
        const mainBtn = list.querySelector('.prks-workspace-tab.is-main .prks-workspace-tab__activate');
        if (mainBtn && typeof mainBtn.scrollIntoView === 'function') {
            mainBtn.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        }
    }

    function tabButtons() {
        if (typeof document === 'undefined') return [];
        const list = document.getElementById('prks-workspace-tabs');
        if (!list) return [];
        return Array.prototype.slice.call(list.querySelectorAll('.prks-workspace-tab__activate'));
    }

    function focusTabButton(index, scroll) {
        const buttons = tabButtons();
        if (!buttons.length) return;
        let i = index;
        if (i < 0) i = 0;
        if (i >= buttons.length) i = buttons.length - 1;
        tabFocusIndex = i;
        buttons.forEach(function (btn, n) {
            btn.tabIndex = n === i ? 0 : -1;
        });
        buttons[i].focus();
        if (scroll && typeof buttons[i].scrollIntoView === 'function') {
            buttons[i].scrollIntoView({ block: 'nearest', inline: 'nearest' });
        }
    }

    function onTabKeydown(e) {
        const buttons = tabButtons();
        if (!buttons.length) return;
        const key = e.key;
        if (key === 'ArrowLeft' || key === 'ArrowRight' || key === 'Home' || key === 'End') {
            e.preventDefault();
            let i = buttons.indexOf(e.currentTarget);
            if (i < 0) i = tabFocusIndex;
            if (key === 'ArrowLeft') i -= 1;
            else if (key === 'ArrowRight') i += 1;
            else if (key === 'Home') i = 0;
            else if (key === 'End') i = buttons.length - 1;
            if (i < 0) i = 0;
            if (i >= buttons.length) i = buttons.length - 1;
            focusTabButton(i, true);
            return;
        }
        if (key === 'Enter' || key === ' ') {
            e.preventDefault();
            const wrap = e.currentTarget.closest('[data-tab-id]');
            const id = wrap && wrap.getAttribute('data-tab-id');
            if (id && production) void production.activateTab(id);
        }
    }

    function internalHashFromAnchor(a) {
        if (!a || a.getAttribute('download') != null) return '';
        const target = a.getAttribute('target');
        if (target && target !== '_self') return '';
        const href = a.getAttribute('href') || '';
        if (!href) return '';
        if (href.charAt(0) === '#' && href.charAt(1) === '/') return href;
        try {
            const u = new URL(href, root.location ? root.location.href : 'http://127.0.0.1/');
            const here = root.location || u;
            if (u.origin !== here.origin) return '';
            if (u.hash && u.hash.charAt(1) === '/') return u.hash;
        } catch (_e) {
            return '';
        }
        return '';
    }

    function nestedInteractive(target, rootEl) {
        if (!target || !target.closest || !rootEl) return false;
        const el = target.closest(INTERACTIVE_NEST);
        if (!el || el === rootEl) return false;
        return rootEl.contains(el);
    }

    function isPdfWikiLink(target) {
        return !!(target && target.closest && target.closest('a.wiki-link-pdf-ann'));
    }

    function handleNavEvent(e) {
        if (!e || !e.target || !e.target.closest) return;
        if (isPdfWikiLink(e.target)) return;
        const intent = prksWorkspaceNavigationIntent(e);
        if (intent === 'ignore') return;
        const navEl = e.target.closest('[data-prks-route], a[href]');
        if (!navEl) return;
        if (nestedInteractive(e.target, navEl)) return;
        let hash = '';
        if (navEl.hasAttribute('data-prks-route')) {
            hash = navEl.getAttribute('data-prks-route') || '';
        } else {
            hash = internalHashFromAnchor(navEl);
        }
        if (!hash || hash.charAt(0) !== '#' || hash.charAt(1) !== '/') return;
        if (e.target.closest && e.target.closest('.sidebar-brand')) {
            const loc = (root.location && root.location.hash) || defaultHome();
            if (hash === defaultHome() && (loc === defaultHome() || loc === '' || loc === '#')) {
                return;
            }
        }
        e.preventDefault();
        e.stopPropagation();
        if (intent === 'background') {
            void prksWorkspaceNavigate(hash, { target: 'new-tab', activate: false });
            return;
        }
        void prksWorkspaceNavigate(hash, { target: 'current' });
    }

    function onMiddleMouseDown(e) {
        if (!e || e.button !== 1 || !e.target || !e.target.closest) return;
        if (isPdfWikiLink(e.target)) return;
        const navEl = e.target.closest('[data-prks-route], a[href], [data-prks-middleclick-nav]');
        if (!navEl) return;
        let hash = '';
        if (navEl.hasAttribute('data-prks-route')) hash = navEl.getAttribute('data-prks-route') || '';
        else if (navEl.tagName === 'A' || navEl.closest('a[href]')) {
            const a = navEl.tagName === 'A' ? navEl : navEl.closest('a[href]');
            hash = internalHashFromAnchor(a);
        }
        if (hash || navEl.hasAttribute('data-prks-middleclick-nav') || navEl.hasAttribute('data-prks-route')) {
            e.preventDefault();
        }
    }

    function bindNewTabButton() {
        if (typeof document === 'undefined') return;
        const btn = document.getElementById('prks-workspace-new-tab');
        if (!btn || btn.getAttribute('data-workspace-bound') === '1') return;
        btn.setAttribute('data-workspace-bound', '1');
        btn.addEventListener('click', function () {
            if (typeof root.prksOpenCommandPalette === 'function') {
                root.prksOpenCommandPalette({ scope: 'all', navigationTarget: 'new-tab' });
            }
        });
    }

    function bindLinkLayer() {
        if (typeof document === 'undefined' || document.documentElement.getAttribute('data-prks-workspace-nav') === '1') {
            return;
        }
        document.documentElement.setAttribute('data-prks-workspace-nav', '1');
        document.addEventListener('click', handleNavEvent, true);
        document.addEventListener('auxclick', handleNavEvent, true);
        document.addEventListener('mousedown', onMiddleMouseDown, true);
    }

    function bindHistory() {
        if (typeof root.addEventListener !== 'function') return;
        if (root.__prksWorkspaceHistoryBound) return;
        root.__prksWorkspaceHistoryBound = true;
        root.addEventListener('popstate', function (ev) {
            if (!production) return;
            void production.handlePopState(ev && ev.state);
        });
        root.addEventListener('hashchange', function () {
            if (!production) {
                if (typeof root.handleRoute === 'function') void root.handleRoute();
                return;
            }
            if (production.handleHashChange()) return;
            if (typeof root.handleRoute === 'function') void root.handleRoute({ fromWorkspace: false });
            production.markHandled();
        });
    }

    function ensureProduction() {
        if (production) return production;
        production = createPrksWorkspaceTabs({
            parseRoute: root.prksParseRoute,
            routeLoadingTitle: root.prksRouteLoadingTitle,
            routeTabIcon: root.prksRouteTabIcon,
            homeHash: defaultHome(),
            historyAdapter: defaultHistoryAdapter(),
            isRouteGenCurrent: function (routeGen) {
                if (typeof root.prksIsRouteGenCurrent === 'function') return root.prksIsRouteGenCurrent(routeGen);
                return true;
            },
            canLeave: function (nextHash) {
                if (typeof root.prksCanLeaveCurrentRoute === 'function') {
                    return root.prksCanLeaveCurrentRoute(nextHash);
                }
                return true;
            },
            renderRoute: function (options) {
                if (typeof root.handleRoute === 'function') return root.handleRoute(options);
                return undefined;
            },
            onChange: paintProduction,
            announce: announce,
        });
        return production;
    }

    function prksWorkspaceInit() {
        const ws = ensureProduction();
        if (!productionReady) {
            ws.bootstrap();
            productionReady = true;
        }
        bindLinkLayer();
        bindHistory();
        bindNewTabButton();
        paintProduction();
        root.__prksWorkspaceReady = true;
        return ws.snapshot();
    }

    function prksWorkspaceNavigate(hash, options) {
        const ws = ensureProduction();
        if (!productionReady) {
            ws.bootstrap();
            productionReady = true;
        }
        return ws.navigate(hash, options);
    }

    function prksWorkspaceOpenTab(hash, options) {
        const ws = ensureProduction();
        if (!productionReady) {
            ws.bootstrap();
            productionReady = true;
        }
        return ws.openTab(hash, options);
    }

    function prksWorkspaceActivateTab(tabId) {
        if (!production) return Promise.resolve(false);
        return production.activateTab(tabId);
    }

    function prksWorkspaceCloseTab(tabId) {
        if (!production) return Promise.resolve(false);
        return production.closeTab(tabId);
    }

    function prksWorkspaceAdoptLocation() {
        if (!production) return;
        production.adoptLocation();
    }

    function prksWorkspaceSetResolvedTitle(hash, title, routeGen) {
        if (!production) return false;
        return production.setResolvedTitle(hash, title, routeGen);
    }

    function prksWorkspaceSnapshot() {
        if (!production) {
            return {
                version: WORKSPACE_VERSION,
                mode: MODE_STACKED,
                mainTabId: null,
                focusedTabId: null,
                tabs: [],
            };
        }
        return production.snapshot();
    }

    function prksNavigate(hash, options) {
        if (productionReady && production) return production.navigate(hash, options);
        if (typeof priorNavigate === 'function') return priorNavigate(hash, options);
        if (typeof root.location !== 'undefined') {
            const route = typeof root.prksParseRoute === 'function' ? root.prksParseRoute(hash) : null;
            const target = (route && route.canonicalHash) || hash;
            root.location.hash = target;
        }
        return undefined;
    }

    const api = {
        createPrksWorkspaceTabs: createPrksWorkspaceTabs,
        prksWorkspaceNavigationIntent: prksWorkspaceNavigationIntent,
        prksWorkspaceInit: prksWorkspaceInit,
        prksWorkspaceNavigate: prksWorkspaceNavigate,
        prksWorkspaceOpenTab: prksWorkspaceOpenTab,
        prksWorkspaceActivateTab: prksWorkspaceActivateTab,
        prksWorkspaceCloseTab: prksWorkspaceCloseTab,
        prksWorkspaceSetResolvedTitle: prksWorkspaceSetResolvedTitle,
        prksWorkspaceAdoptLocation: prksWorkspaceAdoptLocation,
        prksWorkspaceSnapshot: prksWorkspaceSnapshot,
        prksNavigate: prksNavigate,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
