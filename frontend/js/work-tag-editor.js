/* Work Tags UI. The TabContext owns the observed base and editor lifetime. */
(function (root) {
    'use strict';
    const unavailable = 'Tag editing not available offline for this Work yet. Connect once and open the Tags panel to prepare it.';
    function live(ctx, state) {
        return ctx && !ctx.destroyed && ctx.generation === state.generation &&
            ctx.getEntity('work') && ctx.getEntity('work').id === state.workId;
    }
    function owns(ctx, state) { return live(ctx, state) && root.prksRightPanelOwnedBy(ctx) && root.prksOwnerTabIsFocused(ctx); }
    function statusText(ops) {
        if (ops.some(o => o.status === 'conflict')) return 'Conflict';
        if (ops.some(o => o.status === 'syncing')) return 'Syncing…';
        if (ops.some(o => o.last_error)) return 'Sync failed · retry scheduled';
        if (ops.length) return root.prksOfflineRuntimeState() === 'online' ? 'Waiting to sync' : 'Offline · saved locally';
        return 'All changes synced';
    }
    function blocked(state, tagId) {
        return state.operations.some(o => o.payload.tag_id === tagId && (o.status !== 'pending' || o.attempt_count > 0));
    }
    async function paint(ctx, state) {
        const paintVersion = state.paintVersion = (state.paintVersion || 0) + 1;
        const rows = await root.prksSync.store.listOperations();
        if (!live(ctx, state) || paintVersion !== state.paintVersion) return;
        state.operations = rows.filter(o => o.entity_type === 'work' && o.entity_id === state.workId &&
            ['ADD_WORK_TAG', 'REMOVE_WORK_TAG'].includes(o.operation) && o.status !== 'acknowledged');
        if (!owns(ctx, state)) return;
        const panel = document.getElementById('panel-content');
        const list = panel.querySelector('#work-tags-list');
        if (!list) return;
        const work = ctx.getEntity('work');
        const effective = { ...work, tags: root.prksEffectiveWorkTags(work, state.operations) };
        const editable = ctx.ui.workDetailsMode === 'tags';
        // Identical markup is not a repaint. Rewriting innerHTML needlessly
        // churns the DOM other tiles may be reading, and the first paint now
        // produces exactly what the panel was rendered with.
        const chips = root.renderWorkTagsChips(effective, { editable });
        if (list.innerHTML !== chips) list.innerHTML = chips;
        list.querySelectorAll('[data-tag-id]').forEach(button => {
            button.disabled = !state.options || blocked(state, button.dataset.tagId);
            if (button.disabled) button.title = state.options ? 'This change is syncing or needs resolution.' : unavailable;
        });
        let status = panel.querySelector('[data-work-tag-sync]');
        if (!status) {
            status = document.createElement('div'); status.dataset.workTagSync = '';
            status.className = 'meta-row'; status.setAttribute('aria-live', 'polite');
            list.after(status);
        }
        status.textContent = state.error || (editable && !state.options ? unavailable : statusText(state.operations));
        for (const op of state.operations.filter(o => o.status === 'conflict')) {
            const result = op.server_result || {};
            const name = op.local_context && op.local_context.tag ? op.local_context.tag.name : 'Tag';
            const item = document.createElement('div');
            const intent = (op.operation === 'ADD_WORK_TAG' ? 'Add ' : 'Remove ') + name;
            let message;
            if (result.code === 'REVISION_CONFLICT') message = 'Local: ' + intent + '. Server: currently ' + (result.current_state ? 'present.' : 'absent.');
            else if (result.code === 'TAG_MERGED') {
                const target = (state.catalog || []).find(t => t.id === result.target_tag_id);
                message = 'Tag ' + name + ' was merged into ' + (target ? target.name : result.target_tag_id) + '.';
            } else if (result.code === 'TAG_DELETED') message = 'Tag ' + name + ' was deleted on the server.';
            else if (result.code === 'ENTITY_NOT_FOUND') message = 'This Work or Tag no longer exists on the server.';
            else message = 'This change could not synchronize (' + (result.code || 'protocol error') + ').';
            item.textContent = message + ' ';
            function action(label, apply) {
                const button = document.createElement('button'); button.type = 'button';
                button.className = 'prks-btn prks-btn--secondary prks-btn--sm'; button.textContent = label;
                button.onclick = async () => {
                    button.disabled = true;
                    try {
                        if (!apply && result.code === 'REVISION_CONFLICT') {
                            const ack = { work_id: state.workId, tag_id: op.payload.tag_id,
                                present: result.current_state, server_revision: result.current_revision,
                                tag: op.local_context.tag };
                            if (!await root.prksOfflineReconcileWorkTag(ack)) throw new Error();
                            acceptAck(ctx, state, ack);
                        } else if (!apply) {
                            // Explicitly discard the intent and stale relationship.
                            // Catalog/lifecycle conflicts do not fabricate a target.
                            const work = ctx.getEntity('work');
                            root.prksOfflineMarkEntityChanged('work', state.workId);
                            root.prksOfflineMarkEntityChanged('work-tag-options', state.workId);
                            ctx.setEntity('work', { ...work, tags: work.tags.filter(t => t.id !== op.payload.tag_id) });
                            state.options = null;
                        }
                        await root.prksSync.store.resolveConflict(op.op_id, apply);
                        state.error = null; root.prksSync.changed();
                    } catch (_) { state.error = 'Could not save the resolution locally. Please retry.'; }
                    await paint(ctx, state);
                };
                item.appendChild(button);
            }
            action(result.code === 'REVISION_CONFLICT' ? 'Use server state' : 'Discard local change', false);
            if (result.code === 'REVISION_CONFLICT') action('Apply my change', true);
            status.appendChild(item);
        }
        const input = panel.querySelector('#work-tag-search');
        if (input) {
            input.disabled = !state.options || !state.catalog;
            input.placeholder = input.disabled ? 'Tags unavailable' : 'Search tags or add…';
        }
    }
    function acceptAck(ctx, state, ack) {
        if (!live(ctx, state) || ack.work_id !== state.workId) return;
        state.readVersion = (state.readVersion || 0) + 1;
        if (state.options && root.prksWorkTagBase(state.options, ack.tag_id).revision > ack.server_revision) return;
        const work = ctx.getEntity('work');
        const tags = work.tags.filter(t => t.id !== ack.tag_id);
        if (ack.present) tags.push(ack.tag);
        ctx.setEntity('work', { ...work, tags });
        if (state.options) {
            state.options.assigned = state.options.assigned.filter(t => t.tag_id !== ack.tag_id);
            delete state.options.known_absent[ack.tag_id];
            if (ack.present) state.options.assigned.push({ tag_id: ack.tag_id, relation_revision: ack.server_revision });
            else if (ack.server_revision) state.options.known_absent[ack.tag_id] = ack.server_revision;
        }
    }
    async function prepare(ctx, state) {
        const readVersion = state.readVersion = (state.readVersion || 0) + 1;
        try {
            const [options, catalog] = await Promise.allSettled([
                root.prksReadWorkTagOptions(state.workId), root.prksReadTagsIndex(),
            ]);
            if (!live(ctx, state) || readVersion !== state.readVersion) return;
            state.options = options.status === 'fulfilled' ? options.value.value : null;
            state.catalog = catalog.status === 'fulfilled' ? catalog.value.value : null;
            state.catalogGeneration = root.prksOfflineDomainGeneration('tags');
        } catch (_) { state.error = unavailable; }
        await paint(ctx, state);
    }
    /** Every unsynchronized operation, or an empty list. */
    async function vocabularyOperations() {
        try {
            if (root.prksSync && root.prksSync.store) {
                return await root.prksSync.store.listOperations();
            }
        } catch (_e) { /* an unreadable store overlays nothing */ }
        return [];
    }

    function bindPicker(ctx, state) {
        const input = document.getElementById('work-tag-search');
        const results = document.getElementById('work-tag-search-results');
        if (!input || !results) return;
        async function dropdown() {
            if (!owns(ctx, state) || input.disabled) return;
            if (state.catalogGeneration !== root.prksOfflineDomainGeneration('tags')) await prepare(ctx, state);
            if (!owns(ctx, state) || !state.catalog) return;
            /* The vocabulary overlay is applied HERE rather than trusted from
             * the last prepare. A Tag created or deleted on this device changes
             * nothing canonical, so the tags domain generation does not move --
             * and a picker that still offered a Tag with a pending deletion
             * would only produce an operation the server refuses. */
            const catalog = typeof root.prksEffectiveTagCatalogue === 'function'
                ? root.prksEffectiveTagCatalogue(state.catalog, await vocabularyOperations())
                : state.catalog;
            if (!owns(ctx, state)) return;
            const value = input.value.trim(); const query = value.toLowerCase();
            const tags = root.prksEffectiveTagChips
                ? root.prksEffectiveTagChips(
                    root.prksEffectiveWorkTags(ctx.getEntity('work'), state.operations),
                    await vocabularyOperations())
                : root.prksEffectiveWorkTags(ctx.getEntity('work'), state.operations);
            const assigned = new Set(tags.map(t => t.id));
            results.innerHTML = '';
            const available = catalog.filter(t => !assigned.has(t.id));
            function item(text, action, disabled, modifier) {
                const div = document.createElement('div');
                div.className = 'result-item' + (modifier ? ' ' + modifier : ''); div.textContent = text;
                if (disabled) div.setAttribute('aria-disabled', 'true');
                else div.onmousedown = ev => { ev.preventDefault(); void action(); };
                results.appendChild(div);
            }
            if (value && !catalog.some(t => root.prksTagExactMatch(t, query))) {
                /* The shared create affordance every other PRKS combobox uses,
                 * with or without a server: the Tag's id is minted on this
                 * device, so it is a real Tag the moment it is written, and the
                 * attachment that follows is ordered behind its creation. */
                item('Create tag "' + value + '"',
                    () => root.prksSubmitNewTag('work', state.workId, value, ctx, input),
                    false, 'result-item--create');
            }
            available.filter(t => root.prksTagMatchesQuery(t, query)).slice(0, 40).forEach(tag => {
                item(root.prksTagComboboxLabel(tag, query), () => edit(ctx, tag.id, true), blocked(state, tag.id));
            });
            if (results.childElementCount) root.prksShowInlineComboboxResults(input, results);
            else root.prksHideInlineComboboxResults(results);
        }
        input.onfocus = () => void dropdown(); input.oninput = () => void dropdown();
        input.onblur = () => setTimeout(() => root.prksHideInlineComboboxResults(results), 200);
    }
    function mount(ctx, workId) {
        if (!ctx || !root.prksSync) return;
        let state = ctx.getResource('workTagEditor');
        if (!state || state.workId !== workId || state.generation !== ctx.generation) {
            state = { workId, generation: ctx.generation, operations: [], options: null, catalog: null, error: null };
            const unsubscribe = root.prksSync.subscribe(event => {
                /* Only THIS family's acknowledgements. The durable queue is
                 * shared, and another family's ACK carries no tag_id -- feeding
                 * it to acceptAck() bumps readVersion, which silently cancels
                 * an in-flight prepare() and leaves the picker disabled with no
                 * catalog and nothing to retry it. */
                if (event.acknowledged && ['ADD_WORK_TAG', 'REMOVE_WORK_TAG'].includes(event.operation)) {
                    acceptAck(ctx, state, event.acknowledged);
                }
                void paint(ctx, state).catch(() => {});
            });
            ctx.setResource('workTagEditor', state, unsubscribe);
        }
        void paint(ctx, state).catch(() => {});
        if (ctx.ui.workDetailsMode === 'tags') {
            bindPicker(ctx, state);
            state.preparing = prepare(ctx, state);
        }
    }
    /**
     * `knownTag` is a Tag this device has just CREATED and not yet sent.
     *
     * It is in no catalogue the server could have answered with -- its id was
     * minted here a moment ago -- so the caller hands the row over directly.
     * Without it the picker would create a Tag and then be unable to attach the
     * very Tag it created, which is the whole point of creating one.
     */
    async function edit(ctx, tagId, present, knownTag) {
        const state = ctx && ctx.getResource('workTagEditor');
        if (!state || !live(ctx, state)) return;
        try {
            if (state.preparing) await state.preparing;
            if (!state.options) throw new Error();
            if (present && !knownTag &&
                state.catalogGeneration !== root.prksOfflineDomainGeneration('tags')) {
                await prepare(ctx, state);
            }
            const tag = knownTag || (state.catalog || []).find(t => t.id === tagId) ||
                ctx.getEntity('work').tags.find(t => t.id === tagId) ||
                state.operations.map(o => o.local_context && o.local_context.tag).find(t => t && t.id === tagId);
            if (!tag || (present && !state.catalog && !knownTag)) throw new Error();
            if (knownTag && Array.isArray(state.catalog) &&
                !state.catalog.some(t => t.id === tagId)) {
                state.catalog = state.catalog.concat([knownTag]);
            }
            const base = root.prksWorkTagBase(state.options, tagId);
            await root.prksSync.store.coalesceWorkTag(state.workId, tagId, present, base.present, base.revision, tag);
            if (!live(ctx, state)) { root.prksSync.changed(); return; }
            state.error = null;
            if (owns(ctx, state)) {
                const input = document.getElementById('work-tag-search');
                if (input) input.value = '';
                const results = document.getElementById('work-tag-search-results');
                if (results) root.prksHideInlineComboboxResults(results);
            }
            await paint(ctx, state); // committed before optimistic paint
            root.prksSync.changed();
        } catch (_) {
            state.error = 'Could not save this Tag change locally. Please retry.';
            await paint(ctx, state);
        }
    }
    root.prksMountWorkTags = mount;
    root.prksWorkTagEdit = edit;
})(typeof window === 'undefined' ? globalThis : window);
