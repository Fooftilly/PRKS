/**
 * Work-Person links on the Work detail panel, on the durable path.
 *
 * Online and offline take the SAME route: the operation is written locally
 * first and synchronized afterwards. There is no direct-API branch, because a
 * semantic action that is durable offline and a direct POST online is two
 * mutation boundaries for one decision -- and only one of them advances the
 * relationship revision every other device measures staleness against.
 *
 * This module owns the observed BASE for each element: its state and revision,
 * read from the relationship-state projection. Saving against a base this
 * session could not establish would overwrite a decision it never saw, so the
 * controls are refused rather than guessed at.
 */
(function (root) {
    'use strict';

    const unavailable = 'Linked people are not available offline for this file yet. '
        + 'Connect once and open this file to prepare them.';

    function panelOf() { return document.getElementById('panel-content'); }

    function live(ctx, state) {
        return ctx && !ctx.destroyed && ctx.generation === state.generation &&
            ctx.getEntity('work') && ctx.getEntity('work').id === state.workId;
    }
    function owns(ctx, state) {
        return live(ctx, state) && root.prksRightPanelOwnedBy(ctx) &&
            root.prksOwnerTabIsFocused(ctx);
    }

    function shape(workId) {
        return value => !!value && typeof value === 'object' && value.work_id === workId &&
            Array.isArray(value.scopes) && value.scopes.every(scope => scope &&
                typeof scope.person_id === 'string' && typeof scope.role_type === 'string' &&
                Number.isSafeInteger(scope.revision) && typeof scope.present === 'boolean');
    }

    /**
     * The base for one element, from the projection plus the Work's own links.
     *
     * The projection carries revisions and tombstones; the credit name lives on
     * the Work record, which already has it. Reading the state from one place
     * and the revision from another is deliberate -- duplicating Person data
     * into a second cached projection would double what every read costs for
     * values the client already holds.
     */
    function baseFor(state, work, personId, roleType) {
        if (!state.observed) return null;
        const scope = state.observed.scopes.find(s =>
            s.person_id === personId && s.role_type === roleType);
        const revision = scope ? scope.revision : 0;
        const links = Array.isArray(work && work.roles) ? work.roles : [];
        const link = links.find(r => String((r && (r.person_id || r.id)) || '') === personId &&
            r.role_type === roleType);
        if (!link) return { state: null, revision };
        return {
            state: String(link.credit_name == null ? '' : link.credit_name).trim(),
            revision,
        };
    }

    async function readBase(ctx, state) {
        const readVersion = state.readVersion = (state.readVersion || 0) + 1;
        try {
            const result = await root.prksOfflineReadEntity('work-people-state', state.workId,
                '/api/works/' + encodeURIComponent(state.workId) + '/people-state',
                { validate: shape(state.workId) });
            if (!live(ctx, state) || readVersion !== state.readVersion) return;
            /* "Not read" is not "revision 0" for every element. Saving against
             * a base this session could not establish would overwrite a
             * decision it never saw, so the control stays disabled instead. */
            state.observed = result && result.source !== 'unavailable' && result.value
                ? result.value : null;
        } catch (_) { state.observed = null; }
    }

    /**
     * An acknowledgement moves the base the NEXT edit on that element is
     * measured against. Without it a second change is created against the
     * revision the first already superseded, and the user conflicts with their
     * own previous edit on a link nobody else touched.
     */
    function acceptAck(ctx, state, ack) {
        if (!live(ctx, state) || ack.work_id !== state.workId) return;
        if (!Number.isSafeInteger(ack.server_revision) || !state.observed) return;
        const scopes = state.observed.scopes.filter(s =>
            !(s.person_id === ack.person_id && s.role_type === ack.role_type));
        scopes.push({ person_id: ack.person_id, role_type: ack.role_type,
            revision: ack.server_revision, present: !!ack.present });
        state.observed = Object.assign({}, state.observed, { scopes });
        /* The tab's own Work is what the panel renders from, and the cache
         * reconciliation cannot reach it. Patched through the SAME helper, so
         * the panel and the cached Work cannot disagree about this link. */
        const patched = root.prksPatchWorkDetailRoles(ctx.getEntity('work'), ack);
        if (patched) ctx.setEntity('work', patched);
    }

    function statusText(operations) {
        if (operations.some(o => o.status === 'conflict')) return 'Some links need your decision.';
        if (operations.some(o => o.status === 'syncing')) return 'Syncing…';
        if (operations.some(o => o.last_error)) return 'Sync failed · retry scheduled';
        if (operations.length) {
            return root.prksOfflineRuntimeState() === 'online'
                ? 'Waiting to sync' : 'Offline · saved locally';
        }
        return 'All changes synced';
    }

    async function paint(ctx, state) {
        const paintVersion = state.paintVersion = (state.paintVersion || 0) + 1;
        const rows = await root.prksRefreshPendingWorkRoles();
        if (!live(ctx, state) || paintVersion !== state.paintVersion) return;
        state.operations = root.prksWorkRoleOperations(rows, state.workId);
        if (!owns(ctx, state)) return;
        const panel = panelOf();
        if (!panel) return;

        const host = panel.querySelector('.work-linked-persons-by-role');
        if (host && typeof root.buildWorkLinkedPersonsHtml === 'function') {
            const effective = root.prksEffectiveWorkDetailRoles(ctx.getEntity('work'));
            const html = root.buildWorkLinkedPersonsHtml(effective, { editable: state.editable });
            if (host.innerHTML !== html) host.innerHTML = html;
        }
        const editable = !!state.observed;
        const linkButton = panel.querySelector('.work-link-person-btn');
        if (linkButton) {
            linkButton.disabled = !editable;
            linkButton.title = editable ? 'Link a person to this file' : unavailable;
        }
        const status = panel.querySelector('[data-prks-role="work-people-sync"]');
        if (status) {
            status.textContent = state.error ||
                (editable ? statusText(state.operations) : unavailable);
        }
    }

    function safePaint(ctx, state) { return paint(ctx, state).catch(() => {}); }

    function mount(ctx, workId, options) {
        if (!ctx || !root.prksSync) return;
        let state = ctx.getResource('workRoleEditor');
        if (!state || state.workId !== workId || state.generation !== ctx.generation) {
            state = { workId, generation: ctx.generation, operations: [], observed: null,
                error: null, editable: !!(options && options.editable) };
            const stopSync = root.prksSync.subscribe(event => {
                if (event && event.operation &&
                    (root.PRKS_WORK_ROLE_OPERATION_TYPES || []).indexOf(event.operation) === -1) {
                    return;
                }
                if (event && event.acknowledged) acceptAck(ctx, state, event.acknowledged);
                void safePaint(ctx, state);
            });
            const stopConnectivity = root.prksOfflineRuntimeSubscribe(() => {
                if (!state.observed && root.prksOfflineRuntimeState() === 'online') {
                    void readBase(ctx, state).then(() => safePaint(ctx, state));
                    return;
                }
                void safePaint(ctx, state);
            });
            ctx.setResource('workRoleEditor', state, () => { stopSync(); stopConnectivity(); });
            state.preparing = readBase(ctx, state).then(() => safePaint(ctx, state));
        } else {
            state.editable = !!(options && options.editable);
            void safePaint(ctx, state);
        }
    }

    /**
     * Save one element's desired state.
     *
     * `desired` is null (unlinked) or the credit name (linked; '' means no
     * override). `person` is what the detail panel needs to render the link
     * before any acknowledgement carries it.
     */
    /**
     * The base for any Work, whether or not an editor is mounted for it.
     *
     * The role modal links a Person to a Work chosen INSIDE the modal, which
     * need not be the one on screen -- and that is the same semantic action as
     * the panel's Link button. One save path serves both, or the same decision
     * would be durable on one surface and a direct POST on the other.
     */
    async function baseForAnyWork(workId, personId, roleType) {
        const [stateResult, workResult] = await Promise.all([
            root.prksOfflineReadEntity('work-people-state', workId,
                '/api/works/' + encodeURIComponent(workId) + '/people-state',
                { validate: shape(workId) }),
            root.prksOfflineReadEntity('work', workId,
                '/api/works/' + encodeURIComponent(workId), {}),
        ]);
        const usable = stateResult && stateResult.source !== 'unavailable' && stateResult.value &&
            workResult && workResult.source !== 'unavailable' && workResult.value;
        if (!usable) return null;
        return baseFor({ observed: stateResult.value }, workResult.value, personId, roleType);
    }

    async function save(workId, personId, roleType, desired, person, work) {
        if (!root.prksSync) return { code: 'unavailable' };
        if (desired !== null && typeof desired !== 'string') return { code: 'unavailable' };
        if (desired !== null &&
            root.prksWorkFieldUtf8Bytes(desired) > root.PRKS_MAX_CREDIT_NAME_BYTES) {
            return { code: 'too-long' };
        }
        const ctx = root.prksGetFocusedTabContext ? root.prksGetFocusedTabContext() : null;
        const state = ctx && ctx.getResource('workRoleEditor');
        const mounted = state && live(ctx, state) && state.workId === workId;
        let base = null;
        if (mounted) {
            if (state.preparing) await state.preparing;
            if (state.observed) base = baseFor(state, ctx.getEntity('work'), personId, roleType);
        } else {
            base = await baseForAnyWork(workId, personId, roleType);
        }
        if (!base) return { code: 'unavailable' };
        try {
            await root.prksSync.store.saveWorkPersonRole(workId,
                { person_id: personId, role_type: roleType, state: desired },
                base, (person || work) ? { person: person || undefined, work: work || undefined } : null);
        } catch (error) {
            /* Named codes, never one bucket. `dependency-failed` is not a
             * failure of THIS save at all -- the link is fine, the Person it
             * names is the thing the server refused -- and "Could not create
             * link" would send the user looking in the wrong place. */
            const code = error && error.prksLocalStoreCode;
            if (code === 'scope_busy') return { code: 'busy' };
            if (code === 'dependency_failed') return { code: 'dependency-failed' };
            return { code: 'failed' };
        }
        if (mounted) {
            state.error = null;
            await safePaint(ctx, state);
        }
        root.prksSync.changed();
        return { code: 'saved' };
    }

    root.prksMountWorkRoleEditor = mount;
    root.prksSaveWorkPersonRoleDurably = save;
})(typeof window === 'undefined' ? globalThis : window);
