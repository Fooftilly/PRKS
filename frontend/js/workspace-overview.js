/**
 * Workspace Overview dialog — read-only view of prksWorkspaceSnapshot().
 * No persistence, no remount, no tree rewrite.
 *
 * Modal dialog: initial focus, Tab containment, Escape closes, restore opener.
 * Visible panes follow prksWorkspaceVisualTiled() — not every secondaryTree leaf.
 */
(function (root) {
    'use strict';

    const POPOVER_ID = 'prks-workspace-overview';
    const BTN_ID = 'prks-workspace-overview-btn';
    const FOCUSABLE =
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

    let openerEl = null;
    let previouslyFocused = null;

    function esc(s) {
        if (typeof root.prksEscapeHtml === 'function') return root.prksEscapeHtml(s);
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    /**
     * Visible pane tab IDs from canonical visual-tiled state.
     * Hide-split and narrow fallback preserve secondaryTree but are not tiled.
     */
    function collectVisibleTabIds(snap) {
        const ids = [];
        if (!snap || !snap.mainTabId) return ids;
        ids.push(String(snap.mainTabId));

        const visual =
            typeof root.prksWorkspaceVisualTiled === 'function'
                ? !!root.prksWorkspaceVisualTiled()
                : snap.mode === 'tiled' &&
                  !!snap.secondaryTree &&
                  !(
                      typeof root.prksWorkspaceIsNarrowFallback === 'function' &&
                      root.prksWorkspaceIsNarrowFallback()
                  );

        if (!visual || !snap.secondaryTree) return ids;

        if (typeof root.collectLeafTabIds === 'function') {
            const leaves = root.collectLeafTabIds(snap.secondaryTree) || [];
            for (let i = 0; i < leaves.length; i += 1) ids.push(String(leaves[i]));
        } else if (snap.secondaryTree.type === 'leaf' && snap.secondaryTree.tabId) {
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
        const visual =
            typeof root.prksWorkspaceVisualTiled === 'function'
                ? !!root.prksWorkspaceVisualTiled()
                : snap.mode === 'tiled';
        const mode = visual ? 'Split view' : 'Stacked';
        const visibleIds = collectVisibleTabIds(snap);
        const visibleSet = {};
        visibleIds.forEach(function (id) {
            visibleSet[id] = true;
        });
        const parked = (snap.tabs || []).filter(function (t) {
            return t && !visibleSet[String(t.id)];
        });
        const maxVisible = 4;
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

    function focusableNodes(el) {
        if (!el || !el.querySelectorAll) return [];
        return Array.prototype.slice.call(el.querySelectorAll(FOCUSABLE)).filter(function (n) {
            return !!(n.offsetWidth || n.offsetHeight || n.getClientRects().length);
        });
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
        el.setAttribute('aria-modal', 'true');
        el.setAttribute('aria-label', 'Workspace overview');
        el.innerHTML =
            '<div class="prks-workspace-overview__panel" data-prks-overview-panel tabindex="-1">' +
            '<div class="prks-workspace-overview__head">' +
            '<h2 class="prks-workspace-overview__title-head" id="prks-workspace-overview-title">Workspace overview</h2>' +
            '<button type="button" class="prks-icon-btn prks-icon-btn--ghost" data-prks-overview-close aria-label="Close overview">' +
            (typeof root.prksIcon === 'function'
                ? root.prksIcon('x', { size: 'sm' })
                : '×') +
            '</button>' +
            '</div>' +
            '<div class="prks-workspace-overview__body" data-prks-overview-body></div>' +
            '</div>';
        el.setAttribute('aria-labelledby', 'prks-workspace-overview-title');
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

    function trapKeydown(e) {
        const el = root.document && root.document.getElementById(POPOVER_ID);
        if (!el || el.hidden) return;
        if (e.key === 'Escape') {
            e.preventDefault();
            e.stopPropagation();
            closeOverview();
            return;
        }
        if (e.key !== 'Tab') return;
        const nodes = focusableNodes(el);
        if (!nodes.length) {
            e.preventDefault();
            const panel = el.querySelector('[data-prks-overview-panel]');
            if (panel) panel.focus();
            return;
        }
        const first = nodes[0];
        const last = nodes[nodes.length - 1];
        const active = root.document.activeElement;
        if (e.shiftKey) {
            if (active === first || !el.contains(active)) {
                e.preventDefault();
                last.focus();
            }
        } else if (active === last || !el.contains(active)) {
            e.preventDefault();
            first.focus();
        }
    }

    function openOverview(fromEl) {
        const el = ensurePopover();
        const btn = root.document && root.document.getElementById(BTN_ID);
        if (!el) return;
        /* Restore preference: explicit opener → current activeElement → toolbar.
         * Command-palette context actions restore palette focus then open
         * Overview; preferring the toolbar button would steal that restore. */
        const explicit = fromEl && typeof fromEl.focus === 'function' ? fromEl : null;
        const active = root.document && root.document.activeElement;
        const activeOk =
            active &&
            typeof active.focus === 'function' &&
            active !== root.document.body &&
            !(el.contains && el.contains(active))
                ? active
                : null;
        const toolbar = btn && typeof btn.focus === 'function' ? btn : null;
        previouslyFocused = explicit || activeOk || toolbar || null;
        openerEl = previouslyFocused;
        refreshBody();
        el.hidden = false;
        if (btn) btn.setAttribute('aria-expanded', 'true');
        root.document.addEventListener('keydown', trapKeydown, true);
        root.requestAnimationFrame(function () {
            const closeBtn = el.querySelector('[data-prks-overview-close]');
            const firstRow = el.querySelector('[data-prks-overview-tab]');
            const target = closeBtn || firstRow || el.querySelector('[data-prks-overview-panel]');
            if (target && target.focus) target.focus();
        });
    }

    function closeOverview() {
        const el = root.document && root.document.getElementById(POPOVER_ID);
        const btn = root.document && root.document.getElementById(BTN_ID);
        if (el) el.hidden = true;
        if (btn) btn.setAttribute('aria-expanded', 'false');
        root.document.removeEventListener('keydown', trapKeydown, true);
        const restore = previouslyFocused || openerEl || btn;
        previouslyFocused = null;
        openerEl = null;
        if (restore && typeof restore.focus === 'function') {
            try {
                restore.focus();
            } catch (_e) {
                /* ignore */
            }
        }
    }

    function toggleOverview(fromEl) {
        const el = root.document && root.document.getElementById(POPOVER_ID);
        if (el && !el.hidden) closeOverview();
        else openOverview(fromEl);
    }

    function bindChrome() {
        const d = root.document;
        if (!d) return;
        const btn = d.getElementById(BTN_ID);
        if (btn && btn.dataset.bound !== '1') {
            btn.dataset.bound = '1';
            btn.setAttribute('aria-haspopup', 'dialog');
            btn.setAttribute('aria-controls', POPOVER_ID);
            if (!btn.hasAttribute('aria-expanded')) btn.setAttribute('aria-expanded', 'false');
            btn.addEventListener('click', function (e) {
                e.preventDefault();
                toggleOverview(btn);
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
