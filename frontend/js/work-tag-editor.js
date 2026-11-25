/* Work Tags UI. The TabContext owns the observed base and editor lifetime. */
(function (root) {
    'use strict';
    const reconcileCore = root.prksSyncReconcile ||
        (typeof module !== 'undefined' && module.exports && typeof require === 'function'
            ? require('./sync-reconcile.js') : null);
    const unavailable = 'Tag editing not available offline for this Work yet. Connect once and open the Tags panel to prepare it.';
    function live(ctx, state) {
        return ctx && !ctx.destroyed && ctx.generation === state.generation &&
            ctx.getEntity('work') && ctx.getEntity('work').id === state.workId &&
            ctx.getResource('workTagEditor') === state;
    }
    function owns(ctx, state) { return live(ctx, state) && root.prksRightPanelOwnedBy(ctx) && root.prksOwnerTabIsFocused(ctx); }
    function statusText(ops) {
        if (ops.some(o => o.status === 'conflict')) return 'Conflict';
        if (ops.some(o => o.status === 'syncing')) return 'Syncing…';
        if (ops.some(o => o.last_error)) return 'Sync failed · retry scheduled';
        if (ops.length) return root.prksOfflineRuntimeState() === 'online' ? 'Waiting to sync' : 'Offline · saved locally';
        return 'All changes synced';
    }
    /**
     * A Tag whose change waits on the user (a conflict) or on a scheduled
     * retry. One merely on the wire is NOT blocked: edit() waits for it to
     * land and records the click against the base it produced. Disabling it
     * for the send's few milliseconds only swallowed a click already on its
     * way -- a disabled button dispatches no click at all.
     */
    function blocked(state, tagId) {
        return state.operations.some(o => o.payload.tag_id === tagId && o.status !== 'syncing' &&
            (o.status !== 'pending' || o.attempt_count > 0));
    }
    async function paint(ctx, state) {
        if (!live(ctx, state)) return;
        const paintVersion = state.paintVersion = (state.paintVersion || 0) + 1;
        const rows = await root.prksSync.store.listOperations();
        if (!live(ctx, state) || paintVersion !== state.paintVersion) return;
        for (const op of settledElsewhere(state, state.operations || [], rows)) {
            await adoptSettledBase(ctx, state, op.payload.tag_id, 'settled', op);
            if (!live(ctx, state) || paintVersion !== state.paintVersion) return;
        }
        state.operations = rows.filter(o => o.entity_type === 'work' && o.entity_id === state.workId &&
            ['ADD_WORK_TAG', 'REMOVE_WORK_TAG'].includes(o.operation) && o.status !== 'acknowledged');
        if (!owns(ctx, state)) return;
        const panel = document.getElementById('panel-content');
        const list = panel.querySelector('#work-tags-list');
        if (!list) return;
        const work = ctx.getEntity('work');
        const effectiveTags = root.prksEffectiveWorkTags(work, state.operations);
        const effective = { ...work, tags: effectiveTags };
        const editable = ctx.ui.workDetailsMode === 'tags';
        let useLegacyTags = true;
        if (!editable && owns(ctx, state) && typeof root.prksRefreshOwnedWorkPanelTags === 'function') {
            const rows = (effectiveTags || []).map(tag => ({
                id: tag && tag.id != null ? String(tag.id) : '',
                name: tag && tag.name != null ? String(tag.name) : '',
                color: tag && tag.color != null ? String(tag.color) : '',
            }));
            const refreshed = root.prksRefreshOwnedWorkPanelTags(ctx, work, rows);
            useLegacyTags = refreshed == null;
        }
        if (useLegacyTags) {
        // Identical markup is not a repaint. Rewriting innerHTML needlessly
        // churns the DOM other tiles may be reading, and the first paint now
        // produces exactly what the panel was rendered with.
        // A null refresh means the Vue bridge is unavailable. false from an
        // already-mounted bridge must not overwrite a Vue-owned target.
        const chips = root.renderWorkTagsChips(effective, { editable });
        if (list.innerHTML !== chips) list.innerHTML = chips;
        }
        list.querySelectorAll('[data-tag-id]').forEach(button => {
            button.disabled = !state.options || blocked(state, button.dataset.tagId);
            if (button.disabled) button.title = state.options ? 'This change is waiting to retry or needs resolution.' : unavailable;
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
                    if (!live(ctx, state)) return;
                    button.disabled = true;
                    try {
                        if (!apply && result.code === 'REVISION_CONFLICT') {
                            const ack = { work_id: state.workId, tag_id: op.payload.tag_id,
                                present: result.current_state, server_revision: result.current_revision,
                                tag: op.local_context.tag };
                            if (!await root.prksOfflineReconcileWorkTag(ack)) throw new Error();
                            if (!live(ctx, state)) return;
                            acceptAck(ctx, state, ack);
                        } else if (!apply) {
                            if (!live(ctx, state)) return;
                            // Explicitly discard the intent and stale relationship.
                            // Catalog/lifecycle conflicts do not fabricate a target.
                            const work = ctx.getEntity('work');
                            root.prksOfflineMarkEntityChanged('work', state.workId);
                            root.prksOfflineMarkEntityChanged('work-tag-options', state.workId);
                            ctx.setEntity('work', { ...work, tags: work.tags.filter(t => t.id !== op.payload.tag_id) });
                            state.options = null;
                        }
                        if (!live(ctx, state)) return;
                        await root.prksSync.store.resolveConflict(op.op_id, apply);
                        if (!live(ctx, state)) return;
                        state.error = null; root.prksSync.changed();
                    } catch (_) {
                        if (!live(ctx, state)) return;
                        state.error = 'Could not save the resolution locally. Please retry.';
                    }
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
    /* `op` is the acknowledged operation when there is one: its creation time
     * is what proves the Work this tab holds older than the ACK (#1088). */
    function acceptAck(ctx, state, ack, op) {
        if (!live(ctx, state) || ack.work_id !== state.workId) return;
        state.readVersion = (state.readVersion || 0) + 1;
        const held = state.options ? root.prksWorkTagBase(state.options, ack.tag_id).revision : null;
        const plan = applyWorkTagAck(ctx, ack, op, held, () => refreshOptions(ctx, state));
        if (plan === 'superseded') return;
        if (state.options) setOptionsBase(state.options, ack.tag_id, ack.present, ack.server_revision);
        // An ACK during a refresh cancelled its read (readVersion): read again.
        if (state.refreshing) refreshOptions(ctx, state);
    }
    /* A re-read moved the Work past the options base (another device's link
     * change), so an edit made against the old base could be a no-op or the
     * wrong direction. Editing waits for options read after it. */
    function refreshOptions(ctx, state) {
        if (!live(ctx, state)) return;
        state.options = null;
        state.refreshing = true;
        state.refreshTries = 0;
        state.preparing = prepare(ctx, state, { dedupe: false });
    }
    /* The links of the held Work, read again from the server. Only the links
     * are taken, and only while this tab still holds the value it read past. */
    async function rereadLinks(ctx, state, readVersion) {
        const id = state.workId;
        const held = ctx.getEntity('work');
        if (!held || held.id !== id) return;
        await reconcileCore.adoptFresh(
            () => root.prksOfflineReadEntity('work', id, '/api/works/' + encodeURIComponent(id), { validate: value => !!value && value.id === id, requestPolicy: { dedupe: false } }),
            // A parked or superseded editor no longer owns the held links.
            () => (live(ctx, state) && readVersion === state.readVersion ? ctx.getEntity('work') : null), held,
            fresh => ctx.setEntity('work', reconcileCore.carryObservation(fresh,
                { ...held, tags: Array.isArray(fresh.tags) ? fresh.tags : held.tags })));
    }
    /* A corrective refresh takes only a server answer that agrees with the
     * adopted links. A cached fallback still holds the delayed ACK's base, and
     * two server reads can straddle another device's change: either would let
     * an edit coalesce against the wrong base, so editing stays off. */
    function consistentOptions(ctx, state, read) {
        if (!read || read.source !== 'server' || !read.value) return false;
        const owner = ctx.getEntity('work');
        if (!owner || owner.id !== state.workId || !Array.isArray(owner.tags)) return false;
        const shown = new Set(owner.tags.map(t => t.id));
        const based = new Set(read.value.assigned.map(t => t.tag_id));
        return shown.size === based.size && [...shown].every(id => based.has(id));
    }
    /**
     * One Tag-link ACK into this owner's Work, under the shared ordering rule
     * (`prksSyncReconcile.applyOwnerAck`): an older ACK never replaces a newer
     * link, and a Work this tab cannot prove older than the ACK is patched and
     * then re-read from the server, whose answer wins.
     */
    function applyWorkTagAck(ctx, ack, op, heldRevision, adopted) {
        return reconcileCore.applyOwnerAck({
            ackRevision: ack.server_revision,
            heldRevision,
            operationCreatedAt: op && op.created_at,
        }, {
            get: () => {
                const work = ctx.getEntity('work');
                return work && work.id === ack.work_id && Array.isArray(work.tags) ? work : null;
            },
            set: value => ctx.setEntity('work', value),
            agrees: work => work.tags.some(t => t.id === ack.tag_id) === !!ack.present,
            patch: work => {
                const tags = work.tags.filter(t => t.id !== ack.tag_id);
                if (ack.present) tags.push(ack.tag);
                return { ...work, tags };
            },
            // A corrective read must start after the ACK, never join an older flight.
            read: () => root.prksOfflineReadEntity('work', ack.work_id,
                '/api/works/' + encodeURIComponent(ack.work_id),
                { validate: value => !!value && value.id === ack.work_id, requestPolicy: { dedupe: false } }),
            // Only the links come from the re-read: the held Work also carries
            // pending Folder, Playlist and source values and other ACKs.
            merge: (held, fresh) => ({ ...held, tags: Array.isArray(fresh.tags) ? fresh.tags : held.tags }),
            adopted,
        });
    }
    function setOptionsBase(options, tagId, present, revision) {
        options.assigned = options.assigned.filter(t => t.tag_id !== tagId);
        delete options.known_absent[tagId];
        if (present) options.assigned.push({ tag_id: tagId, relation_revision: revision });
        else if (revision) options.known_absent[tagId] = revision;
    }
    /** Entity-only tag acknowledgement for a parked owner. No options, no paint. */
    function applyWorkTagEntityAck(ctx, ack, op) {
        if (!ctx || !ack || !Number.isSafeInteger(ack.server_revision)) return;
        applyWorkTagAck(ctx, ack, op, null, null);
    }
    async function prepare(ctx, state, requestPolicy) {
        const readVersion = state.readVersion = (state.readVersion || 0) + 1;
        try {
            const [options, catalog] = await Promise.allSettled([
                root.prksReadWorkTagOptions(state.workId, requestPolicy ? { requestPolicy } : undefined),
                root.prksReadTagsIndex(),
            ]);
            if (!live(ctx, state) || readVersion !== state.readVersion) return;
            if (state.refreshing) {
                const read = options.status === 'fulfilled' ? options.value : null;
                if (!consistentOptions(ctx, state, read)) {
                    state.options = null;
                    // Another device may have moved a link between the reads: read again, a bounded number of times.
                    if (read && read.source === 'server' && (state.refreshTries = (state.refreshTries || 0) + 1) < 3) {
                        // Either read may be the older one, so both are read again.
                        await rereadLinks(ctx, state, readVersion);
                        if (!live(ctx, state) || readVersion !== state.readVersion) return;
                        await (state.preparing = prepare(ctx, state, { dedupe: false }));
                        return;
                    }
                    state.catalog = catalog.status === 'fulfilled' ? catalog.value.value : null;
                    state.catalogGeneration = root.prksOfflineDomainGeneration('tags');
                    await paint(ctx, state);
                    return;
                }
            }
            state.refreshing = false;
            state.options = options.status === 'fulfilled' ? options.value.value : null;
            state.catalog = catalog.status === 'fulfilled' ? catalog.value.value : null;
            state.catalogGeneration = root.prksOfflineDomainGeneration('tags');
        } catch (_) {
            if (!live(ctx, state)) return;
            state.error = unavailable;
        }
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
        if (typeof root.prksBindComboboxKeys === 'function') {
            root.prksBindComboboxKeys(input, results, {
                enterCreates: true,
                onEscape: function () { root.prksHideInlineComboboxResults(results); },
            });
        }
    }
    function mount(ctx, workId) {
        if (!ctx || !root.prksSync) return;
        let state = ctx.getResource('workTagEditor');
        if (!state || state.workId !== workId || state.generation !== ctx.generation) {
            /* Capture the ticket before subscriptions or async prepare.
             * setResource would mint a later ticket. A rejected registration
             * does not subscribe, paint, prepare, or mutate this owner. */
            const ticket = typeof ctx.resourceTicket === 'function' ? ctx.resourceTicket() : null;
            const next = { workId, generation: ctx.generation, operations: [], options: null, catalog: null, error: null };
            const stops = { sync: null };
            const attached = typeof ctx.registerResource === 'function'
                ? ctx.registerResource(ticket, {
                    kind: 'workTagEditor',
                    value: next,
                    suspendable: false,
                    dispose: function () {
                        if (stops.sync) stops.sync();
                    },
                })
                : 'rejected';
            if (attached === 'rejected') return;
            if (typeof root.prksBindOwnerWorkAcknowledgement === 'function') {
                root.prksBindOwnerWorkAcknowledgement(ctx);
            }
            stops.sync = root.prksSync.subscribe(event => {
                /* Only THIS family's acknowledgements. The durable queue is
                 * shared, and another family's ACK carries no tag_id -- feeding
                 * it to acceptAck() bumps readVersion, which silently cancels
                 * an in-flight prepare() and leaves the picker disabled with no
                 * catalog and nothing to retry it. */
                if (event.acknowledged && ['ADD_WORK_TAG', 'REMOVE_WORK_TAG'].includes(event.operation)) {
                    acceptAck(ctx, next, event.acknowledged, event.op);
                }
                void paint(ctx, next).catch(() => {});
            });
            state = next;
        }
        void paint(ctx, state).catch(() => {});
        if (ctx.ui.workDetailsMode === 'tags') {
            bindPicker(ctx, state);
            state.preparing = prepare(ctx, state);
        }
    }
    /** This Work's unacknowledged change of `tagId` that can no longer be rewritten. */
    function inFlightChange(rows, state, tagId) {
        return rows.find(r => r.entity_type === 'work' && r.entity_id === state.workId &&
            ['ADD_WORK_TAG', 'REMOVE_WORK_TAG'].includes(r.operation) &&
            r.payload && r.payload.tag_id === tagId && r.status !== 'acknowledged');
    }
    /**
     * Wait (bounded) for this Tag's in-flight change on the Work to leave the
     * wire. Resolves:
     *  - 'acknowledged' when its ACK reached THIS runtime's subscribers, so
     *    acceptAck has already moved state.options to the new base;
     *  - 'draft' when the claim was released back to a never-sent draft: the
     *    acknowledged base is unchanged and the retry rewrites the draft;
     *  - 'settled' when it left the wire without this runtime seeing the ACK
     *    (another tab sent it, or the ACK landed before this subscription).
     *    state.options may be stale then, so the caller must re-read the base;
     *  - false on a conflict, an unreadable store, or when the wait runs out.
     *
     * The subscription starts before the outbox is read, so no settle between
     * the two is missed; a settle in another tab is found by a slow poll.
     */
    function inFlightSettled(ctx, state, tagId, timeoutMs) {
        return new Promise(resolve => {
            let finished = false;
            let opId = null;
            const timer = setTimeout(() => finish(false), timeoutMs || 10000);
            /* Another tab's sender emits only to its own subscribers, so its
             * settle is seen here by polling the shared outbox. */
            const poll = setInterval(() => void look(), 250);
            function finish(outcome) {
                if (finished) return;
                finished = true;
                clearTimeout(timer);
                clearInterval(poll);
                stop();
                resolve(outcome);
            }
            function look() {
                /* A closed editor cannot save the retry: stop reading. */
                if (!live(ctx, state)) { finish(false); return Promise.resolve(); }
                return root.prksSync.store.listOperations().then(rows => {
                    if (finished) return;
                    if (opId === null) {
                        const op = inFlightChange(rows, state, tagId);
                        if (!op) { finish('settled'); return; }
                        opId = op.op_id;
                    }
                    const row = rows.find(r => r.op_id === opId);
                    if (!row || row.status === 'acknowledged') finish('settled');
                    else if (row.status === 'conflict') finish(false);
                    else if (row.status === 'pending' && !(row.attempt_count > 0)) finish('draft');
                }, () => finish(false));
            }
            const stop = root.prksSync.subscribe(event => {
                if (finished) return;
                if (opId !== null && event && event.op && event.op.op_id === opId) {
                    finish(event.acknowledged ? 'acknowledged' : false);
                    return;
                }
                /* A terminal settle or a released claim emits without the op. */
                void look();
            });
            void look();
        });
    }
    /** The acknowledged base for `tagId` after its change left the wire. */
    async function settledBase(ctx, state, tagId, outcome) {
        for (let attempt = 0; outcome === 'settled' && attempt < 3; attempt += 1) {
            const version = state.readVersion || 0;
            const read = await root.prksReadWorkTagOptions(state.workId);
            if (!read || !read.value || !live(ctx, state)) return null;
            /* An ACK applied while reading (acceptAck bumps readVersion) is
             * newer than this snapshot: read again rather than publish it. */
            if ((state.readVersion || 0) !== version) continue;
            /* Supersede any prepare() still reading the older projection. */
            state.readVersion = version + 1;
            const base = root.prksWorkTagBase(read.value, tagId);
            /* Only this Tag's base: the Work entity is brought up to date for
             * this Tag alone, and another Tag's base must never run ahead of
             * the chip it is measured against. */
            if (state.options) setOptionsBase(state.options, tagId, base.present, base.revision);
            // A corrective refresh installs only options it proved consistent.
            else if (!state.refreshing) state.options = read.value;
            return base;
        }
        if (outcome === 'settled') return null;
        return state.options ? root.prksWorkTagBase(state.options, tagId) : null;
    }
    /** A change this panel showed as sent that left the outbox without its
     * ACK reaching this tab: another tab's sender settled it. The ACK always
     * moves the relationship past the envelope's base, so a base that has not
     * moved is this tab's stale copy. */
    function sentChange(op) {
        return op && op.status !== 'conflict' && (op.status === 'syncing' || op.attempt_count > 0);
    }
    function staleAfter(state, op) {
        return !!state.options && Number.isSafeInteger(op.base_revision) &&
            !(root.prksWorkTagBase(state.options, op.payload.tag_id).revision > op.base_revision);
    }
    function settledElsewhere(state, previous, rows) {
        return previous.filter(op => sentChange(op) && staleAfter(state, op) &&
            !rows.some(r => r.op_id === op.op_id && r.status !== 'acknowledged'));
    }
    /**
     * settledBase, and for a change settled in another tab also the Work's
     * Tags: this tab's entity never received that ACK either, so without this
     * the next paint would show the pre-ACK relationship.
     */
    async function adoptSettledBase(ctx, state, tagId, outcome, op) {
        const base = await settledBase(ctx, state, tagId, outcome);
        if (!base || outcome !== 'settled' || !live(ctx, state)) return base;
        const work = ctx.getEntity('work');
        const tag = (op && op.local_context && op.local_context.tag) ||
            (work.tags || []).find(t => t.id === tagId) ||
            (state.catalog || []).find(t => t.id === tagId);
        if (!base.present || tag) {
            applyWorkTagEntityAck(ctx, { work_id: state.workId, tag_id: tagId, present: base.present,
                server_revision: base.revision, tag }, op);
        }
        return base;
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
        let queued = false;
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
            if (!live(ctx, state)) return;
            if (!owns(ctx, state)) return;
            /* A click can race this Tag's earlier change going on the wire:
             * the panel painted before the send disabled the chip. That
             * envelope is immutable, so wait (within one budget, however
             * often sync reclaims it) for it to leave the wire, and record
             * this change against the base it produced rather than drop the
             * click. */
            const deadline = Date.now() + 10000;
            /* The change this panel showed as sent. If another tab's sender
             * settles and retires it, nothing here hears the ACK: the store
             * then finds no row, and "remove what I just added" equals this
             * tab's pre-ACK base and records nothing. That no-op is re-read
             * once against the server's base rather than taken as done. */
            const shown = state.operations.find(o => o.payload.tag_id === tagId && sentChange(o) &&
                Number.isSafeInteger(o.base_revision));
            let reread = false;
            let next = base;
            for (;;) {
                try {
                    const result = await root.prksSync.store.coalesceWorkTag(state.workId, tagId, present, next.present, next.revision, tag);
                    if (result === null && shown && !reread && !(next.revision > shown.base_revision)) {
                        reread = true;
                        if (!live(ctx, state) || !owns(ctx, state)) return;
                        next = await adoptSettledBase(ctx, state, tagId, 'settled', shown);
                        if (!next) throw new Error('Tag base unavailable');
                        if (!live(ctx, state) || !owns(ctx, state)) return;
                        continue;
                    }
                    break;
                } catch (error) {
                    const left = deadline - Date.now();
                    if (!error || error.prksLocalStoreCode !== 'scope_busy' || left <= 0) throw error;
                    const outcome = await inFlightSettled(ctx, state, tagId, left);
                    if (!outcome) throw error;
                    if (!live(ctx, state) || !owns(ctx, state)) return;
                    next = await adoptSettledBase(ctx, state, tagId, outcome, shown);
                    if (!next) throw error;
                    if (!live(ctx, state) || !owns(ctx, state)) return;
                }
            }
            queued = true;
            if (!live(ctx, state)) { root.prksSync.changed(); return true; }
            state.error = null;
            if (owns(ctx, state)) {
                const input = document.getElementById('work-tag-search');
                if (input) input.value = '';
                const results = document.getElementById('work-tag-search-results');
                if (results) root.prksHideInlineComboboxResults(results);
            }
            await paint(ctx, state); // committed before optimistic paint
            root.prksSync.changed();
            return true;
        } catch (_) {
            if (!live(ctx, state)) return queued;
            state.error = queued
                ? 'Tag change saved locally, but the panel could not refresh.'
                : 'Could not save this Tag change locally. Please retry.';
            await paint(ctx, state);
            return queued;
        }
    }
    root.prksMountWorkTags = mount;
    root.prksWorkTagEdit = edit;
    root.prksApplyWorkTagEntityAck = applyWorkTagEntityAck;
})(typeof window === 'undefined' ? globalThis : window);
