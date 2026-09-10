/**
 * Lightweight Positions: Argument/Stance targets. No Debates yet.
 */
(function (root) {
    'use strict';

    function esc(s) {
        if (typeof root.prksEscapeHtml === 'function') return root.prksEscapeHtml(s);
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function md(text) {
        if (typeof root.prksResearchMarkdownHtml === 'function') return root.prksResearchMarkdownHtml(text);
        return '<p>' + esc(text || '') + '</p>';
    }

    function rowHtml(opts) {
        return typeof root.prksResearchIndexRowHtml === 'function'
            ? root.prksResearchIndexRowHtml(opts)
            : '<a class="prks-list-row prks-research-row" href="' + opts.href + '">' + opts.title + '</a>';
    }

    /** Tags an already-built research row anchor with a data-prks-role. */
    function rowHtmlWithRole(opts, role) {
        const html = rowHtml(opts);
        return html.replace('<a ', '<a data-prks-role="' + esc(role) + '" ');
    }

    function sectionHead(title, opts) {
        if (typeof root.prksResearchSectionHeadHtml === 'function') return root.prksResearchSectionHeadHtml(title, opts);
        return '<h3>' + esc(title) + '</h3>';
    }

    /* --- Offline policy for Position routes (AGENTS.md "Offline / PWA") ------
     * Positions are read-only offline in Phase 1: a cached index/detail
     * renders, every canonical mutation is blocked outright (never queued,
     * never faked), and the one destination that is not cached at all -- the
     * Research Graph -- says so instead of navigating somewhere broken.
     * Controls carry these roles so one helper can settle them all, including
     * markup rerendered after the initial bind. */
    const POSITION_MUTATION_ROLE = 'position-mutation-control';
    const POSITION_ONLINE_ONLY_ROLE = 'position-online-only-control';
    /* Argument/Stance rows keep this role for styling and test identification
     * only. Since Arguments became offline-capable the Argument route owns its
     * own availability, so this role is deliberately absent from
     * POSITION_CONTROL_SELECTOR and carries no offline policy of its own. */
    const POSITION_ARGUMENT_LINK_ROLE = 'position-argument-link';
    const POSITION_CONTROL_SELECTOR =
        '[data-prks-role="' + POSITION_MUTATION_ROLE + '"], ' +
        '[data-prks-role="' + POSITION_ONLINE_ONLY_ROLE + '"]';

    function positionRuntimeState() {
        return typeof root.prksOfflineRuntimeState === 'function' ? root.prksOfflineRuntimeState() : 'online';
    }

    /** Blocks a canonical Position mutation while PRKS is unreachable. */
    function positionMutationBlocked(message) {
        return typeof root.prksOfflineGuardMutation === 'function'
            ? root.prksOfflineGuardMutation(message)
            : false;
    }

    /** The Research Graph is the one destination still online-only. */
    function positionConnectionRequired(message) {
        if (positionRuntimeState() === 'online') return false;
        if (typeof root.prksAlertMessage === 'function') {
            root.prksAlertMessage(message || 'This action requires a connection to PRKS.', 'Offline');
        }
        return true;
    }

    function applyPositionOfflineState(container) {
        if (!container || !container.querySelectorAll) return;
        const online = positionRuntimeState() === 'online';
        const nodes = container.querySelectorAll(POSITION_CONTROL_SELECTOR);
        for (let i = 0; i < nodes.length; i++) {
            const el = nodes[i];
            // Buttons take native `disabled` (blocks pointer AND keyboard, and
            // carries the shared .prks-btn:disabled styling).
            if ('disabled' in el) el.disabled = !online;
            if (online) {
                el.removeAttribute('aria-disabled');
                el.removeAttribute('title');
            } else {
                el.setAttribute('aria-disabled', 'true');
                el.setAttribute('title', 'Requires a connection to PRKS');
            }
        }
    }

    /**
     * Keeps a mounted Position page's controls in step with connectivity: a page
     * built while online becomes read-only in place when PRKS stops answering,
     * and restores on reconnect. Cached Position content itself stays readable.
     *
     * The subscription belongs to the route's owning TabContext, and each bind
     * replaces the previous one on the same container -- a TabContext container
     * survives route changes and rerenders, so re-binding must not accumulate
     * listeners (and there is no global Position runtime singleton).
     */
    function bindPositionOfflineState(ctx, container) {
        if (!container) return function () {};
        if (typeof container.__prksPositionOfflineDispose === 'function') {
            try {
                container.__prksPositionOfflineDispose();
            } catch (_e) {
                /* a stale disposer must not block the new binding */
            }
        }
        // Read current state immediately: a Position page rendered after the
        // runtime already left 'online' is never briefly mutable.
        applyPositionOfflineState(container);
        // No Argument-link activation guard lives here. Argument/Stance rows are
        // ordinary PRKS links in every runtime state: the Argument route decides
        // for itself whether it has cached data, exactly as a Work link does.
        // Ordinary workspace navigation already handles plain/middle/modified
        // clicks and route ownership -- a second Position-specific policy layer
        // would only be able to get that wrong.
        let unsubscribe = function () {};
        if (typeof root.prksOfflineRuntimeSubscribe === 'function') {
            unsubscribe =
                root.prksOfflineRuntimeSubscribe(function () {
                    if (container.__prksPositionOfflineDispose !== dispose) return;
                    applyPositionOfflineState(container);
                }) || function () {};
        }
        let unregister = function () {};
        function dispose() {
            if (container.__prksPositionOfflineDispose === dispose) container.__prksPositionOfflineDispose = null;
            unregister();
            unsubscribe();
        }
        if (ctx && typeof ctx.registerCleanup === 'function') {
            unregister = ctx.registerCleanup(dispose) || function () {};
        }
        container.__prksPositionOfflineDispose = dispose;
        return dispose;
    }

    /** No cached Position index on this device -- distinct from a cached empty one. */
    function renderPositionsIndexUnavailable(container) {
        if (!container) return;
        container.innerHTML =
            '<div class="prks-page-header page-header"><h2 class="prks-page-title">Positions not available offline</h2></div>' +
            '<p class="prks-inline-message" data-prks-role="offline-unavailable">This list has not been cached on this device.</p>';
    }

    async function createPositionFlow(ctx, ownsIndex) {
        if (typeof root.prksPromptTextDialog !== 'function') return null;
        // Guard before the dialog opens: never an editor the user cannot save.
        if (positionMutationBlocked('Creating a Position requires a connection to PRKS.')) return null;
        const name = await root.prksPromptTextDialog({
            title: 'New Position',
            okLabel: 'Create',
        });
        if (!name || !String(name).trim()) return null;
        if (ctx && !ownsIndex()) return null;
        // Connectivity can change while the prompt is open, so re-check
        // immediately before the canonical request: zero POST while offline.
        if (positionMutationBlocked('Creating a Position requires a connection to PRKS.')) return null;
        const created = await root.createPosition({ name: String(name).trim() });
        if (created && created.id && (!ctx || ownsIndex()) && typeof root.prksNavigate === 'function') {
            root.prksNavigate('#/positions/' + encodeURIComponent(created.id), {
                tabId: ctx && ctx.tabId,
            });
        }
        return created;
    }

    function positionsEmptyDataHtml() {
        return (
            '<div class="prks-research-index__empty">' +
            '<p class="meta-row">No Positions yet.</p>' +
            '<p><button type="button" class="prks-btn prks-btn--secondary" id="prks-position-new-empty" data-prks-role="' +
            POSITION_MUTATION_ROLE +
            '">New Position</button></p>' +
            '</div>'
        );
    }

    function positionRowHtml(p, icon) {
        const excerpt = String(p.description || '')
            .replace(/\s+/g, ' ')
            .trim();
        const clip = excerpt.length > 160 ? excerpt.slice(0, 159).trim() + '…' : excerpt;
        return rowHtml({
            href: '#/positions/' + encodeURIComponent(p.id),
            icon: icon,
            title: esc(p.name || 'Position'),
            meta: clip ? [esc(clip)] : [],
        });
    }

    function matchPosition(p, q) {
        if (String(p.name || '').toLowerCase().indexOf(q) >= 0) return true;
        return String(p.description || '').toLowerCase().indexOf(q) >= 0;
    }

    function renderPositionsIndex(ctx, items, container) {
        if (arguments.length < 3) {
            container = items;
            items = ctx;
            ctx = null;
        }
        const generation = ctx && ctx.generation;
        const ownsIndex = function () {
            if (!ctx || !ctx.isCurrent || !ctx.isCurrent(generation)) return false;
            const route = ctx.lastResolvedRoute || ctx.route;
            return !!(route && route.name === 'positions');
        };
        const list = Array.isArray(items) ? items : [];
        const icon = typeof root.prksIcon === 'function' ? root.prksIcon('flag', { size: 'sm' }) : '';

        function renderRows(filtered, query) {
            const host = container.querySelector('#prks-position-rows');
            if (!host) return;
            host.innerHTML = !filtered.length
                ? query && typeof root.prksResearchIndexSearchEmptyHtml === 'function'
                    ? root.prksResearchIndexSearchEmptyHtml('Positions', query)
                    : positionsEmptyDataHtml()
                : filtered
                      .map(function (p) {
                          return positionRowHtml(p, icon);
                      })
                      .join('');
            if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(host);
            if (!filtered.length && !query) {
                const emptyBtn = host.querySelector('#prks-position-new-empty');
                if (emptyBtn) {
                    emptyBtn.addEventListener('click', function () {
                        void createPositionFlow(ctx, ownsIndex);
                    });
                }
            }
            // Local search rerenders replace the empty-state New Position
            // button, so re-apply the current connectivity state to it.
            applyPositionOfflineState(container);
        }

        container.innerHTML =
            '<div class="prks-page-header page-header"><div class="page-header__title-row"><h2 class="prks-page-title">' +
            (typeof root.prksPageHeaderIconHtml === 'function' ? root.prksPageHeaderIconHtml('flag') : '') +
            ' Positions</h2>' +
            '<div class="page-header__actions">' +
            '<button type="button" class="prks-btn prks-btn--secondary" id="prks-position-new" data-prks-role="' +
            POSITION_MUTATION_ROLE +
            '">New Position</button>' +
            '</div></div></div>' +
            (list.length && typeof root.prksResearchIndexToolbarHtml === 'function'
                ? root.prksResearchIndexToolbarHtml('prks-position-search', 'Search positions…')
                : '') +
            '<div class="list-view prks-research-index" id="prks-position-rows"></div>';
        const btn = container.querySelector('#prks-position-new');
        if (btn) {
            btn.addEventListener('click', function () {
                void createPositionFlow(ctx, ownsIndex);
            });
        }
        renderRows(list, '');
        if (list.length && typeof root.prksBindResearchIndexSearch === 'function') {
            // Offline search stays entirely client-side over the already-loaded
            // (possibly cached) array -- it issues no API requests, and it can
            // only match Positions as of that snapshot.
            root.prksBindResearchIndexSearch(container, {
                inputSelector: '#prks-position-search',
                items: list,
                matchFn: matchPosition,
                renderRows: renderRows,
            });
        }
        bindPositionOfflineState(ctx, container);
        if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(container);
    }

    function renderPositionNotFound(container) {
        container.innerHTML =
            '<div class="prks-page-header page-header"><h2 class="prks-page-title">Position not found.</h2></div>' +
            '<p class="meta-row"><a class="prks-btn prks-btn--secondary" href="#/positions">Back to Positions</a></p>';
    }

    function renderPositionDetail(ctx, position, container) {
        if (ctx && typeof ctx.setEntity === 'function') ctx.setEntity('position', position);
        const p = position || {};
        const argList = Array.isArray(p.arguments) ? p.arguments : [];
        const argsHtml = argList.length
            ? '<div class="list-view prks-research-index">' +
              argList
                  .map(function (a) {
                      const kindLabel = a.kind === 'stance' ? 'Stance' : 'Argument';
                      // Rendered from the cached Position detail even offline --
                      // the relationship is real data, and the row is an
                      // ordinary link: the Argument route resolves the
                      // destination from its own cache or reports it
                      // unavailable.
                      return rowHtmlWithRole(
                          {
                              href: '#/arguments/' + encodeURIComponent(a.id),
                              title: esc(a.name || a.id),
                              kind: esc(kindLabel),
                              meta: [esc(a.verdict_label || a.verdict_id || '')],
                          },
                          POSITION_ARGUMENT_LINK_ROLE
                      );
                  })
                  .join('') +
              '</div>'
            : '<p class="meta-row">No Arguments or Stances target this Position yet.</p>';
        container.innerHTML =
            '<div class="prks-page-header page-header"><div class="page-header__title-row"><div>' +
            '<p class="saved-view-detail__kicker">Position</p><h2 class="prks-page-title">' +
            esc(p.name || 'Position') +
            '</h2></div><div class="page-header__actions">' +
            '<button type="button" class="prks-btn prks-btn--secondary" id="prks-position-view-graph" data-prks-role="' +
            POSITION_ONLINE_ONLY_ROLE +
            '">View in graph</button>' +
            '</div></div></div>' +
            '<div class="research-entity">' +
            '<section class="research-entity__section" aria-labelledby="prks-position-desc-h">' +
            sectionHead('Description', { headingId: 'prks-position-desc-h' }) +
            '<div class="research-md">' +
            (String(p.description || '').trim() ? md(p.description) : '<p class="meta-row">No description yet.</p>') +
            '</div></section>' +
            '<section class="research-entity__section" aria-labelledby="prks-position-args-h">' +
            sectionHead('Arguments & Stances', { headingId: 'prks-position-args-h', count: argList.length }) +
            argsHtml +
            '</section>' +
            '</div>';
        const viewGraph = container.querySelector('#prks-position-view-graph');
        if (viewGraph) {
            viewGraph.addEventListener('click', function () {
                // The Research Graph is online-only in Phase 1: say so plainly
                // rather than navigating into a route that cannot load its data.
                if (positionConnectionRequired('The Research Graph requires a connection to PRKS.')) return;
                const hash =
                    typeof root.prksGraphFocusHash === 'function'
                        ? root.prksGraphFocusHash('position', p.id)
                        : '#/graph?focus=' + encodeURIComponent('position:' + p.id);
                if (typeof root.prksNavigate === 'function') {
                    root.prksNavigate(hash, { tabId: ctx && ctx.tabId });
                }
            });
        }
        bindPositionOfflineState(ctx, container);
        if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(container);
    }

    const api = {
        renderPositionsIndex: renderPositionsIndex,
        renderPositionsIndexUnavailable: renderPositionsIndexUnavailable,
        renderPositionDetail: renderPositionDetail,
        renderPositionNotFound: renderPositionNotFound,
        prksBindPositionOfflineState: bindPositionOfflineState,
        prksApplyPositionOfflineState: applyPositionOfflineState,
    };
    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : typeof global !== 'undefined' ? global : this);
