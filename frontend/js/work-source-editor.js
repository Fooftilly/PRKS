/**
 * The Video source editor: one bounded save for a whole identity.
 *
 * Deliberately separate from the field editor. A source change is not a
 * scalar edit -- one decision rewrites `source_kind`, `provider`,
 * `provider_id` and `source_url` together -- so it has its own operation, its
 * own revision scope and its own conflict.
 */
(function (root) {
    'use strict';

    const unavailable = 'Video source not available offline for this file yet. Connect once and open Edit metadata to prepare it.';

    function panelOf() { return document.getElementById('panel-content'); }
    function sectionOf() {
        const panel = panelOf();
        return panel && panel.querySelector('[data-prks-role="work-source-editor"]');
    }

    function live(ctx, state) {
        return ctx && !ctx.destroyed && ctx.generation === state.generation &&
            ctx.getEntity('work') && ctx.getEntity('work').id === state.workId;
    }
    function owns(ctx, state) {
        return live(ctx, state) && root.prksRightPanelOwnedBy(ctx) && root.prksOwnerTabIsFocused(ctx);
    }

    function statusText(ops) {
        if (ops.some(o => o.status === 'conflict')) return 'This needs your decision below.';
        if (ops.some(o => o.status === 'syncing')) return 'Syncing…';
        if (ops.some(o => o.last_error)) return 'Sync failed · retry scheduled';
        if (ops.length) {
            return root.prksOfflineRuntimeState() === 'online'
                ? 'Waiting to sync' : 'Offline · saved locally';
        }
        return 'All changes synced';
    }

    async function paint(ctx, state) {
        const paintVersion = state.paintVersion = (state.paintVersion || 0) + 1;
        const rows = await root.prksRefreshPendingWorkSources();
        if (!live(ctx, state) || paintVersion !== state.paintVersion) return;
        state.operations = root.prksWorkSourceOperations(rows, state.workId);
        if (!owns(ctx, state)) return;
        const section = sectionOf();
        if (!section) return;

        const editable = state.observed !== null && state.observed !== undefined;
        const busy = state.operations.some(o => o.status !== 'pending' || o.attempt_count > 0);
        const input = section.querySelector('#meta-video-url');
        if (input) {
            input.disabled = !editable || busy;
            input.title = input.disabled
                ? (editable ? 'This source is syncing or needs resolution.' : unavailable) : '';
        }
        const save = section.querySelector('#save-work-source-btn');
        if (save) save.disabled = !editable || busy;

        const status = section.querySelector('[data-prks-role="work-source-sync"]');
        if (!status) return;
        status.replaceChildren();
        status.appendChild(document.createTextNode(
            state.error || (editable ? statusText(state.operations) : unavailable)));
        for (const op of state.operations.filter(o => o.status === 'conflict')) {
            status.appendChild(conflictRow(ctx, state, op));
        }
    }

    function safePaint(ctx, state) {
        return paint(ctx, state).catch(() => {});
    }

    function preview(text) {
        const points = Array.from(String(text == null ? '' : text));
        return points.length <= 160 ? points.join('') : points.slice(0, 160).join('') + '…';
    }

    function conflictRow(ctx, state, op) {
        const result = op.server_result || {};
        const item = document.createElement('div');
        item.dataset.prksWorkSourceConflict = state.workId;
        if (result.code === 'SOURCE_REVISION_CONFLICT' || result.code === 'FUTURE_REVISION') {
            /* ONE question: which video should this file be? Never separate
             * questions about `provider` and `provider_id` -- nobody chose
             * those, they were derived from this very URL. */
            item.appendChild(document.createTextNode(
                'This file’s video differs. This device: "' +
                preview(op.payload.source.url) + '". Server: "' +
                preview(result.current_preview) + '". '));
        } else if (result.code === 'ENTITY_NOT_FOUND') {
            item.appendChild(document.createTextNode('This file no longer exists on the server. '));
        } else if (result.code === 'UNSUPPORTED_SOURCE_TRANSITION') {
            item.appendChild(document.createTextNode(
                'This file is no longer a video on the server, so its video source cannot be changed. '));
        } else {
            item.appendChild(document.createTextNode(
                'The video source could not synchronize (' + (result.code || 'protocol error') + '). '));
        }
        const reappliable = result.code === 'SOURCE_REVISION_CONFLICT' &&
            Number.isSafeInteger(result.current_revision);
        action(item, ctx, state, op, reappliable ? 'Use server' : 'Discard my source', false);
        if (reappliable) action(item, ctx, state, op, 'Apply my source', true);
        return item;
    }

    function action(item, ctx, state, op, label, apply) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'prks-btn prks-btn--secondary prks-btn--sm';
        button.textContent = label;
        button.onclick = async () => {
            button.disabled = true;
            try {
                if (!apply) {
                    /* Only a bounded preview came back, so there is no
                     * authoritative source to write: drop the local intent and
                     * let the next read re-establish what the server holds. */
                    root.prksOfflineMarkEntityChanged('work', state.workId);
                    state.observed = null;
                }
                await root.prksSync.store.resolveConflict(op.op_id, apply);
                state.error = null;
                root.prksSync.changed();
            } catch (_) {
                state.error = 'Could not save that resolution locally. Please retry.';
            }
            await safePaint(ctx, state);
        };
        item.appendChild(button);
    }

    async function prepare(ctx, state) {
        const readVersion = state.readVersion = (state.readVersion || 0) + 1;
        try {
            const result = await root.prksOfflineReadEntity('work-source-state', state.workId,
                '/api/works/' + encodeURIComponent(state.workId) + '/source-state', {
                    validate: value => !!value && typeof value === 'object' &&
                        value.work_id === state.workId &&
                        Number.isSafeInteger(value.revision) && value.revision >= 0,
                });
            if (!live(ctx, state) || readVersion !== state.readVersion) return;
            /* "Not read" is not "revision 0". Saving against a base this
             * session could not establish would overwrite a decision it never
             * saw, so the control stays disabled instead. */
            state.observed = result && result.source !== 'unavailable' && result.value
                ? result.value.revision : null;
        } catch (_) { state.observed = null; }
        await safePaint(ctx, state);
    }

    function mount(ctx, workId, options) {
        if (!ctx || !root.prksSync) return;
        let state = ctx.getResource('workSourceEditor');
        if (!state || state.workId !== workId || state.generation !== ctx.generation) {
            state = { workId, generation: ctx.generation, operations: [], observed: undefined,
                error: null };
            const stopSync = root.prksSync.subscribe(event => {
                if (event && event.operation && event.operation !== 'SET_WORK_SOURCE') return;
                void safePaint(ctx, state);
            });
            const stopConnectivity = root.prksOfflineRuntimeSubscribe(() => {
                void safePaint(ctx, state);
            });
            ctx.setResource('workSourceEditor', state, () => { stopSync(); stopConnectivity(); });
        }
        void safePaint(ctx, state);
        if (options && options.editing) state.preparing = prepare(ctx, state);
    }

    /** One Save: a whole identity, or a visible refusal. */
    async function save(workId) {
        const ctx = root.prksGetFocusedTabContext ? root.prksGetFocusedTabContext() : null;
        const state = ctx && ctx.getResource('workSourceEditor');
        if (!state || !live(ctx, state) || state.workId !== workId) return;
        const section = sectionOf();
        if (!section) return;
        const input = section.querySelector('#meta-video-url');
        const error = section.querySelector('#meta-video-url-error');
        const button = section.querySelector('#save-work-source-btn');
        if (button && root.prksSetButtonBusy) {
            root.prksSetButtonBusy(button, true, { busyLabel: 'Saving…' });
        }
        try {
            if (state.preparing) await state.preparing;
            if (state.observed === null || state.observed === undefined) {
                throw new Error('no observed base');
            }
            if (error) error.textContent = '';
            if (input) input.removeAttribute('aria-invalid');
            const source = root.prksCanonicalWorkSource(input ? input.value : '');
            if (!source) {
                /* Refused, never guessed at: an unreadable URL is not an
                 * instruction to clear the video. */
                if (input) { input.setAttribute('aria-invalid', 'true'); input.focus(); }
                if (error) error.textContent = 'Use a YouTube link, for example https://www.youtube.com/watch?v=…';
                return;
            }
            const work = ctx.getEntity('work');
            const current = root.prksWorkSourceOf(root.prksEffectiveWorkSource(work));
            if (root.prksWorkSourceIdentity(current) === root.prksWorkSourceIdentity(source)) {
                // The same video, however it is spelled. Nothing to record.
                state.error = null;
                await safePaint(ctx, state);
                return;
            }
            await root.prksSync.store.enqueueOperation({
                operation: 'SET_WORK_SOURCE', entity_type: 'work', entity_id: workId,
                payload: { source: { kind: 'video', url: source.source_url } },
                base_revision: state.observed,
            });
            state.error = null;
            await safePaint(ctx, state);
            root.prksSync.changed();
        } catch (error_) {
            state.error = error_ && error_.prksLocalStoreCode === 'scope_busy'
                ? 'This source is still syncing or needs a decision below.'
                : 'Could not save the video source locally. Please retry.';
            await safePaint(ctx, state);
        } finally {
            if (button && root.prksSetButtonBusy) root.prksSetButtonBusy(button, false);
        }
    }

    root.prksMountWorkSourceEditor = mount;
    root.prksSaveWorkSource = save;
})(typeof window === 'undefined' ? globalThis : window);
