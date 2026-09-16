/**
 * Workspace Overview popover — read-only view of prksWorkspaceSnapshot().
 * No persistence, no remount, no tree rewrite.
 */
(function (root) {
    'use strict';

    const POPOVER_ID = 'prks-workspace-overview';
    const BTN_ID = 'prks-workspace-overview-btn';

    function esc(s) {
        if (typeof root.prksEscapeHtml === 'function') return root.prksEscapeHtml(s);
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function collectVisibleTabIds(snap) {
        const ids = [];
        if (!snap) return ids;
        if (snap.mainTabId) ids.push(String(snap.mainTabId));
        if (typeof root.collectLeafTabIds === 'function' && snap.secondaryTree) {
            const leaves = root.collectLeafTabIds(snap.secondaryTree) || [];
            for (let i = 0; i < leaves.length; i += 1) ids.push(String(leaves[i]));
        } else if (snap.secondaryTree && snap.secondaryTree.type === 'leaf' && snap.secondaryTree.tabId) {
            ids.push(String(snap.secondaryTree.tabId));
        }
        return ids;
    }

    function tabById(snap, id) {
        const tabs = (snap && snap.tabs) || [];
        for (let i = 0; i < tabs.length; i += 1) {
            if (String(tabs[i].id) === String(id)) return tabs[i];
        }
        return null;
    }

    function buildBodyHtml(snap) {
        if (!snap) {
            return '<p class="meta-row">Workspace not ready.</p>';
        }
        const mode = snap.mode === 'tiled' ? 'Split view' : 'Stacked';
        const visibleIds = collectVisibleTabIds(snap);
        const visibleSet = {};
        visibleIds.forEach(function (id) {
            visibleSet[id] = true;
        });
        const parked = (snap.tabs || []).filter(function (t) {
            return t && !visibleSet[String(t.id)];
        });
        const maxVisible =
            typeof root.PRKS_MAX_VISIBLE_TABS === 'number' ? root.PRKS_MAX_VISIBLE_TABS : 4;
        const remaining = Math.max(0, maxVisible - visibleIds.length);

        let rows = '';
        function row(tab, role) {
            if (!tab) return '';
            const title = String(tab.title || tab.route || 'Untitled').trim() || 'Untitled';
            const hash = String(tab.route || tab.hash || '').trim();
            const focused = String(snap.focusedTabId) === String(tab.id);
            const cls =
                'prks-workspace-overview__row' +
                (focused ? ' is-focused' : '') +
                (role === 'main' ? ' is-main' : '');
            return (
                '<button type="button" class="' +
                cls +
                '" data-prks-overview-tab="' +
                esc(String(tab.id)) +
                '">' +
                '<span class="prks-workspace-overview__role">' +
                esc(role) +
                '</span>' +
                '<span class="prks-workspace-overview__title">' +
                esc(title) +
                '</span>' +
                (hash
                    ? '<span class="prks-workspace-overview__route meta-row">' + esc(hash) + '</span>'
                    : '') +
                '</button>'
            );
        }

        const main = tabById(snap, snap.mainTabId);
        rows += row(main, 'Main');
        visibleIds.forEach(function (id) {
            if (String(id) === String(snap.mainTabId)) return;
            rows += row(tabById(snap, id), 'Split');
        });

        let parkedHtml = '';
        if (parked.length) {
            parkedHtml =
                '<div class="prks-workspace-overview__section">' +
                '<p class="prks-workspace-overview__section-label">Parked · ' +
                parked.length +
                '</p>' +
                parked
                    .map(function (t) {
                        return row(t, 'Parked');
                    })
                    .join('') +
                '</div>';
        }

        const summary =
            typeof root.prksPageSummaryHtml === 'function'
                ? root.prksPageSummaryHtml({
                      parts: [
                          mode,
                          visibleIds.length +
                              (visibleIds.length === 1 ? ' visible pane' : ' visible panes'),
                          remaining
                              ? remaining + ' of ' + maxVisible + ' remaining'
                              : 'pane cap reached',
                          (snap.tabs || []).length +
                              ((snap.tabs || []).length === 1 ? ' tab' : ' tabs'),
                      ],
                  })
                : '';

        return (
            summary +
            '<div class="prks-workspace-overview__section">' +
            '<p class="prks-workspace-overview__section-label">Open</p>' +
            rows +
            '</div>' +
            parkedHtml
        );
    }

    function ensurePopover() {
        const d = root.document;
        if (!d || !d.body) return null;
        let el = d.getElementById(POPOVER_ID);
        if (el) return el;
        el = d.createElement('div');
        el.id = POPOVER_ID;
        el.className = 'prks-workspace-overview';
        el.hidden = true;
        el.setAttribute('role', 'dialog');
        el.setAttribute('aria-label', 'Workspace overview');
        el.innerHTML =
            '<div class="prks-workspace-overview__panel">' +
            '<div class="prks-workspace-overview__head">' +
            '<h2 class="prks-workspace-overview__title-head">Workspace overview</h2>' +
            '<button type="button" class="prks-icon-btn prks-icon-btn--ghost" data-prks-overview-close aria-label="Close overview">' +
            (typeof root.prksIcon === 'function'
                ? root.prksIcon('x', { size: 'sm' })
                : '×') +
            '</button>' +
            '</div>' +
            '<div class="prks-workspace-overview__body" data-prks-overview-body></div>' +
            '</div>';
        d.body.appendChild(el);
        el.addEventListener('click', function (e) {
            const t = e.target;
            if (!t || !t.closest) return;
            if (t.closest('[data-prks-overview-close]') || t === el) {
                closeOverview();
                return;
            }
            const btn = t.closest('[data-prks-overview-tab]');
            if (!btn) return;
            const tabId = btn.getAttribute('data-prks-overview-tab');
            closeOverview();
            if (tabId && typeof root.prksWorkspaceActivateTab === 'function') {
                void root.prksWorkspaceActivateTab(tabId);
            }
        });
        return el;
    }

    function refreshBody() {
        const el = ensurePopover();
        if (!el) return;
        const body = el.querySelector('[data-prks-overview-body]');
        if (!body) return;
        const snap =
            typeof root.prksWorkspaceSnapshot === 'function' ? root.prksWorkspaceSnapshot() : null;
        body.innerHTML = buildBodyHtml(snap);
        if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(el);
    }

    function openOverview() {
        const el = ensurePopover();
        const btn = root.document && root.document.getElementById(BTN_ID);
        if (!el) return;
        refreshBody();
        el.hidden = false;
        if (btn) {
            btn.setAttribute('aria-expanded', 'true');
        }
    }

    function closeOverview() {
        const el = root.document && root.document.getElementById(POPOVER_ID);
        const btn = root.document && root.document.getElementById(BTN_ID);
        if (el) el.hidden = true;
        if (btn) btn.setAttribute('aria-expanded', 'false');
    }

    function toggleOverview() {
        const el = root.document && root.document.getElementById(POPOVER_ID);
        if (el && !el.hidden) closeOverview();
        else openOverview();
    }

    function bindChrome() {
        const d = root.document;
        if (!d) return;
        const btn = d.getElementById(BTN_ID);
        if (btn && btn.dataset.bound !== '1') {
            btn.dataset.bound = '1';
            btn.addEventListener('click', function (e) {
                e.preventDefault();
                toggleOverview();
            });
        }
        if (d.documentElement.dataset.prksOverviewEsc !== '1') {
            d.documentElement.dataset.prksOverviewEsc = '1';
            d.addEventListener('keydown', function (e) {
                if (e.key === 'Escape') closeOverview();
            });
        }
    }

    root.prksWorkspaceOverviewOpen = openOverview;
    root.prksWorkspaceOverviewClose = closeOverview;
    root.prksWorkspaceOverviewToggle = toggleOverview;
    root.prksWorkspaceOverviewRefresh = refreshBody;
    root.prksBindWorkspaceOverviewChrome = bindChrome;

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = {
            buildBodyHtml: buildBodyHtml,
            collectVisibleTabIds: collectVisibleTabIds,
        };
    }
})(typeof window !== 'undefined' ? window : globalThis);
