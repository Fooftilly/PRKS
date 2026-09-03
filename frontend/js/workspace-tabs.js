/**
 * Workspace tabs. Stacked mounts one TabContext; tiled mounts Main + one Secondary.
 * Parked tabs are state only: no DOM, fetch, or render.
 */
(function (root) {
    'use strict';

    const WORKSPACE_VERSION = 1;
    const MODE_STACKED = 'stacked';
    const MODE_TILED = 'tiled';
    const HOME_HASH = '#/folders';
    const INTERACTIVE_NEST =
        'button, a, input, select, textarea, [contenteditable="true"],' +
        '[role="button"], [role="link"], [role="menuitem"], [role="tab"],' +
        '[role="checkbox"], [role="radio"], [role="switch"]';
    const TILE_ROUTE_NAMES = {
        work: true,
        person: true,
        'concept-detail': true,
        'position-detail': true,
        'argument-detail': true,
        'playlist-detail': true,
    };

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

    function copySecondaryTree(tree) {
        if (!tree || tree.type !== 'leaf' || !tree.tabId) return null;
        return { type: 'leaf', tabId: String(tree.tabId) };
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
        if (ev.shiftKey) return 'ignore';
        const button = ev.button;
        if (button === 1) return 'background';
        if (button != null && button !== 0) return 'ignore';
        if (ev.altKey) return 'tile';
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
        const publishMainShell =
            typeof deps.publishMainShell === 'function' ? deps.publishMainShell : function () {};
        const refreshFocusedPanel =
            typeof deps.refreshFocusedPanel === 'function' ? deps.refreshFocusedPanel : function () {};
        const supportsTileFn =
            typeof deps.supportsTile === 'function' ? deps.supportsTile : null;

        let seq = 0;
        let lastHandledHref = '';
        let lastRenderGen = 0;
        let narrowFallback = false;
        const state = {
            version: WORKSPACE_VERSION,
            mode: MODE_STACKED,
            mainTabId: null,
            focusedTabId: null,
            secondaryTree: null,
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

        function routeSupportsTile(hash) {
            if (supportsTileFn) return !!supportsTileFn(hash);
            if (typeof root.prksRouteSupportsTile === 'function') return !!root.prksRouteSupportsTile(hash);
            const route = parseRoute(hash);
            return !!(route && TILE_ROUTE_NAMES[route.name]);
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

        function secondaryTabId() {
            const tree = state.secondaryTree;
            if (!tree || tree.type !== 'leaf' || !tree.tabId) return null;
            if (tree.tabId === state.mainTabId) return null;
            return tree.tabId;
        }

        function visualTiled() {
            return state.mode === MODE_TILED && !narrowFallback && !!secondaryTabId();
        }

        function isVisibleTab(tabId) {
            if (!tabId) return false;
            if (tabId === state.mainTabId) return true;
            return visualTiled() && tabId === secondaryTabId();
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
                secondaryTree: copySecondaryTree(state.secondaryTree),
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

        function parkContext(tabId) {
            if (tabId && typeof root.prksUnmountTabContext === 'function') {
                root.prksUnmountTabContext(tabId, 'park');
            }
        }

        function hostForTab(tabId) {
            if (typeof root.prksWorkspaceHostForTab === 'function') {
                const tiled = root.prksWorkspaceHostForTab(tabId);
                if (tiled) return tiled;
            }
            if (typeof root.prksTabContextHost === 'function') return root.prksTabContextHost();
            if (typeof document !== 'undefined') return document.getElementById('page-content');
            return null;
        }

        function mountContext(tabId) {
            if (!tabId || typeof root.prksMountTabContext !== 'function') return;
            const host = hostForTab(tabId);
            if (host) root.prksMountTabContext(tabId, host);
        }

        function destroyContext(tabId) {
            if (tabId && typeof root.prksDestroyTabContext === 'function') {
                root.prksDestroyTabContext(tabId);
            }
        }

        function resetAllContexts() {
            if (typeof root.prksDestroyAllTabContexts === 'function') {
                root.prksDestroyAllTabContexts();
            }
        }

        function contextMounted(tabId) {
            if (!tabId || typeof root.prksGetTabContext !== 'function') return false;
            const ctx = root.prksGetTabContext(tabId);
            return !!(ctx && ctx.mounted);
        }

        function invokeRender(options) {
            lastRenderGen += 1;
            const optsIn = options || {};
            const tab = optsIn.tabId ? getTab(optsIn.tabId) : getMainTab();
            const opts = Object.assign(
                {
                    leaveApproved: true,
                    fromWorkspace: true,
                    tabId: tab ? tab.id : null,
                    hash: tab ? tab.route : null,
                },
                optsIn
            );
            return renderRoute(opts);
        }

        function setMain(tabId) {
            state.mainTabId = tabId;
            state.focusedTabId = tabId;
        }

        function clearSecondaryIf(tabId) {
            if (secondaryTabId() === tabId || (state.secondaryTree && state.secondaryTree.tabId === tabId)) {
                state.secondaryTree = null;
                state.mode = MODE_STACKED;
            }
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

        function awaitLeave(tabId, nextHash) {
            try {
                return Promise.resolve(canLeave(tabId, nextHash));
            } catch (_e) {
                return Promise.resolve(false);
            }
        }

        function enforceInvariants() {
            if (!state.mainTabId && state.tabs.length) state.mainTabId = state.tabs[0].id;
            const sec = secondaryTabId();
            if (state.secondaryTree && !sec) {
                if (!getTab(state.secondaryTree.tabId) || state.secondaryTree.tabId === state.mainTabId) {
                    state.secondaryTree = null;
                }
            }
            if (state.mode === MODE_TILED && !secondaryTabId()) state.mode = MODE_STACKED;
            if (state.mode === MODE_STACKED) {
                state.focusedTabId = state.mainTabId;
            } else if (state.focusedTabId !== state.mainTabId && state.focusedTabId !== secondaryTabId()) {
                state.focusedTabId = state.mainTabId;
            }
        }

        function paint() {
            enforceInvariants();
            onChange();
            if (typeof root.prksWorkspaceSyncTiles === 'function') {
                root.prksWorkspaceSyncTiles(snapshot(), { visualMode: visualTiled() ? MODE_TILED : MODE_STACKED });
            }
        }

        function paintFocus() {
            if (typeof root.prksWorkspaceApplyFocus === 'function') {
                root.prksWorkspaceApplyFocus(snapshot(), { visualMode: visualTiled() ? MODE_TILED : MODE_STACKED });
            } else {
                paint();
            }
        }

        function bootstrap(initialHash) {
            const hash = canonical(initialHash != null ? initialHash : getHash());
            seq = 0;
            lastHandledHref = '';
            lastRenderGen = 0;
            narrowFallback = false;
            resetAllContexts();
            const tab = makeTab(hash);
            state.tabs = [tab];
            state.secondaryTree = null;
            state.mode = MODE_STACKED;
            setMain(tab.id);
            paint();
            mountContext(tab.id);
            commitUrl(tab, 'replace');
            paint();
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
            paint();
        }

        function openTab(hash, options) {
            const opts = options || {};
            const shouldActivate = opts.activate === true;
            const route = canonical(hash);
            if (!shouldActivate) {
                const tab = makeTab(route);
                state.tabs.push(tab);
                paint();
                announce(tab.title);
                return Promise.resolve(copyTab(tab));
            }
            return awaitLeave(state.mainTabId, route).then(function (ok) {
                if (!ok) return false;
                const prevId = state.mainTabId;
                const tab = makeTab(route);
                state.tabs.push(tab);
                if (prevId && prevId !== tab.id) parkContext(prevId);
                setMain(tab.id);
                mountContext(tab.id);
                commitUrl(tab, 'replace');
                paint();
                return Promise.resolve(
                    invokeRender({ workspaceSwitch: true, tabId: tab.id, hash: tab.route })
                ).then(function () {
                    return copyTab(tab);
                });
            });
        }

        function makeMain(tabId) {
            const tab = getTab(tabId);
            if (!tab) return false;
            if (tab.id === state.mainTabId) {
                state.focusedTabId = tab.id;
                paint();
                refreshFocusedPanel();
                return true;
            }
            if (tab.id !== secondaryTabId()) return false;
            const oldMain = state.mainTabId;
            state.mainTabId = tab.id;
            state.secondaryTree = { type: 'leaf', tabId: oldMain };
            state.mode = MODE_TILED;
            state.focusedTabId = tab.id;
            commitUrl(tab, 'replace');
            paint();
            publishMainShell(tab.id);
            refreshFocusedPanel();
            return true;
        }

        function focusTab(tabId) {
            if (!getTab(tabId)) return false;
            if (!isVisibleTab(tabId)) return false;
            if (state.mode === MODE_STACKED && tabId !== state.mainTabId) return false;
            if (state.focusedTabId === tabId) return true;
            state.focusedTabId = tabId;
            paintFocus();
            refreshFocusedPanel();
            return true;
        }

            function mountAndRenderSecondary(tab) {
            state.mode = MODE_TILED;
            state.focusedTabId = tab.id;
            paint();
            mountContext(tab.id);
            announce(tab.title, 'tile');
            return Promise.resolve(
                invokeRender({
                    workspaceSwitch: true,
                    tabId: tab.id,
                    hash: tab.route,
                    leaveApproved: true,
                })
            ).then(function () {
                return copyTab(tab);
            });
        }

        function tileTab(tabId) {
            const tab = getTab(tabId);
            if (!tab) return Promise.resolve(false);
            if (tab.id === state.mainTabId) return Promise.resolve(false);
            const curSec = secondaryTabId();
            if (curSec === tab.id) {
                state.mode = MODE_TILED;
                state.focusedTabId = tab.id;
                paint();
                if (!contextMounted(tab.id)) {
                    mountContext(tab.id);
                    return Promise.resolve(
                        invokeRender({
                            workspaceSwitch: true,
                            tabId: tab.id,
                            hash: tab.route,
                            leaveApproved: true,
                        })
                    ).then(function () {
                        return copyTab(tab);
                    });
                }
                refreshFocusedPanel();
                return Promise.resolve(copyTab(tab));
            }
            const replaceId = curSec;
            const proceed = function () {
                if (replaceId) parkContext(replaceId);
                state.secondaryTree = { type: 'leaf', tabId: tab.id };
                return mountAndRenderSecondary(tab);
            };
            if (!replaceId || !visualTiled()) {
                return Promise.resolve(proceed());
            }
            return awaitLeave(replaceId, tab.route).then(function (ok) {
                if (!ok) return false;
                if (!getTab(tabId)) return false;
                return proceed();
            });
        }

        function navigateTile(hash, options) {
            const route = canonical(hash);
            if (!routeSupportsTile(route)) {
                announce('', 'promote');
                return openTab(route, { activate: true });
            }
            const opts = options || {};
            if (opts.tabId && getTab(opts.tabId) && opts.tabId !== state.mainTabId && getTab(opts.tabId).route === route) {
                return tileTab(opts.tabId);
            }
            const curSec = secondaryTabId();
            const proceedCreate = function () {
                if (curSec) parkContext(curSec);
                const tab = makeTab(route);
                state.tabs.push(tab);
                state.secondaryTree = { type: 'leaf', tabId: tab.id };
                return mountAndRenderSecondary(tab);
            };
            if (!curSec || !visualTiled()) {
                return Promise.resolve(proceedCreate());
            }
            return awaitLeave(curSec, route).then(function (ok) {
                if (!ok) return false;
                return proceedCreate();
            });
        }

        function navigate(hash, options) {
            const opts = options || {};
            const target = opts.target || 'current';
            if (target !== 'current' && target !== 'new-tab' && target !== 'tile') {
                return Promise.resolve(false);
            }
            const route = canonical(hash);
            if (target === 'new-tab') {
                const activate = opts.activate === true;
                return openTab(route, { activate: activate });
            }
            if (target === 'tile') {
                return navigateTile(route, opts);
            }
            let tab = opts.tabId ? getTab(opts.tabId) : null;
            if (!tab) tab = getTab(state.focusedTabId) || getMainTab();
            if (!tab) {
                bootstrap(route);
                return Promise.resolve(invokeRender({ workspaceSwitch: false }));
            }
            const promoteUnsupported = tab.id !== state.mainTabId && !routeSupportsTile(route);
            if (promoteUnsupported) {
                makeMain(tab.id);
                announce('', 'promote');
                tab = getMainTab();
            }
            const replace = !!opts.replace;
            const unchanged = tab.route === route && !replace;
            const isMainNav = tab.id === state.mainTabId;
            return awaitLeave(tab.id, route).then(function (ok) {
                if (!ok) return false;
                if (!getTab(tab.id)) return false;
                const same = tab.route === route;
                navigateTabHistory(tab, route, replace || same);
                if (isMainNav) commitUrl(tab, replace || same ? 'replace' : 'push');
                paint();
                void unchanged;
                return Promise.resolve(invokeRender({ workspaceSwitch: false, tabId: tab.id, hash: tab.route }));
            });
        }

        function activateTab(tabId, options) {
            const opts = options || {};
            const tab = getTab(tabId);
            if (!tab) return Promise.resolve(false);
            if (tab.id === state.mainTabId && !opts.fromPopstate) {
                state.focusedTabId = tab.id;
                paint();
                refreshFocusedPanel();
                return Promise.resolve(true);
            }
            if (tab.id === secondaryTabId() && visualTiled() && !opts.fromPopstate) {
                return Promise.resolve(makeMain(tab.id));
            }
            return awaitLeave(state.mainTabId, tab.route).then(function (ok) {
                if (!ok) return false;
                if (!getTab(tabId)) return false;
                const prevId = state.mainTabId;
                if (prevId && prevId !== tabId) parkContext(prevId);
                if (secondaryTabId() === tabId) {
                    state.secondaryTree = prevId ? { type: 'leaf', tabId: prevId } : null;
                }
                setMain(tabId);
                mountContext(tabId);
                commitUrl(tab, 'replace');
                paint();
                return Promise.resolve(
                    invokeRender({
                        workspaceSwitch: !opts.fromPopstate,
                        fromPopstate: !!opts.fromPopstate,
                        tabId: tab.id,
                        hash: tab.route,
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
            const closingSec = closing.id === secondaryTabId() || (state.secondaryTree && state.secondaryTree.tabId === closing.id);
            if (!closingMain) {
                const needLeave = closingSec && visualTiled();
                const leaveP = needLeave ? awaitLeave(closing.id, homeHash) : Promise.resolve(true);
                return leaveP.then(function (ok) {
                    if (!ok) return false;
                    if (!getTab(tabId)) return false;
                    destroyContext(closing.id);
                    const i = tabIndex(tabId);
                    if (i >= 0) state.tabs.splice(i, 1);
                    if (closingSec) {
                        state.secondaryTree = null;
                        state.mode = MODE_STACKED;
                        state.focusedTabId = state.mainTabId;
                    }
                    paint();
                    return true;
                });
            }
            const secId = secondaryTabId() || (state.secondaryTree && state.secondaryTree.tabId);
            let successor = secId && secId !== closing.id ? getTab(secId) : null;
            if (!successor) successor = state.tabs[idx + 1] || state.tabs[idx - 1] || null;
            if (successor && successor.id === closing.id) successor = null;
            const nextHash = successor ? successor.route : homeHash;
            return awaitLeave(closing.id, nextHash).then(function (ok) {
                if (!ok) return false;
                const i = tabIndex(tabId);
                if (i < 0) return false;
                const wasMountedSuccessor = successor && contextMounted(successor.id);
                destroyContext(tabId);
                const j = tabIndex(tabId);
                if (j >= 0) state.tabs.splice(j, 1);
                state.secondaryTree = null;
                state.mode = MODE_STACKED;
                if (!successor) {
                    const home = makeTab(homeHash);
                    state.tabs.push(home);
                    setMain(home.id);
                    paint();
                    mountContext(home.id);
                    commitUrl(home, 'replace');
                    paint();
                    return Promise.resolve(
                        invokeRender({ workspaceSwitch: true, tabId: home.id, hash: home.route })
                    ).then(function () {
                        return true;
                    });
                }
                setMain(successor.id);
                if (!wasMountedSuccessor) mountContext(successor.id);
                commitUrl(successor, 'replace');
                paint();
                if (wasMountedSuccessor) {
                    publishMainShell(successor.id);
                    refreshFocusedPanel();
                    return true;
                }
                return Promise.resolve(
                    invokeRender({ workspaceSwitch: true, tabId: successor.id, hash: successor.route })
                ).then(function () {
                    return true;
                });
            });
        }

        function setMode(mode) {
            if (mode !== MODE_STACKED && mode !== MODE_TILED) return Promise.resolve(false);
            if (mode === MODE_TILED) {
                const sec = secondaryTabId();
                if (!sec) return Promise.resolve(false);
                state.mode = MODE_TILED;
                state.focusedTabId = state.mainTabId;
                paint();
                if (!contextMounted(sec) && !narrowFallback) {
                    mountContext(sec);
                    const tab = getTab(sec);
                    return Promise.resolve(
                        invokeRender({
                            workspaceSwitch: true,
                            tabId: sec,
                            hash: tab ? tab.route : null,
                            leaveApproved: true,
                        })
                    ).then(function () {
                        return true;
                    });
                }
                return Promise.resolve(true);
            }
            if (state.mode === MODE_STACKED && !visualTiled()) return Promise.resolve(true);
            const sec = secondaryTabId();
            const leaveP =
                sec && visualTiled() ? awaitLeave(sec, homeHash) : Promise.resolve(true);
            return leaveP.then(function (ok) {
                if (!ok) return false;
                if (sec) parkContext(sec);
                state.mode = MODE_STACKED;
                state.focusedTabId = state.mainTabId;
                paint();
                refreshFocusedPanel();
                return true;
            });
        }

        function setNarrowFallback(narrow) {
            const next = !!narrow;
            if (next === narrowFallback) return Promise.resolve(true);
            const sec = secondaryTabId();
            if (next) {
                if (sec && contextMounted(sec)) parkContext(sec);
                narrowFallback = true;
                state.focusedTabId = state.mainTabId;
                paint();
                refreshFocusedPanel();
                return Promise.resolve(true);
            }
            narrowFallback = false;
            if (state.mode === MODE_TILED && sec && !contextMounted(sec)) {
                paint();
                mountContext(sec);
                const tab = getTab(sec);
                return Promise.resolve(
                    invokeRender({
                        workspaceSwitch: true,
                        tabId: sec,
                        hash: tab ? tab.route : null,
                        leaveApproved: true,
                    })
                ).then(function () {
                    return true;
                });
            }
            paint();
            return Promise.resolve(true);
        }

        function setResolvedTitleForTab(tabId, hash, title, routeGen) {
            const tab = getTab(tabId);
            if (!tab) return false;
            const want = canonical(hash);
            if (tab.route !== want) return false;
            if (routeGen != null) {
                if (isRouteGenCurrent) {
                    if (!isRouteGenCurrent(routeGen, tab.id)) return false;
                } else if (tab.titleRouteGen != null && routeGen !== tab.titleRouteGen) {
                    return false;
                }
            }
            const text = String(title == null ? '' : title);
            if (!text) return false;
            tab.title = text;
            if (routeGen != null) tab.titleRouteGen = routeGen;
            paint();
            return true;
        }

        function setResolvedTitle(hash, title, routeGen) {
            const tab = getMainTab();
            if (!tab) return false;
            return setResolvedTitleForTab(tab.id, hash, title, routeGen);
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
                    const prevId = state.mainTabId;
                    if (tab.id === secondaryTabId()) {
                        state.secondaryTree = prevId && prevId !== tab.id ? { type: 'leaf', tabId: prevId } : null;
                    }
                    if (prevId && prevId !== tab.id) parkContext(prevId);
                    setMain(tab.id);
                    mountContext(tab.id);
                    markHandled();
                    paint();
                    return invokeRender({
                        workspaceSwitch: false,
                        fromPopstate: true,
                        tabId: tab.id,
                        hash: tab.route,
                    });
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
            mountContext(main.id);
            patchHistoryState();
            paint();
            return invokeRender({
                workspaceSwitch: false,
                fromPopstate: true,
                tabId: main.id,
                hash: main.route,
            });
        }

        function handlePopState(eventState) {
            const raw = eventState && eventState.prksWorkspace ? eventState.prksWorkspace : null;
            const locHash = canonical(getHash());
            return awaitLeave(state.mainTabId, locHash).then(function (ok) {
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
            focusTab: focusTab,
            makeMain: makeMain,
            tileTab: tileTab,
            setMode: setMode,
            setNarrowFallback: setNarrowFallback,
            setResolvedTitle: setResolvedTitle,
            setResolvedTitleForTab: setResolvedTitleForTab,
            snapshot: snapshot,
            adoptLocation: adoptLocation,
            handlePopState: handlePopState,
            handleHashChange: handleHashChange,
            isDuplicateLocation: isDuplicateLocation,
            markHandled: markHandled,
            peekRenderGen: peekRenderGen,
            visualTiled: visualTiled,
            getMainTabId: function () {
                return state.mainTabId;
            },
            getFocusedTabId: function () {
                return state.focusedTabId;
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

    function announce(title, kind) {
        const el = liveEl();
        if (!el) return;
        const label = String(title || 'page');
        el.textContent = '';
        if (kind === 'promote') {
            el.textContent = 'Opened as main because this view is not available in a tile yet.';
            return;
        }
        if (kind === 'tile') {
            el.textContent = 'Opened ' + label + ' as a tile';
            return;
        }
        el.textContent = 'Opened ' + label + ' in a new PRKS tab';
    }

    function paintProduction() {
        if (typeof document === 'undefined' || !production) return;
        const list = document.getElementById('prks-workspace-tabs');
        if (!list) return;
        const snap = production.snapshot();
        const secId =
            snap.secondaryTree && snap.secondaryTree.type === 'leaf' ? snap.secondaryTree.tabId : null;
        const visualTiled = typeof production.visualTiled === 'function' ? production.visualTiled() : snap.mode === MODE_TILED;
        list.replaceChildren();
        snap.tabs.forEach(function (tab, i) {
            const isMain = tab.id === snap.mainTabId;
            const isTiled = visualTiled && tab.id === secId && !isMain;
            const isFocused = tab.id === snap.focusedTabId;
            const isParked = !isMain && !isTiled;
            const wrap = document.createElement('div');
            wrap.className =
                'prks-workspace-tab' +
                (isMain ? ' is-main' : '') +
                (isTiled ? ' is-tiled' : '') +
                (isFocused ? ' is-focused' : '') +
                (isParked ? ' is-parked' : '');
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
        if (typeof root.prksWorkspaceSyncTiles === 'function') {
            root.prksWorkspaceSyncTiles(snap, { visualMode: visualTiled ? MODE_TILED : MODE_STACKED });
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

    function ownerTabIdFromEvent(e) {
        if (!e || !e.target) return null;
        if (typeof root.prksContextFromElement === 'function') {
            const ctx = root.prksContextFromElement(e.target);
            if (ctx && ctx.tabId) return ctx.tabId;
        }
        if (e.target.closest) {
            const tile = e.target.closest('.prks-tile[data-prks-tab-id]');
            if (tile) return tile.getAttribute('data-prks-tab-id');
        }
        return production ? production.getMainTabId() : null;
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
        const ownerTabId = ownerTabIdFromEvent(e);
        if (intent === 'background') {
            void prksWorkspaceNavigate(hash, { target: 'new-tab', activate: false });
            return;
        }
        if (intent === 'tile') {
            void prksWorkspaceNavigate(hash, { target: 'tile', tabId: ownerTabId });
            return;
        }
        void prksWorkspaceNavigate(hash, { target: 'current', tabId: ownerTabId });
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

    function bindTileLayoutButton() {
        if (typeof document === 'undefined') return;
        const btn = document.getElementById('prks-workspace-tile-layout');
        if (!btn || btn.getAttribute('data-workspace-bound') === '1') return;
        btn.setAttribute('data-workspace-bound', '1');
        btn.addEventListener('click', function () {
            if (!production) return;
            const snap = production.snapshot();
            const hasLeaf = !!(snap.secondaryTree && snap.secondaryTree.type === 'leaf' && snap.secondaryTree.tabId);
            if (!hasLeaf) {
                if (typeof root.prksOpenCommandPalette === 'function') {
                    root.prksOpenCommandPalette({ scope: 'all', navigationTarget: 'tile' });
                }
                return;
            }
            if (snap.mode === MODE_STACKED) {
                void production.setMode(MODE_TILED);
                return;
            }
            void production.setMode(MODE_STACKED);
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
            supportsTile: root.prksRouteSupportsTile,
            isRouteGenCurrent: function (routeGen, tabId) {
                if (typeof root.prksIsRouteGenCurrent === 'function') {
                    const ctx =
                        typeof root.prksGetTabContext === 'function' && tabId
                            ? root.prksGetTabContext(tabId)
                            : typeof root.prksGetMainTabContext === 'function'
                              ? root.prksGetMainTabContext()
                              : null;
                    return root.prksIsRouteGenCurrent(routeGen, ctx);
                }
                return true;
            },
            canLeave: function (tabId, nextHash) {
                if (typeof root.prksCanLeaveTabContext === 'function') {
                    const ctx =
                        typeof root.prksGetTabContext === 'function' && tabId
                            ? root.prksGetTabContext(tabId)
                            : typeof root.prksGetMainTabContext === 'function'
                              ? root.prksGetMainTabContext()
                              : null;
                    return root.prksCanLeaveTabContext(ctx, nextHash);
                }
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
            publishMainShell: function (tabId) {
                if (typeof root.prksPublishMainShell === 'function') {
                    const ctx =
                        typeof root.prksGetTabContext === 'function' ? root.prksGetTabContext(tabId) : null;
                    root.prksPublishMainShell(ctx);
                }
            },
            refreshFocusedPanel: function () {
                if (typeof root.prksRefreshFocusedRightPanel === 'function') {
                    root.prksRefreshFocusedRightPanel();
                }
            },
        });
        return production;
    }

    function prksWorkspaceInit() {
        const ws = ensureProduction();
        if (!productionReady) {
            if (typeof root.prksWorkspaceInitTiles === 'function') root.prksWorkspaceInitTiles();
            ws.bootstrap();
            productionReady = true;
        }
        bindLinkLayer();
        bindHistory();
        bindNewTabButton();
        bindTileLayoutButton();
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

    function prksWorkspaceFocusTab(tabId) {
        if (!production) return false;
        return production.focusTab(tabId);
    }

    function prksWorkspaceMakeMain(tabId) {
        if (!production) return false;
        return production.makeMain(tabId);
    }

    function prksWorkspaceTileTab(tabId) {
        if (!production) return Promise.resolve(false);
        return production.tileTab(tabId);
    }

    function prksWorkspaceSetMode(mode) {
        if (!production) return Promise.resolve(false);
        return production.setMode(mode);
    }

    function prksWorkspaceSetNarrowFallback(narrow) {
        if (!production) return Promise.resolve(false);
        return production.setNarrowFallback(narrow);
    }

    function prksWorkspaceAdoptLocation() {
        if (!production) return;
        production.adoptLocation();
    }

    function prksWorkspaceSetResolvedTitle(hash, title, routeGen) {
        if (!production) return false;
        return production.setResolvedTitle(hash, title, routeGen);
    }

    function prksWorkspaceSetResolvedTitleForTab(tabId, hash, title, routeGen) {
        if (!production || typeof production.setResolvedTitleForTab !== 'function') return false;
        return production.setResolvedTitleForTab(tabId, hash, title, routeGen);
    }

    function emptySnapshot() {
        return {
            version: WORKSPACE_VERSION,
            mode: MODE_STACKED,
            mainTabId: null,
            focusedTabId: null,
            secondaryTree: null,
            tabs: [],
        };
    }

    function prksWorkspaceSnapshot() {
        if (!production) return emptySnapshot();
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
        prksWorkspaceFocusTab: prksWorkspaceFocusTab,
        prksWorkspaceMakeMain: prksWorkspaceMakeMain,
        prksWorkspaceTileTab: prksWorkspaceTileTab,
        prksWorkspaceSetMode: prksWorkspaceSetMode,
        prksWorkspaceSetNarrowFallback: prksWorkspaceSetNarrowFallback,
        prksWorkspaceSetResolvedTitle: prksWorkspaceSetResolvedTitle,
        prksWorkspaceSetResolvedTitleForTab: prksWorkspaceSetResolvedTitleForTab,
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
