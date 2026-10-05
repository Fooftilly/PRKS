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
            ctx.getEntity('work') && ctx.getEntity('work').id === state.workId &&
            ctx.getResource('workSourceEditor') === state;
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
        if (!live(ctx, state)) return;
        const paintVersion = state.paintVersion = (state.paintVersion || 0) + 1;
        const rows = await root.prksRefreshPendingWorkSources();
        if (!live(ctx, state) || paintVersion !== state.paintVersion) return;
        state.operations = root.prksWorkSourceOperations(rows, state.workId);
        if (!owns(ctx, state)) return;
        const section = sectionOf();
        if (!section) return;

        const editable = !!state.observed;
        const busy = state.operations.some(o => o.status !== 'pending' || o.attempt_count > 0);
        if (typeof root.prksVueApplyWorkMetadataChrome === 'function' &&
            typeof root.prksVueWorkMetadataEditorOwns === 'function' &&
            root.prksVueWorkMetadataEditorOwns(ctx)) {
            root.prksVueApplyWorkMetadataChrome(String(ctx.tabId), state.workId, [{
                name: 'source',
                status: state.error || (editable ? statusText(state.operations) : unavailable),
                saveDisabled: !editable || busy,
                fields: {
                    source_url: {
                        disabled: !editable || busy,
                        title: !editable || busy
                            ? (editable ? 'This source is syncing or needs resolution.' : unavailable)
                            : '',
                    },
                },
                conflicts: state.operations.filter(o => o.status === 'conflict').map(sourceConflictDescriptor),
            }]);
            return;
        }
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

    function sourceConflictDescriptor(op) {
        const result = op.server_result || {};
        let text;
        if (result.code === 'SOURCE_REVISION_CONFLICT' || result.code === 'FUTURE_REVISION') {
            /* ONE question: which video should this file be? Never separate
             * questions about `provider` and `provider_id` -- nobody chose
             * those, they were derived from this very URL. */
            text = 'This file’s video differs. This device: "' +
                preview(op.payload.source.url) + '". Server: "' +
                preview(result.current_preview) + '". ';
        } else if (result.code === 'ENTITY_NOT_FOUND') {
            text = 'This file no longer exists on the server. ';
        } else if (result.code === 'UNSUPPORTED_SOURCE_TRANSITION') {
            text = 'This file is no longer a video on the server, so its video source cannot be changed. ';
        } else if (result.code === 'INVALID_SOURCE_STATE') {
            text = 'This file\u2019s existing video link cannot be read, so it cannot be '
                + 'changed from here yet. ';
        } else {
            text = 'The video source could not synchronize (' + (result.code || 'protocol error') + '). ';
        }
        const reappliable = result.code === 'SOURCE_REVISION_CONFLICT' &&
            Number.isSafeInteger(result.current_revision);
        const actions = [{ label: reappliable ? 'Use server' : 'Discard my source', apply: false }];
        if (reappliable) actions.push({ label: 'Apply my source', apply: true });
        return { opId: op.op_id, field: 'source_url', group: 'source', text: text, actions: actions };
    }

    function conflictRow(ctx, state, op) {
        const described = sourceConflictDescriptor(op);
        const item = document.createElement('div');
        item.dataset.prksWorkSourceConflict = state.workId;
        item.appendChild(document.createTextNode(described.text));
        described.actions.forEach(choice => action(item, ctx, state, op, choice.label, choice.apply));
        return item;
    }

    function action(item, ctx, state, op, label, apply) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'prks-btn prks-btn--secondary prks-btn--sm';
        button.textContent = label;
        button.onclick = async () => {
            button.disabled = true;
            await resolveSource(ctx, state, op, apply);
        };
        item.appendChild(button);
    }

    async function resolveSource(ctx, state, op, apply) {
        if (!live(ctx, state)) return;
        const session = ctx.ui && typeof ctx.ui.workMetaEditSession === 'number' ? ctx.ui.workMetaEditSession : 0;
        const still = () => typeof root.prksWorkMetaSessionStill !== 'function' ||
            root.prksWorkMetaSessionStill(ctx, state.workId, session);
        try {
            if (!apply) {
                /* "Use server" is a decision to adopt a source this device
                 * has never held, and the terminal result cannot supply
                 * it: the URL came back as a BOUNDED PREVIEW, deliberately
                 * shortenable, so fabricating a Work from it could store a
                 * truncated URL as though it were canonical.
                 *
                 * So both representations are dropped and re-read
                 * authoritatively. Dropping only the Work left the cached
                 * source REVISION at its pre-conflict value, which is the
                 * base the next save would have been measured against. */
                if (still() && live(ctx, state)) state.observed = null;
                root.prksOfflineMarkEntityChanged('work', state.workId);
                root.prksOfflineMarkEntityChanged('work-source-state', state.workId);
            }
            await root.prksSync.store.resolveConflict(op.op_id, apply);
            root.prksSync.changed();
            if (still() && live(ctx, state)) {
                state.error = null;
                if (apply) {
                    /* The replacement operation was created against the
                     * revision the server reported, so the editor's base has
                     * to move there too -- BOTH halves. The replacement is
                     * still never-sent and therefore still coalescible: with a
                     * stale base, changing one's mind again would rewrite it
                     * against a revision the server has already passed, and
                     * returning to the server's own video would read as a
                     * change rather than as a cancellation. */
                    state.observed = baseOf(op.server_result.current_revision,
                        root.prksWorkSourceConflictIdentity(op.server_result));
                }
            }
        } catch (_) {
            if (!still() || !live(ctx, state)) return;
            state.error = 'Could not save that resolution locally. Please retry.';
            await safePaint(ctx, state);
            return;
        }
        /* After the discard, and only then: re-establish the authoritative
         * source. If it cannot be read -- offline, say -- the base stays
         * null and the editor stays explicitly unavailable rather than
         * falling back to the stale pre-conflict Work. A session that ended
         * during the resolution must not adopt that re-read. */
        if (!apply && still() && live(ctx, state)) {
            const settled = await readBase(ctx, state, { adopt: true, still: still });
            if (!settled) return;
        }
        if (still()) await safePaint(ctx, state);
    }

    /**
     * Put an authoritative URL into the control.
     *
     * Deliberately NOT conditioned on the control being enabled: it is
     * disabled precisely BECAUSE an operation is in flight, so an enabled-only
     * write would never run and the editor would keep showing the URL the user
     * typed after the server had answered with another. What must not be
     * overwritten is text the user is typing right now -- and only the owner
     * of the shared panel may write into it at all.
     */
    function writeInput(ctx, state, url) {
        if (typeof root.prksVueAcceptWorkMetadataField === 'function' &&
            typeof root.prksVueWorkMetadataEditorOwns === 'function' &&
            root.prksVueWorkMetadataEditorOwns(ctx)) {
            root.prksVueAcceptWorkMetadataField(ctx, 'source_url', url == null ? '' : String(url));
            return;
        }
        const section = owns(ctx, state) ? sectionOf() : null;
        const input = section && section.querySelector('#meta-video-url');
        if (input && document.activeElement !== input) input.value = url;
    }

    /**
     * An acknowledgement moves the base this editor measures its NEXT save
     * against.
     *
     * Without this, a second change is created against the revision the first
     * one already superseded, and the user conflicts with their own previous
     * edit -- on a Work nobody else touched. The cached projection is patched
     * by the runtime; this is the same fact for the editor that is open.
     */
    function acceptAck(ctx, state, ack) {
        if (!live(ctx, state) || ack.work_id !== state.workId) return;
        if (!Number.isSafeInteger(ack.server_revision)) return;
        state.readVersion = (state.readVersion || 0) + 1;
        if (state.observed && state.observed.revision > ack.server_revision) return;
        state.observed = baseOf(ack.server_revision,
            root.prksWorkSourceIdentity(root.prksAcknowledgedWorkSource(ack)));
        ctx.setEntity('work', Object.assign({}, ctx.getEntity('work'),
            root.prksAcknowledgedWorkSource(ack)));
        /* Only the owner of the shared panel may write the panel, and only
         * inside it: this Work's acknowledgement must never be published into
         * whichever Work's editor happens to be on screen. */
        /* The STORED spelling. On a convergent write the server kept its own
         * and stored nothing of ours; showing what we asked for would claim a
         * value the server does not have. */
        writeInput(ctx, state, ack.source_url);
    }

    /** Entity-only source acknowledgement for a parked owner. No observed base, no paint. */
    function applyWorkSourceEntityAck(ctx, ack) {
        if (!ctx || !ack || typeof root.prksAcknowledgedWorkSource !== 'function') return;
        const work = ctx.getEntity('work');
        if (!work || work.id !== ack.work_id) return;
        ctx.setEntity('work', Object.assign({}, work, root.prksAcknowledgedWorkSource(ack)));
    }

    function sourceStateShape(workId) {
        return value => !!value && typeof value === 'object' && value.work_id === workId &&
            Number.isSafeInteger(value.revision) && value.revision >= 0;
    }

    /**
     * The base the next save is measured against: a REVISION and an IDENTITY.
     *
     * Both are needed and neither is derivable from the other. The revision
     * decides staleness; the identity decides whether there is anything to
     * save at all, because returning to the video the server already holds is
     * a cancellation rather than a change. Keeping only the revision meant the
     * identity was re-read from the tab's Work every time -- correct until a
     * resolution moved the server somewhere that Work had never been.
     */
    function baseOf(revision, identity) {
        return { revision, identity: identity || '' };
    }

    /**
     * Read the base: the source revision and the identity it belongs to.
     *
     * Nothing here asks for an "authoritative" read, because there is no such
     * flag and there does not need to be. A read-through always tries the
     * server first; what decides whether a STALE answer may stand in when it
     * fails is whether the entity has been invalidated. "Use server"
     * invalidates both representations before calling this, which is exactly
     * what makes the read authoritative -- and what makes an unreachable
     * server report `unavailable` instead of handing back the pre-conflict
     * Work the user just decided against.
     *
     * `options.adopt` says this read follows a discard, so its result replaces
     * what the tab is holding rather than merely establishing a base.
     */
    async function readBase(ctx, state, options) {
        const readVersion = state.readVersion = (state.readVersion || 0) + 1;
        const adopt = !!(options && options.adopt);
        try {
            const [stateResult, workResult] = await Promise.all([
                root.prksOfflineReadEntity('work-source-state', state.workId,
                    '/api/works/' + encodeURIComponent(state.workId) + '/source-state',
                    { validate: sourceStateShape(state.workId) }),
                root.prksOfflineReadEntity('work', state.workId,
                    '/api/works/' + encodeURIComponent(state.workId),
                    { validate: value => !!value && value.id === state.workId }),
            ]);
            if (!live(ctx, state) || readVersion !== state.readVersion) return false;
            if (options && typeof options.still === 'function' && !options.still()) return false;
            const usable = stateResult && stateResult.source !== 'unavailable' &&
                stateResult.value && workResult && workResult.source !== 'unavailable' &&
                workResult.value;
            /* "Not read" is not "revision 0". Saving against a base this
             * session could not establish would overwrite a decision it never
             * saw, so the control stays disabled instead. */
            if (!usable) { state.observed = null; return true; }
            state.observed = baseOf(stateResult.value.revision,
                root.prksWorkSourceIdentity(root.prksWorkSourceOf(workResult.value)));
            /* The server's Work replaces what this tab was holding. After
             * "Use server" the tab's copy is the source the user just decided
             * AGAINST, and leaving it would show them video A while the server
             * holds video C -- with the pending overlay gone, nothing else
             * would ever correct it. */
            if (adopt) {
                ctx.setEntity('work', workResult.value);
                writeInput(ctx, state, workResult.value.source_url || '');
            }
            return true;
        } catch (_) {
            if (!live(ctx, state) || readVersion !== state.readVersion) return false;
            if (options && typeof options.still === 'function' && !options.still()) return false;
            state.observed = null;
            return true;
        }
    }

    async function prepare(ctx, state) {
        if (!(await readBase(ctx, state))) return;
        await safePaint(ctx, state);
    }

    function mount(ctx, workId, options) {
        if (!ctx || !root.prksSync) return;
        let state = ctx.getResource('workSourceEditor');
        if (!state || state.workId !== workId || state.generation !== ctx.generation) {
            /* Capture the ticket before subscriptions or async prepare.
             * setResource would mint a later ticket. A rejected registration
             * does not subscribe, paint, prepare, or mutate this owner. */
            const ticket = typeof ctx.resourceTicket === 'function' ? ctx.resourceTicket() : null;
            const next = { workId, generation: ctx.generation, operations: [], observed: undefined,
                error: null };
            const stops = { sync: null, connectivity: null };
            const attached = typeof ctx.registerResource === 'function'
                ? ctx.registerResource(ticket, {
                    kind: 'workSourceEditor',
                    value: next,
                    suspendable: false,
                    dispose: function () {
                        if (stops.sync) stops.sync();
                        if (stops.connectivity) stops.connectivity();
                    },
                })
                : 'rejected';
            if (attached === 'rejected') return;
            if (typeof root.prksBindOwnerWorkAcknowledgement === 'function') {
                root.prksBindOwnerWorkAcknowledgement(ctx);
            }
            stops.sync = root.prksSync.subscribe(event => {
                if (event && event.operation && event.operation !== 'SET_WORK_SOURCE') return;
                if (event && event.acknowledged) acceptAck(ctx, next, event.acknowledged);
                void safePaint(ctx, next);
            });
            stops.connectivity = root.prksOfflineRuntimeSubscribe(() => {
                /* A base that could not be established is not a permanent
                 * state. "Use server" while unreachable leaves the editor
                 * deliberately unavailable rather than falling back to the
                 * source the user just rejected -- so when the server comes
                 * back, the editor has to go and get it, or the control stays
                 * dead until the user navigates away and returns. */
                if (!next.observed && root.prksOfflineRuntimeState() === 'online') {
                    void readBase(ctx, next, { adopt: true }).then(settled => {
                        if (settled) void safePaint(ctx, next);
                    });
                    return;
                }
                void safePaint(ctx, next);
            });
            state = next;
        }
        void safePaint(ctx, state);
        if (options && options.editing) state.preparing = prepare(ctx, state);
    }

    /** One Save: a whole identity, or a visible refusal. */
    async function save(workId) {
        const ctx = root.prksGetFocusedTabContext ? root.prksGetFocusedTabContext() : null;
        const state = ctx && ctx.getResource('workSourceEditor');
        if (!state || !live(ctx, state) || state.workId !== workId) return;
        const session = ctx.ui && typeof ctx.ui.workMetaEditSession === 'number' ? ctx.ui.workMetaEditSession : 0;
        const still = () => typeof root.prksWorkMetaSessionStill !== 'function' ||
            root.prksWorkMetaSessionStill(ctx, workId, session);
        if (!still()) return;
        const section = sectionOf();
        const input = section && section.querySelector('#meta-video-url');
        const error = section && section.querySelector('#meta-video-url-error');
        const button = section && section.querySelector('#save-work-source-btn');
        if (button && root.prksSetButtonBusy) {
            root.prksSetButtonBusy(button, true, { busyLabel: 'Saving…' });
        }
        try {
            if (state.preparing) await state.preparing;
            if (!still()) return;
            if (!state.observed) throw new Error('no observed base');
            if (typeof root.prksVueSetWorkMetadataFieldError === 'function') {
                root.prksVueSetWorkMetadataFieldError('', '');
            }
            const vueOwnsErrors = typeof root.prksVueWorkMetadataEditorOwns === 'function' &&
                root.prksVueWorkMetadataEditorOwns(ctx);
            if (!vueOwnsErrors) {
                if (error) error.textContent = '';
                if (input) input.removeAttribute('aria-invalid');
            }
            const typed = ctx.ui && ctx.ui.workMetaDraft && ctx.ui.workMetaDraft.source_url != null
                ? String(ctx.ui.workMetaDraft.source_url)
                : (input ? input.value : '');
            const source = root.prksCanonicalWorkSource(typed);
            if (!source) {
                /* Refused, never guessed at: an unreadable URL is not an
                 * instruction to clear the video. */
                const message = 'Use a YouTube link, for example https://www.youtube.com/watch?v=…';
                if (vueOwnsErrors && typeof root.prksVueSetWorkMetadataFieldError === 'function') {
                    root.prksVueSetWorkMetadataFieldError('source_url', message);
                    return;
                }
                if (input) { input.setAttribute('aria-invalid', 'true'); input.focus(); }
                if (error) error.textContent = message;
                return;
            }
            const work = ctx.getEntity('work');
            const current = root.prksWorkSourceOf(root.prksEffectiveWorkSource(work));
            if (root.prksWorkSourceIdentity(current) === root.prksWorkSourceIdentity(source)) {
                // The same video the user is already looking at, however it is
                // spelled. Nothing to record. (Returning to the ACKNOWLEDGED
                // video over a pending change is a different case and is the
                // store's to cancel -- it owns the durable row.)
                state.error = null;
                if (still() && typeof root.prksCommitWorkMetaBaseline === 'function') {
                    root.prksCommitWorkMetaBaseline(ctx, workId, session, { source_url: typed }, ['source_url']);
                }
                await safePaint(ctx, state);
                return;
            }
            /* Coalescing, not a bare enqueue: a source is an aggregate, so
             * there is at most one unsynchronized intent for a Work. Choosing
             * B and then C must leave ONE operation naming C, and returning to
             * the acknowledged video must leave none. */
            if (!still()) return;
            if (!live(ctx, state)) return;
            await root.prksSync.store.saveWorkSource(workId, {
                kind: 'video',
                url: source.source_url,
                identity: root.prksWorkSourceIdentity(source),
            }, state.observed);
            root.prksSync.changed();
            if (!still()) return;
            if (!live(ctx, state)) return;
            if (typeof root.prksCommitWorkMetaBaseline === 'function') {
                root.prksCommitWorkMetaBaseline(ctx, workId, session, { source_url: typed }, ['source_url']);
            }
            state.error = null;
            await safePaint(ctx, state);
        } catch (error_) {
            if (!still()) return;
            if (!live(ctx, state)) return;
            state.error = error_ && error_.prksLocalStoreCode === 'scope_busy'
                ? 'This source is still syncing or needs a decision below.'
                : 'Could not save the video source locally. Please retry.';
            await safePaint(ctx, state);
        } finally {
            if (button && root.prksSetButtonBusy) root.prksSetButtonBusy(button, false);
        }
    }

    root.prksResolveWorkSourceConflict = function (opId, apply) {
        const ctx = root.prksGetFocusedTabContext ? root.prksGetFocusedTabContext() : null;
        const state = ctx && ctx.getResource('workSourceEditor');
        if (!state || !live(ctx, state)) return;
        const session = ctx.ui && typeof ctx.ui.workMetaEditSession === 'number' ? ctx.ui.workMetaEditSession : 0;
        if (typeof root.prksWorkMetaSessionStill === 'function' &&
            !root.prksWorkMetaSessionStill(ctx, state.workId, session)) return;
        const op = (state.operations || []).find(item => item.op_id === opId);
        if (!op) return;
        return resolveSource(ctx, state, op, !!apply);
    };
    root.prksMountWorkSourceEditor = mount;
    root.prksSaveWorkSource = save;
    root.prksApplyWorkSourceEntityAck = applyWorkSourceEntityAck;
})(typeof window === 'undefined' ? globalThis : window);
