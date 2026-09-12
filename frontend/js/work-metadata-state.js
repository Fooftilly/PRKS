/* Work metadata fields: the synchronization projection, the optimistic
 * overlay, and what a server answer means for this family.
 *
 * The conflict unit is a FIELD. Two devices editing `doi` and `isbn` on the
 * same Work have not disagreed about anything, so they must not be told they
 * have -- which is why every field carries its own revision and its own
 * resolution, and why one conflicting field leaves the rest editable.
 *
 * Only these seven scalars are synchronized. No other cached read model
 * renders them: Work cards, browse catalogs, Folders, People, Playlists and
 * the Graph display none of them, so a pending value needs no optimistic
 * propagation beyond the Work itself.
 */
(function (root) {
    'use strict';
    const FIELDS = Object.freeze(['edition', 'journal', 'volume', 'issue', 'pages', 'isbn', 'doi']);
    const FIELD_SET = new Set(FIELDS);
    const LABELS = Object.freeze({
        edition: 'Edition', journal: 'Journal', volume: 'Volume', issue: 'Issue',
        pages: 'Pages', isbn: 'ISBN', doi: 'DOI',
    });

    /** SQLite NULL and "" are one logical value; whitespace is never stripped. */
    function canonical(value) {
        return value == null ? '' : String(value);
    }

    const revision = v => Number.isSafeInteger(v) && v >= 0;

    function stateShape(value, workId) {
        if (!value || typeof value !== 'object' || typeof value.work_id !== 'string' ||
            (workId && value.work_id !== workId)) return false;
        const fields = value.fields;
        if (!fields || typeof fields !== 'object' || Array.isArray(fields)) return false;
        const names = Object.keys(fields);
        if (names.length !== FIELDS.length || !names.every(name => FIELD_SET.has(name))) return false;
        return names.every(name => {
            const entry = fields[name];
            return !!entry && typeof entry === 'object' &&
                typeof entry.value === 'string' && revision(entry.revision);
        });
    }

    async function readState(workId, options = {}) {
        const result = await root.prksOfflineReadEntity('work-metadata-state', workId,
            '/api/works/' + encodeURIComponent(workId) + '/metadata-state', {
                ...options, validate: v => stateShape(v, workId),
            });
        if (result.value !== null && !stateShape(result.value, workId)) {
            await root.prksOfflineInvalidateEntity('work-metadata-state', workId);
            return { value: null, source: 'unavailable', cachedAt: null };
        }
        return result;
    }

    function fieldOperations(operations, workId) {
        return (operations || []).filter(op => op && op.operation === 'SET_WORK_METADATA_FIELD' &&
            op.entity_type === 'work' && op.entity_id === workId && op.status !== 'acknowledged');
    }

    /**
     * Acknowledged Work + durable pending/conflicted field edits. Pending
     * intent is never written into the cached Work record; the overlay is
     * recomputed from `prks-local-v1`, so it survives a reload.
     */
    function effectiveWork(work, operations) {
        if (!work || typeof work.id !== 'string') return work;
        const pending = fieldOperations(operations, work.id);
        if (!pending.length) return work;
        const out = Object.assign({}, work);
        pending.forEach(op => {
            if (FIELD_SET.has(op.payload.field)) out[op.payload.field] = op.payload.value;
        });
        return out;
    }

    /**
     * What the user actually changed, measured against what the form was
     * SHOWING -- the pending value if there is one, otherwise the server's.
     *
     * Measuring against the server base instead would be subtly wrong in both
     * directions: editing a field back to its server value would look like "no
     * change" and quietly leave the pending operation in place, and a field
     * still displaying an untouched pending value would look dirty on every
     * save.
     */
    function dirtyFields(draft, state, operations) {
        const changes = {};
        if (!draft || !state || !state.fields) return changes;
        const pending = new Map();
        (operations || []).forEach(op => {
            if (op && op.operation === 'SET_WORK_METADATA_FIELD' && op.status !== 'acknowledged' &&
                FIELD_SET.has(op.payload.field)) pending.set(op.payload.field, op.payload.value);
        });
        FIELDS.forEach(field => {
            if (!Object.prototype.hasOwnProperty.call(draft, field)) return;
            const shown = pending.has(field) ? pending.get(field) : canonical(state.fields[field].value);
            const desired = canonical(draft[field]);
            if (desired !== shown) changes[field] = desired;
        });
        return changes;
    }

    /* ---- sync handler ---- */
    function isResult(data, op) {
        if (!data || data.work_id !== op.entity_id || data.field !== op.payload.field) return false;
        switch (data.code) {
            case 'ACKNOWLEDGED':
                return typeof data.value === 'string' && data.value === op.payload.value &&
                    typeof data.changed === 'boolean' && revision(data.server_revision);
            case 'REVISION_CONFLICT': case 'FUTURE_REVISION':
                return revision(data.current_revision) && typeof data.current_value === 'string' &&
                    data.requested_value === op.payload.value;
            case 'ENTITY_NOT_FOUND': return true;
            default: return false;
        }
    }

    /* Every terminal outcome here is the user's to resolve: they typed this
     * value deliberately, so discarding it silently would lose real work. The
     * conflict is recorded against the FIELD, so the rest stay usable. */
    function terminal(data) {
        const out = { code: data.code };
        for (const key of ['current_revision', 'current_value', 'requested_value']) {
            if (Object.prototype.hasOwnProperty.call(data, key)) out[key] = data[key];
        }
        return { conflict: out };
    }

    const handler = {
        isResult, terminal,
        reconcile: data => root.prksOfflineReconcileWorkField(data),
    };

    Object.assign(root, {
        PRKS_SYNCED_WORK_FIELDS: FIELDS,
        PRKS_SYNCED_WORK_FIELD_LABELS: LABELS,
        prksCanonicalWorkField: canonical,
        prksIsWorkMetadataStateShape: stateShape,
        prksReadWorkMetadataState: readState,
        prksEffectiveWorkMetadata: effectiveWork,
        prksWorkMetadataFieldOperations: fieldOperations,
        prksDirtyWorkMetadataFields: dirtyFields,
        prksWorkMetadataSyncHandler: handler,
    });
})(typeof window === 'undefined' ? globalThis : window);
