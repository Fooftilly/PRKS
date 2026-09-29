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
    /* Durable storage could not be read. The editor still opens -- the
     * online-only controls above are unaffected -- but these fields must not
     * become editable from a base we cannot trust: a pending edit may exist
     * that this session simply could not see, and saving over it would destroy
     * work. Falling back to a direct PATCH is equally out: online and offline
     * keep the same durable-first contract, always. */
    const unreadable = 'Local changes could not be read from browser storage. Bibliographic editing is temporarily unavailable.';

    function live(ctx, state) {
        return ctx && !ctx.destroyed && ctx.generation === state.generation &&
            ctx.getEntity('work') && ctx.getEntity('work').id === state.workId;
    }
    function owns(ctx, state) {
        return live(ctx, state) && root.prksRightPanelOwnedBy(ctx) && root.prksOwnerTabIsFocused(ctx);
    }
    function panelOf() { return document.getElementById('panel-content'); }

    /* Each durable Save covers EXACTLY the fields in its own section. Status
     * decides which Progress group a Work belongs to, which is not a
     * bibliographic edit and must not ride along on a button labelled "Save
     * bibliographic details" -- nor the reverse. One button that quietly meant
     * "these fields plus that one" is the mixed-atomicity contract 2D removed
     * from the online save; reintroducing it inside the durable path would be
     * the same mistake with better storage.
     *
     * Which fields belong to a group is read from the DOM rather than listed
     * here, so the control's position IS the answer and the two cannot drift. */
    const SAVE_GROUPS = Object.freeze([
        { name: 'bib', role: 'work-bib-editor', saveId: 'save-work-bib-btn',
          syncRole: 'work-bib-sync',
          failure: 'Could not save these details locally. Please retry.' },
        { name: 'status', role: 'work-status-editor', saveId: 'save-work-status-btn',
          syncRole: 'work-status-sync',
          failure: 'Could not save the status locally. Please retry.' },
        { name: 'identity', role: 'work-identity-editor', saveId: 'save-work-identity-btn',
          syncRole: 'work-identity-sync',
          failure: 'Could not save the identity locally. Please retry.' },
    ]);

    function groupSection(group) {
        const panel = panelOf();
        return panel && panel.querySelector('[data-prks-role="' + group.role + '"]');
    }

    function groupInputs(section) {
        return Array.prototype.slice.call(section.querySelectorAll('[data-prks-work-field]'));
    }

    function groupOperations(state, section) {
        const fields = groupInputs(section).map(input => input.dataset.prksWorkField);
        return state.operations.filter(op => fields.indexOf(op.payload.field) !== -1);
    }

    function errorFor(state, group) {
        return (state.errors && state.errors[group.name]) || null;
    }

    function setError(state, group, text) {
        if (!state.errors) state.errors = {};
        state.errors[group.name] = text;
    }

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

    function fieldsFor(group, work) {
        const kind = work && typeof root.prksInferWorkSourceKind === 'function'
            ? root.prksInferWorkSourceKind(work) : '';
        if (typeof root.prksWorkMetadataGroupFields === 'function') {
            return root.prksWorkMetadataGroupFields(group.name, kind);
        }
        const section = groupSection(group);
        return section ? groupInputs(section).map(input => input.dataset.prksWorkField) : [];
    }

    function sessionStill(ctx, workId, session) {
        return typeof root.prksWorkMetaSessionStill !== 'function' ||
            root.prksWorkMetaSessionStill(ctx, workId, session);
    }

    /** Acknowledged Work + durable pending edits, for the read-only card. */
    async function repaintDisplay(ctx, state) {
        const host = document.querySelector('[data-prks-role="work-bib-rows"]');
        if (!host || !owns(ctx, state)) return;
        const work = ctx.getEntity('work');
        const effective = root.prksEffectiveWorkMetadata(work, state.operations);
        if (typeof root.prksWorkPanelReadOwns === 'function' && root.prksWorkPanelReadOwns(ctx)) {
            const hydration = typeof root.prksPendingWorkMetadataState === 'function'
                ? root.prksPendingWorkMetadataState() : 'ready';
            if (hydration !== 'unavailable' && typeof root.prksVueRefreshWorkPanelRead === 'function') {
                const publishedRaw = effective && effective.published_date;
                const publishedDisplay = typeof root.prksFormatPublishedForDisplay === 'function'
                    ? root.prksFormatPublishedForDisplay(publishedRaw)
                    : (publishedRaw == null ? '' : String(publishedRaw));
                const effectiveMetadata = Object.assign({}, effective);
                delete effectiveMetadata.roles;
                const docMeta = typeof root.prksDocTypeMeta === 'function'
                    ? root.prksDocTypeMeta(effective && effective.doc_type) : null;
                const statusLabel = String((effective && effective.status) || '').trim() || 'Not Started';
                const statusIcon = typeof root.PRKS_PROGRESS_STATUS_ICON === 'object' && root.PRKS_PROGRESS_STATUS_ICON
                    ? root.PRKS_PROGRESS_STATUS_ICON[statusLabel] || ''
                    : '';
                root.prksVueRefreshWorkPanelRead({
                    ownerTabId: String(ctx.tabId),
                    ownerGeneration: ctx.generation,
                    workId: state.workId,
                    effectiveWork: effectiveMetadata,
                    publishedDisplay: publishedDisplay == null ? '' : String(publishedDisplay),
                    docType: docMeta ? {
                        value: docMeta.value,
                        label: docMeta.label,
                        color: docMeta.color,
                        border: docMeta.border,
                    } : null,
                    statusIcon: statusIcon,
                });
            }
            return;
        }
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
        if (!panel) return;

        const durable = typeof root.prksPendingWorkMetadataState === 'function'
            ? root.prksPendingWorkMetadataState() : 'ready';
        const readable = durable !== 'unavailable';
        const editable = !!state.observed && readable;
        const blockedText = readable ? unavailable : unreadable;

        if (typeof root.prksVueApplyWorkMetadataChrome === 'function' &&
            typeof root.prksVueWorkMetadataEditorOwns === 'function' &&
            root.prksVueWorkMetadataEditorOwns(ctx)) {
            const work = ctx.getEntity('work');
            const groups = SAVE_GROUPS.map(group => {
                const fields = fieldsFor(group, work);
                const ops = state.operations.filter(op => fields.indexOf(op.payload.field) !== -1);
                const fieldChrome = {};
                fields.forEach(field => {
                    const blocked = !editable || busy(state, field);
                    fieldChrome[field] = {
                        disabled: blocked,
                        title: blocked
                            ? (!readable ? unreadable : (state.observed ? 'This field is syncing or needs resolution.' : unavailable))
                            : '',
                    };
                });
                return {
                    name: group.name,
                    status: errorFor(state, group) || (editable ? statusText(ops) : blockedText),
                    saveDisabled: !editable,
                    fields: fieldChrome,
                    conflicts: ops.filter(o => o.status === 'conflict').map(op => conflictDescriptor(op, group.name)),
                };
            });
            root.prksVueApplyWorkMetadataChrome(String(ctx.tabId), state.workId, groups);
            return;
        }

        for (const group of SAVE_GROUPS) {
            const section = groupSection(group);
            if (!section) continue;
            groupInputs(section).forEach(input => {
                const blocked = !editable || busy(state, input.dataset.prksWorkField);
                input.disabled = blocked;
                input.title = blocked
                    ? (!readable ? unreadable : (state.observed ? 'This field is syncing or needs resolution.' : unavailable))
                    : '';
                /* A segmented control's field value lives on a HIDDEN input,
                 * so disabling that alone leaves the buttons the user actually
                 * clicks fully live -- a control that looks editable while its
                 * save is refused. Disable the presentation with the value. */
                const wrap = typeof input.closest === 'function'
                    ? input.closest('.prks-segmented-wrap') : null;
                if (wrap) {
                    wrap.querySelectorAll('.prks-segmented__btn').forEach(btn => {
                        btn.disabled = blocked;
                        btn.title = input.title;
                    });
                }
            });
            const save = section.querySelector('#' + group.saveId);
            if (save) save.disabled = !editable;

            const status = section.querySelector('[data-prks-role="' + group.syncRole + '"]');
            if (!status) continue;
            /* A group reports only its OWN fields. A DOI conflict must not
             * tell the user their Status needs a decision, and a Status
             * conflict must not make the bibliographic group look broken. */
            const ops = groupOperations(state, section);
            status.replaceChildren();
            status.appendChild(document.createTextNode(
                errorFor(state, group) || (editable ? statusText(ops) : blockedText)));
            for (const op of ops.filter(o => o.status === 'conflict')) {
                status.appendChild(conflictRow(ctx, state, op, group));
            }
        }
        /* There is nothing left here that saves over HTTP: every user-editable
         * Work metadata value belongs to one of the durable groups above, so
         * the editor has no online-only mutation path to gate on connectivity
         * and no note to show about one. */
    }

    /* Painting must never become an unhandled rejection. The durable store
     * REJECTS when IndexedDB is unavailable -- that is its whole contract --
     * and a browser with storage blocked has to keep working online exactly as
     * it did before, not surface a page error from a background repaint. */
    function safePaint(ctx, state) {
        return paint(ctx, state).catch(() => {});
    }

    /* What to say when a codec refuses a value. A refusal has to name the
     * shape the field wants, because "invalid" tells the user nothing they can
     * act on -- and silence is worse still: the save would appear to do
     * nothing at all. Each message belongs to the field whose codec can
     * produce the refusal. */
    const FIELD_ERRORS = Object.freeze({
        published_date: 'Use dd/mm/yyyy.',
        thumb_page: 'Enter a page number of 1 or more, or leave it empty for page 1.',
    });

    /** The error element that belongs to one field's control, by convention. */
    function fieldErrorElement(input) {
        return input && input.id ? document.getElementById(input.id + '-error') : null;
    }

    /** Inline, field-local feedback for a value the codec rejected. */
    function showFieldError(ctx, field) {
        const message = FIELD_ERRORS[field] || 'That value cannot be saved.';
        if (typeof root.prksVueSetWorkMetadataFieldError === 'function' &&
            typeof root.prksVueWorkMetadataEditorOwns === 'function' &&
            root.prksVueWorkMetadataEditorOwns(ctx)) {
            root.prksVueSetWorkMetadataFieldError(field, message);
            return;
        }
        const input = document.querySelector('[data-prks-work-field="' + field + '"]');
        if (input) { input.setAttribute('aria-invalid', 'true'); input.focus(); }
        const error = fieldErrorElement(input);
        if (error) error.textContent = message;
    }

    function clearFieldErrors(ctx) {
        /* The Vue editor owns its error text. Clearing the DOM here races a
         * same-message set: Vue batches them into no patch, and the emptied
         * node never gets the text back. */
        if (typeof root.prksVueWorkMetadataEditorOwns === 'function' &&
            root.prksVueWorkMetadataEditorOwns(ctx)) {
            return;
        }
        document.querySelectorAll('[data-prks-work-field]').forEach(input => {
            input.removeAttribute('aria-invalid');
            const error = fieldErrorElement(input);
            if (error) error.textContent = '';
        });
    }

    const PREVIEW_CHARS = 160;

    /** Bounded, code-point safe, and never the whole value. */
    function preview(text) {
        const points = Array.from(String(text == null ? '' : text));
        return points.length <= PREVIEW_CHARS
            ? points.join('')
            : points.slice(0, PREVIEW_CHARS).join('') + '…';
    }

    function sizeLabel(bytes) {
        if (!Number.isSafeInteger(bytes)) return 'unknown size';
        return bytes < 1024 ? bytes + ' bytes' : Math.ceil(bytes / 1024) + ' KB';
    }

    function conflictDescriptor(op, groupName) {
        const result = op.server_result || {};
        const field = op.payload.field;
        const label = root.PRKS_SYNCED_WORK_FIELD_LABELS[field] || field;
        let text;
        if (result.code === 'REVISION_CONFLICT' || result.code === 'FUTURE_REVISION') {
            /* Never dump a megabyte of Abstract into a one-line conflict
             * sentence. A byte-limited field reports bounded previews and
             * sizes, which is what the user needs to tell the two apart. */
            if (typeof result.current_preview === 'string') {
                const mine = op.payload.value;
                text = label + ' differs. This device (' + sizeLabel(root.prksWorkFieldUtf8Bytes(mine)) +
                    '): "' + preview(mine) + '". Server (' + sizeLabel(result.current_bytes) +
                    '): "' + preview(result.current_preview) + '". ';
            } else {
                text = label + ' — this device: "' + op.payload.value + '". Server: "' +
                    (result.current_value || '') + '". ';
            }
        } else if (result.code === 'ENTITY_NOT_FOUND') {
            text = 'This Work no longer exists on the server. ';
        } else if (result.code === 'WRONG_OPERATION_FOR_SOURCE') {
            text = 'This file\u2019s source is a video, so its URL is part of the video\u2019s '
                + 'identity and is changed from the Video source section instead. ';
        } else {
            text = label + ' could not synchronize (' + (result.code || 'protocol error') + '). ';
        }
        const reappliable = result.code === 'REVISION_CONFLICT' && Number.isSafeInteger(result.current_revision);
        const actions = [{ label: reappliable ? 'Use server' : 'Discard my value', apply: false }];
        if (reappliable) actions.push({ label: 'Apply my value', apply: true });
        return { opId: op.op_id, field: field, group: groupName, text: text, actions: actions };
    }

    function conflictRow(ctx, state, op, group) {
        const described = conflictDescriptor(op, group.name);
        const result = op.server_result || {};
        const field = op.payload.field;
        const item = document.createElement('div');
        item.dataset.prksWorkFieldConflict = field;
        item.appendChild(document.createTextNode(described.text));
        described.actions.forEach(choice => action(item, ctx, state, op, choice.label, choice.apply, group));
        return item;
    }

    function action(item, ctx, state, op, label, apply, group) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'prks-btn prks-btn--secondary prks-btn--sm';
        button.textContent = label;
        button.onclick = async () => {
            button.disabled = true;
            await actionResolve(ctx, state, op, apply, group);
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
        // Same helper as the cached reconciliation, so the live editor and the
        // cached projection cannot disagree about this field's shape.
        if (entry) {
            state.observed.fields[ack.field] =
                root.prksMetadataStateAckPatch(ack.field, ack.server_revision, ack.value);
        }
        /* The entity write above is scoped to THIS ctx, but the panel is
         * shared: a global lookup would find whichever Work's editor is on
         * screen right now, which is not necessarily this one. An unfocused
         * Work's acknowledgement writing its value into the focused Work's
         * input is the acknowledgement publishing into another Work. Only the
         * owner of the panel may touch the panel, and only within it. */
        const panel = owns(ctx, state) ? panelOf() : null;
        if (typeof root.prksVueAcceptWorkMetadataField === 'function' &&
            root.prksVueAcceptWorkMetadataField(ctx, ack.field, ack.value == null ? '' : String(ack.value))) {
            return;
        }
        const input = panel && panel.querySelector('[data-prks-work-field="' + ack.field + '"]');
        /* Not conditioned on the control being enabled: it is disabled
         * precisely BECAUSE this operation is in flight, so an enabled-only
         * write would never run -- and the field would keep showing the text
         * the user typed even where the server canonicalizes it. Text the user
         * is typing right now is what must not be overwritten. */
        if (input && document.activeElement !== input) input.value = ack.value;
    }

    async function prepare(ctx, state) {
        const readVersion = state.readVersion = (state.readVersion || 0) + 1;
        try {
            const result = await root.prksReadWorkMetadataState(state.workId);
            if (!live(ctx, state) || readVersion !== state.readVersion) return;
            state.observed = result.value;
        } catch (_) {
            /* A rejected read is the same generation of work as a successful
             * one. Closing and reopening Edit metadata reuses this state and
             * starts a newer read; the older rejection must not clear the
             * base that newer read installed, or repaint from the failure. */
            if (!live(ctx, state) || readVersion !== state.readVersion) return;
            state.observed = null;
        }
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
                    acceptAck(ctx, state, root.prksEffectiveMetadataAck(event.acknowledged, event.op));
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
     * One Save, one transaction, however many fields it touched -- and only the
     * fields of ONE group. Only values that actually differ from the observed
     * server state become operations: the form submits every field every time,
     * and one operation per field per Save would be one chance to conflict
     * over nothing per field.
     */
    async function save(workId, groupName) {
        const ctx = root.prksGetFocusedTabContext ? root.prksGetFocusedTabContext() : null;
        const state = ctx && ctx.getResource('workMetadataEditor');
        if (!state || !live(ctx, state) || state.workId !== workId) return;
        const group = SAVE_GROUPS.find(g => g.name === (groupName || 'bib'));
        if (!group) return;
        const session = ctx.ui && typeof ctx.ui.workMetaEditSession === 'number' ? ctx.ui.workMetaEditSession : 0;
        const hasSession = !!(ctx.ui && ctx.ui.workMetaDraft);
        const sessionOwned = () => {
            const liveSession = !!(ctx.ui && ctx.ui.workMetaDraft);
            if (!liveSession) return !hasSession;
            return sessionStill(ctx, workId, session);
        };
        if (!sessionOwned()) return;
        const section = groupSection(group);
        const button = section && section.querySelector('#' + group.saveId);
        if (button && root.prksSetButtonBusy) root.prksSetButtonBusy(button, true, { busyLabel: 'Saving…' });
        try {
            if (state.preparing) await state.preparing;
            if (!sessionOwned()) return;
            if (!state.observed) throw new Error('no observed base');
            if (typeof root.prksVueSetWorkMetadataFieldError === 'function') {
                root.prksVueSetWorkMetadataFieldError('', '');
            }
            clearFieldErrors(ctx);
            const names = fieldsFor(group, ctx.getEntity('work'));
            const source = ctx.ui && ctx.ui.workMetaDraft;
            const draft = {};
            if (source) {
                names.forEach(field => {
                    draft[field] = source[field] == null ? '' : String(source[field]);
                });
            } else if (section) {
                groupInputs(section).forEach(input => {
                    draft[input.dataset.prksWorkField] = input.value;
                });
            } else {
                return;
            }
            const snapshot = Object.assign({}, draft);
            // A byte-limited field's acknowledged base lives on the Work
            // record, not in the projection -- see prksObservedWorkFields().
            // The draft above is the edit session. observed.fields is the
            // server base. effectiveWork is neither.
            const observed = { fields: root.prksObservedWorkFields(state.observed, ctx.getEntity('work')) };
            /* A value the codec cannot interpret -- `31/02/2026` -- must not
             * become an operation. The existing inline date error says so, the
             * draft stays, and nothing is stored or sent. */
            for (const field of Object.keys(draft)) {
                if (root.prksWorkFieldToCanonical(field, draft[field]) !== null) continue;
                if (!sessionOwned()) return;
                setError(state, group, null);
                showFieldError(ctx, field);
                await safePaint(ctx, state);
                return;
            }
            const changes = root.prksDirtyWorkMetadataFields(draft, observed, state.operations);
            /* Refuse visibly rather than enqueue something the server will
             * reject: the draft stays on screen, nothing is stored, and nothing
             * is sent. Silently truncating would destroy the user's text. */
            for (const field of Object.keys(changes)) {
                /* Named the way the FORM names it: a video's control says
                 * "Channel name", and a refusal naming a control the user
                 * cannot see is a refusal they cannot act on. */
                const control = section && section.querySelector('[data-prks-work-field="' + field + '"]');
                const labelEl = control && control.id && section
                    ? section.querySelector('label[for="' + control.id + '"]') : null;
                const label = labelEl ? labelEl.textContent.trim() : (root.PRKS_SYNCED_WORK_FIELD_LABELS[field] || '');
                const tooLong = root.prksWorkFieldLimitError(field, changes[field], label);
                if (tooLong) {
                    if (!sessionOwned()) return;
                    setError(state, group, tooLong);
                    await safePaint(ctx, state);
                    return;
                }
            }
            if (!sessionOwned()) return;
            await root.prksSync.store.saveWorkMetadataFields(workId, changes, observed.fields);
            root.prksSync.changed();
            if (!sessionOwned()) return;
            if (typeof root.prksCommitWorkMetaBaseline === 'function') {
                root.prksCommitWorkMetaBaseline(ctx, workId, session, snapshot, Object.keys(changes));
            }
            setError(state, group, null);
            await safePaint(ctx, state);
        } catch (error) {
            if (!sessionOwned()) return;
            setError(state, group, error && error.prksLocalStoreCode === 'scope_busy'
                ? 'One of these fields is still syncing or needs a decision below.'
                : group.failure);
            await safePaint(ctx, state);
        } finally {
            if (button && root.prksSetButtonBusy) root.prksSetButtonBusy(button, false);
        }
    }

    function resolveFieldConflict(ctx, state, op, apply, group) {
        return actionResolve(ctx, state, op, apply, group);
    }

    async function actionResolve(ctx, state, op, apply, group) {
        const session = ctx.ui && typeof ctx.ui.workMetaEditSession === 'number' ? ctx.ui.workMetaEditSession : 0;
        const still = () => sessionStill(ctx, state.workId, session);
        try {
            const result = op.server_result || {};
            /* With only a preview there is no authoritative value to write,
             * so taking the server's version discards the local intent and
             * lets the next read fetch the real text rather than trusting a
             * truncated copy. */
            if (!apply && typeof result.current_value === 'string') {
                const ack = { work_id: state.workId, field: op.payload.field,
                    value: result.current_value, server_revision: result.current_revision,
                    changed: false, code: 'ACKNOWLEDGED' };
                if (!await root.prksOfflineReconcileWorkField(ack)) throw new Error();
                if (still()) acceptAck(ctx, state, ack);
            } else if (!apply) {
                root.prksOfflineMarkEntityChanged('work', state.workId);
                root.prksOfflineMarkEntityChanged('work-metadata-state', state.workId);
                if (still()) state.observed = null;
            }
            await root.prksSync.store.resolveConflict(op.op_id, apply);
            root.prksSync.changed();
            if (!still()) return;
            setError(state, group, null);
        } catch (_) {
            if (!still()) return;
            setError(state, group, 'Could not save that resolution locally. Please retry.');
        }
        if (!still()) return;
        await safePaint(ctx, state);
    }

    root.prksResolveWorkMetadataFieldConflict = function (opId, apply, groupName) {
        const ctx = root.prksGetFocusedTabContext ? root.prksGetFocusedTabContext() : null;
        const state = ctx && ctx.getResource('workMetadataEditor');
        if (!state || !live(ctx, state)) return;
        const session = ctx.ui && typeof ctx.ui.workMetaEditSession === 'number' ? ctx.ui.workMetaEditSession : 0;
        if (!sessionStill(ctx, state.workId, session)) return;
        const op = (state.operations || []).find(item => item.op_id === opId);
        const group = SAVE_GROUPS.find(item => item.name === groupName);
        if (!op || !group) return;
        return resolveFieldConflict(ctx, state, op, apply, group);
    };

    root.prksMountWorkMetadataEditor = mount;
    root.prksSaveWorkMetadataFields = save;
})(typeof window === 'undefined' ? globalThis : window);
