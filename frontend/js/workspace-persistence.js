/**
 * Workspace logical-state persistence. Serializes the existing workspace state
 * machine to localStorage; it is not itself part of that state machine.
 *
 * v1 is last-writer-wins on this browser profile. No storage-event sync,
 * BroadcastChannel, or server-side workspace copy.
 *
 * Other workspace modules must not call localStorage themselves.
 */
(function (root) {
    'use strict';

    const SCHEMA_VERSION = 1;
    const STORAGE_KEY = 'prks.workspace.v1';
    const DEBOUNCE_MS = 200;
    const MAX_SECONDARY_LEAVES = 3;
    const MAX_TITLE = 200;
    const MAX_ROUTE = 2048;
    const TAB_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
    const ICON_RE = /^[a-z0-9-]{1,40}$/i;
    const AXES = { 'left-right': true, 'top-bottom': true };
    const FORBIDDEN_SERIALIZED_KEYS = {
        generation: true,
        mounted: true,
        controller: true,
        runtime: true,
        narrowFallback: true,
        effectiveRatio: true,
        history: true,
        historyIndex: true,
        titleRouteGen: true,
        focusedTabId: true,
    };

    let persistDisabled = false;
    let pendingState = null;
    let debounceTimer = null;
    let lastWritten = '';
    let pagehideBound = false;

    function warnPersist(code) {
        try {
            if (typeof console !== 'undefined' && typeof console.warn === 'function') {
                console.warn('PRKS workspace persistence: ' + code);
            }
        } catch (_e) {}
    }

    function getStorage() {
        try {
            const ls = root.localStorage;
            if (ls && typeof ls.getItem === 'function' && typeof ls.setItem === 'function') return ls;
        } catch (_e) {
            return null;
        }
        return null;
    }

    function isFiniteRatio(value) {
        return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1;
    }

    function clampRatio(value, fallback) {
        const n = Number(value);
        if (!Number.isFinite(n)) return fallback;
        if (n < 0) return 0;
        if (n > 1) return 1;
        return n;
    }

    function sanitizeTitle(value) {
        if (typeof value !== 'string') return '';
        let text = value.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '');
        if (text.length > MAX_TITLE) text = text.slice(0, MAX_TITLE);
        return text;
    }

    function sanitizeIcon(value) {
        if (typeof value !== 'string') return '';
        return ICON_RE.test(value) ? value : '';
    }

    function generatedTabSeq(id) {
        if (typeof id !== 'string' || !/^tab-\d+$/.test(id)) return undefined;
        const match = /^tab-([1-9]\d*)$/.exec(id);
        if (!match) return null;
        const n = Number(match[1]);
        if (!Number.isSafeInteger(n) || n < 1) return null;
        return n;
    }

    function isTabId(value) {
        if (typeof value !== 'string' || !TAB_ID_RE.test(value)) return false;
        return generatedTabSeq(value) !== null;
    }

    function isRouteString(value) {
        if (typeof value !== 'string') return false;
        if (value.length < 3 || value.length > MAX_ROUTE) return false;
        if (value.charAt(0) !== '#' || value.charAt(1) !== '/') return false;
        if (/[\s\u0000-\u001F]/.test(value)) return false;
        return true;
    }

    function routeName(hash) {
        if (typeof root.prksParseRoute !== 'function') return null;
        try {
            const parsed = root.prksParseRoute(hash);
            return parsed && parsed.name ? parsed.name : null;
        } catch (_e) {
            return null;
        }
    }

    function routeIsKnown(hash) {
        const name = routeName(hash);
        if (name == null) return true;
        return name !== 'unknown';
    }

    function routeSupportsTile(hash) {
        if (typeof root.prksRouteSupportsTile === 'function') {
            try {
                return !!root.prksRouteSupportsTile(hash);
            } catch (_e) {
                return false;
            }
        }
        return true;
    }

    function serializeTree(node) {
        if (!node) return null;
        if (node.type === 'leaf') {
            if (!node.tabId) return null;
            return { type: 'leaf', tabId: String(node.tabId) };
        }
        if (node.type === 'split') {
            const first = serializeTree(node.first);
            const second = serializeTree(node.second);
            if (!first || !second) return null;
            return {
                type: 'split',
                axis: node.axis === 'top-bottom' ? 'top-bottom' : 'left-right',
                ratio: clampRatio(node.ratio, 0.5),
                first: first,
                second: second,
            };
        }
        return null;
    }

    function serializeTab(tab) {
        if (!tab || !isTabId(tab.id) || !isRouteString(tab.route)) return null;
        const out = {
            id: String(tab.id),
            route: String(tab.route),
        };
        const title = sanitizeTitle(tab.title);
        const icon = sanitizeIcon(tab.icon);
        if (title) out.title = title;
        if (icon) out.icon = icon;
        return out;
    }

    function prksSerializeWorkspaceSnapshot(state) {
        if (!state || !Array.isArray(state.tabs) || !state.tabs.length || !state.mainTabId) return null;
        const tabs = [];
        for (let i = 0; i < state.tabs.length; i++) {
            const tab = serializeTab(state.tabs[i]);
            if (!tab) return null;
            tabs.push(tab);
        }
        const snapshot = {
            version: SCHEMA_VERSION,
            tabs: tabs,
            mainTabId: String(state.mainTabId),
            secondaryTree: serializeTree(state.secondaryTree),
            mode: state.mode === 'tiled' ? 'tiled' : 'stacked',
            mainSplitRatio: clampRatio(state.mainSplitRatio, 0.58),
        };
        return snapshot;
    }

    function validateStoredTree(node, ctx, path) {
        if (node == null) return;
        if (typeof node !== 'object' || Array.isArray(node)) {
            ctx.errors.push('malformed tree at ' + path);
            return;
        }
        if (node.type === 'leaf') {
            if (!isTabId(node.tabId)) {
                ctx.errors.push('invalid leaf tabId at ' + path);
                return;
            }
            if (ctx.seenLeaves[node.tabId]) {
                ctx.errors.push('duplicate tree leaf ' + node.tabId);
                return;
            }
            ctx.seenLeaves[node.tabId] = true;
            ctx.leafCount += 1;
            return;
        }
        if (node.type === 'split') {
            if (!AXES[node.axis]) ctx.errors.push('invalid axis at ' + path);
            if (!isFiniteRatio(node.ratio)) ctx.errors.push('invalid ratio at ' + path);
            if (!node.first || !node.second) {
                ctx.errors.push('split missing child at ' + path);
                return;
            }
            validateStoredTree(node.first, ctx, path + '.first');
            validateStoredTree(node.second, ctx, path + '.second');
            return;
        }
        ctx.errors.push('unknown tree node at ' + path);
    }

    function snapshotHasForbiddenKeys(value) {
        if (!value || typeof value !== 'object') return false;
        if (Array.isArray(value)) {
            for (let i = 0; i < value.length; i++) {
                if (snapshotHasForbiddenKeys(value[i])) return true;
            }
            return false;
        }
        const keys = Object.keys(value);
        for (let i = 0; i < keys.length; i++) {
            if (FORBIDDEN_SERIALIZED_KEYS[keys[i]]) return true;
            if (snapshotHasForbiddenKeys(value[keys[i]])) return true;
        }
        return false;
    }

    function prksValidateWorkspaceSnapshot(raw) {
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
        if (raw.version !== SCHEMA_VERSION) return null;
        if (!Array.isArray(raw.tabs) || !raw.tabs.length) return null;
        if (!isTabId(raw.mainTabId)) return null;
        if (raw.mode !== 'stacked' && raw.mode !== 'tiled') return null;
        if (!isFiniteRatio(raw.mainSplitRatio)) return null;
        if (snapshotHasForbiddenKeys(raw)) return null;

        const byId = Object.create(null);
        const tabs = [];
        for (let i = 0; i < raw.tabs.length; i++) {
            const tab = raw.tabs[i];
            if (!tab || typeof tab !== 'object' || Array.isArray(tab)) return null;
            if (!isTabId(tab.id) || byId[tab.id]) return null;
            if (!isRouteString(tab.route) || !routeIsKnown(tab.route)) return null;
            byId[tab.id] = {
                id: tab.id,
                route: tab.route,
                title: sanitizeTitle(tab.title),
                icon: sanitizeIcon(tab.icon),
            };
            tabs.push(byId[tab.id]);
        }
        if (!byId[raw.mainTabId]) return null;

        const treeCtx = { errors: [], seenLeaves: Object.create(null), leafCount: 0 };
        if (raw.secondaryTree != null) {
            validateStoredTree(raw.secondaryTree, treeCtx, 'root');
            if (treeCtx.errors.length) return null;
            if (treeCtx.leafCount < 1) return null;
            if (treeCtx.leafCount > MAX_SECONDARY_LEAVES) return null;
            if (treeCtx.seenLeaves[raw.mainTabId]) return null;
            const leafIds = Object.keys(treeCtx.seenLeaves);
            for (let i = 0; i < leafIds.length; i++) {
                const id = leafIds[i];
                const tab = byId[id];
                if (!tab) return null;
                if (!routeSupportsTile(tab.route)) return null;
            }
        } else if (raw.mode === 'tiled') {
            return null;
        }

        return {
            version: SCHEMA_VERSION,
            tabs: tabs,
            mainTabId: raw.mainTabId,
            secondaryTree: raw.secondaryTree == null ? null : raw.secondaryTree,
            mode: raw.mode,
            mainSplitRatio: raw.mainSplitRatio,
        };
    }

    function prksRehydrateWorkspaceTree(node) {
        if (node == null) return null;
        if (node.type === 'leaf') {
            if (typeof root.makeLeaf === 'function') return root.makeLeaf(node.tabId);
            return { type: 'leaf', tabId: String(node.tabId) };
        }
        if (node.type !== 'split') return null;
        const first = prksRehydrateWorkspaceTree(node.first);
        const second = prksRehydrateWorkspaceTree(node.second);
        if (!first || !second) return null;
        if (typeof root.makeSplit === 'function') {
            return root.makeSplit(node.axis, first, second, node.ratio);
        }
        return {
            type: 'split',
            id: 'split-restored',
            axis: node.axis === 'top-bottom' ? 'top-bottom' : 'left-right',
            ratio: clampRatio(node.ratio, 0.5),
            first: first,
            second: second,
        };
    }

    function discardCorrupt(store) {
        if (!store || typeof store.removeItem !== 'function') return;
        try {
            store.removeItem(STORAGE_KEY);
        } catch (_e) {}
    }

    function prksLoadWorkspaceSnapshot() {
        if (persistDisabled) return null;
        const store = getStorage();
        if (!store) return null;
        let rawText = null;
        try {
            rawText = store.getItem(STORAGE_KEY);
        } catch (_e) {
            persistDisabled = true;
            warnPersist('unavailable');
            return null;
        }
        if (rawText == null || rawText === '') return null;
        let parsed = null;
        try {
            parsed = JSON.parse(rawText);
        } catch (_e) {
            discardCorrupt(store);
            warnPersist('invalid_json');
            return null;
        }
        const valid = prksValidateWorkspaceSnapshot(parsed);
        if (!valid) {
            discardCorrupt(store);
            warnPersist('rejected');
            return null;
        }
        return valid;
    }

    function writeSnapshot(state) {
        if (persistDisabled) return;
        const store = getStorage();
        if (!store) {
            persistDisabled = true;
            warnPersist('unavailable');
            return;
        }
        let snapshot = null;
        try {
            snapshot = prksSerializeWorkspaceSnapshot(state);
        } catch (_e) {
            warnPersist('serialize_failed');
            return;
        }
        if (!snapshot) return;
        const valid = prksValidateWorkspaceSnapshot(snapshot);
        if (!valid) return;
        let text = '';
        try {
            text = JSON.stringify(valid);
        } catch (_e) {
            warnPersist('serialize_failed');
            return;
        }
        if (!text || text === lastWritten) return;
        try {
            store.setItem(STORAGE_KEY, text);
            lastWritten = text;
        } catch (_e) {
            persistDisabled = true;
            warnPersist('write_failed');
        }
    }

    function clearTimer() {
        if (debounceTimer == null) return;
        try {
            if (typeof root.clearTimeout === 'function') root.clearTimeout(debounceTimer);
            else clearTimeout(debounceTimer);
        } catch (_e) {}
        debounceTimer = null;
    }

    function prksScheduleWorkspacePersistence(state) {
        if (persistDisabled) return;
        pendingState = state;
        clearTimer();
        const delay = DEBOUNCE_MS;
        try {
            const setter = typeof root.setTimeout === 'function' ? root.setTimeout : setTimeout;
            debounceTimer = setter(function () {
                debounceTimer = null;
                const next = pendingState;
                pendingState = null;
                if (next) writeSnapshot(next);
            }, delay);
        } catch (_e) {
            const next = pendingState;
            pendingState = null;
            if (next) writeSnapshot(next);
        }
    }

    function prksFlushWorkspacePersistence() {
        clearTimer();
        const next = pendingState;
        pendingState = null;
        if (next) writeSnapshot(next);
    }

    function prksClearWorkspaceSnapshot() {
        persistDisabled = false;
        pendingState = null;
        lastWritten = '';
        clearTimer();
        const store = getStorage();
        if (!store) return;
        try {
            store.removeItem(STORAGE_KEY);
        } catch (_e) {}
    }

    function bindPagehide() {
        if (pagehideBound) return;
        if (typeof root.addEventListener !== 'function') return;
        pagehideBound = true;
        root.addEventListener('pagehide', function () {
            prksFlushWorkspacePersistence();
        });
    }

    bindPagehide();

    const api = {
        prksSerializeWorkspaceSnapshot: prksSerializeWorkspaceSnapshot,
        prksValidateWorkspaceSnapshot: prksValidateWorkspaceSnapshot,
        prksRehydrateWorkspaceTree: prksRehydrateWorkspaceTree,
        prksLoadWorkspaceSnapshot: prksLoadWorkspaceSnapshot,
        prksScheduleWorkspacePersistence: prksScheduleWorkspacePersistence,
        prksFlushWorkspacePersistence: prksFlushWorkspacePersistence,
        prksClearWorkspaceSnapshot: prksClearWorkspaceSnapshot,
        PRKS_WORKSPACE_PERSISTENCE_KEY: STORAGE_KEY,
        PRKS_WORKSPACE_PERSISTENCE_DEBOUNCE_MS: DEBOUNCE_MS,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
