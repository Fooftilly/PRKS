/**
 * Stable workspace content hosts.
 *
 * One `.prks-tile__body` element per tab id. The Vue workspace shell frames
 * these nodes; it does not create or destroy route content. TabContext and
 * the route-surface own what is mounted inside the host. Focusing, resizing,
 * reordering, and ordinary shell rerenders reparent the same element.
 *
 * Parked tabs have no pane host. Close releases exactly that tab's host.
 * This module does not read or write workspace state.
 */
(function (root) {
    'use strict';

    const hosts = Object.create(null);

    function doc() {
        return typeof document !== 'undefined' ? document : null;
    }

    function parking() {
        const d = doc();
        if (!d) return null;
        let el = d.getElementById('prks-workspace-host-parking');
        if (el) return el;
        el = d.createElement('div');
        el.id = 'prks-workspace-host-parking';
        el.hidden = true;
        el.setAttribute('aria-hidden', 'true');
        const parent = d.body || d.documentElement;
        if (parent) parent.appendChild(el);
        return el;
    }

    function prksWorkspaceRetainContentHost(tabId) {
        if (!tabId) return null;
        const id = String(tabId);
        const existing = hosts[id];
        if (existing) return existing;
        const d = doc();
        if (!d) return null;
        const host = d.createElement('div');
        host.className = 'prks-tile__body';
        host.setAttribute('data-prks-content-host', id);
        hosts[id] = host;
        const park = parking();
        if (park) park.appendChild(host);
        return host;
    }

    function prksWorkspacePlaceContentHost(tabId, slot) {
        const host = prksWorkspaceRetainContentHost(tabId);
        if (!host || !slot) return host;
        if (host.parentNode === slot) return host;
        slot.appendChild(host);
        if (typeof root.prksNotifyTabHostReparent === 'function') {
            root.prksNotifyTabHostReparent(String(tabId));
        }
        return host;
    }

    function prksWorkspaceReleaseContentHost(tabId) {
        if (!tabId) return;
        const id = String(tabId);
        const host = hosts[id];
        if (!host) return;
        if (host.parentNode) host.parentNode.removeChild(host);
        delete hosts[id];
    }

    function prksWorkspaceContentHostIds() {
        return Object.keys(hosts);
    }

    /** Coordinator mount target. Same element the shell places into a pane frame. */
    function prksWorkspaceHostForTab(tabId) {
        return prksWorkspaceRetainContentHost(tabId);
    }

    const api = {
        prksWorkspaceRetainContentHost: prksWorkspaceRetainContentHost,
        prksWorkspacePlaceContentHost: prksWorkspacePlaceContentHost,
        prksWorkspaceReleaseContentHost: prksWorkspaceReleaseContentHost,
        prksWorkspaceContentHostIds: prksWorkspaceContentHostIds,
        prksWorkspaceHostForTab: prksWorkspaceHostForTab,
    };

    Object.keys(api).forEach(function (key) {
        root[key] = api[key];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
