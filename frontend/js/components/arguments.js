/**
 * Arguments & Stances index and detail render in Vue
 * (`frontend-app/src/features/arguments/`).
 *
 * Effective rows, pending overlays, and durable writes stay in the route
 * coordinator and argument-state.js. This file keeps
 * `prksCreateArgumentFromWork` for Work Research Notes and offline/durable
 * callers. It does not own a second Argument surface.
 */
(function (root) {
    'use strict';

    async function promptArgumentName(kindName, title) {
        const isStance = kindName === 'stance';
        if (typeof root.prksPromptTextDialog !== 'function') return null;
        const name = await root.prksPromptTextDialog({
            title: title || (isStance ? 'New Stance' : 'New Argument'),
            okLabel: 'Create',
        });
        if (name == null || !String(name).trim()) return null;
        return String(name).trim();
    }

    async function createArgumentFromWork(options) {
        const opts = options || {};
        const kind = opts.kind === 'stance' ? 'stance' : 'argument';
        const provided = opts.name != null ? String(opts.name).trim() : '';
        const name = provided || (await promptArgumentName(kind));
        if (!name) return null;
        const payload = {
            name: String(name).trim(),
            kind: kind,
            main_text: '',
            sources: [],
        };
        if (opts.workId) {
            payload.sources.push({
                work_id: opts.workId,
                pages: opts.pages || '',
            });
        }
        try {
            return await root.createArgument(payload);
        } catch (err) {
            if (typeof root.prksAlertDialog === 'function') {
                await root.prksAlertDialog({
                    title: 'Could not create',
                    message: (err && err.message) || '',
                });
            }
            return null;
        }
    }

    const api = {
        prksCreateArgumentFromWork: createArgumentFromWork,
    };
    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : typeof global !== 'undefined' ? global : this);
