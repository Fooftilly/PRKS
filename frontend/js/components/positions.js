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

    function sectionHead(title, opts) {
        if (typeof root.prksResearchSectionHeadHtml === 'function') return root.prksResearchSectionHeadHtml(title, opts);
        return '<h3>' + esc(title) + '</h3>';
    }

    async function createPositionFlow(ctx, ownsIndex) {
        if (typeof root.prksPromptTextDialog !== 'function') return null;
        const name = await root.prksPromptTextDialog({
            title: 'New Position',
            okLabel: 'Create',
        });
        if (!name || !String(name).trim()) return null;
        if (ctx && !ownsIndex()) return null;
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
            '<p><button type="button" class="prks-btn prks-btn--secondary" id="prks-position-new-empty">New Position</button></p>' +
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
        }

        container.innerHTML =
            '<div class="prks-page-header page-header"><div class="page-header__title-row"><h2 class="prks-page-title">' +
            (typeof root.prksPageHeaderIconHtml === 'function' ? root.prksPageHeaderIconHtml('flag') : '') +
            ' Positions</h2>' +
            '<div class="page-header__actions">' +
            '<button type="button" class="prks-btn prks-btn--secondary" id="prks-position-new">New Position</button>' +
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
            root.prksBindResearchIndexSearch(container, {
                inputSelector: '#prks-position-search',
                items: list,
                matchFn: matchPosition,
                renderRows: renderRows,
            });
        }
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
                      return rowHtml({
                          href: '#/arguments/' + encodeURIComponent(a.id),
                          title: esc(a.name || a.id),
                          kind: esc(kindLabel),
                          meta: [esc(a.verdict_label || a.verdict_id || '')],
                      });
                  })
                  .join('') +
              '</div>'
            : '<p class="meta-row">No Arguments or Stances target this Position yet.</p>';
        container.innerHTML =
            '<div class="prks-page-header page-header"><div class="page-header__title-row"><div>' +
            '<p class="saved-view-detail__kicker">Position</p><h2 class="prks-page-title">' +
            esc(p.name || 'Position') +
            '</h2></div><div class="page-header__actions">' +
            '<button type="button" class="prks-btn prks-btn--secondary" id="prks-position-view-graph">View in graph</button>' +
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
                const hash =
                    typeof root.prksGraphFocusHash === 'function'
                        ? root.prksGraphFocusHash('position', p.id)
                        : '#/graph?focus=' + encodeURIComponent('position:' + p.id);
                if (typeof root.prksNavigate === 'function') {
                    root.prksNavigate(hash, { tabId: ctx && ctx.tabId });
                }
            });
        }
        if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(container);
    }

    const api = {
        renderPositionsIndex: renderPositionsIndex,
        renderPositionDetail: renderPositionDetail,
        renderPositionNotFound: renderPositionNotFound,
    };
    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : typeof global !== 'undefined' ? global : this);
