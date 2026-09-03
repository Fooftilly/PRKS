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

    function renderPositionsIndex(items, container) {
        const list = Array.isArray(items) ? items : [];
        const icon = typeof root.prksIcon === 'function' ? root.prksIcon('flag', { size: 'sm' }) : '';
        const rowHtml =
            typeof root.prksResearchIndexRowHtml === 'function' ? root.prksResearchIndexRowHtml : null;
        const rows = list.length
            ? list
                  .map(function (p) {
                      const excerpt = String(p.description || '')
                          .replace(/\s+/g, ' ')
                          .trim();
                      const clip =
                          excerpt.length > 160 ? excerpt.slice(0, 159).trim() + '…' : excerpt;
                      return rowHtml
                          ? rowHtml({
                                href: '#/positions/' + encodeURIComponent(p.id),
                                icon: icon,
                                title: esc(p.name || 'Position'),
                                meta: clip ? [esc(clip)] : [],
                            })
                          : '<a class="prks-list-row prks-research-row" href="#/positions/' +
                            encodeURIComponent(p.id) +
                            '">' +
                            esc(p.name || 'Position') +
                            '</a>';
                  })
                  .join('')
            : '<p class="meta-row">No Positions yet. Create one to use as an Argument target.</p>';
        container.innerHTML =
            '<div class="prks-page-header page-header"><div class="page-header__title-row"><h2 class="prks-page-title">' +
            (typeof root.prksPageHeaderIconHtml === 'function' ? root.prksPageHeaderIconHtml('flag') : '') +
            ' Positions</h2>' +
            '<div class="page-header__actions">' +
            '<button type="button" class="prks-btn prks-btn--secondary" id="prks-position-new">New Position</button>' +
            '</div></div></div><div class="list-view prks-research-index">' +
            rows +
            '</div>';
        const btn = container.querySelector('#prks-position-new');
        if (btn) {
            btn.addEventListener('click', function () {
                void (async function () {
                    if (typeof root.prksPromptTextDialog !== 'function') return;
                    const name = await root.prksPromptTextDialog({
                        title: 'New Position',
                        okLabel: 'Create',
                    });
                    if (!name || !String(name).trim()) return;
                    const created = await root.createPosition({ name: String(name).trim() });
                    if (created && created.id && typeof root.prksNavigate === 'function') {
                        root.prksNavigate('#/positions/' + encodeURIComponent(created.id));
                    }
                })();
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
        const args = (p.arguments || [])
            .map(function (a) {
                return (
                    '<div class="project-card">' +
                    esc(a.verdict_label || a.verdict_id || '') +
                    ' · <a href="#/arguments/' +
                    encodeURIComponent(a.id) +
                    '">' +
                    esc(a.name || a.id) +
                    '</a></div>'
                );
            })
            .join('') || '<p class="meta-row">No Arguments or Stances target this Position yet.</p>';
        container.innerHTML =
            '<div class="prks-page-header page-header"><div class="page-header__title-row"><div>' +
            '<p class="saved-view-detail__kicker">Position</p><h2 class="prks-page-title">' +
            esc(p.name || 'Position') +
            '</h2></div><div class="page-header__actions">' +
            '<button type="button" class="prks-btn prks-btn--secondary" id="prks-position-view-graph">View in graph</button>' +
            '</div></div></div>' +
            '<div class="research-md">' +
            md(p.description) +
            '</div>' +
            '<h3>Arguments / Stances</h3>' +
            args;
        const viewGraph = container.querySelector('#prks-position-view-graph');
        if (viewGraph) {
            viewGraph.addEventListener('click', function () {
                const hash =
                    typeof root.prksGraphFocusHash === 'function'
                        ? root.prksGraphFocusHash('position', p.id)
                        : '#/graph?focus=' + encodeURIComponent('position:' + p.id);
                if (typeof root.prksNavigate === 'function') root.prksNavigate(hash);
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
