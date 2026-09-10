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

    /** Shared research-index search: normalize once, filter already-loaded rows locally,
     * rerender via the caller's own row markup, and distinguish "no data" from "no matches". */
    function normalizeSearchQuery(q) {
        return String(q == null ? '' : q)
            .trim()
            .toLowerCase();
    }

    /** Clear Search is delegated once per route root, but that root is a persistent
     * TabContext container that survives route changes and rerenders -- only its inner
     * markup is replaced. A listener that closed over one render's `input`/`apply()` would
     * keep firing against that historical render forever. Instead, each bind call replaces
     * a single current-controller slot on the container; the delegated handler always reads
     * that slot at click time and requires the stored input still be attached to the page. */
    function bindResearchIndexSearch(container, config) {
        const input = container && container.querySelector ? container.querySelector(config.inputSelector) : null;
        if (!input) return null;
        function apply() {
            const q = normalizeSearchQuery(input.value);
            const filtered = !q
                ? config.items.slice()
                : config.items.filter(function (item) {
                      return config.matchFn(item, q);
                  });
            config.renderRows(filtered, q);
        }
        input.addEventListener('input', apply);
        container.__prksResearchSearchController = { input: input, apply: apply };
        if (!container.__prksResearchSearchClearBound) {
            container.__prksResearchSearchClearBound = true;
            container.addEventListener('click', function (ev) {
                const btn = ev.target.closest && ev.target.closest('[data-research-search-clear]');
                if (!btn) return;
                const controller = container.__prksResearchSearchController;
                if (!controller || !controller.input || !controller.input.isConnected) return;
                controller.input.value = '';
                controller.input.focus();
                controller.apply();
            });
        }
        apply();
        return { refresh: apply };
    }

    function researchIndexToolbarHtml(inputId, placeholder) {
        return (
            '<div class="prks-toolbar prks-research-index__toolbar">' +
            '<input type="search" class="prks-input" id="' +
            esc(inputId) +
            '" autocomplete="off" placeholder="' +
            esc(placeholder) +
            '" aria-label="' +
            esc(placeholder) +
            '">' +
            '</div>'
        );
    }

    function researchIndexSearchEmptyHtml(pluralLabel, query) {
        return (
            '<div class="prks-research-index__empty">' +
            '<p class="meta-row">No ' +
            esc(pluralLabel) +
            ' match “' +
            esc(query) +
            '”.</p>' +
            '<p><button type="button" class="prks-btn prks-btn--ghost prks-btn--sm" data-research-search-clear>Clear search</button></p>' +
            '</div>'
        );
    }

    /** Section head for `.research-entity__section`: title, optional count, optional
     * section-local action button. Keeps edit controls visually tied to their section
     * instead of floating below the content they modify. */
    function researchSectionHeadHtml(title, opts) {
        const o = opts || {};
        let actionsHtml = '';
        if (o.count != null) {
            actionsHtml += '<span class="research-entity__section-count">' + esc(String(o.count)) + '</span>';
        }
        if (o.actionId) {
            actionsHtml +=
                '<button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" id="' +
                esc(o.actionId) +
                '"' +
                (o.actionRole ? ' data-prks-role="' + esc(o.actionRole) + '"' : '') +
                '>' +
                esc(o.actionLabel || 'Edit') +
                '</button>';
        }
        return (
            '<div class="research-entity__section-head">' +
            '<h3' +
            (o.headingId ? ' id="' + esc(o.headingId) + '"' : '') +
            '>' +
            esc(title) +
            '</h3>' +
            (actionsHtml ? '<div class="research-entity__section-head-actions">' + actionsHtml + '</div>' : '') +
            '</div>' +
            (o.sub ? '<p class="research-entity__section-sub meta-row">' + esc(o.sub) + '</p>' : '')
        );
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

    /* --- Offline policy for Concept routes (AGENTS.md "Offline / PWA") -------
     * Concepts are read-only offline in Phase 1: cached index/detail render, but
     * every canonical mutation is blocked outright -- never queued, never faked.
     * Controls carry these roles so one helper can disable them all, including
     * markup rerendered after the initial bind. */
    const CONCEPT_MUTATION_ROLE = 'concept-mutation-control';
    const CONCEPT_CONTROL_SELECTOR = '[data-prks-role="' + CONCEPT_MUTATION_ROLE + '"]';

    function conceptRuntimeState() {
        return typeof root.prksOfflineRuntimeState === 'function' ? root.prksOfflineRuntimeState() : 'online';
    }

    /** Blocks a canonical Concept mutation while PRKS is unreachable. */
    function conceptMutationBlocked(message) {
        return typeof root.prksOfflineGuardMutation === 'function'
            ? root.prksOfflineGuardMutation(message)
            : false;
    }

    function applyConceptOfflineState(container) {
        if (!container || !container.querySelectorAll) return;
        const online = conceptRuntimeState() === 'online';
        const nodes = container.querySelectorAll(CONCEPT_CONTROL_SELECTOR);
        for (let i = 0; i < nodes.length; i++) {
            const el = nodes[i];
            // Native `disabled` blocks both pointer and keyboard activation and
            // already carries the shared .prks-btn:disabled styling.
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
     * Keeps a mounted Concept page's controls in step with connectivity: a page
     * built while online becomes read-only in place when PRKS stops answering,
     * and restores on reconnect. Read/navigation links are never touched.
     *
     * The subscription belongs to the route's owning TabContext, and each bind
     * replaces the previous one on the same container -- a TabContext container
     * survives route changes and rerenders, so re-binding must not accumulate
     * listeners (and there is no global Concept runtime singleton).
     */
    function bindConceptOfflineState(ctx, container) {
        if (!container) return function () {};
        if (typeof container.__prksConceptOfflineDispose === 'function') {
            try {
                container.__prksConceptOfflineDispose();
            } catch (_e) {
                /* a stale disposer must not block the new binding */
            }
        }
        // Read current state immediately: a Concept page rendered after the
        // runtime already left 'online' is never briefly mutable.
        applyConceptOfflineState(container);
        let unsubscribe = function () {};
        if (typeof root.prksOfflineRuntimeSubscribe === 'function') {
            unsubscribe =
                root.prksOfflineRuntimeSubscribe(function () {
                    if (container.__prksConceptOfflineDispose !== dispose) return;
                    applyConceptOfflineState(container);
                }) || function () {};
        }
        let unregister = function () {};
        function dispose() {
            if (container.__prksConceptOfflineDispose === dispose) container.__prksConceptOfflineDispose = null;
            unregister();
            unsubscribe();
        }
        if (ctx && typeof ctx.registerCleanup === 'function') {
            unregister = ctx.registerCleanup(dispose) || function () {};
        }
        container.__prksConceptOfflineDispose = dispose;
        return dispose;
    }

    /** No cached Concept index on this device -- explicitly different from a cached empty one. */
    function renderConceptsIndexUnavailable(container) {
        if (!container) return;
        container.innerHTML =
            '<div class="prks-page-header page-header"><h2 class="prks-page-title">Concepts not available offline</h2></div>' +
            '<p class="prks-inline-message" data-prks-role="offline-unavailable">This list has not been cached on this device.</p>';
    }

    function conceptsEmptyDataHtml() {
        return (
            '<div class="prks-research-index__empty">' +
            '<p class="meta-row">No Concepts yet.</p>' +
            '<p><button type="button" class="prks-btn prks-btn--secondary" id="prks-concept-new-empty" data-prks-role="' +
            CONCEPT_MUTATION_ROLE +
            '">New Concept</button></p>' +
            '<p class="meta-row prks-research-index__empty-hint">Concepts are also created automatically when you type <code>[[concept:Name]]</code> in research notes.</p>' +
            '</div>'
        );
    }

    function conceptRowHtml(c, icon) {
        const id = String(c.id || '');
        const parentNames = (c.parents || [])
            .map(function (p) {
                return esc(p.name || p.id);
            })
            .filter(Boolean);
        const parentLabel = parentNames.length ? 'Parent: ' + parentNames.join(', ') : 'Top-level concept';
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
    }

    function matchConcept(c, q) {
        if (String(c.name || '').toLowerCase().indexOf(q) >= 0) return true;
        const aliases = Array.isArray(c.aliases) ? c.aliases : [];
        for (let i = 0; i < aliases.length; i++) {
            if (String(aliases[i] || '').toLowerCase().indexOf(q) >= 0) return true;
        }
        const parents = Array.isArray(c.parents) ? c.parents : [];
        for (let j = 0; j < parents.length; j++) {
            if (String((parents[j] && parents[j].name) || '').toLowerCase().indexOf(q) >= 0) return true;
        }
        return false;
    }

    /**
     * `ctx` is the owning TabContext: the index subscribes to connectivity so its
     * New Concept controls follow live state, and that subscription is registered
     * with the route's context rather than leaked globally per render.
     */
    function renderConceptsIndex(ctx, items, container) {
        const list = Array.isArray(items) ? items : [];
        const icon = typeof root.prksIcon === 'function' ? root.prksIcon('network', { size: 'sm' }) : '';

        function renderRows(filtered, query) {
            const host = container.querySelector('#prks-concept-rows');
            if (!host) return;
            host.innerHTML = !filtered.length
                ? query
                    ? researchIndexSearchEmptyHtml('Concepts', query)
                    : conceptsEmptyDataHtml()
                : filtered
                      .map(function (c) {
                          return conceptRowHtml(c, icon);
                      })
                      .join('');
            if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(host);
            if (!filtered.length && !query) {
                const emptyBtn = host.querySelector('#prks-concept-new-empty');
                if (emptyBtn) emptyBtn.addEventListener('click', function () { void createConceptFlow(); });
            }
            // Local search rerenders replace the empty-state New Concept button,
            // so re-apply the current connectivity state to the fresh markup.
            applyConceptOfflineState(container);
        }

        container.innerHTML =
            '<div class="prks-page-header page-header"><div class="page-header__title-row"><h2 class="prks-page-title">' +
            (typeof root.prksPageHeaderIconHtml === 'function' ? root.prksPageHeaderIconHtml('network') : '') +
            ' Concepts</h2>' +
            '<div class="page-header__actions">' +
            '<button type="button" class="prks-btn prks-btn--secondary" id="prks-concept-new" data-prks-role="' +
            CONCEPT_MUTATION_ROLE +
            '">New Concept</button>' +
            '</div></div></div>' +
            (list.length ? researchIndexToolbarHtml('prks-concept-search', 'Search concepts…') : '') +
            '<div class="list-view prks-research-index" id="prks-concept-rows"></div>';

        const btn = container.querySelector('#prks-concept-new');
        if (btn) {
            btn.addEventListener('click', function () {
                void createConceptFlow();
            });
        }
        renderRows(list, '');
        if (list.length) {
            // Offline search stays entirely client-side over the already-loaded
            // (possibly cached) array -- it issues no API requests, and it can
            // only match Concepts as of that snapshot.
            bindResearchIndexSearch(container, {
                inputSelector: '#prks-concept-search',
                items: list,
                matchFn: matchConcept,
                renderRows: renderRows,
            });
        }
        bindConceptOfflineState(ctx, container);
        if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(container);
    }

    async function createConceptFlow(initialName) {
        // Guard before the dialog opens: nothing typed into an editor that can
        // never save. Also reachable from the Work Research Notes markup flow.
        if (conceptMutationBlocked('Creating a Concept requires a connection to PRKS.')) return null;
        const name = await promptText({
            title: 'New Concept',
            defaultValue: initialName || '',
            okLabel: 'Create',
        });
        if (name == null || !String(name).trim()) return null;
        if (typeof root.createConcept !== 'function') return null;
        // Connectivity can change while the dialog is open; re-check immediately
        // before the canonical request so no POST is ever attempted offline.
        if (conceptMutationBlocked('Creating a Concept requires a connection to PRKS.')) return null;
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
        const aliasList = Array.isArray(c.aliases) ? c.aliases : [];
        const aliasesHtml = aliasList.length
            ? '<div class="research-entity__chips">' +
              aliasList
                  .map(function (a) {
                      return '<span class="tag research-entity__alias-chip">' + esc(a) + '</span>';
                  })
                  .join('') +
              '</div>'
            : '<p class="meta-row">No aliases.</p>';
        const parentList = Array.isArray(c.parents) ? c.parents : [];
        const parentsHtml = parentList.length
            ? '<div class="list-view prks-research-index">' +
              parentList
                  .map(function (p) {
                      return researchIndexRowHtml({
                          href: '#/concepts/' + encodeURIComponent(p.id),
                          title: esc(p.name || p.id),
                          kind: 'Concept',
                      });
                  })
                  .join('') +
              '</div>'
            : '<p class="meta-row">Top-level concept.</p>';
        const childList = Array.isArray(c.children) ? c.children : [];
        const childrenHtml = childList.length
            ? '<div class="list-view prks-research-index">' +
              childList
                  .map(function (p) {
                      return researchIndexRowHtml({
                          href: '#/concepts/' + encodeURIComponent(p.id),
                          title: esc(p.name || p.id),
                          kind: 'Concept',
                      });
                  })
                  .join('') +
              '</div>'
            : '<p class="meta-row">No subconcepts.</p>';
        const mentionList = Array.isArray(c.mentions) ? c.mentions : [];
        const mentionsHtml = mentionList.length
            ? '<div class="research-entity__mentions">' +
              mentionList
                  .map(function (m) {
                      const occ = (m.occurrences || [])
                          .map(function (o) {
                              return (
                                  '<p class="research-entity__mention-snippet meta-row">…' +
                                  esc(o.snippet || '') +
                                  '…</p>'
                              );
                          })
                          .join('');
                      return (
                          '<div class="research-entity__mention">' +
                          '<a class="research-entity__mention-title" href="#/works/' +
                          encodeURIComponent(m.work_id) +
                          '">' +
                          esc(m.title || m.work_id) +
                          '</a>' +
                          occ +
                          '</div>'
                      );
                  })
                  .join('') +
              '</div>'
            : '<p class="meta-row">No research-note references.</p>';
        container.innerHTML =
            '<div class="prks-page-header page-header"><div class="page-header__title-row"><div>' +
            '<p class="saved-view-detail__kicker">Concept</p><h2 class="prks-page-title">' +
            esc(c.name || 'Concept') +
            '</h2></div><div class="page-header__actions">' +
            '<button type="button" class="prks-btn prks-btn--secondary" id="prks-concept-view-graph">View in graph</button>' +
            '<button type="button" class="prks-btn prks-btn--secondary" id="prks-concept-rename" data-prks-role="' +
            CONCEPT_MUTATION_ROLE +
            '">Rename</button>' +
            '<button type="button" class="prks-btn prks-btn--quiet-danger prks-page-action--destructive" id="prks-concept-delete" data-prks-role="' +
            CONCEPT_MUTATION_ROLE +
            '">Delete</button>' +
            '</div></div></div>' +
            '<div class="research-entity">' +
            '<section class="research-entity__section" aria-labelledby="prks-concept-def-h">' +
            researchSectionHeadHtml('Definition', { headingId: 'prks-concept-def-h', actionId: 'prks-concept-edit-def',
                actionRole: CONCEPT_MUTATION_ROLE }) +
            '<div class="research-md">' +
            md(c.description) +
            '</div></section>' +
            '<section class="research-entity__section" aria-labelledby="prks-concept-aliases-h">' +
            researchSectionHeadHtml('Search keys / aliases', {
                headingId: 'prks-concept-aliases-h',
                actionId: 'prks-concept-edit-aliases',
                actionRole: CONCEPT_MUTATION_ROLE,
                sub: aliasList.length ? String(aliasList.length) + (aliasList.length === 1 ? ' alias' : ' aliases') : '',
            }) +
            aliasesHtml +
            '</section>' +
            '<section class="research-entity__section" aria-labelledby="prks-concept-parents-h">' +
            researchSectionHeadHtml('Parent concepts', {
                headingId: 'prks-concept-parents-h',
                actionId: 'prks-concept-edit-parents',
                actionRole: CONCEPT_MUTATION_ROLE,
                sub: parentList.length ? String(parentList.length) + (parentList.length === 1 ? ' parent' : ' parents') : '',
            }) +
            parentsHtml +
            '</section>' +
            '<section class="research-entity__section" aria-labelledby="prks-concept-children-h">' +
            researchSectionHeadHtml('Subconcepts', { headingId: 'prks-concept-children-h' }) +
            childrenHtml +
            '</section>' +
            '<section class="research-entity__section" aria-labelledby="prks-concept-mentions-h">' +
            researchSectionHeadHtml('Mentioned in research notes', {
                headingId: 'prks-concept-mentions-h',
                count: Number(c.mention_count) || 0,
            }) +
            mentionsHtml +
            '</section>' +
            '</div>';
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
                if (conceptMutationBlocked('Editing a Concept requires a connection to PRKS.')) return;
                const next = await promptText({
                    title: 'Definition',
                    message: 'Markdown',
                    defaultValue: c.description || '',
                    multiline: true,
                    okLabel: 'Save',
                });
                if (next == null) return;
                if (!ownsConcept()) return;
                // Connectivity may have dropped while the dialog was open.
                if (conceptMutationBlocked('Editing a Concept requires a connection to PRKS.')) return;
                await root.updateConcept(c.id, { description: next });
                refreshConcept();
            })();
        });
        container.querySelector('#prks-concept-edit-aliases').addEventListener('click', function () {
            void (async function () {
                if (conceptMutationBlocked('Editing a Concept requires a connection to PRKS.')) return;
                const next = await promptText({
                    title: 'Search keys / aliases',
                    message: 'One alias per line',
                    defaultValue: (c.aliases || []).join('\n'),
                    multiline: true,
                    okLabel: 'Save',
                });
                if (next == null) return;
                if (!ownsConcept()) return;
                if (conceptMutationBlocked('Editing a Concept requires a connection to PRKS.')) return;
                const aliases = next.split(/\n/).map(function (s) { return s.trim(); }).filter(Boolean);
                await root.putConceptAliases(c.id, aliases);
                refreshConcept();
            })();
        });
        container.querySelector('#prks-concept-edit-parents').addEventListener('click', function () {
            void (async function () {
                if (conceptMutationBlocked('Editing a Concept requires a connection to PRKS.')) return;
                const next = await promptText({
                    title: 'Parent concepts',
                    message: 'Parent Concept IDs, comma-separated',
                    defaultValue: (c.parents || []).map(function (p) { return p.id; }).join(', '),
                    okLabel: 'Save',
                });
                if (next == null) return;
                if (!ownsConcept()) return;
                if (conceptMutationBlocked('Editing a Concept requires a connection to PRKS.')) return;
                const ids = next.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
                await root.putConceptParents(c.id, ids);
                refreshConcept();
            })();
        });
        bindConceptOfflineState(ctx, container);
        if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(container);
    }

    async function renameConcept(ctx, generation, c) {
        if (conceptMutationBlocked('Renaming a Concept requires a connection to PRKS.')) return;
        const next = await promptText({
            title: 'Rename Concept',
            defaultValue: c.name || '',
            okLabel: 'Save',
        });
        if (next == null || !String(next).trim()) return;
        if (!ctx || !ctx.isCurrent || !ctx.isCurrent(generation)) return;
        // Re-check: PRKS may have become unreachable while the dialog was open.
        if (conceptMutationBlocked('Renaming a Concept requires a connection to PRKS.')) return;
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
        if (conceptMutationBlocked('Deleting a Concept requires a connection to PRKS.')) return;
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
        // Re-check: PRKS may have become unreachable while the confirm was open.
        if (conceptMutationBlocked('Deleting a Concept requires a connection to PRKS.')) return;
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
        renderConceptsIndexUnavailable: renderConceptsIndexUnavailable,
        renderConceptDetail: renderConceptDetail,
        renderConceptNotFound: renderConceptNotFound,
        prksBindConceptOfflineState: bindConceptOfflineState,
        prksApplyConceptOfflineState: applyConceptOfflineState,
        prksCreateConceptFlow: createConceptFlow,
        prksResearchMarkdownHtml: md,
        prksResearchIndexRowHtml: researchIndexRowHtml,
        prksNormalizeSearchQuery: normalizeSearchQuery,
        prksBindResearchIndexSearch: bindResearchIndexSearch,
        prksResearchIndexToolbarHtml: researchIndexToolbarHtml,
        prksResearchIndexSearchEmptyHtml: researchIndexSearchEmptyHtml,
        prksResearchSectionHeadHtml: researchSectionHeadHtml,
    };
    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : typeof global !== 'undefined' ? global : this);
