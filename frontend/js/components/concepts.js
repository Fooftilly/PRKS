/**
 * Shared research-index helpers + Concept create flow.
 *
 * Concept index/detail rendering lives in the Vue Concepts feature
 * (`frontend-app/src/features/concepts/`). Sibling research indexes still reuse
 * the research-index / research-markdown helpers defined here.
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

    /**
     * @param {string} [initialName]
     * @param {{ tabId?: string, generation?: number, isCurrent?: (g: number) => boolean }} [ownerOpts]
     *        Optional owning-tab context from the Vue Concepts intent. Callers that omit
     *        it keep the prior focused-tab navigation behavior (notes markup, etc.).
     */
    async function createConceptFlow(initialName, ownerOpts) {
        /* No connectivity guard: a Concept is created under an id this device
         * mints, so it is real the moment it is written. Also reachable from
         * the Work Research Notes markup flow. */
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
                const opts = ownerOpts || {};
                const gen = opts.generation;
                const stillCurrent =
                    typeof opts.isCurrent !== 'function' ||
                    (typeof gen === 'number' && opts.isCurrent(gen));
                if (stillCurrent) {
                    const navOpts = {};
                    if (opts.tabId != null && opts.tabId !== '') navOpts.tabId = opts.tabId;
                    root.prksNavigate('#/concepts/' + encodeURIComponent(created.id), navOpts);
                }
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

    const api = {
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
