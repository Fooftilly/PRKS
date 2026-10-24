/**
 * Research-note markdown helper + Concept create flow.
 *
 * Concept/Position/Argument index and detail presentation lives in Vue
 * (`frontend-app/src/features/{concepts,positions,arguments}/` plus
 * `PrksResearchRow` / `PrksResearchSectionHead`). Sibling notes markup still
 * uses `prksResearchMarkdownHtml` and `prksCreateConceptFlow`.
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
        const opts = ownerOpts || {};
        const gen = opts.generation;
        /* When the Vue Concepts owner supplied a generation guard, ignore the
         * rest of the flow after the prompt if that owner is no longer current
         * (route replaced / pane dismissed) — do not create or navigate. */
        if (
            typeof opts.isCurrent === 'function' &&
            !(typeof gen === 'number' && opts.isCurrent(gen))
        ) {
            return null;
        }
        try {
            const created = await root.createConcept({ name: String(name).trim() });
            if (created && created.id && typeof root.prksNavigate === 'function') {
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
            const stillCurrent =
                typeof opts.isCurrent !== 'function' ||
                (typeof gen === 'number' && opts.isCurrent(gen));
            if (stillCurrent && typeof root.prksAlertDialog === 'function') {
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
    };
    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : typeof global !== 'undefined' ? global : this);
