/**
 * Concepts index + detail. Work membership is derived from research-note references.
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
        const raw = String(text || '');
        if (!raw.trim()) return '<p class="meta-row">No definition yet.</p>';
        if (typeof EasyMDE === 'function' && typeof root.prksSanitizeMarkdownPreviewHtml === 'function') {
            try {
                if (!root.__prksResearchMdEngine) {
                    const ta = document.createElement('textarea');
                    ta.hidden = true;
                    (document.body || document.documentElement).appendChild(ta);
                    root.__prksResearchMdEngine = new EasyMDE({
                        element: ta,
                        spellChecker: false,
                        autoDownloadFontAwesome: false,
                        toolbar: false,
                        status: false,
                    });
                }
                return root.prksSanitizeMarkdownPreviewHtml(root.__prksResearchMdEngine.markdown(raw));
            } catch (_e) {}
        }
        return '<p>' + esc(raw) + '</p>';
    }

    function promptText(opts) {
        if (typeof root.prksPromptTextDialog !== 'function') return Promise.resolve(null);
        return root.prksPromptTextDialog(opts);
    }

    function researchIndexRowHtml(opts) {
        const o = opts || {};
        const icon = o.icon
            ? '<span class="prks-research-row__icon" aria-hidden="true">' + o.icon + '</span>'
            : '';
        const kind = o.kind
            ? '<span class="prks-research-row__kind">' + o.kind + '</span>'
            : '';
        const meta = (o.meta || []).filter(Boolean);
        const metaHtml = meta.length
            ? '<span class="prks-research-row__meta">' +
              meta
                  .map(function (m) {
                      return '<span class="prks-research-row__meta-item">' + m + '</span>';
                  })
                  .join('') +
              '</span>'
            : '';
        return (
            '<a class="prks-list-row prks-research-row" href="' +
            o.href +
            '">' +
            icon +
            '<span class="prks-research-row__body"><span class="prks-research-row__title-line"><span class="prks-research-row__title">' +
            o.title +
            '</span>' +
            kind +
            '</span>' +
            metaHtml +
            '</span></a>'
        );
    }

    function renderConceptsIndex(items, container) {
        const list = Array.isArray(items) ? items : [];
        const icon = typeof root.prksIcon === 'function' ? root.prksIcon('network', { size: 'sm' }) : '';
        const rows = list.length
            ? list
                  .map(function (c) {
                      const id = String(c.id || '');
                      const parentNames = (c.parents || [])
                          .map(function (p) {
                              return esc(p.name || p.id);
                          })
                          .filter(Boolean);
                      const parentLabel = parentNames.length ? parentNames.join(', ') : 'No parent';
                      const subs = Number(c.subconcept_count) || 0;
                      const notes = Number(c.mention_count) || 0;
                      return researchIndexRowHtml({
                          href: '#/concepts/' + encodeURIComponent(id),
                          icon: icon,
                          title: esc(c.name || 'Concept'),
                          meta: [
                              parentLabel,
                              String(subs) + (subs === 1 ? ' subconcept' : ' subconcepts'),
                              String(notes) + (notes === 1 ? ' note mention' : ' note mentions'),
                          ],
                      });
                  })
                  .join('')
            : '<p class="meta-row">No Concepts yet. Type <code>[[concept:Name]]</code> in research notes, or create one here.</p>';
        container.innerHTML =
            '<div class="prks-page-header page-header"><div class="page-header__title-row"><h2 class="prks-page-title">' +
            (typeof root.prksPageHeaderIconHtml === 'function' ? root.prksPageHeaderIconHtml('network') : '') +
            ' Concepts</h2>' +
            '<div class="page-header__actions">' +
            '<button type="button" class="prks-btn prks-btn--secondary" id="prks-concept-new">New Concept</button>' +
            '</div></div></div><div class="list-view prks-research-index">' +
            rows +
            '</div>';
        const btn = container.querySelector('#prks-concept-new');
        if (btn) {
            btn.addEventListener('click', function () {
                void createConceptFlow();
            });
        }
        if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(container);
    }

    async function createConceptFlow(initialName) {
        const name = await promptText({
            title: 'New Concept',
            defaultValue: initialName || '',
            okLabel: 'Create',
        });
        if (name == null || !String(name).trim()) return null;
        if (typeof root.createConcept !== 'function') return null;
        try {
            const created = await root.createConcept({ name: String(name).trim() });
            if (created && created.id && typeof root.prksNavigate === 'function') {
                root.prksNavigate('#/concepts/' + encodeURIComponent(created.id));
            }
            return created;
        } catch (err) {
            if (typeof root.prksAlertDialog === 'function') {
                await root.prksAlertDialog({
                    title: 'Could not create Concept',
                    message: (err && err.message) || 'Could not create Concept.',
                });
            }
            return null;
        }
    }

    function renderConceptNotFound(container) {
        container.innerHTML =
            '<div class="prks-page-header page-header"><h2 class="prks-page-title">Concept not found.</h2></div>' +
            '<p class="meta-row"><a class="prks-btn prks-btn--secondary" href="#/concepts">Back to Concepts</a></p>';
    }

    function renderConceptDetail(ctx, concept, container) {
        if (ctx && typeof ctx.setEntity === 'function') ctx.setEntity('concept', concept);
        const c = concept || {};
        const generation = ctx && ctx.generation;
        const ownsConcept = function () {
            return typeof root.prksTabContextOwnsEntityRoute === 'function'
                ? root.prksTabContextOwnsEntityRoute(ctx, generation, 'concept', c.id, 'concept-detail')
                : !!(ctx && ctx.isCurrent && ctx.isCurrent(generation));
        };
        const refreshConcept = function () {
            if (!ownsConcept() || typeof root.prksNavigate !== 'function') return;
            root.prksNavigate('#/concepts/' + encodeURIComponent(c.id), {
                replace: true,
                tabId: ctx.tabId,
            });
        };
        const aliases = (c.aliases || []).map(function (a) {
            return '<li>' + esc(a) + '</li>';
        }).join('') || '<li class="meta-row">None</li>';
        const parents = (c.parents || []).map(function (p) {
            return (
                '<li><a href="#/concepts/' +
                encodeURIComponent(p.id) +
                '">' +
                esc(p.name) +
                '</a></li>'
            );
        }).join('') || '<li class="meta-row">None</li>';
        const children = (c.children || []).map(function (p) {
            return (
                '<li><a href="#/concepts/' +
                encodeURIComponent(p.id) +
                '">' +
                esc(p.name) +
                '</a></li>'
            );
        }).join('') || '<li class="meta-row">None</li>';
        const mentions = (c.mentions || []).map(function (m) {
            const occ = (m.occurrences || [])
                .map(function (o) {
                    return '<p class="meta-row">…' + esc(o.snippet || '') + '…</p>';
                })
                .join('');
            return (
                '<div class="project-card"><a href="#/works/' +
                encodeURIComponent(m.work_id) +
                '">' +
                esc(m.title || m.work_id) +
                '</a>' +
                occ +
                '</div>'
            );
        }).join('') || '<p class="meta-row">No research-note references.</p>';
        container.innerHTML =
            '<div class="prks-page-header page-header"><div class="page-header__title-row"><div>' +
            '<p class="saved-view-detail__kicker">Concept</p><h2 class="prks-page-title">' +
            esc(c.name || 'Concept') +
            '</h2></div><div class="page-header__actions">' +
            '<button type="button" class="prks-btn prks-btn--secondary" id="prks-concept-view-graph">View in graph</button>' +
            '<button type="button" class="prks-btn prks-btn--secondary" id="prks-concept-rename">Rename</button>' +
            '<button type="button" class="prks-btn prks-btn--secondary" id="prks-concept-delete">Delete</button>' +
            '</div></div></div>' +
            '<h3>Definition</h3><div class="research-md">' +
            md(c.description) +
            '</div>' +
            '<p><button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" id="prks-concept-edit-def">Edit definition</button></p>' +
            '<h3>Search keys / aliases</h3><ul>' +
            aliases +
            '</ul>' +
            '<p><button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" id="prks-concept-edit-aliases">Edit aliases</button></p>' +
            '<h3>Parent concepts</h3><ul>' +
            parents +
            '</ul>' +
            '<p><button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" id="prks-concept-edit-parents">Edit parents</button></p>' +
            '<h3>Subconcepts</h3><ul>' +
            children +
            '</ul>' +
            '<h3>Mentioned in research notes</h3>' +
            '<p class="meta-row">' +
            esc(String(c.mention_count || 0)) +
            ' references</p>' +
            mentions;
        const viewGraph = container.querySelector('#prks-concept-view-graph');
        if (viewGraph) {
            viewGraph.addEventListener('click', function () {
                const hash =
                    typeof root.prksGraphFocusHash === 'function'
                        ? root.prksGraphFocusHash('concept', c.id)
                        : '#/graph?focus=' + encodeURIComponent('concept:' + c.id);
                if (typeof root.prksNavigate === 'function') root.prksNavigate(hash, { tabId: ctx && ctx.tabId });
            });
        }
        container.querySelector('#prks-concept-rename').addEventListener('click', function () {
            void renameConcept(ctx, generation, c);
        });
        container.querySelector('#prks-concept-delete').addEventListener('click', function () {
            void deleteConcept(ctx, generation, c);
        });
        container.querySelector('#prks-concept-edit-def').addEventListener('click', function () {
            void (async function () {
                const next = await promptText({
                    title: 'Definition',
                    message: 'Markdown',
                    defaultValue: c.description || '',
                    multiline: true,
                    okLabel: 'Save',
                });
                if (next == null) return;
                if (!ownsConcept()) return;
                await root.updateConcept(c.id, { description: next });
                refreshConcept();
            })();
        });
        container.querySelector('#prks-concept-edit-aliases').addEventListener('click', function () {
            void (async function () {
                const next = await promptText({
                    title: 'Search keys / aliases',
                    message: 'One alias per line',
                    defaultValue: (c.aliases || []).join('\n'),
                    multiline: true,
                    okLabel: 'Save',
                });
                if (next == null) return;
                if (!ownsConcept()) return;
                const aliases = next.split(/\n/).map(function (s) { return s.trim(); }).filter(Boolean);
                await root.replaceConceptAliases(c.id, aliases);
                refreshConcept();
            })();
        });
        container.querySelector('#prks-concept-edit-parents').addEventListener('click', function () {
            void (async function () {
                const next = await promptText({
                    title: 'Parent concepts',
                    message: 'Parent Concept IDs, comma-separated',
                    defaultValue: (c.parents || []).map(function (p) { return p.id; }).join(', '),
                    okLabel: 'Save',
                });
                if (next == null) return;
                if (!ownsConcept()) return;
                const ids = next.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
                await root.replaceConceptParents(c.id, ids);
                refreshConcept();
            })();
        });
        if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(container);
    }

    async function renameConcept(ctx, generation, c) {
        const next = await promptText({
            title: 'Rename Concept',
            defaultValue: c.name || '',
            okLabel: 'Save',
        });
        if (next == null || !String(next).trim()) return;
        if (!ctx || !ctx.isCurrent || !ctx.isCurrent(generation)) return;
        try {
            await root.updateConcept(c.id, { name: String(next).trim() });
            if (
                typeof root.prksTabContextOwnsEntityRoute === 'function' &&
                root.prksTabContextOwnsEntityRoute(ctx, generation, 'concept', c.id, 'concept-detail') &&
                typeof root.prksNavigate === 'function'
            ) {
                root.prksNavigate('#/concepts/' + encodeURIComponent(c.id), {
                    replace: true,
                    tabId: ctx.tabId,
                });
            }
        } catch (err) {
            if (!ctx || !ctx.isCurrent || !ctx.isCurrent(generation)) return;
            if (typeof root.prksAlertDialog === 'function') {
                await root.prksAlertDialog({ title: 'Could not rename', message: (err && err.message) || '' });
            }
        }
    }

    async function deleteConcept(ctx, generation, c) {
        const ok =
            typeof root.prksConfirmDestructive === 'function'
                ? await root.prksConfirmDestructive({
                      title: 'Delete Concept?',
                      message: 'Delete this Concept? Notes that still mention it will recreate a similarly named Concept on save.',
                      confirmLabel: 'Delete',
                  })
                : true;
        if (!ok) return;
        if (!ctx || !ctx.isCurrent || !ctx.isCurrent(generation)) return;
        try {
            await root.deleteConcept(c.id);
            if (
                typeof root.prksTabContextOwnsEntityRoute === 'function' &&
                root.prksTabContextOwnsEntityRoute(ctx, generation, 'concept', c.id, 'concept-detail') &&
                typeof root.prksNavigate === 'function'
            ) {
                root.prksNavigate('#/concepts', { replace: true, tabId: ctx.tabId });
            }
        } catch (err) {
            if (!ctx || !ctx.isCurrent || !ctx.isCurrent(generation)) return;
            const msg =
                err && err.code === 'concept_in_use'
                    ? 'This Concept is still referenced in research notes. Remove or replace those references before deleting it.'
                    : (err && err.message) || 'Could not delete Concept.';
            if (typeof root.prksAlertDialog === 'function') {
                await root.prksAlertDialog({ title: 'Cannot delete Concept', message: msg });
            }
        }
    }

    const api = {
        renderConceptsIndex: renderConceptsIndex,
        renderConceptDetail: renderConceptDetail,
        renderConceptNotFound: renderConceptNotFound,
        prksCreateConceptFlow: createConceptFlow,
        prksResearchMarkdownHtml: md,
        prksResearchIndexRowHtml: researchIndexRowHtml,
    };
    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : typeof global !== 'undefined' ? global : this);
