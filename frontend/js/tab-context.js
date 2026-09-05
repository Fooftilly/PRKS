/**
 * Per-workspace-tab runtime. Parked contexts are inert: no DOM, network,
 * AbortController, timers, or live resources. Canonical library data is not cached.
 */
(function (root) {
    'use strict';

    const contexts = new Map();

    function sanitizeTabId(tabId) {
        const raw = String(tabId == null ? 'tab' : tabId);
        const cleaned = raw.replace(/[^a-zA-Z0-9_-]/g, '-');
        return cleaned || 'tab';
    }

    function emptyUi() {
        return {
            rightPanelTab: 'details',
            personDetailEditing: false,
            personWorksEditing: false,
            personGroupEditing: false,
            personGroupMembersEditing: false,
            playlistEditing: false,
            playlistRename: {},
            argumentEditing: false,
            workFolderEditing: false,
            workPlaylistEditing: false,
            currentSavedView: null,
            researchNotesHints: null,
        };
    }

    function resetEditUi(ui) {
        ui.personDetailEditing = false;
        ui.personWorksEditing = false;
        ui.personGroupEditing = false;
        ui.personGroupMembersEditing = false;
        ui.playlistEditing = false;
        ui.playlistRename = {};
        ui.argumentEditing = false;
        ui.workFolderEditing = false;
        ui.workPlaylistEditing = false;
        ui.currentSavedView = null;
        ui.researchNotesHints = null;
    }

    function safeCall(fn, kind) {
        if (typeof fn !== 'function') return;
        try {
            fn();
        } catch (_e) {
            if (typeof root.prksLogSafe === 'function') {
                try {
                    root.prksLogSafe('tab_context_cleanup_failed', { resource: String(kind || 'callback') });
                } catch (_ignore) {}
            }
        }
    }

    function createAbort() {
        if (typeof AbortController === 'undefined') {
            return { abort: function () {}, signal: { aborted: false } };
        }
        return new AbortController();
    }

    function createRootElement(tabId) {
        if (typeof document === 'undefined' || !document.createElement) {
            const attrs = { class: 'prks-tab-root', 'data-prks-tab-id': tabId };
            const kids = [];
            const el = {
                nodeType: 1,
                className: 'prks-tab-root',
                children: kids,
                childNodes: kids,
                parentNode: null,
                innerHTML: '',
                dataset: { prksTabId: tabId },
                setAttribute: function (k, v) {
                    attrs[k] = v;
                    if (k === 'data-prks-tab-id') el.dataset.prksTabId = v;
                },
                getAttribute: function (k) {
                    return Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null;
                },
                appendChild: function (c) {
                    kids.push(c);
                    c.parentNode = el;
                    return c;
                },
                removeChild: function (c) {
                    const i = kids.indexOf(c);
                    if (i >= 0) kids.splice(i, 1);
                    c.parentNode = null;
                    return c;
                },
                querySelector: function () {
                    return null;
                },
                querySelectorAll: function () {
                    return [];
                },
                closest: function (sel) {
                    if (sel && String(sel).indexOf('prks-tab-root') !== -1) return el;
                    return null;
                },
                removeAttribute: function (k) {
                    delete attrs[k];
                },
                setAttributeNS: function () {},
            };
            return el;
        }
        const el = document.createElement('div');
        el.className = 'prks-tab-root';
        el.setAttribute('data-prks-tab-id', tabId);
        return el;
    }

    function createPrksTabContext(tabId) {
        const id = String(tabId);
        const ctx = {
            tabId: id,
            mounted: false,
            destroyed: false,
            host: null,
            root: null,
            route: null,
            lastResolvedRoute: null,
            generation: 0,
            abortController: null,
            entity: null,
            routeSidebar: {},
            navigation: {
                routeStates: new Map(),
                origins: new Map(),
            },
            ui: emptyUi(),
            resources: new Map(),
            timers: new Map(),
            cleanupCallbacks: new Set(),
        };

        function assertAlive() {
            return !ctx.destroyed;
        }

        ctx.domId = function (localName) {
            const local = String(localName == null ? '' : localName).replace(/[^a-zA-Z0-9_-]/g, '-');
            return 'prks-tab-' + sanitizeTabId(id) + '-' + (local || 'id');
        };

        ctx.query = function (selector) {
            if (!ctx.root || typeof ctx.root.querySelector !== 'function') return null;
            try {
                return ctx.root.querySelector(selector);
            } catch (_e) {
                return null;
            }
        };

        ctx.queryAll = function (selector) {
            if (!ctx.root || typeof ctx.root.querySelectorAll !== 'function') return [];
            try {
                return Array.prototype.slice.call(ctx.root.querySelectorAll(selector));
            } catch (_e) {
                return [];
            }
        };

        ctx.isCurrent = function (generation) {
            if (ctx.destroyed || !ctx.mounted) return false;
            if (typeof generation !== 'number') return true;
            return generation === ctx.generation;
        };

        ctx.setEntity = function (type, value) {
            if (!assertAlive()) return null;
            if (value == null) {
                ctx.entity = null;
                return null;
            }
            ctx.entity = { type: String(type || ''), value: value };
            return ctx.entity;
        };

        ctx.getEntity = function (type) {
            if (!ctx.entity) return null;
            if (type == null || type === '') return ctx.entity.value;
            if (ctx.entity.type !== String(type)) return null;
            return ctx.entity.value;
        };

        ctx.setResource = function (name, value, disposer) {
            if (!assertAlive()) return value;
            const key = String(name);
            ctx.clearResource(key);
            ctx.resources.set(key, { value: value, disposer: disposer });
            return value;
        };

        ctx.getResource = function (name) {
            const rec = ctx.resources.get(String(name));
            return rec ? rec.value : undefined;
        };

        ctx.clearResource = function (name) {
            const key = String(name);
            const rec = ctx.resources.get(key);
            if (!rec) return;
            ctx.resources.delete(key);
            safeCall(rec.disposer, key);
        };

        ctx.registerCleanup = function (fn) {
            if (!assertAlive() || typeof fn !== 'function') return function () {};
            ctx.cleanupCallbacks.add(fn);
            return function () {
                ctx.cleanupCallbacks.delete(fn);
            };
        };

        ctx.setTimer = function (name, timerId) {
            if (!assertAlive()) return timerId;
            const key = String(name);
            ctx.clearTimer(key);
            ctx.timers.set(key, timerId);
            return timerId;
        };

        ctx.clearTimer = function (name) {
            const key = String(name);
            const timerId = ctx.timers.get(key);
            ctx.timers.delete(key);
            if (timerId == null) return;
            try {
                clearTimeout(timerId);
            } catch (_e) {}
            try {
                clearInterval(timerId);
            } catch (_e) {}
        };

        function clearAllTimers() {
            const names = Array.from(ctx.timers.keys());
            for (let i = 0; i < names.length; i++) ctx.clearTimer(names[i]);
        }

        function clearAllResources() {
            const names = Array.from(ctx.resources.keys());
            for (let i = 0; i < names.length; i++) ctx.clearResource(names[i]);
        }

        function runCleanups() {
            const fns = Array.from(ctx.cleanupCallbacks);
            ctx.cleanupCallbacks.clear();
            for (let i = 0; i < fns.length; i++) safeCall(fns[i], 'callback');
        }

        function abortRoute() {
            if (ctx.abortController && typeof ctx.abortController.abort === 'function') {
                try {
                    ctx.abortController.abort();
                } catch (_e) {}
            }
            ctx.abortController = null;
        }

        function teardownRuntime() {
            abortRoute();
            clearAllTimers();
            clearAllResources();
            runCleanups();
            ctx.entity = null;
            ctx.routeSidebar = {};
            resetEditUi(ctx.ui);
        }

        ctx.beginRoute = function (route) {
            if (ctx.destroyed) return ctx.generation;
            teardownRuntime();
            ctx.generation += 1;
            ctx.abortController = createAbort();
            ctx.route = route || null;
            ctx.lastResolvedRoute = null;
            return ctx.generation;
        };

        ctx.mount = function (host) {
            if (ctx.destroyed) return ctx;
            if (ctx.mounted && ctx.root && ctx.host === host) return ctx;
            if (ctx.mounted) ctx.unmount('remount');
            ctx.host = host || null;
            ctx.root = createRootElement(id);
            if (host && typeof host.appendChild === 'function') {
                host.appendChild(ctx.root);
            }
            ctx.mounted = true;
            if (!ctx.abortController) ctx.abortController = createAbort();
            return ctx;
        };

        ctx.unmount = function (_reason) {
            if (ctx.destroyed || !ctx.mounted) {
                teardownRuntime();
                ctx.mounted = false;
                ctx.root = null;
                ctx.host = null;
                return ctx;
            }
            teardownRuntime();
            if (ctx.root && ctx.root.parentNode && typeof ctx.root.parentNode.removeChild === 'function') {
                try {
                    ctx.root.parentNode.removeChild(ctx.root);
                } catch (_e) {}
            } else if (ctx.host && ctx.root && typeof ctx.host.removeChild === 'function') {
                try {
                    ctx.host.removeChild(ctx.root);
                } catch (_e) {}
            } else if (ctx.host) {
                ctx.host.innerHTML = '';
            }
            ctx.root = null;
            ctx.host = null;
            ctx.mounted = false;
            return ctx;
        };

        ctx.destroy = function () {
            if (ctx.destroyed) return ctx;
            ctx.unmount('destroy');
            ctx.navigation.routeStates.clear();
            ctx.navigation.origins.clear();
            ctx.destroyed = true;
            return ctx;
        };

        ctx.debugSnapshot = function () {
            return {
                tabId: id,
                mounted: !!ctx.mounted,
                generation: ctx.generation,
                hasAbortController: !!ctx.abortController,
                resourceNames: Array.from(ctx.resources.keys()).sort(),
                timerCount: ctx.timers.size,
                cleanupCount: ctx.cleanupCallbacks.size,
            };
        };

        return ctx;
    }

    function prksEnsureTabContext(tabId) {
        const id = String(tabId);
        let ctx = contexts.get(id);
        if (ctx && !ctx.destroyed) return ctx;
        ctx = createPrksTabContext(id);
        contexts.set(id, ctx);
        return ctx;
    }

    function prksGetTabContext(tabId) {
        if (tabId == null) return null;
        const ctx = contexts.get(String(tabId));
        if (!ctx || ctx.destroyed) return null;
        return ctx;
    }

    function workspaceSnap() {
        if (typeof root.prksWorkspaceSnapshot === 'function') return root.prksWorkspaceSnapshot();
        return null;
    }

    function prksGetMainTabContext() {
        const snap = workspaceSnap();
        if (snap && snap.mainTabId) return prksGetTabContext(snap.mainTabId);
        const mounted = [];
        contexts.forEach(function (ctx) {
            if (ctx.mounted && !ctx.destroyed) mounted.push(ctx);
        });
        return mounted.length === 1 ? mounted[0] : null;
    }

    function prksGetFocusedTabContext() {
        const snap = workspaceSnap();
        if (snap && snap.focusedTabId) {
            const focused = prksGetTabContext(snap.focusedTabId);
            if (focused) return focused;
        }
        return prksGetMainTabContext();
    }

    function prksIsMainTabContext(ctx) {
        if (!ctx) return false;
        const snap = workspaceSnap();
        if (snap && snap.mainTabId) return snap.mainTabId === ctx.tabId;
        return !!ctx.mounted;
    }

    function prksContextFromElement(element) {
        if (!element || typeof element.closest !== 'function') return null;
        const rootEl = element.closest('.prks-tab-root[data-prks-tab-id]');
        if (rootEl) {
            const tabId = rootEl.getAttribute('data-prks-tab-id');
            const fromRoot = prksGetTabContext(tabId);
            if (fromRoot) return fromRoot;
        }
        const tile = element.closest('.prks-tile[data-prks-tab-id]');
        if (!tile) return null;
        return prksGetTabContext(tile.getAttribute('data-prks-tab-id'));
    }

    function prksMountTabContext(tabId, host) {
        const ctx = prksEnsureTabContext(tabId);
        return ctx.mount(host);
    }

    function prksUnmountTabContext(tabId, reason) {
        const ctx = prksGetTabContext(tabId);
        if (!ctx) return null;
        ctx.unmount(reason);
        return ctx;
    }

    function prksDestroyTabContext(tabId) {
        const ctx = prksGetTabContext(tabId);
        if (!ctx) {
            contexts.delete(String(tabId));
            return null;
        }
        ctx.destroy();
        contexts.delete(String(tabId));
        return ctx;
    }

    function prksDestroyAllTabContexts() {
        const ids = Array.from(contexts.keys());
        for (let i = 0; i < ids.length; i++) prksDestroyTabContext(ids[i]);
    }

    function prksForEachMountedTabContext(fn) {
        if (typeof fn !== 'function') return;
        contexts.forEach(function (ctx) {
            if (ctx.mounted && !ctx.destroyed) fn(ctx);
        });
    }

    function prksTabContextHost() {
        if (typeof document === 'undefined') return null;
        return document.getElementById('page-content');
    }

    function prksTabContextDebugSnapshot() {
        const list = [];
        let mountedCount = 0;
        contexts.forEach(function (ctx) {
            if (ctx.destroyed) return;
            const row = ctx.debugSnapshot();
            if (row.mounted) mountedCount += 1;
            list.push(row);
        });
        list.sort(function (a, b) {
            return String(a.tabId).localeCompare(String(b.tabId));
        });
        return { mountedCount: mountedCount, contexts: list };
    }

    function prksFocusedRouteRecord() {
        const ctx = prksGetFocusedTabContext();
        if (!ctx) return null;
        return ctx.lastResolvedRoute || ctx.route || null;
    }

    function prksFocusedEntity(type) {
        const ctx = prksGetFocusedTabContext();
        return ctx ? ctx.getEntity(type) : null;
    }

    function prksOwnerTabContext(element) {
        if (element && typeof element.closest === 'function') {
            const fromEl = prksContextFromElement(element);
            if (fromEl) return fromEl;
        }
        return prksGetFocusedTabContext();
    }

    function prksTabContextIsFocused(ctx) {
        if (!ctx || ctx.destroyed) return false;
        const focused = prksGetFocusedTabContext();
        return !!(focused && focused.tabId === ctx.tabId);
    }

    function prksSetFocusedEntity(type, value) {
        const ctx = prksGetFocusedTabContext();
        if (ctx) ctx.setEntity(type, value);
        return value;
    }

    function prksApplyOwnedWorkEntity(ownerCtx, expectedWorkId, fresh) {
        if (!ownerCtx || ownerCtx.destroyed) return false;
        const want = expectedWorkId != null ? String(expectedWorkId) : '';
        if (!want) return false;
        const live = typeof ownerCtx.getEntity === 'function' ? ownerCtx.getEntity('work') : null;
        if (!live || String(live.id) !== want) return false;
        if (fresh && typeof ownerCtx.setEntity === 'function') ownerCtx.setEntity('work', fresh);
        return true;
    }

    function prksTabContextOwnsEntityRoute(ctx, generation, entityType, expectedId, routeNames) {
        if (!ctx || typeof ctx.isCurrent !== 'function' || !ctx.isCurrent(generation)) return false;
        const live = typeof ctx.getEntity === 'function' ? ctx.getEntity(entityType) : null;
        if (!live || String(live.id) !== String(expectedId)) return false;
        const route = ctx.lastResolvedRoute || ctx.route;
        const names = Array.isArray(routeNames) ? routeNames : [routeNames];
        return !!(route && names.indexOf(route.name) >= 0);
    }

    function prksFocusedResource(name) {
        const ctx = prksGetFocusedTabContext();
        return ctx ? ctx.getResource(String(name)) : undefined;
    }

    function prksSetFocusedResource(name, value, disposer) {
        const ctx = prksGetFocusedTabContext();
        if (ctx) return ctx.setResource(String(name), value, disposer);
        return value;
    }

    function prksClearFocusedResource(name) {
        const ctx = prksGetFocusedTabContext();
        if (ctx) ctx.clearResource(String(name));
    }

    function prksFocusedTimer(name, timerId) {
        const ctx = prksGetFocusedTabContext();
        if (ctx) return ctx.setTimer(String(name), timerId);
        return timerId;
    }

    function prksClearFocusedTimer(name) {
        const ctx = prksGetFocusedTabContext();
        if (ctx) ctx.clearTimer(String(name));
    }

    function prksFocusedRouteSidebar() {
        const ctx = prksGetFocusedTabContext();
        return ctx ? ctx.routeSidebar : {};
    }

    function prksFocusedRouteGeneration() {
        const ctx = prksGetFocusedTabContext();
        return ctx ? ctx.generation : 0;
    }

    function prksFocusedRouteIsCurrent(routeGen) {
        const ctx = prksGetFocusedTabContext();
        if (ctx && typeof ctx.isCurrent === 'function') return ctx.isCurrent(routeGen);
        return typeof routeGen !== 'number';
    }

    const api = {
        createPrksTabContext: createPrksTabContext,
        prksEnsureTabContext: prksEnsureTabContext,
        prksGetTabContext: prksGetTabContext,
        prksGetMainTabContext: prksGetMainTabContext,
        prksGetFocusedTabContext: prksGetFocusedTabContext,
        prksIsMainTabContext: prksIsMainTabContext,
        prksContextFromElement: prksContextFromElement,
        prksMountTabContext: prksMountTabContext,
        prksUnmountTabContext: prksUnmountTabContext,
        prksDestroyTabContext: prksDestroyTabContext,
        prksDestroyAllTabContexts: prksDestroyAllTabContexts,
        prksForEachMountedTabContext: prksForEachMountedTabContext,
        prksTabContextHost: prksTabContextHost,
        prksTabContextDebugSnapshot: prksTabContextDebugSnapshot,
        prksFocusedEntity: prksFocusedEntity,
        prksFocusedRouteRecord: prksFocusedRouteRecord,
        prksOwnerTabContext: prksOwnerTabContext,
        prksTabContextIsFocused: prksTabContextIsFocused,
        prksSetFocusedEntity: prksSetFocusedEntity,
        prksApplyOwnedWorkEntity: prksApplyOwnedWorkEntity,
        prksTabContextOwnsEntityRoute: prksTabContextOwnsEntityRoute,
        prksFocusedRouteGeneration: prksFocusedRouteGeneration,
        prksFocusedRouteIsCurrent: prksFocusedRouteIsCurrent,
        prksFocusedResource: prksFocusedResource,
        prksSetFocusedResource: prksSetFocusedResource,
        prksClearFocusedResource: prksClearFocusedResource,
        prksFocusedTimer: prksFocusedTimer,
        prksClearFocusedTimer: prksClearFocusedTimer,
        prksFocusedRouteSidebar: prksFocusedRouteSidebar,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
