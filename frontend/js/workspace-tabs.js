/**
 * Workspace tabs. Stacked mounts one TabContext; tiled mounts Main plus a recursive
 * Secondary split tree of up to 3 more Secondary leaves (4 mounted TabContexts at most).
 * Parked tabs are state only: no DOM, fetch, or render.
 */
(function (root) {
    'use strict';

    function loadWorkspaceModel() {
        /* Require only when it is a real function. A VM that injects `module`
         * without `require` must use the classic-script global instead. */
        if (typeof module !== 'undefined' && module.exports && typeof require === 'function') {
            return require('./workspace-model.js');
        }
        if (!root.prksWorkspaceModel) throw new Error('PRKS workspace model is not loaded');
        return root.prksWorkspaceModel;
    }

    const workspaceModelApi = loadWorkspaceModel();
    const WORKSPACE_VERSION = workspaceModelApi.WORKSPACE_STATE_VERSION;
    const MODE_STACKED = 'stacked';
    const MODE_TILED = 'tiled';
    const HOME_HASH = '#/folders';
    /* Main region width / usable split width (usable excludes the separator track).
     * Canonical preference is persisted by workspace-persistence.js, not here. */
    const DEFAULT_MAIN_SPLIT_RATIO = workspaceModelApi.DEFAULT_MAIN_SPLIT_RATIO;
    /* 1 Main + up to 3 Secondary leaves = 4 mounted TabContexts at once, at most. */
    const PRKS_MAX_VISIBLE_TABS = workspaceModelApi.MAX_VISIBLE_PANES;
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
        /* Test seam. Production leaves through prksTabLeave probes and does not inject this. */
        const injectedCanLeave = typeof deps.canLeave === 'function' ? deps.canLeave : null;
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
        const onMountContext = typeof deps.onMountContext === 'function' ? deps.onMountContext : null;
        const onParkContext = typeof deps.onParkContext === 'function' ? deps.onParkContext : null;
        const onDestroyContext = typeof deps.onDestroyContext === 'function' ? deps.onDestroyContext : null;
        const warmParkContextFn = typeof deps.warmParkContext === 'function' ? deps.warmParkContext : null;
        const resumeWarmContextFn = typeof deps.resumeWarmContext === 'function' ? deps.resumeWarmContext : null;
        const loadSnapshot =
            typeof deps.loadSnapshot === 'function'
                ? deps.loadSnapshot
                : function () {
                      if (typeof root.prksLoadWorkspaceSnapshot === 'function') {
                          return root.prksLoadWorkspaceSnapshot();
                      }
                      return null;
                  };

        let seq = 0;
        let lastHandledHref = '';
        let lastRenderGen = 0;
        let narrowFallback = false;
        const mountedSet = Object.create(null);
        const state = {
            version: WORKSPACE_VERSION,
            mode: MODE_STACKED,
            mainTabId: null,
            focusedTabId: null,
            secondaryTree: null,
            tabs: [],
            mainSplitRatio: DEFAULT_MAIN_SPLIT_RATIO,
        };

        function generatedTabSeq(id) {
            const match = /^tab-([1-9]\d*)$/.exec(String(id || ''));
            if (!match) return 0;
            const n = Number(match[1]);
            if (!Number.isSafeInteger(n) || n < 1) return 0;
            return n;
        }

        function nextId() {
            let id;
            do {
                seq += 1;
                if (!Number.isSafeInteger(seq) || seq < 1) seq = 1;
                id = 'tab-' + seq;
            } while (tabIndex(id) !== -1);
            return id;
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
            /* Sole policy: navigation.js prksRouteSupportsTile / injected deps.
             * No second allow-list here — fail closed when the policy is absent. */
            if (supportsTileFn) return !!supportsTileFn(hash);
            if (typeof root.prksRouteSupportsTile === 'function') return !!root.prksRouteSupportsTile(hash);
            return false;
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

        function visualTiled() {
            return state.mode === MODE_TILED && !narrowFallback && !!state.secondaryTree;
        }

        function isVisibleTab(tabId) {
            if (!tabId) return false;
            if (tabId === state.mainTabId) return true;
            return visualTiled() && root.containsTab(state.secondaryTree, tabId);
        }

        function paneCapReached() {
            return workspaceModelApi.secondaryLeafCapReached(state.secondaryTree);
        }

        function presentation() {
            return { narrowFallback: narrowFallback };
        }

        /* Replace canonical fields from a pure plan. Same-reference results are a no-op
         * so a repair that found nothing to change is not mutated. Ephemeral
         * titleRouteGen stays off the typed model and is copied back onto replacement tabs. */
        function commitCanonical(next) {
            if (!next || next === state) return false;
            const prev = Object.create(null);
            for (let i = 0; i < state.tabs.length; i++) prev[state.tabs[i].id] = state.tabs[i];
            const tabs = next.tabs.slice();
            for (let i = 0; i < tabs.length; i++) {
                const tab = tabs[i];
                const old = prev[tab.id];
                if (old && old !== tab && tab.titleRouteGen == null && old.titleRouteGen != null) {
                    tab.titleRouteGen = old.titleRouteGen;
                }
            }
            state.version = next.version;
            state.mode = next.mode;
            state.mainTabId = next.mainTabId;
            state.focusedTabId = next.focusedTabId;
            state.secondaryTree = next.secondaryTree;
            state.tabs = tabs;
            state.mainSplitRatio = next.mainSplitRatio;
            return true;
        }

        function oldMainSupportsTile() {
            const oldMain = getMainTab();
            return !!(oldMain && routeSupportsTile(oldMain.route));
        }

        /** Depth-first order (first before second); the sole leaf when the tree is a bare leaf. */
        function defaultSplitTargetLeafId() {
            const tree = state.secondaryTree;
            if (!tree) return null;
            if (root.isLeaf(tree)) return tree.tabId;
            if (state.focusedTabId !== state.mainTabId && root.containsTab(tree, state.focusedTabId)) {
                return state.focusedTabId;
            }
            return null;
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

        function restoreTabRecord(record) {
            const route = canonical(record && record.route);
            let title = record && typeof record.title === 'string' ? record.title : '';
            title = title.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '');
            if (!title) title = routeLoadingTitle(route);
            let icon = record && typeof record.icon === 'string' ? record.icon : '';
            if (!/^[a-z0-9-]{1,40}$/i.test(icon)) icon = routeTabIcon(route);
            return {
                id: String(record.id),
                route: route,
                title: title,
                icon: icon || routeTabIcon(route),
                history: [route],
                historyIndex: 0,
                titleRouteGen: null,
            };
        }

        function adoptSeqFromTabs() {
            let max = 0;
            for (let i = 0; i < state.tabs.length; i++) {
                const n = generatedTabSeq(state.tabs[i].id);
                if (n > max) max = n;
            }
            seq = max;
        }

        function noteCanonicalChange() {
            if (!state.mainTabId) return;
            try {
                if (typeof deps.persistNotify === 'function') {
                    deps.persistNotify(snapshot());
                    return;
                }
                if (typeof root.prksScheduleWorkspacePersistence === 'function') {
                    root.prksScheduleWorkspacePersistence(snapshot());
                }
            } catch (_e) {}
        }

        function canvasLooksNarrow() {
            if (typeof root.prksWorkspaceCanvasIsNarrow === 'function') {
                return !!root.prksWorkspaceCanvasIsNarrow();
            }
            const narrowPx =
                typeof root.PRKS_WORKSPACE_NARROW_PX === 'number' && root.PRKS_WORKSPACE_NARROW_PX > 0
                    ? root.PRKS_WORKSPACE_NARROW_PX
                    : 720;
            function widthIsNarrow(width) {
                if (typeof root.prksWorkspaceWidthIsNarrow === 'function') {
                    return !!root.prksWorkspaceWidthIsNarrow(width);
                }
                /* CSS max-width:N includes N — keep the emergency fallback inclusive. */
                return typeof width === 'number' && width > 0 && width <= narrowPx;
            }
            if (typeof document !== 'undefined' && document.querySelector) {
                const canvas = document.querySelector('.prks-workspace-canvas');
                if (canvas && canvas.clientWidth > 0) return widthIsNarrow(canvas.clientWidth);
            }
            if (typeof root.innerWidth === 'number' && root.innerWidth > 0) {
                return widthIsNarrow(root.innerWidth);
            }
            return false;
        }

        function applyNarrowHint() {
            if (!canvasLooksNarrow()) return;
            narrowFallback = true;
            state.focusedTabId = state.mainTabId;
        }

        function reconcileStartupUrl(hash) {
            const current = canonical(hash);
            const main = getMainTab();
            if (!main || main.route === current) return;
            let match = null;
            for (let i = 0; i < state.tabs.length; i++) {
                if (state.tabs[i].route === current) {
                    match = state.tabs[i];
                    break;
                }
            }
            if (match && match.id !== main.id) {
                if (root.containsTab(state.secondaryTree, match.id)) {
                    if (!promoteSecondaryToMain(match.id)) return;
                } else {
                    setMain(match.id);
                }
                state.focusedTabId = state.mainTabId;
                return;
            }
            main.history = [current];
            main.historyIndex = 0;
            applyTabRoute(main, current);
        }

        function tryRestore(hash) {
            let loaded = null;
            try {
                loaded = loadSnapshot();
            } catch (_e) {
                loaded = null;
            }
            if (!loaded || !Array.isArray(loaded.tabs) || !loaded.tabs.length || !loaded.mainTabId) return false;
            let tabs = null;
            try {
                tabs = loaded.tabs.map(restoreTabRecord);
            } catch (_e) {
                return false;
            }
            if (!tabs.length) return false;
            let tree = null;
            if (loaded.secondaryTree) {
                if (typeof root.resetSplitIds === 'function') root.resetSplitIds();
                try {
                    tree =
                        typeof root.prksRehydrateWorkspaceTree === 'function'
                            ? root.prksRehydrateWorkspaceTree(loaded.secondaryTree)
                            : null;
                } catch (_e) {
                    return false;
                }
                if (!tree) return false;
                if (typeof root.validateTree === 'function') {
                    const check = root.validateTree(tree);
                    if (!check || !check.ok) return false;
                }
            }
            state.tabs = tabs;
            adoptSeqFromTabs();
            state.mainTabId = loaded.mainTabId;
            state.secondaryTree = tree;
            state.mode = loaded.mode === MODE_TILED ? MODE_TILED : MODE_STACKED;
            state.mainSplitRatio = clampUnitRatio(loaded.mainSplitRatio);
            state.focusedTabId = state.mainTabId;
            if (!getMainTab()) return false;
            try {
                reconcileStartupUrl(hash);
            } catch (_e) {
                return false;
            }
            return !!getMainTab();
        }

        function mountVisibleContexts() {
            const main = getMainTab();
            if (main) mountContext(main.id);
            if (!visualTiled()) return;
            const leaves = root.collectLeafTabIds(state.secondaryTree);
            for (let i = 0; i < leaves.length; i++) {
                if (leaves[i] !== state.mainTabId) mountContext(leaves[i]);
            }
        }

        function renderRestoredSecondaries() {
            if (!visualTiled()) return;
            const leaves = root.collectLeafTabIds(state.secondaryTree);
            for (let i = 0; i < leaves.length; i++) {
                const tab = getTab(leaves[i]);
                if (!tab) continue;
                invokeRender({
                    workspaceSwitch: true,
                    tabId: tab.id,
                    hash: tab.route,
                    leaveApproved: true,
                });
            }
        }

        function findTabByRoute(hash, options) {
            const route = canonical(hash);
            const opts = options || {};
            const visual = visualTiled();
            for (let i = 0; i < state.tabs.length; i++) {
                const tab = state.tabs[i];
                if (canonical(tab.route) !== route) continue;
                if (opts.excludeMain && tab.id === state.mainTabId) continue;
                if (opts.excludeVisibleSecondary && visual && root.containsTab(state.secondaryTree, tab.id)) continue;
                if (opts.excludeTabId && tab.id === opts.excludeTabId) continue;
                return copyTab(tab);
            }
            return null;
        }

        function snapshot() {
            const snap = workspaceModelApi.workspaceSnapshot(state);
            for (let i = 0; i < snap.tabs.length; i++) {
                const live = getTab(snap.tabs[i].id);
                snap.tabs[i].titleRouteGen = live && live.titleRouteGen != null ? live.titleRouteGen : null;
            }
            return snap;
        }

        function clampUnitRatio(value) {
            return workspaceModelApi.clampMainSplitRatio(value);
        }

        function getMainSplitRatio() {
            return state.mainSplitRatio;
        }

        function setMainSplitRatio(ratio, options) {
            const opts = options || {};
            const plan = workspaceModelApi.planMainSplitRatio(state, ratio);
            commitCanonical(plan.state);
            if (opts.paint === false) {
                noteCanonicalChange();
                return plan.ratio;
            }
            paint();
            return plan.ratio;
        }

        function resetMainSplitRatio(options) {
            return setMainSplitRatio(DEFAULT_MAIN_SPLIT_RATIO, options);
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

        function coldParkContext(tabId) {
            if (!tabId || !contextMounted(tabId)) return;
            if (typeof root.prksUnmountTabContext === 'function') {
                root.prksUnmountTabContext(tabId, 'park');
            }
            delete mountedSet[tabId];
            if (onParkContext) onParkContext(tabId);
        }

        function warmParkContext(tabId) {
            if (!tabId || !contextMounted(tabId)) return false;
            let warmed = false;
            if (warmParkContextFn) {
                warmed = !!warmParkContextFn(tabId);
            } else if (typeof root.prksWarmParkTabContext === 'function') {
                warmed = !!root.prksWarmParkTabContext(tabId);
            }
            if (!warmed) {
                coldParkContext(tabId);
                return false;
            }
            delete mountedSet[tabId];
            if (onParkContext) onParkContext(tabId);
            return true;
        }

        function paneSlotForTab(tabId) {
            if (typeof document === 'undefined' || !tabId || !document.querySelector) return null;
            const id = String(tabId).replace(/"/g, '');
            const tile = document.querySelector('.prks-tile[data-prks-tab-id="' + id + '"]');
            if (!tile || !tile.querySelector) return null;
            return tile.querySelector('.prks-content-host-slot');
        }

        function hostForTab(tabId) {
            const slot = paneSlotForTab(tabId);
            if (slot && typeof root.prksWorkspacePlaceContentHost === 'function') {
                const placed = root.prksWorkspacePlaceContentHost(tabId, slot);
                if (placed) return placed;
            }
            if (typeof root.prksWorkspaceHostForTab === 'function') {
                const tiled = root.prksWorkspaceHostForTab(tabId);
                if (tiled) return tiled;
            }
            if (typeof root.prksTabContextHost === 'function') return root.prksTabContextHost();
            if (typeof document !== 'undefined') return document.getElementById('page-content');
            return null;
        }

        function mountContext(tabId) {
            if (!tabId || contextMounted(tabId)) return false;
            const host = hostForTab(tabId);
            let resumed = false;
            if (resumeWarmContextFn) {
                resumed = !!resumeWarmContextFn(tabId, host);
            } else if (typeof root.prksResumeWarmTabContext === 'function') {
                resumed = !!root.prksResumeWarmTabContext(tabId, host);
            }
            if (resumed) {
                mountedSet[tabId] = true;
                if (onMountContext) onMountContext(tabId);
                return true;
            }
            if (typeof root.prksMountTabContext === 'function') {
                if (host) root.prksMountTabContext(tabId, host);
            }
            mountedSet[tabId] = true;
            if (onMountContext) onMountContext(tabId);
            return false;
        }

        function destroyContext(tabId) {
            if (!tabId) return;
            if (typeof root.prksDestroyTabContext === 'function') {
                root.prksDestroyTabContext(tabId);
            }
            delete mountedSet[tabId];
            if (typeof root.prksWorkspaceReleaseContentHost === 'function') {
                root.prksWorkspaceReleaseContentHost(tabId);
            }
            if (onDestroyContext) onDestroyContext(tabId);
        }

        function resetAllContexts() {
            const ids = Object.keys(mountedSet);
            for (let i = 0; i < ids.length; i++) {
                delete mountedSet[ids[i]];
                if (onDestroyContext) onDestroyContext(ids[i]);
            }
            if (typeof root.prksDestroyAllTabContexts === 'function') {
                root.prksDestroyAllTabContexts();
            }
        }

        function contextMounted(tabId) {
            if (!tabId) return false;
            if (mountedSet[tabId]) return true;
            if (typeof root.prksGetTabContext !== 'function') return false;
            const ctx = root.prksGetTabContext(tabId);
            return !!(ctx && ctx.mounted);
        }

        function publishShell(tabId) {
            const tab = getTab(tabId);
            publishMainShell(tabId, tab ? tab.title : '');
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
            if (!getTab(tabId) || root.containsTab(state.secondaryTree, tabId)) {
                state.mainTabId = tabId;
                state.focusedTabId = tabId;
                return;
            }
            const plan = workspaceModelApi.planActivate(state, tabId, presentation(), {
                fromPopstate: true,
                oldMainSupportsTile: false,
            });
            if (plan.ok && plan.kind === 'set-main') commitCanonical(plan.state);
            else {
                state.mainTabId = tabId;
                state.focusedTabId = tabId;
            }
        }

        function navigateTabHistory(tab, hash, replace) {
            const planned = workspaceModelApi.planTabHistory(tab, hash, !!replace, {
                title: routeLoadingTitle(hash),
                icon: routeTabIcon(hash),
            });
            tab.route = planned.route;
            tab.title = planned.title;
            tab.icon = planned.icon;
            tab.history = planned.history;
            tab.historyIndex = planned.historyIndex;
            tab.titleRouteGen = null;
        }

        function leaveEngine() {
            return root.prksTabLeave || null;
        }

        function ownerSnapshot(tabId) {
            const tab = getTab(tabId);
            if (!tab) return null;
            if (typeof root.prksGetTabContext !== 'function') {
                return { ownerId: String(tabId), generation: null, token: tab };
            }
            const ctx = root.prksGetTabContext(tabId);
            if (!ctx || ctx.destroyed) return null;
            return {
                ownerId: String(tabId),
                generation: typeof ctx.generation === 'number' ? ctx.generation : null,
                token: ctx,
            };
        }

        function ownerStill(tabId, snap) {
            if (!snap || !getTab(tabId)) return false;
            if (typeof root.prksGetTabContext !== 'function') return true;
            const ctx = root.prksGetTabContext(tabId);
            return !!(
                ctx &&
                snap.token === ctx &&
                !ctx.destroyed &&
                ctx.generation === snap.generation
            );
        }

        function assessLeave(tabId, nextHash) {
            if (injectedCanLeave) {
                try {
                    return injectedCanLeave(tabId, nextHash);
                } catch (_e) {
                    return false;
                }
            }
            const engine = leaveEngine();
            if (
                engine &&
                typeof engine.assessOwner === 'function' &&
                typeof root.prksGetTabContext === 'function'
            ) {
                return engine.assessOwner(root.prksGetTabContext(tabId), nextHash);
            }
            return true;
        }

        function flushLeaveNotes(tabId) {
            if (injectedCanLeave) return;
            const engine = leaveEngine();
            if (!engine || typeof engine.flushOwner !== 'function') return;
            if (typeof root.prksGetTabContext !== 'function') return;
            const ctx = root.prksGetTabContext(tabId);
            if (ctx) engine.flushOwner(ctx);
        }

        function interpretLeave(decision) {
            if (!decision || decision.status !== 'approved') return false;
            if (decision.value === undefined) return true;
            return decision.value;
        }

        function leaveAttempt(tabId, nextHash, transition) {
            return {
                ownerId: String(tabId || ''),
                destination: nextHash == null ? null : nextHash,
                transition: transition,
                capture: function () {
                    return ownerSnapshot(tabId);
                },
                still: function (snap) {
                    return ownerStill(tabId, snap);
                },
                assess: function () {
                    return assessLeave(tabId, nextHash);
                },
                flushNotes: function () {
                    flushLeaveNotes(tabId);
                },
            };
        }

        function leaveRejected(result) {
            if (result === false) return true;
            return !!(result && result.status && result.status !== 'approved');
        }

        /** One owner. The commit runs only after approval, while the owner lock is held. */
        function runLeave(tabId, nextHash, transition, commit) {
            const engine = leaveEngine();
            if (!engine || typeof engine.run !== 'function') {
                return Promise.resolve()
                    .then(function () {
                        return assessLeave(tabId, nextHash);
                    })
                    .then(function (result) {
                        if (leaveRejected(result)) return false;
                        flushLeaveNotes(tabId);
                        return commit ? commit() : true;
                    })
                    .catch(function () {
                        return false;
                    });
            }
            const attempt = leaveAttempt(tabId, nextHash, transition);
            attempt.commit = commit;
            return engine.run(attempt).then(interpretLeave);
        }

        /**
         * Every entry is assessed, in order, before `commit`. A rejection commits nothing.
         * Callers that must ignore unmounted tabs filter before calling.
         */
        function runLeaves(entries, transition, commit) {
            const live = [];
            for (let i = 0; i < entries.length; i++) {
                if (entries[i] && entries[i].tabId) live.push(entries[i]);
            }
            if (!live.length) return Promise.resolve(commit ? commit() : true);
            const engine = leaveEngine();
            if (!engine || typeof engine.runBatch !== 'function') {
                function step(i) {
                    if (i >= live.length) {
                        for (let n = 0; n < live.length; n++) flushLeaveNotes(live[n].tabId);
                        return Promise.resolve(commit ? commit() : true);
                    }
                    return Promise.resolve(assessLeave(live[i].tabId, live[i].nextHash)).then(function (result) {
                        if (leaveRejected(result)) return false;
                        return step(i + 1);
                    });
                }
                return step(0).catch(function () {
                    return false;
                });
            }
            return engine
                .runBatch({
                    transition: transition,
                    attempts: live.map(function (entry) {
                        return leaveAttempt(entry.tabId, entry.nextHash, transition);
                    }),
                    commit: commit || function () {
                        return true;
                    },
                })
                .then(interpretLeave);
        }

        function mainPromotionLeaveEntry(targetId) {
            const pf = workspaceModelApi.preflightMakeMain(state, targetId, {
                narrowFallback: narrowFallback,
                homeHash: homeHash,
                oldMainSupportsTile: oldMainSupportsTile(),
            });
            if (pf.type !== 'main-promotion' || !pf.requiresLeave) return null;
            return { tabId: pf.oldMainId, nextHash: pf.nextHash };
        }

        function enforceInvariants() {
            const next = workspaceModelApi.repairWorkspaceState(state);
            if (next !== state) commitCanonical(next);
        }

        const projectionListeners = new Set();
        /* Monotonic id of a real publication. Subscribing does not advance it.
         * Schema `state.version` stays the model version. */
        let publishSerial = 0;
        let latestCommit = 0;
        let cachedProjection = null;
        let shellSeenCommit = 0;
        let pendingFocusTabId = null;
        let publishedStatusKey = null;
        let statusFramePending = false;

        function statusKeyOf(map) {
            if (!map) return '';
            const ids = Object.keys(map);
            let key = '';
            for (let i = 0; i < ids.length; i++) key += ids[i] + '\0' + (map[ids[i]] || '') + '\n';
            return key;
        }

        function projectionAt(commit) {
            const body = {
                state: copyJson(snapshot()) || emptyProjectionState(),
                visualTiled: visualTiled(),
                narrowFallback: !!narrowFallback,
                tabStatus: tabStatusMap(),
                commit: commit,
            };
            const projection = deepFreeze(body);
            cachedProjection = projection;
            publishedStatusKey = statusKeyOf(projection.tabStatus);
            return projection;
        }

        /* One current projection until the next publish. The first subscriber
         * with an empty cache establishes it. Later subscribers reuse it. */
        function currentProjection() {
            if (cachedProjection) return cachedProjection;
            publishSerial += 1;
            latestCommit = publishSerial;
            return projectionAt(publishSerial);
        }

        function emptyProjectionState() {
            return {
                version: WORKSPACE_VERSION,
                mode: MODE_STACKED,
                mainTabId: null,
                focusedTabId: null,
                secondaryTree: null,
                tabs: [],
                mainSplitRatio: DEFAULT_MAIN_SPLIT_RATIO,
            };
        }

        function tabStatusMap() {
            const out = Object.create(null);
            for (let i = 0; i < state.tabs.length; i++) {
                const id = state.tabs[i].id;
                out[id] = tabStatusKind(id);
            }
            return out;
        }

        function deepFreeze(value) {
            if (!value || typeof value !== 'object' || Object.isFrozen(value)) return value;
            Object.freeze(value);
            if (Array.isArray(value)) {
                for (let i = 0; i < value.length; i++) deepFreeze(value[i]);
                return value;
            }
            const keys = Object.keys(value);
            for (let i = 0; i < keys.length; i++) deepFreeze(value[keys[i]]);
            return value;
        }

        /**
         * Notify subscribers with a detached, frozen snapshot. One copy per
         * publish, shared by the listeners of that publish. Does not paint DOM.
         */
        function publishProjection() {
            if (!projectionListeners.size) {
                /* State may have moved. Drop the cache so a later subscribe
                 * rebuilds the current projection instead of replaying the
                 * last one. Do not mint a commit nobody receives. */
                cachedProjection = null;
                return;
            }
            publishSerial += 1;
            latestCommit = publishSerial;
            const projection = projectionAt(publishSerial);
            const listeners = Array.from(projectionListeners);
            for (let i = 0; i < listeners.length; i++) {
                try {
                    listeners[i](projection);
                } catch (_e) {}
            }
        }

        function subscribeProjection(listener) {
            if (typeof listener !== 'function') return function () {};
            projectionListeners.add(listener);
            try {
                listener(currentProjection());
            } catch (_e) {}
            return function unsubscribe() {
                projectionListeners.delete(listener);
            };
        }

        function paint() {
            enforceInvariants();
            onChange();
            noteCanonicalChange();
            publishProjection();
        }

        function paintFocus() {
            publishProjection();
        }

        function bootstrap(initialHash) {
            const hash = canonical(initialHash != null ? initialHash : getHash());
            seq = 0;
            if (typeof root.resetSplitIds === 'function') root.resetSplitIds();
            lastHandledHref = '';
            lastRenderGen = 0;
            narrowFallback = false;
            resetAllContexts();
            if (!tryRestore(hash)) {
                const tab = makeTab(hash);
                state.tabs = [tab];
                state.secondaryTree = null;
                state.mode = MODE_STACKED;
                state.mainSplitRatio = DEFAULT_MAIN_SPLIT_RATIO;
                setMain(tab.id);
            }
            applyNarrowHint();
            paint();
            mountVisibleContexts();
            const main = getMainTab();
            if (main) commitUrl(main, 'replace');
            paint();
            renderRestoredSecondaries();
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

        /* Leave approved this id as the Main that may be parked. Tile-capable Make
         * Main can put a different tab in that role without a leave and without
         * changing generation, so a commit must not park the later Main. */
        function outgoingMainStill(leavingMainId) {
            return !!leavingMainId && state.mainTabId === leavingMainId && !!getTab(leavingMainId);
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
            const leavingMainId = state.mainTabId;
            return runLeave(leavingMainId, route, 'route-replace', function () {
                if (!outgoingMainStill(leavingMainId)) return false;
                const tab = makeTab(route);
                state.tabs.push(tab);
                if (leavingMainId !== tab.id) warmParkContext(leavingMainId);
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

        /**
         * Promote a Secondary leaf to Main. Demotion into `secondaryTree` is allowed only when
         * the old Main route itself supports tiling: then this is an exact leaf role swap
         * (`replaceTabId` at the target's former position). Otherwise the promoted leaf is
         * removed and the tree is normalized; the old Main stays an ordinary parked tab.
         * Does not force tiled mode (hidden/stacked stays hidden). Collapses to stacked when
         * no Secondary leaves remain. Parks the old Main when it is not demoted into the tree.
         * Does not mount or paint.
         */
        function promoteSecondaryToMain(newMainId) {
            const plan = workspaceModelApi.planMakeMain(state, newMainId, oldMainSupportsTile());
            if (!plan.ok) return false;
            if (plan.coldParkTabId) coldParkContext(plan.coldParkTabId);
            if (plan.changed) commitCanonical(plan.state);
            return true;
        }

        function finishMakeMain(tab) {
            if (!getTab(tab.id) || !root.containsTab(state.secondaryTree, tab.id)) return false;
            if (!promoteSecondaryToMain(tab.id)) return false;
            commitUrl(tab, 'replace');
            paintAndRestore(tab.id);
            publishShell(tab.id);
            refreshFocusedPanel();
            return true;
        }

        /**
         * Explicit Make Main. A tileable old Main is a role swap and does not leave.
         * A non-tileable old Main is cold-parked only after its leave is approved.
         * The destination passed to that leave is the old Main's own route.
         */
        function makeMain(tabId) {
            const tab = getTab(tabId);
            if (!tab) return Promise.resolve(false);
            if (tab.id === state.mainTabId) {
                const plan = workspaceModelApi.planMakeMain(state, tab.id, true);
                if (plan.changed) commitCanonical(plan.state);
                paintAndRestore(tab.id);
                refreshFocusedPanel();
                return Promise.resolve(true);
            }
            const entry = mainPromotionLeaveEntry(tab.id);
            if (!entry) return Promise.resolve(finishMakeMain(tab));
            return runLeave(entry.tabId, entry.nextHash, 'promote-main', function () {
                return finishMakeMain(tab);
            });
        }

        function focusTab(tabId) {
            const plan = workspaceModelApi.planFocus(state, tabId, presentation());
            if (!plan.ok) return false;
            if (!plan.changed) return true;
            commitCanonical(plan.state);
            paintFocus();
            refreshFocusedPanel();
            return true;
        }

        function mountAndRenderSecondary(tab) {
            const modePlan = workspaceModelApi.planSetMode(state, MODE_TILED, presentation());
            if (modePlan.ok && modePlan.kind === 'show') commitCanonical(modePlan.state);
            else state.mode = MODE_TILED;
            if (narrowFallback) {
                state.focusedTabId = state.mainTabId;
                paintAndRestore(state.mainTabId);
                announce('', 'narrow');
                return Promise.resolve(copyTab(tab));
            }
            const focus = workspaceModelApi.planFocus(state, tab.id, presentation());
            if (focus.ok && focus.changed) commitCanonical(focus.state);
            else if (!focus.ok) state.focusedTabId = tab.id;
            paintAndRestore(tab.id);
            const resumed = mountContext(tab.id);
            announce(tab.title, 'split');
            if (resumed) {
                publishShell(tab.id);
                refreshFocusedPanel();
                return Promise.resolve(copyTab(tab));
            }
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

        /** Splits `targetTabId`'s leaf into a new split node holding `tab` (spec default:
         * existing leaf stays first, new leaf placed second -- `placement: 'first'` reverses
         * that, used by drag-drop's left/above edge zones). Mounts only `tab` -- the target
         * leaf and every other leaf keep their existing TabContext untouched. */
        function performSplit(targetTabId, axis, tab, placement) {
            const plan = workspaceModelApi.planSplitLeaf(
                state,
                targetTabId,
                tab.id,
                axis,
                placement === 'first' ? 'first' : 'second',
                root.nextSplitId
            );
            if (!plan.ok) return Promise.resolve(false);
            commitCanonical(plan.state);
            return mountAndRenderSecondary(tab);
        }

        /** Explicit Split right ('left-right') / Split down ('top-bottom') action for a specific
         * focused Secondary leaf. `options.tabId` reuses an existing (often parked) tab;
         * `options.hash` creates one. `options.placement` ('first' | 'second', default 'second')
         * lets a caller (drag-drop's edge-zone geometry) put the new leaf before the target
         * instead of after. Refuses (without side effects on an existing tab) when the target
         * isn't a visible leaf, the tab is ineligible, or the visible-pane cap is reached. */
        function splitLeafAction(targetTabId, axis, options) {
            if (!root.containsTab(state.secondaryTree, targetTabId)) return Promise.resolve(false);
            if (paneCapReached()) {
                announce('', 'cap');
                return Promise.resolve(false);
            }
            const opts = options || {};
            const useAxis = axis === 'top-bottom' ? 'top-bottom' : 'left-right';
            if (opts.tabId) {
                const existing = getTab(opts.tabId);
                if (
                    !existing ||
                    existing.id === state.mainTabId ||
                    root.containsTab(state.secondaryTree, existing.id) ||
                    !routeSupportsTile(existing.route)
                ) {
                    return Promise.resolve(false);
                }
                return Promise.resolve(performSplit(targetTabId, useAxis, existing, opts.placement));
            }
            const route = canonical(opts.hash);
            if (!routeSupportsTile(route)) return Promise.resolve(false);
            const tab = makeTab(route);
            state.tabs.push(tab);
            return Promise.resolve(performSplit(targetTabId, useAxis, tab, opts.placement));
        }

        /** Atomic spatial reposition of an already-visible Secondary leaf (`sourceTabId`)
         * relative to another visible Secondary leaf (`targetTabId`) -- one tree transaction via
         * `root.moveLeafRelativeToTarget`, published once (spec #17-21). This is NOT a leave
         * operation: the moved leaf stays mounted and visible before and after, so no leave
         * preflight and no context destroy/remount happen here. `focusedTabId` is left exactly
         * as-is -- moving a pane never changes focus ownership by itself. Declines (no mutation)
         * when source/target aren't distinct visible Secondary leaves, Main is involved (Main
         * never enters secondaryTree by drag), or the layout isn't spatially visible (stacked /
         * narrow fallback -- no Secondary geometry exists to move within). */
        function movePane(sourceTabId, targetTabId, axis, placement) {
            if (!sourceTabId || !targetTabId || sourceTabId === targetTabId) return false;
            if (sourceTabId === state.mainTabId || targetTabId === state.mainTabId) return false;
            if (!visualTiled()) return false;
            if (!root.containsTab(state.secondaryTree, sourceTabId)) return false;
            if (!root.containsTab(state.secondaryTree, targetTabId)) return false;
            const plan = workspaceModelApi.planMovePane(
                state,
                sourceTabId,
                targetTabId,
                axis,
                placement,
                presentation(),
                root.nextSplitId
            );
            if (!plan.ok) return false;
            commitCanonical(plan.state);
            paint();
            return true;
        }

        /** Canonical global tab-strip reorder (spec #9/#48): moves `tabId` to sit immediately
         * before `beforeTabId` in `state.tabs` order (or to the end when `beforeTabId` is
         * falsy/not found). Touches array order only -- never `mainTabId`, `focusedTabId`,
         * `secondaryTree`, mounted contexts, or the URL. Both drag-drop and the tab context
         * menu's Move left/right commands call this one function. */
        function reorderTab(tabId, beforeTabId) {
            const plan = workspaceModelApi.planReorder(state, tabId, beforeTabId || null);
            if (!plan.ok) return false;
            commitCanonical(plan.state);
            paint();
            return true;
        }

        /** Keyboard/menu alternative to drag reordering (spec #43): swaps `tabId` with its
         * immediate left/right neighbor in the tab strip via the same `reorderTab` primitive.
         * No-op at either end of the strip. */
        function moveTabStep(tabId, direction) {
            const idx = tabIndex(tabId);
            if (idx < 0) return false;
            if (direction === 'left') {
                if (idx <= 0) return false;
                return reorderTab(tabId, state.tabs[idx - 1].id);
            }
            if (idx >= state.tabs.length - 1) return false;
            const afterNeighbor = state.tabs[idx + 2];
            return reorderTab(tabId, afterNeighbor ? afterNeighbor.id : null);
        }

        /** Removes `tabId`'s leaf from secondaryTree while keeping its logical tab open and
         * parked (spec: "Hide this pane" is distinct from the global "Hide split"). Normalizes
         * the tree and focuses the closest surviving sibling, else the nearest remaining leaf in
         * deterministic tree order, else Main. */
        function hideLeaf(tabId) {
            const pf = workspaceModelApi.preflightHideLeaf(state, tabId, {
                narrowFallback: narrowFallback,
                homeHash: homeHash,
                mounted: contextMounted(tabId),
            });
            if (pf.type === 'none' && !root.containsTab(state.secondaryTree, tabId)) return Promise.resolve(false);
            const commitHide = function () {
                const plan = workspaceModelApi.planHideLeaf(state, tabId);
                if (!plan.ok) return false;
                if (contextMounted(tabId)) coldParkContext(tabId);
                commitCanonical(plan.state);
                paintAndRestore(state.focusedTabId);
                refreshFocusedPanel();
                return true;
            };
            if (pf.type !== 'leave-tab') return Promise.resolve(commitHide());
            return runLeave(pf.tabId, pf.nextHash, 'cold-park', commitHide);
        }

        function tileTab(tabId) {
            const tab = getTab(tabId);
            if (!tab) return Promise.resolve(false);
            if (tab.id === state.mainTabId) return Promise.resolve(false);
            const tree = state.secondaryTree;
            if (root.containsTab(tree, tab.id)) {
                const modePlan = workspaceModelApi.planSetMode(state, MODE_TILED, presentation());
                if (modePlan.ok && modePlan.kind === 'show') commitCanonical(modePlan.state);
                else state.mode = MODE_TILED;
                if (narrowFallback) {
                    state.focusedTabId = state.mainTabId;
                    paintAndRestore(state.mainTabId);
                    announce('', 'narrow');
                    return Promise.resolve(copyTab(tab));
                }
                const focus = workspaceModelApi.planFocus(state, tab.id, presentation());
                if (focus.ok && focus.changed) commitCanonical(focus.state);
                else if (!focus.ok) state.focusedTabId = tab.id;
                paintAndRestore(tab.id);
                if (!contextMounted(tab.id)) {
                    const resumed = mountContext(tab.id);
                    if (resumed) {
                        publishShell(tab.id);
                        refreshFocusedPanel();
                        return Promise.resolve(copyTab(tab));
                    }
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
            /* Route capability is a workspace state invariant, not just a UI affordance: reject
             * inserting a tab whose route cannot be tiled -- without any tree mutation, mounting,
             * or duplication -- even if a caller bypasses UI filtering. */
            if (!routeSupportsTile(tab.route)) return Promise.resolve(false);
            if (!tree) {
                state.secondaryTree = root.makeLeaf(tab.id);
                return Promise.resolve(mountAndRenderSecondary(tab));
            }
            /* Adding a tab to split view never evicts an existing pane (spec): there is no
             * user-facing "Replace split pane" command, so generic placement always splits
             * instead of replacing. Splitting is unambiguous when there is exactly one existing
             * Secondary leaf (split it) or a Secondary leaf is focused (split that one).
             * Otherwise this generic entry point declines; callers needing a specific
             * placement/axis use the explicit Split right/down action. Never duplicates a tab --
             * `tab` here is already an existing logical tab. */
            const targetLeaf = defaultSplitTargetLeafId();
            if (!targetLeaf || paneCapReached()) {
                announce('', paneCapReached() ? 'cap' : 'ambiguous');
                return Promise.resolve(false);
            }
            return Promise.resolve(performSplit(targetLeaf, 'left-right', tab));
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
            const tree = state.secondaryTree;
            if (!tree) {
                const tab = makeTab(route);
                state.tabs.push(tab);
                state.secondaryTree = root.makeLeaf(tab.id);
                return Promise.resolve(mountAndRenderSecondary(tab));
            }
            /* Adding a new tab to split view never evicts an existing pane (spec): split the
             * default target leaf (the single existing leaf, or the focused Secondary leaf)
             * left-right -- existing leaf stays first, new leaf becomes second/focused.
             * Fail-closed, matching tileTab(): when placement is ambiguous (a recursive tree
             * with no focused Secondary leaf) or the pane cap is already reached, this must not
             * create a tab, mutate secondaryTree, mount anything, or paint -- only announce the
             * guidance and return false. The caller can focus a Secondary leaf or use an
             * explicit Split right/down instead. */
            const targetLeaf = defaultSplitTargetLeafId();
            if (!targetLeaf || paneCapReached()) {
                announce('', paneCapReached() ? 'cap' : 'ambiguous');
                return Promise.resolve(false);
            }
            const tab = makeTab(route);
            state.tabs.push(tab);
            return Promise.resolve(performSplit(targetLeaf, 'left-right', tab));
        }

        function applyCurrentNavigation(tab, route, replace) {
            const same = tab.route === route;
            let routeStateCaptured = false;
            if (!same && typeof root.prksCaptureCurrentRouteState === 'function' && typeof root.prksGetTabContext === 'function') {
                const ctx = root.prksGetTabContext(tab.id);
                const previousRoute = ctx && ctx.lastResolvedRoute;
                if (ctx && previousRoute && previousRoute.canonicalHash !== route) {
                    root.prksCaptureCurrentRouteState(previousRoute, ctx);
                    routeStateCaptured = true;
                }
            }
            navigateTabHistory(tab, route, replace || same);
            if (tab.id === state.mainTabId) commitUrl(tab, replace || same ? 'replace' : 'push');
            paint();
            return Promise.resolve(
                invokeRender({
                    workspaceSwitch: false,
                    tabId: tab.id,
                    hash: tab.route,
                    routeStateCaptured: routeStateCaptured,
                })
            );
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
            const replace = !!opts.replace;
            if (tab.id !== state.mainTabId && !routeSupportsTile(route)) {
                const leavingId = tab.id;
                const entries = [{ tabId: leavingId, nextHash: route }];
                const promo = mainPromotionLeaveEntry(leavingId);
                if (promo) entries.push(promo);
                /* Target leave, then the old Main's cold-park leave, then one commit.
                 * promoteSecondaryToMain stays the unchecked mutation. The render
                 * promise stays outside the lock so a later navigation can start
                 * while this detail request is still open. */
                let pendingPromotion = null;
                return runLeaves(entries, 'promote-main', function () {
                    if (!getTab(leavingId) || !root.containsTab(state.secondaryTree, leavingId)) return false;
                    if (!promoteSecondaryToMain(leavingId)) return false;
                    announce('', 'promote');
                    const promoted = getMainTab();
                    if (!promoted || promoted.id !== leavingId) return false;
                    pendingPromotion = applyCurrentNavigation(promoted, route, replace);
                    return true;
                }).then(function (ok) {
                    if (!ok) return false;
                    return pendingPromotion;
                });
            }
            let pendingRender = null;
            return runLeave(tab.id, route, 'route-replace', function () {
                if (!getTab(tab.id)) return false;
                pendingRender = applyCurrentNavigation(tab, route, replace);
                return true;
            }).then(function (ok) {
                if (!ok) return false;
                return pendingRender;
            });
        }

        function activateTab(tabId, options) {
            const opts = options || {};
            const tab = getTab(tabId);
            if (!tab) return Promise.resolve(false);
            if (tab.id === state.mainTabId && !opts.fromPopstate) {
                const plan = workspaceModelApi.planActivate(state, tab.id, presentation(), {
                    fromPopstate: false,
                    oldMainSupportsTile: oldMainSupportsTile(),
                });
                if (!plan.ok || plan.kind !== 'focus-main') return Promise.resolve(false);
                if (plan.changed) commitCanonical(plan.state);
                paint();
                refreshFocusedPanel();
                return Promise.resolve(true);
            }
            /* A visible Secondary leaf just gets focused, not promoted -- clicking its global
             * tab-strip entry is not the same gesture as an explicit Make main. A leaf that
             * exists in the tree but isn't currently visible (narrow fallback) still falls
             * through to the promote path below, matching a parked tab. */
            if (visualTiled() && root.containsTab(state.secondaryTree, tab.id) && !opts.fromPopstate) {
                return Promise.resolve(focusTab(tab.id));
            }
            const leavingMainId = state.mainTabId;
            return runLeave(leavingMainId, tab.route, 'route-replace', function () {
                if (!getTab(tabId)) return false;
                if (!outgoingMainStill(leavingMainId)) return false;
                if (leavingMainId !== tabId) warmParkContext(leavingMainId);
                if (root.containsTab(state.secondaryTree, tabId)) {
                    if (!promoteSecondaryToMain(tabId)) return false;
                } else {
                    setMain(tabId);
                }
                const resumed = mountContext(tabId);
                commitUrl(tab, 'replace');
                paint();
                if (resumed) {
                    publishShell(tab.id);
                    refreshFocusedPanel();
                    return true;
                }
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
            const closingLeaf = root.containsTab(state.secondaryTree, closing.id);
            /* The role before the dialog only chooses whether to leave and which
             * destination the confirmation is about. Tile-capable Make Main is not
             * a leave and does not change generation, so it can swap Main while
             * this close is waiting. Effects come from a fresh plan at approval. */
            const commitClose = function () {
                if (tabIndex(tabId) < 0) return false;
                const preview = workspaceModelApi.planCloseTab(state, tabId, null);
                if (preview.needsHomeTab) {
                    destroyContext(tabId);
                    const home = makeTab(homeHash);
                    const planned = workspaceModelApi.planCloseTab(state, tabId, home);
                    if (!planned.ok) return false;
                    commitCanonical(planned.state);
                    paint();
                    mountContext(home.id);
                    commitUrl(home, 'replace');
                    paintAndRestore(home.id);
                    return Promise.resolve(
                        invokeRender({ workspaceSwitch: true, tabId: home.id, hash: home.route })
                    ).then(function () {
                        return true;
                    });
                }
                if (!preview.ok) return false;
                if (!preview.successorId) {
                    destroyContext(tabId);
                    commitCanonical(preview.state);
                    paintAndRestore(state.focusedTabId);
                    return true;
                }
                const successor = getTab(preview.successorId);
                if (!successor) return false;
                const wasMountedSuccessor = contextMounted(successor.id);
                destroyContext(tabId);
                commitCanonical(preview.state);
                const resumedSuccessor = !wasMountedSuccessor && mountContext(preview.successorId);
                commitUrl(successor, 'replace');
                paintAndRestore(preview.successorId);
                if (wasMountedSuccessor || resumedSuccessor) {
                    publishShell(preview.successorId);
                    refreshFocusedPanel();
                    return true;
                }
                return Promise.resolve(
                    invokeRender({ workspaceSwitch: true, tabId: successor.id, hash: successor.route })
                ).then(function () {
                    return true;
                });
            };
            if (!closingMain) {
                const needLeave = closingLeaf && visualTiled();
                if (!needLeave) return Promise.resolve(commitClose());
                return runLeave(closing.id, homeHash, 'close', commitClose);
            }
            const previewForLeave = workspaceModelApi.planCloseTab(state, tabId, null);
            const successorForLeave = previewForLeave.successorId ? getTab(previewForLeave.successorId) : null;
            const nextHash = successorForLeave ? successorForLeave.route : homeHash;
            return runLeave(closing.id, nextHash, 'close', commitClose);
        }

        function paintAndRestore(tabId) {
            const id = tabId || state.focusedTabId;
            if (root.__prksWorkspaceShellOwned) pendingFocusTabId = id;
            paint();
            if (!root.__prksWorkspaceShellOwned && typeof root.prksWorkspaceRestoreFocus === 'function') {
                root.prksWorkspaceRestoreFocus(id);
            }
        }

        function queueFocusRestore(tabId) {
            if (!tabId) return;
            pendingFocusTabId = tabId;
            if (shellSeenCommit === latestCommit && latestCommit !== 0) {
                const id = pendingFocusTabId;
                pendingFocusTabId = null;
                if (typeof root.prksWorkspaceApplyRestoredFocus === 'function') {
                    root.prksWorkspaceApplyRestoredFocus(id);
                }
            }
        }

        /** Vue calls this after both teleports have painted `rendered`. */
        function onShellCommit(rendered) {
            if (!rendered || rendered.commit !== latestCommit) return;
            shellSeenCommit = rendered.commit;
            if (typeof root.prksWorkspaceApplyShellDomEffects === 'function') {
                root.prksWorkspaceApplyShellDomEffects(rendered);
            }
            if (!pendingFocusTabId) return;
            const id = pendingFocusTabId;
            pendingFocusTabId = null;
            if (typeof root.prksWorkspaceApplyRestoredFocus === 'function') {
                root.prksWorkspaceApplyRestoredFocus(id);
            }
        }

        function refreshTabStatus() {
            const key = statusKeyOf(tabStatusMap());
            if (key === publishedStatusKey) return;
            if (statusFramePending) return;
            statusFramePending = true;
            const run = function () {
                statusFramePending = false;
                if (statusKeyOf(tabStatusMap()) === publishedStatusKey) return;
                publishProjection();
            };
            if (typeof root.requestAnimationFrame === 'function') root.requestAnimationFrame(run);
            else setTimeout(run, 16);
        }

        function closeTabIds(ids, keepId) {
            const keep = getTab(keepId);
            if (!keep) return Promise.resolve(false);
            const unique = [];
            const seen = Object.create(null);
            for (let i = 0; i < ids.length; i++) {
                const id = ids[i];
                if (!id || id === keepId || seen[id] || !getTab(id)) continue;
                seen[id] = true;
                unique.push(id);
            }
            if (!unique.length) return Promise.resolve(true);

            /* Ids mounted when a prompt opens are the only ones that have approved.
             * A parked id can mount while that prompt is open (browser Back on a
             * warm-parked tab). Recheck before any destroy, and preflight the new
             * mounted owners first. A rejection commits nothing. */
            const preflighted = Object.create(null);

            function commitBatch() {
                if (!getTab(keepId)) return false;
                const closingMain = unique.indexOf(state.mainTabId) >= 0;
                const treeLeavesBefore = root.collectLeafTabIds(state.secondaryTree);
                const closingLeafIds = treeLeavesBefore.filter(function (id) {
                    return unique.indexOf(id) >= 0;
                });
                const keepWasMounted = contextMounted(keepId);
                const keepWasLeaf = treeLeavesBefore.indexOf(keepId) !== -1;
                for (let i = 0; i < unique.length; i++) {
                    destroyContext(unique[i]);
                    const idx = tabIndex(unique[i]);
                    if (idx >= 0) state.tabs.splice(idx, 1);
                }
                if (closingMain) {
                    /* Promote `keepId` into Main's position. If `keepId` is itself a surviving
                     * Secondary leaf, vacate just that one leaf (same in-place swap as the
                     * deterministic single-close successor); either way only the leaves actually
                     * requested to close are removed from the tree. Unrelated surviving Secondary
                     * leaves are never parked or remounted just because Main and some other tabs
                     * closed together in the same batch. Only collapse to stacked if no Secondary
                     * leaves remain afterward. */
                    let nextTree = state.secondaryTree;
                    for (let k = 0; k < closingLeafIds.length; k++) nextTree = root.removeLeaf(nextTree, closingLeafIds[k]);
                    if (keepWasLeaf) nextTree = root.removeLeaf(nextTree, keepId);
                    state.secondaryTree = root.normalizeTree(nextTree);
                    if (!state.secondaryTree) state.mode = MODE_STACKED;
                } else if (closingLeafIds.length) {
                    let nextTree = state.secondaryTree;
                    for (let k = 0; k < closingLeafIds.length; k++) nextTree = root.removeLeaf(nextTree, closingLeafIds[k]);
                    state.secondaryTree = root.normalizeTree(nextTree);
                    if (!state.secondaryTree) state.mode = MODE_STACKED;
                }
                if (closingMain) {
                    setMain(keepId);
                    const resumedKeep = !keepWasMounted && mountContext(keepId);
                    commitUrl(keep, 'replace');
                    paintAndRestore(keepId);
                    if ((keepWasMounted || resumedKeep) && (keepWasLeaf || keepId === state.mainTabId)) {
                        publishShell(keepId);
                        refreshFocusedPanel();
                        return true;
                    }
                    return Promise.resolve(
                        invokeRender({ workspaceSwitch: true, tabId: keep.id, hash: keep.route })
                    ).then(function () {
                        return true;
                    });
                }
                if (!visualTiled() || !isVisibleTab(state.focusedTabId)) {
                    state.focusedTabId = state.mainTabId;
                }
                paintAndRestore(state.focusedTabId);
                refreshFocusedPanel();
                return true;
            }

            function mountedAwaitingLeave() {
                const pending = [];
                for (let i = 0; i < unique.length; i++) {
                    const id = unique[i];
                    if (preflighted[id] || !getTab(id) || !contextMounted(id)) continue;
                    pending.push(id);
                }
                return pending;
            }

            function preflightThenClose() {
                const anchor = getTab(keepId);
                if (!anchor) return Promise.resolve(false);
                const pending = mountedAwaitingLeave();
                if (!pending.length) return Promise.resolve(commitBatch());
                const entries = [];
                for (let i = 0; i < pending.length; i++) {
                    preflighted[pending[i]] = true;
                    entries.push({ tabId: pending[i], nextHash: anchor.route });
                }
                return runLeaves(entries, 'close', function () {
                    if (!getTab(keepId)) return false;
                    if (mountedAwaitingLeave().length) return preflightThenClose();
                    return commitBatch();
                });
            }

            return preflightThenClose();
        }

        function closeOtherTabs(tabId) {
            if (!getTab(tabId)) return Promise.resolve(false);
            const ids = [];
            for (let i = 0; i < state.tabs.length; i++) {
                if (state.tabs[i].id !== tabId) ids.push(state.tabs[i].id);
            }
            return closeTabIds(ids, tabId);
        }

        function closeTabsToTheRight(tabId) {
            const idx = tabIndex(tabId);
            if (idx < 0) return Promise.resolve(false);
            const ids = [];
            for (let i = idx + 1; i < state.tabs.length; i++) ids.push(state.tabs[i].id);
            return closeTabIds(ids, tabId);
        }

        /** Mounts+renders every currently-unmounted leaf in the tree (used by both Show split
         * and leaving narrow fallback -- both remount the whole logical tree at once). */
        function mountAllSecondaryLeaves() {
            const leaves = root.collectLeafTabIds(state.secondaryTree);
            const toMount = leaves.filter(function (id) {
                return !contextMounted(id);
            });
            if (!toMount.length) return Promise.resolve(true);
            const toRender = [];
            toMount.forEach(function (id) {
                if (!mountContext(id)) toRender.push(id);
            });
            return Promise.all(
                toRender.map(function (id) {
                    const tab = getTab(id);
                    return Promise.resolve(
                        invokeRender({
                            workspaceSwitch: true,
                            tabId: id,
                            hash: tab ? tab.route : null,
                            leaveApproved: true,
                        })
                    );
                })
            ).then(function () {
                return true;
            });
        }

        function setMode(mode) {
            const plan = workspaceModelApi.planSetMode(state, mode, presentation());
            if (!plan.ok) return Promise.resolve(false);
            if (plan.kind === 'noop') return Promise.resolve(true);
            if (plan.kind === 'show') {
                commitCanonical(plan.state);
                paintAndRestore(state.focusedTabId || state.mainTabId);
                if (narrowFallback) return Promise.resolve(true);
                return mountAllSecondaryLeaves();
            }
            /* Global Hide split parks every visible leaf at once; this must be atomic (spec:
             * depth-first preflight, stop at first rejection, no partial unmounting).
             * Leave approval is async. Replan from the live state afterward so a workspace
             * edit that landed during the prompt is not overwritten by the pre-await plan.
             * A changed mounted Secondary set was not the set that approved leave, so that
             * newer state stays and this hide does not park or commit. Close, hide-leaf,
             * make-main, narrow fallback, and popstate already re-read or replan after
             * approval; they do not commit a pre-await snapshot. */
            const mountedLeaves = visualTiled() ? plan.leafIds.filter(contextMounted) : [];
            return runLeaves(
                mountedLeaves.map(function (id) {
                    return { tabId: id, nextHash: homeHash };
                }),
                'hide-secondary',
                function () {
                const fresh = workspaceModelApi.planSetMode(state, mode, presentation());
                if (!fresh.ok || fresh.kind !== 'hide') return false;
                const stillMounted = fresh.leafIds.filter(contextMounted);
                if (stillMounted.join(',') !== mountedLeaves.join(',')) return false;
                stillMounted.forEach(coldParkContext);
                commitCanonical(fresh.state);
                paintAndRestore(state.mainTabId);
                refreshFocusedPanel();
                return true;
            });
        }

        function setNarrowFallback(narrow) {
            const next = !!narrow;
            if (next === narrowFallback) return Promise.resolve(true);
            if (next) {
                const leaves = root.collectLeafTabIds(state.secondaryTree);
                const mountedLeaves = leaves.filter(contextMounted);
                if (!mountedLeaves.length) {
                    narrowFallback = true;
                    state.focusedTabId = state.mainTabId;
                    paintAndRestore(state.mainTabId);
                    refreshFocusedPanel();
                    return Promise.resolve(true);
                }
                /* Atomic across every mounted Secondary leaf: depth-first preflight order, stop
                 * at the first rejection, and no partial parking if any leaf rejects. */
                return runLeaves(
                    mountedLeaves.map(function (id) {
                        return { tabId: id, nextHash: homeHash };
                    }),
                    'hide-secondary',
                    function () {
                    const stillLeaves = root.collectLeafTabIds(state.secondaryTree);
                    if (stillLeaves.join(',') !== leaves.join(',')) return false;
                    mountedLeaves.forEach(coldParkContext);
                    narrowFallback = true;
                    state.focusedTabId = state.mainTabId;
                    paintAndRestore(state.mainTabId);
                    refreshFocusedPanel();
                    return true;
                });
            }
            narrowFallback = false;
            showWorkspaceStatus('');
            if (state.mode === MODE_TILED && state.secondaryTree) {
                state.focusedTabId = state.mainTabId;
                paintAndRestore(state.mainTabId);
                return mountAllSecondaryLeaves();
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

        function historyWant(tab, raw, locHash) {
            const idx = Number(raw && raw.historyIndex);
            if (tab && raw && Number.isFinite(idx) && idx >= 0 && idx < tab.history.length) {
                return {
                    route: canonical(tab.history[idx]),
                    historyIndex: idx,
                    fromHistory: true,
                };
            }
            return {
                route: canonical(locHash || (raw && raw.route) || (tab && tab.route) || homeHash),
                historyIndex: tab ? tab.historyIndex : 0,
                fromHistory: false,
            };
        }

        function applyWantToTab(tab, want) {
            if (want.fromHistory) tab.historyIndex = want.historyIndex;
            if (tab.route !== want.route) applyTabRoute(tab, want.route);
            if (!want.fromHistory && tab.history[tab.historyIndex] !== want.route) {
                tab.history[tab.historyIndex] = want.route;
            }
        }

        function restoreMainUrl() {
            const main = getMainTab();
            if (main) commitUrl(main, 'replace');
        }

        function handlePopState(eventState) {
            const raw = eventState && eventState.prksWorkspace ? eventState.prksWorkspace : null;
            const locHash = canonical(getHash());
            const targetId = raw && raw.tabId ? raw.tabId : null;
            const target = targetId ? getTab(targetId) : null;
            const main = getMainTab();
            const isVisibleSecondaryTarget = !!(target && visualTiled() && root.containsTab(state.secondaryTree, target.id));

            if (!main) {
                bootstrap(locHash);
                return Promise.resolve(invokeRender({ fromPopstate: true })).then(function () {
                    return true;
                });
            }

            if (isVisibleSecondaryTarget) {
                const want = historyWant(target, raw, locHash);
                const routeChanging = target.route !== want.route;
                const entries = [];
                if (routeChanging) entries.push({ tabId: target.id, nextHash: want.route });
                const promo = mainPromotionLeaveEntry(target.id);
                if (promo) entries.push(promo);
                const commitHistory = function () {
                    if (!getTab(target.id) || !root.containsTab(state.secondaryTree, target.id)) {
                        restoreMainUrl();
                        return false;
                    }
                    applyWantToTab(target, want);
                    if (!promoteSecondaryToMain(target.id)) {
                        restoreMainUrl();
                        return false;
                    }
                    markHandled();
                    paint();
                    if (routeChanging) {
                        return Promise.resolve(
                            invokeRender({
                                workspaceSwitch: false,
                                fromPopstate: true,
                                tabId: target.id,
                                hash: target.route,
                            })
                        ).then(function () {
                            return true;
                        });
                    }
                    publishShell(target.id);
                    refreshFocusedPanel();
                    return true;
                };
                const after = function (ok) {
                    if (!ok) {
                        restoreMainUrl();
                        return false;
                    }
                    return ok;
                };
                if (!entries.length) return Promise.resolve(commitHistory()).then(after);
                return runLeaves(entries, 'history', commitHistory).then(after);
            }

            if (!target || target.id === main.id) {
                const tab = main;
                const want = target ? historyWant(tab, raw, locHash) : historyWant(tab, null, locHash);
                const routeChanging = tab.route !== want.route;
                const commitHistory = function () {
                    if (target) applyWantToTab(tab, want);
                    else {
                        applyWantToTab(tab, want);
                        patchHistoryState();
                    }
                    markHandled();
                    paint();
                    if (routeChanging) {
                        return Promise.resolve(
                            invokeRender({
                                workspaceSwitch: false,
                                fromPopstate: true,
                                tabId: tab.id,
                                hash: tab.route,
                            })
                        ).then(function () {
                            return true;
                        });
                    }
                    return true;
                };
                const after = function (ok) {
                    if (!ok) {
                        restoreMainUrl();
                        return false;
                    }
                    return ok;
                };
                if (!routeChanging) return Promise.resolve(commitHistory()).then(after);
                return runLeave(tab.id, want.route, 'history', commitHistory).then(after);
            }

            const parkedWant = historyWant(target, raw, locHash);
            const leavingMainId = state.mainTabId;
            return runLeave(leavingMainId, parkedWant.route, 'history', function () {
                if (!getTab(target.id)) {
                    restoreMainUrl();
                    return false;
                }
                if (!outgoingMainStill(leavingMainId)) return false;
                if (leavingMainId !== target.id) warmParkContext(leavingMainId);
                /* A visually parked tab may still occupy a leaf in the preserved logical tree
                 * (Hide split / narrow fallback). Promotion uses the same eligibility rule as
                 * Make Main / startup: demote into that leaf only when the old Main is
                 * tileable; otherwise remove the leaf and keep the old Main parked. */
                if (root.containsTab(state.secondaryTree, target.id)) {
                    if (!promoteSecondaryToMain(target.id)) {
                        restoreMainUrl();
                        return false;
                    }
                } else {
                    setMain(target.id);
                }
                applyWantToTab(target, parkedWant);
                const resumed = mountContext(target.id);
                markHandled();
                paint();
                if (resumed) {
                    publishShell(target.id);
                    refreshFocusedPanel();
                    return true;
                }
                return Promise.resolve(
                    invokeRender({
                        workspaceSwitch: false,
                        fromPopstate: true,
                        tabId: target.id,
                        hash: target.route,
                    })
                ).then(function () {
                    return true;
                });
            }).then(function (ok) {
                if (!ok) {
                    restoreMainUrl();
                    return false;
                }
                return ok;
            });
        }

        function handleHashChange() {
            if (isDuplicateLocation()) return true;
            return false;
        }

        function peekRenderGen() {
            return lastRenderGen;
        }

        /** Nested split ratios are node-local canonical preference, same discipline as the root
         * mainSplitRatio. Persistence reads them from workspace state; this setter never
         * touches storage. `options.paint === false` skips a full repaint (used by drag). */
        function setNestedSplitRatio(splitId, ratio, options) {
            const plan = workspaceModelApi.planNestedSplitRatio(state, splitId, ratio);
            if (!plan.ok) return null;
            commitCanonical(plan.state);
            if (options && options.paint === false) {
                noteCanonicalChange();
                return plan.ratio;
            }
            paint();
            return plan.ratio;
        }

        function canAddSecondaryLeaf() {
            return !paneCapReached();
        }

        function isNarrowFallback() {
            return narrowFallback;
        }

        return {
            bootstrap: bootstrap,
            navigate: navigate,
            openTab: openTab,
            activateTab: activateTab,
            closeTab: closeTab,
            closeOtherTabs: closeOtherTabs,
            closeTabsToTheRight: closeTabsToTheRight,
            focusTab: focusTab,
            makeMain: makeMain,
            tileTab: tileTab,
            splitLeaf: splitLeafAction,
            hideLeaf: hideLeaf,
            movePane: movePane,
            reorderTab: reorderTab,
            moveTabStep: moveTabStep,
            canAddSecondaryLeaf: canAddSecondaryLeaf,
            setNestedSplitRatio: setNestedSplitRatio,
            findTabByRoute: findTabByRoute,
            setMode: setMode,
            isNarrowFallback: isNarrowFallback,
            setNarrowFallback: setNarrowFallback,
            setResolvedTitle: setResolvedTitle,
            setResolvedTitleForTab: setResolvedTitleForTab,
            getMainSplitRatio: getMainSplitRatio,
            setMainSplitRatio: setMainSplitRatio,
            resetMainSplitRatio: resetMainSplitRatio,
            snapshot: snapshot,
            adoptLocation: adoptLocation,
            handlePopState: handlePopState,
            handleHashChange: handleHashChange,
            isDuplicateLocation: isDuplicateLocation,
            markHandled: markHandled,
            peekRenderGen: peekRenderGen,
            visualTiled: visualTiled,
            subscribe: subscribeProjection,
            publish: publishProjection,
            onShellCommit: onShellCommit,
            queueFocusRestore: queueFocusRestore,
            refreshTabStatus: refreshTabStatus,
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
    let pendingHistoryNavigation = null;
    let tabFocusIndex = 0;
    const priorNavigate = root.prksNavigate;


    function liveEl() {
        if (typeof document === 'undefined') return null;
        return document.getElementById('prks-workspace-live');
    }

    function statusEl() {
        if (typeof document === 'undefined') return null;
        return document.getElementById('prks-workspace-status');
    }

    /* Temporary sighted feedback for explicit split/fallback refusals. SR users already hear
     * `#prks-workspace-live`; this element is visual-only (no aria-live) so the message is not
     * double-announced. Repeated identical calls only refresh the dismiss timer. */
    const WORKSPACE_STATUS_MS = 5000;
    const NARROW_SPLIT_MESSAGE = 'Split view needs a wider workspace. The tab remains open.';
    let workspaceStatusTimer = null;
    let workspaceLiveRestoreTimer = null;

    function showWorkspaceStatus(message) {
        const el = statusEl();
        const text = String(message || '');
        if (!el) return;
        if (!text) {
            if (workspaceStatusTimer) {
                clearTimeout(workspaceStatusTimer);
                workspaceStatusTimer = null;
            }
            el.textContent = '';
            el.hidden = true;
            return;
        }
        el.hidden = false;
        el.textContent = text;
        if (workspaceStatusTimer) clearTimeout(workspaceStatusTimer);
        workspaceStatusTimer = setTimeout(function () {
            workspaceStatusTimer = null;
            if (el.textContent === text) {
                el.textContent = '';
                el.hidden = true;
            }
        }, WORKSPACE_STATUS_MS);
    }

    function paintSplitControl() {
        if (typeof document === 'undefined' || !production) return;
        const btn = document.getElementById('prks-workspace-tile-layout');
        if (!btn) return;
        const snap = production.snapshot();
        const hasTree = !!snap.secondaryTree;
        const visual =
            typeof production.visualTiled === 'function' ? production.visualTiled() : snap.mode === MODE_TILED;
        const labelEl = btn.querySelector('.prks-workspace-split-btn__label');
        const narrowBlocked = hasTree && snap.mode === MODE_TILED && !visual;
        btn.classList.toggle('is-active', visual);
        btn.setAttribute('aria-pressed', visual ? 'true' : 'false');
        if (narrowBlocked) {
            if (labelEl) labelEl.textContent = 'Show split';
            btn.setAttribute('aria-label', 'Split unavailable at this width');
            btn.setAttribute('title', NARROW_SPLIT_MESSAGE);
            return;
        }
        if (visual) {
            if (labelEl) labelEl.textContent = 'Hide split';
            btn.setAttribute('aria-label', 'Hide split view');
            btn.setAttribute('title', 'Hide split view');
            return;
        }
        if (hasTree) {
            if (labelEl) labelEl.textContent = 'Show split';
            btn.setAttribute('aria-label', 'Show split view');
            btn.setAttribute('title', 'Show split view');
            return;
        }
        if (labelEl) labelEl.textContent = 'Split';
        btn.setAttribute('aria-label', 'Open split view');
        btn.setAttribute('title', 'Open a page beside the Main tab');
    }

    function announce(title, kind) {
        const el = liveEl();
        const label = String(title || 'page');
        let text = '';
        if (kind === 'promote') {
            text = 'Opened as main because this view is not available in split view yet.';
        } else if (kind === 'narrow') {
            text = NARROW_SPLIT_MESSAGE;
        } else if (kind === 'cap') {
            text = 'Maximum of 4 visible panes. Close or hide a pane to split again.';
        } else if (kind === 'ambiguous') {
            text = 'Focus a split pane, then use Split right or Split down.';
        } else if (kind === 'hide-pane') {
            text = 'Hid ' + label + ' from split view. It remains open as a tab.';
        } else if (kind === 'split' || kind === 'tile') {
            text = 'Opened ' + label + ' in split view';
        } else {
            text = 'Opened ' + label + ' in a new PRKS tab';
        }
        if (el) {
            /* Clear then restore on a later task so repeated identical messages (e.g. a second
             * Show-split while still narrow) are observed by aria-live engines that coalesce
             * same-tick clear+write of the same string. */
            el.textContent = '';
            if (workspaceLiveRestoreTimer) clearTimeout(workspaceLiveRestoreTimer);
            workspaceLiveRestoreTimer = setTimeout(function () {
                workspaceLiveRestoreTimer = null;
                el.textContent = text;
            }, 0);
        }
        /* Visible feedback only for the narrow refusal: other announce kinds already have a
         * clear UI outcome (tile appears, tab opens, promotion happens) or remain SR guidance.
         * Layout reconciliation never calls announce('narrow') — only explicit split/Show-split.
         * Successful/other announces clear any lingering narrow notice so it cannot outlive a
         * later successful tile. */
        if (kind === 'narrow') showWorkspaceStatus(text);
        else showWorkspaceStatus('');
    }

    function tabStatusKind(tabId) {
        if (!tabId || typeof root.prksGetTabContext !== 'function') return '';
        const ctx = root.prksGetTabContext(tabId);
        if (!ctx || ctx.destroyed || (!ctx.mounted && !ctx.suspended)) return '';
        const notes = ctx.getResource ? ctx.getResource('workNotes') : null;
        const pdf = ctx.getResource ? ctx.getResource('pdf') : null;
        const pdfErr = !!(pdf && pdf.syncState && pdf.syncState.lastError);
        const notesErr = !!(notes && notes.saveError);
        if (pdfErr || notesErr) return 'error';
        const saveToken = notes ? Number(notes.latestSaveToken) || 0 : 0;
        const settledToken = notes ? Number(notes.settledSaveToken) || 0 : 0;
        const notesSaving = !!(notes && saveToken > settledToken);
        const pdfSaving =
            typeof root.prksHasPendingWorkAnnotationSync === 'function' &&
            root.prksHasPendingWorkAnnotationSync(ctx);
        if (notesSaving || pdfSaving) return 'saving';
        const editGen = notes ? Number(notes.editGeneration) || 0 : 0;
        const savedEdit = notes ? Number(notes.latestSaveEditGeneration) || 0 : 0;
        if (notes && (notes.drafting || editGen > savedEdit)) return 'drafting';
        return '';
    }

    function statusLabel(kind) {
        if (kind === 'error') return 'Save error';
        if (kind === 'saving') return 'Saving';
        if (kind === 'drafting') return 'Drafting';
        return '';
    }

    function applyTabStatus(wrap, tabId) {
        if (!wrap) return;
        let el = wrap.querySelector(':scope > .prks-workspace-tab__status');
        const kind = tabStatusKind(tabId);
        const activate = wrap.querySelector('.prks-workspace-tab__activate');
        if (!kind) {
            if (el) el.hidden = true;
            if (activate) activate.removeAttribute('aria-busy');
            return;
        }
        if (!el) {
            el = document.createElement('span');
            el.className = 'prks-workspace-tab__status';
            const close = wrap.querySelector('.prks-workspace-tab__close');
            wrap.insertBefore(el, close || null);
        }
        el.hidden = false;
        el.className = 'prks-workspace-tab__status prks-workspace-tab__status--' + kind;
        const label = statusLabel(kind);
        el.title = label;
        el.setAttribute('aria-label', label);
        if (activate) {
            if (kind === 'saving') activate.setAttribute('aria-busy', 'true');
            else activate.removeAttribute('aria-busy');
        }
    }

    function prksWorkspaceRefreshTabStatus(tabId) {
        if (root.__prksWorkspaceShellOwned && production && typeof production.refreshTabStatus === 'function') {
            production.refreshTabStatus(tabId);
            return;
        }
        if (typeof document === 'undefined' || !tabId) return;
        const list = document.getElementById('prks-workspace-tabs');
        if (!list) return;
        const wrap = list.querySelector('.prks-workspace-tab[data-tab-id="' + String(tabId).replace(/"/g, '') + '"]');
        if (wrap) applyTabStatus(wrap, tabId);
        updateTabOverflow();
    }

    function revealWorkspaceTab(tabId) {
        if (typeof document === 'undefined' || !tabId) return;
        const list = document.getElementById('prks-workspace-tabs');
        if (!list) return;
        const wrap = list.querySelector('.prks-workspace-tab[data-tab-id="' + String(tabId).replace(/"/g, '') + '"]');
        if (!wrap) return;
        const lr = list.getBoundingClientRect();
        const wr = wrap.getBoundingClientRect();
        if (wr.left < lr.left) list.scrollLeft += wr.left - lr.left - 8;
        else if (wr.right > lr.right) list.scrollLeft += wr.right - lr.right + 8;
    }

    function updateTabOverflow() {
        if (typeof document === 'undefined') return;
        const list = document.getElementById('prks-workspace-tabs');
        const btn = document.getElementById('prks-workspace-tab-overflow');
        if (!list || !btn) return;
        const overflow = list.clientWidth > 0 && list.scrollWidth > list.clientWidth + 2;
        btn.hidden = !overflow;
        if (!overflow) btn.setAttribute('aria-expanded', 'false');
    }

    function syncTrailingTabStops() {
        if (typeof document === 'undefined') return;
        const list = document.getElementById('prks-workspace-tabs');
        if (!list) return;
        const wraps = list.querySelectorAll('.prks-workspace-tab');
        let activeWrap = null;
        const ae = document.activeElement;
        if (ae && ae.closest) activeWrap = ae.closest('.prks-workspace-tab');
        if (!activeWrap) {
            const buttons = list.querySelectorAll('.prks-workspace-tab__activate');
            for (let i = 0; i < buttons.length; i++) {
                if (buttons[i].tabIndex === 0) {
                    activeWrap = buttons[i].closest('.prks-workspace-tab');
                    break;
                }
            }
        }
        for (let i = 0; i < wraps.length; i++) {
            const enable = wraps[i] === activeWrap;
            const split = wraps[i].querySelector(':scope > .prks-workspace-tab__split');
            const close = wraps[i].querySelector(':scope > .prks-workspace-tab__close');
            if (split) split.tabIndex = enable ? 0 : -1;
            if (close) close.tabIndex = enable ? 0 : -1;
        }
    }

    function bindTabStripChrome() {
        if (typeof document === 'undefined') return;
        const list = document.getElementById('prks-workspace-tabs');
        if (!list || list.getAttribute('data-workspace-overflow-bound') === '1') return;
        list.setAttribute('data-workspace-overflow-bound', '1');
        list.addEventListener(
            'wheel',
            function (e) {
                if (list.scrollWidth <= list.clientWidth) return;
                if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;
                list.scrollLeft += e.deltaY;
                e.preventDefault();
            },
            { passive: false }
        );
        list.addEventListener('scroll', updateTabOverflow);
        list.addEventListener('focusin', syncTrailingTabStops);
        if (typeof ResizeObserver !== 'undefined') {
            const ro = new ResizeObserver(function () {
                updateTabOverflow();
            });
            ro.observe(list);
        }
    }

    /* Tab-strip and pane DOM are painted by the Vue workspace shell.
     * Removed from this production renderer: tabRoleFlags, syncTabTrailing,
     * createTabWrap, applyTabWrap, and paintProduction's DOM reconciliation.
     * Coordinator effects (leave, mount, URL, persistence) stay here.
     * prksWorkspaceTabKeydown still owns roving tabindex and keyboard activation.
     */

    function paintProduction() {
        if (typeof document === 'undefined' || !production) return;
        bindTabStripChrome();
        paintSplitControl();
    }

    /**
     * DOM effects for one painted projection. Overflow is measured before reveal
     * so showing the overflow control cannot clamp the scroll we just applied.
     */
    function prksWorkspaceApplyShellDomEffects(rendered) {
        if (typeof document === 'undefined' || !rendered || !rendered.state) return;
        const snap = rendered.state;
        updateTabOverflow();
        syncTrailingTabStops();
        paintSplitControl();
        revealWorkspaceTab(snap.mainTabId);
        if (rendered.visualTiled && snap.focusedTabId && snap.focusedTabId !== snap.mainTabId) {
            revealWorkspaceTab(snap.focusedTabId);
        }
    }

    function prksWorkspaceOnShellCommit(rendered) {
        if (!production || typeof production.onShellCommit !== 'function') return;
        production.onShellCommit(rendered);
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
        syncTrailingTabStops();
        if (scroll) {
            const wrap = buttons[i].closest('[data-tab-id]');
            if (wrap) revealWorkspaceTab(wrap.getAttribute('data-tab-id'));
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
            return;
        }
        if (key === 'ContextMenu' || (key === 'F10' && e.shiftKey)) {
            e.preventDefault();
            const wrap = e.currentTarget.closest('[data-tab-id]');
            const id = wrap && wrap.getAttribute('data-tab-id');
            if (id && typeof root.prksWorkspaceOpenTabMenu === 'function') {
                root.prksWorkspaceOpenTabMenu(id, e);
            }
        }
    }

    /**
     * Move DOM focus to `tabId`. A tile that is still connected but is not the
     * restore target is stale (the closed Secondary pane can still be in the
     * document when this used to run before Vue patched).
     */
    function prksWorkspaceApplyRestoredFocus(tabId) {
        if (typeof document === 'undefined' || !tabId) return;
        const d = document;
        const active = d.activeElement;
        if (active && active !== d.body && d.contains(active)) {
            const tile = active.closest && active.closest('.prks-tile[data-prks-tab-id]');
            if (tile) {
                if (tile.getAttribute('data-prks-tab-id') === String(tabId)) return;
            } else if (active.closest && active.closest('#prks-command-palette')) return;
            else if (active.closest && active.closest('.prks-workspace-menu')) return;
            else {
                const tabWrap = active.closest && active.closest('.prks-workspace-tab[data-tab-id]');
                if (tabWrap && tabWrap.getAttribute('data-tab-id') === String(tabId)) return;
            }
        }
        const tile = d.querySelector(
            '.prks-tile[data-prks-tab-id="' + String(tabId).replace(/"/g, '') + '"]'
        );
        if (tile && typeof tile.focus === 'function') {
            tile.setAttribute('tabindex', '-1');
            try {
                tile.focus({ preventScroll: true });
            } catch (_e) {
                tile.focus();
            }
            revealWorkspaceTab(tabId);
            return;
        }
        const btn = d.querySelector(
            '.prks-workspace-tab[data-tab-id="' +
                String(tabId).replace(/"/g, '') +
                '"] .prks-workspace-tab__activate'
        );
        if (btn && typeof btn.focus === 'function') {
            try {
                btn.focus({ preventScroll: true });
            } catch (_e2) {
                btn.focus();
            }
            revealWorkspaceTab(tabId);
        }
    }

    function prksWorkspaceRestoreFocus(tabId) {
        if (typeof document === 'undefined' || !tabId) return;
        if (root.__prksWorkspaceShellOwned && production && typeof production.queueFocusRestore === 'function') {
            production.queueFocusRestore(tabId);
            return;
        }
        const d = document;
        const active = d.activeElement;
        if (active && active !== d.body && d.contains(active)) {
            const tile = active.closest && active.closest('.prks-tile[data-prks-tab-id]');
            if (tile && tile.getAttribute('data-prks-tab-id') === String(tabId)) return;
            if (!tile) {
                if (active.closest && active.closest('#prks-command-palette')) return;
                if (active.closest && active.closest('.prks-workspace-menu')) return;
                const tabWrap = active.closest && active.closest('.prks-workspace-tab[data-tab-id]');
                if (tabWrap && tabWrap.getAttribute('data-tab-id') === tabId) return;
            }
        }
        if (typeof root.requestAnimationFrame === 'function') {
            root.requestAnimationFrame(function () {
                prksWorkspaceApplyRestoredFocus(tabId);
            });
            return;
        }
        prksWorkspaceApplyRestoredFocus(tabId);
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
            /* The shared right panel (#panel-content) lives outside every tile's DOM, so
             * neither lookup above ever matches a click that originates inside it. It already
             * records its own owner (panel.dataset.prksOwnerTabId); use that recorded owner
             * instead of falling through to Main or the focused tab, which would misattribute
             * a Secondary-owned panel link's navigation to whichever tab happens to be Main. */
            const panel = e.target.closest('#panel-content');
            if (panel) {
                const ownerTabId = panel.dataset ? panel.dataset.prksOwnerTabId : '';
                if (
                    ownerTabId &&
                    typeof root.prksGetTabContext === 'function' &&
                    root.prksGetTabContext(ownerTabId)
                ) {
                    return ownerTabId;
                }
            }
        }
        /* Main fallback remains only for genuinely shell-global links/elements that have no
         * TabContext or right-panel owner at all. */
        return production ? production.getMainTabId() : null;
    }

    function handleNavEvent(e) {
        if (!e || !e.target || !e.target.closest) return;
        if (isPdfWikiLink(e.target)) return;
        const intent = prksWorkspaceNavigationIntent(e);
        if (intent === 'ignore') return;
        const navEl = e.target.closest('[data-prks-route], a[href]');
        if (!navEl) return;
        // A destination the owning component has explicitly marked disabled is
        // never navigated by this layer, in any intent (same tab, background
        // tab, tile). Offline pages use this to keep a relationship's real href
        // inspectable while explaining that the destination is not cached, so
        // this must bow out entirely -- no preventDefault, no stopPropagation --
        // and let that component's own handler give the feedback.
        if (navEl.getAttribute && navEl.getAttribute('aria-disabled') === 'true') return;
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
            const hasTree = !!snap.secondaryTree;
            const visual =
                typeof production.visualTiled === 'function' ? production.visualTiled() : snap.mode === MODE_TILED;
            if (!hasTree) {
                if (typeof root.prksOpenCommandPalette === 'function') {
                    root.prksOpenCommandPalette({ scope: 'all', navigationTarget: 'tile' });
                }
                return;
            }
            if (snap.mode === MODE_TILED && !visual) {
                announce('', 'narrow');
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
            const pending = Promise.resolve(production.handlePopState(ev && ev.state)).catch(function () {
                return false;
            });
            pendingHistoryNavigation = pending;
            void pending.then(function () {
                if (pendingHistoryNavigation === pending) pendingHistoryNavigation = null;
            });
        });
        root.addEventListener('hashchange', function () {
            const finishHashChange = function () {
                if (!production) {
                    if (typeof root.handleRoute === 'function') void root.handleRoute();
                    return;
                }
                if (production.handleHashChange()) return;
                if (typeof root.handleRoute === 'function') void root.handleRoute({ fromWorkspace: false });
                production.markHandled();
            };
            const pending = pendingHistoryNavigation;
            if (pending) {
                void pending.then(finishHashChange);
                return;
            }
            finishHashChange();
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
            renderRoute: function (options) {
                if (typeof root.handleRoute === 'function') return root.handleRoute(options);
                return undefined;
            },
            onChange: paintProduction,
            announce: announce,
            publishMainShell: function (tabId, title) {
                if (typeof root.prksPublishMainShell === 'function') {
                    const ctx =
                        typeof root.prksGetTabContext === 'function' ? root.prksGetTabContext(tabId) : null;
                    const opts = {};
                    if (title) opts.entityTitle = title;
                    root.prksPublishMainShell(ctx, opts);
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
        /* Vue paints the shell. Set this before bootstrap so the first publish
         * is the presentation path and the legacy tile reconciler is not used. */
        root.__prksWorkspaceShellOwned = true;
        const ws = ensureProduction();
        if (!productionReady) {
            ws.bootstrap();
            productionReady = true;
            if (typeof root.prksWorkspaceInitTiles === 'function') root.prksWorkspaceInitTiles();
        }
        bindLinkLayer();
        bindHistory();
        bindNewTabButton();
        bindTileLayoutButton();
        paintProduction();
        if (typeof root.prksWorkspaceInitTabMenus === 'function') root.prksWorkspaceInitTabMenus();
        if (typeof root.prksWorkspaceInitDrag === 'function') root.prksWorkspaceInitDrag();
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

    function prksWorkspaceCloseOtherTabs(tabId) {
        if (!production || typeof production.closeOtherTabs !== 'function') return Promise.resolve(false);
        return production.closeOtherTabs(tabId);
    }

    function prksWorkspaceCloseTabsToTheRight(tabId) {
        if (!production || typeof production.closeTabsToTheRight !== 'function') return Promise.resolve(false);
        return production.closeTabsToTheRight(tabId);
    }

    function prksWorkspaceFocusTab(tabId) {
        if (!production) return false;
        return production.focusTab(tabId);
    }

    function prksWorkspaceMakeMain(tabId) {
        if (!production) return Promise.resolve(false);
        /* makeMain() may reject the promotion asynchronously (a leave preflight). Callers
         * (e.g. workspace-tab-menu.js) intentionally discard the returned promise with `void`;
         * make sure it can never surface as an unhandled rejection. */
        return Promise.resolve(production.makeMain(tabId)).catch(function () {
            return false;
        });
    }

    function prksWorkspaceTileTab(tabId) {
        if (!production) return Promise.resolve(false);
        return production.tileTab(tabId);
    }

    /** Split right (axis 'left-right') / Split down (axis 'top-bottom') for `targetTabId`, a
     * currently-visible Secondary leaf. `options.tabId` reuses an existing tab; `options.hash`
     * creates a new one. */
    function prksWorkspaceSplitLeaf(targetTabId, axis, options) {
        if (!production || typeof production.splitLeaf !== 'function') return Promise.resolve(false);
        return production.splitLeaf(targetTabId, axis, options);
    }

    /** Removes `tabId`'s leaf from the Secondary tree while keeping the tab open and parked. */
    function prksWorkspaceHideLeaf(tabId) {
        if (!production || typeof production.hideLeaf !== 'function') return Promise.resolve(false);
        return production.hideLeaf(tabId);
    }

    /** Atomic spatial move of an already-visible Secondary leaf (`sourceTabId`) relative to
     * another visible Secondary leaf (`targetTabId`); see `movePane` above. Used by pane-handle
     * drag and by dragging a visible Secondary's global tab onto another pane's edge zone. */
    function prksWorkspaceMovePane(sourceTabId, targetTabId, axis, placement) {
        if (!production || typeof production.movePane !== 'function') return false;
        return production.movePane(sourceTabId, targetTabId, axis, placement);
    }

    /** Canonical tab-strip reorder; see `reorderTab` above. Used by drag-drop tab reordering. */
    function prksWorkspaceReorderTab(tabId, beforeTabId) {
        if (!production || typeof production.reorderTab !== 'function') return false;
        return production.reorderTab(tabId, beforeTabId);
    }

    /** Move-left/right tab-strip step; see `moveTabStep` above. Used by the tab context menu's
     * "Move tab left" / "Move tab right" keyboard-friendly commands. */
    function prksWorkspaceMoveTabStep(tabId, direction) {
        if (!production || typeof production.moveTabStep !== 'function') return false;
        return production.moveTabStep(tabId, direction);
    }

    /** True while the physical viewport is too narrow to show Secondary geometry (spatial
     * drag/drop must not offer pane targets in that state; tab-strip reordering still may). */
    function prksWorkspaceIsNarrowFallback() {
        if (!production || typeof production.isNarrowFallback !== 'function') return false;
        return production.isNarrowFallback();
    }

    function prksWorkspaceCanAddSecondaryLeaf() {
        if (!production || typeof production.canAddSecondaryLeaf !== 'function') return true;
        return production.canAddSecondaryLeaf();
    }

    function prksWorkspaceSetNestedSplitRatio(splitId, ratio, options) {
        if (!production || typeof production.setNestedSplitRatio !== 'function') return null;
        return production.setNestedSplitRatio(splitId, ratio, options);
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

    function prksWorkspaceGetSplitRatio() {
        if (!production) return DEFAULT_MAIN_SPLIT_RATIO;
        return production.getMainSplitRatio();
    }

    function prksWorkspaceSetMainSplitRatio(ratio, options) {
        const ws = ensureProduction();
        if (!productionReady) {
            ws.bootstrap();
            productionReady = true;
        }
        return ws.setMainSplitRatio(ratio, options);
    }

    function prksWorkspaceResetMainSplitRatio(options) {
        const ws = ensureProduction();
        if (!productionReady) {
            ws.bootstrap();
            productionReady = true;
        }
        return ws.resetMainSplitRatio(options);
    }

    function prksWorkspaceDefaultSplitRatio() {
        return DEFAULT_MAIN_SPLIT_RATIO;
    }

    function emptySnapshot() {
        return {
            version: WORKSPACE_VERSION,
            mode: MODE_STACKED,
            mainTabId: null,
            focusedTabId: null,
            secondaryTree: null,
            tabs: [],
            mainSplitRatio: DEFAULT_MAIN_SPLIT_RATIO,
        };
    }

    function prksWorkspaceSnapshot() {
        if (!production) return emptySnapshot();
        return production.snapshot();
    }

    /**
     * Subscribe to detached workspace projections. The listener is called
     * immediately with the current projection and again after each committed
     * canonical change. Returns an unsubscribe function. Multiple subscribers
     * share one coordinator; none of them own state. Usable without Vue.
     */
    function prksWorkspaceSubscribe(listener) {
        const ws = ensureProduction();
        if (!ws || typeof ws.subscribe !== 'function') return function () {};
        return ws.subscribe(listener);
    }

    function prksWorkspaceRepublish() {
        if (!production || typeof production.publish !== 'function') return;
        production.publish();
    }

    function prksWorkspaceFindTabByRoute(hash, options) {
        if (!production || typeof production.findTabByRoute !== 'function') return null;
        return production.findTabByRoute(hash, options);
    }

    function prksWorkspaceVisualTiled() {
        if (!production || typeof production.visualTiled !== 'function') return false;
        return production.visualTiled();
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
        prksWorkspaceCloseOtherTabs: prksWorkspaceCloseOtherTabs,
        prksWorkspaceCloseTabsToTheRight: prksWorkspaceCloseTabsToTheRight,
        prksWorkspaceRestoreFocus: prksWorkspaceRestoreFocus,
        prksWorkspaceApplyRestoredFocus: prksWorkspaceApplyRestoredFocus,
        prksWorkspaceRefreshTabStatus: prksWorkspaceRefreshTabStatus,
        prksWorkspaceSubscribe: prksWorkspaceSubscribe,
        prksWorkspaceRepublish: prksWorkspaceRepublish,
        prksWorkspaceTabKeydown: onTabKeydown,
        prksWorkspaceOnShellCommit: prksWorkspaceOnShellCommit,
        prksWorkspaceApplyShellDomEffects: prksWorkspaceApplyShellDomEffects,
        prksWorkspaceFocusTab: prksWorkspaceFocusTab,
        prksWorkspaceMakeMain: prksWorkspaceMakeMain,
        prksWorkspaceTileTab: prksWorkspaceTileTab,
        prksWorkspaceSplitLeaf: prksWorkspaceSplitLeaf,
        prksWorkspaceHideLeaf: prksWorkspaceHideLeaf,
        prksWorkspaceMovePane: prksWorkspaceMovePane,
        prksWorkspaceReorderTab: prksWorkspaceReorderTab,
        prksWorkspaceMoveTabStep: prksWorkspaceMoveTabStep,
        prksWorkspaceIsNarrowFallback: prksWorkspaceIsNarrowFallback,
        prksWorkspaceCanAddSecondaryLeaf: prksWorkspaceCanAddSecondaryLeaf,
        prksWorkspaceSetNestedSplitRatio: prksWorkspaceSetNestedSplitRatio,
        prksWorkspaceFindTabByRoute: prksWorkspaceFindTabByRoute,
        prksWorkspaceVisualTiled: prksWorkspaceVisualTiled,
        prksWorkspaceSetMode: prksWorkspaceSetMode,
        prksWorkspaceSetNarrowFallback: prksWorkspaceSetNarrowFallback,
        prksWorkspaceSetResolvedTitle: prksWorkspaceSetResolvedTitle,
        prksWorkspaceSetResolvedTitleForTab: prksWorkspaceSetResolvedTitleForTab,
        prksWorkspaceGetSplitRatio: prksWorkspaceGetSplitRatio,
        prksWorkspaceSetMainSplitRatio: prksWorkspaceSetMainSplitRatio,
        prksWorkspaceResetMainSplitRatio: prksWorkspaceResetMainSplitRatio,
        prksWorkspaceDefaultSplitRatio: prksWorkspaceDefaultSplitRatio,
        prksWorkspaceAdoptLocation: prksWorkspaceAdoptLocation,
        prksWorkspaceSnapshot: prksWorkspaceSnapshot,
        prksNavigate: prksNavigate,
        /* Test seam for the production announce/status path (Node selftests stub document). */
        prksWorkspaceAnnounceForTest: announce,
        prksWorkspaceShowStatusForTest: showWorkspaceStatus,
        prksWorkspaceNarrowSplitMessageForTest: NARROW_SPLIT_MESSAGE,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
