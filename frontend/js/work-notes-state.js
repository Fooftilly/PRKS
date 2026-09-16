/**
 * Work Research Notes and Private Notes: durable whole-document aggregates.
 *
 * Each note body is one conflict unit. Two independent scopes:
 * work-research-note (`text_content`, with canonical markup/index side effects)
 * and work-private-note (`private_notes`, no markup). The browser never parses
 * research markup to decide what an acknowledgement means.
 */
(function (root) {
    'use strict';

    const RESEARCH_KIND = 'work-research-note';
    const PRIVATE_KIND = 'work-private-note';
    const RESEARCH_OP = 'SET_WORK_RESEARCH_NOTE';
    const PRIVATE_OP = 'SET_WORK_PRIVATE_NOTE';

    const MAX_RESEARCH_BYTES = 32 * 1024 * 1024;
    const MAX_PRIVATE_BYTES = 64 * 1024;

    function utf8Bytes(s) {
        return new TextEncoder().encode(s || '').length;
    }

    function noteKindOf(op) {
        const opType = op && op.operation;
        if (opType === RESEARCH_OP) return RESEARCH_KIND;
        if (opType === PRIVATE_OP) return PRIVATE_KIND;
        return null;
    }

    function noteText(op) {
        return op && op.payload && typeof op.payload.text === 'string' ? op.payload.text : null;
    }

    /* ---- projection shape ---- */
    function stateShape(value, workId) {
        if (!value || typeof value !== 'object') return false;
        if (workId && value.work_id !== workId) return false;
        if (typeof value.work_id !== 'string') return false;
        const r = value.research_note_revision;
        const p = value.private_note_revision;
        return Number.isSafeInteger(r) && r >= 0 && Number.isSafeInteger(p) && p >= 0;
    }

    async function readState(workId, options) {
        const result = await root.prksOfflineReadEntity('work-notes-state', workId,
            '/api/works/' + encodeURIComponent(workId) + '/notes-state', {
                ...options, validate: v => stateShape(v, workId),
            });
        if (result.value !== null && !stateShape(result.value, workId)) {
            await root.prksOfflineInvalidateEntity('work-notes-state', workId);
            return { value: null, source: 'unavailable', cachedAt: null };
        }
        return result;
    }

    /**
     * The acknowledged base a save is measured against.
     *
     * Values come from the canonical Work record and revisions from
     * notes-state. Never from an already-effective Work: overlaying pending
     * text into the base would make A -> B -> A look like a change.
     */
    function acknowledgedNoteBase(work, state) {
        if (!work || typeof work.id !== 'string' || !stateShape(state, work.id)) return null;
        return {
            research: {
                value: typeof work.text_content === 'string' ? work.text_content : '',
                revision: state.research_note_revision,
            },
            private: {
                value: work.private_notes == null ? '' : String(work.private_notes),
                revision: state.private_note_revision,
            },
        };
    }

    /* ---- operations per scope ---- */
    function noteOperations(operations, workId, kind) {
        const opType = kind === RESEARCH_KIND ? RESEARCH_OP : PRIVATE_OP;
        return (operations || []).filter(op => op && op.operation === opType &&
            op.entity_type === 'work' && op.entity_id === workId && op.status !== 'acknowledged');
    }

    /* RAM copy of the durable queue. Overlay helpers are synchronous because
     * first paint cannot await IndexedDB; refreshPending loads this first. */
    let pendingRows = [];

    async function refreshPending() {
        if (!root.prksSync || !root.prksSync.store) {
            pendingRows = [];
            return pendingRows;
        }
        try {
            pendingRows = await root.prksSync.store.listOperations();
        } catch (_e) {
            pendingRows = [];
        }
        return pendingRows;
    }

    function pendingText(workId, kind, fallback) {
        const ops = noteOperations(pendingRows, workId, kind);
        if (!ops.length) return fallback;
        const text = noteText(ops[ops.length - 1]);
        return text !== null ? text : fallback;
    }

    /* ---- effective Work overlay (pending note text) ---- */
    function effectiveNoteWork(work, operations) {
        if (!work || typeof work.id !== 'string') return work;
        const rows = operations || pendingRows;
        const pendingResearch = noteOperations(rows, work.id, RESEARCH_KIND);
        const pendingPrivate = noteOperations(rows, work.id, PRIVATE_KIND);
        let out = work;
        if (pendingResearch.length) {
            const text = noteText(pendingResearch[pendingResearch.length - 1]);
            if (text !== null) out = Object.assign({}, out, { text_content: text });
        }
        if (pendingPrivate.length) {
            const text = noteText(pendingPrivate[pendingPrivate.length - 1]);
            if (text !== null) out = Object.assign({}, out, { private_notes: text });
        }
        return out;
    }

    function canonicalFrom(work) {
        if (!work || typeof work.id !== 'string') return null;
        return {
            id: work.id,
            text_content: typeof work.text_content === 'string' ? work.text_content : '',
            private_notes: work.private_notes == null ? '' : String(work.private_notes),
        };
    }

    /**
     * Snapshot acknowledged note bodies before any overlay mutates the Work
     * object. Revisions arrive from notes-state (ensureBase). The two are
     * joined only at save time.
     */
    function rememberCanonical(ctx, work) {
        if (!ctx || typeof ctx.setResource !== 'function') return null;
        const existing = ctx.getResource('workNotesCanonical');
        if (existing && existing.id === work.id) return existing;
        const canonical = canonicalFrom(work);
        ctx.setResource('workNotesCanonical', canonical);
        return canonical;
    }

    async function ensureBase(ctx, work, options) {
        if (!ctx || !work || typeof work.id !== 'string') return null;
        const canonical = rememberCanonical(ctx, work) || canonicalFrom(work);
        /* A pending CREATE_WORK has no server row yet. Hitting notes-state
         * would 404 and trip harness console gates; seed revision 0 locally. */
        let state = null;
        if (options && options.pendingCreate) {
            state = {
                work_id: work.id,
                research_note_revision: 0,
                private_note_revision: 0,
            };
        } else {
            const result = await readState(work.id);
            state = result && result.value;
        }
        const base = acknowledgedNoteBase({
            id: work.id,
            text_content: canonical.text_content,
            private_notes: canonical.private_notes,
        }, state);
        if (typeof ctx.setResource === 'function') ctx.setResource('workNotesObserved', base);
        return base;
    }

    function observed(ctx, kind) {
        const base = ctx && typeof ctx.getResource === 'function'
            ? ctx.getResource('workNotesObserved') : null;
        if (!base) return null;
        return kind === PRIVATE_KIND ? base.private : base.research;
    }

    function acceptAck(ctx, event) {
        if (!ctx || !event || !event.acknowledged || !event.op) return;
        const op = event.op;
        if (op.operation !== RESEARCH_OP && op.operation !== PRIVATE_OP) return;
        const work = ctx.getEntity && ctx.getEntity('work');
        if (!work || work.id !== op.entity_id) return;
        const text = noteText(op);
        if (text === null) return;
        const rev = event.acknowledged.server_revision;
        if (!Number.isSafeInteger(rev) || rev < 0) return;
        const canonical = ctx.getResource && ctx.getResource('workNotesCanonical');
        if (canonical && canonical.id === op.entity_id) {
            if (op.operation === RESEARCH_OP) canonical.text_content = text;
            else canonical.private_notes = text;
        }
        const base = ctx.getResource && ctx.getResource('workNotesObserved');
        if (base) {
            const slot = op.operation === RESEARCH_OP ? base.research : base.private;
            if (slot) {
                slot.value = text;
                slot.revision = rev;
            }
        }
    }

    function bindSync(ctx) {
        if (!ctx || typeof ctx.setResource !== 'function') return;
        if (ctx.getResource('workNotesSyncBound')) return;
        if (!root.prksSync || typeof root.prksSync.subscribe !== 'function') return;
        const stop = root.prksSync.subscribe(function (event) {
            if (event && event.acknowledged) acceptAck(ctx, event);
            void refreshPending();
        });
        ctx.setResource('workNotesSyncBound', { stop: stop }, function () { stop(); });
    }

    /* ---- save boundary ---- */
    async function saveWorkNote(workId, kind, text, observed) {
        if (!root.prksSync || !root.prksSync.store) return { code: 'unavailable' };
        const op = kind === RESEARCH_KIND ? RESEARCH_OP : PRIVATE_OP;
        if (!workId || typeof text !== 'string') return { code: 'invalid_envelope' };
        const limit = kind === RESEARCH_KIND ? MAX_RESEARCH_BYTES : MAX_PRIVATE_BYTES;
        if (utf8Bytes(text) > limit) return { code: 'too-long' };
        if (!observed || typeof observed.value !== 'string' ||
            !Number.isSafeInteger(observed.revision) || observed.revision < 0) {
            return { code: 'unknown_base' };
        }
        try {
            await root.prksSync.store.saveWorkNote(workId, op, text, observed);
        } catch (e) {
            return { code: (e && e.prksLocalStoreCode) || 'failed',
                error: (e && e.message) || 'Store refused write.' };
        }
        root.prksSync.changed();
        return { code: 'saved' };
    }

    /* ---- sync handler (compact; server guarantees value_omitted) ---- */
    function isResult(data, op) {
        if (!data || data.work_id !== op.entity_id || !data.note_kind) return false;
        const expectedKind = noteKindOf(op);
        if (!expectedKind || data.note_kind !== expectedKind) return false;
        const has = key => Object.prototype.hasOwnProperty.call(data, key);
        switch (data.code) {
            case 'ACKNOWLEDGED':
                return typeof data.changed === 'boolean' &&
                    Number.isSafeInteger(data.server_revision) &&
                    data.server_revision >= 0 &&
                    data.value_omitted === true &&
                    !has('text') && !has('current_value');
            case 'REVISION_CONFLICT':
            case 'FUTURE_REVISION':
                return Number.isSafeInteger(data.current_revision) &&
                    data.current_revision >= 0 &&
                    Number.isSafeInteger(data.current_bytes) && data.current_bytes >= 0 &&
                    Number.isSafeInteger(data.requested_bytes) && data.requested_bytes >= 0 &&
                    !has('text') && !has('current_value') && !has('current_preview');
            case 'ENTITY_NOT_FOUND':
                return true;
            default:
                return false;
        }
    }

    function terminal(data) {
        const out = { code: data.code };
        for (const k of ['current_revision', 'current_bytes', 'requested_bytes']) {
            if (Object.prototype.hasOwnProperty.call(data, k)) out[k] = data[k];
        }
        return { conflict: out };
    }

    const handler = {
        isResult,
        terminal,
        reconcile: (data, op) => {
            if (!data || data.code !== 'ACKNOWLEDGED') return true;
            if (op.operation === RESEARCH_OP) {
                return root.prksOfflineReconcileWorkNote(data, op);
            }
            return root.prksOfflineReconcilePrivateNote(data, op);
        },
    };

    Object.assign(root, {
        PRKS_MAX_RESEARCH_NOTE_BYTES: MAX_RESEARCH_BYTES,
        PRKS_MAX_PRIVATE_NOTE_BYTES: MAX_PRIVATE_BYTES,
        PRKS_WORK_RESEARCH_NOTE_KIND: RESEARCH_KIND,
        PRKS_WORK_PRIVATE_NOTE_KIND: PRIVATE_KIND,
        prksNoteStateShape: stateShape,
        prksReadWorkNotesState: readState,
        prksAcknowledgedWorkNoteBase: acknowledgedNoteBase,
        prksEffectiveNoteWork: effectiveNoteWork,
        prksWorkNoteOperations: noteOperations,
        prksRefreshPendingWorkNotes: refreshPending,
        prksPendingWorkNoteText: pendingText,
        prksApplyPendingWorkNotes: function (work) { return effectiveNoteWork(work, pendingRows); },
        prksRememberWorkNotesCanonical: rememberCanonical,
        prksEnsureWorkNotesBase: ensureBase,
        prksWorkNoteObserved: observed,
        prksBindWorkNotesSync: bindSync,
        prksSaveWorkNoteDurably: saveWorkNote,
        prksNoteSyncHandler: handler,
    });
})(typeof window === 'undefined' ? globalThis : window);
