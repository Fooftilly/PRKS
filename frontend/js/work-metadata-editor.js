/* Bibliographic metadata UI: one Save for the synchronized group, and a
 * conflict that belongs to a FIELD rather than to the whole form.
 *
 * If only DOI conflicts, only DOI is unusable. Turning the entire editor into
 * one CONFLICT state would be telling the user that edits which never collided
 * with anything need resolving.
 */
(function (root) {
    'use strict';
    const unavailable = 'Bibliographic details not available offline for this Work yet. Connect once and open Edit metadata to prepare them.';

    function live(ctx, state) {
        return ctx && !ctx.destroyed && ctx.generation === state.generation &&
            ctx.getEntity('work') && ctx.getEntity('work').id === state.workId;
    }
    function owns(ctx, state) {
        return live(ctx, state) && root.prksRightPanelOwnedBy(ctx) && root.prksOwnerTabIsFocused(ctx);
    }
    function panelOf() { return document.getElementById('panel-content'); }

    function statusText(ops) {
        if (ops.some(o => o.status === 'conflict')) return 'Some fields need your decision below.';
        if (ops.some(o => o.status === 'syncing')) return 'Syncing…';
        if (ops.some(o => o.last_error)) return 'Sync failed · retry scheduled';
        if (ops.length) return root.prksOfflineRuntimeState() === 'online' ? 'Waiting to sync' : 'Offline · saved locally';
        return 'All changes synced';
    }
    function busy(state, field) {
        return state.operations.some(o => o.payload.field === field &&
            (o.status !== 'pending' || o.attempt_count > 0));
    }

    /** Acknowledged Work + durable pending edits, for the read-only card. */
    async function repaintDisplay(ctx, state) {
        const host = document.querySelector('[data-prks-role="work-bib-rows"]');
        if (!host || !owns(ctx, state)) return;
        const work = ctx.getEntity('work');
        const effective = root.prksEffectiveWorkMetadata(work, state.operations);
        const rows = root.prksWorkBibRowsHtml(effective);
        if (host.innerHTML !== rows) host.innerHTML = rows;
        const empty = document.querySelector('[data-prks-role="work-meta-empty"]');
        if (empty) empty.hidden = !!host.textContent.trim();
    }

    async function paint(ctx, state) {
        const paintVersion = state.paintVersion = (state.paintVersion || 0) + 1;
        // One read serves both this editor and the synchronous overlay other
        // surfaces consult (the leave guard, the form's initial values, the
        // Recently Added filter), so nothing re-reads the queue per consumer --
        // and this is the read that hydrates that overlay in the first place.
        const rows = await root.prksRefreshPendingWorkMetadata();
        if (!live(ctx, state) || paintVersion !== state.paintVersion) return;
        state.operations = root.prksWorkMetadataFieldOperations(rows, state.workId);
        if (!owns(ctx, state)) return;
        await repaintDisplay(ctx, state);
        const panel = panelOf();
        const section = panel && panel.querySelector('[data-prks-role="work-bib-editor"]');
        if (!section) return;

        const editable = !!state.observed;
        section.querySelectorAll('[data-prks-work-field]').forEach(input => {
            const blocked = !editable || busy(state, input.dataset.prksWorkField);
            input.disabled = blocked;
            input.title = blocked
                ? (editable ? 'This field is syncing or needs resolution.' : unavailable)
                : '';
        });
        const save = section.querySelector('#save-work-bib-btn');
        if (save) save.disabled = !editable;

        const status = section.querySelector('[data-prks-role="work-bib-sync"]');
        if (!status) return;
        status.replaceChildren();
        status.appendChild(document.createTextNode(
            state.error || (editable ? statusText(state.operations) : unavailable)));

        for (const op of state.operations.filter(o => o.status === 'conflict')) {
            status.appendChild(conflictRow(ctx, state, op));
        }
        // Everything above the synchronized group still saves over HTTP.
        const note = panel.querySelector('[data-prks-role="work-meta-online-only"]');
        const offline = root.prksOfflineRuntimeState() !== 'online';
        if (note) note.hidden = !offline;
        const onlineSave = panel.querySelector('#inline-save-metadata-btn');
        if (onlineSave) {
            onlineSave.disabled = offline;
            onlineSave.title = offline ? 'These fields require a connection to PRKS.' : '';
        }
    }

    /* Painting must never become an unhandled rejection. The durable store
     * REJECTS when IndexedDB is unavailable -- that is its whole contract --
     * and a browser with storage blocked has to keep working online exactly as
     * it did before, not surface a page error from a background repaint. */
    function safePaint(ctx, state) {
        return paint(ctx, state).catch(() => {});
    }

    function conflictRow(ctx, state, op) {
        const result = op.server_result || {};
        const field = op.payload.field;
        const label = root.PRKS_SYNCED_WORK_FIELD_LABELS[field] || field;
        const item = document.createElement('div');
        item.dataset.prksWorkFieldConflict = field;
        if (result.code === 'REVISION_CONFLICT' || result.code === 'FUTURE_REVISION') {
            item.appendChild(document.createTextNode(
                label + ' — this device: "' + op.payload.value + '". Server: "' +
                (result.current_value || '') + '". '));
        } else if (result.code === 'ENTITY_NOT_FOUND') {
            item.appendChild(document.createTextNode('This Work no longer exists on the server. '));
        } else {
            item.appendChild(document.createTextNode(
                label + ' could not synchronize (' + (result.code || 'protocol error') + '). '));
        }
        const reappliable = result.code === 'REVISION_CONFLICT' && Number.isSafeInteger(result.current_revision);
        action(item, ctx, state, op, reappliable ? 'Use server' : 'Discard my value', false);
        if (reappliable) action(item, ctx, state, op, 'Apply my value', true);
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
                const result = op.server_result || {};
                if (!apply && typeof result.current_value === 'string') {
                    // Taking the server value is itself an acknowledged state:
                    // reconcile it so the cache and the form agree immediately.
                    const ack = { work_id: state.workId, field: op.payload.field,
                        value: result.current_value, server_revision: result.current_revision,
                        changed: false, code: 'ACKNOWLEDGED' };
                    if (!await root.prksOfflineReconcileWorkField(ack)) throw new Error();
                    acceptAck(ctx, state, ack);
                } else if (!apply) {
                    // No server value to fall back on; drop the local intent and
                    // let the next authoritative read re-establish the truth.
                    root.prksOfflineMarkEntityChanged('work', state.workId);
                    root.prksOfflineMarkEntityChanged('work-metadata-state', state.workId);
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

    /* An acknowledgement moves both the cached Work and the observed base this
     * editor measures its next save against. */
    function acceptAck(ctx, state, ack) {
        if (!live(ctx, state) || ack.work_id !== state.workId) return;
        state.readVersion = (state.readVersion || 0) + 1;
        const entry = state.observed && state.observed.fields[ack.field];
        if (entry && entry.revision > ack.server_revision) return;
        const work = ctx.getEntity('work');
        ctx.setEntity('work', Object.assign({}, work, { [ack.field]: ack.value }));
        if (entry) { entry.value = ack.value; entry.revision = ack.server_revision; }
        const input = document.querySelector('[data-prks-work-field="' + ack.field + '"]');
        if (input && !input.disabled && document.activeElement !== input) input.value = ack.value;
    }

    async function prepare(ctx, state) {
        const readVersion = state.readVersion = (state.readVersion || 0) + 1;
        try {
            const result = await root.prksReadWorkMetadataState(state.workId);
            if (!live(ctx, state) || readVersion !== state.readVersion) return;
            state.observed = result.value;
        } catch (_) { state.observed = null; }
        await safePaint(ctx, state);
    }

    /**
     * Mounted for every Work detail render, not only while editing: the
     * read-only card has to show pending values too, and after a reload that
     * overlay can only come from the durable queue. The observed base is
     * fetched only when the editor is actually open -- viewing a Work must not
     * cost a request for synchronization bookkeeping nothing on screen needs.
     */
    function mount(ctx, workId, options) {
        if (!ctx || !root.prksSync) return;
        let state = ctx.getResource('workMetadataEditor');
        if (!state || state.workId !== workId || state.generation !== ctx.generation) {
            state = { workId, generation: ctx.generation, operations: [], observed: null, error: null };
            const stopSync = root.prksSync.subscribe(event => {
                if (event.acknowledged && event.operation === 'SET_WORK_METADATA_FIELD') {
                    acceptAck(ctx, state, event.acknowledged);
                }
                void safePaint(ctx, state);
            });
            const stopConnectivity = root.prksOfflineRuntimeSubscribe(() => {
                void safePaint(ctx, state);
            });
            ctx.setResource('workMetadataEditor', state, () => { stopSync(); stopConnectivity(); });
        }
        void safePaint(ctx, state);
        if (options && options.editing) state.preparing = prepare(ctx, state);
    }

    /**
     * One Save, one transaction, however many fields it touched. Only fields
     * whose value actually differs from the observed server state become
     * operations -- the form submits all nine every time, and nine
     * operations per Save would be nine chances to conflict over nothing.
     */
    async function save(workId) {
        const ctx = root.prksGetFocusedTabContext ? root.prksGetFocusedTabContext() : null;
        const state = ctx && ctx.getResource('workMetadataEditor');
        if (!state || !live(ctx, state) || state.workId !== workId) return;
        const button = document.getElementById('save-work-bib-btn');
        if (button && root.prksSetButtonBusy) root.prksSetButtonBusy(button, true, { busyLabel: 'Saving…' });
        try {
            if (state.preparing) await state.preparing;
            if (!state.observed) throw new Error('no observed base');
            const draft = {};
            document.querySelectorAll('[data-prks-work-field]').forEach(input => {
                draft[input.dataset.prksWorkField] = input.value;
            });
            const changes = root.prksDirtyWorkMetadataFields(draft, state.observed, state.operations);
            await root.prksSync.store.saveWorkMetadataFields(workId, changes, state.observed.fields);
            state.error = null;
            await safePaint(ctx, state);
            root.prksSync.changed();
        } catch (error) {
            state.error = error && error.prksLocalStoreCode === 'scope_busy'
                ? 'One of these fields is still syncing or needs a decision below.'
                : 'Could not save these details locally. Please retry.';
            await safePaint(ctx, state);
        } finally {
            if (button && root.prksSetButtonBusy) root.prksSetButtonBusy(button, false);
        }
    }

    root.prksMountWorkMetadataEditor = mount;
    root.prksSaveWorkMetadataFields = save;
})(typeof window === 'undefined' ? globalThis : window);
